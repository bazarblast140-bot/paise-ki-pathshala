"""One Gemini TTS request for a whole Short.

The free daily quota is unknown, so a Short is one POST, or two when the
first take is under 45 seconds. The reply audio is
raw s16le at 24 kHz mono, unless the bytes start with RIFF. Callers split
that file into beats. This module never prints the API key.
"""
from __future__ import annotations

import base64
import io
import os
import wave

MODEL = "gemini-3.8-flash-tts"
VOICE = "Charon"
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
SAMPLE_RATE = 24000
TIMEOUT = 180
STYLE = (
    "confident deep male narrator, natural Hinglish with English words pronounced the Indian way, "
    "calm, clear teacher pace with short natural pauses between sentences"
)
SLOW_STYLE = (
    "confident deep male narrator, natural Hinglish with English words pronounced the Indian way, "
    "slow, deliberate pace, pause after every sentence"
)
# A four-beat Gemini take may be shorter than Rudra. Under 45s, one slower retry.
GEMINI_MIN_SECONDS = 34.0
GEMINI_RETRY_UNDER = 45.0
GEMINI_MAX_SECONDS = 65.0
# A reply this far from words / pace is not the Short. The caller falls back.
FAR_LO = 0.60
FAR_HI = 1.50


class GeminiFallback(Exception):
    """Gemini did not return usable audio. The slot speaks the next engine."""

    def __init__(self, reason):
        self.reason = redact(reason)
        super().__init__(self.reason)


def redact(text):
    """Drop the key if a library exception includes it."""
    raw = " ".join(str(text or "").split())
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key and key in raw:
        raw = raw.replace(key, "***")
    return raw


def request_body(text, style=None):
    return {
        "model": MODEL,
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{
                    "type": "speech_metadata",
                    "style": style or STYLE,
                }],
            }],
        }],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/l16",
            "sample_rate": SAMPLE_RATE,
        },
        "generation_config": {
            "speech_config": [{"voice": VOICE}],
        },
    }


def _walk_audio(node, audio, inline):
    if isinstance(node, dict):
        if str(node.get("type") or "") == "audio" and node.get("data"):
            audio.append(node["data"])
        box = node.get("inlineData")
        if isinstance(box, dict) and box.get("data"):
            inline.append(box["data"])
        for value in node.values():
            _walk_audio(value, audio, inline)
    elif isinstance(node, list):
        for item in node:
            _walk_audio(item, audio, inline)


def find_audio(node):
    """Base64 audio, searched recursively. type=audio wins over inlineData."""
    audio, inline = [], []
    _walk_audio(node, audio, inline)
    return audio or inline


