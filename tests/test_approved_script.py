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
import common
import credit_log
import narration_dry_run
import run_verify
import scene_frames
import scene_pace
import self_review
import speak

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
    def tearDown(self):
        approved_script._LOCKED = {}

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
        self.assertIn("render_only:", live)
        self.assertIn("run/sc_*.png", live)
        self.assertIn("install_repo_beats", live)
        self.assertIn("ypnkIsDASPgHZuanrF0q", live)
        publish = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        self.assertIn("PUBLISH_SLOT_IST:", publish)
        self.assertIn("PUBLISH_AT_UTC:", publish)
        self.assertIn('default: "16:00"', publish)
        self.assertIn("test -s run/thumbnail.jpg", publish)
        self.assertIn("slot_guard.py --publish-at", publish)
        self.assertIn("python publish_instagram.py", publish)

    def test_committed_hinglish_replaces_the_dry_run_before_the_hash_lock(self):
        import slot_guard
        approved = json.loads(
            (ROOT / "approved" / "risk_daily_loss_limit.json").read_text(encoding="utf-8")
        )
        total = sum(len(approved["beats"][beat]) for beat in ORDER)
        self.assertEqual(
            [len(approved["beats"][beat]) for beat in ORDER],
            [96, 182, 189, 186],
        )
        self.assertEqual(total, 653)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tts_risk_daily_loss_limit.txt").write_text(_lines(), encoding="utf-8")
            installed = approved_script.install_repo_beats(root, "risk_daily_loss_limit")
            self.assertEqual(installed["title"], "डेली लॉस लिमिट")
            self.assertEqual(installed["beats"]["pehchan"], approved["beats"]["pehchan"])
            self.assertNotIn("पूँजी", installed["beats"]["hook"])
            script = approved_script.narration_from_payload(
                approved_script.load_payload(root, "risk_daily_loss_limit"),
                _topic(),
                "37095205924",
            )
        self.assertEqual(
            script["approved_hashes"]["pehchan"],
            approved_script.beat_digest(approved["beats"]["pehchan"]),
        )
        self.assertEqual(script["beats"]["karein"], approved["beats"]["karein"])
        out = io.StringIO()
        with redirect_stdout(out):
            slot_guard.emit_publish_at("16:00", "2026-10-04")
        lines = dict(part.split("=", 1) for part in out.getvalue().splitlines())
        self.assertEqual(lines["slot"], "16:00")
        self.assertEqual(lines["date_ist"], "2026-10-04")
        self.assertEqual(lines["publish_at"], "2026-10-04T10:30:00Z")
        with self.assertRaises(SystemExit):
            slot_guard.emit_publish_at("16:00", "2020-01-01")

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


OWNER_PEHCHAN = (
    "अब इसे पहचानें। पूँजी दो लाख रुपये है, तो दिन की हद दो प्रतिशत यानी "
    "चार हज़ार रुपये बैठती है। यह चार हज़ार रुपये दिन का कुल नुकसान है, "
    "हर ट्रेड का नहीं। यह हद लिखी हुई रहती है।"
)
DRY_PEHCHAN = (
    "अब इसे पहचानें: दो लाख रुपये की पूँजी पर दो प्रतिशत की हद। "
    "उदाहरण: पूँजी दो लाख रुपये, दिन की हद दो प्रतिशत बराबर चार हज़ार रुपये। "
    "यानी चार हज़ार रुपये का नुकसान या चार हज़ार रुपये लाभ, दोनों। यही हद है।"
)


