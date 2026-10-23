"""Gated buffer of Hammer v2 topic videos.

A topic becomes ready on its own when the render passes every hard gate:
code-verified numbers, sync, duration, RR at least 1.5 with the stop on the
correct side, voice gemini/Charon, and no crash. The checked-in
approved/*.json hand flag is not required before that pass, and it is not
required again when a slot publishes the ready item. An Edge take, a missing
Charon take, or a failed sync is held and never published. 08:30 and 21:00
publish the oldest ready item. 16:00 stays Aaj ka Nifty.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "approved_buffer.json"
RUN = ROOT / "run"
RELEASE_TAG = "approved-topic-buffer"
MAX_PER_RUN = 6
REPEAT_DAYS = 14
REVIEW_MIN = 75.0
PART_MIN = 60.0
TOPIC_SLOTS = ("08:30", "21:00")
# elevenlabs is the Rudra voice record. Edge is held.
ACCEPTED_ENGINES = {"gemini", "rudra", "elevenlabs"}
PARTS = ("hook", "visual", "accuracy", "trend", "craft", "sync")
IST = timezone(timedelta(hours=5, minutes=30))


def _now():
    return datetime.now(timezone.utc)


def _iso(when=None):
    when = when or _now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ist(now):
    when = now or _now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return when.astimezone(IST)


def _ist_day(iso):
    text = str(iso or "").strip()
    if not text:
        return ""
    text = text.replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return text[:10]
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(IST).strftime("%Y-%m-%d")


def load_manifest(path=None):
    file = Path(path) if path else MANIFEST
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    data["items"] = [item for item in items if isinstance(item, dict)]
    data["tag"] = RELEASE_TAG
    return data


def save_manifest(manifest, path=None):
    file = Path(path) if path else MANIFEST
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tag": RELEASE_TAG, "items": list(manifest.get("items") or [])}
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def published_within(entries, topic_key, now, days=REPEAT_DAYS):
    """True when this topic already has a video in the last `days` days."""
    key = str(topic_key or "").strip()
    if not key:
        return False
    today = _ist(now).date()
    cutoff = today - timedelta(days=int(days))
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("topic_key") or "") != key:
            continue
        if not str(entry.get("video_id") or "").strip():
            continue
        text = str(entry.get("date_ist") or "")[:10]
        try:
            when = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            continue
        if when >= cutoff:
            return True
    return False


def _ready(manifest, topic_key):
    key = str(topic_key or "").strip()
    for item in manifest.get("items") or []:
        if item.get("topic_key") == key and item.get("state") == "ready":
            return True
    return False


def _held_today(manifest, topic_key, now):
    key = str(topic_key or "").strip()
    today = _ist(now).strftime("%Y-%m-%d")
    for item in manifest.get("items") or []:
        if item.get("topic_key") != key or item.get("state") != "held":
            continue
        if _ist_day(item.get("created_at")) == today:
            return True
    return False


def _last_held(manifest, topic_key):
    """created_at of this topic's newest held row, or "" when it was never held."""
    key = str(topic_key or "").strip()
    latest = ""
    for item in manifest.get("items") or []:
        if item.get("topic_key") != key or item.get("state") != "held":
            continue
        stamp = str(item.get("created_at") or "")
        if stamp > latest:
            latest = stamp
    return latest


def candidate_keys(entries, manifest, now, limit=MAX_PER_RUN):
    """Approved topics that are not ready and were not published in 14 days.

    Never-held topics go first, then the topic held longest ago. Bank order
    alone retried the same first three held topics every day, so the daily
    Charon cap never reached the rest of the bank.
    """
    import approved_bank
    import trade_plan

    found = []
    for script in approved_bank.all_scripts():
        key = str(script.get("key") or "").strip()
        if not key or key == "aaj_ka_nifty" or not trade_plan.has_plan(key):
            continue
        if key in found:
            continue
        if _ready(manifest, key) or _held_today(manifest, key, now):
            continue
        if published_within(entries, key, now):
            continue
        found.append(key)
    # Stable sort: bank order is kept inside each group.
    found.sort(key=lambda key: _last_held(manifest, key))
    return found[:max(0, int(limit))]


