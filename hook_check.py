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

# Compounds and round tens from 11 up. Bare एक…दस are counters ("एक नियम",
# "चार कदम") and are not figures unless a scale or प्रतिशत follows.
_HINDI_ONES = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4,
    "पाँच": 5, "पांच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19,
    "बीस": 20, "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "तेइस": 23, "चौबीस": 24,
    "पच्चीस": 25, "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "अट्ठाइस": 28,
    "उनतीस": 29, "तीस": 30, "इकतीस": 31, "बत्तीस": 32, "तैंतीस": 33,
    "चौंतीस": 34, "पैंतीस": 35, "छत्तीस": 36, "सैंतीस": 37, "अड़तीस": 38,
    "उनतालीस": 39, "चालीस": 40, "इकतालीस": 41, "बयालीस": 42, "तैंतालीस": 43,
    "चवालीस": 44, "पैंतालीस": 45, "छियालीस": 46, "सैंतालीस": 47, "अड़तालीस": 48,
    "उनचास": 49, "पचास": 50, "इक्यावन": 51, "बावन": 52, "तिरेपन": 53,
    "चौवन": 54, "पचपन": 55, "छप्पन": 56, "सत्तावन": 57, "अट्ठावन": 58,
    "उनसठ": 59, "साठ": 60, "इकसठ": 61, "बासठ": 62, "तिरेसठ": 63, "चौंसठ": 64,
    "पैंसठ": 65, "छियासठ": 66, "सड़सठ": 67, "अड़सठ": 68, "उनहत्तर": 69,
    "सत्तर": 70, "इकहत्तर": 71, "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74,
    "पचहत्तर": 75, "छिहत्तर": 76, "सतहत्तर": 77, "अठहत्तर": 78, "उनासी": 79,
    "अस्सी": 80, "इक्यासी": 81, "बयासी": 82, "तिरासी": 83, "चौरासी": 84,
    "पचासी": 85, "छियासी": 86, "सत्तासी": 87, "अठासी": 88, "नवासी": 89,
    "नब्बे": 90, "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94,
    "पचानवे": 95, "छियानवे": 96, "सत्तानवे": 97, "अट्ठानवे": 98, "निन्यानवे": 99,
    "सौ": 100,
}
_HINDI_SCALE = {
    "हजार": 1000, "हज़ार": 1000, "लाख": 100000, "करोड़": 10000000, "करोड": 10000000,
}
_HINDI_WORD = "|".join(sorted(_HINDI_ONES, key=len, reverse=True))
_HINDI_NUM_RE = re.compile(
    rf"(?<![\u0900-\u097F])({_HINDI_WORD})(?![\u0900-\u097F])"
    rf"(?:\s+(हज़ार|हजार|लाख|करोड़|करोड|प्रतिशत))?"
)
_DIGIT_SCALE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(हज़ार|हजार|लाख|करोड़|करोड)"
)


def _add_canon(found, value):
    number = float(value)
    if number.is_integer():
        found.add(str(int(number)))
    else:
        found.add(f"{number:.4f}".rstrip("0").rstrip("."))


# Skip danda (U+0964/U+0965) so "बीस।" is the word बीस.
_HINDI_TOKEN = re.compile(r"[\u0900-\u0963\u0966-\u097F]+")
_HUNDRED_WORD = {"सौ", "सो"}


def _hindi_number_values(text):
    """Hindi figures in order. Compounds stay one number.

    बाईस हज़ार छह सौ is 22600, एक सौ बीस is 120, चार हज़ार is 4000.
    Bare एक through दस are skipped unless प्रतिशत or a scale follows.
    """
    tokens = [match.group() for match in _HINDI_TOKEN.finditer(text or "")]
    found = []
    i = 0
    while i < len(tokens):
        word = tokens[i]
        if word not in _HINDI_ONES:
            i += 1
            continue
        value = 0
        j = i
        consumed = False
        while j < len(tokens) and tokens[j] in _HINDI_ONES:
            number = _HINDI_ONES[tokens[j]]
            nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
            if nxt in _HINDI_SCALE:
                value += number * _HINDI_SCALE[nxt]
                j += 2
                consumed = True
                continue
            if nxt in _HUNDRED_WORD and number < 10:
                value += number * 100
                j += 2
                consumed = True
                continue
            if consumed:
                value += number
                j += 1
            break
        if consumed:
            found.append(value)
            i = j
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if nxt == "प्रतिशत" or _HINDI_ONES[word] >= 11:
            found.append(_HINDI_ONES[word])
        i += 1
    return found


