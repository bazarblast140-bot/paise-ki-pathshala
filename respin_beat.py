"""Re-voice one beat of an existing Short and repaint the picture.

The other beats stay the Rudra audio already in the source final.mp4, cut at
whisper word times. This does not upload to YouTube or Instagram.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess

import approved_script
import board_short
import scene_frames
import scene_pace
import self_review
import sync_gate
from common import ROOT, cfg, die, ok

ORDER = approved_script.ORDER
RUDRA_VOICE_ID = "ypnkIsDASPgHZuanrF0q"
RUDRA_SPEED = 0.90
_COMPANIONS = (
    "thumbnail.jpg",
    "report.md",
    "verify_meta.json",
    "self_review.json",
    "topic.json",
)


def _script_tokens(text):
    """Per-word tokens, the same normalisation whisper words go through."""
    out = []
    for word in str(text or "").split():
        out.extend(sync_gate.tokens(word))
    return out


def _opening_tokens(text, n=3):
    return _script_tokens(text)[:n]


def _whisper_stream(words):
    stream = []
    for word in words or []:
        start = float(word.get("start") or 0)
        for tok in sync_gate.tokens(word.get("word") or ""):
            stream.append((start, tok))
    return stream


def _phrase_found(stream, index, want):
    """want matches at index, in order, with a few inserted whisper tokens."""
    cursor = index
    for tok in want:
        hit = None
        for j in range(cursor, min(len(stream), cursor + 4)):
            if sync_gate._near_token(tok, stream[j][1]):
                hit = j
                break
        if hit is None:
            return False
        cursor = hit + 1
    return True


def _find_opening(stream, after, want):
    """Start time of want, the first time it occurs at or after `after`."""
    if not want:
        return None
    for index, (start, tok) in enumerate(stream):
        if start + 1e-3 < after:
            continue
        if not sync_gate._near_token(want[0], tok):
            continue
        if _phrase_found(stream, index, want):
            return start
    return None


def _word_counts(beats):
    return [max(1, len(str((beats or {}).get(beat) or "").split())) for beat in ORDER]


def _proportional_time(words, counts, index, prev):
    """Whisper-word timestamp at this beat's share of the script word count."""
    total = sum(counts) or 1
    done = sum(counts[: index + 1])
    if not words:
        return None
    k = int(round(done / total * len(words)))
    k = min(max(k, 0), len(words) - 1)
    while k < len(words) and float(words[k].get("start") or 0) <= prev + 0.05:
        k += 1
    if k >= len(words):
        return None
    return float(words[k].get("start") or 0)


def _usable_cut(cut, prev, duration):
    return cut is not None and (prev + 0.05) < float(cut) < (float(duration) - 0.05)


def beat_bounds(beats, words, duration):
    """(start, end) for each beat on the source whisper timeline.

    Beat i ends where beat i+1's first three normalised words are first found
    after beat i starts. A miss uses that beat's share of the whisper word
    timestamps and logs the fallback. The opening of the file stays at 0.
    """
    if not any(str((beats or {}).get(beat) or "").split() for beat in ORDER):
        die("source script has no words to align")
    words = list(words or [])
    if not words:
        die("whisper beat boundary missing: no words")
    duration = float(duration or 0)
    if duration <= 0:
        duration = max(float(word.get("end") or 0) for word in words)
    stream = _whisper_stream(words)
    counts = _word_counts(beats)
    edges = [0.0]
    for index in range(len(ORDER) - 1):
        prev = edges[-1]
        nxt = ORDER[index + 1]
        found = _find_opening(stream, prev, _opening_tokens((beats or {}).get(nxt)))
        if _usable_cut(found, prev, duration):
            edges.append(float(found))
            continue
        cut = _proportional_time(words, counts, index, prev)
        if not _usable_cut(cut, prev, duration):
            shown = 0.0 if cut is None else float(cut)
            die(f"whisper beat boundary for {ORDER[index]} is empty ({prev:.2f}-{shown:.2f}s)")
        print(
            f"[respin] proportional word-count boundary for {nxt} at {cut:.2f}s "
            f"(opening not found after {prev:.2f}s)"
        )
        edges.append(float(cut))
    edges.append(duration)
    bounds = {}
    for index, beat in enumerate(ORDER):
        start, end = edges[index], edges[index + 1]
        if end <= start + 0.05:
            die(f"whisper beat boundary for {beat} is empty ({start:.2f}-{end:.2f}s)")
        bounds[beat] = (start, end)
    return bounds


