"""Scene pacing, ATR chart numbers, adult script, voice settings, and the craft gate."""
import json
import os
import re
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
        self.assertEqual(fresh["atr"], "15.7")
        fresh_ranges = self._true_ranges(fresh["window"])
        self.assertEqual(len(fresh_ranges), 14)
        self.assertEqual(f"{sum(fresh_ranges) / 14:.1f}", "15.7")
        self.assertEqual(daily["ticker"], "HDFCBANK.NS")
        self.assertEqual(str(market_data.ATR_DAILY_PATH.name), "hdfc_atr_daily.json")
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

    def test_short_voice_is_rudra(self):
        with patch.dict(os.environ, {"VIDEO_FORMAT": "short"}):
            voice = __import__("common").cfg()["voice"]
        self.assertEqual(voice["voice_name"], "Rudra")
        self.assertEqual(voice["voice_id"], "ypnkIsDASPgHZuanrF0q")
        self.assertNotEqual(voice["voice_id"], "7qBNUtXRGP0jPi0H4r8k")
        self.assertNotEqual(voice["voice_id"], "onwK4e9ZLuTAKqWW03F9")
        self.assertEqual(voice["model"], "eleven_multilingual_v2")
        self.assertEqual(voice["stability"], 0.50)
        self.assertEqual(voice["style"], 0.25)
        self.assertEqual(voice["similarity_boost"], 0.8)
        self.assertEqual(voice["speed"], 0.90)
        self.assertEqual(voice["words_per_second"], 1.9)
        self.assertEqual(voice["fallback_words_per_second"], 1.9)
        self.assertEqual(voice["fallback_voice_id"], "qsz5tTEjPvsiIJZIpM8S")
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
                expect = scene_frames.measure_ink(layer["text"], layer["size"])
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

    def test_no_tofu_glyph_on_any_atr_sprite(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=date(2026, 10, 2))
        self.assertTrue(scene_frames.missing_glyphs("PKP", scene_frames._FONT))
        frames = []
        for beat, n in (("hook", 3), ("pehchan", 3), ("matlab", 5), ("karein", 5)):
            made, _flags = scene_frames.frames_for(topic, beat, n, spec)
            frames.extend(made)
        for frame in frames:
            for layer in frame.text_layers:
                missing = scene_frames.layer_missing(layer)
                self.assertEqual(missing, [], f"{layer.get('text')!r} {missing}")
        brand = next(
            layer for layer in frames[0].text_layers
            if layer.get("text") == "@ThePaiseKiPathshala" and layer.get("persist")
        )
        self.assertEqual(brand["font"], scene_frames._SANS)

    def test_subtitle_box_holds_every_frame_including_a_long_line(self):
        import engagement

        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=date(2026, 10, 2))
        market_data.apply_atr_card(topic, spec["atr"])
        script = board_short.atr_narration(topic)
        times = {"hook": (0.0, 10.0), "pehchan": (10.0, 21.0), "matlab": (21.0, 38.0), "karein": (38.0, 55.0)}
        cta = engagement.engagement_question(topic)
        window = board_short._subtitle_limits()
        placed = board_short.layout_in_box([cta], window, 40, 18)
        flat = [row for group in placed for row in group]
        self.assertTrue(flat)
        self.assertLessEqual(max(row[1][3] for row in flat), safe_area.SUBTITLE_LIMIT)
        disclaimer = board_short._top_box(board_short.DISCLAIMER, 540, safe_area.SOURCE_Y, 26)
        self.assertFalse(safe_area.overlapping([row[1] for row in flat] + [disclaimer]))
        ass = board_short.build_ass(topic, times, script["beats"], script["hook_line"], cta)
        self.assertNotIn(cta, ass)
        self.assertNotIn(f"मात्रा {spec['atr']['qty']}, जोखिम {spec['atr']['risk']}", ass)
        groups = {}
        for scene in scene_pace.plan_scenes(times):
            groups.setdefault(scene["beat"], []).append(scene)
        frames = []
        scenes = []
        for beat, group in groups.items():
            made, _flags = scene_frames.frames_for(topic, beat, len(group), spec)
            for scene, frame in zip(group, made):
                scene["frame"] = frame
                scenes.append(scene)
                frames.append(frame)
        text_frames = board_short.frame_text_boxes(
            scenes, topic, times, script["beats"], script["hook_line"], cta)
        self.assertGreater(len(text_frames), 50 * 30)
        for t, boxes in text_frames:
            self.assertFalse(safe_area.overlapping(boxes), f"{t:.2f} {safe_area.overlapping(boxes)[:2]}")
            for box in boxes:
                if box[1] < safe_area.BRAND_TOP:
                    self.assertLessEqual(box[3], safe_area.SUBTITLE_LIMIT + 0.5, f"{t:.2f} {box}")


