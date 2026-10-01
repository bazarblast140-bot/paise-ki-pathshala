"""Daily read-only trend research for Paise Ki Pathshala.

    python trend_research.py            # fetch and write data/trends.json + reports/
    python trend_research.py --dry-run  # fetch and print counts, write nothing

The GitHub Actions workflow .github/workflows/daily-trend-research.yml runs this
at 05:30 IST (00:00 UTC) and on workflow_dispatch. It must never publish, upload,
or post. It may commit data/trends.json and the weekly report back to the repo.

What it records
  * Top Hindi finance/trading Shorts from the last 7 days (YouTube Data API
    search.list + videos.list, plus the competitor channels in
    data/research_sources.json).
  * India finance headlines from public RSS feeds, and finance videos among
    YouTube's mostPopular chart for region IN.
  * A weekly markdown summary of hook patterns, title formats, lengths and
    topic clusters.

Schema (schema_version 1), also copied into every data/trends.json as schema_note
---------------------------------------------------------------------------------
shorts[] — one object per stored Short, ranked by statistics.viewCount
  (rows with a missing viewCount sort last). Only videos whose
  contentDetails.duration parses to 1–180 seconds are stored.
  video_id            snippet/id from the API
  title               snippet.title, or null
  opening_line        first non-empty description line, or null
  hook_pattern        classifier below, or null when the line matches none
  hook_source         "description" or "title" or null
  duration_s          parsed contentDetails.duration, else the row is dropped
  views, likes, comments
                      statistics.*Count as integers. Null when the response
                      omits the field. Never coerced to 0.
  channel_id, channel_title
  published_at        snippet.publishedAt as returned (null if omitted)
  visual_style_notes  "" unless a thumbnail width/height or a style word in
                      tags is present. Not a guess about the edit.
  thumbnail_width, thumbnail_height
                      integers from snippet.thumbnails, or null
  tags                snippet.tags (strings only), else []
  source_query        the search query or "channel:<name>" that found it

hook_pattern is only one of: question, how_to, number_led, list, warning,
versus, explainer. It is chosen from the description's first line when that
line matches, otherwise from the title. It is never a sentence we wrote.

trending_topics[] — news or YouTube-chart rows
  title, source, source_url, published_at (null if the feed/API had no date),
  fetched_at, views (integer from YouTube statistics, or null for RSS and when
  YouTube omitted viewCount).

summary.topic_clusters[] — {name, count, keywords}. count is how many stored
  Shorts (or headlines, for headline_terms) actually matched. keywords are
  terms that occurred, not the whole dictionary. Clusters with count < 2 are
  omitted so a single video cannot move the topic picker.

quota.used_units is how many Data API units this process charged before each
call. It is not YouTube's remaining-quota counter (the API does not expose one).

Auth: YT_DATA_API_KEY if set, else the upload OAuth refresh token via
yt_credentials.py (env YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET /
YOUTUBE_REFRESH_TOKEN, GitHub secrets YT_FIN_*). Neither is created here.
Missing credentials skip YouTube and still allow RSS.

Quota: HARD_QUOTA_BUDGET is 1,200 units and config cannot raise it.
search.list costs 100; the other calls used here cost 1. Six searches is the
hard maximum (600) plus a handful of 1-unit calls, well under 1,200. The
pipeline's competitor_research.py caps itself at 500. Combined research stays
under the ~2,000-unit daily ceiling on the shared 10,000-unit quota.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import competitor_research as CR
from competitor_patterns import term_in_text

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TRENDS_PATH = DATA / "trends.json"
REPORTS_DIR = ROOT / "reports"
SOURCES_PATH = DATA / "research_sources.json"
IST = timezone(timedelta(hours=5, minutes=30))

# Hard ceiling for THIS job. Config may lower it, never raise it.
# competitor_research.COMPETITOR_QUOTA_BUDGET (500) + 1200 = 1700 < ~2000.
HARD_QUOTA_BUDGET = 1200
SHORT_MAX_SECONDS = 180
MAX_SEARCH_QUERIES = 6

SCHEMA_NOTE = (
    "schema_version 1. shorts[] metrics views/likes/comments/duration_s/"
    "thumbnail_width/thumbnail_height are copied from YouTube Data API fields "
    "or null when the response omits them. hook_pattern is a fixed classifier "
    "on the description's first line, else the title, and null when neither "
    "matches. visual_style_notes is empty unless thumbnail dimensions or a "
    "style word inside tags are present. trending_topics[] keep source and "
    "published_at (null if the source had no date). quota.used_units counts "
    "units this job charged, not YouTube's remaining quota. Research only — "
    "nothing here is uploaded or posted."
)
POLICY = (
    "Read-only research. This job must not publish, upload, or post. "
    "Learn patterns; do not reuse competitor titles, scripts, or thumbnails."
)

DEFAULT_QUERIES = (
    "stock market hindi shorts",
    "option trading hindi shorts",
    "candlestick pattern hindi",
    "personal finance hindi shorts",
    "intraday trading hindi",
)

DEFAULT_FEEDS = (
    {"name": "Economic Times Markets",
     "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"name": "Mint Markets", "url": "https://www.livemint.com/rss/markets"},
    {"name": "Business Standard Markets",
     "url": "https://www.business-standard.com/rss/markets-106.rss"},
    {"name": "Hindu BusinessLine Markets",
     "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss"},
)

# mostPopular is the whole country. Keep a row only when the title or
# description actually contains one of these. Bare "market" / "bank" are
# omitted on purpose (they match entertainment and unrelated news).
FINANCE_TERMS = (
    "stock", "stocks", "share market", "share", "nifty", "sensex", "trading",
    "trader", "option", "options", "candlestick", "finance", "sip", "demat",
    "intraday", "mutual fund", "ipo", "invest", "portfolio", "banknifty",
    "bank nifty", "futures", "f&o", "breakout", "rupee", "rbi", "sebi", "repo",
    "inflation", "gst", "budget", "equity", "nse", "bse", "midcap", "smallcap",
    "dividend", "शेयर", "ट्रेडिंग", "ट्रेड", "ऑप्शन", "कैंडल", "निफ्टी",
    "सेंसेक्स", "निवेश", "म्यूचुअल", "टैक्स",
)

# Counted only when a stored Short's title or opening line contains the term.
CLUSTER_TERMS = (
    ("options", ("option", "options", "theta", "delta", "vega", "straddle", "strangle", "ऑप्शन")),
    ("candlestick", ("candlestick", "doji", "hammer", "engulfing", "marubozu", "कैंडल")),
    ("intraday", ("intraday", "scalping", "scalp", "vwap", "opening range")),
    ("personal_finance", ("sip", "mutual fund", "emi", "insurance", "ppf", "nps", "टैक्स")),
    ("price_action", ("breakout", "support", "resistance", "trendline", "price action")),
    ("risk", ("stop loss", "stoploss", "drawdown", "position size", "risk management")),
    ("index", ("nifty", "sensex", "banknifty", "bank nifty")),
    ("fundamentals", ("pe ratio", "p/e", "roe", "roce", "earnings", "results")),
    ("tax_ipo", ("ipo", "gst", "income tax", "ltcg", "stcg")),
)

GENERIC_TERMS = {
    "market", "markets", "stock", "stocks", "share", "shares", "trading", "trade",
    "trader", "finance", "hindi", "india", "indian", "today", "video", "shorts",
    "short", "news", "money", "price", "after", "with", "from", "that", "this",
    "have", "will", "your", "what", "when", "where", "just", "about", "more",
    "में", "और", "क्या", "कैसे", "क्यों", "नहीं", "लिए", "होता", "होती", "करें",
    "वाला", "वाली", "रहा", "रहे", "किया",
}

STYLE_TAG_MARKERS = (
    "candlestick", "candle", "chart", "whiteboard", "green screen", "greenscreen",
    "facecam", "face cam", "animation", "screen record", "text overlay",
)

_ISO_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_QUESTION_WORD = re.compile(
    r"(?<![\w])(क्या|क्यों|कैसे|कौन|what|why|how|which|when|kaise|kya|kyun|kaun)(?![\w])",
    re.I,
)
_HOW_TO = re.compile(r"^(how\s+to|कैसे)\b", re.I)
_LIST_WORD = re.compile(
    r"(?<![\w])(?:things|tips|ways|rules|patterns|steps|reasons|mistakes|points|tools|"
    r"indicators|candles|secrets|गलतियाँ|गलतियां|बातें|नियम|तरीके)(?![\w\u0900-\u097f])",
    re.I,
)
_WARNING = re.compile(
    r"(?<![\w])(stop|never|avoid|warning|mistake|mistakes|galati|galti|सावधान|गलती|मत)(?![\w])",
    re.I,
)
_VERSUS = re.compile(r"(?<![\w])(vs|versus)(?![\w])|बनाम", re.I)
_EXPLAINER = re.compile(r"(?<![\w])(explained|explainer|समझो|matlab|मतलब)(?![\w])", re.I)


def resolve_settings(block):
    """Clamp operator config so a bad edit cannot blow the daily quota."""
    block = block if isinstance(block, dict) else {}
    raw_queries = block.get("search_queries") or list(DEFAULT_QUERIES)
    if not isinstance(raw_queries, list):
        raw_queries = list(DEFAULT_QUERIES)
    queries = [str(q).strip() for q in raw_queries if str(q).strip()]
    try:
        max_queries = int(block.get("max_search_queries", MAX_SEARCH_QUERIES))
    except (TypeError, ValueError):
        max_queries = MAX_SEARCH_QUERIES
    queries = queries[:max(0, min(MAX_SEARCH_QUERIES, max_queries))]
    try:
        requested = int(block.get("quota_budget_units", HARD_QUOTA_BUDGET))
    except (TypeError, ValueError):
        requested = HARD_QUOTA_BUDGET
    try:
        lookback = int(block.get("lookback_days", 7))
    except (TypeError, ValueError):
        lookback = 7
    try:
        max_shorts = int(block.get("max_shorts", 40))
    except (TypeError, ValueError):
        max_shorts = 40
    try:
        max_results = int(block.get("max_results_per_query", 10))
    except (TypeError, ValueError):
        max_results = 10
    return {
        "search_queries": queries,
        "quota_budget_units": max(0, min(HARD_QUOTA_BUDGET, requested)),
        "lookback_days": max(1, min(7, lookback)),
        "region_code": str(block.get("region_code") or "IN")[:8] or "IN",
        "relevance_language": str(block.get("relevance_language") or "hi")[:8] or "hi",
        "max_shorts": max(1, min(40, max_shorts)),
        "max_results": max(1, min(15, max_results)),
        "max_per_feed": 8,
        "news_max_age_days": 14,
    }


def trend_settings():
    cfg = CR._read(ROOT / "config.json", {})
    research = cfg.get("research") if isinstance(cfg, dict) else None
    block = research.get("trends") if isinstance(research, dict) else None
    return resolve_settings(block)


def news_feeds():
    sources = CR._read(SOURCES_PATH, {})
    rows = sources.get("news_feeds") if isinstance(sources, dict) else None
    if not isinstance(rows, list) or not rows:
        rows = list(DEFAULT_FEEDS)
    out = []
    for row in rows:
        if isinstance(row, dict) and row.get("name") and row.get("url"):
            out.append({"name": str(row["name"]), "url": str(row["url"])})
        if len(out) >= 8:
            break
    return out


def competitor_channel_ids():
    sources = CR._read(SOURCES_PATH, {})
    ids = []
    for row in (sources.get("competitors") or []) if isinstance(sources, dict) else []:
        if isinstance(row, dict) and row.get("channel_id") and row["channel_id"] not in ids:
            ids.append(row["channel_id"])
    for cid in CR.research_cfg().get("extra_channel_ids") or []:
        if cid and cid not in ids:
            ids.append(cid)
    return ids[:6]


# ------------------------------------------------------------------ parsing
def duration_seconds(value):
    """Seconds from an ISO-8601 duration, or None when it is not one.

    PT0S is 0 (a real value). Garbage and empty strings are None, not 0.
    """
    if not isinstance(value, str) or not value:
        return None
    match = _ISO_DUR.fullmatch(value)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def optional_int(mapping, key):
    """Integer from an API object. Missing or blank stays None, including 0-vs-absent."""
    if not isinstance(mapping, dict) or key not in mapping:
        return None
    raw = mapping[key]
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def first_line(description):
    if not isinstance(description, str):
        return None
    for line in description.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def classify_opening(line):
    """Opening style of one real line. None when no known pattern is present."""
    if not isinstance(line, str):
        return None
    text = line.strip()
    if not text:
        return None
    if "?" in text:
        return "question"
    if _HOW_TO.match(text):
        return "how_to"
    if _QUESTION_WORD.search(text):
        return "question"
    if re.match(r"^[₹\d]", text):
        # "5 candlestick patterns" is a list; "₹1 लाख" is only a number.
        if re.match(r"^\d", text) and _LIST_WORD.search(text[:60]):
            return "list"
        return "number_led"
    if _WARNING.search(text):
        return "warning"
    if _VERSUS.search(text):
        return "versus"
    if _EXPLAINER.search(text):
        return "explainer"
    return None


def infer_hook(title, description):
    """Pattern from the description's first line, else the title. Never invented."""
    opening = first_line(description)
    if opening:
        kind = classify_opening(opening)
        if kind:
            return kind, "description", opening
    title_text = title.strip() if isinstance(title, str) else ""
    if title_text:
        kind = classify_opening(title_text)
        if kind:
            return kind, "title", opening
    return None, None, opening


