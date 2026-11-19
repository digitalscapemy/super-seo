"""Offline tests for extensions/indexnow/indexnow.py (fetch and urllib are mocked; no network)."""
from __future__ import annotations

import contextlib
import email.message
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ext_indexnow", ROOT / "extensions" / "indexnow" / "indexnow.py")
ix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ix)

KEY = "0123456789abcdef0123456789abcdef"


class FakeResp:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._b = body

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fetched(status=200, body="", ctype="text/plain", final_url=None, chain=None, err=None):
    return {"status": status, "body": body, "headers": {"content-type": ctype} if ctype else {}, "error": err,
            "final_url": final_url, "chain": chain or []}


def run(argv):
    out, errb = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errb):
        rc = ix.main(argv)
    return rc, out.getvalue(), errb.getvalue()


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ex.com/old</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://ex.com/new</loc><lastmod>2026-11-10T08:00:00+00:00</lastmod></url>
  <url><loc>https://ex.com/nolastmod</loc></url>
  <url><loc>https://other.com/x</loc><lastmod>2026-11-12</lastmod></url>
</urlset>"""


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"INDEXNOW_KEY": KEY})
        self.env.start()
        self.nm = mock.patch.object(ix.crawl_audit, "set_network_mode", lambda *a, **k: True)
        self.nm.start()

    def tearDown(self):
        self.nm.stop()
        self.env.stop()


class TestKeygen(Base):
    def test_keygen_without_env(self):
        os.environ.pop("INDEXNOW_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = run(["keygen", "--public-dir", tmp, "--json"])
            d = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertRegex(d["key"], r"^[0-9a-f]{32}$")
            p = Path(tmp) / f"{d['key']}.txt"
            self.assertEqual(p.read_text(encoding="utf-8"), d["key"])
            self.assertFalse((Path(tmp) / ".seo").exists())


class TestVerify(Base):
    def test_ok(self):
        with mock.patch.object(ix.crawl_audit, "fetch", return_value=fetched(200, KEY + "\n", "text/plain; charset=utf-8")) as f:
            rc, out, _ = run(["verify", "--host", "ex.com", "--json", "--no-write"])
        self.assertEqual(f.call_args[0][0], f"https://ex.com/{KEY}.txt")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["valid"])

    def test_404(self):
        with mock.patch.object(ix.crawl_audit, "fetch", return_value=fetched(404, "<html>not found</html>", "text/html")):
            rc, out, _ = run(["verify", "--host", "https://ex.com/", "--json", "--no-write"])
        d = json.loads(out)
        self.assertEqual(rc, 1)
        self.assertEqual([x["code"] for x in d["findings"]], ["key_file_missing"])

    def test_spa_fallback_and_content_type(self):
        with mock.patch.object(ix.crawl_audit, "fetch", return_value=fetched(200, "<!doctype html><div id=root>", "text/html")):
            rc, out, _ = run(["verify", "--host", "ex.com", "--json", "--no-write"])
        codes = {x["code"]: x["severity"] for x in json.loads(out)["findings"]}
        self.assertEqual(codes, {"key_mismatch": "high", "key_content_type": "medium"})

    def test_missing_key(self):
        os.environ.pop("INDEXNOW_KEY")
        rc, _, err = run(["verify", "--host", "ex.com", "--no-write"])
        self.assertEqual(rc, 2)
        self.assertIn("INDEXNOW_KEY", err)


class TestSubmit(Base):
    def test_from_sitemap_since_filters(self):
        posted = []

        def fake_open(req, timeout=None):
            self.assertEqual(req.full_url, ix.ENDPOINT)
            posted.append(json.loads(req.data))
            return FakeResp(200)
        with mock.patch.object(ix.crawl_audit, "fetch", return_value=fetched(200, SITEMAP, "application/xml")), \
                mock.patch("urllib.request.urlopen", side_effect=fake_open):
            rc, out, err = run(["submit", "--host", "ex.com", "--from-sitemap", "https://ex.com/sitemap.xml",
                                "--since", "2026-11-01", "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(posted, [{"host": "ex.com", "key": KEY, "keyLocation": f"https://ex.com/{KEY}.txt",
                                   "urlList": ["https://ex.com/new"]}])
        codes = [f["code"] for f in json.loads(out)["findings"]]
        self.assertIn("no_lastmod", codes)
        self.assertIn("off_host_skipped", codes)

    def test_chunking_10000(self):
        sizes = []

        def fake_open(req, timeout=None):
            sizes.append(len(json.loads(req.data)["urlList"]))
            return FakeResp(202)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "urls.txt"
            f.write_text("\n".join(f"https://ex.com/p{i}" for i in range(10005)), encoding="utf-8")
            with mock.patch("urllib.request.urlopen", side_effect=fake_open):
                rc, out, _ = run(["submit", "--host", "ex.com", "--file", str(f), "--json", "--no-write"])
        self.assertEqual(rc, 0)
        self.assertEqual(sizes, [10000, 5])
        self.assertEqual(json.loads(out)["submitted"], 10005)

    def test_status_interpretation(self):
        for code, word in ((403, "Key not valid"), (422, "do not belong"), (429, "Too many"), (400, "Bad request")):
            def fake_open(req, timeout=None, code=code):
                raise error.HTTPError(req.full_url, code, "x", email.message.Message(), io.BytesIO(b""))
            with mock.patch("urllib.request.urlopen", side_effect=fake_open):
                rc, out, _ = run(["submit", "--host", "ex.com", "https://ex.com/a", "--json", "--no-write"])
            d = json.loads(out)
            self.assertEqual(rc, 1, code)
            self.assertEqual(d["batches"][0]["status"], code)
            self.assertIn(word, d["batches"][0]["result"])
            self.assertEqual(d["findings"][0]["code"], f"indexnow_{code}")

    def test_dry_run_sends_nothing(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network in dry run")):
            rc, out, _ = run(["submit", "--host", "ex.com", "https://ex.com/a", "https://ex.com/a", "--dry-run", "--json", "--no-write"])
        d = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(d["urls"], ["https://ex.com/a"])

    def test_sitemap_index_and_doctype_refused(self):
        index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://ex.com/s1.xml</loc></sitemap><sitemap><loc>https://evil.com/s.xml</loc></sitemap></sitemapindex>"""
        child = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://ex.com/z</loc></url></urlset>'
        resp = {"https://ex.com/sitemap.xml": fetched(200, index, "application/xml"),
                "https://ex.com/s1.xml": fetched(200, child, "application/xml")}
        with mock.patch.object(ix.crawl_audit, "fetch", side_effect=lambda u, *a, **k: resp[u]):
            entries, problems = ix.sitemap_entries("https://ex.com/sitemap.xml")
        self.assertEqual(entries, [("https://ex.com/z", None)])
        self.assertTrue(any("cross-host" in p for p in problems))
        evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><urlset/>'
        with mock.patch.object(ix.crawl_audit, "fetch", return_value=fetched(200, evil, "application/xml")):
            entries, problems = ix.sitemap_entries("https://ex.com/sitemap.xml")
        self.assertEqual(entries, [])
        self.assertTrue(re.search("DOCTYPE", problems[0]))


if __name__ == "__main__":
    unittest.main()
