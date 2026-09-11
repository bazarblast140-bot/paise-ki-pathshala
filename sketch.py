"""Hand-drawn (Excalidraw-style) drawing helpers.

Everything here is deterministic: the seed comes from the shot index, so a given
shot renders identically on every run. Wobble that changes between renders would
read as flicker, not as hand-drawn.

Two layers of "hand-drawn":
  - PIL primitives below: marker highlights, rough underlines and boxes, arrows.
  - matplotlib's xkcd() mode in chart_png(): sketchy axes and lines for real data.
"""
import math, random

from PIL import Image, ImageDraw, ImageFilter

# Wealth-Logic-style light board (white canvas, dark ink)
INK = (28, 28, 32)
HIGHLIGHT = (245, 197, 66)      # amber marker
POP = (220, 60, 50)             # red for "against you" / down arrows
COOL = (40, 120, 200)           # calm blue
GREEN = (34, 160, 90)
PAPER = (250, 250, 252)         # near-white background


def _rng(seed):
    return random.Random(seed)


def _wobble(p1, p2, rng, jitter, steps=10):
    """Points along p1->p2 with perpendicular noise — one hand-drawn stroke."""
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = x2 - x1, y2 - y1
    length = max(1.0, math.hypot(dx, dy))
    nx, ny = -dy / length, dx / length
    pts = []
    for i in range(steps + 1):
        t = i / steps
        # taper the jitter at the ends so strokes meet cleanly
        damp = math.sin(math.pi * t) ** 0.5
        off = rng.uniform(-jitter, jitter) * damp
        pts.append((x1 + dx * t + nx * off, y1 + dy * t + ny * off))
    return pts


def rough_line(d, p1, p2, color, width=6, seed=0, passes=2, jitter=3.0):
    """A line drawn the way a human would: two overlapping imperfect strokes."""
    rng = _rng(seed)
    for _ in range(passes):
        d.line(_wobble(p1, p2, rng, jitter), fill=color, width=width, joint="curve")


def rough_rect(d, box, color, width=5, seed=0, jitter=3.0):
    x0, y0, x1, y1 = box
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i in range(4):
        rough_line(d, corners[i], corners[(i + 1) % 4], color, width, seed + i, 2, jitter)


def marker_highlight(size, box, color, seed=0, alpha=238):
    """A marker swipe as its own RGBA layer, composited UNDER the text.

    Near-opaque on purpose: a translucent amber over a near-black canvas goes
    muddy olive. A solid swipe with dark text on top is what actually reads —
    and it is the strongest attention cue on the frame, so it has to be clean.
    Draw the highlighted words in PAPER, not INK, over this.
    """
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rng = _rng(seed)
    x0, y0, x1, y1 = box
    x0 -= rng.randint(12, 24)
    x1 += rng.randint(12, 24)

    # one filled shape with hand-drawn top and bottom edges — no nib banding
    steps = 16
    top = [(x0 + (x1 - x0) * i / steps, y0 + rng.uniform(-5, 5)) for i in range(steps + 1)]
    bottom = [(x0 + (x1 - x0) * i / steps, y1 + rng.uniform(-5, 5)) for i in range(steps, -1, -1)]
    d.polygon(top + bottom, fill=color + (alpha,))
    return layer.filter(ImageFilter.GaussianBlur(2.0))


def rough_underline(d, x0, x1, y, color, width=7, seed=0):
    rough_line(d, (x0, y), (x1, y + _rng(seed).randint(-3, 3)), color, width, seed, 2, 3.5)


def rough_arrow(d, p1, p2, color, width=6, seed=0, head=26):
    rough_line(d, p1, p2, color, width, seed, 2, 2.5)
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    for sign in (+1, -1):
        a = ang + sign * math.radians(150)
        tip = (p2[0] + head * math.cos(a), p2[1] + head * math.sin(a))
        rough_line(d, p2, tip, color, width, seed + 7, 1, 1.5)


