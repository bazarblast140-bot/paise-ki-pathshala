"""The live udaharan beat may use only numbers the topic card already gives.

Run 36976017756 blocked on topic 79 after two DeepSeek calls: the repair
fixed the long hook and karein, and left 1.5 and 300 in udaharan. That beat
has to be the one rewritten, with the card's entry, stop, and target listed.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import hook_check

ROOT = Path(__file__).resolve().parents[1]
HOOK = "1:2 का setup फिर भी घाटा?"
CFG = {"models": {"script_model": "deepseek-flash"}, "voice": {"engine": "elevenlabs", "words_per_second": 2.8}}


def _topic():
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = next(item for item in bank["topics"] if item["key"] == "intraday_risk_reward")
    topic = json.loads(json.dumps(topic))
    topic["_live_short"] = True
    topic["_voice"] = {"words_per_second": 2.8, "voice_id": "ypnkIsDASPgHZuanrF0q"}
    return topic


def _words(n, salt, lead=""):
    parts = str(lead).split() + str(salt).split()
    if len(parts) >= n:
        return " ".join(parts[:n])
    return " ".join(parts + ["नियम"] * (n - len(parts)))


def _script(hook, udaharan, karein):
    return {
        "title": "सेटअप का हिसाब समझिए",
        "hook_line": HOOK,
        "beats": {
            "hook": _words(hook, "हुक", HOOK),
            "pehchan": _words(36, "पहचान"),
            "matlab": _words(40, "मतलब"),
            "udaharan": _words(udaharan, "उदाहरण में चार्ज 1.5 और 300" if udaharan else ""),
            "galti": _words(42, "गलती"),
            "karein": _words(karein, "करें"),
        },
    }


def _fixed_udaharan():
    return _words(48, "उदाहरण में एंट्री 200 एसएल 198 लक्ष्य 204")


class UdaharanNumberTests(unittest.TestCase):
    def test_card_numbers_are_the_allow_list(self):
        topic = _topic()
        allowed = hook_check.allowed_example_numbers(topic)
        for number in ("200", "198", "204", "2", "4", "100", "400"):
            self.assertIn(number, allowed)
        self.assertNotIn("1.5", allowed)
        self.assertNotIn("300", allowed)

    def test_one_point_five_and_300_fail_and_the_repair_rewrites_udaharan(self):
        topic = _topic()
        bad = _script(hook=35, udaharan=48, karein=52)
        fails = board_short.check_narration(bad, topic)
        self.assertTrue(any("1.5" in item and "300" in item for item in fails))
        rewrite = board_short.beats_to_rewrite(bad, topic)
        self.assertIn("udaharan", rewrite)
        self.assertNotIn("pehchan", rewrite)
        self.assertNotIn("matlab", rewrite)
        self.assertNotIn("galti", rewrite)

        good = _script(hook=26, udaharan=48, karein=39)
        good["beats"]["udaharan"] = _fixed_udaharan()
        good["beats"]["pehchan"] = "यह रखना है " + _words(33, "पहचानबदल")
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append({"system": system, "user": user})
            return bad if len(calls) == 1 else good

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(
                 board_short, "fallback_narration",
                 side_effect=AssertionError("template must not be built"),
             ):
            script, source = board_short.narration(topic, CFG)
            self.assertFalse((Path(tmp) / "narration_block.json").exists())
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 2)
        self.assertIn("allowed_numbers", calls[0]["user"])
        self.assertNotIn("1.5", calls[0]["system"])
        self.assertIn("200", calls[0]["system"])
        repair = calls[1]["user"]
        self.assertIn("REWRITE udaharan:", repair)
        self.assertIn("KEEP pehchan VERBATIM", repair)
        self.assertIn(bad["beats"]["pehchan"], repair)
        allowed_line = next(line for line in repair.splitlines() if line.startswith("Allowed numbers:"))
        self.assertIn("200", allowed_line)
        self.assertIn("198", allowed_line)
        self.assertIn("204", allowed_line)
        self.assertNotIn("1.5", allowed_line)
        self.assertNotIn("300", allowed_line)
        self.assertEqual(script["beats"]["pehchan"], bad["beats"]["pehchan"])
        self.assertNotIn("1.5", script["beats"]["udaharan"])
        self.assertNotIn("300", script["beats"]["udaharan"])
        self.assertIn("200", script["beats"]["udaharan"])
        self.assertEqual(board_short.check_narration(script, topic), [])

    def test_a_second_repair_is_not_spent_and_the_template_stays_unused(self):
        topic = _topic()
        bad = _script(hook=35, udaharan=48, karein=52)
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append(user)
            return bad

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(
                 board_short, "fallback_narration",
                 side_effect=AssertionError("template must not be built"),
             ):
            with self.assertRaises(SystemExit):
                board_short.narration(topic, CFG)
            block = json.loads((Path(tmp) / "narration_block.json").read_text(encoding="utf-8"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(block["blocked"])
        self.assertIn("1.5", block["reason"])
        self.assertIn("topic-card", block["reason"])

    def test_labelled_examples_stay_in_the_teaching_beats(self):
        topic = _topic()
        labelled = {"udaharan": "मान लीजिए चार्ज 300 है।"}
        self.assertEqual(
            hook_check.live_number_faults(labelled, topic, ("udaharan",)),
            {"300"},
        )
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        cloud = next(item for item in bank["topics"] if item["key"] == "dark_cloud")
        cloud = json.loads(json.dumps(cloud))
        cloud["_live_short"] = True
        self.assertEqual(
            hook_check.live_number_faults({"udaharan": "मान लीजिए भाव 100 है।"}, cloud, ("udaharan",)),
            set(),
        )
        self.assertEqual(
            hook_check.live_number_faults({"hook": "मान लीजिए भाव 100 है।"}, cloud, ("hook",)),
            {"100"},
        )
        real = {"key": "titan_2010", "cat": "REAL DATA", "topic": "Titan", "_live_short": True,
                "beats": {}, "facts": [], "thumb": []}
        self.assertEqual(
            hook_check.live_number_faults(
                {"udaharan": "उदाहरण के लिए भाव 100।"}, real, ("udaharan",)),
            {"100"},
        )
        self.assertTrue(board_short.number_format_notes(
            ["एंट्री 200"], spoken="मान लीजिए चार्ज 300", caption=""))
