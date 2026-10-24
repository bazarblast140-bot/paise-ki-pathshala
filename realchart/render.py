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
# Safe zone (Rajesh, 23-Oct-2026): the bottom ~25% (y >= 1440) belongs to the IG/YT
# username, follow button and title, and x >= 940 to the right-side icons. Every
# caption and footer stays above y 1440 and left of x 940.
SAFE_BOTTOM = 1440
SAFE_RIGHT = 940
MEDIA = (20, 184, 1060, 1080)       # x0, y0, x1, y1
CAP = (20, 1096, SAFE_RIGHT, 1396)  # the one caption block
FOOT_Y = 1404                       # one small dim source line (ends by ~1430)
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


def _card(title, caption, spoken, footer, visual=None, zoom=1.0, anchor_y=0.5, chips=None, dim=False, zoom_y=0.5,
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
            # Centre the zoom on the day's candles and levels (zoom_y), not the middle of the shot.
            top = min(max(0, round(shot.height * zoom_y - keep_h / 2)), shot.height - keep_h)
            shot = shot.crop((shot.width - keep_w, top, shot.width, top + keep_h))
        if shot.size != (mw, mh):
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
    # No section chip: internal labels (HOOK, CTA, ...) are never shown to viewers (23-Oct).
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
    # one caption block: the narration line
    draw.rounded_rectangle(CAP, 20, fill=PANEL, outline=(46, 62, 80), width=2)
    text = caption or spoken
    for size in (50, 46, 42, 38):
        lines = _wrap(draw, text, size, CAP[2] - CAP[0] - 48, 99)
        if len(lines) * (size + 12) <= CAP[3] - CAP[1] - 32:
            break
    lines = _wrap(draw, text, size, CAP[2] - CAP[0] - 48, max(1, (CAP[3] - CAP[1] - 32) // (size + 12)))
    y = CAP[1] + 16 + max(0, (CAP[3] - CAP[1] - 32 - len(lines) * (size + 12)) // 2)
    for line in lines:
        _text(draw, (44, y), line, size, WHITE)
        y += size + 12
    if footer:
        _text(draw, (44, FOOT_Y), footer, 24, GREY, semi=True)
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
    if kind == "close":
        from realchart.script import SERIES
        brand = f"{SERIES.upper()} • {day_text}".strip(" •")
    elif kind == "news":
        from datetime import datetime, timedelta, timezone
        today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
        brand = f"MARKET NEWS • {today.day} {today.strftime('%b %Y')}"
    chg = float(levels.get("chg_pct") or 0)
    header = {
        "brand": brand,
        "symbol": f"{symbol} · 1D",
        "close_line": f"Close {day_text}: {inr(levels.get('close'), 0)} ({chg:+.2f}%)",
        "close_color": GREEN if chg >= 0 else RED,
    }
    pivot_chips = [
        (f"{name} {inr(levels.get(key), 0)}", fill, ink)
        for name, key, fill, ink in (
            ("P", "pivot", (255, 226, 120), (40, 30, 0)),
            ("R1", "r1", (186, 240, 200), (12, 70, 36)),
            ("S1", "s1", (255, 186, 190), (90, 16, 24)),
        )
        if levels.get(key) is not None
    ]
    axis_report = {}
    shot_maps = {}
    overlaid = None
    if intraday and kind in ("close", "ta") and intraday.get("path") and Path(intraday["path"]).is_file():
        from PIL import Image
        with Image.open(intraday["path"]) as opened:
            shot = opened.convert("RGB")
        if intraday.get("plot"):
            # Our own yfinance plot: the price scale is known.
            paint_level_overlays(shot, levels, intraday.get("plot"), intraday.get("scale_bars") or [])
        else:
            axis_report["intraday"] = _lines_on(shot, levels)
        overlaid = out / "intraday-overlay.png"
        shot.save(overlaid)
        shot_maps = {str(overlaid): MAPPINGS.get(id(shot))}
    daily_lines = chart_path
    if chart_path and Path(chart_path).is_file() and levels.get("chart_source") == "tradingview":
        from PIL import Image
        with Image.open(chart_path) as opened:
            shot = opened.convert("RGB")
        axis_report["daily"] = _lines_on(shot, levels)
        daily_lines = out / "daily-lines.png"
        shot.save(daily_lines)
        shot_maps[str(daily_lines)] = MAPPINGS.get(id(shot))
    # End card: the voice names TOMORROW's pivot and R1 (from today's OHLC), so the
    # chart under it shows those lines and the ring, not today's pivot (16-Oct review).
    next_lines, tomorrow = None, None
    if levels.get("next_pivot") is not None and chart_path and Path(chart_path).is_file() and levels.get("chart_source") == "tradingview":
        from PIL import Image
        tomorrow = {"pivot": levels.get("next_pivot"), "r1": levels.get("next_r1"), "s1": levels.get("next_s1")}
        with Image.open(chart_path) as opened:
            shot = opened.convert("RGB")
        axis_report["next"] = _lines_on(shot, tomorrow)
        if axis_report["next"].get("ok") and axis_report["next"].get("drawn"):
            next_lines = out / "daily-next-lines.png"
            shot.save(next_lines)
            shot_maps[str(next_lines)] = MAPPINGS.get(id(shot))
    (out.parent / "axis.json").write_text(json.dumps(axis_report, indent=2) + "\n", encoding="utf-8")
    lines_drawn = bool((axis_report.get("intraday") or {}).get("drawn"))
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
    variants = {}
    for row in rows:
        sec = row.get("sec")
        # The narration line, with spoken whole prices shown as 22,997 (not 22997).
        spoken = re.sub(r"(?<![\d.,])(\d{4,})(?![\d]|[.,]\d)", lambda m: inr(int(m.group(1)), 0), str(row.get("t") or ""))
        # One caption block per card: the narration line itself (whole points,
        # as spoken). Levels are on the chips; no second level-text block.
        spoken = danda(spoken)
        caption = ""
        if sec == "hook" and kind != "news":
            # The hook card leads with the number (whole points, as spoken).
            caption = f"{name} क्लोज़ {inr(levels.get('close'), 0)} ({chg:+.2f}%) · {spoken}"
        visual, zoom, footer, chips, dim, anchor_y = daily_lines, 1.0, f"Source: {src} · 1D", None, False, 0.5
        card_header = header
        section = ("CHART · DATA", CYAN)
        if sec == "hook":
            zoom, footer, section = 1.5, f"Source: {src} · 1D", ("HOOK", YELLOW)
        elif sec == "chart":
            zoom = 1.4  # the hook ends on the full daily chart; the chart beat opens zoomed in
            if kind == "news" and next_lines:
                visual, footer = next_lines, f"Source: {src} · 1D · आज के levels"
        elif sec == "points":
            visual, anchor_y = points_card(out, (article or {}), 1), 0.0
            section = ("KHABAR · MAIN POINTS", GREEN)
            footer = f"{(article or {}).get('source') or ''} · हिंदी सार".strip(" ·")
        elif sec == "intraday":
            interval = str((intraday or {}).get("interval") or "5")
            visual = overlaid or chart_path
            chips = None if lines_drawn else pivot_chips
            card_header = dict(header, symbol=f"{symbol} · {interval}m")
            section = (f"INTRADAY {interval}m · time IST", CYAN)
            footer = f"Source: {(intraday or {}).get('label') or src} · {interval}m · IST"
        elif sec == "news":
            visual, anchor_y = (news_card(out, article, news_shot) if article else chart_path), 0.0
            published = stamp((article or {}).get("published"))
            section = ("KHABAR · FACT", GREEN)
            footer = f"{(article or {}).get('source') or ''} · {published}".strip(" ·")
        elif sec == "view":
            visual = overlaid or chart_path
            chips = None if lines_drawn else pivot_chips + zone_chips
            if overlaid:
                card_header = dict(header, symbol=f"{symbol} · {str((intraday or {}).get('interval') or '5')}m")
            section = ("HAMARA VIEW · OPINION (scenario)", (255, 159, 28))
            footer = "Source: levels computed from NSE daily data · TradingView"
        elif sec == "cta":
            footer, section = "Real chart · Real data", ("COMMENT", YELLOW)
            if next_lines:
                visual, footer = next_lines, f"Source: {src} · 1D · कल के levels"
        if kind == "ta" and sec in ("intraday", "view") and daily_lines:
            # One stock, one daily chart: the plan's levels live on the daily scale.
            visual, chips, card_header = daily_lines, None, header
            zoom = 1.3 if sec == "intraday" else 1.15
            footer = f"Source: {src} · 1D · plan levels from daily swings"
            section = (("PLAN · VALID ZONE + ENTRY", CYAN) if sec == "intraday"
                       else ("PLAN · SL + TARGET (scenario)", (255, 159, 28)))
        path = out / f"{sec}.png"
        focus = _focus(shot_maps.get(str(visual)), levels)
        five_min = sec in ("intraday", "view") and overlaid and str(visual) == str(overlaid)
        fit_map = shot_maps.get(str(visual)) if five_min else None
        if fit_map:
            zoom, focus = fit_window(fit_map, levels, zoom, focus)
            RANGES[path.name] = visible_range(fit_map, zoom, focus)
        _card(title, caption, spoken, footer, visual, zoom=zoom, anchor_y=anchor_y, chips=chips, dim=dim,
              header=card_header, section=section, zoom_y=focus).save(path)
        made.append(path)
        if sec in ("news", "points") and article:
            # The article card is on screen only while its own (short) line is spoken; the
            # change inside it is the key sentence highlighted. Points reveal one by one.
            drawn_before = len(DRAWN)
            other = (news_card(out, article, news_shot, mode="sentence") if sec == "news"
                     else points_card(out, article, 2))
            dest = out.parent / f"{sec}-step2.png"
            _card(title, caption, spoken, footer, other, zoom=zoom, anchor_y=anchor_y, chips=chips, dim=dim,
                  header=card_header, section=section, zoom_y=0.5).save(dest)
            variants[path.name] = [[str(dest), 1.0]]
            del DRAWN[drawn_before:]
        elif sec in ("hook", "chart", "intraday", "view", "cta") and visual:
            # Visual change every 2-3 s inside a beat: zoom in on the latest
            # candles, then back out with a ring + arrow on the key level.
            def extra(name, vis, z):
                dest = out.parent / f"{sec}-{name}.png"
                zy = focus
                if fit_map:
                    z, zy = fit_window(fit_map, levels, z, focus)
                    RANGES[dest.name] = visible_range(fit_map, z, zy)
                _card(title, caption, spoken, footer, vis, zoom=z, anchor_y=anchor_y, chips=chips, dim=dim,
                      header=card_header, section=section, zoom_y=zy).save(dest)
                return str(dest)
            drawn_before = len(DRAWN)
            # The hook card is already zoomed (1.5), so its change is a pull back to the full daily chart.
            zoomed = extra("zoom", visual, 1.0 if sec == "hook" else 1.4)
            mark_levels = tomorrow if (sec in ("cta", "chart") and str(visual) == str(next_lines) and next_lines) else levels
            marks = _marked(visual, shot_maps.get(str(visual)), mark_levels, out.parent / f"{sec}-shot")
            ring_zoom = 1.0 if sec == "chart" else zoom  # chart: zoom back out onto the ringed level
            pulse = [[extra(f"mark{i}", m, ring_zoom), 0.12] for i, m in enumerate(marks[:-1])]
            held = [[extra("marked", marks[-1], ring_zoom), 1.0]] if marks else []
            if sec == "hook":
                seq = [[zoomed, 1.0]]  # 16-Oct review: the hook sat on one still for ~6 s
            elif sec == "chart":
                seq = pulse + held  # opens zoomed in (1.4), then zooms out onto the ringed level
            elif sec in ("view", "cta"):
                # Ring grows, holds, then zoom: no two neighbouring stills look alike when the cycle repeats.
                seq = pulse + held + [[zoomed, 1.0]]
            elif sec == "intraday":
                seq = pulse + held + [[zoomed, 1.0]]
            else:
                seq = [[zoomed, 1.0]] + held
            variants[path.name] = seq
            del DRAWN[drawn_before:]  # variant shots repeat the same text; count it once
    (out.parent / "variants.json").write_text(json.dumps(variants, indent=1) + "\n", encoding="utf-8")
    (out.parent / "frame_ranges.json").write_text(json.dumps({
        "must": {k: levels.get(k) for k in INTRADAY_MUST},
        # Our own yfinance plot draws every level on a known scale; only a TradingView shot needs the axis read.
        "shot_ok": bool(shot_maps.get(str(overlaid))) if (overlaid and not (intraday or {}).get("plot")) else None,
        "cards": dict(RANGES),
    }, indent=1) + "\n", encoding="utf-8")
    RANGES.clear()
    (out / "overlay_text.json").write_text(json.dumps(list(DRAWN), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return made


MAPPINGS = {}
RANGES = {}
INTRADAY_MUST = ("close", "pivot", "r1", "s1")  # latest candle + every spoken 5m level


def fit_window(mapping, levels, zoom, focus, margin=0.04):
    """(zoom, zoom_y) so every INTRADAY_MUST price stays inside the crop; zoom shrinks if it has to.

    16-Oct: a 1.4x crop centred on the pivot cut the day's candles and R1 off the 5m card.
    """
    height = (mapping or {}).get("height") or 0
    if not mapping or not height:
        return zoom, focus
    ys = [mapping["a"] * float(levels[k]) + mapping["b"] for k in INTRADAY_MUST if levels.get(k) is not None]
    if not ys:
        return zoom, focus
    lo, hi = max(0.0, min(ys) / height - margin), min(1.0, max(ys) / height + margin)
    span = max(hi - lo, 1e-6)
    zoom = max(1.0, min(zoom, 1.0 / span))
    keep = 1.0 / zoom
    centre = min(max((lo + hi) / 2, keep / 2), 1 - keep / 2)
    return zoom, centre


def visible_range(mapping, zoom, focus):
    """Price range [lo, hi] shown after the crop in _card (same top/bottom maths)."""
    height = (mapping or {}).get("height") or 0
    if not mapping or not height or not mapping.get("a"):
        return None
    keep_h = round(height / zoom) if zoom > 1.0 else height
    top = min(max(0, round(height * focus - keep_h / 2)), height - keep_h) if zoom > 1.0 else 0
    p1 = (top - mapping["b"]) / mapping["a"]
    p2 = (top + keep_h - mapping["b"]) / mapping["a"]
    return [round(min(p1, p2), 2), round(max(p1, p2), 2)]


def _focus(mapping, levels):
    """Vertical centre (0-1 of the shot) of the day's high/low and the drawn levels, for zooming."""
    if not mapping:
        return 0.5
    keys = ("high", "low", "close", "stop", "target") if levels.get("plan") else ("high", "low", "close", "r2", "s2")
    prices = [levels.get(k) for k in keys]
    ys = [mapping["a"] * float(p) + mapping["b"] for p in prices if p is not None]
    height = mapping.get("height") or 0
    if not ys or not height:
        return 0.5
    return min(0.85, max(0.15, (min(ys) + max(ys)) / 2 / height))


def _marked(shot_path, mapping, levels, dest_stem):
    """Three stills of a ring + arrow growing onto the pivot chip: the key spot."""
    from PIL import Image
    from realchart import level_lines
    price = levels.get("entry") if levels.get("plan") else levels.get("pivot")
    if not mapping or price is None or not Path(shot_path).is_file():
        return []
    label = f"{'Entry' if levels.get('plan') else 'P'} {inr(price, 0)}"
    paths = []
    for step, grow in enumerate((0.55, 0.8, 1.0)):
        with Image.open(shot_path) as opened:
            shot = opened.convert("RGB")
        if not level_lines.mark(shot, mapping, price, label, grow=grow):
            return []
        dest = Path(f"{dest_stem}-mark{step}.png")
        shot.save(dest)
        paths.append(dest)
    return paths


def _lines_on(shot, levels):
    """Map price->y from the shot's own axis labels and draw the level lines. Report the fit."""
    from realchart import axis, level_lines
    mapping = axis.map_axis_image(shot)
    if not mapping:
        return {"ok": False, "reason": "price axis not readable", "drawn": []}
    result = level_lines.draw(shot, levels, mapping)
    mapping = dict(mapping, height=shot.height)
    MAPPINGS[id(shot)] = mapping
    DRAWN.extend(f"{name} {inr(levels.get(key), 0)}" for name, key, _c, _d in level_lines.specs_for(levels) if name in result["drawn"])
    return {"ok": True, "residual_px": round(mapping["residual"], 2), "ticks": len(mapping["ticks"]), **result}


def _fit_lines(draw, text, width, sizes, max_lines):
    """Largest font whose wrap needs no more than max_lines: the text is never cut."""
    for size in sizes:
        lines = _wrap(draw, text, size, width, 99)
        if len(lines) <= max_lines:
            return size, lines
    size = sizes[-1]
    return size, _wrap(draw, text, size, width, 99)


def news_card(out, article, shot=None, mode="headline"):
    """The article as its own full card: masthead strip, the full headline, source and time.

    mode="headline" highlights the headline; mode="sentence" highlights the key sentence
    (the visual change while the article is on screen). Nothing is truncated.
    """
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = MEDIA
    width, height = x1 - x0, y1 - y0
    card = Image.new("RGB", (width, height), (246, 247, 249))
    if shot and Path(shot).is_file():
        with Image.open(shot) as opened:
            page = opened.convert("RGB")
        strip = page.crop((0, 0, page.width, min(page.height, round(page.width * 0.22))))
        strip = strip.resize((width, round(strip.height * width / strip.width)))
        card.paste(strip, (0, 0))
        top = strip.height + 40
    else:
        top = 90
    draw = ImageDraw.Draw(card)
    headline = str(article.get("title") or "")
    size, lines = _fit_lines(draw, headline, width - 96, (66, 60, 54, 48, 44), 5)
    y = top + 30
    for line in lines:
        w = draw.textlength(line, font=_font_any(size))
        if mode == "headline":
            draw.rectangle((40, y - 4, 48 + w + 16, y + size + 16), fill=(255, 226, 90))
        _text(draw, (56, y), line, size, (16, 18, 22))
        y += size + 30
    meta = f"{article.get('source') or ''} · {stamp(article.get('published'))}".strip(" ·")
    _text(draw, (56, y + 24), meta, 34, (90, 100, 112), semi=True)
    y += 96
    sentence = " ".join(re.sub(r"<[^>]+>", " ", str(article.get("key_sentence") or article.get("summary") or "")).split())
    room = max(1, (height - y - 60) // 58)
    ssize, slines = _fit_lines(draw, sentence, width - 112, (42, 38, 34, 30), room)
    for line in slines[:room]:
        if mode == "sentence":
            w = draw.textlength(line, font=_font_any(ssize, semi=True))
            draw.rectangle((48, y + 14, 56 + w + 12, y + 20 + ssize + 12), fill=(255, 226, 90))
        _text(draw, (56, y + 20), line, ssize, (16, 18, 22) if mode == "sentence" else (52, 58, 66), semi=True)
        y += ssize + 16
    path = Path(out).parent / ("news-card.png" if mode == "headline" else "news-card-sentence.png")
    card.save(path)
    return path


def points_card(out, article, shown):
    """The Hindi summary points (from realchart.summarize), revealed one by one."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = MEDIA
    width, height = x1 - x0, y1 - y0
    card = Image.new("RGB", (width, height), (24, 30, 40))
    draw = ImageDraw.Draw(card)
    hindi = article.get("hindi") or {}
    _text(draw, (56, 70), "खबर के मुख्य points", 60, (255, 226, 90), semi=True)
    head_size, head = _fit_lines(draw, str(article.get("title") or ""), width - 112, (36, 32, 28), 3)
    y = 170
    for line in head:
        _text(draw, (56, y), line, head_size, (170, 180, 192), semi=True)
        y += head_size + 12
    y += 50
    for index, point in enumerate(list(hindi.get("points") or [])[:2]):
        if index >= shown:
            break
        size, lines = _fit_lines(draw, point, width - 190, (58, 52, 46), 4)
        draw.ellipse((56, y + 14, 56 + 40, y + 54), fill=(46, 204, 113) if index == shown - 1 else (120, 130, 140))
        _text(draw, (80 - 12, y + 12), str(index + 1), 30, (14, 16, 20), semi=True)
        for line in lines:
            _text(draw, (130, y), line, size, (245, 247, 250))
            y += size + 22
        y += 50
    path = Path(out).parent / f"points-{shown}.png"
    card.save(path)
    return path


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
