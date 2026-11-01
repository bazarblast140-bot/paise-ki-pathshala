import os
import unittest
from unittest import mock

import publish_instagram as ig


class IgReelCoverTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"IG_USER_ID": "u1", "IG_ACCESS_TOKEN": "t"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _ig_call(self, posts):
        def fake(path, method="GET", params=None):
            if method == "POST" and path.endswith("/media"):
                posts.append(dict(params))
                return {"id": "c1"}
            if method == "POST" and path.endswith("/media_publish"):
                return {"id": "m1"}
            return {"status_code": "FINISHED"}
        return fake

    def test_cover_url_is_sent_with_reels_container(self):
        posts = []
        with mock.patch.object(ig, "ig_call", side_effect=self._ig_call(posts)), \
             mock.patch.object(ig, "persist_ig_media"), mock.patch.object(ig, "confirm_media", return_value=True):
            self.assertEqual(ig.publish_reel("https://x/v.mp4", "cap", "https://x/cover.jpg"), "m1")
        self.assertEqual(posts[0]["media_type"], "REELS")
        self.assertEqual(posts[0]["cover_url"], "https://x/cover.jpg")


if __name__ == "__main__":
    unittest.main()
