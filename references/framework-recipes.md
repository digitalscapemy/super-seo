# Framework recipes: implement the fix in code

Run `scripts/project_scan.py .` first; it prints the framework, rendering mode and missing pieces. Then apply the
matching recipe. Copy-ready files live in `templates/`. Always prefer the framework's native API over hand-written tags.

## Contents
- Architecture decision: marketing site vs app
- Next.js App Router (14/15/16) · Next.js Pages Router
- Vite / React / Vue SPA · Laravel (Blade, Inertia) · Astro · Nuxt · SvelteKit · Remix / React Router 7 / TanStack Start
- Static HTML + nginx (EasyPanel / Docker)
- Markdown for agents (optional) · Verify locally

## Architecture decision: marketing site vs app (decide first)
| Situation | Do this |
|---|---|
| App is a CSR SPA (Vite/CRA/Electron web build) and you need SEO | Keep the app CSR on `app.domain.com` (noindex). Build the public site (home, pricing, features, docs, blog, comparisons) with an SSR/SSG framework (Astro or Next.js) on `domain.com`. Cheapest, cleanest. |
| One Next.js/Nuxt/SvelteKit codebase for both | Route groups: `(marketing)` server-rendered + indexable; `(app)` behind auth with `robots: { index: false }` in its layout. |
| Must stay one Vite SPA | Prerender public routes at build (vite-react-ssg / vike / vite-plugin-prerender), give each route its own head, serve real 404s. |
| Docs | SSG docs (Astro Starlight, Nextra, Fumadocs, Docusaurus, Mintlify) on `/docs` (subfolder beats subdomain for consolidating authority, but either works). |

Subfolder (`domain.com/blog`) vs subdomain (`blog.domain.com`): Google says both work; subfolder keeps internal linking and
analytics simpler. Don't move an established subdomain just for this.

## Next.js App Router (14/15/16)
Files from `templates/nextjs/`: `lib/seo.ts`, `components/json-ld.tsx`, `app/sitemap.ts`, `app/robots.ts`, `app/llms.txt/route.ts`.
1. **Root layout** `app/layout.tsx`: `export const metadata = rootMetadata;` (sets `metadataBase`, `title.template`,
   default OG/Twitter, env-aware robots). Add `<html lang="en">`. Render `<JsonLd data={siteGraph()} />` once.
2. **Each public page**: `export const metadata = pageMetadata({ title, description, path })`.
   Dynamic routes: 
   ```ts
   export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
     const { slug } = await params;            // Next 15+: params is a Promise
     const post = await getPost(slug);
     if (!post) return {};
     return pageMetadata({ title: post.title, description: post.excerpt, path: `/blog/${slug}`, type: "article",
                           publishedTime: post.publishedAt, modifiedTime: post.updatedAt });
   }
   export async function generateStaticParams() { return (await getAllPosts()).map(p => ({ slug: p.slug })); }
   ```
   Call `notFound()` for unknown slugs so they return a real 404 (catch-all 200s create soft-404s).
3. **App area**: `app/(app)/layout.tsx` → `export const metadata = { robots: { index: false, follow: false } };`
4. **OG images**: `app/opengraph-image.tsx` (and per route) with `ImageResponse` from `next/og`, 1200×630. Or a static
   `app/opengraph-image.png`.
5. **Icons**: `app/icon.png` (or `.svg`), `app/apple-icon.png`, `app/favicon.ico`.
6. **Sitemap/robots/llms.txt**: templates above. Large sites: `generateSitemaps()`.
7. **i18n**: `alternates: { canonical: "/ms/pricing", languages: { "en": "/pricing", "ms-MY": "/ms/pricing", "x-default": "/pricing" } }`.
8. **Content in HTML**: fetch data in Server Components (or `generateStaticParams` + ISR `export const revalidate = 3600`),
   not in `useEffect`. Marketing pages: static or ISR; avoid `dynamic = "force-dynamic"` unless needed (TTFB).
9. **Images**: `next/image` with `alt`, `width/height` or `fill` + sized parent; `priority` on the hero only.
10. **Preview safety**: `isProduction` in `lib/seo.ts` uses `VERCEL_ENV`; on other hosts set `SEO_ALLOW_INDEX=false` on
    staging. Next 16 renamed `middleware.ts` to `proxy.ts`; use whichever your version expects.
11. **Trailing slash**: pick one (`trailingSlash` in `next.config`) and keep canonicals consistent with it.

## Next.js Pages Router
- `next/head` per page with title, description, canonical, OG; `_document.tsx` for `<Html lang>`.
- Data via `getStaticProps` / `getServerSideProps` (content in HTML). Sitemap via `next-sitemap` (postbuild) or an API
  route that writes XML. JSON-LD with the same `JsonLd` component inside `<Head>`.

