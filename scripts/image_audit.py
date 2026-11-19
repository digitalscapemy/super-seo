#!/usr/bin/env python3
"""image_audit.py - stdlib-only image SEO + performance audit for one page, a small crawl, or a URL list.

For every <img>, <picture><source> and inline-style CSS background image it resolves the URL, fetches the image
(with Accept: image/avif,image/webp,*/* and a 5 MB cap; larger files are measured from Content-Length and never
downloaded) and checks:
  - served format by Content-Type (not by extension) plus magic-byte sniffing; legacy JPEG/PNG/GIF where AVIF/WebP was accepted
  - byte budgets: hero < 200 KB (high above 700 KB), content < 100 KB (high above 500 KB), thumbnails < 50 KB
  - intrinsic dimensions sniffed from the first bytes (PNG, JPEG, GIF, WebP, AVIF) vs the width attribute (> 2x = oversized)
  - width/height attributes (CLS), alt present / quality (alt == filename, > 125 chars, repeated keywords, empty alt on
    large content images), loading=lazy on the hero (bad), fetchpriority=high on the hero (good), srcset/sizes on large
    images, decoding=async on non-critical images
Then prints a per-image table and the top estimated savings.

Usage:
  python image_audit.py https://example.com/
  python image_audit.py https://example.com/ --max-pages 5          # same-host crawl from the start URL
  python image_audit.py https://example.com/ --urls urls.txt        # audit only these pages
  python image_audit.py http://localhost:3000/pricing --json --no-write

Outputs <out>/images-<host>-<timestamp>.json and .md (default --out .seo/audits) unless --no-write.
Savings are estimates (labelled as such): format conversion uses a typical 30% saving, resizing scales with pixel area.
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import ssl
import struct
import sys
import time
from collections import Counter, deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib import error, request
from urllib.parse import unquote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402
from crawl_audit import (  # noqa: E402
    UA_DEFAULT, SEVERITY_ORDER, PageParser, fetch, normalize, set_network_mode, url_allowed, read_robots, SKIP_EXT,
)

IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
SNIFF_BYTES = 128 * 1024
KB = 1024
BUDGETS = {  # role: (budget, high threshold)
    "hero": (200 * KB, 700 * KB),
    "content": (100 * KB, 500 * KB),
    "background": (100 * KB, 500 * KB),
    "thumbnail": (50 * KB, 200 * KB),
}
LEGACY = {"image/jpeg", "image/png", "image/gif", "image/bmp", "image/x-ms-bmp", "image/tiff"}
MODERN = {"image/webp", "image/avif"}
ICONISH = re.compile(r"logo|icon|avatar|sprite|badge|emoji|favicon|spinner|loader|pixel|tracking", re.I)
THUMBISH = re.compile(r"thumb|avatar|icon|logo|badge", re.I)
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
STOPWORDS = set("a an and the of for to in on with by at or from is are this that your our best".split())


# --------------------------------------------------------------------------- HTML parsing
class ImageParser(HTMLParser):
    """Collects <img>, <picture><source>, inline-style CSS background images and image preloads, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.preloads: list[dict] = []
        self._picture: list[dict] = []
        self._noscript = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "noscript":
            self._noscript += 1
            return
        if self._noscript:
            return  # <noscript> fallbacks duplicate real images
        if tag == "picture":
            self._picture.append({"sources": []})
        elif tag == "source" and self._picture:
            self._picture[-1]["sources"].append({"srcset": a.get("srcset", ""), "type": a.get("type", ""),
                                                 "media": a.get("media", ""), "sizes": a.get("sizes", "")})
        elif tag == "img":
            item = {"kind": "img", "attrs": a, "picture_sources": list(self._picture[-1]["sources"]) if self._picture else []}
            self.items.append(item)
        elif tag == "link" and "preload" in a.get("rel", "").lower().split() and a.get("as", "").lower() == "image":
            self.preloads.append(a)
        style = a.get("style", "")
        if style and "url(" in style.lower() and re.search(r"background", style, re.I):
            for m in CSS_URL.finditer(style):
                self.items.append({"kind": "css-background", "attrs": {"src": m.group(2).strip(), "style": style,
                                                                       "tag": tag}, "picture_sources": []})

    def handle_endtag(self, tag):
        if tag == "noscript" and self._noscript:
            self._noscript -= 1
        elif tag == "picture" and self._picture:
            self._picture.pop()


