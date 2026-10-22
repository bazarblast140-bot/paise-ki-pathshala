"""Shared motion for every Paise topic video.

The numbers come from the trade plan. Timing comes from the Whisper word
clock. Reveals use ease-out cubic over 250–400 ms. The picture style is the
reference sheet in docs/MOTION_LIBRARY.md.

The settled still used by pixel checks is drawn elsewhere. This module is the
moving chart. Aaj ka Nifty does not use it.
"""
import math
import subprocess
import wave
from pathlib import Path

# Reference palette. Yellow is attention only.
BG = (0x16, 0x16, 0x16)
BULL = (0x22, 0xB5, 0x3C)
BEAR = (0xF0, 0x1E, 0x32)
ENTRY = (0xF5, 0xD9, 0x0A)
SL = (0xEF, 0x44, 0x44)
TARGET = (0x22, 0xC5, 0x5E)
ATTENTION = (0xF5, 0xC5, 0x18)
WHITE = (255, 255, 255)
PILL = (0x2A, 0x2A, 0x2A)

REVEAL_S = 0.32
CONTEXT_CANDLE_S = 0.15
PILL_LAG_S = 0.12
ZOOM_S = 0.60
ZOOM_BACK_S = 2.5
ZOOM_PEAK = 1.35
COUNTER_S = 0.50
TITLE_S = 0.30
CANDLE_DIM = 0.35
RR_ALPHA = 0.30
# The moving plot, not the still board. ypad 0.02 and a short pad put the
# price range at about 60% of the 1920 frame. The still path keeps 0.12.
MOTION_YPAD = 0.02
MOTION_PAD = (36, 26, 36, 24)
AXIS = (120, 142, 170)
LABEL_SIZE = 48
COUNTER_SIZE = 72
CAPTION_SIZE = 68
WHOOSH_UNDER_DB = 20.0
WHOOSH_SECONDS = 0.42
MAX_WHOOSH = 4
_LEVELS = ("sr", "entry", "sl", "target")
_KEYWORDS = (
    "टारगेट", "स्टॉप", "एंट्री", "सपोर्ट", "रेसिस्टेंस",
    "रिस्क", "हैमर", "कैंडल",
)


def clamp(u):
    u = float(u)
    if u < 0.0:
        return 0.0
    if u > 1.0:
        return 1.0
    return u


def ease_out_cubic(u):
    """Ease-out cubic. 0 and 1 stay exact. The move is fastest at the start."""
    u = clamp(u)
    inv = 1.0 - u
    return 1.0 - inv * inv * inv


def ease_in_out(u):
    """Ease-in-out cubic. The midpoint stays at 0.5."""
    u = clamp(u)
    if u < 0.5:
        return 4.0 * u * u * u
    inv = -2.0 * u + 2.0
    return 1.0 - (inv * inv * inv) / 2.0


def ease(u):
    """Reveal easing for lines, candles, and counters."""
    return ease_out_cubic(u)


def window_progress(t, start, end):
    """0 before `start`, 1 at and after `end`, eased in between."""
    if t is None:
        return 1.0
    start = float(start)
    end = float(end)
    t = float(t)
    if end <= start:
        return 1.0 if t >= start else 0.0
    if t <= start:
        return 0.0
    if t >= end:
        return 1.0
    return ease((t - start) / (end - start))


def _fmt(value):
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _progress(clock, key, t, default=0.0):
    window = (clock or {}).get(key)
    if not window:
        return float(default)
    return window_progress(t, window[0], window[1])


def split_keyword(text):
    """Prefix, the one yellow word, and the suffix. The word stays intact."""
    text = str(text or "")
    for word in _KEYWORDS:
        at = text.find(word)
        if at >= 0:
            return text[:at], word, text[at + len(word):]
    return text, "", ""


def candle_roles(plan):
    """Context candles, then the 2–3 candles that define the pattern."""
    n = len(plan.ohlc)
    focus = max(0, min(int(plan.focus), n - 1))
    start = max(0, focus - 1)
    end = min(n, start + 3)
    if end - start < 2 and n >= 2:
        start = max(0, end - 2)
    key = list(range(start, end))
    context = [i for i in range(n) if i not in key]
    if not context and key:
        context = [key[0]]
        key = key[1:]
    return context, key


def candle_windows(plan, beats=None):
    """Candles arrive left to right, in time order, so no candle floats ahead of its slot.

    The first candle is done at t=0. The run-up to the focus candle keeps
    arriving through the hook, 150 ms each. The focus candle (the pattern
    the pehchan beat names) finishes exactly when pehchan starts, so the
    highlight box never points at an empty slot. Candles after the focus
    are the "next candle" the entry beat talks about and arrive with it.
    """
    beats = beats or {}
    n = len(plan.ohlc)
    windows = [None] * n
    if not n:
        return windows
    focus = max(0, min(int(plan.focus), n - 1))
    pehchan = beats.get("pehchan")
    hook = beats.get("hook")
    hook_end = float(hook[1]) if hook and len(hook) >= 2 else 5.0
    p0 = float(pehchan[0]) if pehchan else max(hook_end, 2.0)
    windows[0] = (-CONTEXT_CANDLE_S, 0.0)
    focus_start = max(0.0, p0 - REVEAL_S)
    windows[focus] = (focus_start, p0) if focus else (-CONTEXT_CANDLE_S, 0.0)
    before = list(range(1, focus))
    if before:
        last = max(0.4, min(4.6, focus_start - CONTEXT_CANDLE_S - 0.05))
        step = (last - 0.4) / max(1, len(before) - 1)
        for k, index in enumerate(before):
            start = 0.4 + k * step if len(before) > 1 else min(0.8, last)
            windows[index] = (start, start + CONTEXT_CANDLE_S)
    after = list(range(focus + 1, n))
    entry = beats.get("entry") or beats.get("context")
    e0 = float(entry[0]) if entry else p0 + 5.0
    for k, index in enumerate(after):
        start = e0 + k * CONTEXT_CANDLE_S
        dur = REVEAL_S if k == 0 else CONTEXT_CANDLE_S
        windows[index] = (start, start + dur)
    return windows


