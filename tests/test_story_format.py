"""Story Reel: fetched figures only, educational wording, Imagine slot, sample render."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from PIL import Image

from realchart.script import estimate_seconds, lint, number_faults
from realchart.session import IST
from realchart.story_facts import (
    build_figures, collect, detect_reversal, fundamentals_from_payload, parse_breadth,
    parse_fii_dii, parse_sectors,
)
from realchart.story_script import EDU, compliance_faults, rows_for


def _bars(last):
    rows = []
    price = 120.0
    for i in range(30):
        drift = -1.0 if i > 18 else 0.4
        o, c = price, price + drift
        rows.append({"date": f"2026-09-{(i % 27) + 1:02d}", "o": o, "h": max(o, c) + 0.4, "l": min(o, c) - 0.4, "c": c})
        price = c
    rows[-1] = last
    return rows


def _hammer_bars():
    rows = _bars({"date": "2026-10-30", "o": 100, "h": 102, "l": 90, "c": 101.5})
    return rows


def _levels(bars):
    from realchart.levels import compute
    levels = compute(bars, "NSE:SAMPLE", "SAMPLE.NS")
    levels["bars"] = bars
    levels["format"] = "story"
    return levels


class FactTests(unittest.TestCase):
    def test_hammer_is_detected_from_ohlc_only(self):
        found = detect_reversal(_hammer_bars())
        self.assertEqual(found["name"], "hammer")
        self.assertEqual(found["low"], 90.0)
        self.assertEqual(found["source"], "computed from daily OHLC")

    def test_no_reversal_on_a_plain_trend(self):
        rows = _bars({"date": "2026-10-30", "o": 100, "h": 101, "l": 99, "c": 100.4})
        self.assertIsNone(detect_reversal(rows))

    def test_growth_comes_from_the_payload_not_a_guess(self):
        payload = {
            "quarterly": [
                {"period": "2026-09-30", "revenue": 112, "profit": 26},
                {"period": "2026-06-30", "revenue": 100, "profit": 20},
                {"period": "2026-03-31", "revenue": 90, "profit": 18},
                {"period": "2025-12-31", "revenue": 80, "profit": 16},
                {"period": "2025-09-30", "revenue": 100, "profit": 20},
            ],
            "annual": [
                {"period": "2026-03-31", "revenue": 400, "profit": 80},
                {"period": "2025-03-31", "revenue": 320, "profit": 64},
            ],
            "info": {"earningsGrowth": 0.11, "trailingPE": 18.2},
            "source": "Yahoo Finance",
            "url": "https://finance.yahoo.com/quote/SAMPLE.NS/financials",
        }
        got = fundamentals_from_payload(payload)
        self.assertAlmostEqual(got["profit_yoy_q"], 30.0)
        self.assertAlmostEqual(got["revenue_yoy_q"], 12.0)
        self.assertAlmostEqual(got["profit_yoy_y"], 25.0)
        self.assertAlmostEqual(got["future_growth"], 11.0)
        self.assertEqual(got["url"], payload["url"])

    def test_fii_breadth_and_sectors_parse_without_the_network(self):
        flows = parse_fii_dii([
            {"category": "FII/FPI", "date": "30-Oct-2026", "netValue": "-2450.5"},
            {"category": "DII", "date": "30-Oct-2026", "netValue": "1,980"},
        ])
        self.assertEqual(flows["fii_net_cr"], -2450.5)
        self.assertEqual(flows["dii_net_cr"], 1980.0)
        breadth = parse_breadth({"advance": {"advances": "18", "declines": "32", "unchanged": "0"}})
        self.assertEqual(breadth["declines"], 32)
        sectors = parse_sectors([
            {"name": "NIFTY 50", "pct": 0.1},
            {"name": "NIFTY IT", "pct": 1.6},
            {"name": "NIFTY BANK", "pct": -1.1},
        ])
        self.assertEqual(sectors["best"]["name"], "NIFTY IT")
        self.assertEqual(sectors["worst"]["name"], "NIFTY BANK")

    def test_collect_uses_the_injected_fetchers(self):
        levels = _levels(_hammer_bars())
        calls = []

        def financials(ticker):
            calls.append(ticker)
            return {"profit_yoy_q": 18.5, "source": "Yahoo Finance", "url": "https://finance.yahoo.com/quote/SAMPLE.NS/"}

        def flows():
            return {"fii_net_cr": -100.0, "source": "NSE", "url": "https://www.nseindia.com/api/fiidiiTradeReact"}

        story = collect("ta", levels, [], datetime(2026, 10, 30, tzinfo=IST), fetchers={
            "financials": financials, "flows": flows,
        })
        self.assertEqual(calls, ["SAMPLE.NS"])
        self.assertEqual(story["fundamentals"]["profit_yoy_q"], 18.5)
        self.assertEqual(story["reversal"]["name"], "hammer")
        self.assertEqual(story["flows"]["fii_net_cr"], -100.0)


def _story_levels():
    bars = _hammer_bars()
    levels = _levels(bars)
    levels["story"] = {
        "reversal": detect_reversal(bars),
        "plan": {"stop": 90.0, "target": 120.0, "source": "computed from daily OHLC"},
        "fundamentals": {
            "profit_yoy_q": 18.5, "revenue_yoy_q": 11.2, "profit_yoy_y": 15.0,
            "revenue_yoy_y": 9.4, "future_growth": 8.0,
            "source": "Yahoo Finance", "url": "https://finance.yahoo.com/quote/SAMPLE.NS/financials",
        },
        "flows": {"fii_net_cr": -2450.0, "dii_net_cr": 1980.0, "source": "NSE", "url": "https://www.nseindia.com/api/fiidiiTradeReact"},
        "news": {"title": "RBI policy review keeps the rate unchanged", "source": "livemint.com", "url": "https://www.livemint.com/x", "summary": "The rate was unchanged."},
    }
    levels["news_text"] = "RBI policy review keeps the rate unchanged The rate was unchanged."
    levels["story_figures"] = build_figures(levels, levels["story"])
    return levels


class ScriptTests(unittest.TestCase):
    def test_lines_stay_educational_and_inside_the_clock(self):
        levels = _story_levels()
        rows = rows_for("ta", levels)
        self.assertEqual(rows[0]["sec"], "hook")
        spoken = " ".join(row["t"] for row in rows)
        self.assertIn(EDU, spoken)
        self.assertIn("हैमर", spoken)
        self.assertNotIn("खरीदो", spoken)
        self.assertNotIn("बेचो", spoken)
        self.assertEqual(lint(rows), [])
        self.assertEqual(number_faults(rows, levels), [])
        self.assertEqual(compliance_faults(rows), [])
        seconds = estimate_seconds(rows) + 0.25 * len(rows)
        self.assertGreaterEqual(seconds, 40.0, spoken)
        self.assertLessEqual(seconds, 55.0, spoken)

    def test_an_invented_caption_number_is_rejected(self):
        levels = _story_levels()
        rows = rows_for("ta", levels)
        rows[2]["d"] = "प्रॉफिट 99.9%"
        self.assertTrue(number_faults(rows, levels))

    def test_a_news_number_must_be_in_the_article(self):
        levels = _story_levels()
        rows = rows_for("ta", levels)
        news = next(row for row in rows if row["sec"] == "news")
        news["d"] = "RBI cut rates by 50 bps"
        self.assertTrue(any("news number" in fault for fault in number_faults(rows, levels)))

    def test_missing_results_add_no_profit_line(self):
        levels = _story_levels()
        levels["story"].pop("fundamentals")
        levels["story_figures"] = build_figures(levels, levels["story"])
        rows = rows_for("ta", levels)
        self.assertNotIn("results", [row["sec"] for row in rows])
        self.assertEqual(number_faults(rows, levels), [])

    def test_close_rows_name_breadth_flows_and_sectors(self):
        from realchart.sample_facts import build_sample
        from realchart.session import dry_session
        with tempfile.TemporaryDirectory() as tmp:
            levels, _article, _chart, _intra = build_sample("close", dry_session("close", datetime(2026, 10, 31, 18, tzinfo=IST)), Path(tmp))
        rows = rows_for("close", levels)
        secs = [row["sec"] for row in rows]
        for sec in ("hook", "breadth", "flows", "news", "sectors", "level", "cta"):
            self.assertIn(sec, secs)
        self.assertEqual(lint(rows), [])
        self.assertEqual(number_faults(rows, levels), [])
        seconds = estimate_seconds(rows) + 0.25 * len(rows)
        self.assertGreaterEqual(seconds, 40.0)
        self.assertLessEqual(seconds, 55.0)


class ImagineRenderTests(unittest.TestCase):
    def test_absent_slot_falls_back_and_present_slot_stays_decorative(self):
        from realchart.imagine import load_imagine
        from realchart.story_layout import TEXT_LIMIT, story_frames
        levels = _story_levels()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = load_imagine(date="2026-10-30", ticker="SAMPLE", root=root)
            self.assertIsNone(missing["thumb_bg"])
            self.assertEqual(missing["broll_frames"], [])
            slot = root / "assets" / "imagine" / "SAMPLE"
            slot.mkdir(parents=True)
            Image.new("RGB", (200, 200), (220, 20, 20)).save(slot / "broll_a.png")
            Image.new("RGB", (200, 200), (20, 20, 220)).save(slot / "thumb_bg.png")
            Image.new("RGB", (80, 80), (0, 255, 0)).save(slot / "broll_chart.png")
            (slot / "manifest.json").write_text(json.dumps({"assets": [
                {"file": "thumb_bg.png", "role": "thumb_bg"},
                {"file": "broll_a.png", "role": "broll"},
                {"file": "broll_chart.png", "role": "broll"},
                {"file": "fake.json", "role": "chart"},
            ]}), encoding="utf-8")
            found = load_imagine(date="2026-10-30", ticker="SAMPLE", root=root, cache_dir=root / "cache")
            self.assertTrue(found["thumb_bg"])
            self.assertEqual(len(found["broll_frames"]), 1)
            self.assertTrue(any("chart" in item["reason"] for item in found["rejected"]))
            levels["imagine"] = found
            out = root / "cards"
            made = story_frames(out, "ta", levels, rows_for("ta", levels))
            self.assertEqual(len(made), len(rows_for("ta", levels)))
            boxes = json.loads((out / "text_boxes.json").read_text(encoding="utf-8"))
            self.assertTrue(boxes)
            for item in boxes:
                self.assertLess(item["box"][3], TEXT_LIMIT)
                self.assertGreaterEqual(item["box"][0], 0)
            card = Image.open(made[0]).convert("RGB")
            # Bottom strip is the decorative clip. The candle panel is not that clip.
            self.assertGreater(card.getpixel((20, 1800))[0], 180)
            panel = card.crop((70, 640, 1010, 1260))
            colors = panel.getcolors(500000)
            self.assertTrue(any(c[1][1] > 140 and c[1][0] < 80 for c in colors))
            self.assertFalse(all(px[0] > 180 and px[1] < 40 for _n, px in colors))

    def test_sample_render_writes_video_thumb_and_sources(self):
        from realchart.build import run
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1", "REALCHART_IMAGINE_DIR": str(Path(tmp) / "none")}):
                code = run("ta", out, now=datetime(2026, 10, 31, 19, tzinfo=IST), sample=True)
            detail = (out / "qa.json").read_text(encoding="utf-8") if (out / "qa.json").is_file() else (out / "HOLD").read_text(encoding="utf-8") if (out / "HOLD").is_file() else "no qa"
            self.assertEqual(code, 0, detail)
            self.assertTrue((out / "final.mp4").stat().st_size > 1000)
            self.assertTrue((out / "thumbnail.jpg").is_file())
            sources = json.loads((out / "sources.json").read_text(encoding="utf-8"))
            self.assertTrue(sources["sample"])
            names = {item["name"] for item in sources["figures"]}
            self.assertIn("profit_yoy_q", names)
            self.assertIn("fii_net_cr", names)
            self.assertIn("reversal_low", names)
            self.assertEqual(sources["imagine"]["used"], [])
            qa = json.loads((out / "qa.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["verdict"], "DRY-OK")
            self.assertEqual(qa["chart"], "ohlc")
            self.assertTrue((out / "ig_cover.jpg").is_file())
            self.assertGreater((out / "thumbnail.jpg").stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
