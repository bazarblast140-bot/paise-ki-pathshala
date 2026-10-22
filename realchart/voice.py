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


_HI_0_99 = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
    "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस "
    "पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस "
    "सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ "
    "इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर "
    "पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी "
    "सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पंचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()


def hindi_number(n):
    """Whole number as Hindi words (Indian grouping): 1883 -> एक हज़ार आठ सौ तिरासी."""
    n = int(n)
    if n < 0:
        return "माइनस " + hindi_number(-n)
    if n < 100:
        return _HI_0_99[n]
    parts = []
    for size, word in ((10 ** 7, "करोड़"), (10 ** 5, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= size:
            parts.append(f"{hindi_number(n // size)} {word}")
            n %= size
    if n:
        parts.append(_HI_0_99[n])
    return " ".join(parts)


def edge_text(text):
    """Edge Madhur drops digits ('1883' came out as 'ek hazaar aath sau'): speak every whole number as words."""
    import re
    return re.sub(r"(?<![\d.,])\d+(?:,\d+)*(?![\d]|[.,]\d)", lambda m: hindi_number(m.group(0).replace(",", "")), str(text or ""))


def speech_seconds(path, floor_db=-40.0):
    """Seconds of voiced audio (20 ms frames above floor_db) in a wav."""
    frames = _frames_db(path)
    return 0.02 * sum(1 for db in frames if db > floor_db)


def _read_wav(path):
    import numpy as np
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
        if handle.getnchannels() > 1:
            data = data.reshape(-1, handle.getnchannels()).mean(axis=1).astype(np.int16)
    return data, rate


def _frames_db(path, frame_sec=0.02):
    import numpy as np
    data, rate = _read_wav(path)
    step = max(1, int(rate * frame_sec))
    out = []
    for i in range(0, len(data), step):
        chunk = data[i:i + step].astype(float)
        rms = float(np.sqrt((chunk ** 2).mean())) if len(chunk) else 0.0
        out.append(20 * np.log10(max(rms, 1e-3) / 32768.0))
    return out


EDGE_LEAD_SEC = 0.12
EDGE_TAIL_SEC = 0.30
MAX_PAUSE_SEC = 0.45


def tidy_clip(path, floor_db=-40.0, lead=EDGE_LEAD_SEC, tail=EDGE_TAIL_SEC, max_pause=MAX_PAUSE_SEC):
    """Trim leading/trailing hush and shrink long internal pauses. Cuts only inside
    silent 20 ms frames, so no word is ever cut. Returns the new duration."""
    import numpy as np
    data, rate = _read_wav(path)
    frames = _frames_db(path)
    step = int(rate * 0.02)
    voiced = [i for i, db in enumerate(frames) if db > floor_db]
    if not voiced:
        return len(data) / rate
    keep = []
    first, last = voiced[0], voiced[-1]
    start = max(0, first * step - int(lead * rate))
    end = min(len(data), (last + 1) * step + int(tail * rate))
    i = first
    run_start = None
    pieces = [data[start:first * step]]
    cursor = first * step
    while i <= last:
        if frames[i] <= floor_db:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                length = (i - run_start) * 0.02
                if length > max_pause:
                    half = int(max_pause / 2 * rate)
                    a, b = run_start * step, i * step
                    pieces.append(data[cursor:a + half])
                    cursor = b - half
                run_start = None
        i += 1
    pieces.append(data[cursor:end])
    out = np.concatenate(pieces).astype(np.int16)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(out.tobytes())
    del keep
    return len(out) / rate


# Madhur at +20-32% speaks ~2.2-3.2 words a second; under 1.3 w/s of voiced audio
# for a line means Edge dropped words (16-Oct sample: '83' and 'नहीं' missing).
EDGE_MIN_VOICED_RATIO = 0.55


def edge_expected_seconds(spoken):
    return len(str(spoken).split()) / 2.8


def edge_speech(text, dest, cache_key=None, rate=None):
    """Edge TTS with numbers as Hindi words, tidied pauses, and a length check against the text."""
    spoken = edge_text(text)
    last = None
    for attempt_rate in (rate, "+10%", "+0%"):
        _edge_raw(spoken, dest, rate=attempt_rate)
        voiced = speech_seconds(dest)
        last = voiced
        if voiced >= EDGE_MIN_VOICED_RATIO * edge_expected_seconds(spoken):
            tidy_clip(dest)
            return
        print(f"[voice] Edge clip short ({voiced:.1f}s voiced for {len(spoken.split())} words); retrying slower", flush=True)
    raise EdgeUnavailable(f"Edge clip truncated: {last:.1f}s voiced for '{spoken[:60]}'")


def _edge_raw(text, dest, cache_key=None, rate=None):
    """Microsoft Edge TTS (hi-IN-MadhurNeural, no key) to a 24 kHz mono wav. Raises EdgeUnavailable."""
    import hashlib
    import shutil
    import subprocess
    import sys
    dest = Path(dest)
    cache_dir = Path(__file__).resolve().parents[1] / "data" / "gemini_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    rate = rate or os.environ.get("REALCHART_EDGE_RATE", "+20%")
    digest = hashlib.sha1(f"{EDGE_VOICE}|{rate}|{text}".encode("utf-8")).hexdigest()[:12]
    cached = cache_dir / f"edge-{digest}.wav"
    if not cached.is_file():
        mp3 = cached.with_suffix(".mp3")
        try:
            # Edge Madhur is slower than Gemini Charon; +20% keeps the same script under the 50s QA gate.
            subprocess.run([sys.executable, "-m", "edge_tts", "--voice", EDGE_VOICE, f"--rate={rate}", "--text", str(text), "--write-media", str(mp3)],
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
# Silence kept at each clip edge where two clips are joined. Each TTS clip starts with
# ~0.25 s and ends with up to ~0.37 s of hush; with the 0.25 s gap that stacked to
# 0.8-0.9 s of dead air in final.mp4 (QA max 0.8 s). Capped edges keep a join near 0.55 s.
JOIN_LEAD_SEC = 0.12
JOIN_TAIL_SEC = 0.18


def trim_join_edges(path, lead=JOIN_LEAD_SEC, tail=JOIN_TAIL_SEC, floor_db=-40.0, keep_tail=False):
    """Trim a clip's leading/trailing silence down to `lead`/`tail` seconds. Voice is never cut."""
    import re
    import subprocess
    duration = _clip_seconds(path)
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", f"silencedetect=n={floor_db}dB:d=0.05",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    begin, finish = 0.0, duration
    if starts and starts[0] <= 0.01 and ends:
        begin = max(0.0, ends[0] - lead)
    if not keep_tail and starts and (len(ends) < len(starts) or ends[-1] >= duration - 0.01) and starts[-1] > begin:
        finish = min(duration, starts[-1] + tail)
    if begin <= 0.0 and finish >= duration - 0.001:
        return path
    tmp = Path(path).with_suffix(".trim.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", f"atrim={begin:.4f}:{finish:.4f},asetpts=N/SR/TB",
                    str(tmp)], check=True)
    os.replace(tmp, path)
    return path


INTERNAL_PAUSE_MAX_SEC = 0.6


def cap_internal_pauses(path, max_pause=INTERNAL_PAUSE_MAX_SEC, floor_db=-40.0):
    """Shorten a TTS clip's own mid-sentence pauses to `max_pause` (QA allows <=0.8 s dead air).

    Only silence strictly inside the clip is shortened (its middle is cut); voice is never cut.
    """
    import re
    import subprocess
    duration = _clip_seconds(path)
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", f"silencedetect=n={floor_db}dB:d={max_pause}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    cuts = []
    for start, end in zip(starts, ends):
        if start <= 0.01 or end >= duration - 0.01 or end - start <= max_pause:
            continue
        excess = (end - start) - max_pause
        mid = (start + end) / 2.0
        cuts.append((mid - excess / 2.0, mid + excess / 2.0))
    if not cuts:
        return path
    keep, cursor = [], 0.0
    for a, b in cuts:
        keep.append((cursor, a))
        cursor = b
    keep.append((cursor, duration))
    expr = "+".join(f"between(t,{a:.4f},{b:.4f})" for a, b in keep)
    tmp = Path(path).with_suffix(".pause.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", f"aselect='{expr}',asetpts=N/SR/TB",
                    str(tmp)], check=True)
    os.replace(tmp, path)
    return path


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
                info, seconds = _speak_all(texts, dest, kind, dry_run, key_prefix, parts_dir, "edge")
                if sum(seconds) > 49.0 and not os.environ.get("REALCHART_EDGE_RATE"):
                    # Madhur at +20% can still run past the 50s gate; one faster take (free, no key).
                    print(f"[voice] Edge take {sum(seconds):.1f}s; re-voicing at +32%", flush=True)
                    os.environ["REALCHART_EDGE_RATE"] = "+32%"
                    try:
                        info, seconds = _speak_all(texts, dest, kind, dry_run, key_prefix, parts_dir, "edge")
                    finally:
                        os.environ.pop("REALCHART_EDGE_RATE", None)
                return info, seconds
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
        if str(text).strip() and not dry_run:
            trim_join_edges(part, lead=JOIN_LEAD_SEC if index else 0.25, keep_tail=index == len(texts) - 1)
            cap_internal_pauses(part)
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
    # The last clip is already tidied (tidy_clip keeps EDGE_TAIL_SEC after the last
    # voiced frame); the old reverse-silenceremove cut the final word ('advice na-').
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
