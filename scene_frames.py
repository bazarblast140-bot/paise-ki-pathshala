"""Full-screen 9:16 scenes. ATR is seven different cards, not one zoomed chart.

The candlestick chart is only the problem scene and the wrong-vs-right split.
Other scenes are type, a formula, a price ladder, a position-size card, and
a takeaway. Every line of text is one Raqm-shaped sprite. The plate behind it
may slide or wipe; the sprite is only faded as a whole layer.

Labels are recorded so the 6% safe-area gate can reject a crop.
"""
import math
import os
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import board_kit as K
import board_scenes as S
import hook_check
import market_data
import safe_area

_FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"
_SANS = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
_font_cache = {}
_cmap_cache = {}
_BG = None
_SHADOWS = {}
CHART_KINDS = ("problem", "split")
# 1240px is 64.6% of the 1920 frame. The caption band starts just under this.
CARD = (72, 124, 1008, 1364)
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
    beat = {"udaharan": "matlab", "galti": "karein"}.get(beat, beat)
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


def _cy(y):
    """Map a y laid out on the old short card onto the taller card."""
    return 188.0 + (float(y) - 210.0) / 770.0 * 1100.0


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
    img.shapes = []
    img.content_boxes = []
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
    _mark_content(img, box)


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


def _remember(img, sprite, xy, text, box, size, persist, faint, role, runs, quantity=None):
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
        "quantity": quantity,
        "font": runs[0][0] if runs else _FONT,
        "runs": list(runs),
    })
    return rect


def _text(img, x, y, text, fill, size, anchor="lt", persist=False, band=True, alpha=255,
          faint=False, role="lesson", quantity=None):
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
        quantity,
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


def _mark_market(img, lesson):
    """Data scenes must show the close's as-of date. The craft gate reads this."""
    img.shows_market_data = True
    img.market_as_of = str((lesson or {}).get("as_of") or "")


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
    _mark_content(img, box)
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
        _text(img, ax.x1 + 14, y, market_data.format_figure(price, 1), K.MUTED, 20, anchor="lm", role="axis")


def _mark_content(img, box):
    """A chart or card rectangle the watermark must stay clear of."""
    boxes = getattr(img, "content_boxes", None)
    if boxes is None:
        img.content_boxes = []
        boxes = img.content_boxes
    boxes.append(tuple(float(v) for v in box))


def _rule(img, ax, price, color, frac=1.0):
    draw = ImageDraw.Draw(img)
    y = ax.y(price)
    x1 = ax.x0 + 8 + (ax.x1 - ax.x0 - 16) * max(0.08, min(1.0, frac))
    draw.line([(ax.x0 + 8, y), (x1, y)], fill=color, width=3)


def _hook(lesson):
    img = _blank()
    _card(img)
    _accent(img, [220, _cy(300), 860, _cy(300) + 12])
    _text(img, 540, _cy(360), "तंग एसएल", K.YELLOW, 92, anchor="mt")
    _text(img, 540, _cy(520), "बार-बार कटता क्यों?", K.INK, 48, anchor="mt")
    _text(img, 540, _cy(680), "सामान्य हलचल", K.INK, 42, anchor="mt")
    _text(img, 540, _cy(780), "तंग रेखा को काट देती है", K.INK, 38, anchor="mt")
    _text(img, 540, _cy(900), "एटीआर इस हलचल को नापता है", K.YELLOW, 36, anchor="mt")
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
    box = (108, _cy(300), 900, _cy(860))
    ax = _candles(img, box, bars, lo, hi, count)
    if show_hit:
        _rule(img, ax, tight, K.RED, 1.0)
        K.cross(img, ax.x(hit), ax.y(tight), 16, K.RED, 5)
    _mark_content(img, box)
    _text(img, 100, _cy(220), "समस्या", K.YELLOW, 40, anchor="lt")
    _text(img, 100, _cy(274), "एचडीएफसी बैंक दैनिक", K.INK, 28, anchor="lt")
    if show_hit:
        _text(img, 620, _cy(220), "एटीआर", K.YELLOW, 32, anchor="lt")
        _text(img, 620, _cy(268), "हलचल नापता है", K.INK, 26, anchor="lt")
    else:
        _text(img, 620, _cy(220), "सामान्य हलचल", K.YELLOW, 30, anchor="lt")
        _text(img, 620, _cy(268), "तंग रेखा", K.INK, 28, anchor="lt")
    _axis_labels(img, ax)
    _lower_callout(img, "दैनिक भाव")
    _mark_market(img, lesson)
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
    bar_y = _cy(900)
    _accent(img, [200, bar_y, 200 + int(680 * filled), bar_y + 12])
    _text(img, 540, _cy(240), "एटीआर क्या है", K.YELLOW, 64, anchor="mt")
    _text(img, 540, _cy(340), "पिछले कैंडल की औसत हलचल", K.INK, 36, anchor="mt")
    if phase == 0:
        _text(img, 540, _cy(500), str(lesson["period"]), K.YELLOW, 140, anchor="mt")
        _text(img, 540, _cy(720), "कैंडल की औसत", K.INK, 48, anchor="mt")
    elif phase == 1:
        # The figure itself is spoken at the end of this beat, on the next card.
        _text(img, 540, _cy(500), "औसत", K.YELLOW, 96, anchor="mt")
        _text(img, 540, _cy(680), "हलचल", K.INK, 72, anchor="mt")
        _text(img, 540, _cy(820), f"पिछले {lesson['period']} कैंडल", K.MUTED, 36, anchor="mt")
    else:
        # The product is spoken in the next beat. This card stays on the average.
        _text(img, 540, _cy(460), "एचडीएफसी बैंक", K.INK, 44, anchor="mt")
        _text(img, 540, _cy(580), lesson["atr"], K.YELLOW, 128, anchor="mt")
        _text(img, 540, _cy(780), "औसत हलचल", K.INK, 48, anchor="mt")
    _text(img, 540, _cy(960), "दिशा नहीं, सिर्फ़ हलचल", K.MUTED, 32, anchor="mt")
    _lower_callout(img, "सिर्फ़ हलचल")
    _mark_market(img, lesson)
    _source(img, lesson)
    return _finish(img, "formula")


