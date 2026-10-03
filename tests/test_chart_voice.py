"""The chart on a beat must carry that beat's ₹ and % figures, and the same concept."""
import json
import unittest
from pathlib import Path

import board_kit
import board_scenes
import safe_area
import scene_frames

ROOT = Path(__file__).resolve().parents[1]
PEHCHAN = (
    "अब इसे पहचानें। पूँजी दो लाख रुपये है, तो दिन की हद दो प्रतिशत यानी चार हज़ार रुपये बैठती है। "
    "यह चार हज़ार रुपये दिन का कुल नुकसान है, हर ट्रेड का नहीं। यह हद लिखी हुई रहती है।"
)
MATLAB = "दिन की हद पर रुकना है। नुकसान चार हज़ार रुपये पर रुकता है।"


def _topic():
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = json.loads(json.dumps(next(item for item in bank["topics"] if item["key"] == "risk_daily_loss_limit")))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    return topic


class ChartVoiceTests(unittest.TestCase):
    def test_pehchan_capital_slice_passes_and_a_stray_rupee_fails(self):
        spec = board_scenes.chart_spec_for(PEHCHAN)
        self.assertEqual(spec["concept"], "capital_slice")
        self.assertEqual(board_scenes.chart_voice_faults(spec, PEHCHAN), [])
        self.assertEqual(set(spec["rupees"]), {200000, 4000})
        self.assertEqual(spec["percents"], [2.0])
        topic = _topic()
        topic["_spoken_beats"] = {"pehchan": PEHCHAN}
        topic["_scene_cards"] = {"pehchan": [PEHCHAN]}
        import scene_frames
        frame = scene_frames.frames_for(topic, "pehchan", 1, {"mode": "schematic"})[0][0]
        self.assertEqual(frame.chart_spec["concept"], "capital_slice")
        shown = {layer.get("text") for layer in frame.text_layers if layer.get("role") == "chart"}
        self.assertTrue(any("4,000" in text or "₹4" in text for text in shown), shown)
        self.assertTrue(any("2%" in text or "लाख" in text for text in shown), shown)
        region = frame.chart_region
        frac = (region[3] - region[1]) / frame.size[1]
        self.assertGreaterEqual(frac, 0.60)
        self.assertLessEqual(frac, 0.66)
        captions = [layer for layer in frame.text_layers if layer.get("role") == "caption" and layer.get("box")]
        self.assertTrue(captions)
        self.assertGreaterEqual(min(layer["box"][1] for layer in captions), region[3] - 1)
        extra = dict(spec)
        extra["rupees"] = list(spec["rupees"]) + [9000]
        self.assertTrue(any("9000" in fault for fault in board_scenes.chart_voice_faults(extra, PEHCHAN)))

    def test_matlab_stop_line_passes_and_a_missing_figure_fails(self):
        spec = board_scenes.chart_spec_for(MATLAB)
        self.assertEqual(spec["concept"], "pnl_stop")
        self.assertEqual(board_scenes.chart_voice_faults(spec, MATLAB), [])
        self.assertIn(4000, spec["rupees"])
        topic = _topic()
        topic["_spoken_beats"] = {"matlab": MATLAB}
        topic["_scene_cards"] = {"matlab": [MATLAB]}
        import scene_frames
        frame = scene_frames.frames_for(topic, "matlab", 1, {"mode": "schematic"})[0][0]
        self.assertEqual(frame.chart_spec["concept"], "pnl_stop")
        blob = " ".join(layer.get("text") or "" for layer in frame.text_layers)
        self.assertIn("STOP", blob)
        self.assertIn("4,000", blob)
        thin = dict(spec)
        thin["rupees"] = []
        faults = board_scenes.chart_voice_faults(thin, MATLAB)
        self.assertTrue(any("missing" in fault for fault in faults), faults)
        wrong = dict(spec)
        wrong["concept"] = "capital_slice"
        self.assertTrue(any("concept" in fault for fault in board_scenes.chart_voice_faults(wrong, MATLAB)))
        with self.assertRaises(board_scenes.ChartVoiceError):
            board_scenes.enforce_chart_voice(wrong, MATLAB)

    def test_cover_uses_the_board_and_only_the_narration_numbers(self):
        topic = _topic()
        topic["_spoken_beats"] = {
            "hook": "दो लाख रुपये की पूँजी पर बुरा दिन पूरा महीना बिगाड़ देता है।",
            "pehchan": PEHCHAN,
            "matlab": MATLAB,
            "karein": "हद हिट होते ही टर्मिनल बंद। चार हज़ार रुपये तय करो।",
        }
        cover = board_scenes.paint_cover(topic)
        self.assertEqual(cover.size, (board_kit.W, board_kit.H))
        self.assertEqual(cover.chart_spec["concept"], "capital_slice")
        self.assertIn("MAX", " ".join(layer.get("text") or "" for layer in cover.text_layers))
        hook = board_scenes.cover_hook(topic)
        self.assertGreaterEqual(len(hook.split()), 2)
        self.assertLessEqual(len(hook.split()), 4)
        for layer in cover.text_layers:
            if layer.get("box"):
                self.assertTrue(safe_area.inside(layer["box"]), layer.get("text"))
        stray = dict(cover.chart_spec)
        stray["rupees"] = [200000, 4000, 999]
        stray["concept"] = "capital_slice"
        self.assertTrue(board_scenes.chart_voice_faults(stray, PEHCHAN))


