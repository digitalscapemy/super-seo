#!/usr/bin/env python3
"""google.py - Google Search Console, GA4, PageSpeed Insights and CrUX for super-seo. Stdlib only, Python 3.10+.

Authentication (pick one):
  OAuth (your own Google account): create an OAuth client of type "Desktop app" in Google Cloud console, then
      set SUPER_SEO_GOOGLE_CLIENT_ID and SUPER_SEO_GOOGLE_CLIENT_SECRET and run:
      python google.py auth
    The refresh token is saved to %APPDATA%\\super-seo\\google-token.json (Windows) or
    ~/.config/super-seo/google-token.json (file mode 600). Tokens are never printed.
  Service account: set SUPER_SEO_GOOGLE_SA to the path of the service-account JSON key and add the
    service-account email as a user in Search Console (and as Viewer in GA4).
  PSI / CrUX only need an API key: PSI_API_KEY (or SUPER_SEO_CRUX_KEY for CrUX). PSI also works keyless
    while Google's shared quota allows.

Commands:
  python google.py auth
  python google.py sites
  python google.py gsc --property sc-domain:example.com --days 28 --dimensions query --out
  python google.py gsc --property https://example.com/ --start 2026-08-01 --end 2026-10-31 --dimensions page,query --out
  python google.py inspect --property sc-domain:example.com https://example.com/ https://example.com/pricing
  python google.py inspect --property sc-domain:example.com --from-sitemap https://example.com/sitemap.xml --limit 100
  python google.py sitemaps --property sc-domain:example.com
  python google.py ga4 --property-id 123456789 --days 28
  python google.py psi https://example.com/
  python google.py crux --origin https://example.com --url https://example.com/pricing
  python google.py crux-history --origin https://example.com
  python google.py lcp-subparts --origin https://example.com

Every command accepts --json (machine-readable) and --no-write (skip .seo/audits/google-<cmd>-<host>-<stamp>.json|md).
Exit codes: 0 ok, 1 API error, 2 not authenticated / missing key / bad arguments.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import http.server
import json
import os
import random
import re
import secrets
import socket
import statistics
import sys
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
UA = "super-seo-google/1.0"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
WM = "https://www.googleapis.com/webmasters/v3"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
GA4_URL = "https://analyticsdata.googleapis.com/v1beta/{prop}:runReport"
PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_URL = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
CRUX_HIST_URL = "https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord"

SCOPE_GSC = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_GA4 = "https://www.googleapis.com/auth/analytics.readonly"
OAUTH_SCOPES = [SCOPE_GSC, SCOPE_GA4, "openid", "email"]

GSC_DIMENSIONS = {"query", "page", "country", "device", "date", "searchAppearance"}
GSC_PAGE_SIZE = 25000
INSPECT_DAILY_QUOTA = 2000
AI_SOURCES = r"chatgpt\.com|chat\.openai\.com|perplexity\.ai|gemini\.google\.com|copilot\.microsoft\.com|claude\.ai|you\.com|meta\.ai"

# CWV thresholds (good <= first, poor > second)
THRESHOLDS = {"largest_contentful_paint": (2500, 4000), "interaction_to_next_paint": (200, 500),
              "cumulative_layout_shift": (0.1, 0.25)}
CWV_LABEL = {"largest_contentful_paint": "LCP", "interaction_to_next_paint": "INP", "cumulative_layout_shift": "CLS"}
LCP_SUBPARTS = {
    "largest_contentful_paint_image_time_to_first_byte": (
        "TTFB", "Server is slow to send the HTML: cache HTML at the CDN/edge, avoid redirects, render statically where possible."),
    "largest_contentful_paint_image_resource_load_delay": (
        "Resource load delay", "LCP image is discovered late: put it in the initial HTML (not CSS/JS), add fetchpriority=\"high\" "
        "or <link rel=\"preload\">, never lazy-load it."),
    "largest_contentful_paint_image_resource_load_duration": (
        "Resource load duration", "LCP image downloads slowly: resize and compress (AVIF/WebP), serve responsive srcset, use a CDN."),
    "largest_contentful_paint_image_element_render_delay": (
        "Element render delay", "Image is loaded but not painted: cut render-blocking CSS/JS, avoid client-only rendering or "
        "hydration gating the hero, avoid fade-in animations on it."),
}
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

_sleep = time.sleep  # patched in tests


class ApiError(Exception):
    def __init__(self, status: int, message: str, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class NotAuthenticated(Exception):
    pass


NOT_AUTH_HINT = ("Not authenticated with Google. Run `python extensions/google/google.py auth` "
                 "(needs SUPER_SEO_GOOGLE_CLIENT_ID / SUPER_SEO_GOOGLE_CLIENT_SECRET from a 'Desktop app' OAuth client) "
                 "or set SUPER_SEO_GOOGLE_SA to a service-account JSON key file.")


# --------------------------------------------------------------------------- http
def _loads(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"_raw": raw[:2000].decode("utf-8", errors="replace")}


def _err_message(payload, status: int) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return f"HTTP {status} {err.get('status', '')}: {err.get('message', '')}".strip()
        if isinstance(err, str):
            return f"HTTP {status}: {err} {payload.get('error_description', '')}".strip()
        if "_raw" in payload:
            return f"HTTP {status}: {payload['_raw'][:300]}"
    return f"HTTP {status}"


def backoff_seconds(attempt: int, retry_after: str | None = None) -> float:
    wait = 2 ** attempt + random.uniform(0, 1)  # 1, 2, 4, 8, 16 s + jitter
    if retry_after and retry_after.strip().isdigit():
        wait = max(wait, float(retry_after.strip()))
    return min(wait, 120.0)


def http_json(method: str, url: str, body=None, form: dict | None = None, headers: dict | None = None,
              timeout: float = 60.0, retries: int = 5):
    """JSON request with exponential backoff on 429/5xx and network errors. Returns (status, payload)."""
    h = {"Accept": "application/json", "User-Agent": UA}
    data = None
    if form is not None:
        data = urlencode(form).encode("utf-8")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    for attempt in range(retries + 1):
        req = request.Request(url, data=data, headers=h, method=method)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                return getattr(resp, "status", 200), _loads(resp.read())
        except error.HTTPError as e:
            try:
                raw = e.read() or b""
            except Exception:
                raw = b""
            payload = _loads(raw)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                _sleep(backoff_seconds(attempt, e.headers.get("Retry-After") if e.headers else None))
                continue
            raise ApiError(e.code, _err_message(payload, e.code), payload) from None
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                _sleep(backoff_seconds(attempt))
                continue
            raise ApiError(0, f"network error: {getattr(e, 'reason', e)}") from None
    raise ApiError(0, "retries exhausted")


# --------------------------------------------------------------------------- config / token storage
def config_dir() -> Path:
    if os.environ.get("SUPER_SEO_CONFIG_DIR"):
        return Path(os.environ["SUPER_SEO_CONFIG_DIR"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "super-seo"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "super-seo"


def token_path() -> Path:
    return config_dir() / "google-token.json"


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_saved_token() -> dict | None:
    p = token_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- RS256 (pure Python)
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _der_read(buf: bytes, pos: int) -> tuple[int, bytes, int]:
    """Read one DER TLV at pos. Returns (tag, value, next_pos)."""
    if pos + 2 > len(buf):
        raise ValueError("truncated DER")
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or pos + n > len(buf):
            raise ValueError("bad DER length")
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    if pos + length > len(buf):
        raise ValueError("truncated DER value")
    return tag, buf[pos:pos + length], pos + length


def _der_children(value: bytes) -> list[tuple[int, bytes]]:
    out, pos = [], 0
    while pos < len(value):
        tag, val, pos = _der_read(value, pos)
        out.append((tag, val))
    return out


RSA_OID = bytes.fromhex("2a864886f70d010101")  # 1.2.840.113549.1.1.1


def parse_rsa_private_key(pem: str) -> dict:
    """Parse a PKCS#8 ('BEGIN PRIVATE KEY') or PKCS#1 ('BEGIN RSA PRIVATE KEY') PEM into n, e, d, p, q, dp, dq, qinv."""
    m = re.search(r"-----BEGIN (RSA )?PRIVATE KEY-----(.+?)-----END (RSA )?PRIVATE KEY-----", pem, re.S)
    if not m:
        raise ValueError("no PEM private key found")
    der = base64.b64decode("".join(m.group(2).split()))
    tag, body, _ = _der_read(der, 0)
    if tag != 0x30:
        raise ValueError("private key is not a DER SEQUENCE")
    items = _der_children(body)
    if not m.group(1):  # PKCS#8: version, AlgorithmIdentifier, OCTET STRING(RSAPrivateKey)
        if len(items) < 3 or items[1][0] != 0x30 or items[2][0] != 0x04:
            raise ValueError("unexpected PKCS#8 structure")
        alg = _der_children(items[1][1])
        if not alg or alg[0][0] != 0x06 or alg[0][1] != RSA_OID:
            raise ValueError("PKCS#8 key is not RSA")
        tag, body, _ = _der_read(items[2][1], 0)
        if tag != 0x30:
            raise ValueError("bad RSAPrivateKey")
        items = _der_children(body)
    ints = [int.from_bytes(v, "big") for t, v in items if t == 0x02]
    if len(ints) < 9:
        raise ValueError("RSAPrivateKey has too few integers")
    names = ["version", "n", "e", "d", "p", "q", "dp", "dq", "qinv"]
    return dict(zip(names, ints[:9]))


SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")


def emsa_pkcs1_v15(message: bytes, k: int) -> bytes:
    t = SHA256_DIGESTINFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        raise ValueError("RSA modulus too short")
    return b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def rs256_sign(message: bytes, key: dict) -> bytes:
    n = key["n"]
    k = (n.bit_length() + 7) // 8
    m = int.from_bytes(emsa_pkcs1_v15(message, k), "big")
    if all(key.get(x) for x in ("p", "q", "dp", "dq", "qinv")):  # CRT
        m1 = pow(m, key["dp"], key["p"])
        m2 = pow(m, key["dq"], key["q"])
        h = (key["qinv"] * (m1 - m2)) % key["p"]
        s = m2 + h * key["q"]
    else:
        s = pow(m, key["d"], n)
    return s.to_bytes(k, "big")


def make_jwt(claims: dict, key: dict, kid: str | None = None) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    if kid:
        header["kid"] = kid
    signing_input = b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        b64url(json.dumps(claims, separators=(",", ":")).encode())
    return signing_input + "." + b64url(rs256_sign(signing_input.encode("ascii"), key))


# --------------------------------------------------------------------------- access tokens
_TOKEN = {"value": None, "exp": 0.0}


def service_account_token(path: str, scopes: list[str], timeout: float = 30.0) -> tuple[str, float]:
    p = Path(path)
    if not p.is_file():
        raise NotAuthenticated(f"SUPER_SEO_GOOGLE_SA points to a file that does not exist: {path}")
    try:
        sa = json.loads(p.read_text(encoding="utf-8"))
        key = parse_rsa_private_key(sa["private_key"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        raise NotAuthenticated(f"Cannot read the service-account key in SUPER_SEO_GOOGLE_SA: {e.__class__.__name__}: {e}")
    now = int(time.time())
    aud = sa.get("token_uri") or TOKEN_URL
    claims = {"iss": sa.get("client_email"), "scope": " ".join(scopes), "aud": aud, "iat": now, "exp": now + 3600}
    assertion = make_jwt(claims, key, sa.get("private_key_id"))
    try:
        _, tok = http_json("POST", aud, form={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                              "assertion": assertion}, timeout=timeout, retries=3)
    except ApiError as e:
        raise NotAuthenticated(f"Service-account token request failed ({e}). Check the key and that the APIs are enabled.")
    return tok["access_token"], time.time() + float(tok.get("expires_in", 3600))


def refresh_access_token(saved: dict, timeout: float = 30.0) -> tuple[str, float]:
    cid = saved.get("client_id") or os.environ.get("SUPER_SEO_GOOGLE_CLIENT_ID")
    secret = saved.get("client_secret") or os.environ.get("SUPER_SEO_GOOGLE_CLIENT_SECRET")
    if not (cid and saved.get("refresh_token")):
        raise NotAuthenticated(NOT_AUTH_HINT)
    form = {"grant_type": "refresh_token", "refresh_token": saved["refresh_token"], "client_id": cid}
    if secret:
        form["client_secret"] = secret
    try:
        _, tok = http_json("POST", TOKEN_URL, form=form, timeout=timeout, retries=3)
    except ApiError as e:
        if isinstance(e.payload, dict) and e.payload.get("error") == "invalid_grant":
            raise NotAuthenticated("Saved Google authorisation was revoked or expired (an OAuth app in 'Testing' "
                                   "expires refresh tokens after 7 days). Run `python extensions/google/google.py auth` again.")
        raise
    return tok["access_token"], time.time() + float(tok.get("expires_in", 3600))


def access_token() -> str:
    if _TOKEN["value"] and _TOKEN["exp"] > time.time() + 60:
        return _TOKEN["value"]
    sa = os.environ.get("SUPER_SEO_GOOGLE_SA")
    if sa:
        tok, exp = service_account_token(sa, [SCOPE_GSC, SCOPE_GA4])
    else:
        saved = load_saved_token()
        if not saved:
            raise NotAuthenticated(NOT_AUTH_HINT)
        tok, exp = refresh_access_token(saved)
    _TOKEN.update(value=tok, exp=exp)
    return tok


def gapi(method: str, url: str, body=None, timeout: float = 60.0):
    tok = access_token()
    try:
        return http_json(method, url, body=body, headers={"Authorization": f"Bearer {tok}"}, timeout=timeout)[1]
    except ApiError as e:
        if e.status == 401:
            raise NotAuthenticated("Google rejected the access token (401). " + NOT_AUTH_HINT)
        if e.status == 403:
            raise ApiError(403, f"{e} - the account has no access to this property, or the API is not enabled in the "
                                "Google Cloud project (enable Search Console API / Google Analytics Data API).", e.payload)
        raise


# --------------------------------------------------------------------------- OAuth loopback + PKCE
def pkce_pair() -> tuple[str, str]:
    verifier = b64url(secrets.token_bytes(48))  # 64 chars, within RFC 7636's 43-128
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def parse_callback(query: str, expected_state: str) -> dict | None:
    """Validate the loopback redirect. Returns {'code'} or {'error'}; None for unrelated requests (favicon)."""
    q = parse_qs(query)
    state = (q.get("state") or [""])[0]
    if not state and "code" not in q and "error" not in q:
        return None
    if not hmac.compare_digest(state.encode(), expected_state.encode()):
        return {"error": "state mismatch (possible CSRF); start `google.py auth` again"}
    if "error" in q:
        return {"error": q["error"][0]}
    code = (q.get("code") or [""])[0]
    return {"code": code} if code else {"error": "no authorization code in the redirect"}


def decode_id_token_email(id_token: str | None) -> str | None:
    """The id_token comes straight from Google's token endpoint over TLS; only the email claim is read (display only)."""
    if not id_token or id_token.count(".") != 2:
        return None
    part = id_token.split(".")[1]
    try:
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("email")
    except (ValueError, json.JSONDecodeError):
        return None


def cmd_auth(a) -> dict:
    cid = os.environ.get("SUPER_SEO_GOOGLE_CLIENT_ID")
    secret = os.environ.get("SUPER_SEO_GOOGLE_CLIENT_SECRET")
    if not cid:
        raise NotAuthenticated("Set SUPER_SEO_GOOGLE_CLIENT_ID and SUPER_SEO_GOOGLE_CLIENT_SECRET first: Google Cloud console > "
                               "APIs & Services > Credentials > Create OAuth client ID > Application type 'Desktop app'. "
                               "Enable Search Console API and Google Analytics Data API in the same project.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            res = parse_callback(urlparse(self.path).query, state)
            if res is None:
                self.send_response(404)
                self.end_headers()
                return
            result.update(res)
            ok = "code" in res
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Google access granted. You can close this tab and return to the terminal." if ok else \
                "Authorisation failed. Return to the terminal for details."
            self.wfile.write(f"<!doctype html><title>super-seo</title><p>{msg}</p>".encode("utf-8"))

        def log_message(self, *args):  # keep the code out of the console
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/"
    url = AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(OAUTH_SCOPES),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
        "include_granted_scopes": "true", "prompt": "consent"})
    print("Opening the Google consent page. If no browser opens, visit this URL on this machine:\n" + url + "\n",
          file=sys.stderr)
    if not a.no_browser:
        webbrowser.open(url)
    deadline = time.time() + a.wait
    try:
        while not result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise NotAuthenticated(f"No redirect received within {a.wait} s. Run `google.py auth` again.")
    if "error" in result:
        raise NotAuthenticated(f"Google authorisation failed: {result['error']}")
    form = {"grant_type": "authorization_code", "code": result["code"], "client_id": cid,
            "redirect_uri": redirect_uri, "code_verifier": verifier}
    if secret:
        form["client_secret"] = secret
    _, tok = http_json("POST", TOKEN_URL, form=form, timeout=30, retries=2)
    if not tok.get("refresh_token"):
        raise NotAuthenticated("Google returned no refresh token. Remove the app at https://myaccount.google.com/permissions "
                               "and run `google.py auth` again.")
    granted = set((tok.get("scope") or "").split())
    missing = [s for s in (SCOPE_GSC, SCOPE_GA4) if granted and s not in granted]
    email = decode_id_token_email(tok.get("id_token"))
    saved = {"refresh_token": tok["refresh_token"], "client_id": cid, "client_secret": secret, "scopes": sorted(granted),
             "account_email": email, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    write_private(token_path(), json.dumps(saved, indent=2))
    findings = [{"severity": "high", "code": "scope_not_granted", "message": f"Scope not granted: {s} (re-run auth and tick it)"}
                for s in missing]
    return {"command": "auth", "account_email": email, "token_file": str(token_path()), "scopes": sorted(granted),
            "findings": findings}


# --------------------------------------------------------------------------- helpers
def host_of(s: str) -> str:
    if s.startswith("sc-domain:"):
        return s.split(":", 1)[1]
    return urlparse(s if "://" in s else "https://" + s).hostname or "site"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def sort_findings(f: list[dict]) -> list[dict]:
    return sorted(f, key=lambda x: ORDER.get(x["severity"], 9))


def md_table(headers: list[str], rows: list[list]) -> list[str]:
    def cell(v):
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return out


def md_findings(findings: list[dict]) -> list[str]:
    if not findings:
        return ["", "No findings."]
    return ["", "## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in sort_findings(findings)]


def emit(a, name: str, host: str, data: dict, md: str) -> None:
    data.setdefault("findings", [])
    data["findings"] = sort_findings(data["findings"])
    print(json.dumps(data, indent=2, ensure_ascii=False) if a.json else md)
    if not a.no_write:
        out = Path(".seo") / "audits"
        out.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9.-]+", "_", host)[:80] or "site"
        base = out / f"google-{name}-{safe}-{stamp()}"
        (base.parent / (base.name + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        (base.parent / (base.name + ".md")).write_text(md, encoding="utf-8")
        print(f"[saved] {base}.json / .md", file=sys.stderr)


def rate(metric: str, value) -> str | None:
    if value is None or metric not in THRESHOLDS:
        return None
    good, poor = THRESHOLDS[metric]
    return "good" if value <= good else ("needs improvement" if value <= poor else "poor")


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- sites / gsc
def cmd_sites(a) -> tuple[dict, str]:
    r = gapi("GET", f"{WM}/sites")
    sites = [{"siteUrl": s.get("siteUrl"), "permissionLevel": s.get("permissionLevel")} for s in r.get("siteEntry", [])]
    findings = [] if sites else [{"severity": "info", "code": "no_properties",
                                  "message": "This account has no Search Console properties (add the service-account email as a user)."}]
    md = "\n".join(["# Search Console properties", ""] + md_table(["Property", "Permission"],
                                                                 [[s["siteUrl"], s["permissionLevel"]] for s in sites]) + md_findings(findings))
    return {"command": "sites", "sites": sites, "findings": findings}, md


def date_range(a) -> tuple[str, str]:
    end = date.fromisoformat(a.end) if a.end else date.today() - timedelta(days=2)  # GSC lags ~2-3 days
    start = date.fromisoformat(a.start) if a.start else end - timedelta(days=a.days - 1)
    if start > end:
        raise ValueError("--start is after --end")
    return start.isoformat(), end.isoformat()


def gsc_query(prop: str, start: str, end: str, dims: list[str], search_type: str, data_state: str, max_rows: int) -> list[dict]:
    url = f"{WM}/sites/{quote(prop, safe='')}/searchAnalytics/query"
    rows, start_row = [], 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": dims, "type": search_type, "dataState": data_state,
                "rowLimit": GSC_PAGE_SIZE, "startRow": start_row}
        batch = gapi("POST", url, body).get("rows") or []
        for r in batch:
            row = dict(zip(dims, r.get("keys", [])))
            row.update(clicks=r.get("clicks", 0), impressions=r.get("impressions", 0), ctr=r.get("ctr", 0),
                       position=r.get("position", 0))
            rows.append(row)
        if len(batch) < GSC_PAGE_SIZE or len(rows) >= max_rows:
            return rows[:max_rows]
        start_row += GSC_PAGE_SIZE


def write_gsc_csv(rows: list[dict], dims: list[str], out_dir: Path, st: str) -> Path:
    """CSV in the exact formats scripts/gsc_report.py reads."""
    ds = set(dims)
    if ds == {"query"} or ds == {"page"}:
        key = "query" if "query" in ds else "page"
        path = out_dir / (f"queries-{st}.csv" if key == "query" else f"pages-{st}.csv")
        header = ["Top queries" if key == "query" else "Top pages", "Clicks", "Impressions", "CTR", "Position"]
        lines = [[r[key], int(r["clicks"]), int(r["impressions"]), f"{100 * r['ctr']:.2f}%", round(r["position"], 2)] for r in rows]
    elif ds == {"page", "query"}:
        path = out_dir / f"page_query-{st}.csv"
        header = ["page", "query", "clicks", "impressions", "ctr", "position"]
        lines = [[r["page"], r["query"], int(r["clicks"]), int(r["impressions"]), round(r["ctr"], 4), round(r["position"], 2)]
                 for r in rows]
    else:  # other combinations go to raw/ so gsc_report.py (top-level *.csv only) does not misread them
        path = out_dir / "raw" / f"gsc-{'-'.join(dims)}-{st}.csv"
        header = dims + ["clicks", "impressions", "ctr", "position"]
        lines = [[r.get(d) for d in dims] + [int(r["clicks"]), int(r["impressions"]), round(r["ctr"], 4), round(r["position"], 2)]
                 for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(lines)
    return path


def cmd_gsc(a) -> tuple[dict, str]:
    dims = [d.strip() for d in a.dimensions.split(",") if d.strip()]
    bad = [d for d in dims if d not in GSC_DIMENSIONS]
    if bad or not dims:
        raise ValueError(f"unknown dimension(s) {bad}; use {sorted(GSC_DIMENSIONS)}")
    start, end = date_range(a)
    rows = gsc_query(a.property, start, end, dims, a.type, "all" if a.fresh else "final", a.max_rows)
    clicks = sum(r["clicks"] for r in rows)
    impr = sum(r["impressions"] for r in rows)
    data = {"command": "gsc", "property": a.property, "start": start, "end": end, "dimensions": dims, "rows_total": len(rows),
            "clicks": clicks, "impressions": impr, "rows": rows, "findings": []}
    if not rows:
        data["findings"].append({"severity": "info", "code": "no_rows", "message": "No Search Analytics rows for this range."})
    if a.out:
        data["csv"] = str(write_gsc_csv(rows, dims, Path(a.out), stamp()))
    if len(rows) >= a.max_rows:
        data["findings"].append({"severity": "info", "code": "row_cap", "message": f"Stopped at --max-rows {a.max_rows}."})
    top = sorted(rows, key=lambda r: -r["impressions"])[:a.top]
    md = [f"# Search Console: {a.property}", "", f"- Range {start} to {end} ({a.type}, {'fresh' if a.fresh else 'final'} data)",
          f"- {len(rows):,} rows, {clicks:,.0f} clicks, {impr:,.0f} impressions (rows can be anonymised; totals by date alone are more complete)"]
    if data.get("csv"):
        md.append(f"- CSV: `{data['csv']}`" + (" (run `python scripts/gsc_report.py " + str(Path(a.out).as_posix()) + "`)"
                                               if Path(data['csv']).parent == Path(a.out) else ""))
    md += ["", f"## Top {len(top)} by impressions", ""]
    md += md_table(dims + ["Clicks", "Impressions", "CTR", "Position"],
                   [[r.get(d) for d in dims] + [int(r["clicks"]), int(r["impressions"]), f"{100 * r['ctr']:.1f}%",
                                                f"{r['position']:.1f}"] for r in top])
    md += md_findings(data["findings"])
    return data, "\n".join(md)


# --------------------------------------------------------------------------- inspect
def _quota_file() -> Path:
    return config_dir() / "inspect-quota.json"


def inspect_used_today(prop: str) -> int:
    try:
        q = json.loads(_quota_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return int(q.get(prop, {}).get(datetime.now(timezone.utc).date().isoformat(), 0))


def inspect_count(prop: str, n: int = 1) -> None:
    p = _quota_file()
    try:
        q = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        q = {}
    today = datetime.now(timezone.utc).date().isoformat()
    q[prop] = {today: int(q.get(prop, {}).get(today, 0)) + n}  # keep only today
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(q), encoding="utf-8")
    except OSError:
        pass


def _canon(u: str | None) -> str:
    return (u or "").strip().rstrip("/").lower()


def summarise_inspection(url: str, res: dict) -> tuple[dict, list[dict]]:
    ir = (res.get("inspectionResult") or {}).get("indexStatusResult") or {}
    row = {"url": url, "verdict": ir.get("verdict"), "coverageState": ir.get("coverageState"),
           "robotsTxtState": ir.get("robotsTxtState"), "indexingState": ir.get("indexingState"),
           "pageFetchState": ir.get("pageFetchState"), "lastCrawlTime": ir.get("lastCrawlTime"),
           "googleCanonical": ir.get("googleCanonical"), "userCanonical": ir.get("userCanonical")}
    f = []
    if row["verdict"] != "PASS":
        f.append({"severity": "high", "code": "not_indexed",
                  "message": f"{url}: not indexed - {row['coverageState'] or 'unknown state'} (verdict {row['verdict']})"})
    if row["googleCanonical"] and row["userCanonical"] and _canon(row["googleCanonical"]) != _canon(row["userCanonical"]):
        f.append({"severity": "medium", "code": "canonical_mismatch",
                  "message": f"{url}: Google chose {row['googleCanonical']} but the page declares {row['userCanonical']}"})
    if row["robotsTxtState"] == "DISALLOWED":
        f.append({"severity": "high", "code": "blocked_by_robots", "message": f"{url}: blocked by robots.txt"})
    if row["indexingState"] and row["indexingState"] not in ("INDEXING_ALLOWED", "INDEXING_STATE_UNSPECIFIED"):
        f.append({"severity": "high", "code": "noindex", "message": f"{url}: {row['indexingState']}"})
    for x in f:
        x["url"] = url
    return row, f


def cmd_inspect(a) -> tuple[dict, str]:
    urls = list(a.urls)
    findings: list[dict] = []
    if a.from_sitemap:
        sys.path.insert(0, str(ROOT / "scripts"))
        import crawl_audit  # noqa: E402
        crawl_audit.set_network_mode(urlparse(a.from_sitemap).hostname)
        sm_urls, problems = crawl_audit.read_sitemap_urls(a.from_sitemap, crawl_audit.UA_DEFAULT, limit=max(a.limit, 1))
        urls += sm_urls
        findings += [{"severity": "low", "code": "sitemap_problem", "message": p} for p in problems]
    urls = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    if not urls:
        raise ValueError("give URLs or --from-sitemap")
    urls = urls[:a.limit]
    remaining = INSPECT_DAILY_QUOTA - inspect_used_today(a.property)
    if remaining <= 0:
        raise ApiError(429, f"Local URL Inspection counter says the 2,000/day quota for {a.property} is used up today (UTC).")
    if len(urls) > remaining:
        findings.append({"severity": "info", "code": "quota_trimmed",
                         "message": f"Inspecting {remaining} of {len(urls)} URLs (2,000/day per property quota)."})
        urls = urls[:remaining]
    access_token()  # fail fast before touching the quota counter
    rows = []
    for i, u in enumerate(urls):
        if i:
            _sleep(0.11)  # stay under 600/min
        try:
            res = gapi("POST", INSPECT_URL, {"inspectionUrl": u, "siteUrl": a.property, "languageCode": "en-US"})
        except ApiError as e:
            rows.append({"url": u, "error": str(e)})
            if e.status == 429:
                findings.append({"severity": "info", "code": "quota_exhausted", "message": f"Stopped at {u}: {e}"})
                break
            continue
        finally:
            inspect_count(a.property)
        row, f = summarise_inspection(u, res)
        rows.append(row)
        findings += f
    data = {"command": "inspect", "property": a.property, "inspected": len(rows), "results": rows, "findings": findings}
    md = [f"# URL Inspection: {a.property}", "", f"- {len(rows)} URLs inspected", ""]
    md += md_table(["URL", "Verdict", "Coverage", "Robots", "Last crawl", "Google canonical", "Declared canonical"],
                   [[r["url"], r.get("verdict") or r.get("error"), r.get("coverageState"), r.get("robotsTxtState"),
                     r.get("lastCrawlTime"), r.get("googleCanonical"), r.get("userCanonical")] for r in rows])
    md += md_findings(findings)
    return data, "\n".join(md)


# --------------------------------------------------------------------------- sitemaps
def cmd_sitemaps(a) -> tuple[dict, str]:
    r = gapi("GET", f"{WM}/sites/{quote(a.property, safe='')}/sitemaps")
    items, findings = [], []
    for s in r.get("sitemap", []):
        contents = s.get("contents") or []
        it = {"path": s.get("path"), "lastSubmitted": s.get("lastSubmitted"), "lastDownloaded": s.get("lastDownloaded"),
              "isPending": s.get("isPending"), "isSitemapsIndex": s.get("isSitemapsIndex"),
              "errors": int(s.get("errors") or 0), "warnings": int(s.get("warnings") or 0),
              "submitted": sum(int(c.get("submitted") or 0) for c in contents)}
        items.append(it)
        if it["errors"]:
            findings.append({"severity": "high", "code": "sitemap_errors", "message": f"{it['path']}: {it['errors']} error(s)"})
        if it["warnings"]:
            findings.append({"severity": "medium", "code": "sitemap_warnings", "message": f"{it['path']}: {it['warnings']} warning(s)"})
        if not it["lastDownloaded"]:
            findings.append({"severity": "low", "code": "sitemap_not_downloaded", "message": f"{it['path']}: Google has not downloaded it yet"})
    if not items:
        findings.append({"severity": "medium", "code": "no_sitemaps", "message": "No sitemap submitted for this property."})
    md = [f"# Sitemaps: {a.property}", ""] + md_table(
        ["Sitemap", "Last submitted", "Last downloaded", "Pending", "URLs submitted", "Errors", "Warnings"],
        [[i["path"], i["lastSubmitted"], i["lastDownloaded"], i["isPending"], i["submitted"], i["errors"], i["warnings"]] for i in items])
    md += md_findings(findings)
    return {"command": "sitemaps", "property": a.property, "sitemaps": items, "findings": findings}, "\n".join(md)


# --------------------------------------------------------------------------- ga4
def ga4_rows(resp: dict) -> list[dict]:
    dims = [h["name"] for h in resp.get("dimensionHeaders", [])]
    mets = [h["name"] for h in resp.get("metricHeaders", [])]
    out = []
    for r in resp.get("rows", []):
        row = {d: v.get("value") for d, v in zip(dims, r.get("dimensionValues", []))}
        row.update({m: to_float(v.get("value")) or 0 for m, v in zip(mets, r.get("metricValues", []))})
        out.append(row)
    return out


def cmd_ga4(a) -> tuple[dict, str]:
    pid = a.property_id if a.property_id.startswith("properties/") else f"properties/{a.property_id}"
    url = GA4_URL.format(prop=pid)
    rng = [{"startDate": f"{a.days}daysAgo", "endDate": "yesterday"}]
    metrics = [{"name": "sessions"}, {"name": "engagedSessions"}, {"name": "keyEvents"}]
    organic = ga4_rows(gapi("POST", url, {
        "dateRanges": rng, "dimensions": [{"name": "landingPagePlusQueryString"}], "metrics": metrics,
        "dimensionFilter": {"filter": {"fieldName": "sessionDefaultChannelGroup",
                                       "stringFilter": {"matchType": "EXACT", "value": "Organic Search"}}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}], "limit": a.limit}))
    ai = ga4_rows(gapi("POST", url, {
        "dateRanges": rng, "dimensions": [{"name": "sessionSource"}, {"name": "landingPagePlusQueryString"}], "metrics": metrics,
        "dimensionFilter": {"filter": {"fieldName": "sessionSource",
                                       "stringFilter": {"matchType": "PARTIAL_REGEXP", "value": AI_SOURCES}}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}], "limit": 1000}))
    by_source: dict[str, dict] = {}
    for r in ai:
        s = by_source.setdefault(r["sessionSource"], {"source": r["sessionSource"], "sessions": 0, "engagedSessions": 0, "keyEvents": 0})
        for m in ("sessions", "engagedSessions", "keyEvents"):
            s[m] += r.get(m, 0)
    sources = sorted(by_source.values(), key=lambda s: -s["sessions"])
    ai_total = sum(s["sessions"] for s in sources)
    findings = []
    if not organic:
        findings.append({"severity": "medium", "code": "no_organic_sessions", "message": f"No Organic Search sessions in the last {a.days} days."})
    if not ai_total:
        findings.append({"severity": "info", "code": "no_ai_referrals",
                         "message": "No sessions with an AI-assistant referrer (many AI visits arrive with no referrer and count as Direct)."})
    data = {"command": "ga4", "property": pid, "days": a.days, "organic_landing_pages": organic,
            "ai_referrals": {"note": "floor, not total: many AI-assistant visits arrive without a referrer",
                             "total_sessions": ai_total, "by_source": sources, "by_source_landing_page": ai[:50]},
            "findings": findings}
    md = [f"# GA4: {pid} (last {a.days} days)", "", "## Organic Search landing pages", ""]
    md += md_table(["Landing page", "Sessions", "Engaged", "Key events"],
                   [[r["landingPagePlusQueryString"], int(r["sessions"]), int(r["engagedSessions"]), int(r["keyEvents"])] for r in organic[:a.top]])
    md += ["", f"## AI-assistant referrals: {ai_total:,.0f} sessions (a floor: many AI visits have no referrer)", ""]
    md += md_table(["Source", "Sessions", "Engaged", "Key events"],
                   [[s["source"], int(s["sessions"]), int(s["engagedSessions"]), int(s["keyEvents"])] for s in sources])
    if ai:
        md += ["", "### Pages AI assistants send visitors to", ""]
        md += md_table(["Source", "Landing page", "Sessions"], [[r["sessionSource"], r["landingPagePlusQueryString"], int(r["sessions"])] for r in ai[:a.top]])
    md += md_findings(findings)
    return data, "\n".join(md)


# --------------------------------------------------------------------------- psi
LAB_METRICS = [("FCP", "first-contentful-paint"), ("LCP", "largest-contentful-paint"), ("TBT", "total-blocking-time"),
               ("CLS", "cumulative-layout-shift"), ("Speed Index", "speed-index")]
FIELD_KEYS = [("LARGEST_CONTENTFUL_PAINT_MS", "largest_contentful_paint"), ("INTERACTION_TO_NEXT_PAINT", "interaction_to_next_paint"),
              ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "cumulative_layout_shift")]


def parse_psi(resp: dict) -> dict:
    lh = resp.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}
    score = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    failed = []
    for aid, au in audits.items():
        kind = "insight" if aid.endswith("-insight") else ("opportunity" if (au.get("details") or {}).get("type") == "opportunity" else None)
        if not kind:
            continue
        sc, mode = au.get("score"), au.get("scoreDisplayMode")
        if sc is None or mode in ("notApplicable", "manual", "informative", "error") or sc >= 0.9:
            continue
        failed.append({"id": aid, "kind": kind, "title": au.get("title"), "displayValue": au.get("displayValue"), "score": sc})
    failed.sort(key=lambda x: (x["score"], x["id"]))
    field = {}
    for src, scope in (("loadingExperience", "url"), ("originLoadingExperience", "origin")):
        m = (resp.get(src) or {}).get("metrics") or {}
        for k, metric in FIELD_KEYS:
            if k in m and metric not in field:
                p75 = to_float(m[k].get("percentile"))
                if metric == "cumulative_layout_shift" and p75 is not None:
                    p75 = p75 / 100  # PSI reports CLS x100
                field[metric] = {"p75": p75, "rating": rate(metric, p75), "scope": scope}
    return {"score": round(score * 100) if score is not None else None,
            "lab": {name: (audits.get(aid) or {}).get("displayValue") for name, aid in LAB_METRICS},
            "field": field, "failed_audits": failed,
            "lighthouse_version": lh.get("lighthouseVersion")}