def window_text(words, start, end):
    parts = []
    for word in words or []:
        stamp = float(word.get("start") or 0)
        if stamp < float(start) - 1e-3 or stamp >= float(end) - 1e-3:
            continue
        raw = str(word.get("word") or "").strip()
        if raw:
            parts.append(raw)
    return " ".join(parts)


def kept_recall(beats, words, bounds, skip):
    """Recall of each beat we will keep, against the whisper words inside its cut."""
    scores = {}
    for beat in ORDER:
        if beat == skip:
            continue
        start, end = bounds[beat]
        heard = window_text(words, start, end)
        scores[beat] = sync_gate.recall(str((beats or {}).get(beat) or ""), heard)
    return scores


def require_kept_recall(beats, words, bounds, skip):
    """Hard-fail before any paid voice when a kept slice does not match its text."""
    scores = kept_recall(beats, words, bounds, skip)
    low = [
        f"{beat} {score:.2f}"
        for beat, score in scores.items()
        if score + 1e-9 < sync_gate.MIN_RECALL
    ]
    if low:
        die(
            "kept beat recall below "
            f"{sync_gate.MIN_RECALL:g} before paid TTS: " + ", ".join(low)
        )
    detail = ", ".join(f"{beat} {scores[beat]:.2f}" for beat in ORDER if beat in scores)
    print(f"[respin] kept beat recall {detail}")
    return scores


def load_beats(directory):
    payload = approved_script.load_payload(directory, "")
    beats = {}
    if payload:
        raw = payload.get("beats") or {}
        beats = {beat: str(raw.get(beat) or "").strip() for beat in ORDER}
    if not all(beats.get(beat) for beat in ORDER):
        die(f"source run has no four-beat spoken text in {directory}")
    return beats


def find_mp4(directory):
    root = pathlib.Path(directory)
    direct = root / "final.mp4"
    if direct.is_file() and direct.stat().st_size > 1000:
        return direct
    found = next(root.rglob("final.mp4"), None)
    if found is None or found.stat().st_size < 1000:
        die(f"source run has no final.mp4 in {directory}")
    return found


def rudra_cfg():
    previous = os.environ.get("VIDEO_FORMAT")
    os.environ["VIDEO_FORMAT"] = "short"
    try:
        config = cfg()
    finally:
        if previous is None:
            os.environ.pop("VIDEO_FORMAT", None)
        else:
            os.environ["VIDEO_FORMAT"] = previous
    voice = dict(config.get("voice") or {})
    voice["voice_id"] = RUDRA_VOICE_ID
    voice["speed"] = RUDRA_SPEED
    voice["engine"] = "elevenlabs"
    voice["model"] = voice.get("model") or "eleven_multilingual_v2"
    config["voice"] = voice
    return config


def voice_one(text, dest, config=None):
    """ElevenLabs for this beat only. The string is not transliterated.

    PAID_TTS_ENABLED must be true. A closed switch spends 0 characters.
    """
    import eleven_voice
    text = str(text or "")
    if not text.strip():
        die("respin beat text is empty; refusing paid TTS with 0 characters spent")
    eleven_voice.require_paid_tts()
    config = config or rudra_cfg()
    print(f"[respin] voicing {len(text)} characters with Rudra at {RUDRA_SPEED}")
    if not board_short._eleven(text, pathlib.Path(dest), config):
        die("ElevenLabs did not voice the respin beat; not a credit error")
    return pathlib.Path(dest)


def cut_audio(mp4, start, end, dest):
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-ss", f"{float(start):.3f}", "-to", f"{float(end):.3f}",
            "-i", str(mp4),
            "-vn", "-ac", "1", "-ar", "44100",
            str(dest),
        ],
        check=True,
    )
    return dest


def _topic_from(directory, beats):
    path = pathlib.Path(directory) / "topic.json"
    topic = None
    if path.is_file():
        try:
            topic = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            topic = None
    if not isinstance(topic, dict) or not topic.get("beats"):
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = json.loads(json.dumps(
            next(item for item in bank if item["key"] == "risk_daily_loss_limit")
        ))
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    topic["_spoken_beats"] = {beat: beats[beat] for beat in ORDER}
    topic["_voice"] = {"voice_id": RUDRA_VOICE_ID, "speed": RUDRA_SPEED, "words_per_second": 1.9}
    return topic