def _ready_before(windows, focus, at):
    """Every candle up to the focus is finished by `at`. Order is kept."""
    out = list(windows)
    for index in range(0, min(focus, len(out) - 1) + 1):
        w = out[index]
        if w and w[1] > at:
            dur = w[1] - w[0]
            out[index] = (at - dur, at)
    return out


def _axes(box, n, lo, hi, pad, width_frac=0.62, ypad=0.12):
    import board_kit as K
    ax = K.Axes(box, max(len(n) if not isinstance(n, int) else n, 1), lo, hi, pad=pad, ypad=ypad)
    ax.bw = max(10, int(ax.step * width_frac))
    return ax


# Room above the highest and below the lowest price for a level pill and its
# label, as a share of the price span. MOTION_YPAD alone put the SR/SL pills
# on the board edge.
MOTION_HEADROOM = 0.10
FRAME_MARGIN = 14
# The stop-break candle closes this share of `gap` beyond the stop.
BREAK_REACH = 0.6


def _failure_prices(plan):
    """Open, close, high, low of the finished stop-break candle (progress 1)."""
    sl = float(plan.sl)
    entry = float(plan.entry)
    gap = max(abs(entry - sl) * 0.35, 6.0)
    if str(getattr(plan, "side", "long")) == "short":
        open_p = sl - gap * 0.15
        close_p = sl + gap * BREAK_REACH
        return open_p, close_p, close_p + gap * 0.15, open_p - gap * 0.05
    open_p = sl + gap * 0.15
    close_p = sl - gap * BREAK_REACH
    return open_p, close_p, open_p + gap * 0.05, close_p - gap * 0.15


def motion_geometry(plan):
    """One price scale for the whole video, so a new beat does not jump the chart.

    The scale also holds the invalidation beat's stop-break candle, which
    trades beyond the stop. Without it that candle stuck out of the board.
    """
    import board_kit as K
    prices = [candle[1] for candle in plan.ohlc] + [candle[2] for candle in plan.ohlc]
    prices.extend([float(plan.entry), float(plan.sl), float(plan.target), float(plan.sr)])
    _o, _c, high, low = _failure_prices(plan)
    prices.extend([high, low])
    lo, hi = min(prices), max(prices)
    room = (hi - lo) * MOTION_HEADROOM
    return K.BOARD, lo - room, hi + room, MOTION_PAD


