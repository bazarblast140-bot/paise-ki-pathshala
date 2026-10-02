"""Full-screen 9:16 scenes. ATR is seven different cards, not one zoomed chart.

The candlestick chart is only the problem scene and the wrong-vs-right split.
Other scenes are type, a formula, a price ladder, a position-size card, and
a takeaway. Every line of text is one Raqm-shaped sprite. The plate behind it
may slide or wipe; the sprite is only faded as a whole layer.

Labels are recorded so the 6% safe-area gate can reject a crop.
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import board_kit as K
import board_scenes as S
import market_data
import safe_area

_FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"
_font_cache = {}
_BG = None
_SHADOWS = {}
CHART_KINDS = ("problem", "split")
CARD = (72, 188, 1008, 1048)
NAVY = (12, 18, 32)
GOLD = (212, 168, 72)
PROGRESS = (72, 1768, 1008, 1776)


def _font(size):
    size = int(size)
    if size not in _font_cache:
        if os.path.exists(_FONT):
            _font_cache[size] = ImageFont.truetype(_FONT, size)
        else:
            _font_cache[size] = K.font(size)
    return _font_cache[size]


def kinds_for(beat, n):
    """Scene types for one beat. The chart appears in at most two problem frames plus one split."""
    n = max(1, int(n))
    if beat == "hook":
        if n == 1:
            return ["hook"]
        problems = min(2, n - 1)
        return ["hook"] * (n - problems) + ["problem"] * problems
    if beat == "pehchan":
        return ["formula"] * n
    if beat == "matlab":
        return ["ladder"] * n
    if beat == "karein":
        if n == 1:
            return ["cta"]
        if n == 2:
            return ["split", "cta"]
        if n == 3:
            return ["split", "size", "cta"]
        return ["split", "size", "takeaway"] + ["cta"] * (n - 3)
    return ["card"] * n


def zoom_at(img, fx, fy, scale=0.7, shift_x=0):
    """Crop around a point and scale back to the full frame. scale < 1 zooms in."""
    w, h = img.size
    cw = max(2, int(w * scale))
    ch = max(2, int(h * scale))
    cw -= cw % 2
    ch -= ch % 2
    x0 = int(max(0, min(w - cw, fx + shift_x - cw / 2)))
    y0 = int(max(0, min(h - ch, fy - ch / 2)))
    crop = img.crop((x0, y0, x0 + cw, y0 + ch))
    return crop.resize((w, h), Image.Resampling.LANCZOS)


def screen_callout(img, text, x=160, y=250):
    out = img.copy()
    K.arrow(out, (860, y + 40), (560, y + 180), K.YELLOW, 8, 28)
    K.label(out, x, y, text, K.YELLOW, 34, bg=(8, 12, 24))
    return out


def _ease(t):
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def _terminal_bg():
    """Deep navy gradient, a faint grid, and a vignette. Cached."""
    global _BG
    if _BG is None:
        h, w = K.H, K.W
        ys = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        top = np.array([7, 12, 24], dtype=np.float32)
        bot = np.array([4, 8, 16], dtype=np.float32)
        rgb = np.repeat((top + (bot - top) * ys)[:, None, :], w, axis=1)
        cy = (np.linspace(-1.0, 1.0, h, dtype=np.float32) ** 2)[:, None]
        cx = np.linspace(-1.0, 1.0, w, dtype=np.float32) ** 2
        vig = 1.0 - np.clip(np.sqrt(cy + cx[None, :]) * 0.55, 0.0, 0.62)
        rgb = rgb * vig[..., None]
        img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
        draw = ImageDraw.Draw(img)
        grid = (18, 28, 42)
        for x in range(0, w, 54):
            draw.line([(x, 0), (x, h)], fill=grid, width=1)
        for y in range(0, h, 54):
            draw.line([(0, y), (w, y)], fill=grid, width=1)
        _BG = img
    return _BG.copy()


def _blank():
    img = _terminal_bg()
    img.label_boxes = []
    img.text_layers = []
    img.plate = None
    img.scene_kind = ""
    return img


def _card(img, box=CARD):
    """Soft shadow, rounded navy fill, thin gold edge."""
    x0, y0, x1, y1 = box
    key = tuple(int(v) for v in box)
    if key not in _SHADOWS:
        pad = 36
        sx0, sy0 = x0 - pad, y0 - pad
        sx1, sy1 = x1 + pad, y1 + pad + 14
        shadow = Image.new("RGBA", (sx1 - sx0, sy1 - sy0), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [pad + 8, pad + 14, pad + (x1 - x0) + 6, pad + (y1 - y0) + 16],
            28, fill=(0, 0, 0, 130),
        )
        _SHADOWS[key] = (sx0, sy0, shadow.filter(ImageFilter.GaussianBlur(14)))
    sx0, sy0, shadow = _SHADOWS[key]
    img.paste(shadow, (sx0, sy0), shadow)
    ImageDraw.Draw(img).rounded_rectangle(box, 26, fill=NAVY, outline=GOLD, width=2)


def _seal(img):
    """Snapshot geometry. Later text is a sprite, not more pixels in the plate."""
    if getattr(img, "plate", None) is None:
        img.plate = img.copy()
    if getattr(img, "text_layers", None) is None:
        img.text_layers = []
    if getattr(img, "label_boxes", None) is None:
        img.label_boxes = []


def _text(img, x, y, text, fill, size, anchor="lt", persist=False, band=True, alpha=255, faint=False):
    """Draw one whole shaped line and record its padded box.

    One ImageDraw.text call is one HarfBuzz/Raqm sprite. Call this after the
    candles, rules, and card are already on the plate.
    """
    if not text:
        return None
    _seal(img)
    left, top, right, bottom = safe_area.limits()
    if band:
        top = max(top, safe_area.CONTENT_TOP)
        bottom = min(bottom, safe_area.CONTENT_BOTTOM)
    pad = 6
    size = int(size)
    x, y = float(x), float(y)
    probe = ImageDraw.Draw(img)
    while size >= 16:
        font = _font(size)
        box = probe.textbbox((x, y), text, font=font, anchor=anchor)
        rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        dx = dy = 0.0
        if rect[0] < left:
            dx = left - rect[0]
        elif rect[2] > right:
            dx = right - rect[2]
        if rect[1] < top:
            dy = top - rect[1]
        elif rect[3] > bottom:
            dy = bottom - rect[3]
        if dx or dy:
            x += dx
            y += dy
            box = probe.textbbox((x, y), text, font=font, anchor=anchor)
            rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        fits = (
            rect[0] >= left - 0.2 and rect[2] <= right + 0.2
            and rect[1] >= top - 0.2 and rect[3] <= bottom + 0.2
        )
        if fits:
            break
        size -= 2
    font = _font(max(size, 16))
    box = probe.textbbox((x, y), text, font=font, anchor=anchor)
    bleed = 4
    x0 = int(math.floor(box[0])) - bleed
    y0 = int(math.floor(box[1])) - bleed
    x1 = int(math.ceil(box[2])) + bleed
    y1 = int(math.ceil(box[3])) + bleed
    sprite = Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0))
    fill_rgba = tuple(int(c) for c in fill) + (int(alpha),)
    ImageDraw.Draw(sprite).text((x - x0, y - y0), text, font=font, fill=fill_rgba, anchor=anchor)
    img.paste(sprite, (x0, y0), sprite)
    rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    img.label_boxes.append(tuple(rect))
    img.text_layers.append({
        "image": sprite,
        "xy": (x0, y0),
        "text": text,
        "shaped": True,
        "box": tuple(float(v) for v in box),
        "size": int(font.size),
        "persist": bool(persist),
        "faint": bool(faint or alpha < 80),
    })
    return rect


def _glow_line(img, x0, y, x1, color):
    """One bitmap for the active level: a soft halo and a solid stroke."""
    _seal(img)
    pad = 18
    length = max(2, int(round(x1 - x0)))
    sprite = Image.new("RGBA", (length + pad * 2, pad * 2), (0, 0, 0, 0))
    cy = pad
    tone = tuple(int(c) for c in color)
    ImageDraw.Draw(sprite).line([(pad, cy), (pad + length, cy)], fill=tone + (150,), width=14)
    sprite = sprite.filter(ImageFilter.GaussianBlur(6))
    ImageDraw.Draw(sprite).line([(pad, cy), (pad + length, cy)], fill=tone + (255,), width=4)
    xy = (int(round(x0)) - pad, int(round(y)) - pad)
    img.paste(sprite, xy, sprite)
    img.text_layers.append({
        "image": sprite,
        "xy": xy,
        "text": "",
        "shaped": True,
        "glow": True,
        "box": (xy[0], xy[1], xy[0] + sprite.size[0], xy[1] + sprite.size[1]),
        "size": 0,
        "persist": False,
        "faint": False,
    })


def _brand(img):
    """Channel mark in the brand band, under the subtitles and above the progress bar."""
    _text(img, 88, safe_area.BRAND_Y, "PKP", GOLD, 26, anchor="lt", persist=True, band=False)


def paint_progress(img, frac):
    """Bottom bar. Geometry only, so a wipe cannot turn it into letter fragments."""
    frac = max(0.0, min(1.0, float(frac)))
    x0, y0, x1, y1 = PROGRESS
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x0, y0, x1, y1], 4, fill=(28, 36, 52))
    fill_x = x0 + (x1 - x0) * frac
    if fill_x > x0 + 4:
        draw.rounded_rectangle([x0, y0, fill_x, y1], 4, fill=GOLD)


def set_progress(img, frac):
    """Paint the bar on the still and on the plate the transition moves."""
    paint_progress(img, frac)
    plate = getattr(img, "plate", None)
    if plate is not None and plate is not img:
        paint_progress(plate, frac)
    img.progress = max(0.0, min(1.0, float(frac)))


def _finish(img, kind):
    _brand(img)
    img.scene_kind = kind
    _seal(img)
    return img


def _source(img, lesson):
    """Source line shares the brand band. It does not sit in the subtitle band."""
    _text(
        img, 540, safe_area.SOURCE_Y, f"Yahoo Finance   {lesson['as_of']}",
        K.MUTED, 26, anchor="mt", band=False,
    )


def _ohlc(bars):
    return [
        (float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))
        for b in bars
    ]


def _plot_grid(img, box, rows=4):
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    color = (32, 44, 64)
    for k in range(1, rows):
        y = y0 + (y1 - y0) * k / rows
        draw.line([(x0 + 8, y), (x1 - 8, y)], fill=color, width=1)
    draw.line([(x1 - 1, y0), (x1 - 1, y1)], fill=(48, 60, 82), width=1)


def _candles(img, box, bars, lo, hi, count):
    """Thin wicks, green/red bodies, and a volume strip under the price plot."""
    ohlc = _ohlc(bars)
    vols = [float(b.get("volume") or 0.0) for b in bars]
    count = max(1, min(int(count), len(ohlc)))
    _plot_grid(img, box, 4)
    ax = K.Axes(box, len(ohlc), lo, hi, pad=(18, 14, 14, 16), ypad=0.08)
    draw = ImageDraw.Draw(img)
    bw = max(5, int(ax.step * 0.42))
    for i, (o, high, low, c) in enumerate(ohlc[:count]):
        col = K.GREEN if c >= o else K.RED
        cx = ax.x(i)
        draw.line([(cx, ax.y(high)), (cx, ax.y(low))], fill=col, width=2)
        top, bot = ax.y(max(o, c)), ax.y(min(o, c))
        if bot < top + 2:
            bot = top + 2
        draw.rectangle([cx - bw / 2, top, cx + bw / 2, bot], fill=col)
    vmax = max(vols[:count] or [1.0]) or 1.0
    vy0, vy1 = box[3] + 8, box[3] + 44
    for i in range(count):
        o, _, _, c = ohlc[i]
        col = tuple(int(ch * 0.5) for ch in (K.GREEN if c >= o else K.RED))
        cx = ax.x(i)
        bh = (vols[i] / vmax) * (vy1 - vy0)
        draw.rectangle([cx - bw / 2, vy1 - bh, cx + bw / 2, vy1], fill=col)
    return ax


def _axis_labels(img, ax, steps=4):
    for k in range(steps + 1):
        price = ax.lo + (ax.hi - ax.lo) * k / steps
        y = ax.y(price)
        if y < ax.y0 + 4 or y > ax.y1 - 4:
            continue
        _text(img, ax.x1 + 14, y, f"{price:.1f}", K.MUTED, 20, anchor="lm")


def _watermark(img, box):
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    _text(img, cx, cy, "PKP", GOLD, 64, anchor="mm", alpha=42, faint=True)


def _rule(img, ax, price, color, frac=1.0):
    draw = ImageDraw.Draw(img)
    y = ax.y(price)
    x1 = ax.x0 + 8 + (ax.x1 - ax.x0 - 16) * max(0.08, min(1.0, frac))
    draw.line([(ax.x0 + 8, y), (x1, y)], fill=color, width=3)


def _hook(lesson):
    img = _blank()
    _card(img)
    ImageDraw.Draw(img).rectangle([220, 300, 860, 312], fill=K.YELLOW)
    _text(img, 540, 360, "तंग एसएल", K.YELLOW, 92, anchor="mt")
    _text(img, 540, 520, "बार-बार कटता क्यों?", K.INK, 48, anchor="mt")
    _text(img, 540, 680, "सामान्य हलचल", K.INK, 42, anchor="mt")
    _text(img, 540, 780, "तंग रेखा को काट देती है", K.INK, 38, anchor="mt")
    _text(img, 540, 900, "एटीआर इस हलचल को नापता है", K.YELLOW, 32, anchor="mt")
    return _finish(img, "hook")


def _problem(lesson, step, total):
    img = _blank()
    _card(img)
    bars = lesson["wick_bars"]
    ohlc = _ohlc(bars)
    dates = [str(b["date"])[:10] for b in bars]
    hit = dates.index("2026-06-19") if "2026-06-19" in dates else max(0, len(ohlc) - 1)
    show_hit = total <= 1 or step >= total - 1
    count = len(ohlc) if show_hit else max(1, hit)
    tight = float(lesson["tight"])
    lo = min(min(c[2] for c in ohlc), tight)
    hi = max(max(c[1] for c in ohlc), tight)
    box = (108, 330, 880, 860)
    ax = _candles(img, box, bars, lo, hi, count)
    if show_hit:
        _rule(img, ax, tight, K.RED, 1.0)
        K.cross(img, ax.x(hit), ax.y(tight), 16, K.RED, 5)
    _watermark(img, box)
    _text(img, 100, 220, "समस्या", K.YELLOW, 40, anchor="lt")
    _text(img, 100, 274, "एचडीएफसी बैंक दैनिक", K.INK, 28, anchor="lt")
    if show_hit:
        _text(img, 620, 226, "तंग रेखा कट गई", K.RED, 30, anchor="lt")
    else:
        _text(img, 620, 226, "कैंडल दर कैंडल", K.MUTED, 30, anchor="lt")
    _axis_labels(img, ax)
    _source(img, lesson)
    return _finish(img, "problem")


def _formula(lesson, step, total):
    img = _blank()
    _card(img)
    if total <= 1:
        phase = 2
    elif total == 2:
        phase = 0 if step == 0 else 2
    else:
        phase = min(2, step)
    filled = _ease((phase + 1) / 3.0)
    ImageDraw.Draw(img).rounded_rectangle(
        [200, 830, 200 + int(680 * filled), 842], 5, fill=K.YELLOW,
    )
    _text(img, 540, 240, "एटीआर क्या है", K.YELLOW, 58, anchor="mt")
    _text(img, 540, 330, "पिछले कैंडल की औसत हलचल", K.INK, 32, anchor="mt")
    if phase == 0:
        _text(img, 540, 470, str(lesson["period"]), K.YELLOW, 132, anchor="mt")
        _text(img, 540, 680, "कैंडल की औसत", K.INK, 42, anchor="mt")
    elif phase == 1:
        _text(img, 540, 450, "औसत", K.MUTED, 34, anchor="mt")
        _text(img, 540, 530, lesson["atr"], K.YELLOW, 116, anchor="mt")
        _text(img, 540, 720, "पॉइंट हलचल", K.INK, 40, anchor="mt")
    else:
        _text(img, 540, 470, f"{lesson['atr']}  ×  {lesson['multiple']}", K.INK, 50, anchor="mt")
        _text(img, 540, 580, f"=  {lesson['distance']}", K.YELLOW, 80, anchor="mt")
        _text(img, 540, 740, "पॉइंट की दूरी", K.INK, 40, anchor="mt")
    _text(img, 540, 900, "दिशा नहीं, सिर्फ़ हलचल", K.MUTED, 30, anchor="mt")
    _source(img, lesson)
    return _finish(img, "formula")


def _ladder_ys(lesson):
    entry, sl = float(lesson["entry"]), float(lesson["sl"])
    target = float(lesson["target"])
    span = (target - sl) or 1.0

    def y_of(price):
        return 900 - (price - sl) / span * 440

    return entry, sl, target, y_of


def _bracket(img, x, y0, y1, color):
    draw = ImageDraw.Draw(img)
    top, bot = (y0, y1) if y0 <= y1 else (y1, y0)
    if bot - top < 2:
        bot = top + 2
    draw.line([(x, top), (x, bot)], fill=color, width=4)
    draw.line([(x - 16, top), (x + 16, top)], fill=color, width=4)
    draw.line([(x - 16, bot), (x + 16, bot)], fill=color, width=4)


def _push_region(img, box, scale):
    """Zoom one rectangle as a bitmap. The caller decides which image (plate or still)."""
    if scale <= 1.001:
        return
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = img.crop((x0, y0, x1, y1))
    cw, ch = crop.size
    cx, cy = cw // 2, ch // 2
    hw = min(cx, cw - cx, max(2, int(round(cw / scale / 2))))
    hh = min(cy, ch - cy, max(2, int(round(ch / scale / 2))))
    zoomed = crop.crop((cx - hw, cy - hh, cx + hw, cy + hh)).resize((cw, ch), Image.Resampling.LANCZOS)
    img.paste(zoomed, (x0, y0))


def _push(img, box, scale):
    """Camera push on the plot. Sprites inside the plot scale as one layer each."""
    if scale <= 1.001:
        return img
    _push_region(img, box, scale)
    plate = getattr(img, "plate", None)
    if plate is not None:
        _push_region(plate, box, scale)
    x0, y0, x1, y1 = (float(v) for v in box)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    moved = []
    for layer in list(getattr(img, "text_layers", []) or []):
        sprite = layer["image"]
        sw, sh = sprite.size
        lx, ly = layer["xy"]
        scx, scy = lx + sw / 2.0, ly + sh / 2.0
        if not (x0 <= scx <= x1 and y0 <= scy <= y1):
            moved.append(layer)
            continue
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        scaled = sprite.resize((nw, nh), Image.Resampling.LANCZOS)
        ncx = cx + (scx - cx) * scale
        ncy = cy + (scy - cy) * scale
        updated = dict(layer)
        updated["image"] = scaled
        updated["xy"] = (int(round(ncx - nw / 2.0)), int(round(ncy - nh / 2.0)))
        box0 = layer.get("box")
        if box0:
            bcx = (box0[0] + box0[2]) / 2.0
            bcy = (box0[1] + box0[3]) / 2.0
            bw = (box0[2] - box0[0]) * scale
            bh = (box0[3] - box0[1]) * scale
            nbx = cx + (bcx - cx) * scale
            nby = cy + (bcy - cy) * scale
            updated["box"] = (nbx - bw / 2.0, nby - bh / 2.0, nbx + bw / 2.0, nby + bh / 2.0)
        moved.append(updated)
    img.text_layers = moved
    img.label_boxes = [layer["box"] for layer in moved if layer.get("text") and layer.get("box")]
    return img


def _ladder_moving(lesson, phase):
    """Full ladder with a growing entry-to-SL bracket, a counting distance, and a ticking price."""
    phase = max(0.0, min(1.0, float(phase)))
    grown = _ease(phase)
    img = _blank()
    _card(img)
    entry, sl, target, y_of = _ladder_ys(lesson)
    draw = ImageDraw.Draw(img)
    draw.line([(200, 420), (200, 960)], fill=(88, 100, 120), width=3)
    rows = (
        (entry, K.TARGET_GREEN, f"एंट्री  {lesson['entry']}"),
        (sl, K.SL_RED, f"एसएल  {lesson['sl']}"),
        (target, K.BLUE, f"ऊपर  {lesson['target']}"),
    )
    for price, col, label in rows:
        y = y_of(price)
        draw.line([(230, y), (700, y)], fill=col, width=4)
        draw.line([(860, y), (886, y)], fill=(120, 132, 150), width=3)
    tip = y_of(entry) + (y_of(sl) - y_of(entry)) * grown
    _bracket(img, 760, y_of(entry), tip, GOLD)
    final = float(lesson["distance"])
    shown = final * grown
    count = lesson["distance"] if abs(shown - final) < 0.05 else f"{shown:.1f}"
    live = entry + (sl - entry) * grown
    tick = lesson["sl"] if abs(live - sl) < 0.05 else f"{live:.1f}"
    _text(img, 100, 220, "कीमत की सीढ़ी", K.YELLOW, 42, anchor="lt")
    _text(img, 540, 312, f"दूरी  {count}", K.YELLOW, 32, anchor="mt")
    for price, col, label in rows:
        _text(img, 250, y_of(price) - 42, label, col, 30, anchor="lt")
    _text(img, 898, tip, tick, GOLD, 22, anchor="lm")
    img = _finish(img, "ladder")
    _push(img, (150, 380, 990, 1020), 1.0 + 0.06 * phase)
    return img


def _ladder(lesson, step, total):
    img = _blank()
    _card(img)
    entry, sl, target, y_of = _ladder_ys(lesson)
    reveal = 4 if total <= 1 else min(4, step + 1)
    draw = ImageDraw.Draw(img)
    draw.line([(200, 420), (200, 960)], fill=(88, 100, 120), width=3)
    rows = (
        (entry, K.TARGET_GREEN, f"एंट्री  {lesson['entry']}", 1),
        (sl, K.SL_RED, f"एसएल  {lesson['sl']}", 2),
        (target, K.BLUE, f"ऊपर  {lesson['target']}", 3),
    )
    pending = []
    active = min(reveal, 3)
    for price, col, label, need in rows:
        if reveal < need:
            continue
        y = y_of(price)
        glow = need == active
        partial = glow and reveal < 4 and total > 1
        frac = _ease(0.58) if partial else 1.0
        x1 = 230 + 470 * frac
        if not glow:
            draw.line([(230, y), (x1, y)], fill=col, width=4)
        pending.append((230, y, x1, col, label, glow, frac))
    _text(img, 100, 220, "कीमत की सीढ़ी", K.YELLOW, 42, anchor="lt")
    for x0, y, x1, col, label, glow, frac in pending:
        if glow:
            _glow_line(img, x0, y, x1, col)
        if frac > 0.8:
            _text(img, 250, y - 42, label, col, 30, anchor="lt")
    if reveal >= 4 and total <= 1:
        _text(img, 540, 312, f"दूरी  {lesson['distance']}", K.YELLOW, 32, anchor="mt")
    return _finish(img, "ladder")


def _split(lesson):
    img = _blank()
    _card(img)
    bars = lesson["wick_bars"]
    ohlc = _ohlc(bars)
    dates = [str(b["date"])[:10] for b in bars]
    hit = dates.index("2026-06-19") if "2026-06-19" in dates else len(ohlc) - 1
    tight, sl = float(lesson["tight"]), float(lesson["sl"])
    lo = min(min(c[2] for c in ohlc), sl, tight)
    hi = max(max(c[1] for c in ohlc), tight)
    left = (96, 310, 500, 760)
    right = (548, 310, 860, 760)
    ax_l = _candles(img, left, bars, lo, hi, len(ohlc))
    ax_r = _candles(img, right, bars, lo, hi, len(ohlc))
    _rule(img, ax_l, tight, K.RED, 1.0)
    _rule(img, ax_r, sl, K.TARGET_GREEN, 1.0)
    K.cross(img, ax_l.x(hit), ax_l.y(tight), 14, K.RED, 5)
    held = min(c[2] for c in ohlc) + 1e-9 >= sl
    if held:
        K.tick(img, ax_r.x(len(ohlc) - 1), max(340, ax_r.y(sl) - 28), 14, K.GREEN, 5)
    _watermark(img, right)
    _text(img, 300, 220, "गलत", K.RED, 36, anchor="mt")
    _text(img, 760, 220, "सही", K.GREEN, 36, anchor="mt")
    _axis_labels(img, ax_r, steps=3)
    _text(img, 298, 860, "तंग रेखा कट गई", K.RED, 24, anchor="mt")
    _text(img, 760, 860, "एटीआर रेखा रुकी" if held else "एटीआर रेखा", K.GREEN, 24, anchor="mt")
    # Karein already burns the grey disclaimer in the brand band. A second
    # source line there would sit on top of it. The problem and formula
    # scenes, outside that beat, still carry the Yahoo line.
    return _finish(img, "split")


def _size(lesson):
    img = _blank()
    _card(img)
    ImageDraw.Draw(img).rounded_rectangle([200, 800, 880, 812], 5, fill=K.YELLOW)
    _text(img, 540, 250, "चौड़ी एसएल", K.YELLOW, 60, anchor="mt")
    _text(img, 540, 370, "कम मात्रा", K.INK, 48, anchor="mt")
    _text(img, 540, 520, f"{lesson['distance']}  ×  {lesson['qty']}", K.INK, 46, anchor="mt")
    _text(img, 540, 640, f"=  {lesson['risk']} रुपये", K.YELLOW, 50, anchor="mt")
    _text(img, 540, 860, "दूरी बढ़े तो मात्रा घटाओ", K.INK, 32, anchor="mt")
    _text(img, 540, 930, "जोखिम रुपये में रहता है", K.MUTED, 28, anchor="mt")
    return _finish(img, "size")


def _takeaway(lesson):
    img = _blank()
    _card(img)
    ImageDraw.Draw(img).rectangle([240, 340, 840, 350], fill=K.YELLOW)
    _text(img, 540, 250, "याद रखें", K.YELLOW, 46, anchor="mt")
    _text(img, 540, 420, f"{lesson['multiple']} गुना एटीआर", K.YELLOW, 56, anchor="mt")
    _text(img, 540, 580, "तंग रेखा शोर में कटती है", K.INK, 36, anchor="mt")
    _text(img, 540, 700, "चौड़ी दूरी, कम मात्रा", K.INK, 36, anchor="mt")
    _text(img, 540, 880, "शिक्षा है, सलाह नहीं", K.MUTED, 30, anchor="mt")
    return _finish(img, "takeaway")


def _cta(lesson, step=0, total=1):
    """Two different cards so the last beat is not one still for six seconds."""
    img = _blank()
    _card(img)
    draw = ImageDraw.Draw(img)
    if total > 1 and step >= 1:
        draw.rectangle([140, 280, 940, 294], fill=K.YELLOW)
        _text(img, 540, 360, "फिर खुद तय करें", K.YELLOW, 64, anchor="mt")
        _text(img, 540, 540, "शिक्षा है, सलाह नहीं", K.INK, 40, anchor="mt")
        _text(img, 540, 700, f"दूरी {lesson['multiple']} गुना एटीआर", K.MUTED, 34, anchor="mt")
    else:
        draw.rectangle([340, 360, 740, 372], fill=K.YELLOW)
        _text(img, 540, 430, "एक नियम", K.YELLOW, 76, anchor="mt")
        _text(img, 540, 620, f"दूरी {lesson['multiple']} गुना एटीआर", K.INK, 40, anchor="mt")
    return _finish(img, "cta")


_BUILDERS = {
    "hook": lambda lesson, step, total: _hook(lesson),
    "problem": lambda lesson, step, total: _problem(lesson, step, total),
    "formula": lambda lesson, step, total: _formula(lesson, step, total),
    "ladder": lambda lesson, step, total: _ladder(lesson, step, total),
    "split": lambda lesson, step, total: _split(lesson),
    "size": lambda lesson, step, total: _size(lesson),
    "takeaway": lambda lesson, step, total: _takeaway(lesson),
    "cta": lambda lesson, step, total: _cta(lesson, step, total),
}


def _atr_frames(beat, n, lesson):
    kinds = kinds_for(beat, n)
    frames, flags = [], []
    seen = {}
    ladder_total = kinds.count("ladder")
    settled = []
    cursor = {}
    for kind in kinds:
        step = cursor.get(kind, 0)
        if kind == "ladder" and ladder_total > 1 and min(4, step + 1) >= 4:
            settled.append(step)
        cursor[kind] = step + 1
    for kind in kinds:
        step = seen.get(kind, 0)
        total = kinds.count(kind)
        img = _BUILDERS[kind](lesson, step, total)
        if kind == "ladder" and step in settled:
            slot = settled.index(step)
            p0 = slot / len(settled)
            p1 = (slot + 1) / len(settled)
            img = _ladder_moving(lesson, p1)
            img.animate = (lambda u, start=p0, end=p1: _ladder_moving(
                lesson, start + (end - start) * max(0.0, min(1.0, float(u)))
            ))
        frames.append(img)
        flags.append(
            kind in ("formula", "split", "size", "takeaway", "cta")
            or (kind == "problem" and step > 0)
            or (kind == "ladder" and step > 0)
        )
        seen[kind] = step + 1
    return frames, flags


def _generic_frames(topic, beat, n, spec):
    mode = (spec or {}).get("mode")
    if mode == "non_numeric":
        bases = [market_data.non_numeric_frame(topic, beat, i % 2) for i in range(min(n, 2))]
    else:
        img = K.background()
        pair = S.render_beat(topic["key"], beat, img)
        bases = [pair[-1]]
    labels = ("KEY AREA", "RULE", "LOOK HERE", "NEXT STEP")
    frames = []
    flags = []
    for i in range(n):
        src = bases[min(i, len(bases) - 1)].copy()
        if i == 0:
            frame = src
            flag = False
        elif i % 2 == 1:
            frame = zoom_at(src, 560, 640, 0.70 + (i % 3) * 0.03, shift_x=(i % 5 - 2) * 36)
            frame = screen_callout(frame, labels[i % len(labels)])
            flag = True
        else:
            frame = zoom_at(src, 460 + (i * 24), 720, 0.62, shift_x=-50)
            flag = False
        if mode == "series":
            frame = market_data.stamp(frame, spec)
        frames.append(frame)
        flags.append(flag)
    return frames, flags


def frames_for(topic, beat, n, spec):
    """Return (images, callout flags), one per planned scene in this beat."""
    n = max(1, int(n))
    if (spec or {}).get("atr"):
        return _atr_frames(beat, n, spec["atr"])
    return _generic_frames(topic, beat, n, spec)