def _copy_companions(source, run_dir):
    root = pathlib.Path(source)
    for name in _COMPANIONS:
        src = root / name
        if not src.is_file():
            found = next(root.rglob(name), None)
            src = found if found is not None else src
        if src.is_file():
            shutil.copyfile(src, pathlib.Path(run_dir) / name)


def mux_spliced(scenes, wavs, beat_times):
    """Picture from the new cards. Audio is the splice, with no second music bed.

    Slices cut from final.mp4 already contain that file's mix. Mixing the bed
    again would play it twice under the kept beats.
    """
    durations = [float(scene["end"]) - float(scene["start"]) for scene in scenes]
    frames = [scene.get("frame") for scene in scenes]
    if not board_short._layered(frames):
        die("respin frames have no card layers")
    faults = board_short.scene_transition.transition_faults(frames, board_short.SCENE_TRANSITIONS)
    if faults:
        die("shaped text was sliced at a transition: " + "; ".join(faults[:4]))
    run = board_short.RUN
    board_short.scene_transition.write_timeline(
        frames, durations, run / "silent.mp4", fps=30,
        transitions=board_short.SCENE_TRANSITIONS, fade_s=board_short.SCENE_FADE_S,
        overlay=scene_frames.paint_progress,
    )
    parts = []
    for beat in ORDER:
        t0, t1 = beat_times[beat]
        wav = run / f"voice_{beat}.wav"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-i", str(wavs[beat]),
                "-af", f"apad,atrim=0:{t1 - t0:.3f}",
                "-ar", "44100", "-ac", "2", str(wav),
            ],
            check=True,
        )
        parts.append(wav.name)
    (run / "voice_list.txt").write_text("\n".join(f"file '{name}'" for name in parts), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "voice_list.txt",
         "-c", "copy", "voice.wav"],
        cwd=run, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", "voice.wav", "-c:a", "aac", "-b:a", "192k", "audio.m4a"],
        cwd=run, check=True,
    )
    (run / "subs.ass").write_text("", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", "silent.mp4", "-i", "audio.m4a",
         "-vf", "fps=30", *board_short.X264, "-c:a", "copy", "-shortest", "final.mp4"],
        cwd=run, check=True,
    )