def extract_numbers(text):
    """Digits, digit+scale (₹2 लाख = 200000), and Hindi number words.

    पचहत्तर, नवासी, चार हज़ार, दो लाख and बाईस हज़ार छह सौ are figures.
    Bare एक through दस are not, unless प्रतिशत or a scale follows.
    """
    text = text or ""
    found = set()
    consumed = []
    for match in _DIGIT_SCALE_RE.finditer(text):
        raw = float(match.group(1).replace(",", ""))
        _add_canon(found, raw * _HINDI_SCALE[match.group(2)])
        consumed.append((match.start(1), match.end(1)))
    for value in _hindi_number_values(text):
        _add_canon(found, value)
    for match in NUMBER_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in consumed):
            continue
        found.add(match.group(0).replace(",", ""))
    return found


def _sentence_percents(sentence):
    """Percent values in order, including Hindi words such as पचहत्तर प्रतिशत."""
    found = []
    for match in _PERCENT_PAIR.finditer(sentence or ""):
        for group in (1, 2):
            raw = match.group(group)
            if not raw:
                continue
            try:
                found.append((match.start(group), float(raw.replace(",", ""))))
            except ValueError:
                continue
    for match in _HINDI_NUM_RE.finditer(sentence or ""):
        if match.group(2) != "प्रतिशत":
            continue
        found.append((match.start(), float(_HINDI_ONES[match.group(1)])))
    found.sort(key=lambda item: item[0])
    return [value for _pos, value in found]


def allowed_numbers(topic):
    """Numbers the narration may speak: only those already on the topic card."""
    blob = " ".join([
        str(topic.get("topic") or ""),
        *[str(x) for x in (topic.get("thumb") or [])],
        *[str(x) for x in (topic.get("facts") or [])],
        *[str(x) for pair in (topic.get("beats") or {}).values() for x in pair],
    ])
    # 100−p is not a free number. It is allowed only when the same sentence
    # writes the subtraction. See spelled_complement_numbers.
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


# udaharan and galti may use these factors even when the card does not list them.
# A result is allowed only after the written expression is evaluated.
STANDARD_MULTIPLIERS = (1, 1.5, 2, 3)
_EXPR_OP = r"[+\-−–*×x/÷]"
_EXPR_NUM = r"\d[\d,]*(?:\.\d+)?"
_EXPR_RE = re.compile(
    rf"({_EXPR_NUM}(?:\s*{_EXPR_OP}\s*{_EXPR_NUM})+)(?:\s*=\s*({_EXPR_NUM}))?"
)


def standard_multiplier_numbers():
    return {_canon_number(value) for value in STANDARD_MULTIPLIERS}


_PERCENT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(?:%|％|प्रतिशत|percent)",
    re.I,
)


def percentage_complements(topic):
    """Complements of topic percentages. Not legal until the sentence subtracts.

    A 40% win rate is a 60% loss rate only when the same sentence writes
    100−40 or 100 में से 40. The bare complement is not on the allow list.
    Values outside 0–100 do not grow this set.
    """
    found = set()
    for match in _PERCENT_RE.finditer(_topic_blob(topic)):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if not 0 < value < 100:
            continue
        found.add(_canon_number(100 - value))
    return found


def _topic_percents(topic):
    found = []
    for match in _PERCENT_RE.finditer(_topic_blob(topic)):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if 0 < value < 100 and value not in found:
            found.append(value)
    return found


def spelled_complement_numbers(text, topic):
    """100 and 100−p, only when this sentence writes the subtraction.

    "100 − 40 = 60" and "100 में से 40 ... 60" pass. "हार 60 प्रतिशत" with
    no subtraction does not.
    """
    percents = _topic_percents(topic)
    if not percents:
        return set()
    found = set()
    for sentence in re.split(r"[।!?？]+|\.\s+", text or ""):
        for value in percents:
            token = _canon_number(value)
            comp = _canon_number(100 - value)
            wrote = re.search(
                rf"100\s*[-−–]\s*{re.escape(token)}\b",
                sentence,
            ) or re.search(
                rf"100\s*में\s*से\s*{re.escape(token)}\b",
                sentence,
            )
            if not wrote:
                continue
            found.add("100")
            if comp in extract_numbers(sentence):
                found.add(comp)
    return found


_LOSS_WORD = re.compile(r"घट|गिर|नुकसान|डूब|लॉस|loss", re.I)
_RECOVERY_WORD = re.compile(r"चाहिए|वापसी|रिकवर|ऊपर|बढ़", re.I)
_PERCENT_PAIR = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)(?:\s*/\s*(\d[\d,]*(?:\.\d+)?))?\s*(?:%|％|प्रतिशत)",
    re.I,
)


