"""Offline tests for extensions/youtube/youtube.py (urllib mocked; no network)."""
from __future__ import annotations

import contextlib
import email.message
import importlib.util
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib import error
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ext_youtube", ROOT / "extensions" / "youtube" / "youtube.py")
yt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yt)

KEY = "SECRETYTKEY42"
NOW = datetime.now(timezone.utc)


def iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def video(vid, title, channel_id, channel, desc="", caption="false", duration="PT8M5S", views="1000", tags=None, days=10):
    sn = {"title": title, "channelId": channel_id, "channelTitle": channel, "publishedAt": iso(days), "description": desc}
    if tags is not None:
        sn["tags"] = tags
    return {"id": vid, "snippet": sn, "statistics": {"viewCount": views, "likeCount": "10", "commentCount": "2"},
            "contentDetails": {"duration": duration, "caption": caption}}


GOOD_DESC = ("Acme invoicing walkthrough for freelancers: create, send and track invoices in five minutes. "
             "Full guide: https://acme.com/guides/invoices and pricing at https://acme.com/pricing\n\n"
             "0:00 Intro\n0:45 Create an invoice\n2:10 Send and track\n5:30 Reports\n" + "More details about the product. " * 5)


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, body: dict):
    return error.HTTPError(url, code, "err", email.message.Message(), io.BytesIO(json.dumps(body).encode()))


class Router:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        u = urlparse(req.full_url)
        res = u.path.rsplit("/", 1)[-1]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append((res, q))
        r = self.routes[res]
        out = r(q) if callable(r) else r
        if isinstance(out, Exception):
            raise out
        return FakeResp(out)


def run(argv, routes):
    router = Router(routes)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(yt.request, "urlopen", router), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = yt.main(argv + ["--no-write"])
    return rc, out.getvalue(), err.getvalue(), router


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": KEY})
        self.env.start()
        self.sl = mock.patch.object(yt, "_sleep", lambda s: None)
        self.sl.start()

    def tearDown(self):
        self.sl.stop()
        self.env.stop()


class TestHelpers(unittest.TestCase):
    def test_chapters(self):
        ok, why = yt.chapters_valid(yt.chapters(GOOD_DESC))
        self.assertTrue(ok, why)
        self.assertFalse(yt.chapters_valid(yt.chapters("1:00 a\n2:00 b\n3:00 c"))[0])
        self.assertFalse(yt.chapters_valid(yt.chapters("0:00 a\n0:05 b\n3:00 c"))[0])
        self.assertFalse(yt.chapters_valid(yt.chapters("0:00 a\n1:00 b"))[0])
        self.assertTrue(yt.chapters_valid(yt.chapters("00:00 Intro\n1:02:03 - Deep dive\n1:10:00 | End"))[0])

    def test_duration_and_brand_match(self):
        self.assertEqual(yt.iso_duration_seconds("PT1H2M3S"), 3723)
        self.assertEqual(yt.iso_duration_seconds("P1DT1S"), 86401)
        self.assertTrue(yt.mentions_brand("Why Acme beats spreadsheets", "acme"))
        self.assertFalse(yt.mentions_brand("Acmeville travel vlog", "acme"))


