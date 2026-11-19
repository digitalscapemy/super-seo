# `/super-seo` full audit + improvement (multi-agent playbook)

The orchestrator (the main Claude session running this skill) coordinates a team of specialist agents installed with
the skill (`super-seo-*` in the Claude Code agents folder), merges their findings, then implements the fixes.

## Team
| Agent | Owns | Main tools |
|---|---|---|
| `super-seo-code` | Codebase SEO plumbing, drafted code fixes | project_scan, source reading |
| `super-seo-technical` | Crawl & index health, redirects, sitemap, hreflang, drift | crawl_audit, hreflang_check, drift, google `inspect` |
| `super-seo-geo` | AI crawler access, WAF, llms.txt, citability, entity/sources, AI-answer gaps | ai_access, content_check, ai_visibility, WebSearch |
| `super-seo-content` | Page inventory, on-page quality, metadata rewrites, internal links | crawl JSON, content_check, saas-pages spec |
| `super-seo-schema` | JSON-LD validation and ready-to-paste markup | crawl JSON, source, schema templates |
| `super-seo-performance` | CWV field/lab, Lighthouse, rendering diff, screenshots, images, agent-UX | google/lighthouse/browser extensions, image_audit |
| `super-seo-competitors` | Competitors, SERP clusters, page opportunities, prospects | gsc_report, WebSearch |
| `super-seo-fixer` | Implements approved fixes (one instance per non-overlapping file set) | Edit, build/lint, project_scan |

If the agents are not installed (e.g. the skill was copied without `agents/`), run the same specialist instructions with
general-purpose agents by pasting the matching `agents/super-seo-*.md` body into each prompt.

## Phase 0 — Prepare (orchestrator, ~2 min)
1. Locate the project root and `SKILL_DIR` (the folder containing this skill's SKILL.md). Read or create
   `.seo/project.md` (Step 0 in SKILL.md); ask at most ONE question for facts that cannot be inferred.
2. `python "<SKILL_DIR>/scripts/extensions.py" --json` → note active extensions; check your own tool list for MCP
   prefixes listed in manifests.
3. Pick the URL: a production build on localhost if the project can be built (`npm run build && npm run start`,
   `vite build && vite preview`, `php artisan serve`; run it in the background), plus the live domain from
   project.md for access/field-data checks. If neither is possible, run code-only and say so.
4. Baseline: `python "<SKILL_DIR>/scripts/audit.py" --path <PROJECT> --url <URL> [--psi if public]`. Keep the JSON
   paths; every agent reuses them instead of re-crawling.

## Phase 1 — Specialists in parallel
Spawn these in **one message** so they run concurrently (skip ones that clearly don't apply, e.g. performance when
there is no URL): `super-seo-code`, `super-seo-technical`, `super-seo-geo`, `super-seo-content`, `super-seo-schema`,
`super-seo-performance`, `super-seo-competitors`.
Prompt template for each:
```
SKILL_DIR: <abs path>      PROJECT: <abs path>      SEO_DIR: <abs path>/.seo
URL: <audited url>         LIVE: <production domain or none>
Baseline: <SEO_DIR>/audits/<crawl json>, <ai-access json>, <project-scan json>
Active extensions: <names>  MCP available: <prefixes>
Project context: <5-10 lines from .seo/project.md: product, audience, market/language, competitors, key pages, crawler policy>
Scope: follow your agent instructions. Write your findings file early. Return findings in the standard line format.
```
Large sites: split `super-seo-technical` and `super-seo-content` by section (e.g. /blog vs product pages) into two
instances each. Never give two agents the same write target.

## Phase 2 — Synthesise (orchestrator)
1. Merge all findings; de-duplicate by (code, URL/file). When agents disagree, re-check the evidence yourself.
2. Verify every critical/high item (open the file, fetch the URL). Drop anything unverified or mark it "unconfirmed".
3. Prioritise: **access/indexing blockers first**, then impact × confidence ÷ effort. Bounded fixes to demonstrated
   problems beat speculative big projects.
4. Each top recommendation carries: evidence, the fix, effort, how we will know it worked (the metric or check that
   should change), and what would prove it wrong.
5. Write `.seo/reports/super-seo-<YYYY-MM-DD>.md` per `reporting.md` (verdict → top fixes → scorecard → backlog →
   what else we checked → how this was made) and show the user the verdict + top fixes + the plan below.

## Phase 3 — Improve
Split the backlog into:
- **Auto-apply** (safe, mechanical, reversible): metadata/canonical/OG wiring, sitemap and robots code, environment-aware
  indexing, JSON-LD with confirmed facts, image attributes, `lang`, real 404s, internal links between existing pages,
  llms.txt from routes. Apply these without asking again (the user asked for improvement by running `/super-seo`).
- **Confirm first** (content and business decisions): new pages, copy rewrites, title/description rewrites on ranking
  pages, AI training-crawler policy, removing or merging pages, architecture changes (SSR migration, marketing/app split).
  Present them as a short numbered list and apply the ones the user approves.
Group auto-apply items by file ownership and spawn one `super-seo-fixer` per group in parallel (no shared files); small
change sets can be done directly by the orchestrator.

## Phase 4 — Verify & close
1. Rebuild and restart the local server; re-run `audit.py` with the same arguments; run `drift.py --latest` between the
   baseline and the new crawl.
2. Report before → after: readiness score, critical/high counts, fixed vs remaining items, any regressions (fix them).
3. Update `.seo/project.md` (durable facts only) and `.seo/research-log.md`; leave the AI-visibility collection
   checklist for the user if a tracker was created.
4. Do not commit unless the user asks; when they do, follow the repository's own commit conventions.

## Argument shortcuts
| Command | Runs |
|---|---|
| `/super-seo` | Phases 0–4 (full audit + improvement) |
| `/super-seo audit` | Phases 0–2 only (no code changes) |
| `/super-seo fix` | Phase 3–4 using the latest report's backlog |
| `/super-seo geo` | Phase 0 + `super-seo-geo` (+ `super-seo-content` for citability fixes) |
| `/super-seo page <path or url>` | Workflow C for one page |
| `/super-seo launch` | Launch gate (launch-monitoring.md) |
| `/super-seo integrations` | Workflow H (admin integrations) |
| `/super-seo extensions` | Show extension status and setup steps |
| `/super-seo quick <url>` | Baseline `audit.py` only, summarised in chat |
