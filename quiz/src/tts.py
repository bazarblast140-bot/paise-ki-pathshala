"""Voice engine.

Order: ElevenLabs (primary, tumhari female Hindi voice) -> edge-tts (hi-IN-SwaraNeural)
-> silent track (pipeline kabhi crash na ho, video phir bhi bane).

Har call ka output ek WAV file + uska exact duration, kyunki render.py ko
timing chahiye segment length set karne ke liye.
"""
import hashlib
import json
import subprocess
import shutil
from pathlib import Path

import config as C

CACHE = C.OUT / "voice_cache"
CACHE.mkdir(exist_ok=True, parents=True)

# Auth ek baar fail ho gaya to har line pe dobara try karne ka koi matlab nahi —
# ek hi run me 7 identical error aate the. Ab pehli baar pe hi haar maan lete hain.
_eleven_dead = False


# ---------------------------------------------------------------- helpers
def audio_duration(path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _to_wav(src, dst):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-ar", str(C.SAMPLE_RATE), "-ac", "1", str(dst)],
        check=True,
    )
    return dst


from numwords import speak_numbers


def clean_for_tts(text: str) -> str:
    """TTS ko emoji/symbols mat do — Swara aur ElevenLabs dono unko literal padh
    dete hain ya glitch karte hain."""
    drop = "🔥✅❌👉🎯⭐️*#_~`|<>[]{}"
    for ch in drop:
        text = text.replace(ch, " ")
    text = text.replace("?", "? ").replace("।", "। ")
    # Ank ko Hindi shabd me badlo. ElevenLabs digits theek nahi padhti —
    # "1853" ko galat ya ek-ek ank karke bol deti hai. Screen par digits
    # waise hi rehte hain, ye sirf bolne wale text par lagta hai.
    text = speak_numbers(text)
    return " ".join(text.split())


# ---------------------------------------------------------------- engines
def _eleven(text: str, out_wav: Path) -> bool:
    global _eleven_dead
    if _eleven_dead:
        return False
    if not (C.ELEVEN_API_KEY and C.ELEVEN_VOICE_ID):
        return False
    if not C.ELEVEN_API_KEY.startswith("sk_"):
        # Sabse common galti: dashboard se key ka *ID* copy kar lena. Asli key
        # sirf banate/rotate karte waqt ek baar dikhti hai aur 'sk_' se shuru hoti hai.
        print("[tts] ElevenLabs key 'sk_' se shuru nahi hoti — lagta hai key ID "
              "copy ho gaya hai, asli API key nahi. Free voice pe chal rahe hain.")
        _eleven_dead = True
        return False
    try:
        import requests
    except ImportError:
        return False
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{C.ELEVEN_VOICE_ID}"
    headers = {"xi-api-key": C.ELEVEN_API_KEY,
               "Content-Type": "application/json",
               "Accept": "audio/mpeg"}

    def payload(with_speed: bool):
        vs = {"stability": C.ELEVEN_STABILITY,
              "similarity_boost": C.ELEVEN_SIMILARITY}
        if with_speed:
            vs["speed"] = C.ELEVEN_SPEED
        return {"text": text, "model_id": C.ELEVEN_MODEL, "voice_settings": vs}

    try:
        r = requests.post(url, headers=headers, json=payload(True), timeout=90)
        # "speed" har model/plan pe supported nahi hai — 422 aata hai. Us case me
        # bina speed ke dobara try karo, warna poori voice hi free fallback pe
        # chali jaati hai sirf ek optional parameter ki wajah se.
        if r.status_code == 422:
            print(f"[tts] ElevenLabs 422 (speed unsupported?) — bina speed retry: "
                  f"{r.text[:160]}")
            r = requests.post(url, headers=headers, json=payload(False), timeout=90)
        if r.status_code != 200:
            print(f"[tts] ElevenLabs FAIL {r.status_code}: {r.text[:300]}")
            if r.status_code in (400, 401, 403):     # auth/quota — retry bekaar
                _eleven_dead = True
            return False
        mp3 = out_wav.with_suffix(".mp3")
        mp3.write_bytes(r.content)
        _to_wav(mp3, out_wav)
        mp3.unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"[tts] ElevenLabs failed: {e}")
        return False


def _edge(text: str, out_wav: Path) -> bool:
    if not shutil.which("edge-tts"):
        return False
    try:
        mp3 = out_wav.with_suffix(".mp3")
        subprocess.run(
            ["edge-tts", "--voice", C.EDGE_VOICE, f"--rate={getattr(C, 'EDGE_RATE', '+0%')}", "--text", text,
             "--write-media", str(mp3)],
            check=True, capture_output=True,
        )
        _to_wav(mp3, out_wav)
        mp3.unlink(missing_ok=True)
        return True
    except Exception as e:
        print(f"[tts] edge-tts failed: {e}")
        return False


