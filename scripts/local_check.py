#!/usr/bin/env python3
"""local_check.py - local business signals across a site: NAP consistency, LocalBusiness schema, location pages.

  python local_check.py https://example.com --max-pages 40
  python local_check.py https://example.com --urls location-pages.txt --name "Acme Sdn Bhd" --phone "+60 3-1234 5678"

Checks (heuristics; see references/local-seo.md for the playbook):
  - phone numbers found in tel: links, visible text and schema → normalised and compared (more than one main
    number = inconsistent NAP); --phone sets the canonical number
  - postal addresses from JSON-LD PostalAddress → compared across pages (street/locality/postcode variants)
  - LocalBusiness (or subtype) / Organization JSON-LD: name, address, telephone, url, openingHoursSpecification,
    geo, image, priceRange, sameAs (profile links) — missing properties listed
  - tel: links present on pages that show a phone, map embed or directions link on contact/location pages
  - location pages: count, and a pointer to programmatic_check.py for uniqueness when there are many
Python 3.10+, standard library only. Reuses crawl_audit's guarded fetcher and crawler.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_audit import PageParser, crawl, fetch, jsonld_nodes, set_network_mode, UA_DEFAULT  # noqa: E402

PHONE_TEXT = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{1,4}\)?[\s.-]?){2,4}\d{3,4}")
LOCAL_TYPES = {"LocalBusiness", "Store", "ProfessionalService", "Restaurant", "MedicalBusiness", "HomeAndConstructionBusiness",
               "AutomotiveBusiness", "LegalService", "FinancialService", "FoodEstablishment", "HealthAndBeautyBusiness",
               "LodgingBusiness", "SportsActivityLocation", "EntertainmentBusiness", "Dentist", "Physician", "RealEstateAgent",
               "TravelAgency", "EducationalOrganization", "Organization", "Corporation", "OnlineBusiness"}
WANT = ["name", "address", "telephone", "url", "openingHoursSpecification", "geo", "image", "sameAs"]
LOCATION_PATH = re.compile(r"/(locations?|branches|stores?|offices?|cawangan|kedai|lokasi)(/|$)", re.I)


def norm_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    return digits[-9:] if len(digits) >= 9 else digits   # compare on the national significant number


def addr_key(a) -> str:
    if isinstance(a, str):
        return " ".join(a.lower().split())
    if isinstance(a, dict):
        parts = [a.get(k, "") for k in ("streetAddress", "addressLocality", "postalCode", "addressRegion", "addressCountry")]
        return " | ".join(" ".join(str(p).lower().split()) for p in parts)
    return ""


def page_signals(url: str, body: str) -> dict:
    p = PageParser()
    try:
        p.feed(body)
    except Exception:  # noqa: BLE001 - malformed HTML must not stop the scan
        pass
    text = " ".join(" ".join(p.text_parts).split())
    tel_links = [a["href"][4:] for a in p.anchors if a["href"].lower().startswith("tel:")]
    nodes = []
    for raw in p.jsonld_raw:
        try:
            nodes += jsonld_nodes(json.loads(raw))
        except json.JSONDecodeError:
            pass
    biz = [n for n in nodes if any(t in LOCAL_TYPES for t in (n.get("@type") if isinstance(n.get("@type"), list) else [n.get("@type")]))]
    schema_phones = [n.get("telephone") for n in biz if n.get("telephone")]
    addresses = [n.get("address") for n in biz if n.get("address")]
    text_phones = [m.group(0) for m in PHONE_TEXT.finditer(text) if len(re.sub(r"\D", "", m.group(0))) >= 9][:10]
    low = body.lower()
    return {
        "url": url, "tel_links": tel_links, "schema_phones": schema_phones, "text_phones": text_phones,
        "addresses": addresses, "business_nodes": biz,
        "map_embed": "google.com/maps" in low or "maps.google" in low or "openstreetmap.org" in low or "maps.apple.com" in low,
        "is_location_page": bool(LOCATION_PATH.search(urlparse(url).path)) or "/contact" in urlparse(url).path.lower(),
    }


def analyse(pages: list[dict], canonical_phone: str | None, name: str | None) -> dict:
    findings = []

    def add(sev, code, msg, **kw):
        findings.append({"severity": sev, "code": code, "message": msg, **kw})

    phones = Counter()
    where = defaultdict(set)
    for pg in pages:
        for ph in pg["tel_links"] + pg["schema_phones"] + pg["text_phones"]:
            k = norm_phone(ph)
            if len(k) >= 8:
                phones[k] += 1
                where[k].add(pg["url"])
    main = norm_phone(canonical_phone) if canonical_phone else (phones.most_common(1)[0][0] if phones else None)
    others = [(k, n) for k, n in phones.items() if k != main and n >= 2]
    if main and others:
        add("medium", "nap_phone_inconsistent", f"{len(others) + 1} different phone numbers appear across pages "
            f"(main ...{main[-4:]}); make sure each is intentional (branches) and consistent everywhere",
            numbers={k[-6:]: sorted(where[k])[:3] for k, _ in others[:5]})
    if canonical_phone and main not in phones:
        add("high", "nap_phone_missing", "The canonical phone number does not appear on any crawled page")
    addr = Counter()
    for pg in pages:
        for a in pg["addresses"]:
            addr[addr_key(a)] += 1
    if len(addr) > 1:
        add("medium", "nap_address_variants", f"{len(addr)} different address formats in schema; use one exact format per location",
            variants=list(addr)[:5])
    biz_nodes = [n for pg in pages for n in pg["business_nodes"]]
    if not biz_nodes:
        add("high", "no_local_business_schema", "No LocalBusiness/Organization JSON-LD found on crawled pages")
    else:
        best = max(biz_nodes, key=lambda n: sum(1 for k in WANT if n.get(k)))
        missing = [k for k in WANT if not best.get(k)]
        if missing:
            add("medium" if {"address", "telephone"} & set(missing) else "low", "local_schema_incomplete",
                f"Most complete business JSON-LD is missing: {', '.join(missing)}")
        if name:
            names = {str(n.get("name", "")).strip().lower() for n in biz_nodes if n.get("name")}
            if names and name.strip().lower() not in names:
                add("medium", "nap_name_mismatch", f"Business name in schema ({', '.join(sorted(names)[:3])}) differs from '{name}'")
    shows_phone_no_link = [pg["url"] for pg in pages if pg["text_phones"] and not pg["tel_links"]]
    if shows_phone_no_link:
        add("low", "phone_without_tel_link", f"{len(shows_phone_no_link)} pages show a phone number without a tel: link",
            urls=shows_phone_no_link[:5])
    loc_pages = [pg for pg in pages if pg["is_location_page"]]
    if loc_pages and not any(pg["map_embed"] for pg in loc_pages):
        add("low", "no_map_on_location_pages", "Contact/location pages have no map embed or directions link")
    real_locs = [pg for pg in loc_pages if "/contact" not in urlparse(pg["url"]).path.lower()]
    if len(real_locs) >= 30:
        add("medium" if len(real_locs) < 50 else "high", "many_location_pages",
            f"{len(real_locs)} location pages: run programmatic_check.py on them (≥ 60% unique content each)")
    return {"pages": len(pages), "main_phone_suffix": main[-4:] if main else None, "phones_found": len(phones),
            "address_variants": len(addr), "business_schema_nodes": len(biz_nodes), "location_pages": len(loc_pages),
            "findings": findings}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--urls", help="file with one URL per line (audit only these)")
    ap.add_argument("--phone", help="canonical phone number")
    ap.add_argument("--name", help="canonical business name")
    ap.add_argument("--allow-private", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    set_network_mode(urlparse(a.url).hostname, a.allow_private)
    if a.urls:
        urls = [l.strip() for l in Path(a.urls).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    else:
        c = crawl(a.url, a.max_pages, UA_DEFAULT, 0.2, None, True, a.allow_private)
        urls = [pg["final_url"] for pg in c["pages"] if pg.get("status") == 200 and not pg.get("non_html")]
    pages = []
    for u in urls:
        r = fetch(u, UA_DEFAULT)
        if r["status"] == 200 and r["body"]:
            pages.append(page_signals(r["final_url"], r["body"]))
    res = analyse(pages, a.phone, a.name)
    res["site"] = a.url
    lines = [f"# Local signals: {a.url}", "",
             f"- {res['pages']} pages · phones found {res['phones_found']} · address variants {res['address_variants']} · "
             f"business schema nodes {res['business_schema_nodes']} · contact/location pages {res['location_pages']}", ""]
    lines += [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in res["findings"]] or ["- No local-signal issues found."]
    text = "\n".join(lines) + "\n"
    if not a.no_write:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"local-{urlparse(a.url).netloc.replace(':', '_')}-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res, indent=2, default=str) if a.json else text)


if __name__ == "__main__":
    main()
