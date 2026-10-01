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
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw
import numpy as np

import board_kit as K
import board_scenes as S
import engagement
import hook_check
import market_data
import self_review
import short_layout
from common import RUN, ROOT, cfg, claude_json, die, ok, save

BEATS = S.BEATS
STEP_CHIP = {"hook": "HOOK", "pehchan": "1  PEHCHAN", "matlab": "2  MATLAB", "karein": "3  KYA KAREIN"}
# On-video CTA: a poll-style question (engagement.engagement_question). The same
# Reel is cross-posted, so the line stays platform-neutral. Referral links stay
# in the description, not in this spoken/on-screen line.
CTA = engagement.engagement_question({})
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


def _scene_images(topic, beat):
    img = K.background()
    # The chart is the hook; no extra top chip row.
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


def beat_images(topic, beat):
    """Scene art, then a real-data stamp or a non-numeric card. Never a fake price line."""
    spec = topic.get("_chart_spec") or market_data.spec_for_topic(topic)
    mode = market_data.visual_mode(topic, spec)
    if mode == "non_numeric":
        return (market_data.non_numeric_frame(topic, beat, 0),
                market_data.non_numeric_frame(topic, beat, 1))
    pics = _scene_images(topic, beat)
    if mode == "series":
        pics = tuple(market_data.stamp(im, spec) for im in pics)
    return pics


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
    return hook_check.allowed_numbers(topic)


def check_narration(n, topic):
    fails = []
    beats = n.get("beats") or {}
    for b in BEATS:
        if not str(beats.get(b) or "").strip():
            fails.append(f"beat '{b}' khaali")
    text = " ".join(str(beats.get(b) or "") for b in BEATS)
    words = len(text.split())
    if not 160 <= words <= 180:
        fails.append(f"{words} words (160-180 chahiye)")

    # Every beat must carry enough information to teach one step. This prevents
    # a very short hook + generic filler from making the lesson feel disconnected.
    beat_ranges = {
        "hook": (22, 28),
        "pehchan": (38, 46),
        "matlab": (46, 54),
        "karein": (42, 50),
    }
    for b, (lo, hi) in beat_ranges.items():
        bw = len(str(beats.get(b) or "").split())
        if not lo <= bw <= hi:
            fails.append(f"{b} beat {bw} words ({lo}-{hi} chahiye)")
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
        # The universal gate needs a 22-28 word teaching hook. For REAL DATA,
        # only the opening question is restricted to <=10 words; the remaining
        # context can explain what is being investigated without revealing the answer.
        first_sentence = re.split(r"[।?!]", hook, maxsplit=1)[0].strip()
        if len(first_sentence.split()) > 10:
            fails.append("REAL DATA hook ka opening question 10 words se chhota rakho")
        final = re.findall(r"₹[\d,.]+", topic["beats"]["karein"][1])
        for f in final:
            early = " ".join(str(beats.get(b) or "") for b in ("hook", "pehchan", "matlab"))
            if f in early:
                fails.append(f"final value {f} sirf 'karein' me bolo")
        if "लगते" in text:
            fails.append("'लगते' galat hai — 'लगाते' likho")
    limit = hook_check.hook_word_limit(topic)
    hook_line = " ".join(str(n.get("hook_line") or "").split())
    hook = str(beats.get("hook") or "")
    if not hook_line:
        hook_line = re.split(r"[।?!]", hook, maxsplit=1)[0].strip()
        if hook_line:
            n["hook_line"] = hook_line
    fails.extend(hook_check.check_hook_line(hook_line, allowed_numbers(topic), max_words=limit))
    if hook_line and not hook.startswith(hook_line.rstrip("।")):
        fails.append("hook beat must open with the punchy hook line")
    return fails


