"""Offline tests for scripts/entity_check.py (Wikimedia API and homepage fetch mocked)."""
from __future__ import annotations

import contextlib
import email.message
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("entity_check", ROOT / "scripts" / "entity_check.py")
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)


def snak(prop, value):
    return {"mainsnak": {"snaktype": "value", "property": prop, "datavalue": {"value": value}}, "rank": "normal"}


ACME = {"id": "Q900", "labels": {"en": {"language": "en", "value": "Acme Cloud"}},
        "descriptions": {"en": {"language": "en", "value": "Malaysian invoicing software company"}},
        "claims": {"P31": [snak("P31", {"id": "Q4830453"})],
                   "P856": [snak("P856", "https://www.acme.com/")],
                   "P571": [snak("P571", {"time": "+2019-03-01T00:00:00Z", "precision": 11})],
                   "P159": [snak("P159", {"id": "Q1865"})],
                   "P2002": [snak("P2002", "AcmeHQ")],
                   "P2037": [snak("P2037", "acme")],
                   "P2397": [snak("P2397", "UCabc123")],
                   "P2013": [dict(snak("P2013", "oldacme"), rank="deprecated")]},
        "sitelinks": {"enwiki": {"site": "enwiki", "title": "Acme Cloud"}}}
ACME_ROAD = {"id": "Q901", "labels": {"en": {"value": "Acme"}}, "descriptions": {"en": {"value": "fictional company in cartoons"}},
             "claims": {}, "sitelinks": {}}
REF = {"Q4830453": {"id": "Q4830453", "labels": {"en": {"value": "business"}}},
       "Q1865": {"id": "Q1865", "labels": {"en": {"value": "Kuala Lumpur"}}}}

HOMEPAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Acme Cloud","foundingDate":"2019",
"sameAs":["https://twitter.com/acmehq","https://www.linkedin.com/company/acme-cloud/","https://www.youtube.com/@acme"]},
{"@type":"WebSite","name":"Acme"}]}</script></head><body>hi</body></html>"""


class FakeWiki:
    def __init__(self, wd_entities=None, search=None, wiki=None):
        self.wd = {**(wd_entities or {}), **REF}
        self.search = search if search is not None else [{"id": "Q901", "label": "Acme", "description": "fictional company in cartoons"},
                                                          {"id": "Q900", "label": "Acme Cloud", "description": "Malaysian invoicing software company"}]
        self.wiki = wiki
        self.calls = []

    def __call__(self, url, params, **k):
        self.calls.append((url, params))
        if params["action"] == "wbsearchentities":
            return {"search": self.search}
        if params["action"] == "wbgetentities":
            ids = params["ids"].split("|")
            return {"entities": {i: self.wd.get(i, {"id": i, "missing": ""}) for i in ids}}
        if params["action"] == "query":
            return self.wiki or {"query": {"pages": [{"title": params["titles"], "missing": True}]}}
        raise AssertionError(params)


def run(argv, fake, homepage=None):
    out, err = io.StringIO(), io.StringIO()
    fetch = mock.Mock(return_value={"status": 200, "final_url": "https://acme.com/", "body": homepage or "", "error": None})
    with mock.patch.object(ec, "api_get", fake), mock.patch.object(ec, "fetch", fetch), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ec.main(argv)
    return rc, out.getvalue(), err.getvalue(), fetch


def codes(d):
    return [f["code"] for f in d["findings"]]


class TestHelpers(unittest.TestCase):
    def test_profile_key(self):
        pk = ec.profile_key
        self.assertEqual(pk("https://twitter.com/AcmeHQ"), ("P2002", "acmehq"))
        self.assertEqual(pk("https://x.com/acmehq/"), ("P2002", "acmehq"))
        self.assertEqual(pk("https://www.linkedin.com/company/acme-cloud/about"), ("P4264", "acme-cloud"))
        self.assertIsNone(pk("https://www.linkedin.com/in/someone"))
        self.assertEqual(pk("https://www.youtube.com/channel/UCabc123"), ("P2397", "UCabc123"))
        self.assertEqual(pk("https://m.facebook.com/AcmeHQ"), ("P2013", "acmehq"))
        self.assertEqual(pk("https://github.com/Acme/repo"), ("P2037", "acme"))
        self.assertIsNone(pk("https://example.org/acme"))

    def test_domain_match_and_time(self):
        self.assertTrue(ec.domain_matches("https://www.acme.com/", "acme.com"))
        self.assertTrue(ec.domain_matches("https://acme.com", "https://www.acme.com/pricing"))
        self.assertFalse(ec.domain_matches("https://acme.io", "acme.com"))
        self.assertEqual(ec.wd_time({"time": "+2019-03-01T00:00:00Z", "precision": 11}), "2019-03-01")
        self.assertEqual(ec.wd_time({"time": "+2015-00-00T00:00:00Z", "precision": 9}), "2015")

    def test_extract_skips_deprecated(self):
        f = ec.extract_facts(ACME, {"Q1865": "Kuala Lumpur", "Q4830453": "business"})
        self.assertNotIn("P2013", f["socials"])
        self.assertEqual(f["socials"]["P2002"]["urls"], ["https://x.com/AcmeHQ"])
        self.assertEqual(f["headquarters"], ["Kuala Lumpur"])
        self.assertEqual(f["wikipedia"], {"enwiki": "Acme Cloud"})


class TestRun(unittest.TestCase):
    def test_selects_only_by_domain_and_compares_sameas(self):
        rc, out, _, fetch = run(["--name", "Acme", "--domain", "acme.com", "--json"], FakeWiki({"Q900": ACME, "Q901": ACME_ROAD}), HOMEPAGE)
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["qid"], "Q900")
        fetch.assert_called_once()
        self.assertIn("super-seo/1.0", fetch.call_args[0][1])
        c = codes(d)
        self.assertIn("sameas_missing_profile", c)        # GitHub listed on Wikidata, not in sameAs
        self.assertIn("youtube_form_differs", c)           # @handle vs channel id
        self.assertIn("wikidata_missing_profile", c)       # LinkedIn in sameAs, not on Wikidata
        self.assertIn("sameas_missing_wikidata", c)
        self.assertNotIn("profile_conflict", c)            # twitter.com/acmehq == x.com/AcmeHQ
        self.assertNotIn("founding_year_conflict", c)      # 2019 vs 2019-03-01

    def test_generic_name_never_auto_picked(self):
        road = dict(ACME_ROAD)
        rc, out, _, _ = run(["--name", "Acme", "--domain", "acme.com", "--no-fetch", "--json"], FakeWiki({"Q901": road},
                            search=[{"id": "Q901", "label": "Acme", "description": "fictional company in cartoons"}]))
        d = json.loads(out)
        self.assertIsNone(d["qid"])
        self.assertIn("entity_unconfirmed", codes(d))
        self.assertEqual(d["candidates"][0]["description"], "fictional company in cartoons")
        rc, md, _, _ = run(["--name", "Acme", "--domain", "acme.com", "--no-fetch"], FakeWiki({"Q901": road},
                           search=[{"id": "Q901", "label": "Acme", "description": "fictional company in cartoons"}]))
        self.assertIn("rerun with `--qid`", md)

    def test_no_item_gives_notability_rule(self):
        rc, out, _, _ = run(["--name", "Tinyco", "--no-fetch", "--json"], FakeWiki({}, search=[]))
        d = json.loads(out)
        f = [x for x in d["findings"] if x["code"] == "no_wikidata_item"][0]
        self.assertIn("notability is plausible", f["message"])
        self.assertIn("conflict of interest", d["notes"]["conflict_of_interest"])

    def test_qid_website_mismatch_and_conflicts(self):
        rc, out, _, _ = run(["--qid", "Q900", "--domain", "acme.io", "--sameas", "https://x.com/other,https://github.com/acme",
                             "--founded", "2021", "--hq", "Penang", "--json"], FakeWiki({"Q900": ACME}))
        d = json.loads(out)
        c = codes(d)
        self.assertEqual(d["qid"], "Q900")
        self.assertIn("wikidata_website_mismatch", c)
        self.assertIn("profile_conflict", c)
        self.assertIn("founding_year_conflict", c)
        self.assertIn("hq_conflict", c)
        self.assertEqual(d["findings"][0]["severity"], "high")

    def test_wikipedia_redirect_lookup(self):
        wiki = {"query": {"redirects": [{"from": "Acme", "to": "Acme Cloud"}],
                          "pages": [{"title": "Acme Cloud", "fullurl": "https://en.wikipedia.org/wiki/Acme_Cloud",
                                     "pageprops": {"wikibase_item": "Q900"}}]}}
        fake = FakeWiki({"Q900": ACME}, search=[], wiki=wiki)
        rc, out, _, _ = run(["--name", "Acme", "--domain", "acme.com", "--sameas", "", "--json"], fake)
        d = json.loads(out)
        self.assertEqual(d["qid"], "Q900")  # found via the Wikipedia page's Wikidata link, confirmed by domain
        self.assertEqual(d["wikipedia"]["en"]["redirected_from"], "Acme")
        q = [p for u, p in fake.calls if p["action"] == "query"][0]
        self.assertEqual(q["redirects"], 1)

    def test_lang_searches_twice(self):
        fake = FakeWiki({"Q900": ACME, "Q901": ACME_ROAD})
        run(["--name", "Acme", "--lang", "ms", "--no-fetch", "--json"], fake)
        langs = [p["language"] for u, p in fake.calls if p["action"] == "wbsearchentities"]
        self.assertEqual(langs, ["en", "ms"])

    def test_bad_args(self):
        self.assertEqual(run([], FakeWiki())[0], 2)
        self.assertEqual(run(["--qid", "123"], FakeWiki())[0], 2)


class TestHttp(unittest.TestCase):
    def test_backoff_then_error_and_ua(self):
        seen = []

        def urlopen(req, timeout=None):
            seen.append(req.get_header("User-agent"))
            raise error.HTTPError(req.full_url, 429, "slow down", email.message.Message(), io.BytesIO(b""))
        with mock.patch.object(ec.request, "urlopen", urlopen), mock.patch.object(ec, "_sleep", lambda s: None):
            with self.assertRaises(ec.ApiError):
                ec.api_get(ec.WD_API, {"action": "wbsearchentities"}, retries=2)
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(u.startswith("super-seo/1.0 (+https://github.com/") for u in seen))

    def test_api_unreachable_exit_1(self):
        def boom(*a, **k):
            raise ec.ApiError("www.wikidata.org -> network error")
        rc, out, err, _ = run(["--name", "Acme", "--no-fetch"], boom)
        self.assertEqual(rc, 1)
        self.assertIn("Nothing was assumed", err)


if __name__ == "__main__":
    unittest.main()
