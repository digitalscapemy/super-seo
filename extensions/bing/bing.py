#!/usr/bin/env python3
"""bing.py - Bing Webmaster Tools API for super-seo (free). Stdlib only, Python 3.10+.

Setup: Bing Webmaster Tools > Settings > API access > generate an API key, then set BING_WEBMASTER_API_KEY.
Bing feeds Copilot, DuckDuckGo and part of ChatGPT search, so its data is worth reading beside Google's.

  python bing.py sites
  python bing.py queries --site https://example.com/ [--days 90]
  python bing.py pages --site https://example.com/
  python bing.py links --site https://example.com/          # free backlink counts per page
  python bing.py crawl-issues --site https://example.com/
  python bing.py quota --site https://example.com/
  python bing.py submit --site https://example.com/ https://example.com/new-page [--file urls.txt] [--dry-run]

Every command accepts --json and --no-write (skip .seo/audits/bing-<cmd>-<host>-<stamp>.json|md).
If a call fails, the raw Bing error is shown (method names and fields follow Bing's public JSON API; verify against
Bing's documentation if a method returns an error). Exit codes: 0 ok, 1 API error, 2 missing key / bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode, urlparse

BASE = "https://ssl.bing.com/webmaster/api.svc/json/"
UA = "super-seo-bing/1.0"
SUBMIT_BATCH = 500
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
DATE_RX = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")
# Bing CrawlIssues flags (best effort; verify against Bing's docs)
CRAWL_FLAGS = [(1, "301", "low"), (2, "302", "low"), (4, "4xx", "medium"), (8, "5xx", "high"),
               (16, "blocked_by_robots", "low"), (32, "contains_malware", "critical"),
               (64, "important_url_blocked_by_robots", "high"), (128, "dns_error", "high"), (256, "timeout", "high")]

_sleep = time.sleep


class BingError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class MissingKey(Exception):
    pass


def api_key() -> str:
    k = os.environ.get("BING_WEBMASTER_API_KEY")
    if not k:
        raise MissingKey("Set BING_WEBMASTER_API_KEY (Bing Webmaster Tools > Settings > API access > API key).")
    return k


def parse_bing_dates(obj):
    """Convert '/Date(1700000000000)/' (optionally with +hhmm offset) to ISO-8601 UTC strings, recursively."""
    if isinstance(obj, dict):
        return {k: parse_bing_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [parse_bing_dates(v) for v in obj]
    if isinstance(obj, str):
        m = DATE_RX.match(obj)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return obj


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def call(method: str, params: dict | None = None, body: dict | None = None, timeout: float = 60.0, retries: int = 4):
    """Call a Bing Webmaster JSON method; unwraps {"d": ...} and parses /Date()/ values."""
    key = api_key()
    url = BASE + method + "?" + urlencode({"apikey": key, **(params or {})})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    for attempt in range(retries + 1):
        req = request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            break
        except error.HTTPError as e:
            try:
                raw = e.read() or b""
            except Exception:
                raw = b""
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                _sleep(max(2 ** attempt + random.uniform(0, 1), float(ra) if ra and ra.isdigit() else 0))
                continue
            text = _redact(raw.decode("utf-8", errors="replace"), key)[:800]
            raise BingError(e.code, f"{method} -> HTTP {e.code}: {text or e.reason}") from None
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                _sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise BingError(0, f"{method} -> network error: {_redact(str(getattr(e, 'reason', e)), key)}") from None
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise BingError(200, f"{method} -> non-JSON response: {_redact(raw[:300].decode('utf-8', 'replace'), key)}")
    if isinstance(payload, dict) and "d" in payload:
        payload = payload["d"]
    return parse_bing_dates(payload)


# --------------------------------------------------------------------------- output
def host_of(site: str | None) -> str:
    if not site:
        return "account"
    return urlparse(site if "://" in site else "https://" + site).hostname or "site"


def md_table(headers, rows) -> list[str]:
    def cell(v):
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + \
        ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]


def md_findings(findings) -> list[str]:
    if not findings:
        return ["", "No findings."]
    return ["", "## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in findings]


def emit(a, data: dict, md: str) -> None:
    data["findings"] = sorted(data.get("findings", []), key=lambda f: ORDER.get(f["severity"], 9))
    print(json.dumps(data, indent=2, ensure_ascii=False) if a.json else md)
    if not a.no_write:
        out = Path(".seo") / "audits"
        out.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9.-]+", "_", host_of(getattr(a, "site", None)))[:80]
        base = out / f"bing-{a.cmd}-{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        (base.parent / (base.name + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        (base.parent / (base.name + ".md")).write_text(md, encoding="utf-8")
        print(f"[saved] {base}.json / .md", file=sys.stderr)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- commands
def cmd_sites(a):
    sites = call("GetUserSites") or []
    rows = [{"url": s.get("Url"), "verified": s.get("IsVerified")} for s in sites]
    findings = [{"severity": "medium", "code": "site_not_verified", "message": f"{r['url']} is not verified"}
                for r in rows if r["verified"] is False]
    md = ["# Bing Webmaster sites", ""] + md_table(["Site", "Verified"], [[r["url"], r["verified"]] for r in rows]) + md_findings(findings)
    return {"command": "sites", "sites": rows, "raw": sites, "findings": findings}, "\n".join(md)


def aggregate_stats(rows: list[dict], key: str, days: int | None) -> list[dict]:
    """Bing returns one row per key per period; sum clicks/impressions, impression-weighted average position."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    agg: dict[str, dict] = {}
    for r in rows or []:
        d = r.get("Date")
        if cutoff and isinstance(d, str):
            try:
                if datetime.fromisoformat(d.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
        k = r.get(key)
        if k is None:
            continue
        a = agg.setdefault(k, {key.lower(): k, "clicks": 0.0, "impressions": 0.0, "_pos": 0.0})
        imp = _num(r.get("Impressions"))
        a["clicks"] += _num(r.get("Clicks"))
        a["impressions"] += imp
        a["_pos"] += _num(r.get("AvgImpressionPosition")) * imp
    out = []
    for a in agg.values():
        a["avg_position"] = round(a.pop("_pos") / a["impressions"], 1) if a["impressions"] else None
        a["ctr"] = round(a["clicks"] / a["impressions"], 4) if a["impressions"] else 0
        out.append(a)
    return sorted(out, key=lambda x: -x["impressions"])


def _stats_cmd(a, method: str, label: str):
    raw = call(method, {"siteUrl": a.site}) or []
    rows = aggregate_stats(raw, "Query", a.days)
    findings = [] if rows else [{"severity": "info", "code": "no_data", "message": f"Bing returned no {label} stats for {a.site}."}]
    key = "query"
    md = [f"# Bing {label} stats: {a.site}", "", f"- {len(rows)} {label}s" + (f", last {a.days} days" if a.days else ""), ""]
    md += md_table([label.title(), "Impressions", "Clicks", "CTR", "Avg position"],
                   [[r[key], int(r["impressions"]), int(r["clicks"]), f"{100 * r['ctr']:.1f}%", r["avg_position"]] for r in rows[:a.top]])
    md += md_findings(findings)
    return {"command": a.cmd, "site": a.site, "rows": rows, "findings": findings}, "\n".join(md)


def cmd_queries(a):
    return _stats_cmd(a, "GetQueryStats", "query")


def cmd_pages(a):
    return _stats_cmd(a, "GetPageStats", "page")


def cmd_links(a):
    links, page, total_pages = [], 0, 1
    while page < total_pages and page < a.max_pages:
        d = call("GetLinkCounts", {"siteUrl": a.site, "page": page}) or {}
        links += [{"url": l.get("Url"), "inbound_links": int(_num(l.get("Count")))} for l in d.get("Links") or []]
        total_pages = int(_num(d.get("TotalPages"))) or 1
        page += 1
    links.sort(key=lambda x: -x["inbound_links"])
    findings = []
    if not links:
        findings.append({"severity": "info", "code": "no_backlinks", "message": f"Bing reports no inbound links for {a.site}."})
    if page < total_pages:
        findings.append({"severity": "info", "code": "pages_truncated", "message": f"Read {page} of {total_pages} result pages (--max-pages)."})
    total = sum(l["inbound_links"] for l in links)
    md = [f"# Bing inbound links: {a.site}", "", f"- {len(links)} pages with inbound links, {total:,} links in total "
          "(free backlink source; counts are Bing's view of the web)", ""]
    md += md_table(["Page", "Inbound links"], [[l["url"], l["inbound_links"]] for l in links[:a.top]]) + md_findings(findings)
    return {"command": "links", "site": a.site, "total_links": total, "pages": links, "findings": findings}, "\n".join(md)


def decode_issues(flags: int) -> list[tuple[str, str]]:
    return [(name, sev) for bit, name, sev in CRAWL_FLAGS if flags & bit]


def cmd_crawl_issues(a):
    raw = call("GetCrawlIssues", {"siteUrl": a.site}) or []
    rows, findings = [], []
    for r in raw:
        flags = int(_num(r.get("Issues")))
        decoded = decode_issues(flags)
        row = {"url": r.get("Url"), "http_code": r.get("HttpCode"), "issues": [n for n, _ in decoded] or [str(r.get("Issues"))],
               "in_links": r.get("InLinks")}
        rows.append(row)
        sev = min((s for _, s in decoded), key=lambda s: ORDER[s], default="medium")
        findings.append({"severity": sev, "code": "bing_crawl_issue",
                         "message": f"{row['url']}: {', '.join(row['issues'])} (HTTP {row['http_code']})"})
    md = [f"# Bing crawl issues: {a.site}", "", f"- {len(rows)} URLs with issues", ""]
    md += md_table(["URL", "Issues", "HTTP", "Inbound links"], [[r["url"], ", ".join(r["issues"]), r["http_code"], r["in_links"]] for r in rows[:a.top]])
    md += md_findings(findings[:a.top])
    return {"command": "crawl-issues", "site": a.site, "issues": rows, "findings": findings}, "\n".join(md)


def get_quota(site: str) -> dict:
    q = call("GetUrlSubmissionQuota", {"siteUrl": site}) or {}
    return {"daily": int(_num(q.get("DailyQuota"))), "monthly": int(_num(q.get("MonthlyQuota"))), "raw": q}


def cmd_quota(a):
    q = get_quota(a.site)
    findings = [] if q["daily"] else [{"severity": "medium", "code": "no_submission_quota", "message": "Daily URL submission quota is 0."}]
    md = "\n".join([f"# Bing URL submission quota: {a.site}", "", f"- Daily remaining: {q['daily']}",
                    f"- Monthly remaining: {q['monthly']}"] + md_findings(findings))
    return {"command": "quota", "site": a.site, "quota": q, "findings": findings}, md


def same_site(url: str, site: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    s = host_of(site).lower()
    return h == s or h.removeprefix("www.") == s.removeprefix("www.")


def cmd_submit(a):
    urls = list(a.urls)
    if a.file:
        urls += [l.strip() for l in Path(a.file).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    urls = list(dict.fromkeys(urls))
    findings = []
    off = [u for u in urls if not same_site(u, a.site)]
    urls = [u for u in urls if same_site(u, a.site)]
    if off:
        findings.append({"severity": "low", "code": "off_site_urls_skipped", "message": f"Skipped {len(off)} URL(s) not on {a.site}"})
    if not urls:
        raise ValueError("no URLs on the site to submit")
    q = get_quota(a.site)
    allowed = min(q["daily"], q["monthly"]) if q["monthly"] else q["daily"]
    if len(urls) > allowed:
        findings.append({"severity": "medium", "code": "quota_trimmed",
                         "message": f"Quota allows {allowed} URL(s) now; {len(urls) - allowed} not submitted."})
        urls = urls[:allowed]
    submitted, errors = [], []
    for i in range(0, len(urls), SUBMIT_BATCH):
        batch = urls[i:i + SUBMIT_BATCH]
        if a.dry_run:
            submitted += batch
            continue
        try:
            call("SubmitUrlBatch", body={"siteUrl": a.site, "urlList": batch})
            submitted += batch
        except BingError as e:
            errors.append(str(e))
            findings.append({"severity": "high", "code": "submit_failed", "message": str(e)})
            break
    md = [f"# Bing URL submission: {a.site}" + (" (dry run)" if a.dry_run else ""), "",
          f"- Quota before: daily {q['daily']}, monthly {q['monthly']}", f"- Submitted: {len(submitted)}"]
    md += [f"  - {u}" for u in submitted[:50]] + md_findings(findings)
    return {"command": "submit", "site": a.site, "dry_run": a.dry_run, "submitted": submitted, "errors": errors,
            "quota_before": q, "findings": findings}, "\n".join(md)


COMMANDS = {"sites": cmd_sites, "queries": cmd_queries, "pages": cmd_pages, "links": cmd_links,
            "crawl-issues": cmd_crawl_issues, "quota": cmd_quota, "submit": cmd_submit}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--no-write", action="store_true")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sites", parents=[common], help="GetUserSites")
    for name, hlp in (("queries", "GetQueryStats"), ("pages", "GetPageStats"), ("links", "GetLinkCounts (backlinks)"),
                      ("crawl-issues", "GetCrawlIssues"), ("quota", "GetUrlSubmissionQuota"), ("submit", "SubmitUrlBatch")):
        p = sub.add_parser(name, parents=[common], help=hlp)
        p.add_argument("--site", required=True, help="site URL as registered in Bing, e.g. https://example.com/")
        p.add_argument("--top", type=int, default=50)
        if name in ("queries", "pages"):
            p.add_argument("--days", type=int, default=None, help="only rows newer than N days")
        if name == "links":
            p.add_argument("--max-pages", type=int, default=10)
        if name == "submit":
            p.add_argument("urls", nargs="*")
            p.add_argument("--file", help="text file, one URL per line")
            p.add_argument("--dry-run", action="store_true")
    return ap


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args(argv)
    try:
        data, md = COMMANDS[a.cmd](a)
        emit(a, data, md)
        return 0
    except (MissingKey, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except BingError as e:
        print(f"error: Bing API: {e}\n(If the method or a field is rejected, check it against Bing Webmaster API docs.)",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
