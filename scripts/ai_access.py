#!/usr/bin/env python3
"""ai_access.py - can AI search engines and agents actually reach and read this site?

Checks, for one origin:
  1. robots.txt policy per AI crawler (search/retrieval vs training vs user-triggered)
  2. Live probe: fetch key URLs with each crawler's User-Agent and compare to a
     browser fetch -> detects WAF/CDN blocks (Cloudflare "Block AI bots", 403, challenge pages)
  3. Content visible without JavaScript (word count in raw HTML)
  4. llms.txt / llms-full.txt presence and format
  5. Markdown negotiation (Accept: text/markdown) and Content-Signal lines
  6. Snippet controls (nosnippet / max-snippet / data-nosnippet) that silently remove
     pages from Google AI Overviews / AI Mode

Usage:
  python ai_access.py https://example.com
  python ai_access.py http://localhost:3000 --paths / /pricing /docs
  python ai_access.py https://example.com --json --out .seo/audits
Python 3.10+, stdlib only. Imports fetch/PageParser from crawl_audit.py (same folder).

Caveat printed in the report: WAFs often verify real bots by IP / reverse DNS, so a
spoofed UA can be blocked when the real bot is not (or the opposite). A block here is a
strong hint, not proof; confirm in the CDN/WAF dashboard and server logs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_audit import fetch, PageParser, PUBLIC_ONLY, is_private_host  # noqa: E402
from robots_rfc import Robots  # noqa: E402

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# token, operator, purpose, full UA (None = robots.txt token only, never sends requests itself)
# purpose: search = builds an index used to cite/link in AI answers; user = fetches live when a user asks;
#          training = collects model-training data; classic = traditional search engine that also feeds AI features
CRAWLERS = [
    ("Googlebot", "Google", "classic", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
     "Feeds Google Search AND AI Overviews / AI Mode. Blocking it removes you from both."),
    ("Google-Extended", "Google", "training", None,
     "Token only. Controls Gemini training/grounding use; does NOT affect Search or AI Overviews."),
    ("bingbot", "Microsoft", "classic", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
     "Feeds Bing, Copilot, and is a major source for ChatGPT search and DuckDuckGo."),
    ("OAI-SearchBot", "OpenAI", "search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot",
     "ChatGPT search index. Needed to be cited/linked in ChatGPT answers."),
    ("ChatGPT-User", "OpenAI", "user", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
     "Live fetch when a ChatGPT user/agent opens your URL."),
    ("GPTBot", "OpenAI", "training", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot",
     "Training data for OpenAI models."),
    ("Claude-SearchBot", "Anthropic", "search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +https://www.anthropic.com)",
     "Index for Claude's web search results."),
    ("Claude-User", "Anthropic", "user", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +https://www.anthropic.com)",
     "Live fetch when a Claude user asks about your URL."),
    ("ClaudeBot", "Anthropic", "training", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
     "Training data for Anthropic models."),
    ("PerplexityBot", "Perplexity", "search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
     "Perplexity index; needed to be cited in Perplexity."),
    ("Perplexity-User", "Perplexity", "user", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)",
     "Live fetch for a Perplexity user's question."),
    ("Applebot", "Apple", "classic", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
     "Spotlight, Siri, Safari suggestions."),
    ("Applebot-Extended", "Apple", "training", None, "Token only. Apple Intelligence training use."),
    ("DuckAssistBot", "DuckDuckGo", "search", "DuckAssistBot/1.2; (+http://duckduckgo.com/duckassistbot.html)",
     "DuckDuckGo AI-assisted answers."),
    ("MistralAI-User", "Mistral", "user", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; MistralAI-User/1.0; +https://docs.mistral.ai/robots)",
     "Le Chat live fetch."),
    ("meta-externalagent", "Meta", "training", "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
     "Meta AI training/indexing."),
    ("Amazonbot", "Amazon", "search", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)",
     "Alexa / Amazon answers."),
    ("CCBot", "Common Crawl", "training", "CCBot/2.0 (https://commoncrawl.org/faq/)",
     "Open dataset used to train many LLMs."),
    ("Bytespider", "ByteDance", "training", "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)",
     "ByteDance (Doubao) training."),
]
# what a SaaS that WANTS AI visibility should do. Training is a business choice, default allow.
RECOMMEND = {"classic": "allow", "search": "allow", "user": "allow", "training": "your choice"}
PROBE_SET = {"Googlebot", "bingbot", "OAI-SearchBot", "ChatGPT-User", "GPTBot", "Claude-SearchBot", "Claude-User",
             "ClaudeBot", "PerplexityBot", "Perplexity-User"}
CHALLENGE_RX = re.compile(r"(cf-chl|challenge-platform|Just a moment\.\.\.|Attention Required! \| Cloudflare|"
                          r"captcha|px-captcha|_Incapsula_Resource|DataDome|Access denied|Request unsuccessful)", re.I)


def words_in(html: str) -> tuple[int, PageParser]:
    p = PageParser()
    try:
        p.feed(html)
    except Exception:
        pass
    text = " ".join(" ".join(p.text_parts).split())
    return len(re.findall(r"\w+", text)), p


def robots_groups(text: str) -> dict[str, list[str]]:
    """Map lowercased user-agent token -> raw rule lines (for display)."""
    groups: dict[str, list[str]] = {}
    current: list[str] = []
    last_was_ua = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k.lower() == "user-agent":
            if not last_was_ua:
                current = []
            current.append(v.lower())
            groups.setdefault(v.lower(), [])
            last_was_ua = True
        else:
            last_was_ua = False
            for ua in current:
                groups.setdefault(ua, []).append(f"{k}: {v}")
    return groups


def fix_path(p: str) -> str:
    """Undo Git-Bash/MSYS path conversion ('/' -> 'C:/Program Files/Git/') and ensure a leading slash."""
    p = p.replace("\\", "/")
    m = re.match(r"^[A-Za-z]:/(?:Program Files/Git|msys64|Git)(/.*)?$", p, re.I)
    if m:
        p = m.group(1) or "/"
    return p if p.startswith("/") else "/" + p


def check_llms_txt(origin: str) -> dict:
    out = {}
    for name in ("llms.txt", "llms-full.txt"):
        r = fetch(f"{origin}/{name}", BROWSER_UA, accept="text/plain,text/markdown,*/*")
        info = {"status": r["status"], "content_type": r["headers"].get("content-type", "")}
        body = r["body"] if r["status"] == 200 else ""
        if body and "<html" in body[:500].lower():
            info["problem"] = "returns HTML (SPA fallback?) instead of text/markdown"
            body = ""
        if body:
            lines = body.splitlines()
            info["bytes"] = len(body.encode("utf-8"))
            info["has_h1"] = bool(lines and lines[0].startswith("# "))
            info["has_summary_blockquote"] = any(l.startswith("> ") for l in lines[:15])
            info["h2_sections"] = [l[3:].strip() for l in lines if l.startswith("## ")][:20]
            info["links"] = len(re.findall(r"\[[^\]]+\]\((https?://|/)[^)]+\)", body))
            problems = []
            if not info["has_h1"]:
                problems.append("first line must be '# <Project name>'")
            if not info["has_summary_blockquote"]:
                problems.append("recommended: a '> one-paragraph summary' right after the H1 (optional in the spec)")
            rel_links = re.findall(r"\]\((/[^)]*)\)", body)
            if rel_links:
                problems.append(f"{len(rel_links)} relative links; use absolute https URLs")
            if name == "llms.txt" and info["links"] == 0:
                problems.append("no markdown links; llms.txt should list key pages as - [Title](url): note")
            if name == "llms.txt" and info["bytes"] > 50_000:
                problems.append("over ~50 KB; keep llms.txt an index and move bulk text to llms-full.txt")
            info["problems"] = problems
        out[name] = info
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--paths", nargs="*", default=None, help="paths to probe (default: / plus /pricing, /docs, /blog if they exist)")
    ap.add_argument("--no-probe", action="store_true", help="skip live UA probes (robots.txt only)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    u = urlparse(a.url)
    origin = f"{u.scheme}://{u.netloc}"
    PUBLIC_ONLY["on"] = not is_private_host(u.hostname)
    findings = []

    def add(sev, code, msg):
        findings.append({"severity": sev, "code": code, "message": msg})

    # ---- robots.txt
    rr = fetch(origin + "/robots.txt", BROWSER_UA, accept="text/plain,*/*")
    robots_text = rr["body"] if rr["status"] == 200 and "html" not in rr["headers"].get("content-type", "") else ""
    rp = Robots(robots_text)
    content_signals = re.findall(r"(?im)^\s*content-signal:\s*(.+)$", robots_text)
    if rr["status"] >= 500:
        add("critical", "robots_5xx", f"/robots.txt returns {rr['status']}: Google and RFC 9309 treat this as 'disallow everything'")
    if re.search(r"#\s*BEGIN Cloudflare Managed content", robots_text, re.I):
        add("high", "cloudflare_managed_robots", "Live robots.txt contains a Cloudflare-managed block (not in your repo file) that disallows "
            "AI crawlers such as GPTBot, ClaudeBot, Google-Extended, Applebot-Extended, meta-externalagent, CCBot. If you want AI "
            "visibility, turn off the managed robots.txt / 'block AI training' option in Cloudflare (Security > Bots / AI Crawl Control).")
    for line in content_signals:
        for pair in [x.strip() for x in line.split(",") if x.strip()]:
            k, _, v = pair.partition("=")
            k, v = k.strip().lower(), v.strip().lower()
            if k not in ("search", "ai-input", "ai-train"):
                add("low", "content_signal_unknown_key", f"Content-Signal key '{k}' is not one of search / ai-input / ai-train")
            elif v not in ("yes", "no"):
                add("low", "content_signal_bad_value", f"Content-Signal {k}={v}: value must be yes or no")
            elif k in ("search", "ai-input") and v == "no":
                add("medium", f"content_signal_{k}_no", f"Content-Signal {k}=no asks AI systems not to use your content for "
                    + ("search results" if k == "search" else "AI answers (grounding/RAG)"))

    paths = [fix_path(x) for x in a.paths] if a.paths else ["/"]
    if a.paths is None:
        for cand in ("/pricing", "/docs", "/blog", "/features"):
            r = fetch(origin + cand, BROWSER_UA)
            if r["status"] == 200:
                paths.append(cand)
            if len(paths) >= 3:
                break

    policy = []
    for token, op, purpose, ua, note in CRAWLERS:
        allowed = {p: rp.can_fetch(token, origin + p) for p in paths}
        explicit = rp.explicit(token)
        status = "allowed" if all(allowed.values()) else ("blocked" if not any(allowed.values()) else "partial")
        policy.append({"token": token, "operator": op, "purpose": purpose, "robots": status,
                       "explicit_group": explicit, "note": note})
        if status != "allowed" and RECOMMEND[purpose] == "allow":
            sev = "critical" if token in ("Googlebot", "bingbot") else "high"
            add(sev, f"robots_blocks_{token}", f"robots.txt {status} {token} ({op}, {purpose}): {note}")
        elif status != "allowed" and purpose == "training":
            add("info", f"robots_blocks_{token}", f"{token} ({op}) training crawler is {status}: deliberate choice? Does not affect citations.")

    # ---- baseline browser fetch + probes
    probes = []
    base = {}
    result_link_rels: list[str] = []
    for p in paths:
        r = fetch(origin + p, BROWSER_UA)
        w, parser = words_in(r["body"]) if r["status"] == 200 else (0, PageParser())
        base[p] = {"status": r["status"], "words": w, "bytes": r["bytes"]}
        if r["status"] == 200:
            meta_robots = " ".join(filter(None, [parser.meta("robots"), parser.meta("googlebot")])).lower()
            xr = r["headers"].get("x-robots-tag", "").lower()
            if "nosnippet" in meta_robots or "nosnippet" in xr:
                add("high", "nosnippet", f"{p}: nosnippet set -> excluded from AI Overviews / AI Mode and snippets")
            m = re.search(r"max-snippet\s*:\s*(-?\d+)", meta_robots + " " + xr)
            if m and 0 <= int(m.group(1)) < 50:
                add("medium", "max_snippet_low", f"{p}: max-snippet:{m.group(1)} limits how much AI features can quote")
            ai_meta = re.findall(r"\b(noai|noimageai)\b", meta_robots + " " + xr)
            if ai_meta:
                add("info", "noai_meta", f"{p}: {', '.join(sorted(set(ai_meta)))} directive (non-standard; honoured by few crawlers)")
            for m in parser.metas:
                nm = m.get("name", "").lower()
                if nm in ("gptbot", "oai-searchbot", "claudebot", "perplexitybot", "google-extended", "bingbot") and "noindex" in m.get("content", "").lower():
                    sev = "info" if nm in ("gptbot", "claudebot", "google-extended") else "high"
                    add(sev, f"meta_noindex_{nm}", f"{p}: <meta name='{m.get('name')}' content='{m.get('content')}'>")
            if p == paths[0] and r["headers"].get("link"):
                result_link_rels.extend(re.findall(r'rel="?([\w\- ]+)"?', r["headers"]["link"]))
            if "data-nosnippet" in r["body"]:
                add("info", "data_nosnippet", f"{p}: data-nosnippet used; make sure it does not wrap key answers")
            if w < 80:
                add("critical", "no_text_without_js", f"{p}: only {w} words in raw HTML. Non-JS AI crawlers see an empty page.")
    if not a.no_probe:
        for token, op, purpose, ua, _ in CRAWLERS:
            if not ua or token not in PROBE_SET:
                continue
            for p in paths:
                r = fetch(origin + p, ua, timeout=15)
                w, _p = words_in(r["body"]) if r["status"] == 200 else (0, None)
                challenged = bool(CHALLENGE_RX.search(r["body"][:20000])) if r["body"] else False
                b = base.get(p, {})
                verdict = "ok"
                if r["status"] in (401, 403, 406, 429, 503) or r["status"] == 0:
                    verdict = f"blocked ({r['status'] or r['error']})"
                elif challenged and r["status"] != 200:
                    verdict = "challenge page"
                elif b.get("words") and w < 0.5 * b["words"]:
                    verdict = f"degraded ({w} vs {b['words']} words)"
                elif challenged and b.get("words") and w < 0.8 * b["words"]:
                    verdict = "challenge page"
                probes.append({"token": token, "path": p, "status": r["status"], "words": w, "verdict": verdict})
                if verdict != "ok" and RECOMMEND[purpose] == "allow":
                    add("high", f"waf_blocks_{token}", f"{p}: {token} got '{verdict}' while a browser got {b.get('status')}. "
                        "Likely CDN/WAF bot rule (e.g. Cloudflare 'Block AI bots' / Bot Fight Mode). Verify in dashboard.")

    # ---- markdown negotiation
    md = fetch(origin + paths[0], BROWSER_UA, accept="text/markdown, text/plain;q=0.5")
    md_ok = md["status"] == 200 and "markdown" in md["headers"].get("content-type", "")
    if not md_ok:
        add("low", "no_markdown_negotiation", "Server does not return text/markdown for Accept: text/markdown "
            "(optional; lets agents fetch a clean copy. Cloudflare 'Markdown for Agents' or a middleware can add it)")

    # ---- llms.txt
    llms = check_llms_txt(origin)
    if llms["llms.txt"]["status"] != 200 or llms["llms.txt"].get("problem"):
        add("low", "no_llms_txt", f"/llms.txt: {llms['llms.txt'].get('problem') or llms['llms.txt']['status']}. "
            "Cheap to add; evidence it changes AI citations is weak, but coding agents and doc tools do read it.")
    for pr in llms["llms.txt"].get("problems", []):
        add("low", "llms_txt_format", f"/llms.txt: {pr}")

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: order[f["severity"]])
    result = {"tool": "ai_access", "origin": origin, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "robots_status": rr["status"], "content_signals": content_signals, "paths": paths, "baseline": base,
              "policy": policy, "probes": probes, "markdown_negotiation": md_ok, "link_header_rels": result_link_rels, "llms": llms, "findings": findings}

    L = [f"# AI access check: {origin}", "", f"- Checked {result['checked_at']} | robots.txt: {rr['status']} | paths: {', '.join(paths)}",
         f"- Content-Signal: {'; '.join(content_signals) or 'none'} | Markdown negotiation: {'yes' if md_ok else 'no'} | "
         f"llms.txt: {llms['llms.txt']['status']} | llms-full.txt: {llms['llms-full.txt']['status']}", "",
         "## Findings", ""]
    L += [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in findings] or ["- No issues found."]
    L += ["", "## Crawler policy (robots.txt)", "", "| Crawler | Operator | Purpose | robots.txt | Recommended for SaaS |", "|---|---|---|---|---|"]
    for p in policy:
        L.append(f"| {p['token']} | {p['operator']} | {p['purpose']} | {p['robots']}{' (explicit)' if p['explicit_group'] else ''} | {RECOMMEND[p['purpose']]} |")
    if probes:
        L += ["", "## Live probes (spoofed UA)", "", "| Crawler | Path | Status | Words | Verdict |", "|---|---|---|---|---|"]
        for pr in probes:
            L.append(f"| {pr['token']} | {pr['path']} | {pr['status']} | {pr['words']} | {pr['verdict']} |")
        L += ["", "> Probes spoof the User-Agent only. WAFs that verify bots by IP may treat real crawlers differently: "
              "treat blocks as strong hints and confirm in the CDN/WAF dashboard or server logs."]
    text = "\n".join(L) + "\n"
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        host = u.netloc.replace(":", "_")
        (out / f"ai-access-{host}-{stamp}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out / f"ai-access-{host}-{stamp}.md").write_text(text, encoding="utf-8")
    print(json.dumps(result, indent=2) if a.json else text)


if __name__ == "__main__":
    main()
