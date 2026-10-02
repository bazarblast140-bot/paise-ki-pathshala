"""Content gates from the Edge dry run on 42627da.

The leak gate and the live gate must agree on a worked उदाहरण sentence.
100−p is legal only when the sentence subtracts. Loss recovery must be
L/(1−L). Cards take figures from the caption window. Finance English is
folded to Devanagari before TTS. The hook trim stops at a sentence end.
A navy panel under about 35% fill fails craft.
"""
import json
import unittest
from pathlib import Path

import board_short
import hook_check
import self_review

ROOT = Path(__file__).resolve().parents[1]


def _topic(key):
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = json.loads(json.dumps(next(item for item in bank["topics"] if item["key"] == key)))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    topic["_voice"] = {"words_per_second": 1.9}
    return topic


class WorkedExampleTests(unittest.TestCase):
    def test_worked_sentence_passes_and_a_bare_list_does_not(self):
        topic = _topic("risk_daily_loss_limit")
        order = board_short.BEATS
        dump = "उदाहरण: 98, 4000, 200000"
        worked = "उदाहरण: ₹2,00,000 का 2% = ₹4,000"
        self.assertTrue(board_short._number_dump(dump))
        self.assertFalse(board_short._number_dump(worked))
        self.assertTrue(board_short.narrator_leak_notes({"matlab": dump}, order))
        self.assertEqual(board_short.narrator_leak_notes({"matlab": worked + "।"}, order), [])
        system = board_short.four_live_system(topic)
        repair = board_short.repair_instructions(
            {
                "title": "दिन की हद समझिए",
                "hook_line": "दिन का घाटा कहाँ रुके?",
                "beats": {beat: "नियम कदम है।" for beat in order},
            },
            ["matlab"],
            topic,
        )
        for blob in (system, repair, board_short.live_narration_system(topic)):
            self.assertIn("उदाहरण: ₹2,00,000 का 2% = ₹4,000", blob)
            self.assertIn("98, 4000, 200000", blob)
        example = hook_check.worked_example_sentence(topic)
        self.assertFalse(board_short._number_dump(example), example)


class ComplementAndRecoveryTests(unittest.TestCase):
    def test_complement_needs_the_subtraction_and_recovery_must_match(self):
        journal = _topic("risk_journal")
        journal["_live_beats"] = "six"
        self.assertIn("60", hook_check.percentage_complements(journal))
        bare = {"matlab": "हार की दर 60 प्रतिशत रहती है।"}
        self.assertIn("60", hook_check.live_number_faults(bare, journal, ("matlab",)))
        spelled = {"matlab": "100 - 40 = 60 तो हार 60 है।"}
        self.assertEqual(hook_check.live_number_faults(spelled, journal, ("matlab",)), set())

        topic = _topic("loss_recovery")
        wrong = {"matlab": "75% गिरे तो 89/90% चाहिए।"}
        faults = hook_check.live_number_faults(wrong, topic, ("matlab",))
        self.assertIn("89", faults)
        self.assertIn("90", faults)
        self.assertIn("75", faults)
        pair = hook_check.loss_recovery_pair_faults(wrong["matlab"], topic)
        self.assertIn("89", pair)
        self.assertIn("90", pair)
        right = "75% गिरे तो 300% चाहिए।"
        self.assertEqual(hook_check.loss_recovery_pair_faults(right, topic), set())
        card_pair = "50% घटे तो 11% चाहिए।"
        self.assertIn("11", hook_check.loss_recovery_pair_faults(card_pair, topic))
        legal = "10% घटे तो 11% चाहिए।"
        self.assertEqual(hook_check.loss_recovery_pair_faults(legal, topic), set())
        claim = "नीचे गिरे भाव को उतना ही ऊपर जाना पड़ता है।"
        notes = hook_check.fact_contradiction_notes(claim, topic)
        self.assertTrue(notes)
        score, acc = self_review.score_accuracy(
            {"hook_line": "वापसी मुश्किल क्यों?", "beats": {"matlab": claim}},
            topic,
        )
        self.assertEqual(score, 0)
        self.assertTrue(any("contradict" in note for note in acc))


