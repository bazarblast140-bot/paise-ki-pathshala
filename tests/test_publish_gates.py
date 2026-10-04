"""Auto-publish runs every gate after a private upload. No network."""
import inspect
import json
import os
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import board_short
import buffer_shorts
import eleven_voice
import ig_defer
import ledger
import publish_gates
import qa_hold
import self_review
import slot_guard

IST = slot_guard.IST
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=IST)
DATE = "2026-10-02"
SLOT = "21:00"


def _review(sync=None):
    return {
        "public_ok": True,
        "passed": True,
        "narration_source": "llm",
        "narration_fallback": False,
        "overall": 100,
        "parts": {
            "hook": 100, "visual": 100, "accuracy": 100,
            "trend": 100, "craft": 100, "sync": 100,
        },
        "sync": sync or {"ok": True, "reason": "", "beats": []},
        "notes": [],
    }


def _shot(end=55.0):
    line = "हैमर विक लंबी है।"
    return {
        "shots": [
            {
                "shot_id": 1,
                "beat": beat,
                "start_sec": 0.0,
                "end_sec": end,
                "on_screen_text": "हैमर",
                "spoken_line": line,
                "chart_data": {"template": "hammer"},
            }
            for beat in ("hook", "pehchan", "matlab", "karein")
        ]
    }


