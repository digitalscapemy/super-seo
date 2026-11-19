"""Offline tests for scripts/sitemap_gen.py: crawl filtering, XML building, splitting, lastmod, validation."""
import io
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sitemap_gen as sg  # noqa: E402

NS = {"s": sg.NS}


def page(url, status=200, noindex=False, canonical=None, **kw):
    d = {"url": url, "final_url": url, "status": status, "noindex": noindex, "redirects": [],
         "canonical": canonical if canonical is not None else url, "issues": []}
    d["is_indexable"] = not noindex and (d["canonical"] == url)
    d.update(kw)
    return d


CRAWL = {
    "origin": "https://acme.test", "start": "https://acme.test/", "crawl_complete": True,
    "pages": [
        page("https://acme.test/"),
        page("https://acme.test/pricing"),
        page("https://acme.test/features/invoices"),
        page("https://acme.test/old", status=404),
        page("https://acme.test/private", noindex=True),
        page("https://acme.test/dup", canonical="https://acme.test/pricing"),
        page("https://acme.test/app/settings"),
        page("https://acme.test/login"),
        page("https://other.test/x"),
        page("https://acme.test/search?q=a"),
        page("https://acme.test/feed.xml", non_html="application/xml"),
        page("https://acme.test/pricing#plans"),
    ],
}


def fake_fetch(responses):
    def _f(url, ua, timeout=20.0, max_redirects=10, accept=None, max_bytes=15 * 1024 * 1024):
        r = responses.get(url)
        if r is None:
            return {"url": url, "final_url": url, "status": 404, "error": None, "chain": [], "headers": {}, "body": "", "bytes": 0}
        status, headers, body = r
        return {"url": url, "final_url": url, "status": status, "error": None, "chain": [],
                "headers": {k.lower(): v for k, v in headers.items()}, "body": body, "bytes": len(body.encode())}
    return _f


class CollectTests(unittest.TestCase):
    def test_crawl_filter(self):
        entries, excluded = sg.collect_from_crawl(CRAWL)
        locs = [e["loc"] for e in entries]
        self.assertEqual(locs, ["https://acme.test/", "https://acme.test/pricing", "https://acme.test/features/invoices"])
        self.assertEqual(excluded["non_200"], 1)
        self.assertEqual(excluded["noindex"], 1)
        self.assertEqual(excluded["canonicalized_elsewhere"], 1)
        self.assertEqual(excluded["app_path"], 2)
        self.assertEqual(excluded["other_host"], 1)
        self.assertEqual(excluded["query_string"], 1)
        self.assertEqual(excluded["duplicate"], 1)  # fragment variant of /pricing

    def test_url_list(self):
        entries, excluded = sg.collect_from_list(["# comment", "https://acme.test/a", "https://acme.test/a",
                                                  "/relative", "https://evil.test/b", "https://acme.test/c,2024-01-01"])
        self.assertEqual([e["loc"] for e in entries], ["https://acme.test/a", "https://acme.test/c"])
        self.assertEqual(excluded["duplicate"], 1)
        self.assertEqual(excluded["other_host"], 1)
        self.assertEqual(excluded["not_absolute_http_url"], 1)

    def test_rewrite_base(self):
        self.assertEqual(sg.rewrite_base("http://localhost:3000/pricing?x=1", "https://www.acme.com"),
                         "https://www.acme.com/pricing?x=1")
        self.assertEqual(sg.rewrite_base("https://preview.vercel.app/", "acme.com"), "https://acme.com/")


