"""1080x1920 frames. Overlays sit on the chart. Indicators are cards, not lines."""
from __future__ import annotations

import subprocess
from pathlib import Path

import json
import re

from realchart.capture import _font
from realchart.ends import ENCODE
from realchart.fmt import inr, stamp


W, H = 1080, 1920
# Layout after the approved 14-Oct box sample: grid background, a terminal
# header, a tall media panel and a filled caption panel. No plain black.
HEAD_TOP, HEAD_H = 40, 128
MEDIA = (20, 236, 1060, 1436)       # x0, y0, x1, y1: 1040x1200, a tall crop of the chart
CAP = (20, 1456, 1060, 1736)        # caption panel
FOOT = (20, 1752, 1060, 1832)
BG = (20, 27, 36)
GRID = (28, 37, 48)
PANEL = (27, 37, 50)
CHART_FLOOR = (26, 32, 42)          # lifts TradingView's #0F0F0F so no area reads as a void
YELLOW = (255, 214, 10)
WHITE = (236, 240, 244)
GREY = (150, 165, 180)
CYAN = (0, 209, 255)
GREEN = (38, 222, 129)
RED = (255, 77, 90)
DRAWN = []  # every overlay string, for the tofu and raw-float gates


def _text(draw, xy, text, size, fill, semi=False, anchor=None):
    text = str(text)
    DRAWN.append(text)
    draw.text(xy, text, fill=fill, font=_font_any(size, semi), anchor=anchor)


def _font_any(size, semi=False):
    from realchart.fonts import font
    return font(size, semi)


def _wrap(draw, text, size, width, max_lines):
    font = _font_any(size)
    words, lines, cur = str(text or "").split(), [], ""
    for word in words:
        nxt = (cur + " " + word).strip()
        if cur and draw.textlength(nxt, font=font) > width:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.") + " …"
    return lines


