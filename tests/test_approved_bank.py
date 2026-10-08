"""Approved topic bank: fixed scripts, no DeepSeek, two slots a day."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import approved_bank
import board_short
import hook_check
import pattern_charts
import trade_plan

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent.parent
ORDER = ("hook", "pehchan", "matlab", "karein")
BANNED = ("हद", "पूँजी", "क्षमता", "तय करो", "पलटवार", "चीरती")
NEW_KEYS = (
    "pat_hammer", "pat_doji", "pat_bull_engulf", "pat_morning_star", "pat_black_crows",
    "pat_double", "pat_head_shoulders", "pat_cup_handle", "pat_triangle", "pat_flag",
    "pat_breakout", "pat_rsi", "pat_support", "pat_gap_up", "pat_stop_loss",
)


def _topic(key):
    return approved_bank.topic_from(approved_bank.by_key(key))


class ScriptTests(unittest.TestCase):
    def test_fifteen_new_scripts_stay_unapproved_and_pass_the_gate(self):
        import scene_frames
        import scene_pace
        seen = []
        for key in NEW_KEYS:
            script = approved_bank.by_key(key)
            self.assertIsNotNone(script, key)
            self.assertIs(script.get("owner_approved"), False, key)
            self.assertIsNot(script.get("reusable"), True, key)
            self.assertIn(key, pattern_charts.PATTERNS)
            topic = _topic(key)
            narration = approved_bank.narration_from(script)
            fails = board_short.check_narration(narration, topic)
            self.assertEqual(fails, [], (key, fails))
            self.assertEqual(tuple(narration["beats"]), trade_plan.BEATS)
            self.assertTrue(narration["beats"]["cta"].endswith(trade_plan.DISCLAIMER), key)
            self.assertEqual(trade_plan.raw_digit_faults(script), [], key)
            total = sum(len(narration["beats"][beat].split()) for beat in trade_plan.BEATS)
            self.assertGreaterEqual(total, 90, key)
            self.assertLessEqual(total, 140, key)
            self.assertLessEqual(len(narration["hook_line"].split()), 12, key)
            blob = " ".join(narration["beats"].values())
            for word in BANNED:
                self.assertNotIn(word, blob, key)
            spoken = hook_check.extract_numbers(blob)
            chart = trade_plan.number_strings(trade_plan.plan_for(key))
            self.assertFalse(spoken - chart, (key, spoken - chart))
            for beat in trade_plan.BEATS:
                frame = scene_frames.frames_for(topic, beat, 1, {"mode": "schematic"})[0][0]
                region = frame.chart_region
                height = (region[3] - region[1]) / frame.size[1]
                self.assertGreaterEqual(height, 0.60, (key, beat))
                self.assertLessEqual(height, 0.66, (key, beat))
                self.assertLessEqual(scene_pace.empty_row_fraction(frame), 0.20, (key, beat))
                self.assertEqual(scene_frames.frames_safe_area_notes([frame]), [], (key, beat))
            seen.append(key)
        self.assertEqual(len(seen), 15)

    def test_pattern_shapes_match_the_names(self):
        hammer = pattern_charts.PATTERNS["pat_hammer"]["ohlc"][-1]
        body = abs(hammer[3] - hammer[0])
        lower = min(hammer[0], hammer[3]) - hammer[2]
        self.assertGreater(lower, body * 2)
        doji = pattern_charts.PATTERNS["pat_doji"]["ohlc"][-1]
        self.assertEqual(doji[0], doji[3])
        red, green = pattern_charts.PATTERNS["pat_bull_engulf"]["ohlc"]
        self.assertGreater(max(green[0], green[3]), max(red[0], red[3]))
        self.assertLess(min(green[0], green[3]), min(red[0], red[3]))
        closes = [candle[3] for candle in pattern_charts.PATTERNS["pat_black_crows"]["ohlc"]]
        self.assertEqual(closes, sorted(closes, reverse=True))
        self.assertEqual(pattern_charts.PATTERNS["pat_double"]["top"].count(120), 2)
        self.assertEqual(pattern_charts.PATTERNS["pat_double"]["bottom"].count(80), 2)
        points = pattern_charts.PATTERNS["pat_head_shoulders"]["points"]
        self.assertEqual(max(points), 130)
        cup = pattern_charts.PATTERNS["pat_cup_handle"]["points"]
        self.assertEqual(min(cup), 80)
        rsi_lines = [price for price, _label in pattern_charts.PATTERNS["pat_rsi"]["lines"]]
        self.assertIn(70, rsi_lines)
        self.assertIn(30, rsi_lines)


class PickTests(unittest.TestCase):
    def test_nothing_approved_is_a_clean_skip(self):
        self.assertIsNone(approved_bank.next_script([]))
        reason = approved_bank.skip_reason([])
        self.assertIn("owner_approved", reason)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text("[]\n", encoding="utf-8")
            with mock.patch("ledger.PATH", path):
                approved_bank.occupy_skip(reason, slot="08:30", date_ist="2026-10-08")
            rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["video_id"])
        self.assertEqual(rows[0]["slot"], "08:30")
        self.assertIn("owner_approved", rows[0]["slot_skipped"])

    def test_piercing_is_not_reposted_unless_reusable(self):
        piercing = dict(approved_bank.by_key("piercing"))
        self.assertIsNot(piercing.get("owner_approved"), True)
        piercing["owner_approved"] = True
        piercing["reusable"] = False
        posted = [{"topic_key": "piercing", "video_id": "abc123"}]
        self.assertEqual(approved_bank.select([piercing], [])["key"], "piercing")
        self.assertIsNone(approved_bank.select([piercing], posted))
        piercing["reusable"] = True
        self.assertEqual(approved_bank.select([piercing], posted)["key"], "piercing")
        fresh = [dict(approved_bank.by_key(key)) for key in NEW_KEYS]
        self.assertIsNone(approved_bank.select(fresh, []))

    def test_topic_slots_skip_16_and_do_not_call_deepseek(self):
        import slot_guard
        now = datetime(2026, 10, 8, 10, 0, tzinfo=IST)
        with mock.patch.dict(os.environ, {"APPROVED_BANK": "1"}):
            choice = slot_guard.next_unfilled(now, entries=[])
        self.assertEqual(choice["slot"], "21:00")
        topic = _topic("pat_hammer")
        with mock.patch.dict(os.environ, {"APPROVED_BANK": "1"}):
            with mock.patch("common.claude") as claude:
                narration, source = board_short.narration(topic, {"voice": {}})
        claude.assert_not_called()
        self.assertEqual(source, "approved")
        self.assertEqual(narration["source"], "approved")

    def test_sample_workflow_uses_the_gemini_voice_path_and_does_not_upload(self):
        text = (ROOT / ".github/workflows/render-topic-sample.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", text)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", text)
        self.assertIn("ELEVENLABS_API_KEY: ${{ secrets.ELEVENLABS_API_KEY }}", text)
        self.assertIn("PAID_TTS_ENABLED: ${{ vars.PAID_TTS_ENABLED }}", text)
        self.assertIn("DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}", text)
        self.assertIn("KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}", text)
        self.assertIn("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}", text)
        self.assertIn("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}", text)
        self.assertIn("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}", text)
        self.assertIn("PUBLISH_VISIBILITY: public", text)
        self.assertNotIn("PROOF_FREE_AUDIO", text)
        self.assertNotIn("python publish.py", text)
        self.assertNotIn("--publish", text)
        self.assertIn("run/final.mp4", text)
        self.assertIn("run/thumbnail.jpg", text)
        self.assertIn("run/qa_report.md", text)
        pipeline = (ROOT / ".github/workflows/paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("APPROVED_BANK=1 SKIP_DATA_SLOT=1 python slot_guard.py --next", pipeline)
        self.assertIn("slot_guard.py --next", pipeline)

    def test_preflight_skips_deepseek_key_for_bank_script_and_nifty(self):
        import check_setup
        base = {
            key: value for key, value in os.environ.items()
            if key not in ("DEEPSEEK_API_KEY", "APPROVED_BANK", "DATA_SHORT", "NARRATION_FROM_RUN", "TOPIC_KEY")
        }
        with mock.patch.dict(os.environ, base, clear=True):
            self.assertFalse(check_setup.skips_deepseek_key())
            self.assertEqual(check_setup.deepseek_key_problem(), check_setup.DEEPSEEK_KEY_PROBLEM)
        for extra in (
            {"APPROVED_BANK": "1"},
            {"DATA_SHORT": "1"},
            {"NARRATION_FROM_RUN": "37095205924"},
            {"TOPIC_KEY": "aaj_ka_nifty"},
            {"TOPIC_KEY": "pat_hammer"},
        ):
            with mock.patch.dict(os.environ, {**base, **extra}, clear=True):
                self.assertTrue(check_setup.skips_deepseek_key(), extra)
                self.assertIsNone(check_setup.deepseek_key_problem())
        with mock.patch.dict(os.environ, {**base, "DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            self.assertIsNone(check_setup.deepseek_key_problem())
        with mock.patch.dict(os.environ, {**base, "TOPIC_KEY": "not_a_real_topic"}, clear=True):
            self.assertFalse(check_setup.skips_deepseek_key())
            self.assertIn("DEEPSEEK_API_KEY is missing", check_setup.deepseek_key_problem())


if __name__ == "__main__":
    unittest.main()