class ApprovedHashLockTests(unittest.TestCase):
    def tearDown(self):
        approved_script._LOCKED = {}

    def test_voiced_text_matches_the_approved_beat_byte_for_byte(self):
        line = "₹4,000 की हद।"
        self.assertNotEqual(speak.speakable(line), line)
        beats = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        beats["pehchan"] = line
        script = {"beats": beats, "source": "approved"}
        approved_script.remember(script)
        self.assertEqual(script["approved_hashes"]["pehchan"], approved_script.beat_digest(line))
        sent = {}

        class Resp:
            status_code = 200
            content = b"x" * 3000
            headers = {}

        def post(url, headers=None, json=None, timeout=None):
            sent["url"] = url
            sent["text"] = json["text"]
            sent["speed"] = json["voice_settings"]["speed"]
            return Resp()

        env = {
            "NARRATION_FROM_RUN": "37095205924",
            "PAID_TTS_ENABLED": "true",
            "ELEVENLABS_API_KEY": "test-key",
            "PUBLISH_VISIBILITY": "public",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("requests.post", side_effect=post):
            board_short.tts(
                line,
                Path(tmp) / "tts_pehchan.mp3",
                {"voice": {"voice_id": "ypnkIsDASPgHZuanrF0q", "speed": 0.90}},
            )
            logged = (Path(tmp) / "tts_text.txt").read_text(encoding="utf-8")
            self.assertGreater(credit_log.snapshot()["elevenlabs_characters_sent"], 0)
        self.assertEqual(sent["text"], line)
        self.assertEqual(logged, f"tts_pehchan: {line}\n")
        self.assertIn("ypnkIsDASPgHZuanrF0q", sent["url"])
        self.assertEqual(sent["speed"], 0.90)

    def test_a_hash_mismatch_spends_zero_characters(self):
        beats = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        approved_script.remember({"beats": beats, "source": "approved"})
        env = {
            "NARRATION_FROM_RUN": "37095205924",
            "PAID_TTS_ENABLED": "true",
            "ELEVENLABS_API_KEY": "test-key",
            "PUBLISH_VISIBILITY": "public",
        }
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("requests.post", side_effect=AssertionError("paid call")), \
             redirect_stderr(err), self.assertRaises(SystemExit):
            board_short.tts(
                beats["pehchan"] + " बदल गया।",
                Path(tmp) / "tts_pehchan.mp3",
                {"voice": {"voice_id": "ypnkIsDASPgHZuanrF0q", "speed": 0.90}},
            )
            self.assertEqual(credit_log.snapshot()["elevenlabs_characters_sent"], 0)
        self.assertIn("0 characters spent", err.getvalue())

    def test_fold_and_a_narration_deepseek_call_do_not_touch_approved_beats(self):
        spoken = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        spoken["hook"] = "daily limit की लाइन।"
        approved_script.remember({"beats": spoken, "source": "approved"})
        seen = []

        def fake_tts(text, path, c):
            seen.append(text)

        env = {"NARRATION_FROM_RUN": "37095205924"}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(board_short, "tts", side_effect=fake_tts):
            board_short.synthesize_short_voices(spoken, {"voice": {"engine": "elevenlabs"}}, beats=("hook",))
        self.assertEqual(seen, ["daily limit की लाइन।"])
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
             redirect_stderr(err), self.assertRaises(SystemExit):
            common.claude(board_short.four_live_system(_topic()), "rewrite the beats", retries=1, attempts=1)
        self.assertIn("must not rewrite narration", err.getvalue())
        self.assertIn("0 characters spent", err.getvalue())
        self.assertFalse(common.narration_llm_blocked(run_verify.FINAL_SYSTEM))

    def test_hindi_amounts_draw_a_rupee_chart_without_the_example_label(self):
        spoken = {
            "hook": "हुक।",
            "pehchan": DRY_PEHCHAN,
            "matlab": "मतलब।",
            "karein": "करें।",
        }
        times = {
            "hook": (0.0, 4.0),
            "pehchan": (4.0, 20.0),
            "matlab": (20.0, 28.0),
            "karein": (28.0, 36.0),
        }
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 4.0, "index": 0},
            {"beat": "pehchan", "start": 4.0, "end": 12.0, "index": 0},
            {"beat": "pehchan", "start": 12.0, "end": 20.0, "index": 1},
            {"beat": "matlab", "start": 20.0, "end": 28.0, "index": 0},
            {"beat": "karein", "start": 28.0, "end": 36.0, "index": 0},
        ]
        words = spoken["pehchan"].split()
        # "चार हज़ार" first appears after the 12s cut.
        cut = 12.0
        audio = []
        for index, word in enumerate(words):
            start = 4.2 + index * ((20.0 - 4.2) / len(words))
            audio.append({"word": word, "start": start, "end": start + 0.2})
        first_hazar = next(
            item["start"] for item in audio
            if "हज़ार" in item["word"] or "हजार" in item["word"]
        )
        self.assertGreater(first_hazar, cut)
        board_short._CUE_CACHE.clear()
        texts, figures = board_short.scene_cards_for(spoken, times, scenes, audio)
        early = " ".join(texts["pehchan"][0]) + " " + " ".join(figures["pehchan"][0])
        late = " ".join(texts["pehchan"][1]) + " " + " ".join(figures["pehchan"][1])
        self.assertNotIn("उदाहरण:", early)
        self.assertNotIn("उदाहरण:", late)
        self.assertIn("ये उदाहरण है", scene_frames.strip_example_label("ये उदाहरण है"))
        self.assertIn("2,00,000", early)
        self.assertNotIn("4,000", early)
        self.assertIn("4,000", late)
        self.assertIn("2,00,000", late)
        topic = _topic()
        topic["_scene_cards"] = {"pehchan": texts["pehchan"]}
        topic["_scene_figures"] = {"pehchan": figures["pehchan"]}
        topic["_spoken_beats"] = spoken
        frames, _flags = scene_frames.frames_for(topic, "pehchan", 2, {"mode": "schematic"})
        card = scene_frames.frame_card_text(frames[1])
        self.assertNotIn("उदाहरण:", card)
        bars = getattr(frames[1], "chart_bar_boxes", None) or []
        self.assertGreaterEqual(len(bars), 2)
        large = max(bars, key=lambda bar: bar[4])
        small = min(bars, key=lambda bar: bar[4])
        if (large[2] - large[0]) >= (large[3] - large[1]):
            ratio = (large[2] - large[0]) / max(1.0, small[2] - small[0])
        else:
            ratio = (large[3] - large[1]) / max(1.0, small[3] - small[1])
        self.assertGreater(ratio, 8)
        labels = " ".join(sync_gate_lines(frames[1]))
        self.assertIn("₹2,00,000", labels)
        self.assertIn("₹4,000", labels)
        owner_figs = scene_frames._spoken_figures(
            OWNER_PEHCHAN, scene_frames._digit_tokens(OWNER_PEHCHAN),
        )
        self.assertIn("₹2,00,000", owner_figs)
        self.assertIn("₹4,000", owner_figs)
        self.assertNotIn("उदाहरण:", OWNER_PEHCHAN)


