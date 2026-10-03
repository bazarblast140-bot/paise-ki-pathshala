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
import re

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
    """Dedicated four-beat teaching flow for candle patterns.

    Each beat has a different visual purpose:
      hook     = isolate the pattern/question
      pehchan  = anatomy/recognition
      matlab   = buyer/seller meaning
      karein   = confirmation + risk plan

    Do not keep the same full chart on screen for every beat; that makes the
    narration hard to follow and can make the video look like the chart is
    unrelated to what is being explained.
    """
    before, pat, conf, direction, anatomy = _pattern(key)

    def _pattern_only(img):
        panel(img)
        lo = min(c[2] for c in pat)
        hi = max(c[1] for c in pat)
        # Give the pattern the whole board instead of carrying the old trend
        # chart through every beat.
        return candles(img, BOARD, pat, lo, hi, grid=False,
                        width_frac=0.62, pad=(100, 150, 100, 150))

    def _draw_direction(img, ax):
        x = ax.x(len(pat) - 1)
        if direction == "bull":
            arrow(img, (x + 90, ax.y(min(c[2] for c in pat)) - 10),
                  (x + 90, ax.y(max(c[1] for c in pat)) - 30), GREEN, 12)
            label(img, X0 + 60, Y0 + 55, "BUYERS IN CONTROL", GREEN, 42)
        elif direction == "bear":
            arrow(img, (x + 90, ax.y(max(c[1] for c in pat)) + 10),
                  (x + 90, ax.y(min(c[2] for c in pat)) + 30), RED, 12)
            label(img, X0 + 60, Y0 + 55, "SELLERS IN CONTROL", RED, 42)
        else:
            label(img, X0 + 60, Y0 + 55, "INDECISION", YELLOW, 42)

    def _confirmation_candle():
        # Some patterns (e.g. Three White Soldiers) are themselves the signal
        # and have no separate confirmation candle in _pattern(). For the
        # action beat, draw a clearly marked *illustrative* next candle rather
        # than reusing the same chart.
        p = pat[-1][3]
        if direction == "bull":
            return [(p, p + 5.0, p - 0.3, p + 4.4)]
        if direction == "bear":
            return [(p, p + 0.3, p - 5.0, p - 4.4)]
        return [(p, p + 2.0, p - 2.0, p + 0.1)]

    def beat(b, img):
        if b == "hook":
            # Question/recognition: show only the pattern, not the whole trend.
            ax = _pattern_only(img)
            A = img.copy()
            for i in range(len(pat)):
                ring(img, ax.x(i), (ax.y(pat[i][1]) + ax.y(pat[i][2])) / 2,
                     48, max(55, abs(ax.y(pat[i][2]) - ax.y(pat[i][1])) / 2 + 35),
                     YELLOW)
            label(img, X0 + 60, Y0 + 55,
                  f"{len(pat)} CANDLE PATTERN", YELLOW, 42)
            return A, img

        if b == "pehchan":
            # Recognition: enlarged anatomy with three concrete checks.
            panel(img)
            lo = min(c[2] for c in pat)
            hi = max(c[1] for c in pat)
            box = (X0 + 35, Y0 + 60, X0 + 470, Y1 - 70)
            ax = candles(img, box, pat, lo, hi, grid=False,
                         width_frac=0.62, pad=(25, 45, 25, 45))
            A = img.copy()
            x = X0 + 510
            ys = [Y0 + 145, Y0 + 335, Y0 + 525]
            for t, y in zip(anatomy, ys):
                label(img, x - 35, y + 22, "•", YELLOW, 58)
                fit_label(img, x, y, t, X1 - x - 30, INK, 36)
            return A, img

        if b == "matlab":
            # Meaning: remove the old trend and explain the force behind the
            # pattern on the pattern itself.
            ax = _pattern_only(img)
            A = img.copy()
            _draw_direction(img, ax)
            if key in ("three_white_soldiers", "morning_star", "bullish_engulfing",
                       "piercing", "hammer", "inverted_hammer", "marubozu"):
                label(img, X0 + 60, Y1 - 135,
                      "BUYERS PUSH PRICE HIGHER", GREEN, 36, bg=(6, 10, 20))
            elif key in ("three_black_crows", "evening_star", "bearish_engulfing",
                         "dark_cloud", "shooting_star", "hanging_man"):
                label(img, X0 + 60, Y1 - 135,
                      "SELLERS PUSH PRICE LOWER", RED, 36, bg=(6, 10, 20))
            else:
                label(img, X0 + 60, Y1 - 135,
                      "WAIT FOR THE NEXT CANDLE", YELLOW, 36, bg=(6, 10, 20))
            return A, img

        # karein: action/risk beat. Show a separate confirmation candle and
        # explicit SL area instead of another copy of the hook chart.
        panel(img)
        lo = min(c[2] for c in pat)
        hi = max(c[1] for c in pat)
        action = list(pat)
        action_conf = conf[:1] if conf else _confirmation_candle()
        action += action_conf
        ax = candles(img, BOARD, action, min(lo, action_conf[0][2]),
                     max(hi, action_conf[0][1]), grid=False,
                     width_frac=0.58, pad=(80, 130, 80, 130))
        A = img.copy()
        ring(img, ax.x(len(pat) - 1),
             (ax.y(pat[-1][1]) + ax.y(pat[-1][2])) / 2,
             50, 70, (148, 163, 184))

        if direction == "bull":
            dashed(img, X0 + 35, X1 - 35,
                   ax.y(lo) + 10, SL_RED, "SL", label_side="left")
            ring(img, ax.x(len(pat)), 
                 (ax.y(action_conf[0][1]) + ax.y(action_conf[0][2])) / 2,
                 52, 70, GREEN)
            label(img, X0 + 60, Y0 + 55,
                  "WAIT FOR CONFIRMATION", YELLOW, 38)
            label(img, X0 + 60, Y1 - 135,
                  "ENTRY AFTER CONFIRMATION • SL BELOW PATTERN",
                  GREEN, 30, bg=(6, 10, 20))
        elif direction == "bear":
            dashed(img, X0 + 35, X1 - 35,
                   ax.y(hi) - 10, SL_RED, "SL", label_side="left")
            ring(img, ax.x(len(pat)),
                 (ax.y(action_conf[0][1]) + ax.y(action_conf[0][2])) / 2,
                 52, 70, RED)
            label(img, X0 + 60, Y0 + 55,
                  "WAIT FOR CONFIRMATION", YELLOW, 38)
            label(img, X0 + 60, Y1 - 135,
                  "ENTRY AFTER CONFIRMATION • SL ABOVE PATTERN",
                  RED, 30, bg=(6, 10, 20))
        else:
            label(img, X0 + 60, Y0 + 55,
                  "WAIT — NO CLEAR DIRECTION", YELLOW, 36)
            label(img, X0 + 60, Y1 - 135,
                  "CONFIRM FIRST • THEN PLAN RISK",
                  YELLOW, 32, bg=(6, 10, 20))
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
    "pf": ("PERSONAL FINANCE", "INCOME / SAVE / GOAL", "TIME / COST", "CHECK THE PLAN"),
    "tax": ("TAX", "GAIN / EXEMPTION", "RATE / HOLDING", "CHECK THE RULE"),
    "ipo": ("IPO", "ISSUE / ALLOTMENT", "LISTING / RISK", "READ THE DRHP"),
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


