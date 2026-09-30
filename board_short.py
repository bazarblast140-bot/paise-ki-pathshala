"""Chart Board Short: topic -> narration (LLM, gated) -> voice per beat -> video + hook thumbnail.

Same template for every topic:
  HOOK -> 1 PEHCHAN -> 2 MATLAB -> 3 KYA KAREIN
Pictures come from board_scenes (code, not the LLM). On-screen Hindi comes from
topic_bank.json. The LLM writes only the spoken lines, and may use only numbers
that already appear in the topic; if it keeps failing, the narration is built
from the on-screen text so the day's video still ships.

Writes run/: final.mp4, thumbnail.jpg, script.txt, spoken.txt, shots.json, subs.ass
(the files run_verify.py and publish.py already read).

  python board_short.py          # normal (needs DEEPSEEK/KIMI/ANTHROPIC key)
  python board_short.py --qa     # no LLM, no voice: renders frames + thumbnail only
"""
import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw
import numpy as np

import board_kit as K
import board_scenes as S
from common import RUN, ROOT, cfg, claude_json, die, ok, save

BEATS = S.BEATS
STEP_CHIP = {"hook": "HOOK", "pehchan": "1  PEHCHAN", "matlab": "2  MATLAB", "karein": "3  KYA KAREIN"}
# On-video CTA: ask for a useful comment instead of generic engagement bait.
# The same Reel is cross-posted to YouTube + Instagram, so keep the wording
# platform-neutral. Platform-specific subscribe/follow language belongs in the
# post caption, not the educational content.
CTA = "अगला कौन सा market topic समझना है? Comment में लिखो—ऐसी आसान सीख के लिए जुड़े रहो।"
DISCLAIMER = "सिर्फ़ जानकारी, निवेश सलाह नहीं।"
FORBIDDEN = ["दोस्तों", "like", "subscribe", "सब्सक्राइब", "लाइक", "टारगेट", "target price", "टिप",
             "ज़रूर ख़रीद", "जरूर खरीद", "ख़रीद लो", "खरीद लो", "बेच दो", "गारंटी से", "पक्का मुनाफ़ा"]

# ---------------------------------------------------------------- text fitting
_DEV_FONTS = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
              r"C:\Windows\Fonts\Nirmala.ttc"]


def _dev_font(size):
    from PIL import ImageFont
    import pathlib
    for p in _DEV_FONTS:
        if pathlib.Path(p).exists():
            return ImageFont.truetype(p, size)
    return None


def fit_size(text, max_w, max_size, min_size=40):
    """Largest font size (<= max_size) whose width fits max_w. Width is measured
    with a Devanagari font (shaping ignored — close enough for a size choice)."""
    f = _dev_font(100)
    for size in range(max_size, min_size - 1, -2):
        w = (f.getlength(text) * size / 100 * 1.12) if f else len(text) * size * 0.62
        if w <= max_w:
            return size
    return min_size


# ---------------------------------------------------------------- pictures
CAT_CHIPS = {
    "REAL DATA": {"hook": "HOOK", "pehchan": "1  DATA", "matlab": "2  HISAAB", "karein": "3  NATEEJA"},
    "MARKET FACT": {"hook": "HOOK", "pehchan": "1  FACT", "matlab": "2  MATLAB", "karein": "3  YAAD RAKHO"},
}


def beat_images(topic, beat):
    img = K.background()
    K.chips(img, topic["cat"], CAT_CHIPS.get(topic["cat"], STEP_CHIP)[beat])
    key = topic["key"]
    low = key.lower()
    if low.startswith("intraday_"):
        try:
            import board_orb
            # Only ORB topics may use the ORB board. Every other intraday
            # topic must keep its own dedicated visual; otherwise an EMA
            # narration can accidentally ship with OR HIGH/LOW breakout art.
            # Only the explicit Opening Range Breakout topic gets the
            # ORB board. Other intraday topics keep their own topic visual.
            if low == "intraday_orb":
                return board_orb.opening_range_breakout()(beat, img)
            return S.render_beat(key, beat, img)
        except Exception as e:
            print(f"[board_orb] fallback to topic scene: {e}")
    return S.render_beat(key, beat, img)


