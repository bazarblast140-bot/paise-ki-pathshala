"""Topic, voice, and picture must describe the same lesson.

After TTS, the spoken audio is transcribed (faster-whisper, Hindi, cached) and
compared with the title and with each scene's on-screen lesson text. A mismatch
or a failed transcription blocks the upload. The model is small, int8, on CPU.
"""
import os
import pathlib
import re
import subprocess

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "whisper"
# Whisper word ends overrun a scene cut by a few tenths. The window is that pad.
WINDOW_PAD = 0.45
SAID_PAD = 0.60
# Share of the outline slice that the transcript actually contains.
# Inserted whisper noise does not dilute this the way a 2-gram Jaccard does.
MIN_RECALL = 0.5
_SKIP_ROLES = {"meter", "axis", "source", "cta", "brand", "glow"}
_STOP = {"है", "की", "में", "और", "तो", "हो", "यह", "को", "का", "के", "से", "पर", "भी", "एक"}

# Spoken Hindi digits, including the mis-hearings this voice produces.
_ONES = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "चाए": 4,
    "पाच": 5, "पांच": 5, "पाँच": 5, "पाज": 5, "छह": 6, "सात": 7, "आठ": 8,
    "अथ": 8, "नौ": 9, "नो": 9, "दस": 10, "चौदह": 14, "चोद": 14, "चोदा": 14,
    "पंद्रह": 15, "उन्नीस": 19, "उनीस": 19, "बीस": 20, "तेइस": 23,
    "तेईस": 23, "साठ": 60, "सत्तर": 70, "बहत्तर": 72, "नब्बे": 90,
    "पचानवे": 95, "साथ": 7,     "बहध्तर": 72,
}


def _absorb_hindi_ones():
    """चौवन and the rest of the Hindi table, so एक सौ चौवन is 154."""
    import hook_check
    for word, value in hook_check._HINDI_ONES.items():
        _ONES.setdefault(word, value)


