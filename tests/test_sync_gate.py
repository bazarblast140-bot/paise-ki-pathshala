"""Topic–voice–visual sync, and a narration fallback that must not publish."""
import unittest

import numpy as np

import board_short
import self_review
import sync_gate


def _words(beats, times):
    words = []
    for beat, (start, end) in times.items():
        toks = beats[beat].split()
        step = (end - start) / max(1, len(toks))
        for i, tok in enumerate(toks):
            words.append({
                "word": tok,
                "start": start + i * step,
                "end": start + (i + 1) * step - 0.01,
            })
    return words


class SyncGateTests(unittest.TestCase):
    def setUp(self):
        self.beats = {
            "hook": "तंग एसएल बार बार कटता क्यों है एटीआर हलचल",
            "pehchan": "नियम एटीआर 15.7 औसत हलचल",
            "matlab": "एंट्री 795.9 दूरी 23.5 एसएल 772.4",
            "karein": "गलती तंग रेखा जोखिम 2350 सलाह नहीं",
        }
        self.times = {
            "hook": (0.0, 4.0), "pehchan": (4.0, 8.0),
            "matlab": (8.0, 12.0), "karein": (12.0, 16.0),
        }
        self.words = _words(self.beats, self.times)
        self.scenes = [
            {"start": a, "end": b, "beat": beat, "kind": beat, "show": [self.beats[beat]]}
            for beat, (a, b) in self.times.items()
        ]
        self.terms = ["एटीआर", "एसएल", "23.5", "हलचल"]

    def _review(self, report):
        topic = {
            "key": "hammer", "cat": "CANDLESTICK",
            "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
            "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
            "beats": {
                "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
                "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
                "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
                "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
            },
            "facts": ["निचली wick बॉडी से कम से कम 2 गुना", "ये संभावना है, गारंटी नहीं"],
        }
        narration = board_short.fallback_narration(topic)
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        import short_layout
        overlays = short_layout.overlay_plan(topic, times, narration["hook_line"])
        spec = {"mode": "schematic", "source": None, "as_of": None, "series": [], "fabricated": False}
        scenes = __import__("scene_pace").plan_scenes(times)
        return self_review.full_review(
            topic, narration, spec, overlays, trends={}, scenes=scenes, sync=report,
        )

    def test_matching_voice_passes_and_a_mismatch_blocks(self):
        report = sync_gate.check(self.words, self.scenes, self.beats, self.times, self.terms)
        self.assertTrue(report["ok"], report["reason"])
        self.assertEqual(self_review.score_sync(report)[0], 100)
        clean = self._review(report)
        self.assertTrue(clean["public_ok"], clean["summary"])
        self.assertEqual(clean["parts"]["sync"], 100)
        bad_scenes = [dict(scene) for scene in self.scenes]
        bad_scenes[0] = dict(bad_scenes[0], show=["एसआईपी 9999"])
        missed = sync_gate.check(self.words, bad_scenes, self.beats, self.times, self.terms)
        self.assertFalse(missed["ok"])
        self.assertTrue(missed["reason"])
        blocked = self._review(missed)
        self.assertFalse(blocked["public_ok"])
        self.assertEqual(blocked["parts"]["sync"], 0)
        self.assertIn(missed["reason"], blocked["summary"])
        self.assertEqual(self_review.PART_BAR, 60)
        self.assertEqual(self_review.OVERALL_BAR, 75)

    def test_a_failed_transcription_blocks(self):
        empty = sync_gate.check([], self.scenes, self.beats, self.times, self.terms)
        self.assertFalse(empty["ok"])
        self.assertIn("transcription failed", empty["reason"])
        failed = {"ok": False, "reason": "transcription failed: model unavailable", "scenes": []}
        blocked = self._review(failed)
        self.assertFalse(blocked["public_ok"])
        self.assertIn("transcription failed", blocked["summary"])
        score, notes = self_review.score_sync(None)
        self.assertIsNone(score)
        self.assertEqual(notes, [])

    def test_number_words_become_the_lesson_figures(self):
        heard = (
            "HDFC बैंग का ATR 15 दशम लव साथ "
            "अखिरी भाव साथ सो पचानवे दशमलव नो अंट्री "
            "इस यल साथ सो बहत्तर दशमलव चाए "
            "उपर अथ सो उनीस दशमलव चाए "
            "जोखिन 2 ,350 रुपै"
        )
        text = sync_gate.normalize(heard)
        for figure in ("15.7", "795.9", "772.4", "819.4", "2350", "एटीआर", "एसएल"):
            self.assertIn(figure, text.split(), figure)
        # This voice's other spellings of the same words.
        again = sync_gate.normalize("स्येल अटियार पुईंट बरावर रषल Hdfc बान")
        for figure in ("एसएल", "एटीआर", "पॉइंट", "बराबर", "हलचल", "एचडीएफसी", "बैंक"):
            self.assertIn(figure, again.split(), figure)

    def test_edge_whisper_near_spellings_match_and_a_different_topic_does_not(self):
        # Measured on hi-IN-MadhurNeural with faster-whisper small, language=hi:
        # घाटा→गाता, प्रतिशत→प्रतीषत, हजार→हाजार, बंद→बन्द. The sentence is the
        # same lesson. A different topic and a different number still fail.
        script = "दिन का घाटा दो प्रतिशत पर चार हजार पर बंद होता है"
        heard = "दिन का गाता दो प्रतीषत पर चार हाजार पर बन्द होता है"
        self.assertGreaterEqual(sync_gate.recall(script, heard), sync_gate.MIN_RECALL)
        self.assertIn("4000", sync_gate.tokens("चार हाजार"))
        self.assertIn("200000", sync_gate.tokens("2 ,00 ,000"))
        self.assertLess(
            sync_gate.recall("एसआईपी हर महीने इक्विटी फंड में लगाओ", heard),
            sync_gate.MIN_RECALL,
        )
        self.assertLess(sync_gate.recall("चार्ज 300 और भाव 9999", heard), sync_gate.MIN_RECALL)
        self.assertFalse(sync_gate._near_token("300", "4000"))
        self.assertFalse(sync_gate._near_token("हद", "अग"))
        self.assertFalse(sync_gate._near_token("कारोबारी", "व्यापारी"))
        self.assertGreater(sync_gate._edit_distance("कारोबारी", "व्यापारी", 2), 2)
        beats = {"hook": script, "karein": "हद पहले लिखो और टर्मिनल बंद कर दो"}
        times = {"hook": (0.0, 4.0), "karein": (4.0, 8.0)}
        words = []
        for beat, (start, end) in times.items():
            text = heard if beat == "hook" else "अग पहले लिख हो और तरमिनल बन्द कर दो"
            toks = text.split()
            step = (end - start) / len(toks)
            for i, tok in enumerate(toks):
                words.append({
                    "word": tok,
                    "start": start + i * step,
                    "end": start + (i + 1) * step - 0.01,
                })
        scenes = [
            {"start": 0.0, "end": 2.0, "beat": "hook", "kind": "hook", "show": []},
            {"start": 2.0, "end": 4.0, "beat": "hook", "kind": "hook", "show": []},
        ]
        report = sync_gate.check(words, scenes, beats, times, [])
        self.assertTrue(report["ok"], report["reason"])
        self.assertTrue(report["beats"])
        missed = sync_gate.check(
            words, scenes, {"hook": "एसआईपी हर महीने इक्विटी फंड", "karein": beats["karein"]},
            times, ["SIP"],
        )
        self.assertFalse(missed["ok"])
        self.assertIn("title promise", missed["reason"])

    def test_speech_offset_uses_loudness_not_zero_crossings(self):
        rate = 16000
        silence = np.zeros(int(0.30 * rate), dtype=np.float32)
        t = np.arange(int(0.50 * rate)) / rate
        tone = (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
        offset = sync_gate.speech_offset(np.concatenate([silence, tone]), rate=rate)
        self.assertGreater(offset, 0.25)
        self.assertLess(offset, 0.40)

    def test_fallback_does_not_keep_partial_llm_text(self):
        template = {
            "title": "टेम्पलेट शीर्षक यहाँ",
            "hook_line": "टेम्पलेट हुक",
            "beats": {
                "hook": "टेम्पलेट हुक वाला पाठ",
                "pehchan": "टेम्पलेट पहचान",
                "matlab": "टेम्पलेट मतलब",
                "karein": "टेम्पलेट करें",
            },
        }
        partial = {
            "title": "LLM TITLE",
            "hook_line": "LLM HOOK",
            "beats": {
                "hook": "LLM HOOK UNIQUE xyz",
                "pehchan": "LLM body",
                "matlab": "LLM more",
                "karein": "   ",
            },
        }
        narration, source = board_short.resolve_narration(partial, template)
        blob = " ".join(narration["beats"].values()) + narration["title"]
        self.assertNotIn("LLM HOOK UNIQUE", blob)
        self.assertEqual(source, "fallback")
        self.assertEqual(narration["beats"]["hook"], template["beats"]["hook"])
        self.assertTrue(board_short.upload_block_reason("fallback"))
        self.assertEqual(board_short.upload_block_reason("outline"), "")
        self.assertEqual(board_short.upload_block_reason("llm"), "")
        review = {"public_ok": True, "passed": True, "summary": "overall 100", "notes": []}
        blocked = board_short.apply_upload_block(review, "fallback")
        self.assertFalse(blocked["public_ok"])
        self.assertFalse(blocked["passed"])
        self.assertIn("upload blocked", blocked["summary"])


if __name__ == "__main__":
    unittest.main()
