"""Pick the next unused topic from topic_bank.json, mark it used, write run/topic.txt."""
import json
from common import ROOT, save, ok, die

def main():
    bank_path = ROOT / "topic_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    nxt = next((t for t in bank["topics"] if not t["used"]), None)
    if not nxt:
        die("No unused topics left. Add more to topic_bank.json.")
    nxt["used"] = True
    bank_path.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    save("topic.txt", nxt["topic"])
    ok(f"Today's topic (#{nxt['id']}): {nxt['topic']}")

if __name__ == "__main__":
    main()
