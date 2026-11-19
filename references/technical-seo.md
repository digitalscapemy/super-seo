# Technical SEO rules (SaaS)

Thresholds below are what `crawl_audit.py` / `project_scan.py` enforce. Google publishes few hard limits; where a
number is a convention, say "may be truncated / is commonly recommended", never "Google requires".

## Contents
1. Indexability & canonicals · 2. robots.txt · 3. Sitemaps · 4. Rendering (SSR/CSR) · 5. Metadata · 6. Headings, links, URLs
7. Images · 8. Core Web Vitals · 9. hreflang · 10. Transport & headers · 11. Severity model

## 1. Indexability & canonicals
- Every indexable marketing page: HTTP 200 directly (no redirect), self-referencing `rel=canonical` (absolute URL, prod host),
  no `noindex`, reachable by a crawlable `<a href>` from another page, listed in the sitemap.
- One canonical per page. HTML canonical and HTTP `Link: rel=canonical` must agree. Canonical must not point to a
  redirect, a 404, a noindexed URL, or another host (staging, preview, www/non-www mix).
- `noindex` on a public page = **critical** unless intentional (thank-you, search results, filtered views).
  `noindex` on `/app`, `/dashboard`, `/login`, `/signup`, `/account`, `/api` = expected.
- Put canonical, robots meta, title, description and JSON-LD in the **initial server HTML**. Google may honour a raw-HTML
  `noindex` even if JS later removes it, may pick either canonical when JS changes it, and does not render JS on non-200 pages.
- `nosnippet` / `max-snippet:0` / `data-nosnippet` also remove the page (or the wrapped text) from AI Overviews / AI Mode.
- Preview/staging deployments must not be indexable: `X-Robots-Tag: noindex` (Vercel adds it on preview URLs automatically;
  Netlify/Cloudflare Pages/EasyPanel do not — add it), plus env-aware robots.txt. Never hardcode `*.vercel.app` or
  `localhost` in canonical, sitemap, OG, or `metadataBase`.
- Duplicate URL variants: http→https (301), www↔apex (pick one, 301), trailing slash (pick one, 301), tracking params
  (canonical to clean URL). Fixed canonicals can take ~2 weeks to settle in Google's duplicate clusters.

## 2. robots.txt
- Must return 200 `text/plain` at `/robots.txt`. 404 = everything allowed. **5xx = Google treats the whole site as
  disallowed** (and a 429 is treated like a server error).
- RFC 9309 group selection: a crawler obeys only the most specific group naming it; if any group names it, the `*`
  group is ignored entirely. So a bot-specific group must repeat every `Disallow` you need. Tokens are case-insensitive,
  matched whole (`GPTBot`, not `GPTBot/1.1`).
- robots.txt is a crawl preference, not access control. Protect `/app`, `/account`, `/billing` with auth; disallowing
  them only keeps well-behaved bots out (and Google can still index a disallowed URL from links, without content).
- Never `Disallow` CSS/JS needed to render public pages.
- Declare `Sitemap: https://domain/sitemap.xml` (absolute).
- **Cloudflare managed robots.txt**: Cloudflare can prepend a block (`# BEGIN Cloudflare Managed content`) disallowing
  AI crawlers. It is not in the repo file, so only a live fetch reveals it (`ai_access.py` detects it).

## 3. Sitemaps
- Only canonical, indexable, 200, same-host URLs. No app/auth routes, no redirects, no noindexed, no parameter URLs.
- ≤ 50,000 URLs and ≤ 50 MB uncompressed per file; otherwise a sitemap index (no nested indexes).
- `<lastmod>` = last significant content change (W3C datetime). Same timestamp on every URL (build time) = Google learns
  to ignore it. `<priority>` and `<changefreq>` are ignored by Google.
- Image sitemaps: only `image:loc` remains. News sitemaps: last 2 days only.
- Submit in Google Search Console and Bing Webmaster Tools after launch.

## 4. Rendering: the #1 SaaS failure
- Rule: **SSR/SSG for every public page; CSR only behind login.** Google renders JS (with delay, and only on 200 pages),
  but GPTBot, OAI-SearchBot, ClaudeBot, Claude-SearchBot, PerplexityBot and most AI crawlers fetch raw HTML only.
- CSR shell signals: `<div id="root"></div>` / `<div id="app"></div>` with < ~60 words of server text and several scripts.
  `crawl_audit.py` flags `csr_shell` (critical); `project_scan.py` flags `csr_spa` from the framework.
- Next.js App Router pages are server-rendered by default even with `'use client'` children; content fetched in
  `useEffect` is NOT in the HTML. Next.js streams `generateMetadata` for normal browsers but blocks for known bots
  (`htmlLimitedBots`), so a custom-UA fetch may see metadata in `<body>`; that is expected.