# Bar charts for finance-card kinds. Heights are diagram geometry, not facts.
# fund/stock stay on the labels they already shipped with.
_BAR_CHARTS = {
    "fund": ([100, 72, 48], ["REVENUE", "PROFIT", "CASH"], "RESULT -> CASH -> VALUE", "CHECK QUALITY / CASH / VALUATION"),
    "stock": ([62, 82, 105], ["SALES", "PROFIT", "VALUE"], "RESULT -> CASH -> VALUE", "CHECK QUALITY / CASH / VALUATION"),
    "pf": ([100, 45, 80], ["INCOME", "SAVE", "GOAL"], "INCOME -> SAVE -> GOAL", "GOAL / TIME / COST"),
    "tax": ([100, 40, 18], ["GAIN", "EXEMPT", "TAX"], "GAIN -> EXEMPT -> TAX", "HOLDING / EXEMPTION / RATE"),
    "ipo": ([90, 55, 80], ["ISSUE", "ALLOT", "LIST"], "ISSUE -> ALLOT -> LIST", "DRHP / LOT / RISK"),
}


def _topic_kind(key):
    """Map a topic key to a finance-card visual that already has drawings.

    Personal finance, tax and IPO have no candle geometry. They use their own
    bar/checklist layout. Anything else still falls through to the market
    chart so a new prefix cannot raise KeyError the way fund_pe once did.
    """
    low = str(key or "").lower()
    if low.startswith("pf_"):
        return "pf"
    if low.startswith("tax_"):
        return "tax"
    if low.startswith("ipo_"):
        return "ipo"
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


def _kind_rows(table, kind):
    """Visual row for this kind, or the market chart when the kind has none."""
    if kind in table:
        return table[kind]
    return table["market"]


