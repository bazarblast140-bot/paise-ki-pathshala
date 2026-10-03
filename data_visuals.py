"""Animated data visuals for four-beat Shorts.

The chart is the top of the 1080x1920 frame, about 60–65% of the height.
Pills and a short headline sit inside that region. The plot fills most of
it. Under the plot, still inside the region: a yellow beat word and one
short white line. Captions are burned later, under the chart, in the
subtitle band. Nothing here is a full-screen text card.

Palette follows the public reel renderer: slate #020617, neon green and
red, amber callouts. A yellow oval lands on the element the voice is
naming when whisper word times are on the topic.
"""
import math

from PIL import Image, ImageDraw

import safe_area

ORDER = ("hook", "pehchan", "matlab", "karein")
HANDLE = "@ThePaiseKiPathshala"
# Same rectangle as scene_frames.CARD: 1240px is 64.6% of 1920.
REGION = (72, 124, 1008, 1364)
# Plot under the pills and headline, above the beat word.
PLOT = (108, 268, 972, 1148)
SLATE = (2, 6, 23)
PLOT_FILL = (14, 18, 35)
NEON_UP = (34, 197, 94)
NEON_DN = (239, 68, 68)
AMBER = (245, 158, 11)
TEXT = (248, 250, 252)
MUTED = (148, 163, 184)
FRAME = (51, 65, 85)

_RISK_KIND = {
    "hook": "capital",
    "pehchan": "capital",
    "matlab": "intraday",
    "karein": "month",
}
_PILL = {
    "pehchan": "1 PEHCHAN",
    "matlab": "2 MATLAB",
    "karein": "3 KYA KAREIN",
}
_BEAT_WORD = {
    "hook": "LIMIT",
    "pehchan": "PEHCHAN",
    "matlab": "MATLAB",
    "karein": "KAREIN",
}
_MONTH_MOVES = (
    0.25, 0.15, -0.05, 0.20, 0.10, -0.08, 0.18, 0.12, 0.05, -0.10,
    0.22, 0.08, -6.0, 0.16, 0.10, -0.04, 0.14, 0.20, 0.06, 0.12,
)


def _scene_frames():
    import scene_frames
    return scene_frames


def _figures_for(topic, beat, index, n):
    frames = _scene_frames()
    card = frames._scene_card_text(topic, beat, index, n)
    spoken = str(((topic or {}).get("_spoken_beats") or {}).get(beat) or "")
    figures = list(frames._scene_figure_list(topic, beat, index, card, spoken))
    return card, figures


def _value(fig):
    frames = _scene_frames()
    try:
        value = frames._figure_value(fig)
    except Exception:  # noqa: BLE001
        return None
    if value is None:
        return None
    text = str(fig or "")
    if "लाख" in text and value < 1000:
        return value * 100000.0
    if "करोड़" in text and value < 1000:
        return value * 10000000.0
    return float(value)


def _rupees(figures):
    rows = []
    for fig in figures or []:
        text = str(fig)
        if "₹" not in text and "रुप" not in text:
            continue
        value = _value(fig)
        if value is None or value <= 0:
            continue
        rows.append((text, float(value)))
    rows.sort(key=lambda item: item[1], reverse=True)
    kept = []
    seen = set()
    for label, value in rows:
        if value in seen:
            continue
        seen.add(value)
        kept.append((label, value))
    return kept


def _percents(figures):
    rows = []
    for fig in figures or []:
        text = str(fig)
        if "%" not in text:
            continue
        value = _value(fig)
        if value is None or value <= 0:
            continue
        rows.append((text, float(value)))
    return rows


def kind_for(topic, beat, figures):
    """Which reusable chart this beat draws."""
    key = str((topic or {}).get("key") or "")
    if key == "risk_daily_loss_limit":
        return _RISK_KIND.get(beat, "bars")
    rupees = _rupees(figures)
    perc = _percents(figures)
    if len(rupees) >= 2 or len(perc) >= 2 or len(figures or []) >= 2:
        return "bars"
    if figures:
        return "meter"
    return "flow"


def _words(text, limit=5):
    return " ".join(str(text or "").split()[:limit])