def cmd_psi(a) -> tuple[dict, str]:
    key = os.environ.get("PSI_API_KEY")
    strategies = ["mobile", "desktop"] if a.strategy == "both" else [a.strategy]
    results, findings = {}, []
    for st in strategies:
        q = [("url", a.url), ("strategy", st), ("category", "performance")]
        if key:
            q.append(("key", key))
        try:
            _, resp = http_json("GET", PSI_URL + "?" + urlencode(q), timeout=150, retries=3)
        except ApiError as e:
            hint = " - set PSI_API_KEY (free: Google Cloud console > enable PageSpeed Insights API > create API key)" \
                if e.status == 429 and not key else ""
            results[st] = {"error": str(e) + hint}
            findings.append({"severity": "info", "code": "psi_error", "message": f"{st}: {e}{hint}"})
            continue
        r = parse_psi(resp)
        results[st] = r
        if r["score"] is not None and r["score"] < 90:
            findings.append({"severity": "high" if r["score"] < 50 else "medium", "code": f"psi_score_{st}",
                             "message": f"{st} Lighthouse performance score {r['score']}/100"})
        for metric, v in r["field"].items():
            if v["rating"] in ("poor", "needs improvement"):
                findings.append({"severity": "high" if v["rating"] == "poor" else "medium",
                                 "code": f"cwv_{CWV_LABEL[metric].lower()}_{'poor' if v['rating'] == 'poor' else 'ni'}",
                                 "message": f"{st} field {CWV_LABEL[metric]} p75 {v['p75']} ({v['rating']}, {v['scope']} data)"})
        for fa in r["failed_audits"]:
            findings.append({"severity": "medium" if fa["score"] < 0.5 else "low", "code": f"lh_{fa['id'].replace('-', '_')}",
                             "message": f"{st}: {fa['title']}" + (f" ({fa['displayValue']})" if fa.get("displayValue") else "")})
    md = [f"# PageSpeed Insights: {a.url}", ""]
    for st, r in results.items():
        md += [f"## {st.title()}", ""]
        if "error" in r:
            md += [f"Error: {r['error']}", ""]
            continue
        md.append(f"- Performance score: **{r['score']}** (Lighthouse {r.get('lighthouse_version') or '?'})")
        md.append("- Lab: " + ", ".join(f"{k} {v}" for k, v in r["lab"].items() if v))
        if r["field"]:
            md.append("- Field (p75): " + ", ".join(f"{CWV_LABEL[m]} {v['p75']} {v['rating']} ({v['scope']})" for m, v in r["field"].items()))
        else:
            md.append("- Field: no CrUX data for this URL/origin")
        md += ["", "Failed insights / opportunities:", ""]
        md += md_table(["Audit", "Kind", "Score", "Value"],
                       [[f["title"], f["kind"], f["score"], f.get("displayValue")] for f in r["failed_audits"]]) if r["failed_audits"] else ["- none"]
        md.append("")
    md += md_findings(findings)
    return {"command": "psi", "url": a.url, "results": results, "findings": findings}, "\n".join(md)