- Dynamic rendering (serving bots a different prerender) is a workaround Google no longer recommends; prefer real SSR/SSG.
- Keep HTML lean: Google reads the first 2 MB of an HTML file for indexing (15 MB is the crawler fetch cap). Watch huge
  inline JSON (RSC payload, `__NEXT_DATA__`), base64 images, inline SVG sprites. Flag > 1.5 MB.

## 5. Metadata
- Title: unique, 30–60 chars incl. brand suffix (`%s | Brand`), primary term early. "May be truncated" above ~60.
- Meta description: unique, 70–160 chars (aim 120–155), specific outcome + proof; must not open by repeating the title;
  don't end every page with the same stock CTA ("Try it free now.") — that is the signature of a bulk template.
- Open Graph: `og:title`, `og:description`, absolute `og:image` (1200×630), `og:url`; `twitter:card=summary_large_image`.
  These drive link previews in Slack/WhatsApp/X and in AI chat apps.
- `<html lang>`, `<meta name=viewport content="width=device-width, initial-scale=1">`, `<meta charset>`, favicon
  (`<link rel=icon>`, shows in Google results and many AI citations).

## 6. Headings, links, URLs
- One descriptive `<h1>` per page (multiple H1 = low severity; no Google penalty). No skipped levels (accessibility).
- Internal links: real `<a href>` (no `onClick` navigation, no `javascript:`), descriptive anchors (not "learn more"),
  icon links with `aria-label`. Key pages ≤ 3 clicks from home. No orphan pages.
- URLs: lowercase, hyphens, ≤ 100 chars, no session IDs, stable. Redirects: ≤ 1 hop, 301/308 for permanent.
- Back-button hijacking (re-pushing history on `popstate` to trap users) is a Google spam policy violation.

## 7. Images
- `alt` on every `<img>` (empty `alt=""` for decorative). 10–125 chars, describes the image, not a keyword slot.
- `width` + `height` (or CSS `aspect-ratio`) to prevent CLS. Modern formats (AVIF/WebP) via `<picture>`/`next/image`.
- **Never lazy-load the LCP/hero image**; give it `fetchpriority="high"` (Next: `<Image priority />`).
- Budgets: hero < 200 KB (critical > 700 KB), content images < 100 KB (critical > 500 KB).

## 8. Core Web Vitals (p75 field data, 28 days, CrUX)
| Metric | Good | Needs improvement | Poor |
|---|---|---|---|
| LCP | ≤ 2.5 s | 2.5–4.0 s | > 4.0 s |
| INP | ≤ 200 ms | 200–500 ms | > 500 ms |
| CLS | ≤ 0.1 | 0.1–0.25 | > 0.25 |
- INP replaced FID in March 2024; never mention FID. TTFB target < 800 ms. CWV is a modest ranking signal; it matters
  more for conversion.
- Field data: PageSpeed Insights / CrUX API (free; optional API key). Use PHONE form factor; a 404 from CrUX = not enough
  traffic, fall back to lab (Lighthouse). SPAs: CrUX counts hard navigations only.
- Fix map — LCP: preload/`fetchpriority` hero, compress, SSR the hero text, reduce TTFB (edge cache marketing pages).
  CLS: dimensions on media, reserve space for banners/embeds, `font-display: swap` + size-adjusted fallback.
  INP: break long tasks (< 50 ms), defer third-party scripts, avoid huge hydration on marketing pages (ship less JS).

## 9. hreflang (only if the site has language/region versions)
- Each version lists all versions **and itself**; return links must be reciprocal; one `x-default`.
- Codes: ISO 639-1 language (+ optional ISO 15924 script, ISO 3166-1 region): `en`, `ms-MY`, `zh-Hans`, `en-GB`
  (not `en-uk`, not `my` for Malay — `my` is Burmese; Malay is `ms`).
- Only on canonical URLs; hreflang URL must equal the canonical exactly. Large sets: put hreflang in the sitemap.

## 10. Transport & headers
- HTTPS everywhere, valid cert, http→https 301 in one hop. HSTS `max-age=31536000; includeSubDomains`.
- Security headers (CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
  frame-ancestors) are trust/security items, not ranking factors — report them as low severity.
- Compression (br/gzip) on HTML; long `Cache-Control` on hashed static assets; HTML `no-cache` + ETag or ISR.
- `Cache-Control: no-store` on marketing pages disables bfcache (slower back/forward); avoid it outside the app.

## 11. Severity model (use everywhere)
| Severity | Meaning | Examples |
|---|---|---|
| critical | Blocks indexing/citation of important pages | noindex on public page, CSR shell, robots blocks Googlebot, 5xx, robots.txt 5xx |
| high | Large, measurable loss likely | missing title, sitemap contains noindex/404, canonical conflict, AI search bots blocked, WAF blocks bots |
| medium | Optimisation with clear upside | missing description/OG/JSON-LD, duplicate titles, orphan pages, slow TTFB |
| low | Polish / best practice | title length, heading skips, generic anchors, security headers |
| info | Fact, possibly intentional | canonicalized elsewhere, training bots blocked, FAQPage present |
