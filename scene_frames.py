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
_SANS = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
_font_cache = {}
_cmap_cache = {}
_BG = None
_SHADOWS = {}
CHART_KINDS = ("problem", "split")
CARD = (72, 188, 1008, 1048)
NAVY = (12, 18, 32)
GOLD = (212, 168, 72)
PROGRESS = (72, 1768, 1008, 1776)


def _cmap(path):
    if path not in _cmap_cache:
        from fontTools.ttLib import TTFont
        tt = TTFont(path)
        _cmap_cache[path] = set(tt.getBestCmap() or {})
        tt.close()
    return _cmap_cache[path]


def missing_glyphs(text, font_path):
    """Characters the font would draw as .notdef. Spaces are not glyphs."""
    covered = _cmap(font_path)
    return [ch for ch in (text or "") if not ch.isspace() and ord(ch) not in covered]


def _kind_for(ch):
    o = ord(ch)
    deva = (0x0900 <= o <= 0x097F) or (0xA8E0 <= o <= 0xA8FF)
    in_d = o in _cmap(_FONT)
    in_s = o in _cmap(_SANS)
    if deva or (in_d and not in_s):
        return "deva"
    if in_s and not in_d:
        return "sans"
    if in_d and in_s:
        return "common"
    raise ValueError(f"no glyph for {ch!r}")


def _runs(text):
    """One HarfBuzz run per script. Shared punctuation stays with the neighbor that covers it."""
    if not text:
        return []
    chars = list(text)
    kinds = [_kind_for(ch) for ch in chars]
    resolved = list(kinds)
    for i, kind in enumerate(kinds):
        if kind != "common":
            continue
        prev = next((kinds[j] for j in range(i - 1, -1, -1) if kinds[j] != "common"), None)
        nxt = next((kinds[j] for j in range(i + 1, len(kinds)) if kinds[j] != "common"), None)

        def covers(name, ch=chars[i]):
            path = _FONT if name == "deva" else _SANS
            return ord(ch) in _cmap(path)

        if prev and covers(prev):
            resolved[i] = prev
        elif nxt and covers(nxt):
            resolved[i] = nxt
        elif covers("deva"):
            resolved[i] = "deva"
        else:
            resolved[i] = "sans"
    runs = []
    for ch, kind in zip(chars, resolved):
        path = _FONT if kind == "deva" else _SANS
        if runs and runs[-1][0] == path:
            runs[-1] = (path, runs[-1][1] + ch)
        else:
            runs.append((path, ch))
    for path, piece in runs:
        miss = missing_glyphs(piece, path)
        if miss:
            raise ValueError(f"no glyph for {''.join(miss)!r}")
    return runs


def _font(size, path=None):
    path = path or _FONT
    size = int(size)
    key = (size, path)
    if key not in _font_cache:
        if os.path.exists(path):
            _font_cache[key] = ImageFont.truetype(path, size)
        else:
            _font_cache[key] = K.font(size)
    return _font_cache[key]


def _layout_runs(text, size):
    runs = _runs(text)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    x = 0.0
    parts = []
    for path, piece in runs:
        font = _font(size, path)
        box = probe.textbbox((x, 0), piece, font=font, anchor="lt")
        parts.append((path, piece, font, box))
        x = float(box[2])
    union = (
        min(p[3][0] for p in parts),
        min(p[3][1] for p in parts),
        max(p[3][2] for p in parts),
        max(p[3][3] for p in parts),
    )
    return parts, union


def measure_ink(text, size):
    """Width of the shaped line, using the same script runs the drawer uses."""
    _parts, union = _layout_runs(text, int(size))
    return max(1, int(round(union[2] - union[0])))


def layer_missing(layer):
    """Characters a rendered sprite would have drawn as tofu."""
    text = layer.get("text") or ""
    if not text:
        return []
    runs = layer.get("runs")
    if not runs:
        return missing_glyphs(text, layer.get("font") or _FONT)
    missing = []
    for path, piece in runs:
        missing.extend(missing_glyphs(piece, path))
    return missing


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