# --------------------------------------------------------------------------- crux
def crux_key() -> str:
    k = os.environ.get("SUPER_SEO_CRUX_KEY") or os.environ.get("PSI_API_KEY")
    if not k:
        raise NotAuthenticated("CrUX needs an API key: set PSI_API_KEY or SUPER_SEO_CRUX_KEY (free: Google Cloud console > "
                               "enable Chrome UX Report API > Credentials > Create API key).")
    return k


def crux_post(url: str, body: dict) -> dict | None:
    """Returns the record, or None on 404 (not enough real-user traffic)."""
    try:
        return http_json("POST", url + "?" + urlencode({"key": crux_key()}), body=body, timeout=60)[1].get("record") or {}
    except ApiError as e:
        if e.status == 404:
            return None
        raise


def crux_targets(a) -> list[tuple[str, str]]:
    t = ([("origin", a.origin.rstrip("/"))] if a.origin else []) + ([("url", a.url)] if a.url else [])
    if not t:
        raise ValueError("give --origin and/or --url")
    return t


def crux_body(kind: str, target: str, form_factor: str, metrics: list[str]) -> dict:
    b = {kind: target, "metrics": metrics}
    if form_factor != "ALL":
        b["formFactor"] = form_factor
    return b


def _period(p: dict | None) -> str | None:
    if not p:
        return None
    try:
        return f"{p['firstDate']['year']:04d}-{p['firstDate']['month']:02d}-{p['firstDate']['day']:02d} to " \
               f"{p['lastDate']['year']:04d}-{p['lastDate']['month']:02d}-{p['lastDate']['day']:02d}"
    except (KeyError, TypeError):
        return None


