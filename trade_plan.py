"""Full trade plan for every approved topic video.

The OHLC is a generated example: prior trend, the pattern, then what happened
next. Entry, stop, target, and reward-to-risk are computed from that OHLC.
Scripts keep placeholders. This module fills them. A failed gate stops the
render before any voice request.
"""
import re

BEATS = (
    "hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta",
)
REQUIRED = ("context", "entry", "sl", "target", "invalidation")
DISCLAIMER = "Sirf seekhne ke liye, financial advice nahi."
# The topic the owner asked for is 40–50s. A take up to 55s is a review, not a block.
VOICE_SECONDS = (40.0, 50.0)
VOICE_REVIEW_MAX = 55.0
# 40s at 1.9 words/s is 76 words. 50s is 95. 55s is 104. Scripts aim inside 40–50s.
TOTAL_WORDS = (84, 100)
WORD_TARGET = (88, 96)
WORD_TARGETS = {beat: 14 for beat in BEATS}
BANNED = (
    "नतीजा", "हिसाब", "बत्ती", "हद", "पूँजी", "क्षमता", "तय करो", "पलटवार", "चीरती",
)
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class Plan:
    def __init__(self, key, side, ohlc, entry, sl, target, sr, sr_name, focus, extras=None, rsi=None):
        self.key = key
        self.side = side
        self.ohlc = tuple(tuple(float(v) for v in candle) for candle in ohlc)
        self.entry = float(entry)
        self.sl = float(sl)
        self.target = float(target)
        self.sr = float(sr)
        self.sr_name = sr_name
        self.focus = int(focus)
        self.extras = dict(extras or {})
        named_rule = str(self.extras.pop("rule", "") or "")
        self.rsi = tuple(rsi or ())
        self.risk = abs(self.entry - self.sl)
        self.reward = abs(self.target - self.entry)
        if self.risk <= 0:
            raise ValueError(f"{key} has no risk")
        self.rr = self.reward / self.risk
        self.rr_text = _rr_text(self.rr)
        self.rr_spoken = _rr_spoken(self.rr_text)
        self.rule = named_rule or self.rr_text
        if self.rr < 1.5 - 1e-9:
            raise ValueError(f"{key} reward/risk {self.rr:.3f} is under 1.5")
        if side == "long" and not (self.sl < self.entry < self.target):
            raise ValueError(f"{key} long stop is not below entry")
        if side == "short" and not (self.target < self.entry < self.sl):
            raise ValueError(f"{key} short stop is not above entry")

    def tokens(self):
        values = {
            "entry": fmt(self.entry),
            "sl": fmt(self.sl),
            "target": fmt(self.target),
            "sr": fmt(self.sr),
            "risk": fmt(self.risk),
            "reward": fmt(self.reward),
            "rr": self.rr_spoken,
            "rule": self.rule,
        }
        for name, value in self.extras.items():
            values[name] = fmt(value) if isinstance(value, (int, float)) else str(value)
        return values


def fmt(value):
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _rr_text(ratio):
    for places in (0, 1, 2):
        shown = round(float(ratio), places)
        if abs(shown - float(ratio)) < 1e-6:
            if places == 0:
                return f"1:{int(shown)}"
            return f"1:{shown:.{places}f}"
    return f"1:{float(ratio):.2f}"


def _rr_spoken(label):
    left, right = label.split(":")
    return f"{left} बटा {right}"


def _target_long(entry, sl, other=None):
    risk = entry - sl
    if other is not None and other > entry and (other - entry) / risk >= 1.5 - 1e-9:
        return other
    return entry + 2 * risk


def _target_short(entry, sl, other=None):
    risk = sl - entry
    if other is not None and other < entry and (entry - other) / risk >= 1.5 - 1e-9:
        return other
    return entry - 2 * risk


