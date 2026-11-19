#!/usr/bin/env python3
"""transport_check.py - stdlib-only TLS + HTTP transport hygiene check for one site.

Checks:
  - TLS certificate (default ssl context, SNI): expired / not yet valid / hostname mismatch (critical), expires in
    < 14 days (high), untrusted chain (high), negotiated protocol below TLS 1.2 (medium)
  - http:// -> https:// redirect: present (high if missing), a single 301/308 hop (low if 302/307 or chained)
  - www vs apex consolidation: one should 301 to the other (medium if both serve 200)
  - security headers on the final HTML response: HSTS, CSP (report-only, 'unsafe-inline'), X-Content-Type-Options,
    clickjacking (X-Frame-Options or CSP frame-ancestors), Referrer-Policy, Permissions-Policy
  - cookies set on the HTML response without Secure / HttpOnly / SameSite (cookie NAMES only, values are never stored)
  - mixed content: http:// subresources in an https page (high for scripts/iframes/stylesheets, medium for media)

Security headers are trust/security signals, NOT ranking factors; they are reported at low/medium severity.
On a plain-http localhost/private URL the TLS, redirect and www checks are skipped (info note).

Network guard: when the start host is public, connections to private/internal addresses are refused (the peer
address is checked with getpeername() right after the TCP connect, the same rule as crawl_audit.fetch()).

Usage:
  python transport_check.py https://example.com
  python transport_check.py http://localhost:3000 --no-write
  python transport_check.py https://example.com --json --timeout 10

Outputs <out>/transport-<host>-<timestamp>.json and .md (default --out .seo/audits) unless --no-write.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib import error, request
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_audit as ca  # noqa: E402

SEVERITY_ORDER = ca.SEVERITY_ORDER
UA_DEFAULT = ca.UA_DEFAULT
HSTS_MIN = 31536000
EXPIRY_WARN_DAYS = 14
LEGACY_TLS = {"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"}
# OpenSSL X509 verify codes
V_NOT_YET_VALID, V_EXPIRED, V_HOSTNAME = 9, 10, 62


def add(findings, severity, code, message, **extra):
    findings.append({"severity": severity, "code": code, "message": message, **extra})


# --------------------------------------------------------------------------- TLS
def decode_der(der: bytes) -> dict:
    """Decode a DER certificate into the getpeercert()-style dict (used when verification failed)."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        return ssl._ssl._test_decode_cert(path)  # noqa: SLF001 - stdlib has no public DER decoder
    except Exception:
        return {}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def guarded_connect(host: str, port: int, timeout: float, connect=socket.create_connection):
    """TCP connect, then check the connected peer (defeats DNS rebinding). Raises ca.BlockedAddress."""
    reason = ca.url_allowed(f"https://{host}:{port}/")
    if reason:
        raise ca.BlockedAddress(reason)
    sock = connect((host, port), timeout)
    try:
        peer = sock.getpeername()[0]
    except OSError:
        peer = ""
    if ca.PUBLIC_ONLY["on"] and not ca.ip_is_public(peer):
        sock.close()
        raise ca.BlockedAddress(f"blocked private/internal address {peer} (audit started from a public host)")
    return sock, peer