def _draw_finance_visual(img, kind, key, beat):
    """Large, topic-relevant visual. No Hindi is drawn by PIL."""
    box = (X0 + 55, Y0 + 300, X1 - 55, Y1 - 75)
    title = _ascii_title(key)
    if kind == "opt":
        # Option topics must not share one generic payoff chart. Each spread
        # gets its own payoff geometry so adjacent Bull/Bear videos cannot
        # accidentally show the same curve.
        xs = list(range(11))
        low = key.lower()
        if "gamma" in low:
            ys = [0.12, 0.16, 0.22, 0.32, 0.48, 0.70, 0.88, 0.70, 0.48, 0.32, 0.22]
            ax = series_panel(img, box, [(ys, YELLOW, 9)], lo=0, hi=1, pad=(70, 60, 70, 80))
            label(img, X0 + 85, Y0 + 325, "GAMMA = DELTA SENSITIVITY", INK, 34)
            ring(img, ax.x(5), ax.y(ys[5]), 42, 42, GREEN)
        elif "delta" in low or key == "atm_delta":
            ys = [0.18, 0.25, 0.34, 0.43, 0.50, 0.57, 0.66, 0.74, 0.82, 0.88, 0.93]
            ax = series_panel(img, box, [(ys, GREEN, 9)], lo=0, hi=1, pad=(70, 60, 70, 80))
            label(img, X0 + 85, Y0 + 325, "DELTA = PREMIUM SENSITIVITY", INK, 34)
            ring(img, ax.x(5), ax.y(ys[5]), 42, 42, YELLOW)
        elif "bull_call" in low:
            # Bull call: long lower-strike call + short higher-strike call.
            y = [-3,-3,-3,-3,-2,-1,1,3,4,4,4]
            payoff(img, box, xs, y, strike=5)
            label(img, X0 + 85, Y0 + 325, "BULL CALL: LIMITED UPSIDE", INK, 34)
            label(img, X0 + 85, Y0 + 650, "BUY LOWER CALL  ->  SELL HIGHER CALL", GREEN, 29)
        elif "bear_put" in low:
            # Bear put: long higher-strike put + short lower-strike put.
            y = [4,4,4,3,2,1,-1,-3,-3,-3,-3]
            payoff(img, box, xs, y, strike=5)
            label(img, X0 + 85, Y0 + 325, "BEAR PUT: LIMITED DOWNSIDE RISK", INK, 34)
            label(img, X0 + 85, Y0 + 650, "BUY HIGHER PUT  ->  SELL LOWER PUT", RED, 29)
        elif "bear_call" in low:
            # Bear call: short lower-strike call + long higher-strike call.
            y = [2,2,2,2,2,1,-1,-2,-2,-2,-2]
            payoff(img, box, xs, y, strike=5)
            label(img, X0 + 85, Y0 + 325, "BEAR CALL: CREDIT + DEFINED RISK", INK, 34)
            label(img, X0 + 85, Y0 + 650, "SELL LOWER CALL  ->  BUY HIGHER CALL", RED, 29)
        else:
            payoff(img, box, xs, [-4,-3,-2,-1,0,1,2,3,4,5,6], strike=5)
            label(img, X0 + 85, Y0 + 325, "PAYOFF / RISK MAP", INK, 34)
        if beat == "matlab":
            if "bull_call" in low:
                label(img, X0 + 85, Y0 + 705, "PROFIT CAPS ABOVE HIGH STRIKE", GREEN, 27)
            elif "bear_put" in low:
                label(img, X0 + 85, Y0 + 705, "PROFIT CAPS BELOW LOW STRIKE", RED, 27)
            elif "bear_call" in low:
                label(img, X0 + 85, Y0 + 705, "LOSS CAPS ABOVE HIGH STRIKE", RED, 27)
            else:
                arrow(img, (X0 + 170, Y0 + 700), (X1 - 170, Y0 + 700), GREEN, 12)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, "CHECK EXPIRY / IV / MAX RISK", YELLOW, 30)
        return
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
        return
    if kind in _BAR_CHARTS:
        vals, labels, flow, check = _BAR_CHARTS[kind]
        bars(img, box, vals, labels, [BLUE, GREEN, YELLOW], fmt="{:g}", hi=120, value_size=42, label_size=30)
        if beat == "hook":
            label(img, X0 + 85, Y0 + 325, "COMPARE THE NUMBERS", INK, 34)
        elif beat == "pehchan":
            ring(img, X0 + 210, Y0 + 565, 90, 70, YELLOW)
            label(img, X0 + 85, Y0 + 325, "READ EACH BAR", INK, 34)
        elif beat == "matlab":
            arrow(img, (X0 + 270, Y0 + 650), (X1 - 240, Y0 + 650), GREEN, 10)
            label(img, X0 + 85, Y0 + 650, flow, GREEN, 32)
        elif beat == "karein":
            label(img, X0 + 85, Y0 + 650, check, YELLOW, 32)
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
    # No fetched series for this card. Do not draw a fake index line.
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for i, txt in enumerate(("EVENT", "CONTEXT", "LIMIT")):
        y = Y0 + 340 + i * 200
        d.rounded_rectangle([X0 + 120, y, X1 - 120, y + 150], 24,
                            fill=(18, 28, 45), outline=YELLOW if i == 0 else MUTED, width=5)
        label(img, (X0 + X1) / 2, y + 48, txt, INK, 40, anchor="ma")
    label(img, X0 + 85, Y0 + 280, "NO PRICE DATA ON THIS CARD", YELLOW, 32)


