"""Daily Reels: real articles, real levels, dry runs that do not spend Gemini."""
import re
import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import nifty_day
from realchart import capture, script
from realchart.build import _on_session, run
from realchart.levels import (
    apply_screen_levels, compute, floor_pivots, option_chain_url, parse_option_chain, pick_setup, screen_zones,
)
from realchart.news import choose
from realchart.session import close_matches, dry_session, expected_session, started

ROOT = Path(__file__).resolve().parent.parent
IST = nifty_day.IST
FRIDAY = datetime(2026, 10, 9, 12, 0, tzinfo=IST)
LABEL = "Chart: NSE data (yfinance)"


def _bars(n=40, base=22000.0, end=None):
    end = end or datetime(2026, 10, 8, tzinfo=IST).date()
    rows = []
    day = end
    while len(rows) < n:
        if day.weekday() < 5 and nifty_day.is_trading_day(day):
            rows.append(day)
        day -= timedelta(days=1)
    rows.reverse()
    made = []
    price = base
    for index, day in enumerate(rows):
        close = price + (-30 if index % 5 == 0 else 12)
        made.append({
            "date": day.isoformat(),
            "o": price,
            "h": max(price, close) + 40,
            "l": min(price, close) - 35,
            "c": close,
        })
        price = close
    return made


def _stamp(day):
    return datetime(day.year, day.month, day.day, 15, 30, tzinfo=IST)


def _bars_that_clear_rr():
    """Sit the close just above S1 so the pivot walk clears RR 1.5 without invented OI."""
    bars = _bars()
    s1 = floor_pivots(bars[-2])["s1"]
    last = dict(bars[-1])
    last["c"] = round(s1 + 8, 2)
    last["o"] = last["c"]
    last["h"] = max(float(last["h"]), last["c"] + 5)
    last["l"] = min(float(last["l"]), last["c"] - 5)
    bars[-1] = last
    return bars


def _held_chain(_symbol=None):
    return {"ok": False, "reason": "NSE option chain held: test"}


def _intraday_bars(_ticker, _interval):
    return [
        {"date": "2026-10-08", "o": 22100, "h": 22120, "l": 22090, "c": 22110},
        {"date": "2026-10-08", "o": 22110, "h": 22130, "l": 22100, "c": 22115},
    ]


def _color_clip(dest, seconds, size="640x360"):
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x2244aa:s={size}:r=30:d={seconds}",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(dest),
    ], check=True)


class SessionTests(unittest.TestCase):
    def test_live_waits_and_dry_run_still_has_a_session(self):
        self.assertFalse(started("news", FRIDAY, dry_run=False))
        self.assertFalse(started("close", FRIDAY, dry_run=False))
        self.assertTrue(started("recap", datetime(2026, 10, 10, 11, 0, tzinfo=IST)))
        self.assertTrue(started("news", FRIDAY, dry_run=True))
        self.assertEqual(expected_session("news", FRIDAY), FRIDAY.date())
        self.assertIsNone(expected_session("recap", FRIDAY))
        self.assertEqual(dry_session("close", FRIDAY), datetime(2026, 10, 8, tzinfo=IST).date())
        self.assertEqual(dry_session("recap", FRIDAY), datetime(2026, 9, 25, tzinfo=IST).date())
        session = datetime(2026, 10, 8).date()
        self.assertTrue(close_matches(_stamp(session), session, FRIDAY))

    def test_a_dry_run_drops_a_later_partial_bar(self):
        thursday = datetime(2026, 10, 8).date()
        bars = _bars(end=thursday)
        bars.append({"date": "2026-10-09", "o": 1, "h": 2, "l": 1, "c": 2})
        kept, stamp = _on_session(bars, datetime(2026, 10, 9, 5, 30, tzinfo=IST), thursday, FRIDAY, True)
        self.assertEqual(str(kept[-1]["date"]), "2026-10-08")
        self.assertTrue(close_matches(stamp, thursday, FRIDAY))
        self.assertIsNone(_on_session(bars, datetime(2026, 10, 9, 5, 30, tzinfo=IST), thursday, FRIDAY, False)[0])

    def test_a_morning_bar_is_not_the_close(self):
        morning = datetime(2026, 10, 8, 5, 30, tzinfo=IST)
        self.assertFalse(close_matches(morning, datetime(2026, 10, 8).date(), FRIDAY.replace(day=8)))
        self.assertTrue(close_matches(morning, datetime(2026, 10, 8).date(), FRIDAY))


