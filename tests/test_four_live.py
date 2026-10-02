"""The default live Short is four beats at Rudra's 1.9 words/s."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import board_short
import sync_gate

ROOT = Path(__file__).resolve().parents[1]
HOOK = "क्या यह सेटअप गलत है?"
CFG = {
    "models": {"script_model": "deepseek-flash"},
    "voice": {"engine": "elevenlabs", "words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
}


def _topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "_live_short": True,
        "_live_beats": "four",
        "_voice": {"words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना"],
    }


def _packed(n, salt, lead=""):
    counters = ["एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ"]
    parts = []
    count = 0
    lead = " ".join(str(lead).split())
    if lead:
        if lead[-1] not in "।?!":
            lead += "।"
        parts.append(lead)
        count = len(lead.split())
    index = 0
    while count < n:
        need = min(4, n - count)
        words = [salt, counters[index % len(counters)], "नियम", "कदम"][:need]
        parts.append(" ".join(words) + "।")
        count += need
        index += 1
    return " ".join(parts)


class FourLiveWindowTests(unittest.TestCase):
    def test_config_default_is_four_and_windows_match_rudra(self):
        data = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(data["live_beats"], "four")
        self.assertEqual(data["formats"]["short"]["live_beats"], "four")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("LIVE_BEATS", None)
            self.assertEqual(board_short.resolve_live_beats({}), "four")
            self.assertEqual(board_short.resolve_live_beats({"_live_beats": "six"}), "six")
        target, total = board_short.four_live_bounds(1.9)
        self.assertEqual(target, (105, 112))
        self.assertEqual(total, (88, 122))
        targets = board_short.four_live_targets(1.9)
        self.assertEqual(sum(targets.values()), 109)
        self.assertGreaterEqual(sum(targets.values()), 105)
        self.assertLessEqual(sum(targets.values()), 112)
        self.assertEqual(set(targets), {"hook", "pehchan", "matlab", "karein"})
        self.assertEqual(board_short.live_voice_bounds(_topic()), (45.0, 65.0))
        self.assertEqual(board_short.four_margin_words(1.9), 114)
        order, _targets, words, band = board_short._lesson(_topic())
        self.assertEqual(order, board_short.BEATS)
        self.assertEqual(words, (88, 122))
        self.assertEqual(band, (105, 112))
        prompt = board_short.four_live_system(_topic())
        self.assertIn("105-112", prompt)
        self.assertIn("55-59", prompt)

    def test_over_sixty_seconds_is_trimmed_before_tts(self):
        topic = _topic()
        counts = {"hook": 30, "pehchan": 40, "matlab": 48, "karein": 42}
        self.assertEqual(sum(counts.values()), 160)
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": HOOK,
            "beats": {
                beat: _packed(counts[beat], beat, HOOK if beat == "hook" else "")
                for beat in board_short.BEATS
            },
            "source": "llm",
        }
        _words, _wps, seconds = board_short._live_estimate(script, topic)
        self.assertGreater(seconds, 65.0)
        seen = []

        def synthesize(spoken, _c, beats=None):
            seen.append(sum(len(str(spoken[beat]).split()) for beat in beats))

        def trim(beats):
            return {beat: 14.0 for beat in beats}

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "claude_json", side_effect=AssertionError("trim must not call the model")), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch.object(board_short, "_trim_rendered", trim), \
             mock.patch.object(board_short, "tts", side_effect=AssertionError("no re-render")):
            board_short.prepare_spoken(dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], 122)
        self.assertGreaterEqual(seen[0], 88)
        self.assertLessEqual(seen[0], board_short.four_margin_words(1.9))

    def test_estimate_still_outside_45_65_spends_no_tts(self):
        topic = _topic()
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": HOOK,
            "beats": {beat: _packed(8, beat, HOOK if beat == "hook" else "") for beat in board_short.BEATS},
            "source": "llm",
        }
        _words, _wps, seconds = board_short._live_estimate(script, topic)
        self.assertLess(seconds, 45.0)
        seen = []

        def synthesize(*_args, **_kwargs):
            seen.append("tts")

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short, "require_scene_frames"), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", return_value="ypnkIsDASPgHZuanrF0q"), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}), \
             mock.patch.object(board_short, "_one_beat_repair", return_value=None), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch("requests.post", side_effect=AssertionError("no ElevenLabs")), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit):
                board_short.prepare_spoken(dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(seen, [])
        self.assertIn("0 characters", err.getvalue())
        self.assertIn("outside 45-65", err.getvalue())


class CraftAndLeakTests(unittest.TestCase):
    def test_craft_samples_missing_frame_returns_three_values(self):
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 2.0, "frame": None},
            {"beat": "karein", "start": 2.0, "end": 4.0},
        ]
        samples, text_frames, notes = board_short._craft_samples(
            scenes, {}, {}, {}, "", "")
        self.assertEqual(samples, [])
        self.assertEqual(text_frames, [])
        self.assertIn("scene has no frame: hook", notes)
        self.assertIn("scene has no frame: karein", notes)

    def test_leak_and_filler_sentences_fail_the_live_gate(self):
        topic = _topic()
        order = board_short.BEATS
        leaks = [
            "उदाहरण: कार्ड पर दिया हिसाब बोलो।",
            "कार्ड पर लिखा है…",
            "अब इसे chart पर पहचानें।",
            "इन features का मतलब समझिए।",
            "अब आगे traders क्या check करते हैं.",
        ]
        for line in leaks:
            notes = board_short.narrator_leak_notes({"matlab": line}, order)
            self.assertTrue(notes, line)
            self.assertIn("matlab", notes[0])
            self.assertEqual(
                board_short.route_gate_failure(notes[0], order),
                ["matlab"],
            )
        clean = "निचली विक बॉडी से 2 गुना होती है।"
        self.assertEqual(board_short.narrator_leak_notes({"matlab": clean}, order), [])
        dumps = (
            "उदाहरण: दो, अट्ठानवे, चार हज़ार, दो लाख",
            "उदाहरण: 0.2, 0.4, 0.6, 0.8, 40, 60",
        )
        for line in dumps:
            notes = board_short.narrator_leak_notes({"karein": line}, order)
            self.assertTrue(notes, line)
            self.assertEqual(board_short.route_gate_failure(notes[0], order), ["karein"])
        worked = "उदाहरण: एंट्री 200, एसएल = 200 − 1.5×2 = 197"
        self.assertEqual(board_short.narrator_leak_notes({"matlab": worked}, order), [])
        rakho = board_short.narrator_leak_notes({"karein": "सीमा के अंदर rakho"}, order)
        self.assertTrue(any("rakho" in note for note in rakho))
        folded = board_short.fold_roman_words("सीमा के अंदर rakho")
        self.assertIn("रखो", folded)
        self.assertNotIn("rakho", folded)
        self.assertEqual(board_short.narrator_leak_notes({"karein": folded}, order), [])
        for line in (
            "यह explanation इसी दिए गए तथ्य तक सीमित है।",
            "किसी अतिरिक्त price, date या return का अनुमान नहीं लगाया जा रहा।",
            "स्क्रीन पर दिख रहे feature को एक साथ देखें।",
        ):
            notes = board_short.narrator_leak_notes({"matlab": line}, order)
            self.assertTrue(notes, line)
        fallback = board_short.fallback_narration(_topic())
        blob = " ".join(fallback["beats"].values())
        self.assertNotIn("explanation इसी दिए गए तथ्य", blob)
        self.assertNotIn("price, date", blob)
        self.assertNotIn("दिख रहे feature", blob)

    def test_plain_images_pass_through_craft_samples_and_render_at(self):
        from PIL import Image
        import scene_transition
        scenes = []
        beat_times = {}
        spoken = {}
        t = 0.0
        for i, beat in enumerate(("hook", "pehchan", "matlab", "karein")):
            scenes.append({
                "beat": beat,
                "start": t,
                "end": t + 3.0,
                "frame": Image.new("RGB", (108, 192), (20 + i * 40, 40, 60)),
            })
            beat_times[beat] = (t, t + 3.0)
            spoken[beat] = "नियम कदम"
            t += 3.0
        self.assertFalse(hasattr(scenes[0]["frame"], "plate"))
        samples, _text_frames, notes = board_short._craft_samples(
            scenes, {}, beat_times, spoken, "", "",
        )
        self.assertGreaterEqual(len(samples), 4)
        self.assertFalse(any("no frame" in note for note in notes))
        mixed = scene_transition.render_at(
            [scene["frame"] for scene in scenes],
            [3.0, 3.0, 3.0, 3.0],
            2.9,
            board_short.SCENE_TRANSITIONS,
            board_short.SCENE_FADE_S,
        )
        self.assertEqual(mixed.size, (108, 192))
        self.assertEqual(mixed.mode, "RGB")

    def test_four_beat_opening_card_fills_the_frame(self):
        import numpy as np
        import scene_frames
        import scene_pace
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == "risk_daily_loss_limit")
        topic = json.loads(json.dumps(topic))
        topic["_live_short"] = True
        topic["_live_beats"] = "four"
        spec = {"mode": "schematic"}
        import safe_area
        banned = ("POSITION SIZE", "BREAK IT", "LOSS %", "RECOVERY MATH", "CAUSE")
        for beat in board_short.BEATS:
            frames, flags = scene_frames.frames_for(topic, beat, 3, spec)
            self.assertEqual(len(frames), 3)
            self.assertEqual(flags, [False, False, False])
            for frame in frames:
                frac = scene_pace.empty_row_fraction(frame)
                self.assertLessEqual(frac, 0.20, f"{beat} empty {frac:.2f}")
                rows = np.asarray(frame.convert("RGB")).max(axis=2) > 80
                bottom = rows[int(rows.shape[0] * 0.70):]
                self.assertGreaterEqual(float(bottom.any(axis=1).mean()), 0.90, beat)
                self.assertGreater(float(np.asarray(frame).std()), 8.0)
                ok, bad = safe_area.check_boxes(frame.label_boxes)
                self.assertTrue(ok, bad)
                self.assertEqual(scene_frames.frames_safe_area_notes([frame]), [])
                blob = scene_frames.frame_card_text(frame)
                for phrase in banned:
                    self.assertNotIn(phrase, blob)

    def test_two_topics_paint_their_own_numbers(self):
        import scene_frames
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]

        def card(key, beat):
            topic = json.loads(json.dumps(next(t for t in bank if t["key"] == key)))
            topic["_live_short"] = True
            topic["_live_beats"] = "four"
            frame = scene_frames.frames_for(topic, beat, 1, {"mode": "schematic"})[0][0]
            return scene_frames.frame_card_text(frame)

        loss = [card("risk_daily_loss_limit", beat) for beat in board_short.BEATS]
        journal = [card("risk_journal", beat) for beat in board_short.BEATS]
        self.assertNotEqual(loss[0], journal[0])
        self.assertNotEqual(" ".join(loss), " ".join(journal))
        loss_blob = " ".join(loss)
        journal_blob = " ".join(journal)
        self.assertIn("4,000", loss_blob)
        self.assertIn("2%", loss_blob)
        self.assertIn("2 लाख", loss_blob)
        self.assertIn("40%", journal_blob)
        self.assertIn("2R", journal_blob)
        self.assertIn("0.2", journal_blob)

    def test_spoken_cards_follow_the_scene_and_not_a_later_number(self):
        import scene_frames
        import scene_pace
        import sync_gate
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        beats = {
            "hook": "क्या दिन का घाटा रुकना चाहिए? एक खराब दिन पूँजी खा जाता है। लिखी सीमा नहीं होती। यह नियम है।",
            "pehchan": "पूँजी दो लाख है। दिन की नुकसान सीमा दो प्रतिशत है। यह नियम से रखी जाती है। चार हजार रुपये पूरे होते हैं। फिर कारोबार रोक दिया जाता है।",
            "matlab": "इसका मतलब यह उदाहरण है। एक दिन का नुकसान महीने की कमाई मिटा सकता है। चार हजार की सीमा पर दिन रुकता है। बाकी पूँजी अगले दिन सुरक्षित रहती है।",
            "karein": "गलती यह होती है। सीमा हिट होने के बाद नुकसान वापस निकालने की कोशिश रहती है। व्यापारी हद पहले लिखते हैं। हिट होते ही टर्मिनल बंद कर देते हैं। कदम यही है।",
        }
        topic = json.loads(json.dumps(next(t for t in bank if t["key"] == "risk_daily_loss_limit")))
        topic["_live_short"] = True
        topic["_live_beats"] = "four"
        topic["_spoken_beats"] = dict(beats)
        order = list(board_short.BEATS)
        beat_times = {}
        cursor = 0.0
        wps = 1.9
        for beat in order:
            dur = max(4.0, len(beats[beat].split()) / wps)
            beat_times[beat] = (cursor, cursor + dur)
            cursor += dur
        planned = scene_pace.plan_scenes(beat_times, beats=order)
        groups = {}
        for scene in planned:
            groups.setdefault(scene["beat"], []).append(scene)
        words = []
        show_rows = []
        hook_text = []
        pehchan_text = []
        for beat in order:
            group = groups[beat]
            frames, _flags = scene_frames.frames_for(topic, beat, len(group), {"mode": "schematic"})
            self.assertEqual(len(frames), len(group))
            t0, t1 = beat_times[beat]
            toks = beats[beat].split()
            step = (t1 - t0) / len(toks)
            for i, tok in enumerate(toks):
                words.append({
                    "word": tok,
                    "start": t0 + i * step,
                    "end": t0 + (i + 1) * step - 0.01,
                })
            for scene, frame in zip(group, frames):
                text = scene_frames.frame_card_text(frame)
                if beat == "hook":
                    hook_text.append(text)
                if beat == "pehchan":
                    pehchan_text.append(text)
                show_rows.append({
                    "start": scene["start"],
                    "end": scene["end"],
                    "beat": beat,
                    "kind": frame.scene_kind,
                    "show": sync_gate.lesson_lines(frame),
                })
        report = sync_gate.check(
            words, show_rows, beats, beat_times, ["दिन", "नुकसान", "सीमा"])
        self.assertNotIn("before it is said", report["reason"])
        self.assertNotIn("on-screen text is not what this scene says", report["reason"])
        self.assertTrue(report["ok"], report["reason"])
        self.assertFalse(any("4,000" in text or "4000" in text for text in hook_text))
        self.assertTrue(any("4,000" in text or "लाख" in text for text in pehchan_text))

    def test_text_outside_the_safe_area_fails_the_render_check(self):
        from PIL import Image
        import scene_frames
        img = Image.new("RGB", (1080, 1920), (30, 50, 90))
        img.label_boxes = [(-8.0, -8.0, 30.0, 30.0)]
        notes = scene_frames.frames_safe_area_notes([img])
        self.assertTrue(any("safe area" in note for note in notes))

    def test_short_karein_expands_from_topic_facts(self):
        import hook_check
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = json.loads(json.dumps(next(t for t in bank if t["key"] == "risk_overtrading")))
        topic["_live_short"] = True
        topic["_live_beats"] = "four"
        topic["_voice"] = {"words_per_second": 1.9}
        order, targets, _total, _band = board_short._lesson(topic)
        beats = {}
        short = None
        for beat in order:
            lo, hi = board_short.beat_word_window(targets[beat], board_short.LIVE_BEAT_TOLERANCE)
            count = lo if beat != "karein" else lo - 1
            short = (lo, hi)
            beats[beat] = " ".join(["गलती"] * count)
        narration = {"beats": beats}
        before = len(narration["beats"]["karein"].split())
        board_short.expand_short_live_beats(narration, topic)
        after = narration["beats"]["karein"].split()
        lo, hi = short
        self.assertEqual(before, lo - 1)
        self.assertGreaterEqual(len(after), lo)
        self.assertLessEqual(len(after), hi)
        allowed = hook_check.allowed_numbers(topic)
        self.assertTrue(hook_check.extract_numbers(" ".join(after)) <= allowed)

    def test_craft_hold_blocks_before_any_tts(self):
        from PIL import Image
        topic = _topic()
        script = {
            "title": "सेटअप का नियम समझिए",
            "hook_line": HOOK,
            "beats": {beat: _packed(12, beat, HOOK if beat == "hook" else "") for beat in board_short.BEATS},
            "source": "llm",
        }
        seen = []

        def dark_frames(_topic, _beat, n, _spec):
            img = Image.new("RGB", (1080, 1920), (8, 13, 26))
            return [img.copy() for _ in range(n)], [False] * n

        def synthesize(*_args, **_kwargs):
            seen.append("tts")

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short.credit_log, "PATH", Path(tmp) / "credits.json"), \
             mock.patch.object(board_short.scene_frames, "frames_for", side_effect=dark_frames), \
             mock.patch.object(board_short.eleven_voice, "ensure_library_voice", side_effect=AssertionError("no voice api")), \
             mock.patch.object(board_short, "synthesize_short_voices", synthesize), \
             mock.patch("requests.post", side_effect=AssertionError("no ElevenLabs")), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit):
                board_short.prepare_spoken(dict(script["beats"]), script, topic, CFG, {})
        self.assertEqual(seen, [])
        text = err.getvalue()
        self.assertIn("0 characters", text)
        self.assertIn("craft would HOLD", text)
        self.assertTrue(
            "empty background" in text or "visual change" in text,
            text,
        )

    def test_voice_cache_requires_the_beat_text(self):
        import asyncio
        topic_voice = {
            "voice": {"voice_id": "voice-a", "speed": 0.9, "words_per_second": 1.9},
        }
        calls = []

        def fake_run(coro):
            coro.close()
            calls.append("edge")
            path.write_bytes(b"z" * 1500)

        saved = {
            key: os.environ.get(key)
            for key in ("DRY_RUN", "ELEVENLABS_API_KEY", "ELEVENLABS_OPT_IN", "PUBLISH_VISIBILITY")
        }
        try:
            os.environ["DRY_RUN"] = "1"
            os.environ.pop("ELEVENLABS_API_KEY", None)
            os.environ.pop("ELEVENLABS_OPT_IN", None)
            os.environ.pop("PUBLISH_VISIBILITY", None)
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(board_short, "RUN", Path(tmp)), \
                 mock.patch.object(asyncio, "run", side_effect=fake_run):
                path = Path(tmp) / "tts_hook.mp3"
                path.write_bytes(b"a" * 2000)
                (Path(tmp) / "tts_hook.engine").write_text("edge", encoding="utf-8")
                (Path(tmp) / "tts_hook.textid").write_text(
                    board_short.voice_cache_id("पुराना पाठ", topic_voice), encoding="utf-8")
                board_short._reset_voice_state()
                board_short.tts("नया पाठ नियम", path, topic_voice)
                self.assertEqual(calls, ["edge"])
                calls.clear()
                board_short.tts("नया पाठ नियम", path, topic_voice)
                self.assertEqual(calls, [])
                other = {"voice": {"voice_id": "voice-b", "speed": 0.9}}
                board_short.tts("नया पाठ नियम", path, other)
                self.assertEqual(calls, ["edge"])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class HindiNumberTests(unittest.TestCase):
    def test_scale_words_and_title_equivalents(self):
        self.assertIn("4000", sync_gate.tokens("चार हज़ार"))
        self.assertIn("200000", sync_gate.tokens("दो लाख"))
        self.assertIn("2.5", sync_gate.tokens("ढाई"))
        self.assertIn("1.5", sync_gate.tokens("डेढ़"))
        self.assertNotIn("रुपये", sync_gate.tokens("चार हजार रुपये"))
        self.assertNotIn("प्रतिशत", sync_gate.tokens("2 प्रतिशत"))
        beats = {
            "hook": "रोज़ का नुकसान सीमा के अंदर रखो",
            "pehchan": "पूँजी दो लाख पर नियम है",
            "matlab": "दिन का नुकसान चार हजार पर बंद होता है",
            "karein": "फिर ट्रेड बंद कर दो कदम नियम",
        }
        times = {
            "hook": (0.0, 4.0), "pehchan": (4.0, 8.0),
            "matlab": (8.0, 12.0), "karein": (12.0, 16.0),
        }
        spoken = {
            "hook": "रोज का नुकसान सीमा के अंदर रखो",
            "pehchan": "पूंजी दो लाख पर नियम है",
            "matlab": "दिन का नुकसान चार हजार पर बंद होता है",
            "karein": "फिर ट्रेड बंद कर दो कदम नियम",
        }
        words = []
        for beat, (start, end) in times.items():
            toks = spoken[beat].split()
            step = (end - start) / len(toks)
            for i, tok in enumerate(toks):
                words.append({
                    "word": tok,
                    "start": start + i * step,
                    "end": start + (i + 1) * step - 0.01,
                })
        scenes = [
            {"start": a, "end": b, "beat": beat, "kind": beat, "show": [beats[beat]]}
            for beat, (a, b) in times.items()
        ]
        scenes[1] = dict(scenes[1], show=["200000"])
        scenes[2] = dict(scenes[2], show=["4000"])
        report = sync_gate.check(
            words, scenes, beats, times, ["Daily", "Loss", "Limit", "बंद", "4000"])
        self.assertNotIn("title promise", report["reason"])
        self.assertNotIn("before it is said", report["reason"])
        missed = sync_gate.check(words, scenes, beats, times, ["SIP"])
        self.assertIn("SIP", missed["reason"])
        self.assertIn("title promise", missed["reason"])