def _label(image, x, y, text, fill, size, role="lesson", limit=5, anchor="mt", band=True):
    text = _words(text, limit)
    if not text:
        return None
    frames = _scene_frames()
    return frames._text(
        image, x, y, text, fill, size, anchor=anchor, role=role, band=band,
    )


def _clip(progress):
    return max(0.0, min(1.0, float(progress)))


def _plate():
    frames = _scene_frames()
    image = Image.new("RGB", (frames.K.W, frames.K.H), SLATE)
    image.text_layers = []
    image.label_boxes = []
    image.shapes = []
    image.labelled_graphic_boxes = []
    image.chart_bar_boxes = []
    image.chart_region = tuple(float(v) for v in REGION)
    image.chart_box = tuple(float(v) for v in PLOT)
    return image


def _draw_stage(image):
    """Slate field and one stationary chart box. The border lights every row."""
    draw = ImageDraw.Draw(image)
    draw.rectangle(list(REGION), outline=NEON_UP, width=4)
    draw.rounded_rectangle(list(PLOT), 16, fill=PLOT_FILL, outline=FRAME, width=3)
    # A bright rail so the chart rows are not empty background.
    x = REGION[0] + 8
    draw.line([(x, REGION[1] + 2), (x, REGION[3] - 2)], fill=NEON_UP, width=4)


def _dashed(draw, box, y, fill, width=3):
    x0, x1 = box[0], box[2]
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x1, x + 16), y)], fill=fill, width=width)
        x += 28


def _lesson_line(card):
    """At most five words, and not a line that ends on an operator."""
    words = str(card or "").split()
    cut = words[:5]
    while cut and cut[-1].strip("।.,") in {"×", "x", "=", "+", "-", "−"}:
        cut.pop()
    return " ".join(cut)


def _headline(figures):
    rupees = _rupees(figures)
    perc = _percents(figures)
    if len(rupees) < 2 or not perc:
        return ""
    _capital_label, capital = rupees[0]
    limit_label, limit = rupees[-1]
    if capital <= limit * 8:
        return ""
    short = "₹2L" if abs(capital - 200000.0) < 1.0 else rupees[0][0]
    return _words(f"{perc[0][0]} OF {short} = {limit_label} MAX LOSS", 8)


def _draw_capital(image, rupees, progress):
    """Full capital bar. The limit is a highlighted slice, not a second column."""
    progress = _clip(progress)
    if len(rupees) < 2:
        return {}
    _label_text, capital = rupees[0]
    slice_label, limit = rupees[-1]
    if capital <= limit:
        return {}
    x0, x1 = PLOT[0] + 28, PLOT[2] - 28
    y0, y1 = PLOT[1] + 150, PLOT[3] - 36
    grown = x0 + (x1 - x0) * max(0.04, progress)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([x0, y0, x1, y1], 18, fill=(12, 36, 24))
    draw.rounded_rectangle([x0, y0, int(grown), y1], 18, fill=NEON_UP)
    slice_w = (x1 - x0) * (limit / capital)
    slice_x1 = x0 + max(8.0, slice_w * progress)
    draw.rounded_rectangle([x0, y0, int(slice_x1), y1], 18, fill=AMBER)
    _dashed(draw, (x0, y0, x1, y1), y0 - 16, MUTED)
    _label(image, x0 + 8, y0 - 28, "CAPITAL", MUTED, 26, role="axis", anchor="lt", limit=2)
    frames = _scene_frames()
    _label(image, x1 - 16, (y0 + y1) / 2, rupees[0][0], frames.GOLD, 64, role="chart", anchor="rm", limit=4)
    _label(image, x0 + 88, y0 + 48, slice_label, frames.GOLD, 48, role="chart", anchor="lt", limit=4)
    image.chart_bar_boxes = [
        (float(x0), float(y0), float(x1), float(y1), float(capital)),
        (float(x0), float(y0), float(x0 + slice_w), float(y1), float(limit)),
    ]
    cx = x0 + slice_w / 2.0
    return {
        "bar": (x0, y0, x1, y1),
        "slice": (cx - 78, y0 + 24, cx + 78, y1 - 24),
        "focus": (cx - 78, y0 + 24, cx + 78, y1 - 24),
    }


