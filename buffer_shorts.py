"""Backup buffer of 2–3 evergreen Shorts that have already passed every gate.

A scheduled slot whose own build fails is filled from the oldest ready item
instead of being left empty. Items live as GitHub release assets on the tag
`buffer-shorts`. `data/buffer_manifest.json` is the durable index: ready or
used, plus the gate-pass timestamps. A used item is never uploaded again.

Evergreen means a concept lesson with no live market series. Illustrative
numbers have to be labelled as an example on screen. The 3-trading-day
freshness gate is not changed: anything that shows a real as-of date still
goes through `market_data.public_upload_block`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import slot_guard

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "buffer_manifest.json"
RUN = ROOT / "run"
RELEASE_TAG = "buffer-shorts"
TARGET_READY = 3
MIN_READY = 2
# Buffer Shorts never call ElevenLabs. Live slots keep a separate floor.

# After the slot, a complete buffer post may still go out. YouTube needs a
# future publishAt, so a missed slot is scheduled UPLOAD_BUFFER plus this pad
# ahead of now — late, not instant, and not rolled onto another slot.
LAST_CHANCE_BEFORE = timedelta(hours=3)
LAST_CHANCE_AFTER = timedelta(minutes=90)
LATE_PAD = timedelta(minutes=5)

_LIVE_CATS = {"REAL DATA", "MARKET FACT"}
_EXAMPLE_MARKERS = ("example", "misal", "मिसाल", "उदाहरण", "मान लीजिए", "illustrative")
_NARRATION_OK = {"llm", "cache", "outline"}
_GATE_NAMES = (
    "fallback_narration",
    "ai_review",
    "sync",
    "craft",
    "number_match",
    "duplicate_text",
    "freshness",
)


class BufferError(RuntimeError):
    pass


class AlreadyUsed(BufferError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _iso(when=None):
    when = when or _now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_publish_at(value):
    text = str(value or "").strip().replace("+00:00", "Z")
    if not text:
        return ""
    if text.endswith("Z") and "." in text:
        text = text.split(".", 1)[0] + "Z"
    return text


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
    skipped = []
    for key in manifest.get("skipped_topics") or []:
        text = str(key or "").strip()
        if text and text not in skipped:
            skipped.append(text)
    if skipped:
        payload["skipped_topics"] = skipped
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def count_ready(manifest=None):
    manifest = load_manifest() if manifest is None else manifest
    return sum(1 for item in manifest.get("items") or [] if item.get("state") == "ready")


def ready_items(manifest):
    items = [item for item in manifest.get("items") or [] if item.get("state") == "ready"]
    items.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    return items


def on_screen_text(topic):
    parts = []
    beats = (topic or {}).get("beats") or {}
    if isinstance(beats, dict):
        for pair in beats.values():
            if isinstance(pair, (list, tuple)):
                parts.extend(str(piece) for piece in pair)
            else:
                parts.append(str(pair))
    thumbs = (topic or {}).get("thumb") or []
    if isinstance(thumbs, list):
        parts.extend(str(piece) for piece in thumbs)
    return "\n".join(parts)


def _has_number(text):
    import re
    return bool(re.search(r"\d", text or ""))


_NUMBER_TOKEN = None


def _only_rule_of_thumb_numbers(text):
    """True when every number is a bare small integer, such as RSI 70 or 2 गुना.

    Prices, rupee amounts, percentages, decimals, comma figures, years and
    anything above 200 are not rule-of-thumb numbers and still need an
    example label.
    """
    import re
    global _NUMBER_TOKEN
    if _NUMBER_TOKEN is None:
        _NUMBER_TOKEN = re.compile(
            r"(?P<cur>₹\s*|Rs\.?\s*|\$\s*)?(?P<num>\d[\d,]*(?:\.\d+)?)"
            r"(?P<unit>\s*(?:%|लाख|करोड़|हज़ार|lakh|crore|k\b))?",
            re.IGNORECASE,
        )
    for hit in _NUMBER_TOKEN.finditer(text or ""):
        if hit.group("cur") or hit.group("unit"):
            return False
        num = hit.group("num")
        if "," in num or "." in num or int(num) > 200:
            return False
    return True


def example_labelled(topic):
    """True when on-screen copy has no number, or the number is marked as an example.

    A bare small number (RSI 70, 2 गुना wick) is a rule of thumb, not data, and
    does not need the label. Any price, rupee, percent, year or large figure does.
    """
    screen = on_screen_text(topic)
    if not _has_number(screen):
        return True
    if _only_rule_of_thumb_numbers(screen):
        return True
    blob = screen.lower()
    return any(marker in blob or marker in screen for marker in _EXAMPLE_MARKERS)


def is_evergreen_topic(topic):
    """Concept lesson with no live market series.

    Returns (ok, reason). A real fetched series, REAL DATA, or MARKET FACT is
    not evergreen. That does not weaken the freshness gate: those topics stay
    on the normal publish path, which still calls public_upload_block.
    """
    if not isinstance(topic, dict):
        return False, "not a topic"
    import market_data
    key = str(topic.get("key") or "").strip()
    cat = str(topic.get("cat") or "").strip()
    if not key:
        return False, "topic has no key"
    if key in market_data.TOPIC_SERIES or key == "atr_sl":
        return False, "topic shows a fetched market series"
    if cat in _LIVE_CATS:
        return False, f"category {cat} is live or dated market data"
    spec = market_data.spec_for_topic(topic)
    if spec.get("mode") == "series" or spec.get("as_of"):
        return False, "chart spec has a market as-of"
    if not example_labelled(topic):
        return False, "on-screen numbers are not labelled as an example"
    return True, "evergreen concept; no live market as-of"


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _gate(passed, detail, when):
    return {"passed": bool(passed), "at": _iso(when) if passed else None, "detail": detail}


def evaluate_gates(run_dir, topic, now=None, publish_at=None, today=None):
    """Every gate a buffer item has to have passed. Missing evidence fails closed.

    `freshness` calls the existing public-upload rule. An evergreen item has no
    as-of stamp, so the rule does not trip. A real-data stamp is judged by that
    same rule and cannot be stored as evergreen.
    """
    import market_data
    import review_gate

    run_dir = Path(run_dir)
    when = now or _now()
    review = _load_json(run_dir / "self_review.json") or {}
    notes = [str(note) for note in (review.get("notes") or [])]
    parts = review.get("parts") if isinstance(review.get("parts"), dict) else {}
    blob = " ".join(notes).lower()

    blocked = (run_dir / "narration_block.json").exists() or review_gate.narration_is_fallback(review)
    source = str(review.get("narration_source") or review.get("source") or "").strip().lower()
    fallback_ok = (not blocked) and source in _NARRATION_OK and review_gate.narration_is_single_outline(review)
    ai_ok = review_gate.files_allow_public(run_dir, "short")
    sync_ok = review_gate.sync_gate_passed(review)
    try:
        craft_score = float(parts.get("craft"))
    except (TypeError, ValueError):
        craft_score = None
    craft_ok = craft_score is not None and craft_score >= 100 and "outside the 6%" not in blob
    try:
        accuracy = float(parts.get("accuracy"))
    except (TypeError, ValueError):
        accuracy = None
    number_ok = accuracy is not None and accuracy >= 100 and "numbers with no source" not in blob
    duplicate_ok = "overlap" not in blob and "duplicate" not in blob and craft_ok

    evergreen, evergreen_why = is_evergreen_topic(topic)
    probe_at = publish_at or "2099-01-01T00:00:00Z"
    fresh_reason = market_data.public_freshness_reason(run_dir, "public", probe_at, today=today)
    if fresh_reason:
        fresh_ok = False
        fresh_detail = fresh_reason
    elif not evergreen:
        fresh_ok = False
        fresh_detail = evergreen_why
    else:
        fresh_ok = True
        fresh_detail = "no live market as-of; 3-trading-day gate does not apply"

    gates = {
        "fallback_narration": _gate(fallback_ok, "llm outline" if fallback_ok else "fallback narration blocked", when),
        "ai_review": _gate(ai_ok, "READY" if ai_ok else "AI review is not public-ready", when),
        "sync": _gate(sync_ok, "whisper sync passed" if sync_ok else "topic-voice-visual sync did not pass", when),
        "craft": _gate(craft_ok, "craft 100" if craft_ok else "craft gate did not pass", when),
        "number_match": _gate(number_ok, "numbers match" if number_ok else "number-match gate did not pass", when),
        "duplicate_text": _gate(duplicate_ok, "no duplicate text" if duplicate_ok else "duplicate-text gate did not pass", when),
        "freshness": _gate(fresh_ok, fresh_detail, when),
    }
    gates["freshness"]["mode"] = "evergreen" if evergreen and fresh_ok else "real-data"
    return gates


def gates_pass(gates):
    return all(bool((gates.get(name) or {}).get("passed")) for name in _GATE_NAMES)


def topic_in_ledger(topic_key, entries):
    key = (topic_key or "").strip()
    if not key:
        return False
    for entry in entries or []:
        if isinstance(entry, dict) and str(entry.get("topic_key") or "") == key:
            return True
    return False


def topic_in_bank_used(topic_key, bank_topics):
    key = (topic_key or "").strip()
    for topic in bank_topics or []:
        if not isinstance(topic, dict) or topic.get("key") != key:
            continue
        if topic.get("used_short") or topic.get("used"):
            return True
    return False


def topic_in_manifest(topic_key, manifest):
    key = (topic_key or "").strip()
    for item in manifest.get("items") or []:
        if item.get("topic_key") == key:
            return True
    return False


def topic_blocked(topic_key, entries, bank_topics, manifest):
    """Dedupe against the ledger, the topic bank, and items already in the buffer."""
    return (
        topic_in_ledger(topic_key, entries)
        or topic_in_bank_used(topic_key, bank_topics)
        or topic_in_manifest(topic_key, manifest)
    )


def _slot_when(date_ist, slot):
    return slot_guard._at(date_ist, slot)


def in_last_chance(now, date_ist, slot):
    when = _slot_when(date_ist, slot)
    delta = when - now
    return -LAST_CHANCE_AFTER <= delta <= LAST_CHANCE_BEFORE


def publish_at_for(now, date_ist, slot, *, last_chance):
    """UTC publishAt, or None when this path must not fill the slot.

    The scheduled path keeps MIN_LEAD and MAX_LOOKAHEAD. The last-chance path
    may run inside that lead, and up to 90 minutes after the slot. A missed
    slot gets a near-future publishAt so the post is late rather than skipped.
    """
    when = _slot_when(date_ist, slot)
    delta = when - now
    if last_chance:
        if not (-LAST_CHANCE_AFTER <= delta <= LAST_CHANCE_BEFORE):
            return None
        if when >= now + slot_guard.UPLOAD_BUFFER:
            return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        late = now + slot_guard.UPLOAD_BUFFER + LATE_PAD
        return late.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if delta < slot_guard.MIN_LEAD or delta > slot_guard.MAX_LOOKAHEAD:
        return None
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usable_item(manifest, entries, bank_topics):
    """Oldest ready item whose topic is not already in the ledger.

    `bank_topics` is ignored here on purpose. Marking the topic used in the
    bank happens when the item is admitted, so the normal picker will not
    build it again. That mark must not block the fill.
    """
    _ = bank_topics
    for item in ready_items(manifest):
        key = item.get("topic_key") or ""
        if topic_in_ledger(key, entries):
            continue
        if item.get("state") != "ready":
            continue
        return item
    return None


def fill_decision(now, entries, scheduled, manifest, date_ist, slot, *, last_chance, bank_topics=None):
    """What to do for this slot. Never selects a hold, a filled row, or a used item."""
    bank_topics = bank_topics or []
    scheduled = {normalize_publish_at(value) for value in (scheduled or set()) if value}
    entry = slot_guard.find_entry(entries, date_ist, slot)
    state = slot_guard.row_state(entry)
    if state == "held":
        return {"action": "skip", "reason": "hold"}
    if state == "filled":
        return {"action": "skip", "reason": "filled"}
    publish_at = publish_at_for(now, date_ist, slot, last_chance=last_chance)
    if not publish_at:
        reason = "too_late" if last_chance else "too_close"
        if not last_chance:
            delta = _slot_when(date_ist, slot) - now
            if delta > slot_guard.MAX_LOOKAHEAD:
                reason = "too_early"
            elif delta < slot_guard.MIN_LEAD:
                reason = "too_close"
        return {"action": "skip", "reason": reason}
    slot_at = _slot_when(date_ist, slot).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if normalize_publish_at(slot_at) in scheduled or normalize_publish_at(publish_at) in scheduled:
        return {"action": "skip", "reason": "scheduled"}
    item = _usable_item(manifest, entries, bank_topics)
    if not item:
        return {"action": "skip", "reason": "empty", "publish_at": publish_at}
    late = _slot_when(date_ist, slot) < now + slot_guard.UPLOAD_BUFFER
    return {"action": "fill", "reason": "buffer", "item": item, "publish_at": publish_at, "late": late}


def claim_item(manifest, item_id, when=None):
    """Move ready → claimed. A used or claimed item is not uploaded again."""
    for item in manifest.get("items") or []:
        if item.get("id") != item_id:
            continue
        state = item.get("state")
        if state != "ready":
            raise AlreadyUsed(f"{item_id} is {state}; refusing a second upload")
        item["state"] = "claimed"
        item["claimed_at"] = _iso(when)
        return item
    raise BufferError(f"buffer item {item_id} is not in the manifest")


def mark_used(manifest, item_id, *, slot, date_ist, video_id, when=None):
    for item in manifest.get("items") or []:
        if item.get("id") != item_id:
            continue
        if item.get("state") == "used":
            raise AlreadyUsed(f"{item_id} is already used")
        if item.get("state") not in ("claimed", "ready"):
            raise AlreadyUsed(f"{item_id} is {item.get('state')}")
        item["state"] = "used"
        item["used_at"] = _iso(when)
        item["used_slot"] = slot
        item["used_date_ist"] = date_ist
        item["video_id"] = video_id
        return item
    raise BufferError(f"buffer item {item_id} is not in the manifest")


def release_claim(manifest, item_id):
    for item in manifest.get("items") or []:
        if item.get("id") == item_id and item.get("state") == "claimed":
            item["state"] = "ready"
            item.pop("claimed_at", None)
            return item
    return None


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _find_item(manifest, item_id):
    for item in manifest.get("items") or []:
        if item.get("id") == item_id:
            return item
    return None


def perform_fill(
    *,
    now,
    date_ist,
    slot,
    last_chance,
    read_entries,
    read_schedule,
    manifest_path,
    store,
    uploader,
    crosspost,
    alert,
    bank_topics=None,
    record=None,
    status_update=None,
):
    """Fill one open slot from the oldest ready item.

    The ledger and the live YouTube schedule are read again immediately before
    the upload. A hold, a filled row, a video already scheduled at this slot,
    or a used item never uploads.
    """
    bank_topics = list(bank_topics or [])
    entries = list(read_entries() or [])
    entry = slot_guard.find_entry(entries, date_ist, slot)
    state = slot_guard.row_state(entry)
    if state == "held":
        return {"action": "skip", "reason": "hold", "filled": False}
    if state == "filled":
        return {"action": "skip", "reason": "filled", "filled": False}
    scheduled = set(read_schedule() or [])
    manifest = load_manifest(manifest_path)
    decision = fill_decision(
        now, entries, scheduled, manifest, date_ist, slot,
        last_chance=last_chance, bank_topics=bank_topics,
    )
    if decision["action"] != "fill":
        if decision["reason"] == "empty":
            alert(slot, date_ist, "buffer empty")
        decision["filled"] = False
        return decision

    item_id = decision["item"]["id"]
    # Second read, still before any bytes go to YouTube.
    entries = list(read_entries() or [])
    scheduled = set(read_schedule() or [])
    manifest = load_manifest(manifest_path)
    decision = fill_decision(
        now, entries, scheduled, manifest, date_ist, slot,
        last_chance=last_chance, bank_topics=bank_topics,
    )
    if decision["action"] != "fill" or decision["item"]["id"] != item_id:
        decision["filled"] = False
        if decision.get("reason") == "empty":
            alert(slot, date_ist, "buffer empty")
        return decision

    item = _find_item(manifest, item_id)
    try:
        claim_item(manifest, item_id, when=now)
    except AlreadyUsed as exc:
        return {"action": "skip", "reason": "used", "detail": str(exc), "filled": False}
    save_manifest(manifest, manifest_path)

    try:
        payload = store.get(item["asset_name"])
        digest = sha256_bytes(payload)
        if digest != item.get("sha256"):
            raise BufferError(f"sha256 mismatch for {item_id}")
        import qa_hold
        held = qa_hold.hold_active()
        auto = qa_hold.auto_gate_active()
        upload_at = None if held or auto else decision["publish_at"]
        video_id = uploader(
            payload,
            item.get("title") or "",
            item.get("description") or "",
            list(item.get("tags") or []),
            upload_at,
        )
        if not str(video_id or "").strip():
            raise BufferError("upload returned no video id")
    except Exception:
        manifest = load_manifest(manifest_path)
        release_claim(manifest, item_id)
        save_manifest(manifest, manifest_path)
        raise

    manifest = load_manifest(manifest_path)
    mark_used(
        manifest, item_id, slot=slot, date_ist=date_ist, video_id=video_id, when=now,
    )
    save_manifest(manifest, manifest_path)
    if record is None:
        import ledger
        record = ledger.record_publish
    publish_at = None if held else decision["publish_at"]
    slot_skipped = None
    source_run = (os.environ.get("GITHUB_RUN_ID") or "").strip() or None if held else None
    if auto:
        import publish_gates
        if status_update is None:
            status_update = _youtube_status_update
        outcome = publish_gates.finish_item(
            item, video_id, RUN,
            slot=slot, date_ist=date_ist, now=now, update_status=status_update,
        )
        publish_at = outcome.get("publish_at")
        slot_skipped = outcome.get("slot_skipped")
        source_run = outcome.get("source_run")
        defer_at = outcome.get("defer_at")
    else:
        defer_at = None
    record(
        video_id,
        publish_at,
        slot,
        platform="youtube",
        date_ist=date_ist,
        topic_key=item.get("topic_key"),
        source="buffer",
        buffer_id=item_id,
        pending_qa=held,
        source_run=source_run,
        slot_skipped=slot_skipped,
    )
    if defer_at:
        import ig_defer
        ig_defer.write_deferred(RUN, video_id, defer_at, source_run or "")
    ig = {"posted": False, "media_id": ""}
    if held:
        qa_hold.mark_run(RUN)
        print("[qa] buffer upload held: private, no publishAt, no Instagram or Facebook", flush=True)
    elif auto:
        print("[auto] buffer Reel is not posted from this fill", flush=True)
    elif crosspost is not None:
        try:
            ig = crosspost(item, video_id, payload) or ig
        except SystemExit as exc:
            # skip() exits 0. A failed Reel must not discard the YouTube upload.
            if exc.code in (0, None):
                raise
            _soft_reel(video_id, f"Instagram publish rc={exc.code}")
            ig = {"posted": False, "media_id": "", "ig_skipped": True}
        except Exception as exc:  # noqa: BLE001 — the slot is already on YouTube
            _soft_reel(video_id, f"{type(exc).__name__}: {exc}")
            ig = {"posted": False, "media_id": "", "ig_skipped": True}
    return {
        "action": "fill",
        "reason": "buffer",
        "filled": True,
        "item": _find_item(load_manifest(manifest_path), item_id),
        "video_id": video_id,
        "publish_at": publish_at,
        "late": decision.get("late", False),
        "ig": ig,
    }


def failure_should_try_fill():
    """Scheduled public builds only. Unlisted tests and hold skips do not fill."""
    if os.environ.get("BUFFER_FILL", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    vis = os.environ.get("PUBLISH_VISIBILITY", "").strip().lower()
    slot = os.environ.get("PUBLISH_SLOT_IST", "").strip()
    date = os.environ.get("PUBLISH_DATE_IST", "").strip()
    if vis not in ("public", "now") or slot not in slot_guard.SLOTS or not date:
        return False
    return True


def build_failed_gates(log_text, run_dir=None):
    """True when this run did not leave a READY upload behind.

    A hold skip is not a gate failure. An upload that already wrote uploaded.txt
    is not a reason to add a second video.
    """
    run_dir = Path(run_dir) if run_dir else RUN
    if (run_dir / "uploaded.txt").exists():
        return False
    text = log_text or ""
    if "[skip]" in text and "before paid" in text:
        return False
    if (run_dir / "narration_block.json").exists():
        return True
    lowered = text.lower()
    if "deepseek call cap" in lowered or "skipping the slot" in lowered:
        return True
    if "failed at:" in lowered or "[stop]" in lowered or "[block]" in lowered or "[hold]" in lowered:
        return True
    if "traceback" in lowered or "quality-gate" in lowered:
        return True
    return False


def _gha(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def _alert_empty(slot, date_ist, reason):
    import health_check
    http = health_check.RequestsHttp()
    env = os.environ
    secrets = health_check.secrets_from_env(env)
    title = f"[health-alert] buffer empty — {date_ist} {slot} IST left unfilled"
    ready = count_ready(load_manifest())
    body = (
        "<!-- health-alert -->\n"
        f"## Buffer empty\n\n"
        f"{date_ist} {slot} IST is still unfilled. The backup buffer has "
        f"**{ready}** ready Short(s) (need at least 1 to fill, health check wants "
        f"{MIN_READY}). No second video was uploaded.\n\n"
        "Seed items with Actions → **Buffer refill** → Run workflow. "
        "Each run builds at most one evergreen Short.\n"
    )
    result = health_check.sync_issue(
        http,
        (env.get("GITHUB_REPOSITORY") or "").strip(),
        (env.get("GITHUB_TOKEN") or "").strip(),
        title,
        body,
        True,
        secrets,
    )
    print(f"[buffer] health-alert {result.action} ({reason})", flush=True)
    return result


def _load_bank():
    try:
        data = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    topics = data.get("topics") if isinstance(data, dict) else None
    return topics if isinstance(topics, list) else []


def _live_schedule():
    from yt_credentials import youtube_client_or_exit
    yt = youtube_client_or_exit()
    channels = yt.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        raise BufferError("YouTube schedule unreadable: no channel")
    uploads = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not uploads:
        raise BufferError("YouTube schedule unreadable: no uploads playlist")
    page = yt.playlistItems().list(playlistId=uploads, part="contentDetails", maxResults=15).execute()
    ids = []
    for row in page.get("items") or []:
        vid = ((row.get("contentDetails") or {}).get("videoId") or "").strip()
        if vid:
            ids.append(vid)
    if not ids:
        return set()
    listed = yt.videos().list(part="status", id=",".join(ids)).execute()
    found = set()
    for video in listed.get("items") or []:
        status = video.get("status") or {}
        when = status.get("publishAt")
        if when and status.get("privacyStatus") == "private":
            found.add(normalize_publish_at(when))
    return found


def _github_store():
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not repo or not token:
        raise BufferError("GITHUB_REPOSITORY and GITHUB_TOKEN are required to fetch buffer assets")
    return GitHubReleaseStore(repo, token)


def _youtube_status_update(video_id, privacy, publish_at):
    """Set privacy on a video that was uploaded private. Used after the gates pass."""
    import approve_qa
    import publish
    from yt_credentials import youtube_client_or_exit

    yt = youtube_client_or_exit()
    publish.require_channel(yt)
    yt.videos().update(
        part="status", body=approve_qa.status_body(video_id, privacy, publish_at),
    ).execute()


def private_upload_status(publish_at):
    """Private status. publishAt is omitted when QA hold has no schedule."""
    status = {
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status["publishAt"] = publish_at
    return status


def _youtube_uploader(payload, title, description, tags, publish_at):
    """Private upload. A schedule sets publishAt. A QA hold leaves it off."""
    import tempfile
    from yt_credentials import youtube_client_or_exit
    expected = (os.environ.get("EXPECTED_CHANNEL_ID") or "").strip()
    if not expected:
        raise BufferError("EXPECTED_CHANNEL_ID is not set")
    yt = youtube_client_or_exit()
    me = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    got = me[0]["id"] if me else None
    if got != expected:
        raise BufferError("YouTube token is for a different channel; buffer upload refused")
    from googleapiclient.http import MediaFileUpload
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
        handle.write(payload)
        path = handle.name
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags,
            "categoryId": "27",
        },
        "status": private_upload_status(publish_at),
    }
    try:
        media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _status, resp = req.next_chunk()
        return resp["id"]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def stage_run(item, video_id, payload, run_dir=None):
    """Write the gated files publish_instagram already knows how to read."""
    run_dir = Path(run_dir) if run_dir else RUN
    run_dir.mkdir(parents=True, exist_ok=True)
    files = item.get("files") if isinstance(item.get("files"), dict) else {}
    (run_dir / "final.mp4").write_bytes(payload)
    (run_dir / "uploaded.txt").write_text(str(video_id), encoding="utf-8")
    (run_dir / "topic.json").write_text(
        json.dumps(files.get("topic") or {"key": item.get("topic_key")}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "script.txt").write_text(files.get("script") or "", encoding="utf-8")
    (run_dir / "report.md").write_text(files.get("report") or "VERDICT: [READY TO PUBLISH]\n", encoding="utf-8")
    (run_dir / "verify_meta.json").write_text(
        json.dumps(files.get("verify_meta") or {}, ensure_ascii=False), encoding="utf-8")
    (run_dir / "self_review.json").write_text(
        json.dumps(files.get("self_review") or {}, ensure_ascii=False), encoding="utf-8")
    freshness = run_dir / "market_freshness.json"
    stamp = files.get("market_freshness")
    if stamp:
        freshness.write_text(json.dumps(stamp), encoding="utf-8")
    else:
        freshness.unlink(missing_ok=True)
    block = run_dir / "narration_block.json"
    block.unlink(missing_ok=True)


def _soft_reel(video_id, reason, run_dir=None):
    """The Short is on YouTube. Leave a retry marker instead of failing the slot."""
    import publish_instagram as ig
    print(f"[buffer] Reel soft-fail ({reason}); YouTube upload stands", flush=True)
    try:
        ig.mark_ig_skipped(Path(run_dir) if run_dir else RUN, video_id, reason)
    except Exception as exc:  # noqa: BLE001
        print(f"[buffer] ig_skipped note failed: {exc}", flush=True)


def _ig_exit_code(exc):
    code = exc.code
    if code in (0, None):
        return 0
    if isinstance(code, int):
        return code
    return 1


def crosspost_buffer(item, video_id, payload, run_dir=None):
    """IG Reel via the existing idempotency guard. A live or ledger hit does not post again.

    A QA hold does not post to Instagram or Facebook. Approve QA defers the Reel.

    A failed Reel after this YouTube id exists is a soft miss: ig_status.txt
    says skipped, and this function returns instead of exiting the process.
    """
    import publish_instagram as ig
    import qa_hold
    run_dir = Path(run_dir) if run_dir else RUN
    if qa_hold.hold_active() or qa_hold.run_is_held(run_dir):
        print("[qa] Instagram and Facebook cross-post held until Approve QA", flush=True)
        return {"posted": False, "media_id": "", "qa_hold": True}
    stage_run(item, video_id, payload, run_dir)
    # Freshness stays in force for a real as-of stamp staged with the item.
    import market_data
    reason = market_data.public_freshness_reason(run_dir, "private", item.get("publish_at") or "2099-01-01T00:00:00Z")
    if reason:
        raise BufferError(reason + ". Refusing the buffer Reel.")
    ident = ig.video_identity(run_dir / "final.mp4")
    try:
        already = ig.existing_media(ident)
    except ig.LiveCheckError as exc:
        print(f"[buffer] IG live check failed ({exc}); not posting a Reel", flush=True)
        _soft_reel(video_id, f"live Instagram check failed ({exc})", run_dir)
        return {"posted": False, "media_id": "", "error": "live-check", "ig_skipped": True}
    if already:
        (run_dir / "ig_reel.txt").write_text(str(already), encoding="utf-8")
        print(f"[buffer] IG already has {already}; not posting again", flush=True)
        return {"posted": False, "media_id": already}
    if os.environ.get("IG_CROSSPOST", "").strip().lower() not in ("1", "true", "yes", "on"):
        print("[buffer] IG_CROSSPOST off; Reel skipped", flush=True)
        return {"posted": False, "media_id": ""}
    try:
        ig.main()
    except SystemExit as exc:
        code = _ig_exit_code(exc)
        if code != 0:
            _soft_reel(video_id, f"Instagram publish rc={code}", run_dir)
            return {"posted": False, "media_id": "", "ig_skipped": True}
    except Exception as exc:  # noqa: BLE001
        _soft_reel(video_id, f"{type(exc).__name__}: {exc}", run_dir)
        return {"posted": False, "media_id": "", "ig_skipped": True}
    media = ""
    reel = run_dir / "ig_reel.txt"
    if reel.exists():
        media = reel.read_text(encoding="utf-8").strip()
    return {"posted": bool(media), "media_id": media}


def fill_from_env(last_chance, *, now=None, alert=None, uploader=None, crosspost=None, store=None, schedule=None):
    if not failure_should_try_fill() and not last_chance:
        return {"action": "skip", "reason": "not_scheduled", "filled": False}
    date_ist = (os.environ.get("BUFFER_DATE") or os.environ.get("PUBLISH_DATE_IST") or "").strip()
    slot = (os.environ.get("BUFFER_SLOT") or os.environ.get("PUBLISH_SLOT_IST") or "").strip()
    if slot not in slot_guard.SLOTS or not date_ist:
        return {"action": "skip", "reason": "no_slot", "filled": False}
    now = now or datetime.now(slot_guard.IST)
    if uploader is None:
        uploader = _youtube_uploader
    if crosspost is None:
        crosspost = crosspost_buffer
    if store is None:
        store = _github_store()
    if schedule is None:
        schedule = _live_schedule
    if alert is None:
        alert = _alert_empty

    def read_entries():
        return slot_guard._read()

    result = perform_fill(
        now=now,
        date_ist=date_ist,
        slot=slot,
        last_chance=last_chance,
        read_entries=read_entries,
        read_schedule=schedule,
        manifest_path=MANIFEST,
        store=store,
        uploader=uploader,
        crosspost=crosspost,
        alert=alert,
        bank_topics=_load_bank(),
    )
    _gha("filled", "true" if result.get("filled") else "false")
    _gha("buffer_reason", result.get("reason") or "")
    print(f"[buffer] {result.get('action')} reason={result.get('reason')} filled={result.get('filled')}", flush=True)
    return result


def cmd_fill(last_chance):
    try:
        result = fill_from_env(last_chance)
    except BufferError as exc:
        print(f"[buffer] {exc}", flush=True)
        _gha("filled", "false")
        return 1
    if result.get("filled"):
        return 0
    if result.get("reason") == "empty":
        return 1
    return 0


# ---------------------------------------------------------------- refill
def next_evergreen(bank_topics, entries, manifest):
    """First unused concept topic. At most one is built per refill run.

    A topic whose Edge build already failed is listed on the manifest and
    skipped. The topic bank is not marked used, so a live slot can still
    pick it later.
    """
    skipped = {
        str(key).strip()
        for key in (manifest or {}).get("skipped_topics") or []
        if str(key or "").strip()
    }
    eligible = []
    for topic in bank_topics or []:
        if not isinstance(topic, dict):
            continue
        ok, _why = is_evergreen_topic(topic)
        if not ok:
            continue
        if str(topic.get("key") or "") in skipped:
            continue
        if topic_blocked(topic.get("key"), entries, bank_topics, manifest):
            continue
        eligible.append(topic)
    # A topic with no scene of its own stops the build before TTS, so prefer
    # one that can render. With none renderable, keep the first eligible topic.
    try:
        import board_scenes
        renderable = [t for t in eligible if not board_scenes.topic_chart_block(t)]
    except ImportError:
        renderable = eligible
    pool = renderable or eligible
    return pool[0] if pool else None


def remember_skip(manifest, topic_key):
    """Remember a failed Edge build. Does not mark the topic bank used."""
    key = str(topic_key or "").strip()
    if not key:
        return manifest
    skipped = [str(item).strip() for item in (manifest.get("skipped_topics") or []) if str(item or "").strip()]
    if key not in skipped:
        skipped.append(key)
    manifest["skipped_topics"] = skipped
    return manifest


def _mark_bank_used(topic_key):
    path = ROOT / "topic_bank.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for topic in data.get("topics") or []:
        if topic.get("key") == topic_key:
            topic["used_short"] = True
            topic["used_short_at"] = _iso()
            break
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def admit_run(run_dir, topic, store, manifest, *, mp4, title, description, tags, now=None):
    """Store a built Short only when every gate passed. Returns (item or None, gates)."""
    run_dir = Path(run_dir)
    gates = evaluate_gates(run_dir, topic, now=now)
    if not gates_pass(gates):
        return None, gates
    digest = sha256_bytes(mp4)
    item_id = f"buf-{topic.get('key')}-{digest[:12]}"
    if any(existing.get("id") == item_id or existing.get("sha256") == digest for existing in manifest.get("items") or []):
        raise AlreadyUsed(f"{item_id} is already in the buffer")
    review = _load_json(run_dir / "self_review.json") or {}
    meta = _load_json(run_dir / "verify_meta.json") or {}
    script = ""
    script_path = run_dir / "script.txt"
    if script_path.exists():
        script = script_path.read_text(encoding="utf-8")
    topic_doc = _load_json(run_dir / "topic.json") or {"key": topic.get("key"), "cat": topic.get("cat")}
    item = {
        "id": item_id,
        "state": "ready",
        "topic_key": topic.get("key"),
        "title": title,
        "description": description,
        "tags": list(tags or []),
        "sha256": digest,
        "asset_name": f"{item_id}.mp4",
        "meta_name": f"{item_id}.json",
        "created_at": _iso(now),
        "used_at": None,
        "used_slot": None,
        "used_date_ist": None,
        "video_id": None,
        "gates": gates,
        "files": {
            "topic": topic_doc,
            "script": script,
            "report": (run_dir / "report.md").read_text(encoding="utf-8") if (run_dir / "report.md").exists() else "",
            "verify_meta": meta,
            "self_review": review,
        },
    }
    narration = _load_json(run_dir / "narration.json")
    if narration:
        item["files"]["narration"] = narration
    shots = _load_json(run_dir / "shots.json")
    if shots:
        item["files"]["shots"] = shots
    mix = _load_json(run_dir / "audio_mix.json")
    if mix:
        item["files"]["audio_mix"] = mix
    captions = run_dir / "subs.ass"
    if captions.exists():
        item["files"]["captions"] = captions.read_text(encoding="utf-8")
    meta_bytes = json.dumps(
        {
            "id": item_id,
            "topic_key": item["topic_key"],
            "title": title,
            "description": description,
            "tags": item["tags"],
            "sha256": digest,
            "gates": gates,
            "verify_meta": meta,
            "self_review": review,
            "sync": review.get("sync"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    store.put(item["asset_name"], mp4, "video/mp4")
    store.put(item["meta_name"], meta_bytes, "application/json")
    manifest.setdefault("items", []).append(item)
    return item, gates


def default_preview_runner(topic_key):
    """One Edge render through the normal pipeline. No publish and no ElevenLabs."""
    import subprocess
    env = os.environ.copy()
    env["VIDEO_FORMAT"] = "short"
    env["PIPELINE_MODE"] = "preview"
    env["PUBLISH_VISIBILITY"] = "unlisted"
    env["TOPIC_KEY"] = topic_key
    env["DEEPSEEK_MAX_CALLS"] = str(__import__("credit_log").DEFAULT_DEEPSEEK_CALLS)
    env["BUFFER_FILL"] = "0"
    env["ELEVENLABS_OPT_IN"] = "0"
    for key in (
        "PUBLISH_SLOT_IST", "PUBLISH_DATE_IST", "PUBLISH_AT_UTC",
        "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL",
    ):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, str(ROOT / "run_pipeline.py"), "--format", "short"],
        cwd=ROOT,
        env=env,
    ).returncode


def _voice_engine(run_dir):
    meta = _load_json(Path(run_dir) / "voice_meta.json") or {}
    return str(meta.get("engine") or "").strip().lower()


def refill(*, runner=None, store=None, now=None, manifest_path=None):
    """Build at most one evergreen Short when fewer than 3 are ready.

    The render is Edge only (hi-IN-MadhurNeural, +0%). ElevenLabs is not
    called and the character floor does not apply. Writes run/credits.json.
    Gates run on that Edge audio; a failed gate is not stored.
    """
    import credit_log
    manifest_path = Path(manifest_path) if manifest_path else MANIFEST
    manifest = load_manifest(manifest_path)
    ready = count_ready(manifest)
    try:
        if ready >= TARGET_READY:
            print(f"[buffer] {ready} ready; refill not needed", flush=True)
            return 0
        entries = slot_guard._read()
        topic = next_evergreen(_load_bank(), entries, manifest)
        if topic is None:
            print("[buffer] no unused evergreen topic left", flush=True)
            return 1
        print(f"[buffer] building {topic.get('key')} (ready {ready}/{TARGET_READY})", flush=True)
        os.environ["DEEPSEEK_MAX_CALLS"] = str(credit_log.DEFAULT_DEEPSEEK_CALLS)
        runner = runner or default_preview_runner
        rc = runner(topic["key"])
        if rc != 0:
            remember_skip(manifest, topic.get("key"))
            save_manifest(manifest, manifest_path)
            print(f"[buffer] Edge build failed rc={rc}; nothing stored; skip {topic.get('key')}", flush=True)
            return 1
        preview_gates = evaluate_gates(RUN, topic, now=now)
        if not gates_pass(preview_gates):
            failed = [name for name in _GATE_NAMES if not preview_gates[name]["passed"]]
            remember_skip(manifest, topic.get("key"))
            save_manifest(manifest, manifest_path)
            print(f"[buffer] gates failed ({', '.join(failed)}); not stored; skip {topic.get('key')}", flush=True)
            return 1
        import qa_hold
        if qa_hold.auto_gate_active():
            import publish_gates
            verdict = publish_gates.evaluate(RUN)
            if not verdict["passed"]:
                reason = "; ".join(verdict["reasons"])
                remember_skip(manifest, topic.get("key"))
                save_manifest(manifest, manifest_path)
                publish_gates.note_job_summary(f"gate: skipped — {reason}")
                print(f"[buffer] auto gates failed; not stored; skip {topic.get('key')}", flush=True)
                return 1
        if _voice_engine(RUN) != "edge":
            remember_skip(manifest, topic.get("key"))
            save_manifest(manifest, manifest_path)
            print("[buffer] buffer items are Edge only; refusing a non-Edge render", flush=True)
            return 1
        mp4_path = RUN / "final.mp4"
        if not mp4_path.exists():
            remember_skip(manifest, topic.get("key"))
            save_manifest(manifest, manifest_path)
            print("[buffer] final.mp4 missing after the Edge render", flush=True)
            return 1
        import publish
        script = (RUN / "script.txt").read_text(encoding="utf-8") if (RUN / "script.txt").exists() else ""
        sections = publish.sections(script)
        title = publish.build_title(sections) if sections.get("TITLE") else topic.get("topic") or topic["key"]
        try:
            description = publish.build_description(sections, publish.cfg())
            tags = publish.build_tags(title, publish.cfg())
        except Exception:  # noqa: BLE001 — metadata still has the title and topic
            description = topic.get("topic") or ""
            tags = ["stock market hindi", "trading hindi", "paise ki pathshala"]
        store = store or _github_store()
        item, _gates = admit_run(
            RUN, topic, store, manifest,
            mp4=mp4_path.read_bytes(),
            title=title,
            description=description,
            tags=tags,
            now=now,
        )
        if item is None:
            remember_skip(manifest, topic.get("key"))
            save_manifest(manifest, manifest_path)
            print("[buffer] admit refused the Edge render", flush=True)
            return 1
        save_manifest(manifest, manifest_path)
        _mark_bank_used(topic["key"])
        credit_log.set_tts_engine(
            "edge",
            "buffer refill uses Edge hi-IN-MadhurNeural +0%; ElevenLabs not called",
        )
        print(f"[buffer] stored {item['id']} sha256={item['sha256'][:12]}", flush=True)
        return 0
    finally:
        credit_log.write_summary()


# ---------------------------------------------------------------- GitHub release assets
class GitHubReleaseStore:
    """Release assets on the tag `buffer-shorts` in this repo.

    A name that already exists is not uploaded again.
    """

    def __init__(self, repo, token, tag=RELEASE_TAG):
        self.repo = repo
        self.token = token
        self.tag = tag

    def _json(self, method, url, payload=None):
        import urllib.error
        import urllib.request
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "paise-ki-pathshala-buffer",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise BufferError(f"GitHub {method} {url} HTTP {exc.code}") from exc

    def _release(self):
        import urllib.parse
        quoted = urllib.parse.quote(self.tag)
        url = f"https://api.github.com/repos/{self.repo}/releases/tags/{quoted}"
        found = self._json("GET", url)
        if found:
            return found
        created = self._json(
            "POST",
            f"https://api.github.com/repos/{self.repo}/releases",
            {
                "tag_name": self.tag,
                "name": "Buffer Shorts",
                "body": "Pre-gated evergreen Shorts for an empty Paise Ki Pathshala slot. Not a publish schedule.",
            },
        )
        if not created:
            raise BufferError(f"could not create release {self.tag}")
        return created

    def put(self, name, data, content_type):
        release = self._release()
        for asset in release.get("assets") or []:
            if asset.get("name") == name:
                raise AlreadyUsed(f"release asset {name} already exists; not re-uploaded")
        import urllib.parse
        import urllib.request
        upload = str(release.get("upload_url") or "").split("{", 1)[0]
        if not upload:
            upload = f"https://uploads.github.com/repos/{self.repo}/releases/{release['id']}/assets"
        url = upload + "?name=" + urllib.parse.quote(name)
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": content_type,
                "User-Agent": "paise-ki-pathshala-buffer",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, name):
        release = self._release()
        asset = next((row for row in release.get("assets") or [] if row.get("name") == name), None)
        if not asset or not asset.get("url"):
            raise BufferError(f"release asset {name} is missing")
        import urllib.request
        req = urllib.request.Request(
            asset["url"],
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/octet-stream",
                "User-Agent": "paise-ki-pathshala-buffer",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--fill-scheduled" in argv:
        return cmd_fill(False)
    if "--fill-last-chance" in argv:
        return cmd_fill(True)
    if "--refill" in argv:
        return refill()
    if "--count" in argv:
        ready = count_ready(load_manifest())
        print(f"ready={ready}")
        return 0 if ready >= MIN_READY else 1
    print("usage: buffer_shorts.py --fill-scheduled | --fill-last-chance | --refill | --count", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
