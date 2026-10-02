"""Topic, voice, and picture must describe the same lesson.

After TTS, the spoken audio is transcribed (faster-whisper, Hindi, cached) and
compared with the title and with each scene's on-screen lesson text. A mismatch
or a failed transcription blocks the upload. The model is small, int8, on CPU.
"""
import os
import pathlib
import re
import subprocess

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "whisper"
# Whisper word ends overrun a scene cut by a few tenths. The window is that pad.
WINDOW_PAD = 0.45
SAID_PAD = 0.60
# Share of the outline slice that the transcript actually contains.
# Inserted whisper noise does not dilute this the way a 2-gram Jaccard does.
MIN_RECALL = 0.5
_SKIP_ROLES = {"meter", "axis", "source", "cta", "brand", "glow"}
_STOP = {"है", "की", "में", "और", "तो", "हो", "यह", "को", "का", "के", "से", "पर", "भी", "एक"}

# Spoken Hindi digits, including the mis-hearings this voice produces.
_ONES = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "चाए": 4,
    "पाच": 5, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8,
    "अथ": 8, "नौ": 9, "नो": 9, "दस": 10, "चौदह": 14, "चोद": 14,
    "पंद्रह": 15, "उन्नीस": 19, "उनीस": 19, "बीस": 20, "तेइस": 23,
    "तेईस": 23, "साठ": 60, "सत्तर": 70, "बहत्तर": 72, "नब्बे": 90,
    "पचानवे": 95, "साथ": 7,
}
_HUNDRED = {"सौ", "सो"}
_DEC = {"दशमलव", "दशम्लव"}
# Longer aliases first so a short token does not eat a longer one.
_ALIAS = (
    ("पंद्राधशम", "15.7"),
    ("गुनापन्र", "गुना"),
    ("रहल्चल", "हलचल"),
    ("हल्चल", "हलचल"),
    ("तंग्रेखा", "तंग रेखा"),
    ("अट्यार", "एटीआर"),
    ("एशेल", "एसएल"),
    ("अंट्री", "एंट्री"),
    ("अखिरी", "आखिरी"),
    ("दशम्लव", "दशमलव"),
    ("एचडीएफसी", "एचडीएफसी"),
    ("जोखिन", "जोखिम"),
    ("गल्ती", "गलती"),
    ("गलत", "गलती"),
    ("हकंडल", "कैंडल"),
    ("पोईंट", "पॉइंट"),
    ("बराभर", "बराबर"),
    ("कत्ता", "कटता"),
    ("कती", "कटती"),
    ("अशत", "औसत"),
    ("रुपै", "रुपये"),
    ("बैंग", "बैंक"),
    ("उपर", "ऊपर"),
    ("स्यल", "एसएल"),
    ("कात", "काट"),
    ("ATR", "एटीआर"),
    ("atr", "एटीआर"),
    ("HDFC", "एचडीएफसी"),
    ("hdfc", "एचडीएफसी"),
    ("SL", "एसएल"),
    ("sl", "एसएल"),
    ("यल", "एसएल"),
)

_MODEL = None


def _alias(text):
    out = text or ""
    for src, dst in _ALIAS:
        out = out.replace(src, dst)
    return out


def _tokenize(text):
    glued = re.sub(r"(\d)\s*,\s*(\d{3})", r"\1\2", _alias(text or ""))
    # Skip danda (U+0964/U+0965) so "है।" is the word है.
    return re.findall(r"\d+(?:\.\d+)?|[A-Za-z\u0900-\u0963\u0966-\u097F\uA8E0-\uA8FF]+", glued)


def _is_dec(tokens, i):
    if i >= len(tokens):
        return False
    if tokens[i] in _DEC or tokens[i] == "दशमलव":
        return True
    return tokens[i] == "दशम" and i + 1 < len(tokens) and tokens[i + 1] == "लव"


def _skip_dec(tokens, i):
    if i < len(tokens) and tokens[i] == "दशम" and i + 1 < len(tokens) and tokens[i + 1] == "लव":
        return i + 2
    if i < len(tokens) and (tokens[i] in _DEC or tokens[i] == "दशमलव"):
        return i + 1
    return i


