"""Regression tests for the publish-safety fixes (no network, no secrets)."""
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


def on_block(text):
    """Top-level `on:` block of a workflow file (plain text, no YAML dependency)."""
    m = re.search(r"^on:\n(.*?)(?=^\S)", text, re.M | re.S)
    return m.group(1) if m else ""


class WorkflowTriggerTests(unittest.TestCase):
    def test_production_workflow_has_no_push_trigger(self):
        on = on_block((WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8"))
        self.assertTrue(on, "on: block not found")
        self.assertNotRegex(on, r"(?m)^  push:", "a push must never start a public upload")

    def test_reel_only_for_public_runs(self):
        text = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.strip().startswith("IG_CROSSPOST:"))
        self.assertIn("steps.fmt.outputs.visibility == 'public'", line)

    def test_repair_watchdog_is_manual_only(self):
        on = on_block((WF / "autonomous-repair-watchdog.yml").read_text(encoding="utf-8"))
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn("workflow_run", on)

    def test_slot_crons_build_ahead_on_odd_minutes(self):
        import slot_guard
        publish = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        watch = (WF / "slot-watch.yml").read_text(encoding="utf-8")
        health = (WF / "health-check.yml").read_text(encoding="utf-8")
        for cron, _slot in slot_guard.PUBLISH_CRONS:
            self.assertIn(f'"{cron}"', publish)
            self.assertNotIn(int(cron.split()[0]), (0, 30))
        for cron, _slot in slot_guard.WATCH_CRONS:
            self.assertIn(f'"{cron}"', watch)
            self.assertNotIn(int(cron.split()[0]), (0, 30))
        for cron, _check, _slot, _lead in slot_guard.HEALTH_SCHEDULE:
            self.assertIn(f'"{cron}"', health)
            self.assertNotIn(int(cron.split()[0]), (0, 30))
        for old in ('"0 3 * * *"', '"30 10 * * *"', '"30 15 * * *"'):
            self.assertNotIn(old, publish)
        self.assertIn("group: paise-ki-pathshala-publish", publish)
        repair = (WF / "instagram-repair.yml").read_text(encoding="utf-8")
        self.assertIn("group: paise-ki-pathshala-publish", repair)
        self.assertIn("group: paise-ki-pathshala-publish", watch)
        self.assertIn("cancel-in-progress: false", publish)
        self.assertIn("cancel-in-progress: false", repair)
        self.assertIn("cancel-in-progress: false", watch)
        self.assertNotIn("cancel-in-progress: true", publish)
        self.assertIn("run/verify_meta.json", publish)
        self.assertIn("run/self_review.json", publish)
        self.assertNotIn("privacy: now", watch)
        self.assertIn("privacy: public", watch)

    def test_no_one_shot_patch_workflows_left(self):
        for name in ("fix-finance-card", "fix-topic-bank-once", "fix-tts-retry",
                     "restore-board-short-duration", "restore-board-short", "restore-duration-board"):
            self.assertFalse((WF / f"{name}.yml").exists(), name)


class SelfRepairGateTests(unittest.TestCase):
    def test_self_repair_off_by_default(self):
        import auto_agent
        fake = types.ModuleType("self_repair")
        fake.propose_and_apply = mock.Mock(side_effect=AssertionError("self-repair must be off"))
        fake.commit_repair = mock.Mock(side_effect=AssertionError("must not commit"))
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(auto_agent, "RUN", Path(d)), \
             mock.patch.object(auto_agent, "run_once", return_value=(1, "Traceback: boom")), \
             mock.patch.object(auto_agent, "retry_same_topic", side_effect=AssertionError("no retry")), \
             mock.patch.dict(sys.modules, {"self_repair": fake}), \
             mock.patch.dict("os.environ", {}, clear=False), \
             mock.patch.object(sys, "argv", ["auto_agent.py", "--format", "short", "--publish"]):
            import os
            os.environ.pop("SELF_REPAIR", None)
            self.assertEqual(auto_agent.main(), 1)
        fake.propose_and_apply.assert_not_called()


class SlotLedgerTests(unittest.TestCase):
    def test_instagram_only_entry_fills_slot(self):
        import ledger
        import slot_watch
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "published.json"
            path.write_text(json.dumps([{"date_ist": "2026-09-01", "slot": "08:30",
                                         "video_id": "abc", "format": "short"}]), encoding="utf-8")
            with mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(ledger, "ROOT", Path(d)):
                ledger.record_publish(None, None, "16:00", platform="instagram", date_ist="2026-10-01")
            ents = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(ents[-1]["platform"], "instagram")
            self.assertIsNone(ents[-1]["video_id"])

            out = Path(d) / "gha_out"
            env = {"SLOT_CHECK": "16:00", "GITHUB_OUTPUT": str(out)}
            now = slot_watch.datetime(2026, 10, 1, 16, 20, tzinfo=slot_watch.IST)
            buf = __import__("io").StringIO()
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict("os.environ", env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt, \
                 mock.patch("sys.stdout", buf):
                dt.now.return_value = now
                slot_watch.main()
            self.assertIn("missing=false", out.read_text(encoding="utf-8"))
            text = buf.getvalue()
            self.assertIn("YouTube video id missing", text)
            self.assertNotIn("Testing", text)
            self.assertNotIn("token expire", text.lower())

    def test_instagram_only_message_quotes_the_recorded_failure(self):
        import slot_watch
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "published.json"
            path.write_text(json.dumps([
                {"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc", "format": "short"},
                {"date_ist": "2026-10-01", "slot": "16:00", "video_id": None, "platform": "instagram",
                 "note": "Chart Board failed: fallback narration 159 words"},
            ]), encoding="utf-8")
            out = Path(d) / "gha_out"
            env = {"SLOT_CHECK": "16:00", "GITHUB_OUTPUT": str(out)}
            now = slot_watch.datetime(2026, 10, 1, 16, 20, tzinfo=slot_watch.IST)
            buf = __import__("io").StringIO()
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict("os.environ", env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt, \
                 mock.patch("sys.stdout", buf):
                dt.now.return_value = now
                slot_watch.main()
            text = buf.getvalue()
            self.assertIn("Chart Board failed: fallback narration 159 words", text)
            self.assertIn("missing=false", out.read_text(encoding="utf-8"))
            self.assertNotIn("Testing", text)


if __name__ == "__main__":
    unittest.main()