def _best_thumbnail(thumbnails):
    if not isinstance(thumbnails, dict):
        return None, None
    best = None
    area = -1
    for meta in thumbnails.values():
        if not isinstance(meta, dict):
            continue
        width, height = optional_int(meta, "width"), optional_int(meta, "height")
        if width is None or height is None or width <= 0 or height <= 0:
            continue
        if width * height > area:
            area = width * height
            best = (width, height)
    if not best:
        return None, None
    return best


def visual_style_notes(snippet, tags):
    """Facts from thumbnail sizes and tag text. Empty string when neither exists."""
    notes = []
    width, height = _best_thumbnail(snippet.get("thumbnails") if isinstance(snippet, dict) else None)
    if width and height:
        notes.append(f"thumbnail {width}x{height}")
    found = []
    for tag in tags:
        low = tag.lower()
        for marker in STYLE_TAG_MARKERS:
            if marker not in low or marker in found:
                continue
            if any(marker in earlier for earlier in found):
                continue
            found.append(marker)
    if found:
        notes.append("tags mention: " + ", ".join(found))
    return "; ".join(notes)


def parse_video(item, source_query):
    """Map one videos.list (or mostPopular) item. None when the id is missing.

    Does not filter duration; as_short() does that. Omitted statistics stay None.
    """
    if not isinstance(item, dict) or not item.get("id"):
        return None
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    tags = []
    for tag in snippet.get("tags") or []:
        if isinstance(tag, str) and tag.strip():
            tags.append(tag.strip())
        if len(tags) >= 15:
            break
    hook, hook_source, opening = infer_hook(snippet.get("title"), snippet.get("description"))
    width, height = _best_thumbnail(snippet.get("thumbnails"))
    title = snippet.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else None
    channel = snippet.get("channelTitle")
    channel = channel.strip() if isinstance(channel, str) and channel.strip() else None
    return {
        "video_id": item["id"],
        "title": title,
        "opening_line": opening,
        "hook_pattern": hook,
        "hook_source": hook_source,
        "duration_s": duration_seconds(details.get("duration")),
        "views": optional_int(stats, "viewCount"),
        "likes": optional_int(stats, "likeCount"),
        "comments": optional_int(stats, "commentCount"),
        "channel_id": snippet.get("channelId") or None,
        "channel_title": channel,
        "published_at": snippet.get("publishedAt") or None,
        "visual_style_notes": visual_style_notes(snippet, tags),
        "thumbnail_width": width,
        "thumbnail_height": height,
        "tags": tags,
        "source_query": source_query,
    }


