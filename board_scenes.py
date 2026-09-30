"""Scenes for the Chart Board template: key -> 4 beats, each beat -> (base, annot).

render_beat(key, beat, img) draws on an image that already has the background
and chips; it returns (A, B): A = picture only, B = picture + rings/arrows/
lines/labels. The video shows A briefly, then B, so every beat 'explains'.

Beats: "hook", "pehchan", "matlab", "karein".
All labels here are Latin/numbers; Hindi is added by libass.
"""
import json
import math
import pathlib
import random

import board_kit as K
from board_kit import (BOARD, BLUE, GREEN, INK, MUTED, RED, YELLOW, SL_RED, TARGET_GREEN, Axes, arrow, bars, big_text,
                       candles, cross, dashed, fit_label, label, ohlc_from_closes, panel, payoff,
                       polyline, ring, rsi, series_panel, sma, ema, tick, timeline)

ROOT = pathlib.Path(__file__).parent
X0, Y0, X1, Y1 = BOARD
TOP_BOX = (X0, Y0, X1, 730)          # price panel when an indicator panel is below
BOT_BOX = (X0, 745, X1, Y1)          # indicator panel
BEATS = ("hook", "pehchan", "matlab", "karein")


# ================================================================ candle patterns
def _trend(n, start, step, seed, body=0.7):
    rng = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        o = p + rng.uniform(-0.3, 0.3)
        c = o + step * rng.uniform(0.8, 1.2)
        hi = max(o, c) + abs(step) * rng.uniform(0.15, 0.35)
        lo = min(o, c) - abs(step) * rng.uniform(0.15, 0.35)
        out.append((o, hi, lo, c))
        p = c
    return out


def _pattern(key):
    """Returns (before, pattern, confirm, direction, anatomy_labels).
    direction: 'bull' | 'bear' | 'neutral'."""
    if key in ("hammer", "inverted_hammer", "bullish_engulfing", "morning_star", "piercing",
               "three_white_soldiers", "confirmation"):
        before = _trend(7, 130, -3.2, 11)
    elif key in ("doji", "spinning_top", "marubozu"):
        before = _trend(7, 100, 2.0, 12)
    else:
        before = _trend(7, 100, 3.2, 13)
    p = before[-1][3]
    if key in ("hammer", "confirmation"):
        pat = [(p - 0.4, p + 0.6, p - 11.0, p + 0.3)]
        conf = [(p + 0.5, p + 6.0, p - 0.2, p + 5.4)] if key == "hammer" else [(p - 0.2, p + 0.4, p - 14.5, p - 13.2)]
        return before, pat, conf, "bull", ["LOWER WICK >= 2x BODY", "SMALL BODY (TOP)", "UPPER WICK ~ 0"]
    if key == "inverted_hammer":
        pat = [(p - 0.3, p + 10.5, p - 0.8, p + 0.4)]
        conf = [(p + 0.6, p + 6.5, p + 0.2, p + 6.0)]
        return before, pat, conf, "bull", ["UPPER WICK >= 2x BODY", "SMALL BODY (BOTTOM)", "AFTER DOWNTREND"]
    if key == "shooting_star":
        pat = [(p + 0.6, p + 12.5, p - 0.3, p - 0.6)]
        conf = [(p - 1.0, p - 0.5, p - 8.8, p - 8.0)]
        return before, pat, conf, "bear", ["UPPER WICK >= 2x BODY", "SMALL BODY (BOTTOM)", "AFTER UPTREND"]
    if key == "hanging_man":
        pat = [(p + 0.4, p + 0.9, p - 10.5, p - 0.2)]
        conf = [(p - 0.6, p - 0.2, p - 8.0, p - 7.4)]
        return before, pat, conf, "bear", ["LOWER WICK >= 2x BODY", "SMALL BODY (TOP)", "AFTER UPTREND"]
    if key == "doji":
        pat = [(p + 0.1, p + 5.0, p - 5.0, p + 0.15)]
        conf = [(p + 0.2, p + 1.0, p - 6.5, p - 6.0)]
        return before, pat, conf, "neutral", ["OPEN = CLOSE", "WICKS BOTH SIDES", "INDECISION"]
    if key == "spinning_top":
        pat = [(p - 0.8, p + 4.5, p - 5.2, p + 0.8)]
        conf = [(p + 0.6, p + 1.2, p - 6.2, p - 5.6)]
        return before, pat, conf, "neutral", ["SMALL BODY", "LONG WICKS BOTH SIDES", "INDECISION"]
    if key == "marubozu":
        pat = [(p, p + 12.0, p, p + 12.0)]
        conf = [(p + 12.1, p + 16.5, p + 11.2, p + 15.8)]
        return before, pat, conf, "bull", ["NO WICKS", "OPEN = LOW", "CLOSE = HIGH"]
    if key == "bullish_engulfing":
        pat = [(p + 0.4, p + 0.9, p - 3.6, p - 3.0), (p - 3.6, p + 3.6, p - 4.0, p + 3.2)]
        conf = [(p + 3.2, p + 7.4, p + 2.6, p + 6.9)]
        return before, pat, conf, "bull", ["SMALL RED", "BIG GREEN", "GREEN COVERS RED BODY"]
    if key == "bearish_engulfing":
        pat = [(p - 0.4, p + 3.6, p - 0.9, p + 3.0), (p + 3.6, p + 4.0, p - 3.6, p - 3.2)]
        conf = [(p - 3.2, p - 2.6, p - 7.4, p - 6.9)]
        return before, pat, conf, "bear", ["SMALL GREEN", "BIG RED", "RED COVERS GREEN BODY"]
    if key == "morning_star":
        pat = [(p + 0.4, p + 0.8, p - 8.0, p - 7.4), (p - 8.6, p - 7.2, p - 10.2, p - 9.0),
               (p - 8.8, p - 1.0, p - 9.2, p - 1.6)]
        conf = [(p - 1.5, p + 3.8, p - 2.0, p + 3.2)]
        return before, pat, conf, "bull", ["1 BIG RED", "2 SMALL", "3 BIG GREEN"]
    if key == "evening_star":
        pat = [(p - 0.4, p + 8.0, p - 0.8, p + 7.4), (p + 8.6, p + 10.2, p + 7.2, p + 9.0),
               (p + 8.8, p + 9.2, p + 1.0, p + 1.6)]
        conf = [(p + 1.5, p + 2.0, p - 3.8, p - 3.2)]
        return before, pat, conf, "bear", ["1 BIG GREEN", "2 SMALL", "3 BIG RED"]
    if key == "three_white_soldiers":
        pat = [(p, p + 6.2, p - 0.4, p + 5.8), (p + 5.0, p + 11.6, p + 4.6, p + 11.2),
               (p + 10.4, p + 17.0, p + 10.0, p + 16.6)]
        conf = []
        return before, pat, conf, "bull", ["3 BIG GREEN", "EACH CLOSES HIGHER", "SMALL WICKS"]
    if key == "three_black_crows":
        pat = [(p, p + 0.4, p - 6.2, p - 5.8), (p - 5.0, p - 4.6, p - 11.6, p - 11.2),
               (p - 10.4, p - 10.0, p - 17.0, p - 16.6)]
        conf = []
        return before, pat, conf, "bear", ["3 BIG RED", "EACH CLOSES LOWER", "SMALL WICKS"]
    if key == "dark_cloud":
        pat = [(p - 0.2, p + 7.6, p - 0.6, p + 7.2), (p + 8.4, p + 8.9, p + 2.0, p + 2.6)]
        conf = [(p + 2.4, p + 2.8, p - 2.4, p - 2.0)]
        return before, pat, conf, "bear", ["BIG GREEN", "RED OPENS ABOVE", "CLOSES BELOW MIDDLE"]
    if key == "piercing":
        pat = [(p + 0.2, p + 0.6, p - 7.6, p - 7.2), (p - 8.4, p - 2.0, p - 8.9, p - 2.6)]
        conf = [(p - 2.4, p + 2.4, p - 2.8, p + 2.0)]
        return before, pat, conf, "bull", ["BIG RED", "GREEN OPENS BELOW", "CLOSES ABOVE MIDDLE"]
    raise KeyError(key)


def _zoom(img, pat, labels, direction):
    """Big anatomy drawing of the pattern candles (1-3), labels on the right."""
    panel(img)
    lo = min(c[2] for c in pat)
    hi = max(c[1] for c in pat)
    n = len(pat)
    box = (X0 + 40, Y0 + 60, X0 + 430, Y1 - 60)       # fixed: labels start at X0+470
    ax = candles(img, box, pat, lo, hi, grid=False, width_frac=0.62, pad=(20, 30, 20, 30))
    return ax


def _zoom_labels(img, labels):
    x = X0 + 470
    ys = [Y0 + 150, Y0 + 330, Y0 + 510]
    for t, y in zip(labels, ys):
        label(img, x - 40, y + 22, "•", YELLOW, 60)
        fit_label(img, x, y, t, X1 - x - 30, INK, 38)