def _hammer():
    ohlc = (
        (132, 134, 122, 124),
        (124, 126, 114, 116),
        (116, 118, 108, 110),
        (114, 118, 100, 116),
        (116, 124, 114, 122),
        (122, 156, 120, 154),
    )
    o, h, l, c = ohlc[3]
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    if body <= 0 or lower < body * 2 or upper > body:
        raise ValueError("hammer shape")
    entry, sl = h, l
    return Plan(
        "pat_hammer", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 3,
        {"mult": 2, "body": body, "wick": lower, "high": h, "low": l},
    )


def _doji():
    ohlc = (
        (128, 130, 118, 120),
        (120, 122, 112, 114),
        (110, 120, 100, 110),
        (110, 128, 108, 126),
        (126, 162, 124, 160),
    )
    o, h, l, c = ohlc[2]
    if o != c:
        raise ValueError("doji open and close")
    entry, sl = h, l
    return Plan(
        "pat_doji", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 2,
        {"open": o, "high": h, "low": l},
    )


def _engulf():
    ohlc = (
        (130, 132, 120, 122),
        (122, 124, 114, 116),
        (118, 120, 112, 108),
        (106, 124, 104, 122),
        (122, 132, 120, 130),
        (130, 166, 128, 164),
    )
    red, green = ohlc[2], ohlc[3]
    if not (red[3] < red[0] and green[3] > green[0]):
        raise ValueError("engulf colours")
    if max(green[0], green[3]) <= max(red[0], red[3]) or min(green[0], green[3]) >= min(red[0], red[3]):
        raise ValueError("engulf body")
    entry, sl = green[1], green[2]
    return Plan(
        "pat_bull_engulf", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 3,
        {"red_open": red[0], "red_close": red[3], "green_open": green[0], "green_close": green[3]},
    )


def _morning():
    ohlc = (
        (150, 152, 130, 132),
        (130, 132, 120, 124),
        (124, 146, 122, 144),
        (144, 152, 142, 150),
        (150, 200, 148, 198),
    )
    first, star, third = ohlc[0], ohlc[1], ohlc[2]
    mid = (first[0] + first[3]) / 2
    if not (first[3] < first[0] and third[3] > mid and star[1] - star[2] < first[0] - first[3]):
        raise ValueError("morning star")
    entry, sl = third[1], min(star[2], third[2])
    return Plan(
        "pat_morning_star", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 2,
        {"mid": mid, "star_low": star[2]},
    )


def _crows():
    ohlc = (
        (150, 152, 140, 142),
        (142, 144, 130, 132),
        (132, 134, 118, 120),
        (120, 122, 106, 108),
        (108, 110, 96, 98),
        (98, 100, 28, 30),
    )
    crows = ohlc[1:4]
    closes = [candle[3] for candle in crows]
    if closes != sorted(closes, reverse=True) or len(set(closes)) != 3:
        raise ValueError("black crows")
    entry = min(candle[2] for candle in crows)
    sl = max(candle[1] for candle in crows)
    return Plan(
        "pat_black_crows", "short", ohlc, entry, sl, _target_short(entry, sl), sl, "RESIST", 3,
        {"close_a": closes[0], "close_b": closes[1], "close_c": closes[2]},
    )


def _double():
    ohlc = (
        (100, 120, 98, 116),
        (116, 118, 108, 110),
        (110, 120, 108, 116),
        (116, 118, 100, 104),
        (104, 106, 84, 86),
    )
    if ohlc[0][1] != ohlc[2][1]:
        raise ValueError("double top")
    entry = ohlc[1][2]
    sl = ohlc[0][1]
    return Plan(
        "pat_double", "short", ohlc, entry, sl, _target_short(entry, sl), sl, "RESIST", 2,
        {"top": sl, "neck": entry},
    )


def _shoulders():
    ohlc = (
        (100, 110, 98, 108),
        (108, 109, 100, 102),
        (102, 130, 100, 104),
        (104, 106, 100, 103),
        (103, 112, 100, 106),
        (106, 108, 94, 96),
        (96, 98, 68, 70),
    )
    if ohlc[1][2] != ohlc[3][2] or ohlc[2][1] <= max(ohlc[0][1], ohlc[4][1]):
        raise ValueError("head and shoulders")
    neck = ohlc[1][2]
    entry = neck
    sl = ohlc[4][1]
    head = ohlc[2][1]
    height = head - neck
    return Plan(
        "pat_head_shoulders", "short", ohlc, entry, sl, entry - height, head, "RESIST", 2,
        {"head": head, "neck": neck, "shoulder": sl, "rule": "पैटर्न हाइट"},
    )