def plan_gate_reasons(key):
    """RR at least 1.5, and the stop on the far side of the focus candle."""
    import trade_plan

    try:
        plan = trade_plan.plan_for(key)
    except KeyError:
        return ["trade plan missing"]
    reasons = []
    if plan.rr < 1.5 - 1e-9:
        reasons.append(f"RR {plan.rr:.2f} is under 1.5")
    candle = plan.ohlc[plan.focus]
    high, low = float(candle[1]), float(candle[2])
    if plan.side == "long" and not plan.sl < low - 1e-9:
        reasons.append("SL is not below the focus low")
    if plan.side == "short" and not plan.sl > high + 1e-9:
        reasons.append("SL is not above the focus high")
    return reasons


def _voice(run_dir):
    meta = _load_json(Path(run_dir) / "voice_meta.json") or {}
    return str(meta.get("engine") or "").strip().lower()


# Soft craft and a missing trend are review notes on an approved-topic item.
# Numbers, sync, duration, leaks, safe area, a crash, and Edge stay holds.
_SOFT_PARTS = {"hook", "visual", "craft", "trend"}
_HARD_REASON_BITS = (
    "sync", "leak", "safe area", "duration", "narration blocked",
    "narration missing", "spoken number", "math", "voice_engine=",
    "gemini_quota", "before it is said", "accuracy", "crash",
    "rr ", "sl is not", "disclaimer", "banned", "placeholder",
    "forbidden", "hindi", "title 8-90", "beat '", "trade plan",
    "hard gate", "render rc=", "store failed", "gemini_take",
)


def _review_note(reason):
    """True when this line must not hold an approved-topic buffer item."""
    text = " ".join(str(reason or "").lower().split())
    if not text:
        return False
    if any(bit in text for bit in _HARD_REASON_BITS):
        return False
    soft_bits = (
        "trend", "craft", "hook", "visual", "public-review",
        "unverified", "fact ledger", "compliance", "report.md",
        "verify_meta", "public_ready", "self-review overall",
        "existing public-review",
    )
    return any(bit in text for bit in soft_bits)


def public_review_details(run_dir):
    """The line the public-review gate collapsed into one sentence."""
    import review_gate

    run_dir = Path(run_dir)
    details = []
    report = run_dir / "report.md"
    meta = _load_json(run_dir / "verify_meta.json") or {}
    if not report.exists():
        details.append("public-review: report.md missing")
    else:
        text = report.read_text(encoding="utf-8")
        verdict = review_gate.parse_report_verdict(text)
        if verdict != "READY":
            details.append(f"public-review: report verdict {verdict or 'missing'}")
        for line in text.splitlines():
            low = line.lower()
            if any(bit in low for bit in ("unverified", "fact ledger", "compliance", "needs fix")):
                details.append("public-review: " + " ".join(line.split())[:180])
                break
    if not meta:
        details.append("public-review: verify_meta missing")
    else:
        if meta.get("verdict") not in (None, "READY"):
            details.append(f"public-review: verify_meta verdict {meta.get('verdict')}")
        if not meta.get("public_ready"):
            details.append("public-review: verify_meta public_ready is false")
    seen = []
    for item in details:
        if item not in seen:
            seen.append(item)
    return seen or ["public-review: no sub-reason"]


