"""Publish a box-built Reel: upload its files to a release, then dispatch publish-artifact.yml.

Refuses (exit 2, HOLD) unless qa.json is a PASS from today's signed-in capture with EMA 20/50.
YouTube gets final_yt.mp4 (Paise Ki Pathshala end); IG/FB Reel + Story get final_ig.mp4
(Rajesh Technical Trader intro/outro). --dry checks and prints, uploads nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from override_mp4 import BOX_TAG, box_gate  # noqa: E402

REPO = "bazarblast140-bot/paise-ki-pathshala"
FILES = ("final.mp4", "final_yt.mp4", "final_ig.mp4", "thumbnail.jpg", "script.txt", "qa.json", "publish_meta.json")


def publish_meta(out):
    meta = json.loads((out / "post_meta.json").read_text(encoding="utf-8"))
    tags = [tag.lstrip("#") for tag in meta.get("hashtags") or []]
    description = "\n\n".join(x for x in (meta.get("caption"), " ".join(meta.get("hashtags") or [])) if x)
    return {"title": meta["title"][:100], "description": description, "tags": tags}


def main(argv):
    dry = "--dry" in argv
    fb_only = "--fb-only" in argv  # the session is already on YT/IG: add only the Facebook Reel
    args = [a for a in argv if not a.startswith("--")]
    out = Path(args[0]).resolve()
    qa = json.loads((out / "qa.json").read_text(encoding="utf-8"))
    reason = box_gate(qa)
    if reason:
        print(f"HOLD: not publishing {out}: {reason}", file=sys.stderr)
        return 2
    if (out / "post_meta.json").is_file():
        (out / "publish_meta.json").write_text(json.dumps(publish_meta(out), ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    missing = [name for name in FILES if not (out / name).is_file()]
    if missing:
        print(f"HOLD: missing {missing}", file=sys.stderr)
        return 2
    tag = f"{BOX_TAG}{qa.get('kind')}-{qa.get('session')}"
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    dispatch = [
        "gh", "workflow", "run", "publish-artifact.yml", "--repo", REPO, "--ref", "main",
        "-f", "privacy=now", "-f", "skip_ig=false", "-f", "ig_only=false", "-f", "post_story=true", "-f", "post_fb=true",
        "-f", f"override_mp4_url={base}/final.mp4",
        "-f", f"override_thumb_url={base}/thumbnail.jpg",
        "-f", f"override_script_url={base}/script.txt",
        "-f", f"override_meta_url={base}/publish_meta.json",
    ]
    if fb_only:
        dispatch = [a for a in dispatch if a not in ("post_story=true",)]
        dispatch[dispatch.index("post_fb=true")] = "fb_only=true"
        if "post_story=true" not in dispatch:
            dispatch = [a for i, a in enumerate(dispatch) if not (a == "-f" and i + 1 < len(dispatch) and dispatch[i + 1] == "-f")]
    if dry:
        print(f"[dry] would create release {tag} with {list(FILES)}")
        print("[dry] would run:", " ".join(dispatch))
        return 0
    exists = subprocess.run(["gh", "release", "view", tag, "--repo", REPO], capture_output=True).returncode == 0
    if fb_only:
        if not exists:
            print(f"HOLD: --fb-only needs the published release {tag}", file=sys.stderr)
            return 2
        subprocess.run(dispatch, check=True)
        print(f"[box] {tag}: Facebook Reel only, publish-artifact.yml dispatched (fb_only)")
        return 0
    if exists:
        print(f"HOLD: release {tag} already exists; refusing a second publish of the same session", file=sys.stderr)
        return 2
    subprocess.run(["gh", "release", "create", tag, "--repo", REPO, "--prerelease",
                    "--title", tag, "--notes", f"Box-built {qa.get('kind')} Reel, QA PASS"]
                   + [str(out / name) for name in FILES], check=True)
    subprocess.run(dispatch, check=True)
    series_file = os.environ.get("REALCHART_SERIES_FILE")
    if series_file and qa.get("series_n"):
        path = Path(series_file)
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data[qa["kind"]] = int(qa["series_n"])
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    print(f"[box] published {tag}: release uploaded, publish-artifact.yml dispatched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
