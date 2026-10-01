"""Optional, vidIQ-free competitor signal scoring for topic-bank rotation.

Only aggregate metadata is consumed here. Do not store competitor titles,
scripts, thumbnails, or trade calls in the performance snapshot.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Automatic trends are a small tie-break. One strong pattern weight is 2.0
# (label value 1.0 × 2) and our own videos add up to 0.75, so 0.8 cannot
# force a topic the rest of the score disagrees with.
TREND_WEIGHT_CAP = 0.8
TREND_CLUSTER_BONUS = 0.25
TREND_MAX_AGE_DAYS = 8


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def pattern_weights(data):
    """Return dimension -> label -> bounded [0, 1] signal weights.

    Each observation is compared with its creator's baseline views. Ratios are
    capped to dampen one viral outlier and minimum samples prevent a single
    upload from becoming a rule.
    """
    grouped = {}
    observations = data.get("observations", []) if isinstance(data, dict) else []
    if not isinstance(observations, list):
        observations = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        try:
            if item.get("performance_index") is not None:
                ratio = float(item["performance_index"])
            else:
                views = max(0, float(item.get("views", 0)))
                baseline = max(1, float(item.get("baseline_views", 0)))
                ratio = views / baseline
        except (TypeError, ValueError):
            continue
        ratio = max(0.0, min(3.0, ratio))
        for dimension in ("topic_type", "hook_style", "pacing", "visual_style", "angle"):
            label = str(item.get(dimension) or "").strip().lower()
            if label:
                grouped.setdefault(dimension, {}).setdefault(label, []).append(ratio)

    result = {}
    for dimension, labels in grouped.items():
        result[dimension] = {}
        for label, ratios in labels.items():
            if len(ratios) < 2:
                continue
            # A stable 2x performance is strongest. Normalize and dampen.
            mean = sum(ratios) / len(ratios)
            result[dimension][label] = round(max(0.0, min(1.0, (mean - 0.75) / 1.5)), 3)
    return result


def _topic_labels(topic):
    labels = {str(topic.get("cat") or "").strip().lower()}
    text = " ".join(str(topic.get(field) or "") for field in ("topic", "key", "seo", "cat")).lower()
    beats = topic.get("beats") or {}
    if isinstance(beats, dict):
        text += " " + " ".join(str(value) for group in beats.values() for value in (group if isinstance(group, list) else [group]))
    # Lightweight, explainable labels connect aggregate observations to bank
    # entries without passing competitor wording into the topic generator.
    if any(word in text for word in ("गलती", "जाल", "जोखिम", "loss", "risk", "trap", "mistake")):
        labels.update(("mistake_prevention", "warning"))
    if any(word in text for word in ("क्यों", "कैसे", "क्या", "?", "समझें")):
        labels.update(("curiosity_question", "mechanism_explainer"))
    if any(word in text for word in ("₹", "लाख", "करोड़", "2010", "2026", "डेटा", "हिसाब")):
        labels.update(("number_led", "real_data"))
    if any(word in text for word in ("कैंडल", "wick", "chart", "pattern", "indicator", "rsi", "macd")):
        labels.add("chart_concept")
    if topic.get("key"):
        labels.add("whiteboard_chart")
    labels.update(str(x).strip().lower() for x in topic.get("research_tags", []) if str(x).strip())
    # Optional bank metadata maps candidates to observed presentation patterns.
    for field in ("topic_type", "hook_style", "pacing", "visual_style", "angle"):
        value = topic.get(field)
        if value:
            labels.add(str(value).strip().lower())
    return labels


def term_in_text(text, term):
    """Word-boundary match. Underscores and hyphens count as spaces.

    Hindi terms and multi-word terms use a substring check because Devanagari
    has no case and phrases like "bank nifty" are not single tokens.
    """
    if not isinstance(text, str):
        return False
    term = str(term or "").strip().lower().replace("_", " ").replace("-", " ")
    term = re.sub(r"\s+", " ", term)
    if len(term) < 2:
        return False
    hay = re.sub(r"\s+", " ", str(text).lower().replace("_", " ").replace("-", " "))
    if re.search(r"[\u0900-\u097f]", term) or any(ch in term for ch in " /&"):
        return term in hay
    return re.search(rf"(?<![\w\u0900-\u097f]){re.escape(term)}(?![\w\u0900-\u097f])", hay) is not None


def _topic_blob(topic):
    parts = [topic.get("topic"), topic.get("key"), topic.get("seo"), topic.get("cat")]
    facts = topic.get("facts") or []
    if isinstance(facts, list):
        parts.extend(facts)
    beats = topic.get("beats") or {}
    if isinstance(beats, dict):
        for group in beats.values():
            if isinstance(group, list):
                parts.extend(group)
            elif isinstance(group, str):
                parts.append(group)
    tags = topic.get("research_tags") or []
    if isinstance(tags, list):
        parts.extend(tags)
    return " ".join(str(p) for p in parts if p)


def _trends_generated_at(trends):
    if not isinstance(trends, dict):
        return None
    raw = trends.get("generated_at")
    if not raw:
        return None
    try:
        gen = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    return gen.astimezone(timezone.utc)


def trend_weight(topic, trends):
    """Capped boost when a bank topic overlaps repeated trend clusters.

    Missing, malformed, or stale trends (older than TREND_MAX_AGE_DAYS) add
    nothing, so the normal rotation is unchanged. The same keyword does not
    pay twice across clusters.
    """
    gen = _trends_generated_at(trends)
    if gen is None:
        return 0.0
    if datetime.now(timezone.utc) - gen > timedelta(days=TREND_MAX_AGE_DAYS):
        return 0.0
    summary = trends.get("summary") if isinstance(trends.get("summary"), dict) else {}
    clusters = summary.get("topic_clusters") or []
    if not isinstance(clusters, list):
        return 0.0
    text = _topic_blob(topic if isinstance(topic, dict) else {})
    bonus = 0.0
    used = set()
    for cluster in clusters:
        if bonus >= TREND_WEIGHT_CAP or not isinstance(cluster, dict):
            continue
        try:
            count = int(cluster.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if count < 2:
            continue
        keywords = []
        for raw in cluster.get("keywords") or []:
            word = str(raw or "").strip().lower()
            if word:
                keywords.append(word)
        fresh = [word for word in keywords if word not in used and term_in_text(text, word)]
        if not fresh:
            continue
        bonus += TREND_CLUSTER_BONUS
        used.update(fresh)
    return round(min(TREND_WEIGHT_CAP, bonus), 3)


def score_topic(topic, weights, market_topics=None, own_performance=None, video_format=None, trends=None):
    """Rank a bank topic from signals, freshness and first-party outcomes.

    ``trends`` (data/trends.json) adds at most TREND_WEIGHT_CAP. It never
    removes candidates; pick_topic.py drops placeholder topics before scoring.
    """
    labels = _topic_labels(topic)
    if video_format in ("short", "long"):
        labels.add("fast_paced" if video_format == "short" else "deep_dive")
    score = 0.0
    for dimension, values in (weights or {}).items():
        for label, value in values.items():
            if label in labels:
                score += float(value) * 2.0

    # Dated, manually verified market signals are small boosts, never facts.
    key = str(topic.get("key") or "")
    market = market_topics.get("topics", []) if isinstance(market_topics, dict) else []
    if not isinstance(market, list):
        market = []
    for item in market:
        if isinstance(item, dict) and item.get("topic_key") == key:
            try:
                priority = max(0, min(2, float(item.get("priority", 0))))
            except (TypeError, ValueError):
                priority = 0
            score += priority * 0.5
            break

    outcomes = own_performance.get("topics", {}) if isinstance(own_performance, dict) else {}
    if not isinstance(outcomes, dict):
        outcomes = {}
    try:
        # Capped first-party lift is more reliable than competitor signals.
        score += max(-1.0, min(1.0, float(outcomes.get(key, 0)))) * 0.75
    except (TypeError, ValueError):
        pass
    score += trend_weight(topic, trends)
    return score


def load_trends(root: Path):
    """Latest automatic trend snapshot. Missing or broken file = no boost."""
    data = _read_json(Path(root) / "data" / "trends.json", {})
    return data if isinstance(data, dict) else {}


def load_research(root: Path):
    """Load optional inputs; absent or malformed research never blocks runs."""
    performance = _read_json(root / "data" / "competitor_performance.json", {})
    market = _read_json(root / "data" / "current_market_topics.json", {})
    own = _read_json(root / "data" / "own_topic_performance.json", {})
    return pattern_weights(performance), market, own


def is_near_competitor_duplicate(topic_text, phrases):
    text = re.sub(r"[^\w]+", " ", str(topic_text).lower()).strip()
    words = set(text.split())
    for phrase in phrases:
        normalized = re.sub(r"[^\w]+", " ", str(phrase).lower()).strip()
        if not normalized:
            continue
        if normalized in text:
            return True
        phrase_words = set(normalized.split())
        if len(phrase_words) >= 2 and phrase_words <= words:
            return True
    return False

