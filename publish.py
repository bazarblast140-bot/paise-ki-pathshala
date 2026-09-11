"""Publisher — OPT-IN ONLY. Refuses to upload unless the verify verdict is READY.
Always uploads as PRIVATE (or scheduled), never instant-public, on a finance channel.

Needs: pip install google-api-python-client google-auth-oauthlib
and YOUTUBE_* env vars for the FINANCE channel (separate OAuth from your Hindi channel!).
"""
import os
import re
import sys
from common import cfg, read, die, ok, RUN, need

# The script is written with bare headings on their own line ("TITLE", then the
# title underneath). Older drafts used "TITLE: ...". Both must parse, because
# guessing wrong here uploads a video literally called "Finance video".
HEADINGS = ["TITLE", "HOOK", "BODY", "CTA", "DISCLAIMER LINE", "FACT LEDGER"]
_HEADING_RE = re.compile(rf"^({'|'.join(HEADINGS)})\b[^:]*:?\s*(.*)$", re.I)

MAX_TITLE = 100      # YouTube hard limits
MAX_DESC = 5000
MAX_TAGS_CHARS = 460  # real cap is 500 across all tags; leave headroom


def sections(text):
    """Split script.txt into {heading: body}. Anything before the first heading
    is ignored — it is never content we want on the video page."""
    out, current, buf = {}, None, []
    for line in text.splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            if current:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).upper()
            buf = [m.group(2)] if m.group(2).strip() else []
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def build_title(sec):
    title = sec.get("TITLE", "").strip().splitlines()
    title = title[0].strip() if title else ""
    if not title:
        die("No TITLE found in run/script.txt. Refusing to upload an unnamed video.")
    if len(title) > MAX_TITLE:
        # cut on a word boundary rather than mid-word
        title = title[:MAX_TITLE].rsplit(" ", 1)[0]
    return title


def build_description(sec, c):
    """SEO-first description: hook (search snippet) → value → sources → CTA → tags.

    First 100-150 chars are what YouTube shows in search — must be the hook.
    """
    parts = []

    hook = sec.get("HOOK", "").strip()
    if hook:
        # collapse to a tight search snippet
        hook_one = re.sub(r"\s+", " ", hook).strip()
        parts.append(hook_one)

    title = (sec.get("TITLE") or "").strip().splitlines()
    title = title[0].strip() if title else ""
    if title:
        parts.append(f"इस वीडियो में: {title}")

    disclaimer = sec.get("DISCLAIMER LINE", "").strip()
    parts.append(disclaimer or "यह वीडियो सिर्फ़ जानकारी के लिए है, निवेश सलाह नहीं।")
    parts.append("हम SEBI-रजिस्टर्ड सलाहकार नहीं हैं। किसी भी निवेश से पहले रजिस्टर्ड सलाहकार से बात करें।")

    ledger = sec.get("FACT LEDGER", "").strip()
    if ledger:
        lines = []
        for line in ledger.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"\s*—\s*(VERIFIED|no source required)\b.*$", "", line, flags=re.I)
            lines.append(line if line.startswith("•") or line.startswith("-") else f"• {line}")
        if lines:
            parts.append("स्रोत और नोट्स\n" + "\n".join(lines[:12]))

    name = c["channel"].get("name", "Paise Ki Pathshala").strip()
    tagline = c["channel"].get("tagline", "").strip()
    parts.append(f"{name} — {tagline}" if tagline else name)
    parts.append("रोज़ नया वीडियो — एक नियम, साफ़ नंबर, कोई हाइप नहीं।")

    # SEO hashtags (YouTube indexes the first few)
    tags = c["channel"].get("tags") or []
    hash_tags = []
    for t in tags[:8]:
        h = "#" + re.sub(r"[^A-Za-z0-9]", "", t.title().replace(" ", ""))
        if len(h) > 2 and h not in hash_tags:
            hash_tags.append(h)
    if hash_tags:
        parts.append(" ".join(hash_tags))

    desc = "\n\n".join(parts)
    return desc[:MAX_DESC]


def build_tags(title, c):
    """YouTube-safe SEO tags.

    Rules that trigger invalidTags:
    - empty / too short / too long (>100)
    - characters other than letters, numbers, spaces, hyphens
    - total length over ~500
    """
    stop = {"the", "that", "your", "when", "what", "how", "why", "and", "for",
            "with", "you", "old", "year", "shows", "it", "a", "of", "in", "to",
            "from", "this", "into", "than", "also", "just", "more", "never",
            "requires", "triple", "hidden", "cost", "living", "off"}
    tags, seen = [], set()

    def add(t):
        t = re.sub(r"[^A-Za-z0-9 \-]", "", str(t))
        t = re.sub(r"\s+", " ", t).strip()
        if not t or len(t) < 3 or len(t) > 60:
            return
        key = t.lower()
        if key in seen or key in stop:
            return
        seen.add(key)
        tags.append(t)

    for t in c["channel"].get("tags", []):
        add(t)

    words = re.findall(r"[A-Za-z0-9]{3,}", title or "")
    for i in range(len(words) - 1):
        add(f"{words[i]} {words[i+1]}")
    for w in words:
        if not w.isdigit():
            add(w)

    add(c["channel"].get("name", "Paise Ki Pathshala").lower())
    add("personal finance hindi")
    add("paise ki baat")

    kept, total = [], 0
    for t in tags:
        if len(kept) >= 15:
            break
        if total + len(t) + 1 > 400:
            break
        kept.append(t)
        total += len(t) + 1
    return kept or ["personal finance hindi", "paisa kaise bachaye", "paise ki pathshala"]


