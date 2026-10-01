"""Ahead-of-slot scheduling: one unfilled slot, no next-day rollover, odd-minute crons."""
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import slot_guard as SG

IST = SG.IST


class CronTests(unittest.TestCase):
    def test_three_early_triggers_per_slot_on_odd_minutes(self):
        by_slot = {}
        for cron, slot in SG.PUBLISH_CRONS:
            minute = int(cron.split()[0])
            self.assertNotIn(minute, (0, 30), cron)
            by_slot.setdefault(slot, []).append(SG.cron_lead_minutes(cron, slot))
        self.assertEqual(set(by_slot), set(SG.SLOTS))
        for slot, leads in by_slot.items():
            self.assertEqual(len(leads), 3, slot)
            leads = sorted(leads)
            self.assertGreaterEqual(leads[-1], 180, slot)   # about 3–4h before
            self.assertGreaterEqual(leads[-2], 120, slot)   # about 2–3h before
            self.assertGreaterEqual(leads[0], 60, slot)     # backup still before the slot
            self.assertLess(leads[0], 120, slot)

    def test_health_check_is_before_the_earliest_build(self):
        for cron, _check, slot, lead in SG.HEALTH_SCHEDULE:
            self.assertNotIn(int(cron.split()[0]), (0, 30))
            earliest = max(SG.cron_lead_minutes(c, s) for c, s in SG.PUBLISH_CRONS if s == slot)
            self.assertGreater(lead, earliest)
            self.assertEqual(lead, SG.cron_lead_minutes(cron, slot))

    def test_watch_crons_are_just_after_the_slot(self):
        for cron, slot in SG.WATCH_CRONS:
            self.assertNotIn(int(cron.split()[0]), (0, 30))
            after = (SG.cron_lead_minutes(cron, slot) - 24 * 60) % (24 * 60)
            # lead wraps to a large number when the cron is after the slot;
            # minutes-after is 1440 - lead when lead is the wrap.
            minutes_after = (24 * 60 - SG.cron_lead_minutes(cron, slot)) % (24 * 60)
            self.assertGreater(minutes_after, 0)
            self.assertLess(minutes_after, 90, (cron, slot, minutes_after))
            self.assertIsNotNone(after)

    def test_oct2_morning_crons_are_before_0300_utc(self):
        crons = [c for c, slot in SG.PUBLISH_CRONS if slot == "08:30"]
        self.assertIn("17 0 * * *", crons)
        self.assertIn("23 1 * * *", crons)
        self.assertIn("47 23 * * *", crons)
        for cron in crons:
            self.assertLess(SG.cron_lead_minutes(cron, "08:30"), 6 * 60)