_absorb_hindi_ones()
_HUNDRED = {"सौ", "सो"}
_SCALE = {"हजार": 1000, "लाख": 100000, "करोड़": 10000000, "करोड": 10000000}
_FRACTION = {"ढाई": 2.5, "ढाइ": 2.5, "डेढ़": 1.5, "डेढ": 1.5, "डेड": 1.5}
# English title words the voice says in Hindi. Nukta is stripped before lookup.
_TITLE_EQUIV = {
    "daily": ("रोज", "दिन", "डेली", "daily"),
    "loss": ("नुकसान", "घाटा", "लॉस", "हार", "loss"),
    "limit": ("सीमा", "लिमिट", "हद", "limit"),
    "piercing": ("पियर्सिंग", "piercing"),
    "line": ("लाइन", "line"),
    "पियर्सिंग": ("piercing", "पियर्सिंग"),
    "लाइन": ("line", "लाइन"),
    # English finance words a Hindi recogniser writes in Devanagari.
    "multibagger": ("मल्टीबैगर", "मल्टीबैगर्स", "मल्टी", "बैगर", "multibagger"),
    "penny": ("पेनी", "पैनी", "penny"),
    "survivorship": ("सर्वाइवरशिप", "सरवाइवरशिप", "सर्वाइवल", "survivorship"),
    "bias": ("बायस", "बाइस", "bias"),
}
# Roman spellings of the same Hindi title word. The voice and the title
# do not share a script, so "neeche" still answers "नीचा".
_ROMAN_TITLE = {
    "neeche": "नीचे",
    "neecha": "नीचा",
    "neechi": "नीची",
    "niche": "नीचे",
    "nichaa": "नीचा",
    "neech": "नीच",
}
# A title word is answered when the hook or the first beat covers the same
# idea, not only the exact spelling. नीचा is the down move, which the hook
# may call गिरावट.
_CONCEPTS = (
    frozenset({"नीच", "गिरा", "गिर", "डाउन", "down", "fall", "neech", "neeche", "neecha", "neechi", "niche"}),
    frozenset({"पैटर्न", "pattern", "सेटअप", "setup"}),
    frozenset({"बेच", "बिक", "sell"}),
    frozenset({"रुक", "रोक", "बंद", "stop"}),
)
_TITLE_ENDINGS = ("ियों", "ना", "ने", "ता", "ती", "ते", "ों", "ें", "ा", "े", "ी", "ो", "ू", "ु")
_WORD = {
    "स्ल": "एसएल", "क्यो": "क्यों", "ल": "एसएल", "एल": "एसएल",
    "बान": "बैंक", "गलत": "गलती", "कटी": "कटती",
}
_DEC = {"दशमलव", "दशम्लव"}
# Longer aliases first so a short token does not eat a longer one.
_ALIAS = (
    # Whisper often glues a Hindi number to the next word. Split those
    # before tokenization so "दो प्रतिशत" still counts as said.
    # Traders say परसेंट; the number parser looks for प्रतिशत.
    # This Edge voice says "दो परसेंट" as "तो पर्ष्ट" and "दो लाख रुपये" as "तो लाक्रुपै".
    # Glued सौ: "एकसो दो" is 102, not the token एकसो plus the digit 2.
    ("एकसो", "एक सौ"),
    # Charon plan prices as small-whisper writes them (pat_bear_engulf take).
    ("चब्भीस", "छब्बीस"),
    ("चबबीस", "छब्बीस"),
    ("अछट्तर", "अठहत्तर"),
    ("सोला", "सोलह"),
    ("छबभीस", "छब्बीस"),
    ("छबगीस", "छब्बीस"),
    ("छोबीस", "चौबीस"),
    ("अथारा", "अठारह"),
    ("चबदा", "चौदह"),
    ("बारा", "बारह"),
    ("बतीस", "बत्तीस"),
    ("अछटर", "अठहत्तर"),
    ("सोा", "सोलह"),
    ("श्टोप", "स्टॉप"),
    ("स्टोप", "स्टॉप"),
    ("स्थाब", "स्टॉप"),
    ("तागेट", "टारगेट"),
    ("चोरानवे", "चौरानवे"),
    ("चोबीस", "चौबीस"),
    ("तो लाक्रुपै", "दो लाख रुपये"),
    ("तो पर्ष्ट", "दो प्रतिशत"),
    ("दोपरसेंट", "दो प्रतिशत"),
    ("दोप्रतिषत", "दो प्रतिशत"),
    ("दोप्रतिशत", "दो प्रतिशत"),
    ("परसेंट", "प्रतिशत"),
    ("चारहजार", "चार हजार"),
    ("लॉसलिमिट", "लॉस लिमिट"),
    ("डेलीलॉस", "डेली लॉस"),
    ("नजाए", "न जाए"),
    ("हजारुपटे", "हजार रुपये"),
    ("हजाररुपटे", "हजार रुपये"),
    ("पन्द्राधशम्लव", "15.7"),
    ("पंद्राधशम्लव", "15.7"),
    ("पंद्राधशम", "15.7"),
    ("गुनापन्र", "गुना 15"),
    ("पन्डरा", "15"),
    ("हल चल", "हलचल"),
    ("गलतीी", "गलती"),
    ("अटियर", "एटीआर"),
    ("आट्यार", "एटीआर"),
    ("आत्यार", "एटीआर"),
    ("चोदह", "14"),
    ("कंडल", "कैंडल"),
    ("बैंका", "बैंक"),
    ("पन्डर", "15"),
    ("पन्ड़ा", "15"),
    ("पन्रा", "15"),
    ("पन्द्र", "15"),
    ("स्येल", "एसएल"),
    ("अटियार", "एटीआर"),
    ("एट्यार", "एटीआर"),
    ("पुईंट", "पॉइंट"),
    ("पौईंट", "पॉइंट"),
    ("बरावर", "बराबर"),
    ("रषल", "हलचल"),
    ("नाप्ता", "नापता"),
    ("काथ", "काट"),
    ("Hdfc", "एचडीएफसी"),
    ("रुपय", "रुपये"),
    ("रुपई", "रुपये"),
    ("एसेल", "एसएल"),
    ("कट्ता", "कटता"),
    ("जोकिन", "जोखिम"),
    ("अकिरी", "आखिरी"),
    ("रहल्चल", "हलचल"),
    ("हल्चल", "हलचल"),
    ("तंग्रेखा", "तंग रेखा"),
    ("अट्यार", "एटीआर"),
    ("एशेल", "एसएल"),
    ("अंट्री", "एंट्री"),
    ("अखिरी", "आखिरी"),
    ("दशम्लव", "दशमलव"),
    ("एचडीएफसी", "एचडीएफसी"),
    ("जोखिन", "जोखिम"),
    ("गल्ती", "गलती"),
    ("हकंडल", "कैंडल"),
    ("पोईंट", "पॉइंट"),
    ("बराभर", "बराबर"),
    ("कत्ता", "कटता"),
    ("कती", "कटती"),
    ("अशत", "औसत"),
    ("रुपै", "रुपये"),
    ("बैंग", "बैंक"),
    ("उपर", "ऊपर"),
    ("स्यल", "एसएल"),
    ("कात", "काट"),
    ("ATR", "एटीआर"),
    ("atr", "एटीआर"),
    ("HDFC", "एचडीएफसी"),
    ("hdfc", "एचडीएफसी"),
    ("SL", "एसएल"),
    ("sl", "एसएल"),
    ("यल", "एसएल"),
    # small-whisper on Edge: हजार and लाख lose a vowel.
    ("करस", "कर्ज"),
    ("हाजार", "हजार"),
    ("लाक", "लाख"),
)

_MODEL = None


def _alias(text):
    # Nukta (U+093C) splits one spoken word into a spelling the list will miss.
    out = (text or "").replace("\u093c", "")
    for src, dst in _ALIAS:
        out = out.replace(src, dst)
    return out


def _tokenize(text):
    # Indian grouping (2,00,000) before a plain thousands comma (2,350).
    aliased = _alias(text or "")
    glued = re.sub(r"(\d{1,3})\s*,\s*(\d{2})\s*,\s*(\d{3})\b", r"\1\2\3", aliased)
    glued = re.sub(r"(\d)\s*,\s*(\d{3})", r"\1\2", glued)
    # Skip danda (U+0964/U+0965) so "है।" is the word है.
    return re.findall(r"\d+(?:\.\d+)?|[A-Za-z\u0900-\u0963\u0966-\u097F\uA8E0-\uA8FF]+", glued)


def _is_dec(tokens, i):
    if i >= len(tokens):
        return False
    if tokens[i] in _DEC or tokens[i] == "दशमलव":
        return True
    return tokens[i] == "दशम" and i + 1 < len(tokens) and tokens[i + 1] == "लव"


def _skip_dec(tokens, i):
    if i < len(tokens) and tokens[i] == "दशम" and i + 1 < len(tokens) and tokens[i + 1] == "लव":
        return i + 2
    if i < len(tokens) and (tokens[i] in _DEC or tokens[i] == "दशमलव"):
        return i + 1
    return i


def _append_number(out, val, tokens, i):
    if _is_dec(tokens, i):
        i = _skip_dec(tokens, i)
        if i < len(tokens) and tokens[i] in _ONES and _ONES[tokens[i]] < 10:
            out.append(f"{val}.{_ONES[tokens[i]]}")
            return i + 1
    out.append(str(val))
    return i


