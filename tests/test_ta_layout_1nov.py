"""1-Nov TA layout fix: tall chart, no text in the bottom zone, 5m used, motion, Devanagari end card, trigger sanity."""
import json
import tempfile
import unittest
from pathlib import Path

from realchart import ta_layout
from realchart.levels import trigger_gap, trigger_ok

SRC = Path("/workspace/paise-review/box-ta-2026-10-31")


class TriggerTests(unittest.TestCase):
    def test_short_trigger_must_be_below_close(self):
        self.assertFalse(trigger_ok("short", 1245, 1246, trigger_gap(1246, 32)))
        self.assertTrue(trigger_ok("short", 1241.8, 1246, trigger_gap(1246, 32)))

    def test_long_trigger_must_be_above_close(self):
        self.assertFalse(trigger_ok("long", 100, 100.1))
        self.assertTrue(trigger_ok("long", 101, 100, 0.5))


class LayoutMathTests(unittest.TestCase):
    def test_key_levels_spread_over_key_area(self):
        mapping = {"a": -2.0, "b": 2600.0}
        top, height = ta_layout.window_for(mapping, 1058, [1180, 1272])
        ys = [ta_layout.panel_y(mapping, (top, height), p) for p in (1180, 1272)]
        self.assertGreaterEqual(min(ys), ta_layout.KEY_TOP - 2)
        self.assertLessEqual(max(ys), ta_layout.KEY_BOTTOM + 2)
        self.assertLess(ta_layout.MEDIA[1] + max(ys), ta_layout.SAFE_BOTTOM)

    def test_labels_never_overlap(self):
        placed = ta_layout.spread_labels([500, 505, 510, 900], 74, 40, 960)
        placed.sort()
        self.assertTrue(all(b - a >= 74 - 1e-6 for a, b in zip(placed, placed[1:])))

    def test_caption_box_and_header_above_safe_zone(self):
        self.assertLess(ta_layout.CAP[3], ta_layout.SAFE_BOTTOM)
        self.assertLess(ta_layout.MEDIA[1] + ta_layout.KEY_BOTTOM + 40, ta_layout.SAFE_BOTTOM)
        self.assertGreater(ta_layout.MEDIA[3] - ta_layout.MEDIA[1], 1200)  # chart fills the frame


@unittest.skipUnless((SRC / "chart.png").is_file() and (SRC / "chart-5m.png").is_file(), "31-Oct captures not on this box")
class RenderTests(unittest.TestCase):
    def test_render_uses_5m_and_moves(self):
        from realchart.render import frames
        levels = json.loads((SRC / "levels.json").read_text())
        rows = json.loads((SRC / "script.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cards"
            made = frames(out, "ta", levels, rows, str(SRC / "chart.png"), None,
                          {"path": str(SRC / "chart-5m.png"), "interval": "5"}, durations=[2.0] * len(rows))
            self.assertEqual(len(made), len(rows))
            axis = json.loads((Path(tmp) / "axis.json").read_text())
            self.assertTrue({"SL", "Target"} <= set(axis["daily"]["drawn"]))
            self.assertTrue(axis["intraday"]["drawn"])  # the 5m capture is on screen
            variants = json.loads((Path(tmp) / "variants.json").read_text())
            self.assertTrue(all(len(v) >= 7 for v in variants.values()))  # camera stills every ~0.12-0.3 s
            ranges = json.loads((Path(tmp) / "frame_ranges.json").read_text())
            self.assertTrue(ranges["cards"])


class EndCardTests(unittest.TestCase):
    def test_ta_end_card_has_no_text_in_bottom_zone(self):
        from realchart.ends import ta_end_image
        image = ta_end_image(None, "Paise Ki Pathshala", {"symbol": "NSE:X", "entry": 1241.8, "stop": 1271.6, "target": 1181.9},
                             ["रोज़ शाम एक स्टॉक का पूरा प्लान", "कल मिलते हैं"])
        self.assertEqual(image.size, (1080, 1920))
        from realchart.ends import render_end_card
        import tempfile as _t
        with _t.TemporaryDirectory() as tmp:
            png = Path(tmp) / "e.png"
            from unittest import mock
            with mock.patch("subprocess.run"), mock.patch("pathlib.Path.unlink"):
                render_end_card(Path(tmp) / "e.mp4", None, "Paise Ki Pathshala", plan={"symbol": "X", "entry": 1, "stop": 2, "target": 0})
            from PIL import Image as _I
            top = _I.open(png).crop((0, 600, 1080, 1440)).convert("L")
            ref = ta_end_image(None, "Paise Ki Pathshala", {"symbol": "X", "entry": 1, "stop": 2, "target": 0}).crop((0, 600, 1080, 1440)).convert("L")
            self.assertEqual(list(top.getdata())[:5000], list(ref.getdata())[:5000])  # no Latin name drawn over the plan
        bottom = image.crop((0, 1440, 1080, 1920)).convert("L")
        self.assertLess(max(bottom.getdata()), 80)  # only the dark grid down there


if __name__ == "__main__":
    unittest.main()