def _silent(text: str, out_wav: Path) -> bool:
    """Aakhri fallback. Duration estimate asli TTS ke kareeb rakha hai —
    pehle 2.6 words/sec tha, jisse local dry-run 56s dikhata tha jabki asli
    edge-tts video 77s ka bana. Measured rate ~1.9 words/sec hai."""
    words = max(len(text.split()), 1)
    dur = max(1.5, words / 1.9)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anullsrc=r={C.SAMPLE_RATE}:cl=mono",
         "-t", f"{dur:.2f}", str(out_wav)],
        check=True,
    )
    print(f"[tts] SILENT fallback used ({dur:.1f}s) — voice missing!")
    return True


# ---------------------------------------------------------------- public
def say(text: str, tag: str = "") -> tuple[Path, float, str]:
    """Returns (wav_path, duration_seconds, engine_used). Cached by text hash."""
    text = clean_for_tts(text)
    key = hashlib.sha1(
        f"{text}|{C.ELEVEN_VOICE_ID}|{C.ELEVEN_MODEL}".encode()
    ).hexdigest()[:16]
    out_wav = CACHE / f"{tag}_{key}.wav" if tag else CACHE / f"{key}.wav"
    meta = out_wav.with_suffix(".json")

    if out_wav.exists() and meta.exists():
        m = json.loads(meta.read_text())
        return out_wav, m["dur"], m["engine"] + "+cache"

    for name, fn in (("elevenlabs", _eleven), ("edge", _edge), ("silent", _silent)):
        if fn(text, out_wav):
            dur = audio_duration(out_wav)
            meta.write_text(json.dumps({"dur": dur, "engine": name, "text": text}))
            return out_wav, dur, name
    raise RuntimeError("Koi TTS engine kaam nahi kiya")


def question_script(q: dict, index: int, total: int) -> str:
    """Sirf sawaal — koi lead-in nahi ("पहला सवाल" waghairah hata diya).

    Shorts me viewer scroll karke aata hai; pehle 1-2 second me agar sawaal
    shuru nahi hua to wo aage scroll kar deta hai. Isliye frame 0 se hi
    sawaal bolna shuru hota hai.

    Options alag se bolte hain — wo timer ke DAURAN chalte hain."""
    if q.get("sub") == "पहेली":
        return f"पहेली। {q['q']}"
    return q["q"]


def options_script(q: dict) -> str:
    """Timer chalte waqt bolne ke liye — chaaron option."""
    labels = ["ए", "बी", "सी", "डी"]
    opts = "। ".join(f"{labels[i]}, {o}" for i, o in enumerate(q["opts"]))
    return f"{opts}। सोचिए।"


def mask_answer(why: str, q: dict, blank: str) -> str:
    """Hint me se jawab ka naam hata do.

    Zyadatar 'why' lines jawab ke naam se hi shuru hoti hain
    ("प्रशांत महासागर धरती से bada hai") — agar aise hi bol diya to hint hi
    jawab de deta hai aur aakhri beat bekaar ho jaati hai."""
    ans = q["opts"][q["ans"]].strip()
    # Numeric jawab (saal, sankhya) ko "ये" se badalne par vaakya toot jaata
    # hai — "16 अप्रैल 1853 को" banta tha "सोलह अप्रैल ये को". Aise me shabd
    # ko hata dena behtar padhta hai: "सोलह अप्रैल को".
    if blank == "ये" and ans.replace(",", "").replace(" ", "").isdigit():
        blank = ""
    out = why
    for word in sorted({ans, *ans.split()}, key=len, reverse=True):
        if len(word) >= 3:
            out = out.replace(word, blank)
    out = " ".join(out.split())
    # Mask ke baad bache hue akele comma/dash — "तेरह अप्रैल , बैसाखी" jaisa
    out = out.replace(" ,", ",").replace(" —,", " —").replace(",,", ",")
    return out.strip(" ,-—")


def hint_script(q: dict) -> str:
    """Jawab se PEHLE bola jaata hai — sirf wajah, naam nahi.

    Kyun: jawab sunte hi log swipe kar dete hain. Agar 'क्यों' jawab ke BAAD
    aaye to wo kabhi suna hi nahi jaata. Isliye pehle wajah, aur jawab
    video ki AAKHRI line."""
    why = (q.get("why") or "").strip()
    if not why:
        return "ध्यान से देखिए।"
    return f"एक इशारा — {mask_answer(why, q, 'ये')}"


def answer_script(q: dict) -> str:
    """Video ki aakhri line. Iske baad kuch nahi."""
    labels = ["ए", "बी", "सी", "डी"]
    return f"सही जवाब है — {labels[q['ans']]}, {q['opts'][q['ans']]}।"


def cta_script() -> str:
    """Timer ke turant baad, jawab se PEHLE. Yahi ek jagah hai jahan viewer
    sach me ruka hua hai — usne 8 second sawaal pe lagaye hain aur ab use
    jawab chahiye. Isliye subscribe ki baat yahan, video ke ant me nahi:
    jawab sunte hi log swipe kar dete hain."""
    return "चैनल सब्सक्राइब कीजिए, और अब सुनिए सही जवाब।"


if __name__ == "__main__":
    p, d, e = say("नमस्ते, आज का सवाल।", "test")
    print(p, f"{d:.2f}s", e)
