#!/usr/bin/env python3
"""project_scan.py - static SEO/GEO scan of a SaaS codebase (no server needed).

Detects the framework + rendering mode, finds the SEO plumbing that exists or is
missing (metadata, sitemap, robots, llms.txt, OG images, JSON-LD, hreflang),
lists routes without metadata, and greps for classic leaks (noindex left on,
localhost/staging URLs in canonical/sitemap, Disallow: /).

Usage:
  python project_scan.py .                 # markdown to stdout
  python project_scan.py . --json          # machine-readable
  python project_scan.py . --out .seo/audits
Python 3.10+, stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

IGNORE_DIRS = {"node_modules", ".next", ".nuxt", ".output", ".svelte-kit", ".astro", "dist", "build", "out",
               ".git", "coverage", ".vercel", ".turbo", ".cache", "vendor", "storage", ".venv", "venv", "__pycache__",
               ".seo", ".wrangler", "dist-electron", "release", "src-tauri", "target"}
CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro", ".mdx", ".md", ".html", ".php", ".blade.php"}
APP_SEGMENTS = re.compile(r"(^|/)(\(app\)|\(dashboard\)|\(auth\)|\(protected\)|app|dashboard|admin|account|settings|billing|login|signin|sign-in|signup|sign-up|auth|api)(/|$)", re.I)


def walk(root: Path, max_files: int = 20000):
    count = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name in IGNORE_DIRS or e.name.startswith(".") and e.name not in (".well-known",):
                    continue
                stack.append(e)
            elif e.is_file():
                count += 1
                if count > max_files:
                    return
                yield e


def read(p: Path, limit: int = 400_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def detect(root: Path) -> dict:
    pkg = {}
    pj = root / "package.json"
    if pj.exists():
        try:
            pkg = json.loads(read(pj))
        except json.JSONDecodeError:
            pkg = {}
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    fw, render = "unknown", "unknown"
    if ("electron" in deps or "@tauri-apps/api" in deps) and "next" not in deps and "astro" not in deps:
        fw, render = "desktop-app (electron/tauri)", "desktop"
    elif "next" in deps:
        fw = "nextjs"
        cfg = next((read(root / f) for f in ("next.config.ts", "next.config.mjs", "next.config.js") if (root / f).exists()), "")
        render = "static-export" if re.search(r"output\s*:\s*['\"]export['\"]", cfg) else "ssr/ssg"
    elif "@remix-run/react" in deps or "@react-router/dev" in deps:
        fw, render = "remix/react-router", "ssr (check ssr:false in react-router.config)"
    elif "@tanstack/react-start" in deps or "@tanstack/start" in deps:
        fw, render = "tanstack-start", "ssr"
    elif "nuxt" in deps:
        fw, render = "nuxt", "ssr (check ssr:false in nuxt.config)"
    elif "astro" in deps:
        fw, render = "astro", "ssg/ssr"
    elif "@sveltejs/kit" in deps:
        fw, render = "sveltekit", "ssr (check export const ssr=false)"
    elif "gatsby" in deps:
        fw, render = "gatsby", "ssg"
    elif "@angular/core" in deps:
        fw = "angular"
        render = "ssr" if "@angular/ssr" in deps or "@nguniversal/express-engine" in deps else "csr-spa"
    elif "vite" in deps and ("react" in deps or "vue" in deps or "solid-js" in deps or "svelte" in deps):
        fw = "vite-spa"
        prerender = [d for d in deps if re.search(r"prerender|vite-ssg|react-snap|vite-plugin-ssr|vike|@prerenderer", d)]
        render = f"prerendered ({', '.join(prerender)})" if prerender else "csr-spa"
    elif "react-scripts" in deps:
        fw, render = "create-react-app", "csr-spa"
    elif (root / "composer.json").exists() and (root / "artisan").exists():
        fw, render = "laravel", "ssr (blade) / check Inertia"
        comp = read(root / "composer.json")
        if "inertiajs" in comp:
            render = "inertia (csr unless SSR enabled)"
    elif (root / "astro.config.mjs").exists():
        fw, render = "astro", "ssg/ssr"
    seo_deps = sorted(d for d in deps if re.search(
        r"sitemap|next-seo|seo|schema-dts|react-helmet|@unhead|vue-meta|next-intl|i18n|og|satori|@vercel/og|robots|prerender|partytown", d, re.I))
    return {"framework": fw, "render": render, "package_name": pkg.get("name"), "seo_related_deps": seo_deps,
            "has_typescript": "typescript" in deps}


def scan_next_app_router(root: Path, files: list[Path]) -> dict:
    app_dirs = [d for d in (root / "app", root / "src" / "app") if d.is_dir()]
    if not app_dirs:
        return {}
    app = app_dirs[0]
    pages = [f for f in files if f.is_relative_to(app) and re.fullmatch(r"page\.(tsx|jsx|ts|js|mdx)", f.name)]
    layouts = {f.parent: read(f) for f in files if f.is_relative_to(app) and re.fullmatch(r"layout\.(tsx|jsx|ts|js)", f.name)}
    has_meta = re.compile(r"export\s+(const\s+metadata\b|(async\s+)?function\s+generateMetadata\b|const\s+generateMetadata\b)")
    routes = []
    for pg in sorted(pages):
        rel = pg.parent.relative_to(app).as_posix()
        url = "/" + "/".join(seg for seg in rel.split("/") if seg and not (seg.startswith("(") and seg.endswith(")")) and not seg.startswith("@"))
        url = url if url != "/." else "/"
        src = read(pg)
        own = bool(has_meta.search(src))
        inherited = None
        d = pg.parent
        while d.is_relative_to(app):
            if d in layouts and has_meta.search(layouts[d]):
                rel_d = d.relative_to(app).as_posix()
                inherited = "(root)" if rel_d in ("", ".") else rel_d
                break
            if d == app:
                break
            d = d.parent
        routes.append({
            "route": url.replace("/.", "/") or "/", "file": pg.relative_to(root).as_posix(),
            "own_metadata": own, "inherits_from_layout": inherited,
            "dynamic": "[" in rel, "static_params": "generateStaticParams" in src,
            "jsonld": "application/ld+json" in src, "app_area": bool(APP_SEGMENTS.search(rel)),
            "client_page": src.lstrip().startswith(("'use client'", '"use client"')),
        })
    root_layout = layouts.get(app, "")
    special = {name: any(f.is_relative_to(app) and re.fullmatch(rf"{name}\.(ts|tsx|js|jsx|xml|txt|png|jpg|jpeg|ico|svg|webmanifest|json)", f.name) for f in files)
               for name in ("sitemap", "robots", "manifest", "opengraph-image", "twitter-image", "icon", "apple-icon", "favicon", "not-found")}
    llms_route = any(f.is_relative_to(app) and "llms" in f.parent.name for f in files if f.name.startswith("route."))
    return {
        "router": "app", "app_dir": app.relative_to(root).as_posix(), "routes": routes,
        "root_metadataBase": "metadataBase" in root_layout, "root_has_metadata": bool(has_meta.search(root_layout)),
        "root_title_template": bool(re.search(r"template\s*:", root_layout)),
        "root_html_lang": bool(re.search(r"<html[^>]*\blang=", root_layout)),
        "special_files": special, "llms_txt_route": llms_route,
    }


def scan_next_pages_router(root: Path, files: list[Path]) -> dict:
    pdirs = [d for d in (root / "pages", root / "src" / "pages") if d.is_dir()]
    if not pdirs:
        return {}
    pdir = pdirs[0]
    routes = []
    for f in files:
        if not f.is_relative_to(pdir) or f.suffix not in (".tsx", ".jsx", ".ts", ".js", ".mdx"):
            continue
        rel = f.relative_to(pdir).with_suffix("").as_posix()
        if rel.startswith("api/") or rel.split("/")[-1].startswith("_"):
            continue
        src = read(f)
        url = "/" + re.sub(r"(^|/)index$", "", rel)
        routes.append({"route": url or "/", "file": f.relative_to(root).as_posix(),
                       "own_metadata": bool(re.search(r"next/head|<Head|NextSeo|<Seo\b|DefaultSeo", src)),
                       "inherits_from_layout": None, "dynamic": "[" in rel,
                       "static_params": "getStaticPaths" in src, "jsonld": "ld+json" in src,
                       "app_area": bool(APP_SEGMENTS.search(rel)), "client_page": False})
    return {"router": "pages", "routes": routes}


GREP_RULES = [
    # (code, severity, regex, message, file filter)
    ("noindex_in_code", "high", re.compile(r"""(index\s*:\s*false|['"]noindex|content=["'][^"']*noindex)""", re.I),
     "noindex directive in source; confirm it is limited to app/auth/staging", None),
    ("localhost_url", "high", re.compile(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?", re.I),
     "Hardcoded localhost URL (breaks canonical/sitemap/OG in prod if used there)", None),
    ("preview_domain_url", "medium", re.compile(r"https?://[a-z0-9-]+\.(vercel\.app|netlify\.app|pages\.dev|onrender\.com|herokuapp\.com|up\.railway\.app|fly\.dev|easypanel\.host)", re.I),
     "Preview/platform domain hardcoded; canonical/sitemap/OG should use the production domain", None),
    ("robots_disallow_all", "critical", re.compile(r"(?im)^\s*disallow:\s*/\s*$|disallow\s*:\s*['\"]/['\"]"),
     "robots rule 'Disallow: /' found; make sure it only applies to non-production or specific bots", "robots"),
    ("ai_bots_blocked", "medium", re.compile(r"(GPTBot|OAI-SearchBot|ClaudeBot|Claude-SearchBot|PerplexityBot|Google-Extended|CCBot|Applebot-Extended)", re.I),
     "AI crawler named in robots rules; verify allow/block is a deliberate policy", "robots"),
    ("img_no_alt", "low", re.compile(r"<(img|Image)\b(?![^>]*\balt=)[^>]*>", re.S),
     "<img>/<Image> without alt prop", None),
    ("backbutton_hijack", "high", re.compile(r"(addEventListener\(\s*['\"]popstate['\"][\s\S]{0,300}history\.pushState|onpopstate\s*=[\s\S]{0,200}pushState)"),
     "popstate handler that pushes history again: back-button hijacking is a Google spam policy violation", None),
    ("client_jsonld", "medium", re.compile(r"(document\.createElement\(\s*['\"]script['\"]\)[\s\S]{0,200}ld\+json|useEffect\([\s\S]{0,400}ld\+json)"),
     "JSON-LD injected client-side; non-JS AI crawlers never see it. Render it on the server", None),
    ("howto_schema", "info", re.compile(r"['\"]@type['\"]\s*:\s*['\"]HowTo['\"]"),
     "HowTo schema (no longer produces Google rich results)", None),
]


