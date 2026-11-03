"""Spoken Hinglish for the story Reel. Digits in the voice are whole numbers.

Every figure comes from levels['story_figures']. A missing figure drops that
line. The wording stays educational: no order to buy or sell.
"""
from __future__ import annotations

import re

from realchart.fmt import inr
from realchart.script import DISCLAIMER
from realchart.story_facts import flow_sentence

EDU = "शैक्षिक उदाहरण, स्टॉक टिप नहीं"
IMPERATIVE = (
    "खरीदो", "खरीदें", "खरीदिये", "खरीदना", "बेचो", "बेचें", "बेचिये", "बेचना",
    "अभी लो", "buy now", "sell now", "guaranteed", "100%",
)
SECTOR_HI = {
    "NIFTY IT": "निफ्टी आईटी",
    "NIFTY BANK": "निफ्टी बैंक",
    "NIFTY AUTO": "निफ्टी ऑटो",
    "NIFTY FMCG": "निफ्टी एफएमसीजी",
    "NIFTY PHARMA": "निफ्टी फार्मा",
    "NIFTY METAL": "निफ्टी मेटल",
    "NIFTY REALTY": "निफ्टी रियल्टी",
    "NIFTY ENERGY": "निफ्टी एनर्जी",
    "NIFTY FIN SERVICE": "निफ्टी फाइनेंस",
    "NIFTY FINANCIAL SERVICES": "निफ्टी फाइनेंस",
}


def _fig(levels, name):
    for item in levels.get("story_figures") or []:
        if item.get("name") == name and item.get("value") is not None:
            return item
    return None


def _whole(levels, name):
    item = _fig(levels, name)
    if not item:
        return None
    return str(int(round(abs(float(item["value"])))))


def _signed_word(value):
    return "बढ़ा" if float(value) >= 0 else "घटा"


def _name(levels):
    return str(levels.get("symbol") or "").split(":")[-1] or "स्टॉक"


def _row(sec, caption, spoken, source):
    return {
        "sec": sec, "story": True, "start": 0.0, "end": 3.0 if sec == "hook" else None,
        "d": caption, "t": spoken, "k": sec, "source_label": source,
    }


def _pct_caption(levels, name):
    item = _fig(levels, name)
    if not item:
        return None, None
    value = float(item["value"])
    return f"{abs(value):.1f}", _signed_word(value)


def rows_for(kind, levels):
    story = levels.get("story") or {}
    if kind == "close":
        return _close_rows(levels, story)
    return _ta_rows(levels, story)


