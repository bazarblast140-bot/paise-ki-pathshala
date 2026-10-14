"""1080x1920 frames. Overlays sit on the chart. Indicators are cards, not lines."""
from __future__ import annotations

import subprocess
from pathlib import Path

from realchart.capture import FALLBACK_LABEL, _font
from realchart.ends import ENCODE


def _card(title, lines, footer, chart=None, highlight=None, zones=None):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1080, 1920), (14, 16, 20))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1080, 120), fill=(20, 24, 32))
    draw.text((36, 36), title[:42], fill=(255, 214, 10), font=_font(42))
    paste = None
    if chart and Path(chart).is_file():
        shot = Image.open(chart).convert("RGB")
        shot.thumbnail((1040, 900))
        image.paste(shot, (20, 150))
        paste = (20, 150, shot.width, shot.height, getattr(shot, "_src_size", None))
        cap_y = 150 + shot.height + 16
    else:
        cap_y = 180
    if zones and paste:
        _paint_zones(image, zones, paste)
    y = cap_y
    for index, line in enumerate(lines):
        text = str(line)[:70]
        if highlight == index:
            draw.rectangle((28, y - 8, 1052, y + 50), fill=(255, 214, 10))
            draw.text((36, y), text, fill=(20, 16, 8), font=_font(40))
        else:
            draw.text((36, y), text, fill=(236, 240, 244), font=_font(40))
        y += 64
    draw.rectangle((0, 1680, 1080, 1780), fill=(28, 32, 40))
    draw.text((36, 1704), footer[:80], fill=(255, 214, 10), font=_font(36))
    return image


def _paint_zones(image, levels, paste):
    """SL in light red and target in light green, on top of the candle plot. No EMA lines."""
    from PIL import Image, ImageDraw
    bars = list(levels.get("bars") or [])[-80:]
    if len(bars) < 2 or levels.get("chart_source") != "yfinance":
        _zone_chips(image, levels)
        return
    highs = [float(row["h"]) for row in bars]
    lows = [float(row["l"]) for row in bars]
    top, bot = max(highs), min(lows)
    span = max(1.0, top - bot)
    left, top_y, width, height = paste[:4]
    scale = height / 1080.0
    plot_top, plot_h, plot_left, plot_w = 80, 900, 40, 1000

    def y_of(price):
        y_src = plot_top + (top - float(price)) / span * plot_h
        return top_y + y_src * scale

    x0 = left + plot_left * scale
    x1 = left + (plot_left + plot_w) * scale
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    sl = levels.get("sl_zone") or []
    target = levels.get("target_zone") or []
    if len(target) == 2 and None not in target:
        y_a, y_b = y_of(target[0]), y_of(target[1])
        draw.rectangle((x0, min(y_a, y_b), x1, max(y_a, y_b)), fill=(40, 200, 120, 70))
    if len(sl) == 2 and None not in sl:
        y_a, y_b = y_of(sl[0]), y_of(sl[1])
        draw.rectangle((x0, min(y_a, y_b), x1, max(y_a, y_b)), fill=(255, 80, 90, 80))
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
    _zone_chips(image, levels)


def _zone_chips(image, levels):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    sl = levels.get("sl_zone") or [None, None]
    target = levels.get("target_zone") or [None, None]
    draw.rounded_rectangle((36, 1560, 520, 1648), 16, fill=(255, 186, 190))
    draw.text((52, 1576), f"SL {sl[0]}–{sl[1]}"[:28], fill=(90, 16, 24), font=_font(28))
    draw.rounded_rectangle((540, 1560, 1044, 1648), 16, fill=(186, 240, 200))
    draw.text((556, 1576), f"Target {target[0]}–{target[1]}"[:28], fill=(12, 70, 36), font=_font(28))


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
        draw.text((left + 8, y - 30), name, fill=color, font=font)
        labels.append(name)
    chip = str(levels.get("levels_chip") or "")
    if chip:
        draw.rectangle((16, 16, width - 16, 110), fill=(14, 16, 20, 210))
        draw.text((28, 28), chip[:70], fill=(255, 214, 10, 255), font=font)
        if len(chip) > 70:
            draw.text((28, 64), chip[70:140], fill=(255, 214, 10, 255), font=font)
        labels.append("chip")
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
    return labels


def frames(out, kind, levels, rows, chart_path, article=None, intraday=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    symbol = str(levels.get("symbol") or "")
    yf_label = FALLBACK_LABEL if levels.get("chart_source") == "yfinance" else ""
    made = []
    specs = [
        ("hook", [rows[0]["d"]], "HOOK", None, None, None),
        ("chart", [
            f"{symbol}",
            f"Close {levels.get('close')}",
            f"EMA20 {float(levels.get('ema20') or 0):.2f}   EMA50 {float(levels.get('ema50') or 0):.2f}",
            f"RSI14 {float(levels.get('rsi14') or 0):.2f}",
            yf_label,
        ], "CHART · DATA", chart_path, None, None),
        ("view", [
            "HAMARA VIEW · OPINION (scenario)",
            str(levels.get("levels_chip") or "")[:70],
            f"P {float(levels.get('pivot') or 0):.2f}  R1 {float(levels.get('r1') or 0):.2f}  S1 {float(levels.get('s1') or 0):.2f}",
            f"R2 {float(levels.get('r2') or 0):.2f}  S2 {float(levels.get('s2') or 0):.2f}",
        ], "OPINION", chart_path, None, levels),
        ("cta", [rows[-1]["d"][:80]], "CTA", None, None, None),
    ]
    if intraday and kind in ("close", "ta") and intraday.get("path"):
        from PIL import Image
        with Image.open(intraday["path"]) as opened:
            shot = opened.convert("RGB")
        paint_level_overlays(shot, levels, intraday.get("plot"), intraday.get("scale_bars") or [])
        overlaid = out / "intraday-overlay.png"
        shot.save(overlaid)
        interval = str(intraday.get("interval") or "5")
        specs.insert(2, (
            "intraday",
            [
                f"{symbol} {interval}m",
                str(intraday.get("label") or ""),
                str(levels.get("levels_chip") or "")[:70],
            ],
            "INTRADAY · DATA",
            overlaid,
            None,
            None,
        ))
    if article:
        published = str(article.get("published") or "")
        news_at = 3 if intraday and kind in ("close", "ta") else 2
        specs.insert(news_at, (
            "news",
            [
                str(article.get("title") or "")[:70],
                str(article.get("key_sentence") or "")[:70],
                f"{article.get('source') or ''}  {published}"[:70],
            ],
            "KHABAR · FACT",
            article.get("capture", {}).get("path") if isinstance(article.get("capture"), dict) else None,
            1,
            None,
        ))
    for name, lines, footer, chart, highlight, zones in specs:
        path = out / f"{name}.png"
        _card(f"{kind} · {symbol}", lines, footer, chart, highlight=highlight, zones=zones).save(path)
        made.append(path)
    return made


def _concat_path(path):
    """Absolute path. ffmpeg resolves a relative concat entry against frames.txt, so `run/cards/hook.png` became `run/run/cards/hook.png`."""
    text = Path(path).resolve().as_posix().replace("'", "'\\''")
    return text


def assemble(frame_paths, voice, dest, seconds=45.0):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / "frames.txt"
    each = seconds / max(1, len(frame_paths))
    lines = []
    for path in frame_paths:
        lines.append(f"file '{_concat_path(path)}'")
        lines.append(f"duration {each:.3f}")
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
