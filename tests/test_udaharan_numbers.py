"""Live udaharan and galti may use only numbers computed from the topic.

Run 36976017756 blocked on topic 79 after two DeepSeek calls: the repair
fixed the long hook and karein, and left 1.5 and 300 in udaharan. A label
does not make an invented figure legal. The computed figures have to be
marked उदाहरण on the scene card and in the same spoken sentence.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import hook_check
import scene_frames
import short_layout

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
        allowed = hook_check.teaching_allowed_numbers(topic)
        for number in ("200", "198", "204", "2", "4", "100", "400"):
            self.assertIn(number, allowed)
        self.assertNotIn("1.5", allowed)
        self.assertNotIn("300", allowed)

    def test_code_computed_numbers_pass_when_the_sentence_says_udaharan(self):
        topic = _topic()
        spoken = {
            "udaharan": "उदाहरण में एंट्री 200 एसएल 198 लक्ष्य 204 है।",
            "galti": "उदाहरण में 100 शेयर पर रिस्क 200 और रिवॉर्ड 400 है।",
        }
        self.assertEqual(
            hook_check.live_number_faults(spoken, topic, ("udaharan", "galti")),
            set(),
        )
        self.assertEqual(
            hook_check.unlabelled_example_beats(spoken, ("udaharan", "galti")),
            [],
        )
        bare = {"udaharan": "एंट्री 200 एसएल 198 है।"}
        self.assertEqual(hook_check.live_number_faults(bare, topic, ("udaharan",)), set())
        self.assertEqual(hook_check.unlabelled_example_beats(bare, ("udaharan",)), ["udaharan"])
        fails = board_short.check_narration(
            {"title": "सेटअप का हिसाब समझिए", "hook_line": HOOK, "beats": {
                "hook": _words(26, "हुक", HOOK),
                "pehchan": _words(36, "पहचान"),
                "matlab": _words(40, "मतलब"),
                "udaharan": bare["udaharan"] + " " + " ".join(["नियम"] * 40),
                "galti": _words(42, "गलती"),
                "karein": _words(39, "करें"),
            }},
            topic,
        )
        self.assertTrue(any("udaharan" in item and "उदाहरण" in item for item in fails))
        self.assertIn("udaharan", board_short.beats_to_rewrite(
            {"title": "सेटअप का हिसाब समझिए", "hook_line": HOOK, "beats": {
                "hook": _words(26, "हुक", HOOK),
                "pehchan": _words(36, "पहचान"),
                "matlab": _words(40, "मतलब"),
                "udaharan": bare["udaharan"] + " " + " ".join(["नियम"] * 40),
                "galti": _words(42, "गलती"),
                "karein": _words(39, "करें"),
            }},
            topic,
        ))

    def test_scene_card_labels_the_computed_figures(self):
        topic = _topic()
        for beat in ("udaharan", "galti"):
            head, sub = board_short.card_pair(topic, beat)
            self.assertEqual(head, "उदाहरण")
            self.assertIn("उदाहरण", sub)
            self.assertIn("200", sub)
            self.assertIn("198", sub)
            self.assertIn("204", sub)
            self.assertNotIn("1.5", sub)
            self.assertNotIn("300", sub)
        times = {}
        cursor = 0.0
        for beat in short_layout.ORDER:
            times[beat] = (cursor, cursor + 12.0)
            cursor += 12.0
        events = short_layout.overlay_plan(topic, times, HOOK)
        for beat in ("udaharan", "galti"):
            shown = " ".join(event["text"] for event in events if event["beat"] == beat)
            self.assertIn("उदाहरण", shown)
            self.assertIn("200", shown)
        frames, _flags = scene_frames.frames_for(topic, "udaharan", 1, {"mode": "schematic"})
        blob = " ".join(layer.get("text") or "" for layer in frames[0].text_layers)
        self.assertIn("उदाहरण", blob)
        self.assertIn("200", blob)
        self.assertIn("198", blob)
        self.assertIn("204", blob)

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
        self.assertIn("A label does not make an invented number allowed", repair)
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

    def test_a_label_does_not_admit_an_invented_number(self):
        topic = _topic()
        labelled = {"udaharan": "मान लीजिए चार्ज 300 है।", "galti": "उदाहरण में चार्ज 1.5 है।"}
        self.assertEqual(
            hook_check.live_number_faults(labelled, topic, ("udaharan", "galti")),
            {"300", "1.5"},
        )
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        cloud = next(item for item in bank["topics"] if item["key"] == "dark_cloud")
        cloud = json.loads(json.dumps(cloud))
        cloud["_live_short"] = True
        self.assertEqual(
            hook_check.live_number_faults({"udaharan": "मान लीजिए भाव 100 है।"}, cloud, ("udaharan",)),
            {"100"},
        )
        self.assertEqual(
            hook_check.live_number_faults({"hook": "मान लीजिए भाव 100 है।"}, cloud, ("hook",)),
            {"100"},
        )
        self.assertEqual(
            hook_check.unsourced_numbers("उदाहरण के लिए भाव 100 पर देखिए।", cloud),
            set(),
        )
        real = {"key": "titan_2010", "cat": "REAL DATA", "topic": "Titan", "_live_short": True,
                "beats": {}, "facts": ["close 100"], "thumb": []}
        self.assertEqual(
            hook_check.live_number_faults(
                {"udaharan": "उदाहरण के लिए भाव 100।"}, real, ("udaharan",)),
            set(),
        )
        self.assertEqual(
            hook_check.live_number_faults(
                {"udaharan": "उदाहरण के लिए भाव 250।"}, real, ("udaharan",)),
            {"250"},
        )
        self.assertEqual(
            hook_check.live_number_faults({"hook": "भाव 250 था।"}, real, ("hook",)),
            {"250"},
        )
        self.assertEqual(
            hook_check.live_number_faults(
                {"udaharan": "उदाहरण भाव 2350।"},
                real,
                ("udaharan",),
                allowed=hook_check.allowed_numbers(real) | {"2350"},
            ),
            set(),
        )
        empty = {"key": "titan_2010", "cat": "REAL DATA", "topic": "Titan", "_live_short": True,
                 "beats": {}, "facts": [], "thumb": []}
        self.assertEqual(
            hook_check.live_number_faults(
                {"udaharan": "उदाहरण के लिए भाव 100।"}, empty, ("udaharan",)),
            {"100"},
        )
        self.assertTrue(board_short.number_format_notes(
            ["एंट्री 200"], spoken="मान लीजिए चार्ज 300", caption=""))
