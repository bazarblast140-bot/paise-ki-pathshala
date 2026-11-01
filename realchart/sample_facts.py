"""Bundled numbers for a no-network sample render.

The candle dates follow the session so the chart is a real OHLC shape, but
the results, flows and news are fixture values. sources.json marks them as
a sample fixture, not a live market read.
"""
from __future__ import annotations

from datetime import timedelta

from realchart.levels import compute
from realchart.story_facts import BREADTH_URL, FII_URL, SECTOR_URL, YAHOO, build_figures, detect_reversal, example_plan


def _days(session, count):
    cursor = session
    found = []
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor)
        cursor -= timedelta(days=1)
    found.reverse()
    return found


def _bars(session, start, step):
    rows = []
    price = float(start)
    days = _days(session, 70)
    for index, day in enumerate(days):
        drift = step if index < 45 else -abs(step)
        o = price
        c = price + drift
        h = max(o, c) + abs(step)
        l = min(o, c) - abs(step)
        if index == len(days) - 1 and step < 5:
            # Hammer after the decline: long lower wick, close near the high.
            o, c = price, price + abs(step)
            h, l = c + abs(step) * 0.4, o - abs(step) * 6
        rows.append({
            "date": day.isoformat(),
            "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2),
        })
        price = rows[-1]["c"]
    return rows


def _news(kind):
    if kind == "ta":
        title = "RBI policy review keeps the rate unchanged"
        url = "https://www.livemint.com/market/sample-rbi-policy"
    else:
        title = "RBI policy review keeps the rate unchanged"
        url = "https://www.livemint.com/market/sample-nifty-policy"
    return {
        "title": title,
        "url": url,
        "summary": "The policy rate was left unchanged in the sample note.",
        "published": "Fri, 30 Oct 2026 18:00:00 +0530",
        "source": "livemint.com",
    }


def build_sample(kind, session, out):
    if kind == "close":
        bars = _bars(session, 25040, -18)
        symbol, ticker = "NSE:NIFTY", "^NSEI"
    else:
        bars = _bars(session, 210, -1.4)
        symbol, ticker = "NSE:SAMPLE", "SAMPLE.NS"
    levels = compute(bars, symbol, ticker)
    levels["bars"] = bars
    levels["format"] = "story"
    levels["sample"] = True
    levels["values_from"] = "sample fixture"
    news = _news(kind)
    story = {
        "sample": True,
        "reversal": detect_reversal(bars),
        "plan": example_plan(bars),
        "fundamentals": {
            "profit_yoy_q": 18.5,
            "revenue_yoy_q": 11.2,
            "profit_yoy_y": 15.0,
            "revenue_yoy_y": 9.4,
            "future_growth": 8.0,
            "pe": 21.4,
            "source": "sample fixture",
            "url": YAHOO.format(ticker=ticker),
            "as_of": str(session),
        },
        "flows": {
            "fii_net_cr": -2450.0,
            "dii_net_cr": 1980.0,
            "date": session.strftime("%d-%b-%Y"),
            "source": "sample fixture",
            "url": FII_URL,
        },
        "news": {
            "title": news["title"],
            "source": news["source"],
            "url": news["url"],
            "published": news["published"],
            "summary": news["summary"],
        },
    }
    if kind == "close":
        story["breadth"] = {
            "advances": 18, "declines": 32, "unchanged": 0,
            "source": "sample fixture", "url": BREADTH_URL,
        }
        story["sectors"] = {
            "best": {"name": "NIFTY IT", "pct": 1.6},
            "worst": {"name": "NIFTY BANK", "pct": -1.1},
            "source": "sample fixture", "url": SECTOR_URL,
        }
    levels["story"] = story
    levels["story_figures"] = build_figures(levels, story)
    levels["news_text"] = f"{news['title']} {news['summary']}"
    chart = {
        "path": str(out / "chart.png"),
        "source": "ohlc",
        "label": "OHLC candles (sample fixture)",
        "url": "fixture://realchart/sample_facts.py",
        "last_candle_date": session.isoformat(),
        "reason": "sample fixture, not a live market read",
    }
    return levels, news, chart, None