def _append_number(out, val, tokens, i):
    if _is_dec(tokens, i):
        i = _skip_dec(tokens, i)
        if i < len(tokens) and tokens[i] in _ONES and _ONES[tokens[i]] < 10:
            out.append(f"{val}.{_ONES[tokens[i]]}")
            return i + 1
    out.append(str(val))
    return i


def _numbers(tokens):
    """Turn Hindi number-words into digits. Other tokens pass through."""
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.fullmatch(r"\d+", tok) and _is_dec(tokens, i + 1):
            val = int(tok)
            i += 1
            i = _append_number(out, val, tokens, i)
            continue
        if tok in _ONES and _ONES[tok] < 10 and i + 1 < len(tokens) and tokens[i + 1] in _HUNDRED:
            val = _ONES[tok] * 100
            i += 2
            if i < len(tokens) and tokens[i] in _ONES and tokens[i] not in _HUNDRED and _ONES[tokens[i]] < 100:
                val += _ONES[tokens[i]]
                i += 1
            i = _append_number(out, val, tokens, i)
            continue
        if tok in _ONES:
            val = _ONES[tok]
            i += 1
            i = _append_number(out, val, tokens, i)
            continue
        if tok in _HUNDRED:
            out.append("100")
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def normalize(text):
    """Canonical tokens: aliases applied, Hindi number-words written as digits."""
    return " ".join(_numbers(_tokenize(text)))


def tokens(text):
    return [tok for tok in normalize(text).split() if tok]


def numbers_in(text):
    raw = re.sub(r"(\d),(\d)", r"\1\2", text or "")
    return re.findall(r"\d+(?:\.\d+)?", raw)


