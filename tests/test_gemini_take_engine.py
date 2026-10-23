"""Run 39973714175: the Gemini take was timed from the stale Edge proof mp3."""
import io
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import board_short
import gemini_voice


def _tone(path, seconds):
    rate = 24000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frame = (8000).to_bytes(2, "little", signed=True) + (-8000).to_bytes(2, "little", signed=True)
        out.writeframes(frame * int(rate * seconds / 2))


class GeminiTakeEngineTests(unittest.TestCase):
    def test_take_is_timed_from_the_gemini_wav_not_the_edge_proof(self):
        beats = ("hook", "cta")
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            for beat in beats:
                # Edge proof leftovers: a 24s mp3 and an "edge" marker per beat.
                _tone(run / f"tts_{beat}.mp3", 24.0)  # ffmpeg probes the content
                (run / f"tts_{beat}.engine").write_text("edge", encoding="utf-8")

            def fake_write(script, dest, **_kw):
                _tone(dest, 42.0)

            def fake_split(src, spoken, order, dest_dir, transcribe=None):
                for beat in order:
                    _tone(Path(dest_dir) / f"tts_{beat}.wav", 21.0)

            with mock.patch.object(board_short, "RUN", run), \
                 mock.patch.object(gemini_voice, "write_speech", side_effect=fake_write), \
                 mock.patch.object(gemini_voice, "split_into_beats", side_effect=fake_split), \
                 mock.patch.object(board_short, "_expected_voice_seconds", return_value=42.0), \
                 redirect_stdout(io.StringIO()):
                spoken = {beat: "x" for beat in beats}
                total = board_short._one_gemini_take(
                    spoken, {}, beats, dict(spoken), "script", "style", [], 0, None, None,
                )
        self.assertAlmostEqual(total, 42.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
