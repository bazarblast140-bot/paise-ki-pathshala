"""Motion components: not blank, on the word clock, numbers from the plan."""
import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

import board_kit as K
import board_short
import motion_lib
import safe_area
import trade_plan

IST = timezone(timedelta(hours=5, minutes=30))


def _board():
    img = K.background()
    K.panel(img)
    trade_plan._grid(img)
    return img


def _flat():
    return Image.new("RGB", (K.W, K.H), motion_lib.BG)


def _near(img, color, tol=18):
    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    delta = np.abs(rgb - np.array(color, dtype=np.int16)).sum(axis=2)
    return int((delta <= tol).sum())


def _std(img):
    crop = np.asarray(img.convert("RGB").crop(K.BOARD), dtype=np.float32)
    return float(crop.std()), float((crop.mean(axis=2) > 42).mean())


def _assert_painted(test, img):
    std, bright = _std(img)
    test.assertGreaterEqual(std, 5.0, std)
    test.assertGreaterEqual(bright, 0.010, bright)


def _hammer():
    return trade_plan.plan_for("pat_hammer")


def _beats():
    return {
        beat: (index * 8.0, index * 8.0 + 8.0)
        for index, beat in enumerate(trade_plan.BEATS)
    }


def _clock_topic():
    return {
        "hook_line": "हैमर कैंडल पर खरीद कब करें?",
        "_beat_times": _beats(),
        "_chart_overlays": [
            {"kind": "trend", "start": 8.0},
            {"kind": "sr", "start": 16.0, "price": 100.0},
            {"kind": "entry", "start": 24.0, "price": 118.0},
            {"kind": "sl", "start": 32.0, "price": 98.0},
            {"kind": "target", "start": 40.0, "price": 158.0},
        ],
    }


class StyleTests(unittest.TestCase):
    def test_palette_and_timing_match_the_sheet(self):
        self.assertEqual(motion_lib.BG, (0x16, 0x16, 0x16))
        self.assertEqual(motion_lib.BULL, (0x22, 0xB5, 0x3C))
        self.assertEqual(motion_lib.BEAR, (0xF0, 0x1E, 0x32))
        self.assertEqual(motion_lib.ENTRY, (0xF5, 0xD9, 0x0A))
        self.assertEqual(motion_lib.SL, (0xEF, 0x44, 0x44))
        self.assertEqual(motion_lib.TARGET, (0x22, 0xC5, 0x5E))
        self.assertEqual(motion_lib.ATTENTION, (0xF5, 0xC5, 0x18))
        self.assertEqual(motion_lib.CONTEXT_CANDLE_S, 0.15)
        self.assertEqual(motion_lib.PILL_LAG_S, 0.12)
        self.assertEqual(motion_lib.ZOOM_S, 0.60)
        self.assertEqual(motion_lib.ZOOM_PEAK, 1.35)
        self.assertEqual(motion_lib.CANDLE_DIM, 0.35)
        self.assertEqual(motion_lib.RR_ALPHA, 0.30)
        self.assertEqual(motion_lib.TITLE_S, 0.30)
        self.assertEqual(motion_lib.COUNTER_S, 0.50)
        self.assertGreaterEqual(motion_lib.REVEAL_S, 0.25)
        self.assertLessEqual(motion_lib.REVEAL_S, 0.40)
        self.assertGreaterEqual(motion_lib.LABEL_SIZE, 44)
        self.assertLessEqual(motion_lib.LABEL_SIZE, 56)
        self.assertGreaterEqual(motion_lib.COUNTER_SIZE, 64)
        self.assertLessEqual(motion_lib.COUNTER_SIZE, 80)
        self.assertGreaterEqual(motion_lib.CAPTION_SIZE, 64)
        self.assertLessEqual(motion_lib.CAPTION_SIZE, 72)
        self.assertEqual(motion_lib.WHOOSH_UNDER_DB, 20.0)
        self.assertEqual(motion_lib.MAX_WHOOSH, 4)
        self.assertGreater(motion_lib.ease_out_cubic(0.5), 0.5)
        self.assertAlmostEqual(motion_lib.ease_out_cubic(0.5), 0.875)
        self.assertAlmostEqual(motion_lib.ease_in_out(0.5), 0.5)


class EasingTests(unittest.TestCase):
    def test_window_is_zero_before_and_one_after(self):
        self.assertEqual(motion_lib.window_progress(9.9, 10.0, 11.0), 0.0)
        self.assertEqual(motion_lib.window_progress(11.0, 10.0, 11.0), 1.0)
        self.assertEqual(motion_lib.window_progress(12.0, 10.0, 11.0), 1.0)
        mid = motion_lib.window_progress(10.5, 10.0, 11.0)
        self.assertGreater(mid, 0.5)
        self.assertLess(mid, 1.0)


