"""On-screen text plan: punchy hook for the first 2 seconds, key points on every beat, poll at the end."""
from engagement import engagement_question
import hook_check

BEATS = ("hook", "pehchan", "matlab", "karein")
ORDER = ("hook", "pehchan", "matlab", "udaharan", "galti", "karein")
_EXTRA = {
    "udaharan": ("उदाहरण", "एक ठोस केस"),
    "galti": ("आम गलती", "इसे ऐसे सुधारें"),
}


def _lines(topic, beat):
    if beat in hook_check.TEACHING_BEATS:
        spoken = str(((topic or {}).get("_spoken_beats") or {}).get(beat) or "")
        return hook_check.example_card_lines(topic, spoken)
    pair = (topic.get("beats") or {}).get(beat)
    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
        return str(pair[0]), str(pair[1])
    return _EXTRA.get(beat, (beat, beat))


def overlay_plan(topic, beat_times, hook_line, cta=None):
    """List of {role, beat, text, start, end}. Times are seconds."""
    cta = cta or engagement_question(topic)
    # Aaj ka Nifty is four data beats. entry/cta on the clock must not
    # switch it onto the topic trade-plan overlay.
    if str((topic or {}).get("key") or "") != "aaj_ka_nifty" and (
        beat_times and "entry" in beat_times and "cta" in beat_times
    ):
        return _plan_overlay(topic, beat_times, hook_line, cta)
    events = []
    beats = [b for b in ORDER if b in (beat_times or {})] or list(BEATS)
    for beat in beats:
        t0, t1 = beat_times[beat]
        head, sub = _lines(topic, beat)
        if beat == "hook":
            # Spoken and shown from the first frame. The hook beat is longer than
            # 2s; the opening line is on screen for that whole beat, including 0-2s.
            events.append({
                "role": "hook", "beat": beat, "text": hook_line,
                "start": 0.0, "end": float(t1),
            })
            events.append({
                "role": "key", "beat": beat, "text": sub,
                "start": 0.0, "end": float(t1),
            })
        else:
            start = float(t0)
            events.append({
                "role": "key", "beat": beat, "text": head,
                "start": start, "end": float(t1),
            })
            events.append({
                "role": "key", "beat": beat, "text": sub,
                "start": start, "end": float(t1),
            })
    t0, t1 = beat_times["karein"]
    events.append({
        "role": "cta", "beat": "karein", "text": cta,
        "start": max(float(t0), float(t1) - 2.8), "end": float(t1),
    })
    events.append({
        "role": "disclaimer", "beat": "karein",
        "text": "सिर्फ़ जानकारी, निवेश सलाह नहीं।",
        "start": float(t0), "end": float(t1),
    })
    return events


def _plan_overlay(topic, beat_times, hook_line, cta):
    import trade_plan
    events = []
    for beat in trade_plan.BEATS:
        if beat not in beat_times:
            continue
        t0, t1 = beat_times[beat]
        head, sub = _lines(topic, beat)
        if beat == "hook":
            events.append({
                "role": "hook", "beat": beat, "text": hook_line,
                "start": 0.0, "end": float(t1),
            })
            events.append({
                "role": "key", "beat": beat, "text": sub,
                "start": 0.0, "end": float(t1),
            })
        else:
            events.append({
                "role": "key", "beat": beat, "text": head,
                "start": float(t0), "end": float(t1),
            })
            events.append({
                "role": "key", "beat": beat, "text": sub,
                "start": float(t0), "end": float(t1),
            })
    t0, t1 = beat_times["cta"]
    events.append({
        "role": "cta", "beat": "cta", "text": cta,
        "start": max(float(t0), float(t1) - 2.8), "end": float(t1),
    })
    events.append({
        "role": "disclaimer", "beat": "cta", "text": trade_plan.DISCLAIMER,
        "start": float(t0), "end": float(t1),
    })
    return events


def hook_covers_first_two_seconds(events):
    for event in events or []:
        if event.get("role") != "hook":
            continue
        text = " ".join(str(event.get("text") or "").split())
        if text and float(event.get("start", 9)) <= 0.05 and float(event.get("end", 0)) >= 2.0:
            return True
    return False


def beats_have_key_text(events):
    covered = set()
    for event in events or []:
        if event.get("role") in ("hook", "key") and str(event.get("text") or "").strip():
            if float(event.get("end", 0)) > float(event.get("start", 0)):
                covered.add(event.get("beat"))
    names = {event.get("beat") for event in events or []}
    if {"entry", "sl", "target", "cta"} <= names:
        required = {event.get("beat") for event in events or [] if event.get("role") in ("hook", "key")}
    else:
        required = set(BEATS) | {event.get("beat") for event in events or [] if event.get("beat") in ORDER}
    return required <= covered
