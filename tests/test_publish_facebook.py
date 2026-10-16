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


if __name__ == "__main__":
    unittest.main()
