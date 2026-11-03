"""Trader Hinglish. Digits stay on the caption. Spoken lines avoid banned words."""
from __future__ import annotations

import re

BANNED = ("हद", "पूँजी", "क्षमता", "तय करो", "पलटवार", "चीरती", "नतीजा", "हिसाब")
DISCLAIMER = "सिर्फ सीखने के लिए, फाइनेंशियल एडवाइस नहीं।"
CTA = "अगला कौन सा chart देखना है? Comment करो."
FORBIDDEN_FOLLOW = (
    "फॉलो करो",
    "follow करो",
    "follow karo",
    "Paise Ki Pathshala follow",
    "Rajesh Technical Trader ko follow",
)


def _fmt(value):
    """Two decimals with Indian commas; never a raw float like 22997.349609375."""
    from realchart.fmt import inr
    return inr(value)


# Charon reads about 1.75 words a second (42 words came out at 24.0s, runs
# 38683361517 and 38700813455). The Reel QA band is 40-50s.
WORDS_PER_SEC = 1.75
NEWS_FLOOR_SEC = 40.0
NEWS_TARGET_SEC = 45.0
NEWS_CEIL_SEC = 49.0


def estimate_seconds(text_or_rows):
    """Rough spoken length. Takes the rows or the plain spoken text."""
    text = text_or_rows if isinstance(text_or_rows, str) else spoken_text(text_or_rows)
    words = str(text or "").split()
    # A spoken price like 25281 is read as five or six Hindi words.
    extra = sum(2 for w in words if sum(c.isdigit() for c in w) >= 4)
    return (len(words) + extra) / WORDS_PER_SEC


def _sentences(text):
    clean = re.sub(r"<[^>]+>", " ", str(text or ""))
    clean = " ".join(clean.replace("&nbsp;", " ").replace("&amp;", "&").split())
    parts = re.split(r"(?<=[.!?।])\s+", clean)
    return [part.strip() for part in parts if len(part.strip().split()) >= 3]


def _day(article):
    from email.utils import parsedate_to_datetime
    raw = str(article.get("published") or "").strip()
    try:
        return parsedate_to_datetime(raw).strftime("%d %B %Y").lstrip("0")
    except (TypeError, ValueError, IndexError):
        return ""


def news_spoken(article, fixed_words):
    """Article facts only: headline, summary sentences, source and date.

    Sentences are added in article order until the estimate reaches the
    target. Nothing here is written by us except the source/date frame.
    """
    article = article or {}
    headline = str(article.get("title") or "").strip().rstrip(".")
    source = str(article.get("source") or "").strip()
    day = _day(article)
    lines = []
    if headline:
        lines.append(f"हेडलाइन: {headline}.")
    if source or day:
        lines.append(f"यह खबर {source or 'आर्टिकल'} की है{', ' + day if day else ''}।")
    lines.append("यह खबर असली आर्टिकल से है। हमने कोई लाइन नहीं जोड़ी।")
    seen = {headline.lower()}
    facts = []
    for sentence in _sentences(article.get("summary")) + _sentences(article.get("key_sentence")) + _sentences(article.get("why")):
        key = sentence.rstrip(".").lower()
        if key in seen:
            continue
        seen.add(key)
        facts.append(sentence if sentence[-1] in ".!?।" else sentence + ".")
    words = fixed_words + sum(len(line.split()) for line in lines)
    picked = []
    for fact in facts:
        if words / WORDS_PER_SEC >= NEWS_TARGET_SEC:
            break
        n = len(fact.split())
        if (words + n) / WORDS_PER_SEC > NEWS_CEIL_SEC:
            continue
        picked.append(fact)
        words += n
    head = lines[:1]
    return " ".join(head + picked + lines[1:])


def _close_lines(levels):
    """Close narration from computed data only: OHLC, change, pivots, EMA/RSI, zones, source.

    Spoken prices are rounded to whole points (Charon reads each one slowly);
    the exact values stay on the captions.
    """
    def n(key):
        value = levels.get(key)
        return "" if value is None else f"{round(float(value))}"
    chg = float(levels.get("chg_pct") or 0)
    side = "ऊपर" if chg >= 0 else "नीचे"
    ohlc = (
        f"निफ्टी {n('close')} पर बंद, {abs(chg):.2f} प्रतिशत {side}। "
        f"हाई {n('high')}, लो {n('low')}।"
    )
    piv = f"पाँच मिनट चार्ट पर पिवट {n('pivot')}, R1 {n('r1')}, S1 {n('s1')}।"
    parts = []
    if levels.get("ema20") is not None:
        parts.append(f"20 EMA {n('ema20')} पर है।")
    if levels.get("rsi14") is not None and levels.get("rsi_pane_captured"):
        # Only when the TradingView shot actually shows an RSI pane (16-Oct review).
        parts.append(f"RSI {round(float(levels['rsi14']))} पर है।")
    parts.append("ज़ोन सिर्फ लेवल हैं, scenario, ट्रेड सलाह नहीं।")
    src = str(levels.get("values_from") or "").strip()
    parts.append(f"डेटा {src or 'मार्केट डेटा'} से।")
    return ohlc, piv, " ".join(parts)


