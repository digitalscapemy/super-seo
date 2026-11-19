#!/usr/bin/env python3
"""domain_history.py - what a domain used to be, from the Internet Archive (free, no key).

Useful before buying/redirecting a domain, after a migration, or when traffic history looks odd:
  - first and last capture, captures per year (activity gaps = possible drop / expiry / re-registration)
  - status-code mix over time (long 404/5xx periods, redirects to other hosts)
  - sampled page titles per year from archived HTML (topic changes = new owner / repurposed domain)
  - spam-topic signals in old titles (casino, pills, loans, adult, replica... ) — expired-domain abuse risk

  python domain_history.py example.com
  python domain_history.py example.com --samples 6 --json

Uses the Wayback CDX API (web.archive.org/cdx/search/cdx) with a descriptive User-Agent and polite pacing.
Findings are hints for a human: archived captures are incomplete and say nothing about penalties.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib import error, parse, request

UA = "super-seo/1.0 (+https://github.com/digitalscapemy/super-seo)"
CDX = "https://web.archive.org/cdx/search/cdx"
SPAM = re.compile(r"\b(casino|poker|slot|betting|viagra|cialis|pills?|pharmacy|payday|loans?|replica|escort|porn|xxx|"
                  r"crypto (?:giveaway|doubler)|essay writing|cheap (?:jerseys|nfl)|bitcoin casino|judi|togel)\b", re.I)


def get(url: str, timeout: int = 40, attempts: int = 3) -> str:
    for i in range(attempts):
        try:
            with request.urlopen(request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
                return r.read(8_000_000).decode("utf-8", errors="replace")
        except error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
        except (error.URLError, TimeoutError, OSError):
            if i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
    return ""


def captures(domain: str, limit: int = 20000) -> list[dict]:
    q = parse.urlencode({"url": domain, "output": "json", "fl": "timestamp,original,statuscode,mimetype",
                         "collapse": "timestamp:8", "limit": str(limit)})
    rows = json.loads(get(f"{CDX}?{q}") or "[]")
    if not rows:
        return []
    keys = rows[0]
    return [dict(zip(keys, r)) for r in rows[1:]]


def title_of(ts: str, original: str) -> str | None:
    html = get(f"https://web.archive.org/web/{ts}id_/{original}", timeout=30)
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return _html.unescape(" ".join(m.group(1).split()))[:140] if m else None


def analyse(domain: str, samples: int) -> dict:
    caps = captures(domain)
    res = {"domain": domain, "captures": len(caps), "findings": []}
    if not caps:
        res["findings"].append({"severity": "info", "code": "no_archive_history",
                                "message": "No Wayback captures: brand-new domain, or archiving excluded"})
        return res
    years = Counter(c["timestamp"][:4] for c in caps)
    status = defaultdict(Counter)
    for c in caps:
        status[c["timestamp"][:4]][c.get("statuscode", "-")] += 1
    res.update(first=caps[0]["timestamp"], last=caps[-1]["timestamp"], per_year=dict(sorted(years.items())),
               status_per_year={y: dict(v) for y, v in sorted(status.items())})
    ys = sorted(int(y) for y in years)
    gaps = [(a, b) for a, b in zip(ys, ys[1:]) if b - a >= 3]
    if gaps:
        res["findings"].append({"severity": "medium", "code": "activity_gap",
                                "message": "Multi-year capture gaps: " + ", ".join(f"{a}→{b}" for a, b in gaps)
                                           + " (possible expiry and re-registration; check ownership history)"})
    for y, c in status.items():
        total = sum(c.values())
        bad = sum(n for s, n in c.items() if s.startswith(("4", "5")))
        if total >= 5 and bad / total > 0.6:
            res["findings"].append({"severity": "low", "code": "mostly_errors",
                                    "message": f"{y}: {bad}/{total} captures were 4xx/5xx"})
    # sample titles across the timeline (HTML 200 captures only)
    html_caps = [c for c in caps if c.get("statuscode") == "200" and "html" in (c.get("mimetype") or "")]
    picks, seen_years = [], set()
    step = max(1, len(html_caps) // max(1, samples))
    for c in html_caps[::step]:
        if c["timestamp"][:4] not in seen_years:
            picks.append(c)
            seen_years.add(c["timestamp"][:4])
        if len(picks) >= samples:
            break
    titles = []
    for c in picks:
        try:
            t = title_of(c["timestamp"], c["original"])
        except Exception as e:  # noqa: BLE001 - archive hiccups must not abort the report
            t = f"(unavailable: {type(e).__name__})"
        titles.append({"date": c["timestamp"][:8], "title": t})
        time.sleep(1.0)
    res["sampled_titles"] = titles
    spam_hits = [t for t in titles if t["title"] and SPAM.search(t["title"])]
    if spam_hits:
        res["findings"].append({"severity": "high", "code": "spam_history",
                                "message": "Archived titles match spam topics: "
                                           + "; ".join(f"{t['date']} '{t['title'][:60]}'" for t in spam_hits[:3])
                                           + " — expired-domain abuse risk; avoid redirecting this domain into a main site"})
    distinct = {re.sub(r"\W+", " ", (t["title"] or "").lower()).strip() for t in titles if t["title"] and not t["title"].startswith("(")}
    if len(distinct) >= 3 and len(titles) >= 3:
        res["findings"].append({"severity": "info", "code": "topic_changes",
                                "message": f"{len(distinct)} different site titles over time; review whether the topic changed"})
    return res


def render(r: dict) -> str:
    L = [f"# Domain history: {r['domain']}", ""]
    if r.get("first"):
        fmt = lambda ts: datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")
        L.append(f"- Archived {r['captures']} days with captures, from {fmt(r['first'])} to {fmt(r['last'])}")
        L.append("- Captures per year: " + ", ".join(f"{y}: {n}" for y, n in r["per_year"].items()))
    for f in r["findings"]:
        L.append(f"- **{f['severity']}** `{f['code']}`: {f['message']}")
    if r.get("sampled_titles"):
        L += ["", "| Date | Archived title |", "|---|---|"] + [f"| {t['date']} | {(t['title'] or '—').replace('|', '/')} |" for t in r["sampled_titles"]]
    L += ["", "> Archive coverage is incomplete. Treat findings as leads to check (ownership records, old backlinks), not proof."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("domain")
    ap.add_argument("--samples", type=int, default=6, help="archived pages to read for titles (1 request each)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    domain = re.sub(r"^https?://", "", a.domain).strip("/").split("/")[0]
    try:
        r = analyse(domain, a.samples)
    except (error.URLError, OSError, json.JSONDecodeError) as e:
        sys.exit(f"Internet Archive unavailable: {e}")
    text = render(r)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"domain-history-{domain}-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    print(json.dumps(r, indent=2) if a.json else text)


if __name__ == "__main__":
    main()
