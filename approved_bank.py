"""Topic slots speak only scripts in approved/*.json.

owner_approved must be true. A script that already has a video id is not
posted again unless reusable is true. DeepSeek is not asked for a script.
When nothing unused is left, the slot is skipped and the reason is written
to the ledger.
"""
import json
import os
from pathlib import Path

ORDER = ("hook", "pehchan", "matlab", "karein")
ROOT = Path(__file__).parent
DIR = ROOT / "approved"
SKIP_NAME = "bank_skip.txt"


def active():
    return os.environ.get("APPROVED_BANK", "").strip().lower() in ("1", "true", "yes")


def _load(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    data["key"] = str(data.get("key") or path.stem).strip()
    data["_file"] = path.name
    return data


def all_scripts():
    rows = []
    for path in sorted(DIR.glob("*.json")):
        data = _load(path)
        if data is not None:
            rows.append(data)
    return rows


def by_key(key):
    want = str(key or "").strip()
    for script in all_scripts():
        if script["key"] == want or Path(script["_file"]).stem == want:
            return script
    return None


def owner_ok(script):
    return (script or {}).get("owner_approved") is True


def posted(script, entries):
    """True when this script already has a video and must not be repeated."""
    if (script or {}).get("reusable") is True:
        return False
    key = str((script or {}).get("key") or "")
    stem = Path(str((script or {}).get("_file") or "")).stem
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("video_id"):
            continue
        if str(entry.get("topic_key") or "") in {key, stem} and key:
            return True
    return False


def select(scripts, entries):
    """The first unused owner-approved script in the given order."""
    for script in scripts or []:
        if not owner_ok(script) or posted(script, entries):
            continue
        return script
    return None


def next_script(entries):
    """The first unused owner-approved script, in filename order."""
    return select(all_scripts(), entries)


def skip_reason(entries, scripts=None):
    scripts = all_scripts() if scripts is None else scripts
    approved = [script for script in scripts if owner_ok(script)]
    if not approved:
        return "no owner_approved script left in approved/"
    if not any(not posted(script, entries) for script in approved):
        return "no unused approved script left; reusable is not set"
    return ""


def narration_from(script):
    import trade_plan
    if trade_plan.is_plan_script(script):
        return trade_plan.narration_from(script)
    beats_in = (script or {}).get("beats") or {}
    if isinstance(beats_in, dict) and all(isinstance(beats_in.get(beat), str) for beat in ORDER):
        beats = {beat: " ".join(str(beats_in.get(beat) or "").split()) for beat in ORDER}
    else:
        beats = {beat: "" for beat in ORDER}
    return {
        "title": " ".join(str((script or {}).get("title") or "").split()),
        "hook_line": " ".join(str((script or {}).get("hook_line") or "").split()),
        "beats": beats,
        "facts": [str(line) for line in ((script or {}).get("facts") or [])],
        "source": "approved",
        "narration_fallback": False,
        "review_attempts": 1,
    }


def topic_from(script):
    """The chart topic. Spoken lines stay on _spoken_beats, not the card."""
    import trade_plan
    narration = narration_from(script)
    if narration.get("plan"):
        on_screen = (script or {}).get("on_screen") or {}
        beats = {}
        for beat in trade_plan.BEATS:
            pair = on_screen.get(beat)
            if isinstance(pair, list) and len(pair) >= 2:
                beats[beat] = [str(pair[0]), str(pair[1])]
            else:
                beats[beat] = ["प्लान", "लेवल"]
        topic = {
            "key": script["key"],
            "cat": str(script.get("cat") or "PATTERN"),
            "topic": narration["title"],
            "thumb": list(script.get("thumb") or ["PLAN", "LEVEL"]),
            "sticker": str(script.get("sticker") or "प्लान"),
            "beats": beats,
            "facts": list(narration["facts"]),
            "seo": str(script.get("seo") or narration["title"]),
            "_spoken_beats": dict(narration["beats"]),
            "_approved_bank": True,
            "_live_short": True,
            "_live_beats": "plan",
            "_voice": {"words_per_second": 1.9},
        }
        if script.get("cover_lines"):
            topic["cover_lines"] = list(script["cover_lines"])
        if script.get("badge"):
            topic["badge"] = script["badge"]
        return topic
    on_screen = (script or {}).get("on_screen") or {}
    beats = {}
    for beat in ORDER:
        pair = on_screen.get(beat)
        if isinstance(pair, list) and len(pair) >= 2:
            beats[beat] = [str(pair[0]), str(pair[1])]
        else:
            beats[beat] = ["पैटर्न", "लेवल"]
    topic = {
        "key": script["key"],
        "cat": str(script.get("cat") or "PATTERN"),
        "topic": narration["title"],
        "thumb": list(script.get("thumb") or ["PATTERN", "LEVEL"]),
        "sticker": str(script.get("sticker") or "पैटर्न"),
        "beats": beats,
        "facts": list(narration["facts"]),
        "seo": str(script.get("seo") or narration["title"]),
        "_spoken_beats": dict(narration["beats"]),
        "_approved_bank": True,
        "_live_short": True,
        "_live_beats": "four",
        "_voice": {"words_per_second": 1.9},
    }
    if script.get("cover_lines"):
        topic["cover_lines"] = list(script["cover_lines"])
    if script.get("badge"):
        topic["badge"] = script["badge"]
    return topic


def narration_for(topic):
    """The file script for this topic. Never a generated draft."""
    import trade_plan
    if trade_plan.has_plan((topic or {}).get("key")) and (
        trade_plan.is_plan_topic(topic) or (topic or {}).get("_approved_bank")
    ):
        script = by_key((topic or {}).get("key"))
        if script is None:
            raise FileNotFoundError(f"approved script missing for {(topic or {}).get('key')}")
        spoken = (topic or {}).get("_spoken_beats")
        if isinstance(spoken, dict) and all(str(spoken.get(beat) or "").strip() for beat in trade_plan.BEATS):
            merged = dict(script)
            merged["beats"] = spoken
            merged["title"] = (topic or {}).get("topic") or script.get("title")
            return trade_plan.narration_from(merged)
        return trade_plan.narration_from(script)
    spoken = (topic or {}).get("_spoken_beats")
    if isinstance(spoken, dict) and all(str(spoken.get(beat) or "").strip() for beat in ORDER):
        script = by_key((topic or {}).get("key")) or {}
        return narration_from({
            "title": (topic or {}).get("topic") or script.get("title"),
            "hook_line": script.get("hook_line") or "",
            "beats": spoken,
            "facts": (topic or {}).get("facts") or script.get("facts") or [],
        })
    script = by_key((topic or {}).get("key"))
    if script is None:
        raise FileNotFoundError(f"approved script missing for {(topic or {}).get('key')}")
    return narration_from(script)


def occupy_skip(reason, slot=None, date_ist=None):
    """A no-video ledger row. Slot watch must not fill it with another topic."""
    import ledger
    slot = slot or os.environ.get("PUBLISH_SLOT_IST", "").strip() or "08:30"
    date_ist = date_ist or os.environ.get("PUBLISH_DATE_IST", "").strip() or None
    ledger.record_publish(
        None,
        slot=slot,
        platform="skip",
        date_ist=date_ist,
        topic_key=None,
        note=reason,
        slot_skipped=reason,
    )


def skip_marker(reason):
    from common import RUN
    (RUN / SKIP_NAME).write_text(reason + "\n", encoding="utf-8")


def clear_skip_marker():
    from common import RUN
    path = RUN / SKIP_NAME
    if path.exists():
        path.unlink()
