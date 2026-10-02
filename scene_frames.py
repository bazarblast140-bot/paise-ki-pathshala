"""Full-screen 9:16 scenes. ATR is seven different cards, not one zoomed chart.

The candlestick chart is only the problem scene and the wrong-vs-right split.
Other scenes are type, a formula, a price ladder, a position-size card, and
a takeaway. Labels are recorded so the 6% safe-area gate can reject a crop.
"""
import os

import board_kit as K
import board_scenes as S
import market_data
import safe_area
from PIL import Image, ImageDraw, ImageFont

_FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"
_font_cache = {}
CHART_KINDS = ("problem", "split")


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


def _blank():
    img = K.background()
    img.label_boxes = []
    img.scene_kind = ""
    return img


def _text(img, x, y, text, fill, size, anchor="lt"):
    """Draw one label and record its padded box. Shrink or shift to stay in the picture band."""
    d = ImageDraw.Draw(img)
    pad = 6
    left, top, right, bottom = safe_area.limits()
    top = max(top, safe_area.CONTENT_TOP)
    bottom = min(bottom, safe_area.CONTENT_BOTTOM)
    size = int(size)
    x, y = float(x), float(y)
    rect = (left, top, left + 1, top + 1)
    while size >= 16:
        font = _font(size)
        box = d.textbbox((x, y), text, font=font, anchor=anchor)
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
            box = d.textbbox((x, y), text, font=font, anchor=anchor)
            rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        if rect[0] >= left - 0.2 and rect[2] <= right + 0.2 and rect[1] >= top - 0.2 and rect[3] <= bottom + 0.2:
            break
        size -= 2
    d.text((x, y), text, font=_font(max(size, 16)), fill=fill, anchor=anchor)
    img.label_boxes.append(tuple(rect))
    return rect


def _finish(img, kind):
    img.scene_kind = kind
    return img


def _stage(img, fill=None):
    if fill:
        ImageDraw.Draw(img).rectangle([0, 0, K.W, K.H], fill=fill)
    K.panel(img, box=(72, 188, 1008, 1048), radius=32)


def _source(img, lesson):
    _text(img, 540, 988, f"Yahoo Finance   {lesson['as_of']}", K.MUTED, 26, anchor="mt")


def _ohlc(bars):
    return [
        (float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))
        for b in bars
    ]


