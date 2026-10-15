"""15-Oct runner close Reel: tofu, stale chart, voids, raw floats, IG ends, and the QA gate."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import nifty_day
from realchart import capture, script, video_qa
from realchart.build import Hold, _require_today, run
from realchart.fmt import inr, raw_float_faults, stamp
from realchart.fonts import FONT_BOLD, missing_glyphs, tofu_faults
from realchart.levels import apply_screen_levels, compute

IST = nifty_day.IST
LEGEND_TODAY = "Nifty 50 Index\n1D\nNSE\nO\n22,671.30\nH\n23,056.40\nL\n22,637.40\nC\n22,997.35\n+374.65"
LEGEND_AUGUST = "Nifty 50 Index\n1D\nNSE\nO\n24,610.05\nH\n24,708.40\nL\n24,560.10\nC\n24,631.30\n"


def _still(dest, color, seconds=2.0, busy=False):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1080, 1920), color)
    if busy:
        draw = ImageDraw.Draw(image)
        for x in range(0, 1080, 40):
            draw.rectangle((x, 300, x + 18, 1500), fill=(34, 181, 60) if x % 80 else (240, 30, 50))
    png = Path(dest).with_suffix(".png")
    image.save(png)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png), "-t", str(seconds),
        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", str(dest),
    ], check=True)
    return Path(dest)


class FormatTests(unittest.TestCase):
    def test_two_decimals_with_indian_commas(self):
        self.assertEqual(inr(22997.349609375), "22,997.35")
        self.assertEqual(inr(1234567.5), "12,34,567.50")
        self.assertEqual(inr(-374.650390625), "-374.65")
        self.assertEqual(script._fmt(22179.900390625), "22,179.90")

    def test_raw_floats_are_flagged(self):
        self.assertTrue(raw_float_faults(["Close 22997.349609375"]))
        self.assertTrue(raw_float_faults(["Target 22757.94921875–22865.36"]))
        self.assertEqual(raw_float_faults(["Close 22,997.35  +1.66%"]), [])

    def test_rss_stamp_never_leaks_raw(self):
        self.assertEqual(stamp("Thu, 15 Oct 2026 17:40:21 +0530"), "15 Oct 2026, 17:40 IST")
        self.assertEqual(stamp("not a date"), "")
        rows = script.chunks_for("close", compute(_bars()), {
            "title": "Nifty ends higher", "key_sentence": "It rose.", "source": "Mint",
            "published": "Thu, 15 Oct 2026 17:40:21 +0530",
        })
        shown = " ".join(row["d"] for row in rows)
        self.assertNotIn("17:40:21", shown)
        self.assertNotIn("+0530", shown)


class FontTests(unittest.TestCase):
    def test_bundled_font_covers_hindi_and_latin(self):
        self.assertTrue(FONT_BOLD.is_file())
        self.assertEqual(tofu_faults([
            "Rajesh Technical Trader ko follow करो", "निफ्टी क्लोज़ 22,997.35 ऊपर 1.66%",
            "SL 22,040.00–22,179.90 · KHABAR", script.CTA, script.DISCLAIMER,
        ]), [])

    def test_a_glyph_the_font_lacks_is_tofu(self):
        self.assertEqual(missing_glyphs("ok க"), ["க"])
        self.assertTrue(tofu_faults(["தமிழ்"]))

    def test_no_card_uses_a_system_font_path(self):
        root = Path(__file__).resolve().parent.parent / "realchart"
        for name in ("capture.py", "ends.py", "render.py"):
            self.assertNotIn("NotoSansDevanagari-Bold", (root / name).read_text(encoding="utf-8"), name)


def _bars(n=40):
    from datetime import timedelta
    day = datetime(2026, 10, 15, tzinfo=IST).date()
    days = []
    while len(days) < n:
        if day.weekday() < 5 and nifty_day.is_trading_day(day):
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    rows, price = [], 22000.0
    for index, day in enumerate(days):
        close = price + (-30 if index % 5 == 0 else 12)
        rows.append({"date": day.isoformat(), "o": price, "h": max(price, close) + 40, "l": min(price, close) - 35, "c": close})
        price = close
    return rows


class CaptureDateTests(unittest.TestCase):
    EXPECT = {"date": "2026-10-15", "o": 22671.30, "h": 23056.40, "l": 22637.40, "c": 22997.35}

    def test_legend_is_parsed_and_must_be_today(self):
        legend = capture.parse_legend(LEGEND_TODAY)
        self.assertEqual(legend["c"], 22997.35)
        self.assertEqual(capture.verify_last_candle(legend, self.EXPECT), "")
        stale = capture.verify_last_candle(capture.parse_legend(LEGEND_AUGUST), self.EXPECT)
        self.assertIn("last candle is not 2026-10-15", stale)
        self.assertIn("unreadable", capture.verify_last_candle({}, self.EXPECT))

    def test_intraday_legend_may_match_any_of_the_last_session_bars(self):
        from realchart.build import _last_bar_expect
        bars = [
            {"date": "2026-10-15", "o": 22947.2, "h": 22949.5, "l": 22926.05, "c": 22938.6},
            {"date": "2026-10-15", "o": 22939.95, "h": 22997.35, "l": 22939.95, "c": 22997.35},
        ]
        legend = {"o": 22947.20, "h": 22949.50, "l": 22926.05, "c": 22938.60}
        self.assertEqual(capture.verify_last_candle(legend, _last_bar_expect(bars)), "")
        yesterday = {"o": 22610.0, "h": 22630.0, "l": 22600.0, "c": 22622.7}
        self.assertIn("not 2026-10-15", capture.verify_last_candle(yesterday, _last_bar_expect(bars)))

    def test_a_stale_tradingview_shot_is_refused_not_relabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chart.png"

            def opener(_symbol, path):
                from PIL import Image
                Image.new("RGB", (40, 40), (30, 30, 30)).save(path)
                return {"ok": True, "path": str(path), "text": LEGEND_AUGUST, "legend": capture.parse_legend(LEGEND_AUGUST)}

            shot = capture.capture_chart("NSE:NIFTY", dest, _bars(), opener=opener, expect=self.EXPECT)
            self.assertEqual(shot["source"], "stale")
            with self.assertRaises(Hold):
                _require_today(shot, datetime(2026, 10, 15).date(), "daily chart")

    def test_a_chart_whose_last_candle_is_not_today_holds(self):
        with self.assertRaises(Hold) as raised:
            _require_today({"source": "yfinance", "last_candle_date": "2026-08-14"}, datetime(2026, 10, 15).date(), "daily chart")
        self.assertIn("2026-08-14", str(raised.exception))
        _require_today({"source": "tradingview", "last_candle_date": "2026-10-15"}, datetime(2026, 10, 15).date(), "daily chart")

    def test_close_build_holds_on_an_august_tradingview_capture(self):
        bars = _bars()
        now = datetime(2026, 10, 15, 17, 0, tzinfo=IST)
        stamp_ = datetime(2026, 10, 15, 15, 30, tzinfo=IST)

        def opener(_symbol, path):
            from PIL import Image
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 40), (30, 30, 30)).save(path)
            return {"ok": True, "path": str(path), "text": LEGEND_AUGUST, "legend": capture.parse_legend(LEGEND_AUGUST)}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            with mock.patch.dict(os.environ, {"REALCHART_NO_ALERT": "1", "REALCHART_SKIP_BROWSER": "1"}):
                code = run("close", out, now=now, dry_run=True, bars_for=lambda _t: (bars, stamp_), articles=[],
                           chart_opener=opener, chain_for=lambda _s: {"ok": False, "reason": "held"})
            self.assertEqual(code, 2)
            self.assertIn("capture", (out / "HOLD").read_text(encoding="utf-8"))
            self.assertFalse((out / "final.mp4").exists())


class VideoGateTests(unittest.TestCase):
    def test_empty_dark_card_is_black_and_a_chart_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            void = _still(Path(tmp) / "void.mp4", (14, 16, 20))
            chart = _still(Path(tmp) / "chart.mp4", (15, 15, 15), busy=True)
            self.assertTrue(video_qa.empty_frame_faults(void, "void"))
            self.assertEqual(video_qa.empty_frame_faults(chart, "chart"), [])

    def test_capture_date_gate(self):
        self.assertEqual(video_qa.capture_faults({"daily chart": "2026-10-15"}, "2026-10-15"), [])
        self.assertTrue(video_qa.capture_faults({"daily chart": "2026-08-14"}, "2026-10-15"))

    def test_outro_card_is_silent_readable_and_not_a_void(self):
        from realchart.ends import FOLLOW_RTT, render_card
        with tempfile.TemporaryDirectory() as tmp:
            card = render_card(FOLLOW_RTT, Path(tmp) / "outro.mp4")
            self.assertLess(video_qa.mean_volume_db(card), video_qa.OUTRO_MAX_MEAN_DB)
            self.assertEqual(video_qa.empty_frame_faults(card, "outro"), [])

    def test_ig_without_outro_or_with_a_beep_holds(self):
        from realchart.ends import FOLLOW_RTT, attach, render_card
        from realchart.render import assemble
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cards = tmp / "cards"
            cards.mkdir()
            Image.new("RGB", (32, 32), (90, 120, 160)).save(cards / "a.png")
            main = assemble([cards / "a.png"], tmp / "none.wav", tmp / "final.mp4", seconds=1.0)
            attach(main, tmp)
            from realchart.ends import INTRO_RTT, OUTRO_RTT
            self.assertEqual(video_qa.ends_faults(tmp, INTRO_RTT, OUTRO_RTT, FOLLOW_RTT), [])
            beep = tmp / "beep.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-i", str(OUTRO_RTT), "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(beep),
            ], check=True)
            with mock.patch("realchart.ends.OUTRO_RTT", beep):
                attach(main, tmp)
                faults = video_qa.ends_faults(tmp, INTRO_RTT, beep, FOLLOW_RTT)
            self.assertTrue(any("beep" in fault for fault in faults), faults)
            (tmp / "final_ig.mp4").unlink()
            self.assertEqual(video_qa.ends_faults(tmp, INTRO_RTT, OUTRO_RTT, FOLLOW_RTT), ["ig: final_ig.mp4 missing"])


class RenderTests(unittest.TestCase):
    def test_cards_fill_the_frame_follow_the_beats_and_show_no_raw_floats(self):
        from PIL import Image
        from realchart.render import beat_seconds, frames
        levels = compute(_bars(), "NSE:NIFTY", "^NSEI")
        levels["close"] = 22997.349609375
        levels["chart_source"] = "tradingview"
        apply_screen_levels(levels, {"ok": False, "reason": "held"})
        rows = script.chunks_for("close", levels, None)
        with tempfile.TemporaryDirectory() as tmp:
            chart = Path(tmp) / "chart.png"
            img = Image.new("RGB", (1748, 1838), (15, 15, 15))
            for x in range(0, 1748, 30):
                img.paste((34, 181, 60) if x % 60 else (240, 30, 50), (x, 500, x + 14, 1300))
            img.save(chart)
            made = frames(Path(tmp) / "cards", "close", levels, rows, chart, None, {"path": str(chart), "interval": "5"})
            self.assertEqual([p.stem for p in made], [row["sec"] for row in rows])
            texts = json.loads((Path(tmp) / "cards" / "overlay_text.json").read_text(encoding="utf-8"))
            self.assertEqual(raw_float_faults(texts), [])
            self.assertEqual(tofu_faults(texts), [])
            self.assertTrue(any("22,997.35" in text for text in texts))
            for path in made:
                with Image.open(path) as card:
                    # The chart fills the full width: no letterbox column at the sides.
                    self.assertNotEqual(card.getpixel((4, 700)), (14, 16, 20), path.name)
        seconds = beat_seconds(rows, 45.0)
        self.assertAlmostEqual(sum(seconds), 45.0, places=3)
        longest = max(range(len(rows)), key=lambda i: len(rows[i]["t"].split()))
        self.assertEqual(max(range(len(rows)), key=lambda i: seconds[i]), longest)


if __name__ == "__main__":
    unittest.main()
