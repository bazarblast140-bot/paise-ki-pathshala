"""1080x1920 story cards. Text stays above the bottom safe zone.

A real candle panel is drawn from the OHLC bars. An Imagine still, when one
was loaded, is only the paper behind that panel.
"""
from __future__ import annotations

import json
from pathlib import Path

from realchart.fmt import inr
from realchart.story_script import EDU, thumb_headline

W, H = 1080, 1920
TEXT_LIMIT = 1400
CHART = (70, 640, 1010, 1260)
TA_COLORS = ((24, 72, 190), (176, 84, 28), (16, 122, 104), (88, 42, 150), (150, 36, 52), (32, 64, 96))
CLOSE_COLORS = ((10, 78, 96), (18, 52, 120), (12, 96, 84), (36, 44, 92), (16, 72, 110))
PANEL = (18, 28, 44)
UP = (0, 186, 112)
DOWN = (214, 64, 78)
YELLOW = (255, 214, 10)
INK = (28, 28, 28)
WHITE = (255, 255, 255)


def _font(size):
    from realchart.fonts import font
    return font(size, False)


def story_frames(out, kind, levels, rows):
    """One card per line. Returns paths. Writes overlay, boxes, variants, ranges."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    imagine = levels.get("imagine") or {}
    frames = list(imagine.get("broll_frames") or [])
    colors = TA_COLORS if kind == "ta" else CLOSE_COLORS
    made, variants, drawn, boxes = [], {}, [], []
    for index, row in enumerate(rows):
        bg = frames[index % len(frames)] if frames else None
        path = out / f"{row.get('sec') or index}.png"
        texts, card_boxes = paint(path, colors[index % len(colors)], row, levels, bg)
        drawn.extend(texts)
        boxes.extend({"sec": row.get("sec"), "box": list(box)} for box in card_boxes)
        made.append(path)
        if len(frames) > 1:
            alt = frames[(index + 1) % len(frames)]
            if alt != bg:
                dest = out.parent / f"{row.get('sec')}-broll.png"
                paint(dest, colors[index % len(colors)], row, levels, alt)
                variants[path.name] = [[str(dest), 1.0]]
    chart_path = out.parent / "chart.png"
    paint_chart_only(chart_path, levels)
    (out / "overlay_text.json").write_text(json.dumps(drawn, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "text_boxes.json").write_text(json.dumps(boxes, ensure_ascii=False) + "\n", encoding="utf-8")
    (out.parent / "variants.json").write_text(json.dumps(variants, indent=1) + "\n", encoding="utf-8")
    (out.parent / "axis.json").write_text("{}\n", encoding="utf-8")
    (out.parent / "frame_ranges.json").write_text(
        json.dumps({"must": {}, "shot_ok": None, "cards": {}}) + "\n", encoding="utf-8"
    )
    return made


def paint(path, color, row, levels, bg_image=None):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (W, H), color)
    draw = ImageDraw.Draw(image)
    for y in range(0, H, 8):
        shade = tuple(max(0, int(channel * (1 - 0.25 * y / H))) for channel in color)
        draw.line((0, y, W, y), fill=shade, width=8)
    if bg_image and Path(bg_image).is_file():
        with Image.open(bg_image) as opened:
            image.paste(_cover(opened.convert("RGB"), W, H), (0, 0))
    # A light wash only behind the words, so a clip still shows below the chart.
    base = image.convert("RGBA")
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, 0, W, 620), fill=(0, 0, 0, 80))
    image = Image.alpha_composite(base, band).convert("RGB")
    draw = ImageDraw.Draw(image)
    texts, boxes = [], []

    def write(xy, text, size, fill):
        text = str(text)
        if not text:
            return
        face = _font(size)
        draw.text(xy, text, font=face, fill=fill)
        b = face.getbbox(text)
        boxes.append((xy[0] + b[0], xy[1] + b[1], xy[0] + b[2], xy[1] + b[3]))
        texts.append(text)

    title = str(row.get("sec") or "").upper()
    write((48, 48), title, 36, YELLOW)
    y = 110
    for line in _wrap(str(row.get("d") or ""), 52, 980)[:3]:
        write((48, y), line, 52, WHITE)
        y += 68
    _candles(draw, CHART, levels.get("bars") or [], plan_marks(levels))
    source = str(row.get("source_label") or "")
    if levels.get("sample") and source:
        source = source + " · sample fixture"
    write((48, 1288), (source or EDU)[:80], 28, (230, 230, 210))
    write((48, 1332), EDU, 28, YELLOW)
    image.save(path)
    return texts, boxes


def paint_chart_only(path, levels_or_bars):
    from PIL import Image, ImageDraw
    if isinstance(levels_or_bars, dict):
        bars = levels_or_bars.get("bars") or []
        marks = plan_marks(levels_or_bars)
    else:
        bars, marks = levels_or_bars, []
    image = Image.new("RGB", (W, H), PANEL)
    _candles(ImageDraw.Draw(image), (40, 200, 1040, 1500), bars, marks)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def plan_marks(levels):
    """Entry, SL and target in the pattern's direction, for the candle panel."""
    plan = ((levels or {}).get("story") or {}).get("plan") or {}
    entry = plan.get("entry", (levels or {}).get("entry"))
    sl = plan.get("sl", plan.get("stop", (levels or {}).get("sl", (levels or {}).get("stop"))))
    target = plan.get("target", (levels or {}).get("target"))
    marks = []
    if entry is not None:
        marks.append((float(entry), YELLOW, "एंट्री"))
    if sl is not None:
        marks.append((float(sl), DOWN, "एसएल"))
    if target is not None:
        marks.append((float(target), UP, "टारगेट"))
    return marks


