"""Pick the next unused topic from topic_bank.json, mark it used, write run/topic.txt.

Har topic ka ek long video aur ek short banta hai — isliye do alag flag:
"used" (long) aur "used_short" (short). Short us din ke long wale topic ka
teaser ban jaata hai aur viewer ko lambe video tak le jaata hai."""
import json
from common import ROOT, save, ok, die, video_format

def main():
    bank_path = ROOT / "topic_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    flag = "used_short" if video_format() == "short" else "used"
    nxt = next((t for t in bank["topics"] if not t.get(flag)), None)
    if not nxt:
        die(f"No unused topics left for '{flag}'. Add more to topic_bank.json.")
    nxt[flag] = True
    bank_path.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save("topic.txt", nxt["topic"])
    ok(f"Today's topic (#{nxt['id']}): {nxt['topic']}")

if __name__ == "__main__":
    main()
