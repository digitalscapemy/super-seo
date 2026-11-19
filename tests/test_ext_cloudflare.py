"""Offline tests for extensions/cloudflare/cloudflare.py (HTTP and robots.txt fetch mocked)."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions" / "cloudflare"))
import cloudflare as cf  # noqa: E402

ZID = "0123456789abcdef0123456789abcdef"
ROBOTS = """User-agent: *
Allow: /
# BEGIN Cloudflare Managed content
User-agent: *
Content-Signal: search=yes,ai-train=no
Allow: /

User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /

User-agent: PerplexityBot
Disallow: /
# END Cloudflare Managed Content
"""


def gql_rows():
    def row(n, ua, status, host="example.com"):
        return {"count": n, "dimensions": {"userAgent": ua, "clientRequestHTTPHost": host, "edgeResponseStatus": status}}
    return [row(500, "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", 200),
            row(60, "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +https://www.anthropic.com)", 403),
            row(40, "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +https://www.anthropic.com)", 200),
            row(90, "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot", 403),
            row(10, "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot", 200),
            row(5, "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", 200)]


class CloudflareTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.gql_error = False
        p = mock.patch.object(cf, "http_json", side_effect=self.fake)
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "cf_secret_tok", "CLOUDFLARE_ZONE_ID": ""})
        e.start()
        self.addCleanup(e.stop)
        f = mock.patch.object(cf, "fetch", return_value={"status": 200, "body": ROBOTS, "error": None, "headers": {}})
        self.fetch = f.start()
        self.addCleanup(f.stop)
        s = mock.patch.object(cf, "set_network_mode", return_value=True)
        s.start()
        self.addCleanup(s.stop)

    def fake(self, method, url, token, body=None, **kw):
        self.calls.append((method, url, token, body))
        self.assertEqual(token, "cf_secret_tok")
        if url.startswith(cf.API + "/zones?"):
            name = url.split("name=")[1]
            if name == "example.com":
                return {"success": True, "result": [{"id": ZID, "name": "example.com", "plan": {"name": "Free Website"}, "status": "active"}]}
            return {"success": True, "result": []}
        if url == cf.GRAPHQL:
            if self.gql_error:
                return {"data": None, "errors": [{"message": "unknown field \"edgeResponseStatus\"", "path": ["viewer"]}]}
            if "firewallEventsAdaptiveGroups" in body["query"]:
                return {"data": {"viewer": {"zones": [{"firewallEventsAdaptiveGroups": [
                    {"count": 60, "dimensions": {"action": "managed_challenge", "source": "botFight",
                                                 "userAgent": "compatible; Claude-SearchBot/1.0"}}]}]}}}
            return {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": gql_rows()}]}}}
        if url.endswith("/bot_management"):
            return {"success": True, "result": {"fight_mode": True, "ai_bots_protection": "block", "is_robots_txt_managed": True}}
        if url.endswith("/settings"):
            return {"success": True, "result": [{"id": "security_level", "value": "medium"}, {"id": "ssl", "value": "full"},
                                                {"id": "irrelevant", "value": 1}]}
        raise AssertionError(url)

    def test_zone_lookup_walks_parents(self):
        z = cf.find_zone("cf_secret_tok", "https://www.app.example.com/x")
        self.assertEqual(z["id"], ZID)
        names = [c[1].split("name=")[1] for c in self.calls]
        self.assertEqual(names, ["www.app.example.com", "app.example.com", "example.com"])

    def test_bot_for_longest_token(self):
        self.assertEqual(cf.bot_for("x Claude-SearchBot/1.0")[0], "Claude-SearchBot")
        self.assertEqual(cf.bot_for("x ClaudeBot/1.0")[0], "ClaudeBot")
        self.assertIsNone(cf.bot_for("Mozilla/5.0 Chrome/131"))

    def test_bots_report(self):
        res = cf.cmd_bots("cf_secret_tok", {"id": ZID, "name": "example.com"}, 1)
        q = self.calls[0][3]
        self.assertEqual(q["variables"], {"zoneTag": ZID})
        self.assertIn("httpRequestsAdaptiveGroups", q["query"])
        self.assertIn('userAgent_like: "%OAI-SearchBot%"', q["query"])
        bots = {b["bot"]: b for b in res["bots"]}
        self.assertEqual(bots["Claude-SearchBot"]["requests"], 100)
        self.assertEqual(bots["Claude-SearchBot"]["blocked_share_pct"], 60.0)
        self.assertEqual(bots["Googlebot"]["ok_share_pct"], 100.0)
        codes = {f["code"]: f["severity"] for f in res["findings"]}
        self.assertEqual(codes["cf_blocks_claude_searchbot"], "high")
        self.assertEqual(codes["cf_blocks_training_gptbot"], "info")
        self.assertIn("cf_firewall_managed_challenge", codes)
        self.assertEqual(res["firewall"][0]["bot"], "Claude-SearchBot")

    def test_bots_graphql_error_degrades(self):
        self.gql_error = True
        res = cf.cmd_bots("cf_secret_tok", {"id": ZID, "name": "example.com"}, 3)
        self.assertEqual(res["bots"], [])
        self.assertTrue(any("edgeResponseStatus" in e for e in res["graphql_errors"]))
        md = cf.to_markdown({**res, "command": "bots", "generated": "now"})
        self.assertIn("Raw GraphQL errors", md)

    def test_settings_and_managed_robots(self):
        res = cf.cmd_settings("cf_secret_tok", {"id": ZID, "name": "example.com"}, "example.com")
        self.fetch.assert_called_once()
        self.assertEqual(self.fetch.call_args[0][0], "https://example.com/robots.txt")
        self.assertEqual(res["settings"]["zone_settings"], {"security_level": "medium", "ssl": "full"})
        self.assertEqual(res["robots"]["disallowed_agents"], ["ClaudeBot", "GPTBot", "PerplexityBot"])
        self.assertEqual(res["robots"]["content_signals"], ["search=yes,ai-train=no"])
        codes = {f["code"]: f["severity"] for f in res["findings"]}
        self.assertEqual(codes["cf_block_ai_bots_on"], "high")
        self.assertEqual(codes["cloudflare_managed_robots"], "high")  # PerplexityBot is a search agent
        self.assertIn("cf_bot_fight_mode_on", codes)

    def test_no_api_mode_has_no_http_calls(self):
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cf.main(["settings", "--host", "https://Example.com/", "--no-api", "--json", "--seo-dir", td])
            self.assertTrue(list((Path(td) / "audits").glob("cloudflare-settings-*.md")))
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])
        self.assertTrue(json.loads(out.getvalue())["robots"]["present"])

    def test_cli_bots_no_secret(self):
        with tempfile.TemporaryDirectory() as td, mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = cf.main(["bots", "--zone", "example.com", "--days", "2", "--seo-dir", td])
            written = "".join(p.read_text(encoding="utf-8") for p in (Path(td) / "audits").glob("cloudflare-bots-*"))
        self.assertEqual(rc, 0)
        self.assertNotIn("cf_secret_tok", out.getvalue() + written)
        self.assertIn("Claude-SearchBot", out.getvalue())

    def test_bad_host_rejected(self):
        with self.assertRaises(cf.ApiError):
            cf.clean_host("exa mple.com")

    def test_day_windows(self):
        w = cf.day_windows(2, datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(w, [("2026-11-18T12:00:00Z", "2026-11-19T12:00:00Z"), ("2026-11-17T12:00:00Z", "2026-11-18T12:00:00Z")])


if __name__ == "__main__":
    unittest.main()
