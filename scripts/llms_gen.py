#!/usr/bin/env python3
"""llms_gen.py - generate (and validate) llms.txt / llms-full.txt from real site data (stdlib only).

Sources (pick one):
  --from-crawl .seo/audits/crawl-<host>-<stamp>.json   crawl_audit.py output (titles/descriptions already in it)
  --sitemap https://example.com/sitemap.xml|file.xml   fetches each listed page (up to --max-pages)
  --url https://example.com --max-pages 60             live crawl through crawl_audit.crawl()

Output (llmstxt.org format):
  # Site name                       --name, else the homepage og:site_name, else the brand core of the titles
  > summary                         --summary, else the homepage meta description (omitted when neither exists)
  ## Product / Docs / Guides and Blog / Company / Optional
  - [Page title](https://absolute-url): meta description (~160 chars)

Grouping: Product = /, /pricing, /features*, /integrations*; Docs = /docs*, /api*; Guides and Blog = /blog*,
/guides*; Company = /about, /contact, /security, /changelog; Optional = everything else.
Skipped: non-200, noindex, canonicalized elsewhere, app/auth paths, pagination, tag pages, parameter URLs.
Nothing is invented: names, summaries, titles and descriptions come only from the pages or your flags.

Usage:
  python scripts/llms_gen.py --url https://example.com --max-pages 60                # print llms.txt
  python scripts/llms_gen.py --from-crawl .seo/audits/crawl-x.json --out public/ --full  # write llms.txt + llms-full.txt
  python scripts/llms_gen.py --validate https://example.com/llms.txt --json

Options: --max-entries 60, --full (llms-full.txt from each page's <main>/<article> text), --max-full-bytes 2000000,
--out DIR (write files; default print only), --json.
llms.txt carries no ranking weight in Google; it mainly helps AI coding agents and doc tools.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402

SECTIONS = [
    ("Product", ["/", "/pricing", "/pricing/*", "/features", "/features*", "/integrations", "/integrations*"]),
    ("Docs", ["/docs", "/docs*", "/api", "/api*"]),
    ("Guides and Blog", ["/blog", "/blog*", "/guides", "/guides*"]),
    ("Company", ["/about", "/about/*", "/contact", "/security", "/security/*", "/changelog", "/changelog*"]),
]
# Selection priority when --max-entries trims (Optional and long blog archives go first).
PRIORITY = ["Product", "Docs", "Company", "Guides and Blog", "Optional"]
DISPLAY = ["Product", "Docs", "Guides and Blog", "Company", "Optional"]
PAGINATION = re.compile(r"(/page/\d+|/p/\d+)/?$", re.I)
TAG_PAGE = re.compile(r"/(tag|tags|label|labels|author|authors)(/|$)", re.I)
TITLE_SEP = re.compile(r"\s+[|\-\u2013\u2014\u00b7:\u2022]\s+")
# app paths: crawl_audit's list, minus /api (public API reference docs live there) which is handled separately.
APP_PATH = re.compile(r"^/(app|dashboard|admin|account|settings|billing|login|signin|sign-in|signup|sign-up|register|auth|logout)(/|$)", re.I)


def add(findings: list, severity: str, code: str, message: str, **extra) -> None:
    findings.append({"severity": severity, "code": code, "message": message, **extra})


# --------------------------------------------------------------------------- page data
def page_record(res: dict) -> dict:
    """Turn a crawl_audit.fetch() result into a record like the crawl JSON pages (+ site_name, body)."""
    rec = {"url": res.get("url"), "final_url": res.get("final_url"), "status": res.get("status"),
           "redirects": res.get("chain", []), "title": "", "description": "", "noindex": False, "canonical": None,
           "h1": [], "site_name": None, "body": ""}
    if res.get("status") != 200 or "html" not in (res.get("headers") or {}).get("content-type", "text/html"):
        if res.get("status") == 200:
            rec["non_html"] = (res.get("headers") or {}).get("content-type")
        return rec
    p = ca.PageParser()
    try:
        p.feed(res.get("body") or "")
    except Exception:
        pass
    robots = " ".join(filter(None, [p.meta("robots"), p.meta("googlebot")])).lower()
    rec["noindex"] = "noindex" in robots or "noindex" in (res.get("headers") or {}).get("x-robots-tag", "").lower()
    canon = p.link_rel("canonical")
    rec["canonical"] = urljoin(res["final_url"], canon[0].get("href", "")) if canon else None
    rec["title"] = " ".join((p.title or "").split())
    rec["description"] = " ".join((p.meta("description") or "").split())
    rec["site_name"] = (p.meta("og:site_name") or "").strip() or None
    rec["h1"] = [t for tag, t in p.headings if tag == "h1"]
    rec["body"] = res.get("body") or ""
    return rec


def eligible(pg: dict, host: str | None) -> str | None:
    """Return a skip reason, or None when the page may be listed."""
    url = pg.get("final_url") or pg.get("url") or ""
    p = urlparse(url)
    path = p.path or "/"
    if pg.get("status") != 200:
        return "non_200"
    if pg.get("non_html") or pg.get("blocked"):
        return "non_html"
    if pg.get("noindex"):
        return "noindex"
    if pg.get("canonical") and ca.normalize(pg["canonical"]) != ca.normalize(url):
        return "canonicalized"
    if host and p.hostname != host:
        return "other_host"
    if APP_PATH.match(path):
        return "app_path"
    if p.query:
        return "parameter_url"
    if PAGINATION.search(path):
        return "pagination"
    if TAG_PAGE.search(path):
        return "tag_page"
    if ca.SKIP_EXT.search(path):
        return "asset"
    return None


def section_for(path: str) -> str:
    p = (path.rstrip("/") or "/").lower()
    for name, pats in SECTIONS:
        if any(fnmatch.fnmatchcase(p, pat) for pat in pats):
            return name
    return "Optional"


def split_title(title: str) -> list[str]:
    return [s.strip() for s in TITLE_SEP.split(title or "") if s.strip()]


def detect_brand(pages: list[dict]) -> str | None:
    """Brand = a title segment (first or last) shared by several pages; falls back to the homepage title core."""
    c: Counter = Counter()
    for pg in pages:
        segs = split_title(pg.get("title", ""))
        if len(segs) >= 2:
            c[segs[-1]] += 1
            c[segs[0]] += 1
    if c:
        seg, n = c.most_common(1)[0]
        if n >= 2:
            return seg
    home = next((pg for pg in pages if (urlparse(pg.get("final_url") or "").path or "/") == "/"), None)
    if home:
        segs = split_title(home.get("title", ""))
        if segs:
            return segs[-1] if len(segs) >= 2 and len(segs[-1]) <= len(segs[0]) else segs[0]
    return None


def clean_title(title: str, brand: str | None, fallback: str = "") -> str:
    segs = split_title(title)
    if brand and len(segs) >= 2:
        b = brand.lower()
        if segs[-1].lower() == b:
            segs = segs[:-1]
        elif segs[0].lower() == b:
            segs = segs[1:]
    t = " - ".join(segs) if segs else ""
    return t or fallback


def truncate(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 3].rsplit(" ", 1)[0].rstrip(",.;:-")
    return cut + "..."


def md_escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def path_label(url: str) -> str:
    seg = [s for s in (urlparse(url).path or "/").split("/") if s]
    return seg[-1].replace("-", " ").replace("_", " ").capitalize() if seg else "Home"


# --------------------------------------------------------------------------- build
def build_entries(pages: list[dict], host: str | None, brand: str | None, max_entries: int = 60) -> tuple[dict, Counter]:
    skipped: Counter = Counter()
    by_section: dict[str, list[dict]] = {s: [] for s in DISPLAY}
    seen = set()
    for pg in pages:
        reason = eligible(pg, host)
        if reason:
            skipped[reason] += 1
            continue
        url = ca.normalize(pg.get("final_url") or pg.get("url"))
        if url in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(url)
        h1 = (pg.get("h1") or [""])[0] if isinstance(pg.get("h1"), list) else ""
        title = clean_title(pg.get("title", ""), brand, h1 or path_label(url))
        path = urlparse(url).path or "/"
        by_section[section_for(path)].append({"title": title, "url": url, "description": truncate(pg.get("description", "")),
                                              "path": path})
    # A description shared by 3+ pages is a site-wide default, not a page summary: keep it on the homepage only.
    shared = Counter(e["description"].lower() for s in by_section for e in by_section[s] if e["description"])
    for s in by_section:
        for e in by_section[s]:
            if e["description"] and shared[e["description"].lower()] >= 3 and e["path"] != "/":
                e["description"] = ""
                skipped["generic_description_dropped"] += 1
        by_section[s].sort(key=lambda e: (e["path"] != "/", e["path"].count("/"), e["path"]))
    budget = max_entries
    chosen: dict[str, list[dict]] = {s: [] for s in DISPLAY}
    for s in PRIORITY:
        take = by_section[s][:max(0, budget)]
        chosen[s] = take
        budget -= len(take)
        if len(by_section[s]) > len(take):
            skipped["over_max_entries"] += len(by_section[s]) - len(take)
    return chosen, skipped


def render_llms(name: str, summary: str | None, chosen: dict) -> str:
    L = [f"# {name}", ""]
    if summary:
        L += [f"> {' '.join(summary.split())}", ""]
    for s in DISPLAY:
        if not chosen.get(s):
            continue
        L += [f"## {s}", ""]
        for e in chosen[s]:
            line = f"- [{md_escape(e['title'])}]({e['url']})"
            if e["description"]:
                line += f": {e['description']}"
            L.append(line)
        L.append("")
    return "\n".join(L).rstrip() + "\n"


# --------------------------------------------------------------------------- llms-full
class MainText(HTMLParser):
    """Extract readable text as Markdown-ish blocks. Prefers <main>/<article>; drops nav/footer/header/aside/scripts."""
    SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside", "form", "iframe", "button"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "td", "th", "dt", "dd", "div", "section", "br", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.main_depth = 0
        self.found_main = False
        self.blocks_main: list[str] = []
        self.blocks_all: list[str] = []
        self.buf: list[str] = []
        self.prefix = ""
        self.in_pre = 0

    def _flush(self):
        text = "".join(self.buf)
        text = text if self.in_pre else " ".join(text.split())
        if text.strip():
            block = self.prefix + text
            self.blocks_all.append(block)
            if self.main_depth:
                self.blocks_main.append(block)
        self.buf, self.prefix = [], ""

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("main", "article"):
            self._flush()
            self.main_depth += 1
            self.found_main = True
        if tag in self.BLOCK:
            self._flush()
            if tag[0] == "h" and len(tag) == 2 and tag[1].isdigit():
                self.prefix = "#" * min(6, int(tag[1]) + 2) + " "
            elif tag == "li":
                self.prefix = "- "
            elif tag == "blockquote":
                self.prefix = "> "
            elif tag == "pre":
                self.in_pre += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            if self.skip:
                self.skip -= 1
            return
        if self.skip:
            return
        if tag in self.BLOCK or tag in ("main", "article"):
            if tag == "pre" and self.in_pre:
                text = "".join(self.buf).strip("\n")
                self.buf, self.prefix = [], ""
                if text.strip():
                    block = "```\n" + text + "\n```"
                    self.blocks_all.append(block)
                    if self.main_depth:
                        self.blocks_main.append(block)
                self.in_pre -= 1
            else:
                self._flush()
        if tag in ("main", "article") and self.main_depth:
            self.main_depth -= 1

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def text(self) -> str:
        self._flush()
        blocks = self.blocks_main if self.found_main and self.blocks_main else self.blocks_all
        out, prev = [], None
        for b in blocks:
            if b != prev:
                out.append(b)
            prev = b
        return "\n\n".join(out)


def extract_main_text(html: str) -> str:
    p = MainText()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return p.text()


def render_full(name: str, summary: str | None, chosen: dict, bodies: dict[str, str], max_bytes: int) -> tuple[str, int, bool]:
    head = [f"# {name}", ""]
    if summary:
        head += [f"> {' '.join(summary.split())}", ""]
    out = "\n".join(head) + "\n"
    size = len(out.encode("utf-8"))
    included, truncated = 0, False
    for s in DISPLAY:
        for e in chosen.get(s, []):
            body = bodies.get(e["url"])
            if not body:
                continue
            chunk = f"## {e['title']}\n\nSource: {e['url']}\n\n{extract_main_text(body)}\n\n"
            n = len(chunk.encode("utf-8"))
            if size + n > max_bytes:
                truncated = True
                break
            out += chunk
            size += n
            included += 1
        if truncated:
            break
    return out.rstrip() + "\n", included, truncated


# --------------------------------------------------------------------------- validate
LINK_RE = re.compile(r"\[((?:\\.|[^\]])+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def validate_text(text: str, findings: list, src: str = "") -> dict:
    lines = text.splitlines()
    info = {"lines": len(lines), "links": 0, "sections": 0, "bytes": len(text.encode("utf-8"))}
    head = text.lstrip("\ufeff")[:2000].lower()
    if head.lstrip().startswith(("<!doctype html", "<html")) or "<head" in head:
        add(findings, "high", "llms_is_html", "content is HTML, not Markdown text (SPA fallback answering /llms.txt?)")
        return info
    first = lines[0].lstrip("\ufeff") if lines else ""
    if not re.match(r"^# \S", first):
        add(findings, "high", "llms_no_h1_first", f"first line must be '# Site name'; got '{first[:60]}'")
    h1s = [l for l in lines if re.match(r"^# \S", l)]
    if len(h1s) > 1 and not src.endswith("llms-full.txt"):
        add(findings, "low", "llms_multiple_h1", f"{len(h1s)} H1 lines; llms.txt should have exactly one")
    info["sections"] = sum(1 for l in lines if l.startswith("## "))
    if not info["sections"]:
        add(findings, "low", "llms_no_sections", "no '## Section' headings with link lists")
    if not any(l.startswith("> ") for l in lines[:6]):
        add(findings, "info", "llms_no_summary", "no '> summary' blockquote after the H1")
    links = LINK_RE.findall(text)
    info["links"] = len(links)
    rel = [u for _, u in links if not re.match(r"^https?://", u, re.I)]
    if rel:
        add(findings, "high", "llms_relative_links", f"{len(rel)} links are not absolute http(s) URLs", samples=rel[:5])
    dup = [u for u, n in Counter(u for _, u in links).items() if n > 1]
    if dup:
        add(findings, "low", "llms_duplicate_links", f"{len(dup)} URLs are listed more than once", samples=dup[:5])
    hosts = Counter(urlparse(u).hostname for _, u in links if u.startswith("http"))
    if len(hosts) > 1:
        main_host = hosts.most_common(1)[0][0]
        other = [u for _, u in links if u.startswith("http") and urlparse(u).hostname != main_host]
        add(findings, "info", "llms_other_hosts", f"{len(other)} links point to hosts other than {main_host}", samples=other[:3])
    bad_items = [l for l in lines if l.startswith("- ") and "](" in l and not re.match(r"^- \[[^\]]+\]\([^)]+\)(: .+)?$", l)]
    if bad_items:
        add(findings, "low", "llms_item_format", f"{len(bad_items)} list items do not follow '- [Title](url): note'", samples=bad_items[:3])
    if info["bytes"] > 2_000_000 and not src.endswith("llms-full.txt"):
        add(findings, "low", "llms_large", f"llms.txt is {info['bytes'] // 1024} KB; keep it a curated index and move full text to llms-full.txt")
    if re.search(r"\[REPLACE|\[INSERT|lorem ipsum|TODO", text, re.I):
        add(findings, "high", "llms_placeholder", "placeholder text found ([REPLACE], lorem ipsum, TODO)")
    return info


def validate(src: str) -> dict:
    findings: list[dict] = []
    res = {"tool": "llms_gen", "mode": "validate", "source": src, "findings": findings}
    if re.match(r"^https?://", src, re.I):
        ca.set_network_mode(urlparse(src).hostname)
        r = ca.fetch(src, ca.UA_DEFAULT, accept="text/plain,text/markdown,*/*")
        res["status"] = r["status"]
        if r["status"] != 200:
            add(findings, "high", "llms_unreachable", f"{src} returned {r['status'] or r['error']}")
            return res
        ct = r["headers"].get("content-type", "")
        res["content_type"] = ct
        if "html" in ct.lower():
            add(findings, "high", "llms_served_as_html", f"served as '{ct}'; serve text/plain (or text/markdown)")
        elif ct and not ct.lower().startswith(("text/plain", "text/markdown", "text/x-markdown")):
            add(findings, "low", "llms_content_type", f"Content-Type '{ct}' (expected text/plain)")
        if r.get("chain"):
            add(findings, "low", "llms_redirect", f"{src} redirects to {r['final_url']}")
        res.update(validate_text(r["body"], findings, src))
        p = urlparse(r["final_url"])
        probe = f"{p.scheme}://{p.netloc}/llms-probe-404-check.txt"
        pr = ca.fetch(probe, ca.UA_DEFAULT, accept="text/plain,*/*")
        if pr["status"] == 200:
            add(findings, "medium", "unknown_urls_return_200", f"{probe} returns 200; unknown URLs should 404 so a fallback page never fakes llms.txt")
    else:
        path = Path(src)
        if not path.is_file():
            add(findings, "high", "llms_unreachable", f"file not found: {src}")
            return res
        res.update(validate_text(path.read_text(encoding="utf-8", errors="replace"), findings, src))
    return res


# --------------------------------------------------------------------------- sources
def pages_from_sitemap(src: str, max_pages: int, delay: float) -> tuple[list[dict], str]:
    import sitemap_gen as sg
    if re.match(r"^https?://", src, re.I):
        ca.set_network_mode(urlparse(src).hostname)
        urls, _ = ca.read_sitemap_urls(src, ca.UA_DEFAULT, limit=max_pages * 3)
    else:
        parsed = sg.parse_sitemap(Path(src).read_text(encoding="utf-8", errors="replace"))
        urls = [e["loc"] for e in parsed["entries"] if e["loc"]]
        if urls:
            ca.set_network_mode(urlparse(urls[0]).hostname)
    urls = [u for u in urls if not APP_PATH.match(urlparse(u).path or "/") and not TAG_PAGE.search(urlparse(u).path or "/")]
    urls.sort(key=lambda u: ((urlparse(u).path or "/") != "/", (urlparse(u).path or "/").count("/"), u))
    pages = []
    origin = None
    for u in urls[:max_pages]:
        origin = origin or f"{urlparse(u).scheme}://{urlparse(u).netloc}"
        pages.append(page_record(ca.fetch(u, ca.UA_DEFAULT)))
        if delay:
            time.sleep(delay)
    return pages, origin or ""


def fetch_home(origin: str) -> dict | None:
    r = ca.fetch(origin.rstrip("/") + "/", ca.UA_DEFAULT)
    return page_record(r) if r.get("status") == 200 else None


def run(pages: list[dict], origin: str, name: str | None, summary: str | None, max_entries: int,
        fetch_missing_home: bool = True) -> dict:
    host = urlparse(origin).hostname
    home = next((pg for pg in pages if ca.normalize(pg.get("final_url") or pg.get("url") or "") == ca.normalize(origin + "/")), None)
    if (home is None or (not home.get("site_name") and "body" not in home)) and fetch_missing_home and not (name and summary):
        fetched = fetch_home(origin)
        if fetched:
            home = {**(home or {}), **{k: v for k, v in fetched.items() if v}}
    findings: list[dict] = []
    brand = detect_brand(pages)
    site_name = name or (home or {}).get("site_name") or brand
    if not site_name:
        site_name = host or "Site"
        add(findings, "medium", "name_fallback", f"no site name found; used the host '{site_name}' (pass --name)")
    summ = summary if summary is not None else (home or {}).get("description") or None
    if not summ:
        add(findings, "low", "no_summary", "homepage has no meta description; blockquote summary omitted (pass --summary)")
    chosen, skipped = build_entries(pages, host, brand or site_name, max_entries)
    text = render_llms(site_name, summ, chosen)
    total = sum(len(v) for v in chosen.values())
    if not total:
        add(findings, "high", "no_entries", "no eligible pages (all non-200, noindex, app or filtered)")
    for reason, n in sorted(skipped.items()):
        if reason == "generic_description_dropped":
            add(findings, "info", "generic_descriptions_omitted",
                f"{n} entries share a site-wide meta description; it was omitted for them (write page-specific descriptions)")
        else:
            add(findings, "info", f"skipped_{reason}", f"{n} page(s) skipped: {reason.replace('_', ' ')}")
    return {"name": site_name, "summary": summ, "chosen": chosen, "text": text, "entries": total,
            "skipped": dict(skipped), "findings": findings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-crawl", help="crawl_audit.py JSON output")
    src.add_argument("--sitemap", help="sitemap URL or local file")
    src.add_argument("--url", help="start URL for a live crawl")
    src.add_argument("--validate", metavar="FILE_OR_URL", help="validate an existing llms.txt")
    ap.add_argument("--name", help="site name for the H1 (default: og:site_name / title brand)")
    ap.add_argument("--summary", help="one-paragraph summary for the blockquote (default: homepage meta description)")
    ap.add_argument("--max-pages", type=int, default=60, help="pages to crawl/fetch (default 60)")
    ap.add_argument("--max-entries", type=int, default=60, help="max link entries in llms.txt (default 60)")
    ap.add_argument("--full", action="store_true", help="also build llms-full.txt from each listed page's main text")
    ap.add_argument("--max-full-bytes", type=int, default=2_000_000, help="cap for llms-full.txt (default 2 MB)")
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--allow-private", action="store_true")
    ap.add_argument("--offline", action="store_true", help="never fetch (crawl JSON source only; no homepage lookup, no --full)")
    ap.add_argument("--out", help="directory to write llms.txt (and llms-full.txt), e.g. public/")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if a.validate:
        res = validate(a.validate)
        if a.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"# llms.txt validation: {a.validate}\n")
            print(f"- links: {res.get('links', 0)} | sections: {res.get('sections', 0)} | bytes: {res.get('bytes', 0)}"
                  + (f" | content-type: {res['content_type']}" if res.get("content_type") else "") + "\n")
            for f in res["findings"] or [{"severity": "info", "code": "ok", "message": "no problems found"}]:
                print(f"- **{f['severity']}** `{f['code']}`: {f['message']}")
        return 1 if any(ca.SEVERITY_ORDER[f["severity"]] <= 1 for f in res["findings"]) else 0

    if a.from_crawl:
        data = json.loads(Path(a.from_crawl).read_text(encoding="utf-8"))
        pages, origin = data.get("pages", []), data.get("origin", "")
        if not a.offline:
            ca.set_network_mode(urlparse(origin).hostname, a.allow_private)
    elif a.sitemap:
        pages, origin = pages_from_sitemap(a.sitemap, a.max_pages, a.delay)
    else:
        data = ca.crawl(a.url, a.max_pages, ca.UA_DEFAULT, a.delay, None, True, a.allow_private)
        pages, origin = data.get("pages", []), data.get("origin", "")
    res = run(pages, origin, a.name, a.summary, a.max_entries, fetch_missing_home=not a.offline)

    full_text = None
    if a.full:
        if a.offline:
            add(res["findings"], "info", "full_skipped_offline", "--full needs page fetches; skipped because of --offline")
        else:
            bodies = {}
            for s in DISPLAY:
                for e in res["chosen"].get(s, []):
                    pg = next((p for p in pages if ca.normalize(p.get("final_url") or "") == e["url"] and p.get("body")), None)
                    if pg:
                        bodies[e["url"]] = pg["body"]
                    else:
                        r = ca.fetch(e["url"], ca.UA_DEFAULT)
                        if r["status"] == 200:
                            bodies[e["url"]] = r["body"]
                        if a.delay:
                            time.sleep(a.delay)
            full_text, included, truncated = render_full(res["name"], res["summary"], res["chosen"], bodies, a.max_full_bytes)
            res["full"] = {"pages": included, "bytes": len(full_text.encode("utf-8")), "truncated": truncated}
            if truncated:
                add(res["findings"], "info", "full_truncated", f"llms-full.txt stopped at {a.max_full_bytes} bytes ({included} pages)")
    written = []
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "llms.txt").write_text(res["text"], encoding="utf-8", newline="\n")
        written.append(str(out / "llms.txt"))
        if full_text:
            (out / "llms-full.txt").write_text(full_text, encoding="utf-8", newline="\n")
            written.append(str(out / "llms-full.txt"))
    if a.json:
        print(json.dumps({"tool": "llms_gen", "mode": "generate", "name": res["name"], "entries": res["entries"],
                          "llms_txt": res["text"], "full": res.get("full"), "written": written,
                          "findings": res["findings"]}, indent=2, ensure_ascii=False))
    else:
        print(res["text"], end="")
        if full_text and not a.out:
            print("\n<!-- llms-full.txt -->\n" + full_text, end="")
        for f in res["findings"]:
            print(f"[{f['severity']}] {f['code']}: {f['message']}", file=sys.stderr)
        for w in written:
            print(f"[saved] {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
