import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import competitor_research as CR
from competitor_patterns import pattern_weights


def vid(i, ch, title, vpd, dur=45, hour=9):
    return {"id": f"v{i}", "channel_id": ch, "channel": ch.upper(), "title": title, "tags": ["nifty", "trading"],
            "published_ist": "2026-09-20 09:00", "hour_ist": hour, "weekday": "Mon", "duration_s": dur,
            "views": int(vpd * 5), "likes": 1, "comments": 1, "views_per_day": vpd}


class CompetitorResearchTests(unittest.TestCase):
    def sample(self):
        vs = [vid(1, "a", "RSI divergence क्यों fail होता है?", 900),
              vid(2, "a", "Stop loss की 3 गलती", 800),
              vid(3, "a", "market update", 100),
              vid(4, "a", "daily news", 120),
              vid(5, "b", "Candle pattern क्या बताता है?", 5000, hour=20),
              vid(6, "b", "live", 500),
              vid(7, "b", "live 2", 450),
              vid(8, "b", "₹1 लाख Nifty में 10 साल", 3000, dur=58, hour=20)]
        return CR.add_performance_index(vs)

    def test_iso_duration(self):
        self.assertEqual(CR.iso_duration_seconds("PT1M5S"), 65)
        self.assertEqual(CR.iso_duration_seconds("PT45S"), 45)
        self.assertEqual(CR.iso_duration_seconds("garbage"), 0)

    def test_analysis_finds_question_and_number_lift(self):
        a = CR.analyse(self.sample())
        self.assertGreater(a["title_patterns"]["curiosity_question"]["lift"], 1.0)
        self.assertTrue(a["top_videos"])
        self.assertIn(20, a["best_upload_hours_ist"])

    def test_auto_rows_feed_existing_topic_scorer_without_titles(self):
        rows = CR.auto_observations(self.sample())
        self.assertTrue(rows)
        self.assertTrue(all("title" not in r for r in rows))
        weights = pattern_weights({"observations": rows})
        self.assertIn("hook_style", weights)

    def test_prompt_hint_fresh_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ins.json"
            a = CR.analyse(self.sample())
            p.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "analysis": a}),
                         encoding="utf-8")
            with mock.patch.object(CR, "INSIGHTS", p):
                hint = CR.prompt_hint()
                self.assertIn("NEVER copy", hint)
            p.write_text(json.dumps({"generated_at": "2020-01-01T00:00:00+00:00", "analysis": a}), encoding="utf-8")
            with mock.patch.object(CR, "INSIGHTS", p):
                self.assertEqual(CR.prompt_hint(), "")

    def test_missing_credentials_skip_cleanly(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            yt, how = CR.get_client()
            self.assertIsNone(yt)


if __name__ == "__main__":
    unittest.main()


class _Req:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self.data


class FakeYT:
    """Minimal stand-in for the googleapiclient resource (no network)."""
    def __init__(self):
        self.titles = {f"v{i}": t for i, t in enumerate([
            "RSI क्यों fail होता है?", "Stop loss की 3 गलती", "market update", "live", "Candle pattern क्या है?",
            "news", "₹1 लाख Nifty में", "daily"])}

    def channels(self):
        outer = self
        class C:
            def list(self, **kw):
                return _Req({"items": [{"id": kw.get("id"), "snippet": {"title": "Chan"},
                                        "statistics": {"subscriberCount": "1000"},
                                        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        return C()

    def playlistItems(self):
        class P:
            def list(self, **kw):
                return _Req({"items": [{"contentDetails": {"videoId": f"v{i}", "videoPublishedAt": "2099-01-01T00:00:00Z"}}
                                       for i in range(4)]})
        return P()

    def search(self):
        class S:
            def list(self, **kw):
                return _Req({"items": [{"id": {"videoId": f"v{i}"}} for i in range(4, 8)]})
        return S()

    def videos(self):
        outer = self
        class V:
            def list(self, **kw):
                items = []
                for n, vid_id in enumerate(kw["id"].split(",")):
                    items.append({"id": vid_id, "snippet": {
                        "channelId": "c1" if vid_id in ("v0", "v1", "v2", "v3") else "c2",
                        "channelTitle": "X", "title": outer.titles.get(vid_id, "t"), "tags": ["a"],
                        "publishedAt": "2026-09-20T03:30:00Z"},
                        "statistics": {"viewCount": str(1000 * (n + 1))},
                        "contentDetails": {"duration": "PT45S"}})
                return _Req({"items": items})
        return V()


class ResearchMainTests(unittest.TestCase):
    def test_main_writes_files_with_fake_client(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "research_sources.json").write_text(json.dumps({"competitors": [{"channel_id": "c1"}]}), encoding="utf-8")
            (d / "competitor_performance.json").write_text(json.dumps({"observations": [{"channel": "m", "topic_type": "x", "performance_index": 1}]}), encoding="utf-8")
            with mock.patch.object(CR, "DATA", d), \
                 mock.patch.object(CR, "INSIGHTS", d / "ins.json"), \
                 mock.patch.object(CR, "PERF", d / "competitor_performance.json"), \
                 mock.patch.object(CR, "OWN", d / "own.json"), \
                 mock.patch.object(CR, "LEDGER", d / "published.json"), \
                 mock.patch.object(CR, "get_client", return_value=(FakeYT(), "fake")), \
                 mock.patch("sys.argv", ["competitor_research.py"]):
                self.assertEqual(CR.main(), 0)
            ins = json.loads((d / "ins.json").read_text(encoding="utf-8"))
            self.assertEqual(ins["analysis"]["videos_analysed"], 8)
            perf = json.loads((d / "competitor_performance.json").read_text(encoding="utf-8"))
            self.assertEqual(perf["observations"][0]["channel"], "m")          # manual rows kept
            self.assertTrue(any(o.get("source") == CR.AUTO_SOURCE for o in perf["observations"]))
