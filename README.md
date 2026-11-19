<!--
  super-seo: a code-first, multi-agent SEO + GEO skill for Claude Code. Audits a SaaS codebase and its running site
  (localhost, preview or production), then ships the fixes as code.
-->

<p align="center">
  <img src="assets/hero.svg" width="100%" alt="Comic strip: a SaaS site is stuck at #47 while AI bots bounce off a 403 wall; the super-seo robot hero flies in shouting SSR, Schema, Sitemap and Unblock; the site rises to #1 and ChatGPT, Perplexity, Gemini, Claude and Copilot cite it." />
</p>

<h1 align="center">super-seo</h1>

<p align="center">
  <a href="#-quick-start">
    <img src="https://readme-typing-svg.demolab.com?font=Bangers&size=30&duration=2400&pause=600&color=EE3B34&center=true&vCenter=true&width=820&lines=One+command%3A+%2Fsuper-seo;Eight+specialist+agents+in+parallel;Audit+your+code%2C+not+just+your+URL;Crawl+localhost+before+you+deploy;Get+cited+by+ChatGPT%2C+Perplexity+%26+Gemini;Every+fix+shipped+as+code" alt="One command: /super-seo. Eight specialist agents in parallel. Audit your code, not just your URL. Crawl localhost before you deploy. Get cited by ChatGPT, Perplexity and Gemini. Every fix shipped as code." />
  </a>
</p>

<p align="center">
  <b>The SEO + GEO superhero squad that lives inside <a href="https://claude.com/product/claude-code">Claude Code</a>.</b><br />
  Type <code>/super-seo</code>. Eight specialists audit your codebase and your site in parallel, then fix what they find.
</p>

<p align="center">
  <a href="#-meet-the-squad">Squad</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-how-it-works">How it works</a> ·
  <a href="#-quick-start">Quick start</a> ·
  <a href="#-commands">Commands</a> ·
  <a href="#-extensions">Extensions</a> ·
  <a href="#-the-toolkit">Toolkit</a> ·
  <a href="#-admin-integrations">Admin integrations</a> ·
  <a href="#-faq">FAQ</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Claude%20Code-Skill%20%2B%20Agents-ee3b34?style=for-the-badge" alt="Claude Code skill and agents" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-2b7de9?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/tests-296%20passing-3fbf4f?style=for-the-badge" alt="296 tests passing" />
  <img src="https://img.shields.io/badge/Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-supported-ffcf2e?style=for-the-badge&labelColor=141414" alt="Windows, macOS and Linux" />
  <a href="https://github.com/digitalscapemy/super-seo/stargazers"><img src="https://img.shields.io/github/stars/digitalscapemy/super-seo?style=for-the-badge&color=ff8a1f" alt="GitHub stars" /></a>
</p>

---

Most SEO tools look at a live URL and hand you a list of problems. **super-seo starts where your product is built: the code.**
It reads your framework, routes and metadata, crawls a production build on `localhost` before you deploy, checks whether
Google **and** AI engines can actually reach and read your pages, then writes the fix in your framework's own APIs.

## 🦸 Meet the squad

<p align="center">
  <img src="assets/squad.svg" width="100%" alt="The super-seo squad: eight robot superheroes named Code, Technical, GEO, Content, Schema, Speed, Rivals and Fixer, each with an emblem and a one-line power." />
</p>

| Hero | Agent | Power |
|---|---|---|
| **CODE** | `super-seo-code` | Reads routes, metadata, sitemap and robots code, JSON-LD components and leaks; drafts exact diffs |
| **TECHNICAL** | `super-seo-technical` | Crawl and index health: status codes, redirects, canonicals, sitemaps, hreflang, drift between releases |
| **GEO** | `super-seo-geo` | AI crawler access, WAF and CDN blocks, `llms.txt`, citability, entity presence, AI-answer gaps |
| **CONTENT** | `super-seo-content` | Page inventory against the SaaS playbook, on-page quality, metadata rewrites, internal links |
| **SCHEMA** | `super-seo-schema` | Validates JSON-LD everywhere and writes ready-to-paste markup per page type |
| **SPEED** | `super-seo-performance` | Core Web Vitals, Lighthouse, raw-vs-rendered HTML, desktop and mobile screenshots, images |
| **RIVALS** | `super-seo-competitors` | Verified competitors, SERP clusters, comparison-page opportunities, link and mention prospects |
| **FIXER** | `super-seo-fixer` | Applies fixes in parallel, one instance per file set, then verifies with a re-scan |

