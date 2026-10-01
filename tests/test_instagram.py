import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import publish_instagram as IG

SCRIPT = """TITLE:
Bull Call Spread: bullish view में भी risk तय — आसान भाषा में

HOOK (0-3 sec):
Bull Call Spread वह strategy है जहाँ bullish होकर भी आपका सबसे बड़ा loss पहले से तय रहता है।

BODY:
[SEGMENT 1: PEHCHAN]
कुछ भी।

CTA:
अगला कौन सा market topic समझना है? Comment में लिखो—ऐसी आसान सीख के लिए जुड़े रहो।

DISCLAIMER LINE:
सिर्फ़ जानकारी, निवेश सलाह नहीं।

FACT LEDGER:
- buy call और higher-strike sell call का structure
- max loss और max profit पहले calculate करें
- यह educational framework है, किसी शेयर/ट्रेड की खरीद-बिक्री सलाह नहीं
"""
TOPIC = {"key": "option_bull_call_spread", "cat": "OPTIONS",
         "seo": "Bull Call Spread — bullish view में defined-risk strategy",
         "thumb": ["Bull Call Spread", "आसान भाषा में समझो"]}


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        (self.run / "script.txt").write_text(SCRIPT, encoding="utf-8")
        (self.run / "topic.json").write_text(json.dumps(TOPIC, ensure_ascii=False), encoding="utf-8")
        self.p = mock.patch.object(IG, "RUN", self.run)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_structure(self):
        c = IG.build_caption()
        first = c.split("\n")[0]
        self.assertTrue(first.startswith("Bull Call Spread"))          # keyword first
        self.assertIn("📌 याद रखो", c)
        self.assertIn("Save", c)
        self.assertIn("निवेश सलाह नहीं", c)
        self.assertNotRegex(c, r"https?://")                            # IG caption links not tappable
        self.assertLessEqual(len(c), 2200)

    def test_hashtags_keep_main_selection(self):
        tags = re.findall(r"#\w+", IG.build_caption())
        self.assertIn("#PaiseKiPathshala", tags)
        self.assertIn("#OptionsTrading", tags)
        self.assertEqual(len(tags), len(set(tags)))

    def test_disclaimer_fact_not_repeated_as_bullet(self):
        self.assertNotIn("• यह educational framework", IG.build_caption())


class VerdictGateTests(unittest.TestCase):
    """A failed-review video must never become a Reel, even with IG_CROSSPOST_FORCE."""

    def _run(self, report_text):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "final.mp4").write_bytes(b"x" * 2000)
            if report_text is not None:
                (run / "report.md").write_text(report_text, encoding="utf-8")
            env = {"IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1", "VIDEO_FORMAT": "short"}
            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", env), \
                 mock.patch.object(IG, "video_format", return_value="short"), \
                 mock.patch.object(IG, "host_mp4", side_effect=AssertionError("must not upload")) as host, \
                 mock.patch.object(IG, "publish_reel", side_effect=AssertionError("must not publish")):
                with self.assertRaises(SystemExit) as cm:
                    IG.main()
                return cm.exception.code, host.called

    def test_needs_fix_verdict_is_skipped(self):
        code, uploaded = self._run("VERDICT: [NEEDS FIX — 2 items]")
        self.assertEqual(code, 0)
        self.assertFalse(uploaded)

    def test_missing_report_is_skipped(self):
        code, uploaded = self._run(None)
        self.assertEqual(code, 0)
        self.assertFalse(uploaded)


class CoverTests(unittest.TestCase):
    def test_grid_safe_cover_keeps_design_inside_3x4(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "t.jpg", Path(d) / "c.jpg"
            im = Image.new("RGB", (1080, 1920), (0, 0, 0))
            im.paste((255, 255, 255), (0, 100, 1080, 200))            # title band near the top
            im.save(src)
            IG.grid_safe_cover(src, out)
            c = Image.open(out)
            self.assertEqual(c.size, (1080, 1920))
            # the title band must now sit inside the grid crop (y 240..1680)
            band = [y for y in range(1920) if c.getpixel((540, y))[0] > 200]
            self.assertTrue(band and min(band) >= 240 and max(band) <= 1680, (band[:1], band[-1:]))


if __name__ == "__main__":
    unittest.main()
