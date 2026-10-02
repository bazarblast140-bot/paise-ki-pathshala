"""Reviewer truncation is not a verdict, and chart multiples match the script."""
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

import common
import market_data
import review_gate
import run_verify

ROOT_BANK = None


def _bank():
    global ROOT_BANK
    if ROOT_BANK is None:
        from pathlib import Path
        ROOT_BANK = json.loads(
            (Path(__file__).resolve().parent.parent / "topic_bank.json").read_text(encoding="utf-8")
        )["topics"]
    return ROOT_BANK


def _topic(key):
    return deepcopy(next(topic for topic in _bank() if topic["key"] == key))


class ReviewTruncationTests(unittest.TestCase):
    def setUp(self):
        import credit_log
        credit_log.reset()

    def test_length_finish_is_an_error_and_thinking_stays_off(self):
        partial = "VERDICT: [NEEDS FIX — 1 item]\n🔴 Risks / assumptions:\n- Data 3 mahi"
        message = SimpleNamespace(content=partial, reasoning_content="x" * 18, model_extra=None)
        choice = SimpleNamespace(message=message, finish_reason="length")
        completion = SimpleNamespace(choices=[choice])
        captured = {}

        class Completions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return completion

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-test"}, clear=False), \
             mock.patch("common.openai.OpenAI", return_value=client), \
             mock.patch("common.time.sleep"):
            with self.assertRaises(common.TruncatedResponse):
                common.claude(
                    "system", "user", model="deepseek-flash",
                    max_tokens=16000, retries=1, raise_on_truncate=True,
                )
        self.assertEqual(captured["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(captured["max_tokens"], 16000)

    def test_truncated_review_retries_once_larger_and_drops_the_partial(self):
        partial = "VERDICT: [NEEDS FIX — 1 item]\n- Data 3 mahi"
        full = "VERDICT: [READY TO PUBLISH]\n\n✅ Auto-checks pass:\nHook | Facts | Compliance\n"
        calls = []

        def fake_claude(system, user, model=None, max_tokens=16000, retries=3, raise_on_truncate=False):
            calls.append((max_tokens, raise_on_truncate, model))
            if len(calls) == 1:
                raise common.TruncatedResponse(max_tokens)
            return full

        cfg = {"models": {
            "verify_model": "deepseek-flash",
            "verify_max_tokens": 16000,
            "verify_retry_max_tokens": 32000,
        }}
        with mock.patch("run_verify.claude", side_effect=fake_claude):
            text = run_verify.fetch_review(cfg, "system", "user")
        self.assertEqual(text, full)
        self.assertNotIn("Data 3 mahi", text)
        self.assertNotIn("NEEDS FIX", text)
        self.assertEqual(calls, [
            (16000, True, "deepseek-flash"),
            (32000, True, "deepseek-flash"),
        ])
        script = "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- 71.07\n"
        decision = review_gate.decide(text, True, script, {"public_ok": True}, "short")
        self.assertTrue(decision["public_ready"])

    def test_a_second_truncation_is_not_parsed_as_needs_fix(self):
        def fake_claude(system, user, model=None, max_tokens=16000, retries=3, raise_on_truncate=False):
            raise common.TruncatedResponse(max_tokens)

        cfg = {"models": {"verify_model": "deepseek-flash", "verify_max_tokens": 16000,
                          "verify_retry_max_tokens": 32000}}
        with mock.patch("run_verify.claude", side_effect=fake_claude):
            with self.assertRaises(common.TruncatedResponse) as caught:
                run_verify.fetch_review(cfg, "system", "user")
        self.assertEqual(caught.exception.max_tokens, 32000)
        script = "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- x\n"
        blocked = review_gate.decide("", False, script, {"public_ok": True}, "short")
        self.assertFalse(blocked["public_ready"])
        self.assertIn("AI reviewer unavailable", blocked["verdict_line"])
        self.assertNotIn("1 item", blocked["verdict_line"])

    def test_a_complete_needs_fix_still_blocks(self):
        text = "VERDICT: [NEEDS FIX — 1 item]\n\n🔴 Risks / assumptions:\n- Data is three months old.\n"
        script = "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- x\n"
        decision = review_gate.decide(text, True, script, {"public_ok": True}, "short")
        self.assertFalse(decision["public_ready"])
        self.assertEqual(decision["exit_code"], 2)
        self.assertEqual(review_gate.parse_report_verdict(text), "NEEDS_FIX")


class ChartNumberTests(unittest.TestCase):
    def test_titan_multiple_uses_the_chart_divisor_not_the_rounded_71(self):
        self.assertEqual(f"{4231 / 71:.2f}", "59.59")
        figs = market_data.growth_figures("TITAN", 2010)
        self.assertEqual(figs["start_text"], "71.07")
        self.assertEqual(figs["end_text"], "4,231")
        self.assertEqual(figs["multiple_text"], "59.53")
        self.assertEqual(figs["multiple_label"], "= 59.53x")
        self.assertEqual(figs["equation"], "4,231  /  71.07")
        start = float(figs["start_text"].replace(",", ""))
        end = float(figs["end_text"].replace(",", ""))
        self.assertEqual(f"{end / start:.2f}", figs["multiple_text"])
        self.assertEqual(figs["lakh_text"], "59.5")

    def test_script_copy_follows_the_chart_and_keeps_the_hook_closed(self):
        topic = _topic("titan_2010")
        topic["beats"]["matlab"][1] = "करीब 59.99 गुना"
        topic["facts"] = [
            fact if fact != "59.53 गुना" else "59.99 गुना" for fact in topic["facts"]
        ]
        market_data.sync_topic_numbers(topic)
        blob = " ".join([
            topic["beats"]["pehchan"][1],
            topic["beats"]["matlab"][1],
            topic["beats"]["karein"][1],
            *topic["facts"],
        ])
        self.assertIn("71.07 से 4,231", blob)
        self.assertIn("59.53 गुना", blob)
        self.assertIn("2010-01-04 को 71.07", blob)
        self.assertNotIn("59.99", blob)
        self.assertNotIn("59.59", blob)
        hook = " ".join(topic["beats"]["hook"])
        self.assertNotIn("59.53", hook)
        self.assertNotIn("4,231", hook)
        self.assertNotIn("4231", hook)

    def test_every_chart_series_script_matches_the_printed_division(self):
        for key, year in market_data.TOPIC_YEAR.items():
            name = market_data.TOPIC_SERIES[key][0]
            figs = market_data.growth_figures(name, year)
            if figs["multiple"] < 1:
                continue
            start = float(figs["start_text"].replace(",", ""))
            end = float(figs["end_text"].replace(",", ""))
            self.assertEqual(
                f"{end / start:.2f}", figs["multiple_text"],
                f"{key}: {figs['equation']} is not {figs['multiple_text']}x",
            )
            topic = _topic(key)
            market_data.sync_topic_numbers(topic)
            joined = " ".join(str(x) for pair in topic["beats"].values() for x in pair)
            joined += " " + " ".join(topic["facts"])
            self.assertIn(figs["multiple_text"] + " गुना", joined, key)
            self.assertIn(figs["start_text"], joined, key)
            self.assertIn(figs["end_text"], joined, key)


# The 2026-10-02 08:30 review. Judgment only; auto-checks were not hard fails.
# Visuals ✗ is the empty visual_checks blob, not a broken frame.
OCT2_SOFT_REVIEW = """VERDICT: [NEEDS FIX — 3 items]

✅ Auto-checks pass:
Hook ✓ | Facts ⚠️ (entry/SL/target ledger-mein hain par source nahi — illustrative maano) | Compliance ✓ (disclaimer + "सलाह नहीं" framing, koi stock/product recommend nahi) | Coverage ✓ | Visuals ✗ (visual_checks khaali) | Length ✓

♻️ Reused-content risk: LOW
- 0% stock footage, 4/4 chart shots, ElevenLabs premium voice, original script + overlays always-on — yeh teen mitigating factors isko clean rakhte hain.

⚠️ Tumhe dekhna hai (judgment calls):
- Thumbnail: "1:2 setup फिर भी घाटा?" catchy hai, par check karo ki koi arrow/target number on-thumb se "guaranteed profit" jaisa na lage.
- Assumption @ 0:00–0:03: entry 200/SL 198/target 204 illustrative example hai — screen par "example only" chhota tag lagana better hoga.
- "Is this boring?" — tone thoda dry hai; Segment 1 mein ek real chart zoom se retention badh sakta hai.

🔴 Risks / assumptions:
- Charges/STT/slippage ka koi figure diya nahi gaya aur koi tax number nahi — isliye FY note ki zaroorat nahi. Ledger mein sab "Likely/Illustrative" — koi UNVERIFIED figure nahi.
- India jurisdiction, non-SEBI channel — disclaimer line ok par "Not SEBI registered" mention add karna safe
"""

# The 2-Oct Short was the LLM narration that passed the hook/teaching gate.
# narration_source is the mark board_short writes before the soft override.
HIGH_REVIEW = {
    "parts": {"hook": 100, "visual": 90, "accuracy": 100, "trend": None},
    "overall": 96.7,
    "passed": True,
    "public_ok": True,
    "narration_source": "llm",
    "summary": "overall 96.7 (hook 100, visual 90, accuracy 100, trend skip)",
}


def _script():
    return "TITLE:\nTitan\n\nHOOK:\n" + ("सवाल साफ है। " * 8) + "\n\nFACT LEDGER:\n- 71.07\n"


class SoftJudgmentGateTests(unittest.TestCase):
    def test_oct2_soft_review_is_not_a_hard_failure(self):
        self.assertEqual(review_gate.hard_failure_reasons(OCT2_SOFT_REVIEW), [])
        self.assertTrue(review_gate.soft_judgment_only(OCT2_SOFT_REVIEW))
        self.assertTrue(review_gate.self_review_clears_high_bar(HIGH_REVIEW))

    def test_oct2_soft_review_publishes_when_self_review_is_at_the_high_bar(self):
        decision = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), HIGH_REVIEW, "short")
        self.assertTrue(decision["public_ready"])
        self.assertEqual(decision["exit_code"], 0)
        self.assertTrue(decision["soft_judgment_only"])
        self.assertFalse(decision["keep_model_report"])
        self.assertEqual(review_gate.parse_report_verdict(decision["verdict_line"]), "READY")
        report = run_verify._report_from_decision(decision, OCT2_SOFT_REVIEW, HIGH_REVIEW)
        self.assertEqual(review_gate.parse_report_verdict(report), "READY")
        self.assertNotIn("NEEDS FIX", report)
        self.assertIn("example only", report)

    def test_soft_review_still_blocks_without_the_high_bar(self):
        barely = {"public_ok": True, "overall": 80, "parts": {
            "hook": 70, "visual": 70, "accuracy": 100, "trend": None,
        }}
        decision = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), barely, "short")
        self.assertFalse(decision["public_ready"])
        self.assertEqual(decision["exit_code"], 2)
        self.assertFalse(review_gate.self_review_clears_high_bar(barely))
        missing_parts = {"public_ok": True, "overall": 96.7}
        self.assertFalse(review_gate.self_review_clears_high_bar(missing_parts))
        blocked = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), missing_parts, "short")
        self.assertFalse(blocked["public_ready"])

    def test_visual_just_under_the_high_bar_still_blocks(self):
        review = {"public_ok": True, "overall": 96.3, "parts": {
            "hook": 100, "visual": 89, "accuracy": 100, "trend": None,
        }}
        decision = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), review, "short")
        self.assertFalse(decision["public_ready"])

    def test_accuracy_below_100_still_blocks(self):
        review = {"public_ok": True, "overall": 96, "parts": {
            "hook": 100, "visual": 90, "accuracy": 90, "trend": None,
        }}
        self.assertFalse(review_gate.self_review_clears_high_bar(review))
        decision = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), review, "short")
        self.assertFalse(decision["public_ready"])

    def test_long_form_soft_needs_fix_still_blocks(self):
        decision = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), HIGH_REVIEW, "long")
        self.assertFalse(decision["public_ready"])

    def test_needs_fix_without_judgment_calls_still_blocks_at_the_high_bar(self):
        text = "VERDICT: [NEEDS FIX — 1 item]\n\nLooks unfinished.\n"
        decision = review_gate.decide(text, True, _script(), HIGH_REVIEW, "short")
        self.assertFalse(decision["public_ready"])
        self.assertIn("judgment-calls", " ".join(decision["reasons"]))

    def test_hard_fact_compliance_and_safety_still_block_at_the_high_bar(self):
        cases = {
            "unverified figure": OCT2_SOFT_REVIEW + "\n- Close is UNVERIFIED.\n",
            "unsourced figure": OCT2_SOFT_REVIEW.replace(
                "Facts ⚠️ (entry/SL/target ledger-mein hain par source nahi — illustrative maano)",
                "Facts ⚠️ (close has no source)",
            ),
            "stale or outdated figure": (
                "VERDICT: [NEEDS FIX — 1 item]\n\n"
                "⚠️ Tumhe dekhna hai (judgment calls):\n- Thumbnail is plain.\n\n"
                "🔴 Risks / assumptions:\n- Data is three months old.\n"
            ),
            "auto-check failed: facts": OCT2_SOFT_REVIEW.replace("Facts ⚠️", "Facts ✗"),
            "auto-check failed: compliance": OCT2_SOFT_REVIEW.replace("Compliance ✓", "Compliance ✗"),
            "auto-check failed: visuals": OCT2_SOFT_REVIEW.replace(
                "Visuals ✗ (visual_checks khaali)",
                "Visuals ✗ (numbers not on screen)",
            ),
            "guaranteed return or profit": OCT2_SOFT_REVIEW + "\n- The script promises guaranteed returns.\n",
            "advice or fake-expert framing": OCT2_SOFT_REVIEW + "\n- Narrator uses a fake-expert persona.\n",
            "disclaimer missing": OCT2_SOFT_REVIEW + "\n- Disclaimer is missing.\n",
            "subtitles missing": OCT2_SOFT_REVIEW + "\n- Subtitles were not generated.\n",
            "reused-content risk HIGH": OCT2_SOFT_REVIEW.replace(
                "Reused-content risk: LOW", "Reused-content risk: HIGH"),
            "tax figure without a financial year": OCT2_SOFT_REVIEW + (
                "\n- LTCG tax is 12.5% with the financial year not named.\n"
            ),
        }
        for reason, text in cases.items():
            decision = review_gate.decide(text, True, _script(), HIGH_REVIEW, "short")
            self.assertFalse(decision["public_ready"], reason)
            self.assertEqual(decision["exit_code"], 2, reason)
            self.assertIn(reason, decision["reasons"], decision["reasons"])
            self.assertIn("hard compliance", decision["verdict_line"])

    def test_fails_this_round_still_blocks(self):
        text = OCT2_SOFT_REVIEW + "\nFAILS this round: facts → unsourced close → script\n"
        decision = review_gate.decide(text, True, _script(), HIGH_REVIEW, "short")
        self.assertFalse(decision["public_ready"])
        self.assertTrue(any(r.startswith("fails this round:") for r in decision["reasons"]))

    def test_fails_this_round_none_is_not_a_hard_failure(self):
        text = OCT2_SOFT_REVIEW + "\nFAILS this round: none\n"
        self.assertEqual(review_gate.hard_failure_reasons(text), [])
        decision = review_gate.decide(text, True, _script(), HIGH_REVIEW, "short")
        self.assertTrue(decision["public_ready"])

    def test_ready_verdict_is_unchanged(self):
        text = "VERDICT: [READY TO PUBLISH]\n\n✅ Auto-checks pass:\nHook | Facts | Compliance\n"
        decision = review_gate.decide(text, True, _script(), HIGH_REVIEW, "short")
        self.assertTrue(decision["public_ready"])
        self.assertTrue(decision["keep_model_report"])
        self.assertFalse(decision["soft_judgment_only"])

    def test_fallback_blocks_the_soft_override_whatever_the_scores(self):
        """#17's relaxation is only for gated LLM narration. A fallback flag wins."""
        import self_review

        llm = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), HIGH_REVIEW, "short")
        self.assertTrue(llm["public_ready"])
        self.assertTrue(llm["soft_judgment_only"])
        self.assertTrue(review_gate.narration_is_gated_llm(HIGH_REVIEW))

        cached = dict(HIGH_REVIEW)
        cached["narration_source"] = "cache"
        cache_decision = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), cached, "short")
        self.assertTrue(cache_decision["public_ready"])

        flagged = dict(HIGH_REVIEW)
        flagged["narration_fallback"] = True
        flagged["public_ok"] = True
        self.assertFalse(review_gate.self_review_clears_high_bar(flagged))
        self.assertFalse(review_gate.narration_is_gated_llm(flagged))
        blocked = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), flagged, "short")
        self.assertFalse(blocked["public_ready"])
        self.assertEqual(blocked["exit_code"], 2)
        self.assertFalse(blocked["soft_judgment_only"])
        self.assertTrue(any("fallback" in r for r in blocked["reasons"]))

        ready = "VERDICT: [READY TO PUBLISH]\n\nHook is specific.\n"
        ready_blocked = review_gate.decide(ready, True, _script(), flagged, "short")
        self.assertFalse(ready_blocked["public_ready"])

        sourced = dict(HIGH_REVIEW)
        sourced["narration_source"] = "fallback"
        sourced["public_ok"] = True
        self.assertTrue(review_gate.narration_is_fallback(sourced))
        source_blocked = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), sourced, "short")
        self.assertFalse(source_blocked["public_ready"])

        unmarked = dict(HIGH_REVIEW)
        unmarked.pop("narration_source")
        self.assertFalse(review_gate.narration_is_gated_llm(unmarked))
        self.assertTrue(review_gate.self_review_clears_high_bar(unmarked))
        no_source = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), unmarked, "short")
        self.assertFalse(no_source["public_ready"])
        self.assertIn("gated LLM", " ".join(no_source["reasons"]))

        rejected = self_review._reject_fallback(
            {"narration_fallback": True, "source": "fallback"},
            dict(HIGH_REVIEW),
        )
        self.assertGreaterEqual(rejected["overall"], 90)
        self.assertFalse(rejected["public_ok"])
        self.assertTrue(rejected["narration_fallback"])
        hard_fail = review_gate.decide(OCT2_SOFT_REVIEW, True, _script(), rejected, "short")
        self.assertFalse(hard_fail["public_ready"])
        self.assertFalse(review_gate.self_review_clears_high_bar(rejected))

    def test_below_bar_self_review_still_blocks_a_ready_verdict(self):
        text = "VERDICT: [READY TO PUBLISH]\n\nHook is specific.\n"
        decision = review_gate.decide(
            text, True, _script(), {"public_ok": False, "summary": "overall 40"}, "short")
        self.assertFalse(decision["public_ready"])
        self.assertIn("self-review", decision["verdict_line"])


