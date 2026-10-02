"""Holds from Edge dry run 37008112236. One test for each fix."""
import json
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import board_short
import hook_check
import scene_frames
import self_review
import speak
import sync_gate

ROOT = Path(__file__).resolve().parents[1]


def _topic(key):
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    topic = json.loads(json.dumps(next(item for item in bank["topics"] if item["key"] == key)))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    topic["_voice"] = {"words_per_second": 1.9}
    return topic


class FigureBeforeSpokenTests(unittest.TestCase):
    def test_a_figure_is_not_on_a_scene_that_ends_before_it_is_said(self):
        spoken = {
            "hook": "शुरुआत में यह नियम एक कदम है।",
            "pehchan": "दो लाख की हद ₹2,00,000 पर है।",
        }
        beat_times = {"hook": (0.0, 4.0), "pehchan": (4.0, 10.0)}
        scenes = [
            {"beat": "hook", "start": 0.0, "end": 4.0},
            {"beat": "pehchan", "start": 4.0, "end": 7.0},
            {"beat": "pehchan", "start": 7.0, "end": 10.0},
        ]
        words = [
            {"word": "शुरुआत", "start": 0.3, "end": 0.7},
            {"word": "में", "start": 0.8, "end": 1.1},
            {"word": "यह", "start": 1.2, "end": 1.5},
            {"word": "नियम", "start": 1.6, "end": 2.0},
            {"word": "एक", "start": 2.1, "end": 2.4},
            {"word": "कदम", "start": 2.5, "end": 2.9},
            {"word": "है।", "start": 3.0, "end": 3.4},
            {"word": "दो", "start": 4.4, "end": 4.8},
            {"word": "लाख", "start": 5.0, "end": 5.4},
            {"word": "की", "start": 5.5, "end": 5.8},
            {"word": "हद", "start": 5.9, "end": 6.3},
            {"word": "₹2,00,000", "start": 7.4, "end": 8.2},
            {"word": "पर", "start": 8.4, "end": 8.7},
            {"word": "है।", "start": 8.9, "end": 9.3},
        ]
        texts, figures = board_short.scene_cards_for(spoken, beat_times, scenes, words)
        early = texts["hook"][0] + " " + " ".join(figures["hook"][0])
        self.assertNotIn("200000", early.replace(",", ""))
        self.assertNotIn("2,00,000", texts["hook"][0])
        mid = texts["pehchan"][0] + " " + " ".join(figures["pehchan"][0])
        self.assertNotIn("2,00,000", mid)
        late = texts["pehchan"][1] + " " + " ".join(figures["pehchan"][1])
        self.assertIn("2,00,000", late)
        # The same figure said again on a later card of the beat is not dropped.
        self.assertIn("₹2,00,000", late)


class TitleTermFoldTests(unittest.TestCase):
    def test_latin_title_terms_match_the_devanagari_script(self):
        terms = sync_gate.answer_terms_for("Option buyer का breakeven")
        script = "ऑप्शन बायर का ब्रेकईवन स्ट्राइक और प्रीमियम है।"
        full = set(sync_gate.tokens(script))
        self.assertTrue(terms)
        missing = [term for term in terms if not sync_gate.term_covered(term, full)]
        self.assertEqual(missing, [])
        blob = " ".join(terms)
        for word in ("ऑप्शन", "बायर", "ब्रेकईवन"):
            self.assertIn(word, blob)
        titled = sync_gate.answer_terms_for("Option buyer का breakeven — 22,600 + 120 = 22,720")
        heard = "ऑप्शन बायर का ब्रेकईवन बाईस हज़ार छह सौ प्लस एक सौ बीस बराबर बाईस हज़ार सात सौ बीस है।"
        report = sync_gate.check(
            [{"word": heard, "start": 0.1, "end": 1.0}],
            [{"start": 0.0, "end": 2.0, "beat": "hook", "show": ["ब्रेकईवन"]}],
            {"hook": heard},
            {"hook": (0.0, 2.0)},
            titled,
        )
        self.assertNotIn("title promise", report["reason"])


class HookTitleTrimTests(unittest.TestCase):
    def test_trim_drops_a_filler_and_keeps_the_title_sentence(self):
        question = "नुकसान वापस कैसे आए?"
        filler = "यह कदम नियम है।"
        title_line = "यही नुकसान का गणित है।"
        text = f"{question} {filler} {title_line}"
        self.assertGreater(len(text.split()), 10)
        fitted = board_short.fit_live_hook(text, 8, 10, question, "नुकसान का गणित")
        self.assertIn("गणित", fitted)
        self.assertNotIn("कदम नियम", fitted)
        self.assertGreaterEqual(len(fitted.split()), 8)
        self.assertLessEqual(len(fitted.split()), 10)


