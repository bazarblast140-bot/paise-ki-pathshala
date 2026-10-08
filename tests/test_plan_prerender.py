"""Plan topics must not crash on the old karein/matlab beat names."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import approved_bank
import board_short
import short_layout
import sync_gate
import trade_plan


def _clock():
    return {
        beat: (index * 5.0, index * 5.0 + 5.0)
        for index, beat in enumerate(trade_plan.BEATS)
    }


class _Frame:
    def __init__(self):
        self.text_layers = []
        self.animate = None


class PreRenderTests(unittest.TestCase):
    def test_every_approved_script_survives_prerender_and_the_sync_matcher(self):
        scripts = approved_bank.all_scripts()
        self.assertGreaterEqual(len(scripts), 17)
        board_short._CUE_CACHE.clear()
        for script in scripts:
            key = script["key"]
            topic = approved_bank.topic_from(script)
            narration = approved_bank.narration_from(script)
            spoken = narration["beats"]
            words = sum(len(str(spoken[beat]).split()) for beat in trade_plan.BEATS)
            self.assertGreaterEqual(words, trade_plan.TOTAL_WORDS[0], key)
            self.assertLessEqual(words, trade_plan.TOTAL_WORDS[1], key)
            estimate = words / 1.9
            self.assertGreaterEqual(estimate, trade_plan.VOICE_SECONDS[0], key)
            self.assertLessEqual(estimate, trade_plan.VOICE_REVIEW_MAX, key)
            self.assertEqual(board_short.check_narration(narration, topic), [], key)
            beat_times = _clock()
            self.assertNotIn("karein", beat_times, key)
            self.assertNotIn("matlab", beat_times, key)
            hook = narration["hook_line"]
            for second in (0.2, 12.0, 39.5):
                boxes = board_short.on_screen_boxes(
                    topic, beat_times, spoken, hook, "कमेंट", second,
                )
                self.assertIsInstance(boxes, list, key)
            ass = board_short.build_ass(topic, beat_times, spoken, hook, "कमेंट")
            self.assertIn("Dialogue", ass, key)
            scenes = [
                {
                    "frame": _Frame(),
                    "start": beat_times[beat][0],
                    "end": beat_times[beat][1],
                    "beat": beat,
                }
                for beat in trade_plan.BEATS
            ]
            frames = board_short.frame_text_boxes(
                scenes, topic, beat_times, spoken, hook, "कमेंट", fps=1,
            )
            self.assertEqual(len(frames), 40, key)
            events = short_layout.overlay_plan(topic, beat_times, hook, "कमेंट")
            self.assertTrue(any(event.get("role") == "disclaimer" for event in events), key)
            folded = board_short.fold_roman_words(spoken["cta"])
            self.assertNotRegex(folded, r"[A-Za-z]{2,}", key)
            self.assertIn("सीखने", folded, key)
            heard = sync_gate.normalize("karein matlab Sirf seekhne ke liye")
            self.assertNotIn("karein", heard.split(), key)
            self.assertNotIn("matlab", heard.split(), key)
            self.assertIn("करें", heard.split(), key)
            self.assertIn("मतलब", heard.split(), key)
            self.assertIn("सिर्फ", heard.split(), key)
            audio = {beat: f"{beat}.wav" for beat in trade_plan.BEATS}
            blob = np.ones(1600, dtype=np.float32)

            def fake_transcribe(_audio, beat=key):
                return [{"word": "शब्द", "start": 0.2, "end": 0.6}]

            with mock.patch.object(sync_gate, "pcm", return_value=blob), \
                 mock.patch.object(sync_gate, "transcribe_pcm", side_effect=fake_transcribe):
                timed = sync_gate.transcribe_beats(audio, beat_times)
            self.assertEqual(len(timed), len(trade_plan.BEATS), key)
            report = sync_gate.check(
                timed, scenes, spoken, beat_times, [],
            )
            self.assertIn("ok", report, key)
            self.assertNotIn("karein", str(report.get("reason") or ""), key)
            filled = trade_plan.fill("लेवल {missing_token}", trade_plan.plan_for(key))
            self.assertIn("{missing_token}", filled, key)
        board_short._CUE_CACHE.clear()

    def test_upload_pass_is_not_stuck_on_the_edge_proof(self):
        topic = approved_bank.topic_from(approved_bank.by_key("pat_hammer"))
        seen = {}

        def render(*_args, **_kwargs):
            seen["during"] = os.environ.get("PROOF_FREE_AUDIO")
            (board_short.RUN / "self_review.json").write_text(
                json.dumps({"public_ok": True, "summary": "ok"}), encoding="utf-8",
            )
            return {"final_dur": 46.0, "order": trade_plan.BEATS, "spoken": {}}

        def pay(*_args, **_kwargs):
            seen["after"] = os.environ.get("PROOF_FREE_AUDIO")
            return 46.0

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "_needs_paid_proof", return_value=True), \
             mock.patch.object(board_short, "_render_short", side_effect=render), \
             mock.patch.object(board_short, "_eleven_spent", return_value=0), \
             mock.patch.object(board_short, "_pay_and_remux", side_effect=pay):
            board_short.produce_short(topic, {"beats": {}}, {"voice": {}}, {})
        self.assertEqual(seen["during"], "edge")
        self.assertIsNone(seen["after"])

    def test_soft_plan_review_does_not_block_gemini(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        topic = {"key": "pat_hammer", "_live_beats": "plan"}
        soft = {
            "public_ok": False,
            "review_flag": False,
            "verdict": "HOLD",
            "overall": 48.3,
            "parts": {
                "hook": 100, "visual": 80, "accuracy": 100,
                "trend": 100, "craft": 0, "sync": 100,
            },
            "summary": "overall 48.3 vague explanation: matlab, karein; no visual change across 4.0s",
            "notes": [
                "vague explanation (no number, level or step): matlab, karein",
                "no visual change across 4.0s (1.0-5.0, delta 0.10)",
            ],
        }
        holder = {"review": soft}

        def render(*_args, **_kwargs):
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")
            (board_short.RUN / "self_review.json").write_text(
                json.dumps(holder["review"]), encoding="utf-8",
            )
            return {"final_dur": 46.0}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "_render_short", side_effect=render), \
             mock.patch.object(board_short, "_eleven_spent", return_value=0), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APPROVED", None)
            os.environ.pop("DRY_RUN", None)
            out = io.StringIO()
            with redirect_stdout(out):
                proven = board_short.prove_full_render(topic, {}, {}, {})
            self.assertEqual(proven["final_dur"], 46.0)
            self.assertIn("[REVIEW]", out.getvalue())
            saved = json.loads((Path(tmp) / "self_review.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["review_flag"])
            self.assertFalse(saved["public_ok"])
            holder["review"] = {
                **soft,
                "parts": {**soft["parts"], "accuracy": 0},
                "notes": ["numbers with no source: ['100', '118']"],
                "summary": "numbers with no source: ['100', '118']",
            }
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit):
                board_short.prove_full_render(topic, {}, {}, {})
            self.assertIn("pre-render HOLD", err.getvalue())


if __name__ == "__main__":
    unittest.main()
