"""Evening TA Reel layout (1-Nov-2026 fix, after the 31-Oct JSWSTEEL review).

What changed versus the shared card layout in render.py:
- The caption sits at the TOP (under the header); the chart panel is tall
  (y 440-1720) so the frame is never 40% empty. Every text item (caption, level
  chips, ring labels) stays above SAFE_BOTTOM; only plain chart pixels (older
  candles / volume) fall into the IG/YT bottom zone.
- The chart is cropped (never redrawn) around the plan's levels so SL, entry,
  resistance and target are spread over the key area instead of a thin band, and
  the volume pane drops below the key area. Level lines/bands/chips are drawn
  AFTER scaling, crisp, with labels nudged apart so they never overlap.
- Both captures are used: daily (1D) for hook/pattern/plan/CTA and the same
  day's 5-min chart for the entry beat (the 5m capture used to be dropped).
- Motion: every beat is a camera move (eased zoom/pan) rendered as many short
  stills, and the caption steps sentence by sentence, so nothing sits static.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from realchart.fmt import inr

W, H = 1080, 1920
SAFE_BOTTOM = 1440
HEAD_TOP, HEAD_H = 40, 128
CAP = (20, 184, 1060, 424)
MEDIA = (20, 440, 1060, 1720)
PANEL_W, PANEL_H = MEDIA[2] - MEDIA[0], MEDIA[3] - MEDIA[1]
KEY_TOP = 70                                   # panel y where the highest key level may sit
KEY_BOTTOM = SAFE_BOTTOM - MEDIA[1] - 70       # panel y (930) where the lowest key level may sit
MAX_SCALE = 2.6                                # never upscale the capture more than this
STEP_SEC = 0.12                                # one camera still per ~0.12 s (about 8 fps of motion)
MAX_STEPS = 90

YELLOW = (255, 214, 10)
RED = (255, 77, 90)
GREEN = (38, 222, 129)
WHITE = (236, 240, 244)
GREY = (150, 165, 180)
CYAN = (0, 209, 255)
BG = (20, 27, 36)
GRID = (28, 37, 48)
PANEL = (27, 37, 50)


def _font(size, semi=False):
    from realchart.fonts import font
    return font(size, semi)


# ---------------------------------------------------------------- camera maths

def window_for(mapping, shot_h, prices, top_px=KEY_TOP, bottom_px=KEY_BOTTOM, max_scale=MAX_SCALE, min_scale=None):
    """(top, height) of the shot crop so `prices` land between panel y top_px..bottom_px.

    The crop keeps the panel's aspect and is right-anchored (latest candles + axis).
    """
    ys = [mapping["a"] * float(p) + mapping["b"] for p in prices if p is not None]
    full = full_window(shot_h)
    if not ys:
        return full
    y_hi, y_lo = min(ys), max(ys)
    span = max(y_lo - y_hi, 1.0)
    scale = (bottom_px - top_px) / span
    floor = min_scale if min_scale is not None else PANEL_H / shot_h
    scale = max(floor, min(scale, max_scale))
    height = PANEL_H / scale
    top = y_hi - top_px / scale
    if (y_lo - y_hi) * scale < (bottom_px - top_px) * 0.98:
        # Capped zoom: centre the levels inside the key area instead.
        mid = (y_hi + y_lo) / 2
        top = mid - ((top_px + bottom_px) / 2) / scale
    top = min(max(0.0, top), max(0.0, shot_h - height))
    return (top, min(height, shot_h))


def full_window(shot_h):
    return (0.0, float(shot_h))


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def lerp_window(a, b, t):
    k = ease(t)
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)


def price_range(mapping, window):
    top, height = window
    p1 = (top - mapping["b"]) / mapping["a"]
    p2 = (top + height - mapping["b"]) / mapping["a"]
    return [round(min(p1, p2), 2), round(max(p1, p2), 2)]


def panel_y(mapping, window, price):
    top, height = window
    return (mapping["a"] * float(price) + mapping["b"] - top) * (PANEL_H / height)


# ---------------------------------------------------------------- drawing

def crop_panel(shot, window):
    """The real capture, cropped to the window (right-anchored) and scaled to the panel. Pixels untouched."""
    from PIL import Image, ImageChops
    top, height = window
    width = height * PANEL_W / PANEL_H
    right = shot.width
    left = max(0.0, right - width)
    box = (left, top, right, top + height)
    part = shot.resize((PANEL_W, PANEL_H), Image.LANCZOS, box=box)
    if right - left < width - 0.5:  # very tall window on a narrow shot: never stretch
        part = shot.crop((round(left), round(top), right, round(top + height)))
        scale = PANEL_H / part.height
        part = part.resize((round(part.width * scale), PANEL_H), Image.LANCZOS)
        canvas = Image.new("RGB", (PANEL_W, PANEL_H), PANEL)
        canvas.paste(part, (PANEL_W - part.width, 0))
        part = canvas
    return ImageChops.lighter(part.convert("RGB"), Image.new("RGB", part.size, (26, 32, 42)))


def axis_x(shot, window):
    """Panel x of the shot's price-axis column (lines stop there, chips sit left of it)."""
    from realchart.axis import axis_column
    top, height = window
    width = height * PANEL_W / PANEL_H
    left = max(0.0, shot.width - width)
    return (axis_column(shot) - left) * (PANEL_W / min(width, shot.width))


