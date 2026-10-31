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


def test_box_gate_refuses_anything_but_todays_signed_in_pass():
    from override_mp4 import box_gate
    good = {"verdict": "PASS", "capture_mode": "box-signed-in", "ema_ok": True, "session": "2026-10-16"}
    assert box_gate(good, today="2026-10-16") == ""
    assert box_gate(dict(good, verdict="HOLD"), today="2026-10-16")
    assert box_gate(dict(good, capture_mode="anonymous"), today="2026-10-16")
    assert box_gate(dict(good, ema_ok=False), today="2026-10-16")
    assert box_gate(good, today="2026-10-17")


def test_runner_capture_is_a_qa_fault(monkeypatch):
    from realchart import build, capture
    monkeypatch.setattr(capture, "CDP_URL", "")
    faults = build._ta_faults("/nonexistent", "close", {"source": "yfinance"}, None)
    assert any("signed-in box" in f for f in faults) and any("not a TradingView capture" in f for f in faults)


def test_rsi_only_with_pane_and_series_number_in_title():
    lv = {"symbol": "NSE:NIFTY", "last_date": "2026-10-16", "close": 23000.0, "prev_close": 22900.0, "high": 23050.0,
          "low": 22800.0, "chg_pct": 0.44, "rsi14": 55.0, "ema20": 22900.0, "pivot": 22950.0, "r1": 23100.0,
          "s1": 22850.0}
    text = " ".join(r["t"] for r in script.chunks_for("close", dict(lv), None))
    assert "RSI" not in text
    text = " ".join(r["t"] for r in script.chunks_for("close", dict(lv, rsi_pane_captured=True), None))
    assert "RSI 55" in text
    assert "Nifty Close Report #7 16 Oct 2026" in script.post_meta(dict(lv, series_n=7))["title"]


def test_series_number_from_box_file(tmp_path, monkeypatch):
    from realchart import build
    f = tmp_path / "series.json"
    monkeypatch.setenv("REALCHART_SERIES_FILE", str(f))
    assert build._series_next("close") == 1
    f.write_text('{"close": 4}')
    assert build._series_next("close") == 5 and build._series_next("news") is None


def test_stock_tip_articles_are_not_news():
    from realchart.build import about_indian_market
    assert not about_indian_market({"title": "Raja Venkatraman recommends three stocks for 16 October Nifty"})
    assert about_indian_market({"title": "Nifty ends 1.6% higher as banks rally"})


def test_hindi_summary_numbers_must_come_from_the_article():
    from realchart.summarize import check
    art = {"title": "Company X to raise Rs 2,534 crore via IPO", "summary": "Promoter stake falls from 100% to 77%."}
    ok = {"lead": "Company X IPO से 2534 करोड़ जुटाएगी।", "points": ["प्रमोटर हिस्सेदारी 100 से 77 प्रतिशत होगी।", "यह offer for sale है।"]}
    assert check(ok, art) == []
    bad = dict(ok, points=["प्रमोटर हिस्सेदारी 60 प्रतिशत होगी।", "यह offer for sale है।"])
    assert any("60" in f for f in check(bad, art))
    assert check(dict(ok, lead="Company X raises money"), art)


def test_news_rows_have_no_nifty_levels_and_hindi_voice():
    lv = {"symbol": "NEWS", "last_date": "2026-10-23"}
    art = {"title": "Jio Platforms IPO subscribed 5.57 times", "source": "www.livemint.com",
           "key_sentence": "The issue was subscribed 5.57 times.",
           "hindi": {"headline": "Jio IPO को ज़बरदस्त रिस्पॉन्स", "lead": "Jio IPO दूसरे दिन भरा।", "points": ["एक।", "दो।"]}}
    rows = script.chunks_for("news", lv, art)
    assert [r["sec"] for r in rows] == ["hook", "news", "points", "key", "cta"]
    spoken = script.spoken_text(rows)
    assert "Jio IPO को ज़बरदस्त रिस्पॉन्स" in rows[0]["t"] and "Nifty" not in spoken and "pivot" not in spoken
    assert script.lint(rows) == [] and script.number_faults(rows, lv) == []
    assert script.thumb_text("news", lv, art) == "Jio IPO को ज़बरदस्त रिस्पॉन्स"
    assert script.end_card_lines("news", lv) and not any("Pivot" in x for x in script.end_card_lines("news", lv))
    meta = script.news_post_meta(lv, art)
    assert len(meta["title"]) <= 100 and "Nifty" not in meta["caption"]


