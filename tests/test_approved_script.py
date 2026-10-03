"""Reuse a dry-run script, retimecards to the paid voice, and keep soft misses as REVIEW."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import approved_script
import board_short
import narration_dry_run
import scene_frames
import scene_pace
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
    def test_tts_lines_rebuild_beats_without_json_or_a_new_script(self):
        text = _lines()
        beats = approved_script.beats_from_tts_text(text)
        self.assertEqual(list(beats), list(ORDER))
        self.assertEqual(beats["hook"], "hook की तय लाइन 1।")
        self.assertEqual(beats["karein"], "karein की तय लाइन 4।")
        blob = " ".join(f"शब्द{i}" for i in range(105))
        split = approved_script.split_spoken_blob(blob)
        self.assertEqual(
            [len(split[beat].split()) for beat in ORDER],
            [18, 26, 32, 29],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tts_risk_daily_loss_limit.txt").write_text(text, encoding="utf-8")
            (root / "tts_text.txt").write_text("ignored blob", encoding="utf-8")
            payload = approved_script.load_payload(root, "risk_daily_loss_limit")
            self.assertNotIn("title", payload)
            script = approved_script.narration_from_payload(payload, _topic(), "37095205924")
            self.assertEqual(script["beats"], beats)
            self.assertEqual(script["source"], "approved")
            self.assertEqual(script["from_run"], "37095205924")
            saved = {
                "title": "दिन की हद",
                "hook_line": "क्यों रुकें?",
                "beats": {beat: f"json {beat}" for beat in ORDER},
                "source": "llm",
            }
            (root / "narration_risk_daily_loss_limit.json").write_text(
                json.dumps(saved), encoding="utf-8",
            )
            preferred = approved_script.load_payload(root, "risk_daily_loss_limit")
            self.assertEqual(preferred["beats"]["matlab"], "json matlab")

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
            self.assertFalse(script["narration_fallback"])
            self.assertEqual(board_short.upload_block_reason(source), "")
            board_short.require_llm_narration(script, source)

    def test_prepare_spoken_does_not_trim_an_approved_script(self):
        topic = _topic()
        topic["_approved_script"] = True
        beats = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        narration = {"title": "दिन की हद", "hook_line": "क्यों?", "beats": dict(beats), "source": "approved"}
        spoken = dict(beats)

        def synthesize(words, _c, beats=None):
            self.assertEqual({beat: words[beat] for beat in beats}, spoken)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short, "fit_estimated_duration", side_effect=AssertionError("no trim")), \
             mock.patch.object(board_short, "apply_live_draft", side_effect=AssertionError("no trim")), \
             mock.patch.object(board_short, "synthesize_short_voices", side_effect=synthesize), \
             mock.patch.object(board_short, "_trim_rendered", return_value={beat: 12.0 for beat in ORDER}):
            out_spoken, out_narration, *_rest = board_short.prepare_spoken(
                spoken, narration, topic, {"voice": {}}, {},
            )
        self.assertEqual(out_spoken, spoken)
        self.assertEqual(out_narration["beats"], beats)

    def test_dry_run_saves_narration_json_beside_the_tts_text(self):
        narration = {"title": "दिन की हद", "beats": {beat: beat for beat in ORDER}, "source": "llm"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)):
            run = Path(tmp)
            (run / "final.mp4").write_bytes(b"mp4")
            (run / "tts_text.txt").write_text(_lines(), encoding="utf-8")
            narration_dry_run._keep_topic_artifacts("risk_daily_loss_limit", narration)
            saved = json.loads((run / "narration_risk_daily_loss_limit.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["beats"]["hook"], "hook")
            self.assertTrue((run / "tts_risk_daily_loss_limit.txt").is_file())
        dry = (ROOT / ".github" / "workflows" / "narration-dry-run.yml").read_text(encoding="utf-8")
        self.assertIn("run/narration_*.json", dry)
        self.assertIn("run/tts_*.txt", dry)
        live = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("narration_from_run:", live)
        self.assertIn("actions: read", live)
        self.assertIn("approved_script.py --download", live)
        self.assertIn("NARRATION_FROM_RUN:", live)

    def test_a_number_stays_off_the_card_and_chart_until_it_is_spoken(self):
        spoken = {
            "hook": "पूँजी ₹2,00,000 है। दिन की हद ₹4,000 है।",
            "pehchan": "नियम यही है।",
            "matlab": "मतलब यही है।",
            "karein": "करें यही।",
        }
        times = {
            "hook": (0.0, 10.0),
            "pehchan": (10.0, 14.0),
            "matlab": (14.0, 18.0),
            "karein": (18.0, 22.0),
        }
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 5.0, "index": 0},
            {"beat": "hook", "start": 5.0, "end": 10.0, "index": 1},
            {"beat": "pehchan", "start": 10.0, "end": 14.0, "index": 0},
            {"beat": "matlab", "start": 14.0, "end": 18.0, "index": 0},
            {"beat": "karein", "start": 18.0, "end": 22.0, "index": 0},
        ]
        words = []
        timed = [
            ("पूँजी", 0.4), ("₹2,00,000", 1.2), ("है।", 2.0),
            ("दिन", 5.4), ("की", 6.0), ("हद", 6.4), ("₹4,000", 7.4), ("है।", 8.2),
        ]
        for word, start in timed:
            words.append({"word": word, "start": start, "end": start + 0.4})
        board_short._CUE_CACHE.clear()
        texts, figures = board_short.scene_cards_for(spoken, times, scenes, words)
        early = texts["hook"][0] + " " + " ".join(figures["hook"][0])
        self.assertIn("2,00,000", early)
        self.assertNotIn("4,000", early)
        self.assertNotIn("4000", early.replace(",", ""))
        late = texts["hook"][1] + " " + " ".join(figures["hook"][1])
        self.assertIn("4,000", late)
        topic = _topic()
        topic["_scene_cards"] = texts
        topic["_scene_figures"] = figures
        topic["_spoken_beats"] = spoken
        frames, _flags = scene_frames.frames_for(topic, "hook", 2, {"mode": "schematic"})
        import sync_gate
        first = " ".join(sync_gate.lesson_lines(frames[0]))
        second = " ".join(sync_gate.lesson_lines(frames[1]))
        self.assertNotIn("4,000", first)
        self.assertNotIn("4000", first.replace(",", ""))
        self.assertIn("4,000", second)

    def test_a_held_card_is_not_the_same_pixels_four_seconds_apart(self):
        topic = _topic()
        topic["_spoken_beats"] = {"hook": "₹4,000 की हद पहले तय है।"}
        frame = scene_frames.frames_for(topic, "hook", 1, {"mode": "schematic"})[0][0]
        self.assertTrue(callable(getattr(frame, "animate", None)))
        start = np.asarray(frame.animate(0).convert("RGB"), dtype=np.int16)
        later = np.asarray(frame.animate(0.8).convert("RGB"), dtype=np.int16)
        delta = float(np.abs(start - later).mean())
        self.assertGreaterEqual(delta, 1.0)
        self.assertTrue(frame.animate(0.5).text_layers)
        samples = [
            (0.0, np.asarray(frame.animate(0.0).convert("RGB"))),
            (4.0, np.asarray(frame.animate(0.8).convert("RGB"))),
        ]
        self.assertEqual(scene_pace.unchanged_spans(samples), [])

    def test_chart_board_crash_on_the_approved_script_spends_zero_characters(self):
        topic = _topic()
        narration = {
            "title": "दिन की हद",
            "hook_line": "क्यों रुकें?",
            "beats": {beat: "नियम " * 8 for beat in ORDER},
            "source": "approved",
        }

        def prepare(spoken, script, _topic, _c, _spec):
            raw = {beat: 12.0 for beat in ORDER}
            times, _total = board_short._timeline(raw, ORDER)
            audio = {beat: board_short.RUN / f"voice_clean_{beat}.wav" for beat in ORDER}
            return spoken, script, audio, raw, times, _total

        def boom(*_args, **_kwargs):
            raise RuntimeError("Chart Board failed")

        env = {
            "PUBLISH_VISIBILITY": "public",
            "PAID_TTS_ENABLED": "true",
            "ELEVENLABS_API_KEY": "test-key",
            "NARRATION_FROM_RUN": "37095205924",
        }
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(board_short, "prepare_spoken", side_effect=prepare), \
             mock.patch.object(board_short, "_word_timings", return_value=None), \
             mock.patch.object(scene_frames, "frames_for", side_effect=boom), \
             mock.patch.object(board_short, "_eleven_spent", return_value=0), \
             mock.patch("requests.post", side_effect=AssertionError("no ElevenLabs")), \
             redirect_stderr(err):
            for key in ("DRY_RUN", "PIPELINE_MODE", "ELEVENLABS_OPT_IN", "RENDER_SAMPLE"):
                os.environ.pop(key, None)
            with self.assertRaises(SystemExit) as caught:
                board_short.prove_full_render(
                    topic, narration, {"voice": {"engine": "elevenlabs"}}, {"mode": "schematic"},
                    source="approved",
                )
        text = err.getvalue() + str(caught.exception)
        self.assertIn("0 characters spent", text)
        self.assertIn("Chart Board failed", text)
        self.assertNotIn("eleven", text.lower())

    def test_window_sync_and_craft_are_review_and_beat_recall_stays_hold(self):
        beats = [
            {"beat": beat, "recall": 0.65} for beat in ORDER
        ]
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
            self.assertFalse(soft["public_ok"])
            fresh = _hold()
            fresh["notes"] = ["no visual change across 4.0s (8.0-12.0, delta 0.00)"]
            still = board_short.soften_approved_holds(fresh, {"ok": True, "reason": "", "beats": beats})
            self.assertEqual(still["verdict"], "REVIEW")
            missed = board_short.soften_approved_holds(
                _hold(), {"ok": False, "reason": "fact-hook 7.0-10.5s shows 4000 before it is said", "beats": beats},
            )
            self.assertEqual(missed["verdict"], "REVIEW")
            low = list(beats)
            low[1] = {"beat": "pehchan", "recall": 0.33}
            voice = board_short.soften_approved_holds(
                _hold(), {"ok": False, "reason": "recall 0.33", "beats": low},
            )
            self.assertEqual(voice["verdict"], "HOLD")
            self.assertFalse(voice["review_flag"])
            maths = _hold(accuracy=40)
            maths["notes"] = ["wrong number"]
            blocked = board_short.soften_approved_holds(maths, {"ok": True, "beats": beats})
            self.assertEqual(blocked["verdict"], "HOLD")
            area = _hold()
            area["notes"] = ["label outside the 6% safe area: (0,0,10,10)"]
            cut = board_short.soften_approved_holds(area, {"ok": True, "beats": beats})
            self.assertEqual(cut["verdict"], "HOLD")
            self.assertFalse(cut["review_flag"])
        untouched = board_short.soften_approved_holds(_hold(), window)
        self.assertEqual(untouched["verdict"], "HOLD")
        self.assertFalse(untouched["review_flag"])

        review = {
            "public_ok": False,
            "passed": False,
            "review_flag": True,
            "verdict": "REVIEW",
            "overall": 55.8,
            "summary": "window sync",
        }

        def render(*_args, **_kwargs):
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")
            (board_short.RUN / "self_review.json").write_text(
                json.dumps(review), encoding="utf-8",
            )
            return {"final_dur": 55.0}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "_render_short", side_effect=render), \
             mock.patch.object(board_short, "_eleven_spent", return_value=0), \
             mock.patch.dict(os.environ, {"APPROVED": "true"}, clear=False):
            os.environ.pop("DRY_RUN", None)
            allowed = board_short.prove_full_render({}, {}, {}, {})
            self.assertEqual(allowed["final_dur"], 55.0)
            os.environ.pop("APPROVED", None)
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit):
                board_short.prove_full_render({}, {}, {}, {})
            self.assertIn("approved=true", err.getvalue())
            held = dict(review, review_flag=False, verdict="HOLD")
            (Path(tmp) / "self_review.json").write_text(json.dumps(held), encoding="utf-8")

            def render_hold(*_args, **_kwargs):
                return {"final_dur": 55.0}

            os.environ["APPROVED"] = "true"
            err = io.StringIO()
            with mock.patch.object(board_short, "_render_short", side_effect=render_hold), \
                 redirect_stderr(err), self.assertRaises(SystemExit):
                board_short.prove_full_render({}, {}, {}, {})
            self.assertIn("pre-render HOLD", err.getvalue())
            os.environ.pop("APPROVED", None)

    def test_download_unpacks_the_edge_artifact(self):
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w") as bundle:
            bundle.writestr("tts_text.txt", _lines())
        raw = body.getvalue()

        class Resp:
            def __init__(self, payload=None, content=b""):
                self._payload = payload or {}
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        def get(url, headers=None, params=None, timeout=None):
            if url.endswith("/artifacts"):
                return Resp({"artifacts": [{"name": "edge-dry-run", "expired": False, "archive_download_url": "https://example.test/zip"}]})
            return Resp(content=raw)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "o/r"}, clear=False), \
             mock.patch("approved_script.requests.get", side_effect=get):
            approved_script.download_edge_artifact("37095205924", tmp)
            payload = approved_script.load_payload(tmp, "risk_daily_loss_limit")
        self.assertEqual(payload["beats"]["matlab"], "matlab की तय लाइन 3।")


if __name__ == "__main__":
    unittest.main()
