"""Hook, chart provenance, self-review, and fail-closed publish tests. No network."""
import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import board_short
import common
import engagement
import hook_check
import market_data
import publish
import publish_guard
import publish_instagram as IG
import review_gate
import run_pipeline
import self_review
import short_layout

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)


def _topic(**over):
    base = {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर, नीचे 2 गुना wick"],
            "matlab": ["मतलब", "sellers ने गिराया, buyers ने उठाया"],
            "karein": ["क्या करें", "अगली हरी कैंडल पर, SL low के नीचे"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना", "ये संभावना है, गारंटी नहीं"],
    }
    base.update(over)
    return base


class HookCheckTests(unittest.TestCase):
    def test_question_passes_and_clickbait_and_length_fail(self):
        allowed = {"70", "2"}
        ok = "RSI 70 का मतलब गिरना नहीं?"
        self.assertEqual(hook_check.check_hook_line(ok, allowed), [])
        self.assertEqual(hook_check.hook_style(ok, allowed), "question")
        short = hook_check.check_hook_line("क्या?", allowed)
        self.assertTrue(any("words" in f for f in short))
        long_line = " ".join(["शब्द"] * 20) + "?"
        self.assertTrue(any("words" in f for f in hook_check.check_hook_line(long_line, allowed)))
        bait = "गारंटी से 100% रिटर्न हर महीने मिलेगा?"
        self.assertTrue(any("clickbait" in f for f in hook_check.check_hook_line(bait, allowed)))
        self.assertTrue(hook_check.clickbait_return_claim("guaranteed return on this stock"))
        self.assertFalse(hook_check.clickbait_return_claim("पिछला रिटर्न आगे की गारंटी नहीं"))
        self.assertFalse(hook_check.clickbait_return_claim("guaranteed profit नहीं"))

    def test_number_must_come_from_the_topic(self):
        allowed = {"70"}
        self.assertEqual(hook_check.check_hook_line("RSI 70 एक level है", allowed), [])
        invented = hook_check.check_hook_line("कीमत 99999 पर थी", allowed)
        self.assertTrue(any("not in the topic" in f for f in invented))
        self.assertEqual(hook_check.check_hook_line("", allowed), ["hook line missing"])

    def test_every_topic_hook_line_passes(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        for topic in bank:
            line = hook_check.make_hook_line(topic)
            fails = hook_check.check_hook_line(
                line, hook_check.allowed_numbers(topic),
                max_words=hook_check.hook_word_limit(topic))
            self.assertEqual(fails, [], f"{topic['key']}: {line!r} -> {fails}")

    def test_fallback_narration_passes_the_teaching_gate(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        for topic in bank:
            narration = board_short.fallback_narration(topic)
            fails = board_short.check_narration(narration, topic)
            words = len(" ".join(narration["beats"][b] for b in ("hook", "pehchan", "matlab", "karein")).split())
            self.assertGreaterEqual(words, 160, topic["key"])
            self.assertLessEqual(words, 180, topic["key"])
            self.assertGreaterEqual(len(narration["title"]), 8, topic["key"])
            self.assertLessEqual(len(narration["title"]), 90, topic["key"])
            self.assertEqual(fails, [], f"{topic['key']}: {fails}")
            self.assertTrue(narration["beats"]["hook"].startswith(narration["hook_line"].rstrip("।")))

    def test_oversized_thumb_title_is_clipped_inside_the_title_rule(self):
        topic = _topic()
        topic["thumb"] = ["T" * 80, "U" * 80]
        title = board_short._fallback_title(topic)
        self.assertGreaterEqual(len(title), 8)
        self.assertLessEqual(len(title), 90)


class ChartProvenanceTests(unittest.TestCase):
    def test_gaps_are_not_filled_or_interpolated(self):
        points = market_data.points_from_rows([
            ("2020-01", 10),
            ("2020-02", None),
            None,
            ("2020-04", 40),
        ])
        self.assertEqual([p["date"] for p in points], ["2020-01", "2020-04"])
        self.assertEqual([p["close"] for p in points], [10.0, 40.0])
        self.assertTrue(market_data.values_are_fabricated(
            [{"date": "2020-03", "close": 25}], points))
        self.assertFalse(market_data.values_are_fabricated(points, points))

    def test_named_stock_price_must_be_three_months_old(self):
        self.assertTrue(market_data.stock_price_is_old_enough("2026-06-04", TODAY))
        self.assertTrue(market_data.stock_price_is_old_enough("2026-07-01", TODAY))
        self.assertFalse(market_data.stock_price_is_old_enough("2026-07-02", TODAY))
        recent = {
            "series": {
                "TITAN": {
                    "ticker": "TITAN.NS",
                    "monthly": [["2026-08", 100], ["2026-09", 110]],
                    "last": {"date": "2026-09-15", "close": 110},
                }
            }
        }
        spec = market_data.spec_for_topic(
            {"key": "titan_2010", "cat": "REAL DATA"}, today=TODAY, cache=recent)
        self.assertEqual(spec["mode"], "non_numeric")
        self.assertIn("3 months", spec["reason"])
        self.assertEqual(spec["series"], [])
        self.assertFalse(any(ch.isdigit() for ch in " ".join(spec["labels"])))

    def test_old_stock_keeps_source_date_and_only_real_points(self):
        cache = {
            "series": {
                "TITAN": {
                    "ticker": "TITAN.NS",
                    "monthly": [["2010-01", 71], ["2026-06", 4231]],
                    "last": {"date": "2026-06-04", "close": 4231},
                }
            }
        }
        spec = market_data.spec_for_topic(
            {"key": "titan_2010", "cat": "REAL DATA"}, today=TODAY, cache=cache)
        self.assertEqual(spec["mode"], "series")
        self.assertEqual(spec["source"], "Yahoo Finance")
        self.assertEqual(spec["as_of"], "2026-06-04")
        self.assertFalse(spec["fabricated"])
        shown = spec["series"][0]["points"]
        self.assertEqual(shown, market_data.points_from_rows(cache["series"]["TITAN"]["monthly"]))
        line = market_data.provenance_line(spec)
        self.assertIn("Yahoo Finance", line)
        self.assertIn("2026-06-04", line)

    def test_recent_index_is_allowed_and_a_failed_fetch_draws_no_prices(self):
        cache = {
            "series": {
                "NIFTY 50": {
                    "ticker": "^NSEI",
                    "monthly": [["2026-09", 25000], ["2026-10", 25100]],
                    "last": {"date": "2026-09-30", "close": 25100},
                }
            }
        }
        spec = market_data.spec_for_topic(
            {"key": "nifty_2010", "cat": "REAL DATA"}, today=TODAY, cache=cache)
        self.assertEqual(spec["mode"], "series")
        self.assertEqual(spec["as_of"], "2026-09-30")

        def boom(_name):
            raise RuntimeError("yahoo down")

        missed = market_data.spec_for_topic(
            {"key": "titan_2010", "cat": "REAL DATA"}, today=TODAY,
            cache={"series": {}}, fetcher=boom)
        self.assertEqual(missed["mode"], "non_numeric")
        self.assertEqual(missed["series"], [])
        self.assertFalse(any(ch.isdigit() for label in missed["labels"] for ch in label))

    def test_unmapped_real_data_topic_is_non_numeric_and_schematic_is_not(self):
        eicher = market_data.spec_for_topic(
            {"key": "multibagger_case_eicher", "cat": "REAL DATA"}, today=TODAY, cache={"series": {}})
        self.assertEqual(market_data.visual_mode(eicher and {"key": "multibagger_case_eicher", "cat": "REAL DATA"}, eicher), "non_numeric")
        hammer = market_data.spec_for_topic(_topic(), today=TODAY, cache={"series": {}})
        self.assertEqual(hammer["mode"], "schematic")
        self.assertEqual(market_data.visual_mode(_topic(), hammer), "schematic")

    def test_stamp_paints_the_source_line_and_fallback_frame_has_no_digits(self):
        from PIL import Image
        import numpy as np
        spec = {"source": "Yahoo Finance", "as_of": "2026-06-04", "mode": "series"}
        base = Image.new("RGB", (1080, 1920), (8, 13, 26))
        stamped = market_data.stamp(base, spec)
        self.assertNotEqual(stamped.getpixel((80, 1060)), base.getpixel((80, 1060)))
        frame = market_data.non_numeric_frame(_topic(), "hook", 0)
        import board_kit as kit
        crop = frame.crop(kit.BOARD)
        gray = np.asarray(crop.convert("L"), dtype="float32")
        self.assertGreater(float((gray > 42).mean()), 0.01)
        self.assertGreater(float(gray.std()), 5.0)

    def test_market_card_does_not_ship_the_old_fake_index_curve(self):
        src = (ROOT / "board_scenes.py").read_text(encoding="utf-8")
        self.assertNotIn("ys = [100, 103, 101, 108, 112, 109, 118, 123, 121, 130, 136]", src)
        self.assertIn("NO PRICE DATA ON THIS CARD", src)


class OnScreenAndCtaTests(unittest.TestCase):
    def test_hook_is_on_screen_for_the_first_two_seconds_and_every_beat_has_text(self):
        topic = _topic()
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        line = hook_check.make_hook_line(topic)
        events = short_layout.overlay_plan(topic, times, line)
        self.assertTrue(short_layout.hook_covers_first_two_seconds(events))
        self.assertTrue(short_layout.beats_have_key_text(events))
        hook = next(e for e in events if e["role"] == "hook")
        self.assertEqual(hook["text"], line)
        self.assertLessEqual(hook["start"], 0.05)
        self.assertGreaterEqual(hook["end"], 2.0)
        cta = next(e for e in events if e["role"] == "cta")
        self.assertIn("?", cta["text"])
        self.assertIn("Comment", cta["text"])
        self.assertNotIn("SUBSCRIBE", cta["text"])

    def test_description_keeps_the_referral_and_adds_the_poll(self):
        sec = {
            "TITLE": "Hammer candle",
            "HOOK": "Hammer candle ka asli matlab.",
            "CTA": engagement.engagement_question({"cat": "CANDLESTICK"}),
            "DISCLAIMER LINE": "सिर्फ़ जानकारी, निवेश सलाह नहीं।",
        }
        cfg = {"channel": {"name": "Paise Ki Pathshala", "tagline": "t"}}
        with mock.patch.dict(os.environ, {"VIDEO_FORMAT": "short"}), \
                mock.patch.object(publish, "load_topic", return_value={"cat": "CANDLESTICK", "seo": "Hammer"}):
            text = publish.build_description(sec, cfg)
        self.assertIn("हाँ या नहीं", text)
        cta = text.index("🔗 Zerodha / Upstox demat account")
        self.assertLess(cta, text.index("सिर्फ़ जानकारी"))
        self.assertIn("commission", text[cta:cta + 220])
        self.assertIn("https://zerodha.com/open-account", text)


class SelfReviewTests(unittest.TestCase):
    def _overlays(self, topic, line):
        times = {"hook": (0.0, 8.0), "pehchan": (8.0, 18.0), "matlab": (18.0, 32.0), "karein": (32.0, 48.0)}
        return short_layout.overlay_plan(topic, times, line)

    def test_bar_and_bounded_retries(self):
        self.assertEqual(self_review.PART_BAR, 60)
        self.assertEqual(self_review.OVERALL_BAR, 75)
        self.assertEqual(self_review.MAX_ATTEMPTS, 3)
        self.assertEqual(self_review.next_action(False, 1), "retry")
        self.assertEqual(self_review.next_action(False, 2), "retry")
        self.assertEqual(self_review.next_action(False, 3), "block")
        self.assertEqual(self_review.next_action(True, 1), "accept")

    def test_good_schematic_passes_and_trend_is_skipped_when_file_is_absent(self):
        topic = _topic()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "source": None, "as_of": None, "series": [], "fabricated": False}
        review = self_review.full_review(
            topic, narration, spec, self._overlays(topic, narration["hook_line"]), trends={})
        self.assertIsNone(review["parts"]["trend"])
        self.assertGreaterEqual(review["overall"], 75)
        self.assertTrue(review["public_ok"])
        self.assertIsNone(self_review.load_trends(ROOT / "data" / "no-such-trends.json"))

    def test_empty_research_snapshot_skips_the_trend_score(self):
        snapshot = {
            "schema_version": 1,
            "trending_topics": [],
            "summary": {"topic_clusters": [], "hook_patterns": {}},
        }
        self.assertEqual(self_review._themes(snapshot), [])
        topic = _topic()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "series": [], "fabricated": False}
        review = self_review.full_review(
            topic, narration, spec, self._overlays(topic, narration["hook_line"]), trends=snapshot)
        self.assertIsNone(review["parts"]["trend"])
        self.assertTrue(review["public_ok"])

    def test_topic_clusters_count_as_themes(self):
        trends = {"summary": {"topic_clusters": [
            {"name": "candlestick", "count": 3, "keywords": ["hammer"]},
        ]}}
        topic = _topic()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "series": [], "fabricated": False}
        review = self_review.full_review(
            topic, narration, spec, self._overlays(topic, narration["hook_line"]), trends=trends)
        self.assertEqual(review["parts"]["trend"], 100)

    def test_trend_file_is_scored_only_when_present(self):
        topic = _topic()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "schematic", "series": [], "fabricated": False}
        overlays = self._overlays(topic, narration["hook_line"])
        matched = self_review.full_review(
            topic, narration, spec, overlays, trends={"themes": ["candlestick"]})
        self.assertEqual(matched["parts"]["trend"], 100)
        self.assertTrue(matched["public_ok"])
        missed = self_review.full_review(
            topic, narration, spec, overlays, trends={"themes": ["quantum physics"]})
        self.assertLess(missed["parts"]["trend"], self_review.PART_BAR)
        self.assertFalse(missed["public_ok"])

    def test_bad_hook_unsourced_number_and_missing_provenance_block_public(self):
        topic = _topic()
        bad = {"title": "Hammer short title", "hook_line": "गारंटी से पैसा डबल होगा?",
               "beats": {b: "यह सिर्फ़ जानकारी है और संख्या 99999 यहाँ है।" for b in short_layout.BEATS}}
        spec = {"mode": "series", "source": "", "as_of": "", "series": [], "fabricated": True}
        review = self_review.full_review(topic, bad, spec, self._overlays(topic, bad["hook_line"]), trends={})
        self.assertLess(review["parts"]["hook"], self_review.PART_BAR)
        self.assertEqual(review["parts"]["accuracy"], 0)
        self.assertEqual(review["parts"]["visual"], 0)
        self.assertFalse(review["public_ok"])

    def test_series_without_source_fails_visual_even_if_hook_is_good(self):
        topic = _topic()
        narration = board_short.fallback_narration(topic)
        spec = {"mode": "series", "source": None, "as_of": None, "series": [{"points": [{"date": "2020-01", "close": 1}]}],
                "fabricated": False}
        review = self_review.full_review(
            topic, narration, spec, self._overlays(topic, narration["hook_line"]), trends={})
        self.assertEqual(review["parts"]["visual"], 0)
        self.assertFalse(review["public_ok"])