def finance_card_scene(key):
    """Safe generic scene with a genuinely different visual composition per beat.

    Each beat intentionally changes the visual grammar:
      hook    = overview / attention point
      pehchan = component breakdown
      matlab  = cause -> effect flow
      karein  = practical checklist / risk frame

    This prevents the same static chart from occupying the whole Short.
    """
    kind = _topic_kind(key)
    title = _ascii_title(key)

    def beat(b, img):
        panel(img)
        label(img, (X0 + X1) / 2, Y0 + 92, title, YELLOW, 54, anchor="ma")
        stage = {
            "hook": "CORE IDEA",
            "pehchan": "WHAT IT IS",
            "matlab": "HOW IT WORKS",
            "karein": "WHAT TO CHECK",
        }[b]
        label(img, (X0 + X1) / 2, Y0 + 170, stage, MUTED, 30, anchor="ma")

        from PIL import ImageDraw
        d = ImageDraw.Draw(img)

        if b == "hook":
            # Wide overview: one strong visual with a single emphasis point.
            _draw_finance_visual(img, kind, key, "hook")
            A = img.copy()
            label(img, X0 + 85, Y0 + 255, "1 IDEA", YELLOW, 34)
            return A, img

        if b == "pehchan":
            # Replace the overview with a component map; deliberately no copy
            # of the hook chart.
            panel(img)
            boxes = _kind_rows({
                "fund": [("REVENUE", 170), ("PROFIT", 390), ("CASH", 610)],
                "stock": [("BUSINESS", 170), ("RESULTS", 390), ("VALUE", 610)],
                "business": [("CUSTOMER", 170), ("UNIT ECONOMICS", 390), ("CASH", 610)],
                "intraday": [("PRICE", 170), ("VOLUME", 390), ("VWAP", 610)],
                "opt": [("PREMIUM", 170), ("IV / GREEKS", 390), ("PAYOFF", 610)],
                "penny": [("PRICE", 170), ("VOLUME", 390), ("LIQUIDITY", 610)],
                "risk": [("CAPITAL", 170), ("DRAWDOWN", 390), ("RECOVERY", 610)],
                "pf": [("INCOME", 170), ("SAVE", 390), ("GOAL", 610)],
                "tax": [("GAIN", 170), ("EXEMPT", 390), ("TAX", 610)],
                "ipo": [("ISSUE", 170), ("ALLOT", 390), ("LIST", 610)],
                "market": [("INDEX", 170), ("EVENT", 390), ("RECOVERY", 610)],
            }, kind)
            for i, (txt, y) in enumerate(boxes):
                d.rounded_rectangle(
                    [X0 + 90, Y0 + y, X1 - 90, Y0 + y + 130],
                    24, fill=(18, 28, 45),
                    outline=GREEN if i == 1 else MUTED, width=5
                )
                label(img, (X0 + X1) / 2, Y0 + y + 38, txt, INK, 38, anchor="ma")
                if i < 2:
                    arrow(img, ((X0 + X1) / 2, Y0 + y + 138),
                          ((X0 + X1) / 2, Y0 + y + 202), YELLOW, 8, 20)
            label(img, X0 + 90, Y1 - 95, "BREAK IT INTO 3 PARTS", YELLOW, 32)
            A = img.copy()
            return A, img

        if b == "matlab":
            # Cause -> effect visual. This is a new line/flow composition,
            # not the hook chart.
            panel(img)
            vals = _kind_rows({
                "fund": ([40, 65, 90], ["REVENUE", "PROFIT", "CASH"]),
                "stock": ([55, 72, 88], ["SALES", "PROFIT", "VALUE"]),
                "business": ([35, 60, 92], ["CAC", "MARGIN", "LTV"]),
                "intraday": ([45, 62, 84], ["PRICE", "CONTEXT", "CONFIRM"]),
                "opt": ([30, 52, 80], ["SPOT", "DELTA", "PREMIUM"]),
                "penny": ([85, 48, 20], ["PRICE", "VOLUME", "LIQUIDITY"]),
                "risk": ([100, 80, 112], ["START", "LOSS", "RECOVERY"]),
                "pf": ([100, 55, 85], ["INCOME", "SAVE", "GOAL"]),
                "tax": ([100, 40, 18], ["GAIN", "EXEMPT", "TAX"]),
                "ipo": ([80, 50, 90], ["ISSUE", "ALLOT", "LIST"]),
                "market": ([100, 82, 128], ["INDEX", "EVENT", "RECOVERY"]),
            }, kind)
            ax = Axes((X0 + 80, Y0 + 300, X1 - 80, Y1 - 120),
                      len(vals[0]), 0, max(vals[0]) * 1.15,
                      pad=(70, 60, 70, 70), ypad=0.03)
            polyline(img, [(ax.x(i), ax.y(v)) for i, v in enumerate(vals[0])],
                     GREEN if kind not in {"penny", "risk"} else RED, 10)
            for i, txt in enumerate(vals[1]):
                ring(img, ax.x(i), ax.y(vals[0][i]), 34, 34,
                     YELLOW if i == 1 else MUTED)
                label(img, ax.x(i), Y1 - 85, txt, INK, 28, anchor="ma")
                if i < 2:
                    arrow(img, (ax.x(i) + 35, ax.y(vals[0][i])),
                          (ax.x(i + 1) - 35, ax.y(vals[0][i + 1])),
                          GREEN, 7, 18)
            label(img, X0 + 85, Y0 + 255, "CAUSE  ->  EFFECT", YELLOW, 34)
            A = img.copy()
            return A, img

        # karein: a checklist/risk frame, intentionally no chart reuse.
        panel(img)
        checks = _kind_rows({
            "fund": ["QUALITY", "CASH FLOW", "VALUATION"],
            "stock": ["RESULTS", "DEBT", "VALUATION"],
            "business": ["CUSTOMER", "MARGIN", "CASH"],
            "intraday": ["CONTEXT", "CONFIRMATION", "RISK"],
            "opt": ["IV", "EXPIRY", "MAX RISK"],
            "penny": ["VOLUME", "SPREAD", "DILUTION"],
            "risk": ["POSITION SIZE", "STOP LOSS", "RECOVERY MATH"],
            "pf": ["GOAL", "TIME", "COST"],
            "tax": ["HOLDING", "EXEMPTION", "RATE"],
            "ipo": ["DRHP", "LOT SIZE", "RISK"],
            "market": ["DATE", "SOURCE", "METHOD"],
        }, kind)
        for i, txt in enumerate(checks):
            y = Y0 + 300 + i * 190
            d.rounded_rectangle(
                [X0 + 100, y, X1 - 100, y + 125],
                26, fill=(15, 23, 42), outline=GREEN, width=6
            )
            tick(img, X0 + 155, y + 62, 30)
            fit_label(img, X0 + 220, y + 35, txt, X1 - X0 - 360, INK, 38)
        label(img, X0 + 100, Y1 - 80, "CHECK BEFORE YOU ACT", YELLOW, 32)
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
        from market_data import format_growth
        grown = format_growth(start["close"], last["close"])
        mult = grown["multiple"]
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
            label(img, ax.x(0) + 20, ax.y(pts[0]) - 110, f"{grown['start_text']}  ({year})", YELLOW, 36, bg=(6, 10, 20))
            label(img, ax.x(n) - 20, ax.y(pts[-1]) + 50, f"{grown['end_text']}  (JUN 2026)", YELLOW, 36,
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
                         [(grown["equation"], 60, INK),
                          (grown["multiple_label"], 120, RED if loser else YELLOW)])
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
            end_txt = grown["result_label"]
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
    beat = {"udaharan": "matlab", "galti": "karein"}.get(beat, beat)
    return _scene_factory(key)()(beat, img)


