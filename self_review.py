"""Score a Short before it can be uploaded.

Parts (each 0-100):
  hook      opening line passes the deterministic hook check
  visual    on-screen text, and source + as-of date when a price chart is used
  accuracy  every number in the narration and on-screen card is in the topic
            facts or in the fetched series
  trend     only when data/trends.json lists themes or topic clusters;
            skipped when the file is absent or is an empty research snapshot
  craft     no scene held past about 5s, scene count >= duration/4, every
            spoken beat names a number, level, or step, a four-second span
            changes the picture, and text sprites do not overlap

Pass bar: every included part >= PART_BAR (60) and the mean >= OVERALL_BAR (75).
Below the bar, the narration is regenerated up to MAX_ATTEMPTS times. Still
below, public_ok is false and the upload gate blocks a public post.
Craft is scored in full_review. score_content stays hook / accuracy / trend,
which a rewrite can fix; scene timing is fixed by the renderer.
"""
import json
import pathlib

import hook_check
import scene_pace
import short_layout

ROOT = pathlib.Path(__file__).resolve().parent
TRENDS_PATH = ROOT / "data" / "trends.json"

PART_BAR = 60
OVERALL_BAR = 75
MAX_ATTEMPTS = 3


def next_action(passed, attempt, max_attempts=MAX_ATTEMPTS):
    """'accept', 'retry', or 'block'. attempt is 1-based."""
    if passed:
        return "accept"
    if attempt < max_attempts:
        return "retry"
    return "block"


def _mean(values):
    nums = [v for v in values if v is not None]
    if not nums:
        return 0.0
    return round(sum(nums) / len(nums), 1)


def load_trends(path=None):
    """dict, or None when the file is absent or unreadable (trend score is skipped)."""
    path = pathlib.Path(path) if path else TRENDS_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _themes(trends):
    """Theme strings from a hand-written list or from Daily trend research.

    The checked-in research snapshot has empty topic_clusters. That is not a
    theme list, so the trend sub-score stays skipped until research records one.
    """
    if not isinstance(trends, dict):
        return []
    found = []

    def add(item):
        text = str(item).strip().lower()
        if text and text not in found:
            found.append(text)

    raw = trends.get("themes") or trends.get("categories") or trends.get("hot") or []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, dict):
        raw = list(raw.keys())
    for item in raw:
        add(item)

    summary = trends.get("summary") if isinstance(trends.get("summary"), dict) else {}
    for cluster in summary.get("topic_clusters") or []:
        if isinstance(cluster, str):
            add(cluster)
            continue
        if not isinstance(cluster, dict):
            continue
        # headline_terms is a bag of news tokens, not a topic the bank can match.
        name = cluster.get("name")
        if name and str(name) != "headline_terms":
            add(name)
        for keyword in cluster.get("keywords") or []:
            add(keyword)
    return found


def score_hook(narration, topic):
    line = str((narration or {}).get("hook_line") or "")
    allowed = hook_check.allowed_numbers(topic or {})
    fails = hook_check.check_hook_line(
        line, allowed, max_words=hook_check.hook_word_limit(topic))
    if fails:
        return max(0, 40 - 15 * (len(fails) - 1)), fails
    return 100, []


def score_accuracy(narration, topic, spec=None):
    """Every spoken and on-screen number must trace to the topic or the fetch."""
    allowed = set(hook_check.allowed_numbers(topic or {}))
    for series in (spec or {}).get("series") or []:
        for point in series.get("points") or []:
            close = point.get("close")
            if close is None:
                continue
            allowed.add(str(int(close)) if float(close).is_integer() else str(close))
            allowed |= hook_check.extract_numbers(str(close))
            allowed |= hook_check.extract_numbers(str(point.get("date") or ""))
    beats = (narration or {}).get("beats") or {}
    order = getattr(short_layout, "ORDER", short_layout.BEATS)
    names = [b for b in order if b in beats] or list(short_layout.BEATS)
    spoken = " ".join(str(beats.get(b) or "") for b in names)
    spoken = spoken + " " + str((narration or {}).get("hook_line") or "")
    if (topic or {}).get("_live_short") or any(b in beats for b in hook_check.TEACHING_BEATS):
        extra = hook_check.live_number_faults(beats, topic, names, allowed=allowed)
        extra |= hook_check.extract_numbers(str((narration or {}).get("hook_line") or "")) - allowed
    else:
        extra = hook_check.unsourced_numbers(spoken, topic, allowed=allowed)
    if extra:
        return 0, [f"numbers with no source: {sorted(extra)}"]
    return 100, []


