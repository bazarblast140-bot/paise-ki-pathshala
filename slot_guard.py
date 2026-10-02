"""Which upcoming slot should this run fill? (ledger: data/published.json)

Scheduled runs build 12–14h ahead of the slot and YouTube publishes at
publishAt. GitHub cron is late, drops :00 / :30, and often misses the
22:30–01:30 UTC crush. A run takes the earliest unfilled slot that is at
least ~2h ahead and still inside the ~26h lookahead, one slot per run.
A missed slot is never rolled onto another slot and never published instantly.

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

# Selection-time lead. A build has to finish and still leave YouTube a
# future publishAt. About 2h: closer than this is too late to start, and the
# missed slot is left missed. Pass min_lead= to next_unfilled to override.
MIN_LEAD = timedelta(hours=2)

# How far ahead one run may build. About 26h. The slots are 7.5h, 5h and
# 11.5h apart, so 26h always includes the next two future slots (and usually
# the third) while the picker still takes only the earliest open one.
# Pass max_lookahead= to next_unfilled to override.
MAX_LOOKAHEAD = timedelta(hours=26)

# Steady state: at least this many future slots already uploaded
# (private + publishAt). The health check warns when the ledger is below it.
MIN_FUTURE_SCHEDULED = 2

# GitHub's busiest window (UTC). Schedules here are often late or dropped.
# 22:30 inclusive through 01:30 exclusive.
BUSY_UTC_START = 22 * 60 + 30
BUSY_UTC_END = 1 * 60 + 30

# Nine UTC crons, odd minutes, all outside 22:30–01:30 UTC. Three chances per
# slot, each 12–14h before that slot's publish time. The label is the slot
# those chances are aimed at. The picker ignores the label: whichever cron
# fires, it builds the earliest unfilled slot inside [MIN_LEAD, MAX_LOOKAHEAD]
# and schedules it with publishAt. One slot per run. A later chance in the
# same cluster moves on to the following open slot (still inside 26h), so the
# ledger can hold two or more future private + publishAt rows.
# 08:30 IST = 03:00 UTC is armed the previous evening (3 Oct 08:30 on the
# evening of 2 Oct), never inside the midnight crush.
#
#   01:47 UTC = 07:17 IST  → 21:00 in 13h43m
#   02:23 UTC = 07:53 IST  → 21:00 in 13h07m
#   03:13 UTC = 08:43 IST  → 21:00 in 12h17m
#   13:07 UTC = 18:37 IST  → next 08:30 in 13h53m
#   13:47 UTC = 19:17 IST  → next 08:30 in 13h13m
#   14:23 UTC = 19:53 IST  → next 08:30 in 12h37m
#   20:37 UTC = 02:07 IST  → 16:00 in 13h53m
#   21:13 UTC = 02:43 IST  → 16:00 in 13h17m
#   21:47 UTC = 03:17 IST  → 16:00 in 12h43m
PUBLISH_CRONS = (
    ("47 1 * * *", "21:00"),
    ("23 2 * * *", "21:00"),
    ("13 3 * * *", "21:00"),
    ("7 13 * * *", "08:30"),
    ("47 13 * * *", "08:30"),
    ("23 14 * * *", "08:30"),
    ("37 20 * * *", "16:00"),
    ("13 21 * * *", "16:00"),
    ("47 21 * * *", "16:00"),
)

# Read-only health check, odd minute, outside the busy UTC window, and before
# that slot's earliest 12–14h build.
# (cron UTC, check IST, slot IST, lead minutes before the slot)
#   11:47 UTC = 17:17 IST → 08:30 (before the 13:07 UTC / 18:37 IST build)
#   19:23 UTC = 00:53 IST → 16:00 (before the 20:37 UTC / 02:07 IST build)
#   21:23 UTC = 02:53 IST → 21:00 (before the 01:47 UTC / 07:17 IST build)
HEALTH_SCHEDULE = (
    ("47 11 * * *", "17:17", "08:30", 913),
    ("23 19 * * *", "00:53", "16:00", 907),
    ("23 21 * * *", "02:53", "21:00", 1087),
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


def is_withdrawn(entry):
    """A private withdrawn upload is history. It does not occupy the clock slot."""
    return str((entry or {}).get("status") or "").strip().lower() == "withdrawn"


def find_entry(entries, date_ist, slot):
    """First row that still occupies this slot.

    Withdrawn rows stay in the ledger so that video id is not republished,
    but they leave the clock slot open for a new gated upload. A later real
    row or a hold still occupies the slot.
    """
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("date_ist") != date_ist or entry.get("slot") != slot:
            continue
        if is_withdrawn(entry):
            continue
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


def _slot_rows(entries, date_ist, slot):
    return [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("date_ist") == date_ist and entry.get("slot") == slot
    ]


def slot_is_scheduled(entries, date_ist, slot):
    """True when this future slot is already uploaded for publishAt.

    A hold, a withdrawal, or an Instagram-only row (no YouTube id) is not a
    private + publishAt upload. A withdrawn row does not cancel a later real
    upload on the same clock slot.
    """
    rows = [entry for entry in _slot_rows(entries, date_ist, slot) if not is_withdrawn(entry)]
    if any(is_hold(entry) for entry in rows):
        return False
    return any(str(entry.get("video_id") or "").strip() for entry in rows)


def future_scheduled_slots(now=None, entries=None):
    """Canonical slots still in the future that already have a YouTube upload."""
    now = now or datetime.now(IST)
    if entries is None:
        entries = _read()
    found = []
    seen = set()
    for when in _candidates(now):
        if when <= now:
            continue
        date_ist = when.strftime("%Y-%m-%d")
        slot = when.strftime("%H:%M")
        key = (date_ist, slot)
        if key in seen:
            continue
        seen.add(key)
        if not slot_is_scheduled(entries, date_ist, slot):
            continue
        found.append({
            "slot": slot,
            "date_ist": date_ist,
            "publish_at": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return found


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
    hit = find_entry(entries, date, slot)
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