def _cup():
    ohlc = (
        (100, 102, 98, 100),
        (100, 102, 88, 90),
        (90, 92, 80, 84),
        (84, 96, 82, 94),
        (94, 102, 92, 100),
        (100, 101, 94, 96),
        (96, 112, 95, 108),
        (108, 126, 106, 124),
    )
    rim = max(ohlc[0][1], ohlc[4][1])
    cup_low = min(candle[2] for candle in ohlc[:5])
    handle = ohlc[5][2]
    entry, sl = rim, handle
    height = rim - cup_low
    return Plan(
        "pat_cup_handle", "long", ohlc, entry, sl, entry + height, sl, "SUPPORT", 5,
        {"rim": rim, "cup": cup_low, "handle": handle, "rule": "कप की हाइट"},
    )


def _triangle():
    ohlc = (
        (108, 120, 100, 112),
        (112, 116, 104, 108),
        (108, 114, 106, 110),
        (110, 113, 108, 111),
        (111, 124, 110, 122),
        (122, 136, 120, 134),
    )
    highs = [candle[1] for candle in ohlc[:4]]
    lows = [candle[2] for candle in ohlc[:4]]
    if highs != sorted(highs, reverse=True) or lows != sorted(lows):
        raise ValueError("triangle")
    entry, sl = ohlc[3][1], ohlc[3][2]
    height = ohlc[0][1] - ohlc[0][2]
    return Plan(
        "pat_triangle", "long", ohlc, entry, sl, entry + height, sl, "SUPPORT", 3,
        {"high": ohlc[0][1], "low": ohlc[0][2], "rule": "ट्रायंगल हाइट"},
    )


def _flag():
    ohlc = (
        (100, 108, 98, 106),
        (106, 118, 104, 116),
        (116, 130, 114, 128),
        (128, 130, 122, 124),
        (124, 126, 122, 123),
        (123, 136, 122, 134),
        (134, 162, 132, 160),
    )
    entry = max(ohlc[3][1], ohlc[4][1])
    sl = min(ohlc[3][2], ohlc[4][2])
    pole = entry - ohlc[0][0]
    return Plan(
        "pat_flag", "long", ohlc, entry, sl, entry + pole, sl, "SUPPORT", 4,
        {"pole": pole, "flag": sl, "rule": "पोल की हाइट"},
    )


def _breakout():
    ohlc = (
        (104, 110, 100, 106),
        (106, 110, 102, 104),
        (104, 110, 100, 108),
        (108, 116, 109, 115),
        (115, 117, 110, 113),
        (113, 132, 112, 130),
    )
    entry = max(candle[1] for candle in ohlc[:3])
    sl = min(candle[2] for candle in ohlc[:3])
    return Plan(
        "pat_breakout", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 3,
        {"range_high": entry, "range_low": sl},
    )


def _rsi():
    ohlc = (
        (112, 116, 106, 108),
        (108, 110, 98, 100),
        (100, 118, 99, 116),
        (116, 136, 114, 134),
    )
    series = (48, 28, 36, 52)
    entry, sl = ohlc[1][1], ohlc[1][2]
    return Plan(
        "pat_rsi", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 1,
        {"os": 30, "ob": 70, "rsi_low": min(series)}, rsi=series,
    )


def _support():
    ohlc = (
        (118, 130, 114, 120),
        (120, 122, 108, 110),
        (110, 112, 100, 106),
        (106, 118, 101, 116),
        (116, 132, 114, 128),
    )
    support = ohlc[2][2]
    resist = ohlc[0][1]
    entry = ohlc[2][1]
    sl = support
    return Plan(
        "pat_support", "long", ohlc, entry, sl, _target_long(entry, sl, resist), support, "SUPPORT", 2,
        {"resist": resist, "rule": "पिछला रेसिस्टेंस"},
    )


