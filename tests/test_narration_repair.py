"""Narration word tolerance and the one-shot beat repair.

A beat may land ±30% from its target. The total may land in 100-138
(target 112-126). After the first real draft, one repair rewrites only the
beats that missed. An empty or timed-out reply stays inside the 4-call cap
and never becomes the topic-card template.
"""
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import hook_check
import scene_pace


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


def _script(hook=22, pehchan=29, matlab=36, karein=32, hook_line=HOOK):
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
    def test_windows_are_thirty_percent_and_hook_stays_short(self):
        self.assertEqual(board_short.beat_word_window(22), (15, 29))
        self.assertEqual(board_short.beat_word_window(29), (20, 38))
        self.assertEqual(board_short.beat_word_window(36), (25, 47))
        self.assertEqual(board_short.beat_word_window(32), (22, 42))
        self.assertEqual(board_short.TOTAL_WORDS, (100, 138))
        self.assertEqual(board_short.TOTAL_WORD_TARGET, (112, 126))
        self.assertEqual(board_short.NARRATION_CALL_CAP, 4)

    def test_prompt_states_each_target_and_a_short_example(self):
        text = board_short.NARR_SYSTEM
        self.assertIn("target exactly 22 words", text)
        self.assertIn("target exactly 29 words", text)
        self.assertIn("target exactly 36 words", text)
        self.assertIn("target exactly 32 words", text)
        self.assertIn("Accept 100-138", text)
        self.assertIn('"hook":"<exactly 22 words, first sentence is hook_line>"', text)

    def test_off_by_a_few_words_passes_and_a_long_hook_does_not(self):
        topic = _topic()
        # matlab 45 and karein 55 sat outside the old hard windows.
        near = _script(hook=27, pehchan=30, matlab=40, karein=36)
        self.assertEqual(board_short.check_narration(near, topic), [])
        long_hook = _script(hook=41, pehchan=30, matlab=40, karein=40)
        fails = board_short.check_narration(long_hook, topic)
        self.assertTrue(any(f.startswith("hook beat 41") for f in fails))
        self.assertFalse(any(f.startswith("pehchan") for f in fails))
        short = _script(hook=22, pehchan=29, matlab=36, karein=10)
        total = sum(board_short.beat_word_counts(short).values())
        self.assertLess(total, 100)
        total_fails = board_short.check_narration(short, topic)
        self.assertTrue(any("100-138" in f for f in total_fails))

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
        bad = _script(hook=41)
        good = _script(hook=22)
        good["beats"]["pehchan"] = "यह बदलना नहीं चाहिए " + _pad(26)
        calls, result, block = self._run([bad, good])
        self.assertIsNone(block)
        script, source = result
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["attempts"], 1)
        self.assertEqual(calls[0]["retries"], 1)
        repair = calls[1]["user"]
        self.assertIn("REWRITE hook: current 41 words, target exactly 22 words", repair)
        self.assertIn("KEEP pehchan VERBATIM", repair)
        self.assertIn(bad["beats"]["pehchan"], repair)
        self.assertEqual(script["beats"]["pehchan"], bad["beats"]["pehchan"])
        self.assertEqual(script["beats"]["matlab"], bad["beats"]["matlab"])
        self.assertEqual(script["beats"]["karein"], bad["beats"]["karein"])
        self.assertTrue(script["beats"]["hook"].startswith(HOOK))
        self.assertEqual(len(script["beats"]["hook"].split()), 22)

    def test_near_miss_is_accepted_without_a_repair(self):
        near = _script(hook=27, pehchan=30, matlab=40, karein=36)
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

    def test_a_four_beat_script_gets_two_repairs_and_no_more(self):
        bad = _script(hook=41)
        still = _script(hook=40)
        calls, result, block = self._run([bad, still, still, _script()])
        self.assertIsInstance(result, SystemExit)
        self.assertEqual(len(calls), 3)
        self.assertIn("REWRITE hook: current 41 words, target exactly 22 words", calls[1]["user"])
        self.assertIn("topic-card", block["reason"])

    def test_the_second_repair_can_land_the_script(self):
        calls, result, block = self._run([_script(hook=41), _script(hook=40), _script()])
        self.assertNotIsInstance(result, SystemExit)
        self.assertEqual(len(calls), 3)


DARK_HOOK = "काले बादल छा गए क्यों?"
_VAGUE = ["समझिए", "चार्ट", "पर", "देखिए", "कैंडल", "भाव", "तेज़ी", "बादल"]


def _dark_cloud():
    bank = json.loads((Path(__file__).resolve().parents[1] / "topic_bank.json").read_text(encoding="utf-8"))
    return next(topic for topic in bank["topics"] if topic["key"] == "dark_cloud")


def _vague_words(n, prefix=""):
    prefix = " ".join(str(prefix).split())
    have = prefix.split() if prefix else []
    if len(have) >= n:
        return " ".join(have[:n])
    rest = [_VAGUE[i % len(_VAGUE)] for i in range(n - len(have))]
    return " ".join(have + rest)