def _draw_bars(image, figures, progress):
    """Vertical neon bars for two or more comparable figures. One figure draws none."""
    frames = _scene_frames()
    rows = frames._chart_rows(frames.comparable_chart_figures(figures))
    if len(rows) < 2:
        return {}
    progress = _clip(progress)
    values = [value for _label, value in rows]
    peak = max(values) or 1.0
    x0, y0, x1, y1 = PLOT[0] + 48, PLOT[1] + 120, PLOT[2] - 48, PLOT[3] - 36
    gap = 36
    width = (x1 - x0 - gap * (len(rows) - 1)) / len(rows)
    colors = (NEON_DN, NEON_UP, AMBER, (96, 165, 250))
    draw = ImageDraw.Draw(image)
    bars = []
    boxes = []
    for i, (label, value) in enumerate(rows):
        full_h = (y1 - y0) * (value / peak)
        bar_h = full_h * max(0.05, progress)
        left = x0 + i * (width + gap)
        top = y1 - bar_h
        draw.rectangle([left, top, left + width, y1], fill=colors[i % 4])
        bars.append((float(left), float(y1 - full_h), float(left + width), float(y1), float(value)))
        boxes.append((left, top, left + width, y1))
        _label(
            image, left + width / 2, max(PLOT[1] + 36, top - 28),
            label, frames.GOLD, 64, role="chart", anchor="mt", limit=4,
        )
    image.chart_bar_boxes = bars
    focus = boxes[-1] if boxes else None
    return {"bar": boxes[0] if boxes else None, "slice": boxes[-1] if len(boxes) > 1 else None, "focus": focus}


def _intraday_points(limit, n=28):
    pts = []
    for i in range(n):
        t = i / (n - 1)
        if t < 0.42:
            y = limit * 0.28 * math.sin(t * math.pi * 3)
        elif t < 0.72:
            fall = (t - 0.42) / 0.30
            y = limit * 0.15 * (1 - fall) + (-limit) * fall
        else:
            y = -limit
        pts.append(y)
    return pts


def _draw_intraday(image, rupees, progress):
    limit = min((value for _label, value in rupees), default=1.0)
    if limit <= 0:
        limit = 1.0
    progress = _clip(progress)
    pts = _intraday_points(limit)
    x0, x1 = PLOT[0] + 48, PLOT[2] - 48
    top, bot = PLOT[1] + 48, PLOT[3] - 28
    zero = top + (bot - top) * 0.32
    scale = (bot - zero) / limit
    draw = ImageDraw.Draw(image)
    band_bot = zero + limit * scale
    draw.rectangle([x0, zero, x1, band_bot], fill=(150, 42, 52))
    y_lim = int(zero + limit * scale)
    _dashed(draw, (x0, top, x1, bot), y_lim, NEON_DN, width=4)
    show = max(2, int(round(max(0.08, progress) * len(pts))))
    coords = []
    for i, value in enumerate(pts[:show]):
        px = x0 + (x1 - x0) * (i / (len(pts) - 1))
        py = zero - value * scale
        coords.append((px, py))
    if len(coords) >= 2:
        draw.line(coords, fill=TEXT, width=6)
        # A fill under the drawn line so the move is visible at a glance.
        origin = [(coords[-1][0], zero), (coords[0][0], zero)]
        draw.polygon(coords + origin, fill=(34, 197, 94, ))
        draw.line(coords, fill=NEON_DN if coords[-1][1] > zero else NEON_UP, width=6)
    stop_box = None
    if progress >= 0.72 and coords:
        sx, sy = coords[-1]
        draw.ellipse([sx - 18, sy - 18, sx + 18, sy + 18], outline=AMBER, width=6)
        _label(image, sx, sy - 40, "STOP", AMBER, 36, role="axis", anchor="mt", limit=2)
        stop_box = (sx - 36, sy - 36, sx + 36, sy + 36)
    _label(image, (x0 + x1) / 2, min(band_bot + 8, PLOT[3] - 20), "ट्रेडिंग बैंड", AMBER, 32, role="axis")
    if rupees:
        frames = _scene_frames()
        _label(image, x1 - 8, y_lim - 28, rupees[-1][0], frames.GOLD, 40, role="chart", anchor="rm", limit=4)
    return {
        "limit": (x0, y_lim - 16, x1, y_lim + 16),
        "stop": stop_box,
        "line": (coords[-1][0] - 24, coords[-1][1] - 24, coords[-1][0] + 24, coords[-1][1] + 24) if coords else None,
        "focus": stop_box or (x0, y_lim - 20, x1, y_lim + 20),
    }


