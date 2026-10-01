"""Read-only pre-slot health check. Never uploads, publishes, or posts media.

Runs ~90 minutes before each YouTube Shorts slot (08:30, 16:00, 21:00 IST)
and, on any warn/fail, opens or updates one GitHub issue labelled `health-alert`.
When every check is ok, skip, or note, that issue is closed.

What it calls (one request each, no retries):
  * Google OAuth token endpoint — refresh only. The access token is discarded.
  * DeepSeek GET /user/balance
  * Kimi/Moonshot GET /v1/users/me/balance, only if KIMI_API_KEY is set
  * Anthropic GET /v1/models, only if ANTHROPIC_API_KEY is set
    (standard keys have no balance endpoint; a number is never invented)
  * ElevenLabs GET /v1/user/subscription
  * Instagram debug_token, or a one-field read if debug_token is rejected
  * YouTube quota: not called. Remaining daily quota is not in the refresh
    response and this repo has no Cloud Monitoring credential.

Secret values are redacted from stdout and from the issue body.
"""
from __future__ import annotations

import json
import os
import re
import sys

import eleven_voice
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))

# Nominal lead is 90 minutes (the early edge of the 60–90 window). Actions cron
# slips later, not earlier, so a late runner still lands inside that window.
# cron is UTC. IST = UTC+5:30.
SCHEDULE = (
    # cron, check_ist, slot_ist, lead_min
    ("30 1 * * *", "07:00", "08:30", 90),
    ("0 9 * * *", "14:30", "16:00", 90),
    ("0 14 * * *", "19:30", "21:00", 90),
)
SLOT_NAMES = tuple(row[2] for row in SCHEDULE)

LABEL = "health-alert"
LABEL_COLOR = "b60205"
LABEL_DESC = "Pre-slot health check found a token, credit, or expiry problem."

# One Short narration is about 1–2k characters. Warn when the balance cannot
# cover a full day of 3 Shorts. A quota or payment error falls back to Edge
# TTS, so low credits warn without blocking the slot. An invalid key still fails.
CHARS_PER_SHORT = 2000
SHORTS_PER_DAY = 3
ELEVEN_WARN_CHARS = CHARS_PER_SHORT * SHORTS_PER_DAY
# DeepSeek/Kimi report USD or CNY. Below 1 unit the next scripts are at risk.
BALANCE_WARN = 1.0
IG_WARN_DAYS = 14

GITHUB_API = "https://api.github.com"
SECRET_ENV_VARS = (
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "IG_ACCESS_TOKEN",
    "GITHUB_TOKEN",
)
# Env var -> GitHub secret name, when they differ. Used only as names in text.
SECRET_GITHUB_NAME = {
    "YOUTUBE_CLIENT_ID": "YT_FIN_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET": "YT_FIN_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN": "YT_FIN_REFRESH_TOKEN",
}

_LEAK_RES = (
    re.compile(r"(?i)(access_token|refresh_token|client_secret|api_key|client_id)=([^&\s\"']+)"),
    re.compile(r"(?i)(Bearer\s+)\S+"),
    re.compile(r"(?i)(xi-api-key[\"']?\s*[:=]\s*[\"']?)\S+"),
    re.compile(r"ya29\.[0-9A-Za-z_\-]+"),
)


@dataclass
class Check:
    name: str
    status: str  # ok, warn, fail, skip, note
    detail: str
    summary_hi: str = ""
    blocks: str = ""  # "youtube", "instagram", or ""
    missing_secrets: tuple = field(default_factory=tuple)


@dataclass
class HttpResponse:
    status: int
    body: object = None
    text: str = ""

    def __post_init__(self):
        if not self.text and isinstance(self.body, (dict, list)):
            self.text = json.dumps(self.body)


@dataclass
class SyncResult:
    action: str  # opened, updated, closed, none, error, skipped
    number: int | None = None
    error: str = ""


class RequestsHttp:
    """Real transport. Redirects are off so a token in a query string cannot hop hosts."""

    def send(self, method, url, *, headers=None, form=None, json_body=None, params=None, timeout=20):
        import requests

        try:
            r = requests.request(
                method,
                url,
                headers=headers,
                data=form,
                json=json_body,
                params=params,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            return HttpResponse(0, None, f"network error: {type(e).__name__}")
        body = None
        try:
            body = r.json()
        except ValueError:
            body = None
        return HttpResponse(r.status_code, body, (r.text or "")[:2000])


def _env(env, name):
    return (env.get(name) or "").strip()


def secrets_from_env(env):
    vals = []
    for name in SECRET_ENV_VARS:
        value = _env(env, name)
        if len(value) >= 8:
            vals.append(value)
    return vals


def redact(text, secrets):
    if not text:
        return ""
    out = str(text)
    for secret in secrets or ():
        if secret and len(secret) >= 8:
            out = out.replace(secret, "***")
    for pattern in _LEAK_RES:
        out = pattern.sub(lambda m: (m.group(1) + "***") if m.lastindex else "***", out)
    return out


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell(text):
    return (text or "").replace("|", "/").replace("\n", " ").strip()


def github_secret_name(env_name):
    return SECRET_GITHUB_NAME.get(env_name, env_name)


def _error_message(resp):
    body = resp.body if isinstance(resp.body, dict) else {}
    if body.get("error_description"):
        err = body.get("error") or ""
        return f"{err}: {body.get('error_description')}".strip(": ")
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or err)
    if isinstance(err, str) and err:
        return err
    detail = body.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or detail)
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(body.get("message"), str):
        return body["message"]
    return (resp.text or f"HTTP {resp.status}")[:300]


