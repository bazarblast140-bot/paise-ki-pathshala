"""Classify ElevenLabs HTTP errors and record which TTS engine a run used.

Quota, credit, and payment failures are recoverable: the Short switches to
free Edge TTS for the whole run. An invalid key or a missing permission is
not. ElevenLabs has returned HTTP 401 whose body is a payment error
(type=payment_required, code=insufficient_credits, status=quota_exceeded),
so the body is classified before the status code.

The configured voice stays ElevenLabs. This module does not choose a voice.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Codes/types/statuses that mean "paid TTS cannot run because of credits".
QUOTA_IDS = frozenset({
    "quota_exceeded",
    "insufficient_credits",
    "payment_required",
    "max_character_limit_exceeded",
})
AUTH_IDS = frozenset({
    "authentication_error",
    "invalid_api_key",
    "missing_api_key",
    "unauthorized",
})
PERM_IDS = frozenset({
    "authorization_error",
    "insufficient_permissions",
    "forbidden",
    "voice_access_denied",
    "model_access_denied",
    "feature_not_available",
    "subscription_required",
})
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_QUOTA_PHRASES = (
    "quota_exceeded",
    "insufficient_credits",
    "insufficient credits",
    "payment_required",
    "payment required",
    "max_character_limit_exceeded",
    "exceeded your quota",
    "exceeds your quota",
    "quota exceeded",
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")


class QuotaFallback(Exception):
    """Paid TTS cannot continue because of credits, quota, or payment."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = str(reason)


def parse_body(text):
    """JSON object from a response body, or a one-field wrapper around plain text."""
    if isinstance(text, dict):
        return text
    if text is None:
        return {}
    raw = str(text).strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"detail": raw}
    if isinstance(data, dict):
        return data
    return {"detail": data}


def parse_response(response):
    """Body of a requests-like response. Never raises."""
    if isinstance(response, dict):
        return response
    data = None
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        return data
    if data is not None:
        return {"detail": data}
    return parse_body(getattr(response, "text", "") or "")


def _nodes(body):
    if not isinstance(body, dict):
        body = parse_body(body)
    detail = body.get("detail", body) if isinstance(body, dict) else body
    nodes = []
    items = detail if isinstance(detail, list) else [detail]
    for item in items:
        if isinstance(item, dict):
            nodes.append(item)
        elif item is not None and str(item).strip():
            nodes.append({"message": str(item)})
    return nodes


def _signals(body):
    types, codes, statuses, messages = set(), set(), set(), []
    for node in _nodes(body):
        for bucket, key in ((types, "type"), (codes, "code"), (statuses, "status")):
            value = node.get(key)
            if value:
                bucket.add(str(value).strip().lower())
        for key in ("message", "msg"):
            value = node.get(key)
            if value:
                messages.append(str(value))
    return types, codes, statuses, " ".join(messages).lower()


def classify(status, body):
    """Return quota, auth, permission, retry, or fatal.

    Quota wins over HTTP 401/429 when the body says payment or credits.
    An explicit auth code (invalid_api_key) stays auth even if the prose
    mentions credits. Bare 401 is auth. Bare 429 is a retry, not a fallback.
    """
    try:
        status = int(status or 0)
    except (TypeError, ValueError):
        status = 0
    types, codes, statuses, message = _signals(body)
    ids = types | codes | statuses
    has_auth_id = bool(ids & AUTH_IDS)
    has_quota_id = bool(ids & QUOTA_IDS) or status == 402
    quota_phrase = any(phrase in message for phrase in _QUOTA_PHRASES)
    if (has_quota_id or quota_phrase) and not has_auth_id:
        return "quota"
    if has_auth_id or status == 401 or "invalid api key" in message or "invalid_api_key" in message:
        return "auth"
    if ids & PERM_IDS or status == 403:
        return "permission"
    if status in RETRY_STATUSES:
        return "retry"
    return "fatal"