class CandleTests(unittest.TestCase):
    def test_context_bursts_and_key_candles_wait_for_their_beat(self):
        plan = _hammer()
        context, key = motion_lib.candle_roles(plan)
        self.assertIn(plan.focus, key)
        self.assertGreaterEqual(len(key), 2)
        self.assertLessEqual(len(key), 3)
        self.assertTrue(context)
        windows = motion_lib.candle_windows(plan, _beats())
        self.assertEqual(windows[context[0]][1], 0.0)
        self.assertAlmostEqual(windows[context[0]][1] - windows[context[0]][0], 0.15)
        rest = context[1:]
        self.assertTrue(rest)
        self.assertGreater(windows[rest[-1]][0], 1.0)
        for left, right in zip(rest, rest[1:]):
            self.assertGreater(windows[right][0], windows[left][0])
            self.assertAlmostEqual(windows[left][1] - windows[left][0], 0.15)
        self.assertAlmostEqual(windows[rest[-1]][1] - windows[rest[-1]][0], 0.15)
        for index, beat in zip(key, ("pehchan", "context", "entry")):
            self.assertAlmostEqual(windows[index][0], _beats()[beat][0])
            self.assertAlmostEqual(windows[index][1] - windows[index][0], motion_lib.REVEAL_S)
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        empty = _flat()
        motion_lib.candle_build(empty, box, plan.ohlc, lo, hi, 0.0, pad)
        self.assertEqual(_near(empty, motion_lib.BULL) + _near(empty, motion_lib.BEAR), 0)
        half = _flat()
        motion_lib.candle_build(half, box, plan.ohlc, lo, hi, 0.5, pad)
        full = _flat()
        motion_lib.candle_build(full, box, plan.ohlc, lo, hi, 1.0, pad)
        _assert_painted(self, full)
        self.assertGreater(
            _near(full, motion_lib.BULL) + _near(full, motion_lib.BEAR),
            _near(half, motion_lib.BULL) + _near(half, motion_lib.BEAR),
        )
        self.assertGreater(_near(half, motion_lib.BULL) + _near(half, motion_lib.BEAR), 0)
        clock = {"candles": (2.0, 4.0)}
        before = motion_lib.draw_plan(_flat(), plan, [], 1.0, clock)
        after = motion_lib.draw_plan(_flat(), plan, [], 4.0, clock)
        self.assertEqual(before["progress"]["candles"], 0.0)
        self.assertEqual(after["progress"]["candles"], 1.0)

    def test_other_candles_dim_to_35_percent_while_the_key_candle_is_pushed(self):
        plan = _hammer()
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        full = _flat()
        ax = motion_lib.candle_build(full, box, plan.ohlc, lo, hi, 1.0, pad)
        row = plan.ohlc[0]
        cx = int(round(ax.x(0)))
        cy = int(round(ax.y((row[0] + row[3]) / 2.0)))
        solid = np.asarray(full.convert("RGB"))[cy, cx]
        dimmed = _flat()
        motion_lib.candle_build(
            dimmed, box, plan.ohlc, lo, hi, 1.0, pad,
            per_candle=[1.0] * len(plan.ohlc), focus=plan.focus, dim=0.35,
        )
        faded = np.asarray(dimmed.convert("RGB"))[cy, cx]
        self.assertGreater(int(np.abs(solid - motion_lib.BG).sum()), 40)
        self.assertLess(int(np.abs(faded - motion_lib.BG).sum()), int(np.abs(solid - motion_lib.BG).sum()))


class TrendTests(unittest.TestCase):
    def test_arrow_draws_in_and_settles_on_the_same_tip(self):
        plan = _hammer()
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        base = _flat()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        quiet = base.copy()
        self.assertFalse(motion_lib.trend_arrow(quiet, ax, plan, 0.0))
        self.assertTrue(np.array_equal(np.asarray(quiet), np.asarray(base)))
        partial = base.copy()
        done = base.copy()
        reference = base.copy()
        motion_lib.trend_arrow(partial, ax, plan, 0.35)
        motion_lib.trend_arrow(done, ax, plan, 1.0)
        start, end = plan.ohlc[0], plan.ohlc[plan.focus]
        K.arrow(
            reference,
            (ax.x(0), ax.y(start[3])),
            (ax.x(plan.focus), ax.y(end[3])),
            motion_lib.ATTENTION, w=8, head=26,
        )
        _assert_painted(self, done)
        self.assertTrue(np.array_equal(np.asarray(done), np.asarray(reference)))
        self.assertGreater(_near(done, motion_lib.ATTENTION), _near(partial, motion_lib.ATTENTION))
        self.assertGreater(_near(partial, motion_lib.ATTENTION), 0)
        clock = {"trend": (8.0, 8.32), "candles": (0.0, 1.0)}
        early = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 7.0, clock)
        late = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 9.0, clock)
        self.assertEqual(early["progress"]["trend"], 0.0)
        self.assertEqual(late["progress"]["trend"], 1.0)


