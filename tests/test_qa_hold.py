"""QA hold for automatic uploads, and a release jpeg thumbnail. No network."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import approve_qa
import ig_defer
import ledger
import override_mp4
import publish
import qa_hold
import slot_guard

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
JPG = "https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/short-178/thumbnail.jpg"
MP4 = "https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/short-178/final.mp4"


class HoldRuleTests(unittest.TestCase):
    def test_unset_approval_holds_only_automatic_uploads(self):
        base = {"REQUIRE_QA_APPROVAL": None, "AUTOMATIC_UPLOAD": None, "EXPLICIT_PRIVACY": None}
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in base:
                os.environ.pop(key, None)
            self.assertTrue(qa_hold.approval_required())
            self.assertFalse(qa_hold.hold_active())
            vis, when, held = qa_hold.apply_hold("private", "2026-10-05T10:30:00Z")
            self.assertEqual((vis, when, held), ("private", "2026-10-05T10:30:00Z", False))

        with mock.patch.dict(os.environ, {"AUTOMATIC_UPLOAD": "1"}, clear=False):
            os.environ.pop("REQUIRE_QA_APPROVAL", None)
            os.environ.pop("EXPLICIT_PRIVACY", None)
            self.assertTrue(qa_hold.hold_active())
            vis, when, held = qa_hold.apply_hold("private", "2026-10-05T10:30:00Z")
            self.assertEqual((vis, when, held), ("private", None, True))
            vis, when, held = qa_hold.apply_hold("public", None)
            self.assertEqual((vis, when, held), ("private", None, True))
            vis, when, held = qa_hold.apply_hold("unlisted", None)
            self.assertEqual((vis, when, held), ("unlisted", None, False))

        off = {"AUTOMATIC_UPLOAD": "1", "REQUIRE_QA_APPROVAL": "false"}
        with mock.patch.dict(os.environ, off, clear=False):
            self.assertFalse(qa_hold.hold_active())
            self.assertTrue(qa_hold.auto_gate_active())

        explicit = {"AUTOMATIC_UPLOAD": "1", "REQUIRE_QA_APPROVAL": "true", "EXPLICIT_PRIVACY": "1"}
        with mock.patch.dict(os.environ, explicit, clear=False):
            self.assertFalse(qa_hold.hold_active())
            self.assertFalse(qa_hold.auto_gate_active())
            vis, when, held = qa_hold.apply_hold("public", "2026-10-05T10:30:00Z")
            self.assertEqual((vis, when, held), ("public", "2026-10-05T10:30:00Z", False))

    def test_publish_applies_the_hold_after_the_visibility_clamp(self):
        text = (ROOT / "publish.py").read_text(encoding="utf-8")
        self.assertLess(text.index("clamp_visibility"), text.index("apply_hold"))
        self.assertIn("pending_qa=held", text)
        self.assertIn("qa_hold.mark_run(RUN)", text)


class PendingQaSlotTests(unittest.TestCase):
    def _entries(self):
        return [
            {"date_ist": "2026-10-02", "slot": "16:00", "video_id": "already"},
            {
                "date_ist": "2026-10-02",
                "slot": "21:00",
                "video_id": "qaVid123456",
                "pending_qa": True,
                "format": "short",
                "source_run": "37122322074",
            },
        ]

    def test_pending_qa_fills_the_slot_and_is_listed(self):
        entries = self._entries()
        now = datetime(2026, 10, 2, 9, 0, tzinfo=slot_guard.IST)
        self.assertEqual(slot_guard.row_state(slot_guard.find_entry(entries, "2026-10-02", "21:00")), "filled")
        self.assertTrue(slot_guard.slot_is_scheduled(entries, "2026-10-02", "21:00"))
        choice = slot_guard.next_unfilled(now, entries)
        self.assertEqual(choice["date_ist"], "2026-10-03")
        self.assertEqual(choice["slot"], "08:30")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            with mock.patch.object(ledger, "PATH", path), \
                 mock.patch.object(slot_guard, "_read", return_value=entries), \
                 mock.patch.dict(os.environ, {
                     "PUBLISH_VISIBILITY": "public",
                     "PUBLISH_SLOT_IST": "21:00",
                     "PUBLISH_DATE_IST": "2026-10-02",
                 }):
                self.assertEqual(ledger.pending_qa(), ["qaVid123456"])
                self.assertIn("already filled", slot_guard.scheduled_slot_block())

    def test_slot_watch_does_not_build_a_second_video_for_pending_qa(self):
        import slot_watch
        now = slot_watch.datetime(2026, 10, 2, 21, 49, tzinfo=slot_watch.IST)
        entries = [
            {"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc", "format": "short"},
            {
                "date_ist": "2026-10-02",
                "slot": "21:00",
                "video_id": "qaVid123456",
                "pending_qa": True,
                "format": "short",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "published.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            gout = Path(tmp) / "gha"
            env = {"SLOT_CHECK": "21:00", "GITHUB_OUTPUT": str(gout)}
            buf = __import__("io").StringIO()
            with mock.patch.object(slot_watch, "LEDGER", path), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as clock, \
                 mock.patch("sys.stdout", buf):
                clock.now.return_value = now
                slot_watch.main()
            flags = gout.read_text(encoding="utf-8")
            text = buf.getvalue()
        self.assertIn("missing=false", flags)
        self.assertIn("fill=false", flags)
        self.assertIn("use_buffer=false", flags)
        self.assertIn("https://youtu.be/qaVid123456", text)
        self.assertNotIn("KHALI", text)


class CrosspostHoldTests(unittest.TestCase):
    def test_pipeline_does_not_post_or_defer_a_held_reel(self):
        import run_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            qa_hold.mark_run(run)
            with mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.object(run_pipeline, "run", side_effect=AssertionError("instagram")), \
                 mock.patch.object(ig_defer, "write_deferred", side_effect=AssertionError("defer")), \
                 mock.patch.dict(os.environ, {"IG_CROSSPOST": "1", "PUBLISH_AT_UTC": "2099-01-01T00:00:00Z"}):
                rc, published, required = run_pipeline.crosspost_or_defer(True)
            self.assertEqual((rc, published, required), (0, False, False))
            self.assertFalse((run / "ig_status.txt").exists())

    def test_instagram_main_skips_a_held_reel(self):
        import publish_instagram as ig
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            qa_hold.mark_run(run)
            with mock.patch.object(ig, "RUN", run), \
                 mock.patch.object(ig, "publish_reel", side_effect=AssertionError("reel")), \
                 mock.patch.dict(os.environ, {"IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1"}):
                with self.assertRaises(SystemExit) as caught:
                    ig.main()
            self.assertEqual(caught.exception.code, 0)
            self.assertFalse((run / "ig_status.txt").exists())


class ThumbnailTests(unittest.TestCase):
    def test_only_a_release_jpg_is_accepted(self):
        self.assertTrue(override_mp4.thumb_url_ok(JPG))
        self.assertFalse(override_mp4.release_url_ok(JPG))
        self.assertTrue(override_mp4.release_url_ok(MP4))
        self.assertFalse(override_mp4.thumb_url_ok(MP4))
        self.assertFalse(override_mp4.thumb_url_ok(JPG.replace(".jpg", ".jpeg")))
        self.assertFalse(override_mp4.thumb_url_ok(JPG + "?token=1"))
        self.assertFalse(override_mp4.thumb_url_ok(JPG.replace("thumbnail.jpg", "../thumbnail.jpg")))

    def test_private_release_download_replaces_the_jpeg(self):
        blob = b"\xff\xd8\xff" + b"\x00" * 120
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "thumbnail.jpg"
            with mock.patch.object(override_mp4, "_token", return_value="tok"), \
                 mock.patch.object(override_mp4, "_download_private", return_value=blob) as private, \
                 mock.patch.object(override_mp4, "_download_public", side_effect=AssertionError("public")):
                override_mp4.apply_thumb(JPG, dest)
            private.assert_called_once_with(JPG, "tok")
            self.assertEqual(dest.read_bytes(), blob)
            with mock.patch.object(override_mp4, "_token", return_value="tok"), \
                 mock.patch.object(override_mp4, "_download_private", return_value=b"not-jpeg" + b"\x00" * 120), \
                 mock.patch.object(override_mp4, "_download_public", side_effect=AssertionError("public")):
                with self.assertRaises(SystemExit):
                    override_mp4.apply_thumb(JPG, dest)
            self.assertEqual(dest.read_bytes(), blob)
            with mock.patch.object(override_mp4, "_download", side_effect=AssertionError("fetched")):
                with self.assertRaises(SystemExit):
                    override_mp4.apply_thumb("https://example.com/thumb.jpg", dest)

    def test_set_thumbnail_calls_the_youtube_thumbnail_api(self):
        calls = []

        class Thumb:
            def thumbnails(self):
                return self

            def set(self, **kwargs):
                calls.append(kwargs)
                return self

            def execute(self):
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "thumbnail.jpg"
            path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 40)
            with mock.patch("googleapiclient.http.MediaFileUpload", side_effect=lambda p: p):
                publish.set_thumbnail(Thumb(), "qaVid123456", path)
        self.assertEqual(calls[0]["videoId"], "qaVid123456")
        self.assertEqual(calls[0]["media_body"], str(path))


class FakeYouTube:
    def __init__(self):
        self.bodies = []
        self._op = "list"

    def channels(self):
        return self

    def videos(self):
        return self

    def list(self, **_kwargs):
        self._op = "list"
        return self

    def update(self, **kwargs):
        self._op = "update"
        self._pending = kwargs.get("body")
        return self

    def execute(self):
        if self._op == "update":
            self.bodies.append(self._pending)
            self._op = "list"
            return {}
        return {"items": [{"id": "CHAN", "snippet": {"title": "Paise Ki Pathshala"}}]}


class ApproveTests(unittest.TestCase):
    def _ledger(self, path, row):
        path.write_text(json.dumps([row]), encoding="utf-8")

    def test_now_makes_the_video_public_and_defers_the_reel(self):
        row = {
            "date_ist": "2026-10-04",
            "slot": "16:00",
            "video_id": "qaVid123456",
            "pending_qa": True,
            "source_run": "37122322074",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            published = root / "published.json"
            self._ledger(published, row)
            yt = FakeYouTube()
            with mock.patch.object(ledger, "PATH", published), \
                 mock.patch.dict(os.environ, {"EXPECTED_CHANNEL_ID": "CHAN"}):
                result = approve_qa.approve("qaVid123456", "now", "", yt, run_dir=root)
            saved = json.loads(published.read_text(encoding="utf-8"))[0]
            status = (root / "ig_status.txt").read_text(encoding="utf-8")
            with mock.patch.object(ledger, "PATH", published):
                pending = ledger.pending_qa()
        self.assertEqual(result["privacy"], "public")
        self.assertIsNone(result["publish_at"])
        self.assertEqual(yt.bodies[0]["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", yt.bodies[0]["status"])
        self.assertFalse(saved["pending_qa"])
        self.assertEqual(saved["slot"], "16:00")
        self.assertEqual(pending, [])
        self.assertTrue(status.startswith("deferred: qaVid123456"))
        self.assertIn("run=37122322074", status)
        parsed = ig_defer.parse_deferred(status)
        self.assertTrue(ig_defer.is_due(parsed, datetime.now(timezone.utc)))

    def test_a_clock_slot_sets_publish_at_and_keeps_the_slot_filled(self):
        row = {
            "date_ist": "2026-10-04",
            "slot": "16:00",
            "video_id": "qaVid123456",
            "pending_qa": True,
            "source_run": "37122322074",
        }
        now = datetime(2026, 10, 3, 12, 0, tzinfo=slot_guard.IST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            published = root / "published.json"
            self._ledger(published, row)
            yt = FakeYouTube()
            with mock.patch.object(ledger, "PATH", published), \
                 mock.patch.dict(os.environ, {"EXPECTED_CHANNEL_ID": "CHAN"}):
                result = approve_qa.approve("qaVid123456", "21:00", "2026-10-04", yt, now=now, run_dir=root)
            saved = json.loads(published.read_text(encoding="utf-8"))[0]
            status = (root / "ig_status.txt").read_text(encoding="utf-8")
        self.assertEqual(result["privacy"], "private")
        self.assertEqual(result["publish_at"], "2026-10-04T15:30:00Z")
        self.assertEqual(yt.bodies[0]["status"]["privacyStatus"], "private")
        self.assertEqual(yt.bodies[0]["status"]["publishAt"], "2026-10-04T15:30:00Z")
        self.assertEqual(saved["slot"], "21:00")
        self.assertEqual(saved["date_ist"], "2026-10-04")
        self.assertFalse(saved["pending_qa"])
        self.assertIn("publishAt=2026-10-04T15:30:00Z", status)
        self.assertIn("run=37122322074", status)
        parsed = ig_defer.parse_deferred(status)
        self.assertFalse(ig_defer.is_due(parsed, datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)))

    def test_approve_refuses_a_row_with_no_artifact_run(self):
        row = {"date_ist": "2026-10-04", "slot": "16:00", "video_id": "qaVid123456", "pending_qa": True}
        with tempfile.TemporaryDirectory() as tmp:
            published = Path(tmp) / "published.json"
            self._ledger(published, row)
            yt = FakeYouTube()
            with mock.patch.object(ledger, "PATH", published), \
                 mock.patch.dict(os.environ, {"EXPECTED_CHANNEL_ID": "CHAN"}):
                with self.assertRaises(SystemExit):
                    approve_qa.approve("qaVid123456", "now", "", yt, run_dir=Path(tmp))
        self.assertEqual(yt.bodies, [])


class WorkflowTests(unittest.TestCase):
    def test_set_thumbnail_and_approve_qa_are_manual_workflows(self):
        thumb = (WF / "set-thumbnail.yml").read_text(encoding="utf-8")
        approve = (WF / "approve-qa.yml").read_text(encoding="utf-8")
        self.assertTrue(thumb.startswith("name: Set thumbnail\n"))
        self.assertIn("youtube_video_id:", thumb)
        self.assertIn("thumb_url:", thumb)
        self.assertIn("workflow_dispatch:", thumb)
        self.assertNotIn("schedule:", thumb)
        self.assertIn("python set_thumbnail.py", thumb)
        self.assertIn("secrets.YT_FIN_CLIENT_ID", thumb)
        self.assertIn("secrets.YT_FIN_CLIENT_SECRET", thumb)
        self.assertIn("secrets.YT_FIN_REFRESH_TOKEN", thumb)
        self.assertIn("vars.YT_FIN_CHANNEL_ID", thumb)
        self.assertIn("GH_TOKEN: ${{ github.token }}", thumb)
        self.assertIn("releases/download/<tag>/<file>.jpg", thumb)
        self.assertTrue(approve.startswith("name: Approve QA\n"))
        self.assertIn("youtube_video_id:", approve)
        self.assertIn("slot:", approve)
        self.assertIn("date_ist:", approve)
        self.assertIn("python approve_qa.py", approve)
        self.assertNotIn("publish_instagram.py", approve)
        self.assertNotIn("schedule:", approve)
        self.assertIn("secrets.YT_FIN_REFRESH_TOKEN", approve)

    def test_publish_artifact_can_replace_the_thumbnail_and_keeps_explicit_privacy(self):
        text = (WF / "publish-artifact.yml").read_text(encoding="utf-8")
        self.assertIn("override_thumb_url:", text)
        self.assertIn("python override_mp4.py --thumb", text)
        self.assertLess(text.index("python override_mp4.py --thumb"), text.index("python publish.py"))
        self.assertIn('EXPLICIT_PRIVACY: "1"', text)
        self.assertNotIn("AUTOMATIC_UPLOAD", text)
        upload = text.split("name: Upload a new YouTube video", 1)[1].split("name: Cross-post the Reel", 1)[0]
        self.assertIn('EXPLICIT_PRIVACY: "1"', upload)
        self.assertNotIn("skip_ig", upload)

    def test_automatic_builds_print_the_artifact_and_hold_for_qa(self):
        live = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        watch = (WF / "slot-watch.yml").read_text(encoding="utf-8")
        refill = (WF / "buffer-refill.yml").read_text(encoding="utf-8")
        for text in (live, watch, refill):
            self.assertIn("final.mp4: run/final.mp4", text)
            self.assertIn("artifact:", text)
            self.assertIn("$GITHUB_STEP_SUMMARY", text)
        self.assertIn("AUTOMATIC_UPLOAD:", live)
        self.assertIn("vars.REQUIRE_QA_APPROVAL || 'true'", live)
        self.assertIn("github.event_name == 'workflow_dispatch' && '1'", live)
        self.assertIn('AUTOMATIC_UPLOAD: "1"', watch)
        self.assertIn("vars.REQUIRE_QA_APPROVAL || 'true'", watch)
        self.assertIn("artifact: ${{ steps.fmt.outputs.format }}-${{ github.run_number }}", live)
        self.assertIn("artifact: buffer-fill-${{ github.run_id }}", watch)
        self.assertIn("artifact: buffer-refill-${{ github.run_id }}", refill)
        self.assertIn("run/final.mp4", refill)
        self.assertNotIn("publish.py", refill)
        self.assertNotIn("--publish", refill)


if __name__ == "__main__":
    unittest.main()