def sync_gate_lines(frame):
    import sync_gate
    return sync_gate.lesson_lines(frame)


class _SpeakResp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.headers = headers or {}
        if isinstance(body, (bytes, bytearray)):
            self.content = bytes(body)
            self.text = ""
            self._json = None
        else:
            self._json = body
            self.text = json.dumps(body)
            self.content = self.text.encode("utf-8")

    def json(self):
        return self._json


# The render-only dispatch: privacy unlisted, approved script, paid switch on.
RENDER_ONLY_ENV = {
    "PAID_TTS_ENABLED": "true",
    "APPROVED": "true",
    "NARRATION_FROM_RUN": "37095205924",
    "PUBLISH_VISIBILITY": "unlisted",
    "ELEVENLABS_VOICE_ID": "ypnkIsDASPgHZuanrF0q",
    "ELEVENLABS_API_KEY": "test-key",
    "VIDEO_FORMAT": "short",
}


class RenderOnlyRudraTests(unittest.TestCase):
    def tearDown(self):
        approved_script._LOCKED = {}
        board_short._reset_voice_state()

    def _quiet_env(self, extra):
        env = dict(RENDER_ONLY_ENV)
        env.update(extra)
        for key in (
            "DRY_RUN", "RENDER_SAMPLE", "SAMPLE_NO_UPLOAD", "ELEVENLABS_OPT_IN",
            "PIPELINE_MODE", "PROOF_FREE_AUDIO",
        ):
            env.setdefault(key, "")
        return env

    def test_unlisted_approved_narration_from_run_uses_rudra_at_0_90(self):
        import eleven_voice
        self.assertEqual(eleven_voice.LIVE_CREDIT_FLOOR, 1000)
        spoken = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        posts = []

        def get(url, **_kwargs):
            if "subscription" in url:
                return _SpeakResp(200, {
                    "character_count": 0,
                    "character_limit": 100000,
                    "status": "active",
                })
            return _SpeakResp(200, {"voice_id": "ypnkIsDASPgHZuanrF0q"})

        def post(url, **kwargs):
            posts.append((url, kwargs.get("json") or {}))
            return _SpeakResp(200, b"E" * 2500, headers={"character-cost": "12"})

        async def edge(*_args):
            raise AssertionError("Edge must not speak this approved render")

        env = self._quiet_env({})
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("requests.get", get), \
             mock.patch("requests.post", post), \
             mock.patch.object(board_short, "_edge", edge), \
             redirect_stdout(io.StringIO()):
            for key, value in env.items():
                if value == "":
                    os.environ.pop(key, None)
            self.assertTrue(eleven_voice.paid_voice_allowed())
            c = common.cfg()
            self.assertEqual(c["voice"]["voice_id"], "ypnkIsDASPgHZuanrF0q")
            self.assertEqual(c["voice"]["speed"], 0.90)
            topic = _topic()
            self.assertTrue(board_short._live_lesson(c))
            topic["_live_short"] = True
            self.assertTrue(board_short._needs_paid_proof(topic, c))
            approved_script.remember({"beats": spoken, "source": "approved"})
            engine = board_short.synthesize_short_voices(spoken, c, beats=ORDER)
            credits = json.loads((Path(tmp) / "credits.json").read_text(encoding="utf-8"))
        self.assertEqual(engine, "elevenlabs")
        self.assertEqual(len(posts), len(ORDER))
        for url, payload in posts:
            self.assertIn("/text-to-speech/ypnkIsDASPgHZuanrF0q", url)
            self.assertEqual(payload["voice_settings"]["speed"], 0.90)
        self.assertGreater(credits["elevenlabs_characters_sent"], 0)
        self.assertEqual(credits["tts_engine"], "elevenlabs")
        self.assertEqual(credits["deepseek_calls"], 0)

        blocked = [
            {"APPROVED": "false"},
            {"PAID_TTS_ENABLED": "false"},
            {"NARRATION_FROM_RUN": ""},
            {"DRY_RUN": "1"},
            {"PIPELINE_MODE": "repair"},
            {"RENDER_SAMPLE": "1"},
        ]
        for extra in blocked:
            with self.subTest(extra=extra):
                with mock.patch.dict(os.environ, self._quiet_env(extra), clear=False):
                    self.assertFalse(eleven_voice.paid_voice_allowed())

    def test_below_the_1000_floor_speaks_the_approved_script_on_edge(self):
        spoken = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        approved_script.remember({"beats": spoken, "source": "approved"})
        locked = dict(spoken)
        calls = []

        def low(_url, **_kwargs):
            return _SpeakResp(200, {
                "character_count": 10000 - 999,
                "character_limit": 10000,
                "status": "active",
            })

        def post(*_args, **_kwargs):
            raise AssertionError("below the floor must not POST")

        async def edge(text, voice, rate, path):
            calls.append((text, voice, rate))
            path.write_bytes(b"e" * 2000)

        env = self._quiet_env({"GITHUB_STEP_SUMMARY": ""})
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("requests.get", low), \
             mock.patch("requests.post", post), \
             mock.patch.object(board_short, "_edge", edge), \
             redirect_stdout(io.StringIO()):
            for key, value in env.items():
                if value == "":
                    os.environ.pop(key, None)
            os.environ["GITHUB_STEP_SUMMARY"] = str(Path(tmp) / "summary.txt")
            engine = board_short.synthesize_short_voices(spoken, common.cfg(), beats=ORDER)
            meta = json.loads((Path(tmp) / "voice_meta.json").read_text(encoding="utf-8"))
            tts_text = (Path(tmp) / "tts_text.txt").read_text(encoding="utf-8")
            summary = (Path(tmp) / "summary.txt").read_text(encoding="utf-8")
        self.assertEqual(engine, "edge")
        self.assertEqual(spoken, locked)
        self.assertEqual(len(calls), len(ORDER))
        for beat, (text, voice, rate) in zip(ORDER, calls):
            self.assertEqual(text, locked[beat])
            self.assertEqual(voice, "hi-IN-MadhurNeural")
            self.assertEqual(rate, "+0%")
            self.assertIn(locked[beat], tts_text)
        self.assertEqual(meta["engine"], "edge")
        self.assertIn("1000", meta["fallback_reason"])
        self.assertIn("voice_engine=edge", summary)

    def test_the_hash_lock_still_stops_before_a_post(self):
        spoken = {beat: f"{beat} की तय लाइन।" for beat in ORDER}
        approved_script.remember({"beats": spoken, "source": "approved"})
        env = self._quiet_env({})

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("requests.post", side_effect=AssertionError("hash mismatch must not POST")), \
             redirect_stderr(err):
            for key, value in env.items():
                if value == "":
                    os.environ.pop(key, None)
            with self.assertRaises(SystemExit):
                board_short.tts(
                    spoken["pehchan"] + " बदल गया।",
                    Path(tmp) / "tts_pehchan.mp3",
                    common.cfg(),
                )
            self.assertEqual(credit_log.snapshot()["elevenlabs_characters_sent"], 0)
        self.assertIn("0 characters spent", err.getvalue())

    def test_approved_narration_makes_zero_deepseek_calls(self):
        topic = _topic()
        env = self._quiet_env({"APPROVED_SCRIPT_DIR": ""})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "narration.json").write_text(
                json.dumps({
                    "title": "डेली लॉस लिमिट",
                    "hook_line": "डेली लॉस लिमिट क्यों ज़रूरी है?",
                    "beats": {beat: f"{beat} की तय लाइन।" for beat in ORDER},
                    "source": "approved",
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            env["APPROVED_SCRIPT_DIR"] = str(root)
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(board_short, "RUN", root / "run"), \
                 mock.patch.object(credit_log, "PATH", root / "credits.json"), \
                 mock.patch.object(board_short, "claude_json", side_effect=AssertionError("narration DeepSeek")), \
                 mock.patch.object(common, "claude", side_effect=AssertionError("narration DeepSeek")), \
                 redirect_stdout(io.StringIO()):
                (root / "run").mkdir()
                for key, value in env.items():
                    if value == "":
                        os.environ.pop(key, None)
                script, source = board_short.narration(topic, common.cfg())
                saved = credit_log.snapshot()
                self.assertFalse(common.narration_llm_blocked(run_verify.FINAL_SYSTEM))
                self.assertTrue(common.narration_llm_blocked(board_short.four_live_system(topic)))
            self.assertEqual(source, "approved")
            self.assertEqual(script["beats"]["pehchan"], "pehchan की तय लाइन।")
            self.assertEqual(saved["deepseek_calls"], 0)

    def test_approved_script_keeps_the_rudra_duration_window(self):
        import auto_agent
        import quality_check
        self.assertEqual(board_short.APPROVED_VOICE_SECONDS, (45.0, 85.0))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NARRATION_FROM_RUN", None)
            self.assertEqual(board_short.live_voice_bounds(_topic()), (45.0, 65.0))
        with mock.patch.dict(os.environ, {"NARRATION_FROM_RUN": "37095205924"}, clear=False):
            self.assertEqual(
                board_short.live_voice_bounds(_topic()),
                board_short.APPROVED_VOICE_SECONDS,
            )
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "narration.json").write_text(json.dumps({
                "source": "approved",
                "live_beats": "four",
                "beats": {beat: "लाइन" for beat in ORDER},
            }), encoding="utf-8")
            with mock.patch.object(quality_check, "RUN", run):
                self.assertEqual(quality_check.short_duration_bounds(), (45.0, 85.0, "45-85s"))
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window(), (45.0, 85.0, "45-85s"))
            (run / "narration.json").write_text(json.dumps({
                "live_beats": "four",
                "beats": {"hook": "सवाल", "karein": "कदम"},
            }), encoding="utf-8")
            with mock.patch.object(quality_check, "RUN", run):
                self.assertEqual(quality_check.short_duration_bounds(), (45.0, 65.0, "45-65s"))
            with mock.patch.object(auto_agent, "RUN", run):
                self.assertEqual(auto_agent.short_duration_window(), (45.0, 65.0, "45-65s"))


if __name__ == "__main__":
    unittest.main()
