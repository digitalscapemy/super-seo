#!/usr/bin/env python3
"""lighthouse.py - local Lighthouse lab audits (performance, accessibility, best practices, SEO), free, no API key.

  python extensions/lighthouse/lighthouse.py run http://localhost:3000
  python extensions/lighthouse/lighthouse.py run https://example.com/pricing --desktop --agentic
  python extensions/lighthouse/lighthouse.py site https://example.com --max-pages 20
  python extensions/lighthouse/lighthouse.py from-json report.json --json

run        npx -y lighthouse <url> --output=json (headless Chrome). Works on localhost / preview builds.
site       npx -y unlighthouse-ci --site <url> --reporter jsonExpanded; aggregates the worst pages per category.
           If Unlighthouse fails, falls back to `run` over the first --max-pages sitemap URLs.
from-json  parse an existing Lighthouse JSON report (lhr).

Needs Node.js (npx) and a local Chrome/Chromium. Lab data only: field Core Web Vitals come from CrUX.
Writes .seo/audits/lighthouse-<host>-<stamp>.json|md unless --no-write. Stdlib only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
TOOL = "lighthouse"
CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]
AGENTIC = "agentic-browsing"
TIMEOUT = 300
# lab thresholds (good, poor): CWV from references/technical-seo.md; TBT is the lab proxy for INP (Lighthouse scoring)
THRESHOLDS = {"LCP": (2500, 4000, "ms"), "CLS": (0.1, 0.25, ""), "INP": (200, 500, "ms"), "TBT": (200, 600, "ms")}
METRIC_AUDITS = {"LCP": "largest-contentful-paint", "CLS": "cumulative-layout-shift", "TBT": "total-blocking-time",
                 "INP": "interaction-to-next-paint", "FCP": "first-contentful-paint", "SI": "speed-index",
                 "TTFB": "server-response-time"}
SCORED_MODES = {"binary", "numeric", "metricSavings"}


class ToolError(RuntimeError):
    pass


# --------------------------------------------------------------------------- parsing (pure, unit-tested)
def _metric(audits: dict, aid: str):
    a = audits.get(aid) or {}
    v = a.get("numericValue")
    return round(v, 3 if aid == "cumulative-layout-shift" else 0) if isinstance(v, (int, float)) else None


def rate(name: str, value) -> str | None:
    if value is None or name not in THRESHOLDS:
        return None
    good, poor, _ = THRESHOLDS[name]
    return "good" if value <= good else ("needs-improvement" if value <= poor else "poor")


def parse_lhr(lhr: dict) -> dict:
    """Condense a Lighthouse result (lhr) into scores, metrics, failed audits and findings."""
    if "lhr" in lhr and isinstance(lhr["lhr"], dict):
        lhr = lhr["lhr"]
    if lhr.get("runtimeError"):
        raise ToolError(f"Lighthouse runtime error: {lhr['runtimeError'].get('code')}: {lhr['runtimeError'].get('message', '')[:300]}")
    audits = lhr.get("audits") or {}
    cats = lhr.get("categories") or {}
    scores = {cid: (round(c["score"] * 100) if isinstance(c.get("score"), (int, float)) else None)
              for cid, c in cats.items() if cid != AGENTIC}
    metrics = {}
    for name, aid in METRIC_AUDITS.items():
        v = _metric(audits, aid)
        if v is not None:
            metrics[name] = {"value": v, "rating": rate(name, v)}
    owner: dict[str, str] = {}
    for cid, c in cats.items():
        for ref in c.get("auditRefs") or []:
            owner.setdefault(ref.get("id"), cid)
    failed = []
    for aid, a in audits.items():
        s, mode = a.get("score"), a.get("scoreDisplayMode")
        if mode not in SCORED_MODES or not isinstance(s, (int, float)) or s >= 0.9:
            continue
        if aid in METRIC_AUDITS.values():
            continue  # reported as metrics
        failed.append({"id": aid, "title": a.get("title", aid), "score": s, "category": owner.get(aid, "insight" if aid.endswith("-insight") else ""),
                       "insight": aid.endswith("-insight"), "display": a.get("displayValue", ""),
                       "savings_ms": (a.get("metricSavings") or {}).get("LCP") or (a.get("details") or {}).get("overallSavingsMs")})
    failed.sort(key=lambda f: (f["score"], f["id"]))
    agentic = None
    if AGENTIC in cats:
        refs = [r for r in cats[AGENTIC].get("auditRefs") or []]
        checked = [r["id"] for r in refs if (audits.get(r["id"]) or {}).get("scoreDisplayMode") in SCORED_MODES
                   and isinstance((audits.get(r["id"]) or {}).get("score"), (int, float))]
        passed = [i for i in checked if audits[i]["score"] >= 0.9]
        agentic = {"passed": len(passed), "total": len(checked), "label": f"{len(passed)}/{len(checked)} passed",
                   "failed": [{"id": i, "title": audits[i].get("title", i)} for i in checked if i not in passed]}
    res = {"url": lhr.get("finalDisplayedUrl") or lhr.get("finalUrl") or lhr.get("requestedUrl"),
           "lighthouse_version": lhr.get("lighthouseVersion"), "fetch_time": lhr.get("fetchTime"),
           "form_factor": (lhr.get("configSettings") or {}).get("formFactor"),
           "scores": scores, "metrics": metrics, "failed_audits": failed, "agentic": agentic,
           "warnings": lhr.get("runWarnings") or []}
    res["findings"] = findings_for(res)
    return res


def findings_for(r: dict) -> list[dict]:
    out = []
    url = r.get("url")
    for name, m in r["metrics"].items():
        if m["rating"] == "poor":
            out.append({"severity": "high", "code": f"lab_{name.lower()}_poor", "message": f"{name} {fmt(name, m['value'])} is poor on {url} (lab)."})
        elif m["rating"] == "needs-improvement":
            out.append({"severity": "medium", "code": f"lab_{name.lower()}_needs_improvement", "message": f"{name} {fmt(name, m['value'])} needs improvement on {url} (lab)."})
    seo = r["scores"].get("seo")
    if seo is not None and seo < 90:
        out.append({"severity": "high" if seo < 70 else "medium", "code": "lighthouse_seo_low", "message": f"Lighthouse SEO score {seo} on {url}."})
    perf = r["scores"].get("performance")
    if perf is not None and perf < 50:
        out.append({"severity": "medium", "code": "lighthouse_performance_low", "message": f"Lighthouse performance score {perf} on {url} (lab, simulated throttling)."})
    for f in r["failed_audits"]:
        if f["category"] == "seo":
            out.append({"severity": "medium", "code": f"lh_{f['id'].replace('-', '_')}", "message": f"SEO audit failed: {f['title']} ({url})."})
    if r.get("agentic") and r["agentic"]["total"] and r["agentic"]["passed"] < r["agentic"]["total"]:
        out.append({"severity": "low", "code": "agentic_browsing_checks_failed",
                    "message": f"Agentic browsing: {r['agentic']['label']} on {url}: " + ", ".join(x["title"] for x in r["agentic"]["failed"][:5])})
    return out


def fmt(name: str, v) -> str:
    if name == "CLS":
        return f"{v:.3f}"
    return f"{v / 1000:.2f} s" if v >= 1000 else f"{int(v)} ms"


def page_markdown(r: dict, heading: str = "##") -> list[str]:
    L = [f"{heading} {r.get('url')}", "",
         f"Lighthouse {r.get('lighthouse_version')} · {r.get('form_factor') or '?'} · {r.get('fetch_time') or ''}", "",
         "| Category | Score |", "|---|---|"]
    L += [f"| {k} | {v if v is not None else 'n/a'} |" for k, v in r["scores"].items()]
    if r.get("agentic"):
        L.append(f"| agentic-browsing | {r['agentic']['label']} |")
    if r["metrics"]:
        L += ["", "| Metric (lab) | Value | Rating |", "|---|---|---|"]
        L += [f"| {k} | {fmt(k, m['value'])} | {m['rating'] or '-'} |" for k, m in r["metrics"].items()]
    if r["failed_audits"]:
        L += ["", "Failed audits (score < 0.9):", ""]
        for f in r["failed_audits"][:30]:
            tag = " (insight)" if f["insight"] else ""
            L.append(f"- [{f['category'] or '-'}] `{f['id']}`{tag}: {f['title']}" + (f" - {f['display']}" if f["display"] else ""))
    for w in r.get("warnings")[:5]:
        L.append(f"- warning: {w}")
    return L + [""]


# --------------------------------------------------------------------------- execution
def npx_argv() -> list[str]:
    """argv prefix that runs npx without a shell. On Windows npx is npx.cmd (run via cmd.exe), so prefer calling
    node with npm's npx-cli.js directly; this avoids cmd.exe re-parsing URL characters such as & and %."""
    npx = shutil.which("npx")
    if not npx:
        raise ToolError("npx not found: install Node.js 18+ (https://nodejs.org) and make sure npx is on PATH.")
    if os.name == "nt":
        node = shutil.which("node")
        cli = Path(npx).parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
        if node and cli.exists():
            return [node, str(cli)]
    return [npx]