def judge(run_dir, key):
    """Hard reasons this render must be held. Empty means it may be stored.

    Review notes (missing trend, soft craft, a fact-ledger field the
    compliance pass invented) are kept on `judge.last_notes` and logged.
    They do not hold the item. Sync, numbers, duration, and Edge still do.
    """
    import publish_gates
    import trade_plan

    run_dir = Path(run_dir)
    reasons = []
    notes = []
    try:
        verdict = publish_gates.evaluate(run_dir)
    except Exception as exc:  # noqa: BLE001 — a broken run is held, not published
        verdict = {"passed": False, "reasons": [f"hard gate crashed: {type(exc).__name__}"]}
    raw = list(verdict.get("reasons") or []) if not verdict.get("passed") else []
    if not verdict.get("passed") and not raw:
        raw = ["hard gate failed"]
    details = []
    if any(item == "existing public-review gate did not pass" for item in raw):
        details = public_review_details(run_dir)
        for detail in details:
            print(f"[public-review] {key}: {detail}", flush=True)
    for item in raw:
        if item == "existing public-review gate did not pass":
            for detail in details:
                if _review_note(detail):
                    notes.append(detail)
                else:
                    reasons.append(detail)
            continue
        if _review_note(item):
            notes.append(item)
        else:
            reasons.append(item)
    reasons.extend(plan_gate_reasons(key))
    narration = _load_json(run_dir / "narration.json")
    if not isinstance(narration, dict):
        reasons.append("narration missing")
    else:
        body = dict(narration)
        body.setdefault("key", key)
        reasons.extend(trade_plan.script_fails(body, {"key": key}))
    review = _load_json(run_dir / "self_review.json") or {}
    try:
        overall = float(review.get("overall"))
    except (TypeError, ValueError):
        overall = None
    parts = review.get("parts") if isinstance(review.get("parts"), dict) else {}
    for name in PARTS:
        if name not in parts:
            line = f"{name} score missing"
            if name == "trend":
                notes.append(line)
            else:
                reasons.append(line)
            continue
        raw_part = parts.get(name)
        # Trend is None on an evergreen lesson. That skip is not a miss.
        if raw_part is None:
            if name == "trend":
                notes.append("trend skipped")
            continue
        try:
            score = float(raw_part)
        except (TypeError, ValueError):
            line = f"{name} score missing"
            if name == "trend":
                notes.append(line)
            else:
                reasons.append(line)
            continue
        if score < PART_MIN:
            line = f"{name} {score:.0f} is below {PART_MIN:.0f}"
            if name in _SOFT_PARTS:
                notes.append(line)
            else:
                reasons.append(line)
    if overall is None or overall < REVIEW_MIN:
        line = f"self-review {overall} is under {REVIEW_MIN:.0f}"
        hard_part = any(
            "sync" in item or "accuracy" in item for item in reasons
        )
        if hard_part:
            reasons.append(line)
        else:
            notes.append(line)
    sync = review.get("sync") if isinstance(review.get("sync"), dict) else None
    if sync is None or sync.get("ok") is not True:
        if not any("sync" in item for item in reasons):
            reasons.append("sync gate did not pass")
    seconds = publish_gates._duration_s(run_dir)
    if not any(item.startswith("duration") for item in reasons):
        if seconds is None or trade_plan.duration_blocks(seconds):
            shown = "missing" if seconds is None else f"{float(seconds):.1f}s"
            reasons.append(f"duration {shown} is outside 40-55s")
    engine = _voice(run_dir)
    if engine not in ACCEPTED_ENGINES:
        reasons.append(f"voice_engine={engine or 'missing'} is held")
    if (run_dir / "gemini_quota.txt").exists():
        reasons.append("gemini_quota")
    if (run_dir / "gemini_take.txt").exists():
        reasons.append("gemini_take")
    seen = []
    for item in reasons:
        if item not in seen:
            seen.append(item)
    seen_notes = []
    for item in notes:
        if item not in seen_notes and item not in seen:
            seen_notes.append(item)
    judge.last_notes = seen_notes
    for note in seen_notes:
        print(f"[REVIEW] {key}: {note}", flush=True)
    return seen


judge.last_notes = []


def _title_for(run_dir, key):
    script = ""
    path = Path(run_dir) / "script.txt"
    if path.exists():
        script = path.read_text(encoding="utf-8")
    title = key
    description = ""
    tags = ["stock market hindi", "trading hindi", "paise ki pathshala"]
    try:
        import publish
        sections = publish.sections(script) if script else {}
        if sections.get("TITLE"):
            title = publish.build_title(sections)
        description = publish.build_description(sections, publish.cfg()) if sections else key
        tags = publish.build_tags(title, publish.cfg())
    except Exception:  # noqa: BLE001 — the buffer item still has a title
        description = key
    return title, description, tags, script


def _put(store, name, data):
    try:
        store.put(name, data, "video/mp4")
    except TypeError:
        store.put(name, data)


def mark_owner_approved(key, directory=None):
    """Flip the file flag only after this video passed. The workflow commits it."""
    import approved_bank

    directory = Path(directory) if directory else approved_bank.DIR
    path = directory / f"{key}.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    data["owner_approved"] = True
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _hold(manifest, key, reasons, now, engine, seconds):
    text = "; ".join(reasons)
    for item in manifest.get("items") or []:
        if item.get("topic_key") == key and item.get("state") == "held":
            item["reason"] = text
            item["created_at"] = _iso(now)
            item["voice_engine"] = engine
            item["duration_s"] = seconds
            item["owner_approved"] = False
            return item
    item = {
        "id": f"hold-{key}-{_iso(now).replace(':', '').replace('-', '')}",
        "state": "held",
        "topic_key": key,
        "owner_approved": False,
        "created_at": _iso(now),
        "reason": text,
        "voice_engine": engine,
        "duration_s": seconds,
    }
    manifest.setdefault("items", []).append(item)
    return item


