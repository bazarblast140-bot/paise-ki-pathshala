"""Channel identity: the permanent intro and the thumbnail.

The intro is the same every video, so it is rendered once and cached in assets/.
The cache key is a hash of the name, tagline and duration — change any of them in
config.json and the next run rebuilds it without being asked.

The intro is composed frame by frame in PIL rather than as an ffmpeg filtergraph.
It is built once, so the slower path costs nothing, and typing + a drawn underline
+ a fading tagline is far easier to get right in code than in nested expressions.
"""
import hashlib, pathlib, shutil, subprocess

from PIL import Image, ImageDraw, ImageFont

import sketch
from common import ROOT, RUN, ok

W, H = 1920, 1080
ASSETS = ROOT / "assets"

FONTS = [
    ASSETS / "fonts/Inter-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_cache = {}


def font(size):
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


def _ease(t):
    """Ease-out — linear motion reads as mechanical."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _fit_width(text, target, hi, lo=40):
    """Largest size at which text still fits the target width."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    size = hi
    while size > lo and probe.textlength(text, font=font(size)) > target:
        size -= 4
    return font(size)


def _intro_frame(i, total, name, tagline, fps):
    """Pro permanent intro — same every video, cached by name+tagline hash.

    Timeline:
        0.00-0.40s  amber brand pip + thin top rule fade in
        0.15-1.55s  channel name types in with amber cursor
        1.60-2.20s  amber underline draws under the name
        2.10-3.00s  tagline fades up
        last 0.40s  fade to black into the hook
    """
    t = i / fps
    img = Image.new("RGB", (W, H), sketch.PAPER)
    d = ImageDraw.Draw(img)

    # subtle top rule + brand pip (channel identity, always present after 0.1s)
    if t >= 0.08:
        a = _ease(min(1.0, (t - 0.08) / 0.35))
        rule_c = tuple(int(sketch.PAPER[c] + (40 - sketch.PAPER[c]) * a) for c in range(3))
        d.rectangle([W * 0.28, 72, W * 0.72, 74], fill=rule_c)
        pip_a = int(230 * a)
        cx = W // 2
        d.ellipse([cx - 7, 58, cx + 7, 72], fill=sketch.HIGHLIGHT + (pip_a,) if False else sketch.HIGHLIGHT)

    f_name = _fit_width(name, W - 420, 200, 72)
    name_w = d.textlength(name, font=f_name)
    nx, ny = (W - name_w) / 2, H * 0.36

    # type the channel name
    type_start, type_len = 0.15, 1.40
    shown = name
    if t < type_start:
        shown = ""
    elif t < type_start + type_len:
        frac = _ease((t - type_start) / type_len)
        shown = name[:max(0, int(len(name) * frac))]
    if shown:
        # soft shadow for depth
        d.text((nx + 3, ny + 3), shown, font=f_name, fill=(0, 0, 0))
        d.text((nx, ny), shown, font=f_name, fill=sketch.INK)

    # amber cursor while typing
    if type_start <= t < type_start + type_len + 0.45:
        cx = nx + d.textlength(shown, font=f_name) + 10
        blink = t >= type_start + type_len and int((t - type_start) * 6) % 2 == 1
        if not blink:
            bbox = f_name.getbbox("H")
            d.rectangle([cx, ny + bbox[1], cx + 12, ny + bbox[3]], fill=sketch.HIGHLIGHT)

    # amber underline under the full name
    if t >= 1.60:
        grow = _ease((t - 1.60) / 0.55)
        if grow > 0.02:
            y = ny + f_name.size * 1.28
            sketch.rough_underline(d, nx, nx + name_w * grow, y, sketch.HIGHLIGHT, 10, seed=4)

    # tagline
    if tagline and t >= 2.10:
        a = _ease((t - 2.10) / 0.80)
        f_tag = _fit_width(tagline, W - 520, 56, 28)
        tw = d.textlength(tagline, font=f_tag)
        lift = int(20 * (1 - a))
        # blend paper -> soft cool white
        shade = tuple(int(sketch.PAPER[c] + (200 - sketch.PAPER[c]) * a) for c in range(3))
        d.text(((W - tw) / 2, ny + f_name.size * 1.72 + lift), tagline, font=f_tag, fill=shade)

    # fade out into the hook
    total_s = total / fps
    if t > total_s - 0.40:
        k = min(1.0, (t - (total_s - 0.40)) / 0.40)
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), k)
    return img


