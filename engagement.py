"""Poll-style comment question. Educational, not advice, and not a referral."""

_BY_CAT = {
    "CANDLESTICK": "अगली कैंडल का रंग मायने रखता है? Comment में हाँ या नहीं लिखो।",
    "INDICATOR": "यह level अपने आप एक signal है? Comment में हाँ या नहीं लिखो।",
    "ORDERS": "Limit order और market order में फर्क पहले से पता था? Comment में हाँ या नहीं लिखो।",
    "OPTIONS": "Max loss इस setup में पहले से तय था? Comment में हाँ या नहीं लिखो।",
    "RISK": "एक ट्रेड में 1% से ज्यादा risk रखते हो? Comment में हाँ या नहीं लिखो।",
    "REAL DATA": "इस हिसाब में dividend शामिल था? Comment में हाँ या नहीं लिखो।",
    "MARKET FACT": "यह फैक्ट पहले से पता था? Comment में हाँ या नहीं लिखो।",
    "FUNDAMENTAL": "Cash flow और profit एक ही चीज़ है? Comment में हाँ या नहीं लिखो।",
    "PENNY STOCK": "कम कीमत का मतलब सस्ता है? Comment में हाँ या नहीं लिखो।",
    "INTRADAY": "Breakout से पहले volume देखते हो? Comment में हाँ या नहीं लिखो।",
}
_DEFAULT = "यह बात पहले से पता थी? Comment में हाँ या नहीं लिखो।"


def engagement_question(topic=None):
    """A simple हाँ/नहीं poll. Same line goes on screen and in the description."""
    cat = str((topic or {}).get("cat") or "")
    return _BY_CAT.get(cat, _DEFAULT)