def _cover(image, width, height, anchor_x=1.0, anchor_y=0.5):
    """Scale to fill width x height and crop; anchor_x=1 keeps the latest candles and the price axis."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))))
    left = round((resized.width - width) * anchor_x)
    top = round((resized.height - height) * anchor_y)
    return resized.crop((left, top, left + width, top + height))


def background():
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    for y in range(0, H, 60):
        draw.line([(0, y), (W, y)], fill=GRID)
    for x in range(0, W, 60):
        draw.line([(x, 0), (x, H)], fill=GRID)
    return image


def lift(shot):
    """Raise the darkest pixels to CHART_FLOOR; candles and labels keep their colour."""
    from PIL import Image, ImageChops
    return ImageChops.lighter(shot.convert("RGB"), Image.new("RGB", shot.size, CHART_FLOOR))


def _card(title, caption, spoken, footer, visual=None, zoom=1.0, anchor_y=0.5, chips=None, dim=False,
          header=None, section=("CHART · DATA", CYAN)):
    """Terminal header, a tall media panel filled edge to edge, a caption panel, a footer chip."""
    from PIL import ImageDraw, ImageEnhance, ImageFilter
    image = background()
    draw = ImageDraw.Draw(image)
    header = header or {}
    x0, y0, x1, y1 = MEDIA
    mw, mh = x1 - x0, y1 - y0
    if visual and Path(visual).is_file():
        from PIL import Image
        with Image.open(visual) as opened:
            shot = opened.convert("RGB")
        if zoom > 1.0:
            keep_w = round(shot.width / zoom)
            keep_h = round(shot.height / zoom)
            top = round((shot.height - keep_h) * 0.5)
            shot = shot.crop((shot.width - keep_w, top, shot.width, top + keep_h))
        shot = lift(_cover(shot, mw, mh, anchor_x=1.0, anchor_y=anchor_y))
        if dim:
            shot = ImageEnhance.Brightness(shot.filter(ImageFilter.GaussianBlur(6))).enhance(0.75)
        image.paste(shot, (x0, y0))
    else:
        draw.rectangle(MEDIA, fill=PANEL)
    draw.rounded_rectangle(MEDIA, 18, outline=(46, 62, 80), width=3)
    # terminal header
    draw.rounded_rectangle((20, HEAD_TOP, W - 20, HEAD_TOP + HEAD_H), 18, fill=(15, 22, 30), outline=(46, 62, 80), width=2)
    draw.ellipse((44, HEAD_TOP + 28, 64, HEAD_TOP + 48), fill=GREEN)
    # Brand-neutral: the main render is shared by YT and IG; the brand comes
    # only from each platform's intro and end card.
    _text(draw, (76, HEAD_TOP + 16), header.get("brand") or "NIFTY 50", 36, WHITE)
    _text(draw, (76, HEAD_TOP + 70), title, 30, GREY, semi=True)
    sym = str(header.get("symbol") or "")
    if sym:
        width = draw.textlength(sym, font=_font_any(30))
        draw.rounded_rectangle((W - 44 - width - 28, HEAD_TOP + 14, W - 44, HEAD_TOP + 60), 10, fill=(22, 34, 46), outline=CYAN, width=2)
        _text(draw, (W - 44 - width - 14, HEAD_TOP + 16), sym, 30, CYAN)
    close_line = str(header.get("close_line") or "")
    if close_line:
        width = draw.textlength(close_line, font=_font_any(28, True))
        _text(draw, (W - 44 - width, HEAD_TOP + 72), close_line, 28, header.get("close_color") or WHITE, semi=True)
    # section chip, on the media panel's top edge
    label, color = section
    width = draw.textlength(label, font=_font_any(28))
    draw.rounded_rectangle((36, y0 + 16, 36 + width + 56, y0 + 62), 10, fill=(16, 24, 32), outline=color, width=2)
    draw.ellipse((52, y0 + 32, 66, y0 + 46), fill=color)
    _text(draw, (76, y0 + 18), label, 28, color)
    if chips:
        x = 36
        rows_y = y1 - 150  # above the IST time axis
        for text, fill, ink in chips:
            width = draw.textlength(text, font=_font_any(30)) + 32
            if x + width > x1 - 16:
                x, rows_y = 36, rows_y - 66
            draw.rounded_rectangle((x, rows_y, x + width, rows_y + 56), 14, fill=fill)
            _text(draw, (x + 16, rows_y + 6), text, 30, ink)
            x += width + 14
    # caption panel
    draw.rounded_rectangle(CAP, 20, fill=PANEL, outline=(46, 62, 80), width=2)
    y = CAP[1] + 20
    for line in _wrap(draw, caption, 50, CAP[2] - CAP[0] - 48, 3):
        _text(draw, (44, y), line, 50, YELLOW)
        y += 60
    y += 6
    while y + 52 <= CAP[3] - 12:
        lines = _wrap(draw, spoken, 40, CAP[2] - CAP[0] - 48, max(1, (CAP[3] - 12 - y) // 52))
        for line in lines:
            _text(draw, (44, y), line, 40, WHITE, semi=True)
            y += 52
        break
    draw.rounded_rectangle(FOOT, 16, fill=(15, 22, 30), outline=(46, 62, 80), width=2)
    _text(draw, (44, FOOT[1] + 18), footer, 32, YELLOW)
    return image


def _price_span(bars, levels):
    highs = [float(row["h"]) for row in bars] if bars else []
    lows = [float(row["l"]) for row in bars] if bars else []
    if not highs:
        highs = [float(levels.get("high") or levels.get("close") or 1)]
        lows = [float(levels.get("low") or levels.get("close") or 0)]
    return max(highs), min(lows)


def _y_of(price, top, height, price_high, price_low):
    span = max(float(price_high) - float(price_low), 1e-6)
    return top + (float(price_high) - float(price)) / span * height


def paint_level_overlays(image, levels, plot=None, bars=None):
    """Lines and zones on top of the intraday chart. Candle pixels outside a line stay as captured."""
    from PIL import Image, ImageDraw
    width, height = image.size
    if plot:
        left, top = int(plot["left"]), int(plot["top"])
        plot_w, plot_h = int(plot["width"]), int(plot["height"])
        price_high, price_low = _price_span(bars, levels)
    else:
        left = int(width * 0.06)
        top = int(height * 0.14)
        plot_w = int(width * 0.82)
        plot_h = int(height * 0.70)
        price_high, price_low = _price_span(bars, levels)
        pad = max(1.0, (price_high - price_low) * 0.08)
        price_high += pad
        price_low -= pad
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(28)

    def band(zone, color):
        if not zone or len(zone) < 2 or None in zone:
            return
        y_a = _y_of(zone[0], top, plot_h, price_high, price_low)
        y_b = _y_of(zone[1], top, plot_h, price_high, price_low)
        lo, hi = sorted((y_a, y_b))
        if hi < top or lo > top + plot_h:
            return
        draw.rectangle((left, max(top, lo), left + plot_w, min(top + plot_h, hi)), fill=color)

    band(levels.get("sl_zone"), (255, 80, 90, 70))
    band(levels.get("target_zone"), (40, 200, 120, 70))
    labels = []
    specs = (
        ("P", levels.get("pivot"), (255, 214, 10, 230)),
        ("R1", levels.get("r1"), (80, 220, 140, 230)),
        ("R2", levels.get("r2"), (40, 180, 110, 230)),
        ("S1", levels.get("s1"), (255, 120, 130, 230)),
        ("S2", levels.get("s2"), (220, 70, 80, 230)),
        ("Call OI", levels.get("call_oi"), (255, 170, 60, 230)),
        ("Call OI next", levels.get("call_oi_next"), (255, 140, 40, 230)),
        ("Put OI", levels.get("put_oi"), (120, 170, 255, 230)),
        ("Put OI next", levels.get("put_oi_next"), (80, 130, 230, 230)),
    )
    for name, price, color in specs:
        if price is None:
            continue
        y = _y_of(price, top, plot_h, price_high, price_low)
        if y < top or y > top + plot_h:
            labels.append(f"{name} off-scale")
            continue
        draw.line((left, y, left + plot_w, y), fill=color, width=3)
        DRAWN.append(name)
        draw.text((left + 8, y - 30), name, fill=color, font=font)
        labels.append(name)
    chip = str(levels.get("levels_chip") or "")
    if chip:
        DRAWN.append(chip)
        draw.rectangle((16, 16, width - 16, 110), fill=(14, 16, 20, 210))
        draw.text((28, 28), chip[:70], fill=(255, 214, 10, 255), font=font)
        if len(chip) > 70:
            draw.text((28, 64), chip[70:140], fill=(255, 214, 10, 255), font=font)
        labels.append("chip")
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
    return labels


def _caption(kind, row, levels):
    """The on-screen line for a beat: the same facts the narration says, formatted."""
    sec = row.get("sec")
    chg = float(levels.get("chg_pct") or 0)
    if sec == "view":
        return f"P {inr(levels.get('pivot'))}  R1 {inr(levels.get('r1'))}  S1 {inr(levels.get('s1'))}"
    if sec == "chart" and kind != "news":
        return f"Close {inr(levels.get('close'))}  {'+' if chg >= 0 else '−'}{abs(chg):.2f}%  H {inr(levels.get('high'))}  L {inr(levels.get('low'))}"
    return str(row.get("d") or "")


def frames(out, kind, levels, rows, chart_path, article=None, intraday=None):
    """One card per script beat, in narration order. Every card has a real visual."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    del DRAWN[:]
    symbol = str(levels.get("symbol") or "")
    src = "NSE data (yfinance)" if levels.get("chart_source") == "yfinance" else "TradingView"
    title = "Real chart · Real data"
    from datetime import date as _date
    try:
        day = _date.fromisoformat(str(levels.get("last_date"))[:10])
        day_text = f"{day.day} {day.strftime('%b %Y')}"
    except ValueError:
        day_text = ""
    name = "NIFTY 50" if symbol.endswith("NIFTY") else symbol.split(":")[-1]
    brand = " • ".join(part for part in (name, str(kind).upper(), day_text) if part)
    chg = float(levels.get("chg_pct") or 0)
    header = {
        "brand": brand,
        "symbol": f"{symbol} · 1D",
        "close_line": f"Close {levels.get('last_date') or ''}: {inr(levels.get('close'))} ({chg:+.2f}%)",
        "close_color": GREEN if chg >= 0 else RED,
    }
    pivot_chips = [
        (f"{name} {inr(levels.get(key))}", fill, ink)
        for name, key, fill, ink in (
            ("P", "pivot", (255, 226, 120), (40, 30, 0)),
            ("R1", "r1", (186, 240, 200), (12, 70, 36)),
            ("S1", "s1", (255, 186, 190), (90, 16, 24)),
        )
        if levels.get(key) is not None
    ]
    overlaid = None
    if intraday and kind in ("close", "ta") and intraday.get("path") and Path(intraday["path"]).is_file():
        from PIL import Image
        with Image.open(intraday["path"]) as opened:
            shot = opened.convert("RGB")
        if intraday.get("plot"):
            # Lines only where the price scale is known (our own yfinance plot).
            paint_level_overlays(shot, levels, intraday.get("plot"), intraday.get("scale_bars") or [])
        overlaid = out / "intraday-overlay.png"
        shot.save(overlaid)
    news_shot = None
    if article and isinstance(article.get("capture"), dict) and article["capture"].get("ok"):
        news_shot = article["capture"].get("path")
    sl = levels.get("sl_zone") or [None, None]
    target = levels.get("target_zone") or [None, None]
    zone_chips = []
    if None not in sl[:2]:
        zone_chips.append((f"SL {inr(sl[0], 0)}–{inr(sl[1], 0)}", (255, 186, 190), (90, 16, 24)))
    if None not in target[:2]:
        zone_chips.append((f"Target {inr(target[0], 0)}–{inr(target[1], 0)}", (186, 240, 200), (12, 70, 36)))
    made = []
    for row in rows:
        sec = row.get("sec")
        # The narration line, with spoken whole prices shown as 22,997 (not 22997).
        spoken = re.sub(r"(?<![\d.,])(\d{4,})(?![\d]|[.,]\d)", lambda m: inr(int(m.group(1)), 0), str(row.get("t") or ""))
        spoken = danda(spoken)
        caption = danda(_caption(kind, row, levels))
        if same_text(spoken, caption):
            # The CTA beat's caption and narration are the same line: show it once.
            spoken = ""
        visual, zoom, footer, chips, dim, anchor_y = chart_path, 1.0, f"Source: {src} · 1D", None, False, 0.5
        section = ("CHART · DATA", CYAN)
        if sec == "hook":
            zoom, footer, section = 1.5, f"Source: {src} · 1D", ("HOOK", YELLOW)
        elif sec == "intraday":
            interval = str((intraday or {}).get("interval") or "5")
            visual = overlaid or chart_path
            chips = pivot_chips
            section = (f"INTRADAY {interval}m · time IST", CYAN)
            footer = f"Source: {(intraday or {}).get('label') or src} · {interval}m · IST"
        elif sec == "news":
            visual, anchor_y = news_shot or chart_path, 0.0
            published = stamp((article or {}).get("published"))
            section = ("KHABAR · FACT", GREEN)
            footer = f"{(article or {}).get('source') or ''} · {published}".strip(" ·")
        elif sec == "view":
            visual = overlaid or chart_path
            chips = pivot_chips + zone_chips
            section = ("HAMARA VIEW · OPINION (scenario)", (255, 159, 28))
            footer = "Levels computed from NSE daily data"
        elif sec == "cta":
            dim, footer, section = True, "Real chart · Real data", ("COMMENT", YELLOW)
        path = out / f"{sec}.png"
        _card(title, caption, spoken, footer, visual, zoom=zoom, anchor_y=anchor_y, chips=chips, dim=dim,
              header=header, section=section).save(path)
        made.append(path)
    (out / "overlay_text.json").write_text(json.dumps(list(DRAWN), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return made


def danda(text):
    """'22,535।' reads as '22,535l' on screen: put a space before the danda after a digit."""
    return re.sub(r"(\d)\s*([।॥])", r"\1 \2", str(text or ""))


def same_text(a, b):
    def norm(value):
        return " ".join(re.sub(r"[^\w\s]", " ", str(value or "")).split()).lower()
    a, b = norm(a), norm(b)
    return bool(a) and (a == b or a in b or b in a)


def beat_seconds(rows, total):
    """Each card stays up for its share of the narration (spoken word estimate)."""
    from realchart.script import estimate_seconds
    weights = [max(0.6, estimate_seconds(str(row.get("t") or ""))) for row in rows]
    scale = float(total) / sum(weights)
    return [weight * scale for weight in weights]


def _concat_path(path):
    """Absolute path. ffmpeg resolves a relative concat entry against frames.txt, so `run/cards/hook.png` became `run/run/cards/hook.png`."""
    text = Path(path).resolve().as_posix().replace("'", "'\\''")
    return text


def assemble(frame_paths, voice, dest, seconds=45.0, durations=None):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / "frames.txt"
    each = seconds / max(1, len(frame_paths))
    durations = list(durations or [each] * len(frame_paths))
    lines = []
    for path, hold in zip(frame_paths, durations):
        lines.append(f"file '{_concat_path(path)}'")
        lines.append(f"duration {hold:.3f}")
    lines.append(f"file '{_concat_path(frame_paths[-1])}'")
    work.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", str(work),
    ]
    if voice and Path(voice).is_file():
        # Pad a short voice with silence to the full length (a 38s Charon read
        # was cut to 38s by -shortest and failed the 40-50s band).
        cmd += ["-i", str(voice), "-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
    cmd += [
        "-t", str(seconds),
        "-vf", "scale=1080:1920,fps=30,format=yuv420p",
        *ENCODE,
        "-af", "loudnorm=I=-15:TP=-1.5:LRA=11,apad",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    thumb = dest.parent / "thumbnail.jpg"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(dest), "-frames:v", "1", str(thumb),
    ], check=True)
    return dest
