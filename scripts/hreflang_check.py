#!/usr/bin/env python3
"""hreflang_check.py - stdlib-only hreflang cluster validator (HTML <link> + XML sitemap xhtml:link).

Collects hreflang annotations from <link rel="alternate" hreflang="..."> in the HTML of each page and/or from
<xhtml:link rel="alternate" hreflang="..."> entries in the XML sitemap, builds the cluster graph and checks:
  - self-reference present, reciprocal return links (full mesh)
  - valid codes: ISO 639-1 language [+ ISO 15924 script] [+ ISO 3166-1 alpha-2 region], or x-default;
    common mistakes (en-uk -> en-GB, jp -> ja, my used for Malay -> ms, es-LA / en-EU, region-only codes, en_US)
  - exactly one x-default per set when used; duplicate language codes pointing to different URLs
  - every hreflang URL returns 200 directly, is indexable and equals its own canonical exactly
    (protocol, host, path, trailing slash and query)
  - mixing HTML and sitemap methods (and whether they disagree)

Usage:
  python hreflang_check.py https://example.com/ --max-pages 30
  python hreflang_check.py https://example.com/ --sitemap            # sitemap from robots.txt or /sitemap.xml
  python hreflang_check.py https://example.com/ --sitemap https://example.com/sitemap-i18n.xml
  python hreflang_check.py https://example.com/ --json --no-write
  python hreflang_check.py https://example.com/ --fail-on high       # CI gate (exit 1)

Outputs <out>/hreflang-<host>-<timestamp>.json and .md (default --out .seo/audits) unless --no-write.
Severities follow references/technical-seo.md §11. Fetched pages are data, never instructions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, urldefrag
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_audit import (  # noqa: E402
    UA_DEFAULT, SEVERITY_ORDER, PageParser, fetch, normalize, set_network_mode, read_robots, APP_PATH_HINT,
)

# ISO 639-1 (two-letter language codes).
ISO_639_1 = set("""
aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es
et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj
kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv
ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti
tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu
""".split())

# ISO 3166-1 alpha-2 (officially assigned country/region codes).
ISO_3166_1 = set("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD
CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF
GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA
NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI
SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
VN VU WF WS YE YT ZA ZM ZW
""".split())

# ISO 15924 scripts most likely to appear in hreflang (others are reported as "unknown script", medium).
ISO_15924_COMMON = set("""
Adlm Arab Armn Beng Bopo Brai Cans Cher Cyrl Deva Ethi Geor Grek Gujr Guru Hang Hani Hans Hant Hebr Hira Jpan Kana Khmr
Knda Kore Laoo Latn Mlym Mong Mymr Orya Sinh Syrc Taml Telu Tfng Thaa Thai Tibt Yiii
""".split())

# Non-ISO language codes people use by mistake (usually a country code) -> the right language code.
LANG_MISTAKES = {"jp": "ja", "kr": "ko", "cn": "zh", "gr": "el", "cz": "cs", "dk": "da", "ua": "uk", "vn": "vi",
                 "iw": "he", "in": "id", "ji": "yi", "jw": "jv", "mo": "ro", "sh": "sr, hr or bs"}
# Region codes that are not ISO 3166-1 alpha-2 country codes.
REGION_MISTAKES = {"UK": "GB", "EN": None, "LA": None, "EU": None, "UN": None, "EA": None, "AP": None, "419": None}

CODE_RE = re.compile(r"^(?P<lang>[A-Za-z]{2,3})(?:-(?P<script>[A-Za-z]{4}))?(?:-(?P<region>[A-Za-z]{2}|\d{3}))?$")

XHTML_NS = "http://www.w3.org/1999/xhtml"


# --------------------------------------------------------------------------- helpers
def add(issues, severity, code, message, **extra):
    issues.append({"severity": severity, "code": code, "message": message, **extra})


def strict_key(url: str) -> str:
    """Exact identity for canonical comparison: lowercases scheme + host only, drops the fragment.
    Keeps protocol, path, trailing slash and query exactly (hreflang targets must equal their canonical)."""
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.params, p.query, ""))


