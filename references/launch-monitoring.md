# Launch gate & ongoing monitoring

## Pre-launch gate (run on a production build or preview URL)
```
python scripts/audit.py --path . --url http://localhost:3000 --fail-on critical
```
Must pass (no critical):
- [ ] Every public page server-renders its main content (no `csr_shell`, no `csr_spa`)
- [ ] Production: no `noindex` on public pages; staging/preview: `noindex` + robots `Disallow: /`
- [ ] `metadataBase`/canonical/sitemap/OG use the production domain (no localhost, no `*.vercel.app`)
- [ ] robots.txt 200, allows Googlebot, bingbot and AI search/user agents; sitemap declared
- [ ] sitemap.xml: only canonical 200 indexable URLs, no app/auth routes
- [ ] Unique title + description on every public page; OG image renders
- [ ] Organization + WebSite + SoftwareApplication JSON-LD valid, no placeholders, no fake ratings
- [ ] Unknown URLs return 404 (not 200 index.html)
- [ ] http→https and www/apex redirect in one 301 hop
- [ ] App/auth routes noindexed and behind auth
- [ ] Pricing, comparison tables and FAQs are in the HTML (not client-only widgets)
Should pass (high): favicon, `lang`, alt text, no broken internal links, hero image not lazy, llms.txt valid if present.

## Launch day
1. Google Search Console: verify the **Domain property** (DNS TXT), submit `sitemap.xml`, URL-inspect home + pricing.
2. Bing Webmaster Tools: import from GSC or verify; submit sitemap (Bing feeds Copilot, DuckDuckGo and part of ChatGPT search).
3. IndexNow (Bing/Yandex/Naver/Seznam, not Google): host `/<key>.txt`; ping on publish/update. Many hosts/CMS plugins do it.
4. Run `ai_access.py` against the **live** domain: CDN/WAF settings (Cloudflare managed robots.txt, bot fight mode) only
   show up live.
5. Take the post-launch baseline: `crawl_audit.py https://domain --out .seo/audits`.
6. Create the AI visibility tracker and the first observed run (`ai-visibility.md`).

## Every release (CI or manual)
```
python scripts/crawl_audit.py $PREVIEW_URL --max-pages 100 --out .seo/audits --fail-on critical
python scripts/drift.py --latest .seo/audits --fail-on critical
```
GitHub Actions sketch (`.github/workflows/seo-gate.yml`): on `deployment_status` success for preview → checkout →
setup-python 3.11 → run the two commands with `PREVIEW_URL=${{ github.event.deployment_status.target_url }}`.
Note preview deploys are noindex by design; the gate ignores that only if you pass the preview URL knowingly — check
`noindex` findings against the production config instead (project_scan).

## Monthly
- GSC: pages "Crawled/Discovered – currently not indexed", striking-distance queries (pos 5–20, ≥ 50 impressions),
  queries losing clicks; CTR outliers → rewrite titles/descriptions by hand.
- CWV field data (PSI/CrUX, PHONE): LCP/INP/CLS at p75; regressions > 20% → investigate.
- AI visibility run + trend (same prompts, same market).
- Refresh comparison pages' competitor pricing ("as of" dates); update changelog; prune or improve pages with zero
  impressions after 3–6 months.
- Re-run `ai_access.py` (vendors add new bots; CDN defaults change).

## Quarterly
- Re-verify AI crawler list and vendor policies (geo-ai-search.md §2).
- Competitor sitemap diff: new page families they launched.
- Review programmatic batches against the gates in `saas-pages.md` §4.