def cue(key):
    """Regex: the spoken phrase at which a beat's third picture should appear."""
    return getattr(_scene_factory(key)(), "cue", None)


# ================================================================ voice ↔ chart
class ChartVoiceError(RuntimeError):
    """The chart's ₹ / % figures or its concept do not match this beat's line."""


_STOP_MARKS = ("रुक", "रोक", "बंद", "हिट", "स्टॉप", "टर्मिनल", "ज्यादा न")
_PILLS = {
    "hook": "HOOK",
    "pehchan": "1 PEHCHAN",
    "matlab": "2 MATLAB",
    "karein": "3 KYA KAREIN",
}


def _norm_num(kind, value):
    if kind == "pct":
        return round(float(value), 4)
    return int(round(float(value)))


def _classify_label(label):
    """('inr'|'pct'|None, value) for one on-chart or spoken figure."""
    import sync_gate
    raw = str(label or "")
    text = sync_gate._alias(raw).replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None, None
    value = float(match.group(1))
    if "%" in raw or "प्रतिशत" in text:
        return "pct", _norm_num("pct", value)
    if "लाख" in text:
        value *= 100000
    elif "करोड" in text:
        value *= 10000000
    elif "हजार" in text and value < 1000:
        value *= 1000
    if "₹" in raw or "रुप" in text or "लाख" in text or "हजार" in text:
        return "inr", _norm_num("inr", value)
    return None, None


def _surface_label(text, kind, value, fallback):
    """Keep the spelling the line already uses. Hindi amounts stay readable."""
    import scene_frames
    if fallback and fallback in (text or ""):
        return fallback
    if kind == "pct":
        shown = int(value) if float(value).is_integer() else value
        return f"{shown}%"
    amount = int(value)
    aliased = __import__("sync_gate")._alias(text or "")
    if "लाख" in aliased and amount % 100000 == 0 and amount >= 100000:
        return f"₹{amount // 100000} लाख"
    return scene_frames._indian_rupee(amount)


def labels_from_voice(text):
    """(label, kind, value) for every ₹ or % this line says, once each."""
    import scene_frames
    pool = list(scene_frames._figure_tokens([text or ""]))
    pool.extend(scene_frames._spoken_figures(text or "", scene_frames._digit_tokens(text or "")))
    found = []
    seen = set()
    for label in pool:
        kind, value = _classify_label(label)
        if kind not in ("inr", "pct"):
            continue
        key = (kind, value)
        if key in seen:
            continue
        seen.add(key)
        found.append((_surface_label(text, kind, value, label), kind, value))
    return found


def concept_for(text, labels=None):
    """The picture this line is teaching. Stop wins over the capital identity."""
    import sync_gate
    labels = labels_from_voice(text) if labels is None else labels
    rupees = [value for _label, kind, value in labels if kind == "inr"]
    percents = [value for _label, kind, value in labels if kind == "pct"]
    raw = sync_gate._alias(text or "")
    if any(mark in raw for mark in _STOP_MARKS) and (rupees or percents):
        return "pnl_stop"
    if len(rupees) >= 2 and percents:
        capital, limit = max(rupees), min(rupees)
        if capital > 0 and abs((limit / capital) * 100.0 - percents[0]) <= 0.25:
            return "capital_slice"
    if len(rupees) + len(percents) >= 2:
        return "compare"
    if rupees or percents:
        return "level"
    return "plain"


def chart_spec_for(text):
    """Figures and concept taken only from this beat's voice line."""
    labels = labels_from_voice(text)
    return {
        "concept": concept_for(text, labels),
        "rupees": sorted({value for _label, kind, value in labels if kind == "inr"}),
        "percents": sorted({value for _label, kind, value in labels if kind == "pct"}),
        "labels": [{"text": label, "kind": kind, "value": value} for label, kind, value in labels],
    }


