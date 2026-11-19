"""Offline tests for scripts/google_updates.py (HTTP mocked; fixtures trimmed from the live dashboard, Nov 2026)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("google_updates", ROOT / "scripts" / "google_updates.py")
gu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gu)

# Trimmed sample of https://status.search.google.com/incidents.json as observed (list of incident objects).
INCIDENTS = [
    {"id": "WLxcJyC6jMWMfryxsKMc", "number": "6567352038624654547", "begin": "2026-11-10T18:06:00+00:00",
     "created": "2026-11-10T18:07:10+00:00", "modified": "2026-11-10T18:07:10+00:00",
     "external_desc": "November 2026 core update",
     "updates": [{"created": "2026-11-10T18:07:10+00:00", "modified": "2026-11-10T18:07:10+00:00",
                  "when": "2026-11-10T18:07:10+00:00",
                  "text": "Released the November 2026 core update <https://developers.google.com/search/docs/appearance/core-updates>, "
                          "which applies globally and to all languages. The rollout may take up to 2 weeks to complete.",
                  "status": "SERVICE_INFORMATION"}],
     "status_impact": "SERVICE_INFORMATION", "severity": "low", "service_key": "rGHU1u87FJnkP6W2GwMi",
     "service_name": "Ranking", "affected_products": [{"title": "Ranking", "id": "rGHU1u87FJnkP6W2GwMi"}],
     "uri": "incidents/WLxcJyC6jMWMfryxsKMc"},
    {"id": "XhUDXP7A67iHCD2kmbVu", "number": "5986798205446974932", "begin": "2026-09-24T16:15:00+00:00",
     "created": "2026-09-24T16:15:47+00:00", "end": "2026-10-08T08:00:00+00:00", "modified": "2026-10-08T08:37:28+00:00",
     "external_desc": "September 2026 spam update",
     "updates": [{"created": "2026-10-08T08:37:09+00:00", "modified": "2026-10-08T08:37:28+00:00",
                  "when": "2026-10-08T08:37:09+00:00", "text": "The rollout was complete as of October 8, 2026.",
                  "status": "AVAILABLE"}],
     "status_impact": "SERVICE_INFORMATION", "severity": "low", "service_key": "rGHU1u87FJnkP6W2GwMi",
     "service_name": "Ranking", "affected_products": [{"title": "Ranking", "id": "rGHU1u87FJnkP6W2GwMi"}],
     "uri": "incidents/XhUDXP7A67iHCD2kmbVu"},
    {"id": "TZF4pLmbzFdoBPP6o3iv", "number": "8891693348028902943", "begin": "2026-02-25T03:55:00+00:00",
     "created": "2026-02-25T06:33:39+00:00", "end": "2026-02-25T04:10:00+00:00", "modified": "2026-02-25T06:34:29+00:00",
     "external_desc": "Serving was experiencing an issue",
     "updates": [{"created": "2026-02-25T06:34:29+00:00", "modified": "2026-02-25T06:34:29+00:00",
                  "when": "2026-02-25T06:34:29+00:00",
                  "text": "We fixed the issue with serving search results. There will be no more updates.", "status": "AVAILABLE"}],
     "status_impact": "SERVICE_DISRUPTION", "severity": "medium", "service_key": "pKUD9XkLn3TBLquSpQMD",
     "service_name": "Serving", "affected_products": [{"title": "Serving", "id": "pKUD9XkLn3TBLquSpQMD"}],
     "uri": "incidents/TZF4pLmbzFdoBPP6o3iv"},
]
PRODUCTS = {"products": [{"title": "Indexing", "id": "DRyTdKyPd41QXD2hnncp"}, {"title": "Ranking", "id": "rGHU1u87FJnkP6W2GwMi"}]}


def row(iid, title, d, dur):
    return (f'<tr> <th class="ise88CpWulY__summary" scope="row"> <a href="../../incidents/{iid}"> '
            f'<span class="ise88CpWulY__summary-text">{title}</span> <span class="ise88CpWulY__summary-tooltip" '
            f'aria-hidden="true">{title}</span> </a> </th> <td class="ise88CpWulY__date">{d}</td> '
            f'<td class="ise88CpWulY__duration"> <div class="ise88CpWulY__icon-container"> <psd-status-icon '
            f'class="psd__status-icon"><svg class="psd__status-icon psd__available"></svg></psd-status-icon> '
            f'<span class="ise88CpWulY__duration-text">{dur}</span> </div> </td> </tr>')


RANKING_HISTORY = ("<html><body><h3>2026</h3><table><tbody>"
                   + row("WLxcJyC6jMWMfryxsKMc", "November 2026 core update", "10 Nov 2026", "Information")
                   + row("XhUDXP7A67iHCD2kmbVu", "September 2026 spam update", "24 Sep 2026", "13 days, 16 hours")
                   + "</tbody></table><h3>2023</h3><table><tbody>"
                   + row("53diuQvcEsgzqXTPBb8p", "September 2023 helpful content update", "14 Sep 2023", "13 days, 11 hours")
                   + row("VqRTcmyQCwfpuYeaVNfn", "November 2023 reviews update", "8 Nov 2023", "29 days")
                   + "</tbody></table><h3>2022</h3><table><tbody>"
                   + row("zDCQR5mxLU3ZrQeQvwqN", "Page experience update for desktop", "22 Feb 2022", "9 days")
                   + "</tbody></table></body></html>")
INDEXING_HISTORY = ("<table><tbody>" + row("Jh8ty34prbHLqSRiWKqm", "We&#39;re investigating an issue with indexing in Google Search.",
                                           "31 Jan 2024", "7 days, 12 hours") + "</tbody></table>")


def fake_http(url, *a, **k):
    if url == gu.INCIDENTS_URL:
        return json.dumps(INCIDENTS).encode()
    if url == gu.PRODUCTS_URL:
        return json.dumps(PRODUCTS).encode()
    if url.endswith("rGHU1u87FJnkP6W2GwMi/history"):
        return RANKING_HISTORY.encode()
    if url.endswith("DRyTdKyPd41QXD2hnncp/history"):
        return INDEXING_HISTORY.encode()
    raise gu.FeedError(f"unexpected url {url}")


def offline(url, *a, **k):
    raise gu.FeedError(f"{url} -> network error: offline")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = gu.main(argv)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SUPER_SEO_CACHE_DIR": self.tmp.name})
        self.env.start()
        self.sl = mock.patch.object(gu, "_sleep", lambda s: None)
        self.sl.start()

    def tearDown(self):
        self.sl.stop()
        self.env.stop()
        self.tmp.cleanup()


class TestParsing(Base):
    def test_history_rows(self):
        rows = gu.parse_history_html(RANKING_HISTORY)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[1], {"id": "XhUDXP7A67iHCD2kmbVu", "title": "September 2026 spam update",
                                   "date": "24 Sep 2026", "duration": "13 days, 16 hours"})
        self.assertIn("We're investigating", gu.parse_history_html(INDEXING_HISTORY)[0]["title"])

    def test_duration(self):
        self.assertEqual(gu.parse_duration("13 days, 16 hours"), timedelta(days=13, hours=16))
        self.assertEqual(gu.parse_duration("19 hours, 30 minutes"), timedelta(hours=19, minutes=30))
        self.assertIsNone(gu.parse_duration("Information"))

    def test_classify(self):
        c = gu.classify
        self.assertEqual(c("March 2026 core update", "Ranking"), "core")
        self.assertEqual(c("December 2022 link spam update", "Ranking"), "spam")
        self.assertEqual(c("September 2023 helpful content update", "Ranking"), "helpful-content")
        self.assertEqual(c("February 2023 product reviews update", "Ranking"), "reviews")
        self.assertEqual(c("February 2026 Discover update", "Ranking"), "other-ranking")
        self.assertEqual(c("Google Search is experiencing an indexing issue", "Indexing"), "indexing")
        self.assertEqual(c("Serving was experiencing an issue", "Serving"), "serving")
        self.assertEqual(c("Crawling issue", "Crawling"), "crawling")

    def test_normalise_merges_json_first(self):
        feed = {"incidents": INCIDENTS, "history": [dict(r, service_name="Ranking") for r in gu.parse_history_html(RANKING_HISTORY)]}
        ups = gu.normalise(feed)
        by = {u["id"]: u for u in ups}
        self.assertEqual(len(ups), 6)
        self.assertEqual(by["XhUDXP7A67iHCD2kmbVu"]["source"], "incidents.json")
        self.assertEqual(by["XhUDXP7A67iHCD2kmbVu"]["end"], "2026-10-08")
        self.assertTrue(by["WLxcJyC6jMWMfryxsKMc"]["ongoing"])
        self.assertIsNone(by["WLxcJyC6jMWMfryxsKMc"]["end"])
        hc = by["53diuQvcEsgzqXTPBb8p"]
        self.assertEqual((hc["kind"], hc["begin"], hc["end"], hc["source"]), ("helpful-content", "2023-09-14", "2023-09-27", "history"))
        self.assertEqual(by["VqRTcmyQCwfpuYeaVNfn"]["kind"], "reviews")
        self.assertEqual(set(ups[0]), {"id", "title", "kind", "service", "begin", "end", "ongoing", "url", "source"})
        self.assertEqual(ups[0]["url"], "https://status.search.google.com/incidents/WLxcJyC6jMWMfryxsKMc")
        self.assertEqual([u["begin"] for u in ups], sorted([u["begin"] for u in ups], reverse=True))


class TestFeedAndCache(Base):
    def test_live_then_cache(self):
        with mock.patch.object(gu, "http_get", side_effect=fake_http) as h:
            feed, origin = gu.load_feed()
            self.assertEqual(origin, "live")
            self.assertEqual(h.call_count, 4)  # incidents + products + 2 history pages
            feed2, origin2 = gu.load_feed()
            self.assertEqual(origin2, "cache")
            self.assertEqual(h.call_count, 4)
        self.assertTrue((Path(self.tmp.name) / gu.CACHE_FILE).exists())
        self.assertEqual(len(gu.normalise(feed2)), 7)

    def test_expired_cache_refetches(self):
        old = {"fetched_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), "incidents": [],
               "history": [], "with_history": True}
        (Path(self.tmp.name) / gu.CACHE_FILE).write_text(json.dumps(old), encoding="utf-8")
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            _, origin = gu.load_feed()
        self.assertEqual(origin, "live")

    def test_stale_cache_when_offline(self):
        old = {"fetched_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(), "incidents": INCIDENTS,
               "history": [], "with_history": True}
        (Path(self.tmp.name) / gu.CACHE_FILE).write_text(json.dumps(old), encoding="utf-8")
        with mock.patch.object(gu, "http_get", side_effect=offline):
            feed, origin = gu.load_feed()
        self.assertEqual(origin, "stale-cache")
        self.assertTrue(any("live fetch failed" in n for n in feed["notes"]))

    def test_offline_no_cache_no_fabrication(self):
        with mock.patch.object(gu, "http_get", side_effect=offline):
            rc, out, err = run(["list"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("unreachable", err)
        self.assertIn("No update list is shown", err)

    def test_list_kind_and_since(self):
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            rc, out, _ = run(["list", "--since", "2026-01-01", "--kind", "core,spam", "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual([u["kind"] for u in d["updates"]], ["core", "spam"])
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            rc, out, _ = run(["list"])
        self.assertIn("| 2023-09-14 | 2023-09-27 | helpful-content |", out)
        self.assertIn("| 2026-11-10 | ongoing | core |", out)

    def test_bad_kind(self):
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            rc, _, err = run(["list", "--kind", "panda"])
        self.assertEqual(rc, 2)

    def test_bad_json_endpoint(self):
        with mock.patch.object(gu, "http_get", return_value=b"<html>moved</html>"):
            rc, out, err = run(["list", "--no-history"])
        self.assertEqual(rc, 1)
        self.assertIn("not JSON", err)


def series_csv(path: Path, start: date, values: list[float], header=("Date", "Clicks", "Impressions", "CTR", "Position")):
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(header) + "\n")
        for i, v in enumerate(values):
            f.write(f"{(start + timedelta(days=i)).isoformat()},{int(v)},{int(v * 20)},5%,8.1\n")


class TestCorrelate(Base):
    def setUp(self):
        super().setUp()
        self.dir = Path(self.tmp.name) / "gsc"
        self.dir.mkdir()

    def test_read_daily_fills_gaps_and_formats(self):
        p = self.dir / "Dates.csv"
        p.write_text("Date,Clicks,Impressions,CTR,Position\n2026-01-03,5,100,5%,3\n2026-01-01,\"1,200\",2000,6%,4\n",
                     encoding="utf-8")
        s = gu.read_daily(self.dir)
        self.assertEqual([r["clicks"] for r in s], [1200, 0, 5])
        j = self.dir / "gsc-date.json"
        j.write_text(json.dumps({"rows": [{"date": "2026-02-01", "clicks": 3, "impressions": 9}]}), encoding="utf-8")
        self.assertEqual(gu.read_daily(j)[0]["clicks"], 3)
        raw = self.dir / "raw"
        raw.mkdir()
        rp = raw / "gsc-date-20261101.csv"
        rp.write_text("date,clicks,impressions,ctr,position\n2026-03-01,7,70,0.1,5\n", encoding="utf-8")
        self.assertEqual(gu.read_daily(rp)[0]["clicks"], 7)

    def test_missing_daily_file(self):
        with self.assertRaises(ValueError):
            gu.read_daily(self.dir)

    def test_drop_overlapping_update(self):
        start = date(2026, 8, 1)
        drop_day = (date(2026, 9, 26) - start).days
        vals = [100.0] * drop_day + [50.0] * 30
        series_csv(self.dir / "Dates.csv", start, vals)
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            rc, out, _ = run(["correlate", "--gsc", str(self.dir), "--json"])
        self.assertEqual(rc, 0)
        d = json.loads(out)
        drops = [w for w in d["windows"] if w["direction"] == "drop"]
        self.assertEqual(len(drops), 1)
        self.assertIn("September 2026 spam update", [u["title"] for u in drops[0]["overlapping_updates"]])
        self.assertLess(drops[0]["peak_change"], -0.2)
        f = [x for x in d["findings"] if x["code"] == "traffic_drop_overlaps_update"][0]
        self.assertEqual(f["severity"], "high")
        self.assertIn("a hypothesis, not proof", f["message"])
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            rc, md, _ = run(["correlate", "--gsc", str(self.dir)])
        self.assertIn("A date overlap is a hypothesis, not proof.", md)

    def test_change_without_update(self):
        start = date(2025, 1, 1)
        vals = [100.0] * 50 + [160.0] * 20
        series_csv(self.dir / "Dates.csv", start, vals)
        with mock.patch.object(gu, "http_get", side_effect=fake_http):
            rc, out, _ = run(["correlate", "--gsc", str(self.dir / "Dates.csv"), "--json"])
        d = json.loads(out)
        self.assertEqual([w["direction"] for w in d["windows"]], ["rise"])
        self.assertEqual(d["windows"][0]["overlapping_updates"], [])
        self.assertEqual(d["findings"][0]["code"], "traffic_rise_no_update")

    def test_flat_and_low_volume(self):
        self.assertEqual(gu.change_windows([{"date": date(2026, 1, 1) + timedelta(days=i), "clicks": 50.0, "impressions": 0.0}
                                            for i in range(60)]), [])
        tiny = [{"date": date(2026, 1, 1) + timedelta(days=i), "clicks": 1.0 if i < 40 else 4.0, "impressions": 0.0} for i in range(60)]
        self.assertEqual(gu.change_windows(tiny, min_clicks=5), [])

    def test_overlap_padding(self):
        w = {"start": "2026-10-10", "end": "2026-10-12"}
        u = {"begin": "2026-09-24", "end": "2026-10-08"}
        self.assertEqual(gu.overlaps(w, [u], 3), [u])
        self.assertEqual(gu.overlaps(w, [u], 1), [])
        ongoing = {"begin": "2026-11-10", "end": None}
        self.assertEqual(gu.overlaps({"start": "2026-11-15", "end": "2026-11-16"}, [ongoing]), [ongoing])


if __name__ == "__main__":
    unittest.main()
