#!/usr/bin/env python3
"""indexnow.py - IndexNow key setup, verification and URL submission (Bing, Yandex, Naver, Seznam; not Google).
Free, stdlib only, Python 3.10+.

  python indexnow.py keygen [--public-dir public]           # new 32-hex key; optionally writes public/<key>.txt
  python indexnow.py verify --host example.com [--key KEY]  # checks https://example.com/<key>.txt
  python indexnow.py submit --host example.com https://example.com/a https://example.com/b
  python indexnow.py submit --host example.com --file urls.txt
  python indexnow.py submit --host example.com --from-sitemap https://example.com/sitemap.xml --since 2026-11-01

The key comes from --key or the INDEXNOW_KEY environment variable (keygen needs neither).
Only URLs on --host are sent; at most 10,000 per request. Use --dry-run to preview.
Every command accepts --json and --no-write (skip .seo/audits/indexnow-<cmd>-<host>-<stamp>.json|md).
Exit codes: 0 ok, 1 submission/verification failed, 2 bad arguments / missing key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import crawl_audit  # noqa: E402

ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10000
KEY_RX = re.compile(r"^[A-Za-z0-9-]{8,128}$")
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
STATUS_TEXT = {
    200: ("ok", "URLs submitted."),
    202: ("ok", "Accepted; the key is still being validated."),
    400: ("error", "Bad request: invalid format."),
    403: ("error", "Key not valid: key file not found at keyLocation or its content does not match the key."),
    422: ("error", "URLs do not belong to the host, or the key does not match the protocol schema."),
    429: ("error", "Too many requests: slow down (possible spam signal); batch changes and retry later."),
}


def get_key(a) -> str:
    key = getattr(a, "key", None) or os.environ.get("INDEXNOW_KEY")
    if not key:
        raise ValueError("No IndexNow key: pass --key or set INDEXNOW_KEY (create one with `indexnow.py keygen`).")
    if not KEY_RX.match(key):
        raise ValueError("IndexNow key must be 8-128 characters of a-z, A-Z, 0-9 or '-'.")
    return key


def clean_host(h: str) -> str:
    h = h.strip()
    return (urlparse(h).hostname if "://" in h else h.split("/")[0]).lower()


def key_location(host: str, key: str, explicit: str | None = None) -> str:
    return explicit or f"https://{host}/{key}.txt"


# --------------------------------------------------------------------------- output
def md_findings(findings) -> list[str]:
    if not findings:
        return ["", "No findings."]
    return ["", "## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in findings]


def emit(a, host: str, data: dict, md: str) -> None:
    data["findings"] = sorted(data.get("findings", []), key=lambda f: ORDER.get(f["severity"], 9))
    print(json.dumps(data, indent=2, ensure_ascii=False) if a.json else md)
    if not a.no_write:
        out = Path(".seo") / "audits"
        out.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9.-]+", "_", host)[:80] or "site"
        base = out / f"indexnow-{a.cmd}-{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        (base.parent / (base.name + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        (base.parent / (base.name + ".md")).write_text(md, encoding="utf-8")
        print(f"[saved] {base}.json / .md", file=sys.stderr)


# --------------------------------------------------------------------------- keygen / verify
def cmd_keygen(a):
    key = secrets.token_hex(16)
    data = {"command": "keygen", "key": key, "file_name": f"{key}.txt", "file_content": key,
            "serve_at": f"https://<your-host>/{key}.txt", "content_type": "text/plain", "findings": []}
    if a.public_dir:
        p = Path(a.public_dir) / f"{key}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(key, encoding="utf-8", newline="")
        data["written"] = str(p)
    md = ["# New IndexNow key", "", f"- Key: `{key}`", f"- Create the file `{key}.txt` at the site root "
          f"(e.g. `public/{key}.txt`) with exactly this content: `{key}`",
          f"- It must be served at `https://<your-host>/{key}.txt` as `text/plain`",
          f"- Then set the env var: `INDEXNOW_KEY={key}` and run `indexnow.py verify --host <your-host>`"]
    if data.get("written"):
        md.append(f"- Written: `{data['written']}`")
    return "keygen", data, "\n".join(md)


def cmd_verify(a):
    key = get_key(a)
    host = clean_host(a.host)
    loc = key_location(host, key, a.key_location)
    crawl_audit.set_network_mode(urlparse(loc).hostname)
    r = crawl_audit.fetch(loc, crawl_audit.UA_DEFAULT, timeout=20, accept="text/plain,*/*", max_bytes=4096)
    findings = []
    ctype = (r.get("headers") or {}).get("content-type", "")
    body = (r.get("body") or "").strip()
    if r.get("error"):
        findings.append({"severity": "high", "code": "key_file_unreachable", "message": f"{loc}: {r['error']}"})
    elif r["status"] == 404:
        findings.append({"severity": "high", "code": "key_file_missing",
                         "message": f"{loc} returned 404. Serve a text file named {key}.txt at the site root containing the key."})
    elif r["status"] != 200:
        findings.append({"severity": "high", "code": "key_file_status", "message": f"{loc} returned HTTP {r['status']}"})
    else:
        if body != key:
            findings.append({"severity": "high", "code": "key_mismatch",
                             "message": f"{loc} body does not equal the key (got {len(body)} chars; an SPA fallback page is a common cause)"})
        if not ctype.lower().startswith("text/plain"):
            findings.append({"severity": "medium", "code": "key_content_type",
                             "message": f"{loc} is served as '{ctype or 'no content-type'}', expected text/plain"})
        final_host = (urlparse(r.get("final_url") or loc).hostname or "").lower()
        if r.get("chain") and final_host != host:
            findings.append({"severity": "medium", "code": "key_file_redirect_offhost",
                             "message": f"{loc} redirects to another host ({final_host}); IndexNow may not accept it"})
    ok = not any(f["severity"] in ("critical", "high", "medium") for f in findings)
    data = {"command": "verify", "host": host, "key_location": loc, "status": r.get("status"), "content_type": ctype,
            "valid": ok, "findings": findings}
    md = [f"# IndexNow key check: {host}", "", f"- Key file: {loc}", f"- HTTP status: {r.get('status') or r.get('error')}",
          f"- Content-Type: {ctype or '-'}", f"- Result: **{'valid' if ok else 'NOT valid'}**"] + md_findings(findings)
    return host, data, "\n".join(md)


# --------------------------------------------------------------------------- submit
def parse_since(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_lastmod(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else datetime.fromisoformat(s[:10])
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def sitemap_entries(sm_url: str, depth: int = 0) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Return ([(loc, lastmod)], problems). Follows a sitemap index one level deep (same site only)."""
    r = crawl_audit.fetch(sm_url, crawl_audit.UA_DEFAULT, accept="application/xml,text/xml,*/*")
    if r["status"] != 200:
        return [], [f"{sm_url} returned {r['status'] or r['error']}"]
    if re.search(r"<!(DOCTYPE|ENTITY)", r["body"][:5000], re.I):
        return [], [f"{sm_url} contains a DOCTYPE/ENTITY declaration; refused to parse"]
    try:
        root = ET.fromstring(r["body"].encode("utf-8", errors="replace"))
    except ET.ParseError as e:
        return [], [f"{sm_url} is not valid XML: {e}"]
    entries, problems = [], []
    for node in root:
        loc = lastmod = None
        for ch in node:
            t = ch.tag.split("}")[-1]
            if t == "loc" and ch.text:
                loc = ch.text.strip()
            elif t == "lastmod" and ch.text:
                lastmod = ch.text.strip()
        if loc:
            entries.append((loc, lastmod))
    if root.tag.split("}")[-1] == "sitemapindex":
        if depth:
            return [], [f"nested sitemap index {sm_url} skipped"]
        parent = (urlparse(sm_url).hostname or "").removeprefix("www.")
        out = []
        for loc, _ in entries[:50]:
            ch = (urlparse(loc).hostname or "").removeprefix("www.")
            if ch != parent and not ch.endswith("." + parent):
                problems.append(f"skipped cross-host child sitemap {loc}")
                continue
            e, p = sitemap_entries(loc, depth + 1)
            out += e
            problems += p
        return out, problems
    return entries, problems


