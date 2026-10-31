"""Refuse to publish a stale build ("[STOP] stale build").

Run 41199355446 (Sat 31-Oct-2026) published a 14-Oct artifact from branch
cursor/reel-concat-path-00e1 with Nifty data from 09-Oct. A build may only be
published when its data date is the right session (close: today's trading
session, weekdays only; news/TA/recap: the latest completed session, or today
for news, any day) and its source run
is on the default branch and was created today (IST).
"""
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import nifty_day

IST = timezone(timedelta(hours=5, minutes=30))
DATED_KINDS = {"close", "ta", "recap", "news"}


def today_ist(now=None):
    return (now or datetime.now(IST)).astimezone(IST).date()


def latest_completed_session(now=None):
    now = (now or datetime.now(IST)).astimezone(IST)
    day = now.date()
    if nifty_day.is_trading_day(day) and (now.hour, now.minute) >= nifty_day.CASH_CLOSE:
        return day
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if nifty_day.is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def data_date(run_dir):
    run = pathlib.Path(run_dir)
    qa = next(iter(run.rglob("qa.json")), None)
    if qa:
        try:
            d = json.loads(qa.read_text(encoding="utf-8")).get("session")
            if d:
                return str(d)[:10]
        except ValueError:
            pass
    lv = next(iter(run.rglob("levels.json")), None)
    if lv:
        try:
            d = json.loads(lv.read_text(encoding="utf-8")).get("last_date")
            if d:
                return d[:10]
        except ValueError:
            pass
    src = next(iter(run.rglob("source.md")), None)
    if src:
        m = re.search(r"\bdate (\d{4}-\d{2}-\d{2})", src.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return None


def build_kind(run_dir):
    qa = next(iter(pathlib.Path(run_dir).rglob("qa.json")), None)
    if qa:
        try:
            return json.loads(qa.read_text(encoding="utf-8")).get("kind")
        except ValueError:
            return None
    return None


BOX_TAG_DATE = re.compile(r"/releases/download/box-reel-[a-z]+-(\d{4}-\d{2}-\d{2})/")


def check(run_dir, run_meta=None, default_branch="main", now=None, bundle_url=""):
    """Return a list of problems; empty means fresh.

    run_meta is the source Actions run (source_run_id path). bundle_url is the
    override mp4 release URL (box release-bundle path, no source run); a
    box-reel-<kind>-<date> tag must be dated today IST.
    """
    today = today_ist(now)
    problems = []
    m = BOX_TAG_DATE.search(bundle_url or "")
    if m and m.group(1) != today.isoformat():
        problems.append(f"box release tag dated {m.group(1)}, not today {today}")
    kind = build_kind(run_dir)
    d = data_date(run_dir)
    if d is None:
        if kind in DATED_KINDS:
            problems.append(f"{kind} build has no data date (levels.json/source.md)")
    elif kind == "close":
        # The close Reel is weekdays only: data must be today's trading session.
        if not nifty_day.is_trading_day(today):
            problems.append(f"today {today} is not an NSE trading session; close data date {d}")
        elif d != today.isoformat():
            problems.append(f"data date {d} is not today's IST session {today}")
    else:
        # News, TA and recap run every day: data must be the latest completed
        # session (news may also carry today's date).
        valid = {latest_completed_session(now).isoformat()}
        if kind == "news":
            valid.add(today.isoformat())
        if d not in valid:
            problems.append(f"data date {d} is not the latest session {sorted(valid)} for {kind or 'build'}")
    if run_meta:
        branch = run_meta.get("head_branch")
        if branch != default_branch:
            problems.append(f"source run {run_meta.get('id')} is on branch {branch!r}, not {default_branch!r}")
        created = run_meta.get("created_at")
        if not created:
            problems.append("source run has no created_at")
        else:
            c = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(IST).date()
            if c != today:
                problems.append(f"source run created {c} IST, not today {today}")
    return problems


def fetch_run(run_id, repo):
    out = subprocess.run(["gh", "api", f"repos/{repo}/actions/runs/{run_id}"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main():
    run_dir = os.environ.get("RUN_DIR", "run")
    run_id = os.environ.get("SOURCE_RUN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "bazarblast140-bot/paise-ki-pathshala")
    default = os.environ.get("DEFAULT_BRANCH", "main")
    meta = fetch_run(run_id, repo) if run_id else None
    problems = check(run_dir, meta, default, bundle_url=os.environ.get("OVERRIDE_MP4_URL", "") if not run_id else "")
    if problems:
        for p in problems:
            print(f"[STOP] stale build: {p}")
        print("::error::[STOP] stale build - refusing to publish")
        return 1
    print("Fresh build: data date and source run are today's")
    return 0


if __name__ == "__main__":
    sys.exit(main())
