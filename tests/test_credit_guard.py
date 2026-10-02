"""Paid-credit guards: gates before TTS, hold/filled slots, DeepSeek cap, Edge previews, credit log.

No live network. ElevenLabs and DeepSeek clients are mocked.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import auto_agent
import board_short
import common
import credit_log
import eleven_voice
import ledger
import review_gate
import run_pipeline
import run_voice
import self_review
import short_layout
import slot_guard
import slot_watch

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"

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
    }
}


def _topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना", "ये संभावना है, गारंटी नहीं"],
    }


class _Resp:
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


def _completion(text="ok", prompt=3, completion=1):
    message = SimpleNamespace(content=text, reasoning_content="", model_extra=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    return SimpleNamespace(choices=[choice], usage=usage)


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class GateBeforeTtsTests(unittest.TestCase):
    def test_teaching_gate_failure_exits_before_any_tts_call(self):
        topic = _topic()
        posts = []

        def post(*_args, **_kwargs):
            posts.append("post")
            raise AssertionError("TTS must not be called when the teaching gate fails")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", return_value={}), \
             mock.patch("requests.post", post), \
             mock.patch("requests.get", side_effect=AssertionError("no subscription read")):
            with self.assertRaises(SystemExit):
                board_short.narration(topic, {"models": {"script_model": "deepseek-flash"}})
            block = json.loads((Path(tmp) / "narration_block.json").read_text(encoding="utf-8"))
            review = json.loads((Path(tmp) / "self_review.json").read_text(encoding="utf-8"))
            cache_left = (Path(tmp) / "narration.json").exists()
        self.assertEqual(posts, [])
        self.assertTrue(block["blocked"])
        self.assertTrue(block["narration_fallback"])
        self.assertIn("topic-card", block["reason"])
        self.assertIn("no paid voice", block["reason"])
        self.assertFalse(review["public_ok"])
        self.assertFalse(cache_left)

    def test_synthesize_refuses_when_the_block_file_exists(self):
        def post(*_args, **_kwargs):
            raise AssertionError("TTS must not run after a narration block")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch("requests.post", post):
            (Path(tmp) / "narration_block.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                board_short.synthesize_short_voices(
                    {beat: "ek" for beat in board_short.BEATS}, VOICE)

    def test_gate2_failure_does_not_call_tts(self):
        with mock.patch.object(run_voice, "read", return_value="TITLE:\nX\n"), \
             mock.patch.object(run_voice, "claude", return_value="ek"), \
             mock.patch.object(run_voice, "synthesize", side_effect=AssertionError("tts")) as syn:
            with self.assertRaises(SystemExit):
                run_voice.main()
            syn.assert_not_called()

    def test_fallback_flag_fails_self_review_without_lowering_the_scores(self):
        topic = _topic()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "source": None, "as_of": None, "series": [], "fabricated": False}
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        overlays = short_layout.overlay_plan(topic, times, narration["hook_line"])
        plain = self_review.full_review(topic, narration, spec, overlays, trends={})
        self.assertTrue(plain["public_ok"])
        narration["narration_fallback"] = True
        blocked = self_review.full_review(topic, narration, spec, overlays, trends={})
        self.assertGreaterEqual(blocked["overall"], 75)
        self.assertFalse(blocked["passed"])
        self.assertFalse(blocked["public_ok"])
        self.assertTrue(blocked["narration_fallback"])

    def test_publish_files_refuse_a_fallback_even_when_the_report_says_ready(self):
        script = "TITLE:\nHammer\n\n" + ("शब्द " * 30)
        review = {"public_ok": True, "narration_fallback": True, "summary": "overall 96.7"}
        decision = review_gate.decide(
            "VERDICT: [READY TO PUBLISH]\n", True, script, review, "short")
        self.assertFalse(decision["public_ready"])
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
            (run / "verify_meta.json").write_text(json.dumps({
                "ai_verdict_valid": True,
                "verdict": "READY",
                "public_ready": True,
            }), encoding="utf-8")
            (run / "self_review.json").write_text(json.dumps({"public_ok": True}), encoding="utf-8")
            self.assertTrue(review_gate.files_allow_public(run, "short"))
            (run / "narration_block.json").write_text("{}\n", encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))
            (run / "narration_block.json").unlink()
            (run / "self_review.json").write_text(
                json.dumps({"public_ok": True, "narration_fallback": True}), encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))
            (run / "self_review.json").write_text(
                json.dumps({"public_ok": True, "narration_source": "fallback", "overall": 96.7}),
                encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))


class SlotPaidCallTests(unittest.TestCase):
    def _ledger(self, path, entries):
        path.write_text(json.dumps(entries), encoding="utf-8")

    def test_hold_and_filled_slots_exit_before_any_stage(self):
        cases = [
            ([{"date_ist": "2026-10-02", "slot": "21:00", "video_id": None, "platform": "hold"}], "on hold"),
            ([{"date_ist": "2026-10-02", "slot": "21:00", "video_id": "abc123", "platform": "youtube"}], "already filled"),
        ]
        for entries, needle in cases:
            with self.subTest(needle=needle):
                with tempfile.TemporaryDirectory() as tmp:
                    ledger_path = Path(tmp) / "published.json"
                    self._ledger(ledger_path, entries)
                    env = {
                        "PUBLISH_VISIBILITY": "public",
                        "PUBLISH_SLOT_IST": "21:00",
                        "PUBLISH_DATE_IST": "2026-10-02",
                    }
                    with mock.patch.object(slot_guard, "LEDGER", ledger_path), \
                         mock.patch.dict(os.environ, env), \
                         mock.patch.object(run_pipeline, "run", side_effect=AssertionError("stage")), \
                         mock.patch.object(common, "claude", side_effect=AssertionError("deepseek")), \
                         mock.patch("requests.post", side_effect=AssertionError("paid http")):
                        with self.assertRaises(SystemExit) as caught:
                            run_pipeline.main()
                        self.assertEqual(caught.exception.code, 0)
                        reason = slot_guard.scheduled_slot_block()
                        self.assertIn(needle, reason)

    def test_hold_is_reported_as_on_hold_and_not_filled(self):
        now = datetime(2026, 10, 2, 21, 49, tzinfo=slot_watch.IST)
        entries = [{
            "date_ist": "2026-10-02",
            "slot": "21:00",
            "video_id": None,
            "platform": "hold",
        }]
        early = datetime(2026, 10, 2, 5, 47, tzinfo=slot_guard.IST)
        self.assertIsNone(slot_guard.next_unfilled(early, [{
            "date_ist": "2026-10-02", "slot": "08:30", "video_id": None, "platform": "hold",
        }]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            gout = Path(tmp) / "gha"
            env = {"SLOT_CHECK": "21:00", "GITHUB_OUTPUT": str(gout)}
            buf = io.StringIO()
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt, \
                 redirect_stdout(buf):
                dt.now.return_value = now
                slot_watch.main()
            text = buf.getvalue()
            flags = gout.read_text(encoding="utf-8")
        self.assertIn("on hold", text)
        self.assertNotIn("video id missing", text)
        self.assertIn("missing=false", flags)
        self.assertIn("fill=false", flags)

    def test_board_short_does_not_narrate_a_held_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "published.json"
            ledger_path.write_text(json.dumps([{
                "date_ist": "2026-10-02", "slot": "21:00", "video_id": None, "platform": "hold",
            }]), encoding="utf-8")
            env = {
                "PUBLISH_VISIBILITY": "public",
                "PUBLISH_SLOT_IST": "21:00",
                "PUBLISH_DATE_IST": "2026-10-02",
            }
            with mock.patch.object(slot_guard, "LEDGER", ledger_path), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(sys, "argv", ["board_short.py"]), \
                 mock.patch.object(board_short, "narration", side_effect=AssertionError("llm")), \
                 mock.patch("requests.post", side_effect=AssertionError("tts")):
                with self.assertRaises(SystemExit):
                    board_short.main()

    def test_auto_agent_treats_the_skip_as_done(self):
        out = "[skip] 2026-10-02 21:00 IST is on hold; exiting before paid APIs\n"
        with mock.patch.object(auto_agent, "run_once", return_value=(0, out)) as run_once, \
             mock.patch.object(auto_agent, "board_quality", side_effect=AssertionError("qa")):
            self.assertEqual(auto_agent.main(), 0)
            self.assertEqual(run_once.call_count, 1)

    def test_scheduled_workflows_share_one_concurrency_group_and_gate_deepseek(self):
        publish = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        watch = (WF / "slot-watch.yml").read_text(encoding="utf-8")
        self.assertIn("group: paise-ki-pathshala-publish", publish)
        self.assertIn("group: paise-ki-pathshala-publish", watch)
        self.assertIn("cancel-in-progress: false", publish)
        self.assertIn("cancel-in-progress: false", watch)
        self.assertNotIn("cancel-in-progress: true", publish)
        window = publish[publish.index("DeepSeek balance"):publish.index("DeepSeek balance") + 400]
        self.assertIn("steps.guard.outputs.taken != 'true'", window)
        self.assertIn("run/credits.json", publish)


# Slots held on main. Clock is inside the 45-minute to 6-hour lead window
# for that slot, so --next would select it if the hold were ignored.
_HELD_SLOTS = (
    ("2026-10-02", "16:00", datetime(2026, 10, 2, 13, 0)),
    ("2026-10-02", "21:00", datetime(2026, 10, 2, 18, 0)),
    ("2026-10-03", "08:30", datetime(2026, 10, 3, 5, 30)),
    ("2026-10-03", "16:00", datetime(2026, 10, 3, 13, 0)),
    ("2026-10-03", "21:00", datetime(2026, 10, 3, 18, 0)),
)


class RealLedgerHoldTests(unittest.TestCase):
    def test_next_skips_every_held_slot_on_the_real_ledger(self):
        ledger_path = ROOT / "data" / "published.json"
        self.assertEqual(slot_guard.LEDGER.resolve(), ledger_path.resolve())
        entries = json.loads(ledger_path.read_text(encoding="utf-8"))
        holds = [
            e for e in entries
            if isinstance(e, dict) and str(e.get("platform") or "").strip().lower() == "hold"
        ]
        for date, slot, _when in _HELD_SLOTS:
            match = [
                e for e in holds
                if e.get("date_ist") == date and e.get("slot") == slot and e.get("video_id") is None
            ]
            self.assertEqual(len(match), 1, (date, slot))

        posts = mock.Mock(side_effect=AssertionError("paid post"))
        gets = mock.Mock(side_effect=AssertionError("paid get"))
        claude = mock.Mock(side_effect=AssertionError("deepseek"))
        for date, slot, naive in _HELD_SLOTS:
            now = naive.replace(tzinfo=slot_guard.IST)
            with self.subTest(slot=f"{date} {slot}"):
                self.assertIsNone(slot_guard.next_unfilled(now, entries))
                self.assertIsNone(slot_guard.next_unfilled(now, holds))
                out = io.StringIO()
                err = io.StringIO()
                with mock.patch.object(slot_guard, "_now", return_value=now), \
                     mock.patch.object(common, "claude", claude), \
                     mock.patch("requests.post", posts), \
                     mock.patch("requests.get", gets), \
                     redirect_stdout(out), redirect_stderr(err):
                    slot_guard.main(["--next"])
                text = out.getvalue()
                self.assertIn("taken=true", text)
                self.assertNotIn(f"slot={slot}", text)
                self.assertNotIn(f"date_ist={date}", text)
                self.assertIn("no unfilled slot", err.getvalue())

                env = {
                    "PUBLISH_VISIBILITY": "public",
                    "PUBLISH_SLOT_IST": slot,
                    "PUBLISH_DATE_IST": date,
                }
                with tempfile.TemporaryDirectory() as tmp:
                    credits = Path(tmp) / "credits.json"
                    log = io.StringIO()
                    with mock.patch.dict(os.environ, env), \
                         mock.patch.object(credit_log, "PATH", credits), \
                         mock.patch.object(run_pipeline, "run", side_effect=AssertionError("stage")), \
                         mock.patch.object(common, "claude", claude), \
                         mock.patch("requests.post", posts), \
                         mock.patch("requests.get", gets), \
                         redirect_stdout(log):
                        with self.assertRaises(SystemExit) as caught:
                            run_pipeline.main()
                    self.assertEqual(caught.exception.code, 0)
                    skipped = log.getvalue()
                    self.assertIn(date, skipped)
                    self.assertIn(slot, skipped)
                    self.assertIn("before paid", skipped)
                    self.assertIn("deepseek_calls=0", skipped)
                    self.assertIn("elevenlabs_chars=0", skipped)
                    saved = json.loads(credits.read_text(encoding="utf-8"))
                    self.assertEqual(saved["deepseek_calls"], 0)
                    self.assertEqual(saved["elevenlabs_characters_sent"], 0)
        posts.assert_not_called()
        gets.assert_not_called()
        claude.assert_not_called()


class DeepSeekCapTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "credits.json"
        self._patch = mock.patch.object(credit_log, "PATH", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._dir.cleanup()

    def test_timeouts_stay_75s_and_240s(self):
        self.assertEqual(common.LLM_ATTEMPT_TIMEOUT, 75)
        self.assertEqual(common.LLM_TOTAL_BUDGET, 240)
        self.assertEqual(credit_log.DEFAULT_DEEPSEEK_CALLS, 4)

    def test_fifth_call_is_not_sent(self):
        calls = {"n": 0}

        def create(**_kwargs):
            calls["n"] += 1
            return _completion()

        env = {"DEEPSEEK_API_KEY": "sk-test"}
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("common.openai.OpenAI", return_value=_client(create)), \
             mock.patch("common.time.sleep"), \
             redirect_stderr(err):
            os.environ.pop("DEEPSEEK_MAX_CALLS", None)
            for _ in range(4):
                self.assertEqual(common.claude("system", "user", model="deepseek-flash", retries=1), "ok")
            with self.assertRaises(SystemExit):
                common.claude("system", "user", model="deepseek-flash", retries=1)
        self.assertEqual(calls["n"], 4)
        self.assertIn("skipping the slot", err.getvalue())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["deepseek_calls"], 4)
        self.assertEqual(data["deepseek_prompt_tokens"], 12)
        self.assertEqual(data["deepseek_completion_tokens"], 4)

    def test_retries_count_toward_the_cap(self):
        calls = {"n": 0}

        def create(**_kwargs):
            calls["n"] += 1
            raise TimeoutError("no response in 75s")

        err = io.StringIO()
        with mock.patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_MAX_CALLS": "2",
        }, clear=False), \
             mock.patch("common.openai.OpenAI", return_value=_client(create)), \
             mock.patch("common.time.sleep"), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit):
                common.claude("system", "user", model="deepseek-flash", retries=3)
        self.assertEqual(calls["n"], 2)
        self.assertLess(calls["n"], 3)
        self.assertIn("skipping the slot", err.getvalue())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["deepseek_calls"], 2)


class PreviewVoiceTests(unittest.TestCase):
    def _speak(self, env, post_status=200):
        posts = []
        gets = []

        def post(_url, **_kwargs):
            posts.append(_url)
            return _Resp(post_status, b"E" * 2500, headers={"character-cost": "12"})

        def get(_url, **_kwargs):
            gets.append(_url)
            return _Resp(200, {"character_count": 0, "character_limit": 100000, "status": "active"})

        async def edge(_text, voice, rate, path):
            edge.calls.append((voice, rate))
            path.write_bytes(b"e" * 2000)

        edge.calls = []
        spoken = {beat: "ek do teen" for beat in board_short.BEATS}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("requests.post", post), \
             mock.patch("requests.get", get), \
             mock.patch.object(board_short, "_edge", edge), \
             redirect_stdout(io.StringIO()):
            os.environ.pop("ELEVENLABS_OPT_IN", None)
            os.environ.pop("DRY_RUN", None)
            os.environ.pop("PIPELINE_MODE", None)
            os.environ.pop("RENDER_SAMPLE", None)
            for key, value in env.items():
                os.environ[key] = value
            engine = board_short.synthesize_short_voices(spoken, VOICE)
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
            credits = json.loads((Path(tmp) / "credits.json").read_text(encoding="utf-8"))
        return engine, meta, credits, posts, gets, edge.calls

    def test_preview_sample_and_dry_run_use_madhur_and_skip_elevenlabs(self):
        cases = [
            {"ELEVENLABS_API_KEY": "test-key", "PUBLISH_VISIBILITY": "unlisted"},
            {"ELEVENLABS_API_KEY": "test-key", "PUBLISH_VISIBILITY": ""},
            {"ELEVENLABS_API_KEY": "test-key", "PUBLISH_VISIBILITY": "public", "DRY_RUN": "1"},
            {"ELEVENLABS_API_KEY": "test-key", "PUBLISH_VISIBILITY": "public", "PIPELINE_MODE": "preview"},
            {"ELEVENLABS_API_KEY": "test-key", "RENDER_SAMPLE": "1", "PUBLISH_VISIBILITY": "public"},
        ]
        for env in cases:
            with self.subTest(env=env):
                engine, meta, credits, posts, gets, calls = self._speak(env)
                self.assertEqual(posts, [])
                self.assertEqual(gets, [])
                self.assertEqual(engine, "edge")
                self.assertEqual(meta["engine"], "edge")
                self.assertFalse(meta["elevenlabs_attempted"])
                self.assertEqual(calls[0], (eleven_voice.PREVIEW_EDGE_VOICE, eleven_voice.PREVIEW_EDGE_RATE))
                self.assertEqual(calls[0][0], "hi-IN-MadhurNeural")
                self.assertEqual(calls[0][1], "+0%")
                self.assertEqual(credits["elevenlabs_characters_sent"], 0)
                self.assertEqual(credits["tts_engine"], "edge")

    def test_public_publish_and_explicit_opt_in_may_call_elevenlabs(self):
        for env in (
            {"ELEVENLABS_API_KEY": "test-key", "PUBLISH_VISIBILITY": "public"},
            {"ELEVENLABS_API_KEY": "test-key", "PUBLISH_VISIBILITY": "unlisted", "ELEVENLABS_OPT_IN": "1"},
        ):
            with self.subTest(env=env):
                engine, meta, credits, posts, gets, calls = self._speak(env)
                self.assertEqual(engine, "elevenlabs")
                self.assertGreaterEqual(len(posts), 1)
                self.assertEqual(calls, [])
                self.assertTrue(meta["elevenlabs_attempted"])
                self.assertGreater(credits["elevenlabs_characters_sent"], 0)
                self.assertEqual(credits["elevenlabs_characters_remaining"], 100000)
                self.assertEqual(credits["tts_engine"], "elevenlabs")


class CreditLogTests(unittest.TestCase):
    def test_summary_and_ledger_row_carry_the_credit_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credits.json"
            published = Path(tmp) / "published.json"
            published.write_text("[]\n", encoding="utf-8")
            with mock.patch.object(credit_log, "PATH", path), \
                 mock.patch.object(ledger, "PATH", published), \
                 mock.patch.object(ledger, "ROOT", Path(tmp)):
                credit_log.reserve_deepseek_call()
                credit_log.add_deepseek_usage(_completion(prompt=11, completion=7))
                credit_log.add_eleven_chars(80)
                credit_log.set_eleven_remaining(10700)
                credit_log.set_tts_engine("edge")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    line = credit_log.write_summary()
                entry_credits = credit_log.ledger_fields()
                ledger.record_publish("abc123", "2026-10-02T15:30:00Z", "21:00", credits=entry_credits)
                saved = json.loads(path.read_text(encoding="utf-8"))
                entry = json.loads(published.read_text(encoding="utf-8"))[-1]
        self.assertEqual(saved["deepseek_calls"], 1)
        self.assertEqual(saved["deepseek_prompt_tokens"], 11)
        self.assertEqual(saved["deepseek_completion_tokens"], 7)
        self.assertEqual(saved["elevenlabs_characters_sent"], 80)
        self.assertEqual(saved["elevenlabs_characters_remaining"], 10700)
        self.assertEqual(saved["tts_engine"], "edge")
        self.assertIn("deepseek_calls=1", line)
        self.assertIn("prompt_tokens=11", line)
        self.assertIn("completion_tokens=7", line)
        self.assertIn("elevenlabs_chars=80", line)
        self.assertIn("elevenlabs_remaining=10700", line)
        self.assertIn("tts_engine=edge", line)
        self.assertEqual(entry["video_id"], "abc123")
        self.assertEqual(entry["deepseek_calls"], 1)
        self.assertEqual(entry["deepseek_prompt_tokens"], 11)
        self.assertEqual(entry["deepseek_completion_tokens"], 7)
        self.assertEqual(entry["elevenlabs_characters_sent"], 80)
        self.assertEqual(entry["elevenlabs_characters_remaining"], 10700)
        self.assertEqual(entry["tts_engine"], "edge")


if __name__ == "__main__":
    unittest.main()