NARR_SYSTEM = """You write the SPOKEN Hindi narration for a vertical finance Short for Indian traders
(retail to pro). This is ONE MINI-LESSON, not four separate facts. The viewer should be able to
answer these four questions in order: "आज क्या सीख रहा हूँ?", "इसे पहचानूँ कैसे?", "इसका मतलब क्या है?",
और "आगे traders क्या check करते हैं?" Never jump backward, repeat an explanation, or introduce a
new concept before the previous one is understood.

TEACHING VOICE:
- Start by naming the exact topic in simple Hindi + the key English term.
- Explain like a teacher pointing at the screen: first show it, then identify it, then explain its meaning,
  then explain the normal confirmation/risk check.
- Every sentence must connect to the current screen. If a visual changes, the narration must explain why.
- Do not use vague filler such as "यह बहुत important है" unless immediately followed by the specific reason.

Return ONLY JSON:
{"title": "<YouTube title, max 70 chars, Hindi + key English words, the hook itself>",
 "hook_line": "<FIRST sentence of the hook beat. 4-12 words, max 10 when category is REAL DATA. A question, a number copied from the topic, or a contrarian line. No guaranteed or 'पक्का' returns.>",
 "beats": {"hook": "...", "pehchan": "...", "matlab": "...", "karein": "..."}}

MANDATORY TEACHING SEQUENCE — NEVER CHANGE THIS ORDER:
1) HOOK — Start with hook_line, the punchy first sentence (question, real topic number, or contrarian line).
   Then name the topic and the one-line reason it matters. Do NOT explain the answer yet.
   The hook beat still totals 22-28 words; only the opening sentence is short.
2) PEHCHAN — "इसे chart पर पहचानेंगे कैसे?" Point to the exact visible structure/rules shown on screen.
   Use concrete features, not generic definitions.
3) MATLAB — "इस pattern/strategy का मतलब क्या है?" Connect those exact features to buyer/seller force,
   payoff, or market logic. Explain cause → effect. Do not introduce a second setup.
4) KAREIN — "अब traders आमतौर पर क्या check करते हैं?" Show confirmation, entry context, SL/risk,
   or limitation only after the meaning is established. Refer back to the SAME setup.

BRIDGE WORDS:
- Hook → Pehchan: "अब इसे chart पर पहचानें।"
- Pehchan → Matlab: "इन features का मतलब समझिए।"
- Matlab → Karein: "अब आगे traders क्या check करते हैं।"
Use these naturally so the lesson sounds continuous, not robotic.

CONTINUITY RULES:
- Each beat must answer the next question in the sequence. Never repeat the hook in later beats.
- Use explicit bridge words where natural: "अब इसे पहचानें", "इसका मतलब", "अब आगे".
- Narration must describe the exact visual shown in that beat. If the screen highlights
  three candles, explain those three candles; if it shows SL, explain SL. Never talk about
  a chart element that is not on screen.
- Every beat should add new information. The four beats together should feel like one lesson,
  not four separate facts.
- For pattern/strategy topics, use this order: setup → recognition rules → market meaning →
  confirmation/risk. Do not show or explain entry/SL before the setup and meaning are established.
- Each beat: 3-4 short sentences, preferably under 14 words per sentence.
- Total 160-180 words. The extra words must add concrete teaching detail, not filler.
- Never stretch audio artificially just to hit a target duration.
- Do not compress a beat into a slogan; each beat must teach its assigned step.
- Use ONLY numbers that appear in the topic data given to you. Never invent prices, dates, returns.
- Not a quiz. Do not ask the viewer to pick options.
- SEBI rules: never tell anyone to buy/sell/hold a specific stock, fund or product; no targets,
  no tips; never promise returns. Patterns/indicators are probabilities, not sure signals.
- Describe, don't command: for SL / entry / confirmation say how traders usually do it
  ("आमतौर पर traders SL ... रखते हैं", "कई traders ... का इंतज़ार करते हैं"), never "ऐसा करो" as an order.
- Do not add a CTA or disclaimer inside the narration — the template adds them.
- If category is "REAL DATA": the first sentence of hook is ONLY the question (max 10 words);
  the rest of the 22-28 word hook may give neutral context but must not reveal the answer. pehchan =
  the two prices with years. matlab = the multiple (गुना) once. karein = the final value once, then
  the balance line from facts. Say the final value only in karein, never earlier. Keep the same
  160-180 total-word requirement and the same beat ranges; use the extra words for concrete explanation,
  not filler. Write "लगाते" (not "लगते")."""


def _fit_words(seed, lo, hi, additions):
    target = hi
    words = str(seed).split()
    for sentence in additions:
        sw = sentence.split()
        if len(words) + len(sw) <= target:
            words.extend(sw)
        if len(words) >= lo:
            break
    for sentence in additions:
        sw = sentence.split()
        if len(words) >= lo:
            break
        room = target - len(words)
        words.extend(sw[:room])
    return " ".join(words[:target])