HOOK = "डेली लॉस लिमिट क्यों ज़रूरी है? दो लाख रुपये के कैपिटल पर एक बुरा दिन पूरा महीना बिगाड़ देता है।"
PEHCHAN_EARLY = "अब इसे पहचानो। कैपिटल दो लाख रुपये है, तो डेली"
PEHCHAN_FULL = (
    "अब इसे पहचानो। कैपिटल दो लाख रुपये है, तो डेली लॉस लिमिट दो परसेंट, यानी चार हज़ार रुपये। "
    "ये चार हज़ार पूरे दिन का टोटल लॉस है, हर ट्रेड का नहीं। इस लिमिट को पहले से लिखकर फिक्स करो।"
)
MATLAB_BOTH = (
    "मतलब ये है। दो परसेंट की डेली लॉस लिमिट बुरे दिन को छोटा रखती है, ताकि लॉस चार हज़ार रुपये से "
    "ज़्यादा न जाए। दो लाख रुपये का दो परसेंट बराबर चार हज़ार रुपये, यानी कैपिटल का सिर्फ दो परसेंट।"
)
KAREIN = (
    "अब आगे। लिमिट पहले लिखो, हिट होते ही टर्मिनल बंद। स्टॉप लॉस और एंट्री से पहले चार हज़ार रुपये "
    "फिक्स करो, फिर उसी पर टिके रहो।"
)


class ChartBoardPolishTests(unittest.TestCase):
    def _frame(self, beat, spoken, cards=None):
        import scene_frames
        topic = _topic()
        topic["_spoken_beats"] = {beat: spoken}
        if cards is not None:
            topic["_scene_cards"] = {beat: cards}
        return scene_frames.frames_for(topic, beat, len(cards) if cards else 1, {"mode": "schematic"})[0]

    def test_pehchan_opens_on_both_bars_without_the_later_amount(self):
        import sync_gate
        frames = self._frame("pehchan", PEHCHAN_FULL, [PEHCHAN_EARLY, PEHCHAN_FULL])
        bars = frames[0].chart_bar_boxes
        self.assertGreaterEqual(len(bars), 2)
        shown = " ".join(sync_gate.lesson_lines(frames[0]))
        self.assertNotIn("4,000", shown)
        self.assertNotIn("4000", shown.replace(",", ""))
        late = " ".join(sync_gate.lesson_lines(frames[1]))
        self.assertIn("4,000", late)

    def test_matlab_stop_chart_does_not_paint_the_capital(self):
        frames = self._frame("matlab", MATLAB_BOTH)
        chart = " ".join(
            layer.get("text") or ""
            for layer in frames[0].text_layers
            if layer.get("role") == "chart"
        )
        self.assertIn("STOP", chart)
        self.assertIn("4,000", chart)
        self.assertNotIn("2,00,000", chart)
        self.assertNotIn("2 लाख", chart)

    def test_hook_headline_is_capital_2_lakh_without_a_second_amount(self):
        frames = self._frame("hook", HOOK)
        texts = [layer.get("text") or "" for layer in frames[0].text_layers]
        self.assertIn("CAPITAL ₹2 LAKH", texts)
        chart = [
            layer.get("text") or ""
            for layer in frames[0].text_layers
            if layer.get("role") == "chart"
        ]
        self.assertFalse(any("2,00,000" in text or "₹2" in text for text in chart), chart)

    def test_step_label_states_the_max_loss(self):
        frames = self._frame("karein", KAREIN)
        blob = " ".join(layer.get("text") or "" for layer in frames[0].text_layers)
        self.assertIn("SL + entry, max", blob)
        self.assertIn("loss", blob)
        self.assertNotIn("SL + entry  ", blob)
        for layer in frames[0].text_layers:
            if layer.get("box"):
                self.assertTrue(safe_area.inside(layer["box"]), layer.get("text"))