def _is_loss_recovery_topic(topic):
    if str((topic or {}).get("key") or "") == "loss_recovery":
        return True
    blob = _topic_blob(topic)
    return "नुकसान का गणित" in blob or "loss recovery" in blob.lower()


def _recovery_percent(loss):
    """Percent needed to come back from a loss of `loss` percent. L/(1−L)."""
    if loss <= 0 or loss >= 100:
        return None
    return 100.0 * loss / (100.0 - loss)


def loss_recovery_pair_faults(text, topic):
    """A stated loss and recovery must be recovery = L/(1−L). Any other pair blocks.

    10% then 11% passes (11.11 rounds to 11). 75% then 89% or 90% does not;
    that fall needs 300%. 50% then 11% is also a block, even though both
    figures sit on the loss-recovery card. "नहीं" does not excuse a later
    wrong recovery: "25% नहीं, 20% चाहिए" still blocks 20.
    """
    if not _is_loss_recovery_topic(topic):
        return set()
    bad = set()
    for sentence in re.split(r"[।!?？]+|\.\s+", text or ""):
        if not _LOSS_WORD.search(sentence):
            continue
        if not (_RECOVERY_WORD.search(sentence) or "नहीं" in sentence):
            continue
        values = _sentence_percents(sentence)
        if len(values) < 2:
            continue
        expected = _recovery_percent(values[0])
        for value in values[1:]:
            if expected is None or abs(value - expected) > 0.51:
                bad.add(_canon_number(value))
    return bad


_SAME_BOUNCE = re.compile(
    r"उतन[ाीं]\s*ही(?:\s+\S+){0,6}\s*(?:ऊपर|चढ़|वापस|बढ़)"
)
# "नुकसान छोटे हुए पैसे पर गिना जाता है" counts the loss on the reduced base.
# The recovery is what is counted there.
_LOSS_ON_SMALLER = re.compile(
    r"नुकसान\s+(?:छोटे(?:\s+हुए)?|छोटा|छोटी)\s+(?:पैसे|रकम|आधार).{0,24}(?:गिन|हिसाब)"
)
# Below the breakeven only part of the premium is lost. Full loss is at the strike.
_BELOW_BREAKEVEN_LOST = re.compile(
    r"(?:ब्रेकईवन|breakeven|22[,.]?720).{0,48}(?:नीचे|से\s*कम).{0,32}"
    r"(?:डूब|पूरा\s*प्रीमियम|सारा\s*प्रीमियम)"
    r"|(?:ब्रेकईवन|breakeven|22[,.]?720).{0,24}से\s*नीचे\s*पैसा\s*डूब",
    re.I | re.S,
)


def _is_option_breakeven_topic(topic):
    if str((topic or {}).get("key") or "") == "option_breakeven":
        return True
    blob = _topic_blob(topic)
    return "ब्रेकईवन" in blob or "breakeven" in blob.lower()


_VAGUE_DOUBLE = re.compile(r"दुगन|दोगुन|दुगुना|दोगुना|double", re.I)


def _double_is_backed(sentence):
    """True only when this sentence's pair is the 50% loss that needs 100%."""
    values = _sentence_percents(sentence)
    for i in range(len(values) - 1):
        loss, rec = values[i], values[i + 1]
        expected = _recovery_percent(loss)
        if expected is None:
            continue
        if abs(rec - expected) <= 0.51 and abs(rec - 2.0 * loss) <= 0.51:
            return True
    return False


def fact_contradiction_notes(text, topic):
    """Claims that fight the topic's own facts.

    Loss recovery needs more than the fall, and the recovery (not the loss)
    is computed on the smaller base. Between strike and breakeven only part
    of the premium is lost. A vague double is not the recovery formula.
    """
    notes = []
    blob = text or ""
    if _is_loss_recovery_topic(topic):
        if _SAME_BOUNCE.search(blob):
            notes.append(
                "claim contradicts the topic facts: a fall does not come back by the same percent"
            )
        if _LOSS_ON_SMALLER.search(blob):
            notes.append(
                "claim contradicts the topic facts: the recovery, not the loss, "
                "is computed on the smaller base"
            )
        for sentence in re.split(r"[।!?？]+", blob):
            if not _VAGUE_DOUBLE.search(sentence):
                continue
            if _double_is_backed(sentence):
                continue
            notes.append(
                "claim contradicts the topic facts: a 20% loss needs 25%, not double"
            )
            break
    if _is_option_breakeven_topic(topic) and _BELOW_BREAKEVEN_LOST.search(blob):
        notes.append(
            "claim contradicts the topic facts: between the strike and the breakeven "
            "only part of the premium is lost"
        )
    return notes


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


