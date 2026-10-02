"""Deterministic first-2-second hook checks. No LLM, no network.

A punchy opening line must be present, short enough to land in the first
two seconds, and be one of: a question, a surprising number that already
exists in the topic, or a contrarian line. It must not promise returns.
"""
import re

MIN_WORDS = 4
MAX_WORDS = 12
REAL_DATA_MAX_WORDS = 10

# Promises of profit. Negations such as "गारंटी नहीं" do not match.
CLICKBAIT_RE = re.compile(
    r"(गारंटी\s*से|guaranteed|sure[- ]?shot|पक्का\s*मुनाफ|निश्चित\s*रिटर्न|"
    r"100\s*%\s*(रिटर्न|return|मुनाफा|profit)|पैसा\s*डबल|double your money|"
    r"रिटर्न\s*पक्का|मुनाफा\s*पक्का|profit\s+guaranteed)",
    re.I,
)
QUESTION_RE = re.compile(r"(\?|क्या|क्यों|कैसे|कौन|कब|कितना|कहाँ|कहां)")
CONTRARIAN_RE = re.compile(r"(नहीं|गलत|मत\s|उल्टा|हर कोई गलत|सच नहीं)")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numbers(text):
    return {n.replace(",", "") for n in NUMBER_RE.findall(text or "")}


def allowed_numbers(topic):
    """Numbers the narration may speak: only those already on the topic card."""
    blob = " ".join([
        str(topic.get("topic") or ""),
        *[str(x) for x in (topic.get("thumb") or [])],
        *[str(x) for x in (topic.get("facts") or [])],
        *[str(x) for pair in (topic.get("beats") or {}).values() for x in pair],
    ])
    return extract_numbers(blob) | {"1", "2", "3"}


# Same markers the evergreen buffer uses. A figure counts as an example only
# when one of these sits in the same sentence.
_EXAMPLE_MARK = re.compile(r"(example|misal|मिसाल|उदाहरण|मान\s*लीजिए|illustrative)", re.I)
TEACHING_BEATS = ("udaharan", "galti")


def example_labelled_numbers(text):
    """Digits that share a sentence with an explicit example label."""
    found = set()
    for sentence in re.split(r"[।!?]+|\.\s+", text or ""):
        if not _EXAMPLE_MARK.search(sentence):
            continue
        found |= extract_numbers(sentence)
    return found


def allows_example_numbers(topic):
    """Evergreen concept with no live market series. Real prices stay strict."""
    if not isinstance(topic, dict) or topic.get("_atr"):
        return False
    import buffer_shorts
    ok, _why = buffer_shorts.is_evergreen_topic(topic)
    return bool(ok)


def _canon_number(value):
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _topic_blob(topic):
    parts = [str((topic or {}).get("topic") or "")]
    parts.extend(str(x) for x in ((topic or {}).get("thumb") or []))
    parts.extend(str(x) for x in ((topic or {}).get("facts") or []))
    for pair in ((topic or {}).get("beats") or {}).values():
        if isinstance(pair, (list, tuple)):
            parts.extend(str(x) for x in pair)
        else:
            parts.append(str(pair))
    return " ".join(parts)


def _labelled_level(blob, labels):
    for label in labels:
        match = re.search(rf"(?:{label})\s*[:\-]?\s*(\d[\d,]*(?:\.\d+)?)", blob, re.I)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def worked_example_numbers(topic):
    """Figures the worked example may use, taken from the topic card.

    Entry, stop, and target on the card also yield the per-share risk and
    reward, and the same sums for the quantity the card already states.
    A charge is included only when the card already writes that figure.
    """
    blob = _topic_blob(topic)
    found = set(extract_numbers(blob))
    entry = _labelled_level(blob, ["entry", "एंट्री", "buy"])
    stop = _labelled_level(blob, ["SL", "stop(?:-loss)?", "स्टॉप"])
    target = _labelled_level(blob, ["target", "टारगेट"])
    qty = _labelled_level(blob, ["quantity", "qty"])
    qty_match = re.search(r"(\d[\d,]*)\s*शेयर", blob)
    if qty_match:
        qty = float(qty_match.group(1).replace(",", ""))
    derived = set()
    if entry is not None and stop is not None:
        derived.add(_canon_number(abs(entry - stop)))
    if entry is not None and target is not None:
        derived.add(_canon_number(abs(target - entry)))
    if qty is not None and entry is not None and stop is not None:
        derived.add(_canon_number(abs(entry - stop) * qty))
    if qty is not None and entry is not None and target is not None:
        derived.add(_canon_number(abs(target - entry) * qty))
    for label in ("charges", "charge", "brokerage", "STT", "चार्ज", "ब्रोकरेज"):
        match = re.search(rf"{label}\s*[:\-]?\s*(\d[\d,]*(?:\.\d+)?)", blob, re.I)
        if not match:
            match = re.search(rf"(\d[\d,]*(?:\.\d+)?)\s*(?:{label})", blob, re.I)
        if match:
            derived.add(match.group(1).replace(",", ""))
            break
    return found | derived


