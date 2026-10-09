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

    def test_plan_clock_counts_a_target_price_as_said(self):
        """Run 37863543254 scored sync 0 because outline_until stopped at pehchan.

        A chart whose beat was heard is already exempt. This card is not a
        chart, so the price counts only because the plan clock has reached
        the target beat. Whisper heard the word and not the digit.
        """
        script = "टारगेट 124 रिस्क 10 रिवॉर्ड 22 यानी रिस्क रिवॉर्ड एक बटा दो पूरा प्लान यही है"
        words = [
            {"word": "टारगेट", "start": 20.1, "end": 20.4},
            {"word": "रिस्क", "start": 20.5, "end": 20.8},
            {"word": "रिवॉर्ड", "start": 20.9, "end": 21.2},
        ]
        scene = {
            "start": 20.0,
            "end": 24.0,
            "beat": "target",
            "kind": "card",
            "show": ["टारगेट 124"],
        }
        beats = {
            "hook": (0.0, 6.0),
            "pehchan": (6.0, 12.0),
            "target": (20.0, 32.0),
        }
        text = {
            "hook": "एंट्री 102 और स्टॉप 92",
            "pehchan": "रिम 102",
            "target": script,
        }
        outlined = sync_gate.outline_until(text, beats, 24.0, words=words)
        self.assertIn("124", sync_gate.numbers_in(outlined))
        report = sync_gate.check(words, [scene], text, beats, [])
        self.assertNotIn("before it is said", report["reason"])
        self.assertTrue(report["ok"], report["reason"])
        score, notes = self_review.score_sync(report)
        self.assertEqual(score, 100, notes)
        self.assertGreater(score, 0)

    def test_glued_ek_sau_counts_as_the_plan_number(self):
        heard = "अन्टरी एकसो दो और स्टोप बानवे"
        script = "एंट्री 102 और स्टॉप 92"
        self.assertGreaterEqual(sync_gate.recall(script, heard), 0.5)
        self.assertIn("102", sync_gate.tokens(heard))

    def test_next_beat_figure_does_not_fail_the_card_on_screen(self):
        spoken = {
            "hook": "खरीद कब करें",
            "entry": "एंट्री 102",
            "sl": "स्टॉप 92 लो के नीचे",
            "target": "टारगेट 124 रिस्क 10",
            "cta": "कमेंट करो",
        }
        times = {
            "hook": (0.0, 4.0),
            "entry": (8.0, 12.0),
            "sl": (18.0, 23.5),
            "target": (23.5, 30.0),
            "cta": (30.0, 36.0),
        }
        words = [
            {"word": "स्टॉप", "start": 22.0, "end": 22.4},
            {"word": "92", "start": 23.0, "end": 23.3},
            {"word": "टारगेट", "start": 23.35, "end": 23.48},
            {"word": "124", "start": 23.45, "end": 23.7},
        ]
        said = board_short._script_near(spoken, times, words, 22.8, scene_end=23.5)
        self.assertNotIn("124", said)
        self.assertIn("92", said)
        notes = board_short.number_format_notes(["SL 92"], spoken=said, caption="स्टॉप 92")
        self.assertEqual(notes, [])

    def test_hallucinated_tail_does_not_stretch_one_beat(self):
        """A slow whisper tail must not hand one beat the rest of the file.

        Word-index cuts do that. The scorer falls back to the script share,
        which stays under 12s. Run 37863543254 stretched invalidation to ~24s,
        so later scenes scored recall 0 and sat still for the craft sample.
        """
        order = ("hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta")
        spoken = {beat: " ".join(f"शब्द{i}" for i in range(8)) for beat in order}
        words = []
        clock = 0.0
        for i in range(50):
            words.append({
                "word": f"गलत{i}",
                "start": round(clock, 3),
                "end": round(clock + 0.12, 3),
            })
            clock += 0.15
        for i, start in enumerate((10.0, 14.0, 18.0, 22.0, 26.0, 32.0)):
            words.append({
                "word": f"गड़बड़{i}",
                "start": start,
                "end": round(start + 0.4, 3),
            })
        clock = 32.4
        present, weights = gemini_voice._weights(spoken, order)
        raw = gemini_voice._from_words(present, weights, words, clock)
        raw_longest = max(end - start for start, end in raw.values())
        self.assertGreater(raw_longest, 12.0, raw)
        bounds = gemini_voice.beat_bounds(spoken, order, words, clock)
        longest = max(end - start for start, end in bounds.values())
        self.assertLess(longest, 12.0, bounds)


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
            self.assertFalse(any("craft" in item for item in low), low)
            self.assertTrue(any("craft" in item for item in approved_buffer.judge.last_notes))
            self.assertTrue(any("under 75" in item for item in approved_buffer.judge.last_notes))

    def test_skipped_trend_does_not_hold_a_passing_plan(self):
        """Evergreen lessons skip trend. Two of those can be ready together."""
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("publish_gates.evaluate", return_value={"passed": True, "reasons": []}):
            run = self._run(tmp)
            review = {
                "overall": 98.0,
                "parts": {
                    "hook": 100, "visual": 90, "accuracy": 100,
                    "trend": None, "craft": 100, "sync": 100,
                },
                "sync": {"ok": True, "reason": ""},
            }
            (run / "self_review.json").write_text(json.dumps(review), encoding="utf-8")
            import approved_bank
            for key in ("pat_doji", "pat_cup_handle"):
                narration = trade_plan.narration_from(approved_bank.by_key(key))
                (run / "narration.json").write_text(json.dumps(narration), encoding="utf-8")
                self.assertEqual(approved_buffer.judge(run, key), [], key)

    def test_edge_proof_ignores_the_previous_gemini_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run):
                (run / "tts_hook.wav").write_bytes(b"old-gemini-wav")
                (run / "tts_hook.mp3").write_bytes(b"new-edge-mp3!!")
                (run / "tts_hook.engine").write_text("edge", encoding="utf-8")
                (run / "gemini_words.json").write_text(json.dumps({
                    "beats": {"hook": [{"word": "पुराना", "start": 0.1, "end": 0.5}]},
                }), encoding="utf-8")
                self.assertEqual(board_short._beat_tts_path("hook").suffix, ".mp3")
                self.assertIsNone(board_short._gemini_word_timings({"hook": (0.0, 3.0)}))
                board_short._drop_gemini_audio(("hook",))
                self.assertFalse((run / "tts_hook.wav").exists())
                self.assertFalse((run / "gemini_words.json").exists())
                self.assertTrue((run / "tts_hook.mp3").exists())

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
        self.assertIn("max-parallel: 3", text)
        self.assertIn("--key", text)
        self.assertIn("reuse_run_id", text)
        self.assertIn("approved_buffer.py --rescore", text)


