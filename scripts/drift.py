#!/usr/bin/env python3
"""drift.py - compare two crawl_audit.py JSON outputs and flag SEO regressions.

Typical release gate:
  python crawl_audit.py https://example.com --out .seo/audits          # baseline (before deploy)
  python crawl_audit.py https://preview-url.app --out .seo/audits      # or after deploy
  python drift.py .seo/audits/crawl-A.json .seo/audits/crawl-B.json --fail-on critical

  python drift.py --latest .seo/audits        # compare the two newest crawl-*.json files

Rules:
  CRITICAL  status 2xx -> 4xx/5xx, noindex added, canonical changed/removed, title removed,
            H1 removed, H1 rewritten (similarity < 0.5), all structured data removed, page disappeared
  WARNING   title changed, meta description changed, all Open Graph removed, schema types changed,
            word count dropped > 50%, technical score dropped >= 10
  INFO      schema added, new page, word count changed > 30%
URLs are matched across hosts by path, so production vs preview compares cleanly.
"""
from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

ORDER = {"critical": 0, "warning": 1, "info": 2}


def key(url: str) -> str:
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    q = "&".join(sorted(x for x in p.query.split("&") if x and not x.startswith(("utm_", "gclid", "fbclid"))))
    return path + ("?" + q if q else "")


def path_of(u: str | None) -> str | None:
    return key(u) if u else None


def load(p: Path) -> dict:
    d = json.loads(p.read_text(encoding="utf-8"))
    return {key(pg["url"]): pg for pg in d["pages"]} | {"__meta__": d}


def compare(a: dict, b: dict) -> list[dict]:
    out = []

    def add(sev, url, rule, before=None, after=None):
        out.append({"severity": sev, "url": url, "rule": rule, "before": before, "after": after})

    ma, mb = a.pop("__meta__"), b.pop("__meta__")
    sa, sb = ma.get("summary", {}).get("technical_score"), mb.get("summary", {}).get("technical_score")
    if sa is not None and sb is not None and sa - sb >= 10:
        add("warning", "(site)", "technical_score_drop", sa, sb)
    for k, pa in a.items():
        pb = b.get(k)
        if pb is None:
            if not mb.get("crawl_complete", True):
                continue  # partial crawl: absence proves nothing
            add("critical" if pa.get("in_sitemap") else "warning", k, "page_missing_from_new_crawl")
            continue
        s1, s2 = pa.get("status", 0), pb.get("status", 0)
        if 200 <= s1 < 300 and s2 >= 400:
            add("critical", k, "status_broke", s1, s2)
        if not pa.get("noindex") and pb.get("noindex"):
            add("critical", k, "noindex_added")
        ca, cb = path_of(pa.get("canonical")), path_of(pb.get("canonical"))
        if ca and not cb:
            add("critical", k, "canonical_removed", pa.get("canonical"))
        elif ca and cb and ca != cb:
            add("critical", k, "canonical_changed", pa.get("canonical"), pb.get("canonical"))
        ta, tb = pa.get("title") or "", pb.get("title") or ""
        if ta and not tb:
            add("critical", k, "title_removed", ta)
        elif ta != tb:
            add("warning", k, "title_changed", ta, tb)
        da, db = pa.get("description") or "", pb.get("description") or ""
        if da != db:
            add("warning", k, "description_changed", da, db)
        ha, hb = (pa.get("h1") or [""])[0], (pb.get("h1") or [""])[0]
        if ha and not hb:
            add("critical", k, "h1_removed", ha)
        elif ha and hb and SequenceMatcher(None, ha.lower(), hb.lower()).ratio() < 0.5:
            add("critical", k, "h1_rewritten", ha, hb)
        sca, scb = set(pa.get("schema_types") or []), set(pb.get("schema_types") or [])
        if sca and not scb:
            add("critical", k, "structured_data_removed", sorted(sca))
        elif sca != scb:
            if sca - scb:
                add("warning", k, "schema_types_removed", sorted(sca - scb))
            if scb - sca:
                add("info", k, "schema_types_added", sorted(scb - sca))
        oga, ogb = pa.get("og") or {}, pb.get("og") or {}
        if any(oga.values()) and not any(ogb.values()):
            add("warning", k, "open_graph_removed")
        wa, wb = pa.get("word_count") or 0, pb.get("word_count") or 0
        if wa >= 100 and wb < 0.5 * wa:
            add("warning", k, "content_shrunk", wa, wb)
        elif wa and abs(wb - wa) / wa > 0.3:
            add("info", k, "content_changed", wa, wb)
    for k in b:
        if k not in a:
            add("info", k, "new_page")
    out.sort(key=lambda x: (ORDER[x["severity"]], x["url"]))
    return out