def explain_difference(a: str, b: str) -> str:
    pa, pb = urlparse(a), urlparse(b)
    diffs = []
    if pa.scheme.lower() != pb.scheme.lower():
        diffs.append(f"protocol {pa.scheme} vs {pb.scheme}")
    if pa.netloc.lower() != pb.netloc.lower():
        diffs.append(f"host {pa.netloc} vs {pb.netloc}")
    if (pa.path or "/").rstrip("/") == (pb.path or "/").rstrip("/") and (pa.path or "/") != (pb.path or "/"):
        diffs.append("trailing slash")
    elif (pa.path or "/") != (pb.path or "/"):
        diffs.append("path")
    if pa.query != pb.query:
        diffs.append("query string")
    return ", ".join(diffs) or "different URL"


# --------------------------------------------------------------------------- code validation
def validate_code(code: str, page_lang: str | None = None) -> list[dict]:
    """Validate one hreflang value. Returns a list of issues (empty when valid)."""
    issues: list[dict] = []
    raw = (code or "").strip()
    if not raw:
        add(issues, "high", "hreflang_empty", "Empty hreflang value")
        return issues
    if raw.lower() == "x-default":
        return issues
    if "_" in raw:
        fixed = raw.replace("_", "-")
        add(issues, "high", "hreflang_invalid_code", f"'{raw}' uses an underscore; hreflang uses hyphens (use '{fixed}')",
            hreflang=raw, suggestion=fixed)
        return issues
    m = CODE_RE.match(raw)
    if not m:
        add(issues, "high", "hreflang_invalid_code",
            f"'{raw}' is not a valid hreflang value (language[-Script][-REGION], e.g. en, en-GB, zh-Hant-TW, or x-default)",
            hreflang=raw)
        return issues
    lang = m.group("lang").lower()
    script = m.group("script")
    region = (m.group("region") or "").upper() or None

    if lang not in ISO_639_1:
        if lang in LANG_MISTAKES:
            sug = LANG_MISTAKES[lang]
            add(issues, "high", "hreflang_invalid_code",
                f"'{raw}': '{lang}' is not an ISO 639-1 language code (use '{sug}'{'-' + region if region and ' ' not in sug else ''})",
                hreflang=raw, suggestion=sug)
        elif len(lang) == 2 and lang.upper() in ISO_3166_1:
            add(issues, "high", "hreflang_region_only",
                f"'{raw}': '{lang}' looks like a country code; hreflang must start with a language (e.g. 'en-{lang.upper()}')",
                hreflang=raw)
        elif len(lang) == 3:
            add(issues, "high", "hreflang_invalid_code",
                f"'{raw}': three-letter language codes are not supported; Google expects ISO 639-1 (two letters)", hreflang=raw)
        else:
            add(issues, "high", "hreflang_invalid_code", f"'{raw}': unknown language code '{lang}'", hreflang=raw)
        return issues

    if script and script.title() not in ISO_15924_COMMON:
        add(issues, "medium", "hreflang_unknown_script", f"'{raw}': '{script}' is not a common ISO 15924 script code", hreflang=raw)

    if region:
        if region in REGION_MISTAKES:
            sug = REGION_MISTAKES[region]
            why = {"LA": "LA is Laos; Latin America has no ISO 3166-1 code (list countries such as es-MX, es-AR, or use 'es')",
                   "EU": "EU is not an ISO 3166-1 country code (list countries, or use the bare language)",
                   "419": "419 (UN M.49 Latin America) is not supported; list countries such as es-MX, or use 'es'",
                   }.get(region, f"{region} is not an ISO 3166-1 alpha-2 country code")
            if region == "LA" and lang != "es":
                why = None  # e.g. lo-LA (Lao in Laos) is legitimate
            if why:
                text = f"'{raw}': {why}" + (f" (use '{lang}-{sug}')" if sug else "")
                add(issues, "high", "hreflang_invalid_code", text, hreflang=raw, suggestion=f"{lang}-{sug}" if sug else lang)
        elif region not in ISO_3166_1:
            add(issues, "high", "hreflang_invalid_code",
                f"'{raw}': '{region}' is not an ISO 3166-1 alpha-2 region code", hreflang=raw)

    if lang == "my":
        pl = (page_lang or "").lower()
        if region == "MY" or pl.startswith("ms"):
            add(issues, "high", "hreflang_my_for_malay",
                f"'{raw}': 'my' is Burmese. Malay is 'ms' (use 'ms{'-' + region if region else ''}')",
                hreflang=raw, suggestion="ms" + ("-" + region if region else ""))
        else:
            add(issues, "medium", "hreflang_my_check",
                f"'{raw}': 'my' is Burmese. If this page is Malay, use 'ms'", hreflang=raw)
    if lang == "eu" and not region and (page_lang or "").lower()[:2] not in ("", "eu"):
        add(issues, "medium", "hreflang_eu_check",
            f"'{raw}': 'eu' is Basque, not 'Europe'. The page's <html lang> is '{page_lang}'", hreflang=raw)
    return issues