def _store_ready(manifest, run_dir, key, store, now):
    import buffer_shorts

    run_dir = Path(run_dir)
    mp4_path = run_dir / "final.mp4"
    if not mp4_path.exists():
        raise RuntimeError("final.mp4 missing")
    payload = mp4_path.read_bytes()
    digest = buffer_shorts.sha256_bytes(payload)
    item_id = f"ap-{key}-{digest[:12]}"
    if any(row.get("id") == item_id or row.get("sha256") == digest for row in manifest.get("items") or []):
        raise buffer_shorts.AlreadyUsed(f"{item_id} is already in the buffer")
    title, description, tags, script = _title_for(run_dir, key)
    review = _load_json(run_dir / "self_review.json") or {}
    meta = _load_json(run_dir / "verify_meta.json") or {}
    report = ""
    report_path = run_dir / "report.md"
    if report_path.exists():
        report = report_path.read_text(encoding="utf-8")
    asset = f"{item_id}.mp4"
    _put(store, asset, payload)
    try:
        overall = float(review.get("overall"))
    except (TypeError, ValueError):
        overall = None
    item = {
        "id": item_id,
        "state": "ready",
        "topic_key": key,
        "owner_approved": True,
        "title": title,
        "description": description,
        "tags": list(tags or []),
        "sha256": digest,
        "asset_name": asset,
        "created_at": _iso(now),
        "used_at": None,
        "used_slot": None,
        "used_date_ist": None,
        "video_id": None,
        "voice_engine": _voice(run_dir),
        "duration_s": __import__("publish_gates")._duration_s(run_dir),
        "review_overall": overall,
        "reason": "",
        "files": {
            "topic": {"key": key},
            "script": script,
            "report": report or "VERDICT: [READY TO PUBLISH]\n",
            "verify_meta": meta,
            "self_review": review,
        },
    }
    manifest.setdefault("items", []).append(item)
    return item


def default_runner(key):
    """One Hammer v2 render. Gemini when the key is set. No upload."""
    import subprocess

    env = os.environ.copy()
    env["VIDEO_FORMAT"] = "short"
    env["APPROVED_BANK"] = "1"
    env["TOPIC_KEY"] = key
    env["LIVE_BEATS"] = "plan"
    env["PUBLISH_VISIBILITY"] = "public"
    env["AUTOMATIC_UPLOAD"] = "0"
    env.pop("PROOF_FREE_AUDIO", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "run_pipeline.py"), "--format", "short"],
        cwd=ROOT,
        env=env,
    ).returncode


def _export_item(item):
    """One topic's buffer row, for a matrix job to hand to the commit step."""
    if not item:
        return
    path = RUN / "buffer_item.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _upsert(manifest, item):
    items = manifest.setdefault("items", [])
    for index, row in enumerate(items):
        same_id = item.get("id") and row.get("id") == item.get("id")
        same_hold = (
            row.get("topic_key") == item.get("topic_key")
            and row.get("state") == "held"
            and item.get("state") == "held"
        )
        if same_id or same_hold:
            items[index] = item
            return
    items.append(item)