def history(folder: Path) -> list[dict]:
    """Every audit summary (written by audit.py) in chronological order, with change vs the previous run."""
    rows = []
    for f in sorted(folder.glob("audit-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "readiness" not in d:
            continue
        rows.append({"file": f.name, "when": d.get("when"), "target": d.get("target"), "readiness": d.get("readiness"),
                     "critical": d.get("counts", {}).get("critical", 0), "high": d.get("counts", {}).get("high", 0),
                     "categories": d.get("categories", {})})
    for prev, cur in zip(rows, rows[1:]):
        if prev["readiness"] is not None and cur["readiness"] is not None:
            cur["delta"] = cur["readiness"] - prev["readiness"]
        cur["category_drops"] = {c: (prev["categories"].get(c), v) for c, v in cur["categories"].items()
                                 if v is not None and prev["categories"].get(c) is not None and v < prev["categories"][c] - 5}
    return rows


def render_history(rows: list[dict]) -> str:
    if not rows:
        return "No audit summaries found (run audit.py first; it writes .seo/audits/audit-<stamp>.json)."
    out = ["# Readiness history", "", "| When | Target | Readiness | Δ | Critical | High | Category drops > 5 pts |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        d = r.get("delta")
        drops = ", ".join(f"{c} {a}→{b}" for c, (a, b) in (r.get("category_drops") or {}).items())
        out.append(f"| {(r['when'] or '')[:16]} | {(r['target'] or '')[:40]} | {r['readiness'] if r['readiness'] is not None else '—'} | "
                   f"{'' if d is None else f'{d:+d}'} | {r['critical']} | {r['high']} | {drops or '-'} |")
    first, last = rows[0]["readiness"], rows[-1]["readiness"]
    if first is not None and last is not None and len(rows) > 1:
        out += ["", f"Overall: {first} → {last} ({last - first:+d}) across {len(rows)} audits."]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--latest", metavar="DIR", help="compare the two newest crawl-*.json in DIR")
    ap.add_argument("--fail-on", choices=["critical", "warning"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--history", metavar="DIR", help="readiness and severity trend across every audit-*.json summary in DIR")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.history:
        rows = history(Path(a.history))
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            print(render_history(rows))
        return
    if a.latest:
        files = sorted(Path(a.latest).glob("crawl-*.json"), key=lambda p: p.stat().st_mtime)[-2:]
    else:
        files = [Path(f) for f in a.files]
    if len(files) != 2:
        ap.error("need exactly two crawl JSON files (old new) or --latest DIR with >= 2 crawls")
    changes = compare(load(files[0]), load(files[1]))
    if a.json:
        print(json.dumps(changes, indent=2, ensure_ascii=False))
    else:
        print(f"# SEO drift: {files[0].name} -> {files[1].name}\n")
        if not changes:
            print("No changes detected.")
        for c in changes:
            detail = ""
            if c["before"] is not None or c["after"] is not None:
                detail = f" — `{str(c['before'])[:90]}` → `{str(c['after'])[:90]}`"
            print(f"- **{c['severity']}** {c['url']}: {c['rule']}{detail}")
    if a.fail_on and any(ORDER[c["severity"]] <= ORDER[a.fail_on] for c in changes):
        sys.exit(1)


if __name__ == "__main__":
    main()