class CheckerFixTests(unittest.TestCase):
    def test_plan_value_counts_when_the_overlay_is_within_1_5s(self):
        plan = trade_plan.plan_for("pat_hammer")
        img = Image.new("RGB", (8, 8))
        img.text_layers = []
        img.motion_state = {}
        scenes = [{"start": 0.0, "end": 30.0, "beat": "target", "frame": img}]
        entry, stop, target = int(plan.entry), int(plan.sl), int(plan.target)
        spoken = {
            "hook": f"एंट्री {entry} स्टॉप {stop}",
            "entry": f"एंट्री {entry}",
            "target": f"टारगेट {target}",
            "cta": "कमेंट करो",
        }
        times = {
            "hook": (0.0, 8.0),
            "entry": (8.0, 16.0),
            "target": (20.0, 28.0),
            "cta": (28.0, 30.0),
        }
        words = [
            {"word": "एंट्री", "start": 1.0, "end": 1.4},
            {"word": str(entry), "start": 4.0, "end": 4.4},
            {"word": "टारगेट", "start": 22.0, "end": 22.4},
            {"word": str(target), "start": 23.0, "end": 23.4},
        ]
        topic = {
            "key": "pat_hammer",
            "_live_beats": "plan",
            "_beat_times": times,
            "_chart_overlays": [
                {"kind": "entry", "label": f"ENTRY {entry}", "start": 8.2, "end": 30.0},
                {"kind": "target", "label": f"TARGET {target}", "start": 24.2, "end": 30.0},
            ],
        }
        notes = board_short.number_faults_for(scenes, spoken, times, words, topic=topic)
        self.assertFalse(any(str(target) in note for note in notes), notes)
        self.assertFalse(any(str(entry) in note for note in notes), notes)
        spoken = dict(spoken)
        spoken["target"] = spoken["target"] + " 777"
        words = list(words) + [{"word": "777", "start": 25.0, "end": 25.4}]
        missed = board_short.number_faults_for(scenes, spoken, times, words, topic=topic)
        self.assertTrue(any("777" in note for note in missed), missed)

    def test_noisy_whisper_chart_beat_scores_from_the_word_span(self):
        script = "अल्फा बीटा गामा टारगेट 158 रिस्क 20 रिवॉर्ड 40 यानी प्लान सही है यहाँ"
        noisy = "alpha beta gamma target 158 risk 20 reward 40 yani plan sahi yahan"
        strict = sync_gate.recall(script, noisy)
        fuzzy = sync_gate.fuzzy_recall(script, noisy)
        self.assertLess(strict, sync_gate.MIN_RECALL, strict)
        self.assertGreaterEqual(fuzzy, sync_gate.MIN_RECALL, fuzzy)
        words = []
        clock = 14.0
        for word in noisy.split():
            words.append({"word": word, "start": clock, "end": clock + 0.35})
            clock += 0.35
        # Words outside the beat must not be what the score uses.
        words.insert(0, {"word": "अल्फा", "start": 1.0, "end": 1.3})
        scene = {
            "start": 14.0, "end": 16.0, "beat": "target", "kind": "chart-target",
            "show": ["टारगेट 158"],
        }
        report = sync_gate.check(
            words, [scene], {"target": script}, {"target": (14.0, clock)}, [],
        )
        self.assertTrue(report["ok"], report["reason"])
        self.assertGreaterEqual(report["beats"][0]["recall"], sync_gate.MIN_RECALL)
        score, _notes = self_review.score_sync(report)
        self.assertEqual(score, 100)
        wrong = sync_gate.check(
            [{"word": "गुप्तकथा", "start": 14.2, "end": 14.6},
             {"word": "अदृश्यपाठ", "start": 15.0, "end": 15.4}],
            [scene], {"target": script}, {"target": (14.0, 20.0)}, [],
        )
        self.assertFalse(wrong["ok"])

    def test_cta_margin_moves_across_four_seconds(self):
        plan = trade_plan.plan_for("pat_hammer")
        beats = {
            beat: (index * 8.0, index * 8.0 + 8.0)
            for index, beat in enumerate(trade_plan.BEATS)
        }
        topic = {"key": "pat_hammer", "_beat_times": beats, "_chart_overlays": []}
        clock = motion_lib.clock_from(topic, plan)
        start = beats["cta"][0] + 1.0
        left = Image.new("RGB", (1080, 1920), motion_lib.BG)
        right = Image.new("RGB", (1080, 1920), motion_lib.BG)
        motion_lib.draw_plan(left, plan, [], start, clock)
        motion_lib.draw_plan(right, plan, [], start + 4.0, clock)
        import numpy as np
        delta = float(np.abs(np.asarray(left, dtype=np.int16) - np.asarray(right, dtype=np.int16)).mean())
        self.assertGreaterEqual(delta, scene_pace.VISUAL_MIN_DELTA, delta)
        self.assertLessEqual(scene_pace.empty_row_fraction(left), scene_pace.EMPTY_MAX_FRAC)

    def test_premarked_skip_leaves_21_00_open(self):
        import slot_guard
        skip = {
            "date_ist": "2026-10-09",
            "slot": "21:00",
            "video_id": None,
            "platform": "skip",
            "note": "no owner_approved script left in approved/",
            "slot_skipped": "no owner_approved script left in approved/",
        }
        self.assertTrue(slot_guard.is_premarked_skip(skip))
        self.assertIsNone(slot_guard.find_entry([skip], "2026-10-09", "21:00"))
        self.assertEqual(
            slot_guard.row_state(slot_guard.find_entry([skip], "2026-10-09", "21:00")),
            "open",
        )
        uploaded = {
            "date_ist": "2026-10-09", "slot": "21:00", "video_id": "abc123",
            "platform": "youtube",
        }
        self.assertEqual(
            slot_guard.find_entry([skip, uploaded], "2026-10-09", "21:00")["video_id"],
            "abc123",
        )
        hold = {"date_ist": "2026-10-09", "slot": "21:00", "platform": "hold", "video_id": None}
        self.assertEqual(
            slot_guard.row_state(slot_guard.find_entry([hold], "2026-10-09", "21:00")),
            "held",
        )

    def test_merge_parts_keeps_each_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            held = root / "buffer-a"
            ready = root / "buffer-b"
            held.mkdir()
            ready.mkdir()
            (held / "buffer_item.json").write_text(json.dumps({
                "id": "hold-a", "state": "held", "topic_key": "pat_a",
            }), encoding="utf-8")
            (ready / "buffer_item.json").write_text(json.dumps({
                "id": "ap-b", "state": "ready", "topic_key": "pat_b", "owner_approved": True,
            }), encoding="utf-8")
            (ready / "pat_b.json").write_text(
                json.dumps({"key": "pat_b", "owner_approved": True}), encoding="utf-8",
            )
            manifest = root / "manifest.json"
            manifest.write_text('{"tag": "approved-topic-buffer", "items": []}\n', encoding="utf-8")
            approved = root / "approved"
            approved.mkdir()
            approved_buffer.merge_parts(root, manifest_path=manifest, approved_dir=approved)
            keys = {item["topic_key"] for item in json.loads(manifest.read_text())["items"]}
            self.assertEqual(keys, {"pat_a", "pat_b"})
            self.assertIs(
                json.loads((approved / "pat_b.json").read_text(encoding="utf-8"))["owner_approved"],
                True,
            )

    def test_trim_exit_keeps_the_sentence(self):
        import common

        def trim(_beats):
            common.die("TTS beat hook became too short after silence trim: 0.20s")

        spoken = {"hook": "एक"}
        with mock.patch.object(gemini_voice, "write_speech", return_value=None), \
             mock.patch.object(gemini_voice, "wav_seconds", return_value=40.0), \
             mock.patch.object(gemini_voice, "duration_far", return_value=False), \
             mock.patch.object(gemini_voice, "split_into_beats", return_value=None), \
             mock.patch.object(board_short, "_trim_rendered", side_effect=trim):
            with self.assertRaises(gemini_voice.GeminiFallback) as caught:
                board_short._one_gemini_take(
                    spoken, {}, ("hook",), spoken, "एक", gemini_voice.STYLE, [], 0, None, None,
                )
        self.assertIn("too short", str(caught.exception))
        self.assertNotEqual(str(caught.exception).strip(), "1")

    def test_missing_trend_and_soft_public_review_are_notes(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("publish_gates.evaluate", return_value={
                 "passed": False,
                 "reasons": ["existing public-review gate did not pass"],
             }):
            run = BufferGateTests()._run(tmp)
            review = {
                "overall": 98.0,
                "parts": {
                    "hook": 100, "visual": 90, "accuracy": 100, "craft": 100, "sync": 100,
                },
                "sync": {"ok": True, "reason": ""},
            }
            (run / "self_review.json").write_text(json.dumps(review), encoding="utf-8")
            (run / "report.md").write_text(
                "VERDICT: [NEEDS FIX — unverified figure]\n"
                "FACT LEDGER mein sirf पूरा ट्रेड प्लान likha hai\n",
                encoding="utf-8",
            )
            reasons = approved_buffer.judge(run, "pat_hammer")
            self.assertEqual(reasons, [], reasons)
            notes = " ".join(approved_buffer.judge.last_notes)
            self.assertIn("trend score missing", notes)
            self.assertIn("public-review", notes)
            self.assertIn("unverified", notes.lower())

    def test_sync_still_holds_and_edge_stays_held(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("publish_gates.evaluate", return_value={"passed": True, "reasons": []}):
            run = BufferGateTests()._run(tmp, engine="gemini", sync_ok=False)
            review = json.loads((run / "self_review.json").read_text(encoding="utf-8"))
            review["parts"]["sync"] = 0
            review["parts"]["trend"] = None
            review["overall"] = 78
            (run / "self_review.json").write_text(json.dumps(review), encoding="utf-8")
            reasons = approved_buffer.judge(run, "pat_hammer")
            self.assertTrue(any("sync" in item for item in reasons), reasons)
            self.assertFalse(any("trend" in item for item in reasons), reasons)
            (run / "voice_meta.json").write_text(json.dumps({"engine": "edge"}), encoding="utf-8")
            (run / "gemini_quota.txt").write_text("gemini_quota: HTTP 429\n", encoding="utf-8")
            held = approved_buffer.judge(run, "pat_hammer")
            self.assertTrue(any("voice_engine=edge" in item for item in held), held)
            self.assertIn("gemini_quota", held)

    def test_a_39_second_plan_take_is_padded_not_rejected(self):
        self.assertEqual(board_short.gemini_accepts(39.3, trade_plan.VOICE_SECONDS), "target")
        self.assertEqual(board_short.gemini_accepts(37.9, trade_plan.VOICE_SECONDS), "miss")
        self.assertEqual(board_short.gemini_accepts(55.0, trade_plan.VOICE_SECONDS), "review")

    def test_short_silence_trim_is_padded(self):
        import struct
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run):
                path = run / "tts_pehchan.wav"
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(24000)
                    handle.writeframes(struct.pack("<h", 8000) * int(0.82 * 24000))
                (run / "tts_pehchan.engine").write_text("gemini", encoding="utf-8")
                raw = board_short._trim_rendered(("pehchan",))
        self.assertGreaterEqual(raw["pehchan"], 1.0)

    def test_gemini_clock_keeps_slice_words_inside_the_beat(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run):
                for beat in ("entry", "target", "cta"):
                    (run / f"tts_{beat}.wav").write_bytes(b"RIFFgemini")
                    (run / f"tts_{beat}.engine").write_text("gemini", encoding="utf-8")
                (run / "gemini_words.json").write_text(json.dumps({
                    "beats": {
                        "entry": [{"word": "एंट्री", "start": 0.1, "end": 0.4}],
                        "target": [
                            {"word": "टारगेट", "start": 0.4, "end": 0.8},
                            {"word": "158", "start": 0.9, "end": 1.4},
                        ],
                        "cta": [{"word": "कमेंट", "start": 0.1, "end": 0.4}],
                    },
                }), encoding="utf-8")
                words = board_short._gemini_word_timings({
                    "entry": (0.0, 4.0),
                    "target": (10.0, 12.0),
                    "cta": (12.0, 16.0),
                })
                words = [row for row in words if row["word"] in ("टारगेट", "158")]
        self.assertEqual([row["word"] for row in words], ["टारगेट", "158"])
        self.assertGreaterEqual(words[0]["start"], 10.0)
        self.assertLessEqual(words[-1]["end"], 12.0 + 0.05)

    def test_rescore_of_a_hold_row_drops_the_soft_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / "buffer-pat_gap_up"
            part.mkdir()
            (part / "buffer_item.json").write_text(json.dumps({
                "id": "hold-pat_gap_up",
                "state": "held",
                "topic_key": "pat_gap_up",
                "reason": "existing public-review gate did not pass; trend score missing",
                "voice_engine": "gemini",
                "duration_s": 40.47,
            }), encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text('{"tag": "approved-topic-buffer", "items": []}\n', encoding="utf-8")
            buf = __import__("io").StringIO()
            with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "should-be-cleared"}, clear=False):
                from contextlib import redirect_stdout
                with redirect_stdout(buf):
                    approved_buffer.rescore(root, manifest_path=manifest)
            text = buf.getvalue()
            self.assertIn("[REVIEW] pat_gap_up:", text)
            self.assertIn("trend score missing", text)
            self.assertIn("render artifact has no final.mp4", text)
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["items"][0]["state"], "held")
            self.assertIn("final.mp4", saved["items"][0]["reason"])
            self.assertNotIn("trend score missing", saved["items"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
