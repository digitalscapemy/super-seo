"""Offline tests for scripts/crawl_audit.py: parsing, page checks, network-safety helpers, decompression cap, sitemaps."""
import gzip
import socket
import sys
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import crawl_audit as ca  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "core"
FILLER = " ".join(["Acme sends invoices, tracks payments and reconciles bank feeds for freelancers."] * 30)


def page_html(head_extra="", body_extra="", title="Acme Invoicing for Freelancers | Acme",
              desc="Send invoices in two clicks, get paid by card or bank transfer, and see who owes you money at a glance.",
              canonical="https://acme.test/pricing", jsonld=None, robots_meta=None, body_text=FILLER, images=""):
    if jsonld is None:
        jsonld = ('{"@context":"https://schema.org","@type":"Organization","name":"Acme",'
                  '"url":"https://acme.test","logo":"https://acme.test/logo.png"}')
    parts = [
        '<!doctype html><html lang="en"><head>',
        f"<title>{title}</title>",
        f'<meta name="description" content="{desc}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<link rel="icon" href="/favicon.ico">',
        f'<link rel="canonical" href="{canonical}">' if canonical else "",
        f'<meta name="robots" content="{robots_meta}">' if robots_meta else "",
        '<meta property="og:title" content="Acme"><meta property="og:description" content="Invoices">',
        '<meta property="og:image" content="https://acme.test/og.png"><meta name="twitter:card" content="summary_large_image">',
        f'<script type="application/ld+json">{jsonld}</script>' if jsonld else "",
        head_extra, "</head><body><main>",
        '<h1>Invoicing for freelancers</h1>', images,
        f"<p>{body_text}</p>",
        '<a href="/features">Invoice features</a><a href="https://other.test/x">Partner</a>',
        body_extra, "</main></body></html>",
    ]
    return "".join(parts)


def res_for(body, url="https://acme.test/pricing", status=200, headers=None, chain=None):
    h = {"content-type": "text/html; charset=utf-8"}
    h.update(headers or {})
    return {"url": url, "final_url": url, "status": status, "error": None, "chain": chain or [], "headers": h,
            "body": body, "ttfb_ms": 50, "total_ms": 60, "bytes": len(body.encode()), "truncated": False}


def codes(page):
    return {i["code"]: i["severity"] for i in page["issues"]}


class PageParserTests(unittest.TestCase):
    def test_extraction(self):
        html = (FIX / "page.html").read_text(encoding="utf-8")
        p = ca.PageParser()
        p.feed(html)
        self.assertEqual(p.title.strip(), "Acme Invoicing for Freelancers | Acme")
        self.assertEqual(p.lang, "en")
        self.assertEqual(p.meta("description"), "Send invoices fast.")
        self.assertEqual(p.meta("OG:TITLE"), "Acme")
        self.assertEqual(p.link_rel("canonical")[0]["href"], "https://acme.test/")
        self.assertEqual([t for t, _ in p.headings], ["h1", "h2", "h3"])
        self.assertEqual(p.headings[0][1], "Invoicing made simple")
        self.assertEqual(len(p.images), 2)
        self.assertEqual(p.images[0].get("loading"), "lazy")
        self.assertEqual([a["text"] for a in p.anchors], ["Pricing plans", ""])
        self.assertEqual(len(p.jsonld_raw), 1)
        self.assertIn('"@type": "WebSite"', p.jsonld_raw[0])
        self.assertTrue(p.has_main)
        self.assertEqual(p.script_count, 2)
        text = " ".join(p.text_parts)
        self.assertNotIn("trackingCode", text)
        self.assertNotIn("WebSite", text)
        self.assertIn("Get paid faster", text)


