#!/usr/bin/env python3
"""Rebuild final.mp4 from a finished run folder. No speech synthesis.

voice.wav is the narration, reused as-is. Pictures, captions, and the
thumbnail are painted again. The audio post chain matches assemble: whoosh
under the voice, no music bed, then loudnorm -14, then mux.

    python rerender_from_voice.py --run-dir <dir> --out <mp4>

Word timestamps are read from words.json when that file is in the folder.
Otherwise a local faster-whisper pass aligns voice.wav. If that model is
not available, words are spread across the beat boundaries.
"""
import argparse
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import board_short
import scene_frames
import scene_pace
import scene_transition

ORDER = ("hook", "pehchan", "matlab", "karein")
ROOT = Path(__file__).resolve().parent


def parse_script(text):
    """TITLE / HOOK / [SEGMENT …] from script.txt."""
    lines = text.splitlines()
    section = None
    buf = []
    chunks = {}

    def flush():
        if section:
            chunks[section] = "\n".join(buf).strip()

    for line in lines:
        match = re.match(
            r"^(TITLE|HOOK(?:\s*\([^)]*\))?|BODY|CTA|DISCLAIMER LINE|FACT LEDGER):\s*$",
            line.strip(),
        )
        if match:
            flush()
            name = match.group(1)
            section = "HOOK" if name.startswith("HOOK") else name
            buf = []
            continue
        buf.append(line)
    flush()
    hook = " ".join((chunks.get("HOOK") or "").split())
    beats = {"hook": hook}
    body = chunks.get("BODY") or ""
    for match in re.finditer(
        r"\[SEGMENT\s+([A-Za-z_]+)\]\s*(.*?)(?=\n\[SEGMENT|\Z)",
        body,
        re.S,
    ):
        beats[match.group(1).lower()] = " ".join(match.group(2).split())
    hook_line = hook
    if "?" in hook:
        hook_line = hook.split("?", 1)[0].strip() + "?"
    elif "।" in hook:
        hook_line = hook.split("।", 1)[0].strip() + "।"
    title = " ".join((chunks.get("TITLE") or "").split())
    return {"title": title, "hook_line": hook_line.strip(), "beats": beats}


def wav_duration(path):
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate() or 1
        return handle.getnframes() / float(rate)


def beat_times_from_review(review, duration, beats=None):
    """First and last scene of each beat. Scale only when the file disagrees."""
    scenes = (review or {}).get("scenes") or []
    spans = {}
    for scene in scenes:
        beat = str(scene.get("beat") or "")
        if beat not in ORDER:
            continue
        start, end = float(scene["start"]), float(scene["end"])
        if beat not in spans:
            spans[beat] = [start, end]
        else:
            spans[beat][0] = min(spans[beat][0], start)
            spans[beat][1] = max(spans[beat][1], end)
    needed = [beat for beat in ORDER if str((beats or {}).get(beat) or "").strip()]
    if needed and any(beat not in spans for beat in needed):
        return None
    if not spans:
        return None
    last = max(pair[1] for pair in spans.values())
    if last > 0 and abs(last - float(duration)) > 0.4:
        scale = float(duration) / last
        spans = {beat: [start * scale, end * scale] for beat, (start, end) in spans.items()}
    return {beat: (start, end) for beat, (start, end) in spans.items()}


def beat_times_from_words(beats, duration, order=ORDER):
    present = [beat for beat in order if (beats.get(beat) or "").strip()]
    weights = [max(1, len(beats[beat].split())) for beat in present]
    total = float(sum(weights)) or 1.0
    clock = 0.0
    out = {}
    for beat, weight in zip(present, weights):
        span = float(duration) * weight / total
        out[beat] = (clock, clock + span)
        clock += span
    return out


def words_from_boundaries(beats, beat_times):
    """Even words inside each beat. Used when whisper has no timestamps."""
    words = []
    for beat in ORDER:
        if beat not in beat_times:
            continue
        tokens = str((beats or {}).get(beat) or "").split()
        if not tokens:
            continue
        t0, t1 = beat_times[beat]
        step = max(0.01, (float(t1) - float(t0)) / len(tokens))
        for index, token in enumerate(tokens):
            words.append({
                "word": token,
                "start": float(t0) + index * step,
                "end": float(t0) + (index + 1) * step,
            })
    return words


def load_stored_words(run_dir):
    for name in ("words.json", "audio_words.json"):
        path = Path(run_dir) / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("words") or []
        if data:
            return list(data)
    return None


def align_words(run_dir, voice, beats, beat_times):
    stored = load_stored_words(run_dir)
    if stored:
        print(f"[align] stored word timestamps ({len(stored)})")
        return stored
    try:
        import sync_gate
        words = sync_gate.transcribe_file(voice)
        if words:
            print(f"[align] faster-whisper {len(words)} words")
            return words
    except Exception as exc:  # noqa: BLE001 — a box without the model still renders
        print(f"[align] whisper unavailable ({exc}); using beat boundaries")
    print("[align] beat boundaries")
    return words_from_boundaries(beats, beat_times)


def topic_for(run_dir, narration):
    bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    key = ""
    meta_path = Path(run_dir) / "verify_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
        key = str(meta.get("topic_key") or meta.get("key") or "")
        nested = meta.get("topic")
        if not key and isinstance(nested, dict):
            key = str(nested.get("key") or "")
    if key:
        found = next((item for item in bank if item.get("key") == key), None)
        if found:
            return json.loads(json.dumps(found))
    title = (narration.get("title") or "").strip()
    for item in bank:
        topic = str(item.get("topic") or "")
        if topic and (topic == title or title.startswith(topic) or topic.startswith(title)):
            return json.loads(json.dumps(item))
    if "Piercing" in title or "पियर्सिंग" in title:
        found = next((item for item in bank if item.get("key") == "piercing"), None)
        if found:
            return json.loads(json.dumps(found))
    raise SystemExit(f"no topic matches {title!r}")


