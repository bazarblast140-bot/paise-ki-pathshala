"""Mint a (long-lived) YouTube refresh token for the Paise Ki Pathshala channel.

Run this ON YOUR LAPTOP (it opens a browser), not in GitHub Actions:

    pip install google-auth-oauthlib google-api-python-client
    python youtube_auth.py --client-secret-file client_secret.json
    # or, if YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET are in .env:
    python youtube_auth.py

Optional:
    --set-github-secret   also store it in GitHub (needs `gh` logged in); the token
                          is piped to `gh secret set` via stdin, never printed.
    --repo OWNER/NAME     repo for --set-github-secret (default bazarblast140-bot/paise-ki-pathshala)
    --print-token         print the token in this terminal (for manual copy/paste)

BEFORE running: in Google Cloud Console the OAuth consent screen must be
"In production" (not "Testing"), otherwise Google kills the token after 7 days.
See README -> "YouTube token permanent kaise karein".

Safety: after authorising, the script reads back which channel you picked and asks
you to confirm before writing anything, so a token for the wrong channel is never saved.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

from common import ROOT, ok, die

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # to name the channel you just authorised + competitor research reads
    "https://www.googleapis.com/auth/youtube.readonly",
    # channel_profile.py edits the About page / banner; make_public.py flips privacy
    "https://www.googleapis.com/auth/youtube",
    # posting / pinning a comment under our own video needs force-ssl
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
DEFAULT_REPO = "bazarblast140-bot/paise-ki-pathshala"
SECRET_NAME = "YT_FIN_REFRESH_TOKEN"


def _write_env(token):
    path = ROOT / ".env"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"YOUTUBE_REFRESH_TOKEN={token}"
    if re.search(r"^YOUTUBE_REFRESH_TOKEN=.*$", text, re.M):
        text = re.sub(r"^YOUTUBE_REFRESH_TOKEN=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    path.write_text(text, encoding="utf-8")


def _client_config(args):
    if args.client_secret_file:
        data = json.loads(open(args.client_secret_file, encoding="utf-8").read())
        return data if ("installed" in data or "web" in data) else die(
            "client_secret.json me 'installed' ya 'web' section nahi mila.")
    cid = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    sec = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        die("Client ID/secret nahi mile. Ya to --client-secret-file client_secret.json do "
            "(Google Cloud -> Credentials -> OAuth client (Desktop app) -> Download JSON), "
            "ya .env me YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET bharo.")
    return {"installed": {
        "client_id": cid, "client_secret": sec,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-secret-file")
    ap.add_argument("--set-github-secret", action="store_true")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--print-token", action="store_true")
    ap.add_argument("--no-env", action="store_true", help="don't write .env")
    args = ap.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        die("Missing deps. Run: pip install -r requirements.txt")

    flow = InstalledAppFlow.from_client_config(_client_config(args), scopes=SCOPES)
    account = os.environ.get("YOUTUBE_ACCOUNT", "").strip()

    print("\nPehle confirm karo: Google Cloud -> OAuth consent screen = 'In production'.")
    print("(Testing me banaya token 7 din me mar jaata hai.)\n")
    print("Browser khul raha hai:")
    print(f"  1. Sign in as  {account}" if account else
          "  1. Wo Google account chuno jiske paas Paise Ki Pathshala channel hai")
    print("  2. Channel poochhe to 'Paise Ki Pathshala' chuno")
    print("  3. 'Google hasn't verified this app' aaye to: Advanced -> Go to <app> (unsafe)")
    print("  4. Saare permission boxes tick karo. Agar 'time-limited access' ka option aaye,")
    print("     to koi time limit MAT chuno.\n")

    extra = {"login_hint": account} if account else {}
    # prompt=consent + access_type=offline => Google always returns a refresh token
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", **extra)
    if not creds.refresh_token:
        die("Google ne refresh token nahi diya. https://myaccount.google.com/permissions par "
            "is app ka access hatao aur dobara chalao.")

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        die("Is account par koi YouTube channel nahi hai.")
    title, channel_id = items[0]["snippet"]["title"], items[0]["id"]

    print("\n" + "=" * 58)
    print(f"  Authorised channel : {title}")
    print(f"  Channel ID         : {channel_id}")
    print("  (GitHub variable YT_FIN_CHANNEL_ID isi ID se match hona chahiye)")
    print("=" * 58)
    if input("\nKya ye Paise Ki Pathshala channel hai? Uploads yahin jayenge. [y/N] ").strip().lower() != "y":
        die("Kuch nahi likha. Dobara chalao aur sahi channel chuno.")

    token = creds.refresh_token
    if not args.no_env:
        _write_env(token)
        ok("wrote YOUTUBE_REFRESH_TOKEN to .env (gitignored)")

    if args.set_github_secret:
        if not shutil.which("gh"):
            die("gh CLI nahi mila — neeche wala manual step karo.")
        subprocess.run(["gh", "secret", "set", SECRET_NAME, "-R", args.repo],
                       input=token, text=True, check=True)
        ok(f"GitHub secret {SECRET_NAME} updated in {args.repo}")
    else:
        print("\nAb GitHub secret update karo (token terminal me print kiye bina):")
        print(f"  gh secret set {SECRET_NAME} -R {args.repo} < <(grep '^YOUTUBE_REFRESH_TOKEN=' .env | cut -d= -f2-)")
        print("  ya: GitHub -> repo Settings -> Secrets and variables -> Actions -> "
              f"{SECRET_NAME} -> Update")
    if args.print_token:
        print(f"\nREFRESH TOKEN (kisi ko mat bhejna):\n{token}\n")
    print("\nCheck: python yt_credentials.py   (sirf channel ka naam dikhata hai, upload nahi karta)")


if __name__ == "__main__":
    sys.exit(main())