def tls_probe(host: str, port: int = 443, timeout: float = 10.0, connect=socket.create_connection,
              make_context=ssl.create_default_context) -> dict:
    """Handshake with the default (verifying) context. On a verification failure, reconnect without verification
    only to read the certificate details. Returns a plain dict (no live objects)."""
    out = {"host": host, "port": port, "verified": False, "verify_code": None, "verify_message": None,
           "protocol": None, "cipher": None, "cert": {}, "error": None, "peer": None}
    try:
        sock, out["peer"] = guarded_connect(host, port, timeout, connect)
    except ca.BlockedAddress as e:
        out["error"] = str(e)
        out["blocked"] = True
        return out
    except OSError as e:
        out["error"] = f"connect failed: {e}"
        return out
    ctx = make_context()
    try:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            out["verified"] = True
            out["protocol"] = ss.version()
            c = ss.cipher()
            out["cipher"] = c[0] if c else None
            out["cert"] = ss.getpeercert() or {}
            return out
    except ssl.SSLCertVerificationError as e:
        sock.close()
        out["verify_code"] = getattr(e, "verify_code", None)
        out["verify_message"] = getattr(e, "verify_message", None) or str(e)
    except ssl.SSLError as e:
        sock.close()
        msg = str(e)
        out["error"] = f"TLS handshake failed: {msg}"
        if re.search(r"unsupported protocol|wrong version|protocol version|no protocols available", msg, re.I):
            out["legacy_protocol_suspected"] = True
        return out
    except OSError as e:
        sock.close()
        out["error"] = f"TLS handshake failed: {e}"
        return out
    # verification failed: read the certificate without verifying (details only; nothing else is sent)
    try:
        sock2, _ = guarded_connect(host, port, timeout, connect)
        ctx2 = make_context()
        ctx2.check_hostname = False
        ctx2.verify_mode = ssl.CERT_NONE
        with ctx2.wrap_socket(sock2, server_hostname=host) as ss:
            out["protocol"] = ss.version()
            c = ss.cipher()
            out["cipher"] = c[0] if c else None
            der = ss.getpeercert(binary_form=True)
            out["cert"] = decode_der(der) if der else {}
    except (OSError, ssl.SSLError, ca.BlockedAddress) as e:
        out["error"] = f"could not read certificate after verification failure: {e}"
    return out


def cert_names(cert: dict) -> list[str]:
    names = [v for k, v in cert.get("subjectAltName", ()) if k in ("DNS", "IP Address")]
    if not names:
        for rdn in cert.get("subject", ()):
            for k, v in rdn:
                if k == "commonName":
                    names.append(v)
    return names


def hostname_matches(host: str, names: list[str]) -> bool:
    h = host.lower().rstrip(".")
    for n in names:
        n = n.lower().rstrip(".")
        if n == h:
            return True
        if n.startswith("*.") and "." in h:
            if h.split(".", 1)[1] == n[2:]:
                return True
    return False


def analyze_tls(probe: dict, now: float | None = None) -> tuple[list[dict], dict]:
    now = time.time() if now is None else now
    f: list[dict] = []
    host = probe["host"]
    cert = probe.get("cert") or {}
    info = {"protocol": probe.get("protocol"), "cipher": probe.get("cipher"), "verified": probe.get("verified"),
            "issuer": None, "not_before": cert.get("notBefore"), "not_after": cert.get("notAfter"),
            "days_left": None, "names": cert_names(cert)[:20]}
    for rdn in cert.get("issuer", ()):
        for k, v in rdn:
            if k in ("organizationName", "commonName") and not info["issuer"]:
                info["issuer"] = v
    if probe.get("blocked"):
        add(f, "high", "tls_blocked", probe["error"])
        return f, info
    if probe.get("error") and not cert and probe.get("verify_code") is None:
        if probe.get("legacy_protocol_suspected"):
            add(f, "medium", "tls_legacy_protocol", f"TLS handshake failed with a protocol error ({probe['error']}); "
                "the server may only offer TLS < 1.2")
        else:
            add(f, "critical", "tls_unavailable", f"No usable TLS on {host}:{probe['port']}: {probe['error']}")
        return f, info

    code = probe.get("verify_code")
    if not probe.get("verified"):
        msg = probe.get("verify_message") or "verification failed"
        if code == V_EXPIRED:
            add(f, "critical", "cert_expired", f"Certificate has expired ({msg})")
        elif code == V_NOT_YET_VALID:
            add(f, "critical", "cert_not_yet_valid", f"Certificate is not yet valid ({msg})")
        elif code == V_HOSTNAME:
            add(f, "critical", "cert_hostname_mismatch", f"Certificate does not cover {host} ({msg})")
        else:
            add(f, "high", "cert_chain_untrusted", f"Certificate chain is not trusted by the default CA store "
                f"(code {code}: {msg}); missing intermediate or self-signed certificate")

    # date checks on whatever we could read (also catches problems hidden behind another verify error)
    try:
        if cert.get("notAfter"):
            exp = ssl.cert_time_to_seconds(cert["notAfter"])
            days = (exp - now) / 86400
            info["days_left"] = round(days, 1)
            if days < 0 and not any(x["code"] == "cert_expired" for x in f):
                add(f, "critical", "cert_expired", f"Certificate expired {abs(int(days))} days ago ({cert['notAfter']})")
            elif 0 <= days < EXPIRY_WARN_DAYS:
                add(f, "high", "cert_expiring_soon", f"Certificate expires in {days:.1f} days ({cert['notAfter']}); check auto-renewal")
        if cert.get("notBefore") and ssl.cert_time_to_seconds(cert["notBefore"]) > now \
                and not any(x["code"] == "cert_not_yet_valid" for x in f):
            add(f, "critical", "cert_not_yet_valid", f"Certificate is not valid before {cert['notBefore']}")
    except ValueError:
        pass
    names = cert_names(cert)
    if names and not hostname_matches(host, names) and not any(x["code"] == "cert_hostname_mismatch" for x in f) \
            and not re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        add(f, "critical", "cert_hostname_mismatch", f"Certificate names {names[:5]} do not cover {host}")

    proto = probe.get("protocol")
    if proto in LEGACY_TLS:
        add(f, "medium", "tls_legacy_protocol", f"Negotiated {proto}; require TLS 1.2+ (TLS 1.3 preferred)")
    if not f:
        add(f, "info", "tls_ok", f"Valid certificate ({info['days_left']} days left), {proto}, issuer {info['issuer'] or '?'}")
    return f, info


