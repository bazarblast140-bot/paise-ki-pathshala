"""One-time YouTube authorisation for the FINANCE channel.

    python youtube_auth.py

Opens a browser, asks which Google account and channel to authorise, then writes
YOUTUBE_REFRESH_TOKEN into .env.

The client id and secret identify the *app* and are shared with the Hindi
project. The refresh token identifies the *channel*, and must be minted fresh
here — reusing the Hindi channel's token would upload finance videos to the
Hindi channel, which is the one mistake this whole separation exists to prevent.
So this script reads the channel back after authorising and shows you which one
you actually picked, before writing anything.
"""
import os
import re
import sys

from common import ROOT, need, ok, die

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # read-only is not needed to upload; it is here so we can name the channel
    # you just authorised instead of asking you to take it on faith
    "https://www.googleapis.com/auth/youtube.readonly",
    # full scope: channel_profile.py edits the About page and banner, and
    # cleanup_videos.py removes videos. Neither is possible with upload alone.
    "https://www.googleapis.com/auth/youtube",
]


def _write_env(token):
    path = ROOT / ".env"
    text = path.read_text(encoding="utf-8")
    line = f"YOUTUBE_REFRESH_TOKEN={token}"
    if re.search(r"^YOUTUBE_REFRESH_TOKEN=.*$", text, re.M):
        text = re.sub(r"^YOUTUBE_REFRESH_TOKEN=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        die("Missing deps. Run: pip install -r requirements.txt")

    client_id = need("YOUTUBE_CLIENT_ID")
    client_secret = need("YOUTUBE_CLIENT_SECRET")

    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=SCOPES,
    )

    account = os.environ.get("YOUTUBE_ACCOUNT", "").strip()

    print("\nA browser window is opening.")
    if account:
        print(f"  1. Sign in as  {account}  (it should already be selected)")
    else:
        print("  1. Pick the Google account that owns your FINANCE channel")
    print("  2. If it asks which channel, pick the finance one — NOT the Hindi one")
    print("  3. Google will warn the app is unverified: Advanced -> Go to (unsafe)\n")

    # login_hint pre-selects the account, so the wrong one is not one careless
    # click away. prompt=consent forces a refresh token even if this account has
    # authorised the app before.
    extra = {"login_hint": account} if account else {}
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", **extra)

    if not creds.refresh_token:
        die("Google returned no refresh token. Revoke this app's access at "
            "https://myaccount.google.com/permissions and run this again.")

    yt = build("youtube", "v3", credentials=creds)
    resp = yt.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        die("That account has no YouTube channel. Create the finance channel first, "
            "then run this again.")

    title = items[0]["snippet"]["title"]
    channel_id = items[0]["id"]

    print("\n" + "=" * 58)
    print(f"  Authorised channel : {title}")
    print(f"  Channel ID         : {channel_id}")
    print(f"  https://www.youtube.com/channel/{channel_id}")
    if account:
        print(f"  Expected account   : {account}")
    print("=" * 58)

    answer = input("\nIs this your FINANCE channel? Uploads will go here. [y/N] ").strip().lower()
    if answer != "y":
        die("Nothing written. Re-run and pick the finance channel.")

    _write_env(creds.refresh_token)
    ok("wrote YOUTUBE_REFRESH_TOKEN to .env")
    print(f"\nUploads will now go to: {title}")
    print("Still opt-in — publish.py only runs when you ask, and uploads as private.")


if __name__ == "__main__":
    sys.exit(main())
