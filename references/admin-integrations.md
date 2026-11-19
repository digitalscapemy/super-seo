# SEO integrations in the SaaS super-admin backend

Build first-party SEO data into the product's admin panel: Google Search Console, GA4, Bing Webmaster Tools,
IndexNow, PageSpeed Insights / CrUX. All free APIs. Templates: `templates/integrations/laravel/` and
`templates/integrations/nextjs/`. Data exported from the admin (CSV) feeds `scripts/gsc_report.py`.

## Contents
1. Pick the integration mode · 2. Google Cloud setup · 3. OAuth flow (secure) · 4. Search Console API
5. GA4 Data API (organic + AI referrals) · 6. Bing Webmaster + IndexNow · 7. PageSpeed / CrUX
8. Data model & sync jobs · 9. Admin UI: what to show · 10. Security & privacy checklist

## 1. Pick the integration mode first
| Mode | When | Auth |
|---|---|---|
| **Platform** (super admin sees the SaaS's own marketing domain) | One site, owned by you | **Service account** added as a user in GSC (and GA4). No consent screen, no token expiry. Simplest. |
| **Tenant** (each customer connects *their* site, e.g. a store/website builder with custom domains) | Many sites owned by customers | **OAuth 2.0 web flow per tenant**, refresh token stored encrypted per tenant. Needs Google OAuth app verification. |
| **Both** | Platform domain + tenant domains | Service account for the platform; OAuth for tenants. Same tables, `tenant_id` nullable = platform. |
Multi-tenant platforms on subdomains of one domain (`shop1.platform.com`) can use ONE Domain property
(`sc-domain:platform.com`) and filter by `page` contains the subdomain — no per-tenant OAuth needed.

## 2. Google Cloud setup (once per product)
1. Google Cloud project → enable **Google Search Console API**, **Google Analytics Data API**, **PageSpeed Insights API**,
   **Chrome UX Report API**.
2. Service account (platform mode): create, download JSON key → store as a secret (never in git). In Search Console →
   Settings → Users and permissions → add the service-account email (Restricted is enough for reading; Full to submit
   sitemaps). In GA4 → Admin → Property access management → add it as Viewer.
3. OAuth client (tenant mode): OAuth consent screen (External), scopes below, authorised redirect URI
   `https://admin.yourdomain.com/integrations/google/callback` (exact match).
   - While the consent screen is in **Testing**, refresh tokens **expire after 7 days** and only listed test users can
     connect. Publish to **In production** and complete Google's verification (privacy policy, homepage, domain
     verification, scope justification + demo video for sensitive scopes) before real customers connect.
4. API key (PageSpeed/CrUX): restrict it to those two APIs and to your server IPs.

Scopes (least privilege):
| Need | Scope |
|---|---|
| Read GSC performance, sitemaps, URL inspection | `https://www.googleapis.com/auth/webmasters.readonly` |
| Submit/delete sitemaps | `https://www.googleapis.com/auth/webmasters` (only if the UI offers it) |
| GA4 reports | `https://www.googleapis.com/auth/analytics.readonly` |
| Who connected | `openid email` |

## 3. OAuth flow (secure)
1. Admin clicks **Connect Google** → server creates `state` (random 32 bytes, stored in session with tenant id + user id)
   and PKCE `code_verifier`; redirect to `https://accounts.google.com/o/oauth2/v2/auth` with `response_type=code`,
   `client_id`, `redirect_uri`, `scope`, `state`, `code_challenge` (S256), `access_type=offline`,
   `include_granted_scopes=true`, `prompt=consent` (forces a refresh token on re-connect).
2. Callback: verify `state` (constant-time compare, single use, tied to the logged-in admin) → POST
   `https://oauth2.googleapis.com/token` (`grant_type=authorization_code`, `code`, `code_verifier`, client creds) →
   store `refresh_token` **encrypted**, keep `access_token` only in cache until `expires_in`.
3. Check granted scopes in the token response; if a required scope was unticked, show "missing permission" and
   don't mark the integration active.
4. List properties `GET https://www.googleapis.com/webmasters/v3/sites` → admin picks the property that matches the
   tenant's domain (prefer `sc-domain:` Domain properties; else the exact `https://` URL-prefix property). Validate the
   picked property belongs to the tenant's verified domain — never let a tenant attach someone else's property data.
5. Refresh: POST token endpoint with `grant_type=refresh_token`. On `invalid_grant` (revoked, expired, password
   change) → set status `reconnect_required`, notify the admin, stop the sync.