class TestMentions(Base):
    def routes(self, items, details):
        return {"search": {"items": [{"id": {"kind": "youtube#video", "videoId": v}} for v in items]},
                "videos": {"items": details}}

    def test_brand_present_and_quota(self):
        details = [video("v2", "Acme review 2026", "UCx", "Reviewer"), video("v1", "Invoice tips", "UCy", "Other", desc="We use Acme daily"),
                   video("v3", "Rival tutorial", "UCz", "Rival TV")]
        rc, out, err, router = run(["mentions", "--q", "acme", "--months", "6", "--json", "--competitors", "rival"],
                                   self.routes(["v1", "v2", "v3"], details))
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual([v["id"] for v in d["videos"]], ["v1", "v2", "v3"])  # search relevance order kept
        self.assertEqual(d["brand_videos"], 2)
        self.assertEqual(d["quota_used"], 101)
        self.assertIn("Quota spent this run: 101 units", err)
        q = router.calls[0][1]
        self.assertEqual((q["type"], q["order"], q["maxResults"]), ("video", "relevance", "50"))
        after = datetime.strptime(q["publishedAfter"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertAlmostEqual((NOW - after).days, 183, delta=2)
        self.assertNotIn("description", d["videos"][0])
        self.assertIn("few_brand_videos", [f["code"] for f in d["findings"]])

    def test_no_brand_and_competitor_dominates(self):
        details = [video(f"c{i}", f"Rival guide {i}", "UCr", "Rival TV") for i in range(4)]
        rc, out, _, _ = run(["mentions", "--q", "acme invoicing", "--brand", "acme", "--competitors", "rival",
                             "--own-channel", "UCacme", "--json"], self.routes([d["id"] for d in details], details))
        d = json.loads(out)
        c = {f["code"]: f["severity"] for f in d["findings"]}
        self.assertEqual(c["no_brand_videos"], "high")
        self.assertIn("competitor_dominates", c)
        self.assertIn("own_channel_absent", c)
        self.assertIn("channel_concentration", c)
        self.assertEqual(d["findings"][0]["severity"], "high")

    def test_empty_search_skips_videos_call(self):
        rc, out, _, router = run(["mentions", "--q", "zzz", "--json"], self.routes([], []))
        self.assertEqual([c[0] for c in router.calls], ["search"])
        self.assertEqual(json.loads(out)["quota_used"], 100)

    def test_quota_exceeded_not_retried_and_key_redacted(self):
        err_body = {"error": {"code": 403, "message": f"bad key={KEY}", "errors": [{"reason": "quotaExceeded"}]}}
        rc, out, err, router = run(["mentions", "--q", "acme"],
                                   {"search": lambda q: http_error("https://x", 403, err_body)})
        self.assertEqual(rc, 1)
        self.assertEqual(len(router.calls), 1)
        self.assertIn("quota exceeded", err)
        self.assertNotIn(KEY, err + out)

    def test_retry_on_503(self):
        state = {"n": 0}

        def flaky(q):
            state["n"] += 1
            return http_error("https://x", 503, {"error": {"message": "backend"}}) if state["n"] == 1 else {"items": []}
        rc, out, _, router = run(["mentions", "--q", "acme", "--json"], {"search": flaky})
        self.assertEqual(rc, 0)
        self.assertEqual(len(router.calls), 2)

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            rc, _, err, _ = run(["mentions", "--q", "acme"], {})
        self.assertEqual(rc, 2)
        self.assertIn("YOUTUBE_API_KEY", err)


class TestChannel(Base):
    def test_handle_and_recent(self):
        ch = {"items": [{"id": "UCacme", "snippet": {"title": "Acme", "customUrl": "@acme", "description": "short",
                                                     "publishedAt": iso(900)},
                         "statistics": {"viewCount": "5000", "subscriberCount": "120", "videoCount": "2", "hiddenSubscriberCount": False},
                         "contentDetails": {"relatedPlaylists": {"uploads": "UUacme"}}}]}
        vids = {"items": [video("a", "Old demo", "UCacme", "Acme", days=200), video("b", "Older", "UCacme", "Acme", days=300, caption="true")]}
        routes = {"channels": ch, "playlistItems": {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]},
                  "videos": vids}
        rc, out, err, router = run(["channel", "--handle", "acme", "--json"], routes)
        self.assertEqual(rc, 0)
        self.assertEqual(router.calls[0][1]["forHandle"], "@acme")
        d = json.loads(out)
        self.assertEqual(d["channel"]["subscribers"], 120)
        self.assertEqual(d["quota_used"], 3)
        c = [f["code"] for f in d["findings"]]
        for code in ("channel_description_short", "channel_no_country", "channel_inactive", "uploads_without_captions",
                     "uploads_without_chapters"):
            self.assertIn(code, c)
        rc, md, _, _ = run(["channel", "--id", "UCacme"], routes)
        self.assertIn("Subscribers: 120", md)

    def test_not_found(self):
        rc, _, err, _ = run(["channel", "--id", "UCnope"], {"channels": {"items": []}})
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)


class TestVideoSeo(Base):
    def test_good_video(self):
        v = video("g", "How to send invoices with Acme in 5 minutes", "UCacme", "Acme", desc=GOOD_DESC, caption="true", tags=["acme"])
        v["snippet"]["defaultAudioLanguage"] = "en"
        rc, out, err, _ = run(["video-seo", "--id", "g", "--brand", "acme", "--json"], {"videos": {"items": [v]}})
        d = json.loads(out)
        self.assertTrue(d["checks"]["chapters"])
        self.assertEqual(d["checks"]["description_links"], 2)
        self.assertEqual(d["checks"]["tags"], 1)
        self.assertEqual(d["findings"], [])
        self.assertEqual(d["quota_used"], 1)

    def test_weak_video(self):
        v = video("w", "x" * 90, "UCacme", "Acme", desc="Watch this", caption="false", duration="PT12M")
        rc, out, _, _ = run(["video-seo", "--id", "w", "--brand", "acme", "--json"], {"videos": {"items": [v]}})
        c = {f["code"] for f in json.loads(out)["findings"]}
        self.assertTrue({"title_long", "description_short", "no_links", "no_chapters", "no_uploaded_captions", "no_tags",
                         "brand_not_early", "no_audio_language"} <= c)


if __name__ == "__main__":
    unittest.main()