class AuditPageTests(unittest.TestCase):
    def test_clean_page_has_no_high_issues(self):
        page = ca.audit_page(res_for(page_html()), "acme.test")
        bad = {c: s for c, s in codes(page).items() if s in ("critical", "high")}
        self.assertEqual(bad, {})
        self.assertTrue(page["is_indexable"])
        self.assertEqual(page["schema_types"], ["Organization"])
        self.assertIn("https://acme.test/features", page["internal_links"])
        self.assertEqual(page["external_links_count"], 1)

    def test_noindex_public_is_critical(self):
        page = ca.audit_page(res_for(page_html(robots_meta="noindex, follow")), "acme.test")
        self.assertEqual(codes(page)["noindex"], "critical")
        self.assertFalse(page["is_indexable"])

    def test_noindex_header_in_app_area_is_info(self):
        url = "https://acme.test/app/settings"
        page = ca.audit_page(res_for(page_html(canonical=url), url=url, headers={"x-robots-tag": "noindex"}), "acme.test")
        self.assertEqual(codes(page)["noindex"], "info")

    def test_csr_shell(self):
        body = ('<html lang="en"><head><title>App</title></head><body><div id="root"></div>'
                '<script src="/a.js"></script><script src="/b.js"></script><script src="/c.js"></script></body></html>')
        page = ca.audit_page(res_for(body), "acme.test")
        self.assertEqual(codes(page)["csr_shell"], "critical")

    def test_canonical_conflict_with_link_header(self):
        res = res_for(page_html(), headers={"link": '<https://acme.test/other>; rel="canonical"'})
        self.assertEqual(codes(ca.audit_page(res, "acme.test"))["canonical_conflict"], "high")

    def test_jsonld_invalid(self):
        page = ca.audit_page(res_for(page_html(jsonld='{"@context": "https://schema.org", "@type": "Organization",}')), "acme.test")
        self.assertEqual(codes(page)["jsonld_invalid"], "high")

    def test_jsonld_placeholder(self):
        ld = '{"@context":"https://schema.org","@type":"Organization","name":"[REPLACE]","url":"https://acme.test","logo":"https://acme.test/l.png"}'
        self.assertEqual(codes(ca.audit_page(res_for(page_html(jsonld=ld)), "acme.test"))["jsonld_placeholder"], "high")

    def test_jsonld_missing_props(self):
        ld = '{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Acme"},{"@type":"WebSite","name":"Acme","url":"https://acme.test"}]}'
        page = ca.audit_page(res_for(page_html(jsonld=ld)), "acme.test")
        msgs = [i["message"] for i in page["issues"] if i["code"] == "jsonld_missing_props"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("url", msgs[0])
        self.assertIn("logo", msgs[0])
        self.assertEqual(page["schema_types"], ["Organization", "WebSite"])

    def test_deprecated_schema_flagged(self):
        ld = '{"@context":"https://schema.org","@type":"HowTo","name":"Send an invoice"}'
        self.assertIn("schema_deprecated", codes(ca.audit_page(res_for(page_html(jsonld=ld)), "acme.test")))

    def test_templated_metadata(self):
        title = "Acme Invoicing Software for Freelancers | Acme"
        desc = "Acme Invoicing Software for Freelancers helps you bill clients and track payments. Get started today."
        page = ca.audit_page(res_for(page_html(title=title, desc=desc)), "acme.test")
        self.assertEqual(codes(page)["templated_metadata"], "high")
        self.assertEqual(page["desc_cta"], "get started today")
        desc2 = "Acme Invoicing Software for Freelancers helps you bill clients and track every payment in one place."
        self.assertEqual(codes(ca.audit_page(res_for(page_html(title=title, desc=desc2)), "acme.test"))["templated_metadata"], "medium")

    def test_first_image_lazy(self):
        imgs = '<img src="/hero.png" alt="Hero" loading="lazy" width="10" height="10"><img src="/b.png" alt="b">'
        self.assertIn("first_image_lazy", codes(ca.audit_page(res_for(page_html(images=imgs)), "acme.test")))
        imgs2 = '<img src="/hero.png" alt="Hero" fetchpriority="high"><img src="/b.png" alt="b" loading="lazy">'
        self.assertNotIn("first_image_lazy", codes(ca.audit_page(res_for(page_html(images=imgs2)), "acme.test")))

    def test_status_and_waf(self):
        self.assertEqual(codes(ca.audit_page(res_for("", status=500), "acme.test")), {"status_5xx": "critical"})
        self.assertIn("blocked_by_waf", codes(ca.audit_page(res_for("<title>Just a moment...</title>", status=403), "acme.test")))
        err = {"url": "x", "final_url": "x", "status": 0, "error": "boom", "chain": [], "headers": {}, "body": "",
               "ttfb_ms": None, "total_ms": None, "bytes": 0}
        self.assertEqual(codes(ca.audit_page(err, "acme.test")), {"fetch_error": "critical"})


class NetworkSafetyTests(unittest.TestCase):
    def test_ip_is_public(self):
        cases = {
            "8.8.8.8": True, "1.1.1.1": True, "2606:4700:4700::1111": True,
            "127.0.0.1": False, "10.1.2.3": False, "192.168.0.1": False, "172.16.5.5": False,
            "100.64.0.1": False, "100.127.255.254": False,        # CGNAT
            "169.254.169.254": False, "fe80::1": False, "fe80::1%eth0": False,  # link-local
            "::1": False, "0.0.0.0": False, "224.0.0.1": False,
            "::ffff:127.0.0.1": False, "::ffff:10.0.0.1": False, "::ffff:8.8.8.8": True,  # IPv4-mapped
            "2002:7f00:1::": False, "2002:a9fe:a9fe::": False, "2002:808:808::": True,  # 6to4
            "not-an-ip": False, "": False,
        }
        for addr, expected in cases.items():
            with self.subTest(addr=addr):
                self.assertEqual(ca.ip_is_public(addr), expected)

    def test_start_is_local_literals(self):
        for host in ("localhost", "app.localhost", "LOCALHOST.", "127.0.0.1", "[::1]", "10.0.0.5", "192.168.1.20"):
            with self.subTest(host=host):
                self.assertTrue(ca.start_is_local(host))
        self.assertFalse(ca.start_is_local(None))
        self.assertFalse(ca.start_is_local("8.8.8.8"))

    def test_start_is_local_dns(self):
        def fake(addrs):
            return lambda host, port: [(socket.AF_INET, 0, 0, "", (a, 0)) for a in addrs]
        with mock.patch.object(ca.socket, "getaddrinfo", fake(["10.0.0.7", "10.0.0.8"])):
            self.assertTrue(ca.start_is_local("staging.internal"))
        with mock.patch.object(ca.socket, "getaddrinfo", fake(["10.0.0.7", "93.184.216.34"])):
            self.assertFalse(ca.start_is_local("mixed.example"))
        with mock.patch.object(ca.socket, "getaddrinfo", side_effect=OSError("nxdomain")):
            self.assertFalse(ca.start_is_local("nope.invalid"))

    def test_url_allowed_blocks_schemes_and_private(self):
        saved = ca.PUBLIC_ONLY["on"]
        try:
            ca.PUBLIC_ONLY["on"] = True
            for u in ("file:///etc/passwd", "ftp://acme.test/x", "data:text/html,hi", "gopher://x"):
                with self.subTest(u=u):
                    self.assertIn("non-http", ca.url_allowed(u))
            with mock.patch.object(ca.socket, "getaddrinfo", return_value=[(socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))]):
                self.assertIn("private", ca.url_allowed("http://evil.test/"))
            with mock.patch.object(ca.socket, "getaddrinfo", return_value=[(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]):
                self.assertIsNone(ca.url_allowed("https://public.test/"))
            ca.PUBLIC_ONLY["on"] = False
            self.assertIsNone(ca.url_allowed("http://127.0.0.1:3000/"))
            self.assertIn("non-http", ca.url_allowed("file:///C:/Windows/win.ini"))
        finally:
            ca.PUBLIC_ONLY["on"] = saved

    def test_fetch_refuses_file_url_without_network(self):
        with mock.patch.object(ca._opener, "open", side_effect=AssertionError("must not open")):
            r = ca.fetch("file:///etc/passwd", "ua")
        self.assertEqual(r["status"], 0)
        self.assertIn("non-http", r["error"])

    def test_set_network_mode(self):
        saved = ca.PUBLIC_ONLY["on"]
        try:
            self.assertFalse(ca.set_network_mode("localhost"))
            self.assertFalse(ca.set_network_mode("8.8.8.8", allow_private=True))
            self.assertTrue(ca.set_network_mode("8.8.8.8"))
        finally:
            ca.PUBLIC_ONLY["on"] = saved


class DecodeBodyTests(unittest.TestCase):
    def test_gzip_bomb_is_capped(self):
        bomb = gzip.compress(b"\0" * (32 * 1024 * 1024), compresslevel=9)
        self.assertLess(len(bomb), 100_000)
        out, truncated = ca._decode_body(bomb, "gzip", 1024 * 1024)
        self.assertEqual(len(out), 1024 * 1024)
        self.assertTrue(truncated)

    def test_deflate_zlib_and_raw(self):
        data = b"hello world " * 100
        for raw in (zlib.compress(data), zlib.compress(data)[2:-4]):
            out, truncated = ca._decode_body(raw, "deflate", 10_000)
            self.assertEqual(out, data)
            self.assertFalse(truncated)

    def test_small_gzip_and_identity(self):
        out, truncated = ca._decode_body(gzip.compress(b"abc"), "GZIP", 100)
        self.assertEqual((out, truncated), (b"abc", False))
        self.assertEqual(ca._decode_body(b"abcdef", None, 3), (b"abc", True))
        self.assertEqual(ca._decode_body(b"not gzip", "gzip", 100), (b"not gzip", False))


class SitemapTests(unittest.TestCase):
    @staticmethod
    def _fetcher(bodies):
        def fake(url, ua, **kw):
            if url not in bodies:
                return {"status": 404, "error": None, "body": "", "headers": {}}
            return {"status": 200, "error": None, "body": bodies[url], "headers": {"content-type": "application/xml"}}
        return fake

    def test_doctype_refused(self):
        body = (FIX / "sitemap-doctype.xml").read_text(encoding="utf-8")
        with mock.patch.object(ca, "fetch", self._fetcher({"https://acme.test/sitemap.xml": body})):
            urls, problems = ca.read_sitemap_urls("https://acme.test/sitemap.xml", "ua")
        self.assertEqual(urls, [])
        self.assertTrue(any("DOCTYPE" in p for p in problems))

    def test_index_follows_same_host_only(self):
        index = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                 '<sitemap><loc>https://acme.test/sm-1.xml</loc></sitemap>'
                 '<sitemap><loc>https://evil.test/sm.xml</loc></sitemap></sitemapindex>')
        child = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                 '<url><loc>https://acme.test/</loc></url><url><loc>https://acme.test/pricing</loc></url></urlset>')
        with mock.patch.object(ca, "fetch", self._fetcher({"https://acme.test/sitemap.xml": index,
                                                           "https://acme.test/sm-1.xml": child})):
            urls, problems = ca.read_sitemap_urls("https://acme.test/sitemap.xml", "ua")
        self.assertEqual(urls, ["https://acme.test/", "https://acme.test/pricing"])
        self.assertTrue(any("cross-host" in p for p in problems))

    def test_invalid_xml(self):
        with mock.patch.object(ca, "fetch", self._fetcher({"https://acme.test/s.xml": "<urlset><url>"})):
            urls, problems = ca.read_sitemap_urls("https://acme.test/s.xml", "ua")
        self.assertEqual(urls, [])
        self.assertIn("not valid XML", problems[0])


if __name__ == "__main__":
    unittest.main()
