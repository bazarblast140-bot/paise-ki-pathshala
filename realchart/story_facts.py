"""Facts for the story Reel. Every figure is parsed from a payload the caller fetched.

Nothing here downloads on import. Live helpers are optional and return None
when the source is unreachable, so a missing page becomes a missing line,
never a guessed number.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen


FII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
BREADTH_URL = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
SECTOR_URL = "https://www.nseindia.com/api/allIndices"
YAHOO = "https://finance.yahoo.com/quote/{ticker}/financials"


def detect_reversal(bars):
    """A textbook reversal candle on the last bar, or None. OHLC only."""
    rows = list(bars or [])
    if len(rows) < 8:
        return None
    last, prev = rows[-1], rows[-2]
    o, h, l, c = (float(last[k]) for k in ("o", "h", "l", "c"))
    span = h - l
    if span <= 0:
        return None
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    prior = rows[-7:-1]
    trend_up = float(prior[-1]["c"]) > float(prior[0]["c"])
    trend_down = float(prior[-1]["c"]) < float(prior[0]["c"])

    def pack(name, name_hi, side):
        return {
            "name": name,
            "name_hi": name_hi,
            "side": side,
            "low": l,
            "high": h,
            "close": c,
            "date": str(last.get("date") or "")[:10],
            "source": "computed from daily OHLC",
            "url": "",
        }

    if trend_down and lower >= max(2 * body, 0.5 * span) and upper <= max(body, 0.15 * span) and (c - l) / span >= 0.6:
        return pack("hammer", "हैमर", "bullish")
    if trend_up and upper >= max(2 * body, 0.5 * span) and lower <= max(body, 0.15 * span) and (h - c) / span >= 0.6:
        return pack("shooting star", "शूटिंग स्टार", "bearish")
    po, pc = float(prev["o"]), float(prev["c"])
    if trend_down and pc < po and c > o and c >= po and o <= pc and body > abs(pc - po):
        return pack("bullish engulfing", "बुलिश एंगल्फिंग", "bullish")
    if trend_up and pc > po and c < o and c <= po and o >= pc and body > abs(pc - po):
        return pack("bearish engulfing", "बेयरिश एंगल्फिंग", "bearish")
    return None


def example_plan(bars):
    """Stop and target from the last 20 bars' own high and low. Not a call."""
    window = list(bars or [])[-20:]
    if len(window) < 5:
        return None
    stop = min(float(row["l"]) for row in window)
    target = max(float(row["h"]) for row in window)
    close = float(window[-1]["c"])
    if abs(close - stop) < 1e-6 or abs(target - close) < 1e-6:
        return None
    return {
        "stop": stop,
        "target": target,
        "close": close,
        "source": "computed from daily OHLC",
        "url": "",
    }


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _yoy(new, old):
    if new is None or old is None or old == 0:
        return None
    return 100.0 * (float(new) / float(old) - 1.0)


def _newest(rows):
    return sorted(rows or [], key=lambda row: str(row.get("period") or ""), reverse=True)


