import os
import tempfile
import unittest
from unittest import mock

import approved_buffer


class TopicBufHandledTest(unittest.TestCase):
    def test_empty_or_repeat_buffer_does_not_mark_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            open(out, "w").close()
            env = {"GITHUB_OUTPUT": out, "PUBLISH_SLOT_IST": "21:00",
                   "PUBLISH_DATE_IST": "2026-10-23", "AUTOMATIC_UPLOAD": "1"}
            with mock.patch.dict(os.environ, env), \
                 mock.patch("slot_guard._read", return_value=[]), \
                 mock.patch.object(approved_buffer, "load_manifest", return_value={"items": []}):
                self.assertEqual(approved_buffer.publish_slot(manifest_path=os.path.join(tmp, "m.json")), 0)
            with open(out) as fh:
                self.assertNotIn("handled=true", fh.read())


if __name__ == "__main__":
    unittest.main()
