"""Which upcoming slot should this run fill? (ledger: data/published.json)

Scheduled runs build AHEAD of the slot and YouTube publishes at publishAt.
GitHub cron is late, drops :00 / :30, and often misses the 22:30–01:30 UTC
crush entirely. Crons are spread across the rest of the day. A run is
idempotent: it takes the earliest unfilled slot still inside the lookahead
and schedules it with publishAt. A missed slot is never rolled onto another
slot and never published instantly.

Usage:
  python slot_guard.py --next
      stdout: slot=08:30 / date_ist=YYYY-MM-DD / publish_at=...Z / taken=true|false
  python slot_guard.py 16:00
      stdout: taken=true or taken=false for that clock slot (legacy check)
"""
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
LEDGER = pathlib.Path(__file__).parent / "data" / "published.json"

SLOTS = ("08:30", "16:00", "21:00")

# Upload-time buffer. YouTube rejects a publishAt that is not in the future.
# publish.py refuses rather than adding a day when the instant is inside this.
UPLOAD_BUFFER = timedelta(minutes=20)

# Selection-time lead. The build has to finish before UPLOAD_BUFFER is all
# that is left. 45 minutes is the floor. Crons themselves fire at least 2h early.
MIN_LEAD = timedelta(minutes=45)

# How far ahead one run may build. About 14h, so any cron outside the busy
# UTC window can still reach the next empty slot, including 08:30 from the
# previous afternoon. Pass max_lookahead= to next_unfilled to override.
# Longer than this starts to reach the slot after next when the near one is
# still the one that should be built first — the picker still takes the
# earliest open slot, and stops at this horizon.
MAX_LOOKAHEAD = timedelta(hours=14)

# GitHub's busiest window (UTC). Schedules here are often late or dropped.
# 22:30 inclusive through 01:30 exclusive.
BUSY_UTC_START = 22 * 60 + 30
BUSY_UTC_END = 1 * 60 + 30

# Eight UTC crons, odd minutes, all outside 22:30–01:30 UTC. The label is the
# slot that trigger is counted toward. The picker ignores the label: whichever
# cron fires, it builds the earliest unfilled slot inside
# [MIN_LEAD, MAX_LOOKAHEAD] and schedules it with publishAt.
# Each slot has at least three chances at least 2h ahead. 21:00 (the night
# slot) gets those chances from the morning onward, not only just before it.
# 08:30 IST = 03:00 UTC is never armed inside the midnight crush; its chances
# are the previous afternoon and evening.
#
#   02:17 UTC = 07:47 IST  → 16:00 in 8h13m, 21:00 in 13h13m
#   04:43 UTC = 10:13 IST  → 16:00 in 5h47m, 21:00 in 10h47m
#   07:07 UTC = 12:37 IST  → 16:00 in 3h23m, 21:00 in 8h23m
#   13:13 UTC = 18:43 IST  → 21:00 in 2h17m, next 08:30 in 13h47m
#   15:47 UTC = 21:17 IST  → next 08:30 in 11h13m
#   17:23 UTC = 22:53 IST  → next 08:30 in 9h37m
#   19:41 UTC = 01:11 IST  → next 08:30 in 7h19m
#   21:17 UTC = 02:47 IST  → next 08:30 in 5h43m, 16:00 in 13h13m
PUBLISH_CRONS = (
    ("17 2 * * *", "16:00"),
    ("43 4 * * *", "16:00"),
    ("7 7 * * *", "16:00"),
    ("13 13 * * *", "21:00"),
    ("47 15 * * *", "08:30"),
    ("23 17 * * *", "08:30"),
    ("41 19 * * *", "08:30"),
    ("17 21 * * *", "08:30"),
)

# Read-only health check, odd minute, outside the busy UTC window, and before
# that slot's earliest build (the build with the longest in-window lead).
# (cron UTC, check IST, slot IST, lead minutes before the slot)
#   11:47 UTC = 17:17 IST → 08:30 (before the 13:13 UTC / 18:43 IST build)
#   19:53 UTC = 01:23 IST → 16:00 (before the 21:17 UTC / 02:47 IST build)
#   21:47 UTC = 03:17 IST → 21:00 (before the 02:17 UTC / 07:47 IST build)
HEALTH_SCHEDULE = (
    ("47 11 * * *", "17:17", "08:30", 913),
    ("53 19 * * *", "01:23", "16:00", 877),
    ("47 21 * * *", "03:17", "21:00", 1063),
)

