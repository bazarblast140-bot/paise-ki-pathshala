"""Move scene plates without slicing shaped text.

Slide, wipe, and scale apply to the background plate only. Each line of text
is already one HarfBuzz/Raqm sprite and is faded as that whole bitmap. A wipe
edge never masks a glyph, and a scale never stretches one letter on its own.
"""
import subprocess

import numpy as np
from PIL import Image


def ease(t):
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else float(t)
    return t * t * (3.0 - 2.0 * t)


def mode_for(name):
    """Map an ffmpeg xfade name onto a plate move that cannot cut text."""
    name = (name or "fade").lower()
    if name.startswith("slide"):
        direction = "down" if "down" in name else "right" if "right" in name else "left"
        return "slide", direction
    if "zoom" in name or name.startswith("scale"):
        return "scale", ""
    if name.startswith("wipe") or name.startswith("smooth"):
        direction = "down" if "down" in name else "right" if "right" in name else "left"
        return "wipe", direction
    return "fade", ""


def _normalize(mode, direction):
    if mode not in ("slide", "wipe", "scale", "fade"):
        return mode_for(mode)
    return mode, direction or "left"


def outgoing_alpha(u):
    """Old line stays fully opaque until the midpoint, then the new line replaces it.

    A fade through zero left one blank frame. A partial fade stacked the two
    lines. The swap is instant and each side is the whole sprite.
    """
    return 1.0 if u <= 0.5 else 0.0


def incoming_alpha(u):
    return 0.0 if u <= 0.5 else 1.0


