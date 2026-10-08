"""Approved topics render all eight beats, and a missing beat is a gate, not a crash."""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import board_kit
import board_scenes
import board_short
import pattern_charts
import trade_plan


class PlanBeatRenderTests(unittest.TestCase):
    def test_every_approved_topic_renders_all_eight_beats(self):
        import approved_bank
        self.assertEqual(pattern_charts.gate_problems(), [])
        scripts = approved_bank.all_scripts()
        self.assertGreaterEqual(len(scripts), 17)
        for script in scripts:
            topic = approved_bank.topic_from(script)
            marks = pattern_charts.marks_for(script["key"])
            for beat in trade_plan.BEATS:
                self.assertTrue(str(marks.get(beat) or "").strip(), (script["key"], beat))
                plain, marked = board_scenes.render_beat(
                    script["key"], beat, board_kit.background(),
                )
                pics = board_short.beat_images(topic, beat)
                for image in (plain, marked, *pics):
                    self.assertEqual(image.size, (1080, 1920), (script["key"], beat))

    def test_a_missing_mark_uses_a_default_label(self):
        self.assertEqual(pattern_charts.mark_for({"marks": {"hook": "X"}}, "context"), "TREND + SUPPORT")
        self.assertEqual(pattern_charts.mark_for({"marks": {}}, "nope"), "PLAN")
        for spec in pattern_charts.PATTERNS.values():
            for beat in trade_plan.BEATS:
                self.assertTrue(pattern_charts.mark_for(spec, beat))

    def test_other_beat_factories_accept_the_plan_beats(self):
        keys = (
            "products", "t_plus_1", "one_percent", "rr_breakeven", "circuit",
            "bse_1875", "preopen", "depository", "risk_daily_loss_limit",
        )
        for key in keys:
            for beat in ("context", "entry", "sl", "target", "invalidation", "cta"):
                plain, marked = board_scenes.render_beat(key, beat, board_kit.background())
                self.assertEqual(plain.size, (1080, 1920), (key, beat))
                self.assertEqual(marked.size, (1080, 1920), (key, beat))


class ChartGateTests(unittest.TestCase):
    def test_gate_rejects_a_missing_beat_and_a_missing_mark(self):
        beats = {beat: "line" for beat in trade_plan.BEATS}
        beats["context"] = "  "
        missing_beat = pattern_charts.gate_problems(scripts=[{"key": "pat_sample", "beats": beats}])
        self.assertTrue(any("missing beats" in item and "context" in item for item in missing_beat))
        marks = {beat: "LABEL" for beat in trade_plan.BEATS}
        del marks["entry"]
        filled = {beat: "line" for beat in trade_plan.BEATS}
        missing_mark = pattern_charts.gate_problems(
            scripts=[{"key": "pat_sample", "beats": filled}],
            marks={"pat_sample": marks},
        )
        self.assertTrue(any("missing chart marks" in item and "entry" in item for item in missing_mark))
        buf = io.StringIO()
        with mock.patch.object(
            pattern_charts, "gate_problems",
            return_value=["pat_hammer missing beats: context"],
        ):
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as caught:
                    pattern_charts.reject_chart_gate()
        self.assertEqual(caught.exception.code, 1)
        text = buf.getvalue()
        self.assertIn("[GATE]", text)
        self.assertIn("context", text)
        self.assertNotIn("Traceback", text)
