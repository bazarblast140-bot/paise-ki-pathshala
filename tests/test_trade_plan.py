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
# Rajesh approved these seven topics on 22-Oct-2026.
OWNER_APPROVED_2026_10_22 = (
    "pat_bear_engulf", "pat_shooting_star", "pat_double_bottom", "pat_evening_star",
    "pat_bear_flag", "pat_inside_bar", "pat_ema_cross",
    # Rajesh approved these six on 29-Oct-2026.
    "pat_bull_harami", "pat_tweezer_bottom", "pat_marubozu", "pat_hanging_man",
    "pat_dark_cloud", "pat_white_soldiers",
)


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
            raw = json.dumps(
                {"beats": script.get("beats"), "on_screen": script.get("on_screen")},
                ensure_ascii=False,
            )
            for word in trade_plan.BANNED:
                self.assertNotIn(word, raw, key)
            self.assertNotIn("कदम", raw, key)
            screen = json.dumps(script.get("on_screen") or {}, ensure_ascii=False)
            self.assertNotIn("सवाल", screen, key)
            self.assertNotIn("जगह", screen, key)
            self.assertIn("कहाँ वैलिड", screen, key)
            if key in OWNER_APPROVED_2026_10_22:
                self.assertIs(script.get("owner_approved"), True, key)
                self.assertIs(script.get("reusable"), False, key)
            elif key.startswith("pat_"):
                self.assertIs(script.get("owner_approved"), False, key)
                self.assertIs(script.get("reusable"), False, key)
            else:
                self.assertNotIn("owner_approved", script, key)
            topic = _topic(key)
            narration = approved_bank.narration_from(script)
            self.assertEqual(board_short.check_narration(narration, topic), [], key)
            plan = trade_plan.plan_for(key)
            target = narration["beats"]["target"]
            self.assertIn("टारगेट", target, key)
            self.assertIn("रिस्क-रिवॉर्ड", target, key)
            self.assertIn(plan.rule, target, key)
            self.assertGreaterEqual(plan.rr, 1.5, key)
            if plan.side == "long":
                self.assertLess(plan.sl, plan.entry, key)
            else:
                self.assertGreater(plan.sl, plan.entry, key)
            self.assertIn("?", narration["beats"]["cta"].split(trade_plan.DISCLAIMER)[0], key)
            labels = " ".join(row["label"] for row in trade_plan.overlays(plan))
            for mark in ("ENTRY", "SL", "TARGET", "="):
                self.assertIn(mark, labels, key)
            seen.append(narration["beats"]["cta"].split(trade_plan.DISCLAIMER)[0].strip())
        self.assertEqual(len(set(seen)), len(trade_plan.KEYS))
        hammer = json.loads((ROOT / "approved" / "pat_hammer.json").read_text(encoding="utf-8"))
        self.assertTrue(hammer["beats"]["cta"].startswith("आपने हैमर पर कभी स्टॉप लॉस हिट किया? कमेंट करो।"))

    def test_pattern_geometry_is_computed_from_the_ohlc(self):
        hammer_plan = trade_plan.plan_for("pat_hammer")
        self.assertGreaterEqual(len(hammer_plan.ohlc), 8)
        self.assertLessEqual(len(hammer_plan.ohlc), 12)
        hammer = hammer_plan.ohlc[hammer_plan.focus]
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
        self.assertIn("SL 98", target.overlay_labels)
        self.assertIn("TARGET 158", target.overlay_labels)
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
            {"word": "टारगेट", "start": 40.0, "end": 40.4},
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
        late = trade_plan.paint_frame(topic, "target", 0, 1, "टारगेट")
        self.assertIn("rr", late.shown_overlays)
        self.assertIn("target", late.shown_overlays)

    def test_board_short_times_the_labelled_lines_and_draws_them(self):
        import numpy as np
        import board_kit as K
        topic = _topic("pat_hammer")
        words = [
            {"word": "ट्रेंड", "start": 8.0, "end": 8.4},
            {"word": "सपोर्ट", "start": 16.0, "end": 16.4},
            {"word": "एंट्री", "start": 24.0, "end": 24.4},
            {"word": "स्टॉप", "start": 32.0, "end": 32.4},
            {"word": "टारगेट", "start": 40.0, "end": 40.4},
            {"word": "रिस्क", "start": 41.0, "end": 41.4},
        ]
        beat_times = {
            beat: (index * 8.0, index * 8.0 + 8.0) for index, beat in enumerate(trade_plan.BEATS)
        }
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 8.0, "index": 0},
            {"beat": "entry", "start": 24.0, "end": 30.0, "index": 0},
            {"beat": "target", "start": 40.0, "end": 48.0, "index": 0},
        ]
        spoken = approved_bank.narration_for(topic)["beats"]
        board_short._install_scene_cards(topic, spoken, beat_times, scenes, words)
        events = {row["kind"]: row for row in topic["_chart_overlays"]}
        expected = {
            "trend": (8.0, "TREND"),
            "sr": (16.0, "SUPPORT 100"),
            "entry": (24.0, "ENTRY 118"),
            "sl": (32.0, "SL 98"),
            "target": (40.0, "TARGET 158"),
            "rr": (41.0, "Entry 118 · SL 98 · Target 158 = 1:2"),
        }
        for kind, (start, label) in expected.items():
            self.assertEqual(events[kind]["label"], label, kind)
            self.assertAlmostEqual(events[kind]["start"], start, places=2, msg=kind)
            self.assertAlmostEqual(events[kind]["end"], 64.0, places=2, msg=kind)
            self.assertGreater(events[kind]["end"], events[kind]["start"], kind)
        early = scene_frames.frames_for(topic, "entry", 1, {"mode": "schematic"})[0][0]
        self.assertIn("ENTRY 118", early.overlay_labels)
        self.assertIn("SUPPORT 100", early.overlay_labels)
        self.assertNotIn("SL 98", early.overlay_labels)
        frame = scene_frames.frames_for(topic, "target", 1, {"mode": "schematic"})[0][0]
        for label in ("ENTRY 118", "SL 98", "TARGET 158", "SUPPORT 100", "TREND"):
            self.assertIn(label, frame.overlay_labels)
        self.assertTrue(any("1:2" in label for label in frame.overlay_labels))
        rgb = np.asarray(frame.convert("RGB"))
        self.assertGreater(self._near(rgb, K.YELLOW), 400)
        self.assertGreater(self._near(rgb, K.BLUE), 80)
        self.assertEqual(tuple(int(v) for v in rgb[110, 200]), K.RED)
        self.assertEqual(tuple(int(v) for v in rgb[110, 700]), K.GREEN)
        quiet = scene_frames.frames_for(_topic("pat_hammer"), "hook", 1, {"mode": "schematic"})[0][0]
        quiet_rgb = np.asarray(quiet.convert("RGB"))
        self.assertNotEqual(tuple(int(v) for v in quiet_rgb[110, 200]), K.RED)
        self.assertNotEqual(tuple(int(v) for v in quiet_rgb[110, 700]), K.GREEN)

    def test_overlay_order_follows_the_beat_and_stops_clear_the_extreme(self):
        for key in trade_plan.KEYS:
            plan = trade_plan.plan_for(key)
            candle = plan.ohlc[plan.focus]
            if plan.side == "long":
                self.assertLess(plan.sl, candle[2], key)
            else:
                self.assertGreater(plan.sl, candle[1], key)
            self.assertGreaterEqual(plan.rr, 1.5, key)
        beats = {
            beat: (index * 8.0, index * 8.0 + 8.0)
            for index, beat in enumerate(trade_plan.BEATS)
        }
        words = [
            {"word": "टारगेट", "start": 1.2, "end": 1.6},
            {"word": "एंट्री", "start": 50.0, "end": 50.4},
            {"word": "सपोर्ट", "start": 17.0, "end": 17.4},
            {"word": "स्टॉप", "start": 33.0, "end": 33.4},
        ]
        events = {
            row["kind"]: row
            for row in trade_plan.chart_overlay_events(_topic("pat_hammer"), beats, words)
        }
        self.assertAlmostEqual(events["sr"]["start"], 17.0)
        self.assertAlmostEqual(events["entry"]["start"], beats["entry"][0])
        self.assertAlmostEqual(events["sl"]["start"], 33.0)
        self.assertAlmostEqual(events["target"]["start"], beats["target"][0])
        self.assertLess(events["entry"]["start"], events["sl"]["start"])
        self.assertLess(events["sl"]["start"], events["target"]["start"])
        for kind, beat in (("sr", "context"), ("entry", "entry"), ("sl", "sl"), ("target", "target")):
            start = events[kind]["start"]
            self.assertGreaterEqual(start, beats[beat][0] - 0.05, kind)
            self.assertLessEqual(start, beats[beat][1] + 0.05, kind)

    @staticmethod
    def _near(rgb, color, tol=12):
        import numpy as np
        delta = np.abs(rgb.astype(np.int16) - np.array(color, dtype=np.int16)).sum(axis=2)
        return int((delta <= tol).sum())


