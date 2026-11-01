import json
import tempfile
import unittest
from pathlib import Path

import board_short
import publish_gates


class MotionMixEvidenceTests(unittest.TestCase):
    def test_motion_whoosh_writes_evidence_the_gate_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            board_short.write_motion_mix_evidence(
                tmp, {"under_db": 20.4, "sfx_peak": 0.01, "voice_rms": 0.1, "cues": [1.0]})
            data = json.loads(Path(tmp, "audio_mix.json").read_text(encoding="utf-8"))
            self.assertTrue(data["sfx_present"])
            self.assertEqual(publish_gates.audio_reasons(tmp), [])

    def test_no_cues_is_no_sfx_not_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            board_short.write_motion_mix_evidence(
                tmp, {"under_db": None, "sfx_peak": 0.0, "voice_rms": 0.1, "cues": []})
            self.assertEqual(publish_gates.audio_reasons(tmp), [])

    def test_loud_whoosh_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            board_short.write_motion_mix_evidence(
                tmp, {"under_db": 10.0, "sfx_peak": 0.05, "voice_rms": 0.1, "cues": [1.0]})
            self.assertTrue(publish_gates.audio_reasons(tmp))

    def test_mix_call_site_writes_evidence(self):
        import inspect
        src = inspect.getsource(board_short)
        i = src.index("motion_lib.mix_under(RUN / \"voice.wav\", reveals, mixed)")
        self.assertIn("write_motion_mix_evidence(RUN, info)", src[i:i + 200])


if __name__ == "__main__":
    unittest.main()
