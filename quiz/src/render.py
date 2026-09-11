"""1080x1920 frames banata hai. Har frame ek PIL image.

Design rules (rajesh-ui-pro, Shorts variant):
- Mobile pe 3 inch me padhna hai -> question font >= 62px, option >= 52px.
- Safe zone: neeche 380px YouTube UI (title/CTA/like) dhak leta hai -> wahan kuch
  zaroori mat rakho. Timer ko bhi 1500px ke upar rakha hai.
- Right rail: like / dislike / comment / share buttons right kinare par ~900px
  ke baad baithte hain. Options aur neeche wala sab RAIL_X se pehle khatam.
- Contrast pehle, subtlety baad me. Bacchon ka content hai, bold chahiye.
"""
import math
import random
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

import config as C

LABELS = ["A", "B", "C", "D"]

# ---------------------------------------------------------------- emoji icons
# NotoColorEmoji CBDT bitmap font hai — Pillow use SIRF 109px pe khol sakta hai
# ("invalid pixel size" warna). Isliye 109 pe render karke neeche scale karte hain.
EMOJI_NATIVE = 109
_emoji_font = None
_emoji_cache = {}

ICONS = {}
_icons_path = C.DATA / "icons.json"
if _icons_path.exists():
    import json as _json
    ICONS = {k: v for k, v in
             _json.loads(_icons_path.read_text(encoding="utf-8")).items()
             if not k.startswith("_")}


def _emoji_ft():
    global _emoji_font
    if _emoji_font is None:
        import glob
        for f in glob.glob("/usr/share/fonts/**/*Emoji*.ttf", recursive=True):
            try:
                _emoji_font = ImageFont.truetype(f, EMOJI_NATIVE)
                break
            except Exception:
                continue
        if _emoji_font is None:
            _emoji_font = False        # dobara mat dhoondo
    return _emoji_font or None


def emoji_tile(ch, size):
    """Ek emoji ka RGBA tile. None agar emoji font hi na ho — us case me
    layout bina icon ke chal jaata hai, crash nahi hota."""
    key = (ch, size)
    if key in _emoji_cache:
        return _emoji_cache[key]
    ft = _emoji_ft()
    if ft is None:
        return None
    tile = Image.new("RGBA", (EMOJI_NATIVE + 40, EMOJI_NATIVE + 40), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 10), ch, font=ft, embedded_color=True)
    bb = tile.getbbox()
    if not bb:
        _emoji_cache[key] = None
        return None
    tile = tile.crop(bb).resize((size, size), Image.LANCZOS)
    _emoji_cache[key] = tile
    return tile


def icons_for(q):
    """All-or-nothing: chaaron option ka icon ho tabhi dikhao. Aadha set
    bhadda lagta hai aur reading bhi slow karta hai."""
    if not ICONS:
        return None
    got = [ICONS.get(o) for o in q["opts"]]
    if not all(got):
        return None
    return got


# ---------------------------------------------------------------- fonts
def _has_devanagari(path) -> bool:
    """क (U+0915) aur ि (U+093F) dono chahiye — warna matra tut jaayegi."""
    try:
        f = ImageFont.truetype(str(path), 40)
        return bool(f.getmask("कि").getbbox())
    except Exception:
        return False


def _pick(cands, patterns):
    for p in cands:
        if Path(p).exists() and _has_devanagari(p):
            return str(p)
    # CI runner pe path alag ho sakta hai -> system scan
    import glob
    for pat in patterns:
        for p in sorted(glob.glob(f"/usr/share/fonts/**/{pat}", recursive=True)):
            if _has_devanagari(p):
                print(f"[render] font auto-detected: {p}")
                return p
    raise FileNotFoundError(
        "Devanagari font nahi mila.\n"
        "  Ubuntu/CI : sudo apt-get install -y fonts-noto-core fonts-indic\n"
        "  ya manually: assets/fonts/NotoSansDevanagari-Bold.ttf daal do"
    )


