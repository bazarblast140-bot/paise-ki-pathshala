import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import publish_facebook as fb


class FacebookReelCoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        (self.run / "final_ig.mp4").write_bytes(b"video")
        (self.run / "publish_meta.json").write_text('{"title": "T", "description": "d"}')
        self.patches = [mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "t", "FB_DRY": ""}),
                        mock.patch.object(fb, "RUN", self.run), mock.patch.object(fb.time, "sleep")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def fake_call(self, fail_thumb=False):
        def call(method, url, params=None, data=None, headers=None, timeout=120):
            if url.endswith("/me"):
                return {"id": "1", "name": "Rajesh Technical Trader"}
            if url.endswith("/thumbnails"):
                if fail_thumb:
                    raise RuntimeError("HTTP 400 /thumbnails: bad image")
                return {"success": True}
            if url.endswith("/video_reels") and method == "GET":
                return {"data": []}
            if url.endswith("/video_reels"):
                return {"video_id": "55"} if params.get("upload_phase") == "start" else {"success": True}
            if url.startswith("https://rupload"):
                return {"success": True}
            return {"status": {"video_status": "ready", "publishing_phase": {"status": "complete"}},
                    "permalink_url": "/reel/55"}
        return mock.Mock(side_effect=call)

    def thumb_calls(self, m):
        return [c for c in m.call_args_list if c.args[1].endswith("/55/thumbnails")]

    def test_topic_thumbnail_is_sent_as_cover(self):
        (self.run / "thumbnail.jpg").write_bytes(b"\xff\xd8JPEGDATA")
        m = self.fake_call()
        with mock.patch.object(fb, "call", m):
            self.assertEqual(fb.main(), 0)
        calls = self.thumb_calls(m)
        self.assertEqual(len(calls), 1)
        body = calls[0].kwargs["data"]
        self.assertIn(b"\xff\xd8JPEGDATA", body)
        self.assertIn(b'name="source"; filename="thumbnail.jpg"', body)
        self.assertIn(b'name="is_preferred"', body)
        self.assertIn("multipart/form-data", calls[0].kwargs["headers"]["Content-Type"])

    def test_cover_failure_warns_and_still_posts(self):
        (self.run / "thumbnail.jpg").write_bytes(b"\xff\xd8x")
        m = self.fake_call(fail_thumb=True)
        with mock.patch.object(fb, "call", m), mock.patch("builtins.print") as out:
            self.assertEqual(fb.main(), 0)
        self.assertTrue((self.run / "fb_reel.json").is_file())
        self.assertTrue(any("warning: custom cover upload failed" in str(c) for c in out.call_args_list))

    def test_missing_thumbnail_warns_and_posts(self):
        m = self.fake_call()
        with mock.patch.object(fb, "call", m):
            self.assertEqual(fb.main(), 0)
        self.assertEqual(self.thumb_calls(m), [])


if __name__ == "__main__":
    unittest.main()
