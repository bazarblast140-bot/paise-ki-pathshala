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
    faults += single_frame_faults(video, label, panel=(label == "final.mp4"))
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


SINGLE_BLACK_ARGS = "blackdetect=d=0:pix_th=0.10:pic_th=0.98"
PANEL = (20, 184, 1060, 1580)  # the chart panel of a card (x0, y0, x1, y1)


def frame_lumas(video, crop=None):
    """Mean luma of every decoded frame [(t, yavg)], optionally inside crop (x0,y0,x1,y1)."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        log = handle.name
    vf = ""
    if crop:
        x0, y0, x1, y1 = crop
        vf = f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0},"
    vf += f"signalstats,metadata=print:key=lavfi.signalstats.YAVG:file={log}"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", vf, "-an", "-f", "null", "-"],
                   capture_output=True)
    out, t = [], None
    for line in Path(log).read_text(encoding="utf-8", errors="ignore").splitlines():
        hit = re.search(r"pts_time:([0-9.]+)", line)
        if hit:
            t = float(hit.group(1))
        elif "YAVG=" in line and t is not None:
            out.append((t, float(line.split("=")[1])))
    Path(log).unlink(missing_ok=True)
    return out


def dip_faults(lumas, label, floor=18.0, drop=0.5):
    """Single frames (or 2-3 frame blips) far darker than the frames on both sides."""
    faults = []
    for i in range(1, len(lumas) - 1):
        t, y = lumas[i]
        j = i
        while j + 1 < len(lumas) - 1 and j - i < 3 and abs(lumas[j + 1][1] - y) < 2:
            j += 1
        before, after = lumas[i - 1][1], lumas[j + 1][1] if j + 1 < len(lumas) else lumas[i - 1][1]
        if y < floor and before > y + 20 and after > y + 20 or (before > 40 and after > 40 and y < drop * min(before, after)):
            faults.append(f"black frame {label} at {t:.2f}s (luma {y:.0f} between {before:.0f} and {after:.0f})")
    return faults


def single_frame_faults(video, label, panel=True):
    """Catch one-frame black flashes: whole frame (blackdetect d=0) and inside the chart panel."""
    faults = [f"black frame {label} {a:.2f}-{b:.2f}s" for a, b in black_spans(video, SINGLE_BLACK_ARGS)]
    if panel:
        faults += dip_faults(frame_lumas(video, PANEL), f"{label} chart panel")
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
    if outro_d > 2.0:
        faults.append(f"ig: outro {outro_d:.2f}s, keep it under 2s")
    if static_seconds(outro_rtt) > 0.5:
        faults.append("ig: outro is a static frame (needs motion)")
    dead = trailing_silence(ig)
    # A dry run has no voice at all; dead air is a silent tail after real audio.
    if 2.0 < dead < got - intro_d - 1.0:
        faults.append(f"ig: {dead:.1f}s of silence at the end (dead air)")
    faults.extend(f"outro: {fault}" for fault in tofu_faults([outro_text]))
    return faults


def trailing_silence(path, floor_db=-45):
    """Seconds of silence running to the end of the file."""
    import subprocess
    from realchart.qa import probe
    total = probe(path).get("duration") or 0.0
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", f"silencedetect=n={floor_db}dB:d=0.3", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    if not starts or (ends and len(ends) == len(starts) and ends[-1] < total - 0.1):
        return 0.0
    return max(0.0, total - starts[-1])


def static_seconds(path):
    """Longest run of identical frames, in seconds."""
    import subprocess
    log = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "mpdecimate=hi=64:lo=32:frac=1,showinfo", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", log)]
    from realchart.qa import probe
    total = probe(path).get("duration") or 0.0
    times.append(total)
    return max((b - a for a, b in zip(times, times[1:])), default=total)


def sentence_faults(article):
    """The highlighted key sentence must not end mid-clause (feed truncation)."""
    from realchart.news import truncated
    sentence = str((article or {}).get("key_sentence") or "")
    return [f"news: key sentence is cut off: ...{sentence[-40:]}"] if sentence and truncated(sentence) else []


NEAR_BLACK_LUMA = 20
MAX_NEAR_BLACK = 0.20


def near_black_ratio(video, at):
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=270:480", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
    )
    data = proc.stdout or b""
    if len(data) < 270 * 480:
        return 1.0
    return sum(1 for value in data[: 270 * 480] if value < NEAR_BLACK_LUMA) / float(270 * 480)


def fill_faults(video, label, skip_until=0.0, step=0.5):
    """Fail when over 20% of a frame is near-black (letterbox or void), outside the intro."""
    from realchart.qa import probe
    video = Path(video)
    if not video.is_file():
        return [f"{label}: missing"]
    duration = float(probe(video).get("duration") or 0.0)
    worst, at = 0.0, None
    t = skip_until + 0.1
    while t < duration - 0.05:
        ratio = near_black_ratio(video, t)
        if ratio > worst:
            worst, at = ratio, t
        t += step
    if worst > MAX_NEAR_BLACK:
        return [f"fill: {label} is {100 * worst:.0f}% near-black at {at:.2f}s (max {100 * MAX_NEAR_BLACK:.0f}%)"]
    return []


def crosshair_faults(charts):
    from realchart.capture import crosshair_visible
    return [f"crosshair visible in {Path(path).name}" for path in charts if crosshair_visible(path)]


def capture_faults(capture_dates, expected):
    faults = []
    for what, got in capture_dates.items():
        if str(got or "")[:10] != str(expected):
            faults.append(f"capture date: {what} last candle {got or 'unknown'} is not {expected}")
    return faults


def run_all(out, captions, capture_dates, expected_date, intro, outro_rtt, outro_text, charts=()):
    from realchart.fmt import raw_float_faults
    from realchart.fonts import tofu_faults
    out = Path(out)
    faults = []
    faults += empty_frame_faults(out / "final.mp4", "final.mp4")
    faults += empty_frame_faults(out / "final_ig.mp4", "final_ig.mp4")
    from realchart.qa import probe
    intro_len = float(probe(intro).get("duration") or 0.0) if Path(intro).is_file() else 0.0
    faults += fill_faults(out / "final_ig.mp4", "final_ig.mp4", skip_until=intro_len)
    faults += crosshair_faults(charts)
    faults += tofu_faults(captions)
    faults += raw_float_faults(captions)
    faults += capture_faults(capture_dates, expected_date)
    faults += ends_faults(out, intro, outro_rtt, outro_text)
    (out / "video_qa.json").write_text(json.dumps({
        "faults": faults, "expected_date": str(expected_date), "capture_dates": capture_dates,
        "captions_checked": len(captions),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return faults
