"""Hard gates. A miss is a HOLD, never an Edge voice."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def probe(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {}
    data = json.loads(proc.stdout or "{}")
    video = next((row for row in data.get("streams") or [] if row.get("codec_type") == "video"), {})
    duration = (data.get("format") or {}).get("duration") or video.get("duration") or 0
    return {
        "duration": float(duration or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec": str(video.get("codec_name") or ""),
    }


def check(out, rows, levels, voice_engine, dry_run=False, capture_dates=None, expected_date=None):
    faults = []
    out = Path(out)
    # The 40–50s gate is the main Reel only. final_yt.mp4 and final_ig.mp4 are concatenations.
    video = out / "final.mp4"
    if not video.is_file():
        faults.append("final.mp4 missing")
        info = {}
    else:
        info = probe(video)
        if info.get("width") != 1080 or info.get("height") != 1920:
            faults.append(f"frame is {info.get('width')}x{info.get('height')}")
        duration = float(info.get("duration") or 0)
        if not 40.0 <= duration <= 50.0:
            faults.append(f"duration {duration:.2f}s outside 40-50s")
        if info.get("codec") not in ("h264", "avc1"):
            faults.append(f"codec {info.get('codec') or 'missing'}")
    if voice_engine == "edge" or voice_engine == "elevenlabs":
        faults.append(f"voice_engine={voice_engine}")
    if not dry_run and voice_engine != "gemini":
        faults.append(f"voice_engine={voice_engine}")
    from realchart.script import lint, number_faults
    faults.extend(lint(rows))
    faults.extend(number_faults(rows, levels))
    if not (out / "source.md").is_file():
        faults.append("source.md missing")
    if not (out / "script.txt").is_file():
        faults.append("script.txt missing")
    if capture_dates is not None:
        faults.extend(video_faults(out, rows, capture_dates, expected_date))
    return faults


def video_faults(out, rows, capture_dates, expected_date):
    """The pre-publish video gate: black/empty frames, tofu, capture date, raw floats, IG ends."""
    from realchart import ends, video_qa
    captions = []
    listing = Path(out) / "cards" / "overlay_text.json"
    if listing.is_file():
        captions.extend(json.loads(listing.read_text(encoding="utf-8")))
    else:
        return ["overlay_text.json missing, captions not checked"]
    for row in rows:
        captions.extend([str(row.get("d") or ""), str(row.get("t") or "")])
    captions.extend([ends.FOLLOW_RTT, ends.FOLLOW_PKP])
    return video_qa.run_all(
        out, captions, capture_dates, expected_date, ends.INTRO_RTT, ends.OUTRO_RTT, ends.FOLLOW_RTT,
    )
