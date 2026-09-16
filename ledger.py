"""Kaunsa video kis slot par gaya — slot_watch.py isi se dekhta hai ki slot khali to nahi.

publish.py har safal upload ke baad likhta hai; workflow ise commit karta hai.
"""
import json
from datetime import datetime, timedelta, timezone

from common import ROOT, video_format

IST = timezone(timedelta(hours=5, minutes=30))
PATH = ROOT / "data" / "published.json"


def record_publish(video_id, publish_at=None, slot=None):
    """publishAt (UTC) se IST din nikalta hai — raat ka run agle din ke slot me ginta hai."""
    when = (datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if publish_at else datetime.now(timezone.utc)).astimezone(IST)
    try:
        entries = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = []
    slot = slot or when.strftime("%H:%M")
    entries.append({"date_ist": when.strftime("%Y-%m-%d"),
                    "slot": slot,
                    "video_id": video_id,
                    "format": video_format()})
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(entries[-300:], indent=1) + "\n", encoding="utf-8")
    print(f"[OK] ledger: {when:%Y-%m-%d} {slot} -> {video_id}")