class BandTests(unittest.TestCase):
    def test_band_fades_in_on_the_computed_price(self):
        plan = _hammer()
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        base = _flat()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        y = ax.y(plan.sr)
        self.assertGreater(abs(y - 110), 40)
        before = base.copy()
        self.assertFalse(motion_lib.sr_band(before, ax, plan.sr, 0.0))
        after = base.copy()
        self.assertTrue(motion_lib.sr_band(after, ax, plan.sr, 1.0))
        _assert_painted(self, after)
        row = int(round(y))
        sample_x = int(ax.x0 + ax.pl + 4)
        before_px = np.asarray(before.convert("RGB"))[row, sample_x]
        after_px = np.asarray(after.convert("RGB"))[row, sample_x]
        self.assertGreater(int(after_px.sum()), int(before_px.sum()) + 30)
        self.assertLess(int(after_px.max()), 180)
        clock = {"sr": (16.0, 16.32), "candles": (0.0, 1.0)}
        early = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 15.0, clock)
        late = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 17.0, clock)
        self.assertEqual(early["progress"]["sr"], 0.0)
        self.assertEqual(late["progress"]["sr"], 1.0)
        self.assertEqual(late["levels"]["sr"]["price"], plan.sr)


class LevelTests(unittest.TestCase):
    def test_lines_draw_left_to_right_with_the_plan_prices(self):
        plan = _hammer()
        self.assertEqual(plan.entry, 118.0)
        self.assertEqual(plan.sl, 98.0)
        self.assertEqual(plan.target, 158.0)
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        base = _flat()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        y = int(round(ax.y(plan.entry)))
        label = f"ENTRY {trade_plan.fmt(plan.entry)}"
        anchor = (ax.x(plan.focus), ax.y(plan.ohlc[plan.focus][3]))
        hidden = base.copy()
        info = motion_lib.level_line(
            hidden, box, y, motion_lib.ENTRY, label, "right", 0.0, anchor=anchor,
        )
        self.assertFalse(info["drawn"])
        self.assertEqual(_near(hidden, motion_lib.ENTRY), _near(base, motion_lib.ENTRY))
        stub = base.copy()
        stub_info = motion_lib.level_line(
            stub, box, y, motion_lib.ENTRY, label, "right", 0.3, anchor=anchor,
        )
        self.assertIsNone(stub_info["price_label"])
        self.assertFalse(stub_info["pill"])
        x0 = box[0] + 40
        x1 = box[2] - 40
        stub_rgb = np.asarray(stub.convert("RGB"))
        self.assertEqual(tuple(int(v) for v in stub_rgb[y, x0]), motion_lib.ENTRY)
        self.assertNotEqual(tuple(int(v) for v in stub_rgb[y, x1]), motion_lib.ENTRY)
        full = base.copy()
        full_info = motion_lib.level_line(
            full, box, y, motion_lib.ENTRY, label, "right", 1.0, anchor=anchor,
        )
        self.assertEqual(full_info["price_label"], label)
        self.assertIn("118", full_info["price_label"])
        self.assertTrue(full_info["pill"])
        full_rgb = np.asarray(full.convert("RGB"))
        self.assertEqual(tuple(int(v) for v in full_rgb[y, x0]), motion_lib.ENTRY)
        self.assertEqual(tuple(int(v) for v in full_rgb[y, x1]), motion_lib.ENTRY)
        _assert_painted(self, full)
        self.assertGreater(_near(full, motion_lib.WHITE), _near(base, motion_lib.WHITE))
        bare = base.copy()
        motion_lib.level_line(
            bare, box, y, motion_lib.ENTRY, label, "right", 1.0, anchor=None,
        )
        self.assertGreater(_near(full, motion_lib.WHITE), _near(bare, motion_lib.WHITE))
        clock = {
            "candles": (0.0, 1.0),
            "entry": (24.0, 24.32),
            "sl": (32.0, 32.32),
            "target": (40.0, 40.32),
        }
        shown = trade_plan.overlays(plan)
        early = motion_lib.draw_plan(_flat(), plan, shown, 24.0, clock)
        done_line = motion_lib.draw_plan(_flat(), plan, shown, 24.32, clock)
        popped = motion_lib.draw_plan(_flat(), plan, shown, 24.44, clock)
        late = motion_lib.draw_plan(_flat(), plan, shown, 41.0, clock)
        self.assertEqual(early["progress"]["entry"], 0.0)
        self.assertFalse(early["levels"]["entry"]["drawn"])
        self.assertEqual(early["levels"]["entry"]["price"], plan.entry)
        self.assertEqual(done_line["progress"]["entry"], 1.0)
        self.assertFalse(done_line["levels"]["entry"]["pill"])
        self.assertTrue(popped["levels"]["entry"]["pill"])
        self.assertEqual(late["progress"]["entry"], 1.0)
        self.assertEqual(late["progress"]["sl"], 1.0)
        self.assertEqual(late["progress"]["target"], 1.0)
        self.assertEqual(late["levels"]["sl"]["price"], plan.sl)
        self.assertEqual(late["levels"]["target"]["price"], plan.target)
        self.assertIn("98", late["levels"]["sl"]["label"])
        self.assertIn("158", late["levels"]["target"]["label"])


