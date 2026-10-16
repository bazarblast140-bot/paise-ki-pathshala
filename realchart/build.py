"""Build one Reel into a directory. Publish stays in the workflow."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import nifty_day
from realchart import capture, script
from realchart.levels import UNIVERSE, compute, pick_setup
from realchart.news import FEEDS, choose, parse_rss
from realchart.session import close_matches, dry_session, expected_session, now_ist, started

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "realchart_history.json"


class Hold(Exception):
    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _history():
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("items") or [])


def _save_history(items):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps({"items": items[-400:]}, indent=2) + "\n", encoding="utf-8")


def _bars_from_yahoo(ticker):
    import yfinance as yf
    frame = yf.download(ticker, period="3y", interval="1d", progress=False, auto_adjust=False)
    if frame is None or frame.empty:
        return [], None
    if hasattr(frame.columns, "levels"):
        try:
            frame.columns = frame.columns.get_level_values(0)
        except Exception:  # noqa: BLE001
            pass
    rows = []
    stamp = None
    for index, row in frame.dropna(subset=["Close"]).iterrows():
        rows.append({
            "date": str(getattr(index, "date", lambda: index)()),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
        })
        stamp = index
    return rows, stamp


def _load_feeds(loader=None):
    if loader is not None:
        return loader()
    import urllib.request
    items = []
    for url in FEEDS:
        source = url.split("/")[2]
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                xml = resp.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        items.extend(parse_rss(xml, source))
    return items


def _on_session(rows, stamp, session, now, dry_run):
    """Keep the daily bar for this session. A later partial bar is dropped on a dry run."""
    if close_matches(stamp, session, now):
        return rows, stamp
    if not dry_run:
        return None, None
    kept = [row for row in rows if str(row.get("date") or "")[:10] <= session.isoformat()]
    if not kept or str(kept[-1].get("date") or "")[:10] != session.isoformat():
        return None, None
    closed = datetime(session.year, session.month, session.day, 15, 30, tzinfo=now_ist().tzinfo)
    return kept, closed


def run(kind, out, now=None, dry_run=False, bars_for=None, articles=None, chart_opener=None, article_opener=None, bars_intraday=None, chain_for=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    now = now_ist(now)
    if not started(kind, now, dry_run=dry_run):
        print(f"[realchart] {kind} has not started yet; skipping", flush=True)
        (out / "SKIP").write_text(f"{kind} not started\n", encoding="utf-8")
        return 0
    session = dry_session(kind, now) if dry_run else expected_session(kind, now)
    if session is None:
        print(f"[realchart] {kind} has no session today; skipping", flush=True)
        (out / "SKIP").write_text("no session\n", encoding="utf-8")
        return 0
    try:
        levels, article, chart, intraday = _prepare(
            kind, session, now, out, bars_for=bars_for, articles=articles,
            chart_opener=chart_opener, article_opener=article_opener, dry_run=dry_run,
            bars_intraday=bars_intraday, chain_for=chain_for,
        )
        levels["series_n"] = _series_next(kind)
        rows = script.chunks_for(kind, levels, article)
        faults = script.lint(rows) + script.number_faults(rows, levels)
        if faults:
            raise Hold("script", "; ".join(faults))
        if kind == "news" and not dry_run:
            est = script.estimate_seconds(rows)
            if est < script.NEWS_FLOOR_SEC:
                raise Hold("script", f"news script estimate {est:.1f}s below {script.NEWS_FLOOR_SEC:.0f}s floor (article too short)")
        from realchart.voice import speak_segments
        # One clip per card: each card is on screen exactly while its own line is spoken.
        voice, beats = speak_segments(
            [str(row.get("t") or "") for row in rows], out / "voice.wav", kind, dry_run=dry_run,
            key_prefix=_voice_key(kind, levels, ""),
        )
        from realchart.render import assemble, frames
        pics = frames(out / "cards", kind, levels, rows, chart.get("path"), article, intraday)
        spoken = sum(beats)
        if spoken > 50.0 and dry_run:
            beats = [b * 45.0 / spoken for b in beats]  # silent placeholder voice; real reads are timed
            spoken = 45.0
        elif spoken > 50.0:
            raise Hold("voice", f"narration is {spoken:.1f}s, over the 50s limit")
        length = max(40.0, spoken)
        beats[-1] += length - spoken  # a short read holds the CTA card over the silent tail
        pics, beats = _with_variants(pics, beats, out / "variants.json")
        assemble(pics, out / "voice.wav", out / "final.mp4", seconds=length, durations=beats)
        from realchart.ends import FOLLOW_PKP, FOLLOW_RTT, attach, render_end_card
        ends_dir = out / "ends"
        extra = script.end_card_lines(kind, levels)
        outro_rtt = render_end_card(ends_dir / "outro_rtt.mp4", chart.get("path"), "Rajesh Technical Trader", "follow करो", extra=extra)
        outro_pkp = render_end_card(ends_dir / "outro_pkp.mp4", chart.get("path"), "Paise Ki Pathshala", "follow करो", extra=extra)
        try:
            attach(out / "final.mp4", out, outro_rtt=outro_rtt, outro_pkp=outro_pkp)
        except FileNotFoundError as exc:
            raise Hold("ends", str(exc)) from exc
        _write_sources(out, kind, levels, article, chart, rows, voice, now, intraday)
        from realchart.qa import check
        (out / "charts_used.json").write_text(json.dumps({"tradingview": [
            c.get("path") for c in (chart, intraday or {}) if c.get("source") == "tradingview" and c.get("path")
        ]}) + "\n", encoding="utf-8")
        capture_dates = {"daily chart": chart.get("last_candle_date") or ""}
        if intraday is not None:
            capture_dates["intraday chart"] = intraday.get("last_candle_date") or ""
        qa_faults = check(
            out, rows, levels, voice.get("engine"), dry_run=dry_run,
            capture_dates=capture_dates, expected_date=(levels.get("last_date") if kind == "news" else session.isoformat()),
        )
        qa_faults.extend(_ta_faults(out, kind, chart, intraday, dry_run=dry_run))
        _write_post_meta(out, kind, levels, rows)
        verdict = "DRY-OK" if dry_run and not qa_faults else ("PASS" if not qa_faults else "HOLD")
        report = {
            "verdict": verdict,
            "faults": qa_faults,
            "kind": kind,
            "chart": chart.get("source"),
            "label": chart.get("label"),
            "chart_reason": chart.get("reason") or "",
            "intraday_chart": (intraday or {}).get("source") or "",
            "intraday_interval": (intraday or {}).get("interval") or "",
            "intraday_label": (intraday or {}).get("label") or "",
            "intraday_chart_reason": (intraday or {}).get("reason") or "",
            "levels_chip": levels.get("levels_chip") or "",
            "oi_status": levels.get("oi_status") or "",
            "rr": levels.get("rr"),
            "cta_yt": FOLLOW_PKP,
            "cta_social": FOLLOW_RTT,
            "social_intro": "intro_rtt.mp4",
            "values_from": levels.get("values_from"),
            "voice": voice.get("engine"),
            "sync": "script-timeline",
            "hook": "0-3s",
            "capture_mode": "box-signed-in" if os.environ.get("REALCHART_CDP") else "anonymous",
            "ema_ok": all((c or {}).get("ema", {}).get("ema20") and (c or {}).get("ema", {}).get("ema50")
                          for c in (chart, intraday) if c is not None),
            "series_n": levels.get("series_n"),
            "session": session.isoformat(),
        }
        (out / "qa.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if qa_faults:
            raise Hold("qa", "; ".join(qa_faults))
        if kind == "ta" and not dry_run:
            items = _history()
            items.append({"symbol": levels.get("symbol"), "setup": levels.get("setup"), "date": str(now.date())})
            _save_history(items)
        print(
            f"[realchart] chart {chart.get('source')} {chart.get('label')} {chart.get('reason') or ''}".rstrip(),
            flush=True,
        )
        print(verdict, flush=True)
        return 0
    except Hold as exc:
        _hold(out, kind, exc.reason, exc.detail, now, dry_run=dry_run)
        print("HOLD", flush=True)
        print(f" - {exc.reason}: {exc.detail}", flush=True)
        return 2


def _download_intraday(ticker, session, interval):
    try:
        import yfinance as yf
        frame = yf.download(ticker, period="5d", interval=f"{interval}m", progress=False, auto_adjust=False)
    except Exception:  # noqa: BLE001
        return []
    if frame is None or getattr(frame, "empty", True):
        return []
    if hasattr(frame.columns, "levels"):
        try:
            frame.columns = frame.columns.get_level_values(0)
        except Exception:  # noqa: BLE001
            pass
    rows = []
    for index, row in frame.dropna(subset=["Close"]).iterrows():
        stamp = index
        try:
            if getattr(stamp, "tzinfo", None) is None:
                stamp = stamp.tz_localize("UTC")
            stamp = stamp.tz_convert(now_ist().tzinfo)
        except Exception:  # noqa: BLE001
            pass
        day = str(getattr(stamp, "date", lambda: stamp)())[:10]
        if day != session.isoformat():
            continue
        rows.append({
            "date": day,
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
        })
    return rows


def _prepare(kind, session, now, out, bars_for=None, articles=None, chart_opener=None, article_opener=None, dry_run=False, bars_intraday=None, chain_for=None):
    symbol, ticker = ("NSE:NIFTY", "^NSEI")
    if kind == "ta":
        candidates = []
        for name, yf in UNIVERSE:
            rows, stamp = (bars_for(yf) if bars_for else _bars_from_yahoo(yf))
            rows, stamp = _on_session(rows, stamp, session, now, dry_run)
            if not rows or len(rows) < 20 or stamp is None:
                continue
            levels = compute(rows, name, yf)
            candidates.append(levels)
        if not candidates:
            raise Hold("session", f"Yahoo close is not {session}")
        picked = pick_setup(candidates, _history(), now.date())
        if picked is None:
            raise Hold("scan", "no fresh setup in the last five days")
        levels = picked
    else:
        rows, stamp = (bars_for(ticker) if bars_for else _bars_from_yahoo(ticker))
        if kind in ("close", "ta", "recap"):
            rows, stamp = _on_session(rows, stamp, session, now, dry_run)
            if not rows or not close_matches(stamp, session, now):
                raise Hold("session", f"Yahoo close is not {session}")
        if not rows or len(rows) < 20:
            raise Hold("data", "Nifty bars missing")
        levels = compute(rows, symbol, ticker)
        if kind == "recap" and len(rows) > 5:
            week = float(rows[-6]["c"])
            levels["week_ago"] = week
            levels["chg_pct"] = 100.0 * (levels["close"] / week - 1.0) if week else levels["chg_pct"]
        levels["setup"] = "weekly recap" if kind == "recap" else "close"
    items = articles if articles is not None else _load_feeds()
    aliases = [levels["symbol"].split(":")[-1], "nifty", "sensex", "market"]
    article = choose(items, now, aliases)
    if kind != "news" and article and not about_indian_market(article):
        # A close/TA Reel only carries Nifty/Indian-market news (15-Oct: a Bank of
        # America story matched "market" in the livemint URL). Otherwise no news beat.
        article = None
    if kind == "news" and not article:
        raise Hold("news", "no qualifying article")
    day_expect = {
        "date": str(levels.get("last_date") or ""),
        "o": levels.get("open"), "h": levels.get("high"), "l": levels.get("low"), "c": levels.get("close"),
    }
    chart = capture.capture_chart(
        levels["symbol"], out / "chart.png", levels.get("bars") or [], opener=chart_opener, expect=day_expect,
    )
    # News runs at 08:20 before the open, so its last daily candle is the
    # previous session; it still must match the Yahoo bar (no stale capture).
    _require_today(chart, session if kind != "news" else None, "daily chart")
    _apply_technicals(levels, chart)
    if chart.get("reason"):
        levels["chart_reason"] = chart.get("reason")
    if article and article_opener is not None:
        shot = capture.capture_article(article["url"], out / "news.png", opener=article_opener)
        article["capture"] = shot
        if kind == "news" and not shot.get("ok"):
            raise Hold("news", shot.get("reason") or "article capture failed")
    elif article and article_opener is None and not os.environ.get("REALCHART_SKIP_BROWSER"):
        shot = capture.capture_article(article["url"], out / "news.png")
        article["capture"] = shot
        if kind == "news" and not shot.get("ok"):
            raise Hold("news", shot.get("reason") or "article capture failed")
    if article and kind != "news" and not (article.get("capture") or {}).get("ok"):
        # A news beat with no screenshot was an empty dark card (black frames
        # at 0:29-0:38 on 15 Oct). Close/TA drop the beat instead.
        article = None
    intraday = None
    if kind in ("close", "ta"):
        if bars_intraday is not None:
            bars_5m = list(bars_intraday(levels.get("yf") or "^NSEI", "5"))
            bars_15m = list(bars_intraday(levels.get("yf") or "^NSEI", "15"))
        else:
            bars_5m = _download_intraday(levels.get("yf") or "^NSEI", session, "5")
            bars_15m = _download_intraday(levels.get("yf") or "^NSEI", session, "15")
        if not bars_5m:
            raise Hold("capture", f"no 5m bars for {session}, intraday chart date cannot be verified")
        intraday = capture.capture_intraday(
            levels["symbol"], out, bars_5m, bars_15m, opener=chart_opener,
            expect5=_last_bar_expect(bars_5m), expect15=_last_bar_expect(bars_15m or bars_5m),
        )
        _require_today(intraday, session, "intraday chart")
    # News is the article, the highlighted sentence, and the chart. It has no
    # entry, stop, or target, so a failed reward-to-risk screen must not hold it.
    # Close is a market summary: it still screens and shows the pivot and OI
    # levels, but a reward-to-risk below 1.5 does not hold it (Rajesh, 15-Oct).
    # TA still holds before voice when no level clears 1.5.
    if kind != "news":
        from realchart.levels import apply_screen_levels, fetch_option_chain
        chain = chain_for(levels["symbol"]) if chain_for else fetch_option_chain(levels["symbol"], session)
        if not apply_screen_levels(levels, chain):
            if len(levels.get("bars") or []) < 2:
                raise Hold("levels", "fewer than two daily bars for pivot levels")
            if kind != "close":
                raise Hold("rr", "RR below 1.5 from pivot and OI levels")
    (out / "levels.json").write_text(json.dumps({k: v for k, v in levels.items() if k != "bars"}, indent=2) + "\n", encoding="utf-8")
    return levels, article, chart, intraday


INDIA_MARKET = re.compile(
    r"\b(nifty|sensex|bank\s*nifty|nse|bse|dalal street|d-street|sebi|rbi|"
    r"indian (stock|share|equity|equities|market|markets)|india'?s? (stock|equity) market|"
    r"fii|fpi|rupee)\b",
    re.I,
)


def about_indian_market(article):
    """Title or summary (not the URL) names Nifty, Sensex or the Indian market."""
    blob = " ".join(str(article.get(key) or "") for key in ("title", "summary", "key_sentence"))
    return bool(INDIA_MARKET.search(blob))


def _series_next(kind):
    """Next episode number from the box's series file (written by scripts/box_publish.py after a publish)."""
    path = os.environ.get("REALCHART_SERIES_FILE", "")
    if kind != "close" or not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return int(data.get(kind, 0)) + 1
    except FileNotFoundError:
        return 1
    except (OSError, ValueError, TypeError):
        return None