def _same_number(left, right):
    try:
        return abs(float(left) - float(right)) <= 1e-6
    except (TypeError, ValueError):
        return False


def _eval_arithmetic(expr, allowed):
    """Evaluate a left-to-right sum of products. Every operand must be allowed.

    Multiplication and division bind first, so 200 − 1.5×2 is 197, not 397.
    """
    tokens = re.findall(rf"{_EXPR_NUM}|{_EXPR_OP}", expr or "")
    if len(tokens) < 3 or len(tokens) % 2 == 0:
        return None
    values = []
    for token in tokens[0::2]:
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            return None
        if _canon_number(number) not in allowed:
            return None
        values.append(number)
    ops = tokens[1::2]
    if len(ops) != len(values) - 1:
        return None
    reduced = [values[0]]
    add_ops = []
    index = 0
    while index < len(ops):
        op = ops[index]
        rhs = values[index + 1]
        if op in ("*", "×", "x", "/", "÷"):
            left = reduced.pop()
            if op in ("/", "÷") and rhs == 0:
                return None
            product = left * rhs if op in ("*", "×", "x") else left / rhs
            reduced.append(product)
        else:
            reduced.append(rhs)
            add_ops.append("-" if op in ("-", "−", "–") else "+")
        index += 1
    result = reduced[0]
    for op, rhs in zip(add_ops, reduced[1:]):
        result = result + rhs if op == "+" else result - rhs
    return result


def verified_arithmetic_numbers(text, allowed):
    """Results of expressions in `text` whose operands are already allowed.

    A stated equals-value is kept only when it matches the evaluation.
    200 − 1.5×2 = 197 passes. The same expression = 300 does not.
    """
    allowed = {_canon_number(float(str(item).replace(",", ""))) for item in (allowed or set()) if str(item).strip()}
    found = set()
    for match in _EXPR_RE.finditer(text or ""):
        value = _eval_arithmetic(match.group(1), allowed)
        if value is None:
            continue
        stated = match.group(2)
        if stated is None:
            found.add(_canon_number(value))
            continue
        stated_value = float(stated.replace(",", ""))
        if _same_number(value, stated_value):
            found.add(_canon_number(stated_value))
    return found


def same_sentence_stated_results(text, allowed):
    """Equals-results whose operands are allowed and whose arithmetic matches.

    The expression and the written result have to sit in the same sentence.
    40 × 2 = 80 passes. 40 × 2 = 300 does not, and a bare 300 does not either.
    """
    allowed = {
        _canon_number(float(str(item).replace(",", "")))
        for item in (allowed or set())
        if str(item).strip()
    }
    found = set()
    for sentence in re.split(r"[।!?？]+|\.\s+", text or ""):
        for match in _EXPR_RE.finditer(sentence):
            value = _eval_arithmetic(match.group(1), allowed)
            stated = match.group(2)
            if value is None or stated is None:
                continue
            try:
                stated_value = float(stated.replace(",", ""))
            except ValueError:
                continue
            if _same_number(value, stated_value):
                found.add(_canon_number(stated_value))
    return found


def _bare_list(text):
    parts = [part.strip() for part in re.split(r"[,،]", text or "") if part.strip()]
    if len(parts) < 3:
        return False
    bare = re.compile(r"^(?:उदाहरण|example)\s*[:：]?\s*[+\-−]?(?:₹)?\d[\d,]*(?:\.\d+)?%?$", re.I)
    number = re.compile(r"^[+\-−]?(?:₹)?\d[\d,]*(?:\.\d+)?%?$")
    return all(bare.match(part) or number.match(part) for part in parts)


def _fact_worked_sentence(topic):
    """A fact that is already a sentence, never a comma list of bare numbers."""
    for raw in (topic or {}).get("facts") or []:
        text = " ".join(str(raw).split()).strip().rstrip("।")
        if not text or not re.search(r"\d", text):
            continue
        if _bare_list(text):
            continue
        if not re.search(r"[=%×x]|का\s", text):
            continue
        if not text.startswith("उदाहरण"):
            text = "उदाहरण: " + text
        return text + "।"
    return ""