class SoftReviewReportTests(unittest.TestCase):
    def test_verify_writes_ready_and_cache_does_not_restore_needs_fix(self):
        import tempfile
        from pathlib import Path
        import common

        script = _script()
        shots = json.dumps({"shots": [], "checks": {}})
        calls = {"n": 0}

        def fake_review(c, system, user):
            calls["n"] += 1
            return OCT2_SOFT_REVIEW

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "script.txt").write_text(script, encoding="utf-8")
            (run / "shots.json").write_text(shots, encoding="utf-8")
            (run / "self_review.json").write_text(json.dumps(HIGH_REVIEW), encoding="utf-8")
            (run / "subs.ass").write_text("1\n", encoding="utf-8")
            (run / "final.mp4").write_bytes(b"x")
            with mock.patch.object(common, "RUN", run), mock.patch.object(run_verify, "RUN", run), \
                    mock.patch.object(run_verify, "fetch_review", side_effect=fake_review), \
                    mock.patch.dict("os.environ", {"VIDEO_FORMAT": "short"}, clear=False):
                run_verify.main()
                report = (run / "report.md").read_text(encoding="utf-8")
                meta = json.loads((run / "verify_meta.json").read_text(encoding="utf-8"))
                self.assertEqual(review_gate.parse_report_verdict(report), "READY")
                self.assertNotIn("NEEDS FIX", report)
                self.assertTrue(meta["public_ready"])
                self.assertTrue(meta["soft_judgment_only"])
                self.assertEqual(meta["verdict"], "READY")
                self.assertTrue(review_gate.files_allow_public(run, "short"))
                self.assertEqual(calls["n"], 1)
                run_verify.main()
                self.assertEqual(calls["n"], 1)
                again = (run / "report.md").read_text(encoding="utf-8")
                self.assertEqual(review_gate.parse_report_verdict(again), "READY")
                self.assertTrue(review_gate.files_allow_public(run, "short"))