class LevelTests(unittest.TestCase):
    def test_support_is_the_prior_low_and_zones_come_from_that(self):
        bars = _bars()
        levels = compute(bars, "NSE:NIFTY", "^NSEI")
        prior = bars[:-1]
        support = min(float(row["l"]) for row in prior[-252:])
        self.assertEqual(levels["support"], support)
        self.assertEqual(levels["sl_zone"][1], min(support, float(bars[-1]["l"])))
        self.assertEqual(levels["sl_zone"][0] % 10, 0)
        self.assertLessEqual(levels["sl_zone"][0], support)
        self.assertEqual(len(levels["target_zone"]), 2)
        self.assertFalse(any(math.isnan(levels[key]) for key in ("ema20", "ema50", "rsi14")))

    def test_the_same_symbol_and_setup_is_blocked_for_five_days(self):
        levels = compute(_bars(), "NSE:INFY", "INFY.NS")
        today = datetime(2026, 10, 9).date()
        first = pick_setup([levels], [], today)
        setup = first["setup"]
        blocked = [{"symbol": "NSE:INFY", "setup": setup, "date": "2026-10-08"}]
        self.assertIsNone(pick_setup([levels], blocked, today))
        fresh = [{"symbol": "NSE:INFY", "setup": setup, "date": "2026-10-03"}]
        picked = pick_setup([levels], fresh, today)
        self.assertEqual(picked["symbol"], "NSE:INFY")
        self.assertEqual(picked["setup"], setup)

    def test_floor_pivot_is_the_prior_day_ohlc(self):
        bar = {"date": "2026-10-13", "h": 100, "l": 90, "c": 97}
        piv = floor_pivots(bar)
        pivot = (100 + 90 + 97) / 3.0
        self.assertAlmostEqual(piv["pivot"], pivot)
        self.assertAlmostEqual(piv["r1"], 2 * pivot - 90)
        self.assertAlmostEqual(piv["s1"], 2 * pivot - 100)
        self.assertAlmostEqual(piv["r2"], pivot + (100 - 90))
        self.assertAlmostEqual(piv["s2"], pivot - (100 - 90))
        self.assertEqual(piv["pivot_date"], "2026-10-13")

    def test_option_chain_uses_max_and_next_oi_on_the_nearest_expiry(self):
        from datetime import date
        payload = {
            "records": {
                "expiryDates": ["09-Oct-2026", "16-Oct-2026", "23-Oct-2026"],
                "timestamp": "14-Oct-2026 15:30:00",
                "data": [
                    {"expiryDate": "09-Oct-2026", "strikePrice": 1,
                     "CE": {"openInterest": 99999}, "PE": {"openInterest": 99999}},
                    {"expiryDate": "16-Oct-2026", "strikePrice": 22600,
                     "CE": {"openInterest": 100}, "PE": {"openInterest": 50}},
                    {"expiryDate": "16-Oct-2026", "strikePrice": 22700,
                     "CE": {"openInterest": 300}, "PE": {"openInterest": 10}},
                    {"expiryDate": "16-Oct-2026", "strikePrice": 22500,
                     "CE": {"openInterest": 200}, "PE": {"openInterest": 400}},
                    {"expiryDate": "16-Oct-2026", "strikePrice": 22400,
                     "CE": {"openInterest": 10}, "PE": {"openInterest": 250}},
                    {"expiryDate": "23-Oct-2026", "strikePrice": 22800,
                     "CE": {"openInterest": 5}, "PE": {"openInterest": 6}},
                ],
            }
        }
        parsed = parse_option_chain(payload, date(2026, 10, 14))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["expiry"], "16-Oct-2026")
        self.assertEqual(parsed["as_of"], "15:30 IST")
        self.assertEqual(parsed["call_oi"], 22700)
        self.assertEqual(parsed["call_oi_next"], 22500)
        self.assertEqual(parsed["put_oi"], 22500)
        self.assertEqual(parsed["put_oi_next"], 22400)
        later = parse_option_chain(payload, date(2026, 10, 30))
        self.assertEqual(later["expiry"], "23-Oct-2026")
        self.assertEqual(later["call_oi"], 22800)
        self.assertIn("option-chain-indices?symbol=NIFTY", option_chain_url("NSE:NIFTY"))
        self.assertIn("option-chain-equities?symbol=INFY", option_chain_url("NSE:INFY"))
        empty = parse_option_chain({"records": {"expiryDates": ["16-Oct-2026"], "data": []}}, date(2026, 10, 14))
        self.assertFalse(empty["ok"])
        self.assertNotIn("call_oi", empty)

    def test_a_held_chain_keeps_pivots_and_does_not_invent_strikes(self):
        bars = _bars()
        levels = compute(bars, "NSE:NIFTY", "^NSEI")
        structure = list(levels["sl_zone"])
        apply_screen_levels(levels, _held_chain())
        for key in ("call_oi", "call_oi_next", "put_oi", "put_oi_next", "expiry"):
            self.assertNotIn(key, levels)
        self.assertEqual(levels["oi_status"], "held")
        self.assertEqual(levels["levels_chip"], "Levels: Pivot (prev day OHLC) only, NSE option chain held")
        self.assertEqual(levels["pivot_date"], bars[-2]["date"])
        self.assertEqual(levels["structure_sl_zone"], structure)
        self.assertFalse(levels["rr_ok"])

    def test_rr_walks_to_the_first_target_that_clears_one_and_a_half(self):
        bar = {"date": "2026-10-13", "h": 100, "l": 90, "c": 97}
        pivots = floor_pivots(bar)
        zones = screen_zones(97, pivots, None)
        self.assertIsNotNone(zones)
        self.assertGreaterEqual(zones["rr"], 1.5)
        self.assertAlmostEqual(zones["target"], pivots["r2"], places=4)
        self.assertLess((pivots["r1"] - 97) / (97 - pivots["s1"]), 1.5)
        self.assertIsNone(screen_zones(pivots["r2"] + 5, pivots, None))
        levels = compute(_bars_that_clear_rr(), "NSE:NIFTY", "^NSEI")
        chain = {
            "ok": True, "expiry": "16-Oct-2026", "as_of": "15:30 IST",
            "call_oi": 23000.0, "call_oi_next": 23100.0, "put_oi": 22000.0, "put_oi_next": 21900.0,
        }
        self.assertTrue(apply_screen_levels(levels, chain))
        self.assertGreaterEqual(levels["rr"], 1.5)
        self.assertEqual(
            levels["levels_chip"],
            "Levels: Pivot (prev day OHLC) + NSE option chain OI, 16-Oct-2026, 15:30 IST",
        )
        self.assertNotIn("IST IST", levels["levels_chip"])
        self.assertEqual(levels["oi_status"], "ok")


class NewsTests(unittest.TestCase):
    def test_only_a_real_dated_article_is_chosen(self):
        items = [
            {"title": "Weather is fine", "url": "https://example.com/a", "summary": "Sunny skies.",
             "published": "Thu, 08 Oct 2026 10:00:00 +0530", "source": "example.com"},
            {"title": "Nifty rally", "url": "http://example.com/b", "summary": "insecure",
             "published": "Thu, 08 Oct 2026 11:00:00 +0530", "source": "example.com"},
            {"title": "Sensex tomorrow", "url": "https://example.com/c", "summary": "market",
             "published": "Sat, 10 Oct 2026 09:00:00 +0530", "source": "example.com"},
            {"title": "Nifty hits a wall", "url": "https://www.livemint.com/markets/nifty",
             "summary": "The index closed lower.", "published": "", "source": "livemint.com"},
        ]
        self.assertIsNone(choose(items, FRIDAY, ["nifty"]))
        items.append({
            "title": "Nifty ends lower",
            "url": "https://www.livemint.com/markets/nifty-ends",
            "summary": "The index closed lower. More words.",
            "published": "Fri, 09 Oct 2026 08:10:00 +0530",
            "source": "livemint.com",
        })
        picked = choose(items, FRIDAY, ["nifty"])
        self.assertEqual(picked["url"], "https://www.livemint.com/markets/nifty-ends")
        self.assertIn("index closed lower", picked["key_sentence"])