## ✨ Features

<table>
<tr>
<td width="33%" valign="top">

### 🧠 Code-aware audits
Detects Next.js, Vite, Laravel, Astro, Nuxt, SvelteKit, Remix and more. Finds routes without metadata, `noindex`
left on, `localhost` or preview URLs in canonicals, client-only JSON-LD and SPA shells that bots see as empty.

</td>
<td width="33%" valign="top">

### 🕷️ Localhost-first crawler
Audits your production build **before** deploy with 70+ checks: status codes, redirects, canonicals, sitemap, robots,
titles, Open Graph, structured data, hreflang, duplicates, orphans, broken links and WAF blocks.

</td>
<td width="33%" valign="top">

### 🤖 AI search access
Checks every major AI crawler (search, user-triggered and training) with RFC 9309 rules, probes for CDN/WAF blocks,
and detects managed robots.txt blocks you never wrote.

</td>
</tr>
<tr>
<td valign="top">

### 🖥️ Real browser rendering
Compares raw HTML with the rendered DOM to catch content only JavaScript can see, captures desktop and true 390 px
mobile screenshots, and checks agent-friendly UI.

</td>
<td valign="top">

### 📈 AI visibility tracking
Measures how often ChatGPT, Perplexity, Gemini, AI Overviews, Claude and Copilot **mention** and **cite** you
versus competitors, which sources they lean on, and how that moves over time.

</td>
<td valign="top">

### ⚡ Performance, measured
Core Web Vitals field data, CrUX history and LCP subparts, Lighthouse on any URL including localhost, and an image
audit for weight, format, oversizing and hero loading.

</td>
</tr>
<tr>
<td valign="top">

### 🛠️ Fixes shipped as code
Drop-in templates for metadata, sitemaps, environment-aware robots rules, `llms.txt` and a full SaaS JSON-LD graph;
a validation hook checks JSON-LD every time a file is edited.

</td>
<td valign="top">

### 🧭 Every page type
Playbooks for SaaS pages, e-commerce, local businesses, multilingual sites, content briefs and topic clusters,
plus Open Graph images generated in code.

</td>
<td valign="top">

### 🛡️ Release gate
A readiness score that fails CI on criticals, and drift detection that catches `noindex`, canonical or H1 regressions
between releases.

</td>
</tr>
</table>

## 🔁 How it works

<p align="center">
  <img src="assets/flow.svg" width="100%" alt="Five comic panels: scan the code, crawl localhost, unblock AI bots, fix in code, guard releases." />
</p>

1. **Scan** the code: framework, rendering mode, route metadata and leaks.
2. **Crawl** a production build on localhost, the way a search engine sees it.
3. **Unblock** AI bots: robots rules per crawler, CDN and WAF behaviour, text visible without JavaScript.
4. **Fix** in code: specialists propose, fixers apply in parallel, the audit re-runs until no critical issue remains.
5. **Guard** releases: the same checks in CI, with drift detection against the previous release.

## 🎬 See it in action

<p align="center">
  <img src="assets/terminal.svg" width="100%" alt="Comic-framed mission log: /super-seo runs a baseline, the squad works in parallel, findings are listed, fixers change 12 files, and readiness jumps from 42 to 95 with zero criticals." />
</p>

Every run writes a Markdown and a self-contained HTML report to `.seo/reports/`, plus raw JSON for every check.

## 🚀 Quick start

