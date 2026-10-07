"""A scheduled YouTube upload does not post the Reel until the video is public."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import ig_defer
import ledger
import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
WHEN = "2026-10-04T03:00:00Z"
NOW_BEFORE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
NOW_AFTER = datetime(2026, 10, 4, 3, 1, tzinfo=timezone.utc)


def _row(video_id="jgS2HgMHYzc", publish_at=WHEN, run_id="37112471867", media=""):
    status = ig_defer.defer_status(video_id, publish_at, run_id)
    row = {
        "date_ist": "2026-10-04",
        "slot": "08:30",
        "video_id": video_id,
        "format": "short",
        "ig_deferred": status,
        "source_run": run_id,
    }
    if media:
        row["ig_media_id"] = media
    return row


class DeferralTests(unittest.TestCase):
    def test_a_future_publish_at_writes_deferred_and_does_not_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "uploaded.txt").write_text("jgS2HgMHYzc\n", encoding="utf-8")
            path = Path(tmp) / "published.json"
            env = {
                "IG_CROSSPOST": "1", "VIDEO_FORMAT": "short", "PUBLISH_AT_UTC": WHEN,
                "GITHUB_RUN_ID": "37112471867", "PIPELINE_MODE": "", "TOPIC_KEY": "",
                "BOARD_QA": "",
            }
            with mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(ledger, "ROOT", Path(tmp)), \
                 mock.patch.object(run_pipeline, "run", side_effect=AssertionError("must not post")) as called, \
                 mock.patch.dict(os_environ(), env, clear=False):
                ledger.record_publish("jgS2HgMHYzc", WHEN, "08:30", date_ist="2026-10-04")
                rc, published, required = run_pipeline.crosspost_or_defer(True, now=NOW_BEFORE)
            self.assertEqual(rc, 0)
            self.assertFalse(published)
            self.assertFalse(required)
            self.assertFalse(called.called)
            text = (run / "ig_status.txt").read_text(encoding="utf-8").strip()
            self.assertTrue(text.startswith("deferred:"), text)
            self.assertIn("jgS2HgMHYzc", text)
            self.assertIn(f"publishAt={WHEN}", text)
            self.assertIn("run=37112471867", text)
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["ig_deferred"], text)
            self.assertEqual(rows[0]["source_run"], "37112471867")
            self.assertNotIn("ig_media_id", rows[0])

    def test_privacy_now_still_crossposts_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "uploaded.txt").write_text("jgS2HgMHYzc\n", encoding="utf-8")
            env = {
                "IG_CROSSPOST": "1", "VIDEO_FORMAT": "short", "PUBLISH_AT_UTC": "",
                "PIPELINE_MODE": "", "TOPIC_KEY": "", "BOARD_QA": "",
            }

            def fake_run(script, *_args):
                self.assertEqual(script, "publish_instagram.py")
                (run / "ig_reel.txt").write_text("reel-now\n", encoding="utf-8")
                return 0

            with mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.object(run_pipeline, "run", side_effect=fake_run), \
                 mock.patch.dict(os_environ(), env, clear=False):
                rc, published, required = run_pipeline.crosspost_or_defer(True, now=NOW_BEFORE)
            self.assertEqual(rc, 0)
            self.assertTrue(published)
            self.assertTrue(required)
            self.assertFalse((run / "ig_status.txt").exists())


class PostDueTests(unittest.TestCase):
    def test_posts_only_when_the_video_is_public_and_refuses_a_second_reel(self):
        repair = (WF / "instagram-repair.yml").read_text(encoding="utf-8")
        self.assertIn("python ig_defer.py --post-due", repair)
        self.assertIn("github.event.workflow_run.name == 'Slot watch'", repair)
        self.assertIn("github.event_name == 'schedule'", repair)
        self.assertIn('cron: "30,50 2 * * *"', repair)
        self.assertIn('cron: "10,30,50 3-16 * * *"', repair)
        self.assertIn('cron: "10,30 17 * * *"', repair)
        self.assertIn("grep -q '^skipped:'", repair)
        install = repair.split("name: Install dependencies", 1)[1].split("name: Publish Reel only", 1)[0]
        self.assertIn("github.event.workflow_run.name == 'Slot watch'", install)
        self.assertIn("github.event_name == 'schedule'", install)
        deferred = repair.split("name: Post deferred Reels", 1)[1].split("name: Commit the deferred Reel", 1)[0]
        self.assertIn("github.event_name == 'schedule'", deferred)
        watch = (WF / "slot-watch.yml").read_text(encoding="utf-8")
        called = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        self.assertIn("actions: read", called)
        self.assertIn("actions: read", watch)
        posted = []

        def poster(dest, video_id):
            self.assertTrue((Path(dest) / "final.mp4").is_file())
            self.assertTrue((Path(dest) / "thumbnail.jpg").is_file())
            posted.append(video_id)

        def fetcher(run_id):
            self.assertEqual(run_id, "37112471867")
            dest = Path(tempfile.mkdtemp())
            (dest / "final.mp4").write_bytes(b"same-mp4")
            (dest / "thumbnail.jpg").write_bytes(b"cover")
            return dest

        early = ig_defer.post_due(
            [_row()], NOW_BEFORE, lambda _vid: "public", poster, fetcher, lambda _vid: "",
        )
        self.assertEqual(early, [])
        self.assertEqual(posted, [])

        waiting = ig_defer.post_due(
            [_row()], NOW_AFTER, lambda _vid: "private", poster, fetcher, lambda _vid: "",
        )
        self.assertEqual(waiting[0]["action"], "not-public")
        self.assertEqual(posted, [])

        ready = ig_defer.post_due(
            [_row()], NOW_AFTER, lambda _vid: "public", poster, fetcher, lambda _vid: "",
        )
        self.assertEqual(ready[0]["action"], "posted")
        self.assertEqual(posted, ["jgS2HgMHYzc"])

        def refuse(*_args):
            raise AssertionError("must not post twice")

        again = ig_defer.post_due(
            [_row()], NOW_AFTER, lambda _vid: "public", refuse, fetcher,
            lambda video_id: "already-reel" if video_id == "jgS2HgMHYzc" else "",
        )
        self.assertEqual(again[0]["action"], "already-logged")
        self.assertEqual(again[0]["media_id"], "already-reel")
        logged = [_row(media="already-reel")]
        self.assertEqual(ig_defer.due_rows(logged, NOW_AFTER), [])


def os_environ():
    import os
    return os.environ
