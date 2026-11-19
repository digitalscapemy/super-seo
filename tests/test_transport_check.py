"""Offline tests for scripts/transport_check.py (mocked sockets/ssl, synthetic fetch results)."""
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import crawl_audit as ca  # noqa: E402
import transport_check as tc  # noqa: E402

NOW = ssl.cert_time_to_seconds("Jun  1 00:00:00 2026 GMT")


def cert(not_after="Jan  1 00:00:00 2027 GMT", not_before="Jan  1 00:00:00 2026 GMT", names=("example.com", "*.example.com")):
    return {"notAfter": not_after, "notBefore": not_before, "subjectAltName": tuple(("DNS", n) for n in names),
            "issuer": ((("organizationName", "Test CA"),),), "subject": ((("commonName", names[0]),),)}


class FakeSock:
    def __init__(self, peer="93.184.216.34"):
        self.peer = peer
        self.closed = False

    def getpeername(self):
        return (self.peer, 443)

    def close(self):
        self.closed = True


class FakeSSLSock:
    def __init__(self, c, version="TLSv1.3"):
        self.c, self.v = c, version

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def version(self):
        return self.v

    def cipher(self):
        return ("TLS_AES_128_GCM_SHA256", "TLSv1.3", 128)

    def getpeercert(self, binary_form=False):
        return b"DER" if binary_form else self.c


class FakeCtx:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.check_hostname = True
        self.verify_mode = ssl.CERT_REQUIRED

    def wrap_socket(self, sock, server_hostname=None):
        self.sni = server_hostname
        if self.verify_mode == ssl.CERT_NONE:
            return FakeSSLSock({}, "TLSv1.2")
        b = self.behaviour
        if isinstance(b, Exception):
            raise b
        return b


def verr(code, msg):
    e = ssl.SSLCertVerificationError(1, msg)
    e.verify_code, e.verify_message = code, msg
    return e


class TLSProbe(unittest.TestCase):
    def setUp(self):
        ca.PUBLIC_ONLY["on"] = True

    def tearDown(self):
        ca.PUBLIC_ONLY["on"] = True

    def probe(self, behaviour, peer="93.184.216.34", decoded=None):
        ctxs = []

        def make():
            c = FakeCtx(behaviour)
            ctxs.append(c)
            return c
        with mock.patch.object(ca, "is_private_host", return_value=False), \
                mock.patch.object(tc, "decode_der", return_value=decoded or {}):
            r = tc.tls_probe("example.com", 443, 5, connect=lambda addr, t: FakeSock(peer), make_context=make)
        return r, ctxs

    def test_valid(self):
        r, ctxs = self.probe(FakeSSLSock(cert()))
        self.assertTrue(r["verified"])
        self.assertEqual(ctxs[0].sni, "example.com")
        f, info = tc.analyze_tls(r, now=NOW)
        self.assertEqual([x["code"] for x in f], ["tls_ok"])
        self.assertEqual(info["issuer"], "Test CA")

    def test_private_peer_blocked(self):
        r, _ = self.probe(FakeSSLSock(cert()), peer="10.0.0.5")
        self.assertTrue(r.get("blocked"))
        f, _ = tc.analyze_tls(r, now=NOW)
        self.assertEqual(f[0]["code"], "tls_blocked")

    def test_private_peer_allowed_in_local_mode(self):
        ca.PUBLIC_ONLY["on"] = False
        r, _ = self.probe(FakeSSLSock(cert()), peer="127.0.0.1")
        self.assertTrue(r["verified"])

    def test_expired(self):
        r, _ = self.probe(verr(10, "certificate has expired"), decoded=cert(not_after="Jan  1 00:00:00 2026 GMT",
                                                                            not_before="Jan  1 00:00:00 2025 GMT"))
        self.assertFalse(r["verified"])
        f, _ = tc.analyze_tls(r, now=NOW)
        codes = [x["code"] for x in f]
        self.assertIn("cert_expired", codes)
        self.assertEqual(codes.count("cert_expired"), 1)
        self.assertEqual(next(x for x in f if x["code"] == "cert_expired")["severity"], "critical")

    def test_hostname_mismatch(self):
        r, _ = self.probe(verr(62, "Hostname mismatch"), decoded=cert(names=("other.test",)))
        f, _ = tc.analyze_tls(r, now=NOW)
        self.assertEqual([x["code"] for x in f if x["severity"] == "critical"], ["cert_hostname_mismatch"])

    def test_chain_untrusted(self):
        r, _ = self.probe(verr(20, "unable to get local issuer certificate"), decoded=cert())
        f, _ = tc.analyze_tls(r, now=NOW)
        self.assertEqual(f[0]["code"], "cert_chain_untrusted")
        self.assertEqual(f[0]["severity"], "high")

    def test_expiring_soon_and_legacy(self):
        r = {"host": "example.com", "port": 443, "verified": True, "protocol": "TLSv1.1", "cipher": "x",
             "cert": cert(not_after="Jun 10 00:00:00 2026 GMT"), "error": None}
        f, info = tc.analyze_tls(r, now=NOW)
        sev = {x["code"]: x["severity"] for x in f}
        self.assertEqual(sev["cert_expiring_soon"], "high")
        self.assertEqual(sev["tls_legacy_protocol"], "medium")
        self.assertAlmostEqual(info["days_left"], 9.0, places=0)

    def test_not_yet_valid(self):
        r = {"host": "example.com", "port": 443, "verified": True, "protocol": "TLSv1.3",
             "cert": cert(not_before="Jul  1 00:00:00 2026 GMT"), "error": None}
        f, _ = tc.analyze_tls(r, now=NOW)
        self.assertIn("cert_not_yet_valid", [x["code"] for x in f])

    def test_protocol_error(self):
        r, _ = self.probe(ssl.SSLError(1, "[SSL: UNSUPPORTED_PROTOCOL] unsupported protocol"))
        self.assertTrue(r.get("legacy_protocol_suspected"))
        f, _ = tc.analyze_tls(r, now=NOW)
        self.assertEqual(f[0]["code"], "tls_legacy_protocol")

    def test_wildcard(self):
        self.assertTrue(tc.hostname_matches("www.example.com", ["*.example.com"]))
        self.assertFalse(tc.hostname_matches("a.b.example.com", ["*.example.com"]))
        self.assertFalse(tc.hostname_matches("example.com", ["*.example.com"]))


