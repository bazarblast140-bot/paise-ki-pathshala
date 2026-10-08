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
import hashlib
import json
from datetime import date
import math
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
# 1.5× the previous 26px line. It stays in the brand band, under the captions.
DISCLAIMER_SIZE = 39
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
    beat = visual_beat(beat)
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
    if str((topic or {}).get("key") or "") == "aaj_ka_nifty":
        import nifty_day
        nifty_day.ensure_session(topic)
        spoken = str(((topic or {}).get("_spoken_beats") or {}).get(beat) or "")
        frame = nifty_day.paint_frame(topic, beat, 0, 1, spoken)
        return (frame, frame.copy())
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
    """Chart Board cover: the lesson chart, a bold ₹ amount, a 3–4 word Hindi hook.

    Live shorts use this instead of the red host card. YouTube reads thumbnail.jpg.
    Instagram sends that same file as the Reel cover.
    """
    if topic.get("_live_short") or topic.get("_spoken_beats") or hook_img is None:
        img = S.paint_cover(topic)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out_path, quality=92)
        return out_path
    return _legacy_thumbnail(topic, out_path, hook_img)


def _legacy_thumbnail(topic, out_path, hook_img=None):
    """Older red host card. Kept for a caller that already painted a board crop."""
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


# Spoken targets for the Edge (non-live) four-beat Short: buffer and previews.
# Edge hi-IN-MadhurNeural +0% speaks about 2.3 words/s (168 words ran 72.7s,
# 206 words ran 94.8s), and the duration gate is 42-62s. 100-138 words is
# 43-60s. The old 150-190 window always overran the gate on Edge.
# The gate accepts ±30% on each beat, like the live path: the model keeps
# missing a tight count. The hook target stays short so a 40-word opening
# still fails.
BEAT_WORD_TARGET = {"hook": 22, "pehchan": 29, "matlab": 36, "karein": 32}
BEAT_WORD_TOLERANCE = 0.30
TOTAL_WORDS = (100, 138)
TOTAL_WORD_TARGET = (112, 126)
NARRATION_CALL_CAP = 6
# An empty or timed-out DeepSeek reply gets one more try inside that cap.
# A second empty reply skips the topic. It does not burn the rest of the cap
# and it does not stop Chart Board.
EMPTY_NARRATION_TRIES = 2


class NarrationSkip(Exception):
    """DeepSeek stayed empty after one retry. Chart Board tries the next topic."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
NARRATION_STATIC_REPAIRS = 4

# Live Shorts (ElevenLabs) explain the topic in six beats. Buffer/Edge keeps
# the four-beat lesson above. Targets are derived from the voice coefficient.
# Rudra at speed 0.90 was measured at 1.9 words/s (255 words ran 133.9s).
# 75-90s is about 140-175 words. The 82.5s midpoint is 157 words.
LIVE_ORDER = ("hook", "pehchan", "matlab", "udaharan", "galti", "karein")
LIVE_SHARE = {"hook": 18, "pehchan": 24, "matlab": 27, "udaharan": 33, "galti": 29, "karein": 26}
LIVE_BASE_WPS = 1.9
ESTIMATE_SECONDS = (75.0, 90.0)
LIVE_VOICE_SECONDS = (70.0, 95.0)
# The model keeps missing a tight count, and a repair makes the script longer.
# Prompt target is 140-175 words. Accept ±30% per beat and 130-185 words.
# Audio stays 70-95s. The duration estimate is the credit gate, not the word floor.
LIVE_BEAT_TOLERANCE = 0.30
LIVE_WORD_TARGET = (140, 175)
LIVE_TOTAL_WORDS = (130, 185)
# 177 words at 1.9 w/s is about 93s: inside the 95s gate, and too close once
# the take runs long. Trim toward 170 words before any TTS call.
LIVE_ESTIMATE_MARGIN_S = 90.0
LIVE_MARGIN_WORDS = 170
# Proven four-beat live Short. This is the default.
# Word target 98-108 (~52-57s on Rudra at 1.9 w/s). The trim aims at 59s.
# The text gate stays 88-122 and the audio gate stays 45-65s, so a take
# over 59s is not blocked. The aim is the middle of the target, not the floor.
FOUR_LIVE_SHARE = {"hook": 18, "pehchan": 26, "matlab": 32, "karein": 29}
FOUR_LIVE_WORD_TARGET = (98, 108)
FOUR_LIVE_TOTAL_WORDS = (88, 122)
FOUR_LIVE_VOICE_SECONDS = (45.0, 65.0)
# The locked daily-loss script is 138 words. Rudra at 0.90 was measured at
# 1.9 words/s, about 73s, past the 65s four-beat cap. It is not trimmed.
APPROVED_VOICE_SECONDS = (45.0, 85.0)
FOUR_LIVE_MARGIN_S = 59.0
VISUAL_ALIAS = {"udaharan": "matlab", "galti": "karein"}
CARD_LINES = {
    "udaharan": ("उदाहरण", "एक ठोस केस"),
    "galti": ("आम गलती", "इसे ऐसे सुधारें"),
}


def _live_lesson(c):
    """Paid ElevenLabs publishes a live Short. Preview and buffer stay on the evergreen four."""
    engine = str(((c or {}).get("voice") or {}).get("engine") or "elevenlabs").strip().lower()
    return engine == "elevenlabs" and eleven_voice.paid_voice_allowed()


def resolve_live_beats(topic=None):
    """four or six. Topic flag, then LIVE_BEATS, then config.json. Default four."""
    raw = str((topic or {}).get("_live_beats") or "").strip().lower()
    if raw == "plan":
        return "plan"
    if raw in ("four", "six"):
        return raw
    raw = os.environ.get("LIVE_BEATS", "").strip().lower()
    if raw in ("four", "six"):
        return raw
    try:
        data = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "four"
    raw = str(data.get("live_beats") or "").strip().lower()
    if raw not in ("four", "six"):
        short = ((data.get("formats") or {}).get("short") or {})
        raw = str(short.get("live_beats") or "four").strip().lower()
    return raw if raw in ("four", "six") else "four"


def four_live(topic):
    """The default live Short: four beats, ~55s, not the six-beat lesson."""
    if resolve_live_beats(topic) == "plan":
        return False
    return (
        bool((topic or {}).get("_live_short"))
        and not (topic or {}).get("_atr")
        and resolve_live_beats(topic) != "six"
    )


def voice_wps(c):
    voice = (c or {}).get("voice") or {}
    raw = voice.get("words_per_second")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    # Kamal (qsz5tTEjPvsiIJZIpM8S) has not been timed. 1.9 is an unmeasured
    # conservative estimate, the same pace Rudra was measured at, so a fallback
    # script is not planned for a faster voice than the one that will speak it.
    if str(voice.get("voice_id") or "") == eleven_voice.KAMAL_VOICE_ID:
        return 1.9
    return LIVE_BASE_WPS


def live_beat_targets(wps):
    """Scale the Rudra shares so the sum is the midpoint of 75-90s."""
    wps = float(wps)
    scale = wps / LIVE_BASE_WPS
    targets = {beat: max(8, int(round(LIVE_SHARE[beat] * scale))) for beat in LIVE_ORDER}
    aim = int(round(((ESTIMATE_SECONDS[0] + ESTIMATE_SECONDS[1]) / 2.0) * wps))
    delta = aim - sum(targets.values())
    step = 0
    while delta != 0 and step < 40:
        beat = LIVE_ORDER[step % len(LIVE_ORDER)]
        if delta > 0:
            targets[beat] += 1
            delta -= 1
        elif targets[beat] > 8:
            targets[beat] -= 1
            delta += 1
        step += 1
    return targets


def live_word_window(wps):
    """Word counts whose estimate is inside 75-90s at this pace."""
    wps = float(wps)
    lo = int(math.ceil(ESTIMATE_SECONDS[0] * wps - 1e-9))
    hi = int(math.floor(ESTIMATE_SECONDS[1] * wps + 1e-9))
    return lo, hi


def four_live_bounds(wps):
    """Word target and text gate, scaled from the 1.9 w/s Rudra figures."""
    scale = float(wps) / LIVE_BASE_WPS

    def pair(lo, hi):
        return (max(1, int(round(lo * scale))), max(1, int(round(hi * scale))))

    return pair(*FOUR_LIVE_WORD_TARGET), pair(*FOUR_LIVE_TOTAL_WORDS)


def four_live_targets(wps):
    """Per-beat targets whose sum is the middle of 98-108 at 1.9 w/s."""
    wps = float(wps)
    scale = wps / LIVE_BASE_WPS
    targets = {beat: max(8, int(round(FOUR_LIVE_SHARE[beat] * scale))) for beat in BEATS}
    lo, hi = FOUR_LIVE_WORD_TARGET
    aim = int(math.floor(((lo + hi) / 2.0) * scale + 0.5))
    delta = aim - sum(targets.values())
    step = 0
    while delta != 0 and step < 40:
        beat = BEATS[step % len(BEATS)]
        if delta > 0:
            targets[beat] += 1
            delta -= 1
        elif targets[beat] > 8:
            targets[beat] -= 1
            delta += 1
        step += 1
    return targets


def four_margin_words(wps):
    """Word budget for a 59s estimate, inside the 88-122 text gate at 1.9 w/s."""
    return max(1, int(round(FOUR_LIVE_MARGIN_S * float(wps))))


def live_voice_bounds(topic):
    """Seconds the pre-TTS estimate and the finished voice must land in.

    A hash-locked approved script is voiced as stored. The 65s four-beat cap
    would discard Rudra after the characters were already spent.
    """
    if str((topic or {}).get("key") or "") == "aaj_ka_nifty":
        return (45.0, 59.0)
    import trade_plan
    if trade_plan.is_plan_topic(topic):
        # 40–50s is the target. Up to 55s is a review flag, not a block.
        return trade_plan.hard_voice_seconds()
    if _approved_narration(None, topic):
        return APPROVED_VOICE_SECONDS
    if four_live(topic):
        return FOUR_LIVE_VOICE_SECONDS
    return LIVE_VOICE_SECONDS


def _topic_voice(topic):
    return {"voice": (topic or {}).get("_voice") or {}}


def _lesson(topic):
    """Beat order, per-beat targets, accept window, and the prompt's target band."""
    import trade_plan
    if trade_plan.is_plan_topic(topic):
        return trade_plan.BEATS, trade_plan.WORD_TARGETS, trade_plan.TOTAL_WORDS, trade_plan.WORD_TARGET
    if (topic or {}).get("_live_short") and not (topic or {}).get("_atr"):
        wps = voice_wps(_topic_voice(topic))
        if resolve_live_beats(topic) == "six":
            return LIVE_ORDER, live_beat_targets(wps), LIVE_TOTAL_WORDS, LIVE_WORD_TARGET
        target, total = four_live_bounds(wps)
        return BEATS, four_live_targets(wps), total, target
    return BEATS, BEAT_WORD_TARGET, TOTAL_WORDS, TOTAL_WORD_TARGET


def timed_beats(beat_times):
    import trade_plan
    if beat_times and "entry" in beat_times and "cta" in beat_times:
        return tuple(beat for beat in trade_plan.BEATS if beat in beat_times)
    found = tuple(b for b in LIVE_ORDER if b in (beat_times or {}))
    return found or BEATS


def card_pair(topic, beat):
    if beat in hook_check.TEACHING_BEATS:
        spoken = str(((topic or {}).get("_spoken_beats") or {}).get(beat) or "")
        return hook_check.example_card_lines(topic, spoken)
    pair = (topic.get("beats") or {}).get(beat)
    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
        return str(pair[0]), str(pair[1])
    return CARD_LINES.get(beat, (beat, beat))


def visual_beat(beat):
    return VISUAL_ALIAS.get(beat, beat)


def duplicate_sentence_notes(beats, order):
    """A sentence of six or more words copied into another beat."""
    seen = {}
    notes = []
    for beat in order:
        text = str((beats or {}).get(beat) or "")
        for sent in re.split(r"[।?!]", text):
            words = sent.split()
            if len(words) < 6:
                continue
            key = " ".join(words)
            if key in seen and seen[key] != beat:
                notes.append(f"duplicate sentence in {seen[key]} and {beat}")
            else:
                seen.setdefault(key, beat)
    return notes


def beat_word_window(target, tolerance=None):
    """Inclusive word window. Four-beat scripts stay at ±20%. Live uses ±30%."""
    tol = BEAT_WORD_TOLERANCE if tolerance is None else float(tolerance)
    lo = max(1, int(round(target * (1 - tol))))
    hi = int(round(target * (1 + tol)))
    return lo, hi


def beat_word_counts(n):
    beats = (n or {}).get("beats") or {}
    return {b: len(str(beats.get(b) or "").split()) for b in BEATS}


def _teaching_beat_ranges():
    return {b: beat_word_window(BEAT_WORD_TARGET[b]) for b in BEATS}


def check_narration(n, topic):
    import trade_plan
    if trade_plan.is_plan_topic(topic) or (n or {}).get("plan"):
        checked = dict(topic or {})
        if (n or {}).get("plan"):
            checked["_live_beats"] = "plan"
            checked["key"] = checked.get("key") or n.get("key")
        return trade_plan.script_fails(n, checked)
    fails = []
    beats = n.get("beats") or {}
    if topic.get("_atr") or not topic.get("_live_short"):
        order = BEATS
    else:
        order = _lesson(topic)[0]
    for b in order:
        if not str(beats.get(b) or "").strip():
            fails.append(f"beat '{b}' khaali")
    text = " ".join(str(beats.get(b) or "") for b in order)
    words = len(text.split())
    # The ATR short is spoken at a calm rate, so its script is shorter.
    # Every other topic targets 112-126 and may land anywhere in 100-138.
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
        order, targets, (lo, hi), (target_lo, target_hi) = _lesson(topic)
        if not lo <= words <= hi:
            fails.append(
                f"{words} words ({lo}-{hi} chahiye, target {target_lo}-{target_hi})"
            )
        tol = LIVE_BEAT_TOLERANCE if topic.get("_live_short") else None
        beat_ranges = {b: beat_word_window(targets[b], tol) for b in order}
        if topic.get("_live_short"):
            fails.extend(duplicate_sentence_notes(beats, order))
    for b, (lo, hi) in beat_ranges.items():
        bw = len(str(beats.get(b) or "").split())
        if not lo <= bw <= hi:
            fails.append(f"{b} beat {bw} words ({lo}-{hi} chahiye)")
    if topic.get("_live_short"):
        extra = hook_check.live_number_faults(beats, topic, order)
    else:
        extra = hook_check.unsourced_numbers(text, topic)
    if extra:
        fails.append(f"ye numbers topic me nahi hain: {sorted(extra)} — sirf diye gaye numbers use karo")
    if topic.get("_live_short"):
        for beat in hook_check.unlabelled_example_beats(beats, order):
            fails.append(f"{beat} numbers must be labelled उदाहरण in the same sentence")
        fails.extend(hook_check.fact_contradiction_notes(text, topic))
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
    if topic.get("_live_short") and not topic.get("_atr"):
        fold_roman_beats(n)
        fails.extend(narrator_leak_notes(beats, order))
        fails.extend(title_term_notes(n))
    return fails


_LEAK_SNIPS = (
    "अब इसे chart पर पहचानें",
    "अब इसे चार्ट पर पहचानें",
    "अब इसे chart पर पहचानने का तरीका समझिए",
    "इन features का मतलब समझिए",
    "इन फीचर्स का मतलब समझिए",
    "अब आगे traders क्या check करते हैं",
    "अब आगे ट्रेडर्स क्या चेक करते हैं",
    "ट्रेडर्स यही चेक करते",
    "चार्ट पर दिखता",
    "कार्ड पर लिखा है",
    "कार्ड पर दिया हिसाब बोलो",
    "कार्ड का आंकड़ा ही बोलो",
    "अगले हफ़्ते से हर trade के बाद",
    "यह explanation इसी दिए गए तथ्य तक सीमित है",
    "किसी अतिरिक्त price, date या return का अनुमान नहीं लगाया जा रहा",
    "स्क्रीन पर दिख रहे feature",
    "स्क्रीन पर",
    "आपके सामने स्क्रीन",
)
_LEAK_META = re.compile(r"कार्ड|topic\s*card|\bcard\b|\bscript\b|\bbeat\b", re.I)
_LEAK_TELL = re.compile(r"(बोलो|बताओ|बोलिए|बताइए)")
_HINDI_NUMBER = (
    "शून्य|एक|दो|तीन|चार|पाँच|पांच|छह|सात|आठ|नौ|दस|"
    "ग्यारह|बारह|तेरह|चौदह|पंद्रह|सोलह|सत्रह|अठारह|उन्नीस|"
    "बीस|तीस|चालीस|पचास|साठ|सत्तर|अस्सी|नब्बे|सौ|"
    "हज़ार|हजार|लाख|करोड़|अट्ठानवे|अट्ठासी|अट्ठाइस|इक्कीस"
)
_BARE_NUMBER = re.compile(
    rf"(?:[+\-−]?\d[\d,]*(?:\.\d+)?%?"
    rf"|(?:{_HINDI_NUMBER})(?:\s+(?:{_HINDI_NUMBER}))?)"
)
# Two letters too: leftover Latin such as "OK" is still a leak. Finance
# abbreviations are folded to Devanagari before this scan.
_LATIN_WORD = re.compile(r"(?<![A-Za-z])([A-Za-z]{2,})(?![A-Za-z])")
_CHART_FILLER = re.compile(r"चार्ट\s*पर\s*दिख")
_CHART_MENTION = re.compile(r"चार्ट|स्क्रीन")
_TRADER_FILLER = re.compile(r"ट्रेडर्स\s+(?:\S+\s+){0,4}चेक\s+करते")
# Phrases first, so "daily limit" becomes the lesson words and not "डेली लिमिट",
# and "revenge trading" is one phrase rather than two leftover words.
_ROMAN_PHRASES = (
    ("sirf seekhne ke liye, financial advice nahi.", "सिर्फ सीखने के लिए, फाइनेंशियल एडवाइस नहीं।"),
    ("sirf seekhne ke liye", "सिर्फ सीखने के लिए"),
    ("financial advice", "फाइनेंशियल एडवाइस"),
    ("revenge trading", "रिवेंज ट्रेडिंग"),
    ("daily loss limit", "दिन की नुकसान हद"),
    ("daily limit", "दिन की हद"),
    ("profit expectancy", "प्रॉफिट एक्सपेक्टेंसी"),
    ("win rate", "विन रेट"),
)
_ROMAN_FOLD = {
    "rakho": "रखो",
    "karo": "करो",
    "dekho": "देखो",
    "samjho": "समझो",
    "likho": "लिखो",
    "jaano": "जानो",
    "suno": "सुनो",
    "chhodo": "छोड़ो",
    "batao": "बताओ",
    "bolo": "बोलो",
    # Finance English. Edge reads these in English and Whisper then garbles them.
    "call": "कॉल",
    "calls": "कॉल्स",
    "premium": "प्रीमियम",
    "strike": "स्ट्राइक",
    "breakeven": "ब्रेकईवन",
    "expiry": "एक्सपायरी",
    "put": "पुट",
    "puts": "पुट्स",
    "charge": "चार्ज",
    "charges": "चार्जेस",
    "trader": "ट्रेडर",
    "traders": "ट्रेडर्स",
    "trade": "ट्रेड",
    "trading": "ट्रेडिंग",
    "revenge": "रिवेंज",
    "win": "विन",
    "rate": "रेट",
    "profit": "प्रॉफिट",
    "expectancy": "एक्सपेक्टेंसी",
    "brokerage": "ब्रोकरेज",
    "slippage": "स्लिपेज",
    "stt": "एस टी टी",
    "journal": "जर्नल",
    "roce": "आरओसीई",
    "ebit": "ईबिट",
    "emi": "ईएमआई",
    "rbi": "आरबीआई",
    "sebi": "सेबी",
    "ipo": "आईपीओ",
    "repo": "रेपो",
    "operating": "ऑपरेटिंग",
    "capital": "कैपिटल",
    "employed": "एम्प्लॉयड",
    "overtrading": "ओवरट्रेडिंग",
    "borrowers": "बॉरोअर्स",
    "sl": "स्टॉप लॉस",
    "check": "चेक",
    "risk": "रिस्क",
    "buyer": "बायर",
    "buyers": "बायर्स",
    "option": "ऑप्शन",
    "options": "ऑप्शन्स",
    "daily": "डेली",
    "limit": "लिमिट",
    "terminal": "टर्मिनल",
    "chart": "चार्ट",
    "edge": "एज",
    "wick": "विक",
    "features": "फीचर्स",
    "price": "प्राइस",
    "date": "डेट",
    "return": "रिटर्न",
    "explanation": "एक्सप्लेनेशन",
    "entry": "एंट्री",
    "stop": "स्टॉप",
    "loss": "लॉस",
    "low": "लो",
    "high": "हाई",
    "sellers": "सेलर्स",
    "atr": "एटीआर",
    "rsi": "आर एस आई",
    "vwap": "वी वैप",
    "ema": "ई एम ए",
    "sma": "एस एम ए",
    "setup": "सेटअप",
    "hook": "हुक",
    "pehchan": "पहचान",
    "matlab": "मतलब",
    "karein": "करें",
    "udaharan": "उदाहरण",
    "galti": "गलती",
    "sirf": "सिर्फ",
    "seekhne": "सीखने",
    "liye": "लिए",
    "ke": "के",
    "financial": "फाइनेंशियल",
    "advice": "एडवाइस",
    "nahi": "नहीं",
}


def _bare_number_piece(text):
    body = re.sub(r"^(?:उदाहरण|example)\s*[:：]?\s*", "", str(text or ""), flags=re.I)
    body = body.strip().strip("।. ")
    return bool(body) and bool(_BARE_NUMBER.fullmatch(body))


def _number_dump(sentence):
    """A label followed by a bare list of numbers, not a worked example."""
    if not re.search(r"उदाहरण|example", sentence or "", re.I):
        return False
    parts = [part.strip() for part in re.split(r"[,،]", sentence) if part.strip()]
    if len(parts) < 3:
        return False
    bare = sum(1 for part in parts if _bare_number_piece(part))
    return bare >= 3 and bare == len(parts)


def latin_misread_words(text):
    """Latin still left after finance English is written in Devanagari.

    Known finance words are folded first and never block. Two letters still
    count, so a leftover such as OK fails the leak gate. The repair writes
    that leftover in Devanagari.
    """
    folded = fold_roman_words(text or "")
    found = []
    for match in _LATIN_WORD.finditer(folded):
        word = match.group(1)
        if word not in found:
            found.append(word)
    return found


_DEV_LETTER = {
    "a": "अ", "b": "ब", "c": "क", "d": "द", "e": "ए", "f": "फ",
    "g": "ग", "h": "ह", "i": "इ", "j": "ज", "k": "क", "l": "ल",
    "m": "म", "n": "न", "o": "ओ", "p": "प", "q": "क", "r": "र",
    "s": "स", "t": "ट", "u": "उ", "v": "व", "w": "व", "x": "क्स",
    "y": "य", "z": "ज",
}


def _transliterate_latin(word):
    """One Devanagari token. Word count stays the same, so the length gate does not move."""
    return "".join(_DEV_LETTER.get(ch.lower(), "") for ch in word)


def fold_roman_words(text):
    """Known English and romanized Hindi become Devanagari.

    A leftover of three or more letters is transliterated too. Two-letter
    leftovers such as OK still fail the leak gate. The voice never receives
    the original Latin.
    """
    out = str(text or "")
    for src, dst in _ROMAN_PHRASES:
        out = re.sub(re.escape(src), dst, out, flags=re.I)

    def repl(match):
        word = match.group(0)
        mapped = _ROMAN_FOLD.get(word.lower())
        if mapped:
            return mapped
        if len(word) >= 3:
            return _transliterate_latin(word)
        return word

    return _LATIN_WORD.sub(repl, out)


