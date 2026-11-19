"""Offline tests for scripts/drift.py (crawl-to-crawl regression rules)."""
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import drift  # noqa: E402


def pg(url, **kw):
    base = {"url": url, "final_url": url, "status": 200, "noindex": False, "canonical": url, "title": "Title",
            "description": "Desc", "h1": ["Heading"], "schema_types": ["Organization"], "og": {"title": "T"},
            "word_count": 500, "in_sitemap": True}
    base.update(kw)
    return base


OLD = {"summary": {"technical_score": 90}, "crawl_complete": True, "pages": [
    pg("https://prod.test/", title="Acme", h1=["Invoicing made simple"]),
    pg("https://prod.test/pricing", title="Pricing | Acme", h1=["Pricing"], schema_types=["Product"], word_count=400),
    pg("https://prod.test/gone"),
    pg("https://prod.test/blog/", word_count=200, in_sitemap=False),
    pg("https://prod.test/docs", canonical="https://prod.test/docs", h1=["Developer documentation"]),
    pg("https://prod.test/p?utm_source=x&b=2&a=1"),
]}
NEW = {"summary": {"technical_score": 75}, "crawl_complete": True, "pages": [
    pg("https://preview.test/", title="Acme | Home", h1=["Invoicing made simple"], schema_types=["Organization", "WebSite"],
       og={"title": None}, word_count=100),
    pg("https://preview.test/pricing", status=404, noindex=True, canonical=None, title="", h1=[], schema_types=[],
       word_count=0),
    pg("https://preview.test/blog", word_count=120, in_sitemap=False),
    pg("https://preview.test/docs", canonical="https://preview.test/developers", h1=["Pricing plans for teams"]),
    pg("https://preview.test/p?a=1&b=2&gclid=zz"),
    pg("https://preview.test/new"),
]}


def as_loaded(d):
    d = copy.deepcopy(d)
    return {drift.key(p["url"]): p for p in d["pages"]} | {"__meta__": d}


def rules(changes):
    return {(c["url"], c["rule"]): c["severity"] for c in changes}


class KeyTests(unittest.TestCase):
    def test_key_normalisation(self):
        self.assertEqual(drift.key("https://a.test/blog/"), "/blog")
        self.assertEqual(drift.key("https://a.test"), "/")
        self.assertEqual(drift.key("https://a.test/p?utm_source=x&b=2&a=1&fbclid=1"), "/p?a=1&b=2")
        self.assertIsNone(drift.path_of(None))


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = rules(drift.compare(as_loaded(OLD), as_loaded(NEW)))

    def test_site_score_drop(self):
        self.assertEqual(self.r[("(site)", "technical_score_drop")], "warning")

    def test_broken_page_rules(self):
        for rule in ("status_broke", "noindex_added", "canonical_removed", "title_removed", "h1_removed",
                     "structured_data_removed"):
            with self.subTest(rule=rule):
                self.assertEqual(self.r[("/pricing", rule)], "critical")

    def test_home_changes(self):
        self.assertEqual(self.r[("/", "title_changed")], "warning")
        self.assertEqual(self.r[("/", "schema_types_added")], "info")
        self.assertEqual(self.r[("/", "open_graph_removed")], "warning")
        self.assertEqual(self.r[("/", "content_shrunk")], "warning")
        self.assertNotIn(("/", "h1_rewritten"), self.r)
        self.assertNotIn(("/", "canonical_changed"), self.r)  # matched by path across hosts

    def test_canonical_and_h1_rewrites(self):
        self.assertEqual(self.r[("/docs", "canonical_changed")], "critical")
        self.assertEqual(self.r[("/docs", "h1_rewritten")], "critical")

    def test_missing_new_and_content_change(self):
        self.assertEqual(self.r[("/gone", "page_missing_from_new_crawl")], "critical")
        self.assertEqual(self.r[("/new", "new_page")], "info")
        self.assertEqual(self.r[("/blog", "content_changed")], "info")
        self.assertFalse(any(u == "/p?a=1&b=2" for u, _ in self.r))  # tracking params ignored, identical page

    def test_partial_crawl_does_not_flag_missing(self):
        new = copy.deepcopy(NEW)
        new["crawl_complete"] = False
        r = rules(drift.compare(as_loaded(OLD), as_loaded(new)))
        self.assertNotIn(("/gone", "page_missing_from_new_crawl"), r)

    def test_missing_page_not_in_sitemap_is_warning(self):
        old = copy.deepcopy(OLD)
        old["pages"][2]["in_sitemap"] = False
        self.assertEqual(rules(drift.compare(as_loaded(old), as_loaded(NEW)))[("/gone", "page_missing_from_new_crawl")], "warning")

    def test_sorted_by_severity(self):
        sev = [c["severity"] for c in drift.compare(as_loaded(OLD), as_loaded(NEW))]
        self.assertEqual(sev, sorted(sev, key=lambda s: drift.ORDER[s]))


class MainTests(unittest.TestCase):
    def test_main_json_and_fail_on(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            a, b = tmp / "crawl-a.json", tmp / "crawl-b.json"
            a.write_text(json.dumps(OLD), encoding="utf-8")
            b.write_text(json.dumps(NEW), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                drift.main([str(a), str(b), "--json"])
            self.assertTrue(any(c["rule"] == "status_broke" for c in json.loads(buf.getvalue())))
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                drift.main([str(a), str(b), "--fail-on", "critical"])
            self.assertEqual(cm.exception.code, 1)
            buf = io.StringIO()
            with redirect_stdout(buf):
                drift.main([str(a), str(a), "--fail-on", "critical"])  # identical: no exit
            self.assertIn("No changes detected", buf.getvalue())
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
