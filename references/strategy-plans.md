# 90-day SEO + GEO roadmaps by business type

Use after the first audit (workflow A) when the user asks "what should we do for the next quarter?". Pick the plan
that matches `.seo/project.md`, trim it to the team's capacity, and save the chosen plan to
`.seo/reports/roadmap-<date>.md`. Every KPI must be measurable from Search Console, Bing Webmaster, analytics or the
skill's own scripts. No traffic or ranking forecasts: set direction and thresholds, then measure.

## Contents
0. Rules for every plan · 1. SaaS B2B (sales-led) · 2. SaaS self-serve (product-led) · 3. E-commerce
4. Local / service business · 5. Publisher / content site · 6. Agency / portfolio · 7. Marketplace / directory
8. Developer tool / API product · 9. Review cadence

## 0. Rules for every plan
- **Month 1 is always foundations**: fix critical/high findings from `audit.py` before writing new pages. New content on
  a site with noindex leaks, CSR-only pages or blocked AI crawlers is wasted.
- Baseline on day 1: `audit.py --path . --url <prod> --psi`, `crawl_audit.py` (saved to `.seo/audits/`),
  `ai_access.py`, a GSC export into `.seo/gsc/` + `gsc_report.py`, `entity_check.py --name <brand> --domain <domain>`,
  and an AI-visibility baseline (workflow D, `ai_visibility.py`).
- After each release: `drift.py --latest .seo/audits --fail-on critical`. Monthly: `google_updates.py correlate --gsc .seo/gsc`
  to separate Google update effects from site changes (a date overlap is a hypothesis, not proof).
- KPIs are reported as before/after with dates; brand and non-brand clicks are always split (`gsc_report.py --brand`).
- Content is written through workflow C (brief → answer-first sections → `content_check.py`), one page per intent.
- Programmatic page families only through the gates in `saas-pages.md` §4.

### KPI glossary (all free sources)
| KPI | Source | Notes |
|---|---|---|
| Non-brand clicks / impressions | GSC (`gsc_report.py --brand`) | Primary SEO outcome; compare 28-day windows |
| Striking-distance queries moved into top 5 | GSC position 5-20 list from `gsc_report.py` | Count of queries, not a forecast |
| Indexed vs submitted pages | GSC Pages report / google extension URL Inspection | Coverage health |
| Critical/high findings open | `audit.py`, `crawl_audit.py` | Target 0 critical by day 30 |
| CWV pass rate (field) | CrUX via google extension; PSI lab as fallback | Field data needs traffic |
| AI mention / citation rate | `ai_visibility.py score` on a fixed prompt set | Same prompts each month |
| AI referral sessions | GA4 / PostHog / Matomo / Umami extensions | chatgpt.com, perplexity.ai, gemini, copilot referrers |
| Conversions from organic | analytics (signup, demo, purchase, lead) | Define the event once, keep it fixed |
| Entity consistency issues | `entity_check.py` findings | Target 0 high |

### Sizing the plan to the team
| Capacity | Month 1 | Months 2-3 |
|---|---|---|
| Solo founder (2-4 h/week) | Critical/high fixes only, entity + `sameAs`, GSC/Bing setup | 1 money page or update per week; skip original-data assets |
| Small team (1 marketer + dev time) | Full month-1 list | The plan as written, 2-3 new pages per week |
| Content team | Full month-1 list + content inventory | Plan as written plus a refresh cadence for the top 50 pages |

### Hybrid businesses
- SaaS with a marketplace or directory side: run plan 1/2 for the marketing site and plan 7's index rules for the
  listings; keep them in separate sitemaps so coverage is measurable per family.
- Local business that sells online: plan 4 for locations, plan 3 for products; one Organization `@id`, LocalBusiness per
  location, Product pages linked from location pages only where stock is local.
- Agency that ships a product: plan 6 for services, plan 2 or 8 for the product; separate nav sections, shared entity.
- Pick one primary KPI per family; a blended "organic traffic" number hides which part is moving.