class VoiceTests(unittest.TestCase):
    def test_topic_videos_accept_40_to_50_and_55_is_only_a_review(self):
        topic = _topic("pat_hammer")
        self.assertEqual(trade_plan.VOICE_SECONDS, (40.0, 50.0))
        self.assertEqual(trade_plan.VOICE_REVIEW_MAX, 55.0)
        self.assertEqual(board_short.live_voice_bounds(topic), (40.0, 55.0))
        self.assertEqual(board_short._output_bounds(topic, engine="gemini"), (40.0, 55.0))
        self.assertEqual(board_short._output_bounds(topic, engine="edge"), (40.0, 55.0))
        self.assertEqual(board_short._output_bounds(topic, engine="edge", proof=True), (40.0, 65.0))
        self.assertEqual(board_short._output_bounds(topic, engine="gemini", proof=True), (40.0, 55.0))
        self.assertEqual(board_short._gemini_pace_window(topic), (40.0, 50.0))
        self.assertEqual(board_short.gemini_accepts(46.0, trade_plan.VOICE_SECONDS), "target")
        self.assertEqual(board_short.gemini_accepts(52.0, trade_plan.VOICE_SECONDS), "review")
        self.assertEqual(board_short.gemini_accepts(56.0, trade_plan.VOICE_SECONDS), "miss")
        self.assertTrue(board_short._gemini_stop(52.0, trade_plan.VOICE_SECONDS, 0))
        self.assertFalse(board_short._gemini_stop(36.0, trade_plan.VOICE_SECONDS, 0))
        self.assertFalse(trade_plan.duration_blocks(52.0))
        self.assertTrue(trade_plan.duration_is_review(52.0))
        self.assertTrue(trade_plan.duration_blocks(56.0))
        self.assertTrue(trade_plan.duration_blocks(39.0))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(trade_plan.note_duration(52.4, topic))
            self.assertFalse(trade_plan.note_duration(46.0, topic))
        self.assertIn("[REVIEW] duration 52.4s", buf.getvalue())
        four = {"_live_short": True, "_live_beats": "four", "key": "plain"}
        self.assertEqual(board_short._output_bounds(four, engine="gemini"), (34.0, 65.0))
        self.assertEqual(board_short._gemini_pace_window(four), (34.0, 65.0))
        self.assertEqual(board_short._output_bounds(four, engine="edge"), (45.0, 65.0))
        self.assertEqual(board_short._output_bounds(four, engine="edge", proof=True), (45.0, 65.0))
        self.assertFalse(board_short._gemini_stop(37.0, (34.0, 65.0), 0))
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
                self.assertEqual(quality_check.short_duration_bounds(), (40.0, 55.0, "40-55s"))
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window(), (40.0, 55.0, "40-55s"))

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


