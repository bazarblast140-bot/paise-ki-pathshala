"""Shared motion components for every Paise chart.

Each component takes code-computed numbers and a 0–1 progress. Progress comes
from Whisper word times through `window_progress` (smoothstep). At progress 1
the candles, arrow, level lines, risk box, and glow ring match the settled
Chart Board primitives. The whoosh is a short ffmpeg noise sweep mixed about
24 dB under the voice. There is no music bed.
"""
import math
import subprocess
import wave
from pathlib import Path

WHOOSH_UNDER_DB = 24.0
WHOOSH_SECONDS = 0.42
_REVEAL_KINDS = ("trend", "sr", "entry", "sl", "target")
_LEVELS = ("sr", "entry", "sl", "target")


def clamp(u):
    u = float(u)
    if u < 0.0:
        return 0.0
    if u > 1.0:
        return 1.0
    return u


def ease(u):
    """Smoothstep. 0 and 1 stay exact."""
    u = clamp(u)
    return u * u * (3.0 - 2.0 * u)


def window_progress(t, start, end):
    """0 before `start`, 1 at and after `end`, eased in between."""
    if t is None:
        return 1.0
    start = float(start)
    end = float(end)
    t = float(t)
    if end <= start:
        return 1.0 if t >= start else 0.0
    if t <= start:
        return 0.0
    if t >= end:
        return 1.0
    return ease((t - start) / (end - start))


def _fmt(value):
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _progress(clock, key, t, default=0.0):
    window = (clock or {}).get(key)
    if not window:
        return float(default)
    return window_progress(t, window[0], window[1])


