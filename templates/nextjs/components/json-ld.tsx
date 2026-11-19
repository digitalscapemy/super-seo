// components/json-ld.tsx — server-rendered JSON-LD (never inject structured data client-side:
// non-JS AI crawlers would never see it). Render inside a Server Component.
import { SITE, absoluteUrl } from "@/lib/seo";

type Json = Record<string, unknown>;

export function JsonLd({ data }: { data: Json | Json[] }) {
  const graph = Array.isArray(data) ? { "@context": "https://schema.org", "@graph": data } : { "@context": "https://schema.org", ...data };
  return (
    <script
      type="application/ld+json"
      // Escape "<" so a string inside the data can never close the script tag (XSS-safe).
      dangerouslySetInnerHTML={{ __html: JSON.stringify(graph).replace(/</g, "\\u003c") }}
    />
  );
}

const ORG_ID = `${SITE.url}/#organization`;
const SITE_ID = `${SITE.url}/#website`;
const APP_ID = `${SITE.url}/#software`;

/** Site-wide entity graph. Put once in the root layout (or on the homepage). */
export function siteGraph(): Json[] {
  return [
    {
      "@type": "Organization",
      "@id": ORG_ID,
      name: SITE.name,
      legalName: SITE.legalName,
      url: SITE.url,
      logo: { "@type": "ImageObject", url: absoluteUrl(SITE.logo) },
      description: SITE.description,
      sameAs: SITE.sameAs,
      // contactPoint: [{ "@type": "ContactPoint", contactType: "customer support", email: "[REPLACE]", availableLanguage: ["en", "ms"] }],
    },
    {
      "@type": "WebSite",
      "@id": SITE_ID,
      url: SITE.url,
      name: SITE.name,
      publisher: { "@id": ORG_ID },
      inLanguage: "en",
      // No SearchAction: Google retired the sitelinks search box (Nov 2024). Add it only if another consumer needs it.
    },
  ];
}

type Plan = { name: string; price: number; currency?: string; period?: "MONTH" | "YEAR"; url?: string; description?: string };

/** SoftwareApplication for the product. Use on the homepage and/or pricing page.
 *  Do NOT add aggregateRating unless it reflects real, visible, third-party-verifiable reviews on the page. */
export function softwareApp(opts: {
  plans: Plan[];
  category?: string;            // BusinessApplication | FinanceApplication | DeveloperApplication | ...
  features?: string[];
  screenshot?: string;
  releaseNotesUrl?: string;     // e.g. /changelog — freshness signal
  rating?: { value: number; count: number }; // only real data
}): Json {
  const currency = opts.plans[0]?.currency ?? "MYR";
  return {
    "@type": "SoftwareApplication",
    "@id": APP_ID,
    name: SITE.name,
    url: SITE.url,
    description: SITE.description,
    applicationCategory: opts.category ?? "BusinessApplication",
    operatingSystem: "Web",
    publisher: { "@id": ORG_ID },
    ...(opts.features ? { featureList: opts.features } : {}),
    ...(opts.screenshot ? { screenshot: absoluteUrl(opts.screenshot) } : {}),
    ...(opts.releaseNotesUrl ? { releaseNotes: absoluteUrl(opts.releaseNotesUrl) } : {}),
    offers: opts.plans.map((p) => ({
      "@type": "Offer",
      name: p.name,
      price: String(p.price),               // "0" for a free plan
      priceCurrency: p.currency ?? currency,
      url: absoluteUrl(p.url ?? "/pricing"),
      ...(p.description ? { description: p.description } : {}),
      ...(p.period && p.price > 0
        ? { priceSpecification: { "@type": "UnitPriceSpecification", price: String(p.price), priceCurrency: p.currency ?? currency, billingDuration: 1, billingIncrement: 1, unitCode: p.period === "YEAR" ? "ANN" : "MON" } }
        : {}),
    })),
    ...(opts.rating ? { aggregateRating: { "@type": "AggregateRating", ratingValue: opts.rating.value, ratingCount: opts.rating.count } } : {}),
  };
}

export function breadcrumbs(items: { name: string; path: string }[]): Json {
  return {
    "@type": "BreadcrumbList",
    itemListElement: items.map((it, i) => ({ "@type": "ListItem", position: i + 1, name: it.name, item: absoluteUrl(it.path) })),
  };
}

export function article(a: {
  title: string; description: string; path: string; image: string;
  published: string; modified?: string;
  author: { name: string; url?: string; jobTitle?: string; sameAs?: string[] };
}): Json {
  return {
    "@type": "Article",
    headline: a.title,
    description: a.description,
    image: absoluteUrl(a.image),
    datePublished: a.published,
    dateModified: a.modified ?? a.published,
    mainEntityOfPage: absoluteUrl(a.path),
    author: { "@type": "Person", name: a.author.name, ...(a.author.url ? { url: absoluteUrl(a.author.url) } : {}),
              ...(a.author.jobTitle ? { jobTitle: a.author.jobTitle } : {}), ...(a.author.sameAs ? { sameAs: a.author.sameAs } : {}) },
    publisher: { "@id": ORG_ID },
  };
}

/** FAQPage: Google shows FAQ rich results only for well-known government/health sites (since Aug 2023).
 *  Still fine to emit when the Q&A is VISIBLE on the page; it helps other parsers. Never mark up hidden text. */
export function faq(qa: { q: string; a: string }[]): Json {
  return {
    "@type": "FAQPage",
    mainEntity: qa.map(({ q, a }) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })),
  };
}
