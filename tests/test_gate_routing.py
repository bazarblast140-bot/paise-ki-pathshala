"""Every hook/teaching failure is a rewrite, not an early stop.

A vague karein used to end the live loop after call 3 because the rewrite
list only looked at word windows and disallowed numbers. The 4th call is a
repair of the beat the message names. If that still fails, the run blocks.
There is no template sentence pasted onto karein or galti.
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

ROOT = Path(__file__).resolve().parents[1]
HOOK = "क्या यह सेटअप गलत है?"
CFG = {
    "models": {"script_model": "deepseek-flash"},
    "voice": {"engine": "elevenlabs", "words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
}
VAGUE = (
    "सोचिए ध्यान से और फिर देखिए क्या हो रहा है बाजार में आज भी वही बात है समझिए "
    "धीरे शांत रुख तरफ आगे पीछे ऊपर नीचे मन में जो चल रहा है उसे पकड़िए और लिखिए"
).split()


def _journal():
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = next(item for item in bank["topics"] if item["key"] == "risk_journal")
    topic = json.loads(json.dumps(topic))
    topic["_live_short"] = True
    topic["_live_beats"] = "six"
    topic["_voice"] = {"words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"}
    return topic


def _vague_words(n):
    if n > len(VAGUE):
        raise AssertionError(f"need {n} vague words, have {len(VAGUE)}")
    return " ".join(VAGUE[:n])


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


def _live_script(counts=None, vague=()):
    targets = _targets()
    counts = dict(counts or targets)
    salts = {
        "hook": "हुक", "pehchan": "पहचान", "matlab": "मतलब",
        "udaharan": "उदाहरण", "galti": "गलती", "karein": "करें",
    }
    beats = {}
    for beat in board_short.LIVE_ORDER:
        if beat in vague:
            beats[beat] = _vague_words(counts[beat])
        else:
            beats[beat] = _packed(counts[beat], salts[beat], HOOK if beat == "hook" else "")
    return {
        "title": "यह नियम है",
        "hook_line": HOOK,
        "beats": beats,
    }


def _windows(topic):
    _order, targets, _total, _band = board_short._lesson(topic)
    return {
        beat: board_short.beat_word_window(targets[beat], board_short.LIVE_BEAT_TOLERANCE)
        for beat in board_short.LIVE_ORDER
    }


class VagueRepairTests(unittest.TestCase):
    def test_vague_karein_after_call_three_gets_a_fourth_repair(self):
        topic = _journal()
        targets = _targets()
        counts = dict(targets)
        counts["karein"] = _windows(topic)["karein"][0]
        bad = _live_script(counts, vague={"karein"})
        # The vague beat has no "नियम", so the title promise is a word it does say.
        bad["title"] = "क्या यह है"
        fails = board_short.narration_gate_failures(bad, topic)
        self.assertEqual(fails, ["vague explanation (no number, level or step): karein"])
        self.assertEqual(board_short.beats_to_rewrite(bad, topic), ["karein"])
        self.assertFalse(scene_pace.segment_is_concrete(bad["beats"]["karein"]))
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append(user)
            return json.loads(json.dumps(bad))

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(
                 board_short, "fallback_narration",
                 side_effect=AssertionError("template must not be built"),
             ):
            with self.assertRaises(SystemExit):
                board_short.narration(topic, CFG)
            block = json.loads((Path(tmp) / "narration_block.json").read_text(encoding="utf-8"))
        self.assertEqual(len(calls), 4)
        self.assertNotIn("REWRITE karein", calls[0])
        for user in calls[1:]:
            self.assertIn("REWRITE karein", user)
            self.assertIn("vague explanation (no number, level or step): karein", user)
            self.assertIn("Exact failure:", user)
        self.assertIn("vague explanation (no number, level or step): karein", block["reason"])
        self.assertNotIn("अगले हफ़्ते से हर trade के बाद", block["reason"])
        self.assertFalse(hasattr(board_short, "anchor_vague_takeaway"))

    def test_vague_takeaway_is_not_patched_with_a_template_sentence(self):
        self.assertNotIn("anchor_vague_takeaway", inspect.getsource(board_short.narration))
        topic = _journal()
        windows = _windows(topic)
        counts = dict(_targets())
        counts["galti"] = windows["galti"][0]
        draft = _live_script(counts, vague={"galti"})
        before = draft["beats"]["galti"]
        self.assertEqual(
            scene_pace.vague_beats(draft["beats"], order=board_short.LIVE_ORDER),
            ["galti"],
        )
        self.assertNotIn("अगले हफ़्ते से हर trade के बाद", before)
        self.assertIn("galti", board_short.beats_to_rewrite(draft, topic))


class GateRoutingTests(unittest.TestCase):
    def test_every_failure_message_maps_to_a_beat_or_all(self):
        order = board_short.LIVE_ORDER
        numbered = {"beats": {"matlab": "यहाँ चार्ज 300 है।", "pehchan": "नियम है।"}}
        forbidden = {"beats": {"matlab": "please like this नियम।"}}
        lagte = {"beats": {"pehchan": "ये लगते हैं नियम।"}}
        cases = [
            ("beat 'karein' khaali", None, ["karein"]),
            ("100 words (130-185 chahiye, target 140-175)", None, ["all"]),
            ("50 words (75-140 for the calm ATR short)", None, ["all"]),
            ("duplicate sentence in hook and pehchan", None, ["hook", "pehchan"]),
            ("karein beat 8 words (18-34 chahiye)", None, ["karein"]),
            (
                "ye numbers topic me nahi hain: ['300'] — sirf diye gaye numbers use karo",
                numbered,
                ["matlab"],
            ),
            (
                "ye numbers topic me nahi hain: ['300'] — sirf diye gaye numbers use karo",
                None,
                ["all"],
            ),
            ("udaharan numbers must be labelled उदाहरण in the same sentence", None, ["udaharan"]),
            ("mana shabd: ['like']", forbidden, ["matlab"]),
            ("mana shabd: ['like']", None, ["all"]),
            ("narration Hindi (Devanagari) me nahi hai", None, ["all"]),
            ("title 8-90 characters", None, ["all"]),
            ("hook me jawab/number mat batao (['500']) — sirf sawal", None, ["hook"]),
            ("REAL DATA hook ka opening question 10 words se chhota rakho", None, ["hook"]),
            ("final value ₹100 sirf 'karein' me bolo", None, ["hook", "pehchan", "matlab"]),
            ("'लगते' galat hai — 'लगाते' likho", lagte, ["pehchan"]),
            ("'लगते' galat hai — 'लगाते' likho", None, ["all"]),
            ("hook line missing", None, ["hook"]),
            ("hook line 2 words (4-12 required)", None, ["hook"]),
            ("hook must be a question, a real topic number, or a contrarian line", None, ["hook"]),
            ("hook number is not in the topic facts: ['999']", None, ["hook"]),
            ("hook makes a clickbait return claim", None, ["hook"]),
            ("hook beat must open with the punchy hook line", None, ["hook"]),
            ("vague explanation (no number, level or step): karein", None, ["karein"]),
            ("karein narrator leak: कार्ड पर दिया हिसाब बोलो", None, ["karein"]),
            ("vague explanation (no number, level or step): galti, karein", None, ["galti", "karein"]),
            ("self-review below bar: overall 40", None, ["all"]),
            ("estimated 100.0s (200 words / 1.90 w/s) outside 70-95", None, ["all"]),
            ("sync drift on karein", None, ["karein"]),
            ("sync check failed for the whole script", None, ["all"]),
        ]
        for message, narration, expected in cases:
            with self.subTest(message=message, expected=expected):
                self.assertEqual(
                    board_short.route_gate_failure(message, order, narration),
                    expected,
                )
        fragments = [
            "khaali",
            "for the calm ATR short",
            "chahiye, target",
            "chahiye)",
            "duplicate sentence",
            "ye numbers topic me nahi",
            "labelled उदाहरण",
            "mana shabd",
            "Devanagari",
            "title 8-90",
            "sirf sawal",
            "10 words se chhota",
            "me bolo",
            "लगाते",
            "punchy hook line",
            "hook line missing",
            "required)",
            "contrarian line",
            "hook number is not in the topic facts",
            "clickbait return claim",
            "vague explanation",
            "narrator leak",
            "self-review below bar",
            "estimated",
        ]
        blobs = "\n".join(
            inspect.getsource(fn)
            for fn in (
                board_short.check_narration,
                board_short.duplicate_sentence_notes,
                hook_check.check_hook_line,
                board_short.narration_gate_failures,
                board_short.narration,
                board_short._estimate_note,
                board_short._dry_fails,
                board_short.narrator_leak_notes,
            )
        )
        joined = "\n".join(message for message, _narration, _expected in cases)
        for fragment in fragments:
            self.assertIn(fragment, blobs, fragment)
            self.assertIn(fragment, joined, fragment)

        def statements(source):
            lines = source.splitlines()
            index = 0
            while index < len(lines):
                line = lines[index]
                if "fails.append" not in line and "hook line missing" not in line:
                    index += 1
                    continue
                buf = line
                depth = line.count("(") - line.count(")")
                index += 1
                while depth > 0 and index < len(lines):
                    buf += "\n" + lines[index]
                    depth += lines[index].count("(") - lines[index].count(")")
                    index += 1
                yield buf

        skipped = ("fails.append(note)", "fails.append(piece)", "fails.append(str(extra))")
        for fn in (
            board_short.check_narration,
            hook_check.check_hook_line,
            board_short.narration_gate_failures,
            board_short.narration,
            board_short._estimate_note,
            board_short._dry_fails,
            board_short.duplicate_sentence_notes,
        ):
            for statement in statements(inspect.getsource(fn)):
                if any(marker in statement for marker in skipped):
                    continue
                self.assertTrue(
                    any(fragment in statement for fragment in fragments),
                    statement.strip(),
                )


class MarginRecheckTests(unittest.TestCase):
    def _long_script(self):
        """176 words, over 90s. The hook cannot lose its only extra sentence."""
        topic = _journal()
        windows = _windows(topic)
        hook_tail = "पहचान नियम कदम रेखा औसत पॉइंट गुना मात्रा बॉडी और फिर।"
        self.assertEqual(len(hook_tail.split()), 11)
        hook = f"{HOOK} {hook_tail}"
        self.assertEqual(len(hook.split()), 16)
        self.assertLess(len(HOOK.split()), windows["hook"][0])
        counts = {
            "hook": 16,
            "pehchan": 30,
            "matlab": 32,
            "udaharan": 36,
            "galti": 34,
            "karein": 28,
        }
        self.assertEqual(sum(counts.values()), 176)
        salts = {
            "pehchan": "पहचान", "matlab": "मतलब", "udaharan": "उदाहरण",
            "galti": "गलती", "karein": "करें",
        }
        beats = {"hook": hook}
        for beat, count in counts.items():
            if beat == "hook":
                continue
            tail = 8
            beats[beat] = (
                _packed(count - tail, salts[beat]).rstrip("।")
                + "। "
                + _packed(tail, salts[beat] + "अंत")
            )
            self.assertEqual(len(beats[beat].split()), count)
        script = {
            "title": "यह नियम है",
            "hook_line": HOOK,
            "beats": beats,
            "source": "llm",
        }
        return topic, script

    def test_margin_trim_skips_a_beat_that_would_fall_under_its_minimum(self):
        topic, script = self._long_script()
        words, _wps, seconds = board_short._live_estimate(script, topic)
        self.assertEqual(words, 176)
        self.assertGreater(seconds, 90.0)
        self.assertLess(seconds, 95.0)
        self.assertEqual(board_short.check_narration(script, topic), [])
        self.assertEqual(board_short.beats_below_minimum(script, topic), [])
        board_short.tighten_duration_margin(script, topic)
        windows = _windows(topic)
        total = 0
        for beat in board_short.LIVE_ORDER:
            count = len(script["beats"][beat].split())
            total += count
            lo, hi = windows[beat]
            self.assertGreaterEqual(count, lo, beat)
            self.assertLessEqual(count, hi, beat)
        self.assertLessEqual(total, 170)
        self.assertEqual(len(script["beats"]["hook"].split()), 16)
        self.assertTrue(script["beats"]["hook"].startswith(HOOK))
        self.assertEqual(board_short.beats_below_minimum(script, topic), [])

    def test_locked_beat_is_not_the_one_trimmed(self):
        topic = _journal()
        windows = _windows(topic)
        counts = {
            "hook": 16,
            "pehchan": 31,
            "matlab": 30,
            "udaharan": 34,
            "galti": 30,
            "karein": 31,
        }
        self.assertEqual(sum(counts.values()), 172)
        salts = {
            "hook": "हुक", "pehchan": "पहचान", "matlab": "मतलब",
            "udaharan": "उदाहरण", "galti": "गलती", "karein": "करें",
        }
        beats = {}
        for beat, count in counts.items():
            lead = HOOK if beat == "hook" else ""
            tail = 4
            if beat == "hook":
                beats[beat] = _packed(count, salts[beat], lead)
            else:
                beats[beat] = (
                    _packed(count - tail, salts[beat]).rstrip("।")
                    + "। "
                    + _packed(tail, salts[beat] + "अंत")
                )
            self.assertEqual(len(beats[beat].split()), count)
            lo, hi = windows[beat]
            self.assertTrue(lo <= count <= hi)
        script = {
            "title": "यह नियम है",
            "hook_line": HOOK,
            "beats": beats,
        }
        pehchan = script["beats"]["pehchan"]
        board_short.apply_live_draft(script, topic, total_hi=170, locked=("pehchan",))
        self.assertEqual(script["beats"]["pehchan"], pehchan)
        total = sum(len(script["beats"][beat].split()) for beat in board_short.LIVE_ORDER)
        self.assertLessEqual(total, 170)
        self.assertEqual(board_short.beats_below_minimum(script, topic), [])

    def test_real_build_rechecks_windows_after_the_margin_trim(self):
        import scene_frames
        if not Path(scene_frames._FONT).is_file():
            self.skipTest("Noto Devanagari font is not installed")
        topic, script = self._long_script()
        seen = []

        def synthesize(spoken, _c, beats=None):
            seen.append(spoken)

        def trim(beats):
            return {beat: 14.0 for beat in beats}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "claude_json", side_effect=AssertionError("margin trim must not call the model")), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", side_effect=AssertionError("no re-render")):
            board_short.prepare_spoken(dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(len(seen), 1)
        spoken = seen[0]
        windows = _windows(topic)
        total = 0
        for beat in board_short.LIVE_ORDER:
            count = len(str(spoken[beat]).split())
            total += count
            lo, _hi = windows[beat]
            self.assertGreaterEqual(count, lo, beat)
        self.assertLessEqual(total, 170)
        self.assertEqual(len(spoken["hook"].split()), 16)
        self.assertIn("beats_below_minimum", inspect.getsource(board_short.fit_estimated_duration))


if __name__ == "__main__":
    unittest.main()