## Vite / React / Vue SPA
CSR by default: AI crawlers see an empty `<div id="root">`. Options, best first:
1. **Split**: public site on Astro/Next (see decision table); SPA stays the app.
2. **Prerender** public routes at build: `vite-react-ssg` (React Router), `vike` (SSR/SSG for React/Vue/Solid),
   `vite-ssg` (Vue). Each route gets its own `<head>` via `react-helmet-async` / `@unhead/react` / `@vueuse/head`.
3. Minimum stopgap: real `<title>`, description, OG, canonical and JSON-LD in `index.html`; `public/robots.txt`,
   `public/sitemap.xml`, `public/llms.txt`. One head for every route is still weak (duplicate titles).
Server config: unknown paths must return 404, not `index.html` with 200 (soft-404, also breaks llms.txt detection).

## Laravel
- **Blade**: server-rendered, good. Shared `layouts/app.blade.php` with `@yield('title')`, `@section('meta')`,
  canonical `{{ url()->current() }}` (strip query), `<html lang="{{ str_replace('_','-',app()->getLocale()) }}">`.
  Sitemap: `spatie/laravel-sitemap` (scheduled `sitemap:generate`). robots.txt in `public/` or a route that varies by
  `app()->environment()`. JSON-LD: `@verbatim` block or `json_encode($data, JSON_UNESCAPED_SLASHES|JSON_HEX_TAG)`.
- **Inertia (Vue/React)**: CSR unless Inertia SSR is enabled (`php artisan inertia:start-ssr`, `ssr.js` entry). Use
  Inertia `<Head>` per page. Without SSR, serve public pages as Blade.
- App routes: middleware adding `X-Robots-Tag: noindex` for `/app/*`, `/dashboard/*`.

## Astro (recommended for marketing sites and docs)
- `site: "https://domain.com"` in `astro.config.mjs` (required for canonicals/sitemap). `@astrojs/sitemap` integration.
- Layout component with `<title>`, description, canonical `new URL(Astro.url.pathname, Astro.site)`, OG, JSON-LD
  `<script type="application/ld+json" set:html={JSON.stringify(data)} />`.
- Islands keep JS off content; content is static HTML by default. Docs: Starlight.
- llms.txt: `src/pages/llms.txt.ts` exporting `GET` that returns text from your content collections.

## Nuxt 3
- `useSeoMeta({ title, description, ogTitle, ogDescription, ogImage, twitterCard })` per page; `useHead` for canonical/lang.
- `@nuxtjs/seo` (bundles sitemap, robots, schema-org, og-image) with `site: { url, name }` in `nuxt.config`.
- Keep `ssr: true` (default). Route rules: `routeRules: { "/app/**": { ssr: false, robots: false } }`.

## SvelteKit
- `<svelte:head>` per page; `export const prerender = true` for marketing routes; keep `ssr` on.
- `src/routes/sitemap.xml/+server.ts` and `robots.txt/+server.ts` returning text with the right content-type.

## Remix / React Router 7 / TanStack Start
- `meta` export (Remix/RR7) or `head` in route options (TanStack) per route; SSR on by default — check
  `react-router.config.ts` does not set `ssr: false`. Resource routes for `sitemap.xml`, `robots.txt`, `llms.txt`.

## Static HTML + nginx (EasyPanel / Docker)
- Every page its own `<title>`, description, canonical, OG, JSON-LD in the HTML file.
- nginx: `try_files $uri $uri/ =404;` (not `/index.html` for unknown paths on a content site), gzip/brotli on, long
  cache for hashed assets, `add_header X-Robots-Tag "noindex" always;` in the staging server block only.
- Ship `robots.txt`, `sitemap.xml`, `llms.txt`, favicon, `og.png` in the web root.

## Markdown for agents (optional, low priority)
Serve a clean Markdown copy when `Accept: text/markdown` is requested (with `Vary: Accept`), or at `<path>.md`.
Cloudflare's "Markdown for Agents" does it at the edge. No mainstream consumer AI is confirmed to request it; do it for
docs-heavy / developer SaaS where coding agents read your docs. Never serve different *content* to bots (cloaking).

## Verify locally (before deploy)
```
npm run build && npm run start            # or: next build && next start / vite build && vite preview / php artisan serve
python scripts/crawl_audit.py http://localhost:3000 --max-pages 80 --out .seo/audits
python scripts/ai_access.py http://localhost:3000 --no-probe
```
Dev servers (`next dev`) differ from production output; audit a production build.