def fundamentals_from_payload(payload):
    """Quarterly and yearly profit/revenue growth, plus Yahoo's own growth field.

    `quarterly` and `annual` rows are {period, revenue, profit}, any order.
    Growth is the latest period versus the same quarter a year earlier (5th
    row) or the previous annual row. earningsGrowth / revenueGrowth at or
    under 3 are treated as fractions (0.11 -> 11).
    """
    payload = payload or {}
    quarterly = _newest(payload.get("quarterly"))
    annual = _newest(payload.get("annual"))
    info = payload.get("info") or {}
    out = {
        "source": str(payload.get("source") or "Yahoo Finance"),
        "url": str(payload.get("url") or ""),
        "as_of": str((quarterly or annual or [{}])[0].get("period") or "")[:10],
    }
    if len(quarterly) >= 5:
        out["profit_yoy_q"] = _yoy(quarterly[0].get("profit"), quarterly[4].get("profit"))
        out["revenue_yoy_q"] = _yoy(quarterly[0].get("revenue"), quarterly[4].get("revenue"))
    if len(annual) >= 2:
        out["profit_yoy_y"] = _yoy(annual[0].get("profit"), annual[1].get("profit"))
        out["revenue_yoy_y"] = _yoy(annual[0].get("revenue"), annual[1].get("revenue"))
    growth = _num(info.get("earningsGrowth"))
    if growth is not None:
        out["future_growth"] = growth * 100.0 if abs(growth) <= 3 else growth
        out["future_label"] = "Yahoo Finance earningsGrowth"
    revenue_growth = _num(info.get("revenueGrowth"))
    if revenue_growth is not None:
        out["revenue_growth_fwd"] = revenue_growth * 100.0 if abs(revenue_growth) <= 3 else revenue_growth
    pe = _num(info.get("trailingPE"))
    if pe is not None:
        out["pe"] = pe
    return {key: value for key, value in out.items() if value is not None}


def parse_fii_dii(payload):
    """NSE fiidiiTradeReact list -> {fii_net_cr, dii_net_cr, date}."""
    out = {}
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("category") or "").upper()
        key = "fii" if ("FII" in label or "FPI" in label) else "dii" if "DII" in label else ""
        if not key:
            continue
        net = _num(row.get("netValue"))
        if net is None:
            continue
        out[f"{key}_net_cr"] = net
        if row.get("date"):
            out["date"] = str(row.get("date"))
    return out


def parse_breadth(payload):
    block = (payload or {}).get("advance") or payload or {}
    advances, declines = _num(block.get("advances")), _num(block.get("declines"))
    if advances is None or declines is None:
        return None
    unchanged = _num(block.get("unchanged"))
    return {
        "advances": int(advances),
        "declines": int(declines),
        "unchanged": int(unchanged or 0),
        "source": "NSE",
        "url": BREADTH_URL,
    }


def parse_sectors(rows):
    """Index rows {name, pct} -> best and worst by percent. Skips NIFTY 50 itself."""
    cleaned = []
    for row in rows or []:
        name = str(row.get("name") or row.get("index") or "").strip()
        pct = _num(row.get("pct") if "pct" in row else row.get("percentChange"))
        if not name or pct is None:
            continue
        if name.upper().replace(" ", "") in ("NIFTY50", "NIFTY"):
            continue
        cleaned.append({"name": name, "pct": pct})
    if len(cleaned) < 2:
        return None
    best = max(cleaned, key=lambda item: item["pct"])
    worst = min(cleaned, key=lambda item: item["pct"])
    if best["name"] == worst["name"]:
        return None
    return {"best": best, "worst": worst, "source": "NSE", "url": SECTOR_URL}


def flow_sentence(flows):
    """One impact line. The only number is the fetched net, added by the caller."""
    net = (flows or {}).get("fii_net_cr")
    if net is None:
        return ""
    if net < 0:
        return "एफआईआई की बिकवाली से बैंकों पर दबाव हो सकता है।"
    if net > 0:
        return "एफआईआई की खरीद से बाज़ार को सहारा मिल सकता है।"
    return "एफआईआई का नेट लगभग शून्य है।"


def _get(url, timeout=20):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    })
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_fii_dii(opener=None):
    raw = (opener or _get)(FII_URL)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    data = json.loads(raw) if isinstance(raw, str) else raw
    parsed = parse_fii_dii(data)
    if not parsed:
        return None
    parsed["source"] = "NSE"
    parsed["url"] = FII_URL
    return parsed


def fetch_breadth(opener=None):
    raw = (opener or _get)(BREADTH_URL)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    data = json.loads(raw) if isinstance(raw, str) else raw
    parsed = parse_breadth(data)
    return parsed


def fetch_sectors(opener=None):
    raw = (opener or _get)(SECTOR_URL)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    data = json.loads(raw) if isinstance(raw, str) else raw
    rows = data.get("data") if isinstance(data, dict) else data
    parsed = parse_sectors(rows)
    return parsed


