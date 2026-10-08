"""Voice a dry-run script again. No DeepSeek call and no sentence trim.

The Edge dry run uploads `edge-dry-run`. Older runs, including 37095205924,
store `tts_<topic>.txt` and `tts_text.txt` and do not store narration JSON.
Each line is `tts_<beat>: <speakable text>`. Newer runs also store
`narration_<topic>.json`.
"""
import argparse
import hashlib
import io
import json
import os
import pathlib
import zipfile

import requests

from common import die

ORDER = ("hook", "pehchan", "matlab", "karein")
_LINE = __import__("re").compile(
    r"^(?:tts_)?(hook|pehchan|matlab|udaharan|galti|karein)\s*:\s*(.*)$"
)
# Set once, when the dry-run beats are loaded. A later narration.json rewrite
# must not replace these. Paid TTS dies unless the sent string still matches.
_LOCKED = {}


def beat_digest(text):
    """sha256 of the exact beat string that may be sent to ElevenLabs."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _hash_order(beats):
    import trade_plan
    if trade_plan.is_plan_script({"beats": beats}):
        return trade_plan.BEATS
    return ORDER


def hashes_for(beats):
    return {beat: beat_digest((beats or {}).get(beat) or "") for beat in _hash_order(beats)}


def remember(script):
    """Lock the approved beat hashes for this process. Later edits do not move them."""
    global _LOCKED
    beats = (script or {}).get("beats") or {}
    order = _hash_order(beats)
    stored = (script or {}).get("approved_hashes") or hashes_for(beats)
    _LOCKED = {beat: stored[beat] for beat in order if stored.get(beat)}
    if isinstance(script, dict):
        script["approved_hashes"] = {beat: _LOCKED[beat] for beat in order if beat in _LOCKED}
    return script


def locked_hashes():
    return dict(_LOCKED)


def refuse_paid_mismatch(text):
    """Die before any ElevenLabs character unless `text` is an approved beat.

    Run 37098045611 sent the dry-run lines unchanged (tts_text matched, 626
    characters). The live voice path can still rewrite a later script:
    fold_roman_words, then a second speak.speakable() inside tts(). Either
    one changes the sha256, and this refuses the paid call.
    """
    if not active():
        return
    digest = beat_digest(text)
    if not _LOCKED or digest not in set(_LOCKED.values()):
        die(
            "approved voice text hash mismatch; refusing paid TTS with 0 characters spent"
        )


def _shares():
    import board_short
    return [int(board_short.FOUR_LIVE_SHARE[beat]) for beat in ORDER]


def split_spoken_blob(text):
    """Split one spoken blob with the four-beat word shares. Last beat takes the rest."""
    words = str(text or "").split()
    shares = _shares()
    total = sum(shares) or 1
    beats = {}
    cursor = 0
    for index, beat in enumerate(ORDER):
        if index == len(ORDER) - 1:
            beats[beat] = " ".join(words[cursor:])
            break
        count = int(round(len(words) * shares[index] / total))
        beats[beat] = " ".join(words[cursor:cursor + count])
        cursor += count
    return beats


def beats_from_tts_text(text):
    """Saved `tts_<beat>:` lines win. A bare blob uses the four-beat splitter."""
    found = {beat: [] for beat in ORDER}
    prefixed = False
    blobs = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LINE.match(line)
        if match and match.group(1) in found:
            prefixed = True
            found[match.group(1)].append(match.group(2).strip())
        else:
            blobs.append(line)
    if prefixed:
        return {beat: " ".join(parts).strip() for beat, parts in found.items()}
    return split_spoken_blob(" ".join(blobs))


def _read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    beats = payload.get("beats") if isinstance(payload, dict) else None
    if not isinstance(beats, dict):
        return None
    if not any(str(beats.get(beat) or "").strip() for beat in ORDER):
        return None
    return payload


def load_payload(directory, topic_key):
    """Narration dict from a downloaded `edge-dry-run` directory. None if empty."""
    root = pathlib.Path(directory)
    if not root.is_dir():
        return None
    key = str(topic_key or "").strip()
    for name in ((f"narration_{key}.json",) if key else ()) + ("narration.json",):
        path = root / name
        if path.is_file():
            payload = _read_json(path)
            if payload is not None:
                return payload
    texts = []
    if key:
        texts.append(root / f"tts_{key}.txt")
    texts.append(root / "tts_text.txt")
    for path in texts:
        if not path.is_file():
            continue
        beats = beats_from_tts_text(path.read_text(encoding="utf-8"))
        if any(beats.values()):
            return {"beats": beats}
    return None


def _opening_hook(beats, topic):
    """First sentence of the approved hook. That sentence is the hook line.

    An approved script is not dropped when the opening line is a statement.
    An empty hook line is what the later gate reports as 'hook line missing'.
    """
    del topic
    text = str((beats or {}).get("hook") or "").strip()
    sentence = __import__("re").split(r"(?<=[।?!])\s+", text, maxsplit=1)[0].strip()
    return sentence


def narration_from_payload(payload, topic, run_id=""):
    """Exact beats. Title falls back to the topic line. Nothing is trimmed."""
    beats_in = (payload or {}).get("beats") or {}
    import trade_plan
    if (payload or {}).get("plan") or trade_plan.is_plan_script({"beats": beats_in}):
        beats = {beat: str(beats_in.get(beat) or "").strip() for beat in trade_plan.BEATS}
        missing = [beat for beat in trade_plan.BEATS if not beats[beat]]
        if missing:
            die(
                "approved dry-run script is missing "
                + ", ".join(missing)
                + "; refusing a new DeepSeek script"
            )
        title = " ".join(str((payload or {}).get("title") or "").split())
        if not title:
            title = " ".join(str((topic or {}).get("topic") or (topic or {}).get("key") or "पाठ").split())
        hook_line = " ".join(str((payload or {}).get("hook_line") or "").split())
        if not hook_line:
            hook_line = _opening_hook(beats, topic)
        script = {
            "title": title,
            "hook_line": hook_line,
            "beats": beats,
            "source": "approved",
            "plan": True,
            "live_beats": "plan",
            "narration_fallback": False,
            "review_attempts": 1,
            "from_run": str(run_id or ""),
            "approved_hashes": hashes_for(beats),
        }
        return remember(script)
    beats = {beat: str(beats_in.get(beat) or "").strip() for beat in ORDER}
    missing = [beat for beat in ORDER if not beats[beat]]
    if missing:
        die(
            "approved dry-run script is missing "
            + ", ".join(missing)
            + "; refusing a new DeepSeek script"
        )
    title = " ".join(str((payload or {}).get("title") or "").split())
    if not title:
        title = " ".join(str((topic or {}).get("topic") or (topic or {}).get("key") or "पाठ").split())
    hook_line = " ".join(str((payload or {}).get("hook_line") or "").split())
    if not hook_line:
        hook_line = _opening_hook(beats, topic)
    script = {
        "title": title,
        "hook_line": hook_line,
        "beats": beats,
        "source": "approved",
        "narration_fallback": False,
        "review_attempts": 1,
        "from_run": str(run_id or ""),
        "approved_hashes": hashes_for(beats),
    }
    return remember(script)


def active():
    return bool(os.environ.get("NARRATION_FROM_RUN", "").strip())


def load_for_topic(topic):
    """The approved script, or None when this build is not narration_from_run."""
    run_id = os.environ.get("NARRATION_FROM_RUN", "").strip()
    if not run_id:
        return None
    directory = os.environ.get("APPROVED_SCRIPT_DIR", "").strip() or "run/approved_script"
    payload = load_payload(directory, (topic or {}).get("key") or "")
    if payload is None:
        die(
            f"narration_from_run {run_id} has no edge-dry-run script in {directory}"
        )
    script = narration_from_payload(payload, topic, run_id)
    remember(script)
    if isinstance(topic, dict):
        topic["_approved_script"] = True
    return script


def repo_beats_path(topic_key):
    """Committed approved beats for one topic. Absent for every other topic."""
    key = str(topic_key or "").strip()
    if not key:
        return None
    path = pathlib.Path(__file__).parent / "approved" / f"{key}.json"
    return path if path.is_file() else None


def install_repo_beats(directory, topic_key):
    """Write the committed approved script over a downloaded dry run.

    Call this after the artifact download and before narration() locks the
    sha256. The dry-run tts file stays on disk; narration.json wins, and the
    lock is the committed beats, title, and hook line.
    """
    path = repo_beats_path(topic_key)
    if path is None:
        return None
    extra = json.loads(path.read_text(encoding="utf-8"))
    payload = load_payload(directory, topic_key) or {}
    beats = dict((payload.get("beats") or {}))
    incoming = extra.get("beats") if isinstance(extra.get("beats"), dict) else extra
    import trade_plan
    if trade_plan.is_plan_script(extra):
        beats = {
            beat: " ".join(str((incoming or {}).get(beat) or "").split())
            for beat in trade_plan.BEATS
        }
        payload["plan"] = True
        payload["live_beats"] = "plan"
    else:
        for beat, text in (incoming or {}).items():
            if beat not in ORDER:
                continue
            cleaned = " ".join(str(text or "").split())
            if cleaned:
                beats[beat] = cleaned
    payload["beats"] = beats
    title = " ".join(str(extra.get("title") or "").split())
    hook_line = " ".join(str(extra.get("hook_line") or "").split())
    if title:
        payload["title"] = title
    if hook_line:
        payload["hook_line"] = hook_line
    payload["source"] = "approved"
    payload["approved_hashes"] = hashes_for(beats)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    root = pathlib.Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    (root / "narration.json").write_text(raw, encoding="utf-8")
    key = str(topic_key or "").strip()
    if key:
        (root / f"narration_{key}.json").write_text(raw, encoding="utf-8")
    return payload


def download_edge_artifact(run_id, dest):
    """Download the `edge-dry-run` zip for one Actions run. Needs actions:read."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        die("GITHUB_TOKEN and GITHUB_REPOSITORY are required to download the dry-run artifact")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    listed = requests.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts",
        headers=headers,
        params={"per_page": 100},
        timeout=60,
    )
    listed.raise_for_status()
    artifacts = (listed.json() or {}).get("artifacts") or []
    match = next(
        (
            item for item in artifacts
            if item.get("name") == "edge-dry-run" and not item.get("expired")
        ),
        None,
    )
    if match is None:
        die(f"run {run_id} has no edge-dry-run artifact")
    archive = requests.get(match["archive_download_url"], headers=headers, timeout=120)
    archive.raise_for_status()
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        bundle.extractall(dest)
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download one edge-dry-run artifact.")
    parser.add_argument("--download", required=True, help="Actions run id")
    parser.add_argument("--dest", default="run/approved_script")
    args = parser.parse_args(argv)
    download_edge_artifact(args.download, args.dest)
    print(f"[ok] approved script from run {args.download} -> {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
