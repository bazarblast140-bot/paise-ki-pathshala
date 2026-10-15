"""One Gemini TTS request for a whole Short.

The free tier is 10 requests a day on each key, so a Short is one POST
per key it has to try. A 429 is not retried on that key. A successful
Charon wav is cached by topic and script hash and reused with no second
request. The reply audio is raw s16le at 24 kHz
mono, unless the bytes start with RIFF. Callers split that file into
beats on silence or on the whisper clock. This module never prints the key.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
import wave
from pathlib import Path

MODEL = "gemini-3.8-flash-tts"
VOICE = "Charon"
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
SAMPLE_RATE = 24000
TIMEOUT = 180
# One retry for a server error, on the same key. A 429 is that key's daily
# cap (10/day) and is not retried on it: the body says to wait about 20
# hours. The next configured key is tried instead.
RETRY_WAIT_S = 20.0
RETRY_STATUSES = (500, 502, 503, 504)
CACHE_DIR = Path(__file__).resolve().parent / "data" / "gemini_cache"
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

    def __init__(self, reason, status=None):
        self.status = int(status) if status else None
        self.reason = redact(reason)
        super().__init__(self.reason)


def api_keys():
    """(slot, secret) in try order. The secret is never logged.

    Slot ``1`` is ``GEMINI_API_KEY``. Slots ``2`` through ``4`` are
    ``GEMINI_API_KEY_2`` through ``GEMINI_API_KEY_4``, and only when that
    env var is set. A later key is used after the earlier one answers
    HTTP 429 or its own daily counter is full. The same secret is not
    tried twice.
    """
    import gemini_budget
    seen = set()
    pairs = []
    secrets_dir = os.environ.get("GEMINI_SECRETS_DIR", "")  # set by scripts/box_close_reel.sh
    for slot, name in gemini_budget.KEY_ENV:
        secret = os.environ.get(name, "").strip()
        if not secret and secrets_dir:
            # Box runs: the key may live in a file (e.g. gemini_api_key_2) instead of the env.
            path = os.path.join(secrets_dir, name.lower())
            try:
                with open(path, encoding="utf-8") as handle:
                    secret = handle.read().strip()
            except OSError:
                secret = ""
        if not secret or secret in seen:
            continue
        seen.add(secret)
        pairs.append((slot, secret))
    return pairs


def configured():
    """True when at least one Charon key is in the environment."""
    return bool(api_keys())


def redact(text):
    """Drop every configured key if a library exception includes one."""
    raw = " ".join(str(text or "").split())
    keys = [secret for _slot, secret in api_keys() if secret]
    for key in sorted(set(keys), key=len, reverse=True):
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


def _response_snippet(response):
    try:
        raw = getattr(response, "text", "") or ""
    except Exception:  # noqa: BLE001 — the status is still the error
        raw = ""
    return redact(" ".join(str(raw).split()))[:180]


def _post_speech(text, dest, style=None, api_key=None):
    """One POST. The error text is the HTTP status plus a short redacted body."""
    key = (api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")).strip()
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
        detail = redact(str(exc))[:180]
        raise GeminiFallback(f"Gemini TTS request failed ({type(exc).__name__}: {detail})")
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        snippet = _response_snippet(response)
        detail = f"Gemini TTS HTTP {status}"
        if snippet:
            detail = f"{detail}: {snippet}"
        if status == 429:
            detail = f"gemini_quota: {detail}"
        raise GeminiFallback(detail, status=status)
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


def cache_id(topic_key, script):
    """Stable id for one topic and the exact text sent to Charon."""
    digest = hashlib.sha256(str(script or "").encode("utf-8")).hexdigest()[:20]
    safe = re.sub(r"[^A-Za-z0-9_-]+", "", str(topic_key or "script"))[:48] or "script"
    return f"{safe}-{digest}"


def _cache_path(cache_key):
    root = Path(os.environ.get("GEMINI_CACHE_DIR") or CACHE_DIR)
    return root / f"{cache_key}.wav"


def _cache_enabled():
    return os.environ.get("GEMINI_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def load_cached_speech(cache_key, dest):
    """Copy a stored Charon wav. True means the caller must not POST."""
    if not cache_key or not _cache_enabled():
        return False
    path = _cache_path(cache_key)
    try:
        if not path.exists() or path.stat().st_size < 1000:
            return False
        data = path.read_bytes()
    except OSError:
        return False
    if hasattr(dest, "write_bytes"):
        dest.write_bytes(data)
    else:
        Path(dest).write_bytes(data)
    print(f"[gemini] cache hit {cache_key}; no request", flush=True)
    return True


def store_cached_speech(cache_key, dest):
    """Keep a successful take so the same script never calls Gemini again."""
    if not cache_key or not _cache_enabled():
        return
    try:
        data = dest.read_bytes() if hasattr(dest, "read_bytes") else Path(dest).read_bytes()
    except OSError:
        return
    if len(data) < 1000:
        return
    path = _cache_path(cache_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        return


def write_speech(text, dest, style=None, cache_key=None):
    """POST the whole script once per key. A 429 tries the next key.

    ``GEMINI_API_KEY`` goes first, then ``_2``, ``_3``, and ``_4`` when
    those env vars are set. A later key is used only when the earlier one
    returns HTTP 429 or its own daily counter is full. A 5xx is retried
    once on the same key. The logged error is the HTTP status and a short
    redacted body. No key is printed. A cached wav for this script is
    returned with no POST. When every configured key is exhausted, the
    error is gemini_quota.
    """
    if load_cached_speech(cache_key, dest):
        return dest
    pairs = api_keys()
    if not pairs:
        raise GeminiFallback("GEMINI_API_KEY is not set")
    import gemini_budget
    caller = gemini_budget.caller_from_env()
    last = None
    for key_id, secret in pairs:
        if not gemini_budget.allow(caller, key_id):
            continue
        for attempt in (0, 1):
            if not gemini_budget.allow(caller, key_id):
                break
            try:
                # Count the POST even when the server answers 429. A cache
                # hit returned above and is not counted. A 5xx retry is a
                # second POST on this same key.
                gemini_budget.note(caller, key_id)
                saved = _post_speech(text, dest, style, api_key=secret)
                store_cached_speech(cache_key, dest)
                return saved
            except GeminiFallback as exc:
                last = exc
                if exc.status == 429:
                    gemini_budget.exhaust(key_id)
                    print(f"[gemini] key {key_id} returned 429", flush=True)
                    break
                if attempt == 0 and exc.status in RETRY_STATUSES:
                    wait = float(RETRY_WAIT_S)
                    print(
                        f"[warn] {exc.reason}. Retrying Gemini once in {wait:.0f}s.",
                        flush=True,
                    )
                    if wait > 0:
                        time.sleep(wait)
                    continue
                raise
    if last is not None and last.status == 429:
        raise last
    if last is None:
        raise GeminiFallback(
            f"gemini_quota: daily budget has no request left for {caller}",
            status=429,
        )
    raise last


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


def _skewed_bounds(bounds, order, weights, duration):
    """True when one beat owns a stretch the script did not speak.

    A hallucinated tail makes word-index cuts hand 20s to one beat. Recall
    on that window is 0 even though the lesson was said earlier.
    """
    total = float(sum(weights)) or 1.0
    duration = max(float(duration), 0.01)
    weight_of = dict(zip(order, weights))
    for beat, span in (bounds or {}).items():
        weight = weight_of.get(beat)
        if not weight or not span or len(span) < 2:
            continue
        share = duration * float(weight) / total
        width = float(span[1]) - float(span[0])
        if share >= 2.0 and width > max(12.0, share * 2.5):
            return True
        if share >= 3.0 and width + 1e-6 < share * 0.45:
            return True
    return False


def _heard_between(words, start, end):
    picked = []
    for word in words or []:
        try:
            w0 = float(word.get("start"))
            w1 = float(word.get("end"))
        except (TypeError, ValueError, AttributeError):
            continue
        text = str(word.get("word") or "").strip()
        if text and w1 > start and w0 < end:
            picked.append(text)
    return " ".join(picked)


def align_short_bounds(bounds, spoken, words, duration):
    """Widen a beat whose whisper slice misses the script, without starving the neighbour.

    A cut that lands a phrase early drops that phrase from the beat. Recall
    on the short window is then under 0.5 even though the words were said
    just outside it. The neighbour keeps a 0.5 match and a 1.05s span.
    """
    import sync_gate

    order = [beat for beat in (bounds or {})]
    if len(order) < 2 or not words:
        return bounds
    tuned = {beat: (float(span[0]), float(span[1])) for beat, span in bounds.items()}

    def score(beat, start, end):
        return sync_gate.fuzzy_recall(
            str((spoken or {}).get(beat) or ""), _heard_between(words, start, end),
        )

    for beat in order:
        start, end = tuned[beat]
        current = score(beat, start, end)
        if current + 1e-9 >= sync_gate.MIN_RECALL:
            continue
        idx = order.index(beat)
        best = (start, end, current)
        for delta in (0.4, 0.8, 1.2, 1.6, 2.0):
            trials = []
            if idx > 0:
                prev = order[idx - 1]
                ps, pe = tuned[prev]
                new_start = max(ps + 1.05, start - delta)
                if new_start + 0.05 < start:
                    trials.append((prev, ps, new_start, new_start, end))
            if idx + 1 < len(order):
                nxt = order[idx + 1]
                ns, ne = tuned[nxt]
                new_end = min(ne - 1.05, end + delta)
                if new_end > end + 0.05:
                    trials.append((nxt, new_end, ne, start, new_end))
            for other, os0, oe0, ns0, ne0 in trials:
                if ne0 - ns0 < 1.05 or oe0 - os0 < 1.05:
                    continue
                mine = score(beat, ns0, ne0)
                theirs = score(other, os0, oe0)
                if mine + 1e-9 < sync_gate.MIN_RECALL or theirs + 1e-9 < sync_gate.MIN_RECALL:
                    continue
                if mine <= best[2] + 0.05:
                    continue
                best = (ns0, ne0, mine)
                tuned[beat] = (ns0, ne0)
                tuned[other] = (os0, oe0)
                start, end = ns0, ne0
    last = order[-1]
    tuned[order[0]] = (0.0, tuned[order[0]][1])
    tuned[last] = (tuned[last][0], float(duration))
    cursor = 0.0
    fixed = {}
    for index, beat in enumerate(order):
        end = float(duration) if index == len(order) - 1 else tuned[beat][1]
        end = max(cursor + 1.05, min(float(duration), end))
        if index == len(order) - 1:
            end = float(duration)
        fixed[beat] = (cursor, end)
        cursor = end
    if abs(cursor - float(duration)) > 0.05:
        return bounds
    return fixed


def silence_bounds(wav_path, order, min_gap=0.50, thresh_db=-36.0):
    """Split on the long pauses between paragraphs. None when the gaps are not one per beat."""
    present = [beat for beat in order or []]
    if len(present) < 2:
        return None
    try:
        with wave.open(str(wav_path), "rb") as handle:
            rate = handle.getframerate() or 1
            width = handle.getsampwidth()
            channels = handle.getnchannels() or 1
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return None
    if width != 2 or not frames:
        return None
    import array
    samples = array.array("h")
    try:
        samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
    except ValueError:
        return None
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        return None
    duration = len(samples) / float(rate)
    frame = max(1, int(rate * 0.02))
    amp = (10 ** (float(thresh_db) / 20.0)) * 32768.0
    gaps = []
    gap_start = None
    for index in range(0, len(samples), frame):
        chunk = samples[index:index + frame]
        if not chunk:
            break
        energy = (sum(sample * sample for sample in chunk) / len(chunk)) ** 0.5
        at = index / float(rate)
        if energy < amp:
            if gap_start is None:
                gap_start = at
        elif gap_start is not None:
            if at - gap_start >= min_gap:
                gaps.append((gap_start, at))
            gap_start = None
    interior = [gap for gap in gaps if gap[0] > 0.25 and gap[1] < duration - 0.25]
    need = len(present) - 1
    if len(interior) < need:
        return None
    chosen = sorted(interior, key=lambda gap: gap[1] - gap[0], reverse=True)[:need]
    chosen.sort()
    cuts = [0.0]
    for start, end in chosen:
        cuts.append((start + end) / 2.0)
    cuts.append(duration)
    bounds = {}
    for beat, start, end in zip(present, cuts, cuts[1:]):
        if end - start < 1.05:
            return None
        bounds[beat] = (start, end)
    return bounds


def _bounds_heard_ok(bounds, spoken, words):
    """True when each beat's slice actually says that beat."""
    import sync_gate
    if not words:
        return False
    scores = []
    for beat, span in bounds.items():
        heard = _heard_between(words, span[0], span[1])
        scores.append(sync_gate.fuzzy_recall(str((spoken or {}).get(beat) or ""), heard))
    if not scores:
        return False
    return min(scores) + 1e-9 >= sync_gate.MIN_RECALL


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

    def _tuned(bounds):
        if not bounds or not usable:
            return bounds
        tuned = align_short_bounds(bounds, spoken, usable, duration)
        if (
            tuned
            and all(end - start >= 1.05 for start, end in tuned.values())
            and not _skewed_bounds(tuned, present, weights, duration)
        ):
            return tuned
        return bounds

    if usable:
        last = max(word["end"] for word in usable)
        if last >= 0.5 * duration:
            content = _content_bounds(present, spoken, usable, duration)
            if content and not _skewed_bounds(content, present, weights, duration):
                return _tuned(content)
            bounds = _from_words(present, weights, usable, duration)
            if (
                bounds
                and all(end - start >= 1.05 for start, end in bounds.values())
                and not _skewed_bounds(bounds, present, weights, duration)
            ):
                return _tuned(bounds)
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
    silence = silence_bounds(wav_path, list(bounds) or list(order))
    if silence and words and _bounds_heard_ok(silence, spoken, words):
        if not _skewed_bounds(silence, list(silence), [1] * len(silence), duration):
            bounds = silence
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
