#!/usr/bin/env python3
"""posthog.py - organic search, AI-referral and landing-page reports from PostHog (HogQL query API).

  python extensions/posthog/posthog.py organic --days 28
  python extensions/posthog/posthog.py ai-referrals --days 90
  python extensions/posthog/posthog.py landing --days 28 --limit 30
  python extensions/posthog/posthog.py organic --json --no-write

Environment (secrets only from env, never printed):
  POSTHOG_PERSONAL_API_KEY   personal API key with the "query:read" scope (required)
  POSTHOG_PROJECT_ID         numeric project id (required; Project settings > Project ID)
  POSTHOG_HOST               default https://us.posthog.com (EU cloud: https://eu.posthog.com; self-hosted: your URL)

Endpoint: POST {host}/api/projects/{project_id}/query/ with {"query": {"kind": "HogQLQuery", "query": "..."}}.
Writes .seo/audits/posthog-<command>-<project>-<stamp>.json|md unless --no-write. Stdlib only, Python 3.10+.
AI-referral numbers are a floor: many AI clicks carry no referrer and are counted as direct traffic.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode, urlparse

TOOL = "posthog"
DEFAULT_HOST = "https://us.posthog.com"
SEARCH_ENGINES = ["google", "bing", "duckduckgo", "yahoo", "ecosia", "brave"]
AI_DOMAINS = ["chatgpt.com", "chat.openai.com", "perplexity.ai", "gemini.google.com", "copilot.microsoft.com",
              "claude.ai", "you.com", "meta.ai"]
# Regexes are built from the fixed lists above only (no user input reaches HogQL).
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


# --------------------------------------------------------------------------- PostHog
def config() -> dict:
    key = os.environ.get("POSTHOG_PERSONAL_API_KEY", "").strip()
    pid = os.environ.get("POSTHOG_PROJECT_ID", "").strip()
    host = (os.environ.get("POSTHOG_HOST") or DEFAULT_HOST).strip().rstrip("/")
    if not key:
        raise ApiError("POSTHOG_PERSONAL_API_KEY is not set (create one in PostHog > Settings > Personal API keys, scope query:read)")
    if not pid.isdigit():
        raise ApiError("POSTHOG_PROJECT_ID must be set to the numeric project id")
    if urlparse(host).scheme not in ("https", "http"):
        raise ApiError("POSTHOG_HOST must be an http(s) URL")
    return {"key": key, "project": pid, "host": host}


def hogql(cfg: dict, query: str, timeout: float = 90.0) -> list[dict]:
    url = f"{cfg['host']}/api/projects/{cfg['project']}/query/"
    d = http_json("POST", url, {"Authorization": f"Bearer {cfg['key']}"},
                  {"query": {"kind": "HogQLQuery", "query": query}}, timeout=timeout)
    if d.get("error"):
        raise ApiError(f"HogQL error: {str(d.get('error'))[:600]}")
    cols = d.get("columns") or []
    return [dict(zip(cols, row)) for row in (d.get("results") or [])]


def lit(s: str) -> str:
    """HogQL string literal (escape backslashes and quotes)."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def q_referrers(days: int, rx: str, exclude_rx: str | None = None) -> str:
    excl = f" AND NOT match(lower(properties.$referring_domain), {lit(exclude_rx)})" if exclude_rx else ""
    return ("SELECT properties.$referring_domain AS domain, count() AS pageviews, count(DISTINCT $session_id) AS sessions "
            f"FROM events WHERE event = '$pageview' AND timestamp >= now() - INTERVAL {days} DAY "
            f"AND match(lower(properties.$referring_domain), {lit(rx)}){excl} "
            "GROUP BY domain ORDER BY pageviews DESC LIMIT 200")


def q_landing(days: int, limit: int, rx: str | None = None, exclude_rx: str | None = None) -> str:
    """Sessions by first pageview path; single-page sessions as a bounce proxy. Optional referrer filter on the entry."""
    cond = ""
    if rx:
        cond += f" AND match(lower(ref), {lit(rx)})"
    if exclude_rx:
        cond += f" AND NOT match(lower(ref), {lit(exclude_rx)})"
    return ("SELECT landing, count() AS sessions, countIf(pv = 1) AS single_page, "
            "round(countIf(pv = 1) * 100.0 / count(), 1) AS bounce_pct "
            "FROM (SELECT $session_id AS sid, argMin(properties.$pathname, timestamp) AS landing, "
            "argMin(properties.$referring_domain, timestamp) AS ref, count() AS pv FROM events "
            f"WHERE event = '$pageview' AND timestamp >= now() - INTERVAL {days} DAY AND $session_id IS NOT NULL "
            f"GROUP BY sid) WHERE 1 = 1{cond} GROUP BY landing ORDER BY sessions DESC LIMIT {limit}")


def q_ai_weekly(days: int) -> str:
    return ("SELECT toStartOfWeek(timestamp) AS week, properties.$referring_domain AS domain, count() AS pageviews, "
            "count(DISTINCT $session_id) AS sessions FROM events "
            f"WHERE event = '$pageview' AND timestamp >= now() - INTERVAL {days} DAY "
            f"AND match(lower(properties.$referring_domain), {lit(AI_RX)}) GROUP BY week, domain ORDER BY week")


