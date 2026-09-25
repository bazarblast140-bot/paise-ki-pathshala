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


def host_mp4(file: Path) -> str:
    """Upload to a GitHub Release and return a public browser_download_url."""
    repo = env("MEDIA_REPO") or env("GITHUB_REPOSITORY")
    token = env("MEDIA_REPO_TOKEN") if env("MEDIA_REPO") else (env("GITHUB_TOKEN") or env("MEDIA_REPO_TOKEN"))
    if not repo or not token:
        die("Need GITHUB_TOKEN+GITHUB_REPOSITORY (Actions) or MEDIA_REPO+MEDIA_REPO_TOKEN for public hosting")

    tag = f"ig-reel-{time.strftime('%Y-%m-%d')}"
    name = f"paise-short-{int(time.time())}.mp4"

    # Ensure release exists
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
            data=json.dumps({
                "tag_name": tag,
                "name": tag,
                "body": "Paise Ki Pathshala short — IG Reel cross-post media",
            }).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )

    # Drop same-name asset if re-running
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

    data = file.read_bytes()
    asset = http_json(
        f"{UPLOADS}/repos/{repo}/releases/{release['id']}/assets?name={urllib.parse.quote(name)}",
        method="POST",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "video/mp4",
            "Content-Length": str(len(data)),
        },
    )
    url = asset.get("browser_download_url") or ""
    if not url.startswith("https://"):
        die(f"No public download URL from release asset: {asset}")

    # Wait until public fetch works (no auth)
    for attempt in range(1, 9):
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ig-host-check"})
            with urllib.request.urlopen(req, timeout=30) as res:
                if res.status == 200 and res.read(8):
                    ok(f"hosted: {url}")
                    return url
        except Exception as err:  # noqa: BLE001
            print(f"  host wait {attempt}/8: {err}")
            time.sleep(1.5)
    die(f"Public URL not serving yet: {url}")
    return url


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


def build_caption() -> str:
    """Short IG caption from script + topic — not the full YouTube description."""
    parts = []
    script = (RUN / "script.txt").read_text(encoding="utf-8") if (RUN / "script.txt").exists() else ""
    # TITLE / HOOK lines
    title = ""
    hook = ""
    current = None
    buf = []
    for line in script.splitlines():
        s = line.strip()
        if re.match(r"^(TITLE|HOOK)\b", s, re.I):
            if current == "TITLE":
                title = "\n".join(buf).strip()
            elif current == "HOOK":
                hook = "\n".join(buf).strip()
            current = s.split()[0].upper()
            rest = re.sub(r"^(TITLE|HOOK)\b[^:]*:?\s*", "", s, flags=re.I)
            buf = [rest] if rest.strip() else []
        elif current in ("TITLE", "HOOK"):
            if re.match(r"^(BODY|CTA|DISCLAIMER|FACT)\b", s, re.I):
                if current == "TITLE":
                    title = "\n".join(buf).strip()
                elif current == "HOOK":
                    hook = "\n".join(buf).strip()
                current = None
                buf = []
            else:
                buf.append(line)
    if current == "TITLE":
        title = "\n".join(buf).strip()
    elif current == "HOOK":
        hook = "\n".join(buf).strip()

    if hook:
        parts.append(re.sub(r"\s+", " ", hook).strip()[:300])
    elif title:
        parts.append(title[:120])

    parts.append("यह सिर्फ़ शिक्षा के लिए है — निवेश सलाह नहीं।")

    # Same topic-relevant referral rule as YouTube; keep the Reel itself clean.
    try:
        ref_path = Path(__file__).parent / "data" / "referrals.json"
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        cat = str(topic.get("cat") or "")
        names = ref.get("category_map", {}).get(cat, [])
        links = ref.get("platforms", {})
        selected = []
        for name in names:
            p = links.get(name) or {}
            if p.get("url"):
                selected.append(f"{name.title()}: {p['url']}")
        if selected:
            parts.append("Referral links\\n" + ref.get("disclosure_hi", "यह referral link है। इससे account खोलने पर हमें referral benefit मिल सकता है।") + "\\n" + "\\n".join(selected))
    except (OSError, ValueError, TypeError):
        pass

    topic = {}
    if (RUN / "topic.json").exists():
        try:
            topic = json.loads((RUN / "topic.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    tags = ["#StockMarketHindi", "#TradingHindi", "#PaiseKiPathshala", "#ShareMarket"]
    if topic.get("seo"):
        t = "#" + re.sub(r"[^A-Za-z0-9]", "", str(topic["seo"]).title())
        if len(t) > 2:
            tags.insert(1, t)
    parts.append(" ".join(dict.fromkeys(tags)))

    return "\n\n".join(parts)[:2200]


def publish_reel(video_url: str, caption: str) -> str:
    ig_user = env("IG_USER_ID")
    if not ig_user or not env("IG_ACCESS_TOKEN"):
        die("IG_USER_ID and IG_ACCESS_TOKEN required")

    container = ig_call(
        f"{ig_user}/media",
        method="POST",
        params={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        },
    )
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

    # Prefer after successful YT upload, but allow force
    if not (RUN / "uploaded.txt").exists() and env("IG_CROSSPOST_FORCE") not in ("1", "true"):
        skip("YouTube upload not recorded (uploaded.txt missing) — set IG_CROSSPOST_FORCE=1 to post anyway")

    print("\n=== Instagram Reel cross-post ===")
    url = host_mp4(mp4)
    caption = build_caption()
    print(f"  caption ({len(caption)} chars): {caption[:80]}…")
    media_id = publish_reel(url, caption)
    (RUN / "ig_reel.txt").write_text(media_id, encoding="utf-8")
    ok(f"IG Reel published: {media_id}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # Never fail the YouTube side of the pipeline because of IG.
        print(f"[ig] error (non-fatal): {e}", file=sys.stderr)
        sys.exit(0)
