"""Motion components: not blank, on the word clock, numbers from the plan."""
import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import board_kit as K
import board_short
import motion_lib
import trade_plan

IST = timezone(timedelta(hours=5, minutes=30))


def _board():
    img = K.background()
    K.panel(img)
    trade_plan._grid(img)
    return img


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


def _geometry(plan, shown=None):
    return trade_plan._chart_geometry(plan, shown if shown is not None else trade_plan.overlays(plan))


class EasingTests(unittest.TestCase):
    def test_window_is_zero_before_and_one_after(self):
        self.assertEqual(motion_lib.window_progress(9.9, 10.0, 11.0), 0.0)
        self.assertEqual(motion_lib.window_progress(11.0, 10.0, 11.0), 1.0)
        self.assertEqual(motion_lib.window_progress(12.0, 10.0, 11.0), 1.0)
        mid = motion_lib.window_progress(10.5, 10.0, 11.0)
        self.assertGreater(mid, 0.0)
        self.assertLess(mid, 1.0)


class CandleTests(unittest.TestCase):
    def test_bodies_grow_and_the_settled_frame_matches_the_chart(self):
        plan = _hammer()
        box, lo, hi, pad = _geometry(plan)
        empty = _board()
        motion_lib.candle_build(empty, box, plan.ohlc, lo, hi, 0.0, pad)
        _assert_painted(self, empty)
        self.assertEqual(_near(empty, K.RED) + _near(empty, K.GREEN), 0)
        half = _board()
        motion_lib.candle_build(half, box, plan.ohlc, lo, hi, 0.5, pad)
        _assert_painted(self, half)
        full = _board()
        motion_lib.candle_build(full, box, plan.ohlc, lo, hi, 1.0, pad)
        plain = _board()
        K.candles(plain, box, plan.ohlc, lo, hi, grid=False, width_frac=0.62, pad=pad)
        self.assertTrue(np.array_equal(np.asarray(full), np.asarray(plain)))
        self.assertGreater(_near(full, K.RED) + _near(full, K.GREEN), _near(half, K.RED) + _near(half, K.GREEN))
        clock = {"candles": (2.0, 4.0)}
        before = motion_lib.draw_plan(_board(), plan, [], 1.0, clock)
        after = motion_lib.draw_plan(_board(), plan, [], 4.0, clock)
        self.assertEqual(before["progress"]["candles"], 0.0)
        self.assertEqual(after["progress"]["candles"], 1.0)


class TrendTests(unittest.TestCase):
    def test_arrow_draws_in_and_settles_on_the_same_tip(self):
        plan = _hammer()
        box, lo, hi, pad = _geometry(plan)
        base = _board()
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
            K.YELLOW, w=8, head=26,
        )
        _assert_painted(self, done)
        self.assertTrue(np.array_equal(np.asarray(done), np.asarray(reference)))
        self.assertGreater(_near(done, K.YELLOW), _near(partial, K.YELLOW))
        self.assertGreater(_near(partial, K.YELLOW), 0)
        clock = {"trend": (8.0, 8.6), "candles": (0.0, 1.0)}
        early = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 7.0, clock)
        late = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 9.0, clock)
        self.assertEqual(early["progress"]["trend"], 0.0)
        self.assertEqual(late["progress"]["trend"], 1.0)


class BandTests(unittest.TestCase):
    def test_band_fades_in_on_the_computed_price(self):
        plan = _hammer()
        box, lo, hi, pad = _geometry(plan)
        base = _board()
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
        self.assertGreater(int(after_px[2]), int(before_px[2]) + 20)
        self.assertLess(int(after_px[2]), K.BLUE[2])
        clock = {"sr": (16.0, 16.8), "candles": (0.0, 1.0)}
        early = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 15.0, clock)
        late = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 17.0, clock)
        self.assertEqual(early["progress"]["sr"], 0.0)
        self.assertEqual(late["progress"]["sr"], 1.0)
        self.assertEqual(late["levels"]["sr"]["price"], plan.sr)


