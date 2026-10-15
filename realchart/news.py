"""Real RSS articles only. A missing article is never filled in."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

FEEDS = (
    "https://www.livemint.com/rss/markets",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    "https://www.business-standard.com/rss/markets-106.rss",
)
BIG = (
    "nifty", "sensex", "bank nifty", "rbi", "sebi", "market", "markets",
    "stocks", "shares", "crash", "rally", "fall", "surge", "rate",
)


def _text(node, name):
    child = node.find(name) if node is not None else None
    if child is None or not child.text:
        return ""
    import html
    # Feeds double-escape: "Hotels &amp;amp; Resorts" must read "Hotels & Resorts".
    return " ".join(html.unescape(html.unescape(child.text)).split())


def parse_rss(xml_text, source):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    rows = []
    for item in root.iter("item"):
        title = _text(item, "title")
        link = _text(item, "link")
        summary = _text(item, "description")
        when = _text(item, "pubDate") or _text(item, "date")
        rows.append({
            "title": title,
            "url": link,
            "summary": summary,
            "published": when,
            "source": source,
        })
    return rows


def _when(item):
    raw = str(item.get("published") or "").strip()
    if not raw:
        return None
    try:
        stamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if stamp.tzinfo is None:
        return None
    return stamp


def choose(items, now, aliases=()):
    """The latest big market item with a real date. None when nothing qualifies."""
    aliases = [str(name).lower() for name in aliases if name]
    best = None
    best_at = None
    for item in items or []:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url.startswith("https://"):
            continue
        stamp = _when(item)
        if stamp is None or stamp > now:
            continue
        blob = f"{title} {item.get('summary') or ''}".lower()
        if aliases and not any(name in blob for name in aliases):
            if not any(word in blob for word in BIG):
                continue
        elif not any(word in blob for word in BIG) and not aliases:
            continue
        if best_at is None or stamp > best_at:
            best = dict(item)
            best["published_at"] = stamp.isoformat()
            sentence = str(item.get("summary") or title).split(".")[0].strip()
            best["key_sentence"] = sentence[:240]
            best_at = stamp
    return best
