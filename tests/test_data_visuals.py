"""Every four-beat live scene is a chart on top, never a full-screen text card."""
import json
import unittest
from pathlib import Path

import numpy as np

import board_short
import data_visuals
import safe_area
import scene_frames

ROOT = Path(__file__).resolve().parents[1]


def _topic(key):
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = json.loads(json.dumps(next(item for item in bank["topics"] if item["key"] == key)))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    return topic


class ChartLayoutTests(unittest.TestCase):
    def test_every_beat_has_a_top_chart_and_not_a_text_card(self):
        for key in ("risk_daily_loss_limit", "risk_journal", "loss_recovery"):
            topic = _topic(key)
            for beat in board_short.BEATS:
                frames, flags = scene_frames.frames_for(topic, beat, 2, {"mode": "schematic"})
                self.assertEqual(flags, [False, False])
                for frame in frames:
                    self.assertTrue(str(frame.scene_kind).startswith("chart"), frame.scene_kind)
                    self.assertTrue(frame.chart_layer)
                    faults = data_visuals.layout_faults(frame)
                    self.assertEqual(faults, [], f"{key} {beat} {faults}")
                    region = frame.chart_region
                    frac = (region[3] - region[1]) / frame.size[1]
                    self.assertGreaterEqual(frac, 0.60)
                    self.assertLessEqual(frac, 0.66)
                    self.assertLess(region[1], frame.size[1] * 0.12)
                    self.assertLessEqual(region[3], safe_area.SUBTITLE_TOP)
                    plot = frame.chart_box
                    self.assertGreaterEqual(
                        (plot[3] - plot[1]) / (region[3] - region[1]), 0.70,
                    )
                    for layer in frame.text_layers:
                        words = str(layer.get("text") or "").split()
                        role = layer.get("role") or "lesson"
                        limit = 5 if role == "lesson" else 8
                        self.assertLessEqual(len(words), limit, layer.get("text"))
                        if layer.get("box"):
                            self.assertTrue(safe_area.inside(layer["box"]), layer.get("text"))

    def test_animation_changes_the_chart_and_captions_stay_under_it(self):
        topic = _topic("risk_daily_loss_limit")
        frame = scene_frames.frames_for(topic, "hook", 1, {"mode": "schematic"})[0][0]
        start = np.asarray(frame.animate(0).convert("RGB"), dtype=np.int16)
        later = np.asarray(frame.animate(0.8).convert("RGB"), dtype=np.int16)
        self.assertGreaterEqual(float(np.abs(start - later).mean()), 1.0)
        self.assertTrue(frame.animate(0.5).text_layers)
        times = {
            "hook": (0.0, 8.0),
            "pehchan": (8.0, 18.0),
            "matlab": (18.0, 32.0),
            "karein": (32.0, 48.0),
        }
        spoken = {beat: "₹4,000 की हद पहले तय है।" for beat in times}
        ass = board_short.build_ass(topic, times, spoken, "", "")
        self.assertIn("Dialogue: 2,", ass)
        self.assertIn(board_short.DISCLAIMER, ass)
        self.assertIn("@ThePaiseKiPathshala", scene_frames.frame_card_text(frame))
        # Caption positions are the \\pos y values on the spoken track.
        for line in ass.splitlines():
            if not line.startswith("Dialogue: 2,"):
                continue
            y = int(line.split("\\pos(540,", 1)[1].split(")", 1)[0])
            self.assertGreaterEqual(y, safe_area.SUBTITLE_TOP)
            self.assertLessEqual(y, safe_area.SUBTITLE_LIMIT)

    def test_a_text_card_without_a_chart_fails_the_layout_check(self):
        from PIL import Image
        card = Image.new("RGB", (1080, 1920), (20, 40, 80))
        card.text_layers = [{
            "text": "यह पूरा कार्ड सिर्फ़ लिखा हुआ पैरा है और कुछ नहीं",
            "box": (80.0, 200.0, 1000.0, 1500.0),
            "role": "lesson",
            "faint": False,
        }]
        card.scene_kind = "text-card"
        faults = data_visuals.layout_faults(card)
        self.assertTrue(any("no chart" in note or "text card" in note for note in faults), faults)


if __name__ == "__main__":
    unittest.main()
