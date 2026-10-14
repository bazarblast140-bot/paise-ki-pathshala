"""Daily Reels: real articles, real levels, dry runs that do not spend Gemini."""
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
from realchart.levels import compute, pick_setup
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
        self.assertEqual(rows[0]["start"], 0.0)
        self.assertEqual(rows[0]["end"], 3.0)
        self.assertEqual(script.lint(rows), [])
        self.assertEqual(script.number_faults(rows, levels), [])
        rows[2]["t"] = "हद पूँजी का हिसाब तय करो"
        self.assertTrue(any(fault.startswith("banned word") for fault in script.lint(rows)))
        bad = script.chunks_for("close", levels, None)
        bad[1]["d"] = "Close 99,999.00"
        self.assertTrue(script.number_faults(bad, levels))


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
            self.assertNotEqual(shot["label"], "TradingView")
            self.assertTrue(dest.is_file())

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

    def test_dry_run_renders_without_gemini_and_reports_the_fallback_chart(self):
        bars = _bars()
        stamp = _stamp(datetime(2026, 10, 8).date())

        def walled(_symbol, dest):
            return {"ok": False, "reason": "captcha", "text": "verify you are human"}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            history = Path(tmp) / "history.json"
            with mock.patch("realchart.build.HISTORY", history), \
                 mock.patch("gemini_voice.write_speech", side_effect=AssertionError("no gemini")):
                code = run(
                    "close", out, now=FRIDAY, dry_run=True,
                    bars_for=lambda _t: (bars, stamp), articles=[], chart_opener=walled,
                )
            detail = (out / "qa.json").read_text(encoding="utf-8") if (out / "qa.json").is_file() else "no qa"
            self.assertEqual(code, 0, detail)
            qa = json.loads(detail)
            self.assertEqual(qa["verdict"], "DRY-OK")
            self.assertEqual(qa["chart"], "yfinance")
            self.assertEqual(qa["label"], LABEL)
            self.assertEqual(qa["voice"], "dry")
            source = (out / "source.md").read_text(encoding="utf-8")
            self.assertIn(LABEL, source)
            self.assertIn("requests 0", source)
            self.assertFalse(history.exists())

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
