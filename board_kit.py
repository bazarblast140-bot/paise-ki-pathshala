"""Drawing kit for the 'Chart Board' Shorts template (1080x1920).

PIL draws every picture — candles, indicator lines, bars, payoff graphs,
timelines, rings, arrows — with Latin/number labels only. All Hindi text
(headlines, captions, thumbnail hook) is written later by libass through
ffmpeg, because Pillow only shapes Devanagari when libraqm is present and the
local Windows build does not have it; libass shapes it everywhere.

Layout (Shorts UI kept clear):
  y   0-170   YouTube top bar            -> background only
  y 165-1115  BOARD  x 25..1055          -> the picture; use the full safe width
  y 1140-1270 headline + sub (Hindi, ASS)
  y 1285-1405 captions (Hindi, ASS)
  y 1470+     CTA / disclaimer            -> below the educational card
"""
import math
import pathlib
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
BOARD = (25, 165, 1055, 1115)
BG_TOP, BG_BOT = (8, 13, 26), (10, 32, 30)
GREEN, RED, GRID = (34, 197, 94), (239, 68, 68), (40, 52, 72)
# Semantic trade markers: keep SL softly red and target/entry softly green.
SL_RED = (248, 150, 150)
TARGET_GREEN = (134, 239, 172)
INK, MUTED, YELLOW, BLUE = (226, 232, 240), (148, 163, 184), (250, 204, 21), (96, 165, 250)
PANEL = (6, 10, 20)

_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_font_cache = {}


def _has_rupee():
    """Screen par "Rs" ki jagah ₹ — sirf jab font me glyph ho (warna dabba dikhega)."""
    try:
        from fontTools.ttLib import TTFont
        for p in _FONTS:
            if pathlib.Path(p).exists():
                return 0x20B9 in TTFont(p, lazy=True).getBestCmap()
    except Exception:  # noqa: BLE001
        pass
    return False


RUPEE = _has_rupee()


def rs(text):
    return text.replace("Rs ", "₹").replace("Rs", "₹") if RUPEE and isinstance(text, str) else text


def font(size):
    if size not in _font_cache:
        for p in _FONTS:
            if pathlib.Path(p).exists():
                _font_cache[size] = ImageFont.truetype(p, size)
                break
        else:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


# ---------------------------------------------------------------- canvas
def background(top=BG_TOP, bot=BG_BOT):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3)))
    return img


def chip(img, text, x, y, col, ink=(10, 10, 10), size=32):
    d = ImageDraw.Draw(img)
    f = font(size)
    tw = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x + tw + 36, y + size + 22], (size + 22) // 2, fill=col)
    d.text((x + 18, y + 9), text, font=f, fill=ink)
    return x + tw + 36


def chips(img, category, step):
    x = chip(img, category, 60, 205, YELLOW)
    if step:
        chip(img, step, x + 18, 205, MUTED)


def panel(img, box=BOARD, radius=28):
    ImageDraw.Draw(img, "RGBA").rounded_rectangle(box, radius, fill=PANEL + (238,),
                                                  outline=(60, 80, 110), width=3)


# ---------------------------------------------------------------- mapping
class Axes:
    """Maps data (i, value) to pixels inside a box with padding."""

    def __init__(self, box, n, lo, hi, pad=(40, 40, 40, 40), ypad=0.12):
        self.x0, self.y0, self.x1, self.y1 = box
        self.pl, self.pt, self.pr, self.pb = pad
        span = (hi - lo) or 1.0
        self.lo, self.hi = lo - span * ypad, hi + span * ypad
        self.n = max(n, 1)
        self.step = (self.x1 - self.x0 - self.pl - self.pr) / self.n

    def x(self, i):
        return self.x0 + self.pl + self.step * (i + 0.5)

    def y(self, v):
        return self.y1 - self.pb - (v - self.lo) / (self.hi - self.lo) * (self.y1 - self.y0 - self.pt - self.pb)

    def grid(self, img, rows=5):
        d = ImageDraw.Draw(img)
        for k in range(1, rows + 1):
            gy = self.y0 + (self.y1 - self.y0) * k / (rows + 1)
            d.line([(self.x0 + 20, gy), (self.x1 - 20, gy)], fill=GRID, width=1)


