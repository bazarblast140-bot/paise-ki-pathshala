"""A topic HELD by final verify must not be picked again (pat_bear_flag loop)."""
import os
import unittest
from unittest import mock

import approved_bank
import run_pipeline


def _s(key):
    return {"key": key, "_file": f"{key}.json", "owner_approved": True}


class HeldApprovedScriptTest(unittest.TestCase):
    def test_held_script_falls_through_to_next(self):
        scripts = [_s("pat_bear_flag"), _s("pat_double_bottom")]
        self.assertEqual(approved_bank.select(scripts, [])["key"], "pat_bear_flag")
        got = approved_bank.select(scripts, [], skips=["pat_bear_flag"])
        self.assertEqual(got["key"], "pat_double_bottom")

    def test_next_script_reads_gate_skips(self):
        scripts = [_s("pat_bear_flag"), _s("pat_double_bottom")]
        with mock.patch.object(approved_bank, "all_scripts", return_value=scripts), \
             mock.patch.object(approved_bank, "_gate_skips", return_value=["pat_bear_flag"]):
            self.assertEqual(approved_bank.next_script([])["key"], "pat_double_bottom")

    def test_all_held_returns_none_and_reason(self):
        scripts = [_s("pat_bear_flag")]
        self.assertIsNone(approved_bank.select(scripts, [], skips=["pat_bear_flag"]))
        reason = approved_bank.skip_reason([], scripts=scripts, skips=["pat_bear_flag"])
        self.assertIn("held by final verify", reason)

    def test_held_matches_file_stem(self):
        script = {"key": "other", "_file": "pat_bear_flag.json", "owner_approved": True}
        self.assertTrue(approved_bank.held(script, ["pat_bear_flag"]))
        self.assertFalse(approved_bank.held(script, []))


class RememberSkipStageTest(unittest.TestCase):
    def test_approved_bank_only_retires_on_hold_stages(self):
        with mock.patch.dict(os.environ, {"APPROVED_BANK": "1"}):
            self.assertTrue(run_pipeline._should_remember_skip("Final verify"))
            self.assertTrue(run_pipeline._should_remember_skip("Pre-publish Quality Gate"))
            self.assertFalse(run_pipeline._should_remember_skip("Chart Board"))
            self.assertFalse(run_pipeline._should_remember_skip("Preflight"))

    def test_topic_bank_keeps_old_behaviour(self):
        with mock.patch.dict(os.environ, {"APPROVED_BANK": ""}):
            self.assertTrue(run_pipeline._should_remember_skip("Chart Board"))


class WorkflowFailStepTest(unittest.TestCase):
    def test_scheduled_slot_is_not_reported_empty(self):
        from pathlib import Path
        wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        step = wf.split("Fail loudly if a scheduled slot built nothing", 1)[1]
        self.assertIn('"$BUFFER_REASON" = "scheduled"', step.split("built nothing::", 1)[0])


if __name__ == "__main__":
    unittest.main()
