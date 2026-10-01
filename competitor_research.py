"""Competitor + own-channel research -> insights the Shorts pipeline learns from.

    python competitor_research.py            # fetch (if creds) and write data/*.json
    python competitor_research.py --dry-run  # fetch + print summary, write nothing
    python competitor_research.py --max-age-hours 20   # skip if insights are fresher

What it does (read-only YouTube Data API calls, never uploads/edits anything):
  1. Recent uploads of the competitor channels in data/research_sources.json
     (+ config.json research.extra_channel_ids) and top results for
     research.search_keywords (Hindi finance Shorts niche).
  2. Scores each video vs. its own channel's median views (performance index),
     so a big channel doesn't win just for being big.
  3. Writes:
       data/competitor_insights.json   top videos (for Rajesh to read), winning
                                       title patterns, keywords, durations, upload hours
       data/competitor_performance.json  auto rows (labels only, no titles) that
                                       pick_topic.py already uses to rank topics
       data/own_topic_performance.json  how OUR uploads did per topic key, so topics
                                       that work for us get picked more
  4. prompt_hint() returns a short, aggregate "what works" note (never a
     competitor title). It is NOT wired into board_short.py's narration prompt
     on purpose (main's narration QA logic is left untouched); call it from
     there later if you want the hint in the prompt.

Credentials (first one found wins; none = clean skip, exit 0):
  YT_DATA_API_KEY                          (GitHub secret; simplest, 10k units/day quota)
  YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN   (same channel OAuth used for uploads)
Quota: ~3-6 units per channel + 100 per search keyword (max 3) ~= 320-350 units/run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
INSIGHTS = DATA / "competitor_insights.json"
PERF = DATA / "competitor_performance.json"
OWN = DATA / "own_topic_performance.json"
LEDGER = DATA / "published.json"
IST = timezone(timedelta(hours=5, minutes=30))
AUTO_SOURCE = "yt_api_auto"

STOP = {"the", "a", "of", "in", "to", "and", "for", "is", "on", "with", "hindi", "shorts", "short",
        "video", "kya", "hai", "ka", "ki", "ke", "se", "me", "mein", "को", "का", "की", "के", "है",
        "में", "से", "और", "क्या", "ये", "यह", "तो", "पर", "एक", "हैं", "नहीं", "कैसे", "क्यों"}


# ------------------------------------------------------------------ helpers
def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write(path, data):
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def research_cfg():
    c = _read(ROOT / "config.json", {})
    r = c.get("research") or {}
    return {
        "enabled": bool(r.get("enabled", True)),
        "lookback_days": int(r.get("lookback_days", 30)),
        "max_videos_per_channel": int(r.get("max_videos_per_channel", 25)),
        "search_keywords": list(r.get("search_keywords") or [])[: int(r.get("max_search_keywords", 3))],
        "region_code": r.get("region_code", "IN"),
        "relevance_language": r.get("relevance_language", "hi"),
        "include_own_channel": bool(r.get("include_own_channel", True)),
        "extra_channel_ids": list(r.get("extra_channel_ids") or []),
    }


def iso_duration_seconds(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def title_labels(title):
    """Map a title to the same label vocabulary competitor_patterns.py understands."""
    t = (title or "").lower()
    labels = set()
    if "?" in t or any(w in t for w in ("क्यों", "कैसे", "क्या", "kaise", "kyun", "why", "how", "what")):
        labels.add("curiosity_question")
    if re.search(r"\d", t) or any(w in t for w in ("₹", "लाख", "करोड़", "lakh", "crore", "%")):
        labels.add("number_led")
    if any(w in t for w in ("गलती", "mistake", "galti", "trap", "जाल", "loss", "नुकसान", "मत", "never", "avoid", "सावधान", "warning", "danger")):
        labels.update(("mistake_prevention", "warning"))
    if any(w in t for w in ("candle", "कैंडल", "chart", "चार्ट", "pattern", "पैटर्न", "rsi", "macd", "ema", "vwap", "indicator", "support", "resistance", "breakout")):
        labels.add("chart_concept")
    if any(w in t for w in ("data", "डेटा", "return", "रिटर्न", "sip", "nifty", "sensex", "cagr", "हिसाब")):
        labels.add("real_data")
    if any(w in t for w in ("समझ", "matlab", "मतलब", "explained", "कैसे काम", "how it works", "secret", "राज़")):
        labels.add("mechanism_explainer")
    return labels


def words(title):
    t = re.sub(r"#\S+", " ", (title or "").lower())
    return [w for w in re.findall(r"[\w\u0900-\u097f]+", t) if len(w) > 2 and w not in STOP and not w.isdigit()]


# ------------------------------------------------------------------ API
def get_client():
    key = (os.environ.get("YT_DATA_API_KEY") or "").strip()
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None, "google-api-python-client not installed"
    if key:
        return build("youtube", "v3", developerKey=key, cache_discovery=False), "api_key"
    try:
        from yt_credentials import youtube_client, missing_env
        if missing_env():
            return None, "no YT_DATA_API_KEY and no YouTube OAuth env"
        return youtube_client(), "oauth"
    except Exception as e:  # noqa: BLE001 -- dead token etc. must not break the pipeline
        return None, f"oauth unavailable ({type(e).__name__})"


def channel_uploads(yt, channel_id, since, limit):
    ch = yt.channels().list(part="contentDetails,snippet,statistics", id=channel_id).execute().get("items") or []
    if not ch:
        return None, []
    info = {"id": channel_id, "title": ch[0]["snippet"]["title"],
            "subscribers": int(ch[0].get("statistics", {}).get("subscriberCount", 0) or 0)}
    uploads = ch[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while len(ids) < limit:
        resp = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                       pageToken=token).execute()
        for it in resp.get("items", []):
            pub = it["contentDetails"].get("videoPublishedAt", "")
            if pub and pub < since:
                return info, ids
            ids.append(it["contentDetails"]["videoId"])
            if len(ids) >= limit:
                break
        token = resp.get("nextPageToken")
        if not token:
            break
    return info, ids


def search_ids(yt, q, since, rc):
    resp = yt.search().list(part="id", q=q, type="video", order="viewCount", publishedAfter=since,
                            regionCode=rc["region_code"], relevanceLanguage=rc["relevance_language"],
                            videoDuration="short", maxResults=25).execute()
    return [i["id"]["videoId"] for i in resp.get("items", []) if i.get("id", {}).get("videoId")]


def video_details(yt, ids):
    out = []
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in resp.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            pub = datetime.strptime(sn["publishedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_days = max(0.5, (datetime.now(timezone.utc) - pub).total_seconds() / 86400)
            views = int(st.get("viewCount", 0) or 0)
            out.append({
                "id": v["id"], "channel_id": sn["channelId"], "channel": sn.get("channelTitle", ""),
                "title": sn.get("title", ""), "tags": (sn.get("tags") or [])[:12],
                "published_ist": pub.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
                "hour_ist": pub.astimezone(IST).hour, "weekday": pub.astimezone(IST).strftime("%a"),
                "duration_s": iso_duration_seconds(v.get("contentDetails", {}).get("duration")),
                "views": views, "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
                "views_per_day": round(views / age_days, 1),
            })
    return out


# ------------------------------------------------------------------ analysis
def add_performance_index(videos):
    by_ch = defaultdict(list)
    for v in videos:
        by_ch[v["channel_id"]].append(v["views_per_day"])
    med = {c: max(1.0, statistics.median(x)) for c, x in by_ch.items()}
    for v in videos:
        # vs. the channel's own typical video; capped so one viral outlier can't dominate
        v["performance_index"] = round(min(5.0, v["views_per_day"] / med[v["channel_id"]]), 2)
    return videos


def analyse(videos):
    if not videos:
        return {}
    videos = sorted(videos, key=lambda v: (v["performance_index"], v["views_per_day"]), reverse=True)
    top = videos[: max(5, len(videos) // 4)]
    rest = videos[len(top):] or videos

    def rate(group, label):
        return sum(1 for v in group if label in title_labels(v["title"])) / max(1, len(group))

    patterns = {}
    for label in ("curiosity_question", "number_led", "warning", "chart_concept", "real_data", "mechanism_explainer"):
        tr, rr = rate(top, label), rate(rest, label)
        patterns[label] = {"top_share": round(tr, 2), "rest_share": round(rr, 2),
                           "lift": round((tr + 0.05) / (rr + 0.05), 2)}
    top_words = Counter(w for v in top for w in set(words(v["title"])))
    all_words = Counter(w for v in videos for w in set(words(v["title"])))
    keywords = [w for w, n in top_words.most_common(40) if n >= 2 and n / max(1, all_words[w]) >= 0.4][:15]
    tag_counts = Counter(t.lower() for v in top for t in v["tags"])
    hours = Counter(v["hour_ist"] for v in top)
    shorts = [v for v in top if 0 < v["duration_s"] <= 180]
    return {
        "videos_analysed": len(videos),
        "title_patterns": patterns,
        "winning_keywords": keywords,
        "top_tags": [t for t, _ in tag_counts.most_common(15)],
        "best_upload_hours_ist": [h for h, _ in hours.most_common(4)],
        "top_short_duration_s": {
            "median": int(statistics.median([v["duration_s"] for v in shorts])) if shorts else None,
            "p25": int(sorted(v["duration_s"] for v in shorts)[len(shorts) // 4]) if shorts else None,
            "p75": int(sorted(v["duration_s"] for v in shorts)[(3 * len(shorts)) // 4]) if shorts else None,
        },
        "top_title_length_chars": int(statistics.median([len(v["title"]) for v in top])),
        "top_videos": [{k: v[k] for k in ("channel", "title", "views", "views_per_day", "performance_index",
                                          "duration_s", "published_ist", "tags")} for v in top[:15]],
    }


def auto_observations(videos):
    rows = []
    for v in videos:
        labels = title_labels(v["title"])
        hook = next((x for x in ("curiosity_question", "number_led", "warning") if x in labels), None)
        topic = next((x for x in ("chart_concept", "real_data", "mistake_prevention", "mechanism_explainer")
                      if x in labels), None)
        if not (hook or topic):
            continue
        row = {"channel": v["channel"], "source": AUTO_SOURCE,
               "pacing": "fast_paced" if 0 < v["duration_s"] <= 180 else "deep_dive",
               "performance_index": v["performance_index"]}
        if hook:
            row["hook_style"] = hook
        if topic:
            row["topic_type"] = topic
        rows.append(row)
    return rows


def own_performance(yt, channel_id):
    """Score OUR uploads per topic_key (ledger rows written by publish.py)."""
    ledger = [e for e in _read(LEDGER, []) if isinstance(e, dict) and e.get("video_id") and e.get("topic_key")]
    if not ledger:
        return {}
    vids = {v["id"]: v for v in video_details(yt, [e["video_id"] for e in ledger][-200:])}
    rows = [(e["topic_key"], vids[e["video_id"]]["views_per_day"]) for e in ledger if e["video_id"] in vids]
    if not rows:
        return {}
    med = max(1.0, statistics.median(x for _, x in rows))
    per = defaultdict(list)
    for key, vpd in rows:
        per[key].append(max(-1.0, min(1.0, math.log2(max(0.01, vpd) / med))))
    return {k: round(sum(x) / len(x), 3) for k, x in per.items()}


# ------------------------------------------------------------------ prompt hint
def prompt_hint(max_age_days=7):
    ins = _read(INSIGHTS, {})
    a = ins.get("analysis") or {}
    try:
        gen = datetime.fromisoformat(ins["generated_at"])
        if datetime.now(timezone.utc) - gen > timedelta(days=max_age_days):
            return ""
    except (KeyError, ValueError, TypeError):
        return ""
    pats = a.get("title_patterns") or {}
    good = [k for k, v in sorted(pats.items(), key=lambda kv: -kv[1].get("lift", 1)) if v.get("lift", 1) >= 1.2][:3]
    names = {"curiosity_question": "a clear question", "number_led": "a concrete number from the topic",
             "warning": "a mistake/warning angle", "chart_concept": "the chart/pattern name",
             "real_data": "real data (index/return)", "mechanism_explainer": "'why/how it works' framing"}
    parts = []
    if good:
        parts.append("titles/hooks that use " + ", ".join(names.get(g, g) for g in good))
    kw = a.get("winning_keywords") or []
    if kw:
        parts.append("search words viewers respond to: " + ", ".join(kw[:8]))
    if a.get("top_title_length_chars"):
        parts.append(f"title length around {a['top_title_length_chars']} characters")
    if not parts:
        return ""
    return ("\n- Audience signal (aggregate stats from similar Hindi finance Shorts, last "
            f"{ins.get('lookback_days', 30)} days — NEVER copy a competitor's title or script): "
            "work in " + "; ".join(parts) + ". Only when it fits this topic honestly; SEBI rules still win.")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-age-hours", type=float, default=0)
    args = ap.parse_args()
    rc = research_cfg()
    if not rc["enabled"]:
        print("[research] disabled in config.json -> research.enabled")
        return 0
    if args.max_age_hours:
        try:
            gen = datetime.fromisoformat(_read(INSIGHTS, {})["generated_at"])
            if datetime.now(timezone.utc) - gen < timedelta(hours=args.max_age_hours):
                print(f"[research] insights fresher than {args.max_age_hours}h — skip")
                return 0
        except (KeyError, ValueError, TypeError):
            pass
    yt, how = get_client()
    if yt is None:
        print(f"[research] skipped: {how}. Add secret YT_DATA_API_KEY to enable competitor research.")
        return 0
    since_dt = datetime.now(timezone.utc) - timedelta(days=rc["lookback_days"])
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    sources = _read(DATA / "research_sources.json", {})
    ch_ids = [c.get("channel_id") for c in sources.get("competitors", []) if c.get("channel_id")]
    ch_ids += [c for c in rc["extra_channel_ids"] if c not in ch_ids]

    from googleapiclient.errors import HttpError
    ids, channels, errors = [], [], []
    for cid in ch_ids:
        try:
            info, vids = channel_uploads(yt, cid, since, rc["max_videos_per_channel"])
            if info:
                channels.append(info)
                ids += vids
        except HttpError as e:
            errors.append(f"channel {cid}: HTTP {e.resp.status}")
    for q in rc["search_keywords"]:
        try:
            ids += search_ids(yt, q, since, rc)
        except HttpError as e:
            errors.append(f"search '{q}': HTTP {e.resp.status}")
    own_id = (os.environ.get("EXPECTED_CHANNEL_ID") or "").strip()
    ids = list(dict.fromkeys(ids))
    videos = add_performance_index([v for v in video_details(yt, ids) if v["channel_id"] != own_id]) if ids else []
    analysis = analyse(videos)
    own = {}
    if rc["include_own_channel"]:
        try:
            own = own_performance(yt, own_id)
        except HttpError as e:
            errors.append(f"own channel: HTTP {e.resp.status}")

    print(f"[research] via {how}: {len(channels)} channels, {len(videos)} videos analysed, "
          f"{len(own)} own topics scored" + (f"; errors: {errors}" if errors else ""))
    if analysis:
        print(f"[research] winning keywords: {analysis['winning_keywords'][:8]}")
        print(f"[research] best upload hours IST: {analysis['best_upload_hours_ist']}; "
              f"top short median duration: {analysis['top_short_duration_s']['median']}s")
    if args.dry_run or not videos:
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(INSIGHTS, {"generated_at": now, "lookback_days": rc["lookback_days"], "via": how,
                      "channels": channels, "search_keywords": rc["search_keywords"],
                      "errors": errors, "analysis": analysis,
                      "policy": "Reference only. Learn patterns; never reuse competitor titles, scripts, thumbnails or layouts."})
    perf = _read(PERF, {"schema_version": 1, "observations": []})
    manual = [o for o in perf.get("observations", []) if isinstance(o, dict) and o.get("source") != AUTO_SOURCE]
    perf["observations"] = manual + auto_observations(videos)
    perf["updated_at"] = now[:10]
    _write(PERF, perf)
    if own:
        o = _read(OWN, {"schema_version": 1, "topics": {}})
        o.setdefault("topics", {}).update(own)
        o["updated_at"] = now[:10]
        _write(OWN, o)
    print("[research] wrote data/competitor_insights.json, competitor_performance.json"
          + (", own_topic_performance.json" if own else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 -- research is optional; never fail the pipeline
        print(f"[research] skipped after error: {type(e).__name__}: {str(e)[:200]}")
        sys.exit(0)
