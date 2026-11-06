"""Story Reel fixes: directional plan, Rajesh thumbnail, Follow CTA, empty Yahoo bar."""
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from realchart.build import Hold, _fallback_session, _prepare_story, rows_from_records
from realchart.host_photo import _avoid_blue_front, _blocked_photo, host_image, host_look
from realchart.script import lint, number_faults
from realchart.session import IST
from realchart.story_facts import detect_reversal, direction_ok, pattern_plan, stamp_plan
from realchart.story_layout import plan_marks, write_thumbnail
from realchart.story_script import post_meta, rows_for, thumb_headline


def _rising(last, start=100.0, step=1.0, count=40):
    rows = []
    price = float(start)
    for i in range(count - 1):
        opened, close = price, price + step
        rows.append({
            "date": f"2026-08-{(i % 27) + 1:02d}",
            "o": round(opened, 2),
            "h": round(max(opened, close) + 0.4, 2),
            "l": round(min(opened, close) - 0.4, 2),
            "c": round(close, 2),
        })
        price = close
    rows.append(last)
    return rows


def _shooting_star():
    """Uptrend, then a bearish star. Prior lows are far enough for 1.5R."""
    last = {"date": "2026-10-30", "o": 138.0, "h": 152.0, "l": 137.0, "c": 139.0}
    return _rising(last, start=80.0, step=1.5, count=42)


def _tight_star():
    """A shooting star whose wick is too long for any prior low to pay 1.5R."""
    rows = []
    price = 100.0
    for i in range(40):
        opened, close = price, min(107.5, price + 0.2)
        rows.append({
            "date": f"2026-08-{(i % 27) + 1:02d}",
            "o": round(opened, 2), "h": round(close + 0.3, 2),
            "l": round(max(100.0, opened - 0.2), 2), "c": round(close, 2),
        })
        price = close
    rows[-1] = {"date": "2026-10-30", "o": 107.0, "h": 140.0, "l": 106.4, "c": 107.3}
    return rows


def _hammer():
    # Decline from 150 into a hammer. Prior highs sit far enough above for 1.5R.
    return _rising(
        {"date": "2026-10-30", "o": 100, "h": 102, "l": 90, "c": 101.5},
        start=150.0, step=-1.1, count=36,
    )


