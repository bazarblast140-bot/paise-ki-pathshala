"""One main Reel. Channel follow lines live on fixed clips, not a second render."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
INTRO_RTT = ASSETS / "intro_rtt.mp4"
OUTRO_RTT = ASSETS / "outro_rtt.mp4"
OUTRO_PKP = ASSETS / "outro_pkp.mp4"
INTRO_SOURCE = ROOT / "uploads" / "rajesh-technical-trader-intro.mp4"

COMMENT = "अगला कौन सा chart देखना है? Comment करो."
FOLLOW_RTT = "Rajesh Technical Trader ko follow करो"
FOLLOW_PKP = "Paise Ki Pathshala follow करो"
INTRO_LO = 5.5
INTRO_HI = 7.5
INTRO_TAKE = 6.5
OUTRO_SECONDS = 2.0

# Shared with the main Reel so ffmpeg can concat with -c copy.
ENCODE = [
    "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
    "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "30000",
    "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
    "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
]


def _font():
    from PIL import ImageFont
    for path in (
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, 64)
        except OSError:
            continue
    return ImageFont.load_default()


def render_card(text, dest, seconds=OUTRO_SECONDS):
    """A still card plus a tone. No spoken voice, so Edge is never called."""
    from PIL import Image, ImageDraw
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1080, 1920), (14, 16, 20))
    draw = ImageDraw.Draw(image)
    font = _font()
    words = str(text).split()
    lines, cur = [], ""
    for word in words:
        nxt = (cur + " " + word).strip()
        if len(nxt) > 22 and cur:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    y = 860 - 40 * len(lines)
    for line in lines:
        draw.text((80, y), line, fill=(255, 214, 10), font=font)
        y += 84
    png = dest.with_suffix(".png")
    image.save(png)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(png),
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", f"{seconds:.3f}",
        "-vf", "scale=1080:1920,fps=30,format=yuv420p",
        "-af", "volume=0.15,loudnorm=I=-15:TP=-1.5:LRA=11",
        *ENCODE,
        "-shortest",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True)
    finally:
        if png.is_file():
            png.unlink()
    return dest


def trim_intro(src, dest, seconds=INTRO_TAKE):
    """Scale the Rajesh intro to 1080x1920 and keep about 5.5–7.5s."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise FileNotFoundError(src)
    take = min(INTRO_HI, max(INTRO_LO, float(seconds)))
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-t", f"{take:.3f}",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
        "-af", "aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-15:TP=-1.5:LRA=11",
        *ENCODE,
        str(dest),
    ]
    subprocess.run(cmd, check=True)
    return dest


def concat_copy(parts, dest):
    """Stream-copy concat. The main Reel is not encoded again."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".txt")
    lines = []
    for part in parts:
        path = Path(part).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{path}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy",
        str(dest),
    ], check=True)
    return dest


def attach(main, directory):
    """YT is main + Pathshala outro. IG/FB is the RTT intro, main, and RTT outro."""
    main = Path(main)
    directory = Path(directory)
    needed = (("intro_rtt.mp4", INTRO_RTT), ("outro_rtt.mp4", OUTRO_RTT), ("outro_pkp.mp4", OUTRO_PKP))
    missing = [label for label, path in needed if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "channel clips missing: " + ", ".join(missing)
            + ". Trim uploads/rajesh-technical-trader-intro.mp4 with realchart.ends.trim_intro; do not substitute a title card."
        )
    yt = concat_copy([main, OUTRO_PKP], directory / "final_yt.mp4")
    ig = concat_copy([INTRO_RTT, main, OUTRO_RTT], directory / "final_ig.mp4")
    return yt, ig
