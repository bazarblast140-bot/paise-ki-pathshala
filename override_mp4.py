"""Replace run/final.mp4 with one GitHub release asset before publish.

The URL has to be a release on this repo. Duration stays within a second of
the artifact, and the picture stays 1080x1920 h264, so a hand remix cannot
swap in a different video. A box Reel that also replaces script.txt skips
that duration check, and a dispatch with no source run downloads the mp4,
thumbnail, and script straight from the release. YouTube and the Reel both
read the replaced files.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RUN = Path(__file__).resolve().parent / "run"
PREFIX = "/bazarblast140-bot/paise-ki-pathshala/releases/download/"
API_REPO = "https://api.github.com/repos/bazarblast140-bot/paise-ki-pathshala"
MAX_BYTES = 80 * 1024 * 1024


def die(msg):
    print(f"[override] FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def release_url_ok(url, suffix=".mp4"):
    """True only for https://github.com/.../releases/download/<tag>/<file><suffix>."""
    raw = str(url or "").strip()
    if not raw or raw != str(url or "").strip():
        return False
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    path = parsed.path or ""
    if ".." in path or "\\" in path or not path.startswith(PREFIX):
        return False
    parts = path[len(PREFIX):].split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    return parts[1].lower().endswith(str(suffix).lower())


def thumb_url_ok(url):
    """True only for a .jpg on this repo's release downloads."""
    return release_url_ok(url, suffix=".jpg")


def script_url_ok(url):
    """True only for a .txt on this repo's release downloads."""
    return release_url_ok(url, suffix=".txt")


def meta_url_ok(url):
    """True only for a .json on this repo's release downloads."""
    return release_url_ok(url, suffix=".json")


