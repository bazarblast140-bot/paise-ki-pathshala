"""Trader Hinglish. Digits stay on the caption. Spoken lines avoid banned words."""
from __future__ import annotations

import re

BANNED = ("हद", "पूँजी", "क्षमता", "तय करो", "पलटवार", "चीरती", "नतीजा", "हिसाब")
DISCLAIMER = "सिर्फ सीखने के लिए, फाइनेंशियल एडवाइस नहीं।"
CTA = "पैसे की पाठशाला फॉलो करो।"


def _fmt(value):
    if value is None:
        return ""
    number = float(value)
    if abs(number) >= 100:
        return f"{number:,.2f}"
    return f"{number:.2f}"


def chunks_for(kind, levels, article=None):
    close = _fmt(levels.get("close"))
    chg = float(levels.get("chg_pct") or 0)
    side = "ऊपर" if chg >= 0 else "नीचे"
    pct = f"{abs(chg):.2f}"
    symbol = str(levels.get("symbol") or "NSE:NIFTY")
    setup = str(levels.get("setup") or "close")
    headline = str((article or {}).get("title") or "").strip()
    sentence = str((article or {}).get("key_sentence") or "").strip()
    source = str((article or {}).get("source") or "").strip()
    hook = {
        "news": f"{headline[:80] or 'बाज़ार की बड़ी खबर'}",
        "close": f"{symbol} क्लोज़ {close}",
        "ta": f"{symbol} पर {setup}",
        "recap": f"हफ्ते का क्लोज़ {close}",
    }[kind]
    spoken_hook = {
        "news": "आज की बड़ी मार्केट खबर यह है।",
        "close": "निफ्टी का आज का क्लोज़ आ गया है।",
        "ta": "आज का सेटअप चार्ट पर साफ दिख रहा है।",
        "recap": "इस हफ्ते का निफ्टी क्लोज़ यह रहा।",
    }[kind]
    rows = [
        {
            "sec": "hook",
            "start": 0.0,
            "end": 3.0,
            "d": hook,
            "t": spoken_hook,
            "k": symbol,
            "skip_numbers": kind == "news",
        },
        {
            "sec": "chart",
            "start": 3.0,
            "end": 12.0,
            "d": f"Close {close}  {side} {pct}%",
            "t": "क्लोज़ और बदलाव चार्ट पर लिखे नंबर ही हैं।",
            "k": close,
        },
    ]
    if headline:
        rows.append({
            "sec": "news",
            "start": 12.0,
            "end": 22.0,
            "d": f"{headline[:110]} · {source}",
            "t": "यह खबर असली आर्टिकल से है। हमने कोई लाइन नहीं जोड़ी।",
            "k": source or "खबर",
        })
    sl = levels.get("sl_zone") or [None, None]
    target = levels.get("target_zone") or [None, None]
    rows.append({
        "sec": "view",
        "start": 22.0 if headline else 12.0,
        "end": 40.0,
        "d": (
            f"EMA20 {_fmt(levels.get('ema20'))}  EMA50 {_fmt(levels.get('ema50'))}  "
            f"RSI {_fmt(levels.get('rsi14'))}  "
            f"SL {_fmt(sl[0])}–{_fmt(sl[1])}  target {_fmt(target[0])}–{_fmt(target[1])}"
        ),
        "t": "बुल केस में टारगेट ज़ोन तक बाउंस possible है। बेयर केस में सपोर्ट टूट सकता है। यह एक scenario है।",
        "k": "scenario",
    })
    rows.append({
        "sec": "cta",
        "start": 40.0,
        "end": 45.0,
        "d": f"{CTA} {DISCLAIMER}",
        "t": f"{CTA} {DISCLAIMER}",
        "k": "फॉलो",
    })
    return rows


def spoken_text(rows):
    return " ".join(str(row.get("t") or "").strip() for row in rows if str(row.get("t") or "").strip())


def lint(rows):
    """Hard faults in the narration. Empty means the lines may be spoken."""
    text = spoken_text(rows) + " " + " ".join(str(row.get("d") or "") for row in rows)
    faults = []
    for word in BANNED:
        if word in text:
            faults.append(f"banned word: {word}")
    blob = spoken_text(rows)
    if "possible" not in blob and "scenario" not in blob and "हो सकता" not in blob:
        faults.append("scenario wording missing")
    if CTA not in blob:
        faults.append("cta missing")
    if DISCLAIMER not in blob:
        faults.append("disclaimer missing")
    hook = next((row for row in rows if row.get("sec") == "hook"), None)
    if hook is None or float(hook.get("end") if hook.get("end") is not None else 99) > 3.0:
        faults.append("hook is outside 0-3s")
    elif float(hook.get("start") if hook.get("start") is not None else 1) != 0.0:
        faults.append("hook is outside 0-3s")
    return faults


def price_numbers(text):
    found = []
    for match in re.finditer(r"\d[\d,]*\.?\d*", str(text or "")):
        raw = match.group(0).replace(",", "")
        if not raw or raw == ".":
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        token = match.group(0)
        if "," in token or "." in token or value >= 1000:
            found.append(value)
    return found


def number_faults(rows, levels):
    allowed = []
    for key in ("open", "high", "low", "close", "prev_close", "ema20", "ema50", "rsi14", "atr14", "support", "bounce_high", "next_support"):
        if levels.get(key) is not None:
            allowed.append(float(levels[key]))
    for zone in (levels.get("sl_zone") or [], levels.get("target_zone") or []):
        for item in zone:
            if item is not None:
                allowed.append(float(item))
    if levels.get("chg_pct") is not None:
        allowed.append(abs(float(levels["chg_pct"])))
    faults = []
    for row in rows:
        if row.get("sec") in ("news", "key") or row.get("skip_numbers"):
            continue
        for value in price_numbers(row.get("d")):
            if not any(abs(value - item) <= 0.05 or abs(value - round(item)) <= 0.5 for item in allowed):
                faults.append(f"caption number {value} is not a computed level")
    return faults