def describe(status, body):
    """Short, secret-free reason suitable for a log line and the ledger."""
    try:
        status = int(status or 0)
    except (TypeError, ValueError):
        status = 0
    types, codes, statuses, message = _signals(body)
    parts = [f"HTTP {status}"]
    if types:
        parts.append("type=" + ",".join(sorted(types)))
    if codes:
        parts.append("code=" + ",".join(sorted(codes)))
    if statuses:
        parts.append("status=" + ",".join(sorted(statuses)))
    public = _TOKEN_RE.sub("***", " ".join(message.split()))
    if public:
        parts.append(public[:180])
    return "; ".join(parts)


def subscription_remaining(body):
    """character_limit - character_count, or None when the fields are absent."""
    if not isinstance(body, dict):
        return None
    try:
        return int(body["character_limit"]) - int(body["character_count"])
    except (KeyError, TypeError, ValueError):
        return None


def log_fallback(reason):
    """Plain warning plus a GitHub Actions annotation. Not silent.

    SUPPRESS_GHA_COMMANDS=1 skips only the workflow annotation so the unit
    suite does not mark the job. The [warn] line is always printed.
    """
    reason = " ".join(str(reason).split())
    line = (
        "ElevenLabs quota, credit, or payment error "
        f"({reason}). This run uses free Edge TTS."
    )
    print(f"[warn] {line}", flush=True)
    if os.environ.get("SUPPRESS_GHA_COMMANDS") == "1":
        return
    safe = line.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning title=ElevenLabs quota::{safe}", flush=True)