class RiskTests(unittest.TestCase):
    def test_box_uses_the_computed_risk_reward_and_ratio(self):
        plan = _hammer()
        self.assertEqual(plan.risk, 20.0)
        self.assertEqual(plan.reward, 40.0)
        self.assertEqual(plan.rr_text, "1:2")
        readout = motion_lib.rr_readout(plan)
        self.assertEqual(readout["risk"], 20.0)
        self.assertEqual(readout["reward"], 40.0)
        self.assertEqual(readout["rr"], "1:2")
        self.assertEqual(readout["text"], "RR 1:2")
        quiet = _flat()
        hidden = motion_lib.risk_reward_box(quiet, plan, 0.0)
        self.assertFalse(hidden["shown"])
        self.assertTrue(np.array_equal(np.asarray(quiet), np.asarray(_flat())))
        done = _flat()
        shown = motion_lib.risk_reward_box(done, plan, 1.0)
        _assert_painted(self, done)
        self.assertTrue(shown["shown"])
        self.assertEqual(shown["risk"], plan.risk)
        self.assertEqual(shown["reward"], plan.reward)
        self.assertEqual(shown["rr"], plan.rr_text)
        self.assertEqual(shown["text"], "RR 1:2")
        self.assertGreater(shown["reward_px"], shown["risk_px"])
        self.assertAlmostEqual(shown["reward_px"], shown["risk_px"] * 2.0, delta=2.0)
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        ax = motion_lib._axes(box, len(plan.ohlc), lo, hi, pad)
        cx = int(round(ax.x(plan.focus)))
        reward_y = int(round((ax.y(plan.entry) + ax.y(plan.target)) / 2.0))
        risk_y = int(round((ax.y(plan.entry) + ax.y(plan.sl)) / 2.0))
        rgb = np.asarray(done.convert("RGB"))
        reward = rgb[reward_y, cx]
        risk = rgb[risk_y, cx]
        self.assertGreater(int(reward[1]), int(reward[0]))
        self.assertGreater(int(risk[0]), int(risk[1]))
        clock = {"rr": (41.0, 41.32), "candles": (0.0, 1.0)}
        before = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 40.0, clock)
        after = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 42.0, clock)
        self.assertEqual(before["progress"]["rr"], 0.0)
        self.assertFalse(before["rr"]["shown"])
        self.assertEqual(after["progress"]["rr"], 1.0)
        self.assertEqual(after["rr"]["risk"], plan.risk)
        self.assertEqual(after["rr"]["reward"], plan.reward)
        self.assertEqual(after["rr"]["rr"], "1:2")
        self.assertGreater(after["rr"]["reward_px"], after["rr"]["risk_px"])


class HighlightTests(unittest.TestCase):
    def test_key_candle_pushes_to_1_35_then_eases_back(self):
        start = 8.0
        rest_zoom, rest = motion_lib.key_zoom_at(start, start)
        self.assertEqual(rest_zoom, 1.0)
        self.assertFalse(rest)
        mid_zoom, mid_on = motion_lib.key_zoom_at(start + 0.30, start)
        self.assertTrue(mid_on)
        self.assertAlmostEqual(mid_zoom, 1.175, places=3)
        peak_zoom, peak_on = motion_lib.key_zoom_at(start + motion_lib.ZOOM_S, start)
        self.assertTrue(peak_on)
        self.assertAlmostEqual(peak_zoom, 1.35, places=3)
        back_zoom, back_on = motion_lib.key_zoom_at(start + motion_lib.ZOOM_S + motion_lib.ZOOM_BACK_S, start)
        self.assertEqual(back_zoom, 1.0)
        self.assertFalse(back_on)
        plan = _hammer()
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        base = _flat()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        quiet = base.copy()
        off = motion_lib.highlight_candle(quiet, ax, plan, 0.0)
        self.assertEqual(off["zoom"], 1.0)
        self.assertFalse(off["attention"])
        self.assertEqual(off["dim"], 1.0)
        self.assertTrue(np.array_equal(np.asarray(quiet), np.asarray(base)))
        done = base.copy()
        settled = motion_lib.highlight_candle(done, ax, plan, 1.0)
        self.assertAlmostEqual(settled["zoom"], 1.35, places=3)
        self.assertTrue(settled["attention"])
        self.assertEqual(settled["dim"], 0.35)
        self.assertGreater(_near(done, motion_lib.ATTENTION), 30)
        clock = {"highlight": (8.0, 8.6), "candles": (0.0, 1.0)}
        early = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 7.5, clock)
        peak = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 8.6, clock)
        later = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 12.0, clock)
        self.assertEqual(early["highlight"]["zoom"], 1.0)
        self.assertFalse(early["highlight"]["attention"])
        self.assertAlmostEqual(peak["highlight"]["zoom"], 1.35, places=3)
        self.assertEqual(peak["highlight"]["dim"], 0.35)
        self.assertEqual(later["highlight"]["zoom"], 1.0)
        self.assertFalse(later["highlight"]["attention"])