# After the slot, odd minutes, outside 22:30–01:30 UTC. Alert only; a fill
# uses the same lookahead picker (scheduled publishAt), never an extra
# instant-public post.
#   03:19 UTC = 08:49 IST → 08:30 (19 min after)
#   11:19 UTC = 16:49 IST → 16:00 (49 min after)
#   16:19 UTC = 21:49 IST → 21:00 (49 min after)
WATCH_CRONS = (
    ("19 3 * * *", "08:30"),
    ("19 11 * * *", "16:00"),
    ("19 16 * * *", "21:00"),
)


def _read(path=None):
    file = path or LEDGER
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _at(date_ist, slot):
    hh, mm = (int(x) for x in slot.split(":"))
    year, month, day = (int(x) for x in date_ist.split("-"))
    return datetime(year, month, day, hh, mm, tzinfo=IST)


def find_entry(entries, date_ist, slot):
    for entry in entries:
        if isinstance(entry, dict) and entry.get("date_ist") == date_ist and entry.get("slot") == slot:
            return entry
    return None


def is_hold(entry):
    """A hold row blocks the slot and must not be described as a missing video id."""
    return str((entry or {}).get("platform") or "").strip().lower() == "hold"


def row_state(entry):
    """'open' (no row), 'held' (platform hold), or 'filled' (any other row)."""
    if not entry:
        return "open"
    if is_hold(entry):
        return "held"
    return "filled"


def _filled(entries, date_ist, slot):
    # Held and filled both stay out of the picker. A hold is not an empty slot.
    return row_state(find_entry(entries, date_ist, slot)) != "open"


def scheduled_slot_block():
    """Reason to exit before paid APIs, or None when this run may build.

    Public scheduled builds name their slot via PUBLISH_DATE_IST and
    PUBLISH_SLOT_IST. Unlisted previews and immediate posts (no slot) are
    not blocked here.
    """
    vis = os.environ.get("PUBLISH_VISIBILITY", "").strip().lower()
    slot = os.environ.get("PUBLISH_SLOT_IST", "").strip()
    date = os.environ.get("PUBLISH_DATE_IST", "").strip()
    if vis not in ("public", "now") or not slot or not date:
        return None
    state = row_state(find_entry(_read(), date, slot))
    if state == "held":
        return f"{date} {slot} IST is on hold"
    if state == "filled":
        return f"{date} {slot} IST is already filled"
    return None


def _candidates(now):
    start = now.date() - timedelta(days=1)
    out = []
    for offset in range(4):
        day = (start + timedelta(days=offset)).isoformat()
        for slot in SLOTS:
            out.append(_at(day, slot))
    out.sort()
    return out


def cron_lead_minutes(cron, slot):
    """Minutes from the UTC cron fire time until that slot's UTC publish time."""
    minute, hour, *_ = cron.split()
    sh, sm = (int(x) for x in slot.split(":"))
    slot_utc = (sh * 60 + sm - 5 * 60 - 30) % (24 * 60)
    cron_min = int(hour) * 60 + int(minute)
    return (slot_utc - cron_min) % (24 * 60)


def cron_in_busy_window(cron):
    """True when this UTC cron sits in GitHub's 22:30–01:30 crush."""
    minute, hour, *_ = cron.split()
    minute_of_day = int(hour) * 60 + int(minute)
    return minute_of_day >= BUSY_UTC_START or minute_of_day < BUSY_UTC_END