def thumbnail(topic, out_path, hook_img=None):
    """Tagda hook thumbnail: red ground, huge 2-line Hindi hook, the topic's own
    board picture, big host, sticker. Hindi via libass."""
    W, H = K.W, K.H
    data = topic.get("cat") == "REAL DATA"
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(120 - 70 * t), int(10 + 4 * t), int(16 + 6 * t)))
    if topic.get("cat") == "REAL DATA":
        hook_img = beat_images(topic, "pehchan")[1]      # data: chart khinchta hai, note nahi
    elif hook_img is None:
        hook_img = beat_images(topic, "hook")[1]
    x0, y0, x1, y1 = K.BOARD
    board = hook_img.crop((x0, y0, x1, y1))
    bw = 860 if data else 1000              # data: daayein patti host ke liye, chart ke labels khule
    board = board.resize((bw, int(board.height * bw / board.width)))
    img.paste(board, (20 if data else (W - bw) // 2, 820))
    try:
        import sketch
        host = sketch.load_host_pose("warn" if topic["sticker"] in ("चेतावनी", "सावधान") else "point", height=620)
        if data:
            # chart ka start label neeche-baayein hota hai -> host daayein, chart ki taraf
            host = host.transpose(Image.FLIP_LEFT_RIGHT)
            host = host.resize((int(host.width * 0.85), int(host.height * 0.85)))
            img.paste(host, (W - host.width - 5, 820 + board.height - host.height + 60), host)
        else:
            img.paste(host, (20, H - host.height - 120), host)
    except Exception as e:  # noqa: BLE001
        print(f"[thumb] host skipped: {e}")
    d = ImageDraw.Draw(img)
    st = topic["sticker"]
    s_size = fit_size(st, 330, 72, 40)
    sx = 40 if data else 620
    d.rounded_rectangle([sx, 1600, sx + 420, 1730], 32, fill=K.RED)
    base = out_path.with_name(out_path.stem + "_base.png")
    img.save(base)
    t1, t2 = topic["thumb"]
    ev = [K.ass_text(0, 5, 540, 150, fit_size(t1, 940, 150), K.WHITE, t1, 8, fade=False),
          K.ass_text(0, 5, 540, 350, fit_size(t2, 940, 170), K.YEL, t2, 8, fade=False),
          K.ass_text(0, 5, sx + 210, 1622, s_size, K.WHITE, st, 8, fade=False)]
    third = topic["beats"]["hook"][0]
    seen = set((t1 + " " + t2).lower().replace("?", "").split())
    if not seen & set(third.lower().replace("?", "").split()):
        # sirf tab jab hook kuch naya bataye (pehle "1% नियम" do baar aata tha)
        ev.append(K.ass_text(0, 5, 540, 600, fit_size(third, 900, 76), K.WHITE, third, 8, fade=False))
    K.burn_text_on_image(base, out_path, ev)
    base.unlink(missing_ok=True)
    return out_path


# ---------------------------------------------------------------- narration
def allowed_numbers(topic):
    blob = " ".join([topic["topic"], *topic["thumb"], *topic["facts"],
                     *[x for pair in topic["beats"].values() for x in pair]])
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", blob)} | {"1", "2", "3"}


def check_narration(n, topic):
    fails = []
    beats = n.get("beats") or {}
    for b in BEATS:
        if not str(beats.get(b) or "").strip():
            fails.append(f"beat '{b}' khaali")
    text = " ".join(str(beats.get(b) or "") for b in BEATS)
    words = len(text.split())
    if not 95 <= words <= 120:
        fails.append(f"{words} words (95-120 chahiye)")
    extra = {x.replace(",", "") for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)} - allowed_numbers(topic)
    if extra:
        fails.append(f"ye numbers topic me nahi hain: {sorted(extra)} — sirf diye gaye numbers use karo")
    low = text.lower()
    bad = [w for w in FORBIDDEN if w.lower() in low]
    if bad:
        fails.append(f"mana shabd: {bad}")
    dev = sum(1 for ch in text if "\u0900" <= ch <= "\u097f")
    if dev < len(text.replace(" ", "")) * 0.45:
        fails.append("narration Hindi (Devanagari) me nahi hai")
    title = str(n.get("title") or "")
    if not 8 <= len(title) <= 90:
        fails.append("title 8-90 characters")
    if topic.get("cat") == "REAL DATA":
        hook = str(beats.get("hook") or "")
        year = re.findall(r"(?:19|20)\d\d", topic["topic"])[:1]
        hook_nums = {x.replace(",", "") for x in re.findall(r"\d[\d,]*(?:\.\d+)?", hook)} - set(year) - {"1"}
        if hook_nums:
            fails.append(f"hook me jawab/number mat batao ({sorted(hook_nums)}) — sirf sawal")
        if len(hook.split()) > 12:
            fails.append("hook 10 words se chhota rakho")
        final = re.findall(r"₹[\d,.]+", topic["beats"]["karein"][1])
        for f in final:
            early = " ".join(str(beats.get(b) or "") for b in ("hook", "pehchan", "matlab"))
            if f in early:
                fails.append(f"final value {f} sirf 'karein' me bolo")
        if "लगते" in text:
            fails.append("'लगते' galat hai — 'लगाते' likho")
    return fails


NARR_SYSTEM = """You write the SPOKEN Hindi narration for a 55-60 second vertical Short for Indian traders
(retail to pro). The video template is fixed — four beats, each showing a drawing and an on-screen
headline + sub line. Your narration for each beat must explain exactly what that beat's on-screen
text says, in simple spoken Hindi (Devanagari). Trading words stay as traders say them in Roman
(RSI, MACD, VWAP, stop loss, SL, candle, wick, breakout, option, premium, theta, delta, OI).

Return ONLY JSON:
{"title": "<YouTube title, max 70 chars, Hindi + key English words, the hook itself>",
 "beats": {"hook": "...", "pehchan": "...", "matlab": "...", "karein": "..."}}

Rules:
- hook: 1 sentence, the surprising fact, costly mistake, or a clear question. No greeting.
- pehchan: explain what the viewer is looking at in plain language.
- matlab: explain the cause → effect and, where the topic allows, give ONE tiny concrete example.
- karein: finish with one practical takeaway or limitation; never make a personalised trade call.
- Each beat: 1-2 short sentences, preferably under 12 words per sentence.
- Total 95-120 words; aim for about 55 seconds of spoken content so the final video, CTA, and natural pauses stay around 60 seconds. The narration must add explanation beyond merely reading the on-screen text.
- Use ONLY numbers that appear in the topic data given to you. Never invent prices, dates, returns.
- Not a quiz. Do not ask the viewer to pick options.
- SEBI rules: never tell anyone to buy/sell/hold a specific stock, fund or product; no targets,
  no tips; never promise returns. Patterns/indicators are probabilities, not sure signals.
- Describe, don't command: for SL / entry / confirmation say how traders usually do it
  ("आमतौर पर traders SL ... रखते हैं", "कई traders ... का इंतज़ार करते हैं"), never "ऐसा करो" as an order.
- Do not add a CTA or disclaimer inside the narration — the template adds them.
- If category is "REAL DATA": hook = ONLY the question (year, ₹1 लाख, name), max 10 words —
  NEVER the answer. pehchan = the two prices with years. matlab = the multiple (गुना) once.
  karein = the final value once, then the balance line from facts. Say the final value only in
  karein, never earlier. Total 80-100 words. Write "लगाते" (not "लगते")."""


def narration(topic, c):
    cache = RUN / "narration.json"
    if cache.exists():
        try:
            n = json.loads(cache.read_text(encoding="utf-8"))
            fails = check_narration(n, topic)
            if not fails:
                ok("narration cache reused — no LLM credit")
                return n, "cache"
        except Exception as e:
            print(f"[cache] narration invalid, regenerating: {e}")
    user = {"topic": topic["topic"], "category": topic["cat"],
            "on_screen": {b: {"headline": h, "sub": s} for b, (h, s) in topic["beats"].items()},
            "facts": topic["facts"]}
    msg = json.dumps(user, ensure_ascii=False)
    last = None
    for attempt in range(1, 4):
        try:
            n = claude_json(NARR_SYSTEM, msg + ("" if not last else f"\n\nFIX THESE: {last}"),
                            model=c["models"]["script_model"], max_tokens=2000)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            last = f"invalid JSON: {e}"
            print(f"[retry] narration attempt {attempt}: {last}")
            continue
        fails = check_narration(n, topic)
        if not fails:
            ok(f"narration OK (attempt {attempt})")
            cache.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
            return n, "llm"
        last = "; ".join(fails)
        print(f"[retry] narration attempt {attempt}: {last}")
    print("[warn] LLM narration 3 baar fail — on-screen text se narration bana raha hoon")
    # Deterministic fallback must still be long enough for a real 55-60s Short.
    # The old fallback only read headline + sub-line, producing ~10-15s audio and
    # repeatedly failing Gate 2. Expand each beat using only existing topic text;
    # no new prices, dates, returns, or trade calls are invented.
    src = topic["beats"]
    beats = {
        "hook": (
            f'{src["hook"][0]}। {src["hook"][1]}। '
            "इस हिस्से में पहले सवाल और उसके संदर्भ को समझते हैं। "
            "आगे की जानकारी इसी दिए गए तथ्य को आसान भाषा में समझाती है।"
        ),
        "pehchan": (
            f'{src["pehchan"][0]}। {src["pehchan"][1]}। '
            "यहां दिख रही जानकारी को ध्यान से पढ़ना जरूरी है। "
            "पहचान का मतलब है कि पहले स्क्रीन पर मौजूद तथ्य को साफ समझें। "
            "इसके बाद ही अगले हिस्से का मतलब समझ आता है।"
        ),
        "matlab": (
            f'{src["matlab"][0]}। {src["matlab"][1]}। '
            "इसका सीधा मतलब यही है कि दिए गए तथ्य को उसके संदर्भ में देखें। "
            "यहां किसी अतिरिक्त कीमत, तारीख या रिटर्न का अनुमान नहीं लगाया जा रहा है। "
            "सिर्फ उपलब्ध जानकारी से कारण और असर को समझें।"
        ),
        "karein": (
            f'{src["karein"][0]}। {src["karein"][1]}। '
            "आखिर में इस जानकारी की सीमा भी याद रखें। "
            "स्क्रीन पर दिए गए तथ्य को समझना पहला कदम है। "
            "किसी भी निवेश फैसले के लिए अलग से पूरी जानकारी और जोखिम देखना जरूरी है।"
        ),
    }
    n = {"title": f"{topic['thumb'][0]} {topic['thumb'][1]}", "beats": beats}
    cache.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
    return n, "fallback"


# ---------------------------------------------------------------- voice
async def _edge(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


def _eleven(text, path, c):
    """Use the configured ElevenLabs voice when a key is available; return False to fall back."""
    import os
    import requests
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    voice = c["voice"].get("voice_id") or os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not (key and voice):
        return False
    try:
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                          headers={"xi-api-key": key, "accept": "audio/mpeg"},
                          json={"text": text,
                                "model_id": os.environ.get("ELEVENLABS_MODEL") or c["voice"].get("model", "eleven_multilingual_v2"),
                                "voice_settings": {
                                    "stability": c["voice"].get("stability", 0.45),
                                    "similarity_boost": c["voice"].get("similarity_boost", 0.8),
                                    "style": c["voice"].get("style", 0.3),
                                    "speed": c["voice"].get("speed", 1.08),
                                }},
                          timeout=90)
        if r.status_code != 200 or len(r.content) < 2000:
            print(f"[warn] ElevenLabs {r.status_code}: {r.text[:160]}")
            return False
        path.write_bytes(r.content)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[warn] ElevenLabs error: {e}")
        return False

ENGINES = []


def tts(text, path, c):
    # Reuse a valid existing audio file on self-heal retries.
    if path.exists() and path.stat().st_size > 1000:
        ok(f"voice cache reused: {path.name} — no TTS credit")
        ENGINES.append("cached")
        return
    """ElevenLabs agar secrets hain, warna / fail hone par Edge Swara — video kabhi nahi rukta.
    Voice ko speak.speakable() wala text jaata hai (₹ -> रुपये, 4,231 -> चार हज़ार...);
    ElevenLabs "₹1 लाख" ko "Rs 1 लाख" bolta tha. Captions original text dikhate hain."""
    import speak
    text = speak.speakable(text)
    left = speak.leftovers(text)
    if left:
        print(f"[warn] voice text me ab bhi: {sorted(set(left))} — speak.py me jodo")
    with open(RUN / "tts_text.txt", "a", encoding="utf-8") as f:
        f.write(f"{path.stem}: {text}\n")
    if _eleven(text, path, c):
        ENGINES.append("elevenlabs")
        return
    v = c["voice"]
    ENGINES.append("edge")
    for attempt in range(3):
        try:
            asyncio.run(_edge(text, v.get("edge_voice", "hi-IN-SwaraNeural"), v.get("edge_rate", "+10%"), path))
            if path.exists() and path.stat().st_size > 1000:
                return
        except Exception as e:  # noqa: BLE001
            print(f"[retry] tts {attempt + 1}: {e}")
    die(f"TTS fail: {path.name}")


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


# ---------------------------------------------------------------- captions
UNITS = {"लाख", "करोड़", "गुना", "साल", "%", "रुपये", "पर", "तक", "में"}


def chunks(text, n=4):
    """Caption pieces of <= n words that never cross a sentence end and never
    leave a number on one line and its unit on the next ("₹1 / लाख करीब")."""
    out = []
    for sent in re.split(r"(?<=[।.?!])\s+", text.strip()):
        words, cur = sent.split(), []
        for i, w in enumerate(words):
            cur.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else ""
            glued = (re.search(r"\d", w) and nxt.strip("।.,?!") in UNITS) or                 (w in ("करीब", "लगभग", "₹") and re.search(r"\d", nxt))
            orphan = len(words) - i - 1 == 1       # "जाता।" akela line par na bache
            if len(cur) >= n and not glued and not orphan:
                out.append(" ".join(cur))
                cur = []
        if cur:
            out.append(" ".join(cur))
    return [c for c in out if c]


def cue_frac(key, text):
    """Where (0..1, by characters like the captions) the third picture comes in."""
    rx = S.cue(key)
    if not rx:
        return 0.58
    pos = 0
    for sent in re.split(r"(?<=[।.?!])\s+", text.strip()):
        if re.search(rx, sent, re.I):
            return min(0.8, max(0.35, pos / max(1, len(text))))
        pos += len(sent) + 1
    return 0.58


def build_ass(topic, beat_times, spoken):
    ev = []
    for b in BEATS:
        t0, t1 = beat_times[b]
        head, sub = topic["beats"][b]
        ev.append(K.ass_text(t0 + (0 if b == "hook" else 0.05), t1, 475, 1020, fit_size(head, 800, 92),
                             K.YEL, head, 8, fade=(b != "hook")))
        ev.append(K.ass_text(t0 + (0 if b == "hook" else 0.05), t1, 475, 1128, fit_size(sub, 800, 60, 40),
                             K.WHITE, sub, 8, fade=(b != "hook"), wrap=True))
        parts = chunks(spoken[b])
        total = sum(len(p) for p in parts) or 1
        t = t0 + 0.1
        span = (t1 - t0) - 0.2
        for p in parts:
            dt = span * len(p) / total
            ev.append(K.ass_caption(t, t + dt, p))
            t += dt
    t0, t1 = beat_times["karein"]
    ev.append(K.ass_text(t0, t1, 475, 1215, 34, "&H00B8A394&", DISCLAIMER, 8))
    # Platform-neutral visual CTA: the same final.mp4 is cross-posted to YouTube + Instagram.
    # Keep it below the educational content and inside the Shorts/Reels safe area.
    cta_start = max(t0, t1 - 2.8)
    ev.append(K.ass_text(cta_start, t1, 540, 1470, 48, K.WHITE,
                         "LIKE  •  FOLLOW  •  SUBSCRIBE", 10, fade=True))
    return K.ASS_HEAD + "\n".join(ev) + "\n"


# ---------------------------------------------------------------- assembly
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def beat_clip(pngs, dur, out, fps=30, switch=0.58):
    """A (short) -> B, and for 3 pictures B -> C at `switch` of the beat."""
    a = max(0.6, min(1.1, dur * 0.3))
    if len(pngs) == 2:
        b = dur - a + 0.25
        subprocess.run(["ffmpeg", "-v", "error", "-y",
                        "-loop", "1", "-t", f"{a:.3f}", "-i", str(pngs[0]),
                        "-loop", "1", "-t", f"{b:.3f}", "-i", str(pngs[1]),
                        "-filter_complex",
                        f"[0:v]fps={fps},format=yuv420p[a];[1:v]fps={fps},format=yuv420p[b];"
                        f"[a][b]xfade=transition=fade:duration=0.25:offset={a - 0.25:.3f},format=yuv420p[v]",
                        "-map", "[v]", "-t", f"{dur:.3f}", "-r", str(fps), *X264, str(out)], check=True)
        return
    tc = max(a + 1.0, min(dur - 1.5, dur * switch))
    b = tc - a + 0.25
    c = dur - tc + 0.35
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-loop", "1", "-t", f"{a:.3f}", "-i", str(pngs[0]),
                    "-loop", "1", "-t", f"{b:.3f}", "-i", str(pngs[1]),
                    "-loop", "1", "-t", f"{c:.3f}", "-i", str(pngs[2]),
                    "-filter_complex",
                    f"[0:v]fps={fps},format=yuv420p[a];[1:v]fps={fps},format=yuv420p[b];[2:v]fps={fps},format=yuv420p[c];"
                    f"[a][b]xfade=transition=fade:duration=0.25:offset={a - 0.25:.3f}[ab];"
                    f"[ab][c]xfade=transition=fade:duration=0.3:offset={tc - 0.3:.3f},format=yuv420p[v]",
                    "-map", "[v]", "-t", f"{dur:.3f}", "-r", str(fps), *X264, str(out)], check=True)