def _gap():
    ohlc = (
        (96, 100, 92, 98),
        (108, 114, 106, 112),
        (112, 120, 110, 118),
        (118, 132, 116, 130),
    )
    prior = ohlc[0][1]
    gap = ohlc[1]
    if gap[0] <= prior:
        raise ValueError("gap")
    entry, sl = gap[1], gap[2]
    return Plan(
        "pat_gap_up", "long", ohlc, entry, sl, _target_long(entry, sl), prior, "SUPPORT", 1,
        {"prior": prior, "gap_open": gap[0]},
    )


def _stop():
    ohlc = (
        (100, 108, 98, 104),
        (104, 116, 106, 114),
        (114, 118, 110, 112),
        (112, 128, 111, 124),
        (124, 142, 122, 136),
    )
    entry, sl = ohlc[1][1], ohlc[1][2]
    return Plan(
        "pat_stop_loss", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 1,
        {"swing": sl},
    )


def _piercing():
    ohlc = (
        (140, 142, 124, 128),
        (128, 130, 114, 116),
        (120, 122, 100, 102),
        (98, 116, 96, 114),
        (114, 122, 112, 120),
        (120, 158, 118, 156),
    )
    red, green = ohlc[2], ohlc[3]
    mid = (red[0] + red[3]) / 2.0
    if green[0] >= red[3] or green[3] <= mid:
        raise ValueError("piercing")
    entry, sl = green[1], green[2]
    return Plan(
        "piercing", "long", ohlc, entry, sl, _target_long(entry, sl), red[2], "SUPPORT", 3,
        {"mid": mid, "red_open": red[0], "red_close": red[3], "green_open": green[0], "green_close": green[3]},
    )


def _daily():
    ohlc = (
        (186, 200, 180, 194),
        (194, 210, 188, 206),
        (206, 242, 204, 240),
    )
    capital, pct = 100000, 2
    entry, sl = ohlc[0][1], ohlc[0][2]
    limit = capital * pct / 100.0
    qty = limit / (entry - sl)
    if abs(qty - round(qty)) > 1e-6:
        raise ValueError("daily quantity")
    return Plan(
        "risk_daily_loss_limit", "long", ohlc, entry, sl, _target_long(entry, sl), sl, "SUPPORT", 0,
        {"capital": capital, "pct": pct, "limit": limit, "qty": qty},
    )


BUILDERS = {
    "pat_hammer": _hammer,
    "pat_doji": _doji,
    "pat_bull_engulf": _engulf,
    "pat_morning_star": _morning,
    "pat_black_crows": _crows,
    "pat_double": _double,
    "pat_head_shoulders": _shoulders,
    "pat_cup_handle": _cup,
    "pat_triangle": _triangle,
    "pat_flag": _flag,
    "pat_breakout": _breakout,
    "pat_rsi": _rsi,
    "pat_support": _support,
    "pat_gap_up": _gap,
    "pat_stop_loss": _stop,
    "piercing": _piercing,
    "risk_daily_loss_limit": _daily,
}
KEYS = tuple(BUILDERS)


def has_plan(key):
    return str(key or "") in BUILDERS


def plan_for(key):
    builder = BUILDERS.get(str(key or ""))
    if builder is None:
        raise KeyError(key)
    return builder()


def hard_voice_seconds():
    """Seconds that stop a render. 50–55s is inside this and only raises a review."""
    return (float(VOICE_SECONDS[0]), float(VOICE_REVIEW_MAX))


def duration_is_review(seconds):
    """True when the voice is over the 50s target and still within 55s."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return False
    _lo, hi = VOICE_SECONDS
    return hi < seconds <= float(VOICE_REVIEW_MAX) + 1e-9


def duration_blocks(seconds):
    """True outside 40–55s. The review band does not block."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return True
    lo, hi = hard_voice_seconds()
    return seconds < lo - 1e-9 or seconds > hi + 1e-9