def as_short(parsed):
    """Keep a row only when duration is a real Short (1–180s)."""
    if not parsed:
        return None
    duration = parsed.get("duration_s")
    if not isinstance(duration, int) or duration < 1 or duration > SHORT_MAX_SECONDS:
        return None
    return parsed


def parse_youtube_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def rank_shorts(shorts):
    """Highest viewCount first. Missing viewCount sorts last, not as zero."""
    def key(row):
        views = row.get("views")
        video_id = row.get("video_id") or ""
        if isinstance(views, int) and not isinstance(views, bool):
            return (0, -views, video_id)
        return (1, 0, video_id)
    return sorted(shorts, key=key)


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(node, name):
    for child in list(node):
        if _local(child.tag) == name:
            text = (child.text or "").strip()
            return text or None
    return None


def _link_href(node):
    for child in list(node):
        if _local(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return None


def normalise_date(raw):
    """Return (stored, datetime|None). Unparsed text is kept; nothing is filled in."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    text = raw.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds"), parsed


def parse_feed(source_name, xml_text, fetched_at, now=None, max_age_days=14, limit=8):
    """RSS or Atom items with source and date. Stale parsed dates are dropped."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = _child_text(node, "title")
        if not title:
            continue
        title = html.unescape(re.sub(r"\s+", " ", title)).strip()
        if not title:
            continue
        published_at, parsed = normalise_date(
            _child_text(node, "pubDate") or _child_text(node, "published") or _child_text(node, "updated"))
        if parsed is not None and parsed < cutoff:
            continue
        link = _child_text(node, "link") or _link_href(node)
        items.append({
            "title": title,
            "source": source_name,
            "source_url": link or None,
            "published_at": published_at,
            "fetched_at": fetched_at,
            "views": None,
        })
        if len(items) >= limit:
            break
    return items


def is_finance_text(*parts):
    blob = " ".join(part for part in parts if isinstance(part, str))
    return any(term_in_text(blob, term) for term in FINANCE_TERMS)


def trending_from_video(item, source_name, fetched_at):
    parsed = parse_video(item, source_name)
    if not parsed or not parsed.get("title"):
        return None
    video_id = parsed["video_id"]
    return {
        "title": parsed["title"],
        "source": source_name,
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "published_at": parsed.get("published_at"),
        "fetched_at": fetched_at,
        "views": parsed.get("views"),
    }


def _tokens(text):
    if not isinstance(text, str):
        return []
    return [tok.lower() for tok in re.findall(r"[\w\u0900-\u097f]{4,}", text.lower().replace("_", " "))]


def build_summary(shorts, trending):
    """Counts and observed lengths only. Empty inputs produce nulls, not guesses."""
    hooks = Counter(row.get("hook_pattern") for row in shorts if row.get("hook_pattern"))
    unknown = sum(1 for row in shorts if not row.get("hook_pattern"))
    titles = [row["title"] for row in shorts if isinstance(row.get("title"), str)]
    title_kinds = Counter(classify_opening(title) for title in titles)
    lengths = sorted(len(title) for title in titles)
    durations = sorted(row["duration_s"] for row in shorts
                       if isinstance(row.get("duration_s"), int))

    def observed(nums, fraction):
        if not nums:
            return None
        # An actual sample, not an interpolation between two lengths.
        return nums[int(round(fraction * (len(nums) - 1)))]

    clusters = []
    for name, terms in CLUSTER_TERMS:
        matched = []
        count = 0
        for row in shorts:
            blob = " ".join(part for part in (row.get("title"), row.get("opening_line")) if part)
            hit = [term for term in terms if term_in_text(blob, term)]
            if hit:
                count += 1
                for term in hit:
                    if term not in matched:
                        matched.append(term)
        if count >= 2 and matched:
            clusters.append({"name": name, "count": count, "keywords": matched})

    headline_titles = [row.get("title") for row in trending if isinstance(row, dict) and row.get("title")]
    per_title = []
    df = Counter()
    for title in headline_titles:
        toks = {tok for tok in _tokens(title) if tok not in GENERIC_TERMS}
        per_title.append(toks)
        df.update(toks)
    keywords = [word for word, n in df.most_common() if n >= 2][:12]
    if keywords:
        hit = sum(1 for toks in per_title if any(word in toks for word in keywords))
        if hit >= 2:
            clusters.append({"name": "headline_terms", "count": hit, "keywords": keywords})

    return {
        "hook_patterns": {name: {"count": count} for name, count in hooks.most_common()},
        "hooks_inferred": sum(hooks.values()),
        "hooks_unknown": unknown,
        "title_formats": {
            "count": len(titles),
            "median_chars": observed(lengths, 0.5),
            "question_titles": title_kinds.get("question", 0),
            "number_led_titles": title_kinds.get("number_led", 0) + title_kinds.get("list", 0),
            "how_to_titles": title_kinds.get("how_to", 0),
            "warning_titles": title_kinds.get("warning", 0),
        },
        "duration_s": {
            "n": len(durations),
            "median": observed(durations, 0.5),
            "p25": observed(durations, 0.25),
            "p75": observed(durations, 0.75),
        },
        "topic_clusters": clusters,
    }


def report_filename(when):
    """reports/trends-YYYY-WW.md using the ISO week in IST."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    iso = when.astimezone(IST).isocalendar()
    return f"trends-{iso.year}-{iso.week:02d}.md"


def _fmt_num(value, suffix=""):
    if value is None:
        return None
    return f"{value}{suffix}"


def render_report(payload):
    """Weekly markdown from one snapshot. Omits numbers the snapshot does not have."""
    generated = payload.get("generated_at") or ""
    try:
        when = datetime.fromisoformat(str(generated))
    except ValueError:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    iso = when.astimezone(IST).isocalendar()
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    hooks = summary.get("hook_patterns") if isinstance(summary.get("hook_patterns"), dict) else {}
    titles = summary.get("title_formats") if isinstance(summary.get("title_formats"), dict) else {}
    duration = summary.get("duration_s") if isinstance(summary.get("duration_s"), dict) else {}
    clusters = summary.get("topic_clusters") if isinstance(summary.get("topic_clusters"), list) else []
    quota = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    lines = [
        f"# Trends {iso.year}-W{iso.week:02d}",
        "",
        f"Generated: {generated or 'unknown'}",
        f"Window: Shorts published after {window.get('published_after') or 'unknown'} "
        f"({window.get('lookback_days', 7)} days), region {window.get('region_code', 'IN')}, "
        f"language {window.get('relevance_language', 'hi')}.",
        f"Auth: {payload.get('auth') or 'none'}",
        f"Quota charged by this job: {quota.get('used_units')} / {quota.get('budget_units')} units. "
        f"Stopped: {quota.get('stopped')}.",
        "",
        "Numbers below are counts or observed values from this snapshot. "
        "Missing API fields stay blank. This report does not publish, upload, or post.",
        "",
        "## Winning hook patterns",
        "",
    ]
    if hooks:
        lines.append("| Pattern | Shorts |")
        lines.append("|---|---|")
        for name, info in hooks.items():
            count = info.get("count") if isinstance(info, dict) else None
            lines.append(f"| {name} | {count} |")
        lines.append("")
        lines.append(f"Inferred: {summary.get('hooks_inferred')}. "
                     f"No pattern in the title or opening line: {summary.get('hooks_unknown')}.")
    else:
        lines.append("No hook pattern could be inferred from the stored titles and opening lines.")
    lines += ["", "## Title formats", ""]
    if titles.get("count"):
        bits = [f"Titles stored: {titles.get('count')}"]
        median = _fmt_num(titles.get("median_chars"), " characters")
        if median:
            bits.append(f"median length {median} (an observed title length)")
        bits.append(f"question openings: {titles.get('question_titles')}")
        bits.append(f"number or list openings: {titles.get('number_led_titles')}")
        bits.append(f"how-to openings: {titles.get('how_to_titles')}")
        bits.append(f"warning openings: {titles.get('warning_titles')}")
        lines.append("- " + "; ".join(bits) + ".")
    else:
        lines.append("No titles in this snapshot.")
    lines += ["", "## Typical lengths", ""]
    if duration.get("n"):
        lines.append(
            f"- Shorts with a parsed duration: {duration.get('n')}. "
            f"Observed median {duration.get('median')}s, "
            f"p25 {duration.get('p25')}s, p75 {duration.get('p75')}s."
        )
    else:
        lines.append("No parsed durations in this snapshot.")
    lines += ["", "## Topic clusters", ""]
    if clusters:
        lines.append("| Cluster | Matches | Keywords seen |")
        lines.append("|---|---|---|")
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            words = ", ".join(str(word) for word in (cluster.get("keywords") or []))
            lines.append(f"| {cluster.get('name')} | {cluster.get('count')} | {words} |")
    else:
        lines.append("No cluster reached 2 matches.")
    lines += [
        "",
        "## Trending topics",
        "",
    ]
    trending = payload.get("trending_topics") or []
    if not trending:
        lines.append("No headlines or finance chart rows in this snapshot.")
    for row in trending:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        when_text = row.get("published_at") or "date not provided"
        url = row.get("source_url") or ""
        extra = f" — {url}" if url else ""
        views = row.get("views")
        view_bit = f", {views} views" if isinstance(views, int) and not isinstance(views, bool) else ""
        lines.append(f"- [{row.get('source')}] {row.get('title')} ({when_text}{view_bit}){extra}")
    lines += [
        "",
        "## Top stored Shorts",
        "",
        "Listed for review. The topic picker does not copy these titles.",
        "",
    ]
    shorts = payload.get("shorts") or []
    if not shorts:
        lines.append("No Shorts stored.")
    for row in shorts[:8]:
        if not isinstance(row, dict):
            continue
        bits = [row.get("channel_title") or row.get("channel_id") or "unknown channel"]
        if isinstance(row.get("duration_s"), int):
            bits.append(f"{row['duration_s']}s")
        if isinstance(row.get("views"), int) and not isinstance(row.get("views"), bool):
            bits.append(f"{row['views']} views")
        if isinstance(row.get("likes"), int) and not isinstance(row.get("likes"), bool):
            bits.append(f"{row['likes']} likes")
        if isinstance(row.get("comments"), int) and not isinstance(row.get("comments"), bool):
            bits.append(f"{row['comments']} comments")
        hook = row.get("hook_pattern") or "no hook pattern"
        lines.append(f"- {row.get('title') or '(no title)'} — {hook} ({', '.join(bits)})")
    lines += [
        "",
        "## Picker",
        "",
        "pick_topic.py adds 0.25 for each repeated cluster whose keyword appears in a "
        "bank topic, capped at 0.8. One strong pattern weight is 2.0 and our own video "
        "result adds up to 0.75, so a trend cannot force the next topic. Placeholder "
        "(templated) topics are never selected. Trends older than 8 days add nothing.",
        "",
        "## Quota and errors",
        "",
    ]
    if quota.get("stop_reason"):
        lines.append(f"- Stop: {quota.get('stop_reason')}")
    skipped = quota.get("skipped") or []
    if skipped:
        lines.append(f"- Skipped calls: {skipped}")
    errors = payload.get("errors") or []
    if errors:
        for err in errors:
            lines.append(f"- {err}")
    if not errors and not skipped and not quota.get("stop_reason"):
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ fetch
def fetch_text(url, timeout=20, limit=1_000_000):
    request = urllib.request.Request(
        url, headers={"User-Agent": "PaiseKiPathshalaTrendResearch/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(limit).decode("utf-8", "replace")


def most_popular(yt, budget, region, category_id=None):
    kwargs = dict(part="snippet,statistics,contentDetails", chart="mostPopular",
                  regionCode=region, maxResults=25)
    if category_id:
        kwargs["videoCategoryId"] = str(category_id)
    resp = CR.yt_execute(yt.videos().list(**kwargs), "videos.list", budget)
    if not resp:
        return []
    return resp.get("items") or []


def _remember_error(errors, exc, label):
    if CR.quota_exceeded(exc):
        return "quota"
    errors.append(f"{label}: {type(exc).__name__}")
    return None


def collect(yt, settings, budget, fetch_text_fn, now=None, channel_ids=None, feeds=None):
    """Build the trends payload. `yt` may be None (RSS only)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    since_dt = now - timedelta(days=settings["lookback_days"])
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fetched_at = now.isoformat(timespec="seconds")
    errors = []
    rc = {"region_code": settings["region_code"], "relevance_language": settings["relevance_language"]}
    found = {}
    shorts = []
    chart_rows = []
    own = (os.environ.get("EXPECTED_CHANNEL_ID") or "").strip()

    if yt is None:
        errors.append("youtube skipped: no YT_DATA_API_KEY and no OAuth credentials")
    else:
        for query in settings["search_queries"]:
            if budget.stopped:
                break
            try:
                for video_id in CR.search_ids(
                        yt, query, since, rc, budget,
                        max_results=settings["max_results"]):
                    found.setdefault(video_id, query)
            except Exception as exc:  # HttpError and transport errors
                if _remember_error(errors, exc, f"search {query!r}") == "quota":
                    budget.stop("youtube quotaExceeded")
                    break
        for cid in channel_ids or []:
            if budget.stopped:
                break
            try:
                info, video_ids = CR.channel_uploads(yt, cid, since, 10, budget)
            except Exception as exc:
                if _remember_error(errors, exc, f"channel {cid}") == "quota":
                    budget.stop("youtube quotaExceeded")
                    break
                continue
            label = f"channel:{(info or {}).get('title') or cid}"
            for video_id in video_ids:
                found.setdefault(video_id, label)
        items = []
        if found and not budget.stopped:
            try:
                items = CR.raw_videos(yt, list(found), budget)
            except Exception as exc:
                if _remember_error(errors, exc, "videos.list") == "quota":
                    budget.stop("youtube quotaExceeded")
        seen = set()
        for item in items:
            parsed = as_short(parse_video(item, found.get(item.get("id"))))
            if not parsed or parsed["video_id"] in seen:
                continue
            if own and parsed.get("channel_id") == own:
                continue
            published = parse_youtube_time(parsed.get("published_at"))
            if published is None or published < since_dt:
                continue
            seen.add(parsed["video_id"])
            shorts.append(parsed)
        for category, source_name in ((None, "youtube_most_popular_IN"),
                                      ("25", "youtube_most_popular_IN_news")):
            if budget.stopped:
                break
            try:
                popular = most_popular(yt, budget, settings["region_code"], category)
            except Exception as exc:
                if _remember_error(errors, exc, source_name) == "quota":
                    budget.stop("youtube quotaExceeded")
                    break
                continue
            for item in popular:
                snippet = item.get("snippet") if isinstance(item, dict) else {}
                if not is_finance_text((snippet or {}).get("title"), (snippet or {}).get("description")):
                    continue
                topic = trending_from_video(item, source_name, fetched_at)
                if topic:
                    chart_rows.append(topic)
                parsed = as_short(parse_video(item, source_name))
                if not parsed or parsed["video_id"] in seen:
                    continue
                if own and parsed.get("channel_id") == own:
                    continue
                published = parse_youtube_time(parsed.get("published_at"))
                if published is None or published < since_dt:
                    continue
                seen.add(parsed["video_id"])
                shorts.append(parsed)

    news = []
    for feed in feeds or []:
        try:
            xml_text = fetch_text_fn(feed["url"])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"feed {feed['name']}: {type(exc).__name__}")
            continue
        except Exception as exc:  # noqa: BLE001 -- one bad feed must not abort the run
            errors.append(f"feed {feed['name']}: {type(exc).__name__}")
            continue
        news.extend(parse_feed(
            feed["name"], xml_text, fetched_at, now=now,
            max_age_days=settings["news_max_age_days"], limit=settings["max_per_feed"]))
    if budget.note and not budget.stopped:
        errors.append(budget.note)
    ranked = rank_shorts(shorts)
    stored = ranked[:settings["max_shorts"]]
    trending = news + chart_rows
    return {
        "schema_version": 1,
        "schema_note": SCHEMA_NOTE,
        "generated_at": fetched_at,
        "auth": None,
        "window": {
            "lookback_days": settings["lookback_days"],
            "published_after": since,
            "region_code": settings["region_code"],
            "relevance_language": settings["relevance_language"],
        },
        "quota": {
            "budget_units": budget.budget,
            "used_units": budget.used,
            "stopped": budget.stopped,
            "stop_reason": budget.stop_reason,
            "skipped": budget.skipped,
            "calls": budget.calls,
        },
        "shorts_collected": len(ranked),
        "shorts": stored,
        "trending_topics": trending,
        "summary": build_summary(stored, trending),
        "errors": errors,
        "policy": POLICY,
    }


