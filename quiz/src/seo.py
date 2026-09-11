"""YouTube metadata. Sab template-based — koi API call nahi, deterministic aur free.

Ye rules 2026 ke Shorts behaviour pe based hain, purane long-form SEO pe nahi:

1. HASHTAG: 3-5 se zyada ka koi fayda nahi. 15 se upar gaye to YouTube
   video ke SAARE hashtag ignore kar deta hai — pehle teen bhi. Hum 4 use
   karte hain: broad -> niche -> subject -> brand.
2. #shorts NAHI lagate. YouTube ab aspect ratio (9:16) aur duration se
   Short pehchanta hai; #shorts sirf title/hashtag budget khaata hai aur
   categorisation me zero madad karta hai (har Short use kar sakta hai).
3. TAGS (hidden metadata): 21 generic tags se 8 specific tags behtar hain.
   Tag volume nahi, relevance count hoti hai. Irrelevant tag ulta galat
   audience tak le jaata hai -> low retention -> algorithm neeche kar deta hai.
4. TITLE: sawaal SABSE PEHLE. Shorts feed me mobile pe title ki ek hi line
   dikhti hai (~45-55 char), isliye "क्या तुम्हें पता है?" jaisa hook aage
   rakha to asli sawaal truncate ho jaata hai. Sawaal khud hi hook hai.
5. DESCRIPTION: pehle ~100 character sabse zyada weight rakhte hain —
   wahan keyword + sawaal, na ki generic tagline.

Sabse zaroori baat: ye sab CHHOTA lever hai. Shorts me discovery mainly
retention aur swipe-away rate se chalti hai. SEO sirf ye tay karta hai ki
video KIS audience ko dikhe; wo audience rukegi ya nahi, wo video pe hai.
"""
import random
from datetime import datetime

import config as C

HOOKS = {
    "premarket":  ["मार्केट खुलने से पहले ये बताओ", "आज का ट्रेडिंग सवाल",
                   "90% ट्रेडर्स यहाँ गलत होते हैं", "चार्ट पढ़ना आता है?"],
    "postmarket": ["मार्केट बंद, अब दिमाग़ चलाओ", "प्रो ट्रेडर हो तो बताओ",
                   "ये गलती पैसा डुबोती है", "कितने ट्रेडर्स सही करेंगे?"],
    "night":      ["सोने से पहले एक ट्रेडिंग सवाल", "कल के ट्रेड से पहले ये जान लो",
                   "ज़्यादातर लोग ये नहीं जानते", "आज का आख़िरी सवाल"],
}

# Specific -> broad. 8 tags kaafi hain.
BASE_TAGS = [
    "stock market quiz hindi",
    "trading quiz",
    "share market hindi",
    "stock market for beginners hindi",
    "trading tips hindi",
]

SUB_TAGS = {
    "कैंडलस्टिक":   ["candlestick patterns hindi", "candlestick pattern quiz"],
    "इंडिकेटर":    ["technical indicators hindi", "rsi macd hindi"],
    "ऑर्डर":       ["order types stock market hindi", "stop loss order hindi"],
    "F&O":         ["option trading hindi", "f&o basics hindi"],
    "रिस्क":        ["risk management trading hindi", "position sizing hindi"],
    "मार्केट फैक्ट": ["stock market facts hindi", "nse bse facts"],
    "अगर लगाते":   ["nifty returns", "compounding stock market hindi"],
}

# 4 hashtag: broad -> niche -> subject -> brand. #shorts jaan-boojh ke nahi.
SUB_HASH = {"कैंडलस्टिक": "#Candlestick", "इंडिकेटर": "#TechnicalAnalysis",
            "ऑर्डर": "#StockMarket", "F&O": "#OptionTrading", "रिस्क": "#RiskManagement",
            "मार्केट फैक्ट": "#StockMarketFacts", "अगर लगाते": "#Nifty"}
SUB_HI = {k: k for k in SUB_TAGS}


