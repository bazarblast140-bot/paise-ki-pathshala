"""One Charon request for the Reel. A dry run does not call Gemini."""
from __future__ import annotations

import os
import wave


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