def _stroke_candle(draw, ax, index, row, color, frac):
    o, h, l, c = row
    cx = ax.x(index)
    bw = ax.bw
    draw.line([(cx, ax.y(h)), (cx, ax.y(l))], fill=color, width=max(3, bw // 6))
    top, bot = ax.y(max(o, c)), ax.y(min(o, c))
    mid = (top + bot) / 2.0
    half = max((bot - top) / 2.0, 2.5) * clamp(frac)
    draw.rectangle([cx - bw / 2, mid - half, cx + bw / 2, mid + half], fill=color)
    return cx, mid - half, mid + half


def candle_build(img, box, ohlc, lo, hi, progress, pad, width_frac=0.80,
                 per_candle=None, focus=None, dim=1.0):
    """Draw candles. A float progress fills them together. A list is one value each.

    Bull bodies are #22B53C and bear bodies are #F01E32. During the key-candle
    push, every other candle drops to 35% opacity.
    """
    from PIL import Image, ImageDraw
    import numpy as np

    ohlc = list(ohlc)
    ax = _axes(box, len(ohlc), lo, hi, pad, width_frac, ypad=MOTION_YPAD)
    _plot_rails(img, ax)
    if per_candle is None:
        progress = clamp(progress)
        per_candle = [progress] * len(ohlc)
    else:
        per_candle = [clamp(v) for v in per_candle]
    dim_others = focus is not None and float(dim) < 0.999
    ghost = Image.new("RGBA", img.size, (0, 0, 0, 0)) if dim_others else None
    ghost_draw = ImageDraw.Draw(ghost) if ghost is not None else None
    draw = ImageDraw.Draw(img)
    for index, row in enumerate(ohlc):
        frac = per_candle[index] if index < len(per_candle) else 0.0
        if frac <= 0.0:
            continue
        color = BULL if row[3] >= row[0] else BEAR
        pen = ghost_draw if dim_others and index != int(focus) else draw
        _stroke_candle(pen, ax, index, row, color, frac)
    if ghost is not None:
        arr = np.array(ghost)
        arr[..., 3] = (arr[..., 3].astype(np.float32) * float(dim)).astype(np.uint8)
        faded = Image.fromarray(arr, "RGBA")
        merged = Image.alpha_composite(img.convert("RGBA"), faded).convert("RGB")
        img.paste(merged, (0, 0))
    return ax


def _hline(img, x0, x1, y, color, width=6):
    from PIL import ImageDraw
    if x1 <= x0 + 0.5:
        return
    ImageDraw.Draw(img).line([(x0, y), (x1, y)], fill=tuple(color), width=int(width))


def _fade_hline(img, x0, x1, y, color, alpha, width=6):
    """A full-width level. Alpha 1 is solid. The hook ghosts fade with the RR box."""
    alpha = clamp(alpha)
    if alpha <= 0.0 or x1 <= x0 + 0.5:
        return
    if alpha >= 0.999:
        _hline(img, x0, x1, y, color, width=width)
        return
    half = max(1.0, float(width) / 2.0)
    _blend_rect(img, [x0, y - half, x1, y + half], color, alpha)


def _plot_rails(img, ax):
    """Vertical edges of the price plot, so the chart occupies its rows."""
    from PIL import ImageDraw
    top = float(ax.y(ax.hi))
    bot = float(ax.y(ax.lo))
    if bot < top:
        top, bot = bot, top
    draw = ImageDraw.Draw(img)
    # A closed chart box. Every candle, wick, line and label stays inside it.
    left, right = ax.x0 + 12, ax.x1 - 12
    top, bot = top - FRAME_MARGIN, bot + FRAME_MARGIN
    draw.rectangle([left, top, right, bot], outline=AXIS, width=4)
    ax.frame = (left, top, right, bot)


def _hdash(img, x0, x1, y, color, width=4):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    x = float(x0)
    x1 = float(x1)
    while x < x1:
        draw.line([(x, y), (min(x + 18.0, x1), y)], fill=tuple(color), width=int(width))
        x += 30.0


def _blend_rect(img, rect, color, alpha):
    from PIL import Image, ImageDraw
    alpha = int(round(clamp(alpha) * 255))
    if alpha <= 0:
        return
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(list(rect), fill=tuple(color) + (alpha,))
    merged = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.paste(merged, (0, 0))


PILL_GAP = 60


def _shift_pill(rect, placed, bounds):
    """Stack a pill when another one sits within PILL_GAP px. Used for every topic."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    height = max(1.0, y1 - y0)
    floor = float(bounds[1]) + 8.0
    ceil = float(bounds[3]) - 8.0

    def gap_of(other):
        if x1 <= other[0] or other[2] <= x0:
            return PILL_GAP
        if y1 > other[1] and y0 < other[3]:
            return 0.0
        if y0 >= other[3]:
            return y0 - other[3]
        return other[1] - y1

    for _ in range(12):
        blocker = None
        for other in placed:
            if gap_of(other) < PILL_GAP:
                blocker = other
                break
        if blocker is None:
            break
        below = float(blocker[3]) + PILL_GAP
        above = float(blocker[1]) - PILL_GAP - height
        if below + height <= ceil:
            y0 = below
        elif above >= floor:
            y0 = above
        else:
            y0 = min(max(floor, below), max(floor, ceil - height))
        y1 = y0 + height
    return [x0, y0, x1, y1]


def _pill(img, x, y, text, size=LABEL_SIZE):
    import board_kit as K
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    font = K.font(int(size))
    tw = draw.textlength(str(text), font=font)
    th = int(size)
    rect = [float(x), float(y), float(x) + tw + 28, float(y) + th + 16]
    queue = getattr(img, "_pill_queue", None)
    if queue is not None:
        # draw_plan paints every pill after every line, so no line crosses a label.
        queue.append((rect, str(text), int(size)))
        return rect
    placed = getattr(img, "_placed_pills", None)
    if placed is not None:
        bounds = getattr(img, "_pill_bounds", None) or (0, 0, img.size[0], img.size[1])
        rect = _shift_pill(rect, placed, bounds)
        placed.append(rect)
    draw.rounded_rectangle(rect, 12, fill=PILL)
    draw.text((rect[0] + 14, rect[1] + 6), str(text), font=font, fill=WHITE)
    return rect



PILL_CLEAR = 8.0


def place_pills(rects, line_ys, frame):
    """Final pill rects: inside the frame, apart from each other, off every level line.

    Each pill keeps its x. Its y moves to the nearest free slot, trying the
    asked spot first and then alternating below and above it.
    """
    fx0, fy0, fx1, fy1 = (float(v) for v in frame)
    placed = []
    for rect in rects:
        x0, y0, x1, y1 = (float(v) for v in rect)
        h = y1 - y0
        if x1 > fx1 - 4:
            x0, x1 = fx1 - 4 - (x1 - x0), fx1 - 4
        if x0 < fx0 + 4:
            x0, x1 = fx0 + 4, fx0 + 4 + (x1 - x0)

        def free(top):
            bot = top + h
            if top < fy0 + 4 or bot > fy1 - 4:
                return False
            for y in line_ys:
                if top - 6 <= y <= bot + 6:
                    return False
            for other in placed:
                if x1 <= other[0] or other[2] <= x0:
                    continue
                if top < other[3] + PILL_CLEAR and bot > other[1] - PILL_CLEAR:
                    return False
            return True

        chosen = None
        for k in range(0, 160):
            for top in ((y0 + 6 * k, y0 - 6 * k) if k else (y0,)):
                if free(top):
                    chosen = top
                    break
            if chosen is not None:
                break
        if chosen is None:
            chosen = min(max(y0, fy0 + 4), fy1 - 4 - h)
        placed.append([x0, chosen, x1, chosen + h])
    return placed


def _flush_pills(img, line_ys, frame):
    import board_kit as K
    from PIL import ImageDraw
    queue = getattr(img, "_pill_queue", None) or []
    img._pill_queue = None
    rects = place_pills([row[0] for row in queue], line_ys, frame)
    draw = ImageDraw.Draw(img)
    for rect, (_asked, text, size) in zip(rects, queue):
        draw.rounded_rectangle(rect, 12, fill=PILL)
        draw.text((rect[0] + 14, rect[1] + 6), text, font=K.font(size), fill=WHITE)
    img.pill_rects = rects
    return rects


def _dashed_curve(img, p0, p1, color=WHITE):
    from PIL import ImageDraw
    mx = (p0[0] + p1[0]) / 2.0
    my = (p0[1] + p1[1]) / 2.0 - 40.0
    pts = []
    for step in range(17):
        u = step / 16.0
        omu = 1.0 - u
        x = omu * omu * p0[0] + 2 * omu * u * mx + u * u * p1[0]
        y = omu * omu * p0[1] + 2 * omu * u * my + u * u * p1[1]
        pts.append((x, y))
    draw = ImageDraw.Draw(img)
    for step in range(0, len(pts) - 1, 2):
        draw.line([pts[step], pts[step + 1]], fill=tuple(color), width=3)


def trend_arrow(img, ax, plan, progress):
    """Arrow draws from the first close toward the focus close."""
    import board_kit as K

    progress = clamp(progress)
    if progress <= 0.0 or plan.focus <= 0:
        return False
    start, end = plan.ohlc[0], plan.ohlc[plan.focus]
    p1 = (ax.x(0), ax.y(start[3]))
    p2 = (ax.x(plan.focus), ax.y(end[3]))
    tip = p2 if progress >= 1.0 else (
        p1[0] + (p2[0] - p1[0]) * progress,
        p1[1] + (p2[1] - p1[1]) * progress,
    )
    K.arrow(img, p1, tip, ATTENTION, w=8, head=26)
    return True


def sr_band(img, ax, price, progress, color=None):
    """Translucent white band around support or resistance."""
    progress = clamp(progress)
    if progress <= 0.0 or price is None:
        return False
    color = WHITE if color is None else color
    y = float(ax.y(float(price)))
    half = 18.0
    _blend_rect(
        img,
        [ax.x0 + ax.pl, y - half, ax.x1 - ax.pr, y + half],
        color,
        0.22 * progress,
    )
    return True


def level_line(img, box, y, color, label, side, progress, pill=None, dashed=False,
               width=6, anchor=None):
    """Draw a trade line left to right. The pill pops after the line, with the price.

    ENTRY, SL, and TARGET are solid 6 px. Support and resistance stay dashed.
    """
    progress = clamp(progress)
    x0 = box[0] + 24
    x1 = box[2] - 24
    if progress <= 0.0:
        return {"price_label": None, "drawn": False, "x1": x0, "pill": False}
    end = x1 if progress >= 1.0 else x0 + (x1 - x0) * progress
    if dashed:
        _hdash(img, x0, end, y, WHITE, width=4)
    else:
        _hline(img, x0, end, y, color, width=width)
    show_pill = progress >= 1.0 if pill is None else float(pill) > 0.0
    shown = None
    if show_pill and label:
        size = LABEL_SIZE if not dashed else 36
        text = str(label)
        import board_kit as K
        from PIL import ImageDraw
        tw = ImageDraw.Draw(img).textlength(text, font=K.font(size))
        if side == "right":
            lx = x1 - tw - 36
        else:
            lx = x0 + 8
        ly = y - size - 28
        if ly < box[1] + 8:
            ly = y + 18
        _pill(img, lx, ly, text, size=size)
        if anchor is not None:
            _dashed_curve(img, (lx + 20, ly + size), anchor, WHITE)
        shown = text
    return {"price_label": shown, "drawn": True, "x1": end, "pill": bool(shown)}


def rr_readout(plan):
    """Risk, reward, and the RR text from the computed plan."""
    risk = float(plan.risk)
    reward = float(plan.reward)
    text = f"RR {plan.rr_text}"
    return {
        "risk": risk,
        "reward": reward,
        "rr": plan.rr_text,
        "text": text,
        "reward_px": 0.0,
        "risk_px": 0.0,
    }


def risk_reward_box(img, plan, progress, ax=None):
    """Tall green reward box over a short red risk box, anchored at the key candle.

    Alpha is about 0.30. Progress 1 is the finished box. It is on screen at 0 ms.
    """
    progress = clamp(progress)
    info = rr_readout(plan)
    info["shown"] = progress > 0.0
    if progress <= 0.0:
        return info
    if ax is None:
        box, lo, hi, pad = motion_geometry(plan)
        ax = _axes(box, len(plan.ohlc), lo, hi, pad)
    focus = max(0, min(int(plan.focus), len(plan.ohlc) - 1))
    cx = ax.x(focus)
    half = max(28.0, ax.bw * 1.6)
    entry_y = ax.y(float(plan.entry))
    sl_y = ax.y(float(plan.sl))
    target_y = ax.y(float(plan.target))
    reward_h = abs(target_y - entry_y) * progress
    risk_h = abs(sl_y - entry_y) * progress
    info["reward_px"] = reward_h
    info["risk_px"] = risk_h

    def span(origin, dest, height):
        if dest <= origin:
            return origin - height, origin
        return origin, origin + height

    gy0, gy1 = span(entry_y, target_y, reward_h)
    ry0, ry1 = span(entry_y, sl_y, risk_h)
    _blend_rect(img, [cx - half, gy0, cx + half, gy1], TARGET, RR_ALPHA * progress)
    _blend_rect(img, [cx - half, ry0, cx + half, ry1], SL, RR_ALPHA * progress)
    if progress >= 1.0:
        _outline_rect(img, [cx - half, gy0, cx + half, gy1], TARGET, 4)
        _outline_rect(img, [cx - half, ry0, cx + half, ry1], SL, 4)
        _pill(img, cx + half + 12, min(gy0, gy1) + 8, info["text"], size=44)
    return info


def _outline_rect(img, rect, color, width=4):
    from PIL import ImageDraw
    if rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
        return
    ImageDraw.Draw(img).rectangle(list(rect), outline=tuple(color), width=int(width))


def _zoom_candle(img, cx, cy, zoom):
    """Push in on the key candle. The crop stays inside the board."""
    import board_kit as K
    from PIL import Image

    zoom = float(zoom)
    if zoom <= 1.001:
        return
    board = K.BOARD
    half_w, half_h = 110, 150
    x0 = int(max(board[0], math.floor(cx - half_w)))
    y0 = int(max(board[1], math.floor(cy - half_h)))
    x1 = int(min(board[2], math.ceil(cx + half_w)))
    y1 = int(min(board[3], math.ceil(cy + half_h)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return
    crop = img.crop((x0, y0, x1, y1))
    zw = max(1, int(round((x1 - x0) * zoom)))
    zh = max(1, int(round((y1 - y0) * zoom)))
    scaled = crop.resize((zw, zh), Image.Resampling.BILINEAR)
    left = int(round(cx - zw / 2.0))
    top = int(round(cy - zh / 2.0))
    src_x = max(0, board[0] - left)
    src_y = max(0, board[1] - top)
    dst_x = max(left, board[0])
    dst_y = max(top, board[1])
    src_r = min(zw, board[2] - left)
    src_b = min(zh, board[3] - top)
    if src_r <= src_x or src_b <= src_y:
        return
    img.paste(scaled.crop((src_x, src_y, src_r, src_b)), (dst_x, dst_y))


def key_zoom_at(t, start, push_s=ZOOM_S, back_s=ZOOM_BACK_S):
    """1.0 → 1.35 over 600 ms ease-in-out, then back down before the next beat."""
    if t is None or start is None:
        return 1.0, False
    t = float(t)
    start = float(start)
    if t <= start:
        return 1.0, False
    push_end = start + float(push_s)
    back_end = push_end + float(back_s)
    if t >= back_end:
        return 1.0, False
    if t <= push_end:
        u = ease_in_out((t - start) / float(push_s))
        return 1.0 + (ZOOM_PEAK - 1.0) * u, True
    u = ease_in_out((t - push_end) / float(back_s))
    return ZOOM_PEAK - (ZOOM_PEAK - 1.0) * u, True


def highlight_candle(img, ax, plan, progress, zoom=None):
    """Yellow #F5C518 outline on the key candle, then the push-in.

    `progress` 0 leaves the candle alone. A zoom above 1 dims the caller's
    other candles (they are already drawn) and outlines this one.
    """
    from PIL import ImageDraw

    focus = int(plan.focus)
    if focus < 0 or focus >= len(plan.ohlc):
        return {"zoom": 1.0, "dim": 1.0, "attention": False}
    if zoom is None:
        zoom = 1.0 if progress <= 0.0 else 1.0 + (ZOOM_PEAK - 1.0) * ease_in_out(progress)
    active = float(zoom) > 1.001
    candle = plan.ohlc[focus]
    cx = ax.x(focus)
    top = ax.y(max(candle[0], candle[3]))
    bot = ax.y(min(candle[0], candle[3]))
    if active:
        bw = ax.bw
        ImageDraw.Draw(img).rectangle(
            [cx - bw / 2 - 8, top - 10, cx + bw / 2 + 8, bot + 10],
            outline=ATTENTION, width=6,
        )
        _zoom_candle(img, cx, (top + bot) / 2.0, zoom)
    return {
        "zoom": float(zoom),
        "dim": CANDLE_DIM if active else 1.0,
        "attention": active,
    }


def number_counter(img, value, progress, xy=(80, 1188), label="TARGET", paint=True):
    """Count up to `value` over the caller's window. Progress 1 is exact.

    The wired chart does not paint this. The line pill already names the target.
    """
    import board_kit as K

    value = float(value)
    progress = clamp(progress)
    if progress >= 1.0:
        shown = value
    elif progress <= 0.0:
        shown = 0.0
    else:
        shown = value * progress
    if paint and img is not None and progress > 0.0:
        K.label(img, xy[0], xy[1], f"{label} {_fmt(shown)}", WHITE, COUNTER_SIZE)
    return {"value": value, "shown": shown}


def _failure_candle(img, ax, plan, progress):
    """A red candle that trades through the stop. Longs break below it."""
    from PIL import ImageDraw
    progress = clamp(progress)
    sl = float(plan.sl)
    entry = float(plan.entry)
    gap = max(abs(entry - sl) * 0.35, 6.0)
    short = str(getattr(plan, "side", "long")) == "short"
    # Same shape as _failure_prices at progress 1, which sizes the scale.
    if short:
        open_p = sl - gap * 0.15
        close_p = sl + gap * BREAK_REACH * progress
        high_p = close_p + gap * 0.15
        low_p = open_p - gap * 0.05
    else:
        open_p = sl + gap * 0.15
        close_p = sl - gap * BREAK_REACH * progress
        low_p = close_p - gap * 0.15
        high_p = open_p + gap * 0.05
    last = max(0, len(plan.ohlc) - 1)
    half = max(8.0, float(ax.bw) * 0.45)
    cx = ax.x(last) + ax.step * 0.62
    cx = min(cx, ax.x1 - ax.pr - half - 8)
    cx = max(cx, ax.x(0) + ax.step)
    draw = ImageDraw.Draw(img)
    draw.line(
        [(cx, ax.y(high_p)), (cx, ax.y(low_p))],
        fill=BEAR, width=max(3, int(ax.bw) // 6),
    )
    top, bot = ax.y(max(open_p, close_p)), ax.y(min(open_p, close_p))
    draw.rectangle([cx - half, top, cx + half, max(bot, top + 4)], fill=BEAR)
    return {"low": low_p, "high": high_p, "close": close_p, "cx": cx}


def sl_break(img, ax, plan, progress, flash_on=False):
    """Price breaks the stop with a red candle, and the stop line flashes red."""
    import board_kit as K
    progress = clamp(progress)
    info = {
        "price": float(plan.sl), "progress": progress, "flash": False,
        "low": float(plan.sl), "high": float(plan.sl), "close": float(plan.sl), "cx": 0.0,
    }
    if progress <= 0.0:
        return info
    candle = _failure_candle(img, ax, plan, progress)
    info.update(candle)
    box = (ax.x0, ax.y0, ax.x1, ax.y1)
    y = ax.y(float(plan.sl))
    color = (255, 48, 48) if flash_on else SL
    _hline(img, box[0] + 24, box[2] - 24, y, color, width=8 if flash_on else 6)
    entry_y = ax.y(float(plan.entry))
    mark_y = entry_y + (y - entry_y) * progress
    del K
    short = str(getattr(plan, "side", "long")) == "short"
    # The broken stop price sits beside the break candle, on the inside of the line.
    _pill(img, candle["cx"] - 160, (mark_y + 14) if short else (mark_y - 66), _fmt(plan.sl), size=36)
    info["flash"] = bool(flash_on)
    return info


def hook_hold_until(topic):
    """When the hook narration ends. Whisper wins; the beat end is the fallback.

    A word that starts on the next beat is not part of the hook sentence.
    """
    beats = (topic or {}).get("_beat_times") or {}
    span = beats.get("hook")
    if not span or len(span) < 2:
        return 2.0
    t0, t1 = float(span[0]), float(span[1])
    last = None
    for word in (topic or {}).get("_audio_words") or []:
        try:
            start = float(word.get("start"))
            end = float(word.get("end") if word.get("end") is not None else start)
        except (TypeError, ValueError, AttributeError):
            continue
        if start < t0 - 0.05 or start >= t1 - 0.05:
            continue
        if end > (last or 0.0):
            last = end
    if last is None:
        return t1
    return max(t0, min(t1, last))


def rr_progress(t, clock):
    """Finished RR box until the hook sentence ends, then a fade, then the target beat.

    A caller that passes an explicit ``rr`` window keeps that window. The hook
    flash is only the clock_from mode. ``rr_hook_until`` is the whisper end.
    """
    if not (clock or {}).get("rr_hook"):
        return _progress(clock, "rr", t, 0.0)
    t = 0.0 if t is None else float(t)
    hold = (clock or {}).get("rr_hook_until")
    hold = 2.0 if hold is None else float(hold)
    if t <= hold:
        return 1.0
    fade_end = hold + REVEAL_S
    if t < fade_end:
        return 1.0 - ease((t - hold) / REVEAL_S)
    redraw = (clock or {}).get("rr_redraw")
    if redraw is None:
        return 0.0
    return window_progress(t, float(redraw), float(redraw) + REVEAL_S)


def clock_from(topic, plan=None):
    """Word times for the lines. The RR box is finished at t=0, then leaves."""
    topic = topic or {}
    beats = topic.get("_beat_times") or {}
    by = {}
    for row in topic.get("_chart_overlays") or []:
        by.setdefault(str(row.get("kind") or ""), row)

    def start(kind, beat):
        row = by.get(kind)
        if row and row.get("start") is not None:
            return float(row["start"])
        span = beats.get(beat)
        if span and len(span) >= 1:
            return float(span[0])
        return None

    def window(kind, beat, dur=REVEAL_S):
        t0 = start(kind, beat)
        if t0 is None:
            return None
        return (t0, t0 + float(dur))

    # The pehchan beat names the key candle, so the push-in starts with it.
    pehchan = beats.get("pehchan")
    zoom_at = float(pehchan[0]) if pehchan else start("trend", "pehchan")
    if zoom_at is None:
        zoom_at = start("entry", "entry")
    inv = beats.get("invalidation")
    windows = candle_windows(plan, beats) if plan is not None else None
    if windows and zoom_at is not None:
        # The highlight box lands on a candle that is already drawn.
        windows = _ready_before(windows, int(plan.focus), float(zoom_at))
    clock = {
        "candle_windows": windows,
        "trend": window("trend", "pehchan"),
        "sr": window("sr", "context"),
        "entry": window("entry", "entry"),
        "sl": window("sl", "sl"),
        "target": window("target", "target"),
        "rr_hook": True,
        "rr_hook_until": hook_hold_until(topic),
        "rr_redraw": start("target", "target"),
        "highlight": None if zoom_at is None else (zoom_at, zoom_at + ZOOM_S),
        "counter": window("target", "target", COUNTER_S),
        "title": (0.0, TITLE_S),
        "break": None if not inv else (float(inv[0]), float(inv[0]) + REVEAL_S),
        "cta": None if not beats.get("cta") else (float(beats["cta"][0]), float(beats["cta"][1])),
    }
    return clock


def _pill_on(t, window):
    """The label pill pops 120 ms after the line has finished drawing."""
    if not window or t is None:
        return 0.0
    return 1.0 if float(t) >= float(window[1]) + PILL_LAG_S else 0.0


def reveal_times(topic):
    """At most four whooshes: RR box, key-candle zoom, ENTRY, TARGET."""
    beats = (topic or {}).get("_beat_times") or {}
    by = {}
    for row in (topic or {}).get("_chart_overlays") or []:
        by.setdefault(str(row.get("kind") or ""), row)

    def at(kind, beat):
        row = by.get(kind)
        if row and row.get("start") is not None:
            return float(row["start"])
        span = beats.get(beat)
        if span:
            return float(span[0])
        return None

    cues = [0.0]
    for value in (at("trend", "pehchan"), at("entry", "entry"), at("target", "target")):
        if value is not None:
            cues.append(value)
    found = []
    seen = set()
    for raw in cues:
        key = round(float(raw), 3)
        if key < 0.0 or key in seen:
            continue
        seen.add(key)
        found.append(key)
        if len(found) >= MAX_WHOOSH:
            break
    return found


def unique_reveals(frames):
    """One whoosh per cue, four at most. The opening RR cue at t=0 is kept."""
    found = []
    seen = set()
    for frame in frames or []:
        for raw in getattr(frame, "motion_reveals", None) or []:
            try:
                t = round(float(raw), 3)
            except (TypeError, ValueError):
                continue
            if t < 0.0 or t in seen:
                continue
            seen.add(t)
            found.append(t)
            if len(found) >= MAX_WHOOSH:
                found.sort()
                return found
    found.sort()
    return found


def _candle_progresses(plan, t, clock):
    windows = (clock or {}).get("candle_windows")
    n = len(plan.ohlc)
    if windows and len(windows) == n:
        return [window_progress(t, pair[0], pair[1]) if pair else 1.0 for pair in windows]
    shared = _progress(clock, "candles", t, 1.0)
    return [shared] * n


def _hold_motion(img, t):
    """A slow brightness step in the lower margin so a 4s hold is not a still.

    Samples four seconds apart always differ. The level stays under the
    empty-row threshold, and it sits below the brand so the chart pixels
    and the settled still do not move.
    """
    if t is None or img is None:
        return
    from PIL import ImageDraw
    residue = int(float(t) * 10) % 7
    level = 30 + residue * 8
    color = (level, level, level)
    width, height = img.size
    top = max(0, int(height) - 180)
    ImageDraw.Draw(img).rectangle([0, top, int(width) - 1, int(height) - 1], fill=color)


def draw_plan(img, plan, shown, t, clock):
    """One continuous chart. Overlays accumulate. Nothing here hard-cuts a beat."""
    import trade_plan

    shown = list(shown or [])
    box, lo, hi, pad = motion_geometry(plan)
    img._placed_pills = []
    img._pill_bounds = box
    img._pill_queue = []
    line_ys = []
    per = _candle_progresses(plan, t, clock)
    zoom_start = None
    highlight_window = (clock or {}).get("highlight")
    if highlight_window:
        zoom_start = float(highlight_window[0])
    zoom, attention = key_zoom_at(t, zoom_start)
    focus = int(plan.focus)
    ax = candle_build(
        img, box, plan.ohlc, lo, hi, 1.0, pad,
        per_candle=per,
        focus=focus if attention else None,
        dim=CANDLE_DIM if attention else 1.0,
    )
    if attention and 0 <= focus < len(plan.ohlc):
        from PIL import ImageDraw
        candle = plan.ohlc[focus]
        cx = ax.x(focus)
        top = ax.y(max(candle[0], candle[3]))
        bot = ax.y(min(candle[0], candle[3]))
        ImageDraw.Draw(img).rectangle(
            [cx - ax.bw / 2 - 8, top - 10, cx + ax.bw / 2 + 8, bot + 10],
            outline=ATTENTION, width=6,
        )
        _zoom_candle(img, cx, (top + bot) / 2.0, zoom)
    highlight = {"zoom": zoom, "dim": CANDLE_DIM if attention else 1.0, "attention": attention}
    trend_p = _progress(clock, "trend", t, 0.0)
    if trend_p > 0.0:
        trend_arrow(img, ax, plan, trend_p)
    sr_p = _progress(clock, "sr", t, 0.0)
    sr_price = float(plan.sr)
    for row in shown:
        if row.get("kind") == "sr" and row.get("price") is not None:
            sr_price = float(row["price"])
    if sr_p > 0.0:
        sr_band(img, ax, sr_price, sr_p, WHITE)
    colors = {"sr": WHITE, "entry": ENTRY, "sl": SL, "target": TARGET}
    sides = {"sr": "left", "entry": "right", "sl": "left", "target": "right"}
    labels = {}
    for row in shown:
        if row.get("kind") in colors and row.get("price") is not None:
            labels[row["kind"]] = row
    names = {"sr": "SR", "entry": "ENTRY", "sl": "SL", "target": "TARGET"}
    levels = {}
    anchor = None
    if 0 <= focus < len(plan.ohlc):
        anchor = (ax.x(focus), ax.y(plan.ohlc[focus][3]))
    # Finished ENTRY/SL/TARGET pills share the hook sentence, then fade.
    # They are not the spoken lines: those still wait for their own beat.
    ghost = rr_progress(t, clock) if (clock or {}).get("rr_hook") else 0.0
    on_screen = []
    for kind in _LEVELS:
        row = labels.get(kind)
        price = float(row["price"]) if row and row.get("price") is not None else {
            "sr": float(plan.sr), "entry": float(plan.entry),
            "sl": float(plan.sl), "target": float(plan.target),
        }[kind]
        label = f"{names[kind]} {_fmt(price)}"
        default = 0.0
        prog = _progress(clock, kind, t, default)
        window = (clock or {}).get(kind)
        pill = _pill_on(t, window) if window else (1.0 if prog >= 1.0 else 0.0)
        drawn = {"price_label": None, "drawn": False, "x1": box[0] + 24, "pill": False}
        if prog > 0.0 or (ghost > 0.0 and kind in ("entry", "sl", "target")):
            line_ys.append(float(ax.y(price)))
        if prog > 0.0:
            drawn = level_line(
                img, box, ax.y(price), colors[kind], label, sides[kind], prog,
                pill=pill, dashed=(kind == "sr"), width=6,
                anchor=anchor if kind != "sr" else None,
            )
        elif ghost > 0.0 and kind in ("entry", "sl", "target"):
            if ghost >= 0.85:
                level_line(
                    img, box, ax.y(price), colors[kind], label, sides[kind], 1.0,
                    pill=1.0, dashed=False, width=6, anchor=None,
                )
            else:
                _fade_hline(img, box[0] + 24, box[2] - 24, ax.y(price), colors[kind], ghost, width=6)
            on_screen.append(label)
        if drawn.get("price_label"):
            on_screen.append(drawn["price_label"])
        levels[kind] = {
            "price": price, "drawn": bool(drawn["drawn"]), "label": label,
            "progress": prog, "pill": bool(drawn.get("pill")),
        }
    rr_p = ghost if (clock or {}).get("rr_hook") else rr_progress(t, clock)
    rr = risk_reward_box(img, plan, rr_p, ax) if rr_p > 0.0 else rr_readout(plan)
    if rr_p <= 0.0:
        rr["shown"] = False
    elif rr.get("text"):
        on_screen.append(rr["text"])
    if plan.rsi and any(row.get("kind") == "rsi_os" for row in shown) and trend_p >= 1.0:
        trade_plan._rsi_strip(img, plan)
    break_p = _progress(clock, "break", t, 0.0)
    flash_on = bool(break_p > 0.0 and t is not None and int(float(t) * 4) % 2 == 0)
    broken = sl_break(img, ax, plan, break_p, flash_on=flash_on) if break_p > 0.0 else {
        "price": float(plan.sl), "progress": 0.0, "flash": False,
    }
    frame = getattr(ax, "frame", None) or box
    pill_rects = _flush_pills(img, line_ys, frame)
    counter_p = _progress(clock, "counter", t, 0.0)
    # The line pill is the target label. A second big TARGET under the chart covers the caption.
    counter = number_counter(img, plan.target, counter_p, label="TARGET", paint=False)
    _hold_motion(img, t)
    title_p = _progress(clock, "title", t, 0.0)
    candles_p = sum(per) / float(len(per) or 1)
    return {
        "progress": {
            "candles": candles_p,
            "trend": trend_p,
            "sr": sr_p,
            "entry": levels["entry"]["progress"],
            "sl": levels["sl"]["progress"],
            "target": levels["target"]["progress"],
            "rr": rr_p,
            "highlight": 1.0 if attention else 0.0,
            "counter": counter_p,
            "title": title_p,
            "break": break_p,
        },
        "levels": levels,
        "rr": rr,
        "highlight": highlight,
        "counter": counter,
        "title": {"progress": title_p, "shown": title_p >= 1.0, "text": ""},
        "break": broken,
        "candles": per,
        "on_screen_text": on_screen,
        "frame": tuple(float(v) for v in frame),
        "pill_rects": [tuple(r) for r in pill_rects],
    }


def paint_caption(img, text, y, size=CAPTION_SIZE):
    """Bottom-band caption. One keyword is yellow. The rest is white."""
    import scene_frames

    pre, word, post = split_keyword(text)
    if not str(text or "").strip():
        return []
    if not word:
        scene_frames._text(
            img, 540, y, str(text), WHITE, int(size),
            anchor="mt", band=False, role="caption",
        )
        return [str(text)]
    w_pre = scene_frames._text_extent(pre, int(size))[0] if pre else 0
    w_word = scene_frames._text_extent(word, int(size))[0]
    w_post = scene_frames._text_extent(post, int(size))[0] if post else 0
    left = 540 - (w_pre + w_word + w_post) / 2.0
    cursor = left
    drawn = []
    if pre:
        scene_frames._text(
            img, cursor, y, pre, WHITE, int(size),
            anchor="lt", band=False, role="caption",
        )
        drawn.append(pre)
        cursor += w_pre
    scene_frames._text(
        img, cursor, y, word, ATTENTION, int(size),
        anchor="lt", band=False, role="caption",
    )
    drawn.append(word)
    cursor += w_word
    if post:
        scene_frames._text(
            img, cursor, y, post, WHITE, int(size),
            anchor="lt", band=False, role="caption",
        )
        drawn.append(post)
    return drawn


def render_whoosh(path, seconds=WHOOSH_SECONDS, sr=44100):
    """Pink-noise bands that sweep high to low. No downloaded sample."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        "[0:a]asplit=3[a][b][c];"
        "[a]highpass=f=1400,lowpass=f=4200,afade=t=in:st=0:d=0.02,afade=t=out:st=0.05:d=0.10[hi];"
        "[b]highpass=f=500,lowpass=f=1600,afade=t=in:st=0.04:d=0.03,afade=t=out:st=0.10:d=0.12[mid];"
        "[c]highpass=f=140,lowpass=f=600,afade=t=in:st=0.08:d=0.04,afade=t=out:st=0.16:d=0.22[lo];"
        "[hi][mid][lo]amix=inputs=3:normalize=0,volume=0.7"
    )
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"anoisesrc=color=pink:sample_rate={int(sr)}:duration={float(seconds):.3f}:amplitude=0.45:seed=7",
            "-filter_complex", graph,
            "-ar", str(int(sr)), "-ac", "1", str(path),
        ],
        check=True,
    )
    return path