# --------------------------------------------------------------------------- redirects
def classify_http_redirect(res: dict) -> list[dict]:
    """res = crawl_audit.fetch() of the http:// root."""
    f: list[dict] = []
    chain = res.get("chain") or []
    if not chain:
        if res.get("status", 0) == 0:
            add(f, "low", "http_unreachable", f"http:// root did not answer ({res.get('error')}); visitors typing the bare "
                "domain may fail unless the host is on the HSTS preload list")
        else:
            add(f, "high", "no_https_redirect", f"http:// serves status {res['status']} without redirecting to https://")
        return f
    idx = next((i for i, h in enumerate(chain) if urlparse(h["to"]).scheme == "https"), None)
    if idx is None:
        add(f, "high", "no_https_redirect", "http:// redirects but never reaches https://",
            chain=[f"{h['status']} {h['to']}" for h in chain])
        return f
    temp = [h for h in chain if h["status"] in (302, 303, 307)]
    if temp:
        add(f, "low", "http_redirect_temporary", f"http->https uses {temp[0]['status']}; use 301 or 308 (permanent)")
    if len(chain) > 1:
        add(f, "low", "http_redirect_chained", f"{len(chain)} hops from http:// to the final URL; redirect straight to "
            "the canonical https URL in one hop", chain=[f"{h['status']} {h['to']}" for h in chain])
    if not f:
        add(f, "info", "http_to_https_ok", f"http:// -> https:// in one {chain[0]['status']} hop")
    return f


def classify_www(apex: str, apex_res: dict | None, www_res: dict | None) -> list[dict]:
    f: list[dict] = []
    www = "www." + apex

    def alive(r):
        return bool(r) and (r.get("status", 0) != 0 or r.get("chain"))

    a_ok, w_ok = alive(apex_res), alive(www_res)
    if not a_ok and not w_ok:
        add(f, "info", "www_apex_unchecked", "Neither apex nor www answered")
        return f
    if a_ok != w_ok:
        missing = www if not w_ok else apex
        add(f, "info", "www_single_variant", f"{missing} does not answer; only one host variant exists "
            "(fine, but links to the missing variant will fail)")
        return f

    def first_hop_to(r, target):
        ch = r.get("chain") or []
        return ch[0] if ch and (urlparse(ch[0]["to"]).hostname or "").lower() == target else None

    a2w, w2a = first_hop_to(apex_res, www), first_hop_to(www_res, apex)
    if not apex_res.get("chain") and not www_res.get("chain") and apex_res["status"] == 200 and www_res["status"] == 200:
        add(f, "medium", "www_apex_both_200", f"Both {apex} and {www} serve 200; 301 one to the other "
            "(canonical tags alone split signals and cache)")
        return f
    hop = a2w or w2a
    if hop:
        if hop["status"] in (302, 303, 307):
            add(f, "low", "www_apex_temporary", f"{hop['url']} -> {hop['to']} uses {hop['status']}; use 301/308")
        else:
            add(f, "info", "www_apex_ok", f"{hop['url']} -> {hop['to']} ({hop['status']})")
    else:
        add(f, "info", "www_apex_other", f"apex final {apex_res.get('final_url')} ({apex_res.get('status')}), "
            f"www final {www_res.get('final_url')} ({www_res.get('status')})")
    return f


