"""Text-only check of the live narration path. No TTS, no upload, no commit.

Picks upcoming Short topics without marking them used, runs the DeepSeek
narration (repair and trim included), the text gates, and the duration
estimate at the per-voice pace, then stops. ElevenLabs is never called.

  python narration_dry_run.py --topics 3
"""
import argparse
import json
import os
import sys

from common import ROOT, cfg
from pick_topic import CAT_COOLDOWN, _bank_recent_cats, _pick_fresh, load_research, load_trends

import board_short
import credit_log


def select_by_keys(keys, bank=None):
    """Topics named by key, in that order. The bank file is not written."""
    if bank is None:
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    by_key = {item.get("key"): item for item in (bank.get("topics") or [])}
    picked = []
    missing = []
    for key in keys:
        item = by_key.get(key)
        if not item:
            missing.append(key)
            continue
        picked.append(json.loads(json.dumps(item)))
    return picked, missing


def select_upcoming(count, bank=None):
    """N different unused Short topics. The bank file is not written."""
    if bank is None:
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
    shadow = json.loads(json.dumps(bank))
    research = (*load_research(ROOT), load_trends(ROOT))
    avoid = _bank_recent_cats(shadow, "used_short", CAT_COOLDOWN)
    picked = []
    for _ in range(max(0, int(count))):
        chosen = _pick_fresh(shadow, "used_short", avoid, research, "short")
        if not chosen:
            break
        picked.append(json.loads(json.dumps(chosen)))
        chosen["used_short"] = True
    return picked


def prepare_live(topic, c):
    """Force the six-beat live gates without turning on paid voice."""
    live = json.loads(json.dumps(topic))
    live.pop("_atr", None)
    live["_live_short"] = True
    live["_voice"] = dict((c or {}).get("voice") or {})
    return live


def beat_lines(narration, topic):
    order, targets, (total_lo, total_hi), _band = board_short._lesson(topic)
    beats = (narration or {}).get("beats") or {}
    lines = []
    total = 0
    for beat in order:
        words = len(str(beats.get(beat) or "").split())
        total += words
        lo, hi = board_short.beat_word_window(targets[beat], board_short.LIVE_BEAT_TOLERANCE)
        mark = "OK" if lo <= words <= hi else "FAIL"
        lines.append(f"  {beat} {words} words ({lo}-{hi}) {mark}")
    return lines, total, total_lo, total_hi


def format_report(topic, narration, calls, passed, reason=""):
    key = (topic or {}).get("key") or "?"
    lines, total, total_lo, total_hi = beat_lines(narration, topic)
    _words, wps, seconds = board_short._live_estimate(narration or {"beats": {}}, topic)
    mark = "PASS" if passed else "FAIL"
    out = [
        f"topic {key}",
        *lines,
        f"  total {total} words ({total_lo}-{total_hi})",
        f"  estimated {seconds:.1f}s at {wps:.2f} words/s",
        f"  deepseek_calls {int(calls)}",
        mark if not reason else f"{mark} {reason}",
    ]
    return "\n".join(out)


def _calls_used():
    data = credit_log._read() or {}
    try:
        return int(data.get("deepseek_calls") or 0)
    except (TypeError, ValueError):
        return 0


def evaluate_topic(topic, c):
    """Narrate one topic. Returns (passed, report). Never calls TTS."""
    credit_log.reset()
    cache = board_short.RUN / "narration.json"
    cache.unlink(missing_ok=True)
    (board_short.RUN / "narration_block.json").unlink(missing_ok=True)
    live = prepare_live(topic, c)
    narration = {"beats": {}}
    passed = False
    reason = ""
    try:
        narration, _source = board_short.narration(live, c)
        before = board_short._live_estimate(narration, live)[0]
        board_short.tighten_duration_margin(narration, live)
        gate = []
        if board_short._live_estimate(narration, live)[0] != before:
            gate = board_short.check_narration(narration, live)
        note = board_short._estimate_note(narration, live)
        if gate:
            reason = "; ".join(gate)
        elif note:
            reason = note
        else:
            passed = True
    except SystemExit as exc:
        reason = str(exc) or "narration blocked"
        block = board_short.RUN / "narration_block.json"
        if block.exists():
            try:
                payload = json.loads(block.read_text(encoding="utf-8"))
                reason = str(payload.get("reason") or reason)
            except (OSError, ValueError):
                pass
    calls = _calls_used()
    if isinstance(narration, tuple):
        narration = narration[0]
    return passed, format_report(live, narration if isinstance(narration, dict) else {}, calls, passed, reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live narration text check. Stops before TTS.")
    parser.add_argument("--topics", type=int, default=3)
    parser.add_argument("--topic-keys", default="", help="Comma-separated keys. Empty picks upcoming topics.")
    args = parser.parse_args(argv)
    os.environ["VIDEO_FORMAT"] = "short"
    os.environ["DRY_RUN"] = "1"
    os.environ.pop("ELEVENLABS_API_KEY", None)
    os.environ.pop("ELEVENLABS_OPT_IN", None)
    os.environ.pop("PUBLISH_VISIBILITY", None)
    bank_path = ROOT / "topic_bank.json"
    published = ROOT / "data" / "published.json"
    bank_before = bank_path.read_bytes()
    published_before = published.read_bytes() if published.exists() else None
    raw_keys = [piece.strip() for piece in str(args.topic_keys or "").split(",") if piece.strip()]
    if raw_keys:
        topics, missing = select_by_keys(raw_keys)
        if missing:
            print(f"FAIL unknown topic keys: {', '.join(missing)}")
            return 1
    else:
        count = max(1, int(args.topics))
        topics = select_upcoming(count)
        if len(topics) < count:
            print(f"FAIL only {len(topics)} upcoming topics, wanted {count}")
            return 1
    c = cfg()
    failed = 0
    for topic in topics:
        passed, report = evaluate_topic(topic, c)
        print(report)
        print("")
        if not passed:
            failed += 1
    if bank_path.read_bytes() != bank_before:
        print("FAIL topic_bank.json was written")
        return 1
    if published.exists() and published_before is not None and published.read_bytes() != published_before:
        print("FAIL data/published.json was written")
        return 1
    if failed:
        print(f"FAIL {failed} of {len(topics)} topics")
        return 1
    print(f"PASS {len(topics)} topics, no TTS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
