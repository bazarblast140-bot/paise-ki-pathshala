"""Stage 5: Assemble the video.

Each shot is rendered as its own short clip and the clips are concatenated. One
giant filtergraph over the whole timeline is unreadable and undebuggable; per-shot
clips let a single bad backdrop fail alone instead of taking the render with it.

Per shot:
  backdrop   real Pexels footage (slow pan), or a hand-drawn xkcd chart of the
             script's own numbers, or a plain card — whichever is available
  overlay    section kicker, headline, marker highlight, bottom scrim, progress
             bar; rendered once with PIL, then zoomed and faded in by ffmpeg
  sound      a quiet click on the cut

Layout contract — the two text layers must never fight for the same pixels:

    y 0.00 - 0.10   section kicker (coloured, sketch underline)
    y 0.10 - 0.66   headline zone, auto-sized
    y 0.66 - 0.72   breathing room
    y 0.72 - 1.00   CAPTION ZONE — overlay draws only the scrim here

Captions are .ass, not .srt: ffmpeg renders SRT against ASS's default 288px canvas
and upscales, so an SRT font size reads ~3.75x larger than it looks. And fps= must
come before ass= in the final chain — the concatenated video changes frames only
at cuts, so burning onto it directly freezes each caption until the next cut.
"""
import json, pathlib, re, shutil, subprocess

from PIL import Image, ImageDraw, ImageFont

import assets_pexels
import branding
import sfx
import sketch
from common import cfg, read, ok, die, RUN, ROOT

W, H = 1920, 1080
ASSETS = ROOT / "assets"
ICONS_DIR = ROOT / "assets" / "icons"
CAPTION_TOP = 0.72
SIDE = 150
CAPTION_WORDS = 6
POP_IN = 0.32          # seconds of headline zoom-in
FADE_IN = 0.35
FADE_OUT = 0.30

EMPHASIS = {"positive": sketch.GREEN, "warning": sketch.POP, "neutral": sketch.COOL}

# x264 allocates frame buffers per thread and per lookahead slot. On a 3.4GB
# machine the defaults fail outright with "malloc of size N failed", so cap them.
# Costs a little compression efficiency; the alternative is a render that dies.
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-threads", "2", "-g", "60",
        "-x264-params", "threads=2:lookahead-threads=1:sync-lookahead=0:"
                        "rc-lookahead=10:bframes=2:ref=2",
        "-pix_fmt", "yuv420p"]

