#!/usr/bin/env python3
"""umami.py - organic search, AI-referral and landing-page reports from Umami (Cloud or self-hosted).

  python extensions/umami/umami.py organic --days 28
  python extensions/umami/umami.py ai-referrals --days 90
  python extensions/umami/umami.py landing --days 28 --json

Environment (secrets only from env, never printed):
  UMAMI_URL          self-hosted: https://umami.example.com   Umami Cloud: https://api.umami.is/v1 (default when
                     UMAMI_API_KEY is set and UMAMI_URL is not)
  UMAMI_API_KEY      Umami Cloud API key (sent as header x-umami-api-key)
  UMAMI_USERNAME / UMAMI_PASSWORD   self-hosted login -> bearer token (POST /api/auth/login)
  UMAMI_WEBSITE_ID   website UUID (Settings > Websites > Edit)

Endpoints: GET {base}/websites/{id}/metrics?type=referrer|url|entry&startAt=<ms>&endAt=<ms> (self-hosted base is
{UMAMI_URL}/api). Writes .seo/audits/umami-<command>-<site>-<stamp>.json|md unless --no-write. Stdlib only.
AI-referral numbers are a floor: many AI clicks carry no referrer. Umami has no per-page bounce rate in the
metrics endpoint, so landing pages show visitors/views only (site-wide bounce rate comes from /stats).
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
from urllib.parse import urlencode, urlparse

TOOL = "umami"
CLOUD_BASE = "https://api.umami.is/v1"
SEARCH_ENGINES = ["google", "bing", "duckduckgo", "yahoo", "ecosia", "brave"]
AI_DOMAINS = ["chatgpt.com", "chat.openai.com", "perplexity.ai", "gemini.google.com", "copilot.microsoft.com",
              "claude.ai", "you.com", "meta.ai"]
# Regexes are built from the fixed lists above only (no user input reaches queries).
SEARCH_RX = r"(^|\.)(" + "|".join(SEARCH_ENGINES) + r")\.[a-z.]+$"
AI_RX = r"(^|\.)(" + "|".join(re.escape(d) for d in AI_DOMAINS) + r")$"
AI_FLOOR_NOTE = ("AI referral counts are a floor, not a total: ChatGPT/Claude/Perplexity apps, copy-pasted links and "
                 "privacy settings often send no referrer, so many AI visits are counted as direct traffic.")


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- shared helpers
def classify_referrer(domain: str | None) -> tuple[str, str] | None:
    """Return ("ai"|"search", label) for a referring domain, else None. AI is checked first (gemini.google.com)."""
    d = (domain or "").strip().lower()
    if not d:
        return None
    if "://" in d:
        d = urlparse(d).hostname or ""
    d = d.removeprefix("www.")
    for ai in AI_DOMAINS:
        if d == ai or d.endswith("." + ai):
            return "ai", ai
    m = re.search(SEARCH_RX, d)
    if m:
        return "search", m.group(2)
    return None


def http_json(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 60.0,
              retries: int = 5, sleep=time.sleep, form: dict | None = None):
    """JSON request with exponential backoff on 429/5xx (1, 2, 4, 8, 16 s + jitter), honouring Retry-After.
    body= sends JSON; form= sends application/x-www-form-urlencoded (keeps secrets out of the URL)."""
    hdrs = {"Accept": "application/json", "User-Agent": "super-seo/1.0", **headers}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    last = ""
    for attempt in range(retries + 1):
        req = request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw.decode("utf-8") or "{}")
        except error.HTTPError as e:
            text = ""
            try:
                text = e.read().decode("utf-8", errors="replace")[:2000]
            except Exception:
                pass
            if e.code == 429 or e.code >= 500:
                last = f"HTTP {e.code}: {text[:300]}"
                if attempt < retries:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    wait = float(ra) if ra and ra.strip().isdigit() else 2 ** attempt + random.uniform(0, 0.5)
                    sleep(min(wait, 60))
                    continue
            raise ApiError(f"HTTP {e.code}: {text[:600]}") from None
        except (error.URLError, TimeoutError, OSError) as e:
            last = f"network error: {getattr(e, 'reason', e)}"
            if attempt < retries:
                sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
        except json.JSONDecodeError:
            raise ApiError("response was not JSON") from None
    raise ApiError(f"gave up after {retries + 1} attempts ({last})")


def write_outputs(result: dict, md: str, seo_dir: str, slug: str) -> list[str]:
    out = Path(seo_dir) / "audits"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "report"
    j, m = out / f"{TOOL}-{safe}-{stamp}.json", out / f"{TOOL}-{safe}-{stamp}.md"
    j.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    m.write_text(md, encoding="utf-8")
    return [j.as_posix(), m.as_posix()]


def md_table(rows: list[dict], cols: list[str], limit: int = 25) -> list[str]:
    if not rows:
        return ["_No rows._"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:limit]:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return out


def to_markdown(res: dict) -> str:
    L = [f"# {res['title']}", "", f"Source: {res['source']} · period: last {res['days']} days · generated {res['generated']}", ""]
    if res.get("findings"):
        L += ["## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in res["findings"]] + [""]
    for sec in res.get("sections", []):
        L += [f"## {sec['title']}", ""] + md_table(sec["rows"], sec["cols"]) + [""]
    if res.get("notes"):
        L += ["## Notes", ""] + [f"- {n}" for n in res["notes"]] + [""]
    return "\n".join(L)


def add(findings: list, severity: str, code: str, message: str) -> None:
    findings.append({"severity": severity, "code": code, "message": message})


# --------------------------------------------------------------------------- Umami
def config() -> dict:
    url = (os.environ.get("UMAMI_URL") or "").strip().rstrip("/")
    key = (os.environ.get("UMAMI_API_KEY") or "").strip()
    user = (os.environ.get("UMAMI_USERNAME") or "").strip()
    pw = os.environ.get("UMAMI_PASSWORD") or ""
    site = (os.environ.get("UMAMI_WEBSITE_ID") or "").strip()
    if not url and key:
        url = CLOUD_BASE
    if not url or urlparse(url).scheme not in ("https", "http"):
        raise ApiError("UMAMI_URL must be set (self-hosted base URL, or Umami Cloud https://api.umami.is/v1)")
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", site):
        raise ApiError("UMAMI_WEBSITE_ID must be set to the website UUID")
    if key:
        base = url if re.search(r"/v1$|/api$", url) else url + "/api"
        if urlparse(url).hostname == "api.umami.is" and not url.endswith("/v1"):
            base = url + "/v1"
        return {"base": base, "headers": {"x-umami-api-key": key}, "site": site}
    if user and pw:
        base = url if url.endswith("/api") else url + "/api"
        d = http_json("POST", base + "/auth/login", {}, {"username": user, "password": pw}, timeout=30)
        tok = d.get("token") if isinstance(d, dict) else None
        if not tok:
            raise ApiError("Umami login did not return a token (check UMAMI_USERNAME / UMAMI_PASSWORD)")
        return {"base": base, "headers": {"Authorization": f"Bearer {tok}"}, "site": site}
    raise ApiError("set UMAMI_API_KEY (Umami Cloud) or UMAMI_USERNAME + UMAMI_PASSWORD (self-hosted)")


def window(days: int, now: datetime | None = None) -> tuple[int, int]:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def metrics(cfg: dict, mtype: str, start: int, end: int, limit: int = 500, **filters) -> list[dict]:
    q = {"type": mtype, "startAt": start, "endAt": end, "limit": limit, **filters}
    url = f"{cfg['base']}/websites/{cfg['site']}/metrics?{urlencode(q)}"
    d = http_json("GET", url, cfg["headers"], timeout=60)
    rows = d if isinstance(d, list) else (d.get("data") if isinstance(d, dict) else []) or []
    return [{"x": r.get("x"), "y": int(r.get("y") or 0)} for r in rows if isinstance(r, dict)]


def page_metrics(cfg: dict, start: int, end: int, limit: int, **filters) -> tuple[list[dict], str]:
    """Entry pages when the server supports type=entry, else pages by views (type=url, or type=path on Umami 3)."""
    for t in ("entry", "url", "path"):
        try:
            return metrics(cfg, t, start, end, limit, **filters), t
        except ApiError as e:
            if not re.search(r"HTTP 400|HTTP 422|invalid|type", str(e), re.I):
                raise
    return [], "none"


def rollup(rows: list[dict], kind: str) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        c = classify_referrer(r["x"])
        if not c or c[0] != kind:
            continue
        a = agg.setdefault(c[1], {"source": c[1], "visitors": 0, "domains": set()})
        a["visitors"] += r["y"]
        a["domains"].add(str(r["x"]))
    out = sorted(agg.values(), key=lambda x: -x["visitors"])
    for a in out:
        a["domains"] = ", ".join(sorted(a["domains"]))
    return out


def pages_for_referrers(cfg: dict, domains: list[str], start: int, end: int, limit: int) -> tuple[list[dict], str]:
    agg: dict[str, int] = {}
    kind = "none"
    for d in domains[:5]:  # top referrer domains only, to respect API quotas
        rows, kind = page_metrics(cfg, start, end, limit, referrer=d)
        for r in rows:
            agg[str(r["x"])] = agg.get(str(r["x"]), 0) + r["y"]
    out = [{"landing": k, "count": v} for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]
    return out[:limit], kind


def cmd_organic(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    start, end = window(days)
    refs = metrics(cfg, "referrer", start, end)
    engines = rollup(refs, "search")
    doms = [d for e in engines for d in e["domains"].split(", ")]
    landing, kind = pages_for_referrers(cfg, doms, start, end, limit)
    if sum(e["visitors"] for e in engines) == 0:
        add(findings, "high", "no_organic_sessions",
            f"No visitors from search engines in {days} days. Check indexing (Search Console / Bing Webmaster) and that "
            "the Umami script runs on public marketing pages.")
    return {"title": "Organic search traffic (Umami)", "engines": engines, "landing": landing, "page_metric": kind,
            "findings": findings,
            "sections": [{"title": "Visitors by search engine", "rows": engines, "cols": ["source", "visitors", "domains"]},
                         {"title": f"Pages for organic visitors (metric: {kind})", "rows": landing, "cols": ["landing", "count"]}],
            "notes": ["Search engines matched by referrer domain: " + ", ".join(SEARCH_ENGINES) + ".",
                      "Page counts are per referrer filter on the top search domains; 'entry' = landing pages, 'url'/'path' = page views."]}


def cmd_ai(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    start, end = window(days)
    sources = rollup(metrics(cfg, "referrer", start, end), "ai")
    trend = []
    weeks = max(1, min(26, (days + 6) // 7))
    for i in range(weeks, 0, -1):
        ws, we = end - i * 7 * 86400000, end - (i - 1) * 7 * 86400000
        row = {"week": datetime.fromtimestamp(ws / 1000, timezone.utc).date().isoformat(), "visitors": 0}
        for s in rollup(metrics(cfg, "referrer", ws, we), "ai"):
            row["visitors"] += s["visitors"]
            row[s["source"]] = s["visitors"]
        trend.append(row)
    doms = [d for s in sources for d in s["domains"].split(", ")]
    landing, kind = pages_for_referrers(cfg, doms, start, end, limit)
    total = sum(s["visitors"] for s in sources)
    if total == 0:
        add(findings, "info", "no_ai_referrals",
            f"No referred visitors from AI assistants in {days} days (floor only; see notes). Run the AI visibility "
            "check to see whether the brand is cited at all.")
    else:
        add(findings, "info", "ai_referrals_floor", f"At least {total} visitors arrived from AI assistants in {days} days.")
    return {"title": "AI assistant referrals (Umami)", "sources": sources, "trend": trend, "landing": landing,
            "page_metric": kind, "total_sessions_floor": total, "findings": findings,
            "sections": [{"title": "Visitors by AI source (floor)", "rows": sources, "cols": ["source", "visitors", "domains"]},
                         {"title": "Weekly trend", "rows": trend, "cols": ["week", "visitors"]},
                         {"title": f"Pages for AI-referred visitors (metric: {kind})", "rows": landing, "cols": ["landing", "count"]}],
            "notes": [AI_FLOOR_NOTE, "Matched referrers: " + ", ".join(AI_DOMAINS) + "."]}


def cmd_landing(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    start, end = window(days)
    rows, kind = page_metrics(cfg, start, end, limit)
    landing = [{"landing": r["x"], "count": r["y"]} for r in rows][:limit]
    notes = [f"Page metric used: {kind} ('entry' = landing pages; 'url'/'path' = page views on older/newer Umami)."]
    try:
        st = http_json("GET", f"{cfg['base']}/websites/{cfg['site']}/stats?{urlencode({'startAt': start, 'endAt': end})}",
                       cfg["headers"], timeout=60)
        val = lambda k: (st.get(k) or {}).get("value") if isinstance(st.get(k), dict) else st.get(k)  # noqa: E731
        visits, bounces = int(val("visits") or 0), int(val("bounces") or 0)
        if visits:
            pct = round(bounces * 100.0 / visits, 1)
            notes.append(f"Site-wide bounce proxy: {pct}% of {visits} visits ({bounces} single-page visits).")
            if pct >= 80 and visits >= 100:
                add(findings, "medium", "high_single_page_share", f"Site-wide {pct}% of visits are single-page: check landing intent and internal links.")
    except ApiError as e:
        notes.append(f"stats endpoint unavailable: {str(e)[:120]}")
    return {"title": "Top landing pages (Umami)", "landing": landing, "page_metric": kind, "findings": findings,
            "sections": [{"title": f"Pages (metric: {kind})", "rows": landing, "cols": ["landing", "count"]}], "notes": notes}


COMMANDS = {"organic": cmd_organic, "ai-referrals": cmd_ai, "landing": cmd_landing}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=list(COMMANDS))
    ap.add_argument("--days", type=int, default=28, help="look-back window, 1-365 (default 28)")
    ap.add_argument("--limit", type=int, default=25, help="rows for page tables (default 25)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--no-write", action="store_true", help="do not write .seo/audits files")
    ap.add_argument("--seo-dir", default=".seo")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    days, limit = max(1, min(365, a.days)), max(1, min(500, a.limit))
    try:
        cfg = config()
        res = COMMANDS[a.command](cfg, days, limit)
    except ApiError as e:
        print(f"[umami] {e}", file=sys.stderr)
        return 2
    res.update({"tool": TOOL, "command": a.command, "days": days, "source": f"Umami website {cfg['site']}",
                "generated": datetime.now().isoformat(timespec="seconds")})
    md = to_markdown(res)
    if not a.no_write:
        res["written"] = write_outputs(res, md, a.seo_dir, f"{a.command}-{cfg['site'][:8]}")
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
