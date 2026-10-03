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
        for i, (o, _h, _l, c) in enumerate(levels["pat"]):
            cx = ax.x(i)
            top, bot = ax.y(max(o, c)), ax.y(min(o, c))
            body = (cx - ax.bw / 2, min(top, bot), cx + ax.bw / 2, max(top, bot))
            self.assertFalse(safe_area.intersects(layer["box"], body), name)

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
        self.assertEqual(stored["hook_line"], PIERCING_SCRIPT["hook_line"])
        self.assertLessEqual(len(stored["hook_line"].split()), 12)
        topic = self._topic()
        self.assertEqual(
            hook_check.check_hook_line(
                stored["hook_line"], hook_check.allowed_numbers(topic), max_words=12,
            ),
            [],
        )
        import scene_pace
        self.assertEqual(scene_pace.vague_beats(stored["beats"]), [])


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
