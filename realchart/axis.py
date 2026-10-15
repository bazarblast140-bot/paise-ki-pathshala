"""Price -> pixel y from TradingView's own right price-axis labels.

The axis column is OCR'd (tesseract), each "22,980.00" label gives (price, y),
and a least-squares line y = a*price + b is fitted. The fit is accepted only
when every used tick sits within MAX_RESIDUAL_PX of the line and at least two
ticks agree, so a level line lands on the exact price the axis shows.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_RESIDUAL_PX = 2.0
LABEL = re.compile(r"^\d{1,3}(,\d{2,3})*\.\d{2}$")


def fit(ticks, max_residual=MAX_RESIDUAL_PX, min_share=0.6):
    """ticks: [(price, y)]. Returns {"a", "b", "residual", "ticks"} or None.

    A robust line (median of pairwise slopes) first, so one OCR misread
    (a lost digit, the coloured last-price box over a tick) cannot drag it;
    ticks off that line by more than max_residual are dropped, then the kept
    ticks are refitted by least squares. Needs >= 2 ticks and >= min_share of
    all ticks to agree, and a falling axis (higher price = smaller y).
    """
    points = sorted({(float(p), float(y)) for p, y in ticks})
    if len(points) < 2:
        return None
    slopes = sorted((y2 - y1) / (p2 - p1) for i, (p1, y1) in enumerate(points)
                    for p2, y2 in points[i + 1:] if p2 != p1)
    if not slopes:
        return None
    a0 = slopes[len(slopes) // 2]
    b0 = sorted(y - a0 * p for p, y in points)[len(points) // 2]
    kept = [(p, y) for p, y in points if abs(a0 * p + b0 - y) <= max_residual]
    if len(kept) < 2 or len(kept) < min_share * len(points):
        return None
    n = len(kept)
    mp = sum(p for p, _ in kept) / n
    my = sum(y for _, y in kept) / n
    var = sum((p - mp) ** 2 for p, _ in kept)
    if var == 0:
        return None
    a = sum((p - mp) * (y - my) for p, y in kept) / var
    b = my - a * mp
    worst = max(abs(a * p + b - y) for p, y in kept)
    if a >= 0 or worst > max_residual:
        return None
    return {"a": a, "b": b, "residual": worst, "ticks": kept}


def y_of(mapping, price):
    return mapping["a"] * float(price) + mapping["b"]


def verify(mapping, ticks, max_residual=MAX_RESIDUAL_PX):
    """Check two ticks (top and bottom) map back within max_residual px."""
    if not mapping or len(ticks) < 2:
        return False
    chosen = [min(ticks, key=lambda t: t[1]), max(ticks, key=lambda t: t[1])]
    return all(abs(y_of(mapping, p) - y) <= max_residual for p, y in chosen)


def axis_column(image):
    """Left x of the price-axis labels: the rightmost ~13% of the chart pane."""
    return int(image.width * 0.87)


def read_ticks(png):
    """OCR the price axis. Returns [(price, y_center)] in the screenshot's pixels."""
    from PIL import Image
    with Image.open(png) as opened:
        return read_ticks_image(opened.convert("RGB"))


def read_ticks_image(image):
    from PIL import ImageOps
    if not shutil.which("tesseract"):
        return []
    left = axis_column(image)
    crop = image.crop((left, 0, image.width, int(image.height * 0.97)))
    scale = 2
    gray = ImageOps.invert(ImageOps.grayscale(crop)).resize((crop.width * scale, crop.height * scale))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "axis.png"
        gray.save(src)
        proc = subprocess.run(
            ["tesseract", str(src), "-", "--psm", "11", "-c", "tessedit_char_whitelist=0123456789.,", "tsv"],
            capture_output=True, text=True,
        )
    ticks = []
    for line in (proc.stdout or "").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12:
            continue
        text = cols[11].strip()
        if not LABEL.match(text):
            continue
        top, height = int(cols[7]), int(cols[9])
        ticks.append((float(text.replace(",", "")), (top + height / 2) / scale))
    return ticks


def map_axis(png):
    ticks = read_ticks(png)
    mapping = fit(ticks)
    if mapping and verify(mapping, mapping["ticks"]):
        mapping["axis_x"] = None
        return mapping
    return None


def map_axis_image(image):
    ticks = read_ticks_image(image)
    mapping = fit(ticks)
    if mapping and verify(mapping, mapping["ticks"]):
        return mapping
    return None
