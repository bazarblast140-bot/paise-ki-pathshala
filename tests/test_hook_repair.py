"""A live hook that stays over its window is repaired or clause-trimmed.

Run 36985922021 blocked after one DeepSeek call: the hook was 28 words and
the 13-23 window could not drop the opening question. A blocked run must
not mark the topic used_short.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import board_short
import common
import pick_topic
import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
HOOK = "क्या यह सेटअप गलत है?"


def _words(n, salt, lead=""):
    parts = str(lead).split() + str(salt).split()
    if len(parts) >= n:
        return " ".join(parts[:n])
    return " ".join(parts + ["नियम"] * (n - len(parts)))


def _live_topic():
    return {
        "key": "hammer",
        "cat": "CANDLESTICK",
        "topic": "Hammer कैंडल — डाउनट्रेंड के बाद लंबी निचली wick",
        "_live_short": True,
        "_live_beats": "six",
        "_voice": {"words_per_second": 1.9, "voice_id": "ypnkIsDASPgHZuanrF0q"},
        "thumb": ["ये कैंडल दिखे", "तो गिरावट ख़त्म?"],
        "beats": {
            "hook": ["नीचे लंबी wick", "गिरावट के बाद ख़रीदार लौटे?"],
            "pehchan": ["पहचान", "छोटी बॉडी ऊपर"],
            "matlab": ["मतलब", "नियम कदम"],
            "karein": ["क्या करें", "कदम नियम"],
        },
        "facts": ["निचली wick बॉडी से कम से कम 2 गुना"],
    }


def _on_target(hook):
    targets = board_short.live_beat_targets(1.9)
    beats = {
        beat: _words(targets[beat], "नियम", HOOK if beat == "hook" else "")
        for beat in board_short.LIVE_ORDER
    }
    beats["hook"] = hook
    return {
        "title": "यह नियम है",
        "hook_line": HOOK,
        "beats": beats,
    }


def _clause_hook():
    """28 words. The opening question stays, and whole sentences fit 13-23."""
    parts = [
        HOOK,
        "नियम कदम एक है।",
        "नियम कदम दो है।",
        "नियम कदम तीन है।",
        "नियम कदम चार है।",
        "नियम कदम पांच है।",
        "और छठा नियम।",
    ]
    return " ".join(parts)


class HookFitTests(unittest.TestCase):
    def test_28_word_hook_is_clause_trimmed_into_the_window(self):
        topic = _live_topic()
        hook = _clause_hook()
        self.assertEqual(len(hook.split()), 28)
        lo, hi = board_short.beat_word_window(
            board_short.live_beat_targets(1.9)["hook"], board_short.LIVE_BEAT_TOLERANCE)
        self.assertEqual((lo, hi), (13, 23))
        script = _on_target(hook)
        calls = []

        def fake(*_args, **_kwargs):
            calls.append(1)
            return script

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}):
            out, source = board_short.narration(topic, {"models": {"script_model": "deepseek-flash"}, "voice": {}})
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 1)
        got = len(out["beats"]["hook"].split())
        self.assertGreaterEqual(got, 13)
        self.assertLessEqual(got, 23)
        self.assertTrue(out["beats"]["hook"].startswith(HOOK))
        self.assertEqual(board_short.check_narration(out, topic), [])

    def test_28_word_hook_without_a_clause_break_is_repaired(self):
        topic = _live_topic()
        hook = HOOK + " " + " ".join(["नियम"] * 23)
        self.assertEqual(len(hook.split()), 28)
        bad = _on_target(hook)
        good = _on_target(_words(18, "नियम", HOOK))
        calls = []

        def fake(system, user, model=None, max_tokens=2000, retries=3, attempts=None):
            calls.append(user)
            return bad if len(calls) == 1 else good

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(board_short, "RUN", Path(tmp)), \
             mock.patch.object(board_short, "claude_json", side_effect=fake), \
             mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}):
            out, source = board_short.narration(topic, {"models": {"script_model": "deepseek-flash"}, "voice": {}})
        self.assertEqual(source, "llm")
        self.assertEqual(len(calls), 2)
        self.assertIn("REWRITE hook:", calls[1])
        self.assertIn("13-23", calls[1])
        self.assertIn("Keep the first question", calls[1])
        self.assertIn("allowed numbers", calls[1])
        got = len(out["beats"]["hook"].split())
        self.assertGreaterEqual(got, 13)
        self.assertLessEqual(got, 23)
        self.assertTrue(out["beats"]["hook"].startswith(HOOK))
        self.assertEqual(board_short.check_narration(out, topic), [])

    def test_overlong_question_is_not_cut_on_a_comma(self):
        question = (
            "क्या यह सेटअप गलत है, और नियम कदम एक है, "
            "और नियम कदम दो है, और नियम कदम तीन है, "
            "और नियम कदम चार है, और नियम कदम पांच है?"
        )
        self.assertGreater(len(question.split()), 23)
        fitted = board_short.fit_live_hook(question, 13, 23, "")
        self.assertEqual(fitted, " ".join(question.split()))
        self.assertIn(",", fitted)
        self.assertTrue(fitted.endswith("?"))
        mid = (
            "क्या premium देना खर्च है, और यह वाक्य यहीं खत्म नहीं होता "
            "क्योंकि अल्पविराम के बाद और शब्द हैं और शब्द हैं और शब्द हैं?"
        )
        self.assertEqual(
            board_short.fit_live_hook(mid, 4, 8, ""),
            " ".join(mid.split()),
        )


class UsedShortFlagTests(unittest.TestCase):
    def _bank(self, root, used_short=False):
        topic = {
            "id": 501,
            "used": False,
            "used_short": used_short,
            "key": "sample_hook_rule",
            "cat": "BASICS",
            "topic": "एक छोटा नियम जो हर बार नया है",
            "thumb": ["नियम देखो", "क्या गलत है"],
            "sticker": "नियम",
            "beats": {
                "hook": ["सवाल यह है", "क्या नियम गलत है"],
                "pehchan": ["पहचान", "ऊपर छोटी बॉडी"],
                "matlab": ["मतलब", "नीचे लंबी रेखा"],
                "karein": ["करें", "अगला कदम देखो"],
            },
            "facts": ["नियम एक कदम है"],
            "seo": "छोटा नियम",
        }
        if used_short:
            topic["used_short_at"] = "2026-10-02T08:48:22Z"
        path = root / "topic_bank.json"
        path.write_text(json.dumps({"topics": [topic]}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (root / "data").mkdir(exist_ok=True)
        (root / "run").mkdir(exist_ok=True)
        return path, topic

    def test_blocked_run_leaves_topic_flags_unchanged(self):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        live = next(row for row in bank["topics"] if row["id"] == 79)
        self.assertTrue(live["used_short"])
        self.assertEqual(live["key"], "intraday_risk_reward")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _topic = self._bank(root, used_short=False)
            before = path.read_bytes()
            run = root / "run"
            env = {k: v for k, v in os.environ.items() if k != "TOPIC_KEY"}
            env["VIDEO_FORMAT"] = "short"
            with mock.patch.object(pick_topic, "ROOT", root), \
                 mock.patch.object(common, "RUN", run), \
                 mock.patch.dict(os.environ, env, clear=True):
                pick_topic.main()
            self.assertEqual(path.read_bytes(), before)
            topic = json.loads((run / "topic.json").read_text(encoding="utf-8"))
            topic["_live_short"] = True
            topic["_voice"] = {"words_per_second": 1.9}
            with mock.patch.object(board_short, "RUN", run), \
                 mock.patch.object(board_short, "claude_json", return_value={"title": "खाली"}), \
                 mock.patch.object(board_short, "_content_ok", return_value={"passed": True, "summary": "ok"}):
                with self.assertRaises(board_short.NarrationSkip):
                    board_short.narration(topic, {"models": {"script_model": "deepseek-flash"}, "voice": {}})
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["topics"][0]["used_short"])
        again = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        row = next(item for item in again["topics"] if item["id"] == 79)
        self.assertTrue(row["used_short"])
        self.assertEqual(row.get("used_short_at"), live.get("used_short_at"))

    def test_used_short_is_set_only_after_a_successful_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _topic = self._bank(root, used_short=False)
            run = root / "run"
            (run / "topic.json").write_text(json.dumps({"id": 501}), encoding="utf-8")
            with mock.patch.object(run_pipeline, "ROOT", root), \
                 mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.dict(os.environ, {"VIDEO_FORMAT": "short"}, clear=False):
                os.environ.pop("TOPIC_KEY", None)
                run_pipeline.settle_topic(False)
                fresh = json.loads(path.read_text(encoding="utf-8"))["topics"][0]
                self.assertFalse(fresh["used_short"])
                self.assertEqual(fresh["used_short_fails"], 1)
                run_pipeline.settle_topic(True)
                done = json.loads(path.read_text(encoding="utf-8"))["topics"][0]
                self.assertTrue(done["used_short"])
                self.assertTrue(done.get("used_short_at"))
                self.assertNotIn("used_short_fails", done)

            path, _topic = self._bank(root, used_short=True)
            (run / "topic.json").write_text(json.dumps({"id": 501}), encoding="utf-8")
            with mock.patch.object(run_pipeline, "ROOT", root), \
                 mock.patch.object(run_pipeline, "RUN", run), \
                 mock.patch.dict(os.environ, {"VIDEO_FORMAT": "short"}, clear=False):
                run_pipeline.settle_topic(False)
            kept = json.loads(path.read_text(encoding="utf-8"))["topics"][0]
            self.assertTrue(kept["used_short"])
            self.assertEqual(kept["used_short_at"], "2026-10-02T08:48:22Z")


if __name__ == "__main__":
    unittest.main()
