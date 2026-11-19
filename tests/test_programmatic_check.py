"""Offline tests for scripts/programmatic_check.py (synthetic page sets)."""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import programmatic_check as pc  # noqa: E402

NAV = "<nav>" + " ".join(f"<a href='/n{i}'>Menu item {i}</a>" for i in range(40)) + "</nav>"
FOOTER = "<footer>" + "Copyright Acme company all rights reserved legal privacy terms cookies " * 20 + "</footer>"
BOILER = ("Acme connects your tools in minutes with no code required. Sync data both ways, map fields visually, "
          "and monitor every run from one dashboard. Our support team answers within one business day. ") * 6
VOCAB = [f"w{i}" for i in range(5000)]


def unique_text(seed, n):
    rnd = random.Random(seed)
    return " ".join(rnd.choice(VOCAB) for _ in range(n))


def page(name, body, desc=None):
    desc = desc or f"Connect {name} with Acme. Try it free"
    return (f"<html><head><title>Acme + {name} integration | Acme</title><meta name='description' content='{desc}'></head>"
            f"<body><header>Acme header text</header>{NAV}<main><h1>{name}</h1><p>{body}</p></main>{FOOTER}</body></html>")


class Extract(unittest.TestCase):
    def test_main_only_no_chrome(self):
        ex = pc.extract(page("Slack", "Hello slack world"))
        self.assertIn("Hello slack world", ex["text"])
        self.assertNotIn("Menu item", ex["text"])
        self.assertNotIn("Copyright", ex["text"])
        self.assertEqual(ex["h1"], "Slack")
        self.assertEqual(ex["title"], "Acme + Slack integration | Acme")

    def test_no_main_excludes_nav_footer_script(self):
        html = ("<html><body><nav>NAVTEXT</nav><aside>SIDE</aside><script>var x='SCRIPT'</script>"
                "<div>Body words here</div><footer>FOOT</footer></body></html>")
        t = pc.extract(html)["text"]
        self.assertEqual(t, "Body words here")


class AnalyzeSet(unittest.TestCase):
    def test_templated_set_fails(self):
        names = ["Slack", "Notion", "Jira", "Asana", "Trello", "Zoom"]
        pages = [{"url": f"https://acme.test/integrations/{n.lower()}", "html": page(n, BOILER + unique_text(999, 300) + " " + unique_text(i, 20))}
                 for i, n in enumerate(names)]
        r = pc.analyze_set(pages)
        for row in r["pages"]:
            codes = {i["code"] for i in row["issues"]}
            self.assertIn("unique_below_30", codes, row)
            self.assertIn("boilerplate_over_60", codes)
            self.assertLess(row["unique_ratio"], 0.3)
        sc = {x["code"] for x in r["set_findings"]}
        self.assertIn("near_duplicates", sc)
        self.assertIn("shared_stock_cta", sc)
        self.assertIn("templated_titles", sc)
        tt = next(x for x in r["set_findings"] if x["code"] == "templated_titles")["template"]
        self.assertEqual(tt["template"], "acme + {var} integration | acme")
        self.assertEqual(tt["pages"], 6)

    def test_unique_set_passes(self):
        pages = [{"url": f"https://acme.test/i/{i}", "html": page(f"Tool{i}", unique_text(100 + i, 400),
                                                                   desc=f"Different description number {i} about things.")}
                 for i in range(4)]
        r = pc.analyze_set(pages)
        for row in r["pages"]:
            self.assertGreater(row["unique_ratio"], 0.95)
            self.assertEqual([i for i in row["issues"] if i["severity"] in ("high", "medium")], [])
        self.assertFalse(r["near_duplicate_pairs"])
        self.assertNotIn("shared_stock_cta", {x["code"] for x in r["set_findings"]})

    def test_medium_band_and_thin(self):
        # ~35% unique: 65 shared words + 35 unique words
        shared = unique_text(999, 130)
        pages = [{"url": f"https://acme.test/p/{i}", "html": page(f"P{i}", shared + " " + unique_text(i, 70))} for i in range(3)]
        r = pc.analyze_set(pages)
        row = r["pages"][0]
        codes = {i["code"] for i in row["issues"]}
        self.assertIn("unique_below_40", codes, row)
        self.assertIn("thin_page", codes)
        self.assertNotIn("unique_below_30", codes)

    def test_scale_gates_and_sample(self):
        pages = [{"url": f"https://acme.test/x/{i}", "text": unique_text(i, 50), "title": f"T {i}", "description": ""}
                 for i in range(40)]
        r = pc.analyze_set(pages, declared_size=120)
        self.assertEqual({x["code"]: x["severity"] for x in r["set_findings"]}["scale_review"], "info")
        self.assertTrue(2 <= len(r["review_sample"]) <= 4)
        r = pc.analyze_set(pages, declared_size=600)
        self.assertEqual({x["code"]: x["severity"] for x in r["set_findings"]}["scale_justify"], "high")
        self.assertEqual(pc.analyze_set(pages, sample_seed=1)["review_sample"], pc.analyze_set(pages, sample_seed=1)["review_sample"])

    def test_description_repeats_title(self):
        pages = [{"url": f"https://acme.test/d/{i}", "text": unique_text(i, 400), "title": f"Widget number {i} for teams | Acme",
                  "description": f"Widget number {i} for teams helps you ship. Sign up today."} for i in range(4)]
        r = pc.analyze_set(pages)
        self.assertIn("description_repeats_title", {i["code"] for i in r["pages"][0]["issues"]})
        self.assertIn("templated_descriptions", {x["code"] for x in r["set_findings"]})

    def test_markdown(self):
        pages = [{"url": f"https://acme.test/i/{i}", "html": page(f"T{i}", unique_text(i, 50))} for i in range(3)]
        r = pc.analyze_set(pages)
        r.update(label="acme.test", checked_at="now", skipped=[])
        md = pc.to_markdown(r)
        self.assertIn("Human review sample", md)
        self.assertIn("| URL | Words | Unique |", md)


class Inputs(unittest.TestCase):
    def test_from_crawl(self):
        data = {"pages": [{"url": "https://a.test/integrations/x", "final_url": "https://a.test/integrations/x", "status": 200},
                          {"url": "https://a.test/blog/y", "final_url": "https://a.test/blog/y", "status": 200},
                          {"url": "https://a.test/integrations/z", "final_url": "https://a.test/integrations/z", "status": 404}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "crawl.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(pc.urls_from_crawl(str(p), "/integrations/"), ["https://a.test/integrations/x"])


if __name__ == "__main__":
    unittest.main()