class PlanTests(unittest.TestCase):
    def test_shooting_star_is_short_with_stop_above_and_rr(self):
        bars = _shooting_star()
        found = detect_reversal(bars)
        self.assertEqual(found["name"], "shooting star")
        self.assertEqual(found["side"], "bearish")
        plan = pattern_plan(bars, found)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["side"], "short")
        self.assertEqual(plan["pattern_side"], "bearish")
        self.assertGreater(plan["sl"], plan["entry"])
        self.assertLess(plan["target"], plan["entry"])
        self.assertGreaterEqual(plan["rr"], 1.5)
        self.assertTrue(direction_ok(plan))
        # The broken sample: stop under the close and target over it, RR well under 1.
        self.assertFalse(plan["sl"] < found["close"] < plan["target"])
        self.assertGreater(plan["rr"], 0.23)

    def test_hammer_is_long_with_stop_below(self):
        bars = _hammer()
        found = detect_reversal(bars)
        self.assertEqual(found["name"], "hammer")
        plan = pattern_plan(bars, found)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["side"], "long")
        self.assertLess(plan["sl"], plan["entry"])
        self.assertGreater(plan["target"], plan["entry"])
        self.assertGreaterEqual(plan["rr"], 1.5)

    def test_rr_below_1_5_is_skipped(self):
        bars = _tight_star()
        found = detect_reversal(bars)
        self.assertEqual(found["side"], "bearish")
        self.assertIsNone(pattern_plan(bars, found))

    def test_levels_json_gets_side_entry_sl_and_target(self):
        from realchart.levels import compute
        from realchart.story_facts import build_figures, collect
        bars = _shooting_star()
        levels = compute(bars, "NSE:JSWSTEEL", "JSWSTEEL.NS")
        levels["bars"] = bars
        levels["format"] = "story"
        levels["story"] = collect("ta", levels, [], datetime(2026, 10, 30, 19, tzinfo=IST), fetchers={
            "financials": lambda _t: None, "flows": lambda: None,
        })
        stamp_plan(levels)
        self.assertEqual(levels["side"], "short")
        self.assertGreater(levels["sl"], levels["entry"])
        self.assertLess(levels["target"], levels["entry"])
        with tempfile.TemporaryDirectory() as tmp:
            from realchart.build import _dump_levels
            _dump_levels(Path(tmp), levels)
            stored = json.loads((Path(tmp) / "levels.json").read_text(encoding="utf-8"))
        for key in ("side", "entry", "sl", "target"):
            self.assertIn(key, stored)
        self.assertEqual(stored["side"], "short")
        self.assertGreaterEqual(stored["rr"], 1.5)
        levels["story_figures"] = build_figures(levels, levels["story"])
        spoken = " ".join(row["t"] for row in rows_for("ta", levels))
        self.assertIn("एंट्री", spoken)
        self.assertIn("स्टॉप लॉस", spoken)
        self.assertIn("टारगेट", spoken)
        self.assertIn("शॉर्ट", spoken)

    def test_prepare_skips_a_star_that_cannot_pay_and_picks_another_stock(self):
        session = date(2026, 10, 30)
        now = datetime(2026, 10, 30, 19, 0, tzinfo=IST)
        stamp = datetime(2026, 10, 30, 15, 30, tzinfo=IST)
        good = _hammer()
        bad = _tight_star()

        def bars_for(ticker):
            if ticker == "TCS.NS":
                return good, stamp
            if ticker == "RELIANCE.NS":
                return bad, stamp
            return [], None

        def facts(kind, levels, items, moment):
            from realchart.story_facts import collect
            return collect(kind, levels, items, moment, fetchers={
                "financials": lambda _t: None, "flows": lambda: None,
            })

        with tempfile.TemporaryDirectory() as tmp:
            levels, _article, _chart, _intra = _prepare_story(
                "ta", session, now, Path(tmp), bars_for, [], True, facts,
            )
        self.assertEqual(levels["symbol"], "NSE:TCS")
        self.assertEqual(levels["side"], "long")
        self.assertGreaterEqual(levels["rr"], 1.5)
        self.assertTrue(direction_ok(levels["story"]["plan"]))

    def test_chart_draws_entry_sl_and_target(self):
        from realchart.levels import compute
        from realchart.story_facts import collect
        from realchart.story_layout import paint
        bars = _shooting_star()
        levels = compute(bars, "NSE:JSWSTEEL", "JSWSTEEL.NS")
        levels["bars"] = bars
        levels["story"] = collect("ta", levels, [], datetime(2026, 10, 30, 19, tzinfo=IST), fetchers={
            "financials": lambda _t: None, "flows": lambda: None,
        })
        stamp_plan(levels)
        marks = {label for _v, _c, label in plan_marks(levels)}
        self.assertEqual(marks, {"एंट्री", "एसएल", "टारगेट"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.png"
            paint(path, (24, 72, 190), {"sec": "plan", "d": "प्लान", "source_label": "OHLC"}, levels)
            colors = Image.open(path).convert("RGB").crop((70, 640, 1010, 1260)).getcolors(500000)
        self.assertTrue(any(px[0] > 240 and px[1] > 180 and px[2] < 40 for _n, px in colors))
        self.assertTrue(any(px[0] > 180 and px[1] < 90 and px[2] < 100 for _n, px in colors))
        self.assertTrue(any(px[1] > 150 and px[0] < 40 for _n, px in colors))


class ThumbnailTests(unittest.TestCase):
    def _levels(self):
        from tests.test_story_format import _story_levels
        levels = _story_levels()
        levels["symbol"] = "NSE:JSWSTEEL"
        return levels

    def test_headline_is_devanagari_not_the_ticker(self):
        text = thumb_headline("ta", self._levels())
        first = text.split("\n")[0]
        self.assertRegex(first, r"[\u0900-\u097F]")
        self.assertNotIn("JSWSTEEL", first)
        self.assertNotEqual(text.strip(), "JSWSTEEL")

    def test_looks_vary_and_skip_the_blue_front_face(self):
        seen = set()
        for i in range(16):
            look = host_look(f"STOCK{i}|2026-10-30")
            seen.add((look["pose"], look["outfit"], look["background"]))
            self.assertFalse(look["pose"] in {"neutral", "front", "front_facing"} and look["outfit"] == "blue")
            self.assertNotEqual(look["outfit"], "blue")
            if look.get("path"):
                self.assertFalse(_blocked_photo(Path(look["path"])))
        self.assertGreaterEqual(len(seen), 4)
        self.assertEqual(_avoid_blue_front("neutral", "blue")[0], "maroon")

    def test_a_blue_shirt_front_file_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "assets" / "rajesh"
            folder.mkdir(parents=True)
            Image.new("RGB", (40, 60), (20, 40, 180)).save(folder / "blue_shirt_front.jpg")
            look = host_look("JSWSTEEL|2026-10-30", root=root)
            self.assertEqual(look["source"], "fallback")
            image, _look = host_image("JSWSTEEL|2026-10-30", root=root, height=200)
            self.assertEqual(image.mode, "RGBA")
            self.assertGreater(image.size[1], 50)

    def test_thumbnail_always_composites_a_portrait_and_the_topic_chart(self):
        levels = self._levels()
        magenta = Image.new("RGBA", (180, 320), (255, 0, 255, 255))
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "thumbnail.jpg"
            from unittest import mock
            with mock.patch("realchart.host_photo.host_image", return_value=(magenta, {
                "pose": "point", "outfit": "maroon", "background": "ember",
                "background_rgb": ((48, 16, 18), (120, 48, 24)), "source": "character",
            })):
                write_thumbnail(dest, "ta", levels)
            image = Image.open(dest.with_suffix(".png")).convert("RGB")
            colors = image.getcolors(2_000_000)
            self.assertTrue(any(px[0] > 240 and px[1] < 15 and px[2] > 240 for _n, px in colors))
            self.assertEqual(levels["host"]["pose"], "point")
            self.assertEqual(levels["host"]["outfit"], "maroon")
            # Yellow headline ink is on the cover, and the candle panel is not flat.
            self.assertTrue(any(px[0] > 230 and px[1] > 180 and px[2] < 40 for _n, px in colors))
            panel = image.crop((36, 760, 700, 1560))
            self.assertGreater(len(panel.getcolors(200000)), 8)


class CtaTests(unittest.TestCase):
    def test_story_cta_is_follow_only(self):
        from tests.test_story_format import _story_levels
        levels = _story_levels()
        for kind in ("ta", "close"):
            rows = rows_for(kind, levels)
            blob = " ".join(row["t"] + " " + row["d"] for row in rows)
            self.assertIn("Follow करो", blob)
            self.assertNotIn("Comment", blob)
            self.assertNotIn("कमेंट", blob)
            self.assertEqual(lint(rows), [])
            self.assertEqual(number_faults(rows, levels), [])
        self.assertEqual(post_meta("ta", levels)["pinned_comment"], "Follow करो")


class YahooEmptyBarTests(unittest.TestCase):
    def test_nan_latest_bar_falls_back_to_the_previous_session(self):
        records = []
        for day in range(1, 30):
            records.append((
                datetime(2026, 10, day, 15, 30),
                {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5},
            ))
        # 30 Oct is a Friday. The newest bar is empty.
        records.append((
            datetime(2026, 10, 30, 15, 30),
            {"Open": float("nan"), "High": float("nan"), "Low": float("nan"), "Close": float("nan")},
        ))
        rows, _stamp, empty = rows_from_records(records)
        self.assertEqual(empty, date(2026, 10, 30))
        self.assertEqual(rows[-1]["date"], "2026-10-29")
        self.assertEqual(_fallback_session(rows, date(2026, 10, 30), empty), date(2026, 10, 29))
        # A genuinely old last bar is not treated as that fallback.
        old = [dict(rows[0], date="2026-10-01")]
        self.assertEqual(_fallback_session(old, date(2026, 10, 30), date(2026, 10, 30)), date(2026, 10, 30))

    def test_prepare_close_uses_the_last_valid_session(self):
        session = date(2026, 10, 30)
        now = datetime(2026, 10, 30, 19, 0, tzinfo=IST)
        rows = []
        price = 25000.0
        for day in range(1, 30):
            rows.append({
                "date": f"2026-10-{day:02d}",
                "o": price, "h": price + 20, "l": price - 20, "c": price + 5,
            })
            price += 5
        stamp = datetime(2026, 10, 29, 15, 30, tzinfo=IST)

        def bars_for(_ticker):
            return rows, stamp, date(2026, 10, 30)

        def facts(kind, levels, items, moment):
            return {"reversal": None, "plan": None, "news": None}

        with tempfile.TemporaryDirectory() as tmp:
            levels, _a, chart, _i = _prepare_story(
                "close", session, now, Path(tmp), bars_for, [], True, facts,
            )
        self.assertTrue(levels["session_fallback"])
        self.assertEqual(str(levels["last_date"])[:10], "2026-10-29")
        self.assertEqual(chart["last_candle_date"], "2026-10-29")

    def test_a_real_miss_still_says_yahoo_close_is_not_the_session(self):
        session = date(2026, 10, 30)
        now = datetime(2026, 10, 30, 19, 0, tzinfo=IST)
        rows = [{
            "date": "2026-10-29", "o": 1, "h": 2, "l": 0.5, "c": 1.5,
        }] * 25
        stamp = datetime(2026, 10, 29, 15, 30, tzinfo=IST)

        def bars_for(_ticker):
            return rows, stamp

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Hold) as caught:
                _prepare_story("close", session, now, Path(tmp), bars_for, [], False, lambda *a: {})
        self.assertIn("Yahoo close is not 2026-10-30", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