def candle_scene(key):
    before, pat, conf, direction, anatomy = _pattern(key)
    seq = before + pat
    allc = seq + conf
    lo = min(c[2] for c in allc)
    hi = max(c[1] for c in allc)
    i0, i1 = len(before), len(before) + len(pat) - 1

    def ring_pattern(img, ax, col=YELLOW):
        top = max(c[1] for c in pat)
        bot = min(c[2] for c in pat)
        cx = (ax.x(i0) + ax.x(i1)) / 2
        rx = max(60, (ax.x(i1) - ax.x(i0)) / 2 + 55)
        ring(img, cx, (ax.y(top) + ax.y(bot)) / 2, rx, (ax.y(bot) - ax.y(top)) / 2 + 45, col)

    def base_full(img, with_conf=False):
        panel(img)
        return candles(img, BOARD, allc if with_conf else seq, lo, hi)

    def beat(b, img):
        if b == "hook":
            ax = base_full(img)
            A = img.copy()
            ring_pattern(img, ax)
            x = ax.x(i1) + 70
            if direction == "bull":
                arrow(img, (x, ax.y(lo) - 10), (x, ax.y(lo) - 230), GREEN)
            elif direction == "bear":
                arrow(img, (x, ax.y(hi) + 10), (x, ax.y(hi) + 230), RED)
            else:
                label(img, x - 20, ax.y(hi) + 20, "?", YELLOW, 110)
            return A, img
        if b == "pehchan":
            _zoom(img, pat, anatomy, direction)
            A = img.copy()
            _zoom_labels(img, anatomy)
            return A, img
        if b == "matlab":
            ax = base_full(img)
            A = img.copy()
            c = pat[-1]
            x = ax.x(i1)
            # free corner: uptrend leaves top-left empty, downtrend bottom-left
            up = before[-1][3] > before[0][3]
            lx, ly = X0 + 50, (Y0 + 50 if up else Y1 - 140)
            if key in ("marubozu", "three_white_soldiers"):
                arrow(img, (x + 70, ax.y(pat[0][2])), (x + 70, ax.y(pat[-1][1]) - 10), GREEN, 10)
                label(img, lx, ly, "BUYERS: OPEN SE CLOSE TAK", GREEN, 38, bg=(6, 10, 20))
            elif key == "three_black_crows":
                arrow(img, (x + 70, ax.y(pat[0][1])), (x + 70, ax.y(pat[-1][2]) + 10), RED, 10)
                label(img, lx, ly, "SELLERS: LAGATAAR", RED, 40, bg=(6, 10, 20))
            elif direction == "bear":
                arrow(img, (x - 150, ax.y(c[0]) + 40), (x - 40, ax.y(c[1]) + 20), GREEN, 10)
                arrow(img, (x + 40, ax.y(c[1]) + 20), (x + 150, ax.y(min(c[0], c[3])) + 60), RED, 10)
                label(img, lx, ly, "BUYERS UPAR LE GAYE", GREEN, 38, bg=(6, 10, 20))
                label(img, lx, ly + 62, "SELLERS NE GIRAYA", RED, 38, bg=(6, 10, 20))
            elif direction == "bull":
                arrow(img, (x - 150, ax.y(c[0]) - 40), (x - 40, ax.y(c[2]) - 20), RED, 10)
                arrow(img, (x + 40, ax.y(c[2]) - 20), (x + 150, ax.y(max(c[0], c[3])) - 60), GREEN, 10)
                label(img, lx, ly, "SELLERS NEECHE LE GAYE", RED, 38, bg=(6, 10, 20))
                label(img, lx, ly + 62, "BUYERS NE UTHAYA", GREEN, 38, bg=(6, 10, 20))
            else:
                label(img, lx, ly, "BUYERS = SELLERS", YELLOW, 42, bg=(6, 10, 20))
            ring_pattern(img, ax)
            return A, img
        # karein
        ax = base_full(img, with_conf=bool(conf))
        A = img.copy()
        ring_pattern(img, ax, (148, 163, 184))
        if conf:
            cc = conf[0]
            ring(img, ax.x(i1 + 1), (ax.y(cc[1]) + ax.y(cc[2])) / 2, 55, (ax.y(cc[2]) - ax.y(cc[1])) / 2 + 40,
                 GREEN if (key != "confirmation") else RED)
        x0, x1 = X0 + 30, X1 - 30
        if key == "confirmation":
            cross(img, ax.x(i1 + 1) + 70, ax.y(conf[0][2]) - 20, 36)
            label(img, X0 + 60, Y0 + 50, "NO CONFIRM = NO TRADE", RED, 40)
            dashed(img, x0, x1, ax.y(min(c[2] for c in pat)) + 6, SL_RED, "SL", label_side="left")
        elif direction == "bull":
            dashed(img, x0, x1, ax.y(min(c[2] for c in pat)) + 8, SL_RED, "SL", label_side="left")
            if conf:
                dashed(img, x0, x1, ax.y(conf[0][3]), TARGET_GREEN, "ENTRY", label_side="left")
        elif direction == "bear":
            dashed(img, x0, x1, ax.y(max(c[1] for c in pat)) - 8, SL_RED, "SL", label_side="left")
            if conf:
                dashed(img, x0, x1, ax.y(conf[0][3]), GREEN, "ENTRY", label_side="left")
        else:
            label(img, X0 + 60, Y0 + 50, "NEXT CANDLE = DIRECTION", YELLOW, 40)
        return A, img
    return beat




# ================================================================ finance education cards
FINANCE_CARD_LABELS = {
    "fund": ("BUSINESS QUALITY", "REVENUE / PROFIT / CASH", "MARGIN / ROCE / FCF", "CHECK ANNUAL REPORT"),
    "opt": ("OPTION SETUP", "PREMIUM / IV / GREEKS", "PAYOFF / RISK", "CHECK EXPIRY PAYOFF"),
    "intraday": ("INTRADAY", "PRICE / VOLUME / VWAP", "CONTEXT + CONFIRM", "RISK BEFORE ENTRY"),
    "stock": ("STOCK CASE", "BUSINESS / RESULTS", "VALUATION / RISK", "CHECK FILINGS"),
    "penny": ("PENNY RISK", "PRICE IS NOT VALUE", "LIQUIDITY / DILUTION", "CHECK DISCLOSURES"),
    "risk": ("RISK", "CAPITAL / POSITION", "LOSS / RECOVERY", "PROTECT CAPITAL"),
    "business": ("BUSINESS", "REVENUE / UNIT ECONOMICS", "MARGIN / CASH", "READ THE NUMBERS"),
    "market": ("MARKET HISTORY", "INDEX / EVENT", "DRAWDOWN / RECOVERY", "CHECK THE DATES"),
}


def _topic_beats(key):
    try:
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        t = next((x for x in bank.get("topics", []) if x.get("key") == key), None)
        if not t:
            return {}
        return {b: list(pair) for b, pair in (t.get("beats") or {}).items()}
    except Exception as e:
        print(f"[finance_card] topic_bank load failed: {e}")
        return {}


def _short_line(text, max_chars=42):
    s = " ".join(str(text or "").split())
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "..."


def _ascii_title(key):
    """Board text is intentionally ASCII-only; Devanagari is rendered by libass.
    This prevents tofu/square glyphs when CI/Pillow lacks a Devanagari font."""
    titles = {
        "option_gamma": "OPTION GAMMA",
        "option_delta": "OPTION DELTA",
        "atm_delta": "ATM DELTA",
        "opt_iv_crush": "IV CRUSH",
        "opt_bull_call_spread": "BULL CALL SPREAD",
        "opt_bear_put_spread": "BEAR PUT SPREAD",
        "opt_protective_put": "PROTECTIVE PUT",
        "opt_straddle": "LONG STRADDLE",
        "opt_theta_decay": "THETA DECAY",
        "fund_eps_quality": "EARNINGS QUALITY",
        "fund_roce": "ROCE",
        "fund_margin": "MARGIN",
        "fund_fcf": "FREE CASH FLOW",
        "fund_pledge": "PROMOTER PLEDGE",
        "fund_working_capital": "WORKING CAPITAL",
        "intraday_orb": "OPENING RANGE",
        "intraday_vwap": "VWAP",
        "intraday_gap": "GAP UP / GAP DOWN",
        "intraday_trend_range": "TREND VS RANGE",
        "intraday_size": "POSITION SIZING",
        "intraday_volume": "VOLUME + PRICE",
        "stock_earnings": "QUARTERLY RESULT",
        "stock_turnaround": "TURNAROUND",
        "stock_valuation_trap": "VALUATION TRAP",
        "penny_dilution": "SHARE DILUTION",
        "penny_liquidity": "LIQUIDITY RISK",
        "penny_history": "PENNY STOCK HISTORY",
        "risk_drawdown": "DRAWDOWN MATH",
        "risk_rr": "RISK / REWARD",
        "risk_leverage": "LEVERAGE",
        "risk_journal": "TRADING JOURNAL",
        "business_unit_economics": "UNIT ECONOMICS",
        "business_cash": "EBITDA VS CASH FLOW",
        "market_crash_math": "CRASH / RECOVERY",
        "market_index_base": "INDEX BASE VALUE",
    }
    return titles.get(key, key.replace("_", " ").upper())


def _topic_kind(key):
    low = key.lower()
    if low.startswith(("opt_", "option_")) or low in {"atm_delta"}:
        return "opt"
    if low.startswith(("fund_", "peg_", "roe_", "debt_", "cashflow_", "operating_", "eps_", "promoter_", "shareholding")) or low in {"fcf", "quality_stock_checklist"}:
        return "fund"
    if low.startswith("intraday_"):
        return "intraday"
    if low.startswith(("stock_", "multibagger_", "smallcap_")):
        return "stock"
    if low.startswith("penny_"):
        return "penny"
    if low.startswith("risk_"):
        return "risk"
    if low.startswith("business_"):
        return "business"
    return "market"


