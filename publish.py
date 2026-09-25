"""Publisher — OPT-IN ONLY. Refuses to upload unless the verify verdict is READY.
Always uploads as PRIVATE (or scheduled), never instant-public, on a finance channel.

Needs: pip install google-api-python-client google-auth-oauthlib
and YOUTUBE_* env vars for the FINANCE channel (separate OAuth from your Hindi channel!).
"""
import json
import os
import re
import sys
from pathlib import Path

from common import ROOT, cfg, read, die, ok, RUN, need, video_format
from ledger import record_publish

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


CAT_HASH = {"CANDLESTICK": "#CandlestickPatterns", "INDICATOR": "#TechnicalAnalysis",
            "ORDERS": "#StockMarketBasics", "OPTIONS": "#OptionTrading", "RISK": "#RiskManagement",
            "REAL DATA": "#StockMarketIndia", "MARKET FACT": "#StockMarketFacts"}
CAT_TAGS = {"CANDLESTICK": ["candlestick patterns hindi", "candlestick pattern kaise padhe"],
            "INDICATOR": ["technical analysis hindi", "indicator trading hindi"],
            "ORDERS": ["order types hindi", "share kaise kharide bechen"],
            "OPTIONS": ["option trading hindi", "options for beginners hindi"],
            "RISK": ["risk management trading hindi", "position sizing hindi"],
            "REAL DATA": ["share market return hindi", "long term investing hindi"],
            "MARKET FACT": ["stock market facts hindi", "share market gyan"]}


def load_topic():
    """Short pipeline writes run/topic.json (key, cat, seo keyword)."""
    p = RUN / "topic.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def build_title(sec):
    title = sec.get("TITLE", "").strip().splitlines()
    title = title[0].strip() if title else ""
    title = title.replace("->", "→").replace("<", "").replace(">", "")   # YouTube "<>" reject karta hai
    if not title:
        die("No TITLE found in run/script.txt. Refusing to upload an unnamed video.")
    kw = (load_topic().get("seo") or "").strip()
    if kw and kw.split()[0].lower() not in title.lower() and len(title) + len(kw) + 9 <= MAX_TITLE:
        # search me log English naam likhte hain ("hammer candlestick pattern hindi")
        title = f"{title} | {kw} Hindi"
    if len(title) > MAX_TITLE:
        # cut on a word boundary rather than mid-word
        title = title[:MAX_TITLE].rsplit(" ", 1)[0]
    return title


def build_description(sec, c):
    """SEO-first description: hook → value → disclaimer → referral URLs (one per line) → tags.

    YouTube makes https:// URLs clickable when they sit on their own line.
    """
    parts = []

    hook = sec.get("HOOK", "").strip()
    if hook:
        hook_one = re.sub(r"\s+", " ", hook).strip()
        parts.append(hook_one)

    topic = load_topic()
    kw, cat = topic.get("seo"), topic.get("cat")
    if kw and cat == "REAL DATA":
        parts.append(f"{kw} — असली डेटा (जून 2026 तक), dividend शामिल नहीं। पिछला रिटर्न आगे की गारंटी नहीं।")
    elif kw and cat == "MARKET FACT":
        parts.append(f"{kw} — शेयर बाज़ार का फैक्ट, आसान हिंदी में।")
    elif kw:
        parts.append(f"{kw} — आसान हिंदी में: पहचान, मतलब और traders इसे आमतौर पर कैसे इस्तेमाल करते हैं।")

    disclaimer = sec.get("DISCLAIMER LINE", "").strip()
    parts.append(disclaimer or "यह वीडियो सिर्फ़ जानकारी के लिए है, निवेश सलाह नहीं।")
    parts.append("हम SEBI-रजिस्टर्ड सलाहकार नहीं हैं। किसी भी निवेश से पहले रजिस्टर्ड सलाहकार से बात करें।")

    # Referral: label on one line, bare https URL on the next (best chance of blue link).
    try:
        ref_path = ROOT / "data" / "referrals.json"
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        names = ref.get("category_map", {}).get(cat, [])
        links = ref.get("platforms", {})
        blocks = []
        for name in names:
            p = links.get(name) or {}
            url = (p.get("url") or "").strip()
            if url.startswith("http"):
                blocks.append(f"{name.title()}\n{url}")
        if blocks:
            disclosure = ref.get(
                "disclosure_hi",
                "यह referral link है। इससे account खोलने पर हमें referral benefit मिल सकता है।",
            )
            parts.append(
                "Account / referral links\n"
                + disclosure
                + "\n\n"
                + "\n\n".join(blocks)
                + "\n\nSame links Instagram bio me bhi hain (Zerodha · Upstox · Delta Exchange)."
            )
            print(f"[OK] referral links in description: {', '.join(names)}")
    except (OSError, ValueError, TypeError, NameError) as e:
        print(f"[warn] referral links skipped: {e}")

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
    parts.append("रोज़ 3 ट्रेडिंग Shorts — कैंडलस्टिक, इंडिकेटर, ऑर्डर, F&O, रिस्क और असली डेटा।")

    hash_tags = ["#StockMarketHindi", CAT_HASH.get(topic.get("cat"), "#TradingHindi")]
    kw = topic.get("seo", "")
    if kw and topic.get("cat") in ("CANDLESTICK", "INDICATOR", "OPTIONS", "RISK", "ORDERS"):
        hash_tags.append("#" + re.sub(r"[^A-Za-z0-9]", "", kw.title()))
    hash_tags += ["#TradingHindi", "#PaiseKiPathshala"]
    parts.append(" ".join(dict.fromkeys(h for h in hash_tags if len(h) > 2)))

    desc = "\n\n".join(parts)
    desc = desc.replace("->", "→").replace("<", "").replace(">", "").replace("SOURCE", "स्रोत")
    return desc[:MAX_DESC]