def _voice_key(kind, levels, text):
    """Cache per day AND per text: edited narration must never reuse old audio."""
    import hashlib
    digest = hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:10]
    return f"{kind}-{levels.get('last_date')}-{levels.get('symbol')}-{digest}"


MAX_SHOT_SEC = 3.0


def _with_variants(pics, beats, variants_file):
    """Expand each beat into its card + variant shots so no still holds longer than 3 s.

    variants.json maps card name -> [[path, weight], ...]; the card itself has weight 1.
    The sequence repeats (base, variants, base, ...) until every unit is <= 3 s.
    """
    try:
        variants = json.loads(Path(variants_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        variants = {}
    new_pics, new_beats = [], []
    for pic, sec in zip(pics, beats):
        seq = [[str(pic), 1.0]] + [v if isinstance(v, list) else [v, 1.0] for v in (variants.get(Path(pic).name) or [])
                                   if Path(v[0] if isinstance(v, list) else v).is_file()]
        reps = 1
        while True:
            units = seq * reps
            total = sum(w for _, w in units)
            if max(w for _, w in units) * sec / total <= MAX_SHOT_SEC or reps > 8:
                break
            reps += 1
        for path, weight in units:
            new_pics.append(Path(path))
            new_beats.append(sec * weight / total)
    return new_pics, new_beats


def _ta_faults(out, kind, chart, intraday, dry_run=False):
    """TradingView's own EMA 20/50 must show on every TradingView shot; level lines must be drawn."""
    faults = []
    if kind not in ("close", "ta"):
        return faults
    from realchart.capture import CDP_URL
    if not CDP_URL and not dry_run:
        # Only the box is signed in to TradingView. A runner capture has no EMA 20/50.
        faults.append("chart not captured from the signed-in box browser (REALCHART_CDP unset)")
    for label, shot in (("daily", chart or {}), ("5m", intraday or {})):
        if shot.get("source") != "tradingview" and not dry_run:
            faults.append(f"{label} chart is not a TradingView capture ({shot.get('source') or 'missing'})")
    for label, shot in (("daily", chart or {}), ("5m", intraday or {})):
        if shot.get("source") != "tradingview":
            continue
        ema = shot.get("ema") or {}
        missing = [name.upper().replace("EMA", "EMA ") for name in ("ema20", "ema50") if not ema.get(name)]
        if shot.get("drawings_visible"):
            faults.append(f"{label} TradingView capture shows the layout's own drawings (not today's levels)")
        if missing:
            faults.append(f"{label} TradingView capture has no {' / '.join(missing)} (TradingView indicator not rendered)")
    try:
        axis = json.loads((Path(out) / "axis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        axis = {}
    if (intraday or {}).get("source") == "tradingview":
        got = axis.get("intraday") or {}
        if not got.get("ok") or not got.get("drawn"):
            faults.append(f"5m level lines not drawn ({got.get('reason') or 'no level on scale'})")
    return faults


def _write_post_meta(out, kind, levels, rows):
    if kind != "close":
        return
    meta = script.post_meta(levels)
    (Path(out) / "post_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wav_seconds(path):
    from realchart.qa import probe
    if not Path(path).is_file():
        return 0.0
    return float(probe(path).get("duration") or 0.0)


def _last_bar_expect(bars, tail=4):
    rows = list(bars or [])
    if not rows:
        return None
    day = str(rows[-1].get("date") or "")[:10]
    picked = [
        {"o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"])}
        for r in rows[-tail:] if str(r.get("date") or "")[:10] == day
    ]
    return {"date": day, "bars": picked}


def _require_today(chart, session, what):
    """Fail closed: the chart's last candle must be this session (today IST on a live run)."""
    chart = chart or {}
    if chart.get("source") == "stale":
        raise Hold("capture", f"{what}: {chart.get('reason')}")
    got = str(chart.get("last_candle_date") or "")[:10]
    if session is None:
        return
    if chart.get("source") in ("tradingview", "yfinance") and got != session.isoformat():
        raise Hold("capture", f"{what}: last candle {got or 'unknown'} is not {session.isoformat()}")


def _apply_technicals(levels, chart):
    """Prefer a complete Technicals-page read. Otherwise keep the OHLC cross-check."""
    parsed = capture.parse_technicals((chart.get("technicals") or {}).get("text"))
    levels["chart_source"] = chart.get("source")
    if not parsed:
        levels["values_from"] = "yfinance"
        return
    levels.update(parsed)
    zone = list(levels.get("target_zone") or [None, None])
    zone[1] = parsed["ema20"]
    levels["target_zone"] = zone
    levels["values_from"] = "tradingview"


def _write_sources(out, kind, levels, article, chart, rows, voice, now, intraday=None):
    title = script.post_meta(levels)["title"] if kind == "close" else rows[0]["d"]
    cta_row = next((row for row in rows if row.get("sec") == "cta"), None)
    cta = str((cta_row or {}).get("t") or script.CTA).replace(script.DISCLAIMER, "").strip()
    body = "\n".join(row["d"] for row in rows if row.get("sec") != "key")
    script_txt = (
        f"TITLE\n{title}\n\nHOOK\n{rows[0]['t']}\n\nBODY\n{body}\n\n"
        f"CTA\n{cta}\n\nDISCLAIMER LINE\n{script.DISCLAIMER}\n\n"
        f"FACT LEDGER\n- {levels.get('symbol')} close {levels.get('close')} on {levels.get('last_date')}\n"
        f"- chart {chart.get('source')} {chart.get('label')}\n"
    )
    if article:
        script_txt += f"- {article.get('source')} {article.get('url')} {article.get('published')}\n"
    (out / "script.txt").write_text(script_txt, encoding="utf-8")
    (out / "script.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# source {kind}",
        f"- built {now.strftime('%Y-%m-%d %H:%M IST')}",
        f"- symbol {levels.get('symbol')} date {levels.get('last_date')} setup {levels.get('setup')}",
        f"- chart {chart.get('source')} {chart.get('url')} {chart.get('label')}",
        f"- chart reason {chart.get('reason') or 'none'}",
        f"- intraday {(intraday or {}).get('source') or 'none'} {(intraday or {}).get('interval') or ''} {(intraday or {}).get('label') or ''}".rstrip(),
        f"- intraday reason {(intraday or {}).get('reason') or 'none'}",
        f"- {levels.get('levels_chip') or ''}",
        f"- oi {levels.get('oi_status') or ''} rr {levels.get('rr')}",
        f"- values {levels.get('values_from')} (EMA/RSI stated as cards, not lines)",
        f"- voice {voice.get('engine')} requests {voice.get('requests')}",
        "- yt cta Paise Ki Pathshala follow करो",
        "- social cta Rajesh Technical Trader ko follow करो intro intro_rtt.mp4",
        "- sync script-timeline hook 0-3s",
    ]
    if article:
        lines.append(f"- news {article.get('url')} {article.get('published')} {article.get('title')}")
    else:
        lines.append("- news none")
    (out / "source.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _hold(out, kind, reason, detail, now, dry_run=False):
    payload = {"verdict": "HOLD", "kind": kind, "faults": [f"{reason}: {detail}"]}
    (out / "qa.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out / "HOLD").write_text(f"{reason}: {detail}\n", encoding="utf-8")
    if dry_run:
        return
    _alert(kind, reason, detail, now)


def _alert(kind, reason, detail, now):
    if os.environ.get("REALCHART_NO_ALERT", "").strip() in ("1", "true", "yes"):
        return
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not token or not repo:
        print("[realchart] health-alert skipped (no token)", flush=True)
        return
    import health_check
    title = f"[health-alert] realchart {kind} HOLD — {now.date().isoformat()}"
    body = (
        "<!-- health-alert -->\n"
        f"## {kind} Reel held\n\n"
        f"{reason}: {detail}\n\n"
        "No upload. Voice was not switched to Edge.\n"
    )
    result = health_check.sync_issue(
        health_check.RequestsHttp(), repo, token, title, body, True, health_check.secrets_from_env(os.environ),
    )
    print(f"[realchart] health-alert {result.action}", flush=True)