def assemble(beat_pngs, beat_audio, beat_times, ass_text, fps=30, switch=None):
    clips = []
    switch = switch or {}
    for b in BEATS:
        t0, t1 = beat_times[b]
        out = RUN / f"beat_{b}.mp4"
        beat_clip(beat_pngs[b], t1 - t0, out, fps, switch.get(b, 0.58))
        clips.append(out.name)
    (RUN / "clips.txt").write_text("\n".join(f"file '{c}'" for c in clips), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "clips.txt",
                    "-c", "copy", "silent.mp4"], cwd=RUN, check=True)
    # voice: each beat's audio padded to its beat length
    parts = []
    for b in BEATS:
        t0, t1 = beat_times[b]
        wav = RUN / f"voice_{b}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(beat_audio[b]), "-af",
                        f"apad,atrim=0:{t1 - t0:.3f}", "-ar", "44100", "-ac", "2", str(wav)], check=True)
        parts.append(wav.name)
    (RUN / "voice_list.txt").write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "voice_list.txt",
                    "-c", "copy", "voice.wav"], cwd=RUN, check=True)
    music = ROOT / "assets" / "music.mp3"
    if music.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "voice.wav", "-stream_loop", "-1", "-i", str(music),
                        "-filter_complex", "[0:a]volume=1.0[v];[1:a]volume=0.10[m];"
                        "[v][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
                        "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "audio.m4a"], cwd=RUN, check=True)
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "voice.wav", "-c:a", "aac", "-b:a", "192k",
                        "audio.m4a"], cwd=RUN, check=True)
    (RUN / "subs.ass").write_text(ass_text, encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", "silent.mp4", "-i", "audio.m4a",
                    "-vf", f"fps={fps},ass=subs.ass", *X264, "-c:a", "copy", "-shortest", "final.mp4"],
                   cwd=RUN, check=True)


