"""Paid ElevenLabs runs only after a free render has passed, and only when the switch is on."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from PIL import Image

import board_short
import credit_log
import eleven_voice
import scene_frames

ROOT = Path(__file__).resolve().parent.parent
ORDER = ("hook", "pehchan", "matlab", "karein")


def _voice():
    return {
        "engine": "elevenlabs",
        "voice_id": "ypnkIsDASPgHZuanrF0q",
        "words_per_second": 1.9,
        "model": "eleven_multilingual_v2",
        "stability": 0.45,
        "similarity_boost": 0.8,
        "style": 0.3,
        "speed": 1.0,
    }


def _topic():
    return {
        "key": "loss_recovery",
        "cat": "RISK",
        "topic": "नुकसान",
        "facts": ["दस प्रतिशत गिरे तो ग्यारह प्रतिशत वापसी चाहिए।"],
        "beats": {beat: ("शीर्षक", "उप") for beat in ORDER},
        "_live_short": True,
        "_live_beats": "four",
        "_voice": _voice(),
    }


def _narration():
    return {
        "title": "नुकसान की वापसी",
        "hook_line": "क्या नुकसान उतना ही वापस आता है?",
        "beats": {beat: " ".join(["नियम"] * 22) for beat in ORDER},
        "source": "llm",
    }


def _cfg():
    return {"voice": _voice(), "models": {"script_model": "deepseek-flash"}}


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.headers = {}
        if isinstance(body, (bytes, bytearray)):
            self.content = bytes(body)
            self.text = ""
            self._json = None
        else:
            self._json = body
            self.text = json.dumps(body)
            self.content = self.text.encode("utf-8")

    def json(self):
        return self._json


class PaidRenderTests(unittest.TestCase):
    def _prepare(self, spoken, narration, topic, _c, _spec):
        order = board_short._lesson(topic)[0]
        raw = {beat: 13.0 for beat in order}
        times, _total = board_short._timeline(raw, order)
        audio = {beat: board_short.RUN / f"voice_clean_{beat}.wav" for beat in order}
        return spoken, narration, audio, raw, times, _total

    def _frames(self, _topic, _beat, count, _spec):
        image = Image.new("RGB", (80, 80), (30, 120, 60))
        return [image.copy() for _ in range(int(count))], [False] * int(count)

    def _review(self, *, ok=True, craft=100, summary="overall 90 (craft 100)"):
        return {
            "public_ok": ok,
            "passed": ok,
            "overall": 90 if ok else 40,
            "parts": {
                "hook": 80, "visual": 80, "accuracy": 100,
                "trend": 100, "craft": craft, "sync": 100,
            },
            "summary": summary,
            "notes": [] if ok else ["craft hold"],
        }

    def _run(self, *, craft, review, env, post=None, topic=None, sync=None, review_fn=None, proof_seen=None):
        posts = []

        def craft_samples(*_args, **_kwargs):
            posts.append("craft")
            if isinstance(craft, Exception):
                raise craft
            return [], [], []

        def assemble(*_args, **_kwargs):
            posts.append("encode")
            if proof_seen is not None:
                proof_seen.append(os.environ.get("PROOF_FREE_AUDIO", ""))
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")

        def reviewed(*_args, **kwargs):
            if review_fn is not None:
                return review_fn(*_args, **kwargs)
            return review

        def http_post(*_args, **_kwargs):
            posts.append("eleven")
            if post is not None:
                return post(*_args, **_kwargs)
            return _Resp(200, b"E" * 2500)

        def http_get(*_args, **_kwargs):
            posts.append("get")
            return _Resp(200, {
                "character_count": 0,
                "character_limit": 100000,
                "status": "active",
            })

        spec = {"mode": "schematic", "source": None, "as_of": None}
        err = io.StringIO()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(board_short, "prepare_spoken", side_effect=self._prepare), \
             mock.patch.object(scene_frames, "frames_for", side_effect=self._frames), \
             mock.patch.object(scene_frames, "frames_safe_area_notes", return_value=[]), \
             mock.patch.object(board_short, "visual_gate", return_value=None), \
             mock.patch.object(board_short, "_craft_samples", side_effect=craft_samples), \
             mock.patch.object(board_short.self_review, "full_review", side_effect=reviewed), \
             mock.patch.object(board_short, "assemble", side_effect=assemble), \
             mock.patch.object(board_short, "duration", return_value=55.0), \
             mock.patch.object(board_short, "_trim_rendered", side_effect=lambda order: {b: 13.0 for b in order}), \
             mock.patch.object(board_short, "_sync_report", return_value=sync or {"ok": True, "reason": "", "words": [], "scenes": []}), \
             mock.patch("requests.post", http_post), \
             mock.patch("requests.get", http_get), \
             redirect_stdout(out), redirect_stderr(err):
            for key in ("DRY_RUN", "PIPELINE_MODE", "ELEVENLABS_OPT_IN", "RENDER_SAMPLE", "SAMPLE_NO_UPLOAD"):
                os.environ.pop(key, None)
            os.environ["PUBLISH_VISIBILITY"] = env.get("PUBLISH_VISIBILITY", "public")
            if "PAID_TTS_ENABLED" in env:
                os.environ["PAID_TTS_ENABLED"] = env["PAID_TTS_ENABLED"]
            else:
                os.environ.pop("PAID_TTS_ENABLED", None)
            os.environ["ELEVENLABS_API_KEY"] = "test-key"
            try:
                board_short.produce_short(topic or _topic(), _narration(), _cfg(), spec, source="llm")
            except SystemExit:
                return posts, err.getvalue() + out.getvalue()
            credits = Path(tmp) / "credits.json"
            spent = 0
            if credits.exists():
                spent = int(json.loads(credits.read_text(encoding="utf-8")).get("elevenlabs_characters_sent") or 0)
            return posts, spent

    def test_craft_samples_exception_never_calls_elevenlabs(self):
        posts, log = self._run(
            craft=ValueError("not enough values to unpack (expected 3, got 2)"),
            review=self._review(),
            env={"PUBLISH_VISIBILITY": "public", "PAID_TTS_ENABLED": "true"},
        )
        self.assertIn("0 characters spent", log)
        self.assertIn("craft", posts)
        self.assertNotIn("eleven", posts)
        self.assertNotIn("get", posts)
        self.assertNotIn("encode", posts)

    def test_craft_hold_never_calls_elevenlabs(self):
        posts, log = self._run(
            craft=None,
            review=self._review(ok=False, craft=0, summary="craft hold empty background"),
            env={"PUBLISH_VISIBILITY": "public", "PAID_TTS_ENABLED": "true"},
        )
        self.assertIn("HOLD", log)
        self.assertIn("0 characters spent", log)
        self.assertIn("craft", posts)
        self.assertIn("encode", posts)
        self.assertNotIn("eleven", posts)
        self.assertNotIn("get", posts)

    def test_switch_off_never_calls_elevenlabs(self):
        for flag in (None, "false", "False", "1", "yes"):
            env = {"PUBLISH_VISIBILITY": "public"}
            if flag is not None:
                env["PAID_TTS_ENABLED"] = flag
            with self.subTest(flag=flag):
                posts, log = self._run(craft=None, review=self._review(), env=env)
                self.assertIn("[BLOCK]", log)
                self.assertIn("0 characters", log)
                self.assertNotIn("eleven", posts)
                self.assertNotIn("get", posts)
                self.assertLess(posts.index("craft"), posts.index("encode"))

    def test_happy_path_calls_elevenlabs_once_per_beat_after_the_prerender(self):
        posts, spent = self._run(
            craft=None,
            review=self._review(),
            env={"PUBLISH_VISIBILITY": "public", "PAID_TTS_ENABLED": "true"},
        )
        self.assertEqual(spent > 0, True)
        elevens = [item for item in posts if item == "eleven"]
        self.assertEqual(len(elevens), len(ORDER))
        self.assertLess(posts.index("craft"), posts.index("encode"))
        self.assertLess(posts.index("encode"), posts.index("eleven"))

    def test_direct_eleven_request_is_refused_when_the_switch_is_off(self):
        def post(*_args, **_kwargs):
            raise AssertionError("ElevenLabs client must not be called")

        for value in ("", "false"):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.dict(os.environ, {
                         "ELEVENLABS_API_KEY": "test-key",
                         "PAID_TTS_ENABLED": value,
                     }, clear=False), \
                     mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
                     mock.patch("requests.post", post), \
                     redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    if value == "":
                        os.environ.pop("PAID_TTS_ENABLED", None)
                    with self.assertRaises(SystemExit) as caught:
                        board_short._eleven("नियम", Path(tmp) / "a.mp3", _cfg())
                self.assertIn("[BLOCK]", str(caught.exception))
                self.assertFalse((Path(tmp) / "credits.json").exists())

    def test_workflows_pass_the_switch_and_health_check_does_not_gate_tts(self):
        wf = ROOT / ".github" / "workflows"
        for name in ("paise-ki-pathshala.yml", "render-sample.yml", "trader-quiz.yml", "health-check.yml"):
            text = (wf / name).read_text(encoding="utf-8")
            self.assertIn("ELEVENLABS_API_KEY", text)
            self.assertIn("PAID_TTS_ENABLED: ${{ vars.PAID_TTS_ENABLED }}", text)
        health = (ROOT / "health_check.py").read_text(encoding="utf-8")
        self.assertNotIn("require_paid_tts", health)
        self.assertNotIn("PAID_TTS_ENABLED", health)
        self.assertIn("/v1/user/subscription", health)
        self.assertFalse(eleven_voice.paid_tts_enabled())

    def test_atr_proves_on_edge_before_any_elevenlabs_call(self):
        paid = {"PUBLISH_VISIBILITY": "public", "PAID_TTS_ENABLED": "true"}
        with mock.patch.dict(os.environ, paid, clear=False):
            for key in ("DRY_RUN", "PIPELINE_MODE", "ELEVENLABS_OPT_IN", "RENDER_SAMPLE"):
                os.environ.pop(key, None)
            atr = _topic()
            atr["_atr"] = {"distance": 42}
            atr["_live_short"] = True
            self.assertTrue(board_short._needs_paid_proof(atr, _cfg()))
            atr["_live_short"] = False
            self.assertTrue(board_short._needs_paid_proof(atr, _cfg()))
        proof = []
        posts, second = self._run(
            craft=None,
            review=self._review(),
            env=paid,
            topic=atr,
            proof_seen=proof,
        )
        self.assertEqual(proof[0], "edge")
        self.assertIn("eleven", posts)
        self.assertLess(posts.index("encode"), posts.index("eleven"))
        self.assertGreater(second, 0)

    def test_edge_sync_hold_never_calls_elevenlabs(self):
        def reviewed(*_args, **kwargs):
            sync = kwargs.get("sync") or {}
            if not sync.get("ok"):
                return self._review(ok=False, summary="sync hold narration does not match")
            return self._review()

        proof = []
        posts, log = self._run(
            craft=None,
            review=self._review(),
            env={"PUBLISH_VISIBILITY": "public", "PAID_TTS_ENABLED": "true"},
            sync={"ok": False, "reason": "narration does not match the script", "words": [], "scenes": []},
            review_fn=reviewed,
            proof_seen=proof,
        )
        self.assertEqual(proof, ["edge"])
        self.assertIn("0 characters spent", log)
        self.assertIn("sync hold", log)
        self.assertNotIn("eleven", posts)
        self.assertNotIn("get", posts)

    def test_paid_timeline_hold_blocks_upload_and_logs_characters(self):
        calls = {"n": 0}

        def reviewed(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return self._review()
            return self._review(ok=False, craft=0, summary="panel fill 20% is under 35%")

        posts, log = self._run(
            craft=None,
            review=self._review(),
            env={"PUBLISH_VISIBILITY": "public", "PAID_TTS_ENABLED": "true"},
            review_fn=reviewed,
        )
        self.assertGreaterEqual(calls["n"], 2)
        self.assertIn("eleven", posts)
        self.assertIn("no upload", log)
        self.assertIn("characters spent", log)
        self.assertNotIn("0 characters spent", log)
        self.assertIn("panel fill", log)

    def test_prepare_spoken_does_not_add_a_library_voice(self):
        topic = _topic()
        script = _narration()

        def synthesize(*_args, **_kwargs):
            return None

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "require_scene_frames", return_value=None), \
             mock.patch.object(board_short, "fit_estimated_duration", return_value=script), \
             mock.patch.object(board_short, "synthesize_short_voices", side_effect=synthesize), \
             mock.patch.object(board_short, "_trim_rendered", side_effect=lambda order: {b: 13.0 for b in order}), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", side_effect=AssertionError("no voice api")):
            board_short.prepare_spoken(dict(script["beats"]), script, topic, _cfg(), {})

    def test_library_voice_write_waits_for_the_paid_flag(self):
        order = []

        def ensure(_c):
            order.append("ensure")
            return "ypnkIsDASPgHZuanrF0q"

        spoken = {beat: "नियम" for beat in ORDER}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", side_effect=ensure), \
             mock.patch.object(board_short, "preflight_eleven_credits", return_value=None), \
             mock.patch.object(board_short, "tts", return_value=None), \
             mock.patch.dict(os.environ, {
                 "ELEVENLABS_API_KEY": "test-key",
                 "PUBLISH_VISIBILITY": "public",
             }, clear=False), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            os.environ.pop("PAID_TTS_ENABLED", None)
            os.environ.pop("PIPELINE_MODE", None)
            with self.assertRaises(SystemExit):
                board_short.synthesize_short_voices(spoken, _cfg(), beats=ORDER)
            self.assertEqual(order, [])
            os.environ["PAID_TTS_ENABLED"] = "true"
            board_short.synthesize_short_voices(spoken, _cfg(), beats=("hook",))
            self.assertEqual(order, ["ensure"])
