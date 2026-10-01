"""Kaunsa video kis slot par gaya — slot_watch.py isi se dekhta hai ki slot khali to nahi.

publish.py har safal upload ke baad likhta hai; workflow ise commit karta hai.
"""
import json
from datetime import datetime, timedelta, timezone

from common import ROOT, video_format

IST = timezone(timedelta(hours=5, minutes=30))
PATH = ROOT / "data" / "published.json"


def record_publish(video_id, publish_at=None, slot=None, platform="youtube", date_ist=None, topic_key=None, note=None, voice_engine=None, voice_fallback=None):
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
    entries.append(entry)
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(entries[-300:], indent=1) + "\n", encoding="utf-8")
    print(f"[OK] ledger: {entry['date_ist']} {slot} -> {video_id or platform}")
