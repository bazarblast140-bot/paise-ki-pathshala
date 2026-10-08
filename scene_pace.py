"""Scene timing for a Short.

A chart may not sit still. Each beat is cut into slices of at most 4 seconds,
so a scene is never held for about 5 seconds, and the scene count is at least
duration/4. Cuts are not enough: a four-second span whose pictures match also
fails. The self-review craft score uses the same rules.
"""
import math
import re

import numpy as np

BEATS = ("hook", "pehchan", "matlab", "karein")
MAX_SLICE_S = 4.0
MAX_HOLD_S = 5.0
# A four-second span must change the picture, not only the cut list.
VISUAL_SPAN_S = 4.0
VISUAL_MIN_DELTA = 0.8

# A segment is concrete when it names a number or a level/step the viewer can
# point at. Vague filler ("यह समझिए") fails the craft score.
_CONCRETE_RE = re.compile(
    r"(\d|"
    r"\b(?:ATR|SL|RSI|entry|target|wick|range|support|resistance|high|low|quantity)\b|"
    r"(?:बॉडी|नियम|कदम|गलती|उदाहरण|रेखा|औसत|पॉइंट|गुना|मात्रा))",
    re.I,
)


def plan_scenes(beat_times, beats=BEATS):
    """Split each beat so every slice is <= 4s. Count is then >= duration/4."""
    scenes = []
    for beat in beats:
        if beat not in (beat_times or {}):
            continue
        t0, t1 = beat_times[beat]
        dur = float(t1) - float(t0)
        if dur <= 0:
            continue
        n = max(1, math.ceil(dur / MAX_SLICE_S - 1e-9))
        step = dur / n
        for i in range(n):
            scenes.append({
                "beat": beat,
                "index": i,
                "start": float(t0) + i * step,
                "end": float(t0) + (i + 1) * step,
                "callout": i % 2 == 1,
            })
    return scenes


# A frame that is more than this fraction background, for about four seconds, fails.
EMPTY_MAX_FRAC = 0.30


def empty_row_fraction(image):
    """Share of rows that are bare background.

    The caption and brand bands count as used: those rows are reserved even
    when the burned caption is not in this still.
    """
    arr = np.asarray(image)
    if arr.ndim == 3:
        bright = arr.max(axis=2) > 80
        rows = bright.any(axis=1)
    else:
        rows = np.asarray(arr) > 80
    height = int(rows.shape[0])
    if height >= 1600:
        import safe_area
        rows = rows.copy()
        rows[int(safe_area.SUBTITLE_TOP):int(safe_area.SUBTITLE_LIMIT) + 1] = True
        rows[int(safe_area.BRAND_TOP):int(safe_area.BRAND_BOTTOM) + 1] = True
    return float((~rows).mean())


def empty_background_spans(samples, span=VISUAL_SPAN_S, max_frac=EMPTY_MAX_FRAC):
    """Spans of about `span` seconds whose frames stay mostly empty background."""
    notes = []
    fracs = []
    for item in samples or []:
        image = item[1]
        if np.asarray(image).shape[0] < 1000:
            return []
        fracs.append((float(item[0]), empty_row_fraction(image)))
    for i, (t0, frac) in enumerate(fracs):
        if frac <= max_frac + 1e-9:
            continue
        for t1, later in fracs[i + 1:]:
            if t1 - t0 + 1e-6 < span:
                continue
            if later > max_frac + 1e-9:
                notes.append(
                    f"empty background across {t1 - t0:.1f}s "
                    f"({t0:.1f}-{t1:.1f}, {frac:.0%} of the frame)"
                )
            break
    return notes


def unchanged_spans(samples, span=VISUAL_SPAN_S, min_delta=VISUAL_MIN_DELTA):
    """Spans of about `span` seconds whose frames barely differ.

    samples is (time, image array). Each time is compared with the next sample
    that is at least `span` seconds later. Mean absolute pixel difference below
    min_delta is a still, even when a cut falls inside the span.
    """
    notes = []
    ordered = sorted(samples or [], key=lambda item: float(item[0]))
    for i, (t0, left) in enumerate(ordered):
        for t1, right in ordered[i + 1:]:
            gap = float(t1) - float(t0)
            if gap + 1e-6 < span:
                continue
            delta = float(np.abs(np.asarray(left, dtype=np.int16) - np.asarray(right, dtype=np.int16)).mean())
            if delta < min_delta:
                notes.append(
                    f"no visual change across {gap:.1f}s ({float(t0):.1f}-{float(t1):.1f}, delta {delta:.2f})"
                )
            break
    return notes


def check_scenes(scenes, duration):
    """False when a scene is held past about 5s, or there are fewer than duration/4."""
    notes = []
    if not scenes:
        return False, ["no scenes"]
    longest = max(float(s["end"]) - float(s["start"]) for s in scenes)
    if longest > MAX_HOLD_S + 0.05:
        notes.append(f"scene stays {longest:.2f}s (max about {MAX_HOLD_S:.0f}s)")
    need = float(duration) / MAX_SLICE_S
    if len(scenes) + 1e-6 < need:
        notes.append(f"{len(scenes)} scenes < duration/4 ({need:.2f})")
    return (not notes), notes


def segment_is_concrete(text):
    """A number, a level, or a step. Hindi figures count (दो प्रतिशत, बाईस हज़ार)."""
    raw = str(text or "")
    if _CONCRETE_RE.search(raw):
        return True
    import hook_check
    return bool(hook_check.extract_numbers(raw))


_PLAN_BEATS = (
    "hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta",
)


def vague_beats(beats, order=None):
    """Beat names whose spoken line has no number, level, or step.

    A plan script has no matlab or karein key. Those names are not vague beats.
    An explicit order still treats a missing key as vague, which is how a
    four-beat rewrite finds the line it has to fill.
    """
    beats = beats or {}
    if order is None:
        if "entry" in beats or "context" in beats:
            order = _PLAN_BEATS
        else:
            order = BEATS
        return [b for b in order if b in beats and not segment_is_concrete(beats.get(b))]
    return [b for b in order if not segment_is_concrete(beats.get(b))]


def beat_times_from_overlays(overlays):
    """Rebuild beat spans from the on-screen plan. CTA sits inside karein."""
    spans = {}
    for event in overlays or []:
        beat = event.get("beat")
        if beat not in ("hook", "pehchan", "matlab", "udaharan", "galti", "karein"):
            continue
        start = float(event.get("start") or 0)
        end = float(event.get("end") or 0)
        if beat not in spans:
            spans[beat] = [start, end]
        else:
            spans[beat][0] = min(spans[beat][0], start)
            spans[beat][1] = max(spans[beat][1], end)
    order = (
        "hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta",
        "matlab", "udaharan", "galti", "karein",
    )
    return {b: tuple(spans[b]) for b in order if b in spans}