def _ffmpeg(args, cwd=None):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], cwd=cwd, check=True)


def hook_motion_times(hook_line):
    import hook_motion
    return hook_motion.click_times(hook_line)


def hook_sfx_mix(mixed, hook_line):
    import hook_sfx
    hook_sfx.mix_file(mixed, hook_motion_times(hook_line))


def _mux(work, silent, audio, out):
    """Mux from the work dir so libass finds subs.ass beside the process."""
    _ffmpeg(
        ["-i", silent.name, "-i", audio.name, "-vf", "fps=30,ass=subs.ass",
         *board_short.X264, "-c:a", "copy", "-shortest", str(out.resolve())],
        cwd=work,
    )


def render(run_dir, out):
    """Paint a new mp4 and thumbnail.jpg. voice.wav is not regenerated."""
    import shutil

    import bed_audio

    run_dir = Path(run_dir)
    out = Path(out)
    voice = run_dir / "voice.wav"
    script_path = run_dir / "script.txt"
    if not voice.is_file():
        raise SystemExit(f"missing {voice}")
    if not script_path.is_file():
        raise SystemExit(f"missing {script_path}")
    voice_bytes = voice.read_bytes()
    narration = parse_script(script_path.read_text(encoding="utf-8"))
    spoken = {beat: narration["beats"].get(beat) or "" for beat in ORDER}
    if not spoken["hook"]:
        raise SystemExit("script.txt has no HOOK")
    duration = wav_duration(voice)
    review = {}
    review_path = run_dir / "self_review.json"
    if review_path.is_file():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except ValueError:
            review = {}
    beat_times = beat_times_from_review(review, duration, spoken) or beat_times_from_words(spoken, duration)
    if not beat_times:
        raise SystemExit("could not time the beats from self_review.json or the script")
    words = align_words(run_dir, voice, spoken, beat_times)
    topic = topic_for(run_dir, narration)
    topic["_live_short"] = True
    topic["_spoken_beats"] = dict(spoken)
    order = tuple(beat for beat in ORDER if beat in beat_times)
    scenes = scene_pace.plan_scenes(beat_times, beats=order)
    board_short._install_scene_cards(topic, spoken, beat_times, scenes, words)
    spec = {"mode": "schematic"}
    groups = {beat: [] for beat in order}
    for scene in scenes:
        groups[scene["beat"]].append(scene)
    frames = []
    durations = []
    for beat in order:
        group = groups.get(beat) or []
        if not group:
            continue
        made, _flags = scene_frames.frames_for(topic, beat, len(group), spec)
        for scene, frame in zip(group, made):
            scene["frame"] = frame
            if getattr(frame, "text_layers", None) is not None and getattr(frame, "plate", None) is not None:
                scene_frames.set_progress(frame, (len(frames) + 1) / max(1, len(scenes)))
            frames.append(frame)
            durations.append(max(0.05, float(scene["end"]) - float(scene["start"])))
    if not frames:
        raise SystemExit("no scenes to paint")
    faults = scene_transition.transition_faults(frames, board_short.SCENE_TRANSITIONS)
    if faults:
        raise SystemExit("shaped text was sliced at a transition: " + "; ".join(faults[:4]))
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(out.parent) / (out.stem + "_rerender_work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    silent = work / "silent.mp4"
    scene_transition.write_timeline(
        frames, durations, silent, fps=30,
        transitions=board_short.SCENE_TRANSITIONS, fade_s=board_short.SCENE_FADE_S,
        overlay=scene_frames.paint_progress,
    )
    # Resample a copy. The artifact voice.wav stays the original file.
    clean = work / "voice_44k.wav"
    _ffmpeg(["-i", str(voice), "-ar", "44100", "-ac", "2", str(clean)])
    mixed = work / "mixed.wav"
    bed_audio.ensure_assets()
    bed_audio.mix_into(clean, scenes, mixed)
    hook_line = narration.get("hook_line") or ""
    if hook_line.strip():
        hook_sfx_mix(mixed, hook_line)
    audio = work / "audio.m4a"
    _ffmpeg([
        "-i", str(mixed),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k", str(audio),
    ])
    (work / "subs.ass").write_text(
        board_short.build_ass(topic, beat_times, spoken, hook_line, ""),
        encoding="utf-8",
    )
    _mux(work, silent, audio, out)
    if voice.read_bytes() != voice_bytes:
        raise SystemExit("voice.wav was modified")
    thumb = out.with_name("thumbnail.jpg")
    board_short.thumbnail(topic, thumb)
    shutil.rmtree(work, ignore_errors=True)
    print(f"[rerender] voice {duration:.2f}s, {len(frames)} scenes, no TTS")
    print(f"[rerender] wrote {out}")
    print(f"[rerender] wrote {thumb}")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild final.mp4 from voice.wav. No TTS.")
    parser.add_argument("--run-dir", required=True, help="Artifact folder with voice.wav and script.txt")
    parser.add_argument("--out", required=True, help="Output mp4 path. thumbnail.jpg is written beside it.")
    args = parser.parse_args(argv)
    try:
        render(args.run_dir, args.out)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — the CLI should say why it stopped
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