def fold_roman_beats(narration):
    if not isinstance(narration, dict):
        return narration
    beats = narration.get("beats")
    if isinstance(beats, dict):
        for beat, text in list(beats.items()):
            beats[beat] = fold_roman_words(text)
    if narration.get("hook_line"):
        narration["hook_line"] = fold_roman_words(narration["hook_line"])
    return narration


def _beat_draws_chart(text):
    """True when this beat's own figures can share one labelled chart."""
    figs = scene_frames._figure_tokens([text or ""])
    return len(scene_frames.comparable_chart_figures(figs)) >= 2


def narrator_leak_notes(beats, order):
    """Sentences that instruct the narrator, dump numbers, or leave Latin for the voice.

    A chart or screen reference is a leak when this beat draws no chart.
    "चार्ट पर दिखता" and "ट्रेडर्स यही चेक करते" are narrator filler either way.
    """
    notes = []
    for beat in order or ():
        text = str((beats or {}).get(beat) or "")
        draws = _beat_draws_chart(text)
        for sent in re.split(r"[।!?？]+", text):
            line = " ".join(sent.split())
            if not line:
                continue
            low = line.lower()
            leaked = any(snip.lower() in low for snip in _LEAK_SNIPS)
            if _LEAK_META.search(line) or _LEAK_TELL.search(line):
                leaked = True
            if _number_dump(line):
                leaked = True
            if _CHART_FILLER.search(line) or _TRADER_FILLER.search(line):
                leaked = True
            if _CHART_MENTION.search(line) and not draws:
                leaked = True
            latin = latin_misread_words(line)
            if latin:
                leaked = True
                line = line + " [" + ", ".join(latin) + "]"
            if leaked:
                notes.append(f"{beat} narrator leak: {line[:90]}")
                break
    return notes


# Function words are not the title's promise. का/की/के may sit inside the phrase.
_TITLE_FUNCTION = {"कैसे", "करें", "तय", "नियम", "घटे"}
_TITLE_CONNECTORS = {"का", "की", "के"}


# Title checks keep Latin when we have no spoken form. Letter-by-letter
# transliteration ("Piercing" → पइएरकइनग) is not a pronunciation.
_SPOKEN_TITLE = {
    "piercing": "पियर्सिंग",
    "line": "लाइन",
}


def fold_title_words(text):
    """Known finance English becomes Devanagari. Other Latin title words stay Latin."""
    out = str(text or "")
    for src, dst in _ROMAN_PHRASES:
        out = re.sub(re.escape(src), dst, out, flags=re.I)

    def repl(match):
        word = match.group(0)
        mapped = _ROMAN_FOLD.get(word.lower()) or _SPOKEN_TITLE.get(word.lower())
        return mapped if mapped else word

    return _LATIN_WORD.sub(repl, out)


def key_phrase_for(title):
    """The title's longest noun phrase. Function words and figures are not part of it.

    "डेली लॉस लिमिट पर रुकें" is "डेली लॉस लिमिट". "नुकसान का गणित कैसे घटे"
    is "नुकसान का गणित". A title that is only function words has no phrase.
    """
    import sync_gate
    folded = fold_title_words(title or "")
    groups = []
    current = []
    pending = None
    for tok in sync_gate.tokens(folded):
        if str(tok)[:1].isdigit():
            if current:
                groups.append(current)
                current = []
            pending = None
            continue
        if tok in _TITLE_FUNCTION:
            if current:
                groups.append(current)
                current = []
            pending = None
            continue
        if tok in sync_gate._STOP:
            if tok in _TITLE_CONNECTORS and current:
                pending = tok
            else:
                if current:
                    groups.append(current)
                    current = []
                pending = None
            continue
        if len(tok) < 2:
            continue
        if pending and current:
            current.append(pending)
        pending = None
        current.append(tok)
    if current:
        groups.append(current)
    if not groups:
        return ""
    best = max(
        groups,
        key=lambda group: (
            sum(1 for tok in group if tok not in _TITLE_CONNECTORS),
            len(" ".join(group)),
        ),
    )
    return " ".join(best)


def _token_forms(tok):
    """Latin, the spoken Hindi, and the finance fold all count as the same word."""
    forms = {str(tok or ""), str(tok or "").casefold()}
    low = str(tok or "").casefold()
    mapped = _ROMAN_FOLD.get(low) or _SPOKEN_TITLE.get(low)
    if mapped:
        forms.add(mapped)
    for latin, hindi in _SPOKEN_TITLE.items():
        if tok == hindi or low == hindi.casefold():
            forms.add(latin)
            forms.add(hindi)
    for latin, hindi in _ROMAN_FOLD.items():
        if tok == hindi:
            forms.add(latin)
            forms.add(hindi)
    return {form for form in forms if form}


def phrase_in_text(text, phrase):
    """True when the key phrase is spoken as one stretch, not scattered words.

    A Latin title word matches its spoken Hindi (पियर्सिंग लाइन) or the Latin
    spelling. It does not match a letter-by-letter transliteration.
    """
    phrase = " ".join(str(phrase or "").split())
    if not phrase:
        return False
    if phrase in " ".join(str(text or "").split()):
        return True
    import sync_gate
    folded_phrase = fold_title_words(phrase)
    folded_text = fold_title_words(text or "")
    if folded_phrase and folded_phrase in folded_text:
        return True
    need = sync_gate.tokens(folded_phrase)
    got = sync_gate.tokens(folded_text)
    width = len(need)
    if not width:
        return False
    if any(got[i:i + width] == need for i in range(len(got) - width + 1)):
        return True
    forms = [_token_forms(tok) for tok in need]
    for i in range(len(got) - width + 1):
        if all(got[i + j] in forms[j] or got[i + j].casefold() in {f.casefold() for f in forms[j]} for j in range(width)):
            return True
    return False


def title_term_notes(narration):
    """The hook must say the title's key phrase. Karein does not have to repeat it.

    Function words are not required. The failure names the phrase, so the
    repair writes that phrase in the hook. The hook card is painted from that
    spoken sentence. Numbers stay on the sync gate, not in this phrase.
    """
    title = str((narration or {}).get("title") or "")
    phrase = key_phrase_for(title)
    if not phrase:
        return []
    beats = (narration or {}).get("beats") or {}
    hook = str(beats.get("hook") or "")
    if phrase_in_text(hook, phrase):
        return []
    return [f"hook missing title term: {phrase}"]


NARR_SYSTEM = """You write the SPOKEN Hindi narration for a vertical finance Short for Indian traders
(retail to pro). This is ONE MINI-LESSON, not four separate facts. The viewer should be able to
answer these four questions in order: "आज क्या सीख रहा हूँ?", "इसे पहचानूँ कैसे?", "इसका मतलब क्या है?",
और "आगे traders क्या check करते हैं?" Never jump backward, repeat an explanation, or introduce a
new concept before the previous one is understood.

TEACHING VOICE:
- Adult trader talking to another adult. Calm, direct Hinglish. No cheerleading, no "दोस्तों",
  no baby-talk, no hype.

LANGUAGE — simple everyday Hinglish, the way Indian traders talk on a desk:
- Write spoken words, not shuddh Hindi. Do not use हद, पूँजी, क्षमता, तय करो, or बैठती है.
- Use the words traders use: limit, capital, loss, stop loss, entry, trade, profit.
- In the script say लिमिट, कैपिटल, लॉस, स्टॉप लॉस, एंट्री, ट्रेड, प्रॉफिट, डेली लॉस लिमिट.
- Say फिक्स करो when the lesson is to write the limit down. That is not an order to buy or sell.
- A 2% daily loss limit is the whole day's TOTAL loss, not the loss of one trade. Say that.
- Bridges, when they help: "अब इसे पहचानो", "मतलब ये है", "अब आगे".
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
   The hook beat targets 25 words and stays short; only the opening sentence is the punch.
2) PEHCHAN — "इसे chart पर पहचानेंगे कैसे?" Point to the exact visible structure/rules shown on screen.
   Use concrete features, not generic definitions.
3) MATLAB — "इस pattern/strategy का मतलब क्या है?" Connect those exact features to buyer/seller force,
   payoff, or market logic. Explain cause → effect. Do not introduce a second setup.
4) KAREIN — "अब traders आमतौर पर क्या check करते हैं?" Show confirmation, entry context, SL/risk,
   or limitation only after the meaning is established. Refer back to the SAME setup.

Do not instruct the narrator. Never say बोलो, बताओ, कार्ड, card, script, or beat.
Do not use filler openers such as "अब इसे chart पर पहचानें", "इन features का मतलब समझिए",
or "अब आगे traders क्या check करते हैं". Speak the lesson itself.

CONTINUITY RULES:
- Each beat must answer the next question in the sequence. Never repeat the hook in later beats.
- Use explicit bridge words where natural: "अब इसे पहचानो", "मतलब ये है", "अब आगे".
- Narration must describe the exact visual shown in that beat. If the screen highlights
  three candles, explain those three candles; if it shows SL, explain SL. Never talk about
  a chart element that is not on screen.
- Every beat should add new information. The four beats together should feel like one lesson,
  not four separate facts.
- For pattern/strategy topics, use this order: setup → recognition rules → market meaning →
  confirmation/risk. Do not show or explain entry/SL before the setup and meaning are established.
- Each beat: 3-4 short sentences, preferably under 14 words per sentence.
- Hit the per-beat targets in WORD COUNTS. Extra words must add concrete teaching detail, not filler.
- Never stretch audio artificially just to hit a target duration.
- Do not compress a beat into a slogan; each beat must teach its assigned step.
- Use ONLY numbers that appear in the topic data given to you. Never invent a live price, date, or return.
  On an evergreen topic with no market data, an illustrative figure is allowed only in the same
  sentence as उदाहरण, example, or मिसाल.
- Not a quiz. Do not ask the viewer to pick options.
- SEBI rules: never tell anyone to buy/sell/hold a specific stock, fund or product; no targets,
  no tips; never promise returns. Patterns/indicators are probabilities, not sure signals.
- Describe, don't command: for SL / entry / confirmation say how traders usually do it
  ("आमतौर पर traders SL ... रखते हैं", "कई traders ... का इंतज़ार करते हैं"), never "ऐसा करो" as an order.
- Do not add a CTA or disclaimer inside the narration — the template adds them.
- If category is "REAL DATA": the first sentence of hook is ONLY the question (max 10 words);
  the rest of the hook may give neutral context but must not reveal the answer, and the hook beat
  stays on its short target. pehchan =
  the two prices with years. matlab = the multiple (गुना) once. karein = the final value once, then
  the balance line from facts. Say the final value only in karein, never earlier. Use the same
  per-beat targets and the same total band as every other topic; use extra words for concrete explanation,
  not filler. Write "लगाते" (not "लगते")."""


def word_count_rules():
    """Exact per-beat targets, the ±20% band, and a short JSON example."""
    lines = [
        "WORD COUNTS (this section is the rule; count words by splitting on spaces):",
    ]
    for beat, target in BEAT_WORD_TARGET.items():
        lo, hi = beat_word_window(target)
        lines.append(f"- {beat}: target exactly {target} words. Accept {lo}-{hi} (±{round(BEAT_WORD_TOLERANCE * 100)}%).")
    total_lo, total_hi = TOTAL_WORDS
    target_lo, target_hi = TOTAL_WORD_TARGET
    lines.append(
        f"- total: target {target_lo}-{target_hi} words. Accept {total_lo}-{total_hi}."
    )
    hook_target = BEAT_WORD_TARGET["hook"]
    hook_lo, hook_hi = beat_word_window(hook_target)
    lines.append(
        f"The hook stays short. Its target is {hook_target} words and it must land in {hook_lo}-{hook_hi} words."
    )
    lines.append(
        "Every beat, the hook included, must contain "
        f"a digit, or one of these exact words: ATR, SL, RSI, entry, target, wick, range, support, resistance, high, low, quantity, बॉडी, नियम, कदम, गलती, उदाहरण, रेखा, औसत, पॉइंट, गुना, मात्रा (spell it exactly; synonyms like कैंडल or चार्ट do not count). A vague beat fails."
    )
    lines.append(
        "On an evergreen topic with no market data, a new figure is allowed only in the "
        "same sentence as उदाहरण, example, or मिसाल, and it is an illustration, not a live price. "
        "REAL DATA and a fetched series may use only the numbers on the card."
    )
    lines.append(
        "Short example of the JSON shape (write the real lesson, and hit these counts):"
    )
    t = BEAT_WORD_TARGET
    lines.append(
        '{"title":"1:2 सेटअप और फिर भी घाटा","hook_line":"1:2 सेटअप फिर भी घाटा क्यों?",'
        f'"beats":{{"hook":"<exactly {t["hook"]} words, first sentence is hook_line>",'
        f'"pehchan":"<exactly {t["pehchan"]} words>","matlab":"<exactly {t["matlab"]} words>",'
        f'"karein":"<exactly {t["karein"]} words>"}}}}'
    )
    return "\n".join(lines)


NARR_SYSTEM = NARR_SYSTEM + "\n\n" + word_count_rules()


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
    # The card, this script, and the burned cue share format_figure.
    atr = market_data.format_figure(lesson["atr"], 1)
    entry = market_data.format_figure(lesson["entry"], 1)
    multiple = market_data.format_figure(lesson["multiple"], 1)
    distance = market_data.format_figure(lesson["distance"], 1)
    sl = market_data.format_figure(lesson["sl"], 1)
    target = market_data.format_figure(lesson["target"], 1)
    risk = market_data.format_figure(lesson["risk"], 0)
    hook_line = "तंग एसएल बार-बार कटता क्यों है?"
    beats = {
        "hook": (
            f"{hook_line} भाव की सामान्य हलचल तंग रेखा को काट देती है। "
            f"एटीआर इसी हलचल को नापता है।"
        ),
        "pehchan": (
            f"नियम यह है। एटीआर पिछले {lesson['period']} कैंडल की औसत हलचल है। "
            f"एचडीएफसी बैंक का एटीआर {atr} है।"
        ),
        "matlab": (
            f"आखिरी भाव {entry} एंट्री है। "
            f"{multiple} गुना {atr} बराबर {distance} पॉइंट। "
            f"एसएल {sl}। ऊपर {target}।"
        ),
        "karein": (
            f"गलती तंग एसएल है। {atr} की हलचल में तंग रेखा कटती है। "
            f"दूरी {distance} हो और मात्रा {lesson['qty']} हो तो जोखिम {risk} रुपये रहता है। "
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
            hook_seed, 22, 26,
            [
                "अब इसे पहचानने का तरीका समझिए।",
                "आगे की जानकारी इसी विषय के संदर्भ में रहेगी।",
            ],
        ),
        "pehchan": _fit_words(
            f'{src["pehchan"][0]}। {src["pehchan"][1]}।',
            29, 33,
            [
                "अब इसे पहचानें।",
                "दिख रहे हिस्से को एक साथ देखें।",
                "पहचान का मतलब है कि पहले वही structure साफ समझें।",
                "इसके बाद ही उसका market meaning पढ़ना आसान होता है।",
            ],
        ),
        "matlab": _fit_words(
            f'{src["matlab"][0]}। {src["matlab"][1]}।',
            36, 41,
            [
                "इन बातों का मतलब समझिए।",
                "अब setup के कारण और असर को जोड़कर देखें।",
                "यह मतलब इसी दिए गए तथ्य तक है।",
                "किसी अतिरिक्त अनुमान को यहाँ नहीं जोड़ा जा रहा।",
                "इसलिए meaning को context के साथ पढ़ना जरूरी है।",
            ],
        ),
        "karein": _fit_words(
            f'{src["karein"][0]}। {src["karein"][1]}।',
            32, 36,
            [
                "अब आगे जाँच क्या होती है।",
                "पहले confirmation और risk की सीमा समझी जाती है।",
                "किसी setup को अकेले देखकर फैसला तय नहीं होता।",
                "स्क्रीन पर मौजूद जानकारी इसकी practical सीमा भी दिखाती है।",
                "अलग से पूरी जानकारी और जोखिम देखना जरूरी है।",
            ],
        ),
    }
    # Beat ranges can all sit near their floor and leave the lesson under 112 words.
    # A whole sentence often does not fit the last 1–2 words of room, which is
    # how a lesson stopped at 159. After the sentences, add one Hindi word at a
    # time into whichever beat still has room. Never past a beat cap or 126.
    caps = {"hook": 26, "pehchan": 33, "matlab": 41, "karein": 36}
    fillers = (
        ("karein", "यह सीमा याद रखने लायक है।"),
        ("matlab", "इसीलिए संदर्भ के साथ पढ़ें।"),
        ("pehchan", "यही पहचान का आधार है।"),
        ("hook", "इसी सवाल को आगे खोलते हैं।"),
    )
    guard = 0
    while sum(len(beats[b].split()) for b in BEATS) < 112 and guard < 8:
        guard += 1
        added = False
        for beat, sentence in fillers:
            extra = sentence.split()
            words = beats[beat].split()
            if len(words) + len(extra) <= caps[beat] and sum(len(beats[b].split()) for b in BEATS) + len(extra) <= 126:
                beats[beat] = " ".join(words + extra)
                added = True
                break
        if not added:
            break
    pad = "यही आगे स्पष्ट सीमा संदर्भ सवाल पहचान मतलब जाँच".split()
    pad_i = 0
    guard = 0
    while sum(len(beats[b].split()) for b in BEATS) < 112 and guard < 40:
        guard += 1
        placed = False
        for beat in ("karein", "matlab", "pehchan", "hook"):
            words = beats[beat].split()
            total = sum(len(beats[b].split()) for b in BEATS)
            if len(words) >= caps[beat] or total >= 126:
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
    """TTS runs only for a gated script. A topic-card fallback never spends voice."""
    if (
        source in ("llm", "cache", "outline", "approved")
        and isinstance(n, dict)
        and not n.get("narration_fallback")
        and str(n.get("source") or source).strip().lower() != "fallback"
    ):
        return
    block_fallback(
        n if isinstance(n, dict) else {},
        "narration is not an LLM script that passed the hook/teaching gate; refusing TTS and upload",
    )


def _allowed_number_list(topic):
    nums = hook_check.teaching_allowed_numbers(topic)

    def key(value):
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (1, str(value))

    return sorted(nums, key=key)


def _allowed_number_text(topic):
    return ", ".join(_allowed_number_list(topic))


def _number_beats(n, topic, order):
    """Beats that contain a number the gate will reject."""
    beats = (n or {}).get("beats") or {}
    if (topic or {}).get("_live_short"):
        return hook_check.beats_with_number_faults(beats, topic, order)
    extra = hook_check.unsourced_numbers(
        " ".join(str(beats.get(beat) or "") for beat in order), topic)
    if not extra:
        return []
    found = []
    for beat in order:
        if hook_check.extract_numbers(str(beats.get(beat) or "")) & extra:
            found.append(beat)
    return found


def _split_sentences(text):
    text = " ".join(str(text or "").split())
    if not text:
        return []
    parts = re.split(r"(?<=[।!?])\s+", text)
    out = []
    for part in parts:
        out.extend(bit.strip() for bit in re.split(r"(?<=\.)\s+", part) if bit.strip())
    return out or [text]


def _join_sentences(sentences):
    return " ".join(bit.strip() for bit in sentences if str(bit).strip())