class LevelTests(unittest.TestCase):
    def test_lines_draw_left_to_right_with_the_plan_prices(self):
        plan = _hammer()
        self.assertEqual(plan.entry, 118.0)
        self.assertEqual(plan.sl, 100.0)
        self.assertEqual(plan.target, 154.0)
        box, lo, hi, pad = _geometry(plan)
        base = _board()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        y = ax.y(plan.entry)
        label = f"ENTRY {trade_plan.fmt(plan.entry)}"
        hidden = base.copy()
        info = motion_lib.level_line(hidden, box, y, K.YELLOW, label, "right", 0.0)
        self.assertFalse(info["drawn"])
        self.assertEqual(_near(hidden, K.YELLOW), _near(base, K.YELLOW))
        stub = base.copy()
        stub_info = motion_lib.level_line(stub, box, y, K.YELLOW, label, "right", 0.3)
        self.assertIsNone(stub_info["price_label"])
        full = base.copy()
        full_info = motion_lib.level_line(full, box, y, K.YELLOW, label, "right", 1.0)
        reference = base.copy()
        K.dashed(reference, box[0] + 24, box[2] - 24, y, K.YELLOW, label, size=22, label_side="right")
        self.assertEqual(full_info["price_label"], label)
        self.assertIn("118", full_info["price_label"])
        self.assertTrue(np.array_equal(np.asarray(full), np.asarray(reference)))
        _assert_painted(self, full)
        self.assertGreater(_near(full, K.YELLOW), _near(stub, K.YELLOW))
        clock = {
            "candles": (0.0, 1.0),
            "entry": (24.0, 24.7),
            "sl": (32.0, 32.7),
            "target": (40.0, 40.7),
        }
        shown = trade_plan.overlays(plan)
        early = motion_lib.draw_plan(_board(), plan, shown, 24.0, clock)
        late = motion_lib.draw_plan(_board(), plan, shown, 41.0, clock)
        self.assertEqual(early["progress"]["entry"], 0.0)
        self.assertFalse(early["levels"]["entry"]["drawn"])
        self.assertEqual(early["levels"]["entry"]["price"], plan.entry)
        self.assertEqual(late["progress"]["entry"], 1.0)
        self.assertEqual(late["progress"]["sl"], 1.0)
        self.assertEqual(late["progress"]["target"], 1.0)
        self.assertEqual(late["levels"]["sl"]["price"], plan.sl)
        self.assertEqual(late["levels"]["target"]["price"], plan.target)
        self.assertIn("100", late["levels"]["sl"]["label"])
        self.assertIn("154", late["levels"]["target"]["label"])


class RiskTests(unittest.TestCase):
    def test_box_uses_the_computed_risk_reward_and_ratio(self):
        plan = _hammer()
        self.assertEqual(plan.risk, 18.0)
        self.assertEqual(plan.reward, 36.0)
        self.assertEqual(plan.rr_text, "1:2")
        readout = motion_lib.rr_readout(plan)
        self.assertEqual(readout["risk"], 18.0)
        self.assertEqual(readout["reward"], 36.0)
        self.assertEqual(readout["rr"], "1:2")
        self.assertIn("18", readout["text"])
        self.assertIn("36", readout["text"])
        self.assertIn("1:2", readout["text"])
        quiet = K.background()
        hidden = motion_lib.risk_reward_box(quiet, plan, 0.0)
        self.assertFalse(hidden["shown"])
        rgb = np.asarray(quiet.convert("RGB"))
        self.assertNotEqual(tuple(int(v) for v in rgb[110, 200]), K.RED)
        self.assertNotEqual(tuple(int(v) for v in rgb[110, 700]), K.GREEN)
        early = K.background()
        motion_lib.risk_reward_box(early, plan, 0.25)
        early_rgb = np.asarray(early.convert("RGB"))
        self.assertNotEqual(tuple(int(v) for v in early_rgb[110, 700]), K.GREEN)
        done = K.background()
        shown = motion_lib.risk_reward_box(done, plan, 1.0)
        reference = K.background()
        trade_plan._rr_box(reference, plan)
        done_rgb = np.asarray(done.convert("RGB"))
        ref_rgb = np.asarray(reference.convert("RGB"))
        self.assertEqual(tuple(int(v) for v in done_rgb[110, 200]), K.RED)
        self.assertEqual(tuple(int(v) for v in done_rgb[110, 700]), K.GREEN)
        self.assertTrue(np.array_equal(done_rgb[96:150, 48:900], ref_rgb[96:150, 48:900]))
        _assert_painted(self, done)
        self.assertEqual(shown["risk"], plan.risk)
        self.assertEqual(shown["reward"], plan.reward)
        self.assertEqual(shown["rr"], plan.rr_text)
        clock = {"rr": (41.0, 41.6), "candles": (0.0, 1.0)}
        before = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 40.0, clock)
        after = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 42.0, clock)
        self.assertEqual(before["progress"]["rr"], 0.0)
        self.assertFalse(before["rr"]["shown"])
        self.assertEqual(after["progress"]["rr"], 1.0)
        self.assertEqual(after["rr"]["risk"], plan.risk)
        self.assertEqual(after["rr"]["reward"], plan.reward)
        self.assertEqual(after["rr"]["rr"], "1:2")


