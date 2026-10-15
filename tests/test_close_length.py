import unittest

from realchart import script

LEVELS = {
    "symbol": "NSE:NIFTY", "open": 25210.4, "high": 25330.15, "low": 25120.6, "close": 25281.9,
    "chg_pct": 0.42, "pivot": 25244.2, "r1": 25367.8, "r2": 25453.7, "s1": 25158.3, "s2": 25034.7,
    "ema20": 25190.5, "ema50": 25010.2, "rsi14": 57.3,
    "sl_zone": [25158.3, 25190.5], "target_zone": [25367.8, 25453.7],
    "values_from": "tradingview", "last_date": "2026-10-15",
}


class CloseLength(unittest.TestCase):
    def test_typical_close_script_reaches_forty_seconds(self):
        rows = script.chunks_for("close", LEVELS)
        est = script.estimate_seconds(rows)
        self.assertGreaterEqual(est, 40.0, script.spoken_text(rows))
        self.assertLessEqual(est, 50.0)

    def test_close_script_passes_lint_and_numbers(self):
        rows = script.chunks_for("close", LEVELS)
        self.assertEqual(script.lint(rows), [])
        self.assertEqual(script.number_faults(rows, LEVELS), [])
        spoken = script.spoken_text(rows)
        self.assertIn("ट्रेड सलाह नहीं", spoken)
        self.assertNotIn("बाउंस possible", spoken)


if __name__ == "__main__":
    unittest.main()
