import unittest
from unittest import mock

import buffer_shorts


def script(key, owner=True):
    return {"key": key, "topic": f"{key} topic", "owner_approved": owner}


class RefillApprovedTopics(unittest.TestCase):
    def setUp(self):
        import approved_bank, approved_buffer, trade_plan
        self.patches = [
            mock.patch.object(approved_bank, "owner_ok", lambda s: bool(s and s.get("owner_approved"))),
            mock.patch.object(approved_bank, "posted", lambda s, e: any(x.get("topic_key") == s["key"] and x.get("video_id") for x in e)),
            mock.patch.object(approved_bank, "topic_from", lambda s: {"key": s["key"], "topic": s["topic"], "_approved_bank": True}),
            mock.patch.object(trade_plan, "has_plan", lambda k: True),
            mock.patch.object(approved_buffer, "plan_gate_reasons", lambda k: ["rr below 1.5"] if k == "bad_plan" else []),
            mock.patch.object(buffer_shorts, "is_evergreen_topic", lambda t: (True, "")),
            mock.patch("board_scenes.topic_chart_block", lambda t: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_bank_empty_takes_first_unused_approved(self):
        scripts = [script("not_owner", owner=False), script("posted_one"), script("skipped_one"),
                   script("bad_plan"), script("in_buffer"), script("fresh_one"), script("later")]
        entries = [{"topic_key": "posted_one", "video_id": "abc"}]
        manifest = {"skipped_topics": ["skipped_one"], "items": [{"topic_key": "in_buffer"}]}
        with mock.patch.object(buffer_shorts, "topic_blocked",
                               lambda k, e, b, m: k == "in_buffer"):
            picked = buffer_shorts.next_evergreen([], entries, manifest, approved=scripts)
        self.assertEqual(picked["key"], "fresh_one")
        self.assertTrue(picked["_approved_bank"])

    def test_none_when_all_used(self):
        entries = [{"topic_key": "a", "video_id": "x"}]
        self.assertIsNone(buffer_shorts.next_evergreen([], entries, {}, approved=[script("a"), script("bad_plan")]))

    def test_without_approved_list_behaviour_is_unchanged(self):
        self.assertIsNone(buffer_shorts.next_evergreen([], [], {}))

    def test_runner_switches_on_approved_bank(self):
        import approved_bank
        seen = {}
        def fake_run(cmd, cwd, env):
            seen.update(env)
            return mock.Mock(returncode=0)
        with mock.patch.object(buffer_shorts, "_load_bank", lambda: []), \
             mock.patch.object(approved_bank, "by_key", lambda k: script(k)), \
             mock.patch("subprocess.run", fake_run):
            buffer_shorts.default_preview_runner("pat_x")
        self.assertEqual(seen.get("APPROVED_BANK"), "1")


if __name__ == "__main__":
    unittest.main()
