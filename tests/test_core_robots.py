"""Offline tests for scripts/robots_rfc.py (RFC 9309 matching)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from robots_rfc import Robots  # noqa: E402

U = "https://example.org"


class RobotsTests(unittest.TestCase):
    def test_longest_match_wins(self):
        r = Robots("User-agent: *\nAllow: /\nDisallow: /app/\n")
        self.assertFalse(r.can_fetch("Googlebot", U + "/app/x"))
        self.assertTrue(r.can_fetch("Googlebot", U + "/pricing"))
        r2 = Robots("User-agent: *\nDisallow: /docs\nAllow: /docs/public\n")
        self.assertTrue(r2.can_fetch("x", U + "/docs/public/page"))
        self.assertFalse(r2.can_fetch("x", U + "/docs/private"))

    def test_allow_wins_tie(self):
        r = Robots("User-agent: *\nDisallow: /page\nAllow: /page\n")
        self.assertTrue(r.can_fetch("x", U + "/page"))
        r2 = Robots("User-agent: *\nAllow: /page\nDisallow: /page\n")
        self.assertTrue(r2.can_fetch("x", U + "/page"))

    def test_wildcards(self):
        r = Robots("User-agent: *\nDisallow: /*.pdf\nDisallow: /search*q=\n")
        self.assertFalse(r.can_fetch("x", U + "/files/report.pdf"))
        self.assertFalse(r.can_fetch("x", U + "/files/report.pdf?v=2"))
        self.assertFalse(r.can_fetch("x", U + "/search?q=crm"))
        self.assertTrue(r.can_fetch("x", U + "/files/report.html"))

    def test_end_anchor(self):
        r = Robots("User-agent: *\nDisallow: /*.php$\nDisallow: /exact$\n")
        self.assertFalse(r.can_fetch("x", U + "/index.php"))
        self.assertTrue(r.can_fetch("x", U + "/index.php?x=1"))
        self.assertTrue(r.can_fetch("x", U + "/index.phpx"))
        self.assertFalse(r.can_fetch("x", U + "/exact"))
        self.assertTrue(r.can_fetch("x", U + "/exact/more"))

    def test_stacked_user_agents_share_group(self):
        r = Robots("User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
        self.assertFalse(r.can_fetch("GPTBot", U + "/"))
        self.assertFalse(r.can_fetch("CCBot", U + "/blog"))
        self.assertTrue(r.can_fetch("Googlebot", U + "/blog"))
        self.assertTrue(r.explicit("ccbot"))

    def test_named_group_overrides_star(self):
        r = Robots("User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n")
        self.assertTrue(r.can_fetch("Googlebot", U + "/pricing"))
        self.assertFalse(r.can_fetch("bingbot", U + "/pricing"))
        # a named group does NOT inherit '*' rules
        r2 = Robots("User-agent: *\nDisallow: /private\n\nUser-agent: Googlebot\nDisallow: /tmp\n")
        self.assertTrue(r2.can_fetch("Googlebot", U + "/private"))
        self.assertFalse(r2.can_fetch("Googlebot", U + "/tmp"))

    def test_full_user_agent_string_uses_product_token(self):
        r = Robots("User-agent: Googlebot\nDisallow: /x\n")
        self.assertFalse(r.can_fetch("Googlebot/2.1", U + "/x"))

    def test_empty_disallow_allows_everything(self):
        r = Robots("User-agent: *\nDisallow:\n")
        self.assertTrue(r.can_fetch("x", U + "/anything"))
        self.assertEqual(r.groups["*"], [])

    def test_case_insensitive_tokens_and_keys(self):
        r = Robots("USER-AGENT: gptbot\nDISALLOW: /\n")
        self.assertFalse(r.can_fetch("GPTBot", U + "/"))
        self.assertTrue(r.explicit("GPTBOT"))
        # paths stay case-sensitive
        r2 = Robots("User-agent: *\nDisallow: /Admin\n")
        self.assertFalse(r2.can_fetch("x", U + "/Admin"))
        self.assertTrue(r2.can_fetch("x", U + "/admin"))

    def test_robots_txt_always_allowed(self):
        r = Robots("User-agent: *\nDisallow: /\n")
        self.assertTrue(r.can_fetch("x", U + "/robots.txt"))
        self.assertFalse(r.can_fetch("x", U + "/"))

    def test_no_matching_group_allows(self):
        r = Robots("User-agent: GPTBot\nDisallow: /\n")
        self.assertTrue(r.can_fetch("Googlebot", U + "/"))
        self.assertEqual(r.group_for("Googlebot"), (None, []))

    def test_comments_sitemaps_and_percent_decoding(self):
        r = Robots("# hi\nUser-agent: * # all\nDisallow: /caf%C3%A9 # cafe\nSitemap: https://example.org/sitemap.xml\n")
        self.assertEqual(r.sitemaps, ["https://example.org/sitemap.xml"])
        self.assertFalse(r.can_fetch("x", U + "/caf%C3%A9/menu"))

    def test_duplicate_groups_merge(self):
        r = Robots("User-agent: a\nDisallow: /one\n\nUser-agent: a\nDisallow: /two\n")
        self.assertFalse(r.can_fetch("a", U + "/one"))
        self.assertFalse(r.can_fetch("a", U + "/two"))

    def test_rules_before_any_user_agent_ignored(self):
        r = Robots("Disallow: /\nUser-agent: *\nAllow: /\n")
        self.assertTrue(r.can_fetch("x", U + "/"))


if __name__ == "__main__":
    unittest.main()
