"""Offline tests for extensions/browser/render_check.py (no browser, no network)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extensions" / "browser"))
import cdp_lite  # noqa: E402
import render_check as rc  # noqa: E402

URL = "https://app.test/"
FILLER = " ".join(f"benefit{i} analytics dashboard" for i in range(70))  # 210 words


def codes(findings):
    return {f["code"] for f in findings}


def sev(findings, code):
    return next(f["severity"] for f in findings if f["code"] == code)


CSR_RAW = """<!doctype html><html><head><title>App</title><script src="/a.js"></script></head>
<body><div id="root"></div></body></html>"""
CSR_RENDERED = f"""<html><head><title>App</title></head><body><div id="root"><main><h1>Acme Platform</h1>
<p>{FILLER}</p><nav><a href="/pricing">Pricing</a><a href="/docs">Docs</a><a href="https://app.test/blog">Blog</a>
<a href="https://other.test/x">Ext</a></nav></main></div></body></html>"""


class JsGapTests(unittest.TestCase):
    def test_csr_shell_is_critical(self):
        g = rc.js_gap(CSR_RAW, CSR_RENDERED, URL)
        self.assertEqual(sev(g["findings"], "js_only_content"), "critical")
        self.assertEqual(sev(g["findings"], "js_only_internal_links"), "high")
        self.assertEqual(sev(g["findings"], "js_only_h1"), "high")
        self.assertEqual(g["metrics"]["js_only_internal_links"], 3)  # external link ignored
        self.assertTrue(g["content_significant"])
        self.assertGreater(g["metrics"]["js_only_word_ratio"], 0.9)

    def test_ssr_page_has_no_gap(self):
        g = rc.js_gap(CSR_RENDERED, CSR_RENDERED, URL)
        self.assertEqual(codes(g["findings"]), {"no_js_gap"})

    def test_partial_gap_is_high(self):
        extra = " ".join(f"extra{i}" for i in range(100))
        raw = f"<html><body><h1>T</h1><p>{FILLER}</p></body></html>"
        ren = f"<html><body><h1>T</h1><p>{FILLER}</p><p>{extra}</p></body></html>"
        g = rc.js_gap(raw, ren, URL)  # 100 / 311 = 32%
        self.assertEqual(sev(g["findings"], "js_dependent_content"), "high")
        self.assertNotIn("js_only_content", codes(g["findings"]))

    def test_below_thresholds_not_flagged(self):
        raw = f"<html><body><p>{FILLER}</p><a href='/a'>a</a><a href='/b'>b</a><a href='/c'>c</a></body></html>"
        ren = raw.replace("</body>", "<p>one two three four five</p><a href='/d'>d</a><a href='/e'>e</a></body>")
        g = rc.js_gap(raw, ren, URL)
        self.assertEqual(codes(g["findings"]), {"no_js_gap"})  # 5 words, 2 links: below both thresholds

    def test_few_words_but_high_ratio_not_significant(self):
        g = rc.js_gap("<html><body><p>hi</p></body></html>", "<html><body><p>hi a b c d e f g h</p></body></html>", URL)
        self.assertFalse(g["content_significant"])  # < 25 words

    def test_metadata_conflicts_and_jsonld(self):
        raw = (f'<html><head><title>Old</title><link rel="canonical" href="/a"><meta name="robots" content="index,follow">'
               f'</head><body><h1>Raw</h1><p>{FILLER}</p></body></html>')
        ren = (f'<html><head><title>New</title><link rel="canonical" href="/b"><meta name="robots" content="noindex">'
               f'<script type="application/ld+json">{{"@context":"https://schema.org","@type":"SoftwareApplication"}}</script>'
               f'</head><body><h1>Rendered</h1><p>{FILLER}</p></body></html>')
        g = rc.js_gap(raw, ren, URL)
        for c in ("title_changed_by_js", "canonical_changed_by_js", "robots_changed_by_js"):
            self.assertEqual(sev(g["findings"], c), "high")
        self.assertEqual(sev(g["findings"], "js_only_jsonld"), "medium")
        self.assertEqual(sev(g["findings"], "h1_changed_by_js"), "medium")
        self.assertEqual(g["metrics"]["js_only_jsonld_types"], ["SoftwareApplication"])
        self.assertEqual(set(g["metrics"]["conflicts"]), {"title", "canonical", "robots"})

    def test_same_canonical_relative_vs_absolute_is_not_conflict(self):
        raw = f'<html><head><link rel="canonical" href="/a"></head><body><p>{FILLER}</p></body></html>'
        ren = f'<html><head><link rel="canonical" href="https://app.test/a"></head><body><p>{FILLER}</p></body></html>'
        self.assertNotIn("canonical_changed_by_js", codes(rc.js_gap(raw, ren, URL)["findings"]))

    def test_dynamic_rendering_check(self):
        big = f"<html><body><p>{FILLER}</p></body></html>"
        small = "<html><body><p>loading</p></body></html>"
        self.assertEqual(sev(rc.dynamic_rendering_check(big, small, 200, 200), "ua_content_difference"), "medium")
        self.assertEqual(rc.dynamic_rendering_check(big, big, 200, 200), [])
        self.assertIn("ua_status_difference", codes(rc.dynamic_rendering_check(big, big, 403, 200)))


AGENT_BAD = """<html><body>
<div onclick="go()">Buy</div><span onclick="x()">Open</span><div role="button">Menu</div>
<div role="button" tabindex="0">Focusable</div>
<input type="email" placeholder="Email"><textarea></textarea>
<label for="n">Name</label><input id="n"><label>Phone <input type="tel"></label>
<input type="hidden" name="csrf"><input type="search" aria-label="Search">
<button aria-label="Close"><svg></svg></button><button><svg></svg></button><a href="/x"><img src="i.png"></a>
<a href="/y"><img src="logo.png" alt="Home"></a><button>Save</button>
<img src="decor.png" alt=""><span tabindex="3">x</span><video autoplay muted src="v.mp4"></video>
<iframe src="https://maps.test/"></iframe>
</body></html>"""


class AgentUXTests(unittest.TestCase):
    def test_findings_and_score(self):
        r = rc.agent_ux(AGENT_BAD)
        c = r["counts"]
        self.assertEqual(c["clickable_non_semantic"], 3)  # role=button with tabindex is not counted
        self.assertEqual(c["unlabeled_inputs"], 2)        # email + textarea
        self.assertEqual(c["unnamed_controls"], 2)        # bare svg button + img link without alt
        self.assertEqual(c["images_missing_alt"], 1)
        self.assertEqual(c["positive_tabindex"], 1)
        self.assertEqual(c["autoplay_media"], 1)
        self.assertEqual(c["iframes_missing_title"], 1)
        # 100 - 3*5 - 10 (no landmarks) - 2*4 - 2*3 = 61
        self.assertEqual(r["score"], 61)
        self.assertTrue(r["heuristic"])
        self.assertEqual(sev(r["findings"], "clickable_non_semantic"), "high")
        self.assertEqual(sev(r["findings"], "no_landmarks"), "medium")

    def test_caps(self):
        html = "<main><nav></nav>" + '<div onclick="f()">x</div>' * 10 + "<input>" * 10 + "<button></button>" * 10 + "</main>"
        r = rc.agent_ux(html)
        self.assertEqual(r["score"], 100 - 20 - 20 - 20)

    def test_clean_page(self):
        html = ('<html><body><nav><a href="/">Home</a></nav><main><h1>Hi</h1><label>Email <input type="email"></label>'
                '<button type="submit">Send</button><input type="submit"></main></body></html>')
        r = rc.agent_ux(html)
        self.assertEqual(r["score"], 100)
        self.assertEqual(codes(r["findings"]), {"agent_ux_ok"})

    def test_text_inside_script_does_not_name_control(self):
        r = rc.agent_ux("<main><button><script>var a=1</script></button></main>")
        self.assertEqual(r["counts"]["unnamed_controls"], 1)


class MobileTests(unittest.TestCase):
    def test_missing_viewport(self):
        self.assertEqual(sev(rc.mobile_checks("<html><body>x</body></html>")["findings"], "no_viewport"), "high")

    def test_zoom_block_and_inline_styles(self):
        html = ('<html><head><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, '
                'user-scalable=no"></head><body><div style="width: 960px">a</div><div style="max-width:960px">ok</div>'
                '<p style="font-size:10px">tiny</p><p style="font-size:14px">fine</p></body></html>')
        c = codes(rc.mobile_checks(html)["findings"])
        self.assertTrue({"viewport_blocks_zoom", "viewport_max_scale", "fixed_width_inline", "tiny_font_inline"} <= c)
        f = next(x for x in rc.mobile_checks(html)["findings"] if x["code"] == "fixed_width_inline")
        self.assertEqual(len(f["examples"]), 1)  # max-width is not a fixed width

    def test_good_viewport(self):
        html = '<meta name="viewport" content="width=device-width,initial-scale=1">'
        self.assertEqual(codes(rc.mobile_checks(html)["findings"]), {"mobile_ok"})


class FindBrowserTests(unittest.TestCase):
    def test_env_override(self):
        with mock.patch("os.path.exists", side_effect=lambda p: p == "/opt/mybrowser"), \
                mock.patch("shutil.which", return_value=None):
            self.assertEqual(rc.find_browser("linux", {"SUPER_SEO_CHROME": "/opt/mybrowser"}), "/opt/mybrowser")

    def test_windows_edge_in_program_files_x86(self):
        env = {"PROGRAMFILES": r"C:\Program Files", "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
               "LOCALAPPDATA": r"C:\Users\u\AppData\Local"}
        edge = os.path.join(r"C:\Program Files (x86)", r"Microsoft\Edge\Application\msedge.exe")
        with mock.patch("os.path.exists", side_effect=lambda p: p == edge), mock.patch("shutil.which", return_value=None):
            self.assertEqual(rc.find_browser("win32", env), edge)

    def test_windows_prefers_chrome(self):
        env = {"PROGRAMFILES": r"C:\Program Files", "PROGRAMFILES(X86)": r"C:\Program Files (x86)"}
        with mock.patch("os.path.exists", return_value=True), mock.patch("shutil.which", return_value=None):
            self.assertTrue(rc.find_browser("win32", env).endswith("chrome.exe"))

    def test_macos_app_bundle(self):
        target = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        with mock.patch("os.path.exists", side_effect=lambda p: p == target), mock.patch("shutil.which", return_value=None):
            self.assertEqual(rc.find_browser("darwin", {"HOME": "/Users/u"}), target)

    def test_linux_path(self):
        with mock.patch("os.path.exists", return_value=False), \
                mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/chromium" if n == "chromium" else None):
            self.assertEqual(rc.find_browser("linux", {}), "/usr/bin/chromium")

    def test_none_found(self):
        with mock.patch("os.path.exists", return_value=False), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(rc.find_browser("linux", {}))


class ArgvTests(unittest.TestCase):
    """CLI renderer path: only used when protection is off (localhost / --allow-private)."""

    def setUp(self):
        self._prev = rc.PUBLIC_ONLY["on"]
        rc.PUBLIC_ONLY["on"] = False

    def tearDown(self):
        rc.PUBLIC_ONLY["on"] = self._prev

    def test_dump_dom_argv_via_subprocess(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            prof = next(a.split("=", 1)[1] for a in argv if a.startswith("--user-data-dir="))
            seen["profile_existed"] = os.path.isdir(prof)
            seen["profile"] = prof
            return subprocess.CompletedProcess(argv, 0, stdout=b"<html><body>ok</body></html>", stderr=b"")

        with mock.patch("subprocess.run", side_effect=fake_run):
            r = rc.render_dom("/bin/chrome", "https://app.test/x", budget_ms=10000)
        argv = seen["argv"]
        self.assertEqual(argv[0], "/bin/chrome")
        for flag in ("--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                     "--virtual-time-budget=10000"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-2:], ["--dump-dom", "https://app.test/x"])
        self.assertIsInstance(argv, list)
        self.assertEqual(seen["kw"]["timeout"], 60)
        self.assertTrue(seen["kw"]["capture_output"])
        self.assertTrue(seen["profile_existed"])
        self.assertFalse(os.path.exists(seen["profile"]))  # temp profile deleted
        self.assertIsNone(r["error"])
        self.assertIn("ok", r["html"])

    def test_screenshot_argv(self):
        argv = rc.screenshot_argv("chrome", "https://a.test/", "/tmp/p", "/out/x.png", (390, 844), 5000, rc.MOBILE_UA)
        self.assertIn("--screenshot=/out/x.png", argv)
        self.assertIn("--window-size=390,844", argv)
        self.assertIn("--hide-scrollbars", argv)
        self.assertIn(f"--user-agent={rc.MOBILE_UA}", argv)
        self.assertIn("--virtual-time-budget=5000", argv)
        self.assertEqual(argv[-1], "https://a.test/")

    def test_timeout_reported(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("chrome", 60)):
            r = rc.render_dom("chrome", "https://app.test/")
        self.assertIn("timed out", r["error"])

    def test_empty_dom_is_error(self):
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"boom")):
            self.assertIn("no DOM", rc.render_dom("chrome", "https://app.test/")["error"])

    def test_cdp_launch_argv_is_local(self):
        argv = cdp_lite.launch_argv("chrome", "/tmp/p")
        self.assertIn("--remote-debugging-port=0", argv)
        self.assertIn("--remote-debugging-address=127.0.0.1", argv)
        with self.assertRaises(cdp_lite.CDPError):
            cdp_lite._WS("ws://evil.test:9222/devtools/page/1", timeout=1)


class GuardTests(unittest.TestCase):
    def test_non_http_refused(self):
        self.assertIn("refused", rc.guard("file:///etc/passwd", False))
        self.assertIn("refused", rc.guard("javascript:alert(1)", False))

    def test_localhost_allowed(self):
        self.assertIsNone(rc.guard("http://127.0.0.1:3000/", False))

    def test_public_host_resolving_private_refused(self):
        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]):
            # start host resolves only to private -> treated as local (same rule as crawl_audit)
            self.assertIsNone(rc.guard("http://intranet.test/", False))
        mixed = [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]
        with mock.patch("socket.getaddrinfo", return_value=mixed):
            self.assertIn("refused", rc.guard("http://mixed.test/", False))
            self.assertIsNone(rc.guard("http://mixed.test/", True))


if __name__ == "__main__":
    unittest.main()


class BrowserNetworkGuardTests(unittest.TestCase):
    """Protected mode (public start URL): every browser request is intercepted and checked."""

    def _handler(self):
        sent = []

        class FakeSess:
            def send_nowait(self, method, params=None):
                sent.append((method, params))

        handler, state = cdp_lite.make_network_guard(
            lambda u: None if "public.test" in u else "blocked private/internal address",
            lambda ip: ip == "93.184.216.34")
        return handler, state, FakeSess(), sent

    def _paused(self, url, rid="1"):
        return {"method": "Fetch.requestPaused", "params": {"requestId": rid, "request": {"url": url}}}

    def test_public_request_continues(self):
        h, state, sess, sent = self._handler()
        self.assertTrue(h(sess, self._paused("https://public.test/app.js")))
        self.assertEqual(sent[-1][0], "Fetch.continueRequest")
        self.assertEqual(state["blocked"], [])

    def test_private_redirect_or_subresource_fails(self):
        h, state, sess, sent = self._handler()
        h(sess, self._paused("http://169.254.169.254/latest/meta-data/", "7"))
        self.assertEqual(sent[-1], ("Fetch.failRequest", {"requestId": "7", "errorReason": "BlockedByClient"}))
        self.assertEqual(len(state["blocked"]), 1)

    def test_data_urls_allowed(self):
        h, state, sess, sent = self._handler()
        h(sess, self._paused("data:image/png;base64,AAAA"))
        self.assertEqual(sent[-1][0], "Fetch.continueRequest")

    def test_connected_address_enforced_by_proxy_not_handler(self):
        # The connected-IP rule lives in guard_proxy (see test_ext_browser_proxy.py); the CDP handler only screens.
        h, state, sess, sent = self._handler()
        self.assertFalse(h(sess, {"method": "Network.responseReceived",
                                  "params": {"response": {"url": "https://public.test/", "remoteIPAddress": "127.0.0.1"}}}))

    def test_no_per_host_verdict_cache(self):
        calls = []
        handler, state = cdp_lite.make_network_guard(lambda u: calls.append(u), lambda ip: True)

        class S:
            def send_nowait(self, *a):
                pass
        for i in range(3):
            handler(S(), self._paused("https://public.test/a%d" % i, str(i)))
        self.assertEqual(len(calls), 3)  # evaluated per request

    def test_unrelated_events_pass_through(self):
        h, state, sess, sent = self._handler()
        self.assertFalse(h(sess, {"method": "Page.loadEventFired", "params": {}}))

    def test_protected_render_uses_guarded_devtools(self):
        prev = rc.PUBLIC_ONLY["on"]
        rc.PUBLIC_ONLY["on"] = True
        try:
            with mock.patch.object(rc.cdp_lite, "emulate", return_value={"error": None, "html": "<html>x</html>"}) as em,                     mock.patch("subprocess.run") as run:
                r = rc.render_dom("chrome", "https://public.test/")
            self.assertIsNotNone(em.call_args.kwargs.get("network_guard"))
            run.assert_not_called()  # the un-interceptable CLI renderer is never used for public URLs
            self.assertEqual(r["html"], "<html>x</html>")
        finally:
            rc.PUBLIC_ONLY["on"] = prev