# --------------------------------------------------------------------------- collection
def parse_html(res: dict) -> dict:
    """Extract hreflang links, canonical, robots and <html lang> from a fetch() result."""
    url = res["final_url"]
    info = {"url": res["url"], "final_url": url, "status": res["status"], "error": res.get("error"),
            "redirected": bool(res.get("chain")), "noindex": False, "canonical": None, "lang": None, "html": [],
            "relative_hrefs": []}
    if res["status"] != 200 or "html" not in res.get("headers", {}).get("content-type", "html"):
        return info
    p = PageParser()
    try:
        p.feed(res["body"])
    except Exception:  # malformed HTML should not stop the check
        pass
    x_robots = res.get("headers", {}).get("x-robots-tag", "")
    meta_robots = " ".join(filter(None, [p.meta("robots"), p.meta("googlebot")])).lower()
    info["noindex"] = "noindex" in meta_robots or "noindex" in x_robots.lower()
    info["lang"] = p.lang
    canon = p.link_rel("canonical")
    if canon and canon[0].get("href"):
        info["canonical"] = urljoin(url, canon[0]["href"].strip())
    else:
        hdr = re.search(r'<([^>]+)>\s*;\s*rel="?canonical"?', res.get("headers", {}).get("link", ""), re.I)
        if hdr:
            info["canonical"] = urljoin(url, hdr.group(1).strip())
    base = urljoin(url, p.base_href) if p.base_href else url
    for l in p.link_rel("alternate"):
        if "hreflang" not in l:
            continue
        href = (l.get("href") or "").strip()
        if not href:
            continue
        absu = urljoin(base, href)
        if not urlparse(href).scheme:
            info["relative_hrefs"].append(href)
        info["html"].append({"hreflang": l.get("hreflang", "").strip(), "href": absu})
    # HTTP Link header hreflang (rare, used for PDFs) counts as the HTML method here.
    for m in re.finditer(r'<([^>]+)>\s*;\s*rel="?alternate"?\s*;\s*hreflang="?([^",;]+)"?', res.get("headers", {}).get("link", ""), re.I):
        info["html"].append({"hreflang": m.group(2).strip(), "href": urljoin(url, m.group(1).strip())})
    return info


def parse_sitemap_xml(text: str) -> tuple[dict[str, list[dict]], list[str], list[str]]:
    """Return ({loc: [{hreflang, href}]}, child_sitemaps, problems). Refuses DOCTYPE/ENTITY (XXE / entity expansion)."""
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:5000], re.I):
        return {}, [], ["sitemap contains a DOCTYPE/ENTITY declaration; refused to parse"]
    try:
        root = ET.fromstring(text.encode("utf-8", errors="replace"))
    except ET.ParseError as e:
        return {}, [], [f"sitemap is not valid XML: {e}"]
    local = lambda el: el.tag.split("}")[-1]  # noqa: E731
    if local(root) == "sitemapindex":
        kids = [el.text.strip() for el in root.iter() if local(el) == "loc" and el.text]
        return {}, kids, []
    entries: dict[str, list[dict]] = {}
    problems = []
    for u in root:
        if local(u) != "url":
            continue
        loc = next((c.text.strip() for c in u if local(c) == "loc" and c.text), None)
        if not loc:
            continue
        alts = []
        for c in u:
            if local(c) == "link" and (c.get("rel") or "").lower() == "alternate" and c.get("hreflang") is not None:
                if not c.tag.startswith("{" + XHTML_NS + "}"):
                    problems.append(f"{loc}: <link> without the xhtml namespace ({XHTML_NS}) is ignored by Google")
                    continue
                alts.append({"hreflang": c.get("hreflang", "").strip(), "href": (c.get("href") or "").strip()})
        if alts:
            entries[loc] = alts
    return entries, [], problems