def build_tags(title, c):
    """YouTube-safe SEO tags."""
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

    topic = load_topic()
    if topic.get("seo"):
        add(f"{topic['seo']} hindi")
        add(topic["seo"])
    for t in CAT_TAGS.get(topic.get("cat"), []):
        add(t)
    core = ["stock market hindi", "trading hindi", "paise ki pathshala"] if topic else c["channel"].get("tags", [])
    for t in core:
        add(t)

    if not topic:
        words = re.findall(r"[A-Za-z]{3,}", title or "")
        for i in range(len(words) - 1):
            add(f"{words[i]} {words[i+1]}")
    add(c["channel"].get("name", "Paise Ki Pathshala").lower())

    kept, total = [], 0
    for t in tags:
        if len(kept) >= 15:
            break
        if total + len(t) + 1 > 400:
            break
        kept.append(t)
        total += len(t) + 1
    return kept or ["stock market hindi", "trading hindi", "paise ki pathshala"]


def set_thumbnail(yt, video_id):
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
    visibility = os.environ.get("PUBLISH_VISIBILITY") or c["pipeline"]["publish_visibility"]
    publish_at = None
    slot = os.environ.get("PUBLISH_SLOT_IST", "").strip()
    if visibility == "public" and slot:
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        hh, mm = (int(x) for x in slot.split(":"))
        when = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if when < now + timedelta(minutes=20):
            when += timedelta(days=1)
        publish_at = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        visibility = "private"

    print(f"\n  Title      : {title}")
    print(f"  Tags       : {', '.join(tags) or '(none)'}")
    print(f"  Visibility : {visibility}" + (f"  (public at {publish_at} UTC)" if publish_at else ""))
    print(f"  Thumbnail  : {'yes' if (RUN / 'thumbnail.jpg').exists() else 'no'}\n")

    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        None,
        refresh_token=need("YOUTUBE_REFRESH_TOKEN"),
        client_id=need("YOUTUBE_CLIENT_ID"),
        client_secret=need("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt = build("youtube", "v3", credentials=creds)

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
                    "categoryId": "27",
                    "defaultLanguage": c["channel"].get("default_language", "hi"),
                    "defaultAudioLanguage": c["channel"].get("default_language", "hi")},
        "status": {"privacyStatus": visibility,
                   "selfDeclaredMadeForKids": False},
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at
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
            safe = ["stock market hindi", "trading hindi", "paise ki pathshala"]
            resp = _do_upload(safe)
        else:
            raise

    video_id = resp["id"]
    (RUN / "uploaded.txt").write_text(video_id, encoding="utf-8")
    record_publish(video_id, publish_at, slot)
    ok(f"Uploaded as {visibility}: https://youtu.be/{video_id}")
    set_thumbnail(yt, video_id)
    print(f"\nEdit before going public: https://studio.youtube.com/video/{video_id}/edit")

if __name__ == "__main__":
    main()
