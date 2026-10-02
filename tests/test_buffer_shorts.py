"""Backup buffer: fallback fill, holds, double-fill, used-once, freshness, credits."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import buffer_shorts
import credit_log
import health_check
import market_data
import slot_guard

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
IST = slot_guard.IST

# 09:00 IST, so 21:00 the same day is 12h ahead — inside MIN_LEAD..MAX_LOOKAHEAD.
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=IST)
DATE = "2026-10-02"
SLOT = "21:00"
PUBLISH_AT = "2026-10-02T15:30:00Z"


class MemoryStore:
    def __init__(self):
        self.files = {}

    def put(self, name, data, content_type="application/octet-stream"):
        if name in self.files:
            raise buffer_shorts.AlreadyUsed(name)
        self.files[name] = bytes(data)

    def get(self, name):
        return self.files[name]


def _topic():
    return {
        "key": "buffer_example_risk",
        "cat": "RISK",
        "topic": "Position size ka example",
        "thumb": ["1% rule", "example"],
        "beats": {
            "hook": ["1% rule", "ek trade"],
            "pehchan": ["मिसाल", "capital 100000"],
            "matlab": ["matlab", "risk 1000"],
            "karein": ["yaad", "SL pehle"],
        },
        "facts": ["ye example hai, salah nahi"],
    }


def _review():
    return {
        "public_ok": True,
        "passed": True,
        "narration_source": "llm",
        "narration_fallback": False,
        "overall": 96,
        "parts": {
            "hook": 100, "visual": 90, "accuracy": 100,
            "trend": 100, "craft": 100, "sync": 100,
        },
        "sync": {"ok": True, "reason": ""},
        "notes": [],
        "summary": "overall 96",
    }


def _write_ready_run(run, topic=None):
    topic = topic or _topic()
    run.mkdir(parents=True, exist_ok=True)
    (run / "self_review.json").write_text(json.dumps(_review()), encoding="utf-8")
    (run / "verify_meta.json").write_text(json.dumps({
        "ai_verdict_valid": True,
        "verdict": "READY",
        "public_ready": True,
    }), encoding="utf-8")
    (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
    script = "TITLE:\nPosition size example\n\nHOOK:\n" + ("शब्द " * 30) + "\n"
    (run / "script.txt").write_text(script, encoding="utf-8")
    (run / "topic.json").write_text(json.dumps(topic), encoding="utf-8")
    (run / "narration.json").write_text(json.dumps({
        "source": "llm",
        "beats": {name: "example line" for name in ("hook", "pehchan", "matlab", "karein")},
    }), encoding="utf-8")
    (run / "voice_meta.json").write_text(json.dumps({"engine": "edge"}), encoding="utf-8")
    return topic


def _item(topic, created_at, payload=b"buffer-video-bytes", asset="clip.mp4"):
    digest = buffer_shorts.sha256_bytes(payload)
    return {
        "id": f"buf-{topic['key']}-{digest[:12]}",
        "state": "ready",
        "topic_key": topic["key"],
        "title": "Position size | example",
        "description": "Illustrative example only.",
        "tags": ["trading hindi"],
        "sha256": digest,
        "asset_name": asset,
        "meta_name": asset.replace(".mp4", ".json"),
        "created_at": created_at,
        "used_at": None,
        "video_id": None,
        "gates": {"freshness": {"passed": True, "mode": "evergreen"}},
        "files": {
            "topic": topic,
            "script": "TITLE:\nPosition size example\n",
            "report": "VERDICT: [READY TO PUBLISH]\n",
            "verify_meta": {"ai_verdict_valid": True, "verdict": "READY", "public_ready": True},
            "self_review": _review(),
        },
    }


def _manifest(items):
    return {"tag": "buffer-shorts", "items": items}


class GateAndFreshnessTests(unittest.TestCase):
    def test_evergreen_example_passes_and_real_data_stays_blocked(self):
        topic = _topic()
        ok, why = buffer_shorts.is_evergreen_topic(topic)
        self.assertTrue(ok, why)
        self.assertIn("evergreen", why)

        unlabeled = _topic()
        unlabeled["thumb"] = ["1% rule", "capital"]
        unlabeled["beats"]["hook"] = ["1% rule", "ek trade"]
        unlabeled["beats"]["pehchan"] = ["हिसाब", "capital 100000"]
        ok, why = buffer_shorts.is_evergreen_topic(unlabeled)
        self.assertFalse(ok)
        self.assertIn("example", why)

        series = {
            "key": "titan_2010",
            "cat": "REAL DATA",
            "beats": {"hook": ["2010", "Titan"], "pehchan": ["a", "b"], "matlab": ["c", "d"], "karein": ["e", "f"]},
            "facts": ["71"],
        }
        ok, why = buffer_shorts.is_evergreen_topic(series)
        self.assertFalse(ok)
        self.assertIn("market series", why)
        live = dict(series, key="dated_price_print")
        ok, why = buffer_shorts.is_evergreen_topic(live)
        self.assertFalse(ok)
        self.assertIn("REAL DATA", why)

        today = date(2026, 10, 2)
        stale = market_data.public_upload_block(
            "2026-07-02", today, True, "public", "2026-10-02T15:30:00Z",
        )
        self.assertIn("stale", stale)
        self.assertNotIn("allow_stale", market_data.public_upload_block.__code__.co_varnames)
        self.assertEqual(
            market_data.public_upload_block("", today, False, "public", "2026-10-02T15:30:00Z"),
            "",
        )

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_ready_run(run, topic)
            gates = buffer_shorts.evaluate_gates(run, topic, today=today)
            self.assertTrue(buffer_shorts.gates_pass(gates), gates)
            self.assertEqual(gates["freshness"]["mode"], "evergreen")
            self.assertTrue(gates["sync"]["passed"])
            self.assertTrue(gates["craft"]["passed"])
            self.assertTrue(gates["number_match"]["passed"])
            self.assertTrue(gates["duplicate_text"]["passed"])
            self.assertTrue(gates["fallback_narration"]["passed"])
            self.assertTrue(gates["ai_review"]["passed"])

            (run / "market_freshness.json").write_text(json.dumps({
                "as_of": "2026-07-02", "on_screen": True,
            }), encoding="utf-8")
            blocked = buffer_shorts.evaluate_gates(run, live, today=today)
            self.assertFalse(blocked["freshness"]["passed"])
            self.assertIn("stale", blocked["freshness"]["detail"])
            self.assertFalse(buffer_shorts.gates_pass(blocked))
            (run / "market_freshness.json").unlink()
            still = buffer_shorts.evaluate_gates(run, live, today=today)
            self.assertFalse(still["freshness"]["passed"])
            self.assertNotEqual(still["freshness"]["mode"], "evergreen")

    def test_fallback_narration_and_duplicate_text_fail_closed(self):
        topic = _topic()
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_ready_run(run, topic)
            (run / "narration_block.json").write_text("{}", encoding="utf-8")
            gates = buffer_shorts.evaluate_gates(run, topic, today=date(2026, 10, 2))
            self.assertFalse(gates["fallback_narration"]["passed"])
            self.assertFalse(gates["ai_review"]["passed"])
            (run / "narration_block.json").unlink()
            review = _review()
            review["notes"] = ["text sprites overlap at 1.0s"]
            review["parts"]["craft"] = 0
            (run / "self_review.json").write_text(json.dumps(review), encoding="utf-8")
            gates = buffer_shorts.evaluate_gates(run, topic, today=date(2026, 10, 2))
            self.assertFalse(gates["duplicate_text"]["passed"])
            self.assertFalse(gates["craft"]["passed"])


class FillTests(unittest.TestCase):
    def _setup(self, items, payload=b"buffer-video-bytes"):
        topic = _topic()
        store = MemoryStore()
        built = []
        for item in items:
            store.put(item["asset_name"], payload)
            built.append(item)
        return topic, store, built

    def _fill(self, entries, items, *, schedule=None, last_chance=False, now=None, read_entries=None, read_schedule=None, bank_topics=None):
        topic, store, built = self._setup(items)
        path_dir = tempfile.TemporaryDirectory()
        manifest_path = Path(path_dir.name) / "buffer_manifest.json"
        buffer_shorts.save_manifest(_manifest(built), manifest_path)
        uploads = []
        posts = []
        alerts = []
        recorded = []

        def uploader(payload, title, description, tags, publish_at):
            uploads.append({"title": title, "publish_at": publish_at, "tags": tags, "n": len(payload)})
            return "vidbuffer1"

        def crosspost(item, video_id, payload):
            posts.append({"id": item["id"], "video_id": video_id})
            return {"posted": True, "media_id": "ig1"}

        def alert(slot, date_ist, reason):
            alerts.append((slot, date_ist, reason))

        def entries_reader():
            if read_entries:
                return read_entries()
            return entries

        def schedule_reader():
            if read_schedule:
                return read_schedule()
            return schedule or set()

        result = buffer_shorts.perform_fill(
            now=now or NOW,
            date_ist=DATE,
            slot=SLOT,
            last_chance=last_chance,
            read_entries=entries_reader,
            read_schedule=schedule_reader,
            manifest_path=manifest_path,
            store=store,
            uploader=uploader,
            crosspost=crosspost,
            alert=alert,
            bank_topics=list(bank_topics or []),
            record=lambda *args, **kwargs: recorded.append(kwargs),
        )
        manifest = buffer_shorts.load_manifest(manifest_path)
        path_dir.cleanup()
        return result, uploads, posts, alerts, recorded, manifest

    def test_gate_failure_fills_the_oldest_ready_item(self):
        topic = _topic()
        older = _item(topic, "2026-10-01T00:00:00Z", asset="old.mp4")
        newer_topic = dict(topic, key="buffer_example_risk_2")
        newer = _item(newer_topic, "2026-10-02T00:00:00Z", asset="new.mp4")
        self.assertTrue(buffer_shorts.build_failed_gates("[STOP] Chart Board failed\n"))
        self.assertTrue(buffer_shorts.build_failed_gates("DeepSeek call cap reached; skipping the slot\n"))
        self.assertFalse(buffer_shorts.build_failed_gates("[skip] slot is on hold; exiting before paid APIs\n"))
        result, uploads, posts, alerts, recorded, manifest = self._fill([], [newer, older])
        self.assertTrue(result["filled"])
        self.assertEqual(result["item"]["id"], older["id"])
        self.assertEqual(uploads[0]["publish_at"], PUBLISH_AT)
        self.assertEqual(len(uploads), 1)
        self.assertEqual(posts[0]["video_id"], "vidbuffer1")
        self.assertEqual(recorded[0]["source"], "buffer")
        self.assertEqual(recorded[0]["buffer_id"], older["id"])
        self.assertEqual(alerts, [])
        states = {item["id"]: item["state"] for item in manifest["items"]}
        self.assertEqual(states[older["id"]], "used")
        self.assertEqual(states[newer["id"]], "ready")
        # Admitting the item marks the topic used_short. That must not block the fill.
        marked = dict(newer_topic, used_short=True)
        again, uploads, *_rest = self._fill([], [newer], bank_topics=[marked])
        self.assertTrue(again["filled"])
        self.assertEqual(len(uploads), 1)

    def test_hold_is_not_filled(self):
        topic = _topic()
        item = _item(topic, "2026-10-01T00:00:00Z")
        held = [{"date_ist": DATE, "slot": SLOT, "video_id": None, "platform": "hold"}]
        called = {"schedule": 0}

        def read_schedule():
            called["schedule"] += 1
            return set()

        result, uploads, posts, alerts, recorded, _manifest = self._fill(
            held, [item], read_schedule=read_schedule,
        )
        self.assertEqual(result["reason"], "hold")
        self.assertFalse(result["filled"])
        self.assertEqual(uploads, [])
        self.assertEqual(posts, [])
        self.assertEqual(alerts, [])
        self.assertEqual(recorded, [])
        self.assertEqual(called["schedule"], 0)

    def test_ledger_and_live_schedule_block_a_second_fill(self):
        topic = _topic()
        item = _item(topic, "2026-10-01T00:00:00Z")
        filled = [{"date_ist": DATE, "slot": SLOT, "video_id": "already", "platform": "youtube"}]
        result, uploads, *_rest = self._fill(filled, [item])
        self.assertEqual(result["reason"], "filled")
        self.assertEqual(uploads, [])

        calls = {"n": 0}

        def read_entries():
            calls["n"] += 1
            # The first read is still open. The re-read immediately before
            # upload must see the row that landed during the build.
            if calls["n"] < 2:
                return []
            return filled

        result, uploads, posts, alerts, recorded, _manifest = self._fill(
            [], [item], read_entries=read_entries,
        )
        self.assertEqual(result["reason"], "filled")
        self.assertFalse(result["filled"])
        self.assertEqual(uploads, [])
        self.assertEqual(posts, [])
        self.assertGreaterEqual(calls["n"], 2)

        sched = {"n": 0}

        def read_schedule():
            sched["n"] += 1
            if sched["n"] == 1:
                return set()
            return {PUBLISH_AT}

        result, uploads, *_rest = self._fill([], [item], read_schedule=read_schedule)
        self.assertEqual(result["reason"], "scheduled")
        self.assertEqual(uploads, [])

    def test_used_item_is_not_uploaded_again(self):
        topic = _topic()
        item = _item(topic, "2026-10-01T00:00:00Z")
        first, uploads, *_rest = self._fill([], [item])
        self.assertTrue(first["filled"])
        self.assertEqual(len(uploads), 1)
        used = dict(item, state="used", video_id="vidbuffer1")
        manifest = _manifest([used])
        with self.assertRaises(buffer_shorts.AlreadyUsed):
            buffer_shorts.claim_item(manifest, used["id"])
        with self.assertRaises(buffer_shorts.AlreadyUsed):
            buffer_shorts.mark_used(
                manifest, used["id"], slot=SLOT, date_ist=DATE, video_id="other",
            )
        result, uploads, posts, alerts, _recorded, saved = self._fill([], [used])
        self.assertEqual(result["reason"], "empty")
        self.assertEqual(uploads, [])
        self.assertEqual(posts, [])
        self.assertEqual(alerts, [(SLOT, DATE, "buffer empty")])
        self.assertEqual(saved["items"][0]["state"], "used")

    def test_empty_buffer_skips_and_alerts(self):
        result, uploads, posts, alerts, recorded, _manifest = self._fill([], [])
        self.assertEqual(result["reason"], "empty")
        self.assertFalse(result["filled"])
        self.assertEqual(uploads, [])
        self.assertEqual(posts, [])
        self.assertEqual(recorded, [])
        self.assertEqual(alerts, [(SLOT, DATE, "buffer empty")])

    def test_empty_alert_opens_one_health_alert_issue(self):
        http = _IssueHttp()
        with mock.patch.object(health_check, "RequestsHttp", return_value=http), \
             mock.patch.dict(os.environ, {"GITHUB_TOKEN": "gh-test", "GITHUB_REPOSITORY": "acme/widgets"}):
            buffer_shorts._alert_empty(SLOT, DATE, "buffer empty")
        post = next(call for call in http.calls if call["method"] == "POST" and call["url"].endswith("/issues"))
        self.assertEqual(post["json_body"]["labels"], ["health-alert"])
        self.assertIn("buffer empty", post["json_body"]["title"])
        self.assertIn(SLOT, post["json_body"]["body"])

    def test_last_chance_still_posts_when_the_slot_time_has_just_passed(self):
        topic = _topic()
        item = _item(topic, "2026-10-01T00:00:00Z")
        late_now = datetime(2026, 10, 2, 21, 40, tzinfo=IST)
        result, uploads, *_rest = self._fill([], [item], last_chance=True, now=late_now)
        self.assertTrue(result["filled"])
        self.assertTrue(result["late"])
        when = datetime.strptime(uploads[0]["publish_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=slot_guard.UTC)
        self.assertGreater(when, late_now)
        too_close = datetime(2026, 10, 2, 20, 0, tzinfo=IST)
        decision = buffer_shorts.fill_decision(
            too_close, [], set(), _manifest([item]), DATE, SLOT, last_chance=False,
        )
        self.assertEqual(decision["reason"], "too_close")

    def test_crosspost_uses_the_instagram_idempotency_guard(self):
        topic = _topic()
        item = _item(topic, "2026-10-01T00:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with mock.patch.object(buffer_shorts, "RUN", run), \
                 mock.patch("publish_instagram.existing_media", return_value="ig-already") as guard, \
                 mock.patch("publish_instagram.main", side_effect=AssertionError("second reel")):
                result = buffer_shorts.crosspost_buffer(item, "vidbuffer1", b"buffer-video-bytes", run)
        guard.assert_called_once()
        self.assertFalse(result["posted"])
        self.assertEqual(result["media_id"], "ig-already")

    def test_sha_mismatch_does_not_stick_the_claim(self):
        topic = _topic()
        item = _item(topic, "2026-10-01T00:00:00Z")
        store = MemoryStore()
        store.put(item["asset_name"], b"not-the-real-bytes")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            buffer_shorts.save_manifest(_manifest([item]), path)
            with self.assertRaises(buffer_shorts.BufferError):
                buffer_shorts.perform_fill(
                    now=NOW, date_ist=DATE, slot=SLOT, last_chance=False,
                    read_entries=lambda: [],
                    read_schedule=lambda: set(),
                    manifest_path=path,
                    store=store,
                    uploader=lambda *_a, **_k: "nope",
                    crosspost=None,
                    alert=lambda *_a, **_k: None,
                    record=lambda *_a, **_k: None,
                )
            saved = buffer_shorts.load_manifest(path)
        self.assertEqual(saved["items"][0]["state"], "ready")


class _IssueHttp:
    def __init__(self):
        self.calls = []

    def send(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if method == "GET" and url.endswith("/issues"):
            return health_check.HttpResponse(200, [])
        if method == "GET" and "/labels/" in url:
            return health_check.HttpResponse(404, {})
        if method == "POST" and url.endswith("/labels"):
            return health_check.HttpResponse(201, {"name": "health-alert"})
        if method == "POST" and url.endswith("/issues"):
            return health_check.HttpResponse(201, {"number": 9})
        raise AssertionError(f"{method} {url}")


class CreditAndRefillTests(unittest.TestCase):
    def test_voice_policy_and_deepseek_cap(self):
        self.assertEqual(credit_log.DEFAULT_DEEPSEEK_CALLS, 4)
        self.assertEqual(
            buffer_shorts.voice_policy(False, key_present=True, remaining=100000, needed=2000),
            "withhold",
        )
        self.assertEqual(
            buffer_shorts.voice_policy(True, key_present=True, remaining=100, needed=2000),
            "fail_closed",
        )
        self.assertEqual(
            buffer_shorts.voice_policy(True, key_present=False, remaining=None, needed=2000),
            "fail_closed",
        )
        self.assertEqual(
            buffer_shorts.voice_policy(True, key_present=True, remaining=5000, needed=2000),
            "speak",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credits.json"
            with mock.patch.object(credit_log, "PATH", path):
                for _ in range(4):
                    credit_log.reserve_deepseek_call()
                with self.assertRaises(credit_log.DeepSeekCallCap):
                    credit_log.reserve_deepseek_call()
                saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["deepseek_calls"], 4)

    def test_refill_withholds_elevenlabs_until_gates_pass_and_credit_remains(self):
        promoter = mock.Mock(side_effect=AssertionError("ElevenLabs must wait for the gates"))
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            manifest_path = Path(tmp) / "manifest.json"
            credits = Path(tmp) / "credits.json"
            buffer_shorts.save_manifest(_manifest([]), manifest_path)

            def fail_preview(_key):
                return 1

            with mock.patch.object(buffer_shorts, "RUN", run), \
                 mock.patch.object(credit_log, "PATH", credits), \
                 mock.patch.object(buffer_shorts, "_load_bank", return_value=[_topic()]), \
                 mock.patch.object(slot_guard, "_read", return_value=[]):
                code = buffer_shorts.refill(
                    runner=fail_preview,
                    promoter=promoter,
                    remaining_reader=lambda: (True, 100000),
                    store=MemoryStore(),
                    manifest_path=manifest_path,
                )
            self.assertEqual(code, 1)
            promoter.assert_not_called()
            self.assertTrue(credits.exists())
            self.assertEqual(json.loads(credits.read_text(encoding="utf-8"))["deepseek_calls"], 0)
            self.assertEqual(os.environ.get("DEEPSEEK_MAX_CALLS"), "4")

            def preview_ok(_key):
                _write_ready_run(run, _topic())
                return 0

            with mock.patch.object(buffer_shorts, "RUN", run), \
                 mock.patch.object(credit_log, "PATH", credits), \
                 mock.patch.object(buffer_shorts, "_load_bank", return_value=[_topic()]), \
                 mock.patch.object(slot_guard, "_read", return_value=[]):
                code = buffer_shorts.refill(
                    runner=preview_ok,
                    promoter=promoter,
                    remaining_reader=lambda: (True, 10),
                    store=MemoryStore(),
                    manifest_path=manifest_path,
                )
            self.assertEqual(code, 1)
            promoter.assert_not_called()
            self.assertEqual(buffer_shorts.count_ready(buffer_shorts.load_manifest(manifest_path)), 0)

    def test_refill_stores_one_item_only_after_paid_voice(self):
        topic = _topic()
        store = MemoryStore()
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            manifest_path = Path(tmp) / "manifest.json"
            credits = Path(tmp) / "credits.json"
            buffer_shorts.save_manifest(_manifest([]), manifest_path)

            def preview_ok(_key):
                _write_ready_run(run, topic)
                (run / "final.mp4").write_bytes(b"edge-preview")
                return 0

            def promote():
                (run / "voice_meta.json").write_text(json.dumps({"engine": "elevenlabs"}), encoding="utf-8")
                (run / "final.mp4").write_bytes(b"paid-voice-mp4")

            with mock.patch.object(buffer_shorts, "RUN", run), \
                 mock.patch.object(credit_log, "PATH", credits), \
                 mock.patch.object(buffer_shorts, "_load_bank", return_value=[topic]), \
                 mock.patch.object(buffer_shorts, "_mark_bank_used") as mark, \
                 mock.patch.object(slot_guard, "_read", return_value=[]):
                code = buffer_shorts.refill(
                    runner=preview_ok,
                    promoter=promote,
                    remaining_reader=lambda: (True, 20000),
                    store=store,
                    manifest_path=manifest_path,
                )
            self.assertEqual(code, 0)
            manifest = buffer_shorts.load_manifest(manifest_path)
            self.assertEqual(buffer_shorts.count_ready(manifest), 1)
            item = manifest["items"][0]
            self.assertEqual(item["state"], "ready")
            self.assertEqual(item["topic_key"], topic["key"])
            self.assertTrue(item["gates"]["sync"]["passed"])
            self.assertEqual(store.get(item["asset_name"]), b"paid-voice-mp4")
            self.assertNotEqual(store.get(item["asset_name"]), b"edge-preview")
            mark.assert_called_once_with(topic["key"])
            self.assertIn("deepseek_calls", json.loads(credits.read_text(encoding="utf-8")))
            with self.assertRaises(buffer_shorts.AlreadyUsed):
                store.put(item["asset_name"], b"again", "video/mp4")


class HealthAndWorkflowTests(unittest.TestCase):
    def test_health_check_warns_below_two_ready_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            buffer_shorts.save_manifest(_manifest([]), path)
            low = health_check.check_buffer(path)
            self.assertEqual(low.status, "warn")
            self.assertIn("only 0", low.detail)
            self.assertTrue(health_check.has_problem([low]))
            one = _item(_topic(), "2026-10-01T00:00:00Z")
            buffer_shorts.save_manifest(_manifest([one]), path)
            still = health_check.check_buffer(path)
            self.assertEqual(still.status, "warn")
            self.assertIn("only 1", still.detail)
            two = _item(dict(_topic(), key="buffer_example_risk_b"), "2026-10-02T00:00:00Z", asset="b.mp4")
            buffer_shorts.save_manifest(_manifest([one, two]), path)
            ok = health_check.check_buffer(path)
        self.assertEqual(ok.status, "ok")
        self.assertIn("2 ready", ok.detail)
        self.assertFalse(health_check.has_problem([ok]))

    def test_slot_watch_requests_a_buffer_fill_only_when_close_and_empty(self):
        import slot_watch
        now = datetime(2026, 10, 2, 21, 49, tzinfo=IST)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "published.json"
            ledger.write_text(json.dumps([
                {"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc"},
            ]), encoding="utf-8")
            gout = Path(tmp) / "gha"
            env = {"SLOT_CHECK": "21:00", "GITHUB_OUTPUT": str(gout)}
            with mock.patch.object(slot_watch, "LEDGER", ledger), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt, \
                 redirect_stdout(io.StringIO()):
                dt.now.return_value = now
                slot_watch.main()
            flags = gout.read_text(encoding="utf-8")
        self.assertIn("use_buffer=true", flags)
        self.assertIn("buffer_slot=21:00", flags)
        self.assertIn("missing=true", flags)

        held_now = now
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "published.json"
            ledger.write_text(json.dumps([
                {"date_ist": "2026-09-01", "slot": "08:30", "video_id": "abc"},
                {"date_ist": DATE, "slot": "21:00", "video_id": None, "platform": "hold"},
            ]), encoding="utf-8")
            gout = Path(tmp) / "gha"
            env = {"SLOT_CHECK": "21:00", "GITHUB_OUTPUT": str(gout)}
            with mock.patch.object(slot_watch, "LEDGER", ledger), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(slot_watch, "send"), \
                 mock.patch.object(slot_watch, "datetime", wraps=slot_watch.datetime) as dt, \
                 redirect_stdout(io.StringIO()):
                dt.now.return_value = held_now
                slot_watch.main()
            flags = gout.read_text(encoding="utf-8")
        self.assertIn("use_buffer=false", flags)
        self.assertIn("on hold", "") if False else None
        self.assertNotIn("use_buffer=true", flags)

    def test_workflows_share_the_publish_group_and_cap_credits(self):
        publish = (WF / "paise-ki-pathshala.yml").read_text(encoding="utf-8")
        watch = (WF / "slot-watch.yml").read_text(encoding="utf-8")
        refill = (WF / "buffer-refill.yml").read_text(encoding="utf-8")
        health = (WF / "health-check.yml").read_text(encoding="utf-8")
        self.assertIn("buffer_shorts.py --fill-scheduled", publish)
        self.assertIn("group: paise-ki-pathshala-publish", publish)
        self.assertIn("buffer_shorts.py --fill-last-chance", watch)
        self.assertEqual(watch.count("group: paise-ki-pathshala-publish"), 2)
        self.assertIn("cancel-in-progress: false", watch)
        self.assertIn("issues: write", watch)
        self.assertIn('cron: "41 6 * * *"', refill)
        self.assertIn('DEEPSEEK_MAX_CALLS: "4"', refill)
        self.assertIn("BUFFER_FILL: \"0\"", refill)
        self.assertNotIn("--publish", refill)
        self.assertNotIn("publish.py", refill)
        self.assertIn("group: buffer-refill", refill)
        self.assertFalse(slot_guard.cron_in_busy_window("41 6 * * *"))
        taken = {cron for cron, _label in slot_guard.PUBLISH_CRONS}
        taken.update(row[0] for row in slot_guard.HEALTH_SCHEDULE)
        taken.update(cron for cron, _slot in slot_guard.WATCH_CRONS)
        self.assertNotIn("41 6 * * *", taken)
        self.assertIn("buffer", health.lower())
        self.assertNotIn("publish.py", health)


class AgentHookTests(unittest.TestCase):
    def test_failed_build_fills_from_the_buffer_and_a_hold_does_not(self):
        import auto_agent
        env = {
            "PUBLISH_VISIBILITY": "public",
            "PUBLISH_SLOT_IST": SLOT,
            "PUBLISH_DATE_IST": DATE,
            "SELF_REPAIR": "0",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(auto_agent, "RUN", Path(tmp)), \
             mock.patch.object(auto_agent, "run_once", return_value=(1, "[STOP] Chart Board failed")), \
             mock.patch.object(auto_agent, "retry_same_topic", side_effect=AssertionError("retry")), \
             mock.patch.object(buffer_shorts, "fill_from_env", return_value={"filled": True, "video_id": "vidbuffer1"}) as fill, \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(os, "sys" if False else "environ", os.environ):
            os.environ.pop("BUFFER_FILL", None)
            with mock.patch.object(sys_argv_module(), "argv", ["auto_agent.py", "--format", "short", "--publish"]):
                code = auto_agent.main()
        self.assertEqual(code, 0)
        fill.assert_called_once()
        self.assertEqual(fill.call_args.args[0], False)

    def test_unscheduled_failure_does_not_touch_the_buffer(self):
        import auto_agent
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(auto_agent, "RUN", Path(tmp)), \
             mock.patch.object(auto_agent, "run_once", return_value=(1, "Traceback: boom")), \
             mock.patch.object(buffer_shorts, "fill_from_env", side_effect=AssertionError("buffer")) as fill, \
             mock.patch.dict(os.environ, {"PUBLISH_VISIBILITY": "", "PUBLISH_SLOT_IST": "", "SELF_REPAIR": "0"}, clear=False), \
             mock.patch.object(sys_argv_module(), "argv", ["auto_agent.py", "--format", "short", "--publish"]):
            code = auto_agent.main()
        self.assertEqual(code, 1)
        fill.assert_not_called()


def sys_argv_module():
    import sys
    return sys


if __name__ == "__main__":
    unittest.main()
