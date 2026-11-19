#!/usr/bin/env python3
"""sitemap_gen.py - generate or validate an XML sitemap (stdlib only, Python 3.10+).

Sources (pick one):
  --from-crawl .seo/audits/crawl-<host>-<stamp>.json   crawl_audit.py output; keeps only status 200, indexable,
                                                       self-canonical, same-host, non-app, parameter-free URLs
  --urls urls.txt                                      one URL per line (# comments allowed)
  --url https://example.com --max-pages 200            live crawl through crawl_audit.crawl()

Options:
  --lastmod-from none|http|<file.csv>   none (default), the Last-Modified response header, or a CSV "url,lastmod"
  --base https://www.example.com        rewrite scheme+host (e.g. preview -> production domain)
  --split 50000                         max URLs per file; files are also kept under 50 MB uncompressed
  --out public/                         write sitemap.xml, or sitemap-index.xml + sitemap-N.xml when split
  --legacy-tags                         also emit <changefreq>/<priority> (ignored by Google; off by default)
  --validate <file|url>                 check an existing sitemap / sitemap index instead of generating

Examples:
  python scripts/sitemap_gen.py --from-crawl .seo/audits/crawl-example.com-20250101-120000.json --base https://example.com --out public/
  python scripts/sitemap_gen.py --url http://localhost:3000 --max-pages 300 --base https://example.com --out public/
  python scripts/sitemap_gen.py --validate https://example.com/sitemap.xml --json

Without --out the sitemap XML (or, when split, the index) is printed; nothing is written.
Findings use {"severity", "code", "message"}. Exit 1 on --validate when a critical/high finding exists.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
MAX_URLS = 50_000
MAX_BYTES = 50 * 1024 * 1024
SEVERITY_ORDER = ca.SEVERITY_ORDER
W3C_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?)?)?$")


def add(findings: list, severity: str, code: str, message: str, **extra) -> None:
    findings.append({"severity": severity, "code": code, "message": message, **extra})


# --------------------------------------------------------------------------- helpers
def w3c_date(value: str | None) -> str | None:
    """Normalise a date string (ISO 8601, YYYY-MM-DD, HTTP-date) to W3C datetime. None if unparseable."""
    if not value:
        return None
    v = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            return None
    dt = None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        try:
            dt = parsedate_to_datetime(v)
        except (TypeError, ValueError, IndexError):
            dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def encode_url(url: str) -> str:
    """Percent-encode non-ASCII / spaces (sitemaps need RFC 3986 URLs); existing %XX escapes are kept."""
    return quote(url.strip(), safe=":/?#[]@!$&'()*+,;=%~")


def xml_text(s: str) -> str:
    return escape(s, {"'": "&apos;", '"': "&quot;"})


def rewrite_base(url: str, base: str | None) -> str:
    if not base:
        return url
    b = urlparse(base if "://" in base else "https://" + base)
    p = urlparse(url)
    return urlunparse((b.scheme, b.netloc, p.path or "/", p.params, p.query, ""))


def origin_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# --------------------------------------------------------------------------- collect
def collect_from_crawl(data: dict) -> tuple[list[dict], Counter]:
    """Pick sitemap-worthy pages from a crawl_audit result. Returns (entries, excluded-by-reason)."""
    host = urlparse(data.get("origin") or data.get("start") or "").hostname
    excluded: Counter = Counter()
    seen: set[str] = set()
    out: list[dict] = []
    for pg in data.get("pages", []):
        url = pg.get("final_url") or pg.get("url") or ""
        p = urlparse(url)
        path = p.path or "/"
        if pg.get("status") != 200:
            excluded["non_200"] += 1
            continue
        if pg.get("non_html") or pg.get("blocked"):
            excluded["non_html_or_blocked"] += 1
            continue
        if pg.get("noindex"):
            excluded["noindex"] += 1
            continue
        canon = pg.get("canonical")
        if canon and ca.normalize(canon) != ca.normalize(url):
            excluded["canonicalized_elsewhere"] += 1
            continue
        if pg.get("is_indexable") is False:
            excluded["not_indexable"] += 1
            continue
        if host and p.hostname != host:
            excluded["other_host"] += 1
            continue
        if ca.APP_PATH_HINT.match(path):
            excluded["app_path"] += 1
            continue
        if p.query:
            excluded["query_string"] += 1
            continue
        n = ca.normalize(url)
        if n in seen:
            excluded["duplicate"] += 1
            continue
        seen.add(n)
        out.append({"loc": n})
    return out, excluded


def collect_from_list(lines: list[str]) -> tuple[list[dict], Counter]:
    excluded: Counter = Counter()
    out, seen, host = [], set(), None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url = line.split(",")[0].strip() if "," in line and line.lower().startswith("http") else line
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            excluded["not_absolute_http_url"] += 1
            continue
        host = host or p.hostname
        if p.hostname != host:
            excluded["other_host"] += 1
            continue
        n = ca.normalize(url)
        if n in seen:
            excluded["duplicate"] += 1
            continue
        seen.add(n)
        out.append({"loc": n})
    return out, excluded


def load_lastmod_csv(path: str) -> dict[str, str]:
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 2 or not row[0].strip().lower().startswith("http"):
                continue  # header or junk
            d = w3c_date(row[1])
            if d:
                out[ca.normalize(row[0].strip())] = d
    return out


def apply_lastmod(entries: list[dict], mode: str, ua: str = ca.UA_DEFAULT, delay: float = 0.1) -> list[dict]:
    """mode: 'none' | 'http' | path to CSV. Returns findings."""
    findings: list[dict] = []
    if not mode or mode == "none":
        return findings
    if mode == "http":
        if entries:
            ca.set_network_mode(urlparse(entries[0]["loc"]).hostname)
        missing = 0
        for e in entries:
            r = ca.fetch(e["loc"], ua)
            d = w3c_date((r.get("headers") or {}).get("last-modified"))
            if d:
                e["lastmod"] = d
            else:
                missing += 1
            if delay:
                time.sleep(delay)
        if missing:
            add(findings, "info", "lastmod_header_missing", f"{missing}/{len(entries)} URLs sent no usable Last-Modified header; lastmod omitted for them")
        return findings
    table = load_lastmod_csv(mode)
    hit = 0
    for e in entries:
        d = table.get(e["loc"])
        if d:
            e["lastmod"] = d
            hit += 1
    if hit < len(entries):
        add(findings, "info", "lastmod_csv_partial", f"{len(entries) - hit}/{len(entries)} URLs have no lastmod in {mode}")
    return findings


# --------------------------------------------------------------------------- build
def url_block(e: dict, legacy: bool) -> str:
    parts = [f"  <url>\n    <loc>{xml_text(encode_url(e['loc']))}</loc>\n"]
    if e.get("lastmod"):
        parts.append(f"    <lastmod>{e['lastmod']}</lastmod>\n")
    if legacy:
        path = urlparse(e["loc"]).path or "/"
        prio = "1.0" if path == "/" else ("0.8" if path.count("/") <= 1 else "0.6")
        parts.append(f"    <changefreq>weekly</changefreq>\n    <priority>{prio}</priority>\n")
    parts.append("  </url>\n")
    return "".join(parts)


URLSET_HEAD = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="{NS}">\n'
URLSET_TAIL = "</urlset>\n"


def build_parts(entries: list[dict], split: int = MAX_URLS, legacy: bool = False, max_bytes: int = MAX_BYTES) -> list[str]:
    """Split entries into urlset documents honouring both the URL-count and byte-size limits."""
    split = max(1, min(split, MAX_URLS))
    overhead = len(URLSET_HEAD.encode()) + len(URLSET_TAIL.encode())
    parts, cur, cur_bytes = [], [], overhead
    for e in entries:
        blk = url_block(e, legacy)
        size = len(blk.encode("utf-8"))
        if cur and (len(cur) >= split or cur_bytes + size > max_bytes):
            parts.append(URLSET_HEAD + "".join(cur) + URLSET_TAIL)
            cur, cur_bytes = [], overhead
        cur.append(blk)
        cur_bytes += size
    if cur or not parts:
        parts.append(URLSET_HEAD + "".join(cur) + URLSET_TAIL)
    return parts


def build_index(part_urls: list[str], lastmods: list[str | None]) -> str:
    L = ['<?xml version="1.0" encoding="UTF-8"?>', f'<sitemapindex xmlns="{NS}">']
    for u, lm in zip(part_urls, lastmods):
        L.append(f"  <sitemap>\n    <loc>{xml_text(encode_url(u))}</loc>" + (f"\n    <lastmod>{lm}</lastmod>" if lm else "") + "\n  </sitemap>")
    L.append("</sitemapindex>")
    return "\n".join(L) + "\n"


def generate(entries: list[dict], split: int, legacy: bool, index_base: str | None, max_bytes: int = MAX_BYTES) -> dict:
    """Return {"files": {name: xml}, "index": name|None, "parts": n}."""
    parts = build_parts(entries, split, legacy, max_bytes)
    if len(parts) == 1:
        return {"files": {"sitemap.xml": parts[0]}, "index": None, "parts": 1}
    base = (index_base or (origin_of(entries[0]["loc"]) if entries else "")).rstrip("/")
    files, urls, lms = {}, [], []
    for i, xml in enumerate(parts, 1):
        name = f"sitemap-{i}.xml"
        files[name] = xml
        urls.append(f"{base}/{name}")
        found = re.findall(r"<lastmod>([^<]+)</lastmod>", xml)
        lms.append(max(found) if found else None)
    files["sitemap-index.xml"] = build_index(urls, lms)
    return {"files": files, "index": "sitemap-index.xml", "parts": len(parts)}


# --------------------------------------------------------------------------- validate
def _load(src: str, max_bytes: int = MAX_BYTES + 1024) -> dict:
    """Load a sitemap from a URL or a local file. Returns {text, status, content_type, error, bytes, kind}."""
    if re.match(r"^https?://", src, re.I):
        r = ca.fetch(src, ca.UA_DEFAULT, accept="application/xml,text/xml,*/*", max_bytes=max_bytes)
        body = r.get("body") or ""
        if body.startswith("\x1f\ufffd") or body.startswith("\x1f\x8b"):
            return {"text": "", "status": r["status"], "content_type": r["headers"].get("content-type", ""),
                    "error": "gzip-compressed body without Content-Encoding; download and validate the decompressed file",
                    "bytes": r.get("bytes", 0), "kind": "url"}
        return {"text": body, "status": r["status"], "content_type": (r.get("headers") or {}).get("content-type", ""),
                "error": r.get("error"), "bytes": r.get("bytes", len(body.encode("utf-8"))), "kind": "url",
                "truncated": r.get("truncated", False)}
    p = Path(src)
    if not p.is_file():
        return {"text": "", "status": 0, "content_type": "", "error": f"file not found: {src}", "bytes": 0, "kind": "file"}
    raw = p.read_bytes()
    truncated = False
    if raw[:2] == b"\x1f\x8b":
        raw, truncated = ca._decode_body(raw, "gzip", max_bytes)
    else:
        truncated = len(raw) > max_bytes
    return {"text": raw.decode("utf-8", errors="replace"), "status": 200, "content_type": "", "error": None,
            "bytes": len(raw), "kind": "file", "truncated": truncated}


def parse_sitemap(text: str) -> dict:
    """Safe parse. Returns {type, entries:[{loc, lastmod, legacy}], ns, error}."""
    # Scan the whole document (not only the head): padding with comments must not hide a DTD.
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        return {"type": None, "entries": [], "ns": None, "error": "doctype"}
    try:
        root = ET.fromstring(text.encode("utf-8", errors="replace"))
    except ET.ParseError as e:
        return {"type": None, "entries": [], "ns": None, "error": f"xml: {e}"}
    tag = root.tag.split("}")[-1]
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    entries = []
    child_name = "url" if tag == "urlset" else "sitemap"
    for el in root:
        if el.tag.split("}")[-1] != child_name:
            continue
        rec = {"loc": None, "lastmod": None, "legacy": False}
        for c in el:
            name = c.tag.split("}")[-1]
            if name == "loc" and c.text:
                rec["loc"] = c.text.strip()
            elif name == "lastmod" and c.text:
                rec["lastmod"] = c.text.strip()
            elif name in ("priority", "changefreq"):
                rec["legacy"] = True
        entries.append(rec)
    return {"type": tag, "entries": entries, "ns": ns, "error": None}


def _check_urlset(src: str, loaded: dict, parsed: dict, findings: list) -> list[dict]:
    entries = parsed["entries"]
    name = src
    if loaded["bytes"] > MAX_BYTES or loaded.get("truncated"):
        add(findings, "high", "sitemap_too_large", f"{name} is {loaded['bytes'] // (1024 * 1024)} MB uncompressed (> 50 MB limit); split it")
    if len(entries) > MAX_URLS:
        add(findings, "high", "sitemap_too_many_urls", f"{name} has {len(entries)} URLs (> 50,000 per file); use a sitemap index")
    if not entries:
        add(findings, "high", "sitemap_empty", f"{name} lists no URLs")
    if parsed["ns"] != NS:
        add(findings, "medium", "sitemap_namespace", f"{name} root namespace is '{parsed['ns'] or '-'}', expected {NS}")
    missing_loc = sum(1 for e in entries if not e["loc"])
    if missing_loc:
        add(findings, "high", "sitemap_missing_loc", f"{missing_loc} <url> entries have no <loc> in {name}")
    bad_dates = [e["lastmod"] for e in entries if e["lastmod"] and not W3C_RE.match(e["lastmod"])]
    if bad_dates:
        add(findings, "low", "lastmod_not_w3c", f"{len(bad_dates)} lastmod values are not W3C datetime (e.g. '{bad_dates[0]}')")
    if any(e["legacy"] for e in entries):
        add(findings, "info", "legacy_tags", f"{name} uses <priority>/<changefreq>; Google ignores both (harmless, can be removed)")
    return entries


def validate(src: str, max_children: int = 25) -> dict:
    findings: list[dict] = []
    if re.match(r"^https?://", src, re.I):
        ca.set_network_mode(urlparse(src).hostname)
    loaded = _load(src)
    result = {"tool": "sitemap_gen", "mode": "validate", "source": src, "type": None, "files_checked": 1,
              "url_count": 0, "findings": findings}
    if loaded["error"] or loaded["status"] != 200:
        add(findings, "critical", "sitemap_unreachable", f"{src}: {loaded['error'] or 'HTTP ' + str(loaded['status'])}")
        return result
    if loaded["kind"] == "url":
        ct = loaded["content_type"].lower()
        if "html" in ct:
            add(findings, "high", "sitemap_served_as_html", f"{src} is served as {ct}; an SPA fallback may be answering instead of a real sitemap")
        elif ct and "xml" not in ct and "gzip" not in ct and "text/plain" not in ct:
            add(findings, "low", "sitemap_content_type", f"{src} Content-Type is '{ct}' (expected application/xml or text/xml)")
    parsed = parse_sitemap(loaded["text"])
    if parsed["error"] == "doctype":
        add(findings, "high", "sitemap_doctype", f"{src} contains a DOCTYPE/ENTITY declaration; refused to parse (sitemaps never need one)")
        return result
    if parsed["error"]:
        add(findings, "critical", "sitemap_invalid_xml", f"{src} is not valid XML: {parsed['error']}")
        return result
    result["type"] = parsed["type"]
    all_entries: list[dict] = []
    if parsed["type"] == "sitemapindex":
        children = [e["loc"] for e in parsed["entries"] if e["loc"]]
        result["child_sitemaps"] = len(children)
        if not children:
            add(findings, "high", "sitemap_index_empty", f"{src} is a sitemap index without child sitemaps")
        if len(children) > MAX_URLS:
            add(findings, "high", "sitemap_index_too_large", f"index lists {len(children)} sitemaps (> 50,000)")
        idx_host = (urlparse(src).hostname or "").removeprefix("www.") if loaded["kind"] == "url" else None
        for child in children[:max_children]:
            ch_host = (urlparse(child).hostname or "").removeprefix("www.")
            if idx_host and ch_host != idx_host and not ch_host.endswith("." + idx_host):
                add(findings, "high", "sitemap_child_cross_host", f"child sitemap {child} is on another host; skipped")
                continue
            child_src = child
            if loaded["kind"] == "file":
                local = Path(src).parent / Path(urlparse(child).path).name
                if local.is_file():
                    child_src = str(local)
                else:
                    add(findings, "info", "child_not_local", f"child sitemap {child} not found next to {src}; not checked")
                    continue
            cl = _load(child_src)
            result["files_checked"] += 1
            if cl["error"] or cl["status"] != 200:
                add(findings, "high", "child_sitemap_unreachable", f"{child}: {cl['error'] or 'HTTP ' + str(cl['status'])}")
                continue
            cp = parse_sitemap(cl["text"])
            if cp["error"]:
                add(findings, "high", "child_sitemap_invalid", f"{child}: {'DOCTYPE/ENTITY refused' if cp['error'] == 'doctype' else cp['error']}")
                continue
            if cp["type"] == "sitemapindex":
                add(findings, "high", "nested_sitemap_index", f"{child} is itself a sitemap index; nested indexes are not allowed")
                continue
            all_entries += _check_urlset(child, cl, cp, findings)
        if len(children) > max_children:
            add(findings, "info", "children_not_checked", f"only the first {max_children} of {len(children)} child sitemaps were checked (--max-children)")
        host_ref = urlparse(src).hostname if loaded["kind"] == "url" else None
    elif parsed["type"] == "urlset":
        all_entries = _check_urlset(src, loaded, parsed, findings)
        host_ref = urlparse(src).hostname if loaded["kind"] == "url" else None
    else:
        add(findings, "high", "sitemap_wrong_root", f"{src} root element is <{parsed['type']}>, expected <urlset> or <sitemapindex>")
        return result

    locs = [e["loc"] for e in all_entries if e["loc"]]
    result["url_count"] = len(locs)
    if not host_ref and locs:
        host_ref = Counter(urlparse(u).hostname for u in locs).most_common(1)[0][0]
    result["host"] = host_ref
    # duplicates
    norm = Counter(ca.normalize(u) for u in locs)
    dups = [u for u, n in norm.items() if n > 1]
    if dups:
        add(findings, "medium", "duplicate_urls", f"{len(dups)} URLs are listed more than once", samples=dups[:5])
    rel = [u for u in locs if not urlparse(u).scheme]
    if rel:
        add(findings, "high", "relative_loc", f"{len(rel)} <loc> values are not absolute URLs", samples=rel[:5])
    cross = [u for u in locs if urlparse(u).scheme and urlparse(u).hostname != host_ref]
    if cross:
        add(findings, "high", "cross_host_urls", f"{len(cross)} URLs are on another host than {host_ref} (staging/localhost leak?)", samples=cross[:5])
    plain = [u for u in locs if urlparse(u).scheme == "http" and urlparse(u).hostname not in ("localhost", "127.0.0.1")]
    if plain:
        add(findings, "medium", "non_https_urls", f"{len(plain)} URLs use http:// instead of https://", samples=plain[:5])
    app = [u for u in locs if ca.APP_PATH_HINT.match(urlparse(u).path or "/")]
    if app:
        add(findings, "medium", "app_routes_listed", f"{len(app)} app/auth/API routes are listed (keep marketing pages only)", samples=app[:5])
    params = [u for u in locs if urlparse(u).query]
    if params:
        add(findings, "low", "parameter_urls", f"{len(params)} URLs carry query strings (usually duplicates of a canonical URL)", samples=params[:5])
    lastmods = [e["lastmod"] for e in all_entries if e["lastmod"]]
    result["lastmod_share"] = round(len(lastmods) / len(all_entries), 3) if all_entries else 0
    if len(lastmods) >= 10:
        top, n = Counter(lastmods).most_common(1)[0]
        share = n / len(lastmods)
        result["identical_lastmod_share"] = round(share, 3)
        if share > 0.9:
            add(findings, "low", "identical_lastmod", f"{share:.0%} of lastmod values are identical ({top}); build-time stamps teach Google to ignore lastmod")
    now = datetime.now(timezone.utc)
    future = 0
    for lm in lastmods:
        d = w3c_date(lm)
        if d:
            try:
                dt = datetime.fromisoformat(d) if "T" in d else datetime.fromisoformat(d + "T00:00:00+00:00")
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (dt - now).days > 1:
                    future += 1
            except ValueError:
                pass
    if future:
        add(findings, "low", "lastmod_in_future", f"{future} lastmod values are in the future")
    return result


# --------------------------------------------------------------------------- report
def render_validate(res: dict) -> str:
    L = [f"# Sitemap validation: {res['source']}", "",
         f"- Type: {res.get('type') or '-'} | files checked: {res['files_checked']} | URLs: {res['url_count']}"
         + (f" | child sitemaps: {res['child_sitemaps']}" if "child_sitemaps" in res else ""),
         f"- Host: {res.get('host') or '-'} | lastmod coverage: {res.get('lastmod_share', 0):.0%}"
         + (f" | identical lastmod share: {res['identical_lastmod_share']:.0%}" if "identical_lastmod_share" in res else ""), ""]
    if not res["findings"]:
        L.append("No problems found.")
    else:
        L += ["| Severity | Code | Message |", "|---|---|---|"]
        for f in sorted(res["findings"], key=lambda x: SEVERITY_ORDER[x["severity"]]):
            L.append(f"| {f['severity']} | `{f['code']}` | {f['message'].replace('|', '/')} |")
            for s in f.get("samples", [])[:3]:
                L.append(f"|  |  | e.g. {s} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-crawl", help="crawl_audit.py JSON output")
    src.add_argument("--urls", help="text file with one URL per line")
    src.add_argument("--url", help="start URL for a live crawl (crawl_audit.crawl)")
    src.add_argument("--validate", metavar="FILE_OR_URL", help="validate an existing sitemap or sitemap index")
    ap.add_argument("--max-pages", type=int, default=200, help="live crawl page cap (default 200)")
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between live requests")
    ap.add_argument("--allow-private", action="store_true", help="live crawl: allow private/internal addresses")
    ap.add_argument("--lastmod-from", default="none", help="none | http | path/to/lastmod.csv (url,lastmod)")
    ap.add_argument("--split", type=int, default=MAX_URLS, help="max URLs per sitemap file (<= 50000)")
    ap.add_argument("--base", help="rewrite scheme+host of every URL, e.g. https://www.example.com")
    ap.add_argument("--index-base", help="base URL where the part files are served (default: site origin)")
    ap.add_argument("--legacy-tags", action="store_true", help="emit changefreq/priority (ignored by Google)")
    ap.add_argument("--out", help="directory to write the sitemap file(s) into, e.g. public/")
    ap.add_argument("--max-children", type=int, default=25, help="--validate: child sitemaps to check")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if a.validate:
        res = validate(a.validate, a.max_children)
        print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else render_validate(res))
        return 1 if any(SEVERITY_ORDER[f["severity"]] <= 1 for f in res["findings"]) else 0

    findings: list[dict] = []
    if a.from_crawl:
        data = json.loads(Path(a.from_crawl).read_text(encoding="utf-8"))
        entries, excluded = collect_from_crawl(data)
        if data.get("crawl_complete") is False:
            add(findings, "info", "crawl_incomplete", "the crawl hit its page cap; the sitemap may miss pages (re-crawl with a higher --max-pages)")
    elif a.urls:
        entries, excluded = collect_from_list(Path(a.urls).read_text(encoding="utf-8").splitlines())
    else:
        data = ca.crawl(a.url, a.max_pages, ca.UA_DEFAULT, a.delay, None, True, a.allow_private)
        entries, excluded = collect_from_crawl(data)
        if not data.get("crawl_complete"):
            add(findings, "info", "crawl_incomplete", f"crawl stopped at {a.max_pages} pages; raise --max-pages for a complete sitemap")
    findings += apply_lastmod(entries, a.lastmod_from, delay=a.delay)
    for e in entries:
        e["loc"] = rewrite_base(e["loc"], a.base)
    entries.sort(key=lambda e: (urlparse(e["loc"]).path != "/", e["loc"]))
    for reason, n in sorted(excluded.items()):
        add(findings, "info", f"excluded_{reason}", f"{n} URL(s) excluded: {reason.replace('_', ' ')}")
    if not entries:
        add(findings, "high", "no_urls", "no sitemap-worthy URLs found")
    if any(urlparse(e["loc"]).hostname in ("localhost", "127.0.0.1") for e in entries):
        add(findings, "high", "localhost_urls", "URLs point at localhost; pass --base https://your-production-domain")
    lm = [e.get("lastmod") for e in entries if e.get("lastmod")]
    if len(lm) >= 10 and Counter(lm).most_common(1)[0][1] / len(lm) > 0.9:
        add(findings, "low", "identical_lastmod", "over 90% of lastmod values are identical; use real content-change dates or omit lastmod")
    gen = generate(entries, a.split, a.legacy_tags, a.index_base or (a.base.rstrip("/") if a.base else None))
    written = []
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, xml in gen["files"].items():
            (out / name).write_text(xml, encoding="utf-8", newline="\n")
            written.append(str(out / name))
    summary = {"tool": "sitemap_gen", "mode": "generate", "url_count": len(entries), "parts": gen["parts"],
               "index": gen["index"], "written": written, "excluded": dict(excluded), "findings": findings}
    if a.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    elif a.out:
        print(f"# Sitemap generated\n\n- URLs: {len(entries)} | files: {len(gen['files'])}")
        for w in written:
            print(f"- wrote {w}")
        if gen["index"]:
            print(f"- point robots.txt at the index: Sitemap: {(a.index_base or a.base or origin_of(entries[0]['loc'])).rstrip('/')}/sitemap-index.xml")
        for f in findings:
            print(f"- **{f['severity']}** `{f['code']}`: {f['message']}")
    else:
        print(gen["files"][gen["index"] or "sitemap.xml"], end="")
        for f in findings:
            print(f"<!-- {f['severity']} {f['code']}: {f['message']} -->", file=sys.stderr)
    return 1 if any(SEVERITY_ORDER[f["severity"]] <= 1 for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
