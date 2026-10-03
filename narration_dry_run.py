"""Live narration check. No ElevenLabs, no upload, no commit.

Picks upcoming Short topics without marking them used, runs the DeepSeek
narration (repair and trim included), the text gates, and the duration
estimate at the per-voice pace. With --full, free Edge TTS then renders
final.mp4. ElevenLabs is never called.

  python narration_dry_run.py --topics 3 --beats four
  python narration_dry_run.py --topics 1 --beats four --full
"""
import argparse
import json
import os
import sys
import traceback

from common import ROOT, cfg
from pick_topic import CAT_COOLDOWN, _bank_recent_cats, _pick_fresh, load_research, load_trends

import board_short
import credit_log
import market_data


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


def prepare_live(topic, c, beats=None):
    """Force the live gates without turning on paid voice."""
    live = json.loads(json.dumps(topic))
    live.pop("_atr", None)
    live["_live_short"] = True
    live["_voice"] = dict((c or {}).get("voice") or {})
    chosen = str(beats or live.get("_live_beats") or "").strip().lower()
    if chosen in ("four", "six"):
        live["_live_beats"] = chosen
    else:
        live["_live_beats"] = board_short.resolve_live_beats(live)
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


def apply_beat_overrides(directory, overrides, topic_key=""):
    """Write replacement beat lines into the approved script before the hash lock.

    narration() locks whatever narration.json says. The override has to be
    the file on disk first, or Edge dies on a hash mismatch.
    """
    import approved_script
    from pathlib import Path
    root = Path(directory)
    payload = approved_script.load_payload(root, topic_key) or {}
    beats = dict(payload.get("beats") or {})
    for beat, text in (overrides or {}).items():
        if beat not in approved_script.ORDER:
            continue
        cleaned = " ".join(str(text or "").split())
        if cleaned:
            beats[beat] = cleaned
    payload["beats"] = beats
    payload["approved_hashes"] = approved_script.hashes_for(beats)
    payload["source"] = "approved"
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    root.mkdir(parents=True, exist_ok=True)
    (root / "narration.json").write_text(raw, encoding="utf-8")
    key = str(topic_key or "").strip()
    if key:
        (root / f"narration_{key}.json").write_text(raw, encoding="utf-8")
    return payload


def evaluate_topic(topic, c, beats=None):
    """Narrate one topic. Returns (passed, report, narration). Never calls TTS."""
    credit_log.reset()
    cache = board_short.RUN / "narration.json"
    cache.unlink(missing_ok=True)
    (board_short.RUN / "narration_block.json").unlink(missing_ok=True)
    live = prepare_live(topic, c, beats=beats)
    narration = {"beats": {}}
    passed = False
    reason = ""
    try:
        narration, _source = board_short.narration(live, c)
        before = board_short._live_estimate(narration, live)[0]
        approved = isinstance(narration, dict) and narration.get("source") == "approved"
        if not approved:
            board_short.tighten_duration_margin(narration, live)
        gate = []
        if board_short._live_estimate(narration, live)[0] != before:
            gate = board_short.check_narration(narration, live)
        # An approved script is spoken as stored. The voice duration gate
        # decides, not the 1.9 words/s estimate.
        note = "" if approved else board_short._estimate_note(narration, live)
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
    payload = narration if isinstance(narration, dict) else {}
    return passed, format_report(live, payload, calls, passed, reason), payload


def clear_topic_voice():
    """Drop the previous topic's beat audio. The cache key is the text, and the files must not linger."""
    run = board_short.RUN
    for pattern in ("tts_*.mp3", "tts_*.engine", "tts_*.textid", "voice_clean_*.wav"):
        for path in run.glob(pattern):
            path.unlink(missing_ok=True)
    (run / "tts_text.txt").unlink(missing_ok=True)