def _draw_finance_visual(img, kind, key, beat):
    """Large, topic-relevant visual. No Hindi is drawn by PIL."""
    box = (X0 + 55, Y0 + 300, X1 - 55, Y1 - 75)
    title = _ascii_title(key)
    if kind == "opt":
        # Delta/Gamma family: sensitivity curve + causal chain.
        xs = list(range(11))
        if "gamma" in key.lower():
            ys = [0.12, 0.16, 0.22, 0.32, 0.48, 0.70, 0.88, 0.70, 0.48, 0.32, 0.22]
            ax = series_panel(img, box, [(ys, YELLOW, 9)], lo=0, hi=1, pad=(70, 60, 70, 80))
            label(img, X0 + 85, Y0 + 325, "GAMMA = DELTA SENSITIVITY", INK, 34)
            label(img, X0 + 85, Y1 - 115, "SPOT MOVE", MUTED, 30)
            ring(img, ax.x(5), ax.y(ys[5]), 42, 42, GREEN)
        elif "delta" in key.lower() or key == "atm_delta":
            ys = [0.18, 0.25, 0.34, 0.43, 0.50, 0.57, 0.66, 0.74, 0.82, 0.88, 0.93]
            ax = series_panel(img, box, [(ys, GREEN, 9)], lo=0, hi=1, pad=(70, 60, 70, 80))
            label(img, X0 + 85, Y0 + 325, "DELTA = PREMIUM SENSITIVITY", INK, 34)
            ring(img, ax.x(5), ax.y(ys[5]), 42, 42, YELLOW)
        else:
            payoff(img, box, list(range(11)), [-4,-3,-2,-1,0,1,2,3,4,5,6], strike=5)
            label(img, X0 + 85, Y0 + 325, "PAYOFF / RISK MAP", INK, 34)
        if beat == "matlab":
            arrow(img, (X0 + 170, Y0 + 700), (X1 - 170, Y0 + 700), GREEN, 12)
            label(img, X0 + 85, Y0 + 640, "SPOT MOVE  ->  DELTA CHANGE  ->  PREMIUM IMPACT", GREEN, 30)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, "CHECK EXPIRY / IV / DELTA / MAX RISK", YELLOW, 30)
        return
    if kind in {"fund", "stock", "business"}:
        if kind == "business":
            from PIL import ImageDraw
            steps = [("CAC", 180), ("CUSTOMER", 420), ("LTV", 660)]
            for i, (t, x) in enumerate(steps):
                d = ImageDraw.Draw(img)
                d.rounded_rectangle([x-105, Y0+450, x+105, Y0+570], 22, fill=(18,28,45), outline=GREEN if i==2 else MUTED, width=4)
                label(img, x, Y0+492, t, INK, 34, anchor="ma")
                if i < 2:
                    arrow(img, (x+115, Y0+510), (steps[i+1][1]-115, Y0+510), GREEN, 8)
            label(img, X0 + 85, Y0 + 350, "UNIT ECONOMICS", YELLOW, 42)
        else:
            vals = [100, 72, 48] if kind == "fund" else [62, 82, 105]
            labels = ["REVENUE", "PROFIT", "CASH"] if kind == "fund" else ["SALES", "PROFIT", "VALUE"]
            bars(img, box, vals, labels, [BLUE, GREEN, YELLOW], fmt="{:g}", hi=120, value_size=42, label_size=30)
            if beat == "hook":
            label(img, X0 + 85, Y0 + 325, "COMPARE THE NUMBERS", INK, 34)
        elif beat == "pehchan":
            ring(img, X0 + 210, Y0 + 565, 90, 70, YELLOW)
            label(img, X0 + 85, Y0 + 325, "READ EACH BAR", INK, 34)
        elif beat == "matlab":
            arrow(img, (X0 + 270, Y0 + 650), (X1 - 240, Y0 + 650), GREEN, 10)
            label(img, X0 + 85, Y0 + 650, "RESULT -> CASH -> VALUE", GREEN, 32)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, "CHECK QUALITY / CASH / VALUATION", YELLOW, 32)
        return
    if kind == "intraday":
        closes = [100, 101, 100.5, 102, 101.4, 103, 102.2, 104, 103.2, 105]
        oh = ohlc_from_closes(closes, seed=7)
        ax = candles(img, box, oh)
        vw = [100.2,100.5,100.7,101.1,101.3,101.8,102.0,102.5,102.7,103.0]
        polyline(img, [(ax.x(i), ax.y(vw[i])) for i in range(len(vw))], YELLOW, 8)
        if beat == "hook":
            label(img, X0 + 85, Y0 + 325, "PRICE + VOLUME + CONTEXT", INK, 34)
        elif beat == "pehchan":
            ring(img, ax.x(3), ax.y(closes[3]), 55, 55, YELLOW)
            label(img, X0 + 85, Y0 + 325, "FOLLOW THE PRICE LINE", INK, 30)
        elif beat == "matlab":
            arrow(img, (ax.x(2), ax.y(vw[2])-50), (ax.x(7), ax.y(vw[7])-50), GREEN, 10)
            label(img, X0 + 85, Y0 + 650, "CONTEXT CHANGES THE READING", GREEN, 30)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, "CONTEXT FIRST / RISK BEFORE ENTRY", YELLOW, 32)
        return
    if kind == "risk":
        vals = [100, 80, 100]
        bars(img, box, vals, ["START", "DRAWDOWN", "RECOVERY"], [GREEN, RED, YELLOW], fmt="{:g}", hi=115, value_size=40, label_size=28)
        if beat == "hook":
            label(img, X0 + 85, Y0 + 325, "LOSS % != RECOVERY %", INK, 36)
        elif beat == "pehchan":
            ring(img, X0 + 410, Y0 + 560, 100, 75, RED)
            label(img, X0 + 85, Y0 + 325, "DRAWDOWN BAR", INK, 34)
        elif beat == "matlab":
            arrow(img, (X0 + 250, Y0 + 700), (X1 - 250, Y0 + 700), GREEN, 10)
            label(img, X0 + 85, Y0 + 650, "RECOVERY NEEDS MORE", GREEN, 30)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, "PROTECT CAPITAL / SIZE THE RISK", YELLOW, 32)
        return
    if kind == "penny":
        vals = [100, 35, 12]
        bars(img, box, vals, ["PRICE", "VOLUME", "LIQUIDITY"], [YELLOW, BLUE, RED], fmt="{:g}", hi=110, value_size=40, label_size=28)
        # Keep 100 / 35 / 12 unobstructed and progress the explanation visually.
        if beat == "hook":
            ring(img, X0 + 210, Y0 + 560, 90, 70, YELLOW)
        elif beat == "pehchan":
            ring(img, X0 + 470, Y0 + 560, 85, 65, BLUE)
            label(img, X0 + 85, Y0 + 325, "PRICE != VOLUME", INK, 34)
        elif beat == "matlab":
            arrow(img, (X0 + 560, Y0 + 620), (X1 - 150, Y0 + 620), RED, 10)
            label(img, X0 + 85, Y0 + 650, "LOW LIQUIDITY = HARDER EXIT", RED, 28)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, "CHECK VOLUME / SPREAD / DILUTION", YELLOW, 32)
        return
    # Market/history fallback: always show a real-looking index curve.
    ys = [100, 103, 101, 108, 112, 109, 118, 123, 121, 130, 136]
    ax = series_panel(img, box, [(ys, GREEN, 9)], lo=95, hi=140, pad=(70, 60, 70, 80))
    label(img, X0 + 85, Y0 + 325, "INDEX / EVENT / RECOVERY", INK, 34)
    ring(img, ax.x(7), ax.y(ys[7]), 42, 42, YELLOW)
    if beat == "karein":
        label(img, X0 + 85, Y0 + 650, "CHECK THE DATE / SOURCE / METHOD", YELLOW, 32)


def finance_card_scene(key):
    """Safe generic scene for any topic-bank key.

    Rules: use only ASCII inside PIL (avoids tofu), fill the board with a
    topic-relevant visual, and keep text large enough for a 1080x1920 Short.
    Hindi explanation is rendered later by libass in board_short.py.
    """
    kind = _topic_kind(key)
    title = _ascii_title(key)

    def beat(b, img):
        panel(img)
        label(img, (X0 + X1) / 2, Y0 + 92, title, YELLOW, 54, anchor="ma")
        stage = {"hook":"CORE IDEA", "pehchan":"WHAT IT IS", "matlab":"HOW IT WORKS", "karein":"WHAT TO CHECK"}[b]
        label(img, (X0 + X1) / 2, Y0 + 170, stage, MUTED, 30, anchor="ma")
        _draw_finance_visual(img, kind, key, b)
        # The first picture is the Reel/Short cover frame. It must already
        # contain the educational visual, not an empty pre-draw board.
        A = img.copy()
        return A, img
    return beat


# ================================================================ indicators
def _walk(n, seed, drift=0.0, vol=1.0, start=100.0):
    rng = random.Random(seed)
    v, out = start, []
    for _ in range(n):
        v += drift + rng.gauss(0, vol)
        out.append(v)
    return out


def rsi_levels():
    closes = _walk(40, 3, drift=0.2, vol=0.8) + _walk(22, 5, drift=1.4, vol=0.5, start=0)
    # stitch: second part continues from first
    base = closes[39]
    closes = closes[:40] + [base + x for x in _walk(22, 5, drift=1.3, vol=0.5, start=0)] + \
             [base + 28.6 + x for x in _walk(10, 8, drift=-0.9, vol=0.6, start=0)]
    r = rsi(closes)
    view = slice(18, len(closes))
    c, rv = closes[view], r[view]
    oh = ohlc_from_closes(c, seed=2)
    hot = max(range(len(rv)), key=lambda i: rv[i] or 0)

    def beat(b, img):
        panel(img, TOP_BOX); panel(img, BOT_BOX)
        ax = candles(img, TOP_BOX, oh)
        rx = series_panel(img, BOT_BOX, [(rv, BLUE, 5)], hlines=[(70, RED, "70"), (30, GREEN, "30")], lo=10, hi=95)
        A = img.copy()
        if b == "hook":
            ring(img, rx.x(hot), rx.y(rv[hot]), 50, 50)
            label(img, X0 + 40, BOT_BOX[1] + 20, "RSI 14", MUTED, 30)
        elif b == "pehchan":
            label(img, X0 + 60, rx.y(82), "OVERBOUGHT", RED, 38)
            label(img, X0 + 60, rx.y(22), "OVERSOLD", GREEN, 38)
        elif b == "matlab":
            first = next(i for i, v in enumerate(rv) if v and v > 70)
            arrow(img, (ax.x(first), ax.y(c[first]) + 60), (ax.x(hot), ax.y(c[hot]) - 30), GREEN, 10)
            label(img, X0 + 60, TOP_BOX[1] + 40, "70+ PAR BHI UPAR", GREEN, 38)
        else:
            back = next((i for i in range(hot, len(rv)) if rv[i] and rv[i] < 70), len(rv) - 1)
            ring(img, rx.x(back), rx.y(rv[back]), 45, 45, YELLOW)
            ring(img, ax.x(back), (ax.y(oh[back][1]) + ax.y(oh[back][2])) / 2, 45, 90, RED)
            label(img, X0 + 60, TOP_BOX[1] + 40, "WAIT: 70 KE NEECHE + RED CANDLE", YELLOW, 32)
        return A, img
    return beat


def rsi_divergence():
    closes = [100 - 0.9 * i + 3 * math.sin(i / 2.2) for i in range(24)] + \
             [80 + 1.4 * i for i in range(8)] + [91 - 1.6 * i for i in range(12)] + \
             [72 + 1.8 * i for i in range(10)]
    r = rsi(closes)
    view = slice(14, len(closes))
    c, rv = closes[view], r[view]
    oh = ohlc_from_closes(c, seed=4)
    lows = [i for i in range(2, len(c) - 2) if c[i] == min(c[i - 2:i + 3])]
    l1, l2 = lows[0], lows[-1]

    def beat(b, img):
        panel(img, TOP_BOX); panel(img, BOT_BOX)
        ax = candles(img, TOP_BOX, oh)
        rx = series_panel(img, BOT_BOX, [(rv, BLUE, 5)], hlines=[(30, GREEN, "30")], lo=5, hi=80)
        A = img.copy()
        if b in ("hook", "pehchan", "matlab"):
            polyline(img, [(ax.x(l1), ax.y(oh[l1][2]) + 10), (ax.x(l2), ax.y(oh[l2][2]) + 10)], RED, 7)
            polyline(img, [(rx.x(l1), rx.y(rv[l1])), (rx.x(l2), rx.y(rv[l2]))], GREEN, 7)
            if b != "hook":
                label(img, ax.x(l2) - 260, ax.y(oh[l2][2]) + 20, "LOWER LOW", RED, 34)
                label(img, rx.x(l2) - 260, rx.y(rv[l2]) - 70, "HIGHER LOW", GREEN, 34)
            if b == "matlab":
                label(img, X0 + 60, TOP_BOX[1] + 40, "SELLING KAMZOR", YELLOW, 38)
        else:
            nxt = min(l2 + 3, len(oh) - 1)
            ring(img, ax.x(nxt), (ax.y(oh[nxt][1]) + ax.y(oh[nxt][2])) / 2, 45, 90, GREEN)
            dashed(img, X0 + 30, X1 - 30, ax.y(oh[l2][2]) + 12, SL_RED, "SL")
            label(img, X0 + 60, TOP_BOX[1] + 40, "CONFIRM CANDLE KA WAIT", YELLOW, 34)
        return A, img
    return beat


