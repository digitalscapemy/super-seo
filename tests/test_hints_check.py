"""Offline tests for scripts/hints_check.py (HTML fixtures as strings)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hints_check as hc  # noqa: E402

URL = "https://acme.test/pricing"


def codes(r):
    return {x["code"]: x["severity"] for x in r["findings"]}


GOOD = """<!doctype html><html><head>
<link rel="preconnect" href="https://cdn.acme.test">
<link rel="preload" as="image" href="/hero.avif" fetchpriority="high">
<link rel="stylesheet" href="/app.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter&amp;display=swap">
<style>@font-face{font-family:X;src:url(x.woff2);font-display:swap}</style>
<script src="/app.js" defer></script><script type="module" src="/m.js"></script>
<script type="speculationrules">{"prerender":[{"where":{"href_matches":"/*"},"eagerness":"moderate"}]}</script>
<meta property="og:image" content="https://acme.test/og.png">
</head><body><header><img src="/logo.svg" width="120" height="40"></header>
<main><h1>Pricing</h1><img src="/hero.avif" width="1200" height="600" fetchpriority="high" alt="x"></main></body></html>"""


class Hints(unittest.TestCase):
    def test_clean_page(self):
        r = hc.analyze(GOOD, {"cache-control": "public, max-age=0, must-revalidate"}, URL)
        c = codes(r)
        self.assertIn("speculation_rules_present", c)
        self.assertTrue(all(v == "info" for v in c.values()), c)
        self.assertEqual(r["stats"]["hero"]["src"], "/hero.avif")
        self.assertEqual(r["stats"]["render_blocking_scripts"], 0)
        self.assertEqual(r["hint_score"], 100)

    def test_problems(self):
        preloads = "".join(f'<link rel="preload" as="font" href="/f{i}.woff2" crossorigin>' for i in range(8))
        css = "".join(f'<link rel="stylesheet" href="/s{i}.css">' for i in range(6))
        html = f"""<html><head>{preloads}{css}<link rel="prerender" href="/next">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">
<link rel="stylesheet" href="/print.css" media="print">
<style>@font-face{{font-family:Y;src:url(y.woff2)}}</style>
<script src="/jquery.js"></script><script src="https://www.googletagmanager.com/gtm.js"></script>
<script type="application/ld+json">{{}}</script>
<script>window.addEventListener('unload', function(){{}});</script>
</head><body onunload="x()"><main><img src="/hero.jpg" width="1600" height="900" loading="lazy"></main>
<script src="https://cdn.other.test/widget.js" async></script></body></html>"""
        r = hc.analyze(html, {"Cache-Control": "no-store"}, URL)
        c = codes(r)
        self.assertEqual(c["too_many_preloads"], "low")
        self.assertEqual(c["link_prerender_deprecated"], "low")
        self.assertEqual(c["hero_lazy"], "medium")
        self.assertEqual(c["hero_no_fetchpriority"], "low")
        self.assertEqual(c["render_blocking_script"], "medium")
        self.assertEqual(r["stats"]["render_blocking_scripts"], 2)
        self.assertEqual(c["many_blocking_stylesheets"], "low")
        self.assertEqual(r["stats"]["blocking_stylesheets"], 7)  # print.css excluded
        self.assertEqual(c["font_face_no_display"], "low")
        self.assertEqual(c["google_fonts_no_display"], "low")
        self.assertEqual(c["cache_no_store"], "medium")
        self.assertEqual(c["unload_handler"], "medium")
        self.assertEqual(c["speculation_rules_missing"], "info")
        self.assertEqual(r["stats"]["third_party_script_origins"], ["cdn.other.test", "www.googletagmanager.com"])
        self.assertLess(r["hint_score"], 100)

    def test_no_store_on_app_route_is_info(self):
        r = hc.analyze("<html><body></body></html>", {"cache-control": "no-store"}, "https://acme.test/dashboard")
        self.assertEqual(codes(r)["cache_no_store"], "info")

    def test_inline_js_and_dom(self):
        big = "var a=1;" * 14000  # ~112 KB
        html = "<html><head><script>" + big + "</script><script type=\"application/json\">" + "1" * 300000 + \
               "</script></head><body>" + "<div><span>x</span></div>" * 1600 + "</body></html>"
        r = hc.analyze(html, {}, URL)
        c = codes(r)
        self.assertEqual(c["large_inline_js"], "medium")
        self.assertEqual(c["large_inline_data"], "low")
        self.assertEqual(c["dom_size_large"], "medium")
        r2 = hc.analyze("<html><body>" + "<p>x</p>" * 1600 + "</body></html>", {}, URL)
        self.assertEqual(codes(r2)["dom_size"], "low")

    def test_hero_og_match_and_header_speculation(self):
        html = ('<html><head><meta property="og:image" content="https://cdn.test/img/cover.webp"></head><body><main>'
                '<img src="/a/small.png" width="50" height="50"><img src="/img/cover.webp"></main></body></html>')
        r = hc.analyze(html, {"Speculation-Rules": '"/rules.json"'}, URL)
        self.assertEqual(r["stats"]["hero"]["reason"], "matches og:image")
        self.assertIn("speculation_rules_present", codes(r))

    def test_scripts_in_body_not_blocking(self):
        html = '<html><head></head><body><script src="/late.js"></script></body></html>'
        self.assertEqual(hc.analyze(html, {}, URL)["stats"]["render_blocking_scripts"], 0)


if __name__ == "__main__":
    unittest.main()