def res(status=200, chain=(), final="https://example.com/", error=None):
    return {"status": status, "chain": list(chain), "final_url": final, "error": error, "headers": {}, "body": ""}


class Redirects(unittest.TestCase):
    def codes(self, f):
        return [x["code"] for x in f]

    def test_single_301(self):
        f = tc.classify_http_redirect(res(chain=[{"url": "http://example.com/", "status": 301, "to": "https://example.com/"}]))
        self.assertEqual(self.codes(f), ["http_to_https_ok"])

    def test_missing(self):
        f = tc.classify_http_redirect(res(status=200))
        self.assertEqual(f[0]["code"], "no_https_redirect")
        self.assertEqual(f[0]["severity"], "high")

    def test_302_and_chain(self):
        f = tc.classify_http_redirect(res(chain=[{"url": "http://example.com/", "status": 302, "to": "http://www.example.com/"},
                                                 {"url": "http://www.example.com/", "status": 301, "to": "https://www.example.com/"}]))
        self.assertEqual(sorted(self.codes(f)), ["http_redirect_chained", "http_redirect_temporary"])
        self.assertTrue(all(x["severity"] == "low" for x in f))

    def test_www_both_200(self):
        f = tc.classify_www("example.com", res(final="https://example.com/"), res(final="https://www.example.com/"))
        self.assertEqual(f[0]["code"], "www_apex_both_200")
        self.assertEqual(f[0]["severity"], "medium")

    def test_www_redirect(self):
        apex = res(chain=[{"url": "https://example.com/", "status": 301, "to": "https://www.example.com/"}], final="https://www.example.com/")
        f = tc.classify_www("example.com", apex, res(final="https://www.example.com/"))
        self.assertEqual(f[0]["code"], "www_apex_ok")
        apex["chain"][0]["status"] = 302
        self.assertEqual(tc.classify_www("example.com", apex, res())[0]["code"], "www_apex_temporary")

    def test_apex_heuristic(self):
        self.assertTrue(tc.is_apex_like("example.com"))
        self.assertTrue(tc.is_apex_like("example.co.uk"))
        self.assertFalse(tc.is_apex_like("blog.example.com"))

    def test_www_single(self):
        f = tc.classify_www("example.com", res(), res(status=0, error="dns"))
        self.assertEqual(f[0]["code"], "www_single_variant")


