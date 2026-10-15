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
VIS_TOP, VIS_H = 140, 1100          # chart/article area, full width
CAP_TOP, CAP_BOTTOM = 1262, 1660    # captions stay above the 1680 footer
BG = (14, 16, 20)
YELLOW = (255, 214, 10)
WHITE = (236, 240, 244)
GREY = (150, 165, 180)
DRAWN = []  # every overlay string, for the tofu and raw-float gates


def _text(draw, xy, text, size, fill, semi=False):
    text = str(text)
    DRAWN.append(text)
    draw.text(xy, text, fill=fill, font=_font_any(size, semi))


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
    """Scale to fill width x height and crop; anchor_x=1 keeps the latest candles."""
    scale = max(width / image.width, height / image.height)
    resized = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))))
    left = round((resized.width - width) * anchor_x)
    top = round((resized.height - height) * anchor_y)
    return resized.crop((left, top, left + width, top + height))


def _card(title, caption, spoken, footer, visual=None, zoom=1.0, anchor_y=0.5, chips=None, dim=False):
    """Header, a full-width visual, the beat caption, the narration line, a footer chip."""
    from PIL import Image, ImageDraw, ImageEnhance
    image = Image.new("RGB", (W, H), BG)
    if visual and Path(visual).is_file():
        with Image.open(visual) as opened:
            shot = opened.convert("RGB")
        if zoom > 1.0:
            # Smart zoom on the latest candles: keep the right part of the shot.
            keep_w = round(shot.width / zoom)
            keep_h = round(shot.height / zoom)
            top = round((shot.height - keep_h) * 0.5)
            shot = shot.crop((shot.width - keep_w, top, shot.width, top + keep_h))
        shot = _cover(shot, W, VIS_H, anchor_x=1.0, anchor_y=anchor_y)
        if dim:
            shot = ImageEnhance.Brightness(shot).enhance(0.45)
        image.paste(shot, (0, VIS_TOP))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, W, VIS_TOP - 8), fill=(20, 24, 32))
    _text(draw, (36, 22), "PAISE KI PATHSHALA", 34, WHITE)
    _text(draw, (36, 70), title, 36, YELLOW)
    if chips:
        x = 36
        for text, fill, ink in chips:
            width = draw.textlength(text, font=_font_any(30)) + 32
            draw.rounded_rectangle((x, VIS_TOP + VIS_H - 76, x + width, VIS_TOP + VIS_H - 20), 14, fill=fill)
            _text(draw, (x + 16, VIS_TOP + VIS_H - 72), text, 30, ink)
            x += width + 16
    y = CAP_TOP
    for line in _wrap(draw, caption, 58, W - 72, 3):
        _text(draw, (36, y), line, 58, YELLOW)
        y += 72
    y += 10
    for line in _wrap(draw, spoken, 40, W - 72, max(1, (CAP_BOTTOM - y) // 54)):
        if y + 54 > CAP_BOTTOM:
            break
        _text(draw, (36, y), line, 40, WHITE, semi=True)
        y += 54
    draw.rectangle((0, 1680, W, 1780), fill=(28, 32, 40))
    _text(draw, (36, 1702), footer, 36, YELLOW)
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
    title = f"{kind} · {symbol} · {levels.get('last_date') or ''}".rstrip(" ·")
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
        zone_chips.append((f"SL {inr(sl[0])}–{inr(sl[1])}", (255, 186, 190), (90, 16, 24)))
    if None not in target[:2]:
        zone_chips.append((f"Target {inr(target[0])}–{inr(target[1])}", (186, 240, 200), (12, 70, 36)))
    made = []
    for row in rows:
        sec = row.get("sec")
        # The narration line, with spoken whole prices shown as 22,997 (not 22997).
        spoken = re.sub(r"(?<![\d.,])(\d{4,})(?![\d]|[.,]\d)", lambda m: inr(int(m.group(1)), 0), str(row.get("t") or ""))
        caption = _caption(kind, row, levels)
        visual, zoom, footer, chips, dim, anchor_y = chart_path, 1.0, f"CHART · DATA · {src}", None, False, 0.5
        if sec == "hook":
            zoom, footer = 1.8, f"HOOK · {src}"
        elif sec == "intraday":
            interval = str((intraday or {}).get("interval") or "5")
            visual = overlaid or chart_path
            footer = f"INTRADAY {interval}m · DATA · {(intraday or {}).get('label') or src}"
        elif sec == "news":
            visual, anchor_y = news_shot or chart_path, 0.0
            published = stamp((article or {}).get("published"))
            footer = f"KHABAR · FACT · {(article or {}).get('source') or ''} {published}".strip()
        elif sec == "view":
            visual = overlaid or chart_path
            footer, chips = "HAMARA VIEW · OPINION (scenario)", zone_chips
        elif sec == "cta":
            dim, footer = True, "Paise Ki Pathshala"
        path = out / f"{sec}.png"
        _card(title, caption, spoken, footer, visual, zoom=zoom, anchor_y=anchor_y, chips=chips, dim=dim).save(path)
        made.append(path)
    (out / "overlay_text.json").write_text(json.dumps(list(DRAWN), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return made


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
        cmd += ["-i", str(voice), "-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
    cmd += [
        "-t", str(seconds),
        "-vf", "scale=1080:1920,fps=30,format=yuv420p",
        *ENCODE,
        "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    thumb = dest.parent / "thumbnail.jpg"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(dest), "-frames:v", "1", str(thumb),
    ], check=True)
    return dest
