"""One Charon request for the Reel. A dry run does not call Gemini."""
from __future__ import annotations

import os
import wave
from pathlib import Path


def speak(text, dest, kind, dry_run=False, cache_key=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        _silence(dest, 45.0)
        return {"engine": "dry", "requests": 0}
    os.environ["REALCHART_REEL"] = "1"
    os.environ["REALCHART_KIND"] = str(kind)
    os.environ["GEMINI_BUDGET_CALLER"] = str(kind)
    import gemini_voice
    gemini_voice.write_speech(text, dest, cache_key=cache_key or f"realchart-{kind}")
    return {"engine": "gemini", "requests": 1}


def _silence(dest, seconds, rate=24000):
    frames = int(seconds * rate)
    with wave.open(str(dest), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


SEGMENT_GAP_SEC = 0.25


def wav_seconds(path):
    with wave.open(str(path)) as handle:
        return handle.getnframes() / float(handle.getframerate())


def speak_segments(texts, dest, kind, dry_run=False, key_prefix="realchart"):
    """One TTS clip per card, joined with a short pause. Returns (info, seconds per card).

    Card timing comes from each clip's real length, so a card changes exactly
    when its line starts. Each clip is cached by its own text.
    """
    import hashlib
    import subprocess
    dest = Path(dest)
    parts_dir = dest.parent / "voice_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts, seconds, requests = [], [], 0
    engine = "dry" if dry_run else "gemini"
    for index, text in enumerate(texts):
        part = parts_dir / f"{index:02d}.wav"
        digest = hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]
        if not str(text).strip():
            _silence(part, 0.8)
        elif dry_run:
            from realchart.script import estimate_seconds
            _silence(part, max(0.8, estimate_seconds(str(text))))
        else:
            info = speak(str(text), part, kind, cache_key=f"{key_prefix}-seg{index}-{digest}")
            requests += info.get("requests", 0)
        parts.append(part)
        seconds.append(_clip_seconds(part) + SEGMENT_GAP_SEC)
    listing = parts_dir / "list.txt"
    gap = parts_dir / "gap.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(parts[0]), "-af", f"atrim=0:0,apad=whole_dur={SEGMENT_GAP_SEC}",
                    str(gap)], check=True)
    lines = []
    for part in parts:
        lines += [f"file '{part.resolve().as_posix()}'", f"file '{gap.resolve().as_posix()}'"]
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-ar", "24000", "-ac", "1", str(dest)], check=True)
    return {"engine": engine, "requests": requests, "segments": [round(s, 3) for s in seconds]}, seconds


def _clip_seconds(path):
    import json
    import subprocess
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True)
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (ValueError, KeyError):
        return wav_seconds(path)
