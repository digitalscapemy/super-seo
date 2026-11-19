#!/usr/bin/env python3
"""content_check.py - section-level citability / quality signals for one page.

Input: a URL, or a local .md / .mdx / .html file (blog posts, docs, landing copy in the repo).
Output: per-section facts Claude uses to judge and rewrite. These are HEURISTICS
(third-party GEO research + Google helpful-content guidance), not a predicted citation rate.

  python content_check.py content/blog/e-invoice-guide.mdx
  python content_check.py https://example.com/pricing --json

Signals per section (H2/H3 block):
  answer_first     first sentence <= 30 words, no leading pronoun/conjunction, contains a verb-ish definition or number
  words            section length (self-contained answer blocks commonly 40-170 words; flag < 40 or > 300)
  question_heading heading phrased as a question
  stats / sourced  numbers with % / currency / units, and whether a source/link sits within ~200 chars
  structure        list / table present
  leading_pronoun  section opens with It/This/They/However/But... (not self-contained when quoted alone)
  filler / ai_phrases  stock phrases that add no information
Page-level: dates visible, author byline hint, first-30%-of-page answer coverage, reading level.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_audit import fetch, UA_DEFAULT  # noqa: E402
from html.parser import HTMLParser  # noqa: E402

FILLER = ["it's important to note", "it is important to note", "in today's fast-paced", "in today's digital", "when it comes to",
          "let's dive in", "without further ado", "at the end of the day", "needless to say", "it goes without saying",
          "in this article, we will", "in this blog post", "as we all know", "the fact of the matter"]
AI_PHRASES = ["delve into", "ever-evolving", "ever-changing landscape", "tapestry", "embark on", "a testament to",
              "harness the power", "unlock the potential", "unleash", "game-changer", "game changer", "cutting-edge",
              "seamlessly", "elevate your", "navigate the complexities", "in the realm of", "robust solution",
              "revolutionize", "supercharge", "look no further", "whether you're a"]
LEAD_WEAK = re.compile(r"^(it|this|that|these|those|they|them|he|she|however|but|and|also|so|additionally|moreover|furthermore)\b", re.I)
STAT = re.compile(r"(\b\d[\d,.]*\s?%|(?:RM|USD|\$|€|£|S\$)\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:ms|s|seconds|minutes|hours|days|x|times|users|customers|companies|teams|requests|GB|MB|k|K|million|billion)\b)")
SOURCE = re.compile(r"(https?://|\]\(|according to|source:|per [A-Z]|\[\d+\]|\[\^\d+\]|data from|survey|study by|report by)", re.I)
DEFINITION = re.compile(r"\b(is|are|means|refers to|lets you|helps|allows|costs|takes|supports)\b", re.I)
DATE = re.compile(r"\b(updated|last updated|published|reviewed)\b[^\n]{0,40}\b(20\d\d|\d{1,2}\s\w+\s20\d\d|\w+\s\d{1,2},?\s20\d\d)", re.I)


class Blocks(HTMLParser):
    """HTML -> list of (level, heading, [ (kind, text) ]) without double counting nested text."""
    SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "form"}
    BLOCK = {"p", "li", "td", "th", "blockquote", "pre", "dd", "dt", "figcaption", "div", "section", "article", "aside", "header", "main"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = [{"level": 1, "heading": "(intro)", "parts": [], "lists": 0, "tables": 0}]
        self.skip = 0
        self.h = None
        self.hbuf = []
        self.buf = []
        self.depth_block = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.flush()
            self.h, self.hbuf = tag, []
        elif tag in ("ul", "ol"):
            self.sections[-1]["lists"] += 1
        elif tag == "table":
            self.sections[-1]["tables"] += 1
        elif tag in self.BLOCK:
            self.flush()
        elif tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if self.h and tag == self.h:
            text = " ".join("".join(self.hbuf).split())
            self.sections.append({"level": int(tag[1]), "heading": text, "parts": [], "lists": 0, "tables": 0})
            self.h = None
        elif tag in self.BLOCK:
            self.flush()

    def handle_data(self, d):
        if self.skip:
            return
        (self.hbuf if self.h else self.buf).append(d)

    def flush(self):
        t = " ".join("".join(self.buf).split())
        if t:
            self.sections[-1]["parts"].append(t)
        self.buf = []


def md_to_sections(text: str) -> list[dict]:
    text = re.sub(r"(?s)^---.*?---\s*", "", text)               # frontmatter
    text = re.sub(r"(?m)^(import|export) .*$", "", text)         # mdx imports/exports
    text = re.sub(r"(?s)```.*?```", " ", text)                   # code blocks
    secs = [{"level": 1, "heading": "(intro)", "parts": [], "lists": 0, "tables": 0}]
    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            secs.append({"level": len(m.group(1)), "heading": m.group(2).strip(), "parts": [], "lists": 0, "tables": 0})
            continue
        s = line.strip()
        if not s:
            continue
        if re.match(r"^([-*+]|\d+\.)\s", s):
            secs[-1]["lists"] += 1
        if s.startswith("|"):
            secs[-1]["tables"] += 1
        s = re.sub(r"<[^>]+>", " ", s)
        secs[-1]["parts"].append(re.sub(r"^([-*+]|\d+\.)\s+", "", s))
    for s in secs:
        s["lists"] = 1 if s["lists"] else 0
        s["tables"] = 1 if s["tables"] >= 2 else 0
    return secs


def sentences(t: str) -> list[str]:
    t = re.sub(r"\b(e\.g|i\.e|etc|vs|approx|Mr|Ms|Dr|Inc|Sdn|Bhd)\.", lambda m: m.group(0).replace(".", "§"), t)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", t)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]


def syllables(w: str) -> int:
    w = w.lower()
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups) - (1 if w.endswith("e") and len(groups) > 1 else 0)
    return max(1, n)


def analyze(sections: list[dict], raw_text: str) -> dict:
    rows = []
    all_text = " ".join(" ".join(s["parts"]) for s in sections)
    total_words = len(all_text.split()) or 1
    seen_words = 0
    for s in sections:
        body = " ".join(s["parts"])
        words = len(body.split())
        if words == 0 and s["heading"] == "(intro)":
            continue
        sents = sentences(body)
        first = sents[0] if sents else ""
        stats = STAT.findall(body)
        sourced = 0
        for m in STAT.finditer(body):
            window = body[max(0, m.start() - 200): m.end() + 200]
            sourced += bool(SOURCE.search(window))
        low = body.lower()
        row = {
            "heading": s["heading"], "level": s["level"], "words": words,
            "position_pct": round(100 * seen_words / total_words),
            "question_heading": s["heading"].strip().endswith("?"),
            "answer_first": bool(first) and len(first.split()) <= 30 and not LEAD_WEAK.match(first) and bool(DEFINITION.search(first) or STAT.search(first)),
            "first_sentence": first[:220],
            "leading_pronoun": bool(LEAD_WEAK.match(first)) if first else False,
            "stats": len(stats), "stats_sourced": sourced,
            "has_list": bool(s["lists"]), "has_table": bool(s["tables"]),
            "filler": [f for f in FILLER if f in low], "ai_phrases": [f for f in AI_PHRASES if f in low],
        }
        flags = []
        if s["heading"] != "(intro)" and words and words < 40:
            flags.append("too short to stand alone")
        if words > 300:
            flags.append("long; split with subheadings or add a summary sentence up top")
        if not row["answer_first"] and words >= 40:
            flags.append("does not open with a direct answer")
        if row["leading_pronoun"]:
            flags.append("opens with a pronoun/conjunction; not self-contained when quoted")
        if row["stats"] and row["stats_sourced"] < row["stats"]:
            flags.append(f"{row['stats'] - row['stats_sourced']} numbers without a nearby source")
        if row["filler"] or row["ai_phrases"]:
            flags.append("stock phrasing")
        if re.search(r"\b(vs\.?|versus|compare|comparison|alternatives?)\b", s["heading"], re.I) and not row["has_table"]:
            flags.append("comparison without a table")
        row["flags"] = flags
        rows.append(row)
        seen_words += words
    w = all_text.split()
    # every block (p/li/td) ends a sentence even without punctuation (pricing tables, feature lists)
    sc = max(1, sum(len(sentences(part)) or 1 for s in sections for part in s["parts"]))
    flesch = (round(206.835 - 1.015 * (len(w) / sc) - 84.6 * (sum(syllables(x) for x in w) / max(1, len(w))), 1)
              if len(w) >= 150 else None)
    early = [r for r in rows if r["position_pct"] <= 30]
    page = {
        "words": len(w), "sections": len(rows), "flesch_reading_ease": flesch,
        "date_visible": bool(DATE.search(raw_text)),
        "byline_hint": bool(re.search(r"\b(written by|author|by [A-Z][a-z]+ [A-Z][a-z]+)\b", raw_text)),
        "question_headings": sum(r["question_heading"] for r in rows),
        "answer_first_sections": sum(r["answer_first"] for r in rows),
        "early_answer_first": any(r["answer_first"] for r in early),
        "stats_total": sum(r["stats"] for r in rows), "stats_unsourced": sum(r["stats"] - r["stats_sourced"] for r in rows),
        "tables": sum(r["has_table"] for r in rows), "lists": sum(r["has_list"] for r in rows),
        "filler_hits": sorted({f for r in rows for f in r["filler"]}), "ai_phrase_hits": sorted({f for r in rows for f in r["ai_phrases"]}),
    }
    return {"page": page, "sections": rows}


def render(src: str, res: dict) -> str:
    p = res["page"]
    L = [f"# Content check: {src}", "",
         f"- {p['words']} words, {p['sections']} sections, Flesch {p['flesch_reading_ease']} | question headings {p['question_headings']} | "
         f"answer-first sections {p['answer_first_sections']}/{p['sections']} | tables {p['tables']} | lists {p['lists']}",
         f"- Date visible: {'yes' if p['date_visible'] else 'NO'} | byline hint: {'yes' if p['byline_hint'] else 'no'} | "
         f"direct answer in first 30% of page: {'yes' if p['early_answer_first'] else 'NO'} | numbers without source: {p['stats_unsourced']}/{p['stats_total']}"]
    if p["filler_hits"] or p["ai_phrase_hits"]:
        L.append(f"- Stock phrasing: {', '.join(p['filler_hits'] + p['ai_phrase_hits'])}")
    L += ["", "| Section | Words | Answer-first | Stats (sourced) | Flags |", "|---|---|---|---|---|"]
    for r in res["sections"]:
        L.append(f"| {'#' * r['level']} {r['heading'][:60]} | {r['words']} | {'Y' if r['answer_first'] else '-'} | "
                 f"{r['stats']} ({r['stats_sourced']}) | {'; '.join(r['flags'])[:160]} |")
    L += ["", "> Heuristics, not a citation forecast. Google: write for people; AI features use the same ranking and quality systems as Search."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="URL or local .md/.mdx/.html file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    src = a.source
    if re.match(r"https?://", src):
        r = fetch(src, UA_DEFAULT)
        if r["status"] != 200:
            sys.exit(f"fetch failed: {r['status']} {r['error'] or ''}")
        raw = r["body"]
        b = Blocks(); b.feed(raw); b.flush(); secs = b.sections
    else:
        raw = Path(src).read_text(encoding="utf-8", errors="replace")
        if src.lower().endswith((".html", ".htm")):
            b = Blocks(); b.feed(raw); b.flush(); secs = b.sections
        else:
            secs = md_to_sections(raw)
    res = analyze(secs, re.sub(r"<[^>]+>", " ", raw))
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else render(src, res))


if __name__ == "__main__":
    main()
