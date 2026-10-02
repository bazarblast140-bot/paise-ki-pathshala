"""The narration dry run stops before TTS and does not mark topics used."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import board_short
import credit_log
import narration_dry_run

ROOT = Path(__file__).resolve().parents[1]


def _topic(key, used=False):
    return {
        "id": 800 + len(key),
        "used": False,
        "used_short": used,
        "key": key,
        "cat": "BASICS",
        "topic": f"नियम {key} समझिए",
        "thumb": ["नियम", "क्या गलत है"],
        "sticker": "नियम",
        "beats": {
            "hook": ["सवाल", "क्या नियम गलत है"],
            "pehchan": ["पहचान", "ऊपर छोटी बॉडी"],
            "matlab": ["मतलब", "नीचे लंबी रेखा"],
            "karein": ["करें", "अगला कदम देखो"],
        },
        "facts": ["नियम एक कदम है"],
        "seo": key,
    }


def _script():
    targets = board_short.live_beat_targets(1.9)
    beats = {}
    for beat, target in targets.items():
        lead = "क्या यह सेटअप गलत है?" if beat == "hook" else ""
        parts = lead.split() + ["नियम"] * target
        beats[beat] = " ".join(parts[:target])
    return {
        "title": "सेटअप का नियम समझिए",
        "hook_line": "क्या यह सेटअप गलत है?",
        "beats": beats,
        "source": "llm",
    }


class DryRunTests(unittest.TestCase):
    def test_select_upcoming_does_not_mark_the_bank(self):
        bank = {"topics": [_topic("one"), _topic("two"), _topic("three"), _topic("four", used=True)]}
        raw = json.dumps(bank)
        picked = narration_dry_run.select_upcoming(3, bank)
        self.assertEqual([item["key"] for item in picked], ["one", "two", "three"])
        self.assertFalse(any(item.get("used_short") for item in picked))
        self.assertEqual(json.dumps(bank), raw)

    def test_passing_topic_reports_windows_and_does_not_call_tts(self):
        topic = _topic("hammer_rule")
        script = _script()
        seen = []

        def narrate(live, _c):
            self.assertTrue(live["_live_short"])
            return script, "llm"

        def synthesize(*_args, **_kwargs):
            seen.append("tts")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "narration", side_effect=narrate), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize):
            passed, report, _narration = narration_dry_run.evaluate_topic(
                topic, {"voice": {"words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"}})
        self.assertTrue(passed, report)
        self.assertIn("topic hammer_rule", report)
        self.assertIn("hook ", report)
        self.assertIn("total ", report)
        self.assertIn("estimated ", report)
        self.assertIn("deepseek_calls ", report)
        self.assertIn("PASS", report)
        self.assertEqual(seen, [])

    def test_blocked_topic_fails_and_main_leaves_the_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            bank = {"topics": [_topic("alpha"), _topic("beta"), _topic("gamma")]}
            path = root / "topic_bank.json"
            path.write_text(json.dumps(bank), encoding="utf-8")
            before = path.read_bytes()
            saved = {
                key: os.environ.get(key)
                for key in ("VIDEO_FORMAT", "DRY_RUN", "PUBLISH_VISIBILITY", "ELEVENLABS_OPT_IN")
            }

            def narrate(_topic, _c):
                raise SystemExit("hook beat 28 words (13-23 chahiye)")

            try:
                with mock.patch.object(narration_dry_run, "ROOT", root), \
                     mock.patch.object(board_short, "RUN", root / "run"), \
                     mock.patch.object(credit_log, "PATH", root / "credits.json"), \
                     mock.patch.object(board_short, "narration", side_effect=narrate), \
                     mock.patch.object(board_short, "synthesize_short_voices", side_effect=AssertionError("no TTS")), \
                     mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "should-be-removed"}, clear=False):
                    (root / "run").mkdir()
                    code = narration_dry_run.main(["--topics", "3"])
                    self.assertNotIn("ELEVENLABS_API_KEY", os.environ)
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual(code, 1)
            self.assertEqual(path.read_bytes(), before)

    def test_workflow_is_dispatch_only_and_has_no_elevenlabs(self):
        text = (ROOT / ".github" / "workflows" / "narration-dry-run.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", text)
        self.assertIn("topic_keys:", text)
        self.assertIn("topics:", text)
        self.assertIn("default: 3", text)
        self.assertIn("--topic-keys", text)
        self.assertIn("DEEPSEEK_API_KEY", text)
        self.assertIn("narration_dry_run.py", text)
        self.assertIn("beats:", text)
        self.assertIn("full:", text)
        self.assertIn("--full", text)
        self.assertIn("run/final_*.mp4", text)
        self.assertIn("run/tts_*.txt", text)
        self.assertIn("always()", text)
        self.assertIn("upload-artifact", text)
        self.assertNotIn("ELEVENLABS_API_KEY", text)
        self.assertNotIn("git commit", text)
        self.assertNotIn("git push", text)
        self.assertIn("contents: read", text)

    def test_topic_keys_pick_named_topics_without_writing_the_bank(self):
        bank = {
            "topics": [
                _topic("alpha", used=True),
                _topic("risk_journal"),
                _topic("gamma"),
            ]
        }
        raw = json.dumps(bank)
        picked, missing = narration_dry_run.select_by_keys(
            ["risk_journal", "alpha", "missing-key"], bank)
        self.assertEqual([item["key"] for item in picked], ["risk_journal", "alpha"])
        self.assertTrue(picked[1]["used_short"])
        self.assertEqual(missing, ["missing-key"])
        self.assertEqual(json.dumps(bank), raw)

    def test_unknown_topic_keys_exit_before_narration(self):
        saved = {
            key: os.environ.get(key)
            for key in ("VIDEO_FORMAT", "DRY_RUN", "PUBLISH_VISIBILITY", "ELEVENLABS_OPT_IN", "ELEVENLABS_API_KEY")
        }
        bank_path = ROOT / "topic_bank.json"
        before = bank_path.read_bytes()
        try:
            with mock.patch.object(
                narration_dry_run, "evaluate_topic",
                side_effect=AssertionError("unknown keys must not narrate"),
            ):
                code = narration_dry_run.main(["--topic-keys", "not-a-real-topic"])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(code, 1)
        self.assertEqual(bank_path.read_bytes(), before)

    def test_named_topic_keys_are_narrated_and_the_bank_stays(self):
        saved = {
            key: os.environ.get(key)
            for key in ("VIDEO_FORMAT", "DRY_RUN", "PUBLISH_VISIBILITY", "ELEVENLABS_OPT_IN", "ELEVENLABS_API_KEY")
        }
        bank_path = ROOT / "topic_bank.json"
        before = bank_path.read_bytes()
        seen = []

        def evaluate(topic, _c, beats=None):
            seen.append(topic.get("key"))
            return True, f"topic {topic.get('key')}\nPASS", {}

        try:
            with mock.patch.object(narration_dry_run, "evaluate_topic", side_effect=evaluate):
                code = narration_dry_run.main(["--topic-keys", "risk_journal, intraday_risk_reward"])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["risk_journal", "intraday_risk_reward"])
        self.assertEqual(bank_path.read_bytes(), before)

    def test_full_edge_dry_run_uses_four_beats_and_mocks_tts(self):
        saved = {
            key: os.environ.get(key)
            for key in (
                "VIDEO_FORMAT", "DRY_RUN", "PUBLISH_VISIBILITY", "ELEVENLABS_OPT_IN",
                "ELEVENLABS_API_KEY", "LIVE_BEATS",
            )
        }
        bank_path = ROOT / "topic_bank.json"
        before = bank_path.read_bytes()
        targets = board_short.four_live_targets(1.9)
        beats = {}
        for beat, target in targets.items():
            lead = "क्या यह सेटअप गलत है?" if beat == "hook" else ""
            beats[beat] = " ".join((lead.split() + ["नियम"] * target)[:target])
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": "क्या यह सेटअप गलत है?",
            "beats": beats,
            "source": "llm",
        }
        seen = {}

        def narrate(live, _c, spec=None):
            seen["beats"] = live.get("_live_beats")
            return script, "llm"

        def produce(topic, narration, _c, _spec, source="llm", beat_pngs=None, free="silence"):
            seen["produce"] = topic.get("_live_beats")
            seen["source"] = source
            seen["free"] = free
            self.assertNotIn("ELEVENLABS_API_KEY", os.environ)
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")
            (board_short.RUN / "tts_text.txt").write_text(narration["beats"]["hook"], encoding="utf-8")
            (board_short.RUN / "self_review.json").write_text(json.dumps({
                "public_ok": True,
                "overall": 96.7,
                "parts": {
                    "hook": 100, "visual": 90, "accuracy": 100,
                    "trend": 100, "craft": 100, "sync": 100,
                },
                "summary": "overall 96.7 (craft 100)",
            }), encoding="utf-8")
            return 55.0

        out = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "should-be-removed"}, clear=False), \
                 mock.patch.object(board_short, "RUN", Path(tmp)), \
                 mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
                 mock.patch.object(board_short, "narration", side_effect=narrate), \
                 mock.patch.object(board_short, "prove_full_render", side_effect=produce), \
                 mock.patch.object(board_short, "synthesize_short_voices", side_effect=AssertionError("TTS stays behind prove_full_render")), \
                 redirect_stdout(out):
                (Path(tmp)).mkdir(exist_ok=True)
                code = narration_dry_run.main([
                    "--topic-keys", "risk_journal", "--beats", "four", "--full",
                ])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(code, 0)
        self.assertEqual(seen["beats"], "four")
        self.assertEqual(seen["produce"], "four")
        self.assertEqual(seen["free"], "edge")
        self.assertIn("verdict PASS", out.getvalue())
        self.assertIn("craft 100", out.getvalue())
        self.assertEqual(bank_path.read_bytes(), before)

    def test_one_topic_crash_does_not_stop_the_next(self):
        saved = {
            key: os.environ.get(key)
            for key in (
                "VIDEO_FORMAT", "DRY_RUN", "PUBLISH_VISIBILITY", "ELEVENLABS_OPT_IN",
                "ELEVENLABS_API_KEY", "LIVE_BEATS",
            )
        }
        bank_path = ROOT / "topic_bank.json"
        before = bank_path.read_bytes()
        targets = board_short.four_live_targets(1.9)
        beats = {}
        for beat, target in targets.items():
            lead = "क्या यह सेटअप गलत है?" if beat == "hook" else ""
            beats[beat] = " ".join((lead.split() + ["नियम"] * target)[:target])
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": "क्या यह सेटअप गलत है?",
            "beats": beats,
            "source": "llm",
        }
        seen = []

        def narrate(_live, _c, spec=None):
            return script, "llm"

        def produce(topic, _narration, _c, _spec, source="llm", beat_pngs=None, free="silence"):
            seen.append(topic.get("key"))
            if topic.get("key") == "risk_journal":
                raise RuntimeError("plain image has no plate")
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")
            (board_short.RUN / "tts_text.txt").write_text("voice", encoding="utf-8")
            (board_short.RUN / "self_review.json").write_text(json.dumps({
                "public_ok": True,
                "overall": 96.7,
                "parts": {"hook": 100, "visual": 90, "accuracy": 100, "trend": 100, "craft": 100, "sync": 100},
                "summary": "overall 96.7",
            }), encoding="utf-8")

        try:
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(board_short, "RUN", Path(tmp)), \
                 mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
                 mock.patch.object(board_short, "narration", side_effect=narrate), \
                 mock.patch.object(board_short, "prove_full_render", side_effect=produce):
                code = narration_dry_run.main([
                    "--topic-keys", "risk_journal,intraday_risk_reward",
                    "--beats", "four", "--full",
                ])
                self.assertTrue((Path(tmp) / "final_intraday_risk_reward.mp4").is_file())
                self.assertTrue((Path(tmp) / "tts_intraday_risk_reward.txt").is_file())
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(code, 1)
        self.assertEqual(seen, ["risk_journal", "intraday_risk_reward"])
        self.assertEqual(bank_path.read_bytes(), before)

    def test_clear_topic_voice_drops_the_previous_beat_audio(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)):
            run = Path(tmp)
            (run / "tts_hook.mp3").write_bytes(b"old-voice")
            (run / "tts_hook.engine").write_text("edge", encoding="utf-8")
            (run / "tts_hook.textid").write_text("abc", encoding="utf-8")
            (run / "tts_text.txt").write_text("previous topic", encoding="utf-8")
            (run / "final_keep.mp4").write_bytes(b"mp4")
            narration_dry_run.clear_topic_voice()
            self.assertFalse((run / "tts_hook.mp3").exists())
            self.assertFalse((run / "tts_hook.engine").exists())
            self.assertFalse((run / "tts_hook.textid").exists())
            self.assertFalse((run / "tts_text.txt").exists())
            self.assertTrue((run / "final_keep.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
