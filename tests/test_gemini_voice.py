"""Gemini Charon is one request for the whole Short. No live API calls."""
import base64
import io
import json
import math
import os
import struct
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import gemini_voice
import ledger

ROOT = Path(__file__).resolve().parent.parent
SECRET = "gemini-super-secret-key-DO-NOT-LEAK-9999"
ORDER = ("hook", "pehchan", "matlab", "karein")
VOICE = {
    "voice": {
        "engine": "elevenlabs",
        "voice_id": "ypnkIsDASPgHZuanrF0q",
        "words_per_second": 1.9,
        "edge_voice": "hi-IN-MadhurNeural",
        "edge_rate": "+0%",
        "model": "eleven_multilingual_v2",
        "stability": 0.45,
        "similarity_boost": 0.8,
        "style": 0.3,
        "speed": 1.0,
    }
}


class FakeResp:
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


def _tone(seconds, rate=24000):
    frames = int(seconds * rate)
    pcm = bytearray()
    for i in range(frames):
        sample = int(8000 * math.sin(2 * math.pi * 220 * i / rate))
        pcm += struct.pack("<h", sample)
    return bytes(pcm)


def _wav_bytes(seconds, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(_tone(seconds, rate))
    return buf.getvalue()


def _b64_audio(seconds=8.0, riff=False):
    raw = _wav_bytes(seconds) if riff else _tone(seconds)
    return base64.b64encode(raw).decode("ascii")


def _words(count, duration):
    step = duration / count
    return [
        {"word": f"शब्द{i}", "start": i * step, "end": (i + 1) * step}
        for i in range(count)
    ]


def _spoken(text="एक दो तीन चार"):
    return {beat: text for beat in ORDER}


class DecodeTests(unittest.TestCase):
    def test_request_body_matches_the_working_call(self):
        body = gemini_voice.request_body("पूरा स्क्रिप्ट")
        self.assertEqual(body["model"], "gemini-3.8-flash-tts")
        content = body["input"][0]["content"][0]
        self.assertEqual(content["text"], "पूरा स्क्रिप्ट")
        self.assertEqual(content["annotations"][0]["type"], "speech_metadata")
        self.assertIn("Indian finance teacher", content["annotations"][0]["style"])
        self.assertEqual(body["response_format"]["mime_type"], "audio/l16")
        self.assertEqual(body["response_format"]["sample_rate"], 24000)
        self.assertEqual(body["generation_config"]["speech_config"], [{"voice": "Charon"}])

    def test_nested_audio_and_inline_data(self):
        payload = {"output": [{"content": [{"type": "audio", "data": "abc"}]}]}
        self.assertEqual(gemini_voice.find_audio(payload), ["abc"])
        inline = {"candidates": [{"content": {"parts": [{"inlineData": {"data": "xyz"}}]}}]}
        self.assertEqual(gemini_voice.find_audio(inline), ["xyz"])

    def test_raw_s16le_and_riff_both_become_wav(self):
        raw = gemini_voice.decode_audio([base64.b64encode(_tone(0.2)).decode("ascii")])
        self.assertTrue(raw.startswith(b"RIFF"))
        with wave.open(io.BytesIO(raw), "rb") as handle:
            self.assertEqual(handle.getframerate(), 24000)
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
        riff = gemini_voice.decode_audio([base64.b64encode(_wav_bytes(0.2)).decode("ascii")])
        self.assertTrue(riff.startswith(b"RIFF"))
        with wave.open(io.BytesIO(riff), "rb") as handle:
            self.assertGreater(handle.getnframes(), 0)

    def test_redact_removes_the_key(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": SECRET}):
            self.assertNotIn(SECRET, gemini_voice.redact(f"failed {SECRET}"))
            self.assertIn("***", gemini_voice.redact(f"failed {SECRET}"))

    def test_beat_bounds_follow_whisper_words(self):
        spoken = _spoken()
        words = _words(16, 8.0)
        bounds = gemini_voice.beat_bounds(spoken, ORDER, words, 8.0)
        self.assertEqual(list(bounds), list(ORDER))
        self.assertEqual(bounds["hook"][0], 0.0)
        self.assertAlmostEqual(bounds["karein"][1], 8.0, places=2)
        cursor = 0.0
        for beat in ORDER:
            start, end = bounds[beat]
            self.assertGreaterEqual(start, cursor - 1e-6)
            self.assertGreaterEqual(end - start, 1.05)
            cursor = end

    def test_missing_whisper_splits_by_script_length(self):
        spoken = {"hook": "एक दो", "pehchan": "तीन चार पांच छह"}
        bounds = gemini_voice.beat_bounds(spoken, ORDER, [], 6.0)
        self.assertAlmostEqual(bounds["pehchan"][1] - bounds["pehchan"][0], 4.0, places=2)
        self.assertAlmostEqual(bounds["hook"][1] - bounds["hook"][0], 2.0, places=2)


class SpeakTests(unittest.TestCase):
    def _env(self, **extra):
        env = {
            "GEMINI_API_KEY": SECRET,
            "SUPPRESS_GHA_COMMANDS": "1",
            "PUBLISH_VISIBILITY": "public",
            "PAID_TTS_ENABLED": "true",
        }
        env.update(extra)
        return env

    def _posts(self):
        seen = {"calls": []}

        def post(url, **kwargs):
            seen["calls"].append((url, kwargs))
            return FakeResp(200, {
                "output": [{"content": [{"type": "audio", "data": _b64_audio(8.0)}]}],
            })

        seen["post"] = post
        return seen

    def test_one_request_splits_beats_and_records_the_engine(self):
        import board_short
        import credit_log
        seen = self._posts()
        words = _words(16, 8.0)
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run), \
                 mock.patch.object(credit_log, "PATH", run / "credits.json"), \
                 mock.patch.dict(os.environ, {**self._env(), "GITHUB_STEP_SUMMARY": str(run / "summary.txt")}), \
                 mock.patch("requests.post", seen["post"]), \
                 mock.patch("sync_gate.transcribe_file", return_value=words), \
                 redirect_stdout(buf):
                engine = board_short.synthesize_short_voices(_spoken(), VOICE)
            meta = json.loads((run / "voice_meta.json").read_text(encoding="utf-8"))
            summary = (run / "summary.txt").read_text(encoding="utf-8")
            stored = json.loads((run / "gemini_words.json").read_text(encoding="utf-8"))
            published = run / "published.json"
            published.write_text("[]\n", encoding="utf-8")
            with mock.patch.object(ledger, "PATH", published):
                ledger.record_publish("vid", "2026-10-08T10:30:00Z", "16:00", **ledger_kwargs(run))
            entry = json.loads(published.read_text(encoding="utf-8"))[-1]
            for beat in ORDER:
                self.assertTrue((run / f"tts_{beat}.wav").exists())
                self.assertGreater((run / f"tts_{beat}.wav").stat().st_size, 1000)
        text = buf.getvalue()
        self.assertEqual(engine, "gemini")
        self.assertEqual(len(seen["calls"]), 1)
        url, kwargs = seen["calls"][0]
        self.assertEqual(url, gemini_voice.URL)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], SECRET)
        self.assertEqual(kwargs["timeout"], 180)
        self.assertEqual(kwargs["json"]["model"], "gemini-3.8-flash-tts")
        self.assertEqual(kwargs["json"]["generation_config"]["speech_config"][0]["voice"], "Charon")
        joined = kwargs["json"]["input"][0]["content"][0]["text"]
        self.assertEqual(joined.count("एक दो तीन चार"), 4)
        self.assertEqual(meta["engine"], "gemini")
        self.assertFalse(meta["elevenlabs_attempted"])
        self.assertEqual(meta["fallback_reason"], "")
        self.assertIn("voice_engine=gemini", summary)
        self.assertIn("voice_engine=gemini", text)
        self.assertEqual(entry["voice_engine"], "gemini")
        self.assertEqual(list(stored["beats"]), list(ORDER))
        self.assertNotIn(SECRET, text)
        self.assertNotIn(SECRET, summary)
        self.assertNotIn(SECRET, json.dumps(meta))

    def test_http_429_timeout_missing_audio_and_short_file_fall_back(self):
        import board_short
        import credit_log
        import requests

        cases = {
            "429": FakeResp(429, {"error": {"message": SECRET}}),
            "500": FakeResp(500, {}),
            "timeout": requests.Timeout(SECRET),
            "missing": FakeResp(200, {"output": [{"content": [{"type": "text", "text": "no audio"}]}]}),
            "short": FakeResp(200, {"output": [{"type": "audio", "data": _b64_audio(0.3)}]}),
        }
        for name, result in cases.items():
            with self.subTest(name=name):
                posts = {"n": 0}

                def post(_url, **_kwargs):
                    posts["n"] += 1
                    if isinstance(result, Exception):
                        raise result
                    return result

                async def edge(_text, voice, rate, path):
                    edge.calls.append((voice, rate))
                    path.write_bytes(b"e" * 2000)

                edge.calls = []
                buf = io.StringIO()
                with tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.object(board_short, "RUN", Path(tmp)), \
                     mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
                     mock.patch.dict(os.environ, self._env(PAID_TTS_ENABLED="false")), \
                     mock.patch("requests.post", post), \
                     mock.patch.object(board_short, "_edge", edge), \
                     redirect_stdout(buf):
                    engine = board_short.synthesize_short_voices(
                        _spoken("एक दो तीन चार " * 8), VOICE)
                    meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
                text = buf.getvalue()
                self.assertEqual(posts["n"], 1)
                self.assertEqual(engine, "edge")
                self.assertEqual(meta["engine"], "edge")
                self.assertFalse(meta["elevenlabs_attempted"])
                self.assertEqual(len(edge.calls), len(ORDER))
                self.assertTrue(all(call == ("hi-IN-MadhurNeural", "+0%") for call in edge.calls))
                self.assertNotIn(SECRET, text)
                self.assertNotIn(SECRET, meta["fallback_reason"])
                self.assertIn("Gemini TTS failed", text)
                if name == "429":
                    self.assertIn("HTTP 429", meta["fallback_reason"])
                if name == "500":
                    self.assertIn("HTTP 500", meta["fallback_reason"])
                if name == "timeout":
                    self.assertIn("180", meta["fallback_reason"])
                if name == "missing":
                    self.assertIn("no audio", meta["fallback_reason"])
                if name == "short":
                    self.assertIn("far from expected", meta["fallback_reason"])

    def test_after_gemini_rudra_is_used_only_above_the_floor(self):
        import board_short
        import credit_log
        posts = {"urls": []}

        def post(url, **_kwargs):
            posts["urls"].append(url)
            if "generativelanguage" in url:
                return FakeResp(429, {})
            return FakeResp(200, b"E" * 2500, headers={"character-cost": "12"})

        def get(_url, **_kwargs):
            return FakeResp(200, {
                "character_count": 0, "character_limit": 100000, "status": "active",
            })

        async def edge(*_args, **_kwargs):
            raise AssertionError("Edge must wait until Rudra cannot cover the Short")

        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, {**self._env(), "ELEVENLABS_API_KEY": "eleven-key"}), \
             mock.patch("requests.post", post), \
             mock.patch("requests.get", get), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "_edge", edge), \
             redirect_stdout(buf):
            engine = board_short.synthesize_short_voices(_spoken(), VOICE)
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(engine, "elevenlabs")
        self.assertTrue(meta["elevenlabs_attempted"])
        self.assertIn("HTTP 429", meta["fallback_reason"])
        self.assertEqual(sum("generativelanguage" in url for url in posts["urls"]), 1)
        self.assertEqual(sum("elevenlabs" in url for url in posts["urls"]), len(ORDER))
        self.assertNotIn(SECRET, buf.getvalue())

    def test_rudra_below_the_floor_lands_on_edge(self):
        import board_short
        import credit_log

        def post(url, **_kwargs):
            if "generativelanguage" in url:
                return FakeResp(503, {})
            raise AssertionError("Rudra must not be called below the 1000 floor")

        def get(_url, **_kwargs):
            return FakeResp(200, {
                "character_count": 9500, "character_limit": 10000, "status": "active",
            })

        async def edge(_text, voice, rate, path):
            edge.calls.append((voice, rate))
            path.write_bytes(b"e" * 2000)

        edge.calls = []
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, {**self._env(), "ELEVENLABS_API_KEY": "eleven-key",
                                          "GITHUB_STEP_SUMMARY": str(Path(tmp) / "summary.txt")}), \
             mock.patch("requests.post", post), \
             mock.patch("requests.get", get), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "_edge", edge), \
             redirect_stdout(io.StringIO()):
            engine = board_short.synthesize_short_voices(_spoken(), VOICE)
            summary = (Path(tmp) / "summary.txt").read_text(encoding="utf-8")
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(engine, "edge")
        self.assertIn("1000", meta["fallback_reason"])
        self.assertIn("voice_engine=edge", summary)
        self.assertGreaterEqual(len(edge.calls), len(ORDER))

    def test_proof_audio_does_not_call_gemini(self):
        import board_short
        import credit_log

        def post(*_args, **_kwargs):
            raise AssertionError("a free proof must not call Gemini or ElevenLabs")

        async def edge(_text, voice, rate, path):
            edge.calls.append(voice)
            path.write_bytes(b"e" * 2000)

        edge.calls = []
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, self._env(PROOF_FREE_AUDIO="edge")), \
             mock.patch("requests.post", post), \
             mock.patch.object(board_short, "_edge", edge), \
             redirect_stdout(io.StringIO()):
            engine = board_short.synthesize_short_voices(_spoken(), VOICE)
        self.assertEqual(engine, "edge")
        self.assertEqual(len(edge.calls), len(ORDER))

    def test_workflow_passes_the_key_as_an_env_var(self):
        text = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", text)
        self.assertNotIn("print(os.environ", text)


def ledger_kwargs(run):
    from eleven_voice import ledger_voice_kwargs
    return ledger_voice_kwargs(run)


if __name__ == "__main__":
    unittest.main()
