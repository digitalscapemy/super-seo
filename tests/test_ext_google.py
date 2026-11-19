"""Offline tests for extensions/google/google.py (urllib is mocked; no network)."""
from __future__ import annotations

import base64
import contextlib
import csv
import email.message
import hashlib
import importlib.util
import io
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ext_google", ROOT / "extensions" / "google" / "google.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
sys.path.insert(0, str(ROOT / "scripts"))
import gsc_report  # noqa: E402


# --------------------------------------------------------------------------- test-only RSA keygen + PKCS#8 builder
def _is_probable_prime(n: int, rng: random.Random, rounds: int = 30) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 1), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rng: random.Random) -> int:
    while True:
        c = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(c, rng):
            return c


def gen_rsa(bits: int = 1024, seed: int = 1234) -> dict:
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(bits // 2, rng), _prime(bits // 2, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e and (p * q).bit_length() == bits:
            break
    d = pow(e, -1, phi)
    return {"n": p * q, "e": e, "d": d, "p": p, "q": q, "dp": d % (p - 1), "dq": d % (q - 1), "qinv": pow(q, -1, p)}


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(value)) + value


def _int(v: int) -> bytes:
    b = v.to_bytes((v.bit_length() + 8) // 8 or 1, "big")  # leading 0x00 keeps it positive
    return _tlv(0x02, b)


def pkcs1_der(k: dict) -> bytes:
    return _tlv(0x30, b"".join(_int(x) for x in (0, k["n"], k["e"], k["d"], k["p"], k["q"], k["dp"], k["dq"], k["qinv"])))


def pem(der: bytes, label: str) -> str:
    b = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64)) + f"\n-----END {label}-----\n"


