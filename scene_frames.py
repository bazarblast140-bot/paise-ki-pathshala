"""Several pictures per beat: zoom, callouts, and the ATR storyboard.

ATR candles are real month-to-month closes (open = previous close, high/low =
the body). Wicks are not invented. Other topics reuse their chart and punch in.
"""
import board_kit as K
import board_scenes as S
import market_data
from PIL import Image


def zoom_at(img, fx, fy, scale=0.7, shift_x=0):
    """Crop around a point and scale back to the full frame. scale < 1 zooms in."""
    w, h = img.size
    cw = max(2, int(w * scale))
    ch = max(2, int(h * scale))
    cw -= cw % 2
    ch -= ch % 2
    x0 = int(max(0, min(w - cw, fx + shift_x - cw / 2)))
    y0 = int(max(0, min(h - ch, fy - ch / 2)))
    crop = img.crop((x0, y0, x0 + cw, y0 + ch))
    return crop.resize((w, h), Image.Resampling.LANCZOS)


def screen_callout(img, text, x=160, y=250):
    out = img.copy()
    K.arrow(out, (860, y + 40), (560, y + 180), K.YELLOW, 8, 28)
    K.label(out, x, y, text, K.YELLOW, 34, bg=(8, 12, 24))
    return out


def _bodies(lesson):
    closes = [float(p["close"]) for p in lesson["points"]]
    ohlc = []
    for i in range(1, len(closes)):
        o, c = closes[i - 1], closes[i]
        ohlc.append((o, max(o, c), min(o, c), c))
    return ohlc


def _limits(ohlc, lesson):
    entry = float(lesson["entry"])
    atr = float(lesson["atr"])
    sl = float(lesson["sl"])
    target = float(lesson["target"])
    tight = entry - atr
    lo = min([c[2] for c in ohlc] + [sl, tight])
    hi = max([c[1] for c in ohlc] + [target])
    return lo, hi, tight


def _base_chart(ohlc, lo, hi, box=None):
    img = K.background()
    K.panel(img)
    ax = K.candles(img, box or K.BOARD, ohlc, lo, hi)
    K.label(img, 70, 190, "HDFC BANK  MONTHLY CLOSE", K.INK, 28)
    return img, ax


def _hline(img, ax, price, color, text, size=26):
    K.dashed(img, ax.x0 + 24, ax.x1 - 24, ax.y(price), color, text, size=size, label_side="left")


