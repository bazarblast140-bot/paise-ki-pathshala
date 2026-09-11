"""Push the channel's identity from config.json to YouTube.

    python channel_profile.py            # show what would change, change nothing
    python channel_profile.py --apply    # write it

Sets the About description, search keywords, country and default language, and
uploads a banner rendered in the same hand-drawn style as the intro.

Two things the YouTube API simply cannot do, no matter the scope — you have to
do them once by hand in Studio:
  - the profile picture (avatar)
  - the @handle and the About-page links
Both are printed as a reminder at the end.

Needs the full youtube scope, so re-run `python youtube_auth.py` if the token
was minted before this script existed.
"""
import sys

from common import ROOT, cfg, need, ok, die

ASSETS = ROOT / "assets"


def yt_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        None,
        refresh_token=need("YOUTUBE_REFRESH_TOKEN"),
        client_id=need("YOUTUBE_CLIENT_ID"),
        client_secret=need("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def keywords(c):
    """YouTube stores channel keywords as one space-separated string, so any
    multi-word keyword has to be quoted or it silently becomes several."""
    out = []
    for t in c["channel"].get("tags", []):
        out.append(f'"{t}"' if " " in t else t)
    return " ".join(out)[:500]


def description(c):
    d = c["channel"].get("description")
    if isinstance(d, list):
        d = "\n\n".join(x.strip() for x in d if x.strip())
    return (d or "").strip()[:1000]


def build_banner(c):
    import branding
    ASSETS.mkdir(exist_ok=True)
    path = ASSETS / "banner.jpg"
    if branding.banner(c, path) is None:
        return None
    return path


def main():
    apply = "--apply" in sys.argv
    c = cfg()
    yt = yt_client()

    ch = yt.channels().list(part="snippet,brandingSettings", mine=True).execute()["items"][0]
    current = ch.get("brandingSettings", {}).get("channel", {}) or {}
    want_desc, want_kw = description(c), keywords(c)
    want_country = c["channel"].get("country") or None
    want_lang = c["channel"].get("default_language") or None

    print(f"\nChannel: {ch['snippet']['title']}  ({ch['snippet'].get('customUrl', 'no handle')})\n")
    for label, now, new in [
        ("description", (current.get("description") or "")[:60], want_desc[:60]),
        ("keywords", (current.get("keywords") or "")[:60], want_kw[:60]),
        ("country", current.get("country"), want_country),
        ("language", current.get("defaultLanguage"), want_lang),
    ]:
        mark = " " if str(now or "") == str(new or "") else "*"
        print(f" {mark} {label:<12} {now or '(empty)'!s:<62} ->  {new or '(unset)'}")

    banner_path = build_banner(c)
    print(f" * banner       {'(rebuilt) ' + str(banner_path.name) if banner_path else '(skipped)'}")

    if not apply:
        print("\nDry run. Nothing sent. Re-run with --apply to write these to YouTube.")
        return

    # Banner first: channels.update needs the URL the upload hands back.
    banner_url = None
    if banner_path:
        from googleapiclient.http import MediaFileUpload
        resp = yt.channelBanners().insert(
            media_body=MediaFileUpload(str(banner_path), resumable=False)).execute()
        banner_url = resp["url"]
        ok("banner uploaded")

    # Send back the whole channel block, not just the changed keys — this is a
    # replace, and omitting a field YouTube already has would clear it.
    channel_block = dict(current)
    channel_block["description"] = want_desc
    channel_block["keywords"] = want_kw
    if want_country:
        channel_block["country"] = want_country
    if want_lang:
        channel_block["defaultLanguage"] = want_lang

    body = {"id": ch["id"], "brandingSettings": {"channel": channel_block}}
    if banner_url:
        body["brandingSettings"]["image"] = {"bannerExternalUrl": banner_url}

    from googleapiclient.errors import HttpError
    try:
        yt.channels().update(part="brandingSettings", body=body).execute()
    except HttpError as e:
        die(f"YouTube rejected the update ({e.resp.status}). If this says "
            f"insufficient permissions, re-run `python youtube_auth.py` — the "
            f"stored token predates the full youtube scope.\n{e}")

    ok("channel profile updated")
    print("\nStill manual in YouTube Studio (no API exists for these):")
    print("  - profile picture   Customisation -> Branding -> Picture")
    print(f"  - handle            currently {ch['snippet'].get('customUrl')} — "
          f"Customisation -> Basic info")
    print("  - About links       Customisation -> Basic info -> Links")


if __name__ == "__main__":
    main()
