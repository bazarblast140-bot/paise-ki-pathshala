"""Every approved topic teaches a full trade plan, or it does not render."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import approved_bank
import board_short
import scene_frames
import trade_plan

ROOT = Path(__file__).resolve().parents[1]


def _topic(key):
    return approved_bank.topic_from(approved_bank.by_key(key))


class LibraryTests(unittest.TestCase):
    def test_every_approved_file_is_an_eight_beat_plan(self):
        seen = []
        for path in sorted((ROOT / "approved").glob("*.json")):
            script = json.loads(path.read_text(encoding="utf-8"))
            key = path.stem
            self.assertIn(key, trade_plan.KEYS, key)
            self.assertEqual(trade_plan.raw_digit_faults(script), [], key)
            self.assertTrue(script["beats"]["cta"].endswith(trade_plan.DISCLAIMER), key)
            if key.startswith("pat_"):
                self.assertIs(script.get("owner_approved"), False, key)
                self.assertIs(script.get("reusable"), False, key)
            else:
                self.assertNotIn("owner_approved", script, key)
            topic = _topic(key)
            narration = approved_bank.narration_from(script)
            self.assertEqual(board_short.check_narration(narration, topic), [], key)
            plan = trade_plan.plan_for(key)
            self.assertGreaterEqual(plan.rr, 1.5, key)
            if plan.side == "long":
                self.assertLess(plan.sl, plan.entry, key)
            else:
                self.assertGreater(plan.sl, plan.entry, key)
            labels = " ".join(row["label"] for row in trade_plan.overlays(plan))
            for mark in ("ENTRY", "SL", "TARGET", "="):
                self.assertIn(mark, labels, key)
            seen.append(key)
        self.assertEqual(sorted(seen), sorted(trade_plan.KEYS))

    def test_pattern_geometry_is_computed_from_the_ohlc(self):
        hammer = trade_plan.plan_for("pat_hammer").ohlc[3]
        body = abs(hammer[3] - hammer[0])
        lower = min(hammer[0], hammer[3]) - hammer[2]
        self.assertGreaterEqual(lower, body * 2)
        doji = trade_plan.plan_for("pat_doji").ohlc[2]
        self.assertEqual(doji[0], doji[3])
        red, green = trade_plan.plan_for("pat_bull_engulf").ohlc[2:4]
        self.assertGreater(max(green[0], green[3]), max(red[0], red[3]))
        self.assertLess(min(green[0], green[3]), min(red[0], red[3]))
        closes = [candle[3] for candle in trade_plan.plan_for("pat_black_crows").ohlc[1:4]]
        self.assertEqual(closes, sorted(closes, reverse=True))
        tops = trade_plan.plan_for("pat_double").ohlc
        self.assertEqual(tops[0][1], tops[2][1])
        points = [candle[1] for candle in trade_plan.plan_for("pat_head_shoulders").ohlc[:5]]
        self.assertEqual(max(points), 130)
        cup = [candle[2] for candle in trade_plan.plan_for("pat_cup_handle").ohlc]
        self.assertEqual(min(cup), 80)
        self.assertEqual(trade_plan.plan_for("pat_rsi").extras["os"], 30)
        self.assertEqual(trade_plan.plan_for("pat_rsi").extras["ob"], 70)

    def test_gate_blocks_a_missing_beat_a_bad_number_and_a_bad_stop(self):
        topic = _topic("pat_hammer")
        narration = approved_bank.narration_for(topic)
        broken = json.loads(json.dumps(narration))
        broken["beats"]["entry"] = ""
        self.assertTrue(any("entry" in item for item in trade_plan.script_fails(broken, topic)))
        broken = json.loads(json.dumps(narration))
        broken["beats"]["target"] += " 999"
        self.assertTrue(any("999" in item for item in trade_plan.script_fails(broken, topic)))
        with self.assertRaises(ValueError):
            trade_plan.Plan("bad", "long", ((1, 2, 0, 1),), 10, 12, 14, 0, "SUPPORT", 0)
        with self.assertRaises(ValueError):
            trade_plan.Plan("bad", "long", ((1, 2, 0, 1),), 10, 8, 11, 0, "SUPPORT", 0)
        with mock.patch.object(board_short, "prepare_spoken") as prepare:
            with self.assertRaises(SystemExit):
                board_short._render_short(topic, broken, {"voice": {}}, {})
        prepare.assert_not_called()


class ChartTests(unittest.TestCase):
    def test_overlays_follow_the_beat_and_the_whisper_word(self):
        topic = _topic("pat_hammer")
        hook = scene_frames.frames_for(topic, "hook", 1, {"mode": "schematic"})[0][0]
        entry = scene_frames.frames_for(topic, "entry", 1, {"mode": "schematic"})[0][0]
        target = scene_frames.frames_for(topic, "target", 1, {"mode": "schematic"})[0][0]
        self.assertNotIn("entry", hook.shown_overlays)
        self.assertIn("ENTRY 118", entry.overlay_labels)
        self.assertIn("SL 100", target.overlay_labels)
        self.assertIn("TARGET 154", target.overlay_labels)
        self.assertTrue(any(label.startswith("Entry 118") and "1:2" in label for label in target.overlay_labels))
        region = target.chart_region
        height = (region[3] - region[1]) / target.size[1]
        self.assertGreaterEqual(height, 0.60)
        self.assertLessEqual(height, 0.66)
        words = [
            {"word": "ट्रेंड", "start": 8.0, "end": 8.4},
            {"word": "सपोर्ट", "start": 16.0, "end": 16.4},
            {"word": "एंट्री", "start": 24.0, "end": 24.4},
            {"word": "स्टॉप", "start": 32.0, "end": 32.4},
            {"word": "नतीजा", "start": 40.0, "end": 40.4},
            {"word": "रिस्क", "start": 41.0, "end": 41.4},
        ]
        topic["_audio_words"] = words
        topic["_beat_times"] = {
            beat: (index * 8.0, index * 8.0 + 8.0) for index, beat in enumerate(trade_plan.BEATS)
        }
        topic["_scene_plan"] = [{
            "beat": "entry", "start": 24.0, "end": 30.0, "index": 0,
        }]
        early = trade_plan.paint_frame(topic, "entry", 0, 1, "एंट्री")
        self.assertIn("entry", early.shown_overlays)
        self.assertNotIn("sl", early.shown_overlays)
        topic["_scene_plan"] = [{
            "beat": "target", "start": 40.0, "end": 48.0, "index": 0,
        }]
        late = trade_plan.paint_frame(topic, "target", 0, 1, "नतीजा")
        self.assertIn("rr", late.shown_overlays)
        self.assertIn("target", late.shown_overlays)


class VoiceTests(unittest.TestCase):
    def test_topic_videos_accept_45_to_75_and_four_beat_gemini_keeps_34(self):
        topic = _topic("pat_hammer")
        self.assertEqual(board_short.live_voice_bounds(topic), (45.0, 75.0))
        self.assertEqual(board_short._output_bounds(topic, engine="gemini"), (45.0, 75.0))
        self.assertEqual(board_short._output_bounds(topic, engine="edge"), (45.0, 75.0))
        self.assertEqual(board_short._gemini_pace_window(topic), (45.0, 75.0))
        four = {"_live_short": True, "_live_beats": "four", "key": "plain"}
        self.assertEqual(board_short._output_bounds(four, engine="gemini"), (34.0, 65.0))
        self.assertEqual(board_short._gemini_pace_window(four), (34.0, 65.0))
        self.assertEqual(board_short._output_bounds(four, engine="edge"), (45.0, 65.0))
        nifty = {"key": "aaj_ka_nifty", "_live_short": True, "_live_beats": "four"}
        self.assertEqual(board_short.live_voice_bounds(nifty), (45.0, 59.0))
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "narration.json").write_text(json.dumps({
                "plan": True, "source": "approved", "live_beats": "plan",
            }), encoding="utf-8")
            (run / "voice_engine.txt").write_text("gemini\n", encoding="utf-8")
            import quality_check
            import auto_agent
            with mock.patch.object(quality_check, "RUN", run):
                self.assertEqual(quality_check.short_duration_bounds(), (45.0, 75.0, "45-75s"))
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window(), (45.0, 75.0, "45-75s"))

    def test_a_fallback_voice_is_a_review_flag(self):
        topic = _topic("pat_doji")
        self.assertFalse(trade_plan.voice_review("gemini", topic))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(trade_plan.voice_review("edge", topic))
        self.assertIn("[REVIEW] voice_engine=edge", buf.getvalue())
        self.assertIn("primary voice is gemini", buf.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run), \
                 mock.patch.object(board_short, "_VOICE_TOPIC", topic), \
                 mock.patch("credit_log.set_tts_engine"), \
                 mock.patch("eleven_voice.write_voice_meta", return_value={"engine": "edge"}):
                (run / "voice_meta.json").write_text(json.dumps({"engine": "edge"}), encoding="utf-8")
                board_short._write_spoken_engine("edge")
            meta = json.loads((run / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["review"])


if __name__ == "__main__":
    unittest.main()
