"""Live Shorts stay on ElevenLabs. Config voice_id wins, credits stop, and
the six-beat script is checked before any TTS call.

Buffer and preview runs are not live: they stay on the four-beat Edge path.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import auto_agent
import board_scenes
import board_short
import eleven_voice
import quality_check
import scene_frames
import self_review
import short_layout

RUDRA = "ypnkIsDASPgHZuanrF0q"
KAMAL = "qsz5tTEjPvsiIJZIpM8S"
SECRET_VOICE = "secretVoiceFromEnv0001"
CFG = {"models": {"script_model": "deepseek-flash"}, "voice": {}}


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.headers = headers or {}
        if isinstance(body, (bytes, bytearray)):
            self.content = bytes(body)
            self.text = ""
            self._json = None
        else:
            self._json = body
            self.text = json.dumps(body)
            self.content = self.text.encode("utf-8")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _rudra_voice():
    return {
        "engine": "elevenlabs",
        "voice_id": RUDRA,
        "voice_name": "Rudra",
        "words_per_second": 2.8,
        "shared_voice_search": "Rudra",
        "library_new_name": "Rudra PKP",
        "fallback_voice_id": KAMAL,
        "fallback_voice_name": "Kamal",
        "fallback_words_per_second": 2.0,
        "model": "eleven_multilingual_v2",
        "stability": 0.5,
        "similarity_boost": 0.8,
        "style": 0.25,
        "speed": 0.9,
    }


HOOK_LINE = "क्या यह सेटअप गलत है?"


def _words(n, salt, lead=""):
    n = int(n)
    parts = str(lead).split() + str(salt).split()
    if len(parts) >= n:
        return " ".join(parts[:n])
    return " ".join(parts + ["नियम"] * (n - len(parts)))


def _live_topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "_live_short": True,
        "_voice": {"words_per_second": 2.8, "voice_id": RUDRA},
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना"],
    }


def _live_script(counts=None, salts=None):
    targets = board_short.live_beat_targets(2.8)
    counts = counts or targets
    salts = salts or {
        "hook": "हुक",
        "pehchan": "पहचान",
        "matlab": "मतलब",
        "udaharan": "उदाहरण",
        "galti": "गलती",
        "karein": "करें",
    }
    return {
        "title": "सेटअप का नियम समझिए",
        "hook_line": HOOK_LINE,
        "beats": {
            beat: _words(counts[beat], salts[beat], HOOK_LINE if beat == "hook" else "")
            for beat in board_short.LIVE_ORDER
        },
    }


class VoiceIdTests(unittest.TestCase):
    def tearDown(self):
        board_short._reset_voice_state()

    def test_config_voice_id_wins_over_the_secret(self):
        c = {"voice": _rudra_voice()}
        urls = []

        def post(url, **_kwargs):
            urls.append(url)
            return Resp(200, b"E" * 2500)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {
                 "ELEVENLABS_API_KEY": "test-key",
                 "ELEVENLABS_VOICE_ID": "7qBNUtXRGP0jPi0H4r8k",
             }), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch("requests.post", post):
            self.assertEqual(eleven_voice.configured_voice_id(c), RUDRA)
            path = Path(tmp) / "a.mp3"
            self.assertTrue(board_short._eleven("नमस्ते नियम", path, c))
        self.assertEqual(c["voice"]["voice_id"], RUDRA)
        self.assertIn(RUDRA, urls[0])
        self.assertNotIn("7qBNUtXRGP0jPi0H4r8k", urls[0])

    def test_empty_or_todo_config_uses_the_secret(self):
        for raw in ("", "  ", "TODO_new_voice"):
            c = {"voice": _rudra_voice()}
            c["voice"]["voice_id"] = raw
            urls = []

            def post(url, **_kwargs):
                urls.append(url)
                return Resp(200, b"E" * 2500)

            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.dict(os.environ, {
                     "ELEVENLABS_API_KEY": "test-key",
                     "ELEVENLABS_VOICE_ID": SECRET_VOICE,
                 }), \
                 mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
                 mock.patch("requests.post", post):
                self.assertEqual(eleven_voice.configured_voice_id(c), SECRET_VOICE)
                self.assertTrue(board_short._eleven("नमस्ते नियम", Path(tmp) / "a.mp3", c))
            self.assertIn(SECRET_VOICE, urls[0])
            self.assertNotIn(RUDRA, urls[0])


class EnsureVoiceTests(unittest.TestCase):
    def _run(self, get, post=None):
        urls = []

        def wrapped_get(url, **kwargs):
            urls.append(("GET", url))
            return get(url, **kwargs)

        def wrapped_post(url, **kwargs):
            urls.append(("POST", url, kwargs.get("json")))
            if post is None:
                raise AssertionError("ensure-voice must not POST")
            return post(url, **kwargs)

        voice = _rudra_voice()
        c = {"voice": voice}
        with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
             mock.patch("requests.get", wrapped_get), \
             mock.patch("requests.post", wrapped_post), \
             redirect_stdout(io.StringIO()):
            got = eleven_voice.ensure_library_voice(c)
        self.assertFalse(any("text-to-speech" in item[1] for item in urls))
        return got, voice, urls

    def test_voice_already_on_the_account_is_used(self):
        def get(_url, **_kwargs):
            return Resp(200, {"voice_id": RUDRA, "name": "Rudra"})

        got, voice, urls = self._run(get)
        self.assertEqual(got, RUDRA)
        self.assertEqual(voice["voice_id"], RUDRA)
        self.assertEqual([item[0] for item in urls], ["GET"])

    def test_missing_voice_is_added_from_the_shared_library(self):
        def get(url, **_kwargs):
            if "shared-voices" in url:
                return Resp(200, {"voices": [{
                    "voice_id": RUDRA,
                    "public_owner_id": "owner64hex",
                    "name": "Rudra",
                }]})
            return Resp(404, {"detail": "voice not found"})

        def post(_url, **_kwargs):
            return Resp(200, {"voice_id": "addedRudraPkP"})

        got, voice, urls = self._run(get, post)
        self.assertEqual(got, "addedRudraPkP")
        self.assertEqual(voice["voice_id"], "addedRudraPkP")
        posted = [item for item in urls if item[0] == "POST"]
        self.assertEqual(len(posted), 1)
        self.assertIn("/voices/add/owner64hex/" + RUDRA, posted[0][1])
        self.assertEqual(posted[0][2], {"new_name": "Rudra PKP"})

    def test_add_failure_falls_back_to_kamal(self):
        def get(url, **_kwargs):
            if "shared-voices" in url:
                return Resp(200, {"voices": []})
            return Resp(404, {"detail": "voice not found"})

        got, voice, urls = self._run(get)
        self.assertEqual(got, KAMAL)
        self.assertEqual(voice["voice_id"], KAMAL)
        self.assertEqual(voice["voice_name"], "Kamal")
        self.assertEqual(voice["words_per_second"], 2.0)
        self.assertFalse(any(item[0] == "POST" for item in urls))

    def test_non_404_keeps_the_configured_id(self):
        def get(_url, **_kwargs):
            return Resp(403, {"detail": "forbidden"})

        got, voice, urls = self._run(get)
        self.assertEqual(got, RUDRA)
        self.assertEqual(voice["voice_id"], RUDRA)
        self.assertEqual(len(urls), 1)


class CalibrationTests(unittest.TestCase):
    def test_targets_come_from_the_coefficient(self):
        targets = board_short.live_beat_targets(2.8)
        self.assertEqual(targets, {
            "hook": 26, "pehchan": 36, "matlab": 40,
            "udaharan": 48, "galti": 42, "karein": 39,
        })
        self.assertEqual(sum(targets.values()), 231)
        self.assertEqual(board_short.live_word_window(2.8), (210, 252))
        self.assertTrue(75.0 <= 231 / 2.8 <= 90.0)
        for beat, target in targets.items():
            lo, hi = board_short.beat_word_window(target)
            self.assertLessEqual(lo, target)
            self.assertGreaterEqual(hi, target)
            self.assertAlmostEqual((hi - lo) / target, 0.4, delta=0.08)

        kamal = board_short.live_beat_targets(2.0)
        self.assertEqual(sum(kamal.values()), 165)
        self.assertEqual(board_short.live_word_window(2.0), (150, 180))
        self.assertEqual(board_short.BEAT_WORD_TARGET, {
            "hook": 25, "pehchan": 42, "matlab": 50, "karein": 46,
        })
        self.assertEqual(board_short.TOTAL_WORDS, (150, 190))

    def test_live_gate_uses_the_derived_window_and_catches_a_duplicate(self):
        topic = _live_topic()
        ok = _live_script()
        fails = board_short.check_narration(ok, topic)
        self.assertFalse(any("words" in item for item in fails))
        copied = _live_script()
        copied["beats"]["galti"] = copied["beats"]["udaharan"]
        dupes = board_short.check_narration(copied, topic)
        self.assertTrue(any("duplicate sentence" in item for item in dupes))
        plain = dict(topic)
        plain.pop("_live_short")
        four = {
            "title": ok["title"],
            "hook_line": ok["hook_line"],
            "beats": {
                "hook": _words(25, "हुक"),
                "pehchan": _words(42, "पहचान"),
                "matlab": _words(50, "मतलब"),
                "karein": _words(46, "करें"),
            },
        }
        four["beats"]["karein"] = four["beats"]["matlab"]
        quiet = board_short.check_narration(four, plain)
        self.assertFalse(any("duplicate sentence" in item for item in quiet))

    def test_only_a_paid_elevenlabs_slot_is_a_live_lesson(self):
        voice = {"voice": {"engine": "elevenlabs"}}
        with mock.patch.dict(os.environ, {"PUBLISH_VISIBILITY": "public"}, clear=False):
            os.environ.pop("PIPELINE_MODE", None)
            os.environ.pop("DRY_RUN", None)
            self.assertTrue(board_short._live_lesson(voice))
        with mock.patch.dict(os.environ, {"PUBLISH_VISIBILITY": "unlisted", "PIPELINE_MODE": "preview"}):
            self.assertFalse(board_short._live_lesson(voice))
        with mock.patch.dict(os.environ, {"PUBLISH_VISIBILITY": "public", "PIPELINE_MODE": ""}):
            self.assertFalse(board_short._live_lesson({"voice": {"engine": "edge"}}))


class DryRenderTests(unittest.TestCase):
    def tearDown(self):
        board_short._reset_voice_state()

    def test_estimate_miss_repairs_once_before_tts(self):
        topic = _live_topic()
        bad = _live_script({beat: 8 for beat in board_short.LIVE_ORDER})
        good = _live_script()
        calls = {"n": 0}

        def fake_json(*_args, **_kwargs):
            calls["n"] += 1
            return good

        def post(*_args, **_kwargs):
            raise AssertionError("TTS must wait until the dry check passes")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "claude_json", fake_json), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch("requests.post", post), \
             redirect_stdout(io.StringIO()):
            out = board_short.fit_estimated_duration(bad, topic, CFG, {})
            self.assertFalse(board_short._dry_fails(out, topic, {}))
        self.assertEqual(calls["n"], 1)
        self.assertEqual(out["beats"]["udaharan"], good["beats"]["udaharan"])

    def test_real_miss_rerenders_only_the_changed_beat(self):
        topic = _live_topic()
        script = _live_script()
        spoken = dict(script["beats"])
        repaired = _live_script(salts={
            "hook": "हुकबदल",
            "pehchan": "पहचान",
            "matlab": "मतलब",
            "udaharan": "उदाहरण",
            "galti": "गलती",
            "karein": "करें",
        })
        seen = []
        trims = {"n": 0}

        def synthesize(_spoken, _c, beats=None):
            seen.append(("speak-all", tuple(beats)))
            return "elevenlabs"

        def trim(beats):
            trims["n"] += 1
            if trims["n"] == 1:
                return {beat: 16.0 for beat in beats}
            return {beat: 14.0 for beat in beats}

        def tts(_text, path, _c):
            seen.append(("beat", path.stem))
            path.write_bytes(b"e" * 2000)

        board_short.ELEVEN_REMAINING = 5000
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value=RUDRA), \
             mock.patch.object(board_short, "claude_json", return_value=repaired), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", tts), \
             redirect_stdout(io.StringIO()):
            _spoken, _narr, _audio, raw, _times, total = board_short.prepare_spoken(
                spoken, script, topic, {**CFG, "voice": _rudra_voice()}, {})
        self.assertEqual(seen, [("speak-all", board_short.LIVE_ORDER), ("beat", "tts_hook")])
        self.assertAlmostEqual(total, 94.1, places=3)
        self.assertTrue(70.0 <= total <= 95.0)
        self.assertEqual(raw["hook"], 14.0)
        self.assertEqual(raw["karein"], 16.0)

    def test_rerender_stops_below_the_credit_floor(self):
        board_short.ELEVEN_REMAINING = 1000

        def tts(*_args, **_kwargs):
            raise AssertionError("a re-render that drops below 1,000 characters must not call TTS")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "tts", tts), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                board_short._rerender_beats(
                    {"hook": "नियम " * 40}, {"voice": _rudra_voice()}, ["hook"])

    def test_unknown_remaining_still_rerenders_the_beat(self):
        board_short.ELEVEN_REMAINING = None
        seen = []

        def tts(_text, path, _c):
            seen.append(path.name)
            path.write_bytes(b"e" * 2000)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "tts", tts), \
             mock.patch.object(board_short, "_trim_rendered", return_value={"hook": 12.0}):
            raw = board_short._rerender_beats(
                {"hook": "नियम " * 10}, {"voice": _rudra_voice()}, ["hook"])
        self.assertEqual(seen, ["tts_hook.mp3"])
        self.assertEqual(raw["hook"], 12.0)


class SixBeatGateTests(unittest.TestCase):
    def test_new_beats_reuse_existing_scenes_and_stay_on_screen(self):
        self.assertEqual(
            scene_frames.kinds_for("udaharan", 3),
            scene_frames.kinds_for("matlab", 3),
        )
        self.assertEqual(
            scene_frames.kinds_for("galti", 3),
            scene_frames.kinds_for("karein", 3),
        )
        import board_kit
        image = board_kit.background()
        board_scenes.render_beat("hammer", "udaharan", image.copy())
        board_scenes.render_beat("hammer", "galti", image.copy())
        topic = _live_topic()
        times = {}
        cursor = 0.0
        for beat in short_layout.ORDER:
            times[beat] = (cursor, cursor + 12.0)
            cursor += 12.0
        events = short_layout.overlay_plan(topic, times, "क्या यह सेटअप गलत है?")
        self.assertTrue(short_layout.hook_covers_first_two_seconds(events))
        self.assertTrue(short_layout.beats_have_key_text(events))
        shown = {event["beat"] for event in events}
        self.assertIn("udaharan", shown)
        self.assertIn("galti", shown)

    def test_accuracy_counts_numbers_in_the_new_beats(self):
        topic = _live_topic()
        narration = {"hook_line": "क्या यह सेटअप गलत है?", "beats": {"udaharan": "उदाहरण में कीमत 99 है"}}
        score, notes = self_review.score_accuracy(narration, topic)
        self.assertEqual(score, 0)
        self.assertTrue(notes)

    def test_duration_gates_follow_the_six_beat_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "narration.json").write_text(json.dumps({
                "beats": {"udaharan": "एक उदाहरण", "galti": "एक गलती"},
            }), encoding="utf-8")
            with mock.patch.object(quality_check, "RUN", run):
                self.assertEqual(quality_check.short_duration_bounds(), (70.0, 95.0, "70-95s"))
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window(), (70.0, 95.0, "70-95s"))
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(quality_check, "RUN", run):
                self.assertEqual(quality_check.short_duration_bounds()[2], "45-62s")
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window()[2], "45-70s")


if __name__ == "__main__":
    unittest.main()