def _reencode_brand_clip(src, dest, fps, x264_args, max_seconds=None):
    """Normalize brand video to match shot encode (concat -c copy safe)."""
    import subprocess
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1"
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-vf", vf, "-an", "-r", str(fps), "-fps_mode", "cfr", *x264_args, str(dest)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dest


def intro_clip(cfg_dict, fps, x264_args, force=False):
    """Permanent intro. Prefers assets/brand/intro.mp4 (pro pack), else PIL typewriter.

    x264_args must match the shot clips exactly — the clips are joined with
    `concat -c copy`, which refuses streams whose encoder parameters differ.
    """
    brand = cfg_dict.get("branding", {})
    seconds = float(brand.get("intro_seconds", 0) or 0)
    if seconds <= 0 and not (ASSETS / "brand" / "intro.mp4").exists():
        return None

    # --- Pro brand pack (user-supplied) ---
    pack = ASSETS / "brand" / "intro.mp4"
    if pack.exists():
        dest = ASSETS / "brand" / "intro_norm.mp4"
        if force or not dest.exists() or dest.stat().st_mtime < pack.stat().st_mtime:
            # keep natural length of pro intro (usually ~8–12s)
            _reencode_brand_clip(pack, dest, fps, x264_args)
            ok(f"brand intro normalized -> {dest.name}")
        return dest

    name = str(cfg_dict["channel"].get("name", "")).strip()
    tagline = str(cfg_dict["channel"].get("tagline", "")).strip()
    if not name or name.startswith("TODO"):
        return None

    key = hashlib.sha1(f"{name}|{tagline}|{seconds}|{fps}|{x264_args}".encode()).hexdigest()[:10]
    dest = ASSETS / f"intro-{key}.mp4"
    if dest.exists() and not force:
        return dest

    # drop stale intros so assets/ does not collect one per edit
    for old in ASSETS.glob("intro-*.mp4"):
        old.unlink(missing_ok=True)

    tmp = RUN / "_intro"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    total = int(seconds * fps)
    for i in range(total):
        _intro_frame(i, total, name, tagline, fps).save(tmp / f"f{i:04d}.png")

    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", "f%04d.png",
         "-r", str(fps), *x264_args, str(dest.resolve())],
        cwd=tmp, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(tmp, ignore_errors=True)
    ok(f"built intro ({seconds:.0f}s) -> {dest.name}")
    return dest


# --------------------------------------------------------------------------

# YouTube scales one banner image for TV, desktop and phone. Only the middle
# 1546x423 is guaranteed to survive on every device, so the name and tagline go
# there and everything else is decoration that is allowed to be cropped away.
BANNER_W, BANNER_H = 2560, 1440
SAFE_W, SAFE_H = 1546, 423


def outro_clip(cfg_dict, fps, x264_args, force=False):
    """Optional end card from assets/brand/outro.mp4."""
    pack = ASSETS / "brand" / "outro.mp4"
    if not pack.exists():
        return None
    dest = ASSETS / "brand" / "outro_norm.mp4"
    if force or not dest.exists() or dest.stat().st_mtime < pack.stat().st_mtime:
        _reencode_brand_clip(pack, dest, fps, x264_args)
        ok(f"brand outro normalized -> {dest.name}")
    return dest