## 1. SaaS B2B (sales-led, demo requests)
| | |
|---|---|
| Goals | Rank and be cited for "<category> for <industry>", "<competitor> alternative", "<category> pricing"; feed demo pipeline |
| KPIs | Non-brand clicks to money pages (features, use cases, vs, pricing); demo requests from organic; AI mention rate on 15-25 buying prompts; 0 critical findings |
| Page families | Home, pricing (or "how pricing works" with real ranges), features, industry/role use cases, `/vs/` and `/alternatives/`, integrations, security/compliance, customer stories, glossary for real category terms |
| Schema set | Organization + WebSite + SoftwareApplication (home), `offers` on pricing, BreadcrumbList, Article on vs/guides, FAQ only where visible |

| Month | Actions |
|---|---|
| 1 | Audit + fix (workflow A/B); marketing vs app split, app routes noindex; Organization `sameAs` + `entity_check.py` fixes; `ai_access.py` (AI search/user bots allowed); pricing in HTML text; GSC + Bing verified, IndexNow set up |
| 2 | 3-5 industry/role use-case pages from sales-call language; 2-3 `/vs/` pages with dated, sourced tables (`saas-pages.md` §3); security/compliance page; customer story with numbers |
| 3 | Integration pages for the top 5 integrations with real setup steps; one original-data asset (benchmark, survey, calculator); outreach to "best <category>" listicles and review sites (`research-free.md` §5); re-run AI-visibility prompts |

AI-visibility actions: neutral buying prompts in `tracker.json`; earn listings on G2/Capterra-type directories and
listicles AI answers already cite; consistent facts (founding year, HQ, pricing model) across site, schema, LinkedIn,
Wikidata if notable. **Don't**: gate every page behind a demo form, publish "vs" pages with unsourced claims, buy links,
or spin one use-case template across 50 industries.

## 2. SaaS self-serve (product-led, free plan or trial)
| | |
|---|---|
| Goals | Own "<category> free", "<job-to-be-done> tool", templates and how-to queries; turn readers into signups |
| KPIs | Organic signups (analytics event); non-brand clicks to templates/tools/how-to; striking-distance queries moved to top 5; AI citation rate on "best free <category>" prompts |
| Page families | Pricing with free-plan limits, features, templates/library pages, free tools/calculators, how-to guides tied to features, `/vs/` + alternatives, changelog, public help center |
| Schema set | SoftwareApplication with free Offer (`price: "0"`), BreadcrumbList, Article/BlogPosting with author, `releaseNotes` link to changelog |

| Month | Actions |
|---|---|
| 1 | Audit + fix; help center and templates server-rendered and indexable; signup/login/app noindex; `image_audit.py` on landing pages; CWV fixes on home and pricing |
| 2 | 10-20 template or tool pages that pass the programmatic gates (unique data per page); 4-6 how-to guides from GSC question queries (`gsc_report.py`); internal links guide → feature → signup |
| 3 | Comparison pages for the 3 most searched competitors; refresh striking-distance pages; changelog entries per release; AI prompt re-run and gap actions (workflow D) |

AI-visibility actions: quotable one-sentence product definition on home; feature limits in tables (AI answers quote
limits); llms.txt pointing at docs/help (zero score weight, cheap). **Don't**: hide the free plan's limits, index
thousands of empty template pages, or let the app shell (CSR) replace marketing pages.

## 3. E-commerce
| | |
|---|---|
| Goals | Category and product pages rank for commercial queries; merchant listings eligible; stable crawl of facets |
| KPIs | Non-brand clicks to category + product pages; products with valid Product/Offer markup; crawl waste (facet URLs indexed) trending down; organic revenue (analytics) |
| Page families | Category pages with unique intro + buying guidance, product pages, brand pages, buying guides, comparison/"best X for Y" guides, store policies (shipping, returns) |
| Schema set | Product / ProductGroup + Offer (price, availability, shipping, returns), BreadcrumbList, Organization with `hasMerchantReturnPolicy`; reviews only if real and on-page (`ecommerce.md`) |

