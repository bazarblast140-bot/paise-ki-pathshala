"""The FACT LEDGER of a trade-plan Short names where each plan figure comes from.

Run 39008857526: pat_stop_loss passed self-review (98, sync 100, 41.2s) and
then failed final verify with "Fact Ledger khaali — sab figures UNVERIFIED",
because the ledger only held the topic line "पूरा ट्रेड प्लान".
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import common
import trade_plan


def _topic(key):
    return {"key": key, "cat": "PATTERN", "facts": ["पूरा ट्रेड प्लान"], "_approved_bank": True,
            "_live_short": True, "topic": key}


class PlanFactLedgerTests(unittest.TestCase):
    def test_every_plan_number_is_in_the_ledger_with_its_source(self):
        for key in ("pat_stop_loss", "pat_triangle", "pat_black_crows"):
            plan = trade_plan.plan_for(key)
            text = "\n".join(board_short.plan_fact_lines(_topic(key)))
            self.assertIn("not live market data", text)
            for value in (plan.entry, plan.sl, plan.target, plan.risk, plan.reward):
                self.assertIn(trade_plan.fmt(value), text, key)
            self.assertIn(plan.rr_text, text)
            for candle in plan.ohlc:
                self.assertIn(f"C {trade_plan.fmt(candle[3])}", text)

    def test_a_topic_without_a_plan_gets_no_plan_rows(self):
        self.assertEqual(board_short.plan_fact_lines({"key": "atr_sl", "facts": ["x"]}), [])
        self.assertEqual(board_short.plan_fact_lines({}), [])

    def test_script_txt_ledger_carries_the_plan_rows(self):
        topic = _topic("pat_stop_loss")
        beats = {b: f"{b} line" for b in trade_plan.BEATS}
        narration = {"title": "Stop loss kahan?", "beats": beats, "hook_line": "hook"}
        times = {}
        t = 0.0
        for beat in trade_plan.BEATS:
            times[beat] = (t, t + 5.0)
            t += 5.0
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(common, "RUN", Path(tmp)):
            board_short.write_compat(topic, narration, dict(beats), times)
            script = (Path(tmp) / "script.txt").read_text(encoding="utf-8")
        ledger = script[script.index("FACT LEDGER:"):]
        self.assertIn("- पूरा ट्रेड प्लान", ledger)
        self.assertIn("Entry 116, stop-loss 104, target 140", ledger)
        self.assertIn("risk:reward = 1:2", ledger)


if __name__ == "__main__":
    unittest.main()
