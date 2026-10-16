"""One Charon request for the Reel. A dry run does not call Gemini."""
from __future__ import annotations

import os
import wave
from pathlib import Path


def speak(text, dest, kind, dry_run=False, cache_key=None, engine="gemini"):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        _silence(dest, 45.0)
        return {"engine": "dry", "requests": 0}
    if engine == "edge":
        edge_speech(text, dest, cache_key=cache_key)
        return {"engine": "edge", "requests": 0}
    if engine == "grok":
        grok_speech(text, dest, cache_key=cache_key)
        return {"engine": "grok", "requests": 1}
    os.environ["REALCHART_REEL"] = "1"
    os.environ["REALCHART_KIND"] = str(kind)
    os.environ["GEMINI_BUDGET_CALLER"] = str(kind)
    import gemini_voice
    gemini_voice.write_speech(text, dest, cache_key=cache_key or f"realchart-{kind}")
    return {"engine": "gemini", "requests": 1}


GROK_VOICE = "naksh"
GROK_URL = "https://api.x.ai/v1/tts"


class GrokUnavailable(Exception):
    pass


EDGE_VOICE = "hi-IN-MadhurNeural"


class EdgeUnavailable(Exception):
    pass


def edge_speech(text, dest, cache_key=None):
    """Microsoft Edge TTS (hi-IN-MadhurNeural, no key) to a 24 kHz mono wav. Raises EdgeUnavailable."""
    import hashlib
    import shutil
    import subprocess
    import sys
    dest = Path(dest)
    cache_dir = Path(__file__).resolve().parents[1] / "data" / "gemini_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{EDGE_VOICE}|+20%|{text}".encode("utf-8")).hexdigest()[:12]
    cached = cache_dir / f"edge-{digest}.wav"
    if not cached.is_file():
        mp3 = cached.with_suffix(".mp3")
        try:
            # Edge Madhur is slower than Gemini Charon; +20% keeps the same script under the 50s QA gate.
            subprocess.run([sys.executable, "-m", "edge_tts", "--voice", EDGE_VOICE, "--rate=+20%", "--text", str(text), "--write-media", str(mp3)],
                           check=True, capture_output=True, timeout=90)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(cached)], check=True, timeout=60)
        except (subprocess.SubprocessError, OSError) as err:
            raise EdgeUnavailable(str(err)[:200]) from err
        finally:
            if mp3.is_file():
                mp3.unlink()
    shutil.copyfile(cached, dest)


def xai_key():
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key
    secrets = os.environ.get("GEMINI_SECRETS_DIR", "")
    if secrets:
        try:
            return (Path(secrets) / "xai_api_key").read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def grok_speech(text, dest, cache_key=None):
    """xAI Grok TTS (voice naksh, Hindi) to a 24 kHz mono wav. Raises GrokUnavailable on any failure."""
    import json
    import subprocess
    import urllib.error
    import urllib.request
    cache = Path(__file__).resolve().parent.parent / "data" / "gemini_cache" / f"grok-{cache_key}.wav" if cache_key else None
    if cache and cache.is_file():
        dest.write_bytes(cache.read_bytes())
        return dest
    key = xai_key()
    if not key:
        raise GrokUnavailable("no XAI_API_KEY")
    body = json.dumps({"text": str(text), "language": "hi", "voice_id": GROK_VOICE}).encode("utf-8")
    req = urllib.request.Request(GROK_URL, data=body, headers={"Content-Type": "application/json",
                                                               "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        raise GrokUnavailable(f"xAI TTS HTTP {exc.code}") from exc
    except OSError as exc:
        raise GrokUnavailable(f"xAI TTS {type(exc).__name__}") from exc
    if len(audio) < 1000:
        raise GrokUnavailable("xAI TTS returned no audio")
    mp3 = Path(str(dest) + ".mp3")
    mp3.write_bytes(audio)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(dest)], check=True)
    mp3.unlink()
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(Path(dest).read_bytes())
    return dest


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
    try:
        return _speak_all(texts, dest, kind, dry_run, key_prefix, parts_dir, "dry" if dry_run else "gemini")
    except Exception as exc:  # noqa: BLE001
        import gemini_voice
        if dry_run or not isinstance(exc, gemini_voice.GeminiFallback):
            raise
        # Gemini out (quota/outage): the WHOLE Reel in one fallback voice, never mixed.
        print(f"[voice] Gemini unavailable ({str(exc)[:80]}); trying Grok TTS ({GROK_VOICE})", flush=True)
        try:
            return _speak_all(texts, dest, kind, dry_run, key_prefix, parts_dir, "grok")
        except GrokUnavailable as grok:
            # Last fallback: Edge TTS, again the whole Reel in one voice.
            print(f"[voice] Grok unavailable ({str(grok)[:80]}); using Edge TTS ({EDGE_VOICE})", flush=True)
            try:
                return _speak_all(texts, dest, kind, dry_run, key_prefix, parts_dir, "edge")
            except EdgeUnavailable as edge:
                raise gemini_voice.GeminiFallback(f"{exc}; grok: {grok}; edge: {edge}") from edge


def _speak_all(texts, dest, kind, dry_run, key_prefix, parts_dir, engine):
    dest = Path(dest)
    import hashlib
    import subprocess
    parts, seconds, requests = [], [], 0
    for index, text in enumerate(texts):
        part = parts_dir / f"{index:02d}.wav"
        digest = hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]
        if not str(text).strip():
            _silence(part, 0.8)
        elif dry_run:
            from realchart.script import estimate_seconds
            _silence(part, max(0.8, estimate_seconds(str(text))))
        else:
            info = speak(str(text), part, kind, cache_key=f"{key_prefix}-seg{index}-{digest}", engine=engine)
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
    # Edge/Gemini often leave ~1s of hush at the end; that plus the silent IG
    # outro trips the dead-air QA. Trim only the trailing hush.
    before = _clip_seconds(dest)
    trimmed = dest.with_suffix(".trim.wav")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(dest),
        "-af", "areverse,silenceremove=start_periods=1:start_duration=0.2:start_threshold=-40dB,areverse",
        "-ar", "24000", "-ac", "1", str(trimmed),
    ], check=True)
    after = _clip_seconds(trimmed)
    if 0.15 < before - after < 3.0 and after > 5.0:
        trimmed.replace(dest)
        seconds[-1] = max(0.6, seconds[-1] - (before - after))
    elif trimmed.is_file():
        trimmed.unlink()
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
