"""Reviewer truncation is not a verdict, and chart multiples match the script."""
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

import common
import market_data
import review_gate
import run_verify

ROOT_BANK = None


def _bank():
    global ROOT_BANK
    if ROOT_BANK is None:
        from pathlib import Path
        ROOT_BANK = json.loads(
            (Path(__file__).resolve().parent.parent / "topic_bank.json").read_text(encoding="utf-8")
        )["topics"]
    return ROOT_BANK


def _topic(key):
    return deepcopy(next(topic for topic in _bank() if topic["key"] == key))


class ReviewTruncationTests(unittest.TestCase):
    def test_length_finish_is_an_error_and_thinking_stays_off(self):
        partial = "VERDICT: [NEEDS FIX — 1 item]\n🔴 Risks / assumptions:\n- Data 3 mahi"
        message = SimpleNamespace(content=partial, reasoning_content="x" * 18, model_extra=None)
        choice = SimpleNamespace(message=message, finish_reason="length")
        completion = SimpleNamespace(choices=[choice])
        captured = {}

        class Completions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return completion

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-test"}, clear=False), \
             mock.patch("common.openai.OpenAI", return_value=client), \
             mock.patch("common.time.sleep"):
            with self.assertRaises(common.TruncatedResponse):
                common.claude(
                    "system", "user", model="deepseek-flash",
                    max_tokens=16000, retries=1, raise_on_truncate=True,
                )
        self.assertEqual(captured["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(captured["max_tokens"], 16000)

    def test_truncated_review_retries_once_larger_and_drops_the_partial(self):
        partial = "VERDICT: [NEEDS FIX — 1 item]\n- Data 3 mahi"
        full = "VERDICT: [READY TO PUBLISH]\n\n✅ Auto-checks pass:\nHook | Facts | Compliance\n"
        calls = []

        def fake_claude(system, user, model=None, max_tokens=16000, retries=3, raise_on_truncate=False):
            calls.append((max_tokens, raise_on_truncate, model))
            if len(calls) == 1:
                raise common.TruncatedResponse(max_tokens)
            return full

        cfg = {"models": {
            "verify_model": "deepseek-flash",
            "verify_max_tokens": 16000,
            "verify_retry_max_tokens": 32000,
        }}
        with mock.patch("run_verify.claude", side_effect=fake_claude):
            text = run_verify.fetch_review(cfg, "system", "user")
        self.assertEqual(text, full)
        self.assertNotIn("Data 3 mahi", text)
        self.assertNotIn("NEEDS FIX", text)
        self.assertEqual(calls, [
            (16000, True, "deepseek-flash"),
            (32000, True, "deepseek-flash"),
        ])
        script = "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- 71.07\n"
        decision = review_gate.decide(text, True, script, {"public_ok": True}, "short")
        self.assertTrue(decision["public_ready"])

    def test_a_second_truncation_is_not_parsed_as_needs_fix(self):
        def fake_claude(system, user, model=None, max_tokens=16000, retries=3, raise_on_truncate=False):
            raise common.TruncatedResponse(max_tokens)

        cfg = {"models": {"verify_model": "deepseek-flash", "verify_max_tokens": 16000,
                          "verify_retry_max_tokens": 32000}}
        with mock.patch("run_verify.claude", side_effect=fake_claude):
            with self.assertRaises(common.TruncatedResponse) as caught:
                run_verify.fetch_review(cfg, "system", "user")
        self.assertEqual(caught.exception.max_tokens, 32000)
        script = "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- x\n"
        blocked = review_gate.decide("", False, script, {"public_ok": True}, "short")
        self.assertFalse(blocked["public_ready"])
        self.assertIn("AI reviewer unavailable", blocked["verdict_line"])
        self.assertNotIn("1 item", blocked["verdict_line"])

    def test_a_complete_needs_fix_still_blocks(self):
        text = "VERDICT: [NEEDS FIX — 1 item]\n\n🔴 Risks / assumptions:\n- Data is three months old.\n"
        script = "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- x\n"
        decision = review_gate.decide(text, True, script, {"public_ok": True}, "short")
        self.assertFalse(decision["public_ready"])
        self.assertEqual(decision["exit_code"], 2)
        self.assertEqual(review_gate.parse_report_verdict(text), "NEEDS_FIX")


class ChartNumberTests(unittest.TestCase):
    def test_titan_multiple_uses_the_chart_divisor_not_the_rounded_71(self):
        self.assertEqual(f"{4231 / 71:.2f}", "59.59")
        figs = market_data.growth_figures("TITAN", 2010)
        self.assertEqual(figs["start_text"], "71.07")
        self.assertEqual(figs["end_text"], "4,231")
        self.assertEqual(figs["multiple_text"], "59.53")
        self.assertEqual(figs["multiple_label"], "= 59.53x")
        self.assertEqual(figs["equation"], "4,231  /  71.07")
        start = float(figs["start_text"].replace(",", ""))
        end = float(figs["end_text"].replace(",", ""))
        self.assertEqual(f"{end / start:.2f}", figs["multiple_text"])
        self.assertEqual(figs["lakh_text"], "59.5")

    def test_script_copy_follows_the_chart_and_keeps_the_hook_closed(self):
        topic = _topic("titan_2010")
        topic["beats"]["matlab"][1] = "करीब 59.99 गुना"
        topic["facts"] = [
            fact if fact != "59.53 गुना" else "59.99 गुना" for fact in topic["facts"]
        ]
        market_data.sync_topic_numbers(topic)
        blob = " ".join([
            topic["beats"]["pehchan"][1],
            topic["beats"]["matlab"][1],
            topic["beats"]["karein"][1],
            *topic["facts"],
        ])
        self.assertIn("71.07 से 4,231", blob)
        self.assertIn("59.53 गुना", blob)
        self.assertIn("2010-01-04 को 71.07", blob)
        self.assertNotIn("59.99", blob)
        self.assertNotIn("59.59", blob)
        hook = " ".join(topic["beats"]["hook"])
        self.assertNotIn("59.53", hook)
        self.assertNotIn("4,231", hook)
        self.assertNotIn("4231", hook)

    def test_every_chart_series_script_matches_the_printed_division(self):
        for key, year in market_data.TOPIC_YEAR.items():
            name = market_data.TOPIC_SERIES[key][0]
            figs = market_data.growth_figures(name, year)
            if figs["multiple"] < 1:
                continue
            start = float(figs["start_text"].replace(",", ""))
            end = float(figs["end_text"].replace(",", ""))
            self.assertEqual(
                f"{end / start:.2f}", figs["multiple_text"],
                f"{key}: {figs['equation']} is not {figs['multiple_text']}x",
            )
            topic = _topic(key)
            market_data.sync_topic_numbers(topic)
            joined = " ".join(str(x) for pair in topic["beats"].values() for x in pair)
            joined += " " + " ".join(topic["facts"])
            self.assertIn(figs["multiple_text"] + " गुना", joined, key)
            self.assertIn(figs["start_text"], joined, key)
            self.assertIn(figs["end_text"], joined, key)
