"""Offline tests for extensions/matomo/matomo.py (HTTP mocked)."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions" / "matomo"))
import matomo as mt  # noqa: E402

ENV = {"MATOMO_URL": "https://stats.example.com/", "MATOMO_TOKEN": "tok_secret_123", "MATOMO_SITE_ID": "3"}


def respond(form: dict):
    m = form["method"]
    if m == "Referrers.getSearchEngines":
        return [{"label": "Google", "nb_visits": 400, "nb_actions": 900, "bounce_count": 200},
                {"label": "Bing", "nb_visits": 50, "nb_actions": 70, "bounce_count": 40}]
    if m == "Referrers.getAIAssistants":
        return {"result": "error", "message": "The method 'getAIAssistants' does not exist"}
    if m == "Referrers.getWebsites" and form["period"] == "week":
        return {"2026-11-02,2026-11-08": [{"label": "chatgpt.com", "nb_visits": 6}, {"label": "github.com", "nb_visits": 30}],
                "2026-11-09,2026-11-15": [{"label": "perplexity.ai", "nb_visits": 2}, {"label": "chatgpt.com", "nb_visits": 9}]}
    if m == "Referrers.getWebsites":
        return [{"label": "chatgpt.com", "nb_visits": 15, "nb_actions": 40}, {"label": "claude.ai", "nb_visits": 3, "nb_actions": 5},
                {"label": "github.com", "nb_visits": 30, "nb_actions": 60}]
    if m == "Actions.getEntryPageUrls":
        return [{"label": "pricing", "entry_nb_visits": 100, "entry_bounce_count": 85, "url": "https://example.com/pricing"},
                {"label": "/docs/start", "entry_nb_visits": 20, "entry_bounce_count": 5}]
    raise AssertionError(m)


class MatomoTests(unittest.TestCase):
    def setUp(self):
        self.forms = []

        def fake(method, url, headers, body=None, form=None, **kw):
            self.assertEqual(method, "POST")
            self.assertEqual(url, "https://stats.example.com/index.php")
            self.assertNotIn("token_auth", url)
            self.forms.append(form)
            return respond(form)
        p = mock.patch.object(mt, "http_json", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, ENV)
        e.start()
        self.addCleanup(e.stop)

    def test_organic(self):
        res = mt.cmd_organic(mt.config(), 28, 10)
        f = self.forms[0]
        self.assertEqual((f["module"], f["format"], f["idSite"], f["token_auth"]), ("API", "JSON", "3", "tok_secret_123"))
        self.assertEqual(res["engines"][0], {"source": "Google", "sessions": 400, "actions": 900, "bounce_pct": 50.0})
        self.assertEqual(self.forms[1]["segment"], "referrerType==search")
        self.assertEqual(res["landing"][0]["landing"], "/pricing")
        self.assertEqual(res["landing"][1]["landing"], "/docs/start")
        self.assertIn("high_single_page_share", {x["code"] for x in res["findings"]})

    def test_ai_referrals(self):
        res = mt.cmd_ai(mt.config(), 90, 10)
        self.assertEqual({s["source"]: s["sessions"] for s in res["sources"]}, {"chatgpt.com": 15, "claude.ai": 3})
        self.assertEqual(res["trend"], [{"week": "2026-11-02", "sessions": 6, "chatgpt.com": 6},
                                        {"week": "2026-11-09", "sessions": 11, "perplexity.ai": 2, "chatgpt.com": 9}])
        seg = [f for f in self.forms if f["method"] == "Actions.getEntryPageUrls"][0]["segment"]
        self.assertIn("referrerName=@chatgpt.com", seg)
        self.assertIn(",referrerName=@perplexity.ai", seg)
        self.assertIn("floor", res["notes"][0])

    def test_cli_no_secret_in_output(self):
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(mt.main(["landing", "--seo-dir", td]), 0)
            written = "".join(p.read_text(encoding="utf-8") for p in (Path(td) / "audits").glob("matomo-landing-site3-*"))
        self.assertIn("/pricing", out.getvalue())
        self.assertNotIn("tok_secret_123", out.getvalue() + written)

    def test_error_result(self):
        with self.assertRaises(mt.ApiError):
            mt.api(mt.config(), "Referrers.getAIAssistants", period="range", date="x")

    def test_date_range(self):
        self.assertEqual(mt.date_range(7, date(2026, 11, 19)), "2026-11-13,2026-11-19")


if __name__ == "__main__":
    unittest.main()
