#!/usr/bin/env python3
"""extensions.py - list super-seo extensions and whether each one is active on this machine.

  python extensions.py            # table
  python extensions.py --json     # machine-readable (Claude reads this to decide which extensions to use)

An extension is active when any env var in requires.env_any is set, or any binary in requires.binary_any is on PATH,
or requires is empty. requires.mcp_prefix marks extensions that can also run through a connected MCP server; scripts
cannot see MCP servers, so Claude checks its own tool list for that prefix.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "extensions"


def load() -> list[dict]:
    out = []
    for manifest in sorted(ROOT.glob("*/extension.json")):
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            out.append({"name": manifest.parent.name, "error": f"invalid extension.json: {e}"})
            continue
        req = m.get("requires") or {}
        env_hit = [v for v in req.get("env_any", []) if os.environ.get(v)]
        bin_hit = [b for b in req.get("binary_any", []) if shutil.which(b)]
        always = not req.get("env_any") and not req.get("binary_any") and not req.get("mcp_prefix")
        active = always or bool(env_hit) or bool(bin_hit)
        m["status"] = "active" if active else ("mcp-only" if req.get("mcp_prefix") else "inactive")
        m["active_via"] = env_hit + bin_hit + (["built-in"] if always else [])
        m["path"] = manifest.parent.as_posix()
        out.append(m)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    exts = load()
    if a.json:
        print(json.dumps(exts, indent=2))
        return
    print("| Extension | Status | Enabled by | How to enable |\n|---|---|---|---|")
    for e in exts:
        if "error" in e:
            print(f"| {e['name']} | error | - | {e['error']} |")
            continue
        mcp = (e.get("requires") or {}).get("mcp_prefix")
        hint = e.get("setup", "") if e["status"] != "active" else "-"
        if mcp and e["status"] != "active":
            hint += f" (or connect an MCP server with tools prefixed `{mcp}`)"
        print(f"| {e.get('title', e['name'])} | {e['status']} | {', '.join(e['active_via']) or '-'} | {hint} |")


if __name__ == "__main__":
    main()