def score_visual(spec, overlays):
    if not short_layout.hook_covers_first_two_seconds(overlays):
        return 0, ["hook text is not on screen for the first 2 seconds"]
    if not short_layout.beats_have_key_text(overlays):
        return 20, ["a beat is missing on-screen key text"]
    mode = (spec or {}).get("mode")
    if mode == "series":
        if not (spec.get("source") and spec.get("as_of")):
            return 0, ["price chart is missing source or as-of date"]
        if spec.get("fabricated"):
            return 0, ["price chart contains a value that was not fetched"]
        if not spec.get("series"):
            return 0, ["price chart has no points"]
        return 100, []
    if mode == "non_numeric":
        labels = " ".join(spec.get("labels") or [])
        if any(ch.isdigit() for ch in labels) or spec.get("has_digits"):
            return 0, ["non-numeric fallback still contains a number"]
        return 82, ["market data unavailable; non-numeric card used"]
    if mode == "schematic":
        return 90, []
    return 0, ["visual mode unknown"]


def score_trend(topic, trends):
    """None when there is nothing to score (file missing or no themes)."""
    themes = _themes(trends)
    if not themes:
        return None, []
    blob = " ".join([
        str((topic or {}).get("cat") or ""),
        str((topic or {}).get("key") or ""),
        str((topic or {}).get("seo") or ""),
        str((topic or {}).get("topic") or ""),
    ]).lower()
    if any(theme in blob for theme in themes):
        return 100, []
    return 45, ["topic does not match data/trends.json"]


def _pack(parts, notes):
    overall = _mean(parts.values())
    included = {k: v for k, v in parts.items() if v is not None}
    passed = bool(included) and overall >= OVERALL_BAR and all(v >= PART_BAR for v in included.values())
    summary = ", ".join(
        f"{name} {'skip' if score is None else score}" for name, score in parts.items()
    )
    summary = f"overall {overall} ({summary})"
    if notes:
        summary += " — " + "; ".join(notes[:4])
    return {
        "parts": parts,
        "overall": overall,
        "passed": passed,
        "public_ok": passed,
        "summary": summary,
        "notes": notes,
        "bar": {"part": PART_BAR, "overall": OVERALL_BAR},
    }


def _reject_fallback(narration, packed):
    """A topic-card fallback cannot pass, even when the numeric scores are high."""
    if not isinstance(narration, dict):
        return packed
    flagged = (
        narration.get("narration_fallback") is True
        or str(narration.get("source") or "").strip().lower() == "fallback"
    )
    if not flagged:
        return packed
    note = "narration fallback is blocked: not an LLM script that passed the hook/teaching gate"
    notes = list(packed.get("notes") or [])
    if note not in notes:
        notes.append(note)
    packed["notes"] = notes
    packed["passed"] = False
    packed["public_ok"] = False
    packed["narration_fallback"] = True
    summary = packed.get("summary") or ""
    if note not in summary:
        packed["summary"] = (summary + " — " + note).strip(" —")
    return packed


def score_content(topic, narration, spec=None, trends=None):
    """Hook, accuracy, and trend. Used to decide whether to regenerate narration."""
    if trends is None and trends != {}:
        trends = load_trends()
    # An explicit empty dict from a caller means "do not read the file".
    hook, hook_notes = score_hook(narration, topic)
    accuracy, acc_notes = score_accuracy(narration, topic, spec)
    trend, trend_notes = score_trend(topic, trends if trends else None)
    notes = hook_notes + acc_notes + trend_notes
    return _reject_fallback(narration, _pack({"hook": hook, "accuracy": accuracy, "trend": trend}, notes))


