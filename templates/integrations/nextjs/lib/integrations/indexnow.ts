// lib/integrations/indexnow.ts — notify Bing/Yandex/Naver/Seznam when public URLs change (Google does not use IndexNow).
// Serve the key: app/[key].txt is awkward in Next; instead add a rewrite  { source: "/:key([a-f0-9]{32}).txt", destination: "/api/indexnow-key/:key" }
// and return the key as text/plain only when it matches the host's key.
import "server-only";

export async function submitIndexNow(host: string, key: string, urls: string[]) {
  const urlList = [...new Set(urls)].filter((u) => new URL(u).host === host).slice(0, 10_000);
  if (!urlList.length || process.env.VERCEL_ENV === "preview") return { skipped: true };
  const res = await fetch("https://api.indexnow.org/indexnow", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ host, key, keyLocation: `https://${host}/${key}.txt`, urlList }),
    signal: AbortSignal.timeout(20_000),
  });
  // 200/202 ok · 400 bad request · 403 key not found · 422 URL/host mismatch · 429 too many requests (retry later)
  return { status: res.status };
}
