"""16-Oct review: cut-off key sentence, static silent outro, Edge last fallback."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from realchart import ends, news, video_qa, voice


class SentenceTest(unittest.TestCase):
    def test_cut_number_goes_back_to_clause(self):
        self.assertEqual(news.complete_clause("Nifty was up nearly 118 points or 0"), "Nifty was up nearly 118 points")
        self.assertEqual(news.complete_clause("Markets ended higher, led by"), "Markets ended higher")
        self.assertEqual(news.complete_clause("Nifty rose 1.2% to 22,997"), "Nifty rose 1.2% to 22,997")

    def test_decimal_is_not_a_sentence_end(self):
        self.assertEqual(news.first_sentence("Up 118 points or 0.51 per cent. Next."), "Up 118 points or 0.51 per cent")

    def test_qa_flags_cut_sentence(self):
        self.assertTrue(video_qa.sentence_faults({"key_sentence": "up nearly 118 points or 0"}))
        self.assertEqual(video_qa.sentence_faults({"key_sentence": "up nearly 118 points"}), [])


class OutroTest(unittest.TestCase):
    def test_outro_short_and_moving(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = ends.render_end_card(Path(tmp) / "o.mp4", None, extra=["Aaj ka key level: 22,897"])
            from realchart.qa import probe
            self.assertLess(probe(clip)["duration"], 2.0)
            self.assertLess(video_qa.static_seconds(clip), 0.5)


class EdgeFallbackTest(unittest.TestCase):
    def test_edge_after_grok(self):
        import gemini_voice
        calls = []

        def fake(texts, dest, kind, dry_run, key_prefix, parts_dir, engine):
            calls.append(engine)
            if engine == "gemini":
                raise gemini_voice.GeminiFallback("quota")
            if engine == "grok":
                raise voice.GrokUnavailable("403")
            return ({"engine": engine}, [1.0])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(voice, "_speak_all", fake):
            info, _ = voice.speak_segments(["a"], Path(tmp) / "v.wav", "news")
        self.assertEqual(calls, ["gemini", "grok", "edge"])
        self.assertEqual(info["engine"], "edge")


if __name__ == "__main__":
    unittest.main()


class QuotaKindTest(unittest.TestCase):
    def test_minute_vs_day(self):
        import gemini_voice

        class R:
            def __init__(self, text):
                self.text = text
        self.assertEqual(gemini_voice.quota_kind(R('"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"')), "minute")
        self.assertEqual(gemini_voice.quota_kind(R('"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"')), "day")


class HideDrawingsTest(unittest.TestCase):
    def test_clicks_toolbar_toggle_until_hidden(self):
        from realchart import capture
        states = iter(["shown", "hidden"])

        class Page:
            def evaluate(self, js, click):
                assert "removeAllDrawingTools" not in js
                return "shown" if click else next(states)

            def wait_for_timeout(self, ms):
                pass
        self.assertTrue(capture.hide_drawings(Page()))


class CloseNewsTopicTest(unittest.TestCase):
    def test_metal_story_is_not_the_nifty_close(self):
        from realchart.build import about_indian_market
        self.assertFalse(about_indian_market({"title": "Gold vs silver vs platinum: Which metal saw biggest rally since last Dussehra?",
                                              "summary": "gaining about 47% in rupee terms"}))
        self.assertTrue(about_indian_market({"title": "Sensex, Nifty end higher as IT stocks gain"}))