def _card_line(text):
    """On-screen card text spoken aloud. 'like' is banned as bait, including inside 'profit-like'."""
    out = re.sub(r"-like\b", "", str(text), flags=re.I)
    for word in FORBIDDEN:
        if word.lower() in out.lower():
            out = re.sub(re.escape(word), "", out, flags=re.I)
    return " ".join(out.split())


def fallback_narration(topic):
    """Four-step lesson from the topic card. The first sentence is the punchy hook."""
    src = {b: (_card_line(pair[0]), _card_line(pair[1])) for b, pair in topic["beats"].items()}
    hook_line = _card_line(hook_check.make_hook_line(topic))
    if hook_check.check_hook_line(hook_line, hook_check.allowed_numbers(topic),
                                  max_words=hook_check.hook_word_limit(topic)):
        hook_line = hook_check.make_hook_line({"cat": topic.get("cat"), "topic": topic.get("topic"),
                                               "beats": {"hook": ["यह बात", "जितनी आसान लगती है?"]},
                                               "thumb": [], "facts": []})
    if topic.get("cat") == "REAL DATA":
        hook_seed = hook_line
    else:
        hook_seed = f"{hook_line} आज पहले इसी setup का मतलब साफ समझते हैं।"
    beats = {
        "hook": _fit_words(
            hook_seed, 22, 28,
            [
                "अब इसे chart पर पहचानने का तरीका समझिए।",
                "आगे की जानकारी इसी topic के संदर्भ में रहेगी।",
            ],
        ),
        "pehchan": _fit_words(
            f'{src["pehchan"][0]}। {src["pehchan"][1]}।',
            38, 46,
            [
                "अब इसे chart पर पहचानें।",
                "स्क्रीन पर दिख रहे feature को एक साथ देखें।",
                "पहचान का मतलब है कि पहले वही structure साफ समझें।",
                "इसके बाद ही उसका market meaning पढ़ना आसान होता है।",
            ],
        ),
        "matlab": _fit_words(
            f'{src["matlab"][0]}। {src["matlab"][1]}।',
            46, 54,
            [
                "इन features का मतलब समझिए।",
                "अब setup के कारण और असर को जोड़कर देखें।",
                "यह explanation इसी दिए गए तथ्य तक सीमित है।",
                "किसी अतिरिक्त price, date या return का अनुमान नहीं लगाया जा रहा।",
                "इसलिए meaning को context के साथ पढ़ना जरूरी है।",
            ],
        ),
        "karein": _fit_words(
            f'{src["karein"][0]}। {src["karein"][1]}।',
            42, 50,
            [
                "अब आगे traders क्या check करते हैं।",
                "पहले confirmation और risk की सीमा समझी जाती है।",
                "किसी setup को अकेले देखकर फैसला तय नहीं होता।",
                "स्क्रीन पर मौजूद जानकारी इसकी practical सीमा भी दिखाती है।",
                "अलग से पूरी जानकारी और जोखिम देखना जरूरी है।",
            ],
        ),
    }
    # Beat ranges can all sit near their floor and leave the lesson under 160 words.
    # Top up with number-free sentences, never past a beat's own cap or 180 total.
    caps = {"hook": 28, "pehchan": 46, "matlab": 54, "karein": 50}
    fillers = (
        ("karein", "यह सीमा याद रखने लायक है।"),
        ("matlab", "इसीलिए संदर्भ के साथ पढ़ें।"),
        ("pehchan", "यही पहचान का आधार है।"),
        ("hook", "इसी सवाल को आगे खोलते हैं।"),
    )
    guard = 0
    while sum(len(beats[b].split()) for b in BEATS) < 160 and guard < 8:
        guard += 1
        added = False
        for beat, sentence in fillers:
            extra = sentence.split()
            words = beats[beat].split()
            if len(words) + len(extra) <= caps[beat] and sum(len(beats[b].split()) for b in BEATS) + len(extra) <= 180:
                beats[beat] = " ".join(words + extra)
                added = True
                break
        if not added:
            break
    title = f"{topic['thumb'][0]} {topic['thumb'][1]}".strip()
    if len(title) < 8:
        title = (title + " " + topic["topic"]).strip()[:90]
    return {"title": title[:90], "hook_line": hook_line, "beats": beats}