def _market_claim(topic):
    if not isinstance(topic, dict) or topic.get("_atr"):
        return True
    if str(topic.get("cat") or "") in ("REAL DATA", "MARKET FACT"):
        return True
    import market_data
    key = str(topic.get("key") or "")
    if key in getattr(market_data, "TOPIC_SERIES", ()) or key == "atr_sl":
        return True
    return False


def teaching_allowed_numbers(topic, sourced=None):
    """The only figures udaharan and galti may use.

    Concept topics get numbers computed from the card (entry, stop, target,
    and the risk and reward that arithmetic produces). A market-data topic
    gets only figures already in the topic or the fetched series. A label
    such as उदाहरण does not add a number.
    """
    extra = set(sourced or ())
    if _market_claim(topic):
        # Real prices stay on the card and the fetched series. Do not
        # invent a risk or a charge the source did not write.
        return set(extract_numbers(_topic_blob(topic))) | extra
    return set(worked_example_numbers(topic)) | extra


def live_number_faults(beats, topic, order, allowed=None):
    """Numbers that were not computed from the topic or the sourced data.

    udaharan and galti have no label exception. An invented figure fails
    even when the sentence says उदाहरण. Real market data stays on the
    sourced set only.
    """
    card = set(allowed if allowed is not None else allowed_numbers(topic))
    sourced = set()
    if allowed is not None:
        sourced = set(allowed) - set(allowed_numbers(topic))
    teaching = teaching_allowed_numbers(topic, sourced=sourced)
    extra = set()
    for beat in order:
        text = str((beats or {}).get(beat) or "")
        nums = extract_numbers(text)
        ok = teaching if beat in TEACHING_BEATS else card
        extra |= nums - ok
    return extra


def unlabelled_example_beats(beats, order):
    """Teaching beats whose numbers are not in a sentence that says उदाहरण."""
    found = []
    for beat in order:
        if beat not in TEACHING_BEATS:
            continue
        text = str((beats or {}).get(beat) or "")
        if not extract_numbers(text):
            continue
        pieces = re.split(r"[।!?]+|\.\s+", text) or [text]
        if not any(piece.strip() for piece in pieces):
            pieces = [text]
        for sentence in pieces:
            if extract_numbers(sentence) and "उदाहरण" not in sentence:
                found.append(beat)
                break
    return found


def example_card_lines(topic):
    """Scene card for the worked example: the word उदाहरण plus the computed figures."""
    nums = teaching_allowed_numbers(topic)

    def key(value):
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (1, str(value))

    shown = ", ".join(sorted(nums, key=key))
    detail = f"उदाहरण {shown}".strip()
    return "उदाहरण", detail


def beats_with_number_faults(beats, topic, order, allowed=None):
    """Beat names whose own text contains a rejected number."""
    found = []
    for beat in order:
        if live_number_faults({beat: (beats or {}).get(beat)}, topic, (beat,), allowed=allowed):
            found.append(beat)
    return found


def unsourced_numbers(text, topic, allowed=None):
    """Spoken digits that are neither on the card nor a labelled example.

    A labelled example is accepted only for an evergreen topic with no market
    data. REAL DATA, a fetched series, and an unlabelled figure still fail.
    """
    allowed = set(allowed if allowed is not None else allowed_numbers(topic))
    extra = extract_numbers(text) - allowed
    if extra and allows_example_numbers(topic):
        extra -= example_labelled_numbers(text)
    return extra


def hook_word_limit(topic=None):
    if topic and topic.get("cat") == "REAL DATA":
        return REAL_DATA_MAX_WORDS
    return MAX_WORDS


def hook_style(line, allowed):
    """'question', 'number', 'contrarian', or None."""
    text = (line or "").strip()
    if not text:
        return None
    if QUESTION_RE.search(text):
        return "question"
    nums = extract_numbers(text)
    if nums and nums <= set(allowed or ()):
        return "number"
    if CONTRARIAN_RE.search(text):
        return "contrarian"
    return None


def clickbait_return_claim(line):
    """True for a return promise. A negation ('गारंटी नहीं', 'guaranteed profit नहीं') is allowed."""
    text = line or ""
    if not CLICKBAIT_RE.search(text):
        return False
    if re.search(r"(नहीं|nahi|\bnot\b)", text, re.I):
        return False
    return True


