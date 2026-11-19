#!/usr/bin/env python3
"""entity_check.py - entity presence and consistency on Wikidata / Wikipedia (free APIs). Stdlib only, Python 3.10+.

  python entity_check.py --name "Acme" --domain acme.com
  python entity_check.py --name "Acme" --domain acme.com --sameas https://x.com/acme,https://github.com/acme
  python entity_check.py --qid Q123 --domain acme.com [--lang ms] [--founded 2019] [--hq "Kuala Lumpur"] [--json]

What it does
  1. Wikidata wbsearchentities (English + optional --lang) -> candidates, then wbgetentities for their facts.
     A candidate is selected automatically ONLY when its official website (P856) matches --domain. Otherwise the
     candidates are listed with descriptions; confirm the right one and rerun with --qid. Generic names are never
     auto-picked.
  2. Wikipedia exact title lookup with redirects (action=query&titles=...&redirects=1) and its linked Wikidata item.
  3. Facts: Q-id, official website (P856) vs domain, X (P2002), Facebook (P2013), LinkedIn company (P4264),
     YouTube channel (P2397), GitHub (P2037), inception (P571), headquarters (P159).
  4. Compares with your site: --sameas (comma list) or the homepage JSON-LD sameAs (fetched via crawl_audit.fetch),
     plus optional --founded / --hq, and flags inconsistencies.

Wikimedia API etiquette: descriptive User-Agent, one request at a time with a pause, backoff on 429/5xx.
Exit codes: 0 ok (findings may exist), 1 API unreachable, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import socket
import sys
import time
from pathlib import Path
from urllib import error, request
from urllib.parse import quote, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_audit import PageParser, fetch, jsonld_nodes, set_network_mode  # noqa: E402

UA = "super-seo/1.0 (+https://github.com/digitalscapemy/super-seo)"
WD_API = "https://www.wikidata.org/w/api.php"
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
PAUSE = 1.0  # seconds between Wikimedia requests
SOCIAL = {  # property: (label, url template, host aliases, case-sensitive id?)
    "P2002": ("X (Twitter)", "https://x.com/{}", ("x.com", "twitter.com"), False),
    "P2013": ("Facebook", "https://www.facebook.com/{}", ("facebook.com", "fb.com"), False),
    "P4264": ("LinkedIn company", "https://www.linkedin.com/company/{}", ("linkedin.com",), False),
    "P2397": ("YouTube channel", "https://www.youtube.com/channel/{}", ("youtube.com",), True),
    "P2037": ("GitHub", "https://github.com/{}", ("github.com",), False),
}
FACT_PROPS = ["P31", "P856", "P571", "P159"] + list(SOCIAL)
COI = ("Wikipedia: do not write or edit an article about your own company (conflict of interest). Disclose the COI, "
       "propose changes on the article's talk page or submit a draft via Articles for Creation, and rely on independent, "
       "reliable sources. Wikidata accepts edits from interested parties when every statement is referenced and neutral.")
NOTABILITY = ("Create a Wikidata item only when notability is plausible: the company is a clearly identifiable entity "
              "described by serious, publicly available independent references (news coverage, books, official registries), "
              "or it is needed to link existing items. A company website and social profiles alone are not enough.")

_sleep = time.sleep
_last_call = [0.0]


class ApiError(Exception):
    pass


# --------------------------------------------------------------------------- HTTP
def api_get(url: str, params: dict, timeout: float = 20.0, retries: int = 4) -> dict:
    """GET a Wikimedia API with polite pacing and backoff."""
    full = url + "?" + urlencode({**params, "format": "json", "formatversion": "2"})
    for attempt in range(retries + 1):
        wait = PAUSE - (time.monotonic() - _last_call[0])
        if wait > 0:
            _sleep(wait)
        _last_call[0] = time.monotonic()
        req = request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                _sleep(max(2 ** attempt + random.uniform(0, 1), float(ra) if ra and ra.isdigit() else 0))
                continue
            raise ApiError(f"{urlparse(url).hostname} -> HTTP {e.code}") from None
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            if attempt < retries:
                _sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise ApiError(f"{urlparse(url).hostname} -> network error: {getattr(e, 'reason', e)}") from None
        except json.JSONDecodeError:
            raise ApiError(f"{urlparse(url).hostname} -> non-JSON response") from None
        if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag" and attempt < retries:
            _sleep(5 + random.uniform(0, 1))
            continue
        if isinstance(data, dict) and data.get("error"):
            raise ApiError(f"{urlparse(url).hostname} -> {data['error'].get('code')}: {data['error'].get('info')}")
        return data
    raise ApiError("request failed")


def search_entities(name: str, lang: str, limit: int = 7) -> list[dict]:
    d = api_get(WD_API, {"action": "wbsearchentities", "search": name, "language": lang, "uselang": lang,
                         "type": "item", "limit": limit})
    return [{"id": s.get("id"), "label": s.get("label"), "description": s.get("description"),
             "match": (s.get("match") or {}).get("text")} for s in d.get("search") or []]


def get_entities(ids: list[str], lang: str = "en") -> dict:
    out = {}
    for i in range(0, len(ids), 50):
        d = api_get(WD_API, {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]),
                             "props": "labels|descriptions|claims|sitelinks", "languages": f"{lang}|en"})
        out.update(d.get("entities") or {})
    return out


def wikipedia_lookup(title: str, lang: str = "en") -> dict:
    d = api_get(f"https://{lang}.wikipedia.org/w/api.php",
                {"action": "query", "titles": title, "redirects": 1, "prop": "pageprops|info", "inprop": "url",
                 "ppprop": "wikibase_item|disambiguation"})
    q = d.get("query") or {}
    pages = q.get("pages") or []
    page = pages[0] if pages else {}
    if page.get("missing") or page.get("invalid") or not page:
        return {"lang": lang, "title": title, "exists": False}
    props = page.get("pageprops") or {}
    return {"lang": lang, "title": page.get("title"), "exists": True, "url": page.get("fullurl"),
            "redirected_from": (q.get("redirects") or [{}])[0].get("from"), "qid": props.get("wikibase_item"),
            "disambiguation": "disambiguation" in props}


# --------------------------------------------------------------------------- facts
def _label(ent: dict, lang: str = "en") -> str | None:
    labels = ent.get("labels") or {}
    return (labels.get(lang) or labels.get("en") or {}).get("value")


def _desc(ent: dict, lang: str = "en") -> str | None:
    d = ent.get("descriptions") or {}
    return (d.get(lang) or d.get("en") or {}).get("value")


def claim_values(ent: dict, prop: str) -> list:
    vals = []
    for c in (ent.get("claims") or {}).get(prop, []) or []:
        if c.get("rank") == "deprecated":
            continue
        dv = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if dv is not None:
            vals.append(dv)
    return vals


def wd_time(v: dict) -> str | None:
    t = (v or {}).get("time", "")
    m = re.match(r"[+-]?(\d{4,})-(\d\d)-(\d\d)", t)
    if not m:
        return None
    prec = v.get("precision", 9)
    y, mo, d = m.groups()
    return y if prec <= 9 or mo == "00" else (f"{y}-{mo}" if prec == 10 or d == "00" else f"{y}-{mo}-{d}")


def norm_host(u: str) -> str:
    u = (u or "").strip()
    h = (urlparse(u if "://" in u else "https://" + u).hostname or "").lower()
    return h.removeprefix("www.")


def domain_matches(website: str, domain: str) -> bool:
    w, d = norm_host(website), norm_host(domain)
    return bool(w and d) and (w == d or w.endswith("." + d) or d.endswith("." + w))


def extract_facts(ent: dict, labels: dict[str, str], lang: str = "en") -> dict:
    socials = {}
    for p, (label, tmpl, _, _) in SOCIAL.items():
        ids = [str(v) for v in claim_values(ent, p) if isinstance(v, str)]
        if ids:
            socials[p] = {"platform": label, "ids": ids, "urls": [tmpl.format(quote(i, safe="@._-")) for i in ids]}
    hq = [labels.get(v.get("id"), v.get("id")) for v in claim_values(ent, "P159") if isinstance(v, dict)]
    inst = [labels.get(v.get("id"), v.get("id")) for v in claim_values(ent, "P31") if isinstance(v, dict)]
    sl = ent.get("sitelinks") or {}
    return {"qid": ent.get("id"), "label": _label(ent, lang), "description": _desc(ent, lang), "instance_of": inst,
            "official_websites": [v for v in claim_values(ent, "P856") if isinstance(v, str)],
            "inception": [x for x in (wd_time(v) for v in claim_values(ent, "P571") if isinstance(v, dict)) if x],
            "headquarters": hq, "socials": socials, "wikidata_url": f"https://www.wikidata.org/wiki/{ent.get('id')}",
            "wikipedia": {k: v.get("title") for k, v in sl.items() if k.endswith("wiki") and k not in ("commonswiki", "specieswiki")
                          and k[:-4] in ("en", lang)}}


def referenced_ids(ents: dict) -> list[str]:
    ids = set()
    for e in ents.values():
        for p in ("P159", "P31"):
            ids.update(v.get("id") for v in claim_values(e, p) if isinstance(v, dict) and v.get("id"))
    return sorted(ids)


# --------------------------------------------------------------------------- site side
def profile_key(url: str) -> tuple[str, str] | None:
    """Normalise a profile URL to (platform property, id) for comparison."""
    p = urlparse(url.strip() if "://" in url else "https://" + url.strip())
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [s for s in p.path.split("/") if s]
    for prop, (_, _, hosts, case) in SOCIAL.items():
        if host not in hosts or not parts:
            continue
        if prop == "P4264":
            if len(parts) >= 2 and parts[0] in ("company", "showcase"):
                return prop, parts[1].lower()
            return None
        if prop == "P2397":
            if parts[0] == "channel" and len(parts) >= 2:
                return prop, parts[1]
            if parts[0] in ("c", "user") and len(parts) >= 2:
                return prop, parts[1].lower()
            return prop, parts[0].lower() if parts[0].startswith("@") else parts[0]  # handle: not comparable with an id
        if prop == "P2013" and parts[0] == "profile.php":
            return None
        return prop, parts[0] if case else parts[0].lower()
    return None


def homepage_jsonld(domain: str) -> dict:
    url = domain if "://" in domain else f"https://{domain}/"
    set_network_mode(urlparse(url).hostname)
    res = fetch(url, UA, timeout=20)
    out = {"url": res.get("final_url") or url, "status": res.get("status"), "error": res.get("error"), "sameAs": [],
           "foundingDate": None, "names": []}
    if res.get("status") != 200:
        return out
    p = PageParser()
    try:
        p.feed(res.get("body") or "")
    except Exception:
        return out
    for raw in p.jsonld_raw:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for n in jsonld_nodes(obj):
            sa = n.get("sameAs")
            if isinstance(sa, str):
                sa = [sa]
            if isinstance(sa, list):
                out["sameAs"] += [s for s in sa if isinstance(s, str)]
            if n.get("foundingDate") and not out["foundingDate"]:
                out["foundingDate"] = str(n["foundingDate"])
            if isinstance(n.get("name"), str) and "Organization" in str(n.get("@type")):
                out["names"].append(n["name"])
    out["sameAs"] = list(dict.fromkeys(out["sameAs"]))
    return out


def compare(facts: dict, domain: str | None, same_as: list[str], founded: str | None, hq: str | None) -> list[dict]:
    f: list[dict] = []
    add = lambda s, c, m: f.append({"severity": s, "code": c, "message": m})  # noqa: E731
    sites = facts["official_websites"]
    if not sites:
        add("medium", "wikidata_no_official_website", f"{facts['qid']} has no official website (P856); add it with a reference.")
    elif domain and not any(domain_matches(s, domain) for s in sites):
        add("high", "wikidata_website_mismatch", f"Wikidata official website {', '.join(sites)} does not match {domain}. "
            "Either this is the wrong entity or Wikidata is outdated.")
    if not facts["inception"]:
        add("low", "wikidata_no_inception", "No inception date (P571) on Wikidata.")
    if not facts["headquarters"]:
        add("low", "wikidata_no_hq", "No headquarters location (P159) on Wikidata.")
    if same_as is None:
        return f
    site_keys = {k for k in (profile_key(u) for u in same_as) if k}
    for prop, s in facts["socials"].items():
        for ident, url in zip(s["ids"], s["urls"]):
            key = (prop, ident if SOCIAL[prop][3] else ident.lower())
            if key in site_keys:
                continue
            if prop == "P2397" and any(k[0] == "P2397" for k in site_keys):
                add("info", "youtube_form_differs", f"sameAs has a YouTube URL in a different form than Wikidata's channel id "
                    f"{ident}; check that both point to the same channel.")
                continue
            other = [k[1] for k in site_keys if k[0] == prop]
            if other:
                add("high", "profile_conflict", f"{s['platform']}: Wikidata lists {url} but sameAs has {', '.join(other)}.")
            else:
                add("medium", "sameas_missing_profile", f"sameAs is missing {s['platform']} {url}, which Wikidata lists.")
    for prop, ident in sorted(site_keys):
        if prop not in facts["socials"]:
            add("low", "wikidata_missing_profile", f"Site sameAs has a {SOCIAL[prop][0]} profile ({ident}) that Wikidata "
                f"does not list; add it to {facts['qid']} with a reference if it is official.")
    if same_as and not any("wikidata.org/wiki/" + facts["qid"] in u for u in same_as):
        add("medium", "sameas_missing_wikidata", f"Add {facts['wikidata_url']} to the Organization sameAs.")
    for wk, title in facts["wikipedia"].items():
        if same_as and not any("wikipedia.org" in u for u in same_as):
            add("low", "sameas_missing_wikipedia", f"Consider adding the {wk} article '{title}' to sameAs.")
            break
    if founded and facts["inception"]:
        if not any(str(x).startswith(str(founded)[:4]) for x in facts["inception"]):
            add("high", "founding_year_conflict", f"Founding year {founded} (site) vs Wikidata inception {', '.join(facts['inception'])}.")
    if hq and facts["headquarters"]:
        if not any(hq.lower() in str(h).lower() or str(h).lower() in hq.lower() for h in facts["headquarters"]):
            add("medium", "hq_conflict", f"Headquarters '{hq}' vs Wikidata {', '.join(map(str, facts['headquarters']))}.")
    return f


# --------------------------------------------------------------------------- command
def run(a) -> dict:
    langs = list(dict.fromkeys(["en"] + ([a.lang] if a.lang else [])))
    candidates: dict[str, dict] = {}
    if not a.qid:
        for lg in langs:
            for c in search_entities(a.name, lg, a.limit):
                candidates.setdefault(c["id"], c)
    wiki = {}
    if a.name:
        for lg in langs:
            wiki[lg] = wikipedia_lookup(a.name, lg)
            if wiki[lg].get("qid") and not a.qid:
                candidates.setdefault(wiki[lg]["qid"], {"id": wiki[lg]["qid"], "label": wiki[lg]["title"], "description": None})
    ids = [a.qid] if a.qid else list(candidates)
    ents = get_entities(ids, a.lang or "en") if ids else {}
    ents = {k: v for k, v in ents.items() if not v.get("missing")}
    ref = get_entities(referenced_ids(ents), a.lang or "en") if ents else {}
    labels = {k: _label(v, a.lang or "en") or k for k, v in ref.items()}
    cand_facts = [extract_facts(e, labels, a.lang or "en") for e in ents.values()]
    for c in cand_facts:
        c["domain_match"] = bool(a.domain) and any(domain_matches(s, a.domain) for s in c["official_websites"])
        c["wikipedia_exact_match"] = any(w.get("qid") == c["qid"] for w in wiki.values())
    findings: list[dict] = []
    selected = None
    if a.qid:
        selected = cand_facts[0] if cand_facts else None
        if not selected:
            findings.append({"severity": "high", "code": "qid_not_found", "message": f"{a.qid} does not exist on Wikidata."})
    else:
        matches = [c for c in cand_facts if c["domain_match"]]
        if len(matches) == 1:
            selected = matches[0]
        elif len(matches) > 1:
            findings.append({"severity": "medium", "code": "multiple_entities_same_domain",
                             "message": f"{len(matches)} Wikidata items list {a.domain} as official website "
                                        f"({', '.join(m['qid'] for m in matches)}): possible duplicates; rerun with --qid."})
        elif cand_facts:
            findings.append({"severity": "info", "code": "entity_unconfirmed",
                             "message": "No candidate lists " + (a.domain or "the domain") + " as its official website. "
                                        "Read the descriptions below; if one is your company, rerun with --qid "
                                        "(and add P856 to it). Generic names are never auto-picked."})
        else:
            findings.append({"severity": "medium", "code": "no_wikidata_item",
                             "message": f"No Wikidata item found for '{a.name}'. {NOTABILITY}"})
    site = None
    same_as = None
    if a.sameas is not None:
        same_as = [s.strip() for s in a.sameas.split(",") if s.strip()]
    elif a.domain and not a.no_fetch:
        site = homepage_jsonld(a.domain)
        same_as = site["sameAs"]
        if site.get("status") != 200:
            findings.append({"severity": "info", "code": "homepage_unreachable",
                             "message": f"Could not read {site['url']} ({site.get('error') or site.get('status')}); sameAs not compared."})
            same_as = None
        elif not same_as:
            findings.append({"severity": "medium", "code": "no_sameas",
                             "message": f"Homepage JSON-LD on {site['url']} has no sameAs; add official profiles"
                                        + (" and the Wikidata URL." if selected else ".")})
    founded = a.founded or (site or {}).get("foundingDate")
    if selected:
        findings += compare(selected, a.domain, same_as, founded, a.hq)
    for lg, w in wiki.items():
        if not w.get("exists"):
            continue
        if w.get("disambiguation"):
            findings.append({"severity": "info", "code": "wikipedia_disambiguation",
                             "message": f"{lg}.wikipedia '{w['title']}' is a disambiguation page; the name is ambiguous."})
        elif selected and w.get("qid") and w["qid"] != selected["qid"]:
            findings.append({"severity": "info", "code": "wikipedia_title_other_entity",
                             "message": f"{lg}.wikipedia '{w['title']}' is about {w['qid']}, not {selected['qid']}."})
    if selected and not selected["wikipedia"]:
        findings.append({"severity": "info", "code": "no_wikipedia_article", "message": "No Wikipedia article is linked. " + COI})
    return {"command": "entity_check", "name": a.name, "domain": a.domain, "qid": selected["qid"] if selected else None,
            "selected": selected, "candidates": cand_facts if not selected else [], "wikipedia": wiki,
            "site": site, "sameAs_compared": same_as, "notes": {"notability": NOTABILITY, "conflict_of_interest": COI},
            "findings": sorted(findings, key=lambda x: ORDER.get(x["severity"], 9))}


def to_md(d: dict) -> str:
    md = [f"# Entity check: {d['name'] or d['qid']}" + (f" ({d['domain']})" if d["domain"] else ""), ""]
    s = d["selected"]
    if s:
        md += [f"- **Wikidata**: [{s['qid']}]({s['wikidata_url']}) {s['label']} - {s['description'] or 'no description'}",
               f"- Instance of: {', '.join(map(str, s['instance_of'])) or 'unknown'}",
               f"- Official website (P856): {', '.join(s['official_websites']) or 'none'}"
               + (f" - matches {d['domain']}: {'yes' if s['domain_match'] else 'NO'}" if d["domain"] else ""),
               f"- Inception (P571): {', '.join(s['inception']) or 'none'}",
               f"- Headquarters (P159): {', '.join(map(str, s['headquarters'])) or 'none'}",
               f"- Wikipedia sitelinks: {', '.join(f'{k}: {v}' for k, v in s['wikipedia'].items()) or 'none'}", "",
               "| Profile | Wikidata value |", "|---|---|"]
        md += [f"| {v['platform']} ({p}) | {', '.join(v['urls'])} |" for p, v in s["socials"].items()] or ["| (none) | |"]
    elif d["candidates"]:
        md += ["Candidates (confirm one, then rerun with `--qid`):", "", "| Q-id | Label | Description | Official website | Wikipedia exact title |",
               "|---|---|---|---|---|"]
        md += [f"| {c['qid']} | {c['label']} | {c['description'] or ''} | {', '.join(c['official_websites'])} | "
               f"{'yes' if c['wikipedia_exact_match'] else ''} |" for c in d["candidates"]]
    for lg, w in d["wikipedia"].items():
        md.append("")
        md.append(f"- {lg}.wikipedia exact title: " + (f"[{w['title']}]({w['url']})" + (f" (redirect from {w['redirected_from']})" if w.get("redirected_from") else "")
                                                       + (f" -> {w['qid']}" if w.get("qid") else "") if w.get("exists") else "no article"))
    if d["sameAs_compared"] is not None:
        md += ["", f"- sameAs compared ({len(d['sameAs_compared'])}): " + (", ".join(d["sameAs_compared"]) or "none")]
    md += ["", "## Findings", ""] + ([f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in d["findings"]] or ["No findings."])
    md += ["", "## Rules", "", f"- {NOTABILITY}", f"- {COI}"]
    return "\n".join(md)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="brand / company name")
    ap.add_argument("--qid", help="Wikidata item you confirmed, e.g. Q123")
    ap.add_argument("--domain", help="production domain, e.g. acme.com")
    ap.add_argument("--sameas", help="comma list of profile URLs (skips the homepage fetch)")
    ap.add_argument("--lang", help="extra language for search/labels (e.g. ms, id)")
    ap.add_argument("--founded", help="founding year/date you publish (compared with P571)")
    ap.add_argument("--hq", help="headquarters city you publish (compared with P159)")
    ap.add_argument("--limit", type=int, default=7)
    ap.add_argument("--no-fetch", action="store_true", help="do not fetch the homepage JSON-LD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.name and not a.qid:
        print("error: give --name or --qid", file=sys.stderr)
        return 2
    if a.qid and not re.fullmatch(r"Q\d+", a.qid):
        print("error: --qid must look like Q123", file=sys.stderr)
        return 2
    try:
        d = run(a)
    except ApiError as e:
        print(f"error: Wikimedia API unavailable ({e}). Nothing was assumed; retry later.", file=sys.stderr)
        return 1
    print(json.dumps(d, indent=2, ensure_ascii=False) if a.json else to_md(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