def write_voice_meta(run_dir, engine, fallback_reason="", elevenlabs_attempted=False):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine": engine,
        "fallback_reason": fallback_reason or "",
        "elevenlabs_attempted": bool(elevenlabs_attempted),
    }
    (run_dir / "voice_meta.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "voice_engine.txt").write_text(str(engine) + "\n", encoding="utf-8")
    return payload


def load_voice_record(run_dir):
    run_dir = Path(run_dir)
    engine, reason, attempted = "", "", False
    try:
        data = json.loads((run_dir / "voice_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        engine = str(data.get("engine") or "")
        reason = str(data.get("fallback_reason") or "")
        attempted = bool(data.get("elevenlabs_attempted"))
    if not engine:
        try:
            engine = (run_dir / "voice_engine.txt").read_text(encoding="utf-8").strip()
        except OSError:
            engine = ""
    return {
        "engine": engine,
        "fallback_reason": reason,
        "elevenlabs_attempted": attempted,
    }


def ledger_voice_kwargs(run_dir):
    rec = load_voice_record(run_dir)
    return {
        "voice_engine": rec["engine"] or None,
        "voice_fallback": rec["fallback_reason"] or None,
    }


# Samples, dry runs, and buffer Shorts stay on this free voice.
# A real publish keeps the configured Edge fallback. For Shorts that
# configured voice is the same Madhur +0% sample.
PREVIEW_EDGE_VOICE = "hi-IN-MadhurNeural"
PREVIEW_EDGE_RATE = "+0%"
# A live slot stops when fewer than this many characters remain.
# It does not switch to Edge. Buffer and preview runs never reach this check.
LIVE_CREDIT_FLOOR = 1000
KAMAL_VOICE_ID = "qsz5tTEjPvsiIJZIpM8S"
RUDRA_VOICE_ID = "ypnkIsDASPgHZuanrF0q"

_TRUTHY = {"1", "true", "yes", "on"}
_PREVIEW_MODES = {"preview", "sample", "dry-run", "dry_run", "test", "qa", "retry", "repair"}


def configured_voice_id(c):
    """A set config voice_id always wins. The secret is only a fallback."""
    vid = str(((c or {}).get("voice") or {}).get("voice_id") or "").strip()
    if vid and not vid.upper().startswith("TODO"):
        return vid
    return (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()


def ensure_library_voice(c):
    """Make the configured library voice available. This never calls TTS.

    GET /v1/voices/{id}. On 404, and only when shared_voice_search is set,
    find that id in GET /v1/shared-voices and POST /v1/voices/add/{owner}/{id}.
    Any failure uses the Kamal fallback. A live slot still does not use Edge.
    """
    voice = c.setdefault("voice", {})
    vid = configured_voice_id(c)
    voice["voice_id"] = vid
    search = str(voice.get("shared_voice_search") or "").strip()
    if not search or not vid:
        return vid
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        return vid
    import requests
    headers = {"xi-api-key": key, "Accept": "application/json"}
    try:
        got = requests.get(
            f"https://api.elevenlabs.io/v1/voices/{vid}",
            headers=headers, timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 — the probe is not a reason to spend TTS
        print(f"[warn] voice lookup failed ({type(exc).__name__}); keeping {vid}")
        return vid
    if getattr(got, "status_code", 0) == 200:
        print(f"[voice] {voice.get('voice_name') or vid} is on the account ({vid})")
        return vid
    if getattr(got, "status_code", 0) != 404:
        print(f"[note] voice lookup HTTP {getattr(got, 'status_code', 0)}; TTS remains the auth check")
        return vid
    new_name = str(voice.get("library_new_name") or "Rudra PKP")
    try:
        found = requests.get(
            "https://api.elevenlabs.io/v1/shared-voices",
            headers=headers,
            params={"search": search, "page_size": 30},
            timeout=20,
        )
        body = found.json() if getattr(found, "status_code", 0) == 200 else {}
        rows = body.get("voices") if isinstance(body, dict) else None
        owner = ""
        for row in rows or []:
            if str((row or {}).get("voice_id") or "") == vid:
                owner = str(row.get("public_owner_id") or "").strip()
                break
        if not owner:
            raise RuntimeError(f"{vid} was not in the shared-voice search for {search}")
        added = requests.post(
            f"https://api.elevenlabs.io/v1/voices/add/{owner}/{vid}",
            headers={**headers, "Content-Type": "application/json"},
            json={"new_name": new_name},
            timeout=20,
        )
        payload = added.json() if getattr(added, "status_code", 0) in (200, 201) else {}
        got_id = str((payload or {}).get("voice_id") or "").strip()
        if not got_id:
            raise RuntimeError(f"add voice HTTP {getattr(added, 'status_code', 0)}")
        voice["voice_id"] = got_id
        print(f"[voice] added {new_name} as {got_id}")
        return got_id
    except Exception as exc:  # noqa: BLE001 — Kamal is already on the account
        fallback = str(voice.get("fallback_voice_id") or KAMAL_VOICE_ID).strip() or KAMAL_VOICE_ID
        print(f"[warn] library voice {vid} unavailable ({exc}); using Kamal {fallback}")
        voice["voice_id"] = fallback
        voice["voice_name"] = str(voice.get("fallback_voice_name") or "Kamal")
        if voice.get("fallback_words_per_second") not in (None, ""):
            voice["words_per_second"] = voice.get("fallback_words_per_second")
        return fallback


def paid_voice_allowed():
    """ElevenLabs only on a real public publish, or an explicit sample opt-in.

    Unlisted renders, previews, tests, dry runs, and repair retries use Edge.
    ELEVENLABS_OPT_IN=1 is the only way a sample spends voice credits.
    """
    if os.environ.get("ELEVENLABS_OPT_IN", "").strip().lower() in _TRUTHY:
        return True
    mode = os.environ.get("PIPELINE_MODE", "").strip().lower()
    if mode in _PREVIEW_MODES:
        return False
    for key in ("DRY_RUN", "RENDER_SAMPLE", "SAMPLE_NO_UPLOAD"):
        if os.environ.get(key, "").strip().lower() in _TRUTHY:
            return False
    vis = os.environ.get("PUBLISH_VISIBILITY", "").strip().lower()
    return vis in ("public", "now")
