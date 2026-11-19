// lib/seo.ts — single source of truth for SEO across the marketing site.
// Copy into the project, then fill SITE. Works with Next.js 14/15/16 App Router.
import type { Metadata } from "next";

export const SITE = {
  name: "[REPLACE: Brand]",                       // how people write the brand (drives AI mention matching too)
  legalName: "[REPLACE: Brand Sdn. Bhd.]",
  url: process.env.NEXT_PUBLIC_SITE_URL ?? "https://[REPLACE: domain.com]", // production origin, no trailing slash
  description: "[REPLACE: one factual sentence: what it does, for whom]",
  locale: "en_MY",
  twitter: "@[REPLACE]",
  logo: "/logo.png",                              // >= 112x112, square-ish, crawlable
  sameAs: [
    // Profiles that prove the entity. Most valuable first. Delete what you don't have.
    "https://www.linkedin.com/company/[REPLACE]",
    "https://x.com/[REPLACE]",
    "https://github.com/[REPLACE]",
    "https://www.youtube.com/@[REPLACE]",
    "https://www.producthunt.com/products/[REPLACE]",
    "https://www.g2.com/products/[REPLACE]",
    // "https://www.wikidata.org/wiki/Q[REPLACE]",
  ],
} as const;

export const isProduction =
  process.env.VERCEL_ENV ? process.env.VERCEL_ENV === "production" : process.env.NODE_ENV === "production" && process.env.SEO_ALLOW_INDEX !== "false";

export const absoluteUrl = (path = "/") => new URL(path, SITE.url).toString();

/** Root metadata: use in app/layout.tsx as `export const metadata = rootMetadata;` */
export const rootMetadata: Metadata = {
  metadataBase: new URL(SITE.url),
  title: { default: `${SITE.name} — [REPLACE: primary value prop]`, template: `%s | ${SITE.name}` },
  description: SITE.description,
  applicationName: SITE.name,
  openGraph: { type: "website", siteName: SITE.name, locale: SITE.locale, url: "/" },
  twitter: { card: "summary_large_image", site: SITE.twitter },
  alternates: { canonical: "/" },
  robots: isProduction
    ? { index: true, follow: true, googleBot: { "max-snippet": -1, "max-image-preview": "large", "max-video-preview": -1 } }
    : { index: false, follow: false },
  // verification: { google: "...", other: { "msvalidate.01": "..." } },
};

type PageSeo = {
  title: string;          // 30-60 chars incl. template suffix; primary keyword early
  description: string;    // 70-160 chars; specific outcome + proof, no fluff
  path: string;           // e.g. "/pricing" — becomes the canonical
  image?: string;         // falls back to the route's opengraph-image convention
  noindex?: boolean;
  type?: "website" | "article";
  publishedTime?: string; // ISO 8601, articles only
  modifiedTime?: string;
};

/** Per-page metadata: `export const metadata = pageMetadata({...})` or return it from generateMetadata. */
export function pageMetadata(p: PageSeo): Metadata {
  return {
    title: p.title,
    description: p.description,
    alternates: { canonical: p.path },
    robots: p.noindex || !isProduction ? { index: false, follow: true } : undefined,
    openGraph: {
      title: p.title,
      description: p.description,
      url: p.path,
      type: p.type ?? "website",
      ...(p.image ? { images: [{ url: p.image, width: 1200, height: 630 }] } : {}),
      ...(p.publishedTime ? { publishedTime: p.publishedTime, modifiedTime: p.modifiedTime ?? p.publishedTime } : {}),
    },
    twitter: { card: "summary_large_image", title: p.title, description: p.description },
  };
}
