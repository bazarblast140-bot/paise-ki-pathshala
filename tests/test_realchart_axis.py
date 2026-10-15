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


def test_variants_keep_every_still_under_3s(tmp_path):
    import json
    from realchart.build import _with_variants
    a, z = tmp_path / "chart.png", tmp_path / "z.png"
    a.write_bytes(b"x"); z.write_bytes(b"x")
    (tmp_path / "v.json").write_text(json.dumps({"chart.png": [[str(z), 1.0]]}))
    pics, beats = _with_variants([a], [10.0], tmp_path / "v.json")
    assert max(beats) <= 3.0 and abs(sum(beats) - 10.0) < 1e-6 and len(set(map(str, pics))) == 2


def test_box_mode_uses_saved_layout(monkeypatch):
    from realchart import capture
    monkeypatch.setattr(capture, "TV_LAYOUT", "ckfLnl7v")
    assert capture.chart_url("NSE:NIFTY", "5").startswith("https://www.tradingview.com/chart/ckfLnl7v/")
    assert capture.ema_status("EMA\n20\nclose\n22,865.36\nEMA\n50\nclose\n23,320.79") == {"ema20": True, "ema50": True}


def test_narration_numbers_are_whole():
    import re
    assert script.whole_numbers("RSI 50.00 पर, 1.66 प्रतिशत, 22,997.35") == "RSI 50 पर, 2 प्रतिशत, 22997"
    lv = {"symbol": "NSE:NIFTY", "last_date": "2026-10-15", "close": 22997.35, "prev_close": 22622.7,
          "open": 22671.3, "high": 23056.4, "low": 22637.4, "chg_pct": 1.6561, "rsi14": 50.0, "ema20": 22865.36,
          "ema50": 23320.79, "pivot": 22646.53, "r1": 22734.12, "s1": 22535.12, "r2": 22845.53, "s2": 22447.53,
          "sl_zone": [22040.0, 22179.9], "target_zone": [22757.95, 22865.36]}
    for kind in ("close", "ta", "recap"):
        for row in script.chunks_for(kind, dict(lv), None):
            assert not re.search(r"\d\.\d", row["t"]), (kind, row["t"])


def test_cards_follow_real_segment_audio(tmp_path):
    from realchart.voice import SEGMENT_GAP_SEC, speak_segments, wav_seconds
    info, beats = speak_segments(["एक दो तीन", "", "चार पाँच छह सात आठ नौ दस"], tmp_path / "voice.wav", "close", dry_run=True)
    assert len(beats) == 3 and beats[1] == 0.8 + SEGMENT_GAP_SEC
    assert abs(wav_seconds(tmp_path / "voice.wav") - sum(beats)) < 0.05


def test_single_black_frame_is_caught():
    from realchart.video_qa import dip_faults
    lumas = [(i / 30, 160.0) for i in range(60)]
    lumas[30] = (1.0, 3.0)
    assert dip_faults(lumas, "x")
    assert not dip_faults([(i / 30, 160.0 if i < 30 else 120.0) for i in range(60)], "x")
