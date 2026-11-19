"""Offline tests for scripts/hreflang_check.py (fake fetcher, no network)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import hreflang_check as hc  # noqa: E402


def page(links, canonical=None, lang="en", robots=None):
    head = "".join(f'<link rel="alternate" hreflang="{c}" href="{h}">' for c, h in links)
    if canonical:
        head += f'<link rel="canonical" href="{canonical}">'
    if robots:
        head += f'<meta name="robots" content="{robots}">'
    return f'<!doctype html><html lang="{lang}"><head><title>t</title>{head}</head><body><p>x</p></body></html>'


class FakeFetcher:
    """routes: {url: body | (status, body) | ("redirect", to)}"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, ua, accept=None, **kw):
        self.calls.append(url)
        chain = []
        cur = url
        for _ in range(5):
            r = self.routes.get(cur)
            if isinstance(r, tuple) and r[0] == "redirect":
                chain.append({"url": cur, "status": 301, "to": r[1]})
                cur = r[1]
                continue
            break
        if r is None:
            return {"url": url, "final_url": cur, "status": 404, "error": None, "chain": chain,
                    "headers": {"content-type": "text/html"}, "body": "not found"}
        status, body = (r if isinstance(r, tuple) else (200, r))
        ctype = "application/xml" if body.lstrip().startswith("<?xml") or "<urlset" in body[:200] or "<sitemapindex" in body[:200] else "text/html"
        return {"url": url, "final_url": cur, "status": status, "error": None, "chain": chain,
                "headers": {"content-type": ctype}, "body": body}


EN, DE, FR = "https://ex.com/en/", "https://ex.com/de/", "https://ex.com/fr/"


def codes(result):
    return [i["code"] for i in result["issues"]]


class ValidateCode(unittest.TestCase):
    def ok(self, c):
        self.assertEqual(hc.validate_code(c), [], c)

    def bad(self, c, code="hreflang_invalid_code"):
        got = [i["code"] for i in hc.validate_code(c)]
        self.assertIn(code, got, f"{c}: {got}")

    def test_valid(self):
        for c in ("en", "en-GB", "en-gb", "ms-MY", "zh-Hans", "zh-Hant-TW", "x-default", "X-Default", "pt-BR", "lo-LA"):
            self.ok(c)

    def test_common_mistakes(self):
        self.bad("en-uk")
        self.assertIn("en-GB", hc.validate_code("en-uk")[0]["message"])
        self.bad("jp")
        self.assertEqual(hc.validate_code("jp")[0]["suggestion"], "ja")
        self.bad("es-LA")
        self.bad("es-419")
        self.bad("en-EU")
        self.bad("en_US")
        self.bad("english")
        self.bad("eng")
        self.bad("en-XX")

    def test_region_only(self):
        self.bad("us", "hreflang_region_only")
        self.bad("gb", "hreflang_region_only")

    def test_my_for_malay(self):
        self.bad("my-MY", "hreflang_my_for_malay")
        self.assertEqual([i["code"] for i in hc.validate_code("my", page_lang="ms")], ["hreflang_my_for_malay"])
        self.assertEqual([i["severity"] for i in hc.validate_code("my", page_lang="my")], ["medium"])

    def test_unknown_script(self):
        self.bad("zh-Abcd", "hreflang_unknown_script")

    def test_code_tables(self):
        self.assertEqual(len(hc.ISO_3166_1), 249)
        self.assertGreaterEqual(len(hc.ISO_639_1), 183)