6. Disconnect: POST `https://oauth2.googleapis.com/revoke?token=<refresh_token>`, delete the token, keep or purge
   historical rows per the tenant's choice.

## 4. Search Console API
Base `https://www.googleapis.com/webmasters/v3`. `siteUrl` must be URL-encoded (`sc-domain%3Aexample.com`,
`https%3A%2F%2Fexample.com%2F`).
| Endpoint | Use |
|---|---|
| `POST /sites/{siteUrl}/searchAnalytics/query` | Clicks, impressions, CTR, position by `date`, `query`, `page`, `country`, `device`, `searchAppearance` |
| `GET /sites/{siteUrl}/sitemaps` · `PUT /sites/{siteUrl}/sitemaps/{feedpath}` | Sitemap status (errors, warnings, last downloaded) · submit |
| `POST https://searchconsole.googleapis.com/v1/urlInspection/index:inspect` `{inspectionUrl, siteUrl}` | Index status, coverage state, Google-chosen canonical, last crawl, robots/indexing allowed, rich results |
Search Analytics request body:
```json
{ "startDate": "2026-08-01", "endDate": "2026-11-15", "dimensions": ["date", "page", "query"],
  "type": "web", "dataState": "final", "rowLimit": 25000, "startRow": 0 }
```
- `rowLimit` max 25,000 → paginate with `startRow` until fewer rows return. Query+page rows are sampled/anonymised
  for rare queries (totals by date alone are more complete than the sum of query rows — store both).
- Data lags ~2–3 days; `dataState: "final"` excludes fresh partial data. Sync daily for `today-4 … today-2`, re-pull the
  last 3 days to replace partial rows. History available ≈ 16 months — backfill once on connect.
- Quotas (per Google's docs; re-check): Search Analytics ~1,200 queries/min per site and per user; URL Inspection
  2,000/day and 600/min per site. Back off on 429 (1, 2, 4, 8, 16 s + jitter; honour `Retry-After`).
- Don't call URL Inspection for every page every day: inspect key pages (home, pricing, top 20 landing pages, newly
  published URLs) on a schedule, cache 7 days.
- The Google **Indexing API** is only for JobPosting and BroadcastEvent pages. Don't use it to "force index" normal pages.

## 5. GA4 Data API (organic + AI referrals)
`POST https://analyticsdata.googleapis.com/v1beta/properties/{propertyId}:runReport`
- Organic landing pages: dimensions `landingPagePlusQueryString`, `sessionDefaultChannelGroup`; metrics `sessions`,
  `engagedSessions`, `keyEvents` (conversions); filter channel group = `Organic Search`.
- **AI referral traffic** (GEO outcome metric): dimension `sessionSource`, filter regex
  `chatgpt\.com|chat\.openai\.com|perplexity\.ai|gemini\.google\.com|copilot\.microsoft\.com|claude\.ai|you\.com|meta\.ai`.
  Many AI clicks arrive without a referrer (counted as Direct), so treat this as a floor, not a total.
- Quotas: token-based per property (≈ 25k tokens/day standard). Cache daily aggregates; never query GA4 per page view.

## 6. Bing Webmaster Tools + IndexNow
- Bing Webmaster API: API key from Bing Webmaster → Settings → API access. `https://ssl.bing.com/webmaster/api.svc/json/<Method>?apikey=<key>`
  — e.g. `GetQueryStats?siteUrl=`, `GetUrlSubmissionQuota?siteUrl=`, `SubmitUrlBatch` (POST `{siteUrl, urlList}`).
  Bing feeds Copilot, DuckDuckGo and part of ChatGPT search; show its query stats beside Google's.
- **IndexNow** (Bing, Yandex, Naver, Seznam; not Google): generate a key (8–128 hex chars), serve it at
  `https://domain/<key>.txt` (text/plain, body = key). On publish/update/delete of a public URL, POST
  `https://api.indexnow.org/indexnow` `{"host": "domain", "key": "...", "keyLocation": "https://domain/<key>.txt",
  "urlList": [...]}` (≤ 10,000 URLs per call). Debounce: queue changes, send at most every few minutes. 200/202 = OK,
  403 = key not found, 422 = URLs don't match host, 429 = slow down.
- Multi-tenant custom domains: one key per tenant domain (serve per host).

## 7. PageSpeed Insights / CrUX
- PSI: `GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=&strategy=mobile&category=performance&key=`
  (lab Lighthouse + field data when available). Slow (10–30 s): run in a queue job, never in a request.
