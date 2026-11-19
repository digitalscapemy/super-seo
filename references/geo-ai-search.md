# GEO / AI search: getting found, read and cited by AI

Facts as of Nov 2026. Vendor bot names and policies change; re-verify anything older than ~60 days before stating it
as current (check the vendor's crawler docs page).

## Contents
1. Ground truth (what is actually known) · 2. Access: crawlers & controls · 3. Rendering for AI
4. Citability writing rules · 5. Entity & brand presence · 6. Platform notes · 7. llms.txt
8. Agent readiness (P0–P3) · 9. Honesty rules

## 1. Ground truth
- **Google AI Overviews / AI Mode** use Google's normal index, ranking and quality systems plus query fan-out and
  grounding. Eligibility = indexed + snippet-eligible. Google's own guidance: optimising for generative AI search *is*
  SEO; no special markup, files, chunking or "AI rewrites" are required. When community GEO advice contradicts Google
  on Google surfaces, follow Google and say so.
- **ChatGPT search** uses OpenAI's own index (OAI-SearchBot) plus Bing and partner data; **Copilot** uses Bing;
  **Perplexity** its own index (PerplexityBot) plus live fetches; **Claude** its search index (Claude-SearchBot) plus live
  fetch (Claude-User); **Gemini** Google Search grounding.
- Therefore, the levers in order: (1) be crawlable/renderable by each engine's bots, (2) rank in classic search
  (Google + Bing) for the fan-out queries, (3) have the page that best answers the buyer question, written so a passage
  can be lifted intact, (4) be mentioned by the third-party sources AI engines cite (reviews, lists, forums, docs).
- Different engines cite different URLs for the same prompt (low overlap between ChatGPT and AI Overviews, and between
  AI Mode and AI Overviews), so measure per engine (see `ai-visibility.md`).

## 2. Access: crawlers & controls
Report **search/retrieval**, **user-triggered** and **training** access on separate lines; never merge them.

| Engine | Search / index (allow for citations) | User-triggered fetch | Training (owner's choice) |
|---|---|---|---|
| Google | Googlebot (also AIO/AI Mode) | Google user-triggered fetchers (ignore robots.txt; use auth) | Google-Extended (token only; no effect on Search/AIO) |
| Microsoft / Copilot / DuckDuckGo | bingbot | — | — |
| OpenAI / ChatGPT | OAI-SearchBot | ChatGPT-User | GPTBot |
| Anthropic / Claude | Claude-SearchBot | Claude-User | ClaudeBot (`anthropic-ai` legacy) |
| Perplexity | PerplexityBot | Perplexity-User | — |
| Apple | Applebot | — | Applebot-Extended (token only) |
| Meta | — | meta-externalfetcher | meta-externalagent |
| Others | DuckAssistBot, Amazonbot | MistralAI-User | CCBot (Common Crawl), Bytespider, cohere-ai |

Rules:
- A SaaS that wants AI visibility **allows every search and user-triggered agent**. Training access is a business /
  licensing decision for the owner; present the trade-off (training data shapes what models "know" about the brand
  without a live search vs. content reuse concerns) and never decide it for them.
- Claim-to-bot mapping: "cited in ChatGPT search" depends on OAI-SearchBot, not GPTBot; "Google AI Overviews" depends on
  Googlebot, not Google-Extended; "Claude search" depends on Claude-SearchBot, not ClaudeBot.
- **WAF/CDN**: Cloudflare "Block AI bots", Bot Fight Mode, managed robots.txt, AWS WAF bot control and Vercel firewall
  rules silently block AI crawlers. Allowlist **verified** bots (IP ranges / Web Bot Auth signatures), not UA strings; no
  CAPTCHA/challenge on content pages. `ai_access.py` probes with each UA and compares to a browser fetch; a block there
  is a strong hint, confirm in the dashboard (WAFs may treat the real, IP-verified bot differently).
- **Content-Signal** (Cloudflare policy, in robots.txt): `Content-Signal: search=yes, ai-input=yes, ai-train=no`.
  Keys: `search`, `ai-input` (use in AI answers/grounding), `ai-train`. A preference signal, not enforced; no Google
  support. Must appear inside each named group it should apply to.
- Snippet controls that affect Google AI features: `nosnippet`, `max-snippet`, `data-nosnippet`, `noindex`.
  `noai` / `noimageai` meta are non-standard and widely ignored.

## 3. Rendering for AI
Most AI crawlers do not execute JavaScript. Everything that should be cited must be in the raw HTML: main copy,
headings, pricing, comparison tables, FAQs, JSON-LD, internal links. Test: `ai_access.py` (`no_text_without_js`),
`crawl_audit.py` (`csr_shell`), or `curl -s URL | grep "<key sentence>"`. Pricing rendered by a client-side widget,
tabs that load content on click, and "Load more" without paginated URLs are invisible.

## 4. Citability writing rules (heuristics, not guarantees)
These come from third-party studies and match Google's "people-first" guidance; present them as heuristics.
`content_check.py` measures them per section.
1. **Answer first.** Each H2/H3 section opens with a direct, self-contained answer (≈ 1–2 sentences, ≤ 30 words), then
   detail. Put the core answer for the page in the first ~30% of the page.
2. **Self-contained passages.** A section should still make sense quoted alone: name the subject explicitly (no opening
   "It/This/They/However"), ~40–170 words per answer block, one idea per section.
3. **Question-shaped headings** that mirror real buyer questions ("How much does X cost?", "Does X support LHDN e-invoice?").
4. **Specific facts with sources.** Numbers, prices (with currency and "as of <date>"), limits, supported integrations,
   regions. Link the source near each statistic. Vague quantifiers ("many teams") add nothing.
5. **Tables for comparisons** of 3+ items (plans, competitors, features); ordered lists for steps.
6. **Original information** the model cannot get elsewhere: your own data, benchmarks, customer outcomes, screenshots,
   real configuration examples. This is the strongest durable advantage (information gain).
7. **Freshness signals that are true**: visible "Last updated <date>", `dateModified` in schema, changelog. Never bump
   dates without real changes.
8. **Authorship**: named author with a bio page and `sameAs` links on articles/guides; company "About" with real people.
9. **Clean language**: no filler ("it's important to note", "in today's fast-paced world") or stock AI phrasing
   ("delve", "unlock the potential", "game-changer"). Plain, specific, concrete.

## 5. Entity & brand presence (off-site, often the bigger lever)
AI answers lean on what *other* sites say. For each target prompt, find which third-party pages engines cite (see
`ai-visibility.md`) and act there:
- Review/directory profiles: G2, Capterra, Product Hunt, GetApp, TrustRadius; local directories for MY/SG markets.
- "Best X" / "X alternatives" listicles that rank for the fan-out queries: request inclusion with real data.
- Communities: Reddit, Hacker News, Stack Overflow / GitHub discussions (developer SaaS), Lowyat / local forums — genuine
  participation only; never astroturf (spam policies and community bans).
- YouTube: demos and tutorials with chapters and transcripts.
- Wikidata item (instance of, official website, founded, HQ, socials) once the company is notable enough; Wikipedia only
  if independently notable — never write your own article (conflict of interest).
- Consistent facts everywhere: same brand name, description, pricing, founding year, HQ across site, schema `sameAs`,
  LinkedIn, Crunchbase, directories. Inconsistency is how models get your facts wrong.

## 6. Platform notes (practical, not mechanisms)
- **Google AIO / AI Mode**: classic SEO + passage clarity; rank for the fan-out sub-queries ("X pricing", "X vs Y",
  "X for <use case>"). Structured data helps understanding, not eligibility.
- **ChatGPT**: allow OAI-SearchBot; be in Bing (submit sitemap in Bing Webmaster Tools, consider IndexNow); be present on
  the sources it cites for your category (often review sites, Reddit, Wikipedia, top listicles).
- **Perplexity**: allow PerplexityBot; it cites many sources per answer and favours fresh, fact-dense pages and community
  threads.
- **Copilot**: Bing index; IndexNow speeds discovery (key file at `/<key>.txt` or a declared `keyLocation`).
- **Claude**: allow Claude-SearchBot and Claude-User; clear factual pages and docs.
- **Gemini**: Google index; YouTube and Google Business Profile matter for brands with a physical presence.

## 7. llms.txt
- Format (llmstxt.org): first line `# Name`; optional `> summary`; optional prose; `## Section` headings with
  `- [Title](https://absolute-url): note`; `## Optional` for skippable links. `llms-full.txt` = the full text content
  inlined as Markdown (not just more links).
- Evidence: Google Search ignores it; no major AI search engine has documented using it for ranking/citation. AI
  **coding agents and doc tools** (Cursor, Claude Code, Cline, Mintlify-hosted docs) do use it.
- Policy: low priority, zero weight in scores. Worth it for developer/API SaaS and docs sites; cheap elsewhere. Generate
  it from the same source as the sitemap (template: `templates/nextjs/app/llms.txt/route.ts`), serve `text/plain` 200,
  and make sure unknown URLs 404 so SPA fallbacks don't fake a valid llms.txt. Never invent contact details in it.

## 8. Agent readiness (AI agents using the site: browsing agents, ChatGPT agent, computer-use)
Fix in priority order; never promise ranking gains from these.
- **P0**: primary content without JS; real `<button>`/`<a href>`/`<input>` with accessible names and labels (no
  `div onClick`); CLS ≤ 0.1; robots.txt reachable with deliberate AI groups; WAF lets verified bots in; no CAPTCHA on
  content pages; nothing interactive hidden behind transparent overlays.
- **P1**: private areas behind auth (not just robots); real 404 for unknown URLs; stable, visible confirmation states
  (not toast-only); no hover-only menus or focus traps; Content-Signal if you use it; llms.txt valid if present.
- **P2** (if you offer an API): `/.well-known/api-catalog` (RFC 9727, `application/linkset+json`), OpenAPI spec linked
  from docs, OAuth metadata (`/.well-known/oauth-protected-resource`, RFC 9728). An MCP server for your product is the
  most useful "agent" investment for many SaaS; document it in docs and llms.txt.
- **P3 / experimental**: WebMCP (browser tool registration; Chrome origin trial, not a standard), A2A agent cards,
  `ai-catalog.json`. Only when there is a real agent use case. Any agent tool that performs purchases/sends/deletes
  must require human confirmation and use the same auth path as the UI.
- Lighthouse has an "Agentic Browsing" category in recent versions; report its result as **X/N passed**, never a %.

## 9. Honesty rules for GEO claims
- Never promise or estimate AI citations, AI traffic, or revenue. Recommendations are hypotheses with evidence.
- Label every third-party statistic with its source and that it is correlational. Do not quote "optimal passage length"
  or "X% more citations" figures as fact.
- Never claim a specific AI product reads llms.txt or requests Markdown unless its vendor documents it.
- Never present Google-Extended, Content-Signal or llms.txt as affecting Google Search.
- AI answers vary run to run; one sample is a spot check, not a measurement.