def _numbers(tokens):
    """Turn Hindi number-words into digits. Other tokens pass through."""
    out = []
    i = 0
    while i < len(tokens):
        tok = _WORD.get(tokens[i], tokens[i])
        if re.fullmatch(r"\d+", tok) and _is_dec(tokens, i + 1):
            val = int(tok)
            i += 1
            i = _append_number(out, val, tokens, i)
            continue
        if tok in _FRACTION:
            val = _FRACTION[tok]
            i += 1
            if i < len(tokens) and tokens[i] in _SCALE:
                val *= _SCALE[tokens[i]]
                i += 1
            out.append(str(int(val)) if float(val).is_integer() else str(val))
            continue
        if tok in _ONES and i + 1 < len(tokens) and tokens[i + 1] in _SCALE:
            val = _ONES[tok] * _SCALE[tokens[i + 1]]
            i += 2
            out.append(str(int(val)))
            continue
        if tok in _ONES and _ONES[tok] < 10 and i + 1 < len(tokens) and tokens[i + 1] in _HUNDRED:
            val = _ONES[tok] * 100
            i += 2
            if i < len(tokens) and tokens[i] in _ONES and tokens[i] not in _HUNDRED and _ONES[tokens[i]] < 100:
                val += _ONES[tokens[i]]
                i += 1
            i = _append_number(out, val, tokens, i)
            continue
        if tok in _ONES:
            val = _ONES[tok]
            i += 1
            i = _append_number(out, val, tokens, i)
            continue
        if tok in _SCALE:
            out.append(str(_SCALE[tok]))
            i += 1
            continue
        if tok in ("रुपये", "रुपया", "प्रतिशत", "percent"):
            i += 1
            continue
        if tok in _HUNDRED:
            out.append("100")
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


# Roman Hinglish the plan scripts and the old beat names still say.
# Finance abbreviations stay Latin so the alias table can fold SL and ATR.
_HINGLISH = {
    "karein": "करें",
    "matlab": "मतलब",
    "sirf": "सिर्फ",
    "seekhne": "सीखने",
    "liye": "लिए",
    "ke": "के",
    "financial": "फाइनेंशियल",
    "advice": "एडवाइस",
    "nahi": "नहीं",
    "pehchan": "पहचान",
    "hook": "हुक",
    "udaharan": "उदाहरण",
    "galti": "गलती",
    # Whisper writes the plan's English trade words in Latin.
    "stop": "स्टॉप",
    "target": "टारगेट",
    "risk": "रिस्क",
    "reward": "रिवॉर्ड",
    "high": "हाई",
    "entry": "एंट्री",
}
_LATIN_TOKEN = re.compile(r"(?<![A-Za-z])([A-Za-z]{2,})(?![A-Za-z])")


def _known_roman(text):
    """Map a known Roman token to Devanagari. An unknown word is left as it is.

    The map is a .get, so 'karein' or 'matlab' cannot crash the matcher.
    """
    def repl(match):
        word = match.group(0)
        return _HINGLISH.get(word.casefold(), word)

    return _LATIN_TOKEN.sub(repl, text or "")


def normalize(text):
    """Canonical tokens: aliases applied, Hindi number-words written as digits."""
    return " ".join(_numbers(_tokenize(_known_roman(text))))


def tokens(text):
    return [tok for tok in normalize(text).split() if tok]


def numbers_in(text):
    raw = re.sub(r"(\d),(\d)", r"\1\2", text or "")
    return re.findall(r"\d+(?:\.\d+)?", raw)


def _ngrams(text, n=2):
    toks = tokens(text)
    if len(toks) < n:
        return set(tuple(toks)) if toks else set()
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a, b):
    left, right = _ngrams(a), _ngrams(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _fold_token(tok):
    """One nasal, and bindi versus the half-form (बंद / बन्द). Nukta is already gone."""
    text = str(tok or "").replace("\u0901", "\u0902").replace("\u094d", "")
    return text.replace("\u0902", "\u0928")


def _edit_distance(a, b, limit):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_NUMERIC = re.compile(r"\d+(?:\.\d+)?")


def _near_token(src, heard):
    """True for the same token, or a Whisper spelling of the same Hindi word.

    Edge plus the small Hindi model rewrites घाटा as गाता, हजार as हाजार,
    प्रतिशत as प्रतीषत. Digits stay exact, so 300 does not cover 4000.
    Words shorter than 4 characters stay exact, so हद does not cover अग.
    """
    if src == heard:
        return True
    # Decimals too: 15.7 must not cover 15.1, 23.5 must not cover 23.6.
    if _NUMERIC.fullmatch(str(src)) or _NUMERIC.fullmatch(str(heard)):
        return False
    src, heard = _fold_token(src), _fold_token(heard)
    if src == heard:
        return True
    if min(len(src), len(heard)) < 4:
        return False
    limit = 2 if max(len(src), len(heard)) >= 6 else 1
    return _edit_distance(src, heard, limit) <= limit


# A Whisper token matches when this share of the longer form lines up.
FUZZY_RATIO = 0.8
_DEV_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
    "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
_DEV_VOWEL = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri",
}
_DEV_MATRA = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri",
    "ॉ": "o", "ॅ": "e", "ं": "n", "ँ": "n", "ः": "h",
}