def _anchor_shift(anchor, union):
    """Move a left-top layout so `anchor` sits on the caller's point."""
    x0, y0, x1, y1 = union
    w, h = x1 - x0, y1 - y0
    if anchor == "mt":
        return -w / 2.0 - x0, -y0
    if anchor == "mm":
        return -w / 2.0 - x0, -h / 2.0 - y0
    if anchor == "lm":
        return -x0, -h / 2.0 - y0
    if anchor == "rm":
        return -w - x0, -h / 2.0 - y0
    return -x0, -y0


def _remember(img, sprite, xy, text, box, size, persist, faint, role, runs):
    img.paste(sprite, xy, sprite)
    pad = 6
    rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    img.label_boxes.append(tuple(rect))
    img.text_layers.append({
        "image": sprite,
        "xy": xy,
        "text": text,
        "shaped": True,
        "box": tuple(float(v) for v in box),
        "size": int(size),
        "persist": bool(persist),
        "faint": bool(faint),
        "role": role or "lesson",
        "font": runs[0][0] if runs else _FONT,
        "runs": list(runs),
    })
    return rect


def _text(img, x, y, text, fill, size, anchor="lt", persist=False, band=True, alpha=255,
          faint=False, role="lesson"):
    """Draw one whole shaped line and record its padded box.

    A single script is one ImageDraw.text call (one HarfBuzz/Raqm sprite).
    A mixed line is several runs pasted into that same sprite. Call this after
    the candles, rules, and card are already on the plate.
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
    runs = _runs(text)
    fill_rgba = tuple(int(c) for c in fill) + (int(alpha),)
    probe = ImageDraw.Draw(img)
    while size >= 16:
        if len(runs) == 1:
            font = _font(size, runs[0][0])
            box = probe.textbbox((x, y), text, font=font, anchor=anchor)
        else:
            _parts, union = _layout_runs(text, size)
            dx, dy = _anchor_shift(anchor, union)
            box = (x + dx + union[0], y + dy + union[1], x + dx + union[2], y + dy + union[3])
        rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        shift_x = shift_y = 0.0
        if rect[0] < left:
            shift_x = left - rect[0]
        elif rect[2] > right:
            shift_x = right - rect[2]
        if rect[1] < top:
            shift_y = top - rect[1]
        elif rect[3] > bottom:
            shift_y = bottom - rect[3]
        if shift_x or shift_y:
            x += shift_x
            y += shift_y
            if len(runs) == 1:
                font = _font(size, runs[0][0])
                box = probe.textbbox((x, y), text, font=font, anchor=anchor)
            else:
                _parts, union = _layout_runs(text, size)
                dx, dy = _anchor_shift(anchor, union)
                box = (x + dx + union[0], y + dy + union[1], x + dx + union[2], y + dy + union[3])
            rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        fits = (
            rect[0] >= left - 0.2 and rect[2] <= right + 0.2
            and rect[1] >= top - 0.2 and rect[3] <= bottom + 0.2
        )
        if fits:
            break
        size -= 2
    size = max(size, 16)
    bleed = 4
    if len(runs) == 1:
        font = _font(size, runs[0][0])
        box = probe.textbbox((x, y), text, font=font, anchor=anchor)
        x0 = int(math.floor(box[0])) - bleed
        y0 = int(math.floor(box[1])) - bleed
        x1 = int(math.ceil(box[2])) + bleed
        y1 = int(math.ceil(box[3])) + bleed
        sprite = Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0))
        ImageDraw.Draw(sprite).text((x - x0, y - y0), text, font=font, fill=fill_rgba, anchor=anchor)
    else:
        parts, union = _layout_runs(text, size)
        dx, dy = _anchor_shift(anchor, union)
        box = (x + dx + union[0], y + dy + union[1], x + dx + union[2], y + dy + union[3])
        x0 = int(math.floor(box[0])) - bleed
        y0 = int(math.floor(box[1])) - bleed
        x1 = int(math.ceil(box[2])) + bleed
        y1 = int(math.ceil(box[3])) + bleed
        sprite = Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        for path, piece, font, part_box in parts:
            draw.text(
                (x + dx + part_box[0] - x0, y + dy + part_box[1] - y0),
                piece, font=font, fill=fill_rgba, anchor="lt",
            )
    return _remember(
        img, sprite, (x0, y0), text, box, size, persist, faint or alpha < 80, role, runs,
    )


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
    _text(
        img, 88, safe_area.BRAND_Y, "PKP", GOLD, 26, anchor="lt", persist=True, band=False,
        role="brand",
    )


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
        K.MUTED, 26, anchor="mt", band=False, role="source",
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
        _text(img, ax.x1 + 14, y, f"{price:.1f}", K.MUTED, 20, anchor="lm", role="axis")


def _watermark(img, box):
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    _text(img, cx, cy, "PKP", GOLD, 64, anchor="mm", alpha=42, faint=True, role="brand")


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
        _text(img, 620, 220, "एटीआर", K.YELLOW, 32, anchor="lt")
        _text(img, 620, 268, "हलचल नापता है", K.INK, 26, anchor="lt")
    else:
        _text(img, 620, 220, "सामान्य हलचल", K.YELLOW, 30, anchor="lt")
        _text(img, 620, 268, "तंग रेखा", K.INK, 28, anchor="lt")
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
        # The figure itself is spoken at the end of this beat, on the next card.
        _text(img, 540, 470, "औसत", K.YELLOW, 88, anchor="mt")
        _text(img, 540, 640, "हलचल", K.INK, 64, anchor="mt")
        _text(img, 540, 780, "पिछले कैंडल", K.MUTED, 32, anchor="mt")
    else:
        # The product is spoken in the next beat. This card stays on the average.
        _text(img, 540, 430, "एचडीएफसी बैंक", K.INK, 40, anchor="mt")
        _text(img, 540, 530, lesson["atr"], K.YELLOW, 116, anchor="mt")
        _text(img, 540, 720, "औसत हलचल", K.INK, 42, anchor="mt")
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


def _band(img, y, alpha):
    """Gold row behind the level being drawn. Geometry, not a text sprite."""
    alpha = int(max(0, min(180, alpha)))
    if alpha < 8:
        return
    height = 58
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    top = int(round(float(y) - height / 2.0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        [188, top, 908, top + height], 16, fill=(212, 168, 72, alpha),
    )
    img.paste(overlay, (0, 0), overlay)


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
    """Full ladder with a growing entry-to-SL bracket, a counting distance, and a ticking price.

    SL and the upper price stay off the card until this phase has reached them.
    The distance line is the real figure, never a 0.0 placeholder.
    """
    phase = max(0.0, min(1.0, float(phase)))
    grown = _ease(phase)
    show_sl = phase >= 0.35
    show_target = phase >= 0.70
    img = _blank()
    _card(img)
    entry, sl, target, y_of = _ladder_ys(lesson)
    draw = ImageDraw.Draw(img)
    draw.line([(200, 420), (200, 960)], fill=(88, 100, 120), width=3)
    rows = (
        (entry, K.TARGET_GREEN, f"एंट्री  {lesson['entry']}", True),
        (sl, K.SL_RED, f"एसएल  {lesson['sl']}", show_sl),
        (target, K.BLUE, f"ऊपर  {lesson['target']}", show_target),
    )
    for price, col, label, show in rows:
        if not show:
            continue
        y = y_of(price)
        draw.line([(230, y), (700, y)], fill=col, width=4)
        draw.line([(860, y), (886, y)], fill=(120, 132, 150), width=3)
    tip = y_of(entry) + (y_of(sl) - y_of(entry)) * grown
    _band(img, tip, 90 + 70 * grown)
    _bracket(img, 760, y_of(entry), tip, GOLD)
    final = float(lesson["distance"])
    shown = final * grown
    count = lesson["distance"] if abs(shown - final) < 0.05 else f"{shown:.1f}"
    live = entry + (sl - entry) * grown
    tick = lesson["sl"] if abs(live - sl) < 0.05 else f"{live:.1f}"
    _text(img, 100, 220, "कीमत की सीढ़ी", K.YELLOW, 42, anchor="lt")
    _text(img, 540, 292, f"दूरी  {lesson['distance']}", K.YELLOW, 32, anchor="mt")
    for price, col, label, show in rows:
        if show:
            _text(img, 250, y_of(price) - 42, label, col, 30, anchor="lt")
    if phase >= 0.08:
        _text(img, 860, 300, count, GOLD, 22, anchor="mt", role="meter")
        _text(img, 898, tip, tick, GOLD, 22, anchor="lm", role="meter")
    img = _finish(img, "ladder")
    _push(img, (150, 380, 990, 1020), 1.0 + 0.06 * phase)
    return img


def _ladder_reveal(lesson, phase):
    """Entry, SL, then the upper line draw on while a gold row travels with them.

    phase 1 is the bracket's first frame, so the handoff does not pop.
    """
    phase = max(0.0, min(1.0, float(phase)))
    if phase >= 0.999:
        return _ladder_moving(lesson, 0.0)
    entry, sl, target, y_of = _ladder_ys(lesson)
    img = _blank()
    _card(img)
    draw = ImageDraw.Draw(img)
    draw.line([(200, 420), (200, 960)], fill=(88, 100, 120), width=3)
    rows = (
        (entry, K.TARGET_GREEN, f"एंट्री  {lesson['entry']}"),
        (sl, K.SL_RED, f"एसएल  {lesson['sl']}"),
        (target, K.BLUE, f"ऊपर  {lesson['target']}"),
    )
    cuts = (0.34, 0.67)
    if phase < cuts[0]:
        local = _ease(phase / cuts[0])
        fracs = (local, 0.0, 0.0)
        band_y = y_of(entry)
        band_a = 150 * local
    elif phase < cuts[1]:
        local = _ease((phase - cuts[0]) / (cuts[1] - cuts[0]))
        fracs = (1.0, local, 0.0)
        band_y = y_of(entry) + (y_of(sl) - y_of(entry)) * local
        band_a = 150
    else:
        local = _ease((phase - cuts[1]) / (1.0 - cuts[1]))
        fracs = (1.0, 1.0, local)
        band_y = y_of(sl) + (y_of(target) - y_of(sl)) * local
        fade = max(0.0, (phase - 0.90) / 0.10)
        band_a = 150 * (1.0 - _ease(fade))
    _band(img, band_y, band_a)
    pending = []
    for (price, col, label), frac in zip(rows, fracs):
        if frac <= 0.02:
            continue
        y = y_of(price)
        x1 = 230 + 470 * frac
        draw.line([(230, y), (x1, y)], fill=col, width=4)
        if frac > 0.92:
            draw.line([(860, y), (886, y)], fill=(120, 132, 150), width=3)
        pending.append((y, col, label, frac, price))
    _text(img, 100, 220, "कीमत की सीढ़ी", K.YELLOW, 42, anchor="lt")
    # Price words wait until the voice has reached them. The gold row still travels.
    for y, col, label, frac, price in pending:
        if frac > 0.82 and abs(price - entry) < 0.05:
            _text(img, 250, y - 42, label, col, 30, anchor="lt")
    if phase >= 0.50:
        _text(img, 540, 300, f"{lesson['multiple']} गुना", K.YELLOW, 32, anchor="mt")
    if phase >= 0.75:
        _text(img, 540, 360, f"दूरी  {lesson['distance']}", K.INK, 32, anchor="mt")
    return _finish(img, "ladder")


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
    """This slice says the tight line is cut by the ATR swing, not the rupee total."""
    img = _blank()
    _card(img)
    ImageDraw.Draw(img).rounded_rectangle([200, 860, 880, 872], 5, fill=K.YELLOW)
    _text(img, 540, 280, "तंग रेखा", K.RED, 64, anchor="mt")
    _text(img, 540, 440, f"{lesson['atr']} की हलचल", K.YELLOW, 52, anchor="mt")
    _text(img, 540, 600, "रेखा कटती है", K.INK, 48, anchor="mt")
    _text(img, 540, 760, "शोर में तंग एसएल", K.MUTED, 32, anchor="mt")
    return _finish(img, "size")


def _takeaway(lesson):
    img = _blank()
    _card(img)
    ImageDraw.Draw(img).rectangle([240, 300, 840, 310], fill=K.YELLOW)
    _text(img, 540, 360, "दूरी", K.YELLOW, 48, anchor="mt")
    _text(img, 540, 480, str(lesson["distance"]), K.YELLOW, 96, anchor="mt")
    _text(img, 540, 660, "पॉइंट", K.INK, 40, anchor="mt")
    _text(img, 540, 780, "मात्रा इसी दूरी से", K.INK, 36, anchor="mt")
    return _finish(img, "takeaway")


def _rupees(value):
    return f"₹{int(float(str(value).replace(',', ''))):,}"


def _cta_at(lesson, step=0, total=1, phase=1.0):
    """One recap card. The second still adds the education line and the poll."""
    phase = max(0.0, min(1.0, float(phase)))
    img = _blank()
    _card(img)
    draw = ImageDraw.Draw(img)
    bar = 200 + int(680 * (0.35 + 0.65 * phase))
    draw.rounded_rectangle([200, 250, bar, 264], 5, fill=K.YELLOW)
    sl_line = f"एसएल = भाव − {lesson['multiple']} गुना एटीआर = {lesson['sl']}"
    risk_line = f"{lesson['qty']} शेयर पर जोखिम {_rupees(lesson['risk'])}"
    _text(img, 540, 320, sl_line, K.INK, 32, anchor="mt")
    _text(img, 540, 430, risk_line, K.YELLOW, 36, anchor="mt")
    if total > 1 and step >= 1:
        panel_h = int(220 * phase)
        if panel_h > 8:
            draw.rounded_rectangle([140, 860, 940, 860 + panel_h], 18, fill=(24, 32, 48))
        _text(img, 540, 560, "शिक्षा है, सलाह नहीं", K.INK, 40, anchor="mt")
        import engagement
        _text(
            img, 540, 700, engagement.engagement_question({"cat": "INDICATOR"}),
            K.YELLOW, 28, anchor="mt", role="cta",
        )
    elif phase > 0.15:
        # Motion inside the first recap so a four-second span is not a still.
        tick = int(40 * phase)
        draw.rectangle([160, 620, 160 + int(760 * phase), 628 + tick], fill=(212, 168, 72))
    return _finish(img, "cta")


def _cta(lesson, step=0, total=1):
    """Two different recap cards so the last beat is not one still."""
    img = _cta_at(lesson, step, total, 1.0)
    if total > 1:
        img.animate = (lambda u, step=step, total=total: _cta_at(
            lesson, step, total, max(0.0, min(1.0, float(u))),
        ))
    return img


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
        if kind == "ladder" and ladder_total > 1 and step not in settled:
            # The first ladder cuts only added a line. A gold row now travels
            # with each stroke so a four-second span cannot stay a still.
            drawing = [s for s in range(ladder_total) if s not in settled]
            slot = drawing.index(step)
            p0 = slot / len(drawing)
            p1 = (slot + 1) / len(drawing)
            img = _ladder_reveal(lesson, p1)
            img.animate = (lambda u, start=p0, end=p1: _ladder_reveal(
                lesson, start + (end - start) * max(0.0, min(1.0, float(u)))
            ))
        elif kind == "ladder" and step in settled:
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
