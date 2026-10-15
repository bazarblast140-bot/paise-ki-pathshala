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
        rows = script.chunks_for(kind, levels, article)
        faults = script.lint(rows) + script.number_faults(rows, levels)
        if faults:
            raise Hold("script", "; ".join(faults))
        if kind == "news" and not dry_run:
            est = script.estimate_seconds(rows)
            if est < script.NEWS_FLOOR_SEC:
                raise Hold("script", f"news script estimate {est:.1f}s below {script.NEWS_FLOOR_SEC:.0f}s floor (article too short)")
        from realchart.voice import speak
        voice = speak(script.spoken_text(rows), out / "voice.wav", kind, dry_run=dry_run, cache_key=f"{kind}-{levels.get('last_date')}-{levels.get('symbol')}")
        from realchart.render import assemble, beat_seconds, frames
        pics = frames(out / "cards", kind, levels, rows, chart.get("path"), article, intraday)
        length = min(50.0, max(40.0, _wav_seconds(out / "voice.wav") or 45.0))
        assemble(pics, out / "voice.wav", out / "final.mp4", seconds=length, durations=beat_seconds(rows, length))
        from realchart.ends import FOLLOW_PKP, FOLLOW_RTT, attach, render_end_card
        ends_dir = out / "ends"
        outro_rtt = render_end_card(ends_dir / "outro_rtt.mp4", chart.get("path"), "Rajesh Technical Trader", "follow करो")
        outro_pkp = render_end_card(ends_dir / "outro_pkp.mp4", chart.get("path"), "Paise Ki Pathshala", "follow करो")
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
    title = rows[0]["d"]
    body = "\n".join(row["d"] for row in rows if row.get("sec") != "key")
    script_txt = (
        f"TITLE\n{title}\n\nHOOK\n{rows[0]['t']}\n\nBODY\n{body}\n\n"
        f"CTA\n{script.CTA}\n\nDISCLAIMER LINE\n{script.DISCLAIMER}\n\n"
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

