"""Offline tests for extensions/umami/umami.py (HTTP mocked)."""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions" / "umami"))
import umami as um  # noqa: E402

SITE = "4fb7fa4c-5b46-438d-94b3-3a8fb9bc2e8b"


class UmamiTests(unittest.TestCase):
    def fake(self, method, url, headers, body=None, **kw):
        self.calls.append((method, url, headers, body))
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path.endswith("/auth/login"):
            return {"token": "bearer-tok"}
        if u.path.endswith("/stats"):
            return {"visits": {"value": 200}, "bounces": {"value": 170}}
        t = q.get("type")
        if t == "referrer":
            return [{"x": "google.com", "y": 120}, {"x": "www.bing.com", "y": 10}, {"x": "chatgpt.com", "y": 7},
                    {"x": "perplexity.ai", "y": 2}, {"x": "github.com", "y": 50}]
        if t == "entry":
            if self.no_entry:
                raise um.ApiError("HTTP 400: invalid type")
            return [{"x": "/pricing", "y": 40}, {"x": "/", "y": 20}]
        if t == "url":
            return [{"x": "/blog/a", "y": 9}]
        raise AssertionError(url)

    def setUp(self):
        self.calls, self.no_entry = [], False
        p = mock.patch.object(um, "http_json", side_effect=self.fake)
        p.start()
        self.addCleanup(p.stop)

    def env(self, **kv):
        base = {"UMAMI_URL": "", "UMAMI_API_KEY": "", "UMAMI_USERNAME": "", "UMAMI_PASSWORD": "", "UMAMI_WEBSITE_ID": SITE}
        base.update(kv)
        return mock.patch.dict(os.environ, base)

    def test_cloud_config_and_headers(self):
        with self.env(UMAMI_API_KEY="cloud_secret"):
            cfg = um.config()
            self.assertEqual(cfg["base"], "https://api.umami.is/v1")
            res = um.cmd_organic(cfg, 28, 10)
        method, url, headers, _ = self.calls[0]
        self.assertEqual(headers, {"x-umami-api-key": "cloud_secret"})
        self.assertTrue(url.startswith(f"https://api.umami.is/v1/websites/{SITE}/metrics?"))
        q = parse_qs(urlparse(url).query)
        self.assertEqual(int(q["endAt"][0]) - int(q["startAt"][0]), 28 * 86400000)
        self.assertEqual({e["source"]: e["visitors"] for e in res["engines"]}, {"google": 120, "bing": 10})
        filtered = [parse_qs(urlparse(c[1]).query).get("referrer", [None])[0] for c in self.calls[1:]]
        self.assertEqual(sorted(filtered), ["google.com", "www.bing.com"])
        self.assertEqual(res["landing"][0], {"landing": "/pricing", "count": 80})

    def test_self_hosted_login(self):
        with self.env(UMAMI_URL="https://umami.example.com", UMAMI_USERNAME="viewer", UMAMI_PASSWORD="pw"):
            cfg = um.config()
        self.assertEqual(self.calls[0][1], "https://umami.example.com/api/auth/login")
        self.assertEqual(self.calls[0][3], {"username": "viewer", "password": "pw"})
        self.assertEqual(cfg["headers"], {"Authorization": "Bearer bearer-tok"})
        self.assertEqual(cfg["base"], "https://umami.example.com/api")

    def test_ai_and_trend(self):
        with self.env(UMAMI_API_KEY="k"):
            res = um.cmd_ai(um.config(), 14, 10)
        self.assertEqual({s["source"]: s["visitors"] for s in res["sources"]}, {"chatgpt.com": 7, "perplexity.ai": 2})
        self.assertEqual(len(res["trend"]), 2)
        self.assertEqual(res["trend"][0]["visitors"], 9)
        self.assertIn("floor", res["notes"][0])

    def test_landing_fallback_and_bounce(self):
        self.no_entry = True
        with self.env(UMAMI_API_KEY="k"):
            res = um.cmd_landing(um.config(), 28, 10)
        self.assertEqual(res["page_metric"], "url")
        self.assertEqual(res["landing"], [{"landing": "/blog/a", "count": 9}])
        self.assertIn("high_single_page_share", {f["code"] for f in res["findings"]})

    def test_missing_auth(self):
        with self.env(UMAMI_URL="https://u.example.com"), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(um.main(["organic", "--no-write"]), 2)
        self.assertIn("UMAMI_API_KEY", err.getvalue())

    def test_cli_writes_no_secret(self):
        with self.env(UMAMI_API_KEY="cloud_secret"), tempfile.TemporaryDirectory() as td, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(um.main(["ai-referrals", "--days", "7", "--seo-dir", td]), 0)
            written = "".join(p.read_text(encoding="utf-8") for p in (Path(td) / "audits").glob("umami-ai-referrals-*"))
        self.assertNotIn("cloud_secret", out.getvalue() + written)
        self.assertIn("AI assistant referrals", written)

    def test_window(self):
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)
        s, e = um.window(1, now)
        self.assertEqual(e - s, 86400000)


if __name__ == "__main__":
    unittest.main()