class CaptionCardTests(unittest.TestCase):
    def test_figures_come_from_the_caption_window_and_do_not_repeat(self):
        spoken = {
            "hook": "शुरुआत में यह नियम एक कदम है। 100 का आंकड़ा यहाँ है।",
            "pehchan": "11% और 22,600 यहाँ है। 25% पहली बार। फिर 25% दोबारा है।",
        }
        beat_times = {"hook": (0.0, 8.0), "pehchan": (8.0, 20.0)}
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 4.0},
            {"beat": "hook", "start": 4.0, "end": 8.0},
            {"beat": "pehchan", "start": 8.0, "end": 14.0},
            {"beat": "pehchan", "start": 14.0, "end": 20.0},
        ]
        words = []
        timed = {
            "hook": [
                ("शुरुआत", 0.2), ("में", 0.6), ("यह", 1.0), ("नियम", 1.6),
                ("एक", 2.2), ("कदम", 2.8), ("है।", 3.4),
                ("100", 4.6), ("का", 5.2), ("आंकड़ा", 5.8), ("यहाँ", 6.4), ("है।", 7.2),
            ],
            "pehchan": [
                ("11%", 8.4), ("और", 9.0), ("22,600", 10.2), ("यहाँ", 11.2), ("है।", 12.0),
                ("25%", 12.6), ("पहली", 13.0), ("बार।", 13.4),
                ("फिर", 15.0), ("25%", 15.6), ("दोबारा", 16.4), ("है।", 17.2),
            ],
        }
        for beat, rows in timed.items():
            for token, start in rows:
                words.append({"word": token, "start": start, "end": start + 0.4})
        texts, figures = board_short.scene_cards_for(spoken, beat_times, scenes, words)
        self.assertNotIn("100", texts["hook"][0])
        self.assertIn("100", texts["hook"][1])
        late = texts["pehchan"][0] + " " + " ".join(figures["pehchan"][0])
        self.assertIn("11", late)
        self.assertIn("22,600", late)
        first = figures["pehchan"][0]
        second = figures["pehchan"][1]
        self.assertEqual(sum(1 for fig in first if fig.replace(" ", "").startswith("25")), 1)
        self.assertTrue(
            any(fig.replace(" ", "").startswith("25") for fig in second),
            second,
        )

    def test_a_figure_said_inside_the_scene_is_on_that_card(self):
        """A cue clamped to the next beat still paints a figure already spoken."""
        spoken = {
            "hook": "यह कदम नियम है।",
            "pehchan": "10% घटे तो 11% चाहिए।",
        }
        beat_times = {"hook": (0.0, 9.29), "pehchan": (9.29, 16.0)}
        scenes = [
            {"beat": "hook", "start": 6.19, "end": 9.29},
            {"beat": "pehchan", "start": 9.29, "end": 13.0},
        ]
        words = [
            {"word": "यह", "start": 6.4, "end": 6.8},
            {"word": "कदम", "start": 7.0, "end": 7.4},
            {"word": "नियम", "start": 7.6, "end": 8.0},
            {"word": "है", "start": 8.2, "end": 8.6},
            {"word": "10%", "start": 9.09, "end": 9.45},
            {"word": "घटे", "start": 9.6, "end": 10.0},
            {"word": "तो", "start": 10.1, "end": 10.4},
            {"word": "11%", "start": 10.6, "end": 11.1},
            {"word": "चाहिए", "start": 11.3, "end": 11.9},
        ]
        texts, figures = board_short.scene_cards_for(spoken, beat_times, scenes, words)
        self.assertIn("10", texts["hook"][0])
        late = texts["pehchan"][0] + " " + " ".join(figures["pehchan"][0])
        self.assertIn("11", late)


class FinanceFoldTests(unittest.TestCase):
    def test_finance_english_is_devanagari_before_tts(self):
        raw = (
            "Call premium strike breakeven expiry Put charges "
            "traders check risk Buyer"
        )
        folded = board_short.fold_roman_words(raw)
        for word in (
            "Call", "premium", "strike", "breakeven", "expiry", "Put",
            "charges", "traders", "check", "risk", "Buyer",
        ):
            self.assertNotIn(word, folded)
            self.assertNotIn(word.lower(), folded.lower())
        for dev in ("कॉल", "प्रीमियम", "स्ट्राइक", "ब्रेकईवन", "एक्सपायरी", "पुट", "चार्जेस", "ट्रेडर्स", "चेक", "रिस्क", "बायर"):
            self.assertIn(dev, folded)
        self.assertEqual(board_short.latin_misread_words(folded), [])
        beats = {"matlab": raw}
        board_short.fold_roman_beats({"beats": beats})
        self.assertEqual(board_short.narrator_leak_notes(beats, ("matlab",)), [])


class PanelFillTests(unittest.TestCase):
    def test_sparse_panel_fails_and_a_labelled_card_passes(self):
        from PIL import Image, ImageDraw
        import scene_frames
        import scene_pace
        sparse = Image.new("RGB", (1080, 1920), (22, 48, 96))
        draw = ImageDraw.Draw(sparse)
        draw.rounded_rectangle([70, 190, 1010, 1300], 28, fill=(22, 48, 96))
        sparse.panel_box = (70, 190, 1010, 1300)
        sparse.text_layers = [{
            "text": "25",
            "box": (500.0, 400.0, 560.0, 450.0),
            "faint": False,
        }]
        sparse.label_boxes = []
        sparse.labelled_graphic_boxes = []
        self.assertLess(scene_frames.panel_fill_fraction(sparse), 0.35)
        notes = scene_frames.frame_layout_notes(sparse)
        self.assertTrue(any("panel fill" in note for note in notes))
        topic = _topic("loss_recovery")
        topic["_spoken_beats"] = {
            "matlab": "उदाहरण: 20% घटे तो 25% चाहिए। नुकसान 25% के बाद वापसी अलग है।",
        }
        frame = scene_frames.frames_for(topic, "matlab", 1, {"mode": "schematic"})[0][0]
        self.assertGreaterEqual(frame.panel_fill, 0.35)
        self.assertGreaterEqual(scene_frames.panel_fill_fraction(frame), 0.35)
        blob = scene_frames.frame_card_text(frame)
        self.assertTrue("नुकसान" in blob or "आंकड़ा" in blob or "20%" in blob)
        self.assertEqual(scene_frames.frame_layout_notes(frame), [])
        self.assertLessEqual(scene_pace.empty_row_fraction(frame), 0.30)


if __name__ == "__main__":
    unittest.main()
