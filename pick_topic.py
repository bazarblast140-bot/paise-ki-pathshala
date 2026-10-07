"""Choose an original, unused topic from topic_bank.json.

Research signals only rank valid bank entries; missing data leaves the regular
rotation intact. Short topics keep their scene keys and pre-authored beats.

Trend weighting (data/trends.json, written by trend_research.py):
  a repeated cluster that shares a keyword with the topic adds 0.25, capped
  at 0.8 (competitor_patterns.TREND_WEIGHT_CAP). That is smaller than one
  strong pattern weight (2.0) or our own measured result (up to 0.75), so a
  current trend cannot force the pick. Stale or missing trends add zero.
  Placeholder topics — templated beats from the ids 63–133 audit — are removed
  before scoring and are never returned, including for TOPIC_KEY. The check
  is the beat text, not the id, so a rewrite that drops the template is
  eligible again.
  Dated rows in data/current_market_topics.json (valid_until) lose their
  priority boost and are not picked at or after that instant. A calendar
  date includes that whole IST day.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT, save, ok, die, video_format
from competitor_patterns import (
    blocked_topic_keys, is_near_competitor_duplicate, load_research, load_trends,
    score_topic,
)

# Structural fingerprint of the templated block (topic-bank audit, ids 63–102)
# and the semi-templated block (ids 103–133). A rewrite that drops these
# patterns becomes eligible again; the id is not what disqualifies a topic.
_TEMPLATED_THUMB = "आसान भाषा में समझो"
_TEMPLATED_HOOK = "इसे आसान तरीके से समझो"


def _beat_line(topic, name, index):
    beats = topic.get("beats") if isinstance(topic.get("beats"), dict) else {}
    group = beats.get(name)
    if not isinstance(group, list) or len(group) <= index:
        return None
    return group[index]


def is_placeholder_topic(topic):
    """True for templated rows that are not yet a real lesson.

    Templated: generic thumb line, generic hook line, and pehchan copied into
    matlab. Semi-templated: the hook line is repeated and the first fact is
    only the title. Hand-authored topics do not have both signals at once.
    """
    if not isinstance(topic, dict):
        return False
    thumb = topic.get("thumb")
    pehchan = _beat_line(topic, "pehchan", 1)
    templated = (
        isinstance(thumb, list) and len(thumb) > 1 and thumb[1] == _TEMPLATED_THUMB
        and _beat_line(topic, "hook", 1) == _TEMPLATED_HOOK
        and pehchan is not None and pehchan == _beat_line(topic, "matlab", 1)
    )
    hook0 = _beat_line(topic, "hook", 0)
    facts = topic.get("facts")
    semi = (
        isinstance(hook0, str) and hook0.strip() and hook0 == _beat_line(topic, "hook", 1)
        and isinstance(facts, list) and bool(facts) and facts[0] == topic.get("topic")
    )
    return bool(templated or semi)

CAT_COOLDOWN = 3


def _bank_recent_cats(bank, flag, n=CAT_COOLDOWN):
    stamped = []
    at_key = f"{flag}_at"
    for t in bank["topics"]:
        if t.get(flag) and t.get(at_key):
            stamped.append((t[at_key], t.get("cat") or ""))
    stamped.sort(reverse=True)
    return [c for _, c in stamped[:n] if c]


def _seen_competitor_phrases():
    path = ROOT / "data" / "seen_competitor_topics.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [str(x).strip() for x in data.get("phrases_to_avoid_as_exact_or_near_duplicate_topics", []) if str(x).strip()]
    except (OSError, ValueError, TypeError):
        return []


def _is_competitor_duplicate(topic):
    return is_near_competitor_duplicate(topic.get("topic", ""), _seen_competitor_phrases())


def load_gate_skips(path=None):
    """Topic keys whose narration gate already blocked a Short.

    The topic bank stays unused, so a later rewrite can still publish them.
    Without this list the highest-scoring blocked topic is picked forever
    and the slot stays empty.
    """
    path = path or (ROOT / "data" / "gate_skips.json")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    raw = data.get("keys") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    keys = []
    for key in raw:
        text = str(key or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def remember_gate_skip(key, path=None):
    text = str(key or "").strip()
    if not text:
        return []
    path = path or (ROOT / "data" / "gate_skips.json")
    keys = load_gate_skips(path)
    if text not in keys:
        keys.append(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": keys}, indent=1) + "\n", encoding="utf-8")
    print(f"[info] narration gate skipped topic {text} for the next Short")
    return keys


def _chart_blocked(topic):
    """True when the topic has no scene of its own and would stop the build.

    board_short refuses such a topic before TTS (it would paint the crash
    curve). Picking one only burns a run, so it is never picked.
    """
    try:
        import board_scenes
        return bool(board_scenes.topic_chart_block(topic))
    except Exception:  # noqa: BLE001 - a broken import must not empty the bank
        return False


def _pick_fresh(bank, flag, avoid_cats, research, fmt, now=None, skips=None):
    weights, market, own = research[0], research[1], research[2]
    trends = research[3] if len(research) > 3 else None
    blocked = blocked_topic_keys(market, now)
    skipped = {str(key).strip() for key in (skips or []) if str(key or "").strip()}
    unused = [t for t in bank["topics"]
              if not t.get(flag) and t.get("key") not in blocked
              and t.get("key") not in skipped
              and not _is_competitor_duplicate(t) and not is_placeholder_topic(t)]
    if not unused:
        return None
    # Prefer topics that can render. If none can, keep the old behaviour so the
    # build fails with its own clear error instead of an empty-bank message.
    renderable = [t for t in unused if not _chart_blocked(t)]
    if renderable:
        unused = renderable
    # Apply the category cooldown when possible, but do not stall if it would
    # otherwise exhaust the bank. Bank order remains the fallback/tie-break.
    # Trends only change the score; they do not bypass the cooldown or pull a
    # placeholder back in.
    outside_cooldown = [t for t in unused if (t.get("cat") or "") not in avoid_cats]
    candidates = outside_cooldown or unused
    return max(candidates, key=lambda t: score_topic(t, weights, market, own, fmt, trends, now=now))


def fresh_short(now=None):
    """Next Short the narration gate may try. Gate skips and used flags stay out."""
    bank_path = ROOT / "topic_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    flag = "used_short" if video_format() == "short" else "used"
    research = (*load_research(ROOT), load_trends(ROOT))
    avoid = _bank_recent_cats(bank, flag, CAT_COOLDOWN)
    return _pick_fresh(bank, flag, avoid, research, video_format(), now=now, skips=load_gate_skips())


def main():
    bank_path = ROOT / "topic_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    flag = "used_short" if video_format() == "short" else "used"
    at_key = f"{flag}_at"
    research = (*load_research(ROOT), load_trends(ROOT))

    test_key = os.environ.get("TOPIC_KEY", "").strip()
    if test_key:
        nxt = next((t for t in bank["topics"] if t.get("key") == test_key), None)
        if not nxt:
            die(f"TOPIC_KEY '{test_key}' topic_bank me nahi hai")
        if _is_competitor_duplicate(nxt):
            die(f"TOPIC_KEY '{test_key}' competitor-seen duplicate hai — naya angle/topic use karo")
        if is_placeholder_topic(nxt):
            die(f"TOPIC_KEY '{test_key}' placeholder/templated topic hai — pehle beats rewrite karo")
        if nxt.get("key") in blocked_topic_keys(research[1]):
            die(f"TOPIC_KEY '{test_key}' dated topic expire ho chuka hai — valid_until ke baad pick nahi hoga")
        save("topic.txt", nxt["topic"])
        save("topic.json", json.dumps(nxt, ensure_ascii=False, indent=1))
        ok(f"TEST topic (used mark nahi): {nxt['key']}")
        return

    avoid = _bank_recent_cats(bank, flag, CAT_COOLDOWN)
    nxt = _pick_fresh(bank, flag, avoid, research, video_format(), skips=load_gate_skips())
    if not nxt:
        die(f"saare fresh non-placeholder topics '{flag}' khatam hain. "
            "Templated placeholder topics pick nahi honge — pehle naye verified topics jodo.")

    # A Short is marked used_short only after a successful upload. Picking it,
    # or blocking on the narration gate, must leave the bank flags alone.
    if flag != "used_short":
        nxt[flag] = True
        nxt[at_key] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bank_path.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    save("topic.txt", nxt["topic"])
    save("topic.json", json.dumps(nxt, ensure_ascii=False, indent=1))
    ok(f"Today's topic (#{nxt['id']} {nxt.get('cat','')}): {nxt['topic']}")
    if avoid:
        print(f"[info] category cooldown: {avoid}")


if __name__ == "__main__":
    main()

