"""Offline tests for scripts/content_check.py on an MDX fixture and small HTML snippets."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import content_check as cc  # noqa: E402

MDX = Path(__file__).resolve().parent / "fixtures" / "core" / "post.mdx"


class MdxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = MDX.read_text(encoding="utf-8")
        cls.sections = cc.md_to_sections(raw)
        cls.res = cc.analyze(cls.sections, raw)
        cls.rows = {r["heading"]: r for r in cls.res["sections"]}

    def test_frontmatter_imports_and_code_removed(self):
        text = " ".join(" ".join(s["parts"]) for s in self.sections)
        self.assertNotIn("A practical guide", text)
        self.assertNotIn("import Callout", text)
        self.assertNotIn("export const meta", text)
        self.assertNotIn("code block text", text)
        self.assertNotIn("Not a heading", [s["heading"] for s in self.sections])
        self.assertIn("Register on the MyInvois portal early.", text)  # JSX tags stripped, text kept

    def test_headings(self):
        self.assertEqual([s["heading"] for s in self.sections[1:]],
                         ["E-invoicing in Malaysia: a 2026 guide", "What is an e-invoice?", "Who must comply",
                          "Invoicely vs FreshBooks", "Pricing comparison table"])

    def test_answer_first_question_section(self):
        r = self.rows["What is an e-invoice?"]
        self.assertTrue(r["question_heading"])
        self.assertTrue(r["answer_first"])
        self.assertEqual(r["flags"], [])

    def test_pronoun_and_filler(self):
        r = self.rows["Who must comply"]
        self.assertTrue(r["leading_pronoun"])
        self.assertIn("in today's fast-paced", r["filler"])
        self.assertIn("does not open with a direct answer", r["flags"])
        self.assertIn("stock phrasing", r["flags"])
        self.assertEqual(r["stats"], 3)

    def test_comparison_needs_table(self):
        self.assertIn("comparison without a table", self.rows["Invoicely vs FreshBooks"]["flags"])
        self.assertTrue(self.rows["Invoicely vs FreshBooks"]["has_list"])
        r = self.rows["Pricing comparison table"]
        self.assertTrue(r["has_table"])
        self.assertNotIn("comparison without a table", r["flags"])
        self.assertIn("2 numbers without a nearby source", r["flags"])

    def test_page_level(self):
        p = self.res["page"]
        self.assertTrue(p["date_visible"])
        self.assertTrue(p["byline_hint"])
        self.assertTrue(p["early_answer_first"])
        self.assertEqual(p["question_headings"], 1)
        self.assertEqual(p["tables"], 1)
        self.assertEqual(p["filler_hits"], ["in today's fast-paced"])
        self.assertIsNotNone(p["flesch_reading_ease"])

    def test_per_month_is_not_a_source(self):
        # BUG: SOURCE is compiled with re.I, so the 'per [A-Z]' alternative (meant for "per Gartner") also matches
        # "per month" and an unsourced price counts as sourced.
        self.assertEqual(self.rows["Invoicely vs FreshBooks"]["stats_sourced"], 0)

    def test_main_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cc.main([str(MDX), "--json"])
        self.assertEqual(json.loads(buf.getvalue())["page"]["sections"], 5)


class HtmlTests(unittest.TestCase):
    def test_blocks_parser_skips_nav_and_scripts(self):
        html = ("<html><body><nav><a>Home</a> Pricing</nav><h1>Title</h1><p>Intro text here.</p>"
                "<h2>How much does it cost?</h2><p>The plan costs $10 per user.</p><ul><li>One</li></ul>"
                "<script>var x = 'hidden';</script><footer>Copyright</footer></body></html>")
        b = cc.Blocks()
        b.feed(html)
        b.flush()
        heads = [s["heading"] for s in b.sections]
        self.assertEqual(heads, ["(intro)", "Title", "How much does it cost?"])
        all_text = " ".join(" ".join(s["parts"]) for s in b.sections)
        self.assertNotIn("hidden", all_text)
        self.assertNotIn("Copyright", all_text)
        self.assertNotIn("Home", all_text)
        self.assertEqual(b.sections[2]["lists"], 1)

    def test_sentences_keep_abbreviations(self):
        self.assertEqual(cc.sentences("Use e.g. Stripe. Then pay."), ["Use e.g. Stripe.", "Then pay."])


if __name__ == "__main__":
    unittest.main()
