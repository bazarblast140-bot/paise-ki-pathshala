"""Ank -> Hindi shabd, SIRF voice ke liye.

Kyun: ElevenLabs digits ko theek nahi padhti — "1853" ko wo अंग्रेज़ी tarah
ya ek-ek ank karke bol deti hai. Saal ko Hindi me "अठारह सौ तिरपन" bolna
chahiye, "एक हज़ार आठ सौ तिरपन" nahi.

Screen par digits hi rehte hain — ye sirf TTS text par lagta hai.
"""
import re

ONES = ["शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
        "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह",
        "अठारह", "उन्नीस", "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस",
        "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस", "तीस", "इकतीस", "बत्तीस",
        "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
        "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस",
        "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास", "पचास", "इक्यावन", "बावन",
        "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ", "साठ",
        "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ",
        "उनहत्तर", "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर",
        "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उन्यासी", "अस्सी",
        "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी",
        "अठासी", "नवासी", "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे",
        "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे"]


def under_hundred(n: int) -> str:
    return ONES[n]


def hundreds(n: int) -> str:
    """1..999"""
    if n < 100:
        return under_hundred(n)
    h, r = divmod(n, 100)
    out = f"{ONES[h]} सौ"
    return out if r == 0 else f"{out} {under_hundred(r)}"


def year_words(n: int) -> str:
    """Saal Hindi me: 1853 -> 'अठारह सौ तिरपन', 1900 -> 'उन्नीस सौ'.

    2000 ke baad ka poora dashak apvaad hai — koi 'बीस सौ दस' nahi bolta,
    'दो हज़ार दस' bolte hain."""
    if 2000 <= n <= 2099:
        r = n - 2000
        return "दो हज़ार" if r == 0 else f"दो हज़ार {under_hundred(r)}"
    hi, lo = divmod(n, 100)
    out = f"{under_hundred(hi)} सौ"
    return out if lo == 0 else f"{out} {under_hundred(lo)}"


def number_words(n: int, as_year: bool = False) -> str:
    if as_year and 1000 <= n <= 2099:
        return year_words(n)
    if n < 1000:
        return hundreds(n)
    if n < 100000:
        th, r = divmod(n, 1000)
        out = f"{hundreds(th)} हज़ार"
        return out if r == 0 else f"{out} {hundreds(r)}"
    if n < 10000000:
        la, r = divmod(n, 100000)
        out = f"{hundreds(la)} लाख"
        return out if r == 0 else f"{out} {number_words(r)}"
    cr, r = divmod(n, 10000000)
    out = f"{hundreds(cr)} करोड़"
    return out if r == 0 else f"{out} {number_words(r)}"


# 4 ank ka number saal hai ya nahi — aas-paas ke shabd se tay karte hain.
_YEARISH = re.compile(r"(साल|वर्ष|सन|ई\.?|में|को|तक|से)\s*$")
_NUM = re.compile(r"\d[\d,]*")


def speak_numbers(text: str) -> str:
    """Har ank ko Hindi shabd me badal do. Comma wale (21,000) bhi."""
    def repl(m):
        raw = m.group(0).rstrip(",")
        digits = raw.replace(",", "")
        if not digits.isdigit():
            return m.group(0)
        n = int(digits)
        before = text[:m.start()]
        # 4-ank ka number jo 1000-2099 me ho -> saal maan lo. Yahi wo case hai
        # jahan TTS sabse zyada galat bolti hai (1853, 1947, 1901).
        as_year = len(digits) == 4 and 1000 <= n <= 2099 and "," not in raw
        return number_words(n, as_year=as_year)
    out = _NUM.sub(repl, text)
    out = out.replace("%", " प्रतिशत")
    return " ".join(out.split())
