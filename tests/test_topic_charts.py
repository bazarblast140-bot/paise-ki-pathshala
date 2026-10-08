"""Topic charts stay on their own scene. The daily-loss board is unchanged."""
import hashlib
import json
import unittest
from pathlib import Path

import board_kit
import board_scenes
import board_short
import sync_gate

ROOT = Path(__file__).resolve().parents[1]
LOSS_SPOKEN = {
    "hook": "दो लाख रुपये की पूँजी पर बुरा दिन पूरा महीना बिगाड़ देता है।",
    "pehchan": "अब इसे पहचानें। पूँजी दो लाख रुपये है, तो दिन की हद दो प्रतिशत यानी चार हज़ार रुपये बैठती है।",
    "matlab": "दिन की हद पर रुकना है। नुकसान चार हज़ार रुपये पर रुकता है।",
    "karein": "हद हिट होते ही टर्मिनल बंद। चार हज़ार रुपये तय करो।",
}
LOSS_HASH = {
    "hook": "1488e0a5221893a813540bd019fe801a26ac85bc44c4b87702144fe2d9ef354f",
    "pehchan": "089d63691db96bfbabbab33628ce8c5b79cccf20844f9db7c3f05978c23e38a6",
    "matlab": "ba6af3886538fe5cf09fbe659cc82c3bf7b5529f238ab3b6d1505a3462349dfb",
    "karein": "0d77af6fbbe73f23e6aca4370941b92f693bb34246b8a2ab5abe73c9cc48fedd",
    "cover": "0db63179f571537853442905379d24323c998bc76a9b8e1be171ac4179afc9eb",
}


def _bank_topic(key):
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = json.loads(json.dumps(next(item for item in bank["topics"] if item["key"] == key)))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    return topic


def _candle_counts(img):
    import numpy as np
    x0, y0, x1, y1 = board_kit.BOARD
    arr = np.asarray(img.convert("RGB"))[int(y0):int(y1), int(x0):int(x1)]
    red = (arr[:, :, 0] > 200) & (arr[:, :, 1] < 90) & (arr[:, :, 2] < 90)
    green = (arr[:, :, 1] > 160) & (arr[:, :, 0] < 80) & (arr[:, :, 2] < 140)
    return int(red.sum()), int(green.sum())


class DailyLossRegressionTests(unittest.TestCase):
    def test_risk_daily_loss_limit_pixels_stay_the_same(self):
        topic = _bank_topic("risk_daily_loss_limit")
        topic["_spoken_beats"] = dict(LOSS_SPOKEN)
        for beat, digest in LOSS_HASH.items():
            if beat == "cover":
                continue
            topic["_scene_cards"] = {beat: [LOSS_SPOKEN[beat]]}
            img = board_scenes.paint_chart_board(topic, beat, 0, 1)
            self.assertEqual(hashlib.sha256(img.tobytes()).hexdigest(), digest, beat)
        topic["_scene_cards"] = {beat: [LOSS_SPOKEN[beat]] for beat in LOSS_SPOKEN}
        cover = board_scenes.paint_cover(topic)
        self.assertEqual(hashlib.sha256(cover.tobytes()).hexdigest(), LOSS_HASH["cover"])


