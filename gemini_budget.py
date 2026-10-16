"""Shared daily Gemini TTS counter.

Each API key has its own free-tier cap of 10 requests a day. The file
``data/gemini_budget.json`` is keyed by the IST date and then by key
slot (``1`` is ``GEMINI_API_KEY``, then ``GEMINI_API_KEY_2`` through
``GEMINI_API_KEY_4``). The secret itself is never stored. A cache hit
does not count. Weekdays hold three requests on each key for the news, close, and TA
Reels. Saturday holds one for the weekly recap. Topic Shorts and the
morning buffer may spend a request only when that hold is already
covered. The buffer is also capped at three new topics a day.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
LIMIT = 10
BUFFER_CAP = 3
# Reels are spent first. These callers may use the held requests.
REEL_CALLERS = ("realchart", "news", "close", "ta", "recap")
CALLERS = ("realchart", "nifty", "buffer", "other", "news", "close", "ta", "recap")
# Slot id, then the env var. Later slots are used only when present.
# Slots 1-9: GEMINI_API_KEY, GEMINI_API_KEY_2 ... _9 (env, or gemini_api_key_N in GEMINI_SECRETS_DIR).
KEY_ENV = (("1", "GEMINI_API_KEY"),) + tuple((str(n), f"GEMINI_API_KEY_{n}") for n in range(2, 10))
KEY_IDS = tuple(slot for slot, _name in KEY_ENV)
ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "data" / "gemini_budget.json"


def ist_day(now=None):
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    return now.astimezone(IST).strftime("%Y-%m-%d")


def path():
    raw = os.environ.get("GEMINI_BUDGET_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_PATH


def enforced():
    """Production runs check the counter. The unit suite does not, unless a test sets the path."""
    flag = os.environ.get("GEMINI_BUDGET", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if os.environ.get("GEMINI_BUDGET_PATH", "").strip():
        return True
    if "unittest" in sys.modules and os.environ.get("GEMINI_BUDGET_FORCE", "").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        return False
    return True


def caller_from_env():
    """Which daily bucket this Charon call belongs to."""
    explicit = os.environ.get("GEMINI_BUDGET_CALLER", "").strip().lower()
    if explicit in CALLERS:
        return explicit
    kind = os.environ.get("REALCHART_KIND", "").strip().lower()
    if kind in ("news", "close", "ta", "recap"):
        return kind
    if os.environ.get("REALCHART_REEL", "").strip().lower() in ("1", "true", "yes", "on"):
        return "realchart"
    topic = os.environ.get("TOPIC_KEY", "").strip()
    data_short = os.environ.get("DATA_SHORT", "").strip().lower() in ("1", "true", "yes", "on")
    if data_short or topic == "aaj_ka_nifty":
        return "nifty"
    bank = os.environ.get("APPROVED_BANK", "").strip().lower() in ("1", "true", "yes", "on")
    upload = os.environ.get("AUTOMATIC_UPLOAD", "").strip().lower()
    if bank and upload in ("0", "false", "no", "off"):
        return "buffer"
    return "other"


def active_key_ids():
    """Key slots that are configured. The secret values are not returned.

    The same secret in two env vars counts once, under the earlier slot.
    With no key set, slot ``1`` is still the planning bucket so the daily
    cap stays in force.
    """
    seen = set()
    ids = []
    for slot, name in KEY_ENV:
        secret = os.environ.get(name, "").strip()
        if not secret or secret in seen:
            continue
        seen.add(secret)
        ids.append(slot)
    return ids or ["1"]


def _empty():
    return {"days": {}, "charges": []}


def load():
    file = path()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("days", {})
    data.setdefault("charges", [])
    return data


def save(data):
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _keys(data, day):
    """Per-key caller counts. A flat day dict from before rotation is key 1."""
    raw = data.setdefault("days", {}).setdefault(day, {})
    if not isinstance(raw, dict):
        raw = {}
        data["days"][day] = raw
    if "keys" not in raw:
        flat = {name: int(raw.pop(name) or 0) for name in CALLERS if name in raw}
        raw.pop("exhausted", None)
        raw["keys"] = {}
        if any(flat.values()):
            raw["keys"]["1"] = flat
    if not isinstance(raw.get("keys"), dict):
        raw["keys"] = {}
    return raw["keys"]


def _bucket(data, day, key_id):
    keys = _keys(data, day)
    bucket = keys.setdefault(str(key_id), {})
    for name in CALLERS:
        bucket[name] = int(bucket.get(name) or 0)
    bucket["exhausted"] = bool(bucket.get("exhausted"))
    return bucket


def used(bucket):
    return sum(int(bucket.get(name) or 0) for name in CALLERS)


def reel_need(now=None):
    """How many Charon requests today's Reels still need on each key.

    Monday–Friday is the news, close, and TA Reels. Saturday is the
    weekly recap. Sunday has none.
    """
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    weekday = now.astimezone(IST).weekday()
    if weekday == 6:
        return 0
    if weekday == 5:
        return 1
    return 3


def owed(bucket, caller, now=None):
    """Requests this key still holds for Reels that have not run yet."""
    if caller in REEL_CALLERS:
        return 0
    spent = sum(int(bucket.get(name) or 0) for name in REEL_CALLERS)
    return max(0, reel_need(now) - spent)


def _buffer_total(data, day):
    total = 0
    for bucket in _keys(data, day).values():
        if isinstance(bucket, dict):
            total += int(bucket.get("buffer") or 0)
    return total


def allow(caller, key_id="1", now=None):
    """True when this caller may send one more POST on this key today."""
    if not enforced():
        return True
    caller = caller if caller in CALLERS else "other"
    key_id = str(key_id or "1")
    data = load()
    day = ist_day(now)
    if caller == "buffer" and _buffer_total(data, day) >= BUFFER_CAP:
        return False
    bucket = _bucket(data, day, key_id)
    if bucket.get("exhausted"):
        return False
    remaining = LIMIT - used(bucket)
    return remaining - owed(bucket, caller, now) >= 1


def topic_short_allowed(now=None):
    """True when an 08:30 or 21:00 topic Short can spend one request."""
    if not enforced():
        return True
    return any(allow("other", key_id, now) for key_id in active_key_ids())


def buffer_topic_limit(requested, now=None):
    """How many new buffer topics may start today.

    Never above three across every configured key, and never into a key's reserves.
    """
    requested = max(0, int(requested))
    if not enforced():
        return requested
    data = load()
    day = ist_day(now)
    room = max(0, BUFFER_CAP - _buffer_total(data, day))
    spendable = 0
    for key_id in active_key_ids():
        bucket = _bucket(data, day, key_id)
        if bucket.get("exhausted"):
            continue
        spendable += max(0, LIMIT - used(bucket) - owed(bucket, "buffer", now))
    return min(requested, room, spendable)


def note(caller, key_id="1", now=None):
    """Record one real POST on one key. A cache hit must not call this."""
    if not enforced():
        return None
    caller = caller if caller in CALLERS else "other"
    key_id = str(key_id or "1")
    day = ist_day(now)
    data = load()
    bucket = _bucket(data, day, key_id)
    bucket[caller] = int(bucket.get(caller) or 0) + 1
    charge = {
        "id": uuid.uuid4().hex,
        "date": day,
        "caller": caller,
        "key": key_id,
    }
    charges = list(data.get("charges") or [])
    charges.append(charge)
    # Keep two weeks of ids so a later merge of the same artifact does not double-count.
    cutoff = (datetime.now(IST) - timedelta(days=14)).strftime("%Y-%m-%d")
    data["charges"] = [row for row in charges if str(row.get("date") or "") >= cutoff]
    save(data)
    _append_run_charge(charge)
    return charge


def exhaust(key_id, now=None):
    """The server returned 429 for this key. Do not POST it again today."""
    if not enforced():
        return
    data = load()
    bucket = _bucket(data, ist_day(now), str(key_id or "1"))
    bucket["exhausted"] = True
    save(data)


def _append_run_charge(charge):
    raw = os.environ.get("GEMINI_CHARGE_PATH", "").strip()
    if raw:
        file = Path(raw)
    else:
        run = os.environ.get("REALCHART_RUN", "").strip()
        file = Path(run) / "gemini_charges.json" if run else ROOT / "run" / "gemini_charges.json"
    try:
        rows = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    if not isinstance(rows, list):
        rows = []
    rows.append(charge)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def merge_charges(records, now=None):
    """Add charge ids that are not already in the file. Counts move with the ids."""
    data = load()
    known = {str(row.get("id")) for row in data.get("charges") or [] if isinstance(row, dict)}
    added = 0
    for row in records or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        caller = str(row.get("caller") or "other")
        day = str(row.get("date") or ist_day(now))
        key_id = str(row.get("key") or "1")
        if not cid or cid in known or caller not in CALLERS or key_id not in KEY_IDS:
            continue
        known.add(cid)
        data.setdefault("charges", []).append({
            "id": cid, "date": day, "caller": caller, "key": key_id,
        })
        bucket = _bucket(data, day, key_id)
        bucket[caller] = int(bucket.get(caller) or 0) + 1
        added += 1
    if added:
        save(data)
    return added
