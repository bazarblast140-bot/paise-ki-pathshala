"""Rebuild a short from voice.wav. No TTS client and no model call."""
import json
import unittest
import wave
from pathlib import Path

import rerender_from_voice

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """TITLE:
Piercing Line — गिरावट में रिवर्सल?

HOOK (0-3 sec):
हरी कैंडल ने लाल का 70% काटा, क्या रिवर्सल आएगा? इसे पियर्सिंग लाइन कहते हैं।

BODY:
[SEGMENT PEHCHAN]
पहचान आसान है। पहले दिन बड़ी लाल कैंडल, 100 से 90।

[SEGMENT MATLAB]
हरी कैंडल 88 पर खुलती है और 97 पर बंद होती है।

[SEGMENT KAREIN]
स्टॉप लॉस 87। अगली हरी कैंडल ऊपर बंद हो, वही कन्फर्मेशन कैंडल है।

CTA:
और सीखें

DISCLAIMER LINE:
शिक्षा मात्र।

FACT LEDGER:
- स्टॉप लॉस 87
"""


def _tone(path, seconds=0.4, rate=16000):
    import math
    import struct
    n = int(seconds * rate)
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate)))
        for i in range(n)
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


class RerenderParseTests(unittest.TestCase):
    def test_script_and_review_times(self):
        parsed = rerender_from_voice.parse_script(SCRIPT)
        self.assertEqual(parsed["title"], "Piercing Line — गिरावट में रिवर्सल?")
        self.assertTrue(parsed["hook_line"].endswith("?"))
        self.assertIn("कन्फर्मेशन", parsed["beats"]["karein"])
        self.assertIn("70%", parsed["beats"]["hook"])
        review = {"scenes": [
            {"beat": "hook", "start": 0, "end": 10},
            {"beat": "pehchan", "start": 10, "end": 20},
            {"beat": "matlab", "start": 20, "end": 30},
            {"beat": "karein", "start": 30, "end": 40},
        ]}
        same = rerender_from_voice.beat_times_from_review(review, 40.0, parsed["beats"])
        self.assertEqual(same["karein"], (30.0, 40.0))
        scaled = rerender_from_voice.beat_times_from_review(review, 20.0, parsed["beats"])
        self.assertAlmostEqual(scaled["karein"][1], 20.0, places=2)
        partial = {"scenes": [{"beat": "hook", "start": 0, "end": 10}]}
        self.assertIsNone(rerender_from_voice.beat_times_from_review(partial, 10.0, parsed["beats"]))

    def test_stored_words_win_and_whisper_failure_uses_boundaries(self):
        beats = {"hook": "एक दो", "pehchan": "", "matlab": "", "karein": ""}
        times = {"hook": (0.0, 2.0)}
        run = Path(self.id().replace(".", "_"))
        # No folder: the helper only reads files that exist.
        called = {"n": 0}

        def boom(_path):
            called["n"] += 1
            raise RuntimeError("no model")

        import sync_gate
        original = sync_gate.transcribe_file
        sync_gate.transcribe_file = boom
        try:
            words = rerender_from_voice.align_words(Path("/tmp/missing-run-dir"), Path("voice.wav"), beats, times)
        finally:
            sync_gate.transcribe_file = original
        self.assertEqual(called["n"], 1)
        self.assertEqual([item["word"] for item in words], ["एक", "दो"])
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[-1]["end"], 2.0)

    def test_source_does_not_call_tts_or_deepseek(self):
        text = (ROOT / "rerender_from_voice.py").read_text(encoding="utf-8").lower()
        for banned in ("elevenlabs", "edge_tts", "deepseek", "prepare_spoken", "narration("):
            self.assertNotIn(banned, text)
        self.assertIn("loudnorm=i=-14:tp=-1.5:lra=11", text)
        self.assertIn("mix_into", text)


class RerenderCliTests(unittest.TestCase):
    def test_rebuilds_mp4_and_thumbnail_without_touching_voice(self):
        import tempfile
        import sync_gate
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            _tone(run / "voice.wav", seconds=0.45)
            original = (run / "voice.wav").read_bytes()
            (run / "script.txt").write_text(SCRIPT, encoding="utf-8")
            (run / "verify_meta.json").write_text(
                json.dumps({"topic_key": "piercing"}), encoding="utf-8",
            )
            (run / "words.json").write_text(json.dumps([
                {"word": "हरी", "start": 0.0, "end": 0.2},
                {"word": "कैंडल", "start": 0.2, "end": 0.45},
            ]), encoding="utf-8")
            (run / "self_review.json").write_text(json.dumps({"scenes": [
                {"beat": "hook", "start": 0.0, "end": 0.12},
                {"beat": "pehchan", "start": 0.12, "end": 0.24},
                {"beat": "matlab", "start": 0.24, "end": 0.34},
                {"beat": "karein", "start": 0.34, "end": 0.45},
            ]}), encoding="utf-8")

            def boom(_path):
                raise AssertionError("whisper must not run when words.json is present")

            original_fn = sync_gate.transcribe_file
            sync_gate.transcribe_file = boom
            out = Path(tmp) / "fresh" / "final.mp4"
            try:
                rerender_from_voice.render(run, out)
            finally:
                sync_gate.transcribe_file = original_fn
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 1000)
            thumb = out.with_name("thumbnail.jpg")
            self.assertTrue(thumb.is_file())
            self.assertGreater(thumb.stat().st_size, 1000)
            self.assertEqual((run / "voice.wav").read_bytes(), original)
            self.assertFalse((out.parent / "final_rerender_work").exists())
