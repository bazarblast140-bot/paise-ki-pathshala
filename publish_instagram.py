"""Cross-post the short (9:16) video to Instagram as a Reel.

Same final.mp4 that just went to YouTube Shorts — no re-render, no HeyGen,
no extra TTS. Cost is only hosting + Graph API (free).

Opt-in:
  IG_CROSSPOST=1
  IG_USER_ID=...
  IG_ACCESS_TOKEN=...
  IG_SURFACE=facebook   # or instagram

Hosting (public HTTPS URL for Instagram to fetch):
  GITHUB_TOKEN + GITHUB_REPOSITORY  (Actions default), or
  MEDIA_REPO + MEDIA_REPO_TOKEN     (separate public bucket repo)

Only runs for short format. Long (16:9) is skipped.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import RUN, cfg, ok, video_format

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
IG_VERSION = os.environ.get("IG_API_VERSION", "v23.0")


def die(msg: str) -> None:
    print(f"[ig] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def skip(msg: str) -> None:
    print(f"[ig] skip — {msg}")
    sys.exit(0)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def http_json(url: str, *, method: str = "GET", data: bytes | None = None,
              headers: dict | None = None, form: dict | None = None) -> dict:
    hdrs = {"User-Agent": "paise-ki-pathshala-ig-crosspost", **(headers or {})}
    body = data
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw = res.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e


import urllib.parse  # after http_json uses it


def host_release_asset(file: Path, *, name: str, content_type: str) -> str:
    """Upload a public release asset so Meta can fetch media without auth."""
    repo = env("MEDIA_REPO") or env("GITHUB_REPOSITORY")
    token = env("MEDIA_REPO_TOKEN") if env("MEDIA_REPO") else (env("GITHUB_TOKEN") or env("MEDIA_REPO_TOKEN"))
    if not repo or not token:
        die("Need GitHub media hosting credentials")
    tag = f"ig-reel-{time.strftime('%Y-%m-%d')}"
    try:
        release = http_json(
            f"{API}/repos/{repo}/releases/tags/{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    except RuntimeError as e:
        if "404" not in str(e):
            raise
        release = http_json(
            f"{API}/repos/{repo}/releases",
            method="POST",
            data=json.dumps({"tag_name": tag, "name": tag, "body": "Paise Ki Pathshala short — IG Reel media"}).encode(),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
        )
    for asset in release.get("assets") or []:
        if asset.get("name") == name:
            try:
                http_json(
                    f"{API}/repos/{repo}/releases/assets/{asset['id']}",
                    method="DELETE",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                )
            except RuntimeError:
                pass
    asset = http_json(
        f"{UPLOADS}/repos/{repo}/releases/{release['id']}/assets?name={urllib.parse.quote(name)}",
        method="POST",
        data=file.read_bytes(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": content_type, "Content-Length": str(file.stat().st_size)},
    )
    url = asset.get("browser_download_url") or ""
    if not url.startswith("https://"):
        die(f"No public download URL from release asset: {asset}")
    for attempt in range(1, 9):
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ig-host-check"})
            with urllib.request.urlopen(req, timeout=30) as res:
                if res.status == 200 and res.read(8):
                    ok(f"hosted: {url}")
                    return url
        except Exception as err:
            print(f"  host wait {attempt}/8: {err}")
            time.sleep(1.5)
    die(f"Public URL not serving yet: {url}")
    return url


def host_mp4(file: Path) -> str:
    return host_release_asset(file, name=f"paise-short-{int(time.time())}.mp4", content_type="video/mp4")


def host_cover(file: Path) -> str:
    return host_release_asset(file, name=f"paise-cover-{int(time.time())}.jpg", content_type="image/jpeg")

def ig_host() -> str:
    surface = env("IG_SURFACE", "facebook")
    if env("IG_API_HOST"):
        return env("IG_API_HOST")
    return {
        "facebook": "https://graph.facebook.com",
        "instagram": "https://graph.instagram.com",
    }.get(surface, "https://graph.facebook.com")


def ig_call(path: str, *, method: str = "GET", params: dict | None = None) -> dict:
    token = env("IG_ACCESS_TOKEN")
    base = f"{ig_host()}/{IG_VERSION}/{path}"
    params = {**(params or {}), "access_token": token}
    if method == "GET":
        url = base + "?" + urllib.parse.urlencode(params)
        return http_json(url)
    return http_json(base, method="POST", form=params)


def script_sections(script: str) -> dict:
    """script.txt ke 'TITLE:' / 'HOOK (0-3 sec):' / 'CTA:' / 'FACT LEDGER:' blocks."""
    out, cur, buf = {}, None, []
    head = re.compile(r"^(TITLE|HOOK|BODY|CTA|DISCLAIMER(?: LINE)?|FACT LEDGER)\b[^:]*:?\s*(.*)$", re.I)
    for line in script.splitlines():
        m = head.match(line.strip())
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur = m.group(1).upper().split()[0]
            buf = [m.group(2)] if m.group(2).strip() else []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


def load_topic() -> dict:
    try:
        return json.loads((RUN / "topic.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hashtags_for(topic: dict) -> list[str]:
    """Topic-specific hashtags (main's selection logic, unchanged)."""
    cat = str(topic.get("cat") or "")
    cat_tags = {
        "CANDLESTICK": ["#CandlestickPatterns", "#CandlestickTrading"],
        "INDICATOR": ["#TechnicalAnalysis", "#TradingIndicators"],
        "OPTIONS": ["#OptionTrading", "#OptionsTrading"],
        "RISK": ["#RiskManagement", "#TradingRisk"],
        "ORDERS": ["#StockMarketBasics", "#OrderTypes"],
        "REAL DATA": ["#StockMarketFacts", "#MarketData"],
        "MARKET FACT": ["#StockMarketFacts", "#FinanceFacts"],
        "FUNDAMENTAL": ["#FundamentalAnalysis", "#StockAnalysis"],
        "PENNY STOCK": ["#PennyStocks", "#LiquidityRisk"],
        "INTRADAY": ["#IntradayTrading", "#PriceAction"],
    }
    tags = ["#StockMarketHindi", "#TradingHindi", "#PaiseKiPathshala", "#ShareMarket"] + cat_tags.get(cat, [])
    if topic.get("seo"):
        words = re.findall(r"[A-Za-z0-9]+", str(topic["seo"]))
        t = "#" + "".join(w[:1].upper() + w[1:] for w in words)
        if 2 < len(t) <= 40:
            tags.insert(1, t)
    return list(dict.fromkeys(tags))


def build_caption() -> str:
    """Reel caption. The first ~125 characters show before "more" and Instagram
    search reads caption words, so the keyword title goes first, then the hook,
    2 save-worthy takeaways (FACT LEDGER), comment + save CTA, disclaimer, bio
    CTA (with referral disclosure), hashtags. No URLs (not tappable in captions)."""
    script = (RUN / "script.txt").read_text(encoding="utf-8") if (RUN / "script.txt").exists() else ""
    sec = script_sections(script)
    topic = load_topic()
    one = lambda s: re.sub(r"\s+", " ", s or "").strip()  # noqa: E731
    title = one((sec.get("TITLE") or "").splitlines()[0] if sec.get("TITLE") else "")
    title = title or one((topic.get("thumb") or [""])[0])
    hook = one(sec.get("HOOK"))
    parts = []
    if title:
        parts.append(title[:120])
    if hook and hook.lower() != title.lower():
        parts.append(hook[:300])

    facts = [one(re.sub(r"^[-•*]\s*", "", f)) for f in (sec.get("FACT") or "").splitlines() if f.strip()]
    facts = facts or [one(str(f)) for f in topic.get("facts") or []]
    facts = [f for f in facts if f and "सलाह नहीं" not in f and "advice" not in f.lower()][:2]
    if facts:
        parts.append("📌 याद रखो:\n" + "\n".join(f"• {f}" for f in facts))

    cta = one(sec.get("CTA"))
    ask = cta.split("—")[0].split("?")[0].strip() + "?" if "?" in cta else "अगला कौन सा topic समझना है?"
    parts.append(f"💬 {ask} Comment में लिखो।\n💾 Save करो — अगली बार काम आएगा।")
    parts.append("यह सिर्फ़ शिक्षा के लिए है — निवेश सलाह नहीं।")

    # Referral link only in bio (caption URLs are not clickable). ASCI influencer
    # guidelines: a commission link must be disclosed.
    try:
        ref = json.loads((Path(__file__).parent / "data" / "referrals.json").read_text(encoding="utf-8"))
        names = ref.get("category_map", {}).get(str(topic.get("cat") or ""), [])
        if names:
            disc = ref.get("disclosure_hi") or "यह referral link है।"
            parts.append(f"Demat / invest account chahiye ho to profile bio me link dekho. {disc}")
            print(f"[OK] IG bio CTA (topic refs: {', '.join(names)})")
    except (OSError, ValueError, TypeError):
        pass

    parts.append(" ".join(hashtags_for(topic))[:900])
    return "\n\n".join(parts)[:2200]


# ---------------------------------------------------------------- IG cover
def ig_settings() -> dict:
    return {"grid_safe_cover": True, **(cfg().get("instagram") or {})}


def grid_safe_cover(src: Path, out: Path) -> Path:
    """The profile grid crops a Reel cover to the centre 3:4 (1080x1440, y 240..1680).
    Our thumbnail puts the title at y~150, so it was cut off in the grid. Shrink the
    whole design into a centred 3:4 box over a blurred copy of itself. Still looks
    fine full-screen (9:16)."""
    from PIL import Image, ImageEnhance, ImageFilter
    im = Image.open(src).convert("RGB")
    W, H = 1080, 1920
    if im.size != (W, H):
        im = im.resize((W, H))
    box_h = 1440
    inner = im.resize((int(W * box_h / H), box_h), Image.LANCZOS)
    bg = im.filter(ImageFilter.GaussianBlur(48))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    bg.paste(inner, ((W - inner.width) // 2, (H - box_h) // 2))
    bg.save(out, quality=92)
    return out


def prepare_cover(thumb: Path | None) -> Path | None:
    """IG-only cover copy (YouTube thumbnail.jpg is not changed). On any error use
    the original thumbnail so a Reel is never blocked by this."""
    if thumb is None or not ig_settings().get("grid_safe_cover"):
        return thumb
    try:
        cover = grid_safe_cover(thumb, RUN / "ig_cover.jpg")
        print("  cover: grid-safe ig_cover.jpg")
        return cover
    except Exception as e:  # noqa: BLE001
        print(f"[warn] grid-safe cover skipped ({type(e).__name__}) — original thumbnail")
        return thumb


def publish_reel(video_url: str, caption: str, cover_url: str = "") -> str:
    ig_user = env("IG_USER_ID")
    if not ig_user or not env("IG_ACCESS_TOKEN"):
        die("IG_USER_ID and IG_ACCESS_TOKEN required")

    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
    }
    if cover_url:
        params["cover_url"] = cover_url
    try:
        container = ig_call(f"{ig_user}/media", method="POST", params=params)
    except RuntimeError as e:
        if cover_url and "cover" in str(e).lower():
            print("[warn] Instagram cover rejected; retrying Reel without cover")
            params.pop("cover_url", None)
            container = ig_call(f"{ig_user}/media", method="POST", params=params)
        else:
            raise

    container_id = container.get("id")
    if not container_id:
        die(f"No container id: {container}")
    print(f"  container: {container_id}")

    for i in range(60):
        st = ig_call(container_id, params={"fields": "status_code,status"})
        code = st.get("status_code")
        print(f"  processing: {code}")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            die(f"Instagram rejected video: {st}")
        time.sleep(5)
    else:
        die("Timed out waiting for Instagram processing")

    pub = ig_call(
        f"{ig_user}/media_publish",
        method="POST",
        params={"creation_id": container_id},
    )
    media_id = pub.get("id") or ""
    if not media_id:
        die(f"Publish failed: {pub}")
    return media_id


def main() -> None:
    if env("IG_CROSSPOST") not in ("1", "true", "yes", "on"):
        skip("IG_CROSSPOST not enabled (set IG_CROSSPOST=1 to post short as Reel)")

    if video_format() != "short":
        skip("only short (9:16) videos become Reels")

    mp4 = RUN / "final.mp4"
    if not mp4.exists():
        die("run/final.mp4 missing")

    # Same quality bar as YouTube: IG_CROSSPOST_FORCE only bypasses "YouTube
    # upload missing" (e.g. dead token), never a NOT-READY verify verdict.
    # run_verify.py exits 0 even on "NEEDS FIX", and publish.py --only-if-ready
    # then refuses YouTube, so without this check a failed-review video was
    # still posted as a public Reel.
    report = RUN / "report.md"
    if not report.exists() or "READY TO PUBLISH" not in report.read_text(encoding="utf-8"):
        skip("verify verdict is not READY TO PUBLISH — Reel not posted")

    if not (RUN / "uploaded.txt").exists() and env("IG_CROSSPOST_FORCE") not in ("1", "true"):
        skip("YouTube upload not recorded (uploaded.txt missing) — set IG_CROSSPOST_FORCE=1 to post anyway")

    print("\n=== Instagram Reel cross-post ===")
    thumb = RUN / "thumbnail.jpg"
    cover = prepare_cover(thumb if thumb.exists() else None)
    url = host_mp4(mp4)
    cover_url = ""
    if cover is not None and cover.exists():
        cover_url = host_cover(cover)
        print(f"  cover: {cover_url}")
    caption = build_caption()
    print(f"  caption ({len(caption)} chars): {caption[:80]}…")
    media_id = publish_reel(url, caption, cover_url)
    (RUN / "ig_reel.txt").write_text(media_id, encoding="utf-8")
    ok(f"IG Reel published: {media_id}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ig] error: {e}", file=sys.stderr)
        sys.exit(1)
