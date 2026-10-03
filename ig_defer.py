"""Hold a Reel until the YouTube video is public.

A scheduled upload writes run/ig_status.txt and does not call Instagram.
Slot watch (08:49, 16:49, 21:49 IST) then starts the Instagram Reel Publish
Watchdog, which posts that same final.mp4 and cover only after privacyStatus
is public. A Reel already stored for the video id is not posted again.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parse_publish_at(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def publish_at_is_future(raw, now=None):
    when = parse_publish_at(raw)
    if when is None:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return when > now.astimezone(timezone.utc)


def defer_status(video_id, publish_at, run_id=""):
    line = f"deferred: {str(video_id).strip()} publishAt={str(publish_at).strip()}"
    run_id = str(run_id or "").strip()
    if run_id:
        line += f" run={run_id}"
    return line


def parse_deferred(text):
    line = ""
    for raw in str(text or "").splitlines():
        if raw.strip():
            line = raw.strip()
            break
    if not line.startswith("deferred:"):
        return None
    parts = line[len("deferred:"):].split()
    if not parts:
        return None
    parsed = {"video_id": parts[0], "publish_at": "", "run_id": ""}
    for part in parts[1:]:
        if part.startswith("publishAt="):
            parsed["publish_at"] = part.split("=", 1)[1]
        elif part.startswith("run="):
            parsed["run_id"] = part.split("=", 1)[1]
    if not parsed["video_id"] or not parsed["publish_at"]:
        return None
    return parsed


def is_due(parsed, now):
    when = parse_publish_at((parsed or {}).get("publish_at"))
    if when is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return when <= now.astimezone(timezone.utc)


def write_deferred(run_dir, video_id, publish_at, run_id=""):
    """Write ig_status.txt and note the same line on the YouTube ledger row."""
    import ledger
    status = defer_status(video_id, publish_at, run_id)
    path = Path(run_dir) / "ig_status.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(status + "\n", encoding="utf-8")
    ledger.note_ig_deferred(video_id, status, run_id)
    print(f"[ig] deferred until {publish_at}: {status}")
    return status


def due_rows(entries, now):
    """Deferred Reels whose publishAt has passed and that have no media id yet."""
    found = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("ig_media_id") or "").strip():
            continue
        parsed = parse_deferred(entry.get("ig_deferred") or "")
        if not parsed or not is_due(parsed, now):
            continue
        video_id = str(entry.get("video_id") or parsed["video_id"]).strip()
        source_run = str(entry.get("source_run") or parsed["run_id"]).strip()
        if not video_id or not source_run:
            continue
        found.append({
            "video_id": video_id,
            "publish_at": parsed["publish_at"],
            "source_run": source_run,
        })
    return found


def post_due(entries, now, privacy_of, poster, fetcher, already_logged):
    """Post each due Reel whose YouTube video is public.

    privacy_of(video_id) is the YouTube privacyStatus. already_logged returns
    the stored media id, or "" when this video has no Reel yet.
    """
    results = []
    for row in due_rows(entries, now):
        video_id = row["video_id"]
        prior = str(already_logged(video_id) or "").strip()
        if prior:
            print(f"[ig] Reel already logged for {video_id}: {prior}")
            results.append({"video_id": video_id, "action": "already-logged", "media_id": prior})
            continue
        try:
            privacy = str(privacy_of(video_id) or "").strip().lower()
        except Exception as exc:  # noqa: BLE001 — a failed check must not post early
            print(f"[ig] deferred {video_id} privacy check failed ({exc}); not posting")
            results.append({"video_id": video_id, "action": "not-public", "privacy": ""})
            continue
        if privacy != "public":
            print(f"[ig] deferred {video_id} is {privacy or 'unknown'}; not posting")
            results.append({"video_id": video_id, "action": "not-public", "privacy": privacy})
            continue
        dest = fetcher(row["source_run"])
        poster(dest, video_id)
        results.append({"video_id": video_id, "action": "posted"})
    return results


def privacy_status(video_id):
    """YouTube privacyStatus for this id, or "" when the video cannot be read."""
    from yt_credentials import youtube_client_or_exit
    yt = youtube_client_or_exit()
    listed = yt.videos().list(part="status", id=str(video_id)).execute()
    items = listed.get("items") or []
    if not items:
        return ""
    return str((items[0].get("status") or {}).get("privacyStatus") or "")


def download_run(run_id, dest):
    """Download one Actions run into dest and surface final.mp4 at the top."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    subprocess.run(
        ["gh", "run", "download", str(run_id), "-D", str(dest)],
        check=True,
    )
    if not (dest / "final.mp4").is_file():
        found = next(dest.rglob("final.mp4"), None)
        if found is not None:
            for item in found.parent.iterdir():
                target = dest / item.name
                if item.is_file() and not target.exists():
                    shutil.copy2(item, target)
    return dest


def _poster(dest, video_id):
    import publish_instagram as ig
    dest = Path(dest)
    uploaded = dest / "uploaded.txt"
    if not uploaded.is_file() or not uploaded.read_text(encoding="utf-8").strip():
        uploaded.write_text(str(video_id) + "\n", encoding="utf-8")
    ig.RUN = dest
    ig.main()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "--post-due":
        print("usage: ig_defer.py --post-due", file=sys.stderr)
        return 2
    import ledger
    now = datetime.now(timezone.utc)
    post_due(
        ledger._entries(),
        now,
        privacy_status,
        _poster,
        lambda run_id: download_run(run_id, ROOT / "run"),
        lambda video_id: ledger.find_ig_media(video_id=video_id),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[ig] deferred post failed: {exc}", file=sys.stderr)
        sys.exit(1)
