# E-commerce SEO (products, categories, merchant listings)

For SaaS with a store, marketplaces, D2C brands and Shopify/WooCommerce/headless shops. Pair with `schema.md`
(JSON-LD rules), `technical-seo.md` (canonicals, sitemaps) and `extensions/shopify/` for Shopify stores.

## Contents
1. Product page essentials · 2. Product schema (Offer, AggregateOffer, ProductGroup) · 3. Merchant listing vs product
snippet · 4. Returns and shipping · 5. Faceted navigation · 6. Out-of-stock and discontinued · 7. Category pages
8. Merchant Center free listings · 9. Images · 10. Reviews and ratings

## 1. Product page essentials
- One indexable URL per product (variants via parameter or path, canonicalised to the product or self-canonical when
  the variant has its own demand). Server-rendered title, price, availability, images, description and JSON-LD.
- Unique description written for buyers: what it is, who it is for, materials/specs, sizes, compatibility, care,
  what's in the box. Manufacturer copy shared by every reseller adds no information gain.
- Title pattern: `<Product name> <key attribute> | <Brand>` (≤ 60 chars). H1 = product name.
- Price, currency, stock status and delivery estimate **visible in the HTML**, not only after JS or in a modal.
- Breadcrumbs (visible + `BreadcrumbList`) from the primary category.

## 2. Product schema
One server-rendered `Product` graph per product page. Values must match what the page shows **at crawl time**.

| Property | Rule |
|---|---|
| `name`, `image`, `description` | Required in practice; images crawlable, absolute URLs |
| `sku`, `gtin8/12/13/14`, `mpn`, `brand` (Brand object) | Add every identifier you have; GTIN is the strongest match key for Google Shopping |
| `offers` (`Offer`) | `price` (number, no currency symbol), `priceCurrency` (ISO 4217), `availability` (schema.org URL, e.g. `https://schema.org/InStock`), `url`, `itemCondition`, optional `priceValidUntil` |
| Sale price | Current price in `price`; strikethrough via `priceSpecification` with `priceType: https://schema.org/StrikethroughPrice` |
| `AggregateOffer` | Several sellers/prices for one product (marketplace): `lowPrice`, `highPrice`, `offerCount`, `priceCurrency`. Product snippets only; merchant listings need a concrete `Offer` |
| Variants | `ProductGroup` with `productGroupID`, `variesBy` (e.g. `https://schema.org/size`, `/color`), `hasVariant` → `Product` per variant (own `sku`, `offers`, `image`, attribute values); each variant `isVariantOf` the group |
| `aggregateRating`, `review` | Only from real reviews of this product shown on the page (§10) |

Example (variants + store-wide return policy referenced by `@id`; real values only):
```json
{"@context": "https://schema.org", "@graph": [
  {"@type": "ProductGroup", "@id": "https://shop.example/products/wool-coat#group", "name": "Wool Overcoat",
   "productGroupID": "WC-01", "brand": {"@type": "Brand", "name": "Northwind"},
   "variesBy": ["https://schema.org/size", "https://schema.org/color"],
   "hasVariant": [
     {"@type": "Product", "sku": "WC-01-NAVY-M", "gtin13": "0123456789012", "name": "Wool Overcoat, Navy, M",
      "size": "M", "color": "Navy", "image": "https://shop.example/img/wc-navy-front.jpg",
      "offers": {"@type": "Offer", "url": "https://shop.example/products/wool-coat?variant=101",
                 "price": 499.00, "priceCurrency": "MYR", "availability": "https://schema.org/InStock",
                 "itemCondition": "https://schema.org/NewCondition",
                 "hasMerchantReturnPolicy": {"@id": "https://shop.example/#returns"}}}]},
  {"@type": "Organization", "@id": "https://shop.example/#organization", "name": "Northwind", "url": "https://shop.example/",
   "hasMerchantReturnPolicy": {"@type": "MerchantReturnPolicy", "@id": "https://shop.example/#returns",
     "returnPolicyCountry": "MY", "applicableCountry": "MY",
     "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 30,
     "returnMethod": "https://schema.org/ReturnByMail", "returnFees": "https://schema.org/FreeReturn"}}]}
```

