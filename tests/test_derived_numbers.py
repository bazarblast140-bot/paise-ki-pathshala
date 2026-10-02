"""Derived numbers may pass the live gate. A bare invented figure may not.

risk_journal was blocked on 60, the loss rate of a 40% win rate, after one
DeepSeek call. 100−p is allowed only when the same sentence writes the
subtraction. A written sum, difference, product, or ratio of two topic numbers
is allowed when that same sentence evaluates correctly. 300 with no such
arithmetic still fails, and that failure is repaired inside the 4-call budget
before the run blocks.

A live estimate over 90s is trimmed toward 170 words before any TTS call.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import hook_check

ROOT = Path(__file__).resolve().parents[1]
HOOK = "क्या यह सेटअप गलत है?"
CFG = {
    "models": {"script_model": "deepseek-flash"},
    "voice": {"engine": "elevenlabs", "words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
}


def _journal():
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = next(item for item in bank["topics"] if item["key"] == "risk_journal")
    topic = json.loads(json.dumps(topic))
    topic["_live_short"] = True
    topic["_live_beats"] = "six"
    topic["_voice"] = {"words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"}
    return topic


def _words(n, salt, lead=""):
    parts = str(lead).split() + str(salt).split()
    if len(parts) >= n:
        return " ".join(parts[:n])
    return " ".join(parts + ["नियम"] * (n - len(parts)))


def _packed(n, salt, lead=""):
    counters = [
        "एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ", "नौ", "दस",
        "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह",
    ]
    lead = " ".join(str(lead).split())
    parts = []
    count = len(lead.split()) if lead else 0
    if lead:
        if lead[-1] not in "।?!":
            lead += "।"
        parts.append(lead)
    index = 0
    while count < n:
        need = min(4, n - count)
        mark = counters[index % len(counters)]
        words = [salt, mark, "नियम", "कदम"][:need]
        parts.append(" ".join(words) + "।")
        count += need
        index += 1
    return " ".join(parts)


def _targets():
    return board_short.live_beat_targets(1.9)


def _live_script(counts=None):
    targets = _targets()
    counts = counts or targets
    salts = {
        "hook": "हुक", "pehchan": "पहचान", "matlab": "मतलब",
        "udaharan": "उदाहरण", "galti": "गलती", "karein": "करें",
    }
    return {
        "title": "सेटअप का नियम समझिए",
        "hook_line": HOOK,
        "beats": {
            beat: _words(counts[beat], salts[beat], HOOK if beat == "hook" else "")
            for beat in board_short.LIVE_ORDER
        },
    }


class DerivedNumberTests(unittest.TestCase):
    def test_risk_journal_loss_rate_is_the_complement_of_forty(self):
        topic = _journal()
        self.assertIn("60", hook_check.percentage_complements(topic))
        self.assertNotIn("60", hook_check.allowed_numbers(topic))
        bare = {
            "matlab": "हार की दर 60 प्रतिशत रहती है और जीत 40 है।",
            "udaharan": "उदाहरण में हार 60 और जीत 40 है।",
        }
        self.assertIn(
            "60",
            hook_check.live_number_faults(bare, topic, ("matlab", "udaharan")),
        )
        spelled = {
            "matlab": "100 − 40 = 60 तो हार की दर 60 प्रतिशत है।",
            "udaharan": "उदाहरण: 100 में से 40 निकलें तो हार 60 है।",
        }
        self.assertEqual(
            hook_check.live_number_faults(spelled, topic, ("matlab", "udaharan")),
            set(),
        )

    def test_three_hundred_without_arithmetic_is_blocked(self):
        topic = _journal()
        bare = {"matlab": "यहाँ चार्ज 300 है।", "udaharan": "उदाहरण में चार्ज 300 है।"}
        self.assertEqual(
            hook_check.live_number_faults(bare, topic, ("matlab", "udaharan")),
            {"300"},
        )
        wrong = {"matlab": "40 × 2 = 300।"}
        self.assertEqual(hook_check.live_number_faults(wrong, topic, ("matlab",)), {"300"})
        split = {"matlab": "40 × 2 है। नतीजा 80।"}
        self.assertIn("80", hook_check.live_number_faults(split, topic, ("matlab",)))

    def test_written_arithmetic_of_two_topic_numbers_is_allowed(self):
        topic = _journal()
        lines = {
            "matlab": "हिसाब 40 + 2 = 42 और 2 − 1 = 1 और 40 × 2 = 80 और 40 / 2 = 20।",
        }
        self.assertEqual(hook_check.live_number_faults(lines, topic, ("matlab",)), set())

    def test_number_gate_failure_repairs_before_it_blocks(self):
        topic = _journal()
        targets = _targets()
        bad = _live_script()
        spare = "चार्ज 300 है।"
        base = _packed(targets["udaharan"], "उदाहरण")
        bad["beats"]["udaharan"] = base + " " + spare
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append(user)
            return bad

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(
                 board_short, "fallback_narration",
                 side_effect=AssertionError("template must not be built"),
             ):
            script, source = board_short.narration(topic, CFG)
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 2)
        self.assertIn("REWRITE udaharan", calls[1])
        self.assertIn("Disallowed numbers", calls[1])
        self.assertIn("'300'", calls[1])
        self.assertIn("Allowed numbers", calls[1])
        self.assertNotIn("300", script["beats"]["udaharan"])
        self.assertTrue(script["beats"]["hook"].startswith(HOOK))
        self.assertEqual(board_short.check_narration(script, topic), [])

    def test_over_ninety_seconds_is_trimmed_to_170_before_tts(self):
        topic = _journal()
        counts = {
            "hook": 22, "pehchan": 28, "matlab": 31,
            "udaharan": 37, "galti": 33, "karein": 26,
        }
        self.assertEqual(sum(counts.values()), 177)
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": HOOK,
            "beats": {
                beat: _packed(counts[beat], {
                    "hook": "हुक", "pehchan": "पहचान", "matlab": "मतलब",
                    "udaharan": "उदाहरण", "galti": "गलती", "karein": "करें",
                }[beat], HOOK if beat == "hook" else "")
                for beat in board_short.LIVE_ORDER
            },
            "source": "llm",
        }
        words, wps, seconds = board_short._live_estimate(script, topic)
        self.assertEqual(words, 177)
        self.assertEqual(wps, 1.9)
        self.assertGreater(seconds, 90.0)
        self.assertLess(seconds, 95.0)
        self.assertEqual(board_short.check_narration(script, topic), [])
        seen = []

        def synthesize(spoken, _c, beats=None):
            seen.append(spoken)

        def trim(beats):
            return {beat: 14.0 for beat in beats}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "claude_json", side_effect=AssertionError("margin trim must not call the model")), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", side_effect=AssertionError("no re-render")):
            board_short.prepare_spoken(
                dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(len(seen), 1)
        spoken = seen[0]
        total = sum(len(str(spoken[beat]).split()) for beat in board_short.LIVE_ORDER)
        self.assertLessEqual(total, 170)
        self.assertGreater(total, 130)
        self.assertTrue(spoken["hook"].startswith(HOOK))
        prompt = board_short.live_narration_system(topic)
        self.assertIn("140-175", prompt)
        self.assertIn("155", prompt)