def post_indexnow(endpoint: str, payload: dict, timeout: float = 30.0) -> tuple[int, str]:
    req = request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
                          headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "super-seo-indexnow/1.0"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return getattr(resp, "status", 200), resp.read(2000).decode("utf-8", errors="replace")
    except error.HTTPError as e:
        try:
            body = e.read(2000).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except (error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        return 0, str(getattr(e, "reason", e))


def cmd_submit(a):
    key = get_key(a)
    host = clean_host(a.host)
    since = parse_since(a.since)
    findings: list[dict] = []
    urls = list(a.urls)
    if a.file:
        urls += [l.strip() for l in Path(a.file).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    if a.from_sitemap:
        crawl_audit.set_network_mode(urlparse(a.from_sitemap).hostname)
        entries, problems = sitemap_entries(a.from_sitemap)
        findings += [{"severity": "low", "code": "sitemap_problem", "message": p} for p in problems]
        no_lastmod = 0
        for loc, lm in entries:
            if since:
                d = parse_lastmod(lm)
                if d is None:
                    no_lastmod += 1
                    continue
                if d < since:
                    continue
            urls.append(loc)
        if no_lastmod:
            findings.append({"severity": "info", "code": "no_lastmod",
                             "message": f"{no_lastmod} sitemap URL(s) have no usable <lastmod> and were skipped by --since"})
    elif since:
        findings.append({"severity": "info", "code": "since_ignored", "message": "--since only filters --from-sitemap URLs"})
    urls = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    off = [u for u in urls if (urlparse(u).hostname or "").lower() != host or urlparse(u).scheme not in ("http", "https")]
    urls = [u for u in urls if u not in set(off)]
    if off:
        findings.append({"severity": "low", "code": "off_host_skipped",
                         "message": f"Skipped {len(off)} URL(s) not on {host} (IndexNow rejects them with 422)"})
    if not urls:
        data = {"command": "submit", "host": host, "submitted": 0, "batches": [], "findings": findings +
                [{"severity": "info", "code": "nothing_to_submit", "message": "No URLs to submit."}]}
        return host, data, "\n".join([f"# IndexNow submit: {host}", "", "Nothing to submit."] + md_findings(data["findings"]))
    loc = key_location(host, key, a.key_location)
    batches = []
    for i in range(0, len(urls), MAX_URLS):
        chunk = urls[i:i + MAX_URLS]
        payload = {"host": host, "key": key, "keyLocation": loc, "urlList": chunk}
        if a.dry_run:
            batches.append({"urls": len(chunk), "status": None, "result": "dry run"})
            continue
        status, body = post_indexnow(a.endpoint, payload)
        kind, text = STATUS_TEXT.get(status, ("error", f"Unexpected response{': ' + body[:200] if body else ''}"))
        batches.append({"urls": len(chunk), "status": status, "result": text})
        if kind != "ok":
            findings.append({"severity": "high", "code": f"indexnow_{status or 'network'}", "message": f"HTTP {status}: {text}"})
            if status in (403, 422, 429, 0):
                break
    sent = sum(b["urls"] for b in batches if b["status"] in (200, 202))
    data = {"command": "submit", "host": host, "endpoint": a.endpoint, "key_location": loc, "dry_run": a.dry_run,
            "urls": urls, "submitted": sent, "batches": batches, "findings": findings}
    md = [f"# IndexNow submit: {host}" + (" (dry run)" if a.dry_run else ""), "", f"- Endpoint: {a.endpoint}",
          f"- Key location: {loc}", f"- URLs: {len(urls)}" + ("" if a.dry_run else f", accepted: {sent}")]
    md += [f"- Batch {i + 1}: {b['urls']} URLs -> {b['status'] if b['status'] is not None else '-'} {b['result']}" for i, b in enumerate(batches)]
    md += [f"  - {u}" for u in urls[:30]] + (["  - ..."] if len(urls) > 30 else []) + md_findings(findings)
    return host, data, "\n".join(md)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--no-write", action="store_true")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("keygen", parents=[common], help="generate a 32-hex key")
    p.add_argument("--public-dir", help="also write <key>.txt into this folder (your web root, e.g. public/)")
    for name in ("verify", "submit"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--host", required=True, help="example.com (no scheme)")
        p.add_argument("--key", help="defaults to INDEXNOW_KEY")
        p.add_argument("--key-location", help="defaults to https://<host>/<key>.txt")
        if name == "submit":
            p.add_argument("urls", nargs="*")
            p.add_argument("--file", help="text file, one URL per line")
            p.add_argument("--from-sitemap", help="sitemap or sitemap index URL")
            p.add_argument("--since", help="ISO date/time; only sitemap URLs with lastmod >= this")
            p.add_argument("--endpoint", default=ENDPOINT)
            p.add_argument("--dry-run", action="store_true")
    return ap


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args(argv)
    try:
        host, data, md = {"keygen": cmd_keygen, "verify": cmd_verify, "submit": cmd_submit}[a.cmd](a)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.cmd == "keygen":
        a.no_write = True  # never copy the key into project files other than the one the user asked for
    emit(a, host, data, md)
    if a.cmd == "verify" and not data["valid"]:
        return 1
    if a.cmd == "submit" and any(f["severity"] == "high" for f in data["findings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