def score_craft(narration, overlays=None, scenes=None, labels=None, samples=None, text_frames=None,
                layout_notes=None):
    """0 when a scene sits too long, there are too few scenes, a beat is vague, or a label leaves the 6% margin.

    scenes=None derives the cut list from the on-screen beat spans, which is what
    the renderer does. An explicit static list is scored as given.
    labels=None skips the safe-area check. A list of boxes fails if any box
    crosses the 6% frame margin.
    samples, when given, are (time, image) pairs. A span of about 4s with no
    pixel change fails even if a cut sits inside it.
    text_frames, when given, are (time, boxes). Two sprites that cover the same
    pixels fail.
    """
    notes = []
    vague = scene_pace.vague_beats((narration or {}).get("beats"))
    if vague:
        notes.append("vague explanation (no number, level or step): " + ", ".join(vague))
    if labels is not None:
        import safe_area
        ok_boxes, bad = safe_area.check_boxes(labels)
        if not ok_boxes:
            sample = ", ".join(
                f"({b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f})" for b in bad[:3]
            )
            notes.append(f"label outside the 6% safe area: {sample}")
    if scenes is None:
        times = scene_pace.beat_times_from_overlays(overlays or [])
        if len(times) < len(scene_pace.BEATS):
            planned = []
            notes.append("scene plan missing")
        else:
            planned = scene_pace.plan_scenes(times)
    else:
        planned = list(scenes)
    duration = max((float(s["end"]) for s in planned), default=0.0)
    ok, scene_notes = scene_pace.check_scenes(planned, duration)
    if not ok:
        notes.extend(scene_notes)
    if samples:
        notes.extend(scene_pace.unchanged_spans(samples))
        notes.extend(scene_pace.empty_background_spans(samples))
    if text_frames:
        import safe_area
        for t, boxes in text_frames:
            hits = safe_area.overlapping(boxes)
            if hits:
                a, b = hits[0]
                notes.append(
                    "text sprites overlap at "
                    f"{float(t):.1f}s: ({a[0]:.0f},{a[1]:.0f},{a[2]:.0f},{a[3]:.0f}) "
                    f"and ({b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f})"
                )
                break
            for box in boxes:
                # A subtitle that starts in the caption band and crosses the ceiling.
                if box[1] < safe_area.BRAND_TOP and box[3] > safe_area.SUBTITLE_LIMIT + 0.5:
                    notes.append(
                        f"subtitle leaves its band at {float(t):.1f}s "
                        f"(bottom {box[3]:.0f} > {safe_area.SUBTITLE_LIMIT})"
                    )
                    break
            if notes and notes[-1].startswith("subtitle leaves"):
                break
    if layout_notes:
        notes.extend(layout_notes)
    if notes:
        return 0, notes
    return 100, []


def score_sync(report):
    """None when the caller did not transcribe. A failed transcript scores 0."""
    if report is None:
        return None, []
    if report.get("ok"):
        return 100, []
    reason = report.get("reason") or "narration does not match the title and the scenes"
    return 0, [reason]


def full_review(topic, narration, spec, overlays, trends=None, scenes=None, labels=None,
                samples=None, text_frames=None, sync=None, layout_notes=None):
    """All parts, including the visual and the craft cut. public_ok follows `passed`.

    sync=None skips the transcript check (older callers). A report that is not
    ok scores 0 and blocks, including when transcription itself failed.
    """
    if trends is None:
        trends = load_trends()
    hook, hook_notes = score_hook(narration, topic)
    visual, vis_notes = score_visual(spec, overlays)
    accuracy, acc_notes = score_accuracy(narration, topic, spec)
    trend, trend_notes = score_trend(topic, trends)
    craft, craft_notes = score_craft(
        narration, overlays, scenes, labels, samples=samples, text_frames=text_frames,
        layout_notes=layout_notes)
    sync_score, sync_notes = score_sync(sync)
    notes = hook_notes + vis_notes + acc_notes + trend_notes + craft_notes + sync_notes
    packed = _pack(
        {"hook": hook, "visual": visual, "accuracy": accuracy, "trend": trend,
         "craft": craft, "sync": sync_score},
        notes,
    )
    packed["chart"] = {
        "mode": (spec or {}).get("mode"),
        "source": (spec or {}).get("source"),
        "as_of": (spec or {}).get("as_of"),
    }
    return _reject_fallback(narration, packed)
