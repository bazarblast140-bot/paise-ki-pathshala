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


# The evening TA Reel is ONE liquid NSE stock a day; Nifty has the 4 PM close Reel
# (Rajesh, 16-Oct-2026). Indices never enter the TA scan.
STOCK_UNIVERSE = tuple((f"NSE:{name}", f"{name}.NS") for name in (
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "ITC", "LT", "AXISBANK",
    "BHARTIARTL", "KOTAKBANK", "BAJFINANCE", "HINDUNILVR", "MARUTI", "SUNPHARMA", "TITAN",
    "M&M", "NTPC", "POWERGRID", "TATASTEEL", "ULTRACEMCO", "ASIANPAINT", "HCLTECH", "WIPRO",
    "ONGC", "COALINDIA", "JSWSTEEL", "ADANIPORTS", "BAJAJFINSV", "NESTLEIND", "TECHM",
    "HINDALCO", "GRASIM", "CIPLA", "DRREDDY", "EICHERMOT", "BAJAJ-AUTO", "HEROMOTOCO",
    "TRENT", "BEL", "SHRIRAMFIN", "APOLLOHOSP", "TATACONSUM", "INDUSINDBK", "BPCL",
))
MIN_RR = 1.5


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


def pick_stock(candidates, history, today):
    """Try candidates (most-tested level first) until one has a clear pattern and RR >= 1.5."""
    ranked = []
    for levels in candidates:
        plan = stock_plan(levels)
        if plan is None or _repeated(history, levels["symbol"], plan["pattern"], today):
            continue
        # Most-tested level first; on a tie the bigger real move (a stronger hook number).
        ranked.append((plan["touches"], abs(float(levels.get("chg_pct") or 0)), -plan["distance_atr"], levels, plan))
    ranked.sort(key=lambda item: item[:3], reverse=True)
    return [(levels, plan) for _t, _m, _d, levels, plan in ranked]


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


INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50"}
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}


def floor_pivots(bar):
    """Classic floor pivot from one day's OHLC. The prior day is the caller's job."""
    high = float(bar["h"])
    low = float(bar["l"])
    close = float(bar["c"])
    pivot = (high + low + close) / 3.0
    span = high - low
    return {
        "pivot": pivot,
        "r1": 2.0 * pivot - low,
        "s1": 2.0 * pivot - high,
        "r2": pivot + span,
        "s2": pivot - span,
        "pivot_date": str(bar.get("date") or ""),
    }


def option_chain_url(symbol):
    name = str(symbol or "").split(":")[-1].upper()
    if name in INDEX_SYMBOLS:
        return f"https://www.nseindia.com/api/option-chain-indices?symbol={name}"
    return f"https://www.nseindia.com/api/option-chain-equities?symbol={name}"


def _top_strikes(rows, side):
    ranked = []
    for row in rows:
        leg = row.get(side) or {}
        oi = leg.get("openInterest")
        strike = row.get("strikePrice", leg.get("strikePrice"))
        if oi is None or strike is None:
            continue
        try:
            ranked.append((float(oi), float(strike)))
        except (TypeError, ValueError):
            continue
    ranked.sort(key=lambda item: (-item[0], item[1]))
    seen = []
    for _oi, strike in ranked:
        if any(abs(strike - prev) < 0.01 for prev in seen):
            continue
        seen.append(strike)
        if len(seen) == 2:
            break
    if not seen:
        return None, None
    return seen[0], (seen[1] if len(seen) > 1 else None)


def _ist_clock(stamp):
    text = str(stamp or "").strip()
    parts = text.split()
    if len(parts) >= 2 and ":" in parts[1]:
        hour, minute = parts[1].split(":")[:2]
        return f"{hour}:{minute} IST"
    if not text:
        return ""
    return text if "IST" in text else f"{text} IST"