def is_plan_topic(topic):
    if not isinstance(topic, dict):
        return False
    # The 16:00 data Short keeps its own chart and its own four-beat gate.
    if str(topic.get("key") or "") == "aaj_ka_nifty":
        return False
    if str(topic.get("_live_beats") or "") == "plan":
        return True
    return bool(topic.get("_approved_bank")) and has_plan(topic.get("key"))


def is_plan_script(script):
    beats = (script or {}).get("beats") or {}
    return isinstance(beats, dict) and all(isinstance(beats.get(beat), str) for beat in BEATS)


def fill(text, plan):
    values = plan.tokens()

    def repl(match):
        name = match.group(1)
        # A missing token stays as {name}. The gate reports it. It is not a crash.
        return values.get(name, match.group(0))

    return _PLACEHOLDER.sub(repl, str(text or ""))


def raw_digit_faults(script):
    """Digits typed into the file. Placeholders are the only numbers allowed."""
    beats = (script or {}).get("beats") or {}
    bad = []
    for beat in BEATS:
        body = _PLACEHOLDER.sub(" ", str(beats.get(beat) or ""))
        if re.search(r"\d", body):
            bad.append(beat)
    hook = _PLACEHOLDER.sub(" ", str((script or {}).get("hook_line") or ""))
    if re.search(r"\d", hook):
        bad.append("hook_line")
    return bad


def number_strings(plan):
    import hook_check
    blob = " ".join(plan.tokens().values()) + " " + plan.rr_text
    return hook_check.extract_numbers(blob)


def overlays(plan):
    entry, sl, target = fmt(plan.entry), fmt(plan.sl), fmt(plan.target)
    rows = [
        {"kind": "trend", "beat": "pehchan", "label": "TREND", "price": None},
        {"kind": "sr", "beat": "context", "label": f"{plan.sr_name} {fmt(plan.sr)}", "price": plan.sr},
        {"kind": "entry", "beat": "entry", "label": f"ENTRY {entry}", "price": plan.entry},
        {"kind": "sl", "beat": "sl", "label": f"SL {sl}", "price": plan.sl},
        {"kind": "target", "beat": "target", "label": f"TARGET {target}", "price": plan.target},
        {
            "kind": "rr",
            "beat": "target",
            "label": f"Entry {entry} · SL {sl} · Target {target} = {plan.rr_text}",
            "price": None,
        },
    ]
    if plan.rsi:
        rows.append({"kind": "rsi_os", "beat": "pehchan", "label": f"RSI {fmt(plan.extras['os'])}", "price": None})
        rows.append({"kind": "rsi_ob", "beat": "pehchan", "label": f"RSI {fmt(plan.extras['ob'])}", "price": None})
    return rows


def _trigger_time(words, overlay, plan, beat_times):
    tokens = {
        "trend": ("ट्रेंड", "गिरावट", "तेजी"),
        "sr": ("सपोर्ट", "रेसिस्टेंस", fmt(plan.sr)),
        "entry": ("एंट्री", fmt(plan.entry)),
        "sl": ("स्टॉप", fmt(plan.sl)),
        "target": ("टारगेट", fmt(plan.target)),
        "rr": ("रिस्क", "बटा"),
        "rsi_os": ("आरएसआई", fmt(plan.extras["os"]) if "os" in plan.extras else ""),
        "rsi_ob": ("आरएसआई", fmt(plan.extras["ob"]) if "ob" in plan.extras else ""),
    }.get(overlay["kind"], ())
    best = None
    for row in words or []:
        word = str(row.get("word") or "")
        if any(tok and tok in word for tok in tokens):
            start = float(row.get("start") or 0)
            if best is None or start < best:
                best = start
    if best is not None:
        return best
    home = overlay["beat"]
    span = (beat_times or {}).get(home)
    if span:
        return float(span[0])
    return 0.0