**Requirements:** [Claude Code](https://claude.com/product/claude-code) and Python 3.10 or newer. Node.js and a
Chromium-based browser unlock the Lighthouse and browser extensions.

**macOS / Linux**

```bash
git clone https://github.com/digitalscapemy/super-seo.git
cd super-seo && ./install.sh                 # add --enable-hook for JSON-LD validation on every edit
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/digitalscapemy/super-seo.git
cd super-seo; .\install.ps1                  # add -EnableHook for JSON-LD validation on every edit
```

The installer copies the skill, its extensions and the eight `super-seo-*` agents into your Claude Code folder
(respecting `CLAUDE_CONFIG_DIR`). Re-run it after `git pull` to update.

**Then open any project in Claude Code and type:**

```text
/super-seo
```

## 🎮 Commands

| Command | What happens |
|---|---|
| `/super-seo` | **Full audit + improvement**: baseline → squad in parallel → verified, prioritised report → safe fixes applied, content changes confirmed → re-audit with before/after |
| `/super-seo audit` | The full audit without changing code |
| `/super-seo fix` | Implement the latest report's backlog, then verify |
| `/super-seo geo` | AI-search access, citability and AI-answer gaps |
| `/super-seo page <path or url>` | Write or optimise one page |
| `/super-seo launch` | Pre-launch gate and launch-day setup |
| `/super-seo integrations` | Build SEO integrations into your super-admin backend |
| `/super-seo extensions` | Show which extensions are active and how to enable the rest |
| `/super-seo quick <url>` | A fast baseline audit, summarised in chat |

You can also just talk to it: *"Why doesn't ChatGPT mention us?"*, *"Make this Next.js app SEO-ready"*,
*"Write a comparison page against our main competitor"*, *"Did the last release break anything?"*

## 🔌 Extensions

Extensions switch on automatically when their requirement is present. Run `python scripts/extensions.py` to see the
status on your machine.

| Extension | Adds | Enable with |
|---|---|---|
| **Google** | Search Console queries, pages, URL Inspection, sitemaps; GA4 organic and AI referrals; PageSpeed; CrUX and history; LCP subparts | OAuth desktop login, a service account, or a PageSpeed API key |
| **Bing Webmaster** | Query and page stats, inbound link counts, crawl issues, URL submission | Bing Webmaster API key |
| **IndexNow** | Key generation and verification, change submission | an IndexNow key |
| **PostHog · Matomo · Umami** | Organic and AI-assistant referral traffic, landing pages | each tool's API key |
| **Cloudflare** | Bot and AI-crawler traffic and block rates, managed robots detection | a Cloudflare API token |
| **Lighthouse** | Lighthouse on any URL including localhost, Agentic Browsing results, site-wide runs | Node.js (`npx`) |
| **Browser** | Raw vs rendered HTML diff, desktop and mobile screenshots, agent-UX and mobile checks | an installed Chrome, Edge or Chromium |
| **Shopify** | Product and collection SEO gaps, redirect chains | a store Admin API token |

## 🧰 The toolkit

Every script runs on the Python standard library. Claude runs them for you; you can also run them directly or in CI.

| Script | What it does |
|---|---|
| `audit.py` | Runs the core checks and produces the readiness scorecard plus Markdown/HTML reports; CI exit codes |
| `project_scan.py` | Static scan of the codebase: framework, rendering mode, routes without metadata, leaks, missing SEO files |
| `crawl_audit.py` | Crawler for localhost, preview or live sites with 70+ page and site checks |
| `ai_access.py` | robots.txt per AI crawler, WAF/CDN probes, Content-Signal, snippet controls, `llms.txt`, Markdown negotiation |
| `content_check.py` | Citability signals per section for a URL or a local `.md`/`.mdx`/`.html` file |
| `hreflang_check.py` | hreflang mesh: self and return links, x-default, language codes, canonical targets |
| `image_audit.py` | Image weight, served format, oversizing, alt quality, hero lazy-loading and priority |
| `ai_visibility.py` | Scores collected AI answers: mention rate, citation rate, position, gaps, sources, trends |
| `gsc_report.py` | Search Console exports → striking-distance queries, low CTR, cannibalisation, question queries |
| `drift.py` | Compares two crawls and flags SEO regressions between releases |
| `extensions.py` | Lists extensions and whether each one is active |

<details>
<summary><b>Command reference</b></summary>

```bash
python scripts/audit.py --path . --url http://localhost:3000 --fail-on critical
python scripts/audit.py --url https://example.com --psi
python scripts/crawl_audit.py https://example.com --max-pages 100 --out .seo/audits
python scripts/ai_access.py https://example.com --paths pricing docs
python scripts/hreflang_check.py https://example.com
python scripts/image_audit.py https://example.com
python scripts/content_check.py content/blog/post.mdx
python scripts/gsc_report.py .seo/gsc --brand "acme"
python scripts/drift.py --latest .seo/audits --fail-on critical
python extensions/google/google.py gsc --property sc-domain:example.com --days 90
python extensions/lighthouse/lighthouse.py run http://localhost:3000 --agentic
python extensions/browser/render_check.py render http://localhost:3000/pricing
```

</details>

## 🔌 Admin integrations

Bring first-party search data into your SaaS super-admin backend, with templates for **Laravel** and **Next.js**:
Search Console (OAuth 2.0 with PKCE per tenant, or a service account for the platform), GA4 organic and AI-referral
traffic, Bing Webmaster, IndexNow and Core Web Vitals. Tokens are encrypted at rest, every query is tenant-scoped,
and a tenant can only attach a property that matches its own verified domain.

## 🏗️ Framework support

| Stack | What super-seo sets up |
|---|---|
| **Next.js App Router** | `metadataBase`, per-route `generateMetadata`, `sitemap.ts`, environment-aware `robots.ts`, OG images, `llms.txt` route, server-rendered JSON-LD |
| **Next.js Pages Router** | `next/head` per page, data in `getStaticProps`, sitemap generation |
| **Vite / React / Vue SPA** | Marketing-site split or build-time prerendering, per-route head, real 404s |
| **Laravel (Blade / Inertia)** | Layout metadata, Inertia SSR, sitemap package, environment-aware robots, `noindex` middleware for app routes |
| **Astro** | Site config, sitemap integration, layout head, JSON-LD, `llms.txt` endpoint |
| **Nuxt · SvelteKit · Remix · React Router 7 · TanStack Start** | Native head/meta APIs, prerendering, resource routes for sitemap and robots |
| **Shopify · static HTML + nginx** | Store SEO gaps and redirects; per-page head, 404 handling, staging `noindex` |

## 🗂️ Project memory

```text
.seo/
├── project.md          # positioning, audience, competitors, key pages, crawler policy
├── research-log.md     # one line per research task, reused for 30 days
├── audits/             # raw JSON from every check and every agent (drift compares these)
├── reports/            # human-readable reports (.md + .html)
├── screenshots/        # desktop and mobile captures from the browser extension
├── ai-visibility/      # tracker, collected answers per run, scores and trends
└── gsc/                # Search Console exports
```

## 📐 Principles

- **Evidence before advice.** Every finding comes from a file that was read, a page that was fetched or a script that ran.
- **Fixes, not reports.** The deliverable is a diff plus a short report.
- **One system for Google and AI.** Crawlable, server-rendered, well-ranked, clearly written pages win in both.
- **Honest claims.** No ranking, traffic or citation forecasts; heuristics are labelled as heuristics.
- **Safe by default.** Private-network guards on every fetch, decompression limits, signed OAuth state, hardened CI.

## ❓ FAQ

<details>
<summary><b>Does it work on a site that isn't deployed yet?</b></summary>

Yes, and that's the point. Run a production build locally and audit `http://localhost:<port>`. Development servers
behave differently, so audit a production build.
</details>

<details>
<summary><b>Do I need all the extensions?</b></summary>

No. The core works on its own. Each extension adds a data source and turns on automatically when its key or tool is present.
</details>

<details>
<summary><b>Will it change my code without asking?</b></summary>

`/super-seo` applies safe, mechanical fixes (metadata, sitemap, robots, schema with confirmed facts) and asks before
content or business changes. Every edit goes through your normal Claude Code permissions as a reviewable diff, and
nothing is committed for you.
</details>

<details>
<summary><b>How is AI visibility measured?</b></summary>

You collect real answers from each engine for a fixed set of buyer prompts (super-seo prints the checklist), and the
scorer computes mention rate, citation rate, position and competitor gaps. Simulated, search-grounded answers are
reported separately from real observations.
</details>

<details>
<summary><b>Which languages and markets does it support?</b></summary>

Any. Reports follow the language you write in, page copy follows the target market, and hreflang, currencies and local
search terms are handled per market.
</details>

## 🤝 Contributing

Issues and pull requests are welcome. Run `python -m unittest discover -s tests` before opening a PR, and include the
framework, the command you ran and the relevant part of the report when reporting a problem.

---

<p align="center">
  <sub>Built by <a href="https://github.com/digitalscapemy">Digitalscape</a> · Made for teams who ship and want to be found.</sub><br />
  <a href="#super-seo"><sub>Back to top ↑</sub></a>
</p>
