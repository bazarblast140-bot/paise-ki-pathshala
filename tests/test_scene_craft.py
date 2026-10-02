"""Scene pacing, ATR chart numbers, adult script, voice settings, and the craft gate."""
import json
import os
import subprocess
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import board_short
import market_data
import safe_area
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
    def _true_ranges(self, window):
        ranges = []
        for i in range(1, len(window)):
            prev = float(window[i - 1]["close"])
            high, low = float(window[i]["high"]), float(window[i]["low"])
            ranges.append(max(high - low, abs(high - prev), abs(low - prev)))
        return ranges

    def test_chart_numbers_match_the_daily_true_range(self):
        lesson = market_data.atr_lesson(today=TODAY)
        self.assertEqual(lesson["source"], "Yahoo Finance")
        # 2026-10-01 is three months after 2026-07-01, the newest print the rule allows.
        self.assertEqual(lesson["as_of"], "2026-07-01")
        self.assertNotEqual(lesson["as_of"], "2026-06-04")
        self.assertTrue(market_data.stock_price_is_old_enough(lesson["as_of"], TODAY))
        self.assertEqual(lesson["name"], "HDFC BANK")
        self.assertEqual(len(lesson["points"]), 15)
        self.assertEqual(lesson["atr"], f"{sum(self._true_ranges(lesson['window'])) / 14:.1f}")
        self.assertNotEqual(lesson["atr"], "36.7")
        self.assertEqual(lesson["distance"], f"{float(lesson['atr']) * 1.5:.1f}")
        self.assertEqual(lesson["entry"], f"{lesson['points'][-1]['close']:.1f}")
        self.assertEqual(lesson["sl"], f"{float(lesson['entry']) - float(lesson['distance']):.1f}")
        self.assertEqual(lesson["target"], f"{float(lesson['entry']) + float(lesson['distance']):.1f}")
        self.assertTrue(lesson["close_matches_adj"])
        self.assertNotIn("2026-06-26", [p["date"] for p in lesson["points"]])
        daily = market_data.load_atr_daily()
        source = [
            {"date": b["date"], "close": b["close"]}
            for b in daily["bars"] if b["volume"] > 0 and b["date"] <= lesson["as_of"]
        ]
        self.assertFalse(market_data.values_are_fabricated(lesson["points"], source))
        fresh = market_data.atr_lesson(today=date(2026, 10, 2))
        self.assertEqual(fresh["as_of"], "2026-07-02")
        self.assertEqual(fresh["entry"], "795.9")
        self.assertTrue(fresh["close_matches_adj"])
        self.assertTrue(market_data.stock_price_is_old_enough(fresh["as_of"], date(2026, 10, 2)))

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
        self.assertTrue(hook.startswith("तंग एसएल बार-बार कटता क्यों है?"))
        self.assertNotIn(lesson["atr"], hook)
        self.assertNotIn(lesson["distance"], hook)
        self.assertIn(lesson["atr"], narration["beats"]["pehchan"])
        self.assertIn(lesson["distance"], narration["beats"]["matlab"])
        self.assertIn(lesson["entry"], narration["beats"]["matlab"])
        self.assertIn(lesson["sl"], narration["beats"]["matlab"])
        self.assertIn(lesson["target"], narration["beats"]["matlab"])
        self.assertIn("गलती", narration["beats"]["karein"])
        self.assertIn("सलाह नहीं", narration["beats"]["karein"])
        self.assertIn(str(lesson["qty"]), narration["beats"]["karein"])
        self.assertIn(lesson["risk"], narration["beats"]["karein"])
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
        with patch.dict(os.environ, {"VIDEO_FORMAT": "short"}):
            voice = __import__("common").cfg()["voice"]
        self.assertEqual(voice["voice_name"], "Kamal")
        self.assertEqual(voice["voice_id"], "qsz5tTEjPvsiIJZIpM8S")
        self.assertNotEqual(voice["voice_id"], "onwK4e9ZLuTAKqWW03F9")
        self.assertEqual(voice["model"], "eleven_multilingual_v2")
        self.assertGreaterEqual(voice["stability"], 0.7)
        self.assertLessEqual(voice["style"], 0.05)
        self.assertLessEqual(voice["speed"], 1.05)
        self.assertEqual(voice["edge_voice"], "hi-IN-MadhurNeural")
        rate = int(str(voice["edge_rate"]).replace("%", "").replace("+", ""))
        self.assertGreaterEqual(rate, 0)
        self.assertLessEqual(rate, 10)
        self.assertNotIn("Swara", voice["edge_voice"])

    def test_scene_types_are_different_and_the_chart_is_rare(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=TODAY)
        times = {"hook": (0.0, 9.0), "pehchan": (9.0, 20.0), "matlab": (20.0, 34.0), "karein": (34.0, 52.0)}
        groups = {}
        for scene in scene_pace.plan_scenes(times):
            groups.setdefault(scene["beat"], []).append(scene)
        kinds = []
        for beat, group in groups.items():
            planned = scene_frames.kinds_for(beat, len(group))
            frames, _flags = scene_frames.frames_for(topic, beat, len(group), spec)
            self.assertEqual([f.scene_kind for f in frames], planned)
            kinds.extend(planned)
            for frame in frames:
                ok, bad = safe_area.check_boxes(frame.label_boxes)
                self.assertTrue(ok, bad)
                self.assertTrue(frame.label_boxes)
        self.assertTrue({"hook", "problem", "formula", "ladder", "split", "size", "takeaway", "cta"} <= set(kinds))
        charts = [k for k in kinds if k in scene_frames.CHART_KINDS]
        self.assertLessEqual(len(charts), 3)
        self.assertGreaterEqual(len(charts), 1)

    def test_a_label_outside_the_margin_fails_the_gate(self):
        topic = _hammer()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "series": [], "fabricated": False}
        overlays = short_layout.overlay_plan(
            topic, {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)},
            narration["hook_line"])
        leaked = self_review.full_review(
            topic, narration, spec, overlays, trends={}, labels=[(0, 0, 40, 40)])
        self.assertEqual(leaked["parts"]["craft"], 0)
        self.assertFalse(leaked["public_ok"])
        self.assertIn("6%", leaked["summary"])
        self.assertEqual(self_review.PART_BAR, 60)
        self.assertEqual(self_review.OVERALL_BAR, 75)
        clean = self_review.full_review(topic, narration, spec, overlays, trends={})
        self.assertEqual(clean["parts"]["craft"], 100)

    def test_transitions_include_slide_wipe_and_scale(self):
        names = board_short.SCENE_TRANSITIONS
        self.assertIn("slideleft", names)
        self.assertIn("wipeleft", names)
        self.assertIn("zoomin", names)
        self.assertLessEqual(board_short.SCENE_FADE_S, 0.45)

    def test_shaped_lines_survive_every_transition(self):
        """Mid-wipe text is one pre-shaped sprite. A cropped sprite must fail."""
        import numpy as np
        from PIL import Image, ImageDraw, features

        import scene_transition

        self.assertTrue(features.check("raqm"))
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=TODAY)
        frames = []
        for beat, n in (("hook", 3), ("pehchan", 2), ("matlab", 2), ("karein", 5)):
            made, _flags = scene_frames.frames_for(topic, beat, n, spec)
            frames.extend(made)
        ctas = [frame for frame in frames if frame.scene_kind == "cta"]
        self.assertGreaterEqual(len(ctas), 2)
        differ = float(np.abs(np.asarray(ctas[0]).astype(int) - np.asarray(ctas[1]).astype(int)).mean())
        self.assertGreater(differ, 0.4)
        probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
        for frame in frames:
            self.assertIsNotNone(frame.plate)
            texts = [layer for layer in frame.text_layers if layer.get("text") and not layer.get("faint")]
            self.assertTrue(texts)
            for layer in texts:
                self.assertTrue(layer["shaped"])
                self.assertIsInstance(layer["image"], Image.Image)
                self.assertEqual(layer["image"].mode, "RGBA")
                ink = np.asarray(layer["image"])
                cols = np.flatnonzero(ink[:, :, 3].max(axis=0) > 20)
                self.assertGreater(cols.size, 2)
                ink_w = int(cols[-1] - cols[0] + 1)
                box = probe.textbbox((0, 0), layer["text"], font=scene_frames._font(layer["size"]))
                expect = box[2] - box[0]
                self.assertGreater(expect, 0)
                self.assertAlmostEqual(ink_w, expect, delta=max(12, expect * 0.2))
        names = board_short.SCENE_TRANSITIONS
        for i in range(len(frames) - 1):
            for t in (0.5, 0.82):
                notes = scene_transition.sliced_glyph_notes(
                    frames[i], frames[i + 1], t, names[i % len(names)])
                self.assertEqual(notes, [], f"{names[i % len(names)]} t={t} {notes}")
        self.assertEqual(scene_transition.mode_for("smoothleft")[0], "wipe")
        self.assertEqual(scene_transition.mode_for("zoomin")[0], "scale")
        self.assertEqual(scene_transition.mode_for("slideleft")[0], "slide")
        takeaway = next(frame for frame in frames if frame.scene_kind == "takeaway")
        layer = next(
            item for item in takeaway.text_layers
            if item.get("text") and not item.get("faint") and not item.get("persist")
        )
        plate = scene_transition.blend_plates(takeaway.plate, ctas[0].plate, 0.5, "wipeleft")
        rendered = plate.copy()
        sprite = layer["image"]
        half = sprite.crop((0, 0, max(1, sprite.size[0] // 2), sprite.size[1]))
        rendered.paste(half, layer["xy"], half)
        self.assertLess(scene_transition.ink_column_coverage(layer, plate, rendered), 0.85)
        whole = scene_transition.composite(takeaway, ctas[0], 0.5, "wipeleft")
        self.assertGreater(scene_transition.ink_column_coverage(layer, plate, whole), 0.85)


class MotionGateTests(unittest.TestCase):
    def test_a_still_span_fails_even_when_the_cuts_are_short(self):
        import numpy as np

        topic = _hammer()
        narration = board_short.fallback_narration(topic)
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        scenes = scene_pace.plan_scenes(times)
        ok, notes = scene_pace.check_scenes(scenes, 48.0)
        self.assertTrue(ok, notes)
        still = np.zeros((24, 24, 3), dtype=np.uint8)
        samples = [(0.0, still), (4.0, still.copy())]
        self.assertTrue(scene_pace.unchanged_spans(samples))
        craft, craft_notes = self_review.score_craft(narration, scenes=scenes, samples=samples)
        self.assertEqual(craft, 0)
        self.assertTrue(any("visual change" in note for note in craft_notes))
        moved = np.full((24, 24, 3), 80, dtype=np.uint8)
        clean, clean_notes = self_review.score_craft(
            narration, scenes=scenes, samples=[(0.0, still), (4.0, moved)])
        self.assertEqual(clean, 100, clean_notes)
        self.assertEqual(scene_pace.VISUAL_SPAN_S, 4.0)

    def test_overlapping_sprites_fail_and_the_bands_stay_apart(self):
        import json
        from datetime import date

        import market_data
        import numpy as np
        import scene_transition

        topic = _hammer()
        narration = board_short.fallback_narration(topic)
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        scenes = scene_pace.plan_scenes(times)
        craft, notes = self_review.score_craft(
            narration, scenes=scenes,
            text_frames=[(52.0, [(100, 100, 220, 180), (160, 140, 280, 220)])])
        self.assertEqual(craft, 0)
        self.assertTrue(any("overlap" in note for note in notes))
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        atr = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(atr, today=date(2026, 10, 2))
        market_data.apply_atr_card(atr, spec["atr"])
        script = board_short.atr_narration(atr)
        beat_times = {"hook": (0.0, 10.0), "pehchan": (10.0, 21.0), "matlab": (21.0, 38.0), "karein": (38.0, 55.0)}
        groups = {}
        for scene in scene_pace.plan_scenes(beat_times):
            groups.setdefault(scene["beat"], []).append(scene)
        frames = []
        for beat, group in groups.items():
            made, _flags = scene_frames.frames_for(atr, beat, len(group), spec)
            frames.extend(made)
        durations = [3.4] * len(frames)
        # The poll is on during the last scene. Its box must miss the grey disclaimer.
        moment = sum(durations) - 1.5
        frame = scene_transition.render_at(frames, durations, moment)
        boxes = [
            layer["box"] for layer in frame.text_layers
            if layer.get("text") and layer.get("box") and not layer.get("faint")
        ]
        boxes.extend(board_short.on_screen_boxes(
            atr, beat_times, script["beats"], script["hook_line"],
            __import__("engagement").engagement_question(atr), 53.5))
        self.assertFalse(safe_area.overlapping(boxes), safe_area.overlapping(boxes)[:3])
        moving = [frame for frame in frames if getattr(frame, "animate", None)]
        self.assertGreaterEqual(len(moving), 2)
        early = np.asarray(moving[0].animate(0).convert("RGB"))
        later = np.asarray(moving[0].animate(1).convert("RGB"))
        delta = float(np.abs(early.astype(np.int16) - later.astype(np.int16)).mean())
        self.assertGreaterEqual(delta, scene_pace.VISUAL_MIN_DELTA)


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