def _clause_pieces(text):
    """Clauses and sentences. A comma, danda, or question mark is a boundary."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    parts = re.split(r"(?<=[,،;:—–-])\s+|(?<=[।!?？])\s+", text)
    return [part.strip() for part in parts if part and part.strip()]


def _hook_question_and_rest(text, hook_line):
    """The opening question, then whatever follows it."""
    text = " ".join(str(text or "").split())
    line = " ".join(str(hook_line or "").split()).rstrip("।")
    if line and text.startswith(line):
        return line, text[len(line):].strip()
    match = re.search(r"[?？]", text)
    if not match:
        parts = _split_sentences(text)
        if len(parts) <= 1:
            return text, ""
        return parts[0], _join_sentences(parts[1:])
    return text[:match.end()].strip(), text[match.end():].strip()


_FILLER_SNIPS = (
    "यह कदम नियम है",
    "अगला कदम नियम है",
    "यह नियम है",
    "अगला कदम",
    "कदम नियम",
)


def _is_filler_sentence(sentence):
    body = str(sentence or "").strip().strip("।!?？. ")
    return any(snip in body for snip in _FILLER_SNIPS)


def _terms_in_sentence(sentence, terms):
    if not terms:
        return []
    import sync_gate
    blob = set(sync_gate.tokens(sentence))
    return [term for term in terms if sync_gate.term_covered(term, blob)]


def _sole_title_carrier(parts, index, terms):
    """True when dropping this sentence would drop the only copy of a title term."""
    covered = _terms_in_sentence(parts[index], terms)
    if not covered:
        return False
    others = set()
    for j, part in enumerate(parts):
        if j == index:
            continue
        others.update(_terms_in_sentence(part, terms))
    return any(term not in others for term in covered)


def fit_live_hook(text, lo, hi, hook_line="", title=""):
    """Keep whole sentences. A comma is not a cut.

    The trim lands inside the window only by dropping a sentence that already
    ended at ।, ?, or !. A filler goes before a sentence that is the only
    copy of a title term. "यही नुकसान का गणित है।" stays. "premium देना खर्च है,"
    stays with the rest of its sentence. If the only boundary is a comma, or
    the only way under the cap is to drop that title sentence, the hook is
    left for a repair.
    """
    text = " ".join(str(text or "").split())
    words_n = len(text.split())
    if not text or lo <= words_n <= hi:
        return text
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return text
    question, _rest = _hook_question_and_rest(text, hook_line)
    if len(question.split()) > hi and len(_split_sentences(question)) <= 1:
        return text
    phrase = key_phrase_for(title) if title else ""

    def count(parts):
        return len(_join_sentences(parts).split())

    kept = list(sentences)

    def drop_pass(fillers_only):
        nonlocal kept
        progressed = True
        while progressed and count(kept) > hi:
            progressed = False
            for index in range(len(kept) - 1, -1, -1):
                if index == 0:
                    continue
                if phrase and phrase_in_text(kept[index], phrase):
                    continue
                filler = _is_filler_sentence(kept[index])
                if fillers_only and not filler:
                    continue
                trial = kept[:index] + kept[index + 1:]
                if count(trial) < lo:
                    continue
                kept = trial
                progressed = True
                break

    drop_pass(True)
    drop_pass(False)
    if kept and lo <= count(kept) <= hi and count(kept) < words_n:
        return _join_sentences(kept)
    return text


def _apply_hook_fit(narration, topic):
    """Deterministic hook fit. No model call. False when the hook is unchanged."""
    if not (topic or {}).get("_live_short") or (topic or {}).get("_atr"):
        return False
    if not isinstance(narration, dict):
        return False
    order, targets, _, _ = _lesson(topic)
    beats = narration.setdefault("beats", {})
    lo, hi = beat_word_window(targets["hook"], LIVE_BEAT_TOLERANCE)
    current = str(beats.get("hook") or "")
    title = narration.get("title") or (topic or {}).get("topic") or ""
    fitted = fit_live_hook(current, lo, hi, narration.get("hook_line"), title)
    if fitted == " ".join(current.split()):
        return False
    beats["hook"] = fitted
    note = f"hook: kept the first question and landed at {len(fitted.split())} words"
    notes = narration.setdefault("_trim_notes", [])
    if isinstance(notes, list):
        notes.append(note)
    print(f"[trim] {note}", flush=True)
    topic["_spoken_beats"] = {beat: str(beats.get(beat) or "") for beat in order}
    return True


def _sentence_protected(beat, sentence, index, example, phrase=""):
    """The hook keeps its opening line. The code-written udaharan example is never cut.

    A sentence that carries the title's key phrase is never cut for length.
    """
    if phrase and phrase_in_text(sentence, phrase):
        return True
    if beat == "hook" and index == 0:
        return True
    if beat != "udaharan":
        return False
    core = str(example or "").strip().strip("।")
    if not core:
        return False
    body = core
    if body.startswith("उदाहरण:"):
        body = body[len("उदाहरण:"):].strip()
    elif body.startswith("उदाहरण"):
        body = body[len("उदाहरण"):].strip(" :")
    return core in sentence or (len(body) >= 12 and body in sentence)


def _drop_index(sentences, beat, example, lo, phrase=""):
    """Last sentence that can be removed without cutting a protected line or falling under lo."""
    total = len(_join_sentences(sentences).split())
    for index in range(len(sentences) - 1, -1, -1):
        if _sentence_protected(beat, sentences[index], index, example, phrase):
            continue
        trial = _join_sentences(sentences[:index] + sentences[index + 1:])
        if len(trial.split()) < lo:
            continue
        if len(trial.split()) < total:
            return index
    return None


def _expansion_sentences(topic):
    """Fact lines that can lengthen a short beat without a new number."""
    sentences = []
    blobs = []
    for fact in (topic or {}).get("facts") or []:
        blobs.append(str(fact))
    for beat in BEATS:
        pair = ((topic or {}).get("beats") or {}).get(beat)
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            blobs.append(str(pair[1]))
    for raw in blobs:
        text = fold_roman_words(" ".join(raw.split()))
        if not text or "शिक्षा है" in text or "सलाह नहीं" in text:
            continue
        if latin_misread_words(text) or _number_dump(text):
            continue
        if not text.endswith("।"):
            text = text.rstrip(".") + "।"
        if hook_check.live_number_faults({"karein": text}, topic, ("karein",)):
            continue
        if not scene_pace.segment_is_concrete(text) and not re.search(r"\d", text):
            continue
        if text not in sentences:
            sentences.append(text)
    return sentences


def expand_short_live_beats(narration, topic):
    """Bring a short live beat up to its minimum with the topic's own facts.

    The word window stays where it is. A beat that still cannot reach the
    minimum, because no fact fits, is left short and the gate still fails.
    """
    if not isinstance(narration, dict):
        return narration
    if not (topic or {}).get("_live_short") or (topic or {}).get("_atr"):
        return narration
    order, targets, (_lo_total, hi_total), _band = _lesson(topic)
    beats = narration.setdefault("beats", {})
    sentences = _expansion_sentences(topic)
    ranges = {beat: beat_word_window(targets[beat], LIVE_BEAT_TOLERANCE) for beat in order}
    notes = narration.setdefault("_trim_notes", [])
    used = set()
    for beat in order:
        if beat in hook_check.TEACHING_BEATS:
            continue
        lo, hi = ranges[beat]
        words = str(beats.get(beat) or "").split()
        if len(words) >= lo:
            continue
        for sentence in sentences:
            if sentence in used:
                continue
            extra = sentence.split()
            total = sum(len(str(beats.get(name) or "").split()) for name in order)
            if len(words) + len(extra) > hi or total + len(extra) > hi_total:
                continue
            if sentence.strip("।") and sentence.strip("।") in str(beats.get(beat) or ""):
                continue
            words.extend(extra)
            beats[beat] = " ".join(words)
            used.add(sentence)
            note = f"{beat}: expanded with a topic fact to {len(words)} words"
            if isinstance(notes, list):
                notes.append(note)
            print(f"[trim] {note}", flush=True)
            if len(words) >= lo:
                break
    topic["_spoken_beats"] = {beat: str(beats.get(beat) or "") for beat in order}
    return narration


def apply_live_draft(narration, topic, total_hi=None, locked=()):
    """Insert उदाहरण, then drop whole sentences from the end of long beats.

    The model is not asked to shorten a script that this pass can finish.
    A beat that is still outside its window, including a long hook, is repaired.
    `locked` beats are not used to shorten the total: a beat that would fall
    under its minimum is left alone and another beat is trimmed instead.
    """
    if not isinstance(narration, dict):
        return narration
    if (topic or {}).get("_live_short") and not (topic or {}).get("_atr"):
        fold_roman_beats(narration)
    order, targets, (lo_total, hi_total), _band = _lesson(topic)
    if total_hi is not None:
        hi_total = int(total_hi)
    beats = narration.setdefault("beats", {})
    example = hook_check.worked_example_sentence(topic)
    notes = []
    for beat in order:
        if beat not in hook_check.TEACHING_BEATS:
            continue
        sentences = _split_sentences(beats.get(beat) or "")
        changed = False
        for index, sentence in enumerate(sentences):
            if hook_check.extract_numbers(sentence) and "उदाहरण" not in sentence:
                sentences[index] = "उदाहरण " + sentence.strip()
                changed = True
        if changed:
            beats[beat] = _join_sentences(sentences)
            notes.append(f"{beat}: inserted उदाहरण at the start of the example sentence")
    ranges = {beat: beat_word_window(targets[beat], LIVE_BEAT_TOLERANCE) for beat in order}
    locked = {beat for beat in (locked or ()) if beat}
    phrase = key_phrase_for(
        narration.get("title") or (topic or {}).get("topic") or ""
    )

    def counts():
        return {beat: len(str(beats.get(beat) or "").split()) for beat in order}

    def trim_once(beat, honor_lock=False):
        if honor_lock and beat in locked:
            return False
        lo, _hi = ranges[beat]
        sentences = _split_sentences(beats.get(beat) or "")
        index = _drop_index(sentences, beat, example, lo, phrase)
        if index is None:
            return False
        dropped = sentences.pop(index)
        beats[beat] = _join_sentences(sentences)
        snippet = " ".join(str(dropped).split())
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        notes.append(f"{beat}: dropped '{snippet}'")
        return True

    # Per beat, shed trailing sentences until the ±30% window, or until another
    # drop would fall below the minimum or cut the protected line.
    guard = 0
    for beat in order:
        while counts()[beat] > ranges[beat][1] and guard < 80:
            guard += 1
            if not trim_once(beat):
                break
    while sum(counts().values()) > hi_total and guard < 80:
        guard += 1
        # Most room above the minimum first. A beat that cannot spare a
        # sentence without falling under its minimum is skipped.
        ranked = sorted(
            order,
            key=lambda beat: counts()[beat] - ranges[beat][0],
            reverse=True,
        )
        if not any(trim_once(beat, honor_lock=True) for beat in ranked):
            break
    for note in notes:
        print(f"[trim] {note}", flush=True)
    narration["_trim_notes"] = notes
    topic["_spoken_beats"] = {beat: str(beats.get(beat) or "") for beat in order}
    _apply_hook_fit(narration, topic)
    expand_short_live_beats(narration, topic)
    return narration


def drop_disallowed_sentences(narration, topic):
    """Drop sentences that still contain a rejected number. No model call.

    The hook's first sentence and the udaharan core calculation stay. The
    caller re-checks the word windows after this and blocks only if they fail.
    """
    if not isinstance(narration, dict):
        return False
    if not (topic or {}).get("_live_short") or (topic or {}).get("_atr"):
        return False
    order = _lesson(topic)[0]
    beats = narration.setdefault("beats", {})
    example = hook_check.worked_example_sentence(topic)
    notes = []
    changed = False
    for beat in order:
        sentences = _split_sentences(beats.get(beat) or "")
        kept = []
        beat_changed = False
        for index, sentence in enumerate(sentences):
            faults = hook_check.live_number_faults({beat: sentence}, topic, (beat,))
            protected = (beat == "hook" and index == 0) or _sentence_protected(
                beat, sentence, index, example)
            if faults and not protected:
                snippet = " ".join(str(sentence).split())
                if len(snippet) > 80:
                    snippet = snippet[:77] + "..."
                notes.append(
                    f"{beat}: dropped '{snippet}' (disallowed {', '.join(sorted(faults))})"
                )
                beat_changed = True
                changed = True
                continue
            kept.append(sentence)
        if beat_changed:
            beats[beat] = _join_sentences(kept)
    if not changed:
        return False
    bucket = narration.setdefault("_trim_notes", [])
    if isinstance(bucket, list):
        bucket.extend(notes)
    for note in notes:
        print(f"[trim] {note}", flush=True)
    topic["_spoken_beats"] = {beat: str(beats.get(beat) or "") for beat in order}
    return True


def narration_gate_failures(n, topic, extra=""):
    """Every hook/teaching failure string, including ones passed in from the last call.

    Self-review and the duration estimate are not recomputed here. When the
    previous gate already produced them, they arrive in `extra`.
    """
    fails = list(check_narration(n or {}, topic or {}))
    order = _lesson(topic)[0]
    vague = scene_pace.vague_beats((n or {}).get("beats"), order=order)
    if vague:
        fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
    for piece in re.split(r";\s*", str(extra or "")):
        piece = " ".join(piece.split())
        if piece and piece not in fails:
            fails.append(piece)
    return fails


def route_gate_failure(message, order, narration=None, topic=None):
    """Map one gate failure to the beats it names, or ['all'] for the whole script.

    A message that names a beat rewrites that beat. A message that names none
    is script-level, except a disallowed number, a forbidden word, or 'लगते',
    which are traced to the beat that contains them. 'sirf X me bolo' names
    the beat that must keep the value, so the earlier beats are rewritten.
    """
    text = " ".join(str(message or "").split())
    order = tuple(order or ())
    keeper = re.search(r"sirf '([A-Za-z_]+)' me bolo", text)
    if keeper and keeper.group(1) in order:
        early = [beat for beat in ("hook", "pehchan", "matlab") if beat in order and beat != keeper.group(1)]
        return early or [beat for beat in order if beat != keeper.group(1)]
    named = [beat for beat in order if re.search(rf"(?<![A-Za-z_]){re.escape(beat)}(?![A-Za-z_])", text)]
    if named:
        return named
    beats = ((narration or {}).get("beats") or {}) if isinstance(narration, dict) else {}
    if "ye numbers topic me nahi" in text:
        wanted = set()
        for match in re.finditer(r"'([^']*)'|\"([^\"]*)\"", text):
            token = match.group(1) if match.group(1) is not None else match.group(2)
            wanted |= hook_check.extract_numbers(token)
        found = [
            beat for beat in order
            if hook_check.extract_numbers(str(beats.get(beat) or "")) & wanted
        ]
        return found or ["all"]
    if text.startswith("mana shabd"):
        words = re.findall(r"'([^']+)'", text)
        found = [
            beat for beat in order
            if any(word.lower() in str(beats.get(beat) or "").lower() for word in words)
        ]
        return found or ["all"]
    if "लगते" in text:
        found = [beat for beat in order if "लगते" in str(beats.get(beat) or "")]
        return found or ["all"]
    return ["all"]


def beats_to_rewrite(n, topic, extra=""):
    """Beats the model may rewrite.

    A live short rewrites whatever the hook/teaching gate just failed. Every
    failure string maps to the beat it names, or to every beat when the
    failure is script-level. The loop does not stop while any failure remains.
    """
    order, targets, total_window, _band = _lesson(topic)
    if topic.get("_atr"):
        return list(BEATS)
    beats = (n or {}).get("beats") or {}
    counts = {b: len(str(beats.get(b) or "").split()) for b in order}
    if topic.get("_live_short"):
        fails = narration_gate_failures(n, topic, extra)
        if not fails:
            return []
        rewrite = []
        for message in fails:
            targets_for = route_gate_failure(message, order, n, topic)
            if "all" in targets_for:
                return list(order)
            for beat in targets_for:
                if beat in order and beat not in rewrite:
                    rewrite.append(beat)
        return rewrite or list(order)
    rewrite = []
    for beat in order:
        lo, hi = beat_word_window(targets[beat])
        text = str(beats.get(beat) or "").strip()
        if not text or not lo <= counts[beat] <= hi:
            rewrite.append(beat)
    total = sum(counts.values())
    if not (total_window[0] <= total <= total_window[1]) and not rewrite:
        if total > total_window[1]:
            rewrite = [b for b in order if counts[b] > targets[b]]
        else:
            rewrite = [b for b in order if counts[b] < targets[b]]
        if not rewrite:
            rewrite = list(order)
    fails = list(check_narration(n, topic))
    if extra:
        fails.append(str(extra))
    joined = " ".join(fails)
    for beat in order:
        if beat in joined and beat not in rewrite:
            rewrite.append(beat)
    for beat in scene_pace.vague_beats(beats, order=order):
        if beat not in rewrite:
            rewrite.append(beat)
    for beat in _number_beats(n, topic, order):
        if beat not in rewrite:
            rewrite.append(beat)
    if (topic or {}).get("_live_short"):
        for beat in hook_check.unlabelled_example_beats(beats, order):
            if beat not in rewrite:
                rewrite.append(beat)
    if fails and not rewrite:
        rewrite = list(order)
    return rewrite


def repair_instructions(draft, rewrite, topic=None, failures=None):
    """Tell the model the current count, the exact target, and the gate failure."""
    order, targets, _window, _band = _lesson(topic)
    counts = {b: len(str(((draft or {}).get("beats") or {}).get(b) or "").split()) for b in order}
    vague = set(scene_pace.vague_beats((draft or {}).get("beats"), order=order))
    how_many = "six" if order == LIVE_ORDER else "four"
    lines = [
        "The draft missed the word targets or a beat is vague.",
        "Rewrite ONLY the beats listed under REWRITE.",
        "Copy every KEEP beat verbatim, character for character.",
        f"Return one JSON object with title, hook_line, and all {how_many} beats.",
        "Keep sentence punctuation (। . ?) so the voice can pause. Do not write run-on lines.",
    ]
    if topic is not None:
        allowed = _allowed_number_text(topic)
        lines.append(
            f"Allowed numbers: {allowed}. "
            "A rewritten beat may use only these figures. Do not invent any other number."
        )
        lines.append(
            "In udaharan and galti, every number must be an allowed figure, "
            "a multiplier 1, 1.5, 2, or 3, or a result whose expression evaluates to it. "
            "A label does not make an invented number allowed. "
            "Real market data may use only the sourced figures."
        )
        if topic.get("_live_short"):
            lines.append(
                "Write every figure in a worked sentence such as "
                "उदाहरण: ₹2,00,000 का 2% = ₹4,000. "
                "Use this topic's own figures. A bare list such as "
                "उदाहरण: 98, 4000, 200000 is rejected. The gate accepts the worked sentence."
            )
            lines.append(
                "100−p is allowed only when the same sentence writes the subtraction, "
                "for example 100 − 40 = 60 or 100 में से 40. A bare complement is blocked."
            )
            lines.extend(_topic_fact_rules(topic))
            phrase = key_phrase_for(str((draft or {}).get("title") or ""))
            if phrase:
                lines.append(
                    "The hook must contain this key phrase in Devanagari, spoken and "
                    "on the hook card: " + phrase + ". "
                    "The karein does not have to repeat it. "
                    "Do not require function words such as कैसे, करें, तय, नियम, or घटे."
                )
            example = hook_check.worked_example_sentence(topic)
            if example and not _number_dump(example):
                lines.append(
                    "Use this udaharan sentence verbatim or near-verbatim: " + example
                )
    tol = LIVE_BEAT_TOLERANCE if (topic or {}).get("_live_short") else None
    gate_fails = list(failures) if failures is not None else (
        narration_gate_failures(draft, topic) if topic is not None else []
    )
    missing_terms = []
    for message in gate_fails:
        found = re.findall(r"missing title term:\s*(.+)", message)
        for term in found:
            if term not in missing_terms:
                missing_terms.append(term)
    if missing_terms:
        lines.append(
            "Missing title term: " + ", ".join(missing_terms) + ". "
            "Write that phrase in the hook."
        )
    for beat in order:
        target = targets[beat]
        lo, hi = beat_word_window(target, tol)
        current = counts[beat]
        text = str((draft.get("beats") or {}).get(beat) or "")
        if beat in rewrite:
            line = (
                f"REWRITE {beat}: current {current} words, target exactly {target} words "
                f"(accept {lo}-{hi})."
            )
            if beat == "hook" and order == LIVE_ORDER:
                line += (
                    " Keep the first question. Keep the topic and the allowed numbers. "
                    f"The hook must land in {lo}-{hi} words."
                )
            elif beat == "hook":
                line += f" The hook must be {lo}-{hi} words."
            if current < lo:
                line += (
                    " This beat is short. Add words until it lands in the window. "
                    "Use only the allowed numbers already on the topic. Do not invent a figure."
                )
            if beat in vague:
                line += (
                    " This beat is vague: it needs a concrete number, level, or step "
                    "the viewer can point at. Include a digit or one of these exact words: "
                    "ATR, SL, RSI, entry, target, wick, range, support, resistance, high, low, "
                    "quantity, बॉडी, नियम, कदम, गलती, उदाहरण, रेखा, औसत, पॉइंट, गुना, मात्रा."
                )
            if topic is not None and topic.get("_live_short"):
                bad = sorted(hook_check.live_number_faults({beat: text}, topic, (beat,)))
                if bad:
                    line += (
                        f" Disallowed numbers: {bad}. Remove every one of them. "
                        "Use only the allowed list."
                    )
            exact = [
                message for message in gate_fails
                if beat in route_gate_failure(message, order, draft, topic)
                or "all" in route_gate_failure(message, order, draft, topic)
            ]
            if exact:
                line += " Exact failure: " + " | ".join(exact)
            lines.append(line)
        else:
            lines.append(
                f"KEEP {beat} VERBATIM ({current} words; target {target}): {text}"
            )
    if "hook" not in rewrite:
        lines.append(f"KEEP hook_line VERBATIM: {draft.get('hook_line') or ''}")
        lines.append(f"KEEP title VERBATIM: {draft.get('title') or ''}")
    else:
        lines.append("hook_line must be the first sentence of the rewritten hook.")
    if topic is not None and hook_check.allows_example_numbers(topic):
        lines.append(
            "This is an evergreen topic with no market data. "
            "Do not present an example figure as a live price."
        )
    return "\n".join(lines)


def merge_repair(original, repaired, rewrite, topic=None):
    """Keep passing beats verbatim even if the repair rewrites them."""
    order = _lesson(topic)[0]
    out = {
        "title": str((original or {}).get("title") or ""),
        "hook_line": str((original or {}).get("hook_line") or ""),
        "beats": {
            b: str(((original or {}).get("beats") or {}).get(b) or "") for b in order
        },
    }
    got = repaired if isinstance(repaired, dict) else {}
    got_beats = got.get("beats") if isinstance(got.get("beats"), dict) else {}
    for beat in rewrite:
        text = str(got_beats.get(beat) or "").strip()
        if text:
            out["beats"][beat] = text
    if "hook" in rewrite:
        title = str(got.get("title") or "").strip()
        hook_line = str(got.get("hook_line") or "").strip()
        if title:
            out["title"] = title
        if hook_line:
            out["hook_line"] = hook_line
    return out


def _usable_narration(n, topic=None):
    """A real script. An empty body or a timeout verdict is not one."""
    if not isinstance(n, dict):
        return False
    if n.get("ai_verdict_valid") is False and "beats" not in n:
        return False
    beats = n.get("beats")
    if not isinstance(beats, dict):
        return False
    order = _lesson(topic)[0]
    return any(str(beats.get(b) or "").strip() for b in order)


def _topic_fact_rules(topic):
    """Facts the model keeps getting backwards. Empty for every other topic."""
    lines = []
    if hook_check._is_loss_recovery_topic(topic):
        lines.append(
            "The recovery percent is computed on the smaller amount left after the loss, "
            "not on the money that was lost. Say that वापसी छोटे हुए पैसे पर गिनी जाती है. "
            "Do not say the loss itself is counted on the smaller money."
        )
        lines.append(
            "Do not say मेहनत दुगनी or any vague double. A 20% loss needs 25%, not double. "
            "दुगनी is allowed only in the same sentence as the 50% loss that needs 100%."
        )
    if hook_check._is_option_breakeven_topic(topic):
        lines.append(
            "On a call, between the strike and the breakeven only part of the premium is lost. "
            "The full premium is lost only at or below the strike. "
            "Do not say that below the breakeven the money is already gone."
        )
    return lines


def live_narration_system(topic):
    """Live prompt. Four beats by default; six only when live_beats is six."""
    if four_live(topic):
        return four_live_system(topic)
    _order, targets, (lo, hi), (target_lo, target_hi) = _lesson(topic)
    wps = voice_wps(_topic_voice(topic))
    lines = [
        "LIVE SHORT OVERRIDE. This replaces the four-beat word counts above.",
        "Beats, in order: hook, pehchan, matlab, udaharan, galti, karein.",
        "hook = the problem. The punchy hook_line is on screen from the first frame.",
        "pehchan = how to recognise it. matlab = what it means.",
        "udaharan = one worked example. galti = one common mistake and the fix.",
        "Use this udaharan sentence verbatim or near-verbatim:",
        hook_check.worked_example_sentence(topic),
        "In udaharan and galti a number may be one of the allowed figures, a multiplier "
        "1, 1.5, 2, or 3, or the result of an expression written with those values. "
        "The result is accepted only when the expression evaluates to it. "
        "Any other number is blocked. उदाहरण does not make an invented number legal.",
        "Every such number must sit in a sentence that contains उदाहरण.",
        "Write that sentence as a worked example such as "
        "उदाहरण: ₹2,00,000 का 2% = ₹4,000, using this topic's own figures. "
        "A bare list such as उदाहरण: 98, 4000, 200000 is rejected. "
        "The gate accepts the worked sentence.",
        "The four-beat evergreen permission to add a labelled figure does not apply.",
        "A real market price may use only the sourced figures. Multipliers do not apply there.",
        f"Allowed numbers for udaharan and galti: {_allowed_number_text(topic)}.",
        "karein = the confirmation or risk check, then one takeaway. Do not speak a CTA.",
        "Keep sentence punctuation (। . ?) so the voice can pause. Do not write run-on lines.",
        f"Pace coefficient: {wps:.2f} words/second. Target {target_lo}-{target_hi} words "
        "(about 75-90 seconds). Aim for about 155 words.",
        "100−p is allowed only when the same sentence writes the subtraction, "
        "for example 100 − 40 = 60 or 100 में से 40. A bare complement is blocked. "
        "A sum, difference, product, or ratio of two topic numbers is allowed "
        "only when the same sentence writes that arithmetic and the result matches. "
        "On a loss-recovery lesson, recovery must be L/(1−L). Any other pair is blocked. "
        "Any other new number is blocked.",
        *_topic_fact_rules(topic),
        "Spoken audio must land in 70-95 seconds.",
        f"The script may land in {lo}-{hi} words. A long beat is shortened in code. Do not pad.",
    ]
    for beat, target in targets.items():
        blo, bhi = beat_word_window(target, LIVE_BEAT_TOLERANCE)
        lines.append(f"- {beat}: target about {target} words. Accept {blo}-{bhi} (±30%).")
    example = ", ".join(
        f'"{beat}":"<exactly {target} words{"" if beat != "hook" else ", first sentence is hook_line"}>"'
        for beat, target in targets.items()
    )
    lines.append(
        '{"title":"सेटअप का नियम","hook_line":"यह सेटअप गलत क्यों?",'
        f'"beats":{{{example}}}}}'
    )
    return NARR_SYSTEM + "\n\n" + "\n".join(lines)


def four_live_system(topic):
    """Four-beat live prompt. ~55s on Rudra. Same number and leak gates."""
    _order, targets, (lo, hi), (target_lo, target_hi) = _lesson(topic)
    wps = voice_wps(_topic_voice(topic))
    sec_lo, sec_hi = FOUR_LIVE_VOICE_SECONDS
    lines = [
        "LIVE SHORT OVERRIDE. Four beats, not six. This replaces the word counts above.",
        "Beats, in order: hook, pehchan, matlab, karein.",
        "hook = the problem. pehchan = how to recognise it. matlab = one worked example.",
        "karein = the confirmation or risk check, then one takeaway. Do not speak a CTA.",
        "Do not invent a number. Do not instruct the narrator.",
        "Never mention कार्ड, card, script, or beat. Never say बोलो or बताओ.",
        f"Allowed numbers: {_allowed_number_text(topic)}.",
        "Every number in the worked example sits in a sentence such as "
        "उदाहरण: ₹2,00,000 का 2% = ₹4,000, using this topic's own figures. "
        "A bare list such as उदाहरण: 98, 4000, 200000 is rejected. "
        "The gate accepts the worked sentence.",
        "100−p is allowed only when the same sentence writes the subtraction, "
        "for example 100 − 40 = 60 or 100 में से 40. A bare complement is blocked. "
        "A sum, difference, product, or ratio of two topic numbers is allowed "
        "only when the same sentence writes that arithmetic and the result matches. "
        "On a loss-recovery lesson, recovery must be L/(1−L). Any other pair is blocked.",
        "The hook must contain the title's main key phrase in Devanagari, spoken and "
        "on the hook card, for example डेली लॉस लिमिट. "
        "Do not require that phrase in karein. "
        "Do not require function words such as कैसे, करें, तय, नियम, or घटे. "
        "A missing phrase is named in the repair, such as hook missing title term: डेली लॉस लिमिट.",
        *_topic_fact_rules(topic),
        f"Pace coefficient: {wps:.2f} words/second. Target {target_lo}-{target_hi} words "
        f"(about 52-57 seconds on Rudra, aiming at 59 seconds or less). "
        f"The script may land in {lo}-{hi} words.",
        f"Spoken audio must land in {sec_lo:.0f}-{sec_hi:.0f} seconds.",
        "Keep sentence punctuation (। . ?) so the voice can pause.",
    ]
    for beat, target in targets.items():
        blo, bhi = beat_word_window(target, LIVE_BEAT_TOLERANCE)
        lines.append(f"- {beat}: target about {target} words. Accept {blo}-{bhi}.")
    return NARR_SYSTEM + "\n\n" + "\n".join(lines)


def topic_key_figure(topic):
    """A figure already written on the card, for the vague-beat fallback."""
    blob = hook_check._topic_blob(topic)
    percent = re.search(r"\d[\d,]*(?:\.\d+)?\s*%", blob)
    if percent:
        return re.sub(r"\s+", "", percent.group(0))
    signed = re.search(r"[+\-]?\d[\d,]*(?:\.\d+)?R\b", blob)
    if signed:
        return signed.group(0)
    allowed = [number for number in hook_check.allowed_numbers(topic) if number not in {"1", "2", "3"}]
    if not allowed:
        allowed = list(hook_check.allowed_numbers(topic))

    def sort_key(value):
        try:
            return (0, -len(str(value)), float(value))
        except (TypeError, ValueError):
            return (1, 0, str(value))

    allowed.sort(key=sort_key)
    return allowed[0] if allowed else ""


def narration(topic, c, spec=None):
    market_data.sync_topic_numbers(topic)
    if _live_lesson(c) and not topic.get("_atr"):
        topic["_live_short"] = True
        topic["_voice"] = dict((c or {}).get("voice") or {})
    if topic.get("_live_short") and not topic.get("_atr") and not topic.get("_live_beats"):
        topic["_live_beats"] = resolve_live_beats(topic)
    if str((topic or {}).get("key") or "") == "aaj_ka_nifty":
        import nifty_day
        try:
            n = nifty_day.narration_from_session(topic)
        except FileNotFoundError as exc:
            die(f"Nifty session missing; refusing a DeepSeek script: {exc}")
        fails = check_narration(n, topic)
        vague = scene_pace.vague_beats(n.get("beats"), order=_lesson(topic)[0])
        if vague:
            fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
        if fails:
            die("Nifty narration failed its gate: " + "; ".join(fails))
        n["source"] = "template"
        n["narration_fallback"] = False
        (RUN / "narration.json").write_text(
            json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
        ok("narration: fixed Nifty template, no DeepSeek script")
        return n, "template"
    import approved_bank
    if approved_bank.active() or topic.get("_approved_bank"):
        try:
            n = approved_bank.narration_for(topic)
        except FileNotFoundError as exc:
            die(f"approved script missing; refusing a DeepSeek script: {exc}")
        topic["_approved_bank"] = True
        topic["_live_short"] = True
        topic["_live_beats"] = "plan" if n.get("plan") else "four"
        topic["_voice"] = {"words_per_second": 1.9}
        topic["_spoken_beats"] = dict(n["beats"])
        topic["facts"] = list(n.get("facts") or topic.get("facts") or [])
        fails = check_narration(n, topic)
        vague = scene_pace.vague_beats(n.get("beats"), order=_lesson(topic)[0])
        if n.get("plan"):
            vague = [beat for beat in vague if beat != "cta"]
        if vague:
            fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
        if fails:
            die("approved narration failed its gate: " + "; ".join(fails))
        n["source"] = "approved"
        n["narration_fallback"] = False
        (RUN / "narration.json").write_text(
            json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
        ok("narration: approved bank script, no DeepSeek")
        return n, "approved"
    approved = _approved_script(topic)
    if approved is not None:
        if topic.get("_live_short") and not topic.get("_atr"):
            approved["live_beats"] = resolve_live_beats(topic)
        (RUN / "narration.json").write_text(
            json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
        ok("narration: approved dry-run script, no DeepSeek and no trim")
        return approved, "approved"
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
        n["source"] = "outline"
        (RUN / "narration.json").write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
        ok("narration: calm ATR script")
        return n, "outline"
    cache = RUN / "narration.json"
    if cache.exists():
        try:
            n = json.loads(cache.read_text(encoding="utf-8"))
            if n.get("narration_fallback") or str(n.get("source") or "").strip().lower() == "fallback":
                block_fallback(
                    n,
                    "cached narration is a topic-card fallback, not an LLM script that passed the hook/teaching gate",
                )
            if topic.get("_live_short") and not topic.get("_atr"):
                n = apply_live_draft(n, topic)
            fails = check_narration(n, topic)
            content = _content_ok(topic, n, spec)
            # Only an LLM pass is reusable. A file without that mark is regenerated.
            if not fails and content["passed"] and n.get("source") == "llm" and not n.get("narration_fallback"):
                if topic.get("_live_short") and not topic.get("_atr"):
                    n["live_beats"] = resolve_live_beats(topic)
                ok("narration cache reused — no LLM credit")
                return n, "cache"
        except SystemExit:
            raise
        except Exception as e:
            print(f"[cache] narration invalid, regenerating: {e}")
    user = {"topic": topic["topic"], "category": topic["cat"],
            "on_screen": {b: {"headline": h, "sub": s} for b, (h, s) in topic["beats"].items()},
            "facts": topic["facts"],
            "allowed_numbers": _allowed_number_list(topic),
            "udaharan_sentence": hook_check.worked_example_sentence(topic)}
    msg = json.dumps(user, ensure_ascii=False)
    draft = None
    repair_done = False
    repairs = 0
    last = ""
    calls = 0
    empty_tries = 0
    model = c["models"]["script_model"]

    def _empty(reason):
        nonlocal last, empty_tries
        last = reason
        empty_tries += 1
        print(f"[retry] narration call {calls}/{NARRATION_CALL_CAP}: {last}")
        if empty_tries >= EMPTY_NARRATION_TRIES:
            print(f"[skip] DeepSeek stayed empty after one retry: {last}")
            raise NarrationSkip(last)

    def _gate(candidate):
        fails = check_narration(candidate, topic)
        vague = scene_pace.vague_beats(candidate.get("beats"), order=_lesson(topic)[0])
        if vague:
            fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
        content = _content_ok(topic, candidate, spec) if not fails else None
        if content and not content["passed"]:
            fails.append("self-review below bar: " + content["summary"])
        return fails

    def _accept(script, calls_used):
        ok(f"narration OK (call {calls_used})")
        script["review_attempts"] = calls_used
        script["narration_fallback"] = False
        script["source"] = "llm"
        if topic.get("_live_short") and not topic.get("_atr"):
            script["live_beats"] = resolve_live_beats(topic)
        cache.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        return script, "llm"

    # Call 1 is a full script. Later calls rewrite only the beats the gate
    # named. An empty body never becomes the topic-card template. A live
    # short uses all 4 calls while any failure remains. Four-beat scripts
    # still stop after one contentful repair.
    while calls < NARRATION_CALL_CAP:
        rewrite = None
        if draft is not None and not repair_done:
            rewrite = beats_to_rewrite(draft, topic, last)
            if topic.get("_live_short") and not topic.get("_atr") and not rewrite:
                pending = narration_gate_failures(draft, topic, last)
                if not pending:
                    if not _gate(draft):
                        return _accept(draft, calls)
                rewrite = list(_lesson(topic)[0])
            elif not rewrite:
                break
            user_msg = msg + "\n\n" + repair_instructions(
                draft, rewrite, topic, failures=narration_gate_failures(draft, topic, last),
            )
            kind = "repair"
        else:
            extra = ""
            if last:
                extra = (
                    "\n\nPrevious response was empty, timed out, or was not a script. "
                    "Return the JSON now. Do not leave beats out."
                )
            user_msg = msg + extra
            kind = "full"
        calls += 1
        try:
            system = live_narration_system(topic) if topic.get("_live_short") else NARR_SYSTEM
            n = claude_json(
                system, user_msg, model=model, max_tokens=2200,
                retries=1, attempts=1,
            )
        except SystemExit:
            raise
        except NarrationSkip:
            raise
        except Exception as e:  # noqa: BLE001
            _empty(f"empty or timeout: {e}")
            continue
        if not _usable_narration(n, topic):
            _empty("empty or timeout response")
            continue
        empty_tries = 0
        if kind == "repair":
            n = merge_repair(draft, n, rewrite, topic)
            repairs += 1
            # Live beats that stay outside the window get another repair
            # inside the same 4-call budget. Four-beat scripts stop after
            # NARRATION_STATIC_REPAIRS contentful repairs (the model often
            # overshoots the word window once and lands it on the next try).
            if not (topic.get("_live_short") and not topic.get("_atr")):
                if repairs >= NARRATION_STATIC_REPAIRS:
                    repair_done = True
        if not topic.get("_atr"):
            # The model often overshoots a beat by a sentence. Dropping the
            # trailing sentence in code is cheaper and safer than another call.
            n = apply_live_draft(n, topic)
        draft = n
        fails = _gate(n)
        if (
            fails
            and kind == "repair"
            and topic.get("_live_short")
            and not topic.get("_atr")
            and drop_disallowed_sentences(n, topic)
        ):
            expand_short_live_beats(n, topic)
            draft = n
            fails = _gate(n)
        if not fails:
            return _accept(n, calls)
        last = "; ".join(fails)
        print(f"[retry] narration call {calls}/{NARRATION_CALL_CAP}: {last}")
        if (
            kind == "repair"
            and not (topic.get("_live_short") and not topic.get("_atr"))
            and repairs >= NARRATION_STATIC_REPAIRS
        ):
            break
    reason = (
        "LLM narration failed the hook/teaching gate; "
        "refusing the topic-card template (no upload, no paid voice)"
    )
    if last:
        reason += ": " + last
    block_fallback(draft or {}, reason)


def resolve_narration(llm_result, template):
    """One outline only. A partial LLM result is dropped; the template is used whole."""
    beats = (llm_result or {}).get("beats") if isinstance(llm_result, dict) else None
    title = str((llm_result or {}).get("title") or "").strip() if isinstance(llm_result, dict) else ""
    hook = str((llm_result or {}).get("hook_line") or "").strip() if isinstance(llm_result, dict) else ""
    complete = (
        isinstance(beats, dict)
        and bool(title) and bool(hook)
        and all(str(beats.get(b) or "").strip() for b in BEATS)
    )
    if not complete:
        return {
            "title": template["title"],
            "hook_line": template["hook_line"],
            "beats": {b: template["beats"][b] for b in BEATS},
        }, "fallback"
    return {
        "title": title,
        "hook_line": hook,
        "beats": {b: str(beats[b]).strip() for b in BEATS},
    }, "llm"


def upload_block_reason(source, narration=None):
    """A fallback or a mixed draft does not publish. Skipping the slot is the safe outcome."""
    if source in ("outline", "llm", "cache", "approved"):
        return ""
    if source == "fallback":
        return "LLM narration fell back to the topic card; upload blocked"
    return f"narration source {source} is not a single outline; upload blocked"


def approved_publish():
    """True only when a human passed approved=true. A REVIEW score does not self-approve."""
    return os.environ.get("APPROVED", "").strip().lower() in {"1", "true", "yes"}


def _approved_script(topic=None):
    """The dry-run script when NARRATION_FROM_RUN is set. None on a normal build."""
    import approved_script
    if isinstance(topic, dict) and topic.get("_approved_script") and not approved_script.active():
        return None
    return approved_script.load_for_topic(topic or {})


def _approved_narration(narration, topic=None):
    if isinstance(narration, dict) and narration.get("source") == "approved":
        return True
    if isinstance(topic, dict) and topic.get("_approved_script"):
        return True
    return bool(os.environ.get("NARRATION_FROM_RUN", "").strip())


def _force_hold(review):
    review["public_ok"] = False
    review["passed"] = False
    review["review_flag"] = False
    review["verdict"] = "HOLD"
    return review


def soften_approved_holds(review, sync_report=None):
    """On a narration_from_run build, only a few misses stay HOLD.

    Hard: wrong number or maths, beat-level recall under the bar (wrong voice
    or topic), a failed transcription, and text cut off or outside the safe
    area. Window sync, chart timing, a static frame, trend, and craft become
    REVIEW. approved=true can publish that REVIEW. A normal build is unchanged.
    """
    if not isinstance(review, dict) or not _approved_narration(None):
        return review
    if review.get("public_ok") or review.get("narration_fallback"):
        return review
    parts = review.get("parts") or {}
    try:
        accuracy = float(parts.get("accuracy"))
    except (TypeError, ValueError):
        return _force_hold(review)
    if accuracy < self_review.PART_BAR:
        return _force_hold(review)
    blob = " ".join(str(note) for note in (review.get("notes") or []))
    blob = blob + " " + str(review.get("summary") or "")
    if "safe area" in blob or "cut off" in blob:
        return _force_hold(review)
    report = sync_report if isinstance(sync_report, dict) else {}
    reason = str(report.get("reason") or "")
    if "transcription failed" in reason:
        return _force_hold(review)
    import sync_gate
    beats = report.get("beats") or []
    if beats:
        for row in beats:
            try:
                recall = float(row.get("recall"))
            except (TypeError, ValueError):
                return _force_hold(review)
            if recall + 1e-9 < sync_gate.MIN_RECALL:
                return _force_hold(review)
    elif report and not report.get("ok"):
        return _force_hold(review)
    review["review_flag"] = True
    review["verdict"] = "REVIEW"
    review["public_ok"] = False
    review["passed"] = False
    return review


def _hold_hard(review):
    """Duration, safe area, and fallback stay a hold. They are not a soft REVIEW."""
    if isinstance(review, dict):
        review["public_ok"] = False
        review["passed"] = False
        review["review_flag"] = False
        review["verdict"] = "HOLD"
    return review


def apply_upload_block(review, source):
    reason = upload_block_reason(source)
    if not reason:
        return review
    review["public_ok"] = False
    review["passed"] = False
    review["review_flag"] = False
    review["verdict"] = "HOLD"
    review.setdefault("notes", []).append(reason)
    summary = review.get("summary") or ""
    review["summary"] = (summary + " — " + reason).strip(" —")
    return review


# ---------------------------------------------------------------- voice
async def _edge(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


ENGINES = []
ELEVEN_EXHAUSTED = False
ELEVEN_REMAINING = None
VOICE_FALLBACK_REASON = ""
_VOICE_TOPIC = None


def _reset_voice_state():
    global ENGINES, ELEVEN_EXHAUSTED, ELEVEN_REMAINING, VOICE_FALLBACK_REASON
    ENGINES = []
    ELEVEN_EXHAUSTED = False
    ELEVEN_REMAINING = None
    VOICE_FALLBACK_REASON = ""


def _switch_to_edge(reason):
    """Speak this live slot with the configured Edge voice. Do not skip it."""
    global ELEVEN_EXHAUSTED, VOICE_FALLBACK_REASON
    ELEVEN_EXHAUSTED = True
    if not VOICE_FALLBACK_REASON:
        VOICE_FALLBACK_REASON = str(reason)
        eleven_voice.log_fallback(VOICE_FALLBACK_REASON)


def _note_voice_engine(engine):
    """The Actions job summary names the engine the slot actually spoke."""
    line = f"voice_engine={engine}"
    print(line, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _note_edge_engine():
    _note_voice_engine("edge")


def _write_spoken_engine(engine, elevenlabs_attempted=None):
    """voice_meta is what the ledger copies into voice_engine."""
    key_set = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
    if elevenlabs_attempted is None:
        elevenlabs_attempted = key_set and eleven_voice.paid_voice_allowed()
    eleven_voice.write_voice_meta(
        RUN,
        engine,
        VOICE_FALLBACK_REASON,
        elevenlabs_attempted=elevenlabs_attempted,
    )
    credit_log.set_tts_engine(engine, VOICE_FALLBACK_REASON)
    _note_voice_engine(engine)
    import trade_plan
    if trade_plan.voice_review(engine, _VOICE_TOPIC):
        try:
            meta_path = RUN / "voice_meta.json"
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["review"] = True
            meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            pass
    ok(f"voice engine: {engine}" + (f" ({VOICE_FALLBACK_REASON})" if VOICE_FALLBACK_REASON else ""))
    return engine


def _credits_stop(reason):
    """A non-credit ElevenLabs failure. No Edge audio, no second upload."""
    print(f"[credits] STOP: {reason}", flush=True)
    print("[STOP] live slot skipped; buffer may fill. No Edge fallback.", flush=True)
    try:
        eleven_voice.write_voice_meta(RUN, "elevenlabs", str(reason), elevenlabs_attempted=True)
        credit_log.set_tts_engine("elevenlabs", str(reason))
    except Exception:  # noqa: BLE001 — the stop line matters more than the ledger row
        pass
    die(f"[credits] STOP: {reason}")


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
    except Exception as exc:  # noqa: BLE001 — a live slot stops; it does not speak Edge
        _credits_stop(f"subscription pre-check failed ({type(exc).__name__})")
        return
    body = eleven_voice.parse_response(response)
    status = getattr(response, "status_code", 0)
    kind = eleven_voice.classify(status, body)
    if kind == "quota":
        _switch_to_edge(eleven_voice.describe(status, body))
        return
    if status != 200:
        # A scoped key may 401/403 on the subscription read. TTS stays the auth check.
        if kind in ("auth", "permission"):
            print(f"[note] ElevenLabs subscription pre-check HTTP {status}; TTS remains the auth check")
            return
        _credits_stop(f"subscription pre-check failed ({eleven_voice.describe(status, body)})")
        return
    remaining = eleven_voice.subscription_remaining(body)
    if remaining is None:
        return
    ELEVEN_REMAINING = remaining
    credit_log.set_eleven_remaining(remaining)
    need = sum(len(_voice_text(text or "")) for text in texts)
    floor = eleven_voice.LIVE_CREDIT_FLOOR
    # Below the floor, or not enough characters to finish the Short: Edge.
    # At or above the floor, with enough characters left, Rudra stays.
    if remaining < floor:
        _switch_to_edge(
            f"pre-check remaining={remaining} is below the {floor} character floor"
        )
        return
    if need > 0 and remaining < need:
        _switch_to_edge(
            f"pre-check remaining={remaining} characters, this Short needs {need}"
        )


def _voice_text(text):
    """The string that will be spoken.

    An approved dry-run beat is already the speakable line stored in the
    artifact. A second speak.speakable() or fold_roman_words call is what
    can make the paid voice differ from that script, so neither runs.
    """
    import approved_script
    if approved_script.active():
        return str(text or "")
    import speak
    return speak.speakable(text or "")


def _eleven(text, path, c):
    """Call ElevenLabs. Quota raises QuotaFallback. Auth and permission die."""
    import time
    import requests

    import approved_script
    approved_script.refuse_paid_mismatch(text)

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice = eleven_voice.configured_voice_id(c)
    if not key:
        return False
    eleven_voice.require_paid_tts()

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
    voice, rate = eleven_voice.PREVIEW_EDGE_VOICE, eleven_voice.PREVIEW_EDGE_RATE
    # Unpaid sample only. The paid path above does not read this.
    sample = os.environ.get("EDGE_SAMPLE_RATE", "").strip()
    if sample:
        rate = sample
    return voice, rate


def _paid_voice_this_beat():
    return (
        eleven_voice.paid_voice_allowed()
        and bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
        and not ELEVEN_EXHAUSTED
    )


def voice_cache_id(text, c):
    """Hash of the beat text plus the voice id and speed. Existence is not enough."""
    voice = ((c or {}).get("voice") or {})
    edge_voice, edge_rate = _edge_voice_rate(c)
    blob = "\n".join([
        str(text or ""),
        str(voice.get("voice_id") or ""),
        str(voice.get("speed") or ""),
        str(edge_voice or ""),
        str(edge_rate or ""),
    ])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tts(text, path, c):
    """ElevenLabs only on a real publish (or an explicit sample opt-in).

    Quota on that path uses the configured Edge voice. Auth still fails.
    Previews use free Edge and never call ElevenLabs.
    Voice text is speak.speakable() (₹ -> रुपये). Captions keep the original text.
    A cached file is reused only when its text hash and voice match this beat.
    """
    engine_file = path.with_suffix(".engine")
    id_file = path.with_suffix(".textid")
    cache_id = voice_cache_id(text, c)
    cached_engine = engine_file.read_text(encoding="utf-8").strip().lower() if engine_file.exists() else ""
    cached_id = id_file.read_text(encoding="utf-8").strip() if id_file.exists() else ""
    eleven_key = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
    proof_audio = os.environ.get("PROOF_FREE_AUDIO", "").strip().lower()
    if proof_audio in ("edge", "silence"):
        allow_paid = False
    elif _paid_voice_this_beat():
        # A live slot with the switch off fails here. It does not speak Edge.
        eleven_voice.require_paid_tts()
        allow_paid = True
    else:
        allow_paid = False
    fresh = path.exists() and path.stat().st_size > 1000 and cached_id == cache_id
    # Reuse paid audio only while this run is still on ElevenLabs.
    # After a credit fallback, an old paid file is discarded and Edge is used.
    # A preview never reuses a paid file. A different beat text never reuses it.
    reuse = None
    if fresh and cached_engine == "elevenlabs" and allow_paid:
        reuse = cached_engine
    elif fresh and cached_engine == "edge" and not allow_paid:
        reuse = cached_engine
    elif fresh and cached_engine == "edge" and (not eleven_key or ELEVEN_EXHAUSTED):
        reuse = cached_engine
    import approved_script
    voice_text = _voice_text(text)
    if approved_script.active():
        # Before cache reuse and before the HTTP call. A mismatch spends nothing.
        approved_script.refuse_paid_mismatch(voice_text)
    if reuse:
        ok(f"voice cache reused: {path.name} ({reuse})")
        ENGINES.append(reuse)
        return
    if not approved_script.active():
        import speak
        left = speak.leftovers(voice_text)
        if left:
            print(f"[warn] voice text me ab bhi: {sorted(set(left))} — speak.py me jodo")
    with open(RUN / "tts_text.txt", "a", encoding="utf-8") as handle:
        handle.write(f"{path.stem}: {voice_text}\n")
    if allow_paid:
        if not _eleven(voice_text, path, c):
            die(f"ElevenLabs TTS did not produce paid audio: {path.name}; not a credit error, so Edge was not used")
        ENGINES.append("elevenlabs")
        engine_file.write_text("elevenlabs", encoding="utf-8")
        id_file.write_text(cache_id, encoding="utf-8")
        return
    edge_voice, edge_rate = _edge_voice_rate(c)
    ENGINES.append("edge")
    for attempt in range(3):
        try:
            asyncio.run(_edge(
                voice_text,
                edge_voice,
                edge_rate,
                path,
            ))
            if path.exists() and path.stat().st_size > 1000:
                engine_file.write_text("edge", encoding="utf-8")
                id_file.write_text(cache_id, encoding="utf-8")
                return
        except Exception as exc:  # noqa: BLE001
            print(f"[retry] tts {attempt + 1}: {exc}")
    die(f"TTS fail: {path.name}")


def _beat_tts_path(beat):
    """Gemini writes a wav. ElevenLabs and Edge still write an mp3."""
    wav = RUN / f"tts_{beat}.wav"
    if wav.exists():
        return wav
    mp3 = RUN / f"tts_{beat}.mp3"
    if mp3.exists():
        return mp3
    return None


def _drop_gemini_audio(beats):
    """A failed Gemini take must not be trimmed or captioned later."""
    for name in ("gemini_voice.wav", "gemini_words.json", "words.json"):
        (RUN / name).unlink(missing_ok=True)
    for beat in beats:
        for name in (
            f"tts_{beat}.wav", f"tts_{beat}.engine", f"tts_{beat}.textid",
            f"voice_clean_{beat}.wav",
        ):
            (RUN / name).unlink(missing_ok=True)


def _fold_voice_line(text):
    """Roman Hinglish, including the disclaimer, becomes Devanagari before TTS."""
    return fold_roman_words(text)


def _gemini_script(spoken, beats):
    """One speakable script. Folding stays on a copy until Gemini is kept."""
    import approved_script
    folded = {}
    parts = []
    for beat in beats:
        text = spoken.get(beat) or ""
        if not approved_script.active():
            text = _fold_voice_line(text)
        folded[beat] = text
        voice = _voice_text(text).strip()
        approved_script.refuse_paid_mismatch(voice)
        if voice:
            parts.append(voice)
    return folded, " ".join(parts)


def _expected_voice_seconds(spoken, beats, c):
    words = sum(len(_voice_text(spoken.get(beat) or "").split()) for beat in beats)
    voice = (c or {}).get("voice") or {}
    try:
        wps = float(voice.get("words_per_second") or 1.9)
    except (TypeError, ValueError):
        wps = 1.9
    if wps <= 0:
        wps = 1.9
    return (words / wps) if words else 0.0


def _gemini_take_names(beats):
    names = ["gemini_voice.wav", "gemini_words.json", "words.json"]
    for beat in beats:
        names.extend([
            f"tts_{beat}.wav", f"tts_{beat}.engine", f"tts_{beat}.textid",
            f"voice_clean_{beat}.wav",
        ])
    return names


def _snapshot_run_files(names):
    saved = {}
    for name in names:
        path = RUN / name
        if path.exists():
            saved[name] = path.read_bytes()
    return saved


def _restore_run_files(saved):
    for name, data in saved.items():
        (RUN / name).write_bytes(data)


def _gemini_pace_window(topic):
    """34-65s for a four-beat Gemini take. Topic plans accept 40-50s."""
    import trade_plan
    if trade_plan.is_plan_topic(topic):
        return trade_plan.VOICE_SECONDS
    if topic is None or not four_live(topic):
        return None
    lo, hi = _output_bounds(topic, engine="")
    if lo == 45.0 and hi == 65.0:
        import gemini_voice
        return (gemini_voice.GEMINI_MIN_SECONDS, gemini_voice.GEMINI_MAX_SECONDS)
    return None


def gemini_accepts(total, window):
    """'target' inside the window, 'review' in a plan's 50–55s band, else 'miss'."""
    if not window:
        return "target"
    try:
        total = float(total)
        lo, hi = float(window[0]), float(window[1])
    except (TypeError, ValueError):
        return "miss"
    import trade_plan
    hard = hi
    if (lo, hi) == trade_plan.VOICE_SECONDS:
        hard = float(trade_plan.VOICE_REVIEW_MAX)
    if lo <= total <= hi:
        return "target"
    if hi < total <= hard + 1e-9:
        return "review"
    return "miss"


