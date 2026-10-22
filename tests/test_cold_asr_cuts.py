import unittest
from unittest import mock

import gemini_voice
import sync_gate


def words_for(rows):
    out, t = [], 0.0
    for text in rows:
        for w in text.split():
            out.append({"word": w, "start": t, "end": t + 0.4})
            t += 0.5
        t += 0.6
    return out, t


class GarbledBeatKeepsOtherCuts(unittest.TestCase):
    def test_one_garbled_beat_does_not_drop_content_cuts(self):
        spoken = {
            "hook": "बेयरिश एंगल्फिंग पर शॉर्ट कब करें",
            "pehchan": "लाल बॉडी हरी को पूरा ढकती है",
            "context": "तेजी के बाद रेसिस्टेंस पर वैलिड",
            "target": "टारगेट रिस्क रिवॉर्ड यानी रिस्क रिवॉर्ड",
            "invalidation": "गलती जल्दी शॉर्ट हाई के जस्ट ऊपर स्टॉप टूटे तो स्किप",
        }
        heard = dict(spoken, pehchan="ताल बोडी रही ढखती पुरा")
        words, dur = words_for([heard[b] for b in spoken])
        bounds = gemini_voice._content_bounds(list(spoken), spoken, words, dur)
        self.assertIsNotNone(bounds)
        gal = next(w["start"] for w in words if w["word"] == "गलती")
        self.assertAlmostEqual(bounds["invalidation"][0], gal, places=2)
        tgt = next(w["start"] for w in words if w["word"] == "टारगेट")
        self.assertAlmostEqual(bounds["target"][0], tgt, places=2)


class ContextRetry(unittest.TestCase):
    def test_context_words_kept_inside_beat_only(self):
        import numpy as np
        times = {"a": (0.0, 1.0), "b": (1.0, 2.0), "c": (2.0, 3.0)}
        audio = {k: f"/x/{k}.wav" for k in times}
        fake = [{"word": "x", "start": 0.5, "end": 0.7},
                {"word": "y", "start": 1.2, "end": 1.5},
                {"word": "z", "start": 2.4, "end": 2.6}]
        with mock.patch.object(sync_gate, "pcm", lambda p: np.zeros(16000, dtype=np.float32)), \
             mock.patch.object(sync_gate, "transcribe_pcm", lambda a, beam_size=1: fake):
            got = sync_gate._words_in_context("b", times, audio, 5)
        self.assertEqual([w["word"] for w in got], ["y"])
        self.assertAlmostEqual(got[0]["start"], 1.2)


if __name__ == "__main__":
    unittest.main()
