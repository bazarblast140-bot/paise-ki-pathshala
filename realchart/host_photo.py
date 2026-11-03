"""Rajesh on the story thumbnail. A different look every seed.

The portrait comes from the repo's character art (assets/character) or from
photos dropped in assets/rajesh or assets/host. Clothing is recolored so the
frame is not the original navy front-facing drawing, and a file that is only
the blue-shirt front photo is never the one we paste. With no art on disk, a
small drawn host is the fallback. Expression, pose, outfit and background
all move with the seed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSES = ("point", "point_up", "think", "warn", "ok", "neutral")
FRONT = {"neutral", "front", "front_facing"}
# No blue shirt. The original character art is a dark navy front view; tinting
# and this list keep that one photo from being the thumbnail every time.
OUTFITS = (
    ("maroon", (150, 36, 52)),
    ("green", (16, 118, 74)),
    ("ochre", (176, 112, 28)),
    ("purple", (108, 48, 138)),
    ("cream", (214, 198, 164)),
    ("charcoal", (58, 62, 68)),
)
BACKGROUNDS = (
    ("ember", ((48, 16, 18), (120, 48, 24))),
    ("forest", ((10, 42, 32), (18, 70, 52))),
    ("sand", ((72, 48, 24), (132, 92, 44))),
    ("plum", ((42, 16, 48), (90, 36, 70))),
    ("ink", ((16, 20, 32), (36, 40, 58))),
    ("copper", ((64, 28, 16), (148, 78, 30))),
)
_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}


def host_look(seed, root=None):
    """Pose, outfit and background for this seed. Never neutral + blue."""
    root = Path(root) if root else ROOT
    poses = _pose_choices(root)
    digest = _mix(seed)
    pose = poses[digest % len(poses)]
    outfit_name, outfit_rgb = OUTFITS[(digest // 7) % len(OUTFITS)]
    bg_name, bg_rgb = BACKGROUNDS[(digest // 49) % len(BACKGROUNDS)]
    outfit_name, outfit_rgb = _avoid_blue_front(pose["name"], outfit_name, outfit_rgb)
    return {
        "pose": pose["name"],
        "outfit": outfit_name,
        "outfit_rgb": outfit_rgb,
        "background": bg_name,
        "background_rgb": bg_rgb,
        "source": pose["source"],
        "path": pose.get("path"),
        "tint": pose["source"] != "photo",
    }


def host_image(seed, root=None, height=860):
    """RGBA portrait plus the look dict. Always returns a portrait."""
    look = host_look(seed, root=root)
    if look["source"] == "fallback" or not look.get("path"):
        image = _draw_fallback(look["pose"], look["outfit_rgb"])
    else:
        image = _load_pose(look["path"])
        if look["tint"]:
            image = _tint_outfit(image, look["outfit_rgb"])
    image = _fit_height(image, height)
    return image, look


def _avoid_blue_front(pose, outfit_name, outfit_rgb=None):
    """The blue-shirt front-facing photo is not a choice."""
    if pose in FRONT and outfit_name == "blue":
        for name, rgb in OUTFITS:
            if name != "blue":
                return name, rgb
        return "maroon", (150, 36, 52)
    if outfit_rgb is None:
        outfit_rgb = next((rgb for name, rgb in OUTFITS if name == outfit_name), (150, 36, 52))
    return outfit_name, outfit_rgb


def _pose_choices(root):
    files = _portrait_files(root)
    if files:
        return files
    return [{"name": name, "source": "fallback", "path": None} for name in POSES if name != "neutral"]


def _portrait_files(root):
    """Character drawings first, then any host photos that are not the blocked one."""
    found = []
    character = root / "assets" / "character"
    for name in POSES:
        path = character / f"{name}.png"
        if path.is_file() and not _blocked_photo(path):
            found.append({"name": name, "source": "character", "path": str(path)})
    for folder in (root / "assets" / "rajesh", root / "assets" / "host"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in _IMAGE or not path.is_file():
                continue
            if _blocked_photo(path):
                continue
            found.append({"name": path.stem, "source": "photo", "path": str(path)})
    return found


def _blocked_photo(path):
    """The one blue-shirt front-facing file, by name."""
    name = path.stem.lower().replace("-", "_").replace(" ", "_")
    if name in {"blue_shirt", "blue_shirt_front", "front_blue", "rajesh_blue", "blue_front"}:
        return True
    return "blue" in name and "front" in name


def _mix(seed):
    return int.from_bytes(hashlib.sha256(str(seed).encode("utf-8")).digest()[:8], "big")


def _load_pose(path):
    from PIL import Image
    image = Image.open(path).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha < 16 or (red < 12 and green < 12 and blue < 12):
                pixels[x, y] = (red, green, blue, 0)
    return image


def _skin(red, green, blue):
    return red > 170 and green > 110 and blue > 80 and red > blue + 20


def _tint_outfit(image, color):
    """Recolor clothing. Skin and the ink outline stay."""
    pixels = image.load()
    width, height = image.size
    cred, cgreen, cblue = color
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha < 30 or _skin(red, green, blue):
                continue
            if max(red, green, blue) < 42 or max(red, green, blue) > 190:
                continue
            shade = min(1.0, max(0.45, max(red, green, blue) / 90.0))
            pixels[x, y] = (int(cred * shade), int(cgreen * shade), int(cblue * shade), alpha)
    return image


def _fit_height(image, height):
    from PIL import Image
    if image.height == height:
        return image
    width = max(1, int(image.width * height / image.height))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _draw_fallback(pose, color):
    """A simple host when the character files are missing. Pose changes the arms."""
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (480, 860), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    skin = (232, 186, 146, 255)
    ink = (28, 24, 22, 255)
    draw.rounded_rectangle((150, 390, 330, 700), 28, fill=color + (255,))
    draw.ellipse((170, 120, 310, 280), fill=skin, outline=ink, width=4)
    draw.pieslice((170, 110, 310, 230), 200, 340, fill=ink)
    eye_y = 180
    if pose == "think":
        draw.arc((200, 210, 280, 260), 10, 170, fill=ink, width=4)
        draw.line((250, 240, 300, 300), fill=skin, width=16)
    elif pose == "warn":
        draw.line((190, 168, 230, 176), fill=ink, width=4)
        draw.line((250, 176, 290, 168), fill=ink, width=4)
        draw.line((300, 420, 390, 300), fill=color + (255,), width=28)
    elif pose == "ok":
        draw.arc((200, 200, 280, 255), 15, 165, fill=ink, width=4)
        draw.line((300, 430, 380, 340), fill=color + (255,), width=26)
    elif pose == "point_up":
        draw.arc((200, 200, 280, 250), 15, 165, fill=ink, width=4)
        draw.line((300, 430, 360, 220), fill=color + (255,), width=26)
    else:
        draw.arc((200, 200, 280, 250), 15, 165, fill=ink, width=4)
        draw.line((300, 450, 420, 400), fill=color + (255,), width=26)
    draw.ellipse((195, eye_y, 215, eye_y + 18), fill=ink)
    draw.ellipse((265, eye_y, 285, eye_y + 18), fill=ink)
    return image