def read_sitemap_hreflang(sm_url: str, ua: str, fetcher=fetch) -> tuple[dict[str, list[dict]], list[str]]:
    r = fetcher(sm_url, ua, accept="application/xml,text/xml,*/*")
    if r["status"] != 200:
        return {}, [f"{sm_url} returned {r['status'] or r.get('error')}"]
    entries, kids, problems = parse_sitemap_xml(r["body"])
    problems = [f"{sm_url}: {p}" for p in problems]
    parent = (urlparse(sm_url).hostname or "").removeprefix("www.")
    for child in kids[:50]:  # one level of sitemap index only
        ch = (urlparse(child).hostname or "").removeprefix("www.")
        if ch != parent and not ch.endswith("." + parent):
            problems.append(f"skipped cross-host child sitemap {child}")
            continue
        cr = fetcher(child, ua, accept="application/xml,text/xml,*/*")
        if cr["status"] != 200:
            problems.append(f"{child} returned {cr['status'] or cr.get('error')}")
            continue
        e2, _, p2 = parse_sitemap_xml(cr["body"])
        entries.update(e2)
        problems += [f"{child}: {p}" for p in p2]
    return entries, problems


# --------------------------------------------------------------------------- analysis
def analyze(pages: dict[str, dict], sitemap: dict[str, list[dict]]) -> dict:
    """pages: {strict_key: parse_html() result}; sitemap: {loc: [{hreflang, href}]}. Returns {issues, clusters, sets}."""
    issues: list[dict] = []
    sm = {strict_key(k): v for k, v in sitemap.items()}
    by_final = {strict_key(p["final_url"]): p for p in pages.values()}
    page_of = lambda key: pages.get(key) or by_final.get(key)  # noqa: E731

    # effective annotation set per source URL
    sets: dict[str, dict] = {}
    for key in set(pages) | set(sm):
        pg = pages.get(key)
        if pg and pg.get("redirected"):
            continue  # annotations are read from the final URL's entry
        html_set = (pg or {}).get("html", [])
        sm_set = sm.get(key, [])
        if not html_set and not sm_set:
            continue
        method = "html+sitemap" if html_set and sm_set else ("html" if html_set else "sitemap")
        url = pg["final_url"] if pg else next(k for k in sitemap if strict_key(k) == key)
        sets[key] = {"url": url, "method": method, "entries": html_set or sm_set, "lang": (pg or {}).get("lang")}
        if html_set and sm_set:
            a = {(e["hreflang"].lower(), strict_key(e["href"])) for e in html_set}
            b = {(e["hreflang"].lower(), strict_key(e["href"])) for e in sm_set}
            if a == b:
                add(issues, "low", "hreflang_mixed_methods",
                    "hreflang is declared in both the HTML and the sitemap; pick one method to keep them from drifting", url=url)
            else:
                add(issues, "medium", "hreflang_methods_disagree",
                    f"HTML and sitemap hreflang differ (HTML only: {len(a - b)}, sitemap only: {len(b - a)}); keep one source of truth",
                    url=url, html_only=sorted(f"{c} {u}" for c, u in a - b)[:5], sitemap_only=sorted(f"{c} {u}" for c, u in b - a)[:5])
        if pg and pg.get("relative_hrefs"):
            add(issues, "medium", "hreflang_relative_url",
                f"{len(pg['relative_hrefs'])} hreflang href(s) are relative; use fully-qualified URLs (protocol + host)",
                url=url, samples=pg["relative_hrefs"][:3])

    # per-set checks
    edges: dict[str, set[str]] = defaultdict(set)
    targets: dict[str, str] = {}
    code_issues: dict[tuple, dict] = {}
    for key, s in sets.items():
        url, entries = s["url"], s["entries"]
        seen_codes: dict[str, set[str]] = defaultdict(set)
        xdef = []
        for e in entries:
            code = e["hreflang"]
            for iss in validate_code(code, s["lang"]):
                ck = (iss["code"], iss.get("hreflang"))
                if ck not in code_issues:
                    iss["url"] = url
                    iss["pages"] = []
                    code_issues[ck] = iss
                    issues.append(iss)
                if url not in code_issues[ck]["pages"]:
                    code_issues[ck]["pages"].append(url)
            if not e["href"]:
                add(issues, "high", "hreflang_empty_href", f"hreflang '{code}' has an empty href", url=url)
                continue
            tk = strict_key(e["href"])
            targets.setdefault(tk, e["href"])
            seen_codes[code.lower()].add(tk)
            if code.lower() == "x-default":
                xdef.append(tk)
            if tk != key:
                edges[key].add(tk)
                edges[tk]  # noqa: B018 - make sure the node exists
        for code, urls in seen_codes.items():
            if len(urls) > 1 and code != "x-default":
                add(issues, "high", "hreflang_duplicate_code",
                    f"hreflang '{code}' points to {len(urls)} different URLs in one set; each code must map to one URL",
                    url=url, targets=sorted(urls)[:5])
        if len(set(xdef)) > 1:
            add(issues, "high", "hreflang_multiple_x_default", f"{len(set(xdef))} different x-default URLs in one set; use exactly one",
                url=url, targets=sorted(set(xdef))[:5])
        own = {strict_key(e["href"]) for e in entries if e["href"]}
        final_key = strict_key(page_of(key)["final_url"]) if page_of(key) else key
        if key not in own and final_key not in own:
            add(issues, "high", "hreflang_missing_self",
                "The set does not include the page itself; every version must list itself as well as all other versions", url=url)
        # reciprocal return links
        for e in entries:
            if not e["href"]:
                continue
            tk = strict_key(e["href"])
            if tk in (key, final_key):
                continue
            tset = sets.get(tk)
            if tset is None:
                tp = page_of(tk)
                if tp is None:
                    add(issues, "info", "hreflang_return_unverified",
                        f"Return link from {e['href']} not checked (not fetched; raise --max-pages)", url=url, target=e["href"])
                    continue
                if tp.get("redirected"):
                    continue  # reported as a target problem below
                if tp.get("status") != 200:
                    continue
                add(issues, "high", "hreflang_missing_return",
                    f"{e['href']} ({e['hreflang']}) has no hreflang annotations, so it does not link back; "
                    "Google may ignore this pair's annotations", url=url, target=e["href"])
                continue
            back = {strict_key(x["href"]) for x in tset["entries"] if x["href"]}
            if key not in back and final_key not in back:
                add(issues, "high", "hreflang_missing_return",
                    f"{e['href']} ({e['hreflang']}) does not link back to this URL; "
                    "Google may ignore this pair's annotations (return links must be reciprocal)", url=url, target=e["href"])

    # target checks: 200, no redirect, indexable, equal to own canonical
    for tk, href in sorted(targets.items()):
        tp = pages.get(tk)
        if tp is None:
            continue
        if tp.get("status") == 0:
            add(issues, "high", "hreflang_target_unreachable", f"hreflang URL could not be fetched: {tp.get('error')}", url=href)
        elif tp.get("redirected"):
            geo = strict_key(tp["final_url"]) in targets
            add(issues, "high", "hreflang_target_redirects",
                f"hreflang URL redirects to {tp['final_url']}; " + (
                    "the target is another version in the set, so this looks like a locale/IP redirect. Serve every "
                    "hreflang URL without forced redirects (Googlebot mostly crawls from the US and may never see the other versions); "
                    "suggest a locale banner instead" if geo else "reference the final canonical URL directly"), url=href)
        elif tp.get("status") != 200:
            add(issues, "high", "hreflang_target_non_200", f"hreflang URL returns {tp['status']}", url=href)
        else:
            if tp.get("noindex"):
                add(issues, "high", "hreflang_target_noindex", "hreflang URL is noindex; only indexable URLs belong in a set", url=href)
            canon = tp.get("canonical")
            if canon and strict_key(canon) != tk:
                add(issues, "high", "hreflang_target_not_canonical",
                    f"hreflang URL is not its own canonical (canonical: {canon}; differs by {explain_difference(href, canon)})",
                    url=href, canonical=canon)
            elif not canon:
                add(issues, "low", "hreflang_target_no_canonical",
                    "hreflang URL has no rel=canonical; add a self-referencing canonical", url=href)
            if APP_PATH_HINT.match(urlparse(href).path or "/"):
                add(issues, "medium", "hreflang_target_app_route", "hreflang points at an app/auth route", url=href)

    # clusters (connected components)
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, bs in edges.items():
        find(a)
        for b in bs:
            parent[find(a)] = find(b)
    for k in sets:
        find(k)
    groups: dict[str, list[str]] = defaultdict(list)
    for k in parent:
        groups[find(k)].append(k)
    clusters = []
    for members in groups.values():
        codes = Counter()
        has_xdef = False
        for m in members:
            for e in sets.get(m, {}).get("entries", []):
                codes[e["hreflang"].lower()] += 1
                has_xdef |= e["hreflang"].lower() == "x-default"
        urls = sorted(sets[m]["url"] if m in sets else targets.get(m, m) for m in members)
        clusters.append({"size": len(members), "urls": urls[:50], "codes": sorted(codes), "has_x_default": has_xdef,
                         "methods": sorted({sets[m]["method"] for m in members if m in sets})})
        if not has_xdef and len(members) > 1:
            add(issues, "low", "hreflang_no_x_default",
                "Cluster has no x-default; add one for users whose language/region matches no version (optional but recommended)",
                url=urls[0], cluster_size=len(members))
    clusters.sort(key=lambda c: -c["size"])
    # lang attribute sanity (helps catch 'my' vs 'ms' style mistakes)
    for key, s in sets.items():
        selfcodes = [e["hreflang"] for e in s["entries"] if e["href"] and strict_key(e["href"]) == key
                     and e["hreflang"].lower() != "x-default"]
        if s["lang"] and selfcodes:
            pl = s["lang"].split("-")[0].lower()
            if all(c.split("-")[0].lower() != pl for c in selfcodes):
                add(issues, "low", "hreflang_lang_mismatch",
                    f"<html lang=\"{s['lang']}\"> disagrees with this page's own hreflang ({', '.join(selfcodes)})", url=s["url"])
    return {"issues": issues, "clusters": clusters, "sets": sets}


