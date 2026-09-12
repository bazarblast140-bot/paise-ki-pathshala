# -*- coding: utf-8 -*-
"""Narration -> text the voice can't mispronounce.

ElevenLabs read "₹1 लाख" as "Rs 1 लाख", and digits/%, "9:15", "IV" come out in
English or wrong. Captions keep the original (₹, digits read fine on screen);
only the TTS gets this version. Everything is deterministic — no LLM.

  python speak.py        # self-test
"""
import re

ONES = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
        "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस "
        "पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस "
        "अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ "
        "चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर "
        "अठहत्तर उनासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे "
        "बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे").split()
assert len(ONES) == 100

MONTHS = "जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split()

# uppercase short forms: spell them the way Indian traders say them
# (IV was read as Roman "four", OI as "oy", MIS as "miss")
ABBR = {
    "F&O": "एफ़ एंड ओ", "T+1": "टी प्लस वन", "SL-M": "एस एल एम", "P&L": "पी एंड एल",
    "SEBI": "सेबी", "NIFTY": "निफ़्टी", "SENSEX": "सेंसेक्स", "CAGR": "केगर", "NRML": "नॉर्मल",
    "VWAP": "वी वैप", "DMART": "डीमार्ट", "IV": "आई वी", "OI": "ओ आई", "MIS": "एम आई एस",
    "AMO": "ए एम ओ", "EMA": "ई एम ए", "SMA": "एस एम ए", "DMA": "डी एम ए", "SL": "एस एल",
    "RSI": "आर एस आई", "MACD": "एम ए सी डी", "ATR": "ए टी आर", "ATM": "ए टी एम", "ITM": "आई टी एम",
    "OTM": "ओ टी एम", "GTT": "जी टी टी", "CNC": "सी एन सी", "NSE": "एन एस ई", "BSE": "बी एस ई",
    "NSDL": "एन एस डी एल", "CDSL": "सी डी एस एल", "HDFC": "एच डी एफ़ सी", "SBI": "एस बी आई",
    "TCS": "टी सी एस", "LTP": "एल टी पी", "OHLC": "ओ एच एल सी", "IPO": "आई पी ओ", "ETF": "ई टी एफ़",
    "PE": "पी ई", "EPS": "ई पी एस", "KYC": "के वाई सी", "UPI": "यू पी आई", "GST": "जी एस टी",
    "STT": "एस टी टी", "SIP": "सिप", "MTF": "एम टी एफ़", "ASM": "ए एस एम", "RR": "आर आर",
    "CE": "सी ई", "EOD": "ई ओ डी", "YTD": "वाई टी डी", "DP": "डी पी", "FY": "एफ़ वाई",
    "BTST": "बी टी एस टी", "STBT": "एस टी बी टी", "NAV": "एन ए वी", "AMC": "ए एम सी", "MF": "म्यूचुअल फ़ंड",
    "YES": "Yes", "BUY": "buy", "SELL": "sell", "AND": "and",       # 3-akshar English shabd, spell nahi
}
UNIT = {"करोड़": 10 ** 7, "लाख": 10 ** 5, "हज़ार": 1000, "हजार": 1000}


