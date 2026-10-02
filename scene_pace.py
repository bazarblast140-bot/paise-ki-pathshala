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
VISUAL_MIN_DELTA = 1.0

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
    return bool(_CONCRETE_RE.search(str(text or "")))


def vague_beats(beats, order=BEATS):
    """Beat names whose spoken line has no number, level, or step."""
    beats = beats or {}
    return [b for b in order if not segment_is_concrete(beats.get(b))]


def beat_times_from_overlays(overlays):
    """Rebuild beat spans from the on-screen plan. CTA sits inside karein."""
    spans = {}
    for event in overlays or []:
        beat = event.get("beat")
        if beat not in BEATS:
            continue
        start = float(event.get("start") or 0)
        end = float(event.get("end") or 0)
        if beat not in spans:
            spans[beat] = [start, end]
        else:
            spans[beat][0] = min(spans[beat][0], start)
            spans[beat][1] = max(spans[beat][1], end)
    return {b: tuple(spans[b]) for b in BEATS if b in spans}
