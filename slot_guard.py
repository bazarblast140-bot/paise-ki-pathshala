"""Which upcoming slot should this run fill? (ledger: data/published.json)

Scheduled runs build AHEAD of the slot and YouTube publishes at publishAt.
GitHub cron is late and drops :00 / :30 schedules, so each slot has extra
early triggers. A run is idempotent: it takes the next unfilled slot whose
publish time is still far enough ahead, and it never rolls a missed slot
onto the next day (that was pushing today's video into tomorrow's slot).

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
# that is left. 45 minutes is the floor; the crons themselves fire 1.5–4h early.
MIN_LEAD = timedelta(minutes=45)

# Only the upcoming slot. The gap to the slot after next is longer than this,
# so an early trigger for 08:30 cannot also fill 16:00.
MAX_LOOKAHEAD = timedelta(hours=6)

# UTC crons, odd minutes only. GitHub sheds :00 and :30 under load.
# Per slot: about 3–4h before, about 2–3h before, and a backup still before
# the publish time. 08:30 IST = 03:00 UTC.
#   23:47 UTC = 05:17 IST (3h13m lead) — still before 03:00 UTC on a same-day merge
#   00:17 UTC = 05:47 IST (2h43m)
#   01:23 UTC = 06:53 IST (1h37m backup)
PUBLISH_CRONS = (
    ("47 23 * * *", "08:30"),
    ("17 0 * * *", "08:30"),
    ("23 1 * * *", "08:30"),
    ("41 6 * * *", "16:00"),
    ("47 7 * * *", "16:00"),
    ("53 8 * * *", "16:00"),
    ("47 11 * * *", "21:00"),
    ("13 13 * * *", "21:00"),
    ("7 14 * * *", "21:00"),
)

# Read-only health check, odd minute, before that slot's earliest build.
# (cron UTC, check IST, slot IST, lead minutes before the slot)
HEALTH_SCHEDULE = (
    ("17 23 * * *", "04:47", "08:30", 223),
    ("11 6 * * *", "11:41", "16:00", 259),
    ("17 11 * * *", "16:47", "21:00", 253),
)

# After the slot, odd minutes. Alert only; a fill uses the same ahead-of-slot
# picker (scheduled publishAt), never an extra instant-public post.
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
    """The next ledger-empty slot inside the lead window, or None.

    One slot per call. A filled slot is skipped. Nothing outside the window
    is chosen, so a duplicate early run does not fill the rest of the day.
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
