"""16-Oct review of the BHARTIARTL TA sample: Edge dropped '83', 'advice na-' was cut, the end card was silent."""
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from realchart import qa, script
from realchart.voice import edge_text, hindi_number, speech_seconds, tidy_clip


def _wav(path, pieces, rate=24000):
    """pieces: [(seconds, voiced?)] -> tone or silence."""
    out = []
    for sec, voiced in pieces:
        n = int(sec * rate)
        out.append((np.sin(np.arange(n) * 2 * np.pi * 220 / rate) * 12000).astype(np.int16) if voiced
                   else np.zeros(n, np.int16))
    with wave.open(str(path), "w") as h:
        h.setnchannels(1); h.setsampwidth(2); h.setframerate(rate)
        h.writeframes(np.concatenate(out).tobytes())


class HindiNumbers(unittest.TestCase):
    def test_words(self):
        self.assertEqual(hindi_number(1883), "एक हज़ार आठ सौ तिरासी")
        self.assertEqual(hindi_number(51), "इक्यावन")
        self.assertEqual(hindi_number(22997), "बाईस हज़ार नौ सौ सत्तानवे")
        self.assertEqual(hindi_number(250000), "दो लाख पचास हज़ार")

    def test_edge_text_keeps_punctuation_and_decimals(self):
        self.assertEqual(edge_text("स्टॉप लॉस 1883, टारगेट 1,954।"), "स्टॉप लॉस एक हज़ार आठ सौ तिरासी, टारगेट एक हज़ार नौ सौ चौवन।")
        self.assertIn("1.7", edge_text("RR 1.7"))


class TidyClip(unittest.TestCase):
    def test_pauses_shrink_and_voice_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.wav"
            _wav(p, [(1.0, False), (1.0, True), (1.5, False), (1.0, True), (1.0, False)])
            before = speech_seconds(p)
            tidy_clip(p)
            self.assertAlmostEqual(speech_seconds(p), before, delta=0.05)
            self.assertTrue(qa.ends_in_hush(p))
            self.assertLess(qa.probe(p).get("duration") or 9, 3.2)


class AudioQa(unittest.TestCase):
    def test_gap_and_truncated_clip_are_faults(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "voice_parts").mkdir()
            _wav(out / "voice_parts" / "00.wav", [(1.0, True), (0.3, False)])
            _wav(out / "voice_parts" / "01.wav", [(1.0, True), (1.2, False), (1.0, True)])  # cut mid-word
            _wav(out / "voice.wav", [(1.0, True), (1.2, False), (1.0, True)])
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3.2",
                            "-i", str(out / "voice.wav"), "-shortest", str(out / "final.mp4")], check=True)
            faults = qa.audio_faults(out)
            self.assertTrue(any("silence" in f for f in faults), faults)
            self.assertTrue(any("01.wav ends mid-word" in f for f in faults), faults)
            self.assertTrue(any("voice.wav ends mid-word" in f for f in faults), faults)
            self.assertFalse(any("00.wav" in f for f in faults), faults)


class EndCardVoice(unittest.TestCase):
    def test_ta_end_card_speaks_follow(self):
        say = script.end_card_voice("ta")
        self.assertIn("Rajesh Technical Trader", say["rtt"])
        self.assertIn("follow", say["rtt"])
        self.assertEqual(script.end_card_voice("close"), {})


if __name__ == "__main__":
    unittest.main()
