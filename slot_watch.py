"""Slot pehredaar — dekhta hai ki abhi-abhi guzre slot ka video sach me gaya ya nahi.

Kyun: GitHub kabhi cron drop kar deta hai aur DeepSeek kabhi fail hota hai. Tab
slot chupchaap khali reh jaata tha aur pata hi tab chalta tha jab koi YouTube
kholta. Ab har slot ke ~45 min baad ye chalta hai aur khali slot par Telegram
bhejta hai.

Ledger: data/published.json — publish.py har upload ke baad likhta hai.
Sirf stdlib + requests (workflow halka rahe).
"""
import json, os, pathlib, sys
from datetime import datetime, timedelta

import slot_guard

# Windows console cp1252 par emoji se crash karta hai (common.py me bhi yahi guard hai)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = pathlib.Path(__file__).parent
LEDGER = ROOT / "data" / "published.json"
IST = slot_guard.IST
SLOTS = list(slot_guard.SLOTS)


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


def gha_output(key, value):
    """GitHub Actions ko batao (fill job isi output par chalta hai)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            print(f"{key}={value}", file=f)


def _flags(missing, fill=False, use_buffer=False, buffer_date="", buffer_slot="", nifty_fill=False):
    gha_output("missing", "true" if missing else "false")
    # fill is the earliest unfilled slot still inside the lookahead,
    # scheduled via publishAt. A missed slot is not rolled and does not
    # become an extra instant-public video.
    gha_output("fill", "true" if fill else "false")
    # 16:00 on a trading day is the Nifty data Short, even when the cron dropped.
    gha_output("nifty_fill", "true" if nifty_fill else "false")
    # Last-chance buffer fill for THIS slot when it is still empty, not on
    # hold, and close to its publish time. A late complete post beats a skip.
    gha_output("use_buffer", "true" if use_buffer else "false")
    gha_output("buffer_date", buffer_date or "")
    gha_output("buffer_slot", buffer_slot or "")


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
        _flags(False)
        return
    ents = entries()
    start = min((e.get("date_ist", "") for e in ents), default="")
    if start and date < start:
        # ledger shuru hone se pehle ka din — us waqt ka record hai hi nahi, jhoothi alert mat do
        print(f"[skip] {date} ledger se purana hai (ledger {start} se).")
        _flags(False)
        return
    # Withdrawn rows keep the old video id private. They are not this slot's upload.
    hit = slot_guard.find_entry(ents, date, slot)
    if hit and slot_guard.is_hold(hit):
        # A hold is not a missing upload. Do not fill it and do not page about a video id.
        msg = (f"⚠️ Paise Ki Pathshala\n{date} {slot} IST is on hold "
               f"(platform=hold).\nSlot watch is not filling this slot.\n")
        print(msg)
        _flags(False)
        try:
            send(msg)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Telegram fail: {e}")
        return
    if hit and not hit.get("video_id"):
        # YouTube id missing. Do not fill: a re-run would post another public Reel.
        # Do not blame the OAuth Testing expiry. The token health step can be
        # green while Chart Board or the upload failed for a different reason.
        # That reason, when we have it, is the ledger note.
        note = " ".join(str(hit.get("note") or "").split())
        msg = (f"⚠️ Paise Ki Pathshala\n{date} {slot} IST: YouTube video id missing"
               f" (platform={hit.get('platform') or 'unknown'}).\n"
               "Slot watch is not filling this slot, so a second Reel is not posted.\n")
        if note:
            msg += f"Recorded YouTube failure: {note}\n"
        else:
            msg += ("No failure note was stored. Read the pipeline log "
                    "(Chart Board, verify, or the upload step). "
                    "A healthy YouTube token check means this is not a refresh-token rejection.\n")
        print(msg)
        _flags(False)
        try:
            send(msg)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Telegram fail: {e}")
        return
    if hit:
        print(f"[OK] {date} {slot} IST — https://youtu.be/{hit['video_id']}")
        _flags(False)
        return
    nxt = slot_guard.next_unfilled(now, ents)
    msg = (f"⚠️ Paise Ki Pathshala\n{date} ka {slot} IST slot KHALI gaya — koi video upload nahi hua.\n\n"
           f"Agla slot bharne ke liye:\ngh workflow run \"Paise Ki Pathshala\" "
           f"-R bazarblast140-bot/paise-ki-pathshala -f format=short -f privacy=public")
    if nxt:
        msg += (f"\n\nNext unfilled slot still ahead: {nxt['date_ist']} {nxt['slot']} IST "
                f"(publishAt {nxt['publish_at']}). No extra public post.")
    print(msg)
    import buffer_shorts
    use_buffer = buffer_shorts.in_last_chance(now, date, slot)
    if use_buffer:
        print(f"[buffer] last chance for {date} {slot} IST — fill from the buffer if it is still empty")
    import nifty_day
    nifty_fill = (
        slot == nifty_day.SLOT
        and nifty_day.is_trading_day(datetime.strptime(date, "%Y-%m-%d").date())
    )
    if nifty_fill:
        print(f"[nifty] {date} {slot} IST is empty on a trading day; data Short is the fallback")
    _flags(
        True, fill=bool(nxt), use_buffer=use_buffer,
        buffer_date=date, buffer_slot=slot, nifty_fill=nifty_fill,
    )
    try:
        send(msg)
    except Exception as e:  # noqa: BLE001 — khabar na jaaye to bhi run fail na ho
        print(f"[warn] Telegram fail: {e}")


if __name__ == "__main__":
    sys.exit(main())
