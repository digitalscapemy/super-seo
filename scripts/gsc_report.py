#!/usr/bin/env python3
"""gsc_report.py - turn Search Console exports into an action list. No API, no pip.

Input folder (default .seo/gsc/) with any of:
  Queries.csv / queries*.csv       GSC UI export: "Top queries,Clicks,Impressions,CTR,Position"
  Pages.csv   / pages*.csv         GSC UI export: "Top pages,Clicks,Impressions,CTR,Position"
  page_query*.csv                  admin-panel export: page,query,clicks,impressions,ctr,position
(Search Console > Performance > Export > Download CSV, unzip into .seo/gsc/.)

  python gsc_report.py .seo/gsc --brand "invoicely|invoicly"
  python gsc_report.py .seo/gsc --json

Outputs: striking-distance queries (pos 5-20, >= 50 impressions), low-CTR pages/queries versus the site's own
CTR-by-position curve, cannibalisation candidates (one query split across pages), brand vs non-brand share,
question-style queries to reuse as AI-visibility prompts.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

QUESTION_WORDS = ["how", "what", "which", "why", "when", "where", "who", "is", "are", "can", "does", "do", "should", "best", "top", "vs", "versus"]


def question_rx(extra: list[str]) -> re.Pattern:
    words = "|".join(re.escape(w) for w in QUESTION_WORDS + extra)
    return re.compile(rf"^({words})\b|\bvs\.?\b|\balternative", re.I)


QUESTION = question_rx([])


def num(v: str) -> float:
    v = (v or "").strip().replace(",", "")
    if v.endswith("%"):
        return float(v[:-1] or 0) / 100
    try:
        return float(v)
    except ValueError:
        return 0.0


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        k = {re.sub(r"\s+", "_", (key or "").strip().lower()): v for key, v in r.items()}
        out.append({
            "query": k.get("top_queries") or k.get("query") or k.get("queries"),
            "page": k.get("top_pages") or k.get("page") or k.get("pages") or k.get("landing_page"),
            "clicks": num(k.get("clicks", "0")), "impressions": num(k.get("impressions", "0")),
            "ctr": num(k.get("ctr", "0")), "position": num(k.get("position", "0")),
        })
    return out


def load(folder: Path) -> dict:
    data = {"queries": [], "pages": [], "page_query": []}
    for p in sorted(folder.glob("*.csv")):
        name = p.name.lower()
        rows = read_rows(p)
        if "page_query" in name or (rows and rows[0]["page"] and rows[0]["query"]):
            data["page_query"] += rows
        elif name.startswith("quer") or (rows and rows[0]["query"]):
            data["queries"] += rows
        elif name.startswith("page") or (rows and rows[0]["page"]):
            data["pages"] += rows
    if data["page_query"] and not data["queries"]:   # derive totals from page x query
        agg = defaultdict(lambda: {"clicks": 0.0, "impressions": 0.0, "pos_w": 0.0})
        for r in data["page_query"]:
            a = agg[r["query"]]
            a["clicks"] += r["clicks"]
            a["impressions"] += r["impressions"]
            a["pos_w"] += r["position"] * r["impressions"]
        data["queries"] = [{"query": q, "page": None, "clicks": a["clicks"], "impressions": a["impressions"],
                            "ctr": a["clicks"] / a["impressions"] if a["impressions"] else 0,
                            "position": a["pos_w"] / a["impressions"] if a["impressions"] else 0} for q, a in agg.items()]
    return data


BUCKETS = [(1, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 4.5), (4.5, 5.5), (5.5, 7.5), (7.5, 10.5), (10.5, 15.5), (15.5, 20.5)]


def bucket(pos: float) -> int | None:
    for i, (lo, hi) in enumerate(BUCKETS):
        if lo <= pos < hi or (i == 0 and 0 < pos < hi):
            return i
    return None


def ctr_curve(rows: list[dict]) -> dict[int, float]:
    """Median CTR per position bucket from the site's OWN data (positions 1-20), min 3 rows per bucket."""
    buckets = defaultdict(list)
    for r in rows:
        b = bucket(r["position"])
        if r["impressions"] >= 20 and b is not None:
            buckets[b].append(r["ctr"])
    return {b: statistics.median(v) for b, v in buckets.items() if len(v) >= 3}


