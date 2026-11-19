# super-seo

Skill Claude Code untuk SEO + GEO (carian AI) bagi setiap projek SaaS. Ia audit kod dan site (termasuk `localhost`
sebelum deploy), kemudian betulkan terus dalam kod.

Tiada DataForSEO, tiada API berbayar, tiada web app self-host, tiada `pip install`.

## Pasang
Keperluan: Claude Code dan Python 3.10 ke atas. Tiada `pip install`.

**Cara 1: clone terus ke folder skill**
```bash
# macOS / Linux
git clone https://github.com/digitalscapemy/super-seo.git ~/.claude/skills/super-seo
```
```powershell
# Windows
git clone https://github.com/digitalscapemy/super-seo.git "$env:USERPROFILE\.claude\skills\super-seo"
```
Untuk kemas kini: `git pull` dalam folder tersebut.

**Cara 2: clone ke mana-mana folder, kemudian salin dengan installer** (Windows; ikut `CLAUDE_CONFIG_DIR` kalau ditetapkan)
```powershell
git clone https://github.com/digitalscapemy/super-seo.git
powershell -ExecutionPolicy Bypass -File .\super-seo\install.ps1
```
Skill aktif secara automatik bila kau sebut SEO, GEO, sitemap, schema, "nak muncul dalam ChatGPT" dan lain-lain.
Atau panggil terus: "guna super-seo, audit projek ni".

## Contoh guna dalam projek
- "Audit SEO projek ni" → `audit.py --path . --url http://localhost:3000`, kemudian laporan dan cadangan fix
- "Setup SEO untuk Next.js app ni" → metadata, sitemap, robots, JSON-LD, llms.txt dipasang dalam kod
- "Buat page pricing / vs Bukku yang SEO friendly" → page spec, kajian SERP, copy answer-first, schema
- "Kenapa ChatGPT tak sebut produk aku?" → semak akses bot AI, tracker prompt, analisis jurang
- "Sebelum launch" → senarai semakan pelancaran + baseline + gate CI

## Skrip (boleh juga guna tanpa Claude)
```
python scripts/audit.py --path . --url http://localhost:3000 --fail-on critical
python scripts/project_scan.py .
python scripts/crawl_audit.py https://domain.com --max-pages 100 --out .seo/audits
python scripts/ai_access.py https://domain.com
python scripts/content_check.py content/blog/post.mdx
python scripts/ai_visibility.py checklist .seo/ai-visibility
python scripts/drift.py --latest .seo/audits
```
PageSpeed / CrUX (`--psi`): set `PSI_API_KEY` (kunci percuma dari Google Cloud Console). Tanpa kunci, ia guna kuota
kongsi yang selalu penuh.
