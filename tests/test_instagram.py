import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import publish_instagram as IG

SCRIPT = """TITLE:
Bull Call Spread: bullish view में भी risk तय — आसान भाषा में

HOOK (0-3 sec):
Bull Call Spread वह strategy है जहाँ bullish होकर भी आपका सबसे बड़ा loss पहले से तय रहता है।

BODY:
[SEGMENT 1: PEHCHAN]
कुछ भी।

CTA:
अगला कौन सा market topic समझना है? Comment में लिखो—ऐसी आसान सीख के लिए जुड़े रहो।

DISCLAIMER LINE:
सिर्फ़ जानकारी, निवेश सलाह नहीं।

FACT LEDGER:
- buy call और higher-strike sell call का structure
- max loss और max profit पहले calculate करें
- यह educational framework है, किसी शेयर/ट्रेड की खरीद-बिक्री सलाह नहीं
"""
TOPIC = {"key": "option_bull_call_spread", "cat": "OPTIONS",
         "seo": "Bull Call Spread — bullish view में defined-risk strategy",
         "thumb": ["Bull Call Spread", "आसान भाषा में समझो"]}


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        (self.run / "script.txt").write_text(SCRIPT, encoding="utf-8")
        (self.run / "topic.json").write_text(json.dumps(TOPIC, ensure_ascii=False), encoding="utf-8")
        self.p = mock.patch.object(IG, "RUN", self.run)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_structure(self):
        c = IG.build_caption()
        first = c.split("\n")[0]
        self.assertTrue(first.startswith("Bull Call Spread"))          # keyword first
        self.assertIn("📌 याद रखो", c)
        self.assertIn("Save", c)
        self.assertIn("निवेश सलाह नहीं", c)
        self.assertNotRegex(c, r"https?://")                            # IG caption links not tappable
        self.assertLessEqual(len(c), 2200)

    def test_hashtags_keep_main_selection(self):
        tags = re.findall(r"#\w+", IG.build_caption())
        self.assertIn("#PaiseKiPathshala", tags)
        self.assertIn("#OptionsTrading", tags)
        self.assertEqual(len(tags), len(set(tags)))

    def test_disclaimer_fact_not_repeated_as_bullet(self):
        self.assertNotIn("• यह educational framework", IG.build_caption())


class VerdictGateTests(unittest.TestCase):
    """A failed-review video must never become a Reel, even with IG_CROSSPOST_FORCE."""

    def _run(self, report_text):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "final.mp4").write_bytes(b"x" * 2000)
            if report_text is not None:
                (run / "report.md").write_text(report_text, encoding="utf-8")
            env = {"IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1", "VIDEO_FORMAT": "short"}
            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", env), \
                 mock.patch.object(IG, "video_format", return_value="short"), \
                 mock.patch.object(IG, "host_mp4", side_effect=AssertionError("must not upload")) as host, \
                 mock.patch.object(IG, "publish_reel", side_effect=AssertionError("must not publish")):
                with self.assertRaises(SystemExit) as cm:
                    IG.main()
                return cm.exception.code, host.called

    def test_needs_fix_verdict_is_skipped(self):
        code, uploaded = self._run("VERDICT: [NEEDS FIX — 2 items]")
        self.assertEqual(code, 0)
        self.assertFalse(uploaded)

    def test_missing_report_is_skipped(self):
        code, uploaded = self._run(None)
        self.assertEqual(code, 0)
        self.assertFalse(uploaded)


