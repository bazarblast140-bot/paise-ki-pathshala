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


def check(out, rows, levels, voice_engine, dry_run=False, capture_dates=None, expected_date=None, article=None):
    faults = []
    from realchart.video_qa import sentence_faults
    faults.extend(sentence_faults(article))
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
        hi = 55.0 if (levels or {}).get("format") == "story" else 50.0
        if not 40.0 <= duration <= hi:
            faults.append(f"duration {duration:.2f}s outside 40-{hi:.0f}s")
        if info.get("codec") not in ("h264", "avc1"):
            faults.append(f"codec {info.get('codec') or 'missing'}")
    if voice_engine == "elevenlabs":
        faults.append(f"voice_engine={voice_engine}")
    if not dry_run and voice_engine not in ("gemini", "grok", "edge"):
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
    if not dry_run:
        faults.extend(audio_faults(out))
    return faults


MAX_GAP_SEC = 0.8
MIN_END_HUSH_SEC = 0.15


def silence_gaps(path, floor_db=-40, min_sec=MAX_GAP_SEC):
    """[(start, seconds)] of every silence longer than min_sec in a media file."""
    import re
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", f"silencedetect=n={floor_db}dB:d={min_sec}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    lengths = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", log)]
    if len(starts) > len(lengths):
        total = probe(path).get("duration") or 0.0
        lengths.append(max(0.0, total - starts[-1]))
    return [(start, length) for start, length in zip(starts, lengths) if length > min_sec]


def ends_in_hush(wav, hush=MIN_END_HUSH_SEC, floor_db=-40.0):
    """True when the clip's last `hush` seconds are silent: the last word was not cut."""
    from realchart.voice import _frames_db
    frames = _frames_db(wav)
    need = max(1, int(round(hush / 0.02)))
    return len(frames) >= need and all(db <= floor_db for db in frames[-need:])


def audio_faults(out):
    """16-Oct review: a 1.5 s hole where Edge dropped '83', and 'advice na-' cut at the end."""
    out = Path(out)
    faults = []
    main = out / "final.mp4"
    if main.is_file():
        for start, length in silence_gaps(main):
            faults.append(f"audio: {length:.1f}s silence at {start:.1f}s in final.mp4 (max {MAX_GAP_SEC}s)")
    parts = sorted((out / "voice_parts").glob("[0-9][0-9].wav"))
    for part in parts:
        if not ends_in_hush(part):
            faults.append(f"audio: voice clip {part.name} ends mid-word (truncated)")
    voice = out / "voice.wav"
    if voice.is_file() and not ends_in_hush(voice):
        faults.append("audio: voice.wav ends mid-word (last clip truncated)")
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
    outro = Path(out) / "ends" / "outro_rtt.mp4"
    used = json.loads((Path(out) / "charts_used.json").read_text(encoding="utf-8")) if (Path(out) / "charts_used.json").is_file() else {}
    # Only the TradingView shots that are on screen; our yfinance plot has no crosshair.
    charts = [Path(p) for p in used.get("tradingview", []) if Path(p).is_file()]
    return video_qa.run_all(
        out, captions, capture_dates, expected_date, ends.INTRO_RTT, outro if outro.is_file() else ends.OUTRO_RTT,
        ends.FOLLOW_RTT, charts=charts,
    )
