#!/usr/bin/env python3
"""cloudflare.py - what Cloudflare does to search and AI crawlers on your zone (free plan works, with limits).

  python extensions/cloudflare/cloudflare.py zone --zone example.com
  python extensions/cloudflare/cloudflare.py bots --zone example.com --days 3
  python extensions/cloudflare/cloudflare.py settings --zone example.com
  python extensions/cloudflare/cloudflare.py settings --host www.example.com --no-api   # live robots.txt check only

zone      look up the zone id for a domain (REST GET /zones?name=...; token permission Zone:Read)
bots      GraphQL Analytics API: requests by crawler user-agent, host and edge status code (403/429/503 share shows
          blocks and challenges), plus firewall events per crawler when the plan exposes them (Zone Analytics:Read)
settings  REST zone settings relevant to bots (bot management / "block AI bots" / managed robots.txt when exposed)
          and ALWAYS a live fetch of https://<host>/robots.txt to detect a "# BEGIN Cloudflare Managed content" block

Environment (secrets only from env, never printed): CLOUDFLARE_API_TOKEN, optional CLOUDFLARE_ZONE_ID.
Writes .seo/audits/cloudflare-<command>-<host>-<stamp>.json|md unless --no-write. Stdlib only, Python 3.10+.
GraphQL dataset availability and query range differ by plan; on error the raw GraphQL error is printed and the
command degrades instead of failing.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from crawl_audit import fetch, set_network_mode, UA_DEFAULT  # noqa: E402

TOOL = "cloudflare"
API = "https://api.cloudflare.com/client/v4"
GRAPHQL = API + "/graphql"
try:  # single source of truth for crawler tokens (references/geo-ai-search.md is mirrored in ai_access.py)
    from ai_access import CRAWLERS  # noqa: E402
    BOTS = [(c[0], c[1], c[2]) for c in CRAWLERS if c[3]]
except Exception:  # pragma: no cover - fallback if ai_access cannot be imported
    BOTS = [("Googlebot", "Google", "classic"), ("bingbot", "Microsoft", "classic"), ("OAI-SearchBot", "OpenAI", "search"),
            ("ChatGPT-User", "OpenAI", "user"), ("GPTBot", "OpenAI", "training"), ("Claude-SearchBot", "Anthropic", "search"),
            ("Claude-User", "Anthropic", "user"), ("ClaudeBot", "Anthropic", "training"), ("PerplexityBot", "Perplexity", "search"),
            ("Perplexity-User", "Perplexity", "user"), ("Applebot", "Apple", "classic"), ("DuckAssistBot", "DuckDuckGo", "search"),
            ("MistralAI-User", "Mistral", "user"), ("meta-externalagent", "Meta", "training"), ("Amazonbot", "Amazon", "search"),
            ("CCBot", "Common Crawl", "training"), ("Bytespider", "ByteDance", "training")]
BOTS += [("meta-externalfetcher", "Meta", "user"), ("cohere-ai", "Cohere", "training")]
BLOCK_STATUSES = {403, 429, 503}
MANAGED_RX = re.compile(r"#\s*BEGIN Cloudflare Managed content(.*?)(#\s*END Cloudflare Managed content|\Z)", re.I | re.S)


class ApiError(RuntimeError):
    pass


def add(findings: list, severity: str, code: str, message: str) -> None:
    findings.append({"severity": severity, "code": code, "message": message})


def http_json(method: str, url: str, token: str, body: dict | None = None, timeout: float = 60.0,
              retries: int = 5, sleep=time.sleep) -> dict:
    """Cloudflare API call with exponential backoff on 429/5xx (1, 2, 4, 8, 16 s + jitter), honouring Retry-After.
    Returns the parsed JSON even for 4xx when the body is JSON (Cloudflare puts errors in it)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "super-seo/1.0"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    last = ""
    for attempt in range(retries + 1):
        try:
            with request.urlopen(request.Request(url, data=data, headers=hdrs, method=method), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", errors="replace")
            except Exception:
                text = ""
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                sleep(min(float(ra) if ra and ra.strip().isdigit() else 2 ** attempt + random.uniform(0, 0.5), 60))
                last = f"HTTP {e.code}"
                continue
            try:
                d = json.loads(text)
                d.setdefault("_http_status", e.code)
                return d
            except json.JSONDecodeError:
                raise ApiError(f"HTTP {e.code}: {text[:400]}") from None
        except (error.URLError, TimeoutError, OSError) as e:
            last = f"network error: {getattr(e, 'reason', e)}"
            if attempt < retries:
                sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
    raise ApiError(f"gave up after {retries + 1} attempts ({last})")


def rest(token: str, path: str, **q) -> dict:
    url = API + path + (("?" + urlencode(q)) if q else "")
    return http_json("GET", url, token)


def cf_errors(d: dict) -> str:
    errs = d.get("errors") or []
    return "; ".join(f"{e.get('code', '')} {e.get('message', e)}".strip() if isinstance(e, dict) else str(e) for e in errs)[:600]


def token() -> str:
    t = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not t:
        raise ApiError("CLOUDFLARE_API_TOKEN is not set (My Profile > API Tokens; see EXTENSION.md for permissions)")
    return t


def find_zone(tok: str, name: str) -> dict:
    name = name.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    labels = name.split(".")
    for i in range(len(labels) - 1):  # www.app.example.com -> app.example.com -> example.com
        cand = ".".join(labels[i:])
        d = rest(tok, "/zones", name=cand)
        if not d.get("success", False):
            raise ApiError(f"zone lookup failed: {cf_errors(d)} (token needs Zone:Read)")
        if d.get("result"):
            z = d["result"][0]
            return {"id": z.get("id"), "name": z.get("name"), "plan": (z.get("plan") or {}).get("name"), "status": z.get("status")}
    raise ApiError(f"no zone found for {name} with this token")


def resolve_zone(tok: str, zone: str | None) -> dict:
    zid = os.environ.get("CLOUDFLARE_ZONE_ID", "").strip()
    if zone:
        return find_zone(tok, zone)
    if re.fullmatch(r"[0-9a-f]{32}", zid):
        d = rest(tok, f"/zones/{zid}")
        r = d.get("result") or {}
        return {"id": zid, "name": r.get("name"), "plan": (r.get("plan") or {}).get("name"), "status": r.get("status")}
    raise ApiError("pass --zone example.com or set CLOUDFLARE_ZONE_ID")


# --------------------------------------------------------------------------- bots (GraphQL)
def bot_for(ua: str) -> tuple[str, str, str] | None:
    u = (ua or "").lower()
    for b in sorted(BOTS, key=lambda x: -len(x[0])):  # longest token first: Claude-SearchBot before ClaudeBot
        if b[0].lower() in u:
            return b
    return None


def gql_requests(zone_id: str, start: str, end: str) -> dict:
    ors = ", ".join('{userAgent_like: "%' + b[0] + '%"}' for b in BOTS)
    q = ("query BotTraffic($zoneTag: string) { viewer { zones(filter: {zoneTag: $zoneTag}) { "
         f'httpRequestsAdaptiveGroups(limit: 5000, filter: {{datetime_geq: "{start}", datetime_lt: "{end}", OR: [{ors}]}}, '
         "orderBy: [count_DESC]) { count dimensions { userAgent clientRequestHTTPHost edgeResponseStatus } } } } }")
    return {"query": q, "variables": {"zoneTag": zone_id}}


def gql_firewall(zone_id: str, start: str, end: str) -> dict:
    ors = ", ".join('{userAgent_like: "%' + b[0] + '%"}' for b in BOTS)
    q = ("query BotFirewall($zoneTag: string) { viewer { zones(filter: {zoneTag: $zoneTag}) { "
         f'firewallEventsAdaptiveGroups(limit: 5000, filter: {{datetime_geq: "{start}", datetime_lt: "{end}", OR: [{ors}]}}, '
         "orderBy: [count_DESC]) { count dimensions { action source userAgent } } } } }")
    return {"query": q, "variables": {"zoneTag": zone_id}}


def gql(tok: str, payload: dict) -> tuple[list, str | None]:
    """Return (rows of the first dataset, raw error text or None)."""
    d = http_json("POST", GRAPHQL, tok, payload, timeout=90)
    if d.get("errors"):
        return [], json.dumps(d["errors"])[:1500]
    zones = ((d.get("data") or {}).get("viewer") or {}).get("zones") or []
    if not zones:
        return [], "no zone data returned (wrong zone id or token lacks Zone Analytics:Read)"
    vals = list(zones[0].values())
    return (vals[0] if vals else []) or [], None


def day_windows(days: int, now: datetime | None = None) -> list[tuple[str, str]]:
    end = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    out = []
    for i in range(days):  # one query per day: adaptive datasets cap the range per query (1 day on lower plans)
        e = end - timedelta(days=i)
        s = e - timedelta(days=1)
        out.append((s.strftime("%Y-%m-%dT%H:%M:%SZ"), e.strftime("%Y-%m-%dT%H:%M:%SZ")))
    return out


def summarize_requests(rows: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        dims = r.get("dimensions") or {}
        b = bot_for(dims.get("userAgent", ""))
        if not b:
            continue
        a = agg.setdefault(b[0], {"bot": b[0], "operator": b[1], "purpose": b[2], "requests": 0, "statuses": {}, "hosts": set()})
        n = int(r.get("count") or 0)
        a["requests"] += n
        st = str(dims.get("edgeResponseStatus", "?"))
        a["statuses"][st] = a["statuses"].get(st, 0) + n
        if dims.get("clientRequestHTTPHost"):
            a["hosts"].add(dims["clientRequestHTTPHost"])
    out = []
    for a in sorted(agg.values(), key=lambda x: -x["requests"]):
        blocked = sum(v for k, v in a["statuses"].items() if k.isdigit() and int(k) in BLOCK_STATUSES)
        a["blocked_share_pct"] = round(blocked * 100.0 / a["requests"], 1) if a["requests"] else 0.0
        a["ok_share_pct"] = round(sum(v for k, v in a["statuses"].items() if k.startswith(("2", "3"))) * 100.0 / a["requests"], 1) if a["requests"] else 0.0
        a["hosts"] = sorted(a["hosts"])
        a["status_mix"] = ", ".join(f"{k}:{v}" for k, v in sorted(a["statuses"].items(), key=lambda kv: -kv[1])[:6])
        out.append(a)
    return out


def summarize_firewall(rows: list[dict]) -> list[dict]:
    agg: dict[tuple, int] = {}
    for r in rows:
        dims = r.get("dimensions") or {}
        b = bot_for(dims.get("userAgent", ""))
        if b:
            k = (b[0], dims.get("action", "?"), dims.get("source", "?"))
            agg[k] = agg.get(k, 0) + int(r.get("count") or 0)
    return [{"bot": k[0], "action": k[1], "source": k[2], "events": v} for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]


def cmd_bots(tok: str, zone: dict, days: int) -> dict:
    findings, notes, rows, fw_rows, raw_errors = [], [], [], [], []
    for s, e in day_windows(days):
        got, err = gql(tok, gql_requests(zone["id"], s, e))
        if err:
            raw_errors.append(f"httpRequestsAdaptiveGroups {s[:10]}: {err}")
            if not rows:
                break  # same error every day; stop early
            continue
        rows += got
    for s, e in day_windows(days):
        got, err = gql(tok, gql_firewall(zone["id"], s, e))
        if err:
            raw_errors.append(f"firewallEventsAdaptiveGroups {s[:10]}: {err}")
            break
        fw_rows += got
    bots = summarize_requests(rows)
    firewall = summarize_firewall(fw_rows)
    if raw_errors:
        notes.append("GraphQL returned errors (dataset or range may not be available on this plan, or a field name "
                     "changed; verify against Cloudflare's GraphQL Analytics schema). Raw errors are listed below.")
    for b in bots:
        if b["purpose"] in ("classic", "search", "user") and b["blocked_share_pct"] >= 20:
            add(findings, "critical" if b["bot"] in ("Googlebot", "bingbot") else "high", "cf_blocks_" + re.sub(r"\W+", "_", b["bot"].lower()),
                f"{b['bot']} ({b['operator']}, {b['purpose']}): {b['blocked_share_pct']}% of {b['requests']} requests got "
                f"403/429/503 at the edge ({b['status_mix']}). Allow verified bots / turn off 'Block AI bots' for search and user agents.")
        elif b["purpose"] == "training" and b["blocked_share_pct"] >= 50:
            add(findings, "info", "cf_blocks_training_" + re.sub(r"\W+", "_", b["bot"].lower()),
                f"{b['bot']} (training) is mostly blocked ({b['blocked_share_pct']}%). That is a business choice; confirm it is intended.")
    seen = {b["bot"] for b in bots}
    if not raw_errors:
        for name in ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"):
            if name not in seen:
                add(findings, "info", "cf_no_requests_" + re.sub(r"\W+", "_", name.lower()),
                    f"No {name} requests seen in {days} days (not crawled yet, or blocked before analytics).")
    chal = [f for f in firewall if f["action"] in ("managed_challenge", "challenge", "js_challenge", "block", "jschallenge")]
    for f in chal[:10]:
        add(findings, "medium", "cf_firewall_" + re.sub(r"\W+", "_", f["action"]),
            f"{f['bot']}: {f['events']} firewall events with action '{f['action']}' (source {f['source']}).")
    return {"title": f"Cloudflare crawler traffic: {zone.get('name')}", "zone": zone, "days": days, "bots": bots,
            "firewall": firewall, "graphql_errors": raw_errors, "findings": findings, "notes": notes}


# --------------------------------------------------------------------------- settings (+ live robots.txt)
def parse_managed_robots(text: str) -> dict:
    m = MANAGED_RX.search(text or "")
    if not m:
        return {"present": False}
    block = m.group(1)
    disallowed, group, in_rules = [], [], False
    for line in block.splitlines():
        k, _, v = line.split("#", 1)[0].partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if in_rules:  # a user-agent after rules starts a new group
                group, in_rules = [], False
            group.append(v)
        elif k in ("disallow", "allow", "content-signal", "crawl-delay"):
            in_rules = True
            if k == "disallow" and v == "/":
                disallowed += group
    signals = re.findall(r"^\s*Content-Signal:\s*(.+)$", block, re.I | re.M)
    return {"present": True, "disallowed_agents": sorted(set(disallowed)), "content_signals": signals}


def clean_host(h: str) -> str:
    h = (h or "").strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if not re.fullmatch(r"[a-z0-9.-]+(:\d+)?", h):
        raise ApiError(f"not a hostname: {h!r}")
    return h


def live_robots(host: str) -> dict:
    host = clean_host(host)
    set_network_mode(host.split(":")[0])
    r = fetch(f"https://{host}/robots.txt", UA_DEFAULT, timeout=20, accept="text/plain,*/*")
    out = {"url": f"https://{host}/robots.txt", "status": r["status"], "error": r.get("error")}
    out.update(parse_managed_robots(r["body"] if r["status"] == 200 else ""))
    return out


def cmd_settings(tok: str | None, zone: dict | None, host: str) -> dict:
    findings, notes, settings = [], [], {}
    if tok and zone:
        d = rest(tok, f"/zones/{zone['id']}/bot_management")
        if d.get("success"):
            r = d.get("result") or {}
            keep = ("fight_mode", "ai_bots_protection", "crawler_protection", "is_robots_txt_managed", "enable_js",
                    "sbfm_definitely_automated", "sbfm_likely_automated", "sbfm_verified_bots", "using_latest_model")
            settings["bot_management"] = {k: r[k] for k in keep if k in r}
        else:
            notes.append(f"bot_management not readable: {cf_errors(d) or d.get('_http_status')} (token may need Bot Management read, or plan does not expose it)")
        d = rest(tok, f"/zones/{zone['id']}/settings")
        if d.get("success"):
            want = {"security_level", "browser_check", "challenge_ttl", "always_use_https", "ssl", "min_tls_version",
                    "http3", "early_hints", "rocket_loader", "email_obfuscation", "server_side_exclude", "0rtt", "brotli"}
            settings["zone_settings"] = {s.get("id"): s.get("value") for s in d.get("result") or [] if s.get("id") in want}
        else:
            notes.append(f"zone settings not readable: {cf_errors(d)} (token needs Zone Settings:Read or Zone:Read)")
        bm = settings.get("bot_management", {})
        if bm.get("ai_bots_protection") == "block":
            add(findings, "high", "cf_block_ai_bots_on",
                "'Block AI bots' is ON: it blocks AI search and user-triggered agents too (OAI-SearchBot, Claude-SearchBot, "
                "PerplexityBot...). For AI visibility turn it off and decide on training crawlers separately.")
        if bm.get("fight_mode"):
            add(findings, "medium", "cf_bot_fight_mode_on",
                "Bot Fight Mode is ON: it can challenge AI crawlers that are not on Cloudflare's verified list. Check the bots report.")
        if bm.get("is_robots_txt_managed"):
            add(findings, "medium", "cf_managed_robots_on", "Managed robots.txt is ON: Cloudflare prepends rules to your robots.txt.")
        zs = settings.get("zone_settings", {})
        if zs.get("security_level") == "under_attack":
            add(findings, "critical", "cf_under_attack_mode", "Security level is 'I'm Under Attack': every crawler gets a JS challenge.")
        if zs.get("email_obfuscation") == "on":
            add(findings, "info", "cf_email_obfuscation", "Email obfuscation rewrites HTML; harmless for SEO, noted for completeness.")
    else:
        notes.append("API checks skipped (no token or --no-api); live robots.txt check only.")
    robots = live_robots(host)
    if robots.get("present"):
        agents = robots.get("disallowed_agents") or []
        search_like = [a for a in agents if (bot_for(a) or ("", "", ""))[2] in ("classic", "search", "user")]
        add(findings, "high" if search_like else "medium", "cloudflare_managed_robots",
            f"Live robots.txt has a Cloudflare-managed block (not in your repo) disallowing: {', '.join(agents) or 'nothing'}."
            + (f" Search/user agents affected: {', '.join(search_like)}." if search_like else ""))
    elif robots["status"] in (404, 410):
        add(findings, "info", "robots_missing", f"{robots['url']} returned {robots['status']} (crawlers treat this as allow-all).")
    elif robots["status"] != 200:
        add(findings, "high" if robots["status"] >= 500 or robots["status"] == 0 else "medium", "robots_unreachable",
            f"{robots['url']} returned {robots['status'] or robots.get('error')} (5xx makes Google pause crawling; 403 may be a bot rule).")
    return {"title": f"Cloudflare bot-related settings: {host}", "zone": zone, "settings": settings, "robots": robots,
            "findings": findings, "notes": notes}


# --------------------------------------------------------------------------- output
def to_markdown(res: dict) -> str:
    L = [f"# {res['title']}", "", f"Generated {res['generated']}", ""]
    if res.get("findings"):
        L += ["## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in res["findings"]] + [""]
    if res.get("zone") and res["command"] == "zone":
        z = res["zone"]
        L += [f"- Zone: {z.get('name')} · id `{z.get('id')}` · plan {z.get('plan')} · status {z.get('status')}", ""]
    if res.get("bots") is not None:
        L += ["## Requests by crawler", "", "| Bot | Purpose | Requests | 2xx/3xx % | 403/429/503 % | Status mix |", "|---|---|---|---|---|---|"]
        L += [f"| {b['bot']} | {b['purpose']} | {b['requests']} | {b['ok_share_pct']} | {b['blocked_share_pct']} | {b['status_mix']} |" for b in res["bots"]]
        if res.get("firewall"):
            L += ["", "## Firewall events by crawler", "", "| Bot | Action | Source | Events |", "|---|---|---|---|"]
            L += [f"| {f['bot']} | {f['action']} | {f['source']} | {f['events']} |" for f in res["firewall"][:40]]
        if res.get("graphql_errors"):
            L += ["", "## Raw GraphQL errors", ""] + [f"- `{e[:500]}`" for e in res["graphql_errors"][:6]]
        L.append("")
    if res.get("settings") is not None:
        for k, v in res["settings"].items():
            L += [f"## {k}", ""] + [f"- {kk}: {vv}" for kk, vv in v.items()] + [""]
        rb = res.get("robots") or {}
        L += ["## Live robots.txt", "", f"- {rb.get('url')} -> {rb.get('status')}",
              f"- Cloudflare managed block: {'yes' if rb.get('present') else 'no'}"]
        if rb.get("present"):
            L += [f"- Disallowed agents: {', '.join(rb.get('disallowed_agents') or []) or '-'}",
                  f"- Content-Signal: {'; '.join(rb.get('content_signals') or []) or '-'}"]
        L.append("")
    if res.get("notes"):
        L += ["## Notes", ""] + [f"- {n}" for n in res["notes"]]
    return "\n".join(L) + "\n"


def write_outputs(result: dict, md: str, seo_dir: str, slug: str) -> list[str]:
    out = Path(seo_dir) / "audits"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "report"
    j, m = out / f"{TOOL}-{safe}-{stamp}.json", out / f"{TOOL}-{safe}-{stamp}.md"
    j.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    m.write_text(md, encoding="utf-8")
    return [j.as_posix(), m.as_posix()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["zone", "bots", "settings"])
    ap.add_argument("--zone", help="zone / domain name (else CLOUDFLARE_ZONE_ID)")
    ap.add_argument("--host", help="hostname for the live robots.txt check (default: zone name)")
    ap.add_argument("--days", type=int, default=3, help="bots: days to look back, 1-30 (default 3; free plans keep little history)")
    ap.add_argument("--no-api", action="store_true", help="settings: skip API calls, live robots.txt only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--seo-dir", default=".seo")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        if a.command == "settings" and a.no_api:
            if not (a.host or a.zone):
                raise ApiError("--no-api needs --host or --zone")
            res = cmd_settings(None, None, (a.host or a.zone).lower())
        else:
            tok = token()
            zone = resolve_zone(tok, a.zone)
            if a.command == "zone":
                res = {"title": f"Cloudflare zone {zone.get('name')}", "zone": zone, "findings": [], "notes": []}
            elif a.command == "bots":
                res = cmd_bots(tok, zone, max(1, min(30, a.days)))
            else:
                res = cmd_settings(tok, zone, (a.host or zone.get("name") or "").lower())
    except ApiError as e:
        print(f"[cloudflare] {e}", file=sys.stderr)
        return 2
    res.update({"tool": TOOL, "command": a.command, "generated": datetime.now().isoformat(timespec="seconds")})
    md = to_markdown(res)
    if not a.no_write:
        host = a.host or (res.get("zone") or {}).get("name") or a.zone or "zone"
        res["written"] = write_outputs(res, md, a.seo_dir, f"{a.command}-{host}")
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
