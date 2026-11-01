#!/usr/bin/env python3
"""Pick the newest successful daily-reels build of a kind created TODAY (IST).

Reads `gh run list --json databaseId,displayTitle,conclusion,createdAt` JSON on
stdin. Prints the databaseId, or nothing if no build from today exists.
Never returns a stale build (e.g. 30-Oct must not publish the 14-Oct build).
"""
import json
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def ist_date(created_at):
    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def pick(runs, kind, cron, today=None):
    today = today or datetime.now(IST).date()
    titles = {f"daily-reel {kind} build", f"daily-reel {cron} cron"}
    for r in runs:
        if r.get("conclusion") != "success" or r.get("displayTitle") not in titles:
            continue
        if not r.get("createdAt") or ist_date(r["createdAt"]) != today:
            continue
        return r["databaseId"]
    return None


if __name__ == "__main__":
    kind, cron = sys.argv[1], sys.argv[2]
    rid = pick(json.load(sys.stdin), kind, cron)
    if rid is not None:
        print(rid)
