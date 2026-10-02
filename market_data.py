"""Real market series for Shorts charts.

Points come from the checked-in Yahoo Finance monthly file, or from a live
yfinance download when MARKET_DATA_LIVE=1. Missing closes are dropped, never
filled. A named stock price newer than three months is not shown. If nothing
real is available, the caller draws a non-numeric card instead of a fake line.
"""
import json
import os
import pathlib
from datetime import date

import board_kit as K

ROOT = pathlib.Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "index_monthly.json"

INDEX_NAMES = {"NIFTY 50", "SENSEX", "NIFTY BANK"}
SOURCE_NAME = "Yahoo Finance"

# topic key -> series names stored in data/index_monthly.json
TOPIC_SERIES = {
    "nifty_2010": ["NIFTY 50"],
    "nifty_2015": ["NIFTY 50"],
    "nifty_cagr": ["NIFTY 50"],
    "nifty_base": ["NIFTY 50"],
    "sensex_2008": ["SENSEX"],
    "niftybank_2010": ["NIFTY BANK"],
    "hdfcbank_2010": ["HDFC BANK"],
    "titan_2010": ["TITAN"],
    "asianpaints_2010": ["ASIAN PAINTS"],
    "infosys_2015": ["INFOSYS"],
    "sbi_2015": ["SBI"],
    "tcs_2020": ["TCS"],
    "dmart_2020": ["DMART"],
    "yesbank_2015": ["YES BANK"],
    "titan_vs_yes_2015": ["TITAN", "YES BANK"],
}

# Same year the chart scene passes to data_line / stock_line.
TOPIC_YEAR = {
    "nifty_2010": 2010,
    "nifty_2015": 2015,
    "nifty_cagr": 2010,
    "sensex_2008": 2008,
    "niftybank_2010": 2010,
    "hdfcbank_2010": 2010,
    "titan_2010": 2010,
    "asianpaints_2010": 2010,
    "infosys_2015": 2015,
    "sbi_2015": 2015,
    "tcs_2020": 2020,
    "dmart_2020": 2020,
    "yesbank_2015": 2015,
}

_CACHE = None

NON_NUMERIC_LABELS = ("NO LIVE PRICE", "EDUCATIONAL CARD", "DATA NOT SHOWN")


def months_before(day, months=3):
    """Calendar date `months` before `day` (day-of-month clamped)."""
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    import calendar
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def parse_point_date(value):
    text = str(value)[:10]
    if len(text) == 7:
        year, month = text.split("-")
        return date(int(year), int(month), 1)
    return date.fromisoformat(text)


def stock_price_is_old_enough(as_of, today=None, months=3):
    """True when a named stock print is at least `months` old."""
    today = today or date.today()
    if isinstance(as_of, str):
        as_of = parse_point_date(as_of)
    return as_of <= months_before(today, months)


def points_from_rows(rows):
    """Keep only real (date, close) pairs. None and blanks are gaps, not fills."""
    out = []
    for row in rows or []:
        if not row or row[0] is None or row[1] is None:
            continue
        try:
            close = float(row[1])
        except (TypeError, ValueError):
            continue
        out.append({"date": str(row[0])[:10], "close": close})
    return out


def values_are_fabricated(shown, source):
    """True if any shown point was not in the source series (invented or interpolated)."""
    src = set()
    for point in source or []:
        src.add((str(point["date"])[:10], round(float(point["close"]), 4)))
    for point in shown or []:
        key = (str(point["date"])[:10], round(float(point["close"]), 4))
        if key not in src:
            return True
    return False


def load_cache(path=None):
    global _CACHE
    if path is not None:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if _CACHE is None:
        _CACHE = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return _CACHE


def series_from_cache(name, cache):
    """Monthly closes already stored from Yahoo Finance. No new points are added."""
    blob = (cache or {}).get("series", {}).get(name)
    if not blob:
        return None
    monthly = points_from_rows(blob.get("monthly") or [])
    last = blob.get("last") or {}
    as_of = str(last.get("date") or (monthly[-1]["date"] if monthly else ""))
    return {
        "name": name,
        "ticker": blob.get("ticker"),
        "points": monthly,
        "as_of": as_of,
        "source": SOURCE_NAME,
        "kind": "index" if name in INDEX_NAMES else "stock",
    }