# --------------------------------------------------------------------------- run
def run(start: str, max_pages: int = 30, sitemap: str | None = None, ua: str = UA_DEFAULT, delay: float = 0.2,
        allow_private: bool = False, fetcher=fetch) -> dict:
    sp = urlparse(start)
    if sp.scheme not in ("http", "https"):
        raise SystemExit("start URL must be http(s)")
    set_network_mode(sp.hostname, allow_private)
    origin = f"{sp.scheme}://{sp.netloc}"
    problems: list[str] = []
    sm_entries: dict[str, list[dict]] = {}
    if sitemap is not None:
        candidates = [sitemap] if sitemap else []
        if not candidates:
            try:
                candidates = read_robots(origin, ua)["sitemaps"][:5] if fetcher is fetch else []
            except Exception:
                candidates = []
            candidates = candidates or [origin + "/sitemap.xml"]
        for sm_url in candidates:
            e, pr = read_sitemap_hreflang(sm_url, ua, fetcher)
            sm_entries.update(e)
            problems += pr

    queue: list[str] = [start]
    for loc, alts in sm_entries.items():
        queue.append(loc)
        queue += [a["href"] for a in alts if a["href"]]
    pages: dict[str, dict] = {}
    fetched = 0
    i = 0
    while i < len(queue) and fetched < max_pages:
        u = queue[i]
        i += 1
        k = strict_key(u)
        if k in pages:
            continue
        res = fetcher(u, ua)
        fetched += 1
        info = parse_html(res)
        pages[k] = info
        fk = strict_key(info["final_url"])
        if fk != k and fk not in pages and info["status"] == 200:
            pages[fk] = dict(info, url=info["final_url"], redirected=False)  # the final URL carries the annotations
        for e in info["html"]:
            if e["href"] and strict_key(e["href"]) not in pages:
                queue.append(e["href"])
        if delay and fetcher is fetch:
            time.sleep(delay)
    pending = sum(1 for u in queue[i:] if strict_key(u) not in pages)
    result = analyze(pages, sm_entries)
    issues = result["issues"]
    for pr in problems[:10]:
        add(issues, "medium", "sitemap_problem", pr)
    if not result["sets"]:
        add(issues, "info", "hreflang_none", "No hreflang annotations found (fine for single-language sites)", url=start)
    counts = Counter(iss["severity"] for iss in issues)
    return {
        "tool": "hreflang_check", "version": 1, "start": start, "origin": origin,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_fetched": fetched, "complete": pending == 0, "sitemap_entries": len(sm_entries),
        "sets": len(result["sets"]), "clusters": result["clusters"],
        "summary": {"counts": dict(counts)},
        "issues": sorted(issues, key=lambda x: (SEVERITY_ORDER[x["severity"]], x["code"])),
        "pages": [{k: v for k, v in p.items() if k != "relative_hrefs"} for p in pages.values()],
    }


