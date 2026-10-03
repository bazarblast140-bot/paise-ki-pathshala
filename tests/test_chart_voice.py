"""The chart on a beat must carry that beat's ₹ and % figures, and the same concept."""
import json
import unittest
from pathlib import Path

import board_kit
import board_scenes
import safe_area

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


if __name__ == "__main__":
    unittest.main()
