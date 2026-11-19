#!/usr/bin/env python3
"""audit.py - one command: code scan + crawl + AI access (+ optional PageSpeed/CrUX) -> scorecard + report.

  python audit.py --path . --url http://localhost:3000            # pre-deploy (production build running locally)
  python audit.py --url https://example.com --psi                 # live site, with Core Web Vitals
  python audit.py --path . --url https://preview.app --fail-on critical   # CI gate

Writes .seo/audits/* (raw JSON/MD from each tool) and .seo/reports/audit-<date>.md + .html.
--psi uses the free PageSpeed Insights API (set PSI_API_KEY for a reliable quota; works keyless when the shared quota allows).
Stdlib only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib import request, parse

HERE = Path(__file__).resolve().parent
SEV_W = {"critical": 15, "high": 7, "medium": 3, "low": 1, "info": 0}
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
WEIGHTS = {"indexability": 25, "rendering": 20, "ai_access": 15, "metadata": 15, "structured_data": 10, "performance": 15}
LABELS = {"indexability": "Indexability & crawl", "rendering": "Rendering (content without JS)", "ai_access": "AI access",
          "metadata": "Metadata & on-page", "structured_data": "Structured data", "performance": "Performance (CWV)"}

RENDER_CODES = {"csr_shell", "csr_spa", "no_text_without_js", "inertia_csr", "spa_index_meta", "client_jsonld"}
SCHEMA_PREFIX = ("jsonld", "schema_", "no_structured_data", "no_jsonld", "software_no_rating", "howto_schema")
META_CODES = {"missing_title", "title_long", "title_short", "missing_description", "description_long", "description_short",
              "templated_metadata", "description_duplicates_title", "shared_cta_descriptions", "duplicate_title",
              "duplicate_description", "missing_lang", "missing_viewport", "missing_favicon_link", "missing_h1", "multiple_h1",
              "empty_h1", "heading_skip", "thin_content", "no_main_landmark", "missing_og", "og_image_relative",
              "missing_twitter_card", "img_missing_alt", "img_missing_dimensions", "first_image_lazy", "generic_anchor_text",
              "empty_anchor_text", "js_link", "placeholder_text", "routes_without_metadata", "routes_inherit_only",
              "dynamic_routes_no_generateMetadata", "pages_without_head", "no_title_template", "no_html_lang", "no_og_image",
              "img_no_alt", "html_heavy", "slow_ttfb"}
AI_PREFIX = ("robots_blocks_", "waf_blocks_", "nosnippet", "max_snippet", "cloudflare_managed", "robots_5xx", "content_signal",
             "meta_noindex_", "ai_bots_blocked", "noai_meta", "data_nosnippet", "no_llms_txt", "llms_txt", "no_markdown")


def category(code: str, source: str) -> str:
    if code in RENDER_CODES:
        return "rendering"
    if code.startswith(SCHEMA_PREFIX):
        return "structured_data"
    if source == "ai_access" or code.startswith(AI_PREFIX):
        return "ai_access"
    if code in META_CODES:
        return "metadata"
    return "indexability"


def run_tool(args: list[str]) -> dict | None:
    cmd = [sys.executable, str(HERE / args[0])] + args[1:]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if p.returncode != 0 and not p.stdout.strip().startswith("{"):
        print(f"[warn] {args[0]} failed: {p.stderr.strip()[-400:]}", file=sys.stderr)
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        print(f"[warn] {args[0]} returned non-JSON output", file=sys.stderr)
        return None


def psi(url: str) -> dict:
    q = {"url": url, "strategy": "mobile", "category": "performance"}
    if os.environ.get("PSI_API_KEY"):
        q["key"] = os.environ["PSI_API_KEY"]
    api = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + parse.urlencode(q)
    import time
    d = None
    for attempt in range(2):
        try:
            with request.urlopen(request.Request(api, headers={"User-Agent": "super-seo-audit"}), timeout=120) as r:
                d = json.loads(r.read().decode("utf-8"))
            break
        except Exception as e:  # quota (429 on the shared keyless quota), network, localhost (PSI cannot reach it)
            if "429" in str(e) and attempt == 0:
                time.sleep(8)
                continue
            hint = " — set PSI_API_KEY (free key: Google Cloud console > APIs > PageSpeed Insights API)" if "429" in str(e) else ""
            return {"error": str(e) + hint}
    lab = d.get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score")
    field = {}
    for src in ("loadingExperience", "originLoadingExperience"):
        m = d.get(src, {}).get("metrics", {})
        if m:
            for k, name in (("LARGEST_CONTENTFUL_PAINT_MS", "LCP"), ("INTERACTION_TO_NEXT_PAINT", "INP"), ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "CLS")):
                if k in m and name not in field:
                    field[name] = {"p75": m[k].get("percentile"), "category": m[k].get("category"), "scope": "page" if src == "loadingExperience" else "origin"}
    audits = d.get("lighthouseResult", {}).get("audits", {})
    lab_metrics = {k: audits.get(a, {}).get("displayValue") for k, a in
                   (("LCP", "largest-contentful-paint"), ("CLS", "cumulative-layout-shift"), ("TBT", "total-blocking-time"), ("TTFB", "server-response-time"))}
    return {"lab_score": round(lab * 100) if lab is not None else None, "field": field, "lab": lab_metrics}


def score_categories(findings: list[dict], pages: int, measured: set[str]) -> dict:
    by_cat: dict[str, dict[str, dict]] = defaultdict(dict)
    for f in findings:
        c = category(f["code"], f["source"])
        e = by_cat[c].setdefault(f["code"], {"severity": f["severity"], "message": f["message"], "count": 0})
        e["count"] += 1
        if ORDER[f["severity"]] < ORDER[e["severity"]]:
            e["severity"] = f["severity"]
    out = {}
    for c in WEIGHTS:
        if c not in measured:
            out[c] = {"score": None, "issues": {}}
            continue
        penalty = 0.0
        for code, e in by_cat.get(c, {}).items():
            share = min(1.0, e["count"] / max(1, pages))
            penalty += SEV_W[e["severity"]] * (0.6 + 0.4 * share) * (2.0 if c != "metadata" else 1.0)
        out[c] = {"score": max(0, round(100 - penalty)), "issues": by_cat.get(c, {})}
    return out


def perf_score(p: dict) -> int | None:
    if not p or p.get("error"):
        return None
    vals = []
    if p.get("lab_score") is not None:
        vals.append(p["lab_score"])
    cat_pts = {"FAST": 100, "AVERAGE": 60, "SLOW": 20}
    field = [cat_pts.get(m.get("category"), None) for m in p.get("field", {}).values()]
    field = [x for x in field if x is not None]
    if field:
        vals.append(sum(field) / len(field))
    return round(sum(vals) / len(vals)) if vals else None


def to_html(md_title: str, sc: dict, overall, top: list[dict], backlog: list[dict], meta: dict) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(LABELS[c])}</td><td class='n'>{'—' if v['score'] is None else v['score']}</td>"
        f"<td>{e(', '.join(k for k, i in sorted(v['issues'].items(), key=lambda kv: ORDER[kv[1]['severity']])[:4] if i['severity'] != 'info') or ('not measured' if v['score'] is None else 'no issues'))}</td></tr>"
        for c, v in sc.items())
    tops = "".join(f"<li><span class='sev {f['severity']}'>{f['severity']}</span> <code>{e(f['code'])}</code> {e(f['message'][:220])}"
                   f"{(' <small>(' + e(f.get('where', '')) + ')</small>') if f.get('where') else ''}</li>" for f in top)
    blog = "".join(f"<tr><td><span class='sev {f['severity']}'>{f['severity']}</span></td><td><code>{e(f['code'])}</code></td>"
                   f"<td>{e(f['message'][:200])}</td><td>{e(f.get('where', ''))[:120]}</td></tr>" for f in backlog)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(md_title)}</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e5e5e5;--card:#fafafa;--crit:#b42318;--high:#c4320a;--med:#b54708;--low:#475467}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--mut:#aaa;--line:#2a2a2a;--card:#181818;--crit:#f97066;--high:#fd853a;--med:#fdb022;--low:#98a2b3}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}}
h1{{font-size:1.6rem;margin:.2em 0}} h2{{font-size:1.15rem;margin-top:2em;border-bottom:1px solid var(--line);padding-bottom:.3em}}
.big{{font-size:3rem;font-weight:700;line-height:1}} .mut{{color:var(--mut)}} table{{width:100%;border-collapse:collapse;font-size:.92rem}}
td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}} td.n{{font-weight:600;width:70px}}
.sev{{display:inline-block;font-size:.75rem;font-weight:700;text-transform:uppercase;padding:1px 6px;border-radius:4px;border:1px solid currentColor}}
.critical{{color:var(--crit)}} .high{{color:var(--high)}} .medium{{color:var(--med)}} .low,.info{{color:var(--low)}}
li{{margin:.35em 0}} code{{font-size:.85em}} .card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px}}
.wrap{{overflow-x:auto}}</style></head><body>
<h1>{e(md_title)}</h1><p class="mut">{e(meta['when'])} · {e(meta['target'])}</p>
<div class="card"><div class="big">{'—' if overall is None else overall}<span class="mut" style="font-size:1rem">/100 readiness</span></div>
<p class="mut">Weighted over measured categories only. A readiness indicator for the site's own controls, not a ranking or AI-citation forecast.</p></div>
<h2>Fix first</h2><ol>{tops or '<li>No critical or high issues.</li>'}</ol>
<h2>Scorecard</h2><div class="wrap"><table><tr><th>Category</th><th>Score</th><th>Main issues</th></tr>{rows}</table></div>
<h2>Backlog</h2><div class="wrap"><table><tr><th>Sev</th><th>Code</th><th>Detail</th><th>Where</th></tr>{blog}</table></div>
<h2>How this was made</h2><p class="mut">{e(meta['how'])}</p></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", help="project root to scan statically")
    ap.add_argument("--url", help="site to crawl (localhost production build, preview, or live)")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--psi", action="store_true", help="add PageSpeed Insights / CrUX (public URLs only)")
    ap.add_argument("--no-probe", action="store_true", help="skip spoofed-UA bot probes")
    ap.add_argument("--allow-private", action="store_true", help="pass through to crawlers: allow private addresses for a non-localhost URL")
    ap.add_argument("--seo-dir", default=None, help="where .seo/ lives (default: <path>/.seo or ./.seo)")
    ap.add_argument("--fail-on", choices=["critical", "high"])
    a = ap.parse_args(argv)
    if not a.path and not a.url:
        ap.error("give --path and/or --url")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    seo = Path(a.seo_dir) if a.seo_dir else (Path(a.path) / ".seo" if a.path else Path(".seo"))
    audits, reports = seo / "audits", seo / "reports"
    audits.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    findings, measured, pages, how = [], set(), 1, []
    scan = crawl = access = perf = None
    if a.path:
        scan = run_tool(["project_scan.py", a.path, "--json"])
        if scan:
            (audits / f"project-scan-{stamp}.json").write_text(json.dumps(scan, indent=2, default=str), encoding="utf-8")
            for f in scan["findings"]:
                findings.append({**f, "source": "project_scan", "where": f"{f['file']}:{f['line']}" if f.get("file") else "project"})
            measured |= {"rendering", "metadata", "structured_data", "indexability"}
            how.append(f"project_scan on {scan['root']} ({scan['detect']['framework']}, {scan['files_scanned']} files)")
    if a.url:
        crawl = run_tool(["crawl_audit.py", a.url, "--max-pages", str(a.max_pages), "--json", "--no-write"]
                         + (["--allow-private"] if a.allow_private else []))
        if crawl:
            host = crawl["origin"].split("://", 1)[1].replace(":", "_")
            (audits / f"crawl-{host}-{stamp}.json").write_text(json.dumps(crawl, indent=2, ensure_ascii=False), encoding="utf-8")
            pages = max(1, crawl["pages_crawled"])
            for f in crawl["site_issues"]:
                findings.append({**f, "source": "crawl", "where": "site"})
            for pg in crawl["pages"]:
                for f in pg["issues"]:
                    findings.append({**f, "source": "crawl", "where": pg["final_url"]})
            measured |= {"indexability", "rendering", "metadata", "structured_data"}
            how.append(f"crawl_audit {crawl['origin']}: {crawl['pages_crawled']} pages ({'complete' if crawl.get('crawl_complete') else 'partial'}), "
                       f"sitemap {crawl['sitemap_url_count']} URLs")
        acc_args = (["ai_access.py", a.url, "--json"] + (["--no-probe"] if a.no_probe else [])
                    + (["--allow-private"] if a.allow_private else []))
        access = run_tool(acc_args)
        if access:
            (audits / f"ai-access-{stamp}.json").write_text(json.dumps(access, indent=2), encoding="utf-8")
            for f in access["findings"]:
                findings.append({**f, "source": "ai_access", "where": "site"})
            measured.add("ai_access")
            how.append("ai_access: robots.txt per crawler" + ("" if a.no_probe else " + spoofed-UA probes (hints, not proof)"))
        if a.psi:
            perf = psi(a.url)
            how.append("PageSpeed Insights mobile" + (f" (failed: {perf['error'][:80]})" if perf.get("error") else ""))
            if perf_score(perf) is not None:
                measured.add("performance")

    # de-duplicate project-level duplicates (e.g. same code from scan and crawl stays separate by source; fine)
    sc = score_categories(findings, pages, measured)
    if "performance" in measured:
        sc["performance"]["score"] = perf_score(perf)
        for m, v in (perf.get("field") or {}).items():
            if v.get("category") == "SLOW":
                sc["performance"]["issues"][f"cwv_{m.lower()}_poor"] = {"severity": "high", "message": f"{m} p75 {v['p75']} is poor ({v['scope']} field data)", "count": 1}
                findings.append({"severity": "high", "code": f"cwv_{m.lower()}_poor", "message": f"{m} p75 {v['p75']} poor ({v['scope']})", "source": "psi", "where": a.url})
    num = sum(WEIGHTS[c] * v["score"] for c, v in sc.items() if v["score"] is not None)
    den = sum(WEIGHTS[c] for c, v in sc.items() if v["score"] is not None)
    overall = round(num / den) if den else None

    # top issues: unique by code, worst first, then by frequency
    agg: dict[str, dict] = {}
    for f in findings:
        e = agg.setdefault(f["code"], {**f, "count": 0, "wheres": []})
        e["count"] += 1
        if len(e["wheres"]) < 3 and f.get("where"):
            e["wheres"].append(f["where"])
        if ORDER[f["severity"]] < ORDER[e["severity"]]:
            e["severity"], e["message"] = f["severity"], f["message"]
    ranked = sorted(agg.values(), key=lambda x: (ORDER[x["severity"]], -x["count"]))
    for r in ranked:
        r["where"] = ", ".join(r["wheres"]) + (f" (+{r['count'] - len(r['wheres'])} more)" if r["count"] > len(r["wheres"]) else "")
    top = [r for r in ranked if r["severity"] in ("critical", "high")][:8]
    backlog = [r for r in ranked if r["severity"] not in ("info",)]

    target = " + ".join(x for x in [a.path and f"code: {Path(a.path).resolve().name}", a.url and f"site: {a.url}"] if x)
    title = f"SEO/GEO audit — {datetime.now().strftime('%b %d, %Y')}"
    L = [f"# {title}", "", f"- Target: {target}", f"- **Readiness: {overall if overall is not None else '—'}/100** "
         "(measured categories only; not a ranking/citation forecast)", "", "## Fix first", ""]
    L += [f"{i}. **{t['severity']}** `{t['code']}` — {t['message']}" + (f"  \n   _{t['where']}_" if t.get("where") else "") for i, t in enumerate(top, 1)] or ["No critical or high issues."]
    L += ["", "## Scorecard", "", "| Category | Weight | Score | Main issues |", "|---|---|---|---|"]
    for c, v in sc.items():
        iss = ", ".join(k for k, i in sorted(v["issues"].items(), key=lambda kv: ORDER[kv[1]["severity"]]) if i["severity"] != "info")[:120]
        L.append(f"| {LABELS[c]} | {WEIGHTS[c]} | {'not measured' if v['score'] is None else v['score']} | {iss or '-'} |")
    if perf and not perf.get("error"):
        L += ["", f"Performance: lab score {perf.get('lab_score')} | field: " +
              (", ".join(f"{k} p75 {v['p75']} ({v['category']}, {v['scope']})" for k, v in perf["field"].items()) or "no CrUX data (low traffic)") +
              f" | lab: {perf.get('lab')}"]
    L += ["", "## Backlog", "", "| Sev | Code | Count | Detail | Where |", "|---|---|---|---|---|"]
    L += [f"| {b['severity']} | `{b['code']}` | {b['count']} | {b['message'][:150].replace('|', '/')} | {b['where'][:100]} |" for b in backlog]
    L += ["", "## How this was made", ""] + [f"- {h}" for h in how] + [f"- Raw outputs: `{audits.as_posix()}/*-{stamp}.json`"]
    md = "\n".join(L) + "\n"
    day = datetime.now().strftime("%Y-%m-%d")
    (reports / f"audit-{day}.md").write_text(md, encoding="utf-8")
    (reports / f"audit-{day}.html").write_text(
        to_html(title, sc, overall, top, backlog, {"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "target": target, "how": " · ".join(how)}),
        encoding="utf-8")
    print(md)
    print(f"[saved] {(reports / f'audit-{day}.md').as_posix()} and .html", file=sys.stderr)
    if a.fail_on and any(ORDER[f["severity"]] <= ORDER[a.fail_on] for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
