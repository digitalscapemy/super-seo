#!/usr/bin/env python3
"""crawl_audit.py - stdlib-only SEO/GEO crawler for SaaS sites.

Works against a local dev/preview server (http://localhost:3000) BEFORE deploy,
or against a live URL. No pip installs. Python 3.10+. Windows/macOS/Linux.

Usage:
  python crawl_audit.py http://localhost:3000 --max-pages 60 --out .seo/audits
  python crawl_audit.py https://example.com --fail-on high      # CI gate (exit 1)
  python crawl_audit.py https://example.com --urls urls.txt      # audit a fixed list

Outputs <out>/crawl-<host>-<timestamp>.json and .md (unless --no-write).
Severity levels: critical > high > medium > low > info.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import ipaddress
import http.client
import json
import re
import socket
import ssl
import sys
import time
import zlib
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib import request, error
from urllib.parse import urljoin, urlparse, urlunparse, urldefrag
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from robots_rfc import Robots  # noqa: E402

UA_DEFAULT = "Mozilla/5.0 (compatible; super-seo-audit/1.0; +https://github.com/)"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SKIP_EXT = re.compile(
    r"\.(png|jpe?g|gif|webp|avif|svg|ico|pdf|zip|gz|mp4|webm|mp3|wav|woff2?|ttf|otf|eot|css|js|mjs|map|json|xml|txt|rss|atom)$",
    re.I,
)
GENERIC_ANCHORS = {"click here", "read more", "learn more", "here", "more", "link", "this page", "details", "view", "continue", "go"}
STOCK_CTA = re.compile(r"(try it free( now)?|start free|learn more( now)?|sign up (today|now)|get started( today| now)?)[.!]?\s*$", re.I)
PLACEHOLDER = re.compile(r"lorem ipsum|dolor sit amet|consectetur adipiscing|\[REPLACE|\[INSERT|\[Your ", re.I)
PLACEHOLDER_LD = re.compile(PLACEHOLDER.pattern + r"|TODO|XXX|example\.com", re.I)
DEPRECATED_SCHEMA = {"HowTo": "removed from Google rich results (2023); use plain H2 steps",
                     "SpecialAnnouncement": "deprecated (2025)", "CourseInfo": "deprecated; use Course",
                     "EstimatedSalary": "deprecated; use JobPosting.baseSalary", "LearningVideo": "deprecated; use VideoObject",
                     "VehicleListing": "deprecated; use Product", "ClaimReview": "no longer shown in Search"}
REQUIRED_PROPS = {  # minimum useful properties per type (Google rich-result requirements or entity basics)
    "Organization": ["name", "url", "logo"], "WebSite": ["name", "url"],
    "SoftwareApplication": ["name", "offers", "applicationCategory"], "WebApplication": ["name", "offers", "applicationCategory"],
    "Article": ["headline", "datePublished", "author", "image"], "BlogPosting": ["headline", "datePublished", "author", "image"],
    "TechArticle": ["headline", "datePublished", "author"], "BreadcrumbList": ["itemListElement"],
    "Product": ["name", "offers"], "FAQPage": ["mainEntity"], "VideoObject": ["name", "thumbnailUrl", "uploadDate"],
}

# Paths that are almost always the authenticated app, not marketing pages.
APP_PATH_HINT = re.compile(r"^/(app|dashboard|admin|account|settings|billing|login|signin|sign-in|signup|sign-up|register|auth|logout|api)(/|$)", re.I)


# --------------------------------------------------------------------------- fetch
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_ctx = ssl.create_default_context()
_opener = request.OpenerDirector()
for _h in (request.HTTPHandler(), request.HTTPSHandler(context=_ctx), NoRedirect(),
           request.HTTPErrorProcessor(), request.HTTPDefaultErrorHandler()):
    _opener.add_handler(_h)  # deliberately no FileHandler/FTPHandler/DataHandler


def _decode_body(raw: bytes, encoding: str | None, max_bytes: int) -> tuple[bytes, bool]:
    """Streaming decompression with an OUTPUT cap (defends against decompression bombs).
    Returns (body, truncated)."""
    enc = (encoding or "").lower()
    if enc not in ("gzip", "deflate"):
        return raw[:max_bytes], len(raw) > max_bytes
    for wbits in ((31,) if enc == "gzip" else (15, -15)):
        try:
            d = zlib.decompressobj(wbits)
            out = d.decompress(raw, max_bytes + 1)
            return out[:max_bytes], len(out) > max_bytes or bool(d.unconsumed_tail)
        except zlib.error:
            continue
    return raw[:max_bytes], len(raw) > max_bytes


def is_private_host(host: str | None) -> bool:
    """True if host is (or resolves to) a loopback/private/link-local/reserved address."""
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # unresolvable: the fetch itself will fail
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


# Set by crawl(): when the audit starts from a PUBLIC host, never follow redirects/sitemaps into private networks.
PUBLIC_ONLY = {"on": False}


def url_allowed(url: str) -> str | None:
    """Return a reason string if the URL must not be fetched, else None."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return f"blocked non-http(s) scheme '{p.scheme}'"
    if PUBLIC_ONLY["on"] and is_private_host(p.hostname):
        return f"blocked private/internal address {p.hostname} (audit started from a public host)"
    return None