def _atr_states(beat, lesson):
    """Ordered drawings. Later scenes reveal the next line or a closer crop."""
    ohlc = _bodies(lesson)
    lo, hi, tight = _limits(ohlc, lesson)
    entry = float(lesson["entry"])
    sl = float(lesson["sl"])
    target = float(lesson["target"])
    atr = lesson["atr"]
    dist = lesson["distance"]
    i_last = len(ohlc) - 1
    i_rng = max(0, len(ohlc) - 6)

    def wide_tight():
        img, ax = _base_chart(ohlc, lo, hi)
        _hline(img, ax, tight, K.RED, "TOO TIGHT", 28)
        K.cross(img, ax.x(i_last), ax.y(tight), 26, K.RED)
        K.label(img, 70, 240, "NOISE HITS A TIGHT SL", K.RED, 30, bg=(8, 12, 24))
        return img, ax, False

    def zoom_tight():
        img, ax, _ = wide_tight()
        fx, fy = ax.x(i_last), ax.y(tight)
        return zoom_at(img, fx, fy, 0.62), None, True

    def callout_noise():
        img, ax, _ = wide_tight()
        fx, fy = ax.x(i_last), (ax.y(ohlc[i_last][1]) + ax.y(ohlc[i_last][2])) / 2
        img = zoom_at(img, fx, fy, 0.7, shift_x=-30)
        return screen_callout(img, "TIGHT SL"), None, True

    def range_wide():
        img, ax = _base_chart(ohlc, lo, hi)
        h, l = ohlc[i_rng][1], ohlc[i_rng][2]
        x = ax.x(i_rng) + 28
        K.arrow(img, (x, ax.y(l)), (x, ax.y(h)), K.YELLOW, 6, 20)
        K.label(img, 70, 240, f"ATR(14) = {atr}", K.YELLOW, 34, bg=(8, 12, 24))
        return img, ax, False

    def range_zoom():
        img, ax, _ = range_wide()
        h, l = ohlc[i_rng][1], ohlc[i_rng][2]
        return zoom_at(img, ax.x(i_rng), (ax.y(h) + ax.y(l)) / 2, 0.58), None, True

    def range_rule():
        img, ax = _base_chart(ohlc, lo, hi)
        K.label(img, 70, 250, "14-CANDLE AVERAGE", K.YELLOW, 36, bg=(8, 12, 24))
        K.arrow(img, (760, 300), (ax.x(i_rng), ax.y(ohlc[i_rng][3])), K.YELLOW, 8, 26)
        return img, ax, True

    def entry_only():
        img, ax = _base_chart(ohlc, lo, hi)
        _hline(img, ax, entry, K.TARGET_GREEN, f"ENTRY {lesson['entry']}", 26)
        return img, ax, True

    def entry_sl():
        img, ax = _base_chart(ohlc, lo, hi)
        _hline(img, ax, entry, K.TARGET_GREEN, f"ENTRY {lesson['entry']}", 24)
        _hline(img, ax, sl, K.SL_RED, f"SL {lesson['sl']}", 24)
        return img, ax, True

    def gap_zoom():
        img, ax, _ = entry_sl()
        mid_y = (ax.y(entry) + ax.y(sl)) / 2
        img = zoom_at(img, ax.x(i_last) - 40, mid_y, 0.6)
        return screen_callout(img, f"1.5 x ATR = {dist}"), None, True

    def all_lines():
        img, ax = _base_chart(ohlc, lo, hi)
        _hline(img, ax, entry, K.TARGET_GREEN, f"ENTRY {lesson['entry']}", 24)
        _hline(img, ax, sl, K.SL_RED, f"SL {lesson['sl']}", 24)
        _hline(img, ax, target, K.BLUE, f"ABOVE {lesson['target']}", 24)
        K.arrow(img, (ax.x(i_last) + 50, ax.y(sl)), (ax.x(i_last) + 50, ax.y(entry)), K.YELLOW, 6, 18)
        return img, ax, True

    def split():
        img = K.background()
        K.panel(img)
        left = (40, 250, 520, 1020)
        right = (560, 250, 1040, 1020)
        ax_l = K.candles(img, left, ohlc, lo, hi, pad=(16, 28, 16, 28))
        ax_r = K.candles(img, right, ohlc, lo, hi, pad=(16, 28, 16, 28))
        _hline(img, ax_l, tight, K.RED, "WRONG", 22)
        _hline(img, ax_r, sl, K.SL_RED, "ATR SL", 22)
        K.label(img, 70, 190, "TOO TIGHT", K.RED, 32)
        K.label(img, 600, 190, "1.5 x ATR", K.GREEN, 32)
        K.cross(img, 120, 210, 22, K.RED)
        K.tick(img, 980, 210, 22, K.GREEN)
        return img, None, True

    def right_zoom():
        img, ax = _base_chart(ohlc, lo, hi)
        _hline(img, ax, entry, K.TARGET_GREEN, f"ENTRY {lesson['entry']}", 24)
        _hline(img, ax, sl, K.SL_RED, f"SL {lesson['sl']}", 24)
        img = zoom_at(img, ax.x(i_last), ax.y(sl), 0.66, shift_x=20)
        return img, None, False

    def size_note():
        img, _, _ = all_lines()
        return screen_callout(img, "WIDER SL, SMALLER SIZE"), None, True

    def takeaway():
        img, _, _ = all_lines()
        img = zoom_at(img, 620, 560, 0.78)
        return screen_callout(img, f"RULE  1.5 x {atr}"), None, True

    table = {
        "hook": [wide_tight, zoom_tight, callout_noise],
        "pehchan": [range_wide, range_zoom, range_rule],
        "matlab": [entry_only, entry_sl, gap_zoom, all_lines],
        "karein": [split, right_zoom, size_note, takeaway],
    }
    return table[beat]


def _atr_frames(beat, n, lesson):
    states = _atr_states(beat, lesson)
    frames = []
    flags = []
    for i in range(n):
        if i < len(states):
            img, _ax, flag = states[i]()
        else:
            img, _ax, _flag = states[-1]()
            img = zoom_at(img, 540, 620, max(0.55, 0.75 - 0.05 * (i - len(states) + 1)))
            flag = True
        frames.append(img)
        flags.append(flag)
    return frames, flags


def _generic_frames(topic, beat, n, spec):
    mode = (spec or {}).get("mode")
    if mode == "non_numeric":
        bases = [market_data.non_numeric_frame(topic, beat, i % 2) for i in range(min(n, 2))]
    else:
        img = K.background()
        pair = S.render_beat(topic["key"], beat, img)
        bases = [pair[-1]]
    labels = ("KEY AREA", "RULE", "LOOK HERE", "NEXT STEP")
    frames = []
    flags = []
    for i in range(n):
        src = bases[min(i, len(bases) - 1)].copy()
        if i == 0:
            frame = src
            flag = False
        elif i % 2 == 1:
            frame = zoom_at(src, 560, 640, 0.70 + (i % 3) * 0.03, shift_x=(i % 5 - 2) * 36)
            # Non-numeric cards must not gain a digit. These labels have none.
            frame = screen_callout(frame, labels[i % len(labels)])
            flag = True
        else:
            frame = zoom_at(src, 460 + (i * 24), 720, 0.62, shift_x=-50)
            flag = False
        if mode == "series":
            frame = market_data.stamp(frame, spec)
        frames.append(frame)
        flags.append(flag)
    return frames, flags


def frames_for(topic, beat, n, spec):
    """Return (images, callout flags), one per planned scene in this beat."""
    n = max(1, int(n))
    if (spec or {}).get("atr"):
        frames, flags = _atr_frames(beat, n, spec["atr"])
        frames = [market_data.stamp(im, spec) for im in frames]
        return frames, flags
    return _generic_frames(topic, beat, n, spec)