class WhisperRetryTests(unittest.TestCase):
    def test_each_beat_uses_temperature_fallback_and_vad(self):
        calls = []

        class Word:
            word = "नियम"
            start = 0.1
            end = 0.4

        class Segment:
            words = [Word()]

        def fake_transcribe(audio, **kwargs):
            calls.append(kwargs)
            return [Segment()], None

        model = mock.Mock(transcribe=fake_transcribe)
        audio = np.zeros(1600, dtype=np.float32)
        with mock.patch.object(sync_gate, "model", return_value=model):
            words = sync_gate.transcribe_pcm(audio)
        self.assertTrue(words)
        self.assertGreaterEqual(len(calls), 1)
        kwargs = calls[0]
        self.assertFalse(kwargs["condition_on_previous_text"])
        self.assertEqual(kwargs["compression_ratio_threshold"], 2.4)
        self.assertIn(0.0, kwargs["temperature"])
        self.assertIn(0.2, kwargs["temperature"])
        self.assertIn(0.4, kwargs["temperature"])
        self.assertTrue(kwargs["vad_filter"])

        decoded = []

        def fake_pcm(blob):
            decoded.append(1)
            return [{"word": "नियम", "start": 0.2, "end": 0.5}]

        with mock.patch.object(sync_gate, "transcribe_pcm", side_effect=fake_pcm), \
             mock.patch.object(sync_gate, "pcm", return_value=audio):
            sync_gate.transcribe_beats(
                {"hook": "h.mp3", "pehchan": "p.mp3"},
                {"hook": (0.0, 2.0), "pehchan": (2.0, 4.0)},
                beats=("hook", "pehchan"),
            )
        self.assertEqual(decoded, [1, 1])


class SpacedMinusTests(unittest.TestCase):
    def test_a_minus_between_words_is_spoken_as_mainas(self):
        said = speak.speakable("स्ट्राइक − प्रीमियम")
        self.assertIn("माइनस", said)
        self.assertNotIn("−", said)
        self.assertNotIn("-", said)
        ranged = speak.speakable("2-3 दिन")
        self.assertNotIn("माइनस", ranged)


class CardChartTests(unittest.TestCase):
    def test_one_bar_is_not_a_chart_and_phrases_are_not_cut(self):
        from PIL import Image
        image = Image.new("RGB", (1080, 1920), (0, 0, 0))
        image.labelled_graphic_boxes = []
        image.text_layers = []
        image.label_boxes = []
        panel = (70, 190, 1010, 1300)
        scene_frames._paint_labelled_chart(image, ["₹4,000"], panel)
        self.assertEqual(getattr(image, "chart_bar_boxes", []), [])
        self.assertEqual(image.labelled_graphic_boxes, [])
        blue = np.asarray(image)
        self.assertEqual(int(((blue[:, :, 2] > 200) & (blue[:, :, 0] < 160)).sum()), 0)

        scene_frames._paint_labelled_chart(image, ["₹4,000", "₹2,00,000"], panel, text_bottom=200)
        bars = image.chart_bar_boxes
        self.assertEqual(len(bars), 2)
        small = [bar for bar in bars if bar[4] == 4000.0][0]
        large = [bar for bar in bars if bar[4] == 200000.0][0]
        self.assertGreater(large[3] - large[1], small[3] - small[1])
        self.assertEqual(image.labelled_graphic_boxes, [])
        image.panel_box = panel
        before = scene_frames.panel_fill_fraction(image)
        image.labelled_graphic_boxes = [tuple(bar[:4]) for bar in bars]
        after = scene_frames.panel_fill_fraction(image)
        image.labelled_graphic_boxes = []
        self.assertGreater(after, before)

        rows = scene_frames.phrases_that_fit(
            "पहला वाक्य पूरा है। दूसरा वाक्य भी पूरा है।",
            limit=12,
            max_lines=2,
        )
        shown = " ".join(rows)
        self.assertTrue(shown.endswith("।"), shown)
        self.assertNotIn("दूसरा", shown)

        topic = _topic("loss_recovery")
        topic["_spoken_beats"] = {
            "matlab": "उदाहरण: 20% घटे तो 25% चाहिए। नुकसान 25% के बाद वापसी अलग है।",
        }
        frame = scene_frames.frames_for(topic, "matlab", 1, {"mode": "schematic"})[0][0]
        head = next(layer for layer in frame.text_layers if layer.get("text") == "हिसाब")
        self.assertGreaterEqual(head["size"], 64)
        blob = scene_frames.frame_card_text(frame)
        self.assertIn("चाहिए", blob)
        self.assertNotRegex(blob, r"₹[\d,]+\s+₹[\d,]+,")
        self.assertGreaterEqual(frame.panel_fill, 0.35)