def _dark_draft(hook, pehchan, matlab, karein, *, concrete_karein=True):
    """Word counts from the dark_cloud log. Vague beats have no number, level, or step."""
    return {
        "title": "काले बादल का मतलब",
        "hook_line": DARK_HOOK,
        "beats": {
            "hook": _vague_words(hook, DARK_HOOK),
            "pehchan": _vague_words(pehchan),
            "matlab": _vague_words(matlab),
            "karein": _vague_words(karein, "गलती" if concrete_karein else ""),
        },
    }


def _dark_fixed():
    hook = _pad(22, DARK_HOOK + " उदाहरण के लिए भाव 100 पर काला बादल देखिए")
    return {
        "title": "काले बादल का मतलब",
        "hook_line": DARK_HOOK,
        "beats": {
            "hook": hook,
            "pehchan": _pad(29, "नियम"),
            "matlab": _pad(36, "कदम"),
            "karein": _pad(32, "गलती"),
        },
    }


class DarkCloudRepairTests(unittest.TestCase):
    def test_labelled_example_numbers_only_on_evergreen_topics(self):
        cloud = _dark_cloud()
        self.assertTrue(hook_check.allows_example_numbers(cloud))
        labelled = "उदाहरण के लिए भाव 100 पर देखिए।"
        self.assertEqual(hook_check.unsourced_numbers(labelled, cloud), set())
        self.assertEqual(hook_check.unsourced_numbers("भाव 100 पर देखिए।", cloud), {"100"})
        real = {"key": "titan_2010", "cat": "REAL DATA", "topic": "Titan", "beats": {}, "facts": [], "thumb": []}
        self.assertFalse(hook_check.allows_example_numbers(real))
        self.assertEqual(hook_check.unsourced_numbers(labelled, real), {"100"})
        self.assertEqual(
            board_short.number_format_notes(["काले बादल"], spoken="उदाहरण 100", caption="100", ignore={"100"}),
            [],
        )
        self.assertTrue(board_short.number_format_notes(["एसएल 772.4"], spoken="एसएल 772.41", caption=""))

    def test_logged_failures_repair_vague_beats_and_shrink_the_hook(self):
        cloud = _dark_cloud()
        cases = [
            {"hook": 47, "pehchan": 42, "matlab": 43, "karein": 46, "keep": ("karein",)},
            {"hook": 48, "pehchan": 56, "matlab": 64, "karein": 58, "keep": ()},
            {"hook": 26, "pehchan": 34, "matlab": 33, "karein": 38, "keep": ("karein",)},
        ]
        for case in cases:
            with self.subTest(hook=case["hook"], total=(
                case["hook"] + case["pehchan"] + case["matlab"] + case["karein"]
            )):
                bad = _dark_draft(**{k: case[k] for k in ("hook", "pehchan", "matlab", "karein")})
                self.assertEqual(
                    scene_pace.vague_beats(bad["beats"]),
                    ["hook", "pehchan", "matlab"],
                )
                calls = []

                def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None, _calls=calls):
                    _calls.append(user)
                    if len(_calls) == 1:
                        return bad
                    return _dark_fixed()

                with tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.object(board_short, "RUN", Path(tmp)), \
                     mock.patch.object(board_short, "claude_json", side_effect=fake), \
                     mock.patch.object(
                         board_short, "fallback_narration",
                         side_effect=AssertionError("template must not be built"),
                     ):
                    script, source = board_short.narration(cloud, CFG)
                self.assertEqual(source, "llm")
                self.assertEqual(len(calls), 2)
                repair = calls[1]
                self.assertIn(
                    f"REWRITE hook: current {case['hook']} words, target exactly 22 words (accept 15-29).",
                    repair,
                )
                self.assertIn("The hook must be 15-29 words.", repair)
                for beat in ("hook", "pehchan", "matlab"):
                    row = next(line for line in repair.splitlines() if line.startswith(f"REWRITE {beat}:"))
                    self.assertIn("This beat is vague: it needs a concrete number, level, or step", row)
                karein_row = next(line for line in repair.splitlines() if "karein" in line and line.startswith(("REWRITE", "KEEP")))
                self.assertNotIn("is vague", karein_row)
                self.assertIn("evergreen topic with no market data", repair)
                self.assertIn("उदाहरण", repair)
                self.assertGreaterEqual(len(script["beats"]["hook"].split()), 20)
                self.assertLessEqual(len(script["beats"]["hook"].split()), 30)
                self.assertEqual(board_short.check_narration(script, cloud), [])
                for beat in case["keep"]:
                    self.assertIn(f"KEEP {beat} VERBATIM", repair)
                    self.assertEqual(script["beats"][beat], bad["beats"][beat])

    def test_buffer_refill_uses_the_short_narration_path(self):
        import buffer_shorts
        import run_pipeline
        captured = {}

        def fake_run(args, cwd=None, env=None):
            captured["args"] = list(args)
            return mock.Mock(returncode=0)

        with mock.patch("subprocess.run", fake_run):
            code = buffer_shorts.default_preview_runner("dark_cloud")
        self.assertEqual(code, 0)
        self.assertTrue(str(captured["args"][1]).endswith("run_pipeline.py"))
        self.assertEqual(captured["args"][-2:], ["--format", "short"])
        self.assertIn("board_short.py", [script for _name, script in run_pipeline.SHORT_STAGES])
        self.assertIn("n, source = narration(", inspect.getsource(board_short.main))


if __name__ == "__main__":
    unittest.main()
