"""Reuse a dry-run script, retimecards to the paid voice, and keep soft misses as REVIEW."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import approved_script
import board_short
import narration_dry_run
import self_review

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

    def test_committed_hinglish_replaces_the_dry_run_before_the_hash_lock(self):
        import slot_guard
        approved = json.loads(
            (ROOT / "approved" / "risk_daily_loss_limit.json").read_text(encoding="utf-8")
        )
        total = sum(len(approved["beats"][beat]) for beat in ORDER)
        self.assertEqual(total, 653)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tts_risk_daily_loss_limit.txt").write_text(_lines(), encoding="utf-8")
            installed = approved_script.install_repo_beats(root, "risk_daily_loss_limit")
            self.assertEqual(installed["title"], "डेली लॉस लिमिट")
        out = io.StringIO()
        with redirect_stdout(out):
            # Far-future date so this never goes stale
            slot_guard.emit_publish_at("16:00", "2026-12-31")
        lines = dict(part.split("=", 1) for part in out.getvalue().splitlines())
        self.assertEqual(lines["slot"], "16:00")
        self.assertEqual(lines["date_ist"], "2026-12-31")
        self.assertEqual(lines["publish_at"], "2026-12-31T10:30:00Z")
        with self.assertRaises(SystemExit):
            slot_guard.emit_publish_at("16:00", "2020-01-01")

    def test_window_sync_and_craft_are_review_and_beat_recall_stays_hold(self):
        beats = [{"beat": beat, "recall": 0.65} for beat in ORDER]
        window = {
            "ok": False,
            "reason": "fact-pehchan 14.0-17.8s narration does not match the script (recall 0.33)",
            "beats": beats,
        }
        env = {"NARRATION_FROM_RUN": "37095205924"}
        with mock.patch.dict(os.environ, env, clear=False):
            soft = board_short.soften_approved_holds(_hold(), window)
            self.assertEqual(soft["verdict"], "REVIEW")
            self.assertTrue(soft["review_flag"])


if __name__ == "__main__":
    unittest.main()