class DateTests(unittest.TestCase):
    def test_w3c(self):
        self.assertEqual(sg.w3c_date("2024-05-01"), "2024-05-01")
        self.assertEqual(sg.w3c_date("2024-05-01T10:00:00Z"), "2024-05-01T10:00:00+00:00")
        self.assertEqual(sg.w3c_date("2024-05-01T12:00:00+02:00"), "2024-05-01T10:00:00+00:00")
        self.assertEqual(sg.w3c_date("Wed, 21 Oct 2015 07:28:00 GMT"), "2015-10-21T07:28:00+00:00")
        self.assertIsNone(sg.w3c_date("yesterday"))
        self.assertIsNone(sg.w3c_date("2024-13-45"))

    def test_lastmod_csv(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lm.csv"
            p.write_text("url,lastmod\nhttps://acme.test/,2024-01-02\nhttps://acme.test/pricing,garbage\n", encoding="utf-8")
            entries = [{"loc": "https://acme.test/"}, {"loc": "https://acme.test/pricing"}]
            findings = sg.apply_lastmod(entries, str(p))
            self.assertEqual(entries[0]["lastmod"], "2024-01-02")
            self.assertNotIn("lastmod", entries[1])
            self.assertEqual(findings[0]["code"], "lastmod_csv_partial")

    def test_lastmod_http(self):
        resp = {"https://acme.test/": (200, {"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"}, "<html></html>"),
                "https://acme.test/b": (200, {}, "<html></html>")}
        entries = [{"loc": "https://acme.test/"}, {"loc": "https://acme.test/b"}]
        with mock.patch.object(sg.ca, "fetch", fake_fetch(resp)), mock.patch.object(sg.ca, "set_network_mode"):
            findings = sg.apply_lastmod(entries, "http", delay=0)
        self.assertEqual(entries[0]["lastmod"], "2015-10-21T07:28:00+00:00")
        self.assertEqual(findings[0]["code"], "lastmod_header_missing")


class BuildTests(unittest.TestCase):
    def test_escaping_and_no_legacy_tags(self):
        entries = [{"loc": "https://acme.test/a?x=1&y=<2>", "lastmod": "2024-01-01"}, {"loc": "https://acme.test/caf\u00e9"}]
        xml = sg.build_parts(entries)[0]
        self.assertIn("&amp;y=", xml)
        self.assertNotIn("<2>", xml)
        self.assertIn("caf%C3%A9", xml)
        self.assertNotIn("priority", xml)
        self.assertNotIn("changefreq", xml)
        root = ET.fromstring(xml.encode())
        self.assertEqual(len(root.findall("s:url", NS)), 2)
        self.assertEqual(root.find("s:url/s:lastmod", NS).text, "2024-01-01")

    def test_legacy_tags(self):
        xml = sg.build_parts([{"loc": "https://acme.test/"}], legacy=True)[0]
        self.assertIn("<priority>1.0</priority>", xml)

    def test_split_by_count_and_index(self):
        entries = [{"loc": f"https://acme.test/p{i}", "lastmod": f"2024-01-{i + 1:02d}"} for i in range(5)]
        gen = sg.generate(entries, split=2, legacy=False, index_base="https://www.acme.com")
        self.assertEqual(gen["parts"], 3)
        self.assertEqual(set(gen["files"]), {"sitemap-1.xml", "sitemap-2.xml", "sitemap-3.xml", "sitemap-index.xml"})
        idx = ET.fromstring(gen["files"]["sitemap-index.xml"].encode())
        locs = [e.text for e in idx.findall("s:sitemap/s:loc", NS)]
        self.assertEqual(locs[0], "https://www.acme.com/sitemap-1.xml")
        self.assertEqual(idx.find("s:sitemap/s:lastmod", NS).text, "2024-01-02")

    def test_split_by_bytes(self):
        entries = [{"loc": f"https://acme.test/{'x' * 200}{i}"} for i in range(10)]
        parts = sg.build_parts(entries, split=50000, max_bytes=1200)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p.encode()), 1200)
        self.assertEqual(sum(p.count("<url>") for p in parts), 10)

    def test_single_file_and_split_cap(self):
        gen = sg.generate([{"loc": "https://acme.test/"}], split=999999, legacy=False, index_base=None)
        self.assertEqual(list(gen["files"]), ["sitemap.xml"])

    def test_cli_from_crawl_writes(self):
        with tempfile.TemporaryDirectory() as d:
            cj = Path(d) / "crawl.json"
            cj.write_text(json.dumps(CRAWL), encoding="utf-8")
            out = Path(d) / "public"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = sg.main(["--from-crawl", str(cj), "--base", "https://www.acme.com", "--out", str(out), "--json"])
            self.assertEqual(code, 0)
            res = json.loads(buf.getvalue())
            self.assertEqual(res["url_count"], 3)
            xml = (out / "sitemap.xml").read_text(encoding="utf-8")
            self.assertIn("<loc>https://www.acme.com/pricing</loc>", xml)
            self.assertNotIn("acme.test", xml)