def merge_parts(directory, manifest_path=None, approved_dir=None):
    """Fold per-topic artifacts into the manifest. A passing JSON is copied."""
    directory = Path(directory)
    manifest_path = Path(manifest_path) if manifest_path else MANIFEST
    manifest = load_manifest(manifest_path)
    if approved_dir is None:
        import approved_bank
        approved_dir = approved_bank.DIR
    approved_dir = Path(approved_dir)
    for item_path in sorted(directory.glob("**/buffer_item.json")):
        try:
            item = json.loads(item_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(item, dict) or not item.get("topic_key"):
            continue
        _upsert(manifest, item)
        key = str(item.get("topic_key"))
        src = item_path.parent / f"{key}.json"
        if not src.exists():
            continue
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("owner_approved") is True:
            dest = approved_dir / f"{key}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    save_manifest(manifest, manifest_path)
    return manifest


def build(*, max_n=MAX_PER_RUN, runner=None, store=None, now=None, manifest_path=None, only=None):
    """Render up to `max_n` topics. Passes are stored. Failures are held.

    `only` renders that one key. The buffer workflow uses it as a matrix.
    """
    import slot_guard

    now = now or _now()
    manifest_path = Path(manifest_path) if manifest_path else MANIFEST
    manifest = load_manifest(manifest_path)
    keys = candidate_keys(slot_guard._read(), manifest, now, limit=max_n)
    if only:
        only = str(only).strip()
        keys = [only] if only in keys else []
    if not keys:
        print("[buffer] no approved topic left to render", flush=True)
        save_manifest(manifest, manifest_path)
        return 0
    import gemini_budget
    room = gemini_budget.buffer_topic_limit(len(keys), now)
    if room < len(keys):
        print(f"[buffer] daily Gemini cap keeps {room} of {len(keys)} new topics", flush=True)
        keys = keys[:room]
    if not keys:
        print("[buffer] daily Gemini cap reached; no new topic", flush=True)
        save_manifest(manifest, manifest_path)
        return 0
    runner = runner or default_runner
    for key in keys:
        print(f"[buffer] rendering {key}", flush=True)
        try:
            rc = runner(key)
        except Exception as exc:  # noqa: BLE001
            rc = 1
            print(f"[HOLD] {key}: runner {type(exc).__name__}: {exc}", flush=True)
        reasons = judge(RUN, key)
        if rc not in (0, None) and not reasons:
            reasons = [f"render rc={rc}"]
        engine = _voice(RUN)
        seconds = __import__("publish_gates")._duration_s(RUN)
        if reasons:
            item = _hold(manifest, key, reasons, now, engine, seconds)
            save_manifest(manifest, manifest_path)
            _export_item(item)
            print(f"[HOLD] {key}: {'; '.join(reasons)}", flush=True)
            continue
        try:
            item = _store_ready(manifest, RUN, key, store or _github_store(), now)
        except Exception as exc:  # noqa: BLE001 — a video we cannot store is not ready
            item = _hold(manifest, key, [f"store failed: {exc}"], now, engine, seconds)
            save_manifest(manifest, manifest_path)
            _export_item(item)
            print(f"[HOLD] {key}: store failed: {exc}", flush=True)
            continue
        mark_owner_approved(key)
        save_manifest(manifest, manifest_path)
        _export_item(item)
        print(f"[READY] {key} {item['id']} voice={item.get('voice_engine')}", flush=True)
    return 0


def _github_store():
    import buffer_shorts

    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not repo or not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return buffer_shorts.GitHubReleaseStore(repo, token, tag=RELEASE_TAG)


def choose_item(manifest, entries, now, slot):
    """Oldest ready item that was not published in the last 14 days.

    Returns (item or None, reason). 16:00 is not a topic slot.
    """
    if slot not in TOPIC_SLOTS:
        return None, "not_topic_slot"
    # state=ready is the gate pass. The file flag in approved/ is written
    # afterwards for the ledger; a slot does not wait for that hand edit.
    ready = [
        item for item in (manifest.get("items") or [])
        if item.get("state") == "ready"
    ]
    ready.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    repeated = False
    for item in ready:
        if published_within(entries, item.get("topic_key"), now):
            repeated = True
            continue
        return item, "ready"
    if repeated:
        return None, "repeat"
    return None, "empty"


def _handled():
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("handled=true\n")


def publish_slot(*, now=None, manifest_path=None, store=None, uploader=None, crosspost=None, record=None):
    """Publish the oldest gated item on 08:30 or 21:00. An empty buffer skips."""
    import buffer_shorts
    import slot_guard

    slot = (os.environ.get("PUBLISH_SLOT_IST") or os.environ.get("BUFFER_SLOT") or "").strip()
    date_ist = (os.environ.get("PUBLISH_DATE_IST") or os.environ.get("BUFFER_DATE") or "").strip()
    now = now or datetime.now(slot_guard.IST)
    if slot == slot_guard.DATA_SLOT:
        print("[skip] 16:00 stays Aaj ka Nifty", flush=True)
        return 0
    if slot not in TOPIC_SLOTS or not date_ist:
        print(f"[skip] {slot or 'no slot'} is not an approved-topic slot", flush=True)
        return 0
    if os.environ.get("AUTOMATIC_UPLOAD", "1").strip().lower() in ("0", "false", "no", "off"):
        print("[skip] automatic upload is off", flush=True)
        return 0
    entries = slot_guard._read()
    state = slot_guard.row_state(slot_guard.find_entry(entries, date_ist, slot))
    if state in ("held", "filled"):
        print(f"[skip] {date_ist} {slot} is {state}", flush=True)
        _handled()
        return 0
    manifest_path = Path(manifest_path) if manifest_path else MANIFEST
    manifest = load_manifest(manifest_path)
    item, why = choose_item(manifest, entries, now, slot)
    if item is None:
        print(f"[skip] no gated buffer item for {date_ist} {slot} ({why})", flush=True)
        return 0
    if published_within(entries, item.get("topic_key"), now):
        print(f"[skip] {item.get('topic_key')} was published within {REPEAT_DAYS} days", flush=True)
        return 0
    publish_at = buffer_shorts.publish_at_for(now, date_ist, slot, last_chance=False)
    if not publish_at:
        publish_at = buffer_shorts.publish_at_for(now, date_ist, slot, last_chance=True)
    if not publish_at:
        print(f"[skip] {date_ist} {slot} is outside the publish window", flush=True)
        return 0
    store = store or _github_store()
    uploader = uploader or buffer_shorts._youtube_uploader
    if crosspost is None:
        crosspost = buffer_shorts.crosspost_buffer
    item_id = item["id"]
    try:
        buffer_shorts.claim_item(manifest, item_id, when=now)
    except buffer_shorts.AlreadyUsed as exc:
        print(f"[skip] {exc}", flush=True)
        return 0
    save_manifest(manifest, manifest_path)
    try:
        payload = store.get(item["asset_name"])
        if buffer_shorts.sha256_bytes(payload) != item.get("sha256"):
            raise RuntimeError(f"sha256 mismatch for {item_id}")
        import qa_hold
        held = qa_hold.hold_active()
        upload_at = None if held else publish_at
        video_id = uploader(
            payload,
            item.get("title") or "",
            item.get("description") or "",
            list(item.get("tags") or []),
            upload_at,
        )
        if not str(video_id or "").strip():
            raise RuntimeError("upload returned no video id")
    except Exception:
        manifest = load_manifest(manifest_path)
        buffer_shorts.release_claim(manifest, item_id)
        save_manifest(manifest, manifest_path)
        raise
    manifest = load_manifest(manifest_path)
    buffer_shorts.mark_used(
        manifest, item_id, slot=slot, date_ist=date_ist, video_id=video_id, when=now,
    )
    save_manifest(manifest, manifest_path)
    if record is None:
        import ledger
        record = ledger.record_publish
    record(
        video_id,
        None if held else publish_at,
        slot,
        platform="youtube",
        date_ist=date_ist,
        topic_key=item.get("topic_key"),
        source="approved-buffer",
        buffer_id=item_id,
        pending_qa=held,
    )
    if held:
        print(f"[qa] {item.get('topic_key')} uploaded private; no Reel", flush=True)
    else:
        try:
            crosspost(item, video_id, payload)
        except Exception as exc:  # noqa: BLE001 — YouTube already has the video
            print(f"[buffer] cross-post soft-fail: {type(exc).__name__}: {exc}", flush=True)
    print(f"[publish] {item.get('topic_key')} {video_id}", flush=True)
    # Only a real publish (or a held/filled slot) hands the slot over; an
    # empty or repeat buffer must let the live build and buffer fill run.
    _handled()
    return 0


def _key_near(path):
    path = Path(path)
    item = _load_json(path / "buffer_item.json") if path.is_dir() else None
    if isinstance(item, dict) and item.get("topic_key"):
        return str(item["topic_key"])
    topic = _load_json(path / "topic.json") if path.is_dir() else None
    if isinstance(topic, dict) and topic.get("key"):
        return str(topic["key"])
    narration = _load_json(path / "narration.json") if path.is_dir() else None
    if isinstance(narration, dict) and narration.get("key"):
        return str(narration["key"])
    return ""


def rescore(directory, manifest_path=None, now=None):
    """Re-run the gates on a downloaded run. No Gemini and no ElevenLabs.

    A folder with self_review.json is judged as a render. A folder that only
    has buffer_item.json is reclassified from the stored reason. A pass
    still needs final.mp4 before it can be stored as ready.
    """
    os.environ.pop("ELEVENLABS_API_KEY", None)
    for _name in (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
    ):
        os.environ.pop(_name, None)
    directory = Path(directory)
    now = now or _now()
    manifest_path = Path(manifest_path) if manifest_path else MANIFEST
    manifest = load_manifest(manifest_path)
    reviewed = 0
    for review_path in sorted(directory.glob("**/self_review.json")):
        run_dir = review_path.parent
        key = _key_near(run_dir) or _key_near(run_dir.parent)
        if not key:
            print(f"[HOLD] {review_path}: topic key missing", flush=True)
            continue
        reviewed += 1
        reasons = judge(run_dir, key)
        engine = _voice(run_dir)
        seconds = __import__("publish_gates")._duration_s(run_dir)
        if reasons:
            item = _hold(manifest, key, reasons, now, engine, seconds)
            item["review_notes"] = list(judge.last_notes)
            _export_copy(run_dir, item)
            print(f"[HOLD] {key}: {'; '.join(reasons)}", flush=True)
            continue
        if not (run_dir / "final.mp4").exists():
            item = _hold(
                manifest, key, ["render artifact has no final.mp4"], now, engine, seconds,
            )
            item["review_notes"] = list(judge.last_notes)
            _export_copy(run_dir, item)
            print(f"[HOLD] {key}: render artifact has no final.mp4", flush=True)
            continue
        try:
            item = _store_ready(manifest, run_dir, key, _github_store(), now)
        except Exception as exc:  # noqa: BLE001
            item = _hold(manifest, key, [f"store failed: {exc}"], now, engine, seconds)
            _export_copy(run_dir, item)
            print(f"[HOLD] {key}: store failed: {exc}", flush=True)
            continue
        mark_owner_approved(key)
        _export_copy(run_dir, item)
        print(f"[READY] {key} {item['id']} voice={item.get('voice_engine')}", flush=True)
    if reviewed:
        save_manifest(manifest, manifest_path)
        return 0
    for item_path in sorted(directory.glob("**/buffer_item.json")):
        try:
            item = json.loads(item_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(item, dict) or not item.get("topic_key"):
            continue
        key = str(item["topic_key"])
        stored = [part.strip() for part in str(item.get("reason") or "").split(";") if part.strip()]
        reasons, notes = [], []
        for part in stored:
            if _review_note(part):
                notes.append(part)
            else:
                reasons.append(part)
        if (item_path.parent / "gemini_quota.txt").exists() and "gemini_quota" not in reasons:
            reasons.append("gemini_quota")
        engine = str(item.get("voice_engine") or "")
        if engine and engine not in ACCEPTED_ENGINES and not any("voice_engine=" in r for r in reasons):
            reasons.append(f"voice_engine={engine} is held")
        for note in notes:
            print(f"[REVIEW] {key}: {note}", flush=True)
        if reasons:
            print(f"[HOLD] {key}: {'; '.join(reasons)}", flush=True)
        else:
            print(
                f"[HOLD] {key}: render artifact has no final.mp4"
                + (f" (review: {'; '.join(notes)})" if notes else ""),
                flush=True,
            )
            reasons = ["render artifact has no final.mp4"]
        held = _hold(manifest, key, reasons, now, engine, item.get("duration_s"))
        held["review_notes"] = notes
        reviewed += 1
    save_manifest(manifest, manifest_path)
    if not reviewed:
        print("[rescore] no run files and no buffer items", flush=True)
    return 0


def _export_copy(run_dir, item):
    """Write the row next to the run and into run/ for the commit job."""
    _export_item(item)
    try:
        (Path(run_dir) / "buffer_item.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    except OSError:
        return


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--publish" in argv:
        return publish_slot()
    if "--merge-parts" in argv:
        folder = argv[argv.index("--merge-parts") + 1]
        merge_parts(folder)
        return 0
    if "--rescore" in argv:
        folder = argv[argv.index("--rescore") + 1]
        return rescore(folder)
    if "--build" in argv:
        limit = MAX_PER_RUN
        if "--max" in argv:
            limit = int(argv[argv.index("--max") + 1])
        only = None
        if "--key" in argv:
            only = argv[argv.index("--key") + 1]
        return build(max_n=limit, only=only)
    print(
        "usage: approved_buffer.py --build [--max 6] [--key TOPIC] "
        "| --rescore DIR | --publish | --merge-parts DIR",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
