"""Parsing, ranking, quota, picker weighting, and the no-publish guarantee."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import competitor_research as CR
import trend_research as TR
from competitor_patterns import TREND_WEIGHT_CAP, load_trends, score_topic, trend_weight
from pick_topic import _pick_fresh, is_placeholder_topic

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "daily-trend-research.yml"
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def on_block(text):
    match = re.search(r"^on:\n(.*?)(?=^\S)", text, re.M | re.S)
    return match.group(1) if match else ""


class _Req:
    def __init__(self, data=None, exc=None):
        self.data = data
        self.exc = exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.data


class _QuotaError(Exception):
    def __init__(self):
        self.resp = type("R", (), {"status": 403})()
        super().__init__("The request cannot be completed because you have exceeded your quota.")


def video_item(video_id, title, views=None, likes=None, comments=None, duration="PT48S",
               description="", tags=None, published="2026-09-28T04:00:00Z", thumbs=True):
    stats = {}
    if views is not None:
        stats["viewCount"] = str(views)
    if likes is not None:
        stats["likeCount"] = str(likes)
    if comments is not None:
        stats["commentCount"] = str(comments)
    snippet = {
        "title": title,
        "description": description,
        "channelId": "c1",
        "channelTitle": "Chan",
        "publishedAt": published,
        "tags": tags or [],
    }
    if thumbs:
        snippet["thumbnails"] = {"high": {"url": "https://i.ytimg.com/x.jpg", "width": 480, "height": 360}}
    return {
        "id": video_id,
        "snippet": snippet,
        "statistics": stats,
        "contentDetails": {"duration": duration} if duration is not None else {},
    }


def fresh_trends(clusters):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"topic_clusters": clusters},
    }


def placeholder(key="peg_ratio"):
    return {
        "id": 64, "used_short": False, "key": key, "cat": "FUNDAMENTAL",
        "topic": "PEG ratio — growth के मुकाबले valuation",
        "thumb": ["PEG ratio", "आसान भाषा में समझो"],
        "sticker": "सीखो",
        "beats": {
            "hook": ["PEG ratio", "इसे आसान तरीके से समझो"],
            "pehchan": ["पहचान", "same sentence"],
            "matlab": ["मतलब", "same sentence"],
        },
        "facts": ["same sentence"],
        "seo": "PEG ratio",
    }


def semi_placeholder():
    title = "Earnings quality — profit बढ़े लेकिन cash flow न बढ़े"
    return {
        "used_short": False, "key": "fund_eps_quality", "cat": "FUNDAMENTALS",
        "topic": title,
        "beats": {"hook": [title, title]},
        "facts": [title, "Operating cash flow को profit के साथ देखो"],
    }


def real_topic(key="hammer", topic="Hammer candle wick", cat="CANDLESTICK"):
    return {
        "used_short": False, "key": key, "cat": cat, "topic": topic,
        "seo": topic, "beats": {"hook": ["नीचे लंबी wick", "खरीदार लौटे?"]},
        "facts": ["निचली wick बॉडी से 2 गुना"],
    }


class ParsingTests(unittest.TestCase):
    def test_duration_distinguishes_zero_from_missing(self):
        self.assertEqual(TR.duration_seconds("PT1M5S"), 65)
        self.assertEqual(TR.duration_seconds("PT45S"), 45)
        self.assertEqual(TR.duration_seconds("PT3M"), 180)
        self.assertEqual(TR.duration_seconds("PT0S"), 0)
        self.assertIsNone(TR.duration_seconds("garbage"))
        self.assertIsNone(TR.duration_seconds(""))
        self.assertIsNone(TR.duration_seconds(None))

    def test_missing_statistics_stay_null(self):
        row = TR.parse_video(video_item("abc", "RSI क्यों?", views=1000), "q")
        self.assertEqual(row["views"], 1000)
        self.assertIsNone(row["likes"])
        self.assertIsNone(row["comments"])
        zero = TR.parse_video(video_item("abc", "RSI", views=0, likes=0, comments=0), "q")
        self.assertEqual(zero["views"], 0)
        self.assertEqual(zero["likes"], 0)
        self.assertEqual(zero["comments"], 0)

    def test_hook_comes_from_text_or_stays_null(self):
        described = TR.parse_video(video_item(
            "a", "plain title", description="3 गलतियाँ जो account खाली करती हैं"), "q")
        self.assertEqual(described["hook_pattern"], "list")
        self.assertEqual(described["hook_source"], "description")
        self.assertEqual(described["opening_line"], "3 गलतियाँ जो account खाली करती हैं")
        numbered = TR.parse_video(video_item("a2", "₹1 लाख Nifty में 10 साल", description=""), "q")
        self.assertEqual(numbered["hook_pattern"], "number_led")
        self.assertEqual(numbered["hook_source"], "title")
        from_title = TR.parse_video(video_item(
            "b", "RSI क्यों fail होता है?", description="पहली लाइन"), "q")
        self.assertEqual(from_title["hook_pattern"], "question")
        self.assertEqual(from_title["hook_source"], "title")
        self.assertEqual(from_title["opening_line"], "पहली लाइन")
        none = TR.parse_video(video_item("c", "daily news", description="market update"), "q")
        self.assertIsNone(none["hook_pattern"])
        self.assertIsNone(none["hook_source"])
        self.assertIsNone(TR.classify_opening("Breakout Trading Strategy"))
        self.assertEqual(TR.classify_opening("How To Find A Real Breakout"), "how_to")
        self.assertEqual(TR.classify_opening("5 candlestick patterns"), "list")
        self.assertEqual(TR.classify_opening("Stop loss की गलती"), "warning")
        self.assertEqual(TR.classify_opening("Support vs Resistance"), "versus")
        self.assertEqual(TR.classify_opening("Market Structure Explained"), "explainer")

    def test_visual_notes_only_from_metadata(self):
        styled = TR.parse_video(video_item("a", "t", tags=["candlestick pattern", "nifty"]), "q")
        self.assertIn("thumbnail 480x360", styled["visual_style_notes"])
        self.assertIn("candlestick", styled["visual_style_notes"])
        markers = styled["visual_style_notes"].split("tags mention: ", 1)[-1]
        self.assertNotRegex(markers, r"(^|, )candle(,|$)")
        self.assertNotIn("talking", styled["visual_style_notes"])
        bare = TR.parse_video(video_item("b", "t", tags=["nifty", "hindi"], thumbs=False), "q")
        self.assertEqual(bare["visual_style_notes"], "")
        self.assertIsNone(bare["thumbnail_width"])

    def test_short_filter_uses_parsed_duration_only(self):
        self.assertIsNotNone(TR.as_short(TR.parse_video(video_item("a", "t", duration="PT3M"), "q")))
        self.assertIsNone(TR.as_short(TR.parse_video(video_item("b", "t", duration="PT3M1S"), "q")))
        self.assertIsNone(TR.as_short(TR.parse_video(video_item("c", "t", duration=None), "q")))

    def test_rank_puts_missing_views_last(self):
        rows = [
            {"video_id": "b", "views": 10},
            {"video_id": "c", "views": None},
            {"video_id": "a", "views": 50},
        ]
        self.assertEqual([r["video_id"] for r in TR.rank_shorts(rows)], ["a", "b", "c"])

    def test_rss_keeps_source_and_date_and_drops_stale(self):
        fresh = NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")
        xml = f"""<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>RBI holds repo rate</title>
            <link>https://example.com/rbi</link>
            <pubDate>{fresh}</pubDate>
          </item>
          <item>
            <title>Old budget story</title>
            <link>https://example.com/old</link>
            <pubDate>Wed, 05 Oct 2016 14:00:44 +0530</pubDate>
          </item>
          <item><title>   </title><link>https://example.com/blank</link></item>
          <item>
            <title>Undated headline</title>
            <link>https://example.com/nodate</link>
          </item>
        </channel></rss>"""
        items = TR.parse_feed("Economic Times Markets", xml, "2026-10-01T00:00:00+00:00", now=NOW)
        titles = [item["title"] for item in items]
        self.assertEqual(titles, ["RBI holds repo rate", "Undated headline"])
        self.assertEqual(items[0]["source"], "Economic Times Markets")
        self.assertEqual(items[0]["source_url"], "https://example.com/rbi")
        self.assertTrue(items[0]["published_at"])
        self.assertIsNone(items[1]["published_at"])
        self.assertIsNone(items[0]["views"])
        self.assertEqual(TR.parse_feed("x", "<not xml", "t", now=NOW), [])

    def test_finance_chart_filter_does_not_keep_unrelated_videos(self):
        self.assertFalse(TR.is_finance_text("Bollywood song", "dance"))
        self.assertTrue(TR.is_finance_text("Nifty aaj", ""))


class SummaryAndReportTests(unittest.TestCase):
    def test_summary_counts_only_real_rows_and_report_quotes_them(self):
        shorts = [
            TR.as_short(TR.parse_video(video_item(
                "a", "Nifty option kyun?", views=100, duration="PT40S", tags=["chart"]), "q")),
            TR.as_short(TR.parse_video(video_item(
                "b", "Nifty breakout kaise?", views=50, duration="PT50S"), "q")),
            TR.as_short(TR.parse_video(video_item(
                "c", "daily news", views=None, duration="PT30S"), "q")),
        ]
        trending = [
            {"title": "RBI holds repo rate", "source": "Mint Markets",
             "source_url": "https://example.com/r", "published_at": "2026-10-01T00:00:00+00:00",
             "fetched_at": "2026-10-01T00:00:00+00:00", "views": None},
            {"title": "Repo rate and the rupee", "source": "Mint Markets",
             "source_url": "https://example.com/r2", "published_at": None,
             "fetched_at": "2026-10-01T00:00:00+00:00", "views": None},
        ]
        summary = TR.build_summary(shorts, trending)
        self.assertGreaterEqual(summary["hook_patterns"]["question"]["count"], 2)
        self.assertEqual(summary["duration_s"]["n"], 3)
        self.assertIn(summary["duration_s"]["median"], (30, 40, 50))
        names = [c["name"] for c in summary["topic_clusters"]]
        self.assertIn("index", names)
        index = next(c for c in summary["topic_clusters"] if c["name"] == "index")
        self.assertGreaterEqual(index["count"], 2)
        self.assertIn("nifty", index["keywords"])
        payload = {
            "generated_at": "2026-10-01T00:00:00+00:00",
            "auth": "oauth",
            "window": {"published_after": "2026-09-24T00:00:00Z", "lookback_days": 7,
                       "region_code": "IN", "relevance_language": "hi"},
            "quota": {"used_units": 101, "budget_units": 1200, "stopped": False,
                      "stop_reason": None, "skipped": []},
            "summary": summary,
            "trending_topics": trending,
            "shorts": shorts,
            "errors": [],
        }
        report = TR.render_report(payload)
        self.assertIn("# Trends 2026-W40", report)
        self.assertIn("Mint Markets", report)
        self.assertIn("RBI holds repo rate", report)
        self.assertIn("40s", report)
        self.assertNotIn("99999", report)
        self.assertIn("does not publish", report)
        self.assertEqual(TR.report_filename(datetime(2026, 10, 1, 5, 30, tzinfo=IST)), "trends-2026-40.md")


class QuotaTests(unittest.TestCase):
    def test_budget_refuses_before_http(self):
        class Boom:
            def execute(self):
                raise AssertionError("HTTP must not run")

        budget = CR.QuotaBudget(50)
        self.assertIsNone(CR.yt_execute(Boom(), "search.list", budget))
        self.assertEqual(budget.used, 0)
        self.assertEqual(budget.skipped[0]["method"], "search.list")
        self.assertFalse(budget.stopped)

    def test_cheaper_call_still_allowed_after_an_expensive_skip(self):
        budget = CR.QuotaBudget(101)
        self.assertEqual(CR.yt_execute(_Req({"items": []}), "search.list", budget), {"items": []})
        self.assertIsNone(CR.yt_execute(_Req({"items": []}), "search.list", budget))
        self.assertEqual(CR.yt_execute(_Req({"items": [1]}), "videos.list", budget), {"items": [1]})
        self.assertEqual(budget.used, 101)
        self.assertFalse(budget.stopped)

    def test_quota_exceeded_stops_later_calls(self):
        budget = CR.QuotaBudget(500)
        self.assertIsNone(CR.yt_execute(_Req(exc=_QuotaError()), "videos.list", budget))
        self.assertTrue(budget.stopped)
        self.assertEqual(budget.used, 1)
        self.assertIsNone(CR.yt_execute(_Req({"ok": 1}), "videos.list", budget))
        self.assertEqual(budget.used, 1)

    def test_uncosted_method_does_not_run(self):
        class Boom:
            def execute(self):
                raise AssertionError("uncosted method must not run")

        budget = CR.QuotaBudget(500)
        self.assertIsNone(CR.yt_execute(Boom(), "videos.insert", budget))
        self.assertTrue(budget.stopped)

    def test_config_cannot_raise_the_hard_cap(self):
        settings = TR.resolve_settings({
            "quota_budget_units": 9000,
            "search_queries": [f"q{i}" for i in range(20)],
            "lookback_days": 30,
            "max_shorts": 500,
        })
        self.assertEqual(settings["quota_budget_units"], TR.HARD_QUOTA_BUDGET)
        self.assertEqual(len(settings["search_queries"]), TR.MAX_SEARCH_QUERIES)
        self.assertEqual(settings["lookback_days"], 7)
        self.assertEqual(settings["max_shorts"], 40)
        planned = TR.MAX_SEARCH_QUERIES * CR.QUOTA_UNITS["search.list"] + 6 * 2 + 4
        self.assertLessEqual(planned, TR.HARD_QUOTA_BUDGET)
        self.assertLessEqual(TR.HARD_QUOTA_BUDGET + CR.COMPETITOR_QUOTA_BUDGET, 1800)

    def test_collect_skips_further_searches_when_budget_is_low(self):
        api = _CountingAPI()
        settings = TR.resolve_settings({})
        settings["search_queries"] = ["q1", "q2"]
        settings["max_shorts"] = 10
        budget = CR.QuotaBudget(100)
        payload = TR.collect(api, settings, budget, lambda url: "", now=NOW, channel_ids=[], feeds=[])
        self.assertEqual(api.searches, 1)
        self.assertEqual(api.video_calls, [])
        self.assertEqual(payload["quota"]["used_units"], 100)
        self.assertTrue(payload["quota"]["skipped"])
        self.assertEqual(payload["shorts"], [])


class _CountingAPI:
    def __init__(self):
        self.searches = 0
        self.video_calls = []

    def search(self):
        outer = self

        class S:
            def list(self, **kwargs):
                class R:
                    def execute(self_inner):
                        outer.searches += 1
                        return {"items": [{"id": {"videoId": f"v{outer.searches}"}}]}
                return R()
        return S()

    def videos(self):
        outer = self

        class V:
            def list(self, **kwargs):
                class R:
                    def execute(self_inner):
                        outer.video_calls.append(kwargs)
                        return {"items": []}
                return R()
        return V()


class CollectTests(unittest.TestCase):
    def test_collect_parses_api_rows_and_rss_without_inventing_likes(self):
        class API:
            def search(self):
                class S:
                    def list(self, **kwargs):
                        return _Req({"items": [{"id": {"videoId": "v1"}}]})
                return S()

            def videos(self):
                class V:
                    def list(self, **kwargs):
                        if kwargs.get("chart") == "mostPopular":
                            return _Req({"items": [
                                video_item("pop-music", "Bollywood song", views=9, duration="PT20S"),
                                video_item("pop-fin", "Nifty aaj", views=50, duration="PT40S",
                                           published="2026-09-30T00:00:00Z"),
                            ]})
                        return _Req({"items": [video_item(
                            "v1", "Nifty option kyun?", views=1000, duration="PT48S",
                            description="pehli line", tags=["candlestick pattern"])]})
                return V()

        settings = TR.resolve_settings({})
        settings["search_queries"] = ["option trading hindi shorts"]
        fresh = NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")
        xml = (f"<rss><channel><item><title>SEBI tightens F&amp;O rules</title>"
               f"<link>https://example.com/sebi</link><pubDate>{fresh}</pubDate></item></channel></rss>")
        payload = TR.collect(
            API(), settings, CR.QuotaBudget(1200), lambda url: xml, now=NOW,
            channel_ids=[], feeds=[{"name": "Mint Markets", "url": "https://example.com/rss"}])
        ids = [row["video_id"] for row in payload["shorts"]]
        self.assertIn("v1", ids)
        self.assertIn("pop-fin", ids)
        self.assertNotIn("pop-music", ids)
        v1 = next(row for row in payload["shorts"] if row["video_id"] == "v1")
        self.assertEqual(v1["views"], 1000)
        self.assertIsNone(v1["likes"])
        self.assertEqual(v1["hook_pattern"], "question")
        self.assertIn("480x360", v1["visual_style_notes"])
        titles = [row["title"] for row in payload["trending_topics"]]
        self.assertIn("SEBI tightens F&O rules", titles)
        self.assertIn("Nifty aaj", titles)
        self.assertTrue(any(row["source"] == "Mint Markets" for row in payload["trending_topics"]))


class PickerWeightTests(unittest.TestCase):
    def test_trend_boost_is_capped_and_is_not_an_override(self):
        trends = fresh_trends([
            {"name": "index", "count": 4, "keywords": ["nifty"]},
            {"name": "options", "count": 3, "keywords": ["option", "theta"]},
            {"name": "personal_finance", "count": 2, "keywords": ["sip"]},
            {"name": "tax_ipo", "count": 2, "keywords": ["ipo"]},
            {"name": "headline_terms", "count": 2, "keywords": ["rupee"]},
        ])
        strong = real_topic("rsi_levels", "RSI galati risk", "INDICATOR")
        trendy = real_topic("nifty_base", "Nifty option theta aur SIP IPO rupee", "MARKET FACT")
        plain = real_topic("journal", "trading journal habit", "RISK")
        weights = {"topic_type": {"mistake_prevention": 1.0}}
        strong_score = score_topic(strong, weights, trends=trends)
        trendy_score = score_topic(trendy, {}, trends=trends)
        self.assertGreater(strong_score, trendy_score)
        base = score_topic(trendy, {})
        self.assertGreater(trendy_score, base)
        self.assertLessEqual(trendy_score - base, TREND_WEIGHT_CAP)
        self.assertEqual(score_topic(plain, {}), score_topic(plain, {}, trends=trends))
        stale = dict(trends, generated_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(trend_weight(trendy, stale), 0.0)
        self.assertEqual(trend_weight(trendy, {"generated_at": None}), 0.0)
        self.assertEqual(trend_weight(trendy, {"summary": "nope", "generated_at": trends["generated_at"]}), 0.0)

    def test_single_observation_cluster_does_not_boost(self):
        trends = fresh_trends([{"name": "index", "count": 1, "keywords": ["nifty"]}])
        topic = real_topic("nifty_base", "Nifty ka base", "MARKET FACT")
        self.assertEqual(trend_weight(topic, trends), 0.0)

    def test_placeholder_loses_even_when_it_matches_the_trend(self):
        self.assertTrue(is_placeholder_topic(placeholder()))
        self.assertTrue(is_placeholder_topic(semi_placeholder()))
        self.assertFalse(is_placeholder_topic(real_topic()))
        trends = fresh_trends([{"name": "fundamentals", "count": 5, "keywords": ["peg"]}])
        # The placeholder text contains "peg"; the real topic does not.
        bank = {"topics": [placeholder(), real_topic()]}
        picked = _pick_fresh(bank, "used_short", [], ({}, {}, {}, trends), "short")
        self.assertEqual(picked["key"], "hammer")
        self.assertIsNone(_pick_fresh({"topics": [placeholder()]}, "used_short", [], ({}, {}, {}), "short"))

    def test_trend_does_not_bypass_cooldown_or_tie_break(self):
        first = real_topic("a", "alpha journal", "RISK")
        second = real_topic("b", "beta journal", "RISK")
        self.assertEqual(
            _pick_fresh({"topics": [first, second]}, "used_short", [], ({}, {}, {}), "short")["key"],
            "a")
        nifty = real_topic("nifty_base", "Nifty ka base", "OPTIONS")
        journal = real_topic("journal", "trading journal", "RISK")
        trends = fresh_trends([{"name": "index", "count": 4, "keywords": ["nifty"]}])
        cooled = _pick_fresh(
            {"topics": [nifty, journal]}, "used_short", ["OPTIONS"], ({}, {}, {}, trends), "short")
        self.assertEqual(cooled["key"], "journal")
        preferred = _pick_fresh(
            {"topics": [journal, nifty]}, "used_short", [], ({}, {}, {}, trends), "short")
        self.assertEqual(preferred["key"], "nifty_base")

    def test_missing_trends_file_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_trends(Path(directory)), {})


class NoPublishTests(unittest.TestCase):
    def test_workflow_and_module_never_publish(self):
        text = WF.read_text(encoding="utf-8")
        src = (ROOT / "trend_research.py").read_text(encoding="utf-8")
        on = on_block(text)
        self.assertIn("workflow_dispatch", on)
        self.assertIn('cron: "0 0 * * *"', on)
        self.assertNotRegex(on, r"(?m)^  push:")
        perms = re.search(r"^permissions:\n(.*?)(?=^\S)", text, re.M | re.S)
        self.assertIsNotNone(perms)
        self.assertIn("contents: write", perms.group(1))
        self.assertNotIn("actions:", perms.group(1))
        for banned in ("publish.py", "publish_instagram", "auto_agent", "make_public",
                       "run_pipeline", "videos().insert", "videos().update", "videos().delete",
                       "videos().rate"):
            self.assertNotIn(banned, text, banned)
            self.assertNotIn(banned, src, banned)
        self.assertIn("trend_research.py", text)
        self.assertNotRegex(text, r"python\s+(publish|auto_agent|run_pipeline)")
        self.assertIn("rebase", text)

    def test_dry_run_writes_nothing_and_live_run_writes_snapshot(self):
        fresh = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        xml = (f"<rss><channel><item><title>Rupee steady</title>"
               f"<link>https://example.com/r</link><pubDate>{fresh}</pubDate></item></channel></rss>")
        settings = TR.resolve_settings({"search_queries": []})
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            trends = directory / "trends.json"
            reports = directory / "reports"
            with mock.patch.object(TR, "trend_settings", return_value=settings), \
                 mock.patch.object(TR, "competitor_channel_ids", return_value=[]), \
                 mock.patch.object(TR, "news_feeds", return_value=[{"name": "Mint Markets", "url": "https://example.com"}]), \
                 mock.patch.object(TR, "fetch_text", return_value=xml), \
                 mock.patch.object(TR, "TRENDS_PATH", trends), \
                 mock.patch.object(TR, "REPORTS_DIR", reports), \
                 mock.patch.object(TR.CR, "get_client", return_value=(None, "no creds")):
                self.assertEqual(TR.main(["--dry-run"]), 0)
                self.assertFalse(trends.exists())
                self.assertEqual(TR.main([]), 0)
            payload = json.loads(trends.read_text(encoding="utf-8"))
            self.assertIsNone(payload["auth"])
            self.assertEqual(payload["shorts"], [])
            self.assertEqual(payload["trending_topics"][0]["title"], "Rupee steady")
            self.assertEqual(payload["trending_topics"][0]["source"], "Mint Markets")
            self.assertIsNone(payload["trending_topics"][0]["views"])
            self.assertEqual(payload["quota"]["used_units"], 0)
            written = list(reports.glob("trends-*.md"))
            self.assertEqual(len(written), 1)
            report = written[0].read_text(encoding="utf-8")
            self.assertIn("Rupee steady", report)
            self.assertNotIn("0 views", report)


if __name__ == "__main__":
    unittest.main()