SITEMAP_OK = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://acme.test/</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://acme.test/pricing</loc><lastmod>2024-02-01</lastmod></url>
</urlset>"""


def urlset(locs, lastmod=None):
    body = "".join(f"<url><loc>{u}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>" for u in locs)
    return f'<?xml version="1.0"?><urlset xmlns="{sg.NS}">{body}</urlset>'


class ValidateTests(unittest.TestCase):
    def _file(self, d, name, text):
        p = Path(d) / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def codes(self, res):
        return {f["code"] for f in res["findings"]}

    def test_clean_file(self):
        with tempfile.TemporaryDirectory() as d:
            res = sg.validate(self._file(d, "sitemap.xml", SITEMAP_OK))
        self.assertEqual(res["url_count"], 2)
        self.assertFalse([f for f in res["findings"] if f["severity"] in ("critical", "high", "medium")])

    def test_problems(self):
        locs = [f"https://acme.test/p{i}" for i in range(12)] + ["https://acme.test/p1", "http://acme.test/plain",
                                                                 "https://staging.acme.test/x", "https://acme.test/app/home"]
        with tempfile.TemporaryDirectory() as d:
            res = sg.validate(self._file(d, "s.xml", urlset(locs, "2024-03-03T00:00:00+00:00")))
        c = self.codes(res)
        for code in ("duplicate_urls", "cross_host_urls", "non_https_urls", "identical_lastmod", "app_routes_listed"):
            self.assertIn(code, c)
        self.assertEqual(res["url_count"], 16)

    def test_doctype_refused(self):
        evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
        with tempfile.TemporaryDirectory() as d:
            res = sg.validate(self._file(d, "evil.xml", evil))
            self.assertIn("sitemap_doctype", self.codes(res))
            padded = "<?xml version='1.0'?><!--" + "x" * 6000 + "-->" + evil.split("?>", 1)[1]
            self.assertIn("sitemap_doctype", self.codes(sg.validate(self._file(d, "evil2.xml", padded))))

    def test_invalid_xml_and_wrong_root(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("sitemap_invalid_xml", self.codes(sg.validate(self._file(d, "a.xml", "<urlset><url>"))))
            self.assertIn("sitemap_wrong_root", self.codes(sg.validate(self._file(d, "b.xml", "<html></html>"))))

    def test_too_many_urls(self):
        with mock.patch.object(sg, "MAX_URLS", 3):
            with tempfile.TemporaryDirectory() as d:
                res = sg.validate(self._file(d, "s.xml", urlset([f"https://acme.test/{i}" for i in range(5)])))
        self.assertIn("sitemap_too_many_urls", self.codes(res))

    def test_index_local_children(self):
        with tempfile.TemporaryDirectory() as d:
            self._file(d, "sitemap-1.xml", urlset(["https://acme.test/a", "https://acme.test/b"]))
            self._file(d, "sitemap-2.xml", urlset(["https://acme.test/b"]))
            idx = (f'<?xml version="1.0"?><sitemapindex xmlns="{sg.NS}">'
                   "<sitemap><loc>https://acme.test/sitemap-1.xml</loc></sitemap>"
                   "<sitemap><loc>https://acme.test/sitemap-2.xml</loc></sitemap>"
                   "<sitemap><loc>https://acme.test/sitemap-3.xml</loc></sitemap></sitemapindex>")
            res = sg.validate(self._file(d, "sitemap-index.xml", idx))
        self.assertEqual(res["type"], "sitemapindex")
        self.assertEqual(res["url_count"], 3)
        self.assertIn("duplicate_urls", self.codes(res))
        self.assertIn("child_not_local", self.codes(res))

    def test_url_served_as_html_and_cross_host_child(self):
        resp = {"https://acme.test/sitemap.xml": (200, {"Content-Type": "text/html"}, "<html><body>app</body></html>"),
                "https://acme.test/idx.xml": (200, {"Content-Type": "application/xml"},
                                              f'<sitemapindex xmlns="{sg.NS}"><sitemap><loc>https://evil.test/s.xml</loc></sitemap>'
                                              "<sitemap><loc>https://acme.test/s1.xml</loc></sitemap></sitemapindex>"),
                "https://acme.test/s1.xml": (200, {"Content-Type": "application/xml"}, urlset(["https://acme.test/"]))}
        with mock.patch.object(sg.ca, "fetch", fake_fetch(resp)), mock.patch.object(sg.ca, "set_network_mode"):
            r1 = sg.validate("https://acme.test/sitemap.xml")
            r2 = sg.validate("https://acme.test/idx.xml")
            r3 = sg.validate("https://acme.test/missing.xml")
        self.assertIn("sitemap_served_as_html", self.codes(r1))
        self.assertIn("sitemap_child_cross_host", self.codes(r2))
        self.assertEqual(r2["url_count"], 1)
        self.assertIn("sitemap_unreachable", self.codes(r3))


if __name__ == "__main__":
    unittest.main()
