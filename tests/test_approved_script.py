"""Reuse a dry-run script, retimecards to the paid voice, and keep soft misses as REVIEW."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import approved_script
import board_short
import common
import credit_log
import narration_dry_run
import run_verify
import scene_frames
import scene_pace
import self_review
import speak

ROOT = Path(__file__).resolve().parent.parent
ORDER = ("hook", "pehchan", "matlab", "karein")


def _lines():
    return "\n".join(
        f"tts_{beat}: {beat} की तय लाइन {index}।"
        for index, beat in enumerate(ORDER, start=1)
    ) + "\n"


def _topic():
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    topic = json.loads(json.dumps(next(item for item in bank if item["key"] == "risk_daily_loss_limit")))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    return topic


def _hold(**parts):
    base = {
        "hook": 100, "visual": 90, "accuracy": 100,
        "trend": 45, "craft": 0, "sync": 0,
    }
    base.update(parts)
    packed = self_review._pack(base, ["sync window"])
    packed["verdict"] = "HOLD"
    packed["review_flag"] = False
    packed["public_ok"] = False
    return packed


class ApprovedScriptTests(unittest.TestCase):
    def tearDown(self):
        approved_script._LOCKED = {}

    def test_tts_lines_rebuild_beats_without_json_or_a_new_script(self):
        text = _lines()
        beats = approved_script.beats_from_tts_text(text)
        self.assertEqual(list(beats), list(ORDER))
        self.assertEqual(beats["hook"], "hook की तय लाइन 1।")
        self.assertEqual(beats["karein"], "karein की तय लाइन 4।")
        blob = " ".join(f"शब्द{i}" for i in range(105))
        split = approved_script.split_spoken_blob(blob)
        self.assertEqual(
            [len(split[beat].split()) for beat in ORDER],
            [18, 26, 32, 29],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tts_risk_daily_loss_limit.txt").write_text(text, encoding="utf-8")
            (root / "tts_text.txt").write_text("ignored blob", encoding="utf-8")
            payload = approved_script.load_payload(root, "risk_daily_loss_limit")
            self.assertNotIn("title", payload)
            script = approved_script.narration_from_payload(payload, _topic(), "37095205924")
            self.assertEqual(script["beats"], beats)
            self.assertEqual(script["source"], "approved")
            self.assertEqual(script["from_run"], "37095205924")
            saved = {
                "title": "दिन की हद",
                "hook_line": "क्यों रुकें?",
                "beats": {beat: f"json {beat}" for beat in ORDER},
                "source": "llm",
            }
            (root / "narration_risk_daily_loss_limit.json").write_text(
                json.dumps(saved), encoding="utf-8",
            )
            preferred = approved_script.load_payload(root, "risk_daily_loss_limit")
            self.assertEqual(preferred["beats"]["matlab"], "json matlab")

    def test_narration_skips_deepseek_and_trim(self):
        topic = _topic()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tts_risk_daily_loss_limit.txt").write_text(_lines(), encoding="utf-8")
            env = {
                "NARRATION_FROM_RUN": "37095205924",
                "APPROVED_SCRIPT_DIR": str(root),
            }
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(board_short, "RUN", root / "run"), \
                 mock.patch.object(board_short, "claude_json", side_effect=AssertionError("no DeepSeek")), \
                 mock.patch.object(board_short, "apply_live_draft", side_effect=AssertionError("no trim")), \
                 mock.patch.object(board_short, "fit_estimated_duration", side_effect=AssertionError("no trim")):
                (root / "run").mkdir()
                script, source = board_short.narration(topic, {"voice": {}, "models": {}})
            self.assertEqual(source, "approved")
            self.assertEqual(script["beats"]["pehchan"], "pehchan की तय लाइन 2।")
            self.assertFalse(script["narration_fallback"])
            self.assertEqual(board_short.upload_block_reason(source), "")
            board_short.require_llm_narration(script, source)

    def test_prepare_spoken_does_not_trim_an_approved_script(self):
        topic = _topic()
        topic["_approved_script"] = True
        beats = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        narration = {"title": "दिन की हद", "hook_line": "क्यों?", "beats": dict(beats), "source": "approved"}
        spoken = dict(beats)

        def synthesize(words, _c, beats=None):
            self.assertEqual({beat: words[beat] for beat in beats}, spoken)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short, "fit_estimated_duration", side_effect=AssertionError("no trim")), \
             mock.patch.object(board_short, "apply_live_draft", side_effect=AssertionError("no trim")), \
             mock.patch.object(board_short, "synthesize_short_voices", side_effect=synthesize), \
             mock.patch.object(board_short, "_trim_rendered", return_value={beat: 12.0 for beat in ORDER}):
            out_spoken, out_narration, *_rest = board_short.prepare_spoken(
                spoken, narration, topic, {"voice": {}}, {},
            )
        self.assertEqual(out_spoken, spoken)
        self.assertEqual(out_narration["beats"], beats)

    def test_dry_run_saves_narration_json_beside_the_tts_text(self):
        narration = {"title": "दिन की हद", "beats": {beat: beat for beat in ORDER}, "source": "llm"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)):
            run = Path(tmp)
            (run / "final.mp4").write_bytes(b"mp4")
            (run / "tts_text.txt").write_text(_lines(), encoding="utf-8")
            narration_dry_run._keep_topic_artifacts("risk_daily_loss_limit", narration)
            saved = json.loads((run / "narration_risk_daily_loss_limit.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["beats"]["hook"], "hook")
            self.assertTrue((run / "tts_risk_daily_loss_limit.txt").is_file())
        dry = (ROOT / ".github" / "workflows" / "narration-dry-run.yml").read_text(encoding="utf-8")
        self.assertIn("run/narration_*.json", dry)
        self.assertIn("run/tts_*.txt", dry)
        live = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("narration_from_run:", live)
        self.assertIn("actions: read", live)
        self.assertIn("approved_script.py --download", live)
        self.assertIn("NARRATION_FROM_RUN:", live)
        self.assertIn("render_only:", live)
        self.assertIn("run/sc_*.png", live)
        self.assertIn("install_repo_beats", live)
        self.assertIn("ypnkIsDASPgHZuanrF0q", live)
        publish = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        self.assertIn("PUBLISH_SLOT_IST:", publish)
        self.assertIn("PUBLISH_AT_UTC:", publish)
        self.assertIn('default: "16:00"', publish)
        self.assertIn("test -s run/thumbnail.jpg", publish)
        self.assertIn("slot_guard.py --publish-at", publish)
        self.assertIn("python publish_instagram.py", publish)

    def test_committed_hinglish_replaces_the_dry_run_before_the_hash_lock(self):
        import slot_guard
        approved = json.loads(
            (ROOT / "approved" / "risk_daily_loss_limit.json").read_text(encoding="utf-8")
        )
        total = sum(len(approved["beats"][beat]) for beat in ORDER)
        self.assertEqual(
            [len(approved["beats"][beat]) for beat in ORDER],
            [96, 182, 189, 186],
        )
        self.assertEqual(total, 653)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tts_risk_daily_loss_limit.txt").write_text(_lines(), encoding="utf-8")
            installed = approved_script.install_repo_beats(root, "risk_daily_loss_limit")
            self.assertEqual(installed["title"], "डेली लॉस लिमिट")
            self.assertEqual(installed["beats"]["pehchan"], approved["beats"]["pehchan"])
            self.assertNotIn("पूँजी", installed["beats"]["hook"])
            script = approved_script.narration_from_payload(
                approved_script.load_payload(root, "risk_daily_loss_limit"),
                _topic(),
                "37095205924",
            )
        self.assertEqual(
            script["approved_hashes"]["pehchan"],
            approved_script.beat_digest(approved["beats"]["pehchan"]),
        )
        self.assertEqual(script["beats"]["karein"], approved["beats"]["karein"])
        out = io.StringIO()
        with redirect_stdout(out):
            # Use a far-future date so the test never becomes stale when the
            # calendar moves past the previous hardcoded day.
            slot_guard.emit_publish_at("16:00", "2026-12-31")
        lines = dict(part.split("=", 1) for part in out.getvalue().splitlines())
        self.assertEqual(lines["slot"], "16:00")
        self.assertEqual(lines["date_ist"], "2026-12-31")
        self.assertEqual(lines["publish_at"], "2026-12-31T10:30:00Z")
        with self.assertRaises(SystemExit):
            slot_guard.emit_publish_at("16:00", "2020-01-01")