def analyse(data: dict, brand: str | None) -> dict:
    brand_rx = re.compile(brand, re.I) if brand else None
    q = data["queries"]
    res = {}
    nb = [r for r in q if not (brand_rx and brand_rx.search(r["query"] or ""))]
    tot_c = sum(r["clicks"] for r in q) or 1
    tot_i = sum(r["impressions"] for r in q) or 1
    if brand_rx:
        bc = sum(r["clicks"] for r in q if brand_rx.search(r["query"] or ""))
        bi = sum(r["impressions"] for r in q if brand_rx.search(r["query"] or ""))
        res["brand_share"] = {"clicks_pct": round(100 * bc / tot_c, 1), "impressions_pct": round(100 * bi / tot_i, 1)}
    res["totals"] = {"queries": len(q), "clicks": round(tot_c), "impressions": round(tot_i)}
    res["striking_distance"] = sorted(
        [r for r in nb if 5 <= r["position"] <= 20 and r["impressions"] >= 50], key=lambda r: -r["impressions"])[:40]

    def low_ctr(rows, key):
        curve = ctr_curve(rows)
        out = []
        for r in rows:
            b = bucket(r["position"])
            # missing band -> nearest band at a WORSE position (lower expected CTR = conservative, fewer false flags)
            exp = next((curve[k] for k in range(b, len(BUCKETS)) if k in curve), None) if b is not None else None
            if exp and r["impressions"] >= 100 and r["position"] <= 10 and r["ctr"] < 0.5 * exp:
                out.append({**r, "expected_ctr": exp})
        return sorted(out, key=lambda r: -(r["impressions"] * (r["expected_ctr"] - r["ctr"])))[:25]

    res["low_ctr_queries"] = low_ctr(nb, "query")
    res["low_ctr_pages"] = low_ctr(data["pages"], "page")

    cannibal = []
    if data["page_query"]:
        byq = defaultdict(lambda: defaultdict(float))
        for r in data["page_query"]:
            byq[r["query"]][r["page"]] += r["impressions"]
        for query, pages in byq.items():
            total = sum(pages.values())
            split = [(p, i) for p, i in pages.items() if i >= 0.15 * total]
            if total >= 100 and len(split) >= 2 and not (brand_rx and brand_rx.search(query)):
                cannibal.append({"query": query, "impressions": round(total),
                                 "pages": [{"page": p, "share_pct": round(100 * i / total)} for p, i in sorted(split, key=lambda x: -x[1])]})
        cannibal.sort(key=lambda c: -c["impressions"])
    res["cannibalisation"] = cannibal[:20]
    res["question_queries"] = [r["query"] for r in sorted(nb, key=lambda r: -r["impressions"]) if QUESTION.search(r["query"] or "")][:30]
    return res


def render(res: dict) -> str:
    t = res["totals"]
    L = ["# Search Console opportunities", "",
         f"- {t['queries']} queries, {t['clicks']:,} clicks, {t['impressions']:,} impressions (period = the export's date range)"]
    if "brand_share" in res:
        L.append(f"- Brand queries: {res['brand_share']['clicks_pct']}% of clicks, {res['brand_share']['impressions_pct']}% of impressions "
                 "(the rest is non-brand demand SEO can grow)")
    L += ["", "## 1. Striking distance (position 5–20, ≥ 50 impressions) — improve the ranking page", "",
          "| Query | Impressions | Clicks | Position |", "|---|---|---|---|"]
    L += [f"| {r['query']} | {r['impressions']:.0f} | {r['clicks']:.0f} | {r['position']:.1f} |" for r in res["striking_distance"]] or ["| none | | | |"]
    L += ["", "## 2. Low CTR for the position (rewrite title/description by hand)", "",
          "Expected CTR = the median CTR of this site's own queries in the same position band (1,2,3,4,5,6-7,8-10,11-15,16-20).", "",
          "| Query / page | Impressions | CTR | Expected | Position |", "|---|---|---|---|---|"]
    for r in res["low_ctr_queries"] + res["low_ctr_pages"]:
        L.append(f"| {r['query'] or r['page']} | {r['impressions']:.0f} | {100 * r['ctr']:.1f}% | {100 * r['expected_ctr']:.1f}% | {r['position']:.1f} |")
    if not (res["low_ctr_queries"] or res["low_ctr_pages"]):
        L.append("| none (or not enough rows per position to build a curve) | | | | |")
    L += ["", "## 3. Possible cannibalisation (one query, several pages ≥ 15% each)", ""]
    if res["cannibalisation"]:
        for c in res["cannibalisation"]:
            L.append(f"- **{c['query']}** ({c['impressions']} impressions): " + ", ".join(f"{p['page']} ({p['share_pct']}%)" for p in c["pages"]))
        L.append("\nCheck intent before merging: different intents on different pages is fine.")
    else:
        L.append("- None found" + ("" if res["totals"]["queries"] else "") + " (needs a page × query export).")
    L += ["", "## 4. Question-style queries (reuse as AI-visibility prompts / FAQ / H2s)", ""]
    L += [f"- {q}" for q in res["question_queries"]] or ["- none"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=".seo/gsc")
    ap.add_argument("--brand", help="regex matching brand queries, e.g. 'acme|akme'")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--question-words", default="", help="extra comma-separated question words for non-English markets")
    a = ap.parse_args(argv)
    global QUESTION
    QUESTION = question_rx([w.strip() for w in a.question_words.split(",") if w.strip()])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder = Path(a.folder)
    if not folder.is_dir() or not list(folder.glob("*.csv")):
        sys.exit(f"No CSV files in {folder}. Export from Search Console > Performance > Export, or from the admin panel.")
    res = analyse(load(folder), a.brand)
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else render(res))


if __name__ == "__main__":
    main()
