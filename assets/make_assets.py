"""super-seo README artwork: comic-book superhero style (halftone, ink outlines, SFX bursts). CSS keyframes in SVG."""
import math
import sys
from html import escape
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

INK, PAPER, PAPER2 = "#141414", "#fff6df", "#ffe9b8"
YEL, RED, BLUE, CYAN, MAG, GRN, ORG, PUR = "#ffcf2e", "#ee3b34", "#2b7de9", "#19c2b0", "#e8437f", "#3fbf4f", "#ff8a1f", "#7b5cff"
HEAD = "Impact, 'Arial Black', 'Helvetica Neue', Arial, sans-serif"
BODY = "'Trebuchet MS', 'Segoe UI', Verdana, Arial, sans-serif"
MONO = "ui-monospace, Menlo, Consolas, 'Liberation Mono', monospace"


def p(t, T):
    return round(100 * t / T, 2)


class Anim:
    def __init__(self, T):
        self.T, self.rules, self.n = T, [], 0

    def _name(self):
        self.n += 1
        return f"a{self.n}"

    def raw(self, frames: list[tuple[float, str]], ease="linear", box=True):
        """frames: (time_seconds, css declarations). Returns class name."""
        name = self._name()
        body = "".join(f"{p(t, self.T)}%{{{d}}}" for t, d in frames)
        extra = "transform-box:fill-box;transform-origin:center;" if box else ""
        self.rules.append(f"@keyframes {name}{{{body}}}.{name}{{animation:{name} {self.T}s {ease} infinite;{extra}}}")
        return name

    def show(self, a, b, fade=0.25):
        T = self.T
        return self.raw([(0, "opacity:0"), (a, "opacity:0"), (a + fade, "opacity:1"), (b, "opacity:1"),
                         (min(b + fade, T), "opacity:0"), (T, "opacity:0")], box=False)

    def pop(self, a, b):
        """scale-in with overshoot, hold, scale-out."""
        T = self.T
        return self.raw([(0, "transform:scale(0);opacity:0"), (a, "transform:scale(0);opacity:0"),
                         (a + 0.18, "transform:scale(1.25);opacity:1"), (a + 0.32, "transform:scale(1);opacity:1"),
                         (b, "transform:scale(1);opacity:1"), (min(b + 0.2, T), "transform:scale(0);opacity:0"), (T, "transform:scale(0);opacity:0")])

    def css(self):
        return "\n".join(self.rules)


def defs():
    return f"""<defs>
<pattern id="dots" width="10" height="10" patternUnits="userSpaceOnUse"><circle cx="5" cy="5" r="1.7" fill="{INK}" fill-opacity="0.10"/></pattern>
<pattern id="dotsY" width="9" height="9" patternUnits="userSpaceOnUse"><rect width="9" height="9" fill="{YEL}"/><circle cx="4.5" cy="4.5" r="2.1" fill="{ORG}" fill-opacity="0.55"/></pattern>
<pattern id="dotsB" width="9" height="9" patternUnits="userSpaceOnUse"><rect width="9" height="9" fill="{BLUE}"/><circle cx="4.5" cy="4.5" r="2.2" fill="#1b4fa8" fill-opacity="0.6"/></pattern>
<pattern id="dotsR" width="9" height="9" patternUnits="userSpaceOnUse"><rect width="9" height="9" fill="{RED}"/><circle cx="4.5" cy="4.5" r="2.2" fill="#a8201b" fill-opacity="0.55"/></pattern>
<pattern id="dotsC" width="9" height="9" patternUnits="userSpaceOnUse"><rect width="9" height="9" fill="{CYAN}"/><circle cx="4.5" cy="4.5" r="2.1" fill="#0d7d72" fill-opacity="0.5"/></pattern>
</defs>"""