def check_hook_line(line, allowed, max_words=MAX_WORDS, min_words=MIN_WORDS):
    """Return a list of failures. Empty list means the opening line is OK."""
    fails = []
    text = " ".join(str(line or "").split())
    if not text:
        return ["hook line missing"]
    words = text.split()
    if not min_words <= len(words) <= max_words:
        fails.append(f"hook line {len(words)} words ({min_words}-{max_words} required)")
    style = hook_style(text, allowed)
    if style is None:
        fails.append("hook must be a question, a real topic number, or a contrarian line")
    elif style == "number":
        extra = extract_numbers(text) - set(allowed or ())
        if extra:
            fails.append(f"hook number is not in the topic facts: {sorted(extra)}")
    else:
        extra = extract_numbers(text) - set(allowed or ())
        if extra:
            fails.append(f"hook number is not in the topic facts: {sorted(extra)}")
    if clickbait_return_claim(text):
        fails.append("hook makes a clickbait return claim")
    return fails


def _clip_question(text, max_words):
    words = " ".join(str(text or "").split()).split()
    if len(words) > max_words:
        words = words[:max_words]
    line = " ".join(words).rstrip("।. ")
    if "?" not in line and not CONTRARIAN_RE.search(line):
        if len(line.split()) >= max_words:
            line = " ".join(line.split()[: max_words - 1])
        line = line + "?"
    return " ".join(line.split())


def _clip_around_number(text, max_words):
    """Keep a window that still contains the fact's number."""
    words = " ".join(str(text or "").split()).split()
    if not words:
        return ""
    idx = next((i for i, word in enumerate(words) if NUMBER_RE.search(word)), None)
    if idx is None or len(words) <= max_words:
        return " ".join(words[:max_words])
    start = max(0, min(idx, len(words) - max_words))
    return " ".join(words[start:start + max_words])


def _fact_number_hook(topic, limit):
    """Short line whose number is copied from the facts. None when nothing fits."""
    facts = [str(x) for x in (topic or {}).get("facts") or []]
    allowed = allowed_numbers(topic)
    ranked = []
    for fact in facts:
        nums = extract_numbers(fact)
        if not nums:
            continue
        line = _clip_around_number(fact, limit)
        kept = extract_numbers(line)
        if not (kept & nums):
            continue
        if check_hook_line(line, allowed, max_words=limit):
            continue
        interesting = kept - {"1", "2", "3"}
        ranked.append((0 if interesting else 1, len(line.split()), len(line), line))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][3]


def number_provenance(line, topic):
    """Where each number in the line was copied from. None means it was invented."""
    topic = topic or {}
    sources = (
        ("facts", " ".join(str(x) for x in (topic.get("facts") or []))),
        ("topic", str(topic.get("topic") or "")),
        ("beats", " ".join(str(x) for pair in (topic.get("beats") or {}).values() for x in pair)),
        ("thumb", " ".join(str(x) for x in (topic.get("thumb") or []))),
    )
    found = {}
    for number in extract_numbers(line):
        source = None
        for name, blob in sources:
            if number in extract_numbers(blob):
                source = name
                break
        found[number] = source
    return found


def _real_data_hook(topic, limit):
    """Question only. REAL DATA hooks may keep the year and '1', not the answer."""
    topic_text = str((topic or {}).get("topic") or "")
    beats = ((topic or {}).get("beats") or {}).get("hook") or []
    blob = topic_text + " " + " ".join(str(x) for x in beats)
    years = re.findall(r"(?:19|20)\d\d", topic_text)
    if years and re.search(r"₹\s*1|1\s*लाख", blob):
        line = f"{years[0]} में ₹1 लाख का असली हिसाब?"
    elif years:
        line = f"{years[0]} में यह सवाल क्यों जरूरी है?"
    else:
        line = "यह सवाल क्यों जरूरी है अभी?"
    return _clip_question(line, limit)


def make_hook_line(topic):
    """Punchy opening line from the topic card only. Never adds a new number."""
    limit = hook_word_limit(topic)
    if (topic or {}).get("cat") == "REAL DATA":
        return _real_data_hook(topic, limit)
    numbered = _fact_number_hook(topic, limit)
    if numbered:
        return numbered
    beats = (topic.get("beats") or {}).get("hook") or ["", ""]
    h0, h1 = str(beats[0]), str(beats[1] if len(beats) > 1 else "")
    candidates = []
    combined = " ".join(f"{h0} {h1}".split())
    if combined:
        candidates.append(combined if "?" in combined or CONTRARIAN_RE.search(combined)
                          else combined.rstrip("।. ") + "?")
    for raw in (h1, h0):
        s = " ".join(str(raw).split())
        if s:
            candidates.append(s if "?" in s else s.rstrip("।. ") + "?")
    allowed = allowed_numbers(topic)
    for raw in candidates:
        line = _clip_question(raw, limit)
        # If the clip dropped the question mark's word budget, rebuild once.
        if len(line.split()) < MIN_WORDS and h0 and h1:
            line = _clip_question(f"{h0} {h1}", limit)
        if not check_hook_line(line, allowed, max_words=limit):
            return line
    # Last resort: a short question with no new number and no return claim.
    return _clip_question("यह बात जितनी आसान लगती है", limit)