def build(questions, slot="morning", date=None):
    date = date or datetime.now()
    rng = random.Random(f"{date:%Y%m%d}{slot}")
    # Hook pehle sirf slot se aata tha. Ab morning aur night me bhi paheli
    # jaati hai (config.PAHELI_SLOTS), aur riddle par "सुबह का GK सवाल" galat
    # hook hai — isliye content dekhkar chunte hain, slot ke naam se nahi.
    is_paheli = slot in getattr(C, "PAHELI_SLOTS", ["paheli"]) or any(
        q.get("sub") == C.PAHELI_SUB for q in questions)
    hook = rng.choice(HOOKS["paheli"] if is_paheli
                      else HOOKS.get(slot, HOOKS["premarket"]))
    labels = ["A", "B", "C", "D"]

    q1 = questions[0]
    qtext = q1["q"].strip()
    subs = list(dict.fromkeys(q.get("sub", "GK") for q in questions))
    sub_hi = " + ".join(SUB_HI.get(x, "ट्रेडिंग") for x in subs)

    # --- TITLE: sawaal pehle -------------------------------------------------
    # Mobile Shorts feed me title ki ek line dikhti hai. Isliye sawaal poora
    # aage, aur context (subject/class) baad me — truncate ho bhi jaaye to
    # viewer ne asli hook padh liya hota hai.
    if is_paheli:
        tail = " | हिंदी पहेली"
        if len(qtext) + len(tail) > 96:
            tail = " | पहेली"
        title = f"{qtext} 🤔{tail}"
    else:
        tail = f" | {sub_hi} क्विज़"
        if len(qtext) + len(tail) > 96:
            tail = f" | {sub_hi} क्विज़"
        title = f"{qtext} 🤔{tail}"
    if len(title) > 100:
        title = f"{qtext[:100 - len(tail) - 4]}…{tail}"

    # --- DESCRIPTION ---------------------------------------------------------
    q_block = "\n\n".join(
        f"सवाल {i+1} ({SUB_HI.get(q.get('sub','GK'),'ट्रेडिंग')}): {q['q']}\n"
        + "\n".join(f"{labels[j]}) {o}" for j, o in enumerate(q["opts"]))
        + f"\n✅ सही जवाब: {labels[q['ans']]}) {q['opts'][q['ans']]}"
        + (f"\n💡 क्यों: {q['why']}" if q.get("why") else "")
        for i, q in enumerate(questions)
    )
    engage = ("कितने सही हुए? घर में सबसे ये सवाल पूछिए 🎯"
              if C.MADE_FOR_KIDS else "कितने सही हुए? कमेंट में लिखो 👇")

    # Pehli line me hi keyword + sawaal — ye ~100 char sabse zyada weight
    # rakhte hain, isliye yahan "hook" nahi, saaf topic likhte hain.
    slot_times = ", ".join(C.SLOTS[k] for k in C.SLOT_ORDER)
    if is_paheli:
        first = (f"दिमाग़ घुमाने वाली हिंदी पहेली — {qtext} "
                 f"8 सेकंड का टाइमर, फिर सही जवाब और वजह।")
    else:
        first = (f"ट्रेडिंग क्विज़ ({sub_hi}) — {qtext} "
                 f"8 सेकंड का टाइमर, फिर सही जवाब और वजह।")

    description = f"""{first}

{hook} रोज़ {C.VIDEOS_PER_DAY} नए सवाल — {C.BRAND} को सब्सक्राइब करो! 🔔

{q_block}

{engage}

इस चैनल पर मिलेगा:
• कैंडलस्टिक, इंडिकेटर, ऑर्डर टाइप, F&O, रिस्क मैनेजमेंट, मार्केट फैक्ट
• हर जवाब के साथ एक लाइन में "क्यों"
• रोज़ {C.VIDEOS_PER_DAY} नए शॉर्ट्स — {slot_times}

⚠️ यह चैनल सिर्फ़ शिक्षा के लिए है। हम SEBI-रजिस्टर्ड सलाहकार नहीं हैं; किसी शेयर को खरीदने-बेचने की सलाह नहीं देते। पिछला रिटर्न भविष्य की गारंटी नहीं है।

{hashtags(subs)}"""

    # --- TAGS ----------------------------------------------------------------
    tags = list(BASE_TAGS)
    if is_paheli:
        tags = ["paheli in hindi with answer", "दिमागी पहेलियाँ हिंदी",
                "hindi riddles for class 1 to 5"] + tags
    if any(q.get("topic") == "सबसे" for q in questions):
        tags = ["duniya me sabse bada kaun sa hai", "sabse uncha lamba gehra"] + tags
    for c in subs:
        tags += SUB_TAGS.get(c, [])
    seen, out_tags, total = set(), [], 0
    for t in tags:
        if t in seen or total + len(t) + 1 > 460:
            continue
        seen.add(t); out_tags.append(t); total += len(t) + 1
        if len(out_tags) == 8:        # volume nahi, relevance — 8 pe rok do
            break

    # NOTE: kisi bhi question count ke saath chalna chahiye. Pehle yahan
    # hardcoded questions[1] tha, jo QUESTIONS_PER_VIDEO=1 hote hi IndexError
    # de raha tha — video ban jaata tha par metadata.json likha hi nahi jaata.
    ans_line = "  ".join(f"{i+1}) {labels[q['ans']]}" for i, q in enumerate(questions))
    pinned = None if C.MADE_FOR_KIDS else (
        f"जवाब: {ans_line}\n"
        f"दोनों सही? रोज़ {C.VIDEOS_PER_DAY} नए सवाल आते हैं 🔔\n"
        f"किस पर और सवाल चाहिए — कैंडलस्टिक, इंडिकेटर, F&O या रिस्क?"
    )

    return {
        "title": title[:100],
        "description": description[:4900],
        "tags": out_tags,
        "categoryId": "27",           # Education
        "pinned_comment": pinned,
        "made_for_kids": C.MADE_FOR_KIDS,
        "slot": slot,
        "date": f"{date:%Y-%m-%d}",
        "question_ids": [q["id"] for q in questions],
    }


def hashtags(subs):
    """4 hashtag: broad -> niche -> subject -> brand.

    15 se upar gaye to YouTube saare ignore kar deta hai, isliye ginti kam
    rakhna hi surakshit hai. #shorts nahi lagate — YouTube ab ratio aur
    duration se Short pehchanta hai, aur wo tag har Short pe lag sakta hai
    isliye categorisation me kuch batata nahi.
    """
    tags = ["#StockMarket", "#TradingQuiz"]
    tags.append(SUB_HASH.get(subs[0], "#ShareMarket"))
    tags.append("#PaiseKiPathshala")
    return " ".join(dict.fromkeys(tags))


if __name__ == "__main__":
    import json
    from pick import pick
    qs = pick(2, dry_run=True, slot='noon')
    print(json.dumps(build(qs, "morning"), ensure_ascii=False, indent=2))
