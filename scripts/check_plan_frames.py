#!/usr/bin/env python3
"""Frame check for a finished plan Short. Read-only; nothing is uploaded.

    python scripts/check_plan_frames.py final.mp4 --times 6 22 36 42 --out-dir frames/ \
        --highlight-time 6

For each time: the chart box (the AXIS-coloured rectangle) is found and no
candle-coloured pixel may sit outside it. At --highlight-time the yellow
key-candle box must hold a candle, not empty space. Exit 1 on any fault.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import motion_lib  # noqa: E402


def _near(rgb, color, tol=40):
    return np.abs(rgb.astype(np.int16) - np.array(color, dtype=np.int16)).sum(axis=2) <= tol


def _longest_run(row):
    best = run = 0
    for v in row:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def chart_box(rgb):
    axis = _near(rgb, motion_lib.AXIS, 45)
    h, w = axis.shape
    rows = [y for y in range(h) if axis[y].sum() > 0.6 * w]
    cols = [x for x in range(w) if axis[:, x].sum() > 0.25 * h]
    if not rows or not cols:
        return None
    return min(cols), min(rows), max(cols), max(rows)


def candle_mask(rgb):
    return _near(rgb, motion_lib.BEAR) | _near(rgb, motion_lib.BULL)


def faults_for(rgb, highlight=False):
    out = []
    box = chart_box(rgb)
    if box is None:
        return ["chart box not found"]
    x0, y0, x1, y1 = box
    mask = candle_mask(rgb)
    inside = np.zeros_like(mask)
    inside[y0:y1 + 1, x0:x1 + 1] = True
    stray = int((mask & ~inside)[: y1 + 40].sum())
    if stray > 20:
        ys, xs = np.nonzero(mask & ~inside)
        out.append(f"{stray} candle pixels outside the chart box (top y {int(ys.min())}, box top {y0})")
    if highlight:
        yellow = _near(rgb, motion_lib.ATTENTION, 40)
        # The key-candle box has vertical yellow edges; the trend arrow does not.
        cols = [x for x in range(x0, x1) if _longest_run(yellow[y0:y1, x]) >= 40]
        if not cols:
            out.append("no highlight box")
        else:
            hx0, hx1 = min(cols), max(cols)
            ys = np.nonzero(yellow[y0:y1, hx0])[0] + y0
            region = mask[int(ys.min()):int(ys.max()) + 1, hx0:hx1 + 1]
            if int(region.sum()) < 200:
                out.append(f"highlight box x {hx0}-{hx1} holds no candle")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--times", nargs="+", type=float, default=[6, 22, 36, 42])
    ap.add_argument("--highlight-time", type=float, action="append", default=[])
    ap.add_argument("--out-dir")
    args = ap.parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    bad = 0
    stem = Path(args.video).stem
    for t in sorted(set(args.times) | set(args.highlight_time)):
        dest = (out_dir or Path("/tmp")) / f"{stem}-{t:g}s.png"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", args.video,
             "-frames:v", "1", str(dest)], check=True,
        )
        rgb = np.asarray(Image.open(dest).convert("RGB"))
        faults = faults_for(rgb, highlight=t in args.highlight_time)
        print(f"[frames] {t:g}s {'PASS' if not faults else 'FAIL: ' + '; '.join(faults)} -> {dest}")
        bad += bool(faults)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