def cmd_crux(a) -> tuple[dict, str]:
    metrics = list(THRESHOLDS)
    out, findings, md = [], [], [f"# CrUX field data ({a.form_factor}, p75, last 28 days)", ""]
    for kind, target in crux_targets(a):
        rec = crux_post(CRUX_URL, crux_body(kind, target, a.form_factor, metrics))
        if rec is None:
            out.append({"scope": kind, "target": target, "data": None})
            findings.append({"severity": "info", "code": "no_field_data",
                             "message": f"{target}: no field data (not enough Chrome traffic); use `google.py psi` lab data"})
            md += [f"## {kind}: {target}", "", "No field data (CrUX returned 404).", ""]
            continue
        vals = {}
        for m in metrics:
            p75 = to_float(((rec.get("metrics") or {}).get(m) or {}).get("percentiles", {}).get("p75"))
            vals[m] = {"p75": p75, "rating": rate(m, p75)}
            if vals[m]["rating"] in ("poor", "needs improvement"):
                findings.append({"severity": "high" if vals[m]["rating"] == "poor" else "medium",
                                 "code": f"cwv_{CWV_LABEL[m].lower()}_{'poor' if vals[m]['rating'] == 'poor' else 'ni'}",
                                 "message": f"{target}: {CWV_LABEL[m]} p75 {p75} ({vals[m]['rating']})"})
        period = _period(rec.get("collectionPeriod"))
        out.append({"scope": kind, "target": target, "period": period, "data": vals})
        md += [f"## {kind}: {target}", "", f"Collection period: {period or '?'}", ""]
        md += md_table(["Metric", "p75", "Rating", "Good / poor threshold"],
                       [[CWV_LABEL[m], v["p75"], v["rating"] or "no data", f"{THRESHOLDS[m][0]} / {THRESHOLDS[m][1]}"] for m, v in vals.items()])
        md.append("")
    md += md_findings(findings)
    return {"command": "crux", "form_factor": a.form_factor, "results": out, "findings": findings}, "\n".join(md)


