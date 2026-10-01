"""DeepSeek replies: thinking mode must not be reported as an empty body."""
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


if __name__ == "__main__":
    unittest.main()