def _ladder_ys(lesson):
    entry, sl = float(lesson["entry"]), float(lesson["sl"])
    target = float(lesson["target"])
    span = (target - sl) or 1.0
    top, bot = _cy(480), _cy(920)

    def y_of(price):
        return bot - (price - sl) / span * (bot - top)

    return entry, sl, target, y_of


def _mark_shape(img, box, alpha=255, kind="fill"):
    shapes = getattr(img, "shapes", None)
    if shapes is None:
        img.shapes = []
    img.shapes.append({
        "box": tuple(float(v) for v in box),
        "alpha": int(alpha),
        "kind": kind,
    })


def _accent(img, box):
    """Thin rule. A divider, not an empty bar."""
    ImageDraw.Draw(img).rounded_rectangle(list(box), 4, fill=K.YELLOW)
    _mark_shape(img, box, 255, "divider")


def _highlight(img, y, alpha=64):
    """Translucent gold wash on the plot, to the right of the price labels."""
    alpha = int(max(0, min(90, int(alpha))))
    height = 96
    x0, x1 = 560, 1000
    top = int(round(float(y) - height / 2.0))
    box = (x0, top, x1, top + height)
    if alpha >= 8:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            [x0, top, x1, top + height], 8, fill=(212, 168, 72, alpha),
        )
        img.paste(overlay, (0, 0), overlay)
        _mark_shape(img, box, alpha, "highlight")
    return box


def _band_label(img, y):
    """'जोखिम क्षेत्र' sits inside the wash, above the price line, with no second distance."""
    height = 96
    x0, x1 = 560, 1000
    top = float(y) - height / 2.0
    cx = (x0 + x1) / 2.0
    anchor_y = (top + float(y)) / 2.0
    label = "जोखिम क्षेत्र"
    chosen = 20
    for size in (28, 26, 24, 22, 20):
        font = _font(size)
        probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
        box = probe.textbbox((cx, anchor_y), label, font=font, anchor="mm")
        if box[0] >= x0 + 4 and box[2] <= x1 - 8 and box[1] >= top + 2 and box[3] <= float(y) - 2:
            chosen = size
            break
    _text(img, cx, anchor_y, label, K.INK, chosen, anchor="mm", role="lesson")


_UNIT_WORDS = {"पॉइंट", "रुपये", "शेयर"}


def _lone_unit_word(text):
    """True when the whole sprite is a unit and it carries no number."""
    raw = str(text or "").strip()
    if not raw or re.search(r"\d", raw):
        return False
    words = re.findall(r"[\u0900-\u097F]+", raw)
    return len(words) == 1 and words[0] in _UNIT_WORDS


def _number_only(text):
    raw = str(text or "").strip().replace(",", "").replace(" ", "")
    return bool(re.fullmatch(r"₹?\d+(?:\.\d+)?", raw))


def _unit_labels_a_figure(layer, layers):
    """A big figure directly above the unit is the number it names."""
    box = layer.get("box")
    if not box:
        return False
    mid = (float(box[0]) + float(box[2])) / 2.0
    for other in layers:
        if other is layer or not _number_only(other.get("text")):
            continue
        ob = other.get("box")
        if not ob:
            continue
        other_mid = (float(ob[0]) + float(ob[2])) / 2.0
        gap = float(box[1]) - float(ob[3])
        if abs(other_mid - mid) < 180 and -8 <= gap <= 360:
            return True
    return False


def normalize_label(text):
    """Compare titles and labels as one string, ignoring spacing and a closing danda."""
    return re.sub(r"\s+", " ", str(text or "").strip()).strip("।.!?… ")


def repeated_text_notes(layers, extra_texts=None, active_caption=""):
    """Fail when one scene paints the same string twice.

    The burned caption cue is not a scene sprite. Pass it as `active_caption`
    so it is left out; two matching labels still fail when the cue repeats them.
    """
    skip = normalize_label(active_caption)
    texts = []
    for layer in layers or []:
        if layer.get("faint") or layer.get("role") in ("brand", "glow"):
            continue
        if layer.get("text"):
            texts.append(layer.get("text"))
    for text in extra_texts or []:
        if normalize_label(text) == skip:
            continue
        texts.append(text)
    counts = {}
    for text in texts:
        key = normalize_label(text)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return [f"repeated text: {key}" for key, count in counts.items() if count > 1]


def frame_layout_notes(img):
    """Opaque covers, empty boxes, two numbers for one quantity, and a lone unit."""
    layers = [
        layer for layer in (getattr(img, "text_layers", None) or [])
        if layer.get("text") and layer.get("box") and not layer.get("faint")
    ]
    notes = []
    for shape in getattr(img, "shapes", None) or []:
        box = shape.get("box")
        if not box:
            continue
        kind = shape.get("kind") or "fill"
        alpha = int(shape.get("alpha") or 255)
        if kind != "divider" and alpha >= 120:
            for layer in layers:
                if safe_area.intersects(box, layer["box"]):
                    notes.append("opaque shape covers a text sprite")
                    break
        if kind in ("fill", "highlight"):
            width = abs(float(box[2]) - float(box[0]))
            height = abs(float(box[3]) - float(box[1]))
            if width * height < 2400 or min(width, height) <= 16:
                continue
            if not any(safe_area.intersects(box, layer["box"]) for layer in layers):
                notes.append("empty box with no content")
    for note in _watermark_notes(img, layers):
        notes.append(note)
    groups = {}
    for layer in layers:
        key = layer.get("quantity")
        if not key:
            continue
        raw = str(layer.get("text") or "").replace(",", "")
        groups.setdefault(key, set()).update(re.findall(r"\d+(?:\.\d+)?", raw))
    for key, nums in groups.items():
        if len(nums) > 1:
            notes.append("two values for " + key + ": " + ", ".join(sorted(nums)))
    for layer in layers:
        if _lone_unit_word(layer.get("text")) and not _unit_labels_a_figure(layer, layers):
            notes.append("lone unit word with no number")
            break
    notes.extend(repeated_text_notes(layers))
    if getattr(img, "panel_box", None) is not None:
        frac = panel_fill_fraction(img)
        img.panel_fill = frac
        if frac + 1e-9 < 0.35:
            notes.append(f"panel fill {frac:.0%} is under 35%")
    as_of = str(getattr(img, "market_as_of", "") or "")
    if as_of:
        blob = " ".join(str(layer.get("text") or "") for layer in (getattr(img, "text_layers", None) or []))
        if as_of not in blob:
            notes.append("as-of date is not on screen")
    return notes