def _check(name, status, detail, secrets, summary_hi="", blocks="", missing=()):
    return Check(
        name=name,
        status=status,
        detail=redact(detail, secrets),
        summary_hi=redact(summary_hi, secrets),
        blocks=blocks if status == "fail" else "",
        missing_secrets=tuple(missing),
    )


def _network_fail(name, secrets, blocks):
    return _check(
        name,
        "fail",
        "could not reach the API (network). This does not by itself prove the credential is dead.",
        secrets,
        summary_hi=(
            f"{name} tak network se request nahi pahunchi. Credential dead confirm nahi — "
            "lekin is slot se pehle verify nahi ho paya."
        ),
        blocks=blocks,
    )


def read_provider(path=None):
    file = path or (ROOT / "config.json")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "deepseek"
    return ((data.get("models") or {}).get("llm_provider") or "deepseek").strip().lower()


def read_voice_engine(path=None):
    file = path or (ROOT / "config.json")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "elevenlabs"
    return ((data.get("voice") or {}).get("engine") or "elevenlabs").strip().lower()


def schedule_row(slot):
    for row in SCHEDULE:
        if row[2] == slot:
            return row
    return SCHEDULE[0]


def resolve_slot(env, now):
    """Return (slot_ist, cron). Explicit slot wins, then the cron that fired, then the clock."""
    chosen = _env(env, "HEALTH_SLOT")
    cron = _env(env, "HEALTH_CRON")
    if chosen in SLOT_NAMES:
        return chosen, schedule_row(chosen)[0]
    for row in SCHEDULE:
        if cron == row[0]:
            return row[2], row[0]
    hhmm = now.strftime("%H:%M")
    for row in SCHEDULE:
        if hhmm <= row[2]:
            return row[2], row[0]
    return SCHEDULE[0][2], SCHEDULE[0][0]


def _root_url(base, default):
    b = (base or default).strip().rstrip("/")
    if b.endswith("/v1"):
        b = b[:-3]
    return b