class LatinAndNumberGateTests(unittest.TestCase):
    def test_latin_screen_words_and_negated_recovery_are_blocked(self):
        raw = "daily limit terminal chart edge"
        self.assertCountEqual(
            board_short.latin_misread_words(raw),
            ["daily", "limit", "terminal", "chart", "edge"],
        )
        folded = board_short.fold_roman_words("daily limit और terminal chart edge")
        self.assertEqual(board_short.latin_misread_words(folded), [])
        self.assertNotIn("daily", folded.lower())
        order = board_short.BEATS
        leaked = board_short.narrator_leak_notes(
            {"matlab": "आपके सामने स्क्रीन पर यह नियम है।"}, order,
        )
        self.assertTrue(leaked)
        leftover = board_short.narrator_leak_notes(
            {"karein": "यह foobar नियम है।"}, order,
        )
        self.assertTrue(any("foobar" in note for note in leftover))

        self.assertIn("75", hook_check.extract_numbers("पचहत्तर प्रतिशत गलत है"))
        self.assertIn("89", hook_check.extract_numbers("नवासी लिख दिया"))
        self.assertIn("4000", hook_check.extract_numbers("चार हज़ार"))
        self.assertIn("200000", hook_check.extract_numbers("दो लाख"))
        self.assertIn("200000", hook_check.extract_numbers("₹2 लाख"))
        self.assertEqual(
            hook_check.extract_numbers("बाईस हज़ार छह सौ प्लस एक सौ बीस"),
            {"22600", "120"},
        )
        self.assertIn("22720", hook_check.extract_numbers("बाईस हज़ार सात सौ बीस"))
        self.assertNotIn("4", hook_check.extract_numbers("4 हज़ार"))
        self.assertNotIn("1", hook_check.extract_numbers("एक नियम"))
        self.assertNotIn("4", hook_check.extract_numbers("चार कदम"))
        topic = _topic("loss_recovery")
        faults = hook_check.live_number_faults(
            {"matlab": "चार हज़ार का हिसाब गलत है।"}, topic, ("matlab",),
        )
        self.assertIn("4000", faults)

        negated = "20% घटे तो 25% नहीं, 20% चाहिए।"
        pair = hook_check.loss_recovery_pair_faults(negated, topic)
        self.assertIn("20", pair)
        self.assertNotIn("25", pair)
        words = "बीस प्रतिशत घटे तो पच्चीस प्रतिशत नहीं, बीस प्रतिशत चाहिए।"
        word_pair = hook_check.loss_recovery_pair_faults(words, topic)
        self.assertIn("20", word_pair)
        self.assertNotIn("25", word_pair)
        still = hook_check.loss_recovery_pair_faults("75% गिरे तो 90% नहीं।", topic)
        self.assertIn("90", still)


class CaptionWindowTests(unittest.TestCase):
    def test_the_0_85_second_window_stops_at_the_scene_end(self):
        spoken = {"hook": "नियम यहाँ है 200000 बाद में"}
        beat_times = {"hook": (0.0, 8.0)}
        words = [
            {"word": "नियम", "start": 0.4, "end": 0.8},
            {"word": "यहाँ", "start": 1.2, "end": 1.6},
            {"word": "है", "start": 2.0, "end": 2.4},
            {"word": "200000", "start": 4.6, "end": 5.1},
            {"word": "बाद", "start": 5.4, "end": 5.8},
            {"word": "में", "start": 6.2, "end": 6.6},
        ]
        near = board_short._script_near(spoken, beat_times, words, 4.0)
        self.assertIn("200000", near)
        clamped = board_short._script_near(
            spoken, beat_times, words, 4.0, scene_end=4.3,
        )
        self.assertNotIn("200000", clamped)
        # 0.2 would miss a word 0.6s ahead. 0.85 includes it.
        ahead = board_short._script_near(spoken, beat_times, words, 4.0)
        self.assertIn("200000", ahead)


class FactWordingTests(unittest.TestCase):
    def test_partial_premium_and_recovery_base_are_in_the_prompt_and_the_gate(self):
        option = _topic("option_breakeven")
        system = board_short.four_live_system(option)
        self.assertIn("only part of the premium", system)
        self.assertIn("at or below the strike", system)
        claim = "22,720 से नीचे पैसा डूबा।"
        notes = hook_check.fact_contradiction_notes(claim, option)
        self.assertTrue(any("premium" in note for note in notes))
        fair = "स्ट्राइक से नीचे पूरा प्रीमियम डूबता है। ब्रेकईवन तक प्रीमियम का कुछ हिस्सा जाता है।"
        self.assertEqual(hook_check.fact_contradiction_notes(fair, option), [])
        score, acc = self_review.score_accuracy(
            {"hook_line": "ब्रेकईवन क्या है?", "beats": {"matlab": claim}},
            option,
        )
        self.assertEqual(score, 0)
        self.assertTrue(acc)

        loss = _topic("loss_recovery")
        loss_system = board_short.four_live_system(loss)
        self.assertIn("smaller amount", loss_system)
        bad = "नुकसान छोटे हुए पैसे पर गिना जाता है।"
        loss_notes = hook_check.fact_contradiction_notes(bad, loss)
        self.assertTrue(any("smaller base" in note for note in loss_notes))
        score, acc = self_review.score_accuracy(
            {"hook_line": "वापसी क्यों ज़्यादा?", "beats": {"matlab": bad}},
            loss,
        )
        self.assertEqual(score, 0)


if __name__ == "__main__":
    unittest.main()