def _watermark_notes(img, layers):
    """PKP must miss every content sprite and every chart or card rectangle."""
    brands = [
        layer for layer in (getattr(img, "text_layers", None) or [])
        if layer.get("role") == "brand" and layer.get("box") and layer.get("text")
    ]
    if not brands:
        return []
    content = [
        layer for layer in layers
        if layer.get("role") != "brand" and layer.get("box")
    ]
    regions = list(getattr(img, "content_boxes", None) or [])
    notes = []
    for brand in brands:
        hit = any(safe_area.intersects(brand["box"], layer["box"]) for layer in content)
        hit = hit or any(safe_area.intersects(brand["box"], box) for box in regions)
        if hit:
            notes.append("watermark overlaps content")
            break
    return notes


def _lower_callout(img, text):
    """A key line in the lower card, above the Shorts UI and left of the side buttons."""
    if not text:
        return
    size = 34
    x, y = 430.0, 1288.0
    font = _font(size)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    box = probe.textbbox((x, y), text, font=font, anchor="mt")
    pad = 8
    rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    if rect[3] > 1600 or rect[2] > 880 or rect[1] < CARD[1] or rect[3] > CARD[3] - 4:
        return
    for layer in getattr(img, "text_layers", None) or []:
        if layer.get("box") and not layer.get("faint") and safe_area.intersects(rect, layer["box"]):
            return
    for shape in getattr(img, "shapes", None) or []:
        if shape.get("box") and safe_area.intersects(rect, shape["box"]):
            return
    _text(img, x, y, text, K.YELLOW, size, anchor="mt", role="lesson")


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


def _ladder_labels(img, lesson, y_of, sl_lesson, target_lesson):
    """Every level keeps its price. Early SL/target words are the chart scale."""
    rows = (
        (lesson["entry"], K.TARGET_GREEN, f"एंट्री  {lesson['entry']}", "lesson"),
        (lesson["sl"], K.SL_RED, f"एसएल  {lesson['sl']}", "lesson" if sl_lesson else "axis"),
        (lesson["target"], K.BLUE, f"ऊपर  {lesson['target']}", "lesson" if target_lesson else "axis"),
    )
    for price, col, label, role in rows:
        y = y_of(float(price))
        _text(img, 150, y - 8, label, col, 34, anchor="lm", role=role)


def _ladder_lines(img, lesson, y_of):
    entry, sl, target, _ = _ladder_ys(lesson)
    draw = ImageDraw.Draw(img)
    draw.line([(500, y_of(target) - 36), (500, y_of(sl) + 36)], fill=(88, 100, 120), width=3)
    colors = ((entry, K.TARGET_GREEN), (sl, K.SL_RED), (target, K.BLUE))
    for price, col in colors:
        y = y_of(price)
        draw.line([(560, y), (900, y)], fill=col, width=5)
    return entry, sl, target


def _ladder_moving(lesson, phase):
    """All three prices stay labeled. The distance figure is the final formatted value."""
    phase = max(0.0, min(1.0, float(phase)))
    grown = _ease(phase)
    img = _blank()
    _card(img)
    entry, sl, target, y_of = _ladder_ys(lesson)
    tip = y_of(entry) + (y_of(sl) - y_of(entry)) * grown
    _highlight(img, tip, 56 + 20 * grown)
    _ladder_lines(img, lesson, y_of)
    _bracket(img, 930, y_of(entry), tip, GOLD)
    _band_label(img, tip)
    _text(img, 150, _cy(230), "कीमत की सीढ़ी", K.YELLOW, 44, anchor="lt")
    # The multiple, the ATR, and the distance use the same strings as the caption.
    # A running counter used to print a different rounding while the subtitle
    # already said the final figure.
    _text(
        img, 540, _cy(300), f"{lesson['multiple']} गुना {lesson['atr']}",
        K.YELLOW, 36, anchor="mt",
    )
    # SL is already drawn. Counting it as lesson text keeps this beat matched
    # to the voice once the distance label is hidden.
    _ladder_labels(img, lesson, y_of, True, phase >= 0.70)
    if phase >= 0.9:
        distance = f"दूरी {lesson['distance']} पॉइंट"
    else:
        distance = f"दूरी {lesson['distance']}"
    _text(img, 540, _cy(390), distance, K.YELLOW, 40, anchor="mt", quantity="distance")
    _lower_callout(img, "एंट्री से एसएल")
    _mark_market(img, lesson)
    _source(img, lesson)
    return _finish(img, "ladder")


