#!/usr/bin/env bash
# install.sh - copy this skill into the Claude Code skills folder (re-run after editing the source).
#   ./install.sh                  install / update the skill (+ extensions, hooks, agents)
#   ./install.sh --enable-hook    also register the JSON-LD PostToolUse hook in settings.json
#   ./install.sh --disable-hook   remove only that hook entry from settings.json
# Honours $CLAUDE_CONFIG_DIR (default: ~/.claude). Needs python3 (stdlib only) for the hook switches.
set -euo pipefail

ACTION=""
for arg in "$@"; do
  case "$arg" in
    --enable-hook) [ -z "$ACTION" ] || { echo "Use either --enable-hook or --disable-hook" >&2; exit 1; }; ACTION="enable" ;;
    --disable-hook) [ -z "$ACTION" ] || { echo "Use either --enable-hook or --disable-hook" >&2; exit 1; }; ACTION="disable" ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DEST="$CONFIG_DIR/skills/super-seo"

mkdir -p "$CONFIG_DIR"
DEST_REAL=""
if [ -d "$DEST" ]; then DEST_REAL="$(cd "$DEST" && pwd -P)"; fi

# ---------------------------------------------------------------- skill files
if [ "$DEST_REAL" = "$SRC" ]; then
  echo "Already installed in place ($DEST). Update with: git pull"
else
  mkdir -p "$DEST"
  for item in SKILL.md README.md references scripts templates extensions hooks; do
    [ -e "$SRC/$item" ] || continue
    rm -rf "${DEST:?}/$item"
    cp -R "$SRC/$item" "$DEST/$item"
  done
  find "$DEST" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
  echo "Installed super-seo -> $DEST"
fi

# ---------------------------------------------------------------- agents (only super-seo-*.md are touched)
if [ -d "$SRC/agents" ]; then
  count=0
  for f in "$SRC"/agents/super-seo-*.md; do
    [ -f "$f" ] || continue
    mkdir -p "$CONFIG_DIR/agents"
    cp -f "$f" "$CONFIG_DIR/agents/$(basename "$f")"
    count=$((count + 1))
  done
  [ "$count" -eq 0 ] || echo "Installed $count agent(s) -> $CONFIG_DIR/agents"
fi

# ---------------------------------------------------------------- hook registration
[ -n "$ACTION" ] || exit 0

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || { echo "python3 is required for --$ACTION-hook" >&2; exit 1; }

HOOK_FILE="$DEST/hooks/validate_jsonld.py"
if [ "$ACTION" = "enable" ] && [ ! -f "$HOOK_FILE" ]; then
  echo "Hook script not found at $HOOK_FILE; install the skill first." >&2; exit 1
fi

"$PY" - "$ACTION" "$CONFIG_DIR/settings.json" "$PY \"$HOOK_FILE\"" <<'PYEOF'
import json, os, shutil, sys, time

action, path, command = sys.argv[1], sys.argv[2], sys.argv[3]
MATCHER = "Edit|Write|MultiEdit"


def ours(cmd):
    return isinstance(cmd, str) and "super-seo" in cmd and "validate_jsonld.py" in cmd


settings = {}
if os.path.exists(path):
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()
    if raw.strip():
        try:
            settings = json.loads(raw)
        except ValueError as e:
            sys.exit(f"settings.json is not valid JSON; fix it first (nothing was changed): {e}")
if not isinstance(settings, dict):
    sys.exit("settings.json does not contain a JSON object; nothing was changed")


def save():
    if os.path.exists(path):
        backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        print(f"Backed up settings.json -> {backup}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


hooks = settings.get("hooks")
if action == "enable":
    if not isinstance(hooks, dict):
        hooks = settings["hooks"] = {}
    groups = hooks.get("PostToolUse")
    if not isinstance(groups, list):
        groups = hooks["PostToolUse"] = []
    if any(isinstance(g, dict) and any(isinstance(h, dict) and h.get("command") == command for h in g.get("hooks") or [])
           for g in groups):
        print(f"JSON-LD hook already enabled in {path}")
        sys.exit(0)
    groups.append({"matcher": MATCHER, "hooks": [{"type": "command", "command": command}]})
    save()
    print(f"Enabled JSON-LD hook in {path}\n  command: {command}")
else:
    removed = 0
    if isinstance(hooks, dict) and isinstance(hooks.get("PostToolUse"), list):
        kept = []
        for g in hooks["PostToolUse"]:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                kept.append(g)
                continue
            inner = [h for h in g["hooks"] if not (isinstance(h, dict) and ours(h.get("command")))]
            removed += len(g["hooks"]) - len(inner)
            if inner:
                g["hooks"] = inner
                kept.append(g)
        if kept:
            hooks["PostToolUse"] = kept
        else:
            del hooks["PostToolUse"]
            if not hooks:
                del settings["hooks"]
    if removed:
        save()
        print(f"Removed {removed} super-seo JSON-LD hook entr{'y' if removed == 1 else 'ies'} from {path}")
    else:
        print(f"No super-seo JSON-LD hook found in {path} (nothing changed)")
PYEOF
