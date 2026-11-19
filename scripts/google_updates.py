#!/usr/bin/env python3
"""google_updates.py - live Google Search update timeline + traffic correlation. Stdlib only, Python 3.10+.

Source: Google Search Status Dashboard (https://status.search.google.com/), read live, cached 24 h. No hardcoded ledger.
  1. https://status.search.google.com/incidents.json         full detail (begin/end/updates) for the ~10 newest incidents
  2. https://status.search.google.com/products/<id>/history  HTML history per product (Crawling, Indexing, Ranking,
     Serving; back to 2021), parsed for older incidents. Product ids come from /products.json.
  (The Atom feed /en/feed.atom only carries the newest entry; summary.json does not exist.)

  python google_updates.py list [--since 2025-01-01] [--kind core,spam] [--refresh] [--no-history] [--json]
  python google_updates.py correlate --gsc .seo/gsc [--since 2025-01-01] [--threshold 0.2] [--min-clicks 5] [--json]

correlate reads daily clicks/impressions from:
  - the GSC UI export "Dates.csv" (Date,Clicks,Impressions,CTR,Position), or any CSV with date + clicks columns
    (e.g. the google extension's raw/gsc-date-*.csv), or the google extension's JSON output (rows with "date");
  - a folder: the newest Dates.csv / *date*.csv / *dates*.csv in it (or in raw/).
It flags windows where the 7-day average of clicks moved more than --threshold (default 20%) against the prior
28 days, and lists Google updates whose rollout window (begin - 3 days .. end + 3 days) overlaps. A date overlap is a
hypothesis, not proof.

Kinds: core, spam, helpful-content, reviews, other-ranking, crawling, indexing, serving.
Cache: %APPDATA%\\super-seo\\cache\\ (Windows) or ~/.cache/super-seo/ (override: SUPER_SEO_CACHE_DIR).
Offline with no cache: prints a clear message and exits 1; never invents updates.
Exit codes: 0 ok, 1 feed unavailable, 2 bad input.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import random
import re
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request

BASE = "https://status.search.google.com/"
INCIDENTS_URL = BASE + "incidents.json"
PRODUCTS_URL = BASE + "products.json"
UA = "super-seo/1.0 (+https://github.com/digitalscapemy/super-seo)"
CACHE_TTL = 24 * 3600
CACHE_FILE = "google-search-status.json"
KINDS = ["core", "spam", "helpful-content", "reviews", "other-ranking", "crawling", "indexing", "serving"]
HYPOTHESIS = "A date overlap is a hypothesis, not proof."
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
HISTORY_ROW = re.compile(
    r'incidents/([A-Za-z0-9]+)"\s*>\s*<span[^>]*>(.*?)</span>.*?__date"\s*>([^<]*)</td>.*?__duration-text"\s*>([^<]*)</span>',
    re.S)
DUR_PART = re.compile(r"(\d+)\s*(day|hour|minute)s?", re.I)

_sleep = time.sleep


class FeedError(Exception):
    pass


# --------------------------------------------------------------------------- fetch + cache
def cache_dir() -> Path:
    override = os.environ.get("SUPER_SEO_CACHE_DIR")
    if override:
        return Path(override)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "super-seo" / "cache"
    return Path.home() / ".cache" / "super-seo"


def http_get(url: str, timeout: float = 20.0, retries: int = 3) -> bytes:
    """GET a fixed Google status URL with a descriptive UA; backoff on 429/5xx (honours Retry-After)."""
    for attempt in range(retries + 1):
        req = request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.9"})
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                _sleep(max(2 ** attempt + random.uniform(0, 1), float(ra) if ra and ra.isdigit() else 0))
                continue
            raise FeedError(f"{url} -> HTTP {e.code}") from None
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            if attempt < retries:
                _sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise FeedError(f"{url} -> network error: {getattr(e, 'reason', e)}") from None
    raise FeedError(f"{url} -> failed")


def fetch_live(with_history: bool = True) -> dict:
    raw = http_get(INCIDENTS_URL)
    try:
        incidents = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise FeedError("incidents.json is not JSON (the endpoint may have changed)") from None
    if not isinstance(incidents, list):
        raise FeedError("incidents.json has an unexpected shape (expected a list)")
    history, notes = [], []
    if with_history:
        try:
            products = json.loads(http_get(PRODUCTS_URL).decode("utf-8", errors="replace")).get("products") or []
        except (FeedError, json.JSONDecodeError, AttributeError) as e:
            products = []
            notes.append(f"products.json unavailable ({e}); history pages skipped")
        for p in products:
            pid, title = p.get("id"), p.get("title")
            if not pid:
                continue
            try:
                page = http_get(f"{BASE}products/{pid}/history").decode("utf-8", errors="replace")
                history += [dict(r, service_name=title) for r in parse_history_html(page)]
            except FeedError as e:
                notes.append(f"history for {title} unavailable ({e})")
            _sleep(0.3)
    return {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": INCIDENTS_URL,
            "incidents": incidents, "history": history, "with_history": with_history, "notes": notes}


def load_feed(refresh: bool = False, with_history: bool = True) -> tuple[dict, str]:
    """Return (feed, origin) where origin is 'cache', 'live' or 'stale-cache'. Raises FeedError when nothing is usable."""
    path = cache_dir() / CACHE_FILE
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
    if cached and not refresh and (cached.get("with_history") or not with_history):
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = CACHE_TTL + 1
        if age < CACHE_TTL:
            return cached, "cache"
    try:
        feed = fetch_live(with_history)
    except FeedError as e:
        if cached:
            cached.setdefault("notes", []).append(f"live fetch failed ({e}); using cache from {cached.get('fetched_at')}")
            return cached, "stale-cache"
        raise
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return feed, "live"


# --------------------------------------------------------------------------- normalise
def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_history_html(page: str) -> list[dict]:
    """Rows from a dashboard product history page: id, title, date ('10 Nov 2026'), duration ('13 days, 16 hours')."""
    out = []
    for iid, title, d, dur in HISTORY_ROW.findall(page):
        out.append({"id": iid, "title": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip(),
                    "date": d.strip(), "duration": html.unescape(dur).strip()})
    return out


def parse_day(s: str) -> datetime | None:
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", s or "")
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc)


def parse_duration(s: str) -> timedelta | None:
    parts = DUR_PART.findall(s or "")
    if not parts:
        return None  # "Information" / "Ongoing": no end yet
    td = timedelta()
    for n, unit in parts:
        td += timedelta(**{unit.lower() + "s": int(n)})
    return td


def classify(title: str, service: str | None) -> str:
    t, s = (title or "").lower(), (service or "").lower()
    if s in ("crawling", "indexing", "serving"):
        return s
    if "core update" in t:
        return "core"
    if "spam" in t:
        return "spam"
    if "helpful content" in t:
        return "helpful-content"
    if "review" in t:
        return "reviews"
    if "crawl" in t and s != "ranking":
        return "crawling"
    if "index" in t and s != "ranking":
        return "indexing"
    return "other-ranking"


def _day(d: datetime | None) -> str | None:
    return d.date().isoformat() if d else None


def normalise(feed: dict) -> list[dict]:
    """Merge incidents.json (preferred) with history rows into {id, title, kind, begin, end, url, service, ongoing}."""
    out: dict[str, dict] = {}
    for i in feed.get("incidents") or []:
        if not isinstance(i, dict) or not i.get("id"):
            continue
        title = (i.get("external_desc") or "").strip()
        service = i.get("service_name") or ((i.get("affected_products") or [{}])[0].get("title"))
        begin, end = parse_iso(i.get("begin")), parse_iso(i.get("end"))
        uri = i.get("uri") or f"incidents/{i['id']}"
        out[i["id"]] = {"id": i["id"], "title": title, "kind": classify(title, service), "service": service,
                        "begin": _day(begin), "end": _day(end), "ongoing": end is None,
                        "url": BASE + uri.lstrip("/"), "source": "incidents.json"}
    for r in feed.get("history") or []:
        if r.get("id") in out or not r.get("id"):
            continue
        begin = parse_day(r.get("date", ""))
        if not begin:
            continue
        dur = parse_duration(r.get("duration", ""))
        end = begin + dur if dur is not None else None
        out[r["id"]] = {"id": r["id"], "title": r.get("title", ""), "kind": classify(r.get("title", ""), r.get("service_name")),
                        "service": r.get("service_name"), "begin": _day(begin), "end": _day(end), "ongoing": end is None,
                        "url": f"{BASE}incidents/{r['id']}", "source": "history"}
    return sorted(out.values(), key=lambda u: (u["begin"] or "", u["id"]), reverse=True)


def filter_updates(updates: list[dict], since: str | None, kinds: list[str] | None) -> list[dict]:
    res = updates
    if since:
        res = [u for u in res if (u["end"] or date.today().isoformat()) >= since or (u["begin"] or "") >= since]
    if kinds:
        res = [u for u in res if u["kind"] in kinds]
    return res


# --------------------------------------------------------------------------- GSC daily series
def _num(v) -> float:
    v = str(v if v is not None else "").strip().replace(",", "")
    if v.endswith("%"):
        v = v[:-1]
    try:
        return float(v)
    except ValueError:
        return 0.0


def _parse_date(v: str) -> date | None:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(v[:20], fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def find_daily_file(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_dir():
        raise ValueError(f"{path} does not exist")
    cands = [p for pat in ("Dates.csv", "dates*.csv", "*date*.csv", "*Dates*.csv", "raw/*date*.csv", "*date*.json", "raw/*date*.json")
             for p in path.glob(pat)]
    cands = [p for p in dict.fromkeys(cands) if "query" not in p.name.lower() and "page" not in p.name.lower()]
    if not cands:
        raise ValueError(f"no daily file in {path} (expected Dates.csv from the GSC export, or the google extension's "
                         "date output: google.py gsc --dimensions date --out .seo/gsc)")
    return max(cands, key=lambda p: p.stat().st_mtime)


def read_daily(path: Path) -> list[dict]:
    """Return [{date, clicks, impressions}] sorted by date, gaps filled with 0 (GSC omits empty days)."""
    f = find_daily_file(path)
    rows: dict[date, dict] = {}
    if f.suffix.lower() == ".json":
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        items = data.get("rows", []) if isinstance(data, dict) else data
        for r in items or []:
            d = _parse_date(str(r.get("date") or r.get("Date") or ""))
            if d:
                rows[d] = {"date": d, "clicks": _num(r.get("clicks")), "impressions": _num(r.get("impressions"))}
    else:
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                k = {re.sub(r"\s+", "_", (key or "").strip().lower()): v for key, v in r.items()}
                d = _parse_date(k.get("date") or k.get("day") or "")
                if d:
                    rows[d] = {"date": d, "clicks": _num(k.get("clicks")), "impressions": _num(k.get("impressions"))}
    if not rows:
        raise ValueError(f"{f}: no date rows found (need Date and Clicks columns)")
    start, end = min(rows), max(rows)
    out, d = [], start
    while d <= end:
        out.append(rows.get(d) or {"date": d, "clicks": 0.0, "impressions": 0.0})
        d += timedelta(days=1)
    return out


def change_windows(series: list[dict], threshold: float = 0.2, min_clicks: float = 5.0) -> list[dict]:
    """7-day average clicks vs the prior 28 days; consecutive flagged days of the same direction form one window."""
    flagged = []
    for i in range(34, len(series)):
        cur = [r["clicks"] for r in series[i - 6:i + 1]]
        base = [r["clicks"] for r in series[i - 34:i - 6]]
        avg7, avg28 = sum(cur) / 7, sum(base) / 28
        if avg28 < min_clicks:
            continue
        ch = (avg7 - avg28) / avg28
        if abs(ch) > threshold:
            imp7 = sum(r["impressions"] for r in series[i - 6:i + 1]) / 7
            imp28 = sum(r["impressions"] for r in series[i - 34:i - 6]) / 28
            flagged.append({"i": i, "dir": "drop" if ch < 0 else "rise", "change": ch, "avg7": avg7, "avg28": avg28,
                            "imp_change": (imp7 - imp28) / imp28 if imp28 else None})
    windows: list[dict] = []
    for f in flagged:
        w = windows[-1] if windows else None
        if w and w["direction"] == f["dir"] and f["i"] - w["_last"] <= 1:
            w["_last"] = f["i"]
            if abs(f["change"]) > abs(w["peak_change"]):
                w.update(peak_change=f["change"], peak_date=series[f["i"]]["date"].isoformat(), avg7=f["avg7"],
                         baseline_avg28=f["avg28"], impressions_change=f["imp_change"])
            continue
        windows.append({"direction": f["dir"], "_first": f["i"], "_last": f["i"], "peak_change": f["change"],
                        "peak_date": series[f["i"]]["date"].isoformat(), "avg7": f["avg7"], "baseline_avg28": f["avg28"],
                        "impressions_change": f["imp_change"]})
    for w in windows:
        w["start"] = series[w["_first"] - 6]["date"].isoformat()  # first day inside the first flagged 7-day window
        w["end"] = series[w.pop("_last")]["date"].isoformat()
        w.pop("_first")
        for k in ("peak_change", "impressions_change"):
            if w[k] is not None:
                w[k] = round(w[k], 3)
        w["avg7"], w["baseline_avg28"] = round(w["avg7"], 1), round(w["baseline_avg28"], 1)
    return windows


def overlaps(window: dict, updates: list[dict], pad_days: int = 3) -> list[dict]:
    ws, we = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    hits = []
    for u in updates:
        if not u.get("begin"):
            continue
        ub = date.fromisoformat(u["begin"]) - timedelta(days=pad_days)
        ue = (date.fromisoformat(u["end"]) if u.get("end") else date.today()) + timedelta(days=pad_days)
        if ws <= ue and ub <= we:
            hits.append(u)
    return hits


# --------------------------------------------------------------------------- output
def md_table(headers, rows) -> list[str]:
    def cell(v):
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + \
        ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]


def source_lines(feed: dict, origin: str, updates_total: int) -> list[str]:
    lines = [f"- Source: Google Search Status Dashboard ({feed.get('source', INCIDENTS_URL)}"
             + (" + product history pages" if feed.get("with_history") else "") + f"), {origin}, fetched {feed.get('fetched_at')}",
             f"- {updates_total} incidents in the feed (the dashboard starts in 2021; older updates are not listed there)"]
    lines += [f"- Note: {n}" for n in feed.get("notes") or []]
    return lines


def cmd_list(a, feed, origin):
    kinds = [k.strip() for k in a.kind.split(",")] if a.kind else None
    if kinds and any(k not in KINDS for k in kinds):
        raise ValueError(f"unknown kind; use one or more of {', '.join(KINDS)}")
    allu = normalise(feed)
    ups = filter_updates(allu, a.since, kinds)
    md = ["# Google Search updates and incidents", ""] + source_lines(feed, origin, len(allu))
    md += [f"- Showing {len(ups)}" + (f" since {a.since}" if a.since else "") + (f", kind {a.kind}" if a.kind else ""), ""]
    md += md_table(["Begin", "End", "Kind", "Title", "URL"],
                   [[u["begin"], u["end"] or "ongoing", u["kind"], u["title"], u["url"]] for u in ups])
    return {"command": "list", "origin": origin, "fetched_at": feed.get("fetched_at"), "updates": ups,
            "findings": []}, "\n".join(md)


def cmd_correlate(a, feed, origin):
    series = read_daily(Path(a.gsc))
    allu = normalise(feed)
    since = a.since or series[0]["date"].isoformat()
    ups = filter_updates(allu, since, None)
    wins = change_windows(series, a.threshold, a.min_clicks)
    findings = []
    for w in wins:
        w["overlapping_updates"] = [{k: u[k] for k in ("id", "title", "kind", "begin", "end", "url")} for u in overlaps(w, ups, a.pad)]
        pct = f"{100 * w['peak_change']:+.0f}%"
        if w["overlapping_updates"]:
            names = "; ".join(f"{u['title']} ({u['begin']}..{u['end'] or 'ongoing'})" for u in w["overlapping_updates"])
            sev = "high" if w["direction"] == "drop" else "info"
            msg = (f"Clicks {w['direction']} {pct} (7-day avg vs prior 28 days) {w['start']}..{w['end']} overlaps: {names}. "
                   f"{HYPOTHESIS} Check which pages/queries moved (gsc_report.py, page-level GSC data) before acting.")
        else:
            sev = "medium" if w["direction"] == "drop" else "info"
            msg = (f"Clicks {w['direction']} {pct} {w['start']}..{w['end']} with no listed Google update within +/-{a.pad} days: "
                   "look for site causes first (releases, drift.py, robots/noindex, tracking, seasonality).")
        findings.append({"severity": sev, "code": f"traffic_{w['direction']}_{'overlaps_update' if w['overlapping_updates'] else 'no_update'}",
                         "message": msg})
    if len(series) < 35:
        findings.append({"severity": "info", "code": "series_too_short",
                         "message": f"Only {len(series)} days of data; need at least 35 (7 + 28) to compare."})
    md = ["# Traffic changes vs Google updates", ""] + source_lines(feed, origin, len(allu))
    md += [f"- GSC daily data: {series[0]['date']} to {series[-1]['date']} ({len(series)} days)",
           f"- Rule: 7-day average clicks vs the prior 28 days, |change| > {100 * a.threshold:.0f}%, baseline >= {a.min_clicks:g} clicks/day; "
           f"update window = begin - {a.pad} days .. end + {a.pad} days",
           f"- **{HYPOTHESIS}** Updates roll out over days or weeks, GSC data lags 2-3 days, and seasonality, releases and "
           "tracking changes move traffic too.", ""]
    if wins:
        md += md_table(["Window", "Direction", "Peak change", "7d avg", "28d baseline", "Impressions change", "Overlapping updates"],
                       [[f"{w['start']}..{w['end']}", w["direction"], f"{100 * w['peak_change']:+.0f}%", w["avg7"], w["baseline_avg28"],
                         "" if w["impressions_change"] is None else f"{100 * w['impressions_change']:+.0f}%",
                         "; ".join(u["title"] for u in w["overlapping_updates"]) or "none"] for w in wins])
    else:
        md.append("No window moved more than the threshold.")
    md += ["", "## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in findings] if findings else []
    return {"command": "correlate", "origin": origin, "fetched_at": feed.get("fetched_at"), "gsc_file": str(find_daily_file(Path(a.gsc))),
            "days": len(series), "threshold": a.threshold, "windows": wins, "note": HYPOTHESIS, "findings": findings}, "\n".join(md)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--refresh", action="store_true", help="ignore the 24 h cache")
    common.add_argument("--no-history", action="store_true", help="only incidents.json (newest ~10 incidents)")
    common.add_argument("--since", help="YYYY-MM-DD")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", parents=[common], help="list updates and incidents")
    p.add_argument("--kind", help="comma list: " + ",".join(KINDS))
    c = sub.add_parser("correlate", parents=[common], help="flag GSC click changes that overlap updates")
    c.add_argument("--gsc", required=True, help="folder with Dates.csv (or the google extension's date output) or a file")
    c.add_argument("--threshold", type=float, default=0.2)
    c.add_argument("--min-clicks", type=float, default=5.0, help="ignore windows whose 28-day baseline is below this")
    c.add_argument("--pad", type=int, default=3, help="days added before/after each update window")
    return ap


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args(argv)
    if a.since:
        try:
            date.fromisoformat(a.since)
        except ValueError:
            print("error: --since must be YYYY-MM-DD", file=sys.stderr)
            return 2
    try:
        feed, origin = load_feed(a.refresh, not a.no_history)
    except FeedError as e:
        print(f"Google Search Status Dashboard is unreachable and there is no cached copy ({e}).\n"
              "No update list is shown rather than an invented one. Retry when online, or read "
              "https://status.search.google.com/ manually.", file=sys.stderr)
        return 1
    try:
        data, md = (cmd_list if a.cmd == "list" else cmd_correlate)(a, feed, origin)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
