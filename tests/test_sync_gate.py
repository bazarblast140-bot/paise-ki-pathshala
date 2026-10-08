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


class HindiNumberTests(unittest.TestCase):
    def test_ek_sau_chauvan_is_154(self):
        self.assertIn("154", sync_gate.normalize("एक सौ चौवन").split())

    def test_chart_labels_do_not_fail_when_the_spoken_words_match(self):
        beats = {"entry": "एंट्री एक सौ अठारह और स्टॉप अट्ठानवे"}
        times = {"entry": (0.0, 4.0)}
        words = _words(beats, times)
        scenes = [{
            "start": 0.0, "end": 4.0, "beat": "entry", "kind": "chart-entry",
            "show": ["ENTRY 118", "SL 98"],
        }]
        report = sync_gate.check(words, scenes, beats, times, [])
        self.assertNotIn("on-screen text is not what this scene says", report["reason"])
        self.assertEqual(report["scenes"][0]["missing_numbers"], [])


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

    def test_a_slow_sentence_is_scored_on_its_spoken_window(self):
        """Dry run 37095205924: 3.4s windows failed while the whole beat passed.

        An even word split reads the later sentence during the earlier audio.
        The threshold stays 0.5. The window must use the words actually spoken.
        """
        self.assertEqual(sync_gate.MIN_RECALL, 0.5)
        heard = [
            "सीमा", "पूँजी", "नुकसान", "महीने", "कमाई",
            "सुरक्षित", "उदाहरण", "मतलब", "रुकता", "रहती",
        ]
        missed = [
            "गुप्तकथा", "अदृश्यपाठ", "लुप्तवाक्य", "छिपाशब्द", "अनसुनावाक्य",
            "गायबपंक्ति", "ओझलपाठ", "विलीनशब्द", "नदारदवाक्य", "अप्रकटपाठ",
        ]
        script = " ".join(heard + missed)
        beats = {"matlab": script}
        times = {"matlab": (20.0, 40.0)}
        audio = []
        for i, word in enumerate(heard):
            start = 20.0 + i * 1.8
            audio.append({"word": word, "start": start, "end": start + 1.7})
        scene = {
            "start": 32.2, "end": 35.6, "beat": "matlab", "kind": "matlab",
            "show": ["रुकता"],
        }
        even = sync_gate.script_slice(
            script, 20.0, 40.0,
            32.2 - sync_gate.WINDOW_PAD, 35.6 + sync_gate.WINDOW_PAD,
        )
        self.assertLess(sync_gate.recall(even, " ".join(heard)), sync_gate.MIN_RECALL)
        report = sync_gate.check(audio, [scene], beats, times, [])
        row = report["scenes"][0]
        self.assertGreaterEqual(row["recall"], sync_gate.MIN_RECALL, row["script"])
        self.assertNotIn("गुप्तकथा", row["script"])
        self.assertIn("रुकता", row["script"])
        spoken = {"matlab": "सीमा पूँजी नुकसान। गुप्तकथा अदृश्यपाठ लुप्तवाक्य।"}
        cue_audio = [
            {"word": "सीमा", "start": 20.0, "end": 25.0},
            {"word": "पूँजी", "start": 25.0, "end": 30.0},
            {"word": "नुकसान।", "start": 30.0, "end": 36.0},
            {"word": "गुप्तकथा", "start": 36.2, "end": 37.4},
            {"word": "अदृश्यपाठ", "start": 37.4, "end": 38.6},
            {"word": "लुप्तवाक्य।", "start": 38.6, "end": 39.8},
        ]
        board_short._CUE_CACHE.clear()
        cues = board_short.plan_cues(spoken, times, cue_audio)
        late = [cue for cue in cues if "गुप्तकथा" in cue["text"]]
        self.assertTrue(late, [cue["text"] for cue in cues])
        self.assertGreaterEqual(late[0]["t0"], 34.0)

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
        for left, right in (("15.7", "15.1"), ("23.5", "23.6"), ("772.4", "772.9"), ("2350", "2360")):
            self.assertFalse(sync_gate._near_token(left, right), (left, right))
        self.assertLess(sync_gate.recall("एटीआर 15.7 है", "एटीआर 15.1 है"), 1.0)
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
        self.assertIn("narration does not match", missed["reason"])
        self.assertNotIn("title promise", missed["reason"])

    def test_title_inflection_is_a_review_note_and_does_not_zero_sync(self):
        beats = {
            "hook": "तीन लाल कैंडल लगातार गिरावट दिखाती हैं",
            "pehchan": "हर बार नीचे close होता है neeche",
        }
        times = {"hook": (0.0, 4.0), "pehchan": (4.0, 8.0)}
        words = []
        for beat, (start, end) in times.items():
            toks = beats[beat].split()
            step = (end - start) / len(toks)
            for i, tok in enumerate(toks):
                words.append({
                    "word": tok,
                    "start": start + i * step,
                    "end": start + (i + 1) * step - 0.01,
                })
        scenes = [
            {"start": 0.0, "end": 4.0, "beat": "hook", "kind": "hook", "show": ["गिरावट"]},
            {"start": 4.0, "end": 8.0, "beat": "pehchan", "kind": "pehchan", "show": ["नीचे"]},
        ]
        for term in ("नीचा", "नीचे", "नीची", "neeche"):
            report = sync_gate.check(words, scenes, beats, times, [term])
            self.assertTrue(report["ok"], (term, report["reason"], report.get("title_note")))
            self.assertEqual(report.get("title_note"), "")
            self.assertEqual(self_review.score_sync(report)[0], 100)
        # The hook names the down move without the title's exact word.
        concept_beats = {"hook": "लगातार गिरावट दिखी", "pehchan": "sellers का कंट्रोल रहा"}
        concept_words = []
        for beat, (start, end) in times.items():
            toks = concept_beats[beat].split()
            step = (end - start) / len(toks)
            for i, tok in enumerate(toks):
                concept_words.append({
                    "word": tok,
                    "start": start + i * step,
                    "end": start + (i + 1) * step - 0.01,
                })
        concept_scenes = [
            {"start": 0.0, "end": 4.0, "beat": "hook", "kind": "hook", "show": ["गिरावट"]},
            {"start": 4.0, "end": 8.0, "beat": "pehchan", "kind": "pehchan", "show": ["कंट्रोल"]},
        ]
        covered = sync_gate.check(concept_words, concept_scenes, concept_beats, times, ["नीचा"])
        self.assertTrue(covered["ok"], covered.get("title_note"))
        self.assertEqual(covered.get("title_note"), "")
        # An unrelated title word is a note. Sync stays 100.
        noted = sync_gate.check(words, scenes, beats, times, ["SIP"])
        self.assertTrue(noted["ok"], noted["reason"])
        self.assertIn("title promise review", noted["title_note"])
        self.assertIn("SIP", noted["title_note"])
        score, notes = self_review.score_sync(noted)
        self.assertEqual(score, 100)
        self.assertTrue(notes)
        reviewed = self._review(noted)
        self.assertTrue(reviewed["public_ok"], reviewed["summary"])
        self.assertEqual(reviewed["parts"]["sync"], 100)
        self.assertIn("title promise review", reviewed["summary"])
        # A real recall miss still blocks, and it still scores sync at 0.
        wrong = sync_gate.check(
            words, scenes, {"hook": "एसआईपी हर महीने इक्विटी फंड", "pehchan": "अलग विषय"}, times, [],
        )
        self.assertFalse(wrong["ok"])
        self.assertIn("narration does not match", wrong["reason"])
        self.assertEqual(self_review.score_sync(wrong)[0], 0)

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


class LatinTermRecallTests(unittest.TestCase):
    def test_english_finance_words_do_not_fail_a_correct_hindi_read(self):
        script = "EBITDA 100 करोड़, cash सिर्फ़ 40? यानी"
        heard = "एबिट्द 100 करोड करसा सर्फ चालीस यानी"
        self.assertGreaterEqual(sync_gate.recall(script, heard), sync_gate.MIN_RECALL)

    def test_a_different_hindi_lesson_still_fails(self):
        script = "EBITDA 100 करोड़, cash सिर्फ़ 40? यानी"
        heard = "आज मौसम बहुत अच्छा है घूमने चलते हैं"
        self.assertLess(sync_gate.recall(script, heard), sync_gate.MIN_RECALL)
