import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_scenes
import board_short
import buffer_shorts
import pick_topic


def _bank(*keys):
    return {"topics": [
        {"id": i, "key": k, "cat": "X", "topic": k, "used_short": False,
         "beats": {}, "facts": [], "thumb": []}
        for i, k in enumerate(keys, 1)
    ]}


class UnrenderableTopicTests(unittest.TestCase):
    def setUp(self):
        key = next(iter(board_scenes.SCENES))
        self.good = key
        self.bad = "no_such_scene_topic"
        self.assertNotIn(self.bad, board_scenes.SCENES)

    def _pick(self, bank, trends_favour=None):
        research = ({}, {}, {}, None)
        score = (lambda t, *a, **k: 100 if t["key"] == trends_favour else 1)
        with mock.patch.object(pick_topic, "score_topic", side_effect=score), \
             mock.patch.object(pick_topic, "_is_competitor_duplicate", return_value=False), \
             mock.patch.object(pick_topic, "is_placeholder_topic", return_value=False):
            return pick_topic._pick_fresh(bank, "used_short", [], research, "short")

    def test_a_topic_without_a_scene_is_never_picked_even_when_trending(self):
        pick = self._pick(_bank(self.bad, self.good), trends_favour=self.bad)
        self.assertEqual(pick["key"], self.good)

    def test_with_no_renderable_topic_the_old_pick_is_kept(self):
        self.assertEqual(self._pick(_bank(self.bad))["key"], self.bad)

    def test_the_buffer_prefers_a_topic_that_can_render(self):
        def topic(key):
            return {"key": key, "cat": "RISK", "beats": {}, "thumb": [], "facts": []}
        bank = [topic(self.bad), topic(self.good)]
        with mock.patch.object(buffer_shorts, "is_evergreen_topic", return_value=(True, "ok")):
            picked = buffer_shorts.next_evergreen(bank, [], {"items": []})
            self.assertEqual(picked["key"], self.good)
            only_bad = buffer_shorts.next_evergreen([topic(self.bad)], [], {"items": []})
            self.assertIsNone(only_bad)

    def test_option_topics_without_scenes_hold_before_tts(self):
        for key in ("option_vega", "opt_expiry_elm"):
            self.assertNotIn(key, board_scenes.SCENES)
            reason = board_scenes.topic_chart_block({"key": key})
            self.assertIn("no SCENES", reason)
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(board_short, "RUN", Path(tmp)):
                with self.assertRaises(SystemExit) as caught:
                    board_short.require_scene_frames({"key": key, "_live_short": True}, {})
            self.assertIn("HOLD", caught.exception.reason)
            self.assertIn(key, caught.exception.reason)


if __name__ == "__main__":
    unittest.main()