TA_LINES = (
    ("SL", "stop", RED, False),
    ("Target", "target", GREEN, False),
    ("Entry", "entry", YELLOW, True),
    ("Resistance", "resistance", RED, True),
    ("Support", "support", GREEN, True),
)


def spread_labels(ys, gap, lo, hi):
    """Nudge label centres apart (>= gap) inside [lo, hi], keeping order. Lines stay at their exact y."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    placed = [0.0] * len(ys)
    last = -1e9
    for i in order:
        y = max(ys[i], last + gap, lo)
        placed[i] = y
        last = y
    over = (last - hi) if order else 0
    if over > 0:  # pushed past the bottom: shift the stack up
        last = hi + gap
        for i in reversed(order):
            y = min(placed[i], last - gap)
            placed[i] = y
            last = y
    return placed


def draw_levels(panel, mapping, window, levels, which, drawn_log, ax_x, ring=None, ring_grow=1.0):
    """Bands, lines and chips on the scaled panel. Off-scale levels get an edge chip with an arrow.

    Returns {"drawn": [...], "off_scale": [...]}.
    """
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", panel.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    key_lo, key_hi = 16, KEY_BOTTOM + 40
    right = max(200, min(PANEL_W - 8, ax_x - 6))

    def band(zone, color):
        if not zone or len(zone) < 2 or None in zone[:2]:
            return False
        a, b = sorted((panel_y(mapping, window, zone[0]), panel_y(mapping, window, zone[1])))
        b = max(b, a + 10)  # a thin zone still reads as a band
        if b < 0 or a > PANEL_H:
            return False
        pen.rectangle((0, max(0, a), right, min(PANEL_H, b)), fill=color)
        return True

    out = {"drawn": [], "off_scale": [], "bands": []}
    if "SL" in which and band(levels.get("sl_zone"), (255, 80, 90, 80)):
        out["bands"].append("SL")
    if "Target" in which and band(levels.get("target_zone"), (40, 200, 120, 80)):
        out["bands"].append("Target")
    face = _font(34)
    chips, seen = [], []
    for name, key, color, dashed in TA_LINES:
        price = levels.get(key)
        if name not in which or price is None:
            continue
        if any(abs(float(price) - s) < 1e-6 for s in seen):
            continue
        seen.append(float(price))
        y = panel_y(mapping, window, price)
        label = f"{name} {inr(price, 0)}"
        if y < key_lo or y > key_hi:
            out["off_scale"].append(name)
            chips.append((min(max(y, key_lo + 30), key_hi - 30), label, color, "up" if y < key_lo else "down", None))
            continue
        if dashed:
            x = 0
            while x < right:
                pen.line((x, y, min(x + 30, right), y), fill=color + (255,), width=5)
                x += 46
        else:
            pen.line((0, y, right, y), fill=color + (255,), width=6)
        chips.append((y, label, color, None, y))
        out["drawn"].append(name)
    if chips:
        placed = spread_labels([c[0] for c in chips], 74, key_lo + 30, key_hi - 30)
        for (y, label, color, arrow, line_y), cy in zip(chips, placed):
            tw = pen.textlength(label, font=face)
            x1 = right - 10
            x0 = x1 - tw - 36 - (34 if arrow else 0)
            pen.rounded_rectangle((x0, cy - 28, x1, cy + 28), 14, fill=color + (255,))
            pen.text((x0 + 16, cy - 24), label, fill=(14, 16, 20, 255), font=face)
            drawn_log.append(label)
            if arrow:
                ax = x1 - 30
                tri = ([(ax, cy - 14), (ax - 12, cy + 10), (ax + 12, cy + 10)] if arrow == "up"
                       else [(ax, cy + 14), (ax - 12, cy - 10), (ax + 12, cy - 10)])
                pen.polygon(tri, fill=(14, 16, 20, 255))
            elif line_y is not None and abs(cy - line_y) > 4:
                pen.line((x0, cy, x0 - 24, line_y), fill=color + (255,), width=3)
            if ring and label.startswith(ring) and not arrow:
                rx, ry = (tw / 2 + 44) * ring_grow, 46 * ring_grow
                cx = (x0 + x1) / 2
                pen.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=YELLOW + (255,), width=7)
                tip = (cx - rx * 0.72, cy - ry * 0.72)
                tail = (tip[0] - 150 * ring_grow, tip[1] - 80 * ring_grow)
                pen.line((tail, tip), fill=YELLOW + (255,), width=7)
                pen.polygon([tip, (tip[0] - 30, tip[1] - 4), (tip[0] - 12, tip[1] - 30)], fill=YELLOW + (255,))
    panel.paste(Image.alpha_composite(panel.convert("RGBA"), overlay).convert("RGB"))
    return out


def sentences(text):
    parts = [p.strip() for p in re.split(r"(?<=[।?!])\s+", str(text or "")) if p.strip()]
    return parts or [str(text or "")]


def _wrap(draw, text, face, width):
    words, lines, cur = str(text).split(), [], ""
    for word in words:
        nxt = (cur + " " + word).strip()
        if cur and draw.textlength(nxt, font=face) > width:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    return lines


def fit_caption(draw, text, box=CAP):
    """Largest size (60..36) whose wrap fits the caption box; never cut."""
    width = box[2] - box[0] - 56
    room = box[3] - box[1] - 28
    for size in (60, 56, 52, 48, 44, 40, 36):
        lines = _wrap(draw, text, _font(size), width)
        if len(lines) * (size + 16) <= room:
            return size, lines
    size = 36
    return size, _wrap(draw, text, _font(size), width)


def compose(panel, header, title, caption, drawn_log, accent=YELLOW):
    """Full 1080x1920 frame: grid, header, top caption, tall chart panel. No text below SAFE_BOTTOM."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    for y in range(0, H, 60):
        draw.line([(0, y), (W, y)], fill=GRID)
    for x in range(0, W, 60):
        draw.line([(x, 0), (x, H)], fill=GRID)
    image.paste(panel, (MEDIA[0], MEDIA[1]))
    draw.rounded_rectangle(MEDIA, 18, outline=(46, 62, 80), width=3)
    # header
    draw.rounded_rectangle((20, HEAD_TOP, W - 20, HEAD_TOP + HEAD_H), 18, fill=(15, 22, 30), outline=(46, 62, 80), width=2)
    draw.ellipse((44, HEAD_TOP + 28, 64, HEAD_TOP + 48), fill=GREEN)
    for text, xy, size, fill, semi in (
        (header.get("brand") or "", (76, HEAD_TOP + 16), 36, WHITE, False),
        (title, (76, HEAD_TOP + 70), 28, GREY, True),
    ):
        draw.text(xy, text, fill=fill, font=_font(size, semi))
        drawn_log.append(text)
    sym = str(header.get("symbol") or "")
    if sym:
        width = draw.textlength(sym, font=_font(30))
        draw.rounded_rectangle((W - 44 - width - 28, HEAD_TOP + 14, W - 44, HEAD_TOP + 60), 10, fill=(22, 34, 46), outline=CYAN, width=2)
        draw.text((W - 44 - width - 14, HEAD_TOP + 16), sym, fill=CYAN, font=_font(30))
        drawn_log.append(sym)
    close_line = str(header.get("close_line") or "")
    if close_line:
        width = draw.textlength(close_line, font=_font(28, True))
        draw.text((W - 44 - width, HEAD_TOP + 72), close_line, fill=header.get("close_color") or WHITE, font=_font(28, True))
        drawn_log.append(close_line)
    # caption (top)
    draw.rounded_rectangle(CAP, 20, fill=PANEL, outline=(46, 62, 80), width=2)
    draw.rectangle((CAP[0] + 2, CAP[1] + 18, CAP[0] + 10, CAP[3] - 18), fill=accent)
    size, lines = fit_caption(draw, caption)
    block = len(lines) * (size + 16)
    y = CAP[1] + 14 + max(0, (CAP[3] - CAP[1] - 28 - block) // 2)
    for line in lines:
        draw.text((48, y), line, fill=WHITE, font=_font(size))
        drawn_log.append(line)
        y += size + 16
    return image


# ---------------------------------------------------------------- beats

def beat_plan(sec, daily, intra, levels):
    """(shot, [(window_from, window_to)], lines, ring, timeframe) for one TA beat."""
    plan_prices = [levels.get(k) for k in ("stop", "target", "entry", "resistance", "close")]
    plan_prices += [z for k in ("sl_zone", "target_zone") for z in (levels.get(k) or [])]
    every = ("SL", "Target", "Entry", "Resistance", "Support")
    if sec == "intraday" and intra:
        m, h = intra["map"], intra["img"].height
        near = [levels.get(k) for k in ("entry", "resistance") if levels.get(k) is not None]
        near.append(intra.get("close") or levels.get("close"))
        inside = [p for p in near if 0 <= m["a"] * float(p) + m["b"] <= h]
        tight = window_for(m, h, inside, max_scale=1.9) if inside else full_window(h)
        return intra, [(full_window(h), tight)], ("Entry", "Resistance", "SL", "Target"), "Entry", "5m"
    m, h = daily["map"], daily["img"].height
    key = window_for(m, h, plan_prices)
    full = full_window(h)
    if sec == "hook":
        return daily, [(full, key)], (), None, "1D"
    if sec == "chart":
        res = [levels.get("resistance"), levels.get("support"), levels.get("close")]
        return daily, [(key, window_for(m, h, res + plan_prices[:2], max_scale=MAX_SCALE))], ("Resistance", "Support"), "Resistance", "1D"
    if sec == "view":
        sl_side = window_for(m, h, [levels.get("stop"), levels.get("entry"), levels.get("close")], max_scale=MAX_SCALE)
        return daily, [(sl_side, key)], every, "Entry", "1D"
    # cta: pull back out from the plan to the wider daily picture
    return daily, [(key, lerp_window(key, full, 0.6))], every, None, "1D"


def render_ta(out, levels, rows, daily_path, intraday, header, title_for, durations, drawn_log, axis_report):
    """Render every TA card + its camera stills. Returns (made, variants, ranges)."""
    from PIL import Image
    from realchart import axis
    out = Path(out)
    shots = {}
    for label, path in (("daily", daily_path), ("intraday", (intraday or {}).get("path"))):
        if not path or not Path(path).is_file():
            continue
        with Image.open(path) as opened:
            img = opened.convert("RGB")
        mapping = axis.map_axis_image(img)
        if mapping:
            shots[label] = {"img": img, "map": dict(mapping, height=img.height), "path": str(path)}
            axis_report[label] = {"ok": True, "residual_px": round(mapping["residual"], 2),
                                  "ticks": len(mapping["ticks"]), "drawn": [], "off_scale": [], "bands": []}
        else:
            axis_report[label] = {"ok": False, "reason": "price axis not readable", "drawn": []}
    if "intraday" in shots:
        bars = (intraday or {}).get("bars") or []
        shots["intraday"]["close"] = float(bars[-1]["c"]) if bars else None
    made, variants, ranges = [], {}, {}
    durations = list(durations or [])
    for index, row in enumerate(rows):
        sec = str(row.get("sec") or "")
        seconds = float(durations[index]) if index < len(durations) else 6.0
        daily = shots.get("daily")
        intra = shots.get("intraday") if sec == "intraday" else None
        if daily is None and intra is None:
            raise RuntimeError("TA: no readable chart capture")
        shot, moves, lines, ring, tf = beat_plan(sec, daily or intra, intra, levels)
        start, end = moves[0]
        steps = max(8, min(MAX_STEPS, round(seconds / STEP_SEC)))
        spoken = row.get("_spoken") or ""
        parts = sentences(spoken)
        weights = [max(1, len(p)) for p in parts]
        bounds, acc = [], 0
        for w in weights:
            acc += w
            bounds.append(acc / sum(weights))
        card_header = dict(header, symbol=f"{levels.get('symbol')} · {tf}")
        title = title_for(tf)
        seq = []
        report = axis_report.get("intraday" if shot is intra and intra else "daily")
        for step in range(steps):
            t = step / max(1, steps - 1)
            window = lerp_window(start, end, t)
            panel = crop_panel(shot["img"], window)
            grow = min(1.0, 0.55 + 0.45 * max(0.0, (t - 0.35) / 0.3)) if ring else 1.0
            show_ring = ring if (ring and t >= 0.35) else None
            got = draw_levels(panel, shot["map"], window, levels, lines if t > 0.12 or sec != "hook" else (),
                              drawn_log if step == 0 else [], axis_x(shot["img"], window), ring=show_ring, ring_grow=grow)
            if report is not None and step == steps - 1:
                report["drawn"] = sorted(set(report.get("drawn", [])) | set(got["drawn"]))
                report["bands"] = sorted(set(report.get("bands", [])) | set(got["bands"]))
                report["off_scale"] = sorted(set(report.get("off_scale", [])) | set(got["off_scale"]) - set(report["drawn"]))
            which = next(i for i, b in enumerate(bounds) if t <= b + 1e-9) if bounds else 0
            caption = parts[min(which, len(parts) - 1)]
            frame = compose(panel, card_header, title, caption, drawn_log if step == 0 else [],
                            accent=CYAN if tf == "5m" else YELLOW)
            dest = (out / f"{sec}.png") if step == 0 else (out.parent / f"{sec}-cam{step:02d}.png")
            frame.save(dest, compress_level=1)
            if step == 0:
                made.append(dest)
            else:
                seq.append([str(dest), 1.0])
            if tf == "5m":
                ranges[dest.name] = price_range(shot["map"], window)
            for part in parts[1:]:
                pass
        for part in parts:
            if part not in drawn_log:
                drawn_log.append(part)
        variants[made[-1].name] = seq
    return made, variants, ranges
