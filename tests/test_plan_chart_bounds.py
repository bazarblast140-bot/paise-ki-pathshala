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