def _ta_rows(levels, story):
    name = _name(levels)
    close = _whole(levels, "close")
    reversal = story.get("reversal") or {}
    rev = reversal.get("name_hi") or ""
    rows = []
    if rev and close:
        low = _whole(levels, "reversal_low")
        rows.append(_row(
            "hook",
            f"{name} · {rev} · क्लोज़ {inr(levels.get('close'), 2)}",
            f"{name} में {rev} रिवर्सल! क्लोज़ {close}।",
            "OHLC",
        ))
        if low:
            rows.append(_row(
                "reversal",
                f"{rev} · लो {inr(reversal.get('low'), 2)}",
                f"लो {low} पर {rev}, ओएचएलसी से।",
                reversal.get("source") or "computed from daily OHLC",
            ))
    elif close:
        side = "ऊपर" if float(levels.get("chg_pct") or 0) >= 0 else "नीचे"
        pct = _whole(levels, "chg_pct") or "0"
        rows.append(_row(
            "hook",
            f"{name} क्लोज़ {inr(levels.get('close'), 2)} ({float(levels.get('chg_pct') or 0):+.2f}%)",
            f"{name} {close} पर बंद, आज {pct} प्रतिशत {side}। साफ रिवर्सल कैंडल नहीं बनी।",
            "OHLC",
        ))
    fundamentals = story.get("fundamentals") or {}
    bits_q = []
    for key, label in (("profit_yoy_q", "प्रॉफिट"), ("revenue_yoy_q", "रेवेन्यू")):
        shown, word = _pct_caption(levels, key)
        if shown:
            bits_q.append(f"{label} {shown}% {word}")
    if bits_q:
        spoken_bits = []
        for key, label in (("profit_yoy_q", "प्रॉफिट"), ("revenue_yoy_q", "रेवेन्यू")):
            whole = _whole(levels, key)
            item = _fig(levels, key)
            if whole and item:
                spoken_bits.append(f"{label} {whole} प्रतिशत {_signed_word(item['value'])}")
        rows.append(_row(
            "results",
            "तिमाही · " + " · ".join(bits_q),
            "तिमाही में " + ", ".join(spoken_bits) + "।",
            fundamentals.get("source") or "fundamentals",
        ))
    bits_y = []
    spoken_y = []
    for key, label in (("profit_yoy_y", "प्रॉफिट"), ("revenue_yoy_y", "रेवेन्यू")):
        shown, word = _pct_caption(levels, key)
        whole = _whole(levels, key)
        item = _fig(levels, key)
        if shown and whole and item:
            bits_y.append(f"{label} {shown}% {word}")
            spoken_y.append(f"{label} {whole} प्रतिशत")
    future = _whole(levels, "future_growth")
    if bits_y or future:
        caption = "सालाना · " + " · ".join(bits_y)
        spoken = ""
        if spoken_y:
            spoken = "सालाना " + " और ".join(spoken_y) + "।"
        if future:
            caption = (caption + " · " if bits_y else "") + f"आगे की ग्रोथ { _pct_caption(levels, 'future_growth')[0] }%"
            spoken = (spoken + " " if spoken else "") + f"आगे ग्रोथ {future} प्रतिशत।"
        rows.append(_row("growth", caption.strip(" ·"), spoken.strip(), fundamentals.get("source") or "fundamentals"))
    flows = story.get("flows") or {}
    fii, dii = _whole(levels, "fii_net_cr"), _whole(levels, "dii_net_cr")
    if fii or dii:
        parts, spoken_parts = [], []
        if fii:
            parts.append(f"FII {inr(_fig(levels, 'fii_net_cr')['value'], 0)} करोड़")
            spoken_parts.append(f"एफआईआई नेट {fii} करोड़")
        if dii:
            parts.append(f"DII {inr(_fig(levels, 'dii_net_cr')['value'], 0)} करोड़")
            spoken_parts.append(f"डीआईआई नेट {dii} करोड़")
        rows.append(_row(
            "flows",
            " · ".join(parts),
            ", ".join(spoken_parts) + "। " + flow_sentence(flows),
            flows.get("source") or "NSE",
        ))
    news = story.get("news") or {}
    if news.get("title"):
        rows.append(_row(
            "news",
            f"{news.get('title')} · {news.get('source') or ''}".strip(" ·"),
            f"खबर: {_clip(news.get('title'))}। स्रोत {news.get('source') or 'आर्टिकल'}।",
            news.get("source") or "news",
        ))
        rows[-1]["skip_numbers"] = True
    plan = story.get("plan") or {}
    stop, target = _whole(levels, "plan_stop"), _whole(levels, "plan_target")
    if stop and target:
        rows.append(_row(
            "plan",
            f"उदाहरण स्टॉप {inr(plan.get('stop'), 2)} · टारगेट {inr(plan.get('target'), 2)}",
            f"उदाहरण: स्टॉप {stop}, टारगेट {target}। यह scenario है।",
            plan.get("source") or "computed from daily OHLC",
        ))
    rows.append(_cta())
    return rows


def _close_rows(levels, story):
    close = _whole(levels, "close")
    points = _whole(levels, "points")
    side = "ऊपर" if float(levels.get("chg_pct") or 0) >= 0 else "नीचे"
    rows = []
    if close and points:
        rows.append(_row(
            "hook",
            f"निफ्टी {inr(levels.get('close'), 2)} · {float(levels.get('chg_pct') or 0):+.2f}%",
            f"निफ्टी {points} अंक {side}, {close} पर बंद!",
            "OHLC",
        ))
    breadth = story.get("breadth") or {}
    adv, dec = _whole(levels, "advances"), _whole(levels, "declines")
    if adv and dec:
        weak = float(_fig(levels, "declines")["value"]) > float(_fig(levels, "advances")["value"])
        rows.append(_row(
            "breadth",
            f"ऊपर {int(float(_fig(levels, 'advances')['value']))} · नीचे {int(float(_fig(levels, 'declines')['value']))}",
            f"निफ्टी में {adv} ऊपर, {dec} नीचे। चौड़ाई {'कमजोर' if weak else 'मजबूत'} है।",
            breadth.get("source") or "NSE",
        ))
    flows = story.get("flows") or {}
    fii, dii = _whole(levels, "fii_net_cr"), _whole(levels, "dii_net_cr")
    if fii or dii:
        parts, spoken_parts = [], []
        if fii:
            parts.append(f"FII {inr(_fig(levels, 'fii_net_cr')['value'], 0)} करोड़")
            spoken_parts.append(f"एफआईआई नेट {fii} करोड़")
        if dii:
            parts.append(f"DII {inr(_fig(levels, 'dii_net_cr')['value'], 0)} करोड़")
            spoken_parts.append(f"डीआईआई नेट {dii} करोड़")
        rows.append(_row(
            "flows",
            " · ".join(parts),
            ", ".join(spoken_parts) + "। " + flow_sentence(flows),
            flows.get("source") or "NSE",
        ))
    news = story.get("news") or {}
    if news.get("title"):
        rows.append(_row(
            "news",
            f"{news.get('title')} · {news.get('source') or ''}".strip(" ·"),
            f"खबर: {_clip(news.get('title'))}। स्रोत {news.get('source') or 'आर्टिकल'}।",
            news.get("source") or "news",
        ))
        rows[-1]["skip_numbers"] = True
    sectors = story.get("sectors") or {}
    best, worst = (sectors.get("best") or {}), (sectors.get("worst") or {})
    if best and worst and _fig(levels, "sector_best_pct") and _fig(levels, "sector_worst_pct"):
        bp, wp = _whole(levels, "sector_best_pct"), _whole(levels, "sector_worst_pct")
        rows.append(_row(
            "sectors",
            f"{best.get('name')} {float(best.get('pct')):+.1f}% · {worst.get('name')} {float(worst.get('pct')):+.1f}%",
            (f"{_sector(best.get('name'))} {bp} प्रतिशत {'ऊपर' if float(best.get('pct')) >= 0 else 'नीचे'}, "
             f"{_sector(worst.get('name'))} {wp} प्रतिशत {'ऊपर' if float(worst.get('pct')) >= 0 else 'नीचे'}।"),
            sectors.get("source") or "NSE",
        ))
    pivot = _whole(levels, "next_pivot")
    if pivot:
        rows.append(_row(
            "level",
            f"कल का पिवट {inr(_fig(levels, 'next_pivot')['value'], 2)}",
            f"कल का पिवट {pivot}। यह scenario है, सलाह नहीं।",
            "classic pivot from today's high, low and close",
        ))
    rows.append(_cta())
    return rows


