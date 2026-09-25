import json
import tempfile
import unittest
from pathlib import Path

from competitor_patterns import is_near_competitor_duplicate, load_research, pattern_weights, score_topic


class CompetitorPatternTests(unittest.TestCase):
    def test_requires_repeated_observations_and_scores_aggregate_signal(self):
        data = {"observations": [
            {"topic_type": "mistake", "views": 2000, "baseline_views": 1000},
            {"topic_type": "mistake", "views": 1800, "baseline_views": 1000},
            {"hook_style": "one-off", "views": 9000, "baseline_views": 1000},
        ]}
        weights = pattern_weights(data)
        self.assertGreater(weights["topic_type"]["mistake"], 0)
        self.assertNotIn("one-off", weights.get("hook_style", {}))

    def test_market_and_first_party_data_rank_valid_candidate(self):
        topic = {"key": "rbi_update", "cat": "RBI", "research_tags": ["mistake"]}
        score = score_topic(topic, {"topic_type": {"mistake": 0.5}},
                            {"topics": [{"topic_key": "rbi_update", "priority": 2}]},
                            {"topics": {"rbi_update": 1}})
        self.assertGreater(score, 2)

    def test_duplicate_filter_and_missing_files_are_safe(self):
        self.assertTrue(is_near_competitor_duplicate("Support Resistance का नियम", ["support resistance"]))
        self.assertFalse(is_near_competitor_duplicate("SIP में फीस का असर", ["support resistance"]))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_research(Path(directory)), ({}, {}, {}))


if __name__ == "__main__":
    unittest.main()