- CrUX: `POST https://chromeuxreport.googleapis.com/v1/records:queryRecord?key=` `{"origin": "https://domain", "formFactor": "PHONE"}`
  → p75 LCP / INP / CLS. 404 = not enough traffic (show "no field data", fall back to PSI lab). Weekly is enough
  (28-day rolling data). History: `records:queryHistoryRecord` (≈ 25 weeks).

## 8. Data model & sync jobs
```
seo_integrations   id, tenant_id NULL, provider (google|bing|indexnow|psi), auth_type (oauth|service_account|api_key),
                   account_email, gsc_property, ga4_property_id, scopes, refresh_token (ENCRYPTED), api_key (ENCRYPTED),
                   status (active|reconnect_required|error|disconnected), last_synced_at, last_error, connected_by, timestamps
gsc_daily          integration_id, date, clicks, impressions, ctr, position                      UNIQUE(integration_id, date)
gsc_page_query     integration_id, date, page, query, country, device, clicks, impressions, ctr, position
                   UNIQUE(integration_id, date, page_hash, query_hash, country, device)   -- hash long strings for the index
gsc_url_inspection integration_id, url, verdict, coverage_state, google_canonical, user_canonical, last_crawl_at,
                   robots_state, indexing_state, inspected_at
seo_vitals         integration_id, scope (origin|url), url, form_factor, lcp_p75, inp_p75, cls_p75, source (crux|psi), measured_at
indexnow_queue     host, url, action, queued_at, sent_at, response_code
```
Jobs (Laravel scheduler / cron / Vercel cron / queue):
- `SyncSearchConsole` daily 03:00 local: per active integration, pull `date` totals + `date,page,query` for the last
  3 final days (upsert), backfill on first run (16 months, chunked by month).
- `InspectKeyUrls` daily, ≤ 200 URLs/property/day, oldest-first.
- `SyncCruxVitals` weekly; `SendIndexNow` every 5 min from the queue.
- Retention: keep page×query rows 16 months (matches GSC), daily totals forever.
- Idempotent upserts; one job per integration (rate limits are per property); exponential backoff; dead-letter after 5 fails.

## 9. Admin UI: what to show (useful, not vanity)
1. **Connection card** per provider: status, connected account, property, last sync, error + Reconnect button.
2. **Performance**: clicks & impressions (28 days vs previous), top pages, top queries, country/device split; brand vs
   non-brand split (regex of brand names from settings).
3. **Opportunities** (actionable): striking-distance queries (position 5–20, ≥ 50 impressions), high-impression low-CTR
   pages (CTR well below the site's curve for that position → rewrite title/description), pages losing clicks
   (28d vs prior 28d), queries split across 2+ pages (possible cannibalisation).
4. **Indexing**: sitemap status (submitted vs indexed, errors), URL Inspection results for key pages (not indexed,
   Google-chosen canonical ≠ declared canonical, blocked by robots).
5. **Core Web Vitals**: p75 LCP/INP/CLS (field) with good/needs-improvement/poor labels + text, not colour alone.
6. **AI referrals**: sessions from AI assistants over time (GA4), labelled "minimum; many AI visits have no referrer".
7. **Export**: CSV in Search Console's format (`Top queries`, `Clicks`, `Impressions`, `CTR`, `Position`; and a
   page+query export) → drop into the project's `.seo/gsc/` and run `scripts/gsc_report.py`.
8. **Alerts** (email/Slack): clicks −30% week over week, key page becomes "not indexed", sitemap errors, integration
   needs reconnect, CWV metric turns poor.

## 10. Security & privacy checklist
- Super-admin-only routes (RBAC policy/middleware), tenant scoping on every query (`where tenant_id = current`), audit log
  of connect/disconnect/export.
- Refresh tokens and API keys encrypted at rest (Laravel `encrypted` cast / KMS envelope), never sent to the browser,
  never logged (scrub HTTP client logs). Service-account JSON in the secret store.
- OAuth: `state` + PKCE, exact redirect URI, HTTPS only, `prompt=consent` only when re-connecting, minimal scopes.
- Validate property ownership against the tenant's verified domains; reject properties for other domains.
- Revoke on disconnect; delete tokens and (on request) the tenant's synced data. Disclose in the privacy policy that
  Search Console/Analytics data is stored, for what purpose, and for how long; list Google as a sub-processor.
- Respect quotas; one integration's failure must not block others; timeouts on every outbound call.
- Treat API responses as data: escape query strings and page URLs when rendering (search queries can contain HTML).
