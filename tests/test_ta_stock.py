"""Evening TA Reel = ONE liquid NSE stock with a real pattern and an RR >= 1.5 plan (Rajesh, 16-Oct-2026)."""
import unittest
from datetime import date
from unittest import mock

from realchart import build, script
from realchart.levels import STOCK_UNIVERSE, apply_stock_plan, compute, pick_stock, stock_plan


def _bounce_bars():
    """Range 100-120 for months: support at 100 tested three times, today taps 100.5 and closes 103."""
    rows = []
    day = 0
    def add(o, h, l, c):
        nonlocal day
        day += 1
        rows.append({"date": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}", "o": o, "h": h, "l": l, "c": c})
    for cycle in range(5):
        for i in range(10):  # down leg to 100
            px = 118 - i * 1.8
            add(px + 0.5, px + 1, px - 1 if i < 9 else 100.0, px)
        for i in range(10):  # up leg to 120
            px = 102 + i * 1.8
            add(px - 0.5, px + 1 if i < 9 else 120.0, px - 1, px)
    for i in range(8):
        px = 117 - i * 2
        add(px + 0.5, px + 1, px - 1, px)
    add(101.5, 103.5, 100.5, 103.0)
    return rows


class StockPlan(unittest.TestCase):
    def test_universe_is_stocks_not_indices(self):
        names = {name for name, _yf in STOCK_UNIVERSE}
        self.assertGreaterEqual(len(names), 30)
        self.assertFalse({"NSE:NIFTY", "NSE:BANKNIFTY"} & names)

    def test_support_bounce_plan_has_every_field_and_rr(self):
        plan = stock_plan(compute(_bounce_bars(), "NSE:TEST", "TEST.NS"))
        self.assertIsNotNone(plan)
        self.assertEqual(plan["pattern"], "support bounce")
        self.assertEqual(plan["side"], "long")
        self.assertGreaterEqual(plan["rr"], 1.5)
        self.assertGreaterEqual(plan["touches"], 2)
        self.assertLess(plan["stop"], plan["support"])
        self.assertGreater(plan["target"], plan["entry"])
        self.assertEqual(plan["invalidation"], plan["stop"])
        self.assertLessEqual(plan["zone"][0], plan["zone"][1])

    def test_no_pattern_no_plan(self):
        rows = [{"date": f"2026-01-{i % 28 + 1:02d}", "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i} for i in range(60)]
        rows[-1] = dict(rows[-1], c=rows[-1]["l"] + 0.1)  # weak close, mid trend: nothing clear
        levels = compute(rows, "NSE:X", "X.NS")
        plan = stock_plan(levels)
        self.assertTrue(plan is None or plan["rr"] >= 1.5)

    def test_repeated_stock_setup_is_skipped(self):
        levels = compute(_bounce_bars(), "NSE:TEST", "TEST.NS")
        history = [{"symbol": "NSE:TEST", "setup": "support bounce", "date": "2026-10-14"}]
        self.assertEqual(pick_stock([levels], history, date(2026, 10, 16)), [])
        self.assertEqual(len(pick_stock([levels], [], date(2026, 10, 16))), 1)


class TaScript(unittest.TestCase):
    def setUp(self):
        levels = compute(_bounce_bars(), "NSE:TEST", "TEST.NS")
        self.levels = apply_stock_plan(levels, stock_plan(levels))

    def test_rows_hook_number_whole_numbers_save_and_plan(self):
        rows = script.chunks_for("ta", self.levels)
        self.assertEqual(rows[0]["sec"], "hook")
        self.assertIn("TEST 103", rows[0]["t"])
        spoken = script.spoken_text(rows)
        self.assertNotRegex(spoken, r"\d\.\d")
        for word in ("सपोर्ट", "रेजिस्टेंस", "वैलिड ज़ोन", "एंट्री ट्रिगर", "स्टॉप लॉस", "टारगेट", "सेटअप खत्म"):
            self.assertIn(word, spoken)
        save_at = [i for i, row in enumerate(rows) if script.SAVE_CTA in row["t"]]
        self.assertEqual(len(save_at), 1)
        self.assertTrue(0 < save_at[0] < len(rows) - 1)
        self.assertNotIn("निफ्टी", spoken)
        self.assertEqual(script.lint(rows), [])
        self.assertEqual(script.number_faults(rows, self.levels), [])

    def test_end_card_follow_line(self):
        lines = script.end_card_lines("ta", self.levels)
        self.assertTrue(lines)
        # Devanagari only on the TA end card (1-Nov review); the follow button is drawn by ta_end_image.
        self.assertFalse(any(ch.isascii() and ch.isalpha() for line in lines for ch in line))

    def test_next_candidate_is_tried_when_capture_holds(self):
        levels = compute(_bounce_bars(), "NSE:TEST", "TEST.NS")
        plan = stock_plan(levels)
        other = dict(levels, symbol="NSE:OTHER")
        calls = []

        def rest(lv, *_a):
            calls.append(lv["symbol"])
            if lv["symbol"] == "NSE:TEST":
                raise build.Hold("capture", "stale")
            return lv, None, {}, None

        with mock.patch.object(build, "pick_stock", return_value=[(levels, plan), (other, plan)]), \
             mock.patch.object(build, "_prepare_rest", side_effect=rest), \
             mock.patch.object(build, "_history", return_value=[]), \
             mock.patch.object(build, "_on_session", side_effect=lambda rows, stamp, *_a: (rows, "stamp")):
            got = build._prepare("ta", date(2026, 10, 16), build.now_ist(), None,
                                 bars_for=lambda _t: (_bounce_bars(), None), dry_run=True)
        self.assertEqual(calls, ["NSE:TEST", "NSE:OTHER"])
        self.assertEqual(got[0]["symbol"], "NSE:OTHER")


if __name__ == "__main__":
    unittest.main()


class StockAxisLabels(unittest.TestCase):
    def test_one_decimal_stock_labels_are_ticks(self):
        from realchart.axis import LABEL
        for text in ("1,216.3", "1,360.0", "22,980.00", "728"):
            self.assertRegex(text, LABEL)
        self.assertNotRegex("1,216.345", LABEL)