TWO_PART_SUFFIX = re.compile(r"\.(co|com|org|net|ac|gov|edu|ne|or|go)\.[a-z]{2}$")


def is_apex_like(host: str) -> bool:
    """Heuristic without a public-suffix list: example.com or example.co.uk count as an apex."""
    dots = host.count(".")
    return dots == 1 or (dots == 2 and bool(TWO_PART_SUFFIX.search(host)))


# --------------------------------------------------------------------------- headers / cookies
def parse_hsts(v: str) -> dict:
    out = {"max_age": None, "include_subdomains": False, "preload": False}
    for part in v.split(";"):
        p = part.strip().lower()
        if p.startswith("max-age"):
            m = re.search(r"\d+", p)
            out["max_age"] = int(m.group(0)) if m else None
        elif p == "includesubdomains":
            out["include_subdomains"] = True
        elif p == "preload":
            out["preload"] = True
    return out


def csp_directives(csp: str) -> dict:
    out = {}
    for part in csp.split(";"):
        toks = part.strip().split()
        if toks:
            out.setdefault(toks[0].lower(), [t.lower() for t in toks[1:]])
    return out


def check_headers(headers: dict, https: bool) -> list[dict]:
    """headers: lowercase name -> value (single values)."""
    f: list[dict] = []
    h = {k.lower(): v for k, v in headers.items()}
    if https:
        hsts = h.get("strict-transport-security")
        if not hsts:
            add(f, "medium", "hsts_missing", "No Strict-Transport-Security header; add max-age=31536000; includeSubDomains")
        else:
            p = parse_hsts(hsts)
            if not p["max_age"] or p["max_age"] < HSTS_MIN:
                add(f, "low", "hsts_short", f"HSTS max-age={p['max_age']} (< 31536000)")
            if not p["include_subdomains"]:
                add(f, "low", "hsts_no_subdomains", "HSTS without includeSubDomains")
            if (p["max_age"] or 0) >= HSTS_MIN and p["include_subdomains"]:
                if p["preload"]:
                    add(f, "info", "hsts_preload_ready", "HSTS is preload-ready (max-age >= 1 year, includeSubDomains, preload)")
                else:
                    add(f, "info", "hsts_ok", "HSTS ok; add 'preload' and submit to hstspreload.org if every subdomain is https")
    else:
        add(f, "info", "hsts_not_applicable", "Plain http response: HSTS only applies over https")

    csp = h.get("content-security-policy")
    csp_ro = h.get("content-security-policy-report-only")
    d = csp_directives(csp) if csp else {}
    if not csp and csp_ro:
        add(f, "low", "csp_report_only", "CSP is report-only (not enforced)")
    elif not csp:
        add(f, "low", "csp_missing", "No Content-Security-Policy header")
    else:
        src = d.get("script-src", d.get("default-src", []))
        if "'unsafe-inline'" in src and not any(t.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-", "'strict-dynamic'")) for t in src):
            add(f, "low", "csp_unsafe_inline", "CSP script-src allows 'unsafe-inline' without nonces/hashes (weak XSS protection)")
    if (h.get("x-content-type-options") or "").strip().lower() != "nosniff":
        add(f, "low", "xcto_missing", "X-Content-Type-Options: nosniff missing")
    xfo = (h.get("x-frame-options") or "").strip().upper()
    if xfo not in ("DENY", "SAMEORIGIN") and "frame-ancestors" not in d:
        add(f, "low", "clickjacking_unprotected", "No X-Frame-Options (DENY/SAMEORIGIN) or CSP frame-ancestors")
    rp = (h.get("referrer-policy") or "").strip().lower()
    last = rp.split(",")[-1].strip() if rp else ""
    if not rp:
        add(f, "low", "referrer_policy_missing", "No Referrer-Policy (browsers default to strict-origin-when-cross-origin; set it explicitly)")
    elif last == "unsafe-url":
        add(f, "low", "referrer_policy_unsafe", "Referrer-Policy: unsafe-url leaks full URLs (incl. query strings) to other sites")
    if not h.get("permissions-policy"):
        add(f, "info", "permissions_policy_missing", "No Permissions-Policy header")
    return f


def check_cookies(set_cookies: list[str], https: bool) -> tuple[list[dict], list[dict]]:
    """Return (findings, per-cookie attribute table). Only cookie NAMES are kept; values are discarded."""
    f: list[dict] = []
    rows = []
    for sc in set_cookies:
        parts = [p.strip() for p in sc.split(";")]
        name = parts[0].split("=", 1)[0].strip() if parts and parts[0] else "?"
        attrs = {p.split("=", 1)[0].strip().lower(): (p.split("=", 1)[1].strip().lower() if "=" in p else True) for p in parts[1:] if p}
        rows.append({"name": name[:80], "secure": "secure" in attrs, "httponly": "httponly" in attrs,
                     "samesite": attrs.get("samesite") if isinstance(attrs.get("samesite"), str) else None})
    if not rows:
        return f, rows
    no_secure = [r["name"] for r in rows if not r["secure"]]
    no_http = [r["name"] for r in rows if not r["httponly"]]
    no_ss = [r["name"] for r in rows if not r["samesite"]]
    none_insecure = [r["name"] for r in rows if r["samesite"] == "none" and not r["secure"]]
    if https and no_secure:
        add(f, "low", "cookie_no_secure", f"{len(no_secure)} cookie(s) without Secure", cookies=no_secure[:20])
    if no_http:
        add(f, "low", "cookie_no_httponly", f"{len(no_http)} cookie(s) without HttpOnly (fine only if JS must read them)", cookies=no_http[:20])
    if no_ss:
        add(f, "low", "cookie_no_samesite", f"{len(no_ss)} cookie(s) without SameSite", cookies=no_ss[:20])
    if none_insecure:
        add(f, "low", "cookie_samesite_none_insecure", "SameSite=None without Secure is rejected by browsers", cookies=none_insecure[:20])
    return f, rows


# --------------------------------------------------------------------------- mixed content
class _ResParser(HTMLParser):
    ACTIVE = {"script": "src", "iframe": "src", "frame": "src", "embed": "src", "object": "data"}
    PASSIVE = {"img": "src", "audio": "src", "video": "src", "source": "src", "track": "src"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active: list[tuple[str, str]] = []
        self.passive: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in self.ACTIVE and a.get(self.ACTIVE[tag]):
            self.active.append((tag, a[self.ACTIVE[tag]].strip()))
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if a.get("href") and ("stylesheet" in rel or ("preload" in rel and a.get("as", "").lower() in ("script", "style"))
                                  or "modulepreload" in rel):
                self.active.append(("link", a["href"].strip()))
            elif a.get("href") and "icon" in rel:
                self.passive.append(("link", a["href"].strip()))
        elif tag in self.PASSIVE:
            if a.get(self.PASSIVE[tag]):
                self.passive.append((tag, a[self.PASSIVE[tag]].strip()))
            for cand in a.get("srcset", "").split(","):
                u = cand.strip().split(" ")[0] if cand.strip() else ""
                if u:
                    self.passive.append((tag, u))
        if a.get("poster"):
            self.passive.append((tag, a["poster"].strip()))


def mixed_content(html: str) -> list[dict]:
    p = _ResParser()
    try:
        p.feed(html)
    except Exception:
        pass
    f: list[dict] = []
    act = sorted({f"<{t}> {u}" for t, u in p.active if u.lower().startswith("http://")})
    pas = sorted({f"<{t}> {u}" for t, u in p.passive if u.lower().startswith("http://")})
    if act:
        add(f, "high", "mixed_content_active", f"{len(act)} http:// scripts/iframes/stylesheets on an https page "
            "(browsers block them; the page may break)", samples=act[:10])
    if pas:
        add(f, "medium", "mixed_content_passive", f"{len(pas)} http:// images/media on an https page "
            "(auto-upgraded or blocked; 'Not secure' warnings)", samples=pas[:10])
    return f


# --------------------------------------------------------------------------- one raw GET (all Set-Cookie headers)
def get_raw(url: str, ua: str, timeout: float) -> dict:
    reason = ca.url_allowed(url)
    if reason:
        return {"status": 0, "error": reason, "headers": {}, "set_cookie": [], "body": ""}
    req = request.Request(url, headers={"User-Agent": ua, "Accept": "text/html,*/*;q=0.8", "Accept-Encoding": "gzip, deflate"})
    try:
        resp = ca._opener.open(req, timeout=timeout)  # noqa: SLF001 - guarded opener shared with crawl_audit
    except error.HTTPError as e:
        resp = e
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "error": str(getattr(e, "reason", e)), "headers": {}, "set_cookie": [], "body": ""}
    hdrs = resp.headers
    try:
        raw = resp.read(5 * 1024 * 1024 + 1)
    except Exception:  # noqa: BLE001
        raw = b""
    body, _ = ca._decode_body(raw, hdrs.get("Content-Encoding"), 5 * 1024 * 1024)  # noqa: SLF001
    return {"status": getattr(resp, "status", None) or getattr(resp, "code", 0), "error": None,
            "headers": {k.lower(): v for k, v in hdrs.items()}, "set_cookie": hdrs.get_all("Set-Cookie") or [],
            "body": body.decode(hdrs.get_content_charset() or "utf-8", errors="replace")}


# --------------------------------------------------------------------------- run
def run(url: str, ua: str = UA_DEFAULT, timeout: float = 15.0, allow_private: bool = False) -> dict:
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    host = (p.hostname or "").lower()
    protected = ca.set_network_mode(host, allow_private)
    local = ca.start_is_local(host)
    findings: list[dict] = []
    details: dict = {"tls": None, "http_redirect": None, "www": None, "cookies": [], "final_url": None}
    is_ip = bool(re.fullmatch(r"[\d.]+|[0-9a-f:]+", host))

    # ---- TLS
    tls_wanted = p.scheme == "https" or not local
    if tls_wanted:
        port = p.port if (p.scheme == "https" and p.port) else 443
        probe = tls_probe(host, port, timeout)
        tf, details["tls"] = analyze_tls(probe)
        findings += tf
    else:
        add(findings, "info", "tls_skipped_local", f"{host} is local and served over http; TLS, http->https and www checks skipped")

    # ---- http -> https and www/apex
    if not local and not is_ip:
        hres = ca.fetch(f"http://{p.netloc if p.scheme == 'http' else host}/", ua, timeout=timeout)
        details["http_redirect"] = {"status": hres["status"], "chain": hres["chain"], "error": hres["error"]}
        findings += classify_http_redirect(hres)
        apex = host[4:] if host.startswith("www.") else host
        if not is_apex_like(apex):
            add(findings, "info", "www_apex_skipped", f"{host} looks like a subdomain; www/apex consolidation not checked "
                "(run against the apex or www host)")
        else:
            scheme = "https"
            ares = ca.fetch(f"{scheme}://{apex}/", ua, timeout=timeout, max_redirects=3)
            wres = ca.fetch(f"{scheme}://www.{apex}/", ua, timeout=timeout, max_redirects=3)
            details["www"] = {"apex": {"status": ares["status"], "chain": ares["chain"], "final": ares["final_url"]},
                              "www": {"status": wres["status"], "chain": wres["chain"], "final": wres["final_url"]}}
            findings += classify_www(apex, ares, wres)

    # ---- final HTML: headers, cookies, mixed content
    page = ca.fetch(url, ua, timeout=timeout)
    final = page["final_url"]
    details["final_url"] = final
    if page["status"] == 0:
        add(findings, "high", "fetch_error", f"Could not fetch {url}: {page['error']}")
    else:
        raw = get_raw(final, ua, timeout)
        headers = raw["headers"] or page["headers"]
        body = raw["body"] or page["body"]
        https = urlparse(final).scheme == "https"
        if not https and not local:
            add(findings, "critical", "final_not_https", f"Final URL {final} is plain http")
        hf = check_headers(headers, https)
        for x in hf:
            x["signal"] = "security"
        findings += hf
        cf, details["cookies"] = check_cookies(raw["set_cookie"], https)
        findings += cf
        if https and "html" in headers.get("content-type", ""):
            findings += mixed_content(body)
        details["security_headers"] = {k: headers.get(k) for k in (
            "strict-transport-security", "content-security-policy", "content-security-policy-report-only",
            "x-content-type-options", "x-frame-options", "referrer-policy", "permissions-policy")}
    counts = Counter(f["severity"] for f in findings)
    return {"tool": "transport_check", "version": 1, "url": url, "host": host, "origin": f"{p.scheme}://{p.netloc}",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "network_guard": protected,
            "note": "Security headers are trust/security signals, not ranking factors.",
            "findings": sorted(findings, key=lambda x: SEVERITY_ORDER[x["severity"]]), "details": details,
            "summary": {"counts": dict(counts)}}


def to_markdown(r: dict) -> str:
    c = r["summary"]["counts"]
    L = [f"# Transport & security check: {r['host']}", "",
         f"- URL: {r['url']} -> final {r['details'].get('final_url')}",
         f"- Checked: {r['checked_at']} | " + " ".join(f"{k}: {c.get(k, 0)}" for k in SEVERITY_ORDER),
         "- Note: security headers are **trust/security signals, not ranking factors**. HTTPS itself, a valid "
         "certificate and clean redirects do affect crawling and users.", ""]
    t = r["details"].get("tls")
    if t:
        L += ["## TLS", "", f"- Protocol: {t.get('protocol')} | cipher: {t.get('cipher')} | verified: {t.get('verified')}",
              f"- Issuer: {t.get('issuer')} | valid: {t.get('not_before')} -> {t.get('not_after')} | days left: {t.get('days_left')}",
              f"- Names: {', '.join(t.get('names') or [])[:300]}", ""]
    L += ["## Findings", "", "| Severity | Code | Message |", "|---|---|---|"]
    for f in r["findings"]:
        msg = f["message"].replace("|", "\\|")
        extra = f.get("samples") or f.get("cookies") or f.get("chain")
        if extra:
            msg += " — " + ", ".join(str(x) for x in extra[:5]).replace("|", "\\|")
        L.append(f"| {f['severity']} | `{f['code']}` | {msg[:400]} |")
    if r["details"].get("cookies"):
        L += ["", "## Cookies on the HTML response (names only)", "", "| Name | Secure | HttpOnly | SameSite |", "|---|---|---|---|"]
        for ck in r["details"]["cookies"]:
            L.append(f"| {ck['name']} | {'Y' if ck['secure'] else 'N'} | {'Y' if ck['httponly'] else 'N'} | {ck['samesite'] or '-'} |")
    return "\n".join(L) + "\n"


def write_outputs(prefix: str, host: str, js: str, md: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    host = re.sub(r"[^\w.-]", "_", host)
    (out / f"{prefix}-{host}-{stamp}.json").write_text(js, encoding="utf-8")
    (out / f"{prefix}-{host}-{stamp}.md").write_text(md, encoding="utf-8")
    print(f"[saved] {out / f'{prefix}-{host}-{stamp}.md'}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--ua", default=UA_DEFAULT)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--allow-private", action="store_true", help="allow private/internal addresses for a non-local start host")
    ap.add_argument("--out", default=".seo/audits")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on", choices=["critical", "high", "medium"])
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = run(a.url, a.ua, a.timeout, a.allow_private)
    md = to_markdown(r)
    js = json.dumps(r, indent=2, ensure_ascii=False, default=str)
    if not a.no_write:
        write_outputs("transport", urlparse(r["origin"]).netloc, js, md, a.out)
    print(js if a.json else md)
    if a.fail_on:
        lim = SEVERITY_ORDER[a.fail_on]
        if any(SEVERITY_ORDER[s] <= lim and n for s, n in r["summary"]["counts"].items()):
            sys.exit(1)


if __name__ == "__main__":
    main()