def grep(root: Path, files: list[Path]) -> list[dict]:
    hits = []
    for f in files:
        name = f.name.lower()
        if f.suffix not in CODE_EXT and name not in ("robots.txt",) and not name.endswith(".txt"):
            continue
        rel_posix = "/" + f.relative_to(root).as_posix()   # match against the path INSIDE the project only
        if re.search(r"(\.test\.|\.spec\.|__tests__|/tests?/|stories|\.stories\.)", rel_posix):
            continue
        src = read(f)
        if not src:
            continue
        for code, sev, rx, msg, filt in GREP_RULES:
            if filt == "robots" and "robots" not in name and "robots" not in src[:3000].lower():
                continue
            for m in rx.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                if code == "localhost_url" and re.search(r"(dev|test|mock|example|playwright|vite\.config|\.env)", f.name, re.I):
                    break
                # strip the router root (app/ or src/app/, pages/ or src/pages/) so "app/" itself isn't read as the app area
                route_rel = re.sub(r"^(src/)?(app|pages)/", "", f.relative_to(root).as_posix())
                if code == "noindex_in_code" and APP_SEGMENTS.search(route_rel):
                    sev = "info"  # noindex inside the authenticated app area is expected
                hits.append({"code": code, "severity": sev, "message": msg, "file": f.relative_to(root).as_posix(),
                             "line": line, "match": m.group(0)[:100]})
                if sum(1 for h in hits if h["code"] == code and h["file"] == f.relative_to(root).as_posix()) >= 3:
                    break
    return hits


