"""Holds from Edge dry run 37008112236. One test for each fix."""
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import board_short
import hook_check
import scene_frames
import scene_pace
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
        # The earlier slice still shows the words already said, not a blank card.
        self.assertIn("लाख", texts["pehchan"][0])


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
        self.assertIsNone(kwargs["log_prob_threshold"])
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
        names = {layer.get("text") for layer in frame.text_layers}
        for banned in ("हिसाब", "एक बात", "पहचान", "जाँच"):
            self.assertNotIn(banned, names)
        body = [
            layer for layer in frame.text_layers
            if layer.get("text") and not layer.get("faint") and layer.get("role") != "brand"
        ]
        self.assertTrue(body)
        self.assertGreaterEqual(max(layer["size"] for layer in body), 64)
        blob = scene_frames.frame_card_text(frame)
        self.assertIn("चाहिए", blob)
        self.assertNotRegex(blob, r"₹[\d,]+\s+₹[\d,]+,")
        self.assertGreaterEqual(frame.panel_fill, 0.35)
        import safe_area
        boxes = [
            layer["box"] for layer in frame.text_layers
            if layer.get("text") and layer.get("box") and not layer.get("faint")
        ]
        self.assertEqual(safe_area.overlapping(boxes), [])
        short = _topic("risk_daily_loss_limit")
        short["_scene_cards"] = {"hook": ["दो लाख की हद"]}
        short["_scene_figures"] = {"hook": [[]]}
        short["_spoken_beats"] = {"hook": "दो लाख की हद"}
        small = scene_frames.frames_for(short, "hook", 1, {"mode": "schematic"})[0][0]
        self.assertGreaterEqual(small.panel_fill, 0.35)
        shown = scene_frames.frame_card_text(small)
        self.assertIn("दो लाख की हद", shown)
        self.assertNotIn("2,00,000", shown)