FONT_BOLD = _pick(C.FONT_CANDIDATES,
                  ["*Devanagari*Bold*.ttf", "Poppins-Bold.ttf",
                   "*Devanagari*.ttf", "Lohit-Devanagari.ttf"])
FONT_REG = _pick(C.FONT_CANDIDATES_REG,
                 ["*Devanagari*Regular*.ttf", "Poppins-Medium.ttf",
                  "*Devanagari*.ttf", "Lohit-Devanagari.ttf"])
_font_cache = {}


def F(size, bold=True):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    return _font_cache[key]


# ---------------------------------------------------------------- text utils
def text_w(draw, s, font):
    b = draw.textbbox((0, 0), s, font=font)
    return b[2] - b[0]


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_w(draw, trial, font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_lines(draw, text, max_w, max_h, start=76, min_size=44, max_lines=4, bold=True):
    """Font size ghatate jao jab tak text box me fit na ho jaye."""
    size = start
    while size >= min_size:
        f = F(size, bold)
        lines = wrap(draw, text, f, max_w)
        lh = int(size * 1.34)
        if len(lines) <= max_lines and len(lines) * lh <= max_h:
            return f, lines, lh
        size -= 3
    f = F(min_size, bold)
    return f, wrap(draw, text, f, max_w), int(min_size * 1.34)


# Stroke SIRF glow layer par. Core par stroke mat lagana — Devanagari ke
# andar ke patle gaps (स, म, भ) bhar jaate hain aur akshar ek jaise dikhne
# lagte hain. Motai font SIZE se laate hain, stroke se nahi.
NEON_GLOW_STROKE = 3


def neon_text(img, xy, text, font, glow, core=None, pad=80):
    """Neon glow — blur ki kai layers ko ADDITIVELY jodo, phir upar crisp core.

    Pehle ye alpha_composite se ho raha tha, jisse glow bright hi nahi hota:
    alpha-over roshni ko jodta nahi, sirf dhak deta hai. Asli neon additive
    hota hai — isliye ImageChops.add.

    Core pura safed nahi rakha; glow rang ka halka tint hai, warna shabd ka
    rang wash out ho jaata hai aur sab safed dikhne lagta hai.

    Blur sirf shabd ke aas-paas ke box par lagta hai, poori 1080x1920 image
    par nahi — warna har shabd par 3 full-frame blur padte aur render kai
    guna dheema ho jaata.
    """
    d0 = ImageDraw.Draw(img)
    b = d0.textbbox(xy, text, font=font)
    box = (max(0, int(b[0]) - pad), max(0, int(b[1]) - pad),
           min(img.width, int(b[2]) + pad), min(img.height, int(b[3]) + pad))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    w, h = box[2] - box[0], box[3] - box[1]
    off = (xy[0] - box[0], xy[1] - box[1])

    # Glow bhi MOTE shape se banta hai, taaki halo akshar ke bahar rahe aur
    # patli jagahon (matra, sir-rekha) me ghus kar unhe na dhundhla kare.
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(layer).text(off, text, font=font, fill=glow,
                               stroke_width=NEON_GLOW_STROKE, stroke_fill=glow)

    region = img.crop(box).convert("RGB")
    # Sirf BAHAR ki do layers. Pehle ek 5px blur bhi tha jo glyph ke UPAR
    # baithta tha — usi se text dhundhla dikh raha tha. Neon me glow text ke
    # CHAARON OR hona chahiye, text ke upar nahi.
    # Halo ab door aur halka. Pehle 14px @0.85 abhi bhi glyph ke kinaron par
    # chadh raha tha, isliye text dhundhla lag raha tha.
    for radius, gain in ((34, 0.60), (18, 0.45)):
        blurred = layer.filter(ImageFilter.GaussianBlur(radius))
        if gain < 1:
            blurred = blurred.point(lambda v, g=gain: int(v * g))
        region = ImageChops.add(region, blurred)

    region = region.convert("RGBA")
    if core is None:
        # Core lagbhag safed — reference neon me bhi beech safed hota hai aur
        # rang kinaron/glow me. Halka tint (85% safed) rakha hai taaki shabd
        # ka rang phir bhi pehchana jaaye, par edge crisp rahe.
        core = tuple(min(255, int(c * 0.15 + 255 * 0.85)) for c in glow)
    # Core mota (stroke) — isse akshar glow ke beech se saaf ubhar aata hai
    # aur mobile pe 3 inch me bhi turant padha jaata hai.
    ImageDraw.Draw(region).text(off, text, font=font, fill=tuple(core) + (255,))
    img.paste(region, box)


def centered_multicolor(draw, lines, font, lh, cx, top, colors, img=None):
    """Har shabd alag rang me. Devanagari shaping shabd ke ANDAR hoti hai,
    space ke aar-paar nahi — isliye word-by-word draw karna surakshit hai.
    img diya ho aur NEON on ho to har shabd glow ke saath aata hai."""
    space = draw.textlength(" ", font=font)
    wi, y = 0, top
    for ln in lines:
        words = ln.split()
        widths = [draw.textlength(w, font=font) for w in words]
        total = sum(widths) + space * (len(words) - 1)
        x = cx - total / 2
        for w, wd in zip(words, widths):
            col = colors[wi % len(colors)]
            if C.NEON and img is not None:
                neon_text(img, (x, y), w, font, col)
            else:
                draw.text((x, y), w, font=font, fill=col)
            x += wd + space
            wi += 1
        y += lh
    return y


def centered(draw, lines, font, lh, cx, top, fill, stroke=0, stroke_fill=None):
    y = top
    for ln in lines:
        w = text_w(draw, ln, font)
        draw.text((cx - w // 2, y), ln, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=stroke_fill)
        y += lh
    return y


# ---------------------------------------------------------------- background
_bg_cache = {}


def background(seed=1):
    if seed in _bg_cache:
        return _bg_cache[seed]
    img = Image.new("RGB", (C.W, C.H))
    d = ImageDraw.Draw(img)
    top, bot = C.COLORS["bg_top"], C.COLORS["bg_bottom"]
    for y in range(C.H):
        t = y / C.H
        d.line([(0, y), (C.W, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    # confetti — bacchon wali vibe, halka rakha hai taki text na dabe
    rng = random.Random(seed)
    conf = ["#ffffff", "#ffe066", "#ff6b6b", "#4dabf7", "#69db7c"]
    ov = Image.new("RGBA", (C.W, C.H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for _ in range(90 if getattr(C, "CONFETTI", True) else 0):
        x, y = rng.randint(0, C.W), rng.randint(0, C.H)
        r = rng.randint(6, 16)
        col = conf[rng.randrange(len(conf))]
        od.ellipse([x, y, x + r, y + r], fill=col + "55")
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    _bg_cache[seed] = img
    return img


def card(img, box, radius=44, fill=(255, 255, 255), shadow=True, outline=None, width=0):
    if shadow:
        sh = Image.new("RGBA", (C.W, C.H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [box[0] + 6, box[1] + 12, box[2] + 6, box[3] + 14], radius, fill=(0, 0, 0, 70))
        sh = sh.filter(ImageFilter.GaussianBlur(14))
        img.alpha_composite(sh) if img.mode == "RGBA" else img.paste(
            Image.alpha_composite(img.convert("RGBA"), sh).convert("RGB"), (0, 0))
    ImageDraw.Draw(img).rounded_rectangle(box, radius, fill=fill,
                                          outline=outline, width=width)
    return img


# ---------------------------------------------------------------- chrome
def banner(img, qnum=None, total=None, subject=None):
    """Top ribbon + left me 'सवाल 1/2' chip. Right side timer ke liye khaali
    chhoda hai — isliye chip center me nahi hai."""
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, -60, C.W, 210], 60, fill=C.COLORS["banner"])
    f = F(70)
    w = text_w(d, C.BRAND, f)
    d.text(((C.W - w) // 2, 62), C.BRAND, font=f, fill=C.COLORS["banner_text"],
           stroke_width=3, stroke_fill=(90, 8, 42))
    # 1 sawaal wali video me "सवाल 1/1" bekaar hai — sirf subject chip dikhao
    if qnum and total > 1:
        chip = f"सवाल {qnum}/{total}"
        cf = F(46)
        cw = text_w(d, chip, cf)
        d.rounded_rectangle([70, 240, 70 + cw + 68, 336], 48,
                            fill=C.COLORS["card"] if C.NEON else (255, 255, 255),
                            outline=C.COLORS["banner_text"] if C.NEON else None,
                            width=3 if C.NEON else 0)
        d.text((104, 256), chip, font=cf,
               fill=C.COLORS["banner_text"] if C.NEON else C.COLORS["banner"])
    if subject:
        sf = F(44)
        sw = text_w(d, subject, sf)
        # Chip left me. Timer right me rehta hai, isliye takraav nahi hota.
        x0 = 70 if not (qnum and total > 1) else 70 + text_w(d, f"सवाल {qnum}/{total}", F(46)) + 92
        d.rounded_rectangle([x0, 244, x0 + sw + 60, 332], 44,
                            fill=C.COLORS["banner_text"])
        d.text((x0 + 30, 258), subject, font=sf, fill=C.COLORS["banner"])
    return img


# YouTube Shorts ka apna UI (channel row, description, "Share your video"
# bar) lagbhag 1450px se neeche sab dhak leta hai. Pehle footer 1470 par
# tha aur handle 1584 par — dono UI ke PEECHE chale jaate the.
# Ab FOOTER_Y ek CEILING hai, floor nahi: footer content ke turant neeche
# baithta hai aur is line se kabhi neeche nahi jaata.
FOOTER_Y = 1330
SAFE_BOTTOM = 1420       # is se neeche kuch bhi mat rakho

# YouTube Shorts ka right action rail (like, dislike, comment, share, remix)
# video ke right kinare par, lagbhag beech se neeche tak rehta hai — 1080px
# wide frame me ~900px ke baad. Pehle options 1010px tak jaate the aur sahi
# jawab ka tick 930px par banta tha, yaani theek like/comment button ke
# peeche; viewer ko like button content me ghula hua dikhta tha.
# Isliye screen ke neeche wale hisse (options, इशारा, badge, footer) ka
# column COL_L se RAIL_X tak hai. Upar ka card aur timer rail se upar hain,
# wo poori width le sakte hain.
RAIL_X = 890             # is x ke right me (options ke level se neeche) kuch nahi
COL_L = 70
COL_CX = (COL_L + RAIL_X) // 2


EMOJI_STRIP = "👇👆✅❌🎯🔔🤔💡🔥"


def footer(img, text=None, y=FOOTER_Y, handle=False, after=None):
    """Screen text me emoji mat daalo — Poppins me emoji glyph nahi hai aur
    tofu box (□) render ho jaata hai. Option icons alag emoji font se aate hain.

    after = upar wale content ka bottom. FOOTER_Y ceiling hai, isliye lambe
    sawaal par footer UPAR khinch kar "सही जवाब!" badge ke upar chadh jaata
    tha. Ab jagah na ho to footer banta hi nahi — CTA pinned comment aur
    description me waise bhi hai."""
    d = ImageDraw.Draw(img)
    y = min(y, FOOTER_Y)          # safe zone se bahar kabhi nahi
    if after is not None and y < after + 12:
        return img
    t = text or C.CTA_TEXT
    for ch in EMOJI_STRIP:
        t = t.replace(ch, "")
    t = " ".join(t.split())
    f = F(44)
    w = text_w(d, t, f)
    pill = C.COLORS["card"] if C.NEON else (255, 255, 255)
    ink = C.COLORS["banner_text"] if C.NEON else C.COLORS["banner"]
    d.rounded_rectangle([COL_CX - w // 2 - 40, y, COL_CX + w // 2 + 40, y + 88],
                        44, fill=pill,
                        outline=ink if C.NEON else None, width=3 if C.NEON else 0)
    d.text((COL_CX - w // 2, y + 18), t, font=f, fill=ink)
    # Handle line hata di: wo 1584px par baithti thi (poori tarah YouTube ke
    # UI ke peeche) aur YouTube khud channel ka naam wahin dikhata hai —
    # duplicate tha. Chahiye to handle=True karo.
    if handle:
        hf = F(38, bold=False)
        hw = text_w(d, C.HANDLE, hf)
        d.text(((C.W - hw) // 2, y + 114), C.HANDLE, font=hf,
               fill=(255, 255, 255), stroke_width=2, stroke_fill=(60, 30, 90))
    return img


TIMER_CX, TIMER_CY, TIMER_R = 928, 288, 80


def timer_ring(img, remaining, total):
    """Top-right countdown ring. Yahan isliye rakha hai kyunki neeche options
    ki jagah hai — overlap ka risk zero. remaining = float seconds."""
    d = ImageDraw.Draw(img)
    cx, cy, r = TIMER_CX, TIMER_CY, TIMER_R
    box = [cx - r, cy - r, cx + r, cy + r]
    d.ellipse([cx - r - 8, cy - r - 8, cx + r + 8, cy + r + 8],
              fill=C.COLORS["card"] if C.NEON else (255, 255, 255))
    d.arc(box, 0, 360, fill=C.COLORS["timer_ring_bg"], width=20)
    frac = max(0.0, min(1.0, remaining / total))
    if frac > 0:
        d.arc(box, -90, -90 + 360 * frac, fill=C.COLORS["timer_ring"], width=20)
    secs = max(0, math.ceil(remaining - 1e-6))
    f = F(86)
    s = str(secs)
    b = d.textbbox((0, 0), s, font=f)
    d.text((cx - (b[2] - b[0]) // 2 - b[0], cy - (b[3] - b[1]) // 2 - b[1]),
           s, font=f, fill=C.COLORS["timer_ring"])
    return img


# ---------------------------------------------------------------- panels
CARD_TOP = 348
CARD_X = 58
# Card ki height cap zaroori hai: lamba sawaal card ko bada karta hai,
# jo options ko neeche dhakelta hai, aur phir CTA/ishara YouTube ke UI
# (1420px se neeche) me chale jaate hain.
CARD_MIN_H, CARD_MAX_H = 240, 352
OPT_H = 112
OPT_GAP = 20
OPT_PAD = 34            # card ke neeche se options tak ka gap


def question_panel(q, qnum, total, highlight=None, dim_others=False,
                   show_options=4):
    """show_options: kitne option dikhane hain (0-4).

    Shorts me sabse bada dushman STATIC screen hai. Pehle sawaal aur chaaron
    option ek saath aa jaate the aur phir 12 second tak kuch hilta hi nahi
    tha — viewer ke paas swipe karne ke alawa kuch bacha hi nahi tha. Ab
    option ek-ek karke aate hain, thik us waqt jab wo bole ja rahe hote hain.
    """
    """highlight=None -> normal. highlight=idx -> us option ko green.
    Ye base image hai; timer per-frame iske upar paste hota hai.
    Return: (image, options_block_end_y) — reveal badge/footer isi se position hote hain."""
    img = background(qnum).convert("RGB")
    banner(img, qnum, total, q.get("sub"))

    d = ImageDraw.Draw(img)
    inner_w = C.W - 2 * CARD_X - 96
    f, lines, lh = fit_lines(d, q["q"], inner_w, CARD_MAX_H - 76,
                             start=92, min_size=56, max_lines=3)
    card_h = max(CARD_MIN_H, min(CARD_MAX_H, len(lines) * lh + 76))
    card_box = [CARD_X, CARD_TOP, C.W - CARD_X, CARD_TOP + card_h]
    card(img, card_box, fill=C.COLORS["card"])

    d = ImageDraw.Draw(img)
    top = CARD_TOP + (card_h - len(lines) * lh) // 2
    if C.NEON:
        img = img.convert("RGBA")
        d = ImageDraw.Draw(img)
    centered_multicolor(d, lines, f, lh, C.W // 2, top,
                        [C.COLORS[k] for k in C.QUESTION_COLORS], img=img)

    opt_top = card_box[3] + OPT_PAD
    icons = icons_for(q)
    q_opts = q["opts"][:max(0, min(4, show_options))]
    text_x = 296 if icons else 206
    for i, opt in enumerate(q_opts):
        y = opt_top + i * (OPT_H + OPT_GAP)
        box = [COL_L, y, RAIL_X, y + OPT_H]
        is_hit = (highlight is not None and i == highlight)
        if is_hit:
            bg, fg, lab = C.COLORS["correct_bg"], C.COLORS["correct"], C.COLORS["correct"]
            d.rounded_rectangle(box, 34, fill=bg, outline=C.COLORS["correct"], width=8)
        else:
            faded = dim_others and highlight is not None
            # Faded rang theme ke hisaab se — dark mode me halka grey pill
            # bilkul toota hua dikhta hai.
            if faded:
                bg = (30, 22, 44) if C.NEON else (235, 235, 240)
            else:
                bg = C.COLORS["opt_bg"]
            # Har option ka apna rang — A neela, B hara, C laal, D purple
            dim_fg = (110, 96, 140) if C.NEON else (150, 150, 160)
            fg = (dim_fg if faded
                  else C.COLORS[C.OPTION_COLORS[i % len(C.OPTION_COLORS)]])
            lab = dim_fg if faded else C.COLORS["opt_label"]
            d.rounded_rectangle(box, 34, fill=bg,
                                outline=(70, 48, 110) if C.NEON else (220, 222, 235),
                                width=4)

        cx = 134
        d.ellipse([cx - 42, y + 14, cx + 42, y + OPT_H - 14],
                  fill=bg if C.NEON else (255, 255, 255), outline=lab, width=6)
        lf = F(50)
        lb = d.textbbox((0, 0), LABELS[i], font=lf)
        d.text((cx - (lb[2] - lb[0]) // 2 - lb[0], y + OPT_H // 2 - (lb[3] + lb[1]) // 2),
               LABELS[i], font=lf, fill=lab)

        if icons:
            tile = emoji_tile(icons[i], 72)
            if tile is not None:
                if dim_others and not is_hit:
                    faded_tile = tile.copy()
                    faded_tile.putalpha(tile.getchannel("A").point(lambda a: a // 3))
                    tile = faded_tile
                img.paste(tile, (200, y + (OPT_H - 72) // 2), tile)

        of = F(64)
        max_w = RAIL_X - 110 - text_x      # 110 = tick ki jagah + padding
        while text_w(d, opt, of) > max_w and of.size > 34:
            of = F(of.size - 2)
        ob = d.textbbox((0, 0), opt, font=of)
        oy = y + OPT_H // 2 - (ob[3] + ob[1]) // 2
        if C.NEON and not (dim_others and not is_hit):
            neon_text(img, (text_x, oy), opt, of, fg)
        else:
            d.text((text_x, oy), opt, font=of, fill=fg)

        if is_hit:
            # Poppins me U+2713 glyph nahi hai (tofu box aata hai) -> haath se draw
            tx, ty = RAIL_X - 76, y + OPT_H // 2
            d.line([(tx - 26, ty + 2), (tx - 8, ty + 22), (tx + 28, ty - 24)],
                   fill=C.COLORS["correct"], width=11, joint="curve")

    opt_end = opt_top + 4 * (OPT_H + OPT_GAP) - OPT_GAP
    if img.mode == "RGBA":
        img = img.convert("RGB")
    return img, opt_end


def cta_panel(q, qnum, total):
    """Timer khatam, jawab abhi nahi. Options wahi rehte hain (viewer abhi bhi
    soch raha hai) aur upar se subscribe ka banner aata hai."""
    img, opt_end = question_panel(q, qnum, total)
    d = ImageDraw.Draw(img)
    t = "सब्सक्राइब करें"
    f = F(68)
    w = text_w(d, t, f)
    by = opt_end + 26
    d.rounded_rectangle([COL_CX - w // 2 - 52, by, COL_CX + w // 2 + 52, by + 104],
                        50, fill=C.COLORS["banner"], outline=C.COLORS["banner_text"],
                        width=6)
    d.text((COL_CX - w // 2, by + 16), t, font=f, fill=C.COLORS["banner_text"])
    footer(img, "जवाब आ रहा है…", y=max(FOOTER_Y, by + 140), after=by + 104)
    return img


def hint_panel(q, qnum, total):
    """Wajah dikhti hai, par sahi option abhi highlight NAHI hota — warna
    jawab yahin khul jaayega aur aakhri beat ka koi matlab nahi bachega."""
    img, opt_end = question_panel(q, qnum, total)
    d = ImageDraw.Draw(img)
    import tts as _tts
    why = (q.get("why") or "").strip()
    if why:
        why = _tts.mask_answer(why, q, "_____")
    y = opt_end + 20
    if why:
        # Options 1254px par khatam hote hain aur safe zone 1420 par. Yaani
        # ishara card ke paas sirf ~140px hai — isliye 2 line, chhota font,
        # aur kam padding. 3-4 line ka card seedha YouTube ke UI me ghus jaata tha.
        wf, wlines, wlh = fit_lines(d, why, RAIL_X - 80 - 80, 120, start=38,
                                    min_size=27, max_lines=2, bold=False)
        h = len(wlines) * wlh + 40
        top = y + 10
        d.rounded_rectangle([80, top, RAIL_X, top + h], 30,
                            fill=C.COLORS["card"] if C.NEON else (255, 255, 255),
                            outline=C.COLORS["timer_ring"], width=5)
        lf = F(34)
        bg_stroke = C.COLORS["bg_top"] if C.NEON else (255, 255, 255)
        d.text((116, top - 22), "इशारा", font=lf, fill=C.COLORS["timer_ring"],
               stroke_width=6, stroke_fill=bg_stroke)
        centered(d, wlines, wf, wlh, (80 + RAIL_X) // 2, top + 30, C.COLORS["opt_text"])
        y = top + h
    # Yahan footer chip NAHI. "इशारा" card khud hi message hai; ek aur chip
    # lagane se content safe zone (1420px) se bahar chala jaata tha.
    return img


def reveal_panel(q, qnum, total):
    img, opt_end = question_panel(q, qnum, total, highlight=q["ans"], dim_others=True)
    d = ImageDraw.Draw(img)

    t = "सही जवाब!"
    f = F(64)
    w = text_w(d, t, f)
    by = opt_end + 22
    d.rounded_rectangle([COL_CX - w // 2 - 44, by, COL_CX + w // 2 + 44, by + 96],
                        46, fill=C.COLORS["correct"])
    d.text((COL_CX - w // 2, by + 12), t, font=f, fill=(255, 255, 255))
    y = by + 96

    footer(img, C.CTA_TEXT, y=max(FOOTER_Y, y + 34), after=y)
    return img


def title_panel(title, subtitle, seed=9, big=True):
    img = background(seed).convert("RGB")
    banner(img)
    card(img, [58, 520, C.W - 58, 1180], fill=C.COLORS["card"])
    d = ImageDraw.Draw(img)
    f, lines, lh = fit_lines(d, title, C.W - 240, 380, start=104 if big else 80,
                             min_size=56, max_lines=3)
    top = 620 + (380 - len(lines) * lh) // 2
    y = centered(d, lines, f, lh, C.W // 2, top, C.COLORS["q_text"])
    sf, slines, slh = fit_lines(d, subtitle, C.W - 260, 200, start=58, min_size=40,
                                max_lines=3, bold=False)
    centered(d, slines, sf, slh, C.W // 2, y + 40, (90, 96, 130))
    footer(img)
    return img
