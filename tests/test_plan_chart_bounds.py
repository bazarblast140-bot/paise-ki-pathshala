"""Every candle, wick, stop-break candle and level stays on the board for the 7 plan topics."""
import unittest

import numpy as np
from PIL import Image

import board_kit as K
import motion_lib
import trade_plan

SEVEN = (
    "pat_bear_engulf", "pat_shooting_star", "pat_double_bottom", "pat_evening_star",
    "pat_bear_flag", "pat_inside_bar", "pat_ema_cross",
)


def _beats():
    return {beat: (i * 5.0, i * 5.0 + 5.0) for i, beat in enumerate(trade_plan.BEATS)}


class PlanChartBoundsTests(unittest.TestCase):
    def _plot(self, plan):
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        ax = motion_lib._axes(box, len(plan.ohlc), lo, hi, pad, ypad=motion_lib.MOTION_YPAD)
        return box, ax

    def test_every_price_maps_inside_the_plot(self):
        for key in SEVEN:
            plan = trade_plan.plan_for(key)
            box, ax = self._plot(plan)
            top, bottom = box[1] + ax.pt, box[3] - ax.pb
            _o, _c, high, low = motion_lib._failure_prices(plan)
            prices = [v for c in plan.ohlc for v in (c[1], c[2])]
            prices += [plan.entry, plan.sl, plan.target, plan.sr, high, low]
            for price in prices:
                y = ax.y(float(price))
                with self.subTest(key=key, price=price):
                    self.assertGreaterEqual(y, top - 0.5)
                    self.assertLessEqual(y, bottom + 0.5)

    def test_no_candle_pixel_leaves_the_board_through_the_stop_break(self):
        beats = _beats()
        topic_clock = {"_beat_times": beats}
        for key in SEVEN:
            plan = trade_plan.plan_for(key)
            clock = motion_lib.clock_from(topic_clock, plan)
            for t in (3.0, 12.0, 23.0, 33.0, 36.0, 38.0, 39.9):
                img = Image.new("RGB", (K.W, K.H), (0, 0, 0))
                motion_lib.draw_plan(img, plan, trade_plan.overlays(plan), t, clock)
                rgb = np.asarray(img, dtype=np.int16)
                outside = np.ones(rgb.shape[:2], dtype=bool)
                outside[K.BOARD[1]:K.BOARD[3], K.BOARD[0]:K.BOARD[2]] = False
                for color in (motion_lib.BEAR, motion_lib.BULL):
                    near = np.abs(rgb - np.array(color, dtype=np.int16)).sum(axis=2) <= 18
                    with self.subTest(key=key, t=t, color=color):
                        self.assertEqual(int((near & outside).sum()), 0)


if __name__ == "__main__":
    unittest.main()


def _video_beats():
    """A 44s plan clock shaped like the bearish-engulfing render."""
    edges = (0.0, 4.9, 10.9, 16.4, 20.7, 26.5, 30.2, 36.5, 43.9)
    return {beat: (edges[i], edges[i + 1]) for i, beat in enumerate(trade_plan.BEATS)}


class AnimatedFrameTests(unittest.TestCase):
    """The moving chart at the times the review looked at, for all 7 topics."""

    TIMES = (1.0, 5.0, 6.0, 9.0, 11.5, 17.0, 22.0, 24.0, 28.0, 31.0, 34.0, 36.0, 40.0, 42.0)

    def _frame(self, key, t):
        plan = trade_plan.plan_for(key)
        topic = {"key": key, "_beat_times": _video_beats()}
        clock = motion_lib.clock_from(topic, plan)
        img = Image.new("RGB", (K.W, K.H), (0, 0, 0))
        state = motion_lib.draw_plan(img, plan, trade_plan.overlays(plan), t, clock)
        return plan, img, state

    def test_candles_and_labels_stay_inside_the_chart_box(self):
        for key in SEVEN:
            for t in self.TIMES:
                plan, img, state = self._frame(key, t)
                fx0, fy0, fx1, fy1 = (int(round(v)) for v in state["frame"])
                rgb = np.asarray(img, dtype=np.int16)
                outside = np.ones(rgb.shape[:2], dtype=bool)
                outside[fy0 + 2:fy1 - 1, fx0 + 2:fx1 - 1] = False
                for color in (motion_lib.BEAR, motion_lib.BULL):
                    near = np.abs(rgb - np.array(color, dtype=np.int16)).sum(axis=2) <= 18
                    with self.subTest(key=key, t=t, color=color):
                        self.assertEqual(int((near & outside).sum()), 0)
                for rect in state["pill_rects"]:
                    with self.subTest(key=key, t=t, pill=rect):
                        self.assertGreaterEqual(rect[1], fy0)
                        self.assertLessEqual(rect[3], fy1)
                        self.assertGreaterEqual(rect[0], fx0)
                        self.assertLessEqual(rect[2], fx1)

    def test_labels_never_overlap_and_no_line_crosses_a_label(self):
        for key in SEVEN:
            for t in self.TIMES:
                plan, img, state = self._frame(key, t)
                rects = state["pill_rects"]
                for i, a in enumerate(rects):
                    for b in rects[i + 1:]:
                        overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                        with self.subTest(key=key, t=t, a=a, b=b):
                            self.assertFalse(overlap)
                box, lo, hi, pad = motion_lib.motion_geometry(plan)
                ax = motion_lib._axes(box, len(plan.ohlc), lo, hi, pad, ypad=motion_lib.MOTION_YPAD)
                for name, level in state["levels"].items():
                    if not level["drawn"]:
                        continue
                    y = ax.y(level["price"])
                    for r in rects:
                        with self.subTest(key=key, t=t, line=name, pill=r):
                            self.assertFalse(r[1] - 2 <= y <= r[3] + 2)

    def test_focus_candle_is_in_place_before_the_highlight(self):
        for key in SEVEN:
            plan = trade_plan.plan_for(key)
            beats = _video_beats()
            clock = motion_lib.clock_from({"key": key, "_beat_times": beats}, plan)
            windows = clock["candle_windows"]
            starts = [w[0] for w in windows]
            with self.subTest(key=key):
                # Left to right in time, so nothing floats on the far right early.
                self.assertEqual(starts, sorted(starts))
                self.assertLessEqual(windows[plan.focus][1], clock["highlight"][0] + 1e-9)
                for index in range(plan.focus + 1, len(plan.ohlc)):
                    self.assertGreaterEqual(windows[index][0], beats["pehchan"][1] - 1e-9)
            for t in (beats["pehchan"][0] + 0.05, 6.0, 9.0):
                _plan, _img, state = self._frame(key, t)
                with self.subTest(key=key, t=t):
                    self.assertEqual(state["candles"][plan.focus], 1.0)
                    for index in range(plan.focus + 1, len(plan.ohlc)):
                        self.assertEqual(state["candles"][index], 0.0)
