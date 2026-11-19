#!/usr/bin/env python3
"""programmatic_check.py - measure the quality of a set of similar (scaled / programmatic) pages.

For each page it extracts the visible main text (nav, header, footer, aside, script, style, form chrome excluded;
<main> only when the page has one), builds 5-word shingles and measures:
  - unique-content ratio: share of the page's words not covered by any shingle that also appears on another page
  - shared boilerplate ratio: share of words covered by shingles present on at least half of the set
  - near-duplicate pairs (Jaccard similarity of shingle sets >= 0.8)
  - word counts, templated metadata (description opening with the title, the same stock CTA across pages,
    titles that differ only by one variable token/phrase)
Gates (references/saas-pages.md section 4):
  unique < 30% = high (do not publish / noindex) | 30-40% = medium | boilerplate > 60% = medium | < 300 words = low
  set size >= 100 = info (needs human review) | >= 500 = high (justify the scale)
It prints a per-page table, a set summary and a 5-10% random sample of pages for human review.

Input (pick one):
  python programmatic_check.py --urls urls.txt
  python programmatic_check.py --from-crawl .seo/audits/crawl-example.com-20250101-120000.json --pattern "/integrations/"
  python programmatic_check.py --sitemap https://example.com/sitemap.xml --pattern "/integrations/" --max-pages 200
Options: --json, --no-write, --out DIR, --delay S, --seed N (review sample), --timeout S.
--pattern is a regular expression searched in each URL.

Outputs <out>/programmatic-<host>-<timestamp>.json and .md (default --out .seo/audits) unless --no-write.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402

SEVERITY_ORDER = ca.SEVERITY_ORDER
UA_DEFAULT = ca.UA_DEFAULT
SHINGLE = 5
NEAR_DUP = 0.8
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.U)


def add(findings, severity, code, message, **extra):
    findings.append({"severity": severity, "code": code, "message": message, **extra})


class MainTextParser(HTMLParser):
    SKIP = {"nav", "header", "footer", "aside", "script", "style", "noscript", "template", "svg", "iframe", "select", "button"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_stack: list[str] = []
        self.main_depth = 0
        self.has_main = False
        self.all_text: list[str] = []
        self.main_text: list[str] = []
        self.title = None
        self._in_title = False
        self.description = None
        self.h1 = None
        self._in_h1 = False
        self._h1buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in self.VOID:
            if tag == "meta" and a.get("name", "").lower() == "description" and self.description is None:
                self.description = " ".join(a.get("content", "").split())
            return
        if tag == "title" and self.title is None:
            self._in_title, self.title = True, ""
        if tag in self.SKIP or a.get("role", "").lower() in ("navigation", "banner", "contentinfo") or "hidden" in a:
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return
        if tag == "main":
            self.main_depth += 1
            self.has_main = True
        if tag == "h1" and self.h1 is None:
            self._in_h1, self._h1buf = True, []
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "section", "article", "br", "tr", "dd", "dt"):
            self._push(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self.skip_stack:
            if tag == self.skip_stack[-1]:
                self.skip_stack.pop()
            elif tag in self.skip_stack:
                while self.skip_stack and self.skip_stack.pop() != tag:
                    pass
            return
        if tag == "main" and self.main_depth:
            self.main_depth -= 1
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.h1 = " ".join("".join(self._h1buf).split())
        self._push(" ")

    def _push(self, s):
        self.all_text.append(s)
        if self.main_depth:
            self.main_text.append(s)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip_stack:
            return
        if self._in_h1:
            self._h1buf.append(data)
        self._push(data)


def extract(html: str) -> dict:
    p = MainTextParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001
        pass
    text = "".join(p.main_text if p.has_main else p.all_text)
    return {"text": " ".join(text.split()), "title": " ".join((p.title or "").split()),
            "description": p.description or "", "h1": p.h1 or "", "has_main": p.has_main}


def words_of(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def shingles(words: list[str], k: int = SHINGLE) -> list[int]:
    """Shingle hash per start position (crc32 of the k-word window)."""
    return [zlib.crc32(" ".join(words[i:i + k]).encode("utf-8")) for i in range(max(0, len(words) - k + 1))]


def coverage(words: list[str], sh: list[int], keep) -> int:
    """Number of word positions covered by at least one shingle for which keep(hash) is True."""
    covered = bytearray(len(words))
    for i, h in enumerate(sh):
        if keep(h):
            covered[i:i + SHINGLE] = b"\x01" * len(covered[i:i + SHINGLE])
    return sum(covered)


def title_template(titles: list[str]) -> dict | None:
    """Find a (prefix, suffix) frame shared by many titles where only a 1-3 token span varies."""
    n = len(titles)
    if n < 3:
        return None
    keys = defaultdict(set)
    for idx, t in enumerate(titles):
        toks = t.lower().split()
        seen = set()
        for span in (1, 2, 3):
            for i in range(0, len(toks) - span + 1):
                key = (tuple(toks[:i]), tuple(toks[i + span:]))
                if len(key[0]) + len(key[1]) >= 2 and key not in seen:
                    seen.add(key)
                    keys[key].add(idx)
    need = max(3, math.ceil(0.5 * n))
    best = None
    for key, idxs in keys.items():
        if len(idxs) >= need:
            fixed = len(key[0]) + len(key[1])
            if best is None or (len(idxs), fixed) > (len(best[1]), len(best[0][0]) + len(best[0][1])):
                best = (key, idxs)
    if not best:
        return None
    (pre, suf), idxs = best
    return {"template": " ".join(pre + ("{var}",) + suf), "pages": len(idxs), "share": round(len(idxs) / n, 2)}


def analyze_set(pages: list[dict], declared_size: int | None = None, sample_seed: int = 7) -> dict:
    """pages: [{"url", "html"}] (or already-extracted {"url", "text", "title", "description"})."""
    recs = []
    for pg in pages:
        ex = extract(pg["html"]) if "html" in pg else {"text": pg.get("text", ""), "title": pg.get("title", ""),
                                                        "description": pg.get("description", ""), "h1": pg.get("h1", ""), "has_main": None}
        w = words_of(ex["text"])
        sh = shingles(w)
        recs.append({"url": pg["url"], **ex, "words": w, "sh": sh, "shset": set(sh)})
    n = len(recs)
    df = Counter()
    for r in recs:
        df.update(r["shset"])
    boiler_min = max(2, math.ceil(0.5 * n))
    set_findings: list[dict] = []
    rows = []
    for r in recs:
        f: list[dict] = []
        wc = len(r["words"])
        if wc == 0:
            uniq = boil = 0.0
        elif wc < SHINGLE:
            uniq, boil = 1.0, 0.0
        else:
            uniq = 1 - coverage(r["words"], r["sh"], lambda h: df[h] >= 2) / wc
            boil = coverage(r["words"], r["sh"], lambda h: df[h] >= boiler_min) / wc if n >= 2 else 0.0
        unique_words = round(uniq * wc)
        if n >= 2:
            if uniq < 0.30:
                add(f, "high", "unique_below_30", f"Only {uniq:.0%} unique content; do not publish (or noindex) until it has page-specific substance")
            elif uniq < 0.40:
                add(f, "medium", "unique_below_40", f"{uniq:.0%} unique content (target >= 40%)")
            if boil > 0.60:
                add(f, "medium", "boilerplate_over_60", f"{boil:.0%} of the text is boilerplate shared across the set (> 60% = scaled-content risk)")
        if wc < 300:
            add(f, "low", "thin_page", f"{wc} words of main content (< 300; review for usefulness)")
        t, d = r["title"], r["description"]
        if t and d:
            core = re.split(r"\s[|\-–—]\s", t)[0].strip().lower()
            if core and len(core) > 8 and d.lower().startswith(core):
                add(f, "low", "description_repeats_title", "Meta description opens by repeating the title (templated metadata)")
        r["cta"] = ca.STOCK_CTA.search(d).group(0).lower().strip(" .!") if d and ca.STOCK_CTA.search(d) else None
        rows.append({"url": r["url"], "title": t, "words": wc, "unique_words": unique_words,
                     "unique_ratio": round(uniq, 3), "boilerplate_ratio": round(boil, 3), "issues": f})

    # ---- near duplicates
    pairs = []
    for i in range(n):
        a = recs[i]["shset"]
        if not a:
            continue
        for j in range(i + 1, n):
            b = recs[j]["shset"]
            if not b:
                continue
            inter = len(a & b)
            if not inter:
                continue
            jac = inter / len(a | b)
            if jac >= NEAR_DUP:
                pairs.append({"a": recs[i]["url"], "b": recs[j]["url"], "jaccard": round(jac, 3)})
    pairs.sort(key=lambda x: -x["jaccard"])
    if pairs:
        add(set_findings, "high", "near_duplicates", f"{len(pairs)} near-duplicate page pair(s) (Jaccard >= {NEAR_DUP}); "
            "merge, canonicalise or add real per-page substance", pairs=pairs[:25])
    dup_urls = {p["a"] for p in pairs} | {p["b"] for p in pairs}
    for row in rows:
        if row["url"] in dup_urls:
            add(row["issues"], "high", "near_duplicate", "Near-duplicate of another page in the set")

    # ---- templated metadata (set level)
    ctas = Counter(r["cta"] for r in recs if r.get("cta"))
    for cta, k in ctas.items():
        if k >= 3 and k >= 0.3 * n:
            add(set_findings, "medium", "shared_stock_cta", f"{k}/{n} meta descriptions end with the same stock CTA '{cta}'")
    tails = Counter()
    for r in recs:
        sents = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", r["description"]) if s.strip()]
        if len(sents) >= 2:
            tails[sents[-1]] += 1
    for tail, k in tails.items():
        if k >= 3 and k >= 0.3 * n and not any(x["code"] == "shared_stock_cta" for x in set_findings):
            add(set_findings, "medium", "shared_description_sentence", f"{k}/{n} meta descriptions end with the same sentence '{tail[:80]}'")
    rep = sum(1 for row in rows if any(i["code"] == "description_repeats_title" for i in row["issues"]))
    if rep >= 3 and rep >= 0.3 * n:
        add(set_findings, "medium", "templated_descriptions", f"{rep}/{n} descriptions open by repeating the title")
    tt = title_template([r["title"] for r in recs if r["title"]])
    if tt:
        add(set_findings, "medium", "templated_titles", f"{tt['pages']}/{n} titles follow one frame and differ only by a "
            f"variable token: '{tt['template']}'. Fine for navigation pages, but the body must differ substantively", template=tt)
    dup_titles = [t for t, k in Counter(r["title"].lower() for r in recs if r["title"]).items() if k > 1]
    if dup_titles:
        add(set_findings, "medium", "duplicate_titles", f"{len(dup_titles)} title(s) repeated across pages", samples=dup_titles[:5])

    # ---- set size gates
    size = declared_size if declared_size is not None else n
    if size >= 500:
        add(set_findings, "high", "scale_justify", f"{size} pages in the set (>= 500): stop and justify the scale; publish in "
            "batches of 50-100 and watch indexing 2-4 weeks between batches")
    elif size >= 100:
        add(set_findings, "info", "scale_review", f"{size} pages in the set (>= 100): require human review of a 5-10% sample before publishing")

    # ---- summary
    def avg(key):
        vals = [row[key] for row in rows if row["words"]]
        return round(sum(vals) / len(vals), 3) if vals else None
    gate = Counter()
    for row in rows:
        codes = {i["code"] for i in row["issues"]}
        gate["fail_unique_30"] += "unique_below_30" in codes
        gate["warn_unique_40"] += "unique_below_40" in codes
        gate["boilerplate_60"] += "boilerplate_over_60" in codes
        gate["under_300_words"] += "thin_page" in codes
    k = 0 if n == 0 else min(n, max(min(n, 3), math.ceil(n * 0.075)))
    rnd = random.Random(sample_seed)
    sample = sorted(rnd.sample([r["url"] for r in rows], k)) if k else []
    counts = Counter(i["severity"] for i in set_findings)
    for row in rows:
        counts.update(i["severity"] for i in row["issues"])
    return {"pages": rows, "set_findings": set_findings, "near_duplicate_pairs": pairs,
            "review_sample": sample,
            "summary": {"pages_analyzed": n, "set_size": size, "avg_unique_ratio": avg("unique_ratio"),
                        "avg_boilerplate_ratio": avg("boilerplate_ratio"), "avg_words": avg("words"),
                        "gates": dict(gate), "counts": dict(counts)}}


# --------------------------------------------------------------------------- inputs
def urls_from_crawl(path: str, pattern: str | None) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for pg in data.get("pages", []):
        u = pg.get("final_url") or pg.get("url")
        if u and pg.get("status") == 200 and (not pattern or re.search(pattern, u)):
            out.append(u)
    return list(dict.fromkeys(out))


def urls_from_sitemap(sm: str, pattern: str | None, ua: str) -> list[str]:
    ca.set_network_mode(urlparse(sm).hostname)
    urls, _ = ca.read_sitemap_urls(sm, ua, limit=50000)
    return list(dict.fromkeys(u for u in urls if not pattern or re.search(pattern, u)))


def fetch_pages(urls: list[str], ua: str, delay: float, timeout: float, allow_private: bool) -> tuple[list[dict], list[dict]]:
    pages, skipped = [], []
    if urls:
        ca.set_network_mode(urlparse(urls[0]).hostname, allow_private)
    for i, u in enumerate(urls):
        res = ca.fetch(u, ua, timeout=timeout)
        if res["status"] != 200 or "html" not in res["headers"].get("content-type", ""):
            skipped.append({"url": u, "status": res["status"], "error": res.get("error")})
        else:
            pages.append({"url": res["final_url"], "html": res["body"]})
        if delay and i < len(urls) - 1:
            time.sleep(delay)
    return pages, skipped


def to_markdown(r: dict) -> str:
    s = r["summary"]
    L = [f"# Programmatic page quality: {r.get('label') or ''}", "",
         f"- Checked: {r['checked_at']} | pages analysed: {s['pages_analyzed']} (set size {s['set_size']}) | skipped: {len(r.get('skipped', []))}",
         f"- Avg unique: {_pct(s['avg_unique_ratio'])} | avg boilerplate: {_pct(s['avg_boilerplate_ratio'])} | avg words: {s['avg_words']}",
         f"- Gates: unique < 30%: {s['gates'].get('fail_unique_30', 0)} | 30-40%: {s['gates'].get('warn_unique_40', 0)} | "
         f"boilerplate > 60%: {s['gates'].get('boilerplate_60', 0)} | < 300 words: {s['gates'].get('under_300_words', 0)}",
         "- Gate source: unique >= 40% target, < 30% do not publish; > 60% shared boilerplate = scaled-content risk.", "",
         "## Set findings", ""]
    if not r["set_findings"]:
        L.append("- none")
    for x in r["set_findings"]:
        L.append(f"- **{x['severity']}** `{x['code']}`: {x['message']}")
        for p in x.get("pairs", [])[:10]:
            L.append(f"  - {p['jaccard']}: {p['a']} <-> {p['b']}")
    L += ["", "## Pages", "", "| URL | Words | Unique | Boilerplate | Worst | Issues |", "|---|---|---|---|---|---|"]
    for row in sorted(r["pages"], key=lambda x: x["unique_ratio"]):
        worst = min((i["severity"] for i in row["issues"]), key=lambda v: SEVERITY_ORDER[v], default="-")
        codes = ", ".join(sorted({i["code"] for i in row["issues"]}))
        L.append(f"| {row['url']} | {row['words']} | {_pct(row['unique_ratio'])} | {_pct(row['boilerplate_ratio'])} | {worst} | {codes} |")
    L += ["", f"## Human review sample ({len(r['review_sample'])} pages, ~5-10%)", ""]
    L += [f"- [ ] {u}" for u in r["review_sample"]]
    if r.get("skipped"):
        L += ["", "## Skipped (not a 200 HTML page)", ""]
        L += [f"- {x['url']}: {x['status'] or x['error']}" for x in r["skipped"][:50]]
    return "\n".join(L) + "\n"


def _pct(v):
    return "-" if v is None else f"{v:.0%}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--urls", help="file with one URL per line")
    src.add_argument("--from-crawl", help="crawl_audit JSON output; use with --pattern")
    src.add_argument("--sitemap", help="sitemap (or sitemap index) URL; use with --pattern")
    ap.add_argument("--pattern", help="regex searched in each URL to select the page set (e.g. '/integrations/')")
    ap.add_argument("--max-pages", type=int, default=300, help="max pages fetched (default 300)")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=7, help="seed for the human-review sample")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--allow-private", action="store_true")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.urls:
        urls = [l.strip() for l in Path(a.urls).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        if a.pattern:
            urls = [u for u in urls if re.search(a.pattern, u)]
    elif a.from_crawl:
        urls = urls_from_crawl(a.from_crawl, a.pattern)
    else:
        urls = urls_from_sitemap(a.sitemap, a.pattern, a.ua)
    if not urls:
        print("No URLs matched.", file=sys.stderr)
        sys.exit(2)
    declared = len(urls)
    if len(urls) > a.max_pages:
        print(f"[info] {len(urls)} URLs matched; analysing a random {a.max_pages} (raise --max-pages for all)", file=sys.stderr)
        urls = sorted(random.Random(a.seed).sample(urls, a.max_pages))
    pages, skipped = fetch_pages(urls, a.ua, a.delay, a.timeout, a.allow_private)
    r = analyze_set(pages, declared, a.seed)
    host = urlparse(urls[0]).netloc
    r.update(tool="programmatic_check", version=1, label=f"{host} {a.pattern or ''}".strip(), skipped=skipped,
             checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    md = to_markdown(r)
    js = json.dumps(r, indent=2, ensure_ascii=False)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        h = host.replace(":", "_")
        (out / f"programmatic-{h}-{stamp}.json").write_text(js, encoding="utf-8")
        (out / f"programmatic-{h}-{stamp}.md").write_text(md, encoding="utf-8")
        print(f"[saved] {out / f'programmatic-{h}-{stamp}.md'}", file=sys.stderr)
    print(js if a.json else md)


if __name__ == "__main__":
    main()
