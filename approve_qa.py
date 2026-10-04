"""Schedule a QA-held video, or make it public now, and defer the Reel.

The Instagram Reel Publish Watchdog posts that Reel once the video is public.
This does not call Instagram itself.
"""
import os
import re
from datetime import datetime, timedelta, timezone

import ig_defer
import ledger
import slot_guard
from common import RUN, die

_SLOT = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_slot(raw):
    text = str(raw or "").strip()
    if text.lower() == "now":
        return "now"
    match = _SLOT.match(text)
    if not match:
        die("slot must be HH:MM IST or now")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        die("slot must be HH:MM IST or now")
    return f"{hour:02d}:{minute:02d}"


def parse_date(raw):
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        die("date_ist must be YYYY-MM-DD")
    return text


def schedule_for(slot, date_ist, now=None):
    """YouTube privacy, the publishAt sent to YouTube, and the deferred instant.

    slot=now makes the video public and marks the Reel due immediately.
    A clock slot stays private and sets publishAt. A time that is not at
    least the upload buffer ahead is refused, so YouTube does not reject it.
    """
    now = now or datetime.now(slot_guard.IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=slot_guard.IST)
    if slot == "now":
        due = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return "public", None, due
    if not date_ist:
        die("date_ist is required unless slot is now")
    when = slot_guard._at(date_ist, slot)
    if when < now + slot_guard.UPLOAD_BUFFER:
        die("That slot is not far enough ahead. Pass slot=now to make the video public now.")
    publish_at = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "private", publish_at, publish_at


def status_body(video_id, privacy, publish_at):
    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status["publishAt"] = publish_at
    return {"id": video_id, "status": status}


def _row(video_id):
    found = None
    for entry in ledger._entries():
        if isinstance(entry, dict) and str(entry.get("video_id") or "") == video_id:
            found = entry
    return found


def approve(video_id, slot, date_ist, yt, now=None, run_dir=None):
    """Set privacy on YouTube, clear pending_qa, and write the deferred Reel."""
    video_id = str(video_id or "").strip()
    if not video_id:
        die("youtube_video_id is required")
    slot = parse_slot(slot)
    date_ist = parse_date(date_ist)
    row = _row(video_id)
    if row is None:
        die(f"ledger has no row for {video_id}")
    source_run = str(row.get("source_run") or "").strip()
    if not source_run:
        die(f"{video_id} has no source_run, so the Reel watchdog cannot fetch the artifact")
    if slot != "now":
        other = slot_guard.find_entry(ledger._entries(), date_ist, slot)
        other_id = str((other or {}).get("video_id") or "")
        if other and other_id and other_id != video_id:
            die(f"{date_ist} {slot} IST is already filled by {other_id}")
    privacy, publish_at, defer_at = schedule_for(slot, date_ist, now=now)
    import publish
    publish.require_channel(yt)
    yt.videos().update(part="status", body=status_body(video_id, privacy, publish_at)).execute()
    if slot == "now":
        ledger.release_qa(video_id)
    else:
        ledger.release_qa(video_id, slot=slot, date_ist=date_ist)
    ig_defer.write_deferred(run_dir or RUN, video_id, defer_at, source_run)
    if publish_at:
        print(f"[qa] {video_id} private publishAt={publish_at}")
    else:
        print(f"[qa] {video_id} public now")
    return {"privacy": privacy, "publish_at": publish_at, "deferred": defer_at, "source_run": source_run}


def main():
    from yt_credentials import youtube_client_or_exit
    video_id = (os.environ.get("YOUTUBE_VIDEO_ID") or "").strip()
    slot = os.environ.get("QA_SLOT") or ""
    date_ist = os.environ.get("QA_DATE_IST") or ""
    yt = youtube_client_or_exit()
    approve(video_id, slot, date_ist, yt)


if __name__ == "__main__":
    main()
