# Structured data for SaaS (JSON-LD)

Implementation: `templates/nextjs/components/json-ld.tsx` (helpers: `siteGraph`, `softwareApp`, `breadcrumbs`,
`article`, `faq`) and `templates/jsonld/saas-graph.json` for non-React stacks.

## Rules
- JSON-LD only, `"@context": "https://schema.org"`, **server-rendered in the initial HTML** (JS-injected markup is
  processed late by Google and never seen by non-JS AI crawlers).
- One `@graph` per page with stable `@id`s: `https://domain/#organization`, `#website`, `#software`; reference them
  (`"publisher": {"@id": ".../#organization"}`) instead of repeating objects.
- Absolute URLs, ISO 8601 dates, real values only. Placeholders (`[REPLACE]`, `XXX`, lorem ipsum, `example.com`) must
  never ship — `crawl_audit.py` flags `jsonld_placeholder`.
- Markup must describe content **visible on the page**. Never mark up hidden FAQs, fake ratings, or prices not shown.
- Escape `<` when serialising (`JSON.stringify(x).replace(/</g, "\\u003c")`) so user content cannot break out of the script.
- Validate: Google Rich Results Test (what Google can use) and validator.schema.org (syntax). `crawl_audit.py` checks
  parse errors, minimum properties and deprecated types.

## Page → schema map
| Page | Types |
|---|---|
| Home | Organization + WebSite + SoftwareApplication (or WebApplication) |
| Pricing | SoftwareApplication with `offers` (one Offer per plan; free plan `price: "0"`) + BreadcrumbList |
| Features / use-case / integration pages | WebPage + BreadcrumbList (+ SoftwareApplication reference via `@id`) |
| Blog post / guide | BlogPosting or Article (headline, image, datePublished, dateModified, author Person with url) + BreadcrumbList |
| Docs page | TechArticle + BreadcrumbList |
| Comparison / alternatives / "best X" | Article + ItemList of the compared products (no third-party ratings) |
| Changelog | WebPage (+ `releaseNotes` on SoftwareApplication pointing here) |
| About / team | AboutPage + Organization (founders as Person with sameAs) |
| Contact | ContactPage + Organization `contactPoint` |
| Real user Q&A / community | QAPage or DiscussionForumPosting |
| Demo / tutorial video | VideoObject (name, description, thumbnailUrl, uploadDate, contentUrl/embedUrl) |

## Minimum properties
| Type | Must have |
|---|---|
| Organization | name, url, logo (ImageObject ≥ 112×112), sameAs (verified profiles) |
| WebSite | name, url, publisher |
| SoftwareApplication / WebApplication | name, applicationCategory, offers (price, priceCurrency) — Google's software rich result additionally needs aggregateRating or review |
| Article / BlogPosting | headline, image, datePublished, dateModified, author (Person/Organization object, not a string) |
| BreadcrumbList | itemListElement (position, name, item) |

## Ratings: do not fake them
`aggregateRating` only from real reviews collected about the product and shown on the page, with real counts.
Never copy G2/Capterra ratings into your own markup as if self-hosted, never hardcode a rating in a template, never rate
competitors. Self-serving review snippets for Organization/LocalBusiness are not displayed by Google anyway.

## Deprecated / restricted (don't recommend)
- **HowTo**: no rich results since 2023 → plain H2 steps.
- **FAQPage**: no FAQ rich results for most sites. Keep existing markup that matches visible Q&A (harmless); don't add it
  for SERP or AI gains.
- **Sitelinks search box** (WebSite `SearchAction`): retired by Google Nov 2024. Optional for other consumers only.
- SpecialAnnouncement, CourseInfo (→ Course), EstimatedSalary (→ JobPosting.baseSalary), LearningVideo (→ VideoObject),
  VehicleListing (→ Product), ClaimReview (no SERP display).
- `speakable`: Google beta for news publishers in Assistant only; not a general AI signal.

## sameAs priority (entity disambiguation)
Wikidata / Wikipedia (only if real) → LinkedIn company → GitHub org (dev tools) → YouTube → X → Crunchbase →
Product Hunt / G2 / Capterra profiles → App Store / Play / Chrome Web Store listings. Only profiles you control or that
are verifiably about you; every URL must resolve.
