"""The default live Short is four beats at Rudra's 1.9 words/s."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import board_short
import sync_gate

ROOT = Path(__file__).resolve().parents[1]
HOOK = "क्या यह सेटअप गलत है?"
CFG = {
    "models": {"script_model": "deepseek-flash"},
    "voice": {"engine": "elevenlabs", "words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
}


def _topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "_live_short": True,
        "_live_beats": "four",
        "_voice": {"words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना"],
    }


def _packed(n, salt, lead=""):
    counters = ["एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ"]
    parts = []
    count = 0
    lead = " ".join(str(lead).split())
    if lead:
        if lead[-1] not in "।?!":
            lead += "।"
        parts.append(lead)
        count = len(lead.split())
    index = 0
    while count < n:
        need = min(4, n - count)
        words = [salt, counters[index % len(counters)], "नियम", "कदम"][:need]
        parts.append(" ".join(words) + "।")
        count += need
        index += 1
    return " ".join(parts)


class FourLiveWindowTests(unittest.TestCase):
    def test_config_default_is_four_and_windows_match_rudra(self):
        data = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(data["live_beats"], "four")
        self.assertEqual(data["formats"]["short"]["live_beats"], "four")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("LIVE_BEATS", None)
            self.assertEqual(board_short.resolve_live_beats({}), "four")
            self.assertEqual(board_short.resolve_live_beats({"_live_beats": "six"}), "six")
        target, total = board_short.four_live_bounds(1.9)
        self.assertEqual(target, (95, 115))
        self.assertEqual(total, (88, 122))
        targets = board_short.four_live_targets(1.9)
        self.assertEqual(sum(targets.values()), 105)
        self.assertEqual(set(targets), {"hook", "pehchan", "matlab", "karein"})
        self.assertEqual(board_short.live_voice_bounds(_topic()), (45.0, 65.0))
        self.assertEqual(board_short.four_margin_words(1.9), 114)
        order, _targets, words, band = board_short._lesson(_topic())
        self.assertEqual(order, board_short.BEATS)
        self.assertEqual(words, (88, 122))
        self.assertEqual(band, (95, 115))

    def test_over_sixty_seconds_is_trimmed_before_tts(self):
        topic = _topic()
        counts = {"hook": 30, "pehchan": 40, "matlab": 48, "karein": 42}
        self.assertEqual(sum(counts.values()), 160)
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": HOOK,
            "beats": {
                beat: _packed(counts[beat], beat, HOOK if beat == "hook" else "")
                for beat in board_short.BEATS
            },
            "source": "llm",
        }
        _words, _wps, seconds = board_short._live_estimate(script, topic)
        self.assertGreater(seconds, 65.0)
        seen = []

        def synthesize(spoken, _c, beats=None):
            seen.append(sum(len(str(spoken[beat]).split()) for beat in beats))

        def trim(beats):
            return {beat: 14.0 for beat in beats}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "claude_json", side_effect=AssertionError("trim must not call the model")), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", side_effect=AssertionError("no re-render")):
            board_short.prepare_spoken(dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], 122)
        self.assertGreaterEqual(seen[0], 88)
        self.assertLessEqual(seen[0], board_short.four_margin_words(1.9))

    def test_estimate_still_outside_45_65_spends_no_tts(self):
        topic = _topic()
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": HOOK,
            "beats": {beat: _packed(8, beat, HOOK if beat == "hook" else "") for beat in board_short.BEATS},
            "source": "llm",
        }
        _words, _wps, seconds = board_short._live_estimate(script, topic)
        self.assertLess(seconds, 45.0)
        seen = []

        def synthesize(*_args, **_kwargs):
            seen.append("tts")

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "_one_beat_repair", return_value=None), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch("requests.post", side_effect=AssertionError("no ElevenLabs")), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit):
                board_short.prepare_spoken(dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(seen, [])
        self.assertIn("0 characters", err.getvalue())
        self.assertIn("outside 45-65", err.getvalue())


class CraftAndLeakTests(unittest.TestCase):
    def test_craft_samples_missing_frame_returns_three_values(self):
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 2.0, "frame": None},
            {"beat": "karein", "start": 2.0, "end": 4.0},
        ]
        samples, text_frames, notes = board_short._craft_samples(
            scenes, {}, {}, {}, "", "")
        self.assertEqual(samples, [])
        self.assertEqual(text_frames, [])
        self.assertIn("scene has no frame: hook", notes)
        self.assertIn("scene has no frame: karein", notes)

    def test_leak_and_filler_sentences_fail_the_live_gate(self):
        topic = _topic()
        order = board_short.BEATS
        leaks = [
            "उदाहरण: कार्ड पर दिया हिसाब बोलो।",
            "कार्ड पर लिखा है…",
            "अब इसे chart पर पहचानें।",
            "इन features का मतलब समझिए।",
            "अब आगे traders क्या check करते हैं.",
        ]
        for line in leaks:
            notes = board_short.narrator_leak_notes({"matlab": line}, order)
            self.assertTrue(notes, line)
            self.assertIn("matlab", notes[0])
            self.assertEqual(
                board_short.route_gate_failure(notes[0], order),
                ["matlab"],
            )
        clean = "निचली wick बॉडी से 2 गुना होती है।"
        self.assertEqual(board_short.narrator_leak_notes({"matlab": clean}, order), [])


class HindiNumberTests(unittest.TestCase):
    def test_scale_words_and_title_equivalents(self):
        self.assertIn("4000", sync_gate.tokens("चार हज़ार"))
        self.assertIn("200000", sync_gate.tokens("दो लाख"))
        self.assertIn("2.5", sync_gate.tokens("ढाई"))
        self.assertIn("1.5", sync_gate.tokens("डेढ़"))
        self.assertNotIn("रुपये", sync_gate.tokens("चार हजार रुपये"))
        self.assertNotIn("प्रतिशत", sync_gate.tokens("2 प्रतिशत"))
        beats = {
            "hook": "रोज़ का नुकसान सीमा के अंदर रखो",
            "pehchan": "पूँजी दो लाख पर नियम है",
            "matlab": "दिन का नुकसान चार हजार पर बंद होता है",
            "karein": "फिर ट्रेड बंद कर दो कदम नियम",
        }
        times = {
            "hook": (0.0, 4.0), "pehchan": (4.0, 8.0),
            "matlab": (8.0, 12.0), "karein": (12.0, 16.0),
        }
        spoken = {
            "hook": "रोज का नुकसान सीमा के अंदर रखो",
            "pehchan": "पूंजी दो लाख पर नियम है",
            "matlab": "दिन का नुकसान चार हजार पर बंद होता है",
            "karein": "फिर ट्रेड बंद कर दो कदम नियम",
        }
        words = []
        for beat, (start, end) in times.items():
            toks = spoken[beat].split()
            step = (end - start) / len(toks)
            for i, tok in enumerate(toks):
                words.append({
                    "word": tok,
                    "start": start + i * step,
                    "end": start + (i + 1) * step - 0.01,
                })
        scenes = [
            {"start": a, "end": b, "beat": beat, "kind": beat, "show": [beats[beat]]}
            for beat, (a, b) in times.items()
        ]
        scenes[1] = dict(scenes[1], show=["200000"])
        scenes[2] = dict(scenes[2], show=["4000"])
        report = sync_gate.check(
            words, scenes, beats, times, ["Daily", "Loss", "Limit", "बंद", "4000"])
        self.assertNotIn("title promise", report["reason"])
        self.assertNotIn("before it is said", report["reason"])
        missed = sync_gate.check(words, scenes, beats, times, ["SIP"])
        self.assertIn("SIP", missed["reason"])
        self.assertIn("title promise", missed["reason"])
