"""One-shot: set the most recent channel upload to public."""
import os, sys

def need(key):
    v = os.environ.get(key)
    if not v:
        print(f"[FAIL] Missing env var {key}", file=sys.stderr)
        sys.exit(1)
    return v

def main():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        None,
        refresh_token=need("YOUTUBE_REFRESH_TOKEN"),
        client_id=need("YOUTUBE_CLIENT_ID"),
        client_secret=need("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt = build("youtube", "v3", credentials=creds)

    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    items = ch.get("items") or []
    if not items:
        print("[FAIL] No channel found for this OAuth token.", file=sys.stderr)
        sys.exit(1)
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    pl = yt.playlistItems().list(
        part="snippet,contentDetails", playlistId=uploads, maxResults=5
    ).execute()
    vids = pl.get("items") or []
    if not vids:
        print("[FAIL] No uploaded videos found.", file=sys.stderr)
        sys.exit(1)

    video_id = vids[0]["contentDetails"]["videoId"]
    title = vids[0]["snippet"]["title"]
    print(f"[info] Latest video: {title} ({video_id})")

    meta = yt.videos().list(part="status,snippet", id=video_id).execute()
    st = (meta.get("items") or [{}])[0].get("status", {})
    print(f"[info] Current privacy: {st.get('privacyStatus')}")

    if st.get("privacyStatus") == "public":
        print("[OK] Already public — nothing to do.")
        return

    yt.videos().update(
        part="status",
        body={
            "id": video_id,
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        },
    ).execute()
    print(f"[OK] Set PUBLIC: https://youtu.be/{video_id}")
    print(f"Title: {title}")

if __name__ == "__main__":
    main()