def month_series(limit, days=20):
    """Cumulative P&L with and without the daily cap. Not market data."""
    limit = float(limit or 1.0)
    moves = [step * limit for step in _MONTH_MOVES[:days]]
    without, capped = [], []
    left = right = 0.0
    for move in moves:
        left += move
        right += max(move, -limit)
        without.append(left)
        capped.append(right)
    return without, capped


def _draw_month(image, rupees, progress):
    show_limit = bool(rupees)
    limit = min((value for _label, value in rupees), default=4000.0) if show_limit else 4000.0
    progress = _clip(progress)
    left_s, right_s = month_series(limit)
    panels = (
        (PLOT[0] + 16, PLOT[1] + 56, 520, PLOT[3] - 16, left_s, NEON_DN, "बिना हद"),
        (560, PLOT[1] + 56, PLOT[2] - 16, PLOT[3] - 16, right_s, NEON_UP, "हद सहित"),
    )
    draw = ImageDraw.Draw(image)
    show = max(2, int(round(max(0.08, progress) * len(left_s))))
    focus = None
    for x0, y0, x1, y1, series, color, title in panels:
        panel_fill = (110, 28, 36) if color == NEON_DN else (18, 90, 52)
        draw.rounded_rectangle([x0, y0, x1, y1], 12, fill=panel_fill, outline=TEXT, width=2)
        _label(image, (x0 + x1) / 2, y0 + 22, title, TEXT, 32, role="axis")
        window = series[:show]
        peak = max(max(window), 0.0) or 1.0
        floor = min(min(window), 0.0)
        span = max(1.0, peak - floor)
        coords = []
        for i, value in enumerate(window):
            px = x0 + 20 + (x1 - x0 - 40) * (i / max(1, len(series) - 1))
            py = y1 - 28 - (value - floor) / span * (y1 - y0 - 80)
            coords.append((px, py))
        if len(coords) >= 2:
            draw.line(coords, fill=color, width=7)
            focus = (coords[-1][0] - 20, coords[-1][1] - 20, coords[-1][0] + 20, coords[-1][1] + 20)
    _label(image, 540, PLOT[1] + 28, "उदाहरण", MUTED, 26, role="axis")
    if show_limit:
        frames = _scene_frames()
        _label(image, PLOT[2] - 12, PLOT[1] + 28, rupees[-1][0], frames.GOLD, 40, role="chart", anchor="rt", limit=4)
    return {"focus": focus, "limit": focus}


def _draw_meter(image, figures, progress):
    """One number, drawn as a filling track. Not a comparison chart."""
    del figures
    progress = _clip(progress)
    x0, x1 = PLOT[0] + 48, PLOT[2] - 48
    y0, y1 = 820, 960
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([x0, y0, x1, y1], 16, fill=(18, 28, 24))
    grown = x0 + (x1 - x0) * max(0.06, progress)
    draw.rounded_rectangle([x0, y0, int(grown), y1], 16, fill=NEON_UP)
    return {"focus": (x0, y0, grown, y1), "bar": (x0, y0, x1, y1)}


def _clauses(text):
    import re
    parts = re.split(r"[।?!]", str(text or ""))
    out = []
    for part in parts:
        short = _words(part, 3)
        if short and short not in out:
            out.append(short)
        if len(out) == 3:
            break
    return out[:3]