DECIMAL = re.compile(r"(?<![\d.])(\d[\d,]*)\.(\d+)(?![\d.])")


def whole_numbers(text):
    """Every number the voice reads is a whole number: 50.00 -> 50, 1.66 -> 2, 22,997.35 -> 22997."""
    def repl(match):
        value = float(match.group(1).replace(",", "") + "." + match.group(2))
        return str(int(round(value)))
    return DECIMAL.sub(repl, str(text or ""))


def chunks_for(kind, levels, article=None):
    rows = _chunks_for(kind, levels, article)
    for row in rows:
        row["t"] = whole_numbers(row.get("t"))
    return rows


NEWS_SERIES = "Market News Today"


def _stamp(value):
    from realchart.fmt import stamp
    return stamp(value)


def _news_rows(levels, article):
    """Morning news Reel (Rajesh, 23-Oct-2026): no Nifty chart and no Nifty levels. The single
    biggest real story of the day, told from the article itself: hook, what happened, the two
    key facts, the article's own key line, and a comment question. One row = one voice clip."""
    hindi = article.get("hindi") or {}
    points = list(hindi.get("points") or [])
    source = str(article.get("source") or "").replace("www.", "")
    headline = str(hindi.get("headline") or "").strip() or "आज की सबसे बड़ी खबर"
    rows = [
        {"sec": "hook", "start": 0.0, "end": 3.0, "skip_numbers": True,
         "d": headline, "t": f"{headline}! पूरी खबर देखो।", "k": "hook"},
        {"sec": "news", "skip_numbers": True,
         "d": " · ".join(x for x in (str(article.get("title") or ""), source, _stamp(article.get("published"))) if x),
         "t": str(hindi.get("lead") or "").strip(), "k": source or "खबर"},
        {"sec": "points", "d": " · ".join(points[:2]), "skip_numbers": True,
         "t": " ".join(points[:2]), "k": "points"},
        {"sec": "key", "skip_numbers": True,
         "d": str(article.get("key_sentence") or "")[:240],
         "t": " ".join(points[2:3] + [f"यह सब सीधे {source or 'आर्टिकल'} के आर्टिकल से है।"]), "k": "source"},
    ]
    second = article.get("second") or {}
    h2 = second.get("hindi") or {}
    if h2:
        # Second story (Rajesh, 23-Oct-2026: top 2 stories, ~45 s, <= 7 voice clips).
        rows[0]["t"] = f"{headline}! और साथ में आज की दूसरी बड़ी खबर भी।"
        rows[3]["t"] = " ".join(points[2:3] + [f"यह {source or 'आर्टिकल'} से है।"])
        src2 = str(second.get("source") or "").replace("www.", "")
        p2 = list(h2.get("points") or [])
        rows.append({"sec": "news2", "skip_numbers": True,
                     "d": " · ".join(x for x in (str(second.get("title") or ""), src2, _stamp(second.get("published"))) if x),
                     "t": f"दूसरी बड़ी खबर: {str(h2.get('headline') or '').strip()}। {str(h2.get('lead') or '').strip()}",
                     "k": src2 or "खबर 2"})
        rows.append({"sec": "points2", "skip_numbers": True, "d": " · ".join(p2[:2]),
                     "t": " ".join(p2[:2] + [f"यह {src2 or 'आर्टिकल'} से है।"]), "k": "points"})
    question = "इन खबरों का आप पर क्या असर होगा? Comment करो।" if h2 else "इस खबर का आप पर क्या असर होगा? Comment करो।"
    rows.append({"sec": "cta", "skip_numbers": True, "d": f"{question} {DISCLAIMER}", "t": f"{question} {DISCLAIMER}", "k": "comment"})
    return rows


