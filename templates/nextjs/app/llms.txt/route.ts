// app/llms.txt/route.ts — serves /llms.txt as text/plain, built from the same data as the sitemap
// so it never drifts. Keep it an INDEX of the pages an agent needs; bulk text goes to /llms-full.txt.
import { SITE } from "@/lib/seo";

export const dynamic = "force-static";

const sections: { title: string; links: { title: string; path: string; note: string }[] }[] = [
  {
    title: "Product",
    links: [
      { title: "Overview", path: "/", note: "[REPLACE: what it does, for whom, in one factual line]" },
      { title: "Pricing", path: "/pricing", note: "[REPLACE: plans, free tier, currency, billing periods]" },
      { title: "Features", path: "/features", note: "[REPLACE]" },
    ],
  },
  {
    title: "Docs",
    links: [
      { title: "Getting started", path: "/docs", note: "[REPLACE]" },
      { title: "API reference", path: "/docs/api", note: "[REPLACE]" },
    ],
  },
  {
    title: "Optional",
    links: [
      { title: "Changelog", path: "/changelog", note: "Release notes, newest first" },
      { title: "Blog", path: "/blog", note: "[REPLACE]" },
    ],
  },
];

export function GET() {
  const body = [
    `# ${SITE.name}`,
    "",
    `> ${SITE.description}`,
    "",
    "[REPLACE: 2-4 sentences of facts an AI should get right: who it is for, key differentiator, regions/currencies, integrations, what it does NOT do.]",
    "",
    ...sections.flatMap((s) => [`## ${s.title}`, "", ...s.links.map((l) => `- [${l.title}](${SITE.url}${l.path}): ${l.note}`), ""]),
  ].join("\n");
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=3600" } });
}
