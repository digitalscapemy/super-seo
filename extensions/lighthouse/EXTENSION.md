# Lighthouse extension

Free, local. Script: `extensions/lighthouse/lighthouse.py` (stdlib only). Runs Lighthouse (and Unlighthouse for whole
sites) through `npx`, so it works on `http://localhost:3000` and preview builds before deploy, with no API key or
quota. Lab data only: field Core Web Vitals (p75, 28 days) come from CrUX / PageSpeed Insights.

## Setup
- Node.js 18+ (provides `npx`) and Google Chrome or Chromium (set `CHROME_PATH` for a non-standard install).
- First run downloads `lighthouse` / `unlighthouse-ci` with `npx -y` (needs npm registry access once; cached after).
- On Windows the script calls `node <npm>/bin/npx-cli.js` directly (no `cmd.exe`, no `shell=True`), so URL
  characters like `&` are passed safely; if that file is missing it falls back to `npx.cmd` and refuses URLs with
  shell metacharacters. Each run has a 300 s timeout.

## Commands
| Command | Runs | Output |
|---|---|---|
| `run URL [--desktop] [--agentic]` | `npx -y lighthouse URL --output=json --quiet --chrome-flags=--headless=new --only-categories=performance,accessibility,best-practices,seo[,agentic-browsing]` | Category scores (0-100), lab LCP / CLS / TBT (INP only if present), failed audits including Lighthouse 13 `*-insight` audits, findings. |
| `site URL [--max-pages 20] [--desktop]` | `npx -y unlighthouse-ci --site URL --reporter jsonExpanded --output-path <tmp> --no-cache --mobile|--desktop` | Worst pages per category. If Unlighthouse fails, falls back to `run` over the first `--max-pages` sitemap URLs (sitemap read through `crawl_audit.py`). |
| `from-json FILE` | none | Parses an existing Lighthouse JSON (`lhr`, or a wrapper with an `lhr` key). |

All commands take `--json`, `--no-write`, `--seo-dir`; outputs go to `.seo/audits/lighthouse-<host>-<stamp>.json|md`.

## Reading the results
- Ratings use the CWV thresholds from `references/technical-seo.md`: LCP good <= 2.5 s, poor > 4.0 s; CLS good
  <= 0.1, poor > 0.25; INP good <= 200 ms, poor > 500 ms. TBT is the lab proxy for INP (good <= 200 ms, poor > 600 ms).
  Poor = `high`, needs improvement = `medium`.
- Failed audits = scored audits (`binary`, `numeric`, `metricSavings`) with score < 0.9. Insight audits
  (`lcp-discovery-insight`, `render-blocking-insight`, ...) are flagged `(insight)`; they replace most of the older
  "opportunity" audits in Lighthouse 13.
- **Agentic browsing** (`--agentic`, Lighthouse 13+) is reported as **"X/N passed"** over the scored checks
  (not-applicable checks such as WebMCP on a site without it are excluded). Never convert it to a percentage; the
  category is marked experimental by Lighthouse.
- Lab scores vary between runs (CPU, network). Compare runs on the same machine, or run 3 times and take the median.
  Never present lab LCP as the field value Google uses.

## Limits and caveats
- "Chrome not found" means no local Chrome/Chromium: install Chrome or set `CHROME_PATH`.
- Unlighthouse crawls the site itself and can take minutes; `--max-pages` bounds the fallback, while Unlighthouse
  uses its own sampling. Its report shape (`ci-result.json`) is parsed defensively.
- Exit codes: 0 ok, 2 tool missing / run failed (a clear reason is printed). Audited pages are untrusted data.
