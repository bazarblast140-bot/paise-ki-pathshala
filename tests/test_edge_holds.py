"""Holds from Edge dry run 37008112236. One test for each fix."""
import json
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
        head = next(layer for layer in frame.text_layers if layer.get("text") == "हिसाब")
        self.assertGreaterEqual(head["size"], 64)
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
        self.assertEqual(rupees, [])
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
        notes = board_short.title_term_notes(draft)
        self.assertIn("hook missing title term: डेली", notes)
        self.assertIn("karein missing title term: लिमिट", notes)
        self.assertEqual(
            board_short.route_gate_failure("hook missing title term: डेली", board_short.BEATS),
            ["hook"],
        )
        repair = board_short.repair_instructions(
            draft, ["hook", "karein"], topic,
            failures=["hook missing title term: डेली", "karein missing title term: लिमिट"],
        )
        self.assertIn("डेली लॉस लिमिट", repair)
        self.assertIn("Missing title term: डेली", repair)
        self.assertIn("लिमिट", repair)
        covered = dict(draft)
        covered["title"] = "डेली लॉस लिमिट"
        covered["beats"] = dict(draft["beats"])
        covered["beats"]["hook"] = "डेली लॉस लिमिट पर ₹4,000 क्यों रोकें?"
        covered["beats"]["karein"] = "डेली लॉस लिमिट पर ₹4,000 हिट हुई तो टर्मिनल बंद।"
        self.assertEqual(board_short.title_term_notes(covered), [])
        loss = _topic("loss_recovery")
        how = {
            "title": "नुकसान वापस कैसे आए",
            "beats": {
                "hook": "50% घटे तो 100% क्यों चाहिए?",
                "karein": "50% गिरने के बाद 100% वापसी चाहिए।",
            },
        }
        how_notes = board_short.title_term_notes(how)
        self.assertTrue(any(note == "hook missing title term: कैसे" for note in how_notes))
        self.assertTrue(any("karein missing title term: कैसे" in note for note in how_notes))


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
        self.assertEqual(board_short.latin_misread_words("इसलिए SL छोटा रखें"), ["SL"])
        latin = board_short.narrator_leak_notes(
            {"karein": "इसलिए SL छोटा रखें।"}, order,
        )
        self.assertTrue(any("SL" in note for note in latin))
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


if __name__ == "__main__":
    unittest.main()
