# Keyword, competitor and link research without paid tools

Data sources allowed: WebSearch, WebFetch, the codebase, Google Search Console (user export or API), Bing Webmaster
Tools, Google Trends, PageSpeed/CrUX. No paid keyword APIs. **Never invent search volume, KD or CPC** — write `unknown`
and use the proxies below.

## 1. Demand proxies (free)
| Signal | How | Reads as |
|---|---|---|
| Search Console | queries/pages export (`.seo/gsc/*.csv`) or API | Real demand for your site (best source) |
| Striking distance | GSC position 5–20 with ≥ 50 impressions (3 months) | Fastest wins: work these first |
| Autocomplete / PAA breadth | WebSearch the seed, read "People also ask" / related searches | Topic has many questions |
| SERP composition | Who ranks in top 10 | Big brands + UGC = hard; small blogs/forums = gap |
| Google Trends | relative interest, seasonality, country | Direction, not volume |
| Communities | Reddit/forum thread count and recency | Real pain, phrasing |
| Competitor page families | their sitemap / nav | What they bet on |

## 2. Keyword research workflow
1. Seeds from `.seo/project.md`: category, problems solved, use cases, audiences, integrations, competitor names
   (for vs/alternatives), local modifiers (Malaysia, LHDN, FPX, SST, BM terms).
2. Expand each seed (WebSearch): related searches, PAA, modifiers `best / vs / alternative / pricing / free / template /
   how to / for <audience> / Malaysia`.
3. Classify intent — **SERP intent wins over word cues**: informational, commercial (best/top/review/vs/alternatives/
   pricing), transactional (signup/free trial/template download), navigational/branded.
4. Prioritise: product/page fit > clear intent > SERP you can realistically compete in > demand signal. Prefer
   business-fit over the biggest term.
5. Map each keyword (cluster) to one existing URL, a proposed new page, or "not now".
Output: Opportunity (1–2 sentences) → Target now (keyword, intent, demand signal, page) → Why → Longer list →
Risks/unknowns → Next step. Save to `.seo/research-log.md`; reuse research < 30 days old instead of redoing it.

## 3. Clustering by SERP overlap
WebSearch each candidate; keep top-10 organic URLs (normalise host, strip `www`, query, trailing slash; ignore
Wikipedia/Reddit/YouTube when counting since they rank for everything).
| Shared top-10 URLs | Decision |
|---|---|
| 7–10 | Same page |
| 4–6 | Same cluster (one page, sections for each) |
| 2–3 | Related: separate pages that interlink (merge only if intent + page type match) |
| 0–1 | Separate |
Pre-group by intent to cut comparisons; a 10× demand gap → its own page. Cannibalisation only with evidence (GSC
query+page: one query splitting impressions across URLs).
Rank checks: read the SERP 2–3 times; a result seen once is "unconfirmed"; report "#11 (page 2)", "not in the first 20",
or "unknown" (failed lookup ≠ absent). Record query, country, date.

## 4. Competitor analysis
- Business competitors (sell to the same customers) ≠ SEO competitors (rank for your terms: publishers, directories).
  Verify a business competitor by reading its site.
- Market query set: 5–10 representative queries of mixed intent → recurring domains → group: product competitors,
  publishers/media, marketplaces/directories, communities, docs/resources.
- Deep dive: read the competitor's sitemap + nav; list page families (features, use cases, integrations, vs pages,
  templates, docs, blog themes); note what they do well and where they're thin (templated comparisons, missing use cases,
  stale pricing). Recommend a stronger angle; never copy.

## 5. Link & mention prospecting
- Queries: `<topic> resources`, `best <category> tools`, `<competitor> alternatives`, `<topic> statistics`,
  `<topic> templates`, `<category> for <audience>`, `"competitor.com" -site:competitor.com`.
- Highest yield: pages AI answers already cite for your prompts (from `ai-visibility.md`) and "best X" listicles in the SERP.
- Keep: editorial roundups, resource pages, directories, comparison pages, stats pages. Skip: spam, paid placements
  (flag them), unrelated forums.
- Angles: missing from a list (with real data), outdated info you can correct, a free tool/template, original data,
  expert quote. Contacts only from public sources with the URL cited; never invent emails or names.
- Output: Angle → prospects table (URL, site, angle, contact path, priority) → 2–3 outreach drafts → order of sending.

## 6. Search Console without an API
Ask the user to export (Performance → Search results → Export) into `.seo/gsc/`: `queries-3m.csv`, `pages-3m.csv`,
optionally `queries-16m.csv` and query+page. Never claim GSC is connected or data is uploaded unless the file exists.