def _write_run(run, *, script="TITLE:\nहैमर\n", review=None, shots=None, mix=None, topic=None):
    run.mkdir(parents=True, exist_ok=True)
    topic = topic or {"key": "hammer", "_live_short": True}
    (run / "topic.json").write_text(json.dumps(topic), encoding="utf-8")
    (run / "script.txt").write_text(script, encoding="utf-8")
    (run / "title.txt").write_text("हैमर\n", encoding="utf-8")
    (run / "self_review.json").write_text(json.dumps(review or _review()), encoding="utf-8")
    (run / "shots.json").write_text(json.dumps(shots or _shot()), encoding="utf-8")
    (run / "audio_mix.json").write_text(json.dumps(mix or {
        "bed_mixed": False, "background": False, "sfx_present": False, "sfx_under_db": None,
    }), encoding="utf-8")
    (run / "narration.json").write_text(json.dumps({
        "title": "हैमर",
        "beats": {beat: "हैमर विक लंबी है।" for beat in ("hook", "pehchan", "matlab", "karein")},
    }), encoding="utf-8")
    (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
    (run / "verify_meta.json").write_text(json.dumps({
        "ai_verdict_valid": True, "verdict": "READY", "public_ready": True,
    }), encoding="utf-8")
    return run


def _wav(path, samples, rate=8000):
    import numpy as np
    pcm = (np.asarray(samples, dtype="float32") * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


class GateTests(unittest.TestCase):
    def test_ready_bar_is_the_existing_threshold(self):
        self.assertEqual(self_review.OVERALL_BAR, 75)
        self.assertEqual(self_review.PART_BAR, 60)
        self.assertEqual(publish_gates.READY_OVERALL, 75)
        self.assertEqual(publish_gates.READY_PART, 60)

    def test_a_clean_run_passes_and_sixty_seconds_is_not_a_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _write_run(Path(tmp), shots=_shot(60.0))
            verdict = publish_gates.evaluate(run)
        self.assertEqual(verdict["reasons"], [], verdict)
        self.assertTrue(verdict["passed"])

    def test_tail_miss_does_not_fail_the_score_and_another_miss_does(self):
        sync = {
            "ok": False,
            "reason": "narration does not match the script (recall 0.33)",
            "beats": [{"script": "हैमर लौटे आखिर", "heard": "हैमर"}],
        }
        review = _review(sync)
        review["parts"]["sync"] = 0
        review["public_ok"] = False
        self.assertEqual(publish_gates.score_reasons(review), [])
        self.assertTrue(publish_gates.tail_only_miss(sync))
        earlier = {
            "ok": False,
            "reason": "narration does not match the script (recall 0.25)",
            "beats": [{"script": "हैमर विक खरीदार लौटे आखिर", "heard": "लौटे आखिर"}],
        }
        self.assertFalse(publish_gates.tail_only_miss(earlier))
        missed = _review(earlier)
        missed["parts"]["sync"] = 0
        self.assertTrue(publish_gates.score_reasons(missed))

    def test_chart_cue_hinglish_market_and_audio_each_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrong = _write_run(root / "chart")
            shots = _shot()
            shots["shots"][0]["chart_data"]["template"] = "doji"
            shots["shots"][1]["spoken_line"] = "कुछ और कहा।"
            (root / "chart" / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
            chart = publish_gates.evaluate(root / "chart")
            self.assertFalse(chart["passed"])
            self.assertTrue(any("does not belong" in reason for reason in chart["reasons"]))
            self.assertTrue(any("cue word" in reason for reason in chart["reasons"]))

            banned = _write_run(root / "words", script="TITLE:\nहद और तय करो\n")
            words = publish_gates.evaluate(banned)
            self.assertTrue(any("हद" in reason and "तय करो" in reason for reason in words["reasons"]))

            claim = _write_run(root / "claim", script="TITLE:\nहैमर\nआज market closed 2026\n")
            claimed = publish_gates.evaluate(claim)
            self.assertTrue(any("unverified market claim" in reason for reason in claimed["reasons"]))
            self.assertTrue(any("2026" in reason for reason in claimed["reasons"]))
            (root / "claim" / "market_freshness.json").write_text(
                json.dumps({
                    "as_of": "2026-10-02",
                    "on_screen": True,
                    "note": "आज market closed 2026",
                }),
                encoding="utf-8",
            )
            allowed = publish_gates.evaluate(root / "claim")
            self.assertTrue(allowed["passed"], allowed["reasons"])

            loud = _write_run(root / "loud", mix={
                "bed_mixed": False, "background": False, "sfx_present": True, "sfx_under_db": 10.0,
            })
            self.assertTrue(any("sfx" in reason for reason in publish_gates.evaluate(loud)["reasons"]))
            bed = _write_run(root / "bed", mix={
                "bed_mixed": True, "background": True, "sfx_present": False, "sfx_under_db": None,
            })
            self.assertTrue(any("music bed" in reason for reason in publish_gates.evaluate(bed)["reasons"]))

    def test_wav_measure_flags_a_bed_and_a_loud_effect(self):
        import numpy as np
        rate = 8000
        n = rate
        t = np.arange(n) / rate
        voice = 0.2 * np.sin(2 * np.pi * 180 * t)
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            _wav(run / "voice.wav", voice, rate)
            _wav(run / "mixed.wav", voice, rate)
            quiet = publish_gates.measure_wavs(run / "voice.wav", run / "mixed.wav")
            self.assertFalse(quiet["background"])
            self.assertFalse(quiet["sfx_present"])
            burst = voice.copy()
            burst[: int(rate * 0.05)] += 0.25
            _wav(run / "mixed.wav", burst, rate)
            loud = publish_gates.measure_wavs(run / "voice.wav", run / "mixed.wav")
            self.assertFalse(loud["background"])
            self.assertLess(loud["sfx_under_db"], 18.0)
            _wav(run / "mixed.wav", voice + 0.05, rate)
            bed = publish_gates.measure_wavs(run / "voice.wav", run / "mixed.wav")
            self.assertTrue(bed["background"])

    def test_over_fifty_nine_seconds_stays_inside_the_existing_duration_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _write_run(Path(tmp) / "ok", shots=_shot(60.0))
            self.assertEqual(publish_gates.duration_reasons(run, {"key": "hammer", "_live_short": True}), [])
            late = _write_run(Path(tmp) / "late", shots=_shot(70.0))
            self.assertTrue(publish_gates.duration_reasons(late, {"key": "hammer", "_live_short": True}))

    def test_credit_floor_stops_the_live_slot_and_does_not_speak_edge(self):
        self.assertEqual(eleven_voice.LIVE_CREDIT_FLOOR, 1000)
        preflight = inspect.getsource(board_short.preflight_eleven_credits)
        stop = inspect.getsource(board_short._credits_stop)
        self.assertIn("_credits_stop", preflight)
        self.assertNotIn("_switch_to_edge", preflight)
        self.assertIn("No Edge fallback", stop)

    def test_release_plan_schedules_ahead_and_publishes_a_recent_slot(self):
        ahead = publish_gates.release_plan(SLOT, DATE, now=NOW)
        self.assertEqual(ahead["action"], "schedule")
        self.assertEqual(ahead["publish_at"], "2026-10-02T15:30:00Z")
        recent = publish_gates.release_plan(
            SLOT, DATE, now=datetime(2026, 10, 2, 21, 10, tzinfo=IST),
        )
        self.assertEqual(recent["action"], "public")
        self.assertIsNone(recent["publish_at"])
        parsed = ig_defer.parse_deferred(f"deferred: vid publishAt={recent['defer_at']}")
        self.assertTrue(ig_defer.is_due(parsed, datetime.now(timezone.utc)))
        late = publish_gates.release_plan(
            SLOT, DATE, now=datetime(2026, 10, 2, 21, 40, tzinfo=IST),
        )
        self.assertEqual(late["action"], "skip")
        soon = NOW + timedelta(hours=12) - timedelta(minutes=10)
        # 21:00 is 12h after 09:00. Ten minutes before the slot is inside the upload buffer.
        inside = publish_gates.release_plan(SLOT, DATE, now=datetime(2026, 10, 2, 20, 50, tzinfo=IST))
        self.assertEqual(inside["action"], "skip")
        self.assertIn("20 minute", inside["reason"])
        self.assertIsNotNone(soon)


class BufferAutoTests(unittest.TestCase):
    def _fill(self, item, now=NOW, status=None, last_chance=False):
        updates = []

        def update(video_id, privacy, publish_at):
            updates.append((video_id, privacy, publish_at))
            if status is not None:
                status.append((video_id, privacy, publish_at))

        recorded = []
        posts = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
            store = mock.Mock()
            store.get.return_value = b"buffer-video-bytes"
            env = {
                "AUTOMATIC_UPLOAD": "1",
                "REQUIRE_QA_APPROVAL": "false",
                "EXPLICIT_PRIVACY": "",
                "GITHUB_RUN_ID": "555",
                "GITHUB_STEP_SUMMARY": str(root / "summary.txt"),
            }
            with mock.patch.object(buffer_shorts, "RUN", root / "run"), \
                 mock.patch.object(ledger, "PATH", root / "published.json"), \
                 mock.patch.dict(os.environ, env, clear=False):
                result = buffer_shorts.perform_fill(
                    now=now,
                    date_ist=DATE,
                    slot=SLOT,
                    last_chance=last_chance,
                    read_entries=lambda: [],
                    read_schedule=lambda: set(),
                    manifest_path=manifest_path,
                    store=store,
                    uploader=lambda *args: "vidbuffer1",
                    crosspost=lambda *args: posts.append(args),
                    alert=lambda *args: None,
                    record=lambda *args, **kwargs: recorded.append(kwargs) or ledger.record_publish(*args, **kwargs),
                    status_update=update,
                )
                summary = (root / "summary.txt").read_text(encoding="utf-8") if (root / "summary.txt").exists() else ""
                status_file = root / "run" / "ig_status.txt"
                ig = status_file.read_text(encoding="utf-8") if status_file.exists() else ""
                rows = json.loads((root / "published.json").read_text(encoding="utf-8")) if (root / "published.json").exists() else []
        return result, recorded, posts, updates, summary, ig, rows

    def _passing_item(self):
        digest = buffer_shorts.sha256_bytes(b"buffer-video-bytes")
        return {
            "id": f"buf-hammer-{digest[:12]}",
            "state": "ready",
            "topic_key": "hammer",
            "title": "हैमर",
            "description": "हैमर विक।",
            "tags": ["trading hindi"],
            "sha256": digest,
            "asset_name": "clip.mp4",
            "created_at": "2026-10-01T00:00:00Z",
            "files": {
                "topic": {"key": "hammer", "_live_short": True},
                "script": "TITLE:\nहैमर\n",
                "report": "VERDICT: [READY TO PUBLISH]\n",
                "verify_meta": {"ai_verdict_valid": True, "verdict": "READY", "public_ready": True},
                "self_review": _review(),
                "narration": {
                    "title": "हैमर",
                    "beats": {beat: "हैमर विक लंबी है।" for beat in ("hook", "pehchan", "matlab", "karein")},
                },
                "shots": _shot(),
                "audio_mix": {
                    "bed_mixed": False, "background": False, "sfx_present": False, "sfx_under_db": None,
                },
            },
        }

    def test_a_failing_buffer_video_stays_private_and_blocks_the_slot(self):
        digest = buffer_shorts.sha256_bytes(b"buffer-video-bytes")
        item = {
            "id": f"buf-hammer-{digest[:12]}",
            "state": "ready",
            "topic_key": "buffer_example_risk",
            "title": "हद तय करो",
            "description": "",
            "tags": [],
            "sha256": digest,
            "asset_name": "clip.mp4",
            "created_at": "2026-10-01T00:00:00Z",
            "files": {"topic": {"key": "buffer_example_risk"}, "script": "हद", "self_review": {}},
        }
        result, recorded, posts, updates, summary, ig, _rows = self._fill(item)
        self.assertTrue(result["filled"])
        self.assertIsNone(result["publish_at"])
        self.assertEqual(posts, [])
        self.assertEqual(updates, [])
        self.assertTrue(recorded[0]["slot_skipped"])
        self.assertIn("हद", recorded[0]["slot_skipped"])
        self.assertIn("gate: skipped", summary)
        self.assertFalse(ig.startswith("deferred:"))
        self.assertFalse(qa_hold.hold_active())
        import slot_watch
        row = {
            "date_ist": DATE, "slot": SLOT, "video_id": "vidbuffer1",
            "slot_skipped": recorded[0]["slot_skipped"], "format": "short",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text(json.dumps([
                {"date_ist": "2026-09-01", "slot": "08:30", "video_id": "old", "format": "short"},
                row,
            ]), encoding="utf-8")
            gout = Path(tmp) / "gha"
            buf = __import__("io").StringIO()
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict(os.environ, {"SLOT_CHECK": SLOT, "GITHUB_OUTPUT": str(gout)}), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as clock, \
                 mock.patch("sys.stdout", buf):
                clock.now.return_value = datetime(2026, 10, 2, 21, 49, tzinfo=IST)
                slot_watch.main()
            flags = gout.read_text(encoding="utf-8")
        self.assertIn("use_buffer=false", flags)
        self.assertIn("missing=false", flags)

    def test_a_passing_buffer_video_is_scheduled_and_the_reel_is_deferred(self):
        result, recorded, posts, updates, summary, ig, rows = self._fill(self._passing_item())
        self.assertTrue(result["filled"])
        self.assertEqual(result["publish_at"], "2026-10-02T15:30:00Z")
        self.assertEqual(posts, [])
        self.assertEqual(updates, [("vidbuffer1", "private", "2026-10-02T15:30:00Z")])
        self.assertIsNone(recorded[0]["slot_skipped"])
        self.assertIn("deferred:", ig)
        self.assertIn("gate: passed", summary)
        self.assertTrue(rows)
        self.assertIn("deferred:", rows[-1].get("ig_deferred") or "")

    def test_a_slot_ten_minutes_past_goes_public_and_forty_does_not(self):
        recent_now = datetime(2026, 10, 2, 21, 10, tzinfo=IST)
        _result, _recorded, posts, updates, _summary, ig, _rows = self._fill(
            self._passing_item(), now=recent_now, last_chance=True,
        )
        self.assertEqual(posts, [])
        self.assertEqual(updates[0][1], "public")
        self.assertIsNone(updates[0][2])
        self.assertIn("deferred:", ig)
        late_now = datetime(2026, 10, 2, 21, 40, tzinfo=IST)
        result, recorded, posts, updates, summary, ig, _rows = self._fill(
            self._passing_item(), now=late_now, last_chance=True,
        )
        self.assertEqual(updates, [])
        self.assertEqual(posts, [])
        self.assertIn("30 minute", recorded[0]["slot_skipped"])
        self.assertIn("gate: skipped", summary)
        self.assertNotIn("deferred:", ig)


class CrosspostTests(unittest.TestCase):
    def test_auto_mode_does_not_post_the_reel_from_the_build(self):
        import run_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "auto_gate_pass.txt").write_text("2026-10-02T15:30:00Z\n", encoding="utf-8")
            with mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.object(run_pipeline, "run", side_effect=AssertionError("instagram")), \
                 mock.patch.dict(os.environ, {
                     "AUTOMATIC_UPLOAD": "1",
                     "REQUIRE_QA_APPROVAL": "false",
                     "IG_CROSSPOST": "1",
                 }):
                rc, published, required = run_pipeline.crosspost_or_defer(True)
            self.assertEqual((rc, published, required), (0, False, False))


if __name__ == "__main__":
    unittest.main()
