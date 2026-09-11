"""Ek jagah pe saare knobs. Baaki koi file me hardcode mat karna."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
ASSETS = ROOT / "assets"
OUT.mkdir(exist_ok=True)

# ---------- video ----------
W, H = 1080, 1920           # YouTube Shorts vertical
FPS = 30
QUESTIONS_PER_VIDEO = 1     # 1 sawaal = ~25s video. Top Hindi GK shorts
                            # 16-30s ke hote hain; 50s wale pe log swipe kar dete hain.
TIMER_SECONDS = 8           # Rajesh spec: 8 second tik-tik
# Length ke do alag number hain — inhe mat mila dena:
#   HARD_LIMIT_SECONDS = platform ka asli rule. Oct 2024 se Shorts 3 MINUTE
#     tak ke ho sakte hain (pehle 60s tha). Isse upar gaya to normal video
#     ban jaata hai, Shorts feed me nahi jaata.
#   TARGET_SECONDS = retention ka target, rule nahi. Shorts ka engagement
#     sweet spot 30-60s hai, isliye hum yahin rehne ki koshish karte hain.
HARD_LIMIT_SECONDS = 180.0
TARGET_SECONDS = 32.0   # ab 1 sawaal hai, video 25-30s me aani chahiye

INTRO_SECONDS = 2.0
REVEAL_SECONDS = 3.2
OUTRO_SECONDS = 3.0
READ_PAD_SECONDS = 0.6      # voice khatam hone ke baad thoda gap

# ---------- brand ----------
# Paise Ki Pathshala — traders ke liye quiz Shorts (retail se pro tak).
BRAND = "PAISE KI PATHSHALA"
HANDLE = ""        # handle confirm nahi hua; footer(handle=True) tabhi use karna

# ---------- COPPA / Made for Kids ----------
# Rajesh ne False choose kiya taaki comments chaalu rahein (engagement CTA
# kaam kare, aur personalized ads se RPM bacha rahe).
#
# Iska matlab: tum YouTube ko bata rahe ho ki ye video bacchon ke liye
# "directed" NAHI hai — audience parents/teachers hai jo bacchon ko padhate
# hain. Content khud bacchon ke syllabus ka hai, isliye ye grey area hai.
# Ye compliance call tumhara hai. Galat label pe FTC/YouTube action ka risk
# rehta hai. Channel-level "audience" setting bhi isi ke saath match rakhna.
MADE_FOR_KIDS = False

CTA_TEXT = ("घर में सबको ये सवाल पूछो!" if MADE_FOR_KIDS
            else "जवाब कमेंट में लिखो 👇")

# Palette — high contrast, mobile pe 3 inch me turant padha jaana chahiye
# Palette channel branding se match ki hai — purple banner, yellow accent,
# white "?" logo. Shorts feed me channel avatar aur video ek hi family lagein.
COLORS = {
    "bg_top":     (124, 58, 237),    # violet-600
    "bg_bottom":  (167, 139, 250),   # violet-400
    "card":       (255, 255, 255),
    "card_shadow": (0, 0, 0, 60),
    "q_text":     (46, 16, 101),     # violet-950
    "opt_text":   (39, 27, 72),
    "opt_label":  (124, 58, 237),    # violet A/B/C/D
    "opt_bg":     (247, 245, 255),
    "correct":    (22, 163, 74),
    "correct_bg": (220, 252, 231),
    "timer_ring": (245, 158, 11),    # amber — purple pe sabse zyada pop karta hai
    "timer_ring_bg": (233, 228, 245),
    "banner":     (76, 29, 149),     # violet-900
    "banner_text": (253, 224, 71),   # yellow-300, banner ke Hindi text jaisa
    "footer":     (60, 60, 80),

    # Multi-colour text — Hindi GK shorts ka standard look. Ye rang SIRF
    # safed card par use hote hain, isliye contrast ki dikkat nahi.
    "word1": (198, 40, 40),     # gehra laal
    "word2": (21, 101, 192),    # gehra neela
    "word3": (46, 125, 50),     # gehra hara
    "word4": (74, 20, 140),     # brand purple
}

# ---------- NEON mode ----------
# BAND. Glow ke saath text ke aas-paas hamesha halka dhundhlapan reh jaata
# tha — Devanagari ki matra aur sir-rekha patli hoti hain, unke upar koi bhi
# blur turant chipak jaata hai. Shorts me 8 second me padhna hota hai,
# isliye chamak se zyada saaf dikhna zaroori hai.
#
# Ab: solid background, SAFED boxes, aur box par gehre rang ka text.
# Yahi format Hindi GK shorts me sabse zyada chalta hai kyunki contrast
# sabse zyada milta hai.
NEON = False

# Bacchon wala confetti traders ke channel par bachkana lagta hai.
CONFETTI = False

COLORS.update({
    # Trading terminal jaisa: gehra navy -> gehra hara. Quiz Pathshala (purple)
    # se alag dikhe taaki feed me dono channel mix na hon.
    "bg_top":        (11, 20, 38),
    "bg_bottom":     (6, 58, 44),
    "card":          (255, 255, 255),   # sawaal aur options, dono ek hi rang
    "opt_bg":        (255, 255, 255),
    "opt_text":      (39, 27, 72),
    "q_text":        (26, 16, 54),
    "opt_label":     (11, 20, 38),
    "banner":        (5, 12, 26),       # bg se thoda gehra
    "banner_text":   (253, 224, 71),    # yellow
    "correct":       (22, 163, 74),
    "correct_bg":    (214, 250, 227),
    "timer_ring":    (229, 57, 53),     # laal — safed ring par sabse alert
    "timer_ring_bg": (228, 224, 238),
    # Safed box par gehre rang — teeno me contrast 7:1 se upar hai
    "word1": (198, 40, 40),     # gehra laal
    "word2": (21, 101, 192),    # gehra neela
    "word3": (27, 94, 32),      # gehra hara
    "word4": (69, 39, 160),     # gehra purple
})

QUESTION_COLORS = ["word1", "word2", "word3", "word4"]
OPTION_COLORS = ["word2", "word3", "word1", "word4"]

FONT_CANDIDATES = [
    str(ASSETS / "fonts" / "NotoSansDevanagari-Bold.ttf"),
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
FONT_CANDIDATES_REG = [
    str(ASSETS / "fonts" / "NotoSansDevanagari-Regular.ttf"),
    "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf",
    "/usr/share/fonts/truetype/google-fonts/Poppins-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

# ---------- audio ----------
SAMPLE_RATE = 44100
TICK_VOLUME = 0.20   # voice ab timer ke dauran chalti hai, tick dabna nahi chahiye
VOICE_VOLUME = 1.0

# ---------- ElevenLabs ----------
# Voice ID env se aata hai — female Hindi voice jo tumne set ki hai.
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVEN_STABILITY = 0.45
ELEVEN_SIMILARITY = 0.80
# 1.08 sirf isliye rakha tha ki video 60s ke andar aa jaye — par wo "limit"
# thi hi nahi (asli limit 3 min hai), aur ElevenLabs edge-tts se waise hi
# kaafi tez hai (video 54s -> 40s). Saaf aur aaram se bolti awaaz chahiye,
# isliye wapas normal speed.
ELEVEN_SPEED = 1.0

# Female Hindi voice. ElevenLabs secrets na hon to seedha ye chalti hai (free).
EDGE_VOICE = os.getenv("EDGE_VOICE", "hi-IN-SwaraNeural")
# Swara normal speed par 50s ka Short banta tha — thoda tez, phir bhi saaf.
EDGE_RATE = os.getenv("EDGE_RATE", "+12%")

# ---------- scheduling ----------
# GitHub Actions me UTC cron use hota hai; ye sirf metadata/labelling ke liye.
# 3 video roz — traders ke time par:
#   premarket  — 08:30 IST, market khulne se pehle
#   postmarket — 16:00 IST, market band hone ke baad
#   night      — 21:00 IST, din ka review
SLOT_ORDER = ["premarket", "postmarket", "night"]
QUIZ_SLOTS = list(SLOT_ORDER)       # sab slot subject-rotate karte hain
PAHELI_SLOTS = []                   # is channel par paheli nahi
SLOTS = {
    "premarket":  "08:30 IST",
    "postmarket": "16:00 IST",
    "night":      "21:00 IST",
}
VIDEOS_PER_DAY = len(SLOT_ORDER)

# Subject rotation (pick.py). "अगर लगाते" wale sawaal ka jawab asli historical
# data se nikla hai — 3 mahine se purana (SEBI: educator ko recent price data nahi).
SUBJECTS = ["कैंडलस्टिक", "इंडिकेटर", "ऑर्डर", "F&O", "रिस्क", "मार्केट फैक्ट", "अगर लगाते"]
PAHELI_SUB = "पहेली"
