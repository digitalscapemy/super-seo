#!/usr/bin/env python3
"""validate_jsonld.py - Claude Code PostToolUse hook: checks JSON-LD in files Claude just edited or wrote.

Reads the hook event JSON from stdin (tool_name, tool_input.file_path, cwd, ...). For supported file types it checks:
  - <script type="application/ld+json"> blocks with literal JSON: must parse (template expressions are skipped)
  - JS/TS object literals that look like JSON-LD (contain "@context" / "@type"): text checks only, no JS parsing
  - .json files that contain JSON-LD: must parse
Checks: placeholder values, example.com in url/logo/sameAs/@id, deprecated schema.org types, hardcoded
aggregateRating values in templates/components, missing @context, http:// schema.org context.

Contract (Claude Code hooks):
  exit 2 + message on stderr  -> blocking problems; stderr is fed back to Claude so it fixes them
  exit 0 + JSON on stdout     -> warnings only: {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                 "additionalContext": "..."}, "systemMessage": "..."}
  exit 0, no output           -> nothing found, unsupported file, unreadable/binary/huge file, or any internal error

Manual use:  python validate_jsonld.py path/to/file.tsx      (same exit codes; warnings printed to stderr)
Disable for one session: set SUPER_SEO_JSONLD_HOOK=off. Stdlib only, no network.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_OBJECT_CHARS = 200_000
EXTS = (".blade.php", ".html", ".htm", ".jsx", ".tsx", ".ts", ".js", ".mjs", ".vue", ".svelte", ".astro", ".php",
        ".mdx", ".md", ".json", ".liquid")
# files where a literal ratingValue means someone typed it in instead of loading real data
COMPONENT_EXTS = (".blade.php", ".html", ".htm", ".jsx", ".tsx", ".ts", ".js", ".mjs", ".vue", ".svelte", ".astro",
                  ".php", ".mdx", ".liquid")

DEPRECATED = {"HowTo": "removed from Google rich results; use plain H2 steps",
              "SpecialAnnouncement": "deprecated", "CourseInfo": "deprecated; use Course",
              "EstimatedSalary": "deprecated; use JobPosting.baseSalary", "LearningVideo": "deprecated; use VideoObject",
              "VehicleListing": "deprecated; use Product", "ClaimReview": "no longer shown in Google Search"}
PLACEHOLDER_CS = re.compile(r"\[REPLACE|\[INSERT|\[Your |\bXXX\b|(?i:lorem ipsum)")  # XXX only in capitals
EXAMPLE_HOST = re.compile(r"//(?:www\.)?example\.(?:com|org|net)\b", re.I)
URL_PROP = re.compile(r"""["']?(url|logo|sameAs|@id)["']?\s*:\s*(\[[^\]]{0,4000}\]|"[^"\n]{0,2000}"|'[^'\n]{0,2000}')""")
TYPE_PROP = re.compile(r"""["']?@type["']?\s*:\s*(\[[^\]]{0,2000}\]|"[^"\n]{0,200}"|'[^'\n]{0,200}')""")
HTTP_CONTEXT = re.compile(r"""["']?@context["']?\s*:\s*["']http://schema\.org""", re.I)
RATING_LITERAL = re.compile(r"""["']?aggregateRating["']?\s*:\s*\{[^{}]{0,2000}?["']?ratingValue["']?\s*:\s*["']?\d""")
SCRIPT_LD = re.compile(r"""<script\b([^>]*?\btype\s*=\s*["']?application/ld\+json["']?[^>]*)>(.*?)</script\s*>""",
                       re.I | re.S)
TEMPLATE_MARKERS = ("JSON.stringify", "{{", "{%", "{@html", "<?php", "<?=", "${", "{!!", "@json(", "@php")
LD_VAR = re.compile(r"(json_?ld|ld_?json|schema|structured_?data|rich_?result)\w*\s*(?::\s*[\w.<>\[\], |]+)?\s*=\s*$", re.I)
TOKENS = re.compile(r"""[{}]|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/""", re.S)
JS_EXPR = re.compile(r"\{\s*[A-Za-z_$@(]")  # {jsonLd} / {schema(...)} expression, not a JSON object
FENCE = re.compile(r"(?ms)^(```|~~~).*?^\1[ \t]*$")


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


class Report:
    def __init__(self, text: str):
        self.text = text
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, pos: int, msg: str):
        if len(self.errors) >= 50:
            return
        item = f"line {line_of(self.text, pos)}: {msg}"
        if item not in self.errors:
            self.errors.append(item)

    def warn(self, pos: int, msg: str):
        if len(self.warnings) >= 50:
            return
        item = f"line {line_of(self.text, pos)}: {msg}"
        if item not in self.warnings:
            self.warnings.append(item)


# --------------------------------------------------------------------------- checks
def text_checks(rep: Report, chunk: str, base: int, component: bool):
    """Checks that work on raw JSON-LD text (literal JSON, JS object literal, or template)."""
    for m in PLACEHOLDER_CS.finditer(chunk):
        rep.err(base + m.start(), f"placeholder value '{m.group(0).strip()}' in JSON-LD; use real data or remove the property")
        break
    for m in URL_PROP.finditer(chunk):
        if EXAMPLE_HOST.search(m.group(2)):
            rep.err(base + m.start(), f"{m.group(1)} points to example.com (placeholder); use the real production URL")
    for m in TYPE_PROP.finditer(chunk):
        for name in re.findall(r"""["']([A-Za-z]+)["']""", m.group(1)):
            if name in DEPRECATED:
                rep.warn(base + m.start(), f"schema type {name}: {DEPRECATED[name]}")
    m = HTTP_CONTEXT.search(chunk)
    if m:
        rep.warn(base + m.start(), "@context uses http://schema.org; use https://schema.org")
    if component:
        m = RATING_LITERAL.search(chunk)
        if m:
            rep.warn(base + m.start(), "aggregateRating has a hardcoded ratingValue in a template/component; it must come "
                                       "from real, visible reviews (fake ratings risk a manual action)")


def missing_context(data) -> bool:
    """True when a top-level node has @type but no @context."""
    items = data if isinstance(data, list) else [data]
    for it in items:
        if isinstance(it, dict) and ("@type" in it or "@graph" in it) and "@context" not in it:
            return True
    return False


def check_literal_json(rep: Report, chunk: str, base: int, component: bool, label: str):
    try:
        data = json.loads(chunk)
    except json.JSONDecodeError as e:
        rep.err(base + _offset(chunk, e.lineno, e.colno), f"{label} is not valid JSON: {e.msg} (column {e.colno})")
        text_checks(rep, chunk, base, component)
        return
    if missing_context(data):
        rep.err(base, f"{label} has @type at top level but no @context; add \"@context\": \"https://schema.org\"")
    text_checks(rep, chunk, base, component)


def _offset(chunk: str, lineno: int, colno: int) -> int:
    lines = chunk.split("\n")
    return sum(len(l) + 1 for l in lines[: max(0, lineno - 1)]) + max(0, colno - 1)


def is_template(chunk: str) -> bool:
    return any(mk in chunk for mk in TEMPLATE_MARKERS)


# --------------------------------------------------------------------------- extraction
def blank_comments(chunk: str) -> str:
    """Replace // and /* */ comments with spaces (same length, so offsets stay valid); strings are left alone."""
    def repl(m: re.Match) -> str:
        tok = m.group(0)
        return re.sub(r"[^\n]", " ", tok) if tok.startswith(("//", "/*")) else tok
    return TOKENS.sub(repl, chunk)


LD_KEY_TOKEN = re.compile(r"""^["']@(context|type)["']$""")
COLON_AFTER = re.compile(r"\s*:")


def js_objects(text: str, skip: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Outermost object literals that hold an "@context" / "@type" key, outside the skipped ranges.
    One regex-driven pass over braces, strings and comments; nested JSON-LD nodes (offers, author,
    @graph items) are covered by their outermost JSON-LD object."""
    if skip:  # blank the already-checked <script> blocks, keeping offsets
        parts, last = [], 0
        for a, b in skip:
            parts += [text[last:a], re.sub(r"[^\n]", " ", text[a:b])]
            last = b
        text = "".join(parts) + text[last:]
    stack: list[list] = []          # [start, holds_ld_key]
    spans: list[tuple[int, int]] = []
    for m in TOKENS.finditer(text):
        tok = m.group(0)
        if tok == "{":
            stack.append([m.start(), False])
        elif tok == "}":
            if stack:
                start, is_ld = stack.pop()
                if is_ld and m.end() - start <= MAX_OBJECT_CHARS:
                    spans.append((start, m.end()))
        elif stack and len(tok) <= 10 and LD_KEY_TOKEN.match(tok) and COLON_AFTER.match(text, m.end()):
            stack[-1][1] = True
    spans.sort()
    outer, last_end = [], -1
    for s in spans:
        if s[0] >= last_end:
            outer.append(s)
            last_end = s[1]
    return outer


def check_text(path: str, text: str) -> Report:
    rep = Report(text)
    low = path.lower()
    component = low.endswith(COMPONENT_EXTS)
    scan = text
    if low.endswith((".md", ".mdx")):  # documentation examples inside code fences are not shipped markup
        scan = FENCE.sub(lambda m: " " * len(m.group(0)), text)

    if low.endswith(".json"):
        if "@context" in text or '"@type"' in text:
            check_literal_json(rep, text, 0, False, "JSON-LD file")
        return rep

    if "@context" not in scan and "@type" not in scan and "ld+json" not in scan.lower():
        return rep

    skip: list[tuple[int, int]] = []
    for m in SCRIPT_LD.finditer(scan):
        attrs, body = m.group(1), m.group(2)
        if attrs.rstrip().endswith("/"):  # <script ... /> self-closing (JSX dangerouslySetInnerHTML) has no body
            continue
        skip.append((m.start(), m.end()))
        start = m.start(2)
        stripped = body.strip()
        if not stripped:
            continue
        if is_template(body) or JS_EXPR.match(stripped):
            text_checks(rep, body, start, component)  # template expression: no parse, text checks only
            continue
        check_literal_json(rep, body, start, component, "JSON-LD <script> block")

    if not low.endswith((".html", ".htm")):
        for a, b in js_objects(scan, skip):
            chunk = blank_comments(scan[a:b])  # commented-out code is not shipped
            text_checks(rep, chunk, a, component)
            if '@context' not in chunk:
                before = scan[max(0, a - 200):a]
                if LD_VAR.search(before):
                    rep.warn(a, "JSON-LD object has @type but no @context; add \"@context\": \"https://schema.org\" "
                                "(ignore if it is merged into a parent that has one)")
    return rep


def check_file(path: str) -> Report | None:
    if not path or not path.lower().endswith(EXTS):
        return None
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")
    return check_text(path, text)


# --------------------------------------------------------------------------- entry points
def _emit(path: str, rep: Report | None, hook_mode: bool) -> int:
    if rep is None or (not rep.errors and not rep.warnings):
        return 0
    name = Path(path).name
    if rep.errors:
        lines = [f"super-seo JSON-LD check failed for {path}:"] + [f"  - {e}" for e in rep.errors[:15]]
        if rep.warnings:
            lines += ["Warnings:"] + [f"  - {w}" for w in rep.warnings[:10]]
        lines.append("Fix these in the file (structured data must be valid and use real values only).")
        print("\n".join(lines), file=sys.stderr)
        return 2
    msg = f"super-seo JSON-LD warnings for {name}:\n" + "\n".join(f"  - {w}" for w in rep.warnings[:15])
    if hook_mode:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg},
                          "systemMessage": msg}))
    else:
        print(msg, file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get("SUPER_SEO_JSONLD_HOOK", "").lower() in ("off", "0", "false", "no"):
        return 0
    if argv:
        worst = 0
        for f in argv:
            worst = max(worst, _emit(f, check_file(f), hook_mode=False))
        return worst
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return 0
    if not isinstance(event, dict):
        return 0
    tool_input = event.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not isinstance(path, str) or not path:
        return 0
    if not os.path.isabs(path) and event.get("cwd"):
        path = os.path.join(str(event["cwd"]), path)
    return _emit(path, check_file(path), hook_mode=True)


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # a validator bug must never break the user's editing session
        code = 0
    sys.exit(code)
