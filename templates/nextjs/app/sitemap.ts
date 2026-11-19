// app/sitemap.ts — only canonical, indexable, 200-status marketing URLs. Never app/auth routes.
import type { MetadataRoute } from "next";
import { SITE } from "@/lib/seo";

// Replace with real sources: your CMS/MDX loaders, docs index, integrations list, etc.
async function getDynamicEntries(): Promise<{ path: string; updatedAt: string }[]> {
  // e.g. const posts = await getAllPosts(); return posts.map(p => ({ path: `/blog/${p.slug}`, updatedAt: p.updatedAt }));
  return [];
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  // path -> date the page content last really changed (update when you edit the page). Omit = no lastmod.
  const staticRoutes: Record<string, string | undefined> = {
    "/": undefined, "/pricing": undefined, "/features": undefined, "/integrations": undefined,
    "/docs": undefined, "/blog": undefined, "/changelog": undefined, "/about": undefined, "/contact": undefined,
  };
  const dynamic = await getDynamicEntries();
  return [
    // lastModified must be the real content change date; a fake "now" on every build teaches Google to ignore it.
    ...Object.entries(staticRoutes).map(([path, date]) => ({
      url: `${SITE.url}${path === "/" ? "" : path}`,
      ...(date ? { lastModified: date } : {}),
    })),
    ...dynamic.map((e) => ({ url: `${SITE.url}${e.path}`, lastModified: e.updatedAt })),
  ];
  // > 50,000 URLs: switch to generateSitemaps() and split by section.
}
