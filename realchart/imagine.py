"""Decorative stills and clips made outside CI (Grok Imagine).

Look in REALCHART_IMAGINE_DIR, or assets/imagine/<ticker>, <date>/<ticker>,
<date>, then assets/imagine itself. A manifest.json names the files. Without
one, thumb_bg.png (or .jpg) and broll_* clips are the slot.

Only two roles are accepted: thumb_bg (thumbnail photo/background) and broll
(decorative background). Charts, news screenshots and anything whose name or
role looks like data are rejected. Callers draw real candles and numbers
themselves; these files never replace them.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("thumb_bg", "broll")
IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO = {".mp4", ".mov", ".webm"}
# A decorative file must not pretend to be a chart, a headline or a data table.
_BLOCK = ("chart", "news", "number", "screenshot", "ohlc", "candle", "fundamental", "fii", "dii")


def load_imagine(date="", ticker="", cache_dir=None, root=None):
    """Return thumb_bg, broll frame paths, used and rejected. Missing slot is empty."""
    root = Path(root) if root else ROOT
    chosen = _pick_dir(root, str(date or "")[:10], str(ticker or ""))
    empty = {"thumb_bg": None, "broll_frames": [], "used": [], "rejected": [], "dir": None}
    if chosen is None:
        return empty
    cache = Path(cache_dir) if cache_dir else chosen / "_frames"
    assets, rejected = _listed(chosen)
    used = []
    thumb = None
    frames = []
    for item in assets:
        ok, reason = _allowed(item["file"], item["role"])
        if not ok:
            rejected.append({"file": item["file"], "reason": reason})
            continue
        path = chosen / item["file"]
        if not path.is_file():
            rejected.append({"file": item["file"], "reason": "file missing"})
            continue
        frame = _as_image(path, cache)
        if not frame:
            rejected.append({"file": item["file"], "reason": "could not read image or clip"})
            continue
        used.append({"role": item["role"], "file": item["file"], "dir": str(chosen)})
        if item["role"] == "thumb_bg" and thumb is None:
            thumb = frame
        elif item["role"] == "broll":
            frames.append(frame)
    return {"thumb_bg": thumb, "broll_frames": frames, "used": used, "rejected": rejected, "dir": str(chosen)}


def _pick_dir(root, date, ticker):
    env = os.environ.get("REALCHART_IMAGINE_DIR", "").strip()
    symbol = ticker.split(":")[-1].strip()
    if env:
        dirs = [Path(env)]
    else:
        base = root / "assets" / "imagine"
        dirs = [base / symbol, base / date / symbol, base / date, base]
    seen = set()
    for folder in dirs:
        key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        if folder.is_dir() and _has_slot(folder):
            return folder
    return None


def _has_slot(folder):
    if (folder / "manifest.json").is_file():
        return True
    for name in ("thumb_bg.png", "thumb_bg.jpg", "thumb_bg.jpeg", "thumb_bg.webp"):
        if (folder / name).is_file():
            return True
    return any(folder.glob("broll_*"))


def _listed(folder):
    manifest = folder / "manifest.json"
    rejected = []
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], [{"file": "manifest.json", "reason": "manifest is not json"}]
        rows = []
        for item in data.get("assets") or []:
            role = str(item.get("role") or "").strip().lower()
            name = str(item.get("file") or "").strip()
            if not name:
                continue
            rows.append({"file": name, "role": role})
        return rows, rejected
    rows = []
    for name in ("thumb_bg.png", "thumb_bg.jpg", "thumb_bg.jpeg", "thumb_bg.webp"):
        if (folder / name).is_file():
            rows.append({"file": name, "role": "thumb_bg"})
            break
    for path in sorted(folder.glob("broll_*")):
        if path.suffix.lower() in IMAGE | VIDEO:
            rows.append({"file": path.name, "role": "broll"})
    return rows, rejected


def _allowed(name, role):
    if role not in ROLES:
        return False, f"role {role or 'missing'} is not decorative (only thumb_bg and broll)"
    low = Path(name).name.lower()
    for token in _BLOCK:
        if token in low:
            return False, f"{name} looks like {token}; AI files cannot supply charts, news or numbers"
    return True, ""


def _as_image(path, cache):
    path = Path(path)
    if path.suffix.lower() in IMAGE:
        return str(path)
    if path.suffix.lower() not in VIDEO:
        return None
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{path.stem}.png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "0.2", "-i", str(path), "-frames:v", "1", str(dest)],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return str(dest) if dest.is_file() else None
