"""Pick the next unused topic from topic_bank.json, mark it used, write run/topic.txt.

Har topic ka ek long video aur ek short banta hai — isliye do alag flag:
"used" (long) aur "used_short" (short). Short us din ke long wale topic ka
teaser ban jaata hai aur viewer ko lambe video tak le jaata hai.

21-Sep-2026: category cooldown + cycle least-recently-used. Pehle saare
used_short ho jaane par topics[0] (hammer) dobara aa jata tha aur channel
pe same-looking candlestick shorts lagatar lagte the.
"""
import json
import os
from datetime import datetime, timezone

from common import ROOT, save, ok, die, video_format

# Last N short uploads ki category skip — feed pe same type dobara na lage
CAT_COOLDOWN = 3


def _recent_cats(n=CAT_COOLDOWN):
    """published.json se last n short video_ids -> unke topics ki cat (agar mile)."""
    pub_path = ROOT / "data" / "published.json"
    if not pub_path.exists():
        return []
    try:
        rows = json.loads(pub_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    shorts = [r for r in rows if r.get("format") == "short"][-n:]
    # video_id se topic nahi milta — bank me used_at / last picks se approximate:
    # topic_bank me jinke used_short True + used_short_at recent, unki cat lo
    return []  # filled below via bank timestamps


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
        return [str(x).lower().strip() for x in data.get("phrases_to_avoid_as_exact_or_near_duplicate_topics", []) if str(x).strip()]
    except (OSError, ValueError, TypeError):
        return []

def _is_competitor_duplicate(topic):
    text = str(topic.get("topic") or "").lower()
    return any(p in text for p in _seen_competitor_phrases())

def _pick_fresh(bank, flag, avoid_cats):
    """Unused topic only; never recycle a published short and never pick a
    phrase already seen in competitor research/screenshots."""
    unused = [t for t in bank["topics"] if not t.get(flag) and not _is_competitor_duplicate(t)]
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

    # TEST: TOPIC_KEY=<key> -> wahi topic, aur 'used' mark NAHI
    test_key = os.environ.get("TOPIC_KEY", "").strip()
    if test_key:
        nxt = next((t for t in bank["topics"] if t.get("key") == test_key), None)
        if not nxt:
        die(f"saare fresh topics '{flag}' khatam hain. Purana topic repeat nahi karunga — pehle naye verified topics jodo.")

    nxt[flag] = True
    nxt[at_key] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bank_path.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    save("topic.txt", nxt["topic"])
    save("topic.json", json.dumps(nxt, ensure_ascii=False, indent=1))
    ok(f"Today's topic (#{nxt['id']} {nxt.get('cat','')}): {nxt['topic']}")
    if avoid:
        print(f"[info] category cooldown skip: {avoid}")


if __name__ == "__main__":
    main()
