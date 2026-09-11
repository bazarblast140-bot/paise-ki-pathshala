"""Fetch and cache real stock footage from Pexels, keyed by the Pexels asset id.

Cached under assets/cache/ so re-renders cost nothing — Pexels is free but
rate-limited, and re-downloading the same clip is wasteful.

Reused-content guardrail: a channel-wide ledger (assets/used_clips.json) records
every Pexels asset this channel has ever used. When a new video searches a query,
any asset already in the ledger is skipped, so the SAME stock clip never recurs
across videos — the "repetitious content" signal YouTube's reused-content review
penalises. The cache is keyed by asset id (not query) so two videos with similar
queries no longer collide on one cached file.

Everything degrades: no key, no network, no match, or a failed download all
return None, and the caller falls back to a plain card. Footage is a nice-to-have
and must never be able to fail a run — the ledger included: a missing or corrupt
ledger is treated as empty, never as an error.
"""
import json, os
from datetime import date

import requests

from common import ROOT, ok

CACHE = ROOT / "assets" / "cache"
LEDGER = ROOT / "assets" / "used_clips.json"
API_VIDEO = "https://api.pexels.com/videos/search"
API_PHOTO = "https://api.pexels.com/v1/search"
TIMEOUT = 45


def _key():
    return os.environ.get("PEXELS_API_KEY") or None


def _load_ledger():
    """Asset-id -> {query, first_used}. Corrupt or missing reads as empty so a
    bad ledger can never fail a run."""
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(ledger, asset_id, query):
    """Record a freshly used asset and persist. Best-effort — a write failure is
    swallowed, at worst a clip may be reusable on a later run."""
    ledger[str(asset_id)] = {"query": query, "first_used": date.today().isoformat()}
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    except OSError:
        pass


def _pick_fresh(items, ledger):
    """First asset whose id the channel has never used; if every candidate is
    used, the least-recently-used one, so a run never fails for lack of a novel
    clip. Returns (item, asset_id) or (None, None) for an empty list."""
    if not items:
        return None, None
    fresh = [it for it in items if str(it.get("id")) not in ledger]
    if fresh:
        return fresh[0], fresh[0].get("id")
    oldest = min(items, key=lambda it: ledger.get(str(it.get("id")), {}).get("first_used", ""))
    return oldest, oldest.get("id")


def _download(url, path):
    try:
        with requests.get(url, stream=True, timeout=TIMEOUT) as r:
            if r.status_code != 200:
                return None
            tmp = path.with_suffix(path.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(path)
        return path
    except requests.RequestException:
        return None


def fetch_video(query, min_seconds=4):
    """Best landscape clip for the query that this channel has NOT used before,
    at or just under 1920 wide. Cached by Pexels asset id. Returns a Path, or
    None if anything at all goes wrong."""
    key = _key()
    if not key or not query:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)

    try:
        r = requests.get(API_VIDEO, headers={"Authorization": key},
                         params={"query": query, "per_page": 12,
                                 "orientation": "landscape", "size": "medium"},
                         timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        videos = r.json().get("videos") or []
    except (requests.RequestException, ValueError):
        return None

    # Only clips long enough and available at <=1920 wide are real candidates —
    # filter first, then let the ledger pick one this channel hasn't used.
    usable = [v for v in videos
              if float(v.get("duration") or 0) >= min_seconds
              and any((f.get("width") or 0) <= 1920 for f in v.get("video_files", []))]
    ledger = _load_ledger()
    while usable:
        v, asset_id = _pick_fresh(usable, ledger)
        if v is None:
            break
        dest = CACHE / f"v-{asset_id}.mp4"
        if dest.exists() and dest.stat().st_size > 20000:
            _remember(ledger, asset_id, query)
            return dest
        files = [f for f in v.get("video_files", []) if (f.get("width") or 0) <= 1920]
        best = max(files, key=lambda f: f.get("width") or 0)
        if _download(best["link"], dest):
            _remember(ledger, asset_id, query)
            return dest
        usable.remove(v)  # download failed — try the next candidate
    return None


def fetch_photo(query):
    key = _key()
    if not key or not query:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(API_PHOTO, headers={"Authorization": key},
                         params={"query": query, "per_page": 12, "orientation": "landscape"},
                         timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        photos = r.json().get("photos") or []
    except (requests.RequestException, ValueError):
        return None

    ledger = _load_ledger()
    while photos:
        p, asset_id = _pick_fresh(photos, ledger)
        if p is None:
            break
        dest = CACHE / f"p-{asset_id}.jpg"
        if dest.exists() and dest.stat().st_size > 10000:
            _remember(ledger, asset_id, query)
            return dest
        link = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
        if link and _download(link, dest):
            _remember(ledger, asset_id, query)
            return dest
        photos.remove(p)  # bad link or failed download — try the next
    return None


def backdrop(query):
    """Video if possible, else a photo, else None."""
    return fetch_video(query) or fetch_photo(query)


def warm_cache(shots):
    """Pre-fetch every shot's footage up front so the render loop doesn't stall
    on network calls, and so one slow query doesn't look like a hang."""
    hits = 0
    for i, s in enumerate(shots):
        q = s.get("source_asset_query")
        if not q:
            continue
        path = backdrop(q)
        s["_backdrop"] = str(path) if path else None
        if path:
            hits += 1
        print(f"  [{i+1}/{len(shots)}] {'ok ' if path else 'miss'}  {q[:56]}")
    ok(f"footage: {hits}/{len(shots)} shots have a backdrop")
    return hits
