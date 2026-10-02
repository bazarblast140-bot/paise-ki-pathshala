"""Cross-post the short (9:16) video to Instagram as a Reel.

Same final.mp4 that just went to YouTube Shorts — no re-render, no HeyGen,
no extra TTS. Cost is only hosting + Graph API (free).

Opt-in:
  IG_CROSSPOST=1
  IG_USER_ID=...
  IG_ACCESS_TOKEN=...
  IG_SURFACE=facebook   # or instagram

Hosting (public HTTPS URL for Instagram to fetch):
  GITHUB_TOKEN + GITHUB_REPOSITORY  (Actions default), or
  MEDIA_REPO + MEDIA_REPO_TOKEN     (separate public bucket repo)

Only runs for short format. Long (16:9) is skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from common import RUN, cfg, ok, video_format
import publish_guard
import review_gate

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
IG_VERSION = os.environ.get("IG_API_VERSION", "v23.0")


def die(msg: str) -> None:
    print(f"[ig] FAIL: {redact_text(msg)}", file=sys.stderr)
    sys.exit(1)


def skip(msg: str) -> None:
    print(f"[ig] skip — {msg}")
    sys.exit(0)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def http_json(url: str, *, method: str = "GET", data: bytes | None = None,
              headers: dict | None = None, form: dict | None = None) -> dict:
    hdrs = {"User-Agent": "paise-ki-pathshala-ig-crosspost", **(headers or {})}
    body = data
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw = res.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e


class GitHubHttpError(RuntimeError):
    def __init__(self, code: int, url: str, body: str = ""):
        self.code = int(code)
        self.url = url
        self.body = body or ""
        super().__init__(f"HTTP {self.code} {url}: {self.body[:400]}")


class HostNotReady(Exception):
    """The public asset URL is not serving yet. The upload loop retries this."""


# uploads.github.com 404s once in a while, and it also 404s (not 403) when the
# token cannot write. Re-resolve the release and try again before blaming it.
UPLOAD_ATTEMPTS = 3


def redact_text(text: str, extra: str = "") -> str:
    """Drop credential material. Never print MEDIA_REPO_TOKEN or a Bearer value."""
    out = str(text or "")
    secrets = [extra, env("MEDIA_REPO_TOKEN"), env("GITHUB_TOKEN"), env("ACTIONS_TOKEN_FOR_COMPARE"), env("IG_ACCESS_TOKEN")]
    for secret in secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, "***")
    out = re.sub(r"(?i)(Bearer\s+)\S+", r"\1***", out)
    out = re.sub(r"(?i)(token\s+)\S+", r"\1***", out)
    return out


def media_tag(now=None) -> str:
    """Daily release tag. UTC on purpose — runner local time is not IST.

    A 21:00 IST run that GitHub starts after midnight IST is still the previous
    UTC date. The tag is whatever UTC day it is at upload time, then looked up
    again on every attempt.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return "ig-reel-" + now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def hosting_credentials() -> tuple[str, str, str]:
    """(repo, token, source name). Source is a label, never the token value.

    GET and upload both use this same token. A public release GET succeeds
    even when the token cannot write; the upload is what needs contents:write.
    """
    media_repo = env("MEDIA_REPO")
    media_token = env("MEDIA_REPO_TOKEN")
    gh_token = env("GITHUB_TOKEN") or env("ACTIONS_TOKEN_FOR_COMPARE")
    if media_repo:
        if not media_token and not gh_token:
            die("Need GitHub media hosting credentials")
        token = media_token or gh_token
        if media_token and gh_token and media_token == gh_token:
            source = "GITHUB_TOKEN"
        elif media_token:
            source = "MEDIA_REPO_TOKEN"
        else:
            source = "GITHUB_TOKEN"
        if source == "GITHUB_TOKEN":
            print(
                f"[ig] WARN: media upload is using the calling repo GITHUB_TOKEN "
                f"for {media_repo}. That token can GET a public release and still "
                f"be refused on upload (HTTP 404). Set MEDIA_REPO_TOKEN to a PAT "
                f"with contents:write on {media_repo}. The token value is not logged."
            )
        return media_repo, token, source
    repo = env("GITHUB_REPOSITORY")
    token = gh_token or media_token
    if not repo or not token:
        die("Need GitHub media hosting credentials")
    return repo, token, "GITHUB_TOKEN" if token == gh_token and gh_token else "MEDIA_REPO_TOKEN"