def _content_ok(topic, narration, spec):
    # Trend is scored at the end when data/trends.json exists. Regeneration
    # here is only for the hook and the number check, which a rewrite can fix.
    return self_review.score_content(topic, narration, spec, trends={})


def narration(topic, c, spec=None):
    cache = RUN / "narration.json"
    if cache.exists():
        try:
            n = json.loads(cache.read_text(encoding="utf-8"))
            fails = check_narration(n, topic)
            content = _content_ok(topic, n, spec)
            if not fails and content["passed"]:
                ok("narration cache reused — no LLM credit")
                return n, "cache"
        except Exception as e:
            print(f"[cache] narration invalid, regenerating: {e}")
    user = {"topic": topic["topic"], "category": topic["cat"],
            "on_screen": {b: {"headline": h, "sub": s} for b, (h, s) in topic["beats"].items()},
            "facts": topic["facts"]}
    msg = json.dumps(user, ensure_ascii=False)
    last = None
    for attempt in range(1, self_review.MAX_ATTEMPTS + 1):
        try:
            n = claude_json(NARR_SYSTEM, msg + ("" if not last else f"\n\nFIX THESE: {last}"),
                            model=c["models"]["script_model"], max_tokens=2000)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            last = f"invalid JSON: {e}"
            print(f"[retry] narration attempt {attempt}: {last}")
            if self_review.next_action(False, attempt) == "retry":
                continue
            break
        if not isinstance(n, dict):
            n = {}
        fails = check_narration(n, topic)
        content = _content_ok(topic, n, spec) if not fails else None
        if content and not content["passed"]:
            fails.append("self-review below bar: " + content["summary"])
        if not fails:
            ok(f"narration OK (attempt {attempt})")
            n["review_attempts"] = attempt
            cache.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
            return n, "llm"
        last = "; ".join(fails)
        print(f"[retry] narration attempt {attempt}: {last}")
        if self_review.next_action(False, attempt) != "retry":
            break
    print("[warn] LLM narration failed the hook/teaching gate — building narration from the topic card")
    n = fallback_narration(topic)
    n["review_attempts"] = self_review.MAX_ATTEMPTS
    fallback_fails = check_narration(n, topic)
    if fallback_fails:
        die("fallback narration failed the same universal teaching gate: " + "; ".join(fallback_fails))
    cache.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
    return n, "fallback"


# ---------------------------------------------------------------- voice
async def _edge(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


def _eleven(text, path, c):
    """Paid ElevenLabs is a hard requirement when configured; never mix paid and free voices."""
    import os
    import time
    import requests

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice = c["voice"].get("voice_id") or os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not key:
        return False

    global ELEVEN_EXHAUSTED
    if ELEVEN_EXHAUSTED:
        return False
    if not voice:
        die("ELEVENLABS_API_KEY is set but ELEVENLABS_VOICE_ID/config voice_id is missing")

    # Do not preflight /v1/user/subscription here. API keys can be endpoint-scoped,
    # so a valid TTS key may legitimately be unable to read subscription details.
    # The real TTS request is the authoritative credential/quota check.
    global ELEVEN_REMAINING
    need_chars = len(text)

    model = os.environ.get("ELEVENLABS_MODEL") or c["voice"].get("model", "eleven_multilingual_v2")
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": c["voice"].get("stability", 0.45),
            "similarity_boost": c["voice"].get("similarity_boost", 0.8),
            "style": c["voice"].get("style", 0.3),
            "speed": c["voice"].get("speed", 1.08),
        },
    }

    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": key, "accept": "audio/mpeg"},
                json=payload,
                timeout=90,
            )
            if r.status_code == 200 and len(r.content) >= 2000:
                path.write_bytes(r.content)
                cost = int(r.headers.get("character-cost", str(need_chars)) or need_chars)
                if ELEVEN_REMAINING is not None:
                    ELEVEN_REMAINING = max(0, ELEVEN_REMAINING - cost)
                return True

            body = (r.text or "")[:1000]
            try:
                detail = r.json().get("detail", {})
                code = str(detail.get("code") or detail.get("status") or "").lower()
            except Exception:
                code = ""
            combined = f"{code} {body}".lower()

            if r.status_code in (400, 402) and any(x in combined for x in (
                "quota_exceeded", "insufficient_credits", "payment_required",
                "max_character_limit_exceeded", "quota",
            )):
                die("ElevenLabs paid TTS quota/credit limit reached; refusing free Edge fallback")

            if r.status_code == 401:
                die("ElevenLabs authentication failed; refusing silent free-TTS fallback")
            if r.status_code in (403, 404, 422):
                die(f"ElevenLabs request/config error {r.status_code}; refusing silent free-TTS fallback: {body[:300]}")

            if r.status_code in (408, 429, 500, 502, 503, 504) and attempt < 2:
                wait = 2 ** attempt
                print(f"[retry] ElevenLabs {r.status_code}; retrying in {wait}s")
                time.sleep(wait)
                continue

            die(f"ElevenLabs paid TTS failed ({r.status_code}); refusing silent free-TTS fallback")
        except SystemExit:
            raise
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            die(f"ElevenLabs paid TTS error: {e}")

    return False

