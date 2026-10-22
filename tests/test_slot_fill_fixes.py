"""Regressions behind the missed 08:30 / 21:00 topic slots of 15-17 Oct 2026."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import numpy as np

import approved_buffer
import slot_guard
import sync_gate

IST = slot_guard.IST
WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"

ENTRY = "एंट्री 106, अगली कैंडल लो क्रॉस पर।"
CONTEXT = "तेजी के बाद रेसिस्टेंस 144 पर वैलिड। साइडवेज में कमजोर।"
TARGET = "टारगेट 30, 1:2। रिस्क 38, रिवॉर्ड 76।"


def _words(text, t0, step=0.35):
    return [
        {"word": word, "start": t0 + i * step, "end": t0 + i * step + 0.3}
        for i, word in enumerate(text.split())
    ]


class ChartRetryUsesTheGateNumberTests(unittest.TestCase):
    """Run 39004812914, pat_black_crows: Whisper wrote the Gemini entry beat in
    Latin ("Entry 106 Agli candle low cross"). The chart gate (fuzzy beat
    recall) passed it at 0.67, but the retry picked weak beats by slice recall
    (0.17), re-decoded at beam 5, and kept "अगली कंडल सुन्ट्रावाओ" because its
    slice recall was higher. That transcript scores 0.33 on the chart gate, so
    sync went to 0 every day."""

    def setUp(self):
        # target is the beat Whisper garbled ("absolutamente"). It fails, so
        # the report is not ok and the retry loop looks at every beat.
        self.beats = {"context": CONTEXT, "entry": ENTRY, "target": TARGET}
        self.times = {"context": (0.0, 4.0), "entry": (4.0, 7.0), "target": (7.0, 11.0)}
        self.scenes = [
            {"start": 0.0, "end": 4.0, "beat": "context", "kind": "chart-context", "show": []},
            {"start": 4.0, "end": 7.0, "beat": "entry", "kind": "chart-entry", "show": []},
            {"start": 7.0, "end": 11.0, "beat": "target", "kind": "chart-target", "show": []},
        ]
        self.garbled = [{"word": "absolutamente.", "start": 8.0, "end": 8.6}]
        self.first = _words(CONTEXT, 0.1, step=0.4) + _words(
            "Entry 106 Agli candle low cross पर", 4.1) + self.garbled

    def _run(self, fresh_words, retry_beats=("context", "entry")):
        calls = []

        def fake_pcm(_audio, beam_size=1):
            calls.append(beam_size)
            return list(fresh_words)

        with tempfile.TemporaryDirectory() as tmp:
            audio = {}
            for beat in self.beats:
                path = Path(tmp) / f"{beat}.wav"
                if beat in retry_beats:
                    path.write_bytes(b"wav")
                audio[beat] = path
            with mock.patch.object(sync_gate, "pcm", return_value=np.zeros(1600, dtype=np.float32)), \
                 mock.patch.object(sync_gate, "transcribe_pcm", side_effect=fake_pcm):
                report = sync_gate.upgrade_weak_scenes(
                    self.first, self.scenes, self.beats, self.times, [], audio,
                )
        return report, calls

    def _passed(self, report):
        return {row["beat"]: row["passed"] for row in report["scenes"]}

    def test_the_bar_is_unchanged(self):
        self.assertEqual(sync_gate.MIN_RECALL, 0.5)

    def test_a_chart_beat_that_passes_its_gate_is_not_replaced(self):
        first = sync_gate.check(self.first, self.scenes, self.beats, self.times, [])
        self.assertFalse(first["ok"])
        entry = [row for row in first["scenes"] if row["beat"] == "entry"][0]
        # The slice recall is low, the gate number is not.
        self.assertLess(entry["recall"], sync_gate.MIN_RECALL)
        self.assertGreaterEqual(entry["gate_recall"], sync_gate.MIN_RECALL)
        self.assertEqual(self._passed(first), {"context": True, "entry": True, "target": False})
        worse = [
            {"word": "अगली", "start": 0.1, "end": 0.4},
            {"word": "कंडल", "start": 0.5, "end": 0.8},
            {"word": "सुन्ट्रावाओ,", "start": 0.9, "end": 1.5},
            {"word": "अच्टाब,", "start": 1.6, "end": 2.0},
        ]
        report, calls = self._run(worse)
        # Before the fix: entry was re-decoded and the worse pass was kept,
        # so entry failed at 0.33 next to the real target miss.
        self.assertEqual(calls, [])
        self.assertEqual(self._passed(report), {"context": True, "entry": True, "target": False})
        self.assertNotIn("chart-entry", report["reason"])

    def test_a_retry_that_scores_worse_on_the_gate_is_discarded(self):
        self.first = _words(CONTEXT, 0.1, step=0.4) + _words("अगली कंडल", 4.1) + self.garbled
        report, calls = self._run([{"word": "absolutamente.", "start": 0.1, "end": 0.6}])
        # Beam-5 beat retry, then one context pass for a beat still weak.
        self.assertEqual(calls, [5, 5])
        self.assertFalse(report["ok"])
        self.assertEqual(
            [w["word"] for w in report["words"] if 4.0 <= w["start"] < 7.0],
            ["अगली", "कंडल"],
        )

    def test_a_retry_that_breaks_the_neighbour_beat_is_discarded(self):
        # Entry is weak. Four context words sit inside entry's retry pad
        # (3.55s onward), so the trial drops them along with entry's words.
        head = " ".join(CONTEXT.split()[:4])
        tail = " ".join(CONTEXT.split()[4:])
        self.first = (
            _words(head, 0.0, step=0.3) + _words(tail, 3.6, step=0.1)
            + _words("अगली कंडल", 4.1) + self.garbled
        )
        before = sync_gate.check(self.first, self.scenes, self.beats, self.times, [])
        self.assertEqual(
            self._passed(before), {"context": True, "entry": False, "target": False},
            before["reason"])
        report, calls = self._run(_words(ENTRY, 0.1), retry_beats=("entry",))
        # Beam-5 beat retry, then one context pass for a beat still weak.
        self.assertEqual(calls, [5, 5])
        # The trial would fix entry but fail context. It is not kept.
        self.assertEqual(self._passed(report), {"context": True, "entry": False, "target": False})

    def test_a_genuinely_weak_beat_still_upgrades(self):
        self.first = _words(CONTEXT, 0.1, step=0.4) + _words("अगली कंडल", 4.1) + self.garbled
        report, calls = self._run(_words(ENTRY, 0.1), retry_beats=("entry",))
        self.assertEqual(calls, [5])
        self.assertEqual(self._passed(report), {"context": True, "entry": True, "target": False})


class BufferCandidateRotationTests(unittest.TestCase):
    """The plan took the first three held keys in bank order every day
    (black crows, breakout, bull engulf). The rest of the bank never built."""

    def test_never_held_topics_come_before_retries_and_oldest_hold_next(self):
        now = datetime(2026, 10, 18, 6, 20, tzinfo=IST)
        scripts = [{"key": k} for k in ("pat_a", "pat_b", "pat_c", "pat_d", "pat_e")]
        manifest = {"items": [
            {"id": "h-a", "state": "held", "topic_key": "pat_a", "created_at": "2026-10-17T05:04:20Z"},
            {"id": "h-b", "state": "held", "topic_key": "pat_b", "created_at": "2026-10-09T00:13:22Z"},
            {"id": "h-c", "state": "held", "topic_key": "pat_c", "created_at": "2026-10-16T05:00:00Z"},
        ]}
        with mock.patch("approved_bank.all_scripts", return_value=scripts), \
             mock.patch("trade_plan.has_plan", return_value=True):
            keys = approved_buffer.candidate_keys([], manifest, now, limit=3)
            every = approved_buffer.candidate_keys([], manifest, now, limit=10)
        self.assertEqual(keys, ["pat_d", "pat_e", "pat_b"])
        self.assertEqual(every, ["pat_d", "pat_e", "pat_b", "pat_c", "pat_a"])

    def test_held_today_ready_and_recent_rules_still_apply(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=IST)
        scripts = [{"key": k} for k in ("pat_a", "pat_b", "pat_c")]
        manifest = {"items": [
            {"id": "h-a", "state": "held", "topic_key": "pat_a", "created_at": "2026-10-17T05:04:20Z"},
            {"id": "r-b", "state": "ready", "topic_key": "pat_b", "created_at": "2026-10-16T05:00:00Z"},
        ]}
        entries = [{"topic_key": "pat_c", "date_ist": "2026-10-16", "video_id": "x"}]
        with mock.patch("approved_bank.all_scripts", return_value=scripts), \
             mock.patch("trade_plan.has_plan", return_value=True):
            self.assertEqual(approved_buffer.candidate_keys(entries, manifest, now, limit=6), [])


class PastHoldDoesNotBlockTests(unittest.TestCase):
    """A hold row on a slot that has already passed must not stop a later slot."""

    def _ledger(self, tmp):
        path = Path(tmp) / "published.json"
        path.write_text(
            '[{"date_ist": "2026-10-02", "slot": "21:00", "platform": "hold", "note": "on hold"}]',
            encoding="utf-8",
        )
        return path

    def test_next_unfilled_ignores_a_past_hold(self):
        now = datetime(2026, 10, 17, 11, 30, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(slot_guard, "LEDGER", self._ledger(tmp)), \
             mock.patch.dict(os.environ, {"APPROVED_BANK": "1", "SKIP_DATA_SLOT": "1"}):
            choice = slot_guard.next_unfilled(now)
        self.assertEqual((choice["date_ist"], choice["slot"]), ("2026-10-17", "21:00"))

    def test_a_stale_bound_date_on_hold_is_rebound_not_blocked(self):
        now = datetime(2026, 10, 17, 11, 30, tzinfo=IST)
        env = {
            "PUBLISH_VISIBILITY": "public", "PUBLISH_SLOT_IST": "21:00",
            "PUBLISH_DATE_IST": "2026-10-02", "APPROVED_BANK": "1", "SKIP_DATA_SLOT": "1",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(slot_guard, "LEDGER", self._ledger(tmp)), \
             mock.patch.dict(os.environ, env):
            blocked = slot_guard.bind_next_open_slot(now)
            self.assertIsNone(blocked)
            self.assertEqual(os.environ["PUBLISH_DATE_IST"], "2026-10-17")
            self.assertEqual(os.environ["PUBLISH_SLOT_IST"], "21:00")


class BuildLogTailTests(unittest.TestCase):
    def test_unit_test_output_is_not_the_build_log_tail(self):
        text = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        tests_at = text.index("python -m unittest discover -s tests")
        clear_at = text.index("rm -f run/auto_agent_last.log")
        tail_at = text.index("f=run/auto_agent_last.log")
        self.assertLess(tests_at, clear_at)
        self.assertLess(clear_at, tail_at)


if __name__ == "__main__":
    unittest.main()
