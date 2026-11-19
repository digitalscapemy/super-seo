#!/usr/bin/env python3
"""hints_check.py - stdlib-only page-performance HINTS from raw HTML + response headers (no browser).

What it looks at:
  - speculation rules (<script type="speculationrules"> or Speculation-Rules header); deprecated <link rel=prerender>
  - preload / preconnect / modulepreload counts (more than 6 preloads compete with the LCP resource)
  - hero / LCP candidate image (the og:image match, else the first large <img> inside <main>): fetchpriority=high,
    and never loading=lazy
  - render-blocking resources: <script src> in <head> without async/defer/type=module, > 4 blocking stylesheets
  - fonts: inline @font-face without font-display, Google Fonts CSS URLs without &display=swap
  - bfcache killers: Cache-Control: no-store on the HTML, inline `unload` listeners / onunload attributes
  - inline JavaScript total (> 100 KB), DOM size estimate (> 1500 elements low, > 3000 medium)
  - third-party script origins

These are HINTS from static analysis. The hint score is NOT a Core Web Vitals prediction; measure CWV with field
data (CrUX / PageSpeed Insights) or a lab run.

Usage:
  python hints_check.py https://example.com/
  python hints_check.py http://localhost:3000/pricing --json --no-write

Outputs <out>/hints-<host>-<timestamp>.json and .md (default --out .seo/audits) unless --no-write.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402

SEVERITY_ORDER = ca.SEVERITY_ORDER
UA_DEFAULT = ca.UA_DEFAULT
NON_JS_TYPES = re.compile(r"json|ld\+json|speculationrules|importmap|text/template|text/x-template|text/html|text/plain|x-shader", re.I)
JS_TYPES = ("", "text/javascript", "application/javascript", "module", "text/ecmascript", "application/ecmascript")
UNLOAD_RE = re.compile(r"""addEventListener\(\s*['"]unload['"]|\bonunload\s*=|\$\(\s*window\s*\)\.(on\(\s*['"]unload|unload\()""", re.I)
FONT_FACE_RE = re.compile(r"@font-face\s*\{([^}]*)\}", re.I | re.S)
MAX_PRELOADS = 6
MAX_BLOCKING_CSS = 4
INLINE_JS_LIMIT = 100 * 1024
WEIGHTS = {"critical": 20, "high": 10, "medium": 5, "low": 2, "info": 0}


def add(findings, severity, code, message, **extra):
    findings.append({"severity": severity, "code": code, "message": message, **extra})


class HintsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = 0
        self.in_head = False
        self.head_seen_end = False
        self.main_depth = 0
        self.scripts: list[dict] = []
        self.links: list[dict] = []
        self.imgs: list[dict] = []
        self.metas: list[dict] = []
        self.unload_attrs: list[str] = []
        self._script = None
        self._style = None
        self.styles: list[str] = []
        self.body_started = False

    def handle_starttag(self, tag, attrs):
        self.elements += 1
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
            self.body_started = True
        elif tag == "main":
            self.main_depth += 1
        if "onunload" in a:
            self.unload_attrs.append(tag)
        if tag == "script":
            rec = {"src": a.get("src", "").strip(), "async": "async" in a, "defer": "defer" in a,
                   "type": a.get("type", "").strip().lower(), "in_head": self.in_head and not self.body_started,
                   "inline_len": 0, "text": [], "nomodule": "nomodule" in a}
            self.scripts.append(rec)
            self._script = rec
        elif tag == "style":
            self._style = []
        elif tag == "link":
            a["_in_head"] = self.in_head and not self.body_started
            self.links.append(a)
        elif tag == "img":
            a["_in_main"] = self.main_depth > 0
            a["_index"] = len(self.imgs)
            self.imgs.append(a)
        elif tag == "meta":
            self.metas.append(a)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("script", "style"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False
        elif tag == "main" and self.main_depth:
            self.main_depth -= 1
        elif tag == "script" and self._script is not None:
            self._script["text"] = "".join(self._script["text"])
            self._script["inline_len"] = len(self._script["text"].encode("utf-8"))
            self._script = None
        elif tag == "style" and self._style is not None:
            self.styles.append("".join(self._style))
            self._style = None

    def handle_data(self, data):
        if self._script is not None:
            self._script["text"].append(data)
        elif self._style is not None:
            self._style.append(data)

    def meta(self, key):
        for m in self.metas:
            if m.get("property", "").lower() == key or m.get("name", "").lower() == key:
                return m.get("content")
        return None


def _rels(link):
    return link.get("rel", "").lower().split()


def _int(v):
    m = re.match(r"\s*(\d+)", v or "")
    return int(m.group(1)) if m else None


def _site(host: str) -> str:
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def pick_hero(p: HintsParser, base: str) -> dict | None:
    og = p.meta("og:image")
    og_name = urlparse(urljoin(base, og)).path.rsplit("/", 1)[-1].lower() if og else ""
    def name(img):
        return urlparse(urljoin(base, img.get("src", ""))).path.rsplit("/", 1)[-1].lower()
    if og_name:
        for img in p.imgs[:30]:
            if img.get("src") and name(img) == og_name:
                return {**img, "_reason": "matches og:image"}
    pool = [i for i in p.imgs if i["_in_main"]] or p.imgs
    for img in pool[:30]:
        src = img.get("src", "")
        if not src and not img.get("srcset"):
            continue
        if src.startswith("data:") or re.search(r"(logo|icon|avatar|sprite|pixel|badge)", src, re.I):
            continue
        w, h = _int(img.get("width")), _int(img.get("height"))
        if w is not None and w < 300 or h is not None and h < 150:
            continue
        return {**img, "_reason": "first large <img> in <main>" if img["_in_main"] else "first large <img>"}
    return None


def analyze(html: str, headers: dict, url: str) -> dict:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    f: list[dict] = []
    p = HintsParser()
    try:
        p.feed(html)
    except Exception as e:  # noqa: BLE001
        add(f, "low", "html_parse_error", f"HTML parser error: {e}")
    page_host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path or "/"
    stats: dict = {"elements": p.elements, "scripts": len(p.scripts), "images": len(p.imgs)}

    # ---- speculation rules / prerender
    spec_tags = [s for s in p.scripts if s["type"] == "speculationrules"]
    stats["speculation_rules"] = bool(spec_tags or headers.get("speculation-rules"))
    if stats["speculation_rules"]:
        add(f, "info", "speculation_rules_present", "Speculation rules present (prefetch/prerender of likely next pages)")
    else:
        add(f, "info", "speculation_rules_missing", "No speculation rules; consider <script type=\"speculationrules\"> "
            "with prefetch/prerender (moderate eagerness) for instant navigations on marketing pages")
    if any("prerender" in _rels(l) for l in p.links):
        add(f, "low", "link_prerender_deprecated", "<link rel=prerender> is deprecated (NoState Prefetch); use speculation rules")

    # ---- preload / preconnect
    preloads = [l for l in p.links if "preload" in _rels(l)]
    preconnects = [l for l in p.links if "preconnect" in _rels(l)]
    modpre = [l for l in p.links if "modulepreload" in _rels(l)]
    stats.update(preloads=len(preloads), preconnects=len(preconnects), modulepreloads=len(modpre))
    if len(preloads) > MAX_PRELOADS:
        add(f, "low", "too_many_preloads", f"{len(preloads)} <link rel=preload> (> {MAX_PRELOADS}); they compete for "
            "bandwidth with the LCP resource. Keep preloads for the hero image and critical fonts",
            samples=[l.get("href", "")[:120] for l in preloads[:10]])
    if len(preconnects) > MAX_PRELOADS:
        add(f, "low", "too_many_preconnects", f"{len(preconnects)} preconnects (> {MAX_PRELOADS}); keep only the 2-4 most critical origins")

    # ---- hero / LCP candidate
    hero = pick_hero(p, url)
    if hero:
        src = hero.get("src") or hero.get("srcset", "").split(" ")[0]
        stats["hero"] = {"src": src[:200], "reason": hero["_reason"], "fetchpriority": hero.get("fetchpriority") or None,
                         "loading": hero.get("loading") or None}
        if (hero.get("loading") or "").lower() == "lazy":
            add(f, "medium", "hero_lazy", f"Hero/LCP candidate image is loading=lazy ({src[:100]}); remove lazy, add fetchpriority=high")
        if (hero.get("fetchpriority") or "").lower() != "high":
            abs_src = urljoin(url, hero.get("src", ""))
            preloaded = any(l.get("as", "").lower() == "image" and (urljoin(url, l.get("href", "")) == abs_src or l.get("imagesrcset"))
                            and (l.get("fetchpriority") or "").lower() == "high" for l in preloads)
            if not preloaded:
                add(f, "low", "hero_no_fetchpriority", f"Hero/LCP candidate image ({src[:100]}) has no fetchpriority=high")
    else:
        stats["hero"] = None
        add(f, "info", "hero_not_found", "No hero image candidate found in the HTML (LCP may be text, CSS background or client-rendered)")

    # ---- render-blocking scripts / CSS
    blocking = []
    for s in p.scripts:
        if not s["in_head"] or not s["src"] or s["async"] or s["defer"]:
            continue
        if s["type"] == "module" or (s["type"] and s["type"] not in JS_TYPES) or NON_JS_TYPES.search(s["type"]):
            continue
        blocking.append(s["src"])
    stats["render_blocking_scripts"] = len(blocking)
    for src in blocking[:5]:
        add(f, "medium", "render_blocking_script", f"Render-blocking <script src> in <head> without async/defer: {src[:150]}")
    if len(blocking) > 5:
        add(f, "medium", "render_blocking_script_more", f"{len(blocking) - 5} more render-blocking scripts in <head>",
            samples=[b[:120] for b in blocking[5:15]])
    css = [l for l in p.links if "stylesheet" in _rels(l) and l["_in_head"] and "alternate" not in _rels(l)
           and (l.get("media", "all").strip().lower() in ("", "all", "screen")) and "disabled" not in l]
    stats["blocking_stylesheets"] = len(css)
    if len(css) > MAX_BLOCKING_CSS:
        add(f, "low", "many_blocking_stylesheets", f"{len(css)} render-blocking stylesheets in <head> (> {MAX_BLOCKING_CSS}); "
            "bundle them or inline critical CSS", samples=[l.get("href", "")[:120] for l in css[:8]])

    # ---- fonts
    no_display = 0
    for block in p.styles:
        for m in FONT_FACE_RE.finditer(block):
            if "font-display" not in m.group(1).lower():
                no_display += 1
    if no_display:
        add(f, "low", "font_face_no_display", f"{no_display} inline @font-face rule(s) without font-display "
            "(invisible text while fonts load; use font-display: swap or optional)")
    gf = []
    for l in p.links:
        href = l.get("href", "")
        if "fonts.googleapis.com/css" in href and ("stylesheet" in _rels(l) or l.get("as", "").lower() == "style"):
            gf.append(href)
    for block in p.styles:
        gf += re.findall(r"@import\s+(?:url\()?['\"]?([^'\")\s]*fonts\.googleapis\.com/css[^'\")\s]*)", block, re.I)
    bad_gf = [h for h in gf if "display" not in parse_qs(urlparse(h.replace("&amp;", "&")).query)]
    if bad_gf:
        add(f, "low", "google_fonts_no_display", f"{len(bad_gf)} Google Fonts URL(s) without &display=swap", samples=bad_gf[:5])

    # ---- bfcache
    cc = headers.get("cache-control", "").lower()
    if "no-store" in cc:
        sev = "info" if ca.APP_PATH_HINT.match(path) else "medium"
        add(f, sev, "cache_no_store", "HTML sent with Cache-Control: no-store; this blocks the back/forward cache on "
            "marketing pages (instant back navigation). Use no-cache + ETag or ISR instead")
    unload_inline = [s for s in p.scripts if not s["src"] and isinstance(s["text"], str) and UNLOAD_RE.search(s["text"])]
    if p.unload_attrs or unload_inline:
        add(f, "medium", "unload_handler", f"unload handler found ({len(p.unload_attrs)} onunload attribute(s), "
            f"{len(unload_inline)} inline script(s)); unload makes pages ineligible for bfcache. Use pagehide/visibilitychange")

    # ---- inline JS, DOM size, third parties
    inline_js = sum(s["inline_len"] for s in p.scripts if not s["src"] and not NON_JS_TYPES.search(s["type"])
                    and (s["type"] in JS_TYPES))
    inline_data = sum(s["inline_len"] for s in p.scripts if not s["src"] and NON_JS_TYPES.search(s["type"] or ""))
    stats.update(inline_js_bytes=inline_js, inline_data_bytes=inline_data)
    if inline_js > INLINE_JS_LIMIT:
        add(f, "medium", "large_inline_js", f"{inline_js // 1024} KB of inline JavaScript (> 100 KB); parsed on every load and not cacheable")
    if inline_data > 2 * INLINE_JS_LIMIT:
        add(f, "low", "large_inline_data", f"{inline_data // 1024} KB of inline JSON (hydration/state payload); trim what the page does not need")
    if p.elements > 3000:
        add(f, "medium", "dom_size_large", f"~{p.elements} elements in the HTML (> 3000); large DOMs slow style/layout and INP")
    elif p.elements > 1500:
        add(f, "low", "dom_size", f"~{p.elements} elements in the HTML (> 1500)")
    origins = Counter()
    for s in p.scripts:
        if s["src"]:
            h = (urlparse(urljoin(url, s["src"])).hostname or "").lower()
            if h and _site(h) != _site(page_host):
                origins[h] += 1
    stats["third_party_script_origins"] = sorted(origins)
    if origins:
        sev = "low" if len(origins) > 10 else "info"
        add(f, sev, "third_party_scripts", f"{len(origins)} third-party script origin(s): {', '.join(sorted(origins)[:10])}"
            + (" ..." if len(origins) > 10 else "") + "; defer or remove what marketing pages do not need")

    score = max(0, 100 - sum(WEIGHTS[x["severity"]] for x in f))
    return {"findings": sorted(f, key=lambda x: SEVERITY_ORDER[x["severity"]]), "stats": stats, "hint_score": score}


def run(url: str, ua: str = UA_DEFAULT, timeout: float = 20.0, allow_private: bool = False) -> dict:
    host = urlparse(url).hostname
    ca.set_network_mode(host, allow_private)
    res = ca.fetch(url, ua, timeout=timeout)
    out = {"tool": "hints_check", "version": 1, "url": url, "final_url": res["final_url"], "status": res["status"],
           "origin": f"{urlparse(res['final_url']).scheme}://{urlparse(res['final_url']).netloc}",
           "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "note": "Hints from static HTML/headers only. The hint score is NOT a Core Web Vitals prediction."}
    if res["status"] != 200 or "html" not in res["headers"].get("content-type", "html"):
        out.update(findings=[{"severity": "high", "code": "fetch_error",
                              "message": f"Could not analyse: status {res['status']} {res.get('error') or ''}".strip()}],
                   stats={}, hint_score=None)
    else:
        out.update(analyze(res["body"], res["headers"], res["final_url"]))
        out["stats"]["html_bytes"] = res["bytes"]
        out["stats"]["ttfb_ms"] = res["ttfb_ms"]
    out["summary"] = {"counts": dict(Counter(x["severity"] for x in out["findings"]))}
    return out


def to_markdown(r: dict) -> str:
    c = r["summary"]["counts"]
    s = r.get("stats") or {}
    L = [f"# Performance hints: {r['final_url']}", "",
         f"- Checked: {r['checked_at']} | status {r['status']} | hint score: **{r.get('hint_score')}/100** "
         "(static hints, NOT a Core Web Vitals prediction)",
         "- " + " ".join(f"{k}: {c.get(k, 0)}" for k in SEVERITY_ORDER), ""]
    if s:
        hero = s.get("hero") or {}
        L += ["## Page facts", "",
              f"- Elements: {s.get('elements')} | scripts: {s.get('scripts')} | images: {s.get('images')} | HTML: {s.get('html_bytes')} bytes | TTFB: {s.get('ttfb_ms')} ms",
              f"- Preloads: {s.get('preloads')} | preconnects: {s.get('preconnects')} | render-blocking scripts: {s.get('render_blocking_scripts')} | blocking CSS: {s.get('blocking_stylesheets')}",
              f"- Inline JS: {s.get('inline_js_bytes')} bytes | inline JSON: {s.get('inline_data_bytes')} bytes | speculation rules: {s.get('speculation_rules')}",
              f"- Hero candidate: {hero.get('src') or '-'} ({hero.get('reason', '-')}, fetchpriority={hero.get('fetchpriority')}, loading={hero.get('loading')})",
              f"- Third-party script origins: {', '.join(s.get('third_party_script_origins') or []) or '-'}", ""]
    L += ["## Hints", "", "| Severity | Code | Message |", "|---|---|---|"]
    for x in r["findings"]:
        msg = x["message"].replace("|", "\\|")
        if x.get("samples"):
            msg += " — " + ", ".join(x["samples"][:4]).replace("|", "\\|")
        L.append(f"| {x['severity']} | `{x['code']}` | {msg[:400]} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--allow-private", action="store_true")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on", choices=["critical", "high", "medium"])
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = run(a.url, a.ua, a.timeout, a.allow_private)
    md = to_markdown(r)
    js = json.dumps(r, indent=2, ensure_ascii=False, default=str)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        host = urlparse(r["origin"]).netloc.replace(":", "_")
        (out / f"hints-{host}-{stamp}.json").write_text(js, encoding="utf-8")
        (out / f"hints-{host}-{stamp}.md").write_text(md, encoding="utf-8")
        print(f"[saved] {out / f'hints-{host}-{stamp}.md'}", file=sys.stderr)
    print(js if a.json else md)
    if a.fail_on:
        lim = SEVERITY_ORDER[a.fail_on]
        if any(SEVERITY_ORDER[s] <= lim and n for s, n in r["summary"]["counts"].items()):
            sys.exit(1)


if __name__ == "__main__":
    main()