class ScriptTests(unittest.TestCase):
    def test_hook_cta_and_numbers_stay_inside_the_gates(self):
        levels = compute(_bars())
        article = {
            "title": "Nifty hits 99,999 in a made-up line",
            "key_sentence": "Nifty hits 99,999.",
            "source": "Mint",
            "url": "https://www.livemint.com/markets/x",
            "published": "Fri, 09 Oct 2026 08:10:00 +0530",
        }
        rows = script.chunks_for("news", levels, article)
        self.assertEqual([row["sec"] for row in rows], ["hook", "chart", "news", "cta"])
        shown = " ".join(row["d"] for row in rows)
        self.assertIn(article["title"][:80], shown)
        self.assertIn(article["key_sentence"], shown)
        self.assertIn(article["source"], shown)
        self.assertIn(article["published"], shown)
        for token in (" SL ", " target ", " R1 ", " S1 ", " R2 ", " S2 "):
            self.assertNotIn(token, shown)
        self.assertEqual(rows[0]["start"], 0.0)
        self.assertEqual(rows[0]["end"], 3.0)
        self.assertEqual(script.lint(rows), [])
        self.assertEqual(script.number_faults(rows, levels), [])
        rows[2]["t"] = "हद पूँजी का हिसाब तय करो"
        self.assertTrue(any(fault.startswith("banned word") for fault in script.lint(rows)))
        bad = script.chunks_for("close", levels, None)
        bad[1]["d"] = "Close 99,999.00"
        self.assertTrue(script.number_faults(bad, levels))
        close_rows = script.chunks_for("close", levels, None)
        ta_rows = script.chunks_for("ta", levels, None)
        recap_rows = script.chunks_for("recap", levels, None)
        self.assertEqual([row["sec"] for row in close_rows], ["hook", "chart", "intraday", "view", "cta"])
        self.assertEqual([row["sec"] for row in ta_rows][:3], ["hook", "chart", "intraday"])
        self.assertNotIn("intraday", [row["sec"] for row in recap_rows])
        self.assertNotIn("intraday", [row["sec"] for row in rows])
        spoken = script.spoken_text(close_rows)
        self.assertIn(script.CTA, spoken)
        self.assertIn(script.DISCLAIMER, spoken)
        for phrase in script.FORBIDDEN_FOLLOW:
            self.assertNotIn(phrase.lower(), spoken.lower())
        self.assertIn("SL ", close_rows[-2]["d"])
        self.assertIn("target ", close_rows[-2]["d"])
        self.assertEqual(script.lint(close_rows), [])
        no_scenario = [dict(row) for row in close_rows]
        no_scenario[-2]["t"] = "बुल केस में टारगेट ज़ोन तक बाउंस है।"
        self.assertIn("scenario wording missing", script.lint(no_scenario))
        close_rows[-1]["d"] = "Paise Ki Pathshala follow करो"
        self.assertTrue(any(fault.startswith("channel follow line") for fault in script.lint(close_rows)))


