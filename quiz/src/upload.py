"""YouTube pe upload. Refresh-token auth — GitHub Actions me interactive login
possible nahi hai, isliye token pehle ek baar local machine pe banao
(neeche `--auth` wala mode).

Usage:
    python src/upload.py --auth                      # ek baar, laptop pe
    python src/upload.py --dir out/20260825_morning  # CI me
    python src/upload.py --dir ... --privacy private # test upload

Quota warning: ek upload ~1600 quota units kharch karta hai. Default daily
quota 10,000 hai = din me ~6 upload. Tumhein 5/din chahiye, so headroom hai —
par agar script retry loop me phans gayi to quota ek hi din me khatam.
Isliye retry max 2 rakha hai.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as C

IST = timezone(timedelta(hours=5, minutes=30))


def resolve_publish_at(slot: str):
    """Slot ke exact IST time ka RFC3339 (UTC) lautata hai.

    Kyun: GitHub ka cron on-time nahi hai — runner load pe runs 10-20 ghante
    late tak chal jaate hain. Isliye hum cron ko target se ~20h PEHLE chalate
    hain aur video ko 'private + publishAt' ke saath upload karte hain.
    Uske baad publish YouTube ke haath me hai — video hamesha exact clock
    time pe live hoti hai.

    Agar run itna late chala ki AAJ ka target nikal gaya, to KAL usi time
    pe schedule karte hain (pehle turant-public kar dete the jo galat time
    pe live ho jaata tha).
    """
    label = C.SLOTS.get(slot, "")            # e.g. "20:30 IST"
    if not label:
        print(f"[upload] WARN: unknown slot {slot!r} — publishAt skip, turant privacy use hogi")
        return None
    try:
        hh, mm = label.split()[0].split(":")
        h, m = int(hh), int(mm)
    except Exception as e:
        print(f"[upload] WARN: slot time parse fail ({label!r}): {e}")
        return None
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    target = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
    # 5 min buffer — build+upload ke beech thoda time lag sakta hai
    if target <= now_ist + timedelta(minutes=5):
        target = target + timedelta(days=1)
        print(
            f"[upload] {slot}: aaj ka time ({label}) nikal chuka "
            f"(ab {now_ist.strftime('%H:%M IST')}) — "
            f"KAL {label} pe schedule "
            f"({target.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})"
        )
    else:
        print(
            f"[upload] {slot}: YouTube {label} par khud public karega "
            f"({target.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}) "
            f"— abhi {now_ist.strftime('%H:%M IST')}"
        )
    return target.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# IMPORTANT: ye list repo ke youtube_auth.py se EXACT match honi chahiye.
# Refresh ke waqt aisa scope maanga jo grant nahi hua tha, to Google
# invalid_scope/403 deta hai — video ban jaata hai par upload fail hota hai.
# force-ssl yahan JAAN-BOOJH KE nahi hai: wo kabhi grant nahi hua tha.
# commentThreads.insert full "youtube" scope se bhi chal jaata hai.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]


def _creds():
    from google.oauth2.credentials import Credentials
    cid = os.environ["YT_CLIENT_ID"]
    csec = os.environ["YT_CLIENT_SECRET"]
    rtok = os.environ["YT_REFRESH_TOKEN"]
    # scopes JAAN-BOOJH KE nahi bhej rahe. Refresh ke waqt scope param bhejne se
    # Google use granted set se match karta hai; zara sa farq bhi ho to
    # invalid_scope aata hai. Bina param ke wo jo granted hai wahi de deta hai.
    return Credentials(
        None, refresh_token=rtok, client_id=cid, client_secret=csec,
        token_uri="https://oauth2.googleapis.com/token",
    )


def do_auth():
    """Local machine pe chalao. client_secret.json Google Cloud Console se
    download karo (OAuth client type: Desktop app)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)
    print("\n=== Ye teen values GitHub repo Secrets me daalo ===")
    print("YT_CLIENT_ID     =", creds.client_id)
    print("YT_CLIENT_SECRET =", creds.client_secret)
    print("YT_REFRESH_TOKEN =", creds.refresh_token)


