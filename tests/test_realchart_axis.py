import shutil

import pytest

from realchart import axis, level_lines
from realchart.capture import ema_status
from realchart import script


def _ticks(a=-0.5, b=11800.0, prices=range(22400, 23200, 50)):
    return [(float(p), a * p + b) for p in prices]


def test_fit_recovers_linear_map():
    m = axis.fit(_ticks())
    assert m and abs(m["a"] + 0.5) < 1e-6 and m["residual"] < 0.01
    assert abs(axis.y_of(m, 22800) - (-0.5 * 22800 + 11800)) < 0.01


def test_fit_drops_ocr_outlier():
    ticks = _ticks()
    ticks[3] = (2265.0, ticks[3][1])  # OCR lost a digit
    m = axis.fit(ticks)
    assert m and m["residual"] <= 2.0
    assert all(p != 2265.0 for p, _ in m["ticks"])


def test_fit_rejects_inverted_or_noisy_axis():
    assert axis.fit([(p, 0.5 * p) for p in range(22400, 23000, 100)]) is None
    noisy = [(p, -0.5 * p + 11800 + (9 if i % 2 else -9)) for i, p in enumerate(range(22400, 23000, 50))]
    assert axis.fit(noisy) is None
    assert axis.fit(_ticks()[:1]) is None


def test_verify_two_ticks_within_2px():
    m = axis.fit(_ticks())
    assert axis.verify(m, m["ticks"])
    bad = dict(m, b=m["b"] + 5)
    assert not axis.verify(bad, m["ticks"])


def test_level_lines_drawn_without_touching_candles_outside_lines():
    from PIL import Image
    img = Image.new("RGB", (1000, 800), (20, 20, 20))
    m = {"a": -1.0, "b": 23200.0, "residual": 0.0, "ticks": []}
    levels = {"pivot": 22800, "r1": 22900, "s1": 22700, "r2": 23300, "s2": 22600,
              "sl_zone": [22550, 22580], "target_zone": [22950, 22980]}
    res = level_lines.draw(img, levels, m)
    assert "P" in res["drawn"] and "R2" in res["off_scale"]
    assert img.getpixel((5, 5)) == (20, 20, 20)


def test_ema_status_needs_values():
    assert ema_status("EMA 20 close 22,865.10\nEMA 50 close 22,700.40") == {"ema20": True, "ema50": True}
    assert ema_status("EMA 20 close\nEMA 50 close") == {"ema20": False, "ema50": False}


def test_close_growth_items():
    lv = {"symbol": "NSE:NIFTY", "last_date": "2026-10-15", "close": 22997.35, "high": 23056.0,
          "low": 22637.0, "chg_pct": 1.66}
    lines = script.end_card_lines("close", lv)
    assert lines[0] == "Kal ka key level: 22,897 (Pivot)"
    assert script.FOLLOW_LINE in lines
    assert script.post_meta(lv)["title"].startswith("Nifty analysis today | Nifty Close Report")


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract missing")
def test_ocr_reads_rendered_axis():
    from PIL import Image, ImageDraw
    from realchart.fonts import font
    img = Image.new("RGB", (1280, 900), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f = font(22)
    for i, p in enumerate(range(22400, 23200, 100)):
        y = 820 - i * 100
        label = f"{p:,}.00"
        top, bottom = d.textbbox((0, 0), label, font=f)[1::2]
        d.text((1180, y - (top + bottom) / 2), label, fill=(30, 30, 30), font=f)
    m = axis.map_axis_image(img)
    assert m is not None
    assert abs(axis.y_of(m, 22700) - 520) <= 2.0