def worked_example_sentence(topic):
    """The udaharan line the model should speak verbatim or near-verbatim.

    Built only from the card. A market-data topic does not get a multiplier
    formula; concept topics may show entry − factor × risk when that equals
    the stop already on the card. A bare list such as "उदाहरण: 98, 4000, 200000"
    is never returned. The repair prompt asks for a worked sentence instead.
    """
    if _market_claim(topic):
        return _fact_worked_sentence(topic) or "उदाहरण: दिए गए तथ्य।"
    blob = _topic_blob(topic)
    entry = _labelled_level(blob, ["entry", "एंट्री", "buy"])
    stop = _labelled_level(blob, ["SL", "stop(?:-loss)?", "स्टॉप"])
    target = _labelled_level(blob, ["target", "टारगेट"])
    parts = ["उदाहरण:"]
    if entry is not None and stop is not None:
        risk = abs(entry - stop)
        if _canon_number(risk) in standard_multiplier_numbers():
            parts.append(
                f"एंट्री {_canon_number(entry)}, रिस्क {_canon_number(risk)}, "
                f"एसएल = {_canon_number(entry)} − 1×{_canon_number(risk)} = {_canon_number(stop)}"
            )
        else:
            parts.append(
                f"एंट्री {_canon_number(entry)}, एसएल {_canon_number(stop)}, "
                f"रिस्क {_canon_number(entry)} − {_canon_number(stop)} = {_canon_number(risk)}"
            )
    elif entry is not None:
        parts.append(f"एंट्री {_canon_number(entry)}")
    if entry is not None and target is not None:
        reward = abs(target - entry)
        parts.append(
            f"लक्ष्य {_canon_number(target)}, रिवॉर्ड {_canon_number(target)} − {_canon_number(entry)} = {_canon_number(reward)}"
        )
    qty_match = re.search(r"(\d[\d,]*)\s*शेयर", blob)
    if qty_match and entry is not None and stop is not None and target is not None:
        qty = float(qty_match.group(1).replace(",", ""))
        risk_total = abs(entry - stop) * qty
        reward_total = abs(target - entry) * qty
        parts.append(
            f"{_canon_number(qty)} शेयर पर रिस्क {_canon_number(risk_total)} और रिवॉर्ड {_canon_number(reward_total)}"
        )
    if len(parts) == 1:
        sentence = _fact_worked_sentence(topic)
        if sentence:
            return sentence
        fact = next((str(x).strip().rstrip("।") for x in (topic.get("facts") or []) if str(x).strip()), "")
        if fact and not _bare_list(fact):
            return f"उदाहरण: {fact}।"
        return "उदाहरण: दिए गए तथ्य।"
    return " ".join(parts) + "।"


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

    udaharan and galti may also use a standard multiplier (1, 1.5, 2, 3) or a
    result that the beat's own expression evaluates to. Any other beat may use
    a sum, difference, product, or ratio of two topic numbers when that same
    sentence writes the arithmetic and the result matches. 100−p is allowed
    only when that same sentence writes the subtraction. A loss-recovery pair
    must be L/(1−L). A label does not add a number. Real market data stays on
    the sourced set only: no multiplier and no extra arithmetic.
    """
    card = set(allowed if allowed is not None else allowed_numbers(topic))
    import trade_plan
    if isinstance(topic, dict) and trade_plan.has_plan(topic.get("key")) and (
        trade_plan.is_plan_topic(topic) or topic.get("_approved_bank")
    ):
        try:
            card |= set(trade_plan.number_strings(trade_plan.plan_for(topic.get("key"))))
        except KeyError:
            pass
    sourced = set()
    if allowed is not None:
        sourced = set(allowed) - set(allowed_numbers(topic))
    teaching = teaching_allowed_numbers(topic, sourced=sourced)
    market = _market_claim(topic)
    operands = set(extract_numbers(_topic_blob(topic)))
    extra = set()
    for beat in order:
        text = str((beats or {}).get(beat) or "")
        nums = extract_numbers(text)
        if beat in TEACHING_BEATS and not market:
            ok = set(teaching) | standard_multiplier_numbers()
            ok |= verified_arithmetic_numbers(text, ok)
        elif beat in TEACHING_BEATS:
            ok = set(teaching)
        else:
            ok = set(card)
            if not market:
                ok |= same_sentence_stated_results(text, operands)
        ok |= spelled_complement_numbers(text, topic)
        bad = (nums - ok) | loss_recovery_pair_faults(text, topic)
        extra |= bad
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


def example_card_lines(topic, spoken=""):
    """Scene card: the word उदाहरण plus the figures this beat actually says.

    With no spoken line yet, the card lists the code-computed set. After a
    trim, only numbers still in that beat stay on screen, so the sync gate
    sees the same figures as the narration.
    """
    base = set(teaching_allowed_numbers(topic))
    said = extract_numbers(spoken)
    if said:
        if _market_claim(topic):
            nums = said & base
        else:
            ok = set(base) | standard_multiplier_numbers()
            ok |= verified_arithmetic_numbers(spoken, ok)
            nums = said & ok
    else:
        nums = base

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
