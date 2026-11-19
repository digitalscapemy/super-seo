#!/usr/bin/env python3
"""ai_visibility.py - measure brand visibility in AI answers WITHOUT paid APIs.

Answers come from (a) the user pasting real ChatGPT / Perplexity / Gemini / AI Mode /
Claude / Copilot answers, or (b) Claude-simulated answers grounded in WebSearch
(must be marked simulated). This script only scores them; it never calls an AI API.

Files (inside the project's .seo/ai-visibility/):
  tracker.json            brand, competitors, prompts (see templates/tracker.example.json)
  runs/<YYYY-MM-DD>/answers.md   one block per (engine, prompt):

      ### chatgpt | best invoicing app for freelancers
      <answer text, keep the links as markdown or bare URLs>

      ### perplexity | best invoicing app for freelancers
      ...
  Engine header may add flags:  ### claude-sim | <prompt> | simulated
  A block whose body is exactly NO_ANSWER means the engine showed no AI answer.

Usage:
  python ai_visibility.py score  .seo/ai-visibility/runs/2026-11-19
  python ai_visibility.py trend  .seo/ai-visibility        # latest run vs previous comparable run
  python ai_visibility.py checklist .seo/ai-visibility     # print the prompt x engine checklist to collect

Metric rules (adapted from open-seo, MIT):
  - Denominator = answered observations only; no-answer and failed are coverage, not absence.
  - Branded prompts (that name the brand) are excluded from rates.
  - Mention = brand name/alias/domain in the prose, URLs blanked first. Citation = a linked URL on the brand's domain/subdomain.
  - Position = rank of the brand's first mention among TRACKED brands mentioned in that answer.
  - Simulated answers are reported separately and never mixed into observed rates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

URL_RX = re.compile(r"https?://[^\s)\]>\"'<]+", re.I)
MD_LINK_RX = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
NOT_COMPETITORS = {"reddit.com", "youtube.com", "wikipedia.org", "quora.com", "medium.com", "linkedin.com", "x.com",
                   "twitter.com", "facebook.com", "instagram.com", "tiktok.com", "github.com", "stackoverflow.com",
                   "producthunt.com", "g2.com", "capterra.com", "trustpilot.com", "getapp.com", "softwareadvice.com",
                   "forbes.com", "techradar.com", "zapier.com", "pcmag.com", "google.com", "apple.com"}
SOURCE_TYPES = {
    "forum": {"reddit.com", "quora.com", "news.ycombinator.com", "stackoverflow.com", "indiehackers.com", "lowyat.net"},
    "review": {"g2.com", "capterra.com", "trustpilot.com", "getapp.com", "softwareadvice.com", "producthunt.com", "trustradius.com"},
    "video": {"youtube.com", "tiktok.com", "vimeo.com"},
    "reference": {"wikipedia.org", "github.com", "medium.com", "dev.to"},
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return " ".join(s.split()).lower()


def reg_domain(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    # naive registrable domain; handles co.uk / com.my style second-level suffixes
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "gov", "edu", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def name_patterns(brand: dict) -> list[re.Pattern]:
    names = [brand.get("name", "")] + list(brand.get("aliases", []))
    dom = (brand.get("domain") or "").lower().removeprefix("www.")
    if dom:
        names.append(dom)
        stem = dom.split(".")[0]
        if brand.get("name") and re.sub(r"\W", "", brand["name"].lower()) == stem:
            names.append(stem)
    pats = []
    for n in {x.strip() for x in names if x and len(x.strip()) >= 2}:
        esc = r"\s+".join(re.escape(p) for p in n.split())
        pats.append(re.compile(rf"(?<![\w]){esc}(?![\w])", re.I))
    return pats


def first_mention(text: str, pats: list[re.Pattern]) -> int | None:
    best = None
    for p in pats:
        m = p.search(text)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def cited(urls: list[str], domain: str) -> bool:
    d = (domain or "").lower().removeprefix("www.")
    if not d:
        return False
    for u in urls:
        h = (urlparse(u).hostname or "").lower().removeprefix("www.")
        if h == d or h.endswith("." + d):
            return True
    return False


def parse_answers(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^###\s+", text)
    out = []
    for b in blocks[1:]:
        header, _, body = b.partition("\n")
        parts = [p.strip() for p in header.split("|")]
        if len(parts) < 2:
            continue
        engine, prompt = parts[0].lower(), parts[1]
        simulated = "simulated" in [p.lower() for p in parts[2:]] or engine.endswith("-sim")
        body = body.strip()
        status = "no_answer" if body.upper() == "NO_ANSWER" else ("failed" if body.upper() == "FAILED" else "answered")
        urls = [m.group(2) for m in MD_LINK_RX.finditer(body)] + URL_RX.findall(MD_LINK_RX.sub(" ", body))
        urls = [u.rstrip(".,;:") for u in urls if "google.com/url" not in u]
        prose = MD_LINK_RX.sub(lambda m: m.group(1), body)
        prose = EMAIL_RX.sub(" ", URL_RX.sub(" ", prose))
        out.append({"engine": engine, "prompt": prompt, "simulated": simulated, "status": status,
                    "prose": prose, "urls": urls})
    return out


def is_branded(prompt: str, brand: dict) -> bool:
    return first_mention(prompt, name_patterns(brand)) is not None


def score_run(run_dir: Path, tracker: dict) -> dict:
    answers_file = run_dir / "answers.md"
    obs = parse_answers(answers_file)
    brand = tracker["brand"]
    comps = tracker.get("competitors", [])
    tracked = [{"key": "__own__", **brand}] + [{"key": c.get("domain") or c["name"], **c} for c in comps]
    pats = {t["key"]: name_patterns(t) for t in tracked}
    topic_of = {norm(p["text"]): p.get("topic", "") for p in tracker.get("prompts", [])}

    def blank():
        return {"answered": 0, "no_answer": 0, "failed": 0, "branded_excluded": 0, "brands": defaultdict(lambda: {
            "mentions": 0, "citations": 0, "positions": [], "top1": 0})}

    groups = {"observed": blank(), "simulated": blank()}
    per_engine: dict[str, dict] = defaultdict(blank)
    gaps, sources = [], defaultdict(lambda: {"answers": 0, "prompts": set(), "engines": set()})
    prompt_rows = defaultdict(lambda: {"answered": 0, "own": 0, "engines_comp_not_you": set()})

    for o in obs:
        g = groups["simulated" if o["simulated"] else "observed"]
        if is_branded(o["prompt"], brand):
            g["branded_excluded"] += 1
            continue
        if o["status"] != "answered":
            g[o["status"]] += 1
            continue
        eng = per_engine[o["engine"] + (" (sim)" if o["simulated"] else "")]
        for bucket in (g, eng):
            bucket["answered"] += 1
        hits = {}
        for t in tracked:
            fm = first_mention(o["prose"], pats[t["key"]])
            c = cited(o["urls"], t.get("domain", ""))
            hits[t["key"]] = (fm, c)
        mentioned_order = sorted([k for k, (fm, _) in hits.items() if fm is not None], key=lambda k: hits[k][0])
        for k, (fm, c) in hits.items():
            for bucket in (g, eng):
                b = bucket["brands"][k]
                b["mentions"] += fm is not None
                b["citations"] += c
                if fm is not None:
                    pos = mentioned_order.index(k) + 1
                    b["positions"].append(pos)
                    b["top1"] += pos == 1
        own_fm, own_c = hits["__own__"]
        pr = prompt_rows[o["prompt"]]
        if not o["simulated"]:
            pr["answered"] += 1
            pr["own"] += (own_fm is not None or own_c)
        comp_present = [k for k in hits if k != "__own__" and (hits[k][0] is not None or hits[k][1])]
        if own_fm is None and not own_c and comp_present:
            gaps.append({"engine": o["engine"], "prompt": o["prompt"], "simulated": o["simulated"], "competitors": comp_present})
            pr["engines_comp_not_you"].add(o["engine"])
        if not o["simulated"]:
            for u in set(o["urls"]):
                d = reg_domain(urlparse(u).hostname or "")
                s = sources[d]
                s["answers"] += 1
                s["prompts"].add(o["prompt"])
                s["engines"].add(o["engine"])

    def finalize(g):
        n = g["answered"]
        rows = {}
        total_m = sum(b["mentions"] for b in g["brands"].values()) or 1
        total_c = sum(b["citations"] for b in g["brands"].values()) or 1
        for k, b in g["brands"].items():
            rows[k] = {"mentions": b["mentions"], "citations": b["citations"],
                       "mention_rate": round(100 * b["mentions"] / n, 1) if n else None,
                       "citation_rate": round(100 * b["citations"] / n, 1) if n else None,
                       "avg_position": round(sum(b["positions"]) / len(b["positions"]), 2) if b["positions"] else None,
                       "top1_rate": round(100 * b["top1"] / n, 1) if n else None,
                       "mention_sov": round(100 * b["mentions"] / total_m, 1),
                       "citation_sov": round(100 * b["citations"] / total_c, 1)}
        return {"answered": n, "no_answer": g["no_answer"], "failed": g["failed"],
                "branded_excluded": g["branded_excluded"], "brands": rows}

    own_domains = {reg_domain(t.get("domain", "")) for t in tracked if t.get("domain")}
    src_rows = []
    for d, s in sorted(sources.items(), key=lambda kv: -kv[1]["answers"]):
        own = "own" if d == reg_domain(brand.get("domain", "")) else ("competitor" if d in own_domains else "other")
        typ = next((t for t, ds in SOURCE_TYPES.items() if d in ds), "competitor" if own == "competitor" else "owned" if own == "own" else "article/other")
        src_rows.append({"domain": d, "answers": s["answers"], "prompts": len(s["prompts"]),
                         "engines": sorted(s["engines"]), "ownership": own, "type": typ})
    suggested = [r["domain"] for r in src_rows if r["ownership"] == "other" and r["domain"] not in NOT_COMPETITORS][:5]

    cells = {}
    for o in obs:
        if o["simulated"] or o["status"] != "answered" or is_branded(o["prompt"], brand):
            continue
        key = f"{norm(o['prompt'])}|{o['engine']}|{tracker.get('market', '')}|{norm(brand.get('name', ''))}|{brand.get('domain', '')}"
        fm = first_mention(o["prose"], pats["__own__"])
        cells[key] = {"mentioned": fm is not None, "cited": cited(o["urls"], brand.get("domain", ""))}

    return {
        "run": run_dir.name, "scored_at": datetime.now().isoformat(timespec="seconds"),
        "brand": brand.get("name"), "market": tracker.get("market"),
        "observed": finalize(groups["observed"]), "simulated": finalize(groups["simulated"]),
        "per_engine": {e: finalize(g) for e, g in per_engine.items()},
        "gaps": gaps, "sources": src_rows[:40], "suggested_competitors": suggested,
        "prompts": [{"prompt": p, "topic": topic_of.get(norm(p), ""), "answered": r["answered"],
                     "own_rate": round(100 * r["own"] / r["answered"], 1) if r["answered"] else None,
                     "engines_competitor_not_you": sorted(r["engines_comp_not_you"])} for p, r in prompt_rows.items()],
        "cells": cells,
    }


def label(k: str, tracker: dict) -> str:
    if k == "__own__":
        return f"**{tracker['brand']['name']}** (you)"
    for c in tracker.get("competitors", []):
        if (c.get("domain") or c["name"]) == k:
            return c["name"]
    return k


def render(res: dict, tracker: dict) -> str:
    o = res["observed"]
    L = [f"# AI visibility: {res['brand']} — run {res['run']}", "",
         f"- Market: {res.get('market') or 'unspecified'} | observed answers: {o['answered']} | no AI answer: {o['no_answer']} | "
         f"failed: {o['failed']} | branded prompts excluded: {o['branded_excluded']}"]
    if o["answered"] < 10:
        L.append(f"- Small sample ({o['answered']} answered). Treat rates as directional, not a measurement.")
    L += ["", "## Brands (observed answers only)", "",
          "| Brand | Mentioned | Mention rate | Cited | Citation rate | Avg position | #1 rate | Mention SoV |", "|---|---|---|---|---|---|---|---|"]
    for k, b in sorted(o["brands"].items(), key=lambda kv: -kv[1]["mentions"]):
        L.append(f"| {label(k, tracker)} | {b['mentions']}/{o['answered']} | {b['mention_rate']}% | {b['citations']}/{o['answered']} | "
                 f"{b['citation_rate']}% | {b['avg_position'] or '-'} | {b['top1_rate']}% | {b['mention_sov']}% |")
    if res["per_engine"]:
        L += ["", "## By engine (you)", "", "| Engine | Answered | Mention rate | Citation rate | Avg position |", "|---|---|---|---|---|"]
        for e, g in sorted(res["per_engine"].items()):
            b = g["brands"].get("__own__", {})
            L.append(f"| {e} | {g['answered']} | {b.get('mention_rate')}% | {b.get('citation_rate')}% | {b.get('avg_position') or '-'} |")
    obs_gaps = [g for g in res["gaps"] if not g["simulated"]]
    if obs_gaps:
        L += ["", "## Gaps: a competitor appears, you do not", "", "| Prompt | Engines | Competitors named |", "|---|---|---|"]
        byp = defaultdict(lambda: {"engines": set(), "comps": set()})
        for g in obs_gaps:
            byp[g["prompt"]]["engines"].add(g["engine"])
            byp[g["prompt"]]["comps"].update(label(c, tracker) for c in g["competitors"])
        for p, v in sorted(byp.items(), key=lambda kv: -len(kv[1]["engines"])):
            L.append(f"| {p} | {', '.join(sorted(v['engines']))} | {', '.join(sorted(v['comps']))} |")
    if res["sources"]:
        L += ["", "## What AI cites in this market", "", "| Domain | Type | Answers citing | Prompts | Engines | Ownership |", "|---|---|---|---|---|---|"]
        for s in res["sources"][:20]:
            L.append(f"| {s['domain']} | {s['type']} | {s['answers']} | {s['prompts']} | {', '.join(s['engines'])} | {s['ownership']} |")
    if res["suggested_competitors"]:
        L += ["", f"Frequently cited, not tracked (possible competitors, verify by reading their site): {', '.join(res['suggested_competitors'])}"]
    s = res["simulated"]
    if s["answered"]:
        b = s["brands"].get("__own__", {})
        L += ["", "## Simulated answers (WebSearch-grounded, NOT observed)", "",
              f"- {s['answered']} simulated answers: you mentioned in {b.get('mention_rate')}%, cited in {b.get('citation_rate')}%. "
              "Use only to find likely gaps; never report as real AI visibility."]
    return "\n".join(L) + "\n"


def trend(base: Path, tracker: dict) -> str:
    runs = sorted(d for d in (base / "runs").iterdir() if d.is_dir() and (d / "answers.md").exists())
    if len(runs) < 2:
        return "Need at least two runs for a trend.\n"
    cur, prev = score_run(runs[-1], tracker), score_run(runs[-2], tracker)
    matched = set(cur["cells"]) & set(prev["cells"])
    if not cur["cells"]:
        status = "no_data"
    elif not matched:
        status = "scope_changed"
    else:
        status = "comparable"
    L = [f"# AI visibility trend: {runs[-2].name} -> {runs[-1].name}", "", f"- Status: **{status}** | matched cells: {len(matched)} "
         f"(cell = prompt x engine x market x brand; changing any starts a new cell)"]
    if status == "comparable":
        def rate(r, f):
            return 100 * sum(r["cells"][k][f] for k in matched) / len(matched)
        for f in ("mentioned", "cited"):
            a, b = rate(prev, f), rate(cur, f)
            L.append(f"- {f.title()} rate on matched cells: {a:.1f}% -> {b:.1f}% ({b - a:+.1f} pts)")
        lost = [k.split("|")[:2] for k in matched if prev["cells"][k]["mentioned"] and not cur["cells"][k]["mentioned"]]
        won = [k.split("|")[:2] for k in matched if not prev["cells"][k]["mentioned"] and cur["cells"][k]["mentioned"]]
        if won:
            L += ["", "Won:"] + [f"- {e}: {p}" for p, e in won]
        if lost:
            L += ["", "Lost:"] + [f"- {e}: {p}" for p, e in lost]
        if len(matched) < 10:
            L.append("\n- Fewer than 10 matched cells: changes of one answer move the rate a lot; directional only.")
    return "\n".join(L) + "\n"


def checklist(base: Path, tracker: dict) -> str:
    engines = tracker.get("engines") or ["chatgpt", "perplexity", "google-ai-mode", "gemini", "claude", "copilot"]
    today = datetime.now().strftime("%Y-%m-%d")
    L = [f"# Collect answers for run {today}", "",
         f"Save to `{(base / 'runs' / today / 'answers.md').as_posix()}`. Use a logged-out / incognito window, "
         f"market: {tracker.get('market') or 'set market in tracker.json'}. Paste the full answer with its source links.", ""]
    for p in tracker.get("prompts", []):
        for e in engines:
            L.append(f"### {e} | {p['text']}\n<paste answer here, or NO_ANSWER>\n")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["score", "trend", "checklist"])
    ap.add_argument("path", help="run dir for score; .seo/ai-visibility for trend/checklist")
    ap.add_argument("--tracker", help="tracker.json (default: <ai-visibility>/tracker.json)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = Path(a.path)
    base = p.parent.parent if a.cmd == "score" else p
    tracker = json.loads(Path(a.tracker or base / "tracker.json").read_text(encoding="utf-8"))
    if a.cmd == "score":
        res = score_run(p, tracker)
        (p / "score.json").write_text(json.dumps(res, indent=2, default=list), encoding="utf-8")
        md = render(res, tracker)
        (p / "score.md").write_text(md, encoding="utf-8")
        print(json.dumps(res, indent=2, default=list) if a.json else md)
    elif a.cmd == "trend":
        print(trend(p, tracker))
    else:
        out = checklist(p, tracker)
        print(out)


if __name__ == "__main__":
    main()
