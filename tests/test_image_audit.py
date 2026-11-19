"""Offline tests for scripts/image_audit.py (synthetic image headers, fake fetchers)."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import image_audit as ia  # noqa: E402

KB = 1024


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"


def gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 8


def jpeg(w, h):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    dqt = b"\xff\xdb" + struct.pack(">H", 5) + b"\x00\x00\x00"
    sof = b"\xff\xc2" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + dqt + sof + b"\xff\xda"


def webp_vp8x(w, h):
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_vp8(w, h):
    frame = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", w, h)
    return b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8 " + struct.pack("<I", len(frame)) + frame


def webp_vp8l(w, h):
    bits = (w - 1) | ((h - 1) << 14)
    return b"RIFF" + struct.pack("<I", 30) + b"WEBP" + b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)


def avif(w, h):
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"avif" + b"\x00\x00\x00\x00" + b"mif1avif"
    ispe = struct.pack(">I", 20) + b"ispe" + b"\x00\x00\x00\x00" + struct.pack(">II", w, h)
    return ftyp + b"\x00" * 40 + ispe


class Sniff(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(ia.sniff(png(1200, 630)), ("png", (1200, 630)))
        self.assertEqual(ia.sniff(gif(16, 9)), ("gif", (16, 9)))
        self.assertEqual(ia.sniff(jpeg(2400, 1600)), ("jpeg", (2400, 1600)))
        self.assertEqual(ia.sniff(webp_vp8x(1920, 1080)), ("webp", (1920, 1080)))
        self.assertEqual(ia.sniff(webp_vp8(800, 600)), ("webp", (800, 600)))
        self.assertEqual(ia.sniff(webp_vp8l(321, 123)), ("webp", (321, 123)))
        self.assertEqual(ia.sniff(avif(3000, 2000)), ("avif", (3000, 2000)))
        self.assertEqual(ia.sniff(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")[0], "svg")
        self.assertEqual(ia.sniff(b""), (None, None))
        self.assertEqual(ia.sniff(b"garbage"), (None, None))


class Alt(unittest.TestCase):
    def codes(self, alt, src="https://ex.com/img/team-photo.jpg"):
        return [c for _, c, _ in ia.alt_problems(alt, src)]

    def test_filename(self):
        self.assertIn("alt_is_filename", self.codes("team-photo"))
        self.assertIn("alt_is_filename", self.codes("team_photo.jpg"))
        self.assertIn("alt_is_filename", self.codes("IMG_2034"))

    def test_long_and_stuffed(self):
        self.assertIn("alt_too_long", self.codes("A " + "very " * 40 + "long description"))
        self.assertIn("alt_keyword_stuffed", self.codes("invoice software, invoice app, invoice tool, best invoice Malaysia"))

    def test_good_alt(self):
        self.assertEqual(self.codes("Dashboard showing monthly revenue by plan"), [])


class FakeImages:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, url, ua):
        self.calls.append(url)
        if url not in self.table:
            return {"url": url, "final_url": url, "status": 404, "error": None, "redirects": 0}
        ct, head, size = self.table[url]
        return {"url": url, "final_url": url, "status": 200, "error": None, "content_type": ct, "bytes": size,
                "over_cap": size > ia.MAX_IMAGE_BYTES, "head": head, "redirects": 0}


def fake_page(html):
    def f(url, ua, **kw):
        return {"url": url, "final_url": url, "status": 200, "error": None, "chain": [],
                "headers": {"content-type": "text/html"}, "body": html}
    return f


HTML = """<html><body>
<header><img src="/logo.svg" alt="Acme" width="100" height="32"></header>
<img src="/hero.jpg" alt="Invoice dashboard on a laptop" width="1200" height="600" loading="lazy">
<picture><source srcset="/team.avif" type="image/avif"><img src="/team.jpg" alt="" width="600" height="400"></picture>
<img src="/big.png" alt="big.png" width="300">
<img src="/thumb.webp" alt="Customer avatar" width="64" height="64" loading="lazy" decoding="async">
<div style="background-image: url('/bg.jpg')">x</div>
<img src="/missing.png" alt="x" width="10" height="10">
<noscript><img src="/hero.jpg"></noscript>
<img src="data:image/gif;base64,R0lGOD" alt="">
</body></html>"""

TABLE = {
    "https://ex.com/logo.svg": ("image/svg+xml", b"<svg></svg>", 2 * KB),
    "https://ex.com/hero.jpg": ("image/jpeg", jpeg(2400, 1200), 820 * KB),
    "https://ex.com/team.jpg": ("image/jpeg", jpeg(1200, 800), 150 * KB),
    "https://ex.com/big.png": ("image/png", png(1600, 1200), 300 * KB),
    "https://ex.com/thumb.webp": ("image/webp", webp_vp8x(128, 128), 70 * KB),
    "https://ex.com/bg.jpg": ("image/jpeg", jpeg(1920, 1080), 120 * KB),
}


class PageAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imgs = FakeImages(TABLE)
        cls.r = ia.run("https://ex.com/", page_fetcher=fake_page(HTML), image_fetcher=cls.imgs, delay=0)
        cls.images = {im["src"]: im for im in cls.r["pages"][0]["images"]}

    def codes(self, src):
        return {i["code"]: i["severity"] for i in self.images[src]["issues"]}

    def test_hero_detection_and_lazy(self):
        hero = self.images["/hero.jpg"]
        self.assertEqual(hero["role"], "hero")
        c = self.codes("/hero.jpg")
        self.assertEqual(c["hero_lazy"], "high")
        self.assertIn("hero_no_fetchpriority", c)
        self.assertEqual(c["image_over_budget"], "high")  # 820 KB > 700 KB
        self.assertEqual(c["legacy_format"], "medium")
        self.assertIn("no_srcset", c)
        self.assertEqual(hero["intrinsic"], [2400, 1200])

    def test_logo_is_not_hero(self):
        self.assertNotEqual(self.images["/logo.svg"]["role"], "hero")

    def test_picture_modern_source_not_flagged_legacy(self):
        c = self.codes("/team.jpg")
        self.assertNotIn("legacy_format", c)
        self.assertIn("empty_alt_check", c)
        self.assertTrue(any("picture" in g for g in self.images["/team.jpg"]["good"]))

    def test_oversized_and_filename_alt(self):
        c = self.codes("/big.png")
        self.assertIn("image_oversized", c)
        self.assertIn("alt_is_filename", c)
        self.assertIn("missing_dimensions", c)

    def test_thumbnail_budget(self):
        im = self.images["/thumb.webp"]
        self.assertEqual(im["role"], "thumbnail")
        self.assertEqual(self.codes("/thumb.webp").get("image_over_budget"), "medium")
        self.assertIn("webp", im["good"])

    def test_css_background_and_broken(self):
        self.assertEqual(self.images["/bg.jpg"]["kind"], "css-background")
        self.assertEqual(self.codes("/missing.png")["image_broken"], "high")

    def test_noscript_and_data_uri_not_fetched(self):
        self.assertEqual(self.imgs.calls.count("https://ex.com/hero.jpg"), 1)
        self.assertFalse(any(u.startswith("data:") for u in self.imgs.calls))

    def test_savings_and_markdown(self):
        top = self.r["top_savings"]
        self.assertEqual(top[0]["url"], "https://ex.com/hero.jpg")
        self.assertGreater(self.r["summary"]["estimated_savings_bytes"], 600 * KB)
        md = ia.to_markdown(self.r)
        self.assertIn("Top savings", md)
        self.assertIn("| # | Role |", md)

    def test_fetchpriority_marks_hero(self):
        html = '<img src="/a.jpg" alt="a photo of a cat" width="800" height="400"><img src="/b.webp" alt="Team planning board in the app" fetchpriority="high" width="800" height="400">'
        r = ia.run("https://ex.com/", page_fetcher=fake_page(html),
                   image_fetcher=FakeImages({"https://ex.com/b.webp": ("image/webp", webp_vp8x(800, 400), 90 * KB),
                                             "https://ex.com/a.jpg": ("image/jpeg", jpeg(800, 400), 40 * KB)}), delay=0)
        ims = r["pages"][0]["images"]
        self.assertEqual(ims[1]["role"], "hero")
        self.assertIn("fetchpriority=high", ims[1]["good"])
        self.assertEqual([i for i in ims[1]["issues"] if i["severity"] != "info"], [])


class FetchImageCap(unittest.TestCase):
    def test_blocked_scheme(self):
        r = ia.fetch_image("file:///etc/passwd")
        self.assertEqual(r["status"], 0)
        self.assertIn("scheme", r["error"])


if __name__ == "__main__":
    unittest.main()
