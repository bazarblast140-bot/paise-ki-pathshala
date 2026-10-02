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
CFG = {"models": {"script_model": "deepseek-flash"}, "voice": {"engine": "elevenlabs", "words_per_second": 1.9}}


def _topic():
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = next(item for item in bank["topics"] if item["key"] == "intraday_risk_reward")
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
    """n words split into short sentences so a long beat can be trimmed from the end."""
    counters = [
        "एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ", "नौ", "दस",
        "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह",
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
        mark = counters[index] if index < len(counters) else "और"
        words = [salt, mark, "नियम", "कदम"][:need]
        parts.append(" ".join(words) + "।")
        count += need
        index += 1
    return " ".join(parts)


def _targets():
    return board_short.live_beat_targets(1.9)


def _script(hook=None, udaharan=None, karein=None):
    targets = _targets()
    if hook is None:
        hook = targets["hook"]
    if udaharan is None:
        udaharan = targets["udaharan"]
    if karein is None:
        karein = targets["karein"]
    return {
        "title": "सेटअप का हिसाब समझिए",
        "hook_line": HOOK,
        "beats": {
            "hook": _words(hook, "हुक", HOOK),
            "pehchan": _words(targets["pehchan"], "पहचान"),
            "matlab": _words(targets["matlab"], "मतलब"),
            "udaharan": _words(udaharan, "उदाहरण में चार्ज 1.5 और 300" if udaharan else ""),
            "galti": _words(targets["galti"], "गलती"),
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
        targets = _targets()
        pad = targets["udaharan"] - len(("एंट्री 200 एसएल 198 है।").split())
        labelled = {
            "title": "सेटअप का हिसाब समझिए", "hook_line": HOOK, "beats": {
                "hook": _words(targets["hook"], "हुक", HOOK),
                "pehchan": _words(targets["pehchan"], "पहचान"),
                "matlab": _words(targets["matlab"], "मतलब"),
                "udaharan": bare["udaharan"] + " " + " ".join(["नियम"] * pad),
                "galti": _words(targets["galti"], "गलती"),
                "karein": _words(targets["karein"], "करें"),
            },
        }
        fails = board_short.check_narration(labelled, topic)
        self.assertTrue(any("udaharan" in item and "उदाहरण" in item for item in fails))
        draft = json.loads(json.dumps(labelled))
        self.assertIn("udaharan", board_short.beats_to_rewrite(draft, topic))
        board_short.apply_live_draft(draft, topic)
        self.assertEqual(
            hook_check.unlabelled_example_beats(draft["beats"], ("udaharan", "galti")),
            [],
        )
        self.assertEqual(board_short.check_narration(draft, topic), [])

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

    def test_a_312_word_hook_44_script_is_trimmed_into_range(self):
        topic = _topic()
        example = hook_check.worked_example_sentence(topic)
        long = {
            "title": "सेटअप का हिसाब समझिए",
            "hook_line": HOOK,
            "beats": {
                "hook": _packed(44, "हुक", HOOK),
                "pehchan": _packed(50, "पहचान"),
                "matlab": _packed(55, "मतलब"),
                "udaharan": _packed(60, "उदाहरणवाक्य", example),
                "galti": _packed(48, "गलती"),
                "karein": _packed(55, "करें"),
            },
        }
        raw = " ".join(long["beats"].values())
        self.assertEqual(len(raw.split()), 312)
        self.assertEqual(len(long["beats"]["hook"].split()), 44)
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append({"system": system, "user": user})
            return long

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
        self.assertEqual(len(calls), 1)
        self.assertIn(example, calls[0]["system"])
        self.assertIn("udaharan_sentence", calls[0]["user"])
        self.assertIn("verbatim", calls[0]["system"])
        counts = {beat: len(script["beats"][beat].split()) for beat in board_short.LIVE_ORDER}
        total = sum(counts.values())
        self.assertGreaterEqual(total, board_short.LIVE_TOTAL_WORDS[0])
        self.assertLessEqual(total, board_short.LIVE_TOTAL_WORDS[1])
        hook_lo, hook_hi = board_short.beat_word_window(
            _targets()["hook"], board_short.LIVE_BEAT_TOLERANCE)
        self.assertLessEqual(counts["hook"], hook_hi)
        self.assertGreaterEqual(counts["hook"], hook_lo)
        self.assertIn("140-175", calls[0]["system"])
        self.assertIn("1.90", calls[0]["system"])
        self.assertNotIn("200-270", calls[0]["system"])
        self.assertTrue(script["beats"]["hook"].startswith(HOOK))
        self.assertIn(example.strip("।"), script["beats"]["udaharan"])
        self.assertTrue(script.get("_trim_notes"))
        self.assertEqual(board_short.check_narration(script, topic), [])
        topic["_spoken_beats"] = script["beats"]
        _head, sub = board_short.card_pair(topic, "udaharan")
        self.assertIn("उदाहरण", sub)
        for number in hook_check.extract_numbers(sub):
            self.assertIn(number, hook_check.extract_numbers(script["beats"]["udaharan"]))

    def test_an_invented_number_is_repaired_then_blocked(self):
        topic = _topic()
        bad = _script()
        bad["beats"]["udaharan"] = _words(_targets()["udaharan"], "उदाहरण में चार्ज 300")
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
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("REWRITE udaharan", calls[1])
        self.assertIn("Disallowed numbers", calls[1])
        self.assertIn("300", calls[1])
        self.assertIn("Allowed numbers", calls[1])
        self.assertTrue(block["blocked"])
        self.assertNotIn("1.5", block["reason"])
        self.assertIn("topic-card", block["reason"])

    def test_a_label_does_not_admit_an_invented_number(self):
        topic = _topic()
        labelled = {"udaharan": "मान लीजिए चार्ज 300 है।", "galti": "उदाहरण में चार्ज 1.5 है।"}
        self.assertEqual(
            hook_check.live_number_faults(labelled, topic, ("udaharan", "galti")),
            {"300"},
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

    def test_a_verified_multiplier_result_passes_and_a_wrong_result_does_not(self):
        topic = _topic()
        line = "उदाहरण: एंट्री 200, एसएल = 200 − 1.5×2 = 197।"
        self.assertEqual(hook_check.live_number_faults({"udaharan": line}, topic, ("udaharan",)), set())
        wrong = "उदाहरण: एंट्री 200, एसएल = 200 − 1.5×2 = 300।"
        self.assertEqual(
            hook_check.live_number_faults({"udaharan": wrong}, topic, ("udaharan",)),
            {"300"},
        )
        real = {"key": "titan_2010", "cat": "REAL DATA", "topic": "Titan", "_live_short": True,
                "beats": {}, "facts": ["close 100"], "thumb": []}
        self.assertEqual(
            hook_check.live_number_faults(
                {"udaharan": "उदाहरण 100 − 1.5×2 = 97।"}, real, ("udaharan",)),
            {"1.5", "2", "97"},
        )

    def test_missing_udaharan_label_is_inserted_before_the_gate(self):
        topic = _topic()
        script = _script()
        script["beats"]["udaharan"] = "एंट्री 200 है। रिस्क 2 है। " + " ".join(["नियम"] * 24)
        script["beats"]["galti"] = "रिस्क 4 बिना लेबल। " + " ".join(["गलती"] * 22)
        board_short.apply_live_draft(script, topic)
        self.assertTrue(script["beats"]["udaharan"].startswith("उदाहरण "))
        self.assertIn("उदाहरण रिस्क 2", script["beats"]["udaharan"])
        self.assertTrue(script["beats"]["galti"].startswith("उदाहरण "))
        self.assertEqual(
            hook_check.unlabelled_example_beats(script["beats"], ("udaharan", "galti")),
            [],
        )
        self.assertTrue(any("inserted उदाहरण" in note for note in script["_trim_notes"]))

    def test_trimmed_card_matches_the_spoken_line_for_the_sync_gate(self):
        import sync_gate
        topic = _topic()
        spoken = "उदाहरण एंट्री 200 एसएल 198 है।"
        topic["_spoken_beats"] = {"udaharan": spoken, "galti": "उदाहरण रिस्क 2 है।"}
        head, sub = board_short.card_pair(topic, "udaharan")
        self.assertEqual(head, "उदाहरण")
        self.assertIn("200", sub)
        self.assertIn("198", sub)
        self.assertNotIn("400", sub)
        self.assertNotIn("300", sub)
        words = []
        cursor = 0.0
        for word in spoken.split():
            words.append({"word": word, "start": cursor, "end": cursor + 0.3})
            cursor += 0.3
        end = max(1.0, cursor)
        report = sync_gate.check(
            words,
            [{"start": 0.0, "end": end, "beat": "udaharan", "kind": "ladder", "show": [sub]}],
            {"udaharan": spoken},
            {"udaharan": (0.0, end)},
            [],
        )
        self.assertTrue(report["ok"], report["reason"])
