"""Chart Board Short: topic -> narration (LLM, gated) -> voice per beat -> video + hook thumbnail.

Same template for every topic:
  HOOK -> 1 PEHCHAN -> 2 MATLAB -> 3 KYA KAREIN
Pictures come from board_scenes (code, not the LLM). On-screen Hindi comes from
topic_bank.json. The LLM writes only the spoken lines, and may use only numbers
that already appear in the topic. If that narration misses the hook/teaching
gate, the topic-card template is not spoken and not uploaded — the slot stays empty.

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

import bed_audio
import board_kit as K
import board_scenes as S
import credit_log
import eleven_voice
import engagement
import hook_check
import hook_motion
import hook_sfx
import market_data
import scene_frames
import scene_pace
import scene_transition
import self_review
import short_layout
import slot_guard
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
    if spec.get("atr"):
        frames, _flags = scene_frames.frames_for(topic, beat, 3, spec)
        return tuple(frames)
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
    # The ATR short is spoken at a calm rate, so its script is shorter.
    # Every other topic still has to land in the 160-180 teaching band.
    if topic.get("_atr"):
        if not 75 <= words <= 140:
            fails.append(f"{words} words (75-140 for the calm ATR short)")
        beat_ranges = {
            "hook": (18, 26),
            "pehchan": (16, 30),
            "matlab": (14, 32),
            "karein": (22, 36),
        }
    else:
        if not 160 <= words <= 180:
            fails.append(f"{words} words (160-180 chahiye)")
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
- Adult trader talking to another adult. Calm, direct Hinglish. No cheerleading, no "दोस्तों",
  no baby-talk, no hype.
- One lesson, in this order, mapped onto the four beats:
  hook = the problem (do not give the answer yet);
  pehchan = the rule, with the number that is on the chart;
  matlab = one worked example using the chart's own prices;
  karein = one common mistake, then one takeaway line.
  The on-screen poll is the CTA. Do not speak a CTA.
- Every sentence points at what is on screen in that beat: a number, a level, or a step.
  No line like "यह बहुत important है" unless the next words name the number or the level.
- Explain like a teacher pointing at the screen: first show it, then identify it, then explain its meaning,
  then explain the normal confirmation/risk check.
- Every sentence must connect to the current screen. If a visual changes, the narration must explain why.

Return ONLY JSON:
{"title": "<YouTube title, max 70 chars, Hindi + key English words, the hook itself>",
 "hook_line": "<FIRST sentence of the hook beat. 4-12 words, max 10 when category is REAL DATA. Prefer a real number copied from the topic facts, or a curiosity-gap question that adds no new number. No guaranteed or 'पक्का' returns.>",
 "beats": {"hook": "...", "pehchan": "...", "matlab": "...", "karein": "..."}}

MANDATORY TEACHING SEQUENCE — NEVER CHANGE THIS ORDER:
1) HOOK — Start with hook_line, the punchy first sentence (a number copied from the topic facts, or a curiosity-gap question with no new number).
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


def _ensure_concrete(beats, hook_line):
    """A vague beat fails the craft score. Add or swap one step-word, never a new number."""
    caps = {"hook": 28, "pehchan": 46, "matlab": 54, "karein": 50}
    prefix = hook_line.rstrip("।").split()
    for beat in BEATS:
        if scene_pace.segment_is_concrete(beats.get(beat)):
            continue
        words = str(beats.get(beat) or "").split()
        total = sum(len(str(beats.get(b) or "").split()) for b in BEATS)
        if len(words) < caps[beat] and total < 180:
            beats[beat] = " ".join(words + ["उदाहरण"])
            continue
        if beat == "hook" and len(words) <= len(prefix):
            continue
        if words:
            words[-1] = "उदाहरण"
            beats[beat] = " ".join(words)
    return beats


def atr_narration(topic):
    """Calm ATR lesson sized for a normal speaking rate, not a sped-up voice.

    Every figure is the daily true-range lesson. Devanagari abbreviations stay
    one spoken word. The written script is short so Edge at +0% lands near a minute.
    """
    lesson = topic["_atr"]
    name = lesson["name_hi"]
    hook_line = "तंग एसएल बार-बार कटता क्यों है?"
    beats = {
        "hook": (
            f"{hook_line} भाव की सामान्य हलचल तंग रेखा को काट देती है। "
            f"एटीआर इसी हलचल को नापता है।"
        ),
        "pehchan": (
            f"नियम यह है। एटीआर पिछले {lesson['period']} कैंडल की औसत हलचल है। "
            f"एचडीएफसी बैंक का एटीआर {lesson['atr']} है।"
        ),
        "matlab": (
            f"आखिरी भाव {lesson['entry']} एंट्री है। "
            f"{lesson['multiple']} गुना {lesson['atr']} बराबर {lesson['distance']} पॉइंट। "
            f"एसएल {lesson['sl']}। ऊपर {lesson['target']}।"
        ),
        "karein": (
            f"गलती तंग एसएल है। {lesson['atr']} की हलचल में तंग रेखा कटती है। "
            f"दूरी {lesson['distance']} हो और मात्रा {lesson['qty']} हो तो जोखिम {lesson['risk']} रुपये रहता है। "
            f"यह सलाह नहीं।"
        ),
    }
    title = f"ATR से SL — {name} पर दूरी {lesson['distance']}"
    if len(title) > 90:
        title = title[:90].rstrip()
    return {"title": title, "hook_line": hook_line, "beats": beats}


def fallback_narration(topic):
    """Four-step lesson from the topic card. The first sentence is the punchy hook."""
    if topic.get("_atr"):
        return atr_narration(topic)
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
    # A whole sentence often does not fit the last 1–2 words of room, which is
    # how a lesson stopped at 159. After the sentences, add one Hindi word at a
    # time into whichever beat still has room. Never past a beat cap or 180.
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
    pad = "यही आगे स्पष्ट सीमा संदर्भ सवाल पहचान मतलब जाँच".split()
    pad_i = 0
    guard = 0
    while sum(len(beats[b].split()) for b in BEATS) < 160 and guard < 40:
        guard += 1
        placed = False
        for beat in ("karein", "matlab", "pehchan", "hook"):
            words = beats[beat].split()
            total = sum(len(beats[b].split()) for b in BEATS)
            if len(words) >= caps[beat] or total >= 180:
                continue
            words.append(pad[pad_i % len(pad)])
            pad_i += 1
            beats[beat] = " ".join(words)
            placed = True
            break
        if not placed:
            break
    _ensure_concrete(beats, hook_line)
    title = _fallback_title(topic)
    return {"title": title, "hook_line": hook_line, "beats": beats}


def _fallback_title(topic):
    """8–90 characters. A long thumb line used to fail the title rule after the word gate."""
    thumb = topic.get("thumb") or []
    parts = [str(x).strip() for x in list(thumb)[:2] if str(x).strip()]
    title = " ".join(parts)
    if len(title) < 8:
        title = (title + " " + str(topic.get("topic") or "")).strip()
    title = " ".join(title.split())
    if len(title) > 90:
        title = title[:90].rstrip()
    if len(title) < 8:
        title = "Paise Ki Pathshala"
    return title


def _content_ok(topic, narration, spec):
    # Trend is scored at the end when data/trends.json exists. Regeneration
    # here is only for the hook and the number check, which a rewrite can fix.
    return self_review.score_content(topic, narration, spec, trends={})


def block_fallback(n, reason):
    """Record why this slot will not ship, then stop before TTS or upload."""
    if not isinstance(n, dict):
        n = {}
    n["narration_fallback"] = True
    n["source"] = "fallback"
    payload = {
        "blocked": True,
        "reason": reason,
        "narration_source": "fallback",
        "narration_fallback": True,
    }
    (RUN / "narration_block.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review = {
        "passed": False,
        "public_ok": False,
        "narration_fallback": True,
        "narration_source": "fallback",
        "summary": reason,
        "notes": [reason],
        "parts": {"hook": 0, "visual": 0, "accuracy": 0, "trend": None},
        "overall": 0,
        "bar": {"part": self_review.PART_BAR, "overall": self_review.OVERALL_BAR},
    }
    (RUN / "self_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache = RUN / "narration.json"
    cache.unlink(missing_ok=True)
    print(f"[BLOCK] {reason}", flush=True)
    die(reason)


def require_llm_narration(n, source):
    """TTS runs only for an LLM narration that already passed the teaching gate."""
    if (
        source in ("llm", "cache")
        and isinstance(n, dict)
        and not n.get("narration_fallback")
        and str(n.get("source") or "llm").strip().lower() != "fallback"
    ):
        return
    block_fallback(
        n if isinstance(n, dict) else {},
        "narration is not an LLM script that passed the hook/teaching gate; refusing TTS and upload",
    )


def narration(topic, c, spec=None):
    market_data.sync_topic_numbers(topic)
    if topic.get("_atr"):
        # The calm-rate script is fixed to the chart numbers. A 160-word draft
        # would run long, and speeding the voice up is not allowed.
        n = atr_narration(topic)
        fails = check_narration(n, topic)
        vague = scene_pace.vague_beats(n.get("beats"))
        if vague:
            fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
        content = _content_ok(topic, n, spec) if not fails else None
        if fails or not content["passed"]:
            why = fails or [content["summary"]]
            die("ATR narration failed its gate: " + "; ".join(why))
        n["review_attempts"] = 1
        (RUN / "narration.json").write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
        ok("narration: calm ATR script")
        return n, "fallback"
    cache = RUN / "narration.json"
    if cache.exists():
        try:
            n = json.loads(cache.read_text(encoding="utf-8"))
            if n.get("narration_fallback") or str(n.get("source") or "").strip().lower() == "fallback":
                block_fallback(
                    n,
                    "cached narration is a topic-card fallback, not an LLM script that passed the hook/teaching gate",
                )
            fails = check_narration(n, topic)
            content = _content_ok(topic, n, spec)
            # Only an LLM pass is reusable. A file without that mark is regenerated.
            if not fails and content["passed"] and n.get("source") == "llm" and not n.get("narration_fallback"):
                ok("narration cache reused — no LLM credit")
                return n, "cache"
        except SystemExit:
            raise
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
        vague = scene_pace.vague_beats(n.get("beats"))
        if vague:
            fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
        content = _content_ok(topic, n, spec) if not fails else None
        if content and not content["passed"]:
            fails.append("self-review below bar: " + content["summary"])
        if not fails:
            ok(f"narration OK (attempt {attempt})")
            n["review_attempts"] = attempt
            n["narration_fallback"] = False
            n["source"] = "llm"
            cache.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
            return n, "llm"
        last = "; ".join(fails)
        print(f"[retry] narration attempt {attempt}: {last}")
        if self_review.next_action(False, attempt) != "retry":
            break
    print("[warn] LLM narration failed the hook/teaching gate — building narration from the topic card")
    n = fallback_narration(topic)
    n["review_attempts"] = self_review.MAX_ATTEMPTS
    n["narration_fallback"] = True
    n["source"] = "fallback"
    fallback_fails = check_narration(n, topic)
    reason = (
        "LLM narration failed the hook/teaching gate; "
        "refusing the topic-card template (no upload, no paid voice)"
    )
    if fallback_fails:
        reason += ": " + "; ".join(fallback_fails)
    block_fallback(n, reason)


# ---------------------------------------------------------------- voice
async def _edge(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


ENGINES = []
ELEVEN_EXHAUSTED = False
ELEVEN_REMAINING = None
VOICE_FALLBACK_REASON = ""


def _reset_voice_state():
    global ENGINES, ELEVEN_EXHAUSTED, ELEVEN_REMAINING, VOICE_FALLBACK_REASON
    ENGINES = []
    ELEVEN_EXHAUSTED = False
    ELEVEN_REMAINING = None
    VOICE_FALLBACK_REASON = ""


def _switch_to_edge(reason):
    """One warning, then every remaining beat uses Edge. Paid and free audio are not mixed."""
    global ELEVEN_EXHAUSTED, VOICE_FALLBACK_REASON
    ELEVEN_EXHAUSTED = True
    if not VOICE_FALLBACK_REASON:
        VOICE_FALLBACK_REASON = str(reason)
        eleven_voice.log_fallback(VOICE_FALLBACK_REASON)


def preflight_eleven_credits(texts):
    """Fall back before synthesis when this Short's characters will not fit.

    GET /v1/user/subscription is not an auth check. A scoped key may be unable
    to read it, so 401/403 here does not fail the run. A quota-shaped body does
    switch to Edge. The TTS request remains the authority for a bad key.
    """
    global ELEVEN_REMAINING
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key or ELEVEN_EXHAUSTED:
        return
    import requests
    try:
        response = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": key, "Accept": "application/json"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 — network must not kill the slot
        print(f"[note] ElevenLabs subscription pre-check skipped ({exc}); TTS will decide")
        return
    body = eleven_voice.parse_response(response)
    status = getattr(response, "status_code", 0)
    if eleven_voice.classify(status, body) == "quota":
        _switch_to_edge(eleven_voice.describe(status, body))
        return
    if status != 200:
        print(f"[note] ElevenLabs subscription pre-check HTTP {status}; TTS remains the auth check")
        return
    remaining = eleven_voice.subscription_remaining(body)
    if remaining is None:
        return
    ELEVEN_REMAINING = remaining
    credit_log.set_eleven_remaining(remaining)
    import speak
    need = sum(len(speak.speakable(text or "")) for text in texts)
    if need > 0 and remaining < need:
        _switch_to_edge(f"pre-check remaining={remaining} characters, this Short needs {need}")


def _eleven(text, path, c):
    """Call ElevenLabs. Quota raises QuotaFallback. Auth and permission die."""
    import time
    import requests

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice = c["voice"].get("voice_id") or os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not key:
        return False

    global ELEVEN_EXHAUSTED, ELEVEN_REMAINING
    if ELEVEN_EXHAUSTED:
        return False
    if not voice:
        die("ELEVENLABS_API_KEY is set but ELEVENLABS_VOICE_ID/config voice_id is missing")

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
            credit_log.add_eleven_chars(len(text))
            response = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": key, "accept": "audio/mpeg"},
                json=payload,
                timeout=90,
            )
            if response.status_code == 200 and len(response.content) >= 2000:
                path.write_bytes(response.content)
                cost = int(response.headers.get("character-cost", str(need_chars)) or need_chars)
                if ELEVEN_REMAINING is not None:
                    ELEVEN_REMAINING = max(0, ELEVEN_REMAINING - cost)
                return True

            body = eleven_voice.parse_response(response)
            kind = eleven_voice.classify(response.status_code, body)
            why = eleven_voice.describe(response.status_code, body)
            if kind == "quota":
                raise eleven_voice.QuotaFallback(why)
            if kind == "auth":
                die(f"ElevenLabs authentication failed ({why}); not falling back to Edge")
            if kind == "permission":
                die(f"ElevenLabs permission error ({why}); not falling back to Edge")
            if kind == "retry" and attempt < 2:
                wait = 2 ** attempt
                print(f"[retry] ElevenLabs {response.status_code}; retrying in {wait}s")
                time.sleep(wait)
                continue
            if response.status_code == 200:
                die("ElevenLabs returned an empty or tiny audio file; not a credit error, so Edge was not used")
            die(f"ElevenLabs paid TTS failed ({why}); not a credit error, so Edge was not used")
        except eleven_voice.QuotaFallback:
            raise
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            die(f"ElevenLabs paid TTS error: {exc}")

    die("ElevenLabs paid TTS failed after retries; not a credit error, so Edge was not used")


def _edge_voice_rate(c):
    """Publish-run quota fallback keeps the configured Edge voice.

    Previews, samples, and dry runs use free hi-IN-MadhurNeural at +0%.
    """
    if eleven_voice.paid_voice_allowed():
        voice = c["voice"]
        return voice.get("edge_voice", "hi-IN-SwaraNeural"), voice.get("edge_rate", "+10%")
    return eleven_voice.PREVIEW_EDGE_VOICE, eleven_voice.PREVIEW_EDGE_RATE


def _paid_voice_this_beat():
    return (
        eleven_voice.paid_voice_allowed()
        and bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
        and not ELEVEN_EXHAUSTED
    )


def tts(text, path, c):
    """ElevenLabs only on a real publish (or an explicit sample opt-in).

    Quota on that path uses the configured Edge voice. Auth still fails.
    Previews use free Edge and never call ElevenLabs.
    Voice text is speak.speakable() (₹ -> रुपये). Captions keep the original text.
    """
    engine_file = path.with_suffix(".engine")
    cached_engine = engine_file.read_text(encoding="utf-8").strip().lower() if engine_file.exists() else ""
    eleven_key = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
    allow_paid = _paid_voice_this_beat()
    fresh = path.exists() and path.stat().st_size > 1000
    # Reuse paid audio only while this run is still on ElevenLabs.
    # After a credit fallback, an old paid file is discarded and Edge is used.
    # A preview never reuses a paid file.
    reuse = None
    if fresh and cached_engine == "elevenlabs" and allow_paid:
        reuse = cached_engine
    elif fresh and cached_engine == "edge" and not allow_paid:
        reuse = cached_engine
    elif fresh and cached_engine == "edge" and (not eleven_key or ELEVEN_EXHAUSTED):
        reuse = cached_engine
    if reuse:
        ok(f"voice cache reused: {path.name} ({reuse})")
        ENGINES.append(reuse)
        return
    import speak
    text = speak.speakable(text)
    left = speak.leftovers(text)
    if left:
        print(f"[warn] voice text me ab bhi: {sorted(set(left))} — speak.py me jodo")
    with open(RUN / "tts_text.txt", "a", encoding="utf-8") as handle:
        handle.write(f"{path.stem}: {text}\n")
    if allow_paid:
        if not _eleven(text, path, c):
            die(f"ElevenLabs TTS did not produce paid audio: {path.name}; not a credit error, so Edge was not used")
        ENGINES.append("elevenlabs")
        engine_file.write_text("elevenlabs", encoding="utf-8")
        return
    edge_voice, edge_rate = _edge_voice_rate(c)
    ENGINES.append("edge")
    for attempt in range(3):
        try:
            asyncio.run(_edge(
                text,
                edge_voice,
                edge_rate,
                path,
            ))
            if path.exists() and path.stat().st_size > 1000:
                engine_file.write_text("edge", encoding="utf-8")
                return
        except Exception as exc:  # noqa: BLE001
            print(f"[retry] tts {attempt + 1}: {exc}")
    die(f"TTS fail: {path.name}")


def synthesize_short_voices(spoken, c):
    """Speak every beat on one engine. A quota error redoes the whole run on Edge."""
    global ENGINES
    if (RUN / "narration_block.json").exists():
        die("narration blocked; refusing TTS")
    _reset_voice_state()
    # Subscription GET is free, and only relevant when this run may call ElevenLabs.
    if eleven_voice.paid_voice_allowed():
        preflight_eleven_credits([spoken[b] for b in BEATS])

    def once():
        global ENGINES
        ENGINES = []
        for beat in BEATS:
            tts(spoken[beat], RUN / f"tts_{beat}.mp3", c)

    try:
        once()
    except eleven_voice.QuotaFallback as exc:
        _switch_to_edge(exc.reason)
        for beat in BEATS:
            audio = RUN / f"tts_{beat}.mp3"
            audio.unlink(missing_ok=True)
            audio.with_suffix(".engine").unlink(missing_ok=True)
        once()
    engine = "edge" if "edge" in ENGINES else "elevenlabs"
    key_set = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
    eleven_voice.write_voice_meta(
        RUN,
        engine,
        VOICE_FALLBACK_REASON,
        elevenlabs_attempted=key_set and eleven_voice.paid_voice_allowed(),
    )
    credit_log.set_tts_engine(engine)
    ok(f"voice engine: {engine}" + (f" ({VOICE_FALLBACK_REASON})" if VOICE_FALLBACK_REASON else ""))
    return engine


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
            # Solid gold typewriter from the first frame. The full line is on
            # screen by 1.4s and stays through the hook beat (covers 0-2s).
            head = hook_line
            t0 = 0.0
            ev.extend(hook_motion.dialogues(head, t1, 540, 1170, fit_size(head, 980, 84)))
        else:
            ev.append(K.ass_text(t0 + 0.05, t1, 540, 1170, fit_size(head, 980, 84),
                                 K.YEL, head, 8, fade=True))
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
# Slide, wipe, and scale. Not a Ken Burns hold. ATR scenes move the plate
# only; each Devanagari line is one pre-shaped sprite and is faded whole.
# The overlap keeps the joined picture the same length as the voice.
SCENE_FADE_S = 0.28
SCENE_TRANSITIONS = ("slideleft", "wipeleft", "zoomin", "slideright", "wipedown", "smoothleft")


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


def still_clip(png, dur, out, fps=30):
    """One picture, one cut. Duration is the scene slice, not the whole beat."""
    dur = max(0.4, float(dur))
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-loop", "1", "-t", f"{dur:.3f}", "-i", str(png),
                    "-vf", f"fps={fps},format=yuv420p",
                    "-t", f"{dur:.3f}", "-r", str(fps), *X264, str(out)], check=True)


def join_scenes(clips, durations, out, fps=30):
    """Slide, wipe, or scale between stills. Output length equals the sum of the scene durations."""
    durations = [float(d) for d in durations]
    if len(clips) <= 1:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clips[0]), "-c", "copy", str(out)], check=True)
        return
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for clip in clips:
        cmd += ["-i", str(clip)]
    filters = []
    prev = "[0:v]"
    for i in range(1, len(clips)):
        offset = max(0.0, sum(durations[:i]) - 0.02)
        name = SCENE_TRANSITIONS[(i - 1) % len(SCENE_TRANSITIONS)]
        label = "[v]" if i == len(clips) - 1 else f"[x{i}]"
        filters.append(
            f"{prev}[{i}:v]xfade=transition={name}:duration={SCENE_FADE_S:.3f}:offset={offset:.3f}{label}"
        )
        prev = label
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-r", str(fps), "-t", f"{sum(durations):.3f}",
        *X264, str(out),
    ]
    subprocess.run(cmd, check=True)


def _layered(frames):
    return bool(frames) and all(
        frame is not None
        and getattr(frame, "plate", None) is not None
        and getattr(frame, "text_layers", None) is not None
        for frame in frames
    )


def assemble(beat_pngs, beat_audio, beat_times, ass_text, fps=30, switch=None, hook_line="",
             scenes=None):
    scenes = scenes or scene_pace.plan_scenes(beat_times)
    durations = []
    frames = []
    for scene in scenes:
        durations.append(float(scene["end"]) - float(scene["start"]))
        frames.append(scene.get("frame"))
    if _layered(frames):
        faults = scene_transition.transition_faults(frames, SCENE_TRANSITIONS)
        if faults:
            die("shaped text was sliced at a transition: " + "; ".join(faults[:4]))
        scene_transition.write_timeline(
            frames, durations, RUN / "silent.mp4", fps=fps,
            transitions=SCENE_TRANSITIONS, fade_s=SCENE_FADE_S,
            overlay=scene_frames.paint_progress,
        )
    else:
        clips = []
        for i, scene in enumerate(scenes):
            png = scene.get("png")
            if not png:
                pics = beat_pngs[scene["beat"]]
                png = pics[min(scene["index"], len(pics) - 1)]
                scene["png"] = str(png)
            pad = SCENE_FADE_S if i < len(scenes) - 1 else 0.0
            out = RUN / f"scene_{i:02d}.mp4"
            still_clip(png, durations[i] + pad, out, fps)
            clips.append(out)
        join_scenes(clips, durations, RUN / "silent.mp4", fps)
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
    # Clicks sit under the voice. The wav length does not change, so narration
    # is not delayed, and the music mix below still starts from this same file.
    if str(hook_line or "").strip():
        hook_sfx.mix_file(RUN / "voice.wav", hook_motion.click_times(hook_line))
    bed = ROOT / "assets" / "bed" / "bed.wav"
    if bed.exists():
        mixed = RUN / "mixed.wav"
        bed_audio.mix_into(RUN / "voice.wav", scenes, mixed)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mixed), "-c:a", "aac", "-b:a", "192k",
                        "audio.m4a"], cwd=RUN, check=True)
    else:
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
    if not args.qa:
        blocked = slot_guard.scheduled_slot_block()
        if blocked:
            print(f"[skip] {blocked}; exiting before paid APIs")
            die(blocked)
    c = cfg()
    if not shutil.which("ffmpeg"):
        die("ffmpeg missing")

    if args.key:
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
        topic = next(t for t in bank if t["key"] == args.key)
    else:
        topic = load_topic()
    ok(f"template: {topic['key']} ({topic['cat']})")
    # Script prices and the गुना line use the same closes the chart divides.
    market_data.sync_topic_numbers(topic)
    spec = market_data.spec_for_topic(topic)
    if spec.get("atr"):
        market_data.apply_atr_card(topic, spec["atr"])
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
    require_llm_narration(n, source)
    # CTA/disclaimer stay on-screen; do not spend TTS credits speaking them.
    spoken = dict(n["beats"])
    # Generate each beat voice, then trim only the *outer* TTS silence.
    # ElevenLabs/Edge can add a little leading/trailing room around every beat;
    # concatenating those raw files made the narration sound like it "waited"
    # at every beat. We keep natural pauses *inside* a sentence, but remove
    # artificial silence at beat boundaries. No artificial 50s padding.
    beat_audio, raw_durations = {}, {}
    (RUN / "tts_text.txt").unlink(missing_ok=True)
    synthesize_short_voices(spoken, c)
    for b in BEATS:
        mp3 = RUN / f"tts_{b}.mp3"
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
    switch = {b: cue_frac(topic["key"], spoken[b]) for b in BEATS if len(beat_pngs[b]) == 3}
    if switch:
        ok("3rd picture at " + ", ".join(f"{b} {f:.0%}" for b, f in switch.items()))
    cta = engagement.engagement_question(topic)
    hook_line = n.get("hook_line") or ""
    scenes = scene_pace.plan_scenes(beat_times)
    groups = {b: [] for b in BEATS}
    for scene in scenes:
        groups[scene["beat"]].append(scene)
    paced = {}
    label_boxes = []
    labels_ready = True
    for beat, group in groups.items():
        if not group:
            continue
        frames, flags = scene_frames.frames_for(topic, beat, len(group), spec)
        kinds = scene_frames.kinds_for(beat, len(group)) if spec.get("atr") else [""] * len(group)
        paths = []
        for i, (scene, frame, flag) in enumerate(zip(group, frames, flags)):
            if getattr(frame, "text_layers", None) is not None and getattr(frame, "plate", None) is not None:
                scene["frame"] = frame
                scene_frames.set_progress(frame, (scenes.index(scene) + 1) / len(scenes))
            path = RUN / f"sc_{beat}_{i}.png"
            frame.save(path)
            scene["png"] = str(path)
            scene["kind"] = kinds[i] if kinds[i] else getattr(frame, "scene_kind", "")
            scene["callout"] = bool(scene.get("callout") or flag)
            boxes = getattr(frame, "label_boxes", None)
            if boxes is None:
                labels_ready = False
            elif labels_ready:
                label_boxes.extend(boxes)
            paths.append(path)
        paced[beat] = tuple(paths)
    visual_gate(paced)
    ok(f"scenes: {len(scenes)} cuts, longest {max(s['end'] - s['start'] for s in scenes):.2f}s")
    overlays = short_layout.overlay_plan(topic, beat_times, hook_line, cta)
    review = self_review.full_review(
        topic, n, spec, overlays, scenes=scenes,
        labels=(label_boxes if labels_ready else None),
    )
    review["narration_source"] = source
    review["attempts"] = n.get("review_attempts") or self_review.MAX_ATTEMPTS
    review["scenes"] = [
        {"beat": s["beat"], "kind": s.get("kind") or "",
         "start": round(s["start"], 2), "end": round(s["end"], 2),
         "callout": bool(s.get("callout"))}
        for s in scenes
    ]
    (RUN / "self_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    if review["public_ok"]:
        ok("self-review passed: " + review["summary"])
    else:
        print("[HOLD] self-review below the bar — public upload blocked. " + review["summary"])
    assemble(paced, beat_audio, beat_times,
             build_ass(topic, beat_times, spoken, hook_line, cta), switch=switch,
             hook_line=hook_line, scenes=scenes)

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
