// app/robots.ts — environment-aware. Preview/staging is never crawlable; production welcomes
// search engines AND AI search/answer crawlers. Training crawlers are a business decision (see TRAINING_POLICY).
import type { MetadataRoute } from "next";
import { SITE, isProduction } from "@/lib/seo";

const PRIVATE = ["/api/", "/app/", "/dashboard/", "/account/", "/settings/", "/billing/", "/login", "/signup", "/auth/"];

// AI crawlers that fetch content to CITE/LINK it in answers or fetch live for a user. Allow for AI visibility.
const AI_RETRIEVAL = ["OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot", "Perplexity-User", "DuckAssistBot", "MistralAI-User"];
// Crawlers/tokens that collect TRAINING data. Allowing = your content can shape model knowledge of your brand.
const AI_TRAINING = ["GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "meta-externalagent", "CCBot"];
const TRAINING_POLICY: "allow" | "block" = "allow"; // [REPLACE] decide once per product

export default function robots(): MetadataRoute.Robots {
  if (!isProduction) {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }
  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: PRIVATE },
      { userAgent: AI_RETRIEVAL, allow: "/", disallow: PRIVATE },
      TRAINING_POLICY === "allow"
        ? { userAgent: AI_TRAINING, allow: "/", disallow: PRIVATE }
        : { userAgent: AI_TRAINING, disallow: "/" },
    ],
    sitemap: `${SITE.url}/sitemap.xml`,
    host: SITE.url,
  };
}
// NOTE: Cloudflare can prepend its own "managed" robots.txt block that disallows AI bots.
// After deploy run: python scripts/ai_access.py https://yourdomain.com  (it detects that block).
