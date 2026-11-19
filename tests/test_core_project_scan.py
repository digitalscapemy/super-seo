"""Offline tests for scripts/project_scan.py on the Next.js App Router fixture in tests/fixtures/next-app."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import project_scan as ps  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "next-app"


def scan(root: Path) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        ps.main([str(root), "--json"])
    return json.loads(buf.getvalue())


class NextAppRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Scan a copy outside tests/: grep() skips any path containing /test/ or /tests/ (see expectedFailure below).
        cls.tmp = Path(tempfile.mkdtemp())
        cls.root = cls.tmp / "next-app"
        shutil.copytree(FIXTURE, cls.root)
        cls.info = scan(cls.root)
        cls.routes = {r["route"]: r for r in cls.info["next"]["routes"]}
        cls.codes = {}
        for f in cls.info["findings"]:
            cls.codes.setdefault(f["code"], []).append(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_detect(self):
        d = self.info["detect"]
        self.assertEqual(d["framework"], "nextjs")
        self.assertEqual(d["render"], "ssr/ssg")
        self.assertTrue(d["has_typescript"])
        self.assertEqual(d["package_name"], "next-fixture")

    def test_routes_and_groups(self):
        self.assertEqual(sorted(self.routes), ["/", "/about", "/blog/[slug]", "/contact", "/dashboard", "/docs",
                                               "/features", "/pricing"])
        self.assertTrue(self.routes["/dashboard"]["app_area"])          # (app) route group
        self.assertFalse(self.routes["/contact"]["app_area"])           # (marketing) group stripped from URL
        self.assertTrue(self.routes["/contact"]["own_metadata"])        # generateMetadata
        self.assertTrue(self.routes["/"]["jsonld"])

    def test_metadata_inheritance(self):
        self.assertFalse(self.routes["/about"]["own_metadata"])
        self.assertEqual(self.routes["/about"]["inherits_from_layout"], "(root)")
        self.assertIn("3 public routes only inherit", self.codes["routes_inherit_only"][0]["message"])
        self.assertNotIn("routes_without_metadata", self.codes)

    def test_dynamic_route(self):
        r = self.routes["/blog/[slug]"]
        self.assertTrue(r["dynamic"])
        self.assertTrue(r["static_params"])
        self.assertIn("/blog/[slug]", self.codes["dynamic_routes_no_generateMetadata"][0]["message"])

    def test_root_layout_and_special_files(self):
        nx = self.info["next"]
        self.assertTrue(nx["root_metadataBase"])
        self.assertTrue(nx["root_title_template"])
        self.assertTrue(nx["root_html_lang"])
        self.assertTrue(nx["special_files"]["sitemap"])
        self.assertTrue(nx["special_files"]["robots"])
        self.assertTrue(nx["llms_txt_route"])
        for code in ("no_metadataBase", "no_sitemap", "no_robots", "no_llms_txt", "no_jsonld"):
            self.assertNotIn(code, self.codes)
        self.assertIn("no_og_image", self.codes)

    def test_preview_url(self):
        hits = self.codes["preview_domain_url"]
        self.assertEqual(hits[0]["file"], "app/docs/page.tsx")
        self.assertIn("vercel.app", hits[0]["match"])

    def test_noindex_in_app_area_is_info(self):
        hits = {h["file"]: h["severity"] for h in self.codes["noindex_in_code"]}
        self.assertEqual(hits["app/(app)/dashboard/page.tsx"], "info")

    def test_noindex_on_public_route_is_high(self):
        # BUG: grep() checks APP_SEGMENTS against the path relative to the project root, so every file under
        # app/ (Next.js App Router) matches the 'app' segment and a noindex on a public page is downgraded to info.
        hits = {h["file"]: h["severity"] for h in self.codes["noindex_in_code"]}
        self.assertEqual(hits["app/pricing/page.tsx"], "high")

    def test_markdown_output(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.main([str(self.root)])
        md = buf.getvalue()
        self.assertIn("Next.js App Router (app)", md)
        self.assertIn("| /dashboard |", md)


class InPlaceScanTests(unittest.TestCase):
    def test_grep_runs_when_project_path_contains_tests_dir(self):
        # BUG: grep() applies its test-file filter regex (/tests?/) to the ABSOLUTE path, so a project located
        # under any folder named test/ or tests/ (like this fixture) gets no grep findings at all.
        info = scan(FIXTURE)
        self.assertIn("preview_domain_url", {f["code"] for f in info["findings"]})


class BareRoutesTests(unittest.TestCase):
    def test_routes_without_metadata_and_missing_plumbing(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "app" / "pricing").mkdir(parents=True)
            (tmp / "package.json").write_text('{"dependencies": {"next": "15.0.0"}}', encoding="utf-8")
            (tmp / "app" / "layout.tsx").write_text("export default function L({children}) { return <html>{children}</html> }", encoding="utf-8")
            (tmp / "app" / "page.tsx").write_text("export default function P() { return <h1>Hi</h1> }", encoding="utf-8")
            (tmp / "app" / "pricing" / "page.tsx").write_text("export default function P() { return <h1>Pricing</h1> }", encoding="utf-8")
            (tmp / "next.config.mjs").write_text("export default { output: 'export' }", encoding="utf-8")
            info = scan(tmp)
            codes = {f["code"]: f for f in info["findings"]}
            self.assertEqual(info["detect"]["render"], "static-export")
            self.assertEqual(codes["routes_without_metadata"]["severity"], "high")
            self.assertIn("/pricing", codes["routes_without_metadata"]["message"])
            for code in ("no_metadataBase", "no_sitemap", "no_robots", "no_html_lang", "no_jsonld", "no_llms_txt"):
                self.assertIn(code, codes)
        finally:
            shutil.rmtree(tmp)

    def test_vite_spa_is_csr(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "package.json").write_text('{"dependencies": {"react": "19", "vite": "6"}}', encoding="utf-8")
            (tmp / "index.html").write_text('<html><head></head><body><div id="root"></div></body></html>', encoding="utf-8")
            codes = {f["code"] for f in scan(tmp)["findings"]}
            self.assertIn("csr_spa", codes)
            self.assertIn("spa_index_meta", codes)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
