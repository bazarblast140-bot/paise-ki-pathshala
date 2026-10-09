"""Which session a Reel is allowed to describe."""
from __future__ import annotations

from datetime import datetime, timedelta

import nifty_day

IST = nifty_day.IST
START = {
    "news": datetime(2026, 10, 12, tzinfo=IST).date(),
    "close": datetime(2026, 10, 12, tzinfo=IST).date(),
    "ta": datetime(2026, 10, 12, tzinfo=IST).date(),
    "recap": datetime(2026, 10, 10, tzinfo=IST).date(),
}


def now_ist(now=None):
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    return now.astimezone(IST)


def expected_session(kind, now=None):
    """The cash session this Reel may talk about, or None when the day is off.

    News runs on weekdays with no close gate. Close and TA need today's
    session. Saturday recap needs Friday, when Friday was a trading day.
    """
    now = now_ist(now)
    day = now.date()
    kind = str(kind or "")
    if kind == "news":
        if day.weekday() >= 5:
            return None
        return day
    if kind == "recap":
        if day.weekday() != 5:
            return None
        friday = day - timedelta(days=1)
        if not nifty_day.is_trading_day(friday):
            return None
        return friday
    if day.weekday() >= 5 or not nifty_day.is_trading_day(day):
        return None
    return day


def started(kind, now=None, dry_run=False):
    """Live crons wait for Mon 12 Oct, and the recap for Sat 10 Oct."""
    if dry_run:
        return True
    now = now_ist(now)
    return now.date() >= START[str(kind)]


def _previous_trading_day(day):
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if nifty_day.is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def dry_session(kind, now=None):
    """A finished session a dry run can describe before the live start date."""
    now = now_ist(now)
    day = now.date()
    kind = str(kind or "")
    if kind == "news":
        cursor = day
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        return cursor
    if kind == "recap":
        live = expected_session("recap", now)
        if live is not None:
            return live
        cursor = day
        if cursor.weekday() != 4:
            cursor = cursor - timedelta(days=(cursor.weekday() - 4) % 7)
        if cursor == day and (now.hour, now.minute) < nifty_day.CASH_CLOSE:
            cursor -= timedelta(days=7)
        for _ in range(8):
            closed = cursor < day or (now.hour, now.minute) >= nifty_day.CASH_CLOSE
            if cursor.weekday() == 4 and nifty_day.is_trading_day(cursor) and closed:
                return cursor
            cursor -= timedelta(days=7)
        return None
    if nifty_day.is_trading_day(day) and (now.hour, now.minute) >= nifty_day.CASH_CLOSE:
        return day
    return _previous_trading_day(day)


def close_matches(stamp, session, now=None):
    """True when a Yahoo bar is that session's 15:30 IST close."""
    now = now_ist(now)
    if session is None:
        return False
    try:
        got = nifty_day._as_ist(stamp)
    except (TypeError, ValueError):
        return False
    if got.date() != session:
        return False
    if (got.hour, got.minute) < (9, 15):
        return now.date() > session or (now.date() == session and (now.hour, now.minute) >= nifty_day.CASH_CLOSE)
    return (got.hour, got.minute) >= nifty_day.CASH_CLOSE