def probe(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        die(f"ffprobe failed: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), None)
    if video is None:
        die(f"{path} has no video stream")
    duration = (data.get("format") or {}).get("duration") or video.get("duration")
    if duration is None:
        die(f"{path} has no duration")
    return {
        "duration": float(duration),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "codec": str(video.get("codec_name") or ""),
    }


def _token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _keeps_auth(host):
    """The API host may see the token. A storage redirect must not."""
    return (host or "").lower().rstrip(".") in ("github.com", "api.github.com")


class _DropAuthOffGitHub(urllib.request.HTTPRedirectHandler):
    """Follow the asset redirect, and do not send the token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        host = urllib.parse.urlparse(newurl).hostname
        if _keeps_auth(host):
            return new
        for bag in (new.headers, getattr(new, "unredirected_hdrs", {})):
            for key in [k for k in bag if k.lower() == "authorization"]:
                del bag[key]
        return new


def _open(req):
    opener = urllib.request.build_opener(_DropAuthOffGitHub)
    return opener.open(req, timeout=120)


def _read(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with _open(req) as resp:
            return resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        die(f"HTTP {exc.code} for {url}")


def _release_name(url):
    parsed = urllib.parse.urlparse(url)
    tag, name = parsed.path[len(PREFIX):].split("/")
    return urllib.parse.unquote(tag), urllib.parse.unquote(name)


def _download_public(url):
    req = urllib.request.Request(url, headers={"User-Agent": "paise-ki-pathshala"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read(MAX_BYTES + 1)


def _download_private(url, token):
    """Resolve the release asset with the API, then download that asset id."""
    tag, name = _release_name(url)
    meta_url = f"{API_REPO}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    auth = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "paise-ki-pathshala",
        "Accept": "application/vnd.github+json",
    }
    raw = _read(meta_url, auth)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die(f"release {tag} did not return JSON")
    asset_id = None
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        raw_id = asset.get("id")
        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
            asset_id = raw_id
            break
    if not asset_id:
        die(f"release {tag} has no asset named {name}")
    asset_url = f"{API_REPO}/releases/assets/{asset_id}"
    return _read(asset_url, {
        "Authorization": f"Bearer {token}",
        "Accept": "application/octet-stream",
        "User-Agent": "paise-ki-pathshala",
    })


def _download(url, dest, min_bytes=1000, label="override mp4"):
    token = _token()
    blob = _download_private(url, token) if token else _download_public(url)
    if len(blob) > MAX_BYTES:
        die(f"{label} is larger than 80 MB")
    if len(blob) < min_bytes:
        die(f"{label} is empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)


def apply(url, artifact, dest=None, check_duration=True):
    """Download url over artifact when the picture matches.

    Duration stays within 1s of the source artifact unless check_duration
    is false. A box Reel sets that false because its length is its own.
    """
    url = str(url or "").strip()
    if not release_url_ok(url):
        die("override_mp4_url must be https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/<tag>/<file>.mp4")
    artifact = Path(artifact)
    dest = Path(dest or artifact)
    original = None
    if check_duration:
        if not artifact.is_file() or artifact.stat().st_size < 1000:
            die(f"artifact mp4 missing: {artifact}")
        original = probe(artifact)
    tmp = dest.with_name(dest.name + ".override.part")
    try:
        _download(url, tmp)
        got = probe(tmp)
        if got["codec"] != "h264":
            die(f"override video is {got['codec'] or 'unknown'}, want h264")
        if (got["width"], got["height"]) != (1080, 1920):
            die(f"override video is {got['width']}x{got['height']}, want 1080x1920")
        if check_duration:
            delta = abs(got["duration"] - original["duration"])
            if delta > 1.0:
                die(f"override duration {got['duration']:.2f}s is {delta:.2f}s off the artifact {original['duration']:.2f}s")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"[override] replaced {dest.name} from {url}")
    return dest


def apply_thumb(url, dest):
    """Download a release jpeg over dest. Same token path as the mp4 override."""
    url = str(url or "").strip()
    if not thumb_url_ok(url):
        die("thumb url must be https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/<tag>/<file>.jpg")
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".override.part")
    try:
        _download(url, tmp, min_bytes=100, label="thumbnail")
        blob = tmp.read_bytes()
        if not blob.startswith(b"\xff\xd8"):
            die("thumbnail is not a jpeg")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"[override] replaced {dest.name} from {url}")
    return dest


def _has_title(text):
    for line in str(text or "").splitlines():
        head = line.strip().upper()
        if head == "TITLE" or head.startswith("TITLE:") or head.startswith("TITLE "):
            return True
    return False


def apply_script(url, dest):
    """Download a release script.txt. Title, description, and the IG caption read it."""
    url = str(url or "").strip()
    if not script_url_ok(url):
        die("script url must be a .txt release asset on this repo")
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".override.part")
    try:
        _download(url, tmp, min_bytes=50, label="script.txt")
        try:
            text = tmp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            die("script.txt is not utf-8")
        if not _has_title(text):
            die("script.txt has no TITLE heading")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"[override] replaced {dest.name} from {url}")
    return dest


def apply_meta(url, dest):
    """Download optional JSON: title, description, and tags."""
    url = str(url or "").strip()
    if not meta_url_ok(url):
        die("meta url must be a .json release asset on this repo")
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".override.part")
    try:
        _download(url, tmp, min_bytes=2, label="publish meta")
        try:
            data = json.loads(tmp.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            die("publish meta is not JSON")
        if not isinstance(data, dict):
            die("publish meta must be a JSON object")
        clean = {}
        if "title" in data:
            title = data.get("title")
            if not isinstance(title, str) or not title.strip():
                die("meta title must be a non-empty string")
            clean["title"] = title.strip()
        if "description" in data:
            description = data.get("description")
            if not isinstance(description, str) or not description.strip():
                die("meta description must be a non-empty string")
            clean["description"] = description
        if "tags" in data:
            tags = data.get("tags")
            if not isinstance(tags, list) or not tags or not all(isinstance(item, str) and item.strip() for item in tags):
                die("meta tags must be a list of strings")
            clean["tags"] = [item.strip() for item in tags]
        if not clean:
            die("publish meta needs title, description, or tags")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"[override] replaced {dest.name} from {url}")
    return dest


def bundle(dest=None):
    """Place a release mp4, thumbnail, and script when there is no source run.

    override_meta_url is optional. Duration is not compared to another video.
    """
    dest = Path(dest or RUN)
    mp4 = (os.environ.get("OVERRIDE_MP4_URL") or "").strip()
    thumb = (os.environ.get("OVERRIDE_THUMB_URL") or "").strip()
    script = (os.environ.get("OVERRIDE_SCRIPT_URL") or "").strip()
    meta = (os.environ.get("OVERRIDE_META_URL") or "").strip()
    if not mp4 or not thumb or not script:
        die("source_run_id is empty; set override_mp4_url, override_thumb_url and override_script_url")
    dest.mkdir(parents=True, exist_ok=True)
    apply(mp4, dest / "final.mp4", check_duration=False)
    tag, _name = _release_name(mp4)
    if tag.startswith(BOX_TAG):
        box_bundle(mp4, dest)
    apply_thumb(thumb, dest / "thumbnail.jpg")
    apply_script(script, dest / "script.txt")
    if meta:
        apply_meta(meta, dest / "publish_meta.json")
    return dest


BOX_TAG = "box-reel-"


def _sibling(url, name):
    return url.rsplit("/", 1)[0] + "/" + name


def box_gate(qa, today=None):
    """A box-built Reel publishes only on a PASS from today's signed-in capture with EMA 20/50."""
    from datetime import datetime, timedelta, timezone
    today = today or (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()
    if qa.get("verdict") != "PASS":
        return f"qa verdict {qa.get('verdict')!r}"
    if qa.get("capture_mode") != "box-signed-in":
        return f"capture_mode {qa.get('capture_mode')!r}"
    if qa.get("ema_ok") is not True:
        return "EMA 20/50 not confirmed on the captures"
    if qa.get("session") != today:
        return f"session {qa.get('session')} is not today {today}"
    return ""


def box_bundle(mp4_url, dest):
    """Box Reel: also fetch qa.json (gate), final_yt.mp4 (PKP end) and final_ig.mp4 (RTT ends)."""
    qa_path = dest / "qa.json"
    _download(_sibling(mp4_url, "qa.json"), qa_path, min_bytes=2, label="qa.json")
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        die("box qa.json is not JSON")
    reason = box_gate(qa)
    if reason:
        die(f"box Reel refused: {reason}")
    for name in ("final_yt.mp4", "final_ig.mp4"):
        apply(_sibling(mp4_url, name), dest / name, check_duration=False)


def _skip_duration():
    return (os.environ.get("SKIP_DURATION_CHECK") or "").strip().lower() in ("1", "true", "yes", "on")


def main():
    if "--bundle" in sys.argv:
        bundle(RUN)
        return
    if "--script" in sys.argv:
        url = (os.environ.get("OVERRIDE_SCRIPT_URL") or "").strip()
        if not url:
            return
        apply_script(url, RUN / "script.txt")
        return
    if "--meta" in sys.argv:
        url = (os.environ.get("OVERRIDE_META_URL") or "").strip()
        if not url:
            return
        apply_meta(url, RUN / "publish_meta.json")
        return
    if "--thumb" in sys.argv:
        url = (os.environ.get("OVERRIDE_THUMB_URL") or "").strip()
        if not url:
            return
        apply_thumb(url, RUN / "thumbnail.jpg")
        return
    url = (os.environ.get("OVERRIDE_MP4_URL") or "").strip()
    if not url:
        return
    apply(url, RUN / "final.mp4", check_duration=not _skip_duration())


if __name__ == "__main__":
    main()