def _better_gemini_take(new, old, window=None):
    """Prefer a take inside the window. A plan review beat a miss, not a 40–50s take."""
    import trade_plan
    if window == trade_plan.VOICE_SECONDS:
        rank = {"target": 0, "review": 1, "miss": 2}
        left = gemini_accepts(new, window)
        right = gemini_accepts(old, window)
        if rank[left] != rank[right]:
            return rank[left] < rank[right]
        return abs(float(new) - 45.0) < abs(float(old) - 45.0)
    import gemini_voice
    if new >= gemini_voice.GEMINI_RETRY_UNDER and old < gemini_voice.GEMINI_RETRY_UNDER:
        return True
    if old >= gemini_voice.GEMINI_RETRY_UNDER and new < gemini_voice.GEMINI_RETRY_UNDER:
        return False
    return new > old


def _gemini_stop(total, window, attempt):
    """True when another Charon take would not help."""
    if window is None:
        return True
    import trade_plan
    status = gemini_accepts(total, window)
    if window == trade_plan.VOICE_SECONDS:
        if status in ("target", "review"):
            return True
        if attempt == 1 or float(total) > float(trade_plan.VOICE_REVIEW_MAX):
            return True
        return False
    import gemini_voice
    return float(total) >= gemini_voice.GEMINI_RETRY_UNDER or attempt == 1