def _github_request(url: str, *, method: str = "GET", data: bytes | None = None,
                    headers: dict | None = None, timeout: int = 120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise GitHubHttpError(e.code, url, body[:500]) from e


def _gh_headers(token: str, *, content_type: str | None = None, content_length: int | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "paise-ki-pathshala-ig-crosspost",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return headers


def _gh_json(method: str, url: str, token: str, data: bytes | None = None,
             content_type: str | None = None) -> dict:
    length = len(data) if data is not None else None
    _status, raw = _github_request(
        url, method=method, data=data,
        headers=_gh_headers(token, content_type=content_type, content_length=length),
    )
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else (raw or "")
    if not text:
        return {}
    return json.loads(text)


def _upload_backoff(attempt: int) -> float:
    raw = min(8.0, 2.0 * (2 ** attempt))
    return raw / 2.0 + random.uniform(0, raw / 2.0)


def ensure_release(repo: str, token: str, tag: str) -> dict:
    """Look the release up by tag now. Create it if that GET is a real 404."""
    tag_url = f"{API}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}"
    try:
        return _gh_json("GET", tag_url, token)
    except GitHubHttpError as e:
        if e.code != 404:
            raise
        print(f"[ig] release {tag} not found; creating it")
        try:
            return _gh_json(
                "POST", f"{API}/repos/{repo}/releases", token,
                data=json.dumps({
                    "tag_name": tag, "name": tag,
                    "body": "Paise Ki Pathshala short — IG Reel media",
                }).encode(),
                content_type="application/json",
            )
        except GitHubHttpError as created:
            if created.code not in (409, 422):
                raise
            return _gh_json("GET", tag_url, token)


def _upload_url(repo: str, release: dict, name: str) -> str:
    template = str(release.get("upload_url") or "")
    base = template.split("{", 1)[0] if template else ""
    if not base:
        base = f"{UPLOADS}/repos/{repo}/releases/{release['id']}/assets"
    return base + "?name=" + urllib.parse.quote(name)


def _asset_download_url(release: dict, name: str) -> str:
    """Public URL of an asset that already has this name, or ""."""
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        url = str(asset.get("browser_download_url") or "")
        if url.startswith("https://"):
            return url
    return ""


def diagnose_upload_failure(repo: str, token: str, tag: str, err: BaseException) -> str:
    """Tell a missing release apart from a token that cannot write. No token text."""
    code = getattr(err, "code", None)
    parts = []
    if env("MEDIA_REPO") and env("MEDIA_REPO_TOKEN") and env("ACTIONS_TOKEN_FOR_COMPARE") \
            and env("MEDIA_REPO_TOKEN") == env("ACTIONS_TOKEN_FOR_COMPARE"):
        parts.append(
            f"MEDIA_REPO_TOKEN is identical to the calling repo GITHUB_TOKEN. "
            f"That token is not contents:write on {repo}. "
            "uploads.github.com returns HTTP 404 for the upload. "
            f"Set MEDIA_REPO_TOKEN to a PAT with contents:write on {repo}. "
            "This job cannot change secrets."
        )
    try:
        repo_doc = _gh_json("GET", f"{API}/repos/{repo}", token)
    except GitHubHttpError as e:
        if e.code == 401:
            parts.append(
                f"MEDIA_REPO_TOKEN was rejected (HTTP 401) on GET /repos/{repo}. "
                "The token is expired, revoked, or not a GitHub token. "
                "A public release page can still exist while this credential cannot upload. "
                f"Update the MEDIA_REPO_TOKEN secret with contents:write on {repo}. "
                "This job cannot change secrets."
            )
        elif e.code == 404:
            parts.append(
                f"GET /repos/{repo} returned 404 with this token. "
                f"The token cannot see that repo, so it lacks contents:write on {repo}. "
                "GitHub answers the asset upload with HTTP 404 in that case, not 403. "
                "Put a PAT with contents:write in MEDIA_REPO_TOKEN. "
                "This job cannot change secrets."
            )
        else:
            parts.append(f"GET /repos/{repo} failed with HTTP {e.code}; token permissions were not confirmed.")
        return redact_text(" ".join(parts), token)

    perms = repo_doc.get("permissions") if isinstance(repo_doc, dict) else None
    push = perms.get("push") if isinstance(perms, dict) else None
    try:
        rel = _gh_json("GET", f"{API}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}", token)
        rel_state = f"Release tag {tag} exists (id {rel.get('id')})."
        missing = False
    except GitHubHttpError as e:
        if e.code == 404:
            parts.append(
                f"Release tag {tag} is missing (GET /releases/tags/{tag} returned 404). "
                "This is a missing release, not a contents:write error. "
                "The upload retried after creating the tag and still failed."
            )
            missing = True
            rel_state = ""
        elif e.code == 401:
            parts.append(
                f"MEDIA_REPO_TOKEN was rejected (HTTP 401) reading release tag {tag}. "
                "The token is expired or revoked. Update MEDIA_REPO_TOKEN. "
                "This job cannot change secrets."
            )
            return redact_text(" ".join(parts), token)
        else:
            rel_state = f"GET release tag {tag} returned HTTP {e.code}."
            missing = False
    if not missing:
        if push is False:
            parts.append(
                f"{rel_state} The same token's repo permissions have push=false, "
                f"so it lacks contents:write on {repo}. "
                "uploads.github.com returns HTTP 404, not 403, for that upload. "
                "A successful GET of a public release does not mean the token can upload. "
                f"Set MEDIA_REPO_TOKEN to a PAT with contents:write on {repo}, "
                "not the calling repository GITHUB_TOKEN. This job cannot change secrets."
            )
        elif push is True:
            parts.append(
                f"{rel_state} The token's repo permissions include push=true. "
                "The release was found by tag, so this is not a missing release and not a missing contents:write grant. "
                f"uploads.github.com still returned HTTP {code} after {UPLOAD_ATTEMPTS} tries. "
                "That matches a transient uploads.github.com failure. Re-run the Instagram step; do not rotate the token on this evidence alone."
            )
        else:
            parts.append(
                f"{rel_state} GET /repos/{repo} did not include permissions.push, "
                "so contents:write could not be confirmed. "
                f"If uploads keep returning HTTP {code}, confirm MEDIA_REPO_TOKEN has contents:write on {repo}."
            )
    if code is not None and int(code) >= 500 and push is not False:
        parts.append(
            f"Last uploads.github.com status was HTTP {code} (server error), after re-resolving tag {tag} on each try."
        )
    return redact_text(" ".join(parts), token)


def _url_serves(url: str) -> bool:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ig-host-check"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status == 200 and bool(res.read(8))


def _wait_until_public(url: str, token: str) -> None:
    """Poll the public URL. Not serving yet is retryable; it does not end the upload."""
    for wait_i in range(1, 9):
        try:
            if _url_serves(url):
                ok(f"hosted: {url}")
                return
        except Exception as err:
            print(f"  host wait {wait_i}/8: {redact_text(str(err), token)}")
        time.sleep(1.5)
    raise HostNotReady(f"Public URL not serving yet: {url}")


def host_release_asset(file: Path, *, name: str, content_type: str) -> str:
    """Upload a public release asset so Meta can fetch media without auth.

    The release id is resolved by tag at upload time (and again after a 404 or
    5xx). GET and POST use the same token. An asset that already has this name
    is reused. It is never deleted — that used to remove another run's file.
    """
    repo, token, source = hosting_credentials()
    tag = media_tag()
    print(f"[ig] hosting repo={repo} tag={tag} token_source={source} date_basis=UTC")
    last: BaseException | None = None
    for attempt in range(UPLOAD_ATTEMPTS):
        retryable = False
        try:
            release = ensure_release(repo, token, tag)
            print(f"[ig] upload {name} -> release {release.get('id')} tag {tag} (looked up by tag)")
            existing = _asset_download_url(release, name)
            if existing:
                print(f"[ig] reuse existing asset {name}; not deleting it")
                _wait_until_public(existing, token)
                return existing
            payload = file.read_bytes()
            asset = _gh_json(
                "POST", _upload_url(repo, release, name), token,
                data=payload, content_type=content_type,
            )
            url = asset.get("browser_download_url") or ""
            if not url.startswith("https://"):
                die(f"No public download URL from release asset: {asset}")
            _wait_until_public(url, token)
            return url
        except HostNotReady as e:
            last = e
            retryable = True
        except GitHubHttpError as e:
            if e.code == 422:
                # Same name already exists (this attempt's earlier POST). Reuse it.
                try:
                    reused = _asset_download_url(ensure_release(repo, token, tag), name)
                except GitHubHttpError as again:
                    last = again
                    retryable = again.code == 404 or again.code >= 500
                else:
                    if reused:
                        try:
                            _wait_until_public(reused, token)
                            return reused
                        except HostNotReady as not_ready:
                            last = not_ready
                            retryable = True
                    else:
                        last = e
                        retryable = True
            else:
                last = e
                retryable = e.code == 404 or e.code >= 500
        except TimeoutError as e:
            last = e
            retryable = True
        if last is None or not retryable or attempt >= UPLOAD_ATTEMPTS - 1:
            break
        wait = _upload_backoff(attempt)
        code = getattr(last, "code", None)
        print(
            f"[ig] upload HTTP {code or type(last).__name__} on {tag}; "
            f"re-resolve release and retry in {wait:.1f}s ({attempt + 1}/{UPLOAD_ATTEMPTS})"
        )
        time.sleep(wait)
    if isinstance(last, (GitHubHttpError, TimeoutError)):
        die(diagnose_upload_failure(repo, token, tag, last))
    die(redact_text(str(last), token) if last else "upload failed")
    return ""


def release_asset_name(kind: str, file: Path, suffix: str) -> str:
    """One name per Actions attempt and file bytes. Not a clock timestamp.

    ``paise-short-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}-{content hash}.mp4``
    (and the same shape for the cover). Two runs no longer share a name, so
    a retry does not have to delete anything.
    """
    run_id = re.sub(r"[^A-Za-z0-9._-]", "", env("GITHUB_RUN_ID") or "local") or "local"
    attempt = re.sub(r"[^A-Za-z0-9._-]", "", env("GITHUB_RUN_ATTEMPT") or "1") or "1"
    return f"{kind}-{run_id}-{attempt}-{content_hash(file)}{suffix}"


def host_mp4(file: Path) -> str:
    return host_release_asset(
        file, name=release_asset_name("paise-short", file, ".mp4"), content_type="video/mp4")


def host_cover(file: Path) -> str:
    return host_release_asset(
        file, name=release_asset_name("paise-cover", file, ".jpg"), content_type="image/jpeg")

def ig_host() -> str:
    surface = env("IG_SURFACE", "facebook")
    if env("IG_API_HOST"):
        return env("IG_API_HOST")
    return {
        "facebook": "https://graph.facebook.com",
        "instagram": "https://graph.instagram.com",
    }.get(surface, "https://graph.facebook.com")


def ig_call(path: str, *, method: str = "GET", params: dict | None = None) -> dict:
    token = env("IG_ACCESS_TOKEN")
    base = f"{ig_host()}/{IG_VERSION}/{path}"
    params = {**(params or {}), "access_token": token}
    if method == "GET":
        url = base + "?" + urllib.parse.urlencode(params)
        return http_json(url)
    return http_json(base, method="POST", form=params)


def script_sections(script: str) -> dict:
    """script.txt ke 'TITLE:' / 'HOOK (0-3 sec):' / 'CTA:' / 'FACT LEDGER:' blocks."""
    out, cur, buf = {}, None, []
    head = re.compile(r"^(TITLE|HOOK|BODY|CTA|DISCLAIMER(?: LINE)?|FACT LEDGER)\b[^:]*:?\s*(.*)$", re.I)
    for line in script.splitlines():
        m = head.match(line.strip())
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur = m.group(1).upper().split()[0]
            buf = [m.group(2)] if m.group(2).strip() else []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


def load_topic() -> dict:
    try:
        return json.loads((RUN / "topic.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hashtags_for(topic: dict) -> list[str]:
    """Topic-specific hashtags (main's selection logic, unchanged)."""
    cat = str(topic.get("cat") or "")
    cat_tags = {
        "CANDLESTICK": ["#CandlestickPatterns", "#CandlestickTrading"],
        "INDICATOR": ["#TechnicalAnalysis", "#TradingIndicators"],
        "OPTIONS": ["#OptionTrading", "#OptionsTrading"],
        "RISK": ["#RiskManagement", "#TradingRisk"],
        "ORDERS": ["#StockMarketBasics", "#OrderTypes"],
        "REAL DATA": ["#StockMarketFacts", "#MarketData"],
        "MARKET FACT": ["#StockMarketFacts", "#FinanceFacts"],
        "FUNDAMENTAL": ["#FundamentalAnalysis", "#StockAnalysis"],
        "PENNY STOCK": ["#PennyStocks", "#LiquidityRisk"],
        "INTRADAY": ["#IntradayTrading", "#PriceAction"],
    }
    tags = ["#StockMarketHindi", "#TradingHindi", "#PaiseKiPathshala", "#ShareMarket"] + cat_tags.get(cat, [])
    if topic.get("seo"):
        words = re.findall(r"[A-Za-z0-9]+", str(topic["seo"]))
        t = "#" + "".join(w[:1].upper() + w[1:] for w in words)
        if 2 < len(t) <= 40:
            tags.insert(1, t)
    return list(dict.fromkeys(tags))


def build_caption() -> str:
    """Reel caption. The first ~125 characters show before "more" and Instagram
    search reads caption words, so the keyword title goes first, then the hook,
    2 save-worthy takeaways (FACT LEDGER), comment + save CTA, disclaimer, bio
    CTA (with referral disclosure), hashtags. No URLs (not tappable in captions)."""
    script = (RUN / "script.txt").read_text(encoding="utf-8") if (RUN / "script.txt").exists() else ""
    sec = script_sections(script)
    topic = load_topic()
    one = lambda s: re.sub(r"\s+", " ", s or "").strip()  # noqa: E731
    title = one((sec.get("TITLE") or "").splitlines()[0] if sec.get("TITLE") else "")
    title = title or one((topic.get("thumb") or [""])[0])
    hook = one(sec.get("HOOK"))
    parts = []
    if title:
        parts.append(title[:120])
    if hook and hook.lower() != title.lower():
        parts.append(hook[:300])

    facts = [one(re.sub(r"^[-•*]\s*", "", f)) for f in (sec.get("FACT") or "").splitlines() if f.strip()]
    facts = facts or [one(str(f)) for f in topic.get("facts") or []]
    facts = [f for f in facts if f and "सलाह नहीं" not in f and "advice" not in f.lower()][:2]
    if facts:
        parts.append("📌 याद रखो:\n" + "\n".join(f"• {f}" for f in facts))

    cta = one(sec.get("CTA"))
    ask = cta.split("—")[0].split("?")[0].strip() + "?" if "?" in cta else "अगला कौन सा topic समझना है?"
    parts.append(f"💬 {ask} Comment में लिखो।\n💾 Save करो — अगली बार काम आएगा।")
    parts.append("यह सिर्फ़ शिक्षा के लिए है — निवेश सलाह नहीं।")

    # Referral link only in bio (caption URLs are not clickable). ASCI influencer
    # guidelines: a commission link must be disclosed.
    try:
        ref = json.loads((Path(__file__).parent / "data" / "referrals.json").read_text(encoding="utf-8"))
        names = ref.get("category_map", {}).get(str(topic.get("cat") or ""), [])
        if names:
            disc = ref.get("disclosure_hi") or "यह referral link है।"
            parts.append(f"Demat / invest account chahiye ho to profile bio me link dekho. {disc}")
            print(f"[OK] IG bio CTA (topic refs: {', '.join(names)})")
    except (OSError, ValueError, TypeError):
        pass

    parts.append(" ".join(hashtags_for(topic))[:900])
    return "\n\n".join(parts)[:2200]


# ---------------------------------------------------------------- IG cover
def ig_settings() -> dict:
    return {"grid_safe_cover": True, **(cfg().get("instagram") or {})}


def grid_safe_cover(src: Path, out: Path) -> Path:
    """The profile grid crops a Reel cover to the centre 3:4 (1080x1440, y 240..1680).
    Our thumbnail puts the title at y~150, so it was cut off in the grid. Shrink the
    whole design into a centred 3:4 box over a blurred copy of itself. Still looks
    fine full-screen (9:16)."""
    from PIL import Image, ImageEnhance, ImageFilter
    im = Image.open(src).convert("RGB")
    W, H = 1080, 1920
    if im.size != (W, H):
        im = im.resize((W, H))
    box_h = 1440
    inner = im.resize((int(W * box_h / H), box_h), Image.LANCZOS)
    bg = im.filter(ImageFilter.GaussianBlur(48))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    bg.paste(inner, ((W - inner.width) // 2, (H - box_h) // 2))
    bg.save(out, quality=92)
    return out


def prepare_cover(thumb: Path | None) -> Path | None:
    """IG-only cover copy (YouTube thumbnail.jpg is not changed). On any error use
    the original thumbnail so a Reel is never blocked by this."""
    if thumb is None or not ig_settings().get("grid_safe_cover"):
        return thumb
    try:
        cover = grid_safe_cover(thumb, RUN / "ig_cover.jpg")
        print("  cover: grid-safe ig_cover.jpg")
        return cover
    except Exception as e:  # noqa: BLE001
        print(f"[warn] grid-safe cover skipped ({type(e).__name__}) — original thumbnail")
        return thumb


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def video_identity(mp4: Path) -> dict:
    """YouTube id, topic key, file hash, and normalized title. One of these is enough to refuse a second Reel."""
    youtube_id = ""
    uploaded = RUN / "uploaded.txt"
    if uploaded.exists():
        youtube_id = uploaded.read_text(encoding="utf-8").strip().split()[0]
        if youtube_id.lower().startswith("http"):
            youtube_id = youtube_id.rstrip("/").split("/")[-1]
    topic = load_topic()
    script = (RUN / "script.txt").read_text(encoding="utf-8") if (RUN / "script.txt").exists() else ""
    sec = script_sections(script)
    one = lambda s: re.sub(r"\s+", " ", s or "").strip()  # noqa: E731
    title = one((sec.get("TITLE") or "").splitlines()[0] if sec.get("TITLE") else "")
    if not title:
        thumbs = topic.get("thumb") or [""]
        title = one(str(thumbs[0] if thumbs else ""))
    return {
        "video_id": youtube_id,
        "topic_key": str(topic.get("key") or "").strip(),
        "content_hash": content_hash(mp4),
        "title_norm": normalize_title(title),
    }


def caption_marker(ident: dict) -> str:
    """Short id tag embedded in the caption. The topic key stays stable across a re-render."""
    key = ident.get("topic_key") or "video"
    tag = f"pkp:{key}"
    if ident.get("video_id"):
        tag += ":" + ident["video_id"]
    elif ident.get("content_hash"):
        tag += ":" + ident["content_hash"]
    return tag


def titles_match(caption: str, title_norm: str) -> bool:
    if not title_norm or len(title_norm) < 8:
        return False
    lines = [ln for ln in (caption or "").splitlines() if ln.strip()]
    if lines and normalize_title(lines[0]) == title_norm:
        return True
    return title_norm in normalize_title(caption)


class LiveCheckError(RuntimeError):
    """The live media list could not be read. Publishing must stop."""


# Graph 5xx and timeouts are transient. Three tries, then fail closed — never post blind.
MEDIA_LIST_TRIES = 3
MEDIA_LIST_BACKOFF = (2, 5, 10)


def _transient_live_error(exc) -> bool:
    """HTTP 5xx and timeouts only. Auth and not-found stay hard errors."""
    if isinstance(exc, LiveCheckError):
        return False
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        return True
    text = str(exc)
    low = text.lower()
    if "timed out" in low or "timeout" in low:
        return True
    match = re.search(r"HTTP\s+(\d+)", text)
    return bool(match and int(match.group(1)) >= 500)


def _fetch_recent_media_once() -> list:
    """One media-list read. Raises the underlying HTTP/timeout error, or LiveCheckError."""
    ig_user = env("IG_USER_ID")
    if not ig_user or not env("IG_ACCESS_TOKEN"):
        raise LiveCheckError("IG_USER_ID and IG_ACCESS_TOKEN required for the live Reel check")
    params = {"fields": "id,caption,permalink", "limit": "25"}
    items = []
    for _page in range(3):
        data = ig_call(f"{ig_user}/media", params=params)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise LiveCheckError("Instagram media list did not return data; not posting")
        items.extend(data["data"])
        after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
        if not after:
            break
        params = {**params, "after": after}
    return items


def fetch_recent_media() -> list:
    """Recent IG media via the Graph API. Raises LiveCheckError — never returns a guess.

    5xx and timeouts are retried. Anything still failing blocks the Reel. A 4xx is not retried.
    """
    last = None
    for attempt in range(MEDIA_LIST_TRIES):
        try:
            return _fetch_recent_media_once()
        except LiveCheckError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _transient_live_error(exc):
                raise LiveCheckError(redact_text(str(exc))) from exc
            last = exc
            if attempt + 1 >= MEDIA_LIST_TRIES:
                break
            time.sleep(MEDIA_LIST_BACKOFF[attempt])
    raise LiveCheckError(redact_text(f"live media list failed after {MEDIA_LIST_TRIES} tries: {last}")) from last


def live_match(items, ident: dict) -> str:
    """Media id whose caption or permalink contains our marker, else the same normalized title."""
    marker = caption_marker(ident)
    topic_tag = f"pkp:{ident['topic_key']}" if ident.get("topic_key") else ""
    youtube_id = ident.get("video_id") or ""
    digest = ident.get("content_hash") or ""
    title = ident.get("title_norm") or ""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        blob = str(item.get("caption") or "") + "\n" + str(item.get("permalink") or "")
        if marker and marker in blob:
            return str(item.get("id") or "")
        if topic_tag and topic_tag in blob:
            return str(item.get("id") or "")
        if youtube_id and "pkp:" in blob and youtube_id in blob:
            return str(item.get("id") or "")
        if digest and digest in blob:
            return str(item.get("id") or "")
        if titles_match(str(item.get("caption") or ""), title):
            return str(item.get("id") or "")
    return ""


def existing_media(ident: dict) -> str:
    """Ledger and the live list. A live-list failure still refuses when the ledger already has this Reel.

    If the ledger has no match and the live list cannot be read, LiveCheckError — do not post.
    """
    import ledger
    found = ledger.find_ig_media(
        video_id=ident.get("video_id"),
        topic_key=ident.get("topic_key"),
        content_hash=ident.get("content_hash"),
        title_norm=ident.get("title_norm"),
    )
    try:
        live = live_match(fetch_recent_media(), ident)
    except LiveCheckError:
        if found:
            print(f"[ig] live check failed; ledger already has {found}; not posting")
            return found
        raise
    return found or live


def persist_ig_media(media_id: str, ident: dict | None) -> None:
    """Write the media id before any confirm poll. A later timeout must not publish again."""
    ident = ident or {}
    import ledger
    ledger.attach_ig_media(
        media_id,
        video_id=ident.get("video_id"),
        topic_key=ident.get("topic_key"),
        content_hash=ident.get("content_hash"),
        title_norm=ident.get("title_norm"),
    )
    (RUN / "ig_reel.txt").write_text(str(media_id), encoding="utf-8")


def recover_published(ident: dict | None) -> str:
    """Re-read the live list after a timeout. Empty string if it cannot be read — still do not post again."""
    if not ident:
        return ""
    try:
        return live_match(fetch_recent_media(), ident)
    except LiveCheckError as exc:
        print(f"[ig] live re-check failed ({exc}); not posting again")
        return ""


def confirm_media(media_id: str) -> bool:
    """Best-effort check that the published id is visible. A miss is not a reason to publish again."""
    for _ in range(3):
        try:
            seen = ig_call(media_id, params={"fields": "id"})
        except Exception as exc:  # noqa: BLE001
            print(f"[ig] confirm poll: {redact_text(str(exc))}")
            seen = {}
        if str((seen or {}).get("id") or "") == str(media_id):
            return True
        time.sleep(2)
    return False


def publish_reel(video_url: str, caption: str, cover_url: str = "", ident: dict | None = None) -> str:
    ig_user = env("IG_USER_ID")
    if not ig_user or not env("IG_ACCESS_TOKEN"):
        die("IG_USER_ID and IG_ACCESS_TOKEN required")

    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
    }
    if cover_url:
        params["cover_url"] = cover_url
    try:
        container = ig_call(f"{ig_user}/media", method="POST", params=params)
    except RuntimeError as e:
        if cover_url and "cover" in str(e).lower():
            print("[warn] Instagram cover rejected; retrying Reel without cover")
            params.pop("cover_url", None)
            container = ig_call(f"{ig_user}/media", method="POST", params=params)
        else:
            raise

    container_id = container.get("id")
    if not container_id:
        die(f"No container id: {container}")
    print(f"  container: {container_id}")

    finished = False
    for _i in range(60):
        st = ig_call(container_id, params={"fields": "status_code,status"})
        code = st.get("status_code")
        print(f"  processing: {code}")
        if code == "FINISHED":
            finished = True
            break
        if code in ("ERROR", "EXPIRED"):
            die(f"Instagram rejected video: {st}")
        time.sleep(5)
    if not finished:
        found = recover_published(ident)
        if found:
            persist_ig_media(found, ident)
            print(f"[ig] processing poll timed out; live list already has {found}; not posting again")
            return found
        die("Timed out waiting for Instagram processing")

    # One container publish. The media id is stored before the confirm poll.
    pub = ig_call(
        f"{ig_user}/media_publish",
        method="POST",
        params={"creation_id": container_id},
    )
    media_id = pub.get("id") or ""
    if not media_id:
        found = recover_published(ident)
        if found:
            persist_ig_media(found, ident)
            print(f"[ig] publish response had no id; live list already has {found}; not posting again")
            return found
        die(f"Publish failed: {pub}")
    persist_ig_media(media_id, ident)
    if not confirm_media(media_id):
        found = recover_published(ident)
        print(f"[ig] confirm timed out for {media_id}; not posting a second Reel")
        if found and found != media_id:
            persist_ig_media(found, ident)
        return media_id
    return media_id


def mark_ig_skipped(run_dir, video_id, reason) -> str:
    """YouTube already has this Short. Record the Reel miss for a later retry.

    Writes ``ig_status.txt`` as ``skipped: <reason>`` and notes that on the
    existing ledger row. Does not append a second slot row.
    """
    reason = " ".join(str(reason or "Instagram Reel was not confirmed").split())
    text = reason if reason.startswith("skipped:") else f"skipped: {reason}"
    path = Path(run_dir) / "ig_status.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    video_id = str(video_id or "").strip()
    if video_id:
        try:
            import ledger
            ledger.note_ig_skipped(video_id, text)
        except Exception as exc:  # noqa: BLE001 — the YouTube slot must still stand
            print(f"[ig] ledger ig_skipped note skipped: {exc}")
    else:
        print("[ig] no YouTube id; ledger ig_skipped note skipped")
    return text


def main() -> None:
    if env("IG_CROSSPOST") not in ("1", "true", "yes", "on"):
        skip("IG_CROSSPOST not enabled (set IG_CROSSPOST=1 to post short as Reel)")

    if video_format() != "short":
        skip("only short (9:16) videos become Reels")

    mp4 = RUN / "final.mp4"
    if not mp4.exists():
        die("run/final.mp4 missing")

    # Same quality bar as YouTube: IG_CROSSPOST_FORCE only bypasses "YouTube
    # upload missing" (e.g. dead token), never a NOT-READY verify verdict.
    # run_verify.py exits 0 even on "NEEDS FIX", and publish.py --only-if-ready
    # then refuses YouTube, so without this check a failed-review video was
    # still posted as a public Reel.
    reason = publish_guard.blocked_reason()
    if reason:
        skip(f"public Reel blocked ({reason}) — repair/test/retry never posts publicly")

    if not review_gate.files_allow_public(RUN, "short"):
        skip("verify verdict is not READY TO PUBLISH — Reel not posted")

    if not (RUN / "uploaded.txt").exists() and env("IG_CROSSPOST_FORCE") not in ("1", "true"):
        skip("YouTube upload not recorded (uploaded.txt missing) — set IG_CROSSPOST_FORCE=1 to post anyway")

    ident = video_identity(mp4)
    try:
        already = existing_media(ident)
    except LiveCheckError as exc:
        die(f"live Instagram check failed ({exc}); not posting")
    if already:
        (RUN / "ig_reel.txt").write_text(already, encoding="utf-8")
        skip(f"already published as Instagram media {already}")

    print("\n=== Instagram Reel cross-post ===")
    thumb = RUN / "thumbnail.jpg"
    cover = prepare_cover(thumb if thumb.exists() else None)
    url = host_mp4(mp4)
    cover_url = ""
    if cover is not None and cover.exists():
        cover_url = host_cover(cover)
        print(f"  cover: {cover_url}")
    caption = build_caption()
    marker = caption_marker(ident)
    if marker not in caption:
        extra = "\n\n" + marker
        caption = caption.rstrip()[: 2200 - len(extra)] + extra
    print(f"  caption ({len(caption)} chars): {caption[:80]}…")
    media_id = publish_reel(url, caption, cover_url, ident=ident)
    (RUN / "ig_reel.txt").write_text(media_id, encoding="utf-8")
    ok(f"IG Reel published: {media_id}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[ig] error: {redact_text(str(e))}", file=sys.stderr)
        sys.exit(1)
