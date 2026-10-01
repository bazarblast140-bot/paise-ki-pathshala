"""Grapheme typewriter, click mix, and fact-sourced hook numbers. No network."""
import json
import re
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import hook_check
import hook_motion
import hook_sfx
import self_review

ROOT = Path(__file__).resolve().parent.parent


def _topic(key):
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    return next(topic for topic in bank if topic["key"] == key)


class GraphemeTypewriterTests(unittest.TestCase):
    def test_devanagari_conjuncts_and_matras_stay_whole(self):
        self.assertEqual(hook_motion.grapheme_clusters("क्ष"), ["क्ष"])
        self.assertEqual(hook_motion.grapheme_clusters("कि"), ["कि"])
        self.assertEqual(hook_motion.grapheme_clusters("क़"), ["क़"])
        self.assertEqual(hook_motion.grapheme_clusters("की"), ["की"])
        self.assertEqual(hook_motion.grapheme_clusters("क्षत्रिय"), ["क्ष", "त्रि", "य"])
        self.assertEqual(hook_motion.grapheme_clusters("गिरावट"), ["गि", "रा", "व", "ट"])
        for text in ("क्ष", "त्रि", "कि", "क़", "की", "क्षत्रिय"):
            for cluster in hook_motion.grapheme_clusters(text):
                self.assertFalse(cluster.endswith("\u094d"))
                self.assertNotEqual(cluster, "\u093f")
                self.assertNotEqual(cluster, "\u0940")

    def test_a_split_virama_is_joined_before_the_next_cluster(self):
        with mock.patch("hook_motion.regex.findall", return_value=["क्", "ष", "त्रि", "य"]):
            self.assertEqual(hook_motion.grapheme_clusters("क्षत्रिय"), ["क्ष", "त्रि", "य"])

    def test_reveal_finishes_by_1_4_seconds_and_prefixes_are_clusters(self):
        line = "निचली wick बॉडी से कम से कम 2 गुना"
        steps = hook_motion.reveal_steps(line)
        clusters = hook_motion.grapheme_clusters(line)
        built, prefixes = "", []
        for cluster in clusters:
            built += cluster
            prefixes.append(built)
        self.assertEqual([prefix for _t, prefix in steps], prefixes)
        self.assertLessEqual(steps[-1][0], 1.4)
        self.assertEqual(steps[-1][1], line)
        self.assertEqual(hook_motion.visible_at(line, 1.4), line)
        self.assertEqual(hook_motion.visible_at(line, 1.6), line)
        partial = hook_motion.visible_at(line, 0.5)
        self.assertTrue(partial)
        self.assertNotEqual(partial, line)
        self.assertTrue(line.startswith(partial))
        self.assertEqual(len(hook_motion.click_times(line)), len(clusters))

    def test_ass_hook_is_solid_gold_with_an_offset_shadow(self):
        events = hook_motion.dialogues("क्षत्रिय क्यों मायने रखता है?", 8.0, 540, 1170, 72)
        blob = "\n".join(events)
        self.assertIn(hook_motion.GOLD, blob)
        self.assertIn(hook_motion.SHADOW, blob)
        self.assertIn("\\xshad4", blob)
        self.assertIn("\\yshad4", blob)
        self.assertIn("\\1a&H00&", blob)
        self.assertNotIn("\\fad", blob)
        self.assertIsNone(re.search(r"\\[kK]", blob))
        self.assertTrue(blob.startswith("Dialogue: 1,0:00:00.00,"))
        self.assertIn("क्षत्रिय क्यों मायने रखता है?", events[-1])
        times = []
        for event in events:
            start, end = event.split(",")[1:3]
            times.append((start, end))
        for (_start, end), (nxt, _end2) in zip(times, times[1:]):
            self.assertLessEqual(end, nxt)


class ClickMixTests(unittest.TestCase):
    def test_clicks_sit_under_the_voice_without_moving_or_clipping_it(self):
        sr = hook_sfx.SR
        voice = np.zeros(sr, dtype=np.float32)
        voice[30000] = 0.4
        mixed = hook_sfx.mix_under_voice(voice, [0.0], gain=hook_sfx.GAIN, sr=sr)
        self.assertEqual(mixed.shape, voice.shape)
        self.assertAlmostEqual(float(mixed[30000]), 0.4, places=4)
        click_peak = float(np.max(np.abs(mixed[: int(0.03 * sr)])))
        self.assertAlmostEqual(click_peak, 0.18, delta=0.01)
        self.assertAlmostEqual(hook_sfx.GAIN, 0.18)

        loud = np.full(sr, 0.95, dtype=np.float32)
        limited = hook_sfx.mix_under_voice(loud, [0.0, 0.01, 0.02], gain=0.18, sr=sr)
        self.assertEqual(limited.shape, loud.shape)
        self.assertLessEqual(float(np.max(np.abs(limited))), 1.0)


class HookNumberProvenanceTests(unittest.TestCase):
    def test_hammer_hook_uses_the_fact_number_and_scores_full(self):
        topic = _topic("hammer")
        line = hook_check.make_hook_line(topic)
        self.assertIn("2", hook_check.extract_numbers(line))
        self.assertEqual(hook_check.number_provenance(line, topic).get("2"), "facts")
        score, fails = self_review.score_hook({"hook_line": line}, topic)
        self.assertEqual(fails, [])
        self.assertEqual(score, 100)

    def test_an_invented_number_has_no_source_and_misses_the_bar(self):
        topic = _topic("hammer")
        line = "कीमत 99999 पर थी"
        self.assertIsNone(hook_check.number_provenance(line, topic).get("99999"))
        score, fails = self_review.score_hook({"hook_line": line}, topic)
        self.assertTrue(fails)
        self.assertLess(score, self_review.PART_BAR)
        self.assertEqual(self_review.PART_BAR, 60)
        self.assertEqual(self_review.OVERALL_BAR, 75)

    def test_real_data_hook_does_not_reveal_the_answer(self):
        topic = _topic("titan_2010")
        line = hook_check.make_hook_line(topic)
        nums = hook_check.extract_numbers(line)
        self.assertNotIn("4231", nums)
        self.assertNotIn("59.53", nums)
        self.assertNotIn("595", nums)
        for number, source in hook_check.number_provenance(line, topic).items():
            self.assertIsNotNone(source, number)
        score, fails = self_review.score_hook({"hook_line": line}, topic)
        self.assertEqual((score, fails), (100, []))