def _keep_topic_artifacts(key, narration=None):
    """Copy this topic's video, spoken text, and narration aside before the next topic."""
    run = board_short.RUN
    video = run / "final.mp4"
    named = run / f"final_{key}.mp4"
    if video.exists():
        named.write_bytes(video.read_bytes())
    spoken = run / "tts_text.txt"
    if spoken.exists():
        (run / f"tts_{key}.txt").write_text(spoken.read_text(encoding="utf-8"), encoding="utf-8")
    payload = narration if isinstance(narration, dict) else None
    if payload is None:
        path = run / "narration.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
    if isinstance(payload, dict) and isinstance(payload.get("beats"), dict):
        (run / f"narration_{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )


def _print_self_review(key):
    """The same verdict a paid build would use. Missing or HOLD fails the topic."""
    path = board_short.RUN / "self_review.json"
    if not path.is_file():
        print(f"self-review {key}: missing verdict HOLD")
        raise SystemExit(f"self-review missing for {key}")
    review = json.loads(path.read_text(encoding="utf-8"))
    parts = review.get("parts") or {}
    if review.get("review_flag") and not review.get("public_ok"):
        verdict = "REVIEW"
    elif review.get("public_ok"):
        verdict = "PASS"
    else:
        verdict = "HOLD"
    bits = " ".join(
        f"{name} {parts.get(name)}"
        for name in ("hook", "visual", "accuracy", "trend", "craft", "sync")
    )
    print(f"self-review {key}: overall {review.get('overall')} verdict {verdict} {bits}")
    summary = str(review.get("summary") or "").strip()
    if summary:
        print(f"  {summary}")
    if verdict == "HOLD":
        raise SystemExit(f"self-review HOLD {key}")


def render_topic(topic, narration, c, beats=None):
    """Edge TTS and the visual pipeline. No upload. Caller already popped ElevenLabs."""
    live = prepare_live(topic, c, beats=beats)
    spec = market_data.spec_for_topic(live)
    if spec.get("atr"):
        market_data.apply_atr_card(live, spec["atr"])
    live["_chart_spec"] = spec
    live["_allow_stale_sample"] = True
    key = topic.get("key") or "topic"
    clear_topic_voice()
    try:
        board_short.prove_full_render(
            live, narration, c, spec,
            source=narration.get("source") or "llm",
            free="edge",
        )
    except (Exception, SystemExit):
        _keep_topic_artifacts(key, narration)
        if (board_short.RUN / "self_review.json").is_file():
            _print_self_review(key)
        raise
    _keep_topic_artifacts(key, narration)
    _print_self_review(key)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live narration check. Optional Edge render.")
    parser.add_argument("--topics", type=int, default=3)
    parser.add_argument("--topic-keys", default="", help="Comma-separated keys. Empty picks upcoming topics.")
    parser.add_argument("--beats", default="", choices=["", "four", "six"])
    parser.add_argument("--full", action="store_true", help="Render final.mp4 with free Edge TTS.")
    parser.add_argument("--narration-from-run", default="", help="Actions run id of an edge-dry-run script.")
    parser.add_argument("--overrides", default="", help="JSON object of beat text replacements.")
    parser.add_argument("--approved-dir", default="run/approved_script")
    args = parser.parse_args(argv)
    if not str(args.overrides or "").strip():
        args.overrides = os.environ.get("NARRATION_OVERRIDES", "")
    os.environ["VIDEO_FORMAT"] = "short"
    os.environ["DRY_RUN"] = "1"
    os.environ.pop("ELEVENLABS_API_KEY", None)
    os.environ.pop("ELEVENLABS_OPT_IN", None)
    os.environ.pop("PUBLISH_VISIBILITY", None)
    sample_rate = False
    if str(args.narration_from_run or "").strip():
        os.environ["NARRATION_FROM_RUN"] = str(args.narration_from_run).strip()
        os.environ["APPROVED_SCRIPT_DIR"] = str(args.approved_dir)
        os.environ["EDGE_SAMPLE_RATE"] = "+10%"
        sample_rate = True
    if args.beats in ("four", "six"):
        os.environ["LIVE_BEATS"] = args.beats
    bank_path = ROOT / "topic_bank.json"
    published = ROOT / "data" / "published.json"
    bank_before = bank_path.read_bytes()
    published_before = published.read_bytes() if published.exists() else None
    raw_keys = [piece.strip() for piece in str(args.topic_keys or "").split(",") if piece.strip()]
    if str(args.narration_from_run or "").strip() and str(args.overrides or "").strip():
        overrides = json.loads(args.overrides)
        if not isinstance(overrides, dict):
            print("FAIL overrides must be a JSON object")
            if sample_rate:
                os.environ.pop("EDGE_SAMPLE_RATE", None)
                os.environ.pop("NARRATION_FROM_RUN", None)
                os.environ.pop("APPROVED_SCRIPT_DIR", None)
            return 1
        apply_beat_overrides(args.approved_dir, overrides, raw_keys[0] if raw_keys else "")
    try:
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
        rendered = 0
        for topic in topics:
            key = topic.get("key") or "topic"
            try:
                passed, report, narration = evaluate_topic(topic, c, beats=args.beats)
            except (Exception, SystemExit):
                print(f"FAIL {key}\n{traceback.format_exc()}")
                failed += 1
                print("")
                continue
            print(report)
            if passed and args.full:
                try:
                    render_topic(topic, narration, c, beats=args.beats)
                    rendered += 1
                    print(f"RENDER {key} run/final_{key}.mp4")
                except (Exception, SystemExit):
                    print(f"FAIL render {key}\n{traceback.format_exc()}")
                    _keep_topic_artifacts(key)
                    failed += 1
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
        if args.full:
            print(f"PASS {len(topics)} topics, Edge render {rendered}")
        else:
            print(f"PASS {len(topics)} topics, no TTS")
        return 0
    finally:
        if sample_rate:
            os.environ.pop("EDGE_SAMPLE_RATE", None)
            os.environ.pop("NARRATION_FROM_RUN", None)
            os.environ.pop("APPROVED_SCRIPT_DIR", None)


if __name__ == "__main__":
    sys.exit(main())