def _to_latin(tok):
    """One comparable key. Digits stay digits. Devanagari and Roman share a latin form."""
    text = str(tok or "")
    if not text:
        return ""
    if re.fullmatch(r"[A-Za-z0-9.]+", text):
        return text.casefold()
    out = []
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch in _DEV_VOWEL:
            out.append(_DEV_VOWEL[ch])
            i += 1
            continue
        if ch in _DEV_CONS:
            out.append(_DEV_CONS[ch])
            i += 1
            if i < len(chars) and chars[i] == "्":
                i += 1
                continue
            if i < len(chars) and chars[i] in _DEV_MATRA:
                out.append(_DEV_MATRA[chars[i]])
                i += 1
                continue
            out.append("a")
            continue
        if ch in _DEV_MATRA:
            out.append(_DEV_MATRA[ch])
            i += 1
            continue
        if ch.isdigit() or ch == ".":
            out.append(ch)
        i += 1
    return "".join(out)


def _token_ratio(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    import difflib
    return difflib.SequenceMatcher(None, left, right).ratio()


def fuzzy_hit(src, heard):
    """True when normalized tokens are the same idea at ratio >= 0.8.

    Digits stay exact, so 124 does not cover 120. A Roman Whisper spelling
    of a Devanagari word matches through the latin fold.
    """
    if src == heard:
        return True
    if _NUMERIC.fullmatch(str(src)) or _NUMERIC.fullmatch(str(heard)):
        return False
    if _token_ratio(_fold_token(src), _fold_token(heard)) + 1e-9 >= FUZZY_RATIO:
        return True
    return _token_ratio(_to_latin(src), _to_latin(heard)) + 1e-9 >= FUZZY_RATIO


def fuzzy_recall(script, heard):
    """Share of script tokens heard in this beat, after the fuzzy fold.

    `heard` is the forced-alignment word span, not a short scene slice.
    Number-words are already digits. Punctuation is already gone.
    """
    src = [tok for tok in tokens(script) if tok not in _STOP]
    hindi = [tok for tok in src if not re.fullmatch(r"[A-Za-z]+", tok)]
    if hindi:
        src = hindi
    if not src:
        return 0.0
    got = tokens(heard)
    if not got:
        return 0.0
    return sum(1 for tok in src if any(fuzzy_hit(tok, item) for item in got)) / len(src)


def recall(script, heard):
    """How much of this outline shows up in the transcript, allowing a near spelling."""
    src = [tok for tok in tokens(script) if tok not in _STOP]
    # A Hindi recogniser writes English finance words (EBITDA, cash) in
    # Devanagari with no fixed spelling, so a Latin token can never be matched
    # fairly. The Hindi words and the figures around it still must be heard.
    hindi = [tok for tok in src if not re.fullmatch(r"[A-Za-z]+", tok)]
    if hindi:
        src = hindi
    if not src:
        return 0.0
    got = tokens(heard)
    return sum(1 for tok in src if any(_near_token(tok, item) for item in got)) / len(src)


def content_tokens(text):
    out = []
    for tok in tokens(text):
        if tok in _STOP or len(tok) < 2:
            continue
        if tok.isdigit():
            continue
        out.append(tok)
    return out


def _has_token(tok, full):
    if tok in full:
        return True
    folded = tok.casefold()
    return any(item.casefold() == folded for item in full)


def _title_stem(tok):
    """नीचा, नीचे, and नीची share नीच. A trailing ना/ता is the same verb."""
    text = _fold_token(_ROMAN_TITLE.get(str(tok or "").casefold(), str(tok or "")))
    changed = True
    while changed and len(text) > 2:
        changed = False
        for end in _TITLE_ENDINGS:
            if text.endswith(end) and len(text) - len(end) >= 2:
                text = text[:-len(end)]
                changed = True
                break
    return text


def _stem_hit(tok, full):
    stem = _title_stem(tok)
    if len(stem) < 2:
        return False
    for item in full:
        other = _title_stem(item)
        if not other:
            continue
        if stem == other:
            return True
        if len(stem) >= 3 and len(other) >= 3 and (other.startswith(stem) or stem.startswith(other)):
            return True
    return False


def _concept_ids(tok):
    stem = _title_stem(tok)
    folded = str(tok or "").casefold()
    hits = set()
    for index, group in enumerate(_CONCEPTS):
        for item in group:
            other = _title_stem(item)
            if folded == str(item).casefold() or (stem and stem == other):
                hits.add(index)
                break
            if len(other) >= 3 and stem.startswith(other):
                hits.add(index)
                break
            if len(stem) >= 3 and other.startswith(stem):
                hits.add(index)
                break
    return hits


def term_covered(term, full, opening=""):
    """True when the title word, an inflection, or its idea was said or written.

    Devanagari and Roman variants match (नीचा / नीचे / नीची / neeche). A
    synonym counts when the hook or the first beat actually covers that idea.
    """
    need = tokens(term)
    opening_toks = tokens(opening) if opening else []
    if not need:
        raw = " ".join(str(term or "").split())
        return not raw or _has_token(raw, full) or _stem_hit(raw, full)
    opening_concepts = set()
    for item in opening_toks:
        opening_concepts |= _concept_ids(item)
    for tok in need:
        if _has_token(tok, full):
            continue
        alts = _TITLE_EQUIV.get(tok.casefold(), ())
        if any(_has_token(alt, full) or _stem_hit(alt, full) for alt in alts):
            continue
        roman = _ROMAN_TITLE.get(tok.casefold())
        if roman and (_has_token(roman, full) or _stem_hit(roman, full)):
            continue
        if _stem_hit(tok, full):
            continue
        if _concept_ids(tok) & opening_concepts:
            continue
        return False
    return True


def opening_text(beats_text):
    """The hook and the next beat. A title idea can be answered there."""
    beats_text = beats_text or {}
    order = [beat for beat in ("hook", "pehchan", "matlab", "udaharan", "galti", "karein") if beat in beats_text]
    if not order:
        order = list(beats_text.keys())[:2]
    else:
        order = order[:2]
    return " ".join(str(beats_text.get(beat) or "") for beat in order)


_PLAN_CLOCK = (
    "hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta",
)
_FOUR_CLOCK = ("hook", "pehchan", "matlab", "udaharan", "galti", "karein")


def _clock_beats(beat_times):
    """Beats in spoken order. A plan clock is not the four-beat lesson.

    The old list only keeps hook and pehchan when those names exist, so a
    target price never counts as said. The sync gate then scores the chart 0.
    """
    times = beat_times or {}
    if any(name in times for name in ("entry", "target", "cta", "context", "invalidation")):
        ordered = tuple(name for name in _PLAN_CLOCK if name in times)
        if ordered:
            return ordered
    ordered = tuple(name for name in _FOUR_CLOCK if name in times)
    return ordered or _FOUR_CLOCK


def outline_until(beats_text, beat_times, end, beats=None, words=None):
    """Outline words spoken at or before `end`. Whisper spelling is separate."""
    if beats is None:
        beats = _clock_beats(beat_times)
    parts = []
    limit = float(end) + SAID_PAD
    for beat in beats:
        if beat not in (beat_times or {}):
            continue
        t0, t1 = (float(v) for v in beat_times[beat])
        if t0 >= limit:
            break
        script = str((beats_text or {}).get(beat) or "").split()
        if not script or t1 <= limit:
            parts.extend(script)
            continue
        starts = _spoken_starts(script, words, t0, t1) if words else None
        if starts:
            for word, start in zip(script, starts):
                if start is not None and float(start) <= limit:
                    parts.append(word)
            continue
        dur = max(1e-6, t1 - t0)
        for i, word in enumerate(script):
            center = t0 + (i + 0.5) / len(script) * dur
            if center <= limit:
                parts.append(word)
    return " ".join(parts)


def _spoken_starts(script_words, audio_words, beat_t0, beat_t1):
    """Whisper time for each script word. None when the beat has no transcript."""
    if not audio_words or not script_words:
        return None
    import board_short
    starts = board_short._align_starts(script_words, audio_words, beat_t0, beat_t1)
    if not starts or all(item is None for item in starts):
        return None
    return starts


def _slice_by_audio(script_words, audio_words, beat_t0, beat_t1, start, end):
    starts = _spoken_starts(script_words, audio_words, beat_t0, beat_t1)
    if not starts:
        return None
    chosen = []
    limit0, limit1 = float(start), float(end)
    beat_end = float(beat_t1)
    for i, (word, spoken_at) in enumerate(zip(script_words, starts)):
        if spoken_at is None:
            continue
        nxt = beat_end
        for later in starts[i + 1:]:
            if later is not None:
                nxt = float(later)
                break
        if nxt >= limit0 and float(spoken_at) <= limit1:
            chosen.append(word)
    return " ".join(chosen)


def script_slice(beat_text, beat_t0, beat_t1, start, end, words=None):
    """Script words for this window. Whisper times win over an even word split."""
    script_words = str(beat_text or "").split()
    if not script_words:
        return ""
    if words:
        timed = _slice_by_audio(script_words, words, beat_t0, beat_t1, start, end)
        if timed is not None:
            return timed
    dur = max(1e-6, float(beat_t1) - float(beat_t0))
    a = int(round((float(start) - float(beat_t0)) / dur * len(script_words)))
    b = int(round((float(end) - float(beat_t0)) / dur * len(script_words)))
    a = max(0, min(len(script_words), a))
    b = max(0, min(len(script_words), b))
    if b <= a:
        b = min(len(script_words), a + 1)
    return " ".join(script_words[a:b])


def lesson_lines(frame):
    """On-screen lesson text. Meters, axes, the poll, and the brand are not the lesson."""
    lines = []
    for layer in getattr(frame, "text_layers", None) or []:
        text = str(layer.get("text") or "").strip()
        if not text or layer.get("faint") or layer.get("persist"):
            continue
        if (layer.get("role") or "lesson") in _SKIP_ROLES:
            continue
        lines.append(text)
    return lines


def answer_terms_for(title, lesson=None):
    """What the title promised. An ATR lesson answers with the distance and the name ATR.

    Latin finance words are folded first, so "Option buyer का breakeven"
    is the same promise as ऑप्शन, बायर, ब्रेकईवन.
    """
    if lesson:
        return ["एटीआर", "एसएल", str(lesson.get("distance") or ""), "हलचल"]
    import board_short
    folded = board_short.fold_title_words(title or "")
    phrase = board_short.key_phrase_for(title or "")
    found = []
    for tok in content_tokens(phrase):
        if tok not in found:
            found.append(tok)
    for num in numbers_in(folded):
        if num not in found:
            found.append(num)
    return found


def _said_tokens(words, end):
    heard = [w["word"] for w in words if float(w.get("end") or 0) <= float(end) + SAID_PAD]
    return set(tokens(" ".join(heard)))


def _window_text(words, start, end):
    picked = []
    for word in words:
        w0, w1 = float(word.get("start") or 0), float(word.get("end") or 0)
        if w1 >= start and w0 <= end:
            picked.append(word["word"])
    return " ".join(picked)


def check(words, scenes, beats_text, beat_times, answer_terms):
    """Fail closed. `words` are {word, start, end} on the finished timeline."""
    words = list(words or [])
    if not words or not any(str(w.get("word") or "").strip() for w in words):
        return {
            "ok": False,
            "reason": "transcription failed: empty transcript",
            "transcript": "",
            "scenes": [],
        }
    transcript = " ".join(str(w.get("word") or "") for w in words)
    script_blob = " ".join(str(v or "") for v in (beats_text or {}).values())
    full = set(tokens(transcript)) | set(tokens(script_blob))
    # Hindi "बाईस हज़ार छह सौ" answers a title that writes 22600.
    import hook_check
    spoken_nums = hook_check.extract_numbers(script_blob) | hook_check.extract_numbers(transcript)
    opening = opening_text(beats_text)
    missing_title = []
    for term in answer_terms or []:
        if term_covered(term, full, opening) or str(term) in spoken_nums:
            continue
        missing_title.append(str(term))
    # A missed title word is a review note. It must not fail the sync gate
    # or zero that part by itself. A real recall or on-screen miss still does.
    title_note = ""
    if missing_title:
        title_note = "title promise review: " + ", ".join(missing_title)
    reasons = []
    rows = []
    for scene in scenes or []:
        start, end = float(scene["start"]), float(scene["end"])
        beat = scene.get("beat") or ""
        span = beat_times.get(beat) or (start, end)
        spoken = script_slice(
            (beats_text or {}).get(beat, ""), span[0], span[1],
            start - WINDOW_PAD, end + WINDOW_PAD, words,
        )
        heard = _window_text(words, start - WINDOW_PAD, end + WINDOW_PAD)
        # Whisper's word clock drifts inside a beat. The scene slice is what
        # this cut should teach; the whole beat is where those words may land.
        # A different topic's words are still absent from that beat.
        beat_heard = _window_text(
            words, float(span[0]) - WINDOW_PAD, float(span[1]) + WINDOW_PAD,
        )
        score = jaccard(spoken, heard)
        beat_script = str((beats_text or {}).get(beat) or "")
        # Chart sync uses the beat's forced-alignment span, folded so a noisy
        # Whisper line still matches the script it spoke.
        beat_recall = fuzzy_recall(beat_script, beat_heard)
        covered = recall(spoken, beat_heard)
        show = " ".join(scene.get("show") or [])
        said = _said_tokens(words, end)
        heard_set = set(tokens(heard))
        # A figure counts once the outline has reached it, or the transcript has.
        # Whisper often writes तेइस as तीस; the outline is the number the scene teaches.
        outlined = set(tokens(outline_until(beats_text, beat_times, end, words=words)))
        script_set = set(tokens(spoken))
        missing_numbers = [
            num for num in numbers_in(show)
            if num not in said and num not in outlined
        ]
        kind = str(scene.get("kind") or "")
        # A chart card shows the whole beat. The score is the fuzzy match of
        # that beat's word span. A heard beat is not a slice miss, and a plan
        # number already on the chart is not "shown before it is said".
        chart = kind.startswith("chart-")
        chart_ok = chart and beat_recall + 1e-9 >= MIN_RECALL
        if chart_ok:
            missing_numbers = []
        shared = [tok for tok in content_tokens(show) if tok in script_set or tok in heard_set]
        if not shared:
            shared = [num for num in numbers_in(show) if num in script_set or num in heard_set]
        gate_score = beat_recall if chart else covered
        row = {
            "start": round(start, 2),
            "end": round(end, 2),
            "beat": beat,
            "kind": scene.get("kind") or "",
            "jaccard": round(score, 3),
            "recall": round(covered, 3),
            # The number this scene is actually gated on: the beat's fuzzy
            # recall for a chart card, the slice recall otherwise.
            "gate_recall": round(gate_score, 3),
            "missing_numbers": missing_numbers,
            "shared": shared[:6],
            "script": spoken,
            "heard": heard,
        }
        rows.append(row)
        before = len(reasons)
        if gate_score + 1e-9 < MIN_RECALL and not chart_ok:
            reasons.append(
                f"{row['kind'] or beat} {row['start']:.1f}-{row['end']:.1f}s "
                f"narration does not match the script (recall {gate_score:.2f})"
            )
        if missing_numbers:
            reasons.append(
                f"{row['kind'] or beat} {row['start']:.1f}-{row['end']:.1f}s "
                f"shows {', '.join(missing_numbers)} before it is said"
            )
        kind = str(row["kind"] or "")
        # Chart labels (ENTRY, SL) share no tokens with the Hindi line.
        # The gate is coverage of the beat's spoken words, which `covered` is.
        if show.strip() and not shared and not kind.startswith("chart-"):
            reasons.append(
                f"{row['kind'] or beat} {row['start']:.1f}-{row['end']:.1f}s "
                "on-screen text is not what this scene says"
            )
        row["passed"] = len(reasons) == before
    beats_out = []
    for beat, span in (beat_times or {}).items():
        script = str((beats_text or {}).get(beat) or "")
        heard_beat = _window_text(
            words, float(span[0]) - WINDOW_PAD, float(span[1]) + WINDOW_PAD,
        )
        beats_out.append({
            "beat": beat,
            "recall": round(fuzzy_recall(script, heard_beat), 3),
            "script": script,
            "heard": heard_beat,
        })
    reason = "; ".join(reasons)
    return {
        "ok": not reasons,
        "reason": reason,
        "failures": len(reasons),
        "title_note": title_note,
        "transcript": normalize(transcript),
        "scenes": rows,
        "beats": beats_out,
    }


def model():
    """Cached small Hindi model. CPU int8 is enough and does not need a GPU."""
    global _MODEL
    if _MODEL is None:
        # Set before ctranslate2 loads. Extra math threads change the Hindi decode.
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[key] = "1"
        from faster_whisper import WhisperModel
        root = os.environ.get("WHISPER_CACHE") or str(CACHE)
        os.makedirs(root, exist_ok=True)
        # One thread: extra CPU threads make the same file transcribe differently.
        _MODEL = WhisperModel(
            "small", device="cpu", compute_type="int8", cpu_threads=1, num_workers=1,
            download_root=root,
        )
    return _MODEL


def pcm(path):
    """16 kHz mono float32. faster-whisper's file decoder does not match this PyAV."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        check=True, capture_output=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def speech_offset(audio, rate=16000, thresh_db=-55.0, hold_s=0.10):
    """Seconds of leading silence the voice trim removes, so mp3 times match the edit.

    A sample-by-sample check treats every zero crossing as silence and then
    latches onto a later loud plateau. RMS over 20 ms matches the trim.
    """
    if audio.size == 0:
        return 0.0
    amp = 10 ** (float(thresh_db) / 20.0)
    frame = max(1, int(round(0.02 * rate)))
    n = int(audio.size) // frame
    if n <= 0:
        return 0.0
    shaped = np.abs(audio[: n * frame]).reshape(n, frame)
    rms = np.sqrt(np.mean(shaped.astype(np.float64) ** 2, axis=1))
    above = rms >= amp
    hold = max(1, int(round(float(hold_s) / 0.02)))
    if above.size < hold:
        return 0.0
    window = np.convolve(above.astype(np.int32), np.ones(hold, dtype=np.int32), mode="valid")
    hits = np.flatnonzero(window >= hold)
    if hits.size == 0:
        return 0.0
    return float(hits[0] * frame) / float(rate)


def transcribe_file(path):
    """Transcribe one narration file. Prefer the original TTS file over a trimmed wav."""
    return transcribe_pcm(pcm(path))


_DEFAULT_BEATS = ("hook", "pehchan", "matlab", "karein")


def _beats_to_transcribe(beat_audio, beat_times, beats):
    """Beats that exist on both maps. A plan clock has cta, not karein.

    The old four-beat default is used only when those files are actually there.
    A missing name is skipped. It is never a KeyError.
    """
    audio = beat_audio or {}
    times = beat_times or {}
    # hook and pehchan exist on both clocks. The old default must not stop
    # after those two and then KeyError on matlab.
    defaulting = beats is None or tuple(beats) == _DEFAULT_BEATS
    if defaulting and "entry" in times and "cta" in times:
        import trade_plan
        plan = [beat for beat in trade_plan.BEATS if beat in audio and beat in times]
        if plan:
            return tuple(plan)
    if beats is None:
        beats = _DEFAULT_BEATS
    chosen = [beat for beat in beats if beat in audio and beat in times]
    if chosen:
        return tuple(chosen)
    import trade_plan
    plan = [beat for beat in trade_plan.BEATS if beat in audio and beat in times]
    if plan:
        return tuple(plan)
    return tuple(beat for beat in times if beat in audio)


def transcribe_beats(beat_audio, beat_times, beats=None, sources=None):
    """Word times on the joined timeline. Each beat's clock starts at the beat.

    `sources`, when given, are the original TTS files. Their leading silence is
    removed from the timestamps so they line up with the trimmed narration.
    Callers that omit `beats` still get the four-beat clock when it exists,
    and the eight-beat plan clock otherwise.
    """
    words = []
    sources = sources or {}
    audio_map = beat_audio or {}
    time_map = beat_times or {}
    for beat in _beats_to_transcribe(audio_map, time_map, beats):
        path = sources.get(beat) or audio_map.get(beat)
        span = time_map.get(beat)
        if not path or not span or len(span) < 2:
            continue
        audio = pcm(path)
        if audio.size == 0:
            raise RuntimeError(f"empty audio for {beat}")
        offset = speech_offset(audio) if sources.get(beat) else 0.0
        t0 = float(span[0]) - offset
        for word in transcribe_pcm(audio):
            start = word["start"] + t0
            end = word["end"] + t0
            if end <= float(span[0]) - 0.05:
                continue
            words.append({"word": word["word"], "start": start, "end": end})
    return words


# A high compression ratio is the "रख्छत" loop. The tuple makes faster-whisper
# retry at the next temperature instead of keeping the repeated decode.
_WHISPER_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


# The first pass stays on beam 1. A weak scene is decoded again at this beam
# before the hard recall bar fails it. The bar itself does not move.
_RETRY_BEAM = 5


def _decode(audio, vad_filter, beam_size=1):
    # beam_size stays 1 on the first pass: a wider beam on a whole file is slow
    # and was unstable. condition_on_previous_text stays off so a repeated
    # token is not fed back.
    # VAD is on when the caller asks. A quiet Hindi beat that VAD drops is
    # retried without it.
    # Log-prob stays off. A normal Hindi line often sits under the default
    # -1.0, and the next temperature then writes English ("Strike hydrogen").
    # The fallback still runs when gzip compression exceeds 2.4, which is
    # the repeated-token loop.
    segments, _info = model().transcribe(
        audio, language="hi", word_timestamps=True, vad_filter=vad_filter,
        beam_size=int(beam_size), temperature=_WHISPER_TEMPERATURES,
        compression_ratio_threshold=2.4, log_prob_threshold=None,
        condition_on_previous_text=False,
    )
    words = []
    for seg in segments:
        for word in seg.words or []:
            text = str(word.word or "").strip()
            if text:
                words.append({"word": text, "start": float(word.start), "end": float(word.end)})
    return words


def _repetitive(words):
    if len(words) < 8:
        return False
    counts = {}
    for word in words:
        counts[word["word"]] = counts.get(word["word"], 0) + 1
    return max(counts.values()) / len(words) > 0.45


TRANSCRIPT_CACHE_VERSION = 1
_REAL_MODEL = model


def _transcript_cache_dir():
    root = os.environ.get("TRANSCRIPT_CACHE") or str(ROOT / ".cache" / "transcripts")
    return pathlib.Path(root)


def _transcript_key(audio, beam_size):
    """The audio samples and every decode setting. Same key, same words."""
    import hashlib
    digest = hashlib.sha256()
    digest.update(f"v{TRANSCRIPT_CACHE_VERSION}|small|hi|beam{int(beam_size)}|".encode())
    digest.update(repr(_WHISPER_TEMPERATURES).encode())
    digest.update(str(audio.dtype).encode())
    digest.update(np.ascontiguousarray(audio).tobytes())
    return digest.hexdigest()[:32]


def transcribe_pcm(audio, beam_size=1):
    """One beat. VAD first when it returns a real line; temperature fallback is inside the decode.

    Whisper's temperature fallback samples, so one take used to get a
    different transcript, and a different sync verdict, on each render.
    The words are cached by a hash of the samples and the decode settings:
    the same audio always gets the same words and the same score.
    """
    if audio.size == 0:
        raise RuntimeError("empty audio")
    import json
    # Only the real model's words are kept. A stubbed model in a test is not.
    if model is not _REAL_MODEL or os.environ.get("TRANSCRIPT_CACHE") == "0":
        return _transcribe_uncached(audio, beam_size)
    path = _transcript_cache_dir() / f"{_transcript_key(audio, beam_size)}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cached, list):
            return cached
    except (OSError, ValueError):
        pass
    words = _transcribe_uncached(audio, beam_size)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return words


def _transcribe_uncached(audio, beam_size=1):
    words = _decode(audio, True, beam_size=beam_size)
    if not words or _repetitive(words):
        alt = _decode(audio, False, beam_size=beam_size)
        if alt and (not words or not _repetitive(alt)):
            return alt
    return words


def _row_gate(row):
    """The recall this scene is gated on. Older rows only carry `recall`."""
    raw = row.get("gate_recall")
    if raw is None:
        raw = row.get("recall")
    return float(raw or 0)


def _worst_recall(report, beat):
    rows = [row for row in (report.get("scenes") or []) if row.get("beat") == beat]
    if not rows:
        return 1.0
    return min(_row_gate(row) for row in rows)


def _breaks_a_passing_scene(before, after):
    """True when a scene that passed in `before` fails in `after`."""
    old_rows = before.get("scenes") or []
    new_rows = after.get("scenes") or []
    if len(old_rows) != len(new_rows):
        return True
    for old, new in zip(old_rows, new_rows):
        if old.get("passed", True) and not new.get("passed", True):
            return True
    return False


def _words_on_timeline(path, t0, beam_size):
    """Word times for one beat file, shifted onto the finished timeline."""
    fresh = transcribe_pcm(pcm(path), beam_size=beam_size)
    shifted = []
    for word in fresh:
        text = str(word.get("word") or "").strip()
        if not text:
            continue
        shifted.append({
            "word": text,
            "start": float(word.get("start") or 0) + float(t0),
            "end": float(word.get("end") or 0) + float(t0),
        })
    return shifted


def upgrade_weak_scenes(words, scenes, beats_text, beat_times, answer_terms, beat_audio):
    """Re-decode a weak scene's beat with beam 5 and keep the clearer transcript.

    MIN_RECALL does not change. A second pass that scores worse is discarded,
    and the scene still fails.
    """
    report = check(words, scenes, beats_text, beat_times, answer_terms)
    report["words"] = list(words or [])
    if report.get("ok"):
        return report
    weak = []
    for row in report.get("scenes") or []:
        beat = row.get("beat") or ""
        if beat in weak or beat not in (beat_audio or {}) or beat not in (beat_times or {}):
            continue
        # Weak means below the bar this scene is gated on. A chart card that
        # already passes on its beat recall is not re-decoded: a beam-5 pass
        # with a better slice recall could replace a passing transcript.
        if _row_gate(row) + 1e-9 < MIN_RECALL:
            path = pathlib.Path(beat_audio[beat])
            if path.is_file():
                weak.append(beat)
    upgraded = list(words or [])
    for beat in weak:
        span = beat_times[beat]
        t0, t1 = float(span[0]), float(span[1])
        try:
            fresh = _words_on_timeline(beat_audio[beat], t0, _RETRY_BEAM)
        except Exception as exc:  # noqa: BLE001 — the first transcript still stands
            print(f"[sync] stronger whisper skipped for {beat}: {exc}")
            continue
        if not fresh:
            continue
        trial = [
            word for word in upgraded
            if not (
                float(word.get("end") or 0) >= t0 - WINDOW_PAD
                and float(word.get("start") or 0) <= t1 + WINDOW_PAD
            )
        ]
        trial.extend(fresh)
        trial.sort(key=lambda word: float(word.get("start") or 0))
        trial_report = check(trial, scenes, beats_text, beat_times, answer_terms)
        # Keep the second pass only when it scores better on the gate's own
        # number for this beat and adds no failure elsewhere (the replaced pad
        # words belong to the neighbouring beats too). MIN_RECALL is unchanged.
        better = _worst_recall(trial_report, beat) > _worst_recall(report, beat) + 1e-9
        if better and not _breaks_a_passing_scene(report, trial_report):
            upgraded = trial
            report = trial_report
            report["words"] = upgraded
            print(f"[sync] stronger whisper kept for {beat} (beam {_RETRY_BEAM})")
    report["words"] = upgraded
    return report