class CoverTests(unittest.TestCase):
    def test_grid_safe_cover_keeps_design_inside_3x4(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "t.jpg", Path(d) / "c.jpg"
            im = Image.new("RGB", (1080, 1920), (0, 0, 0))
            im.paste((255, 255, 255), (0, 100, 1080, 200))            # title band near the top
            im.save(src)
            IG.grid_safe_cover(src, out)
            c = Image.open(out)
            self.assertEqual(c.size, (1080, 1920))
            # the title band must now sit inside the grid crop (y 240..1680)
            band = [y for y in range(1920) if c.getpixel((540, y))[0] > 200]
            self.assertTrue(band and min(band) >= 240 and max(band) <= 1680, (band[:1], band[-1:]))


TOKEN = "ghp_TESTTOKEN_DO_NOT_LEAK_123456"
REPO = "bazarblast140-bot/HEYGEN--INSTAGRAM-"
RELEASE = {
    "id": 400567125,
    "tag_name": "ig-reel-2026-10-01",
    "assets": [],
    "upload_url": f"https://uploads.github.com/repos/{REPO}/releases/400567125/assets{{?name,label}}",
}


def _release_json(release=None):
    return json.dumps(release or RELEASE).encode()


class MediaTagTests(unittest.TestCase):
    def test_tag_uses_utc_not_ist(self):
        from datetime import datetime, timedelta, timezone
        utc = datetime(2026, 10, 1, 20, 24, tzinfo=timezone.utc)
        self.assertEqual(IG.media_tag(utc), "ig-reel-2026-10-01")
        ist = timezone(timedelta(hours=5, minutes=30))
        # 20:24 UTC is 01:54 IST the next calendar day. The tag stays on the UTC date.
        late = datetime(2026, 10, 2, 1, 54, tzinfo=ist)
        self.assertEqual(IG.media_tag(late), "ig-reel-2026-10-01")


class HostingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "clip.mp4"
        self.file.write_bytes(b"mp4-bytes")
        self.env = {
            "MEDIA_REPO": REPO,
            "MEDIA_REPO_TOKEN": TOKEN,
            "ACTIONS_TOKEN_FOR_COMPARE": "ghp_OTHER_TOKEN_VALUE_999999",
            "GITHUB_TOKEN": "",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, handler):
        calls = []

        def request(url, method="GET", data=None, headers=None, timeout=120):
            headers = headers or {}
            calls.append((method, url, headers.get("Authorization"), data))
            return handler(method, url, calls)

        stdout, stderr = __import__("io").StringIO(), __import__("io").StringIO()
        with mock.patch.dict("os.environ", self.env, clear=False), \
             mock.patch.object(IG, "_github_request", side_effect=request), \
             mock.patch.object(IG, "_url_serves", return_value=True), \
             mock.patch.object(IG, "media_tag", return_value="ig-reel-2026-10-01"), \
             mock.patch.object(IG.time, "sleep"), \
             mock.patch("sys.stdout", stdout), \
             mock.patch("sys.stderr", stderr):
            try:
                url = IG.host_release_asset(self.file, name="paise-short-1790890344.mp4", content_type="video/mp4")
            except SystemExit as e:
                return calls, e.code, stdout.getvalue() + stderr.getvalue(), None
        return calls, 0, stdout.getvalue() + stderr.getvalue(), url

    def _auth(self, calls):
        return {auth for _m, _u, auth, _d in calls}

    def test_get_and_upload_use_the_same_token(self):
        def handler(method, url, calls):
            if method == "GET" and "/releases/tags/" in url:
                return 200, _release_json()
            if method == "POST" and "uploads.github.com" in url:
                body = json.dumps({"browser_download_url": "https://github.com/example/a.mp4"}).encode()
                return 201, body
            raise AssertionError(method + " " + url)

        calls, code, err, url = self._run(handler)
        self.assertEqual(code, 0, err)
        self.assertEqual(url, "https://github.com/example/a.mp4")
        self.assertEqual(self._auth(calls), {f"Bearer {TOKEN}"})
        self.assertNotIn(TOKEN, err)
        self.assertTrue(any(m == "GET" and "/releases/tags/ig-reel-2026-10-01" in u for m, u, _a, _d in calls))
        self.assertTrue(any(m == "POST" and "uploads.github.com" in u and "400567125" in u for m, u, _a, _d in calls))

    def test_missing_tag_is_created_then_uploaded(self):
        def handler(method, url, calls):
            if method == "GET" and "/releases/tags/" in url:
                raise IG.GitHubHttpError(404, url, "not found")
            if method == "POST" and url.endswith("/releases"):
                return 201, _release_json()
            if method == "POST" and "uploads.github.com" in url:
                return 201, json.dumps({"browser_download_url": "https://github.com/example/a.mp4"}).encode()
            raise AssertionError(method + " " + url)

        calls, code, err, url = self._run(handler)
        self.assertEqual(code, 0, err)
        self.assertEqual(url, "https://github.com/example/a.mp4")
        self.assertTrue(any(m == "POST" and u.endswith("/releases") for m, u, _a, _d in calls))

    def test_upload_404_reresolves_the_tag_and_retries(self):
        def handler(method, url, calls):
            if method == "GET" and "/releases/tags/" in url:
                return 200, _release_json()
            if method == "POST" and "uploads.github.com" in url:
                posts = [c for c in calls if c[0] == "POST" and "uploads.github.com" in c[1]]
                if len(posts) == 1:
                    raise IG.GitHubHttpError(404, url, f"missing {TOKEN}")
                return 201, json.dumps({"browser_download_url": "https://github.com/example/a.mp4"}).encode()
            raise AssertionError(method + " " + url)

        calls, code, err, url = self._run(handler)
        self.assertEqual(code, 0, err)
        self.assertEqual(url, "https://github.com/example/a.mp4")
        gets = [c for c in calls if c[0] == "GET" and "/releases/tags/" in c[1]]
        posts = [c for c in calls if c[0] == "POST" and "uploads.github.com" in c[1]]
        self.assertGreaterEqual(len(gets), 2)
        self.assertEqual(len(posts), 2)
        self.assertLess(calls.index(gets[1]), calls.index(posts[1]))
        self.assertNotIn(TOKEN, err)

    def test_upload_5xx_retries_then_succeeds(self):
        def handler(method, url, calls):
            if method == "GET" and "/releases/tags/" in url:
                return 200, _release_json()
            if method == "POST" and "uploads.github.com" in url:
                posts = [c for c in calls if c[0] == "POST" and "uploads.github.com" in c[1]]
                if len(posts) == 1:
                    raise IG.GitHubHttpError(502, url, "bad gateway")
                return 201, json.dumps({"browser_download_url": "https://github.com/example/a.mp4"}).encode()
            raise AssertionError(method + " " + url)

        calls, code, err, _url = self._run(handler)
        self.assertEqual(code, 0, err)
        posts = [c for c in calls if c[0] == "POST" and "uploads.github.com" in c[1]]
        self.assertEqual(len(posts), 2)

    def test_404_with_push_false_names_contents_write_and_hides_the_token(self):
        def handler(method, url, calls):
            if method == "GET" and url.endswith(f"/repos/{REPO}"):
                return 200, json.dumps({"permissions": {"push": False, "pull": True}}).encode()
            if method == "GET" and "/releases/tags/" in url:
                return 200, _release_json()
            if method == "POST" and "uploads.github.com" in url:
                raise IG.GitHubHttpError(404, url, f"nope {TOKEN}")
            raise AssertionError(method + " " + url)

        _calls, code, err, _url = self._run(handler)
        self.assertEqual(code, 1)
        self.assertIn("contents:write", err)
        self.assertIn("push=false", err)
        self.assertIn("HEYGEN--INSTAGRAM-", err)
        self.assertNotIn("missing release", err.lower())
        self.assertNotIn(TOKEN, err)
        self.assertNotIn("Bearer ghp_", err)

    def test_404_when_the_release_is_missing_is_not_blamed_on_contents_write(self):
        def handler(method, url, calls):
            if method == "GET" and url.endswith(f"/repos/{REPO}"):
                return 200, json.dumps({"permissions": {"push": True, "pull": True}}).encode()
            if method == "GET" and "/releases/tags/" in url:
                raise IG.GitHubHttpError(404, url, "absent")
            if method == "POST" and url.endswith("/releases"):
                raise IG.GitHubHttpError(404, url, "absent")
            if method == "POST" and "uploads.github.com" in url:
                raise IG.GitHubHttpError(404, url, "absent")
            raise AssertionError(method + " " + url)

        _calls, code, err, _url = self._run(handler)
        self.assertEqual(code, 1)
        self.assertIn("missing", err.lower())
        self.assertIn("not a contents:write error", err)
        self.assertNotIn(TOKEN, err)

    def test_401_says_the_token_was_rejected_without_printing_it(self):
        def handler(method, url, calls):
            if method == "GET" and "/releases/tags/" in url:
                return 200, _release_json()
            if method == "GET" and url.endswith(f"/repos/{REPO}"):
                raise IG.GitHubHttpError(401, url, f"bad credentials {TOKEN}")
            if method == "POST" and "uploads.github.com" in url:
                raise IG.GitHubHttpError(404, url, "no")
            raise AssertionError(method + " " + url)

        _calls, code, err, _url = self._run(handler)
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", err)
        self.assertIn("expired", err.lower())
        self.assertNotIn(TOKEN, err)

    def test_push_true_404_is_described_as_transient(self):
        def handler(method, url, calls):
            if method == "GET" and url.endswith(f"/repos/{REPO}"):
                return 200, json.dumps({"permissions": {"push": True, "pull": True}}).encode()
            if method == "GET" and "/releases/tags/" in url:
                return 200, _release_json()
            if method == "POST" and "uploads.github.com" in url:
                raise IG.GitHubHttpError(404, url, "flake")
            raise AssertionError(method + " " + url)

        _calls, code, err, _url = self._run(handler)
        self.assertEqual(code, 1)
        self.assertIn("push=true", err)
        self.assertIn("transient", err)
        self.assertNotIn(TOKEN, err)

    def test_identical_github_token_is_named_and_not_printed(self):
        self.env["ACTIONS_TOKEN_FOR_COMPARE"] = TOKEN
        buf = __import__("io").StringIO()
        with mock.patch.dict("os.environ", self.env, clear=False), mock.patch("sys.stdout", buf):
            _repo, token, source = IG.hosting_credentials()
        self.assertEqual(token, TOKEN)
        self.assertEqual(source, "GITHUB_TOKEN")
        text = buf.getvalue()
        self.assertIn("contents:write", text)
        self.assertIn("GITHUB_TOKEN", text)
        self.assertNotIn(TOKEN, text)


class WatchdogVerdictTests(unittest.TestCase):
    """The Reel gate stays fail-closed. A retry works only when the artifact carries the verdict files."""

    def test_ready_report_without_meta_still_skips(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "final.mp4").write_bytes(b"x" * 2000)
            (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
            env = {"IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1", "VIDEO_FORMAT": "short",
                   "TOPIC_KEY": "", "PIPELINE_MODE": "", "BOARD_QA": ""}
            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", env), \
                 mock.patch.object(IG, "video_format", return_value="short"), \
                 mock.patch.object(IG, "host_mp4", side_effect=AssertionError("must not upload")):
                with self.assertRaises(SystemExit) as cm:
                    IG.main()
        self.assertEqual(cm.exception.code, 0)

    def test_carried_verdict_files_reach_the_existing_gate(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "final.mp4").write_bytes(b"x" * 2000)
            (run / "script.txt").write_text(SCRIPT, encoding="utf-8")
            (run / "topic.json").write_text(json.dumps(TOPIC), encoding="utf-8")
            (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
            (run / "verify_meta.json").write_text(json.dumps({
                "ai_verdict_valid": True, "verdict": "READY", "public_ready": True,
            }), encoding="utf-8")
            (run / "self_review.json").write_text(json.dumps({"public_ok": True}), encoding="utf-8")
            env = {"IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1", "VIDEO_FORMAT": "short",
                   "TOPIC_KEY": "", "PIPELINE_MODE": "", "BOARD_QA": "",
                   "IG_USER_ID": "1789", "IG_ACCESS_TOKEN": "ig-token-test"}
            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", env), \
                 mock.patch.object(IG, "video_format", return_value="short"), \
                 mock.patch.object(IG, "fetch_recent_media", return_value=[]), \
                 mock.patch.object(IG, "host_mp4", return_value="https://example.com/a.mp4") as host, \
                 mock.patch.object(IG, "publish_reel", return_value="1789") as reel:
                IG.main()
            self.assertTrue(host.called)
            self.assertTrue(reel.called)
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "1789")
            marker = reel.call_args.args[1]
            self.assertIn("pkp:option_bull_call_spread", marker)


READY_META = {"ai_verdict_valid": True, "verdict": "READY", "public_ready": True}
ATR_TITLE = "SL बार-बार हिट? ATR से दूरी तय करो"


def _ready_tree(root: Path, *, topic_key="atr_sl", title=ATR_TITLE, youtube_id="Lx2vFMDO5ws", mp4=b"same-bytes"):
    run = root / "run"
    run.mkdir()
    (run / "final.mp4").write_bytes(mp4)
    (run / "script.txt").write_text(f"TITLE:\n{title}\n", encoding="utf-8")
    (run / "topic.json").write_text(json.dumps({"key": topic_key, "cat": "INDICATOR", "thumb": [title]}), encoding="utf-8")
    (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
    (run / "verify_meta.json").write_text(json.dumps(READY_META), encoding="utf-8")
    (run / "self_review.json").write_text(json.dumps({"public_ok": True}), encoding="utf-8")
    if youtube_id:
        (run / "uploaded.txt").write_text(youtube_id + "\n", encoding="utf-8")
    ledger_path = root / "published.json"
    ledger_path.write_text("[]\n", encoding="utf-8")
    return run, ledger_path


class IdempotencyTests(unittest.TestCase):
    """One video, one Reel. Every entry point goes through publish_instagram.main."""

    def _env(self):
        return {
            "IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1", "VIDEO_FORMAT": "short",
            "TOPIC_KEY": "", "PIPELINE_MODE": "", "BOARD_QA": "",
            "IG_USER_ID": "178900", "IG_ACCESS_TOKEN": "ig-token-secret",
        }

    def _run_main(self, run, ledger_path, live, host=None):
        host = host if host is not None else mock.Mock(side_effect=AssertionError("must not upload"))
        reel = mock.Mock(side_effect=AssertionError("must not publish"))
        with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", self._env()), \
             mock.patch.object(IG, "video_format", return_value="short"), \
             mock.patch("ledger.PATH", ledger_path), \
             mock.patch.object(IG, "fetch_recent_media", live) as fetched, \
             mock.patch.object(IG, "host_mp4", host), \
             mock.patch.object(IG, "publish_reel", reel):
            with self.assertRaises(SystemExit) as cm:
                IG.main()
        return cm.exception.code, host, reel, fetched

    def test_ledger_youtube_id_blocks_every_entry_point(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            ledger_path.write_text(json.dumps([{
                "video_id": "Lx2vFMDO5ws", "topic_key": "atr_sl", "ig_media_id": "17896148799412730",
            }]), encoding="utf-8")
            code, host, reel, fetched = self._run_main(run, ledger_path, mock.Mock(return_value=[]))
            self.assertEqual(code, 0)
            self.assertFalse(host.called)
            self.assertFalse(reel.called)
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "17896148799412730")
            self.assertTrue(fetched.called)

    def test_ledger_topic_and_hash_blocks_rereender_with_a_new_youtube_id(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d), youtube_id="yLGrcUS8-u4")
            digest = IG.content_hash(run / "final.mp4")
            ledger_path.write_text(json.dumps([{
                "video_id": "Lx2vFMDO5ws", "topic_key": "atr_sl", "content_hash": digest,
                "ig_media_id": "111",
            }]), encoding="utf-8")
            code, host, reel, _fetched = self._run_main(run, ledger_path, mock.Mock(return_value=[]))
            self.assertEqual(code, 0)
            self.assertFalse(host.called)
            self.assertFalse(reel.called)
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "111")

    def test_live_marker_blocks_when_the_ledger_has_no_ig_id(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            ident = None
            with mock.patch.object(IG, "RUN", run):
                ident = IG.video_identity(run / "final.mp4")
            caption = "SL बार-बार हिट? ATR से दूरी तय करो\n\n" + IG.caption_marker(ident)
            live = mock.Mock(return_value=[{"id": "18110931077268518", "caption": caption, "permalink": "https://instagram.com/reel/abc"}])
            code, host, reel, _fetched = self._run_main(run, ledger_path, live)
            self.assertEqual(code, 0)
            self.assertFalse(host.called)
            self.assertFalse(reel.called)
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "18110931077268518")

    def test_live_title_fallback_blocks_a_rereender_without_a_marker(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d), youtube_id="yLGrcUS8-u4", mp4=b"different-render")
            live = mock.Mock(return_value=[{
                "id": "17896148799412730",
                "caption": ATR_TITLE + "\n\n📌 याद रखो",
                "permalink": "https://instagram.com/reel/old",
            }])
            code, host, reel, _fetched = self._run_main(run, ledger_path, live)
            self.assertEqual(code, 0)
            self.assertFalse(reel.called)
            self.assertFalse(host.called)
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "17896148799412730")

    def test_media_list_retries_5xx_then_succeeds(self):
        calls = {"n": 0}

        def once():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("HTTP 500 https://graph.facebook.com/v23.0/1789/media")
            return [{"id": "1", "caption": "", "permalink": ""}]

        with mock.patch.object(IG, "_fetch_recent_media_once", side_effect=once), \
             mock.patch.object(IG.time, "sleep") as sleep:
            items = IG.fetch_recent_media()
        self.assertEqual([item["id"] for item in items], ["1"])
        self.assertEqual(calls["n"], 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 5])
        self.assertFalse(IG._transient_live_error(RuntimeError("HTTP 401 denied")))
        self.assertTrue(IG._transient_live_error(TimeoutError("timed out")))

    def test_persistent_5xx_blocks_the_reel(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            host = mock.Mock(side_effect=AssertionError("must not upload"))
            reel = mock.Mock(side_effect=AssertionError("must not publish"))
            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", self._env()), \
                 mock.patch.object(IG, "video_format", return_value="short"), \
                 mock.patch("ledger.PATH", ledger_path), \
                 mock.patch.object(IG, "_fetch_recent_media_once",
                                   side_effect=RuntimeError("HTTP 500 https://graph.facebook.com/media")), \
                 mock.patch.object(IG.time, "sleep") as sleep, \
                 mock.patch.object(IG, "host_mp4", host), \
                 mock.patch.object(IG, "publish_reel", reel):
                with self.assertRaises(SystemExit) as cm:
                    IG.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertFalse(host.called)
            self.assertFalse(reel.called)
            self.assertFalse((run / "ig_reel.txt").exists())
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 5])

    def test_live_check_failure_does_not_post(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            live = mock.Mock(side_effect=IG.LiveCheckError("HTTP 500 graph"))
            code, host, reel, _fetched = self._run_main(run, ledger_path, live)
            self.assertEqual(code, 1)
            self.assertFalse(host.called)
            self.assertFalse(reel.called)
            self.assertFalse((run / "ig_reel.txt").exists())

    def test_live_error_hides_the_access_token(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            token = "ig-token-secret"
            live = mock.Mock(side_effect=IG.LiveCheckError(f"HTTP 401 https://graph.facebook.com?access_token={token}"))
            buf = __import__("io").StringIO()
            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", self._env()), \
                 mock.patch.object(IG, "video_format", return_value="short"), \
                 mock.patch("ledger.PATH", ledger_path), \
                 mock.patch.object(IG, "fetch_recent_media", live), \
                 mock.patch.object(IG, "host_mp4", side_effect=AssertionError("must not upload")), \
                 mock.patch("sys.stderr", buf):
                with self.assertRaises(SystemExit) as cm:
                    IG.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertNotIn(token, buf.getvalue())

    def test_confirm_timeout_persists_before_poll_and_does_not_publish_again(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            with mock.patch.object(IG, "RUN", run):
                ident = IG.video_identity(run / "final.mp4")
            events = []

            def ig_call(path, method="GET", params=None):
                events.append((method, path))
                if method == "POST" and path.endswith("/media"):
                    return {"id": "container-1"}
                if method == "GET" and path == "container-1":
                    return {"status_code": "FINISHED"}
                if method == "POST" and str(path).endswith("/media_publish"):
                    return {"id": "media-new"}
                if method == "GET" and path == "media-new":
                    stored_now = json.loads(ledger_path.read_text(encoding="utf-8"))
                    if not stored_now or stored_now[-1].get("ig_media_id") != "media-new":
                        raise AssertionError("media id must be in the ledger before the confirm poll")
                    raise RuntimeError("confirm timeout")
                if method == "GET" and path.endswith("/media"):
                    return {"data": [{"id": "media-new", "caption": ATR_TITLE, "permalink": ""}]}
                raise AssertionError(method + " " + path)

            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", self._env()), \
                 mock.patch("ledger.PATH", ledger_path), \
                 mock.patch.object(IG, "ig_call", side_effect=ig_call), \
                 mock.patch.object(IG, "time") as clock:
                clock.sleep.return_value = None
                media_id = IG.publish_reel("https://example.com/a.mp4", ATR_TITLE, ident=ident)
            self.assertEqual(media_id, "media-new")
            publishes = [e for e in events if e[0] == "POST" and str(e[1]).endswith("/media_publish")]
            self.assertEqual(len(publishes), 1)
            stored = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(stored[-1]["ig_media_id"], "media-new")
            self.assertEqual(stored[-1]["slot"], "ig")
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "media-new")
            # The ledger write happens on media_publish, before the confirm GETs.
            publish_at = next(i for i, e in enumerate(events) if e[0] == "POST" and str(e[1]).endswith("media_publish"))
            confirm_at = next(i for i, e in enumerate(events) if e[1] == "media-new")
            self.assertLess(publish_at, confirm_at)

    def test_processing_timeout_rechecks_live_list_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            with mock.patch.object(IG, "RUN", run):
                ident = IG.video_identity(run / "final.mp4")
            calls = []

            def ig_call(path, method="GET", params=None):
                calls.append((method, path))
                if method == "POST" and path.endswith("/media"):
                    return {"id": "container-1"}
                if method == "GET" and path == "container-1":
                    return {"status_code": "IN_PROGRESS"}
                if method == "GET" and path.endswith("/media"):
                    return {"data": [{"id": "17896148799412730", "caption": ATR_TITLE, "permalink": ""}]}
                if method == "POST" and str(path).endswith("/media_publish"):
                    raise AssertionError("must not publish after a processing timeout")
                raise AssertionError(method + " " + path)

            with mock.patch.object(IG, "RUN", run), mock.patch.dict("os.environ", self._env()), \
                 mock.patch("ledger.PATH", ledger_path), \
                 mock.patch.object(IG, "ig_call", side_effect=ig_call), \
                 mock.patch.object(IG.time, "sleep"):
                media_id = IG.publish_reel("https://example.com/a.mp4", ATR_TITLE, ident=ident)
            self.assertEqual(media_id, "17896148799412730")
            self.assertFalse(any(m == "POST" and str(p).endswith("media_publish") for m, p in calls))
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "17896148799412730")

    def test_manual_second_call_does_not_host(self):
        """pipeline, watchdog, slot-watch and a hand run all enter main()."""
        with tempfile.TemporaryDirectory() as d:
            run, ledger_path = _ready_tree(Path(d))
            ledger_path.write_text(json.dumps([{
                "topic_key": "atr_sl", "title_norm": IG.normalize_title(ATR_TITLE),
                "ig_media_id": "17896148799412730", "slot": "ig",
            }]), encoding="utf-8")
            code, host, _reel, _fetched = self._run_main(
                run, ledger_path, mock.Mock(side_effect=IG.LiveCheckError("down")))
            self.assertEqual(code, 0)
            self.assertFalse(host.called)
            self.assertEqual((run / "ig_reel.txt").read_text(encoding="utf-8"), "17896148799412730")


if __name__ == "__main__":
    unittest.main()