def banner(cfg_dict, path):
    """The channel banner, in the same hand-drawn language as the intro."""
    name = str(cfg_dict["channel"].get("name", "")).strip()
    tagline = str(cfg_dict["channel"].get("tagline", "")).strip()
    if not name or name.startswith("TODO"):
        return None

    img = Image.new("RGB", (BANNER_W, BANNER_H), sketch.PAPER)
    d = ImageDraw.Draw(img)

    sx0, sy0 = (BANNER_W - SAFE_W) / 2, (BANNER_H - SAFE_H) / 2
    cx, cy = BANNER_W / 2, BANNER_H / 2

    # Decoration lives outside the safe area on purpose — it is cropped away on
    # phones and that is fine. Two rising arrows, a doubling curve and a bar
    # ramp: the three shapes this channel is actually about.
    for x, y, w, seed in [(240, 300, 300, 1), (2100, 980, 260, 2)]:
        sketch.rough_arrow(d, (x, y + 160), (x + w, y), sketch.COOL, 7, seed, 30)
    # normalised so the curve always rises by RISE px regardless of the base
    steps, rise, base = 25, 230.0, cy + 330
    span = 1.12 ** steps - 1
    pts = [(70 + i * 30, base - rise * ((1.12 ** i - 1) / span)) for i in range(steps + 1)]
    for i in range(len(pts) - 1):
        sketch.rough_line(d, pts[i], pts[i + 1], sketch.GREEN, 6, 40 + i, 1, 1.4)
    for i, bh in enumerate([60, 96, 150, 232]):
        x = BANNER_W - 620 + i * 92
        sketch.rough_rect(d, (x, cy - 300 - bh, x + 58, cy - 300), sketch.HIGHLIGHT, 5, 60 + i)

    # The name, tagline and sub-line are stacked from the top of the safe area
    # and each one is sized off what the previous one left, so they cannot
    # collide the way absolute positions eventually do.
    sub = str(cfg_dict["channel"].get("banner_line", "")).strip()
    f_name = _fit_width(name.upper(), SAFE_W - 120, int(SAFE_H * 0.46), 80)
    name_w = d.textlength(name.upper(), font=f_name)
    nx, ny = cx - name_w / 2, sy0 + 8
    d.text((nx, ny), name.upper(), font=f_name, fill=sketch.INK)

    y = ny + f_name.size * 1.18
    sketch.rough_underline(d, nx, nx + name_w, y, sketch.HIGHLIGHT, 11, seed=4)
    y += 30

    if tagline:
        f_tag = _fit_width(tagline, SAFE_W - 260, int(SAFE_H * 0.14), 30)
        d.text((cx - d.textlength(tagline, font=f_tag) / 2, y), tagline,
               font=f_tag, fill=sketch.COOL)
        y += f_tag.size * 1.55

    if sub:
        f_sub = _fit_width(sub, SAFE_W - 300, int(SAFE_H * 0.10), 24)
        d.text((cx - d.textlength(sub, font=f_sub) / 2, y), sub, font=f_sub,
               fill=tuple(int(c * 0.62) for c in sketch.INK))

    img.save(path, quality=95)
    return path


def _pick_shot(shots):
    """The thumbnail wants the punchiest frame, not the first one: a short
    headline with a highlight reads at phone size; a full sentence does not."""
    def score(s):
        head = str(s.get("on_screen_text") or "")
        if not head:
            return -999
        v = 100 - len(head) * 2
        if s.get("highlight"):
            v += 30
        if any(ch.isdigit() for ch in head):
            v += 25
        return v
    return max(shots, key=score) if shots else None


