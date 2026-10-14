"""Live Shorts stay on ElevenLabs. Config voice_id wins, credits stop, and
the six-beat script is checked before any TTS call.

Buffer and preview runs are not live: they stay on the four-beat Edge path.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        "words_per_second": 1.9,
        "shared_voice_search": "Rudra",
        "library_new_name": "Rudra PKP",
        "fallback_voice_id": KAMAL,
        "fallback_voice_name": "Kamal",
        "fallback_words_per_second": 1.9,
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


def _packed(n, salt, lead=""):
    """n words in short sentences so a long beat can be trimmed from the end."""
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
        mark = counters[index % len(counters)]
        words = [salt, mark, "नियम", "कदम"][:need]
        parts.append(" ".join(words) + "।")
        count += need
        index += 1
    return " ".join(parts)


def _live_topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "_live_short": True,
        "_live_beats": "six",
        "_voice": {"words_per_second": 1.9, "voice_id": RUDRA},
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
    targets = board_short.live_beat_targets(1.9)
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
        "title": "यह नियम है",
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
                 "PAID_TTS_ENABLED": "true",
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
                     "PAID_TTS_ENABLED": "true",
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
        self.assertEqual(voice["words_per_second"], 1.9)
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
        targets = board_short.live_beat_targets(1.9)
        self.assertEqual(targets, {
            "hook": 18, "pehchan": 24, "matlab": 27,
            "udaharan": 33, "galti": 29, "karein": 26,
        })
        self.assertEqual(sum(targets.values()), 157)
        self.assertEqual(board_short.LIVE_TOTAL_WORDS, (130, 185))
        self.assertEqual(board_short.LIVE_WORD_TARGET, (140, 175))
        self.assertEqual(board_short.live_word_window(1.9), (143, 171))
        self.assertTrue(75.0 <= 157 / 1.9 <= 90.0)
        for beat, target in targets.items():
            lo, hi = board_short.beat_word_window(target, board_short.LIVE_BEAT_TOLERANCE)
            self.assertLessEqual(lo, target)
            self.assertGreaterEqual(hi, target)
            self.assertAlmostEqual((hi - lo) / target, 0.6, delta=0.15)

        faster = board_short.live_beat_targets(2.0)
        self.assertEqual(sum(faster.values()), 165)
        self.assertEqual(board_short.live_word_window(2.0), (150, 180))
        self.assertEqual(board_short.voice_wps({"voice": {"voice_id": KAMAL}}), 1.9)
        self.assertEqual(board_short.voice_wps({"voice": {}}), 1.9)
        self.assertEqual(board_short.BEAT_WORD_TARGET, {
            "hook": 22, "pehchan": 29, "matlab": 36, "karein": 32,
        })
        self.assertEqual(board_short.TOTAL_WORDS, (100, 138))
        prompt = board_short.live_narration_system(_live_topic())
        self.assertIn("140-175", prompt)
        self.assertIn("130-185", prompt)
        self.assertIn("1.90 words/second", prompt)
        self.assertNotIn("200-270", prompt)
        self.assertNotIn("2.80", prompt)

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
    def setUp(self):
        self._frames = mock.patch.object(board_short, "require_scene_frames")
        self._frames.start()

    def tearDown(self):
        self._frames.stop()
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

    def test_255_words_on_rudra_is_trimmed_before_tts(self):
        topic = _live_topic()
        # 255 words estimate about 134s. The margin trim runs before the
        # duration guard and aims at 170 words, which is inside 70-95s.
        counts = {
            "hook": 40, "pehchan": 42, "matlab": 44,
            "udaharan": 48, "galti": 42, "karein": 39,
        }
        script = _live_script()
        for beat, count in counts.items():
            lead = HOOK_LINE if beat == "hook" else ""
            script["beats"][beat] = _packed(count, "शब्द", lead)
        words, wps, seconds = board_short._live_estimate(script, topic)
        self.assertEqual(words, 255)
        self.assertEqual(wps, 1.9)
        self.assertAlmostEqual(seconds, 134.2, places=1)
        self.assertGreater(seconds, 95.0)
        seen = []

        def synthesize(spoken, _c, beats=None):
            seen.append(sum(len(str(spoken[beat]).split()) for beat in beats))

        def post(*_args, **_kwargs):
            raise AssertionError("ElevenLabs must not be called")

        def trim(beats):
            return {beat: 14.0 for beat in beats}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value=RUDRA), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", side_effect=AssertionError("no re-render")), \
             mock.patch("requests.post", post), \
             redirect_stdout(io.StringIO()):
            _spoken, _narr, _audio, _raw, _times, total = board_short.prepare_spoken(
                dict(script["beats"]), script, topic, {**CFG, "voice": _rudra_voice()}, {})
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], 170)
        self.assertGreater(seen[0], 130)
        self.assertTrue(70.0 <= total <= 95.0)

    def test_about_160_words_allows_one_full_render(self):
        topic = _live_topic()
        script = _live_script()
        script["beats"]["karein"] += " कदम नियम वाक्य"
        words, _wps, seconds = board_short._live_estimate(script, topic)
        self.assertEqual(words, 160)
        self.assertTrue(70.0 <= seconds <= 95.0)
        seen = []

        def synthesize(_spoken, _c, beats=None):
            seen.append(("speak-all", tuple(beats)))
            return "elevenlabs"

        def tts(*_args, **_kwargs):
            raise AssertionError("an in-range estimate must not re-render beats")

        def trim(beats):
            return {beat: 14.0 for beat in beats}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value=RUDRA), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", tts), \
             redirect_stdout(io.StringIO()):
            _spoken, _narr, _audio, _raw, _times, total = board_short.prepare_spoken(
                dict(script["beats"]), script, topic, {**CFG, "voice": _rudra_voice()}, {})
        self.assertEqual(seen, [("speak-all", board_short.LIVE_ORDER)])
        self.assertTrue(70.0 <= total <= 95.0)

    def test_duration_trim_passes_below_130_words_when_audio_is_in_range(self):
        topic = _live_topic()
        targets = board_short.live_beat_targets(1.9)
        script = _live_script()
        salts = {
            "hook": "हुक", "pehchan": "पहचान", "matlab": "मतलब",
            "udaharan": "उदाहरण", "galti": "गलती", "karein": "करें",
        }
        for beat, target in targets.items():
            lead = HOOK_LINE if beat == "hook" else ""
            script["beats"][beat] = _packed(target, salts[beat], lead)
        spoken = dict(script["beats"])
        words_before = sum(len(spoken[beat].split()) for beat in board_short.LIVE_ORDER)
        self.assertGreaterEqual(words_before, 140)
        self.assertLessEqual(words_before, 175)
        seen = []
        trims = {"n": 0}

        def synthesize(_spoken, _c, beats=None):
            seen.append(("speak-all", tuple(beats)))
            return "elevenlabs"

        def trim(beats):
            trims["n"] += 1
            if trims["n"] == 1:
                return {beat: 20.0 for beat in beats}
            seen.append(("beats", tuple(beats)))
            return {beat: 13.0 for beat in beats}

        def tts(_text, path, _c):
            path.write_bytes(b"e" * 2000)

        board_short.ELEVEN_REMAINING = 5000
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value=RUDRA), \
             mock.patch.object(board_short, "claude_json", side_effect=AssertionError("duration trim must not call the model")), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", tts), \
             redirect_stdout(io.StringIO()):
            spoken_out, narr, _audio, _raw, _times, total = board_short.prepare_spoken(
                spoken, script, topic, {**CFG, "voice": _rudra_voice()}, {})
        words_after = sum(len(str(spoken_out[beat]).split()) for beat in board_short.LIVE_ORDER)
        self.assertLess(words_after, 130)
        self.assertTrue(board_short._dry_fails(narr, topic, {}))
        self.assertEqual(seen[0], ("speak-all", board_short.LIVE_ORDER))
        self.assertEqual(len([item for item in seen if item[0] == "speak-all"]), 1)
        self.assertGreaterEqual(len(seen[1][1]), 1)
        self.assertTrue(70.0 <= total <= 95.0)

    def test_real_miss_rerenders_only_the_changed_beat(self):
        topic = _live_topic()
        script = _live_script()
        # Extra sentences keep the beats inside ±30%. A long render trims them
        # in code and re-speaks only those beats. The model is not called.
        script["beats"]["pehchan"] += "। अतिरिक्त पंक्ति हटाओ।"
        script["beats"]["matlab"] += "। दूसरी पंक्ति हटाओ।"
        spoken = dict(script["beats"])
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
             mock.patch.object(board_short, "claude_json", side_effect=AssertionError("long audio must be trimmed, not rewritten")), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", tts), \
             redirect_stdout(io.StringIO()):
            _spoken, _narr, _audio, raw, _times, total = board_short.prepare_spoken(
                spoken, script, topic, {**CFG, "voice": _rudra_voice()}, {})
        self.assertEqual(seen[0], ("speak-all", board_short.LIVE_ORDER))
        self.assertEqual(sorted(item[1] for item in seen[1:]), ["tts_matlab", "tts_pehchan"])
        self.assertAlmostEqual(total, 92.1, places=3)
        self.assertTrue(70.0 <= total <= 95.0)
        self.assertEqual(raw["hook"], 16.0)
        self.assertEqual(raw["pehchan"], 14.0)
        self.assertEqual(raw["matlab"], 14.0)
        self.assertEqual(raw["karein"], 16.0)

    def test_rerender_below_the_credit_floor_uses_edge(self):
        board_short.ELEVEN_REMAINING = 1000
        board_short.ELEVEN_EXHAUSTED = False
        board_short.VOICE_FALLBACK_REASON = ""
        seen = []

        def tts(_text, path, _c):
            seen.append(board_short.ELEVEN_EXHAUSTED)
            path.write_bytes(b"e" * 2000)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "tts", tts), \
             mock.patch.object(board_short, "_trim_rendered", return_value={"hook": 12.0}), \
             mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(Path(tmp) / "summary.txt")}, clear=False), \
             redirect_stdout(io.StringIO()):
            raw = board_short._rerender_beats(
                {"hook": "नियम " * 40}, {"voice": _rudra_voice()}, ["hook"])
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(seen, [True])
        self.assertIn("1000", board_short.VOICE_FALLBACK_REASON)
        self.assertEqual(meta["engine"], "edge")
        self.assertTrue(meta.get("preview"))
        self.assertIs(meta.get("upload"), False)
        self.assertEqual(raw["hook"], 12.0)

    def test_rerender_quota_retries_that_beat_on_edge(self):
        board_short.ELEVEN_REMAINING = 5000
        board_short.ELEVEN_EXHAUSTED = False
        board_short.VOICE_FALLBACK_REASON = ""
        calls = {"n": 0}

        def tts(_text, path, _c):
            calls["n"] += 1
            if calls["n"] == 1:
                raise eleven_voice.QuotaFallback("HTTP 402; code=insufficient_credits")
            self.assertTrue(board_short.ELEVEN_EXHAUSTED)
            path.write_bytes(b"e" * 2000)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "tts", tts), \
             mock.patch.object(board_short, "_trim_rendered", return_value={"hook": 12.0}), \
             redirect_stdout(io.StringIO()):
            raw = board_short._rerender_beats(
                {"hook": "नियम " * 10}, {"voice": _rudra_voice()}, ["hook"])
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(calls["n"], 2)
        self.assertEqual(meta["engine"], "edge")
        self.assertTrue(meta.get("preview"))
        self.assertIn("insufficient_credits", meta["fallback_reason"])
        self.assertEqual(raw["hook"], 12.0)

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
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "narration.json").write_text(json.dumps({
                "live_beats": "four",
                "beats": {"hook": "सवाल", "karein": "कदम"},
            }), encoding="utf-8")
            with mock.patch.object(quality_check, "RUN", run):
                self.assertEqual(quality_check.short_duration_bounds(), (45.0, 65.0, "45-65s"))
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window(), (45.0, 65.0, "45-65s"))


if __name__ == "__main__":
    unittest.main()
