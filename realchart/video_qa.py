"""Pre-publish video QA. Any fault is a HOLD; publish only runs on PASS.

Gates (15-Oct close Reel): black or empty frames, tofu captions, a chart
whose last candle is not today, raw floats on screen, and an IG version
without Rajesh's intro and outro card (or with a loud tone at the end).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# pic_th 0.90 flags frames that are 90% near-black. That alone also flags a
# dark TradingView chart, so each flagged span is then sampled: a frame is
# empty when under EMPTY_DETAIL_PCT of its pixels are bright or colourful.
# 15-Oct bad Reel: 0.5-3.8%. 14-Oct box sample: 6.4-6.7%.
BLACK_ARGS = "blackdetect=d=0.5:pix_th=0.10:pic_th=0.90"
HARD_BLACK_ARGS = "blackdetect=d=0.5:pix_th=0.10:pic_th=0.98"
EMPTY_DETAIL_PCT = 5.0
OUTRO_MAX_MEAN_DB = -45.0
SPAN = re.compile(r"black_start:([0-9.]+) black_end:([0-9.]+)")


def black_spans(video, args=BLACK_ARGS):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-vf", args, "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [(float(a), float(b)) for a, b in SPAN.findall(proc.stderr or "")]


def detail_pct(video, at):
    """Percent of pixels that are bright (luma > 60) or saturated (max-min > 60)."""
    from PIL import Image
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=270:480", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    data = proc.stdout or b""
    if len(data) < 270 * 480 * 3:
        return 0.0
    image = Image.frombytes("RGB", (270, 480), data[: 270 * 480 * 3])
    busy = 0
    raw = image.tobytes()
    for r, g, b in zip(raw[0::3], raw[1::3], raw[2::3]):
        luma = (299 * r + 587 * g + 114 * b) / 1000
        if luma > 60 or max(r, g, b) - min(r, g, b) > 60:
            busy += 1
    return 100.0 * busy / (270 * 480)


def empty_frame_faults(video, label):
    faults = []
    video = Path(video)
    if not video.is_file():
        return [f"{label}: missing"]
    for start, end in black_spans(video, HARD_BLACK_ARGS):
        faults.append(f"black frames {label} {start:.2f}-{end:.2f}s")
    for start, end in black_spans(video):
        if any(a <= start + 0.01 and end <= b + 0.01 for a, b in black_spans(video, HARD_BLACK_ARGS)):
            continue
        t = start + 0.25
        while t < end:
            pct = detail_pct(video, t)
            if pct < EMPTY_DETAIL_PCT:
                faults.append(f"black/empty frames {label} {start:.2f}-{end:.2f}s (detail {pct:.1f}% at {t:.2f}s)")
                break
            t += 0.5
    return faults


def mean_volume_db(video, start=None):
    cmd = ["ffmpeg", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    cmd += ["-i", str(video), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"mean_volume:\s*(-?[0-9.]+|-inf) dB", proc.stderr or "")
    if not match or match.group(1) == "-inf":
        return -120.0
    return float(match.group(1))


def ends_faults(out, intro, outro_rtt, outro_text):
    """IG = Rajesh intro + main + Rajesh outro card. The outro is silent and readable."""
    from realchart.fonts import tofu_faults
    from realchart.qa import probe
    out = Path(out)
    ig, main = out / "final_ig.mp4", out / "final.mp4"
    faults = []
    if not ig.is_file():
        return ["ig: final_ig.mp4 missing"]
    for name, path in (("intro_rtt.mp4", intro), ("outro_rtt.mp4", outro_rtt)):
        if not Path(path).is_file():
            faults.append(f"ig: {name} missing")
    if faults:
        return faults
    got = probe(ig).get("duration") or 0.0
    want = sum(probe(p).get("duration") or 0.0 for p in (intro, main, outro_rtt))
    intro_d = probe(intro).get("duration") or 0.0
    outro_d = probe(outro_rtt).get("duration") or 0.0
    if intro_d < 1.0:
        faults.append("ig: intro missing")
    if outro_d < 1.0:
        faults.append("ig: outro card missing")
    if abs(got - want) > 0.3:
        faults.append(f"ig: duration {got:.2f}s is not intro+main+outro {want:.2f}s")
    listing = out / "final_ig.txt"
    if listing.is_file():
        parts = listing.read_text(encoding="utf-8").splitlines()
        if len(parts) != 3 or "intro_rtt" not in parts[0] or "outro_rtt" not in parts[-1]:
            faults.append("ig: concat list is not intro, main, outro")
    tail = mean_volume_db(ig, start=got - outro_d + 0.05)
    if tail > OUTRO_MAX_MEAN_DB:
        faults.append(f"ig: outro audio {tail:.1f} dB mean (tone/beep), expected silence")
    faults.extend(f"outro: {fault}" for fault in tofu_faults([outro_text]))
    return faults


def capture_faults(capture_dates, expected):
    faults = []
    for what, got in capture_dates.items():
        if str(got or "")[:10] != str(expected):
            faults.append(f"capture date: {what} last candle {got or 'unknown'} is not {expected}")
    return faults


def run_all(out, captions, capture_dates, expected_date, intro, outro_rtt, outro_text):
    from realchart.fmt import raw_float_faults
    from realchart.fonts import tofu_faults
    out = Path(out)
    faults = []
    faults += empty_frame_faults(out / "final.mp4", "final.mp4")
    faults += empty_frame_faults(out / "final_ig.mp4", "final_ig.mp4")
    faults += tofu_faults(captions)
    faults += raw_float_faults(captions)
    faults += capture_faults(capture_dates, expected_date)
    faults += ends_faults(out, intro, outro_rtt, outro_text)
    (out / "video_qa.json").write_text(json.dumps({
        "faults": faults, "expected_date": str(expected_date), "capture_dates": capture_dates,
        "captions_checked": len(captions),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return faults
