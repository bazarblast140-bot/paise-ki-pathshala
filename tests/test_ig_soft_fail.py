"""YouTube upload stands when the Reel fails. The ledger is pushed; the Reel is retried."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import buffer_shorts
import ledger
import publish_instagram as IG
import run_pipeline

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


class PublishStatusTests(unittest.TestCase):
    def test_youtube_upload_with_a_missed_reel_is_success_with_a_warning(self):
        status, stop = run_pipeline.resolve_publish_status(
            yt_rc=0, yt_uploaded=True, ig_published=False, ig_required=True, verdict_ready=True)
        self.assertEqual(status, "success")
        self.assertIn("[warn]", stop)
        self.assertNotIn("[STOP]", stop)
        self.assertIn("ig_status.txt", stop)

    def test_a_missed_reel_without_a_youtube_upload_still_fails(self):
        status, stop = run_pipeline.resolve_publish_status(
            yt_rc=0, yt_uploaded=False, ig_published=False, ig_required=True, verdict_ready=True)
        self.assertNotEqual(status, "success")
        self.assertIn("[STOP]", stop)
        self.assertIn("Instagram Reel publish was not confirmed", stop)

    def test_invalid_grant_still_fails_when_a_reel_was_posted(self):
        status, stop = run_pipeline.resolve_publish_status(
            yt_rc=3, yt_uploaded=False, ig_published=True, ig_required=True, verdict_ready=True)
        self.assertNotEqual(status, "success")
        self.assertIn("invalid_grant", status)
        self.assertIn("non-zero", stop)

    def test_soft_fail_writes_ig_status_and_notes_the_existing_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "uploaded.txt").write_text("abc123xyz\n", encoding="utf-8")
            path = Path(tmp) / "published.json"
            with mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(ledger, "ROOT", Path(tmp)):
                ledger.record_publish("abc123xyz", "2026-10-02T15:30:00Z", "21:00", date_ist="2026-10-02")
                status, stop = run_pipeline.finish_publish(
                    yt_rc=0, yt_uploaded=True, ig_published=False, ig_required=True,
                    verdict_ready=True, ig_rc=1)
            self.assertEqual(status, "success")
            self.assertIn("[warn]", stop)
            text = (run / "ig_status.txt").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("skipped:"), text)
            self.assertIn("rc=1", text)
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["video_id"], "abc123xyz")
            self.assertTrue(rows[0]["ig_skipped"].startswith("skipped:"))
            self.assertNotIn("platform", rows[0])

    def test_soft_fail_does_not_append_a_row_when_the_video_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            path = Path(tmp) / "published.json"
            path.write_text("[]\n", encoding="utf-8")
            with mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(ledger, "ROOT", Path(tmp)):
                IG.mark_ig_skipped(run, "missing-video", "Instagram publish rc=1")
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(rows, [])
            self.assertTrue((run / "ig_status.txt").read_text(encoding="utf-8").startswith("skipped:"))

    def test_youtube_failure_does_not_write_a_skip_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            with mock.patch.object(run_pipeline, "RUN", run):
                status, stop = run_pipeline.finish_publish(
                    yt_rc=3, yt_uploaded=False, ig_published=True, ig_required=True,
                    verdict_ready=True, ig_rc=0)
            self.assertNotEqual(status, "success")
            self.assertIn("non-zero", stop)
            self.assertFalse((run / "ig_status.txt").exists())

    def test_main_exits_only_when_status_is_not_success(self):
        text = (ROOT / "run_pipeline.py").read_text(encoding="utf-8")
        self.assertIn('status, stop = finish_publish(', text)
        self.assertIn('if status != "success":\n        sys.exit(1)', text)


class BufferSoftFailTests(unittest.TestCase):
    def _item(self):
        return {
            "topic_key": "buffer_example_risk",
            "files": {"script": "TITLE:\nPosition size example\n", "topic": {"key": "buffer_example_risk"}},
        }

    def test_reel_crash_after_upload_notes_the_row_and_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            path = Path(tmp) / "published.json"
            with mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(ledger, "ROOT", Path(tmp)), \
                 mock.patch.object(buffer_shorts, "RUN", run), \
                 mock.patch.dict("os.environ", {"IG_CROSSPOST": "1"}), \
                 mock.patch("publish_instagram.existing_media", return_value=""), \
                 mock.patch("publish_instagram.main", side_effect=SystemExit(1)):
                ledger.record_publish(
                    "vidbuffer1", "2026-10-02T15:30:00Z", "21:00", date_ist="2026-10-02")
                result = buffer_shorts.crosspost_buffer(
                    self._item(), "vidbuffer1", b"buffer-video-bytes", run)
            self.assertFalse(result["posted"])
            self.assertTrue(result["ig_skipped"])
            self.assertTrue((run / "ig_status.txt").read_text(encoding="utf-8").startswith("skipped:"))
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertIn("skipped:", rows[0]["ig_skipped"])

    def test_intentional_skip_does_not_mark_the_reel_missed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(buffer_shorts, "RUN", run), \
                 mock.patch.dict("os.environ", {"IG_CROSSPOST": "1"}), \
                 mock.patch("publish_instagram.existing_media", return_value=""), \
                 mock.patch("publish_instagram.main", side_effect=SystemExit(0)):
                result = buffer_shorts.crosspost_buffer(
                    self._item(), "vidbuffer1", b"buffer-video-bytes", run)
            self.assertFalse(result["posted"])
            self.assertNotIn("ig_skipped", result)
            self.assertFalse((run / "ig_status.txt").exists())

    def test_fill_stays_filled_when_the_crosspost_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            path = Path(tmp) / "published.json"
            manifest = Path(tmp) / "manifest.json"
            item = {
                "id": "buf-1",
                "state": "ready",
                "topic_key": "buffer_example_risk",
                "title": "example",
                "description": "example",
                "tags": [],
                "sha256": buffer_shorts.sha256_bytes(b"video"),
                "asset_name": "clip.mp4",
                "created_at": "2026-10-01T00:00:00Z",
            }
            buffer_shorts.save_manifest({"tag": "buffer-shorts", "items": [item]}, manifest)
            store = mock.Mock()
            store.get.return_value = b"video"

            def explode(*_a, **_k):
                raise SystemExit(1)

            now = __import__("datetime").datetime(
                2026, 10, 2, 9, 0, tzinfo=buffer_shorts.slot_guard.IST)
            with mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(ledger, "ROOT", Path(tmp)), \
                 mock.patch.object(buffer_shorts, "RUN", run):
                result = buffer_shorts.perform_fill(
                    now=now, date_ist="2026-10-02", slot="21:00", last_chance=False,
                    read_entries=lambda: [],
                    read_schedule=lambda: set(),
                    manifest_path=manifest,
                    store=store,
                    uploader=lambda *_a, **_k: "vidbuffer9",
                    crosspost=explode,
                    alert=lambda *_a, **_k: None,
                    bank_topics=[{"key": "buffer_example_risk"}],
                )
            self.assertTrue(result["filled"])
            self.assertEqual(result["video_id"], "vidbuffer9")
            self.assertTrue(result["ig"]["ig_skipped"])
            self.assertTrue((run / "ig_status.txt").read_text(encoding="utf-8").startswith("skipped:"))
            rows = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["video_id"], "vidbuffer9")
            self.assertIn("skipped:", rows[0]["ig_skipped"])


class WorkflowTests(unittest.TestCase):
    def test_ledger_commit_runs_when_a_youtube_id_exists(self):
        publish = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        watch = (WF / "slot-watch.yml").read_text(encoding="utf-8")
        old = "steps.guard.outputs.taken != 'true' && (success() || steps.buffer.outcome == 'success')"
        self.assertNotIn(old, publish)
        self.assertIn("steps.uploaded.outputs.youtube_uploaded == 'true'", publish)
        self.assertIn("!cancelled()", publish)
        self.assertIn("run/uploaded.txt", publish)
        self.assertIn("run/ig_status.txt", publish)
        commit = watch[watch.index("Commit ledger and buffer manifest"):]
        self.assertIn("!cancelled()", commit)
        self.assertIn("steps.uploaded.outputs.youtube_uploaded == 'true'", commit)
        self.assertIn("run/ig_status.txt", watch)

    def test_repair_follows_ig_status_for_publish_and_slot_watch(self):
        repair = (WF / "instagram-repair.yml").read_text(encoding="utf-8")
        self.assertIn('"Paise Ki Pathshala"', repair)
        self.assertIn('"Slot watch"', repair)
        self.assertIn("ig_status.txt", repair)
        self.assertIn("skipped:", repair)
        self.assertIn("ig_reel.txt", repair)
        self.assertNotIn("--log-failed", repair)
        self.assertNotIn("Instagram Reel publish was not confirmed", repair)
        self.assertIn("group: paise-ki-pathshala-publish", repair)
        self.assertIn("cancel-in-progress: false", repair)
        # The Reel step is still publish_instagram.py, which keeps the READY gate.
        self.assertIn("python publish_instagram.py", repair)
        self.assertIn("conclusion != 'cancelled'", repair)
