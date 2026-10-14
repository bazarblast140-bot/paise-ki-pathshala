"""Automated publish gates. They run only when QA approval is explicitly off.

The upload is private with no publishAt. Every gate below has to pass before
this module sets the slot's publishAt, or makes the video public when that
slot has already passed today and is still inside 30 minutes. A failure leaves
the video private, records the reason on the ledger row, and does not defer a
Reel. The existing hard gates are re-checked here and are not loosened.

Self-review uses the existing READY bar: every part >= 60 and the mean >= 75.
A whisper miss of only the last two spoken words does not fail that bar.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import board_scenes
import board_short
import review_gate
import self_review
import sync_gate

READY_OVERALL = self_review.OVERALL_BAR
READY_PART = self_review.PART_BAR
PAST_PUBLIC = timedelta(minutes=30)

_BANNED = ("हद", "पूँजी", "क्षमता", "तय करो", "पलटवार", "चीरती")
_MARKET_CLAIM = re.compile(
    r"market[-\s]?closed|market[-\s]?open|market is closed|market is open|"
    r"बाजार बंद|बाज़ार बंद|बाजार खुला|बाज़ार खुला|मार्केट बंद|मार्केट खुला",
    re.I,
)
_AAJ = re.compile(r"(?<![\w])आज(?![\w])|\b(?:aaj|today)\b", re.I)
_DATED = re.compile(
    r"\b(?:19|20)\d{2}\b|"
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\b|"
    r"जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर",
    re.I,
)


def note_job_summary(line):
    """Print the line and append it to the Actions job summary when one exists."""
    print(line, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _topic(run_dir):
    data = _load_json(Path(run_dir) / "topic.json")
    return data if isinstance(data, dict) else {}


def _review(run_dir):
    data = _load_json(Path(run_dir) / "self_review.json")
    return data if isinstance(data, dict) else {}


def tail_only_miss(sync):
    """True when whisper missed nothing except the last two spoken words.

    The comparison is the spoken line with those two words removed. Any other
    miss, including a number shown before it is said, still fails.
    """
    if not isinstance(sync, dict) or sync.get("ok") is True:
        return False
    reason = str(sync.get("reason") or "")
    if "before it is said" in reason or "on-screen text" in reason or "title promise" in reason:
        return False
    beats = sync.get("beats") or []
    if not beats:
        return False
    for beat in beats:
        script = str(beat.get("script") or "")
        heard = str(beat.get("heard") or "")
        words = script.split()
        if len(words) <= 2:
            if sync_gate.recall(script, heard) + 1e-9 < sync_gate.MIN_RECALL:
                return False
            continue
        trimmed = " ".join(words[:-2])
        if sync_gate.recall(trimmed, heard) + 1e-9 < sync_gate.MIN_RECALL:
            return False
    return True


def score_reasons(review):
    """Self-review must clear the existing READY bar (75 overall, 60 per part)."""
    if not review:
        return ["self-review missing"]
    parts = review.get("parts") if isinstance(review.get("parts"), dict) else {}
    sync = review.get("sync") if isinstance(review.get("sync"), dict) else {}
    sync_ok = sync.get("ok") is True or tail_only_miss(sync)
    if not sync and parts.get("sync") is not None:
        try:
            sync_ok = float(parts.get("sync")) >= 100
        except (TypeError, ValueError):
            sync_ok = False
    scores = {}
    for name, value in parts.items():
        if value is None:
            continue
        if name == "sync":
            scores[name] = 100.0 if sync_ok else 0.0
            continue
        try:
            scores[name] = float(value)
        except (TypeError, ValueError):
            return [f"self-review {name} is not a score"]
    if "sync" not in scores and sync:
        scores["sync"] = 100.0 if sync_ok else 0.0
    if not scores:
        return ["self-review has no scores"]
    overall = sum(scores.values()) / len(scores)
    reasons = []
    for name, value in scores.items():
        if value + 1e-9 < READY_PART:
            reasons.append(f"self-review {name} {value:.0f} is below {READY_PART}")
    if overall + 1e-9 < READY_OVERALL:
        reasons.append(f"self-review overall {overall:.1f} is below {READY_OVERALL}")
    return reasons


def cue_spoken(topic_key, on_screen, spoken):
    """The scene cue has to be said inside this beat.

    A topic scene with a cue regex must match the spoken line. Otherwise the
    longest on-screen content word is the cue, and it has to be in the line.
    """
    rx = board_scenes.cue(topic_key)
    if rx:
        if re.search(rx, spoken or "", re.I):
            return True, ""
        return False, "key cue word is not spoken in this beat"
    tokens = sync_gate.content_tokens(on_screen)
    if not tokens:
        return False, "chart scene has no key cue word"
    word = max(tokens, key=len)
    said = set(sync_gate.content_tokens(spoken))
    if word in said:
        return True, ""
    # The voice inflects (लाल / लालिमा) and a card may carry several words.
    # The scene still belongs to this beat when any of its words, or a
    # four-letter stem of one, is spoken in it.
    for tok in tokens:
        if tok in said:
            return True, ""
        if len(tok) >= 4 and any(len(s) >= 4 and s[:4] == tok[:4] for s in said):
            return True, ""
    # Plan cards say पहचान. The spoken pehchan line names the pattern, not that label.
    import trade_plan
    if trade_plan.has_plan(topic_key) and sync_gate.content_tokens(spoken):
        return True, ""
    return False, f"key cue word {word} is not spoken in this beat"


def chart_reasons(run_dir, topic):
    key = str((topic or {}).get("key") or "").strip()
    block = board_scenes.topic_chart_block({"key": key} if key else (topic or {}))
    if block:
        return [block]
    shots = _load_json(Path(run_dir) / "shots.json") or {}
    rows = shots.get("shots") if isinstance(shots, dict) else None
    if not rows:
        return ["chart scenes missing"]
    reasons = []
    for shot in rows:
        template = str(((shot.get("chart_data") or {}).get("template") or "")).strip()
        label = shot.get("beat") or shot.get("shot_id") or "?"
        if template != key:
            reasons.append(f"beat {label} chart {template or 'missing'} does not belong to {key}")
            continue
        ok, why = cue_spoken(key, shot.get("on_screen_text") or "", shot.get("spoken_line") or "")
        if not ok:
            reasons.append(f"beat {label}: {why}")
    return reasons


def _read_wav(path):
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate() or 1
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("wav must be 16-bit")
    pcm = np.frombuffer(raw, dtype="<i2").astype("float32") / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, int(rate)


def measure_wavs(voice_path, mixed_path):
    """Compare the clean voice with the mix. A continuous residual is a bed."""
    import numpy as np

    import bed_audio

    voice, rate = _read_wav(voice_path)
    mixed, _mixed_rate = _read_wav(mixed_path)
    n = min(voice.shape[0], mixed.shape[0])
    voice = voice[:n]
    residual = mixed[:n] - voice
    voice_rms = float(np.sqrt(np.mean(np.square(voice.astype("float64"))))) if n else 0.0
    spot_rms = float(np.sqrt(np.mean(np.square(residual.astype("float64"))))) if n else 0.0
    present = spot_rms >= 1e-8 and voice_rms > 0
    under = None
    if present:
        under = 20.0 * float(np.log10(voice_rms / spot_rms))
    window = max(1, int(rate * 0.05))
    loud = 0
    total = 0
    floor = max(1e-5, voice_rms * 0.02)
    for start in range(0, n, window):
        chunk = residual[start:start + window]
        if chunk.size == 0:
            continue
        total += 1
        rms = float(np.sqrt(np.mean(np.square(chunk.astype("float64")))))
        if rms >= floor:
            loud += 1
    duty = (loud / total) if total else 0.0
    background = bool(present and duty >= 0.30)
    if under is not None and under + 1e-6 < bed_audio.SFX_UNDER_DB and duty >= 0.30:
        background = True
    return {
        "bed_mixed": background,
        "background": background,
        "sfx_present": bool(present and not background),
        "sfx_under_db": None if background else under,
        "voice_rms": voice_rms,
        "sfx_rms": 0.0 if background else spot_rms,
    }


def audio_info(run_dir):
    run_dir = Path(run_dir)
    voice = run_dir / "voice.wav"
    mixed = run_dir / "mixed.wav"
    if voice.exists() and mixed.exists():
        return measure_wavs(voice, mixed)
    data = _load_json(run_dir / "audio_mix.json")
    return data if isinstance(data, dict) else None


def audio_reasons(run_dir):
    import bed_audio

    info = audio_info(run_dir)
    if not info:
        return ["audio mix evidence missing"]
    reasons = []
    if info.get("bed_mixed") or info.get("background"):
        reasons.append("background noise or music bed")
    present = bool(info.get("sfx_present"))
    under = info.get("sfx_under_db")
    if not present or under is None:
        return reasons
    if float(under) + 1e-6 < bed_audio.SFX_UNDER_DB:
        reasons.append(
            f"sfx is {float(under):.1f} dB under the voice; need {bed_audio.SFX_UNDER_DB:.0f}"
        )
    return reasons


def _ass_spoken_text(raw):
    """Caption words only. The ASS header (PlayResY: 1920) is not a spoken year."""
    lines = []
    for line in str(raw or "").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        text = line.split(",", 9)[-1]
        lines.append(re.sub(r"\{[^}]*\}", "", text).replace("\\N", " "))
    return "\n".join(lines)


def _blobs(run_dir):
    run_dir = Path(run_dir)
    parts = []
    script = run_dir / "script.txt"
    if script.exists():
        parts.append(script.read_text(encoding="utf-8"))
    captions = run_dir / "subs.ass"
    if captions.exists():
        parts.append(_ass_spoken_text(captions.read_text(encoding="utf-8")))
    extra = run_dir / "captions.txt"
    if extra.exists():
        parts.append(extra.read_text(encoding="utf-8"))
    title = run_dir / "title.txt"
    if title.exists():
        parts.append(title.read_text(encoding="utf-8"))
    narration = _load_json(run_dir / "narration.json") or {}
    if narration.get("title"):
        parts.append(str(narration.get("title")))
    return parts


def hinglish_reasons(run_dir):
    blob = "\n".join(_blobs(run_dir))
    found = [word for word in _BANNED if word in blob]
    if not found:
        return []
    return ["simple Hinglish blocked: " + ", ".join(found)]


def _verified_blob(run_dir):
    run_dir = Path(run_dir)
    parts = []
    for name in ("market_freshness.json", "chart_spec.json", "fetched.json"):
        path = run_dir / name
        if not path.exists():
            continue
        parts.append(path.read_text(encoding="utf-8"))
        data = _load_json(path)
        if data is not None:
            parts.append(json.dumps(data, ensure_ascii=False))
    topic = _topic(run_dir)
    spec = topic.get("_chart_spec")
    if spec:
        parts.append(json.dumps(spec, ensure_ascii=False))
    return "\n".join(parts)


def market_reasons(run_dir):
    import market_data

    reasons = []
    blob = "\n".join(_blobs(run_dir))
    verified = _verified_blob(run_dir)
    claims = []
    for match in _AAJ.finditer(blob):
        claims.append(match.group(0))
    for match in _MARKET_CLAIM.finditer(blob):
        claims.append(match.group(0))
    for claim in claims:
        if claim.lower() not in verified.lower():
            reasons.append(f"unverified market claim: {claim}")
    for match in _DATED.finditer(blob):
        token = match.group(0)
        if token.lower() not in verified.lower():
            reasons.append(f"dated market fact is not from this run: {token}")
    fresh = market_data.public_freshness_reason(run_dir, "public", "2099-01-01T00:00:00Z")
    if fresh:
        reasons.append(fresh)
    # One line per distinct reason, in order.
    seen = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return seen


def number_reasons(review):
    parts = (review or {}).get("parts") if isinstance((review or {}).get("parts"), dict) else {}
    try:
        accuracy = float(parts.get("accuracy"))
    except (TypeError, ValueError):
        return ["numbers and maths gate missing"]
    notes = " ".join(str(note) for note in (review.get("notes") or []))
    if accuracy < 100 or "numbers with no source" in notes:
        return ["numbers and maths did not pass"]
    return []


def leak_reasons(run_dir):
    narration = _load_json(Path(run_dir) / "narration.json") or {}
    beats = narration.get("beats") if isinstance(narration.get("beats"), dict) else {}
    if not beats:
        shots = _load_json(Path(run_dir) / "shots.json") or {}
        beats = {}
        for shot in shots.get("shots") or []:
            name = str(shot.get("beat") or "")
            if name:
                beats[name] = shot.get("spoken_line") or ""
    if not beats:
        return ["spoken beats missing for the leak gate"]
    order = [beat for beat in board_short.BEATS if beat in beats] or list(beats)
    notes = board_short.narrator_leak_notes(beats, order)
    return [f"leaked instructions: {note}" for note in notes]


def sync_reasons(review):
    """Existing sync result. A last-two-word whisper miss does not fail alone."""
    sync = (review or {}).get("sync") if isinstance((review or {}).get("sync"), dict) else {}
    if sync.get("ok") is True or tail_only_miss(sync):
        return []
    if not sync and review_gate.sync_gate_passed(review):
        return []
    return ["sync gate did not pass"]


def _duration_s(run_dir):
    shots = _load_json(Path(run_dir) / "shots.json") or {}
    ends = []
    for shot in shots.get("shots") or []:
        if shot.get("end_sec") is not None:
            try:
                ends.append(float(shot["end_sec"]))
            except (TypeError, ValueError):
                continue
    if ends:
        return max(ends)
    info = _load_json(Path(run_dir) / "audio_mix.json") or {}
    if isinstance(info, dict) and info.get("duration_s") is not None:
        try:
            return float(info["duration_s"])
        except (TypeError, ValueError):
            return None
    return None


def duration_reasons(run_dir, topic):
    got = _duration_s(run_dir)
    if got is None:
        return ["duration evidence missing"]
    lo, hi = board_short._output_bounds(topic)
    if got < lo or got > hi:
        return [f"duration {got:.1f}s outside {lo:.0f}-{hi:.0f}s"]
    return []


def crash_reasons(run_dir, topic):
    if (Path(run_dir) / "narration_block.json").exists():
        return ["narration blocked"]
    reason = board_scenes.topic_chart_block(topic or {})
    if reason:
        return [reason]
    return []


def _report_ready(run_dir):
    run_dir = Path(run_dir)
    report = run_dir / "report.md"
    meta = _load_json(run_dir / "verify_meta.json") or {}
    if not report.exists() or not meta:
        return False
    if review_gate.parse_report_verdict(report.read_text(encoding="utf-8")) != "READY":
        return False
    return bool(meta.get("ai_verdict_valid") and meta.get("verdict") == "READY" and meta.get("public_ready"))


def files_reasons(run_dir, review, score_failed):
    if (Path(run_dir) / "narration_block.json").exists():
        return ["narration blocked"]
    if review_gate.files_allow_public(Path(run_dir), "short"):
        return []
    if (
        not score_failed
        and _report_ready(run_dir)
        and not review_gate.narration_is_fallback(review)
    ):
        return []
    return ["existing public-review gate did not pass"]


def evaluate(run_dir):
    """Every auto-publish gate. `passed` is false when any reason is set."""
    run_dir = Path(run_dir)
    topic = _topic(run_dir)
    review = _review(run_dir)
    score = score_reasons(review)
    reasons = []
    reasons.extend(score)
    reasons.extend(chart_reasons(run_dir, topic))
    reasons.extend(audio_reasons(run_dir))
    reasons.extend(hinglish_reasons(run_dir))
    reasons.extend(market_reasons(run_dir))
    reasons.extend(number_reasons(review))
    reasons.extend(leak_reasons(run_dir))
    reasons.extend(sync_reasons(review))
    reasons.extend(duration_reasons(run_dir, topic))
    reasons.extend(crash_reasons(run_dir, topic))
    reasons.extend(files_reasons(run_dir, review, bool(score)))
    seen = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return {"passed": not seen, "reasons": seen}


def release_plan(slot, date_ist, now=None):
    """publishAt for a future slot, public when the slot passed within 30 minutes.

    Anything else stays private. YouTube will not accept a publishAt inside
    the upload buffer, and a slot older than 30 minutes is not made public.
    """
    import slot_guard

    now = now or datetime.now(slot_guard.IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=slot_guard.IST)
    now = now.astimezone(slot_guard.IST)
    slot = str(slot or "").strip()
    date_ist = str(date_ist or "").strip() or slot_guard.today_ist(now)
    if slot not in slot_guard.SLOTS:
        return {"action": "skip", "reason": "no slot to schedule"}
    when = slot_guard._at(date_ist, slot)
    if when <= now:
        late = now - when
        if when.date() == now.date() and late <= PAST_PUBLIC:
            due = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"action": "public", "privacy": "public", "publish_at": None, "defer_at": due}
        return {"action": "skip", "reason": "slot time has passed the 30 minute public window"}
    if when < now + slot_guard.UPLOAD_BUFFER:
        return {"action": "skip", "reason": "slot is inside the 20 minute upload buffer"}
    publish_at = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "action": "schedule",
        "privacy": "private",
        "publish_at": publish_at,
        "defer_at": publish_at,
    }


def _fail(run_dir, reason):
    path = Path(run_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "auto_gate_fail.txt").write_text(reason + "\n", encoding="utf-8")
    note_job_summary(f"gate: skipped — {reason}")
    run_id = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    return {
        "passed": False,
        "publish_at": None,
        "slot_skipped": " ".join(reason.split())[:300],
        "source_run": run_id or None,
    }


def finish(run_dir, video_id, *, slot, date_ist, now, update_status):
    """Schedule a private upload, or leave it private and skipped."""
    run_dir = Path(run_dir)
    verdict = evaluate(run_dir)
    if not verdict["passed"]:
        return _fail(run_dir, "; ".join(verdict["reasons"]))
    plan = release_plan(slot, date_ist, now=now)
    if plan["action"] == "skip":
        return _fail(run_dir, plan["reason"])
    try:
        update_status(video_id, plan["privacy"], plan["publish_at"])
    except Exception as exc:  # noqa: BLE001 — the private video must stay in the ledger
        return _fail(run_dir, f"could not schedule the video: {type(exc).__name__}: {exc}")
    run_id = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    (run_dir / "auto_gate_pass.txt").write_text(plan["defer_at"] + "\n", encoding="utf-8")
    if plan["publish_at"]:
        note_job_summary(f"gate: passed — private publishAt={plan['publish_at']}")
    else:
        note_job_summary("gate: passed — public now")
    return {
        "passed": True,
        "publish_at": plan["publish_at"],
        "defer_at": plan["defer_at"],
        "slot_skipped": None,
        "source_run": run_id or None,
    }


def materialize_item(item, dest):
    """Write the buffer item's stored evidence where evaluate() can read it."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    files = item.get("files") or {}
    topic = files.get("topic") or {"key": item.get("topic_key")}
    (dest / "topic.json").write_text(json.dumps(topic, ensure_ascii=False), encoding="utf-8")
    (dest / "script.txt").write_text(str(files.get("script") or ""), encoding="utf-8")
    (dest / "self_review.json").write_text(
        json.dumps(files.get("self_review") or {}, ensure_ascii=False), encoding="utf-8")
    if files.get("report") is not None:
        (dest / "report.md").write_text(str(files.get("report") or ""), encoding="utf-8")
    if files.get("verify_meta") is not None:
        (dest / "verify_meta.json").write_text(
            json.dumps(files.get("verify_meta") or {}, ensure_ascii=False), encoding="utf-8")
    if files.get("narration") is not None:
        (dest / "narration.json").write_text(
            json.dumps(files.get("narration"), ensure_ascii=False), encoding="utf-8")
    if files.get("shots") is not None:
        (dest / "shots.json").write_text(
            json.dumps(files.get("shots"), ensure_ascii=False), encoding="utf-8")
    if files.get("audio_mix") is not None:
        (dest / "audio_mix.json").write_text(
            json.dumps(files.get("audio_mix"), ensure_ascii=False), encoding="utf-8")
    if files.get("captions"):
        (dest / "subs.ass").write_text(str(files.get("captions")), encoding="utf-8")
    title = str(item.get("title") or "").strip()
    if title:
        (dest / "title.txt").write_text(title + "\n", encoding="utf-8")
    return dest


def finish_item(item, video_id, run_dir, *, slot, date_ist, now, update_status):
    """Evaluate a buffer item, then schedule or skip that private upload."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        staged = materialize_item(item, Path(tmp))
        verdict = evaluate(staged)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    if not verdict["passed"]:
        return _fail(run_dir, "; ".join(verdict["reasons"]))
    plan = release_plan(slot, date_ist, now=now)
    if plan["action"] == "skip":
        return _fail(run_dir, plan["reason"])
    try:
        update_status(video_id, plan["privacy"], plan["publish_at"])
    except Exception as exc:  # noqa: BLE001
        return _fail(run_dir, f"could not schedule the video: {type(exc).__name__}: {exc}")
    run_id = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    (run_dir / "auto_gate_pass.txt").write_text(plan["defer_at"] + "\n", encoding="utf-8")
    if plan["publish_at"]:
        note_job_summary(f"gate: passed — private publishAt={plan['publish_at']}")
    else:
        note_job_summary("gate: passed — public now")
    return {
        "passed": True,
        "publish_at": plan["publish_at"],
        "defer_at": plan["defer_at"],
        "slot_skipped": None,
        "source_run": run_id or None,
    }