def test_biggest_story_is_last_24h_and_cross_source():
    from datetime import datetime, timedelta, timezone
    from realchart.news import biggest
    now = datetime(2026, 10, 23, 8, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    fresh = "Fri, 23 Oct 2026 06:00:00 +0530"
    items = [
        {"title": "Nvidia shares surge to record on AI chip demand", "url": "https://a.com/1", "published": fresh, "source": "a.com"},
        {"title": "Nvidia record: AI chip demand lifts shares", "url": "https://b.com/1", "published": fresh, "source": "b.com"},
        {"title": "Bitcoin dips slightly", "url": "https://c.com/1", "published": fresh, "source": "c.com"},
        {"title": "Stocks in news: A, B, C, D", "url": "https://d.com/1", "published": fresh, "source": "d.com"},
        {"title": "Fed crash plunge tariff record trillion", "url": "https://e.com/1", "published": "Mon, 19 Oct 2026 06:00:00 +0530", "source": "e.com"},
    ]
    best = biggest(items, now)
    assert best and "Nvidia" in best["title"]


def test_cards_keep_text_in_the_safe_zone():
    from realchart import render
    assert render.CAP[3] < 1440 and render.FOOT_Y + 30 < 1440 and render.CAP[2] <= 940


def test_news_card_never_cuts_the_headline(tmp_path):
    from realchart import render
    head = "Stock market outlook today, 16 Oct: Sensex, Nifty prediction - DJIA, S&P, NASDAQ, GIFT Nifty, Kospi, Nikkei, Taiwan cues and more global signals"
    del render.DRAWN[:]
    render.news_card(tmp_path / "cards", {"title": head, "source": "x", "key_sentence": "A b c."})
    shown = " ".join(render.DRAWN)
    assert "…" not in shown and "signals" in shown


def test_key_files_beyond_four_are_used(tmp_path, monkeypatch):
    import gemini_voice
    for n in ["", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9"]:
        monkeypatch.delenv(f"GEMINI_API_KEY{n}", raising=False)
    (tmp_path / "gemini_api_key_4").write_text("k4")
    (tmp_path / "gemini_api_key_6").write_text("k6")
    monkeypatch.setenv("GEMINI_SECRETS_DIR", str(tmp_path))
    assert gemini_voice.api_keys() == [("4", "k4"), ("6", "k6")]


def test_gemini_out_falls_back_to_grok_for_the_whole_reel(tmp_path, monkeypatch):
    import gemini_voice
    from realchart import voice
    calls = []

    def fake_speak(text, dest, kind, dry_run=False, cache_key=None, engine="gemini"):
        calls.append(engine)
        if engine == "gemini" and len(calls) > 1:
            raise gemini_voice.GeminiFallback("quota", status=429)
        voice._silence(dest, 1.0)
        return {"engine": engine, "requests": 1}

    monkeypatch.setattr(voice, "speak", fake_speak)
    info, beats = voice.speak_segments(["एक", "दो"], tmp_path / "v.wav", "close")
    assert info["engine"] == "grok" and calls == ["gemini", "gemini", "grok", "grok"] and len(beats) == 2


def test_grok_failure_keeps_the_gemini_error(tmp_path, monkeypatch):
    import gemini_voice
    import pytest
    from realchart import voice

    def fake_speak(text, dest, kind, dry_run=False, cache_key=None, engine="gemini"):
        if engine == "gemini":
            raise gemini_voice.GeminiFallback("quota", status=429)
        if engine == "edge":
            raise voice.EdgeUnavailable("offline")
        raise voice.GrokUnavailable("xAI TTS HTTP 403")

    monkeypatch.setattr(voice, "speak", fake_speak)
    with pytest.raises(gemini_voice.GeminiFallback):
        voice.speak_segments(["एक"], tmp_path / "v.wav", "close")


def test_gemini_thumbnail_falls_back_and_never_asks_for_charts(tmp_path):
    import urllib.error
    from realchart import thumbgen

    def failing(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, None)

    assert thumbgen.gemini_background("Jio IPO", tmp_path / "bg.png", keys=["k"], opener=failing) is None
    prompt = thumbgen.prompt_for("Jio IPO").lower()
    assert "no charts" in prompt and "no text" in prompt


def test_top_two_stories_are_distinct_and_rows_fit_voice_budget():
    from datetime import datetime, timedelta, timezone
    from realchart.news import top_stories
    from realchart.script import _news_rows
    now = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    t = "Fri, 23 Oct 2026 11:00:00 +0530"
    items = [
        {"title": "Nvidia AI chip stock surges on record demand", "url": "https://a.com/1", "source": "a.com", "published": t},
        {"title": "Nvidia AI chip stock surges after record demand", "url": "https://b.com/1", "source": "b.com", "published": t},
        {"title": "Bitcoin crypto price jumps as ETF inflows rise", "url": "https://c.com/1", "source": "c.com", "published": t},
    ]
    top = top_stories(items, now, 2)
    assert len(top) == 2 and top[0]["source"] != top[1]["source"] and "Bitcoin" in top[1]["title"]
    hindi = {"headline": "एक", "lead": "दो", "points": ["क", "ख", "ग"]}
    article = {**top[0], "hindi": hindi, "second": {**top[1], "hindi": hindi}}
    rows = _news_rows({}, article)
    assert [r["sec"] for r in rows] == ["hook", "news", "points", "key", "news2", "points2", "cta"]
    assert len(rows) <= 7  # one Gemini voice request per row


def test_box_gate_skips_ema_only_for_article_news():
    from override_mp4 import box_gate
    news = {"verdict": "PASS", "capture_mode": "box-signed-in", "session": "2026-11-01",
            "kind": "news", "chart": "article", "ema_ok": False}
    assert box_gate(news, today="2026-11-01") == ""
    assert box_gate(dict(news, ema_ok=None), today="2026-11-01") == ""
    assert box_gate(dict(news, chart="tradingview"), today="2026-11-01")
    assert box_gate(dict(news, kind="ta", chart="tradingview"), today="2026-11-01")
    assert box_gate(dict(news, verdict="HOLD"), today="2026-11-01")
    assert box_gate(dict(news, session="2026-10-31"), today="2026-11-01")
