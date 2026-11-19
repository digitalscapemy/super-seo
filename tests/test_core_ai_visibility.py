"""Offline tests for scripts/ai_visibility.py: answer parsing, scoring and trend on fixture runs."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_visibility as av  # noqa: E402

BASE = Path(__file__).resolve().parent / "fixtures" / "core" / "ai-visibility"
TRACKER = json.loads((BASE / "tracker.json").read_text(encoding="utf-8"))
RUN = BASE / "runs" / "2026-11-01"


class ParseAnswersTests(unittest.TestCase):
    def setUp(self):
        self.obs = av.parse_answers(RUN / "answers.md")

    def test_blocks_and_status(self):
        self.assertEqual(len(self.obs), 7)
        st = [(o["engine"], o["status"]) for o in self.obs]
        self.assertIn(("chatgpt", "no_answer"), st)
        self.assertIn(("gemini", "failed"), st)
        self.assertEqual(sum(o["status"] == "answered" for o in self.obs), 5)

    def test_simulated_flag(self):
        sim = [o for o in self.obs if o["simulated"]]
        self.assertEqual([o["engine"] for o in sim], ["claude-sim"])
        self.assertEqual(sim[0]["prompt"], "best invoicing app for freelancers")

    def test_urls_and_prose(self):
        first = self.obs[0]
        self.assertIn("https://invoicely.my/pricing", first["urls"])
        self.assertIn("https://www.reddit.com/r/freelance/comments/abc", first["urls"])  # trailing dot stripped
        self.assertNotIn("https://", first["prose"])
        self.assertIn("(pricing)", first["prose"])  # markdown link text kept, URL removed
        px = self.obs[1]
        self.assertFalse(any("google.com/url" in u for u in px["urls"]))
        copilot = [o for o in self.obs if o["engine"] == "copilot"][0]
        self.assertNotIn("invoicely", copilot["prose"].lower())  # email + URL blanked


class ScoreRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = av.score_run(RUN, TRACKER)

    def test_observed_counts_and_branded_exclusion(self):
        o = self.res["observed"]
        self.assertEqual(o["answered"], 3)
        self.assertEqual(o["no_answer"], 1)
        self.assertEqual(o["failed"], 1)
        self.assertEqual(o["branded_excluded"], 1)

    def test_own_mention_citation_position(self):
        own = self.res["observed"]["brands"]["__own__"]
        self.assertEqual(own["mentions"], 1)
        self.assertEqual(own["citations"], 2)          # chatgpt link + copilot source link
        self.assertEqual(own["mention_rate"], 33.3)
        self.assertEqual(own["citation_rate"], 66.7)
        self.assertEqual(own["avg_position"], 2.0)     # FreshBooks named first
        self.assertEqual(own["top1_rate"], 0.0)

    def test_competitors(self):
        b = self.res["observed"]["brands"]
        self.assertEqual(b["freshbooks.com"]["mentions"], 2)
        self.assertEqual(b["freshbooks.com"]["avg_position"], 1.5)
        self.assertEqual(b["bukku.my"]["citations"], 1)
        self.assertEqual(b["bukku.my"]["avg_position"], 1.0)

    def test_gaps(self):
        gaps = [g for g in self.res["gaps"] if not g["simulated"]]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["engine"], "perplexity")
        self.assertEqual(sorted(gaps[0]["competitors"]), ["bukku.my", "freshbooks.com"])

    def test_simulated_kept_separate(self):
        s = self.res["simulated"]
        self.assertEqual(s["answered"], 1)
        self.assertEqual(s["brands"]["__own__"]["mentions"], 1)
        self.assertEqual(s["brands"]["__own__"]["avg_position"], 1.0)
        self.assertNotIn("claude-sim", [e for e in self.res["per_engine"] if "(sim)" not in e])
        self.assertIn("claude-sim (sim)", self.res["per_engine"])
        self.assertFalse(any(c.split("|")[1] == "claude-sim" for c in self.res["cells"]))

    def test_sources_and_suggestions(self):
        src = {s["domain"]: s for s in self.res["sources"]}
        self.assertEqual(src["invoicely.my"]["ownership"], "own")
        self.assertEqual(src["invoicely.my"]["answers"], 2)
        self.assertEqual(src["bukku.my"]["ownership"], "competitor")
        self.assertEqual(src["reddit.com"]["type"], "forum")
        self.assertEqual(src["g2.com"]["type"], "review")
        self.assertEqual(self.res["suggested_competitors"], ["zoho.com"])

    def test_prompt_rows_and_render(self):
        rows = {p["prompt"]: p for p in self.res["prompts"]}
        self.assertEqual(rows["best invoicing app for freelancers"]["own_rate"], 50.0)
        self.assertEqual(rows["best invoicing app for freelancers"]["topic"], "invoicing app")
        md = av.render(self.res, TRACKER)
        self.assertIn("Small sample", md)
        self.assertIn("Simulated answers", md)

    def test_is_branded(self):
        self.assertTrue(av.is_branded("is Invoicely good", TRACKER["brand"]))
        self.assertTrue(av.is_branded("invoicely.my reviews", TRACKER["brand"]))
        self.assertFalse(av.is_branded("invoice apps", TRACKER["brand"]))

    def test_reg_domain(self):
        self.assertEqual(av.reg_domain("www.shop.example.co.uk"), "example.co.uk")
        self.assertEqual(av.reg_domain("blog.acme.com.my:443"), "acme.com.my")
        self.assertEqual(av.reg_domain("docs.acme.io"), "acme.io")


class TrendTests(unittest.TestCase):
    def test_matched_cells(self):
        out = av.trend(BASE, TRACKER)
        self.assertIn("Status: **comparable**", out)
        self.assertIn("matched cells: 3", out)
        self.assertIn("Mentioned rate on matched cells: 33.3% -> 33.3% (+0.0 pts)", out)
        self.assertIn("- chatgpt: best invoicing app for freelancers", out.split("Lost:")[0].split("Won:")[1])
        self.assertIn("- perplexity: best invoicing app for freelancers", out.split("Lost:")[1])

    def test_scope_changed_and_too_few_runs(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "runs" / "a").mkdir(parents=True)
            (tmp / "runs" / "a" / "answers.md").write_text("### chatgpt | best invoicing app for freelancers\nFreshBooks.\n", encoding="utf-8")
            self.assertIn("at least two runs", av.trend(tmp, TRACKER))
            (tmp / "runs" / "b").mkdir()
            (tmp / "runs" / "b" / "answers.md").write_text("### gemini | best invoicing app for freelancers\nFreshBooks.\n", encoding="utf-8")
            self.assertIn("scope_changed", av.trend(tmp, TRACKER))
        finally:
            shutil.rmtree(tmp)

    def test_checklist(self):
        out = av.checklist(BASE, TRACKER)
        self.assertEqual(out.count("### "), 6)  # 3 prompts x 2 engines
        self.assertIn("### perplexity | free invoice software for small business", out)


if __name__ == "__main__":
    unittest.main()