PATTERN_HI = {
    "support bounce": "सपोर्ट से बाउंस",
    "breakout": "20 दिन का ब्रेकआउट",
    "resistance rejection": "रेजिस्टेंस से रिजेक्शन",
}
TA_SERIES = "Aaj Ka Stock TA"


def _ta_rows(levels):
    """Evening TA Reel (Rajesh, 16-Oct-2026): ONE stock. Pattern, support, resistance and the
    full plan (valid zone, entry trigger, SL, target with RR >= 1.5, invalidation). Spoken
    numbers are whole; the hook is the stock's real close and move in the first 1-2 s."""
    plan = levels.get("plan") or {}
    name = str(levels.get("symbol") or "").split(":")[-1]
    w = lambda v: round(float(v))  # noqa: E731
    chg = float(levels.get("chg_pct") or 0)
    side = "ऊपर" if chg >= 0 else "नीचे"
    pattern = PATTERN_HI.get(plan.get("pattern"), str(plan.get("pattern") or ""))
    long = plan.get("side") == "long"
    zone = plan.get("zone") or [None, None]
    rr = float(plan.get("rr") or 0)
    entry_word = "ऊपर" if long else "नीचे"
    sl_word = "नीचे" if long else "ऊपर"
    prev = levels.get("prev_close")
    move = (f"{w(abs(float(levels['close']) - float(prev)))} रुपये {side}" if prev is not None
            else f"{w(abs(chg))} प्रतिशत {side}")
    rows = [
        {"sec": "hook", "start": 0.0, "end": 3.0,
         "d": f"{name} {_fmt(levels.get('close'))} ({chg:+.2f}%) · {plan.get('pattern')}",
         "t": f"{name} {w(levels['close'])} पर, आज {move}! पूरा प्लान देखो।", "k": name},
        {"sec": "chart", "k": "pattern",
         "d": f"Pattern: {plan.get('pattern')} · Support {_fmt(levels.get('support'))} · Resistance {_fmt(levels.get('resistance'))}",
         "t": (f"डेली चार्ट पर {pattern}। सपोर्ट {w(levels['support'])}, "
               f"रेजिस्टेंस {w(levels['resistance'])}।")},
        {"sec": "intraday", "k": "zone",
         "d": f"Valid zone {_fmt(zone[0])}–{_fmt(zone[1])} · Entry trigger {_fmt(plan.get('entry'))}",
         "t": (f"वैलिड ज़ोन {w(zone[0])} से {w(zone[1])}। एंट्री ट्रिगर: डेली कैंडल "
               f"{w(plan['entry'])} के {entry_word} बंद हो। {SAVE_CTA}")},
        {"sec": "view", "k": "plan",
         "d": (f"SL {_fmt(plan.get('stop'))} · Target {_fmt(plan.get('target'))} · RR {rr:.1f} · "
               f"Invalidation: close {sl_word} {_fmt(plan.get('invalidation'))}"),
         "t": (f"स्टॉप लॉस {w(plan['stop'])}, टारगेट {w(plan['target'])}, रिस्क रिवॉर्ड {str(int(rr)) + ' गुना से ज़्यादा' if rr >= 2 else 'डेढ़ गुना से ज़्यादा'}। "
               f"क्लोज़ {w(plan['invalidation'])} के {sl_word} तो सेटअप खत्म, यह scenario है।")},
    ]
    question = f"{name} {w(plan['entry'])} {'पार करेगा' if long else 'तोड़ेगा'} या नहीं? Comment करो।"
    rows.append({"sec": "cta", "d": f"{question} {DISCLAIMER}", "t": f"{question} {DISCLAIMER}", "k": "comment"})
    return rows


