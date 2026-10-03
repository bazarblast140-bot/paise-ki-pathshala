"""One beat is re-voiced. The other beats stay slices of the source video."""
import io
import os
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import board_short
import respin_beat

ROOT = Path(__file__).resolve().parent.parent
ORDER = ("hook", "pehchan", "matlab", "karein")
OWNER_PEHCHAN = (
    "अब इसे पहचानें। पूँजी दो लाख रुपये है, तो दिन की हद दो प्रतिशत यानी "
    "चार हज़ार रुपये बैठती है। यह चार हज़ार रुपये दिन का कुल नुकसान है, "
    "हर ट्रेड का नहीं। यह हद लिखी हुई रहती है।"
)


def _beats():
    return {
        "hook": "पूँजी हद",
        "pehchan": "नियम साफ",
        "matlab": "मतलब सीधा",
        "karein": "टर्मिनल बंद",
    }


def _words():
    rows = [
        ("पूँजी", 0.2), ("हद", 0.8),
        ("नियम", 3.0), ("साफ", 3.6),
        ("मतलब", 6.0), ("सीधा", 6.6),
        ("टर्मिनल", 9.0), ("बंद", 9.6),
    ]
    return [{"word": word, "start": start, "end": start + 0.4} for word, start in rows]


class RespinBeatTests(unittest.TestCase):
    def test_other_beats_are_cut_at_whisper_boundaries(self):
        bounds = respin_beat.beat_bounds(_beats(), _words(), 12.0)
        self.assertEqual(bounds["hook"][0], 0.0)
        self.assertAlmostEqual(bounds["pehchan"][0], 3.0)
        self.assertAlmostEqual(bounds["matlab"][0], 6.0)
        self.assertAlmostEqual(bounds["karein"][0], 9.0)
        self.assertAlmostEqual(bounds["karein"][1], 12.0)
        self.assertLess(bounds["hook"][1], bounds["pehchan"][0] + 1e-6)

    def test_only_the_named_beat_is_voiced_and_the_text_is_not_rewritten(self):
        self.assertGreater(len(OWNER_PEHCHAN), 140)
        self.assertLess(len(OWNER_PEHCHAN), 200)
        voiced = []
        cuts = []

        def voice(text, dest, config=None):
            voiced.append((text, config["voice"]["voice_id"], config["voice"]["speed"]))

        def cut(mp4, start, end, dest):
            cuts.append((round(start, 2), round(end, 2)))

        with mock.patch.object(respin_beat, "load_beats", return_value=_beats()), \
             mock.patch.object(respin_beat, "find_mp4", return_value=Path("final.mp4")), \
             mock.patch.object(respin_beat.sync_gate, "transcribe_file", return_value=_words()), \
             mock.patch.object(board_short, "duration", return_value=12.0), \
             mock.patch.object(respin_beat, "voice_one", side_effect=voice), \
             mock.patch.object(respin_beat, "cut_audio", side_effect=cut), \
             mock.patch.object(respin_beat, "_copy_companions"), \
             mock.patch.object(respin_beat, "_topic_from", return_value={}), \
             mock.patch.object(respin_beat, "render", return_value=({}, [], {}, [], 55.0)), \
             mock.patch.object(respin_beat, "hard_gates"):
            respin_beat.respin("run", "pehchan", OWNER_PEHCHAN)
        self.assertEqual(voiced, [(OWNER_PEHCHAN, "ypnkIsDASPgHZuanrF0q", 0.90)])
        self.assertEqual(len(cuts), 3)

    def test_paid_switch_off_spends_zero_characters(self):
        with mock.patch.dict(os.environ, {"PAID_TTS_ENABLED": "false"}, clear=False):
            with self.assertRaises(SystemExit) as caught:
                respin_beat.voice_one(OWNER_PEHCHAN, Path("beat.mp3"))
        self.assertIn("0 characters spent", str(caught.exception))

    def test_rudra_voice_sends_the_owner_line_unchanged(self):
        sent = {}

        def fake(text, path, config):
            sent["text"] = text
            sent["voice"] = config["voice"]["voice_id"]
            sent["speed"] = config["voice"]["speed"]
            return True

        with mock.patch.dict(os.environ, {"PAID_TTS_ENABLED": "true"}, clear=False), \
             mock.patch.object(board_short, "_eleven", side_effect=fake):
            respin_beat.voice_one(OWNER_PEHCHAN, Path("beat.mp3"), respin_beat.rudra_cfg())
        self.assertEqual(sent["text"], OWNER_PEHCHAN)
        self.assertEqual(sent["voice"], "ypnkIsDASPgHZuanrF0q")
        self.assertEqual(sent["speed"], 0.90)

    def test_low_beat_recall_refuses(self):
        import self_review
        narration = {"beats": _beats(), "title": "दिन की हद"}
        scenes = [
            {"beat": beat, "start": float(index), "end": float(index + 1), "frame": None}
            for index, beat in enumerate(ORDER)
        ]
        times = {beat: (float(index), float(index + 1)) for index, beat in enumerate(ORDER)}
        topic = {"_live_short": True, "_live_beats": "four"}
        heard = [{"word": "अलग", "start": 0.2, "end": 0.6}]
        err = io.StringIO()
        with mock.patch.object(self_review, "score_accuracy", return_value=(100, [])), \
             redirect_stderr(err), self.assertRaises(SystemExit):
            respin_beat.hard_gates(topic, narration, scenes, times, heard, 55.0)
        self.assertIn("beat recall", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            respin_beat.hard_gates(topic, narration, scenes, times, [], 55.0)
        self.assertIn("transcription failed", err.getvalue())

    def test_workflows_respin_without_upload_and_publish_a_new_video(self):
        respin = (ROOT / ".github" / "workflows" / "respin-beat.yml").read_text(encoding="utf-8")
        publish = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", respin)
        self.assertIn("source_run_id:", respin)
        self.assertIn("RESPIN_TEXT:", respin)
        self.assertIn("ypnkIsDASPgHZuanrF0q", respin)
        self.assertIn("PAID_TTS_ENABLED:", respin)
        self.assertIn("upload-artifact@v4", respin)
        self.assertIn("group: paise-ki-pathshala-publish", respin)
        self.assertNotIn("publish.py", respin)
        self.assertNotIn("publish_instagram.py", respin)
        self.assertIn("workflow_dispatch:", publish)
        self.assertIn("source_run_id:", publish)
        self.assertIn("default: public", publish)
        self.assertIn("python publish.py", publish)
        self.assertIn("python publish_instagram.py", publish)
        self.assertIn("EXPECTED_CHANNEL_ID:", publish)
        self.assertIn("data/published.json", publish)
        self.assertIn("group: paise-ki-pathshala-publish", publish)
        self.assertNotIn("make_public.py", publish)
        self.assertNotIn("nxfA4sACCkU", publish)
        self.assertNotIn("videos().update", publish)