def ma_cross(golden=True):
    n = 320
    if golden:
        closes = [200 - 0.25 * i + 4 * math.sin(i / 9) for i in range(170)] + \
                 [157.5 + 0.55 * i + 4 * math.sin(i / 9) for i in range(150)]
    else:
        closes = [120 + 0.25 * i + 4 * math.sin(i / 9) for i in range(170)] + \
                 [162.5 - 0.55 * i + 4 * math.sin(i / 9) for i in range(150)]
    s50, s200 = sma(closes, 50), sma(closes, 200)
    view = slice(200, n)
    c, a, b_ = closes[view], s50[view], s200[view]
    cr = next((i for i in range(1, len(a)) if (a[i - 1] - b_[i - 1]) * (a[i] - b_[i]) <= 0), len(a) // 2)

    ly = Y0 + 40 if golden else Y1 - 110      # empty corner

    def beat(b, img):
        panel(img)
        ax = series_panel(img, BOARD, [(c, (100, 116, 139), 3), (a, YELLOW, 7), (b_, BLUE, 7)])
        A = img.copy()
        if b == "hook":
            ring(img, ax.x(cr), ax.y(a[cr]), 60, 60)
        elif b == "pehchan":
            label(img, X0 + 60, ly, "50 DMA", YELLOW, 40)
            label(img, X0 + 260, ly, "200 DMA", BLUE, 40)
            ring(img, ax.x(cr), ax.y(a[cr]), 55, 55)
        elif b == "matlab":
            end = len(c) - 1
            col = GREEN if golden else RED
            arrow(img, (ax.x(cr) + 40, ax.y(a[cr])), (ax.x(end) - 20, ax.y(a[end])), col, 10)
            label(img, X0 + 60, ly, "LAMBA TREND" + (" UP" if golden else " DOWN"), col, 40)
        else:
            label(img, X0 + 60, ly, "LATE SIGNAL - SL ZAROORI", YELLOW, 36)
            ring(img, ax.x(cr), ax.y(a[cr]), 55, 55, (148, 163, 184))
        return A, img
    return beat


def macd_scene():
    closes = [100 - 0.4 * i + 2 * math.sin(i / 3) for i in range(40)] + \
             [84 + 0.9 * i + 2 * math.sin(i / 3) for i in range(40)]
    e12, e26 = ema(closes, 12), ema(closes, 26)
    m = [x - y for x, y in zip(e12, e26)]
    sig = ema(m, 9)
    view = slice(26, len(closes))
    c, mm, ss = closes[view], m[view], sig[view]
    hist = [x - y for x, y in zip(mm, ss)]
    oh = ohlc_from_closes(c, seed=7)
    cr = next((i for i in range(1, len(mm)) if mm[i - 1] < ss[i - 1] and mm[i] >= ss[i]), len(mm) // 2)

    def beat(b, img):
        panel(img, TOP_BOX); panel(img, BOT_BOX)
        ax = candles(img, TOP_BOX, oh)
        lo, hi = min(min(mm), min(ss), min(hist)), max(max(mm), max(ss), max(hist))
        rx = Axes(BOT_BOX, len(mm), lo, hi, pad=(40, 30, 40, 30))
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        for i, h in enumerate(hist):
            d.rectangle([rx.x(i) - 5, min(rx.y(h), rx.y(0)), rx.x(i) + 5, max(rx.y(h), rx.y(0))],
                        fill=GREEN if h >= 0 else RED)
        polyline(img, [(rx.x(i), rx.y(v)) for i, v in enumerate(mm)], BLUE, 5)
        polyline(img, [(rx.x(i), rx.y(v)) for i, v in enumerate(ss)], YELLOW, 5)
        A = img.copy()
        if b == "hook":
            ring(img, rx.x(cr), rx.y(mm[cr]), 45, 45)
        elif b == "pehchan":
            label(img, X0 + 50, BOT_BOX[1] + 14, "MACD 12-26", BLUE, 30)
            label(img, X0 + 330, BOT_BOX[1] + 14, "SIGNAL 9", YELLOW, 30)
            label(img, X0 + 560, BOT_BOX[1] + 14, "HISTOGRAM", GREEN, 30)
        elif b == "matlab":
            ring(img, rx.x(cr), rx.y(mm[cr]), 45, 45)
            arrow(img, (ax.x(cr), ax.y(c[cr]) + 80), (ax.x(len(c) - 1), ax.y(c[-1]) + 40), GREEN, 10)
        else:
            label(img, X0 + 60, TOP_BOX[1] + 40, "PRICE + VOLUME SE CONFIRM", YELLOW, 34)
            ring(img, rx.x(cr), rx.y(mm[cr]), 45, 45, (148, 163, 184))
        return A, img
    return beat


def vwap_scene():
    rng = random.Random(21)
    closes, vols, p = [], [], 100.0
    for i in range(36):
        p += (0.35 if i > 12 else -0.1) + rng.gauss(0, 0.45)
        closes.append(p)
        vols.append(rng.uniform(0.6, 1.6) * (1.8 if 12 < i < 18 else 1))
    oh = ohlc_from_closes(closes, seed=9)
    cum_pv, cum_v, vw = 0, 0, []
    for (o, h, l, c), v in zip(oh, vols):
        cum_pv += (h + l + c) / 3 * v; cum_v += v
        vw.append(cum_pv / cum_v)
    cross_i = next((i for i in range(1, len(closes)) if closes[i - 1] < vw[i - 1] and closes[i] >= vw[i]), 14)

    def beat(b, img):
        panel(img)
        ax = candles(img, BOARD, oh)
        polyline(img, [(ax.x(i), ax.y(v)) for i, v in enumerate(vw)], YELLOW, 7)
        A = img.copy()
        if b == "hook":
            label(img, X0 + 60, Y0 + 40, "VWAP", YELLOW, 44)
        elif b == "pehchan":
            label(img, X0 + 60, Y0 + 40, "VOLUME-WEIGHTED AVG PRICE", YELLOW, 32)
        elif b == "matlab":
            label(img, X0 + 60, Y0 + 40, "ABOVE = BUYERS STRONG", GREEN, 36)
            ring(img, ax.x(cross_i), ax.y(vw[cross_i]), 50, 50)
        else:
            dashed(img, X0 + 30, X1 - 30, ax.y(vw[-1]) + 6, SL_RED, "VWAP = SL ZONE", label_side="left")
        return A, img
    return beat


def volume_breakout():
    rng = random.Random(33)
    res = 110.0
    closes = [104 + 4.5 * math.sin(i / 1.7) + rng.gauss(0, 0.5) for i in range(18)]
    closes = [min(x, res - 0.8) for x in closes] + [111.6, 113.4, 115.1]
    vols = [rng.uniform(0.7, 1.2) for _ in range(18)] + [3.0, 2.4, 2.1]
    fake_vols = vols[:18] + [0.8, 0.7, 0.9]
    fake_closes = closes[:18] + [110.9, 108.2, 105.9]

    def draw(img, cl, vo):
        panel(img, TOP_BOX); panel(img, BOT_BOX)
        oh = ohlc_from_closes(cl, seed=5)
        ax = candles(img, TOP_BOX, oh, 97, 118)
        vx = Axes(BOT_BOX, len(vo), 0, max(vols), pad=(40, 30, 40, 30), ypad=0.02)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        for i, v in enumerate(vo):
            d.rectangle([vx.x(i) - 12, vx.y(v), vx.x(i) + 12, vx.y(0)], fill=GREEN if cl[i] >= (cl[i - 1] if i else cl[0]) else RED)
        dashed(img, X0 + 30, X1 - 30, ax.y(res), YELLOW, "RESISTANCE", size=26, label_side="left")
        return ax, vx

    def beat(b, img):
        if b == "karein":
            ax, vx = draw(img, fake_closes, fake_vols)
            A = img.copy()
            ring(img, vx.x(18), vx.y(0.8) - 20, 60, 60, RED)
            cross(img, ax.x(20), ax.y(fake_closes[20]) - 80, 34)
            label(img, X0 + 60, TOP_BOX[1] + 36, "LOW VOLUME = FAKE", RED, 36)
            return A, img
        ax, vx = draw(img, closes, vols)
        A = img.copy()
        if b in ("hook", "matlab"):
            ring(img, ax.x(18), ax.y(closes[18]), 55, 80, GREEN)
        if b in ("pehchan", "matlab"):
            ring(img, vx.x(18), vx.y(vols[18]) + 30, 55, 60, YELLOW)
            label(img, X0 + 60, TOP_BOX[1] + 36, "BREAKOUT + HIGH VOLUME", GREEN, 34)
        return A, img
    return beat


def atr_sl():
    closes = _walk(30, 17, drift=0.35, vol=1.0)
    oh = ohlc_from_closes(closes, wick=0.6, seed=17)
    entry = oh[-1][3]
    atr = sum(h - l for _, h, l, _ in oh[-14:]) / 14

    def beat(b, img):
        panel(img)
        ax = candles(img, BOARD, oh, entry - 3 * atr, max(c[1] for c in oh))
        A = img.copy()
        i = len(oh) - 5
        if b == "hook":
            ring(img, ax.x(i), (ax.y(oh[i][1]) + ax.y(oh[i][2])) / 2, 45, 90)
        elif b == "pehchan":
            h, l = oh[i][1], oh[i][2]
            arrow(img, (ax.x(i) + 60, ax.y(l)), (ax.x(i) + 60, ax.y(h)), YELLOW, 6, 22)
            label(img, ax.x(i) - 30, (ax.y(h) + ax.y(l)) / 2 - 20, "DAILY RANGE", YELLOW, 32, anchor="ra")
            label(img, X0 + 60, Y0 + 40, f"ATR(14) = AVG RANGE", INK, 36)
        elif b == "matlab":
            label(img, X0 + 60, Y0 + 40, "ZYADA ATR = ZYADA VOLATILITY", YELLOW, 32)
        else:
            dashed(img, X0 + 30, X1 - 30, ax.y(entry), TARGET_GREEN, "ENTRY", label_side="left")
            dashed(img, X0 + 30, X1 - 30, ax.y(entry - 2 * atr), SL_RED, "SL = ENTRY - 2 ATR", size=26, label_side="left")
        return A, img
    return beat


# ================================================================ orders
def limit_vs_market():
    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        rows = [("ASK 101.20", 50), ("ASK 100.80", 120), ("ASK 100.40", 40), ("ASK 100.20", 10),
                ("BID 100.00", 300), ("BID 99.80", 500)]
        y = Y0 + 40
        for i, (t, q) in enumerate(rows):
            col = RED if t.startswith("ASK") else GREEN
            d.rounded_rectangle([X0 + 50, y, X0 + 320, y + 70], 12, fill=col)
            label(img, X0 + 70, y + 16, t, (10, 10, 10), 34)
            d.rounded_rectangle([X0 + 340, y + 16, X0 + 340 + q * 0.55 + 20, y + 54], 10, fill=col + (0,) if False else (51, 65, 85))
            label(img, X1 - 50, y + 16, f"QTY {q}", MUTED, 32, anchor="ra")
            y += 88
        A = img.copy()
        if b == "hook":
            ring(img, X0 + 185, Y0 + 40 + 88 * 3 + 35, 170, 55)
        elif b == "pehchan":
            dashed(img, X0 + 30, X1 - 30, Y0 + 40 + 88 * 4 + 35, YELLOW, "LIMIT BUY 100.00")
        elif b == "matlab":
            label(img, X0 + 60, Y1 - 90, "MARKET BUY 200 -> AVG 100.60+", RED, 34)
            for k in (3, 2, 1):
                arrow(img, (X0 + 330, Y0 + 40 + 88 * k + 35), (X0 + 330, Y0 + 40 + 88 * (k - 1) + 35), YELLOW, 6, 20)
        else:
            label(img, X0 + 60, Y1 - 90, "KAM LIQUIDITY = LIMIT ORDER", GREEN, 34)
        return A, img
    return beat


def sl_vs_slm():
    before = _trend(8, 110, -0.8, 41)
    p = before[-1][3]
    gap = (p - 9, p - 8.2, p - 12, p - 11.4)
    seq = before + [gap]
    sl = p - 3

    def beat(b, img):
        panel(img)
        ax = candles(img, BOARD, seq)
        dashed(img, X0 + 30, X1 - 30, ax.y(sl), YELLOW, "SL TRIGGER")
        A = img.copy()
        gx = ax.x(len(seq) - 1)
        if b == "hook":
            ring(img, gx, (ax.y(gap[1]) + ax.y(gap[2])) / 2, 55, 100, RED)
            label(img, X0 + 60, Y1 - 150, "GAP DOWN", RED, 44)
        elif b == "pehchan":
            label(img, X0 + 60, Y1 - 190, "SL-L = LIMIT PRICE", INK, 36)
            label(img, X0 + 60, Y1 - 130, "SL-M = MARKET", INK, 36)
        elif b == "matlab":
            cross(img, gx - 110, ax.y(sl) + 70, 30)
            label(img, X0 + 60, Y1 - 190, "SL-L: FILL NAHI", RED, 40)
            tick(img, gx + 70, ax.y(gap[3]) + 60, 30)
            label(img, X0 + 60, Y1 - 130, "SL-M: FILL (NEECHE)", GREEN, 40)
        else:
            label(img, X0 + 60, Y1 - 150, "SL-L: LIMIT THODA DOOR RAKHO", YELLOW, 32)
        return A, img
    return beat


def product_cards():
    cards = [("MIS", "INTRADAY", "SAME DAY EXIT"), ("CNC", "DELIVERY", "DEMAT ME"), ("NRML", "F&O", "CARRY FORWARD")]

    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        y = Y0 + 50
        boxes = []
        for name, t1, t2 in cards:
            d.rounded_rectangle([X0 + 40, y, X1 - 40, y + 190], 22, outline=(60, 80, 110), width=4, fill=(15, 23, 42))
            label(img, X0 + 80, y + 40, name, YELLOW, 70)
            label(img, X0 + 380, y + 40, t1, INK, 42)
            label(img, X0 + 380, y + 110, t2, MUTED, 34)
            boxes.append((y, y + 190))
            y += 215
        A = img.copy()
        k = {"hook": None, "pehchan": 0, "matlab": 1, "karein": 2}[b]
        if k is not None:
            y0, y1 = boxes[k]
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([X0 + 34, y0 - 6, X1 - 34, y1 + 6], 26, outline=GREEN, width=8)
        return A, img
    return beat


def gtt_amo():
    def beat(b, img):
        panel(img)
        if b in ("hook", "pehchan", "matlab"):
            timeline(img, (X0, Y0 + 40, X1, Y0 + 330),
                     [("9:15", "OPEN"), ("3:30", "CLOSE"), ("AMO", "AFTER CLOSE"), ("9:15", "NEXT DAY")],
                     active={"hook": None, "pehchan": [2, 3], "matlab": [2, 3]}.get(b))
        closes = _walk(26, 29, drift=-0.25, vol=0.8)
        oh = ohlc_from_closes(closes, seed=29)
        ax = candles(img, (X0, Y0 + 360, X1, Y1), oh)
        A = img.copy()
        if b in ("karein", "matlab"):
            tgt = min(c[2] for c in oh) + 0.3
            dashed(img, X0 + 30, X1 - 30, ax.y(tgt), GREEN, "GTT BUY TRIGGER", label_side="left")
            hit = min(range(len(oh)), key=lambda i: oh[i][2])
            ring(img, ax.x(hit), ax.y(oh[hit][2]), 45, 45, GREEN)
        return A, img
    return beat


def t_plus_1():
    def beat(b, img):
        panel(img)
        timeline(img, (X0, Y0 + 120, X1, Y1 - 200), [("DAY T", "BUY"), ("T+1", "DEMAT ME")],
                 active={"hook": None, "pehchan": [0], "matlab": [1], "karein": [0, 1]}[b])
        A = img.copy()
        if b == "karein":
            label(img, X0 + 60, Y1 - 150, "SELL BEFORE T+1 = BTST RISK", YELLOW, 34)
        elif b == "matlab":
            label(img, X0 + 60, Y1 - 150, "INDIA: T+1 SINCE JAN 2023", INK, 34)
        return A, img
    return beat


# ================================================================ options
def _xs(lo=80, hi=120, n=41):
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def long_call_theta():
    xs = _xs()
    K, prem = 100, 4
    ys = [max(x - K, 0) - prem for x in xs]

    def beat(b, img):
        if b in ("hook", "pehchan", "matlab"):
            panel(img)
            ax = payoff(img, BOARD, xs, ys, strike=xs.index(K))
            A = img.copy()
            if b == "hook":
                ring(img, ax.x(5), ax.y(-prem), 120, 50, RED)
                label(img, X0 + 60, Y0 + 40, "MAX LOSS = PREMIUM", RED, 40)
            elif b == "pehchan":
                label(img, X0 + 60, Y0 + 40, "BUY CALL  STRIKE 100  PREMIUM 4", INK, 32)
            else:
                arrow(img, (ax.x(28), ax.y(0) - 20), (ax.x(40), ax.y(ys[-1]) + 20), GREEN, 10)
                label(img, X0 + 60, Y0 + 40, "UPAR = PROFIT", GREEN, 40)
            return A, img
        panel(img)
        days = list(range(30, -1, -1))
        prem_t = [4 * math.sqrt(d / 30) for d in days]
        ax = series_panel(img, BOARD, [(prem_t, RED, 8)], lo=0, hi=4.4, pad=(60, 60, 60, 80))
        A = img.copy()
        label(img, X0 + 60, Y0 + 40, "THETA: PREMIUM ROZ GHATTA", RED, 36)
        label(img, X0 + 60, Y1 - 70, "30 DAYS", MUTED, 30)
        label(img, X1 - 60, Y1 - 70, "EXPIRY", MUTED, 30, anchor="ra")
        return A, img
    return beat


def naked_call():
    xs = _xs()
    K, prem = 100, 4
    ys = [prem - max(x - K, 0) for x in xs]

    def beat(b, img):
        panel(img)
        ax = payoff(img, BOARD, xs, ys, strike=xs.index(K))
        A = img.copy()
        if b == "hook":
            arrow(img, (ax.x(30), ax.y(ys[30]) + 20), (ax.x(40), ax.y(ys[40]) + 60), RED, 12)
            label(img, X0 + 60, Y1 - 130, "LOSS: NO LIMIT", RED, 44)
        elif b == "pehchan":
            label(img, X0 + 60, Y1 - 130, "SELL CALL  STRIKE 100  PREMIUM 4", INK, 32)
            ring(img, ax.x(8), ax.y(prem), 140, 45, GREEN)
        elif b == "matlab":
            label(img, X0 + 60, Y1 - 130, "PROFIT MAX 4, LOSS OPEN", YELLOW, 38)
        else:
            label(img, X0 + 60, Y1 - 130, "HEDGE / SL / MARGIN", YELLOW, 40)
        return A, img
    return beat


def atm_delta():
    def beat(b, img):
        panel(img)
        ax = bars(img, (X0, Y0 + 90, X1, Y1), [8, 5, 2], ["ITM", "ATM", "OTM"], [GREEN, YELLOW, BLUE], fmt="+{:g}")
        A = img.copy()
        label(img, X0 + 60, Y0 + 30, "SPOT +10  ->  PREMIUM (APPROX)", INK, 32)
        if b in ("pehchan", "matlab", "hook"):
            ring(img, ax.x(1), ax.y(5) - 40, 110, 90)
        if b == "karein":
            fit_label(img, X0 + 60, Y0 + 80, "DELTA 0.5 = RS 1 PAR ~50 PAISE", X1 - X0 - 120, YELLOW, 36)
        return A, img
    return beat


def open_interest():
    strikes = ["23000", "23200", "23400", "23600", "23800"]
    calls = [2.1, 3.4, 5.2, 8.9, 6.1]
    puts = [7.8, 6.6, 4.9, 2.6, 1.5]

    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        ax = Axes(BOARD, len(strikes), 0, 10, pad=(50, 80, 50, 100), ypad=0.03)
        for i, s in enumerate(strikes):
            x = ax.x(i)
            d.rectangle([x - 42, ax.y(calls[i]), x - 4, ax.y(0)], fill=RED)
            d.rectangle([x + 4, ax.y(puts[i]), x + 42, ax.y(0)], fill=GREEN)
            d.text((x, ax.y(0) + 20), s, font=__import__("board_kit").font(28), fill=MUTED, anchor="ma")
        label(img, X0 + 60, Y0 + 30, "CALL OI", RED, 34)
        label(img, X0 + 280, Y0 + 30, "PUT OI", GREEN, 34)
        label(img, X1 - 50, Y0 + 30, "ILLUSTRATIVE", MUTED, 26, anchor="ra")
        A = img.copy()
        if b in ("matlab", "hook"):
            ring(img, ax.x(3) - 23, ax.y(calls[3]) + 60, 60, 90)
        if b in ("matlab", "pehchan"):
            label(img, X0 + 60, Y0 + 90, "OI = KHULE CONTRACTS", YELLOW, 34)
        if b == "karein":
            label(img, X0 + 60, Y0 + 90, "OI + PRICE SAATH DEKHO", YELLOW, 34)
        return A, img
    return beat


def iv_premium():
    iv = list(range(10, 61, 5))
    prem = [round(0.4 * v, 1) for v in iv]

    def beat(b, img):
        panel(img)
        ax = series_panel(img, BOARD, [(prem, YELLOW, 8)], lo=0, hi=max(prem) * 1.1, pad=(60, 60, 60, 90))
        label(img, X0 + 60, Y1 - 70, "IV 10%", MUTED, 30)
        label(img, X1 - 60, Y1 - 70, "IV 60%", MUTED, 30, anchor="ra")
        A = img.copy()
        if b == "hook":
            ring(img, ax.x(len(prem) - 2), ax.y(prem[-2]), 50, 50)
        elif b == "pehchan":
            label(img, X0 + 60, Y0 + 40, "IV = EXPECTED MOVE", INK, 38)
        elif b == "matlab":
            label(img, X0 + 60, Y0 + 40, "IV UP -> PREMIUM UP", GREEN, 38)
        else:
            label(img, X0 + 60, Y0 + 40, "EVENT KE BAAD IV CRUSH", RED, 38)
        return A, img
    return beat


def sebi_study():
    def beat(b, img):
        panel(img)
        ax = bars(img, (X0, Y0 + 80, X1, Y1), [89, 11], ["LOSS", "PROFIT"], [RED, GREEN], fmt="{:g}%", hi=100)
        A = img.copy()
        label(img, X1 - 50, Y0 + 30, "SEBI STUDY  FY22  F&O", INK, 32, anchor="ra")
        if b != "pehchan":
            ring(img, ax.x(0), ax.y(89) - 40, 130, 80, RED)
        if b == "karein":
            fit_label(img, X0 + 440, Y0 + 250, "PEHLE RISK,", X1 - X0 - 470, YELLOW, 44)
            fit_label(img, X0 + 440, Y0 + 310, "PHIR TRADE", X1 - X0 - 470, YELLOW, 44)
        return A, img
    return beat


# ================================================================ risk
def one_percent():
    closes = [498.5 + 1.6 * math.sin(i / 1.6) + 0.12 * i for i in range(14)]
    oh = ohlc_from_closes(closes, seed=51)

    def beat(b, img):
        panel(img)
        ax = candles(img, (X0, Y0, X1, Y1 - 240), oh, 487, 504)
        dashed(img, X0 + 30, X1 - 30, ax.y(500), TARGET_GREEN, "ENTRY 500", label_side="left")
        dashed(img, X0 + 30, X1 - 30, ax.y(490), SL_RED, "SL 490", label_side="left")
        A = img.copy()
        lines = {"hook": [("1%", 120, YELLOW)],
                 "pehchan": [("CAPITAL 1,00,000", 44, INK), ("RISK 1% = 1,000", 52, YELLOW)],
                 "matlab": [("500 - 490 = 10 / SHARE", 50, INK)],
                 "karein": [("1,000 / 10 = 100 SHARES", 56, GREEN)]}[b]
        big_text(img, (X0, Y1 - 240, X1, Y1), lines)
        return A, img
    return beat


def rr_breakeven():
    def beat(b, img):
        panel(img)
        ax = bars(img, (X0, Y0, X1, Y1 - 200), [1, 2], ["RISK", "REWARD"], [RED, GREEN], fmt="{:g}")
        A = img.copy()
        lines = {"hook": [("1 : 2", 110, YELLOW)],
                 "pehchan": [("RISK 1 : REWARD 2", 52, INK)],
                 "matlab": [("1 / (1 + 2) = 33%", 62, YELLOW)],
                 "karein": [("33% SE ZYADA JEETO = PROFIT", 42, GREEN)]}[b]
        big_text(img, (X0, Y1 - 200, X1, Y1), lines)
        return A, img
    return beat


def loss_recovery():
    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        vals = [("10%", 10, 11), ("20%", 20, 25), ("50%", 50, 100)]
        ax = Axes((X0, Y0, X1, Y1), 3, 0, 100, pad=(50, 90, 50, 100), ypad=0.03)
        d = ImageDraw.Draw(img)
        import board_kit as bk
        for i, (t, lo_, need) in enumerate(vals):
            x = ax.x(i)
            d.rectangle([x - 70, ax.y(lo_), x - 8, ax.y(0)], fill=RED)
            d.rectangle([x + 8, ax.y(need), x + 70, ax.y(0)], fill=GREEN)
            d.text((x - 39, ax.y(lo_) - 10), f"-{lo_}", font=bk.font(34), fill=INK, anchor="md")
            d.text((x + 39, ax.y(need) - 10), f"+{need}", font=bk.font(34), fill=INK, anchor="md")
        label(img, X0 + 60, Y0 + 30, "LOSS", RED, 36)
        label(img, X0 + 220, Y0 + 30, "GAIN NEEDED", GREEN, 36)
        A = img.copy()
        if b in ("hook", "matlab", "karein"):
            ring(img, ax.x(2) + 39, ax.y(100) + 120, 70, 160)
        if b == "karein":
            label(img, X0 + 60, Y0 + 90, "BADA LOSS MAT HONE DO", YELLOW, 36)
        return A, img
    return beat


def leverage():
    def beat(b, img):
        panel(img)
        ax = bars(img, BOARD, [20, 100], ["1x", "5x"], [YELLOW, RED], fmt="-{:g}%", hi=110)
        label(img, X0 + 60, Y0 + 30, "PRICE 20% ULTA", INK, 36)
        A = img.copy()
        if b != "pehchan":
            ring(img, ax.x(1), ax.y(100) - 40, 130, 70, RED)
        if b == "karein":
            label(img, X0 + 60, Y0 + 260, "LEVERAGE KAM,", YELLOW, 40)
            label(img, X0 + 60, Y0 + 316, "SL PAKKA", YELLOW, 40)
        return A, img
    return beat


def revenge():
    eq = [100, 101, 102, 99, 100, 94, 95, 84, 86, 66]

    def beat(b, img):
        panel(img)
        ax = series_panel(img, BOARD, [(eq, YELLOW, 8)], lo=60, hi=106, pad=(60, 60, 60, 60))
        A = img.copy()
        if b in ("hook", "matlab"):
            for i, t in ((3, "LOSS"), (5, "BIGGER BET"), (7, "BIGGER LOSS")):
                ring(img, ax.x(i), ax.y(eq[i]), 36, 36, RED)
                label(img, ax.x(i) - 60, ax.y(eq[i]) + 50, t, RED, 28)
        if b == "pehchan":
            label(img, X0 + 60, Y0 + 40, "LOSS -> GUSSA -> BADA TRADE", RED, 34)
        if b == "karein":
            label(img, X0 + 60, Y0 + 40, "DAILY LOSS LIMIT -> STOP", GREEN, 36)
        return A, img
    return beat


def averaging():
    prices = [100, 96, 92, 88, 84, 80]
    qty = [10, 20, 40, 80, 160, 0]

    def beat(b, img):
        panel(img)
        ax = series_panel(img, BOARD, [(prices, RED, 8)], lo=74, hi=104, pad=(80, 60, 80, 60))
        A = img.copy()
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        if b in ("pehchan", "matlab", "hook"):
            for i in range(5):
                r = 10 + qty[i] ** 0.5 * 2.2
                d.ellipse([ax.x(i) - r, ax.y(prices[i]) - r, ax.x(i) + r, ax.y(prices[i]) + r], fill=GREEN)
            label(img, X0 + 60, Y0 + 40, "BUY 10, 20, 40, 80, 160", INK, 34)
        if b == "matlab":
            avg = sum(p * q for p, q in zip(prices, qty)) / sum(qty)
            dashed(img, X0 + 30, X1 - 30, ax.y(avg), YELLOW, f"AVG {avg:.1f}")
        if b == "karein":
            label(img, X0 + 60, Y0 + 40, "GALAT TRADE ME PAISA MAT BADHAO", YELLOW, 30)
            dashed(img, X0 + 30, X1 - 30, ax.y(96), SL_RED, "SL")
        return A, img
    return beat


def journal():
    rows = [("DATE", "SETUP", "RESULT", "MISTAKE"), ("12/09", "BREAKOUT", "+1.5R", "-"),
            ("13/09", "RSI 70", "-1R", "NO SL"), ("14/09", "HAMMER", "+2R", "-")]

    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        xs = [X0 + 50, X0 + 230, X0 + 470, X0 + 650]
        y = Y0 + 90
        for r, row in enumerate(rows):
            for x, cell in zip(xs, row):
                label(img, x, y, cell, YELLOW if r == 0 else INK, 34 if r else 30)
            d.line([(X0 + 40, y + 70), (X1 - 40, y + 70)], fill=(60, 80, 110), width=2)
            y += 120
        A = img.copy()
        if b in ("matlab", "karein"):
            ring(img, xs[3] + 60, Y0 + 90 + 240 + 20, 100, 55, RED)
        if b == "karein":
            label(img, X0 + 60, Y1 - 90, "HAR HAFTE REVIEW", GREEN, 38)
        return A, img
    return beat


# ================================================================ real data
_DATA = None


def _data():
    global _DATA
    if _DATA is None:
        _DATA = json.loads((ROOT / "data" / "index_monthly.json").read_text(encoding="utf-8"))
    return _DATA


def data_line(series, year, cagr=False, balance=None):
    """Real data, a different picture on every beat (v1 showed one chart for
    40 seconds and said the answer in the hook):
      hook    Rs 1,00,000 ... = ?          (question only)
      pehchan the line draws itself, start/end prices
      matlab  end / start = Nx             (the maths)
      karein  Rs 1 lakh -> result + balance card
    balance = (line1, line2) shown on the last beat (e.g. Yes Bank for winners)."""
    from datetime import date
    from PIL import ImageDraw

    def beat(b, img):
        s = _data()["series"][series]
        start, last = s["first_of_year"][str(year)], s["last"]
        pts = [v for m, v in s["monthly"] if m >= f"{year}-01"]
        mult = last["close"] / start["close"]
        loser = mult < 1
        col = RED if loser else GREEN
        yrs = (date.fromisoformat(last["date"]) - date.fromisoformat(start["date"])).days / 365.25
        panel(img)
        d = ImageDraw.Draw(img, "RGBA")
        label(img, X0 + 50, Y0 + 30, series, INK, 36)
        chart_box = (X0, Y0 + 40, X1, Y1 - 60)

        def chart(frac=1.0, fade=False):
            ax = Axes(chart_box, len(pts), 0, max(pts), pad=(60, 60, 60, 60), ypad=0.06)
            ax.grid(img, rows=4)
            upto = max(2, int(len(pts) * frac))
            polyline(img, [(ax.x(i), ax.y(v)) for i, v in enumerate(pts[:upto])], col, 6)
            if fade:
                d.rectangle(chart_box, fill=(6, 10, 20, 170))
            return ax

        if b == "hook":
            cx, cy = (X0 + X1) / 2 - 70, Y0 + 250
            d.rounded_rectangle([cx - 250, cy - 110, cx + 250, cy + 110], 26, fill=(21, 128, 61), outline=(187, 247, 208), width=6)
            d.ellipse([cx - 60, cy - 60, cx + 60, cy + 60], outline=(187, 247, 208), width=6)
            label(img, cx, cy - 28, "Rs", (187, 247, 208), 50, anchor="ma")
            big_text(img, (X0, Y0 + 400, X1, Y0 + 560), [("Rs 1,00,000", 84, INK)])
            big_text(img, (X0, Y0 + 560, X1, Y1 - 20), [(f"{year}  ->  JUN 2026", 50, MUTED)])
            A = img.copy()
            label(img, X1 - 45, Y0 + 110, "?", YELLOW, 230, anchor="ra")
            return A, img
        if b == "pehchan":
            chart(0.35)
            A = img.copy()
            ax = chart(1.0)
            n = len(pts) - 1
            ring(img, ax.x(0), ax.y(pts[0]), 30, 30, YELLOW)
            ring(img, ax.x(n), ax.y(pts[-1]), 30, 30, YELLOW)
            label(img, ax.x(0) + 20, ax.y(pts[0]) - 110, f"{start['close']:,.0f}  ({year})", YELLOW, 36, bg=(6, 10, 20))
            label(img, ax.x(n) - 20, ax.y(pts[-1]) + 50, f"{last['close']:,.0f}  (JUN 2026)", YELLOW, 36,
                  bg=(6, 10, 20), anchor="ra")
            label(img, X1 - 50, Y1 - 50, "PRICE, DIVIDEND ALAG", MUTED, 24, anchor="ra")
            return A, img
        if b == "matlab":
            chart(1.0, fade=True)
            A = img.copy()
            if cagr:
                c = (mult ** (1 / yrs) - 1) * 100
                big_text(img, (X0, Y0 + 60, X1, Y1 - 60),
                         [(f"{mult:.2f}x  IN  {yrs:.0f} SAAL", 60, INK), (f"~ {c:.1f}% / SAAL", 96, YELLOW)])
            else:
                big_text(img, (X0, Y0 + 60, X1, Y1 - 60),
                         # start 2 decimal tak: "4,231 / 71 = 59.53" galat lagta tha (4231/71 = 59.59)
                         [(f"{last['close']:,.0f}  /  {start['close']:,.2f}" if start["close"] < 1000
                           else f"{last['close']:,.0f}  /  {start['close']:,.0f}", 60, INK),
                          (f"= {mult:.2f}x", 120, RED if loser else YELLOW)])
            return A, img
        # karein / nateeja: result, then (when the voice gets there) the balance picture
        box = (X0, Y0 + 60, X1, Y1 - 60)
        if cagr:
            c = (mult ** (1 / yrs) - 1) * 100
            A = img.copy()
            big_text(A, box, [("NIFTY 50", 60, INK)])
            big_text(img, box, [("NIFTY 50", 60, INK), (f"~ {c:.1f}% / SAAL", 110, GREEN), ("(PRICE)", 40, MUTED)])
            if balance:
                C = K.background()
                K.chips(C, *CHIP_OF_LAST)
                _balance_panel(C, balance, year)
                return A, img, C
            return A, img
        else:
            end_txt = (f"Rs {round(mult * 100000, -3):,.0f}" if loser else f"Rs {mult:.1f} LAKH")
            A = img.copy()
            big_text(A, box, [("Rs 1 LAKH  ->", 64, INK)])
            big_text(img, box, [("Rs 1 LAKH  ->", 64, INK), (end_txt, 120, col), ("(JUN 2026)", 44, MUTED)])
            if not balance:
                return A, img
            C = K.background()
            K.chips(C, *CHIP_OF_LAST)
            _balance_panel(C, balance, year)
            return A, img, C
        A = img.copy()
        if balance:
            C = K.background()
            K.chips(C, *CHIP_OF_LAST)
            _balance_panel(C, balance, year)
            return A, img, C
        return A, img
    beat.cue = _cue_for(balance) if balance else None
    return beat


CHIP_OF_LAST = ("REAL DATA", "3 NATEEJA")
CUES = {"YES": r"Yes Bank|यस बैंक|हर शेयर",
        "EK SHARE": r"एक शेयर|एक ही शेयर|जोखिम|रिस्क|risk",
        "INDEX": r"index|इंडेक्स|कंपनि|कंपनी|बैंक",
        "DIVIDEND": r"dividend|डिविडेंड|लाभांश"}


def _cue_for(balance):
    head = balance[0].upper()
    for k, rx in CUES.items():
        if k in head:
            return rx
    return None


def _balance_panel(img, balance, year):
    """Full-board picture for the balance line, so the last beat isn't one still."""
    from PIL import ImageDraw
    panel(img)
    d = ImageDraw.Draw(img, "RGBA")
    head, val = balance
    if "YES BANK" in head:
        s = _data()["series"]["YES BANK"]
        pts = [v for m, v in s["monthly"] if m >= "2015-01"]
        label(img, X0 + 50, Y0 + 30, "YES BANK", INK, 36)
        ax = Axes((X0, Y0 + 40, X1, Y0 + 400), len(pts), 0, max(pts), pad=(60, 60, 60, 30), ypad=0.06)
        ax.grid(img, rows=3)
        polyline(img, [(ax.x(i), ax.y(v)) for i, v in enumerate(pts)], RED, 6)
        ring(img, ax.x(len(pts) - 1), ax.y(pts[-1]), 30, 30, YELLOW)
        big_text(img, (X0, Y0 + 405, X1, Y1 - 15),
                 [("2015  ->  JUN 2026", 38, MUTED), (val, 110, RED), ("HAR SHARE AISA NAHI", 42, INK)])
        return
    if head.startswith("INDEX"):
        import re as _re
        m = _re.search(r"\d+", val)
        n = int(m.group()) if m else 12
        cols = 10
        rows = -(-n // cols)
        cell, gap = 62, 14
        gw = cols * cell + (cols - 1) * gap
        gx = (X0 + X1) / 2 - gw / 2
        gy = Y0 + 90
        for i in range(n):
            r, c = divmod(i, cols)
            x, y = gx + c * (cell + gap), gy + r * (cell + gap)
            d.rounded_rectangle([x, y, x + cell, y + cell], 10, fill=(21, 128, 61, 235), outline=(187, 247, 208), width=3)
        top = gy + rows * (cell + gap) + 20
        big_text(img, (X0, top, X1, Y1 - 20), [(head, 50, INK), (val, 84, GREEN)])
        return
    colr = RED if "RISK" in val else YELLOW
    d.rounded_rectangle([X0 + 60, Y0 + 170, X1 - 60, Y1 - 170], 30, fill=(20, 12, 16, 235), outline=colr, width=6)
    big_text(img, (X0 + 60, Y0 + 170, X1 - 60, Y1 - 170), [(head, 70, INK), (val, 110, colr)])


def _yes_bank_loss():
    s = _data()["series"]["YES BANK"]
    return round((1 - s["last"]["close"] / s["first_of_year"]["2015"]["close"]) * 100)


def stock_line(series, year):
    """Winners get the Yes Bank reminder, losers the concentration-risk card."""
    def beat(b, img):
        s = _data()["series"][series]
        mult = s["last"]["close"] / s["first_of_year"][str(year)]["close"]
        bal = ("EK SHARE =", "BADA RISK") if mult < 1 else ("BUT: YES BANK (2015)", f"-{_yes_bank_loss()}%")
        return data_line(series, year, balance=bal)(b, img)
    beat.cue = CUES["EK SHARE"] if series == "YES BANK" else CUES["YES"]
    return beat


def compare_lines(a_name, b_name, year):
    """Same year, same Rs 1 lakh, two shares: one multiplies, one sinks."""
    def norm(name):
        s = _data()["series"][name]
        st = s["first_of_year"][str(year)]["close"]
        return [100000 * v / st for m, v in s["monthly"] if m >= f"{year}-01"], s["last"]["close"] / st

    def beat(b, img):
        (pa, ma), (pb, mb) = norm(a_name), norm(b_name)
        panel(img)
        ax = series_panel(img, (X0, Y0, X1, Y1 - 170), [(pa, GREEN, 7), (pb, RED, 7)], lo=0,
                          hi=max(pa + pb), pad=(60, 70, 60, 50))
        A = img.copy()
        label(img, X0 + 60, Y0 + 30, f"Rs 1 LAKH ({year})", INK, 36)
        n = len(pa) - 1
        if b in ("pehchan", "matlab", "karein"):
            label(img, ax.x(n) - 20, ax.y(pa[-1]) + 30, a_name, GREEN, 36, anchor="ra")
            label(img, ax.x(n) - 20, ax.y(pb[-1]) - 70, b_name, RED, 36, anchor="ra")
        if b in ("hook", "matlab"):
            ring(img, ax.x(n), ax.y(pa[-1]), 34, 34, GREEN)
            ring(img, ax.x(n), ax.y(pb[-1]), 34, 34, RED)
        lines = {"hook": [(f"{a_name}  vs  {b_name}", 46, INK)],
                 "pehchan": [("GREEN vs RED", 50, INK)],
                 "matlab": [(f"Rs {ma:.1f} LAKH  vs  Rs {round(mb * 100000, -3):,.0f}", 44, YELLOW)],
                 "karein": [("EK SHARE = BADA RISK", 50, RED)]}[b]
        big_text(img, (X0, Y1 - 170, X1, Y1), lines)
        return A, img
    return beat


# ================================================================ facts
def circuit():
    def beat(b, img):
        panel(img)
        ax = bars(img, BOARD, [10, 15, 20], ["LEVEL 1", "LEVEL 2", "LEVEL 3"], [YELLOW, (249, 115, 22), RED],
                  fmt="{:g}%", hi=24)
        A = img.copy()
        label(img, X0 + 60, Y0 + 30, "INDEX MOVE -> MARKET HALT", INK, 34)
        k = {"hook": None, "pehchan": 0, "matlab": 1, "karein": 2}[b]
        if k is not None:
            ring(img, ax.x(k), ax.y([10, 15, 20][k]) - 40, 90, 70)
        return A, img
    return beat


def bse_timeline():
    def beat(b, img):
        panel(img)
        timeline(img, (X0, Y0 + 120, X1, Y1 - 160), [("1875", "BSE"), ("1986", "SENSEX"), ("1994", "NSE")],
                 active={"hook": [0], "pehchan": [0], "matlab": [1], "karein": [2]}[b])
        A = img.copy()
        if b == "hook":
            label(img, X0 + 60, Y1 - 130, "ASIA KA SABSE PURANA (1 OF)", YELLOW, 32)
        return A, img
    return beat


def nifty_base():
    def beat(b, img):
        s = _data()["series"]["NIFTY 50"]
        pts = [v for _, v in s["monthly"]]
        panel(img)
        ax = series_panel(img, (X0, Y0, X1, Y1 - 150), [([1000] + pts, GREEN, 6)], lo=0, hi=max(pts), pad=(60, 70, 60, 50))
        A = img.copy()
        ring(img, ax.x(0), ax.y(1000), 34, 34)
        label(img, ax.x(0) + 20, ax.y(1000) - 80, "1000 (NOV 1995)", YELLOW, 32)
        if b in ("matlab", "karein"):
            label(img, ax.x(len(pts)) - 10, ax.y(pts[-1]) + 40, f"{pts[-1]:,.0f} (JUN 2026)", YELLOW, 32, anchor="ra")
        big_text(img, (X0, Y1 - 150, X1, Y1), [("BASE 1000", 56, INK)] if b != "karein" else [("50 BADI COMPANIES", 50, INK)])
        return A, img
    return beat


def preopen():
    def beat(b, img):
        panel(img)
        timeline(img, (X0, Y0 + 120, X1, Y1 - 160), [("9:00", "ORDER"), ("9:08", "MATCH"), ("9:15", "OPEN")],
                 active={"hook": None, "pehchan": [0], "matlab": [1], "karein": [2]}[b])
        A = img.copy()
        if b == "matlab":
            label(img, X0 + 60, Y1 - 130, "EQUILIBRIUM PRICE TAY HOTA", YELLOW, 32)
        return A, img
    return beat


def muhurat():
    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        cx, cy = (X0 + X1) / 2, Y0 + 300
        d.ellipse([cx - 150, cy + 20, cx + 150, cy + 110], fill=(194, 65, 12))
        d.polygon([(cx - 40, cy + 30), (cx, cy - 120), (cx + 40, cy + 30)], fill=YELLOW)
        A = img.copy()
        big_text(img, (X0, Y0 + 460, X1, Y1), [("DIWALI", 70, YELLOW), ("1 HOUR SESSION", 44, INK)]
                 if b != "karein" else [("CHHOTA SESSION", 50, INK), ("RISK SAME", 50, RED)])
        return A, img
    return beat


def depository():
    def beat(b, img):
        panel(img)
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        boxes = [("YOU", Y0 + 60), ("BROKER / DP", Y0 + 280), ("NSDL / CDSL", Y0 + 500)]
        for t, y in boxes:
            d.rounded_rectangle([X0 + 150, y, X1 - 150, y + 130], 24, fill=(15, 23, 42), outline=(60, 80, 110), width=4)
            label(img, (X0 + X1) / 2, y + 40, t, INK, 46, anchor="ma")
        for y in (Y0 + 190, Y0 + 410):
            arrow(img, ((X0 + X1) / 2, y + 5), ((X0 + X1) / 2, y + 85), YELLOW, 8, 24)
        A = img.copy()
        k = {"hook": 2, "pehchan": 2, "matlab": 1, "karein": 0}[b]
        t, y = boxes[k]
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([X0 + 142, y - 8, X1 - 142, y + 138], 28, outline=GREEN, width=8)
        return A, img
    return beat



# ================================================================ intraday EMA
def intraday_ema_scene():
    """Dedicated visual for 9 EMA vs 21 EMA; never reuse the ORB board."""
    closes = [
        100.0, 99.7, 100.4, 100.1, 100.9, 101.5, 101.1, 102.0, 102.8,
        102.2, 103.0, 103.7, 103.2, 104.1, 104.8, 104.2, 103.6, 104.0,
        104.9, 105.7, 106.2, 105.5, 106.4, 107.2, 106.8, 107.7, 108.4,
        109.0, 108.6, 109.5
    ]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)

    def beat(b, img):
        panel(img)
        ax = series_panel(
            img, BOARD,
            [(closes, (100, 116, 139), 4), (e9, YELLOW, 8), (e21, BLUE, 8)],
            lo=min(closes) - 1.5, hi=max(closes) + 1.5,
            pad=(70, 80, 70, 110)
        )
        A = img.copy()
        label(img, X0 + 70, Y0 + 45, "PRICE", MUTED, 30)
        label(img, X0 + 230, Y0 + 45, "9 EMA", YELLOW, 34)
        label(img, X0 + 390, Y0 + 45, "21 EMA", BLUE, 34)
        if b == "hook":
            i = len(closes) - 1
            ring(img, ax.x(i), ax.y(e9[i]), 48, 48, YELLOW)
        elif b == "pehchan":
            label(img, X0 + 70, Y0 + 125, "9 EMA = FAST / 21 EMA = SLOW", INK, 34)
            ring(img, ax.x(17), ax.y(e9[17]), 44, 44, YELLOW)
            ring(img, ax.x(17), ax.y(e21[17]), 44, 44, BLUE)
        elif b == "matlab":
            i = len(closes) - 1
            arrow(img, (ax.x(17), ax.y(e9[17]) + 70), (ax.x(i), ax.y(e9[i]) - 30), GREEN, 10)
            label(img, X0 + 70, Y0 + 125, "9 ABOVE 21 = BULLISH CONTEXT", GREEN, 34)
        else:
            i = len(closes) - 1
            ring(img, ax.x(i), ax.y(e9[i]), 48, 48, GREEN)
            label(img, X0 + 70, Y0 + 125, "PULLBACK + PRICE HOLDING 9/21", YELLOW, 32)
        return A, img
    return beat

# ================================================================ registry
SCENES = {
    **{k: (lambda k=k: finance_card_scene(k)) for k in (
        "fund_eps_quality","fund_roce","fund_margin","fund_fcf","fund_pledge","fund_working_capital",
        "opt_iv_crush","opt_bull_call_spread","opt_bear_put_spread","opt_protective_put","opt_straddle","opt_theta_decay",
        "intraday_orb","intraday_vwap","intraday_gap","intraday_trend_range","intraday_size","intraday_volume","intraday_ema",
        "stock_earnings","stock_turnaround","stock_valuation_trap",
        "penny_dilution","penny_liquidity","penny_history",
        "risk_drawdown","risk_rr","risk_leverage","risk_journal",
        "business_unit_economics","business_cash","market_crash_math","market_index_base"
    )},
    **{k: (lambda k=k: candle_scene(k)) for k in (
        "hammer", "shooting_star", "doji", "bullish_engulfing", "bearish_engulfing", "morning_star",
        "evening_star", "hanging_man", "inverted_hammer", "marubozu", "spinning_top",
        "three_white_soldiers", "three_black_crows", "dark_cloud", "piercing", "confirmation")},
    "rsi_levels": rsi_levels, "rsi_divergence": rsi_divergence,
    "golden_cross": lambda: ma_cross(True), "death_cross": lambda: ma_cross(False),
    "macd": macd_scene, "vwap": vwap_scene, "volume_breakout": volume_breakout, "atr_sl": atr_sl,
    "limit_vs_market": limit_vs_market, "sl_vs_slm": sl_vs_slm, "products": product_cards,
    "gtt_amo": gtt_amo, "t_plus_1": t_plus_1,
    "long_call_theta": long_call_theta, "naked_call": naked_call, "atm_delta": atm_delta,
    "open_interest": open_interest, "iv_premium": iv_premium, "sebi_study": sebi_study,
    "one_percent": one_percent, "rr_breakeven": rr_breakeven, "loss_recovery": loss_recovery,
    "leverage": leverage, "revenge": revenge, "averaging": averaging, "journal": journal,
    "nifty_2010": lambda: data_line("NIFTY 50", 2010, balance=("INDEX =", "50 COMPANIES")),
    "nifty_2015": lambda: data_line("NIFTY 50", 2015, balance=("INDEX =", "50 COMPANIES")),
    "sensex_2008": lambda: data_line("SENSEX", 2008, balance=("INDEX =", "30 COMPANIES")),
    "niftybank_2010": lambda: data_line("NIFTY BANK", 2010, balance=("INDEX =", "BANK SHARES")),
    "nifty_cagr": lambda: data_line("NIFTY 50", 2010, cagr=True, balance=("DIVIDEND", "ALAG SE")),
    "hdfcbank_2010": lambda: stock_line("HDFC BANK", 2010), "titan_2010": lambda: stock_line("TITAN", 2010),
    "asianpaints_2010": lambda: stock_line("ASIAN PAINTS", 2010), "infosys_2015": lambda: stock_line("INFOSYS", 2015),
    "sbi_2015": lambda: stock_line("SBI", 2015), "tcs_2020": lambda: stock_line("TCS", 2020),
    "dmart_2020": lambda: stock_line("DMART", 2020), "yesbank_2015": lambda: stock_line("YES BANK", 2015),
    "titan_vs_yes_2015": lambda: compare_lines("TITAN", "YES BANK", 2015),
    "circuit": circuit, "bse_1875": bse_timeline, "nifty_base": nifty_base, "preopen": preopen,
    "muhurat": muhurat, "depository": depository,
}


def _scene_factory(key):
    """Return a dedicated scene when available, otherwise a safe generic card.

    This prevents a newly added topic-bank key from taking down the whole
    scheduled run with KeyError: <topic_key>.
    """
    factory = SCENES.get(key)
    if factory is not None:
        return factory
    return lambda: finance_card_scene(key)


def render_beat(key, beat, img):
    return _scene_factory(key)()(beat, img)


def cue(key):
    """Regex: the spoken phrase at which a beat's third picture should appear."""
    return getattr(_scene_factory(key)(), "cue", None)
