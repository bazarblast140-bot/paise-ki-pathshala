"""Levels computed from OHLC. The script never invents a price."""
from __future__ import annotations

import math


UNIVERSE = (
    ("NSE:NIFTY", "^NSEI"),
    ("NSE:BANKNIFTY", "^NSEBANK"),
    ("NSE:RELIANCE", "RELIANCE.NS"),
    ("NSE:HDFCBANK", "HDFCBANK.NS"),
    ("NSE:ICICIBANK", "ICICIBANK.NS"),
    ("NSE:INFY", "INFY.NS"),
    ("NSE:TCS", "TCS.NS"),
    ("NSE:SBIN", "SBIN.NS"),
    ("NSE:ITC", "ITC.NS"),
    ("NSE:LT", "LT.NS"),
    ("NSE:AXISBANK", "AXISBANK.NS"),
    ("NSE:BHARTIARTL", "BHARTIARTL.NS"),
    ("NSE:KOTAKBANK", "KOTAKBANK.NS"),
    ("NSE:BAJFINANCE", "BAJFINANCE.NS"),
)


def _ema(values, span):
    alpha = 2.0 / (span + 1.0)
    acc = None
    out = []
    for value in values:
        acc = float(value) if acc is None else alpha * float(value) + (1.0 - alpha) * acc
        out.append(acc)
    return out


def _wilder(values, period=14):
    alpha = 1.0 / period
    acc = None
    out = []
    for value in values:
        acc = float(value) if acc is None else alpha * float(value) + (1.0 - alpha) * acc
        out.append(acc)
    return out


def _f(bar, name):
    return float(bar[name])


def compute(bars, symbol="NSE:NIFTY", yf="^NSEI"):
    """Support, SL, target, EMA and RSI from daily bars. Last bar is the close."""
    rows = list(bars or [])
    if len(rows) < 20:
        raise ValueError("need at least 20 daily bars")
    closes = [_f(row, "c") for row in rows]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    deltas = [0.0] + [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    up = _wilder([max(0.0, item) for item in deltas], 14)
    down = _wilder([max(0.0, -item) for item in deltas], 14)
    rsi = []
    for up_i, down_i in zip(up, down):
        if down_i == 0:
            rsi.append(100.0)
        else:
            rsi.append(100.0 - 100.0 / (1.0 + up_i / down_i))
    trs = [rows[0]["h"] - rows[0]["l"]]
    for prev, row in zip(rows, rows[1:]):
        trs.append(max(
            _f(row, "h") - _f(row, "l"),
            abs(_f(row, "h") - _f(prev, "c")),
            abs(_f(row, "l") - _f(prev, "c")),
        ))
    atr = _wilder(trs, 14)
    last = rows[-1]
    prev = rows[-2]
    window = rows[max(0, len(rows) - 253):-1]
    sup_row = min(window, key=lambda row: _f(row, "l"))
    support = _f(sup_row, "l")
    win = rows[max(0, len(rows) - 11):-1]
    piv = min(win, key=lambda row: _f(row, "l"))
    piv_at = rows.index(piv)
    after = rows[piv_at + 1:-1]
    bounce = max(after or win, key=lambda row: _f(row, "h"))
    two_y = rows[max(0, len(rows) - 505):-1]
    deeper = [row for row in two_y if _f(row, "l") < min(support, _f(last, "l"))]
    nxt = min(deeper, key=lambda row: _f(row, "l")) if deeper else None
    atr_last = float(atr[-1])
    sl_low = math.floor((support - 0.5 * atr_last) / 10.0) * 10
    sl_high = min(support, _f(last, "l"))
    close = _f(last, "c")
    prev_close = _f(prev, "c")
    return {
        "symbol": symbol,
        "yf": yf,
        "last_date": str(last.get("date") or ""),
        "open": _f(last, "o"),
        "high": _f(last, "h"),
        "low": _f(last, "l"),
        "close": close,
        "prev_close": prev_close,
        "chg": close - prev_close,
        "chg_pct": 100.0 * (close / prev_close - 1.0) if prev_close else 0.0,
        "ema20": float(ema20[-1]),
        "ema50": float(ema50[-1]),
        "rsi14": float(rsi[-1]),
        "atr14": atr_last,
        "support": support,
        "support_date": str(sup_row.get("date") or ""),
        "bounce_high": _f(bounce, "h"),
        "bounce_high_date": str(bounce.get("date") or ""),
        "next_support": None if nxt is None else _f(nxt, "l"),
        "next_support_date": None if nxt is None else str(nxt.get("date") or ""),
        "sl_zone": [sl_low, sl_high],
        "target_zone": [_f(bounce, "h"), float(ema20[-1])],
        "bars": rows[-260:],
    }


def _score(levels):
    close = float(levels["close"])
    low = float(levels["low"])
    support = float(levels["support"])
    prior = levels["bars"][:-1]
    high20 = max(float(row["h"]) for row in prior[-20:]) if prior else close
    if low <= support * 1.004 and close >= support:
        return "support test", 3
    if close > high20:
        return "breakout", 2
    ema20 = float(levels["ema20"])
    ema50 = float(levels["ema50"])
    if ema20 > ema50 and abs(ema20 - ema50) / close < 0.01:
        return "ema cross", 2
    if abs(low - support) / close < 0.015:
        return "retest", 1
    return "support test", 1


def pick_setup(candidates, history, today):
    """Highest score that is not the same symbol and setup inside 5 days."""
    ranked = []
    for levels in candidates:
        setup, score = _score(levels)
        if _repeated(history, levels["symbol"], setup, today):
            continue
        ranked.append((score, levels, setup))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    _score_n, levels, setup = ranked[0]
    picked = dict(levels)
    picked["setup"] = setup
    return picked


def _repeated(history, symbol, setup, today):
    for item in history or []:
        if item.get("symbol") != symbol or item.get("setup") != setup:
            continue
        try:
            then = datetime_date(str(item.get("date") or ""))
        except ValueError:
            continue
        gap = (today - then).days
        if 0 <= gap <= 5:
            return True
    return False


def datetime_date(text):
    from datetime import date
    year, month, day = [int(part) for part in str(text)[:10].split("-")]
    return date(year, month, day)