def _pcm_wav(pcm, rate=SAMPLE_RATE):
    if len(pcm) % 2:
        pcm = pcm[:-1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


def audio_bytes_to_wav(raw):
    """RIFF is stored as-is. Anything else is s16le 24 kHz mono."""
    if not raw:
        raise GeminiFallback("Gemini TTS audio was empty")
    if raw.startswith(b"RIFF"):
        return raw
    if len(raw) < 2:
        raise GeminiFallback("Gemini TTS audio was empty")
    return _pcm_wav(raw)


def decode_audio(pieces):
    """Decode every base64 hit. Raw pieces are concatenated. A RIFF blob is used whole."""
    if not pieces:
        raise GeminiFallback("Gemini TTS response had no audio")
    raws = []
    for piece in pieces:
        try:
            raws.append(base64.b64decode(piece))
        except Exception as exc:  # noqa: BLE001 — the body is not logged
            raise GeminiFallback(f"Gemini TTS audio was not valid base64 ({type(exc).__name__})")
    if any(raw.startswith(b"RIFF") for raw in raws):
        return audio_bytes_to_wav(next(raw for raw in raws if raw.startswith(b"RIFF")))
    return audio_bytes_to_wav(b"".join(raws))


def wav_seconds(path):
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate() or 1
        return handle.getnframes() / float(rate)


def duration_far(actual, expected):
    """True when the file is not the length this script should speak."""
    try:
        actual = float(actual)
        expected = float(expected)
    except (TypeError, ValueError):
        return True
    if actual <= 0:
        return True
    if expected <= 0:
        return actual < 8.0 or actual > 120.0
    ratio = actual / expected
    return ratio < FAR_LO or ratio > FAR_HI


def write_speech(text, dest, style=None):
    """POST the whole script once. Write a wav. Never log the key or the body."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiFallback("GEMINI_API_KEY is not set")
    import requests
    try:
        response = requests.post(
            URL,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=request_body(text, style=style),
            timeout=TIMEOUT,
        )
    except requests.Timeout:
        raise GeminiFallback("Gemini TTS timed out after 180s")
    except requests.RequestException as exc:
        raise GeminiFallback(f"Gemini TTS request failed ({type(exc).__name__})")
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise GeminiFallback(f"Gemini TTS HTTP {status}")
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 — a non-JSON body has no audio
        raise GeminiFallback("Gemini TTS response had no audio")
    wav = decode_audio(find_audio(payload))
    dest = dest if hasattr(dest, "write_bytes") else None
    if dest is None:
        raise GeminiFallback("Gemini TTS audio was empty")
    dest.write_bytes(wav)
    if wav_seconds(dest) <= 0:
        raise GeminiFallback("Gemini TTS audio was empty")
    return dest


def _weights(spoken, order):
    present = [beat for beat in order if str((spoken or {}).get(beat) or "").strip()]
    weights = [max(1, len(str(spoken[beat]).split())) for beat in present]
    return present, weights


def _proportional(order, weights, duration):
    total = float(sum(weights)) or 1.0
    clock = 0.0
    bounds = {}
    for beat, weight in zip(order, weights):
        span = float(duration) * weight / total
        bounds[beat] = (clock, clock + span)
        clock += span
    if bounds:
        last = order[-1]
        start, _end = bounds[last]
        bounds[last] = (start, float(duration))
    return bounds


def _from_words(order, weights, words, duration):
    total = float(sum(weights)) or 1.0
    n = len(words)
    cursor = 0
    consumed = 0
    bounds = {}
    for index, (beat, weight) in enumerate(zip(order, weights)):
        if index == len(order) - 1:
            end_index = n
        else:
            consumed += weight
            end_index = int(round(consumed / total * n))
            end_index = min(n, max(end_index, cursor + 1 if cursor < n else cursor))
        chunk = words[cursor:end_index]
        if chunk:
            start = float(chunk[0].get("start") or 0)
            end = float(chunk[-1].get("end") or start)
        else:
            start = duration * max(0, consumed - weight) / total
            end = duration * consumed / total
        bounds[beat] = (max(0.0, start), max(0.0, end))
        cursor = end_index
    items = list(bounds.items())
    fixed = {}
    prev = 0.0
    for index, (beat, (_start, end)) in enumerate(items):
        start = prev
        if index == len(items) - 1:
            end = float(duration)
        else:
            end = min(float(duration), max(start, float(end)))
        fixed[beat] = (start, end)
        prev = end
    return fixed


def _content_bounds(present, spoken, words, duration):
    """Cut on the whisper tokens that match each beat, skipping early garbage.

    Word-count proportion treats hallucinated tokens as the hook and hands the
    next beat the tail of the previous sentence. A weak match returns None so
    the caller keeps the proportional cut.
    """
    import sync_gate
    flat = []
    for index, word in enumerate(words):
        for tok in sync_gate.tokens(str(word.get("word") or "")):
            if tok in sync_gate._STOP:
                continue
            flat.append((index, tok))
    if len(flat) < 2:
        return None
    cursor = 0
    spans = []
    matched = 0
    wanted = 0
    for beat in present:
        want = [
            tok for tok in sync_gate.tokens(str((spoken or {}).get(beat) or ""))
            if tok not in sync_gate._STOP
        ]
        wanted += len(want)
        first = last = None
        scan = cursor
        hit = 0
        window = max(8, len(want) * 4)
        for tok in want:
            found = None
            for j in range(scan, min(len(flat), scan + window)):
                if sync_gate._near_token(tok, flat[j][1]):
                    found = j
                    break
            if found is None:
                continue
            hit += 1
            word_index = flat[found][0]
            if first is None:
                first = word_index
            last = word_index
            scan = found + 1
        matched += hit
        if not want or first is None or hit < max(1, int(round(len(want) * 0.34))):
            return None
        spans.append((first, last))
        cursor = scan
    if wanted == 0 or matched / float(wanted) < 0.45:
        return None
    bounds = {}
    prev = 0.0
    for index, beat in enumerate(present):
        if index == len(present) - 1:
            end = float(duration)
        else:
            next_first = spans[index + 1][0]
            end = float(words[next_first].get("start") or prev)
            end = min(float(duration), max(prev, end))
        if end - prev < 1.05:
            return None
        bounds[beat] = (prev, end)
        prev = end
    return bounds


def beat_bounds(spoken, order, words, duration):
    """Beat spans on the whole-file clock. Whisper ends the cuts; word count is the fallback."""
    present, weights = _weights(spoken, order)
    duration = max(float(duration or 0), 0.01)
    if not present:
        return {}
    usable = []
    for word in words or []:
        try:
            start = float(word.get("start"))
            end = float(word.get("end"))
        except (TypeError, ValueError, AttributeError):
            continue
        text = str(word.get("word") or "").strip()
        if text and end > start:
            usable.append({"word": text, "start": start, "end": end})
    if usable:
        last = max(word["end"] for word in usable)
        if last >= 0.5 * duration:
            content = _content_bounds(present, spoken, usable, duration)
            if content:
                return content
            bounds = _from_words(present, weights, usable, duration)
            if bounds and all(end - start >= 1.05 for start, end in bounds.values()):
                return bounds
    return _proportional(present, weights, duration)


def _slice_wav(src, dest, start, end):
    with wave.open(str(src), "rb") as handle:
        rate = handle.getframerate() or 1
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        count = handle.getnframes()
        a = max(0, min(count, int(float(start) * rate)))
        b = max(a, min(count, int(float(end) * rate)))
        handle.setpos(a)
        frames = handle.readframes(b - a)
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames)


def _words_in(words, start, end):
    rows = []
    for word in words or []:
        try:
            w0 = float(word.get("start"))
            w1 = float(word.get("end"))
        except (TypeError, ValueError, AttributeError):
            continue
        text = str(word.get("word") or "").strip()
        if not text or w1 <= start + 0.02 or w0 >= end - 0.02:
            continue
        rows.append({
            "word": text,
            "start": round(max(0.0, w0 - start), 3),
            "end": round(max(0.0, w1 - start), 3),
        })
    return rows


def split_into_beats(wav_path, spoken, order, dest_dir, transcribe=None):
    """Cut one wav into tts_{beat}.wav and remember the whisper words that timed the cuts."""
    import json
    from pathlib import Path

    dest_dir = Path(dest_dir)
    duration = wav_seconds(wav_path)
    words = []
    if transcribe is None:
        def transcribe(path):
            import sync_gate
            return sync_gate.transcribe_file(path)
    try:
        words = list(transcribe(wav_path) or [])
    except Exception as exc:  # noqa: BLE001 — a missing model still leaves even beat cuts
        print(f"[align] whisper unavailable ({type(exc).__name__}); splitting by script length")
        words = []
    bounds = beat_bounds(spoken, order, words, duration)
    per_beat = {}
    for beat, (start, end) in bounds.items():
        _slice_wav(wav_path, dest_dir / f"tts_{beat}.wav", start, end)
        per_beat[beat] = _words_in(words, start, end)
    payload = {
        "bounds": {beat: [round(start, 3), round(end, 3)] for beat, (start, end) in bounds.items()},
        "beats": per_beat,
        "words": [
            {"word": str(word.get("word") or ""), "start": float(word.get("start") or 0), "end": float(word.get("end") or 0)}
            for word in words
            if str(word.get("word") or "").strip()
        ],
    }
    (dest_dir / "gemini_words.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (dest_dir / "words.json").write_text(
        json.dumps(payload["words"], ensure_ascii=False) + "\n", encoding="utf-8")
    return bounds