def _ngrams(text, n=2):
    toks = tokens(text)
    if len(toks) < n:
        return set(tuple(toks)) if toks else set()
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a, b):
    left, right = _ngrams(a), _ngrams(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def recall(script, heard):
    """How much of this scene's outline shows up in the transcript window."""
    src = [tok for tok in tokens(script) if tok not in _STOP]
    if not src:
        return 0.0
    got = set(tokens(heard))
    return sum(1 for tok in src if tok in got) / len(src)


def content_tokens(text):
    out = []
    for tok in tokens(text):
        if tok in _STOP or len(tok) < 2:
            continue
        if tok.isdigit():
            continue
        out.append(tok)
    return out


def script_slice(beat_text, beat_t0, beat_t1, start, end):
    words = str(beat_text or "").split()
    if not words:
        return ""
    dur = max(1e-6, float(beat_t1) - float(beat_t0))
    a = int(round((float(start) - float(beat_t0)) / dur * len(words)))
    b = int(round((float(end) - float(beat_t0)) / dur * len(words)))
    a = max(0, min(len(words), a))
    b = max(0, min(len(words), b))
    if b <= a:
        b = min(len(words), a + 1)
    return " ".join(words[a:b])


def lesson_lines(frame):
    """On-screen lesson text. Meters, axes, the poll, and the brand are not the lesson."""
    lines = []
    for layer in getattr(frame, "text_layers", None) or []:
        text = str(layer.get("text") or "").strip()
        if not text or layer.get("faint") or layer.get("persist"):
            continue
        if (layer.get("role") or "lesson") in _SKIP_ROLES:
            continue
        lines.append(text)
    return lines


def answer_terms_for(title, lesson=None):
    """What the title promised. An ATR lesson answers with the distance and the name ATR."""
    if lesson:
        return ["एटीआर", "एसएल", str(lesson.get("distance") or ""), "हलचल"]
    found = []
    for tok in content_tokens(title):
        if tok not in found:
            found.append(tok)
    for num in numbers_in(title or ""):
        if num not in found:
            found.append(num)
    return found


def _said_tokens(words, end):
    heard = [w["word"] for w in words if float(w.get("end") or 0) <= float(end) + SAID_PAD]
    return set(tokens(" ".join(heard)))


def _window_text(words, start, end):
    picked = []
    for word in words:
        w0, w1 = float(word.get("start") or 0), float(word.get("end") or 0)
        if w1 >= start and w0 <= end:
            picked.append(word["word"])
    return " ".join(picked)


def check(words, scenes, beats_text, beat_times, answer_terms):
    """Fail closed. `words` are {word, start, end} on the finished timeline."""
    words = list(words or [])
    if not words or not any(str(w.get("word") or "").strip() for w in words):
        return {
            "ok": False,
            "reason": "transcription failed: empty transcript",
            "transcript": "",
            "scenes": [],
        }
    transcript = " ".join(str(w.get("word") or "") for w in words)
    full = set(tokens(transcript))
    missing_title = []
    for term in answer_terms or []:
        need = tokens(term) or ([term] if str(term).strip() else [])
        if need and not all(tok in full for tok in need):
            missing_title.append(str(term))
    reasons = []
    if missing_title:
        reasons.append("title promise not answered: " + ", ".join(missing_title))
    rows = []
    for scene in scenes or []:
        start, end = float(scene["start"]), float(scene["end"])
        beat = scene.get("beat") or ""
        span = beat_times.get(beat) or (start, end)
        spoken = script_slice(
            (beats_text or {}).get(beat, ""), span[0], span[1],
            start - WINDOW_PAD, end + WINDOW_PAD,
        )
        heard = _window_text(words, start - WINDOW_PAD, end + WINDOW_PAD)
        score = jaccard(spoken, heard)
        covered = recall(spoken, heard)
        show = " ".join(scene.get("show") or [])
        said = _said_tokens(words, end)
        heard_set = set(tokens(heard))
        missing_numbers = [num for num in numbers_in(show) if num not in said]
        shared = [tok for tok in content_tokens(show) if tok in heard_set]
        if not shared:
            shared = [num for num in numbers_in(show) if num in heard_set]
        row = {
            "start": round(start, 2),
            "end": round(end, 2),
            "beat": beat,
            "kind": scene.get("kind") or "",
            "jaccard": round(score, 3),
            "recall": round(covered, 3),
            "missing_numbers": missing_numbers,
            "shared": shared[:6],
        }
        rows.append(row)
        if covered + 1e-9 < MIN_RECALL:
            reasons.append(
                f"{row['kind'] or beat} {row['start']:.1f}-{row['end']:.1f}s "
                f"narration does not match the script (recall {covered:.2f})"
            )
        if missing_numbers:
            reasons.append(
                f"{row['kind'] or beat} {row['start']:.1f}-{row['end']:.1f}s "
                f"shows {', '.join(missing_numbers)} before it is said"
            )
        if show.strip() and not shared:
            reasons.append(
                f"{row['kind'] or beat} {row['start']:.1f}-{row['end']:.1f}s "
                "on-screen text is not what this scene says"
            )
    reason = "; ".join(reasons)
    return {
        "ok": not reasons,
        "reason": reason,
        "transcript": normalize(transcript),
        "scenes": rows,
    }


def model():
    """Cached small Hindi model. CPU int8 is enough and does not need a GPU."""
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        root = os.environ.get("WHISPER_CACHE") or str(CACHE)
        os.makedirs(root, exist_ok=True)
        _MODEL = WhisperModel("small", device="cpu", compute_type="int8", download_root=root)
    return _MODEL


def pcm(path):
    """16 kHz mono float32. faster-whisper's file decoder does not match this PyAV."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        check=True, capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def transcribe_file(path):
    audio = pcm(path)
    if audio.size == 0:
        raise RuntimeError("empty audio")
    segments, _info = model().transcribe(
        audio, language="hi", word_timestamps=True, vad_filter=True,
    )
    words = []
    for seg in segments:
        for word in seg.words or []:
            text = str(word.word or "").strip()
            if text:
                words.append({"word": text, "start": float(word.start), "end": float(word.end)})
    return words


def transcribe_beats(beat_audio, beat_times, beats=("hook", "pehchan", "matlab", "karein")):
    """Word times on the joined timeline. Each beat's clock starts at the beat."""
    words = []
    for beat in beats:
        path = beat_audio[beat]
        t0 = float(beat_times[beat][0])
        for word in transcribe_file(path):
            words.append({
                "word": word["word"],
                "start": word["start"] + t0,
                "end": word["end"] + t0,
            })
    return words