def chart_voice_faults(spec, narration):
    """₹ or % on the chart but not in the line, or the other way around, or the wrong picture."""
    spec = spec or {}
    voice = chart_spec_for(narration)
    faults = []
    chart_r = {int(v) for v in (spec.get("rupees") or [])}
    chart_p = {round(float(v), 4) for v in (spec.get("percents") or [])}
    voice_r = set(voice["rupees"])
    voice_p = set(voice["percents"])
    for value in sorted(chart_r - voice_r):
        faults.append(f"chart shows ₹{value} which this beat does not say")
    for value in sorted(voice_r - chart_r):
        faults.append(f"narration ₹{value} is missing from the chart")
    for value in sorted(chart_p - voice_p):
        faults.append(f"chart shows {value:g}% which this beat does not say")
    for value in sorted(voice_p - chart_p):
        faults.append(f"narration {value:g}% is missing from the chart")
    expected = voice["concept"]
    if spec.get("concept") != expected:
        faults.append(f"chart concept {spec.get('concept')} does not match {expected}")
    return faults


def enforce_chart_voice(spec, narration):
    faults = chart_voice_faults(spec, narration)
    if faults:
        raise ChartVoiceError("; ".join(faults))
    return spec


def _plot_box():
    x0, y0, x1, y1 = BOARD
    return (x0 + 48, y0 + 150, x1 - 48, y1 - 28)


def _draw_geometry(img, spec, index=0, plot=None):
    """Bars, the stop line, or a plain trace. Labels are drawn afterwards."""
    from PIL import ImageDraw
    plot = plot or _plot_box()
    concept = spec.get("concept")
    labels = spec.get("labels") or []
    rupees = [item for item in labels if item["kind"] == "inr"]
    percents = [item for item in labels if item["kind"] == "pct"]
    draw = ImageDraw.Draw(img)
    img.chart_bar_boxes = []
    if concept == "capital_slice" and len(rupees) >= 2:
        capital = max(rupees, key=lambda item: item["value"])
        limit = min(rupees, key=lambda item: item["value"])
        left, top, right, bottom = plot
        height = min(280, (bottom - top) * 0.42)
        bar_y0 = top + (bottom - top - height) * 0.55
        bar_y1 = bar_y0 + height
        width = right - left
        slice_w = max(8.0, width * (float(limit["value"]) / float(capital["value"])))
        draw.rounded_rectangle([left, bar_y0, right, bar_y1], 18, fill=GREEN)
        draw.rounded_rectangle([left, bar_y0, left + slice_w, bar_y1], 12, fill=YELLOW)
        img.chart_bar_boxes = [
            (float(left), float(bar_y0), float(right), float(bar_y1), float(capital["value"])),
            (float(left), float(bar_y0), float(left + slice_w), float(bar_y1), float(limit["value"])),
        ]
        ring(img, left + slice_w, (bar_y0 + bar_y1) / 2, 28, 46, YELLOW)
        return {"bar": (left, bar_y0, right, bar_y1), "slice": slice_w}
    if concept == "compare" and len(labels) >= 2:
        rows = percents if len(percents) >= 2 else rupees
        if len(rows) < 2:
            rows = labels
        peak = max(float(item["value"]) for item in rows) or 1.0
        left, top, right, bottom = plot
        gap = 36
        count = len(rows)
        span = right - left
        bw = (span - gap * (count + 1)) / count
        base = bottom - 20
        room = (bottom - top) * 0.62
        boxes = []
        for i, item in enumerate(rows):
            h = max(24.0, room * (float(item["value"]) / peak))
            x = left + gap + i * (bw + gap)
            y0 = base - h
            draw.rounded_rectangle([x, y0, x + bw, base], 14, fill=GREEN if i % 2 == 0 else YELLOW)
            boxes.append((float(x), float(y0), float(x + bw), float(base), float(item["value"])))
        img.chart_bar_boxes = boxes
        return {"bars": boxes, "rows": rows}
    if concept == "pnl_stop":
        loss = min((item["value"] for item in rupees), default=0) or 1
        ys = [0, -0.2 * loss, -0.45 * loss, -0.75 * loss, -loss]
        ax = series_panel(
            img, plot, [(ys, RED, 8)], lo=-loss * 1.25, hi=loss * 0.35, pad=(36, 24, 36, 24),
        )
        dashed(img, plot[0] + 16, plot[2] - 16, ax.y(-loss), RED)
        ring(img, ax.x(len(ys) - 1), ax.y(-loss), 34, 34, YELLOW)
        return {"stop_y": ax.y(-loss), "plot": plot}
    if concept == "level" and labels:
        left, top, right, bottom = plot
        y = (top + bottom) / 2 + (index % 3) * 12
        dashed(img, left, right, y, YELLOW)
        ring(img, (left + right) / 2, y, 40, 40, YELLOW)
        return {"level_y": y, "plot": plot}
    left, top, right, bottom = plot
    mid = (top + bottom) / 2
    polyline(img, [(left + 20, mid + 40), ((left + right) / 2, mid - 30), (right - 20, mid + 10)], GREEN, 8)
    return {"plot": plot}


def _place(img, x, y, text, fill, size, anchor="lt", role="chart"):
    import scene_frames
    return scene_frames._text(
        img, x, y, text, fill, size, anchor=anchor, band=True, role=role,
    )