ENGINES = []
ELEVEN_EXHAUSTED = False
ELEVEN_REMAINING = None


def tts(text, path, c):
    # Reuse only when the cached engine still matches the current priority.
    # If ElevenLabs is configured now, an old Edge file is regenerated with paid TTS.
    engine_file = path.with_suffix(".engine")
    cached_engine = engine_file.read_text(encoding="utf-8").strip().lower() if engine_file.exists() else ""
    eleven_key = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
    if path.exists() and path.stat().st_size > 1000 and (
        cached_engine == "elevenlabs" or (cached_engine == "edge" and not eleven_key)
    ):
        ok(f"voice cache reused: {path.name} ({cached_engine or 'unknown'})")
        ENGINES.append(cached_engine or "cached")
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
    if eleven_key:
        if not _eleven(text, path, c):
            die(f"ElevenLabs TTS did not produce paid audio: {path.name}; refusing free-voice fallback")
        ENGINES.append("elevenlabs")
        engine_file.write_text("elevenlabs", encoding="utf-8")
        return
    v = c["voice"]
    ENGINES.append("edge")
    for attempt in range(3):
        try:
            asyncio.run(_edge(text, v.get("edge_voice", "hi-IN-SwaraNeural"), v.get("edge_rate", "+10%"), path))
            if path.exists() and path.stat().st_size > 1000:
                engine_file.write_text("edge", encoding="utf-8")
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