def blend_plates(a, b, u, mode="wipe", direction="left"):
    """Blend two RGB plates. Text is not in these images."""
    mode, direction = _normalize(mode, direction)
    u = 0.0 if u < 0.0 else 1.0 if u > 1.0 else float(u)
    left = a.convert("RGB")
    right = b.convert("RGB")
    if mode == "scale":
        scale = 0.88 + 0.12 * u
        w, h = right.size
        grown = right.resize((max(2, int(w * scale)), max(2, int(h * scale))), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", right.size, (4, 8, 16))
        canvas.paste(grown, ((w - grown.size[0]) // 2, (h - grown.size[1]) // 2))
        return Image.blend(left, canvas, u)
    A = np.asarray(left)
    B = np.asarray(right)
    h, w = A.shape[:2]
    if mode == "fade":
        mixed = A.astype(np.float32) * (1.0 - u) + B.astype(np.float32) * u
        return Image.fromarray(mixed.astype(np.uint8), "RGB")
    out = A.copy()
    if mode == "wipe" and direction == "down":
        cut = int(round(u * h))
        if cut > 0:
            out[:cut, :] = B[:cut, :]
        return Image.fromarray(out, "RGB")
    if mode == "wipe":
        cut = int(round(u * w))
        if direction == "right":
            if cut > 0:
                out[:, w - cut:] = B[:, w - cut:]
        elif cut > 0:
            out[:, :cut] = B[:, :cut]
        return Image.fromarray(out, "RGB")
    shift = int(round(u * w))
    if direction == "down":
        shift = int(round(u * h))
        if shift <= 0:
            return left
        if shift >= h:
            return right
        out = np.empty_like(A)
        out[:h - shift, :] = A[shift:, :]
        out[h - shift:, :] = B[:shift, :]
        return Image.fromarray(out, "RGB")
    if shift <= 0:
        return left
    if shift >= w:
        return right
    out = np.empty_like(A)
    if direction == "right":
        out[:, shift:] = A[:, :w - shift]
        out[:, :shift] = B[:, w - shift:]
    else:
        out[:, :w - shift] = A[:, shift:]
        out[:, w - shift:] = B[:, :shift]
    return Image.fromarray(out, "RGB")


def _with_alpha(sprite, alpha):
    if alpha >= 0.99:
        return sprite
    faded = sprite.copy()
    band = faded.getchannel("A").point(lambda p: int(p * alpha))
    faded.putalpha(band)
    return faded


def _paint_layers(base, frame, alpha, persist=False):
    if alpha <= 0.01:
        return
    for layer in getattr(frame, "text_layers", []) or []:
        if bool(layer.get("persist")) != persist:
            continue
        sprite = layer["image"] if persist else _with_alpha(layer["image"], alpha)
        base.paste(sprite, layer["xy"], sprite)


def _plate(frame):
    """Background to move. A plain PIL image is its own plate."""
    plate = getattr(frame, "plate", None)
    if plate is None:
        return frame.convert("RGB")
    return plate


def composite(a, b, t, mode="wipe", direction="left"):
    """RGB frame at transition progress t. Plates move; sprites only fade.

    Layered scenes carry a plate plus text sprites. A generic four-beat card
    is a plain Image. That picture is the plate, and it has no sprites to paint.
    """
    mode, direction = _normalize(mode, direction)
    u = ease(t)
    # A baked card has no separate plate. Sliding it cuts the words and leaves
    # the old card on top of the new one. One full card, then the other.
    if getattr(a, "plate", None) is None and getattr(b, "plate", None) is None:
        shown = a if u <= 0.5 else b
        return shown.convert("RGB")
    base = blend_plates(_plate(a), _plate(b), u, mode, direction)
    _paint_layers(base, a, outgoing_alpha(u), persist=False)
    _paint_layers(base, b, incoming_alpha(u), persist=False)
    _paint_layers(base, b if u >= 0.5 else a, 1.0, persist=True)
    return base


def ink_column_coverage(layer, background, rendered, min_delta=10):
    """Share of the sprite's solid columns that still differ from the plate.

    A wipe that keeps only the left half of a glyph drops this near 0.5.
    A whole sprite stays near 1. Padding and faint antialiasing are ignored.
    """
    sprite = np.asarray(layer["image"])
    if sprite.ndim != 3 or sprite.shape[2] < 4:
        return 1.0
    strong = np.flatnonzero(sprite[:, :, 3].max(axis=0) >= 140)
    if strong.size < 3:
        return 1.0
    x, y = int(layer["xy"][0]), int(layer["xy"][1])
    sh, sw = sprite.shape[0], sprite.shape[1]
    fg = np.asarray(rendered.convert("RGB"))
    bg = np.asarray(background.convert("RGB"))
    rh, rw = fg.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(rw, x + sw), min(rh, y + sh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    diff = np.abs(fg[y0:y1, x0:x1].astype(np.int16) - bg[y0:y1, x0:x1].astype(np.int16)).max(axis=2)
    hits = 0
    seen = 0
    for col in strong:
        seen += 1
        dest = int(col) + x
        if dest < x0 or dest >= x1:
            continue
        if int(diff[:, dest - x0].max()) >= min_delta:
            hits += 1
    return hits / seen if seen else 1.0


def sliced_glyph_notes(a, b, t=0.5, mode="wipe", direction="left"):
    """Names of text sprites whose ink columns were cut at this progress.

    Faint watermarks are skipped. An empty list means every shaped line is whole.
    """
    mode, direction = _normalize(mode, direction)
    u = ease(t)
    plate = blend_plates(_plate(a), _plate(b), u, mode, direction)
    rendered = composite(a, b, t, mode, direction)
    notes = []
    visible = ((a, outgoing_alpha(u)), (b, incoming_alpha(u)))
    for frame, alpha in visible:
        for layer in getattr(frame, "text_layers", []) or []:
            text = layer.get("text") or ""
            if not text or layer.get("faint") or layer.get("glow"):
                continue
            shown = 1.0 if layer.get("persist") else alpha
            if shown < 0.12:
                continue
            coverage = ink_column_coverage(layer, plate, rendered)
            if coverage < 0.85:
                notes.append(f"{text[:32]} coverage {coverage:.2f}")
    return notes


def _posed(img, end):
    """Still used at a wipe. An animated scene enters at its start and leaves at its end."""
    anim = getattr(img, "animate", None)
    if anim is None:
        return img
    return anim(1.0 if end else 0.0)


def transition_faults(images, transitions):
    """Check the midpoint of every transition. A sliced line is a fault."""
    faults = []
    names = tuple(transitions or ("fade",))
    for i in range(len(images) - 1):
        name = names[i % len(names)]
        for t in (0.5, 0.82):
            notes = sliced_glyph_notes(_posed(images[i], True), _posed(images[i + 1], False), t, name)
            if notes:
                faults.append(f"{i}:{name}@{t:.2f}:{notes[0]}")
                break
    return faults


def render_at(images, durations, t, transitions=(), fade_s=0.28, overlay=None):
    """The picture at time t. Animated scenes are redrawn; a wipe never cuts their type."""
    durations = [float(d) for d in durations]
    starts = []
    acc = 0.0
    for dur in durations:
        starts.append(acc)
        acc += dur
    total = acc
    t = min(max(0.0, float(t)), total if total else 0.0)
    names = tuple(transitions or ("fade",))
    fade = float(fade_s)
    hit = _window(t, starts, fade) if len(images) > 1 else None
    if hit is not None:
        i, u = hit
        left, right = images[i], images[i + 1]
        both_move = getattr(left, "animate", None) and getattr(right, "animate", None)
        both_chart = getattr(left, "chart_region", None) and getattr(right, "chart_region", None)
        # The chart box stays put. Elements crossfade inside it instead of a hard cut.
        if both_move and both_chart:
            a = left.animate(1.0).convert("RGB")
            b = right.animate(0.0).convert("RGB")
            mixed = Image.blend(a, b, ease(u))
            if overlay is not None:
                overlay(mixed, 0.0 if total <= 0 else min(1.0, t / total))
            return mixed
        if both_move:
            hit = None
    if hit is None:
        idx = _scene_index(t, starts, durations)
        img = images[idx]
        anim = getattr(img, "animate", None)
        if anim is not None:
            local = 0.0 if durations[idx] <= 0 else (t - starts[idx]) / durations[idx]
            frame = anim(local)
        else:
            frame = img
        if overlay is not None and anim is not None:
            overlay(frame, 0.0 if total <= 0 else min(1.0, t / total))
        return frame
    i, u = hit
    # Pose animated neighbours so a wipe does not flash the other end of the move.
    frame = composite(_posed(images[i], True), _posed(images[i + 1], False), u, names[i % len(names)])
    if overlay is not None:
        overlay(frame, 0.0 if total <= 0 else min(1.0, t / total))
    return frame


def _scene_index(t, starts, durations):
    for i, start in enumerate(starts):
        if t < start + durations[i] or i == len(starts) - 1:
            return i
    return 0


def _window(t, starts, fade):
    for i in range(len(starts) - 1):
        boundary = starts[i + 1]
        begin = boundary - fade / 2.0
        if begin <= t < begin + fade:
            return i, (t - begin) / fade
    return None


def write_timeline(images, durations, out, fps=30, transitions=(), fade_s=0.28, overlay=None):
    """Encode holds and plate transitions. Output length matches the scene durations."""
    if not images:
        raise ValueError("no frames")
    w, h = images[0].size
    durations = [float(d) for d in durations]
    total = sum(durations)
    fps = int(fps)
    nframes = max(1, int(round(total * fps)))
    holds = []
    starts = []
    acc = 0.0
    for img, dur in zip(images, durations):
        starts.append(acc)
        acc += dur
        holds.append(None if getattr(img, "animate", None) else img.convert("RGB").tobytes())
    log_path = str(out) + ".ffmpeg.log"
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        str(out),
    ]
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
        try:
            for fi in range(nframes):
                t = min(total, fi / float(fps))
                idx = _scene_index(t, starts, durations)
                hit = _window(t, starts, float(fade_s)) if len(images) > 1 else None
                if hit is None and holds[idx] is not None:
                    proc.stdin.write(holds[idx])
                else:
                    frame = render_at(images, durations, t, transitions, fade_s, overlay)
                    proc.stdin.write(frame.convert("RGB").tobytes())
            proc.stdin.close()
            proc.stdin = None
        except Exception:
            proc.kill()
            proc.wait()
            raise
        code = proc.wait()
    if code != 0:
        with open(log_path, encoding="utf-8") as log:
            err = log.read().strip()
        raise RuntimeError(err or f"ffmpeg exited {code}")
