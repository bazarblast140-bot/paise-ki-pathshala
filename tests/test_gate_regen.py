"""Hook/teaching gate failure: regenerate with the gate reasons, then move on.

The gate is unchanged. A script that still fails after the repair loop gets
NARRATION_GATE_REGENS full regenerations, each told the exact reasons. The
buffer refill then tries the next eligible topic in the same run.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import buffer_shorts
import credit_log
import slot_guard
from tests import test_buffer_shorts as tb
from tests import test_narration_repair as tn

ROOT = Path(__file__).resolve().parent.parent


class GateRegenTests(unittest.TestCase):
    def _run(self, side_effect, cap="20"):
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append(user)
            item = side_effect[min(len(calls) - 1, len(side_effect) - 1)]
            return json.loads(json.dumps(item))

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, {"DEEPSEEK_MAX_CALLS": cap}), \
             mock.patch.object(board_short, "claude_json", side_effect=fake):
            try:
                result = board_short.narration(tn._topic(), tn.CFG)
            except SystemExit as exc:
                result = exc
            block = Path(tmp) / "narration_block.json"
            block = json.loads(block.read_text(encoding="utf-8")) if block.exists() else None
        return calls, result, block

    def _bad_count(self):
        """Calls the repair loop spends on a script that never passes."""
        bad = tn._script(hook=41)
        with mock.patch.object(board_short, "NARRATION_GATE_REGENS", 0):
            calls, _r, block = self._run([bad])
        self.assertTrue(block["blocked"])
        return len(calls)

    def test_regenerates_twice_with_exact_reasons_then_blocks(self):
        base = self._bad_count()
        calls, result, block = self._run([tn._script(hook=41)])
        self.assertIsInstance(result, SystemExit)
        self.assertEqual(len(calls), base + board_short.NARRATION_GATE_REGENS)
        self.assertEqual(board_short.NARRATION_GATE_REGENS, 2)
        for user in calls[-2:]:
            self.assertIn("FAILED the hook/teaching gate", user)
            self.assertIn("Gate reasons, verbatim: hook beat 41 words", user)
        self.assertIn("hook/teaching gate", block["reason"])

    def test_a_regeneration_that_passes_is_accepted(self):
        base = self._bad_count()
        seq = [tn._script(hook=41)] * base + [tn._script()]
        calls, result, block = self._run(seq)
        self.assertIsNone(block)
        script, source = result
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), base + 1)
        # The gate did not change: the accepted script passes it as is.
        self.assertEqual(board_short.check_narration(script, tn._topic()), [])

    def test_no_regeneration_past_the_deepseek_cap(self):
        base = self._bad_count()
        bad = tn._script(hook=41)
        calls, _result, block = self._run([bad], cap=str(base))
        self.assertTrue(block["blocked"])
        # The fake does not reserve calls, so seed the counter instead.
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "c.json"), \
             mock.patch.dict(os.environ, {"DEEPSEEK_MAX_CALLS": "3"}):
            for _ in range(3):
                credit_log.reserve_deepseek_call()
            self.assertEqual(board_short._deepseek_room(), 0)

    def test_hook_hint_reaches_the_prompt(self):
        topic = tn._topic()
        topic["hook_hint"] = "Hammer कैंडल दिखे तो क्या करें?"
        text = board_short.gate_regen_instructions("hook missing title term: X", topic)
        self.assertIn("hook missing title term: X", text)
        self.assertIn(topic["hook_hint"], text)

    def test_protective_put_hint_passes_the_unchanged_hook_gate(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "opt_protective_put")
        hint = topic["hook_hint"]
        self.assertTrue(board_short.key_phrase_for(topic["topic"]))
        self.assertEqual(board_short.title_term_notes({"title": topic["topic"], "beats": {"hook": hint}}), [])
        self.assertTrue(12 <= len(hint.split()) <= 22)


class RefillNextTopicTests(unittest.TestCase):
    def _topics(self):
        a = tb._topic()
        b = dict(tb._topic(), key="buffer_example_risk_b")
        return a, b

    def _refill(self, runner, topics, tmp):
        run = Path(tmp) / "run"
        manifest_path = Path(tmp) / "manifest.json"
        buffer_shorts.save_manifest(tb._manifest([]), manifest_path)
        store = tb.MemoryStore()
        with mock.patch.object(buffer_shorts, "RUN", run), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(buffer_shorts, "_load_bank", return_value=list(topics)), \
             mock.patch.object(buffer_shorts, "_mark_bank_used"), \
             mock.patch.object(slot_guard, "_read", return_value=[]), \
             mock.patch("board_scenes.topic_chart_block", return_value=""):
            code = buffer_shorts.refill(runner=runner, store=store, manifest_path=manifest_path)
        return code, run, buffer_shorts.load_manifest(manifest_path), store

    def test_gate_failure_moves_to_the_next_topic_and_stores_one(self):
        a, b = self._topics()
        built = []
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"

            def runner(key):
                built.append((key, os.environ.get("DEEPSEEK_MAX_CALLS")))
                run.mkdir(parents=True, exist_ok=True)
                if key == a["key"]:
                    (run / "narration_block.json").write_text(json.dumps({
                        "blocked": True,
                        "reason": "LLM narration failed the hook/teaching gate; refusing: x",
                    }), encoding="utf-8")
                    return 1
                self.assertFalse((run / "narration_block.json").exists())
                tb._write_ready_run(run, b)
                (run / "final.mp4").write_bytes(b"mp4")
                return 0

            code, _run, manifest, store = self._refill(runner, [a, b], tmp)
        self.assertEqual(code, 0)
        self.assertEqual([k for k, _ in built], [a["key"], b["key"]])
        self.assertEqual(built[0][1], str(buffer_shorts.REFILL_DEEPSEEK_CALLS))
        self.assertEqual(buffer_shorts.count_ready(manifest), 1)
        self.assertTrue(store.files)
        self.assertIn(a["key"], manifest["skipped_topics"])

    def test_other_failures_do_not_try_another_topic(self):
        a, b = self._topics()
        built = []
        with tempfile.TemporaryDirectory() as tmp:
            def runner(key):
                built.append(key)
                return 1
            code, _run, manifest, store = self._refill(runner, [a, b], tmp)
        self.assertEqual(code, 1)
        self.assertEqual(built, [a["key"]])
        self.assertEqual(store.files, {})

    def test_at_most_two_topics_per_run(self):
        a, b = self._topics()
        c = dict(tb._topic(), key="buffer_example_risk_c")
        built = []
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"

            def runner(key):
                built.append(key)
                run.mkdir(parents=True, exist_ok=True)
                (run / "narration_block.json").write_text(json.dumps({
                    "blocked": True, "reason": "failed the hook/teaching gate",
                }), encoding="utf-8")
                return 1
            code, _run, _m, store = self._refill(runner, [a, b, c], tmp)
        self.assertEqual(code, 1)
        self.assertEqual(built, [a["key"], b["key"]])
        self.assertEqual(store.files, {})


if __name__ == "__main__":
    unittest.main()
