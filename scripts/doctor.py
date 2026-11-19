#!/usr/bin/env python3
"""doctor.py - check that super-seo is installed and ready on this machine.

  python doctor.py            # human-readable checklist
  python doctor.py --online   # also test outbound HTTPS
  python doctor.py --json

Checks: Python version, scripts compile, extensions and what enables them, headless browser, Node/npx (Lighthouse),
Claude Code config folder, installed specialist agents, the optional JSON-LD hook, and write access to .seo/.
Nothing is changed. Exit code 1 when a required check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
AGENTS = ["super-seo-code", "super-seo-technical", "super-seo-geo", "super-seo-content", "super-seo-schema",
          "super-seo-performance", "super-seo-competitors", "super-seo-fixer"]


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def check(results, name, status, detail, fix=""):
    results.append({"check": name, "status": status, "detail": detail, "fix": fix})


def run(online: bool) -> list[dict]:
    r: list[dict] = []
    v = sys.version_info
    check(r, "Python", "ok" if v >= (3, 10) else "fail", f"{v.major}.{v.minor}.{v.micro}",
          "" if v >= (3, 10) else "Install Python 3.10 or newer")

    bad = []
    files = sorted(list((ROOT / "scripts").glob("*.py")) + list((ROOT / "extensions").glob("*/*.py"))
                   + list((ROOT / "hooks").glob("*.py")))
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            try:
                py_compile.compile(str(f), cfile=str(Path(tmp) / (f.stem + ".pyc")), doraise=True)
            except py_compile.PyCompileError as e:
                bad.append(f"{f.relative_to(ROOT).as_posix()}: {str(e).splitlines()[-1][:120]}")
    check(r, "Scripts compile", "ok" if not bad else "fail", f"{len(files)} files" + (f"; {len(bad)} broken" if bad else ""),
          "; ".join(bad[:3]))

    try:
        import extensions as ext_mod
        exts = ext_mod.load()
        active = [e["name"] for e in exts if e.get("status") == "active"]
        inactive = [e["name"] for e in exts if e.get("status") != "active"]
        check(r, "Extensions", "ok", f"{len(active)} active ({', '.join(active) or 'none'}); {len(inactive)} available to enable",
              "python scripts/extensions.py shows how to enable each one")
    except Exception as e:  # noqa: BLE001 - report, never crash the doctor
        check(r, "Extensions", "warn", f"could not read extensions: {e}")

    try:
        sys.path.insert(0, str(ROOT / "extensions" / "browser"))
        import render_check
        b = render_check.find_browser()
        check(r, "Headless browser", "ok" if b else "warn", b or "no Chrome/Edge/Chromium found",
              "" if b else "Install Chrome, Edge or Chromium, or set SUPER_SEO_CHROME to its path (enables render/screenshot)")
    except Exception as e:  # noqa: BLE001
        check(r, "Headless browser", "warn", f"browser extension not loadable: {e}")

    npx = shutil.which("npx")
    check(r, "Node.js / npx", "ok" if npx else "warn", npx or "not found",
          "" if npx else "Install Node.js 20+ to enable the Lighthouse extension")

    cfg = config_dir()
    skill = cfg / "skills" / "super-seo" / "SKILL.md"
    installed = skill.exists() or (ROOT / "SKILL.md").resolve().parent == (cfg / "skills" / "super-seo").resolve()
    check(r, "Skill installed", "ok" if installed else "warn", str(skill.parent),
          "" if installed else "Run install.ps1 (Windows) or ./install.sh (macOS/Linux)")

    missing = [a for a in AGENTS if not (cfg / "agents" / f"{a}.md").exists()]
    check(r, "Specialist agents", "ok" if not missing else "warn",
          f"{len(AGENTS) - len(missing)}/{len(AGENTS)} installed in {cfg / 'agents'}",
          "" if not missing else "Re-run the installer to copy agents/ (/super-seo falls back to general agents meanwhile)")

    hook_on = False
    settings = cfg / "settings.json"
    if settings.exists():
        try:
            hook_on = "validate_jsonld.py" in settings.read_text(encoding="utf-8")
        except OSError:
            pass
    check(r, "JSON-LD hook", "ok" if hook_on else "info", "enabled" if hook_on else "not enabled (optional)",
          "" if hook_on else "install.ps1 -EnableHook / ./install.sh --enable-hook")

    try:
        probe = Path.cwd() / ".seo"
        probe.mkdir(exist_ok=True)
        t = probe / ".doctor-write-test"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
        check(r, "Write .seo/", "ok", str(probe))
    except OSError as e:
        check(r, "Write .seo/", "fail", str(e), "Run from a folder you can write to (the project root)")

    if online:
        from crawl_audit import fetch, set_network_mode
        set_network_mode("www.google.com")
        res = fetch("https://www.google.com/robots.txt", "super-seo-doctor/1.0", timeout=15)
        ok = res["status"] == 200
        check(r, "Outbound HTTPS", "ok" if ok else "fail", f"status {res['status']} {res.get('error') or ''}".strip(),
              "" if ok else "Check proxy/firewall; crawling live sites needs outbound HTTPS")
    for var in ("PSI_API_KEY", "SUPER_SEO_GOOGLE_SA", "BING_WEBMASTER_API_KEY"):
        if os.environ.get(var):
            check(r, f"Env {var}", "ok", "set (value hidden)")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    res = run(a.online)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        icon = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]", "info": "[info]"}
        print("super-seo doctor\n")
        for c in res:
            print(f"{icon[c['status']]} {c['check']}: {c['detail']}")
            if c["fix"] and c["status"] != "ok":
                print(f"        → {c['fix']}")
    sys.exit(1 if any(c["status"] == "fail" for c in res) else 0)


if __name__ == "__main__":
    main()
