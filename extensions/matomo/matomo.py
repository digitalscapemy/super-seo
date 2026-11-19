#!/usr/bin/env python3
"""matomo.py - organic search, AI-referral and landing-page reports from Matomo (Reporting API).

  python extensions/matomo/matomo.py organic --days 28
  python extensions/matomo/matomo.py ai-referrals --days 90
  python extensions/matomo/matomo.py landing --days 28 --limit 30 --json

Environment (secrets only from env, never printed):
  MATOMO_URL       base URL of your Matomo, e.g. https://analytics.example.com (Matomo Cloud or self-hosted)
  MATOMO_TOKEN     token_auth (Administration > Personal > Security > Auth tokens; view access is enough)
  MATOMO_SITE_ID   idSite (default 1)

Calls POST {MATOMO_URL}/index.php with module=API, format=JSON and token_auth in the POST body (never the query
string, so it stays out of server logs). Methods: Referrers.getSearchEngines, Referrers.getWebsites,
Actions.getEntryPageUrls. Writes .seo/audits/matomo-<command>-site<id>-<stamp>.json|md unless --no-write.
Stdlib only, Python 3.10+. AI-referral numbers are a floor: many AI clicks carry no referrer.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode, urlparse

TOOL = "matomo"
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


# --------------------------------------------------------------------------- Matomo
def config() -> dict:
    url = os.environ.get("MATOMO_URL", "").strip().rstrip("/")
    token = os.environ.get("MATOMO_TOKEN", "").strip()
    site = (os.environ.get("MATOMO_SITE_ID") or "1").strip()
    if not url or urlparse(url).scheme not in ("https", "http"):
        raise ApiError("MATOMO_URL must be set to your Matomo base URL (https://...)")
    if not token:
        raise ApiError("MATOMO_TOKEN is not set (Matomo > Administration > Personal > Security > Auth tokens)")
    if not site.isdigit():
        raise ApiError("MATOMO_SITE_ID must be a number")
    if url.endswith("/index.php"):
        url = url[: -len("/index.php")]
    return {"url": url, "token": token, "site": site}


def api(cfg: dict, method: str, **params):
    form = {"module": "API", "method": method, "idSite": cfg["site"], "format": "JSON", **params,
            "token_auth": cfg["token"]}
    d = http_json("POST", cfg["url"] + "/index.php", {}, form=form, timeout=120)
    if isinstance(d, dict) and d.get("result") == "error":
        raise ApiError(f"Matomo {method}: {str(d.get('message'))[:500]}")
    return d


def date_range(days: int, today: date | None = None) -> str:
    end = today or date.today()
    return f"{(end - timedelta(days=days - 1)).isoformat()},{end.isoformat()}"


def as_rows(d) -> list[dict]:
    if isinstance(d, list):
        return [r for r in d if isinstance(r, dict)]
    if isinstance(d, dict):  # multi-period result: {"2026-01-05,2026-01-11": [...]}
        out = []
        for v in d.values():
            out += as_rows(v)
        return out
    return []


def bounce(visits: int, bounces: int) -> float:
    return round(bounces * 100.0 / visits, 1) if visits else 0.0


def landing_rows(raw) -> list[dict]:
    out = []
    for r in as_rows(raw):
        v = int(r.get("entry_nb_visits") or 0)
        b = int(r.get("entry_bounce_count") or 0)
        label = str(r.get("label") or "")
        if r.get("url"):
            label = urlparse(str(r["url"])).path or label
        if label and not label.startswith("/"):
            label = "/" + label
        out.append({"landing": label or "/", "sessions": v, "single_page": b, "bounce_pct": bounce(v, b)})
    return sorted(out, key=lambda x: -x["sessions"])


def landing_findings(rows: list[dict], findings: list, label: str) -> None:
    for r in rows[:10]:
        if int(r.get("sessions") or 0) >= 30 and float(r.get("bounce_pct") or 0) >= 80:
            add(findings, "medium", "high_single_page_share",
                f"{label} landing page {r.get('landing')} has {r.get('bounce_pct')}% bounced sessions "
                f"({r.get('sessions')} sessions): check intent match, above-the-fold answer and internal links.")


def entry_pages(cfg: dict, days: int, limit: int, segment: str | None = None) -> list[dict]:
    params = {"period": "range", "date": date_range(days), "flat": "1", "filter_limit": str(limit),
              "filter_sort_column": "entry_nb_visits", "filter_sort_order": "desc"}
    if segment:
        params["segment"] = segment
    return landing_rows(api(cfg, "Actions.getEntryPageUrls", **params))[:limit]


def ai_segment() -> str:
    # Matomo segment: comma = OR; '=@' = contains. Values are fixed domains (no user input).
    return ",".join(f"referrerName=@{d}" for d in AI_DOMAINS)


def cmd_organic(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    raw = as_rows(api(cfg, "Referrers.getSearchEngines", period="range", date=date_range(days), filter_limit="-1"))
    engines = []
    for r in raw:
        v, b = int(r.get("nb_visits") or 0), int(r.get("bounce_count") or 0)
        engines.append({"source": r.get("label"), "sessions": v, "actions": int(r.get("nb_actions") or 0),
                        "bounce_pct": bounce(v, b)})
    engines.sort(key=lambda x: -x["sessions"])
    landing = entry_pages(cfg, days, limit, "referrerType==search")
    total = sum(e["sessions"] for e in engines)
    if total == 0:
        add(findings, "high", "no_organic_sessions",
            f"No visits from search engines in {days} days. Check indexing (Search Console / Bing Webmaster) and that "
            "the Matomo tracker runs on public marketing pages.")
    landing_findings(landing, findings, "Organic")
    return {"title": "Organic search traffic (Matomo)", "engines": engines, "landing": landing, "findings": findings,
            "sections": [{"title": "Visits by search engine", "rows": engines, "cols": ["source", "sessions", "actions", "bounce_pct"]},
                         {"title": "Top organic entry pages", "rows": landing, "cols": ["landing", "sessions", "single_page", "bounce_pct"]}],
            "notes": ["Search engines as detected by Matomo (referrer type 'search'). Google sends no keyword data; use Search Console for queries."]}


def cmd_ai(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    notes = [AI_FLOOR_NOTE, "Matched referrers: " + ", ".join(AI_DOMAINS) + "."]
    rows = as_rows(api(cfg, "Referrers.getWebsites", period="range", date=date_range(days), filter_limit="-1"))
    try:  # newer Matomo versions may report AI assistants as their own channel; ignore when the method is absent
        rows += as_rows(api(cfg, "Referrers.getAIAssistants", period="range", date=date_range(days), filter_limit="-1"))
    except ApiError:
        pass
    agg: dict[str, dict] = {}
    for r in rows:
        c = classify_referrer(str(r.get("label") or ""))
        if not c or c[0] != "ai":
            continue
        a = agg.setdefault(c[1], {"source": c[1], "sessions": 0, "actions": 0})
        a["sessions"] += int(r.get("nb_visits") or 0)
        a["actions"] += int(r.get("nb_actions") or 0)
    sources = sorted(agg.values(), key=lambda x: -x["sessions"])
    weekly_raw = api(cfg, "Referrers.getWebsites", period="week", date=date_range(days), filter_limit="-1")
    trend = []
    if isinstance(weekly_raw, dict):
        for period_label in sorted(weekly_raw):
            row = {"week": str(period_label).split(",")[0], "sessions": 0}
            for r in as_rows(weekly_raw[period_label]):
                c = classify_referrer(str(r.get("label") or ""))
                if c and c[0] == "ai":
                    row["sessions"] += int(r.get("nb_visits") or 0)
                    row[c[1]] = row.get(c[1], 0) + int(r.get("nb_visits") or 0)
            trend.append(row)
    landing = entry_pages(cfg, days, limit, ai_segment())
    total = sum(s["sessions"] for s in sources)
    if total == 0:
        add(findings, "info", "no_ai_referrals",
            f"No referred visits from AI assistants in {days} days (floor only; see notes). Run the AI visibility check "
            "to see whether the brand is cited at all.")
    else:
        add(findings, "info", "ai_referrals_floor", f"At least {total} visits arrived from AI assistants in {days} days.")
    return {"title": "AI assistant referrals (Matomo)", "sources": sources, "trend": trend, "landing": landing,
            "total_sessions_floor": total, "findings": findings,
            "sections": [{"title": "Visits by AI source (floor)", "rows": sources, "cols": ["source", "sessions", "actions"]},
                         {"title": "Weekly trend", "rows": trend, "cols": ["week", "sessions"]},
                         {"title": "Entry pages from AI referrals", "rows": landing, "cols": ["landing", "sessions", "single_page", "bounce_pct"]}],
            "notes": notes}


def cmd_landing(cfg: dict, days: int, limit: int) -> dict:
    findings: list = []
    rows = entry_pages(cfg, days, limit)
    landing_findings(rows, findings, "Top")
    return {"title": "Top entry pages (Matomo)", "landing": rows, "findings": findings,
            "sections": [{"title": "Entry pages (all sources)", "rows": rows, "cols": ["landing", "sessions", "single_page", "bounce_pct"]}],
            "notes": ["bounce_pct = Matomo entry bounce count / entry visits."]}


COMMANDS = {"organic": cmd_organic, "ai-referrals": cmd_ai, "landing": cmd_landing}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=list(COMMANDS))
    ap.add_argument("--days", type=int, default=28, help="look-back window, 1-365 (default 28)")
    ap.add_argument("--limit", type=int, default=25, help="rows for entry-page tables (default 25)")
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
        print(f"[matomo] {e}", file=sys.stderr)
        return 2
    res.update({"tool": TOOL, "command": a.command, "days": days, "source": f"Matomo site {cfg['site']} ({urlparse(cfg['url']).hostname})",
                "generated": datetime.now().isoformat(timespec="seconds")})
    md = to_markdown(res)
    if not a.no_write:
        res["written"] = write_outputs(res, md, a.seo_dir, f"{a.command}-site{cfg['site']}")
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
