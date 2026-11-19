#!/usr/bin/env python3
"""youtube.py - YouTube Data API v3 for super-seo (free daily quota). Stdlib only, Python 3.10+.

Setup: Google Cloud console > enable "YouTube Data API v3" > Credentials > API key, restrict it to that API,
then set YOUTUBE_API_KEY. Default free quota: 10,000 units/day per project.

  python youtube.py mentions --q "acme" [--months 12] [--max 50] [--own-channel UCxxxx] [--competitors "foo,bar"]
  python youtube.py channel --handle @acme          (or --id UCxxxx) [--recent 10]
  python youtube.py video-seo --id dQw4w9WgXcQ [--brand acme]

Quota cost per command (printed after each run): mentions = 100 (search.list) + 1 (videos.list);
channel = 1 (channels.list) + 1 (playlistItems.list) + 1 (videos.list); video-seo = 1 (videos.list).
Every command accepts --json and --no-write (skip .seo/audits/youtube-<cmd>-<slug>-<stamp>.json|md).
Exit codes: 0 ok, 1 API error, 2 missing key / bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

BASE = "https://www.googleapis.com/youtube/v3/"
UA = "super-seo/1.0 (+https://github.com/digitalscapemy/super-seo)"
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
COST = {"search": 100, "videos": 1, "channels": 1, "playlistItems": 1}
DAILY_QUOTA = 10_000
TIMESTAMP = re.compile(r"^\s*[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?\s*[-–—:|]?\s*\S", re.M)
LINK = re.compile(r"https?://[^\s<>\"]+", re.I)
DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

_sleep = time.sleep
QUOTA = {"used": 0}


class YTError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class MissingKey(Exception):
    pass


def api_key() -> str:
    k = os.environ.get("YOUTUBE_API_KEY")
    if not k:
        raise MissingKey("Set YOUTUBE_API_KEY (Google Cloud > YouTube Data API v3 > API key, restricted to that API).")
    return k


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def call(resource: str, params: dict, timeout: float = 30.0, retries: int = 4) -> dict:
    """GET a YouTube Data API resource; counts quota units; backoff on 429/5xx (quotaExceeded is not retried)."""
    key = api_key()
    url = BASE + resource + "?" + urlencode({**params, "key": key})
    for attempt in range(retries + 1):
        req = request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            QUOTA["used"] += COST.get(resource, 1)
            return json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except error.HTTPError as e:
            try:
                body = (e.read() or b"").decode("utf-8", errors="replace")
            except Exception:
                body = ""
            QUOTA["used"] += COST.get(resource, 1)  # failed calls still cost quota
            reason = ""
            try:
                errs = json.loads(body).get("error", {})
                reason = (errs.get("errors") or [{}])[0].get("reason", "") or errs.get("status", "")
                body = errs.get("message", body)
            except (json.JSONDecodeError, AttributeError):
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                raise YTError(e.code, f"{resource} -> quota exceeded for today (resets at midnight Pacific time)") from None
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                _sleep(max(2 ** attempt + random.uniform(0, 1), float(ra) if ra and ra.isdigit() else 0))
                continue
            raise YTError(e.code, f"{resource} -> HTTP {e.code} {reason}: {_redact(body, key)[:400]}") from None
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                _sleep(2 ** attempt + random.uniform(0, 1))
                continue
            raise YTError(0, f"{resource} -> network error: {_redact(str(getattr(e, 'reason', e)), key)}") from None
        except json.JSONDecodeError:
            raise YTError(200, f"{resource} -> non-JSON response") from None
    raise YTError(0, f"{resource} -> failed")


# --------------------------------------------------------------------------- helpers
def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def iso_duration_seconds(s: str) -> int:
    m = DURATION.fullmatch(s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _secs(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def chapters(description: str) -> list[dict]:
    """YouTube chapter rules: first timestamp 0:00, at least 3 ascending timestamps, each chapter >= 10 s."""
    marks = [(m.group(1), _secs(m.group(1))) for m in TIMESTAMP.finditer(description or "")]
    return [{"time": t, "seconds": s} for t, s in marks]


def chapters_valid(marks: list[dict]) -> tuple[bool, str]:
    if not marks:
        return False, "no timestamps in the description"
    if marks[0]["seconds"] != 0:
        return False, "first timestamp is not 0:00"
    if len(marks) < 3:
        return False, f"only {len(marks)} timestamps (need 3+)"
    for a, b in zip(marks, marks[1:]):
        if b["seconds"] - a["seconds"] < 10:
            return False, f"chapter at {a['time']} is shorter than 10 seconds or out of order"
    return True, f"{len(marks)} chapters"


def mentions_brand(text: str, brand: str) -> bool:
    return bool(brand) and re.search(r"(?<![a-z0-9])" + re.escape(brand.lower()) + r"(?![a-z0-9])", (text or "").lower()) is not None


def video_rows(items: list[dict]) -> list[dict]:
    rows = []
    for v in items or []:
        sn, st, cd = v.get("snippet") or {}, v.get("statistics") or {}, v.get("contentDetails") or {}
        rows.append({"id": v.get("id"), "title": sn.get("title"), "channel_id": sn.get("channelId"),
                     "channel": sn.get("channelTitle"), "published": sn.get("publishedAt"),
                     "views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                     "comments": _int(st.get("commentCount")), "duration_s": iso_duration_seconds(cd.get("duration", "")),
                     "captions": cd.get("caption") == "true", "description": sn.get("description") or "",
                     "url": f"https://www.youtube.com/watch?v={v.get('id')}"})
    return rows


def md_table(headers, rows) -> list[str]:
    def cell(v):
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + \
        ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]


def md_findings(findings) -> list[str]:
    if not findings:
        return ["", "No findings."]
    return ["", "## Findings", ""] + [f"- **{f['severity']}** `{f['code']}`: {f['message']}" for f in findings]


def quota_line() -> str:
    return f"Quota spent this run: {QUOTA['used']} units (default free quota {DAILY_QUOTA:,}/day)."


def emit(a, data: dict, md: str, slug: str) -> None:
    data["findings"] = sorted(data.get("findings", []), key=lambda f: ORDER.get(f["severity"], 9))
    data["quota_used"] = QUOTA["used"]
    md = md + "\n\n" + quota_line()
    print(json.dumps(data, indent=2, ensure_ascii=False) if a.json else md)
    print(quota_line(), file=sys.stderr)
    if not a.no_write:
        out = Path(".seo") / "audits"
        out.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9.@-]+", "_", slug)[:60] or "x"
        base = out / f"youtube-{a.cmd}-{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        (base.parent / (base.name + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        (base.parent / (base.name + ".md")).write_text(md, encoding="utf-8")
        print(f"[saved] {base}.json / .md", file=sys.stderr)


# --------------------------------------------------------------------------- commands
def cmd_mentions(a):
    after = (datetime.now(timezone.utc) - timedelta(days=round(30.44 * a.months))).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"part": "snippet", "q": a.q, "type": "video", "order": "relevance", "maxResults": min(max(a.max, 1), 50),
              "publishedAfter": after}
    if a.region:
        params["regionCode"] = a.region
    if a.lang:
        params["relevanceLanguage"] = a.lang
    res = call("search", params)
    ids = [(i.get("id") or {}).get("videoId") for i in res.get("items") or []]
    ids = [i for i in ids if i]
    rows = video_rows(call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)}).get("items")) if ids else []
    order = {v: n for n, v in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r["id"], 999))
    brand = a.brand or a.q.strip('"')
    for r in rows:
        r["mentions_brand"] = mentions_brand(r["title"] + " " + r["description"], brand)
        r["own_channel"] = bool(a.own_channel) and r["channel_id"] == a.own_channel
    about = [r for r in rows if r["mentions_brand"]]
    channels: dict[str, dict] = {}
    for r in rows:
        c = channels.setdefault(r["channel_id"], {"channel_id": r["channel_id"], "channel": r["channel"], "videos": 0, "views": 0})
        c["videos"] += 1
        c["views"] += r["views"]
    top_channels = sorted(channels.values(), key=lambda c: (-c["videos"], -c["views"]))
    findings = []
    if not about:
        findings.append({"severity": "high", "code": "no_brand_videos",
                         "message": f"No video in the top {len(rows)} results for '{a.q}' (last {a.months} months) mentions "
                                    f"'{brand}' in its title or description."})
    elif len(about) < 5:
        findings.append({"severity": "medium", "code": "few_brand_videos",
                         "message": f"Only {len(about)} of {len(rows)} results mention '{brand}'."})
    if a.own_channel:
        own = [r for r in rows if r["own_channel"]]
        if not own:
            findings.append({"severity": "medium", "code": "own_channel_absent",
                             "message": f"Your channel {a.own_channel} has no video in these results."})
        third = [r for r in about if not r["own_channel"]]
        if about and not third:
            findings.append({"severity": "medium", "code": "no_third_party_videos",
                             "message": "Every brand video is from your own channel; independent reviews/tutorials are missing."})
    comp_counts = {}
    for comp in [c.strip() for c in (a.competitors or "").split(",") if c.strip()]:
        n = sum(1 for r in rows if mentions_brand(r["title"] + " " + r["description"], comp))
        comp_counts[comp] = n
        if n > len(about):
            findings.append({"severity": "medium", "code": "competitor_dominates",
                             "message": f"'{comp}' appears in {n} results vs {len(about)} for '{brand}'."})
    if top_channels and rows and top_channels[0]["videos"] >= max(3, len(rows) // 3) and top_channels[0]["channel_id"] != a.own_channel:
        findings.append({"severity": "info", "code": "channel_concentration",
                         "message": f"{top_channels[0]['channel']} holds {top_channels[0]['videos']} of {len(rows)} results."})
    md = [f"# YouTube mentions: {a.q}", "", f"- Window: last {a.months} months (publishedAfter {after}), order=relevance, "
          f"{len(rows)} videos; {len(about)} mention '{brand}'", ""]
    md += md_table(["#", "Title", "Channel", "Published", "Views", "Captions", "Mentions brand"],
                   [[n + 1, r["title"], r["channel"], (r["published"] or "")[:10], f"{r['views']:,}", "yes" if r["captions"] else "",
                     "yes" if r["mentions_brand"] else ""] for n, r in enumerate(rows[:a.top])])
    md += ["", "## Channels in the results", ""] + md_table(["Channel", "Videos", "Views"],
                                                             [[c["channel"], c["videos"], f"{c['views']:,}"] for c in top_channels[:10]])
    if comp_counts:
        md += ["", "Competitor mentions: " + ", ".join(f"{k}: {v}" for k, v in comp_counts.items())]
    md += md_findings(findings)
    for r in rows:
        r.pop("description", None)
    return {"command": "mentions", "q": a.q, "brand": brand, "months": a.months, "videos": rows, "brand_videos": len(about),
            "channels": top_channels, "competitor_mentions": comp_counts, "findings": findings}, "\n".join(md), a.q


def cmd_channel(a):
    params = {"part": "snippet,statistics,contentDetails,brandingSettings"}
    if a.id:
        params["id"] = a.id
    else:
        params["forHandle"] = a.handle if a.handle.startswith("@") else "@" + a.handle
    items = call("channels", params).get("items") or []
    if not items:
        raise ValueError(f"channel not found: {a.id or a.handle}")
    ch = items[0]
    sn, st = ch.get("snippet") or {}, ch.get("statistics") or {}
    uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    recent = []
    if uploads:
        pl = call("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": min(a.recent, 50)}).get("items") or []
        vids = [(i.get("contentDetails") or {}).get("videoId") for i in pl]
        vids = [v for v in vids if v]
        if vids:
            recent = video_rows(call("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(vids)}).get("items"))
            recent.sort(key=lambda r: r["published"] or "", reverse=True)
    info = {"id": ch.get("id"), "title": sn.get("title"), "handle": sn.get("customUrl"), "country": sn.get("country"),
            "published": sn.get("publishedAt"), "description_length": len(sn.get("description") or ""),
            "subscribers": None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
            "views": _int(st.get("viewCount")), "videos": _int(st.get("videoCount")),
            "url": f"https://www.youtube.com/channel/{ch.get('id')}"}
    findings = []
    if info["description_length"] < 100:
        findings.append({"severity": "medium", "code": "channel_description_short",
                         "message": f"Channel description is {info['description_length']} characters; say what the brand is and link the site."})
    if not info["country"]:
        findings.append({"severity": "low", "code": "channel_no_country", "message": "Channel country is not set."})
    if recent:
        last = datetime.fromisoformat(recent[0]["published"].replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - last).days
        if days > 90:
            findings.append({"severity": "medium", "code": "channel_inactive", "message": f"Last upload {days} days ago."})
        nocap = [r for r in recent if not r["captions"]]
        if nocap:
            findings.append({"severity": "low", "code": "uploads_without_captions",
                             "message": f"{len(nocap)} of {len(recent)} recent uploads have no uploaded captions (auto captions only)."})
        nochap = [r for r in recent if r["duration_s"] >= 180 and not chapters_valid(chapters(r["description"]))[0]]
        if nochap:
            findings.append({"severity": "low", "code": "uploads_without_chapters",
                             "message": f"{len(nochap)} recent uploads over 3 minutes have no valid chapters."})
    elif info["videos"] == 0:
        findings.append({"severity": "high", "code": "channel_empty", "message": "The channel has no public videos."})
    subs = "hidden" if info["subscribers"] is None else f"{info['subscribers']:,}"
    md = [f"# YouTube channel: {info['title']}", "", f"- {info['url']} ({info['handle'] or 'no handle'})",
          f"- Subscribers: {subs} · "
          f"views {info['views']:,} · videos {info['videos']:,} · country {info['country'] or 'not set'}", "",
          f"## {len(recent)} most recent uploads", ""]
    md += md_table(["Published", "Title", "Views", "Duration", "Captions", "Chapters"],
                   [[(r["published"] or "")[:10], r["title"], f"{r['views']:,}", f"{r['duration_s'] // 60}:{r['duration_s'] % 60:02d}",
                     "yes" if r["captions"] else "", "yes" if chapters_valid(chapters(r["description"]))[0] else ""] for r in recent])
    md += md_findings(findings)
    for r in recent:
        r.pop("description", None)
    return {"command": "channel", "channel": info, "recent": recent, "findings": findings}, "\n".join(md), info["handle"] or info["id"]


def cmd_video_seo(a):
    items = call("videos", {"part": "snippet,statistics,contentDetails", "id": a.id}).get("items") or []
    if not items:
        raise ValueError(f"video not found: {a.id}")
    v = items[0]
    sn = v.get("snippet") or {}
    row = video_rows([v])[0]
    title, desc = sn.get("title") or "", sn.get("description") or ""
    links = LINK.findall(desc)
    marks = chapters(desc)
    ok, why = chapters_valid(marks)
    tags = sn.get("tags") or []
    checks = {"title_length": len(title), "description_length": len(desc), "description_links": len(links),
              "tags": len(tags), "chapters": ok, "chapters_detail": why, "captions": row["captions"],
              "duration_s": row["duration_s"], "default_audio_language": sn.get("defaultAudioLanguage"),
              "first_150_chars": desc[:150]}
    findings = []
    if len(title) > 70:
        findings.append({"severity": "low", "code": "title_long", "message": f"Title is {len(title)} characters; it truncates around 70 in search."})
    elif len(title) < 20:
        findings.append({"severity": "low", "code": "title_short", "message": f"Title is {len(title)} characters; say what the video shows."})
    if len(desc) < 200:
        findings.append({"severity": "medium", "code": "description_short",
                         "message": f"Description is {len(desc)} characters; summarise the video in 2-3 sentences (this text is what search and AI systems read)."})
    if a.brand and not mentions_brand(desc[:150] + " " + title, a.brand):
        findings.append({"severity": "low", "code": "brand_not_early", "message": f"'{a.brand}' is not in the title or first 150 characters of the description."})
    if not links:
        findings.append({"severity": "low", "code": "no_links", "message": "No links in the description; link the relevant page on your site."})
    if not ok and row["duration_s"] >= 180:
        findings.append({"severity": "medium", "code": "no_chapters",
                         "message": f"No valid chapters ({why}); add timestamps starting at 0:00 (3+, each >= 10 s)."})
    if not row["captions"]:
        findings.append({"severity": "medium", "code": "no_uploaded_captions",
                         "message": "No uploaded captions (contentDetails.caption=false); auto captions are less accurate. Upload a reviewed transcript."})
    if not tags:
        findings.append({"severity": "info", "code": "no_tags", "message": "No tags (minor: YouTube says tags play a small role, mainly for misspellings)."})
    if not sn.get("defaultAudioLanguage"):
        findings.append({"severity": "info", "code": "no_audio_language", "message": "Default audio language is not set."})
    md = [f"# YouTube video SEO: {title}", "", f"- {row['url']} · {row['channel']} · {(row['published'] or '')[:10]} · {row['views']:,} views", "",
          "| Check | Value |", "|---|---|", f"| Title length | {len(title)} |", f"| Description length | {len(desc)} |",
          f"| Links in description | {len(links)} |", f"| Tags | {len(tags)} |", f"| Chapters | {'yes' if ok else 'no'} ({why}) |",
          f"| Uploaded captions | {'yes' if row['captions'] else 'no'} |", f"| Duration | {row['duration_s'] // 60}:{row['duration_s'] % 60:02d} |"]
    md += md_findings(findings)
    row.pop("description", None)
    return {"command": "video-seo", "video": row, "checks": checks, "chapters": marks, "links": links,
            "findings": findings}, "\n".join(md), a.id


COMMANDS = {"mentions": cmd_mentions, "channel": cmd_channel, "video-seo": cmd_video_seo}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--no-write", action="store_true")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mentions", parents=[common], help="search.list videos about a brand (100 units)")
    m.add_argument("--q", required=True, help="search query, usually the brand name")
    m.add_argument("--brand", help="text that counts as a brand mention (default: --q)")
    m.add_argument("--months", type=int, default=12)
    m.add_argument("--max", type=int, default=50)
    m.add_argument("--top", type=int, default=50)
    m.add_argument("--own-channel", help="your channel id (UC...)")
    m.add_argument("--competitors", help="comma list of competitor names to count")
    m.add_argument("--region", help="regionCode, e.g. MY")
    m.add_argument("--lang", help="relevanceLanguage, e.g. en")
    c = sub.add_parser("channel", parents=[common], help="channels.list statistics + recent uploads (3 units)")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--id")
    g.add_argument("--handle")
    c.add_argument("--recent", type=int, default=10)
    v = sub.add_parser("video-seo", parents=[common], help="one video's on-page checks (1 unit)")
    v.add_argument("--id", required=True)
    v.add_argument("--brand")
    return ap


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = build_parser().parse_args(argv)
    QUOTA["used"] = 0
    try:
        data, md, slug = COMMANDS[a.cmd](a)
        emit(a, data, md, slug)
        return 0
    except (MissingKey, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        if QUOTA["used"]:
            print(quota_line(), file=sys.stderr)
        return 2
    except YTError as e:
        print(f"error: YouTube API: {e}\n{quota_line()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
