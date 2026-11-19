"""Offline tests for scripts/gsc_report.py on fixture Search Console exports."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gsc_report as g  # noqa: E402

GSC = Path(__file__).resolve().parent / "fixtures" / "core" / "gsc"


class HelperTests(unittest.TestCase):
    def test_num(self):
        self.assertEqual(g.num("1,234"), 1234.0)
        self.assertAlmostEqual(g.num("12.5%"), 0.125)
        self.assertEqual(g.num(""), 0.0)
        self.assertEqual(g.num("n/a"), 0.0)
        self.assertEqual(g.num(None), 0.0)

    def test_bucket(self):
        self.assertEqual(g.bucket(1.0), 0)
        self.assertEqual(g.bucket(0.5), 0)
        self.assertEqual(g.bucket(2.0), 1)
        self.assertEqual(g.bucket(9.9), 6)
        self.assertEqual(g.bucket(20.4), 8)
        self.assertIsNone(g.bucket(25))
        self.assertIsNone(g.bucket(0))


class AnalyseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = g.load(GSC)
        cls.res = g.analyse(cls.data, "invoicely")

    def test_load_routes_files(self):
        self.assertEqual(len(self.data["queries"]), 9)
        self.assertEqual(len(self.data["pages"]), 4)
        self.assertEqual(len(self.data["page_query"]), 6)
        tmpl = [r for r in self.data["queries"] if r["query"] == "invoice template"][0]
        self.assertEqual((tmpl["clicks"], tmpl["impressions"]), (1234.0, 10000.0))

    def test_totals_and_brand_share(self):
        self.assertEqual(self.res["totals"], {"queries": 9, "clicks": 2670, "impressions": 16140})
        self.assertEqual(self.res["brand_share"], {"clicks_pct": 18.7, "impressions_pct": 6.2})

    def test_striking_distance(self):
        self.assertEqual([r["query"] for r in self.res["striking_distance"]],
                         ["invoice template", "best invoicing software", "freshbooks alternative"])

    def test_low_ctr_uses_own_curve(self):
        q = self.res["low_ctr_queries"]
        self.assertEqual([r["query"] for r in q], ["invoice generator"])
        self.assertAlmostEqual(q[0]["expected_ctr"], 0.29)
        p = self.res["low_ctr_pages"]
        self.assertEqual([r["page"] for r in p], ["https://acme.test/blog/e-invoice"])

    def test_cannibalisation_excludes_brand_and_small_shares(self):
        c = self.res["cannibalisation"]
        self.assertEqual([x["query"] for x in c], ["invoice app malaysia"])
        self.assertEqual([p["page"] for p in c[0]["pages"]], ["https://acme.test/pricing", "https://acme.test/features"])
        self.assertEqual([p["share_pct"] for p in c[0]["pages"]], [58, 38])
        no_brand = g.analyse(self.data, None)
        self.assertIn("invoicely login", [x["query"] for x in no_brand["cannibalisation"]])
        self.assertNotIn("brand_share", no_brand)

    def test_question_queries(self):
        self.assertEqual(self.res["question_queries"], ["how to send e-invoice", "best invoicing software", "freshbooks alternative"])

    def test_derives_queries_from_page_query_only(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copy(GSC / "page_query.csv", tmp / "page_query.csv")
            data = g.load(tmp)
            agg = {r["query"]: r for r in data["queries"]}
            self.assertEqual(agg["invoice app malaysia"]["impressions"], 520)
            self.assertEqual(agg["invoice app malaysia"]["clicks"], 16)
            self.assertAlmostEqual(agg["invoice app malaysia"]["position"], (4 * 300 + 6 * 200 + 9 * 20) / 520)
        finally:
            shutil.rmtree(tmp)

    def test_main_json_and_render(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            g.main([str(GSC), "--brand", "invoicely", "--json"])
        self.assertEqual(json.loads(buf.getvalue())["totals"]["queries"], 9)
        md = g.render(self.res)
        self.assertIn("Striking distance", md)
        self.assertIn("invoice app malaysia", md)

    def test_extra_question_words(self):
        rx = g.question_rx(["bagaimana"])
        self.assertTrue(rx.search("bagaimana hantar e-invois"))
        self.assertFalse(g.question_rx([]).search("bagaimana hantar e-invois"))


if __name__ == "__main__":
    unittest.main()
