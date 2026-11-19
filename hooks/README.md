# Hooks

## `validate_jsonld.py`: JSON-LD check after every edit

A Claude Code `PostToolUse` hook for `Edit|Write|MultiEdit`. After Claude changes a file it reads the file and checks
the structured data in it, so broken or placeholder JSON-LD is caught in the same turn, not in a later audit.
Python 3.10+ standard library only, no network, typically well under 100 ms (under 1 s for a 1 MB file).

**File types:** `.html .htm .jsx .tsx .ts .js .mjs .vue .svelte .astro .php .blade.php .mdx .md .json .liquid`.
Anything else, and any unreadable, binary or > 5 MB file, is skipped silently.

**What it inspects**
- `<script type="application/ld+json">…</script>` blocks with literal JSON: parsed with `json.loads`.
- Template expressions inside those blocks (`{JSON.stringify(…)}`, `{jsonLd}`, `{{ … }}`, `{% … %}`, `{@html …}`,
  `<?php`, `${…}`, `@json(…)`): not parsed, only text checks.
- JS/TS object literals that contain `"@context"` / `"@type"` keys (e.g. `const jsonLd = { "@context": … }`):
  not parsed as JS, only text checks. Comments inside them are ignored.
- `.json` files that contain JSON-LD: parsed.
- In `.md` / `.mdx`, fenced code blocks are ignored (documentation examples are not shipped markup).

**Checks**

| Check | Result |
|---|---|
| Literal JSON-LD block does not parse | blocking |
| Placeholder values: `[REPLACE`, `[INSERT`, `[Your `, `XXX`, `lorem ipsum` | blocking |
| `example.com` in `url`, `logo`, `sameAs` or `@id` | blocking |
| Parsed JSON-LD with top-level `@type` but no `@context` | blocking |
| JS object assigned to a JSON-LD-like variable (`jsonLd`, `schema`, `structuredData`…) with `@type` but no `@context` | warning |
| Deprecated types: HowTo, SpecialAnnouncement, CourseInfo, EstimatedSalary, LearningVideo, VehicleListing, ClaimReview | warning |
| Hardcoded `aggregateRating.ratingValue` literal in a template/component file | warning (must come from real reviews) |
| `"@context": "http://schema.org"` | warning (use `https://`) |

## Output contract

This follows the documented Claude Code hook semantics for `PostToolUse` (the tool has already run, so nothing is undone):

- **Blocking problems** → exit code **2**, message on **stderr**. Claude Code feeds stderr back to Claude, which then
  fixes the file.
- **Warnings only** → exit code **0** and one JSON object on **stdout**:
  `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "…"}, "systemMessage": "…"}`.
  `additionalContext` puts the warnings in front of Claude without blocking; `systemMessage` shows them to you.
  (Plain stderr with exit 0 is not shown to Claude, so it is not used.)
- **Nothing found / not applicable / internal error** → exit 0, no output. A bug in the validator never interrupts editing.

Manual run (same checks, warnings go to stderr): `python hooks/validate_jsonld.py path/to/page.tsx`

## Enable

Easiest: re-run the installer with the hook switch.

```powershell
.\install.ps1 -EnableHook        # Windows
```
```bash
./install.sh --enable-hook       # macOS / Linux
```

The installer backs up `settings.json` to `settings.json.bak-<timestamp>`, adds the entry only if the same command is
not already there, and leaves every other setting untouched.

Manual setup: add this to `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`), using the absolute path of
the installed skill. Use forward slashes on Windows too, and `python3` instead of `python` where that is the name.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          { "type": "command", "command": "python \"C:/Users/you/.claude/skills/super-seo/hooks/validate_jsonld.py\"" }
        ]
      }
    ]
  }
}
```

If `PostToolUse` already exists, add the object to its array instead of replacing it. Restart Claude Code (or open
`/hooks`) for the change to take effect.

## Disable

- Run `.\install.ps1 -DisableHook` or `./install.sh --disable-hook`: removes only this hook's entry (backup first).
- Or delete the entry from `settings.json` by hand.
- To pause it without editing settings, start Claude Code with the environment variable `SUPER_SEO_JSONLD_HOOK=off`.