class AiFailClosedTests(unittest.TestCase):
    def _ready_script(self):
        return "TITLE:\nHammer\n\nHOOK (0-3 sec):\n" + ("गिरावट के बाद खरीदार लौटे। " * 12) + "\n\nFACT LEDGER:\n- wick\n"

    def test_json_fallback_is_not_a_pass(self):
        with mock.patch("common.claude", side_effect=RuntimeError("DeepSeek returned empty content")), \
                mock.patch("common.time.sleep"):
            out = common.claude_json("system", "user", retries=1)
        self.assertNotEqual(out.get("verdict"), "PASS")
        self.assertFalse(out.get("ai_verdict_valid"))
        self.assertIn("NOT READY", out.get("notes", ""))
        offline = json.loads(common._offline_financial_agent("please give a verdict", "user"))
        self.assertNotEqual(offline.get("verdict"), "PASS")
        src = (ROOT / "common.py").read_text(encoding="utf-8")
        self.assertNotIn("safe PASS fallback", src)
        self.assertNotIn('"verdict": "PASS"', src)

    def test_missing_invalid_and_empty_narration_are_not_public(self):
        blocked = review_gate.decide("", False, self._ready_script(), {"public_ok": True}, "short")
        self.assertFalse(blocked["public_ready"])
        self.assertEqual(blocked["exit_code"], 2)
        self.assertNotIn("READY TO PUBLISH", blocked["verdict_line"])
        self.assertIn("AI reviewer unavailable", blocked["verdict_line"])

        template = "VERDICT: [READY TO PUBLISH] / [NEEDS FIX — X items]\nlooks fine"
        ambiguous = review_gate.decide(template, True, self._ready_script(), {"public_ok": True}, "short")
        self.assertFalse(ambiguous["public_ready"])
        self.assertIsNone(review_gate.parse_report_verdict(template))

        fallback = json.dumps(common.INVALID_AI_VERDICT)
        from_fallback = review_gate.decide(fallback, True, self._ready_script(), {"public_ok": True}, "short")
        self.assertFalse(from_fallback["public_ready"])

        empty = review_gate.decide(
            "VERDICT: [READY TO PUBLISH]\n", True, "FACT LEDGER:\n- none\n", {"public_ok": True}, "short")
        self.assertFalse(empty["public_ready"])
        self.assertIn("narration", empty["verdict_line"])
        self.assertEqual(review_gate.spoken_word_count("FACT LEDGER:\n- none\n"), 0)

    def test_ready_requires_ai_and_self_review(self):
        text = "VERDICT: [READY TO PUBLISH]\n\nHook is specific.\n"
        ready = review_gate.decide(text, True, self._ready_script(), {"public_ok": True, "summary": "ok"}, "short")
        self.assertTrue(ready["public_ready"])
        self.assertEqual(ready["exit_code"], 0)
        below = review_gate.decide(
            text, True, self._ready_script(),
            {"public_ok": False, "summary": "overall 40"}, "short")
        self.assertFalse(below["public_ready"])
        self.assertIn("self-review", below["verdict_line"])

    def test_files_on_disk_must_agree_before_a_public_upload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))
            (run / "verify_meta.json").write_text(json.dumps({
                "ai_verdict_valid": True, "verdict": "READY", "public_ready": True,
            }), encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))
            (run / "self_review.json").write_text(json.dumps({"public_ok": False}), encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))
            (run / "self_review.json").write_text(json.dumps({"public_ok": True}), encoding="utf-8")
            self.assertTrue(review_gate.files_allow_public(run, "short"))
            (run / "report.md").write_text(
                "VERDICT: [READY TO PUBLISH] / [NEEDS FIX — 1 item]\n", encoding="utf-8")
            self.assertFalse(review_gate.files_allow_public(run, "short"))


