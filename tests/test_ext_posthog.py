"""Offline tests for extensions/posthog/posthog.py (HTTP mocked)."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions" / "posthog"))
import posthog as ph  # noqa: E402

ENV = {"POSTHOG_PERSONAL_API_KEY": "phx_secret_value", "POSTHOG_PROJECT_ID": "1234", "POSTHOG_HOST": "https://eu.posthog.com"}


def hogql_response(query: str) -> dict:
    if "toStartOfWeek" in query:
        return {"columns": ["week", "domain", "pageviews", "sessions"],
                "results": [["2026-11-02", "chatgpt.com", 10, 7], ["2026-11-02", "perplexity.ai", 3, 2],
                            ["2026-11-09", "chatgpt.com", 15, 11]]}
    if "argMin" in query:
        return {"columns": ["landing", "sessions", "single_page", "bounce_pct"],
                "results": [["/pricing", 120, 100, 83.3], ["/", 50, 10, 20.0]]}
    return {"columns": ["domain", "pageviews", "sessions"],
            "results": [["www.google.com", 300, 200], ["google.co.uk", 20, 15], ["bing.com", 40, 30],
                        ["chatgpt.com", 25, 18], ["gemini.google.com", 5, 4], ["news.ycombinator.com", 9, 9]]}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class PostHogTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake(method, url, headers, body=None, **kw):
            self.calls.append((method, url, headers, body))
            return hogql_response(body["query"]["query"])
        p = mock.patch.object(ph, "http_json", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, ENV, clear=False)
        e.start()
        self.addCleanup(e.stop)

    def test_classify(self):
        self.assertEqual(ph.classify_referrer("gemini.google.com"), ("ai", "gemini.google.com"))
        self.assertEqual(ph.classify_referrer("https://www.google.de/"), ("search", "google"))
        self.assertEqual(ph.classify_referrer("search.brave.com"), ("search", "brave"))
        self.assertEqual(ph.classify_referrer("chat.openai.com"), ("ai", "chat.openai.com"))
        self.assertIsNone(ph.classify_referrer("example.com"))
        self.assertIsNone(ph.classify_referrer(None))

    def test_request_shape(self):
        res = ph.cmd_organic(ph.config(), 28, 10)
        method, url, headers, body = self.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://eu.posthog.com/api/projects/1234/query/")
        self.assertEqual(headers["Authorization"], "Bearer phx_secret_value")
        self.assertEqual(body["query"]["kind"], "HogQLQuery")
        self.assertIn("INTERVAL 28 DAY", body["query"]["query"])
        eng = {e["source"]: e for e in res["engines"]}
        self.assertEqual(eng["google"]["sessions"], 215)
        self.assertEqual(eng["bing"]["sessions"], 30)
        self.assertNotIn("gemini.google.com", eng)
        self.assertIn("high_single_page_share", {f["code"] for f in res["findings"]})

    def test_ai_referrals_floor(self):
        res = ph.cmd_ai(ph.config(), 90, 10)
        src = {s["source"]: s["sessions"] for s in res["sources"]}
        self.assertEqual(src, {"chatgpt.com": 18, "gemini.google.com": 4})
        self.assertEqual(res["trend"][0], {"week": "2026-11-02", "sessions": 9, "pageviews": 13, "chatgpt.com": 7, "perplexity.ai": 2})
        self.assertIn("floor", " ".join(res["notes"]).lower())
        self.assertEqual(res["findings"][0]["code"], "ai_referrals_floor")

    def test_cli_writes_and_never_prints_secret(self):
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = ph.main(["landing", "--seo-dir", td, "--json"])
            files = list((Path(td) / "audits").glob("posthog-landing-p1234-*"))
            written = "".join(f.read_text(encoding="utf-8") for f in files)
        self.assertEqual(rc, 0)
        self.assertEqual(len(files), 2)
        self.assertNotIn("phx_secret_value", out.getvalue() + written)
        self.assertEqual(json.loads(out.getvalue())["landing"][0]["landing"], "/pricing")

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"POSTHOG_PERSONAL_API_KEY": ""}):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(ph.main(["organic", "--no-write"]), 2)
            self.assertIn("POSTHOG_PERSONAL_API_KEY", err.getvalue())

    def test_lit_escapes(self):
        self.assertEqual(ph.lit("a'b\\."), "'a\\'b\\\\.'")


class BackoffTests(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        err = error.HTTPError("u", 429, "rate", {"Retry-After": "3"}, io.BytesIO(b"slow down"))
        ok = FakeResp(json.dumps({"results": [], "columns": []}).encode())
        sleeps = []
        with mock.patch.object(ph.request, "urlopen", side_effect=[err, ok]):
            d = ph.http_json("POST", "https://x/api", {}, {"a": 1}, sleep=sleeps.append)
        self.assertEqual(d, {"results": [], "columns": []})
        self.assertEqual(sleeps, [3.0])

    def test_4xx_raises_without_retry(self):
        err = error.HTTPError("u", 403, "forbidden", {}, io.BytesIO(b'{"detail":"no scope"}'))
        with mock.patch.object(ph.request, "urlopen", side_effect=[err]):
            with self.assertRaises(ph.ApiError) as cm:
                ph.http_json("POST", "https://x/api", {}, {"a": 1}, sleep=lambda s: None)
        self.assertIn("403", str(cm.exception))

    def test_gives_up_after_retries(self):
        errs = [error.HTTPError("u", 503, "x", {}, io.BytesIO(b"")) for _ in range(3)]
        with mock.patch.object(ph.request, "urlopen", side_effect=errs):
            with self.assertRaises(ph.ApiError):
                ph.http_json("GET", "https://x/api", {}, retries=2, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