FONTS = [
    ROOT / "assets/fonts/Inter-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_cache = {}


def _font(size):
    size = int(size)
    if size not in _cache:
        for p in FONTS:
            try:
                if pathlib.Path(p).exists():
                    _cache[size] = ImageFont.truetype(str(p), size)
                    break
            except OSError:
                continue
        else:
            try:
                _cache[size] = ImageFont.load_default(size=size)
            except TypeError:
                _cache[size] = ImageFont.load_default()
    return _cache[size]


def _wrap(d, text, font, max_w):
    lines, cur = [], ""
    for word in str(text).split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(d, text, max_w, max_h, hi=340, lo=54, gap=1.16):
    """Headlines range from '72' to a six-word phrase; a fixed size either clips
    the long ones or wastes the short ones."""
    size = hi
    while size >= lo:
        f = _font(size)
        lines = _wrap(d, text, f, max_w)
        line_h = size * gap
        if len(lines) * line_h <= max_h and all(d.textlength(l, font=f) <= max_w for l in lines):
            return f, lines, line_h
        size -= 8
    f = _font(lo)
    return f, _wrap(d, text, f, max_w)[:4], lo * gap


def _scrim(layer):
    """Dark gradient over the caption zone so burned-in captions stay legible on
    top of bright footage."""
    d = ImageDraw.Draw(layer)
    top = int(H * 0.58)
    for y in range(top, H):
        t = (y - top) / (H - top)
        d.line([(0, y), (W, y)], fill=(8, 10, 14, int(225 * t ** 1.4)))


def build_overlay(shot, idx, total, path, mode):
    """mode: 'chart' | 'footage' | 'card'.

    On a chart shot the chart IS the visual, so the headline shrinks to a title
    band at the top — a 300px headline over a chart hides the very numbers the
    shot exists to show.
    """
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # Footage varies wildly in brightness (one clip here is a green-screen
    # subscribe animation). A flat veil keeps text contrast predictable.
    if mode == "footage":
        layer.alpha_composite(Image.new("RGBA", (W, H), (8, 10, 14, 96)))
    _scrim(layer)
    d = ImageDraw.Draw(layer)

    emphasis = str(shot.get("emphasis", "neutral")).lower()
    accent = EMPHASIS.get(emphasis, sketch.COOL)

    # ---- Host character (card scenes) + icon ----
    # Wealth-Logic layout: host on left, message/icon on right
    host_w = 0
    if mode == "card":
        try:
            # Map emphasis → character pose
            emp = str(shot.get("emphasis", "neutral")).lower()
            section = str(shot.get("section") or "").lower()
            if emp in ("warning",) or "trap" in section:
                pose = "warn"
            elif emp in ("positive",):
                pose = "ok"
            elif "action" in section:
                pose = "point"
            elif "math" in section or "rule" in section:
                pose = "point"
            else:
                pose = "think" if "myth" in section else "neutral"
            # Allow explicit override from visual plan
            if shot.get("pose"):
                pose = str(shot.get("pose")).lower().strip()
            # Full head always visible — stay well inside frame
            host_h = int(H * 0.52)  # ~560px on 1080p
            host = sketch.load_host_pose(pose, height=host_h)
            hx = int(W * 0.03)
            # feet near bottom, head has >= 90px clearance from top edge
            hy = max(90, H - host.height - int(H * 0.05))
            # safety: if still would clip top, shrink further
            if hy < 90:
                hy = 90
            layer.alpha_composite(host, (hx, hy))
            host_w = host.width + hx
        except Exception as e:
            print(f"[warn] host pose failed: {e}")
            host_w = 0

    icon_name = shot.get("icon")
    icon_y_offset = 0
    if icon_name and mode != "chart":
        try:
            icon_path = ICONS_DIR / f"{icon_name}.png"
            if icon_path.exists():
                icon_img = Image.open(icon_path).convert("RGBA")
            else:
                icon_img = sketch.draw_icon(icon_name, 200)
            icon_size = 280
            icon_img = icon_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
            # place upper-right of content area
            icon_x = host_w + int((W - host_w - icon_size) / 2) if host_w else (W - icon_size) // 2
            icon_y = int(H * 0.18)
            layer.alpha_composite(icon_img, (icon_x, icon_y))
            icon_y_offset = icon_size + 16
        except Exception:
            pass

    headline = str(shot.get("on_screen_text") or "").strip()
    if headline.lower() == "null":
        headline = ""

    if headline:
        if mode == "chart":
            box_top, box_bottom, hi, lo = int(H * 0.145), int(H * 0.265), 82, 40
        else:
            box_top_start = int(H * 0.18) + icon_y_offset if icon_y_offset else int(H * 0.22)
            box_bottom, hi, lo = int(H * 0.62), 220, 48
            box_top = box_top_start
        fit_w = (W - host_w - 2 * SIDE) if host_w else (W - 2 * SIDE)
        f, lines, line_h = _fit(d, headline, fit_w, box_bottom - box_top, hi, lo)
        y0 = box_top + ((box_bottom - box_top) - len(lines) * line_h) / 2

        # locate the highlighted span with real glyph metrics — line_h fractions
        # drift away from the actual ink at different sizes
        hl = str(shot.get("highlight") or "").strip()
        hl_i, hl_pos, hl_box = -1, -1, None
        if hl and hl.lower() != "null":
            for i, line in enumerate(lines):
                pos = line.lower().find(hl.lower())
                if pos >= 0:
                    lx = (W - d.textlength(line, font=f)) / 2
                    x = lx + d.textlength(line[:pos], font=f)
                    ly = y0 + i * line_h
                    b = d.textbbox((x, ly), line[pos:pos + len(hl)], font=f)
                    hl_box = (b[0] - 8, b[1] - 8, b[2] + 8, b[3] + 8)
                    hl_i, hl_pos = i, pos
                    break
        if hl_box:
            layer.alpha_composite(sketch.marker_highlight((W, H), hl_box, sketch.HIGHLIGHT,
                                                          seed=idx * 13 + 5))
            d = ImageDraw.Draw(layer)

        # Keep text in the right content zone when host stands on the left
        content_left = host_w + 20 if host_w else 0
        content_w = W - content_left - SIDE
        for i, line in enumerate(lines):
            tw = d.textlength(line, font=f)
            lx = content_left + max(0, (content_w - tw) / 2)
            y = y0 + i * line_h
            d.text((lx, y), line, font=f, fill=sketch.INK)
            if i == hl_i:
                # dark-on-amber, the way a real highlighter reads
                span = line[hl_pos:hl_pos + len(hl)]
                d.text((lx + d.textlength(line[:hl_pos], font=f), y), span, font=f,
                       fill=sketch.PAPER)


        # Supporting line under headline (fills empty board)
        sub = str(shot.get("sub_text") or "").strip()
        if sub and sub.lower() != "null" and mode == "card":
            sf = _font(42)
            words = sub.split()
            slines, cur = [], ""
            for w in words:
                trial = (cur + " " + w).strip()
                if d.textlength(trial, font=sf) <= content_w * 0.92:
                    cur = trial
                else:
                    if cur: slines.append(cur)
                    cur = w
            if cur: slines.append(cur)
            sy = y0 + len(lines) * line_h + 18
            for sl in slines[:2]:
                stw = d.textlength(sl, font=sf)
                sx = content_left + max(0, (content_w - stw) / 2)
                cool = getattr(sketch, "COOL", (80, 90, 110))
                d.text((sx, sy), sl, font=sf, fill=cool if isinstance(cool, tuple) else (80, 90, 110))
                sy += 50

    layer.save(path)
    return path


KICK_Y = 66
KICK_FONT = 38


def build_chrome(shot, idx, total, path, channel):
    """Static layer: section underline, pro channel watermark, progress bar.

    Watermark is top-right, static, high-legibility but not loud — reads as a
    real channel mark on every frame without competing with the headline.
    """
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Section labels (TRAP/RULE/MATH) disabled — clean pro frames only
    if channel:
        # Pro watermark: small amber pip + uppercase name, soft shadow for legibility
        label = channel.upper().strip()
        f = _font(28)
        tw = d.textlength(label, font=f)
        x = W - SIDE - tw
        y = KICK_Y + 4
        # soft dark plate behind text so it survives bright footage
        pad_x, pad_y = 14, 8
        d.rounded_rectangle(
            [x - pad_x - 18, y - pad_y, x + tw + pad_x, y + 28 + pad_y],
            radius=6, fill=(255, 255, 255, 200))
        # amber accent pip (channel identity colour)
        d.ellipse([x - pad_x - 10, y + 6, x - pad_x + 4, y + 20], fill=sketch.HIGHLIGHT + (230,))
        d.text((x, y), label, font=f, fill=(*sketch.INK, 240))

    frac = (idx + 1) / max(total, 1)
    d.rectangle([0, H - 8, int(W * frac), H], fill=sketch.HIGHLIGHT + (255,))
    layer.save(path)
    return path


def _ass_colour(rgb):
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def kicker_events(shot, start, end, typewriter=True):
    """Section kickers (TRAP/RULE/MATH) disabled — clean pro frames."""
    return []

def _ass_time(sec):
    sec = max(0.0, float(sec))
    return f"{int(sec // 3600):d}:{int((sec % 3600) // 60):02d}:{sec % 60:05.2f}"


def _cues(text, start, end, max_words=CAPTION_WORDS):
    words = str(text or "").split()
    if not words:
        return []
    groups = [words[i:i + max_words] for i in range(0, len(words), max_words)]
    span = max(0.4, float(end) - float(start))
    t, out = float(start), []
    for g in groups:
        share = span * (len(g) / len(words))
        out.append((t, t + share, " ".join(g)))
        t += share
    return out


# Captions narration ke hain — Hindi channel par Devanagari. libass HarfBuzz
# se matra sahi jodta hai, bas font install hona chahiye (workflow: fonts-noto-core).
CAP_FONT = ("Noto Sans Devanagari"
            if str(cfg()["channel"].get("default_language", "")).lower().startswith("hi")
            else "Arial")

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{CAP_FONT},58,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,160,160,90,1
Style: Kick,Arial,38,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,2,0,1,3,0,7,{SIDE},160,{KICK_Y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_escape(text):
    return re.sub(r"\s+", " ", str(text)).replace("{", "(").replace("}", ")").strip()


def _run(args, **kw):
    """ffmpeg failures surface as a bare exit code otherwise, which says nothing
    about which filter rejected what."""
    p = subprocess.run(args, cwd=RUN, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True, **kw)
    if p.returncode != 0:
        lines = (p.stderr or "").splitlines()
        # the real message is buried above the encoder's summary statistics
        hits = [l for l in lines
                if re.search(r"error|invalid|no such|unable|cannot|not found|failed to",
                             l, re.I) and "libx264" not in l]
        detail = "\n".join(hits[-8:] or lines[-8:])
        die(f"ffmpeg failed (exit {p.returncode}):\n{detail}")
    return p


def _require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(f"{tool} not found on PATH. Install FFmpeg and re-run.\n"
                f"  Windows: winget install Gyan.FFmpeg   (then open a new terminal)\n"
                f"  macOS:   brew install ffmpeg\n"
                f"  Linux:   sudo apt-get install ffmpeg")


def _seconds(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


TYPE_START, TYPE_LEN = 0.15, 0.70


def render_shot(idx, shot, dur, fps, overlay, backdrop, still, chrome=None):
    """One shot -> shot_NNN.mp4.

    Layers, bottom to top: backdrop (drifts), headline (zooms + fades), chrome
    (static). The section heading is typed in later by the subtitle pass.
    """
    out = f"shot_{idx:03d}.mp4"
    args = ["ffmpeg", "-y"]

    if backdrop and still:
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(backdrop)]
    elif backdrop:
        args += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(backdrop)]
    else:
        args += ["-f", "lavfi", "-t", f"{dur:.3f}",
                 "-i", f"color=c=0xfafafc:s={W}x{H}:r={fps}"]
    args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(overlay)]

    idx_chrome = None
    if chrome:
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(chrome)]
        idx_chrome = 2

    # A chart is data — it must not drift under the viewer's eye. Footage does,
    # because a frozen stock clip looks like the video has hung.
    is_chart = still and backdrop and "chart" in str(backdrop)
    if backdrop and not is_chart:
        # force_original_aspect_ratio=increase on BOTH axes, or an ultrawide clip
        # scales to under 1080 tall and the crop fails outright
        ow, oh = int(W * 1.12) // 2 * 2, int(H * 1.12) // 2 * 2
        pan = (f"scale={ow}:{oh}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H}:'(iw-{W})*t/{max(dur, 0.1):.3f}':'(ih-{H})/2'")
        bg = f"[0:v]{pan},fps={fps},eq=brightness=-0.20:saturation=0.72,setsar=1[bg]"
    else:
        bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},fps={fps},setsar=1[bg]")

    # ---- Dynamic animation logic ----
    # Host is painted ON the overlay — never y-shift or zoom the overlay
    # (old fade_in_up used y=-100 which clipped the character head).
    anim = shot.get("animation_in", "fade_in")
    tx_chain = f"[1:v]format=rgba,fade=t=in:st=0:d={FADE_IN}:alpha=1[tx]"
    overlay_chain = "[bg][tx]overlay=x=0:y=0:format=auto[s1]"

    parts = [bg, tx_chain, overlay_chain]
    last = "s1"

    if idx_chrome is not None:
        parts.append(f"[{last}][{idx_chrome}:v]overlay=x=0:y=0:format=auto[s2]")
        last = "s2"

    # Soft edge: fade out last 0.3s so hard cuts between shots feel calmer
    fade_st = max(0.0, float(dur) - FADE_OUT)
    parts.append(f"[{last}]fade=t=out:st={fade_st:.3f}:d={FADE_OUT:.3f}[vout]")
    last = "vout"

    args += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-an",
             "-t", f"{dur:.3f}", "-r", str(fps), "-fps_mode", "cfr", *X264, out]
    _run(args)
    return out