def _ladder_reveal(lesson, phase):
    """The three prices are on from the first frame. The gold wash stays behind them."""
    phase = max(0.0, min(1.0, float(phase)))
    if phase >= 0.999:
        return _ladder_moving(lesson, 0.0)
    img = _blank()
    _card(img)
    entry, sl, target, y_of = _ladder_ys(lesson)
    # Linear, across the whole ladder. An ease curve arrives early and the
    # last four seconds of the reveal then sit still.
    travel = y_of(sl) + (y_of(target) - y_of(sl)) * phase
    _highlight(img, travel, 64)
    _ladder_lines(img, lesson, y_of)
    _band_label(img, travel)
    _text(img, 150, _cy(230), "कीमत की सीढ़ी", K.YELLOW, 44, anchor="lt")
    _ladder_labels(img, lesson, y_of, False, False)
    if phase >= 0.52:
        _text(
            img, 540, _cy(300), f"{lesson['multiple']} गुना {lesson['atr']}",
            K.YELLOW, 36, anchor="mt",
        )
    elif phase >= 0.50:
        _text(img, 540, _cy(300), f"{lesson['multiple']} गुना", K.YELLOW, 36, anchor="mt")
    if phase >= 0.52:
        _text(
            img, 540, _cy(390), f"दूरी  {lesson['distance']}", K.YELLOW, 40,
            anchor="mt", quantity="distance",
        )
    _lower_callout(img, "एंट्री से एसएल")
    _mark_market(img, lesson)
    _source(img, lesson)
    return _finish(img, "ladder")


def _ladder(lesson, step, total):
    """A single ladder card shows the finished picture. Several cards animate."""
    del step, total
    return _ladder_moving(lesson, 1.0)


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
    left = (96, _cy(280), 500, _cy(780))
    right = (548, _cy(280), 860, _cy(780))
    ax_l = _candles(img, left, bars, lo, hi, len(ohlc))
    ax_r = _candles(img, right, bars, lo, hi, len(ohlc))
    _rule(img, ax_l, tight, K.RED, 1.0)
    _rule(img, ax_r, sl, K.TARGET_GREEN, 1.0)
    K.cross(img, ax_l.x(hit), ax_l.y(tight), 14, K.RED, 5)
    held = min(c[2] for c in ohlc) + 1e-9 >= sl
    if held:
        K.tick(img, ax_r.x(len(ohlc) - 1), max(340, ax_r.y(sl) - 28), 14, K.GREEN, 5)
    _text(img, 300, _cy(210), "गलत", K.RED, 40, anchor="mt")
    _text(img, 760, _cy(210), "सही", K.GREEN, 40, anchor="mt")
    _axis_labels(img, ax_r, steps=3)
    _text(img, 298, _cy(900), "तंग रेखा कट गई", K.RED, 28, anchor="mt")
    _text(img, 760, _cy(900), "एटीआर रेखा रुकी" if held else "एटीआर रेखा", K.GREEN, 28, anchor="mt")
    _lower_callout(img, "तंग बनाम एटीआर")
    # Karein already burns the grey disclaimer in the brand band. The as-of
    # date stays inside the card so it does not sit on that line.
    _text(
        img, 540, 250, f"{lesson['as_of']}   एटीआर {lesson['atr']}",
        K.MUTED, 24, anchor="mt", role="source",
    )
    _mark_market(img, lesson)
    return _finish(img, "split")


def _size(lesson):
    """This slice says the tight line is cut by the ATR swing, not the rupee total."""
    img = _blank()
    _card(img)
    _accent(img, [220, _cy(860), 860, _cy(860) + 12])
    _text(img, 540, _cy(300), "तंग रेखा", K.RED, 72, anchor="mt")
    _text(img, 540, _cy(480), f"{lesson['atr']} की हलचल", K.YELLOW, 56, anchor="mt")
    _text(img, 540, _cy(660), "रेखा कटती है", K.INK, 52, anchor="mt")
    _text(img, 540, _cy(820), "शोर में तंग एसएल", K.MUTED, 36, anchor="mt")
    _lower_callout(img, "शोर काटता है")
    return _finish(img, "size")


def _takeaway(lesson):
    img = _blank()
    _card(img)
    _accent(img, [240, _cy(300), 840, _cy(300) + 10])
    _text(img, 540, _cy(240), f"एटीआर {lesson['atr']}", K.MUTED, 32, anchor="mt")
    _text(img, 540, _cy(400), "दूरी", K.YELLOW, 56, anchor="mt")
    _text(img, 540, _cy(540), str(lesson["distance"]), K.YELLOW, 120, anchor="mt", quantity="distance")
    _text(img, 540, _cy(740), "पॉइंट", K.INK, 48, anchor="mt")
    _text(img, 540, _cy(880), f"मात्रा {lesson['qty']}", K.INK, 36, anchor="mt")
    _lower_callout(img, "मात्रा इसी से")
    return _finish(img, "takeaway")


def _rupees(value):
    """Same integer the narration speaks. A thousands comma would not match 2350."""
    return "₹" + market_data.format_figure(str(value).replace(",", ""), 0)


def _cta_at(lesson, step=0, total=1, phase=1.0):
    """One recap card. The second still adds the education line and a large poll."""
    del phase  # the two cards differ by content, not by an empty growing bar
    img = _blank()
    _card(img)
    sl_line = f"एसएल = भाव − {lesson['multiple']} गुना एटीआर = {lesson['sl']}"
    risk_line = f"{lesson['qty']} शेयर पर जोखिम {_rupees(lesson['risk'])}"
    distance_line = f"दूरी {lesson['distance']}"
    # The first card centers the two facts. The second keeps them up so the poll fits.
    later = total > 1 and step >= 1
    _text(img, 540, _cy(200 if later else 360), distance_line, K.YELLOW, 36, anchor="mt")
    _text(img, 540, _cy(280 if later else 520), sl_line, K.INK, 36, anchor="mt")
    _text(img, 540, _cy(460 if later else 760), risk_line, K.YELLOW, 52 if not later else 48, anchor="mt")
    if later:
        _text(img, 540, _cy(640), "शिक्षा है, सलाह नहीं", K.INK, 48, anchor="mt")
        import engagement
        poll = engagement.engagement_question({"cat": "INDICATOR"})
        head, _, tail = poll.partition("? ")
        _text(img, 540, _cy(800), head + "?", K.YELLOW, 48, anchor="mt", role="cta")
        _text(img, 540, _cy(920), tail or poll, K.YELLOW, 48, anchor="mt", role="cta")
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