def _one_gemini_take(spoken, c, beats, folded, script, style, durations, attempt, topic, window):
    import gemini_voice
    dest = RUN / "gemini_voice.wav"
    gemini_voice.write_speech(script, dest, style=style)
    seconds = gemini_voice.wav_seconds(dest)
    expected = _expected_voice_seconds(folded, beats, c)
    if gemini_voice.duration_far(seconds, expected):
        durations.append(seconds)
        print(f"[gemini] take {attempt + 1}: {seconds:.1f}s", flush=True)
        raise gemini_voice.GeminiFallback(
            f"duration {seconds:.1f}s is far from expected {expected:.1f}s"
        )
    for beat in beats:
        spoken[beat] = folded[beat]
    gemini_voice.split_into_beats(dest, spoken, beats, RUN)
    try:
        raw = _trim_rendered(beats)
    except SystemExit as exc:
        raise gemini_voice.GeminiFallback(str(exc))
    total = sum(raw.values())
    durations.append(total)
    print(f"[gemini] take {attempt + 1}: {total:.1f}s", flush=True)
    if window is None and topic is not None:
        lo, hi = _output_bounds(topic, engine="")
        if total < lo or total > hi:
            raise gemini_voice.GeminiFallback(
                f"duration {total:.1f}s is outside {lo:.0f}-{hi:.0f}s"
            )
    return total


def _speak_gemini(spoken, c, beats, topic):
    """Charon, then one slower retry when the take is under 45s. At most two calls."""
    import gemini_voice
    folded, script = _gemini_script(spoken, beats)
    if not script:
        raise gemini_voice.GeminiFallback("Gemini TTS script was empty")
    window = _gemini_pace_window(topic)
    durations = []
    kept = None
    last_error = None
    styles = (gemini_voice.STYLE, gemini_voice.SLOW_STYLE)
    for attempt, style in enumerate(styles):
        if attempt:
            _drop_gemini_audio(beats)
        try:
            total = _one_gemini_take(
                spoken, c, beats, folded, script, style, durations, attempt, topic, window,
            )
        except gemini_voice.GeminiFallback as exc:
            last_error = exc
            fast = durations and durations[-1] < gemini_voice.GEMINI_RETRY_UNDER
            if window is not None and attempt == 0 and fast:
                continue
            break
        if window is None:
            kept = (total, None)
            break
        if gemini_accepts(total, window) in ("target", "review"):
            snap = _snapshot_run_files(_gemini_take_names(beats))
            if kept is None or _better_gemini_take(total, kept[0], window):
                kept = (total, snap)
        if _gemini_stop(total, window, attempt):
            break
    if kept is not None and kept[1] is not None:
        _restore_run_files(kept[1])
    import trade_plan
    if kept is None:
        if last_error is not None and (window is None or not durations):
            raise last_error
        shown = ", ".join(f"{item:.1f}s" for item in durations) or "none"
        lo, hi = window or (gemini_voice.GEMINI_MIN_SECONDS, gemini_voice.GEMINI_MAX_SECONDS)
        if window == trade_plan.VOICE_SECONDS:
            hi = trade_plan.VOICE_REVIEW_MAX
        raise gemini_voice.GeminiFallback(f"no take in {lo:.0f}-{hi:.0f}s ({shown})")
    shown = ", ".join(f"{item:.1f}s" for item in durations)
    print(f"[gemini] takes: {shown}; voice_engine=gemini", flush=True)
    trade_plan.note_duration(kept[0], topic)
    for beat in beats:
        (RUN / f"tts_{beat}.engine").write_text("gemini", encoding="utf-8")
    ENGINES.append("gemini")
    return kept[0]


def synthesize_short_voices(spoken, c, beats=None, topic=None):
    """Gemini Charon, then Rudra when credits cover it, then Edge Madhur."""
    global ENGINES, VOICE_FALLBACK_REASON
    if topic is None:
        topic = _VOICE_TOPIC
    beats = tuple(beats) if beats else tuple(b for b in BEATS if b in (spoken or {}))
    if (RUN / "narration_block.json").exists():
        die("narration blocked; refusing TTS")
    _reset_voice_state()
    proof_audio = os.environ.get("PROOF_FREE_AUDIO", "").strip().lower()
    gemini_note = ""
    if proof_audio not in ("edge", "silence") and os.environ.get("GEMINI_API_KEY", "").strip():
        import gemini_voice
        try:
            _speak_gemini(spoken, c, beats, topic)
            return _write_spoken_engine("gemini", elevenlabs_attempted=False)
        except gemini_voice.GeminiFallback as exc:
            gemini_note = gemini_voice.redact(exc.reason)
            print(f"[warn] Gemini TTS failed ({gemini_note}). Falling back.", flush=True)
            _drop_gemini_audio(beats)
    wants_paid = (
        proof_audio not in ("edge", "silence")
        and eleven_voice.paid_voice_allowed()
        and str(((c or {}).get("voice") or {}).get("engine") or "elevenlabs").lower() == "elevenlabs"
    )
    # The paid switch still blocks a surprise ElevenLabs call. After Gemini
    # has already failed, Edge speaks instead of ending the slot.
    if gemini_note and not eleven_voice.paid_tts_enabled():
        wants_paid = False
    if wants_paid:
        eleven_voice.require_paid_tts()
        eleven_voice.ensure_library_voice(c)
        preflight_eleven_credits([spoken[b] for b in beats])

    def once():
        global ENGINES
        ENGINES = []
        import approved_script
        for beat in beats:
            if not approved_script.active():
                spoken[beat] = fold_roman_words(spoken[beat])
            tts(spoken[beat], RUN / f"tts_{beat}.mp3", c)

    try:
        once()
    except eleven_voice.QuotaFallback as exc:
        # One engine for the whole slot. Paid files from earlier beats are not reused.
        _switch_to_edge(exc.reason)
        once()
    engine = "edge" if "edge" in ENGINES else "elevenlabs"
    if gemini_note and not VOICE_FALLBACK_REASON:
        VOICE_FALLBACK_REASON = gemini_note
    # Word timestamps and every later gate run on this audio.
    return _write_spoken_engine(engine)


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


def _wrap_lines(text, size, max_w):
    """Break a line the same way the burned subtitle will, so the gate box matches."""
    font = scene_frames._font(size)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lines, cur = [], ""
    for word in str(text or "").split():
        trial = word if not cur else cur + " " + word
        if probe.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _top_box(text, x, y, size, max_w=None):
    """Bounding box for an=8 (top-center) text. max_w wraps on spaces."""
    font = scene_frames._font(int(size))
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    lines = _wrap_lines(text, size, max_w) if max_w else [text]
    gap = int(size * 1.25)
    boxes = []
    for i, line in enumerate(lines):
        if not line:
            continue
        boxes.append(probe.textbbox((x, y + i * gap), line, font=font, anchor="mt"))
    if not boxes:
        return (x, y, x + 1, y + 1)
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def _subtitle_limits():
    import safe_area
    left, _top, right, _bottom = safe_area.limits()
    return (left + 8, safe_area.SUBTITLE_TOP, right - 8, safe_area.SUBTITLE_LIMIT)


def layout_in_box(lines, box, max_size, min_size):
    """Shrink and wrap until every input line sits inside box.

    Returns one group per input line. Each group is (text, bbox, size, y) rows.
    The last attempt is returned even if it still sticks out, so the gate can fail it.
    """
    left, top, right, bottom = (float(v) for v in box)
    max_w = max(40.0, right - left - 8)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    best = []
    size = int(max_size)
    floor = int(min_size)
    while size >= floor:
        gap = max(4, int(size * 0.22))
        y = top + 4
        groups = []
        fits = True
        font = scene_frames._font(size)
        for line in lines:
            rows = []
            for piece in (_wrap_lines(line, size, max_w) or [""]):
                if not str(piece).strip():
                    continue
                b = probe.textbbox((540, y), piece, font=font, anchor="mt")
                rect = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
                if rect[0] < left - 0.5 or rect[2] > right + 0.5 or rect[1] < top - 0.5 or rect[3] > bottom + 0.5:
                    fits = False
                rows.append((piece, rect, size, y))
                y = rect[3] + gap
            groups.append(rows)
            y += 6
        best = groups
        if fits and groups and any(groups):
            return groups
        size -= 2
    return best


def _measure_at(text, y, size, max_w):
    """Boxes for `text` wrapped at a reserved size and top, so a shorter line stays put."""
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    font = scene_frames._font(int(size))
    gap = max(4, int(size * 0.22))
    rows = []
    cursor = float(y)
    for piece in (_wrap_lines(text, size, max_w) or [""]):
        if not str(piece).strip():
            continue
        b = probe.textbbox((540, cursor), piece, font=font, anchor="mt")
        rows.append((piece, (float(b[0]), float(b[1]), float(b[2]), float(b[3])), int(size), cursor))
        cursor = float(b[3]) + gap
    return rows


def _caption_width():
    left, _top, right, _bottom = _subtitle_limits()
    return max(40.0, right - left - 8)


def _wrap_caption(text, size=40):
    return [ln for ln in _wrap_lines(text, size, _caption_width()) if str(ln).strip()]


def _cue_texts(text):
    """Spoken groups that wrap to at most two lines, and never split a number from its unit."""
    size = 40
    out = []
    for sent in re.split(r"(?<=[।.?!])\s+", (text or "").strip()):
        words = sent.split()
        cur = []
        for i, word in enumerate(words):
            nxt = words[i + 1] if i + 1 < len(words) else ""
            glued = (bool(re.search(r"\d", word)) and nxt.strip("।.,?!") in UNITS) or (
                word in ("करीब", "लगभग", "₹") and bool(re.search(r"\d", nxt))
            )
            # Each figure gets its own cue, including the first, so the subtitle
            # does not say ₹2,00,000 while the voice is still on the words before it.
            # Gluing keeps "2%" with the following "पर", not with the figure before it.
            prev = cur[-1] if cur else ""
            stuck = prev in ("करीब", "लगभग", "₹") or (
                bool(re.search(r"\d", prev)) and word.strip("।.,?!") in UNITS
            )
            if re.search(r"\d", word) and cur and not stuck:
                out.append(" ".join(cur))
                cur = [word]
                continue
            trial = cur + [word]
            lines = _wrap_caption(" ".join(trial), size)
            if len(lines) > 2 and cur and not glued:
                out.append(" ".join(cur))
                cur = [word]
            else:
                cur = trial
        while cur:
            lines = _wrap_caption(" ".join(cur), size)
            if len(lines) <= 2 or len(cur) == 1:
                out.append(" ".join(cur))
                break
            if len(cur) >= 2 and re.search(r"\d", cur[-2]) and cur[-1].strip("।.,?!") in UNITS:
                out.append(" ".join(cur))
                break
            last = cur.pop()
            out.append(" ".join(cur))
            cur = [last]
    merged = []
    for part in out:
        # A one-word orphan can join the previous line. A figure, or the first
        # word of the next sentence, must not: that is how "2% =" started
        # showing ₹4,000 while the card had already cut it.
        orphan = len(part.split()) == 1 and not re.search(r"\d", part)
        previous = merged[-1] if merged else ""
        boundary = bool(re.search(r"[।.!?？]$", previous.strip()))
        if merged and orphan and not boundary:
            combo = previous + " " + part
            if len(_wrap_caption(combo)) <= 2:
                merged[-1] = combo
                continue
        merged.append(part)
    return [part for part in merged if part.strip()]


def _cue_rows(text):
    groups = layout_in_box([text or " "], _subtitle_limits(), 40, 18)
    return groups[0] if groups else []


def _find_token_span(stream, pos, want):
    """First exact token run at or after pos. A miss waits; it never matches early."""
    limit = min(len(stream), pos + 8)
    for k in range(pos, limit):
        if stream[k][1] != want[0]:
            continue
        cursor = k + 1
        ok_span = True
        for tok in want[1:]:
            hit = None
            for j in range(cursor, min(len(stream), cursor + 3)):
                if stream[j][1] == tok:
                    hit = j + 1
                    break
            if hit is None:
                ok_span = False
                break
            cursor = hit
        if ok_span:
            return stream[k][0], cursor
    return None


def _align_starts(script_words, audio_words, t0, t1):
    """Whisper start for each script word. Unmatched words share the gap by length."""
    import sync_gate
    window = [
        w for w in audio_words
        if float(w.get("end") or 0) >= float(t0) - 0.25
        and float(w.get("start") or 0) <= float(t1) + 0.45
    ]
    stream = []
    for word in window:
        start = float(word.get("start") or 0)
        for tok in sync_gate.tokens(word.get("word") or ""):
            stream.append((start, tok))
    starts = [None] * len(script_words)
    pos = 0
    for i, word in enumerate(script_words):
        want = sync_gate.tokens(word)
        if not want:
            continue
        found = _find_token_span(stream, pos, want)
        if found is None:
            continue
        starts[i], pos = found
    if starts and all(item is None for item in starts) and window:
        floor = min(float(word.get("start") or 0) for word in window)
        return [floor] * len(starts)
    # Unmatched words take a share of the gap, sized like the spoken text.
    # Copying the next match's start, then slicing that pile into equal cues,
    # is what put the wrong sentence in a 3.4s window.
    return _spread_unmatched(starts, script_words, t0, t1)


def _char_weight(word):
    return max(1, len(str(word or "").strip()))


def _spread_unmatched(starts, words, t0, t1):
    """Place words whisper did not match by text length between the anchors."""
    known = [i for i, item in enumerate(starts) if item is not None]
    if not known:
        return starts
    t0, t1 = float(t0), float(t1)

    def place(left_i, right_i, left_t, right_t):
        gap = list(range(left_i + 1, right_i))
        if not gap:
            return
        weights = [_char_weight(words[i]) for i in gap]
        total = sum(weights) or 1
        width = max(0.0, float(right_t) - float(left_t))
        if left_i >= 0:
            lead = _char_weight(words[left_i])
            total += lead
            acc = lead
        else:
            acc = 0.0
        for i, weight in zip(gap, weights):
            starts[i] = float(left_t) + width * acc / total
            acc += weight

    place(-1, known[0], t0, starts[known[0]])
    for left, right in zip(known, known[1:]):
        place(left, right, starts[left], starts[right])
    place(known[-1], len(words), starts[known[-1]], t1)
    return starts


def _place_cues(parts, starts, t0, t1):
    """One interval per cue. A shared start is sequenced so the later cue waits."""
    cues = []
    idx = 0
    for part in parts:
        nwords = len(part.split())
        word_start = None
        if starts is not None:
            known = [starts[i] for i in range(idx, min(len(starts), idx + nwords)) if starts[i] is not None]
            word_start = known[0] if known else None
        cues.append({
            "text": part,
            "lines": _wrap_caption(part),
            "word_start": word_start,
        })
        idx += nwords
    if not cues:
        return []
    if starts is None:
        total = sum(len(cue["text"]) for cue in cues) or 1
        cursor = float(t0)
        span = max(0.05, float(t1) - float(t0))
        for cue in cues:
            cue["t0"] = cursor
            cursor += span * max(1, len(cue["text"])) / total
            cue["t1"] = cursor
        cues[-1]["t1"] = float(t1)
        return cues
    merged = []
    for cue in cues:
        if merged and cue["word_start"] is not None and merged[-1]["word_start"] == cue["word_start"]:
            combo = merged[-1]["text"] + " " + cue["text"]
            lines = _wrap_caption(combo)
            if len(lines) <= 2:
                merged[-1]["text"] = combo
                merged[-1]["lines"] = lines
                continue
        merged.append(cue)
    cues = merged
    prev = None
    for cue in cues:
        start = cue["word_start"] if cue["word_start"] is not None else (prev if prev is not None else float(t0))
        if cue["word_start"] is not None:
            start = max(float(start), float(cue["word_start"]))
        if prev is not None:
            start = max(float(start), prev)
        cue["t0"] = float(start)
        prev = cue["t0"]
    i = 0
    while i < len(cues):
        j = i
        while j + 1 < len(cues) and cues[j + 1]["t0"] <= cues[i]["t0"] + 1e-3:
            j += 1
        if j > i:
            end_anchor = float(t1)
            for k in range(j + 1, len(cues)):
                ws = cues[k]["word_start"]
                if ws is not None and float(ws) > cues[i]["t0"] + 1e-3:
                    end_anchor = float(ws)
                    break
            weights = [max(1, len(cues[k]["text"])) for k in range(i, j + 1)]
            total = sum(weights) or 1
            width = max(0.05 * len(weights), end_anchor - float(cues[i]["t0"]))
            base = float(cues[i]["t0"])
            acc = 0
            for k, weight in zip(range(i, j + 1), weights):
                cues[k]["t0"] = base + width * acc / total
                acc += weight
                ws = cues[k]["word_start"]
                if ws is not None and cues[k]["t0"] < float(ws):
                    cues[k]["t0"] = float(ws)
            for k in range(i + 1, j + 1):
                if cues[k]["t0"] <= cues[k - 1]["t0"] + 1e-3:
                    cues[k]["t0"] = cues[k - 1]["t0"] + 0.05
        i = j + 1
    for k in range(1, len(cues)):
        if cues[k]["t0"] <= cues[k - 1]["t0"] + 1e-3:
            cues[k]["t0"] = cues[k - 1]["t0"] + 0.05
    for k, cue in enumerate(cues):
        nxt = float(cues[k + 1]["t0"]) if k + 1 < len(cues) else float(t1)
        cue["t1"] = max(float(cue["t0"]), nxt)
    cues[-1]["t1"] = max(float(cues[-1]["t0"]), float(t1))
    for k in range(len(cues) - 1):
        cues[k]["t1"] = min(float(cues[k]["t1"]), float(cues[k + 1]["t0"]))
    return cues


_CUE_CACHE = {}


def plan_cues(spoken, beat_times, audio_words=None):
    """One caption at a time. With whisper times, a cue starts when its words do."""
    word_key = None
    if audio_words:
        word_key = tuple(
            (str(word.get("word") or ""), round(float(word.get("start") or 0), 3),
             round(float(word.get("end") or 0), 3))
            for word in audio_words
        )
    key = (
        tuple((beat, (spoken or {}).get(beat) or "", (beat_times or {}).get(beat)) for beat in timed_beats(beat_times)),
        word_key,
    )
    cached = _CUE_CACHE.get(key)
    if cached is not None:
        return cached
    cues = []
    for beat in timed_beats(beat_times):
        if beat not in (beat_times or {}):
            continue
        t0, t1 = (float(v) for v in beat_times[beat])
        if beat == "hook":
            t0 = 0.0
        text = (spoken or {}).get(beat) or ""
        parts = _cue_texts(text)
        if not parts:
            continue
        starts = _align_starts(text.split(), audio_words, t0, t1) if audio_words else None
        for cue in _place_cues(parts, starts, t0, t1):
            cue["beat"] = beat
            cues.append(cue)
    for i in range(1, len(cues)):
        if cues[i]["t0"] < cues[i - 1]["t1"] - 1e-3:
            cues[i]["t0"] = max(float(cues[i]["t0"]), float(cues[i - 1]["t1"]))
            ws = cues[i].get("word_start")
            if ws is not None:
                cues[i]["t0"] = max(float(cues[i]["t0"]), float(ws))
    for i in range(len(cues) - 1):
        if cues[i]["t1"] > cues[i + 1]["t0"]:
            cues[i]["t1"] = cues[i + 1]["t0"]
    _CUE_CACHE[key] = cues
    return cues