def best_src(attrs: dict) -> str:
    """The URL a browser (or our fetch) would load for the fallback: src, then lazy-load data attributes, then srcset."""
    for k in ("src", "data-src", "data-lazy-src", "data-original"):
        v = (attrs.get(k) or "").strip()
        if v and not v.startswith("data:"):
            return v
    ss = attrs.get("srcset") or attrs.get("data-srcset") or ""
    cands = parse_srcset(ss)
    return cands[-1][0] if cands else (attrs.get("src") or "").strip()


def parse_srcset(srcset: str) -> list[tuple[str, str]]:
    out = []
    for part in re.split(r",\s+(?=\S)", srcset.strip()):
        bits = part.strip().split()
        if bits:
            out.append((bits[0], bits[1] if len(bits) > 1 else "1x"))
    return out


def int_attr(v) -> int | None:
    m = re.match(r"^\s*(\d+)(?:\.\d+)?\s*(px)?\s*$", str(v or ""))
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- image fetch (binary-safe, capped)
def fetch_image(url: str, ua: str = UA_DEFAULT, timeout: float = 20.0, cap: int = MAX_IMAGE_BYTES) -> dict:
    """Binary-safe image fetch on crawl_audit's guarded opener (same private-network protection as fetch()).
    crawl_audit.fetch() decodes bodies to text, so it cannot return image bytes; this keeps the same safety rules,
    reads at most SNIFF_BYTES when Content-Length is known, and never downloads more than `cap` bytes."""
    current, hops = url, 0
    for _ in range(6):
        reason = url_allowed(current)
        if reason:
            return {"url": url, "final_url": current, "status": 0, "error": reason, "redirects": hops}
        req = request.Request(current, method="GET", headers={
            "User-Agent": ua, "Accept": IMAGE_ACCEPT, "Accept-Encoding": "identity"})
        try:
            resp = ca._opener.open(req, timeout=timeout)
            status, hdrs = resp.status, resp.headers
        except error.HTTPError as e:
            status, hdrs, resp = e.code, e.headers, e
        except (error.URLError, socket.timeout, ConnectionError, ssl.SSLError, OSError, ValueError,
                http.client.HTTPException) as e:
            return {"url": url, "final_url": current, "status": 0, "error": str(getattr(e, "reason", e)), "redirects": hops}
        if status in (301, 302, 303, 307, 308) and hdrs.get("Location"):
            current = urljoin(current, hdrs.get("Location"))
            hops += 1
            continue
        ctype = (hdrs.get("Content-Type") or "").split(";")[0].strip().lower()
        clen = hdrs.get("Content-Length")
        clen = int(clen) if clen and clen.isdigit() else None
        over_cap = False
        try:
            if status != 200:
                head, size = b"", None
            elif clen is not None and not hdrs.get("Content-Encoding"):
                head, size = resp.read(min(SNIFF_BYTES, clen)), clen
                over_cap = clen > cap
            else:
                head = resp.read(cap + 1)
                over_cap = len(head) > cap
                size = None if over_cap else len(head)
                head = head[:SNIFF_BYTES]
        except Exception as e:  # noqa: BLE001
            return {"url": url, "final_url": current, "status": 0, "error": f"read failed: {e}", "redirects": hops}
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        return {"url": url, "final_url": current, "status": status, "error": None, "content_type": ctype,
                "bytes": size, "over_cap": over_cap, "head": head, "redirects": hops,
                "vary": (hdrs.get("Vary") or ""), "cache_control": hdrs.get("Cache-Control") or ""}
    return {"url": url, "final_url": current, "status": 0, "error": "too many redirects", "redirects": hops}


