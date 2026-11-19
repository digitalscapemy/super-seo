#!/usr/bin/env python3
"""robots_gen.py - generate a policy-driven robots.txt (or Next.js app/robots.ts) and verify it (stdlib only).

RFC 9309: a crawler that matches a named group ignores the '*' group completely, so every named group this
script writes repeats ALL private Disallow lines (and the Content-Signal line, when given).

Usage:
  python scripts/robots_gen.py --domain example.com
  python scripts/robots_gen.py --domain example.com --training block --content-signal "search=yes, ai-input=yes, ai-train=no"
  python scripts/robots_gen.py --domain example.com --private /api/ /app/ /admin/ --format nextjs
  python scripts/robots_gen.py --domain staging.example.com --staging
  python scripts/robots_gen.py --domain example.com --training block --check https://example.com/robots.txt

Policy flags:
  --private PATH ...      paths every crawler must stay out of (default: /api/ /app/ /dashboard/ /account/
                          /settings/ /billing/ /login /signup /auth/)
  --training allow|block  AI training crawlers (GPTBot, ClaudeBot, Google-Extended, ...): owner's business decision
  --search-bots allow     search engines + AI search / user-triggered agents are always allowed ...
  --block-search          ... unless this is passed explicitly (Disallow: / for them too)
  --content-signal STR    "search=yes, ai-input=yes, ai-train=no" (keys search / ai-input / ai-train, values yes / no)
  --staging               Disallow: / for everyone (preview / staging hosts; also send X-Robots-Tag: noindex)
  --format txt|nextjs     robots.txt text (default) or an app/robots.ts using MetadataRoute.Robots
  --out FILE              write the output to FILE instead of only printing it
  --check FILE_OR_URL     compare an existing robots.txt against the policy and list violations (exit 1 if any)

The generated file is parsed back with robots_rfc.Robots and every bot is checked against the policy; the
verification table is printed (stderr in txt/nextjs mode so stdout stays a clean file, or inside --json).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from robots_rfc import Robots  # noqa: E402

DEFAULT_PRIVATE = ["/api/", "/app/", "/dashboard/", "/account/", "/settings/", "/billing/", "/login", "/signup", "/auth/"]
# Classic search engines: covered by the '*' group (they have no named group, so '*' applies).
SEARCH_ENGINES = ["Googlebot", "Bingbot", "Applebot", "DuckDuckBot", "YandexBot"]
# AI search / answer crawlers and user-triggered fetchers: needed to be cited or read on request.
AI_RETRIEVAL = ["OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot", "Perplexity-User",
                "DuckAssistBot", "MistralAI-User", "meta-externalfetcher", "Amazonbot"]
# Training-data crawlers / opt-out tokens.
AI_TRAINING = ["GPTBot", "ClaudeBot", "anthropic-ai", "Google-Extended", "Applebot-Extended", "meta-externalagent",
               "CCBot", "Bytespider", "cohere-ai"]
SIGNAL_KEYS = ("search", "ai-input", "ai-train")


class PolicyError(ValueError):
    pass


def parse_content_signal(value: str | None) -> str | None:
    """Validate and normalise a Content-Signal value. Raises PolicyError on bad keys/values."""
    if value is None:
        return None
    pairs, seen = [], set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise PolicyError(f"Content-Signal part '{part}' is not key=value")
        k, v = [x.strip().lower() for x in part.split("=", 1)]
        if k not in SIGNAL_KEYS:
            raise PolicyError(f"Content-Signal key '{k}' is unknown (use {', '.join(SIGNAL_KEYS)})")
        if v not in ("yes", "no"):
            raise PolicyError(f"Content-Signal value for '{k}' must be yes or no, got '{v}'")
        if k in seen:
            raise PolicyError(f"Content-Signal key '{k}' given twice")
        seen.add(k)
        pairs.append((k, v))
    if not pairs:
        raise PolicyError("Content-Signal is empty")
    order = {k: i for i, k in enumerate(SIGNAL_KEYS)}
    return ", ".join(f"{k}={v}" for k, v in sorted(pairs, key=lambda kv: order[kv[0]]))


def normalize_domain(domain: str) -> str:
    d = domain.strip()
    if "://" in d:
        d = urlparse(d).netloc
    return d.strip("/").lower()


def normalize_private(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        p = p.strip()
        if not p:
            continue
        if re.match(r"^[A-Za-z]:[\\/]", p):
            raise PolicyError(f"private path '{p}' looks like a Windows path: Git Bash rewrites /admin/ to C:/...; "
                              "re-run with MSYS_NO_PATHCONV=1 set, or from PowerShell/cmd")
        p = re.sub(r"^/{2,}", "/", p)
        if not p.startswith("/") and not p.startswith("*"):
            p = "/" + p
        if p == "/":
            raise PolicyError("'/' is not a private path; use --staging to block the whole site")
        if p not in out:
            out.append(p)
    return out


def build_policy(domain: str, private: list[str] | None = None, training: str = "allow", block_search: bool = False,
                 content_signal: str | None = None, staging: bool = False, sitemap: str | None = "") -> dict:
    dom = normalize_domain(domain)
    if not dom:
        raise PolicyError("--domain is required")
    if training not in ("allow", "block"):
        raise PolicyError("--training must be allow or block")
    sig = parse_content_signal(content_signal)
    if sitemap == "":
        sitemap = f"https://{dom}/sitemap.xml"
    return {"domain": dom, "private": normalize_private(private if private is not None else DEFAULT_PRIVATE),
            "training": training, "block_search": block_search, "content_signal": sig, "staging": staging,
            "sitemap": sitemap}


def policy_warnings(policy: dict) -> list[dict]:
    w = []
    sig = policy["content_signal"] or ""
    if "ai-train=yes" in sig and policy["training"] == "block":
        w.append({"severity": "medium", "code": "signal_conflict", "message": "Content-Signal says ai-train=yes but training crawlers are blocked"})
    if "ai-train=no" in sig and policy["training"] == "allow":
        w.append({"severity": "low", "code": "signal_soft_optout", "message": "ai-train=no is a preference only; training crawlers are still allowed by robots rules (use --training block to disallow them)"})
    if "search=no" in sig and not policy["block_search"]:
        w.append({"severity": "medium", "code": "signal_conflict", "message": "Content-Signal search=no while search bots are allowed"})
    if policy["block_search"] and not policy["staging"]:
        w.append({"severity": "high", "code": "search_blocked", "message": "--block-search removes the site from search engines AND AI search citations"})
    return w


# --------------------------------------------------------------------------- render
def _group(agents: list[str], allow_all: bool, private: list[str], signal: str | None) -> list[str]:
    L = [f"User-agent: {a}" for a in agents]
    if allow_all:
        L.append("Allow: /")
        L += [f"Disallow: {p}" for p in private]
    else:
        L.append("Disallow: /")
    if signal:
        L.append(f"Content-Signal: {signal}")
    return L


def render_txt(policy: dict) -> str:
    sig = policy["content_signal"]
    L = [f"# robots.txt for {policy['domain']} (generated by super-seo robots_gen.py)",
         "# RFC 9309: a bot matching a named group ignores '*', so each group repeats the private paths.",
         "# robots.txt is a crawl preference, not access control: protect private routes with auth.", ""]
    if policy["staging"]:
        L += ["# STAGING / PREVIEW: nothing may be crawled. Also send 'X-Robots-Tag: noindex' on every response.",
              "User-agent: *", "Disallow: /"]
        return "\n".join(L) + "\n"
    search_ok = not policy["block_search"]
    L += ["# Search engines and every crawler without a named group below"]
    L += _group(["*"], search_ok, policy["private"], sig)
    L += ["", "# AI search + user-triggered agents (needed to be cited / read on request)"]
    L += _group(AI_RETRIEVAL, search_ok, policy["private"], sig)
    L += ["", f"# AI training crawlers (owner's policy: {policy['training']})"]
    L += _group(AI_TRAINING, policy["training"] == "allow", policy["private"], sig)
    if sig:
        L += ["", "# Content-Signal is a stated preference (not enforced; no Google support)."]
    if policy["sitemap"]:
        L += ["", f"Sitemap: {policy['sitemap']}"]
    return "\n".join(L) + "\n"


def _ts_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def render_nextjs(policy: dict) -> str:
    L = ["// app/robots.ts (generated by super-seo robots_gen.py). Every named group repeats PRIVATE (RFC 9309).",
         'import type { MetadataRoute } from "next";', ""]
    if policy["content_signal"]:
        L += ["// MetadataRoute.Robots cannot emit Content-Signal lines. If you need them, serve robots.txt from",
              "// app/robots.txt/route.ts (text/plain) with the txt output of robots_gen.py instead of this file.",
              f"// Intended Content-Signal: {policy['content_signal']}", ""]
    if policy["staging"]:
        L += ["export default function robots(): MetadataRoute.Robots {",
              "  // Staging / preview: block everything (also send X-Robots-Tag: noindex).",
              '  return { rules: [{ userAgent: "*", disallow: "/" }] };', "}"]
        return "\n".join(L) + "\n"
    allow = not policy["block_search"]
    L += [f"const PRIVATE = {_ts_list(policy['private'])};",
          f"const AI_RETRIEVAL = {_ts_list(AI_RETRIEVAL)};",
          f"const AI_TRAINING = {_ts_list(AI_TRAINING)};", "",
          "export default function robots(): MetadataRoute.Robots {", "  return {", "    rules: ["]

    def rule(agent_expr: str, ok: bool) -> str:
        return (f'      {{ userAgent: {agent_expr}, allow: "/", disallow: PRIVATE }},' if ok
                else f'      {{ userAgent: {agent_expr}, disallow: "/" }},')
    L.append(rule('"*"', allow))
    L.append(rule("AI_RETRIEVAL", allow))
    L.append(rule("AI_TRAINING", policy["training"] == "allow"))
    L.append("    ],")
    if policy["sitemap"]:
        L.append(f"    sitemap: {json.dumps(policy['sitemap'])},")
    L += ["  };", "}"]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- verify
def _probe_path(rule: str) -> str:
    p = rule.replace("*", "x").rstrip("$")
    return p if p.startswith("/") else "/" + p


def raw_groups(text: str) -> list[dict]:
    """Group-aware raw view (agents + lines) used for Content-Signal checks; mirrors robots_rfc grouping."""
    groups, cur, in_rules = [], None, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if cur is None or in_rules:
                cur = {"agents": [], "lines": []}
                groups.append(cur)
                in_rules = False
            cur["agents"].append(v.lower().split("/")[0].strip())
        elif k == "sitemap":
            continue
        elif cur is not None:
            in_rules = True
            cur["lines"].append((k, v))
    return groups


def expected(policy: dict) -> list[dict]:
    """List of (bot, kind, path, should_allow) expectations."""
    rows = []
    if policy["staging"]:
        for bot in SEARCH_ENGINES + AI_RETRIEVAL + AI_TRAINING:
            rows.append({"bot": bot, "kind": "staging", "path": "/", "allow": False})
        return rows
    search_ok = not policy["block_search"]
    for bot, kind in [(b, "search") for b in SEARCH_ENGINES] + [(b, "ai-retrieval") for b in AI_RETRIEVAL] + \
                     [(b, "training") for b in AI_TRAINING]:
        ok = search_ok if kind != "training" else policy["training"] == "allow"
        rows.append({"bot": bot, "kind": kind, "path": "/", "allow": ok})
        if ok:
            for p in policy["private"]:
                rows.append({"bot": bot, "kind": kind, "path": _probe_path(p), "allow": False})
    return rows


def verify(text: str, policy: dict) -> dict:
    rb = Robots(text)
    origin = f"https://{policy['domain']}"
    rows, violations = [], []
    failed: dict[tuple, list[str]] = {}
    for exp in expected(policy):
        got = rb.can_fetch(exp["bot"], origin + exp["path"])
        group, _ = rb.group_for(exp["bot"])
        row = {**exp, "got": got, "group": group or "-", "ok": got == exp["allow"]}
        rows.append(row)
        if not row["ok"]:
            failed.setdefault((exp["path"], exp["allow"], group or "-"), []).append(f"{exp['bot']} ({exp['kind']})")
    for (path, want, group), bots in failed.items():
        sev = "critical" if path == "/" and want else "high"
        what = "cannot fetch" if want else "can fetch"
        shown = ", ".join(bots[:6]) + (f" and {len(bots) - 6} more" if len(bots) > 6 else "")
        violations.append({"severity": sev, "code": "policy_violation", "bots": bots,
                           "message": f"{shown} {what} {path} (group '{group}'); policy wants {'allow' if want else 'disallow'}"})
    sig = policy["content_signal"]
    if sig and not policy["staging"]:
        for g in raw_groups(text):
            signals = [v for k, v in g["lines"] if k == "content-signal"]
            if not signals:
                violations.append({"severity": "medium", "code": "content_signal_missing",
                                   "message": f"group [{', '.join(g['agents'])}] has no Content-Signal line (each named group needs its own)"})
            elif parse_content_signal_safe(signals[0]) != sig:
                violations.append({"severity": "medium", "code": "content_signal_mismatch",
                                   "message": f"group [{', '.join(g['agents'])}] Content-Signal '{signals[0]}' != policy '{sig}'"})
    if policy.get("sitemap") and not policy["staging"] and policy["sitemap"] not in rb.sitemaps:
        violations.append({"severity": "low", "code": "sitemap_not_declared", "message": f"robots.txt does not declare Sitemap: {policy['sitemap']}"})
    if "# BEGIN Cloudflare Managed" in text:
        violations.append({"severity": "high", "code": "cloudflare_managed_block",
                           "message": "Cloudflare managed robots.txt content is prepended; it may disallow AI crawlers your policy allows"})
    return {"rows": rows, "violations": violations, "ok": not violations}


def parse_content_signal_safe(v: str) -> str | None:
    try:
        return parse_content_signal(v)
    except PolicyError:
        return None


def render_table(rows: list[dict]) -> str:
    """One line per bot: root access (expected/got) and how many private paths are blocked."""
    bots: dict[str, dict] = {}
    for r in rows:
        b = bots.setdefault(r["bot"], {"kind": r["kind"], "group": r["group"], "root": None, "priv": 0, "priv_ok": 0, "ok": True})
        if r["path"] == "/":
            b["root"] = (r["allow"], r["got"])
        else:
            b["priv"] += 1
            b["priv_ok"] += int(r["ok"])
        b["ok"] = b["ok"] and r["ok"]
    L = ["| Bot | Kind | Group | `/` expected | `/` got | Private paths blocked | OK |", "|---|---|---|---|---|---|---|"]
    for bot, b in bots.items():
        exp, got = b["root"] if b["root"] else (None, None)
        priv = f"{b['priv_ok']}/{b['priv']}" if b["priv"] else "-"
        L.append(f"| {bot} | {b['kind']} | `{b['group']}` | {'allow' if exp else 'disallow'} | {'allow' if got else 'disallow'} | "
                 f"{priv} | {'yes' if b['ok'] else '**NO**'} |")
    return "\n".join(L)


def load_existing(src: str) -> tuple[str, list[dict]]:
    problems = []
    if re.match(r"^https?://", src, re.I):
        import crawl_audit as ca
        ca.set_network_mode(urlparse(src).hostname)
        r = ca.fetch(src, ca.UA_DEFAULT, accept="text/plain,*/*")
        if r["status"] != 200:
            sev = "critical" if r["status"] >= 500 or r["status"] in (0, 429) else "medium"
            problems.append({"severity": sev, "code": "robots_unreachable",
                             "message": f"{src} returned {r['status'] or r['error']}" + ("; 5xx/429 makes Google treat the site as disallowed" if r["status"] >= 500 or r["status"] == 429 else "")})
            return "", problems
        if "html" in r["headers"].get("content-type", ""):
            problems.append({"severity": "high", "code": "robots_served_as_html", "message": f"{src} is served as HTML (SPA fallback?); crawlers will not parse it"})
        return r["body"][:500_000], problems
    return Path(src).read_text(encoding="utf-8", errors="replace"), problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", required=True, help="production host, e.g. example.com or www.example.com")
    ap.add_argument("--private", nargs="+", help="private path prefixes (default: " + " ".join(DEFAULT_PRIVATE) + ")")
    ap.add_argument("--training", choices=["allow", "block"], default="allow", help="AI training crawlers policy")
    ap.add_argument("--search-bots", choices=["allow"], default="allow", help="search + AI search bots (always allow)")
    ap.add_argument("--block-search", action="store_true", help="explicitly block search engines and AI search bots")
    ap.add_argument("--content-signal", help='e.g. "search=yes, ai-input=yes, ai-train=no"')
    ap.add_argument("--staging", action="store_true", help="Disallow: / for everyone")
    ap.add_argument("--sitemap", default="", help="Sitemap URL (default https://<domain>/sitemap.xml)")
    ap.add_argument("--no-sitemap", action="store_true", help="omit the Sitemap: line")
    ap.add_argument("--format", choices=["txt", "nextjs"], default="txt")
    ap.add_argument("--out", help="write the generated file here")
    ap.add_argument("--check", metavar="FILE_OR_URL", help="check an existing robots.txt against the policy")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        policy = build_policy(a.domain, a.private, a.training, a.block_search, a.content_signal, a.staging,
                              None if a.no_sitemap else a.sitemap)
    except PolicyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    warnings = policy_warnings(policy)

    if a.check:
        text, problems = load_existing(a.check)
        res = verify(text, policy) if text else {"rows": [], "violations": [], "ok": False}
        res["violations"] = problems + res["violations"]
        res["ok"] = not res["violations"]
        if a.json:
            print(json.dumps({"tool": "robots_gen", "mode": "check", "source": a.check, "policy": policy,
                              "findings": res["violations"], "rows": res["rows"], "ok": res["ok"]}, indent=2))
        else:
            print(f"# robots.txt check: {a.check}\n")
            print(f"Policy: training={policy['training']}, search={'block' if policy['block_search'] else 'allow'}, "
                  f"staging={policy['staging']}, private={' '.join(policy['private'])}\n")
            if res["violations"]:
                print("## Violations\n")
                for v in res["violations"]:
                    print(f"- **{v['severity']}** `{v['code']}`: {v['message']}")
            else:
                print("No violations: the existing file matches the policy.")
            if res["rows"]:
                print("\n## Verification\n\n" + render_table(res["rows"]))
        return 0 if res["ok"] else 1

    txt = render_txt(policy)
    output = txt if a.format == "txt" else render_nextjs(policy)
    res = verify(txt, policy)  # the nextjs file encodes the same rule set as the txt rendering
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(output, encoding="utf-8", newline="\n")
    if a.json:
        print(json.dumps({"tool": "robots_gen", "mode": "generate", "format": a.format, "policy": policy,
                          "output": output, "verified": res["ok"], "rows": res["rows"],
                          "findings": warnings + res["violations"], "written": a.out}, indent=2))
    else:
        print(output, end="")
        print(f"\n## Verification ({'all checks pass' if res['ok'] else 'FAILED'})\n\n" + render_table(res["rows"]), file=sys.stderr)
        for w in warnings + res["violations"]:
            print(f"- **{w['severity']}** `{w['code']}`: {w['message']}", file=sys.stderr)
        if a.out:
            print(f"[saved] {a.out}", file=sys.stderr)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