def pkcs8_pem(k: dict) -> str:
    alg = _tlv(0x30, _tlv(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00")
    return pem(_tlv(0x30, _int(0) + alg + _tlv(0x04, pkcs1_der(k))), "PRIVATE KEY")


KEY = gen_rsa()


# --------------------------------------------------------------------------- urllib fakes
class FakeResp:
    def __init__(self, payload, status=200):
        self._b = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, payload=b"", headers=None):
    h = email.message.Message()
    for k, v in (headers or {}).items():
        h[k] = v
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return error.HTTPError(url, code, "err", h, io.BytesIO(body))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = g.main(argv)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SUPER_SEO_CONFIG_DIR": self.tmp.name}, clear=False)
        self.env.start()
        for k in ("SUPER_SEO_GOOGLE_SA", "SUPER_SEO_GOOGLE_CLIENT_ID", "SUPER_SEO_GOOGLE_CLIENT_SECRET",
                  "PSI_API_KEY", "SUPER_SEO_CRUX_KEY"):
            os.environ.pop(k, None)
        g._TOKEN.update(value=None, exp=0)
        self.sleep = mock.patch.object(g, "_sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.env.stop()
        self.tmp.cleanup()


# --------------------------------------------------------------------------- RS256
class TestRS256(Base):
    def test_pkcs8_parse_roundtrip(self):
        parsed = g.parse_rsa_private_key(pkcs8_pem(KEY))
        for k in ("n", "e", "d", "p", "q", "dp", "dq", "qinv"):
            self.assertEqual(parsed[k], KEY[k], k)

    def test_pkcs1_parse(self):
        parsed = g.parse_rsa_private_key(pem(pkcs1_der(KEY), "RSA PRIVATE KEY"))
        self.assertEqual(parsed["n"], KEY["n"])

    def test_signature_verifies_with_public_key(self):
        msg = b"header.payload"
        sig = g.rs256_sign(msg, g.parse_rsa_private_key(pkcs8_pem(KEY)))
        k = (KEY["n"].bit_length() + 7) // 8
        self.assertEqual(len(sig), k)
        recovered = pow(int.from_bytes(sig, "big"), KEY["e"], KEY["n"]).to_bytes(k, "big")
        expected = b"\x00\x01" + b"\xff" * (k - 3 - 51) + b"\x00" + \
            bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(msg).digest()
        self.assertEqual(recovered, expected)
        # CRT and plain exponentiation agree
        plain = {"n": KEY["n"], "d": KEY["d"]}
        self.assertEqual(g.rs256_sign(msg, plain), sig)

    def test_bad_pem(self):
        with self.assertRaises(ValueError):
            g.parse_rsa_private_key("not a key")

    def test_service_account_jwt_flow(self):
        sa = Path(self.tmp.name) / "sa.json"
        sa.write_text(json.dumps({"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com",
                                  "private_key": pkcs8_pem(KEY), "private_key_id": "kid1",
                                  "token_uri": "https://oauth2.googleapis.com/token"}))
        os.environ["SUPER_SEO_GOOGLE_SA"] = str(sa)
        seen = {}

        def fake(req, timeout=None):
            if req.full_url.startswith("https://oauth2.googleapis.com/token"):
                form = parse_qs(req.data.decode())
                seen["assertion"] = form["assertion"][0]
                self.assertEqual(form["grant_type"][0], "urn:ietf:params:oauth:grant-type:jwt-bearer")
                return FakeResp({"access_token": "AT", "expires_in": 3600})
            seen["auth"] = req.get_header("Authorization")
            return FakeResp({"siteEntry": [{"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}]})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, err = run(["sites", "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["sites"][0]["siteUrl"], "sc-domain:example.com")
        self.assertEqual(seen["auth"], "Bearer AT")
        h, p, s = seen["assertion"].split(".")
        pad = lambda x: x + "=" * (-len(x) % 4)  # noqa: E731
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(h))), {"alg": "RS256", "typ": "JWT", "kid": "kid1"})
        claims = json.loads(base64.urlsafe_b64decode(pad(p)))
        self.assertEqual(claims["iss"], "bot@proj.iam.gserviceaccount.com")
        self.assertIn("webmasters.readonly", claims["scope"])
        k = (KEY["n"].bit_length() + 7) // 8
        rec = pow(int.from_bytes(base64.urlsafe_b64decode(pad(s)), "big"), KEY["e"], KEY["n"]).to_bytes(k, "big")
        self.assertEqual(rec, g.emsa_pkcs1_v15(f"{h}.{p}".encode(), k))
        self.assertNotIn("AT", out + err)


# --------------------------------------------------------------------------- auth helpers
class TestAuth(Base):
    def test_not_authenticated_is_clean(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network expected")):
            rc, out, err = run(["sites", "--no-write"])
        self.assertEqual(rc, 2)
        self.assertIn("google.py auth", err)
        self.assertIn("SUPER_SEO_GOOGLE_SA", err)
        self.assertNotIn("Traceback", err)

    def test_auth_requires_client_id(self):
        rc, _, err = run(["auth", "--no-browser"])
        self.assertEqual(rc, 2)
        self.assertIn("Desktop app", err)

    def test_pkce(self):
        v, c = g.pkce_pair()
        self.assertTrue(43 <= len(v) <= 128)
        self.assertEqual(c, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode())

    def test_parse_callback(self):
        self.assertEqual(g.parse_callback("code=abc&state=s1", "s1"), {"code": "abc"})
        self.assertIn("state mismatch", g.parse_callback("code=abc&state=evil", "s1")["error"])
        self.assertEqual(g.parse_callback("error=access_denied&state=s1", "s1"), {"error": "access_denied"})
        self.assertIsNone(g.parse_callback("", "s1"))

    def test_saved_token_refresh_and_file_location(self):
        g.write_private(g.token_path(), json.dumps({"refresh_token": "RT", "client_id": "cid", "client_secret": "cs"}))
        self.assertEqual(g.token_path().parent, Path(self.tmp.name))
        if os.name != "nt":
            self.assertEqual(g.token_path().stat().st_mode & 0o777, 0o600)

        def fake(req, timeout=None):
            form = parse_qs(req.data.decode())
            self.assertEqual(form["refresh_token"], ["RT"])
            return FakeResp({"access_token": "AT2", "expires_in": 3600})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(g.access_token(), "AT2")

    def test_invalid_grant_message(self):
        g.write_private(g.token_path(), json.dumps({"refresh_token": "RT", "client_id": "cid"}))
        with mock.patch("urllib.request.urlopen",
                        side_effect=lambda req, timeout=None: (_ for _ in ()).throw(
                            http_error(req.full_url, 400, {"error": "invalid_grant"}))):
            with self.assertRaises(g.NotAuthenticated) as cm:
                g.access_token()
        self.assertIn("auth", str(cm.exception))


# --------------------------------------------------------------------------- http backoff
class TestHttp(Base):
    def test_retries_429_then_ok(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise http_error(req.full_url, 429, {"error": {"message": "slow"}}, {"Retry-After": "3"})
            return FakeResp({"ok": True})

        waits = []
        with mock.patch("urllib.request.urlopen", side_effect=fake), mock.patch.object(g, "_sleep", waits.append):
            self.assertEqual(g.http_json("GET", "https://x.test/")[1], {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(waits[0], 3)

    def test_error_message(self):
        with mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: (_ for _ in ()).throw(
                http_error(req.full_url, 400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad"}}))):
            with self.assertRaises(g.ApiError) as cm:
                g.http_json("GET", "https://x.test/")
        self.assertEqual(cm.exception.status, 400)
        self.assertIn("bad", str(cm.exception))


# --------------------------------------------------------------------------- gsc
class TestGsc(Base):
    def _fake(self, total):
        starts = []

        def fake(req, timeout=None):
            body = json.loads(req.data)
            starts.append(body["startRow"])
            n = min(25000, total - body["startRow"])
            rows = [{"keys": [f"https://ex.com/p{(body['startRow'] + i) % 7}", f"query {body['startRow'] + i}"],
                     "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 4.5} for i in range(max(n, 0))]
            return FakeResp({"rows": rows})
        return fake, starts

    def test_pagination_and_page_query_csv(self):
        fake, starts = self._fake(25003)
        out_dir = Path(self.tmp.name) / "gsc"
        with mock.patch.object(g, "access_token", return_value="T"), mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, err = run(["gsc", "--property", "sc-domain:ex.com", "--dimensions", "page,query", "--start", "2026-10-01",
                                "--end", "2026-10-28", "--out", str(out_dir), "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(starts, [0, 25000])
        data = json.loads(out)
        self.assertEqual(data["rows_total"], 25003)
        path = Path(data["csv"])
        self.assertTrue(path.name.startswith("page_query-"))
        with path.open(encoding="utf-8") as f:
            self.assertEqual(next(csv.reader(f)), ["page", "query", "clicks", "impressions", "ctr", "position"])
        loaded = gsc_report.load(out_dir)
        self.assertEqual(len(loaded["page_query"]), 25003)
        self.assertAlmostEqual(loaded["page_query"][0]["ctr"], 0.1)

    def test_query_csv_format(self):
        def fake(req, timeout=None):
            return FakeResp({"rows": [{"keys": ["best invoicing app"], "clicks": 5, "impressions": 200, "ctr": 0.025, "position": 7.25}]})
        out_dir = Path(self.tmp.name) / "q"
        with mock.patch.object(g, "access_token", return_value="T"), mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, err = run(["gsc", "--property", "https://ex.com/", "--days", "7", "--out", str(out_dir), "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        path = Path(json.loads(out)["csv"])
        self.assertTrue(path.name.startswith("queries-"))
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Top queries,Clicks,Impressions,CTR,Position")
        self.assertEqual(lines[1], "best invoicing app,5,200,2.50%,7.25")
        q = gsc_report.load(out_dir)["queries"][0]
        self.assertEqual((q["query"], q["impressions"], q["ctr"]), ("best invoicing app", 200, 0.025))

    def test_bad_dimension(self):
        rc, _, err = run(["gsc", "--property", "sc-domain:ex.com", "--dimensions", "keyword", "--no-write"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown dimension", err)


# --------------------------------------------------------------------------- inspect / sitemaps
class TestInspect(Base):
    def test_summary_findings(self):
        res = {"inspectionResult": {"indexStatusResult": {
            "verdict": "NEUTRAL", "coverageState": "Crawled - currently not indexed", "robotsTxtState": "ALLOWED",
            "indexingState": "INDEXING_ALLOWED", "lastCrawlTime": "2026-11-01T00:00:00Z",
            "googleCanonical": "https://ex.com/a", "userCanonical": "https://ex.com/b"}}}
        row, f = g.summarise_inspection("https://ex.com/b", res)
        codes = {x["code"]: x["severity"] for x in f}
        self.assertEqual(codes, {"not_indexed": "high", "canonical_mismatch": "medium"})
        ok = {"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "googleCanonical": "https://ex.com/a/",
                                                         "userCanonical": "https://ex.com/a"}}}
        self.assertEqual(g.summarise_inspection("https://ex.com/a", ok)[1], [])

    def test_inspect_command_counts_quota(self):
        def fake(req, timeout=None):
            return FakeResp({"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}})
        with mock.patch.object(g, "access_token", return_value="T"), mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, err = run(["inspect", "--property", "sc-domain:ex.com", "https://ex.com/", "https://ex.com/x",
                                "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["inspected"], 2)
        self.assertEqual(g.inspect_used_today("sc-domain:ex.com"), 2)

    def test_inspect_quota_trim(self):
        g.inspect_count("sc-domain:ex.com", 1999)
        with mock.patch.object(g, "access_token", return_value="T"), mock.patch(
                "urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp({"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}})):
            rc, out, _ = run(["inspect", "--property", "sc-domain:ex.com", "https://ex.com/1", "https://ex.com/2", "--json", "--no-write"])
        data = json.loads(out)
        self.assertEqual(data["inspected"], 1)
        self.assertIn("quota_trimmed", [f["code"] for f in data["findings"]])

    def test_sitemaps(self):
        payload = {"sitemap": [{"path": "https://ex.com/sitemap.xml", "lastSubmitted": "2026-11-01", "lastDownloaded": "2026-11-02",
                                "errors": "2", "warnings": "1", "contents": [{"type": "web", "submitted": "120"}]}]}
        with mock.patch.object(g, "access_token", return_value="T"), mock.patch(
                "urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(payload)):
            rc, out, _ = run(["sitemaps", "--property", "sc-domain:ex.com", "--json", "--no-write"])
        data = json.loads(out)
        self.assertEqual(data["sitemaps"][0]["submitted"], 120)
        self.assertEqual([f["code"] for f in data["findings"]], ["sitemap_errors", "sitemap_warnings"])


# --------------------------------------------------------------------------- ga4
class TestGa4(Base):
    def test_organic_and_ai(self):
        def fake(req, timeout=None):
            body = json.loads(req.data)
            self.assertIn("properties/42:runReport", req.full_url)
            if body["dimensions"][0]["name"] == "landingPagePlusQueryString":
                return FakeResp({"dimensionHeaders": [{"name": "landingPagePlusQueryString"}],
                                 "metricHeaders": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "keyEvents"}],
                                 "rows": [{"dimensionValues": [{"value": "/pricing"}],
                                           "metricValues": [{"value": "100"}, {"value": "60"}, {"value": "3"}]}]})
            self.assertIn("perplexity", body["dimensionFilter"]["filter"]["stringFilter"]["value"])
            return FakeResp({"dimensionHeaders": [{"name": "sessionSource"}, {"name": "landingPagePlusQueryString"}],
                             "metricHeaders": [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "keyEvents"}],
                             "rows": [{"dimensionValues": [{"value": "chatgpt.com"}, {"value": "/"}],
                                       "metricValues": [{"value": "7"}, {"value": "5"}, {"value": "1"}]},
                                      {"dimensionValues": [{"value": "chatgpt.com"}, {"value": "/vs"}],
                                       "metricValues": [{"value": "3"}, {"value": "2"}, {"value": "0"}]}]})
        with mock.patch.object(g, "access_token", return_value="T"), mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, err = run(["ga4", "--property-id", "42", "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertEqual(d["organic_landing_pages"][0]["sessions"], 100)
        self.assertEqual(d["ai_referrals"]["total_sessions"], 10)
        self.assertIn("floor", d["ai_referrals"]["note"])


# --------------------------------------------------------------------------- psi / crux
class TestPsiCrux(Base):
    def test_parse_psi_insights(self):
        resp = {"lighthouseResult": {"lighthouseVersion": "13.0.0", "categories": {"performance": {"score": 0.42}},
                                     "audits": {
                                         "lcp-discovery-insight": {"title": "LCP request discovery", "score": 0, "scoreDisplayMode": "metricSavings", "displayValue": ""},
                                         "image-delivery-insight": {"title": "Improve image delivery", "score": 0.5, "scoreDisplayMode": "metricSavings", "displayValue": "Est savings of 120 KiB"},
                                         "cls-culprits-insight": {"title": "Layout shift culprits", "score": 1, "scoreDisplayMode": "metricSavings"},
                                         "network-dependency-tree-insight": {"title": "Network tree", "score": None, "scoreDisplayMode": "informative"},
                                         "unused-javascript": {"title": "Reduce unused JavaScript", "score": 0.3, "displayValue": "Est savings of 80 KiB",
                                                               "details": {"type": "opportunity"}},
                                         "largest-contentful-paint": {"displayValue": "4.1 s", "score": 0.2}}},
                "loadingExperience": {"metrics": {"CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 30}}}}
        r = g.parse_psi(resp)
        self.assertEqual(r["score"], 42)
        ids = [f["id"] for f in r["failed_audits"]]
        self.assertEqual(set(ids), {"lcp-discovery-insight", "image-delivery-insight", "unused-javascript"})
        self.assertEqual(r["field"]["cumulative_layout_shift"], {"p75": 0.3, "rating": "poor", "scope": "url"})
        self.assertEqual(r["lab"]["LCP"], "4.1 s")

    def test_rate_thresholds(self):
        self.assertEqual(g.rate("largest_contentful_paint", 2500), "good")
        self.assertEqual(g.rate("largest_contentful_paint", 4000), "needs improvement")
        self.assertEqual(g.rate("largest_contentful_paint", 4001), "poor")
        self.assertEqual(g.rate("interaction_to_next_paint", 201), "needs improvement")
        self.assertEqual(g.rate("cumulative_layout_shift", 0.26), "poor")

    def test_crux_no_key(self):
        rc, _, err = run(["crux", "--origin", "https://www.python.org", "--no-write"])
        self.assertEqual(rc, 2)
        self.assertIn("PSI_API_KEY", err)

    def test_crux_record_and_404(self):
        os.environ["PSI_API_KEY"] = "K"

        def fake(req, timeout=None):
            body = json.loads(req.data)
            self.assertEqual(body["formFactor"], "PHONE")
            if "url" in body:
                raise http_error(req.full_url, 404, {"error": {"code": 404, "message": "chrome ux report data not found"}})
            return FakeResp({"record": {"metrics": {
                "largest_contentful_paint": {"percentiles": {"p75": 3100}},
                "interaction_to_next_paint": {"percentiles": {"p75": 150}},
                "cumulative_layout_shift": {"percentiles": {"p75": "0.30"}}},
                "collectionPeriod": {"firstDate": {"year": 2026, "month": 10, "day": 1}, "lastDate": {"year": 2026, "month": 10, "day": 28}}}})
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            rc, out, err = run(["crux", "--origin", "https://ex.com/", "--url", "https://ex.com/p", "--json", "--no-write"])
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        origin = d["results"][0]["data"]
        self.assertEqual(origin["largest_contentful_paint"]["rating"], "needs improvement")
        self.assertEqual(origin["cumulative_layout_shift"]["p75"], 0.3)
        self.assertIsNone(d["results"][1]["data"])
        codes = [f["code"] for f in d["findings"]]
        self.assertIn("cwv_cls_poor", codes)
        self.assertIn("no_field_data", codes)
        self.assertNotIn("K", [c for c in out.split('"') if c == "K"])

    def test_history_regression(self):
        an = g.analyse_series("largest_contentful_paint", [2000, 2100, None, 2000, 2050] + [2100] * 15 + [2600, 2700, 2800, 2900, 3000])
        self.assertEqual(an["trend"], "regressing")
        self.assertGreater(an["change_pct"], 20)
        self.assertEqual(g.analyse_series("cumulative_layout_shift", ["0.05", "0.05", "0.05", "0.05", "0.051"])["trend"], "stable")
        os.environ["SUPER_SEO_CRUX_KEY"] = "K"
        rec = {"record": {"metrics": {"interaction_to_next_paint": {"percentilesTimeseries": {"p75s": [100, 100, 100, 100, 300]}}},
                          "collectionPeriods": [{"firstDate": {"year": 2026, "month": 5, "day": 1}, "lastDate": {"year": 2026, "month": 5, "day": 28}}]}}
        with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(rec)) as m:
            rc, out, _ = run(["crux-history", "--origin", "https://ex.com", "--json", "--no-write"])
            self.assertEqual(json.loads(m.call_args[0][0].data)["collectionPeriodCount"], 25)
        self.assertIn("cwv_inp_regression", [f["code"] for f in json.loads(out)["findings"]])

    def test_lcp_subparts(self):
        os.environ["PSI_API_KEY"] = "K"
        rec = {"record": {"metrics": {
            "largest_contentful_paint": {"percentiles": {"p75": 4500}},
            "largest_contentful_paint_image_time_to_first_byte": {"percentiles": {"p75": 600}},
            "largest_contentful_paint_image_resource_load_delay": {"percentiles": {"p75": 2000}},
            "largest_contentful_paint_image_resource_load_duration": {"percentiles": {"p75": 400}},
            "largest_contentful_paint_image_element_render_delay": {"percentiles": {"p75": 300}}}}}
        with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(rec)):
            rc, out, _ = run(["lcp-subparts", "--origin", "https://ex.com", "--json", "--no-write"])
        f = json.loads(out)["findings"]
        self.assertEqual([x["code"] for x in f], ["lcp_subpart_resource_load_delay"])
        self.assertEqual(f[0]["severity"], "high")
        self.assertIn("fetchpriority", f[0]["message"])


class TestWrite(Base):
    def test_writes_audit_files(self):
        os.environ["PSI_API_KEY"] = "K"
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout=None: (_ for _ in ()).throw(
                    http_error(r.full_url, 404, b""))):
                rc, out, err = run(["crux", "--origin", "https://ex.com"])
            self.assertEqual(rc, 0, err)
            files = sorted(p.name for p in (Path(self.tmp.name) / ".seo" / "audits").iterdir())
            self.assertEqual(len(files), 2)
            self.assertTrue(files[0].startswith("google-crux-ex.com-"), files)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
