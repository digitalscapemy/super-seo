"""Offline tests for scripts/backlink_verify.py (fake fetcher, synthetic HTML)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backlink_verify as bv  # noqa: E402
import crawl_audit as ca  # noqa: E402
from robots_rfc import Robots  # noqa: E402


def res(url, body="", status=200, headers=None, final=None, error=None):
    h = {"content-type": "text/html; charset=utf-8"}
    h.update(headers or {})
    return {"url": url, "final_url": final or url, "status": status, "error": error, "chain": [], "headers": h,
            "body": body, "ttfb_ms": 10, "total_ms": 10, "bytes": len(body)}


def html(links="", head=""):
    return f"<html><head><title>Blog post about tools</title>{head}</head><body><main><p>Some text.</p>{links}</main></body></html>"


class ParseInput(unittest.TestCase):
    def test_txt(self):
        rows = bv.parse_input("https://a.test/x\n# comment\nb.test/y, https://acme.test/pricing\n", "acme.test")
        self.assertEqual(rows[0], {"source_url": "https://a.test/x", "expected_target": "acme.test", "expected_anchor": None})
        self.assertEqual(rows[1]["source_url"], "https://b.test/y")
        self.assertEqual(rows[1]["expected_target"], "https://acme.test/pricing")

    def test_bing_csv(self):
        text = '﻿"Source Url","Target Url","Anchor Text"\n"https://a.test/p","https://acme.test/","Acme tool"\n'
        rows = bv.parse_input(text)
        self.assertEqual(rows, [{"source_url": "https://a.test/p", "expected_target": "https://acme.test/", "expected_anchor": "Acme tool"}])

    def test_simple_csv_and_missing_column(self):
        rows = bv.parse_input("source_url,expected_target\nhttps://a.test/p,\n", "acme.test")
        self.assertEqual(rows[0]["expected_target"], "acme.test")
        with self.assertRaises(ValueError):
            bv.parse_input("foo,bar\n1,2\n")


class Analyze(unittest.TestCase):
    def test_dofollow(self):
        r = bv.analyze_source(res("https://a.test/p", html('<a href="https://www.acme.test/">Acme invoicing</a>')), "acme.test", True)
        self.assertEqual(r["state"], "live_dofollow")
        self.assertEqual(r["anchor"], "Acme invoicing")
        self.assertIsNone(r["rel"])

    def test_nofollow_variants(self):
        for rel in ("nofollow", "ugc", "sponsored noopener"):
            r = bv.analyze_source(res("https://a.test/p", html(f'<a rel="{rel}" href="https://acme.test/x">A</a>')), "acme.test")
            self.assertEqual(r["state"], "live_nofollow", rel)
        r = bv.analyze_source(res("https://a.test/p", html('<a href="https://acme.test/">A</a>', '<meta name="robots" content="nofollow">')), "acme.test")
        self.assertEqual(r["state"], "live_nofollow")

    def test_subdomain_and_lookalike(self):
        r = bv.analyze_source(res("https://a.test/p", html('<a href="https://app.acme.test/">A</a>')), "acme.test")
        self.assertTrue(r["link_found"])
        r = bv.analyze_source(res("https://a.test/p", html('<a href="https://notacme.test/">A</a>')), "acme.test")
        self.assertEqual(r["state"], "lost")

    def test_exact_url_target(self):
        body = html('<a href="https://acme.test/pricing/">P</a><a href="https://acme.test/">Home</a>')
        r = bv.analyze_source(res("https://a.test/p", body), "https://acme.test/pricing")
        self.assertEqual(len(r["links"]), 1)
        self.assertEqual(r["anchor"], "P")
        r = bv.analyze_source(res("https://a.test/p", html('<a href="/pricing">rel</a>')), "https://acme.test/pricing")
        self.assertEqual(r["state"], "lost")

    def test_relative_link_on_own_domain(self):
        r = bv.analyze_source(res("https://blog.acme.test/p", html('<a href="/x"><img src="a.png"></a>')), "acme.test")
        self.assertEqual(r["anchor"], "(image/empty)")

    def test_gone_blocked_error(self):
        self.assertEqual(bv.analyze_source(res("https://a.test/p", status=404), "acme.test")["state"], "page_gone")
        self.assertEqual(bv.analyze_source(res("https://a.test/p", status=503), "acme.test")["state"], "page_gone")
        r = bv.analyze_source(res("https://a.test/p", status=403), "acme.test")
        self.assertEqual((r["state"], r["status_class"]), ("unverified", "blocked"))
        r = bv.analyze_source(res("https://a.test/p", status=0, error="blocked private/internal address 10.0.0.1"), "acme.test")
        self.assertEqual((r["state"], r["status_class"]), ("unverified", "blocked"))

    def test_caveats(self):
        body = html('<a href="https://acme.test/">A</a>', '<meta name="robots" content="noindex"><link rel="canonical" href="https://a.test/other">')
        r = bv.analyze_source(res("https://a.test/p", body), "acme.test", googlebot_allowed=False)
        self.assertTrue(r["noindex"])
        self.assertTrue(r["canonicalised_elsewhere"])
        self.assertEqual(len(r["caveats"]), 3)
        r = bv.analyze_source(res("https://a.test/p", html('<a href="https://acme.test/">A</a>'), headers={"x-robots-tag": "noindex"}), "acme.test")
        self.assertTrue(r["noindex"])


class Run(unittest.TestCase):
    def test_run_summary_and_guard_mode(self):
        pages = {
            "https://a.test/1": res("https://a.test/1", html('<a href="https://acme.test/">Acme</a>')),
            "https://a.test/2": res("https://a.test/2", html('<a rel="nofollow" href="https://acme.test/">Acme</a>')),
            "https://b.test/3": res("https://b.test/3", html("<p>nothing</p>")),
            "https://b.test/4": res("https://b.test/4", status=410),
        }
        robots = {"https://a.test": {"parser": Robots("User-agent: Googlebot\nDisallow: /2\n")}, "https://b.test": {"parser": None}}
        rows = [{"source_url": u, "expected_target": "acme.test", "expected_anchor": None} for u in pages]
        rows[0]["expected_anchor"] = "Something else"
        with mock.patch.object(ca, "set_network_mode") as snm:
            r = bv.run(rows, delay=0, fetcher=lambda u: pages[u], robots_reader=lambda o: robots[o])
        self.assertEqual([c.args[0] for c in snm.call_args_list], ["a.test", "a.test", "b.test", "b.test"])
        self.assertEqual(r["summary"], {"live_dofollow": 1, "live_nofollow": 1, "lost": 1, "page_gone": 1, "unverified": 0})
        by = {x["source_url"]: x for x in r["results"]}
        self.assertFalse(by["https://a.test/2"]["googlebot_allowed"])
        self.assertTrue(by["https://a.test/1"]["googlebot_allowed"])
        self.assertIsNone(by["https://b.test/3"]["googlebot_allowed"])
        self.assertTrue(any("anchor changed" in c for c in by["https://a.test/1"]["caveats"]))
        md = bv.to_markdown(r, "acme.test")
        self.assertIn("Live dofollow: 1", md)


if __name__ == "__main__":
    unittest.main()