def analyse_series(metric: str, series: list) -> dict:
    vals = [to_float(v) for v in series]
    clean = [v for v in vals if v is not None]
    if len(clean) < 2:
        return {"points": len(clean), "trend": "insufficient data"}
    baseline = statistics.median(clean[:4])
    latest = clean[-1]
    change = (latest - baseline) / baseline if baseline else 0.0
    trend = "regressing" if change > 0.20 else ("improving" if change < -0.20 else "stable")
    # week-over-week jumps > 20 %
    jumps = [i for i in range(1, len(vals)) if vals[i] is not None and vals[i - 1] not in (None, 0) and
             (vals[i] - vals[i - 1]) / vals[i - 1] > 0.20]
    return {"points": len(clean), "baseline": baseline, "latest": latest, "min": min(clean), "max": max(clean),
            "change_pct": round(100 * change, 1), "trend": trend, "rating": rate(metric, latest), "jump_weeks": jumps}


def cmd_crux_history(a) -> tuple[dict, str]:
    metrics = list(THRESHOLDS)
    out, findings = [], []
    md = [f"# CrUX history ({a.form_factor}, p75, {a.weeks} weekly 28-day windows)", ""]
    for kind, target in crux_targets(a):
        body = crux_body(kind, target, a.form_factor, metrics)
        body["collectionPeriodCount"] = a.weeks
        rec = crux_post(CRUX_HIST_URL, body)
        if rec is None:
            out.append({"scope": kind, "target": target, "data": None})
            findings.append({"severity": "info", "code": "no_field_data", "message": f"{target}: no CrUX history (not enough traffic)"})
            md += [f"## {kind}: {target}", "", "No field data.", ""]
            continue
        periods = rec.get("collectionPeriods") or []
        res = {}
        for m in metrics:
            series = (((rec.get("metrics") or {}).get(m) or {}).get("percentilesTimeseries") or {}).get("p75s") or []
            an = analyse_series(m, series)
            an["series"] = series
            res[m] = an
            if an.get("trend") == "regressing":
                findings.append({"severity": "high" if an.get("rating") == "poor" else "medium", "code": f"cwv_{CWV_LABEL[m].lower()}_regression",
                                 "message": f"{target}: {CWV_LABEL[m]} p75 rose {an['change_pct']}% (baseline {an['baseline']:g} -> latest {an['latest']:g}, {an['rating']})"})
        out.append({"scope": kind, "target": target, "first_period": _period(periods[0]) if periods else None,
                    "last_period": _period(periods[-1]) if periods else None, "data": res})
        md += [f"## {kind}: {target}", "", f"From {_period(periods[0]) if periods else '?'} to {_period(periods[-1]) if periods else '?'}", ""]
        md += md_table(["Metric", "Baseline (first 4 wk)", "Latest", "Change", "Trend", "Rating", "Min / max"],
                       [[CWV_LABEL[m], f"{v['baseline']:g}" if "baseline" in v else "-", f"{v['latest']:g}" if "latest" in v else "-",
                         f"{v['change_pct']}%" if "change_pct" in v else "-", v["trend"], v.get("rating") or "-",
                         f"{v['min']:g} / {v['max']:g}" if "min" in v else "-"] for m, v in res.items()])
        md.append("")
    md += md_findings(findings)
    return {"command": "crux-history", "form_factor": a.form_factor, "results": out, "findings": findings}, "\n".join(md)