def rollup(rows: list[dict], kind: str) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        c = classify_referrer(r.get("domain"))
        if not c or c[0] != kind:
            continue
        a = agg.setdefault(c[1], {"source": c[1], "pageviews": 0, "sessions": 0, "domains": set()})
        a["pageviews"] += int(r.get("pageviews") or 0)
        a["sessions"] += int(r.get("sessions") or 0)
        a["domains"].add(str(r.get("domain")))
    out = sorted(agg.values(), key=lambda x: -x["pageviews"])
    for a in out:
        a["domains"] = ", ".join(sorted(a["domains"]))
    return out


def landing_findings(rows: list[dict], findings: list, label: str) -> None:
    for r in rows[:10]:
        if int(r.get("sessions") or 0) >= 30 and float(r.get("bounce_pct") or 0) >= 80:
            add(findings, "medium", "high_single_page_share",
                f"{label} landing page {r.get('landing')} has {r.get('bounce_pct')}% single-page sessions "
                f"({r.get('sessions')} sessions): check intent match, above-the-fold answer and internal links.")


def cmd_organic(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    by_domain = hogql(cfg, q_referrers(days, SEARCH_RX, AI_RX))
    engines = rollup(by_domain, "search")
    landing = hogql(cfg, q_landing(days, limit, SEARCH_RX, AI_RX))
    total = sum(e["sessions"] for e in engines)
    if total == 0:
        add(findings, "high", "no_organic_sessions",
            f"No sessions from search engines in {days} days. Check indexing (Search Console / Bing Webmaster) "
            "and that the PostHog snippet runs on public marketing pages.")
    landing_findings(landing, findings, "Organic")
    return {"title": "Organic search traffic (PostHog)", "engines": engines, "landing": landing, "findings": findings,
            "sections": [{"title": "Sessions by search engine", "rows": engines, "cols": ["source", "sessions", "pageviews", "domains"]},
                         {"title": "Top organic landing pages", "rows": landing, "cols": ["landing", "sessions", "single_page", "bounce_pct"]}],
            "notes": ["Search engines matched by referring domain: " + ", ".join(SEARCH_ENGINES) + ". Google sends no query data; "
                      "use Search Console for queries.", "bounce_pct = share of sessions with exactly one pageview (a proxy)."]}


def cmd_ai(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    sources = rollup(hogql(cfg, q_referrers(days, AI_RX)), "ai")
    weekly_raw = hogql(cfg, q_ai_weekly(days))
    weekly: dict[str, dict] = {}
    for r in weekly_raw:
        c = classify_referrer(r.get("domain"))
        if not c:
            continue
        w = str(r.get("week"))[:10]
        row = weekly.setdefault(w, {"week": w, "sessions": 0, "pageviews": 0})
        row["sessions"] += int(r.get("sessions") or 0)
        row["pageviews"] += int(r.get("pageviews") or 0)
        row[c[1]] = row.get(c[1], 0) + int(r.get("sessions") or 0)
    trend = [weekly[k] for k in sorted(weekly)]
    landing = hogql(cfg, q_landing(days, limit, AI_RX))
    total = sum(s["sessions"] for s in sources)
    if total == 0:
        add(findings, "info", "no_ai_referrals",
            f"No referred sessions from AI assistants in {days} days (floor only; see notes). Run the AI visibility "
            "check to see whether the brand is cited at all.")
    else:
        add(findings, "info", "ai_referrals_floor", f"At least {total} sessions arrived from AI assistants in {days} days.")
    return {"title": "AI assistant referrals (PostHog)", "sources": sources, "trend": trend, "landing": landing,
            "total_sessions_floor": total, "findings": findings,
            "sections": [{"title": "Sessions by AI source (floor)", "rows": sources, "cols": ["source", "sessions", "pageviews", "domains"]},
                         {"title": "Weekly trend", "rows": trend, "cols": ["week", "sessions", "pageviews"]},
                         {"title": "Landing pages from AI referrals", "rows": landing, "cols": ["landing", "sessions", "single_page", "bounce_pct"]}],
            "notes": [AI_FLOOR_NOTE, "Matched referrers: " + ", ".join(AI_DOMAINS) + "."]}


def cmd_landing(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    rows = hogql(cfg, q_landing(days, limit))
    landing_findings(rows, findings, "Top")
    return {"title": "Top landing pages (PostHog)", "landing": rows, "findings": findings,
            "sections": [{"title": "Landing pages (all sources)", "rows": rows, "cols": ["landing", "sessions", "single_page", "bounce_pct"]}],
            "notes": ["bounce_pct = share of sessions with exactly one pageview (a proxy, not PostHog's bounce definition)."]}


COMMANDS = {"organic": cmd_organic, "ai-referrals": cmd_ai, "landing": cmd_landing}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=list(COMMANDS))
    ap.add_argument("--days", type=int, default=28, help="look-back window, 1-365 (default 28)")
    ap.add_argument("--limit", type=int, default=25, help="rows for landing-page tables (default 25)")
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
        print(f"[posthog] {e}", file=sys.stderr)
        return 2
    res.update({"tool": TOOL, "command": a.command, "days": days, "source": f"PostHog project {cfg['project']}",
                "generated": datetime.now().isoformat(timespec="seconds")})
    md = to_markdown(res)
    if not a.no_write:
        res["written"] = write_outputs(res, md, a.seo_dir, f"{a.command}-p{cfg['project']}")
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
