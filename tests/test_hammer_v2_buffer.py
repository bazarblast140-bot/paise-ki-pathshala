"""Hammer v2 review fixes, pill collision, and the gated topic buffer."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from PIL import Image

import approved_buffer
import board_kit as K
import board_short
import gemini_voice
import motion_lib
import scene_pace
import self_review
import sync_gate
import trade_plan

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent.parent


def _words_for(spoken, order, garbage, step=0.45):
    rows = []
    clock = 0.0
    for text in garbage:
        rows.append({"word": text, "start": clock, "end": clock + step})
        clock += step
    for beat in order:
        for word in str(spoken[beat]).split():
            rows.append({"word": word, "start": round(clock, 3), "end": round(clock + step, 3)})
            clock += step
    return rows, clock


class ReviewRegressionTests(unittest.TestCase):
    def test_ratio_text_covers_a_spoken_two(self):
        notes = board_short.number_format_notes(
            ["RR 1:2", "ENTRY 118", "SL 98"],
            spoken="2 98 118",
            caption="एंट्री 118",
        )
        self.assertEqual(notes, [])

    def test_cta_is_not_a_vague_beat(self):
        beats = {beat: "सिर्फ एक वाक्य" for beat in trade_plan.BEATS}
        beats["entry"] = "एंट्री 118"
        vague = scene_pace.vague_beats(beats)
        self.assertNotIn("cta", vague)
        self.assertIn("hook", vague)
        explicit = scene_pace.vague_beats(beats, order=trade_plan.BEATS)
        self.assertNotIn("cta", explicit)

    def test_hook_hold_follows_the_whisper_sentence(self):
        topic = {
            "_beat_times": {"hook": (0.0, 8.0)},
            "_audio_words": [
                {"word": "करें", "start": 1.2, "end": 1.6},
                {"word": "98", "start": 3.6, "end": 4.2},
                {"word": "ट्रेंड", "start": 8.0, "end": 8.4},
            ],
        }
        self.assertAlmostEqual(motion_lib.hook_hold_until(topic), 4.2)
        clock = {"rr_hook": True, "rr_hook_until": 4.2, "rr_redraw": 40.0}
        self.assertEqual(motion_lib.rr_progress(3.0, clock), 1.0)
        self.assertEqual(motion_lib.rr_progress(4.2, clock), 1.0)
        self.assertEqual(motion_lib.rr_progress(4.2 + motion_lib.REVEAL_S, clock), 0.0)

    def test_content_bounds_keep_the_stop_clause_in_the_hook(self):
        """Run 37802686357 cut the hook before स्टॉप 98 because garbage tokens counted."""
        order = ("hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta")
        spoken = {
            "hook": "हैमर कैंडल पर खरीद कब करें एंट्री 118 और स्टॉप 98 का प्लान",
            "pehchan": "निचली विक बॉडी से 2 गुना हो ऊपर की विक छोटी हो",
            "context": "सपोर्ट 100 पर वैलिड साइडवेज में कमजोर",
            "entry": "एंट्री 118 हाई के क्लोज पर खरीद की पुष्टि",
            "sl": "स्टॉप 98 लो के नीचे लो टूटा तो प्लान गलत",
            "target": "टारगेट 158 रिस्क 20 रिवॉर्ड 40 यानी रिस्क रिवॉर्ड एक बटा 2",
            "invalidation": "लो टूटे तो स्किप यह सेटअप छोड़ दो",
            "cta": "आपने हैमर पर कभी स्टॉप लॉस हिट किया कमेंट करो",
        }
        garbage = [f"गड़बड़{i}" for i in range(10)]
        words, duration = _words_for(spoken, order, garbage)
        stop_at = next(word["start"] for word in words if word["word"] == "98")
        present, weights = gemini_voice._weights(spoken, order)
        proportional = gemini_voice._from_words(present, weights, words, duration)
        self.assertLess(proportional["hook"][1], stop_at)
        bounds = gemini_voice.beat_bounds(spoken, order, words, duration)
        self.assertGreaterEqual(bounds["hook"][1], stop_at)
        self.assertGreaterEqual(bounds["pehchan"][0], stop_at)
        self.assertEqual(bounds["hook"][0], 0.0)
        self.assertAlmostEqual(bounds["cta"][1], duration, places=2)
        for beat in order:
            self.assertGreaterEqual(bounds[beat][1] - bounds[beat][0], 1.05)

    def test_chart_beat_sync_does_not_fail_a_heard_beat(self):
        script = "अल्फा बीटा गामा टारगेट 158 रिस्क 20 रिवॉर्ड 40 यानी प्लान सही है यहाँ"
        heard = "टारगेट 158 रिस्क 20 रिवॉर्ड 40 यानी प्लान सही है यहाँ"
        words = []
        clock = 14.0
        for word in heard.split():
            words.append({"word": word, "start": clock, "end": clock + 0.4})
            clock += 0.4
        scene = {
            "start": 10.0,
            "end": 12.0,
            "beat": "target",
            "kind": "chart-target",
            "show": ["टारगेट 158 रिस्क 20 रिवॉर्ड 40"],
        }
        beats = {"target": (10.0, clock)}
        report = sync_gate.check(words, [scene], {"target": script}, beats, [])
        self.assertTrue(report["ok"], report["reason"])
        score, notes = self_review.score_sync(report)
        self.assertEqual(score, 100)
        self.assertFalse(any("before it is said" in note for note in notes))
        card = dict(scene)
        card["kind"] = "card"
        missed = sync_gate.check(words, [card], {"target": script}, beats, [])
        self.assertFalse(missed["ok"])


class PillAndCaptionTests(unittest.TestCase):
    def test_close_pills_stack_by_at_least_60px(self):
        img = Image.new("RGB", (K.W, K.H), motion_lib.BG)
        img._placed_pills = []
        box = (40, 200, 1000, 1400)
        img._pill_bounds = box
        motion_lib.level_line(img, box, 980, motion_lib.SL, "SL 98", "left", 1.0, pill=1.0)
        motion_lib.level_line(
            img, box, 990, motion_lib.WHITE, "SR 100", "left", 1.0, pill=1.0, dashed=True,
        )
        self.assertGreaterEqual(len(img._placed_pills), 2)
        first, second = img._placed_pills[0], img._placed_pills[1]
        if second[1] >= first[1]:
            gap = second[1] - first[3]
        else:
            gap = first[1] - second[3]
        self.assertGreaterEqual(gap, motion_lib.PILL_GAP - 0.5)


class BufferGateTests(unittest.TestCase):
    def _review(self, overall=82, part=80, engine="gemini", seconds=46.0, sync_ok=True):
        parts = {name: part for name in approved_buffer.PARTS}
        return {
            "overall": overall,
            "parts": parts,
            "sync": {"ok": sync_ok, "reason": ""},
            "narration_source": "approved",
        }, engine, seconds

    def _run(self, tmp, engine="gemini", seconds=46.0, overall=82, part=80, sync_ok=True):
        import approved_bank

        run = Path(tmp)
        narration = trade_plan.narration_from(approved_bank.by_key("pat_hammer"))
        (run / "narration.json").write_text(json.dumps(narration), encoding="utf-8")
        review, engine, seconds = self._review(overall, part, engine, seconds, sync_ok)
        (run / "self_review.json").write_text(json.dumps(review), encoding="utf-8")
        (run / "voice_meta.json").write_text(json.dumps({"engine": engine}), encoding="utf-8")
        (run / "shots.json").write_text(
            json.dumps({"shots": [{"end_sec": seconds}]}), encoding="utf-8")
        (run / "final.mp4").write_bytes(b"mp4-bytes")
        return run

    def test_edge_is_held_and_gemini_can_pass(self):
        now = datetime(2026, 10, 9, 3, 10, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("publish_gates.evaluate", return_value={"passed": True, "reasons": []}):
            run = self._run(tmp, engine="edge")
            reasons = approved_buffer.judge(run, "pat_hammer")
            self.assertTrue(any("voice_engine=edge" in item for item in reasons), reasons)
            (run / "voice_meta.json").write_text(json.dumps({"engine": "gemini"}), encoding="utf-8")
            self.assertEqual(approved_buffer.judge(run, "pat_hammer"), [])
            (run / "self_review.json").write_text(json.dumps({
                "overall": 65,
                "parts": {name: 40 if name == "craft" else 80 for name in approved_buffer.PARTS},
                "sync": {"ok": True},
            }), encoding="utf-8")
            low = approved_buffer.judge(run, "pat_hammer")
            self.assertTrue(any("under 75" in item for item in low))
            self.assertTrue(any("craft" in item for item in low))

    def test_repeat_window_skips_the_oldest_ready_item(self):
        now = datetime(2026, 10, 9, 8, 0, tzinfo=IST)
        manifest = {"items": [
            {
                "id": "old", "state": "ready", "owner_approved": True,
                "topic_key": "pat_hammer", "created_at": "2026-10-01T00:00:00Z",
                "asset_name": "old.mp4", "sha256": "a",
            },
            {
                "id": "next", "state": "ready", "owner_approved": True,
                "topic_key": "pat_engulfing", "created_at": "2026-10-02T00:00:00Z",
                "asset_name": "next.mp4", "sha256": "b",
            },
        ]}
        entries = [{
            "topic_key": "pat_hammer", "date_ist": "2026-10-01", "video_id": "abc",
        }]
        item, why = approved_buffer.choose_item(manifest, entries, now, "08:30")
        self.assertEqual(why, "ready")
        self.assertEqual(item["topic_key"], "pat_engulfing")
        only = {"items": [manifest["items"][0]]}
        skipped, reason = approved_buffer.choose_item(only, entries, now, "21:00")
        self.assertIsNone(skipped)
        self.assertEqual(reason, "repeat")
        self.assertEqual(
            approved_buffer.choose_item(manifest, entries, now, "16:00")[1],
            "not_topic_slot",
        )
        old = [{
            "topic_key": "pat_hammer", "date_ist": "2026-09-01", "video_id": "old",
        }]
        again, _why = approved_buffer.choose_item(only, old, now, "08:30")
        self.assertEqual(again["topic_key"], "pat_hammer")

    def test_candidates_skip_ready_and_recent_topics(self):
        now = datetime(2026, 10, 9, 3, 10, tzinfo=IST)
        entries = [{
            "topic_key": "pat_hammer", "date_ist": "2026-10-08", "video_id": "abc",
        }]
        manifest = {"items": [{
            "id": "ready", "state": "ready", "topic_key": "pat_doji",
            "owner_approved": True, "created_at": "2026-10-08T00:00:00Z",
        }]}
        keys = approved_buffer.candidate_keys(entries, manifest, now, limit=6)
        self.assertNotIn("pat_hammer", keys)
        self.assertNotIn("pat_doji", keys)
        self.assertLessEqual(len(keys), 6)
        self.assertTrue(keys)

    def test_passing_render_is_stored_and_a_miss_is_held(self):
        now = datetime(2026, 10, 9, 3, 10, tzinfo=IST)
        store = {}

        class Memory:
            def put(self, name, data, content_type="video/mp4"):
                store[name] = data

            def get(self, name):
                return store[name]

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("publish_gates.evaluate", return_value={"passed": True, "reasons": []}):
            run = self._run(tmp)
            manifest_path = Path(tmp) / "manifest.json"
            approved = Path(tmp) / "approved"
            approved.mkdir()
            script = json.loads((ROOT / "approved" / "pat_hammer.json").read_text(encoding="utf-8"))
            script["owner_approved"] = False
            (approved / "pat_hammer.json").write_text(json.dumps(script), encoding="utf-8")
            real_mark = approved_buffer.mark_owner_approved

            def _mark(key):
                return real_mark(key, approved)

            with mock.patch.object(approved_buffer, "RUN", run), \
                 mock.patch.object(approved_buffer, "judge", return_value=[]), \
                 mock.patch.object(approved_buffer, "candidate_keys", return_value=["pat_hammer"]), \
                 mock.patch.object(approved_buffer, "mark_owner_approved", side_effect=_mark):
                approved_buffer.build(
                    max_n=6, runner=lambda key: 0, store=Memory(), now=now,
                    manifest_path=manifest_path,
                )
            manifest = approved_buffer.load_manifest(manifest_path)
            ready = [item for item in manifest["items"] if item["state"] == "ready"]
            self.assertEqual(len(ready), 1)
            self.assertTrue(ready[0]["owner_approved"])
            self.assertIn(ready[0]["asset_name"], store)
            self.assertIs(
                json.loads((approved / "pat_hammer.json").read_text(encoding="utf-8"))["owner_approved"],
                True,
            )
            repo = json.loads((ROOT / "approved" / "pat_hammer.json").read_text(encoding="utf-8"))
            self.assertIs(repo.get("owner_approved"), False)

    def test_workflow_is_capped_and_slots_publish_the_buffer(self):
        text = (ROOT / ".github" / "workflows" / "build-approved-buffer.yml").read_text(encoding="utf-8")
        self.assertIn("Build approved topic buffer", text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn('"40 21 * * *"', text)
        self.assertIn("approved_buffer.py --build --max 6", text)
        self.assertIn("group: approved-topic-buffer", text)
        self.assertNotIn("paise-ki-pathshala-publish", text)
        watch = (ROOT / ".github" / "workflows" / "slot-watch.yml").read_text(encoding="utf-8")
        self.assertEqual(watch.count("group: paise-ki-pathshala-publish"), 2)
        fill = watch.split("  fill:", 1)[1].split("  nifty-fill:", 1)[0]
        self.assertIn("from_buffer: true", fill)
        nifty = watch.split("nifty-fill:", 1)[1]
        self.assertIn("data_short: true", nifty)
        self.assertNotIn("from_buffer", nifty)
        pipeline = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("approved_buffer.py --publish", pipeline)
        self.assertIn("APPROVED_BANK=1 SKIP_DATA_SLOT=1 python slot_guard.py --next", pipeline)
        self.assertIn("buffer_shorts.py --fill-scheduled", pipeline)


if __name__ == "__main__":
    unittest.main()