Rules:
- Price/availability in markup **must equal the visible page**; mismatches get items disapproved in Merchant Center
  and rich results dropped. Update markup in the same render as the price (no stale cached JSON-LD).
- Multi-currency: the markup's currency matches the currency shown for that URL/market.
- Category/listing pages: do **not** mark every listed product as `Product` with offers; use `ItemList` (or nothing).
- Don't inject Product JSON-LD client-side via apps/tag managers; one graph, no duplicates from theme + app.

## 3. Merchant listing vs product snippet
| | Product snippet | Merchant listing |
|---|---|---|
| For | Pages about a product (reviews, comparisons, also shops) | Pages where the customer can **buy** the product |
| Shows | Rating stars, price, availability in the normal result | Shopping knowledge panel, Shopping tab, popular products, rich price/shipping/returns |
| Needs | `name` + one of `review`, `aggregateRating` or `offers` | `name`, `image`, `offers` with `price` + `priceCurrency` (concrete `Offer`, not only `AggregateOffer`) |
| Recommended | — | `availability`, `shippingDetails`, `hasMerchantReturnPolicy`, GTIN/brand, `itemCondition` |
Validate with the Rich Results Test (it reports both types separately) and watch the Search Console "Merchant
listings" and "Product snippets" reports.

## 4. Returns and shipping
- `hasMerchantReturnPolicy` → `MerchantReturnPolicy`: **`returnPolicyCountry`** (ISO 3166-1 alpha-2 country the item
  is returned to; required by Google), `applicableCountry` (where the policy applies), `returnPolicyCategory`
  (`MerchantReturnFiniteReturnWindow` + `merchantReturnDays`, `MerchantReturnNotPermitted`, `MerchantReturnUnlimitedWindow`),
  `returnMethod`, `returnFees` / `returnShippingFeesAmount`.
- `shippingDetails` → `OfferShippingDetails`: `shippingRate` (MonetaryAmount), `shippingDestination` (DefinedRegion with
  `addressCountry`), `deliveryTime` (`handlingTime` + `transitTime` as QuantitativeValue in days).
- Store-wide policies can sit **once on `Organization`** (`hasMerchantReturnPolicy`, `hasShippingService`) instead of
  repeating on every offer; product-level markup overrides it. Merchant Center account-level settings also override
  markup for Shopping surfaces.
- The policy page itself must exist and match (days, fees, countries).

## 5. Faceted navigation
Filters (size, colour, price, brand, sort) multiply URLs and waste crawl budget. Decide per facet:

| Facet type | Example | Treatment |
|---|---|---|
| Has its own search demand and stock | `/shoes/running/` , `/dresses/red/` | Static, indexable URL; self-canonical; unique H1 + intro; in sitemap; linked from navigation |
| Useful to users, no demand | `?size=9`, `?color=navy&material=wool` | `rel=canonical` to the unfiltered category; not in sitemap; links can be `nofollow` or JS buttons |
| Sort, view, pagination size, session | `?sort=price_asc`, `?view=grid`, `?limit=96` | `Disallow` the parameter patterns in robots.txt (saves crawling), canonical to base |
| Infinite combinations | multi-select filters, price sliders | robots `Disallow` patterns **or** client-side filtering that does not create crawlable URLs |
Rules:
- Don't combine `Disallow` with `noindex` on the same URL (a disallowed URL is never fetched, so its noindex is never seen).
  Use `noindex` (crawlable) to clean up URLs already indexed; `Disallow` to stop new crawling afterwards.
- Canonical is a hint; filtered pages with very different content may be indexed anyway — keep the crawlable link
  graph small.
- Pagination: `?page=2` pages self-canonical (not to page 1), crawlable `<a href>` next links; `rel=next/prev` is not
  used by Google. Avoid infinite scroll without paginated URLs.
- Consistent parameter order and lowercase values; one URL per filter state.