def chart_overlay_events(topic, beat_times, words):
    """ENTRY, SL, TARGET, support, trend, and the RR box, timed to the words."""
    plan = plan_for((topic or {}).get("key"))
    last = 0.0
    for span in (beat_times or {}).values():
        if span and len(span) >= 2:
            last = max(last, float(span[1]))
    events = []
    for row in overlays(plan):
        start = _trigger_time(words, row, plan, beat_times)
        events.append({
            "kind": row["kind"],
            "label": row["label"],
            "beat": row["beat"],
            "start": start,
            "end": max(last, start),
        })
    return events


def _scene_end(topic, beat, index):
    rows = [row for row in (topic.get("_scene_plan") or []) if row.get("beat") == beat]
    if index < len(rows):
        return float(rows[index]["end"])
    span = (topic.get("_beat_times") or {}).get(beat)
    if span:
        return float(span[1])
    return None


def active_overlays(topic, beat, index=0):
    plan = plan_for(topic.get("key"))
    rows = overlays(plan)
    words = topic.get("_audio_words") or []
    if words:
        end = _scene_end(topic, beat, index)
        if end is None:
            end = 10**9
        shown = []
        for row in rows:
            if _trigger_time(words, row, plan, topic.get("_beat_times") or {}) <= float(end) + 0.05:
                shown.append(row)
        return shown
    rank = {name: pos for pos, name in enumerate(BEATS)}
    here = rank.get(beat, 0)
    return [row for row in rows if rank.get(row["beat"], 0) <= here]


def script_fails(narration, topic):
    """Hard failures. A non-primary voice is a review flag, not one of these."""
    fails = []
    try:
        plan = plan_for((topic or {}).get("key") or (narration or {}).get("key"))
    except KeyError:
        return ["trade plan missing"]
    beats = (narration or {}).get("beats") or {}
    for beat in BEATS:
        if not str(beats.get(beat) or "").strip():
            fails.append(f"beat '{beat}' missing")
    text = " ".join(str(beats.get(beat) or "") for beat in BEATS)
    if not text.rstrip().endswith(DISCLAIMER):
        fails.append("disclaimer missing")
    if "{" in text:
        fails.append("placeholder left in the script")
    for word in BANNED:
        if word in text:
            fails.append(f"banned word {word}")
    import board_short
    low = text.lower()
    # टारगेट is the trader word for this plan. Generated scripts still ban it.
    bad = [
        word for word in board_short.FORBIDDEN
        if word != "टारगेट" and word.lower() in low
    ]
    if bad:
        fails.append(f"forbidden {bad}")
    body = text.replace(" ", "")
    dev = sum(1 for ch in body if "\u0900" <= ch <= "\u097f")
    if body and dev < len(body) * 0.45:
        fails.append("narration Hindi (Devanagari) me nahi hai")
    title = str((narration or {}).get("title") or "")
    if not 8 <= len(title) <= 90:
        fails.append("title 8-90 characters")
    hook_line = " ".join(str((narration or {}).get("hook_line") or "").split())
    hook = str(beats.get("hook") or "")
    if not hook_line or not hook.startswith(hook_line.rstrip("।")):
        fails.append("hook beat must open with the hook line")
    words = len(text.split())
    lo, hi = TOTAL_WORDS
    if not lo <= words <= hi:
        fails.append(f"{words} words ({lo}-{hi})")
    import hook_check
    allowed = number_strings(plan)
    fails.extend(hook_check.check_hook_line(hook_line, allowed, max_words=12))
    fails.extend(board_short.title_term_notes({
        "title": title,
        "beats": {"hook": hook},
    }))
    said = hook_check.extract_numbers(text)
    extra = said - allowed
    if extra:
        fails.append(f"spoken number is not the computed value: {sorted(extra)}")
    for name in ("entry", "sl", "target"):
        if fmt(getattr(plan, name)) not in text:
            fails.append(f"spoken script omits {name}")
    if plan.rr_spoken not in text:
        fails.append("spoken script omits the reward to risk")
    if plan.rule not in str(beats.get("target") or ""):
        fails.append("target beat omits the rule")
    rows = overlays(plan)
    kinds = {row["kind"] for row in rows}
    for kind in ("entry", "sl", "target", "rr"):
        if kind not in kinds:
            fails.append(f"overlay missing {kind}")
    labels = " ".join(row["label"] for row in rows)
    for mark in ("ENTRY", "SL", "TARGET"):
        if mark not in labels:
            fails.append(f"overlay label missing {mark}")
    screen = hook_check.extract_numbers(labels)
    screen_extra = screen - allowed
    if screen_extra:
        fails.append(f"on-screen number is not the computed value: {sorted(screen_extra)}")
    if plan.rr < 1.5:
        fails.append(f"reward/risk {plan.rr:.2f} is under 1.5")
    if plan.side == "long" and not plan.sl < plan.entry:
        fails.append("stop is not below entry")
    if plan.side == "short" and not plan.sl > plan.entry:
        fails.append("stop is not above entry")
    return fails


