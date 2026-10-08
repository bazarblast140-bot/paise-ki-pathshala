"""Aaj ka Nifty: one data Short a day, in the 16:00 IST slot.

The spoken script is a fixed Hinglish template. Every number is computed
here from Yahoo Finance daily bars for ^NSEI. DeepSeek may only pick one
line from a fixed list. It never writes a number.

A bar counts only when its close is today's IST date and at or after the
15:30 IST cash close. A date-only daily stamp is that session's 15:30 close,
and only after the clock has reached 15:30. Weekends and NSE holidays write
a ledger skip and make no video.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TICKER = "^NSEI"
SLOT = "16:00"
TOPIC_KEY = "aaj_ka_nifty"
CASH_CLOSE = (15, 30)

# NSE equity cash holidays for 2026 (circular CMTR71775). Weekend dates in
# that circular are already skipped as weekends. 8 Nov 2026 is Muhurat on a
# Sunday, not a normal 15:30 session.
NSE_HOLIDAYS = {
    date(2026, 1, 26),
    date(2026, 3, 3),
    date(2026, 3, 26),
    date(2026, 3, 31),
    date(2026, 4, 3),
    date(2026, 4, 14),
    date(2026, 5, 1),
    date(2026, 5, 28),
    date(2026, 6, 26),
    date(2026, 9, 14),
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 10),
    date(2026, 11, 24),
    date(2026, 12, 25),
}

# No digits in any line. Each line is six words so the beat stays in window.
LINE_CHOICES = (
    "बायर्स ने आखिरी हिस्से में संभाला।",
    "सेलर्स आखिरी हिस्से में हावी रहे।",
    "भाव एक छोटी रेंज में अटका।",
)
_BANNED = ("हद", "पूँजी", "क्षमता", "तय करो", "पलटवार", "चीरती")


def is_weekend(day):
    return day.weekday() >= 5


def is_nse_holiday(day):
    return day in NSE_HOLIDAYS


def is_trading_day(day):
    return not is_weekend(day) and not is_nse_holiday(day)


# Slot watch looks here once the 15:51 IST cron has had time to upload.
CATCHUP_AT = (16, 20)
_BUSY_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}


def catchup_due(now, entries, busy=False):
    """True when today's 16:00 Nifty Short is still missing after 16:20 IST.

    A ledger row with a video id is already uploaded. A row with no video id
    is a skip or a recorded failure, and a second dispatch would post again.
    """
    if busy:
        return False
    now = now.astimezone(IST)
    if (now.hour, now.minute) < CATCHUP_AT:
        return False
    if not is_trading_day(now.date()):
        return False
    import slot_guard
    hit = slot_guard.find_entry(entries or [], now.strftime("%Y-%m-%d"), SLOT)
    if not hit:
        return True
    return False


def workflow_busy():
    """True when Aaj ka Nifty is already queued or running.

    NIFTY_RUN_IN_PROGRESS forces the answer so a test never calls GitHub.
    Outside GitHub Actions there is nothing to dispatch, so this is not busy.
    A failed status check is treated as busy so a dropped response cannot
    start a second upload.
    """
    flag = os.environ.get("NIFTY_RUN_IN_PROGRESS", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() not in ("true", "1"):
        return False
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    if not repo or not token:
        print("[nifty] catch-up skipped: GitHub token missing; treating the workflow as busy")
        return True
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        "aaj-ka-nifty.yml/runs?per_page=20"
    )
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "paise-ki-pathshala",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — unknown status must not double-dispatch
        print(f"[nifty] catch-up skipped: workflow status unknown ({type(exc).__name__})")
        return True
    for run in payload.get("workflow_runs") or []:
        if str(run.get("status") or "").lower() in _BUSY_STATUSES:
            return True
    return False


def _as_ist(stamp):
    if isinstance(stamp, datetime):
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=IST)
        return stamp.astimezone(IST)
    if isinstance(stamp, date):
        return datetime(stamp.year, stamp.month, stamp.day, tzinfo=IST)
    text = str(stamp or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("empty bar timestamp")
    if len(text) == 10:
        year, month, day = (int(part) for part in text.split("-"))
        return datetime(year, month, day, tzinfo=IST)
    when = datetime.fromisoformat(text)
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return when.astimezone(IST)


def close_is_today(stamp, now):
    """True when this bar is today's cash close.

    A clock time is used as Yahoo sent it, and must be at or after 15:30 IST
    on today's date. Midnight, or a date with no clock, is the official
    15:30 close of that session, accepted only once `now` is past that close.
    """
    now = now.astimezone(IST)
    when = _as_ist(stamp)
    if when.date() != now.date():
        return False
    # Midnight, a date with no clock, and anything before the 09:15 IST open
    # are the session date. UTC midnight is 05:30 IST, not a 05:30 close.
    before_open = (when.hour, when.minute) < (9, 15)
    if before_open:
        return (now.hour, now.minute) >= CASH_CLOSE
    return (when.hour, when.minute) >= CASH_CLOSE


def money(value):
    return f"{float(value):.2f}"


def key_level(close, prior_high=None, prior_low=None):
    """Nearest round 50/100, or the prior day's high or low.

    The level is never the close itself. A price under the close is support.
    """
    close = float(close)
    candidates = []
    for price, kind in (
        (round(close / 100.0) * 100.0, "round"),
        (round(close / 50.0) * 50.0, "round"),
        (None if prior_high is None else float(prior_high), "prior_high"),
        (None if prior_low is None else float(prior_low), "prior_low"),
    ):
        if price is None or abs(price - close) < 0.01:
            continue
        candidates.append((abs(price - close), price, kind))
    if not candidates:
        price, kind = round(close / 100.0) * 100.0, "round"
        if abs(price - close) < 0.01:
            price = price - 100.0
    else:
        candidates.sort(key=lambda row: (row[0], row[1]))
        _gap, price, kind = candidates[0]
    role = "support" if price < close else "resistance"
    return {"price": float(price), "kind": kind, "role": role}


def choose_line(change, reply=None):
    """One fixed line. A reply that is not 1, 2, or 3 is ignored.

    Positive uses line 1, negative line 2, flat line 3. DeepSeek is not
    asked to write words.
    """
    if float(change) > 0:
        index = 0
    elif float(change) < 0:
        index = 1
    else:
        index = 2
    text = " ".join(str(reply or "").split())
    if text in {"1", "2", "3"}:
        index = int(text) - 1
    return LINE_CHOICES[index]


def _ask_deepseek():
    """One short call. Empty, timeout, or any other text is not a line."""
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return ""
    if os.environ.get("NIFTY_SKIP_LINE_PICK", "").strip().lower() in ("1", "true", "yes"):
        return ""
    import common
    system = (
        "Reply with a single digit 1, 2, or 3. "
        "Do not write a sentence and do not write a number other than that digit."
    )
    user = "\n".join(f"{i}. {line}" for i, line in enumerate(LINE_CHOICES, start=1))
    try:
        return common.claude(system, user, max_tokens=8, retries=0, attempts=1)
    except Exception as exc:  # noqa: BLE001 — the code line is the fallback
        print(f"[nifty] line pick skipped: {type(exc).__name__}", file=sys.stderr)
        return ""


def session_from_bars(bars, now, reply=None):
    """Build the verified session, or (None, reason).

    `bars` are oldest-first dicts with ts, open, high, low, close.
    """
    now = now.astimezone(IST)
    rows = [row for row in (bars or []) if row and row.get("close") is not None and row.get("ts")]
    if len(rows) < 1:
        return None, "Yahoo ^NSEI returned no daily bar"
    today_rows = [row for row in rows if close_is_today(row["ts"], now)]
    if not today_rows:
        latest = rows[-1]
        return None, (
            f"bar close {latest.get('ts')} is not today's IST session "
            f"at or after {CASH_CLOSE[0]:02d}:{CASH_CLOSE[1]:02d}"
        )
    today = today_rows[-1]
    prior = None
    for row in rows:
        if row is today:
            break
        prior = row
    if prior is None and len(rows) >= 2 and rows[-1] is today:
        prior = rows[-2]
    close = float(today["close"])
    high = float(today["high"])
    low = float(today["low"])
    base = float(prior["close"]) if prior else float(today.get("open") or close)
    change = close - base
    pct = (change / base * 100.0) if base else 0.0
    level = key_level(
        close,
        prior.get("high") if prior else None,
        prior.get("low") if prior else None,
    )
    picked = choose_line(change, reply if reply is not None else _ask_deepseek())
    chart = rows[-5:]
    session = {
        "ticker": TICKER,
        "date_ist": now.date().isoformat(),
        "slot": SLOT,
        "close": close,
        "open": float(today.get("open") or close),
        "high": high,
        "low": low,
        "change": change,
        "pct": pct,
        "prior_close": base,
        "level": level,
        "line": picked,
        "bars": [
            {
                "ts": _as_ist(row["ts"]).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for row in chart
        ],
        "verified_claim": "आज",
    }
    session["narration"] = build_narration(session)
    session["facts"] = list(session["narration"]["facts"])
    return session, ""


def _direction(change):
    if change > 0:
        return "ऊपर"
    if change < 0:
        return "नीचे"
    return "फ्लैट"


def _role(level):
    return "सपोर्ट" if level.get("role") == "support" else "रेसिस्टेंस"


def build_narration(session):
    """Fixed four-beat script. Slots are the code numbers only."""
    close = money(session["close"])
    change = money(abs(session["change"]))
    pct = money(abs(session["pct"]))
    high = money(session["high"])
    low = money(session["low"])
    level = money(session["level"]["price"])
    way = _direction(session["change"])
    role = _role(session["level"])
    line = str(session.get("line") or LINE_CHOICES[2]).strip()
    hook_line = f"आज निफ्टी {close} पर बंद हुआ?"
    beats = {
        "hook": (
            f"{hook_line} बदलाव {change} अंक {way} है, "
            f"यानी {pct} प्रतिशत। यही आज का निफ्टी बंद है।"
        ),
        "pehchan": (
            f"पहचान इन तीन भावों से होती है। क्लोज {close} है। "
            f"दिन का हाई {high} है और दिन का लो {low} है। "
            f"ये तीनों आज के सेशन के आंकड़े हैं।"
        ),
        "matlab": (
            f"मतलब साफ है। नज़दीकी {role} {level} पर है। "
            f"{line} इस लेवल के पार बंद होना आगे का संकेत है, अकेले एंट्री नहीं।"
        ),
        "karein": (
            f"क्या करें, ट्रेडर इस लेवल {level} को नोट करें। "
            f"अगली कैंडल इसी तरफ बंद हो तभी प्लान बनता है। "
            f"स्टॉप अपने हिसाब से रखें। सिर्फ़ जानकारी, निवेश सलाह नहीं।"
        ),
    }
    facts = [
        f"आज निफ्टी क्लोज {close}",
        f"बदलाव {change} अंक {way}",
        f"{pct} प्रतिशत",
        f"हाई {high}",
        f"लो {low}",
        f"{role} {level}",
    ]
    title = "आज का निफ्टी बंद"
    return {
        "title": title,
        "hook_line": hook_line,
        "beats": beats,
        "facts": facts,
        "source": "template",
        "narration_fallback": False,
        "review_attempts": 1,
    }


def narration_from_session(topic, path=None):
    """Load the locked template onto `topic`. No DeepSeek script call."""
    from common import RUN
    path = path or (RUN / "nifty_session.json")
    if not path.is_file():
        raise FileNotFoundError(f"nifty session missing: {path}")
    session = json.loads(path.read_text(encoding="utf-8"))
    narration = session.get("narration") or build_narration(session)
    topic["key"] = TOPIC_KEY
    topic["_live_short"] = True
    topic["_live_beats"] = "four"
    topic["_voice"] = {"words_per_second": 1.9, "engine": "template"}
    topic["_nifty"] = session
    topic["_spoken_beats"] = dict(narration["beats"])
    topic["facts"] = list(narration.get("facts") or session.get("facts") or [])
    topic["topic"] = narration.get("title") or topic.get("topic") or "आज का निफ्टी"
    on_screen = {
        "hook": ["आज का निफ्टी", f"बंद {money(session['close'])}"],
        "pehchan": ["हाई लो", f"{money(session['high'])} / {money(session['low'])}"],
        "matlab": ["की लेवल", f"{_role(session['level'])} {money(session['level']['price'])}"],
        "karein": ["क्या देखें", "सिर्फ़ जानकारी"],
    }
    topic["beats"] = on_screen
    (RUN / "topic.json").write_text(
        json.dumps(topic, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
    )
    _write_verification(session, RUN)
    return narration


def _write_verification(session, run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "Yahoo Finance",
        "ticker": TICKER,
        "आज": session.get("verified_claim") or "आज",
        "date_ist": session.get("date_ist"),
        "close": money(session["close"]),
        "change": money(session["change"]),
        "pct": money(session["pct"]),
        "high": money(session["high"]),
        "low": money(session["low"]),
        "level": money(session["level"]["price"]),
        "line": session.get("line"),
    }
    (run_dir / "fetched.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
    )
    (run_dir / "market_freshness.json").write_text(
        json.dumps({
            "as_of": session.get("date_ist"),
            "on_screen": True,
            "source": "Yahoo Finance",
            "ticker": TICKER,
        }, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    (run_dir / "nifty_session.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
    )


def plan(now, bars, entries=None, pick_line=False):
    """What this clock should do with the 16:00 slot.

    pick_line asks DeepSeek for 1, 2, or 3 once. Tests leave it off.
    """
    now = now.astimezone(IST)
    day = now.date()
    date_ist = day.isoformat()
    rows = entries or []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        if entry.get("date_ist") != date_ist or entry.get("slot") != SLOT:
            continue
        if str(entry.get("status") or "").strip().lower() == "withdrawn":
            continue
        return {
            "action": "filled",
            "occupy": False,
            "reason": f"{date_ist} {SLOT} IST is already filled",
            "date_ist": date_ist,
            "session": None,
        }
    if is_weekend(day):
        return {
            "action": "skip",
            "occupy": True,
            "reason": f"{date_ist} is a weekend; no Nifty short",
            "date_ist": date_ist,
            "session": None,
        }
    if is_nse_holiday(day):
        return {
            "action": "skip",
            "occupy": True,
            "reason": f"{date_ist} is an NSE holiday; no Nifty short",
            "date_ist": date_ist,
            "session": None,
        }
    session, reason = session_from_bars(bars, now, reply=None if pick_line else "")
    if session is None:
        return {
            "action": "stale",
            "occupy": False,
            "reason": reason,
            "date_ist": date_ist,
            "session": None,
        }
    return {
        "action": "build",
        "occupy": False,
        "reason": "",
        "date_ist": date_ist,
        "session": session,
    }


def occupy_skip(decision):
    """Write a no-video ledger row so slot watch does not build a topic."""
    if not decision.get("occupy"):
        return
    import ledger
    ledger.record_publish(
        None,
        slot=SLOT,
        platform="skip",
        date_ist=decision["date_ist"],
        topic_key=TOPIC_KEY,
        note=decision["reason"],
        slot_skipped=decision["reason"],
    )


def bars_from_yahoo_frame(frame):
    """Daily OHLC rows. A missing close is dropped, never filled."""
    if frame is None or getattr(frame, "empty", True):
        return []
    def column(name):
        series = frame[name]
        if hasattr(series, "columns"):
            series = series.iloc[:, 0]
        return series
    try:
        opens, highs, lows, closes = (column(name) for name in ("Open", "High", "Low", "Close"))
    except (KeyError, IndexError, TypeError):
        return []
    rows = []
    for stamp in closes.index:
        try:
            close = float(closes.loc[stamp])
            if close != close:
                continue
            rows.append({
                "ts": stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp,
                "open": float(opens.loc[stamp]),
                "high": float(highs.loc[stamp]),
                "low": float(lows.loc[stamp]),
                "close": close,
            })
        except (TypeError, ValueError, KeyError):
            continue
    return rows


def fetch_yahoo_bars():
    import yfinance as yf
    frame = yf.download(
        TICKER, period="15d", interval="1d",
        progress=False, auto_adjust=False, threads=False,
    )
    return bars_from_yahoo_frame(frame)


def emit_slot_env(now=None, bars=None, entries=None):
    """GitHub outputs for the 16:00 data Short. Prints taken=true to skip.

    Library and DeepSeek logs stay on stderr. Stdout is only the four
    slot lines the workflow appends to GITHUB_OUTPUT.
    """
    import contextlib
    now = now or datetime.now(IST)
    if entries is None:
        import slot_guard
        entries = slot_guard._read()
    with contextlib.redirect_stdout(sys.stderr):
        if bars is None:
            try:
                bars = fetch_yahoo_bars()
            except Exception as exc:  # noqa: BLE001
                print(f"[nifty] Yahoo fetch failed: {type(exc).__name__}", file=sys.stderr)
                bars = []
        decision = plan(now, bars, entries, pick_line=True)
        if decision["action"] == "build":
            from common import RUN
            _write_verification(decision["session"], RUN)
            print(
                f"[nifty] {decision['date_ist']} {SLOT} close {money(decision['session']['close'])}",
                file=sys.stderr,
            )
    if decision["action"] == "build":
        print(f"slot={SLOT}")
        print(f"date_ist={decision['date_ist']}")
        print("publish_at=")
        print("taken=false")
        return decision
    print(f"[skip] {decision['reason']}", file=sys.stderr)
    if decision["action"] == "skip":
        occupy_skip(decision)
    print(f"slot={SLOT}")
    print(f"date_ist={decision['date_ist']}")
    print("publish_at=")
    print("taken=true")
    return decision


def paint_frame(topic, beat, index=0, n=1, spoken=""):
    """Chart on the top board (about 65% of the frame), captions underneath."""
    import board_kit as K
    import board_scenes
    import scene_frames
    import safe_area

    session = (topic or {}).get("_nifty") or {}
    bars = list(session.get("bars") or [])
    img = K.background()
    K.panel(img)
    draw_box = K.BOARD
    # A bright grid keeps chart rows from reading as empty background.
    from PIL import ImageDraw
    grid = ImageDraw.Draw(img)
    for y in range(int(draw_box[1]) + 8, int(draw_box[3]) - 8, 36):
        grid.line([(draw_box[0] + 12, y), (draw_box[2] - 12, y)], fill=(120, 150, 190), width=2)
    if bars:
        ohlc = [
            (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
            for row in bars
        ]
        lo = min(row[2] for row in ohlc)
        hi = max(row[1] for row in ohlc)
        level = float((session.get("level") or {}).get("price") or ohlc[-1][3])
        lo = min(lo, level)
        hi = max(hi, level)
        ax = K.candles(img, draw_box, ohlc, lo, hi, grid=False, width_frac=0.45, pad=(70, 90, 70, 80))
        last = ohlc[-1]
        K.ring(
            img, ax.x(len(ohlc) - 1), ax.y(last[3]),
            36, 28, K.YELLOW,
        )
        K.dashed(
            img, draw_box[0] + 24, draw_box[2] - 24, ax.y(level),
            K.YELLOW, money(level), size=28, label_side="left",
        )
        K.label(img, draw_box[0] + 48, draw_box[1] + 100, f"CLOSE {money(last[3])}", K.INK, 32)
        if beat == "pehchan":
            K.label(img, draw_box[0] + 48, draw_box[1] + 148, f"HIGH {money(session.get('high') or last[1])}", K.GREEN, 28)
            K.label(img, draw_box[0] + 48, draw_box[1] + 192, f"LOW {money(session.get('low') or last[2])}", K.RED, 28)
        elif beat == "matlab":
            side = "SUPPORT" if (session.get("level") or {}).get("role") == "support" else "RESIST"
            K.label(img, draw_box[0] + 48, draw_box[1] + 148, side, K.YELLOW, 28)
        elif beat == "karein":
            K.label(img, draw_box[0] + 48, draw_box[1] + 148, "NEXT CLOSE", K.INK, 28)
    lines = board_scenes._caption_lines(spoken)
    y = safe_area.SUBTITLE_TOP + 18
    for line in lines:
        scene_frames._text(
            img=img, x=540, y=y, text=line, fill=(250, 250, 250), size=36,
            anchor="mt", band=False, role="caption",
        )
        y += 70
    scene_frames._brand(img)
    img.chart_region = tuple(float(v) for v in K.BOARD)
    img.scene_kind = f"chart-{beat}"
    img.chart_spec = {
        "concept": "nifty_day",
        "rupees": [],
        "percents": [],
        "labels": [money(session["close"])] if session.get("close") is not None else [],
        "scene": TOPIC_KEY,
    }
    caps = [
        layer["box"] for layer in (getattr(img, "text_layers", None) or [])
        if layer.get("role") == "caption" and layer.get("box")
    ]
    if caps:
        img.panel_box = (
            min(box[0] for box in caps), min(box[1] for box in caps),
            max(box[2] for box in caps), max(box[3] for box in caps),
        )
        img.panel_fill = scene_frames.panel_fill_fraction(img)
    else:
        img.panel_fill = 1.0
    img.label_boxes = list(getattr(img, "label_boxes", None) or [])
    del index, n
    return img


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--slot-env":
        emit_slot_env()
        return
    print("usage: python nifty_day.py --slot-env", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