def _draw_labels(img, spec, geom):
    import scene_frames
    gold = scene_frames.GOLD
    labels = spec.get("labels") or []
    concept = spec.get("concept")
    if concept == "capital_slice" and geom.get("bar"):
        left, bar_y0, right, _bar_y1 = geom["bar"]
        rupees = [item for item in labels if item["kind"] == "inr"]
        capital = max(rupees, key=lambda item: item["value"])
        limit = min(rupees, key=lambda item: item["value"])
        percents = [item for item in labels if item["kind"] == "pct"]
        _place(img, left + 12, bar_y0 - 148, limit["text"], gold, 64, anchor="lt")
        if percents:
            _place(img, left + 12, bar_y0 - 78, percents[0]["text"], gold, 48, anchor="lt")
        _place(img, right - 280, bar_y0 - 78, capital["text"], gold, 64, anchor="lt")
        return
    if concept == "compare" and geom.get("bars"):
        rows = geom.get("rows") or labels
        for item, box in zip(rows, geom["bars"]):
            cx = (box[0] + box[2]) / 2
            _place(img, cx, max(box[1] - 78, 180), item["text"], gold, 64, anchor="mt")
        return
    if concept == "pnl_stop":
        plot = geom.get("plot") or _plot_box()
        _place(img, plot[0] + 16, geom.get("stop_y", plot[3]) - 48, "STOP", RED, 36, anchor="lt", role="axis")
        y = plot[1] + 12
        for item in labels:
            _place(img, plot[0] + 16, y, item["text"], gold, 56, anchor="lt")
            y += 70
        return
    if labels:
        plot = geom.get("plot") or _plot_box()
        _place(img, (plot[0] + plot[2]) / 2, geom.get("level_y", (plot[1] + plot[3]) / 2) - 80,
               labels[0]["text"], gold, 72, anchor="mb")


def _caption_lines(text, width=920, size=40, max_lines=2):
    import scene_frames
    words = str(text or "").split()
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if scene_frames._text_extent(trial, size)[0] <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines]


def _draw_chrome(img, beat, card, index=0):
    """Badge inside the chart box, and the 1–2 line caption under it."""
    import safe_area
    import scene_frames
    x0, y0, _x1, _y1 = BOARD
    _place(img, x0 + 36, y0 + 28, _PILLS.get(beat, beat.upper()), YELLOW, 28, anchor="lt", role="axis")
    lines = _caption_lines(card)
    y = safe_area.SUBTITLE_TOP + 16
    for line in lines:
        scene_frames._text(
            img, 540, y, line, (250, 250, 250), 40, anchor="mt", band=False, role="caption",
        )
        y += 62
    scene_frames._brand(img)
    caps = [
        layer["box"] for layer in (img.text_layers or [])
        if layer.get("role") == "caption" and layer.get("box")
    ]
    if caps:
        img.panel_box = (
            min(box[0] for box in caps), min(box[1] for box in caps),
            max(box[2] for box in caps), max(box[3] for box in caps),
        )
        img.panel_fill = scene_frames.panel_fill_fraction(img)
    else:
        img.panel_fill = 1.0


def _bare_word(word):
    import sync_gate
    text = sync_gate._alias(str(word or ""))
    return re.sub(r"[^\dA-Za-z\u0900-\u097F%]", "", text)


def _pair_figure(word, nxt):
    """A Hindi amount or percent that occupies this word and the next."""
    import sync_gate
    left, right = _bare_word(word), _bare_word(nxt)
    if left in sync_gate._ONES and right in ("प्रतिशत", "percent"):
        return "pct", _norm_num("pct", sync_gate._ONES[left]), 2
    if left in sync_gate._FRACTION and right in ("प्रतिशत", "percent"):
        return "pct", _norm_num("pct", sync_gate._FRACTION[left]), 2
    if left in sync_gate._ONES and right in sync_gate._SCALE:
        return "inr", _norm_num("inr", sync_gate._ONES[left] * sync_gate._SCALE[right]), 2
    if left in sync_gate._FRACTION and right in sync_gate._SCALE:
        return "inr", _norm_num("inr", sync_gate._FRACTION[left] * sync_gate._SCALE[right]), 2
    return None, None, 1


def _allowed_figure_keys(topic, beat, index):
    """Figures this scene may draw. None when the scene has no installed list."""
    preset = (topic or {}).get("_scene_figures")
    if not isinstance(preset, dict) or beat not in preset:
        return None
    rows = preset.get(beat) or []
    if index >= len(rows):
        return None
    allowed = set()
    for label in rows[index] or []:
        kind, value = _classify_label(label)
        if kind:
            allowed.add((kind, value))
    return allowed


def _drop_unsaid_figures(text, allowed):
    """Remove ₹/% phrases this scene has not reached yet."""
    words = str(text or "").split()
    if not words:
        return ""
    drop = [False] * len(words)
    i = 0
    while i < len(words):
        kind, value, width = _pair_figure(words[i], words[i + 1] if i + 1 < len(words) else "")
        if kind:
            if (kind, value) not in allowed:
                for j in range(i, min(len(words), i + width)):
                    drop[j] = True
            i += width
            continue
        kind, value = _classify_label(words[i])
        if kind and (kind, value) not in allowed:
            drop[i] = True
        i += 1
    return " ".join(word for word, gone in zip(words, drop) if not gone)


def _voice_for_scene(topic, beat, index, card):
    """The card, without a figure whose words have not started."""
    allowed = _allowed_figure_keys(topic, beat, index)
    if allowed is None:
        return card
    voice = _drop_unsaid_figures(card, allowed)
    have = {(kind, value) for _label, kind, value in labels_from_voice(voice)}
    extra = []
    rows = ((topic or {}).get("_scene_figures") or {}).get(beat) or []
    for label in (rows[index] if index < len(rows) else []) or []:
        kind, value = _classify_label(label)
        if not kind or (kind, value) not in allowed or (kind, value) in have:
            continue
        extra.append(str(label))
        have.add((kind, value))
    if extra:
        voice = (voice + " " + " ".join(extra)).strip()
    return voice


