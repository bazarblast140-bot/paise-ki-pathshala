"""Horizontal level lines on the original TradingView screenshot.

Prices map to pixels through realchart.axis (the chart's own price-axis
labels). Only lines, bands and chips are drawn on top; candle pixels outside a
line stay exactly as captured.
"""
from __future__ import annotations

from realchart.axis import axis_column, y_of
from realchart.fmt import inr

YELLOW = (255, 214, 10, 255)
RED = (255, 77, 90, 255)
GREEN = (38, 222, 129, 255)
SPECS = (
    ("P", "pivot", YELLOW, False),
    ("R1", "r1", RED, True),
    ("R2", "r2", RED, True),
    ("S1", "s1", GREEN, True),
    ("S2", "s2", GREEN, True),
)


def _dashed(draw, x0, x1, y, color, width, dash=26, gap=14):
    x = x0
    while x < x1:
        draw.line((x, y, min(x + dash, x1), y), fill=color, width=width)
        x += dash + gap


def draw(image, levels, mapping, font_size=None):
    """Draw on image (PIL, RGB) in place. Returns {"drawn": [...], "off_scale": [...]}."""
    from PIL import Image, ImageDraw
    from realchart.fonts import font
    width, height = image.size
    right = axis_column(image) - 6
    top, bottom = int(height * 0.04), int(height * 0.95)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)

    def band(zone, color):
        if not zone or len(zone) < 2 or None in zone[:2]:
            return False
        ya, yb = sorted((y_of(mapping, zone[0]), y_of(mapping, zone[1])))
        if yb < top or ya > bottom:
            return False
        pen.rectangle((0, max(top, ya), right, min(bottom, yb)), fill=color)
        return True

    out = {"drawn": [], "off_scale": [], "bands": []}
    if band(levels.get("sl_zone"), (255, 80, 90, 60)):
        out["bands"].append("SL")
    if band(levels.get("target_zone"), (40, 200, 120, 60)):
        out["bands"].append("Target")
    font_size = font_size or max(14, round(image.width / 110))
    chip_font = font(font_size)
    line_w = max(3, width // 450)
    for name, key, color, dashed in SPECS:
        price = levels.get(key)
        if price is None:
            continue
        y = y_of(mapping, price)
        if y < top or y > bottom:
            out["off_scale"].append(name)
            continue
        if dashed:
            _dashed(pen, 0, right, y, color, line_w)
        else:
            pen.line((0, y, right, y), fill=color, width=line_w + 1)
        label = f"{name} {inr(price, 0)}"
        tw = pen.textlength(label, font=chip_font)
        x1 = right - 8
        pen.rounded_rectangle((x1 - tw - 24, y - font_size * 0.8, x1, y + font_size * 0.8), 10, fill=color)
        pen.text((x1 - tw - 12, y - font_size * 0.62), label, fill=(14, 16, 20, 255), font=chip_font)
        out["drawn"].append(name)
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
    return out


def mark(image, mapping, price, label, grow=1.0, font_size=None):
    """Circle + arrow on the key level's chip (the key spot). grow < 1 draws the
    ring smaller, so a few stills in a row read as an animated pulse."""
    from PIL import ImageDraw
    from realchart.fonts import font
    from realchart.axis import axis_column
    font_size = font_size or max(14, round(image.width / 110))
    y = y_of(mapping, price)
    if not 0 <= y <= image.height:
        return False
    pen = ImageDraw.Draw(image)
    tw = pen.textlength(label, font=font(font_size)) + 24
    right = axis_column(image) - 14
    cx, cy = right - tw / 2, y
    rx, ry = (tw / 2 + 26) * grow, (font_size * 1.6 + 14) * grow
    ring = (255, 214, 10)
    width = max(4, image.width // 300)
    pen.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=ring, width=width)
    # Arrow from the upper left into the ring.
    tip = (cx - rx * 0.75, cy - ry * 0.75)
    tail = (tip[0] - 5 * font_size, tip[1] - 3 * font_size)
    pen.line((tail, tip), fill=ring, width=width)
    head = font_size * 0.9
    pen.polygon([tip, (tip[0] - head, tip[1] - head * 0.15), (tip[0] - head * 0.35, tip[1] - head)], fill=ring)
    return True
