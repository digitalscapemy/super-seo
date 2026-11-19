"""Offline tests for scripts/llms_gen.py: filtering, grouping, titles, rendering, llms-full extraction, validation."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llms_gen as lg  # noqa: E402

O = "https://acme.test"


def pg(path, title, desc="", status=200, noindex=False, canonical=None, **kw):
    url = O + path
    return {"url": url, "final_url": url, "status": status, "title": title, "description": desc, "noindex": noindex,
            "canonical": canonical or url, "h1": [], **kw}


PAGES = [
    pg("/", "Acme | Invoicing for freelancers", "Acme sends invoices and chases late payments for freelancers."),
    pg("/pricing", "Pricing | Acme", "Plans from $0 to $29 per month."),
    pg("/features/reminders", "Payment reminders | Acme", "x " * 200),
    pg("/integrations/stripe", "Stripe integration | Acme"),
    pg("/docs/getting-started", "Getting started - Docs | Acme", "Set up Acme in five minutes."),
    pg("/api/reference", "API reference | Acme", "REST API endpoints."),
    pg("/blog/late-payments", "How to handle late payments | Acme", "A guide."),
    pg("/blog/page/2", "Blog page 2 | Acme"),
    pg("/blog/tag/tax", "Tax posts | Acme"),
    pg("/about", "About us | Acme", "Who we are."),
    pg("/changelog", "Changelog | Acme"),
    pg("/customers", "Customers | Acme", "Case studies."),
    pg("/login", "Log in | Acme"),
    pg("/app/home", "Dashboard | Acme"),
    pg("/old", "Gone | Acme", status=404),
    pg("/secret", "Secret | Acme", noindex=True),
    pg("/pricing-copy", "Pricing | Acme", canonical=O + "/pricing"),
    pg("/search?q=x", "Search | Acme"),
]


class HelperTests(unittest.TestCase):
    def test_sections(self):
        self.assertEqual(lg.section_for("/"), "Product")
        self.assertEqual(lg.section_for("/features/a/b"), "Product")
        self.assertEqual(lg.section_for("/pricing/"), "Product")
        self.assertEqual(lg.section_for("/docs"), "Docs")
        self.assertEqual(lg.section_for("/api/reference"), "Docs")
        self.assertEqual(lg.section_for("/guides/x"), "Guides and Blog")
        self.assertEqual(lg.section_for("/security"), "Company")
        self.assertEqual(lg.section_for("/customers"), "Optional")

    def test_brand_and_titles(self):
        self.assertEqual(lg.detect_brand(PAGES), "Acme")
        self.assertEqual(lg.clean_title("Pricing | Acme", "Acme"), "Pricing")
        self.assertEqual(lg.clean_title("Acme | Invoicing for freelancers", "Acme"), "Invoicing for freelancers")
        self.assertEqual(lg.clean_title("Getting started - Docs | Acme", "Acme"), "Getting started - Docs")
        self.assertEqual(lg.clean_title("", "Acme", "Fallback"), "Fallback")

    def test_truncate(self):
        t = lg.truncate("word " * 100)
        self.assertLessEqual(len(t), 160)
        self.assertTrue(t.endswith("..."))
        self.assertEqual(lg.truncate("short  text"), "short text")

    def test_page_record(self):
        html = ('<html><head><title>Pricing | Acme</title><meta name="description" content="Plans.">'
                '<meta property="og:site_name" content="Acme Inc"><meta name="robots" content="noindex">'
                '<link rel="canonical" href="/pricing"></head><body><h1>Pricing</h1></body></html>')
        rec = lg.page_record({"url": O + "/pricing", "final_url": O + "/pricing", "status": 200, "chain": [],
                              "headers": {"content-type": "text/html"}, "body": html})
        self.assertEqual(rec["site_name"], "Acme Inc")
        self.assertTrue(rec["noindex"])
        self.assertEqual(rec["canonical"], O + "/pricing")
        self.assertEqual(rec["h1"], ["Pricing"])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.res = lg.run(PAGES, O, None, None, 60, fetch_missing_home=False)
        self.text = self.res["text"]

    def test_structure(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "# Acme")
        self.assertEqual(lines[2], "> Acme sends invoices and chases late payments for freelancers.")
        order = [l for l in lines if l.startswith("## ")]
        self.assertEqual(order, ["## Product", "## Docs", "## Guides and Blog", "## Company", "## Optional"])
        self.assertIn("- [Pricing](https://acme.test/pricing): Plans from $0 to $29 per month.", lines)
        self.assertIn("- [Stripe integration](https://acme.test/integrations/stripe)", lines)
        self.assertIn("- [Customers](https://acme.test/customers): Case studies.", lines)

    def test_skips(self):
        for bad in ("/login", "/app/home", "/old", "/secret", "/pricing-copy", "/blog/page/2", "/blog/tag/tax", "search?q"):
            self.assertNotIn(bad + ")", self.text.replace("?q=x", "search?q"))
        sk = self.res["skipped"]
        for k in ("app_path", "non_200", "noindex", "canonicalized", "pagination", "tag_page", "parameter_url"):
            self.assertIn(k, sk)

    def test_validates_clean(self):
        findings = []
        lg.validate_text(self.text, findings)
        self.assertFalse([f for f in findings if f["severity"] in ("critical", "high", "medium", "low")], findings)

    def test_max_entries_priority(self):
        res = lg.run(PAGES, O, "Acme", "Summary.", 3, fetch_missing_home=False)
        self.assertEqual(res["entries"], 3)
        self.assertNotIn("## Optional", res["text"])
        self.assertIn("## Product", res["text"])
        self.assertEqual(res["skipped"]["over_max_entries"], 7)

    def test_name_and_summary_flags_and_no_invention(self):
        pages = [pg("/", "Home"), pg("/pricing", "Pricing")]
        res = lg.run(pages, O, "Acme Cloud", None, 60, fetch_missing_home=False)
        self.assertTrue(res["text"].startswith("# Acme Cloud\n"))
        self.assertNotIn(">", res["text"])  # no description anywhere -> no made-up summary
        self.assertIn("no_summary", {f["code"] for f in res["findings"]})
        self.assertNotRegex(res["text"], r"@|\+\d|tel:|mailto:")

    def test_generic_description_dropped(self):
        same = "The official home of Acme."
        pages = [pg("/", "Acme", same), pg("/a", "A | Acme", same), pg("/b", "B | Acme", same), pg("/c", "C | Acme", "Own text.")]
        text = lg.run(pages, O, "Acme", None, 60, fetch_missing_home=False)["text"]
        self.assertIn("- [A](https://acme.test/a)" + chr(10), text)
        self.assertIn("- [C](https://acme.test/c): Own text.", text)
        self.assertIn(f"(https://acme.test/): {same}", text)

    def test_site_name_from_fetched_home(self):
        def fake(url, ua, **kw):
            body = ('<html><head><title>Welcome</title><meta property="og:site_name" content="Acme HQ">'
                    '<meta name="description" content="From the homepage."></head><body></body></html>')
            return {"url": url, "final_url": url, "status": 200, "error": None, "chain": [],
                    "headers": {"content-type": "text/html"}, "body": body}
        with mock.patch.object(lg.ca, "fetch", fake):
            res = lg.run([pg("/pricing", "Pricing")], O, None, None, 60)
        self.assertTrue(res["text"].startswith("# Acme HQ\n\n> From the homepage.\n"))


class FullTextTests(unittest.TestCase):
    HTML = ("<html><head><title>T</title><style>.x{}</style></head><body><header>Site header</header>"
            "<nav><a href='/'>Home</a></nav><main><h1>Getting started</h1><p>Install the <b>CLI</b> first.</p>"
            "<ul><li>Step one</li><li>Step two</li></ul><pre>npm i acme\nacme login</pre>"
            "<script>var secret=1</script></main><footer>Copyright</footer></body></html>")

    def test_extract_main(self):
        t = lg.extract_main_text(self.HTML)
        self.assertIn("### Getting started", t)
        self.assertIn("Install the CLI first.", t)
        self.assertIn("- Step one", t)
        self.assertIn("```\nnpm i acme\nacme login\n```", t)
        for junk in ("Site header", "Home", "Copyright", "secret", ".x{}"):
            self.assertNotIn(junk, t)

    def test_no_main_falls_back_to_body(self):
        t = lg.extract_main_text("<body><nav>Menu</nav><h2>Title</h2><p>Body text.</p><footer>F</footer></body>")
        self.assertIn("#### Title", t)
        self.assertIn("Body text.", t)
        self.assertNotIn("Menu", t)

    def test_render_full_cap(self):
        chosen = {"Product": [{"title": "A", "url": O + "/a", "description": ""}, {"title": "B", "url": O + "/b", "description": ""}]}
        bodies = {O + "/a": "<main><p>" + "a" * 500 + "</p></main>", O + "/b": "<main><p>" + "b" * 500 + "</p></main>"}
        full, n, truncated = lg.render_full("Acme", "S", chosen, bodies, 700)
        self.assertEqual(n, 1)
        self.assertTrue(truncated)
        self.assertIn("Source: https://acme.test/a", full)
        self.assertLessEqual(len(full.encode()), 700)


class ValidateTests(unittest.TestCase):
    def codes(self, text, src=""):
        f = []
        lg.validate_text(text, f, src)
        return {x["code"] for x in f}

    def test_bad_files(self):
        self.assertIn("llms_no_h1_first", self.codes("Acme\n## Docs\n- [A](https://a.test/)\n"))
        self.assertIn("llms_relative_links", self.codes("# Acme\n## Docs\n- [A](/docs)\n"))
        self.assertIn("llms_is_html", self.codes("<!doctype html><html><head></head></html>"))
        self.assertIn("llms_placeholder", self.codes("# Acme\n> [REPLACE]\n## D\n- [A](https://a.test/)\n"))

    def test_url_served_as_html_and_spa_fallback(self):
        def fake(url, ua, **kw):
            return {"url": url, "final_url": url, "status": 200, "error": None, "chain": [],
                    "headers": {"content-type": "text/html; charset=utf-8"}, "body": "<!doctype html><html><head></head></html>"}
        with mock.patch.object(lg.ca, "fetch", fake), mock.patch.object(lg.ca, "set_network_mode"):
            res = lg.validate("https://acme.test/llms.txt")
        codes = {f["code"] for f in res["findings"]}
        self.assertIn("llms_served_as_html", codes)
        self.assertIn("llms_is_html", codes)
        self.assertIn("unknown_urls_return_200", codes)

    def test_cli_from_crawl_offline_writes(self):
        with tempfile.TemporaryDirectory() as d:
            cj = Path(d) / "crawl.json"
            cj.write_text(json.dumps({"origin": O, "pages": PAGES}), encoding="utf-8")
            out = Path(d) / "public"
            buf, err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                code = lg.main(["--from-crawl", str(cj), "--offline", "--out", str(out)])
            self.assertEqual(code, 0)
            text = (out / "llms.txt").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Acme\n"))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(lg.main(["--validate", str(out / "llms.txt")]), 0)


if __name__ == "__main__":
    unittest.main()