class NextSlotTests(unittest.TestCase):
    def test_early_trigger_selects_oct2_0830(self):
        # 00:17 UTC = 05:47 IST on 2 Oct, the trigger that can still land before 03:00 UTC.
        now = datetime(2026, 10, 2, 5, 47, tzinfo=IST)
        choice = SG.next_unfilled(now, [])
        self.assertEqual(choice["slot"], "08:30")
        self.assertEqual(choice["date_ist"], "2026-10-02")
        self.assertEqual(choice["publish_at"], "2026-10-02T03:00:00Z")

    def test_2347_utc_also_selects_oct2_0830(self):
        now = datetime(2026, 10, 2, 5, 17, tzinfo=IST)  # 23:47 UTC 1 Oct
        choice = SG.next_unfilled(now, [])
        self.assertEqual(choice["date_ist"], "2026-10-02")
        self.assertEqual(choice["slot"], "08:30")

    def test_same_ledger_is_idempotent_and_a_filled_slot_is_skipped(self):
        now = datetime(2026, 10, 2, 5, 47, tzinfo=IST)
        first = SG.next_unfilled(now, [])
        again = SG.next_unfilled(now, [])
        self.assertEqual(first, again)
        filled = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": "S5KRdVkEJf0"}]
        self.assertIsNone(SG.next_unfilled(now, filled))

    def test_instagram_only_row_counts_as_filled(self):
        now = datetime(2026, 10, 2, 5, 47, tzinfo=IST)
        entries = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": None, "platform": "instagram"}]
        self.assertIsNone(SG.next_unfilled(now, entries))

    def test_late_1600_run_does_not_steal_tomorrow(self):
        # 17:04 UTC = 22:34 IST. The old code scheduled the next day's 16:00.
        now = datetime(2026, 10, 1, 22, 34, tzinfo=IST)
        self.assertIsNone(SG.next_unfilled(now, []))
        self.assertIsNone(SG.scheduled_publish_at("16:00", now))
        self.assertIsNone(SG.scheduled_publish_at("16:00", now, date_ist="2026-10-01"))

    def test_late_2100_run_does_not_push_the_video_to_the_next_2100(self):
        # 20:24 UTC = 01:54 IST the next morning. Old code targeted the next 21:00.
        now = datetime(2026, 10, 2, 1, 54, tzinfo=IST)
        choice = SG.next_unfilled(now, [])
        self.assertNotEqual((choice or {}).get("slot"), "21:00")
        # 08:30 is 6h36m away, outside the 6h window, so this late run skips.
        self.assertIsNone(choice)
        self.assertIsNone(SG.scheduled_publish_at("21:00", now, date_ist="2026-10-01"))

    def test_slot_inside_the_window_is_not_replaced_by_a_later_one(self):
        now = datetime(2026, 10, 2, 12, 11, tzinfo=IST)  # 06:41 UTC, 16:00 early trigger
        choice = SG.next_unfilled(now, [])
        self.assertEqual(choice["slot"], "16:00")
        self.assertEqual(choice["date_ist"], "2026-10-02")
        filled = [{"date_ist": "2026-10-02", "slot": "16:00", "video_id": "abc"}]
        self.assertIsNone(SG.next_unfilled(now, filled))

    def test_too_close_to_publish_is_not_selected(self):
        now = datetime(2026, 10, 2, 8, 15, tzinfo=IST)  # 15 min before 08:30, inside the 20 min buffer
        self.assertIsNone(SG.next_unfilled(now, []))
        self.assertIsNone(SG.scheduled_publish_at("08:30", now, date_ist="2026-10-02"))

    def test_cli_prints_one_slot_and_skips_when_filled(self):
        now = datetime(2026, 10, 2, 5, 47, tzinfo=IST)
        buf = io.StringIO()
        with mock.patch.object(SG, "_now", return_value=now), \
             mock.patch.object(SG, "_read", return_value=[]), \
             mock.patch("sys.stdout", buf):
            SG.main(["--next"])
        text = buf.getvalue()
        self.assertIn("slot=08:30", text)
        self.assertIn("date_ist=2026-10-02", text)
        self.assertIn("publish_at=2026-10-02T03:00:00Z", text)
        self.assertIn("taken=false", text)

        buf = io.StringIO()
        filled = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": "abc"}]
        with mock.patch.object(SG, "_now", return_value=now), \
             mock.patch.object(SG, "_read", return_value=filled), \
             mock.patch("sys.stdout", buf):
            SG.main(["--next"])
        text = buf.getvalue()
        self.assertIn("taken=true", text)
        self.assertIn("slot=", text)
        self.assertNotIn("slot=08:30", text)

    def test_publish_date_env_wins_over_the_clock_rollover(self):
        now = datetime(2026, 10, 1, 22, 34, tzinfo=IST)
        with mock.patch.dict("os.environ", {"PUBLISH_DATE_IST": "2026-10-02", "PUBLISH_AT_UTC": ""}, clear=False):
            self.assertEqual(SG.target_date("16:00", now), "2026-10-02")
        with mock.patch.dict("os.environ", {"PUBLISH_DATE_IST": "", "PUBLISH_AT_UTC": "2026-10-02T03:00:00Z"}, clear=False):
            self.assertEqual(SG.target_date("08:30", now), "2026-10-02")


class WatchFillTests(unittest.TestCase):
    def test_empty_past_slot_does_not_request_an_extra_post_when_nothing_is_ahead(self):
        import slot_watch
        now = slot_watch.datetime(2026, 10, 1, 21, 49, tzinfo=slot_watch.IST)
        entries = [{"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc", "format": "short"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "published.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            gout = Path(d) / "gha"
            env = {"SLOT_CHECK": "21:00", "GITHUB_OUTPUT": str(gout)}
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict("os.environ", env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt:
                dt.now.return_value = now
                slot_watch.main()
            text = gout.read_text(encoding="utf-8")
            self.assertIn("missing=true", text)
            self.assertIn("fill=false", text)


if __name__ == "__main__":
    unittest.main()