def check_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError(f"not an http(s) URL: {url!r}")
    if os.name == "nt" and npx_argv()[0].lower().endswith((".cmd", ".bat")) and re.search(r'[&|<>^%"\s]', url):
        raise ToolError("URL contains characters cmd.exe would interpret; install Node.js so node + npx-cli.js can be used directly.")
    return url


def run_proc(argv: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        raise ToolError(f"timed out after {timeout}s: {' '.join(argv[-6:])}") from None
    except FileNotFoundError as e:
        raise ToolError(f"could not start {argv[0]}: {e}") from None


def explain_failure(stderr: str) -> str:
    s = stderr or ""
    if re.search(r"CHROME_PATH|No Chrome installations found|Unable to find Chrome|chrome-launcher", s, re.I):
        return "Chrome/Chromium not found. Install Google Chrome or set CHROME_PATH to a Chromium binary."
    if re.search(r"ENOTFOUND|ERR_NAME_NOT_RESOLVED|ECONNREFUSED|ERR_CONNECTION_REFUSED", s):
        return "The page could not be reached (is the server running / is the URL right?)."
    if re.search(r"EAI_AGAIN|ETIMEDOUT|registry\.npmjs\.org", s):
        return "npx could not download the package (offline or npm registry blocked)."
    return "Lighthouse failed."


def run_lighthouse(url: str, desktop: bool = False, agentic: bool = False, timeout: int = TIMEOUT) -> dict:
    url = check_url(url)
    cats = CATEGORIES + ([AGENTIC] if agentic else [])
    with tempfile.TemporaryDirectory(prefix="super-seo-lh-") as td:
        out = Path(td) / "report.json"
        argv = npx_argv() + ["-y", "lighthouse", url, "--output=json", f"--output-path={out}", "--quiet",
                             "--chrome-flags=--headless=new", f"--only-categories={','.join(cats)}"]
        if desktop:
            argv.append("--preset=desktop")
        p = run_proc(argv, timeout)
        if not out.exists() or out.stat().st_size == 0:
            tail = (p.stderr or p.stdout or "").strip()[-800:]
            if agentic and re.search(AGENTIC, tail, re.I):
                raise ToolError(f"This Lighthouse version has no '{AGENTIC}' category; rerun without --agentic.\n{tail}")
            raise ToolError(f"{explain_failure(p.stderr)} (exit {p.returncode})\n{tail}")
        lhr = json.loads(out.read_text(encoding="utf-8"))
    return parse_lhr(lhr)


def parse_unlighthouse(data) -> list[dict]:
    """Normalise Unlighthouse jsonExpanded/json output into [{path, url, scores{cat: 0-100}, metrics}]."""
    rows = data if isinstance(data, list) else (data.get("routes") or data.get("reports") or data.get("results") or [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        scores = {}
        for cid, c in (r.get("categories") or {}).items():
            v = c.get("score") if isinstance(c, dict) else c
            if isinstance(v, (int, float)):
                scores[cid] = round(v * 100) if v <= 1 else round(v)
        metrics = {}
        for k, v in (r.get("metrics") or {}).items():
            if isinstance(v, dict) and isinstance(v.get("numericValue"), (int, float)):
                metrics[k] = v["numericValue"]
        out.append({"path": r.get("path") or urlparse(r.get("url") or "").path or "/", "url": r.get("url"),
                    "score": r.get("score"), "scores": scores, "metrics": metrics})
    return out


def worst_pages(pages: list[dict], n: int = 5) -> dict:
    cats = sorted({c for p in pages for c in p["scores"]})
    return {c: sorted(({"path": p["path"], "score": p["scores"][c]} for p in pages if c in p["scores"]),
                      key=lambda x: x["score"])[:n] for c in cats}


def run_unlighthouse(url: str, max_pages: int, desktop: bool) -> list[dict]:
    url = check_url(url)
    with tempfile.TemporaryDirectory(prefix="super-seo-ulh-") as td:
        argv = npx_argv() + ["-y", "unlighthouse-ci", "--site", url, "--reporter", "jsonExpanded", "--output-path", td,
                             "--no-cache"]
        argv.append("--desktop" if desktop else "--mobile")
        p = run_proc(argv, max(TIMEOUT, 60 * max_pages))
        found = sorted(Path(td).rglob("ci-result.json")) or sorted(Path(td).rglob("*.json"))
        for f in found:
            try:
                pages = parse_unlighthouse(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
            if pages:
                return pages
        raise ToolError(f"Unlighthouse produced no report (exit {p.returncode}): {(p.stderr or p.stdout or '').strip()[-600:]}")


def sitemap_urls(url: str, n: int) -> list[str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from crawl_audit import read_robots, read_sitemap_urls, set_network_mode, UA_DEFAULT  # noqa: E402
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    set_network_mode(p.hostname)
    sitemaps = read_robots(origin, UA_DEFAULT).get("sitemaps") or [origin + "/sitemap.xml"]
    urls: list[str] = []
    for sm in sitemaps[:3]:
        got, _ = read_sitemap_urls(sm, UA_DEFAULT, limit=n)
        urls += [u for u in got if urlparse(u).hostname == p.hostname and u not in urls]
        if len(urls) >= n:
            break
    return urls[:n] or [url]


def cmd_site(url: str, max_pages: int, desktop: bool, agentic: bool) -> dict:
    notes, pages, per_page = [], [], []
    try:
        pages = run_unlighthouse(url, max_pages, desktop)
        mode = "unlighthouse"
    except ToolError as e:
        notes.append(f"Unlighthouse failed, fell back to Lighthouse over sitemap URLs: {str(e)[:400]}")
        mode = "lighthouse-fallback"
        for u in sitemap_urls(url, max_pages):
            try:
                r = run_lighthouse(u, desktop, agentic)
            except ToolError as err:
                notes.append(f"{u}: {str(err).splitlines()[0]}")
                continue
            per_page.append(r)
            pages.append({"path": urlparse(r["url"] or u).path or "/", "url": r["url"], "scores": r["scores"],
                          "metrics": {k: m["value"] for k, m in r["metrics"].items()}})
    worst = worst_pages(pages)
    findings = []
    for c, rows in worst.items():
        for w in rows:
            if w["score"] < 50:
                findings.append({"severity": "high" if c == "seo" else "medium", "code": f"site_{c.replace('-', '_')}_low",
                                 "message": f"{c} score {w['score']} on {w['path']}"})
    for r in per_page:
        findings += [f for f in r["findings"] if f["severity"] in ("critical", "high")]
    return {"mode": mode, "site": url, "pages": pages, "worst": worst, "page_reports": per_page, "findings": findings, "notes": notes}


def site_markdown(res: dict) -> str:
    L = [f"# Lighthouse site scan: {res['site']}", "", f"Mode: {res['mode']} · pages: {len(res['pages'])}", ""]
    if res["findings"]:
        L += ["## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in res["findings"][:40]] + [""]
    L += ["## Worst pages per category", ""]
    for c, rows in res["worst"].items():
        L += [f"### {c}", ""] + [f"- {w['score']} {w['path']}" for w in rows] + [""]
    if res["notes"]:
        L += ["## Notes", ""] + [f"- {n}" for n in res["notes"]]
    return "\n".join(L) + "\n"


def single_markdown(r: dict) -> str:
    L = [f"# Lighthouse: {r.get('url')}", ""]
    if r["findings"]:
        L += ["## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in r["findings"]] + [""]
    L += page_markdown(r)
    L += ["Lab data (simulated throttling) varies run to run; confirm field Core Web Vitals with CrUX / PageSpeed Insights.", ""]
    return "\n".join(L)


def write_outputs(result: dict, md: str, seo_dir: str, host: str) -> list[str]:
    out = Path(seo_dir) / "audits"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", host or "report").strip("-") or "report"
    j, m = out / f"{TOOL}-{safe}-{stamp}.json", out / f"{TOOL}-{safe}-{stamp}.md"
    j.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    m.write_text(md, encoding="utf-8")
    return [j.as_posix(), m.as_posix()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "site", "from-json"):
        sp = sub.add_parser(name)
        sp.add_argument("target", help="URL (run/site) or path to a Lighthouse JSON file (from-json)")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--no-write", action="store_true")
        sp.add_argument("--seo-dir", default=".seo")
        if name != "from-json":
            sp.add_argument("--desktop", action="store_true", help="desktop preset (default: mobile emulation)")
            sp.add_argument("--agentic", action="store_true", help=f"also run the '{AGENTIC}' category (Lighthouse 13+)")
        if name == "site":
            sp.add_argument("--max-pages", type=int, default=20)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        if a.command == "from-json":
            res = parse_lhr(json.loads(Path(a.target).read_text(encoding="utf-8")))
            md = single_markdown(res)
        elif a.command == "run":
            res = run_lighthouse(a.target, a.desktop, a.agentic)
            md = single_markdown(res)
        else:
            res = cmd_site(a.target, max(1, min(200, a.max_pages)), a.desktop, a.agentic)
            md = site_markdown(res)
    except (ToolError, OSError, json.JSONDecodeError) as e:
        print(f"[lighthouse] {e}", file=sys.stderr)
        return 2
    res["tool"], res["command"] = TOOL, a.command
    if not a.no_write:
        res["written"] = write_outputs(res, md, a.seo_dir, urlparse(res.get("url") or res.get("site") or "").hostname or "local")
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
