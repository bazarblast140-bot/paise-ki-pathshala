"""1080x1920 frames. Overlays sit on the chart. Indicators are cards, not lines."""
from __future__ import annotations

import subprocess
from pathlib import Path

from realchart.capture import FALLBACK_LABEL, _font


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


def frames(out, kind, levels, rows, chart_path, article=None):
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
            f"EMA20 {float(levels.get('ema20') or 0):.2f}",
            f"EMA50 {float(levels.get('ema50') or 0):.2f}",
            f"RSI14 {float(levels.get('rsi14') or 0):.2f}",
        ], "OPINION", chart_path, None, levels),
        ("cta", [rows[-1]["d"][:80]], "CTA", None, None, None),
    ]
    if article:
        published = str(article.get("published") or "")
        specs.insert(2, (
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


def assemble(frame_paths, voice, dest, seconds=45.0):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / "frames.txt"
    each = seconds / max(1, len(frame_paths))
    lines = []
    for path in frame_paths:
        lines.append(f"file '{path}'")
        lines.append(f"duration {each:.3f}")
    lines.append(f"file '{frame_paths[-1]}'")
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
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    thumb = dest.parent / "thumbnail.jpg"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(dest), "-frames:v", "1", str(thumb),
    ], check=True)
    return dest
