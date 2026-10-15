import unittest
from datetime import datetime
from pathlib import Path

import yaml

import reel_catchup as rc

IST = rc.IST
THU = datetime(2026, 10, 15, 9, 10, tzinfo=IST)  # news window passed 25 min ago


def _run(title, at="2026-10-15T02:50:00Z"):
    return {"display_title": title, "created_at": at}


class CatchupTests(unittest.TestCase):
    def test_missed_news_cron_dispatches(self):
        self.assertEqual(rc.due(THU, [], []), ["news"])

    def test_inside_grace_does_nothing(self):
        self.assertEqual(rc.due(THU.replace(minute=0), [], []), [])

    def test_any_run_today_blocks(self):
        for title in ("daily-reel 50 2 * * 1-5 cron", "daily-reel 15 3 * * 1-5 cron", "daily-reel news both"):
            self.assertEqual(rc.due(THU, [_run(title)], []), [], title)

    def test_yesterday_run_does_not_block(self):
        self.assertEqual(rc.due(THU, [_run("daily-reel news both", "2026-10-14T03:00:00Z")], []), ["news"])

    def test_other_kind_run_does_not_block(self):
        self.assertEqual(rc.due(THU, [_run("daily-reel ta build")], []), ["news"])

    def test_ledger_entry_blocks(self):
        ledger = [{"date_ist": "2026-10-15", "slot": "08:52", "video_id": "abc"}]
        self.assertEqual(rc.due(THU, [], ledger), [])

    def test_weekend_and_stale(self):
        self.assertEqual(rc.due(datetime(2026, 10, 17, 9, 10, tzinfo=IST), [], []), [])
        self.assertEqual(rc.due(THU.replace(hour=11, minute=0), [], []), [])

    def test_close_and_ta_windows(self):
        self.assertEqual(rc.due(THU.replace(hour=16, minute=40), [], []), ["close"])
        self.assertEqual(rc.due(THU.replace(hour=18, minute=55), [], []), ["ta"])

    def test_windows_match_daily_reels_crons(self):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        text = (root / "daily-reels.yml").read_text(encoding="utf-8")
        for build, publish, _ in rc.WINDOWS.values():
            self.assertIn(f'"{build}"', text)
            self.assertIn(f'"{publish}"', text)
        wf = yaml.safe_load((root / "reel-catchup.yml").read_text(encoding="utf-8"))
        self.assertEqual(wf["permissions"]["actions"], "write")

    def test_publish_lookup_has_no_regex_escape(self):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        text = (root / "daily-reels.yml").read_text(encoding="utf-8")
        self.assertNotIn("\\\\*", text)
        self.assertNotIn('test(\\"$CRON', text)


if __name__ == "__main__":
    unittest.main()