class CounterTests(unittest.TestCase):
    def test_counter_lands_on_the_computed_target(self):
        plan = _hammer()
        quiet = _flat()
        off = motion_lib.number_counter(quiet, plan.target, 0.0)
        self.assertEqual(off["shown"], 0.0)
        self.assertNotEqual(off["shown"], plan.target)
        mid = motion_lib.number_counter(_flat(), plan.target, 0.5)
        self.assertEqual(mid["shown"], plan.target * 0.5)
        self.assertNotEqual(mid["shown"], plan.target)
        done_img = _flat()
        done = motion_lib.number_counter(done_img, plan.target, 1.0)
        self.assertGreater(_near(done_img, motion_lib.WHITE), 20)
        self.assertEqual(done["value"], plan.target)
        self.assertEqual(done["shown"], plan.target)
        self.assertEqual(done["shown"], plan.target)
        clock = {"counter": (40.0, 40.5), "candles": (0.0, 1.0)}
        before = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 39.0, clock)
        after = motion_lib.draw_plan(_flat(), plan, trade_plan.overlays(plan), 40.5, clock)
        self.assertEqual(before["counter"]["shown"], 0.0)
        self.assertEqual(after["counter"]["shown"], plan.target)
        self.assertEqual(motion_lib.window_progress(40.5, 40.0, 40.5), 1.0)


class BreakTests(unittest.TestCase):
    def test_price_breaks_the_stop_and_the_line_flashes_red(self):
        plan = _hammer()
        box, lo, hi, pad = motion_lib.motion_geometry(plan)
        base = _flat()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        y = int(round(ax.y(plan.sl)))
        x = box[0] + 80
        steady = base.copy()
        flash = base.copy()
        off = motion_lib.sl_break(base.copy(), ax, plan, 0.0, flash_on=True)
        self.assertEqual(off["progress"], 0.0)
        self.assertFalse(off["flash"])
        held = motion_lib.sl_break(steady, ax, plan, 1.0, flash_on=False)
        lit = motion_lib.sl_break(flash, ax, plan, 1.0, flash_on=True)
        self.assertEqual(held["price"], plan.sl)
        self.assertTrue(lit["flash"])
        self.assertLess(held["low"], plan.sl)
        steady_px = tuple(int(v) for v in np.asarray(steady.convert("RGB"))[y, x])
        flash_px = tuple(int(v) for v in np.asarray(flash.convert("RGB"))[y, x])
        self.assertEqual(steady_px, motion_lib.SL)
        self.assertNotEqual(flash_px, steady_px)
        self.assertGreater(flash_px[0], steady_px[0])
        y_body = int(round((ax.y(plan.sl) + ax.y(held["close"])) / 2.0))
        body = tuple(int(v) for v in np.asarray(steady.convert("RGB"))[y_body, int(round(held["cx"]))])
        self.assertEqual(body, motion_lib.BEAR)
        self.assertGreater(y_body, y)
        clock = motion_lib.clock_from(_clock_topic(), plan)
        before = motion_lib.draw_plan(_flat(), plan, [], 47.0, clock)
        after = motion_lib.draw_plan(_flat(), plan, [], 56.0, clock)
        self.assertEqual(before["progress"]["break"], 0.0)
        self.assertEqual(after["progress"]["break"], 1.0)
        self.assertEqual(after["break"]["price"], plan.sl)
        self.assertTrue(after["break"]["flash"])


class WhooshTests(unittest.TestCase):
    def test_ffmpeg_whoosh_sits_about_20db_under_the_voice(self):
        src = inspect.getsource(motion_lib.render_whoosh)
        self.assertIn("anoisesrc", src)
        self.assertIn("highpass", src)
        self.assertIn("lowpass", src)
        self.assertNotIn("bed.wav", inspect.getsource(motion_lib.mix_under))
        self.assertEqual(motion_lib.WHOOSH_UNDER_DB, 20.0)
        assemble = inspect.getsource(board_short.assemble)
        self.assertIn("motion_lib.mix_under", assemble)
        self.assertIn("bed_audio.mix_into", assemble)
        self.assertIn("loudnorm=I=-14:TP=-1.5:LRA=11", assemble)
        self.assertNotIn("bed.wav", assemble)
        sr = 44100
        t = np.arange(sr * 2, dtype=np.float64) / sr
        mono = (0.2 * np.sin(2 * np.pi * 180.0 * t)).astype(np.float32)
        stereo = np.column_stack([mono, mono])
        with tempfile.TemporaryDirectory() as tmp:
            voice = Path(tmp) / "voice.wav"
            mixed = Path(tmp) / "mixed.wav"
            motion_lib._write_wav(voice, stereo, sr)
            before = voice.read_bytes()
            info = motion_lib.mix_under(voice, [0.0, 0.80, 1.1, 1.4, 1.7, 1.9], mixed)
            self.assertEqual(voice.read_bytes(), before)
            got, got_sr = motion_lib._read_wav(mixed)
        self.assertEqual(got_sr, sr)
        self.assertEqual(info["cues"], [0.0, 0.80, 1.1, 1.4])
        self.assertLessEqual(len(info["cues"]), 4)
        self.assertIsNotNone(info["under_db"])
        self.assertGreaterEqual(info["under_db"], 19.5)
        self.assertLessEqual(info["under_db"], 21.5)
        added = got[:, 0] - mono
        self.assertGreater(float(np.max(np.abs(added[int(0.05 * sr): int(0.30 * sr)]))), 0.0)
        self.assertLess(float(np.max(np.abs(added[int(0.50 * sr): int(0.70 * sr)]))), 1e-4)
        self.assertGreater(float(np.max(np.abs(added[int(0.82 * sr): int(1.15 * sr)]))), 0.0)
        with tempfile.TemporaryDirectory() as tmp2:
            whoosh = Path(tmp2) / "whoosh.wav"
            motion_lib.render_whoosh(whoosh)
            clip, clip_sr = motion_lib._read_wav(whoosh)
            self.assertEqual(clip_sr, 44100)
            self.assertGreater(clip.shape[0], 1000)
            self.assertLess(clip.shape[0], 44100)