def to_markdown(r: dict) -> str:
    c = r["summary"]["counts"]
    L = [f"# hreflang check: {r['origin']}", "",
         f"- Checked: {r['checked_at']} | pages fetched: {r['pages_fetched']} ({'complete' if r['complete'] else 'partial; raise --max-pages'})"
         f" | annotated URLs: {r['sets']} | sitemap entries with hreflang: {r['sitemap_entries']}",
         "- " + " ".join(f"{k}: {c.get(k, 0)}" for k in SEVERITY_ORDER), "",
         "## Clusters", "", "| Size | Codes | x-default | Method | Example URL |", "|---|---|---|---|---|"]
    for cl in r["clusters"][:30]:
        L.append(f"| {cl['size']} | {', '.join(cl['codes'])[:80]} | {'yes' if cl['has_x_default'] else 'no'} | "
                 f"{', '.join(cl['methods']) or '-'} | {cl['urls'][0] if cl['urls'] else '-'} |")
    L += ["", "## Issues (worst first)", "", "| Severity | Code | URL | Message |", "|---|---|---|---|"]
    for iss in r["issues"]:
        if iss["severity"] == "info" and iss["code"] == "hreflang_return_unverified":
            continue
        L.append(f"| {iss['severity']} | `{iss['code']}` | {iss.get('url', '-')} | {iss['message'].replace('|', '/')[:200]} |")
    unver = sum(1 for i in r["issues"] if i["code"] == "hreflang_return_unverified")
    if unver:
        L += ["", f"{unver} return links were not verified because the target was not fetched (raise --max-pages)."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="start URL (a page that carries hreflang, or the home page)")
    ap.add_argument("--sitemap", nargs="?", const="", default=None,
                    help="also read hreflang from the XML sitemap (no value = robots.txt Sitemap: lines or /sitemap.xml)")
    ap.add_argument("--max-pages", type=int, default=30, help="max pages to fetch (default 30)")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--allow-private", action="store_true", help="allow private/internal addresses for a public-looking start host")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--fail-on", choices=["critical", "high", "medium"], help="exit 1 if any issue at/above this severity")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = run(a.url, a.max_pages, a.sitemap, a.ua, a.delay, a.allow_private)
    md = to_markdown(r)
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        host = urlparse(r["origin"]).netloc.replace(":", "_")
        (out / f"hreflang-{host}-{stamp}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / f"hreflang-{host}-{stamp}.md").write_text(md, encoding="utf-8")
        print(f"[saved] {out / f'hreflang-{host}-{stamp}.md'}", file=sys.stderr)
    print(json.dumps(r, indent=2, ensure_ascii=False) if a.json else md)
    if a.fail_on:
        limit = SEVERITY_ORDER[a.fail_on]
        if any(SEVERITY_ORDER[s] <= limit and n for s, n in r["summary"]["counts"].items()):
            sys.exit(1)


if __name__ == "__main__":
    main()
