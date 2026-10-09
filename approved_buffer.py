"""Gated buffer of Hammer v2 topic videos.

The owner approved the format. A topic is stored, and marked owner_approved,
only when that video passes every gate. An Edge take, a short review, or a
failed sync is held and never published. 08:30 and 21:00 publish the oldest
ready item. 16:00 stays Aaj ka Nifty.
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


def candidate_keys(entries, manifest, now, limit=MAX_PER_RUN):
    """Approved topics that are not ready and were not published in 14 days."""
    import approved_bank
    import trade_plan

    found = []
    for script in approved_bank.all_scripts():
        key = str(script.get("key") or "").strip()
        if not key or key == "aaj_ka_nifty" or not trade_plan.has_plan(key):
            continue
        if _ready(manifest, key) or _held_today(manifest, key, now):
            continue
        if published_within(entries, key, now):
            continue
        found.append(key)
        if len(found) >= int(limit):
            break
    return found


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


def judge(run_dir, key):
    """Reasons this render must be held. Empty means it may be stored."""
    import publish_gates
    import trade_plan

    run_dir = Path(run_dir)
    reasons = []
    try:
        verdict = publish_gates.evaluate(run_dir)
    except Exception as exc:  # noqa: BLE001 — a broken run is held, not published
        verdict = {"passed": False, "reasons": [f"hard gate crashed: {type(exc).__name__}"]}
    if not verdict.get("passed"):
        reasons.extend(verdict.get("reasons") or ["hard gate failed"])
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
    if overall is None or overall < REVIEW_MIN:
        reasons.append(f"self-review {overall} is under {REVIEW_MIN:.0f}")
    parts = review.get("parts") if isinstance(review.get("parts"), dict) else {}
    for name in PARTS:
        try:
            score = float(parts.get(name))
        except (TypeError, ValueError):
            reasons.append(f"{name} score missing")
            continue
        if score < PART_MIN:
            reasons.append(f"{name} {score:.0f} is below {PART_MIN:.0f}")
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
    seen = []
    for item in reasons:
        if item not in seen:
            seen.append(item)
    return seen


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
    ready = [
        item for item in (manifest.get("items") or [])
        if item.get("state") == "ready" and item.get("owner_approved") is True
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

    _handled()
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
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--publish" in argv:
        return publish_slot()
    if "--merge-parts" in argv:
        folder = argv[argv.index("--merge-parts") + 1]
        merge_parts(folder)
        return 0
    if "--build" in argv:
        limit = MAX_PER_RUN
        if "--max" in argv:
            limit = int(argv[argv.index("--max") + 1])
        only = None
        if "--key" in argv:
            only = argv[argv.index("--key") + 1]
        return build(max_n=limit, only=only)
    print(
        "usage: approved_buffer.py --build [--max 6] [--key TOPIC] | --publish | --merge-parts DIR",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