# ---------------------------------------------------------------- primitives
def candles(img, box, ohlc, lo=None, hi=None, grid=True, width_frac=0.56, pad=None):
    lo = min(c[2] for c in ohlc) if lo is None else lo
    hi = max(c[1] for c in ohlc) if hi is None else hi
    ax = Axes(box, len(ohlc), lo, hi, pad=pad or (40, 40, 40, 40))
    if grid:
        ax.grid(img)
    d = ImageDraw.Draw(img)
    bw = max(10, int(ax.step * width_frac))
    for i, (o, h, l, c) in enumerate(ohlc):
        col = GREEN if c >= o else RED
        cx = ax.x(i)
        d.line([(cx, ax.y(h)), (cx, ax.y(l))], fill=col, width=max(3, bw // 6))
        top, bot = ax.y(max(o, c)), ax.y(min(o, c))
        d.rectangle([cx - bw / 2, top, cx + bw / 2, max(bot, top + 5)], fill=col)
    ax.bw = bw
    return ax


def polyline(img, pts, col, width=6):
    if len(pts) > 1:
        ImageDraw.Draw(img).line(pts, fill=col, width=width, joint="curve")


def dashed(img, x0, x1, y, col, label=None, size=32, label_side="right"):
    d = ImageDraw.Draw(img)
    x = x0
    while x < x1:
        d.line([(x, y), (min(x + 26, x1), y)], fill=col, width=5)
        x += 42
    if label:
        f = font(size)
        tw = d.textlength(label, font=f)
        lx = x1 - tw - 30 if label_side == "right" else x0
        d.rounded_rectangle([lx, y - size // 2 - 10, lx + tw + 30, y + size // 2 + 10], 10, fill=col)
        d.text((lx + 15, y - size // 2 - 4), label, font=f, fill=(10, 10, 10))


def ring(img, cx, cy, rx, ry, col=YELLOW):
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=col + (255,), width=14)
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    img.paste(glow, (0, 0), glow)
    ImageDraw.Draw(img).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=col, width=6)


def arrow(img, p1, p2, col, w=12, head=38):
    d = ImageDraw.Draw(img)
    d.line([p1, p2], fill=col, width=w)
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    for s in (+0.5, -0.5):
        d.line([p2, (p2[0] - head * math.cos(ang + s), p2[1] - head * math.sin(ang + s))], fill=col, width=w)


def label(img, x, y, text, col=INK, size=38, bg=None, anchor="la"):
    text = rs(text)
    d = ImageDraw.Draw(img)
    f = font(size)
    if bg is not None:
        b = d.textbbox((x, y), text, font=f, anchor=anchor)
        d.rounded_rectangle([b[0] - 14, b[1] - 10, b[2] + 14, b[3] + 10], 12, fill=bg)
    d.text((x, y), text, font=f, fill=col, anchor=anchor)


def fit_label(img, x, y, text, max_w, col=INK, size=38, min_size=22, anchor="la"):
    """Label that shrinks until it fits max_w — long English labels never run
    off the board."""
    text = rs(text)
    d = ImageDraw.Draw(img)
    while size > min_size and d.textlength(text, font=font(size)) > max_w:
        size -= 2
    d.text((x, y), text, font=font(size), fill=col, anchor=anchor)


def cross(img, cx, cy, r=34, col=RED, w=10):
    d = ImageDraw.Draw(img)
    d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=col, width=w)
    d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=col, width=w)


def tick(img, cx, cy, r=34, col=GREEN, w=10):
    ImageDraw.Draw(img).line([(cx - r, cy), (cx - r / 3, cy + r * 0.7), (cx + r, cy - r * 0.7)],
                             fill=col, width=w, joint="curve")


def bars(img, box, values, labels, colors, fmt="{:g}", lo=0.0, hi=None, value_size=44, label_size=34):
    """Vertical bars with value on top and label below (Latin/number only)."""
    hi = max(values) if hi is None else hi
    ax = Axes(box, len(values), lo, hi, pad=(50, 80, 50, 90), ypad=0.05)
    d = ImageDraw.Draw(img)
    bw = ax.step * 0.55
    base = ax.y(lo)
    for i, v in enumerate(values):
        cx = ax.x(i)
        top = ax.y(v)
        d.rounded_rectangle([cx - bw / 2, min(top, base - 4), cx + bw / 2, base], 10, fill=colors[i % len(colors)])
        d.text((cx, min(top, base) - 14), fmt.format(v), font=font(value_size), fill=INK, anchor="md")
        d.text((cx, base + 22), labels[i], font=font(label_size), fill=MUTED, anchor="ma")
    return ax


def series_panel(img, box, series, hlines=(), lo=None, hi=None, pad=(40, 30, 40, 30), grid=True):
    """series: list of (values, colour, width). hlines: list of (value, colour, label)."""
    vals = [v for s, _, _ in series for v in s if v is not None]
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    n = max(len(s) for s, _, _ in series)
    ax = Axes(box, n, lo, hi, pad=pad, ypad=0.06)
    if grid:
        ax.grid(img, rows=3)
    for v, col, lab in hlines:
        dashed(img, box[0] + 20, box[2] - 20, ax.y(v), col, lab, size=28)
    for s, col, w in series:
        polyline(img, [(ax.x(i), ax.y(v)) for i, v in enumerate(s) if v is not None], col, w)
    return ax


def big_text(img, box, lines):
    """lines: list of (text, size, colour) centred vertically inside box.
    Each line shrinks to fit the box width — CI (Linux) fonts are wider than
    local ones ("Rs 59.5 LAKH" spilled off the board in CI only)."""
    d = ImageDraw.Draw(img)
    room = (box[2] - box[0]) - 60
    fitted = []
    for text, size, col in lines:
        text = rs(text)
        while size > 24 and d.textlength(text, font=font(size)) > room:
            size -= 4
        fitted.append((text, size, col))
    lines = fitted
    total = sum(s * 1.25 for _, s, _ in lines)
    y = (box[1] + box[3]) / 2 - total / 2
    cx = (box[0] + box[2]) / 2
    for text, size, col in lines:
        d.text((cx, y), text, font=font(size), fill=col, anchor="ma")
        y += size * 1.25


def timeline(img, box, points, active=None, col=YELLOW):
    """points: list of (top_label, bottom_label). Horizontal line with dots."""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    y = (y0 + y1) / 2
    d.line([(x0 + 100, y), (x1 - 100, y)], fill=MUTED, width=12)
    n = len(points)
    for i, (top, bot) in enumerate(points):
        x = x0 + 100 + (x1 - x0 - 200) * (i / max(n - 1, 1))
        on = active is None or i in (active if isinstance(active, (list, tuple, set)) else [active])
        r = 38 if on else 26
        d.ellipse([x - r, y - r, x + r, y + r], fill=col if on else MUTED)
        # har label apni jagah (do dots ke beech) me fit — 4 points par bade font chipak jaate the
        room = (x1 - x0 - 200) / max(n - 1, 1) - 24 if n > 1 else x1 - x0
        for txt, yy, size, anc in ((top, y - 70, 64 if on else 50, "md"), (bot, y + 70, 40 if on else 34, "ma")):
            while size > 20 and d.textlength(txt, font=font(size)) > room:
                size -= 2
            d.text((x, yy), txt, font=font(size), fill=INK if on else MUTED, anchor=anc)


def payoff(img, box, xs, ys, strike=None, lo=None, hi=None, col=GREEN, neg_col=RED):
    """Option payoff at expiry. Positive part green, negative part red; zero line."""
    lo = min(ys) if lo is None else lo
    hi = max(ys) if hi is None else hi
    ax = Axes(box, len(xs), lo, hi, pad=(60, 60, 60, 60), ypad=0.1)
    ax.grid(img, rows=4)
    d = ImageDraw.Draw(img)
    zy = ax.y(0)
    d.line([(box[0] + 30, zy), (box[2] - 30, zy)], fill=INK, width=3)
    for i in range(len(xs) - 1):
        p1, p2 = (ax.x(i), ax.y(ys[i])), (ax.x(i + 1), ax.y(ys[i + 1]))
        d.line([p1, p2], fill=col if (ys[i] + ys[i + 1]) / 2 >= 0 else neg_col, width=9)
    if strike is not None:
        sx = ax.x(strike)
        d.line([(sx, box[1] + 40), (sx, box[3] - 40)], fill=MUTED, width=2)
    return ax


# ---------------------------------------------------------------- series math
def sma(v, n):
    out = [None] * len(v)
    for i in range(n - 1, len(v)):
        out[i] = sum(v[i - n + 1:i + 1]) / n
    return out


def ema(v, n):
    k, out, prev = 2 / (n + 1), [], None
    for x in v:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(v, n=14):
    out = [None] * len(v)
    gains, losses = [], []
    for i in range(1, len(v)):
        ch = v[i] - v[i - 1]
        gains.append(max(ch, 0)); losses.append(max(-ch, 0))
        if i >= n:
            g = sum(gains[-n:]) / n
            l_ = sum(losses[-n:]) / n
            out[i] = 100 - 100 / (1 + (g / l_ if l_ else 99))
    return out


def ohlc_from_closes(closes, wick=0.35, seed=0):
    import random
    rng = random.Random(seed)
    out, prev = [], closes[0]
    for c in closes:
        o = prev
        hi = max(o, c) + abs(c - o) * wick + rng.uniform(0.1, 0.5)
        lo = min(o, c) - abs(c - o) * wick - rng.uniform(0.1, 0.5)
        out.append((o, hi, lo, c))
        prev = c
    return out


# ---------------------------------------------------------------- libass text
ASS_HEAD = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
            "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: T,Noto Sans Devanagari,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,"
            "100,100,0,0,1,7,2,8,60,190,0,1\n"
            "Style: Cap,Noto Sans Devanagari,62,&H00FFFFFF,&H00FFFFFF,&H00101010,&HA0000000,-1,0,0,0,"
            "100,100,0,0,3,4,0,2,70,190,520,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
WHITE, YEL, REDC, GRN = "&H00FFFFFF&", "&H0015CCFA&", "&H004444EF&", "&H005EC522&"


def ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_text(t0, t1, x, y, size, colour, text, an=8, fade=True, wrap=False):
    """wrap=False adds \\q2: one line only. Headline/hook lines are sized to fit,
    so a wrap would only mean the next line lands on top of this one."""
    fx = "\\fad(150,100)" if fade else ""
    q = "" if wrap else "\\q2"
    return (f"Dialogue: 1,{ass_time(t0)},{ass_time(t1)},T,,0,0,0,,"
            f"{{\\an{an}\\pos({x},{y})\\fs{size}\\c{colour}{q}{fx}}}{text}")


def ass_caption(t0, t1, text, x=540, y=1376, size=40):
    """One spoken line in the subtitle band. y is the top of the line (an=8)."""
    return (f"Dialogue: 2,{ass_time(t0)},{ass_time(t1)},T,,0,0,0,,"
            f"{{\\an8\\pos({int(x)},{int(y)})\\fs{int(size)}\\c{WHITE}\\q2}}{text}")


def burn_text_on_image(base_png, out_path, events):
    """Hindi on a still (thumbnail): ffmpeg + libass, 1 frame."""
    base_png, out_path = pathlib.Path(base_png), pathlib.Path(out_path)
    ass = out_path.with_suffix(".ass")
    ass.write_text(ASS_HEAD + "\n".join(events) + "\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", base_png.name,
                    "-vf", f"ass={ass.name}", "-frames:v", "1", "-q:v", "2", out_path.name],
                   cwd=out_path.parent, check=True)
    return out_path
