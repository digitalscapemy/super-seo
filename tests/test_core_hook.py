"""Offline tests for hooks/validate_jsonld.py (Claude Code PostToolUse hook)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "validate_jsonld.py"
sys.path.insert(0, str(HOOK.parent))
import validate_jsonld as vj  # noqa: E402

ORG_OK = '{"@context": "https://schema.org", "@type": "Organization", "name": "Acme", "url": "https://acme.test"}'


def run_hook(event, env_extra=None):
    env = dict(os.environ)
    env.pop("SUPER_SEO_JSONLD_HOOK", None)
    env.update(env_extra or {})
    data = event if isinstance(event, str) else json.dumps(event)
    p = subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout, p.stderr


class HookContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def event(self, path, tool="Write"):
        return {"session_id": "x", "hook_event_name": "PostToolUse", "tool_name": tool, "cwd": str(self.tmp),
                "tool_input": {"file_path": str(path)}}

    def test_valid_html_is_silent(self):
        p = self.write("index.html", f'<html><head><script type="application/ld+json">{ORG_OK}</script></head></html>')
        self.assertEqual(run_hook(self.event(p)), (0, "", ""))

    def test_invalid_json_blocks_with_stderr(self):
        p = self.write("index.html", '<script type="application/ld+json">\n{"@context": "https://schema.org",\n "@type": "Organization",}\n</script>')
        code, out, err = run_hook(self.event(p, "Edit"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("not valid JSON", err)
        self.assertIn("line 3", err)

    def test_warnings_go_to_additional_context(self):
        p = self.write("page.tsx", 'const jsonLd = { "@context": "http://schema.org", "@type": "HowTo", name: "Steps" };\n')
        code, out, err = run_hook(self.event(p))
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("HowTo", ctx)
        self.assertIn("https://schema.org", ctx)
        self.assertIn("systemMessage", payload)

    def test_relative_path_resolved_against_cwd(self):
        self.write("bad.json", '{"@context": "https://schema.org", "@type": "Organization", "name": "[INSERT name]"}')
        code, _, err = run_hook({"tool_name": "Write", "cwd": str(self.tmp), "tool_input": {"file_path": "bad.json"}})
        self.assertEqual(code, 2)
        self.assertIn("placeholder", err)

    def test_never_crashes(self):
        self.assertEqual(run_hook("not json")[0], 0)
        self.assertEqual(run_hook("")[0], 0)
        self.assertEqual(run_hook("[1, 2]")[0], 0)
        self.assertEqual(run_hook({"tool_input": {"file_path": str(self.tmp / "missing.tsx")}}), (0, "", ""))
        self.assertEqual(run_hook({"tool_input": {}})[0], 0)
        bin_path = self.tmp / "blob.json"
        bin_path.write_bytes(b'\x00\x01{"@type": "x"' + bytes(range(256)) * 10)
        self.assertEqual(run_hook(self.event(bin_path)), (0, "", ""))
        py = self.write("script.py", 'x = {"@type": "HowTo", "name": "[REPLACE]"}')
        self.assertEqual(run_hook(self.event(py)), (0, "", ""))
        d = self.tmp / "dir.tsx"
        d.mkdir()
        self.assertEqual(run_hook(self.event(d))[0], 0)

    def test_env_switch_disables(self):
        p = self.write("bad.html", '<script type="application/ld+json">{oops}</script>')
        self.assertEqual(run_hook(self.event(p), {"SUPER_SEO_JSONLD_HOOK": "off"}), (0, "", ""))

    def test_cli_mode(self):
        p = self.write("bad.html", '<script type="application/ld+json">{"@type": "Organization", "name": "Acme"}</script>')
        r = subprocess.run([sys.executable, str(HOOK), str(p)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no @context", r.stderr)

    def test_one_mb_file_is_fast(self):
        obj = ('const jsonLd%d = { "@context": "https://schema.org", "@type": "Product", name: "Plan %d", '
               'offers: { "@type": "Offer", price: "%d", priceCurrency: "USD" } };\n')
        body = "".join(obj % (i, i, i) for i in range(6000))
        body += "export const text = `" + ("lorem-free filler line with {braces} and 'quotes'\n" * 3000) + "`;\n"
        p = self.write("big.tsx", body)
        self.assertGreater(p.stat().st_size, 1_000_000)
        t0 = time.perf_counter()
        rep = vj.check_file(str(p))
        self.assertLess(time.perf_counter() - t0, 3.0)  # generous: shared CI runners are slow
        self.assertEqual(rep.errors, [])


class CheckTextTests(unittest.TestCase):
    def errs(self, name, text):
        rep = vj.check_text(name, text)
        return rep.errors, rep.warnings

    def test_template_expressions_are_not_parsed(self):
        cases = {
            "page.tsx": '<script type="application/ld+json">{JSON.stringify(jsonLd)}</script>',
            "page.astro": '<script type="application/ld+json" set:html={JSON.stringify(schema)}></script>',
            "Page.svelte": '<script type="application/ld+json">{@html JSON.stringify(ld)}</script>',
            "x.vue": '<script type="application/ld+json">{{ jsonLd }}</script>',
            "product.liquid": '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":{{ product.title | json }}}</script>',
            "page.php": '<script type="application/ld+json"><?php echo json_encode($ld); ?></script>',
            "page.blade.php": '<script type="application/ld+json">@json($schema)</script>',
            "layout.jsx": '<script type="application/ld+json">{schema}</script>',
            "tpl.ts": 'const html = `<script type="application/ld+json">${JSON.stringify(data)}</script>`;',
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.errs(name, text), ([], []))

    def test_self_closing_jsx_script(self):
        text = ('const jsonLd = { "@context": "https://schema.org", "@type": "WebSite", name: "Acme", url: "https://acme.test" };\n'
                '<script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />\n')
        self.assertEqual(self.errs("page.tsx", text), ([], []))

    def test_placeholders_block(self):
        for val in ("[REPLACE]", "[INSERT logo]", "[Your Company]", "XXX", "Lorem ipsum dolor"):
            with self.subTest(val=val):
                e, _ = self.errs("page.tsx", 'const jsonLd = { "@context": "https://schema.org", "@type": "Organization", name: "%s" };' % val)
                self.assertTrue(e and "placeholder" in e[0])
        e, _ = self.errs("page.tsx", 'const jsonLd = { "@context": "https://schema.org", "@type": "Thing", name: "xxx-large" };')
        self.assertEqual(e, [])

    def test_commented_placeholder_ignored(self):
        text = ('const org = {\n  "@type": "Organization",\n  name: SITE.name,\n'
                '  // contactPoint: [{ "@type": "ContactPoint", email: "[REPLACE]" }],\n  /* logo: "[INSERT]" */\n};\n')
        self.assertEqual(self.errs("json-ld.tsx", text)[0], [])

    def test_example_com_in_url_props(self):
        e, _ = self.errs("page.tsx", 'const jsonLd = { "@context": "https://schema.org", "@type": "Organization", '
                                     'sameAs: ["https://x.com/acme", "https://www.example.com/acme"] };')
        self.assertTrue(any("sameAs" in x for x in e))
        e, _ = self.errs("index.html", '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization",'
                                       '"logo":"https://example.com/logo.png"}</script>')
        self.assertTrue(any("logo" in x for x in e))
        e, _ = self.errs("page.tsx", 'const jsonLd = { "@context": "https://schema.org", "@type": "Article", '
                                     'description: "see example.com for details", url: "https://acme.test" };')
        self.assertEqual(e, [])

    def test_missing_context(self):
        e, _ = self.errs("index.html", '<script type="application/ld+json">[{"@type": "Organization", "name": "A"}]</script>')
        self.assertTrue(any("no @context" in x for x in e))
        e, _ = self.errs("index.html", '<script type="application/ld+json">{"@context": "https://schema.org", "@graph": '
                                       '[{"@type": "Organization", "name": "A"}]}</script>')
        self.assertEqual(e, [])
        _, w = self.errs("schema.ts", 'export const jsonLd = { "@type": "Organization", name: "Acme" };')
        self.assertTrue(any("no @context" in x for x in w))
        # a fragment merged into a parent graph later is fine
        self.assertEqual(self.errs("org.ts", 'const org = { "@type": "Organization", name: "Acme" };'), ([], []))

    def test_rating_warning_only_in_components(self):
        text = ('{"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Acme", '
                '"aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.9", "reviewCount": "120"}}')
        _, w = self.errs("page.html", f'<script type="application/ld+json">{text}</script>')
        self.assertTrue(any("aggregateRating" in x for x in w))
        _, w = self.errs("data.json", text)
        self.assertEqual(w, [])
        _, w = self.errs("page.tsx", 'const jsonLd = { "@context": "https://schema.org", "@type": "Product", '
                                     'aggregateRating: { "@type": "AggregateRating", ratingValue: rating.avg, reviewCount: rating.n } };')
        self.assertEqual(w, [])

    def test_nested_deprecated_type(self):
        text = ('const jsonLd = {\n  "@context": "https://schema.org",\n  "@graph": [\n'
                '    { "@type": "Organization", name: "Acme" },\n    { "@type": ["HowTo"], name: "Send" },\n  ],\n};\n')
        e, w = self.errs("page.tsx", text)
        self.assertEqual(e, [])
        self.assertEqual(len([x for x in w if "HowTo" in x]), 1)
        self.assertTrue(w[0].startswith("line 5"))

    def test_markdown_code_fences_ignored(self):
        md = ('# Docs\n\n```html\n<script type="application/ld+json">{"@type": "Organization", "name": "[REPLACE]"}</script>\n```\n\n'
              'Real markup:\n<script type="application/ld+json">' + ORG_OK + '</script>\n')
        self.assertEqual(self.errs("guide.md", md), ([], []))
        self.assertTrue(self.errs("guide.mdx", md.replace(ORG_OK, '{"@type": "Organization"}'))[0])

    def test_non_jsonld_code_is_ignored(self):
        self.assertEqual(self.errs("app.ts", 'const x = { type: "Organization", name: "[REPLACE]" };'), ([], []))
        self.assertEqual(self.errs("package.json", '{"name": "x", "version": "1.0.0"}'), ([], []))
        self.assertEqual(self.errs("broken.json", '{"name": '), ([], []))  # not JSON-LD: not our business

    def test_check_file_skips_unsupported_and_huge(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            big = tmp / "huge.html"
            big.write_bytes(b" " * (vj.MAX_BYTES + 1))
            self.assertIsNone(vj.check_file(str(big)))
            self.assertIsNone(vj.check_file(str(tmp / "a.txt")))
            self.assertIsNone(vj.check_file(""))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