def _stamp_teaching_card(frame, topic, beat, spec):
    """Put उदाहरण and the code-computed figures on the udaharan and galti cards.

    A non-numeric card stays free of digits. Real market scenes still get the
    label, and only the sourced figures, so a spoken example matches the frame.
    """
    if beat not in hook_check.TEACHING_BEATS:
        return
    if (spec or {}).get("mode") == "non_numeric":
        return
    spoken = str(((topic or {}).get("_spoken_beats") or {}).get(beat) or "")
    _head, detail = hook_check.example_card_lines(topic, spoken)
    if not str(detail or "").strip():
        return
    _text(frame, 540, safe_area.CONTENT_TOP + 28, detail, GOLD, 30, anchor="mt")


def _wants_filled_plate(topic):
    """Four-beat live cards must fill the frame. Six-beat and evergreen stay put."""
    if not isinstance(topic, dict) or not topic.get("_live_short") or topic.get("_atr"):
        return False
    raw = str(topic.get("_live_beats") or os.environ.get("LIVE_BEATS", "")).strip().lower()
    return raw != "six"


_CARD_FOLDS = (
    ("daily loss limit", "दिन की नुकसान हद"),
    ("daily limit", "दिन की हद"),
    ("win rate", "जीत दर"),
    ("expectancy", "अपेक्षा"),
    ("trading days", "कारोबारी दिन"),
    ("overtrading", "ज़्यादा ट्रेड"),
    ("journal", "जर्नल"),
    ("terminal", "टर्मिनल"),
    ("charges", "शुल्क"),
    ("revenge trading", "बदले का ट्रेड"),
)
_FIGURE_RE = re.compile(
    r"₹\s?\d[\d,]*(?:\.\d+)?(?:\s*लाख)?"
    r"|\d[\d,]*(?:\.\d+)?\s*%"
    r"|\+\s?\d+(?:\.\d+)?\s*R"
    r"|\d+(?:\.\d+)?\s*R"
    r"|\d{1,3}(?:,\d{2,3})+"
    r"|\d+(?:\.\d+)?\s*लाख"
)
_BEAT_HEAD = {
    "hook": "एक बात",
    "pehchan": "पहचान",
    "matlab": "हिसाब",
    "karein": "जाँच",
}
_BEAT_SHIFT = {"hook": 0, "pehchan": 1, "matlab": 2, "karein": 3}


def _fold_card_text(text):
    out = " ".join(str(text or "").split())
    for src, dst in _CARD_FOLDS:
        out = re.sub(re.escape(src), dst, out, flags=re.I)
    return out


def topic_display_lines(topic):
    """Fact lines for this topic only. The shared English template is not one of them."""
    lines = []

    def add(raw):
        text = _fold_card_text(raw)
        if not text or text in lines:
            return
        if "शिक्षा है" in text or "सलाह नहीं" in text:
            return
        lines.append(text)

    for fact in (topic or {}).get("facts") or []:
        add(fact)
    beats = (topic or {}).get("beats") or {}
    for beat in ("hook", "pehchan", "matlab", "karein"):
        pair = beats.get(beat)
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            add(pair[1])
    topic_line = str((topic or {}).get("topic") or "")
    if "—" in topic_line:
        add(topic_line.split("—", 1)[1])
    elif " - " in topic_line:
        add(topic_line.split(" - ", 1)[1])
    return lines


def _figure_tokens(lines):
    found = []
    for line in lines or []:
        for match in _FIGURE_RE.finditer(line):
            token = " ".join(match.group(0).split())
            if token and token not in found:
                found.append(token)
    return found


def _digit_tokens(text):
    """Digits the sync gate will read out of this line, including Hindi number-words."""
    import sync_gate
    return {tok for tok in sync_gate.tokens(text) if tok[:1].isdigit()}


def _numbers_ok(label, allowed):
    """True when every raw digit in `label` is already in this scene's outline."""
    import sync_gate
    nums = sync_gate.numbers_in(label)
    return bool(nums) and all(num in allowed for num in nums)


def _indian_rupee(value):
    text = str(int(value))
    if len(text) <= 3:
        body = text
    else:
        head, tail = text[:-3], text[-3:]
        parts = [tail]
        while head:
            parts.append(head[-2:])
            head = head[:-2]
        body = ",".join(reversed(parts))
    return "₹" + body


def _spoken_figures(text, allowed):
    """Big figures for numbers this slice actually says. No digit from a later beat."""
    import sync_gate
    found = []

    def add(label):
        label = " ".join(str(label or "").split())
        if label and label not in found and _numbers_ok(label, allowed):
            found.append(label)

    for token in _figure_tokens([text]):
        add(token)
    raw = sync_gate._tokenize(sync_gate._alias(text))
    i = 0
    while i < len(raw):
        tok = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        if tok in sync_gate._ONES and nxt in sync_gate._SCALE:
            add(_indian_rupee(sync_gate._ONES[tok] * sync_gate._SCALE[nxt]))
            i += 2
            continue
        if tok in sync_gate._FRACTION and nxt in sync_gate._SCALE:
            add(_indian_rupee(sync_gate._FRACTION[tok] * sync_gate._SCALE[nxt]))
            i += 2
            continue
        if tok in sync_gate._ONES and nxt in ("प्रतिशत", "percent"):
            add(f"{sync_gate._ONES[tok]}%")
            i += 2
            continue
        if tok in sync_gate._FRACTION and nxt in ("प्रतिशत", "percent"):
            val = sync_gate._FRACTION[tok]
            add(f"{int(val) if float(val).is_integer() else val}%")
            i += 2
            continue
        i += 1
    return found


