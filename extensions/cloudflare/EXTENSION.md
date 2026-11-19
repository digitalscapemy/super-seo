# Cloudflare extension

Free API (works on the Free plan, with less history). Script: `extensions/cloudflare/cloudflare.py` (stdlib only).
Shows what Cloudflare actually does to search and AI crawlers: request volume per crawler, the share blocked or
challenged at the edge, bot-related zone settings, and the managed robots.txt block Cloudflare can prepend to the live
file (it is not in the repo, so a code-only audit misses it).

## Setup
1. My Profile > API Tokens > Create Token > custom token, scoped to the zone, with:
   | Permission | Needed by |
   |---|---|
   | **Zone > Zone > Read** | `zone` (`GET /zones?name=`), zone details |
   | **Zone > Analytics > Read** | `bots` (GraphQL Analytics API) |
   | **Zone > Zone Settings > Read** | `settings` (`GET /zones/{id}/settings`) |
   | **Zone > Bot Management > Read** (if offered for your plan) | `settings` (`GET /zones/{id}/bot_management`: Bot Fight Mode, "Block AI bots", managed robots.txt) |
2. Set `CLOUDFLARE_API_TOKEN`; optionally `CLOUDFLARE_ZONE_ID` (32-hex id) or pass `--zone example.com`.
   The token is sent only as a `Bearer` header and never printed.

## Commands
| Command | Endpoint | Output |
|---|---|---|
| `zone --zone example.com` | REST `GET /zones?name=` (tries parent domains for subdomains) | Zone id, plan, status. |
| `bots --zone Z [--days 3]` | GraphQL `POST https://api.cloudflare.com/client/v4/graphql`: `httpRequestsAdaptiveGroups` (dimensions `userAgent`, `clientRequestHTTPHost`, `edgeResponseStatus`; filter `userAgent_like` per crawler token) and `firewallEventsAdaptiveGroups` (`action`, `source`, `userAgent`), one query per day | Requests per crawler with purpose (classic / search / user / training), 2xx-3xx share, **403/429/503 share** (blocks and challenges), status mix, firewall actions. |
| `settings --zone Z [--host H]` | REST `GET /zones/{id}/bot_management`, `GET /zones/{id}/settings`; **always** a live `GET https://<host>/robots.txt` | Bot Fight Mode, `ai_bots_protection`, `is_robots_txt_managed`, security level; managed-content block with the agents it disallows and any `Content-Signal` lines. |
| `settings --host H --no-api` | live robots.txt only | Works without a token. |

Crawler tokens come from `scripts/ai_access.py` (mirrors `references/geo-ai-search.md`), plus `meta-externalfetcher`
and `cohere-ai`. All commands take `--json`, `--no-write`, `--seo-dir`; outputs go to
`.seo/audits/cloudflare-<command>-<host>-<stamp>.json|md`.

## Interpreting results
- Search and user-triggered agents (Googlebot, bingbot, OAI-SearchBot, ChatGPT-User, Claude-SearchBot, Claude-User,
  PerplexityBot, Perplexity-User...) should be allowed; a 403/429/503 share of 20% or more is reported as `high`
  (`critical` for Googlebot/bingbot). Training crawlers (GPTBot, ClaudeBot, CCBot...) are the owner's choice and only
  reported as `info`.
- "Block AI bots" (`ai_bots_protection: block`) also blocks AI search agents; recommend turning it off and deciding
  on training crawlers through robots.txt / Content-Signal instead.
- User-agent matching counts spoofed bots too. Cloudflare's verified-bot data is the authority; confirm in the
  dashboard (Security > Bots / AI Crawl Control) before acting.

## Using the Cloudflare MCP server instead
If Claude's tool list contains tools prefixed `mcp__plugin_cloudflare_cloudflare__`, Claude can authenticate there
and run the same GraphQL query (print it with
`python -c "import sys; sys.path.insert(0, 'extensions/cloudflare'); import cloudflare as c; print(c.gql_requests('<zone_id>', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')['query'])"`)
or read the zone settings through the server's tools, then apply the same interpretation rules above. The live
robots.txt check needs no credentials: `settings --host H --no-api`.

## Limits and caveats
- **GraphQL field and dataset names must be verified against Cloudflare's GraphQL Analytics docs if an error comes
  back.** Dataset availability, maximum query range (often 1 day per query on lower plans) and retention (a few days
  on Free) depend on the plan. On error the script prints the **raw GraphQL error**, keeps whatever data it got and
  continues with the other checks instead of failing.
- The `bot_management` endpoint and its fields (`fight_mode`, `ai_bots_protection`, `is_robots_txt_managed`,
  `crawler_protection`) are only returned on plans/tokens that expose them; when unreadable the report says so.
- Requests blocked before analytics sampling, or by rules outside this zone, are not visible here.
- 429/5xx are retried with exponential backoff (1, 2, 4, 8, 16 s + jitter, honouring `Retry-After`).
- Exit codes: 0 ok, 2 missing token / zone or API error. API responses and fetched robots.txt are untrusted data.