def _candles(img, box, ohlc, lo, hi, count):
    ax = K.Axes(box, len(ohlc), lo, hi, pad=(24, 16, 24, 16), ypad=0.06)
    ax.grid(img)
    d = ImageDraw.Draw(img)
    bw = max(8, int(ax.step * 0.5))
    for i, (o, h, low, c) in enumerate(ohlc[:count]):
        col = K.GREEN if c >= o else K.RED
        cx = ax.x(i)
        d.line([(cx, ax.y(h)), (cx, ax.y(low))], fill=col, width=max(3, bw // 5))
        top, bot = ax.y(max(o, c)), ax.y(min(o, c))
        d.rectangle([cx - bw / 2, top, cx + bw / 2, max(bot, top + 4)], fill=col)
    return ax


def _rule(img, ax, price, color, frac=1.0):
    d = ImageDraw.Draw(img)
    y = ax.y(price)
    x1 = ax.x0 + 12 + (ax.x1 - ax.x0 - 24) * max(0.08, min(1.0, frac))
    d.line([(ax.x0 + 12, y), (x1, y)], fill=color, width=6)


def _hook(lesson):
    img = _blank()
    _stage(img, fill=(10, 14, 26))
    ImageDraw.Draw(img).rectangle([120, 250, 960, 268], fill=K.YELLOW)
    _text(img, 540, 320, "तंग एसएल", K.YELLOW, 96, anchor="mt")
    _text(img, 540, 500, "बार-बार कटता क्यों?", K.INK, 52, anchor="mt")
    _text(img, 540, 680, "सामान्य हलचल", K.INK, 44, anchor="mt")
    _text(img, 540, 780, "तंग रेखा को काट देती है", K.INK, 40, anchor="mt")
    _text(img, 540, 920, "एटीआर इस हलचल को नापता है", K.YELLOW, 34, anchor="mt")
    return _finish(img, "hook")


def _problem(lesson, step, total):
    img = _blank()
    _stage(img)
    bars = lesson["wick_bars"]
    ohlc = _ohlc(bars)
    dates = [str(b["date"])[:10] for b in bars]
    hit = dates.index("2026-06-19") if "2026-06-19" in dates else max(0, len(ohlc) - 1)
    show_hit = total <= 1 or step >= total - 1
    count = len(ohlc) if show_hit else max(1, hit)
    tight = float(lesson["tight"])
    lo = min(min(c[2] for c in ohlc), tight)
    hi = max(max(c[1] for c in ohlc), tight)
    _text(img, 100, 214, "समस्या", K.YELLOW, 40, anchor="lt")
    _text(img, 100, 270, "एचडीएफसी बैंक दैनिक", K.INK, 28, anchor="lt")
    ax = _candles(img, (96, 360, 984, 930), ohlc, lo, hi, count)
    if show_hit:
        _rule(img, ax, tight, K.RED, 1.0)
        K.cross(img, ax.x(hit), ax.y(tight), 20, K.RED, 7)
        _text(img, 620, 214, "तंग रेखा कट गई", K.RED, 32, anchor="lt")
    else:
        _text(img, 620, 214, "कैंडल दर कैंडल", K.MUTED, 32, anchor="lt")
    _source(img, lesson)
    return _finish(img, "problem")


def _formula(lesson, step, total):
    img = _blank()
    _stage(img)
    if total <= 1:
        phase = 2
    elif total == 2:
        phase = 0 if step == 0 else 2
    else:
        phase = min(2, step)
    _text(img, 540, 230, "एटीआर क्या है", K.YELLOW, 60, anchor="mt")
    _text(img, 540, 330, "पिछले कैंडल की औसत हलचल", K.INK, 34, anchor="mt")
    if phase == 0:
        _text(img, 540, 480, str(lesson["period"]), K.YELLOW, 140, anchor="mt")
        _text(img, 540, 700, "कैंडल की औसत", K.INK, 44, anchor="mt")
    elif phase == 1:
        _text(img, 540, 460, "औसत", K.MUTED, 36, anchor="mt")
        _text(img, 540, 540, lesson["atr"], K.YELLOW, 120, anchor="mt")
        _text(img, 540, 740, "पॉइंट हलचल", K.INK, 40, anchor="mt")
    else:
        _text(img, 540, 470, f"{lesson['atr']}  ×  {lesson['multiple']}", K.INK, 52, anchor="mt")
        _text(img, 540, 580, f"=  {lesson['distance']}", K.YELLOW, 84, anchor="mt")
        _text(img, 540, 740, "पॉइंट की दूरी", K.INK, 40, anchor="mt")
    _text(img, 540, 880, "दिशा नहीं, सिर्फ़ हलचल", K.MUTED, 32, anchor="mt")
    _source(img, lesson)
    return _finish(img, "formula")


def _ladder(lesson, step, total):
    img = _blank()
    _stage(img)
    _text(img, 100, 214, "कीमत की सीढ़ी", K.YELLOW, 44, anchor="lt")
    entry, sl = float(lesson["entry"]), float(lesson["sl"])
    target = float(lesson["target"])
    reveal = 4 if total <= 1 else min(4, step + 1)
    d = ImageDraw.Draw(img)
    d.line([(200, 380), (200, 930)], fill=K.MUTED, width=4)
    span = (target - sl) or 1.0

    def y_of(price):
        return 900 - (price - sl) / span * 480

    rows = (
        (entry, K.TARGET_GREEN, f"एंट्री  {lesson['entry']}", 1),
        (sl, K.SL_RED, f"एसएल  {lesson['sl']}", 2),
        (target, K.BLUE, f"ऊपर  {lesson['target']}", 3),
    )
    for price, col, label, need in rows:
        if reveal < need:
            continue
        y = y_of(price)
        frac = 1.0 if need < reveal or step == total - 1 else 0.62
        d.line([(220, y), (220 + 620 * frac, y)], fill=col, width=8)
        if frac > 0.9:
            _text(img, 240, y - 46, label, col, 34, anchor="lt")
    if reveal >= 4:
        _text(img, 540, 300, f"दूरी  {lesson['distance']} पॉइंट", K.YELLOW, 32, anchor="mt")
    return _finish(img, "ladder")


def _split(lesson):
    img = _blank()
    _stage(img)
    _text(img, 300, 214, "गलत", K.RED, 40, anchor="mt")
    _text(img, 780, 214, "सही", K.GREEN, 40, anchor="mt")
    bars = lesson["wick_bars"]
    ohlc = _ohlc(bars)
    dates = [str(b["date"])[:10] for b in bars]
    hit = dates.index("2026-06-19") if "2026-06-19" in dates else len(ohlc) - 1
    tight, sl = float(lesson["tight"]), float(lesson["sl"])
    lo = min(min(c[2] for c in ohlc), sl, tight)
    hi = max(max(c[1] for c in ohlc), tight)
    ax_l = _candles(img, (88, 300, 520, 880), ohlc, lo, hi, len(ohlc))
    ax_r = _candles(img, (560, 300, 992, 880), ohlc, lo, hi, len(ohlc))
    _rule(img, ax_l, tight, K.RED, 1.0)
    _rule(img, ax_r, sl, K.TARGET_GREEN, 1.0)
    K.cross(img, ax_l.x(hit), ax_l.y(tight), 16, K.RED, 6)
    held = min(c[2] for c in ohlc) + 1e-9 >= sl
    if held:
        K.tick(img, ax_r.x(len(ohlc) - 1), max(320, ax_r.y(sl) - 36), 16, K.GREEN, 6)
    _text(img, 300, 910, "तंग रेखा कट गई", K.RED, 26, anchor="mt")
    _text(img, 780, 910, "एटीआर रेखा रुकी" if held else "एटीआर रेखा", K.GREEN, 26, anchor="mt")
    _source(img, lesson)
    return _finish(img, "split")


def _size(lesson):
    img = _blank()
    _stage(img)
    _text(img, 540, 240, "चौड़ी एसएल", K.YELLOW, 64, anchor="mt")
    _text(img, 540, 360, "कम मात्रा", K.INK, 52, anchor="mt")
    _text(img, 540, 520, f"{lesson['distance']}  ×  {lesson['qty']}", K.INK, 48, anchor="mt")
    _text(img, 540, 640, f"=  {lesson['risk']} रुपये", K.YELLOW, 52, anchor="mt")
    _text(img, 540, 820, "दूरी बढ़े तो मात्रा घटाओ", K.INK, 34, anchor="mt")
    _text(img, 540, 900, "जोखिम रुपये में रहता है", K.MUTED, 30, anchor="mt")
    return _finish(img, "size")


def _takeaway(lesson):
    img = _blank()
    _stage(img)
    _text(img, 540, 260, "याद रखें", K.YELLOW, 48, anchor="mt")
    _text(img, 540, 420, f"{lesson['multiple']} गुना एटीआर", K.YELLOW, 60, anchor="mt")
    _text(img, 540, 580, "तंग रेखा शोर में कटती है", K.INK, 38, anchor="mt")
    _text(img, 540, 700, "चौड़ी दूरी, कम मात्रा", K.INK, 38, anchor="mt")
    _text(img, 540, 880, "शिक्षा है, सलाह नहीं", K.MUTED, 32, anchor="mt")
    return _finish(img, "takeaway")


def _cta(lesson):
    img = _blank()
    _stage(img, fill=(22, 12, 16))
    ImageDraw.Draw(img).rectangle([120, 250, 960, 268], fill=K.YELLOW)
    _text(img, 540, 320, "एक नियम", K.YELLOW, 80, anchor="mt")
    _text(img, 540, 500, f"दूरी {lesson['multiple']} गुना एटीआर", K.INK, 40, anchor="mt")
    _text(img, 540, 660, "फिर खुद तय करें", K.YELLOW, 52, anchor="mt")
    _text(img, 540, 840, "शिक्षा है, सलाह नहीं", K.MUTED, 34, anchor="mt")
    return _finish(img, "cta")


_BUILDERS = {
    "hook": lambda lesson, step, total: _hook(lesson),
    "problem": lambda lesson, step, total: _problem(lesson, step, total),
    "formula": lambda lesson, step, total: _formula(lesson, step, total),
    "ladder": lambda lesson, step, total: _ladder(lesson, step, total),
    "split": lambda lesson, step, total: _split(lesson),
    "size": lambda lesson, step, total: _size(lesson),
    "takeaway": lambda lesson, step, total: _takeaway(lesson),
    "cta": lambda lesson, step, total: _cta(lesson),
}


def _atr_frames(beat, n, lesson):
    kinds = kinds_for(beat, n)
    frames, flags = [], []
    seen = {}
    for kind in kinds:
        step = seen.get(kind, 0)
        total = kinds.count(kind)
        img = _BUILDERS[kind](lesson, step, total)
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