def require(topic, narration):
    fails = script_fails(narration, topic)
    if fails:
        import board_short
        board_short.die("trade plan gate: " + "; ".join(fails))


def voice_review(engine, topic):
    """Primary voice is Gemini. Anything else is a review flag, not a hard fail."""
    if not is_plan_topic(topic):
        return False
    name = str(engine or "").strip().lower()
    if name == "gemini":
        return False
    print(f"[REVIEW] voice_engine={name or 'unknown'}; primary voice is gemini", flush=True)
    return True


def note_duration(seconds, topic):
    """50–55s prints a review and returns True. It does not stop the render."""
    if not is_plan_topic(topic) or not duration_is_review(seconds):
        return False
    print(
        f"[REVIEW] duration {float(seconds):.1f}s is over {VOICE_SECONDS[1]:.0f}s "
        f"and within {VOICE_REVIEW_MAX:.0f}s",
        flush=True,
    )
    return True


def narration_from(script):
    plan = plan_for(script.get("key"))
    raw = script.get("beats") or {}
    beats = {beat: " ".join(fill(raw.get(beat) or "", plan).split()) for beat in BEATS}
    if beats["cta"] and not beats["cta"].endswith(DISCLAIMER):
        beats["cta"] = f"{beats['cta']} {DISCLAIMER}".strip()
    return {
        "title": " ".join(str(script.get("title") or "").split()),
        "hook_line": " ".join(fill(script.get("hook_line") or "", plan).split()),
        "beats": beats,
        "facts": [str(line) for line in (script.get("facts") or [])],
        "source": "approved",
        "plan": True,
        "live_beats": "plan",
        "narration_fallback": False,
        "review_attempts": 1,
        "key": plan.key,
    }


def _grid(img):
    import board_kit as K
    from PIL import ImageDraw
    box = K.BOARD
    grid = ImageDraw.Draw(img)
    for y in range(int(box[1]) + 8, int(box[3]) - 8, 36):
        grid.line([(box[0] + 12, y), (box[2] - 12, y)], fill=(120, 150, 190), width=2)


def _draw_price(img, plan, shown):
    import board_kit as K
    box = K.BOARD
    prices = [candle[1] for candle in plan.ohlc] + [candle[2] for candle in plan.ohlc]
    for row in shown:
        if row["price"] is not None:
            prices.append(float(row["price"]))
    lo, hi = min(prices), max(prices)
    top_pad = 118 if any(row["kind"] == "rr" for row in shown) else 70
    ax = K.candles(
        img, box, plan.ohlc, lo, hi, grid=False, width_frac=0.62,
        pad=(64, top_pad, 64, 72),
    )
    kinds = {row["kind"] for row in shown}
    if "trend" in kinds and plan.focus > 0:
        start, end = plan.ohlc[0], plan.ohlc[plan.focus]
        K.arrow(
            img,
            (ax.x(0), ax.y(start[3])),
            (ax.x(plan.focus), ax.y(end[3])),
            K.YELLOW, w=8, head=26,
        )
    colors = {
        "sr": K.BLUE,
        "entry": K.YELLOW,
        "sl": K.RED,
        "target": K.GREEN,
    }
    sides = {"sr": "left", "entry": "right", "sl": "left", "target": "right"}
    for row in shown:
        if row["kind"] not in colors or row["price"] is None:
            continue
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(float(row["price"])),
            colors[row["kind"]], row["label"], size=22, label_side=sides[row["kind"]],
        )
    if "rr" in kinds:
        _rr_box(img, plan)
    if plan.rsi and "rsi_os" in kinds:
        _rsi_strip(img, plan)
    if plan.focus < len(plan.ohlc):
        candle = plan.ohlc[plan.focus]
        K.ring(img, ax.x(plan.focus), (ax.y(candle[1]) + ax.y(candle[2])) / 2, 28, 40, K.YELLOW)
    return ax


