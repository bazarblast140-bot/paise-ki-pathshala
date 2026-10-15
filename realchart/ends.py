"""One shared Reel. YouTube and social differ by the follow card, and social adds the intro."""
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
# The source is 10s. The blast is the opening; the name is on screen at about 6–8s.
# Start just after the black frame and keep 7.5s, so the name sits in the last ~2s.
INTRO_LO = 5.5
INTRO_HI = 7.5
INTRO_START = 0.5
INTRO_TAKE = 7.5
OUTRO_SECONDS = 2.0

# Shared with the main Reel so ffmpeg can concat with -c copy.
ENCODE = [
    "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
    "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "30000",
    "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
    "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
]


def _font(size=72):
    from realchart.fonts import font
    return font(size)


def render_card(text, dest, seconds=OUTRO_SECONDS):
    """A still card with silent audio. No voice and no tone (the old 440 Hz tone,
    loudness-normalised to -15 LUFS, was the loud beep at the end of the IG Reel)."""
    from PIL import Image, ImageDraw
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1080, 1920), (14, 16, 20))
    draw = ImageDraw.Draw(image)
    font = _font(72)
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
    # A solid panel, so the card is never a near-black void (blackdetect gate).
    top = 900 - 48 * len(lines) - 70
    draw.rounded_rectangle((70, top, 1010, top + 96 * len(lines) + 120), 36, fill=(255, 214, 10))
    y = top + 60
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text(((1080 - width) / 2, y), line, fill=(14, 16, 20), font=font)
        y += 96
    png = dest.with_suffix(".png")
    image.save(png)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{seconds:.3f}",
        "-vf", "scale=1080:1920,fps=30,format=yuv420p",
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


def trim_intro(src, dest, seconds=INTRO_TAKE, start=INTRO_START):
    """Keep the blast plus the name, then scale and pad to 1080x1920. No substitute footage."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise FileNotFoundError(src)
    take = min(INTRO_HI, max(INTRO_LO, float(seconds)))
    start = max(0.0, float(start))
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-ss", f"{start:.3f}",
        "-t", f"{take:.3f}",
        "-vf", (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p"
        ),
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
    """YT is the shared Reel plus the Pathshala follow card. IG/FB prepends Rajesh's intro and uses his follow card."""
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