def words(n):
    """Integer -> Hindi words, Indian grouping (लाख/करोड़)."""
    n = int(n)
    if n < 0:
        return "माइनस " + words(-n)
    if n < 100:
        return ONES[n]
    if n == 100:
        return "सौ"                              # "सौ प्रतिशत", not "एक सौ"
    out = []
    for size, name in ((10 ** 7, "करोड़"), (10 ** 5, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= size:
            out.append(f"{words(n // size)} {name}")
            n %= size
    if n:
        out.append(ONES[n])
    return " ".join(out)


def year_words(n):
    """1875 -> अठारह सौ पचहत्तर (years read in hundreds); 2010 -> दो हज़ार दस."""
    if 1100 <= n <= 1999:
        rest = n % 100
        return f"{ONES[n // 100]} सौ" + (f" {ONES[rest]}" if rest else "")
    return words(n)


def number(tok):
    """'4,231' / '59.53' / '1.0' / '2010' -> words."""
    raw = tok.replace(",", "")
    if "." in raw:
        whole, frac = raw.split(".", 1)
        frac = frac.rstrip("0")
        if not frac:
            return words(int(whole or 0))
        return f"{words(int(whole or 0))} दशमलव " + " ".join(ONES[int(ch)] for ch in frac)
    n = int(raw)
    if "," not in tok and len(raw) == 4:
        return year_words(n)
    return words(n)


def _rupees(m):
    val = float(m.group(1).replace(",", "")) * UNIT.get(m.group(2) or "", 1)
    rupees, paise = int(val), round((val - int(val)) * 100)
    s = f"{words(rupees)} रुपये"
    return s + (f" {words(paise)} पैसे" if paise else "")


def _time(m):
    h, mm = int(m.group(1)), int(m.group(2))
    if h == 1 or len(m.group(2)) == 1:          # 1:2, 1:10, 2:1 = ratio, not a clock
        return f"{words(h)} अनुपात {words(mm)}"
    h = h - 12 if h > 12 else h
    if mm == 0:
        return f"{words(h)} बजे"
    if mm == 30:
        return {1: "डेढ़ बजे", 2: "ढाई बजे"}.get(h, f"साढ़े {words(h)} बजे")
    return f"{words(h)} बजकर {words(mm)} मिनट"


def _date(m):
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{words(d)} {MONTHS[mo - 1]} {year_words(y)}"


NUM = r"\d+(?:,\d+)*(?:\.\d+)?"          # "70, Rs" me comma number ka hissa nahi


def speakable(text):
    t = text
    for k in sorted(ABBR, key=len, reverse=True):                          # longest first (SL-M before SL)
        t = re.sub(rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])", ABBR[k], t)
    t = re.sub(r"(\d{4})-(\d{2})-(\d{2})", _date, t)                       # 2010-01-04
    t = re.sub(rf"(?:₹|Rs\.?|रु\.?)\s?({NUM})(?:\s*(करोड़|लाख|हज़ार|हजार))?", _rupees, t)
    t = re.sub(r"(?<![\d:])(\d{1,2}):(\d{1,2})(?![\d:])", _time, t)
    t = re.sub(rf"({NUM})\s?[xX](?![A-Za-z])", r"\1 गुना", t)               # 5x, 59.53x
    t = re.sub(r"(?<![\w])[-−](?=\d)", "माइनस ", t)
    t = re.sub(r"(?<=\d)\s?[-–]\s?(?=\d)", " ", t)                        # 2-3 दिन, 12-26
    t = re.sub(r"\s*%", " प्रतिशत", t)
    t = re.sub(rf"(?<=\d)\s*/\s*(?=\d|\()", " बटा ", t)
    t = t.replace("/", " और ").replace("=", " बराबर ").replace("+", " प्लस ").replace("&", " एंड ")
    # baaki CAPS: 4+ akshar = naam ("TITAN" -> "Titan", warna T-I-T-A-N), 2-3 = akshar alag
    t = re.sub(r"(?<![A-Za-z])[A-Z]{4,}(?![A-Za-z])", lambda m: m.group().title(), t)
    t = re.sub(r"(?<![A-Za-z])[A-Z]{2,3}(?![A-Za-z])", lambda m: " ".join(m.group()), t)
    t = re.sub(r"\s*(?:→|->)\s*", " से ", t)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = re.sub(r"[()]", " ", t)
    t = re.sub(NUM, lambda m: number(m.group()), t)
    t = re.sub(r"₹|\bRs\b\.?", "रुपये", t)                                  # akela ₹ / Rs bina number
    return re.sub(r"\s{2,}", " ", t).strip()


def leftovers(t):
    """Anything the voice may still misread — for a warning in the log."""
    return re.findall(r"\d|₹|%|\bRs\b|[&+=/]|\b[A-Z]{2,}\b", t)


if __name__ == "__main__":
    cases = {
        "₹1 लाख करीब ₹59.5 लाख बन जाता।": "एक लाख रुपये करीब उनसठ लाख पचास हज़ार रुपये बन जाता।",
        "Titan करीब 71 पर था, जून 2026 में 4,231।": "Titan करीब इकहत्तर पर था, जून दो हज़ार छब्बीस में चार हज़ार दो सौ इकतीस।",
        "यानी 59.53 गुना।": "यानी उनसठ दशमलव पाँच तीन गुना।",
        "Yes Bank 2015 से करीब 85% नीचे": "Yes Bank दो हज़ार पंद्रह से करीब पचासी प्रतिशत नीचे",
        "₹15,000 बचते": "पंद्रह हज़ार रुपये बचते",
        "Rs 1,00,000": "एक लाख रुपये",
        "₹1.0 लाख": "एक लाख रुपये",
        "9:00 से 9:08, 9:15 पर, 3:30 बंद": "नौ बजे से नौ बजकर आठ मिनट, नौ बजकर पंद्रह मिनट पर, साढ़े तीन बजे बंद",
        "RR 1:2 हो": "आर आर एक अनुपात दो हो",
        "5x पर 100% नुकसान": "पाँच गुना पर सौ प्रतिशत नुकसान",
        "1 / (1+2) = 33.3%": "एक बटा एक प्लस दो बराबर तैंतीस दशमलव तीन प्रतिशत",
        "IV और OI, SL-M, F&O, T+1": "आई वी और ओ आई, एस एल एम, एफ़ एंड ओ, टी प्लस वन",
        "NSDL / CDSL": "एन एस डी एल और सी डी एस एल",
        "2010-01-04 को 71": "चार जनवरी दो हज़ार दस को इकहत्तर",
        "BSE 1875 में बना": "बी एस ई अठारह सौ पचहत्तर में बना",
        "NIFTY 50 में 50 DMA": "निफ़्टी पचास में पचास डी एम ए",
        "delta करीब 0.5": "delta करीब शून्य दशमलव पाँच",
        "₹500 का SL, 1% risk": "पाँच सौ रुपये का एस एल, एक प्रतिशत risk",
        "23,417 पर": "तेईस हज़ार चार सौ सत्रह पर",
        "RSI 70 के ऊपर": "आर एस आई सत्तर के ऊपर",
        "TITAN और YES BANK, HDFC BANK": "Titan और Yes Bank, एच डी एफ़ सी Bank",
        "BTST, DP और XYZ": "बी टी एस टी, डी पी और X Y Z",
        "RSI 70, Rs 500 और Rs में": "आर एस आई सत्तर, पाँच सौ रुपये और रुपये में",
    }
    bad = 0
    for src, want in cases.items():
        got = speakable(src)
        if got != want:
            bad += 1
            print(f"FAIL {src!r}\n  got : {got}\n  want: {want}")
    assert words(4231) == "चार हज़ार दो सौ इकतीस" and words(5950000) == "उनसठ लाख पचास हज़ार"
    assert words(12345678) == "एक करोड़ तेईस लाख पैंतालीस हज़ार छह सौ अठहत्तर"
    print("speak self-test:", "OK" if not bad else f"{bad} FAIL")
    raise SystemExit(1 if bad else 0)