def cmd_lcp_subparts(a) -> tuple[dict, str]:
    metrics = ["largest_contentful_paint"] + list(LCP_SUBPARTS)
    out, findings = [], []
    md = [f"# LCP subparts ({a.form_factor}, p75, image LCPs only)", "",
          "p75 of each subpart is computed separately, so they do not add up exactly to LCP p75.", ""]
    for kind, target in crux_targets(a):
        rec = crux_post(CRUX_URL, crux_body(kind, target, a.form_factor, metrics))
        if rec is None:
            out.append({"scope": kind, "target": target, "data": None})
            findings.append({"severity": "info", "code": "no_field_data", "message": f"{target}: no CrUX field data"})
            md += [f"## {kind}: {target}", "", "No field data.", ""]
            continue
        mm = rec.get("metrics") or {}
        lcp = to_float((mm.get("largest_contentful_paint") or {}).get("percentiles", {}).get("p75"))
        parts = {}
        for m, (label, hint) in LCP_SUBPARTS.items():
            p75 = to_float((mm.get(m) or {}).get("percentiles", {}).get("p75"))
            share = round(p75 / lcp, 3) if (p75 is not None and lcp) else None
            parts[m] = {"label": label, "p75": p75, "share": share}
            if share is not None and share >= 0.40:
                findings.append({"severity": "high" if rate("largest_contentful_paint", lcp) == "poor" else "medium",
                                 "code": "lcp_subpart_" + m.removeprefix("largest_contentful_paint_image_"),
                                 "message": f"{target}: {label} is {round(100 * share)}% of LCP p75 ({p75:g} of {lcp:g} ms). Fix: {hint}"})
        if all(v["p75"] is None for v in parts.values()):
            findings.append({"severity": "info", "code": "no_lcp_subparts",
                             "message": f"{target}: no image-LCP subpart data (LCP element may be text, or too little traffic)"})
        out.append({"scope": kind, "target": target, "lcp_p75": lcp, "lcp_rating": rate("largest_contentful_paint", lcp), "subparts": parts})
        md += [f"## {kind}: {target}", "", f"LCP p75: {lcp} ms ({rate('largest_contentful_paint', lcp) or 'no data'})", ""]
        md += md_table(["Subpart", "p75 (ms)", "Share of LCP"],
                       [[v["label"], v["p75"], f"{round(100 * v['share'])}%" if v["share"] is not None else "-"] for v in parts.values()])
        md.append("")
    md += md_findings(findings)
    return {"command": "lcp-subparts", "form_factor": a.form_factor, "results": out, "findings": findings}, "\n".join(md)