class PlanPathTests(unittest.TestCase):
    def _topic(self):
        import approved_bank
        topic = approved_bank.topic_from(approved_bank.by_key("pat_hammer"))
        words = [
            {"word": "ट्रेंड", "start": 8.0, "end": 8.4},
            {"word": "सपोर्ट", "start": 16.0, "end": 16.4},
            {"word": "एंट्री", "start": 24.0, "end": 24.4},
            {"word": "स्टॉप", "start": 32.0, "end": 32.4},
            {"word": "टारगेट", "start": 40.0, "end": 40.4},
            {"word": "रिस्क", "start": 41.0, "end": 41.4},
        ]
        scenes = [
            {"beat": beat, "start": span[0], "end": span[1], "index": 0}
            for beat, span in _beats().items()
        ]
        spoken = approved_bank.narration_for(topic)["beats"]
        board_short._install_scene_cards(topic, spoken, _beats(), scenes, words)
        return topic

    def test_open_has_the_rr_box_and_the_question_by_300ms(self):
        plan = _hammer()
        topic = self._topic()
        clock = motion_lib.clock_from(topic, plan)
        opening = _flat()
        state = motion_lib.draw_plan(opening, plan, trade_plan.overlays(plan), 0.0, clock)
        _assert_painted(self, opening)
        self.assertEqual(tuple(int(v) for v in np.asarray(opening)[10, 10]), motion_lib.BG)
        self.assertEqual(state["progress"]["rr"], 1.0)
        self.assertTrue(state["rr"]["shown"])
        self.assertEqual(state["rr"]["text"], "RR 1:2")
        self.assertGreater(max(state["candles"]), 0.99)
        self.assertLess(state["progress"]["candles"], 1.0)
        self.assertEqual(state["title"]["progress"], 0.0)
        titled = motion_lib.draw_plan(_flat(), plan, [], 0.30, clock)
        self.assertEqual(titled["title"]["progress"], 1.0)
        gone = motion_lib.draw_plan(_flat(), plan, [], 3.0, clock)
        self.assertEqual(gone["progress"]["rr"], 0.0)
        self.assertFalse(gone["rr"]["shown"])
        redraw = motion_lib.draw_plan(_flat(), plan, [], _beats()["target"][0] + motion_lib.REVEAL_S, clock)
        self.assertEqual(redraw["progress"]["rr"], 1.0)
        left = _flat()
        right = _flat()
        motion_lib.draw_plan(left, plan, trade_plan.overlays(plan), 1.0, clock)
        motion_lib.draw_plan(right, plan, trade_plan.overlays(plan), 5.0, clock)
        delta = float(np.abs(np.asarray(left, dtype=np.int16) - np.asarray(right, dtype=np.int16)).mean())
        self.assertGreaterEqual(delta, 0.8, delta)
        same_scale = motion_lib.motion_geometry(plan)
        self.assertEqual(motion_lib.motion_geometry(plan), same_scale)
        frame = trade_plan.paint_frame(topic, "hook", 0, 1, "एंट्री 118")
        self.assertIsNone(frame.motion_state)
        early = frame.motion_at(0.0)
        self.assertEqual(early.motion_state["progress"]["rr"], 1.0)
        self.assertFalse(any(layer.get("role") == "caption" for layer in early.text_layers))
        question = frame.motion_at(0.30 / 8.0)
        captions = [layer for layer in question.text_layers if layer.get("role") == "caption"]
        self.assertTrue(captions)
        self.assertTrue(any("हैमर" in layer["text"] or "कैंडल" in layer["text"] for layer in captions))
        for layer in captions:
            self.assertGreaterEqual(layer["size"], 64)
            self.assertLessEqual(layer["size"], 72)
            self.assertLessEqual(layer["box"][3], safe_area.SUBTITLE_LIMIT + 1)
        self.assertEqual(question.motion_state["title"]["text"], topic["hook_line"])

    def test_hook_shows_ghost_levels_and_the_plot_is_about_60_percent(self):
        import scene_pace
        plan = _hammer()
        topic = self._topic()
        clock = motion_lib.clock_from(topic, plan)
        opening = _flat()
        state = motion_lib.draw_plan(opening, plan, trade_plan.overlays(plan), 0.0, clock)
        self.assertEqual(state["progress"]["rr"], 1.0)
        self.assertEqual(state["progress"]["entry"], 0.0)
        self.assertFalse(state["levels"]["entry"]["drawn"])
        self.assertFalse(state["levels"]["sl"]["drawn"])
        self.assertFalse(state["levels"]["target"]["drawn"])
        self.assertGreater(_near(opening, motion_lib.ENTRY), 40)
        self.assertGreater(_near(opening, motion_lib.SL), 40)
        self.assertGreater(_near(opening, motion_lib.TARGET), 40)
        rgb = np.asarray(opening.convert("RGB"))
        bright = rgb.max(axis=2) > 80
        rows = np.where(bright[:1380].any(axis=1))[0]
        height = (int(rows[-1]) - int(rows[0])) / float(rgb.shape[0])
        self.assertGreaterEqual(height, 0.55, height)
        self.assertLessEqual(height, 0.66, height)
        self.assertLessEqual(scene_pace.empty_row_fraction(opening), scene_pace.EMPTY_MAX_FRAC)
        later = _flat()
        motion_lib.draw_plan(later, plan, trade_plan.overlays(plan), 4.0, clock)
        self.assertLessEqual(scene_pace.empty_row_fraction(later), scene_pace.EMPTY_MAX_FRAC)
        gone = _flat()
        motion_lib.draw_plan(gone, plan, [], 3.0, clock)
        self.assertEqual(motion_lib.rr_progress(3.0, clock), 0.0)
        self.assertLess(_near(gone, motion_lib.ENTRY), 10)

    def test_all_eight_beats_use_the_library_and_keep_the_chart_band(self):
        topic = self._topic()
        plan = trade_plan.plan_for("pat_hammer")
        reveals = motion_lib.unique_reveals([
            type("F", (), {"motion_reveals": motion_lib.reveal_times(topic)})(),
            type("F", (), {"motion_reveals": motion_lib.reveal_times(topic)})(),
        ])
        self.assertEqual(reveals, [0.0, 8.0, 24.0, 40.0])
        self.assertNotIn(16.0, reveals)
        self.assertNotIn(32.0, reveals)
        self.assertLessEqual(len(reveals), 4)
        for beat in trade_plan.BEATS:
            frame = trade_plan.paint_frame(topic, beat, 0, 1, "एंट्री 118")
            self.assertTrue(callable(frame.motion_at))
            self.assertTrue(frame.motion_reveals)
            region = frame.chart_region
            height = (region[3] - region[1]) / frame.size[1]
            self.assertGreaterEqual(height, 0.60)
            self.assertLessEqual(height, 0.66)
            _assert_painted(self, frame)
            done = frame.motion_at(1.0)
            _assert_painted(self, done)
            self.assertGreaterEqual((done.chart_region[3] - done.chart_region[1]) / done.size[1], 0.60)
            self.assertLessEqual(done.chart_region[3] - done.chart_region[1], frame.size[1] * 0.66)
            if beat == "entry":
                early = frame.motion_at(0.0)
                self.assertEqual(early.motion_state["progress"]["entry"], 0.0)
                self.assertEqual(early.motion_state["progress"]["rr"], 0.0)
                self.assertEqual(early.motion_state["levels"]["entry"]["price"], plan.entry)
                self.assertEqual(done.motion_state["levels"]["entry"]["price"], 118.0)
                self.assertEqual(done.motion_state["progress"]["entry"], 1.0)
            if beat == "target":
                self.assertEqual(done.motion_state["counter"]["shown"], plan.target)
                self.assertEqual(done.motion_state["rr"]["risk"], plan.risk)
                self.assertEqual(done.motion_state["rr"]["reward"], plan.reward)
                self.assertEqual(done.motion_state["rr"]["rr"], plan.rr_text)
                self.assertEqual(done.motion_state["levels"]["sl"]["price"], 98.0)
                self.assertEqual(done.motion_state["levels"]["target"]["price"], 158.0)
            if beat == "invalidation":
                self.assertEqual(done.motion_state["progress"]["break"], 1.0)
                self.assertEqual(done.motion_state["break"]["price"], plan.sl)
            if beat == "context":
                text = " ".join(
                    layer.get("text") or "" for layer in done.text_layers if layer.get("role") == "caption"
                )
                self.assertIn("सपोर्ट 100 पर वैलिड", text)
                self.assertIn("साइडवेज", text)
                captions = [layer for layer in done.text_layers if layer.get("role") == "caption"]
                self.assertLessEqual(len(captions), 2)
                self.assertFalse(any("TARGET" in (layer.get("text") or "") for layer in done.text_layers))
            if beat == "cta":
                self.assertEqual(done.motion_state["progress"]["candles"], 1.0)
                self.assertEqual(done.motion_state["progress"]["rr"], 1.0)
                sources = [layer for layer in done.text_layers if layer.get("role") == "source"]
                self.assertEqual(len(sources), 1)
                self.assertEqual(sources[0]["text"], trade_plan.ON_SCREEN_DISCLAIMER)
                self.assertGreaterEqual(sources[0]["size"], 36)
                blob = " ".join(layer.get("text") or "" for layer in done.text_layers)
                self.assertNotIn(trade_plan.DISCLAIMER, blob)
                self.assertNotIn(board_short.DISCLAIMER, blob)
                ass = board_short.build_ass(topic, _beats(), {}, topic["hook_line"], "")
                self.assertNotIn(board_short.DISCLAIMER, ass)
                self.assertNotIn(trade_plan.DISCLAIMER, ass)

    def test_every_approved_plan_settles_on_its_computed_numbers(self):
        clock = {
            "candles": (0.0, 1.0),
            "trend": (1.0, 1.5),
            "sr": (1.5, 2.0),
            "entry": (2.0, 2.5),
            "sl": (2.5, 3.0),
            "target": (3.0, 3.5),
            "rr": (3.5, 4.0),
            "highlight": (1.0, 2.0),
            "counter": (3.0, 4.0),
            "break": (4.0, 4.4),
        }
        for key in trade_plan.KEYS:
            plan = trade_plan.plan_for(key)
            img = _flat()
            state = motion_lib.draw_plan(img, plan, trade_plan.overlays(plan), 10.0, clock)
            _assert_painted(self, img)
            self.assertEqual(state["levels"]["entry"]["price"], plan.entry, key)
            self.assertEqual(state["levels"]["sl"]["price"], plan.sl, key)
            self.assertEqual(state["levels"]["target"]["price"], plan.target, key)
            self.assertEqual(state["rr"]["risk"], plan.risk, key)
            self.assertEqual(state["rr"]["reward"], plan.reward, key)
            self.assertEqual(state["rr"]["rr"], plan.rr_text, key)
            self.assertEqual(state["rr"]["text"], f"RR {plan.rr_text}", key)
            self.assertGreater(state["rr"]["reward_px"], state["rr"]["risk_px"], key)
            self.assertEqual(state["counter"]["shown"], plan.target, key)
            self.assertEqual(state["highlight"]["zoom"], 1.0, key)
            self.assertFalse(state["highlight"]["attention"], key)
            self.assertEqual(state["progress"]["candles"], 1.0, key)
            self.assertEqual(state["progress"]["break"], 1.0, key)