def _chunks_for(kind, levels, article=None):
    if levels.get("format") == "story" and kind in ("ta", "close"):
        from realchart.story_script import rows_for
        return rows_for(kind, levels)
    if kind == "news" and article and article.get("hindi"):
        return _news_rows(levels, article)
    if kind == "ta" and levels.get("plan"):
        return _ta_rows(levels)
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
    if kind in ("close", "ta"):
        rows.append({
            "sec": "intraday",
            "start": 12.0,
            "end": 22.0,
            "d": "5m session",
            "t": "डेली चार्ट के बाद आज का इंट्राडे चार्ट है।",
            "k": "5m",
        })
    if headline or sentence:
        from realchart.fmt import stamp
        published = stamp((article or {}).get("published"))
        shown = " · ".join(part for part in (headline[:110], sentence[:240], source, published) if part)
        rows.append({
            "sec": "news",
            "start": 12.0,
            "end": 40.0 if kind == "news" else 22.0,
            "d": shown,
            "t": (
                news_spoken(article, len(spoken_text(rows).split()) + len(f"{CTA} {DISCLAIMER}".split()))
                if kind == "news" else "यह खबर असली आर्टिकल से है। हमने कोई लाइन नहीं जोड़ी।"
            ),
            "k": source or "खबर",
        })
    if kind != "news":
        sl = levels.get("sl_zone") or [None, None]
        target = levels.get("target_zone") or [None, None]
        rows.append({
            "sec": "view",
            "start": 22.0 if headline or kind in ("close", "ta") else 12.0,
            "end": 40.0,
            "d": (
                f"P {_fmt(levels.get('pivot'))} R1 {_fmt(levels.get('r1'))} S1 {_fmt(levels.get('s1'))} "
                f"R2 {_fmt(levels.get('r2'))} S2 {_fmt(levels.get('s2'))} "
                f"SL {_fmt(sl[0])}–{_fmt(sl[1])} target {_fmt(target[0])}–{_fmt(target[1])}"
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
    if kind == "close":
        ohlc, piv, view = _close_lines(levels)
        for row in rows:
            if row["sec"] == "chart":
                row["t"] = ohlc
            elif row["sec"] == "intraday":
                row["t"] = piv
            elif row["sec"] == "view":
                row["t"] = view
        _growth_close(rows, levels)
        for row in rows:
            if row["sec"] == "chart":
                row["t"] = f"{CURIOSITY} {row['t']}"
            elif row["sec"] == "intraday":
                row["t"] = f"{row['t']} {SAVE_CTA}"
    return rows


CURIOSITY = "कल का सबसे important लेवल end में।"
SAVE_CTA = "यह chart save करो, कल काम आएगा।"
SERIES = "Nifty Close Report"


def next_pivot(levels):
    """Tomorrow's classic pivot from today's real H/L/C."""
    try:
        return (float(levels["high"]) + float(levels["low"]) + float(levels["close"])) / 3.0
    except (KeyError, TypeError, ValueError):
        return None


def _growth_close(rows, levels):
    """Section 8: the day's biggest real number first, a level-specific comment question, tomorrow's level."""
    chg = float(levels.get("chg_pct") or 0)
    side = "ऊपर" if chg >= 0 else "नीचे"
    close = round(float(levels.get("close") or 0))
    levels["next_pivot"] = next_pivot(levels)
    if levels["next_pivot"] is not None:
        # Tomorrow's classic R1/S1 from today's real H/L: the level the question is about.
        levels["next_r1"] = 2 * levels["next_pivot"] - float(levels["low"])
        levels["next_s1"] = 2 * levels["next_pivot"] - float(levels["high"])
    for row in rows:
        if row["sec"] == "chart":
            # The hook already said the close and the move; don't read them twice.
            row["t"] = re.sub(r"निफ्टी [\d,]+ पर बंद, [\d.]+ प्रतिशत \S+।\s*", "", row["t"])
        elif row["sec"] == "view":
            row["t"] = re.sub(r"\s*डेटा [^।]*से।", "", row["t"])
        if row["sec"] == "hook":
            row["d"] = f"Nifty {chg:+.2f}%, {_fmt(levels.get('close'))} पर बंद!"
            prev = levels.get("prev_close")
            if prev is not None:
                points = round(abs(float(levels.get("close") or 0) - float(prev)))
                row["t"] = f"निफ्टी आज {points} अंक {side}, {close} पर बंद!"
            else:
                row["t"] = f"निफ्टी आज {round(abs(chg))} प्रतिशत {side}, {close} पर बंद!"
        elif row["sec"] == "cta":
            r1 = levels.get("next_r1") if chg >= 0 else levels.get("next_s1")
            verb = "टूटेगा" if chg >= 0 else "बचेगा"
            question = f"कल {round(float(r1))} {verb} या नहीं? Comment करो।" if r1 is not None else CTA
            key = levels.get("next_pivot")
            lead = f"कल का key लेवल {round(key)}, pivot। " if key is not None else ""
            row["t"] = f"{lead}{question} कल मिलते हैं। {DISCLAIMER}"
            key_d = f"कल का key लेवल {_fmt(key)} (pivot) · " if key is not None else ""
            row["d"] = f"{key_d}{question} {DISCLAIMER}"


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
    # The scenario line belongs to the trade view. News has no entry, stop, or target.
    if any(row.get("sec") == "view" for row in rows):
        if "possible" not in blob and "scenario" not in blob and "हो सकता" not in blob:
            faults.append("scenario wording missing")
    if CTA not in blob and "Comment करो" not in blob:
        faults.append("cta missing")
    if DISCLAIMER not in blob:
        faults.append("disclaimer missing")
    lowered = text.lower()
    for phrase in FORBIDDEN_FOLLOW:
        if phrase.lower() in lowered:
            faults.append(f"channel follow line: {phrase}")
    if any(row.get("story") for row in rows):
        from realchart.story_script import compliance_faults
        faults.extend(compliance_faults(rows))
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
    for key in (
        "open", "high", "low", "close", "prev_close", "ema20", "ema50", "rsi14", "atr14",
        "support", "bounce_high", "next_support", "pivot", "r1", "s1", "r2", "s2", "next_pivot", "next_r1", "next_s1",
        "call_oi", "call_oi_next", "put_oi", "put_oi_next",
        "resistance", "entry", "stop", "target", "rr",
    ):
        if levels.get(key) is not None:
            allowed.append(float(levels[key]))
    for zone in (levels.get("sl_zone") or [], levels.get("target_zone") or [], levels.get("valid_zone") or []):
        for item in zone:
            if item is not None:
                allowed.append(float(item))
    if levels.get("chg_pct") is not None:
        allowed.append(abs(float(levels["chg_pct"])))
    for fig in levels.get("story_figures") or []:
        for key in ("value", "display"):
            val = fig.get(key)
            if isinstance(val, (int, float)):
                allowed.append(float(val))
                allowed.append(abs(float(val)))
    faults = []
    for row in rows:
        if row.get("story") and row.get("sec") == "news":
            faults.extend(_news_numbers_from_source(row, levels))
            continue
        if row.get("sec") in ("news", "key") or row.get("skip_numbers"):
            continue
        values = _all_numbers(row.get("d")) if row.get("story") else price_numbers(row.get("d"))
        for value in values:
            ok = _story_match(value, allowed) if row.get("story") else any(
                abs(value - item) <= 0.05 or abs(value - round(item)) <= 0.5 for item in allowed
            )
            if not ok:
                faults.append(f"caption number {value} is not a computed level")
    return faults


def _story_match(value, allowed):
    """A caption number must be the fetched figure, or the whole-rupee form of a price."""
    for item in allowed:
        if abs(value - item) <= 0.05 or abs(value - abs(item)) <= 0.05:
            return True
        rounded = round(abs(item))
        if abs(item) >= 50 and abs(value - rounded) <= 0.05:
            return True
    return False


def _all_numbers(text):
    found = []
    for match in re.finditer(r"\d[\d,]*\.?\d*", str(text or "")):
        raw = match.group(0).replace(",", "")
        if not raw or raw == ".":
            continue
        try:
            found.append(float(raw))
        except ValueError:
            continue
    return found


def _news_numbers_from_source(row, levels):
    """A news caption may only repeat digits that were in the fetched article."""
    blob = str(levels.get("news_text") or "").replace(",", "")
    faults = []
    for match in re.finditer(r"\d[\d,]*\.?\d*", str(row.get("d") or "")):
        plain = match.group(0).replace(",", "")
        if plain and plain not in blob:
            faults.append(f"news number {match.group(0)} is not in the fetched article")
    return faults


FOLLOW_LINE = "Roz 4 baje Nifty analysis ke liye follow karo"


def end_card_lines(kind, levels):
    """Extra end-card lines: tomorrow's real key level, the daily follow promise, sign-off."""
    if levels.get("format") == "story":
        if kind == "ta":
            return ["रोज़ एक स्टॉक का उदाहरण", "कल मिलते हैं"]
        if kind == "close":
            return ["रोज़ निफ्टी का क्लोज़", "कल मिलते हैं"]
    if kind == "news":
        return ["Roz subah 8 baje sabse badi khabar", "4 baje milte hain: Nifty Close Report"]
    if kind == "ta":
        from realchart.fmt import inr
        plan = levels.get("plan") or {}
        name = str(levels.get("symbol") or "").split(":")[-1]
        # Devanagari only (1-Nov review); the plan rows are drawn by ends.ta_end_image.
        return ["रोज़ शाम एक स्टॉक का पूरा प्लान", "कल मिलते हैं"]
    if kind != "close":
        return []
    key = levels.get("next_pivot") or next_pivot(levels)
    lines = []
    if key is not None:
        from realchart.fmt import inr
        lines.append(f"Kal ka key level: {inr(key, 0)} (Pivot)")
    lines += [FOLLOW_LINE, "Kal milte hain"]
    return lines


def post_meta(levels):
    """Keyword-first title, caption, hashtags and pinned comment, all from real levels."""
    from datetime import date
    from realchart.fmt import inr
    try:
        day = date.fromisoformat(str(levels.get("last_date"))[:10])
        day_text = f"{day.day} {day.strftime('%b %Y')}"
    except ValueError:
        day_text = ""
    chg = float(levels.get("chg_pct") or 0)
    close = inr(levels.get("close"), 0)
    key = levels.get("next_pivot") or next_pivot(levels)
    q = levels.get("next_r1") if chg >= 0 else levels.get("next_s1")
    series = f"{SERIES} #{int(levels['series_n'])}" if levels.get("series_n") else SERIES
    title = f"Nifty analysis today | {series} {day_text}".strip()
    caption = (
        f"Nifty prediction for tomorrow | Nifty analysis today {day_text}: Nifty {chg:+.2f}% पर {close} पर बंद। "
        f"कल का key level {inr(key, 0)} (pivot). Stock market Hindi me roz 4 baje. Educational only, not advice."
    )
    pinned = f"कल Nifty {inr(q, 0)} {'तोड़ेगा' if chg >= 0 else 'बचाएगा'} या नहीं? आपका view comment करो 👇" if q else ""
    return {
        "title": title,
        "caption": caption,
        "hashtags": ["#NiftyPrediction", "#NiftyAnalysisToday", "#StockMarketHindi", "#Nifty50", "#NiftyCloseReport"],
        "pinned_comment": pinned,
        "series": SERIES,
    }


def news_post_meta(levels, article):
    """Keyword-first title and post text for the news Reel."""
    from datetime import datetime, timedelta, timezone
    from realchart.fmt import inr
    today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
    day_text = f"{today.day} {today.strftime('%b %Y')}"
    head = str(article.get("title") or "").strip()
    prefix = f"Aaj ki sabse badi khabar | {day_text} | "
    room = 100 - len(prefix)
    if len(head) > room:
        head = head[:room].rsplit(" ", 1)[0].rstrip(" ,:-") + "…"
    hindi = article.get("hindi") or {}
    caption = (f"Aaj ki sabse badi khabar ({day_text}): {hindi.get('lead') or ''} "
               f"Source: {article.get('source') or ''} ({article.get('url') or ''}). Educational only, not advice.")
    return {
        "title": prefix + head,
        "caption": caption,
        "hashtags": ["#BusinessNews", "#TechNews", "#AINews", "#StockMarketNews", "#HindiNews"],
        "pinned_comment": "इस खबर का आप पर क्या असर होगा? Comment करो 👇",
        "series": NEWS_SERIES,
    }


def end_card_voice(kind):
    """Spoken follow line for the end card (not part of the narration rows the lint checks)."""
    if kind != "ta":
        return {}
    return {
        "rtt": "रोज़ शाम एक स्टॉक का पूरा प्लान। Rajesh Technical Trader को follow करो।",
        "pkp": "रोज़ शाम एक स्टॉक का पूरा प्लान। Paise Ki Pathshala को follow करो।",
    }


def thumb_text(kind, levels, article=None):
    """The clear Hindi headline burned on the YouTube thumbnail for every Paise Reel."""
    from realchart.fmt import inr
    if kind == "news":
        return str(((article or {}).get("hindi") or {}).get("headline") or "आज की सबसे बड़ी खबर")
    name = str(levels.get("symbol") or "").split(":")[-1]
    if kind == "ta":
        return f"{name}: आज का पूरा ट्रेड प्लान"
    if levels.get("close") is None:
        return "आज का Nifty क्लोज़"
    word = "चढ़ा" if float(levels.get("chg_pct") or 0) >= 0 else "गिरा"
    return f"Nifty {inr(levels.get('close'), 0)} पर बंद, आज {word}"


def thumb_topic(kind, levels, article=None):
    """What the Gemini thumbnail background should depict (scene only, never data)."""
    if kind == "news":
        return str((article or {}).get("title") or "biggest business news of the day")
    name = str(levels.get("symbol") or "").split(":")[-1]
    if kind == "ta":
        return f"{name} stock: a trader's game plan for today"
    up = float(levels.get("chg_pct") or 0) >= 0
    return f"Indian stock market closing bell, Nifty {'rallies' if up else 'falls'} today"