def render(topic, spoken, wavs):
    raw = {beat: max(0.4, board_short.duration(wavs[beat])) for beat in ORDER}
    beat_times, total = board_short._timeline(raw, ORDER)
    scenes = scene_pace.plan_scenes(beat_times, beats=ORDER)
    try:
        words = sync_gate.transcribe_beats({beat: wavs[beat] for beat in ORDER}, beat_times)
    except Exception as exc:  # noqa: BLE001 — a missed transcript is a hard stop
        die(f"transcription failed; refusing the respin: {exc}")
    if not words:
        die("transcription failed; refusing the respin")
    topic["_spoken_beats"] = {beat: spoken[beat] for beat in ORDER}
    board_short._CUE_CACHE.clear()
    board_short._install_scene_cards(topic, spoken, beat_times, scenes, words)
    groups = {}
    for scene in scenes:
        groups.setdefault(scene["beat"], []).append(scene)
    for beat, group in groups.items():
        frames, flags = scene_frames.frames_for(topic, beat, len(group), {"mode": "schematic"})
        if len(frames) < len(group):
            die(f"Chart Board failed for {beat}")
        for index, (scene, frame, flag) in enumerate(zip(group, frames, flags)):
            scene["frame"] = frame
            scene["callout"] = bool(flag)
            path = board_short.RUN / f"sc_{beat}_{index}.png"
            frame.save(path)
            scene["png"] = str(path)
    hook = str(spoken.get("hook") or "").split("।")[0].strip()
    narration = {
        "title": str(topic.get("topic") or "पाठ"),
        "hook_line": hook,
        "beats": {beat: spoken[beat] for beat in ORDER},
        "source": "respin",
    }
    try:
        mux_spliced(scenes, wavs, beat_times)
    except Exception as exc:  # noqa: BLE001
        die(f"respin render crashed: {exc}")
    board_short.write_compat(topic, narration, spoken, beat_times)
    (board_short.RUN / "tts_text.txt").write_text(
        "".join(f"tts_{beat}: {spoken[beat]}\n" for beat in ORDER),
        encoding="utf-8",
    )
    (board_short.RUN / "narration.json").write_text(
        json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return narration, scenes, beat_times, words, total


def hard_gates(topic, narration, scenes, beat_times, words, total):
    """Numbers, beat recall, duration, safe area. A crash is a die() above."""
    if not words or not any(str(word.get("word") or "").strip() for word in words):
        die("transcription failed; refusing the respin")
    score, notes = self_review.score_accuracy(narration, topic, None)
    if score < self_review.PART_BAR:
        die("numbers/maths failed; refusing the respin: " + "; ".join(notes))
    rows = []
    for scene in scenes:
        frame = scene.get("frame")
        rows.append({
            "start": scene["start"],
            "end": scene["end"],
            "beat": scene["beat"],
            "kind": scene.get("kind") or "",
            "show": sync_gate.lesson_lines(frame) if frame is not None else [],
        })
    report = sync_gate.check(words, rows, narration.get("beats") or {}, beat_times, [])
    low = [
        row for row in (report.get("beats") or [])
        if float(row.get("recall") or 0) + 1e-9 < sync_gate.MIN_RECALL
    ]
    if low:
        detail = ", ".join(f"{row['beat']} {row['recall']}" for row in low)
        die(f"beat recall below {sync_gate.MIN_RECALL}: {detail}")
    lo, hi = board_short.live_voice_bounds(topic)
    if total < lo or total > hi:
        die(f"duration outside {lo:.0f}-{hi:.0f}s; refusing the respin: {total:.1f}s")
    final_path = board_short.RUN / "final.mp4"
    final_dur = board_short.duration(final_path)
    if final_dur < lo or final_dur > hi:
        die(f"final.mp4 duration outside {lo:.0f}-{hi:.0f}s; refusing the respin: {final_dur:.1f}s")
    outside = scene_frames.frames_safe_area_notes(
        [scene.get("frame") for scene in scenes if scene.get("frame") is not None]
    )
    if outside:
        die("text outside the safe area; refusing the respin: " + "; ".join(outside[:4]))
    ok(
        f"respin hard gates passed ({final_dur:.1f}s, accuracy {score}, "
        f"beat recall >= {sync_gate.MIN_RECALL})"
    )
    return report


def respin(source, beat, text, run_dir=None):
    beat = str(beat or "").strip()
    if beat not in ORDER:
        die(f"respin beat must be one of {', '.join(ORDER)}")
    source = pathlib.Path(source)
    run_dir = pathlib.Path(run_dir or board_short.RUN)
    run_dir.mkdir(parents=True, exist_ok=True)
    old = load_beats(source)
    mp4 = find_mp4(source)
    try:
        words = sync_gate.transcribe_file(mp4)
    except Exception as exc:  # noqa: BLE001
        die(f"transcription failed on the source video: {exc}")
    if not words:
        die("transcription failed on the source video")
    duration = board_short.duration(mp4)
    bounds = beat_bounds(old, words, duration)
    require_kept_recall(old, words, bounds, beat)
    spoken = dict(old)
    spoken[beat] = str(text)
    _copy_companions(source, run_dir)
    wavs = {}
    config = rudra_cfg()
    for name in ORDER:
        dest = run_dir / f"tts_{name}.mp3"
        if name == beat:
            voice_one(spoken[name], dest, config)
        else:
            start, end = bounds[name]
            cut_audio(mp4, start, end, dest)
            print(f"[respin] kept {name} {start:.2f}-{end:.2f}s from {mp4.name}")
        wavs[name] = dest
    topic = _topic_from(source, spoken)
    narration, scenes, beat_times, heard, total = render(topic, spoken, wavs)
    hard_gates(topic, narration, scenes, beat_times, heard, total)
    ok(f"respin wrote {run_dir / 'final.mp4'} (no upload)")
    return run_dir / "final.mp4"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Re-voice one beat and repaint. No upload.")
    parser.add_argument("--source", default=os.environ.get("RESPIN_SOURCE", "run"))
    parser.add_argument("--beat", default=os.environ.get("RESPIN_BEAT", ""))
    parser.add_argument("--text", default=os.environ.get("RESPIN_TEXT", ""))
    args = parser.parse_args(argv)
    respin(args.source, args.beat, args.text, run_dir=board_short.RUN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
