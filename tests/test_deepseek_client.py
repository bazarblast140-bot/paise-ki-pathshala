"""DeepSeek replies: thinking mode must not be reported as an empty body."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import common


class DeepSeekMessageTests(unittest.TestCase):
    def test_content_wins_over_reasoning(self):
        message = SimpleNamespace(content='{"ok": true}', reasoning_content="thinking")
        self.assertEqual(common.deepseek_message_text(message), '{"ok": true}')

    def test_empty_content_uses_reasoning_content(self):
        message = SimpleNamespace(content="", reasoning_content='{"title":"x"}')
        self.assertEqual(common.deepseek_message_text(message), '{"title":"x"}')

    def test_reasoning_from_model_extra(self):
        message = SimpleNamespace(content=None, reasoning_content=None, model_extra={"reasoning_content": "answer"})
        self.assertEqual(common.deepseek_message_text(message), "answer")

    def test_call_disables_thinking_and_reads_reasoning_when_content_is_empty(self):
        message = SimpleNamespace(content="", reasoning_content="TITLE: real answer", model_extra=None)
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
            text = common.claude("system", "user", model="deepseek-flash", max_tokens=2000, retries=1)
        self.assertEqual(text, "TITLE: real answer")
        self.assertEqual(captured["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(captured["model"], "deepseek-flash")
        self.assertEqual(captured["max_tokens"], 2000)


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class DeepSeekBudgetTests(unittest.TestCase):
    def test_defaults_stay_inside_one_ladder(self):
        self.assertGreaterEqual(common.LLM_ATTEMPT_TIMEOUT, 60)
        self.assertLessEqual(common.LLM_ATTEMPT_TIMEOUT, 90)
        self.assertGreaterEqual(common.LLM_TOTAL_BUDGET, 3 * common.LLM_ATTEMPT_TIMEOUT)
        self.assertLess(common.LLM_TOTAL_BUDGET, 3 * 3 * common.LLM_ATTEMPT_TIMEOUT)

    def test_request_timeout_is_split_and_under_90s(self):
        message = SimpleNamespace(content="ok", reasoning_content="", model_extra=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        completion = SimpleNamespace(choices=[choice])
        captured = {}

        def create(**kwargs):
            return completion

        class OpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        env = {"DEEPSEEK_API_KEY": "sk-test"}
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch("common.openai.OpenAI", OpenAI), \
             mock.patch("common.time.sleep"):
            os.environ.pop("LLM_CALL_TIMEOUT", None)
            os.environ.pop("LLM_TOTAL_BUDGET", None)
            text = common.claude("system", "user", model="deepseek-flash", retries=1)
        self.assertEqual(text, "ok")
        timeout = captured["timeout"]
        read = getattr(timeout, "read", timeout)
        self.assertGreaterEqual(float(read), 60)
        self.assertLessEqual(float(read), 90)
        connect = getattr(timeout, "connect", None)
        if connect is not None:
            self.assertLessEqual(float(connect), 15)
        self.assertEqual(captured["max_retries"], 0)

    def test_backoff_is_exponential_with_jitter(self):
        with mock.patch("common.random.uniform", return_value=0.25):
            self.assertEqual(common._retry_delay(0), 1.25)
            self.assertEqual(common._retry_delay(1), 2.25)
            self.assertEqual(common._retry_delay(2), 4.25)
            self.assertLessEqual(common._retry_delay(10), 20)

    def test_transport_failure_is_not_retried_as_three_full_ladders(self):
        calls = {"n": 0}

        def create(**kwargs):
            calls["n"] += 1
            raise TimeoutError("no response in 75s")

        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-test"}, clear=False), \
             mock.patch("common.openai.OpenAI", return_value=_client(create)), \
             mock.patch("common.time.sleep"):
            os.environ.pop("LLM_TOTAL_BUDGET", None)
            out = common.claude_json("system", "user", retries=3)
        self.assertEqual(calls["n"], 3)
        self.assertLess(calls["n"], 9)
        self.assertEqual(out["verdict"], "FAIL")
        self.assertFalse(out["ai_verdict_valid"])
        self.assertIn("NOT READY", out["notes"])

    def test_wall_budget_stops_the_nested_loop(self):
        calls = {"n": 0}
        clock = {"t": 0.0}

        def create(**kwargs):
            calls["n"] += 1
            clock["t"] += 100
            raise TimeoutError("no response in 75s")

        def mono():
            return clock["t"]

        with mock.patch.dict("os.environ", {
            "DEEPSEEK_API_KEY": "sk-test",
            "LLM_TOTAL_BUDGET": "50",
            "LLM_CALL_TIMEOUT": "75",
        }, clear=False), \
             mock.patch("common.openai.OpenAI", return_value=_client(create)), \
             mock.patch("common.time.monotonic", mono), \
             mock.patch("common.time.sleep"):
            out = common.claude_json("system", "user", retries=3)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(out["verdict"], "FAIL")
        self.assertFalse(out["ai_verdict_valid"])


if __name__ == "__main__":
    unittest.main()