class CaptureTests(unittest.TestCase):
    def test_a_bot_wall_is_labeled_as_nse_data(self):
        self.assertEqual(capture.FALLBACK_LABEL, LABEL)
        bars = _bars()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chart.png"
            shot = capture.capture_chart("NSE:NIFTY", dest, bars, opener=lambda _s, _d: {
                "ok": False, "reason": "captcha", "text": "unusual traffic",
            })
            self.assertEqual(shot["source"], "yfinance")
            self.assertEqual(shot["label"], LABEL)
            self.assertEqual(shot["reason"], "bot wall")
            self.assertNotEqual(shot["label"], "TradingView")
            self.assertTrue(dest.is_file())
            note = dest.with_name("chart-capture.txt")
            self.assertIn("reason: bot wall", note.read_text(encoding="utf-8"))
            self.assertIn("unusual traffic", note.read_text(encoding="utf-8"))

    def test_a_sign_in_link_is_not_a_bot_wall(self):
        self.assertFalse(capture.walled("Sign in to save your layout"))
        self.assertTrue(capture.walled("Please verify you are human"))
        browser = type("Browser", (), {"version": "156.0.8078.4"})()
        agent = capture._browser_user_agent(browser)
        self.assertIn("Chrome/156.0.8078.4", agent)
        self.assertNotIn("Headless", agent)

    def test_a_failed_capture_keeps_the_screenshot_and_the_error(self):
        from PIL import Image
        bars = _bars()

        def opener(_symbol, path):
            Image.new("RGB", (8, 8), (1, 2, 3)).save(path)
            return {
                "ok": False,
                "reason": "TimeoutError: navigation",
                "text": "net::ERR_CONNECTION_RESET",
                "path": str(path),
            }

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chart.png"
            shot = capture.capture_chart("NSE:NIFTY", dest, bars, opener=opener)
            self.assertEqual(shot["source"], "yfinance")
            self.assertEqual(shot["reason"], "TimeoutError: navigation")
            note = (Path(tmp) / "chart-capture.txt").read_text(encoding="utf-8")
            self.assertIn("TimeoutError: navigation", note)
            self.assertIn("ERR_CONNECTION_RESET", note)
            with Image.open(Path(tmp) / "chart-capture.png") as kept:
                self.assertEqual(kept.size, (8, 8))
            with Image.open(dest) as drawn:
                self.assertNotEqual(drawn.size, (8, 8))

    def test_a_blank_shot_is_not_a_painted_chart(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            blank = Path(tmp) / "blank.png"
            Image.new("RGB", (80, 80), (15, 15, 15)).save(blank)
            self.assertFalse(capture.chart_painted(blank))
            painted = Path(tmp) / "painted.png"
            image = Image.new("RGB", (80, 80), (15, 15, 15))
            for x in range(10, 30):
                for y in range(20, 60):
                    image.putpixel((x, y), (34, 181, 60))
            for x in range(40, 60):
                for y in range(10, 50):
                    image.putpixel((x, y), (240, 30, 50))
            image.save(painted)
            self.assertTrue(capture.chart_painted(painted))

    def test_cookie_accept_is_clicked_before_the_chart(self):
        clicked = {}

        class Button:
            def is_visible(self):
                return True

            def click(self, timeout=0):
                clicked["timeout"] = timeout

        class Locator:
            def __init__(self, button):
                self.button = button

            def count(self):
                return 0 if self.button is None else 1

            @property
            def first(self):
                return self.button

        class Page:
            def get_by_role(self, role, name):
                self.role = role
                return Locator(Button() if name == "Accept all" else None)

        self.assertEqual(capture._dismiss_cookies(Page()), "Accept all")
        self.assertEqual(clicked["timeout"], 2000)

    def test_technicals_page_values_replace_the_cross_check(self):
        text = (
            "Exponential Moving Average (20) 22,100.50 "
            "Exponential Moving Average (50) 22,400.25 "
            "Relative Strength Index (14) 41.20"
        )
        parsed = capture.parse_technicals(text)
        self.assertEqual(parsed["ema20"], 22100.50)
        self.assertEqual(parsed["rsi14"], 41.20)
        self.assertEqual(capture.parse_technicals("no numbers here"), {})

    def test_five_minute_failure_keeps_the_fifteen_minute_tradingview_chart(self):
        bars = _bars()

        def opener(_symbol, dest):
            if Path(dest).name.startswith("chart-5m"):
                return {"ok": False, "reason": "captcha", "text": "captcha"}
            return {"ok": True, "text": "candles", "path": str(dest)}

        with tempfile.TemporaryDirectory() as tmp:
            shot = capture.capture_intraday("NSE:NIFTY", tmp, bars, bars, opener=opener)
            self.assertEqual(shot["source"], "tradingview")
            self.assertEqual(shot["interval"], "15")
            self.assertTrue(str(shot["reason"]).startswith("5m:"))
            self.assertNotEqual(shot["label"], capture.FALLBACK_LABEL_5M)
            self.assertEqual(Path(shot["path"]).name, "chart-15m.png")
            note = (Path(tmp) / "chart-5m-capture.txt").read_text(encoding="utf-8")
            self.assertIn("captcha", note)

    def test_both_blocked_intraday_charts_use_the_five_minute_yfinance_label(self):
        bars = _bars()

        def opener(_symbol, _dest):
            return {"ok": False, "reason": "captcha", "text": "unusual traffic"}

        with tempfile.TemporaryDirectory() as tmp:
            shot = capture.capture_intraday("NSE:NIFTY", tmp, bars, bars, opener=opener)
            self.assertEqual(shot["source"], "yfinance")
            self.assertEqual(shot["interval"], "5")
            self.assertEqual(shot["label"], capture.FALLBACK_LABEL_5M)
            self.assertIn("5m:", shot["reason"])
            self.assertIn("15m:", shot["reason"])
            self.assertEqual(shot["plot"], capture.YF_PLOT)
            self.assertTrue((Path(tmp) / "chart-5m.png").is_file())
            self.assertTrue((Path(tmp) / "chart-15m-capture.txt").is_file())


class BuildTests(unittest.TestCase):
    def test_no_article_holds_and_does_not_invent_one(self):
        now = datetime(2026, 10, 13, 9, 0, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1"}):
                code = run("news", out, now=now, articles=[], bars_for=lambda _t: (_bars(), _stamp(now.date())))
            self.assertEqual(code, 2)
            qa = json.loads((out / "qa.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["verdict"], "HOLD")
            self.assertIn("no qualifying article", qa["faults"][0])
            self.assertFalse((out / "final.mp4").exists())

    def test_a_relative_out_dir_does_not_double_the_concat_path(self):
        from PIL import Image
        from realchart.render import assemble
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                cards = Path("run") / "cards"
                cards.mkdir(parents=True)
                image = Image.new("RGB", (32, 32), (10, 20, 30))
                paths = []
                for name in ("hook", "chart"):
                    image.save(cards / f"{name}.png")
                    paths.append(Path("run") / "cards" / f"{name}.png")
                dest = assemble(paths, Path("run") / "missing.wav", Path("run") / "final.mp4", seconds=1.0)
                listing = (Path("run") / "frames.txt").read_text(encoding="utf-8")
                self.assertTrue(dest.is_file())
                self.assertGreater(dest.stat().st_size, 500)
            finally:
                os.chdir(here)
        self.assertNotIn("run/run/", listing)
        for line in listing.splitlines():
            if not line.startswith("file "):
                continue
            quoted = line.split("'", 1)[1].rsplit("'", 1)[0]
            self.assertTrue(Path(quoted).is_absolute(), quoted)
            self.assertTrue(quoted.endswith(".png"))

    def test_rr_below_one_and_a_half_holds_before_voice(self):
        bars = _bars()
        stamp = _stamp(datetime(2026, 10, 8).date())

        def walled(_symbol, _dest):
            return {"ok": False, "reason": "captcha", "text": "captcha"}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1"}), \
                 mock.patch("gemini_voice.write_speech", side_effect=AssertionError("no gemini")):
                code = run(
                    "close", out, now=FRIDAY, dry_run=True,
                    bars_for=lambda _t: (bars, stamp), articles=[], chart_opener=walled,
                    bars_intraday=_intraday_bars, chain_for=_held_chain,
                )
            self.assertEqual(code, 2)
            self.assertIn("RR below 1.5", (out / "HOLD").read_text(encoding="utf-8"))
            self.assertFalse((out / "voice.wav").exists())
            self.assertFalse((out / "final.mp4").exists())

    def test_news_posts_the_article_when_rr_is_below_one_and_a_half(self):
        """News is the article and the chart. The same bars that hold a close Reel do not hold news."""
        bars = _bars()
        stamp = _stamp(datetime(2026, 10, 8).date())
        now = datetime(2026, 10, 15, 10, 2, tzinfo=IST)
        article = {
            "title": "Nifty ends lower after the open",
            "url": "https://www.livemint.com/markets/nifty-ends",
            "summary": "The index closed lower. Traders watched the cash session.",
            "published": "Thu, 15 Oct 2026 08:10:00 +0530",
            "source": "livemint.com",
        }
        screened = []

        def walled(_symbol, _dest):
            return {"ok": False, "reason": "captcha", "text": "captcha"}

        def shot(_url, dest):
            from PIL import Image
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (80, 80), (20, 20, 20)).save(dest)
            return {"ok": True, "path": str(dest)}

        def refused(_symbol):
            screened.append(_symbol)
            raise AssertionError("news must not screen pivot or OI levels")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1"}), \
                 mock.patch("gemini_voice.write_speech", side_effect=AssertionError("no gemini")):
                code = run(
                    "news", out, now=now, dry_run=True,
                    bars_for=lambda _t: (bars, stamp), articles=[article],
                    chart_opener=walled, article_opener=shot, chain_for=refused,
                )
            detail = (out / "qa.json").read_text(encoding="utf-8") if (out / "qa.json").is_file() else "no qa"
            self.assertEqual(code, 0, detail)
            self.assertFalse((out / "HOLD").exists())
            self.assertEqual(screened, [])
            qa = json.loads(detail)
            self.assertEqual(qa["verdict"], "DRY-OK")
            self.assertEqual(qa["kind"], "news")
            self.assertIsNone(qa["rr"])
            self.assertEqual(qa["voice"], "dry")
            rows = json.loads((out / "script.json").read_text(encoding="utf-8"))
            self.assertEqual([row["sec"] for row in rows], ["hook", "chart", "news", "cta"])
            shown = " ".join(row["d"] for row in rows)
            self.assertIn("The index closed lower", shown)
            self.assertIn("livemint.com", shown)
            self.assertIn("15 Oct 2026", shown)
            for token in (" SL ", " target ", " R1 ", " S1 "):
                self.assertNotIn(token, shown)
            names = [path.name for path in (out / "cards").iterdir()]
            self.assertEqual(sorted(names), ["chart.png", "cta.png", "hook.png", "news.png"])
            self.assertNotIn("view.png", names)

    def test_dry_run_renders_without_gemini_and_reports_the_fallback_chart(self):
        from realchart.ends import trim_intro
        from realchart.qa import probe
        bars = _bars_that_clear_rr()
        stamp = _stamp(datetime(2026, 10, 8).date())

        def walled(_symbol, dest):
            return {"ok": False, "reason": "captcha", "text": "verify you are human"}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            history = Path(tmp) / "history.json"
            source = Path(tmp) / "intro-source.mp4"
            intro = Path(tmp) / "intro_rtt.mp4"
            _color_clip(source, 8)
            trim_intro(source, intro)
            with mock.patch("realchart.build.HISTORY", history), \
                 mock.patch("realchart.ends.INTRO_RTT", intro), \
                 mock.patch("gemini_voice.write_speech", side_effect=AssertionError("no gemini")):
                code = run(
                    "close", out, now=FRIDAY, dry_run=True,
                    bars_for=lambda _t: (bars, stamp), articles=[], chart_opener=walled,
                    bars_intraday=_intraday_bars, chain_for=_held_chain,
                )
            detail = (out / "qa.json").read_text(encoding="utf-8") if (out / "qa.json").is_file() else "no qa"
            self.assertEqual(code, 0, detail)
            qa = json.loads(detail)
            self.assertEqual(qa["verdict"], "DRY-OK")
            self.assertEqual(qa["chart"], "yfinance")
            self.assertEqual(qa["label"], LABEL)
            self.assertEqual(qa["voice"], "dry")
            self.assertEqual(qa["chart_reason"], "bot wall")
            self.assertEqual(qa["intraday_chart"], "yfinance")
            self.assertEqual(qa["intraday_label"], capture.FALLBACK_LABEL_5M)
            self.assertIn("5m:", qa["intraday_chart_reason"])
            self.assertIn("15m:", qa["intraday_chart_reason"])
            self.assertEqual(qa["oi_status"], "held")
            self.assertGreaterEqual(qa["rr"], 1.5)
            self.assertEqual(qa["cta_yt"], "Paise Ki Pathshala follow करो")
            self.assertEqual(qa["cta_social"], "Rajesh Technical Trader ko follow करो")
            self.assertEqual(qa["social_intro"], "intro_rtt.mp4")
            source_md = (out / "source.md").read_text(encoding="utf-8")
            self.assertIn(LABEL, source_md)
            self.assertIn("chart reason bot wall", source_md)
            self.assertIn("requests 0", source_md)
            self.assertIn(capture.FALLBACK_LABEL_5M, source_md)
            self.assertIn("bot wall", (out / "chart-capture.txt").read_text(encoding="utf-8"))
            levels = json.loads((out / "levels.json").read_text(encoding="utf-8"))
            self.assertEqual(levels["chart_reason"], "bot wall")
            self.assertNotIn("call_oi", levels)
            self.assertFalse(history.exists())
            script_txt = (out / "script.txt").read_text(encoding="utf-8")
            self.assertIn(script.CTA, script_txt)
            self.assertIn(script.DISCLAIMER, script_txt)
            for phrase in script.FORBIDDEN_FOLLOW:
                self.assertNotIn(phrase.lower(), script_txt.lower())
            main = probe(out / "final.mp4")
            yt = probe(out / "final_yt.mp4")
            ig = probe(out / "final_ig.mp4")
            self.assertGreaterEqual(main["duration"], 40)
            self.assertLessEqual(main["duration"], 50)
            self.assertEqual(main["width"], 1080)
            self.assertEqual(main["height"], 1920)
            self.assertGreater(yt["duration"], main["duration"])
            self.assertGreater(ig["duration"], yt["duration"])
            order = [row["sec"] for row in json.loads((out / "script.json").read_text(encoding="utf-8"))]
            self.assertEqual(order[:3], ["hook", "chart", "intraday"])

    def test_a_repeated_setup_holds(self):
        now = datetime(2026, 10, 13, 18, 0, tzinfo=IST)
        bars = _bars()
        stamp = _stamp(now.date())
        history = [{"symbol": name, "setup": setup, "date": "2026-10-09"}
                   for name, _yf in __import__("realchart.levels", fromlist=["UNIVERSE"]).UNIVERSE
                   for setup in ("support test", "breakout", "ema cross", "retest")]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            hist = Path(tmp) / "history.json"
            hist.write_text(json.dumps({"items": history}), encoding="utf-8")
            with mock.patch("realchart.build.HISTORY", hist), \
                 mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1"}):
                code = run(
                    "ta", out, now=now,
                    bars_for=lambda _t: (bars, stamp), articles=[],
                    chart_opener=lambda _s, _d: {"ok": False, "reason": "captcha"},
                )
            self.assertEqual(code, 2)
            self.assertIn("no fresh setup", (out / "HOLD").read_text(encoding="utf-8"))


class WorkflowTests(unittest.TestCase):
    def test_crons_story_and_budget_priority(self):
        daily = (ROOT / ".github" / "workflows" / "daily-reels.yml").read_text(encoding="utf-8")
        for cron in (
            '"50 2 * * 1-5"',
            '"15 3 * * 1-5"',
            '"20 10 * * 1-5"',
            '"45 10 * * 1-5"',
            '"35 12 * * 1-5"',
            '"0 13 * * 1-5"',
            '"35 5 * * 6"',
            '"0 6 * * 6"',
        ):
            self.assertIn(cron, daily)
        self.assertIn("kind:", daily)
        self.assertIn("dry_run:", daily)
        self.assertIn("playwright install chromium", daily)
        self.assertIn("run/chart-capture.png", daily)
        self.assertIn("run/chart-capture.txt", daily)
        self.assertIn("run/final_yt.mp4", daily)
        self.assertIn("run/final_ig.mp4", daily)
        self.assertIn("run/chart-5m-capture.png", daily)
        self.assertIn("run/chart-15m-capture.txt", daily)
        publish_text = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        youtube = publish_text.split("name: Upload a new YouTube video", 1)[1].split("name: Cross-post the Reel", 1)[0]
        reel = publish_text.split("name: Cross-post the Reel", 1)[1].split("name: Commit the ledger fill", 1)[0]
        self.assertLess(youtube.find("final_yt.mp4"), youtube.find("python publish.py"))
        self.assertNotIn("final_ig", youtube)
        self.assertNotIn("skip_ig", youtube)
        self.assertLess(reel.find("final_ig.mp4"), reel.find("python publish_instagram.py"))
        self.assertIn("main.mp4", reel)
        self.assertIn('"$code" != "0"', daily)
        self.assertIn('"$code" != "2"', daily)
        self.assertIn("post_story=true", daily)
        self.assertNotIn("ELEVENLABS", daily)
        publish = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        self.assertIn("post_story:", publish)
        self.assertIn('default: "false"', publish)
        self.assertIn("IG_STORY:", publish)
        pipeline = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("topic_budget_skip.txt", pipeline)
        self.assertIn("gemini_budget.topic_short_allowed", pipeline)
        first = next(line for line in pipeline.splitlines() if line.strip().startswith("IG_CROSSPOST:"))
        self.assertIn("steps.fmt.outputs.visibility == 'public'", first)

    def test_close_reel_story_uses_final_ig_without_posting(self):
        daily = (ROOT / ".github" / "workflows" / "daily-reels.yml").read_text(encoding="utf-8")
        publish_step = daily.split("name: Publish via publish-artifact", 1)[1]
        self.assertIn("-f privacy=now", publish_step)
        self.assertIn("-f skip_ig=false", publish_step)
        self.assertIn("-f ig_only=false", publish_step)
        self.assertIn("-f post_story=true", publish_step)
        text = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        download = text.split("name: Download source artifact", 1)[1].split("name: Download the release bundle", 1)[0]
        self.assertIn('name in final_yt.mp4 final_ig.mp4', download)
        youtube = text.split("name: Upload a new YouTube video", 1)[1].split("name: Cross-post the Reel", 1)[0]
        reel = text.split("name: Cross-post the Reel", 1)[1].split("name: Commit the ledger fill", 1)[0]
        self.assertLess(youtube.find("final_yt.mp4"), youtube.find("python publish.py"))
        self.assertLess(reel.find("final_ig.mp4"), reel.find("python publish_instagram.py"))
        self.assertIn("IG_STORY:", reel)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "run" / "realchart-close"
            nested.mkdir(parents=True)
            (nested / "final.mp4").write_bytes(b"MAIN")
            (nested / "final_yt.mp4").write_bytes(b"YT")
            (nested / "final_ig.mp4").write_bytes(b"IG")
            (nested / "script.txt").write_bytes(b"SCRIPT")
            (nested / "thumbnail.jpg").write_bytes(b"THUMB")
            # final.mp4 is already at the artifact root's sibling only.
            # Promote the channel cuts the way the download step does when
            # the main file was copied and the cuts stayed nested.
            run = root / "flat"
            run.mkdir()
            (run / "final.mp4").write_bytes(b"MAIN")
            (run / "script.txt").write_bytes(b"SCRIPT")
            (run / "thumbnail.jpg").write_bytes(b"THUMB")
            for name in ("final_yt.mp4", "final_ig.mp4"):
                if not (run / name).is_file():
                    found = next(root.joinpath("run").rglob(name))
                    (run / name).write_bytes(found.read_bytes())
            main = run / "final.mp4"
            if (run / "final_yt.mp4").stat().st_size:
                (run / "main.mp4").write_bytes(main.read_bytes())
                main.write_bytes((run / "final_yt.mp4").read_bytes())
            self.assertEqual(main.read_bytes(), b"YT")
            if (run / "final_ig.mp4").stat().st_size:
                main.write_bytes((run / "final_ig.mp4").read_bytes())
            self.assertEqual(main.read_bytes(), b"IG")
            import publish_instagram as ig
            hosted = {}

            def host(path):
                hosted["bytes"] = Path(path).read_bytes()
                return "https://example.test/final_ig.mp4"

            story = {}

            def remember_story(url):
                story["url"] = url

            env = {
                "IG_CROSSPOST": "1",
                "IG_STORY": "1",
                "APPROVED": "true",
                "VIDEO_FORMAT": "short",
                "IG_SURFACE": "facebook",
            }
            (run / "uploaded.txt").write_text("dryvideoid\n", encoding="utf-8")
            with mock.patch.object(ig, "RUN", run), \
                 mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(ig, "video_format", return_value="short"), \
                 mock.patch("publish_guard.blocked_reason", return_value=""), \
                 mock.patch.object(ig, "existing_media", return_value=""), \
                 mock.patch.object(ig, "host_mp4", side_effect=host), \
                 mock.patch.object(ig, "host_cover", return_value=""), \
                 mock.patch.object(ig, "publish_reel", return_value="reel-dry") as reel, \
                 mock.patch.object(ig, "maybe_publish_story", side_effect=remember_story):
                ig.main()
        self.assertEqual(hosted["bytes"], b"IG")
        self.assertEqual(story["url"], "https://example.test/final_ig.mp4")
        reel.assert_called_once()
        self.assertEqual(reel.call_args.args[0], story["url"])

    def test_topic_shorts_wait_when_the_reel_hold_is_still_full(self):
        import gemini_budget
        now = FRIDAY
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "GEMINI_BUDGET_PATH": str(Path(tmp) / "budget.json"),
                "GEMINI_BUDGET": "1",
                "GEMINI_API_KEY": "key-one",
                "GEMINI_API_KEY_2": "key-two",
                "GEMINI_API_KEY_3": "",
                "GEMINI_API_KEY_4": "",
                "GEMINI_CHARGE_PATH": str(Path(tmp) / "charges.json"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(gemini_budget.reel_need(now), 3)
                self.assertEqual(gemini_budget.reel_need(datetime(2026, 10, 10, 11, tzinfo=IST)), 1)
                self.assertEqual(gemini_budget.reel_need(datetime(2026, 10, 11, 11, tzinfo=IST)), 0)
                for _ in range(7):
                    gemini_budget.note("other", "1", now)
                    gemini_budget.note("other", "2", now)
                self.assertFalse(gemini_budget.topic_short_allowed(now))
                self.assertTrue(gemini_budget.allow("news", "1", now))
                self.assertTrue(gemini_budget.allow("close", "1", now))


class OverlayTests(unittest.TestCase):
    def test_overlays_leave_pixels_outside_the_plot_and_do_not_clamp_off_scale(self):
        from PIL import Image
        from realchart.render import frames, paint_level_overlays
        image = Image.new("RGB", (1080, 1080), (22, 26, 32))
        outside = (8, 200)
        below = (500, 1050)
        image.putpixel(outside, (11, 22, 33))
        image.putpixel(below, (44, 55, 66))
        candle = (500, 400)
        image.putpixel(candle, (34, 181, 60))
        bars = [{"h": 110, "l": 90, "o": 95, "c": 100, "date": "2026-10-14"}]
        levels = {
            "pivot": 100, "r1": 104, "s1": 96, "r2": 108, "s2": 92,
            "sl_zone": [92, 96], "target_zone": [104, 108],
            "levels_chip": "Levels: Pivot (prev day OHLC) only, NSE option chain held",
        }
        labels = paint_level_overlays(image, levels, plot=capture.YF_PLOT, bars=bars)
        self.assertEqual(image.getpixel(outside), (11, 22, 33))
        self.assertEqual(image.getpixel(below), (44, 55, 66))
        self.assertIn("P", labels)
        self.assertIn("chip", labels)
        off = Image.new("RGB", (200, 200), (10, 10, 10))
        edge = (100, 40)
        off.putpixel(edge, (10, 10, 10))
        named = paint_level_overlays(
            off, {"r2": 99999, "levels_chip": ""},
            plot={"left": 20, "top": 40, "width": 160, "height": 100},
            bars=[{"h": 10, "l": 1}],
        )
        self.assertIn("R2 off-scale", named)
        self.assertEqual(off.getpixel(edge), (10, 10, 10))
        levels = compute(_bars_that_clear_rr(), "NSE:NIFTY", "^NSEI")
        apply_screen_levels(levels, _held_chain())
        rows = script.chunks_for("close", levels, None)
        with tempfile.TemporaryDirectory() as tmp:
            chart = Path(tmp) / "chart.png"
            Image.new("RGB", (1080, 1080), (30, 30, 30)).save(chart)
            intra = {
                "path": str(chart), "label": capture.FALLBACK_LABEL_5M, "interval": "5",
                "plot": capture.YF_PLOT, "scale_bars": _intraday_bars(None, "5"),
            }
            made = frames(Path(tmp) / "cards", "close", levels, rows, chart, None, intra)
            self.assertEqual([path.name for path in made], ["hook.png", "chart.png", "intraday.png", "view.png", "cta.png"])
            article = {"title": "Nifty ends", "key_sentence": "Lower.", "source": "Mint", "published": "14 Oct"}
            news = frames(Path(tmp) / "news", "news", levels, script.chunks_for("news", levels, article), chart, article, intra)
            self.assertEqual([path.name for path in news], ["hook.png", "chart.png", "news.png", "cta.png"])


class EndsTests(unittest.TestCase):
    def test_trim_and_copy_concat_do_not_reencode_the_main_reel(self):
        import inspect
        from realchart import qa
        from realchart.ends import (
            FOLLOW_PKP, FOLLOW_RTT, INTRO_HI, INTRO_LO, INTRO_RTT, INTRO_SOURCE, OUTRO_PKP, OUTRO_RTT, attach, trim_intro,
        )
        self.assertEqual(FOLLOW_RTT, "Rajesh Technical Trader ko follow करो")
        self.assertEqual(FOLLOW_PKP, "Paise Ki Pathshala follow करो")
        self.assertTrue(OUTRO_RTT.is_file())
        self.assertTrue(OUTRO_PKP.is_file())
        self.assertTrue(INTRO_SOURCE.is_file())
        self.assertTrue(INTRO_RTT.is_file())
        intro_info = qa.probe(INTRO_RTT)
        self.assertEqual((intro_info["width"], intro_info["height"]), (1080, 1920))
        self.assertEqual(intro_info["codec"], "h264")
        self.assertGreaterEqual(intro_info["duration"], INTRO_LO)
        self.assertLessEqual(intro_info["duration"], INTRO_HI)
        self.assertGreater(INTRO_RTT.stat().st_size, 200_000)
        for path in (OUTRO_RTT, OUTRO_PKP):
            info = qa.probe(path)
            self.assertEqual(info["width"], 1080)
            self.assertEqual(info["height"], 1920)
            self.assertEqual(info["codec"], "h264")
            self.assertGreaterEqual(info["duration"], 1.8)
            self.assertLessEqual(info["duration"], 2.3)
        source = inspect.getsource(qa.check)
        self.assertIn('video = out / "final.mp4"', source)
        self.assertNotIn('out / "final_ig.mp4"', source)
        self.assertNotIn('out / "final_yt.mp4"', source)
        ends = (ROOT / "realchart" / "ends.py").read_text(encoding="utf-8")
        self.assertNotIn("edge_tts", ends.lower())
        self.assertNotIn("eleven", ends.lower())
        self.assertNotIn("gemini", ends.lower())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clip = tmp / "long.mp4"
            intro = tmp / "intro_rtt.mp4"
            _color_clip(clip, 9)
            trim_intro(clip, intro, seconds=8)
            info = qa.probe(intro)
            self.assertGreaterEqual(info["duration"], INTRO_LO)
            self.assertLessEqual(info["duration"], INTRO_HI)
            self.assertEqual((info["width"], info["height"]), (1080, 1920))
            with self.assertRaises(FileNotFoundError):
                trim_intro(tmp / "no-such-intro.mp4", tmp / "missing.mp4")
            main = tmp / "main.mp4"
            _color_clip(main, 1, size="1080x1920")
            # The color clip is not the shared encode. Build a matching main, then copy-concat.
            from realchart.render import assemble
            from PIL import Image
            cards = tmp / "cards"
            cards.mkdir()
            image = Image.new("RGB", (32, 32), (12, 18, 24))
            paths = []
            for name in ("a", "b"):
                image.save(cards / f"{name}.png")
                paths.append(cards / f"{name}.png")
            main = assemble(paths, tmp / "missing.wav", tmp / "final.mp4", seconds=1.0)
            before = main.read_bytes()
            with mock.patch("realchart.ends.INTRO_RTT", intro):
                yt, ig = attach(main, tmp)
            self.assertEqual(main.read_bytes(), before)
            self.assertTrue(yt.is_file() and ig.is_file())
            main_info = qa.probe(main)
            yt_info = qa.probe(yt)
            ig_info = qa.probe(ig)
            self.assertAlmostEqual(yt_info["duration"], main_info["duration"] + qa.probe(OUTRO_PKP)["duration"], delta=0.15)
            self.assertAlmostEqual(
                ig_info["duration"],
                qa.probe(intro)["duration"] + main_info["duration"] + qa.probe(OUTRO_RTT)["duration"],
                delta=0.2,
            )
            self.assertGreater(ig_info["duration"], yt_info["duration"])
            self.assertEqual(yt_info["codec"], main_info["codec"])
            yt_list = (tmp / "final_yt.txt").read_text(encoding="utf-8").splitlines()
            ig_list = (tmp / "final_ig.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn(intro.name, ig_list[0])
            self.assertIn(main.name, ig_list[1])
            self.assertIn("outro_rtt.mp4", ig_list[2])
            self.assertNotIn("intro_rtt", yt_list[0])
            self.assertIn(main.name, yt_list[0])
            self.assertIn("outro_pkp.mp4", yt_list[1])
            with mock.patch("realchart.ends.INTRO_RTT", tmp / "absent.mp4"):
                with self.assertRaises(FileNotFoundError) as raised:
                    attach(main, tmp)
            self.assertIn("intro_rtt.mp4", str(raised.exception))


class StoryTests(unittest.TestCase):
    def test_story_uses_the_same_mp4(self):
        import publish_instagram as ig
        calls = []

        def ig_call(path, method="GET", params=None):
            calls.append((path, method, dict(params or {})))
            if str(path).endswith("/media") and method == "POST":
                return {"id": "container-1"}
            if params and "status_code" in str(params.get("fields") or ""):
                return {"status_code": "FINISHED"}
            if str(path).endswith("media_publish"):
                return {"id": "story-9"}
            return {"id": "story-9"}

        with mock.patch.dict(os.environ, {"IG_USER_ID": "1789", "IG_ACCESS_TOKEN": "token"}):
            with mock.patch.object(ig, "ig_call", side_effect=ig_call):
                self.assertEqual(ig.publish_story("https://example.com/final.mp4"), "story-9")
        self.assertEqual(calls[0][2]["media_type"], "STORIES")
        self.assertEqual(calls[0][2]["video_url"], "https://example.com/final.mp4")


class NewsLengthTests(unittest.TestCase):
    """The news Reel held at 24.00s after the levels beat left (#117)."""

    ARTICLE = {
        "title": "Sensex, Nifty open higher as banking stocks gain ahead of quarterly results",
        "summary": (
            "Indian equity benchmarks opened higher on Thursday, led by gains in private lenders and IT stocks. "
            "Foreign investors were net buyers in the previous session, according to provisional exchange data. "
            "Analysts said earnings from large banks this week will set the near-term direction for the market."
        ),
        "source": "livemint.com",
        "url": "https://www.livemint.com/markets/x",
        "published": "Thu, 15 Oct 2026 08:10:00 +0530",
    }

    def test_typical_news_script_reaches_the_40s_floor(self):
        rows = script.chunks_for("news", compute(_bars()), dict(self.ARTICLE))
        est = script.estimate_seconds(rows)
        self.assertGreaterEqual(est, script.NEWS_FLOOR_SEC)
        self.assertLessEqual(est, 50.0)
        spoken = script.spoken_text(rows)
        self.assertIn("Sensex, Nifty open higher", spoken)
        self.assertIn("livemint.com", spoken)
        self.assertIn("15 October 2026", spoken)
        self.assertEqual(script.lint(rows), [])

    def test_news_speaks_only_article_sentences(self):
        rows = script.chunks_for("news", compute(_bars()), dict(self.ARTICLE))
        spoken = next(row["t"] for row in rows if row["sec"] == "news")
        for number in re.findall(r"\d+", spoken):
            self.assertTrue(number in self.ARTICLE["published"] or number in self.ARTICLE["summary"] + self.ARTICLE["title"], number)

    def test_old_24s_script_is_below_the_floor(self):
        self.assertLess(script.estimate_seconds(" ".join(["शब्द"] * 42)), script.NEWS_FLOOR_SEC)

    def test_short_live_news_holds_before_voice(self):
        now = datetime(2026, 10, 15, 10, 2, tzinfo=IST)
        article = {
            "title": "Nifty ends lower",
            "url": "https://www.livemint.com/markets/nifty-ends",
            "summary": "The index closed lower.",
            "published": "Thu, 15 Oct 2026 08:10:00 +0530",
            "source": "livemint.com",
        }

        def shot(_url, dest):
            from PIL import Image
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (80, 80), (20, 20, 20)).save(dest)
            return {"ok": True, "path": str(dest)}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1"}), \
                 mock.patch("realchart.voice.speak", side_effect=AssertionError("no voice")):
                code = run(
                    "news", out, now=now, dry_run=False,
                    bars_for=lambda _t: (_bars(), _stamp(now.date())), articles=[article],
                    chart_opener=lambda _s, _d: {"ok": False, "reason": "x"}, article_opener=shot,
                )
            self.assertNotEqual(code, 0)
            self.assertTrue((out / "HOLD").exists())
            self.assertIn("40s floor", (out / "HOLD").read_text(encoding="utf-8"))
