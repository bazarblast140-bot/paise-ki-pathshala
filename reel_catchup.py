"""Catch up a daily Reel when GitHub drops its scheduled crons.

For news, close and TA: once the publish time has passed by more than 20
minutes and today (IST) shows no daily-reels run for that kind and no ledger
entry in its window, dispatch daily-reels.yml with kind, phase=both, live.
Any existing run today, of any status (a HOLD included), means no dispatch,
so this never posts twice.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
GRACE_MIN = 20
LATE_MIN = 120  # after this the Reel is stale and would crowd the next window
# kind: (build cron, publish cron, publish HH:MM IST)
WINDOWS = {
    "news": ("50 2 * * 1-5", "15 3 * * 1-5", "08:45"),
    "close": ("20 10 * * 1-5", "45 10 * * 1-5", "16:15"),
    "ta": ("35 12 * * 1-5", "0 13 * * 1-5", "18:30"),
}
LEDGER = Path(__file__).resolve().parent / "data" / "published.json"


def _at(now, hhmm):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def run_kind(run):
    title = str(run.get("display_title") or run.get("displayTitle") or "")
    for kind, (build, publish, _) in WINDOWS.items():
        if title.startswith(f"daily-reel {kind} ") or build in title or publish in title:
            return kind
    return None


def _created(run):
    raw = str(run.get("created_at") or run.get("createdAt") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(IST)
    except ValueError:
        return None


def ran_today(runs, kind, now):
    for run in runs or []:
        stamp = _created(run)
        if stamp and stamp.date() == now.date() and run_kind(run) == kind:
            return True
    return False


def ledger_has(ledger, kind, now):
    """A ledger row today inside the build..late window counts as posted."""
    build = _at(now, WINDOWS[kind][2]) - timedelta(minutes=30)
    late = _at(now, WINDOWS[kind][2]) + timedelta(minutes=LATE_MIN)
    for row in ledger or []:
        if str(row.get("date_ist")) != now.date().isoformat() or not row.get("video_id"):
            continue
        try:
            at = _at(now, str(row.get("slot")))
        except (ValueError, TypeError):
            continue
        if build <= at <= late:
            return True
    return False


def due(now, runs, ledger):
    """Kinds to dispatch now."""
    if now.weekday() >= 5:
        return []
    out = []
    for kind, (_, _, hhmm) in WINDOWS.items():
        publish = _at(now, hhmm)
        if not (publish + timedelta(minutes=GRACE_MIN) < now <= publish + timedelta(minutes=LATE_MIN)):
            continue
        if ran_today(runs, kind, now) or ledger_has(ledger, kind, now):
            continue
        out.append(kind)
    return out


def _api(path, method="GET", body=None):
    repo = os.environ["GITHUB_REPOSITORY"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def main(now=None):
    now = now or datetime.now(IST)
    since = _at(now, "00:00").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = _api(f"/actions/workflows/daily-reels.yml/runs?per_page=100&created=%3E%3D{since}").get("workflow_runs") or []
    try:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = []
    kinds = due(now, runs, ledger)
    if not kinds:
        print(f"[catchup] {now:%Y-%m-%d %H:%M} IST nothing missing")
        return 0
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    for kind in kinds:
        print(f"[catchup] {kind}: no run today and window passed; dispatching phase=both live")
        _api("/actions/workflows/daily-reels.yml/dispatches", "POST", {
            "ref": ref,
            "inputs": {"kind": kind, "phase": "both", "dry_run": "false"},
        })
    return 0


if __name__ == "__main__":
    sys.exit(main())
