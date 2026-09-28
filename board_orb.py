"""Dedicated Opening Range Breakout board scenes.

Imported by board_scenes so intraday_opening_range / intraday_orb never fall
through to the empty generic finance card (text + ring, no chart).
"""
import random

from board_kit import (
    BOARD, BLUE, GREEN, INK, RED, YELLOW, SL_RED, TARGET_GREEN,
    arrow, candles, dashed, label, panel, ring,
)

X0, Y0, X1, Y1 = BOARD


def _orb_candles():
    """Synthetic 1-min style session: 3 pre-range, 5 range (tight), 1 breakout, 4 follow."""
    rng = random.Random(42)
    out, p = [], 100.0
    # pre-open drift
    for _ in range(3):
        o = p
        c = o + rng.uniform(-0.4, 0.4)
        hi = max(o, c) + rng.uniform(0.2, 0.5)
        lo = min(o, c) - rng.uniform(0.2, 0.5)
        out.append((o, hi, lo, c))
        p = c
    # opening range — tight 15-min box
    range_hi, range_lo = p + 1.8, p - 1.6
    for i in range(5):
        o = p
        c = o + rng.uniform(-0.6, 0.6)
        c = max(range_lo + 0.2, min(range_hi - 0.2, c))
        hi = min(range_hi, max(o, c) + rng.uniform(0.1, 0.4))
        lo = max(range_lo, min(o, c) - rng.uniform(0.1, 0.4))
        out.append((o, hi, lo, c))
        p = c
    # breakout candle closes above range high
    o = p
    c = range_hi + 1.4
    hi = c + 0.4
    lo = o - 0.2
    out.append((o, hi, lo, c))
    p = c
    # follow-through
    for _ in range(4):
        o = p
        c = o + rng.uniform(0.3, 1.1)
        hi = max(o, c) + rng.uniform(0.15, 0.4)
        lo = min(o, c) - rng.uniform(0.1, 0.35)
        out.append((o, hi, lo, c))
        p = c
    range_start, range_end = 3, 7  # inclusive indices of OR candles
    break_i = 8
    return out, range_hi, range_lo, range_start, range_end, break_i


def opening_range_breakout():
    """Real candle chart: mark OR high/low, then breakout + SL on other side."""
    allc, or_hi, or_lo, i0, i1, bi = _orb_candles()
    lo = min(c[2] for c in allc) - 0.5
    hi = max(c[1] for c in allc) + 0.5

    def beat(b, img):
        panel(img)
        ax = candles(img, BOARD, allc, lo, hi)
        A = img.copy()
        x0, x1 = X0 + 30, X1 - 30

        if b == "hook":
            # highlight the opening-range window
            cx = (ax.x(i0) + ax.x(i1)) / 2
            ring(img, cx, (ax.y(or_hi) + ax.y(or_lo)) / 2,
                 max(70, (ax.x(i1) - ax.x(i0)) / 2 + 40),
                 (ax.y(or_lo) - ax.y(or_hi)) / 2 + 50, YELLOW)
            label(img, X0 + 50, Y0 + 40, "FIRST 15 MIN = OPENING RANGE", YELLOW, 34, bg=(6, 10, 20))
        elif b == "pehchan":
            dashed(img, x0, x1, ax.y(or_hi), BLUE, "OR HIGH", label_side="left")
            dashed(img, x0, x1, ax.y(or_lo), BLUE, "OR LOW", label_side="left")
            label(img, X0 + 50, Y0 + 40, "MARK HIGH + LOW", INK, 36, bg=(6, 10, 20))
        elif b == "matlab":
            dashed(img, x0, x1, ax.y(or_hi), BLUE, "OR HIGH", label_side="left")
            dashed(img, x0, x1, ax.y(or_lo), BLUE, "OR LOW", label_side="left")
            # ring breakout candle
            cc = allc[bi]
            ring(img, ax.x(bi), (ax.y(cc[1]) + ax.y(cc[2])) / 2, 55,
                 (ax.y(cc[2]) - ax.y(cc[1])) / 2 + 40, GREEN)
            arrow(img, (ax.x(bi), ax.y(or_hi) + 20), (ax.x(bi), ax.y(cc[3]) - 10), GREEN, 10)
            label(img, X0 + 50, Y0 + 40, "CLOSE ABOVE OR HIGH = BREAKOUT", GREEN, 32, bg=(6, 10, 20))
        else:  # karein
            dashed(img, x0, x1, ax.y(or_hi), TARGET_GREEN, "ENTRY ZONE", label_side="left")
            dashed(img, x0, x1, ax.y(or_lo), SL_RED, "SL (OTHER SIDE)", label_side="left")
            cc = allc[bi]
            ring(img, ax.x(bi), (ax.y(cc[1]) + ax.y(cc[2])) / 2, 50,
                 (ax.y(cc[2]) - ax.y(cc[1])) / 2 + 35, GREEN)
            label(img, X0 + 50, Y0 + 40, "VOLUME + RISK FIRST — NOT EVERY BO WORKS", YELLOW, 28, bg=(6, 10, 20))
        return A, img

    return beat


def intraday_chart_fallback(key):
    """Any other intraday_* topic: still show a real range chart, not empty text."""
    return opening_range_breakout()
