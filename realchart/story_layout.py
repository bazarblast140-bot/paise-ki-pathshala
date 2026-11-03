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
    paint_chart_only(chart_path, levels.get("bars") or [])
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
    _candles(draw, CHART, levels.get("bars") or [])
    source = str(row.get("source_label") or "")
    if levels.get("sample") and source:
        source = source + " · sample fixture"
    write((48, 1288), (source or EDU)[:80], 28, (230, 230, 210))
    write((48, 1332), EDU, 28, YELLOW)
    image.save(path)
    return texts, boxes


def paint_chart_only(path, bars):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (W, H), PANEL)
    _candles(ImageDraw.Draw(image), (40, 200, 1040, 1500), bars)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def write_thumbnail(dest, kind, levels):
    """9:16 cover: big Devanagari headline over a photo or the real candle panel."""
    from PIL import Image, ImageDraw
    imagine = levels.get("imagine") or {}
    bg = imagine.get("thumb_bg")
    if bg and Path(bg).is_file():
        with Image.open(bg) as opened:
            image = _cover(opened.convert("RGB"), W, H)
        image = Image.blend(image, Image.new("RGB", (W, H), (10, 16, 28)), 0.35)
    else:
        image = Image.new("RGB", (W, H), (18, 48, 120) if kind == "ta" else (8, 64, 82))
    draw = ImageDraw.Draw(image)
    _candles(draw, (40, 860, 1040, 1680), levels.get("bars") or [])
    headline = thumb_headline(kind, levels)
    y = 180
    for line in headline.split("\n"):
        size = 120 if len(line) <= 12 else 88
        face = _font(size)
        width = face.getlength(line)
        x = (W - width) / 2
        draw.text((x, y), line, font=face, fill=YELLOW)
        y += size + 24
    sub = inr(levels.get("close"), 2)
    if sub:
        face = _font(54)
        label = f"क्लोज़ {sub}"
        width = face.getlength(label)
        draw.text(((W - width) / 2, y + 10), label, font=face, fill=WHITE)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest.with_suffix(".png"))
    image.save(dest if dest.suffix.lower() == ".jpg" else dest.with_suffix(".jpg"), quality=90)
    return dest if dest.suffix.lower() == ".jpg" else dest.with_suffix(".jpg")


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


def _candles(draw, box, bars):
    rows = list(bars or [])[-48:]
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, 18, fill=PANEL)
    if len(rows) < 2:
        return
    hi = max(float(row["h"]) for row in rows)
    lo = min(float(row["l"]) for row in rows)
    pad = (hi - lo) * 0.06 or 1.0
    hi += pad
    lo -= pad
    n = len(rows)
    bw = (x1 - x0 - 24) / n

    def y_of(value):
        return y1 - 16 - (float(value) - lo) / (hi - lo) * (y1 - y0 - 32)

    for index, row in enumerate(rows):
        x = x0 + 12 + index * bw + bw / 2
        up = float(row["c"]) >= float(row["o"])
        color = UP if up else DOWN
        draw.line((x, y_of(row["h"]), x, y_of(row["l"])), fill=color, width=3)
        top = y_of(max(float(row["o"]), float(row["c"])))
        bot = y_of(min(float(row["o"]), float(row["c"])))
        draw.rectangle((x - bw * 0.28, top, x + bw * 0.28, max(bot, top + 2)), fill=color)