def covering_leads(slot, min_minutes=120, max_minutes=None):
    """(cron, lead minutes) for triggers that can still build this slot.

    A chance counts when the lead is at least min_minutes and not past
    MAX_LOOKAHEAD. The cron label is ignored; any trigger in that window
    can fill the slot if it is the earliest open one.
    """
    if max_minutes is None:
        max_minutes = int(MAX_LOOKAHEAD.total_seconds() // 60)
    found = []
    for cron, _label in PUBLISH_CRONS:
        lead = cron_lead_minutes(cron, slot)
        if min_minutes <= lead <= max_minutes:
            found.append((cron, lead))
    return found


def target_date(slot, now=None):
    """IST date for a ledger row.

    PUBLISH_DATE_IST / PUBLISH_AT_UTC win — the guard already chose them.
    Without those, a clock time that has passed rolls one day. Scheduled
    uploads do not use that roll-forward; they refuse instead.
    """
    forced = os.environ.get("PUBLISH_DATE_IST", "").strip()
    if forced:
        return forced
    publish_at = os.environ.get("PUBLISH_AT_UTC", "").strip()
    if publish_at:
        when = datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return when.astimezone(IST).strftime("%Y-%m-%d")
    now = now or datetime.now(IST)
    hh, mm = (int(x) for x in slot.split(":"))
    when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if when < now + UPLOAD_BUFFER:
        when += timedelta(days=1)
    return when.strftime("%Y-%m-%d")


def scheduled_publish_at(slot, now=None, date_ist=None):
    """UTC publishAt for this slot, or None if it is no longer far enough ahead.

    Does not add a day. A missed slot stays missed so tomorrow's slot is not taken.
    """
    now = now or datetime.now(IST)
    if date_ist:
        when = _at(date_ist, slot)
    else:
        when = _at(now.strftime("%Y-%m-%d"), slot)
    if when < now + UPLOAD_BUFFER:
        return None
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_unfilled(now=None, entries=None, min_lead=None, max_lookahead=None):
    """The earliest ledger-open slot inside the lead window, or None.

    One slot per call, in time order. Hold rows and filled rows are skipped.
    A missed slot (inside MIN_LEAD, or already past) is left missed: it is
    not rolled onto a later slot and not published instantly. The following
    open slot is chosen only when it is still inside max_lookahead.
    """
    now = now or datetime.now(IST)
    if entries is None:
        entries = _read()
    if min_lead is None:
        min_lead = MIN_LEAD
    if max_lookahead is None:
        max_lookahead = MAX_LOOKAHEAD
    for when in _candidates(now):
        delta = when - now
        if delta < min_lead:
            continue
        if delta > max_lookahead:
            break
        date_ist = when.strftime("%Y-%m-%d")
        slot = when.strftime("%H:%M")
        if _filled(entries, date_ist, slot):
            continue
        return {
            "slot": slot,
            "date_ist": date_ist,
            "publish_at": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    return None


def taken(slot, now=None):
    try:
        entries = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    date = target_date(slot, now)
    hit = next((e for e in entries if e.get("date_ist") == date and e.get("slot") == slot), None)
    return hit and (date, hit)


def _now():
    return datetime.now(IST)


def _emit_next(now=None):
    choice = next_unfilled(now or _now())
    if not choice:
        print("[skip] no unfilled slot far enough ahead", file=sys.stderr)
        print("slot=")
        print("date_ist=")
        print("publish_at=")
        print("taken=true")
        return
    print(
        f"[slot] {choice['date_ist']} {choice['slot']} IST "
        f"publishAt {choice['publish_at']}",
        file=sys.stderr,
    )
    print(f"slot={choice['slot']}")
    print(f"date_ist={choice['date_ist']}")
    print(f"publish_at={choice['publish_at']}")
    print("taken=false")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--next":
        _emit_next()
        return
    slot = argv[0].strip() if argv else ""
    hit = taken(slot) if slot else None
    if hit:
        date, entry = hit
        if is_hold(entry):
            print(f"[skip] {date} {slot} IST is on hold", file=sys.stderr)
        else:
            where = (
                f"https://youtu.be/{entry['video_id']}"
                if entry.get("video_id") else entry.get("platform", "instagram")
            )
            print(f"[skip] {date} {slot} IST ka video pehle se hai: {where}", file=sys.stderr)
    print("taken=true" if hit else "taken=false")


if __name__ == "__main__":
    main()