class NiftyTests(unittest.TestCase):
    def test_data_short_keeps_its_ohlc_and_skips_the_plan_clock(self):
        import nifty_day
        import scene_frames

        now = datetime(2026, 10, 8, 15, 50, tzinfo=IST)
        prices = (24880.0, 24940.0, 25010.0, 25002.95, 25123.45)
        bars = []
        for offset, price in enumerate(prices):
            day = (now - timedelta(days=len(prices) - 1 - offset)).date()
            bars.append({
                "ts": datetime(day.year, day.month, day.day, 15, 35, tzinfo=IST),
                "open": price - 10,
                "high": price + 80,
                "low": price - 70,
                "close": price,
            })
        session, reason = nifty_day.session_from_bars(bars, now, reply="")
        self.assertEqual(reason, "")
        topic = {
            "key": "aaj_ka_nifty",
            "_nifty": session,
            "_live_short": True,
            "_live_beats": "plan",
            "_approved_bank": True,
            "_spoken_beats": session["narration"]["beats"],
        }
        self.assertFalse(trade_plan.is_plan_topic(topic))
        frame = scene_frames.frames_for(topic, "matlab", 1, {"mode": "schematic"})[0][0]
        self.assertFalse(getattr(frame, "motion_reveals", None))
        self.assertFalse(getattr(frame, "motion_state", None))
        region = frame.chart_region
        height = (region[3] - region[1]) / frame.size[1]
        self.assertGreaterEqual(height, 0.60)
        self.assertLessEqual(height, 0.66)
        _assert_painted(self, frame)
        card = scene_frames.frame_card_text(frame)
        self.assertIn("25100.00", card)
        rgb = np.asarray(frame.convert("RGB").crop(K.BOARD), dtype=np.int16)
        red = np.abs(rgb - np.array(K.RED, dtype=np.int16)).sum(axis=2) <= 18
        green = np.abs(rgb - np.array(K.GREEN, dtype=np.int16)).sum(axis=2) <= 18
        self.assertGreater(int(red.sum() + green.sum()), 40)