| Month | Actions |
|---|---|
| 1 | `crawl_audit.py` on categories + top products; faceted navigation rules (canonical/noindex/robots) per `ecommerce.md`; out-of-stock handling; shopify extension if Shopify; image weight fixes |
| 2 | Rewrite top 20 category intros with buying criteria; product page template: specs table, FAQ from support tickets, original photos; Merchant Center feed matches page prices |
| 3 | 3-5 buying guides linked to categories; internal links from guides; seasonal pages published 6-8 weeks before the season; AI prompts for "best <product> for <need>" |

AI-visibility actions: spec tables and clear return/shipping facts (AI shopping answers quote them); consistent
prices between feed, page and schema. **Don't**: index every filter combination, copy manufacturer descriptions
verbatim, mark up ratings you don't display, or delete seasonal URLs each year.

## 4. Local / service business (clinic, contractor, firm, multi-location)
| | |
|---|---|
| Goals | Local pack + organic for "<service> <city>", "<service> near me"; calls and bookings |
| KPIs | Clicks to service and location pages; calls/bookings from organic (analytics events); business profile actions (from the profile dashboard, recorded manually); NAP consistency issues = 0 |
| Page families | One page per service, one page per real location (unique details, staff, photos, directions), service-area pages only with real local proof, reviews page, FAQ, pricing guide |
| Schema set | LocalBusiness subtype per location (address, geo, openingHours, telephone), Service, BreadcrumbList, Organization linking locations (`local-seo.md`) |

| Month | Actions |
|---|---|
| 1 | Business profile complete and verified; NAP identical on site, schema, profile and top citations; location pages server-rendered with embedded map and hours; mobile check with the browser extension |
| 2 | Service pages with process, price ranges, time frames, before/after photos; review request process (no incentives); top 10 local citations cleaned |
| 3 | Local proof content (projects by suburb, case notes); local press/sponsorship mentions; FAQ from real calls; AI prompts "best <service> in <city>" |

AI-visibility actions: AI answers for local queries lean on profiles, reviews and directories; keep them consistent and
current. **Don't**: create doorway pages for cities you don't serve, fake addresses, keyword-stuff the business name,
or buy reviews.

## 5. Publisher / content site
| | |
|---|---|
| Goals | Topical authority in 2-3 clusters; resilience to core updates; AI citations for explainer queries |
| KPIs | Non-brand clicks per cluster; share of pages with clicks in 90 days (prune signal); pages with author + updated date; AI citation rate on cluster prompts; revenue per session (analytics) |
| Page families | Pillar + cluster articles (`content-briefs.md`), author pages, original data/research, glossary, updated "best of" guides with methodology |
| Schema set | Article/NewsArticle/BlogPosting with author Person (url, sameAs), Organization publisher, BreadcrumbList, ProfilePage for authors |

| Month | Actions |
|---|---|
| 1 | Audit + fix; `google_updates.py correlate` on 16 months of GSC dates to see which updates moved the site; content inventory: keep / update / merge / remove (with redirects) |
| 2 | Update the top 30 decaying pages with first-hand evidence, dates, sources; merge cannibalising pages (`gsc_report.py`); author bios with real credentials |
| 3 | One original study or dataset per cluster; 8-12 new cluster pages from SERP-overlap clustering (`research-free.md` §3); AI prompt re-run per cluster |

AI-visibility actions: answer-first sections with sourced numbers (`geo-ai-search.md` §4); `content_check.py` on every
updated page. **Don't**: mass-publish AI-written articles without first-hand value, change dates without changing
content, or chase trending topics outside the clusters.

## 6. Agency / portfolio / consultancy
| | |
|---|---|
| Goals | Rank for "<service> agency <city/industry>"; case studies cited as proof; leads |
| KPIs | Clicks to service + case-study pages; qualified leads from organic; entity consistency (founders, HQ, services) |
| Page families | Service pages, industry pages backed by case studies, case studies with numbers and client permission, team/founder pages, pricing or engagement models, process page |
| Schema set | Organization or ProfessionalService, Person for founders/team (sameAs), Article for case studies, BreadcrumbList |

| Month | Actions |
|---|---|
| 1 | Audit + fix; one URL per service (not one long services page); founder/team pages with real credentials; `entity_check.py` for brand and founders' public profiles |
| 2 | 3-5 case studies: problem, approach, measured result, client quote; industry pages only where 2+ case studies exist |
| 3 | Thought-leadership pieces with original data from client work (anonymised, permitted); speaking/podcast mentions; directory listings where clients actually search |

