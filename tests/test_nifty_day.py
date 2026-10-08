"""Aaj ka Nifty: mocked Yahoo bars, holiday skip, stale close, fixed script."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import nifty_day
import slot_guard

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent.parent


def _bar(day, hour, minute, close, high=None, low=None, open_=None):
    high = close + 40 if high is None else high
    low = close - 40 if low is None else low
    open_ = close - 10 if open_ is None else open_
    return {
        "ts": datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _week(now, close=25123.45):
    """Five daily bars ending in a 15:35 IST close today."""
    prices = (24880.0, 24940.0, 25010.0, 25002.95, close)
    rows = []
    for offset, price in enumerate(prices):
        day = (now - timedelta(days=len(prices) - 1 - offset)).date()
        rows.append(_bar(day, 15, 35, price, high=price + 80, low=price - 70))
    return rows


class SessionTests(unittest.TestCase):
    def test_trading_day_bar_after_close_builds_oct7_style_session(self):
        # Thursday 8 Oct 2026, 15:50 IST. The bar is today's 15:35 close.
        now = datetime(2026, 10, 8, 15, 50, tzinfo=IST)
        bars = _week(now, close=25123.45)
        bars[-2]["high"] = 25180.0
        bars[-2]["low"] = 24940.0
        bars[-2]["close"] = 25002.95
        decision = nifty_day.plan(now, bars, entries=[])
        self.assertEqual(decision["action"], "build")
        session = decision["session"]
        self.assertEqual(session["date_ist"], "2026-10-08")
        self.assertEqual(session["slot"], "16:00")
        self.assertEqual(nifty_day.money(session["close"]), "25123.45")
        self.assertEqual(nifty_day.money(session["change"]), "120.50")
        self.assertEqual(session["level"]["price"], 25100.0)
        self.assertEqual(session["level"]["role"], "support")
        self.assertIn(session["line"], nifty_day.LINE_CHOICES)
        blob = json.dumps(session["narration"], ensure_ascii=False)
        for word in nifty_day._BANNED:
            self.assertNotIn(word, blob)
        self.assertIn("आज", blob)
        self.assertNotIn("DeepSeek", blob)

    def test_weekend_and_nse_holiday_skip_with_no_video(self):
        saturday = datetime(2026, 10, 10, 15, 50, tzinfo=IST)
        weekend = nifty_day.plan(saturday, _week(saturday), entries=[])
        self.assertEqual(weekend["action"], "skip")
        self.assertTrue(weekend["occupy"])
        self.assertIn("weekend", weekend["reason"])
        self.assertIsNone(weekend["session"])
        holiday = datetime(2026, 10, 2, 15, 50, tzinfo=IST)
        closed = nifty_day.plan(holiday, _week(holiday), entries=[])
        self.assertEqual(closed["action"], "skip")
        self.assertTrue(closed["occupy"])
        self.assertIn("NSE holiday", closed["reason"])
        self.assertIsNone(closed["session"])

    def test_stale_or_pre_close_timestamp_does_not_build(self):
        now = datetime(2026, 10, 8, 15, 50, tzinfo=IST)
        stale = _week(now)
        stale[-1]["ts"] = datetime(2026, 10, 7, 15, 35, tzinfo=IST)
        missed = nifty_day.plan(now, stale, entries=[])
        self.assertEqual(missed["action"], "stale")
        self.assertFalse(missed["occupy"])
        self.assertIn("not today's IST session", missed["reason"])
        early = _week(now)
        early[-1]["ts"] = datetime(2026, 10, 8, 14, 55, tzinfo=IST)
        before = nifty_day.plan(now, early, entries=[])
        self.assertEqual(before["action"], "stale")
        self.assertIsNone(before["session"])
        # A date-only stamp is the 15:30 close, and only after that clock.
        dated = _week(now)
        dated[-1]["ts"] = "2026-10-08"
        ready = nifty_day.plan(now, dated, entries=[])
        self.assertEqual(ready["action"], "build")
        too_soon = nifty_day.plan(datetime(2026, 10, 8, 15, 0, tzinfo=IST), dated, entries=[])
        self.assertEqual(too_soon["action"], "stale")
        # Yahoo often stamps a daily bar at UTC midnight, which is 05:30 IST.
        utc_bar = _week(now)
        utc_bar[-1]["ts"] = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
        utc_ready = nifty_day.plan(now, utc_bar, entries=[])
        self.assertEqual(utc_ready["action"], "build")
        utc_early = nifty_day.plan(datetime(2026, 10, 8, 15, 0, tzinfo=IST), utc_bar, entries=[])
        self.assertEqual(utc_early["action"], "stale")

    def test_deepseek_may_only_choose_a_listed_line(self):
        self.assertEqual(nifty_day.choose_line(10, reply="2"), nifty_day.LINE_CHOICES[1])
        self.assertEqual(nifty_day.choose_line(10, reply="buy 25100 now"), nifty_day.LINE_CHOICES[0])
        self.assertEqual(nifty_day.choose_line(-3, reply=""), nifty_day.LINE_CHOICES[1])
        self.assertEqual(nifty_day.choose_line(0, reply=None), nifty_day.LINE_CHOICES[2])
        for line in nifty_day.LINE_CHOICES:
            self.assertIsNone(__import__("re").search(r"\d", line))

    def test_template_passes_the_narration_gate_and_stays_under_a_minute(self):
        import board_short
        now = datetime(2026, 10, 8, 15, 50, tzinfo=IST)
        session, reason = nifty_day.session_from_bars(_week(now), now, reply="1")
        self.assertEqual(reason, "")
        topic = {
            "key": "aaj_ka_nifty",
            "cat": "NIFTY",
            "topic": "आज का निफ्टी बंद",
            "_live_short": True,
            "_live_beats": "four",
            "_voice": {"words_per_second": 1.9},
            "facts": session["facts"],
            "beats": {"hook": ["आज", "बंद"], "pehchan": ["हाई", "लो"], "matlab": ["लेवल", "सपोर्ट"], "karein": ["देखें", "जानकारी"]},
        }
        narration = session["narration"]
        fails = board_short.check_narration(narration, topic)
        self.assertEqual(fails, [], fails)
        words = sum(len(narration["beats"][beat].split()) for beat in board_short.BEATS)
        self.assertGreaterEqual(words, 98)
        self.assertLessEqual(words, 108)
        self.assertLessEqual(len(narration["hook_line"].split()), 11)
        seconds = words / 1.9
        self.assertLessEqual(seconds, 59.0)
        self.assertEqual(board_short._output_bounds(topic), (45.0, 59.0))
        import publish_gates
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "narration.json").write_text(
                json.dumps(narration, ensure_ascii=False), encoding="utf-8",
            )
            bare = publish_gates.market_reasons(run)
            self.assertTrue(any("आज" in reason for reason in bare), bare)
            nifty_day._write_verification(session, run)
            self.assertEqual(publish_gates.market_reasons(run), [])


class ChartTests(unittest.TestCase):
    def test_chart_sits_in_the_top_band_with_the_close_and_level(self):
        import scene_frames
        import safe_area
        now = datetime(2026, 10, 8, 15, 50, tzinfo=IST)
        session, _reason = nifty_day.session_from_bars(_week(now), now, reply="")
        topic = {
            "key": "aaj_ka_nifty",
            "_nifty": session,
            "_spoken_beats": session["narration"]["beats"],
            "_live_short": True,
        }
        hook = scene_frames.frames_for(topic, "hook", 0, {"mode": "schematic"})[0][0]
        frame = scene_frames.frames_for(topic, "matlab", 1, {"mode": "schematic"})[0][0]
        region = frame.chart_region
        height = (region[3] - region[1]) / frame.size[1]
        width = (region[2] - region[0]) / frame.size[0]
        self.assertGreaterEqual(height, 0.60)
        self.assertLessEqual(height, 0.66)
        self.assertGreaterEqual(width, 0.90)
        self.assertLessEqual(region[3], safe_area.SUBTITLE_TOP + 1)
        self.assertIn("25123.45", scene_frames.frame_card_text(hook))
        card = scene_frames.frame_card_text(frame)
        self.assertIn("25100.00", card)
        self.assertIn("25100.00", session["narration"]["beats"]["matlab"])
        import numpy as np
        import scene_pace
        for still in (hook, frame):
            self.assertLessEqual(scene_pace.empty_row_fraction(still), 0.20)
            rows = np.asarray(still.convert("RGB")).max(axis=2) > 80
            chart_rows = rows[int(region[1]):int(region[3])]
            self.assertGreaterEqual(float(chart_rows.any(axis=1).mean()), 0.90)
            self.assertEqual(scene_frames.frames_safe_area_notes([still]), [])


class ScheduleTests(unittest.TestCase):
    def test_topic_picker_skips_the_data_short_and_the_16_slot(self):
        import pick_topic
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        topic = next(item for item in bank["topics"] if item["key"] == "aaj_ka_nifty")
        self.assertTrue(topic["data_short"])
        self.assertIn("aaj_ka_nifty", __import__("board_scenes").SCENES)
        now = datetime(2026, 10, 8, 10, 0, tzinfo=IST)
        with mock.patch.dict("os.environ", {"SKIP_DATA_SLOT": "1"}, clear=False):
            choice = slot_guard.next_unfilled(now, entries=[])
        self.assertNotEqual(choice["slot"], "16:00")
        self.assertEqual(choice["slot"], "21:00")
        picked = pick_topic._pick_fresh(
            bank, "used_short", [], (None, None, None, None), "short", skips=[],
        )
        if picked:
            self.assertNotEqual(picked.get("key"), "aaj_ka_nifty")

    def test_slot_watch_fills_a_missed_trading_day_with_the_nifty_job(self):
        import slot_watch
        now = datetime(2026, 10, 8, 16, 49, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "published.json"
            ledger.write_text("[]", encoding="utf-8")
            gout = Path(tmp) / "out.txt"
            env = {"SLOT_CHECK": "16:00", "GITHUB_OUTPUT": str(gout)}
            with mock.patch.object(slot_watch, "LEDGER", ledger), \
                 mock.patch.object(slot_guard, "LEDGER", ledger), \
                 mock.patch.dict("os.environ", env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime") as dt:
                dt.now.return_value = now
                dt.strptime = datetime.strptime
                slot_watch.main()
            flags = gout.read_text(encoding="utf-8")
        self.assertIn("nifty_fill=true", flags)
        watch = (ROOT / ".github" / "workflows" / "slot-watch.yml").read_text(encoding="utf-8")
        nifty = watch.split("nifty-fill:", 1)[1]
        self.assertIn("data_short: true", nifty)
        self.assertIn("uses: ./.github/workflows/paise-ki-pathshala.yml", nifty)
        cron = (ROOT / ".github" / "workflows" / "aaj-ka-nifty.yml").read_text(encoding="utf-8")
        self.assertIn('"21 10 * * 1-5"', cron)
        self.assertIn("data_short: true", cron)
        self.assertIn("secrets: inherit", cron)
        self.assertIn("secrets: inherit", nifty)
        pipeline = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        slot_env = pipeline.split("Format decide karo", 1)[1].split("Slot pehle", 1)[0]
        self.assertIn("DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}", slot_env)
        run = pipeline.split("Run Pipeline", 1)[1].split("Show why the build failed", 1)[0]
        for line in (
            "DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}",
            "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}",
            "ELEVENLABS_API_KEY: ${{ secrets.ELEVENLABS_API_KEY }}",
            "ELEVENLABS_VOICE_ID:",
            "YOUTUBE_CLIENT_ID: ${{ secrets.YT_FIN_CLIENT_ID }}",
            "YOUTUBE_CLIENT_SECRET: ${{ secrets.YT_FIN_CLIENT_SECRET }}",
            "YOUTUBE_REFRESH_TOKEN: ${{ secrets.YT_FIN_REFRESH_TOKEN }}",
            "IG_ACCESS_TOKEN: ${{ secrets.IG_ACCESS_TOKEN }}",
            "IG_USER_ID:",
        ):
            self.assertIn(line, run, line)

    def test_holiday_skip_is_written_to_the_ledger(self):
        import ledger
        now = datetime(2026, 10, 2, 15, 51, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text("[]", encoding="utf-8")
            with mock.patch.object(ledger, "PATH", path):
                decision = nifty_day.plan(now, _week(now), entries=[])
                nifty_day.occupy_skip(decision)
            rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slot"], "16:00")
        self.assertIsNone(rows[0]["video_id"])
        self.assertIn("NSE holiday", rows[0]["slot_skipped"])