def _read_wav(path):
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sr = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    import numpy as np
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    return pcm, sr


def _write_wav(path, samples, sr):
    import numpy as np
    samples = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        channels = 1
        pcm = (samples * 32767.0).astype("<i2")
    else:
        channels = samples.shape[1]
        pcm = (samples.reshape(-1) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(int(sr))
        handle.writeframes(pcm.tobytes())
    return path


def _rms(mono):
    import numpy as np
    mono = np.asarray(mono, dtype=np.float64)
    if mono.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(mono))))


def mix_under(voice_path, reveals, out_path, under_db=WHOOSH_UNDER_DB):
    """Place one whoosh at each reveal, about 20 dB under the voice RMS.

    Four cues maximum. The voice file is only read. No bed and no music.
    """
    import numpy as np

    voice, sr = _read_wav(voice_path)
    stereo = voice.ndim == 2
    mono = voice.mean(axis=1) if stereo else voice
    voice_rms = _rms(mono)
    whoosh_path = Path(out_path).with_name("_motion_whoosh.wav")
    render_whoosh(whoosh_path, sr=sr)
    clip, clip_sr = _read_wav(whoosh_path)
    if clip_sr != sr:
        raise RuntimeError(f"whoosh sample rate {clip_sr} != voice {sr}")
    if clip.ndim > 1:
        clip = clip[:, 0]
    peak = float(np.max(np.abs(clip))) if clip.size else 0.0
    track = np.zeros(mono.shape[0], dtype=np.float32)
    cues = []
    seen = set()
    for raw in reveals or []:
        try:
            t = float(raw)
        except (TypeError, ValueError):
            continue
        if t < 0.0:
            continue
        key = round(t, 3)
        if key in seen:
            continue
        seen.add(key)
        cues.append(t)
        if len(cues) >= MAX_WHOOSH:
            break
    if peak > 0.0 and cues:
        for t in cues:
            i = int(round(t * sr))
            if i < 0 or i >= track.shape[0]:
                continue
            n = min(clip.shape[0], track.shape[0] - i)
            track[i:i + n] += clip[:n]
        bus_peak = float(np.max(np.abs(track))) if track.size else 0.0
        if bus_peak > 0.0 and voice_rms > 0.0:
            cap = voice_rms * (10 ** (-float(under_db) / 20.0))
            steps = max(0, int(math.floor(cap * 32767.0)) - 1)
            pcm_cap = steps / 32767.0
            track *= np.float32(pcm_cap / bus_peak)
    if stereo:
        mixed = voice + track[:, None]
    else:
        mixed = voice + track
    mix_peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if mix_peak > 0.98:
        mixed = mixed * np.float32(0.98 / mix_peak)
    _write_wav(out_path, mixed, sr)
    sfx_peak = float(np.max(np.abs(track))) if track.size else 0.0
    under = None
    if voice_rms > 0.0 and sfx_peak > 0.0:
        under = 20.0 * math.log10(voice_rms / sfx_peak)
    try:
        whoosh_path.unlink()
    except OSError:
        pass
    return {"under_db": under, "sfx_peak": sfx_peak, "voice_rms": voice_rms, "cues": cues}
