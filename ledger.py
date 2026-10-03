"""Kaunsa video kis slot par gaya — slot_watch.py isi se dekhta hai ki slot khali to nahi.

publish.py har safal upload ke baad likhta hai; workflow ise commit karta hai.
"""
import json
from datetime import datetime, timedelta, timezone

from common import ROOT, video_format

IST = timezone(timedelta(hours=5, minutes=30))
PATH = ROOT / "data" / "published.json"


_CREDIT_KEYS = (
    "deepseek_calls",
    "deepseek_prompt_tokens",
    "deepseek_completion_tokens",
    "elevenlabs_characters_sent",
    "elevenlabs_characters_remaining",
    "tts_engine",
)


def record_publish(video_id, publish_at=None, slot=None, platform="youtube", date_ist=None, topic_key=None, note=None, voice_engine=None, voice_fallback=None, credits=None, source=None, buffer_id=None):
    """publishAt (UTC) se IST din nikalta hai — raat ka run agle din ke slot me ginta hai.
    platform="instagram" + video_id=None = sirf IG Reel gaya (YouTube token dead etc.);
    slot phir bhi bhara maana jaata hai taaki Slot watch duplicate fill na chalaye."""
    when = (datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if publish_at else datetime.now(timezone.utc)).astimezone(IST)
    try:
        entries = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = []
    slot = slot or when.strftime("%H:%M")
    if topic_key is None:
        try:
            topic_key = json.loads((ROOT / "run" / "topic.json").read_text(encoding="utf-8")).get("key")
        except (OSError, ValueError):
            topic_key = None
    entry = {"date_ist": date_ist or when.strftime("%Y-%m-%d"),
             "slot": slot,
             "video_id": video_id,
             "format": video_format()}
    if platform != "youtube":
        entry["platform"] = platform
    if topic_key:
        entry["topic_key"] = topic_key   # lets competitor_research learn from our own results
    if note:
        entry["note"] = " ".join(str(note).split())[:300]
    if voice_engine:
        entry["voice_engine"] = str(voice_engine)
    if voice_fallback:
        entry["voice_fallback"] = " ".join(str(voice_fallback).split())[:300]
    if isinstance(credits, dict):
        for key in _CREDIT_KEYS:
            if key in credits:
                entry[key] = credits[key]
    if source:
        entry["source"] = str(source)
    if buffer_id:
        entry["buffer_id"] = str(buffer_id)
    entries.append(entry)
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(entries[-300:], indent=1) + "\n", encoding="utf-8")
    print(f"[OK] ledger: {entry['date_ist']} {slot} -> {video_id or platform}")


def _entries():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def find_ig_media(video_id=None, topic_key=None, content_hash=None, title_norm=None) -> str:
    """Media id already stored for this video, or "" if none.

    A match is the YouTube video id, the topic key plus the content hash,
    the same topic key (a re-render still has a new hash), or the normalized title.
    Rows without ig_media_id do not count — a YouTube-only upload is not an IG post.
    """
    video_id = (video_id or "").strip()
    topic_key = (topic_key or "").strip()
    content_hash = (content_hash or "").strip()
    title_norm = (title_norm or "").strip()
    for entry in reversed(_entries()):
        if not isinstance(entry, dict):
            continue
        media_id = str(entry.get("ig_media_id") or "").strip()
        if not media_id:
            continue
        if video_id and entry.get("video_id") == video_id:
            return media_id
        same_topic = topic_key and entry.get("topic_key") == topic_key
        if same_topic and content_hash and entry.get("content_hash") == content_hash:
            return media_id
        if same_topic:
            return media_id
        if title_norm and entry.get("title_norm") == title_norm:
            return media_id
    return ""


def note_ig_deferred(video_id, status, run_id=None):
    """Remember a Reel that waits for the YouTube video to become public.

    The row is the one publish.py already wrote. source_run is the Actions
    run whose artifact still has final.mp4 and the cover.
    """
    video_id = str(video_id or "").strip()
    status = " ".join(str(status or "").split())[:300]
    if not video_id or not status:
        return False
    entries = _entries()
    idx = None
    for i, entry in enumerate(entries):
        if isinstance(entry, dict) and str(entry.get("video_id") or "") == video_id:
            idx = i
    if idx is None:
        print(f"[warn] ledger has no row for {video_id}; ig_deferred not appended")
        return False
    entries[idx]["ig_deferred"] = status
    run_id = str(run_id or "").strip()
    if run_id:
        entries[idx]["source_run"] = run_id
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(entries[-300:], indent=1) + "\n", encoding="utf-8")
    print(f"[ig] ledger deferred for {video_id}")
    return True


def note_ig_skipped(video_id, reason):
    """Note a missed Reel on the YouTube row for this video.

    publish.py already appended that row. A second row for the same slot would
    look like another fill and is how a later run uploaded again.
    """
    video_id = str(video_id or "").strip()
    note = " ".join(str(reason or "").split())[:300]
    if not video_id or not note:
        return False
    entries = _entries()
    idx = None
    for i, entry in enumerate(entries):
        if isinstance(entry, dict) and str(entry.get("video_id") or "") == video_id:
            idx = i
    if idx is None:
        print(f"[warn] ledger has no row for {video_id}; ig_skipped not appended")
        return False
    entries[idx]["ig_skipped"] = note
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(entries[-300:], indent=1) + "\n", encoding="utf-8")
    print(f"[ig] ledger ig_skipped for {video_id}")
    return True


def attach_ig_media(media_id, *, video_id=None, topic_key=None, content_hash=None, title_norm=None):
    """Store the IG media id the moment media_publish returns.

    Updates the YouTube row for this video when there is one. Otherwise appends
    a row with slot "ig", which is not one of the three public slots.
    """
    media_id = str(media_id or "").strip()
    if not media_id:
        return
    video_id = (video_id or "").strip() or None
    topic_key = (topic_key or "").strip() or None
    content_hash = (content_hash or "").strip() or None
    title_norm = (title_norm or "").strip() or None
    entries = _entries()
    idx = None
    if video_id:
        for i, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("video_id") == video_id:
                idx = i
    if idx is None and topic_key:
        for i, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("topic_key") == topic_key and not entry.get("ig_media_id"):
                idx = i
    if idx is not None:
        entry = entries[idx]
        entry["ig_media_id"] = entry.get("ig_media_id") or media_id
        if topic_key:
            entry["topic_key"] = topic_key
        if content_hash:
            entry["content_hash"] = content_hash
        if title_norm:
            entry["title_norm"] = title_norm
    else:
        when = datetime.now(IST)
        entries.append({
            "date_ist": when.strftime("%Y-%m-%d"),
            "slot": "ig",
            "video_id": video_id,
            "format": video_format(),
            "platform": "instagram",
            "ig_media_id": media_id,
            "topic_key": topic_key,
            "content_hash": content_hash,
            "title_norm": title_norm,
        })
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(entries[-300:], indent=1) + "\n", encoding="utf-8")
    print(f"[ig] ledger media id {media_id}")