def _prefer_installed_labels(topic, beat, index, spec):
    """Use the scene's own figure spelling when it names the same amount."""
    preset = (topic or {}).get("_scene_figures")
    if not isinstance(preset, dict) or beat not in preset:
        return spec
    rows = preset.get(beat) or []
    if index >= len(rows):
        return spec
    for label in rows[index] or []:
        kind, value = _classify_label(label)
        if kind not in ("inr", "pct"):
            continue
        for item in spec.get("labels") or []:
            if item["kind"] == kind and item["value"] == value:
                item["text"] = str(label)
    return spec


def paint_chart_board(topic, beat, index=0, n=1):
    """One Chart Board frame. The spec is this scene's line, or the render fails."""
    import scene_frames
    spoken = scene_frames._scene_card_text(topic, beat, index, n)
    card = _voice_for_scene(topic, beat, index, spoken)
    spec = _prefer_installed_labels(topic, beat, index, chart_spec_for(card))
    enforce_chart_voice(spec, card)
    img = K.background()
    panel(img)
    img.label_boxes = []
    img.text_layers = []
    img.shapes = []
    img.labelled_graphic_boxes = []
    img.chart_region = tuple(float(v) for v in BOARD)
    img.scene_kind = f"chart-{beat}"
    img.chart_spec = spec
    geom = _draw_geometry(img, spec, index=index)
    _draw_labels(img, spec, geom or {})
    _draw_chrome(img, beat, spoken, index=index)
    return img


def cover_hook(topic):
    """Three or four Devanagari words. The first thumb line when it already is."""
    parts = [str(part).strip() for part in (topic or {}).get("thumb") or [] if str(part).strip()]
    if parts:
        words = parts[0].replace("?", "").replace("।", "").split()
        if 2 <= len(words) <= 4:
            return " ".join(words)
        if len(words) > 4:
            return " ".join(words[:4])
    words = " ".join(parts).replace("?", "").split()
    return " ".join(words[:4]) or "दिन की हद"


def cover_line(topic):
    """The voice line the cover illustrates. A capital-slice line wins."""
    import scene_frames
    spoken = (topic or {}).get("_spoken_beats") or {}
    lines = []
    for beat in ("pehchan", "matlab", "hook", "karein"):
        text = " ".join(str(spoken.get(beat) or "").split())
        if text:
            lines.append(text)
    if not lines:
        for beat in ("pehchan", "hook", "matlab", "karein"):
            text = scene_frames._scene_card_text(topic, beat, 0, 1)
            if text:
                lines.append(text)
    best_text, best_spec = "", chart_spec_for("")
    for text in lines:
        spec = chart_spec_for(text)
        if spec["concept"] == "capital_slice":
            return text, spec
        if len(spec["labels"]) > len(best_spec["labels"]):
            best_text, best_spec = text, spec
    return best_text, best_spec


def _bold_amount(spec):
    import scene_frames
    rupees = [int(v) for v in (spec.get("rupees") or [])]
    if not rupees:
        return ""
    value = min(rupees) if spec.get("concept") == "capital_slice" else rupees[0]
    return scene_frames._indian_rupee(value) + " MAX"


def paint_cover(topic):
    """1080x1920 Chart Board cover. Same gate as a beat: numbers match the line."""
    import safe_area
    import scene_frames
    from PIL import ImageDraw
    line, spec = cover_line(topic)
    joined = " ".join(str(v) for v in ((topic or {}).get("_spoken_beats") or {}).values()) or line
    faults = chart_voice_faults(spec, line)
    if joined.strip():
        full = chart_spec_for(joined)
        for value in spec["rupees"]:
            if value not in set(full["rupees"]):
                faults.append(f"cover shows ₹{value} which the narration does not say")
        for value in spec["percents"]:
            if value not in set(full["percents"]):
                faults.append(f"cover shows {value:g}% which the narration does not say")
    if faults:
        raise ChartVoiceError("; ".join(faults))
    img = K.background()
    # The IG grid keeps y 240–1680. The chart, the hook, and the amount sit there.
    box = (72, 400, 1008, 1120)
    ImageDraw.Draw(img).rounded_rectangle(box, 28, fill=K.PANEL, outline=(60, 80, 110), width=4)
    img.label_boxes = []
    img.text_layers = []
    img.shapes = []
    img.labelled_graphic_boxes = []
    img.chart_bar_boxes = []
    img.chart_region = tuple(float(v) for v in box)
    img.chart_spec = spec
    img.scene_kind = "chart-cover"
    geom = _draw_geometry(img, spec, index=0, plot=(box[0] + 36, box[1] + 36, box[2] - 36, box[3] - 28))
    _draw_labels(img, spec, geom or {})
    hook = cover_hook(topic)
    scene_frames._text(
        img, 540, 280, hook, (255, 255, 255), 72, anchor="mt", band=False, role="caption",
    )
    bold = _bold_amount(spec)
    if bold:
        scene_frames._text(
            img, 540, 1280, bold, scene_frames.GOLD, 92, anchor="mt", band=False, role="chart",
        )
    # Safe-area check is the caller's. Keep every sprite inside 6%.
    del safe_area
    return img