# ---------------------------------------------------------------- checks
def check_youtube(http, env, secrets):
    name = "YouTube token"
    missing = [v for v in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN") if not _env(env, v)]
    if missing:
        names = ", ".join(github_secret_name(v) for v in missing)
        return _check(
            name, "fail",
            f"missing GitHub secret(s): {names}",
            secrets,
            summary_hi=(
                f"YouTube secrets missing hain ({names}). Bina inke upload nahi hoga. "
                "Naya token: `python youtube_auth.py` (README → YouTube token permanent kaise karein)."
            ),
            blocks="youtube",
            missing=tuple(github_secret_name(v) for v in missing),
        )
    resp = http.send(
        "POST",
        "https://oauth2.googleapis.com/token",
        form={
            "grant_type": "refresh_token",
            "client_id": _env(env, "YOUTUBE_CLIENT_ID"),
            "client_secret": _env(env, "YOUTUBE_CLIENT_SECRET"),
            "refresh_token": _env(env, "YOUTUBE_REFRESH_TOKEN"),
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    if resp.status == 0:
        return _network_fail(name, secrets, "youtube")
    body = resp.body if isinstance(resp.body, dict) else {}
    err = str(body.get("error") or "")
    if resp.status == 200 and body.get("access_token"):
        expires = body.get("expires_in")
        extra = f", expires_in={expires}s" if expires else ""
        return _check(
            name, "ok",
            f"refresh ok{extra}. Access token discarded. No YouTube Data API call.",
            secrets,
        )
    message = redact(_error_message(resp), secrets)
    if err == "invalid_grant" or "invalid_grant" in message:
        return _check(
            name, "fail",
            f"invalid_grant — {message}",
            secrets,
            summary_hi=(
                "YouTube refresh token Google ne reject kar diya (invalid_grant). "
                "Is slot ka upload nahi hoga. Naya token laptop pe `python youtube_auth.py` se banao "
                "aur GitHub secret `YT_FIN_REFRESH_TOKEN` update karo. OAuth consent screen "
                "'In production' hona chahiye — Testing me token 7 din me mar jaata hai."
            ),
            blocks="youtube",
        )
    if err == "invalid_client" or "invalid_client" in message:
        return _check(
            name, "fail",
            f"invalid_client — {message}",
            secrets,
            summary_hi=(
                "YouTube OAuth client match nahi kar raha (invalid_client). "
                "`YT_FIN_CLIENT_ID` / `YT_FIN_CLIENT_SECRET` usi app ke hone chahiye jisne refresh token banaya."
            ),
            blocks="youtube",
        )
    return _check(
        name, "fail",
        f"HTTP {resp.status} — {message}",
        secrets,
        summary_hi=f"YouTube token refresh fail (HTTP {resp.status}). Upload is slot pe nahi hoga.",
        blocks="youtube",
    )


def _balance_url_deepseek(env):
    return _root_url(_env(env, "DEEPSEEK_BASE_URL"), "https://api.deepseek.com") + "/user/balance"


def _balance_url_kimi(env):
    base = (_env(env, "KIMI_BASE_URL") or "https://api.moonshot.ai/v1").rstrip("/")
    if base.endswith("/v1"):
        return base + "/users/me/balance"
    return base + "/v1/users/me/balance"


def _fmt_balances(pairs):
    return ", ".join(f"{cur} {tot:g}" for cur, tot in pairs) or "no balance rows"


def check_deepseek(http, env, secrets, selected=True):
    return _check_balance_provider(
        http, env, secrets,
        name="DeepSeek",
        env_var="DEEPSEEK_API_KEY",
        url=_balance_url_deepseek(env),
        selected=selected,
        kind="deepseek",
    )


def check_kimi(http, env, secrets, selected=False):
    if not _env(env, "KIMI_API_KEY"):
        return _check("Kimi", "skip", "KIMI_API_KEY not set — backup not configured, skipped.", secrets)
    return _check_balance_provider(
        http, env, secrets,
        name="Kimi",
        env_var="KIMI_API_KEY",
        url=_balance_url_kimi(env),
        selected=selected,
        kind="kimi",
    )


def _check_balance_provider(http, env, secrets, *, name, env_var, url, selected, kind):
    key = _env(env, env_var)
    if not key:
        if selected:
            gname = github_secret_name(env_var)
            return _check(
                name, "fail",
                f"missing GitHub secret {gname} (config selects this provider)",
                secrets,
                summary_hi=(
                    f"{name} is channel ka LLM hai, lekin `{gname}` set nahi hai. "
                    "Script nahi banegi, slot miss ho jayega."
                ),
                blocks="youtube",
                missing=(gname,),
            )
        return _check(name, "skip", f"{env_var} not set — skipped.", secrets)

    resp = http.send(
        "GET", url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=20,
    )
    if resp.status == 0:
        return _network_fail(name, secrets, "youtube" if selected else "")
    message = redact(_error_message(resp), secrets)
    low = message.lower()
    blocks = "youtube" if selected else ""
    role = "primary LLM" if selected else "backup LLM"

    if "suspend" in low:
        return _check(
            name, "fail", f"account suspended — {message}", secrets,
            summary_hi=f"{name} account suspended hai ({role}). Script is provider se nahi banegi.",
            blocks=blocks,
        )
    if resp.status in (401, 403):
        return _check(
            name, "fail", f"HTTP {resp.status} — {message}", secrets,
            summary_hi=(
                f"{name} API key reject ho gayi (HTTP {resp.status}). "
                f"Secret `{github_secret_name(env_var)}` check karo — value issue me paste mat karna."
            ),
            blocks=blocks,
        )
    if resp.status == 402 or "credit" in low and "low" in low:
        return _check(
            name, "fail", f"no credits — {message}", secrets,
            summary_hi=f"{name} credits khatam hain ({role}). Script empty ya fail ho sakti hai.",
            blocks=blocks,
        )
    if resp.status >= 500 or resp.status == 429:
        return _check(
            name, "warn",
            f"HTTP {resp.status} — check inconclusive. {message}",
            secrets,
            summary_hi=f"{name} ka balance endpoint ne HTTP {resp.status} diya. Abhi confirm nahi ho paya.",
        )
    if resp.status != 200 or not isinstance(resp.body, dict):
        return _check(
            name, "fail", f"HTTP {resp.status} — {message}", secrets,
            summary_hi=f"{name} balance check fail (HTTP {resp.status}).",
            blocks=blocks,
        )

    if kind == "deepseek":
        return _judge_deepseek(name, resp.body, secrets, blocks, role)
    return _judge_kimi(name, resp.body, secrets, blocks, role)


def _judge_deepseek(name, body, secrets, blocks, role):
    infos = body.get("balance_infos") or []
    pairs = []
    for info in infos:
        if not isinstance(info, dict):
            continue
        tot = _num(info.get("total_balance"))
        if tot is None:
            continue
        pairs.append((str(info.get("currency") or "?"), tot))
    available = bool(body.get("is_available"))
    pretty = _fmt_balances(pairs)
    if not pairs:
        return _check(
            name, "warn",
            "balance endpoint returned 200 but no total_balance rows — balance not measurable, not guessing.",
            secrets,
            summary_hi=f"{name} ne balance number nahi diya. Credits confirm nahi hue.",
        )
    if not available or all(tot <= 0 for _, tot in pairs):
        return _check(
            name, "fail",
            f"is_available={available}, balance {pretty}",
            secrets,
            summary_hi=(
                f"DeepSeek balance khali hai ({pretty}, is_available={available}). "
                "Pehle jab credits khatam hue the, script empty aayi thi aur slot miss ho gaya. "
                "platform.deepseek.com pe credits daalo."
            ),
            blocks=blocks,
        )
    if all(tot < BALANCE_WARN for _, tot in pairs):
        return _check(
            name, "warn",
            f"is_available=true, balance {pretty} (warn below {BALANCE_WARN:g})",
            secrets,
            summary_hi=(
                f"DeepSeek balance bahut kam hai ({pretty}). "
                f"{BALANCE_WARN:g} unit se neeche warning hai — agle slot se pehle top-up kar do."
            ),
        )
    return _check(name, "ok", f"is_available=true, balance {pretty} ({role})", secrets)


def _judge_kimi(name, body, secrets, blocks, role):
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if body.get("status") is False:
        return _check(
            name, "fail", "balance endpoint status=false", secrets,
            summary_hi="Kimi balance endpoint ne status=false diya. Account ya key theek nahi hai.",
            blocks=blocks,
        )
    bal = _num(data.get("available_balance") if isinstance(data, dict) else None)
    if bal is None:
        return _check(
            name, "warn",
            "balance endpoint returned 200 but available_balance was absent — not measurable, not guessing.",
            secrets,
            summary_hi="Kimi ne balance number nahi diya. Credits confirm nahi hue.",
        )
    if bal <= 0:
        return _check(
            name, "fail", f"available_balance={bal:g} ({role})", secrets,
            summary_hi="Kimi balance khali hai. Backup LLM se script nahi banegi.",
            blocks=blocks,
        )
    if bal < BALANCE_WARN:
        return _check(
            name, "warn",
            f"available_balance={bal:g} (warn below {BALANCE_WARN:g})",
            secrets,
            summary_hi=f"Kimi balance bahut kam hai ({bal:g}). Top-up kar do warna backup LLM nahi chalegi.",
        )
    return _check(name, "ok", f"available_balance={bal:g} ({role})", secrets)


def check_anthropic(http, env, secrets, selected=False):
    name = "Anthropic"
    key = _env(env, "ANTHROPIC_API_KEY")
    if not key:
        if selected:
            return _check(
                name, "fail",
                "missing GitHub secret ANTHROPIC_API_KEY (config selects this provider)",
                secrets,
                summary_hi="Anthropic selected provider hai lekin `ANTHROPIC_API_KEY` missing hai. Script nahi banegi.",
                blocks="youtube",
                missing=("ANTHROPIC_API_KEY",),
            )
        return _check(name, "skip", "ANTHROPIC_API_KEY not set — backup not configured, skipped.", secrets)

    resp = http.send(
        "GET",
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        },
        timeout=20,
    )
    if resp.status == 0:
        return _network_fail(name, secrets, "youtube" if selected else "")
    message = redact(_error_message(resp), secrets)
    blocks = "youtube" if selected else ""
    low = message.lower()
    if "credit balance" in low or ("credit" in low and "low" in low):
        return _check(
            name, "fail",
            f"credit balance too low — {message}",
            secrets,
            summary_hi=(
                "Anthropic ne kaha credit balance too low hai. "
                "Console → Plans & Billing pe credits daalo. Billed messages call yahan nahi kiya."
            ),
            blocks=blocks,
        )
    if resp.status in (401, 403):
        return _check(
            name, "fail", f"HTTP {resp.status} — {message}", secrets,
            summary_hi="Anthropic API key invalid hai. Secret `ANTHROPIC_API_KEY` check karo — value paste mat karna.",
            blocks=blocks,
        )
    if resp.status >= 500 or resp.status == 429:
        return _check(
            name, "warn",
            f"HTTP {resp.status} — key check inconclusive. {message}",
            secrets,
            summary_hi=f"Anthropic models endpoint ne HTTP {resp.status} diya. Key confirm nahi ho payi.",
        )
    if resp.status != 200:
        return _check(
            name, "fail", f"HTTP {resp.status} — {message}", secrets,
            summary_hi=f"Anthropic key check fail (HTTP {resp.status}).",
            blocks=blocks,
        )
    return _check(
        name, "ok",
        "key accepted by GET /v1/models. Credit balance is not measurable on a standard API key "
        "(Anthropic has no balance endpoint; a billed messages call was not made). Not guessing a number.",
        secrets,
    )


def check_elevenlabs(http, env, secrets, engine="elevenlabs"):
    name = "ElevenLabs"
    key = _env(env, "ELEVENLABS_API_KEY")
    engine = (engine or "elevenlabs").lower()
    if not key:
        if engine == "elevenlabs":
            return _check(
                name, "warn",
                "ELEVENLABS_API_KEY is not set. voice.engine is elevenlabs, so this run would fall back to Edge TTS. "
                "A video can still publish; paid voice will not be used.",
                secrets,
                summary_hi=(
                    "`ELEVENLABS_API_KEY` set nahi hai. Pipeline Edge TTS pe chali jayegi — "
                    "video ban sakta hai, paid voice nahi."
                ),
                missing=("ELEVENLABS_API_KEY",),
            )
        return _check(name, "skip", "ELEVENLABS_API_KEY not set and voice.engine is not elevenlabs — skipped.", secrets)

    resp = http.send(
        "GET",
        "https://api.elevenlabs.io/v1/user/subscription",
        headers={"xi-api-key": key, "Accept": "application/json"},
        timeout=20,
    )
    in_use = engine == "elevenlabs"
    blocks = "youtube" if in_use else ""
    if resp.status == 0:
        return _network_fail(name, secrets, blocks)
    message = redact(_error_message(resp), secrets)
    parsed = resp.body if isinstance(resp.body, dict) else eleven_voice.parse_body(resp.text or "")
    if resp.status == 200 and isinstance(resp.body, dict):
        return _elevenlabs_subscription(name, resp.body, secrets)

    kind = eleven_voice.classify(resp.status, parsed)
    if kind == "quota":
        return _check(
            name, "warn",
            f"HTTP {resp.status} — {message}. Credits or payment block paid TTS; "
            "this run falls back to free Edge TTS.",
            secrets,
            summary_hi=(
                "ElevenLabs ne credit ya payment error diya. "
                "Is run me free Edge TTS chalega; slot credit ki wajah se nahi ruke ga."
            ),
        )
    if kind == "auth":
        summary = (
            "ElevenLabs key invalid hai. Galat key pe Edge pe nahi jaate, "
            "isliye slot miss ho sakta hai. Secret `ELEVENLABS_API_KEY` update karo."
            if in_use else
            "ElevenLabs key reject ho gayi. voice.engine abhi elevenlabs nahi hai, lekin secret galat hai."
        )
        return _check(
            name, "fail", f"HTTP {resp.status} — {message}", secrets,
            summary_hi=summary, blocks=blocks,
        )
    if kind == "permission":
        return _check(
            name, "fail", f"HTTP {resp.status} — {message}", secrets,
            summary_hi=(
                "ElevenLabs key ko permission nahi hai. Ye misconfiguration hai — "
                "Edge pe chupke nahi jaate. Secret ya voice access theek karo."
            ),
            blocks=blocks,
        )
    if kind == "retry" or resp.status >= 500 or resp.status == 429:
        return _check(
            name, "warn", f"HTTP {resp.status} — subscription check inconclusive. {message}", secrets,
            summary_hi=f"ElevenLabs subscription endpoint ne HTTP {resp.status} diya. Credits confirm nahi hue.",
        )
    return _check(
        name, "fail", f"HTTP {resp.status} — {message}", secrets,
        summary_hi=f"ElevenLabs subscription check fail (HTTP {resp.status}).",
        blocks=blocks,
    )


def _elevenlabs_subscription(name, body, secrets):
    """Judge a 200 subscription payload. Low credits and a bad payment status warn."""
    limit = _num(body.get("character_limit"))
    used = _num(body.get("character_count"))
    status = str(body.get("status") or "")
    tier = str(body.get("tier") or "")
    if limit is None or used is None:
        return _check(
            name, "warn",
            "subscription endpoint returned 200 but character_limit/character_count missing — "
            "remaining credit not measurable, not guessing 0.",
            secrets,
            summary_hi="ElevenLabs ne character credit number nahi diya. Kitna bacha hai, pata nahi.",
        )
    remaining = int(limit - used)
    meta = f"tier={tier or '?'} status={status or '?'}"
    bad_status = status.lower() in {"past_due", "canceled", "cancelled", "incomplete", "unpaid", "payment_failed"}
    reasons = []
    if bad_status:
        reasons.append(f"payment status {status}")
    if remaining <= 0:
        reasons.append("no characters left")
    elif remaining < ELEVEN_WARN_CHARS:
        reasons.append(
            f"remaining {remaining} is below {ELEVEN_WARN_CHARS} "
            f"({SHORTS_PER_DAY} Shorts x {CHARS_PER_SHORT})"
        )
    if reasons:
        return _check(
            name, "warn",
            f"remaining={remaining} (limit={int(limit)} used={int(used)}) {meta} — " + "; ".join(reasons),
            secrets,
            summary_hi=(
                f"ElevenLabs: {remaining} characters, status {status or '?'}. "
                f"Ek din = {SHORTS_PER_DAY} Shorts (~{ELEVEN_WARN_CHARS} characters). "
                "Quota, credit, ya payment error par pipeline free Edge TTS pe chali jayegi."
            ),
        )
    return _check(
        name, "ok",
        f"remaining={remaining} (limit={int(limit)} used={int(used)}) {meta}",
        secrets,
    )


def _ig_host(env):
    surface = (_env(env, "IG_SURFACE") or "facebook").lower()
    if _env(env, "IG_API_HOST"):
        return _env(env, "IG_API_HOST").rstrip("/")
    if surface == "instagram":
        return "https://graph.instagram.com"
    return "https://graph.facebook.com"


def check_instagram(http, env, secrets, now):
    name = "Instagram"
    token = _env(env, "IG_ACCESS_TOKEN")
    if not token:
        return _check(
            name, "fail",
            "missing GitHub secret IG_ACCESS_TOKEN. Reel cross-post will not run. YouTube upload is separate.",
            secrets,
            summary_hi=(
                "`IG_ACCESS_TOKEN` missing hai. Reel cross-post nahi hoga. "
                "YouTube upload is secret se alag hai. Naya long-lived token secret me daalo — value paste mat karna."
            ),
            blocks="instagram",
            missing=("IG_ACCESS_TOKEN",),
        )

    version = _env(env, "IG_API_VERSION") or "v23.0"
    debug = http.send(
        "GET",
        "https://graph.facebook.com/debug_token",
        params={"input_token": token, "access_token": token},
        timeout=20,
    )
    parsed = _parse_debug(debug)
    if parsed is not None:
        return _judge_ig_debug(name, parsed, secrets, now)

    # debug_token often wants an app token we do not have. Fall back to a one-field read.
    user = _env(env, "IG_USER_ID")
    path = user or "me"
    read = http.send(
        "GET",
        f"{_ig_host(env)}/{version}/{path}",
        params={"fields": "id", "access_token": token},
        timeout=20,
    )
    if read.status == 0:
        return _network_fail(name, secrets, "instagram")
    if read.status == 200 and isinstance(read.body, dict) and read.body.get("id"):
        why = redact(_error_message(debug), secrets) if debug.status != 0 else "debug_token unreachable"
        via = f"/{path}"
        return _check(
            name, "ok",
            f"token accepted by GET {via}?fields=id. Expiry not measurable "
            f"(debug_token was not usable: {why}). No date was guessed.",
            secrets,
        )
    message = redact(_error_message(read), secrets)
    return _check(
        name, "fail",
        f"token rejected (debug_token HTTP {debug.status}, read HTTP {read.status}) — {message}",
        secrets,
        summary_hi=(
            "Instagram token invalid hai ya expire ho chuka hai. Reel cross-post nahi hoga. "
            "Naya long-lived token `IG_ACCESS_TOKEN` me daalo. Value issue me paste mat karna."
        ),
        blocks="instagram",
    )


def _parse_debug(resp):
    if resp.status == 0 or not isinstance(resp.body, dict):
        return None
    if resp.body.get("error"):
        return None
    data = resp.body.get("data")
    if not isinstance(data, dict) or "is_valid" not in data:
        return None
    return data


def _judge_ig_debug(name, data, secrets, now):
    if not data.get("is_valid"):
        return _check(
            name, "fail",
            "debug_token is_valid=false",
            secrets,
            summary_hi=(
                "Instagram token invalid hai (debug_token is_valid=false). Reel nahi jayegi. "
                "Naya long-lived token `IG_ACCESS_TOKEN` me daalo."
            ),
            blocks="instagram",
        )
    stamps = []
    for key in ("expires_at", "data_access_expires_at"):
        raw = data.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            stamps.append(int(raw))
    if not stamps:
        return _check(
            name, "ok",
            "debug_token is_valid=true, expires_at=0 (no expiry). Days-to-expiry not applicable.",
            secrets,
        )
    soonest = min(stamps)
    now_ts = int(now.timestamp())
    days = (soonest - now_ts) / 86400
    when = datetime.fromtimestamp(soonest, IST).strftime("%Y-%m-%d")
    if days <= 0:
        return _check(
            name, "fail",
            f"token expired ({when}, {days:.1f} days)",
            secrets,
            summary_hi=(
                f"Instagram token expire ho chuka hai ({when}). Reel nahi jayegi. "
                "Naya long-lived token chahiye (~60 din)."
            ),
            blocks="instagram",
        )
    if days <= IG_WARN_DAYS:
        shown = "under 1 day" if days < 1 else f"{int(days)} day(s)"
        return _check(
            name, "warn",
            f"valid, expires in {days:.2f} days ({when}); warn at {IG_WARN_DAYS} days or fewer",
            secrets,
            summary_hi=(
                f"Instagram token {shown} me expire hoga ({when}). "
                f"{IG_WARN_DAYS} din ya usse kam pe warning hai — abhi naya long-lived token bana lo, "
                "warna Reel ruk jayegi."
            ),
        )
    return _check(name, "ok", f"valid, expires in {days:.1f} days ({when})", secrets)


def check_youtube_quota():
    """Remaining daily quota is not on the OAuth refresh response.

    Service Usage / Cloud Monitoring would need a different credential this repo
    does not have. A Data API probe would spend quota and still would not return
    the remainder. Do not invent a number.
    """
    return Check(
        name="YouTube quota",
        status="note",
        detail=(
            "not measurable. The OAuth refresh response does not include remaining "
            "YouTube Data API quota, and no Cloud Monitoring / Service Usage credential "
            "is configured. No Data API call was made, and no number was guessed."
        ),
    )


def run_checks(http, env, now, provider=None, engine=None):
    secrets = secrets_from_env(env)
    provider = (provider if provider is not None else read_provider()).lower()
    engine = engine if engine is not None else read_voice_engine()
    return [
        check_youtube(http, env, secrets),
        check_deepseek(http, env, secrets, selected=(provider == "deepseek")),
        check_kimi(http, env, secrets, selected=(provider == "kimi")),
        check_anthropic(http, env, secrets, selected=(provider == "anthropic")),
        check_elevenlabs(http, env, secrets, engine=engine),
        check_instagram(http, env, secrets, now),
        check_youtube_quota(),
    ]


def has_problem(checks):
    return any(c.status in ("fail", "warn") for c in checks)


def impact(checks):
    youtube = "AT RISK" if any(c.blocks == "youtube" for c in checks) else "ok"
    if any(c.blocks == "instagram" for c in checks):
        instagram = "AT RISK"
    elif any(c.name == "Instagram" and c.status == "warn" for c in checks):
        instagram = "warning (expiry)"
    else:
        instagram = "ok"
    return youtube, instagram


def _title(slot, checks):
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    names = ", ".join(c.name for c in (fails or warns)[:3])
    if any(c.blocks == "youtube" for c in checks):
        title = f"[health-alert] {slot} IST slot at risk — {names}"
    elif any(c.blocks == "instagram" for c in checks):
        title = f"[health-alert] {slot} IST — Instagram cross-post at risk — {names}"
    elif fails:
        title = f"[health-alert] {slot} IST — backup provider problem — {names}"
    else:
        title = f"[health-alert] {slot} IST heads-up — {names}"
    return title[:240]


def render_issue(checks, slot, now, secrets):
    cron, check_ist, _slot, lead = schedule_row(slot)
    when = now.strftime("%Y-%m-%d %H:%M IST")
    youtube, instagram = impact(checks)
    problems = [c for c in checks if c.status in ("fail", "warn") and c.summary_hi]
    lines = [
        "<!-- health-alert -->",
        f"## {slot} IST",
        "",
        (
            f"Agla publish slot **{slot} IST** hai. Ye check **{check_ist} IST** pe schedule hai "
            f"({lead} min pehle; Actions cron `{cron}` UTC, IST = UTC+5:30). "
            f"Run time: {when}."
        ),
        "",
        f"**Video / YouTube:** {youtube}",
        f"**Instagram Reel:** {instagram}",
        "",
    ]
    if problems:
        lines.append("### Kya karna hai")
        lines.append("")
        for c in problems:
            lines.append(f"- **{c.name}** — {c.summary_hi}")
        lines.append("")
    else:
        lines.append(
            "Sab tracked checks theek hain. YouTube quota headroom measurable nahi hai — "
            "koi number guess nahi kiya, API ye remainder deti hi nahi."
        )
        lines.append("")
    lines.append("### Checks")
    lines.append("")
    lines.append("| Check | Status | Detail |")
    lines.append("|---|---|---|")
    for c in checks:
        lines.append(f"| {c.name} | {c.status.upper()} | {_cell(c.detail)} |")
    lines.append("")
    missing = []
    for c in checks:
        for secret in c.missing_secrets:
            if secret not in missing:
                missing.append(secret)
    lines.append("### Secrets missing (names only, values never written)")
    lines.append("")
    if missing:
        for secret in missing:
            lines.append(f"- `{secret}`")
    else:
        lines.append("None.")
    lines.append("")
    lines.append(
        "YouTube daily quota headroom: **not measurable** with these credentials. "
        "No Data API call was made to probe it."
    )
    lines.append("")
    lines.append(
        "_Opened by the Health check workflow. Updated in place (label `health-alert`, no duplicate). "
        "Auto-closes when every check is ok, skip, or note. Do not paste tokens here._"
    )
    body = redact("\n".join(lines) + "\n", secrets)
    if has_problem(checks):
        title = _title(slot, checks)
    else:
        title = f"[health-alert] resolved — {slot} IST checks passed"
    return redact(title, secrets), body


def format_log(checks, slot, now):
    cron, check_ist, _slot, lead = schedule_row(slot)
    lines = [
        f"[health] slot={slot} IST planned_check={check_ist} IST ({lead} min before, cron `{cron}` UTC) "
        f"ran={now.strftime('%Y-%m-%d %H:%M IST')}",
    ]
    for c in checks:
        lines.append(f"[{c.status}] {c.name} — {c.detail}")
    youtube, instagram = impact(checks)
    lines.append(f"[health] video/youtube={youtube} instagram={instagram}")
    return "\n".join(lines)


# ---------------------------------------------------------------- issues
def _gh_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "paise-ki-pathshala-health-check",
    }


def _repo_api(repo):
    return f"{GITHUB_API}/repos/{repo}"


def sync_issue(http, repo, token, title, body, problem, secrets):
    """Open, update, or close the single health-alert issue. Never creates a second open one."""
    if not token or not repo:
        return SyncResult("skipped", error="GITHUB_TOKEN or GITHUB_REPOSITORY missing; issue not filed")
    title = redact(title, secrets)
    body = redact(body, secrets)
    headers = _gh_headers(token)
    listed = http.send(
        "GET",
        f"{_repo_api(repo)}/issues",
        headers=headers,
        params={"state": "open", "labels": LABEL, "per_page": "100"},
        timeout=20,
    )
    if listed.status != 200 or not isinstance(listed.body, list):
        return SyncResult("error", error=redact(f"list issues HTTP {listed.status}: {_error_message(listed)}", secrets))
    issues = sorted(
        (i for i in listed.body if isinstance(i, dict) and "pull_request" not in i),
        key=lambda i: int(i.get("number") or 0),
    )
    if problem:
        ensured = _ensure_label(http, repo, headers, secrets)
        if ensured.action == "error":
            return ensured
        if not issues:
            created = http.send(
                "POST",
                f"{_repo_api(repo)}/issues",
                headers=headers,
                json_body={"title": title, "body": body, "labels": [LABEL]},
                timeout=20,
            )
            if created.status not in (200, 201) or not isinstance(created.body, dict):
                return SyncResult("error", error=redact(
                    f"create issue HTTP {created.status}: {_error_message(created)}", secrets))
            return SyncResult("opened", number=created.body.get("number"))
        primary = issues[0]
        updated = _patch_issue(http, repo, headers, primary["number"], {"title": title, "body": body}, secrets)
        if updated.action == "error":
            return updated
        for extra in issues[1:]:
            _close_one(
                http, repo, headers, extra["number"], secrets,
                comment=f"Duplicate health-alert. Tracking on #{primary['number']}.",
                title=None, body=None,
            )
        return SyncResult("updated", number=primary["number"])

    if not issues:
        return SyncResult("none")
    for issue in issues:
        comment = (
            "Health check pass ho gaya — koi warn/fail nahi bacha. "
            "YouTube quota headroom is run me bhi measurable nahi tha; ye expected hai, "
            "isliye issue close ho raha hai.\n\n" + body
        )
        closed = _close_one(
            http, repo, headers, issue["number"], secrets,
            comment=comment,
            title=title,
            body=body,
        )
        if closed.action == "error":
            return closed
    return SyncResult("closed", number=issues[0]["number"])


def _ensure_label(http, repo, headers, secrets):
    got = http.send("GET", f"{_repo_api(repo)}/labels/{LABEL}", headers=headers, timeout=20)
    if got.status == 200:
        return SyncResult("none")
    if got.status not in (404,):
        return SyncResult("error", error=redact(f"get label HTTP {got.status}: {_error_message(got)}", secrets))
    made = http.send(
        "POST",
        f"{_repo_api(repo)}/labels",
        headers=headers,
        json_body={"name": LABEL, "color": LABEL_COLOR, "description": LABEL_DESC},
        timeout=20,
    )
    if made.status in (200, 201, 422):
        # 422 = already exists (race).
        return SyncResult("none")
    return SyncResult("error", error=redact(f"create label HTTP {made.status}: {_error_message(made)}", secrets))


def _patch_issue(http, repo, headers, number, payload, secrets):
    resp = http.send(
        "PATCH",
        f"{_repo_api(repo)}/issues/{number}",
        headers=headers,
        json_body=payload,
        timeout=20,
    )
    if resp.status not in (200, 201):
        return SyncResult("error", number=number, error=redact(
            f"patch issue HTTP {resp.status}: {_error_message(resp)}", secrets))
    return SyncResult("updated", number=number)


def _close_one(http, repo, headers, number, secrets, comment, title, body):
    commented = http.send(
        "POST",
        f"{_repo_api(repo)}/issues/{number}/comments",
        headers=headers,
        json_body={"body": redact(comment, secrets)},
        timeout=20,
    )
    if commented.status not in (200, 201):
        return SyncResult("error", number=number, error=redact(
            f"comment HTTP {commented.status}: {_error_message(commented)}", secrets))
    payload = {"state": "closed", "state_reason": "completed"}
    if title:
        payload["title"] = title
    if body:
        payload["body"] = body
    return _patch_issue(http, repo, headers, number, payload, secrets)


def format_deepseek_balance(check):
    """One log line. sufficient=yes only when the shared balance judge says ok.

    ok means is_available and every currency is at least BALANCE_WARN.
    warn (balance above zero but under that bar) is sufficient=no.
    """
    sufficient = "yes" if check.status == "ok" else "no"
    detail = " ".join(str(check.detail or "").split())
    return f"[deepseek] status={check.status} sufficient={sufficient} {detail}"


def log_deepseek_balance(http, env):
    """Print the DeepSeek balance. Always returns 0 so a low balance is a log, not a new publish rule."""
    secrets = secrets_from_env(env)
    try:
        check = check_deepseek(http, env, secrets, selected=True)
    except Exception as e:  # noqa: BLE001
        print(redact(f"[deepseek] status=fail sufficient=no balance check crashed: {type(e).__name__}", secrets),
              flush=True)
        return 0
    print(redact(format_deepseek_balance(check), secrets), flush=True)
    return 0


def main(http=None, env=None, now=None):
    env = os.environ if env is None else env
    now = now or datetime.now(IST)
    http = http or RequestsHttp()
    secrets = secrets_from_env(env)
    slot, _cron = resolve_slot(env, now)
    try:
        checks = run_checks(http, env, now)
    except Exception as e:  # noqa: BLE001 — surface a red run without leaking secrets
        print(redact(f"[health] checker crashed: {type(e).__name__}: {e}", secrets), flush=True)
        return 1
    print(redact(format_log(checks, slot, now), secrets), flush=True)
    problem = has_problem(checks)
    title, body = render_issue(checks, slot, now, secrets)
    if _env(env, "HEALTH_DRY_RUN") == "1":
        print("[health] dry-run — GitHub issue not touched", flush=True)
        print(body, flush=True)
        return 1 if problem else 0

    result = sync_issue(
        http,
        _env(env, "GITHUB_REPOSITORY"),
        _env(env, "GITHUB_TOKEN"),
        title,
        body,
        problem,
        secrets,
    )
    print(f"[health] issue {result.action}" + (f" #{result.number}" if result.number else ""), flush=True)
    if result.error:
        print(redact(f"[health] issue error: {result.error}", secrets), flush=True)
    if problem or result.action == "error":
        return 1
    return 0


if __name__ == "__main__":
    if "--deepseek-balance" in sys.argv:
        # Log only. Do not open a GitHub issue and do not upload.
        sys.exit(log_deepseek_balance(RequestsHttp(), os.environ))
    sys.exit(main())