def write_thumbnail(dest, kind, levels, root=None):
    """9:16 cover: Devanagari headline, the candle plan, and a photo of Rajesh.

    The portrait is always composited. Imagine art, when present, is only the
    backdrop. Pose, outfit and background change with the symbol and session.
    """
    from PIL import Image, ImageDraw
    from realchart.host_photo import host_image

    seed = f"{levels.get('symbol')}|{str(levels.get('last_date') or '')[:10]}|{kind}"
    portrait, look = host_image(seed, root=root)
    levels["host"] = {
        "pose": look["pose"],
        "outfit": look["outfit"],
        "background": look["background"],
        "source": look["source"],
    }
    image = _gradient(look["background_rgb"])
    imagine = (levels.get("imagine") or {}).get("thumb_bg")
    if imagine and Path(imagine).is_file():
        with Image.open(imagine) as opened:
            image = Image.blend(image, _cover(opened.convert("RGB"), W, H), 0.45)
    draw = ImageDraw.Draw(image)
    # Topic visual: the real candles, with entry / SL / target, left of Rajesh.
    _candles(draw, (36, 760, 700, 1560), levels.get("bars") or [], plan_marks(levels))
    max_w = W - 48
    if portrait.width > max_w:
        from PIL import Image as _Image
        scaled_h = max(1, int(portrait.height * max_w / portrait.width))
        portrait = portrait.resize((max_w, scaled_h), _Image.Resampling.LANCZOS)
    portrait_x = max(0, W - portrait.width - 24)
    portrait_y = max(0, min(820, H - portrait.height - 16))
    if portrait_x + portrait.width > W or portrait_y + portrait.height > H:
        portrait = portrait.crop((0, 0, min(portrait.width, W - portrait_x), min(portrait.height, H - portrait_y)))
    base = image.convert("RGBA")
    base.alpha_composite(portrait, (portrait_x, portrait_y))
    image = base.convert("RGB")
    draw = ImageDraw.Draw(image)
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, 60, W, 620), fill=(8, 10, 16, 150))
    image = Image.alpha_composite(image.convert("RGBA"), band).convert("RGB")
    draw = ImageDraw.Draw(image)
    headline = thumb_headline(kind, levels)
    y = 120
    for line in headline.split("\n"):
        size = _headline_size(line)
        face = _font(size)
        width = face.getlength(line)
        x = (W - width) / 2
        draw.text((x + 3, y + 3), line, font=face, fill=(0, 0, 0))
        draw.text((x, y), line, font=face, fill=YELLOW)
        y += size + 18
    ticker = str(levels.get("symbol") or "").split(":")[-1]
    sub = inr(levels.get("close"), 2)
    small = " · ".join(part for part in (ticker, f"क्लोज़ {sub}" if sub else "") if part)
    if small:
        face = _font(40)
        width = face.getlength(small)
        draw.text(((W - width) / 2, y + 8), small, font=face, fill=WHITE)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest.with_suffix(".png"))
    jpg = dest if dest.suffix.lower() == ".jpg" else dest.with_suffix(".jpg")
    image.save(jpg, quality=90)
    return jpg


def _headline_size(line):
    for size in (118, 104, 92, 80, 68, 56):
        if _font(size).getlength(line) <= 980:
            return size
    return 56


def _gradient(stops):
    from PIL import Image, ImageDraw
    top, bottom = stops
    image = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(image)
    for y in range(0, H, 8):
        blend = y / H
        color = tuple(int(a + (b - a) * blend) for a, b in zip(top, bottom))
        draw.line((0, y, W, y + 8), fill=color)
    return image


def _cover(image, width, height):
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))))
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _wrap(text, size, width):
    face = _font(size)
    lines, cur = [], ""
    for word in str(text or "").split():
        nxt = (cur + " " + word).strip()
        if cur and face.getlength(nxt) > width:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    return lines


def _candles(draw, box, bars, marks=None):
    rows = list(bars or [])[-48:]
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, 18, fill=PANEL)
    if len(rows) < 2:
        return
    hi = max(float(row["h"]) for row in rows)
    lo = min(float(row["l"]) for row in rows)
    for value, _color, _label in marks or []:
        if value is None:
            continue
        hi = max(hi, float(value))
        lo = min(lo, float(value))
    pad = (hi - lo) * 0.08 or 1.0
    hi += pad
    lo -= pad
    span = hi - lo or 1.0
    n = len(rows)
    bw = (x1 - x0 - 24) / n

    def y_of(value):
        return y1 - 16 - (float(value) - lo) / span * (y1 - y0 - 32)

    for index, row in enumerate(rows):
        x = x0 + 12 + index * bw + bw / 2
        up = float(row["c"]) >= float(row["o"])
        color = UP if up else DOWN
        draw.line((x, y_of(row["h"]), x, y_of(row["l"])), fill=color, width=3)
        top = y_of(max(float(row["o"]), float(row["c"])))
        bot = y_of(min(float(row["o"]), float(row["c"])))
        draw.rectangle((x - bw * 0.28, top, x + bw * 0.28, max(bot, top + 2)), fill=color)
    face = _font(26)
    for value, color, label in marks or []:
        if value is None:
            continue
        y = y_of(value)
        if y < y0 + 8 or y > y1 - 8:
            continue
        draw.line((x0 + 10, y, x1 - 10, y), fill=color, width=4)
        text_w = face.getlength(label)
        label_y = min(max(y0 + 8, y - 32), y1 - 40)
        draw.rectangle((x0 + 14, label_y, x0 + 22 + text_w, label_y + 32), fill=PANEL)
        draw.text((x0 + 18, label_y), label, font=face, fill=color)