class NoPublicFromRepairOrTestTests(unittest.TestCase):
    def test_clamp_and_topic_key_block_public_and_scheduled_public(self):
        with mock.patch.dict(os.environ, {"PIPELINE_MODE": "repair", "PUBLISH_VISIBILITY": "public"}, clear=False):
            os.environ.pop("TOPIC_KEY", None)
            vis, when = publish_guard.clamp_visibility("public", "2026-10-02T03:00:00Z")
        self.assertEqual(vis, "unlisted")
        self.assertIsNone(when)
        with mock.patch.dict(os.environ, {"TOPIC_KEY": "hammer", "PIPELINE_MODE": ""}, clear=False):
            self.assertIn("TOPIC_KEY", publish_guard.blocked_reason())
            vis, when = publish_guard.clamp_visibility("private", "2026-10-02T03:00:00Z")
        self.assertEqual(vis, "unlisted")
        self.assertIsNone(when)
        with mock.patch.dict(os.environ, {"PIPELINE_MODE": "", "TOPIC_KEY": ""}, clear=False):
            os.environ.pop("TOPIC_KEY", None)
            os.environ.pop("PIPELINE_MODE", None)
            vis, when = publish_guard.clamp_visibility("private", "2026-10-02T03:00:00Z")
        self.assertEqual((vis, when), ("private", "2026-10-02T03:00:00Z"))

    def test_approved_narration_from_run_is_not_a_test(self):
        approved = {
            "TOPIC_KEY": "risk_daily_loss_limit",
            "NARRATION_FROM_RUN": "37095205924",
            "APPROVED": "true",
            "PIPELINE_MODE": "",
            "BOARD_QA": "",
        }
        with mock.patch.dict(os.environ, approved, clear=False):
            self.assertIsNone(publish_guard.blocked_reason())
            vis, when = publish_guard.clamp_visibility("public", "2026-10-03T10:30:00Z")
        self.assertEqual((vis, when), ("public", "2026-10-03T10:30:00Z"))
        still_test = dict(approved, APPROVED="false")
        with mock.patch.dict(os.environ, still_test, clear=False):
            self.assertIn("TOPIC_KEY", publish_guard.blocked_reason())
        repair = dict(approved, PIPELINE_MODE="repair")
        with mock.patch.dict(os.environ, repair, clear=False):
            self.assertEqual(publish_guard.blocked_reason(), "mode=repair")
        qa = dict(approved, BOARD_QA="1", PIPELINE_MODE="")
        with mock.patch.dict(os.environ, qa, clear=False):
            self.assertEqual(publish_guard.blocked_reason(), "qa render")

    def test_retry_and_repair_env_cannot_request_a_public_upload(self):
        import auto_agent
        with mock.patch.object(auto_agent, "run_cmd", return_value=(0, "")) as cmd, \
                mock.patch.object(auto_agent, "topic_key", return_value="hammer"):
            auto_agent.retry_same_topic("short", True, mode="repair")
        env = cmd.call_args.kwargs["env"]
        self.assertEqual(env["PIPELINE_MODE"], "repair")
        self.assertEqual(env["PUBLISH_VISIBILITY"], "unlisted")
        self.assertEqual(env["TOPIC_KEY"], "hammer")

    def test_instagram_repair_and_test_never_publish(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "final.mp4").write_bytes(b"x" * 2000)
            (run / "report.md").write_text("VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
            (run / "verify_meta.json").write_text(json.dumps({
                "ai_verdict_valid": True, "verdict": "READY", "public_ready": True,
            }), encoding="utf-8")
            (run / "self_review.json").write_text(json.dumps({"public_ok": True}), encoding="utf-8")
            env = {"IG_CROSSPOST": "1", "IG_CROSSPOST_FORCE": "1", "VIDEO_FORMAT": "short",
                   "PIPELINE_MODE": "test", "TOPIC_KEY": "hammer"}
            with mock.patch.object(IG, "RUN", run), mock.patch.dict(os.environ, env), \
                    mock.patch.object(IG, "video_format", return_value="short"), \
                    mock.patch.object(IG, "host_mp4", side_effect=AssertionError("must not upload")), \
                    mock.patch.object(IG, "publish_reel", side_effect=AssertionError("must not publish")):
                with self.assertRaises(SystemExit) as cm:
                    IG.main()
        self.assertEqual(cm.exception.code, 0)

    def test_invalid_grant_fails_the_job_even_if_a_reel_was_posted(self):
        status, stop = run_pipeline.resolve_publish_status(
            yt_rc=3, yt_uploaded=False, ig_published=True, ig_required=True, verdict_ready=True)
        self.assertNotEqual(status, "success")
        self.assertIn("invalid_grant", status)
        self.assertIn("non-zero", stop)
        ok, msg = run_pipeline.resolve_publish_status(
            yt_rc=0, yt_uploaded=True, ig_published=True, ig_required=True, verdict_ready=True)
        self.assertEqual(ok, "success")
        self.assertIsNone(msg)


if __name__ == "__main__":
    unittest.main()
