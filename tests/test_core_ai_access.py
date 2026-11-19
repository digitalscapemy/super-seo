"""Offline tests for scripts/ai_access.py: MSYS path repair and robots.txt / Content-Signal handling via a mocked fetch."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_access  # noqa: E402

ORIGIN = "http://localhost:3000"
PAGE = ("<html lang='en'><head><title>Acme</title></head><body><main><h1>Acme</h1><p>"
        + "Acme sends invoices and tracks payments for freelancers and small teams. " * 20 + "</p></main></body></html>")


def fake_fetch_factory(robots_body, robots_status=200, robots_ctype="text/plain"):
    calls = []

    def fake(url, ua, timeout=20.0, max_redirects=10, accept=None, max_bytes=0):
        calls.append((url, ua, accept))
        if url == ORIGIN + "/robots.txt":
            return {"status": robots_status, "error": None, "headers": {"content-type": robots_ctype},
                    "body": robots_body, "bytes": len(robots_body)}
        if url.endswith(("/llms.txt", "/llms-full.txt")):
            return {"status": 404, "error": None, "headers": {}, "body": "", "bytes": 0}
        return {"status": 200, "error": None, "headers": {"content-type": "text/html"}, "body": PAGE, "bytes": len(PAGE)}
    return fake, calls


def run_main(robots_body, *extra, **kw):
    fake, calls = fake_fetch_factory(robots_body, **kw)
    buf = io.StringIO()
    with mock.patch.object(ai_access, "fetch", fake), mock.patch.object(ai_access, "set_network_mode", return_value=False), \
            redirect_stdout(buf):
        ai_access.main([ORIGIN, "--no-probe", "--json", "--paths", "/", *extra])
    return json.loads(buf.getvalue()), calls


class FixPathTests(unittest.TestCase):
    def test_msys_conversion_undone(self):
        self.assertEqual(ai_access.fix_path("C:/Program Files/Git/pricing"), "/pricing")
        self.assertEqual(ai_access.fix_path("C:/Program Files/Git/"), "/")
        self.assertEqual(ai_access.fix_path("C:/Program Files/Git"), "/")
        self.assertEqual(ai_access.fix_path("c:\\Program Files\\Git\\docs\\api"), "/docs/api")
        self.assertEqual(ai_access.fix_path("C:/msys64/blog"), "/blog")
        self.assertEqual(ai_access.fix_path("D:/Git/features"), "/features")

    def test_plain_paths(self):
        self.assertEqual(ai_access.fix_path("/docs"), "/docs")
        self.assertEqual(ai_access.fix_path("pricing"), "/pricing")
        self.assertEqual(ai_access.fix_path("\\blog"), "/blog")
        self.assertTrue(ai_access.fix_path("D:/work/site").startswith("/"))


class ContentSignalTests(unittest.TestCase):
    def test_content_signal_parsing(self):
        robots = ("User-agent: *\nContent-Signal: search=yes, ai-input=no, ai-train=maybe, colour=blue\n"
                  "Allow: /\n")
        res, _ = run_main(robots)
        self.assertEqual(res["content_signals"], ["search=yes, ai-input=no, ai-train=maybe, colour=blue"])
        f = {x["code"]: x["severity"] for x in res["findings"]}
        self.assertEqual(f.get("content_signal_ai-input_no"), "medium")
        self.assertEqual(f.get("content_signal_bad_value"), "low")
        self.assertEqual(f.get("content_signal_unknown_key"), "low")
        self.assertNotIn("content_signal_search_no", f)

    def test_content_signal_search_no_and_case(self):
        res, _ = run_main("User-agent: *\ncontent-signal: Search=No\nAllow: /\n")
        self.assertIn("content_signal_search_no", {x["code"] for x in res["findings"]})

    def test_no_content_signal(self):
        res, _ = run_main("User-agent: *\nAllow: /\n")
        self.assertEqual(res["content_signals"], [])
        self.assertFalse(any(x["code"].startswith("content_signal") for x in res["findings"]))

    def test_html_robots_is_ignored(self):
        res, _ = run_main("<html>User-agent: *\nContent-Signal: search=no</html>", robots_ctype="text/html")
        self.assertEqual(res["content_signals"], [])

    def test_robots_policy_and_5xx(self):
        res, _ = run_main("User-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
        f = {x["code"]: x["severity"] for x in res["findings"]}
        self.assertEqual(f["robots_blocks_OAI-SearchBot"], "high")
        self.assertEqual(f["robots_blocks_GPTBot"], "info")
        pol = {p["token"]: p for p in res["policy"]}
        self.assertEqual(pol["OAI-SearchBot"]["robots"], "blocked")
        self.assertTrue(pol["GPTBot"]["explicit_group"])
        self.assertEqual(pol["Googlebot"]["robots"], "allowed")
        res5, _ = run_main("", robots_status=503)
        self.assertIn("robots_5xx", {x["code"] for x in res5["findings"]})

    def test_msys_mangled_paths_are_repaired_before_fetch(self):
        res, calls = run_main("User-agent: *\nAllow: /\n", "C:/Program Files/Git/pricing")
        self.assertEqual(res["paths"], ["/", "/pricing"])
        self.assertIn(ORIGIN + "/pricing", [c[0] for c in calls])


if __name__ == "__main__":
    unittest.main()
