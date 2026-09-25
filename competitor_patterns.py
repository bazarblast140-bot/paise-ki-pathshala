"""Optional, vidIQ-free competitor signal scoring for topic-bank rotation.

Only aggregate metadata is consumed here. Do not store competitor titles,
scripts, thumbnails, or trade calls in the performance snapshot.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def pattern_weights(data):
    """Return dimension -> label -> bounded [0, 1] signal weights.

    Each observation is compared with its creator's baseline views. Median
    multiples dampen one viral outlier and minimum sample counts prevent a
    single upload from becoming a rule.
    """
    grouped = {}
    observations = data.get("observations", []) if isinstance(data, dict) else []
    if not isinstance(observations, list):
        observations = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        try:
            views = max(0, float(item.get("views", 0)))
            baseline = max(1, float(item.get("baseline_views", 0)))
        except (TypeError, ValueError):
            continue
        ratio = min(3.0, views / baseline)
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


def score_topic(topic, weights, market_topics=None, own_performance=None, video_format=None):
    """Rank a bank topic from signals, freshness and first-party outcomes."""
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
    return score


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