def fetch_yahoo_monthly(ticker, start="2005-01-01", end=None):
    """Live monthly closes via yfinance. Returns None if the fetch does not work.

    Adjusted rows with a missing close are dropped. This function does not
    call interpolate() or fillna().
    """
    if os.environ.get("MARKET_DATA_LIVE", "").strip() not in ("1", "true", "yes", "on"):
        return None
    try:
        import yfinance as yf
    except ImportError:
        print("[market_data] yfinance is not installed; using the cached Yahoo file")
        return None
    end = end or date.today().isoformat()
    try:
        frame = yf.download(
            ticker, start=start, end=end, interval="1mo",
            progress=False, auto_adjust=False, threads=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[market_data] Yahoo fetch failed for {ticker}: {type(exc).__name__}")
        return None
    if frame is None or getattr(frame, "empty", True):
        return None
    close = frame["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    rows = []
    for stamp, value in close.items():
        try:
            if value != value:  # NaN
                continue
        except Exception:  # noqa: BLE001
            continue
        rows.append((str(stamp)[:10], float(value)))
    points = points_from_rows(rows)
    if len(points) < 2:
        return None
    return points


def _stock_series_allowed(kind, points, as_of, today):
    """Indexes are not a single stock. A named stock's latest print must be >= 3 months old.

    The chart scene labels that latest print, so a too-recent series is refused
    entirely instead of being trimmed into a made-up shape.
    """
    if kind != "stock":
        return True, None
    stamp = as_of or (points[-1]["date"] if points else None)
    if not stamp or not stock_price_is_old_enough(stamp, today):
        return False, "named stock price is newer than 3 months"
    return True, None


def _non_numeric(reason):
    return {
        "mode": "non_numeric",
        "source": None,
        "as_of": None,
        "series": [],
        "reason": reason,
        "fabricated": False,
        "has_digits": False,
        "labels": list(NON_NUMERIC_LABELS),
    }


def atr_lesson(cache=None, today=None, name="HDFC BANK", period=14, multiple=1.5):
    """ATR from real monthly closes. The printed ATR is the figure the chart uses.

    Distance is 1.5 times that printed ATR, not a second rounding of the raw mean.
    Candles later use these closes only: body from close to close, no invented wick.
    Returns None when the series is missing or the stock print is too recent.
    """
    today = today or date.today()
    if cache is None:
        try:
            cache = load_cache()
        except (OSError, ValueError):
            return None
    series = series_from_cache(name, cache)
    if not series or len(series["points"]) < period + 1:
        return None
    ok, _err = _stock_series_allowed("stock", series["points"], series["as_of"], today)
    if not ok:
        return None
    points = series["points"][-(period + 1):]
    closes = [float(p["close"]) for p in points]
    changes = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    atr_text = f"{(sum(changes) / len(changes)):.1f}"
    atr = float(atr_text)
    dist_text = f"{atr * float(multiple):.1f}"
    dist = float(dist_text)
    entry_text = f"{closes[-1]:.1f}"
    entry = float(entry_text)
    return {
        "name": name,
        "name_hi": "HDFC बैंक" if name == "HDFC BANK" else name,
        "source": SOURCE_NAME,
        "as_of": series["as_of"],
        "period": int(period),
        "multiple": f"{float(multiple):.1f}",
        "atr": atr_text,
        "distance": dist_text,
        "entry": entry_text,
        "sl": f"{entry - dist:.1f}",
        "target": f"{entry + dist:.1f}",
        "points": points,
    }


def apply_atr_card(topic, lesson):
    """Put the chart's own figures on the topic card so the script may speak them."""
    topic["_atr"] = lesson
    topic["topic"] = f"ATR से SL की दूरी — {lesson['name']} ATR {lesson['atr']}"
    topic["thumb"] = ["तंग SL बार-बार कटता?", f"ATR {lesson['atr']} से दूरी"]
    topic["facts"] = [
        f"ATR {lesson['period']} candles की औसत हलचल",
        f"ATR {lesson['atr']} और {lesson['multiple']} गुना = {lesson['distance']} पॉइंट",
        f"entry {lesson['entry']}, SL {lesson['sl']}, ऊपर रेखा {lesson['target']}",
        f"SOURCE {lesson['source']} as of {lesson['as_of']}",
        "ज़्यादा दूरी = कम मात्रा, जोखिम वही",
        "ATR दिशा नहीं बताता, सिर्फ़ हलचल",
        "ये शिक्षा है, किसी शेयर की खरीद-बिक्री सलाह नहीं",
    ]
    topic["beats"] = {
        "hook": ["तंग SL क्यों कटता है?", "सामान्य हलचल से तंग रेखा कटती है"],
        "pehchan": ["नियम", f"{lesson['period']} महीने की औसत = ATR {lesson['atr']}"],
        "matlab": ["हिसाब", f"{lesson['multiple']} × {lesson['atr']} = {lesson['distance']} पॉइंट"],
        "karein": ["गलती", "दूरी बढ़े तो मात्रा घटाओ"],
    }
    return topic


def spec_for_topic(topic, today=None, fetcher=None, cache=None):
    """Chart contract for one topic.

    mode 'series'     real points, source and as_of set, fabricated False
    mode 'schematic'  a teaching diagram (candle, RSI), not a market print
    mode 'non_numeric' data missing or too recent — draw no prices
    """
    today = today or date.today()
    key = str((topic or {}).get("key") or "")
    if key == "atr_sl":
        if cache is None:
            try:
                cache = load_cache()
            except (OSError, ValueError) as exc:
                cache = {"series": {}}
                print(f"[market_data] cache unreadable: {exc}")
        lesson = atr_lesson(cache=cache, today=today)
        full = series_from_cache("HDFC BANK", cache) if lesson else None
        if not lesson or not full or values_are_fabricated(lesson["points"], full["points"]):
            return _non_numeric("ATR lesson has no real monthly closes old enough to show")
        return {
            "mode": "series",
            "source": lesson["source"],
            "as_of": lesson["as_of"],
            "series": [{"name": lesson["name"], "points": list(lesson["points"]), "kind": "stock"}],
            "reason": None,
            "fabricated": False,
            "has_digits": False,
            "labels": [],
            "atr": lesson,
        }
    names = TOPIC_SERIES.get(key)
    if not names:
        if (topic or {}).get("cat") == "REAL DATA":
            return _non_numeric("no fetched series for this REAL DATA topic")
        return {
            "mode": "schematic", "source": None, "as_of": None, "series": [],
            "reason": None, "fabricated": False, "has_digits": False, "labels": [],
        }

    if cache is None:
        try:
            cache = load_cache()
        except (OSError, ValueError) as exc:
            cache = {"series": {}}
            print(f"[market_data] cache unreadable: {exc}")

    built = []
    as_ofs = []
    for name in names:
        cached = series_from_cache(name, cache)
        points = cached["points"] if cached else None
        source = SOURCE_NAME
        kind = "index" if name in INDEX_NAMES else "stock"
        as_of = cached["as_of"] if cached else None
        if not points and fetcher is not None:
            try:
                live = fetcher(name)
            except Exception as exc:  # noqa: BLE001
                print(f"[market_data] fetch failed for {name}: {type(exc).__name__}")
                live = None
            if live:
                points = points_from_rows([(p["date"], p["close"]) for p in live])
                as_of = points[-1]["date"]
                source = SOURCE_NAME
        elif not points and fetcher is None:
            ticker = (cached or {}).get("ticker")
            if ticker:
                live = fetch_yahoo_monthly(ticker)
                if live:
                    points = live
                    as_of = points[-1]["date"]
        if not points or len(points) < 2:
            return _non_numeric(f"no real data for {name}")
        ok, err = _stock_series_allowed(kind, points, as_of, today)
        if not ok:
            return _non_numeric(err)
        if values_are_fabricated(points, points):
            return _non_numeric(f"refusing fabricated points for {name}")
        built.append({"name": name, "points": points, "kind": kind})
        as_ofs.append(as_of or points[-1]["date"])

    return {
        "mode": "series",
        "source": SOURCE_NAME,
        "as_of": min(as_ofs),
        "series": built,
        "reason": None,
        "fabricated": False,
        "has_digits": False,
        "labels": [],
    }


def visual_mode(topic, spec=None, today=None):
    spec = spec if spec is not None else spec_for_topic(topic, today=today)
    if spec.get("mode") == "series":
        return "series"
    if spec.get("mode") == "non_numeric" or (topic or {}).get("cat") == "REAL DATA":
        return "non_numeric"
    return "schematic"


def format_growth(start_close, end_close):
    """The numbers the chart prints, from the raw start and end closes.

    A start under 1,000 keeps two decimals. 71.07 → 4,231 is 59.53x.
    Rounding the start to 71 first would print 59.59x, which is a different sum.
    """
    start_close = float(start_close)
    end_close = float(end_close)
    multiple = end_close / start_close
    start_text = f"{start_close:,.2f}" if start_close < 1000 else f"{start_close:,.0f}"
    end_text = f"{end_close:,.0f}"
    multiple_text = f"{multiple:.2f}"
    return {
        "start_close": start_close,
        "end_close": end_close,
        "multiple": multiple,
        "start_text": start_text,
        "end_text": end_text,
        "multiple_text": multiple_text,
        "lakh_text": f"{multiple:.1f}",
        "loss_pct": int(round((1 - multiple) * 100)),
        "result_rupees": f"{round(multiple * 100000, -3):,.0f}",
        "equation": f"{end_text}  /  {start_text}",
        "multiple_label": f"= {multiple_text}x",
        "result_label": (
            f"Rs {round(multiple * 100000, -3):,.0f}" if multiple < 1
            else f"Rs {multiple:.1f} LAKH"
        ),
    }


def growth_figures(name, year, cache=None):
    """Start, end, and multiple for one series, using the chart's year and last close."""
    cache = load_cache() if cache is None else cache
    series = (cache.get("series") or {}).get(name) or {}
    start = (series.get("first_of_year") or {}).get(str(year)) or {}
    last = series.get("last") or {}
    figs = format_growth(start["close"], last["close"])
    figs["start_date"] = str(start.get("date") or "")[:10]
    figs["end_date"] = str(last.get("date") or "")[:10]
    figs["name"] = name
    figs["year"] = int(year)
    return figs


def sync_topic_numbers(topic, cache=None):
    """Rewrite the on-screen and fact-ledger figures from the chart's closes.

    The hook line is left alone, so a REAL DATA question still does not reveal
    the answer. Returns the same topic dict.
    """
    import re
    key = str((topic or {}).get("key") or "")
    names = TOPIC_SERIES.get(key)
    year = TOPIC_YEAR.get(key)
    if not names or len(names) != 1 or not year:
        return topic
    try:
        figs = growth_figures(names[0], year, cache)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return topic
    span = re.compile(r"\d[\d,]*(?:\.\d+)?\s*से\s*\d[\d,]*(?:\.\d+)?")
    guna = re.compile(r"\d[\d,]*(?:\.\d+)?(?=\s*गुना)")
    result_lakh = re.compile(r"(करीब\s*)₹\s*\d[\d,]*(?:\.\d+)?\s*लाख")
    result_rupee = re.compile(r"(करीब\s*)₹\s*\d[\d,]*(?:\.\d+)?(?!\s*लाख)")
    date_price = re.compile(r"^(\d{4}-\d{2}-\d{2}) को \d[\d,]*(?:\.\d+)?")

    def rewrite(text, allow_result=True):
        text = span.sub(f"{figs['start_text']} से {figs['end_text']}", str(text), count=1)
        if figs["multiple"] >= 1 and "गुना" in text:
            text = guna.sub(figs["multiple_text"], text, count=1)
        if allow_result and figs["multiple"] >= 1 and "लाख" in text:
            text = result_lakh.sub(rf"\1₹{figs['lakh_text']} लाख", text, count=1)
        if allow_result and figs["multiple"] < 1 and "नुकसान" in text:
            text = re.sub(r"\d+(?=\s*%)", str(figs["loss_pct"]), text, count=1)
        if allow_result and figs["multiple"] < 1 and "₹" in text:
            text = result_rupee.sub(rf"\1₹{figs['result_rupees']}", text, count=1)
        return text

    beats = topic.get("beats") or {}
    for beat, pair in list(beats.items()):
        if beat == "hook" or not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        beats[beat] = [str(pair[0]), rewrite(pair[1])]
    facts = []
    for fact in topic.get("facts") or []:
        text = str(fact)
        matched = date_price.match(text)
        if matched:
            which = figs["start_text"] if matched.group(1) == figs["start_date"] else None
            if matched.group(1) == figs["end_date"]:
                which = figs["end_text"]
            if which:
                text = date_price.sub(rf"{matched.group(1)} को {which}", text, count=1)
        facts.append(rewrite(text))
    topic["facts"] = facts
    return topic


def provenance_line(spec):
    source = (spec or {}).get("source") or ""
    as_of = (spec or {}).get("as_of") or ""
    if not source or not as_of:
        return ""
    return f"SOURCE: {source} | AS OF {as_of}"


def stamp(img, spec):
    """Draw the source and as-of date on the chart card. Latin text only."""
    line = provenance_line(spec)
    if not line:
        return img
    out = img.copy()
    d = K.ImageDraw.Draw(out)
    d.rectangle([40, 1044, 1040, 1108], fill=(6, 10, 20))
    d.text((56, 1056), line, fill=K.YELLOW, font=K.font(26))
    return out


def non_numeric_frame(topic, beat, variant=0):
    """Educational card with shapes and words. No prices and no digits."""
    img = K.background()
    K.panel(img)
    K.label(img, 540, 280, NON_NUMERIC_LABELS[0], K.YELLOW, 52, anchor="ma")
    K.label(img, 540, 380, NON_NUMERIC_LABELS[1], K.INK, 36, anchor="ma")
    d = K.ImageDraw.Draw(img)
    colors = [(96, 165, 250), (250, 204, 21), (34, 197, 94)]
    for i, col in enumerate(colors):
        height = 160 + int(variant) * 36 + i * 28
        x = 180 + i * 240
        d.rounded_rectangle([x, 900 - height, x + 150, 900], 18, fill=col)
    K.label(img, 80, 980, NON_NUMERIC_LABELS[2], K.MUTED, 32)
    # beat name is Latin and has no digits (hook, pehchan, matlab, karein).
    K.label(img, 80, 1040, str(beat or "").upper()[:12], K.INK, 28)
    return img
