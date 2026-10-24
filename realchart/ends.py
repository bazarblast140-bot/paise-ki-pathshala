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
# The source is 10s; "RAJESH TECHNICAL TRADER" is fully visible from ~6.6s (frame-checked); 6.0–6.5s is still the blast.
# Keep only that name reveal, 1.4s, no chart animation before it (Rajesh, 15-Oct).
INTRO_LO = 1.0
INTRO_HI = 2.0
INTRO_START = 6.6
INTRO_TAKE = 1.4
OUTRO_SECONDS = 1.5  # with a slow push-in; no static dead air (Rajesh, 16-Oct)

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


BED = ASSETS / "bed" / "bed.wav"
VOICED_TAIL_SEC = 0.6


def end_card_audio(dest, voice_text):
    """The spoken follow line (Edge) over the music bed, so the end card is never silent.
    Returns (wav path, seconds) or (None, None) when Edge is unavailable."""
    from realchart.voice import EdgeUnavailable, edge_speech, wav_seconds
    dest = Path(dest)
    line = dest.with_name(dest.stem + "-line.wav")
    try:
        edge_speech(voice_text, line)
    except EdgeUnavailable:
        return None, None
    seconds = wav_seconds(line) + VOICED_TAIL_SEC
    mixed = dest.with_name(dest.stem + "-audio.wav")
    inputs = ["-i", str(line)]
    if BED.is_file():
        inputs += ["-stream_loop", "-1", "-i", str(BED)]
        graph = (f"[1:a]volume=0.5,afade=t=in:d=0.3,afade=t=out:st={max(0.0, seconds - 0.5):.3f}:d=0.5[bed];"
                 f"[0:a]apad[v];[v][bed]amix=inputs=2:duration=longest:normalize=0[a]")
    else:
        graph = "[0:a]apad[a]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[a]",
                    "-t", f"{seconds:.3f}", "-ar", "48000", "-ac", "2", str(mixed)], check=True)
    return mixed, seconds


def render_end_card(dest, background=None, name="Rajesh Technical Trader", call="follow करो", seconds=OUTRO_SECONDS, extra=None, voice_text=None):
    """Full-frame end card: the chart blurred behind, the big channel name, a follow button.
    With voice_text the card is as long as the spoken follow line plus music; otherwise silent."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    from realchart.fonts import font
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if background and Path(background).is_file():
        with Image.open(background) as opened:
            shot = opened.convert("RGB")
        scale = max(1080 / shot.width, 1920 / shot.height)
        shot = shot.resize((round(shot.width * scale) + 1, round(shot.height * scale) + 1))
        left, top = (shot.width - 1080) // 2, (shot.height - 1920) // 2
        image = shot.crop((left, top, left + 1080, top + 1920)).filter(ImageFilter.GaussianBlur(14))
        from PIL import ImageChops
        image = ImageChops.lighter(ImageEnhance.Brightness(image).enhance(0.8), Image.new("RGB", image.size, (30, 38, 52)))
    else:
        image = Image.new("RGB", (1080, 1920), (30, 38, 52))
        draw = ImageDraw.Draw(image)
        for y in range(0, 1920, 60):
            draw.line([(0, y), (1080, y)], fill=(40, 50, 66))
        for x in range(0, 1080, 60):
            draw.line([(x, 0), (x, 1920)], fill=(40, 50, 66))
    draw = ImageDraw.Draw(image)
    words = str(name).split()
    lines = [" ".join(words[:1]), " ".join(words[1:])] if len(words) > 2 else [str(name)]
    big = font(132)
    y = 220  # safe zone: the whole stack ends above y 1440 (bottom 25%, 23-Oct)
    for line in lines:
        width = draw.textlength(line, font=big)
        draw.text(((1080 - width) / 2 + 4, y + 4), line, fill=(0, 0, 0), font=big)
        draw.text(((1080 - width) / 2, y), line, fill=(255, 255, 255), font=big)
        y += 150
    button = font(84)
    width = draw.textlength(call, font=button)
    top = y + 40
    draw.rounded_rectangle(((1080 - width) / 2 - 60, top, (1080 + width) / 2 + 60, top + 150), 40, fill=(255, 214, 10))
    draw.text(((1080 - width) / 2, top + 22), call, fill=(14, 16, 20), font=button)
    sub = font(44, semi=True)
    note = "Real chart · Real data · Daily"
    width = draw.textlength(note, font=sub)
    draw.text(((1080 - width) / 2, top + 210), note, fill=(230, 236, 242), font=sub)
    y = top + 290
    for index, text in enumerate(extra or []):
        size = 58 if index == 0 else 50
        face = font(size, semi=True)
        # Wrap to the frame; every line on its own dark pill so it reads over the chart.
        lines, current = [], ""
        for word in str(text).split():
            trial = f"{current} {word}".strip()
            if current and draw.textlength(trial, font=face) > 820:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
        for line in lines:
            width = draw.textlength(line, font=face)
            draw.rounded_rectangle(((1080 - width) / 2 - 28, y - 8, (1080 + width) / 2 + 28, y + size + 22), 22,
                                   fill=(14, 18, 26))
            draw.text(((1080 - width) / 2, y), line, fill=(255, 226, 120) if index == 0 else (245, 247, 250), font=face)
            y += size + 30
        y += 14
    png = dest.with_suffix(".png")
    image.save(png)
    audio_in = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    if voice_text:
        mixed, voiced = end_card_audio(dest, voice_text)
        if mixed is not None:
            seconds = max(seconds, voiced)
            audio_in = ["-i", str(mixed)]
    try:
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png),
            *audio_in,
            "-t", f"{seconds:.3f}", "-vf",
            # Slow push-in (100% -> ~106%) so the card moves the whole time.
            f"scale=1080:1920,zoompan=z='1+0.0013*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={round(seconds*30)+30}:s=1080x1920:fps=30,format=yuv420p",
            *ENCODE, "-shortest", str(dest),
        ], check=True)
    finally:
        if png.is_file():
            png.unlink()
    return dest


def attach(main, directory, outro_rtt=None, outro_pkp=None):
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
    yt = concat_copy([main, outro_pkp or OUTRO_PKP], directory / "final_yt.mp4")
    ig = concat_copy([INTRO_RTT, main, outro_rtt or OUTRO_RTT], directory / "final_ig.mp4")
    return yt, ig