class LayoutGateTests(unittest.TestCase):
    def _lesson(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=date(2026, 10, 2))
        return spec["atr"]

    def test_ladder_labels_stay_visible_and_one_distance_number(self):
        lesson = self._lesson()
        for phase in (0.0, 0.5, 0.86):
            img = scene_frames._ladder_reveal(lesson, phase)
            self._labels_clear(img)
        for phase in (0.2, 0.66, 1.0):
            img = scene_frames._ladder_moving(lesson, phase)
            self._labels_clear(img)
            nums = set()
            blob = " ".join(layer.get("text") or "" for layer in img.text_layers)
            for layer in img.text_layers:
                if layer.get("quantity") == "distance":
                    nums.update(re.findall(r"\d+(?:\.\d+)?", layer.get("text") or ""))
            self.assertLessEqual(len(nums), 1, (phase, nums))
            if phase < 0.9:
                self.assertNotIn("पॉइंट", blob)
            else:
                self.assertIn(f"दूरी {lesson['distance']} पॉइंट", blob)

    def _labels_clear(self, img):
        blob = " ".join(layer.get("text") or "" for layer in img.text_layers)
        for needle in ("एंट्री", "795.9", "एसएल", "772.4", "ऊपर", "819.4"):
            self.assertIn(needle, blob)
        self.assertIn("जोखिम क्षेत्र", blob)
        self.assertEqual(scene_frames.frame_layout_notes(img), [])
        layers = [layer for layer in img.text_layers if layer.get("text") and not layer.get("faint")]
        for shape in img.shapes:
            if shape.get("kind") != "highlight":
                continue
            self.assertLess(shape["alpha"], 120)
            labelled = False
            for layer in layers:
                text = layer.get("text") or ""
                hit = safe_area.intersects(shape["box"], layer["box"])
                if text == "जोखिम क्षेत्र":
                    self.assertTrue(hit)
                    self.assertFalse(re.search(r"\d", text))
                    mid_y = (layer["box"][1] + layer["box"][3]) / 2.0
                    self.assertGreater(mid_y, shape["box"][1])
                    self.assertLess(mid_y, shape["box"][3])
                    labelled = True
                else:
                    self.assertFalse(hit, (shape["box"], text))
            self.assertTrue(labelled)

    def test_outro_has_no_empty_bar_and_a_large_poll(self):
        lesson = self._lesson()
        for step in (0, 1):
            img = scene_frames._cta_at(lesson, step, 2, 0.4)
            self.assertFalse(any(shape.get("kind") == "fill" for shape in img.shapes))
            self.assertEqual(scene_frames.frame_layout_notes(img), [])
        poll = [
            layer for layer in scene_frames._cta_at(lesson, 1, 2, 1.0).text_layers
            if layer.get("role") == "cta"
        ]
        self.assertGreaterEqual(len(poll), 2)
        self.assertGreaterEqual(min(layer["size"] for layer in poll), 40)
        self.assertTrue(any("हाँ" in layer["text"] for layer in poll))

    def test_card_uses_the_frame_and_the_caption_sits_under_it(self):
        height = scene_frames.CARD[3] - scene_frames.CARD[1]
        frac = height / safe_area.FRAME_H
        self.assertGreaterEqual(frac, 0.60)
        self.assertLessEqual(frac, 0.66)
        self.assertGreater(safe_area.SUBTITLE_TOP, scene_frames.CARD[3])
        self.assertLess(safe_area.SUBTITLE_TOP - scene_frames.CARD[3], 80)
        self.assertLess(safe_area.SUBTITLE_LIMIT, safe_area.SOURCE_Y)

    def test_opaque_cover_empty_box_and_two_numbers_fail(self):
        covered = scene_frames._blank()
        scene_frames._text(covered, 200, 400, "एंट्री  795.9", (255, 255, 255), 32, band=False)
        scene_frames._mark_shape(covered, (180, 360, 520, 460), 220, "fill")
        self.assertTrue(any("opaque" in note for note in scene_frames.frame_layout_notes(covered)))
        empty = scene_frames._blank()
        scene_frames._mark_shape(empty, (200, 400, 860, 760), 255, "fill")
        self.assertTrue(any("empty box" in note for note in scene_frames.frame_layout_notes(empty)))
        rule = scene_frames._blank()
        scene_frames._accent(rule, [200, 400, 860, 412])
        self.assertEqual(scene_frames.frame_layout_notes(rule), [])
        pair = scene_frames._blank()
        scene_frames._text(pair, 200, 300, "दूरी  23.5", (255, 220, 40), 36, band=False, quantity="distance")
        scene_frames._text(pair, 200, 420, "20.8", (255, 220, 40), 36, band=False, quantity="distance")
        self.assertTrue(any("two values" in note for note in scene_frames.frame_layout_notes(pair)))
        orphan = scene_frames._blank()
        scene_frames._text(orphan, 540, 400, "पॉइंट", (255, 255, 255), 32, anchor="mt", band=False)
        self.assertTrue(any("lone unit" in note for note in scene_frames.frame_layout_notes(orphan)))
        named = scene_frames._blank()
        scene_frames._text(named, 540, 300, "23.5", (255, 220, 40), 96, anchor="mt", band=False)
        scene_frames._text(named, 540, 460, "पॉइंट", (255, 255, 255), 36, anchor="mt", band=False)
        self.assertEqual(scene_frames.frame_layout_notes(named), [])

    def test_a_short_card_is_empty_background_and_the_atr_card_is_not(self):
        import numpy as np

        short = np.zeros((1920, 1080, 3), dtype=np.uint8)
        short[:768, :, :] = 180
        notes = scene_pace.empty_background_spans([(0.0, short), (4.0, short.copy())])
        self.assertTrue(any("empty background" in note for note in notes))
        lesson = self._lesson()
        for img in (
            scene_frames._hook(lesson),
            scene_frames._ladder_moving(lesson, 0.5),
            scene_frames._cta_at(lesson, 1, 2, 1.0),
        ):
            self.assertLessEqual(scene_pace.empty_row_fraction(img), scene_pace.EMPTY_MAX_FRAC)

    def test_one_cue_at_a_time_and_never_before_its_words(self):
        early = [{"t0": 0.0, "t1": 2.0, "text": "तंग एसएल", "lines": ["तंग एसएल"], "word_start": 1.0}]
        early_notes = board_short.cue_faults(early)
        self.assertTrue(any("before audio" in note for note in early_notes))
        overlap = [
            {"t0": 0.0, "t1": 2.0, "text": "पहला", "lines": ["पहला"], "word_start": 0.0},
            {"t0": 1.0, "t1": 3.0, "text": "दूसरा", "lines": ["दूसरा"], "word_start": 1.0},
        ]
        overlap_notes = board_short.cue_faults(overlap)
        self.assertTrue(any("two cues" in note for note in overlap_notes))
        narration = {"beats": {"hook": "x", "pehchan": "x", "matlab": "x", "karein": "x"}}
        scenes = scene_pace.plan_scenes(
            {"hook": (0.0, 8.0), "pehchan": (8.0, 16.0), "matlab": (16.0, 28.0), "karein": (28.0, 42.0)})
        craft, notes = self_review.score_craft(narration, scenes=scenes, layout_notes=early_notes)
        self.assertEqual(craft, 0)
        self.assertTrue(any("before audio" in note for note in notes))
        craft, notes = self_review.score_craft(narration, scenes=scenes, layout_notes=overlap_notes)
        self.assertEqual(craft, 0)
        self.assertTrue(any("two cues" in note for note in notes))

        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=date(2026, 10, 2))
        market_data.apply_atr_card(topic, spec["atr"])
        script = board_short.atr_narration(topic)
        times = {"hook": (0.0, 10.0), "pehchan": (10.0, 21.0), "matlab": (21.0, 38.0), "karein": (38.0, 55.0)}
        audio = []
        for beat, (t0, t1) in times.items():
            words = script["beats"][beat].split()
            span = (t1 - t0) * 0.8
            for i, word in enumerate(words):
                start = t0 + 0.35 + span * i / max(1, len(words))
                audio.append({"word": word, "start": start, "end": start + 0.25})
        board_short._CUE_CACHE.clear()
        cues = board_short.plan_cues(script["beats"], times, audio)
        self.assertEqual(board_short.cue_faults(cues), [])
        for cue in cues:
            self.assertLessEqual(len(cue["lines"]), 2)
            self.assertGreaterEqual(cue["t0"] + 1e-6, cue["word_start"] - 0.02)
        for moment in (0.5, 1.5, 3.0, 12.0, 30.0, 35.0, 42.0):
            self.assertEqual(len(board_short.cues_at(cues, moment)), 1, moment)
        self.assertEqual(board_short.cues_at(cues, 0.05), [])
        ass = board_short.build_ass(topic, times, script["beats"], script["hook_line"], "poll", audio)
        self.assertNotIn(f"मात्रा {spec['atr']['qty']}, जोखिम {spec['atr']['risk']}", ass)

    def test_a_repeated_label_fails_and_the_hook_says_it_once(self):
        lesson = self._lesson()
        hook = scene_frames._hook(lesson)
        blob = " ".join(layer.get("text") or "" for layer in hook.text_layers)
        self.assertEqual(blob.count("सामान्य हलचल"), 1)
        self.assertEqual(scene_frames.frame_layout_notes(hook), [])
        pair = scene_frames._blank()
        scene_frames._text(pair, 540, 400, "सामान्य हलचल", (255, 255, 255), 42, anchor="mt", band=False)
        scene_frames._text(pair, 430, 900, "सामान्य हलचल", (255, 220, 40), 34, anchor="mt", band=False)
        self.assertTrue(any("repeated text" in note for note in scene_frames.frame_layout_notes(pair)))
        # The burned cue is not a second sprite. Two labels still fail if the cue matches them.
        layers = [layer for layer in pair.text_layers if layer.get("text")]
        self.assertTrue(scene_frames.repeated_text_notes(
            layers, extra_texts=["सामान्य हलचल"], active_caption="सामान्य हलचल"))
        one = scene_frames._blank()
        scene_frames._text(one, 540, 400, "सामान्य हलचल", (255, 255, 255), 42, anchor="mt", band=False)
        only = [layer for layer in one.text_layers if layer.get("text")]
        self.assertEqual(scene_frames.repeated_text_notes(
            only, extra_texts=["सामान्य हलचल"], active_caption="सामान्य हलचल"), [])

    def test_caption_numbers_use_the_on_screen_rounding(self):
        self.assertTrue(board_short.number_format_notes(
            ["एसएल  772.4"], spoken="एसएल 772.4", caption="एसएल 772.41"))
        self.assertEqual(board_short.number_format_notes(
            ["एसएल  772.4", "एंट्री  795.9"], spoken="एसएल 772.4", caption="एसएल 772.4।"), [])
        lesson = self._lesson()
        self.assertEqual(lesson["sl"], "772.4")
        self.assertEqual(lesson["sl"], market_data.format_figure(lesson["sl"], 1))
        self.assertNotEqual(market_data.format_figure("772.41", 1), "772.41")
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "atr_sl")
        spec = market_data.spec_for_topic(topic, today=date(2026, 10, 2))
        market_data.apply_atr_card(topic, spec["atr"])
        script = board_short.atr_narration(topic)
        self.assertIn("772.4", script["beats"]["matlab"])
        self.assertNotIn("772.41", " ".join(script["beats"].values()))
        times = {"hook": (0.0, 10.58), "pehchan": (10.58, 21.11), "matlab": (21.11, 38.33), "karein": (38.33, 55.07)}
        audio = []
        for beat, (t0, t1) in times.items():
            words = script["beats"][beat].split()
            span = max(0.2, (t1 - t0) - 0.4)
            for i, word in enumerate(words):
                start = t0 + span * (i + 0.15) / max(1, len(words))
                audio.append({"word": word, "start": start, "end": start + 0.28})
        scenes = scene_pace.plan_scenes(times)
        groups = {}
        for scene in scenes:
            groups.setdefault(scene["beat"], []).append(scene)
        built = []
        for beat, group in groups.items():
            frames, _flags = scene_frames.frames_for(topic, beat, len(group), spec)
            for scene, frame in zip(group, frames):
                scene["frame"] = frame
                built.append(scene)
        board_short._CUE_CACHE.clear()
        faults = board_short.number_faults_for(built, script["beats"], times, audio)
        self.assertEqual(faults, [], faults)
        for frame in built:
            if getattr(frame.get("frame"), "market_as_of", ""):
                self.assertEqual(scene_frames.frame_layout_notes(frame["frame"]), [])

    def test_stale_close_blocks_public_upload_and_the_sample_flag_stays_off_that_path(self):
        today = date(2026, 10, 2)
        self.assertEqual(market_data.weekdays_since_close("2026-09-29", today), 3)
        self.assertTrue(market_data.close_is_fresh("2026-09-29", today))
        self.assertEqual(market_data.weekdays_since_close("2026-09-28", today), 4)
        self.assertFalse(market_data.close_is_fresh("2026-09-28", today))
        self.assertFalse(market_data.close_is_fresh("2026-07-02", today))
        self.assertEqual(market_data.weekdays_since_close(today, today), 0)
        # A Friday close is still fresh on the following Monday (the weekend does not count).
        self.assertEqual(market_data.weekdays_since_close("2026-10-02", date(2026, 10, 5)), 1)
        self.assertTrue(board_short.stale_data_notes("2026-07-02", today, allow_stale_sample=False))
        self.assertEqual(board_short.stale_data_notes("2026-07-02", today, allow_stale_sample=True), [])
        self.assertEqual(board_short.stale_data_notes("2026-09-29", today, allow_stale_sample=False), [])
        narration = {"beats": {"hook": "नियम 14", "pehchan": "नियम 14", "matlab": "नियम 14", "karein": "नियम 14"}}
        scenes = scene_pace.plan_scenes(
            {"hook": (0.0, 8.0), "pehchan": (8.0, 16.0), "matlab": (16.0, 28.0), "karein": (28.0, 42.0)})
        craft, notes = self_review.score_craft(
            narration, scenes=scenes,
            layout_notes=board_short.stale_data_notes("2026-07-02", today))
        self.assertEqual(craft, 0)
        self.assertTrue(any("stale market data" in note for note in notes))
        hidden = scene_frames._blank()
        hidden.market_as_of = "2026-07-02"
        self.assertTrue(any("as-of date" in note for note in scene_frames.frame_layout_notes(hidden)))
        craft, notes = self_review.score_craft(
            narration, scenes=scenes, layout_notes=["as-of date is not on screen"])
        self.assertEqual(craft, 0)
        self.assertTrue(market_data.public_upload_block("2026-07-02", today, True, "public", None))
        self.assertTrue(market_data.public_upload_block(
            "2026-09-29", today, False, "private", "2026-10-02T15:30:00Z"))
        self.assertEqual(market_data.public_upload_block("2026-07-02", today, True, "unlisted", None), "")
        self.assertEqual(market_data.public_upload_block("2026-09-29", today, True, "public", None), "")
        self.assertNotIn("allow_stale", market_data.public_upload_block.__code__.co_varnames)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "market_freshness.json"
            stamp.write_text(json.dumps({
                "as_of": "2026-07-02", "on_screen": True, "allow_stale_sample": True,
            }), encoding="utf-8")
            reason = market_data.public_freshness_reason(tmp, "public", None, today)
            self.assertIn("stale", reason)
            self.assertIn("stale", market_data.public_freshness_reason(
                tmp, "private", "2026-10-02T15:30:00Z", today))
            self.assertEqual(market_data.public_freshness_reason(tmp, "unlisted", None, today), "")
        for name in ("publish.py", "run_pipeline.py", ".github/workflows/paise-ki-pathshala.yml"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("allow_stale_sample", text, name)
        src = (ROOT / "board_short.py").read_text(encoding="utf-8")
        self.assertIn("topic[\"_allow_stale_sample\"] = bool(args.key)", src)
        lesson = self._lesson()
        for img in (
            scene_frames._problem(lesson, 1, 2),
            scene_frames._formula(lesson, 2, 3),
            scene_frames._ladder_moving(lesson, 1.0),
            scene_frames._split(lesson),
        ):
            blob = " ".join(layer.get("text") or "" for layer in img.text_layers)
            self.assertIn(lesson["as_of"], blob)
            self.assertEqual(scene_frames.frame_layout_notes(img), [])

    def test_watermark_misses_chart_and_card_content(self):
        lesson = self._lesson()
        for img in (
            scene_frames._problem(lesson, 0, 2),
            scene_frames._problem(lesson, 1, 2),
            scene_frames._split(lesson),
            scene_frames._ladder_moving(lesson, 1.0),
        ):
            self.assertFalse(any("watermark" in note for note in scene_frames.frame_layout_notes(img)))
            brands = [layer for layer in img.text_layers if layer.get("role") == "brand"]
            self.assertTrue(brands)
            for brand in brands:
                for box in img.content_boxes:
                    self.assertFalse(safe_area.intersects(brand["box"], box), (brand["box"], box))
        covered = scene_frames._blank()
        scene_frames._card(covered)
        scene_frames._text(covered, 540, 700, "795.9", (255, 255, 255), 48, anchor="mm", band=False)
        scene_frames._text(covered, 540, 700, "PKP", (212, 168, 72), 48, anchor="mm", band=False, role="brand")
        self.assertTrue(any("watermark" in note for note in scene_frames.frame_layout_notes(covered)))


class GlyphTests(unittest.TestCase):
    def test_a_formula_arrow_still_draws(self):
        self.assertNotIn("→", scene_frames.drawable_text("P/E 30 → PEG 1"))
        self.assertTrue(scene_frames._runs("P/E 30 → PEG 1"))


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