class OwnerApprovedSevenTests(unittest.TestCase):
    def test_seven_owner_approved_scripts_pass_the_plan_gate(self):
        for key in OWNER_APPROVED_2026_10_22:
            script = approved_bank.by_key(key)
            self.assertIsNotNone(script, key)
            self.assertIs(script.get("owner_approved"), True, key)
            narration = approved_bank.narration_from(script)
            topic = _topic(key)
            self.assertEqual(trade_plan.script_fails(narration, topic), [], key)
            self.assertEqual(board_short.check_narration(narration, topic), [], key)
            plan = trade_plan.plan_for(key)
            self.assertGreaterEqual(plan.rr, 1.5, key)
            words = sum(len(narration["beats"][beat].split()) for beat in trade_plan.BEATS)
            self.assertLessEqual(words / trade_plan.CHARON_WPS, trade_plan.VOICE_SECONDS[1] + 1e-9, key)
            self.assertGreaterEqual(words / trade_plan.CHARON_WPS, trade_plan.VOICE_SECONDS[0], key)
            self.assertTrue(pattern_marks_exist(key), key)


def pattern_marks_exist(key):
    import pattern_charts
    marks = pattern_charts.marks_for(key)
    return all(marks.get(beat) for beat in trade_plan.BEATS)


class DarkCloudPehchanTests(unittest.TestCase):
    def test_pehchan_speaks_one_number(self):
        # 30-Oct: three prices in one beat ran into the context beat and failed sync.
        script = approved_bank.by_key("pat_dark_cloud")
        self.assertEqual(trade_plan._PLACEHOLDER.findall(script["beats"]["pehchan"]), ["mid"])


class EdgeProofFinalWindowTests(unittest.TestCase):
    def test_final_mp4_check_uses_edge_proof_window_only_in_proof(self):
        import inspect
        src = inspect.getsource(board_short)
        i = src.index("refusing post-render speed change: {final_dur")
        block = src[max(0, i - 900):i]
        self.assertIn("_output_bounds(topic, proof=edge_proof)", block)
        self.assertIn('PROOF_FREE_AUDIO', block)
        topic = _topic("pat_hammer")
        with mock.patch.object(board_short.eleven_voice, "load_voice_record", return_value={"engine": "edge"}):
            self.assertEqual(board_short._output_bounds(topic, proof=True), (40.0, 65.0))
            self.assertEqual(board_short._output_bounds(topic), (40.0, 55.0))
        with mock.patch.object(board_short.eleven_voice, "load_voice_record", return_value={"engine": "gemini"}):
            self.assertEqual(board_short._output_bounds(topic, proof=True), (40.0, 55.0))