def _write(path, data):
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only daily trend research. Never publishes.")
    parser.add_argument("--dry-run", action="store_true", help="fetch and print, write nothing")
    args = parser.parse_args(argv)
    settings = trend_settings()
    budget = CR.QuotaBudget(settings["quota_budget_units"])
    yt, how = CR.get_client()
    payload = collect(
        yt, settings, budget, fetch_text,
        channel_ids=competitor_channel_ids(),
        feeds=news_feeds(),
    )
    payload["auth"] = how if yt is not None else None
    summary = payload["summary"]
    print(f"[trends] auth={payload['auth'] or 'none'} quota={budget.used}/{budget.budget} "
          f"shorts={len(payload['shorts'])}/{payload['shorts_collected']} "
          f"trending={len(payload['trending_topics'])} "
          f"clusters={len(summary.get('topic_clusters') or [])} "
          f"stopped={budget.stopped}")
    if payload["errors"]:
        print("[trends] notes: " + "; ".join(str(err) for err in payload["errors"]))
    if args.dry_run:
        print("[trends] dry-run: wrote nothing")
        return 0
    TRENDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _write(TRENDS_PATH, payload)
    try:
        when = datetime.fromisoformat(str(payload.get("generated_at")))
    except (TypeError, ValueError):
        when = datetime.now(timezone.utc)
    report_path = REPORTS_DIR / report_filename(when)
    report_path.write_text(render_report(payload), encoding="utf-8")

    def _shown(path):
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    print(f"[trends] wrote {_shown(TRENDS_PATH)} and {_shown(report_path)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
