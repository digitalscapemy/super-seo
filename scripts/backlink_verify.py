#!/usr/bin/env python3
"""backlink_verify.py - check that a list of backlinks still exists (stdlib only, polite, one request at a time).

For each source page it reports: HTTP status (200 / 4xx / 5xx / blocked / error), whether an <a href> to the target
domain (or the exact expected URL) is in the raw HTML, the link's rel (nofollow / ugc / sponsored), anchor text,
whether the source page is noindex or canonicalised elsewhere (the link may pass little value then), and whether
robots.txt disallows the source page for Googlebot (Google cannot see the link). Links injected by JavaScript are
not visible to this check (raw HTML only).

Summary buckets: live_dofollow | live_nofollow | lost (page up, link gone) | page_gone (404/410/other 4xx/5xx)
| unverified (401/403/429/WAF challenge, network guard, fetch error). Redirects to another host are noted as a caveat.

Input file (--input): CSV or TXT.
  TXT: one source URL per line; optionally "source_url,expected_target" or whitespace-separated.
  CSV with a header row. Recognised columns (case/space-insensitive):
    source:  source_url, source, source url, source page, referring page, referring url, linking page, from, url
    target:  expected_target, target, target url, target_url, destination, to, link url
    anchor:  anchor, anchor text  (optional; reported as the expected anchor)
  The Bing Webmaster Tools backlinks export (Backlinks -> export CSV, columns "Source Url", "Target Url",
  "Anchor Text") can be passed as-is. Rows without a target use --target.
Expected target: a bare domain ("example.com", matches the domain and its subdomains; a root URL such as
"https://example.com/" counts as the domain) or a full URL with a path (exact match after normalisation; trailing
slash and www. ignored).

Usage:
  python backlink_verify.py --input backlinks.csv --target example.com
  python backlink_verify.py --input links.txt --target https://example.com/pricing --delay 2 --json --no-write

Outputs <out>/backlinks-<target>-<timestamp>.json and .md (default --out .seo/audits) unless --no-write.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402

UA_DEFAULT = ca.UA_DEFAULT
SRC_COLS = ("source_url", "source", "source url", "source page", "referring page", "referring url", "referring page url",
            "linking page", "from", "url", "page url")
TGT_COLS = ("expected_target", "target", "target url", "target_url", "destination", "to", "link url", "target page")
ANCHOR_COLS = ("anchor", "anchor text", "anchor_text")
QUALIFIERS = ("nofollow", "ugc", "sponsored")


def _norm_col(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip().lstrip("﻿").lower().replace("_", " ")).strip()


def parse_input(text: str, default_target: str | None = None) -> list[dict]:
    text = text.lstrip("﻿")
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return []
    rows: list[dict] = []
    first = lines[0]
    delim = "\t" if "\t" in first else ("," if "," in first else (";" if ";" in first else None))
    header_like = delim and not re.match(r"\s*https?://", first.split(delim)[0])
    if header_like:
        reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)
        header = [_norm_col(h) for h in next(reader)]
        def idx(names):
            norm = [_norm_col(n) for n in names]
            for n in norm:
                if n in header:
                    return header.index(n)
            return None
        si, ti, ai = idx(SRC_COLS), idx(TGT_COLS), idx(ANCHOR_COLS)
        if si is None:
            raise ValueError(f"no source column found in header {header}; expected one of {SRC_COLS}")
        for rec in reader:
            if len(rec) <= si or not rec[si].strip():
                continue
            rows.append({"source_url": rec[si].strip(),
                         "expected_target": (rec[ti].strip() if ti is not None and len(rec) > ti and rec[ti].strip() else default_target),
                         "expected_anchor": rec[ai].strip() if ai is not None and len(rec) > ai else None})
    else:
        for l in lines:
            parts = [p.strip() for p in re.split(r"[,\t;]|\s+", l.strip()) if p.strip()]
            rows.append({"source_url": parts[0], "expected_target": parts[1] if len(parts) > 1 else default_target,
                         "expected_anchor": None})
    out, seen = [], set()
    for r in rows:
        if not re.match(r"https?://", r["source_url"], re.I):
            r["source_url"] = "https://" + r["source_url"]
        key = (r["source_url"], r["expected_target"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _strip_www(h: str) -> str:
    h = (h or "").lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def target_matcher(target: str):
    t = (target or "").strip()
    if not t:
        raise ValueError("no expected target (pass --target or an expected_target column)")
    if re.match(r"https?://", t, re.I) and (urlparse(t).path not in ("", "/") or urlparse(t).query):
        want = ca.normalize(t).rstrip("/")
        def match(u):
            return ca.normalize(u).rstrip("/").replace("://www.", "://") == want.replace("://www.", "://")
        return match, "url"
    dom = _strip_www(urlparse(t).hostname if "://" in t else t.split("/")[0])
    def match(u):
        h = _strip_www(urlparse(u).hostname or "")
        return h == dom or h.endswith("." + dom)
    return match, "domain"


def analyze_source(res: dict, target: str, googlebot_allowed: bool | None = None) -> dict:
    """res = crawl_audit.fetch() result for the source page."""
    out = {"source_url": res["url"], "final_url": res["final_url"], "status": res["status"], "error": res.get("error"),
           "state": None, "links": [], "link_found": False, "rel": None, "anchor": None, "noindex": False,
           "canonical": None, "canonicalised_elsewhere": False, "meta_nofollow": False,
           "googlebot_allowed": googlebot_allowed, "caveats": []}
    st = res["status"]
    if st == 0:
        out["state"] = "unverified"
        out["status_class"] = "blocked" if "blocked" in (res.get("error") or "") else "error"
        return out
    page = ca.audit_page(res, urlparse(res["final_url"]).hostname or "")
    if page.get("blocked"):
        out["state"], out["status_class"] = "unverified", "blocked"
        return out
    if st >= 400:
        out["state"], out["status_class"] = "page_gone", f"{st // 100}xx"
        return out
    out["status_class"] = "200" if st == 200 else str(st)
    if _strip_www(urlparse(res["final_url"]).hostname or "") != _strip_www(urlparse(res["url"]).hostname or ""):
        out["caveats"].append(f"source redirects to another host ({res['final_url']})")
    p = ca.PageParser()
    try:
        p.feed(res["body"])
    except Exception:  # noqa: BLE001
        pass
    match, mode = target_matcher(target)
    base = urljoin(res["final_url"], p.base_href) if p.base_href else res["final_url"]
    for a in p.anchors:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absu = urljoin(base, href)
        if urlparse(absu).scheme in ("http", "https") and match(absu):
            rels = [r for r in (a.get("rel") or "").lower().split() if r]
            out["links"].append({"href": absu[:300], "rel": " ".join(rels) or None, "anchor": (a.get("text") or "")[:150] or "(image/empty)",
                                 "qualified": any(q in rels for q in QUALIFIERS)})
    robots = " ".join(filter(None, [p.meta("robots"), p.meta("googlebot")])).lower()
    xr = res["headers"].get("x-robots-tag", "").lower()
    out["noindex"] = "noindex" in robots or "noindex" in xr
    out["meta_nofollow"] = "nofollow" in robots or "nofollow" in xr
    canon = p.link_rel("canonical")
    if canon and canon[0].get("href"):
        c = urljoin(res["final_url"], canon[0]["href"])
        out["canonical"] = c
        out["canonicalised_elsewhere"] = ca.normalize(c).rstrip("/") != ca.normalize(res["final_url"]).rstrip("/")
    if out["links"]:
        out["link_found"] = True
        ranked = sorted(out["links"], key=lambda l: (l["qualified"], l["anchor"] == "(image/empty)"))
        best = ranked[0]
        out["rel"], out["anchor"] = best["rel"], best["anchor"]
        dofollow = not best["qualified"] and not out["meta_nofollow"]
        out["state"] = "live_dofollow" if dofollow else "live_nofollow"
    else:
        out["state"] = "lost"
    if out["noindex"]:
        out["caveats"].append("source page is noindex (link likely passes little or no value)")
    if out["canonicalised_elsewhere"]:
        out["caveats"].append(f"source canonicalises to {out['canonical']} (signals consolidate there; check the link exists on it)")
    if out["meta_nofollow"]:
        out["caveats"].append("page-level meta robots nofollow")
    if googlebot_allowed is False:
        out["caveats"].append("robots.txt disallows Googlebot on the source page (Google cannot see the link)")
    if mode == "domain" and len(out["links"]) > 1:
        out["caveats"].append(f"{len(out['links'])} links to the target on this page")
    return out


def run(rows: list[dict], ua: str = UA_DEFAULT, delay: float = 1.0, timeout: float = 20.0, allow_private: bool = False,
        fetcher=None, robots_reader=None) -> dict:
    fetcher = fetcher or (lambda u: ca.fetch(u, ua, timeout=timeout))
    robots_reader = robots_reader or (lambda origin: ca.read_robots(origin, ua))
    robots_cache: dict[str, object] = {}
    results = []
    last_host = None
    # Backlink sources are third-party URLs from a file: protection stays ON for every row unless the user explicitly
    # passes --allow-private. A row's own host can never switch it off.
    ca.PUBLIC_ONLY["on"] = not allow_private
    for i, r in enumerate(rows):
        src = r["source_url"]
        host = urlparse(src).hostname
        if delay and i and host == last_host:
            time.sleep(delay)
        elif delay and i:
            time.sleep(min(delay, 0.3))
        last_host = host
        origin = f"{urlparse(src).scheme}://{urlparse(src).netloc}"
        if origin not in robots_cache:
            try:
                robots_cache[origin] = robots_reader(origin).get("parser")
            except Exception:  # noqa: BLE001
                robots_cache[origin] = None
        rp = robots_cache[origin]
        allowed = rp.can_fetch("Googlebot", src) if rp else None
        res = fetcher(src)
        try:
            item = analyze_source(res, r.get("expected_target") or "", allowed)
        except ValueError as e:
            item = {"source_url": src, "state": "unverified", "status": res["status"], "error": str(e), "caveats": []}
        item["expected_target"] = r.get("expected_target")
        if r.get("expected_anchor"):
            item["expected_anchor"] = r["expected_anchor"]
            if item.get("anchor") and r["expected_anchor"].lower() not in item["anchor"].lower():
                item["caveats"].append(f"anchor changed (expected '{r['expected_anchor'][:60]}')")
        results.append(item)
    counts = Counter(x["state"] for x in results)
    return {"tool": "backlink_verify", "version": 1, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": len(results),
            "summary": {k: counts.get(k, 0) for k in ("live_dofollow", "live_nofollow", "lost", "page_gone", "unverified")},
            "results": results,
            "note": "Raw HTML only: links added by JavaScript are not seen. 'live_nofollow' includes rel=ugc/sponsored "
                    "and page-level meta nofollow."}


def to_markdown(r: dict, label: str) -> str:
    s = r["summary"]
    L = [f"# Backlink verification: {label}", "", f"- Checked: {r['checked_at']} | sources: {r['total']}",
         f"- **Live dofollow: {s['live_dofollow']}** | live nofollow/ugc/sponsored: {s['live_nofollow']} | lost: {s['lost']} | "
         f"page gone: {s['page_gone']} | unverified: {s['unverified']}", f"- {r['note']}", "",
         "| Source | Status | State | Rel | Anchor | Noindex | Canonical elsewhere | Googlebot allowed | Caveats |",
         "|---|---|---|---|---|---|---|---|---|"]
    order = {"lost": 0, "page_gone": 1, "unverified": 2, "live_nofollow": 3, "live_dofollow": 4}
    for x in sorted(r["results"], key=lambda x: order.get(x["state"], 9)):
        def cell(v):
            return str(v if v not in (None, "") else "-").replace("|", "\\|")[:120]
        status = x.get("status") or (x.get("error") or "")[:60]
        L.append(f"| {x['source_url']} | {cell(status)} | {x['state']} | {cell(x.get('rel'))} | {cell(x.get('anchor'))} | "
                 f"{'Y' if x.get('noindex') else 'N'} | {'Y' if x.get('canonicalised_elsewhere') else 'N'} | "
                 f"{cell(x.get('googlebot_allowed'))} | {cell('; '.join(x.get('caveats') or []))} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="CSV or TXT of source pages (see docstring for columns)")
    ap.add_argument("--target", help="expected target domain or URL for rows without one")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests to the same host (default 1.0)")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--max", type=int, default=500, help="max sources checked (default 500)")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--allow-private", action="store_true", help="allow private/internal source hosts")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        rows = parse_input(Path(a.input).read_text(encoding="utf-8-sig", errors="replace"), a.target)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if not rows:
        print("No source URLs in input.", file=sys.stderr)
        sys.exit(2)
    if any(not r["expected_target"] for r in rows):
        print("error: some rows have no expected target; pass --target", file=sys.stderr)
        sys.exit(2)
    r = run(rows[: a.max], a.ua, a.delay, a.timeout, a.allow_private)
    label = a.target or rows[0]["expected_target"]
    md = to_markdown(r, label)
    js = json.dumps(r, indent=2, ensure_ascii=False)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tag = re.sub(r"[^\w.-]", "_", (urlparse(label).hostname if "://" in label else label) or "target")
        (out / f"backlinks-{tag}-{stamp}.json").write_text(js, encoding="utf-8")
        (out / f"backlinks-{tag}-{stamp}.md").write_text(md, encoding="utf-8")
        print(f"[saved] {out / f'backlinks-{tag}-{stamp}.md'}", file=sys.stderr)
    print(js if a.json else md)


if __name__ == "__main__":
    main()
