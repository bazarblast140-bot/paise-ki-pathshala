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

import board_kit as K
import board_scenes as S
from common import RUN, ROOT, cfg, claude_json, die, ok, save

BEATS = S.BEATS
STEP_CHIP = {"hook": "HOOK", "pehchan": "1  PEHCHAN", "matlab": "2  MATLAB", "karein": "3  KYA KAREIN"}
CTA = "ऐसे ही रोज़ एक ट्रेडिंग फैक्ट के लिए फ़ॉलो करो।"
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
def beat_images(topic, beat):
    img = K.background()
    K.chips(img, topic["cat"], STEP_CHIP[beat])
    return S.render_beat(topic["key"], beat, img)


def thumbnail(topic, out_path, hook_img=None):
    """Tagda hook thumbnail: red ground, huge 2-line Hindi hook, the topic's own
    board picture, big host, sticker. Hindi via libass."""
    W, H = K.W, K.H
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(120 - 70 * t), int(10 + 4 * t), int(16 + 6 * t)))
    if hook_img is None:
        _, hook_img = beat_images(topic, "hook")
    x0, y0, x1, y1 = K.BOARD
    board = hook_img.crop((x0, y0, x1, y1))
    bw = 1000
    board = board.resize((bw, int(board.height * bw / board.width)))
    img.paste(board, ((W - bw) // 2, 820))
    try:
        import sketch
        host = sketch.load_host_pose("warn" if topic["sticker"] in ("चेतावनी", "सावधान") else "point", height=620)
        img.paste(host, (20, H - host.height - 120), host)
    except Exception as e:  # noqa: BLE001
        print(f"[thumb] host skipped: {e}")
    d = ImageDraw.Draw(img)
    st = topic["sticker"]
    s_size = fit_size(st, 330, 72, 40)
    d.rounded_rectangle([620, 1560, 1040, 1690], 32, fill=K.RED)
    base = out_path.with_name(out_path.stem + "_base.png")
    img.save(base)
    t1, t2 = topic["thumb"]
    ev = [K.ass_text(0, 5, 540, 150, fit_size(t1, 940, 150), K.WHITE, t1, 8, fade=False),
          K.ass_text(0, 5, 540, 350, fit_size(t2, 940, 170), K.YEL, t2, 8, fade=False),
          K.ass_text(0, 5, 830, 1582, s_size, K.WHITE, st, 8, fade=False)]
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
    if not 55 <= words <= 120:
        fails.append(f"{words} words (55-120 chahiye)")
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
    return fails


NARR_SYSTEM = """You write the SPOKEN Hindi narration for a 40-50 second vertical Short for Indian traders
(retail to pro). The video template is fixed — four beats, each showing a drawing and an on-screen
headline + sub line. Your narration for each beat must explain exactly what that beat's on-screen
text says, in simple spoken Hindi (Devanagari). Trading words stay as traders say them in Roman
(RSI, MACD, VWAP, stop loss, SL, candle, wick, breakout, option, premium, theta, delta, OI).

Return ONLY JSON:
{"title": "<YouTube title, max 70 chars, Hindi + key English words, the hook itself>",
 "beats": {"hook": "...", "pehchan": "...", "matlab": "...", "karein": "..."}}

Rules:
- hook: 1 sentence, the surprising fact or costly mistake. No greeting.
- pehchan / matlab / karein: 1-2 short sentences each (under 12 words per sentence).
- Total 60-100 words.
- Use ONLY numbers that appear in the topic data given to you. Never invent prices, dates, returns.
- Not a quiz. Do not ask the viewer to pick options.
- SEBI rules: never tell anyone to buy/sell/hold a specific stock, fund or product; no targets,
  no tips; never promise returns. Patterns/indicators are probabilities, not sure signals.
- Do not add a CTA or disclaimer — the template adds them."""


def narration(topic, c):
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
            return n, "llm"
        last = "; ".join(fails)
        print(f"[retry] narration attempt {attempt}: {last}")
    print("[warn] LLM narration 3 baar fail — on-screen text se narration bana raha hoon")
    beats = {b: f"{h}। {s}।" for b, (h, s) in topic["beats"].items()}
    return {"title": f"{topic['thumb'][0]} {topic['thumb'][1]}", "beats": beats}, "fallback"


# ---------------------------------------------------------------- voice
async def _edge(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


def tts(text, path, c):
    v = c["voice"]
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
def chunks(text, n=4):
    words = text.split()
    return [" ".join(words[i:i + n]) for i in range(0, len(words), n)]


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
    return K.ASS_HEAD + "\n".join(ev) + "\n"


# ---------------------------------------------------------------- assembly
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def beat_clip(a_png, b_png, dur, out, fps=30):
    a = max(0.6, min(1.1, dur * 0.3))
    b = dur - a + 0.25
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-loop", "1", "-t", f"{a:.3f}", "-i", str(a_png),
                    "-loop", "1", "-t", f"{b:.3f}", "-i", str(b_png),
                    "-filter_complex",
                    f"[0:v]fps={fps},format=yuv420p[a];[1:v]fps={fps},format=yuv420p[b];"
                    f"[a][b]xfade=transition=fade:duration=0.25:offset={a - 0.25:.3f},format=yuv420p[v]",
                    "-map", "[v]", "-t", f"{dur:.3f}", "-r", str(fps), *X264, str(out)], check=True)


def assemble(beat_pngs, beat_audio, beat_times, ass_text, fps=30):
    clips = []
    for b in BEATS:
        t0, t1 = beat_times[b]
        out = RUN / f"beat_{b}.mp4"
        beat_clip(beat_pngs[b][0], beat_pngs[b][1], t1 - t0, out, fps)
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
        A, B = beat_images(topic, b)
        pa, pb = RUN / f"b_{b}_A.png", RUN / f"b_{b}_B.png"
        A.save(pa); B.save(pb)
        beat_pngs[b] = (pa, pb)
    thumbnail(topic, RUN / "thumbnail.jpg", Image.open(beat_pngs["hook"][1]))
    ok("frames + thumbnail")
    if args.qa:
        return

    n, source = narration(topic, c)
    spoken = dict(n["beats"])
    spoken["karein"] = spoken["karein"].rstrip() + " " + CTA
    beat_audio, beat_times, t = {}, {}, 0.0
    for b in BEATS:
        mp3 = RUN / f"tts_{b}.mp3"
        tts(spoken[b], mp3, c)
        d = duration(mp3) + (0.25 if b != "karein" else 0.6)
        d = max(d, 2.4)
        beat_audio[b] = mp3
        beat_times[b] = (t, t + d)
        t += d
    ok(f"voice: {t:.1f}s ({source})")
    assemble(beat_pngs, beat_audio, beat_times, build_ass(topic, beat_times, spoken))
    write_compat(topic, n, spoken, beat_times)
    ok(f"wrote run/final.mp4 ({t:.1f}s) + thumbnail.jpg")


if __name__ == "__main__":
    sys.exit(main())