# --------------------------------------------------------------------------- sniffing
def sniff(head: bytes) -> tuple[str | None, tuple[int, int] | None]:
    """Return (format, (width, height)) from the first bytes. Formats: png, jpeg, gif, webp, avif, svg, ico, bmp."""
    if not head:
        return None, None
    try:
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return "png", struct.unpack(">II", head[16:24])
        if head[:6] in (b"GIF87a", b"GIF89a"):
            return "gif", struct.unpack("<HH", head[6:10])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            chunk = head[12:16]
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return "webp", (w & 0x3FFF, h & 0x3FFF)
            if chunk == b"VP8L":
                b0, b1, b2, b3 = head[21:25]
                return "webp", (1 + (((b1 & 0x3F) << 8) | b0), 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6)))
            if chunk == b"VP8X":
                return "webp", (1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little"))
            return "webp", None
        if head[4:8] == b"ftyp" and (b"avif" in head[8:64] or b"avis" in head[8:64]):
            dims = None
            i = head.find(b"ispe")
            while i != -1 and i + 16 <= len(head):
                w, h = struct.unpack(">II", head[i + 8:i + 16])
                if not dims or w * h > dims[0] * dims[1]:
                    dims = (w, h)
                i = head.find(b"ispe", i + 4)
            return "avif", dims
        if head[:2] == b"\xff\xd8":
            return "jpeg", _jpeg_dims(head)
        if head[:4] == b"\x00\x00\x01\x00":
            return "ico", None
        if head[:2] == b"BM" and len(head) >= 26:
            w, h = struct.unpack("<ii", head[18:26])
            return "bmp", (abs(w), abs(h))
        txt = head[:1024].lstrip().lower()
        if txt.startswith(b"<svg") or (txt.startswith(b"<?xml") and b"<svg" in txt):
            return "svg", None
    except struct.error:
        return None, None
    return None, None


def _jpeg_dims(d: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(d)
    while i + 9 < n:
        if d[i] != 0xFF:
            i += 1
            continue
        marker = d[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", d[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", d[i + 5:i + 9])
            return w, h
        i += 2 + seglen
    return None


FORMAT_CT = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "avif": "image/avif",
             "svg": "image/svg+xml", "ico": "image/x-icon", "bmp": "image/bmp"}


# --------------------------------------------------------------------------- checks
def add(issues, severity, code, message, **extra):
    issues.append({"severity": severity, "code": code, "message": message, **extra})


def alt_problems(alt: str, src: str) -> list[tuple[str, str, str]]:
    out = []
    a = " ".join(alt.split())
    base = unquote(Path(urlparse(src).path).name)
    stem = re.sub(r"\.[a-z0-9]{2,5}$", "", base, flags=re.I)
    norm = lambda s: re.sub(r"[\W_]+", " ", s).strip().lower()  # noqa: E731
    if a and (norm(a) == norm(stem) or norm(a) == norm(base) or re.search(r"\.(jpe?g|png|gif|webp|avif|svg)\b", a, re.I)
              or re.fullmatch(r"(img|image|dsc|pxl|photo|screenshot)[\s_-]*\d[\w-]*", a, re.I)):
        out.append(("medium", "alt_is_filename", f"alt text looks like a file name ('{a[:60]}'); describe the image"))
    if len(a) > 125:
        out.append(("low", "alt_too_long", f"alt text is {len(a)} chars (> 125); screen readers read it in full, keep it concise"))
    words = [w for w in re.findall(r"[a-zA-ZÀ-￿]{3,}", a.lower()) if w not in STOPWORDS]
    if words:
        top, n = Counter(words).most_common(1)[0]
        commas = a.count(",") + a.count("|")
        if n >= 3 or (n >= 2 and len(words) <= 6) or commas >= 4:
            out.append(("medium", "alt_keyword_stuffed",
                        f"alt text repeats '{top}' {n}x or lists keywords; describe the image in plain words"))
    return out


def pick_hero(items: list[dict]) -> int | None:
    """fetchpriority=high wins; else the first <img> that does not look like a logo/icon/tracking pixel."""
    for i, it in enumerate(items):
        if it["kind"] == "img" and it["attrs"].get("fetchpriority", "").lower() == "high":
            return i
    for i, it in enumerate(items):
        if it["kind"] != "img":
            continue
        a = it["attrs"]
        w, h = int_attr(a.get("width")), int_attr(a.get("height"))
        hint = " ".join([best_src(a), a.get("class", ""), a.get("alt", ""), a.get("id", "")])
        if ICONISH.search(hint) or (w is not None and w < 120) or (h is not None and h < 80):
            continue
        return i
    return None


def role_of(it: dict, idx: int, hero: int | None) -> str:
    if idx == hero:
        return "hero"
    if it["kind"] == "css-background":
        return "background"
    a = it["attrs"]
    w, h = int_attr(a.get("width")), int_attr(a.get("height"))
    if (w is not None and w <= 300 and (h is None or h <= 300)) or THUMBISH.search(best_src(a) + " " + a.get("class", "")):
        return "thumbnail"
    return "content"


def audit_image(it: dict, role: str, page_url: str, res: dict | None) -> dict:
    a = it["attrs"]
    src = best_src(a)
    url = urljoin(page_url, src) if src else ""
    rec = {"kind": it["kind"], "role": role, "src": src, "url": url, "issues": [], "good": [],
           "alt": a.get("alt") if "alt" in a else None, "loading": a.get("loading", ""),
           "fetchpriority": a.get("fetchpriority", ""), "decoding": a.get("decoding", ""),
           "attr_width": int_attr(a.get("width")), "attr_height": int_attr(a.get("height")),
           "srcset": bool(a.get("srcset") or a.get("data-srcset")), "sizes": bool(a.get("sizes")),
           "picture_types": [s["type"] for s in it.get("picture_sources", []) if s.get("type")],
           "content_type": None, "format": None, "bytes": None, "intrinsic": None, "savings_bytes": 0, "savings_why": []}
    iss = rec["issues"]
    if not src or src.startswith("data:"):
        if it["kind"] == "img" and not src:
            add(iss, "high", "img_no_src", "<img> without src/srcset (nothing for crawlers to index)")
        return rec

    # ---- markup checks
    if it["kind"] == "img":
        style = a.get("style", "").lower()
        if (rec["attr_width"] is None or rec["attr_height"] is None) and "aspect-ratio" not in style \
                and not ("width" in style and "height" in style):
            add(iss, "medium", "missing_dimensions", "No width/height attributes (or CSS aspect-ratio); reserves no space, causes layout shift (CLS)")
        if rec["alt"] is None:
            add(iss, "medium", "missing_alt", "No alt attribute; add a description (or alt=\"\" if purely decorative)")
        elif rec["alt"].strip() == "":
            if role in ("hero", "content"):
                add(iss, "low", "empty_alt_check", "alt=\"\" marks this image as decorative; confirm it carries no information")
        else:
            for sev, code, msg in alt_problems(rec["alt"], src):
                add(iss, sev, code, msg)
        loading = rec["loading"].lower()
        if role == "hero":
            if loading == "lazy":
                add(iss, "high", "hero_lazy", "Hero/LCP candidate has loading=lazy; this delays LCP. Remove it and add fetchpriority=high")
            if rec["fetchpriority"].lower() == "high":
                rec["good"].append("fetchpriority=high")
            else:
                add(iss, "low", "hero_no_fetchpriority", "Hero/LCP candidate lacks fetchpriority=\"high\" (Next.js: <Image priority />)")
        else:
            if loading == "lazy":
                rec["good"].append("loading=lazy")
            if rec["decoding"].lower() == "async":
                rec["good"].append("decoding=async")
            else:
                add(iss, "info", "no_decoding_async", "Non-critical image without decoding=\"async\"")
        ws = [d for _, d in parse_srcset(a.get("srcset", ""))]
        if any(d.endswith("w") for d in ws) and not rec["sizes"]:
            add(iss, "low", "srcset_no_sizes", "srcset uses w descriptors but no sizes attribute; the browser assumes 100vw and may pick a larger file")
    else:
        if role == "background":
            add(iss, "info", "css_background_image", "CSS background image: not indexed as an image and cannot use alt, lazy loading or fetchpriority; "
                "use <img> if it is content or the LCP element")

    # ---- fetched image checks
    if res is None:
        return rec
    if res.get("status") != 200:
        add(iss, "high", "image_broken", f"Image returns {res.get('status') or res.get('error')}")
        return rec
    ct = res.get("content_type") or ""
    fmt, dims = sniff(res.get("head", b""))
    rec["format"] = fmt
    rec["content_type"] = ct
    rec["bytes"] = res.get("bytes")
    rec["intrinsic"] = list(dims) if dims else None
    if res.get("redirects"):
        add(iss, "low", "image_redirects", f"Image URL redirects ({res['redirects']} hop(s)); reference the final URL")
    if not ct.startswith("image/"):
        add(iss, "medium", "image_bad_content_type", f"Served with Content-Type '{ct or 'none'}' (sniffed: {fmt or 'unknown'}); serve the correct image/* type")
    elif fmt and FORMAT_CT.get(fmt) != ct and not (fmt == "ico" and "icon" in ct):
        add(iss, "low", "content_type_mismatch", f"Content-Type {ct} but the bytes are {fmt}")
    served = ct if ct.startswith("image/") else FORMAT_CT.get(fmt or "", "")
    size = rec["bytes"]
    modern_source = any(t.lower() in MODERN for t in rec["picture_types"])
    if served in LEGACY:
        if modern_source:
            rec["good"].append("<picture> offers " + "/".join(sorted({t.split('/')[-1] for t in rec['picture_types']})))
        elif size and size > 20 * KB and not (served == "image/gif" and size < 50 * KB):
            add(iss, "medium", "legacy_format",
                f"Served as {served} although the request accepted AVIF/WebP; serve WebP/AVIF (<picture>, framework image component or image CDN)")
            rec["savings_bytes"] = max(rec["savings_bytes"], int(size * 0.30))
            rec["savings_why"].append("modern format (~30%, estimate)")
    elif served in MODERN:
        rec["good"].append(served.split("/")[-1])

    if res.get("over_cap"):
        add(iss, "high", "image_over_5mb", f"Image is larger than 5 MB ({(size or 0) // KB or '>5120'} KB); not downloaded")
    if size is not None and fmt != "svg":
        budget, high = BUDGETS.get(role, BUDGETS["content"])
        if size > high:
            add(iss, "high", "image_over_budget", f"{size // KB} KB is far over the {role} budget ({budget // KB} KB; high above {high // KB} KB)")
        elif size > budget:
            add(iss, "medium", "image_over_budget", f"{size // KB} KB is over the {role} budget ({budget // KB} KB)")
        if size > budget:
            rec["savings_bytes"] = max(rec["savings_bytes"], size - budget)
            rec["savings_why"].append(f"to {role} budget")

    # oversized vs rendered width attribute (only when no srcset/picture chooses a smaller file)
    if dims and rec["attr_width"] and fmt not in ("svg", None):
        iw = dims[0]
        aw = rec["attr_width"]
        if iw > 2 * aw and not rec["srcset"] and not it.get("picture_sources"):
            add(iss, "medium", "image_oversized",
                f"Intrinsic {iw}x{dims[1]} px rendered at width={aw} ({iw / aw:.1f}x); resize to <= {2 * aw}px wide or add srcset")
            if size:
                rec["savings_bytes"] = max(rec["savings_bytes"], int(size * (1 - (2 * aw / iw) ** 2)))
                rec["savings_why"].append("resize to 2x rendered width (estimate)")
    large = (dims and dims[0] >= 1000) or (size or 0) > 100 * KB
    if it["kind"] == "img" and large and not rec["srcset"] and not any(s.get("srcset") for s in it.get("picture_sources", [])) and fmt != "svg":
        add(iss, "medium", "no_srcset", "Large image without srcset/sizes; phones download the desktop file")
    return rec


# --------------------------------------------------------------------------- page + crawl
def audit_page_images(page_url: str, html: str, ua: str, max_images: int, image_fetcher, cache: dict) -> dict:
    p = ImageParser()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001
        pass
    items = [it for it in p.items if best_src(it["attrs"]) or it["kind"] == "img"][:max_images]
    hero = pick_hero(items)
    out = []
    for idx, it in enumerate(items):
        role = role_of(it, idx, hero)
        src = best_src(it["attrs"])
        res = None
        if src and not src.startswith("data:"):
            u = urljoin(page_url, src)
            if urlparse(u).scheme in ("http", "https"):
                if u not in cache:
                    cache[u] = image_fetcher(u, ua)
                res = cache[u]
        rec = audit_image(it, role, page_url, res)
        rec["index"] = idx
        out.append(rec)
    page_issues = []
    total = sum(r["bytes"] or 0 for r in out)
    if total > 2 * 1024 * KB:
        add(page_issues, "medium", "page_image_weight", f"Images on this page total {total // KB} KB (> 2 MB)")
    if hero is None and out:
        add(page_issues, "info", "no_hero_detected", "No hero/LCP image candidate detected (text LCP, or the hero is a CSS background)")
    lazy_first = out and out[0]["kind"] == "img" and out[0]["loading"].lower() == "lazy" and hero != 0
    if lazy_first:
        add(page_issues, "low", "first_image_lazy", "The first <img> in the HTML is loading=lazy; fine if it is below the fold")
    return {"url": page_url, "images": out, "issues": page_issues, "total_bytes": total, "hero_index": hero,
            "preloads": [l.get("href") or l.get("imagesrcset", "")[:80] for l in p.preloads]}


def run(start: str, max_pages: int = 1, urls: list[str] | None = None, ua: str = UA_DEFAULT, delay: float = 0.2,
        max_images: int = 40, allow_private: bool = False, page_fetcher=fetch, image_fetcher=fetch_image) -> dict:
    sp = urlparse(start)
    if sp.scheme not in ("http", "https"):
        raise SystemExit("start URL must be http(s)")
    set_network_mode(sp.hostname, allow_private)
    robots = None
    if page_fetcher is fetch and not urls and max_pages > 1:
        try:
            robots = read_robots(f"{sp.scheme}://{sp.netloc}", ua).get("parser")
        except Exception:  # noqa: BLE001
            robots = None
    queue = deque(urls or [start])
    seen, pages, cache = set(), [], {}
    while queue and len(pages) < max(1, max_pages if not urls else len(urls)):
        u = normalize(queue.popleft())
        if u in seen or SKIP_EXT.search(urlparse(u).path):
            continue
        seen.add(u)
        if robots and u != normalize(start) and not robots.can_fetch(ua, u):
            continue
        res = page_fetcher(u, ua)
        if res["status"] != 200 or "html" not in res.get("headers", {}).get("content-type", "html"):
            pages.append({"url": u, "images": [], "issues": [{"severity": "high", "code": "page_fetch_failed",
                          "message": f"Page returned {res['status'] or res.get('error')}"}], "total_bytes": 0, "hero_index": None})
            continue
        pages.append(audit_page_images(res["final_url"], res["body"], ua, max_images, image_fetcher, cache))
        if not urls and max_pages > 1:
            pp = PageParser()
            try:
                pp.feed(res["body"])
            except Exception:  # noqa: BLE001
                pass
            for an in pp.anchors:
                href = urljoin(res["final_url"], an.get("href", ""))
                if urlparse(href).hostname == sp.hostname and urlparse(href).scheme in ("http", "https"):
                    n = normalize(href)
                    if n not in seen:
                        queue.append(n)
        if delay and page_fetcher is fetch:
            time.sleep(delay)
    counts = Counter()
    savings = []
    for pg in pages:
        for i in pg["issues"]:
            counts[i["severity"]] += 1
        for im in pg["images"]:
            for i in im["issues"]:
                counts[i["severity"]] += 1
            if im["savings_bytes"] > 0:
                savings.append({"page": pg["url"], "url": im["url"], "role": im["role"], "bytes": im["bytes"],
                                "savings_bytes": im["savings_bytes"], "why": ", ".join(im["savings_why"])})
    # same image on several pages: keep the largest single estimate
    best = {}
    for s in savings:
        if s["url"] not in best or s["savings_bytes"] > best[s["url"]]["savings_bytes"]:
            best[s["url"]] = s
    top = sorted(best.values(), key=lambda s: -s["savings_bytes"])[:15]
    return {"tool": "image_audit", "version": 1, "start": start, "origin": f"{sp.scheme}://{sp.netloc}",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages_audited": len(pages), "images_fetched": len(cache),
            "summary": {"counts": dict(counts), "estimated_savings_bytes": sum(s["savings_bytes"] for s in best.values())},
            "top_savings": top, "pages": pages}


def _kb(n):
    return "-" if n is None else f"{n / KB:.0f}"


def to_markdown(r: dict) -> str:
    c = r["summary"]["counts"]
    L = [f"# Image audit: {r['origin']}", "",
         f"- Checked: {r['checked_at']} | pages: {r['pages_audited']} | unique images fetched: {r['images_fetched']}",
         "- " + " ".join(f"{k}: {c.get(k, 0)}" for k in SEVERITY_ORDER) +
         f" | estimated savings: {r['summary']['estimated_savings_bytes'] // KB} KB (estimate)", ""]
    if r["top_savings"]:
        L += ["## Top savings (estimates)", "", "| KB now | Save ~KB | Role | Why | Image |", "|---|---|---|---|---|"]
        for s in r["top_savings"]:
            L.append(f"| {_kb(s['bytes'])} | {_kb(s['savings_bytes'])} | {s['role']} | {s['why']} | {s['url'][:110]} |")
        L.append("")
    for pg in r["pages"]:
        L += [f"## {pg['url']}", "", f"Images: {len(pg['images'])} | total {_kb(pg['total_bytes'])} KB", ""]
        for i in pg["issues"]:
            L.append(f"- **{i['severity']}** `{i['code']}`: {i['message']}")
        if pg["images"]:
            L += ["", "| # | Role | Type | KB | Intrinsic | Attr WxH | Alt | Good | Issues |", "|---|---|---|---|---|---|---|---|---|"]
            for im in pg["images"]:
                alt = "missing" if im["alt"] is None else ("empty" if not im["alt"].strip() else f"{len(im['alt'])} ch")
                intr = "x".join(map(str, im["intrinsic"])) if im["intrinsic"] else "-"
                attr = f"{im['attr_width'] or '?'}x{im['attr_height'] or '?'}" if im["kind"] == "img" else "css"
                iss = ", ".join(f"{i['code']}({i['severity'][0]})" for i in im["issues"] if i["severity"] != "info")
                L.append(f"| {im['index']} | {im['role']} | {(im['content_type'] or '-').replace('image/', '')} | {_kb(im['bytes'])} | "
                         f"{intr} | {attr} | {alt} | {', '.join(im['good']) or '-'} | {iss or '-'} |")
            L.append("")
            L.append("<details><summary>Image URLs</summary>\n")
            for im in pg["images"]:
                L.append(f"{im['index']}. {im['url'][:200] or '(none)'}")
            L.append("\n</details>\n")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=1, help="crawl up to N same-host pages from the start URL (default 1)")
    ap.add_argument("--urls", help="file with one page URL per line (audit only these)")
    ap.add_argument("--max-images", type=int, default=40, help="max images per page (default 40)")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--allow-private", action="store_true")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on", choices=["critical", "high", "medium"])
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    urls = None
    if a.urls:
        urls = [l.strip() for l in Path(a.urls).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    r = run(a.url, a.max_pages, urls, a.ua, a.delay, a.max_images, a.allow_private)
    md = to_markdown(r)
    js = json.dumps(r, indent=2, ensure_ascii=False, default=lambda o: None)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        host = urlparse(r["origin"]).netloc.replace(":", "_")
        (out / f"images-{host}-{stamp}.json").write_text(js, encoding="utf-8")
        (out / f"images-{host}-{stamp}.md").write_text(md, encoding="utf-8")
        print(f"[saved] {out / f'images-{host}-{stamp}.md'}", file=sys.stderr)
    print(js if a.json else md)
    if a.fail_on:
        limit = SEVERITY_ORDER[a.fail_on]
        if any(SEVERITY_ORDER[s] <= limit and n for s, n in r["summary"]["counts"].items()):
            sys.exit(1)


if __name__ == "__main__":
    main()