def fetch(url: str, ua: str, timeout: float = 20.0, max_redirects: int = 10, accept: str | None = None,
          max_bytes: int = 15 * 1024 * 1024) -> dict:
    """Fetch URL following redirects manually so the chain is recorded."""
    chain = []
    current = url
    started = time.perf_counter()
    for _ in range(max_redirects + 1):
        reason = url_allowed(current)
        if reason:
            return {"url": url, "final_url": current, "status": 0, "error": reason, "chain": chain,
                    "headers": {}, "body": "", "ttfb_ms": None, "total_ms": None, "bytes": 0}
        headers = {
            "User-Agent": ua,
            "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en;q=0.9,*;q=0.5",
        }
        req = request.Request(current, headers=headers, method="GET")
        t0 = time.perf_counter()
        try:
            resp = _opener.open(req, timeout=timeout)
            status, hdrs = resp.status, resp.headers
            ttfb = time.perf_counter() - t0
            raw = resp.read(max_bytes + 1)
        except error.HTTPError as e:  # 3xx (because NoRedirect) and 4xx/5xx land here
            status, hdrs = e.code, e.headers
            ttfb = time.perf_counter() - t0
            try:
                raw = e.read(max_bytes + 1)
            except Exception:
                raw = b""
        except (error.URLError, socket.timeout, ConnectionError, ssl.SSLError, OSError, ValueError, http.client.HTTPException) as e:
            return {"url": url, "final_url": current, "status": 0, "error": str(getattr(e, "reason", e)),
                    "chain": chain, "headers": {}, "body": "", "ttfb_ms": None, "total_ms": None, "bytes": 0}
        if status in (301, 302, 303, 307, 308) and hdrs.get("Location"):
            nxt = urljoin(current, hdrs.get("Location"))
            chain.append({"url": current, "status": status, "to": nxt})
            current = nxt
            continue
        body_bytes, truncated = _decode_body(raw, hdrs.get("Content-Encoding"), max_bytes)
        charset = hdrs.get_content_charset() or "utf-8"
        try:
            body = body_bytes.decode(charset, errors="replace")
        except LookupError:
            body = body_bytes.decode("utf-8", errors="replace")
        return {
            "url": url, "final_url": current, "status": status, "error": None, "chain": chain,
            "headers": {k.lower(): v for k, v in hdrs.items()}, "body": body,
            "ttfb_ms": round(ttfb * 1000), "total_ms": round((time.perf_counter() - started) * 1000),
            "bytes": len(body_bytes), "truncated": truncated or len(raw) > max_bytes,
        }
    return {"url": url, "final_url": current, "status": 0, "error": "too many redirects", "chain": chain,
            "headers": {}, "body": "", "ttfb_ms": None, "total_ms": None, "bytes": 0}


