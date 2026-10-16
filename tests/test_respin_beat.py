"""One beat is re-voiced. The other beats stay slices of the source video."""
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
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


# The source artifact's tts_text.txt. Prefixes are part of the file.
SOURCE_TTS = """\
tts_hook: डेली लॉस लिमिट क्यों ज़रूरी है? दो लाख रुपये की पूँजी पर बुरा दिन पूरा महीना बिगाड़ देता है।
tts_pehchan: अब इसे पहचानें: दो लाख रुपये की पूँजी पर दो प्रतिशत की हद। उदाहरण: पूँजी दो लाख रुपये, दिन की हद दो प्रतिशत बराबर चार हज़ार रुपये। यानी चार हज़ार रुपये का नुकसान या चार हज़ार रुपये लाभ, दोनों। यही हद है।
tts_matlab: इसका मतलब: दो प्रतिशत की हद बुरे दिन को छोटा रखती है, ताकि चार हज़ार रुपये से ज़्यादा न जाए। दो लाख रुपये का दो प्रतिशत बराबर चार हज़ार रुपये, यानी पूँजी का केवल दो प्रतिशत।
tts_karein: अब आगे: हद पहले लिखो, हिट होते ही टर्मिनल बंद। स्टॉप लॉस और एंट्री से पहले चार हज़ार रुपये तय करो, फिर उससे जुड़े रहो। हद अपनी क्षमता से तय करो, ये उदाहरण है।
"""
SPANS = {
    "hook": (0.5, 7.0),
    "pehchan": (7.0, 24.0),
    "matlab": (24.0, 43.0),
    "karein": (43.0, 60.8),
}


def _source_beats():
    import approved_script
    return approved_script.beats_from_tts_text(SOURCE_TTS)


def _drift(word):
    # Nukta and anusvara/chandrabindu are the spellings that missed exact match.
    return word.replace("\u093c", "").replace("\u0902", "\u0901").strip("।?!.,:;")


def _place(parts, t0, t1):
    step = (t1 - t0) / max(1, len(parts))
    rows = []
    for index, part in enumerate(parts):
        start = t0 + index * step
        rows.append({
            "word": part,
            "start": round(start, 3),
            "end": round(min(t1, start + step * 0.8), 3),
        })
    return rows


def _synthetic_words(beats, garbage=8, replace_openings=False):
    # No digits: a "2" in the first whisper tokens exact-matches दो and the old aligner moves.
    marks = "अआइईउऊएऐ"
    words = []
    for index in range(garbage):
        start = index * 0.04
        words.append({
            "word": "क्ववव" + marks[index],
            "start": round(start, 3),
            "end": round(start + 0.03, 3),
        })
    for beat in ORDER:
        parts = [_drift(word) for word in beats[beat].split()]
        parts = [part for part in parts if part]
        if replace_openings and beat != "hook":
            parts = [f"zz{beat}{i}" for i in range(min(3, len(parts)))] + parts[3:]
        t0, t1 = SPANS[beat]
        words.extend(_place(parts, t0, t1))
    return words


class RespinBoundaryTests(unittest.TestCase):
    def test_drifted_tts_text_cuts_where_the_next_opening_is_said(self):
        beats = _source_beats()
        words = _synthetic_words(beats)
        pieces = []
        for beat in ORDER:
            pieces.extend(beats[beat].split())
        collapsed = board_short._align_starts(pieces, words, 0.0, 60.8)
        self.assertTrue(collapsed)
        self.assertTrue(all(abs(item - collapsed[0]) < 1e-6 for item in collapsed))
        bounds = respin_beat.beat_bounds(beats, words, 60.8)
        self.assertEqual(bounds["hook"][0], 0.0)
        self.assertAlmostEqual(bounds["pehchan"][0], 7.0, delta=0.05)
        self.assertAlmostEqual(bounds["matlab"][0], 24.0, delta=0.05)
        self.assertAlmostEqual(bounds["karein"][0], 43.0, delta=0.05)
        self.assertAlmostEqual(bounds["karein"][1], 60.8, delta=0.01)
        scores = respin_beat.require_kept_recall(beats, words, bounds, "pehchan")
        for beat, score in scores.items():
            self.assertGreaterEqual(score, 0.5, beat)

    def test_missing_opening_uses_proportional_word_times(self):
        beats = _source_beats()
        words = _synthetic_words(beats, garbage=0, replace_openings=True)
        out = io.StringIO()
        with redirect_stdout(out):
            bounds = respin_beat.beat_bounds(beats, words, 60.8)
        log = out.getvalue()
        self.assertIn("proportional word-count boundary for pehchan", log)
        self.assertIn("proportional word-count boundary for matlab", log)
        self.assertIn("proportional word-count boundary for karein", log)
        self.assertAlmostEqual(bounds["pehchan"][0], 7.0, delta=0.05)
        self.assertAlmostEqual(bounds["matlab"][0], 24.0, delta=0.05)
        self.assertAlmostEqual(bounds["karein"][0], 43.0, delta=0.05)
        scores = respin_beat.kept_recall(beats, words, bounds, "pehchan")
        for beat, score in scores.items():
            self.assertGreaterEqual(score, 0.5, beat)

    def test_low_kept_recall_refuses_before_elevenlabs(self):
        beats = _source_beats()
        words = [
            {"word": "अलग", "start": index * 0.4, "end": index * 0.4 + 0.2}
            for index in range(150)
        ]
        voiced = []

        def voice(*_args, **_kwargs):
            voiced.append("eleven")
            raise AssertionError("ElevenLabs was called before the boundary check")

        err = io.StringIO()
        with mock.patch.object(respin_beat, "load_beats", return_value=beats), \
             mock.patch.object(respin_beat, "find_mp4", return_value=Path("final.mp4")), \
             mock.patch.object(respin_beat.sync_gate, "transcribe_file", return_value=words), \
             mock.patch.object(board_short, "duration", return_value=60.8), \
             mock.patch.object(respin_beat, "voice_one", side_effect=voice), \
             redirect_stderr(err), self.assertRaises(SystemExit):
            respin_beat.respin("run", "pehchan", OWNER_PEHCHAN)
        self.assertEqual(voiced, [])
        self.assertIn("before paid TTS", err.getvalue())
