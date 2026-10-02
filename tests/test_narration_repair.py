"""Narration word tolerance and the one-shot beat repair.

A beat may land ±20% from its target. The total may land in 150-190
(target 160-180). After the first real draft, one repair rewrites only the
beats that missed. An empty or timed-out reply stays inside the 4-call cap
and never becomes the topic-card template.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short


HOOK = "क्या यह सेटअप गलत है?"
CFG = {"models": {"script_model": "deepseek-flash"}}


def _topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना", "ये संभावना है, गारंटी नहीं"],
    }


def _pad(n, prefix=""):
    cycle = ["नियम", "समझिए", "चार्ट", "पर"]
    prefix = " ".join(str(prefix).split())
    have = prefix.split() if prefix else []
    if len(have) >= n:
        return " ".join(have[:n])
    rest = [cycle[i % 4] for i in range(n - len(have))]
    return " ".join(have + rest)


def _script(hook=25, pehchan=42, matlab=50, karein=46, hook_line=HOOK):
    return {
        "title": "सेटअप का नियम समझिए",
        "hook_line": hook_line,
        "beats": {
            "hook": _pad(hook, hook_line),
            "pehchan": _pad(pehchan, "नियम"),
            "matlab": _pad(matlab, "नियम"),
            "karein": _pad(karein, "नियम"),
        },
    }


class WordWindowTests(unittest.TestCase):
    def test_windows_are_twenty_percent_and_hook_stays_short(self):
        self.assertEqual(board_short.beat_word_window(25), (20, 30))
        self.assertEqual(board_short.beat_word_window(42), (34, 50))
        self.assertEqual(board_short.beat_word_window(50), (40, 60))
        self.assertEqual(board_short.beat_word_window(46), (37, 55))
        self.assertEqual(board_short.TOTAL_WORDS, (150, 190))
        self.assertEqual(board_short.TOTAL_WORD_TARGET, (160, 180))
        self.assertEqual(board_short.NARRATION_CALL_CAP, 4)

    def test_prompt_states_each_target_and_a_short_example(self):
        text = board_short.NARR_SYSTEM
        self.assertIn("target exactly 25 words", text)
        self.assertIn("target exactly 42 words", text)
        self.assertIn("target exactly 50 words", text)
        self.assertIn("target exactly 46 words", text)
        self.assertIn("Accept 150-190", text)
        self.assertIn('"hook":"<exactly 25 words, first sentence is hook_line>"', text)

    def test_off_by_a_few_words_passes_and_a_long_hook_does_not(self):
        topic = _topic()
        # matlab 45 and karein 55 sat outside the old hard windows.
        near = _script(hook=30, pehchan=34, matlab=45, karein=55)
        self.assertEqual(board_short.check_narration(near, topic), [])
        long_hook = _script(hook=41, pehchan=35, matlab=45, karein=55)
        fails = board_short.check_narration(long_hook, topic)
        self.assertTrue(any(f.startswith("hook beat 41") for f in fails))
        self.assertFalse(any(f.startswith("pehchan") for f in fails))
        short = _script(hook=25, pehchan=42, matlab=50, karein=20)
        total = sum(board_short.beat_word_counts(short).values())
        self.assertLess(total, 150)
        total_fails = board_short.check_narration(short, topic)
        self.assertTrue(any("150-190" in f for f in total_fails))

    def test_invented_number_still_fails(self):
        topic = _topic()
        script = _script()
        script["beats"]["matlab"] = _pad(49, "नियम") + " 99999"
        fails = board_short.check_narration(script, topic)
        self.assertTrue(any("99999" in f for f in fails))


class RepairLoopTests(unittest.TestCase):
    def _run(self, side_effect):
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append({
                "user": user,
                "model": model,
                "retries": retries,
                "attempts": attempts,
            })
            item = side_effect[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            return item

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(
                 board_short, "fallback_narration",
                 side_effect=AssertionError("template must not be built"),
             ):
            try:
                result = board_short.narration(_topic(), CFG)
            except SystemExit as exc:
                result = exc
            block_path = Path(tmp) / "narration_block.json"
            block = json.loads(block_path.read_text(encoding="utf-8")) if block_path.exists() else None
        return calls, result, block

    def test_one_repair_rewrites_only_the_failing_beat(self):
        bad = _script(hook=41, pehchan=42, matlab=50, karein=46)
        good = _script(hook=25)
        good["beats"]["pehchan"] = "यह बदलना नहीं चाहिए " + _pad(38)
        calls, result, block = self._run([bad, good])
        self.assertIsNone(block)
        script, source = result
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["attempts"], 1)
        self.assertEqual(calls[0]["retries"], 1)
        repair = calls[1]["user"]
        self.assertIn("REWRITE hook: current 41 words, target exactly 25 words", repair)
        self.assertIn("KEEP pehchan VERBATIM", repair)
        self.assertIn(bad["beats"]["pehchan"], repair)
        self.assertEqual(script["beats"]["pehchan"], bad["beats"]["pehchan"])
        self.assertEqual(script["beats"]["matlab"], bad["beats"]["matlab"])
        self.assertEqual(script["beats"]["karein"], bad["beats"]["karein"])
        self.assertTrue(script["beats"]["hook"].startswith(HOOK))
        self.assertEqual(len(script["beats"]["hook"].split()), 25)

    def test_near_miss_is_accepted_without_a_repair(self):
        near = _script(hook=30, pehchan=34, matlab=45, karein=55)
        calls, result, block = self._run([near])
        self.assertIsNone(block)
        script, source = result
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 1)
        self.assertEqual(script["beats"]["matlab"], near["beats"]["matlab"])

    def test_timeout_and_empty_retry_inside_the_cap_and_skip_the_template(self):
        good = _script()
        calls, result, block = self._run([
            TimeoutError("no response in 75s"),
            {"verdict": "FAIL", "ai_verdict_valid": False},
            good,
        ])
        self.assertIsNone(block)
        script, source = result
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 3)
        self.assertLessEqual(len(calls), 4)
        self.assertIn("empty, timed out", calls[2]["user"])
        self.assertNotIn("REWRITE", calls[2]["user"])

    def test_four_timeouts_block_without_the_template(self):
        calls, result, block = self._run([TimeoutError("timed out")] * 4)
        self.assertIsInstance(result, SystemExit)
        self.assertEqual(len(calls), 4)
        self.assertTrue(block["blocked"])
        self.assertIn("topic-card", block["reason"])
        self.assertIn("empty or timeout", block["reason"])

    def test_a_contentful_repair_is_not_repeated(self):
        bad = _script(hook=41)
        still = _script(hook=40)
        calls, result, block = self._run([bad, still, _script()])
        self.assertIsInstance(result, SystemExit)
        self.assertEqual(len(calls), 2)
        self.assertIn("REWRITE hook: current 41 words, target exactly 25 words", calls[1]["user"])
        self.assertIn("topic-card", block["reason"])


if __name__ == "__main__":
    unittest.main()
