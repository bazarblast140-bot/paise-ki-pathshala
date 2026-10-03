"""Replace run/final.mp4 with one GitHub release asset before publish.

The URL has to be a release on this repo. Duration stays within a second of
the artifact, and the picture stays 1080x1920 h264, so a hand remix cannot
swap in a different video. YouTube and the Reel both read the replaced file.
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

RUN = Path(__file__).resolve().parent / "run"
PREFIX = "/bazarblast140-bot/paise-ki-pathshala/releases/download/"
MAX_BYTES = 80 * 1024 * 1024


def die(msg):
    print(f"[override] FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def release_url_ok(url):
    """True only for https://github.com/.../paise-ki-pathshala/releases/download/<tag>/<file>.mp4."""
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
    return parts[1].lower().endswith(".mp4")


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


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "paise-ki-pathshala"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        blob = resp.read(MAX_BYTES + 1)
    if len(blob) > MAX_BYTES:
        die("override mp4 is larger than 80 MB")
    if len(blob) < 1000:
        die("override mp4 is empty")
    dest.write_bytes(blob)


def apply(url, artifact, dest=None):
    """Download url over artifact only when the picture and duration match."""
    url = str(url or "").strip()
    if not release_url_ok(url):
        die("override_mp4_url must be https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/<tag>/<file>.mp4")
    artifact = Path(artifact)
    dest = Path(dest or artifact)
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
        delta = abs(got["duration"] - original["duration"])
        if delta > 1.0:
            die(f"override duration {got['duration']:.2f}s is {delta:.2f}s off the artifact {original['duration']:.2f}s")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    print(f"[override] replaced {dest.name} from {url}")
    return dest


def main():
    url = (os.environ.get("OVERRIDE_MP4_URL") or "").strip()
    if not url:
        return
    apply(url, RUN / "final.mp4")


if __name__ == "__main__":
    main()
