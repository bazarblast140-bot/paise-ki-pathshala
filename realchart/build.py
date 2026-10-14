"""Build one Reel into a directory. Publish stays in the workflow."""
from __future__ import annotations

import json
import os
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


def run(kind, out, now=None, dry_run=False, bars_for=None, articles=None, chart_opener=None, article_opener=None):
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
        levels, article, chart = _prepare(
            kind, session, now, out, bars_for=bars_for, articles=articles,
            chart_opener=chart_opener, article_opener=article_opener, dry_run=dry_run,
        )
        rows = script.chunks_for(kind, levels, article)
        faults = script.lint(rows) + script.number_faults(rows, levels)
        if faults:
            raise Hold("script", "; ".join(faults))
        from realchart.voice import speak
        voice = speak(script.spoken_text(rows), out / "voice.wav", kind, dry_run=dry_run, cache_key=f"{kind}-{levels.get('last_date')}-{levels.get('symbol')}")
        from realchart.render import assemble, frames
        pics = frames(out / "cards", kind, levels, rows, chart.get("path"), article)
        assemble(pics, out / "voice.wav", out / "final.mp4")
        _write_sources(out, kind, levels, article, chart, rows, voice, now)
        from realchart.qa import check
        qa_faults = check(out, rows, levels, voice.get("engine"), dry_run=dry_run)
        verdict = "DRY-OK" if dry_run and not qa_faults else ("PASS" if not qa_faults else "HOLD")
        report = {
            "verdict": verdict,
            "faults": qa_faults,
            "kind": kind,
            "chart": chart.get("source"),
            "label": chart.get("label"),
            "chart_reason": chart.get("reason") or "",
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


def _prepare(kind, session, now, out, bars_for=None, articles=None, chart_opener=None, article_opener=None, dry_run=False):
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
    if kind == "news" and not article:
        raise Hold("news", "no qualifying article")
    chart = capture.capture_chart(levels["symbol"], out / "chart.png", levels.get("bars") or [], opener=chart_opener)
    _apply_technicals(levels, chart)
    if chart.get("reason"):
        levels["chart_reason"] = chart.get("reason")
    if article and article_opener is not None:
        shot = capture.capture_article(article["url"], out / "news.png", opener=article_opener)
        article["capture"] = shot
        if kind == "news" and not shot.get("ok"):
            raise Hold("news", shot.get("reason") or "article capture failed")
    elif kind == "news" and article_opener is None and not os.environ.get("REALCHART_SKIP_BROWSER"):
        shot = capture.capture_article(article["url"], out / "news.png")
        article["capture"] = shot
        if not shot.get("ok"):
            raise Hold("news", shot.get("reason") or "article capture failed")
    (out / "levels.json").write_text(json.dumps({k: v for k, v in levels.items() if k != "bars"}, indent=2) + "\n", encoding="utf-8")
    return levels, article, chart


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


def _write_sources(out, kind, levels, article, chart, rows, voice, now):
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
        f"- values {levels.get('values_from')} (EMA/RSI stated as cards, not lines)",
        f"- voice {voice.get('engine')} requests {voice.get('requests')}",
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