def cues_at(cues, t):
    """The single cue on screen at t. The end instant belongs to the next cue."""
    t = float(t)
    hits = [cue for cue in cues or [] if float(cue["t0"]) - 1e-9 <= t < float(cue["t1"]) - 1e-9]
    if not hits and cues:
        last = max(cues, key=lambda cue: float(cue["t1"]))
        if float(last["t0"]) - 1e-9 <= t <= float(last["t1"]) + 1e-6:
            hits = [last]
    return hits


def number_format_notes(on_screen_texts, spoken="", caption="", ignore=None):
    """Every spoken or captioned number must match an on-screen figure exactly.

    772.41 does not match 772.4. Extra figures on the card are allowed.
    ignore holds labelled example figures on an evergreen topic. A live price
    is never ignored.
    """
    import sync_gate
    screen = set()
    for text in on_screen_texts or []:
        screen.update(sync_gate.numbers_in(text))
    skipped = set(ignore or ())
    notes = []
    seen = set()
    for origin, blob in (("spoken", spoken), ("caption", caption)):
        for num in sync_gate.numbers_in(blob):
            if num in screen or num in seen or num in skipped:
                continue
            seen.add(num)
            notes.append(f"{origin} number {num} is not an on-screen value")
    return notes


def stale_data_notes(as_of, today, allow_stale_sample=False):
    """Real runs refuse a close older than three weekdays. A sample may opt in."""
    if not as_of or market_data.close_is_fresh(as_of, today):
        return []
    if allow_stale_sample:
        return []
    return [f"stale market data as of {as_of}"]


def _script_near(spoken, beat_times, audio_words, t, radius=0.85, scene_end=None):
    """Narration words due around t, spelled as the script, not as the transcript.

    The forward window is 0.85s again. It stops at the scene end: a figure
    spoken in the next scene is not required on the card that is still up.
    Painting that figure here is what the sync gate calls "shown before it
    is said". Whisper may split 2350 into 2 and 350. The gate compares the
    script's own figures to the card.
    """
    t = float(t)
    limit = t + float(radius)
    if scene_end is not None:
        limit = min(limit, float(scene_end))
    parts = []
    for beat in timed_beats(beat_times):
        if beat not in (beat_times or {}):
            continue
        t0, t1 = (float(v) for v in beat_times[beat])
        words = str((spoken or {}).get(beat) or "").split()
        if not words:
            continue
        if audio_words:
            starts = _align_starts(words, audio_words, t0, t1)
        else:
            dur = max(1e-6, t1 - t0)
            starts = [t0 + dur * (i + 0.5) / len(words) for i in range(len(words))]
        for word, start in zip(words, starts):
            if start is None:
                continue
            if t - 0.05 <= float(start) <= limit:
                parts.append(word)
    return " ".join(parts)


def _scene_pose_at(scenes, t):
    """The scene card at t, not the wipe between cards."""
    t = float(t)
    chosen = scenes[-1]
    for scene in scenes:
        if float(scene["start"]) - 1e-6 <= t:
            chosen = scene
        if t < float(scene["end"]) - 1e-9:
            break
    frame = chosen.get("frame")
    anim = getattr(frame, "animate", None)
    if anim is None:
        return frame
    dur = float(chosen["end"]) - float(chosen["start"])
    local = 0.0 if dur <= 0 else (t - float(chosen["start"])) / dur
    return anim(max(0.0, min(1.0, local)))


def _script_word_times(spoken, beat_times, audio_words):
    """(beat, word, start) in script order. Missing whisper times stay proportional."""
    rows = []
    for beat in timed_beats(beat_times):
        if beat not in (beat_times or {}):
            continue
        words = str((spoken or {}).get(beat) or "").split()
        if not words:
            continue
        t0, t1 = (float(v) for v in beat_times[beat])
        if audio_words:
            starts = _align_starts(words, audio_words, t0, t1)
        else:
            starts = None
        dur = max(1e-6, t1 - t0)
        for i, word in enumerate(words):
            start = None if starts is None else starts[i]
            if start is None:
                start = t0 + dur * (i + 0.5) / len(words)
            rows.append((beat, word, float(start)))
    return rows


def _timed_sentences(rows):
    """Sentences that keep each word's spoken start. A danda ends the sentence."""
    sentences = []
    cur = []
    for beat, word, start in rows:
        cur.append((beat, word, start))
        if re.search(r"[।!?？.]$", word):
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    return sentences


_CARD_CONJUNCTIONS = frozenset({
    "और", "तो", "पर", "कि", "या", "लेकिन", "मगर", "तथा", "क्योंकि",
})


def _trim_card_clause(text):
    """Drop a trailing comma or conjunction left when a sentence is cut."""
    words = str(text or "").split()
    while words:
        bare = words[-1].strip("।.!?？…")
        if bare.endswith(",") or bare.endswith("，"):
            bare = bare.rstrip(",，").strip("।.!?？… ")
            if not bare:
                words.pop()
                continue
            words[-1] = bare
            continue
        if bare in _CARD_CONJUNCTIONS:
            words.pop()
            continue
        break
    return " ".join(words)


def _scene_clause(sent, t1):
    """The full caption sentence, unless a later digit is not said yet.

    Words after the last spoken digit stay, so the card does not end on
    "पर" or "और". A digit whose start is still ahead of this scene is omitted.
    """
    cut = len(sent)
    for i, (_beat, word, start) in enumerate(sent):
        if float(start) >= float(t1) - 1e-9 and re.search(r"\d", word or ""):
            cut = i
            break
    if cut == len(sent):
        text = " ".join(word for _beat, word, _start in sent)
    else:
        text = " ".join(word for _beat, word, _start in sent[:cut])
    return _trim_card_clause(text)


def _said_figure_blob(beat, scene_end, rows, pad=0.0):
    """Script words of this beat whose whisper start is not still ahead."""
    limit = float(scene_end) + float(pad)
    words = [
        word for beat_name, word, start in rows or []
        if beat_name == beat and float(start) <= limit
    ]
    return " ".join(words)


def _without_unsaid_figures(text, beat, scene_end, rows):
    """Drop a digit whose whisper start is still after this scene.

    A cue can start on an earlier word and still carry a later number. The
    chart is built from this card, so that number stays off until it is said.
    """
    import sync_gate
    limit = float(scene_end) + sync_gate.SAID_PAD
    late = set()
    said = set()
    for beat_name, word, start in rows or []:
        if beat_name != beat:
            continue
        nums = set(sync_gate.numbers_in(word))
        if not nums:
            continue
        if float(start) <= limit:
            said |= nums
        else:
            late |= nums
    late -= said
    if not late:
        return text
    kept = []
    for word in str(text or "").split():
        nums = set(sync_gate.numbers_in(word))
        if nums and nums <= late:
            continue
        kept.append(word)
    return _trim_card_clause(" ".join(kept))


def scene_cards_for(spoken, beat_times, scenes, audio_words=None):
    """One card per scene, from the caption cues on screen during that scene.

    The card shows those cue sentences in full, so every caption number is an
    on-screen value. A cue that has not started is not painted, and the text
    does not end on a comma or a conjunction. Dedupe is only inside the scene,
    so a later scene that says the figure again still shows it.
    """
    import sync_gate
    cues = plan_cues(spoken, beat_times, audio_words)
    rows = _script_word_times(spoken, beat_times, audio_words) if audio_words else []
    texts = {}
    figures = {}
    for scene in scenes or []:
        beat = scene.get("beat")
        t0, t1 = float(scene["start"]), float(scene["end"])
        parts = []
        latest = ""
        for cue in cues:
            c0, c1 = float(cue["t0"]), float(cue["t1"])
            text = _trim_card_clause(cue.get("text") or "")
            if not text:
                continue
            if c0 < t1 - 1e-9:
                latest = text
            spoken_at = cue.get("word_start")
            spoken_in = (
                spoken_at is not None
                and t0 - 1e-6 <= float(spoken_at) < t1 - 1e-6
            )
            # Visible caption, or a figure already said whose cue was clamped
            # to the next beat. A cue that has not started stays off this card.
            visible = c1 > t0 + 1e-6 and c0 < t1 - 1e-6
            if spoken_in or visible:
                if text not in parts:
                    parts.append(text)
        if not parts and latest:
            parts.append(latest)
        blob = scene_frames.strip_example_label(_trim_card_clause(" ".join(parts)))
        if audio_words:
            blob = scene_frames.strip_example_label(
                _without_unsaid_figures(blob, beat, t1, rows)
            )
        texts.setdefault(beat, []).append(blob)
        # Digits stay on the card's own gate. Hindi amounts (दो लाख, चार हज़ार)
        # become ₹ figures only when those words have started, and only when
        # the beat does not also write that amount as digits.
        said = _said_figure_blob(beat, t1, rows) if audio_words else blob
        beat_digits = set(sync_gate.numbers_in((spoken or {}).get(beat) or ""))
        raw = list(scene_frames._figure_tokens([blob]))
        for label in scene_frames._spoken_figures(said, scene_frames._digit_tokens(said)):
            nums = set(sync_gate.numbers_in(label))
            if beat_digits and nums and nums <= beat_digits:
                continue
            if label not in raw:
                raw.append(label)
        kept = []
        shown = set()
        for label in raw:
            nums = frozenset(sync_gate.numbers_in(label))
            if nums and nums <= shown:
                continue
            if nums:
                shown |= set(nums)
            kept.append(label)
        figures.setdefault(beat, []).append(kept)
    return texts, figures


def _install_scene_cards(topic, spoken, beat_times, scenes, audio_words=None):
    texts, figures = scene_cards_for(spoken, beat_times, scenes, audio_words)
    topic["_scene_cards"] = texts
    topic["_scene_figures"] = figures
    # The level chart reads these so a candle can start on a spoken word.
    # Both the live build and the no-TTS rerender come through here.
    topic["_audio_words"] = list(audio_words) if audio_words else None
    topic["_beat_times"] = {
        str(beat): (float(span[0]), float(span[1]))
        for beat, span in (beat_times or {}).items()
        if span and len(span) >= 2
    }
    topic["_scene_plan"] = [
        {
            "beat": scene.get("beat"),
            "start": float(scene["start"]),
            "end": float(scene["end"]),
            "index": scene.get("index", 0),
        }
        for scene in (scenes or [])
    ]
    import trade_plan
    if trade_plan.is_plan_topic(topic):
        topic["_chart_overlays"] = trade_plan.chart_overlay_events(
            topic, topic["_beat_times"], topic.get("_audio_words") or [],
        )


def _gemini_word_timings(beat_times):
    """Words from the one Gemini file, shifted onto each trimmed beat."""
    path = RUN / "gemini_words.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    per_beat = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(per_beat, dict):
        return None
    import sync_gate
    words = []
    for beat in timed_beats(beat_times):
        rows = per_beat.get(beat)
        if not rows:
            return None
        source = _beat_tts_path(beat)
        offset = 0.0
        if source is not None:
            try:
                offset = sync_gate.speech_offset(sync_gate.pcm(source))
            except Exception:  # noqa: BLE001 — the stored clock is still usable
                offset = 0.0
        t0 = float(beat_times[beat][0]) - offset
        for row in rows:
            text = str(row.get("word") or "").strip()
            if not text:
                continue
            start = float(row.get("start") or 0) + t0
            end = float(row.get("end") or 0) + t0
            if end <= float(beat_times[beat][0]) - 0.05:
                continue
            words.append({"word": text, "start": start, "end": end})
    return words or None


def _word_timings(beat_audio, beat_times):
    """Whisper words for the caption windows. None when transcription fails.

    A Gemini Short already has one faster-whisper pass on the whole file.
    Those timestamps time the captions and the chart cues. Other engines
    transcribe each beat, which is the same gate.
    """
    stored = _gemini_word_timings(beat_times)
    if stored:
        return stored
    import sync_gate
    order = timed_beats(beat_times)
    sources = {}
    for beat in order:
        source = _beat_tts_path(beat)
        if source is not None:
            sources[beat] = source
    return sync_gate.transcribe_beats(
        beat_audio, beat_times, beats=order, sources=sources or None,
    )


def _scene_at(scenes, t):
    chosen = scenes[-1]
    for scene in scenes:
        if float(scene["start"]) - 1e-6 <= t:
            chosen = scene
        if t < float(scene["end"]) - 1e-9:
            break
    return chosen


def _frame_lesson_text(frame):
    return [
        layer.get("text") or ""
        for layer in (getattr(frame, "text_layers", None) or [])
        if layer.get("text") and not layer.get("faint")
    ]


def number_faults_for(scenes, spoken, beat_times, audio_words=None, topic=None):
    """Sample each second. Caption and spoken numbers must be on that frame.

    A figure already visible on an earlier card of the same beat counts, so
    a later card does not have to repeat it. The 0.85s window stops at the
    scene end, so the next scene's figure is not demanded early.
    """
    # A labelled उदाहरण still has to be the figure drawn on that frame.
    ignore = set()
    frames = [s.get("frame") for s in scenes]
    if not frames or any(frame is None for frame in frames):
        return []
    durations = [float(s["end"]) - float(s["start"]) for s in scenes]
    total = sum(durations)
    cues = plan_cues(spoken, beat_times, audio_words)
    notes = []
    seen = set()
    earlier = {}
    t = 0.0
    while t < total - 0.05:
        scene = _scene_at(scenes, t)
        frame = _scene_pose_at(scenes, t)
        texts = _frame_lesson_text(frame)
        beat = scene.get("beat")
        prior = earlier.get(beat, "")
        on_screen = list(texts)
        if prior:
            on_screen.append(prior)
        caption = " ".join(cue.get("text") or "" for cue in cues_at(cues, t))
        said = _script_near(
            spoken, beat_times, audio_words, t, scene_end=float(scene["end"]),
        )
        for note in number_format_notes(on_screen, said, caption, ignore=ignore):
            stamped = f"{note} at {t:.1f}s"
            if stamped not in seen:
                seen.add(stamped)
                notes.append(stamped)
        blob = " ".join(texts)
        if blob:
            earlier[beat] = (prior + " " + blob).strip()
        t += 1.0
    return notes


def cue_faults(cues):
    """More than one cue in a frame, more than two lines, or a cue ahead of its words."""
    notes = []
    ordered = sorted(cues or [], key=lambda cue: (float(cue["t0"]), float(cue["t1"])))
    for i, cue in enumerate(ordered):
        lines = cue.get("lines") or _wrap_caption(cue.get("text") or "")
        if len([ln for ln in lines if str(ln).strip()]) > 2:
            notes.append(f"cue has {len(lines)} lines at {float(cue['t0']):.2f}s")
        ws = cue.get("word_start")
        if ws is not None and float(cue["t0"]) + 1e-3 < float(ws) - 0.02:
            notes.append(
                f"cue before audio at {float(cue['t0']):.2f}s (word {float(ws):.2f}s)"
            )
        if i and float(ordered[i - 1]["t1"]) > float(cue["t0"]) + 1e-3:
            notes.append(f"two cues visible at {float(cue['t0']):.2f}s")
            break
    return notes


def closing_span(beat_times):
    """Disclaimer window. Plan topics end on cta. Four-beat topics end on karein.

    A missing name is the last beat on the clock. It is never a KeyError.
    """
    times = beat_times or {}
    for name in ("karein", "cta"):
        span = times.get(name)
        if span and len(span) >= 2:
            return (float(span[0]), float(span[1]))
    last = None
    for span in times.values():
        if not span or len(span) < 2:
            continue
        start, end = float(span[0]), float(span[1])
        if last is None or end > last[1]:
            last = (start, end)
    return last


def on_screen_boxes(topic, beat_times, spoken, hook_line, cta, t, audio_words=None):
    """Boxes for the one spoken cue at t, plus the grey disclaimer on the last beat.

    The poll stays on the recap card. Headlines and key lines are not burned.
    """
    import safe_area
    del topic, hook_line, cta
    boxes = []
    t = float(t)
    for cue in cues_at(plan_cues(spoken, beat_times, audio_words), t):
        boxes.extend(row[1] for row in _cue_rows(cue["text"]))
        break
    span = closing_span(beat_times)
    if span and span[0] - 1e-6 <= t <= span[1] + 1e-6:
        boxes.append(_top_box(DISCLAIMER, 540, safe_area.SOURCE_Y, DISCLAIMER_SIZE))
    return boxes


def _layer_boxes(frame):
    return [
        layer["box"] for layer in (getattr(frame, "text_layers", None) or [])
        if layer.get("text") and layer.get("box") and not layer.get("faint")
    ]


def frame_text_boxes(scenes, topic, beat_times, spoken, hook_line, cta, fps=30, audio_words=None):
    """Every frame: scene lesson boxes plus the burned subtitle boxes.

    Animated scenes are redrawn at a few phases and reused, so this stays a
    box check rather than a full encode.
    """
    frames = [s.get("frame") for s in scenes]
    if not frames or any(frame is None for frame in frames):
        return None
    durations = [float(s["end"]) - float(s["start"]) for s in scenes]
    total = sum(durations)
    starts = []
    acc = 0.0
    for dur in durations:
        starts.append(acc)
        acc += dur
    cache = {}

    def scene_boxes(index, t):
        frame = frames[index]
        anim = getattr(frame, "animate", None)
        if anim is None:
            return _layer_boxes(frame)
        local = 0.0 if durations[index] <= 0 else (t - starts[index]) / durations[index]
        key = (index, round(max(0.0, min(1.0, local)) * 8) / 8)
        if key not in cache:
            cache[key] = _layer_boxes(anim(key[1]))
        return cache[key]

    text_frames = []
    n = max(1, int(math.ceil(total * fps)))
    for i in range(n):
        t = min(total - 1e-3, i / fps)
        index = 0
        for k, start in enumerate(starts):
            if start <= t:
                index = k
        boxes = list(scene_boxes(index, t))
        extra = on_screen_boxes(
            topic, beat_times, spoken, hook_line, cta, t, audio_words=audio_words)
        layers = getattr(frames[index], "text_layers", None) or []
        if any(layer.get("role") == "caption" and layer.get("text") for layer in layers):
            # The Chart Board already paints the cue under the chart. The
            # unburned cue box would sit on those same pixels.
            import safe_area
            extra = [box for box in extra if float(box[1]) >= safe_area.BRAND_TOP - 0.5]
        boxes.extend(extra)
        text_frames.append((t, boxes))
    return text_frames


def _craft_samples(scenes, topic, beat_times, spoken, hook_line, cta, audio_words=None):
    """One frame a second for the still check, and every frame for text boxes."""
    frames = [s.get("frame") for s in scenes]
    if not frames or any(frame is None for frame in frames):
        missing = [
            str(scene.get("beat") or "?")
            for scene, frame in zip(scenes, frames)
            if frame is None
        ]
        notes = [f"scene has no frame: {beat}" for beat in missing] or ["scene has no frame"]
        return [], [], notes
    durations = [float(s["end"]) - float(s["start"]) for s in scenes]
    total = sum(durations)
    samples = []
    layout_notes = []
    seen = set()
    t = 0.0
    while t < total - 0.05:
        frame = scene_transition.render_at(frames, durations, t, SCENE_TRANSITIONS, SCENE_FADE_S)
        for note in scene_frames.frame_layout_notes(frame):
            if note not in seen:
                seen.add(note)
                layout_notes.append(f"{note} at {t:.1f}s")
        samples.append((t, np.asarray(frame.convert("RGB"))))
        t += 1.0
    cues = plan_cues(spoken, beat_times, audio_words)
    for note in cue_faults(cues):
        if note not in seen:
            seen.add(note)
            layout_notes.append(note)
    for note in number_faults_for(scenes, spoken, beat_times, audio_words, topic=topic):
        if note not in seen:
            seen.add(note)
            layout_notes.append(note)
    as_of = None
    spec = (topic or {}).get("_chart_spec") or {}
    atr = (topic or {}).get("_atr") or spec.get("atr") or {}
    as_of = atr.get("as_of") or spec.get("as_of")
    for note in stale_data_notes(
        as_of, date.today(), allow_stale_sample=bool((topic or {}).get("_allow_stale_sample")),
    ):
        if note not in seen:
            seen.add(note)
            layout_notes.append(note)
    text_frames = frame_text_boxes(
        scenes, topic, beat_times, spoken, hook_line, cta, audio_words=audio_words)
    return samples, text_frames, layout_notes


def _ass_rows(rows, t0, t1, colour, fade):
    ev = []
    for piece, _rect, size, y in rows:
        ev.append(K.ass_text(t0, t1, 540, y, size, colour, piece, 8, fade=fade, wrap=False))
    return ev


def build_ass(topic, beat_times, spoken, hook_line, cta, audio_words=None):
    import safe_area
    del topic, hook_line, cta, spoken, audio_words
    # The card already shows the spoken line at a phone-readable size. A 40px
    # bottom caption is too small to read and sits on the card, so it is not
    # burned. plan_cues still times those lines for the number check. Sync
    # reads the audio, not this track.
    ev = []
    span = closing_span(beat_times)
    if span:
        t0, t1 = span
        # Grey disclaimer sits in the brand band, under the fixed subtitle box.
        ev.append(K.ass_text(t0, t1, 540, safe_area.SOURCE_Y, DISCLAIMER_SIZE, "&H00B8A394&", DISCLAIMER, 8))
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
    """One card, one cut, with a slow 3% zoom so a held frame is not frozen."""
    dur = max(0.4, float(dur))
    frames = max(2, int(round(dur * fps)))
    zoom = f"min(1+0.03*on/{frames - 1},1.03)"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-loop", "1", "-framerate", str(fps), "-i", str(png),
         "-vf",
         f"scale={K.W}:{K.H}:force_original_aspect_ratio=increase,crop={K.W}:{K.H},"
         f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
         f"d=1:s={K.W}x{K.H}:fps={fps},format=yuv420p",
         "-frames:v", str(frames), "-r", str(fps), *X264, str(out)],
        check=True,
    )


