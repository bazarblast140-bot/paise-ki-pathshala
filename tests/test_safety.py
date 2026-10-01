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
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict("os.environ", env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt:
                dt.now.return_value = now
                slot_watch.main()
            self.assertIn("missing=false", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
