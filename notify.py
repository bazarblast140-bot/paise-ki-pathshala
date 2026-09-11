"""Sends the verify report to Telegram. Called at the end of every run.

Degrades gracefully: if Telegram isn't configured, the report still prints to the
console. A missing notification must never look like a failed video.
"""
import os, sys, requests
from common import read, RUN, ok

def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[skip] Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). "
              "Report printed above only.")
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text[:4000]}, timeout=30)
    r.raise_for_status()
    return True

def main():
    status = "success"
    if "--status" in sys.argv:
        status = sys.argv[sys.argv.index("--status") + 1]

    if (RUN / "report.md").exists():
        body = read("report.md")
    else:
        details = []
        for gf in ("gate1_report.txt", "gate2_report.txt", "gate3_report.txt"):
            if (RUN / gf).exists():
                details.append(f"• {gf}:\n" + (RUN / gf).read_text(encoding="utf-8"))
        diag = "\n\n".join(details) if details else "Check console output logs."
        body = f"STATUS: {status}\n\n⚠️ Gate Details:\n{diag}"

    message = "🎬 Daily Finance Video\n\n" + body
    try:
        if send(message):
            ok("notified on Telegram")
    except requests.RequestException as e:
        # never fail the pipeline over a notification
        print(f"[warn] Telegram send failed: {e}")

if __name__ == "__main__":
    main()
