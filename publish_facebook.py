"""Facebook Reel for the box Reel (final_ig.mp4: Rajesh Technical Trader ends) on the Page behind IG_ACCESS_TOKEN.

The token is a Page token (GET /me returns the Page). The Page name must contain FB_PAGE_NAME
(default "Rajesh Technical Trader"), otherwise nothing is posted. A Reel whose description
already carries the same title is not posted again. Upload: /me/video_reels start -> rupload
binary -> finish (PUBLISHED). Exit 0 posted/already there, 2 refused (wrong Page/no token), 1 error.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RUN = Path(os.environ.get("RUN_DIR", "run"))
VERSION = os.environ.get("FB_API_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{VERSION}"
PAGE = "me"


def call(method, url, params=None, data=None, headers=None, timeout=120):
    token = os.environ.get("FB_ACCESS_TOKEN") or os.environ.get("IG_ACCESS_TOKEN") or ""
    params = dict(params or {})
    if not url.startswith("https://rupload"):
        params["access_token"] = token
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(full, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        body = err.read().decode()[:400].replace(token, "***") if token else err.read().decode()[:400]
        raise RuntimeError(f"HTTP {err.code} {url.split('?')[0].replace(GRAPH, '')}: {body}") from None


def caption():
    meta = RUN / "publish_meta.json"
    if meta.is_file():
        data = json.loads(meta.read_text(encoding="utf-8"))
        title = str(data.get("title") or "").strip()
        body = str(data.get("description") or "").strip()
        return title, (title + "\n\n" + body).strip()[:2000]
    lines = (RUN / "script.txt").read_text(encoding="utf-8").splitlines()
    title = next((l.strip() for l in lines[1:] if l.strip()), "Paise Ki Pathshala")
    return title, title


def video_path():
    for name in ("final_ig.mp4", "final.mp4"):
        if (RUN / name).is_file() and (RUN / name).stat().st_size > 0:
            return RUN / name
    raise FileNotFoundError("run/final_ig.mp4 missing")


def already_posted(title):
    data = call("GET", f"{GRAPH}/{PAGE}/video_reels", {"fields": "id,description,created_time,permalink_url", "limit": "25"})
    for item in data.get("data") or []:
        if title and title in str(item.get("description") or ""):
            return item
    return None


def main():
    token = os.environ.get("FB_ACCESS_TOKEN") or os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        print("[fb] no page token (FB_ACCESS_TOKEN / IG_ACCESS_TOKEN); Facebook Reel not posted")
        return 2
    want = os.environ.get("FB_PAGE_NAME", "Rajesh Technical Trader").strip()
    want_id = os.environ.get("FB_PAGE_ID", "").strip()
    page = call("GET", f"{GRAPH}/me", {"fields": "id,name"})
    name = str(page.get("name") or "")
    print(f"[fb] token page: {name or '(no name)'} ({page.get('id')})")
    page_id = str(page.get("id") or "")
    if want_id and page_id != want_id:
        print(f"[fb] refused: the token's Page id is {page_id}, not FB_PAGE_ID {want_id}; Facebook Reel not posted")
        return 2
    if want.casefold() not in name.casefold():
        print(f"[fb] refused: the token's Page is {name!r}, not {want!r}; Facebook Reel not posted")
        return 2
    global PAGE
    PAGE = want_id or page_id or "me"
    title, text = caption()
    existing = already_posted(title)
    if existing:
        print(f"[fb] already on the Page: reel {existing.get('id')} {existing.get('permalink_url') or ''}")
        return 0
    path = video_path()
    if os.environ.get("FB_DRY") == "1":
        print(f"[fb] dry: would post {path} ({path.stat().st_size} bytes) titled {title!r}")
        return 0
    start = call("POST", f"{GRAPH}/{PAGE}/video_reels", {"upload_phase": "start"})
    video_id = start["video_id"]
    blob = path.read_bytes()
    up = call("POST", f"https://rupload.facebook.com/video-upload/{VERSION}/{video_id}", data=blob, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(blob)),
    }, timeout=600)
    if not up.get("success"):
        raise RuntimeError(f"upload not accepted: {up}")
    call("POST", f"{GRAPH}/{PAGE}/video_reels", {"upload_phase": "finish", "video_id": video_id,
                                              "video_state": "PUBLISHED", "description": text})
    link = ""
    for _ in range(40):
        info = call("GET", f"{GRAPH}/{video_id}", {"fields": "status,permalink_url"})
        status = (info.get("status") or {})
        link = info.get("permalink_url") or link
        phase = status.get("video_status")
        print(f"[fb] status {phase} {(status.get('publishing_phase') or {}).get('status', '')}")
        if phase == "ready" and (status.get("publishing_phase") or {}).get("status") == "complete":
            break
        if phase == "error":
            raise RuntimeError(f"processing error: {status}")
        time.sleep(15)
    if link and link.startswith("/"):
        link = "https://www.facebook.com" + link
    print(f"[fb] reel {video_id} {link}")
    (RUN / "fb_reel.json").write_text(json.dumps({"video_id": video_id, "permalink": link}) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[fb] error: {exc}")
        sys.exit(1)