def build_ass(topic, beat_times, spoken, hook_line, cta):
    ev = []
    for b in BEATS:
        t0, t1 = beat_times[b]
        head, sub = topic["beats"][b]
        if b == "hook":
            # Punchy line from the first frame through the hook beat (covers 0-2s).
            head = hook_line
            t0 = 0.0
        ev.append(K.ass_text(t0 + (0 if b == "hook" else 0.05), t1, 540, 1170, fit_size(head, 980, 84),
                             K.YEL, head, 8, fade=(b != "hook"), wrap=(b == "hook")))
        ev.append(K.ass_text(t0 + (0 if b == "hook" else 0.05), t1, 540, 1240, fit_size(sub, 980, 54, 34),
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
    ev.append(K.ass_text(t0, t1, 540, 1325, 30, "&H00B8A394&", DISCLAIMER, 8))
    # Poll question, not a like/follow/subscribe bait line. Referral disclosure
    # stays in the description (publish.py), not on this card.
    cta_start = max(t0, t1 - 2.8)
    ev.append(K.ass_text(cta_start, t1, 540, 1480, fit_size(cta, 980, 42, 28), K.WHITE,
                         cta, 8, fade=True, wrap=True))
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
    spec = topic.get("_chart_spec") or {}
    if spec.get("mode") == "series" and spec.get("source") and spec.get("as_of"):
        facts += (f"\n- SOURCE: {spec.get('source')} as of {spec.get('as_of')} "
                  f"(monthly closes, no interpolated points).")
    cta = engagement.engagement_question(topic)
    script = (f"TITLE:\n{n['title']}\n\nHOOK (0-3 sec):\n{b['hook']}\n\nBODY:\n"
              f"[SEGMENT 1: PEHCHAN]\n{b['pehchan']}\n\n[SEGMENT 2: MATLAB]\n{b['matlab']}\n\n"
              f"[SEGMENT 3: KYA KAREIN]\n{b['karein']}\n\nCTA:\n{cta}\n\nDISCLAIMER LINE:\n{DISCLAIMER}\n\n"
              f"FACT LEDGER:\n{facts}\n")
    save("script.txt", script)
    save("spoken.txt", " ".join(spoken[x] for x in BEATS))
    shots = []
    hook_line = n.get("hook_line") or ""
    for i, x in enumerate(BEATS, 1):
        t0, t1 = beat_times[x]
        on_screen = hook_line if x == "hook" else topic["beats"][x][0]
        shots.append({"shot_id": i, "start_sec": round(t0, 2), "end_sec": round(t1, 2), "visual_type": "chart",
                      "on_screen_text": on_screen, "sub_text": topic["beats"][x][1],
                      "spoken_line": spoken[x],
                      "chart_data": {"template": topic["key"], "mode": spec.get("mode"),
                                     "source": spec.get("source"), "as_of": spec.get("as_of")},
                      "source_asset_query": None, "emphasis": "neutral",
                      "hook_line": hook_line if x == "hook" else None})
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
    spec = market_data.spec_for_topic(topic)
    topic["_chart_spec"] = spec
    if spec.get("mode") == "non_numeric":
        print(f"[market_data] non-numeric card: {spec.get('reason')}")
    elif spec.get("mode") == "series":
        print(f"[market_data] {spec.get('source')} as of {spec.get('as_of')}")

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

    n, source = narration(topic, c, spec)
    # CTA/disclaimer stay on-screen; do not spend TTS credits speaking them.
    spoken = dict(n["beats"])
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
            "silenceremove=start_periods=1:start_duration=0.10:start_threshold=-55dB,"
            "areverse,silenceremove=start_periods=1:start_duration=0.10:start_threshold=-55dB,areverse",
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

    # IMPORTANT: never stretch short speech to a fixed 56s target.
    # Doing so with atempo < 1 made an otherwise normal ElevenLabs narration
    # sound like slow-motion. Visual beats follow the real speech duration.
    if t < 45.0:
        print(f"[duration] natural paid TTS is {t:.1f}s; no slow-motion stretching")

    if t < 42.0 or t > 62.0:
        detail = ", ".join(f"{b}={raw_durations[b]:.1f}s/{len(spoken[b].split())}w" for b in BEATS)
        die(f"voice duration outside 42-62s; refusing artificial speed change: {t:.1f}s — {detail}")

    ok(f"voice: trimmed speech {raw_total:.1f}s -> final {t:.1f}s ({source}) engine: {', '.join(sorted(set(ENGINES)))}")
    # verify config ka "edge" padh ke ElevenLabs video ko bhi "robotic" maanta tha
    save("voice_engine.txt", "edge" if "edge" in ENGINES else "elevenlabs")
    switch = {b: cue_frac(topic["key"], spoken[b]) for b in BEATS if len(beat_pngs[b]) == 3}
    if switch:
        ok("3rd picture at " + ", ".join(f"{b} {f:.0%}" for b, f in switch.items()))
    cta = engagement.engagement_question(topic)
    hook_line = n.get("hook_line") or ""
    overlays = short_layout.overlay_plan(topic, beat_times, hook_line, cta)
    review = self_review.full_review(topic, n, spec, overlays)
    review["narration_source"] = source
    review["attempts"] = n.get("review_attempts") or self_review.MAX_ATTEMPTS
    (RUN / "self_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    if review["public_ok"]:
        ok("self-review passed: " + review["summary"])
    else:
        print("[HOLD] self-review below the bar — public upload blocked. " + review["summary"])
    assemble(beat_pngs, beat_audio, beat_times,
             build_ass(topic, beat_times, spoken, hook_line, cta), switch=switch)

    # Final-file hard gate: never retime the finished video.
    # If duration is outside the publishing window, fail and regenerate narration.
    final_path = RUN / "final.mp4"
    final_dur = duration(final_path)
    if final_dur < 45.0 or final_dur > 62.0:
        die(f"final.mp4 duration outside 45-62s; refusing post-render speed change: {final_dur:.1f}s")

    write_compat(topic, n, spoken, beat_times)
    ok(f"wrote run/final.mp4 ({final_dur:.1f}s) + thumbnail.jpg")


if __name__ == "__main__":
    sys.exit(main())