def static_assets(root: Path) -> dict:
    found = {}
    for pub in ("public", "static", "src/static", "public_html", "assets"):
        d = root / pub
        if d.is_dir():
            for name in ("robots.txt", "sitemap.xml", "llms.txt", "llms-full.txt", "favicon.ico", "manifest.json",
                         "site.webmanifest", "og.png", "og-image.png", "humans.txt", ".well-known/security.txt"):
                if (d / name).exists():
                    found[name] = f"{pub}/{name}"
    idx = root / "index.html"
    if idx.exists():
        html = read(idx)
        found["index.html"] = {
            "title": bool(re.search(r"<title>[^<]{3,}</title>", html)),
            "description": "name=\"description\"" in html,
            "og_image": "og:image" in html,
            "root_div_only": bool(re.search(r"<body[^>]*>\s*<div id=\"(root|app)\"></div>", html)),
        }
    return found


def evaluate(info: dict) -> list[dict]:
    out = []

    def add(sev, code, msg):
        out.append({"severity": sev, "code": code, "message": msg})

    fw, render = info["detect"]["framework"], info["detect"]["render"]
    assets = info["static_assets"]
    if render == "desktop":
        add("info", "desktop_app", "Desktop app: web SEO does not apply to the app itself. Audit its marketing/download site instead.")
        return out
    if render in ("csr-spa",) or (fw == "vite-spa" and render == "csr-spa"):
        add("critical", "csr_spa", f"{fw} renders on the client. Google can render JS (delayed), but GPTBot/ClaudeBot/PerplexityBot "
            "and most AI crawlers do not run JS: marketing pages will look empty to them. Prerender/SSG the public pages "
            "or split marketing site onto an SSR/SSG framework.")
    if "inertia" in render:
        add("high", "inertia_csr", "Inertia without SSR renders public pages client-side. Enable Inertia SSR or serve marketing pages as Blade.")
    nx = info.get("next") or {}
    if nx.get("router") == "app":
        sp = nx["special_files"]
        if not nx["root_metadataBase"]:
            add("high", "no_metadataBase", "Root layout has no metadataBase: relative OG/canonical URLs will resolve wrongly (falls back to localhost or the deploy URL)")
        if not nx["root_title_template"]:
            add("low", "no_title_template", "No title.template in root metadata (e.g. '%s | Brand')")
        if not nx["root_html_lang"]:
            add("medium", "no_html_lang", "Root layout <html> has no lang")
        if not sp.get("sitemap") and "sitemap.xml" not in assets and not any("sitemap" in d for d in info["detect"]["seo_related_deps"]):
            add("high", "no_sitemap", "No app/sitemap.ts, public/sitemap.xml or sitemap package")
        if not sp.get("robots") and "robots.txt" not in assets:
            add("medium", "no_robots", "No app/robots.ts or public/robots.txt")
        if not sp.get("opengraph-image") and not any(a.startswith("og") for a in assets):
            add("medium", "no_og_image", "No opengraph-image file convention or default OG image")
        public_routes = [r for r in nx["routes"] if not r["app_area"]]
        bare = [r["route"] for r in public_routes if not r["own_metadata"] and not r["inherits_from_layout"]]
        only_inherited = [r["route"] for r in public_routes if not r["own_metadata"] and r["inherits_from_layout"]]
        if bare:
            add("high", "routes_without_metadata", f"{len(bare)} public routes have no metadata at all: {', '.join(bare[:12])}")
        if len(only_inherited) > 1:
            add("medium", "routes_inherit_only", f"{len(only_inherited)} public routes only inherit layout metadata (duplicate titles/descriptions likely): {', '.join(only_inherited[:12])}")
        dyn = [r["route"] for r in public_routes if r["dynamic"] and not r["own_metadata"]]
        if dyn:
            add("high", "dynamic_routes_no_generateMetadata", f"Dynamic public routes without generateMetadata: {', '.join(dyn[:10])}")
        if not any(r["jsonld"] for r in nx["routes"]) and not info["jsonld_anywhere"]:
            add("medium", "no_jsonld", "No JSON-LD found anywhere in the codebase (Organization/WebSite/SoftwareApplication missing)")
    elif nx.get("router") == "pages":
        bare = [r["route"] for r in nx["routes"] if not r["own_metadata"] and not r["app_area"]]
        if bare:
            add("medium", "pages_without_head", f"{len(bare)} pages without next/head or SEO component: {', '.join(bare[:12])}")
    else:
        if "robots.txt" not in assets:
            add("medium", "no_robots", "No robots.txt in public/static")
        if "sitemap.xml" not in assets and not any("sitemap" in d for d in info["detect"]["seo_related_deps"]):
            add("high", "no_sitemap", "No sitemap file or sitemap integration detected")
        if not info["jsonld_anywhere"]:
            add("medium", "no_jsonld", "No JSON-LD found in the codebase")
    if "llms.txt" not in assets and not (nx.get("llms_txt_route")):
        add("low", "no_llms_txt", "No llms.txt (low-cost, unproven for ranking; useful for agents/IDE assistants reading docs)")
    idx = assets.get("index.html")
    if isinstance(idx, dict) and fw in ("vite-spa", "create-react-app", "unknown"):
        if not idx["title"] or not idx["description"]:
            add("high", "spa_index_meta", "index.html lacks a real <title>/description: every route shares one head")
    for h in info["grep"]:
        out.append(h)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = Path(a.path).resolve()
    files = list(walk(root))
    info = {"root": str(root), "scanned_at": datetime.now().isoformat(timespec="seconds"), "files_scanned": len(files),
            "detect": detect(root), "static_assets": static_assets(root)}
    if info["detect"]["framework"] == "nextjs":
        info["next"] = scan_next_app_router(root, files) or scan_next_pages_router(root, files)
    info["jsonld_anywhere"] = any("ld+json" in read(f, 200_000) for f in files if f.suffix in CODE_EXT)
    info["grep"] = grep(root, files)
    info["findings"] = evaluate(info)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    info["findings"].sort(key=lambda x: order[x["severity"]])

    d = info["detect"]
    L = [f"# Project SEO/GEO scan: {d.get('package_name') or root.name}", "",
         f"- Framework: **{d['framework']}** | rendering: **{d['render']}** | files scanned: {len(files)}",
         f"- SEO-related deps: {', '.join(d['seo_related_deps']) or 'none'}",
         f"- Static SEO files: {', '.join(k for k in info['static_assets'] if k != 'index.html') or 'none'}"]
    nx = info.get("next") or {}
    if nx.get("router") == "app":
        sp = nx["special_files"]
        L.append(f"- Next.js App Router ({nx['app_dir']}): metadataBase={'yes' if nx['root_metadataBase'] else 'NO'}, "
                 f"title.template={'yes' if nx['root_title_template'] else 'no'}, special files: "
                 + ", ".join(k for k, v in sp.items() if v))
    L += ["", "## Findings", "", "| Sev | Code | Where | Detail |", "|---|---|---|---|"]
    for f in info["findings"]:
        where = f"{f['file']}:{f['line']}" if f.get("file") else "project"
        L.append(f"| {f['severity']} | `{f['code']}` | {where} | {f['message'].replace('|', '/')[:200]} |")
    if nx.get("routes"):
        L += ["", "## Routes", "", "| Route | Own meta | Inherits | Dynamic | JSON-LD | Area |", "|---|---|---|---|---|---|"]
        for r in nx["routes"][:200]:
            L.append(f"| {r['route']} | {'Y' if r['own_metadata'] else '-'} | {r['inherits_from_layout'] or '-'} | "
                     f"{'Y' if r['dynamic'] else '-'} | {'Y' if r['jsonld'] else '-'} | {'app' if r['app_area'] else 'public'} |")
    md = "\n".join(L) + "\n"
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out / f"project-scan-{stamp}.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
        (out / f"project-scan-{stamp}.md").write_text(md, encoding="utf-8")
    print(json.dumps(info, indent=2, default=str) if a.json else md)


if __name__ == "__main__":
    main()