def _slice_words(words, index, n):
    """The words of equal scene `index`, matching scene_pace's even time split."""
    words = list(words or [])
    if not words:
        return []
    n = max(1, int(n))
    index = max(0, min(int(index), n - 1))
    start = int(round(index / n * len(words)))
    end = int(round((index + 1) / n * len(words)))
    if end <= start:
        end = min(len(words), start + 1)
    return words[start:end]


def _outline_card_text(topic, beat):
    """This beat's own outline, plus a fact that shares one of its numbers."""
    import sync_gate
    parts = []
    pair = ((topic or {}).get("beats") or {}).get(beat)
    if isinstance(pair, (list, tuple)):
        for piece in pair:
            text = _fold_card_text(piece)
            if text and text not in parts:
                parts.append(text)
    blob = " ".join(parts)
    beat_nums = _digit_tokens(blob) | set(sync_gate.numbers_in(blob))
    for line in topic_display_lines(topic):
        line_nums = _digit_tokens(line) | set(sync_gate.numbers_in(line))
        if beat_nums and line_nums and (beat_nums & line_nums) and line not in parts:
            parts.append(line)
    if not parts:
        lines = topic_display_lines(topic)
        if lines:
            parts.append(lines[0])
    return " ".join(parts)


def _scene_card_text(topic, beat, index, n):
    """Words this scene says. A caption window wins over an even word split."""
    cards = (topic or {}).get("_scene_cards")
    if isinstance(cards, dict) and beat in cards and index < len(cards[beat]):
        return str(cards[beat][index])
    spoken = _fold_card_text(((topic or {}).get("_spoken_beats") or {}).get(beat) or "")
    if spoken:
        return " ".join(_slice_words(spoken.split(), index, n))
    return _outline_card_text(topic, beat)


def _scene_figure_list(topic, beat, index, card, spoken):
    """Figures for this scene. An installed list may be empty after dedupe."""
    preset = (topic or {}).get("_scene_figures")
    if isinstance(preset, dict) and beat in preset and index < len(preset[beat]):
        return list(preset[beat][index])
    allowed = _digit_tokens(card)
    return _spoken_figures(card, allowed) if spoken else _figure_tokens([card])


def _summary_line(figures):
    """A labelled figure line, not a bare number. Recovery pairs use the formula."""
    perc = []
    for fig in figures or []:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(fig))
        if match:
            perc.append((str(fig), float(match.group(1))))
    if len(perc) >= 2 and 0 < perc[0][1] < 100:
        expected = 100.0 * perc[0][1] / (100.0 - perc[0][1])
        if abs(perc[1][1] - expected) <= 0.51:
            return f"नुकसान {perc[0][0]} से वापसी +{perc[1][0]}"
    if figures:
        return "आंकड़ा " + " · ".join(str(fig) for fig in figures[:3])
    return ""


def _chart_label(fig, index):
    """A label with no extra digit. An index like 1. would show before it is said."""
    text = " ".join(str(fig or "").split())
    if not text:
        return ""
    if not re.search(r"[\u0900-\u097F]", text):
        text = ("मान", "सीमा", "राशि", "कदम")[index % 4] + " " + text
    return text[:24]


def panel_fill_fraction(image):
    """Share of the card panel covered by text.

    Chart bars do not count, labelled or not. A single full-width bar used
    to clear 35% with almost no words. Empty navy inside the panel fails
    below about 35%.
    """
    panel = getattr(image, "panel_box", None)
    if not panel:
        return 1.0
    x0, y0, x1, y1 = (float(v) for v in panel)
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)
    mask = np.zeros((int(height), int(width)), dtype=bool)
    boxes = []
    for layer in getattr(image, "text_layers", None) or []:
        if layer.get("faint") or not layer.get("text") or not layer.get("box"):
            continue
        boxes.append(layer["box"])
    boxes.extend(getattr(image, "labelled_graphic_boxes", None) or [])
    for box in boxes:
        left = int(max(0, min(width, float(box[0]) - x0)))
        top = int(max(0, min(height, float(box[1]) - y0)))
        right = int(max(0, min(width, float(box[2]) - x0)))
        bottom = int(max(0, min(height, float(box[3]) - y0)))
        if right > left and bottom > top:
            mask[top:bottom, left:right] = True
    return float(mask.mean())


def _figure_value(fig):
    raw = str(fig or "").replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", raw)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _chart_rows(figures):
    """(label, value) for a real chart. One figure is not a chart."""
    rows = []
    for fig in figures or []:
        value = _figure_value(fig)
        if value is None or value <= 0:
            continue
        label = _chart_label(fig, len(rows))
        if not label:
            continue
        rows.append((label, value))
    return rows[:4]


def _paint_labelled_chart(image, figures, panel, text_bottom=None):
    """Two or more bars, scaled to the values, each labelled with its figure.

    A single label draws nothing. Bars are not added to the fill boxes.
    """
    rows = _chart_rows(figures)
    if len(rows) < 2:
        return
    x0, y0, x1, y1 = (int(v) for v in panel)
    n = len(rows)
    gap = 28
    inner = (x1 - x0) - 80
    bar_w = max(80, int((inner - gap * (n - 1)) / n))
    max_h = int((y1 - y0) * 0.22)
    min_h = max(36, int((y1 - y0) * 0.07))
    base = y1 - 72
    zone_top = base - max_h
    if text_bottom is not None and float(text_bottom) > zone_top - 16:
        return
    peak = max(value for _label, value in rows) or 1.0
    colors = ((96, 165, 250), (34, 197, 94), (250, 204, 21), (239, 68, 68))
    draw = ImageDraw.Draw(image)
    bars = []
    left = x0 + 40
    for i, (label, value) in enumerate(rows):
        frac = max(0.0, min(1.0, value / peak))
        bar_h = int(min_h + (max_h - min_h) * frac)
        top = base - bar_h
        x = left + i * (bar_w + gap)
        draw.rounded_rectangle([x, top, x + bar_w, base], 16, fill=colors[i % 4])
        bars.append((float(x), float(top), float(x + bar_w), float(base), float(value)))
        _text(image, x + bar_w / 2, base + 8, label, (255, 255, 255), 28, anchor="mt")
    image.chart_bar_boxes = bars


