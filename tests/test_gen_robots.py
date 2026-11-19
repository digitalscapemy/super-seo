"""Offline tests for scripts/robots_gen.py: policy rendering, RFC 9309 self-verification, Content-Signal, --check."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import robots_gen as rg  # noqa: E402
from robots_rfc import Robots  # noqa: E402

U = "https://example.com"


def run_cli(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = rg.main(args)
    return code, out.getvalue(), err.getvalue()


class ContentSignalTests(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(rg.parse_content_signal("ai-train=NO,search=yes , ai-input=yes"), "search=yes, ai-input=yes, ai-train=no")

    def test_invalid(self):
        for bad in ("search=maybe", "foo=yes", "search", "search=yes, search=no", " , "):
            with self.assertRaises(rg.PolicyError, msg=bad):
                rg.parse_content_signal(bad)

    def test_cli_rejects_bad_signal(self):
        code, _, err = run_cli(["--domain", "example.com", "--content-signal", "train=no"])
        self.assertEqual(code, 2)
        self.assertIn("unknown", err)


class RenderTests(unittest.TestCase):
    def test_every_named_group_repeats_private_paths(self):
        pol = rg.build_policy("example.com", training="allow", content_signal="search=yes, ai-input=yes, ai-train=no")
        txt = rg.render_txt(pol)
        groups = rg.raw_groups(txt)
        self.assertEqual(len(groups), 3)
        for g in groups:
            disallows = [v for k, v in g["lines"] if k == "disallow"]
            for p in rg.DEFAULT_PRIVATE:
                self.assertIn(p, disallows, g["agents"])
            self.assertIn(("content-signal", "search=yes, ai-input=yes, ai-train=no"), g["lines"])
        r = Robots(txt)
        for bot in ["GPTBot", "OAI-SearchBot", "Googlebot", "ClaudeBot"]:
            self.assertTrue(r.can_fetch(bot, U + "/pricing"))
            self.assertFalse(r.can_fetch(bot, U + "/app/x"))
        self.assertIn("Sitemap: https://example.com/sitemap.xml", txt)

    def test_training_block(self):
        pol = rg.build_policy("https://example.com/", training="block")
        r = Robots(rg.render_txt(pol))
        for bot in rg.AI_TRAINING:
            self.assertFalse(r.can_fetch(bot, U + "/"), bot)
        for bot in rg.AI_RETRIEVAL + rg.SEARCH_ENGINES:
            self.assertTrue(r.can_fetch(bot, U + "/"), bot)
            self.assertFalse(r.can_fetch(bot, U + "/api/v1"), bot)
        self.assertTrue(rg.verify(rg.render_txt(pol), pol)["ok"])

    def test_staging(self):
        pol = rg.build_policy("staging.example.com", staging=True)
        txt = rg.render_txt(pol)
        r = Robots(txt)
        self.assertFalse(r.can_fetch("Googlebot", U + "/"))
        self.assertNotIn("Sitemap:", txt)
        self.assertTrue(rg.verify(txt, pol)["ok"])

    def test_block_search_explicit(self):
        pol = rg.build_policy("example.com", block_search=True)
        r = Robots(rg.render_txt(pol))
        self.assertFalse(r.can_fetch("Googlebot", U + "/"))
        self.assertFalse(r.can_fetch("OAI-SearchBot", U + "/"))
        self.assertIn("search_blocked", {w["code"] for w in rg.policy_warnings(pol)})

    def test_custom_private_and_slash_rejected(self):
        pol = rg.build_policy("example.com", private=["api/", "/admin/", "/admin/"])
        self.assertEqual(pol["private"], ["/api/", "/admin/"])
        with self.assertRaises(rg.PolicyError):
            rg.build_policy("example.com", private=["/"])

    def test_nextjs(self):
        pol = rg.build_policy("example.com", training="block", content_signal="search=yes, ai-input=yes, ai-train=no")
        ts = rg.render_nextjs(pol)
        self.assertIn('import type { MetadataRoute } from "next";', ts)
        self.assertIn("MetadataRoute.Robots", ts)
        self.assertIn('{ userAgent: AI_TRAINING, disallow: "/" }', ts)
        self.assertIn('{ userAgent: AI_RETRIEVAL, allow: "/", disallow: PRIVATE }', ts)
        self.assertIn("Content-Signal", ts)  # documented limitation comment
        self.assertIn('sitemap: "https://example.com/sitemap.xml"', ts)

    def test_signal_conflict_warning(self):
        pol = rg.build_policy("example.com", training="block", content_signal="ai-train=yes")
        self.assertIn("signal_conflict", {w["code"] for w in rg.policy_warnings(pol)})


class VerifyTests(unittest.TestCase):
    def test_detects_named_group_without_private_paths(self):
        # Classic mistake: GPTBot group only says Allow: /, so it ignores '*' and may crawl /app/.
        txt = "User-agent: *\nAllow: /\nDisallow: /app/\n\nUser-agent: GPTBot\nAllow: /\n\nSitemap: https://example.com/sitemap.xml\n"
        pol = rg.build_policy("example.com", private=["/app/"])
        res = rg.verify(txt, pol)
        self.assertFalse(res["ok"])
        msgs = " ".join(v["message"] for v in res["violations"])
        self.assertIn("GPTBot", msgs)
        self.assertIn("/app/", msgs)

    def test_detects_blocked_search_bot(self):
        txt = "User-agent: *\nAllow: /\nDisallow: /app/\nUser-agent: OAI-SearchBot\nDisallow: /\n"
        res = rg.verify(txt, rg.build_policy("example.com", private=["/app/"], sitemap=None))
        crit = [v for v in res["violations"] if v["severity"] == "critical"]
        self.assertTrue(any("OAI-SearchBot" in v["message"] for v in crit))

    def test_content_signal_missing_in_group(self):
        pol = rg.build_policy("example.com", private=["/app/"], content_signal="search=yes, ai-train=no")
        txt = rg.render_txt(pol).replace("Content-Signal: search=yes, ai-train=no\n", "", 1)
        codes = [v["code"] for v in rg.verify(txt, pol)["violations"]]
        self.assertEqual(codes.count("content_signal_missing"), 1)

    def test_cli_generate_and_table(self):
        code, out, err = run_cli(["--domain", "example.com", "--training", "block",
                                  "--content-signal", "search=yes, ai-input=yes, ai-train=no"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# robots.txt for example.com"))
        self.assertIn("all checks pass", err)
        self.assertIn("| GPTBot | training | `gptbot` | disallow | disallow | - | yes |", err)
        self.assertIn("| OAI-SearchBot | ai-retrieval | `oai-searchbot` | allow | allow | 9/9 | yes |", err)

    def test_cli_json_and_out(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "public" / "robots.txt"
            code, out, _ = run_cli(["--domain", "example.com", "--out", str(target), "--json"])
            self.assertEqual(code, 0)
            res = json.loads(out)
            self.assertTrue(res["verified"])
            self.assertEqual(target.read_text(encoding="utf-8"), res["output"])

    def test_cli_check_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "robots.txt"
            p.write_text("User-agent: *\nDisallow: /api/\nUser-agent: GPTBot\nAllow: /\n", encoding="utf-8")
            code, out, _ = run_cli(["--domain", "example.com", "--private", "/api/", "--training", "block", "--check", str(p)])
            self.assertEqual(code, 1)
            self.assertIn("GPTBot (training) can fetch /", out)
            self.assertIn("sitemap_not_declared", out)
            good = Path(d) / "good.txt"
            good.write_text(rg.render_txt(rg.build_policy("example.com", private=["/api/"], training="block")), encoding="utf-8")
            code, out, _ = run_cli(["--domain", "example.com", "--private", "/api/", "--training", "block", "--check", str(good)])
            self.assertEqual(code, 0, out)

    def test_cli_check_url_html_fallback(self):
        import crawl_audit as ca

        def fake(url, ua, **kw):
            return {"url": url, "final_url": url, "status": 200, "error": None, "chain": [],
                    "headers": {"content-type": "text/html"}, "body": "<html>app shell</html>", "bytes": 20}
        with mock.patch.object(ca, "fetch", fake), mock.patch.object(ca, "set_network_mode"):
            code, out, _ = run_cli(["--domain", "example.com", "--check", "https://example.com/robots.txt", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("robots_served_as_html", {f["code"] for f in json.loads(out)["findings"]})


if __name__ == "__main__":
    unittest.main()