class Cluster(unittest.TestCase):
    def full_mesh(self):
        links = [("en", EN), ("de", DE), ("fr", FR), ("x-default", EN)]
        return {EN: page(links, EN, "en"), DE: page(links, DE, "de"), FR: page(links, FR, "fr")}

    def test_clean_full_mesh(self):
        r = hc.run(EN, fetcher=FakeFetcher(self.full_mesh()), delay=0)
        bad = [i for i in r["issues"] if i["severity"] in ("critical", "high", "medium")]
        self.assertEqual(bad, [])
        self.assertEqual(r["clusters"][0]["size"], 3)
        self.assertTrue(r["clusters"][0]["has_x_default"])
        self.assertTrue(r["complete"])

    def test_missing_return_and_self(self):
        routes = self.full_mesh()
        routes[DE] = page([("de", DE)], DE, "de")  # DE drops EN/FR
        routes[FR] = page([("en", EN), ("de", DE)], FR, "fr")  # FR omits itself
        r = hc.run(EN, fetcher=FakeFetcher(routes), delay=0)
        miss = [i for i in r["issues"] if i["code"] == "hreflang_missing_return"]
        self.assertTrue(any(i["url"] == EN and i["target"] == DE for i in miss))
        self.assertTrue(all(i["severity"] == "high" for i in miss))
        self.assertNotIn("ignores the whole", " ".join(i["message"] for i in miss))
        self.assertIn("hreflang_missing_self", codes(r))

    def test_target_not_canonical_trailing_slash_and_protocol(self):
        routes = self.full_mesh()
        routes[DE] = page([("en", EN), ("de", DE), ("fr", FR)], "https://ex.com/de", "de")
        routes[FR] = page([("en", EN), ("de", DE), ("fr", FR)], "http://ex.com/fr/", "fr")
        r = hc.run(EN, fetcher=FakeFetcher(routes), delay=0)
        nc = {i["url"]: i["message"] for i in r["issues"] if i["code"] == "hreflang_target_not_canonical"}
        self.assertIn("trailing slash", nc[DE])
        self.assertIn("protocol", nc[FR])

    def test_redirect_404_noindex_targets(self):
        links = [("en", EN), ("de", DE), ("fr", FR), ("es", "https://ex.com/es/")]
        routes = {EN: page(links, EN), DE: ("redirect", "https://ex.com/de-de/"),
                  "https://ex.com/de-de/": page(links, "https://ex.com/de-de/", "de"),
                  FR: page(links, FR, "fr", robots="noindex")}
        r = hc.run(EN, fetcher=FakeFetcher(routes), delay=0)
        c = codes(r)
        self.assertIn("hreflang_target_redirects", c)
        self.assertIn("hreflang_target_noindex", c)
        self.assertIn("hreflang_target_non_200", c)

    def test_duplicate_code_and_multiple_x_default(self):
        links = [("en", EN), ("en", "https://ex.com/en-2/"), ("x-default", EN), ("x-default", DE), ("de", DE)]
        r = hc.run(EN, fetcher=FakeFetcher({EN: page(links, EN), DE: page(links, DE, "de")}), delay=0, max_pages=2)
        c = codes(r)
        self.assertIn("hreflang_duplicate_code", c)
        self.assertIn("hreflang_multiple_x_default", c)

    def test_missing_x_default_is_low(self):
        links = [("en", EN), ("de", DE)]
        r = hc.run(EN, fetcher=FakeFetcher({EN: page(links, EN), DE: page(links, DE, "de")}), delay=0)
        xd = [i for i in r["issues"] if i["code"] == "hreflang_no_x_default"]
        self.assertEqual(len(xd), 1)
        self.assertEqual(xd[0]["severity"], "low")

    def test_invalid_code_deduped_with_pages(self):
        links = [("en", EN), ("en-uk", DE)]
        r = hc.run(EN, fetcher=FakeFetcher({EN: page(links, EN), DE: page(links, DE)}), delay=0)
        inv = [i for i in r["issues"] if i["code"] == "hreflang_invalid_code"]
        self.assertEqual(len(inv), 1)
        self.assertEqual(len(inv[0]["pages"]), 2)
        self.assertEqual(inv[0]["severity"], "high")

    def test_relative_href(self):
        links = [("en", "/en/"), ("de", DE)]
        r = hc.run(EN, fetcher=FakeFetcher({EN: page(links, EN), DE: page([("en", EN), ("de", DE)], DE, "de")}), delay=0)
        self.assertIn("hreflang_relative_url", codes(r))

    def test_max_pages_partial(self):
        r = hc.run(EN, fetcher=FakeFetcher(self.full_mesh()), delay=0, max_pages=1)
        self.assertFalse(r["complete"])
        self.assertIn("hreflang_return_unverified", codes(r))

    def test_no_hreflang(self):
        r = hc.run(EN, fetcher=FakeFetcher({EN: page([], EN)}), delay=0)
        self.assertIn("hreflang_none", codes(r))


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url><loc>{en}</loc>
    <xhtml:link rel="alternate" hreflang="en" href="{en}"/>
    <xhtml:link rel="alternate" hreflang="de" href="{de}"/>
  </url>
  <url><loc>{de}</loc>
    <xhtml:link rel="alternate" hreflang="en" href="{en}"/>
    <xhtml:link rel="alternate" hreflang="de" href="{de}"/>
  </url>
</urlset>""".format(en=EN, de=DE)


class Sitemap(unittest.TestCase):
    def test_parse_sitemap(self):
        entries, kids, problems = hc.parse_sitemap_xml(SITEMAP)
        self.assertEqual(set(entries), {EN, DE})
        self.assertEqual(len(entries[EN]), 2)
        self.assertEqual(problems, [])

    def test_refuses_doctype(self):
        evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><urlset></urlset>'
        entries, _, problems = hc.parse_sitemap_xml(evil)
        self.assertEqual(entries, {})
        self.assertIn("DOCTYPE", problems[0])

    def test_sitemap_only_clean(self):
        routes = {EN: page([], EN), DE: page([], DE, "de"), "https://ex.com/sitemap.xml": SITEMAP}
        r = hc.run(EN, sitemap="https://ex.com/sitemap.xml", fetcher=FakeFetcher(routes), delay=0)
        hi = [i for i in r["issues"] if i["severity"] in ("critical", "high", "medium")]
        self.assertEqual(hi, [])
        self.assertEqual(r["clusters"][0]["methods"], ["sitemap"])

    def test_mixed_methods_low_and_disagree_medium(self):
        same = [("en", EN), ("de", DE)]
        routes = {EN: page(same, EN), DE: page(same + [("fr", FR)], DE, "de"), FR: page(same + [("fr", FR)], FR, "fr"),
                  "https://ex.com/sitemap.xml": SITEMAP}
        r = hc.run(EN, sitemap="https://ex.com/sitemap.xml", fetcher=FakeFetcher(routes), delay=0)
        sev = {i["code"]: i["severity"] for i in r["issues"]}
        self.assertEqual(sev.get("hreflang_mixed_methods"), "low")
        self.assertEqual(sev.get("hreflang_methods_disagree"), "medium")

    def test_markdown_renders(self):
        r = hc.run(EN, fetcher=FakeFetcher({EN: page([("en", EN), ("jp", DE)], EN), DE: page([], DE)}), delay=0)
        md = hc.to_markdown(r)
        self.assertIn("# hreflang check", md)
        self.assertIn("hreflang_invalid_code", md)


if __name__ == "__main__":
    unittest.main()