class HighlightTests(unittest.TestCase):
    def test_ring_settles_at_28_by_40_and_the_zoom_is_only_while_it_draws(self):
        plan = _hammer()
        box, lo, hi, pad = _geometry(plan)
        base = _board()
        ax = motion_lib.candle_build(base, box, plan.ohlc, lo, hi, 1.0, pad)
        quiet = base.copy()
        off = motion_lib.highlight_candle(quiet, ax, plan, 0.0)
        self.assertEqual(off["zoom"], 1.0)
        self.assertEqual(off["rx"], 0.0)
        self.assertTrue(np.array_equal(np.asarray(quiet), np.asarray(base)))
        mid_img = base.copy()
        mid = motion_lib.highlight_candle(mid_img, ax, plan, 0.5)
        self.assertGreater(mid["zoom"], 1.0)
        self.assertLess(mid["zoom"], 1.08)
        self.assertLess(mid["rx"], 28.0)
        _assert_painted(self, mid_img)
        done = base.copy()
        settled = motion_lib.highlight_candle(done, ax, plan, 1.0)
        candle = plan.ohlc[plan.focus]
        reference = base.copy()
        K.ring(
            reference, ax.x(plan.focus),
            (ax.y(candle[1]) + ax.y(candle[2])) / 2.0, 28, 40, K.YELLOW,
        )
        self.assertEqual(settled["zoom"], 1.0)
        self.assertEqual(settled["rx"], 28.0)
        self.assertEqual(settled["ry"], 40.0)
        self.assertTrue(np.array_equal(np.asarray(done), np.asarray(reference)))
        self.assertGreater(_near(done, K.YELLOW), 30)
        clock = {"highlight": (8.0, 9.2), "candles": (0.0, 1.0)}
        early = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 7.5, clock)
        late = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 9.2, clock)
        self.assertEqual(early["progress"]["highlight"], 0.0)
        self.assertEqual(early["highlight"]["rx"], 0.0)
        self.assertEqual(late["progress"]["highlight"], 1.0)
        self.assertEqual(late["highlight"]["rx"], 28.0)
        self.assertEqual(late["highlight"]["zoom"], 1.0)


class CounterTests(unittest.TestCase):
    def test_counter_lands_on_the_computed_target(self):
        plan = _hammer()
        quiet = _board()
        off = motion_lib.number_counter(quiet, plan.target, 0.0)
        self.assertEqual(off["shown"], 0.0)
        self.assertNotEqual(off["shown"], plan.target)
        mid = motion_lib.number_counter(_board(), plan.target, 0.5)
        self.assertEqual(mid["shown"], plan.target * 0.5)
        self.assertNotEqual(mid["shown"], plan.target)
        done_img = _board()
        done = motion_lib.number_counter(done_img, plan.target, 1.0)
        _assert_painted(self, done_img)
        self.assertEqual(done["value"], plan.target)
        self.assertEqual(done["shown"], plan.target)
        self.assertEqual(done["shown"], 154.0)
        clock = {"counter": (40.0, 40.9), "candles": (0.0, 1.0)}
        before = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 39.0, clock)
        after = motion_lib.draw_plan(_board(), plan, trade_plan.overlays(plan), 41.0, clock)
        self.assertEqual(before["counter"]["shown"], 0.0)
        self.assertEqual(after["counter"]["shown"], plan.target)


