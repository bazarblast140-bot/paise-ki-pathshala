"""ElevenLabs error classes, Edge fallback warning, and the ledger voice record.

No live API calls. requests.get / requests.post are mocked.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import eleven_voice
import ledger

ROOT = Path(__file__).resolve().parent.parent
SECRET = "el-super-secret-key-DO-NOT-LEAK-9999"

VOICE = {
    "voice": {
        "voice_id": "7qBNUtXRGP0jPi0H4r8k",
        "model": "eleven_multilingual_v2",
        "edge_voice": "hi-IN-SwaraNeural",
        "edge_rate": "+0%",
        "stability": 0.5,
        "similarity_boost": 0.8,
        "style": 0.0,
        "speed": 1.10,
        "engine": "elevenlabs",
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


def _payment_body(message="You have insufficient credits to complete this request."):
    return {"detail": {
        "type": "payment_required",
        "code": "insufficient_credits",
        "status": "quota_exceeded",
        "message": message,
    }}


class ClassifyTests(unittest.TestCase):
    def test_each_error_class(self):
        cases = [
            (402, {"detail": {"status": "quota_exceeded", "message": "You have exceeded your quota."}}, "quota"),
            (401, _payment_body(), "quota"),
            (429, {"detail": {"type": "payment_required", "code": "insufficient_credits",
                              "message": "insufficient credits"}}, "quota"),
            (400, {"detail": {"code": "max_character_limit_exceeded",
                              "message": "This request exceeds the maximum character limit."}}, "quota"),
            (402, {}, "quota"),
            (401, {"detail": {"type": "authentication_error", "code": "invalid_api_key",
                              "message": "Invalid API key"}}, "auth"),
            (401, {}, "auth"),
            (401, {"detail": "Invalid API key"}, "auth"),
            (403, {"detail": {"type": "authorization_error", "code": "insufficient_permissions",
                              "message": "missing permission"}}, "permission"),
            (403, {"detail": {"type": "authorization_error", "code": "subscription_required",
                              "message": "subscription required"}}, "permission"),
            (403, {"detail": {"code": "voice_access_denied", "message": "voice access denied"}}, "permission"),
            (429, {"detail": {"type": "rate_limit_error", "code": "rate_limit_exceeded",
                              "message": "rate limit exceeded"}}, "retry"),
            (500, {}, "retry"),
            (503, {"detail": {"code": "system_busy", "message": "system busy"}}, "retry"),
            (422, {"detail": [{"loc": ["body", "text"], "msg": "field required", "type": "missing"}]}, "fatal"),
            (404, {"detail": {"code": "voice_not_found", "message": "voice not found"}}, "fatal"),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status, expected=expected, body=body):
                self.assertEqual(eleven_voice.classify(status, body), expected)

    def test_explicit_auth_code_beats_a_quota_phrase(self):
        body = {"detail": {
            "type": "authentication_error",
            "code": "invalid_api_key",
            "message": "Invalid API key. insufficient credits mentioned in passing",
        }}
        self.assertEqual(eleven_voice.classify(401, body), "auth")

    def test_describe_drops_long_tokens(self):
        text = eleven_voice.describe(402, _payment_body(f"insufficient credits for {SECRET}"))
        self.assertNotIn(SECRET, text)
        self.assertNotIn("DO-NOT-LEAK", text)
        self.assertIn("insufficient_credits", text)
        self.assertIn("quota_exceeded", text)


class WarningTests(unittest.TestCase):
    def test_warning_annotation_names_the_reason(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"SUPPRESS_GHA_COMMANDS": "", "GITHUB_ACTIONS": "true"}):
            with redirect_stdout(buf):
                eleven_voice.log_fallback("HTTP 402; code=insufficient_credits")
        text = buf.getvalue()
        self.assertIn("[warn] ElevenLabs quota, credit, or payment error", text)
        self.assertIn("::warning title=ElevenLabs quota::", text)
        self.assertIn("insufficient_credits", text)
        self.assertIn("free Edge TTS", text)

    def test_unit_suite_suppression_skips_only_the_annotation(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"SUPPRESS_GHA_COMMANDS": "1"}):
            with redirect_stdout(buf):
                eleven_voice.log_fallback("HTTP 402; code=quota_exceeded")
        text = buf.getvalue()
        self.assertIn("[warn]", text)
        self.assertNotIn("::warning", text)


class ShortFallbackTests(unittest.TestCase):
    def setUp(self):
        import board_short
        board_short._reset_voice_state()
        self.board = board_short

    def _spoken(self, text="ek do teen"):
        return {beat: text for beat in self.board.BEATS}

    def _env(self):
        return {
            "ELEVENLABS_API_KEY": "test-eleven-key",
            "SUPPRESS_GHA_COMMANDS": "",
            # Paid TTS is the publish path. Previews stay on Edge unless this is set.
            "PUBLISH_VISIBILITY": "public",
        }

    def test_default_voice_choice_is_unchanged(self):
        import common
        data = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(data["voice"]["engine"], "elevenlabs")
        self.assertEqual(data["voice"]["voice_id"], "qsz5tTEjPvsiIJZIpM8S")
        self.assertNotEqual(data["voice"]["voice_id"], "7qBNUtXRGP0jPi0H4r8k")
        with mock.patch.dict(os.environ, {"VIDEO_FORMAT": "short"}):
            merged = common.cfg()
        self.assertEqual(merged["voice"]["engine"], "elevenlabs")
        self.assertEqual(merged["voice"]["voice_name"], "Rudra")
        self.assertEqual(merged["voice"]["voice_id"], "ypnkIsDASPgHZuanrF0q")
        self.assertEqual(merged["voice"]["words_per_second"], 1.9)
        self.assertEqual(merged["voice"]["fallback_words_per_second"], 1.9)
        self.assertEqual(merged["voice"]["fallback_voice_id"], "qsz5tTEjPvsiIJZIpM8S")
        self.assertEqual(merged["voice"]["edge_voice"], "hi-IN-MadhurNeural")
        self.assertEqual(merged["voice"]["edge_rate"], "+0%")

    def test_quota_on_a_later_beat_stops_the_live_slot(self):
        posts = {"n": 0, "urls": []}

        def post(url, **_kwargs):
            posts["n"] += 1
            posts["urls"].append(url)
            if posts["n"] == 1:
                return FakeResp(200, b"E" * 2500, headers={"character-cost": "12"})
            return FakeResp(402, _payment_body())

        def get(_url, **_kwargs):
            return FakeResp(200, {
                "character_count": 0, "character_limit": 100000, "status": "active",
            })

        async def edge(_text, voice, rate, path):
            path.write_bytes(b"e" * 2000)
            edge.calls.append((voice, rate))

        edge.calls = []
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(self.board, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, self._env()), \
             mock.patch("requests.get", get), \
             mock.patch("requests.post", post), \
             mock.patch.object(self.board, "_edge", edge), \
             redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                self.board.synthesize_short_voices(self._spoken(), VOICE)
            run = Path(tmp)
            meta = json.loads((run / "voice_meta.json").read_text(encoding="utf-8"))
            text = buf.getvalue()
            published = run / "published.json"
            published.write_text("[]\n", encoding="utf-8")
            with mock.patch.object(ledger, "PATH", published), \
                 mock.patch.object(ledger, "ROOT", run):
                ledger.record_publish(
                    "abc123", "2026-10-02T03:00:00Z", "08:30",
                    **eleven_voice.ledger_voice_kwargs(run),
                )
            entry = json.loads(published.read_text(encoding="utf-8"))[-1]

        self.assertEqual(posts["n"], 2)
        self.assertTrue(all("7qBNUtXRGP0jPi0H4r8k" in url for url in posts["urls"]))
        self.assertEqual(edge.calls, [])
        self.assertEqual(meta["engine"], "elevenlabs")
        self.assertTrue(meta["elevenlabs_attempted"])
        self.assertIn("insufficient_credits", meta["fallback_reason"])
        self.assertIn("[credits] STOP", text)
        self.assertIn("[STOP]", text)
        self.assertNotIn("Edge TTS", text)
        self.assertEqual(entry["voice_engine"], "elevenlabs")
        self.assertIn("insufficient_credits", entry["voice_fallback"])
        self.assertEqual(entry["video_id"], "abc123")
        self.assertEqual(entry["slot"], "08:30")

    def test_precheck_falls_back_before_synthesis_when_one_short_will_not_fit(self):
        def post(*_args, **_kwargs):
            raise AssertionError("TTS must not be called when the pre-check says it will not fit")

        def get(_url, **_kwargs):
            return FakeResp(200, {
                "character_count": 9990, "character_limit": 10000, "status": "active",
            })

        async def edge(_text, voice, _rate, path):
            self.assertEqual(voice, "hi-IN-SwaraNeural")
            path.write_bytes(b"e" * 2000)

        text = "yeh ek short hai " * 20
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(self.board, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, self._env()), \
             mock.patch("requests.get", get), \
             mock.patch("requests.post", post), \
             mock.patch.object(self.board, "_edge", edge), \
             redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                self.board.synthesize_short_voices(self._spoken(text), VOICE)
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(edge.calls if hasattr(edge, "calls") else [], [])
        self.assertIn("pre-check remaining=10", meta["fallback_reason"])
        self.assertIn("[credits] STOP", buf.getvalue())
        self.assertIn("1000", buf.getvalue())
        self.assertTrue(meta["elevenlabs_attempted"])

    def test_subscription_auth_error_does_not_skip_tts(self):
        """A scoped key may 401 on /user/subscription. TTS is the auth check."""
        def get(_url, **_kwargs):
            return FakeResp(401, {"detail": {
                "type": "authentication_error", "code": "invalid_api_key", "message": "Invalid API key",
            }})

        def post(_url, **_kwargs):
            return FakeResp(200, b"E" * 2500)

        async def edge(*_args, **_kwargs):
            raise AssertionError("Edge must not run when TTS succeeds")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(self.board, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, self._env()), \
             mock.patch("requests.get", get), \
             mock.patch("requests.post", post), \
             mock.patch.object(self.board, "_edge", edge), \
             redirect_stdout(io.StringIO()):
            engine = self.board.synthesize_short_voices(self._spoken(), VOICE)
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(engine, "elevenlabs")
        self.assertEqual(meta["fallback_reason"], "")
        self.assertTrue(meta["elevenlabs_attempted"])

    def test_subscription_quota_body_falls_back_without_tts(self):
        def get(_url, **_kwargs):
            return FakeResp(401, _payment_body())

        def post(*_args, **_kwargs):
            raise AssertionError("quota-shaped subscription response should skip TTS")

        async def edge(_text, _voice, _rate, path):
            path.write_bytes(b"e" * 2000)

        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(self.board, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, self._env()), \
             mock.patch("requests.get", get), \
             mock.patch("requests.post", post), \
             mock.patch.object(self.board, "_edge", edge), \
             redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                self.board.synthesize_short_voices(self._spoken(), VOICE)
        self.assertIn("[credits] STOP", buf.getvalue())
        self.assertNotIn("Edge TTS", buf.getvalue())

    def test_invalid_key_is_a_hard_error(self):
        def post(_url, **_kwargs):
            return FakeResp(401, {"detail": {
                "type": "authentication_error",
                "code": "invalid_api_key",
                "message": f"Invalid API key {SECRET}",
            }})

        err = io.StringIO()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-eleven-key"}), \
             mock.patch("requests.post", post), \
             redirect_stderr(err), redirect_stdout(out):
            with self.assertRaises(SystemExit):
                self.board._eleven("namaste", Path(tmp) / "a.mp3", VOICE)
        text = err.getvalue() + out.getvalue()
        self.assertIn("authentication failed", text.lower())
        self.assertNotIn("::warning", text)
        self.assertNotIn(SECRET, text)

    def test_missing_permission_is_a_hard_error(self):
        def post(_url, **_kwargs):
            return FakeResp(403, {"detail": {
                "type": "authorization_error",
                "code": "insufficient_permissions",
                "message": "missing permission",
            }})

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-eleven-key"}), \
             mock.patch("requests.post", post), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit):
                self.board._eleven("namaste", Path(tmp) / "a.mp3", VOICE)
        self.assertIn("permission error", err.getvalue().lower())
        self.assertNotIn("::warning", err.getvalue())

    def test_bare_429_retries_and_does_not_fall_back(self):
        calls = {"n": 0}

        def post(_url, **_kwargs):
            calls["n"] += 1
            return FakeResp(429, {"detail": {
                "type": "rate_limit_error", "code": "rate_limit_exceeded", "message": "slow down",
            }})

        err = io.StringIO()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-eleven-key"}), \
             mock.patch("requests.post", post), \
             mock.patch("time.sleep", lambda _s: None), \
             redirect_stderr(err), redirect_stdout(out):
            with self.assertRaises(SystemExit):
                self.board._eleven("namaste", Path(tmp) / "a.mp3", VOICE)
        self.assertEqual(calls["n"], 3)
        self.assertIn("not a credit error", err.getvalue())
        self.assertNotIn("::warning", out.getvalue())
        self.assertIn("[retry] ElevenLabs 429", out.getvalue())

    def test_429_then_200_uses_paid_audio(self):
        calls = {"n": 0}

        def post(_url, **_kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                return FakeResp(429, {"detail": {"code": "rate_limit_exceeded", "message": "slow down"}})
            return FakeResp(200, b"E" * 2500)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-eleven-key"}), \
             mock.patch("requests.post", post), \
             mock.patch("time.sleep", lambda _s: None), \
             redirect_stdout(io.StringIO()):
            path = Path(tmp) / "a.mp3"
            self.assertTrue(self.board._eleven("namaste", path, VOICE))
            self.assertEqual(path.read_bytes()[:1], b"E")
        self.assertEqual(calls["n"], 2)

    def test_429_quota_body_does_not_retry(self):
        calls = {"n": 0}

        def post(_url, **_kwargs):
            calls["n"] += 1
            return FakeResp(429, _payment_body())

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-eleven-key"}), \
             mock.patch("requests.post", post), \
             mock.patch("time.sleep", lambda _s: (_ for _ in ()).throw(AssertionError("no retry"))):
            with self.assertRaises(eleven_voice.QuotaFallback) as caught:
                self.board._eleven("namaste", Path(tmp) / "a.mp3", VOICE)
        self.assertEqual(calls["n"], 1)
        self.assertIn("insufficient_credits", caught.exception.reason)


class LongFormTests(unittest.TestCase):
    def test_quota_falls_back_and_auth_does_not(self):
        import run_voice

        def quota(_url, **_kwargs):
            return FakeResp(402, _payment_body())

        def auth(_url, **_kwargs):
            return FakeResp(401, {"detail": {"code": "invalid_api_key", "message": "Invalid API key"}})

        def edge(_text, _c, path):
            path.write_bytes(b"e" * 2000)
            edge.calls += 1

        edge.calls = 0
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(run_voice, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, {
                 "ELEVENLABS_API_KEY": "test-eleven-key",
                 "SUPPRESS_GHA_COMMANDS": "",
                 "PUBLISH_VISIBILITY": "public",
             }), \
             mock.patch("requests.post", quota), \
             mock.patch.object(run_voice, "edge_request", edge), \
             redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                run_voice.synthesize("ek chhota vakya.", VOICE)
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(edge.calls, 0)
        self.assertEqual(meta["engine"], "elevenlabs")
        self.assertIn("insufficient_credits", meta["fallback_reason"])
        self.assertIn("[credits] STOP", buf.getvalue())
        self.assertIn("[OK] narration: elevenlabs", buf.getvalue())

        edge.calls = 0
        err = io.StringIO()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(run_voice, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, {
                 "ELEVENLABS_API_KEY": "test-eleven-key",
                 "PUBLISH_VISIBILITY": "public",
             }), \
             mock.patch("requests.post", auth), \
             mock.patch.object(run_voice, "edge_request", edge), \
             redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit):
                run_voice.synthesize("ek chhota vakya.", VOICE)
        self.assertEqual(edge.calls, 0)
        self.assertIn("authentication failed", err.getvalue().lower())
        self.assertFalse((Path(tmp) / "voice_meta.json").exists())


if __name__ == "__main__":
    unittest.main()
