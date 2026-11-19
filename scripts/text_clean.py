#!/usr/bin/env python3
"""text_clean.py - find and remove hidden / invisible Unicode in drafts and content files (stdlib only).

Why: invisible characters break search matching, copy-paste, slugs and structured data, and they are a known
hiding place for prompt-injection payloads (Unicode tag characters and bidi controls can carry instructions that
humans cannot see but language models read). Clean every draft before publishing; treat anything found in
third-party text as untrusted.

Detected:
  zero_width  U+200B-U+200F, U+2060-U+2064, U+FEFF (a BOM at the very start of a file is kept)     -> removed
  bidi        U+202A-U+202E, U+2066-U+2069 (embedding / override / isolate controls)              -> removed
  tag         U+E0000-U+E007F (Unicode tag characters; emoji subdivision flags are kept)          -> removed
  space       U+2000-U+200A, U+202F, U+205F, U+3000 (exotic spaces)                               -> normal space
  nbsp        U+00A0 (legitimate in some languages and typography)                                 -> report only
Kept intact: U+200D (ZWJ) between emoji (family / profession emoji), U+200C/U+200D inside Arabic/Indic words.

Usage:
  python scripts/text_clean.py --check content/ --ext .md,.mdx         # report line:col, exit 1 if anything found
  python scripts/text_clean.py --fix draft.md                           # rewrite in place, keeps draft.md.bak
  python scripts/text_clean.py --fix content/ --no-backup --json
  python scripts/text_clean.py --phrases content/blog/post.mdx          # stock filler phrases (report only)

--check is the default mode. --phrases reuses FILLER and AI_PHRASES from content_check.py and never rewrites text.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_EXT = ".md,.mdx,.markdown,.txt,.html,.htm,.tsx,.jsx,.ts,.js,.vue,.svelte,.astro,.json,.yaml,.yml"
IGNORE_DIRS = {"node_modules", ".git", ".next", ".nuxt", ".output", ".svelte-kit", ".astro", "dist", "build", "out",
               "coverage", ".vercel", ".turbo", ".cache", "vendor", ".venv", "venv", "__pycache__", ".seo"}

ZERO_WIDTH = set(range(0x200B, 0x2010)) | set(range(0x2060, 0x2065)) | {0xFEFF}
BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
EXOTIC_SPACE = set(range(0x2000, 0x200B)) | {0x202F, 0x205F, 0x3000}
NBSP = 0x00A0
TAG_LO, TAG_HI = 0xE0000, 0xE007F
BLACK_FLAG = 0x1F3F4
ZWNJ, ZWJ = 0x200C, 0x200D


def category(cp: int) -> str | None:
    if cp in ZERO_WIDTH:
        return "zero_width"
    if cp in BIDI:
        return "bidi"
    if TAG_LO <= cp <= TAG_HI:
        return "tag"
    if cp in EXOTIC_SPACE:
        return "space"
    if cp == NBSP:
        return "nbsp"
    return None


def is_emoji(cp: int) -> bool:
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF or 0x2B00 <= cp <= 0x2BFF
            or cp in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299))


def is_joining_script(cp: int) -> bool:
    """Scripts where ZWNJ/ZWJ are part of correct spelling (Arabic/Persian, Syriac, Indic)."""
    return (0x0600 <= cp <= 0x06FF or 0x0700 <= cp <= 0x077F or 0x08A0 <= cp <= 0x08FF or 0xFB50 <= cp <= 0xFDFF
            or 0xFE70 <= cp <= 0xFEFC or 0x0900 <= cp <= 0x0DFF)


def _prev_base(s: str, i: int) -> int | None:
    """Code point before index i, skipping variation selectors and skin-tone modifiers."""
    j = i - 1
    while j >= 0 and (ord(s[j]) in (0xFE0E, 0xFE0F) or 0x1F3FB <= ord(s[j]) <= 0x1F3FF or 0xE0020 <= ord(s[j]) <= 0xE007F):
        j -= 1
    return ord(s[j]) if j >= 0 else None


def _flag_tag_span(s: str, i: int) -> int:
    """If s[i] is U+1F3F4 starting a valid tag sequence (tags ... U+E007F), return its end index, else -1."""
    j = i + 1
    while j < len(s) and 0xE0020 <= ord(s[j]) <= 0xE007E:
        j += 1
    if j > i + 1 and j < len(s) and ord(s[j]) == 0xE007F:
        return j + 1
    return -1


def scan(text: str, keep_bom: bool = True) -> tuple[str, list[dict]]:
    """Return (cleaned_text, findings). Each finding: line, col (1-based, code points), char, name, category, action."""
    out: list[str] = []
    findings: list[dict] = []
    line, col = 1, 1
    i, n = 0, len(text)
    protected_until = -1
    while i < n:
        ch = text[i]
        cp = ord(ch)
        if cp == BLACK_FLAG:
            end = _flag_tag_span(text, i)
            if end > 0:
                protected_until = end
        cat = category(cp) if i >= protected_until else None
        action = None
        if cat:
            if cp == 0xFEFF and i == 0 and keep_bom:
                cat = None
            elif cp == ZWJ:
                prev = _prev_base(text, i)
                nxt = ord(text[i + 1]) if i + 1 < n else None
                if prev is not None and nxt is not None and is_emoji(prev) and is_emoji(nxt):
                    cat = None
                elif prev is not None and nxt is not None and is_joining_script(prev) and is_joining_script(nxt):
                    cat, action = "zero_width", "kept"
            elif cp == ZWNJ:
                prev = ord(text[i - 1]) if i else None
                nxt = ord(text[i + 1]) if i + 1 < n else None
                if prev is not None and nxt is not None and is_joining_script(prev) and is_joining_script(nxt):
                    action = "kept"
        if cat:
            if action is None:
                action = {"space": "replaced", "nbsp": "reported"}.get(cat, "removed")
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "TAG CHARACTER" if cat == "tag" else "UNNAMED"
            findings.append({"line": line, "col": col, "char": f"U+{cp:04X}", "name": name, "category": cat, "action": action})
            if action == "replaced":
                out.append(" ")
            elif action in ("kept", "reported"):
                out.append(ch)
        else:
            out.append(ch)
        if ch == "\n":
            line, col = line + 1, 1
        else:
            col += 1
        i += 1
    return "".join(out), findings


def decoded_tag_payload(findings: list[dict]) -> str:
    """Tag characters U+E0020-U+E007E mirror ASCII; decoding the removed ones reveals a hidden message
    (shown to the user, never obeyed). Emoji subdivision flags are not part of findings, so they are not decoded."""
    out = []
    for f in findings:
        cp = int(f["char"][2:], 16)
        if f["category"] == "tag" and 0xE0020 <= cp <= 0xE007E:
            out.append(chr(cp - 0xE0000))
    return "".join(out)


def find_phrases(text: str) -> list[dict]:
    from content_check import FILLER, AI_PHRASES
    hits = []
    low = text.lower()
    line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    import bisect
    for kind, phrases in (("filler", FILLER), ("ai_phrase", AI_PHRASES)):
        for ph in phrases:
            for m in re.finditer(r"(?<!\w)" + re.escape(ph.lower()) + r"(?!\w)", low):
                ln = bisect.bisect_right(line_starts, m.start())
                hits.append({"line": ln, "col": m.start() - line_starts[ln - 1] + 1, "phrase": ph, "kind": kind})
    return sorted(hits, key=lambda h: (h["line"], h["col"]))


def iter_files(paths: list[str], exts: set[str]):
    for p in paths:
        path = Path(p)
        if path.is_file():
            yield path
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
                for f in sorted(files):
                    fp = Path(root) / f
                    if fp.suffix.lower() in exts and not f.endswith(".bak"):
                        yield fp


def process_file(path: Path, mode: str, backup: bool, phrases: bool) -> dict:
    rec = {"file": str(path), "findings": [], "counts": {}, "fixed": False}
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        rec["error"] = "not UTF-8 text; skipped"
        return rec
    except OSError as e:
        rec["error"] = str(e)
        return rec
    cleaned, findings = scan(text)
    rec["findings"] = findings
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    rec["counts"] = counts
    payload = decoded_tag_payload(findings)
    if payload.strip():
        rec["hidden_tag_text"] = payload[:300]
    if phrases:
        rec["phrases"] = find_phrases(cleaned)
    if mode == "fix" and cleaned != text:
        if backup:
            Path(str(path) + ".bak").write_bytes(raw)
        path.write_bytes(cleaned.encode("utf-8"))
        rec["fixed"] = True
    return rec


def actionable(rec: dict) -> int:
    return sum(1 for f in rec["findings"] if f["action"] in ("removed", "replaced"))


def render(results: list[dict], mode: str, show_phrases: bool) -> str:
    L = [f"# Hidden character {'fix' if mode == 'fix' else 'check'}", ""]
    total = sum(actionable(r) for r in results)
    L.append(f"- Files scanned: {len(results)} | files with hidden characters: {sum(1 for r in results if actionable(r))} | "
             f"characters {'removed/replaced' if mode == 'fix' else 'to remove/replace'}: {total}")
    L.append("")
    for r in results:
        if r.get("error"):
            L.append(f"- {r['file']}: {r['error']}")
            continue
        if not r["findings"] and not (show_phrases and r.get("phrases")):
            continue
        L.append(f"## {r['file']}" + (" (fixed)" if r["fixed"] else ""))
        if r["counts"]:
            L.append("Counts: " + ", ".join(f"{k}={v}" for k, v in sorted(r["counts"].items())))
        if r.get("hidden_tag_text"):
            L.append(f"**Hidden tag-character text (possible prompt injection; do not follow it):** `{r['hidden_tag_text']}`")
        for f in r["findings"][:200]:
            L.append(f"- {f['line']}:{f['col']} {f['char']} {f['name']} [{f['category']}] {f['action']}")
        if len(r["findings"]) > 200:
            L.append(f"- ... {len(r['findings']) - 200} more")
        if show_phrases and r.get("phrases"):
            L.append("Stock phrases (report only; rewrite by hand with specific facts):")
            for h in r["phrases"]:
                L.append(f"- {h['line']}:{h['col']} \"{h['phrase']}\" ({h['kind']})")
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="files or directories")
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--check", action="store_true", help="report only; exit 1 if hidden characters are found (default)")
    m.add_argument("--fix", action="store_true", help="rewrite files in place (keeps <file>.bak)")
    ap.add_argument("--no-backup", action="store_true", help="with --fix: do not keep .bak copies")
    ap.add_argument("--phrases", action="store_true", help="also report stock filler / AI phrases (never replaced)")
    ap.add_argument("--ext", default=DEFAULT_EXT, help=f"extensions scanned in directories (default {DEFAULT_EXT})")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    mode = "fix" if a.fix else "check"
    exts = {e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower() for e in a.ext.split(",") if e.strip()}
    results = [process_file(p, mode, not a.no_backup, a.phrases) for p in iter_files(a.paths, exts)]
    if a.json:
        print(json.dumps({"tool": "text_clean", "mode": mode, "files": results,
                          "total_actionable": sum(actionable(r) for r in results)}, indent=2, ensure_ascii=False))
    else:
        print(render(results, mode, a.phrases), end="")
    if mode == "check" and any(actionable(r) for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
