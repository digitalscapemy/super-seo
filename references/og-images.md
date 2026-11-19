# Open Graph and social card images

The image shown when a URL is shared in chat apps, social networks, team tools and AI chat interfaces. Not a ranking
factor, but it drives click-through on shared links and is the first impression of the brand. Generate them **in
code** so every page has one that is correct, legible and current.

## 1. Specs
| Item | Rule |
|---|---|
| Size | **1200×630 px** (1.91:1). Minimum 600×315; below that many platforms show a small thumbnail |
| File | PNG or JPEG (WebP is not supported everywhere for cards); **< 5 MB** (aim < 300 KB; some apps fetch with short timeouts) |
| URL | **Absolute** `https://` URL on a public host; no auth, not blocked by robots.txt, no redirects to login; stable (platforms cache by URL) |
| Tags | `og:image`, `og:image:width` = 1200, `og:image:height` = 630, `og:image:alt` (describes the image), `og:image:type` |
| Companion tags | `og:title`, `og:description`, `og:url` (canonical), `og:type`, `og:site_name` |
| Large card | `twitter:card` = `summary_large_image` (+ `twitter:image` / `twitter:image:alt` optional; most platforms fall back to og:*) |
| One image | Put the best image first; several `og:image` tags are allowed but most platforms use the first |
| Cache busting | Changing the image? Change its URL (`?v=2` or a content hash); platforms keep the old one for days otherwise |

### Safe zones (cropping)
Platforms crop the same 1200×630 image differently: 1.91:1 in most link previews, **1:1 square** in some chat apps,
search features and small previews, and rounded corners in others.
- Keep the **logo and headline inside the central 630×630 square** (x = 285–915 px) so a square crop still works.
- Keep a **60 px margin** on every edge; nothing important within 40 px of the corners.
- Text ≥ 48 px for the headline, ≥ 28 px for supporting text; at most ~60 characters of headline; high contrast
  (WCAG AA 4.5:1). The card is often shown at ~500 px wide or smaller on phones.
- Avoid thin fonts, small screenshots full of UI text, and important content at the bottom edge (some apps overlay
  the domain/title there).

## 2. Dynamic generation in code
Render title/brand **as text in code** (not baked into an AI or stock picture) so it is sharp, correct, localised and
updated with the page.

### Next.js (App Router): `ImageResponse` from `next/og`
File convention: `app/<route>/opengraph-image.tsx` (Next adds the og:image tags automatically; also
`twitter-image.tsx`). For dynamic routes, read params; static routes are rendered at build time.
```tsx
// app/blog/[slug]/opengraph-image.tsx
import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { getPost } from "@/lib/posts";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Article cover";          // overridden per page below via generateImageMetadata if needed

export default async function Image({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = await getPost(slug);
  const font = await readFile(join(process.cwd(), "assets/Inter-SemiBold.ttf"));
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between",
                    padding: 64, background: "linear-gradient(135deg,#0B1220 0%,#1E3A8A 100%)", color: "#fff", fontFamily: "Inter" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 32 }}>
          {/* logo as inline SVG or <img src={absoluteUrl}> */}
          <span>Acme</span>
        </div>
        <div style={{ display: "flex", fontSize: 64, lineHeight: 1.1, maxWidth: 1000 }}>{post.title}</div>
        <div style={{ display: "flex", fontSize: 28, opacity: 0.8 }}>{post.category} · acme.com</div>
      </div>
    ),
    { ...size, fonts: [{ name: "Inter", data: font, weight: 600, style: "normal" }] }
  );
}
```
Notes: only flexbox layout and a CSS subset are supported (every `div` with several children needs
`display: flex`); bundle the font file (TTF/OTF/WOFF, not WOFF2); keep the bundle small (edge size limits); set
`metadataBase` in the root layout so the generated URL is absolute. Pages Router: an API route returning
`ImageResponse`, referenced from `<meta property="og:image">`.

### Other Node stacks: satori (+ resvg)
`satori` renders the same JSX/flexbox subset to SVG; `@resvg/resvg-js` converts it to PNG. Run it in a route
(`/og/:slug.png`) with caching (`Cache-Control: public, max-age=31536000, immutable` on hashed URLs) or at build time
(Astro, SvelteKit, Nuxt, Remix/React Router, Vite SSG) writing files to `public/og/<slug>.png`.
```js
import satori from "satori";
import { Resvg } from "@resvg/resvg-js";
const svg = await satori(html /* JSX or satori-html object */, { width: 1200, height: 630, fonts: [{ name: "Inter", data: fontBuffer, weight: 600 }] });
const png = new Resvg(svg, { fitTo: { mode: "width", value: 1200 } }).render().asPng();
```
Non-Node backends (Laravel, Django, Rails, Go): a headless-browser screenshot of an HTML template at 1200×630 in a
queue job, or an image library (Intervention Image, Pillow) drawing text on a template; cache the file.

### Static fallback (always)
- A brand default `/og-default.png` (1200×630, logo + tagline) referenced in the root/site metadata, used whenever a
  page has no specific image and when the generator fails.
- Home, pricing and top feature pages: hand-designed images are fine (they change rarely).

## 3. Brand template guidance
- 2–3 layouts maximum (article, product/feature, generic), same grid, logo position and type scale everywhere so
  shares are recognisable.
- Background: brand colour or gradient, subtle pattern, or hero art (§4) behind a solid/blurred text panel.
- Content: page title (shortened if > 60 chars), one supporting line (category, author, date, price "from RM29/mo"),
  domain. Optional author photo for articles, product UI crop for features (large, few details).
- Localised pages: render the localised title; check fonts cover the script (CJK, Thai, Arabic, Tamil need their own fonts).
- Dark/light: the card must work on both white and dark chat backgrounds — avoid pure white or pure black edges
  that blend in; a 1–2 px border or inner padding helps.
- `og:image:alt`: describe the card ("Acme logo with the headline 'How to run payroll in Malaysia'").

## 4. Using image generation for hero/OG art
If an image-generation tool is available in the session, it can produce **background art, illustrations or
hero visuals** for the template. Keep these rules:
- Generate **art without text**; render the headline, logo, prices and any words in code (generated text is often
  misspelled, off-brand and cannot be localised or updated).
- Prompt with the brand palette, style and the 1.91:1 frame; ask for empty space where the text panel goes.
- Save the art as a static asset (`public/og/bg-<theme>.webp/png`), compress it, and composite it in the generator
  (`<img>` with an absolute URL in `ImageResponse`/satori).
- No real people's likeness, competitor logos or trademarked characters; no fake UI that misrepresents the product.
- Same art can serve the page hero (resize/re-crop; run `scripts/image_audit.py` for size and format).

## 5. Testing
- `crawl_audit.py` flags missing `og:title`/`og:description`/`og:image` and relative `og:image` URLs.
- Fetch the image URL directly: 200, `Content-Type: image/png|jpeg`, size < 5 MB, 1200×630 (`image_audit.py` on a page
  that shows it, or `curl -I`).
- Check the rendered tags in the **server HTML** (view-source), not only in the DOM after JS.
- Use the social platforms' card/preview debuggers (most large networks offer one) to see the preview and to
  **force a re-scrape** after changing the image; paste the URL into a private chat/team-tool channel as a final check.
- Check both the 1.91:1 and the square crop; test a very long title and a non-Latin title.
- Preview/staging: platforms cannot reach localhost or auth-protected previews; test on a public preview URL that is
  `noindex` but publicly reachable.