def thumbnail(cfg_dict, shots, path, tw=1280, th=720):
    """1280x720 thumbnail built from the video's strongest shot."""
    shot = _pick_shot(shots)
    if shot is None:
        return None

    img = Image.new("RGB", (tw, th), sketch.PAPER)

    # reuse the shot's own footage as the backdrop, heavily darkened
    src = shot.get("_backdrop")
    if src and pathlib.Path(src).exists():
        grab = RUN / "_thumb_src.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", str(src), "-frames:v", "1",
                            "-vf", f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
                                   f"crop={tw}:{th}", "-q:v", "3", str(grab)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with Image.open(grab) as bg:
                # Heavy: stock clips often contain their own text or numbers, and
                # a glittering "10" behind a headline that says 72 is worse than
                # no backdrop at all. It is texture, not content.
                img = Image.blend(bg.convert("RGB"), Image.new("RGB", (tw, th), sketch.PAPER), 0.76)
        except (subprocess.CalledProcessError, OSError):
            pass

    # ---- Power Graphic (Arrow) based on emphasis ----
    emphasis = str(shot.get("emphasis", "neutral")).lower()
    arrow_d = ImageDraw.Draw(img)
    arrow_width = 140
    arrow_x = tw - arrow_width - 80
    arrow_y_center = th / 2
    arrow_head = 90

    if emphasis == "positive":
        p1 = (arrow_x, arrow_y_center + 160)
        p2 = (arrow_x, arrow_y_center - 160)
        sketch.rough_arrow(arrow_d, p1, p2, sketch.GREEN, 35, seed=shot["shot_id"], head=arrow_head)
    elif emphasis == "warning":
        p1 = (arrow_x, arrow_y_center - 160)
        p2 = (arrow_x, arrow_y_center + 160)
        sketch.rough_arrow(arrow_d, p1, p2, sketch.POP, 35, seed=shot["shot_id"], head=arrow_head)

    # vignette so the centre carries the eye
    vig = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vig)
    for k in range(60):
        a = int(120 * (k / 60) ** 2)
        vd.rectangle([-k * 14, -k * 9, tw + k * 14, th + k * 9], outline=(0, 0, 0, a), width=14)
    img = Image.alpha_composite(img.convert("RGBA"), vig).convert("RGB")

    d = ImageDraw.Draw(img)
    head = str(shot.get("on_screen_text")).strip()

    # Grow to fill, don't shrink to fit: a thumbnail is judged at ~320px wide in
    # the feed, so the headline has to dominate the frame.
    max_w, max_h = tw - 120, th * 0.66
    size, f, lines = 60, font(60), [head]
    for probe in range(400, 48, -6):
        pf = font(probe)
        words, ls, cur = head.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if d.textlength(trial, font=pf) <= max_w or not cur:
                cur = trial
            else:
                ls.append(cur)
                cur = w
        if cur:
            ls.append(cur)
        if len(ls) <= 3 and len(ls) * probe * 1.14 <= max_h \
                and all(d.textlength(l, font=pf) <= max_w for l in ls):
            size, f, lines = probe, pf, ls
            break

    line_h = size * 1.14
    y = (th - len(lines) * line_h) / 2 - 24
    hl = str(shot.get("highlight") or "").strip()

    layer = img.convert("RGBA")
    for i, line in enumerate(lines):
        pos = line.lower().find(hl.lower()) if hl and hl.lower() != "null" else -1
        lx = (tw - d.textlength(line, font=f)) / 2
        if pos >= 0:
            x = lx + d.textlength(line[:pos], font=f)
            b = ImageDraw.Draw(layer).textbbox((x, y + i * line_h), line[pos:pos + len(hl)], font=f)
            layer.alpha_composite(sketch.marker_highlight(
                (tw, th), (b[0] - 10, b[1] - 10, b[2] + 10, b[3] + 10), sketch.HIGHLIGHT, seed=2))
    img = layer.convert("RGB")
    d = ImageDraw.Draw(img)

    for i, line in enumerate(lines):
        lx = (tw - d.textlength(line, font=f)) / 2
        ly = y + i * line_h
        d.text((lx, ly), line, font=f, fill=sketch.INK, stroke_width=6, stroke_fill=(0, 0, 0))
        pos = line.lower().find(hl.lower()) if hl and hl.lower() != "null" else -1
        if pos >= 0:
            d.text((lx + d.textlength(line[:pos], font=f), ly), line[pos:pos + len(hl)],
                   font=f, fill=sketch.PAPER)

    name = str(cfg_dict["channel"].get("name", "")).strip()
    if name and not name.startswith("TODO"):
        fn = font(38)
        d.text((54, th - 74), name.upper(), font=fn, fill=sketch.HIGHLIGHT)

    img.save(path, quality=92)
    return path