class ChartBoardOnlyPathTests(unittest.TestCase):
    def test_every_non_atr_short_frame_has_a_chart_and_no_text_card(self):
        import scene_frames
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        loss = json.loads(json.dumps(next(t for t in bank if t["key"] == "risk_daily_loss_limit")))
        journal = json.loads(json.dumps(next(t for t in bank if t["key"] == "risk_journal")))
        live = json.loads(json.dumps(loss))
        live["_live_short"] = True
        live["_live_beats"] = "four"
        six = json.loads(json.dumps(loss))
        six["_live_short"] = True
        six["_live_beats"] = "six"
        banned = {"KEY AREA", "RULE", "LOOK HERE", "NEXT STEP"}
        cases = (
            (live, {"mode": "schematic"}),
            (six, {"mode": "schematic"}),
            (loss, {"mode": "schematic"}),
            (loss, {"mode": "non_numeric"}),
            (journal, {"mode": "series"}),
        )
        for topic, spec in cases:
            for beat in ("hook", "pehchan", "matlab", "karein"):
                frames, flags = scene_frames.frames_for(topic, beat, 2, spec)
                self.assertEqual(flags, [False, False], beat)
                for frame in frames:
                    region = getattr(frame, "chart_region", None)
                    self.assertIsNotNone(region, beat)
                    height = (region[3] - region[1]) / frame.size[1]
                    self.assertGreater(height, 0.50, beat)
                    self.assertLess(height, 0.85, beat)
                    self.assertGreater(region[1], 40, beat)
                    texts = {layer.get("text") for layer in frame.text_layers or []}
                    self.assertFalse(texts & banned, texts & banned)


class ChartPolishTests(unittest.TestCase):
    def test_capital_amount_on_the_green_bar_is_white_and_bold(self):
        import numpy as np
        topic = _topic()
        line = (
            "अब इसे पहचानो। कैपिटल ₹2,00,000 है, तो डेली लॉस लिमिट दो परसेंट, "
            "यानी ₹4,000। ये चार हज़ार पूरे दिन का टोटल लॉस है।"
        )
        topic["_spoken_beats"] = {"pehchan": line}
        topic["_scene_cards"] = {"pehchan": [line]}
        frame = scene_frames.frames_for(topic, "pehchan", 1, {"mode": "schematic"})[0][0]
        layer = next(
            item for item in frame.text_layers
            if "2,00,000" in str(item.get("text") or "")
        )
        self.assertIn("Bold", str(layer.get("font") or ""))
        sprite = np.asarray(layer["image"])
        ink = sprite[sprite[..., 3] > 128][:, :3].astype(int)
        mean = ink.mean(axis=0)
        self.assertGreater(mean[0], 240)
        self.assertGreater(mean[1], 240)
        self.assertGreater(mean[2], 200)
        gold = np.array(scene_frames.GOLD)
        self.assertGreater(float(np.abs(mean - gold).mean()), 40)

    def test_matlab_stop_line_keeps_moving_across_the_beat(self):
        import numpy as np
        import scene_pace
        topic = _topic()
        matlab = (
            "मतलब ये है। दो परसेंट की डेली लॉस लिमिट बुरे दिन को छोटा रखती है, "
            "ताकि लॉस चार हज़ार रुपये से ज़्यादा न जाए। दो लाख रुपये का दो परसेंट "
            "बराबर चार हज़ार रुपये, यानी कैपिटल का सिर्फ दो परसेंट।"
        )
        topic["_spoken_beats"] = {"matlab": matlab}
        topic["_scene_cards"] = {"matlab": [matlab] * 4}
        frames, _flags = scene_frames.frames_for(topic, "matlab", 4, {"mode": "schematic"})
        self.assertEqual(frames[0].chart_spec["concept"], "pnl_stop")
        posed = [np.asarray(frame.animate(0).convert("RGB"), dtype=np.int16) for frame in frames]
        samples = [(index * 4.0, image) for index, image in enumerate(posed)]
        self.assertEqual(scene_pace.unchanged_spans(samples), [])
        for earlier, later in zip(posed, posed[1:]):
            self.assertGreaterEqual(float(np.abs(earlier - later).mean()), 1.0)


if __name__ == "__main__":
    unittest.main()