def main():
    c = cfg()
    _require_ffmpeg()

    plan = json.loads(read("shots.json"))
    shots = plan.get("shots") or []
    if not shots:
        die("No shots in shots.json")
    voice = RUN / "voice.mp3"
    if not voice.exists():
        die("run/voice.mp3 missing — run run_voice.py first.")

    fps = int(c["video"]["fps"])

    # 1) rescale estimated timings onto the real narration
    audio_dur = _seconds(voice)
    planned = max(float(s["end_sec"]) for s in shots)
    scale = audio_dur / planned if planned > 0 else 1.0
    ok(f"narration {audio_dur:.1f}s vs planned {planned:.1f}s — scaling timings x{scale:.3f}")
    for s in shots:
        s["start_sec"] = float(s["start_sec"]) * scale
        s["end_sec"] = float(s["end_sec"]) * scale

    # 2) fetch footage up front so the render loop never stalls on the network
    print("=== footage ===")
    assets_pexels.warm_cache(shots)

    # 3) permanent intro. Everything after it shifts by its length.
    brand = c.get("branding", {})
    channel = str(c["channel"].get("name", "")).strip()
    if channel.startswith("TODO"):
        channel = ""
    intro = branding.intro_clip(c, fps, X264)
    offset = _seconds(intro) if intro else 0.0
    if intro:
        ok(f"intro {offset:.1f}s — everything after it shifts by that much")

    typewriter = bool(brand.get("typewriter", True))

    # 4) per-shot clips
    print("=== shots ===")
    clips, events, click_times = [], [], []
    if intro:
        shutil.copyfile(intro, RUN / "intro.mp4")
        clips.append("intro.mp4")
    for i, shot in enumerate(shots):
        dur = max(0.6, float(shot["end_sec"]) - float(shot["start_sec"]))

        # backdrop first — the overlay's layout depends on what is behind it
        backdrop, still, mode = None, False, "card"
        chart = shot.get("chart_data")
        if isinstance(chart, dict):
            cp = RUN / f"chart_{i:03d}.png"
            if sketch.chart_png(chart, str(cp)):
                backdrop, still, mode = cp, True, "chart"
        if backdrop is None and shot.get("_backdrop"):
            backdrop = pathlib.Path(shot["_backdrop"])
            still = backdrop.suffix.lower() in (".jpg", ".jpeg", ".png")
            mode = "footage"

        overlay = build_overlay(shot, i, len(shots), RUN / f"ov_{i:03d}.png", mode)
        chrome = build_chrome(shot, i, len(shots), RUN / f"ch_{i:03d}.png", channel)

        clips.append(render_shot(
            i, shot, dur, fps, overlay.name,
            backdrop.resolve() if backdrop else None, still, chrome=chrome.name))

        click_times.append(float(shot["start_sec"]) + offset)
        s0, s1 = shot["start_sec"] + offset, shot["end_sec"] + offset
        events += kicker_events(shot, s0, s1, typewriter)
        for a, b, text in _cues(shot.get("spoken_line"), shot["start_sec"], shot["end_sec"]):
            events.append(f"Dialogue: 0,{_ass_time(a + offset)},{_ass_time(b + offset)},"
                          f"Cap,,0,0,0,,{_ass_escape(text)}")
        print(f"  [{i+1}/{len(shots)}] {dur:4.1f}s  {mode:<7} "
              f"{str(shot.get('on_screen_text'))[:44]}")

    # permanent outro (brand pack)
    outro = branding.outro_clip(c, fps, X264)
    if outro:
        shutil.copyfile(outro, RUN / "outro.mp4")
        clips.append("outro.mp4")
        ok(f"outro {_seconds(outro):.1f}s appended")

    (RUN / "clips.txt").write_text("\n".join(f"file '{c_}'" for c_ in clips), encoding="utf-8")
    (RUN / "subs.ass").write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")
    ok(f"built {len(clips)} clips + {len(events)} caption cues")

    # 4) join clips
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "clips.txt",
          "-c", "copy", "silent.mp4"])

    # 6) narration (pushed past the intro) + clicks + special SFX
    sfx.click_track(click_times, audio_dur + offset, RUN / "clicks.wav")
    sfx.sfx_track(shots, audio_dur + offset, offset, RUN / "special_sfx.wav")
    delay = int(offset * 1000)
    _run(["ffmpeg", "-y", "-i", "voice.mp3", "-i", "clicks.wav", "-i", "special_sfx.wav",
          "-filter_complex",
          f"[0:a]adelay={delay}|{delay}[nar];"
          f"[nar][1:a][2:a]amix=inputs=3:duration=longest:normalize=0[a]",
          "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "mixed.m4a"])

    # 7) mix narration with background music
    final_audio = "final_audio.m4a"
    music_file = ASSETS / "music.mp3"
    use_music = brand.get("background_music", True)
    if use_music and music_file.exists():
        ok("mixing in background music")
        _run(["ffmpeg", "-y", "-i", "mixed.m4a", "-stream_loop", "-1", "-i", str(music_file),
              "-filter_complex",
              # mix voice (1.0) with music (0.12), stop when voice ends
              "[0:a]volume=1.0[v];[1:a]volume=0.12[m];[v][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
              "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-shortest", final_audio])
    else:
        if use_music and not music_file.exists():
            print("[skip] background music: assets/music.mp3 not found")
        (RUN / "mixed.m4a").replace(RUN / final_audio)

    # 8) captions + final mux. fps= before ass= or every caption freezes until the next cut.
    _run(["ffmpeg", "-y", "-i", "silent.mp4", "-i", final_audio,
          "-vf", f"fps={fps},ass=subs.ass",
          "-r", str(fps), "-fps_mode", "cfr", *X264,
          "-c:a", "copy", "-shortest", "final.mp4"])

    ok(f"wrote run/final.mp4 ({(audio_dur + offset) / 60:.1f} min)")

    # 9) thumbnail — generated from the punchiest shot, not the first frame
    if brand.get("thumbnail", True):
        thumb = branding.thumbnail(c, shots, RUN / "thumbnail.jpg")
        if thumb:
            ok("wrote run/thumbnail.jpg (1280x720)")


if __name__ == "__main__":
    main()
