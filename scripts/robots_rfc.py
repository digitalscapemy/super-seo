"""robots_rfc.py - RFC 9309 robots.txt matcher (what Google and most modern crawlers implement).

Python's urllib.robotparser uses first-match-wins, which is wrong for files like
    Allow: /
    Disallow: /app/
(Google: /app/x is disallowed, longest match wins). This module implements:
  - group selection: the group(s) whose user-agent token matches the crawler's product token (case-insensitive);
    if none match, the '*' group; stacked User-agent lines share one group; duplicate groups are merged
  - longest matching path wins; on a tie Allow wins
  - '*' wildcard and '$' end anchor, percent-decoding of the path
  - /robots.txt itself is always allowed
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, unquote


class Robots:
    def __init__(self, text: str):
        self.groups: dict[str, list[tuple[bool, str]]] = {}
        self.sitemaps: list[str] = []
        current: list[str] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = [x.strip() for x in line.split(":", 1)]
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    current, in_rules = [], False
                token = val.lower().split("/")[0].strip()
                current.append(token)
                self.groups.setdefault(token, [])
            elif key in ("allow", "disallow"):
                in_rules = True
                if not current:
                    continue
                if key == "disallow" and val == "":
                    continue  # empty Disallow = allow everything
                for ua in current:
                    self.groups[ua].append((key == "allow", val))
            elif key == "sitemap":
                self.sitemaps.append(val)
            else:
                in_rules = in_rules or bool(current)

    def group_for(self, agent: str) -> tuple[str | None, list[tuple[bool, str]]]:
        token = agent.lower().split("/")[0].strip()
        if token in self.groups:
            return token, self.groups[token]
        if "*" in self.groups:
            return "*", self.groups["*"]
        return None, []

    @staticmethod
    def _pattern(rule: str) -> re.Pattern:
        anchored = rule.endswith("$")
        body = rule[:-1] if anchored else rule
        rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in unquote(body))
        return re.compile("^" + rx + ("$" if anchored else ""))

    def can_fetch(self, agent: str, url: str) -> bool:
        p = urlparse(url)
        path = unquote(p.path or "/") + (("?" + p.query) if p.query else "")
        if path == "/robots.txt":
            return True
        _, rules = self.group_for(agent)
        best_len, best_allow = -1, True
        for allow, rule in rules:
            if not rule:
                continue
            if self._pattern(rule).match(path):
                length = len(rule)
                if length > best_len or (length == best_len and allow):
                    best_len, best_allow = length, allow
        return best_allow

    def explicit(self, agent: str) -> bool:
        return agent.lower().split("/")[0].strip() in self.groups