class WhooshTests(unittest.TestCase):
    def test_ffmpeg_whoosh_sits_about_24db_under_the_voice(self):
        src = inspect.getsource(motion_lib.render_whoosh)
        self.assertIn("anoisesrc", src)
        self.assertIn("highpass", src)
        self.assertIn("lowpass", src)
        self.assertNotIn("bed.wav", inspect.getsource(motion_lib.mix_under))
        self.assertEqual(motion_lib.WHOOSH_UNDER_DB, 24.0)
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
            info = motion_lib.mix_under(voice, [0.0, 0.80], mixed)
            self.assertEqual(voice.read_bytes(), before)
            got, got_sr = motion_lib._read_wav(mixed)
        self.assertEqual(got_sr, sr)
        self.assertEqual(info["cues"], [0.80])
        self.assertIsNotNone(info["under_db"])
        self.assertGreaterEqual(info["under_db"], 23.5)
        self.assertLessEqual(info["under_db"], 25.5)
        added = got[:, 0] - mono
        self.assertLess(float(np.max(np.abs(added[: int(0.70 * sr)]))), 1e-4)
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
        beat_times = {
            beat: (index * 8.0, index * 8.0 + 8.0)
            for index, beat in enumerate(trade_plan.BEATS)
        }
        scenes = [
            {"beat": beat, "start": span[0], "end": span[1], "index": 0}
            for beat, span in beat_times.items()
        ]
        spoken = approved_bank.narration_for(topic)["beats"]
        board_short._install_scene_cards(topic, spoken, beat_times, scenes, words)
        return topic

    def test_all_eight_beats_use_the_library_and_keep_the_chart_band(self):
        topic = self._topic()
        plan = trade_plan.plan_for("pat_hammer")
        reveals = motion_lib.unique_reveals([
            type("F", (), {"motion_reveals": motion_lib.reveal_times(topic)})(),
            type("F", (), {"motion_reveals": motion_lib.reveal_times(topic)})(),
        ])
        for cue in (8.0, 16.0, 24.0, 32.0, 40.0):
            self.assertIn(cue, reveals)
        self.assertNotIn(0.0, reveals)
        expect = {
            "hook": "candles",
            "pehchan": "trend",
            "context": "sr",
            "entry": "entry",
            "sl": "sl",
            "target": "target",
            "invalidation": "target",
            "cta": "rr",
        }
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
            self.assertEqual(done.motion_state["progress"][expect[beat]], 1.0)
            if beat == "hook":
                early = frame.motion_at(0.0)
                self.assertEqual(early.motion_state["progress"]["candles"], 0.0)
                _assert_painted(self, early)
            if beat == "entry":
                early = frame.motion_at(0.0)
                self.assertEqual(early.motion_state["progress"]["entry"], 0.0)
                self.assertEqual(early.motion_state["levels"]["entry"]["price"], plan.entry)
                self.assertEqual(done.motion_state["levels"]["entry"]["price"], 118.0)
            if beat == "target":
                self.assertEqual(done.motion_state["counter"]["shown"], plan.target)
                self.assertEqual(done.motion_state["rr"]["risk"], plan.risk)
                self.assertEqual(done.motion_state["rr"]["reward"], plan.reward)
                self.assertEqual(done.motion_state["rr"]["rr"], plan.rr_text)
                self.assertEqual(done.motion_state["levels"]["sl"]["price"], 100.0)
                self.assertEqual(done.motion_state["levels"]["target"]["price"], 154.0)

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
        }
        for key in trade_plan.KEYS:
            plan = trade_plan.plan_for(key)
            img = _board()
            state = motion_lib.draw_plan(img, plan, trade_plan.overlays(plan), 10.0, clock)
            _assert_painted(self, img)
            self.assertEqual(state["levels"]["entry"]["price"], plan.entry, key)
            self.assertEqual(state["levels"]["sl"]["price"], plan.sl, key)
            self.assertEqual(state["levels"]["target"]["price"], plan.target, key)
            self.assertEqual(state["rr"]["risk"], plan.risk, key)
            self.assertEqual(state["rr"]["reward"], plan.reward, key)
            self.assertEqual(state["rr"]["rr"], plan.rr_text, key)
            self.assertEqual(state["counter"]["shown"], plan.target, key)
            self.assertEqual(state["highlight"]["rx"], 28.0, key)
            self.assertEqual(state["highlight"]["zoom"], 1.0, key)
            self.assertEqual(state["progress"]["candles"], 1.0, key)


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