def _join_hard(clips, out):
    """One card, then the next. A slide leaves the old words on the new card."""
    import pathlib
    listing = pathlib.Path(out).parent / "scene_list.txt"
    listing.write_text(
        "\n".join(f"file '{pathlib.Path(clip).name}'" for clip in clips) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", listing.name, "-c", "copy", pathlib.Path(out).name],
        cwd=listing.parent, check=True,
    )


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
        for scene in scenes:
            png = scene.get("png")
            if not png:
                pics = beat_pngs[scene["beat"]]
                png = pics[min(scene["index"], len(pics) - 1)]
                scene["png"] = str(png)
            out = RUN / f"scene_{len(clips):02d}.mp4"
            still_clip(png, durations[len(clips)], out, fps)
            clips.append(out)
        _join_hard(clips, RUN / "silent.mp4")
    # voice: each beat's audio padded to its beat length
    parts = []
    for b in timed_beats(beat_times):
        t0, t1 = beat_times[b]
        wav = RUN / f"voice_{b}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(beat_audio[b]), "-af",
                        f"apad,atrim=0:{t1 - t0:.3f}", "-ar", "44100", "-ac", "2", str(wav)], check=True)
        parts.append(wav.name)
    (RUN / "voice_list.txt").write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "voice_list.txt",
                    "-c", "copy", "voice.wav"], cwd=RUN, check=True)
    # voice.wav stays the clean narration so a later remix needs no new TTS.
    # Whoosh and the callout click go on mixed.wav. There is no bed and no air.
    # A trade-plan frame carries motion_reveals: those cues get the library
    # whoosh, about 20 dB under the voice. Every other short keeps mix_into.
    mixed = RUN / "mixed.wav"
    import motion_lib
    reveals = motion_lib.unique_reveals(frames)
    if reveals:
        motion_lib.mix_under(RUN / "voice.wav", reveals, mixed)
    else:
        bed_audio.mix_into(RUN / "voice.wav", scenes, mixed)
    if str(hook_line or "").strip():
        hook_sfx.mix_file(mixed, hook_motion.click_times(hook_line))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mixed),
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-c:a", "aac", "-b:a", "192k", "audio.m4a"], cwd=RUN, check=True)
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
    body_beats = timed_beats(beat_times)
    segments = []
    for x in body_beats:
        if x == "hook":
            continue
        segments.append(f"[SEGMENT {x.upper()}]\n{b.get(x, '')}")
    script = (f"TITLE:\n{n['title']}\n\nHOOK (0-3 sec):\n{b['hook']}\n\nBODY:\n"
              + "\n\n".join(segments)
              + f"\n\nCTA:\n{cta}\n\nDISCLAIMER LINE:\n{DISCLAIMER}\n\n"
              f"FACT LEDGER:\n{facts}\n")
    save("script.txt", script)
    save("spoken.txt", " ".join(spoken[x] for x in body_beats))
    shots = []
    hook_line = n.get("hook_line") or ""
    import trade_plan
    plan_shots = trade_plan.is_plan_topic(topic) and trade_plan.has_plan(topic.get("key"))
    for i, x in enumerate(body_beats, 1):
        t0, t1 = beat_times[x]
        head, sub = card_pair(topic, x)
        on_screen = hook_line if x == "hook" else head
        if plan_shots:
            import sync_gate
            said = sync_gate.content_tokens(spoken.get(x) or "")
            if said:
                on_screen = max(said, key=len)
        shots.append({"shot_id": i, "beat": x, "start_sec": round(t0, 2), "end_sec": round(t1, 2), "visual_type": "chart",
                      "on_screen_text": on_screen, "sub_text": sub,
                      "spoken_line": spoken[x],
                      "chart_data": {"template": topic["key"], "mode": spec.get("mode"),
                                     "source": spec.get("source"), "as_of": spec.get("as_of")},
                      "source_asset_query": None, "emphasis": "neutral",
                      "hook_line": hook_line if x == "hook" else None})
    (RUN / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- visual QA gate
def _reject_topic_chart_gate(topic):
    """Approved topic videos must have eight beats. The Nifty data Short does not."""
    if str((topic or {}).get("key") or "") == "aaj_ka_nifty":
        return
    import pattern_charts
    pattern_charts.reject_chart_gate()


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


def _sync_report(topic, narration, scenes, beat_audio, beat_times, spec, words=None):
    """Transcribe the beat audio and fail closed on a mismatch or a whisper error."""
    import sync_gate
    try:
        if words is None:
            words = _word_timings(beat_audio, beat_times)
        rows = []
        for scene in scenes:
            frame = scene.get("frame")
            # The stored pose is the cut. An animated scene's lesson is the
            # picture held through the slice, not the handoff frame.
            if frame is not None and getattr(frame, "animate", None):
                frame = frame.animate(0.5)
            rows.append({
                "start": scene["start"],
                "end": scene["end"],
                "beat": scene["beat"],
                "kind": scene.get("kind") or "",
                "show": sync_gate.lesson_lines(frame) if frame is not None else [],
            })
        lesson = (spec or {}).get("atr") or topic.get("_atr")
        terms = sync_gate.answer_terms_for(narration.get("title") or "", lesson)
        report = sync_gate.upgrade_weak_scenes(
            words, rows, narration.get("beats") or {}, beat_times, terms, beat_audio,
        )
        words = report.get("words") or words
        report["words"] = [
            {"word": w.get("word") or "", "start": float(w.get("start") or 0),
             "end": float(w.get("end") or 0)}
            for w in words
        ]
    except Exception as exc:  # noqa: BLE001
        report = {
            "ok": False,
            "reason": f"transcription failed: {exc}",
            "transcript": "",
            "scenes": [],
        }
    if report.get("ok"):
        ok("sync: narration matches the title and each scene")
        if report.get("title_note"):
            print("[REVIEW] " + report["title_note"])
    else:
        print("[HOLD] sync: " + (report.get("reason") or "failed"))
    for row in report.get("beats") or []:
        print(
            f"  [sync-beat] {row.get('beat')} recall {row.get('recall')}\n"
            f"    script: {row.get('script')}\n"
            f"    heard:  {row.get('heard')}"
        )
    for row in report.get("scenes") or []:
        print(
            f"  [sync] {row.get('kind') or row.get('beat')} "
            f"{row.get('start')}-{row.get('end')} jaccard {row.get('jaccard')} "
            f"shared {row.get('shared')} missing {row.get('missing_numbers')}"
        )
    return report


# ---------------------------------------------------------------- main
def load_topic():
    p = RUN / "topic.json"
    if not p.exists():
        die("run/topic.json missing — pick_topic.py pehle chalao.")
    return json.loads(p.read_text(encoding="utf-8"))


def _trim_rendered(beats):
    raw = {}
    for beat in beats:
        src = _beat_tts_path(beat)
        if src is None:
            die(f"TTS beat {beat} has no audio file")
        clean = RUN / f"voice_clean_{beat}.wav"
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-i", str(src),
            "-af",
            "silenceremove=start_periods=1:start_duration=0.10:start_threshold=-55dB,"
            "areverse,silenceremove=start_periods=1:start_duration=0.10:start_threshold=-55dB,areverse",
            "-ar", "44100", "-ac", "2", str(clean),
        ], check=True)
        seconds = duration(clean)
        if seconds < 1.0:
            die(f"TTS beat {beat} became too short after silence trim: {seconds:.2f}s")
        raw[beat] = seconds
    return raw


def _timeline(raw, order):
    seam = 0.02
    beat_times, total = {}, 0.0
    for beat in order:
        pad = seam if beat != order[-1] else 0.0
        span = raw[beat] + pad
        beat_times[beat] = (total, total + span)
        total += span
    return beat_times, total


def _duration_detail(spoken, raw, order):
    return ", ".join(
        f"{beat}={raw[beat]:.1f}s/{len(str(spoken.get(beat) or '').split())}w" for beat in order
    )


def _live_estimate(narration, topic):
    """Words, pace, and estimated seconds. This is the pre-TTS credit check."""
    order = _lesson(topic)[0]
    beats = (narration or {}).get("beats") or {}
    words = sum(len(str(beats.get(beat) or "").split()) for beat in order)
    wps = voice_wps(_topic_voice(topic))
    seconds = (words / wps) if wps else 0.0
    return words, wps, seconds


def _estimate_note(narration, topic):
    """None when words/pace lands in the live duration gate. Otherwise skip TTS."""
    if not (topic or {}).get("_live_short") or (topic or {}).get("_atr"):
        return None
    words, wps, seconds = _live_estimate(narration, topic)
    lo, hi = live_voice_bounds(topic)
    if lo <= seconds <= hi:
        return None
    return (
        f"estimated {seconds:.1f}s ({words} words / {wps:.2f} w/s) "
        f"outside {lo:.0f}-{hi:.0f}"
    )


def beats_below_minimum(narration, topic):
    """Live beats whose word count is under the window. Empty when none are."""
    if not (topic or {}).get("_live_short") or (topic or {}).get("_atr"):
        return []
    order, targets, _, _ = _lesson(topic)
    beats = (narration or {}).get("beats") or {}
    under = []
    for beat in order:
        lo, _hi = beat_word_window(targets[beat], LIVE_BEAT_TOLERANCE)
        if len(str(beats.get(beat) or "").split()) < lo:
            under.append(beat)
    return under


def _margin_budget(topic):
    """Seconds that trigger a free trim, and the word budget that trim aims at."""
    if four_live(topic):
        return FOUR_LIVE_MARGIN_S, four_margin_words(voice_wps(_topic_voice(topic)))
    return LIVE_ESTIMATE_MARGIN_S, LIVE_MARGIN_WORDS


def tighten_duration_margin(narration, topic):
    """Before TTS, a long live estimate is trimmed. Six-beat: over 90s toward 170.

    Four-beat: over 59s toward about 112 words at 1.9 w/s. No model call and no
    voice request. The hook's first question and the udaharan core stay.
    After the trim the beat windows are checked again. A beat that fell under
    its minimum is locked and a different beat is trimmed instead.
    """
    if not isinstance(narration, dict):
        return narration
    if not (topic or {}).get("_live_short") or (topic or {}).get("_atr"):
        return narration
    cap_s, budget = _margin_budget(topic)
    words, _wps, seconds = _live_estimate(narration, topic)
    if seconds <= cap_s:
        return narration
    print(
        f"[duration] estimate {seconds:.1f}s ({words} words) over "
        f"{cap_s:.0f}s; trimming toward {budget} words",
        flush=True,
    )
    saved = json.loads(json.dumps(narration.get("beats") or {}))
    apply_live_draft(narration, topic, total_hi=budget)
    under = beats_below_minimum(narration, topic)
    if under:
        print(
            f"[duration] {', '.join(under)} fell under the minimum; trimming a different beat",
            flush=True,
        )
        narration["beats"] = json.loads(json.dumps(saved))
        apply_live_draft(narration, topic, total_hi=budget, locked=under)
        if beats_below_minimum(narration, topic):
            narration["beats"] = json.loads(json.dumps(saved))
    return narration


def _dry_fails(narration, topic, spec):
    fails = check_narration(narration, topic)
    order = _lesson(topic)[0]
    vague = scene_pace.vague_beats(narration.get("beats"), order=order)
    if vague:
        fails.append("vague explanation (no number, level or step): " + ", ".join(vague))
    if not fails:
        content = _content_ok(topic, narration, spec)
        if content and not content["passed"]:
            fails.append("self-review below bar: " + content["summary"])
    return fails