def frame(w, h, body, css, title, desc):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>
{defs()}
<style>
text{{font-family:{BODY}}} .h{{font-family:{HEAD};letter-spacing:.5px}} .m{{font-family:{MONO}}}
@keyframes bob{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-6px)}}}}
@keyframes blink{{0%,92%,100%{{transform:scaleY(1)}}95%{{transform:scaleY(.1)}}}}
@keyframes flap{{0%,100%{{transform:skewX(0deg) scaleX(1)}}50%{{transform:skewX(-7deg) scaleX(1.06)}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes jitter{{0%,100%{{transform:translate(0,0) rotate(0)}}25%{{transform:translate(1px,-1px) rotate(-1deg)}}75%{{transform:translate(-1px,1px) rotate(1deg)}}}}
.bob{{animation:bob 2.4s ease-in-out infinite;transform-box:fill-box}}
.eye{{animation:blink 4s infinite;transform-box:fill-box;transform-origin:center}}
.cape{{animation:flap .7s ease-in-out infinite;transform-box:fill-box;transform-origin:left center}}
.spin{{animation:spin 14s linear infinite;transform-box:fill-box;transform-origin:center}}
.jit{{animation:jitter .35s steps(2) infinite;transform-box:fill-box;transform-origin:center}}
{css}
@media (prefers-reduced-motion: reduce){{*{{animation:none!important}}.i0{{opacity:0!important}}.i1{{opacity:1!important;transform:none!important}}}}
</style>
{body}
</svg>"""


def burst(cx, cy, r, spikes=12, fill=YEL, inner=0.68, rot=0):
    pts = []
    for i in range(spikes * 2):
        a = math.pi * i / spikes + rot
        rr = r if i % 2 == 0 else r * inner
        pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{fill}" stroke="{INK}" stroke-width="4" stroke-linejoin="round"/>'


def sfx(cx, cy, text, cls, fill=YEL, color=RED, size=30, r=None, rot=-8):
    r = r or (len(text) * size * 0.64 / 2 + 14)
    return (f'<g class="{cls} i1">{burst(cx, cy, r, 14, fill, 0.72, rot / 50)}'
            f'<text class="h" x="{cx}" y="{cy + size * 0.36}" text-anchor="middle" font-size="{size}" fill="{color}" stroke="{INK}" '
            f'stroke-width="1.6" paint-order="stroke" transform="rotate({rot} {cx} {cy})">{escape(text)}</text></g>')


def bubble(x, y, w, h, text, tail="bl", size=15, cls="", fill="#fff"):
    tx = {"bl": (x + 24, y + h, x + 10, y + h + 22, x + 46, y + h),
          "br": (x + w - 46, y + h, x + w - 10, y + h + 22, x + w - 24, y + h),
          "tl": (x + 24, y, x + 12, y - 20, x + 48, y)}[tail]
    return (f'<g class="{cls}"><polygon points="{tx[0]},{tx[1]} {tx[2]},{tx[3]} {tx[4]},{tx[5]}" fill="{fill}" stroke="{INK}" stroke-width="3.5" stroke-linejoin="round"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}" fill="{fill}" stroke="{INK}" stroke-width="3.5"/>'
            f'<polygon points="{tx[0] + 3},{tx[1] - 2} {tx[4] - 3},{tx[5] - 2} {tx[0] + 3},{tx[1] + 3}" fill="{fill}"/>'
            f'<text x="{x + w / 2}" y="{y + h / 2 + size * 0.36}" text-anchor="middle" font-size="{size}" font-weight="700" fill="{INK}">{escape(text)}</text></g>')


def caption(x, y, w, text, size=15):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{size + 16}" fill="{YEL}" stroke="{INK}" stroke-width="3.5"/>'
            f'<text class="h" x="{x + 10}" y="{y + size + 7}" font-size="{size + 2}" fill="{INK}">{escape(text)}</text>')


def panel(points, fill="url(#dots)", bg=PAPER):
    pts = " ".join(f"{a},{b}" for a, b in points)
    sh = " ".join(f"{a + 7},{b + 7}" for a, b in points)
    return (f'<polygon points="{sh}" fill="{INK}"/><polygon points="{pts}" fill="{bg}"/>'
            f'<polygon points="{pts}" fill="{fill}" stroke="{INK}" stroke-width="5" stroke-linejoin="round"/>')


def site_card(x, y, w, label, rank, mood="sad", rank_color=RED, extra_cls=""):
    face = {"sad": f'<path d="M{x + w / 2 - 14} {y + 74} q14 -12 28 0" stroke="{INK}" stroke-width="4" fill="none" stroke-linecap="round"/>',
            "happy": f'<path d="M{x + w / 2 - 16} {y + 66} q16 16 32 0" stroke="{INK}" stroke-width="4" fill="none" stroke-linecap="round"/>'}[mood]
    return (f'<g class="{extra_cls}"><rect x="{x + 6}" y="{y + 6}" width="{w}" height="104" rx="10" fill="{INK}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="104" rx="10" fill="#fff" stroke="{INK}" stroke-width="4"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="22" rx="10" fill="{BLUE}" stroke="{INK}" stroke-width="4"/>'
            f'<rect x="{x + 2}" y="{y + 14}" width="{w - 4}" height="8" fill="{BLUE}"/>'
            f'<circle cx="{x + 14}" cy="{y + 11}" r="3.5" fill="#fff"/><circle cx="{x + 26}" cy="{y + 11}" r="3.5" fill="#fff"/>'
            f'<text class="m" x="{x + 38}" y="{y + 15}" font-size="11" fill="#fff" font-weight="700">{escape(label)}</text>'
            f'<circle cx="{x + w / 2 - 14}" cy="{y + 50}" r="5" fill="{INK}"/><circle cx="{x + w / 2 + 14}" cy="{y + 50}" r="5" fill="{INK}"/>'
            f'{face}</g>')


def hero(x, y, s=1.0, body=BLUE, cape=RED, emblem="S", emblem_fill=YEL, flying=False, cls=""):
    """Robot superhero mascot drawn around (x, y) at scale s. Emblem = short text glyph."""
    tilt = 'rotate(-18)' if flying else ''
    cape_path = ("M-30 -18 L-92 26 L-78 40 L-96 58 L-34 30 Z" if flying else "M-26 -20 L-46 70 L-24 60 L-8 74 L8 60 L24 72 L28 -20 Z")
    arms = (f'<path d="M24 -10 L62 -40" stroke="{INK}" stroke-width="13" stroke-linecap="round"/><path d="M24 -10 L62 -40" stroke="{body}" stroke-width="7" stroke-linecap="round"/>'
            f'<circle cx="66" cy="-43" r="10" fill="{body}" stroke="{INK}" stroke-width="4"/>'
            f'<path d="M-24 -6 L-48 14" stroke="{INK}" stroke-width="13" stroke-linecap="round"/><path d="M-24 -6 L-48 14" stroke="{body}" stroke-width="7" stroke-linecap="round"/>'
            if flying else
            f'<path d="M26 -8 L44 -42" stroke="{INK}" stroke-width="13" stroke-linecap="round"/><path d="M26 -8 L44 -42" stroke="{body}" stroke-width="7" stroke-linecap="round"/>'
            f'<circle cx="46" cy="-47" r="10" fill="{body}" stroke="{INK}" stroke-width="4"/>'
            f'<path d="M-26 -6 L-40 30" stroke="{INK}" stroke-width="13" stroke-linecap="round"/><path d="M-26 -6 L-40 30" stroke="{body}" stroke-width="7" stroke-linecap="round"/>')
    legs = ("" if flying else
            f'<rect x="-20" y="34" width="14" height="26" rx="5" fill="{body}" stroke="{INK}" stroke-width="4"/>'
            f'<rect x="6" y="34" width="14" height="26" rx="5" fill="{body}" stroke="{INK}" stroke-width="4"/>'
            f'<rect x="-24" y="56" width="20" height="10" rx="4" fill="{cape}" stroke="{INK}" stroke-width="4"/>'
            f'<rect x="4" y="56" width="20" height="10" rx="4" fill="{cape}" stroke="{INK}" stroke-width="4"/>')
    size = 22 if len(emblem) <= 1 else (15 if len(emblem) <= 2 else 12)
    return f"""<g class="{cls}" transform="translate({x} {y}) scale({s})"><g transform="{tilt}">
<g class="cape"><path d="{cape_path}" fill="{cape}" stroke="{INK}" stroke-width="4" stroke-linejoin="round"/></g>
{legs}{arms}
<rect x="-28" y="-26" width="56" height="64" rx="16" fill="{body}" stroke="{INK}" stroke-width="4"/>
<circle cx="0" cy="6" r="17" fill="{emblem_fill}" stroke="{INK}" stroke-width="4"/>
<text class="h" x="0" y="{6 + size * 0.36}" text-anchor="middle" font-size="{size}" fill="{INK}">{escape(emblem)}</text>
<line x1="0" y1="-66" x2="0" y2="-82" stroke="{INK}" stroke-width="4"/><circle cx="0" cy="-86" r="6" fill="{cape}" stroke="{INK}" stroke-width="3.5"/>
<rect x="-30" y="-70" width="60" height="46" rx="16" fill="#e9eef7" stroke="{INK}" stroke-width="4"/>
<rect x="-23" y="-60" width="46" height="22" rx="10" fill="{INK}"/>
<g class="eye"><rect x="-15" y="-55" width="9" height="11" rx="4" fill="{CYAN}"/><rect x="6" y="-55" width="9" height="11" rx="4" fill="{CYAN}"/></g>
<path d="M-30 -52 L-40 -46 L-30 -40 Z" fill="{cape}" stroke="{INK}" stroke-width="3"/><path d="M30 -52 L40 -46 L30 -40 Z" fill="{cape}" stroke="{INK}" stroke-width="3"/>
</g></g>"""


def speed_lines(x, y, n=7, length=150, angle=-18, cls=""):
    out = [f'<g class="{cls}" transform="rotate({angle} {x} {y})">']
    for i in range(n):
        yy = y - 50 + i * 16
        ln = length * (0.55 + 0.45 * ((i * 37) % 10) / 10)
        out.append(f'<line x1="{x - ln}" y1="{yy}" x2="{x - 10}" y2="{yy}" stroke="{INK}" stroke-width="{3 if i % 2 else 5}" stroke-linecap="round"/>')
    out.append("</g>")
    return "".join(out)


def ghost_bot(x, y, label, color):
    return (f'<g><rect x="{x - 26}" y="{y - 20}" width="52" height="40" rx="12" fill="{color}" stroke="{INK}" stroke-width="3.5"/>'
            f'<circle cx="{x - 9}" cy="{y - 4}" r="4" fill="{INK}"/><circle cx="{x + 9}" cy="{y - 4}" r="4" fill="{INK}"/>'
            f'<path d="M{x - 8} {y + 10} q8 -6 16 0" stroke="{INK}" stroke-width="3" fill="none"/>'
            f'<text x="{x}" y="{y + 36}" text-anchor="middle" font-size="11" font-weight="700" fill="{INK}">{escape(label)}</text></g>')


# =================================================================== HERO STRIP
def hero_strip():
    T = 12.0
    A = Anim(T)
    W, H = 1200, 500
    b = [f'<rect width="{W}" height="{H}" fill="{PAPER2}"/><rect width="{W}" height="{H}" fill="url(#dots)"/>']
    # title band
    b.append(f'<g class="spin" opacity="0.35">{burst(150, 70, 140, 24, "#ffd86b", 0.86)}</g>')
    b.append(f'<text class="h" x="36" y="96" font-size="86" fill="{RED}" stroke="{INK}" stroke-width="10" paint-order="stroke" transform="translate(6 6)">SUPER-SEO</text>')
    b.append(f'<text class="h" x="36" y="96" font-size="86" fill="{YEL}" stroke="{INK}" stroke-width="5" paint-order="stroke">SUPER-SEO</text>')
    b.append(f'<g transform="rotate(-2 700 62)"><rect x="520" y="34" width="640" height="58" fill="#fff" stroke="{INK}" stroke-width="4"/>'
             f'<text class="h" x="540" y="73" font-size="27" fill="{INK}">RANK IN GOOGLE. GET CITED BY AI.</text>'
             f'<text x="1144" y="84" text-anchor="end" font-size="13" font-weight="700" fill="{RED}">fixed in your code</text></g>')
    # panels
    P1 = [(24, 130), (392, 124), (380, 470), (24, 476)]
    P2 = [(406, 124), (800, 130), (792, 470), (394, 470)]
    P3 = [(814, 130), (1176, 124), (1176, 476), (806, 470)]
    b.append(panel(P1, "url(#dots)", "#e8f0ff"))
    b.append(panel(P2, "url(#dots)", "#fff4c7"))
    b.append(panel(P3, "url(#dots)", "#e6fbf2"))
    # ---- panel 1: invisible site
    b.append(caption(36, 142, 300, "MEANWHILE, ON PAGE 5 OF GOOGLE…", 13))
    b.append(f'<g class="jit">{site_card(70, 236, 150, "your-saas.com", "#47", "sad")}</g>')
    b.append(f'<text class="h" x="236" y="296" font-size="54" fill="{RED}" stroke="{INK}" stroke-width="3" paint-order="stroke">#47</text>')
    b.append(bubble(52, 186, 210, 34, "Nobody can find me!", "bl", 14))
    # 403 wall + bouncing bots
    b.append(f'<rect x="334" y="300" width="36" height="156" fill="url(#dotsR)" stroke="{INK}" stroke-width="4"/>'
             f'<text class="h" x="352" y="384" text-anchor="middle" font-size="18" fill="#fff" stroke="{INK}" stroke-width="2" paint-order="stroke" transform="rotate(-90 352 378)">403 BLOCKED</text>')
    for i, (lbl, col) in enumerate([("GPTBot", CYAN), ("ClaudeBot", ORG), ("PerplexityBot", MAG)]):
        bx, by = 64 + i * 80, 352 + i * 40
        dx = 334 - 30 - bx
        cls = A.raw([(0, "transform:translateX(0)"), (0.6 + i * 0.3, "transform:translateX(0)"), (1.0 + i * 0.3, f"transform:translateX({dx}px)"),
                     (1.25 + i * 0.3, f"transform:translateX({dx - 40}px) rotate(-16deg)"), (1.9 + i * 0.3, f"transform:translateX({dx}px)"),
                     (2.15 + i * 0.3, f"transform:translateX({dx - 40}px) rotate(12deg)"), (3.4, "transform:translateX(0)"), (T, "transform:translateX(0)")], ease="ease-in-out")
        b.append(f'<g class="{cls}">{ghost_bot(bx, by, lbl, col)}</g>')
    b.append(f'<g class="{A.pop(1.0, 3.4)} i0">{burst(300, 400, 28, 10, "#fff", 0.6)}<text class="h" x="300" y="407" text-anchor="middle" font-size="17" fill="{RED}">BONK!</text></g>')
    # ---- panel 2: hero arrives
    b.append(caption(418, 142, 260, "THEN SUPER-SEO ARRIVED!", 13))
    for k, (line, yy, t0) in enumerate([("+ export const metadata", 206, 4.2), ("+ <JsonLd data={graph} />", 232, 4.9), ("+ allow: OAI-SearchBot", 258, 5.6)]):
        cls = A.raw([(0, "opacity:0;transform:translateX(-30px)"), (t0, "opacity:0;transform:translateX(-30px)"), (t0 + 0.3, "opacity:1;transform:translateX(0)"),
                     (10.6, "opacity:1;transform:translateX(0)"), (10.9, "opacity:0"), (T, "opacity:0")], box=False)
        b.append(f'<g class="{cls} i1"><rect x="420" y="{yy - 15}" width="186" height="22" rx="4" fill="{INK}"/>'
                 f'<text class="m" x="428" y="{yy}" font-size="11" fill="#7CFC8A">{escape(line)}</text></g>')
    for j, (txt, cx, cy, fill, col, t0, size) in enumerate([("SSR!", 744, 318, YEL, RED, 4.0, 22), ("SCHEMA!", 728, 214, "#fff", BLUE, 4.7, 17),
                                                            ("SITEMAP!", 720, 414, YEL, RED, 5.4, 15), ("UNBLOCK!", 482, 414, "#fff", GRN, 6.1, 15)]):
        b.append(sfx(cx, cy, txt, A.pop(t0, 10.6), fill, col, size))
    fly = A.raw([(0, "transform:translate(-260px,-120px) scale(.5);opacity:0"), (3.0, "transform:translate(-260px,-120px) scale(.5);opacity:0"),
                 (3.7, "transform:translate(0,0) scale(1);opacity:1"), (10.6, "transform:translate(0,0) scale(1);opacity:1"),
                 (11.2, "transform:translate(360px,-160px) scale(.6);opacity:0"), (T, "transform:translate(360px,-160px) scale(.6);opacity:0")], ease="cubic-bezier(.2,.9,.3,1.2)", box=False)
    b.append(f'<g class="{fly} i1">{speed_lines(570, 350, 7, 150, -18)}{hero(604, 344, 0.95, BLUE, RED, "S", YEL, flying=True)}</g>')
    # ---- panel 3: #1 and cited
    b.append(caption(826, 142, 250, "NOW EVERYONE CITES IT.", 13))
    climb = A.raw([(0, "transform:translateY(60px);opacity:0"), (6.6, "transform:translateY(60px);opacity:0"), (6.8, "transform:translateY(50px);opacity:1"),
                   (7.4, "transform:translateY(-8px);opacity:1"), (7.6, "transform:translateY(0);opacity:1"), (11.2, "transform:translateY(0);opacity:1"),
                   (11.5, "transform:translateY(0);opacity:0"), (T, "transform:translateY(60px);opacity:0")],
                  ease="cubic-bezier(.2,.9,.3,1.3)", box=False)
    b.append(f'<rect x="874" y="404" width="170" height="52" fill="url(#dotsY)" stroke="{INK}" stroke-width="4"/>'
             f'<text class="h" x="959" y="442" text-anchor="middle" font-size="32" fill="{INK}">#1</text>')
    b.append(f'<g class="{climb} i1">{site_card(884, 288, 150, "your-saas.com", "#1", "happy")}</g>')
    b.append(f'<g class="{A.pop(7.6, 11.2)} i1">{burst(1112, 404, 40, 12, YEL, 0.62)}<text class="h" x="1112" y="412" text-anchor="middle" font-size="22" fill="{RED}">#1!</text></g>')
    for m, (eng, bx, by, col, t0) in enumerate([("ChatGPT: cited", 826, 190, CYAN, 8.0), ("Perplexity: cited", 1004, 190, MAG, 8.4),
                                               ("Gemini: cited", 826, 240, YEL, 8.8), ("Claude: cited", 1048, 240, ORG, 9.2), ("Copilot: cited", 1048, 300, GRN, 9.6)]):
        b.append(f'<g class="{A.pop(t0, 11.2)} i1">{bubble(bx, by, (114 if bx > 1040 else 124) if len(eng) < 15 else 146, 30, eng, "bl" if m % 2 == 0 else "br", 13, fill=col if col != YEL else "#fff4b0")}</g>')
    # confetti
    for c in range(14):
        cx, cy = 830 + (c * 53) % 330, 160 + (c * 37) % 60
        col = [RED, BLUE, YEL, CYAN, MAG, GRN][c % 6]
        cls = A.raw([(0, "opacity:0;transform:translateY(0) rotate(0)"), (7.8, "opacity:0;transform:translateY(0) rotate(0)"),
                     (8.0, "opacity:1"), (11.0, f"opacity:1;transform:translateY({180 + (c % 4) * 40}px) rotate({(c % 2 * 2 - 1) * 260}deg)"),
                     (11.3, "opacity:0"), (T, "opacity:0")])
        b.append(f'<rect class="{cls} i0" x="{cx}" y="{cy}" width="9" height="14" fill="{col}" stroke="{INK}" stroke-width="1.5"/>')
    # footer strip
    b.append(f'<text x="600" y="494" text-anchor="middle" font-size="12" font-weight="700" fill="{INK}" opacity="0.7">A CLAUDE CODE SKILL · CODE-FIRST SEO + GEO FOR SAAS</text>')
    svg = frame(W, H, "\n".join(b), A.css(), "super-seo: rank in Google, get cited by AI",
                "A three-panel comic. Panel 1: a sad SaaS website sits at #47 on page 5 of Google while AI crawler bots bounce off a "
                "403 wall. Panel 2: the super-seo robot hero flies in and fixes things with SSR, schema, sitemap and unblocking bots, "
                "as code patches appear. Panel 3: the happy website rises to #1 and ChatGPT, Perplexity, Gemini, Claude and Copilot cite it.")
    (OUT / "hero.svg").write_text(svg, encoding="utf-8")


# =================================================================== SQUAD
SQUAD = [
    ("CODE", "</>", BLUE, YEL, "Reads your routes, metadata and leaks"),
    ("TECHNICAL", "⚙", "#5b6b7f", ORG, "Crawls, canonicals, sitemaps, drift"),
    ("GEO", "AI", PUR, CYAN, "Unblocks AI bots, earns citations"),
    ("CONTENT", "✎", MAG, YEL, "Answer-first pages that convert"),
    ("SCHEMA", "{ }", GRN, "#fff", "Valid JSON-LD for every page"),
    ("SPEED", "⚡", ORG, YEL, "Core Web Vitals, render, images"),
    ("RIVALS", "◎", RED, "#fff", "Competitors, SERPs, prospects"),
    ("FIXER", "🔧", CYAN, YEL, "Ships every fix as code"),
]


def squad():
    T = 10.0
    A = Anim(T)
    W, H = 1200, 430
    b = [f'<rect width="{W}" height="{H}" fill="{PAPER2}"/><rect width="{W}" height="{H}" fill="url(#dots)"/>']
    b.append(panel([(20, 20), (1180, 16), (1176, 410), (24, 414)], "url(#dots)", "#fff8e6"))
    b.append(f'<text class="h" x="600" y="78" text-anchor="middle" font-size="48" fill="{YEL}" stroke="{INK}" stroke-width="7" paint-order="stroke">THE SUPER-SEO SQUAD</text>')
    b.append(f'<text x="600" y="104" text-anchor="middle" font-size="15" font-weight="700" fill="{INK}">8 specialist agents. One command. They work in parallel.</text>')
    n = len(SQUAD)
    gap = 1120 / n
    for i, (name, emb, body, emb_fill, power) in enumerate(SQUAD):
        x = 40 + gap * i + gap / 2
        t0 = 0.3 + i * 0.45
        popc = A.pop(t0, 9.4)
        b.append(f'<g class="{popc} i1">{burst(x, 214, 56, 12, ["#fff", YEL][i % 2], 0.74, i * 0.2)}</g>')
        emb_txt = emb if emb != "🔧" else "FIX"
        b.append(f'<g class="{A.pop(t0 + 0.1, 9.4)} i1"><g class="bob" style="animation-delay:-{i * 0.3:.1f}s">{hero(x, 238, 0.82, body, RED if body != RED else BLUE, emb_txt, emb_fill)}</g></g>')
        b.append(f'<g class="{A.show(t0 + 0.25, 9.4)} i1"><rect x="{x - 62}" y="320" width="124" height="30" fill="{body}" stroke="{INK}" stroke-width="3.5" transform="rotate({(-1) ** i * 2} {x} 335)"/>'
                 f'<text class="h" x="{x}" y="342" text-anchor="middle" font-size="19" fill="#fff" stroke="{INK}" stroke-width="2.4" paint-order="stroke">{name}</text>'
                 f'<foreignObject x="{x - 66}" y="356" width="132" height="44"></foreignObject>'
                 + "".join(f'<text x="{x}" y="{370 + k * 15}" text-anchor="middle" font-size="12" font-weight="700" fill="{INK}">{escape(line)}</text>'
                           for k, line in enumerate(wrap(power, 20))) + "</g>")
    b.append(f'<g class="{A.pop(4.2, 9.4)} i1">{burst(1110, 70, 46, 14, RED, 0.68)}<text class="h" x="1110" y="79" text-anchor="middle" font-size="22" fill="#fff" stroke="{INK}" stroke-width="2" paint-order="stroke">x8!</text></g>')
    svg = frame(W, H, "\n".join(b), A.css(), "The super-seo squad",
                "Eight robot superheroes pop in one by one: Code, Technical, GEO, Content, Schema, Speed, Rivals and Fixer, each with "
                "its emblem and power, representing the specialist agents that run in parallel.")
    (OUT / "squad.svg").write_text(svg, encoding="utf-8")


def wrap(s, n):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return lines[:2]


# =================================================================== MISSION (flow)
def mission():
    T = 9.0
    A = Anim(T)
    W, H = 1200, 300
    b = [f'<rect width="{W}" height="{H}" fill="{PAPER2}"/><rect width="{W}" height="{H}" fill="url(#dots)"/>']
    steps = [("SCAN", "the code", "framework, routes, leaks", BLUE, "url(#dotsB)"),
             ("CRAWL", "localhost", "before you deploy", ORG, "url(#dotsY)"),
             ("UNBLOCK", "AI bots", "robots, WAF, llms.txt", PUR, None),
             ("FIX", "in code", "metadata, schema, SSR", GRN, "url(#dotsC)"),
             ("GUARD", "releases", "CI gate + drift", RED, "url(#dotsR)")]
    w, gap = 214, 18
    x0 = (W - (5 * w + 4 * gap)) / 2
    b.append(f'<text class="h" x="{W / 2}" y="44" text-anchor="middle" font-size="30" fill="{INK}">THE MISSION, IN FIVE PANELS</text>')
    for i, (big, small, sub, col, pat) in enumerate(steps):
        x = x0 + i * (w + gap)
        sk = (-1) ** i * 3
        pts = [(x, 70 + sk), (x + w, 70 - sk), (x + w - 4, 262), (x + 4, 262)]
        b.append(panel(pts, "url(#dots)", "#fffdf5"))
        t0 = 0.3 + i * 1.3
        b.append(f'<g class="{A.pop(t0, 8.4)} i1">{burst(x + 38, 104, 26, 10, col if pat is None else YEL, 0.7)}'
                 f'<text class="h" x="{x + 38}" y="112" text-anchor="middle" font-size="22" fill="{INK}">{i + 1}</text></g>')
        b.append(f'<text class="h" x="{x + 72}" y="116" font-size="34" fill="{col}" stroke="{INK}" stroke-width="3" paint-order="stroke">{big}</text>')
        b.append(f'<text class="h" x="{x + 18}" y="170" font-size="26" fill="{INK}">{small.upper()}</text>')
        b.append(f'<text x="{x + 18}" y="204" font-size="14" font-weight="700" fill="{INK}" opacity="0.8">{escape(sub)}</text>')
        hl = A.raw([(0, "opacity:0"), (t0, "opacity:0"), (t0 + 0.2, "opacity:1"), (t0 + 1.3, "opacity:1"), (t0 + 1.5, "opacity:0"), (T, "opacity:0")], box=False)
        b.append(f'<polygon class="{hl}" points="{" ".join(f"{a},{c}" for a, c in pts)}" fill="none" stroke="{col}" stroke-width="8" stroke-dasharray="14 8" opacity="0"/>')
        if i < 4:
            ax = x + w + gap / 2
            b.append(f'<polygon points="{ax - 8},160 {ax + 8},170 {ax - 8},180" fill="{YEL}" stroke="{INK}" stroke-width="3"/>')
    b.append(f'<text class="m" x="{W / 2}" y="290" text-anchor="middle" font-size="13" fill="{INK}">/super-seo   →   all five, automatically</text>')
    svg = frame(W, H, "\n".join(b), A.css(), "How super-seo works",
                "Five comic panels light up in turn: scan the code, crawl localhost before deploy, unblock AI bots, fix in code, guard releases.")
    (OUT / "flow.svg").write_text(svg, encoding="utf-8")


# =================================================================== TERMINAL (comic framed)
def terminal():
    T = 13.0
    A = Anim(T)
    W, H = 1200, 470
    b = [f'<rect width="{W}" height="{H}" fill="{PAPER2}"/><rect width="{W}" height="{H}" fill="url(#dots)"/>']
    b.append(f'<rect x="34" y="40" width="1132" height="400" rx="14" fill="{INK}"/>')
    b.append(f'<rect x="26" y="32" width="1132" height="400" rx="14" fill="#10131c" stroke="{INK}" stroke-width="5"/>')
    b.append(f'<rect x="26" y="32" width="1132" height="38" rx="14" fill="{YEL}" stroke="{INK}" stroke-width="5"/><rect x="31" y="56" width="1122" height="12" fill="{YEL}"/>')
    for k, c in enumerate([RED, ORG, GRN]):
        b.append(f'<circle cx="{52 + k * 22}" cy="51" r="7" fill="{c}" stroke="{INK}" stroke-width="3"/>')
    b.append(f'<text class="h" x="600" y="60" text-anchor="middle" font-size="20" fill="{INK}">MISSION LOG · /super-seo</text>')
    lines = [
        (0.6, "#7CFC8A", "$ claude  →  /super-seo"),
        (1.4, "#9aa4b8", "▸ baseline   audit.py · nextjs · 42 routes · 38 pages crawled"),
        (2.2, "#7fd8ff", "▸ squad      code · technical · geo · content · schema · speed · rivals   (parallel)"),
        (3.2, "#ff7b72", "✖ critical   /pricing renders prices client-side (AI bots see none)"),
        (3.7, "#ffb86b", "▲ high       metadataBase missing · 6 routes inherit one title"),
        (4.2, "#ffb86b", "▲ high       Cloudflare managed robots blocks GPTBot, ClaudeBot"),
        (4.7, "#ffe66d", "● medium     no SoftwareApplication schema · 3 orphan pages"),
        (5.6, "#7fd8ff", "▸ fixer ×3   12 files changed · build ok · re-crawl ok"),
    ]
    for t0, col, txt in lines:
        y = 102 + lines.index((t0, col, txt)) * 30
        cls = A.raw([(0, "opacity:0;transform:translateX(-14px)"), (t0, "opacity:0;transform:translateX(-14px)"), (t0 + 0.25, "opacity:1;transform:translateX(0)"),
                     (12.4, "opacity:1;transform:translateX(0)"), (12.7, "opacity:0"), (T, "opacity:0")], box=False)
        b.append(f'<text class="m {cls} i1" x="56" y="{y}" font-size="15" fill="{col}">{escape(txt)}</text>')
    # before / after gauge
    b.append(f'<text class="h" x="56" y="378" font-size="20" fill="#fff">READINESS</text>')
    b.append(f'<rect x="180" y="358" width="600" height="26" rx="6" fill="#262b3a" stroke="#000" stroke-width="2"/>')
    grow = A.raw([(0, "transform:scaleX(.42)"), (6.2, "transform:scaleX(.42)"), (7.4, "transform:scaleX(.95)"), (12.4, "transform:scaleX(.95)"),
                  (12.7, "transform:scaleX(.42)"), (T, "transform:scaleX(.42)")], ease="cubic-bezier(.2,.9,.3,1.2)", box=False)
    b.append(f'<rect class="{grow} i1" style="transform-box:fill-box;transform-origin:left center" x="180" y="358" width="600" height="26" rx="6" fill="url(#dotsY)" stroke="{INK}" stroke-width="3"/>')
    b.append(f'<g class="{A.show(0, 6.6)} i0"><text class="h" x="800" y="380" font-size="26" fill="#ff7b72">42</text></g>')
    b.append(f'<g class="{A.pop(7.4, 12.4)} i1">{burst(870, 368, 46, 14, YEL, 0.7)}<text class="h" x="870" y="378" text-anchor="middle" font-size="28" fill="{RED}">95!</text></g>')
    b.append(f'<g class="{A.pop(8.0, 12.4)} i1">{bubble(940, 326, 200, 40, "0 criticals left", "bl", 15, fill="#c9ffd2")}</g>')
    b.append(f'<text x="56" y="418" font-size="12" fill="#9aa4b8">illustrative run · report saved to .seo/reports/</text>')
    svg = frame(W, H, "\n".join(b), A.css(), "A super-seo run",
                "A comic-framed terminal: /super-seo runs a baseline, sends the specialist squad in parallel, lists critical, high and "
                "medium findings, the fixer agents change 12 files, and readiness jumps from 42 to 95 with zero criticals left.")
    (OUT / "terminal.svg").write_text(svg, encoding="utf-8")