def _clip(title, words=6):
    parts = str(title or "").split()
    text = " ".join(parts[:words])
    if len(parts) > words:
        text = text.rstrip(".,")
    return text


def _sector(name):
    return SECTOR_HI.get(str(name or "").upper(), str(name or ""))


def _cta():
    question = "आप क्या देखते हो? Comment करो।"
    spoken = f"{question} {EDU}। {DISCLAIMER}"
    return _row("cta", f"{question} {EDU}। {DISCLAIMER}", spoken, "")


def compliance_faults(rows):
    blob = " ".join(str(row.get("t") or "") for row in rows)
    faults = []
    if EDU not in blob:
        faults.append("educational line missing")
    low = blob.lower()
    for phrase in IMPERATIVE:
        if phrase.lower() in low:
            faults.append(f"imperative buy/sell: {phrase}")
    hook = next((row for row in rows if row.get("sec") == "hook"), None)
    if hook and not re.search(r"[\u0900-\u097F]", str(hook.get("t") or "")):
        faults.append("hook has no Devanagari")
    return faults


def post_meta(kind, levels):
    """Hindi title and description. Title stays short; numbers are the fetched ones."""
    from datetime import date
    story = levels.get("story") or {}
    try:
        day = date.fromisoformat(str(levels.get("last_date"))[:10])
        day_text = f"{day.day} {day.strftime('%b')}"
    except ValueError:
        day_text = ""
    if kind == "ta":
        rev = (story.get("reversal") or {}).get("name_hi") or "रिवर्सल"
        title = f"{_name(levels)} शेयर: {rev}?"
        topic = f"{_name(levels)} {rev}"
    else:
        word = "चढ़ा" if float(levels.get("chg_pct") or 0) >= 0 else "गिरा"
        title = f"निफ्टी {word}: वजह क्या?"
        topic = f"निफ्टी {word}"
    title = title[:60]
    sources = sorted({item.get("source") for item in (levels.get("story_figures") or []) if item.get("source")})
    caption = (
        f"{topic} ({day_text}). डेटा: {', '.join(sources) or 'OHLC'}। "
        f"{EDU}। {DISCLAIMER}"
    )
    tag = _name(levels) if kind == "ta" else "Nifty"
    return {
        "title": title,
        "caption": caption,
        "hashtags": [f"#{tag}", "#stockmarket", "#nifty50", "#sharemarkethindi", "#paisekipathshala"],
        "pinned_comment": "आप क्या देखते हो? कमेंट करो।",
        "series": "Aaj Ka Stock" if kind == "ta" else "Nifty Close",
    }


def thumb_headline(kind, levels):
    story = levels.get("story") or {}
    if kind == "ta":
        rev = (story.get("reversal") or {}).get("name_hi") or "रिवर्सल"
        return f"{_name(levels)}\n{rev}?"
    word = "चढ़ा" if float(levels.get("chg_pct") or 0) >= 0 else "गिरा"
    return f"निफ्टी {word}\nवजह क्या?"
