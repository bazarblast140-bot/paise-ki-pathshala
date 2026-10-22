"""Gemini one-take cuts: split spoken numbers and whisper-dropped stretches."""
import array
import json
import tempfile
import unittest
import wave
from pathlib import Path

import gemini_voice
import sync_gate


def _wav(path, seconds, voiced):
    rate = 16000
    data = array.array("h")
    for i in range(int(seconds * rate)):
        t = i / rate
        loud = any(a <= t < b for a, b in voiced)
        data.append(8000 if loud and (i // 20) % 2 else (-8000 if loud else 0))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())


class WordTokenTests(unittest.TestCase):
    def test_a_price_split_over_two_words_is_one_token(self):
        words = [
            {"word": "स्टॉप", "start": 0.0, "end": 0.4},
            {"word": "एकसो", "start": 0.4, "end": 0.7},
            {"word": "दस", "start": 0.7, "end": 0.9},
            {"word": "प्लान", "start": 0.9, "end": 1.2},
        ]
        self.assertEqual(
            gemini_voice._word_tokens(words),
            [(0, "स्टॉप"), (1, "110"), (3, "प्लान")],
        )

    def test_charon_mishearings_fold_to_the_plan_prices(self):
        self.assertEqual(
            sync_gate.tokens("एकसो बारा एकसो चबदा एकसो अथारा श्टोप एकसो छबभीस"),
            ["112", "114", "118", "स्टॉप", "126"],
        )


class DroppedGapTests(unittest.TestCase):
    def test_voiced_gap_is_re_decoded_and_silent_gap_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "take.wav"
            _wav(wav, 10.0, [(0.0, 2.0), (3.0, 5.0), (8.0, 9.5)])
            words = [
                {"word": "एक", "start": 0.2, "end": 1.8},
                {"word": "तीन", "start": 8.1, "end": 9.4},
            ]
            calls = []

            def transcribe(path):
                calls.append(path)
                return [{"word": "दो", "start": 0.4 + 0.3, "end": 2.0 + 0.3}]

            merged = gemini_voice.fill_dropped_gaps(wav, words, transcribe)
            self.assertEqual(len(calls), 1)
            self.assertEqual([w["word"] for w in merged], ["एक", "दो", "तीन"])
            added = merged[1]
            self.assertGreaterEqual(added["start"], 1.8)
            self.assertLessEqual(added["end"], 8.1)

    def test_no_transcriber_keeps_the_words(self):
        words = [{"word": "एक", "start": 0.0, "end": 1.0}]
        self.assertEqual(gemini_voice.fill_dropped_gaps("missing.wav", words, None), words)


if __name__ == "__main__":
    unittest.main()