def _wrap_words(text, limit=18):
    words = str(text or "").split()
    rows, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if cur and len(trial) > limit:
            rows.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        rows.append(cur)
    return rows


def _card_sentences(text):
    text = " ".join(str(text or "").split())
    if not text:
        return []
    parts = re.split(r"(?<=[।!?？])\s+", text)
    out = []
    for part in parts:
        out.extend(bit.strip() for bit in re.split(r"(?<=\.)\s+", part) if bit.strip())
    return [bit for bit in out if bit] or [text]


def phrases_that_fit(text, limit, max_lines):
    """Whole sentences that fit in max_lines. A sentence that does not fit is not started."""
    shown = []
    used = 0
    for sentence in _card_sentences(text):
        rows = _wrap_words(sentence, limit)
        if not rows:
            continue
        if used and used + len(rows) > max_lines:
            break
        if not used and len(rows) > max_lines:
            break
        shown.extend(rows)
        used += len(rows)
    return shown


def frame_card_text(frame):
    return " ".join(
        str(layer.get("text") or "")
        for layer in (getattr(frame, "text_layers", None) or [])
        if layer.get("text") and not layer.get("faint")
    )


def frames_safe_area_notes(frames):
    """Boxes that leave the 6% frame margin. Empty when every recorded label fits."""
    notes = []
    for frame in frames or []:
        boxes = getattr(frame, "label_boxes", None) or []
        if not boxes:
            continue
        width, height = frame.size
        ok, bad = safe_area.check_boxes(boxes, width, height)
        if ok:
            continue
        sample = ", ".join(
            f"({b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f})" for b in bad[:3]
        )
        notes.append(f"label outside the 6% safe area: {sample}")
    return notes


def _text_extent(text, size):
    """Pixel width and height of one shaped line at `size`."""
    runs = _runs(text)
    if not runs:
        return 0, 0
    if len(runs) == 1:
        font = _font(int(size), runs[0][0])
        box = ImageDraw.Draw(Image.new("RGB", (8, 8))).textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    _parts, union = _layout_runs(text, int(size))
    return union[2] - union[0], union[3] - union[1]


def _text_width(text, size):
    return _text_extent(text, size)[0]


def _phrase_rows(text, size, max_width=900):
    """Whole sentences wrapped so each line fits at `size`. Orphans stay with the line above."""
    rows = []
    for sentence in _card_sentences(text):
        words = str(sentence or "").split()
        cur = ""
        for word in words:
            trial = (cur + " " + word).strip()
            if cur and _text_width(trial, size) > max_width:
                rows.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            rows.append(cur)
    if len(rows) >= 2 and len(rows[-1].split()) <= 2:
        joined = rows[-2] + " " + rows[-1]
        if _text_width(joined, size) <= max_width + 40:
            rows[-2] = joined
            rows.pop()
    return rows


def _tight_panel(boxes, pad):
    """A card around the words, not a navy field with a caption in the corner."""
    left, top, right, bottom = safe_area.limits()
    top = max(top, safe_area.CONTENT_TOP)
    bottom = min(bottom, safe_area.CONTENT_BOTTOM)
    x0 = min(float(box[0]) for box in boxes) - pad
    y0 = min(float(box[1]) for box in boxes) - pad
    x1 = max(float(box[2]) for box in boxes) + pad
    y1 = max(float(box[3]) for box in boxes) + pad
    return (
        max(left, x0),
        max(top, y0),
        min(right, x1),
        min(bottom, y1),
    )


def _seat_panel(image, panel, color):
    """Draw the card behind the sprites already recorded on `image`."""
    width, height = image.size
    final = _live_plate(width, height)
    draw = ImageDraw.Draw(final)
    draw.rounded_rectangle(
        [int(panel[0]), int(panel[1]), int(panel[2]), int(panel[3])],
        28, fill=color, outline=GOLD, width=4,
    )
    for layer in image.text_layers or []:
        sprite = layer.get("image")
        xy = layer.get("xy")
        if sprite is None or xy is None:
            continue
        final.paste(sprite, xy, sprite)
    final.text_layers = list(image.text_layers or [])
    final.label_boxes = list(getattr(image, "label_boxes", None) or [])
    final.shapes = list(getattr(image, "shapes", None) or [])
    final.labelled_graphic_boxes = []
    final.chart_bar_boxes = list(getattr(image, "chart_bar_boxes", None) or [])
    final.panel_box = tuple(panel)
    final.scene_kind = getattr(image, "scene_kind", "")
    final.panel_fill = panel_fill_fraction(final)
    return final