def candle_build(img, box, ohlc, lo, hi, progress, pad, width_frac=0.62):
    """Candles appear one by one. The newest body grows from its midpoint.

    Progress 1 draws the same candles as `board_kit.candles` with no
    `draw_count`.
    """
    import board_kit as K
    from PIL import ImageDraw

    ohlc = list(ohlc)
    progress = clamp(progress)
    if progress >= 1.0:
        return K.candles(
            img, box, ohlc, lo, hi, grid=False, width_frac=width_frac, pad=pad,
        )
    grown = progress * len(ohlc)
    full = int(math.floor(grown + 1e-9))
    frac = grown - full
    ax = K.candles(
        img, box, ohlc, lo, hi, grid=False, width_frac=width_frac, pad=pad,
        draw_count=full,
    )
    if frac > 1e-6 and full < len(ohlc):
        o, h, l, c = ohlc[full]
        col = K.GREEN if c >= o else K.RED
        cx = ax.x(full)
        bw = ax.bw
        draw = ImageDraw.Draw(img)
        draw.line([(cx, ax.y(h)), (cx, ax.y(l))], fill=col, width=max(3, bw // 6))
        top, bot = ax.y(max(o, c)), ax.y(min(o, c))
        mid = (top + bot) / 2.0
        half = max((bot - top) / 2.0, 2.5) * frac
        draw.rectangle([cx - bw / 2, mid - half, cx + bw / 2, mid + half], fill=col)
    return ax


def trend_arrow(img, ax, plan, progress):
    """Arrow draws from the first close toward the focus close."""
    import board_kit as K

    progress = clamp(progress)
    if progress <= 0.0 or plan.focus <= 0:
        return False
    start, end = plan.ohlc[0], plan.ohlc[plan.focus]
    p1 = (ax.x(0), ax.y(start[3]))
    p2 = (ax.x(plan.focus), ax.y(end[3]))
    tip = p2 if progress >= 1.0 else (
        p1[0] + (p2[0] - p1[0]) * progress,
        p1[1] + (p2[1] - p1[1]) * progress,
    )
    K.arrow(img, p1, tip, K.YELLOW, w=8, head=26)
    return True


def sr_band(img, ax, price, progress, color=None):
    """Translucent support or resistance band around the computed price."""
    import board_kit as K
    from PIL import Image, ImageDraw

    progress = clamp(progress)
    if progress <= 0.0 or price is None:
        return False
    color = color or K.BLUE
    alpha = int(round(78 * progress))
    if alpha <= 0:
        return False
    y = float(ax.y(float(price)))
    half = 16.0
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle(
        [ax.x0 + ax.pl, y - half, ax.x1 - ax.pr, y + half],
        fill=tuple(color) + (alpha,),
    )
    merged = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.paste(merged, (0, 0))
    return True


def level_line(img, box, y, color, label, side, progress):
    """ENTRY / SL / TARGET / SR line draws left to right. The pill waits until 0.45.

    Progress 1 is the same dashed call as the settled chart.
    """
    import board_kit as K

    progress = clamp(progress)
    x0 = box[0] + 24
    x1 = box[2] - 24
    if progress <= 0.0:
        return {"price_label": label, "drawn": False, "x1": x0}
    end = x1 if progress >= 1.0 else x0 + (x1 - x0) * progress
    show = label if progress >= 0.45 else None
    K.dashed(img, x0, end, y, color, show, size=22, label_side=side)
    return {"price_label": show, "drawn": True, "x1": end}


def rr_readout(plan):
    """Risk, reward, and RR from the computed plan. Not a second rounding."""
    risk = float(plan.risk)
    reward = float(plan.reward)
    text = f"RISK {_fmt(risk)}  REWARD {_fmt(reward)}  RR {plan.rr_text}"
    return {"risk": risk, "reward": reward, "rr": plan.rr_text, "text": text}


def _rr_words(img, text):
    import board_kit as K
    x0 = K.BOARD[0] + 20
    y0 = K.BOARD[1] + 16 + 54 + 8
    K.label(img, x0, y0, text, K.INK, 22)


def _rr_partial(img, plan, progress, text):
    import board_kit as K
    from PIL import ImageDraw

    x0, y0 = K.BOARD[0] + 20, K.BOARD[1] + 16
    full, height = 860, 54
    width = max(24, int(round(full * clamp(progress))))
    draw = ImageDraw.Draw(img)
    mid = x0 + int(width * 0.38)
    draw.rounded_rectangle([x0, y0, mid, y0 + height], 10, fill=K.RED)
    draw.rectangle([mid - 8, y0, min(mid + 8, x0 + width), y0 + height], fill=K.RED)
    if mid < x0 + width:
        draw.rounded_rectangle([mid, y0, x0 + width, y0 + height], 10, fill=K.GREEN)
    K.fit_label(
        img, x0 + 12, y0 + 14,
        f"Entry {_fmt(plan.entry)} · SL {_fmt(plan.sl)} · Target {_fmt(plan.target)} = {plan.rr_text}",
        max(20, width - 24), col=(10, 10, 10), size=26, min_size=16,
    )
    _rr_words(img, text)


def risk_reward_box(img, plan, progress):
    """Red/green reward box. Progress 1 calls the settled `_rr_box`."""
    progress = clamp(progress)
    info = rr_readout(plan)
    info["shown"] = progress > 0.0
    if progress <= 0.0:
        return info
    if progress >= 1.0:
        import trade_plan
        trade_plan._rr_box(img, plan)
        _rr_words(img, info["text"])
        return info
    _rr_partial(img, plan, progress, info["text"])
    return info


def _zoom_candle(img, cx, cy, zoom):
    """Scale a window around the key candle and paste it back, inside the board."""
    import board_kit as K
    from PIL import Image

    zoom = float(zoom)
    if zoom <= 1.001:
        return
    board = K.BOARD
    half_w, half_h = 88, 116
    x0 = int(max(board[0], math.floor(cx - half_w)))
    y0 = int(max(board[1], math.floor(cy - half_h)))
    x1 = int(min(board[2], math.ceil(cx + half_w)))
    y1 = int(min(board[3], math.ceil(cy + half_h)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return
    crop = img.crop((x0, y0, x1, y1))
    zw = max(1, int(round((x1 - x0) * zoom)))
    zh = max(1, int(round((y1 - y0) * zoom)))
    scaled = crop.resize((zw, zh), Image.Resampling.BILINEAR)
    left = int(round(cx - zw / 2.0))
    top = int(round(cy - zh / 2.0))
    src_x = max(0, board[0] - left)
    src_y = max(0, board[1] - top)
    dst_x = max(left, board[0])
    dst_y = max(top, board[1])
    src_r = min(zw, board[2] - left)
    src_b = min(zh, board[3] - top)
    if src_r <= src_x or src_b <= src_y:
        return
    piece = scaled.crop((src_x, src_y, src_r, src_b))
    img.paste(piece, (dst_x, dst_y))


def highlight_candle(img, ax, plan, progress):
    """Glow ring on the key candle, with a gentle zoom that settles at progress 1.

    Progress 1 is `board_kit.ring` at rx 28, ry 40 and zoom 1, the settled mark.
    """
    import board_kit as K

    progress = clamp(progress)
    if plan.focus >= len(plan.ohlc) or progress <= 0.0:
        return {"zoom": 1.0, "rx": 0.0, "ry": 0.0}
    candle = plan.ohlc[plan.focus]
    cx = ax.x(plan.focus)
    cy = (ax.y(candle[1]) + ax.y(candle[2])) / 2.0
    if progress >= 1.0:
        K.ring(img, cx, cy, 28, 40, K.YELLOW)
        return {"zoom": 1.0, "rx": 28.0, "ry": 40.0}
    zoom = 1.0 + 0.06 * progress
    _zoom_candle(img, cx, cy, zoom)
    rx = 28.0 * (0.35 + 0.65 * progress)
    ry = 40.0 * (0.35 + 0.65 * progress)
    K.ring(img, cx, cy, rx, ry, K.YELLOW)
    return {"zoom": zoom, "rx": rx, "ry": ry}


def number_counter(img, value, progress, xy=(80, 1264), label="TARGET"):
    """Count up to `value`. Progress 1 shows that value exactly."""
    import board_kit as K

    value = float(value)
    progress = clamp(progress)
    if progress >= 1.0:
        shown = value
    elif progress <= 0.0:
        shown = 0.0
    else:
        shown = value * progress
    if progress > 0.0:
        K.label(img, xy[0], xy[1], f"{label} {_fmt(shown)}", K.INK, 28)
    return {"value": value, "shown": shown}


def clock_from(topic, plan=None):
    """Start/end for each component from overlay word times, else the beat clock."""
    del plan
    topic = topic or {}
    beats = topic.get("_beat_times") or {}
    by = {}
    for row in topic.get("_chart_overlays") or []:
        by.setdefault(str(row.get("kind") or ""), row)

    def start(kind, beat):
        row = by.get(kind)
        if row and row.get("start") is not None:
            return float(row["start"])
        span = beats.get(beat)
        if span and len(span) >= 1:
            return float(span[0])
        return None

    hook = beats.get("hook")
    if hook and len(hook) >= 2:
        c0 = float(hook[0])
        span = max(0.8, float(hook[1]) - c0)
        c1 = c0 + min(span, max(1.2, span * 0.8))
    else:
        c0, c1 = 0.0, 3.2

    def window(kind, beat, dur):
        t0 = start(kind, beat)
        if t0 is None:
            return None
        return (t0, t0 + float(dur))

    return {
        "candles": (c0, c1),
        "trend": window("trend", "pehchan", 0.65),
        "sr": window("sr", "context", 0.75),
        "entry": window("entry", "entry", 0.65),
        "sl": window("sl", "sl", 0.65),
        "target": window("target", "target", 0.65),
        "rr": window("rr", "target", 0.55),
        "highlight": window("trend", "pehchan", 1.15),
        "counter": window("target", "target", 0.85),
    }


def reveal_times(topic):
    """Whoosh cues: trend, support, entry, stop, target, and a late candle start."""
    times = []
    for row in (topic or {}).get("_chart_overlays") or []:
        if row.get("kind") not in _REVEAL_KINDS:
            continue
        try:
            times.append(float(row["start"]))
        except (TypeError, ValueError):
            continue
    hook = ((topic or {}).get("_beat_times") or {}).get("hook")
    if hook:
        try:
            times.append(float(hook[0]))
        except (TypeError, ValueError):
            pass
    return times


def unique_reveals(frames):
    """One whoosh per cue. t≈0 is skipped so the voice is not hit at the cut."""
    found = []
    seen = set()
    for frame in frames or []:
        for raw in getattr(frame, "motion_reveals", None) or []:
            try:
                t = round(float(raw), 3)
            except (TypeError, ValueError):
                continue
            if t <= 0.05 or t in seen:
                continue
            seen.add(t)
            found.append(t)
    found.sort()
    return found


def draw_plan(img, plan, shown, t, clock):
    """Draw every component that the clock says is on at time t.

    `shown` is the scene's overlay list and only sets the price scale, matching
    the settled chart for that beat. A line still waits for its own word.
    """
    import board_kit as K
    import trade_plan

    shown = list(shown or [])
    box, lo, hi, pad = trade_plan._chart_geometry(plan, shown)
    candles_p = _progress(clock, "candles", t, 1.0)
    ax = candle_build(img, box, plan.ohlc, lo, hi, candles_p, pad)
    trend_p = _progress(clock, "trend", t, 1.0 if any(row.get("kind") == "trend" for row in shown) else 0.0)
    if trend_p > 0.0:
        trend_arrow(img, ax, plan, trend_p)
    sr_p = _progress(clock, "sr", t, 0.0)
    sr_price = None
    for row in shown:
        if row.get("kind") == "sr" and row.get("price") is not None:
            sr_price = float(row["price"])
    if sr_price is None:
        sr_price = float(plan.sr)
    if sr_p <= 0.0 and any(row.get("kind") == "sr" for row in shown) and not (clock or {}).get("sr"):
        sr_p = 1.0
    if sr_p > 0.0:
        sr_band(img, ax, sr_price, sr_p, K.BLUE)
    colors = {"sr": K.BLUE, "entry": K.YELLOW, "sl": K.RED, "target": K.GREEN}
    sides = {"sr": "left", "entry": "right", "sl": "left", "target": "right"}
    labels = {}
    for row in shown:
        if row.get("kind") in colors and row.get("price") is not None:
            labels[row["kind"]] = row
    levels = {}
    for kind in _LEVELS:
        row = labels.get(kind)
        price = float(row["price"]) if row and row.get("price") is not None else {
            "sr": float(plan.sr),
            "entry": float(plan.entry),
            "sl": float(plan.sl),
            "target": float(plan.target),
        }[kind]
        label = row["label"] if row else f"{kind.upper()} {_fmt(price)}"
        default = 1.0 if row and not (clock or {}).get(kind) else 0.0
        prog = _progress(clock, kind, t, default)
        drawn = level_line(
            img, box, ax.y(price), colors[kind], label, sides[kind], prog,
        ) if prog > 0.0 else {"price_label": None, "drawn": False, "x1": box[0] + 24}
        levels[kind] = {"price": price, "drawn": bool(drawn["drawn"]), "label": label, "progress": prog}
    rr_default = 1.0 if any(row.get("kind") == "rr" for row in shown) and not (clock or {}).get("rr") else 0.0
    rr_p = _progress(clock, "rr", t, rr_default)
    rr = risk_reward_box(img, plan, rr_p) if rr_p > 0.0 else rr_readout(plan)
    if rr_p <= 0.0:
        rr["shown"] = False
    if plan.rsi and any(row.get("kind") == "rsi_os" for row in shown) and trend_p >= 1.0:
        trade_plan._rsi_strip(img, plan)
    hi_default = 1.0 if not (clock or {}).get("highlight") else 0.0
    hi_p = _progress(clock, "highlight", t, hi_default)
    highlight = highlight_candle(img, ax, plan, hi_p)
    counter_p = _progress(clock, "counter", t, 0.0)
    counter = number_counter(img, plan.target, counter_p, label="TARGET")
    return {
        "progress": {
            "candles": candles_p,
            "trend": trend_p,
            "sr": sr_p,
            "entry": levels["entry"]["progress"],
            "sl": levels["sl"]["progress"],
            "target": levels["target"]["progress"],
            "rr": rr_p,
            "highlight": hi_p,
            "counter": counter_p,
        },
        "levels": levels,
        "rr": rr,
        "highlight": highlight,
        "counter": counter,
    }


def render_whoosh(path, seconds=WHOOSH_SECONDS, sr=44100):
    """Pink-noise bands that sweep high to low. No downloaded sample."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        "[0:a]asplit=3[a][b][c];"
        "[a]highpass=f=1400,lowpass=f=4200,afade=t=in:st=0:d=0.02,afade=t=out:st=0.05:d=0.10[hi];"
        "[b]highpass=f=500,lowpass=f=1600,afade=t=in:st=0.04:d=0.03,afade=t=out:st=0.10:d=0.12[mid];"
        "[c]highpass=f=140,lowpass=f=600,afade=t=in:st=0.08:d=0.04,afade=t=out:st=0.16:d=0.22[lo];"
        "[hi][mid][lo]amix=inputs=3:normalize=0,volume=0.7"
    )
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"anoisesrc=color=pink:sample_rate={int(sr)}:duration={float(seconds):.3f}:amplitude=0.45:seed=7",
            "-filter_complex", graph,
            "-ar", str(int(sr)), "-ac", "1", str(path),
        ],
        check=True,
    )
    return path


def _read_wav(path):
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sr = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    import numpy as np
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    return pcm, sr


def _write_wav(path, samples, sr):
    import numpy as np
    samples = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        channels = 1
        pcm = (samples * 32767.0).astype("<i2")
    else:
        channels = samples.shape[1]
        pcm = (samples.reshape(-1) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(int(sr))
        handle.writeframes(pcm.tobytes())
    return path


def _rms(mono):
    import numpy as np
    mono = np.asarray(mono, dtype=np.float64)
    if mono.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(mono))))


def mix_under(voice_path, reveals, out_path, under_db=WHOOSH_UNDER_DB):
    """Place one whoosh at each reveal, about `under_db` under the voice RMS.

    The voice file is only read. No bed, no music, no second noise layer.
    """
    import numpy as np

    voice, sr = _read_wav(voice_path)
    stereo = voice.ndim == 2
    mono = voice.mean(axis=1) if stereo else voice
    voice_rms = _rms(mono)
    whoosh_path = Path(out_path).with_name("_motion_whoosh.wav")
    render_whoosh(whoosh_path, sr=sr)
    clip, clip_sr = _read_wav(whoosh_path)
    if clip_sr != sr:
        raise RuntimeError(f"whoosh sample rate {clip_sr} != voice {sr}")
    if clip.ndim > 1:
        clip = clip[:, 0]
    peak = float(np.max(np.abs(clip))) if clip.size else 0.0
    track = np.zeros(mono.shape[0], dtype=np.float32)
    cues = []
    for raw in reveals or []:
        try:
            t = float(raw)
        except (TypeError, ValueError):
            continue
        if t <= 0.05:
            continue
        cues.append(t)
    if peak > 0.0 and cues:
        for t in cues:
            i = int(round(t * sr))
            if i < 0 or i >= track.shape[0]:
                continue
            n = min(clip.shape[0], track.shape[0] - i)
            track[i:i + n] += clip[:n]
        bus_peak = float(np.max(np.abs(track))) if track.size else 0.0
        if bus_peak > 0.0 and voice_rms > 0.0:
            cap = voice_rms * (10 ** (-float(under_db) / 20.0))
            steps = max(0, int(math.floor(cap * 32767.0)) - 1)
            pcm_cap = steps / 32767.0
            track *= np.float32(pcm_cap / bus_peak)
    if stereo:
        mixed = voice + track[:, None]
    else:
        mixed = voice + track
    mix_peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if mix_peak > 0.98:
        mixed = mixed * np.float32(0.98 / mix_peak)
    _write_wav(out_path, mixed, sr)
    sfx_peak = float(np.max(np.abs(track))) if track.size else 0.0
    under = None
    if voice_rms > 0.0 and sfx_peak > 0.0:
        under = 20.0 * math.log10(voice_rms / sfx_peak)
    try:
        whoosh_path.unlink()
    except OSError:
        pass
    return {"under_db": under, "sfx_peak": sfx_peak, "voice_rms": voice_rms, "cues": cues}