# --------------------------------------------------------------------------- cli
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    common.add_argument("--no-write", action="store_true", help="do not write .seo/audits/ files")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("auth", parents=[common], help="OAuth loopback sign-in (Desktop app client, PKCE)")
    p.add_argument("--no-browser", action="store_true", help="print the URL only")
    p.add_argument("--wait", type=int, default=300, help="seconds to wait for the redirect")

    sub.add_parser("sites", parents=[common], help="list Search Console properties")

    p = sub.add_parser("gsc", parents=[common], help="Search Analytics query")
    p.add_argument("--property", required=True, help="sc-domain:example.com or https://example.com/")
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--dimensions", default="query", help="comma list of query,page,country,device,date,searchAppearance")
    p.add_argument("--type", default="web", choices=["web", "image", "video", "news", "discover", "googleNews"])
    p.add_argument("--fresh", action="store_true", help="include fresh (partial) data")
    p.add_argument("--max-rows", type=int, default=100000)
    p.add_argument("--top", type=int, default=25, help="rows shown in markdown")
    p.add_argument("--out", nargs="?", const=".seo/gsc", default=None,
                   help="write CSV for scripts/gsc_report.py (default folder .seo/gsc)")

    p = sub.add_parser("inspect", parents=[common], help="URL Inspection (2,000/day per property)")
    p.add_argument("--property", required=True)
    p.add_argument("urls", nargs="*")
    p.add_argument("--from-sitemap")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("sitemaps", parents=[common], help="submitted sitemaps, errors and warnings")
    p.add_argument("--property", required=True)

    p = sub.add_parser("ga4", parents=[common], help="organic landing pages + AI-assistant referrals")
    p.add_argument("--property-id", required=True)
    p.add_argument("--days", type=int, default=28)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--top", type=int, default=25)

    p = sub.add_parser("psi", parents=[common], help="PageSpeed Insights v5 (lab + field)")
    p.add_argument("url")
    p.add_argument("--strategy", default="both", choices=["mobile", "desktop", "both"])

    for name, hlp in (("crux", "CrUX p75 LCP/INP/CLS"), ("crux-history", "CrUX 25-week trend"),
                      ("lcp-subparts", "CrUX LCP image subparts")):
        p = sub.add_parser(name, parents=[common], help=hlp)
        p.add_argument("--origin")
        p.add_argument("--url")
        p.add_argument("--form-factor", default="PHONE", choices=["PHONE", "DESKTOP", "TABLET", "ALL"])
        if name == "crux-history":
            p.add_argument("--weeks", type=int, default=25)
    return ap


COMMANDS = {"sites": cmd_sites, "gsc": cmd_gsc, "inspect": cmd_inspect, "sitemaps": cmd_sitemaps, "ga4": cmd_ga4,
            "psi": cmd_psi, "crux": cmd_crux, "crux-history": cmd_crux_history, "lcp-subparts": cmd_lcp_subparts}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "auth":
            data = cmd_auth(a)
            md = f"Saved Google authorisation for {data['account_email'] or 'your account'} to {data['token_file']}\n" \
                 f"Scopes: {', '.join(data['scopes']) or '(not reported)'}" + "\n".join(md_findings(data["findings"]))
            print(json.dumps(data, indent=2) if a.json else md)
            return 0
        data, md = COMMANDS[a.cmd](a)
        target = getattr(a, "property", None) or getattr(a, "property_id", None) or getattr(a, "origin", None) or \
            getattr(a, "url", None) or "account"
        emit(a, a.cmd, host_of(target) if target != "account" else target, data, md)
        return 0
    except NotAuthenticated as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ApiError as e:
        print(f"error: Google API: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
