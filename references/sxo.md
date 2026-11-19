# SXO: search experience optimisation

Ranking gets the click; the page has to match what the searcher expected and let them act. Use this when a page ranks
but does not convert, when impressions are high and CTR or engagement is low, or before writing a page (workflow C).
Inputs: the target query, the live SERP (WebSearch, read 2-3 times), `.seo/project.md` (audience, offer), and
desktop + mobile screenshots from the browser extension.

## Contents
1. SERP page-type consensus · 2. Mismatch severities · 3. Persona checks · 4. Above-the-fold checklist
5. CTA and form friction · 6. Before/after wireframe format · 7. Output

## 1. SERP page-type consensus
Classify each of the top 10 organic results (ignore ads; count SERP features separately) as one type:

| Type | Recognise it by |
|---|---|
| landing | Product/service marketing page: hero, value proposition, CTA (signup, demo, quote) |
| product | One purchasable item or plan: price, specs, buy/add to cart |
| category | List of products/listings with filters (e-commerce category, directory hub) |
| blog | Article/guide answering a question; byline, date, headings |
| comparison | "X vs Y", "best X", "X alternatives": tables across several products |
| tool | Interactive calculator, generator, checker, converter, template that works on the page |
| video | YouTube or video-first page |
| forum | Reddit, Quora, community threads, Q&A |
| docs | Documentation, API reference, help-center article |

Consensus = share of the most common type in the top 10:

| Share of top type | Reads as | What to build |
|---|---|---|
| > 60% | **Strong consensus** | Build that page type. Fighting it rarely works |
| 40-60% | **Mixed** | Top type is primary; borrow the second type's elements (e.g. a landing page with a comparison table) |
| < 40% | **Fragmented** | Intent is unclear; Google is testing. Pick the type that fits the business, add answer-first content, and watch GSC |

Also record SERP features (AI Overview, local pack, video carousel, People Also Ask, shopping, forums block). A local
pack or video carousel is part of the intent even when organic results disagree. Record query, country, device and date
in `.seo/research-log.md`; SERPs change.

## 2. Mismatch severities
Compare the page you have (or plan) with the consensus type:

| Your page | SERP consensus | Severity | Typical fix |
|---|---|---|---|
| blog post | product / category | **critical** | Build or target the product/category page; the blog supports it with internal links |
| blog post | comparison | high | Rewrite as a comparison: table, criteria, verdict, "as of" dates |
| product / landing | informational (blog, docs, forum) | high | Target the query with a guide that links to the product; keep the product page for commercial queries |
| landing page | tool | high | Ship a working tool (calculator, template, checker) above the fold; marketing copy below |
| service page | local pack | medium | Location pages + LocalBusiness schema + business profile (`local-seo.md`) |
| landing | comparison | medium | Add an honest comparison section or a separate `/vs/` page |
| docs | blog / tutorial | medium | Add a task-led tutorial that links into the reference |
| any | video carousel present | low | Add a video with chapters (youtube extension `video-seo`) and embed it |
| matches | matches | none | Move on to persona and above-the-fold checks |

A critical/high mismatch usually explains "ranks on page 2 forever" better than missing keywords. Fix type before copy.

## 3. Persona checks
Build 4-7 personas from `.seo/project.md`, sales/support language, GSC queries and the SERP (no invented demographics).
Each persona: role, situation, the query they typed, what "done" means for them, their main objection.

