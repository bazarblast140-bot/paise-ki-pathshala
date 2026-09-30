import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("VIDEO_FORMAT", "short")
import board_short as B


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg needed")
class FitDurationTests(unittest.TestCase):
    def make(self, d, secs):
        audio, durs = {}, {}
        for b in B.BEATS:
            p = Path(d) / f"{b}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"sine=frequency=440:duration={secs}", str(p)], check=True)
            audio[b], durs[b] = p, B.duration(p)
        return audio, durs

    def run_fit(self, per_beat):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(B, "RUN", Path(d)):
            audio, durs = self.make(d, per_beat)
            return B.fit_duration(audio, durs, {"video": {"min_seconds": 32, "max_seconds": 62}})

    def test_inside_window_untouched(self):
        _, _, total = self.run_fit(10)          # 40s
        self.assertAlmostEqual(total, 40.06, delta=0.3)

    def test_slightly_short_gets_gentle_slowdown(self):
        _, _, total = self.run_fit(7.6)         # ~30.4s -> ~33s
        self.assertGreaterEqual(total, 32)

    def test_slightly_long_gets_gentle_speedup(self):
        _, _, total = self.run_fit(16.5)        # ~66s -> ~61s
        self.assertLessEqual(total, 62)

    def test_way_off_fails_instead_of_padding(self):
        with self.assertRaises(SystemExit):
            self.run_fit(4)                     # 16s: never stretched into filler
        with self.assertRaises(SystemExit):
            self.run_fit(48)                    # 192s (the 30-Sep failure) -> clear stop


class NarrationWindowTests(unittest.TestCase):
    def test_word_range_from_config(self):
        self.assertEqual(B.word_range({"narration": {"min_words": 100, "max_words": 130}}), (100, 130))
        self.assertEqual(B.word_range({}), (110, 145))


if __name__ == "__main__":
    unittest.main()
