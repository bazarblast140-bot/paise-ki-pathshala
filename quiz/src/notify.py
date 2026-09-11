"""Telegram pe run ka result bhejta hai.

Alag file me isliye hai ki workflow ke andar inline Python heredoc likhna
YAML indentation ke saath hamesha tootta hai (heredoc terminator indent nahi
ho sakta). Yahan test bhi kar sakte ho:

    python src/notify.py --dir out/20260825_morning --upload success --dry-run

Exit code hamesha 0 — notification fail hone se video pipeline nahi rukni chahiye.
Par status stdout pe saaf print hota hai aur LAST_RUN.md me chala jaata hai,
taaki "message aaya hi nahi" chup-chaap na chhupe.
"""
import argparse
import json
import os
import sys
from pathlib import Path


def build_message(meta_path: Path, slot: str, privacy: str, upload: str,
                  run_url: str = "") -> str:
    ok = upload == "success"
    lines = ["✅ QUIZ PATHSHALA short live" if ok else "❌ Short bana par upload FAIL",
             f"slot: {slot} | {privacy}"]
    try:
        m = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        lines.append(f"(metadata nahi mila: {e})")
        m = {}

    if m:
        subs = " + ".join(dict.fromkeys(m.get("subjects", []))) if m.get("subjects") else ""
        lines += ["", m.get("title", "(title nahi)")]
        detail = f"{m.get('duration_sec', '?')}s"
        if m.get("voice_engines"):
            detail += " | voice: " + ", ".join(m["voice_engines"])
        if subs:
            detail += f" | {subs}"
        lines.append(detail)
        if m.get("youtube_url"):
            lines += ["", m["youtube_url"]]
        if m.get("pinned_comment"):
            lines += ["", "Pin karne ke liye comment:", m["pinned_comment"]]

    if not ok and run_url:
        lines += ["", "Log: " + run_url]
    return "\n".join(lines)


def send(text: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        return "telegram: secrets missing — notification skip"
    try:
        import requests
    except ImportError:
        return "telegram: requests module missing"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text,
                  "disable_web_page_preview": "false"},
            timeout=30,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("ok"):
            return f"telegram: sent (http {r.status_code})"
        # Telegram ka error text hi asli diagnosis hai — chat_id galat, bot
        # blocked, bot ne chat start nahi kiya, waghairah.
        return (f"telegram: FAILED http={r.status_code} "
                f"desc={body.get('description', r.text[:200])}")
    except Exception as e:
        return f"telegram: FAILED {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--slot", default="?")
    ap.add_argument("--privacy", default="?")
    ap.add_argument("--upload", default="?")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--dry-run", action="store_true", help="bhejo mat, sirf dikhao")
    a = ap.parse_args()

    msg = build_message(a.dir / "metadata.json", a.slot, a.privacy,
                        a.upload, a.run_url)
    print("--- message ---")
    print(msg)
    print("---------------")
    status = "telegram: dry-run, bheja nahi" if a.dry_run else send(msg)
    print(status)
    Path("telegram_out.txt").write_text(status + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
