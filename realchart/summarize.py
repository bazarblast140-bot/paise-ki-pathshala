"""Short Hindi summary of one real article (one Gemini text request, cached per article).

The model only restates the article: every number it writes must appear in the
article text, or the summary is refused and the news Reel HOLDs.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

TEXT_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ("gemini-flash-latest", "gemini-3.5-flash")
CACHE = Path(__file__).resolve().parent.parent / "data" / "gemini_cache"
PROMPT = (
    "You write the voice-over for a 40-second Hindi stock-market news Reel. Using ONLY the article below, "
    "return JSON {\"lead\": str, \"points\": [str, str]}. lead: one Hindi (Devanagari) sentence, at most 13 words, "
    "saying what happened. points: two Hindi sentences, at most 14 words each, with the article's key facts. "
    "Write numbers with digits exactly as in the article (you may drop commas or 'Rs' and say करोड़/प्रतिशत). "
    "Company and person names stay in English letters. No advice, no opinion, no prediction, nothing that is "
    "not in the article. Output JSON only.\n\nARTICLE\nTitle: {title}\nSummary: {summary}\nKey sentence: {key}\n"
)
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


class SummaryRefused(Exception):
    pass


def _numbers(text):
    return {float(m.replace(",", "")) for m in NUM.findall(str(text or "")) if m.replace(",", "").replace(".", "")}


def check(summary, article):
    """Empty when the summary is short Hindi whose numbers are all in the article."""
    faults = []
    source = " ".join(str(article.get(k) or "") for k in ("title", "summary", "key_sentence", "why"))
    have = _numbers(source)
    parts = [summary.get("lead") or ""] + list(summary.get("points") or [])
    if len(parts) != 3 or not all(isinstance(p, str) and p.strip() for p in parts):
        return ["summary needs a lead and two points"]
    for part in parts:
        if not re.search(r"[\u0900-\u097F]", part):
            faults.append(f"not Hindi: {part[:40]}")
        if len(part.split()) > 20:
            faults.append(f"too long: {part[:40]}")
        for value in _numbers(part):
            if value not in have:
                faults.append(f"number {value:g} is not in the article")
    return faults


def hindi_summary(article, dry_run=False):
    key = hashlib.sha1(json.dumps([article.get("url"), article.get("title")]).encode("utf-8")).hexdigest()[:12]
    if dry_run and not (CACHE / f"summary-{key}.json").is_file():
        source = str(article.get("source") or "आर्टिकल")
        return {"lead": f"{source} की यह खबर स्क्रीन पर है।", "points": ["पूरी हेडलाइन कार्ड पर पढ़ें।",
                                                                        "यह असली आर्टिकल से है।"]}
    key = hashlib.sha1(json.dumps([article.get("url"), article.get("title")]).encode("utf-8")).hexdigest()[:12]
    cached = CACHE / f"summary-{key}.json"
    if cached.is_file():
        summary = json.loads(cached.read_text(encoding="utf-8"))
    else:
        summary = _ask(article)
    faults = check(summary, article)
    if faults:
        raise SummaryRefused("; ".join(faults))
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def _ask(article):
    import gemini_voice
    prompt = (PROMPT.replace("{title}", str(article.get("title") or ""))
              .replace("{summary}", str(article.get("summary") or ""))
              .replace("{key}", str(article.get("key_sentence") or "")))
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}).encode()
    import time
    last = None
    # 429 -> next key; 5xx (overloaded) -> wait, then the fallback model.
    for model in (TEXT_MODEL,) + FALLBACK_MODELS:
        for _slot, secret in gemini_voice.api_keys():
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=body, headers={"Content-Type": "application/json", "x-goog-api-key": secret})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except urllib.error.HTTPError as exc:
                last = f"{model} HTTP {exc.code}"
                if exc.code >= 500:
                    time.sleep(5)
                    break
                if exc.code != 429:
                    raise SummaryRefused(f"summary request failed: {last}") from exc
            except (KeyError, IndexError, ValueError) as exc:
                last = f"{model} bad response: {type(exc).__name__}"
                break
    raise SummaryRefused(f"summary request failed: {last}")