def _rr_box(img, plan):
    import board_kit as K
    from PIL import ImageDraw
    x0, y0 = K.BOARD[0] + 20, K.BOARD[1] + 16
    width, height = 860, 54
    draw = ImageDraw.Draw(img)
    mid = x0 + int(width * 0.38)
    draw.rounded_rectangle([x0, y0, mid, y0 + height], 10, fill=K.RED)
    draw.rectangle([mid - 8, y0, mid + 8, y0 + height], fill=K.RED)
    draw.rounded_rectangle([mid, y0, x0 + width, y0 + height], 10, fill=K.GREEN)
    text = (
        f"Entry {fmt(plan.entry)} · SL {fmt(plan.sl)} · "
        f"Target {fmt(plan.target)} = {plan.rr_text}"
    )
    K.fit_label(img, x0 + 18, y0 + 14, text, width - 36, col=(10, 10, 10), size=26, min_size=16)


def _rsi_strip(img, plan):
    import board_kit as K
    box = K.BOARD
    top = box[1] + (box[3] - box[1]) * 0.78
    ax = K.Axes((box[0], top, box[2], box[3] - 8), len(plan.rsi), 0, 100, pad=(64, 16, 64, 20))
    K.polyline(img, [(ax.x(i), ax.y(value)) for i, value in enumerate(plan.rsi)], K.BLUE, 5)
    K.dashed(img, box[0] + 24, box[2] - 24, ax.y(plan.extras["ob"]), K.RED, str(int(plan.extras["ob"])), size=20)
    K.dashed(img, box[0] + 24, box[2] - 24, ax.y(plan.extras["os"]), K.GREEN, str(int(plan.extras["os"])), size=20)


def paint_frame(topic, beat, index=0, n=1, spoken=""):
    import board_kit as K
    import board_scenes
    import scene_frames
    import safe_area
    plan = plan_for(topic.get("key"))
    shown = active_overlays(topic, beat, index)
    img = K.background()
    K.panel(img)
    _grid(img)
    _draw_price(img, plan, shown)
    img.shown_overlays = [row["kind"] for row in shown]
    img.overlay_labels = [row["label"] for row in shown]
    img.label_boxes = []
    img.text_layers = []
    img.shapes = []
    img.labelled_graphic_boxes = []
    img.chart_bar_boxes = []
    lines = board_scenes._caption_lines(spoken)
    y = safe_area.SUBTITLE_TOP + 18
    for line in lines:
        scene_frames._text(
            img=img, x=540, y=y, text=line, fill=(250, 250, 250), size=36,
            anchor="mt", band=False, role="caption",
        )
        y += 70
    scene_frames._brand(img)
    caps = [
        layer["box"] for layer in (img.text_layers or [])
        if layer.get("role") == "caption" and layer.get("box")
    ]
    if caps:
        img.panel_box = (
            min(box[0] for box in caps), min(box[1] for box in caps),
            max(box[2] for box in caps), max(box[3] for box in caps),
        )
        img.panel_fill = scene_frames.panel_fill_fraction(img)
    else:
        img.panel_fill = 1.0
    img.chart_region = tuple(float(v) for v in K.BOARD)
    img.scene_kind = f"chart-{beat}"
    img.chart_spec = {
        "concept": "topic_scene",
        "rupees": [],
        "percents": [],
        "labels": list(img.overlay_labels),
        "scene": plan.key,
    }
    img.trade_plan = True
    del n
    return img