class Headers(unittest.TestCase):
    GOOD = {"strict-transport-security": "max-age=63072000; includeSubDomains; preload",
            "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
            "x-content-type-options": "nosniff", "referrer-policy": "strict-origin-when-cross-origin",
            "permissions-policy": "camera=()"}

    def test_good(self):
        f = tc.check_headers(self.GOOD, True)
        self.assertEqual([x["code"] for x in f], ["hsts_preload_ready"])

    def test_missing_everything(self):
        sev = {x["code"]: x["severity"] for x in tc.check_headers({}, True)}
        self.assertEqual(sev["hsts_missing"], "medium")
        for c in ("csp_missing", "xcto_missing", "clickjacking_unprotected", "referrer_policy_missing"):
            self.assertEqual(sev[c], "low")
        self.assertEqual(sev["permissions_policy_missing"], "info")

    def test_variants(self):
        h = {"strict-transport-security": "max-age=300", "content-security-policy-report-only": "default-src 'self'",
             "x-frame-options": "SAMEORIGIN", "referrer-policy": "unsafe-url"}
        codes = {x["code"] for x in tc.check_headers(h, True)}
        self.assertTrue({"hsts_short", "hsts_no_subdomains", "csp_report_only", "referrer_policy_unsafe"} <= codes)
        self.assertNotIn("clickjacking_unprotected", codes)
        h = {"content-security-policy": "script-src 'self' 'unsafe-inline'"}
        self.assertIn("csp_unsafe_inline", {x["code"] for x in tc.check_headers(h, True)})
        h = {"content-security-policy": "script-src 'self' 'unsafe-inline' 'nonce-abc'"}
        self.assertNotIn("csp_unsafe_inline", {x["code"] for x in tc.check_headers(h, True)})
        self.assertIn("hsts_not_applicable", {x["code"] for x in tc.check_headers({}, False)})

    def test_cookies_names_only(self):
        f, rows = tc.check_cookies(["sid=SECRETVALUE; Path=/; HttpOnly", "pref=dark; Secure; HttpOnly; SameSite=Lax",
                                    "x=1; SameSite=None"], True)
        dump = repr(f) + repr(rows)
        self.assertNotIn("SECRETVALUE", dump)
        codes = {x["code"]: x["cookies"] for x in f}
        self.assertEqual(codes["cookie_no_secure"], ["sid", "x"])
        self.assertEqual(codes["cookie_no_samesite"], ["sid"])
        self.assertEqual(codes["cookie_no_httponly"], ["x"])
        self.assertEqual(codes["cookie_samesite_none_insecure"], ["x"])

    def test_mixed_content(self):
        html = ('<html><head><script src="http://cdn.test/a.js"></script><link rel="stylesheet" href="http://cdn.test/a.css">'
                '</head><body><img src="http://img.test/a.png"><img src="https://ok.test/b.png" srcset="http://img.test/c.png 2x">'
                '<a href="http://example.org/">plain link is fine</a><iframe src="http://embed.test/"></iframe></body></html>')
        f = {x["code"]: x for x in tc.mixed_content(html)}
        self.assertEqual(f["mixed_content_active"]["severity"], "high")
        self.assertEqual(len(f["mixed_content_active"]["samples"]), 3)
        self.assertEqual(f["mixed_content_passive"]["severity"], "medium")
        self.assertEqual(len(f["mixed_content_passive"]["samples"]), 2)
        self.assertEqual(tc.mixed_content('<img src="https://a.test/x.png">'), [])


class RunLocal(unittest.TestCase):
    def test_local_http_skips_tls(self):
        page = {"url": "http://localhost:3000/", "final_url": "http://localhost:3000/", "status": 200, "error": None,
                "chain": [], "headers": {"content-type": "text/html"}, "body": "<html></html>"}
        raw = {"status": 200, "error": None, "headers": {"content-type": "text/html", "x-content-type-options": "nosniff"},
               "set_cookie": ["a=1"], "body": "<html></html>"}
        with mock.patch.object(tc, "tls_probe") as tp, mock.patch.object(ca, "fetch", return_value=page) as fe, \
                mock.patch.object(tc, "get_raw", return_value=raw):
            r = tc.run("http://localhost:3000/")
        tp.assert_not_called()
        self.assertEqual(fe.call_count, 1)
        codes = [x["code"] for x in r["findings"]]
        self.assertIn("tls_skipped_local", codes)
        self.assertNotIn("final_not_https", codes)
        self.assertIn("not ranking factors", tc.to_markdown(r))


if __name__ == "__main__":
    unittest.main()