def fetch_yahoo_financials(ticker):
    """Best-effort Yahoo read. Returns a payload or None. Never invents a row."""
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        quarterly = _statement(ticker_obj.quarterly_income_stmt)
        annual = _statement(getattr(ticker_obj, "income_stmt", None) or ticker_obj.financials)
        info = {}
        try:
            raw = ticker_obj.info or {}
            info = {key: raw.get(key) for key in ("earningsGrowth", "revenueGrowth", "trailingPE")}
        except Exception:  # noqa: BLE001
            info = {}
    except Exception:  # noqa: BLE001
        return None
    if not quarterly and not annual and not any(info.values()):
        return None
    return {
        "quarterly": quarterly,
        "annual": annual,
        "info": info,
        "source": "Yahoo Finance",
        "url": YAHOO.format(ticker=ticker),
    }


def _statement(frame):
    if frame is None or getattr(frame, "empty", True):
        return []
    rows = []
    for col in frame.columns:
        def cell(names, column=col):
            for name in names:
                if name in frame.index:
                    try:
                        return float(frame.loc[name, column])
                    except (TypeError, ValueError):
                        return None
            return None
        period = getattr(col, "date", lambda: col)()
        rows.append({
            "period": str(period)[:10],
            "revenue": cell(("Total Revenue", "Operating Revenue")),
            "profit": cell(("Net Income", "Net Income Common Stockholders")),
        })
    return rows


