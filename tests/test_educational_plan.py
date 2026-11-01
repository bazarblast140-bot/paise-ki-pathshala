"""Plan Shorts are labelled educational examples; the reviewer must not fail them as tips."""
import json
import unittest
from pathlib import Path

import approved_bank
import board_short
import review_gate
import run_verify
import trade_plan

ROOT = Path(__file__).resolve().parent.parent
IMPERATIVES = ("प्रॉफिट बुक करो", "लगाओ", "खरीदो", "बेचो", "पर लो।")


def _plan_files():
    for path in sorted((ROOT / "approved").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            trade_plan.plan_for(data.get("key"))
        except KeyError:
            continue
        yield data


class PlanBeatTests(unittest.TestCase):
    def test_plan_beats_are_described_not_ordered(self):
        seen = 0
        for data in _plan_files():
            seen += 1
            beats = data["beats"]
            blob = " ".join(beats.values())
            for word in IMPERATIVES:
                self.assertNotIn(word, blob, (data["key"], word))
            self.assertIn("इस उदाहरण में एंट्री {entry} होगी", beats["entry"], data["key"])
            self.assertIn("उदाहरण", beats["hook"], data["key"])
            for slot in ("{entry}", "{sl}", "{target}", "{rr}"):
                self.assertIn(slot, blob, (data["key"], slot))
            self.assertIn("{sl}", beats["sl"], data["key"])
            self.assertIn("{target}", beats["target"], data["key"])
            self.assertTrue(beats["invalidation"].strip(), data["key"])
        self.assertGreaterEqual(seen, 25)

    def test_word_counts_keep_voice_40_to_55s(self):
        lo, hi = trade_plan.TOTAL_WORDS
        for data in _plan_files():
            narration = approved_bank.narration_from(data)
            total = sum(len(narration["beats"][b].split()) for b in trade_plan.BEATS)
            self.assertGreaterEqual(total, lo, data["key"])
            self.assertLessEqual(total, hi, data["key"])
            seconds = total / trade_plan.CHARON_WPS
            self.assertGreaterEqual(seconds, 40.0, data["key"])
            self.assertLessEqual(seconds, 55.0, data["key"])

    def test_bull_engulf_entry_matches_chart(self):
        data = approved_bank.by_key("pat_bull_engulf")
        entry = data["beats"]["entry"]
        self.assertNotIn("ऊपर हाई तोड़ दे", entry)
        self.assertIn("हरी कैंडल का हाई {entry}", entry)
        plan = trade_plan.plan_for("pat_bull_engulf")
        green = plan.ohlc[3]
        self.assertEqual(plan.entry, green[1])
        self.assertTrue(any(c[1] > plan.entry for c in plan.ohlc[4:]))


class LabelTests(unittest.TestCase):
    def test_on_screen_label(self):
        self.assertEqual(trade_plan.EDU_LABEL, "शैक्षिक उदाहरण, स्टॉक टिप नहीं")
        self.assertEqual(trade_plan.ON_SCREEN_DISCLAIMER, trade_plan.EDU_LABEL)

    def test_caption_disclaimer_event_uses_label(self):
        import short_layout
        events = short_layout.overlay_plan(
            approved_bank.topic_from(approved_bank.by_key("pat_hammer")),
            {b: (i * 5.0, i * 5.0 + 5.0) for i, b in enumerate(trade_plan.BEATS)}, "x",
        )
        texts = [e["text"] for e in events if e.get("role") == "disclaimer"]
        self.assertIn(trade_plan.EDU_LABEL, texts)

    def test_description_line_carries_label_for_plan_topics(self):
        self.assertIn(trade_plan.EDU_LABEL, board_short.disclaimer_for({}, ["plan"]))
        self.assertEqual(board_short.disclaimer_for({}, []), board_short.DISCLAIMER)


class ReviewerRuleTests(unittest.TestCase):
    SCRIPT = ("बुलिश एंगल्फिंग पर खरीद कब करें? उदाहरण में एंट्री 124 और स्टॉप 102 का प्लान। "
              "इस उदाहरण में एंट्री 124 होगी। शैक्षिक उदाहरण, स्टॉक टिप नहीं।")

    def test_labelled_example_plan_qualifies(self):
        self.assertTrue(review_gate.educational_example_plan({"key": "pat_bull_engulf"}, self.SCRIPT))

    def test_named_stock_tip_does_not_qualify(self):
        tip = "TATAPOWER में एंट्री 124 पर खरीदो, टारगेट 160।"
        self.assertFalse(review_gate.educational_example_plan({"key": "pat_bull_engulf"}, tip))
        named = self.SCRIPT + " TATAPOWER चार्ट।"
        self.assertFalse(review_gate.educational_example_plan({"key": "pat_bull_engulf"}, named))

    def test_unlabelled_or_imperative_or_real_chart_does_not_qualify(self):
        key = {"key": "pat_bull_engulf"}
        self.assertFalse(review_gate.educational_example_plan(key, "एंट्री 124, स्टॉप 102।"))
        self.assertFalse(review_gate.educational_example_plan(key, self.SCRIPT + " स्टॉप लगाओ।"))
        self.assertFalse(review_gate.educational_example_plan(
            {"key": "pat_bull_engulf", "symbol": "WIPRO"}, self.SCRIPT))
        self.assertFalse(review_gate.educational_example_plan({"key": "nifty_close"}, self.SCRIPT))

    def test_prompt_exempts_labelled_example_but_still_fails_named_tips(self):
        p = run_verify.FINAL_SYSTEM
        self.assertIn("<educational_example_plan>", p)
        self.assertIn("Do NOT fail Compliance", p)
        self.assertIn("names a real stock/fund", p)
        self.assertIn("When the flag is false", p)
        self.assertIn(trade_plan.EDU_LABEL, p)

    def test_hard_reasons_still_catch_real_tips(self):
        text = "VERDICT: [NEEDS FIX — tip]\nCompliance FAIL: you should buy TATAPOWER"
        self.assertIn("advice or fake-expert framing", review_gate.hard_failure_reasons(text))


if __name__ == "__main__":
    unittest.main()
