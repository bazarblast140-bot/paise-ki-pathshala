"""Slot pehredaar — dekhta hai ki abhi-abhi guzre slot ka video sach me gaya ya nahi.

Kyun: GitHub kabhi cron drop kar deta hai aur DeepSeek kabhi fail hota hai. Tab
slot chupchaap khali reh jaata tha aur pata hi tab chalta tha jab koi YouTube
kholta. Ab har slot ke ~45 min baad ye chalta hai aur khali slot par Telegram
bhejta hai.

Ledger: data/published.json — publish.py har upload ke baad likhta hai.
Sirf stdlib + requests (workflow halka rahe).
"""
import json, os, pathlib, sys
from datetime import datetime, timedelta, timezone

# Windows console cp1252 par emoji se crash karta hai (common.py me bhi yahi guard hai)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = pathlib.Path(__file__).parent
LEDGER = ROOT / "data" / "published.json"
IST = timezone(timedelta(hours=5, minutes=30))
SLOTS = ["08:30", "16:00", "21:00"]


def entries():
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def last_slot(now):
    """Aaj ka wo slot jo abhi guzar chuka hai (subah 08:30 se pehle = kal ka 21:00)."""
    hhmm = now.strftime("%H:%M")
    past = [s for s in SLOTS if s <= hhmm]
    if past:
        return now.strftime("%Y-%m-%d"), past[-1]
    return (now - timedelta(days=1)).strftime("%Y-%m-%d"), SLOTS[-1]


def target(now):
    """Kaunsa slot check karna hai. Workflow SLOT_CHECK deta hai (cron se), warna
    abhi-abhi guzra slot. Watcher khud late chale to bhi wahi slot dekhe jo dena tha."""
    want = os.environ.get("SLOT_CHECK", "").strip()
    if want not in SLOTS:
        return last_slot(now)
    if now.strftime("%H:%M") >= want:
        return now.strftime("%Y-%m-%d"), want
    return (now - timedelta(days=1)).strftime("%Y-%m-%d"), want


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[skip] Telegram set nahi hai — sirf console par.")
        return
    import requests
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text[:4000]}, timeout=30)
    r.raise_for_status()
    print("[OK] Telegram par bheja")


def main():
    now = datetime.now(IST)
    date, slot = target(now)
    if not LEDGER.exists():
        print(f"[skip] {LEDGER} abhi nahi hai — pehla upload banate hi ban jayega.")
        return
    hit = next((e for e in entries() if e.get("date_ist") == date and e.get("slot") == slot), None)
    if hit:
        print(f"[OK] {date} {slot} IST — https://youtu.be/{hit['video_id']}")
        return
    msg = (f"⚠️ Paise Ki Pathshala\n{date} ka {slot} IST slot KHALI gaya — koi video upload nahi hua.\n\n"
           f"Agla slot bharne ke liye:\ngh workflow run \"Paise Ki Pathshala\" "
           f"-R bazarblast140-bot/paise-ki-pathshala -f format=short -f privacy=public")
    print(msg)
    try:
        send(msg)
    except Exception as e:  # noqa: BLE001 — khabar na jaaye to bhi run fail na ho
        print(f"[warn] Telegram fail: {e}")


if __name__ == "__main__":
    sys.exit(main())