def collect(kind, levels, articles, now, fetchers=None):
    """Attach story facts onto a copy of the pieces the script is allowed to say.

    `fetchers` replaces the network: financials(ticker), flows(), breadth(), sectors().
    """
    fetchers = fetchers or {}
    ticker = str(levels.get("yf") or "")
    story = {"sample": bool(levels.get("sample"))}

    def safe(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            return None

    financials = safe(fetchers.get("financials", fetch_yahoo_financials), ticker) if ticker else None
    if isinstance(financials, dict) and "quarterly" in financials:
        financials = fundamentals_from_payload(financials)
    if financials:
        story["fundamentals"] = financials
    flows = safe(fetchers.get("flows", fetch_fii_dii))
    if flows:
        story["flows"] = flows
    if kind == "close":
        breadth = safe(fetchers.get("breadth", fetch_breadth))
        if breadth:
            story["breadth"] = breadth
        sectors = safe(fetchers.get("sectors", fetch_sectors))
        if sectors:
            story["sectors"] = sectors
    story["reversal"] = detect_reversal(levels.get("bars"))
    story["plan"] = example_plan(levels.get("bars"))
    story["news"] = _pick_news(articles, now, levels, kind)
    return story


def _pick_news(articles, now, levels, kind):
    from realchart.news import choose
    name = str(levels.get("symbol") or "").split(":")[-1]
    aliases = [name] if kind == "ta" and name and name != "SAMPLE" else ["nifty", "rbi", "sebi", "gst"]
    govt = ("rbi", "sebi", "gst", "pli", "budget", "tariff", "government", "ministry", "policy")
    ranked = []
    for item in articles or []:
        blob = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
        if any(word in blob for word in aliases) or any(word in blob for word in govt):
            ranked.append(item)
    picked = choose(ranked or articles or [], now, aliases if kind == "ta" else ())
    if not picked:
        return None
    return {
        "title": str(picked.get("title") or "").strip(),
        "source": str(picked.get("source") or "").replace("www.", ""),
        "url": str(picked.get("url") or ""),
        "published": str(picked.get("published") or ""),
        "summary": str(picked.get("summary") or ""),
    }


def build_figures(levels, story, source_note=""):
    """Flat list of every number the Reel may show, with where it came from."""
    note = source_note or ("sample fixture, not a live market read" if levels.get("sample") else "")
    figures = []

    def add(name, value, source, url="", unit="", as_of=""):
        if value is None:
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        figures.append({
            "name": name,
            "value": number,
            "display": number,
            "unit": unit,
            "source": source,
            "url": url or "",
            "as_of": as_of or "",
            "note": note,
        })

    add("close", levels.get("close"), levels.get("values_from") or "OHLC", unit="INR", as_of=str(levels.get("last_date") or "")[:10])
    add("open", levels.get("open"), "OHLC", unit="INR", as_of=str(levels.get("last_date") or "")[:10])
    add("high", levels.get("high"), "OHLC", unit="INR", as_of=str(levels.get("last_date") or "")[:10])
    add("low", levels.get("low"), "OHLC", unit="INR", as_of=str(levels.get("last_date") or "")[:10])
    add("prev_close", levels.get("prev_close"), "OHLC", unit="INR")
    if levels.get("chg_pct") is not None:
        add("chg_pct", abs(float(levels["chg_pct"])), "OHLC", unit="percent")
    if levels.get("close") is not None and levels.get("prev_close") is not None:
        add("points", abs(float(levels["close"]) - float(levels["prev_close"])), "OHLC", unit="points")
    reversal = (story or {}).get("reversal") or {}
    add("reversal_low", reversal.get("low"), reversal.get("source") or "computed from daily OHLC", unit="INR", as_of=reversal.get("date") or "")
    add("reversal_high", reversal.get("high"), reversal.get("source") or "computed from daily OHLC", unit="INR", as_of=reversal.get("date") or "")
    fundamentals = (story or {}).get("fundamentals") or {}
    src, url, as_of = fundamentals.get("source") or "", fundamentals.get("url") or "", fundamentals.get("as_of") or ""
    for name, unit in (
        ("profit_yoy_q", "percent"), ("revenue_yoy_q", "percent"),
        ("profit_yoy_y", "percent"), ("revenue_yoy_y", "percent"),
        ("future_growth", "percent"), ("revenue_growth_fwd", "percent"), ("pe", "times"),
    ):
        add(name, fundamentals.get(name), src or "fundamentals", url, unit, as_of)
    flows = (story or {}).get("flows") or {}
    add("fii_net_cr", flows.get("fii_net_cr"), flows.get("source") or "NSE FII/DII", flows.get("url") or "", "crore", flows.get("date") or "")
    add("dii_net_cr", flows.get("dii_net_cr"), flows.get("source") or "NSE FII/DII", flows.get("url") or "", "crore", flows.get("date") or "")
    breadth = (story or {}).get("breadth") or {}
    add("advances", breadth.get("advances"), breadth.get("source") or "NSE breadth", breadth.get("url") or "", "count")
    add("declines", breadth.get("declines"), breadth.get("source") or "NSE breadth", breadth.get("url") or "", "count")
    sectors = (story or {}).get("sectors") or {}
    if sectors.get("best"):
        add("sector_best_pct", sectors["best"].get("pct"), sectors.get("source") or "NSE sectors", sectors.get("url") or "", "percent")
    if sectors.get("worst"):
        add("sector_worst_pct", sectors["worst"].get("pct"), sectors.get("source") or "NSE sectors", sectors.get("url") or "", "percent")
    plan = (story or {}).get("plan") or {}
    add("plan_stop", plan.get("stop"), plan.get("source") or "computed from daily OHLC", unit="INR")
    add("plan_target", plan.get("target"), plan.get("source") or "computed from daily OHLC", unit="INR")
    try:
        from realchart.script import next_pivot
        pivot = next_pivot(levels)
    except Exception:  # noqa: BLE001
        pivot = None
    add("next_pivot", pivot, "classic pivot from today's high, low and close", unit="INR")
    if pivot is not None and levels.get("low") is not None and levels.get("high") is not None:
        add("next_r1", 2 * pivot - float(levels["low"]), "classic R1 from today's high, low and close", unit="INR")
        add("next_s1", 2 * pivot - float(levels["high"]), "classic S1 from today's high, low and close", unit="INR")
    return figures
