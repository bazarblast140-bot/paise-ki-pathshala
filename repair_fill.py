"""One-shot restore for Saturday 2026-10-03 IST.

The cloud-agent token cannot enable or dispatch Actions (it lacks
actions=write). The repair workflow runs this with the job token:

  * enable Paise Ki Pathshala, Slot watch, and Buffer refill
  * seed Edge buffer Shorts by dispatching Buffer refill (no ElevenLabs)
  * fill an open 16:00 or 21:00 slot with privacy=public (private + publishAt)

The slot dispatch is private + publishAt (privacy=public). An instant-public
upload is not requested. A withdrawn video is not republished. Trader Quiz
Shorts stays disabled. After 2026-10-03 IST the command no-ops.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import slot_guard

TARGET_DATE = "2026-10-03"
TARGET_SLOTS = ("16:00", "21:00")
ENABLE = ("Paise Ki Pathshala", "Slot watch", "Buffer refill")
BUFFER_WORKFLOW = "Buffer refill"
BUFFER_FILE = "buffer-refill.yml"
SLOT_WORKFLOW = "Paise Ki Pathshala"
SLOT_FILE = "paise-ki-pathshala.yml"
MAX_BUFFER_BUILDS = 8
TARGET_READY = 2
SLOT_TRIES = 2
# Start the public slot before another buffer build once the lead is this short.
# The picker itself still requires MIN_LEAD (2h) and will not start a slot inside that lead.
SLOT_BEFORE_BUFFER = timedelta(hours=3, minutes=30)


def on_repair_day(now):
    return now.astimezone(slot_guard.IST).strftime("%Y-%m-%d") == TARGET_DATE


def target_state(entries, slot, date=TARGET_DATE):
    return slot_guard.row_state(slot_guard.find_entry(entries, date, slot))


def open_targets(entries, date=TARGET_DATE):
    return [slot for slot in TARGET_SLOTS if target_state(entries, slot, date) == "open"]


def next_public_slot(now, entries):
    """Earliest slot privacy=public would build, if it is a 3 Oct target.

    Holds and filled rows are skipped by the picker. A slot inside the 2h
    lead is left for Slot watch's last-chance buffer path, not published now.
    """
    choice = slot_guard.next_unfilled(now, entries)
    if not choice:
        return None
    if choice["date_ist"] != TARGET_DATE or choice["slot"] not in TARGET_SLOTS:
        return None
    return choice


def count_ready_manifest(data):
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0
    return sum(1 for item in items if isinstance(item, dict) and item.get("state") == "ready")


def lead_is_short(now, choice):
    if not choice:
        return False
    when = slot_guard._at(choice["date_ist"], choice["slot"])
    return when - now <= SLOT_BEFORE_BUFFER


def next_action(ready, builds, lead_short, slot_tries):
    """buffer, slot, or stop.

    slot_tries is None when no 3 Oct target is far enough ahead.
    A comfortable lead keeps building Edge Shorts until one is ready,
    then the public slot can fall back to that Short if the gated
    narration fails. A failed slot is retried up to SLOT_TRIES. Further
    Edge builds stay ahead of that retry while nothing is ready.
    """
    need_buffer = ready < TARGET_READY and builds < MAX_BUFFER_BUILDS
    # First try of an open slot is immediate. Passing None used to look like
    # "no slot" and the restore stopped after the Edge budget without posting.
    if slot_tries == 0:
        return "slot"
    if ready < 1 and need_buffer and not lead_short:
        return "buffer"
    if slot_tries is not None and slot_tries < SLOT_TRIES:
        return "slot"
    if need_buffer:
        return "buffer"
    return "stop"


def _run(args, check=True):
    print("+ " + " ".join(args), flush=True)
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if check and result.returncode != 0:
        if result.stdout:
            print(result.stdout, flush=True)
        if result.stderr:
            print(result.stderr, flush=True)
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result


def _out(args):
    result = _run(args)
    return result.stdout


def names_needing_enable(states):
    """Workflows that are not already active.

    `gh workflow enable` only searches disabled workflows. Calling it again
    after a successful enable exits with "could not find any workflows named …"
    and used to abort the restore before any slot was dispatched.
    """
    return [name for name in ENABLE if states.get(name) != "active"]


def enable_required():
    listing = json.loads(_out(["gh", "workflow", "list", "--all", "--json", "name,state"]))
    states = {row.get("name"): row.get("state") for row in listing}
    for name in names_needing_enable(states):
        _run(["gh", "workflow", "enable", name])
    listing = json.loads(_out(["gh", "workflow", "list", "--all", "--json", "name,state"]))
    states = {row.get("name"): row.get("state") for row in listing}
    bad = names_needing_enable(states)
    if bad:
        print(f"[repair] still not active: {bad} states={ {n: states.get(n) for n in ENABLE} }", flush=True)
        return False
    for name in ENABLE:
        print(f"[repair] workflow active: {name}", flush=True)
    # Leave these alone. Print their states so the log shows we did not enable them.
    for name in ("Trader Quiz Shorts", "Render sample (no upload)", "Autonomous Repair Watchdog"):
        print(f"[repair] left {name}: {states.get(name)}", flush=True)
    return True


def _parse_created(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def dispatch_and_find(workflow, workflow_file, fields, not_before):
    cmd = ["gh", "workflow", "run", workflow, "--ref", "main"]
    for field in fields:
        cmd.extend(["-f", field])
    _run(cmd)
    deadline = time.time() + 180
    while time.time() < deadline:
        rows = json.loads(_out([
            "gh", "run", "list",
            "--workflow", workflow_file,
            "--limit", "8",
            "--json", "databaseId,createdAt,event,status,url,headBranch",
        ]))
        fresh = []
        for row in rows:
            if row.get("event") != "workflow_dispatch" or row.get("headBranch") != "main":
                continue
            if _parse_created(row["createdAt"]) < not_before:
                continue
            fresh.append(row)
        if fresh:
            fresh.sort(key=lambda row: row["createdAt"])
            return fresh[-1]
        time.sleep(5)
    raise RuntimeError(f"dispatch of {workflow} did not appear")


def watch(run):
    result = _run(["gh", "run", "watch", str(run["databaseId"]), "--exit-status"], check=False)
    print(result.stdout[-4000:], flush=True)
    if result.stderr:
        print(result.stderr[-2000:], flush=True)
    print(f"[repair] run {run.get('url')} exit={result.returncode}", flush=True)
    return result.returncode


def _show(path):
    last = None
    for attempt in range(5):
        try:
            _run(["git", "fetch", "origin", "main"])
            return _out(["git", "show", f"origin/main:{path}"])
        except subprocess.CalledProcessError as exc:
            last = exc
            wait = 3 * (attempt + 1)
            print(f"[repair] git fetch failed ({exc.returncode}); retry in {wait}s", flush=True)
            time.sleep(wait)
    raise last


def remote_entries():
    return json.loads(_show("data/published.json"))


def remote_ready():
    return count_ready_manifest(json.loads(_show("data/buffer_manifest.json")))


def active_runs(workflow_file):
    rows = json.loads(_out([
        "gh", "run", "list",
        "--workflow", workflow_file,
        "--limit", "8",
        "--json", "databaseId,status,url,event,createdAt,headBranch",
    ]))
    active = []
    for row in rows:
        if row.get("headBranch") not in ("main", None, ""):
            continue
        if row.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
            active.append(row)
    active.sort(key=lambda row: row.get("createdAt") or "")
    return active


def restore():
    now = datetime.now(slot_guard.IST)
    if not on_repair_day(now):
        print(f"[repair] noop, IST date is {now.strftime('%Y-%m-%d')}", flush=True)
        return 0
    if not enable_required():
        return 1
    urls = []
    builds = 0
    attempted = {}
    while True:
        now = datetime.now(slot_guard.IST)
        if not on_repair_day(now):
            print("[repair] IST date rolled past 2026-10-03; stopping", flush=True)
            break
        entries = remote_entries()
        ready = remote_ready()
        choice = next_public_slot(now, entries)
        key = (choice["date_ist"], choice["slot"]) if choice else None
        short = lead_is_short(now, choice)
        tries = None if key is None else attempted.get(key, 0)
        action = next_action(ready, builds, short, tries)
        print(
            f"[repair] ready={ready} builds={builds} next={key} "
            f"open={open_targets(entries)} action={action}",
            flush=True,
        )
        if action == "buffer":
            not_before = datetime.now(timezone.utc) - timedelta(seconds=20)
            run = dispatch_and_find(BUFFER_WORKFLOW, BUFFER_FILE, [], not_before)
            urls.append(run.get("url"))
            builds += 1
            watch(run)
            continue
        if action == "slot":
            active = active_runs(SLOT_FILE)
            if active:
                print(f"[repair] waiting for in-progress slot run {active[0].get('url')}", flush=True)
                watch(active[0])
                continue
            not_before = datetime.now(timezone.utc) - timedelta(seconds=20)
            run = dispatch_and_find(
                SLOT_WORKFLOW, SLOT_FILE,
                ["format=short", "privacy=public", "beats=four"],
                not_before,
            )
            urls.append(run.get("url"))
            attempted[key] = (attempted.get(key) or 0) + 1
            watch(run)
            continue
        break
    entries = remote_entries()
    ready = remote_ready()
    print("[repair] workflow run URLs:", flush=True)
    for url in urls:
        print(url, flush=True)
    print(
        f"[repair] done ready={ready} open={open_targets(entries)} "
        f"states={ {slot: target_state(entries, slot) for slot in TARGET_SLOTS} }",
        flush=True,
    )
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--restore"]:
        return restore()
    print("usage: repair_fill.py --restore", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