class TopicSceneTests(unittest.TestCase):
    def test_piercing_draws_candles_for_every_beat_and_the_cover(self):
        topic = _bank_topic("piercing")
        topic["_spoken_beats"] = {
            "hook": "पियर्सिंग लाइन गिरावट में पलटवार है।",
            "pehchan": "हरी कैंडल नीचे खुले और लाल के आधे से ऊपर बंद हो।",
            "matlab": "नीचे के भाव पर ख़रीदारी आती है।",
            "karein": "पुष्टि के बाद स्टॉप लॉस लो के नीचे रहता है।",
        }
        pictures = {}
        for beat in board_short.BEATS:
            frame = board_scenes.paint_chart_board(topic, beat, 0, 1)
            later = board_scenes.paint_chart_board(topic, beat, 1, 2)
            red, green = _candle_counts(frame)
            self.assertGreater(red + green, 80, beat)
            region = frame.chart_region
            frac = (region[3] - region[1]) / frame.size[1]
            self.assertGreaterEqual(frac, 0.60, beat)
            self.assertLessEqual(frac, 0.66, beat)
            blob = " ".join(layer.get("text") or "" for layer in frame.text_layers)
            for banned in board_scenes._LOSS_CHROME:
                self.assertNotIn(banned, blob, beat)
            pictures[beat] = hashlib.sha256(frame.tobytes()).hexdigest()
            if beat == "hook":
                self.assertNotEqual(frame.tobytes(), later.tobytes())
        self.assertEqual(len(set(pictures.values())), 4)
        cover = board_scenes.paint_cover(topic)
        red, green = _candle_counts(cover)
        self.assertGreater(red + green, 80)
        cover_text = " ".join(layer.get("text") or "" for layer in cover.text_layers)
        self.assertIn("PIERCING LINE", cover_text)
        self.assertIn("REVERSAL", cover_text)
        self.assertIn("50% SE UPAR", cover_text)
        self.assertNotIn("लाल को चीरती", cover_text)
        self.assertNotIn("EK BURA DIN", cover_text)
        self.assertNotIn("MAHINA KHATAM", cover_text)
        height = cover.size[1]
        region = cover.chart_region
        self.assertGreater((region[3] - region[1]) / height, 0.65)

    def test_a_topic_without_a_scene_fails_before_tts(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        missing = [
            item["key"] for item in bank["topics"]
            if item["key"] not in board_scenes.SCENES and item["key"] != board_scenes.DAILY_LOSS_KEY
        ]
        self.assertTrue(missing)
        topic = _bank_topic(missing[0])
        reason = board_scenes.topic_chart_block(topic)
        self.assertIn(missing[0], reason)
        self.assertIn("crash curve", reason)
        self.assertIn("placeholder", reason)
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            board_short.require_scene_frames(topic, {"mode": "schematic"}, {"beats": {}})
        self.assertIn(missing[0], err.getvalue())
        self.assertIn("0 characters spent", err.getvalue())
        with self.assertRaises(board_scenes.ChartVoiceError) as painted:
            board_scenes.paint_chart_board(topic, "hook", 0, 1)
        self.assertIn("crash curve", str(painted.exception))


PIERCING_SCRIPT = {
    "title": "Piercing Line — गिरावट में रिवर्सल?",
    "hook_line": "हरी कैंडल ने लाल का 70% काटा, क्या रिवर्सल आएगा?",
    "beats": {
        "hook": "हरी कैंडल ने लाल का 70% काटा, क्या रिवर्सल आएगा? इसे पियर्सिंग लाइन कहते हैं।",
        "pehchan": (
            "पहचान आसान है। पहले दिन बड़ी लाल कैंडल, 100 से 90। दूसरे दिन हरी कैंडल 88 पर नीचे खुलती है "
            "और 97 पर बंद होती है। लाल बॉडी का बीच 95 है, हरी कैंडल उसके ऊपर बंद हुई। ये सिर्फ़ गिरावट में मायने रखता है।"
        ),
        "matlab": (
            "मतलब, सेलर्स ने ओपन पर भाव 88 तक गिराया, पर बायर्स ने ख़रीदारी करके भाव 97 तक पहुँचा दिया। "
            "यानी नीचे के भाव पर बायर्स लौट रहे हैं, और गिरावट कमज़ोर हो रही है।"
        ),
        "karein": (
            "क्या करें? अकेले इस पैटर्न पर एंट्री मत लो। अगली हरी कैंडल ऊपर बंद हो, वही कन्फर्मेशन कैंडल है। "
            "तब एंट्री लो, और स्टॉप लॉस 87 पर रखो, पैटर्न के लो के नीचे। सिर्फ़ जानकारी, निवेश सलाह नहीं।"
        ),
    },
}


class PiercingLevelTests(unittest.TestCase):
    def _topic(self):
        topic = _bank_topic("piercing")
        topic["_spoken_beats"] = dict(PIERCING_SCRIPT["beats"])
        topic["_scene_cards"] = {beat: [PIERCING_SCRIPT["beats"][beat]] for beat in PIERCING_SCRIPT["beats"]}
        return topic

    def test_levels_come_from_the_topic_and_match_the_script(self):
        topic = self._topic()
        levels = board_scenes.candle_levels(topic)
        red, green = levels["pat"]
        self.assertEqual(red, (100.0, 100.0, 89.0, 90.0))
        self.assertEqual(green, (88.0, 98.0, 87.5, 97.0))
        self.assertEqual(levels["mid"], 95.0)
        self.assertEqual(levels["sl"], 87.0)
        body = abs(red[0] - red[3])
        frac = (green[3] - min(red[0], red[3])) / body
        self.assertAlmostEqual(frac, 0.70, places=2)
        self.assertGreater(green[3], levels["mid"])
        self.assertLess(green[0], red[2])
        self.assertLess(levels["sl"], min(c[2] for c in levels["pat"]))
        self.assertEqual(levels["confirm"], (97.0, 102.0, 96.5, 101.0))
        self.assertGreater(levels["confirm"][3], levels["pat"][1][1])
        banned = ("पलटवार", "चीरती", "हद", "पूँजी", "क्षमता", "तय करो")
        blob = " ".join(PIERCING_SCRIPT["beats"].values()) + " " + topic["topic"]
        for word in banned:
            self.assertNotIn(word, blob)
        for beat, spoken in PIERCING_SCRIPT["beats"].items():
            frame = board_scenes.paint_chart_board(topic, beat, 0, 1)
            done = frame.motion_at(1.0)
            text = " ".join(layer.get("text") or "" for layer in done.text_layers)
            self.assertIn("95 (50%)", text, beat)
            self.assertIn("100", text, beat)
            self.assertIn("97", text, beat)
            voice = board_scenes.chart_spec_for(spoken)
            spec = dict(frame.chart_spec)
            spec["concept"] = voice["concept"]
            self.assertEqual(board_scenes.chart_voice_faults(spec, spoken), [], beat)
            self.assertEqual(set(frame.chart_spec["percents"]), set(voice["percents"]))
            self.assertEqual(set(frame.chart_spec["rupees"]), set(voice["rupees"]))
        karein = board_scenes.paint_chart_board(topic, "karein", 0, 1)
        karein_text = " ".join(layer.get("text") or "" for layer in karein.text_layers)
        self.assertIn("SL 87", karein_text)
        hook = board_scenes.paint_chart_board(topic, "hook", 0, 1)
        hook_text = " ".join(layer.get("text") or "" for layer in hook.text_layers)
        self.assertIn("70%", hook_text)
        self.assertNotIn("SL 87", hook_text)
        shown = {beat: board_scenes.paint_chart_board(topic, beat, 0, 1).motion_at(1.0)
                 for beat in PIERCING_SCRIPT["beats"]}
        shown["cover"] = board_scenes.paint_cover(topic)
        for name, frame in shown.items():
            self._assert_badge_is_centred(frame, name)
            self._assert_mid_label_clears_the_candles(frame, name)

    def _assert_badge_is_centred(self, frame, name):
        badge = getattr(frame, "_level_badge", None)
        self.assertIsNotNone(badge, name)
        layer = next(item for item in frame.text_layers if item.get("text") == "50% SE UPAR")
        box = layer["box"]
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        self.assertAlmostEqual(cx, (badge[0] + badge[2]) / 2, delta=6, msg=name)
        self.assertAlmostEqual(cy, (badge[1] + badge[3]) / 2, delta=6, msg=name)
        self.assertGreater(box[1], badge[1] + 8, name)
        self.assertLess(box[3], badge[3] - 8, name)
        self.assertGreater(box[0], badge[0] + 8, name)
        self.assertLess(box[2], badge[2] - 8, name)

    def _assert_mid_label_clears_the_candles(self, frame, name):
        import safe_area
        ax = frame._level_axes
        levels = board_scenes.candle_levels(key="piercing")
        layer = next(item for item in frame.text_layers if item.get("text") == "95 (50%)")
        candles = list(levels["pat"])
        if int(getattr(frame, "_level_draw_n", 0) or 0) >= 3 and levels.get("confirm"):
            candles.append(levels["confirm"])
        for i, (o, _h, _l, c) in enumerate(candles):
            cx = ax.x(i)
            top, bot = ax.y(max(o, c)), ax.y(min(o, c))
            body = (cx - ax.bw / 2, min(top, bot), cx + ax.bw / 2, max(top, bot))
            self.assertFalse(safe_area.intersects(layer["box"], body), name)
        centres = [ax.x(i) for i in range(ax.n)]
        self.assertAlmostEqual(sum(centres) / len(centres), 540, delta=24, msg=name)
        self.assertGreater(ax.bw, 140, name)
        self.assertGreater(centres[-1] + ax.bw / 2, 760, name)

    def test_the_hook_percent_stays_whole_on_a_wipe(self):
        """70% sits on the dark panel, so a wipe can still see the whole glyph."""
        import scene_frames
        import scene_transition
        topic = self._topic()
        topic["_scene_cards"] = {
            "hook": [
                "हरी कैंडल ने लाल का 70% काटा, क्या रिवर्सल आएगा?",
                "70% काटा, क्या रिवर्सल आएगा? इसे पियर्सिंग लाइन कहते हैं।",
            ],
            "pehchan": ["पहचान आसान है। पहले दिन बड़ी लाल कैंडल, 100 से 90।"],
        }
        hook, _flags = scene_frames.frames_for(topic, "hook", 2, {"mode": "schematic"})
        nxt, _flags = scene_frames.frames_for(topic, "pehchan", 1, {"mode": "schematic"})
        end = hook[1].animate(1.0)
        layer = next(item for item in end.text_layers if item.get("text") == "70%")
        plate = end.plate
        shown = plate.copy()
        shown.paste(layer["image"], layer["xy"], layer["image"])
        self.assertGreaterEqual(
            scene_transition.ink_column_coverage(layer, plate, shown), 0.85,
        )
        faults = scene_transition.transition_faults(
            [hook[0], hook[1], nxt[0]], board_short.SCENE_TRANSITIONS,
        )
        self.assertEqual(faults, [])

    def test_a_four_second_hold_changes_the_picture(self):
        import scene_pace
        topic = self._topic()
        import scene_frames
        frames, _flags = scene_frames.frames_for(topic, "pehchan", 3, {"mode": "schematic"})
        self.assertEqual(len(frames), 3)
        samples = []
        for index, frame in enumerate(frames):
            for step in range(4):
                t = index * 4 + step
                samples.append((float(t), __import__("numpy").asarray(frame.animate(step / 4.0).convert("RGB"))))
        self.assertEqual(scene_pace.unchanged_spans(samples), [])

    def test_approved_hook_line_passes_and_is_stored(self):
        import hook_check
        import json
        from pathlib import Path
        stored = json.loads((Path(__file__).resolve().parents[1] / "approved" / "piercing.json").read_text())
        self.assertEqual(stored["hook_line"], "पियर्सिंग लाइन पर खरीद कब करें?")
        self.assertLessEqual(len(stored["hook_line"].split()), 12)
        self.assertNotIn("owner_approved", stored)
        import trade_plan
        narration = trade_plan.narration_from({**stored, "key": "piercing"})
        plan_topic = {"key": "piercing", "_live_beats": "plan", "_approved_bank": True}
        self.assertEqual(trade_plan.script_fails(narration, plan_topic), [])
        self.assertEqual(
            hook_check.check_hook_line(
                stored["hook_line"], trade_plan.number_strings(trade_plan.plan_for("piercing")), max_words=12,
            ),
            [],
        )
        import scene_pace
        vague = scene_pace.vague_beats(narration["beats"], order=trade_plan.BEATS)
        self.assertEqual([beat for beat in vague if beat != "cta"], [])
        self.assertIn("?", narration["beats"]["cta"])

    def test_karein_draws_the_confirmation_candle_when_that_line_is_said(self):
        topic = self._topic()
        frame = board_scenes.paint_chart_board(topic, "karein", 0, 1)
        early = frame.motion_at(0.0)
        done = frame.motion_at(1.0)
        self.assertNotIn("CONFIRMATION", self._labels(early))
        self.assertEqual(len(self._green_clusters(early)), 1)
        self.assertIn("CONFIRMATION", self._labels(done))
        self.assertIn("SL 87", self._labels(done))
        self.assertIn("95 (50%)", self._labels(done))
        self.assertEqual(len(self._green_clusters(done)), 2)
        import safe_area
        boxes = [layer["box"] for layer in done.text_layers if layer.get("box")]
        left, top, right, bottom = safe_area.limits()
        for box in boxes:
            self.assertGreaterEqual(box[0], left - 2)
            self.assertGreaterEqual(box[1], top - 2)
            self.assertLessEqual(box[2], right + 2)
            self.assertLessEqual(box[3], bottom + 2)
        for i, left in enumerate(boxes):
            for right in boxes[i + 1:]:
                self.assertFalse(safe_area.intersects(left, right))
        self.assertNotIn("102", self._labels(board_scenes.paint_chart_board(topic, "hook", 0, 1).motion_at(1.0)))
        window = board_scenes._confirm_window(topic)
        self.assertLess(window[0], 0.5)
        self.assertGreater(window[1], window[0])

    def test_confirmation_candle_follows_the_spoken_words(self):
        topic = self._topic()
        topic["_beat_times"] = {"karein": (40.0, 60.0)}
        topic["_audio_words"] = self._confirm_words("अगली")
        start, end = board_scenes._confirm_window(topic)
        self.assertAlmostEqual(start, 0.40, places=2)
        self.assertAlmostEqual(end, 0.58, places=2)
        frame = board_scenes.paint_chart_board(topic, "karein", 0, 1)
        before = frame.motion_at(start - 0.02)
        self.assertEqual(len(self._green_clusters(before)), 1)
        self.assertNotIn("CONFIRMATION", self._labels(before))
        done = frame.motion_at(end)
        self.assertEqual(len(self._green_clusters(done)), 2)
        self.assertIn("CONFIRMATION", self._labels(done))
        self.assertIn("SL 87", self._labels(done))
        # A misspelling, and हरी कैंडल after मत लो when अगली itself is missing.
        topic["_audio_words"] = self._confirm_words("अगलि")
        self.assertEqual(board_scenes._confirm_window(topic), (start, end))
        words = [row for row in self._confirm_words("अगली") if row["word"] != "अगली"]
        topic["_audio_words"] = words
        anchored = board_scenes._confirm_window(topic)
        self.assertAlmostEqual(anchored[0], 0.42, places=2)
        self.assertAlmostEqual(anchored[1], end, places=2)
        # No matching word: grow from that scene's start through the scene end.
        topic["_audio_words"] = [{"word": "शब्द", "start": 42.0, "end": 42.4}]
        topic["_scene_plan"] = [{"beat": "karein", "start": 47.0, "end": 55.0, "index": 1}]
        topic["_scene_cards"] = {"karein": ["अगली हरी कैंडल ऊपर बंद हो, वही कन्फर्मेशन कैंडल है।"]}
        fallen = board_scenes._confirm_window(topic)
        self.assertAlmostEqual(fallen[0], 0.35, places=2)
        self.assertAlmostEqual(fallen[1], 0.75, places=2)

    def test_the_build_stores_word_times_for_the_candle(self):
        """Live render and the no-TTS rerender both install cards before painting."""
        import scene_pace
        topic = self._topic()
        spoken = dict(topic["_spoken_beats"])
        beat_times = {"hook": (0.0, 10.0), "pehchan": (10.0, 25.0), "matlab": (25.0, 40.0), "karein": (40.0, 60.0)}
        scenes = scene_pace.plan_scenes(beat_times)
        words = self._confirm_words("अगली")
        board_short._install_scene_cards(topic, spoken, beat_times, scenes, words)
        self.assertIn("अगली", [row["word"] for row in topic["_audio_words"]])
        start, end = board_scenes._confirm_window(topic)
        self.assertAlmostEqual(start, 0.40, places=2)
        self.assertAlmostEqual(end, 0.58, places=2)

    def test_badge_text_stays_inside_the_box_while_it_pops_in(self):
        import numpy as np
        import scene_frames
        topic = self._topic()
        for beat in ("hook", "matlab", "karein"):
            frames, _flags = scene_frames.frames_for(topic, beat, 1, {"mode": "schematic"})
            for u in (0.64, 0.72, 0.85, 1.0):
                shown = frames[0].animate(u)
                layer = next(item for item in shown.text_layers if item.get("text") == "50% SE UPAR")
                badge = layer["badge_box"]
                box = layer["box"]
                self.assertGreaterEqual(box[0], badge[0] - 1, (beat, u))
                self.assertGreaterEqual(box[1], badge[1] - 1, (beat, u))
                self.assertLessEqual(box[2], badge[2] + 1, (beat, u))
                self.assertLessEqual(box[3], badge[3] + 1, (beat, u))
                arr = np.asarray(shown.convert("RGB"))
                fill = (
                    (np.abs(arr[:, :, 0].astype(int) - 48) <= 14)
                    & (np.abs(arr[:, :, 1].astype(int) - 36) <= 14)
                    & (arr[:, :, 2] <= 28)
                )
                ys, xs = np.nonzero(fill)
                self.assertGreater(int(xs.size), 40, (beat, u))
                self.assertGreaterEqual(box[0], float(xs.min()) - 8, (beat, u))
                self.assertLessEqual(box[2], float(xs.max()) + 8, (beat, u))
                self.assertGreaterEqual(box[1], float(ys.min()) - 8, (beat, u))
                self.assertLessEqual(box[3], float(ys.max()) + 8, (beat, u))

    @staticmethod
    def _confirm_words(agli):
        return [
            {"word": "क्या", "start": 40.2, "end": 40.6},
            {"word": "करें", "start": 40.6, "end": 41.0},
            {"word": "मत", "start": 46.0, "end": 46.3},
            {"word": "लो", "start": 46.3, "end": 46.6},
            {"word": agli, "start": 48.0, "end": 48.4},
            {"word": "हरी", "start": 48.4, "end": 48.7},
            {"word": "कैंडल", "start": 48.7, "end": 49.2},
            {"word": "ऊपर", "start": 49.2, "end": 49.6},
            {"word": "बंद", "start": 49.6, "end": 49.9},
            {"word": "हो", "start": 49.9, "end": 50.2},
            {"word": "वही", "start": 50.2, "end": 50.5},
            {"word": "कन्फर्मेशन", "start": 50.5, "end": 51.1},
            {"word": "कैंडल", "start": 51.1, "end": 51.4},
            {"word": "है", "start": 51.4, "end": 51.6},
        ]

    @staticmethod
    def _labels(img):
        return [layer.get("text") or "" for layer in img.text_layers]

    @staticmethod
    def _green_clusters(img):
        import numpy as np
        arr = np.asarray(img.convert("RGB"))
        mask = (arr[:, :, 1] > 150) & (arr[:, :, 0] < 90)
        cols = np.flatnonzero(mask.any(axis=0))
        groups = []
        if cols.size == 0:
            return groups
        start = prev = int(cols[0])
        for col in cols[1:]:
            col = int(col)
            if col - prev > 18:
                groups.append((start, prev))
                start = col
            prev = col
        groups.append((start, prev))
        return groups


class TitleAndHookTests(unittest.TestCase):
    def test_piercing_line_accepts_the_spoken_or_latin_form(self):
        title = "Piercing Line — गिरावट में पलटवार"
        phrase = board_short.key_phrase_for(title)
        self.assertNotIn("पइएरकइनग", phrase)
        self.assertNotIn("लइनए", phrase)
        spoken = {
            "title": title,
            "beats": {"hook": "पियर्सिंग लाइन गिरावट में पलटवार है।"},
        }
        self.assertEqual(board_short.title_term_notes(spoken), [])
        latin = {
            "title": title,
            "beats": {"hook": "Piercing Line गिरावट में पलटवार है।"},
        }
        self.assertEqual(board_short.title_term_notes(latin), [])
        terms = sync_gate.answer_terms_for(title)
        self.assertNotIn("पइएरकइनग", terms)
        heard = "पियर्सिंग लाइन गिरावट में पलटवार है।"
        report = sync_gate.check(
            [{"word": heard, "start": 0.1, "end": 1.0}],
            [{"start": 0.0, "end": 2.0, "beat": "hook", "show": ["पियर्सिंग"]}],
            {"hook": heard},
            {"hook": (0.0, 2.0)},
            terms,
        )
        self.assertNotIn("title promise", report["reason"])
        latin_report = sync_gate.check(
            [{"word": "Piercing Line गिरावट", "start": 0.1, "end": 1.0}],
            [{"start": 0.0, "end": 2.0, "beat": "hook", "show": ["Piercing"]}],
            {"hook": "Piercing Line गिरावट में पलटवार है।"},
            {"hook": (0.0, 2.0)},
            terms,
        )
        self.assertNotIn("title promise", latin_report["reason"])

    def test_approved_hook_line_is_the_first_sentence(self):
        import approved_script
        topic = _bank_topic("piercing")
        payload = {
            "title": "Piercing Line — गिरावट में पलटवार",
            "hook_line": "",
            "beats": {
                "hook": "पियर्सिंग लाइन गिरावट में पलटवार है। अगली लाइन इंतज़ार है।",
                "pehchan": "हरी कैंडल नीचे खुले।",
                "matlab": "ख़रीदारी नीचे के भाव पर आती है।",
                "karein": "पुष्टि के बाद रुकें।",
            },
        }
        script = approved_script.narration_from_payload(payload, topic, "37118446538")
        self.assertEqual(script["hook_line"], "पियर्सिंग लाइन गिरावट में पलटवार है।")
        self.assertNotEqual(script["hook_line"], "")

    def test_overrides_are_not_pasted_into_the_shell(self):
        text = (ROOT / ".github" / "workflows" / "narration-dry-run.yml").read_text(encoding="utf-8")
        live = text.split("name: Live narration check", 1)[1].split("name: Upload", 1)[0]
        script = live.split("run: |", 1)[1]
        self.assertIn("NARRATION_OVERRIDES: ${{ inputs.overrides }}", live.split("run: |", 1)[0])
        self.assertNotIn("inputs.overrides", script)
        self.assertNotIn("--overrides", script)