def _draw_flow(image, card, progress):
    """Three steps when the beat has no figure. The words stay on the white line."""
    steps = _clauses(card) or ["देखो", "नापो", "रुको"]
    progress = _clip(progress)
    draw = ImageDraw.Draw(image)
    xs = (280, 540, 800)
    y = 760
    shown = max(1, int(math.ceil(max(0.2, progress) * len(steps))))
    colors = (NEON_UP, AMBER, NEON_DN)
    boxes = []
    for i, _step in enumerate(steps):
        if i >= shown:
            break
        x = xs[i]
        draw.ellipse([x - 54, y - 54, x + 54, y + 54], fill=colors[i % 3])
        boxes.append((x - 54, y - 54, x + 54, y + 54))
        if i and i < shown:
            draw.line([(xs[i - 1] + 64, y), (x - 64, y)], fill=AMBER, width=8)
    focus = boxes[-1] if boxes else None
    return {"focus": focus, "slice": focus}


def _active_word(topic, beat, index, progress):
    spans = (topic or {}).get("_scene_spans") or {}
    words = (topic or {}).get("_audio_words") or []
    span = spans.get((beat, int(index)))
    if not span or not words:
        return ""
    t0, t1 = span
    moment = float(t0) + _clip(progress) * max(0.0, float(t1) - float(t0))
    said = [word for word in words if float(word.get("start") or 0) <= moment + 0.04]
    if not said:
        return ""
    return str(said[-1].get("word") or "")


def _callout_box(word, boxes):
    text = str(word or "")
    if any(piece in text for piece in ("हजार", "हज़ार", "4000", "4,000", "प्रतिशत", "%")):
        return boxes.get("slice") or boxes.get("limit") or boxes.get("stop") or boxes.get("focus")
    if any(piece in text for piece in ("लाख", "पूँजी", "पूंजी")):
        return boxes.get("bar") or boxes.get("focus")
    if any(piece in text for piece in ("रुको", "बंद", "स्टॉप", "STOP", "हद")):
        return boxes.get("stop") or boxes.get("limit") or boxes.get("focus")
    return None


def _draw_callout(image, boxes, topic, beat, index, progress):
    progress = _clip(progress)
    boxes = boxes or {}
    target = _callout_box(_active_word(topic, beat, index, progress), boxes)
    if target is None and progress >= 0.55:
        target = boxes.get("focus")
    if not target:
        return
    x0, y0, x1, y1 = target
    pad = 14 + int(8 * (1.0 - progress))
    # Keep the ring inside the plot so it cannot cover the caption band.
    left = max(PLOT[0] + 8, x0 - pad)
    top = max(PLOT[1] + 8, y0 - pad)
    right = min(PLOT[2] - 8, x1 + pad)
    bottom = min(PLOT[3] - 8, y1 + pad)
    if right - left < 24 or bottom - top < 24:
        return
    ImageDraw.Draw(image).ellipse([left, top, right, bottom], outline=AMBER, width=6)


def _stamp_figures(image, figures):
    """Every figure this scene is allowed to show is on the chart.

    A caption number has to match an on-screen figure. The five-word line
    under the plot cannot carry all of them.
    """
    frames = _scene_frames()
    have = {str(layer.get("text") or "") for layer in image.text_layers}
    pending = []
    for fig in list(figures or [])[:3]:
        label = _words(fig, 4)
        if label and label not in have:
            pending.append(label)
    xs = (PLOT[0] + 20, 400, PLOT[2] - 20)
    anchors = ("lt", "mt", "rt")
    y = PLOT[1] + 12
    for label, x, anchor in zip(pending, xs, anchors):
        _label(image, x, y, label, frames.GOLD, 40, role="chart", anchor=anchor, limit=4)


def _chrome(image, topic, beat, card, figures):
    """Pills and headline inside the chart region. Beat word under the plot."""
    key = str((topic or {}).get("key") or "")
    pill = "DAILY LOSS LIMIT" if beat == "hook" and key == "risk_daily_loss_limit" else _PILL.get(beat, "")
    if pill:
        _label(image, 540, 156, pill, MUTED, 28, role="axis", limit=4)
    headline = _headline(figures)
    if headline:
        _label(image, 540, 214, headline, TEXT, 34, role="axis", limit=8)
    _label(image, 540, 1192, _BEAT_WORD.get(beat, "LIMIT"), AMBER, 64, role="axis", limit=2)
    line = _lesson_line(card)
    if line:
        _label(image, 540, 1284, line, TEXT, 36, role="lesson", limit=5)
    frames = _scene_frames()
    frames._text(
        image, 540, safe_area.BRAND_Y, HANDLE, MUTED, 26,
        anchor="mt", persist=True, band=False, role="brand",
    )


