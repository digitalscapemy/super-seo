# Shopify extension

Audits a Shopify store's catalogue SEO through the **GraphQL Admin API**. Free with any Shopify plan: it uses a custom
app's Admin API token, read-only. Complements `crawl_audit.py` (which sees the rendered storefront) with what only the
admin knows: SEO fields, every product's media alt text, empty collections and the full redirect table.

## Setup (5 minutes, store owner or staff with app permissions)
1. Shopify admin → **Settings → Apps and sales channels → Develop apps** → *Allow custom app development* (first time
   only) → **Create an app** (name it e.g. "SEO audit").
2. **Configuration → Admin API integration → Edit**, tick only:
   | Scope | Needed for |
   |---|---|
   | `read_products` | products, collections, media alt text |
   | `read_online_store_navigation` | URL redirects (`urlRedirects`) |
   No write scopes. Nothing else is needed.
3. **Install app** → **API credentials** → reveal the **Admin API access token** (`shpat_…`, shown once).
4. Set environment variables (never commit them; a user config file outside the repo is fine):
   ```powershell
   $env:SHOPIFY_STORE = "myshop.myshopify.com"     # the permanent *.myshopify.com domain, not the custom domain
   $env:SHOPIFY_ADMIN_TOKEN = "shpat_..."
   $env:SHOPIFY_API_VERSION = "2025-10"             # optional
   ```
   ```bash
   export SHOPIFY_STORE=myshop.myshopify.com SHOPIFY_ADMIN_TOKEN=shpat_...
   ```
5. `python scripts/extensions.py` should now list the extension as active.

If the admin no longer offers **Develop apps** (Shopify has been moving custom-app creation to its developer
dashboard), create the app there instead, request the same two read scopes, install it on the store and use the
Admin API access token it issues. The script only needs a token that can call the GraphQL Admin API with those scopes.

**API version.** Shopify releases a version every quarter (`YYYY-01/04/07/10`) and supports each for about 12 months;
a request for a retired version is served by the oldest supported one. The script defaults to `2025-10`, reports
`api_version_fallback` when Shopify serves a different version, and accepts `SHOPIFY_API_VERSION`. Update the default
(`DEFAULT_VERSION` in `shopify.py`) when it is retired, after checking the queries below still validate.

## Commands
```bash
python extensions/shopify/shopify.py audit            # products + collections + redirects + summary
python extensions/shopify/shopify.py products --json
python extensions/shopify/shopify.py collections
python extensions/shopify/shopify.py redirects
# options: --limit 5000 (items per resource)  --short-words 50  --no-write  --out .seo/audits  --fail-on high
```
Output: markdown (or `--json`) and `.seo/audits/shopify-<store>-<stamp>.json|md`. Findings use the shared shape
`{severity, code, message, count, items[]}` (max 25 example items per finding).

| Section | Checks (code) |
|---|---|
| products (ACTIVE only) | `product_missing_seo_title` (low; Shopify falls back to the product title), `product_missing_seo_description` (medium), `product_seo_title_long` / `_description_long` (low), `product_duplicate_titles` / `_duplicate_seo_titles` (medium), `product_no_images` (high), `product_images_missing_alt` (medium; first 10 media per product), `product_empty_description` (high), `product_short_description` (< 50 words, medium), `product_not_on_online_store` (info) |
| collections | same SEO/description checks (`collection_*`), `collection_empty` (0 products, medium), `collection_no_image`, `collection_image_missing_alt` (low) |
| redirects | `redirect_self`, `redirect_loop` (high), `redirect_chain` (≥ 2 hops, medium), `redirect_external` (info). Paths compared case-insensitively without trailing slash; absolute targets on the store's own domains are treated as paths |

**Throttling.** The GraphQL Admin API uses a cost-based leaky bucket. After every call the script reads
`extensions.cost.throttleStatus` (`currentlyAvailable`, `restoreRate`) and sleeps before the next call if the bucket
cannot pay the last query's `requestedQueryCost`; `THROTTLED` errors and HTTP 429/5xx are retried with backoff
(1, 2, 4, 8, 16 s + jitter, `Retry-After` honoured). Page sizes (50 products × 10 media, 100 collections, 250
redirects) keep each query well under the single-query cost limit. Total cost used is in `summary.query_cost_used`.

## What Shopify already handles (don't "fix" these)
- **Canonical tags**: themes output `{{ canonical_url }}`; product URLs reached through a collection
  (`/collections/x/products/y`) canonicalise to `/products/y`.
- **sitemap.xml**: generated automatically at `/sitemap.xml` (index → products, collections, pages, blogs), only for
  published resources; it cannot be edited directly. Submit it in Search Console and Bing Webmaster Tools.
- **robots.txt**: generated with sensible defaults (blocks cart, checkout, account, internal search, `sort_by`
  parameters and combined-tag URLs; check the live file for the current rules). Customise only via the `robots.txt.liquid` theme template, and keep the default rules
  (`{{ group.rules }}`) unless you know why you are removing them.
- HTTPS, `www`/primary-domain redirects, and a redirect when a product handle changes (if "Create a URL redirect" is ticked).

## Common Shopify SEO issues to check alongside this audit
- **Duplicate product paths**: many themes link products as `/collections/<c>/products/<p>` in collection grids. The
  canonical saves indexing, but internal links should point at `/products/<p>` (edit the theme's product-card snippet:
  use `product.url` without `| within: collection`). Run `crawl_audit.py` and look at internal link targets.
- **Faceted filters and sorting** (`?filter.v.…`, `?sort_by=`, tag URLs `/collections/x/tag`): generate near-infinite
  URLs. Keep them out of the sitemap, canonical to the base collection, and leave Shopify's default robots rules in
  place; only make a filtered view indexable when it has its own demand, copy and stable URL (see
  `references/ecommerce.md` §5).
- **Apps injecting schema client-side**: review and SEO apps often add Product/Review JSON-LD with JavaScript, or
  duplicate the theme's own Product markup. Check the raw HTML (`crawl_audit.py` reports JSON-LD per page): keep one
  server-rendered Product graph, and remove duplicates. Ratings only from real reviews shown on the page.
- **Thin collection pages**: add 50–150 words of unique intro copy above or below the grid, plus FAQs where useful.
- **Out-of-stock products**: keep the page live (200) with `availability` OutOfStock; redirect (301) only when the
  product is permanently gone, to the closest replacement or its collection.
- **Variant URLs** (`?variant=123`): canonicalise to the product; mark variants up with `ProductGroup` if the theme
  supports it (`references/ecommerce.md` §2).
- **Image alt text and file names** on product media: fix in bulk in the admin or via a CSV export.
- **Markets / multiple languages**: Shopify Markets outputs hreflang automatically per market subfolder or domain;
  verify with `scripts/hreflang_check.py`.

## Limits
- Read-only; the script never changes the store. Suggested fixes are applied in the admin, a CSV import or the theme.
- Checks the first 10 media per product; products beyond `--limit` are not scanned (reported totals show how many were).
- Does not see theme output (titles as rendered, JSON-LD, internal links): pair with `crawl_audit.py` on the storefront.
- Online Store 2.0 metafield-based SEO overrides set by apps are not read.