Example personas for a B2B SaaS: evaluating buyer (comparing 3 tools), end user (wants to know if it does X),
budget owner (price, contract), technical evaluator (integrations, security, API), switcher (leaving a competitor),
first-timer (doesn't know the category name).

Score each persona on the page, 0-25 per dimension (100 total):

| Dimension | 0-25 question | Evidence to cite |
|---|---|---|
| **Relevance** | Does the page address this persona's query and situation? | Headline, section headings, examples |
| **Clarity** | Can they get the answer **within 10 seconds** (first screen, no scrolling or clicking)? | Screenshot of the first viewport |
| **Trust** | Would they believe it? Proof, specifics, dates, named sources, reviews, security/compliance, real people | Proof elements and their position |
| **Action** | Is the next step obvious, low-risk and suited to their stage? | CTA text, placement, friction |

Bands: 0-5 missing, 6-12 weak, 13-19 adequate, 20-25 strong. Report the lowest-scoring persona first: it is usually
the biggest win. Scores are a structured judgement, not a measurement; always show the evidence behind each score.

## 4. Above-the-fold checklist
Take screenshots with the browser extension (`render_check.py screenshot <url>`: desktop 1366x900, mobile 390x844)
and Read the PNGs. In the first viewport, on both devices:

- [ ] H1 states what it is, for whom, and the outcome (matches the query's wording, not only a slogan)
- [ ] One-sentence answer or definition visible without scrolling (the quotable sentence)
- [ ] Primary CTA visible, with a specific label ("Start free, no card" beats "Submit")
- [ ] Price or "from" price visible when the query is commercial and the price is public
- [ ] A proof element: customer logos with permission, review score with source, a number with a source
- [ ] For tool intent: the tool itself starts in the first viewport
- [ ] No cookie wall, interstitial or chat widget covering the content (intrusive interstitials hurt mobile users)
- [ ] Text readable on mobile (no tiny fonts, no zoom block: `render_check.py mobile`)
- [ ] Hero image is not the LCP bottleneck (`image_audit.py`: fetchpriority, no lazy-load, right size)
- [ ] Nothing jumps after load (layout shift from late banners or fonts)

## 5. CTA and form friction
| Check | Good | Friction signal |
|---|---|---|
| CTA count | One primary, one secondary per section | Five equal buttons competing |
| CTA match to stage | Readers of guides get a soft CTA (template, checklist); pricing readers get signup/demo | "Book a demo" as the only option on a how-to article |
| Label | Outcome + risk reducer ("Get my quote in 2 minutes") | "Submit", "Learn more" (also flagged by `crawl_audit.py` generic anchors) |
| Form fields | Only what the next step needs (email, maybe company) | Phone, budget, company size, job title for a free trial |
| Field details | Labels visible, correct input types, autofill works, errors inline | Placeholder-only labels, errors after submit |
| Commitment | "No card required", cancellation terms, what happens next | Surprise paywall or card request after signup |
| Speed | Form usable before third-party scripts load | Blank form until a widget loads |
| Mobile | Tap targets large, keyboard matches field type | Tiny checkboxes, desktop-width forms |
| Trust near the CTA | Privacy note, security badge you actually hold, response time | Nothing between the price and the card field |

Measure with analytics events (CTA click, form start, form submit) before and after a change; don't claim a lift
without those numbers.

## 6. Before/after wireframe description format
Describe layout changes in text so they can be implemented and reviewed in code (no image needed):

```
Page: /pricing  (query: "acme pricing", consensus: product 70% -> strong)
Device: mobile 390px

BEFORE (first viewport)
1. Hero: slogan "Work smarter" + illustration (LCP 3.8 s)
2. CTA "Learn more" (below the fold on mobile)
Issues: no prices visible, no proof, generic CTA; Clarity 6/25 for "budget owner"

AFTER (first viewport)
1. H1 "Acme pricing: Free plan, Pro from RM29/month"
2. Plan cards (Free / Pro / Team) with price, 3 key limits each, billing toggle (prices also in HTML)
3. Primary CTA "Start free, no card" on Free; secondary "Compare plans" anchor
4. Proof strip: "4.7/5 on <review site>, 1,200 reviews" (linked source)
Below the fold: full comparison table, FAQ on billing, refund policy, contact sales
Implementation: components/pricing/Hero.tsx, PlanCard.tsx; SoftwareApplication offers in JSON-LD
Expected check: Clarity >= 20 for budget owner; verify with screenshot + analytics events after release
```

Rules: number the blocks in visual order, name the component/file to change, keep one "Issues" line per state, and
state the check that proves the change (screenshot, persona re-score, analytics event). No promised conversion uplift.

## 7. Output
In the report (`reporting.md` spine): per page a short table (query, consensus type and share, your type, mismatch
severity), the persona score table with evidence, the above-the-fold checklist results, and up to 3 wireframe
descriptions for the highest-impact changes. Critical/high mismatches go to the top recommendations; persona and
friction fixes go to the backlog with effort estimates.