def _finish_meta(image):
    image.panel_box = tuple(float(v) for v in REGION)
    image.labelled_graphic_boxes = [tuple(float(v) for v in REGION)]
    image.content_boxes = [tuple(float(v) for v in PLOT)]
    image.panel_fill = _scene_frames().panel_fill_fraction(image)
    return image


def render(topic, beat, index, n, progress):
    card, figures = _figures_for(topic, beat, index, n)
    kind = kind_for(topic, beat, figures)
    image = _plate()
    image.scene_kind = f"chart-{kind}"
    image.chart_layer = {"type": kind, "figures": list(figures)}
    _draw_stage(image)
    rupees = _rupees(figures)
    frames = _scene_frames()
    comparable = frames.comparable_chart_figures(figures)
    boxes = {}
    if kind == "month":
        boxes = _draw_month(image, rupees, progress)
    elif kind == "intraday":
        boxes = _draw_intraday(image, rupees, progress)
    elif kind == "capital" and len(rupees) >= 2:
        boxes = _draw_capital(image, rupees, progress)
    elif len(comparable) >= 2:
        boxes = _draw_bars(image, figures, progress)
        image.scene_kind = "chart-bars"
        image.chart_layer["type"] = "bars"
    elif figures:
        boxes = _draw_meter(image, figures, progress)
        if kind != "intraday":
            image.scene_kind = "chart-meter"
            image.chart_layer["type"] = "meter"
    else:
        boxes = _draw_flow(image, card, progress)
        image.scene_kind = "chart-flow"
        image.chart_layer["type"] = "flow"
    _draw_callout(image, boxes, topic, beat, index, progress)
    _stamp_figures(image, figures)
    _chrome(image, topic, beat, card, figures)
    _finish_meta(image)
    return image


def layout_faults(frame):
    """Empty when this beat has a top chart and is not a full-screen text card."""
    notes = []
    width, height = frame.size
    region = getattr(frame, "chart_region", None)
    if not region or len(region) != 4:
        notes.append("beat has no chart region")
        return notes
    top, bottom = float(region[1]), float(region[3])
    frac = (bottom - top) / float(height)
    if frac < 0.60 or frac > 0.66:
        notes.append(f"chart region is {frac:.0%} of the frame")
    if top > height * 0.12:
        notes.append("chart region is not at the top")
    if bottom > safe_area.SUBTITLE_TOP + 0.5:
        notes.append("chart region overlaps the caption band")
    plot = getattr(frame, "chart_box", None)
    if not plot:
        notes.append("beat has no chart plot")
    else:
        plot_h = float(plot[3]) - float(plot[1])
        if plot_h < (bottom - top) * 0.70:
            notes.append("chart plot does not fill the top region")
        if float(plot[3]) > bottom:
            notes.append("chart plot leaves the top region")
        if float(plot[1]) < top:
            notes.append("chart plot leaves the top region")
    area = float(width * height)
    for layer in getattr(frame, "text_layers", None) or []:
        box = layer.get("box")
        if not box or layer.get("faint"):
            continue
        box_w = abs(float(box[2]) - float(box[0]))
        box_h = abs(float(box[3]) - float(box[1]))
        if box_h > height * 0.40 or (box_w * box_h) > area * 0.22:
            notes.append("full-screen text card")
            break
        words = str(layer.get("text") or "").split()
        role = layer.get("role") or "lesson"
        limit = 5 if role == "lesson" else 8
        if len(words) > limit:
            notes.append(f"text block is {len(words)} words")
            break
    kind = str(getattr(frame, "scene_kind", "") or "")
    if kind.startswith("text") or "card" in kind and not kind.startswith("chart"):
        notes.append("full-screen text card")
    return notes


def paint_beat(topic, beat, index=0, n=1, progress=1.0, _root=True):
    """The held pose is the finished visual. `animate(u)` draws the way there."""
    image = render(topic, beat, index, n, progress if not _root else 1.0)
    if not _root:
        return image

    def animate(u, topic=topic, beat=beat, index=index, n=n):
        return render(topic, beat, index, n, u)

    image.animate = animate
    return image
