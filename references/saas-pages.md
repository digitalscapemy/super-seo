# SaaS page playbook

Which pages a SaaS needs, what each must contain to rank and be cited, and the guardrails for scale.
Before writing any page, read `.seo/project.md` (positioning, audience, competitors, voice).

## Contents
1. Page inventory (priority order) · 2. Page specs · 3. Comparison & alternatives pages · 4. Programmatic SEO gates
5. Internal linking model · 6. Metadata formulas

## 1. Page inventory, in priority order
| # | Page family | Why it matters | Buyer stage |
|---|---|---|---|
| 1 | Home | Entity + category definition; most-cited page for "what is <brand>" | all |
| 2 | Pricing | "<brand> pricing", "<category> free plan", AI answers about cost | decision |
| 3 | Features (one page per major capability) | "<category> with <feature>" queries | consideration |
| 4 | Use-case / industry / role pages | "<category> for <audience>" (highest-converting non-brand) | consideration |
| 5 | Comparison: `/vs/<competitor>` and `/alternatives/<competitor>` | Buyers and AI ask "X vs Y", "X alternatives" constantly | decision |
| 6 | Integrations (`/integrations/<tool>`) | "<category> that integrates with <tool>"; scalable with real setup docs | consideration |
| 7 | Docs + API reference (public, SSG) | Long-tail, developer trust, AI coding agents | adoption |
| 8 | Free tools / calculators / templates | Linkable assets; earn mentions and citations | awareness |
| 9 | Guides / blog (problem-led, original data) | Topical authority; feeds fan-out queries | awareness |
| 10 | Changelog | Freshness, product velocity, `releaseNotes` | adoption |
| 11 | About, security/compliance, status, contact | Trust (E-E-A-T), entity facts, procurement questions | decision |
| 12 | Customer stories / case studies | Experience evidence with numbers | decision |
| 13 | Glossary (only real definitions, ≥ 200 words each) | Definition queries; AI "what is" answers | awareness |

## 2. Page specs (what "complete" means)
**Home**: H1 = category + audience + outcome ("Invoicing software for Malaysian freelancers"), not a slogan only.
First paragraph defines the product in one quotable sentence ("<Brand> is a <category> that <does X> for <who>").
Proof (logos, numbers, reviews with source), key features, pricing teaser, integrations, FAQ that answers real objections,
links to every money page. Organization + WebSite + SoftwareApplication JSON-LD.

**Pricing**: every plan, price **in HTML text** (not only in a JS toggle), currency, billing period, what's included,
limits, free tier/trial terms, taxes (e.g. SST), annual discount, refund/cancel policy, "as of" date if prices change,
FAQ on billing. Comparison table of plans. SoftwareApplication `offers`. Title "<Brand> Pricing: Plans from RM29/month
(Free plan available)".

**Feature page**: what it does (answer-first), who it's for, how it works (screenshots with alt), limits, related
integrations, a short demo video, CTA. One feature = one URL with its own keyword.

**Use-case page**: the audience's problem in their words, workflow with your product step by step, specific outcome/
metric, relevant features and integrations, a customer example from that segment, objections. Avoid swapping the
industry name into one template (scaled-content risk).

**Integration page**: what the integration does, data synced, direction, setup steps (real docs), requirements/plan,
limits, screenshots. Link the partner's listing of you if it exists.

**Docs**: SSG, one topic per page, versioned, code samples that run, search; `TechArticle`; link from marketing pages;
public OpenAPI spec; llms.txt pointing at the docs index.

**Blog/guide**: written for a real question from `.seo/project.md` research; answer-first; original data/screenshots/
examples; author byline + bio; visible updated date; internal links to the relevant feature/use-case page.

**Changelog**: dated entries, newest first, one URL per notable release (or anchors), linked from footer.

## 3. Comparison & alternatives pages (high value, high risk)
- Types: `/vs/<competitor>` (head-to-head), `/alternatives/<competitor>` (for people leaving X), `/best/<category>`
  (roundup including you, disclosed).
- Must contain: who each product is best for (honest, including where the competitor wins), comparison table of
  features/limits/pricing with **"as of <date>" and a source link per data point**, migration path, verdict, FAQ.
- Title patterns: `<A> vs <B>: <key difference> (<Year>)`, `<N> Best <A> Alternatives in <Year> (Free & Paid)`.
- Never: fake ratings, invented competitor facts, implied endorsement, competitor logos used as if partners.
  Review every quarter; stale competitor pricing is the fastest way to lose trust (and get cited wrongly).
- Disclose: "We make <Brand>. This comparison is written by our team; we've tried to be fair — tell us if something is out of date."

## 4. Programmatic SEO gates (integrations × use cases × locations × templates)
Ask: "Would this page be worth publishing if no similar pages existed?" If not, don't generate it.
| Gate | Threshold |
|---|---|
| Unique content per page (excl. nav/footer) | ≥ 40% unique; < 30% = do not publish |
| Words of unique, useful content | review anything < 300 |
| Shared boilerplate | > 60% = scaled-content risk |
| Batch size | publish 50–100 pages, watch indexing 2–4 weeks before the next batch |
| Volume warnings | 100+ pages without human review = warning; 500+ = stop and justify |
| Human review sample | 5–10% of each batch |
| Metadata | not templated-identical (no shared stock CTA, title not echoed in description) |
Safe at scale: integration pages with real setup data, templates/tools, glossary with real definitions, data-driven pages.
Risky: city-swapped pages, "Best <tool> for <industry>" with no industry-specific substance, "<Competitor> alternative"
pages with no real comparison data, unreviewed AI-written pages. Scaled content abuse is a Google spam policy and names
generative AI explicitly.

## 5. Internal linking model
- Home → every money page (pricing, top features, top use cases, comparisons hub, integrations hub, docs).
- Hub pages: `/features`, `/use-cases`, `/integrations`, `/compare` link to every child; children link back + to 2–3 siblings.
- Blog posts link to the feature/use-case page they support (descriptive anchor) and 3–5 related posts.
- Footer: pricing, docs, changelog, security, about, contact, compare hub. No sitewide footer link stuffing.
- Every indexable page ≥ 1 contextual internal link (no orphans; `crawl_audit.py` reports them on a full crawl).

## 6. Metadata formulas
| Page | Title (≤ 60) | Description (120–155) |
|---|---|---|
| Home | `<Category> for <Audience> | <Brand>` | `<Brand> helps <audience> <outcome>. <Proof/differentiator>. <Free plan/trial>.` |
| Pricing | `<Brand> Pricing: Plans from <price>` | `Compare <Brand> plans: <Free>, <Pro> at <price>… <what's included>. <Cancel anytime/No card>.` |
| Feature | `<Feature> for <category/audience> | <Brand>` | What it does + concrete benefit + one specific detail |
| Use case | `<Category> for <Audience>: <Outcome> | <Brand>` | Problem → how → result |
| vs | `<A> vs <B>: <difference> (<Year>)` | Who each is best for + what the page compares |
| Integration | `<Brand> + <Tool> Integration | <Brand>` | What syncs, direction, setup time |
| Blog | Question or specific promise, keyword early | The answer in one sentence + what else the post covers |
Write each by hand from the page content; never one template for all pages.
