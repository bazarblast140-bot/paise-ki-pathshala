"""Us slot ka video pehle se bana hua hai kya? (ledger: data/published.json)

Kyun: khali slot ab apne aap bhar jaata hai ("Slot watch" workflow). Uske baad
usi slot ka scheduled run bhi chalta — ek hi waqt par do video. Ye guard aise
run ko pehle hi rok deta hai.

Usage: python slot_guard.py 16:00   -> stdout par "taken=true" ya "taken=false"
Sirf stdlib.
"""
import json, pathlib, sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
LEDGER = pathlib.Path(__file__).parent / "data" / "published.json"


def target_date(slot, now=None):
    """publish.py wahi niyam: slot nikal chuka (ya 20 min me hai) to agla din."""
    now = now or datetime.now(IST)
    hh, mm = (int(x) for x in slot.split(":"))
    when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if when < now + timedelta(minutes=20):
        when += timedelta(days=1)
    return when.strftime("%Y-%m-%d")


def taken(slot, now=None):
    try:
        entries = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    date = target_date(slot, now)
    hit = next((e for e in entries if e.get("date_ist") == date and e.get("slot") == slot), None)
    return hit and (date, hit)


def main():
    slot = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    hit = taken(slot) if slot else None
    if hit:
        date, e = hit
        where = f"https://youtu.be/{e['video_id']}" if e.get("video_id") else e.get("platform", "instagram")
        print(f"[skip] {date} {slot} IST ka video pehle se hai: {where}", file=sys.stderr)
    print("taken=true" if hit else "taken=false")


if __name__ == "__main__":
    main()
