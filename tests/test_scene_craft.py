"""Scene pacing, ATR chart numbers, adult script, voice settings, and the craft gate."""
import json
import os
import subprocess
import unittest
from datetime import date
from pathlib import Path

import board_short
import market_data
import scene_frames
import scene_pace
import self_review
import short_layout

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)


def _hammer():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना", "ये संभावना है, गारंटी नहीं"],
    }


class ScenePaceTests(unittest.TestCase):
    def test_slices_stay_under_four_seconds_and_cover_the_duration(self):
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        scenes = scene_pace.plan_scenes(times)
        self.assertGreaterEqual(len(scenes), 48 / 4)
        self.assertTrue(all(s["end"] - s["start"] <= 4.0 + 1e-9 for s in scenes))
        ok, notes = scene_pace.check_scenes(scenes, 48.0)
        self.assertTrue(ok, notes)
        self.assertGreater(len(scenes), 4)

    def test_a_static_scene_fails(self):
        ok, notes = scene_pace.check_scenes([{"start": 0.0, "end": 8.0}], 8.0)
        self.assertFalse(ok)
        self.assertTrue(any("5" in n or "duration/4" in n for n in notes))


class CraftGateTests(unittest.TestCase):
    def _overlays(self, topic, line):
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        return short_layout.overlay_plan(topic, times, line)

    def test_derived_scenes_still_pass_a_concrete_lesson(self):
        topic = _hammer()
        narration = board_short.fallback_narration(topic)
        self.assertEqual(scene_pace.vague_beats(narration["beats"]), [])
        spec = {"mode": "schematic", "source": None, "as_of": None, "series": [], "fabricated": False}
        review = self_review.full_review(
            topic, narration, spec, self._overlays(topic, narration["hook_line"]), trends={})
        self.assertEqual(review["parts"]["craft"], 100)
        self.assertTrue(review["public_ok"])
        self.assertEqual(self_review.PART_BAR, 60)
        self.assertEqual(self_review.OVERALL_BAR, 75)

    def test_static_chart_and_vague_line_fail_without_moving_the_bar(self):
        topic = _hammer()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "series": [], "fabricated": False}
        overlays = self._overlays(topic, narration["hook_line"])
        static = self_review.full_review(
            topic, narration, spec, overlays, trends={},
            scenes=[{"beat": "hook", "start": 0.0, "end": 48.0, "index": 0}])
        self.assertEqual(static["parts"]["craft"], 0)
        self.assertFalse(static["public_ok"])
        vague = json.loads(json.dumps(narration))
        vague["beats"]["matlab"] = " ".join(["समझिए"] * 50)
        missed = self_review.full_review(topic, vague, spec, overlays, trends={})
        self.assertEqual(missed["parts"]["craft"], 0)
        self.assertFalse(missed["public_ok"])
        self.assertIn("matlab", missed["summary"])


class AtrLessonTests(unittest.TestCase):
    def test_chart_numbers_match_the_monthly_closes(self):
        lesson = market_data.atr_lesson(today=TODAY)
        self.assertEqual(lesson["source"], "Yahoo Finance")
        self.assertEqual(lesson["as_of"], "2026-06-04")
        self.assertEqual(lesson["name"], "HDFC BANK")
        self.assertEqual(len(lesson["points"]), 15)
        closes = [p["close"] for p in lesson["points"]]
        changes = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
        self.assertEqual(lesson["atr"], f"{sum(changes) / len(changes):.1f}")
        self.assertEqual(lesson["distance"], f"{float(lesson['atr']) * 1.5:.1f}")
        self.assertEqual(lesson["entry"], f"{closes[-1]:.1f}")
        self.assertEqual(lesson["sl"], f"{float(lesson['entry']) - float(lesson['distance']):.1f}")
        self.assertEqual(lesson["target"], f"{float(lesson['entry']) + float(lesson['distance']):.1f}")
        cache = market_data.load_cache()
        source = market_data.series_from_cache("HDFC BANK", cache)["points"]
        self.assertFalse(market_data.values_are_fabricated(lesson["points"], source))

    def test_script_uses_those_numbers_and_teaches_in_order(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=TODAY)
        self.assertEqual(spec["mode"], "series")
        market_data.apply_atr_card(topic, spec["atr"])
        narration = board_short.atr_narration(topic)
        fails = board_short.check_narration(narration, topic)
        self.assertEqual(fails, [], fails)
        self.assertEqual(scene_pace.vague_beats(narration["beats"]), [])
        lesson = spec["atr"]
        hook = narration["beats"]["hook"]
        self.assertTrue(hook.startswith("तंग SL बार-बार कटता क्यों है?"))
        self.assertNotIn(lesson["atr"], hook)
        self.assertNotIn(lesson["distance"], hook)
        self.assertIn(lesson["atr"], narration["beats"]["pehchan"])
        self.assertIn(lesson["distance"], narration["beats"]["matlab"])
        self.assertIn(lesson["entry"], narration["beats"]["matlab"])
        self.assertIn(lesson["sl"], narration["beats"]["matlab"])
        self.assertIn(lesson["target"], narration["beats"]["matlab"])
        self.assertIn("गलती", narration["beats"]["karein"])
        self.assertIn("सलाह नहीं", narration["beats"]["karein"])
        frames, flags = scene_frames.frames_for(topic, "matlab", 4, spec)
        self.assertEqual(len(frames), 4)
        self.assertTrue(any(flags))
        import numpy as np
        a = np.asarray(frames[0])
        b = np.asarray(frames[1])
        self.assertGreater(float(np.abs(a.astype(int) - b.astype(int)).mean()), 0.4)
        joined = " ".join(narration["beats"].values()).lower()
        self.assertNotIn("खरीद लो", joined)
        self.assertNotIn("टारगेट", joined)

    def test_short_voice_is_the_calm_male_narrator(self):
        with unittest.mock.patch.dict(os.environ, {"VIDEO_FORMAT": "short"}):
            voice = __import__("common").cfg()["voice"]
        self.assertEqual(voice["voice_name"], "Daniel")
        self.assertEqual(voice["voice_id"], "onwK4e9ZLuTAKqWW03F9")
        self.assertEqual(voice["model"], "eleven_multilingual_v2")
        self.assertGreaterEqual(voice["stability"], 0.65)
        self.assertLessEqual(voice["style"], 0.15)
        self.assertEqual(voice["edge_voice"], "hi-IN-MadhurNeural")
        self.assertNotIn("Swara", voice["edge_voice"])


class BedTests(unittest.TestCase):
    def test_bed_is_in_the_ducking_window_and_licensed(self):
        bed = ROOT / "assets" / "bed" / "bed.wav"
        note = (ROOT / "assets" / "bed" / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("CC0", note)
        self.assertTrue((ROOT / "assets" / "bed" / "whoosh.wav").exists())
        self.assertTrue((ROOT / "assets" / "bed" / "click.wav").exists())
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(bed), "-af", "ebur128=framelog=verbose", "-f", "null", "-"],
            capture_output=True, text=True)
        text = proc.stderr
        line = next(l for l in text.splitlines() if l.strip().startswith("I:"))
        lufs = float(line.split()[1])
        self.assertGreaterEqual(lufs, -24.0)
        self.assertLessEqual(lufs, -20.0)