# --------------------------------------------------------------------------- parse
class PageParser(HTMLParser):
    VOID_TEXT_SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self._in_title = False
        self.lang = None
        self.metas: list[dict] = []
        self.links: list[dict] = []
        self.headings: list[tuple[str, str]] = []
        self._heading_tag = None
        self._heading_buf: list[str] = []
        self.images: list[dict] = []
        self.anchors: list[dict] = []
        self._anchor_stack: list[dict] = []
        self.jsonld_raw: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self._skip_depth = 0
        self.text_parts: list[str] = []
        self.script_count = 0
        self.has_main = False
        self.base_href = None
        self.iframes = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or None
        elif tag == "base" and a.get("href"):
            self.base_href = a["href"]
        elif tag == "title" and self.title is None:
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_tag = tag
            self._heading_buf = []
        elif tag == "img":
            self.images.append(a)
        elif tag == "a":
            rec = {"href": a.get("href", ""), "rel": a.get("rel", ""), "text": ""}
            self.anchors.append(rec)
            self._anchor_stack.append(rec)
        elif tag == "main":
            self.has_main = True
        elif tag == "iframe":
            self.iframes += 1
        if tag == "script":
            self.script_count += 1
            if a.get("type", "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []
        if tag in self.VOID_TEXT_SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == self._heading_tag:
            self.headings.append((tag, " ".join("".join(self._heading_buf).split())))
            self._heading_tag = None
        elif tag == "a" and self._anchor_stack:
            rec = self._anchor_stack.pop()
            rec["text"] = " ".join(rec["text"].split())
        if tag == "script" and self._in_jsonld:
            self.jsonld_raw.append("".join(self._jsonld_buf))
            self._in_jsonld = False
        if tag in self.VOID_TEXT_SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        if self._heading_tag:
            self._heading_buf.append(data)
        for rec in self._anchor_stack:
            rec["text"] += data
        self.text_parts.append(data)

    # helpers
    def meta(self, key: str) -> str | None:
        key = key.lower()
        for m in self.metas:
            if m.get("name", "").lower() == key or m.get("property", "").lower() == key:
                return m.get("content")
        return None

    def link_rel(self, rel: str) -> list[dict]:
        out = []
        for l in self.links:
            rels = l.get("rel", "").lower().split()
            if rel in rels:
                out.append(l)
        return out


def normalize(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", p.query, ""))


# --------------------------------------------------------------------------- checks
def add(issues, severity, code, message, **extra):
    issues.append({"severity": severity, "code": code, "message": message, **extra})


def jsonld_types(obj) -> list[str]:
    types = []
    if isinstance(obj, list):
        for o in obj:
            types += jsonld_types(o)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if isinstance(t, list):
            types += [str(x) for x in t]
        elif t:
            types.append(str(t))
        for g in obj.get("@graph", []) or []:
            types += jsonld_types(g)
    return types


def jsonld_nodes(obj) -> list[dict]:
    out = []
    if isinstance(obj, list):
        for o in obj:
            out += jsonld_nodes(o)
    elif isinstance(obj, dict):
        if "@type" in obj:
            out.append(obj)
        for g in obj.get("@graph", []) or []:
            out += jsonld_nodes(g)
    return out


def audit_page(res: dict, start_host: str) -> dict:
    issues: list[dict] = []
    url = res["final_url"]
    path = urlparse(url).path or "/"
    page = {"url": res["url"], "final_url": url, "status": res["status"], "ttfb_ms": res["ttfb_ms"],
            "bytes": res["bytes"], "redirects": res["chain"], "issues": issues}

    if res["status"] == 0:
        add(issues, "critical", "fetch_error", f"Could not fetch: {res['error']}")
        return page
    if len(res["chain"]) >= 2:
        add(issues, "medium", "redirect_chain", f"{len(res['chain'])} redirect hops before final URL",
            chain=[c["status"] for c in res["chain"]])
    for hop in res["chain"]:
        if hop["status"] in (302, 307) and urlparse(hop["to"]).netloc == urlparse(hop["url"]).netloc:
            add(issues, "low", "temporary_redirect", f"{hop['status']} used for {hop['url']} -> {hop['to']}; use 301/308 if permanent")
            break
    title_m = re.search(r"<title[^>]*>([^<]{0,120})", res["body"][:20000], re.I)
    t0 = title_m.group(1).strip().lower() if title_m else ""
    if (res["status"] in (401, 403, 202) or "cf-mitigated" in res["headers"]
            or t0 in ("just a moment...", "attention required! | cloudflare", "one moment, please...", "checking your browser")
            or (res["status"] == 503 and re.search(r"challenge-platform|cf-browser-verification|verifying you are human", res["body"][:20000], re.I))):
        page["blocked"] = True
        add(issues, "high", "blocked_by_waf", f"Fetch was blocked/challenged (status {res['status']}). Could not audit this page; "
            "bots may be blocked too. Check CDN/WAF bot settings, or re-run against a preview/localhost build.")
        return page
    if res["status"] == 429:
        page["blocked"] = True
        add(issues, "medium", "rate_limited", "429 Too Many Requests; crawl slower (--delay) and re-run")
        return page
    if res["status"] >= 500:
        add(issues, "critical", "status_5xx", f"Server error {res['status']}")
        return page
    if res["status"] >= 400:
        add(issues, "high", "status_4xx", f"Client error {res['status']}")
        return page

    ctype = res["headers"].get("content-type", "")
    if "html" not in ctype:
        page["non_html"] = ctype
        return page

    if urlparse(url).scheme != "https" and urlparse(url).hostname not in ("localhost", "127.0.0.1"):
        add(issues, "critical", "no_https", "Page served over plain HTTP")
    if res.get("truncated"):
        add(issues, "high", "html_over_15mb", "HTML exceeds 15 MB; Googlebot stops reading at 15 MB")
    elif res["bytes"] > 1_500_000:
        add(issues, "low", "html_heavy", f"HTML is {res['bytes'] // 1024} KB; large inline payloads slow parsing for bots and users")
    if res["ttfb_ms"] and res["ttfb_ms"] > 800:
        add(issues, "medium", "slow_ttfb", f"TTFB {res['ttfb_ms']} ms (> 800 ms 'good' threshold)")

    p = PageParser()
    try:
        p.feed(res["body"])
    except Exception as e:  # malformed HTML should not kill the crawl
        add(issues, "medium", "html_parse_error", f"HTML parser error: {e}")

    # ---- indexability
    x_robots = res["headers"].get("x-robots-tag", "")
    meta_robots = " ".join(filter(None, [p.meta("robots"), p.meta("googlebot")])).lower()
    noindex = "noindex" in meta_robots or "noindex" in x_robots.lower()
    page["noindex"] = noindex
    page["nofollow"] = "nofollow" in meta_robots
    if noindex:
        sev = "info" if APP_PATH_HINT.match(path) else "critical"
        add(issues, sev, "noindex", f"Page is noindex (meta='{meta_robots or '-'}', header='{x_robots or '-'}')")
    if "nosnippet" in meta_robots or "max-snippet:0" in meta_robots.replace(" ", ""):
        add(issues, "high", "nosnippet", "nosnippet / max-snippet:0 blocks Google snippets AND AI Overviews/AI Mode use of this page")

    canon = p.link_rel("canonical")
    canonical = urljoin(url, canon[0].get("href", "")) if canon else None
    hdr_canon = re.search(r'<([^>]+)>\s*;\s*rel="?canonical"?', res["headers"].get("link", ""), re.I)
    if hdr_canon:
        hc = urljoin(url, hdr_canon.group(1))
        if canonical and normalize(hc) != normalize(canonical):
            add(issues, "high", "canonical_conflict", f"HTML canonical {canonical} differs from HTTP Link canonical {hc}")
        canonical = canonical or hc
    page["canonical"] = canonical
    if len(canon) > 1:
        add(issues, "high", "multiple_canonicals", f"{len(canon)} rel=canonical tags; Google may ignore all of them")
    if not canon and not noindex:
        add(issues, "medium", "missing_canonical", "No rel=canonical (self-referencing canonical recommended)")
    elif canonical and normalize(canonical) != normalize(url):
        add(issues, "info", "canonicalized_elsewhere", f"Canonical points to {canonical}")
        if urlparse(canonical).netloc and urlparse(canonical).netloc != urlparse(url).netloc:
            add(issues, "medium", "cross_host_canonical", "Canonical points to another host (check www/non-www, staging/prod)")
    page["is_indexable"] = not noindex and (not canonical or normalize(canonical) == normalize(url))

    # ---- title / description
    title = " ".join((p.title or "").split())
    page["title"] = title
    if not title:
        add(issues, "high", "missing_title", "Missing <title>")
    else:
        if len(title) > 60:
            add(issues, "low", "title_long", f"Title {len(title)} chars (>60 likely truncated in SERP)")
        elif len(title) < 25:
            add(issues, "low", "title_short", f"Title {len(title)} chars (<25; probably under-describes the page)")
    desc = " ".join((p.meta("description") or "").split())
    page["description"] = desc
    if not desc:
        add(issues, "medium", "missing_description", "Missing meta description")
    elif len(desc) > 160:
        add(issues, "low", "description_long", f"Meta description {len(desc)} chars (>160 likely truncated)")
    elif len(desc) < 70:
        add(issues, "low", "description_short", f"Meta description {len(desc)} chars (<70)")

    if title and desc:
        t_core = re.split(r"\s[|\-–—]\s", title)[0].strip().lower()
        if t_core and len(t_core) > 12 and desc.lower().startswith(t_core):
            sev = "high" if STOCK_CTA.search(desc) else "medium"
            add(issues, sev, "templated_metadata", "Meta description opens by repeating the title"
                + (" and ends in a stock CTA (bulk-template signal)" if sev == "high" else ""))
        if desc.lower() == title.lower():
            add(issues, "medium", "description_duplicates_title", "Meta description is identical to the title")
    page["desc_cta"] = (STOCK_CTA.search(desc).group(0).lower().strip(" .!") if desc and STOCK_CTA.search(desc) else None)
    if any((m.get("http-equiv", "").lower() == "refresh") for m in p.metas):
        add(issues, "medium", "meta_refresh", "<meta http-equiv=refresh> redirect; use a server-side 301/308")

    # ---- document basics
    if not p.lang:
        add(issues, "medium", "missing_lang", "<html> has no lang attribute")
    if not p.meta("viewport"):
        add(issues, "high", "missing_viewport", "No meta viewport (mobile-first indexing)")
    if not any("icon" in l.get("rel", "").lower() for l in p.links):
        add(issues, "low", "missing_favicon_link", "No <link rel=icon> (favicon shows in Google results and AI citations)")

    # ---- headings
    h1s = [t for tag, t in p.headings if tag == "h1"]
    page["h1"] = h1s
    if not h1s:
        add(issues, "medium", "missing_h1", "No <h1>")
    elif len(h1s) > 1:
        add(issues, "low", "multiple_h1", f"{len(h1s)} <h1> tags")
    if any(not t for t in h1s):
        add(issues, "medium", "empty_h1", "Empty <h1> (often an icon/logo-only heading)")
    levels = [int(tag[1]) for tag, _ in p.headings]
    for a, b in zip(levels, levels[1:]):
        if b - a > 1:
            add(issues, "low", "heading_skip", f"Heading level jumps from h{a} to h{b}")
            break
    page["headings_outline"] = [f"{tag}: {t[:80]}" for tag, t in p.headings[:40]]

    # ---- content / rendering
    text = " ".join(" ".join(p.text_parts).split())
    words = len(re.findall(r"\w+", text))
    page["word_count"] = words
    page["text_hash"] = hashlib.sha1(text.lower().encode("utf-8")).hexdigest()[:16] if words > 30 else None
    if words < 60 and p.script_count >= 3:
        add(issues, "critical", "csr_shell",
            f"Only {words} words in raw HTML with {p.script_count} scripts: content is client-rendered. "
            "Most AI crawlers (GPTBot, ClaudeBot, PerplexityBot) do NOT execute JavaScript; use SSR/SSG/prerender")
    elif words < 250 and not APP_PATH_HINT.match(path) and not noindex:
        add(issues, "low", "thin_content", f"{words} words of server-rendered text; thin for a ranking/citable page")
    if PLACEHOLDER.search(text):
        add(issues, "high", "placeholder_text", f"Placeholder text on page: '{PLACEHOLDER.search(text).group(0)}'")
    if not p.has_main:
        add(issues, "low", "no_main_landmark", "No <main> element (helps agents/readers isolate primary content)")

    # ---- social / preview (also used by AI chat link previews)
    og = {k: p.meta(f"og:{k}") for k in ("title", "description", "image", "url", "type")}
    page["og"] = og
    missing_og = [k for k in ("title", "description", "image") if not og[k]]
    if missing_og:
        add(issues, "medium", "missing_og", f"Missing Open Graph: {', '.join('og:' + m for m in missing_og)}")
    if og["image"] and og["image"].startswith("/"):
        add(issues, "medium", "og_image_relative", "og:image is a relative URL; must be absolute")
    if not p.meta("twitter:card"):
        add(issues, "low", "missing_twitter_card", "No twitter:card meta")

    # ---- structured data
    types, ld_errors = [], 0
    for raw in p.jsonld_raw:
        try:
            data = json.loads(raw)
            types += jsonld_types(data)
            if PLACEHOLDER_LD.search(raw):
                add(issues, "high", "jsonld_placeholder", "JSON-LD contains placeholder values ([REPLACE], lorem ipsum...)")
            for node in jsonld_nodes(data):
                t = node.get("@type")
                for tt in (t if isinstance(t, list) else [t]):
                    missing = [k for k in REQUIRED_PROPS.get(str(tt), []) if not node.get(k)]
                    if missing:
                        add(issues, "medium", "jsonld_missing_props", f"{tt} JSON-LD missing: {', '.join(missing)}")
                    if str(tt) in ("SoftwareApplication", "WebApplication") and not (node.get("aggregateRating") or node.get("review")):
                        add(issues, "info", "software_no_rating", f"{tt} without aggregateRating/review is not eligible for the software rich result; add ONLY real ratings")
                    if str(tt) in ("Article", "BlogPosting") and isinstance(node.get("author"), str):
                        add(issues, "low", "jsonld_author_string", "Article author is a string; use a Person/Organization object with name + url")
        except json.JSONDecodeError as e:
            ld_errors += 1
            add(issues, "high", "jsonld_invalid", f"JSON-LD block does not parse: {e.msg} (line {e.lineno})")
    page["schema_types"] = sorted(set(types))
    if not p.jsonld_raw and not noindex:
        add(issues, "medium", "no_structured_data", "No JSON-LD structured data")
    for t in sorted(set(types)):
        if t in DEPRECATED_SCHEMA:
            add(issues, "low", "schema_deprecated", f"{t}: {DEPRECATED_SCHEMA[t]}")
        if t == "FAQPage":
            add(issues, "info", "schema_faqpage", "FAQPage: no FAQ rich result for most sites; fine to keep for visible Q&A, don't add it expecting SERP/AI gains")

    # ---- images
    no_alt = [i.get("src", "")[:120] for i in p.images if "alt" not in i]
    no_dims = [i.get("src", "")[:120] for i in p.images if not (i.get("width") and i.get("height"))]
    page["images"] = len(p.images)
    if p.images and p.images[0].get("loading", "").lower() == "lazy":
        add(issues, "medium", "first_image_lazy", "First <img> is loading=lazy; if it is the hero/LCP image this delays LCP (use fetchpriority=high instead)")
    if no_alt:
        add(issues, "medium", "img_missing_alt", f"{len(no_alt)} <img> without alt attribute", samples=no_alt[:5])
    if len(no_dims) >= 3:
        add(issues, "low", "img_missing_dimensions", f"{len(no_dims)} <img> without width/height (CLS risk)", samples=no_dims[:5])

    # ---- hreflang
    alts = [l for l in p.link_rel("alternate") if l.get("hreflang")]
    page["hreflang"] = [l.get("hreflang") for l in alts]
    if alts:
        langs = [l.get("hreflang", "").lower() for l in alts]
        if not any(normalize(urljoin(url, l.get("href", ""))) == normalize(url) for l in alts):
            add(issues, "medium", "hreflang_no_self", "hreflang set does not include a self-reference")
        if "x-default" not in langs:
            add(issues, "low", "hreflang_no_xdefault", "hreflang set has no x-default")
        bad = [x for x in langs if x != "x-default" and not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,4})?", x)]
        if bad:
            add(issues, "high", "hreflang_invalid_code", f"Invalid hreflang codes: {bad}")

    # ---- links
    internal, external = [], []
    base = urljoin(url, p.base_href) if p.base_href else url
    empty_anchor = generic_anchor = 0
    for a in p.anchors:
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            if href.startswith("javascript:"):
                add(issues, "low", "js_link", "Link uses javascript: href (not crawlable)")
            continue
        absu = normalize(urljoin(base, href))
        if not a["text"]:
            empty_anchor += 1
        elif a["text"].strip().lower() in GENERIC_ANCHORS:
            generic_anchor += 1
        (internal if urlparse(absu).hostname == start_host else external).append(absu)
    page["internal_links"] = sorted(set(internal))
    page["external_links_count"] = len(set(external))
    if empty_anchor >= 3:
        add(issues, "low", "empty_anchor_text", f"{empty_anchor} links without text (icon links need aria-label)")
    if generic_anchor >= 3:
        add(issues, "low", "generic_anchor_text", f"{generic_anchor} links use generic text ('learn more', 'click here'); describe the target")
    if not internal and not noindex:
        add(issues, "medium", "no_internal_links", "Page has no crawlable internal <a href> links")

    return page


# --------------------------------------------------------------------------- site
def read_robots(origin: str, ua: str) -> dict:
    r = fetch(origin + "/robots.txt", ua, accept="text/plain,*/*")
    out = {"status": r["status"], "sitemaps": [], "text": "", "parser": None}
    if r["status"] == 200 and "html" not in r["headers"].get("content-type", ""):
        out["text"] = r["body"][:200_000]
        rp = Robots(out["text"])
        out["parser"] = rp
        out["sitemaps"] = rp.sitemaps
    return out


def read_sitemap_urls(sm_url: str, ua: str, limit: int = 5000, depth: int = 0) -> tuple[list[str], list[str]]:
    """Return (urls, problems). Follows sitemap indexes one level deep."""
    problems = []
    r = fetch(sm_url, ua, accept="application/xml,text/xml,*/*")
    if r["status"] != 200:
        return [], [f"{sm_url} returned {r['status'] or r['error']}"]
    if re.search(r"<!(DOCTYPE|ENTITY)", r["body"][:5000], re.I):
        # Sitemaps never need a DTD; refusing it blocks XXE / entity-expansion payloads.
        return [], [f"{sm_url} contains a DOCTYPE/ENTITY declaration; refused to parse"]
    try:
        root = ET.fromstring(r["body"].encode("utf-8", errors="replace"))
    except ET.ParseError as e:
        return [], [f"{sm_url} is not valid XML: {e}"]
    tag = root.tag.split("}")[-1]
    locs = [el.text.strip() for el in root.iter() if el.tag.split("}")[-1] == "loc" and el.text]
    if tag == "sitemapindex" and depth == 0:
        urls = []
        parent_host = (urlparse(sm_url).hostname or "").removeprefix("www.")
        for child in locs[:50]:
            ch = (urlparse(child).hostname or "").removeprefix("www.")
            if ch != parent_host and not ch.endswith("." + parent_host):
                problems.append(f"skipped cross-host child sitemap {child}")
                continue
            u, pr = read_sitemap_urls(child, ua, limit - len(urls), depth + 1)
            urls += u
            problems += pr
            if len(urls) >= limit:
                break
        return urls[:limit], problems
    if len(locs) > 50000:
        problems.append(f"{sm_url} has {len(locs)} URLs (> 50,000 limit per sitemap file)")
    return locs[:limit], problems


def crawl(start: str, max_pages: int, ua: str, delay: float, fixed_urls: list[str] | None, respect_robots: bool) -> dict:
    start = normalize(start)
    sp = urlparse(start)
    origin = f"{sp.scheme}://{sp.netloc}"
    host = sp.hostname
    PUBLIC_ONLY["on"] = not is_private_host(host)
    site_issues: list[dict] = []

    robots = read_robots(origin, ua)
    if robots["status"] == 404:
        add(site_issues, "medium", "robots_missing", "/robots.txt returns 404 (everything allowed, but no Sitemap: hint)")
    elif robots["status"] not in (200,):
        add(site_issues, "high", "robots_error", f"/robots.txt returned {robots['status']}; 5xx makes Google treat the site as fully disallowed")
    if robots["parser"] and not robots["parser"].can_fetch("Googlebot", start):
        add(site_issues, "critical", "robots_blocks_start", "robots.txt disallows Googlebot from the start URL")

    sitemap_candidates = robots["sitemaps"] or [origin + "/sitemap.xml"]
    sitemap_urls, sm_problems = [], []
    for sm in sitemap_candidates[:5]:
        u, pr = read_sitemap_urls(sm, ua)
        sitemap_urls += u
        sm_problems += pr
    if not sitemap_urls:
        add(site_issues, "high", "sitemap_missing", "No sitemap URLs found (robots Sitemap: line or /sitemap.xml)", details=sm_problems[:5])
    else:
        for pr in sm_problems[:10]:
            add(site_issues, "medium", "sitemap_problem", pr)
        if not robots["sitemaps"]:
            add(site_issues, "low", "sitemap_not_in_robots", "Sitemap not declared in robots.txt")
        foreign = [u for u in sitemap_urls if urlparse(u).hostname != host]
        if foreign:
            add(site_issues, "high", "sitemap_foreign_host", f"{len(foreign)} sitemap URLs on another host (e.g. {foreign[0]}); staging/localhost leaking into prod sitemap?")
    sitemap_set = {normalize(u) for u in sitemap_urls}

    # ---- BFS
    queue: deque[str] = deque()
    seen: set[str] = set()
    if fixed_urls:
        for u in fixed_urls:
            queue.append(normalize(u))
    else:
        queue.append(start)
        for u in list(sitemap_set)[: max_pages]:
            if urlparse(u).hostname == host:
                queue.append(u)
    pages = []
    inlinks: dict[str, int] = defaultdict(int)
    while queue and len(pages) < max_pages:
        u = queue.popleft()
        if u in seen:
            continue
        seen.add(u)
        if SKIP_EXT.search(urlparse(u).path):
            continue
        if respect_robots and robots["parser"] and not robots["parser"].can_fetch(ua, u) and u != start:
            continue
        res = fetch(u, ua)
        final_norm = normalize(res["final_url"])
        if final_norm != u and final_norm in seen:
            continue  # redirect into a page already audited
        seen.add(final_norm)
        page = audit_page(res, host)
        page["in_sitemap"] = u in sitemap_set or normalize(res["final_url"]) in sitemap_set
        pages.append(page)
        for link in page.get("internal_links", []):
            inlinks[link] += 1
            if not fixed_urls and link not in seen and not APP_PATH_HINT.match(urlparse(link).path):
                queue.append(link)
        if delay:
            time.sleep(delay)

    # ---- cross-page checks
    by_title, by_desc = defaultdict(list), defaultdict(list)
    for pg in pages:
        if pg.get("is_indexable") and pg.get("title"):
            by_title[pg["title"].lower()].append(pg["final_url"])
        if pg.get("is_indexable") and pg.get("description"):
            by_desc[pg["description"].lower()].append(pg["final_url"])
        if pg.get("in_sitemap"):
            if pg["status"] != 200 or pg["redirects"]:
                add(pg["issues"], "high", "sitemap_non_200", "URL is in sitemap but is not a direct 200")
            if pg.get("noindex"):
                add(pg["issues"], "high", "sitemap_noindex", "URL is in sitemap but noindex")
            elif pg.get("canonical") and normalize(pg["canonical"]) != normalize(pg["final_url"]):
                add(pg["issues"], "medium", "sitemap_non_canonical", "URL is in sitemap but canonicalizes elsewhere")
        if APP_PATH_HINT.match(urlparse(pg["final_url"]).path) and pg.get("status") == 200 and not pg.get("noindex") and pg.get("in_sitemap"):
            add(pg["issues"], "medium", "app_route_in_sitemap", "App/auth route is listed in the sitemap")
    by_hash = defaultdict(list)
    status_of = {normalize(pg["url"]): pg["status"] for pg in pages}
    status_of.update({normalize(pg["final_url"]): pg["status"] for pg in pages})
    for pg in pages:
        if pg.get("is_indexable") and pg.get("text_hash"):
            by_hash[pg["text_hash"]].append(pg["final_url"])
        broken = sorted({l for l in pg.get("internal_links", []) if status_of.get(l, 200) >= 400})
        if broken:
            add(pg["issues"], "high", "broken_internal_links", f"{len(broken)} internal links to 4xx/5xx pages", samples=broken[:5])
    for h, urls in by_hash.items():
        if len(urls) > 1:
            add(site_issues, "medium", "duplicate_content", f"{len(urls)} indexable pages have identical visible text", urls=urls[:10])
    cta_counts = Counter(pg.get("desc_cta") for pg in pages if pg.get("desc_cta"))
    for cta, n in cta_counts.items():
        if n >= 3 and n >= 0.3 * len(pages):
            add(site_issues, "medium", "shared_cta_descriptions", f"{n} meta descriptions end with the same CTA '{cta}' (templated metadata)")
    for t, urls in by_title.items():
        if len(urls) > 1:
            add(site_issues, "medium", "duplicate_title", f"{len(urls)} indexable pages share the title '{t[:70]}'", urls=urls[:10])
    for d, urls in by_desc.items():
        if len(urls) > 1:
            add(site_issues, "low", "duplicate_description", f"{len(urls)} pages share a meta description", urls=urls[:10])
    crawled = {normalize(p["final_url"]) for p in pages}
    orphans = [u for u in sitemap_set if u in crawled and inlinks.get(u, 0) == 0 and normalize(u) != start]
    exhausted = not queue
    if orphans and exhausted:
        add(site_issues, "medium", "orphan_pages", f"{len(orphans)} sitemap pages receive no internal links from crawled pages", urls=orphans[:15])
    not_in_sm = [p["final_url"] for p in pages if p.get("is_indexable") and p["status"] == 200 and not p.get("in_sitemap") and not p.get("non_html")]
    if sitemap_set and not_in_sm:
        add(site_issues, "low", "indexable_not_in_sitemap", f"{len(not_in_sm)} indexable pages are missing from the sitemap", urls=not_in_sm[:15])

    return {
        "tool": "crawl_audit", "version": 1, "start": start, "origin": origin,
        "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "robots": {"status": robots["status"], "sitemaps": robots["sitemaps"]},
        "sitemap_url_count": len(sitemap_set), "pages_crawled": len(pages), "crawl_complete": exhausted,
        "site_issues": site_issues, "pages": pages,
    }


# --------------------------------------------------------------------------- report
def summarize(result: dict) -> dict:
    counts = Counter()
    by_code: dict[str, dict] = {}
    for iss in result["site_issues"]:
        counts[iss["severity"]] += 1
        by_code.setdefault(iss["code"], {"severity": iss["severity"], "message": iss["message"], "pages": []})
    for pg in result["pages"]:
        for iss in pg["issues"]:
            counts[iss["severity"]] += 1
            e = by_code.setdefault(iss["code"], {"severity": iss["severity"], "message": iss["message"], "pages": []})
            e["pages"].append(pg["final_url"])
    # simple health score: start 100, subtract weighted unique-issue impact (capped per code)
    weights = {"critical": 15, "high": 7, "medium": 3, "low": 1, "info": 0}
    n = max(1, len(result["pages"]))
    penalty = 0.0
    for code, e in by_code.items():
        share = min(1.0, max(1, len(e["pages"])) / n)
        penalty += weights[e["severity"]] * (0.5 + 0.5 * share)
    score = max(0, round(100 - penalty))
    return {"counts": dict(counts), "by_code": by_code, "technical_score": score}


def to_markdown(result: dict, summary: dict) -> str:
    L = [f"# Crawl audit: {result['origin']}", "",
         f"- Crawled: {result['crawled_at']} | pages: {result['pages_crawled']} | sitemap URLs: {result['sitemap_url_count']}",
         f"- robots.txt: {result['robots']['status']} | sitemaps declared: {len(result['robots']['sitemaps'])}",
         f"- **Technical score: {summary['technical_score']}/100** | " +
         " ".join(f"{k}: {summary['counts'].get(k, 0)}" for k in SEVERITY_ORDER), "",
         "## Issues by type (worst first)", "",
         "| Severity | Code | Pages | Example message |", "|---|---|---|---|"]
    for code, e in sorted(summary["by_code"].items(), key=lambda kv: (SEVERITY_ORDER[kv[1]["severity"]], -len(kv[1]["pages"]))):
        if e["severity"] == "info":
            continue
        msg = e["message"].replace("|", "\\|")[:140]
        L.append(f"| {e['severity']} | `{code}` | {len(e['pages']) or 'site'} | {msg} |")
    L += ["", "## Pages", "", "| URL | Status | Idx | Words | Title len | Schema | Issues (crit/high) |", "|---|---|---|---|---|---|---|"]
    for pg in result["pages"]:
        c = Counter(i["severity"] for i in pg["issues"])
        L.append(f"| {pg['final_url']} | {pg['status']} | {'Y' if pg.get('is_indexable') else 'N'} | {pg.get('word_count', '-')} | "
                 f"{len(pg.get('title') or '')} | {', '.join(pg.get('schema_types', []))[:40]} | {c['critical']}/{c['high']} |")
    L += ["", "## Site-level details", ""]
    for iss in result["site_issues"]:
        L.append(f"- **{iss['severity']}** `{iss['code']}`: {iss['message']}")
        for u in iss.get("urls", [])[:5]:
            L.append(f"  - {u}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--urls", help="file with one URL per line (audit only these)")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between requests")
    ap.add_argument("--ignore-robots", action="store_true", help="crawl paths robots.txt disallows (own site only)")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true", help="print JSON to stdout instead of markdown")
    ap.add_argument("--fail-on", choices=["critical", "high", "medium"], help="exit 1 if any issue at/above this severity (CI gate)")
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fixed = None
    if a.urls:
        fixed = [l.strip() for l in Path(a.urls).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    result = crawl(a.url, a.max_pages, a.ua, a.delay, fixed, not a.ignore_robots)
    summary = summarize(result)
    result["summary"] = {"counts": summary["counts"], "technical_score": summary["technical_score"]}
    md = to_markdown(result, summary)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        host = urlparse(result["origin"]).netloc.replace(":", "_")
        (out / f"crawl-{host}-{stamp}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"crawl-{host}-{stamp}.md").write_text(md, encoding="utf-8")
        print(f"[saved] {out / f'crawl-{host}-{stamp}.md'}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else md)
    if a.fail_on:
        limit = SEVERITY_ORDER[a.fail_on]
        if any(SEVERITY_ORDER[s] <= limit and n for s, n in summary["counts"].items()):
            sys.exit(1)


if __name__ == "__main__":
    main()
