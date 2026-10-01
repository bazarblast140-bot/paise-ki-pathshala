"""One place that turns the long-lived YouTube refresh token into a fresh access token.

Every run starts from the refresh token stored in GitHub Secrets
(YT_FIN_REFRESH_TOKEN -> env YOUTUBE_REFRESH_TOKEN) and asks Google for a brand-new
access token. Access tokens only live ~1 hour; that is normal and handled here.

The refresh token itself only "expires" when:
  * the Google Cloud OAuth consent screen is in **Testing** status (tokens die after
    7 days -- this is what broke uploads on 28-Sep-2026), or
  * access was revoked / the password changed / 100+ newer tokens were minted, or
  * it went unused for 6 months.
Fix once: publish the consent screen to **In production**, then mint a new token
with `python youtube_auth.py` (see README -> "YouTube token permanent kaise karein").

Secret values are never printed or logged by this module.
"""
from __future__ import annotations

import os
import sys

TOKEN_URI = "https://oauth2.googleapis.com/token"

# Exit code used when the refresh token itself is dead (invalid_grant). Callers
# and CI can tell "token must be regenerated" apart from ordinary failures.
EXIT_TOKEN_DEAD = 3

INVALID_GRANT_HELP = """\
[FAIL] YouTube refresh token REJECTED by Google (invalid_grant: expired or revoked).
       Uploads will not work until the token is regenerated. Most common cause:
       the Google Cloud OAuth consent screen is in "Testing" status, where Google
       kills refresh tokens after 7 days.
       Fix (one time, ~5 minutes):
         1. console.cloud.google.com -> APIs & Services -> OAuth consent screen
            (a.k.a. "Google Auth Platform" -> Audience) -> "Publish app" -> In production.
         2. On your laptop: python youtube_auth.py   (sign in with the channel's account)
         3. Update GitHub secret YT_FIN_REFRESH_TOKEN with the new token
            (the script prints the exact `gh secret set` command).
       Details: README.md -> "YouTube token permanent kaise karein".
"""


class YouTubeAuthError(RuntimeError):
    """Raised when credentials are missing or the refresh token is dead."""

    def __init__(self, message: str, token_dead: bool = False):
        super().__init__(message)
        self.token_dead = token_dead


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def missing_env() -> list[str]:
    return [k for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
            if not _env(k)]


def get_credentials(scopes=None):
    """Return google.oauth2 Credentials with a freshly minted access token.

    Raises YouTubeAuthError (token_dead=True for invalid_grant) instead of letting
    a raw traceback (which is easy to misread) reach the logs.
    """
    missing = missing_env()
    if missing:
        raise YouTubeAuthError("Missing env var(s): " + ", ".join(missing)
                               + " (GitHub secrets YT_FIN_CLIENT_ID / YT_FIN_CLIENT_SECRET / "
                               "YT_FIN_REFRESH_TOKEN).")
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    # scopes=None on purpose: sending a scope list on refresh can make Google
    # reject a token that was minted with a different (but valid) scope set.
    creds = Credentials(
        None,
        refresh_token=_env("YOUTUBE_REFRESH_TOKEN"),
        client_id=_env("YOUTUBE_CLIENT_ID"),
        client_secret=_env("YOUTUBE_CLIENT_SECRET"),
        token_uri=TOKEN_URI,
        scopes=scopes,
    )
    last = None
    for attempt in range(3):
        try:
            creds.refresh(Request())
            return creds
        except RefreshError as e:
            text = str(e).lower()
            if "invalid_grant" in text or "expired or revoked" in text:
                raise YouTubeAuthError(INVALID_GRANT_HELP, token_dead=True) from None
            if "invalid_client" in text or "unauthorized_client" in text:
                raise YouTubeAuthError(
                    "[FAIL] Google rejected the OAuth client (invalid_client). "
                    "YT_FIN_CLIENT_ID / YT_FIN_CLIENT_SECRET don't match the OAuth app "
                    "that minted YT_FIN_REFRESH_TOKEN, or the client was deleted.") from None
            raise YouTubeAuthError(f"[FAIL] YouTube token refresh failed: {type(e).__name__}") from None
        except TransportError as e:  # network blip: retry
            last = e
            import time
            time.sleep(5 * (attempt + 1))
    raise YouTubeAuthError(f"[FAIL] Could not reach Google OAuth server: {type(last).__name__}")


def youtube_client(scopes=None):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=get_credentials(scopes), cache_discovery=False)


def youtube_client_or_exit(scopes=None):
    """CLI helper: print a clear message and exit (code 3 = token must be regenerated)."""
    try:
        return youtube_client(scopes)
    except YouTubeAuthError as e:
        print(str(e), file=sys.stderr)
        # Unit tests call this while GITHUB_ACTIONS=true (the preflight step).
        # An annotation from a simulated failure shows up on the job even when
        # the later token health check is healthy, and hides the real error.
        if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("SUPPRESS_GHA_COMMANDS") != "1":
            title = "YouTube token expired/revoked" if e.token_dead else "YouTube auth failed"
            print(f"::error title={title}::See README 'YouTube token permanent kaise karein'")
        sys.exit(EXIT_TOKEN_DEAD if e.token_dead else 1)


if __name__ == "__main__":
    # `python yt_credentials.py` = safe token health check (no upload, prints no secrets).
    yt = youtube_client_or_exit()
    items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not items:
        print("[FAIL] token works but has no YouTube channel", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] YouTube token healthy -> channel: {items[0]['snippet']['title']} ({items[0]['id']})")
