"""Real RSS articles only. A missing article is never filled in."""
from __future__ import annotations

import re
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
            best["key_sentence"] = complete_clause(first_sentence(item.get("summary") or title)[:240])
            best_at = stamp
    return best


CONNECTORS = {"or", "and", "to", "by", "at", "of", "from", "with", "for", "in", "on", "the", "a", "an", "than", "as", "while", "but"}


def first_sentence(text):
    """First sentence; a dot between digits (0.51) is a decimal, not an end."""
    text = " ".join(str(text or "").split())
    return re.split(r"(?<!\d)\.(?!\d)|\.(?=\s)", text, maxsplit=1)[0].strip()


def truncated(sentence):
    """True when the sentence ends mid-thought: an ellipsis, a dangling connector, or a bare number after one."""
    words = str(sentence or "").rstrip().split()
    if not words:
        return False
    last = words[-1].lower().strip(",;:")
    if str(sentence).rstrip().endswith(("…", "...", ",", "-")):
        return True
    if last in CONNECTORS:
        return True
    # "...118 points or 0": a bare number after or/and is an alternative cut off mid-figure.
    return bool(re.fullmatch(r"[\d,]+", last)) and len(words) > 1 and words[-2].lower().strip(",") in ("or", "and")


def complete_clause(sentence):
    """Cut a feed-truncated sentence back to its last complete clause ('...118 points or 0' -> '...118 points')."""
    words = str(sentence or "").replace("…", " ").replace("...", " ").split()
    while words and truncated(" ".join(words)):
        last = words[-1].lower().strip(",;:-")
        words.pop()
        if re.fullmatch(r"[\d,]+", last) and words and words[-1].lower().strip(",") in ("or", "and"):
            words.pop()
        elif last in CONNECTORS:
            # "higher, led by" -> "higher": a dangling phrase goes back to its comma.
            for back in range(1, min(3, len(words)) + 1):
                if words[-back].endswith(","):
                    del words[len(words) - back + 1:]
                    words[-1] = words[-1].rstrip(",")
                    break
    return " ".join(words).rstrip(",;:- ")


# Morning Reel (Rajesh, 23-Oct-2026): no Nifty chart and no Nifty levels. The single
# biggest story of the last 24 h across AI, technology, finance, stocks and crypto.
MORNING_FEEDS = (
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/19854910/device/rss/rss.html",
    "https://techcrunch.com/feed/",
    "https://www.livemint.com/rss/technology",
    "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
) + FEEDS
TOPIC_WORDS = {
    "ai": 3, "openai": 3, "nvidia": 3, "chatgpt": 3, "gemini": 2, "anthropic": 3, "chip": 2, "chips": 2,
    "apple": 2, "google": 2, "microsoft": 2, "meta": 2, "amazon": 2, "tesla": 2, "bitcoin": 3, "crypto": 3,
    "ethereum": 2, "fed": 3, "rate": 2, "rates": 2, "inflation": 2, "tariff": 3, "tariffs": 3, "earnings": 2,
    "stocks": 2, "shares": 1, "nifty": 1, "index": 1, "market": 1, "markets": 1, "sensex": 2, "rbi": 2, "sebi": 2, "ipo": 2, "billion": 2,
    "trillion": 3, "record": 2, "crash": 3, "surge": 2, "plunge": 3, "soar": 2, "deal": 2, "acquire": 2,
}
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "is", "with", "its", "by", "after",
        "from", "says", "will", "new", "how", "why", "what", "this", "that", "are", "be", "it", "his", "her"}
SKIP = re.compile(r"\b(live updates?|live blog|podcast|newsletter|deals? of the day|best .* to buy|review:|quiz|horoscope|"
                  r"stocks? to (buy|sell)|recommends|top picks|buy or sell|stocks? in (the )?news|stocks? to watch|"
                  r"market (live|today|wrap|outlook)|morning (brief|briefing)|week ahead|things to know|explained)\b", re.I)


def _words(text):
    return {w for w in re.findall(r"[a-z][a-z0-9]+", str(text or "").lower()) if w not in STOP and len(w) > 2}


def top_stories(items, now, n=2, hours=24):
    """The `n` biggest distinct real stories of the last `hours`: covered by the most outlets
    (shared headline words), weighted by AI/tech/finance/crypto topic words, then newest.
    Later picks must be a different story (no headline overlap) from a different source."""
    from datetime import timedelta
    pool = []
    for item in items or []:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        stamp = _when(item)
        if not title or not url.startswith("https://") or stamp is None or stamp > now:
            continue
        if now - stamp > timedelta(hours=hours) or SKIP.search(title):
            continue
        pool.append((item, stamp, _words(title)))

    def same(words, ow):
        return len(words & ow) >= 3 and len(words & ow) / max(1, len(words | ow)) >= 0.2

    scored = []
    for item, stamp, words in pool:
        topic = sum(TOPIC_WORDS.get(w, 0) for w in words | _words(item.get("summary")))
        if topic == 0:
            continue
        hosts = {other.get("source") for other, _s, ow in pool if other is not item and same(words, ow)}
        hosts.discard(item.get("source"))
        scored.append(((len(hosts) * 4 + min(topic, 9), stamp), item, words))
    scored.sort(key=lambda row: row[0], reverse=True)
    picked = []
    for key, item, words in scored:
        if any(same(words, w) or item.get("source") == it.get("source") for _k, it, w in picked):
            continue
        picked.append((key, item, words))
        if len(picked) == n:
            break
    out = []
    for key, item, _w in picked:
        best = dict(item)
        best["published_at"] = _when(best).isoformat()
        best["key_sentence"] = complete_clause(first_sentence(best.get("summary") or best["title"])[:240])
        best["coverage"] = key[0]
        out.append(best)
    return out


def biggest(items, now, hours=24):
    """The single biggest real story of the last `hours` (see top_stories)."""
    top = top_stories(items, now, 1, hours)
    return top[0] if top else None