def upload(meta_path: Path, privacy="public", publish_at=None):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    video = Path(meta["video_file"])
    if not video.exists():
        video = meta_path.parent / video.name
    assert video.exists(), f"MP4 nahi mila: {video}"

    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", True)),
    }
    if publish_at:
        # YouTube: private + publishAt = exact time pe public
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at       # RFC3339, e.g. 2026-08-26T02:00:00Z
        print(f"[upload] privacy=private + publishAt={publish_at}")
    else:
        print(f"[upload] privacy={privacy} (no schedule — turant)")

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": meta.get("categoryId", "27"),
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": status,
    }

    creds = _creds()
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    # Galat channel pe upload sabse mehnga error hai — pehle naam padh lo.
    try:
        ch = yt.channels().list(part="snippet", mine=True).execute()
        if ch.get("items"):
            print(f"[upload] channel: {ch['items'][0]['snippet']['title']}")
    except Exception as e:
        print(f"[upload] channel naam nahi padh paaya (upload phir bhi try karenge): {e}")
        ch = {}
    # Galat channel par upload kabhi nahi: token kisi aur channel ka ho, channel
    # padha hi na ja sake, ya expected ID set na ho — to yahin ruk jao.
    expected = os.environ.get("EXPECTED_CHANNEL_ID", "").strip()
    got = ((ch or {}).get("items") or [{}])[0].get("id")
    if not expected or got != expected:
        raise SystemExit(f"[upload] STOP: token channel {got} != EXPECTED_CHANNEL_ID {expected or '(unset)'}")
    media = MediaFileUpload(str(video), chunksize=4 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp, tries = None, 0
    while resp is None:
        try:
            prog, resp = req.next_chunk()
            if prog:
                print(f"[upload] {int(prog.progress() * 100)}%")
        except HttpError as e:
            tries += 1
            if tries > 2 or e.resp.status not in (500, 502, 503, 504):
                raise
            print(f"[upload] retry {tries} after {e.resp.status}")
            time.sleep(5 * tries)

    vid = resp["id"]
    print(f"[upload] done -> https://youtube.com/shorts/{vid}")

    pinned = meta.get("pinned_comment")
    if pinned and not meta.get("made_for_kids"):
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": vid,
                "topLevelComment": {"snippet": {"textOriginal": pinned}},
            }}).execute()
            print("[upload] pinned comment posted (pin manually — API pin nahi karta)")
        except Exception as e:
            print(f"[upload] comment skip: {e}")

    meta["youtube_id"] = vid
    meta["youtube_url"] = f"https://youtube.com/shorts/{vid}"
    meta["scheduled_for"] = publish_at or f"immediate ({privacy})"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return vid


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--privacy", default="public",
                    choices=["public", "private", "unlisted"])
    ap.add_argument("--publish-at", default=None,
                    help="RFC3339 UTC, e.g. 2026-08-28T15:00:00Z")
    ap.add_argument("--publish-slot", default=None,
                    help="slot ka naam — uske config.SLOTS wale IST time pe "
                         "YouTube khud public karega (GitHub cron late ho tab bhi)")
    a = ap.parse_args()
    if a.auth:
        do_auth()
    elif a.dir:
        pub_at = a.publish_at
        # publishAt sirf tab jab video public hone wali ho. unlisted/private
        # test uploads pe scheduling ka koi matlab nahi.
        if not pub_at and a.publish_slot and a.privacy == "public":
            pub_at = resolve_publish_at(a.publish_slot)
        try:
            upload(a.dir / "metadata.json", a.privacy, pub_at)
        except Exception as e:
            # Poora error stdout pe — CI logs blob storage pe hote hain, isliye
            # error ko file me bhi likhte hain jise workflow repo me commit karta hai.
            import traceback
            detail = traceback.format_exc()
            print(detail)
            body = getattr(getattr(e, "resp", None), "status", None)
            print(f"[upload] FAILED (http={body}): {e}")
            try:
                (a.dir / "upload_error.txt").write_text(detail, encoding="utf-8")
            except Exception:
                pass
            sys.exit(1)
    else:
        ap.error("--auth ya --dir me se ek do")