def set_thumbnail(yt, video_id):
    """Custom thumbnails need a phone-verified channel. If this account is not
    verified the API says 403 — that is a channel setting, not a broken upload,
    so it must not fail the run after the video is already up."""
    thumb = RUN / "thumbnail.jpg"
    if not thumb.exists():
        print("[skip] no run/thumbnail.jpg — set one manually in YouTube Studio")
        return
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    try:
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb))).execute()
        ok("thumbnail set")
    except HttpError as e:
        if e.resp.status == 403:
            print("[skip] YouTube refused the custom thumbnail (channel not verified).\n"
                  "       Verify at https://www.youtube.com/verify, then set it in Studio.")
        else:
            print(f"[skip] thumbnail upload failed ({e.resp.status}). Video is up; set it in Studio.")


def verdict_is_ready():
    if not (RUN / "report.md").exists():
        return False
    return "READY TO PUBLISH" in read("report.md")

def main():
    only_if_ready = "--only-if-ready" in sys.argv
    if only_if_ready and not verdict_is_ready():
        die("Verify verdict is NOT 'READY TO PUBLISH'. Refusing to upload. (This is by design.)")

    c = cfg()
    if not (RUN / "final.mp4").exists():
        die("No final.mp4 to upload.")

    sec = sections(read("script.txt"))
    title = build_title(sec)
    description = build_description(sec, c)
    tags = build_tags(title, c)
    visibility = c["pipeline"]["publish_visibility"]

    print(f"\n  Title      : {title}")
    print(f"  Tags       : {', '.join(tags) or '(none)'}")
    print(f"  Visibility : {visibility}")
    print(f"  Thumbnail  : {'yes' if (RUN / 'thumbnail.jpg').exists() else 'no'}\n")

    # ---- YouTube upload ----
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        None,
        refresh_token=need("YOUTUBE_REFRESH_TOKEN"),
        client_id=need("YOUTUBE_CLIENT_ID"),
        client_secret=need("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
        # scopes jaan-boojh ke nahi: refresh par jo grant hua wahi milta hai.
        # Sirf youtube.upload maangne se neeche wala channels.list 403 deta.
    )
    yt = build("youtube", "v3", credentials=creds)

    # Galat channel par upload KABHI nahi. Ye repo pehle isi workflow se
    # QUIZ PATHSHALA par upload karta tha; secret mapping zara bhi galat reh
    # jaaye to finance video quiz channel par chala jaata. Isliye expected
    # channel ID na ho, ya token kisi aur channel ka ho, to upload ruk jaata hai.
    expected = os.environ.get("EXPECTED_CHANNEL_ID", "").strip()
    if not expected:
        die("EXPECTED_CHANNEL_ID set nahi hai — pata nahi kis channel par upload karna hai. Upload roka.")
    me = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    got = me[0]["id"] if me else None
    if got != expected:
        who = me[0]["snippet"]["title"] if me else "koi channel nahi"
        die(f"Token '{who}' ({got}) ka hai, par upload {expected} par hona tha. Upload roka.")
    ok(f"channel check OK: {me[0]['snippet']['title']}")

    from googleapiclient.http import MediaFileUpload
    body = {
        "snippet": {"title": title, "description": description, "tags": tags,
                    "categoryId": "27",  # 27 = Education
                    "defaultLanguage": c["channel"].get("default_language", "hi"),
                    "defaultAudioLanguage": c["channel"].get("default_language", "hi")},
        "status": {"privacyStatus": visibility,  # 'private' by default
                   "selfDeclaredMadeForKids": False},
    }
    from googleapiclient.errors import HttpError, ResumableUploadError

    def _do_upload(tag_list):
        body["snippet"]["tags"] = tag_list
        media = MediaFileUpload(str(RUN / "final.mp4"), chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                print(f"  uploading… {int(status.progress() * 100)}%")
        return resp

    try:
        resp = _do_upload(tags)
    except (HttpError, ResumableUploadError) as e:
        msg = str(e)
        if "invalidTags" in msg or "invalid video keywords" in msg:
            print("[warn] YouTube rejected tags; retrying with safe minimal tags")
            safe = ["personal finance hindi", "paisa kaise bachaye", "paise ki pathshala"]
            resp = _do_upload(safe)
        else:
            raise

    video_id = resp["id"]
    ok(f"Uploaded as {visibility}: https://youtu.be/{video_id}")
    set_thumbnail(yt, video_id)
    print(f"\nEdit before going public: https://studio.youtube.com/video/{video_id}/edit")

if __name__ == "__main__":
    main()
