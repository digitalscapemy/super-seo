"""Offline tests for extensions/bing/bing.py (urllib is mocked; no network)."""
from __future__ import annotations

import contextlib
import email.message
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ext_bing", ROOT / "extensions" / "bing" / "bing.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class FakeResp:
    def __init__(self, payload):
        self._b = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = 200

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, body: bytes):
    return error.HTTPError(url, code, "err", email.message.Message(), io.BytesIO(body))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = b.main(argv)
    return rc, out.getvalue(), err.getvalue()


def method_of(req) -> str:
    return urlparse(req.full_url).path.rsplit("/", 1)[-1]


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"BING_WEBMASTER_API_KEY": "SECRETKEY123"})
        self.env.start()
        self.sl = mock.patch.object(b, "_sleep", lambda s: None)
        self.sl.start()

    def tearDown(self):
        self.sl.stop()
        self.env.stop()


class TestBing(Base):
    def test_dates(self):
        self.assertEqual(b.parse_bing_dates("/Date(1700000000000)/"), "2023-11-14T22:13:20Z")
        self.assertEqual(b.parse_bing_dates({"x": ["/Date(1700000000000-0800)/", "plain"]}),
                         {"x": ["2023-11-14T22:13:20Z", "plain"]})

    def test_missing_key(self):
        os.environ.pop("BING_WEBMASTER_API_KEY")
        rc, _, err = run(["quota", "--site", "https://ex.com/", "--no-write"])
        self.assertEqual(rc, 2)
        self.assertIn("BING_WEBMASTER_API_KEY", err)

    def test_sites_unwraps_d(self):
        def fake(req, timeout=None):
            self.assertEqual(method_of(req), "GetUserSites")
            self.assertEqual(parse_qs(urlparse(req.full_url).query)["apikey"], ["SECRETKEY123"])
            return FakeResp({"d": [{"Url": "https://ex.com/", "IsVerified": True}, {"Url": "https://b.com/", "IsVerified": False}]})
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, _ = run(["sites", "--json", "--no-write"])
        d = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(d["sites"][0]["url"], "https://ex.com/")
        self.assertEqual([f["code"] for f in d["findings"]], ["site_not_verified"])
        self.assertNotIn("SECRETKEY123", out)

    def test_queries_aggregate(self):
        rows = [{"Query": "invoice app", "Impressions": 100, "Clicks": 5, "AvgImpressionPosition": 4, "Date": "/Date(1700000000000)/"},
                {"Query": "invoice app", "Impressions": 300, "Clicks": 10, "AvgImpressionPosition": 8, "Date": "/Date(1700600000000)/"},
                {"Query": "other", "Impressions": 50, "Clicks": 0, "AvgImpressionPosition": 20, "Date": "/Date(1700600000000)/"}]
        with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp({"d": rows})):
            rc, out, _ = run(["queries", "--site", "https://ex.com/", "--json", "--no-write"])
        top = json.loads(out)["rows"][0]
        self.assertEqual((top["query"], top["impressions"], top["clicks"], top["avg_position"]), ("invoice app", 400, 15, 7.0))

    def test_links_pagination(self):
        pages = []

        def fake(req, timeout=None):
            p = int(parse_qs(urlparse(req.full_url).query)["page"][0])
            pages.append(p)
            return FakeResp({"d": {"Links": [{"Url": f"https://ex.com/{p}", "Count": 10 + p}], "TotalPages": 2}})
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, _ = run(["links", "--site", "https://ex.com/", "--json", "--no-write"])
        d = json.loads(out)
        self.assertEqual(pages, [0, 1])
        self.assertEqual(d["total_links"], 21)
        self.assertEqual(d["pages"][0]["url"], "https://ex.com/1")

    def test_crawl_issues_flags(self):
        raw = [{"Url": "https://ex.com/x", "Issues": 8, "HttpCode": 503, "InLinks": 2},
               {"Url": "https://ex.com/y", "Issues": 4 | 16, "HttpCode": 404, "InLinks": 0}]
        with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp({"d": raw})):
            rc, out, _ = run(["crawl-issues", "--site", "https://ex.com/", "--json", "--no-write"])
        d = json.loads(out)
        self.assertEqual(d["issues"][1]["issues"], ["4xx", "blocked_by_robots"])
        self.assertEqual(d["findings"][0]["severity"], "high")

    def test_submit_chunks_to_quota_and_filters_host(self):
        posted = []

        def fake(req, timeout=None):
            m = method_of(req)
            if m == "GetUrlSubmissionQuota":
                return FakeResp({"d": {"DailyQuota": 2, "MonthlyQuota": 100}})
            self.assertEqual(m, "SubmitUrlBatch")
            self.assertEqual(req.get_method(), "POST")
            posted.append(json.loads(req.data))
            return FakeResp({"d": None})
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, _ = run(["submit", "--site", "https://ex.com/", "https://ex.com/a", "https://www.ex.com/b",
                              "https://ex.com/c", "https://evil.com/x", "--json", "--no-write"])
        d = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(posted, [{"siteUrl": "https://ex.com/", "urlList": ["https://ex.com/a", "https://www.ex.com/b"]}])
        codes = [f["code"] for f in d["findings"]]
        self.assertIn("quota_trimmed", codes)
        self.assertIn("off_site_urls_skipped", codes)

    def test_raw_error_shown_and_key_redacted(self):
        body = b'{"ErrorCode":14,"Message":"NotAuthorized SECRETKEY123"}'
        with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: (_ for _ in ()).throw(http_error(r.full_url, 400, body))):
            rc, out, err = run(["quota", "--site", "https://ex.com/", "--no-write"])
        self.assertEqual(rc, 1)
        self.assertIn("NotAuthorized", err)
        self.assertIn("ErrorCode", err)
        self.assertNotIn("SECRETKEY123", err + out)

    def test_retry_on_503(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise http_error(req.full_url, 503, b"busy")
            return FakeResp({"d": {"DailyQuota": 10, "MonthlyQuota": 10}})
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, _ = run(["quota", "--site", "https://ex.com/", "--json", "--no-write"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(out)["quota"]["daily"], 10)

    def test_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp({"d": {"DailyQuota": 1, "MonthlyQuota": 1}})):
                    rc, _, _ = run(["quota", "--site", "https://ex.com/"])
                names = sorted(p.name for p in (Path(tmp) / ".seo" / "audits").iterdir())
            finally:
                os.chdir(cwd)
        self.assertEqual(rc, 0)
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("bing-quota-ex.com-") and names[0].endswith(".json"))


if __name__ == "__main__":
    unittest.main()