def _one_beat_repair(narration, topic, c, spec, why):
    """One DeepSeek rewrite of the failing beats. None when the cap is spent."""
    used = int((credit_log._read() or {}).get("deepseek_calls") or 0)
    if credit_log.call_cap() - used < 1:
        return None
    rewrite = beats_to_rewrite(narration, topic, why)
    if not rewrite:
        return None
    user = json.dumps({
        "topic": topic.get("topic"),
        "category": topic.get("cat"),
        "facts": topic.get("facts"),
    }, ensure_ascii=False)
    user += "\n\n" + repair_instructions(
        narration, rewrite, topic, failures=narration_gate_failures(narration, topic, why),
    )
    user += "\n\n" + why
    system = live_narration_system(topic) if topic.get("_live_short") else NARR_SYSTEM
    try:
        repaired = claude_json(
            system, user, model=c["models"]["script_model"], max_tokens=2200,
            retries=1, attempts=1,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        die(f"beat repair failed without a template fallback: {exc}")
    if not _usable_narration(repaired, topic):
        die("beat repair returned an empty script; refusing a template and a speed change")
    merged = merge_repair(narration, repaired, rewrite, topic)
    narration["beats"] = merged["beats"]
    narration["title"] = merged["title"]
    narration["hook_line"] = merged["hook_line"]
    narration["review_attempts"] = int(narration.get("review_attempts") or 1) + 1
    narration["narration_fallback"] = False
    narration["source"] = "llm"
    return rewrite


def _collect_dry_fails(narration, topic, spec):
    fails = list(_dry_fails(narration, topic, spec))
    note = _estimate_note(narration, topic)
    if note:
        fails.append(note)
    return fails


def fit_estimated_duration(narration, topic, c, spec):
    """Text gates before any TTS request. Trim a long live script, then judge it.

    The duration estimate is words / per-voice pace. The margin trim runs before
    that guard. Still outside the live window (45-65s or 70-95s), no voice
    request is made.
    """
    live = bool(topic.get("_live_short") and not topic.get("_atr"))
    if live:
        apply_live_draft(narration, topic)
        already_short = set(beats_below_minimum(narration, topic))
        tighten_duration_margin(narration, topic)
        under = [beat for beat in beats_below_minimum(narration, topic) if beat not in already_short]
        if under:
            print(
                "[duration] margin trim left "
                + ", ".join(under)
                + " under the minimum; repairing with topic facts",
                flush=True,
            )
            expand_short_live_beats(narration, topic)
    fails = _collect_dry_fails(narration, topic, spec)
    if not fails:
        return narration
    why = "; ".join(fails)
    print(f"[duration] dry check failed before TTS: {why}")
    lo, hi = live_voice_bounds(topic) if live else LIVE_VOICE_SECONDS
    label = f"{lo:.0f}-{hi:.0f}"
    if _one_beat_repair(narration, topic, c, spec, why) is None:
        die(f"estimated duration outside {label}s; refusing TTS with 0 characters spent")
    if live:
        apply_live_draft(narration, topic)
        tighten_duration_margin(narration, topic)
        under = beats_below_minimum(narration, topic)
        if under:
            die(
                "margin trim left "
                + ", ".join(under)
                + " under the beat minimum; refusing TTS with 0 characters spent"
            )
    fails = _collect_dry_fails(narration, topic, spec)
    if fails:
        if live:
            _words, _wps, seconds = _live_estimate(narration, topic)
            if seconds < lo or seconds > hi:
                die(
                    f"estimated duration outside {label}s; refusing TTS with 0 characters spent: "
                    + "; ".join(fails)
                )
        die(
            "dry text check still failed after one repair; refusing TTS and the topic-card template: "
            + "; ".join(fails)
        )
    (RUN / "narration.json").write_text(
        json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8")
    return narration


def _rerender_beats(spoken, c, beats):
    """Speak only the beats whose words changed. Leave the others on disk.

    Gemini is one request for the whole Short. A later duration repair does
    not call it again, and it does not leave some beats on Charon.
    """
    gemini = []
    for beat in list(beats) + list(BEATS):
        engine_file = RUN / f"tts_{beat}.engine"
        try:
            if engine_file.exists() and engine_file.read_text(encoding="utf-8").strip() == "gemini":
                if beat not in gemini:
                    gemini.append(beat)
        except OSError:
            pass
    if gemini:
        beats = tuple(dict.fromkeys([*gemini, *beats]))
        print("[voice] Gemini audio missed the duration gate; re-voicing every beat on the fallback")
        _drop_gemini_audio(beats)
    need = sum(len(_voice_text(str(spoken.get(beat) or ""))) for beat in beats)
    floor = eleven_voice.LIVE_CREDIT_FLOOR
    switched = False
    if (
        not ELEVEN_EXHAUSTED
        and ELEVEN_REMAINING is not None
        and ELEVEN_REMAINING - need < floor
    ):
        _switch_to_edge(
            f"re-render of {len(beats)} beat(s) needs {need} characters and would leave "
            f"{ELEVEN_REMAINING - need} below {floor}"
        )
        switched = True
    for beat in beats:
        for name in (
            f"tts_{beat}.mp3", f"tts_{beat}.wav", f"tts_{beat}.engine",
            f"tts_{beat}.textid", f"voice_clean_{beat}.wav",
        ):
            (RUN / name).unlink(missing_ok=True)
        try:
            tts(spoken[beat], RUN / f"tts_{beat}.mp3", c)
        except eleven_voice.QuotaFallback as exc:
            _switch_to_edge(exc.reason)
            switched = True
            tts(spoken[beat], RUN / f"tts_{beat}.mp3", c)
    if switched:
        _write_spoken_engine("edge")
    return _trim_rendered(beats)


def _static_craft_samples(topic, narration, spec):
    """Scene pictures on the estimated timeline. No voice and no paid call."""
    order = _lesson(topic)[0]
    beats = (narration or {}).get("beats") or {}
    weights = [max(8, len(str(beats.get(beat) or "").split())) for beat in order]
    wps = max(float(voice_wps(_topic_voice(topic)) or LIVE_BASE_WPS), 0.1)
    total = max(16.0, sum(weights) / wps)
    weight_sum = float(sum(weights)) or 1.0
    beat_times = {}
    cursor = 0.0
    for beat, weight in zip(order, weights):
        span = total * weight / weight_sum
        beat_times[beat] = (cursor, cursor + span)
        cursor += span
    scenes = scene_pace.plan_scenes(beat_times, beats=order)
    spoken_map = {beat: str(beats.get(beat) or "") for beat in order}
    _install_scene_cards(topic, spoken_map, beat_times, scenes, None)
    groups = {}
    for scene in scenes:
        groups.setdefault(scene["beat"], []).append(scene)
    frames = []
    built = []
    for beat in order:
        group = groups.get(beat) or []
        if not group:
            continue
        made, _flags = scene_frames.frames_for(topic, beat, len(group), spec or {})
        if len(made) < len(group) or any(frame is None for frame in made):
            die(f"scene has no frame before TTS: {beat}")
        for scene, frame in zip(group, made):
            scene["frame"] = frame
            built.append(scene)
            frames.append(frame)
    durations = [float(scene["end"]) - float(scene["start"]) for scene in built]
    span = sum(durations)
    samples = []
    layout_notes = []
    seen = set()
    t = 0.0
    while built and t < span - 0.05:
        frame = scene_transition.render_at(
            frames, durations, t, SCENE_TRANSITIONS, SCENE_FADE_S)
        for note in scene_frames.frame_layout_notes(frame):
            if note not in seen:
                seen.add(note)
                layout_notes.append(note)
        samples.append((t, np.asarray(frame.convert("RGB"))))
        t += 1.0
    return built, samples, layout_notes


def _hard_visual_notes(outside, notes):
    """Safe area and a truly empty frame stop the render. Other craft notes do not."""
    hard = list(outside or [])
    for note in notes or []:
        if any(mark in note for mark in ("empty background", "safe area", "no picture", "no scenes")):
            if note not in hard:
                hard.append(note)
    return hard


def _refuse_craft_hold(topic, spec, narration):
    """Die only for a hard visual failure, before any ElevenLabs character.

    A soft craft note (repeated text, panel fill, a still span) is logged as
    REVIEW and the render continues. Live still will not auto-publish a REVIEW
    without approved=true.
    """
    scenes, samples, layout_notes = _static_craft_samples(topic, narration, spec)
    labels = []
    for scene in scenes:
        frame = scene.get("frame")
        if frame is None:
            continue
        labels.extend(getattr(frame, "label_boxes", None) or [])
    outside = scene_frames.frames_safe_area_notes(
        [scene.get("frame") for scene in scenes if scene.get("frame") is not None]
    )
    _craft, notes = self_review.score_craft(
        narration, scenes=scenes, samples=samples, labels=labels, layout_notes=layout_notes)
    hard = _hard_visual_notes(outside, notes)
    if hard:
        die(
            "craft would HOLD before TTS; refusing with 0 characters spent: "
            + "; ".join(hard[:4])
        )
    soft = [note for note in notes if note not in hard]
    if soft:
        print(
            "[REVIEW] soft craft — render continues; public upload needs approved=true: "
            + "; ".join(soft[:4]),
            flush=True,
        )


def require_scene_frames(topic, spec, narration=None):
    """Die before TTS when a lesson beat has no picture, or craft would hold."""
    if (topic or {}).get("_atr"):
        return
    import board_scenes
    reason = board_scenes.topic_chart_block(topic)
    if reason:
        die(reason + "; refusing TTS with 0 characters spent")
    order = _lesson(topic)[0] if (topic or {}).get("_live_short") else BEATS
    missing = []
    for beat in order:
        try:
            frames, _flags = scene_frames.frames_for(topic, beat, 1, spec or {})
        except Exception as exc:  # noqa: BLE001 — a render error is a missing frame
            die(f"scene has no frame before TTS: {beat} ({exc})")
        if not frames or frames[0] is None:
            missing.append(beat)
    if missing:
        die("scene has no frame before TTS: " + ", ".join(missing))
    if four_live(topic):
        _refuse_craft_hold(topic, spec, narration or {})


def _write_silence_wav(path, seconds, rate=44100):
    """PCM silence. The proof timeline uses words / 1.9s, not a paid voice."""
    import wave
    seconds = max(0.4, float(seconds))
    frames = max(1, int(seconds * rate))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames * 2)


def _proof_wps(topic, c):
    voice = (topic or {}).get("_voice") or ((c or {}).get("voice") or {})
    try:
        wps = float(voice.get("words_per_second") or 1.9)
    except (TypeError, ValueError):
        wps = 1.9
    return wps if wps > 0 else 1.9


def _proof_silence_spoken(spoken, narration, topic, c, spec):
    """Free audio timed to the voice pace. No ElevenLabs and no Edge network."""
    order = _lesson(topic)[0] if topic.get("_live_short") else tuple(b for b in BEATS if b in spoken)
    if topic.get("_live_short"):
        require_scene_frames(topic, spec, narration)
        if not _approved_narration(narration, topic):
            narration = fit_estimated_duration(narration, topic, c, spec)
        spoken = {beat: narration["beats"][beat] for beat in order}
    wps = _proof_wps(topic, c)
    raw = {}
    for beat in order:
        words = max(1, len(str(spoken.get(beat) or "").split()))
        seconds = max(0.4, words / wps)
        _write_silence_wav(RUN / f"voice_clean_{beat}.wav", seconds)
        raw[beat] = seconds
    beat_times, total = _timeline(raw, order)
    if topic.get("_live_short"):
        lo, hi = live_voice_bounds(topic)
        if total < lo or total > hi:
            die(
                f"proof timeline outside {lo:.0f}-{hi:.0f}s before paid TTS; "
                f"0 characters spent: {total:.1f}s"
            )
    topic["_spoken_beats"] = {beat: spoken[beat] for beat in order}
    audio = {beat: RUN / f"voice_clean_{beat}.wav" for beat in order}
    return spoken, narration, audio, raw, beat_times, total


def prepare_spoken(spoken, narration, topic, c, spec):
    """Render once after the dry check. A live miss re-speaks only the changed beats."""
    if os.environ.get("PROOF_FREE_AUDIO", "").strip().lower() == "silence":
        return _proof_silence_spoken(spoken, narration, topic, c, spec)
    order = _lesson(topic)[0] if topic.get("_live_short") else tuple(b for b in BEATS if b in spoken)
    if topic.get("_live_short"):
        require_scene_frames(topic, spec, narration)
        # Adding a library voice is a write. It runs only on the paid pass,
        # after PAID_TTS_ENABLED is checked, not on a path that skips the proof.
        topic["_voice"] = dict((c or {}).get("voice") or {})
        # An approved dry-run script is voiced as stored. No sentence trim.
        if not _approved_narration(narration, topic):
            narration = fit_estimated_duration(narration, topic, c, spec)
        spoken = {beat: narration["beats"][beat] for beat in order}
    (RUN / "tts_text.txt").unlink(missing_ok=True)
    before = dict(spoken)
    global _VOICE_TOPIC
    _VOICE_TOPIC = topic
    try:
        engine = synthesize_short_voices(spoken, c, beats=order)
    finally:
        _VOICE_TOPIC = None
    raw = _trim_rendered(order)
    beat_times, total = _timeline(raw, order)
    if not topic.get("_live_short"):
        if total < 45.0:
            print(f"[duration] natural speech is {total:.1f}s; no slow-motion stretching")
        if total < 42.0 or total > 62.0:
            detail = _duration_detail(spoken, raw, order)
            die(f"voice duration outside 42-62s; refusing artificial speed change: {total:.1f}s — {detail}")
        audio = {beat: RUN / f"voice_clean_{beat}.wav" for beat in order}
        return spoken, narration, audio, raw, beat_times, total
    lo, hi = _output_bounds(topic, engine=engine)
    label = f"{lo:.0f}-{hi:.0f}"
    if _approved_narration(narration, topic) and not (lo <= total <= hi):
        die(
            f"approved script voice duration outside {label}s; refusing a trim: {total:.1f}s"
        )
    if lo <= total <= hi:
        topic["_spoken_beats"] = {beat: spoken[beat] for beat in order}
        audio = {beat: RUN / f"voice_clean_{beat}.wav" for beat in order}
        return spoken, narration, audio, raw, beat_times, total
    if total > hi:
        words = sum(len(str(spoken.get(beat) or "").split()) for beat in order)
        rate = words / max(total, 0.1)
        # Measured words/s of this audio. Do not lift the budget to the text-gate
        # floor. Four-beat aims at 60s; six-beat aims at 90s. The re-measured
        # duration is the final gate.
        cap_s = FOUR_LIVE_MARGIN_S if four_live(topic) else 90.0
        budget = int(math.floor(cap_s * rate))
        print(
            f"[duration] {total:.1f}s outside {label}; trimming sentences toward {budget} words"
        )
        apply_live_draft(narration, topic, total_hi=budget)
        spoken = {beat: narration["beats"][beat] for beat in order}
        changed = [beat for beat in order if spoken[beat] != before[beat]]
        if not changed:
            die(
                f"voice duration outside {label}s and no sentence could be trimmed; "
                "refusing a longer rewrite: "
                f"{total:.1f}s — {_duration_detail(before, raw, order)}"
            )
    else:
        why = (
            f"The spoken voice is {total:.1f}s. It must land in {label} seconds. "
            "The script is too short. Add words only on the short beats."
        )
        print(f"[duration] {total:.1f}s under {lo:.0f}; one repair of the short beats")
        if _one_beat_repair(narration, topic, c, spec, why) is None:
            die(
                f"voice duration outside {label}s; refusing artificial speed change: "
                f"{total:.1f}s — {_duration_detail(spoken, raw, order)}"
            )
        apply_live_draft(narration, topic)
        spoken = {beat: narration["beats"][beat] for beat in order}
        fails = _dry_fails(narration, topic, spec)
        if fails:
            die(
                "duration repair missed the text gate; refusing another render and the template: "
                + "; ".join(fails)
            )
        changed = [beat for beat in order if spoken[beat] != before[beat]]
        if not changed:
            die(
                f"voice duration outside {label}s and the repair changed no beats; "
                f"refusing artificial speed change: {total:.1f}s"
            )
    raw.update(_rerender_beats(spoken, c, changed))
    beat_times, total = _timeline(raw, order)
    topic["_spoken_beats"] = {beat: spoken[beat] for beat in order}
    if total < lo or total > hi:
        die(
            f"voice duration outside {label}s after one repair; refusing artificial speed change: "
            f"{total:.1f}s — {_duration_detail(spoken, raw, order)}"
        )
    (RUN / "narration.json").write_text(
        json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8")
    audio = {beat: RUN / f"voice_clean_{beat}.wav" for beat in order}
    return spoken, narration, audio, raw, beat_times, total


def next_topic_after_empty(topic, reason):
    """Record the skip and return the next Short. Chart Board keeps going."""
    import pick_topic
    key = str((topic or {}).get("key") or "").strip()
    print(f"[skip] {key}: {reason}; next topic")
    pick_topic.remember_gate_skip(key)
    nxt = pick_topic.fresh_short()
    if not nxt or nxt.get("key") == key:
        die(f"no next topic after empty DeepSeek: {reason}")
    save("topic.txt", nxt["topic"])
    save("topic.json", json.dumps(nxt, ensure_ascii=False, indent=1))
    ok(f"next topic after empty DeepSeek: {nxt['key']}")
    return nxt


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
    locked = bool(args.key or os.environ.get("TOPIC_KEY", "").strip())
    while True:
        ok(f"template: {topic['key']} ({topic['cat']})")
        # Script prices and the गुना line use the same closes the chart divides.
        market_data.sync_topic_numbers(topic)
        spec = market_data.spec_for_topic(topic)
        if spec.get("atr"):
            market_data.apply_atr_card(topic, spec["atr"])
        topic["_chart_spec"] = spec
        # Sample renders (`--key`) may teach from the checked-in July print.
        # Scheduled and dispatch publishes never set this, and publish.py ignores it.
        topic["_allow_stale_sample"] = bool(args.key)
        if spec.get("mode") == "non_numeric":
            print(f"[market_data] non-numeric card: {spec.get('reason')}")
        elif spec.get("mode") == "series":
            print(f"[market_data] {spec.get('source')} as of {spec.get('as_of')}")

        if _live_lesson(c) and not spec.get("atr"):
            topic["_live_short"] = True
            topic["_voice"] = dict((c or {}).get("voice") or {})
            if not topic.get("_live_beats"):
                topic["_live_beats"] = resolve_live_beats(topic)
            lesson = _lesson(topic)[0]
        else:
            lesson = BEATS
        _reject_topic_chart_gate(topic)
        beat_pngs = {}
        for b in lesson:
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
        try:
            n, source = narration(topic, c, spec)
            break
        except NarrationSkip as skipped:
            if locked:
                die(str(skipped))
            topic = next_topic_after_empty(topic, skipped.reason)
    require_llm_narration(n, source)
    produce_short(topic, n, c, spec, source, beat_pngs=beat_pngs)


def _render_short(topic, n, c, spec, source="llm", beat_pngs=None):
    """Picture, free or paid audio, and final.mp4. Callers decide which voice."""
    import trade_plan
    if trade_plan.is_plan_topic(topic):
        trade_plan.require(topic, n)
    if topic.get("_live_short") and not topic.get("_atr"):
        n["live_beats"] = resolve_live_beats(topic)
    # CTA/disclaimer stay on-screen; do not spend TTS credits speaking them.
    spoken = dict(n["beats"])
    # Trim only the outer TTS silence. Sentence punctuation stays, so pauses
    # inside a beat are kept. A live miss re-speaks only the changed beats.
    # Speech speed is never changed.
    spoken, n, beat_audio, raw_durations, beat_times, t = prepare_spoken(
        spoken, n, topic, c, spec)
    topic["_spoken_beats"] = {beat: spoken[beat] for beat in timed_beats(beat_times)}
    raw_total = sum(raw_durations.values())
    order = timed_beats(beat_times)
    if t < 45.0 and not topic.get("_live_short"):
        print(f"[duration] natural speech is {t:.1f}s; no slow-motion stretching")
    ok(f"voice: trimmed speech {raw_total:.1f}s -> final {t:.1f}s ({source}) engine: {', '.join(sorted(set(ENGINES)))}")
    trade_plan.note_duration(t, topic)
    switch = {b: cue_frac(topic["key"], spoken[b]) for b in order if len((beat_pngs or {}).get(b) or ()) == 3}
    if switch:
        ok("3rd picture at " + ", ".join(f"{b} {f:.0%}" for b, f in switch.items()))
    cta = engagement.engagement_question(topic)
    hook_line = n.get("hook_line") or ""
    scenes = scene_pace.plan_scenes(beat_times, beats=order)
    audio_words = None
    try:
        audio_words = _word_timings(beat_audio, beat_times)
    except Exception as exc:  # noqa: BLE001 — cards fall back to even word timing
        print(f"[cards] word timings unavailable ({exc}); using the beat clock")
    _install_scene_cards(topic, spoken, beat_times, scenes, audio_words)
    groups = {b: [] for b in order}
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
            scene["frame"] = frame
            if getattr(frame, "text_layers", None) is not None and getattr(frame, "plate", None) is not None:
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
    sync_report = _sync_report(
        topic, n, scenes, beat_audio, beat_times, spec, words=audio_words)
    if not audio_words:
        audio_words = sync_report.get("words") or None
    samples, text_frames, layout_notes = _craft_samples(
        scenes, topic, beat_times, spoken, hook_line, cta, audio_words=audio_words)
    review = self_review.full_review(
        topic, n, spec, overlays, scenes=scenes,
        labels=(label_boxes if labels_ready else None),
        samples=samples, text_frames=text_frames,
        sync=sync_report, layout_notes=layout_notes,
    )
    as_of = (spec.get("atr") or {}).get("as_of") or spec.get("as_of")
    on_screen = not any("as-of date is not on screen" in note for note in layout_notes)
    if as_of:
        (RUN / "market_freshness.json").write_text(json.dumps({
            "as_of": as_of,
            "on_screen": bool(on_screen and as_of),
            "allow_stale_sample": bool(topic.get("_allow_stale_sample")),
        }, ensure_ascii=False), encoding="utf-8")
    review = apply_upload_block(review, source)
    review["narration_source"] = source
    review["sync"] = {
        "ok": bool(sync_report.get("ok")),
        "reason": sync_report.get("reason") or "",
        "scenes": sync_report.get("scenes") or [],
        "beats": sync_report.get("beats") or [],
    }
    review = soften_approved_holds(review, sync_report)
    review["attempts"] = n.get("review_attempts") or self_review.MAX_ATTEMPTS
    review["scenes"] = [
        {"beat": s["beat"], "kind": s.get("kind") or "",
         "start": round(s["start"], 2), "end": round(s["end"], 2),
         "callout": bool(s.get("callout"))}
        for s in scenes
    ]
    outside = scene_frames.frames_safe_area_notes(
        [scene.get("frame") for scene in scenes if scene.get("frame") is not None]
    )
    if outside:
        _hold_hard(review)
        review.setdefault("notes", []).extend(outside)
        review["summary"] = (str(review.get("summary") or "") + " — " + "; ".join(outside[:2])).strip(" —")
    (RUN / "self_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    if outside:
        die("text outside the frame safe area; refusing the render: " + "; ".join(outside[:4]))
    if review.get("public_ok"):
        ok("self-review passed: " + review["summary"])
    elif review.get("review_flag"):
        print("[REVIEW] soft self-review — mp4 kept, public upload needs approved=true. " + review["summary"])
    else:
        print("[HOLD] self-review below the bar — public upload blocked. " + review["summary"])
    assemble(paced, beat_audio, beat_times,
             build_ass(topic, beat_times, spoken, hook_line, cta, audio_words=audio_words),
             switch=switch, hook_line=hook_line, scenes=scenes)

    # Final-file hard gate: never retime the finished video.
    final_path = RUN / "final.mp4"
    final_dur = duration(final_path)
    if topic.get("_live_short"):
        lo, hi = _output_bounds(topic)
        label = f"{lo:.0f}-{hi:.0f}"
        if final_dur < lo or final_dur > hi:
            _hold_hard(review)
            (RUN / "self_review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            die(f"final.mp4 duration outside {label}s; refusing post-render speed change: {final_dur:.1f}s")
    elif final_dur < 45.0 or final_dur > 62.0:
        _hold_hard(review)
        (RUN / "self_review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        die(f"final.mp4 duration outside 45-62s; refusing post-render speed change: {final_dur:.1f}s")

    write_compat(topic, n, spoken, beat_times)
    (RUN / "narration.json").write_text(
        json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
    topic["_spoken_beats"] = {**(topic.get("_spoken_beats") or {}), **spoken}
    thumbnail(topic, RUN / "thumbnail.jpg")
    ok(f"wrote run/final.mp4 ({final_dur:.1f}s) + thumbnail.jpg")
    return {
        "final_dur": final_dur,
        "spoken": spoken,
        "narration": n,
        "beat_audio": beat_audio,
        "beat_times": beat_times,
        "order": order,
        "scenes": scenes,
        "paced": paced,
        "switch": switch,
        "hook_line": hook_line,
        "cta": cta,
    }


def _eleven_spent():
    try:
        return int((credit_log._read() or {}).get("elevenlabs_characters_sent") or 0)
    except (TypeError, ValueError):
        return 0


def apply_gemini_floor(lo, hi, engine):
    """Four-beat Gemini audio may land at 34s. Edge and Rudra stay at 45s."""
    if str(engine or "").strip().lower() != "gemini":
        return float(lo), float(hi)
    if float(lo) == 45.0 and float(hi) in (65.0, 85.0):
        return gemini_min_seconds(), float(hi)
    return float(lo), float(hi)


def gemini_min_seconds():
    import gemini_voice
    return float(gemini_voice.GEMINI_MIN_SECONDS)


def _output_bounds(topic, engine=None):
    """Seconds final.mp4 must land in. A live short uses its voice window."""
    if str((topic or {}).get("key") or "") == "aaj_ka_nifty":
        lo, hi = 45.0, 59.0
    elif (topic or {}).get("_live_short"):
        lo, hi = live_voice_bounds(topic)
    else:
        lo, hi = 45.0, 62.0
    if engine is None:
        engine = (eleven_voice.load_voice_record(RUN).get("engine") or "")
    return apply_gemini_floor(lo, hi, engine)


def _needs_paid_proof(topic, c):
    """ElevenLabs, live or ATR, must prove the picture before any paid call."""
    if os.environ.get("PROOF_FREE_AUDIO", "").strip():
        return False
    topic = topic or {}
    if not topic.get("_live_short") and not topic.get("_atr"):
        return False
    engine = str((((c or {}).get("voice") or {}).get("engine") or "elevenlabs")).strip().lower()
    return engine == "elevenlabs" and eleven_voice.paid_voice_allowed()


def _spent_hold(message):
    """Stop the slot after characters were already sent. Upload must not run."""
    die(f"{message}; {_eleven_spent()} characters spent; no upload")


def _repaint_scene_frames(topic, spec, scenes, order):
    """Chart Board again from the cards just timed to this voice."""
    groups = {beat: [] for beat in order}
    paced = {}
    for scene in scenes:
        groups.setdefault(scene.get("beat"), []).append(scene)
    for beat, group in groups.items():
        if not group:
            continue
        frames, flags = scene_frames.frames_for(topic, beat, len(group), spec or {})
        paths = []
        for index, (scene, frame, flag) in enumerate(zip(group, frames, flags)):
            scene["frame"] = frame
            path = RUN / f"sc_{beat}_{index}.png"
            frame.save(path)
            scene["png"] = str(path)
            scene["kind"] = getattr(frame, "scene_kind", "") or scene.get("kind") or ""
            scene["callout"] = bool(scene.get("callout") or flag)
            paths.append(path)
        paced[beat] = tuple(paths)
    return paced


def _reuse_proven_frames(old_scenes, beat_times, order):
    """New beat lengths. The Edge proof must already have drawn Chart Board."""
    fresh = scene_pace.plan_scenes(beat_times, beats=order)
    pools = {}
    for scene in old_scenes or []:
        pools.setdefault(scene.get("beat"), []).append(scene)
    paced = {}
    for scene in fresh:
        pool = pools.get(scene["beat"]) or []
        if not pool:
            die(
                f"proven frames missing for {scene['beat']}; "
                "refusing to redraw Chart Board after paid TTS"
            )
        src = pool[min(int(scene.get("index") or 0), len(pool) - 1)]
        scene["frame"] = src.get("frame")
        scene["png"] = src.get("png")
        scene["kind"] = src.get("kind") or ""
        scene["callout"] = bool(src.get("callout"))
        if scene.get("png"):
            paced.setdefault(scene["beat"], []).append(scene["png"])
    paced = {beat: tuple(paths) for beat, paths in paced.items()}
    return fresh, paced


_HARD_NOTE_MARKS = (
    "numbers with no source",
    "leaked",
    "instruction",
    "transcription failed",
    "narration does not match",
    "forbidden",
    "placeholder left",
    "spoken number is not",
    "duration outside",
    "safe area",
    "outside the frame",
)


def _hard_pre_render_block(review):
    """Numbers, leaks, sync, duration, and a crash stay HOLD. Craft does not.

    Soft notes (vague matlab/karein, a still hook, trend, visual) are a review
    flag. They must not stop Gemini on a plan topic.
    """
    if not isinstance(review, dict):
        return True
    if review.get("narration_fallback"):
        return True
    parts = review.get("parts") or {}
    for name in ("accuracy", "sync"):
        raw = parts.get(name)
        if raw is None:
            continue
        try:
            score = float(raw)
        except (TypeError, ValueError):
            return True
        if score < self_review.PART_BAR:
            return True
    blob = " ".join(str(item) for item in (review.get("notes") or []))
    blob = (blob + " " + str(review.get("summary") or "")).lower()
    return any(mark in blob for mark in _HARD_NOTE_MARKS)


def _continue_plan_review(review):
    review["review_flag"] = True
    review["verdict"] = "REVIEW"
    review["public_ok"] = False
    review["passed"] = False
    return review


def _pay_and_remux(topic, n, c, spec, source, proven):
    """The upload voice, then mux it and review the file that would upload.

    Gemini is not an ElevenLabs spend. The paid switch is checked only when
    that one request fails and Rudra would be next.
    """
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        eleven_voice.require_paid_tts()
    spoken = proven["spoken"]
    n = proven.get("narration") or n
    order = tuple(proven["order"])
    hook_line = proven.get("hook_line") or n.get("hook_line") or ""
    cta = proven.get("cta") or ""
    (RUN / "tts_text.txt").unlink(missing_ok=True)
    global _VOICE_TOPIC
    _VOICE_TOPIC = topic
    try:
        synthesize_short_voices(spoken, c, beats=order)
    finally:
        _VOICE_TOPIC = None
    raw = _trim_rendered(order)
    beat_times, total = _timeline(raw, order)
    lo, hi = _output_bounds(topic)
    import trade_plan
    trade_plan.note_duration(total, topic)
    if total < lo or total > hi:
        _spent_hold(
            f"paid voice duration outside {lo:.0f}-{hi:.0f}s; refusing the slot: {total:.1f}s"
        )
    scenes, paced = _reuse_proven_frames(proven.get("scenes") or [], beat_times, order)
    beat_audio = {beat: RUN / f"voice_clean_{beat}.wav" for beat in order}
    audio_words = None
    try:
        audio_words = _word_timings(beat_audio, beat_times)
    except Exception as exc:  # noqa: BLE001 — the review still runs on the beat clock
        print(f"[cards] paid word timings unavailable ({exc}); using the beat clock")
    _install_scene_cards(topic, spoken, beat_times, scenes, audio_words)
    paced = _repaint_scene_frames(topic, spec, scenes, order)
    assemble(
        paced, beat_audio, beat_times,
        build_ass(topic, beat_times, spoken, hook_line, cta, audio_words=audio_words),
        switch=proven.get("switch") or {},
        hook_line=hook_line,
        scenes=scenes,
    )
    final_dur = duration(RUN / "final.mp4")
    sync_report = _sync_report(
        topic, n, scenes, beat_audio, beat_times, spec, words=audio_words)
    if not audio_words:
        audio_words = sync_report.get("words") or None
    samples, text_frames, layout_notes = _craft_samples(
        scenes, topic, beat_times, spoken, hook_line, cta, audio_words=audio_words)
    overlays = short_layout.overlay_plan(topic, beat_times, hook_line, cta)
    labels = []
    labels_ready = True
    for scene in scenes:
        boxes = getattr(scene.get("frame"), "label_boxes", None)
        if boxes is None:
            labels_ready = False
        elif labels_ready:
            labels.extend(boxes)
    review = self_review.full_review(
        topic, n, spec, overlays, scenes=scenes,
        labels=(labels if labels_ready else None),
        samples=samples, text_frames=text_frames,
        sync=sync_report, layout_notes=layout_notes,
    )
    review = apply_upload_block(review, source)
    review = soften_approved_holds(review, sync_report)
    if final_dur < lo or final_dur > hi:
        note = f"final.mp4 duration outside {lo:.0f}-{hi:.0f}s: {final_dur:.1f}s"
        _hold_hard(review)
        review.setdefault("notes", []).append(note)
        review["summary"] = (str(review.get("summary") or "") + " — " + note).strip(" —")
    outside = scene_frames.frames_safe_area_notes(
        [scene.get("frame") for scene in scenes if scene.get("frame") is not None]
    )
    if outside:
        _hold_hard(review)
        review.setdefault("notes", []).extend(outside)
        review["summary"] = (str(review.get("summary") or "") + " — " + "; ".join(outside[:2])).strip(" —")
    review["narration_source"] = source
    review["sync"] = {
        "ok": bool(sync_report.get("ok")),
        "reason": sync_report.get("reason") or "",
        "scenes": sync_report.get("scenes") or [],
        "beats": sync_report.get("beats") or [],
    }
    review["scenes"] = [
        {"beat": s["beat"], "kind": s.get("kind") or "",
         "start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2)}
        for s in scenes
    ]
    review_path = RUN / "self_review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    if not review.get("public_ok"):
        import trade_plan
        if review.get("review_flag") and approved_publish():
            print("[REVIEW] approved=true; " + str(review.get("summary") or ""), flush=True)
        elif trade_plan.is_plan_topic(topic) and not _hard_pre_render_block(review):
            _continue_plan_review(review)
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[REVIEW] " + str(review.get("summary") or ""), flush=True)
        else:
            kind = "REVIEW" if review.get("review_flag") else "HOLD"
            _spent_hold(
                f"paid render {kind}; " + str(review.get("summary") or "self-review failed")
            )
    topic["_spoken_beats"] = {beat: spoken[beat] for beat in order}
    write_compat(topic, n, spoken, beat_times)
    (RUN / "narration.json").write_text(
        json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
    ok(f"paid voice muxed onto the proven picture ({final_dur:.1f}s)")
    return final_dur


def prove_full_render(topic, n, c, spec, source="llm", beat_pngs=None, free="edge"):
    """Chart Board through final.mp4 on free audio. Never calls ElevenLabs.

    The live proof uses Edge and the real whisper sync gate, the duration
    gate, and the full self-review. `free=\"silence\"` only builds a timed
    picture; it is not a passing live proof. A crash, an Edge failure, or a
    self-review hold exits with 0 characters spent.
    The returned picture is what a later paid pass muxes onto.
    """
    kind = "edge" if str(free or "").strip().lower() == "edge" else "silence"
    before = _eleven_spent()
    os.environ["PROOF_FREE_AUDIO"] = kind
    try:
        try:
            proven = _render_short(topic, n, c, spec, source, beat_pngs)
        except SystemExit as exc:
            if _eleven_spent() != before:
                die("ElevenLabs characters were spent during a failed pre-render")
            if "0 characters" not in str(exc):
                die(f"pre-render failed before paid TTS; 0 characters spent: {exc}")
            raise
        except Exception as exc:
            if _eleven_spent() != before:
                die("ElevenLabs characters were spent during a failed pre-render")
            die(f"pre-render failed before paid TTS; 0 characters spent: {exc}")
        if _eleven_spent() != before:
            die("ElevenLabs characters were spent during the pre-render")
        review_path = RUN / "self_review.json"
        review = {}
        if review_path.exists():
            try:
                review = json.loads(review_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                review = {}
        if review.get("public_ok"):
            return proven
        summary = str(review.get("summary") or "no passing self-review")
        import trade_plan
        if trade_plan.is_plan_topic(topic) and not _hard_pre_render_block(review):
            _continue_plan_review(review)
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[REVIEW] " + summary, flush=True)
            return proven
        if review.get("review_flag"):
            if os.environ.get("DRY_RUN", "").strip() == "1":
                print("[REVIEW] " + summary, flush=True)
                return proven
            if approved_publish():
                print("[REVIEW] approved=true; " + summary, flush=True)
                return proven
            die(
                "REVIEW; refusing paid publish without approved=true; 0 characters spent: "
                + summary
            )
        die(
            "pre-render HOLD before paid TTS; 0 characters spent: " + summary
        )
    finally:
        os.environ.pop("PROOF_FREE_AUDIO", None)


def produce_short(topic, n, c, spec, source="llm", beat_pngs=None):
    """Voice, scenes, sync, and final.mp4. No upload and no git commit.

    A live or ATR ElevenLabs slot renders the whole Short on Edge first,
    including whisper sync, duration, and craft. Paid TTS runs only after
    that proof passes, then the paid file is reviewed again before upload.
    """
    if topic.get("_live_short") and not topic.get("_atr"):
        n["live_beats"] = resolve_live_beats(topic)
    if _needs_paid_proof(topic, c):
        proven = prove_full_render(
            topic, n, c, spec, source=source, beat_pngs=beat_pngs, free="edge",
        )
        return _pay_and_remux(topic, n, c, spec, source, proven)
    return _render_short(topic, n, c, spec, source, beat_pngs)["final_dur"]


if __name__ == "__main__":
    sys.exit(main())