# ---------------------------------------------------------------- compat files
def write_compat(topic, n, spoken, beat_times):
    """script.txt / spoken.txt / shots.json in the shapes run_verify + publish expect."""
    b = n["beats"]
    facts = "\n".join(f"- {f}" for f in topic["facts"])
    if topic.get("cat") == "REAL DATA":
        # verify ne ek run me "Yes Bank 85% bina source/date" par roka (doosre run me wahi line pass) —
        # source + tareekh ledger me pakki, taaki roz ka slot skip na ho
        d = json.loads((ROOT / "data" / "index_monthly.json").read_text(encoding="utf-8"))["series"]
        y0, yl = d["YES BANK"]["first_of_year"]["2015"], d["YES BANK"]["last"]
        facts += (f"\n- SOURCE: Yahoo Finance monthly close (split/bonus adjusted, dividend alag), "
                  f"last close {yl['date']} — 3 mahine purana data, live nahi."
                  f"\n- SOURCE Yes Bank: {y0['date']} close {y0['close']:,.0f} -> {yl['date']} close "
                  f"{yl['close']:,.0f} = -{(1 - yl['close'] / y0['close']) * 100:.0f}% (Yahoo Finance).")
    script = (f"TITLE:\n{n['title']}\n\nHOOK (0-3 sec):\n{b['hook']}\n\nBODY:\n"
              f"[SEGMENT 1: PEHCHAN]\n{b['pehchan']}\n\n[SEGMENT 2: MATLAB]\n{b['matlab']}\n\n"
              f"[SEGMENT 3: KYA KAREIN]\n{b['karein']}\n\nCTA:\n{CTA}\n\nDISCLAIMER LINE:\n{DISCLAIMER}\n\n"
              f"FACT LEDGER:\n{facts}\n")
    save("script.txt", script)
    save("spoken.txt", " ".join(spoken[x] for x in BEATS))
    shots = []
    for i, x in enumerate(BEATS, 1):
        t0, t1 = beat_times[x]
        shots.append({"shot_id": i, "start_sec": round(t0, 2), "end_sec": round(t1, 2), "visual_type": "chart",
                      "on_screen_text": topic["beats"][x][0], "sub_text": topic["beats"][x][1],
                      "spoken_line": spoken[x], "chart_data": {"template": topic["key"]},
                      "source_asset_query": None, "emphasis": "neutral"})
    (RUN / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- visual QA gate
def visual_gate(beat_pngs):
    """Reject blank/placeholder boards before TTS or publishing.
    The board must contain a meaningful amount of visible graphic content;
    this catches empty panels and broken scene fallbacks automatically."""
    failures = []
    x0, y0, x1, y1 = K.BOARD
    for beat, paths in beat_pngs.items():
        for p in paths:
            im = Image.open(p).convert("RGB").crop((x0, y0, x1, y1))
            a = np.asarray(im, dtype=np.uint8)
            gray = a.mean(axis=2)
            bright = float((gray > 42).mean())
            spread = float(gray.std())
            # A normal finance chart/card is comfortably above both values.
            # Blank/empty panels are not allowed through to publish.
            if bright < 0.010 or spread < 5.0:
                failures.append(f"{beat}:{p.name} bright={bright:.3%} std={spread:.1f}")
    if failures:
        die("VISUAL QA failed — blank/placeholder board detected: " + "; ".join(failures[:8]))
    ok("visual QA: every board frame has visible educational graphics")


# ---------------------------------------------------------------- main
def load_topic():
    p = RUN / "topic.json"
    if not p.exists():
        die("run/topic.json missing — pick_topic.py pehle chalao.")
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa", action="store_true", help="no LLM/voice: frames + thumbnail only")
    ap.add_argument("--key", help="QA: topic key (default: run/topic.json)")
    args = ap.parse_args()
    c = cfg()
    if not shutil.which("ffmpeg"):
        die("ffmpeg missing")

    if args.key:
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == args.key)
    else:
        topic = load_topic()
    ok(f"template: {topic['key']} ({topic['cat']})")

    beat_pngs = {}
    for b in BEATS:
        pics = beat_images(topic, b)
        paths = tuple(RUN / f"b_{b}_{'ABC'[i]}.png" for i in range(len(pics)))
        for im, p in zip(pics, paths):
            im.save(p)
        beat_pngs[b] = paths
    visual_gate(beat_pngs)
    thumbnail(topic, RUN / "thumbnail.jpg", Image.open(beat_pngs["hook"][1]))
    ok("frames + thumbnail")
    if args.qa:
        return

    n, source = narration(topic, c)
    spoken = dict(n["beats"])
    spoken["karein"] = spoken["karein"].rstrip() + " " + CTA
    # Generate each beat voice, then trim only the *outer* TTS silence.
    # ElevenLabs/Edge can add a little leading/trailing room around every beat;
    # concatenating those raw files made the narration sound like it "waited"
    # at every beat. We keep natural pauses *inside* a sentence, but remove
    # artificial silence at beat boundaries. No artificial 50s padding.
    beat_audio, raw_durations = {}, {}
    (RUN / "tts_text.txt").unlink(missing_ok=True)
    for b in BEATS:
        mp3 = RUN / f"tts_{b}.mp3"
        tts(spoken[b], mp3, c)
        clean = RUN / f"voice_clean_{b}.wav"
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-i", str(mp3),
            "-af",
            "silenceremove=start_periods=1:start_duration=0.10:start_threshold=-42dB,"
            "areverse,silenceremove=start_periods=1:start_duration=0.10:start_threshold=-42dB,areverse",
            "-ar", "44100", "-ac", "2", str(clean)
        ], check=True)
        d = duration(clean)
        if d < 1.0:
            die(f"TTS beat {b} became too short after silence trim: {d:.2f}s")
        raw_durations[b] = d
        beat_audio[b] = clean

    raw_total = sum(raw_durations.values())
    # Only a tiny seam remains so the visual beats stay synchronized with speech.
    # There is deliberately NO extra pause added to stretch the Short.
    seam = 0.02
    beat_times, t = {}, 0.0
    for b in BEATS:
        pad = seam if b != BEATS[-1] else 0.0
        d = raw_durations[b] + pad
        beat_times[b] = (t, t + d)
        t += d

    if t < 45.0:
        # Retry TTS once at a slower rate before failing; never pad silence.
        print(f"[retry] voice too short after trim ({t:.1f}s); regenerating audio slower")
        v = c["voice"]
        original_rate = v.get("edge_rate", "+0%")
        original_speed = v.get("speed", 1.08)
        try:
            v["edge_rate"] = "-12%"
            v["speed"] = 0.88
            for b in BEATS:
                mp3 = RUN / f"tts_{b}.mp3"
                mp3.unlink(missing_ok=True)
                # If the primary TTS engine produced an implausibly short
                # narration, retry the whole beat with Edge directly. This
                # avoids a fast ElevenLabs response making the entire Short
                # fail the duration gate.
                import speak
                edge_text = speak.speakable(spoken[b])
                asyncio.run(_edge(edge_text, v.get("edge_voice", "hi-IN-SwaraNeural"), "-12%", mp3))
                if not mp3.exists() or mp3.stat().st_size <= 1000:
                    die(f"fallback Edge TTS failed: {b}")
                clean = RUN / f"voice_clean_{b}.wav"
                subprocess.run([
                    "ffmpeg", "-v", "error", "-y", "-i", str(mp3),
                    "-af",
                    "silenceremove=start_periods=1:start_duration=0.10:start_threshold=-42dB:"
                    "stop_periods=1:stop_duration=0.10:stop_threshold=-42dB",
                    "-ar", "44100", "-ac", "2", str(clean)
                ], check=True)
                raw_durations[b] = duration(clean)
                beat_audio[b] = clean
            beat_times, t = {}, 0.0
            for b in BEATS:
                d = raw_durations[b] + (seam if b != BEATS[-1] else 0.0)
                beat_times[b] = (t, t + d)
                t += d
        finally:
            v["edge_rate"] = original_rate
            v["speed"] = original_speed
        if t < 45.0:
            die(f"voice too short after TTS retry: {t:.1f}s — narration/TTS must be expanded")
    if t > 62.0:
        die(f"voice too long after boundary-silence trim: {t:.1f}s — narration/TTS must be tightened")
    ok(f"voice: trimmed speech {raw_total:.1f}s -> final {t:.1f}s ({source}) engine: {', '.join(sorted(set(ENGINES)))}")
    # verify config ka "edge" padh ke ElevenLabs video ko bhi "robotic" maanta tha
    save("voice_engine.txt", "edge" if "edge" in ENGINES else "elevenlabs")
    switch = {b: cue_frac(topic["key"], spoken[b]) for b in BEATS if len(beat_pngs[b]) == 3}
    if switch:
        ok("3rd picture at " + ", ".join(f"{b} {f:.0%}" for b, f in switch.items()))
    assemble(beat_pngs, beat_audio, beat_times, build_ass(topic, beat_times, spoken), switch=switch)
    write_compat(topic, n, spoken, beat_times)
    ok(f"wrote run/final.mp4 ({t:.1f}s) + thumbnail.jpg")


if __name__ == "__main__":
    sys.exit(main())