def parse_option_chain(payload, on_date):
    """Max call OI is resistance. Max put OI is support. Nothing is invented."""
    from datetime import datetime
    records = (payload or {}).get("records") or {}
    expiries = []
    for text in records.get("expiryDates") or []:
        try:
            expiries.append((datetime.strptime(str(text), "%d-%b-%Y").date(), str(text)))
        except ValueError:
            continue
    expiries.sort()
    chosen = next((item for item in expiries if item[0] >= on_date), None)
    if chosen is None and expiries:
        chosen = expiries[-1]
    if chosen is None:
        return {"ok": False, "reason": "NSE option chain has no expiry"}
    _day, expiry = chosen
    rows = [row for row in (records.get("data") or []) if str(row.get("expiryDate") or "") == expiry]
    call, call_next = _top_strikes(rows, "CE")
    put, put_next = _top_strikes(rows, "PE")
    if call is None or put is None:
        return {"ok": False, "reason": "NSE option chain has no open interest"}
    return {
        "ok": True,
        "expiry": expiry,
        "as_of": _ist_clock(records.get("timestamp")),
        "call_oi": call,
        "call_oi_next": call_next,
        "put_oi": put,
        "put_oi_next": put_next,
    }


def _nse_fetch(url, timeout=12):
    import http.cookiejar
    import urllib.request
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    home = urllib.request.Request(
        "https://www.nseindia.com/option-chain",
        headers={**NSE_HEADERS, "Accept": "text/html,application/xhtml+xml"},
    )
    opener.open(home, timeout=timeout).read(2048)
    req = urllib.request.Request(
        url, headers={**NSE_HEADERS, "Accept": "application/json, text/plain, */*"},
    )
    with opener.open(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", 200) or 200)
        body = resp.read()
    return status, body


def fetch_option_chain(symbol, on_date, loader=None):
    """NSE cookies then the option-chain API. A block holds OI; it does not invent strikes."""
    import json
    try:
        if loader is None:
            status, body = _nse_fetch(option_chain_url(symbol))
        else:
            status, body = loader(symbol)
        if int(status or 0) != 200 or not body:
            return {"ok": False, "reason": f"NSE option chain held: HTTP {status}"}
        if isinstance(body, dict):
            payload = body
            text = ""
        else:
            text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
            lowered = text.lower()
            if text.lstrip().startswith("<") or "captcha" in lowered or "access denied" in lowered:
                return {"ok": False, "reason": "NSE option chain held: blocked page"}
            payload = json.loads(text)
        parsed = parse_option_chain(payload, on_date)
        if not parsed.get("ok"):
            return {"ok": False, "reason": "NSE option chain held: " + str(parsed.get("reason") or "empty")}
        return parsed
    except Exception as exc:  # noqa: BLE001 — a blocked chain still leaves the pivots
        return {"ok": False, "reason": f"NSE option chain held: {type(exc).__name__}"}


def screen_zones(close, pivots, oi=None):
    """SL and target from pivot and OI levels. RR uses the nearest stop and the first target that clears 1.5."""
    supports = [pivots.get("s1"), pivots.get("s2")]
    resistances = [pivots.get("r1"), pivots.get("r2")]
    if oi and oi.get("ok"):
        supports.extend([oi.get("put_oi"), oi.get("put_oi_next")])
        resistances.extend([oi.get("call_oi"), oi.get("call_oi_next")])
    below = sorted({round(float(value), 4) for value in supports if value is not None and float(value) < close})
    above = sorted({round(float(value), 4) for value in resistances if value is not None and float(value) > close})
    if not below or not above:
        return None
    stop = below[-1]
    risk = close - stop
    if risk <= 0:
        return None
    chosen = None
    for target in above:
        if (target - close) / risk >= 1.5:
            chosen = target
            break
    if chosen is None:
        return None
    further = below[-2] if len(below) > 1 else stop
    nxt = next((target for target in above if target > chosen + 0.01), chosen)
    return {
        "sl_zone": [min(further, stop), max(further, stop)],
        "target_zone": [chosen, nxt],
        "rr": (chosen - close) / risk,
        "stop": stop,
        "target": chosen,
    }


def levels_chip(chain):
    if chain and chain.get("ok"):
        clock = chain.get("as_of") or ""
        return f"Levels: Pivot (prev day OHLC) + NSE option chain OI, {chain.get('expiry')}, {clock}"
    return "Levels: Pivot (prev day OHLC) only, NSE option chain held"


def apply_screen_levels(levels, chain):
    """Keep the structure fields. The zones on screen come from the pivot and, when NSE answered, OI."""
    bars = list(levels.get("bars") or [])
    if len(bars) < 2:
        levels["rr_ok"] = False
        levels["oi_status"] = "held"
        levels["levels_chip"] = levels_chip(None)
        return False
    pivots = floor_pivots(bars[-2])
    levels["structure_sl_zone"] = list(levels.get("sl_zone") or [])
    levels["structure_target_zone"] = list(levels.get("target_zone") or [])
    levels.update(pivots)
    oi_ok = bool(chain and chain.get("ok"))
    if oi_ok:
        for key in ("call_oi", "call_oi_next", "put_oi", "put_oi_next", "expiry"):
            if chain.get(key) is not None:
                levels[key] = chain[key]
        levels["oi_status"] = "ok"
        levels["oi_as_of"] = chain.get("as_of") or ""
    else:
        levels["oi_status"] = "held"
        levels["oi_reason"] = (chain or {}).get("reason") or "NSE option chain held"
        for key in ("call_oi", "call_oi_next", "put_oi", "put_oi_next", "expiry"):
            levels.pop(key, None)
    levels["levels_chip"] = levels_chip(chain if oi_ok else None)
    zones = screen_zones(float(levels["close"]), pivots, chain if oi_ok else None)
    if not zones:
        levels["rr"] = None
        levels["rr_ok"] = False
        return False
    levels["sl_zone"] = zones["sl_zone"]
    levels["target_zone"] = zones["target_zone"]
    levels["rr"] = zones["rr"]
    levels["rr_ok"] = True
    return True


def _swings(rows, side, span=3):
    """Confirmed swing lows ("l") or highs ("h"): the extreme of span bars each side."""
    out = []
    for i in range(span, len(rows) - span):
        value = float(rows[i][side])
        near = [float(row[side]) for row in rows[i - span:i + span + 1]]
        if (side == "l" and value <= min(near)) or (side == "h" and value >= max(near)):
            out.append((value, str(rows[i].get("date") or "")))
    return out


def stock_plan(levels, lookback=120):
    """A clear real pattern at support or resistance and a full plan, or None.

    Patterns (daily bars only, nothing invented):
      support bounce   - today's low tested a swing-low support and the close held above it;
      breakout         - today closed above the highest high of the last 20 sessions;
      resistance rejection - today's high tested a swing-high resistance and closed below it.
    The plan: valid zone, entry trigger (beyond today's candle), SL beyond the level,
    target at the next real swing level, invalidation (a daily close beyond SL). RR >= 1.5.
    """
    rows = list(levels.get("bars") or [])
    if len(rows) < 30:
        return None
    last = rows[-1]
    hist = rows[-lookback - 1:-1]
    close, high, low, opn = (float(last[k]) for k in ("c", "h", "l", "o"))
    atr = float(levels.get("atr14") or 0) or (high - low)
    lows = _swings(hist, "l")
    highs = _swings(hist, "h")
    buf = 0.25 * atr
    plans = []

    def build(pattern, side, level, level_date, zone, entry, stop, targets):
        risk = (entry - stop) if side == "long" else (stop - entry)
        if risk <= 0:
            return
        for target, target_date in targets:
            reward = (target - entry) if side == "long" else (entry - target)
            if reward / risk >= MIN_RR:
                touches = sum(1 for value, _d in (lows + highs) if abs(value - level) <= 0.5 * atr)
                plans.append({
                    "touches": touches + (1 if pattern != "breakout" else 0),
                    "pattern": pattern, "side": side,
                    "level": level, "level_date": level_date,
                    "support": level if side == "long" and pattern != "breakout" else None,
                    "zone": [min(zone), max(zone)], "entry": entry, "stop": stop,
                    "target": target, "target_date": target_date,
                    "rr": reward / risk, "invalidation": stop,
                    "distance_atr": abs(close - level) / atr if atr else 99.0,
                })
                return

    # Support bounce: the nearest swing low under the close that today's low touched.
    under = [item for item in lows if item[0] <= close]
    if under:
        level, when = max(under, key=lambda item: item[0])
        # A clean test: the low reached the level without slicing far through it.
        if level - 0.5 * atr <= low <= level + 0.5 * atr and close > level and close >= (high + low) / 2:
            above = sorted({item for item in highs if item[0] > high}, key=lambda item: item[0])
            build("support bounce", "long", level, when, (level, level + 0.5 * atr),
                  high, min(level, low) - buf, above)
    # Breakout above the 20-session high: the old high becomes support.
    box = hist[-20:]
    top = max(float(row["h"]) for row in box)
    top_date = str(max(box, key=lambda row: float(row["h"])).get("date") or "")
    if close > top and close - top <= 1.0 * atr:
        above = sorted({item for item in highs if item[0] > high}, key=lambda item: item[0])
        bottom = min(float(row["l"]) for row in box)
        measured = top + (top - bottom)
        targets = above + ([(measured, "20-day box height")] if not above or measured > above[-1][0] else [])
        build("breakout", "long", top, top_date, (top, close), high, top - buf, targets)
    # Resistance rejection: the nearest swing high above the low that today's high touched.
    over = [item for item in highs if item[0] >= close]
    if over:
        level, when = min(over, key=lambda item: item[0])
        if level - 0.5 * atr <= high <= level + 0.5 * atr and close < level and close <= (high + low) / 2:
            below = sorted({item for item in lows if item[0] < low}, key=lambda item: -item[0])
            build("resistance rejection", "short", level, when, (level - 0.5 * atr, level),
                  low, max(level, high) + buf, below)
    # "Clear" means the level was tested before: two touches or more (today's counts).
    plans = [plan for plan in plans if plan["touches"] >= 2]
    if not plans:
        return None
    plans.sort(key=lambda plan: (-plan["touches"], plan["distance_atr"]))
    plan = plans[0]
    # Opposite real level for the chart: resistance above a long, support below a short.
    if plan["side"] == "long":
        plan["resistance"] = plan["target"]
        plan["support"] = plan["level"]
    else:
        plan["resistance"] = plan["level"]
        plan["support"] = plan["target"]
    return plan


def apply_stock_plan(levels, plan):
    """Put the plan on the levels the renderer draws: red SL band, green target band."""
    levels["setup"] = plan["pattern"]
    levels["plan"] = plan
    levels["rr"] = plan["rr"]
    levels["rr_ok"] = plan["rr"] >= MIN_RR
    levels["support"] = plan["support"]
    levels["resistance"] = plan["resistance"]
    levels["entry"] = plan["entry"]
    levels["stop"] = plan["stop"]
    levels["target"] = plan["target"]
    levels["valid_zone"] = list(plan["zone"])
    width = max(0.1 * float(levels.get("atr14") or 1.0), 0.002 * float(levels["close"]))
    stop, target = float(plan["stop"]), float(plan["target"])
    levels["sl_zone"] = [stop - width, stop] if plan["side"] == "long" else [stop, stop + width]
    levels["target_zone"] = [target, target + width] if plan["side"] == "long" else [target - width, target]
    levels["levels_chip"] = "Levels: daily swing highs/lows (NSE daily data)"
    return levels
