"""Offline tests for local_check, domain_history and drift --history."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import domain_history as dh  # noqa: E402
import drift  # noqa: E402
import local_check as lc  # noqa: E402

BIZ = """<html><body><a href="tel:+60312345678">Call</a> Call us +60 3-1234 5678
<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"Acme",
"address":{"@type":"PostalAddress","streetAddress":"1 Jalan Ampang","addressLocality":"Kuala Lumpur","postalCode":"50450"},
"telephone":"+60 3-1234 5678","url":"https://acme.test"}</script></body></html>"""
BIZ2 = BIZ.replace("1 Jalan Ampang", "No. 1, Jln Ampang").replace("1234 5678", "9999 0000").replace("12345678", "39990000")


class LocalCheckTests(unittest.TestCase):
    def test_consistent_site_is_quiet_except_missing_props(self):
        pages = [lc.page_signals("https://acme.test/", BIZ), lc.page_signals("https://acme.test/contact", BIZ)]
        r = lc.analyse(pages, None, "Acme")
        codes = {f["code"] for f in r["findings"]}
        self.assertNotIn("nap_phone_inconsistent", codes)
        self.assertNotIn("nap_address_variants", codes)
        self.assertIn("local_schema_incomplete", codes)          # no openingHoursSpecification/geo/image/sameAs
        self.assertIn("no_map_on_location_pages", codes)

    def test_inconsistent_nap_flagged(self):
        pages = [lc.page_signals("https://acme.test/", BIZ), lc.page_signals("https://acme.test/a", BIZ2),
                 lc.page_signals("https://acme.test/b", BIZ2)]
        codes = {f["code"] for f in lc.analyse(pages, "+60 3-1234 5678", None)["findings"]}
        self.assertIn("nap_phone_inconsistent", codes)
        self.assertIn("nap_address_variants", codes)

    def test_no_schema_is_high(self):
        r = lc.analyse([lc.page_signals("https://x.test/", "<html><body>hi</body></html>")], None, None)
        self.assertEqual(r["findings"][0]["code"], "no_local_business_schema")

    def test_name_mismatch(self):
        codes = {f["code"] for f in lc.analyse([lc.page_signals("https://acme.test/", BIZ)], None, "Other Co")["findings"]}
        self.assertIn("nap_name_mismatch", codes)


class DomainHistoryTests(unittest.TestCase):
    def _cdx(self, years):
        rows = [["timestamp", "original", "statuscode", "mimetype"]]
        for y in years:
            rows.append([f"{y}0101000000", "http://ex.test/", "200", "text/html"])
        return json.dumps(rows)

    def test_gap_and_spam_detected(self):
        titles = iter(["Ex Widgets", "Best Online Casino Bonus", "Ex Widgets 2"])
        with mock.patch.object(dh, "get", return_value=self._cdx([2012, 2013, 2019, 2020])), \
                mock.patch.object(dh, "title_of", side_effect=lambda ts, o: next(titles)), mock.patch("time.sleep"):
            r = dh.analyse("ex.test", 3)
        codes = {f["code"] for f in r["findings"]}
        self.assertIn("activity_gap", codes)
        self.assertIn("spam_history", codes)
        self.assertEqual(r["per_year"]["2019"], 1)

    def test_no_history(self):
        with mock.patch.object(dh, "get", return_value="[]"):
            self.assertEqual(dh.analyse("new.test", 2)["findings"][0]["code"], "no_archive_history")


class DriftHistoryTests(unittest.TestCase):
    def test_history_trend_and_drops(self):
        with tempfile.TemporaryDirectory() as d:
            for i, (score, idx) in enumerate([(70, 90), (85, 70)]):
                Path(d, f"audit-2026110{i + 1}-120000.json").write_text(json.dumps({
                    "when": f"2026-11-0{i + 1}T12:00:00", "target": "site", "readiness": score,
                    "categories": {"indexability": idx, "rendering": 100}, "counts": {"critical": 1 - i, "high": 2}}), encoding="utf-8")
            rows = drift.history(Path(d))
        self.assertEqual([r["readiness"] for r in rows], [70, 85])
        self.assertEqual(rows[1]["delta"], 15)
        self.assertEqual(rows[1]["category_drops"], {"indexability": (90, 70)})
        self.assertIn("70 → 85", drift.render_history(rows))


if __name__ == "__main__":
    unittest.main()
