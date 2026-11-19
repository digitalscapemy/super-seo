"""Offline tests for extensions/lighthouse/lighthouse.py (no Node, no Chrome, no network)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions" / "lighthouse"))
import lighthouse as lh  # noqa: E402

FIXTURE = {
    "lighthouseVersion": "13.6.0",
    "requestedUrl": "http://localhost:3000/",
    "finalDisplayedUrl": "http://localhost:3000/pricing",
    "fetchTime": "2026-11-19T05:00:00.000Z",
    "configSettings": {"formFactor": "mobile"},
    "runWarnings": [],
    "categories": {
        "performance": {"id": "performance", "score": 0.42, "auditRefs": [
            {"id": "largest-contentful-paint", "weight": 25}, {"id": "cumulative-layout-shift", "weight": 25},
            {"id": "total-blocking-time", "weight": 30}, {"id": "lcp-discovery-insight", "weight": 0},
            {"id": "render-blocking-insight", "weight": 0}]},
        "accessibility": {"id": "accessibility", "score": 0.95, "auditRefs": [{"id": "image-alt", "weight": 10}]},
        "best-practices": {"id": "best-practices", "score": 1, "auditRefs": []},
        "seo": {"id": "seo", "score": 0.83, "auditRefs": [{"id": "meta-description", "weight": 1},
                                                          {"id": "is-crawlable", "weight": 4}]},
        "agentic-browsing": {"id": "agentic-browsing", "score": 0.5, "auditRefs": [
            {"id": "agent-accessibility-tree", "weight": 1}, {"id": "llms-txt", "weight": 0},
            {"id": "webmcp-registered-tools", "weight": 0}, {"id": "cumulative-layout-shift", "weight": 1}]},
    },
    "audits": {
        "largest-contentful-paint": {"id": "largest-contentful-paint", "title": "Largest Contentful Paint", "score": 0.1,
                                     "scoreDisplayMode": "numeric", "numericValue": 4820.4, "displayValue": "4.8 s"},
        "cumulative-layout-shift": {"id": "cumulative-layout-shift", "title": "Cumulative Layout Shift", "score": 0.4,
                                    "scoreDisplayMode": "numeric", "numericValue": 0.18234},
        "total-blocking-time": {"id": "total-blocking-time", "title": "Total Blocking Time", "score": 0.9,
                                "scoreDisplayMode": "numeric", "numericValue": 150},
        "first-contentful-paint": {"id": "first-contentful-paint", "score": 0.7, "scoreDisplayMode": "numeric", "numericValue": 1900},
        "lcp-discovery-insight": {"id": "lcp-discovery-insight", "title": "LCP request discovery", "score": 0,
                                  "scoreDisplayMode": "metricSavings", "metricSavings": {"LCP": 900}},
        "render-blocking-insight": {"id": "render-blocking-insight", "title": "Render-blocking requests", "score": 1,
                                    "scoreDisplayMode": "metricSavings"},
        "image-alt": {"id": "image-alt", "title": "Image elements have [alt] attributes", "score": 0, "scoreDisplayMode": "binary"},
        "meta-description": {"id": "meta-description", "title": "Document does not have a meta description", "score": 0,
                             "scoreDisplayMode": "binary"},
        "is-crawlable": {"id": "is-crawlable", "title": "Page is not blocked from indexing", "score": 1, "scoreDisplayMode": "binary"},
        "agent-accessibility-tree": {"id": "agent-accessibility-tree", "title": "Accessibility tree is well-formed", "score": 1,
                                     "scoreDisplayMode": "binary"},
        "llms-txt": {"id": "llms-txt", "title": "llms.txt follows recommendations", "score": None, "scoreDisplayMode": "notApplicable"},
        "webmcp-registered-tools": {"id": "webmcp-registered-tools", "title": "WebMCP tools registered", "score": None,
                                    "scoreDisplayMode": "notApplicable"},
        "screenshot-thumbnails": {"id": "screenshot-thumbnails", "score": None, "scoreDisplayMode": "informative"},
    },
}


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.r = lh.parse_lhr(json.loads(json.dumps(FIXTURE)))

    def test_scores_exclude_agentic(self):
        self.assertEqual(self.r["scores"], {"performance": 42, "accessibility": 95, "best-practices": 100, "seo": 83})

    def test_metrics_and_ratings(self):
        m = self.r["metrics"]
        self.assertEqual(m["LCP"], {"value": 4820, "rating": "poor"})
        self.assertEqual(m["CLS"]["rating"], "needs-improvement")
        self.assertEqual(m["TBT"]["rating"], "good")
        self.assertNotIn("INP", m)  # not present in navigation reports

    def test_failed_audits_include_insight(self):
        ids = {f["id"]: f for f in self.r["failed_audits"]}
        self.assertIn("lcp-discovery-insight", ids)
        self.assertTrue(ids["lcp-discovery-insight"]["insight"])
        self.assertEqual(ids["lcp-discovery-insight"]["category"], "performance")
        self.assertNotIn("render-blocking-insight", ids)  # passed
        self.assertIn("meta-description", ids)
        self.assertNotIn("largest-contentful-paint", ids)  # metrics are reported separately
        self.assertNotIn("screenshot-thumbnails", ids)

    def test_agentic_is_fraction_not_percent(self):
        a = self.r["agentic"]
        self.assertEqual((a["passed"], a["total"]), (1, 2))  # n/a checks excluded; CLS failed (0.4)
        self.assertEqual(a["label"], "1/2 passed")
        md = lh.single_markdown(self.r)
        self.assertIn("agentic-browsing | 1/2 passed", md)
        self.assertNotRegex(md, r"agentic-browsing \| \d+%")

    def test_findings(self):
        codes = {f["code"]: f["severity"] for f in self.r["findings"]}
        self.assertEqual(codes["lab_lcp_poor"], "high")
        self.assertEqual(codes["lab_cls_needs_improvement"], "medium")
        self.assertEqual(codes["lighthouse_seo_low"], "medium")
        self.assertIn("lh_meta_description", codes)
        self.assertIn("agentic_browsing_checks_failed", codes)

    def test_wrapper_and_runtime_error(self):
        self.assertEqual(lh.parse_lhr({"lhr": FIXTURE})["scores"]["seo"], 83)
        with self.assertRaises(lh.ToolError):
            lh.parse_lhr({"runtimeError": {"code": "NO_FCP", "message": "no paint"}, "audits": {}})

    def test_from_json_cli(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps(FIXTURE), encoding="utf-8")
            with mock.patch("sys.stdout") as out:
                rc = lh.main(["from-json", str(p), "--json", "--seo-dir", td])
            self.assertEqual(rc, 0)
            printed = "".join(c.args[0] for c in out.write.call_args_list)
            self.assertEqual(json.loads(printed)["scores"]["seo"], 83)
            self.assertTrue(list((Path(td) / "audits").glob("lighthouse-localhost-*.md")))


class RunTests(unittest.TestCase):
    def test_run_builds_argv_without_shell(self):
        def fake_run(argv, **kw):
            self.assertFalse(kw.get("shell"))
            self.assertEqual(kw.get("timeout"), 300)
            self.assertIn("lighthouse", argv)
            self.assertIn("--chrome-flags=--headless=new", argv)
            self.assertIn("--only-categories=performance,accessibility,best-practices,seo,agentic-browsing", argv)
            out = next(a.split("=", 1)[1] for a in argv if a.startswith("--output-path="))
            Path(out).write_text(json.dumps(FIXTURE), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.object(lh, "npx_argv", return_value=["node", "npx-cli.js"]), \
                mock.patch.object(lh.subprocess, "run", side_effect=fake_run):
            r = lh.run_lighthouse("https://example.com/?a=1&b=2", agentic=True)
        self.assertEqual(r["agentic"]["label"], "1/2 passed")

    def test_chrome_missing_is_clean_error(self):
        cp = subprocess.CompletedProcess([], 1, "", "Runtime error encountered: No Chrome installations found.")
        with mock.patch.object(lh, "npx_argv", return_value=["npx"]), mock.patch.object(lh.subprocess, "run", return_value=cp):
            with self.assertRaises(lh.ToolError) as cm:
                lh.run_lighthouse("http://localhost:3000")
        self.assertIn("Chrome/Chromium not found", str(cm.exception))

    def test_npx_missing(self):
        with mock.patch.object(lh.shutil, "which", return_value=None):
            with self.assertRaises(lh.ToolError):
                lh.npx_argv()

    def test_rejects_non_http(self):
        for bad in ("file:///etc/passwd", "--help", "javascript:alert(1)"):
            with self.assertRaises(lh.ToolError):
                lh.check_url(bad)

    def test_timeout(self):
        with mock.patch.object(lh.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 300)):
            with self.assertRaises(lh.ToolError):
                lh.run_proc(["x"])


class SiteTests(unittest.TestCase):
    ULH = [{"path": "/", "url": "https://ex.com/", "score": 0.9,
            "categories": {"performance": {"score": 0.95}, "seo": {"score": 1}}},
           {"path": "/blog", "url": "https://ex.com/blog", "score": 0.5,
            "categories": {"performance": {"score": 0.31}, "seo": {"score": 0.45}}}]

    def test_parse_unlighthouse_and_worst(self):
        pages = lh.parse_unlighthouse(self.ULH)
        worst = lh.worst_pages(pages)
        self.assertEqual(worst["performance"][0], {"path": "/blog", "score": 31})
        self.assertEqual(worst["seo"][0]["score"], 45)

    def test_site_fallback_to_sitemap(self):
        with mock.patch.object(lh, "run_unlighthouse", side_effect=lh.ToolError("boom")), \
                mock.patch.object(lh, "sitemap_urls", return_value=["https://ex.com/", "https://ex.com/a"]), \
                mock.patch.object(lh, "run_lighthouse", return_value=lh.parse_lhr(FIXTURE)) as rl:
            res = lh.cmd_site("https://ex.com", 2, False, False)
        self.assertEqual(res["mode"], "lighthouse-fallback")
        self.assertEqual(rl.call_count, 2)
        self.assertTrue(any("Unlighthouse failed" in n for n in res["notes"]))
        self.assertIn("performance", res["worst"])
        self.assertIn("lab_lcp_poor", {f["code"] for f in res["findings"]})

    def test_site_unlighthouse_ok(self):
        with mock.patch.object(lh, "run_unlighthouse", return_value=lh.parse_unlighthouse(self.ULH)):
            res = lh.cmd_site("https://ex.com", 5, False, False)
        self.assertEqual(res["mode"], "unlighthouse")
        self.assertIn("site_seo_low", {f["code"] for f in res["findings"]})


if __name__ == "__main__":
    unittest.main()
