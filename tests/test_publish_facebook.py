import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import publish_facebook as fb


class FacebookReelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        run = Path(self.tmp.name)
        (run / "final_ig.mp4").write_bytes(b"x")
        (run / "publish_meta.json").write_text('{"title": "Nifty Close Report #1 16 Oct 2026", "description": "d"}')
        self.env = mock.patch.dict(os.environ, {"IG_ACCESS_TOKEN": "t", "FB_DRY": "1"})
        self.env.start()
        self.run_dir = mock.patch.object(fb, "RUN", run)
        self.run_dir.start()

    def tearDown(self):
        self.env.stop()
        self.run_dir.stop()
        self.tmp.cleanup()

    def test_cover_uses_thumbnail_when_the_file_exists(self):
        run = Path(self.tmp.name)
        (run / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff" + b"y" * 200)
        with mock.patch.object(fb, "call", return_value={"success": True}) as call:
            self.assertTrue(fb.set_cover("vid1"))
        self.assertIn("/vid1/thumbnails", call.call_args.args[1])
        self.assertIn(b"thumbnail.jpg", call.call_args.kwargs["data"])

    def test_cover_is_skipped_when_the_thumbnail_is_missing(self):
        with mock.patch.object(fb, "call") as call:
            self.assertFalse(fb.set_cover("vid1"))
        call.assert_not_called()

    def test_wrong_page_is_refused(self):
        with mock.patch.object(fb, "call", return_value={"id": "1", "name": "Some Other Page"}) as call:
            self.assertEqual(fb.main(), 2)
        self.assertEqual(call.call_count, 1)

    def test_existing_reel_is_not_posted_again(self):
        replies = [{"id": "1", "name": "Rajesh Technical Trader"},
                   {"data": [{"id": "9", "description": "Nifty Close Report #1 16 Oct 2026\n\nd"}]}]
        with mock.patch.object(fb, "call", side_effect=replies) as call:
            self.assertEqual(fb.main(), 0)
        self.assertEqual(call.call_count, 2)


    def test_lowercase_page_name_and_id(self):
        replies = [{"id": "1362385786947916", "name": "Rajesh technical trader"}, {"data": []}]
        with mock.patch.dict(os.environ, {"FB_PAGE_ID": "1362385786947916"}), \
             mock.patch.object(fb, "video_path", return_value=Path(__file__)), \
             mock.patch.object(fb, "caption", return_value=("T", "T")), \
             mock.patch.object(fb, "call", side_effect=replies) as call:
            self.assertEqual(fb.main(), 0)
        self.assertIn("/1362385786947916/video_reels", call.call_args_list[1].args[1])

    def test_wrong_page_id_refused(self):
        with mock.patch.dict(os.environ, {"FB_PAGE_ID": "999"}), \
             mock.patch.object(fb, "call", return_value={"id": "1", "name": "Rajesh technical trader"}):
            self.assertEqual(fb.main(), 2)


if __name__ == "__main__":
    unittest.main()