class BoardVisualTests(unittest.TestCase):
    def test_chart_is_proportional_or_absent_and_headers_are_not_beat_names(self):
        import safe_area
        topic = _topic("risk_daily_loss_limit")
        topic["_spoken_beats"] = {
            "hook": "पूँजी ₹2,00,000 पर दिन की हद 2% है, यानी ₹4,000।",
        }
        topic["_scene_cards"] = {"hook": ["पूँजी ₹2,00,000 पर दिन की हद 2% है, यानी ₹4,000।"]}
        topic["_scene_figures"] = {"hook": [["₹2,00,000", "₹4,000", "2%"]]}
        frame = scene_frames.frames_for(topic, "hook", 1, {"mode": "schematic"})[0][0]
        bars = frame.chart_bar_boxes
        self.assertEqual(len(bars), 2)
        small = [bar for bar in bars if bar[4] == 4000.0][0]
        large = [bar for bar in bars if bar[4] == 200000.0][0]
        wide = (large[2] - large[0]) >= (large[3] - large[1])
        if wide:
            self.assertGreater((large[2] - large[0]) / max(1.0, (small[2] - small[0])), 8)
        else:
            self.assertGreater((large[3] - large[1]) / max(1.0, (small[3] - small[1])), 8)
        region = frame.chart_region
        for bar in bars:
            self.assertGreaterEqual(bar[0], region[0] - 1)
            self.assertGreaterEqual(bar[1], region[1] - 1)
            self.assertLessEqual(bar[2], region[2] + 1)
            self.assertLessEqual(bar[3], region[3] + 1)
        labels = [
            layer for layer in frame.text_layers
            if layer.get("text") in ("₹2,00,000", "₹4,000", "₹200,000")
        ]
        self.assertGreaterEqual(len(labels), 1)
        for layer in frame.text_layers:
            self.assertNotIn(layer.get("text"), ("एक बात", "पहचान", "हिसाब", "जाँच", "मान 2%", "सीमा 2%"))
            if layer.get("box"):
                self.assertTrue(safe_area.inside(layer["box"]), layer.get("text"))
        blank = _topic("risk_daily_loss_limit")
        blank["_spoken_beats"] = {"hook": "दिन की हद 2% पर रुकें, फिर 2% याद रखें।"}
        blank["_scene_cards"] = {"hook": ["दिन की हद 2% पर रुकें, फिर 2% याद रखें।"]}
        blank["_scene_figures"] = {"hook": [["2%", "2%"]]}
        quiet = scene_frames.frames_for(blank, "hook", 1, {"mode": "schematic"})[0][0]
        self.assertEqual(quiet.chart_bar_boxes, [])

    def test_chart_labels_may_repeat_the_card_numbers(self):
        """Dry run 37087619108: ₹4,000 and ₹2,00,000 on the chart and the card."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        line = "पूँजी ₹2,00,000 पर दिन की हद 2% है, यानी ₹4,000।"
        topic = _topic("risk_daily_loss_limit")
        topic["_spoken_beats"] = {"hook": line}
        topic["_scene_cards"] = {"hook": [line]}
        topic["_scene_figures"] = {"hook": [["₹2,00,000", "₹4,000", "2%"]]}
        frame = scene_frames.frames_for(topic, "hook", 1, {"mode": "schematic"})[0][0]
        notes = scene_frames.frame_layout_notes(frame)
        self.assertFalse(any(note.startswith("repeated text") for note in notes), notes)
        chart = {
            layer.get("text") for layer in frame.text_layers if layer.get("role") == "chart"
        }
        self.assertTrue({"₹2,00,000", "₹4,000"} <= chart, chart)
        echoed = scene_frames._blank()
        for text, x in (("₹4,000", 180), ("₹2,00,000", 620)):
            scene_frames._text(echoed, x, 420, text, (255, 255, 255), 64, anchor="lt", band=False)
            scene_frames._text(
                echoed, x, 1400, text, (255, 255, 255), 26,
                anchor="lt", band=False, role="chart",
            )
        echoed_notes = scene_frames.frame_layout_notes(echoed)
        self.assertFalse(any("repeated text" in note for note in echoed_notes), echoed_notes)
        pair = scene_frames._blank()
        scene_frames._text(pair, 180, 420, "₹4,000", (255, 255, 255), 64, anchor="lt", band=False)
        scene_frames._text(pair, 180, 820, "₹4,000", (255, 255, 255), 64, anchor="lt", band=False)
        self.assertTrue(any(
            note == "repeated text: ₹4,000" for note in scene_frames.frame_layout_notes(pair)
        ))
        beats = {
            "hook": line,
            "pehchan": "₹2,00,000 का 2% निकालो, वह ₹4,000 की हद है।",
            "matlab": "₹4,000 की हद बुरे दिन को पूरे महीने से रोकती है।",
            "karein": "हद ₹4,000 हिट हो तो टर्मिनल बंद करो।",
        }
        narration = {"title": "दिन की हद", "hook_line": "2% नियम", "beats": beats, "source": "llm"}
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            board_short._refuse_craft_hold(topic, {}, narration)
        self.assertNotIn("craft would HOLD", err.getvalue())
        self.assertNotIn("repeated text", err.getvalue())

    def test_a_card_transition_shows_one_card_and_captions_are_not_burned(self):
        from PIL import Image
        import scene_transition
        first = Image.new("RGB", (40, 40), (10, 20, 30))
        second = Image.new("RGB", (40, 40), (200, 10, 10))
        mid = scene_transition.composite(first, second, 0.5, "slideleft")
        late = scene_transition.composite(first, second, 0.9, "slideleft")
        self.assertEqual(list(mid.getdata()), list(first.convert("RGB").getdata()))
        self.assertEqual(list(late.getdata()), list(second.convert("RGB").getdata()))
        topic = _topic("risk_daily_loss_limit")
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        spoken = {beat: "₹4,000 की हद पहले तय है।" for beat in times}
        ass = board_short.build_ass(topic, times, spoken, "हद क्यों?", "पोल")
        self.assertNotIn("Dialogue: 2,", ass)
        self.assertIn(board_short.DISCLAIMER, ass)
        self.assertIn("\\fs39", ass)
        import safe_area
        box = board_short._top_box(
            board_short.DISCLAIMER, 540, safe_area.SOURCE_Y, board_short.DISCLAIMER_SIZE,
        )
        self.assertTrue(safe_area.inside(box), box)
        self.assertLess(box[3], safe_area.BRAND_Y)
        cues = board_short.plan_cues(spoken, times, None)
        self.assertTrue(cues)


class HindiConcreteTests(unittest.TestCase):
    def test_a_hindi_number_word_keeps_the_beat_concrete(self):
        beats = {
            "hook": "हर दिन दो प्रतिशत की हद क्यों? एक बुरा दिन पूरा महीना खा सकता है।",
            "pehchan": "ऑप्शन बायर का ब्रेकईवन बाईस हज़ार छह सौ है।",
            "matlab": "वापसी छोटे हुए पैसे पर गिनी जाती है।",
            "karein": "यह जोखिम का नियम है।",
        }
        vague = scene_pace.vague_beats(beats)
        self.assertNotIn("hook", vague)
        self.assertNotIn("pehchan", vague)
        self.assertIn("matlab", vague)


class LatinAndNumberGateTests(unittest.TestCase):
    def test_latin_screen_words_and_negated_recovery_are_blocked(self):
        raw = "daily limit terminal chart edge"
        self.assertEqual(board_short.latin_misread_words(raw), [])
        self.assertEqual(board_short.latin_misread_words("यहाँ QQ बचा"), ["QQ"])
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
        self.assertFalse(any("foobar" in note for note in leftover))
        self.assertEqual(board_short.latin_misread_words("ROCE और operating और EMI"), [])
        self.assertEqual(board_short.latin_misread_words("कार्ड पर OK लिखा"), ["OK"])
        journal = "Win rate 40% पर profit expectancy हिसाब कैसे निकलेगा"
        self.assertEqual(board_short.latin_misread_words(journal), [])
        charges = "brokerage, STT और slippage के बाद +0.2R घट सकता है"
        self.assertEqual(board_short.latin_misread_words(charges), [])
        folded_journal = board_short.fold_roman_words(journal)
        self.assertNotIn("Win", folded_journal)
        self.assertIn("विन रेट", folded_journal)

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


_CARD_END = re.compile(
    r"(?:[,，]|(?:^|\s)(?:और|तो|पर|कि|या|लेकिन|मगर|तथा|क्योंकि))\s*$"
)


def _beat_clock(spoken):
    t = 0.0
    times = {}
    for beat in ("hook", "pehchan", "matlab", "karein"):
        words = str(spoken.get(beat) or "").split()
        dur = max(4.0, len(words) / 1.9)
        times[beat] = (t, t + dur)
        t += dur
    return times


class CaptionSentenceCardTests(unittest.TestCase):
    def test_every_caption_number_is_on_its_scene_and_the_card_is_not_cut(self):
        spoken = {
            "hook": "रोज़ का घाटा ₹4,000 पर क्यों रोकें? पूँजी ₹2 लाख है, और दिन की हद सिर्फ 2% मानी जाती है।",
            "pehchan": "अब इसे पहचानें: हिसाब सीधा है। उदाहरण: ₹2,00,000 का 2% = ₹4,000। यह ₹4,000 खाते में घाटा दिखने पर वह दिन किताब बंद करने की रेखा है।",
            "matlab": "इसका मतलब: ₹4,000 की हद पहले तय है। दिन खराब हो तो नुकसान ₹2,00,000 के 2% पर रुक जाता है। बाकी पूँजी अगले दिन के लिए सुरक्षित रहती है।",
            "karein": "हद पहले लिखो। ₹4,000 हिट हुई तो टर्मिनल बंद। यह 2% उदाहरण है। हद अपनी क्षमता से तय करो। एक बुरा दिन पूरा महीना न बिगाड़े।",
        }
        loss = {
            "hook": "50% घटे तो 100% क्यों चाहिए? यही नुकसान का गणित है। 10% नुकसान के बाद 11% चाहिए।",
            "pehchan": "इसे पहचानें: पहले 10% घटे तो 11% चाहिए। फिर 20% घटे तो 25% चाहिए। ऐसे ही 50% घटे तो 100% चाहिए।",
            "matlab": "इसका मतलब: वापसी छोटे हुए पैसे पर गिनी जाती है। उदाहरण: 10% नुकसान के बाद 11% चाहिए। यह 10% नहीं, 11% है।",
            "karein": "50% गिरने के बाद 100% वापसी चाहिए। 20% पर 25% चाहिए। इसलिए एसएल छोटा रखें, ताकि पूंजी बचे।",
        }
        # A following figure stays on its own cue. Joining it onto "2% ="
        # puts ₹4,000 on the caption before that scene's card has it.
        example = board_short._cue_texts("उदाहरण: ₹2,00,000 का 2% = ₹4,000।")
        self.assertFalse(any("2%" in cue and "4,000" in cue for cue in example), example)
        for script in (spoken, loss):
            times = _beat_clock(script)
            audio = []
            for beat, (t0, t1) in times.items():
                tokens = script[beat].split()
                dur = max(1e-6, t1 - t0)
                for i, word in enumerate(tokens):
                    start = t0 + dur * (i + 0.5) / len(tokens)
                    audio.append({"word": word, "start": start, "end": start + 0.15})
            scenes = scene_pace.plan_scenes(times)
            board_short._CUE_CACHE.clear()
            texts, _figures = board_short.scene_cards_for(script, times, scenes, audio)
            cards = []
            cursor = {}
            for scene in scenes:
                beat = scene["beat"]
                i = cursor.get(beat, 0)
                cursor[beat] = i + 1
                cards.append(texts[beat][i])
            for card in cards:
                self.assertFalse(_CARD_END.search(card or ""), card)
            topic = {"_scene_cards": texts, "_spoken_beats": script}
            groups = {}
            for scene in scenes:
                groups.setdefault(scene["beat"], []).append(scene)
            for beat, group in groups.items():
                frames, _flags = scene_frames.frames_for(
                    {**topic, "_live_short": True}, beat, len(group), {"mode": "schematic"},
                )
                for scene, frame in zip(group, frames):
                    scene["frame"] = frame
                    blob = scene_frames.frame_card_text(frame)
                    self.assertNotIn("आंकड़ा ", blob)
            notes = [
                note for note in board_short.number_faults_for(scenes, script, times, audio)
                if note.startswith("caption number")
            ]
            self.assertEqual(notes, [])

    def test_numbers_are_highlighted_and_a_chart_needs_two_comparable_figures(self):
        topic = _topic("loss_recovery")
        topic["_spoken_beats"] = {
            "matlab": "उदाहरण: 20% घटे तो 25% चाहिए।",
            "hook": "₹4,000 की हद पहले तय है।",
        }
        pair = scene_frames.frames_for(topic, "matlab", 1, {"mode": "schematic"})[0][0]
        self.assertGreaterEqual(len(pair.chart_bar_boxes), 2)
        body = [
            layer for layer in pair.text_layers
            if layer.get("text") and layer.get("text") not in ("हिसाब", "एक बात", "पहचान", "जाँच")
        ]
        self.assertTrue(body)
        gold = False
        for layer in body:
            sprite = layer.get("image")
            if sprite is None:
                continue
            arr = np.asarray(sprite)
            if arr.ndim != 3 or arr.shape[2] < 3:
                continue
            ink = arr[:, :, 3] > 40 if arr.shape[2] == 4 else arr.max(axis=2) > 40
            hit = ink & (arr[:, :, 0] > 170) & (arr[:, :, 1] > 120) & (arr[:, :, 2] < 140)
            if int(hit.sum()) > 8:
                gold = True
        self.assertTrue(gold)
        self.assertIn("20", scene_frames.frame_card_text(pair))
        self.assertNotIn("आंकड़ा", scene_frames.frame_card_text(pair))
        lone = _topic("risk_daily_loss_limit")
        lone["_spoken_beats"] = {"hook": "₹4,000 की हद पहले तय है।"}
        one = scene_frames.frames_for(lone, "hook", 1, {"mode": "schematic"})[0][0]
        self.assertEqual(one.chart_bar_boxes, [])
        rupees = scene_frames.comparable_chart_figures(["₹4,000", "₹2,00,000"])
        self.assertEqual(len(rupees), 2)
        self.assertEqual(scene_frames.comparable_chart_figures(["2%", "2%"]), [])
        percents = scene_frames.comparable_chart_figures(["20%", "25%"])
        self.assertEqual(len(percents), 2)


class TitleTermRepairTests(unittest.TestCase):
    def test_the_prompt_and_the_repair_name_the_missing_devanagari_term(self):
        topic = _topic("risk_daily_loss_limit")
        system = board_short.four_live_system(topic)
        self.assertIn("डेली लॉस लिमिट", system)
        self.assertIn("कैसे", system)
        self.assertIn("hook", system)
        self.assertIn("karein", system)
        draft = {
            "title": "डेली लॉस लिमिट पर रुकें",
            "hook_line": "रोज़ का घाटा क्यों रोकें?",
            "beats": {
                "hook": "रोज़ का घाटा ₹4,000 पर क्यों रोकें?",
                "pehchan": "उदाहरण: ₹2,00,000 का 2% = ₹4,000।",
                "matlab": "₹4,000 की हद पहले तय है।",
                "karein": "₹4,000 हिट हुई तो टर्मिनल बंद।",
            },
        }
        self.assertEqual(board_short.key_phrase_for(draft["title"]), "डेली लॉस लिमिट")
        notes = board_short.title_term_notes(draft)
        self.assertEqual(notes, ["hook missing title term: डेली लॉस लिमिट"])
        self.assertFalse(any(note.startswith("karein ") for note in notes))
        self.assertEqual(
            board_short.route_gate_failure(notes[0], board_short.BEATS),
            ["hook"],
        )
        repair = board_short.repair_instructions(
            draft, ["hook"], topic, failures=notes,
        )
        self.assertIn("डेली लॉस लिमिट", repair)
        self.assertIn("Missing title term: डेली लॉस लिमिट", repair)
        self.assertIn("Write that phrase in the hook.", repair)
        self.assertNotIn("in the karein", repair)
        covered = dict(draft)
        covered["beats"] = dict(draft["beats"])
        covered["beats"]["hook"] = "डेली लॉस लिमिट पर ₹4,000 क्यों रोकें?"
        self.assertEqual(board_short.title_term_notes(covered), [])
        spoken = {"hook": covered["beats"]["hook"]}
        texts, _figures = board_short.scene_cards_for(
            spoken, {"hook": (0.0, 4.0)},
            [{"beat": "hook", "start": 0.0, "end": 4.0}],
        )
        self.assertIn("डेली लॉस लिमिट", texts["hook"][0])
        how = {
            "title": "नुकसान का गणित कैसे घटे",
            "beats": {
                "hook": "50% घटे तो 100% क्यों चाहिए?",
                "karein": "50% गिरने के बाद 100% वापसी चाहिए।",
            },
        }
        self.assertEqual(board_short.key_phrase_for(how["title"]), "नुकसान का गणित")
        how_notes = board_short.title_term_notes(how)
        self.assertEqual(how_notes, ["hook missing title term: नुकसान का गणित"])
        self.assertFalse(any("कैसे" in note or "घटे" in note or note.startswith("karein") for note in how_notes))
        self.assertEqual(board_short.key_phrase_for("यह नियम है"), "")
        self.assertEqual(board_short.title_term_notes({"title": "यह नियम है", "beats": {"hook": "क्या यह है?"}}), [])


class LeakAndDoubleTests(unittest.TestCase):
    def test_chart_filler_traders_and_latin_sl_are_blocked(self):
        order = board_short.BEATS
        chart = board_short.narrator_leak_notes(
            {"pehchan": "यही नियम चार्ट पर दिखता है।"}, order,
        )
        self.assertTrue(chart)
        shown = board_short.narrator_leak_notes(
            {"pehchan": "10% और 20% चार्ट पर दिखता है।"}, order,
        )
        self.assertTrue(shown)
        quiet = board_short.narrator_leak_notes(
            {"pehchan": "10% और 20% का यही नियम है।"}, order,
        )
        self.assertEqual(quiet, [])
        no_chart = board_short.narrator_leak_notes(
            {"matlab": "यह नियम चार्ट में है।"}, order,
        )
        self.assertTrue(no_chart)
        traders = board_short.narrator_leak_notes(
            {"karein": "अब आगे ट्रेडर्स यही चेक करते हैं: 50% वापसी चाहिए।"}, order,
        )
        self.assertTrue(traders)
        folded_sl = board_short.fold_roman_words("इसलिए SL छोटा रखें और revenge trading से trade बंद।")
        self.assertIn("स्टॉप लॉस", folded_sl)
        self.assertIn("रिवेंज ट्रेडिंग", folded_sl)
        self.assertIn("ट्रेड", folded_sl)
        self.assertNotIn("SL", folded_sl)
        self.assertNotIn("revenge", folded_sl.lower())
        self.assertEqual(board_short.latin_misread_words("इसलिए SL छोटा रखें"), [])
        self.assertEqual(
            board_short.narrator_leak_notes({"karein": "इसलिए SL छोटा रखें।"}, order),
            [],
        )
        self.assertEqual(board_short.latin_misread_words("कार्ड पर OK लिखा"), ["OK"])

    def test_a_vague_double_is_blocked_and_the_50_percent_pair_is_not(self):
        topic = _topic("loss_recovery")
        bad = "20% पर 25% चाहिए, यानी नुकसान बड़ा तो मेहनत दुगनी।"
        notes = hook_check.fact_contradiction_notes(bad, topic)
        self.assertTrue(any("25%" in note and "double" in note for note in notes))
        vague = "नुकसान बड़ा तो मेहनत दुगनी।"
        self.assertTrue(hook_check.fact_contradiction_notes(vague, topic))
        backed = "50% घटे तो 100% चाहिए, यानी वापसी दुगनी।"
        self.assertEqual(hook_check.fact_contradiction_notes(backed, topic), [])
        pair = "50% घटे तो 100% चाहिए।"
        self.assertEqual(hook_check.fact_contradiction_notes(pair, topic), [])
        system = board_short.four_live_system(topic)
        self.assertIn("25%", system)
        self.assertIn("दुगनी", system)
        score, acc = self_review.score_accuracy(
            {"hook_line": "वापसी क्यों?", "beats": {"karein": bad}},
            topic,
        )
        self.assertEqual(score, 0)
        self.assertTrue(acc)


class KeyPhraseTrimTests(unittest.TestCase):
    def test_hook_and_beat_trims_keep_the_key_phrase_sentence(self):
        phrase = "डेली लॉस लिमिट"
        title = "डेली लॉस लिमिट पर रुकें"
        question = "रोज़ का घाटा क्यों रोकें?"
        filler = "यह कदम अलग बात है।"
        carrier = "यही डेली लॉस लिमिट है।"
        text = f"{question} {filler} {carrier}"
        fitted = board_short.fit_live_hook(text, 6, 10, question, title)
        self.assertIn(phrase, fitted)
        self.assertNotIn("अलग बात", fitted)
        only = f"{question} {carrier}"
        kept = board_short.fit_live_hook(only, 2, 4, question, title)
        self.assertIn(phrase, kept)
        both = [carrier, "दूसरी डेली लॉस लिमिट वाली पंक्ति है।"]
        self.assertIsNone(board_short._drop_index(both, "karein", "", 1, phrase))
        topic = _topic("risk_daily_loss_limit")
        long_tail = " ".join(["अलग"] * 40) + "।"
        narration = {
            "title": title,
            "hook_line": question,
            "beats": {
                "hook": f"{question} {phrase} पर रुकना है।",
                "pehchan": "पूँजी का नियम पहले लिखो।",
                "matlab": "उदाहरण: ₹2,00,000 का 2% = ₹4,000।",
                "karein": f"{long_tail} {carrier}",
            },
        }
        board_short.apply_live_draft(narration, topic)
        self.assertIn(phrase, narration["beats"]["karein"])
        self.assertIn(phrase, narration["beats"]["hook"])


class SoftReviewTests(unittest.TestCase):
    def _parts(self, **over):
        parts = {
            "hook": 100, "visual": 90, "accuracy": 100,
            "trend": 100, "craft": 0, "sync": 100,
        }
        parts.update(over)
        return parts

    def test_soft_75_to_85_is_review_and_hard_misses_stay_hold(self):
        soft = self_review._apply_review_flag(self_review._pack(self._parts(), ["craft"]))
        self.assertEqual(soft["overall"], 81.7)
        self.assertTrue(soft["review_flag"])
        self.assertEqual(soft["verdict"], "REVIEW")
        self.assertFalse(soft["public_ok"])
        synced = self_review._apply_review_flag(
            self_review._pack(self._parts(craft=100, sync=0), ["sync"]),
        )
        self.assertEqual(synced["overall"], 81.7)
        self.assertFalse(synced["review_flag"])
        self.assertEqual(synced["verdict"], "HOLD")
        self.assertFalse(synced["public_ok"])
        maths = self_review._apply_review_flag(
            self_review._pack(self._parts(craft=100, accuracy=0), ["accuracy"]),
        )
        self.assertFalse(maths["review_flag"])
        self.assertEqual(maths["verdict"], "HOLD")
        passed = self_review._apply_review_flag(self_review._pack(self._parts(craft=100), []))
        self.assertTrue(passed["public_ok"])
        self.assertFalse(passed["review_flag"])
        self.assertEqual(passed["verdict"], "PASS")

    def test_soft_trend_above_85_is_review_and_pre_render_does_not_hold(self):
        """Dry run 37095205924: trend 45 with paid sync would be overall 89.2."""
        import io
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        parts = {
            "hook": 100, "visual": 90, "accuracy": 100,
            "trend": 45, "craft": 100, "sync": 100,
        }
        soft = self_review._apply_review_flag(self_review._pack(
            parts, ["topic does not match data/trends.json"],
        ))
        self.assertEqual(soft["overall"], 89.2)
        self.assertEqual(soft["verdict"], "REVIEW")
        self.assertTrue(soft["review_flag"])
        self.assertFalse(soft["public_ok"])
        unpaid = dict(parts, sync=0)
        held = self_review._apply_review_flag(self_review._pack(unpaid, ["sync"]))
        self.assertEqual(held["overall"], 72.5)
        self.assertEqual(held["verdict"], "HOLD")
        self.assertFalse(held["review_flag"])
        fallback = self_review._pack(parts, ["fallback"])
        fallback["narration_fallback"] = True
        blocked = self_review._apply_review_flag(fallback)
        self.assertEqual(blocked["verdict"], "HOLD")

        payload = {"review": soft}

        def render(*_args, **_kwargs):
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")
            (board_short.RUN / "self_review.json").write_text(
                json.dumps(payload["review"]), encoding="utf-8",
            )
            return {"final_dur": 55.0}

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run), \
                 mock.patch.object(board_short, "_render_short", side_effect=render), \
                 mock.patch.object(board_short, "_eleven_spent", return_value=0), \
                 mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DRY_RUN", None)
                os.environ.pop("APPROVED", None)
                err = io.StringIO()
                with redirect_stderr(err), self.assertRaises(SystemExit):
                    board_short.prove_full_render({}, {}, {}, {})
                text = err.getvalue()
                self.assertNotIn("pre-render HOLD", text)
                self.assertIn("REVIEW", text)
                os.environ["APPROVED"] = "true"
                allowed = board_short.prove_full_render({}, {}, {}, {})
                self.assertEqual(allowed["final_dur"], 55.0)
                payload["review"] = held
                err = io.StringIO()
                with redirect_stderr(err), self.assertRaises(SystemExit):
                    board_short.prove_full_render({}, {}, {}, {})
                self.assertIn("pre-render HOLD", err.getvalue())
            os.environ.pop("APPROVED", None)

        publish = (ROOT / ".github" / "workflows" / "paise-ki-pathshala.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("vars.AUTO_APPROVE_REVIEW", publish)
        line = "APPROVED: ${{ github.event_name == 'schedule' && vars.AUTO_APPROVE_REVIEW || inputs.approved }}"
        self.assertEqual(publish.count(line), 2)
        call = publish.split("workflow_dispatch:")[0]
        self.assertIn("type: boolean", call)
        self.assertIn("default: false", call)
        dispatch = publish.split("workflow_dispatch:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("approved:", dispatch)
        self.assertIn('default: "false"', dispatch)

    def test_dry_run_keeps_a_review_mp4_and_live_refuses_without_approval(self):
        import io
        import tempfile
        from contextlib import redirect_stderr, redirect_stdout
        from pathlib import Path

        import narration_dry_run
        import review_gate

        review = {
            "public_ok": False,
            "review_flag": True,
            "verdict": "REVIEW",
            "overall": 81.7,
            "summary": "overall 81.7 (craft 0)",
            "parts": self._parts(),
        }

        def render(*_args, **_kwargs):
            (board_short.RUN / "final.mp4").write_bytes(b"mp4")
            (board_short.RUN / "self_review.json").write_text(
                json.dumps(review), encoding="utf-8",
            )
            return {"final_dur": 55.0}

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(board_short, "RUN", run), \
                 mock.patch.object(board_short, "_render_short", side_effect=render), \
                 mock.patch.object(board_short, "_eleven_spent", return_value=0):
                with mock.patch.dict(os.environ, {"DRY_RUN": "1"}, clear=False):
                    os.environ.pop("APPROVED", None)
                    proven = board_short.prove_full_render({}, {}, {}, {})
                    self.assertEqual(proven["final_dur"], 55.0)
                    self.assertTrue((run / "final.mp4").is_file())
                out = io.StringIO()
                with redirect_stdout(out):
                    narration_dry_run._print_self_review("risk_daily_loss_limit")
                self.assertIn("verdict REVIEW", out.getvalue())
                narration_dry_run._keep_topic_artifacts("risk_daily_loss_limit")
                self.assertTrue((run / "final_risk_daily_loss_limit.mp4").is_file())
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("DRY_RUN", None)
                    os.environ.pop("APPROVED", None)
                    err = io.StringIO()
                    with redirect_stderr(err), self.assertRaises(SystemExit):
                        board_short.prove_full_render({}, {}, {}, {})
                    self.assertIn("REVIEW", err.getvalue())
                    self.assertIn("approved=true", err.getvalue())
                    self.assertTrue((run / "final.mp4").is_file())
                    os.environ["APPROVED"] = "true"
                    allowed = board_short.prove_full_render({}, {}, {}, {})
                    self.assertEqual(allowed["final_dur"], 55.0)
                (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
                (run / "verify_meta.json").write_text(json.dumps({
                    "ai_verdict_valid": True, "verdict": "READY", "public_ready": True,
                }), encoding="utf-8")
                (run / "self_review.json").write_text(json.dumps(review), encoding="utf-8")
                os.environ.pop("APPROVED", None)
                self.assertFalse(review_gate.files_allow_public(run, "short"))
                os.environ["APPROVED"] = "true"
                self.assertTrue(review_gate.files_allow_public(run, "short"))
                hold = dict(review, review_flag=False, verdict="HOLD")
                (run / "self_review.json").write_text(json.dumps(hold), encoding="utf-8")
                self.assertFalse(review_gate.files_allow_public(run, "short"))
                os.environ.pop("APPROVED", None)


if __name__ == "__main__":
    unittest.main()
