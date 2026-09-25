"""Pick the next fresh topic from topic_bank.json.

Shorts and long videos use separate flags (used_short / used). A published topic
is never recycled automatically. Competitor-seen phrases are also excluded.
When the fresh bank is exhausted, the pipeline stops instead of repeating old content.
"""
import json
import os
from datetime import datetime, timezone

from common import ROOT, save, ok, die, video_format

CAT_COOLDOWN = 3


def _bank_recent_cats(bank, flag, n=CAT_COOLDOWN):
    """Topics with recent used_*_at, newest first, return last n cats."""
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
        return [
            str(x).lower().strip()
            for x in data.get("phrases_to_avoid_as_exact_or_near_duplicate_topics", [])
            if str(x).strip()
        ]
    except (OSError, ValueError, TypeError):
        return []


def _is_competitor_duplicate(topic):
    text = str(topic.get("topic") or "").lower()
    return any(p in text for p in _seen_competitor_phrases())


def _pick_fresh(bank, flag, avoid_cats):
    """Pick an unused topic that is not a competitor-seen duplicate."""
    unused = [
        t for t in bank["topics"]
        if not t.get(flag) and not _is_competitor_duplicate(t)
    ]
    if not unused:
        return None
    for t in unused:
        if (t.get("cat") or "") not in avoid_cats:
            return t
    return unused[0]


def main():
    bank_path = ROOT / "topic_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    flag = "used_short" if video_format() == "short" else "used"
    at_key = f"{flag}_at"

    # TEST: TOPIC_KEY=<key> -> exact topic, but never mark it used.
    test_key = os.environ.get("TOPIC_KEY", "").strip()
    if test_key:
        nxt = next((t for t in bank["topics"] if t.get("key") == test_key), None)
        if not nxt:
            die(f"TOPIC_KEY '{test_key}' topic_bank me nahi hai")
        if _is_competitor_duplicate(nxt):
            die(f"TOPIC_KEY '{test_key}' competitor-seen duplicate hai — naya angle/topic use karo")
        save("topic.txt", nxt["topic"])
        save("topic.json", json.dumps(nxt, ensure_ascii=False, indent=1))
        ok(f"TEST topic (used mark nahi): {nxt['key']}")
        return

    avoid = _bank_recent_cats(bank, flag, CAT_COOLDOWN)
    nxt = _pick_fresh(bank, flag, avoid)
    if not nxt:
        die(
            f"saare fresh topics '{flag}' khatam hain. "
            "Purana topic repeat nahi karunga — pehle naye verified topics jodo."
        )

    nxt[flag] = True
    nxt[at_key] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bank_path.write_text(
        json.dumps(bank, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    save("topic.txt", nxt["topic"])
    save("topic.json", json.dumps(nxt, ensure_ascii=False, indent=1))
    ok(f"Today's topic (#{nxt['id']} {nxt.get('cat','')}): {nxt['topic']}")
    if avoid:
        print(f"[info] category cooldown skip: {avoid}")


if __name__ == "__main__":
    main()
