"""Ahead-of-slot scheduling: one unfilled slot, no rollover, spread-out crons."""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import credit_log
import publish
import run_pipeline
import slot_guard as SG

IST = SG.IST
ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
HORIZON = int(SG.MAX_LOOKAHEAD.total_seconds() // 60)


class CronTests(unittest.TestCase):
    def test_eight_odd_minute_triggers_outside_the_busy_window(self):
        self.assertEqual(SG.MAX_LOOKAHEAD.total_seconds(), 14 * 3600)
        self.assertEqual(len(SG.PUBLISH_CRONS), 8)
        self.assertEqual(len({cron for cron, _slot in SG.PUBLISH_CRONS}), 8)
        for cron, _slot in SG.PUBLISH_CRONS:
            minute = int(cron.split()[0])
            self.assertNotIn(minute, (0, 30), cron)
            self.assertEqual(minute % 2, 1, cron)
            self.assertFalse(SG.cron_in_busy_window(cron), cron)
        publish = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        for cron, _slot in SG.PUBLISH_CRONS:
            self.assertIn(f'"{cron}"', publish)
        for dropped in ('"47 23 * * *"', '"17 0 * * *"', '"23 1 * * *"'):
            self.assertNotIn(dropped, publish)

    def test_each_slot_has_three_chances_at_least_two_hours_ahead(self):
        for slot in SG.SLOTS:
            leads = sorted(lead for _cron, lead in SG.covering_leads(slot, min_minutes=120))
            self.assertGreaterEqual(len(leads), 3, (slot, leads))
            self.assertGreaterEqual(leads[0], 120, slot)
        # 21:00 is the night slot. At least one chance is earlier in the day,
        # not only in the last couple of hours before 15:30 UTC.
        night = [lead for _cron, lead in SG.covering_leads("21:00", min_minutes=120)]
        self.assertTrue(any(lead >= 6 * 60 for lead in night), night)
        # 08:30 is armed the previous afternoon/evening, never in the crush.
        morning = [cron for cron, _lead in SG.covering_leads("08:30", min_minutes=120)]
        self.assertGreaterEqual(len(morning), 3)
        for cron in morning:
            self.assertFalse(SG.cron_in_busy_window(cron), cron)
            self.assertLess(int(cron.split()[1]), 22, cron)

    def test_health_and_watch_match_the_lookahead_and_avoid_the_crush(self):
        for cron, _check, slot, lead in SG.HEALTH_SCHEDULE:
            minute = int(cron.split()[0])
            self.assertNotIn(minute, (0, 30))
            self.assertEqual(minute % 2, 1, cron)
            self.assertFalse(SG.cron_in_busy_window(cron), cron)
            in_window = [
                item_lead for _cron, item_lead in SG.covering_leads(
                    slot, min_minutes=int(SG.MIN_LEAD.total_seconds() // 60), max_minutes=HORIZON)
            ]
            self.assertGreater(lead, max(in_window), slot)
            self.assertEqual(lead, SG.cron_lead_minutes(cron, slot))
        for cron, slot in SG.WATCH_CRONS:
            minute = int(cron.split()[0])
            self.assertNotIn(minute, (0, 30))
            self.assertEqual(minute % 2, 1, cron)
            self.assertFalse(SG.cron_in_busy_window(cron), cron)
            minutes_after = (24 * 60 - SG.cron_lead_minutes(cron, slot)) % (24 * 60)
            self.assertGreater(minutes_after, 0)
            self.assertLess(minutes_after, 90, (cron, slot, minutes_after))


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
        # 16:00 is 10h13m away, inside the 14h lookahead, so the run moves on.
        choice = SG.next_unfilled(now, filled)
        self.assertEqual(choice["slot"], "16:00")
        self.assertEqual(choice["date_ist"], "2026-10-02")
        self.assertNotEqual(choice["slot"], first["slot"])

    def test_instagram_only_row_counts_as_filled(self):
        now = datetime(2026, 10, 2, 5, 47, tzinfo=IST)
        entries = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": None, "platform": "instagram"}]
        choice = SG.next_unfilled(now, entries)
        self.assertEqual(choice["slot"], "16:00")
        self.assertNotEqual(choice["slot"], "08:30")

    def test_late_1600_run_does_not_steal_tomorrow(self):
        # 17:04 UTC = 22:34 IST. The old code scheduled the next day's 16:00.
        # 08:30 next morning is 9h56m away (inside 14h). Tomorrow's 16:00 is 17h26m, outside.
        now = datetime(2026, 10, 1, 22, 34, tzinfo=IST)
        choice = SG.next_unfilled(now, [])
        self.assertEqual(choice["slot"], "08:30")
        self.assertEqual(choice["date_ist"], "2026-10-02")
        self.assertNotEqual(choice["slot"], "16:00")
        self.assertIsNone(SG.scheduled_publish_at("16:00", now))
        self.assertIsNone(SG.scheduled_publish_at("16:00", now, date_ist="2026-10-01"))
        held = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": None, "platform": "hold"}]
        self.assertIsNone(SG.next_unfilled(now, held))

    def test_late_2100_run_does_not_push_the_video_to_the_next_2100(self):
        # 20:24 UTC = 01:54 IST the next morning. Old code targeted the next 21:00.
        now = datetime(2026, 10, 2, 1, 54, tzinfo=IST)
        choice = SG.next_unfilled(now, [])
        self.assertEqual(choice["slot"], "08:30")
        self.assertEqual(choice["date_ist"], "2026-10-02")
        self.assertNotEqual(choice["slot"], "21:00")
        self.assertIsNone(SG.scheduled_publish_at("21:00", now, date_ist="2026-10-01"))

    def test_slot_inside_the_window_is_not_replaced_by_a_later_one(self):
        now = datetime(2026, 10, 2, 12, 11, tzinfo=IST)  # 06:41 UTC
        choice = SG.next_unfilled(now, [])
        self.assertEqual(choice["slot"], "16:00")
        self.assertEqual(choice["date_ist"], "2026-10-02")
        filled = [{"date_ist": "2026-10-02", "slot": "16:00", "video_id": "abc"}]
        # 21:00 is 8h49m away, still inside 14h.
        nxt = SG.next_unfilled(now, filled)
        self.assertEqual(nxt["slot"], "21:00")
        self.assertEqual(nxt["date_ist"], "2026-10-02")

    def test_too_close_to_publish_is_not_rolled_or_sent_now(self):
        now = datetime(2026, 10, 2, 8, 15, tzinfo=IST)  # 15 min before 08:30
        choice = SG.next_unfilled(now, [])
        self.assertNotEqual(choice["slot"], "08:30")
        self.assertEqual(choice["slot"], "16:00")
        self.assertEqual(choice["publish_at"], "2026-10-02T10:30:00Z")
        self.assertIsNone(SG.scheduled_publish_at("08:30", now, date_ist="2026-10-02"))

    def test_filled_or_held_slot_moves_on_only_inside_the_lookahead(self):
        # 05:47 IST: 16:00 is 10h13m ahead (in); 21:00 is 15h13m ahead (out).
        now = datetime(2026, 10, 2, 5, 47, tzinfo=IST)
        for platform, video_id in (("youtube", "abc"), ("hold", None)):
            blocked = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": video_id, "platform": platform}]
            moved = SG.next_unfilled(now, blocked)
            self.assertEqual(moved["slot"], "16:00", platform)
            self.assertEqual(moved["publish_at"], "2026-10-02T10:30:00Z")
            both = blocked + [{
                "date_ist": "2026-10-02", "slot": "16:00", "video_id": video_id, "platform": platform,
            }]
            self.assertIsNone(SG.next_unfilled(now, both), platform)
        # Custom horizon stops before the following slot even when the default would allow it.
        held = [{"date_ist": "2026-10-02", "slot": "08:30", "video_id": None, "platform": "hold"}]
        self.assertIsNone(SG.next_unfilled(now, held, max_lookahead=SG.timedelta(hours=6)))
        self.assertEqual(
            SG.next_unfilled(now, held, max_lookahead=SG.timedelta(hours=14))["slot"], "16:00")

    def test_two_simultaneous_runs_cannot_fill_the_same_slot(self):
        now = datetime(2026, 10, 4, 5, 47, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text("[]", encoding="utf-8")
            concurrency = threading.Lock()
            barrier = threading.Barrier(2)
            outcomes = []

            def one_run(video_id):
                # Both runs pick from the post-checkout ledger before either upload.
                picked = SG.next_unfilled(now, json.loads(path.read_text(encoding="utf-8")))
                barrier.wait()
                with concurrency:
                    entries = json.loads(path.read_text(encoding="utf-8"))
                    state = SG.row_state(SG.find_entry(entries, picked["date_ist"], picked["slot"]))
                    if state != "open":
                        outcomes.append(("skip", video_id, SG.next_unfilled(now, entries)))
                        return
                    entries.append({
                        "date_ist": picked["date_ist"],
                        "slot": picked["slot"],
                        "video_id": video_id,
                        "platform": "youtube",
                    })
                    path.write_text(json.dumps(entries), encoding="utf-8")
                    outcomes.append(("fill", video_id, picked))

            threads = [threading.Thread(target=one_run, args=(vid,)) for vid in ("run-a", "run-b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            rows = json.loads(path.read_text(encoding="utf-8"))

        fills = [item for item in outcomes if item[0] == "fill"]
        skips = [item for item in outcomes if item[0] == "skip"]
        self.assertEqual(len(fills), 1)
        self.assertEqual(len(skips), 1)
        self.assertEqual(fills[0][2]["slot"], "08:30")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slot"], "08:30")
        self.assertEqual(rows[0]["date_ist"], "2026-10-04")
        # The loser re-reads and moves on. 16:00 that day is inside 14h.
        self.assertEqual(skips[0][2]["slot"], "16:00")
        self.assertNotEqual(skips[0][2]["slot"], rows[0]["slot"])

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
        self.assertIn("slot=16:00", text)
        self.assertIn("taken=false", text)
        self.assertNotIn("slot=08:30", text)

        buf = io.StringIO()
        blocked = filled + [{"date_ist": "2026-10-02", "slot": "16:00", "video_id": None, "platform": "hold"}]
        with mock.patch.object(SG, "_now", return_value=now), \
             mock.patch.object(SG, "_read", return_value=blocked), \
             mock.patch("sys.stdout", buf):
            SG.main(["--next"])
        text = buf.getvalue()
        self.assertIn("taken=true", text)
        self.assertNotIn("slot=08:30", text)
        self.assertNotIn("slot=16:00", text)

    def test_publish_date_env_wins_over_the_clock_rollover(self):
        now = datetime(2026, 10, 1, 22, 34, tzinfo=IST)
        with mock.patch.dict("os.environ", {"PUBLISH_DATE_IST": "2026-10-02", "PUBLISH_AT_UTC": ""}, clear=False):
            self.assertEqual(SG.target_date("16:00", now), "2026-10-02")
        with mock.patch.dict("os.environ", {"PUBLISH_DATE_IST": "", "PUBLISH_AT_UTC": "2026-10-02T03:00:00Z"}, clear=False):
            self.assertEqual(SG.target_date("08:30", now), "2026-10-02")


class WatchFillTests(unittest.TestCase):
    def _run(self, now, entries, slot="21:00"):
        import slot_watch
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "published.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            gout = Path(d) / "gha"
            env = {"SLOT_CHECK": slot, "GITHUB_OUTPUT": str(gout)}
            buf = io.StringIO()
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict("os.environ", env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt, \
                 mock.patch("sys.stdout", buf):
                dt.now.return_value = now
                slot_watch.main()
            return gout.read_text(encoding="utf-8"), buf.getvalue()

    def test_empty_past_slot_does_not_request_an_extra_post_when_nothing_is_ahead(self):
        import slot_watch
        now = slot_watch.datetime(2026, 10, 1, 21, 49, tzinfo=slot_watch.IST)
        # 08:30 is held. 16:00 is 18h11m away, outside the 14h lookahead.
        entries = [
            {"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc", "format": "short"},
            {"date_ist": "2026-10-02", "slot": "08:30", "video_id": None, "platform": "hold"},
        ]
        flags, _text = self._run(now, entries)
        self.assertIn("missing=true", flags)
        self.assertIn("fill=false", flags)

    def test_watch_fills_the_next_open_slot_inside_the_lookahead(self):
        import slot_watch
        now = slot_watch.datetime(2026, 10, 1, 21, 49, tzinfo=slot_watch.IST)
        entries = [{"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc", "format": "short"}]
        flags, text = self._run(now, entries)
        self.assertIn("missing=true", flags)
        self.assertIn("fill=true", flags)
        self.assertIn("2026-10-02 08:30", text)
        self.assertIn("publishAt", text)
        self.assertNotIn("privacy=now", text)


class PreUploadRereadTests(unittest.TestCase):
    def test_workflow_rereads_the_ledger_after_checkout(self):
        text = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("group: paise-ki-pathshala-publish", text)
        self.assertIn("cancel-in-progress: false", text)
        refresh = text[text.index("Re-read ledger after checkout"):text.index("Format decide karo")]
        self.assertIn("git fetch origin", refresh)
        self.assertIn("data/published.json", refresh)
        self.assertLess(text.index("actions/checkout"), text.index("Re-read ledger after checkout"))
        self.assertLess(text.index("Re-read ledger after checkout"), text.index("slot_guard.py --next"))
        src = (ROOT / "publish.py").read_text(encoding="utf-8")
        upload = src.index("def _do_upload")
        guard = src.index("scheduled_slot_block")
        self.assertLess(guard, upload)

    def test_pipeline_reread_skips_upload_when_the_slot_was_filled_mid_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "published.json"
            ledger_path.write_text("[]", encoding="utf-8")
            credits = Path(tmp) / "credits.json"
            calls = []

            def fake_run(script, *args):
                calls.append(script)
                if script == "check_setup.py":
                    ledger_path.write_text(json.dumps([{
                        "date_ist": "2026-10-04",
                        "slot": "08:30",
                        "video_id": "already",
                        "platform": "youtube",
                    }]), encoding="utf-8")
                return 0

            env = {
                "PUBLISH_VISIBILITY": "public",
                "PUBLISH_SLOT_IST": "08:30",
                "PUBLISH_DATE_IST": "2026-10-04",
                "VIDEO_FORMAT": "long",
            }
            with mock.patch.object(SG, "LEDGER", ledger_path), \
                 mock.patch.object(credit_log, "PATH", credits), \
                 mock.patch.object(run_pipeline, "run", fake_run), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(sys, "argv", ["run_pipeline.py", "--publish"]):
                with self.assertRaises(SystemExit) as caught:
                    run_pipeline.main()
            self.assertEqual(caught.exception.code, 0)
            self.assertNotIn("publish.py", calls)
            self.assertNotIn("publish_instagram.py", calls)
            saved = json.loads(credits.read_text(encoding="utf-8"))
            self.assertEqual(saved["deepseek_calls"], 0)
            self.assertEqual(saved["elevenlabs_characters_sent"], 0)

    def test_publish_rereads_and_refuses_a_filled_slot_before_any_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "published.json"
            ledger_path.write_text(json.dumps([{
                "date_ist": "2026-10-04",
                "slot": "08:30",
                "video_id": "already",
                "platform": "youtube",
            }]), encoding="utf-8")
            env = {
                "PUBLISH_VISIBILITY": "public",
                "PUBLISH_SLOT_IST": "08:30",
                "PUBLISH_DATE_IST": "2026-10-04",
            }
            with mock.patch.object(SG, "LEDGER", ledger_path), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch("yt_credentials.youtube_client_or_exit", side_effect=AssertionError("upload")):
                with self.assertRaises(SystemExit) as caught:
                    publish.main()
            self.assertEqual(caught.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