AI-visibility actions: AI answers for "best <service> agency" quote directories and case-study numbers; make both
easy to find. **Don't**: publish client logos without permission, claim awards you can't link, or build city pages
without local work.

## 7. Marketplace / directory
| | |
|---|---|
| Goals | Category × location (or category × attribute) pages that rank because each has unique inventory; listing pages indexed selectively |
| KPIs | Indexed share of quality-gated pages; clicks per page family; thin pages (below inventory threshold) noindexed; supply-side signups from organic |
| Page families | Category hubs, category × location pages with minimum inventory, listing detail pages, comparison/guide content per category, supplier landing pages |
| Schema set | ItemList on hubs, the listing's own type (Product, LocalBusiness, JobPosting, Event...) on detail pages, BreadcrumbList, Organization + WebSite |

| Month | Actions |
|---|---|
| 1 | Crawl budget audit: `crawl_audit.py` + sitemap vs indexed; index rules (minimum N listings, unique content, else noindex); expired listing handling (410 or redirect); canonical rules for sort/filter |
| 2 | Upgrade top 50 hub pages: unique intro, stats computed from inventory (price ranges, counts), FAQs; internal linking hub ↔ listing ↔ related hubs |
| 3 | Programmatic expansion only for combinations that pass the gates; guides per top category; AI prompts "best <category> in <place>" and "where to find <X>" |

AI-visibility actions: computed, current facts (counts, price ranges, dates) are what AI answers quote; show the
"as of" date. **Don't**: index empty or near-empty combinations, scrape listings from other sites, or let filters create
infinite URL spaces.

## 8. Developer tool / API product
| | |
|---|---|
| Goals | Docs and reference rank for "<task> in <language>", error messages and integration queries; AI coding assistants recommend and use the product correctly |
| KPIs | Clicks to docs/guides; docs pages indexed; signups/API keys from organic; AI mention rate on "how do I <task> with <category>" prompts; GitHub/package referrals (analytics) |
| Page families | Docs (SSG, versioned), API reference from OpenAPI, quickstarts per language/framework, integration guides, error-code pages, examples/templates, changelog, comparison pages, status page |
| Schema set | TechArticle on docs, SoftwareApplication (or SoftwareSourceCode for SDKs), BreadcrumbList, Organization `sameAs` incl. GitHub |

| Month | Actions |
|---|---|
| 1 | Docs server-rendered and indexable; one topic per URL; canonical across versions; llms.txt + Markdown versions of docs (`ai_access.py` checks Markdown negotiation); `entity_check.py` (GitHub P2037 on Wikidata, `sameAs`) |
| 2 | Quickstarts for the top 3-5 languages/frameworks; error-code pages from real support tickets; code samples that run; "copy as Markdown" on docs |
| 3 | Integration guides with partner tools; comparison pages with benchmarks you can reproduce; YouTube walkthroughs with chapters (youtube extension `video-seo`); AI prompt re-run including coding-assistant prompts |

AI-visibility actions: AI coding assistants read docs, READMEs, package pages and Q&A; keep them consistent and
current (version numbers, install commands). **Don't**: block docs from AI user agents, ship docs as a CSR-only app,
leave old versions indexable without canonicals, or publish benchmarks without methodology.

## 9. Review cadence
| When | Do |
|---|---|
| Weekly | Ship the month's actions; `drift.py` after each release |
| Day 30 | Re-run the baseline commands; critical findings must be 0; adjust month 2 |
| Day 60 | GSC 28-day comparison (brand vs non-brand); `google_updates.py correlate`; AI prompt re-run |
| Day 90 | Full before/after report (`reporting.md`): what moved, what didn't, next quarter's plan; append to `.seo/research-log.md` |

If a KPI does not move after 90 days, check in this order: indexing (is the page indexed?), intent (does the page type
match the SERP? see `sxo.md`), competition (who ranks instead and why), then content depth. Do not conclude a page
"failed" from fewer than 28 days of data or from a window that overlaps a core update rollout.
