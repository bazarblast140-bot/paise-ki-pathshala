"""Numbers and dates for the screen: two decimals, Indian commas, no raw stamps."""
from __future__ import annotations

import re
from datetime import datetime

RAW_FLOAT = re.compile(r"\d\.\d{3,}")


def inr(value, nd=2):
    """22997.349609375 -> '22,997.35'; 1234567.5 -> '12,34,567.50'."""
    if value is None or value == "":
        return ""
    number = float(value)
    sign = "-" if number < 0 else ""
    text = f"{abs(number):.{nd}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}" if nd else f"{sign}{whole}"


def stamp(raw):
    """An RSS date or ISO time as '15 Oct 2026, 17:40 IST'. Unparseable text is dropped, never shown raw."""
    from email.utils import parsedate_to_datetime
    from realchart.session import now_ist
    text = str(raw or "").strip()
    if not text:
        return ""
    when = None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if when is None:
        return ""
    when = now_ist(when)
    return f"{when.day} {when.strftime('%b %Y')}, {when.strftime('%H:%M')} IST"


def raw_float_faults(strings):
    faults = []
    for text in strings:
        match = RAW_FLOAT.search(str(text or ""))
        if match:
            faults.append(f"raw float {match.group(0)}... in {str(text)[:40]!r}")
    return faults