def _paint_live_card(topic, beat, index, n, body_size=None):
    """Headline at least 64px. The card hugs the phrase so empty navy is not the panel.

    Body lines are whole sentences. A chart is drawn only for two or more digit
    figures, and its bars are not part of the fill.
    """
    del body_size  # size is chosen so the whole phrase fits
    width, height = K.W, K.H
    shift = (_BEAT_SHIFT.get(beat, 0) + int(index)) % 4
    panel_color = (
        (22, 48, 96),
        (30, 58, 110),
        (18, 52, 88),
        (26, 42, 102),
    )[shift]
    card = _scene_card_text(topic, beat, index, n)
    spoken = bool(str(((topic or {}).get("_spoken_beats") or {}).get(beat) or "").strip())
    figures = _scene_figure_list(topic, beat, index, card, spoken)
    summary = _summary_line(figures)
    # The summary repeats the card when the card already shows those figures.
    if summary and summary not in card:
        phrase = f"{summary} {card}".strip()
    else:
        phrase = card
    head = _BEAT_HEAD.get(beat, "बात")
    head_size = 72
    content_top = safe_area.CONTENT_TOP + 36
    content_bottom = safe_area.CONTENT_BOTTOM - 28
    def _unique_lines(trial):
        seen = set()
        for row in trial:
            key = normalize_label(row)
            if not key:
                continue
            if key in seen:
                return False
            seen.add(key)
        return True

    chosen = 64
    rows = _phrase_rows(phrase, chosen) if phrase else []
    for size in (168, 152, 136, 120, 104, 88, 72, 64):
        trial = _phrase_rows(phrase, size) if phrase else []
        if any(_text_width(row, size) > 920 for row in trial):
            continue
        if not _unique_lines(trial):
            continue
        block = _text_extent(head, head_size)[1] + 24
        block += sum(_text_extent(row, size)[1] + 24 for row in trial)
        if content_top + block <= content_bottom:
            chosen = size
            rows = trial
            break
    image = _live_plate(width, height)
    image.labelled_graphic_boxes = []
    image.chart_bar_boxes = []
    head_h = _text_extent(head, head_size)[1]
    block_h = head_h + 24 + sum(_text_extent(row, chosen)[1] + 24 for row in rows)
    y = content_top + max(0, (content_bottom - content_top - block_h) / 2)
    _text(image, 540, y, head, GOLD, head_size, anchor="mt")
    y += head_h + 24
    for row in rows:
        row_h = _text_extent(row, chosen)[1]
        _text(image, 540, y, row, (250, 250, 250), chosen, anchor="mt")
        y += row_h + 24
    image.scene_kind = f"fact-{beat}"
    boxes = [
        layer["box"] for layer in (image.text_layers or [])
        if layer.get("text") and layer.get("box") and not layer.get("faint")
    ]
    if not boxes:
        image.panel_box = (70, 190, width - 70, 1300)
        image.panel_fill = panel_fill_fraction(image)
        return image
    seated = None
    for pad in (72, 56, 44, 32, 24):
        panel = _tight_panel(boxes, pad)
        trial = _seat_panel(image, panel, panel_color)
        seated = trial
        if trial.panel_fill + 1e-9 >= 0.35:
            break
    # Bars sit under the card. They are not part of the panel, so they cannot
    # satisfy the 35% fill. Two figures or none.
    room_top = float(seated.panel_box[3]) + 16
    room_bottom = min(content_bottom, room_top + 260)
    if len(figures) >= 2 and room_bottom - room_top >= 160:
        _paint_labelled_chart(
            seated, figures, (90, room_top, width - 90, room_bottom),
            text_bottom=room_top,
        )
    return seated


def _live_fact_frame(topic, beat, index, n=1):
    """One card of the words this scene says. No zoom and no second title.

    The headline is at least 64px on a 1080-wide frame. Short phrases are drawn
    whole. A chart is two or more bars scaled to the figures, or it is absent.
    The bars do not count toward the 35% panel fill.
    """
    return _paint_live_card(topic, beat, index, n)


def _live_plate(width, height):
    """Slate chart field. Every row is brighter than the dark-row cutoff."""
    ys = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    top = np.array([38, 62, 108], dtype=np.float32)
    bot = np.array([28, 52, 96], dtype=np.float32)
    rgb = np.repeat((top + (bot - top) * ys)[:, None, :], width, axis=1)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    grid = (78, 112, 158)
    for x in range(0, width, 54):
        draw.line([(x, 0), (x, height)], fill=grid, width=1)
    for y in range(0, height, 46):
        draw.line([(0, y), (width, y)], fill=grid, width=1)
    return img


def _paint_bottom_band(image, beat, index):
    """Chart footer in the bottom 30%. The lesson text stays above it."""
    width, height = image.size
    y0 = int(round(height * 0.70))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, y0, width, height - 1], fill=(36, 64, 118))
    draw.rectangle([0, y0, width, y0 + 10], fill=(212, 168, 72))
    colors = ((96, 165, 250), (34, 197, 94), (250, 204, 21), (239, 68, 68))
    base = {"hook": 0.42, "pehchan": 0.58, "matlab": 0.50, "karein": 0.66}.get(beat, 0.5)
    for i, col in enumerate(colors):
        frac = base + ((index + i) % 4) * 0.06
        bar_h = int(height * (0.08 + 0.10 * frac))
        x = 120 + i * 220
        top = max(y0 + 28, height - 140 - bar_h)
        draw.rounded_rectangle([x, top, x + 150, height - 130], 18, fill=col)
    rail = colors[index % len(colors)]
    rx = 28 + (index % 5) * 14
    draw.rectangle([rx, 96, rx + 16, y0 - 16], fill=rail)


def fill_live_frame(src, beat, index=0):
    """Seat the existing chart in the middle and fill the void around it.

    The old board ends near y=1115 on a near-black gradient, so the opening
    card is about 34% dark rows and craft scores 0. Marks already on the
    chart stay. Everything darker than the cutoff becomes the slate plate,
    and the bottom 30% is a bar footer.
    """
    image = src.convert("RGB")
    width, height = image.size
    arr = np.asarray(image)
    shift = 72
    seated = np.zeros_like(arr)
    seated[shift:, :, :] = arr[: height - shift, :, :]
    plate = np.asarray(_live_plate(width, height))
    void = seated.max(axis=2) <= 80
    seated[void] = plate[void]
    out = Image.fromarray(seated, "RGB")
    _paint_bottom_band(out, beat, index)
    return out


def _generic_frames(topic, beat, n, spec):
    if _wants_filled_plate(topic):
        frames = [_live_fact_frame(topic, beat, i, n) for i in range(n)]
        return frames, [False] * n
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
        _stamp_teaching_card(frame, topic, beat, spec)
        frames.append(frame)
        flags.append(flag)
    return frames, flags


def frames_for(topic, beat, n, spec):
    """Return (images, callout flags), one per planned scene in this beat."""
    n = max(1, int(n))
    if (spec or {}).get("atr"):
        return _atr_frames(beat, n, spec["atr"])
    return _generic_frames(topic, beat, n, spec)