def chart_png(chart, path, size=(1920, 1080)):
    """Hand-drawn chart via matplotlib xkcd — light board, multi-bar preferred."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = list(chart.get("labels") or [])
    values = list(chart.get("values") or [])
    if not labels or not values or len(labels) != len(values):
        return False
    try:
        values = [float(v) for v in values]
    except (TypeError, ValueError):
        return False

    # Single-bar charts look empty — expand to a simple before/after style pair
    if len(values) == 1:
        labels = [str(labels[0])[:18], "context"]
        values = [values[0], max(values[0] * 0.35, 1.0)]

    ink = "#1C1C20"
    accent = "#F5C542"
    accent2 = "#DC3C32"
    paper = "#FAFAFC"
    w, h = size
    colors = [accent if i % 2 == 0 else accent2 for i in range(len(values))]
    if len(values) >= 3:
        colors = [accent, "#2880C8", accent2][:len(values)]
        while len(colors) < len(values):
            colors.append(accent)

    with plt.xkcd(scale=1.0, length=100, randomness=2):
        fig, ax = plt.subplots(figsize=(w / 100, h / 100), dpi=100)
        fig.patch.set_facecolor(paper)
        fig.patch.set_linewidth(0)
        ax.set_facecolor(paper)

        kind = str(chart.get("type", "bar")).lower()
        if kind == "line":
            ax.plot(range(len(values)), values, color=accent, linewidth=4, marker="o",
                    markersize=12, markerfacecolor=accent, markeredgecolor=ink)
        else:
            ax.bar(range(len(values)), values, color=colors[:len(values)], width=0.55,
                   edgecolor=ink, linewidth=1.5)

        for i, v in enumerate(values):
            ax.annotate(f"{v:g}", (i, v), textcoords="offset points", xytext=(0, 12),
                        ha="center", color=ink, fontsize=22, fontweight="bold")

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels([str(x)[:16] for x in labels], color=ink, fontsize=20)
        ax.tick_params(colors=ink, labelsize=18)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("bottom", "left"):
            ax.spines[spine].set_color(ink)
            ax.spines[spine].set_linewidth(2.0)
        ax.set_ylim(0, max(values) * 1.32 if max(values) > 0 else 1)
        fig.subplots_adjust(left=0.10, right=0.95, top=0.70, bottom=0.32)
        fig.savefig(path, facecolor=paper)
        plt.close(fig)
    return True


def draw_host(size=520):
    """Cleaner cartoon lab-coat host — rounded proportions, less stick-figure."""
    from PIL import Image, ImageDraw, ImageFilter
    s = size
    H = int(s * 1.45)
    img = Image.new("RGBA", (int(s * 1.15), H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (25, 28, 35, 255)
    skin = (255, 214, 186, 255)
    coat = (255, 255, 255, 255)
    hair = (32, 32, 38, 255)
    tie = (30, 30, 40, 255)

    def xy(a, b):
        return (int(a * s), int(b * s))

    # legs / trousers hint
    d.rounded_rectangle([xy(0.32, 1.05), xy(0.48, 1.38)], radius=10, fill=(40, 50, 70, 255))
    d.rounded_rectangle([xy(0.52, 1.05), xy(0.68, 1.38)], radius=10, fill=(40, 50, 70, 255))
    # shoes
    d.ellipse([xy(0.28, 1.32), xy(0.50, 1.42)], fill=ink)
    d.ellipse([xy(0.50, 1.32), xy(0.72, 1.42)], fill=ink)

    # coat body (rounded)
    d.rounded_rectangle([xy(0.18, 0.42), xy(0.82, 1.12)], radius=28, fill=coat, outline=ink, width=4)
    # inner shirt
    d.polygon([xy(0.42, 0.44), xy(0.58, 0.44), xy(0.55, 1.05), xy(0.45, 1.05)], fill=(240, 244, 250, 255))
    # tie
    d.polygon([xy(0.47, 0.45), xy(0.53, 0.45), xy(0.55, 0.72), xy(0.50, 0.78), xy(0.45, 0.72)], fill=tie)
    # buttons
    for by in (0.58, 0.70, 0.82, 0.94):
        d.ellipse([xy(0.485, by), xy(0.515, by + 0.03)], fill=ink)

    # left arm (down)
    d.line([xy(0.22, 0.55), xy(0.10, 0.85)], fill=skin, width=22)
    d.ellipse([xy(0.04, 0.80), xy(0.16, 0.92)], fill=skin, outline=ink, width=3)
    # right arm pointing
    d.line([xy(0.78, 0.55), xy(1.00, 0.42)], fill=skin, width=22)
    d.ellipse([xy(0.95, 0.35), xy(1.10, 0.50)], fill=skin, outline=ink, width=3)
    # coat sleeve cuffs
    d.ellipse([xy(0.14, 0.50), xy(0.28, 0.62)], outline=ink, width=3)
    d.ellipse([xy(0.72, 0.50), xy(0.86, 0.62)], outline=ink, width=3)

    # neck
    d.rectangle([xy(0.43, 0.36), xy(0.57, 0.46)], fill=skin)
    # head
    d.ellipse([xy(0.28, 0.02), xy(0.72, 0.40)], fill=skin, outline=ink, width=4)
    # hair (side-part)
    d.chord([xy(0.28, 0.00), xy(0.72, 0.28)], 180, 0, fill=hair)
    d.ellipse([xy(0.28, 0.08), xy(0.42, 0.28)], fill=hair)
    # eyes (friendly)
    d.ellipse([xy(0.40, 0.16), xy(0.47, 0.22)], fill=ink)
    d.ellipse([xy(0.53, 0.16), xy(0.60, 0.22)], fill=ink)
    d.ellipse([xy(0.415, 0.17), xy(0.44, 0.195)], fill=(255, 255, 255, 220))
    d.ellipse([xy(0.545, 0.17), xy(0.57, 0.195)], fill=(255, 255, 255, 220))
    # brows
    d.arc([xy(0.38, 0.12), xy(0.48, 0.18)], 200, 340, fill=ink, width=3)
    d.arc([xy(0.52, 0.12), xy(0.62, 0.18)], 200, 340, fill=ink, width=3)
    # smile
    d.arc([xy(0.40, 0.22), xy(0.60, 0.34)], 15, 165, fill=ink, width=3)

    return img



def load_host_pose(pose="neutral", height=720):
    """Load character PNG from assets/character/, else draw_host fallback.

    pose: neutral | point | point_up | think | warn | ok
    """
    from pathlib import Path
    from PIL import Image
    root = Path(__file__).resolve().parent
    name = {
        "neutral": "neutral.png",
        "point": "point.png",
        "point_up": "point_up.png",
        "think": "think.png",
        "warn": "warn.png",
        "warning": "warn.png",
        "ok": "ok.png",
        "positive": "ok.png",
    }.get(str(pose).lower().strip(), "neutral.png")
    path = root / "assets" / "character" / name
    # fallback chain
    for candidate in (path, root / "assets" / "character" / "neutral.png", root / "assets" / "character" / "point.png"):
        if candidate.exists():
            img = Image.open(candidate).convert("RGBA")
            if height and img.height != height:
                w = int(img.width * height / img.height)
                img = img.resize((w, height), Image.Resampling.LANCZOS)
            # transparent top+bottom pad so head/feet never sit on frame edge
            from PIL import Image as _Image
            pad_top = max(20, img.height // 20)
            pad_bot = max(12, img.height // 30)
            canvas = _Image.new("RGBA", (img.width, img.height + pad_top + pad_bot), (0, 0, 0, 0))
            canvas.paste(img, (0, pad_top), img)
            return canvas
    # procedural fallback
    host = draw_host(int(height * 0.72))
    if host.height != height:
        w = int(host.width * height / host.height)
        host = host.resize((w, height), Image.Resampling.LANCZOS)
    return host


def draw_icon(name, size=200):
    """Minimal flat icons when asset PNG missing."""
    from PIL import Image, ImageDraw
    name = (name or "").lower().strip()
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (28, 28, 32, 255)
    accent = (245, 197, 66, 255)
    red = (220, 60, 50, 255)
    m = size * 0.1
    if name in ("car", "vehicle"):
        d.rounded_rectangle([m, size*0.4, size-m, size*0.7], radius=12, fill=red, outline=ink, width=3)
        d.ellipse([size*0.2, size*0.62, size*0.38, size*0.8], fill=ink)
        d.ellipse([size*0.62, size*0.62, size*0.8, size*0.8], fill=ink)
    elif name in ("cash", "money", "savings"):
        d.rounded_rectangle([m, m*2, size-m, size-m*2], radius=10, fill=(86, 211, 140, 255), outline=ink, width=3)
        d.ellipse([size*0.35, size*0.35, size*0.65, size*0.65], outline=ink, width=4)
    elif name in ("calendar", "month", "time"):
        d.rounded_rectangle([m, m*2, size-m, size-m], radius=10, fill=(250, 250, 252, 255), outline=ink, width=3)
        d.rectangle([m, m*2, size-m, size*0.35], fill=red)
        d.text((size*0.35, size*0.5), "31", fill=ink)
    elif name in ("loan", "paper", "debt"):
        d.rounded_rectangle([m*1.5, m, size-m*1.5, size-m], radius=6, fill=(250, 250, 252, 255), outline=ink, width=3)
        for i in range(4):
            y = size*0.35 + i * size*0.12
            d.line([size*0.28, y, size*0.72, y], fill=ink, width=3)
    elif name in ("loop", "cycle", "again"):
        d.arc([m, m, size-m, size-m], 30, 300, fill=ink, width=10)
        d.polygon([size*0.75, size*0.2, size*0.95, size*0.35, size*0.7, size*0.4], fill=ink)
    elif name in ("arrow_down", "down", "loss"):
        d.polygon([size*0.45, m, size*0.55, m, size*0.55, size*0.55,
                   size*0.75, size*0.55, size*0.5, size*0.9, size*0.25, size*0.55,
                   size*0.45, size*0.55], fill=red, outline=ink)
    elif name in ("arrow_up", "up", "gain"):
        d.polygon([size*0.5, m, size*0.75, size*0.45, size*0.55, size*0.45,
                   size*0.55, size*0.9, size*0.45, size*0.9, size*0.45, size*0.45,
                   size*0.25, size*0.45], fill=(34, 160, 90, 255), outline=ink)
    else:
        # default: amber circle badge
        d.ellipse([m, m, size-m, size-m], fill=accent, outline=ink, width=4)
    return img