## 6. Out-of-stock and discontinued products
| Situation | Do | Don't |
|---|---|---|
| Temporarily out of stock | Keep **200**, show it clearly, `availability: OutOfStock` (or `BackOrder`/`PreOrder` with `availabilityStarts`), restock date, alternatives, back-in-stock signup | 404, redirect to the category, noindex |
| Seasonal / returns regularly | Keep the URL live year-round; update availability | New URL each season |
| Permanently discontinued, clear successor | **301** to the replacement (or closest equivalent) | 301 everything to the home page (treated as soft 404) |
| Permanently gone, no equivalent | 301 to the most relevant category **or** 404/410 with helpful links | Keep a thin page forever |
| Product with valuable reviews/links, discontinued | Keep the page (200) as an archive with "discontinued" + successor link | Delete it |
Remove gone products from the sitemap and Merchant Center feed the same day.

## 7. Category pages
- The money page for non-brand queries ("men's running shoes"). Unique H1, 50–150 words of intro copy that helps
  choosing (above the grid, or short above + longer guide below), buying-guide links, FAQs from real questions.
- Grid of crawlable product links (`<a href>` in server HTML; "load more" must also exist as paginated URLs).
- Subcategory links, sensible default sort (best sellers / in-stock first), no empty categories (hide or 301).
- Avoid duplicates: one canonical category per concept; tag pages and collection copies canonical to it.
- `BreadcrumbList` + optional `ItemList`; no `Product` offers markup for every tile.

## 8. Google Merchant Center free listings
- Free product listings on the Shopping tab, Images, and (with markup) organic product results. Create a Merchant Center
  account, verify and claim the domain, configure shipping and returns, then add products via feed, platform
  integration, or **automatic collection from structured data** on the site.
- Feed values (title, price, availability, GTIN, image) must match the landing page and markup; mismatches → item
  disapprovals. Keep price/stock updates frequent (feed schedule or Content API) for fast-moving stock.
- Policies: real contact info, return policy page, secure checkout, no misleading pricing. Check Diagnostics weekly.
- Bing: Microsoft Merchant Center offers equivalent free listings; import the Google account to reuse feeds.

## 9. Images
- Several high-resolution images per product (Google recommends ≥ 50 K pixels; Merchant Center minimum 100×100,
  apparel 250×250; 800–2000 px wide is practical), multiple aspect ratios in markup (1×1, 4×3, 16×9) where possible.
- Main image: product on a plain background, no promotional text/watermarks/borders (Merchant Center rejects them).
- Crawlable URLs (not blocked by robots.txt, no auth), stable URLs (changing image URLs re-triggers processing).
- Descriptive file names and alt text (`navy-wool-overcoat-front.jpg`, "Navy wool overcoat, front view").
- Performance: WebP/AVIF, `srcset`/`sizes`, first gallery image `fetchpriority="high"` and not lazy. Run
  `scripts/image_audit.py <product url>`.

## 10. Reviews and ratings
- Mark up only **first-party reviews of the specific product**, collected by you (or your review platform) and
  **visible on the page**, with real `ratingValue`, `reviewCount`/`ratingCount` and dates.
- **No self-serving reviews**: Google ignores review stars for `Organization`/`LocalBusiness` about the business itself
  on its own site; don't mark up testimonials about your company as product ratings.
- **No third-party aggregated ratings in your own markup**: don't copy marketplace, review-site or app-store ratings
  into your `aggregateRating`. Link to those profiles instead.
- Never gate, incentivise without disclosure, filter out negative reviews, or hardcode a rating in a template.
- Pros/cons markup (`positiveNotes` / `negativeNotes`) is for editorial product reviews, not shop pages.
- Review apps that inject JSON-LD client-side: make sure the rating ends up in the server HTML, exactly once.

## Audit checklist (quick)
| Check | Tool |
|---|---|
| Product/Offer JSON-LD parse + required props, duplicates | `crawl_audit.py` (JSON-LD per page) + Rich Results Test |
| Price/availability in HTML = markup = feed | manual diff on 5–10 products; Merchant Center Diagnostics |
| Faceted URLs not in sitemap, canonical to base, robots patterns | `crawl_audit.py` (sitemap + canonical findings) |
| Product images: format, size, alt, hero priority | `image_audit.py` |
| Shopify catalogue: SEO fields, alt text, thin descriptions, redirects | `extensions/shopify/shopify.py audit` |
| hreflang across markets | `hreflang_check.py` |
