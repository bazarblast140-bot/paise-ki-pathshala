"""Same take, same transcript, same sync verdict across runs."""
import itertools
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import sync_gate


def _noisy_decoder():
    """Stands in for whisper's sampled fallback: a different line every call."""
    lines = itertools.cycle((
        ["लाल", "बॉडी", "एकसो", "बाइस"],
        ["लाल", "बोड़ी", "एकसो", "बारा"],
        ["ला", "बडी"],
    ))

    def decode(audio, beam_size=1):
        return [
            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
            for i, w in enumerate(next(lines))
        ]
    return decode


class TranscriptCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"TRANSCRIPT_CACHE": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _score(self, words):
        beats = {"pehchan": "लाल बॉडी 122 से 112"}
        times = {"pehchan": (0.0, 2.0)}
        scenes = [{"start": 0.0, "end": 2.0, "beat": "pehchan", "kind": "chart-pehchan", "show": []}]
        return sync_gate.check(words, scenes, beats, times, [])

    def test_same_audio_gives_the_same_words_and_score_across_runs(self):
        audio = np.linspace(-0.2, 0.2, 16000, dtype=np.float32)
        with mock.patch.object(sync_gate, "_transcribe_uncached", side_effect=_noisy_decoder()) as dec:
            runs = [sync_gate.transcribe_pcm(audio.copy()) for _ in range(4)]
            self.assertEqual(dec.call_count, 1)
        self.assertTrue(all(run == runs[0] for run in runs))
        reports = [self._score(run) for run in runs]
        self.assertEqual({r["ok"] for r in reports}, {reports[0]["ok"]})
        self.assertEqual(
            {tuple(row["gate_recall"] for row in r["scenes"]) for r in reports},
            {tuple(row["gate_recall"] for row in reports[0]["scenes"])},
        )

    def test_other_audio_or_beam_is_decoded_again(self):
        a = np.zeros(8000, dtype=np.float32) + 0.1
        b = np.zeros(8000, dtype=np.float32) + 0.2
        with mock.patch.object(sync_gate, "_transcribe_uncached", side_effect=_noisy_decoder()) as dec:
            sync_gate.transcribe_pcm(a)
            sync_gate.transcribe_pcm(b)
            sync_gate.transcribe_pcm(a, beam_size=5)
            sync_gate.transcribe_pcm(a)
            self.assertEqual(dec.call_count, 3)

    def test_a_stubbed_model_is_never_cached(self):
        audio = np.zeros(8000, dtype=np.float32) + 0.3
        with mock.patch.object(sync_gate, "model", return_value=None), \
                mock.patch.object(sync_gate, "_transcribe_uncached", side_effect=_noisy_decoder()) as dec:
            sync_gate.transcribe_pcm(audio)
            sync_gate.transcribe_pcm(audio)
            self.assertEqual(dec.call_count, 2)
        self.assertEqual(os.listdir(self.tmp.name), [])


if __name__ == "__main__":
    unittest.main()


class AsrSanityTests(unittest.TestCase):
    def _scene(self, heard):
        words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(heard.split())]
        beats = {"context": "तेजी के बाद रेसिस्टेंस 124 पर वैलिड। साइडवेज में कमजोर।"}
        scenes = [{"start": 0.0, "end": 4.0, "beat": "context", "kind": "chart-context", "show": []}]
        return sync_gate.check(words, scenes, beats, {"context": (0.0, 4.0)}, [])

    def test_a_misheard_beat_with_its_figures_heard_passes_as_sanity(self):
        report = self._scene("तेजी के बाद रिजिस्तन्स एकसो चोबीस पर वलगद यागद")
        self.assertTrue(report["ok"], report["reason"])
        self.assertTrue(report["scenes"][0]["asr_sanity"])

    def test_a_wrong_figure_or_another_lesson_still_fails(self):
        self.assertFalse(self._scene("तेजी के बाद रिजिस्तन्स एकसो चालीस पर वलगद")["ok"])
        self.assertFalse(self._scene("आज निफ्टी बंद हुआ एकसो चोबीस")["ok"])
