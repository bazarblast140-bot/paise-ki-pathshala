"""Health check: mocked HTTP for every probe, plus issue open/update/close."""
import re
import unittest
from datetime import datetime
from pathlib import Path

import health_check as H

ROOT = Path(__file__).resolve().parent.parent
IST = H.IST
NOW = datetime(2026, 10, 1, 14, 30, tzinfo=IST)

YT_REFRESH = "refresh-token-DO-NOT-LEAK"
YT_SECRET = "client-secret-DO-NOT-LEAK"
YT_CLIENT = "client-id-DO-NOT-LEAK-999"
DS_KEY = "sk-deepseek-DO-NOT-LEAK"
KIMI_KEY = "sk-kimi-DO-NOT-LEAK-000"
ANT_KEY = "sk-ant-DO-NOT-LEAK-111"
EL_KEY = "el-key-DO-NOT-LEAK-2222"
IG_TOKEN = "ig-token-DO-NOT-LEAK-333"
GH_TOKEN = "gh-token-DO-NOT-LEAK-444"
ACCESS = "ya29.ACCESS-TOKEN-DO-NOT-LEAK"


def resp(status, body):
    return H.HttpResponse(status, body)


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def send(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for match, response in self.routes:
            if match(method, url, kwargs):
                return response() if callable(response) else response
        raise AssertionError(f"unexpected {method} {url}")


def route(method, needle, response):
    def match(m, url, _kwargs):
        return m == method and needle in url
    return (match, response)


def secrets_env(**extra):
    env = {
        "YOUTUBE_CLIENT_ID": YT_CLIENT,
        "YOUTUBE_CLIENT_SECRET": YT_SECRET,
        "YOUTUBE_REFRESH_TOKEN": YT_REFRESH,
        "DEEPSEEK_API_KEY": DS_KEY,
        "ELEVENLABS_API_KEY": EL_KEY,
        "IG_ACCESS_TOKEN": IG_TOKEN,
        "IG_USER_ID": "1784000000",
        "GITHUB_TOKEN": GH_TOKEN,
        "GITHUB_REPOSITORY": "acme/widgets",
    }
    env.update(extra)
    return env


def assert_clean(testcase, text):
    for secret in (YT_REFRESH, YT_SECRET, YT_CLIENT, DS_KEY, KIMI_KEY, ANT_KEY, EL_KEY, IG_TOKEN, GH_TOKEN, ACCESS):
        testcase.assertNotIn(secret, text)


class ScheduleTests(unittest.TestCase):
    def test_each_cron_is_90_minutes_before_the_slot_in_utc(self):
        self.assertEqual([row[2] for row in H.SCHEDULE], ["08:30", "16:00", "21:00"])
        for cron, check_ist, slot, lead in H.SCHEDULE:
            self.assertEqual(lead, 90)
            self.assertTrue(60 <= lead <= 90)
            ch, cm = map(int, check_ist.split(":"))
            sh, sm = map(int, slot.split(":"))
            self.assertEqual((ch * 60 + cm + lead) % (24 * 60), sh * 60 + sm)
            utc_min = (ch * 60 + cm - 5 * 60 - 30) % (24 * 60)
            self.assertEqual(cron, f"{utc_min % 60} {utc_min // 60} * * *")

    def test_cron_that_fired_wins_over_the_clock(self):
        env = {"HEALTH_CRON": "30 1 * * *"}
        slot, cron = H.resolve_slot(env, datetime(2026, 10, 1, 18, 0, tzinfo=IST))
        self.assertEqual((slot, cron), ("08:30", "30 1 * * *"))

    def test_explicit_slot_wins(self):
        slot, cron = H.resolve_slot({"HEALTH_SLOT": "21:00", "HEALTH_CRON": "30 1 * * *"}, NOW)
        self.assertEqual((slot, cron), ("21:00", "0 14 * * *"))

    def test_clock_picks_the_next_slot(self):
        self.assertEqual(H.resolve_slot({}, datetime(2026, 10, 1, 10, 0, tzinfo=IST))[0], "16:00")
        self.assertEqual(H.resolve_slot({}, datetime(2026, 10, 1, 22, 0, tzinfo=IST))[0], "08:30")
        self.assertEqual(H.resolve_slot({"HEALTH_SLOT": "auto"}, NOW)[0], "16:00")


class RedactTests(unittest.TestCase):
    def test_redacts_env_secrets_and_token_query_shapes(self):
        raw = f"err key={DS_KEY} access_token={IG_TOKEN} Bearer {GH_TOKEN} ya29.ACCESS-TOKEN-DO-NOT-LEAK"
        out = H.redact(raw, H.secrets_from_env(secrets_env()))
        assert_clean(self, out)
        self.assertIn("***", out)


class YoutubeTests(unittest.TestCase):
    def test_refresh_ok_discards_access_token_and_does_not_call_data_api(self):
        http = FakeHttp([route("POST", "oauth2.googleapis.com/token", resp(200, {
            "access_token": ACCESS, "expires_in": 3599, "token_type": "Bearer",
        }))])
        check = H.check_youtube(http, secrets_env(), H.secrets_from_env(secrets_env()))
        self.assertEqual(check.status, "ok")
        self.assertIn("expires_in=3599", check.detail)
        self.assertNotIn(ACCESS, check.detail)
        self.assertEqual(http.calls[0]["form"]["grant_type"], "refresh_token")
        self.assertNotIn("youtube.googleapis.com", http.calls[0]["url"])
        assert_clean(self, check.detail)

    def test_invalid_grant_blocks_the_slot_without_leaking_the_refresh_token(self):
        http = FakeHttp([route("POST", "oauth2.googleapis.com/token", resp(400, {
            "error": "invalid_grant",
            "error_description": f"Token has been expired or revoked. {YT_REFRESH}",
        }))])
        env = secrets_env()
        check = H.check_youtube(http, env, H.secrets_from_env(env))
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "youtube")
        self.assertIn("invalid_grant", check.detail)
        self.assertIn("In production", check.summary_hi)
        assert_clean(self, check.detail + check.summary_hi)

    def test_invalid_client(self):
        http = FakeHttp([route("POST", "oauth2.googleapis.com/token", resp(401, {
            "error": "invalid_client", "error_description": "The OAuth client was not found.",
        }))])
        check = H.check_youtube(http, secrets_env(), [])
        self.assertEqual(check.status, "fail")
        self.assertIn("invalid_client", check.detail)
        self.assertIn("YT_FIN_CLIENT_ID", check.summary_hi)

    def test_missing_secrets_names_github_secret_names_only(self):
        check = H.check_youtube(http=FakeHttp([]), env={}, secrets=[])
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "youtube")
        self.assertIn("YT_FIN_REFRESH_TOKEN", check.detail)
        self.assertNotIn("YOUTUBE_REFRESH_TOKEN=", check.detail)
        self.assertEqual(check.missing_secrets, ("YT_FIN_CLIENT_ID", "YT_FIN_CLIENT_SECRET", "YT_FIN_REFRESH_TOKEN"))
        self.assertEqual(FakeHttp([]).calls, [])


class DeepSeekTests(unittest.TestCase):
    def _check(self, response, selected=True, env=None):
        env = secrets_env() if env is None else env
        http = FakeHttp([route("GET", "/user/balance", response)])
        check = H.check_deepseek(http, env, H.secrets_from_env(env), selected=selected)
        return check, http

    def test_balance_ok(self):
        check, http = self._check(resp(200, {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "12.50",
                               "granted_balance": "0", "topped_up_balance": "12.50"}],
        }))
        self.assertEqual(check.status, "ok")
        self.assertIn("USD 12.5", check.detail)
        self.assertIn("/user/balance", http.calls[0]["url"])
        self.assertEqual(http.calls[0]["method"], "GET")
        self.assertTrue(http.calls[0]["headers"]["Authorization"].startswith("Bearer "))

    def test_strips_v1_from_base_url(self):
        env = secrets_env(DEEPSEEK_BASE_URL="https://api.deepseek.com/v1")
        check, http = self._check(resp(200, {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "3"}],
        }), env=env)
        self.assertEqual(check.status, "ok")
        self.assertEqual(http.calls[0]["url"], "https://api.deepseek.com/user/balance")

    def test_zero_balance_and_unavailable_fail(self):
        check, _ = self._check(resp(200, {
            "is_available": False,
            "balance_infos": [{"currency": "USD", "total_balance": "0.00"}],
        }))
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "youtube")
        self.assertIn("khali", check.summary_hi)

    def test_low_balance_warns_without_blocking(self):
        check, _ = self._check(resp(200, {
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": "0.40"}],
        }))
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")

    def test_invalid_key_redacts_echoed_secret(self):
        env = secrets_env()
        check, _ = self._check(resp(401, {
            "error": {"message": f"Authentication Fails, Your api key: {DS_KEY} is invalid",
                      "type": "authentication_error"},
        }), env=env)
        self.assertEqual(check.status, "fail")
        assert_clean(self, check.detail + check.summary_hi)

    def test_missing_key_fails_only_when_selected(self):
        missing = H.check_deepseek(FakeHttp([]), {}, [], selected=True)
        self.assertEqual(missing.status, "fail")
        self.assertIn("DEEPSEEK_API_KEY", missing.missing_secrets)
        skipped = H.check_deepseek(FakeHttp([]), {}, [], selected=False)
        self.assertEqual(skipped.status, "skip")

    def test_missing_balance_rows_are_not_guessed_as_zero(self):
        check, _ = self._check(resp(200, {"is_available": True, "balance_infos": []}))
        self.assertEqual(check.status, "warn")
        self.assertIn("not measurable", check.detail)
        self.assertNotIn("USD 0", check.detail)

    def test_publish_log_says_whether_the_balance_is_sufficient_and_hides_the_key(self):
        import io
        env = secrets_env(DEEPSEEK_API_KEY=DS_KEY)
        http = FakeHttp([route("GET", "/user/balance", resp(200, {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "12.50"}],
        }))])
        buf = io.StringIO()
        with unittest.mock.patch("sys.stdout", buf):
            self.assertEqual(H.log_deepseek_balance(http, env), 0)
        line = buf.getvalue()
        self.assertIn("sufficient=yes", line)
        self.assertIn("USD 12.5", line)
        self.assertNotIn(DS_KEY, line)
        low = H.format_deepseek_balance(H.Check("DeepSeek", "warn", "is_available=true, balance USD 0.4"))
        self.assertIn("sufficient=no", low)


class KimiTests(unittest.TestCase):
    def test_skipped_when_unset(self):
        check = H.check_kimi(FakeHttp([]), secrets_env(), [], selected=False)
        self.assertEqual(check.status, "skip")

    def test_balance_ok_reads_nested_data(self):
        http = FakeHttp([route("GET", "/users/me/balance", resp(200, {
            "code": 0, "status": True,
            "data": {"available_balance": 19.5, "voucher_balance": 10, "cash_balance": 9.5},
        }))])
        env = secrets_env(KIMI_API_KEY=KIMI_KEY)
        check = H.check_kimi(http, env, H.secrets_from_env(env), selected=False)
        self.assertEqual(check.status, "ok")
        self.assertIn("19.5", check.detail)
        self.assertEqual(check.blocks, "")
        self.assertIn("/v1/users/me/balance", http.calls[0]["url"])

    def test_zero_balance_fails_but_does_not_block_when_backup(self):
        http = FakeHttp([route("GET", "/users/me/balance", resp(200, {
            "data": {"available_balance": 0},
        }))])
        env = secrets_env(KIMI_API_KEY=KIMI_KEY)
        check = H.check_kimi(http, env, [], selected=False)
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "")

    def test_zero_balance_blocks_when_kimi_is_selected(self):
        http = FakeHttp([route("GET", "/users/me/balance", resp(200, {"available_balance": 0}))])
        env = secrets_env(KIMI_API_KEY=KIMI_KEY)
        check = H.check_kimi(http, env, [], selected=True)
        self.assertEqual(check.blocks, "youtube")

    def test_suspended_account(self):
        http = FakeHttp([route("GET", "/users/me/balance", resp(403, {
            "error": {"message": "Your account has been suspended.", "type": "permission_denied_error"},
        }))])
        env = secrets_env(KIMI_API_KEY=KIMI_KEY)
        check = H.check_kimi(http, env, H.secrets_from_env(env), selected=False)
        self.assertEqual(check.status, "fail")
        self.assertIn("suspended", check.detail.lower())
        assert_clean(self, check.detail)


class AnthropicTests(unittest.TestCase):
    def test_skipped_when_unset(self):
        check = H.check_anthropic(FakeHttp([]), secrets_env(), [])
        self.assertEqual(check.status, "skip")

    def test_valid_key_does_not_invent_a_balance(self):
        http = FakeHttp([route("GET", "api.anthropic.com/v1/models", resp(200, {
            "data": [{"id": "claude-haiku", "type": "model"}],
        }))])
        env = secrets_env(ANTHROPIC_API_KEY=ANT_KEY)
        check = H.check_anthropic(http, env, H.secrets_from_env(env))
        self.assertEqual(check.status, "ok")
        self.assertIn("not measurable", check.detail)
        self.assertNotRegex(check.detail, r"\$\d")
        self.assertEqual(http.calls[0]["headers"]["anthropic-version"], "2023-06-01")
        assert_clean(self, check.detail)

    def test_credit_balance_too_low(self):
        http = FakeHttp([route("GET", "/v1/models", resp(400, {
            "type": "error",
            "error": {"type": "invalid_request_error",
                      "message": "Your credit balance is too low to access the Anthropic API."},
        }))])
        env = secrets_env(ANTHROPIC_API_KEY=ANT_KEY)
        check = H.check_anthropic(http, env, [], selected=False)
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "")
        self.assertIn("credit balance", check.detail)

    def test_invalid_key(self):
        http = FakeHttp([route("GET", "/v1/models", resp(401, {
            "error": {"type": "authentication_error", "message": f"invalid x-api-key {ANT_KEY}"},
        }))])
        env = secrets_env(ANTHROPIC_API_KEY=ANT_KEY)
        check = H.check_anthropic(http, env, H.secrets_from_env(env), selected=True)
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "youtube")
        assert_clean(self, check.detail + check.summary_hi)


class ElevenLabsTests(unittest.TestCase):
    def _check(self, response, env=None, engine="elevenlabs"):
        env = secrets_env() if env is None else env
        http = FakeHttp([route("GET", "/v1/user/subscription", response)])
        return H.check_elevenlabs(http, env, H.secrets_from_env(env), engine=engine), http

    def test_remaining_characters(self):
        check, http = self._check(resp(200, {
            "tier": "creator", "character_count": 1000, "character_limit": 10000, "status": "active",
        }))
        self.assertEqual(check.status, "ok")
        self.assertIn("remaining=9000", check.detail)
        self.assertEqual(http.calls[0]["headers"]["xi-api-key"], EL_KEY)

    def test_low_remaining_warns(self):
        check, _ = self._check(resp(200, {
            "character_count": 9000, "character_limit": 10000, "status": "active",
        }))
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")
        self.assertIn("1000", check.detail)

    def test_zero_remaining_warns_and_names_edge_fallback(self):
        check, _ = self._check(resp(200, {
            "character_count": 10000, "character_limit": 10000, "status": "active",
        }))
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")
        self.assertIn("remaining=0", check.detail)
        self.assertIn("Edge", check.summary_hi)

    def test_day_budget_is_three_shorts(self):
        self.assertEqual(H.CHARS_PER_SHORT, 2000)
        self.assertEqual(H.SHORTS_PER_DAY, 3)
        self.assertEqual(H.ELEVEN_WARN_CHARS, 6000)

    def test_exactly_one_day_of_credits_is_ok(self):
        check, _ = self._check(resp(200, {
            "character_count": 4000, "character_limit": 10000, "status": "active",
        }))
        self.assertEqual(check.status, "ok")
        self.assertIn("remaining=6000", check.detail)

    def test_below_one_day_warns_about_three_shorts(self):
        check, _ = self._check(resp(200, {
            "character_count": 4001, "character_limit": 10000, "status": "active",
        }))
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")
        self.assertIn("5999", check.detail)
        self.assertIn("3 Shorts", check.detail)
        self.assertIn("6000", check.detail)
        self.assertIn("Edge", check.summary_hi)

    def test_incomplete_payment_warns_without_blocking(self):
        check, _ = self._check(resp(200, {
            "character_count": 9300, "character_limit": 20000, "status": "incomplete",
        }))
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")
        self.assertIn("payment status incomplete", check.detail)
        self.assertIn("remaining=10700", check.detail)
        self.assertIn("Edge", check.summary_hi)

    def test_payment_required_body_warns_even_on_http_401(self):
        check, _ = self._check(resp(401, {"detail": {
            "type": "payment_required",
            "code": "insufficient_credits",
            "status": "quota_exceeded",
            "message": f"You have insufficient credits {EL_KEY}",
        }}))
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")
        self.assertIn("Edge", check.detail + check.summary_hi)
        assert_clean(self, check.detail + check.summary_hi)

    def test_missing_permission_still_fails(self):
        check, _ = self._check(resp(403, {"detail": {
            "type": "authorization_error",
            "code": "insufficient_permissions",
            "message": "missing the text_to_speech permission",
        }}))
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "youtube")

    def test_invalid_key_redacts(self):
        env = secrets_env()
        check, _ = self._check(resp(401, {
            "detail": {"status": "invalid_api_key", "message": f"Invalid API key {EL_KEY}"},
        }), env=env)
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "youtube")
        assert_clean(self, check.detail + check.summary_hi)

    def test_missing_character_fields_are_not_treated_as_zero(self):
        check, _ = self._check(resp(200, {"tier": "creator", "status": "active"}))
        self.assertEqual(check.status, "warn")
        self.assertIn("not measurable", check.detail)
        self.assertNotIn("remaining=0", check.detail)

    def test_missing_key_warns_when_engine_is_elevenlabs(self):
        check = H.check_elevenlabs(FakeHttp([]), {}, [], engine="elevenlabs")
        self.assertEqual(check.status, "warn")
        self.assertEqual(check.blocks, "")
        self.assertIn("ELEVENLABS_API_KEY", check.missing_secrets)

    def test_missing_key_skips_when_engine_is_edge(self):
        check = H.check_elevenlabs(FakeHttp([]), {}, [], engine="edge")
        self.assertEqual(check.status, "skip")


class InstagramTests(unittest.TestCase):
    def _debug(self, data, now=NOW):
        http = FakeHttp([route("GET", "debug_token", resp(200, {"data": data}))])
        env = secrets_env()
        return H.check_instagram(http, env, H.secrets_from_env(env), now), http

    def test_valid_token_with_time_to_spare(self):
        expires = int(NOW.timestamp()) + 30 * 86400
        check, http = self._debug({"is_valid": True, "expires_at": expires, "scopes": ["instagram_basic"]})
        self.assertEqual(check.status, "ok")
        self.assertIn("30.0 days", check.detail)
        self.assertEqual(http.calls[0]["params"]["input_token"], IG_TOKEN)
        assert_clean(self, check.detail)

    def test_warns_at_14_days_and_not_at_just_over(self):
        exactly = int(NOW.timestamp()) + 14 * 86400
        check, _ = self._debug({"is_valid": True, "expires_at": exactly})
        self.assertEqual(check.status, "warn")
        self.assertIn("14", check.summary_hi)
        later = int(NOW.timestamp()) + 14 * 86400 + 120
        ok, _ = self._debug({"is_valid": True, "expires_at": later})
        self.assertEqual(ok.status, "ok")

    def test_uses_the_sooner_of_token_and_data_access_expiry(self):
        far = int(NOW.timestamp()) + 40 * 86400
        soon = int(NOW.timestamp()) + 10 * 86400
        check, _ = self._debug({"is_valid": True, "expires_at": far, "data_access_expires_at": soon})
        self.assertEqual(check.status, "warn")

    def test_expired_and_invalid(self):
        past = int(NOW.timestamp()) - 3600
        expired, _ = self._debug({"is_valid": True, "expires_at": past})
        self.assertEqual(expired.status, "fail")
        self.assertEqual(expired.blocks, "instagram")
        invalid, _ = self._debug({"is_valid": False, "expires_at": int(NOW.timestamp()) + 90 * 86400})
        self.assertEqual(invalid.status, "fail")
        self.assertIn("is_valid=false", invalid.detail)

    def test_no_expiry_is_ok(self):
        check, _ = self._debug({"is_valid": True, "expires_at": 0})
        self.assertEqual(check.status, "ok")
        self.assertIn("no expiry", check.detail)

    def test_debug_token_failure_falls_back_to_a_cheap_read_without_guessing_expiry(self):
        http = FakeHttp([
            route("GET", "debug_token", resp(400, {"error": {"message": "You must provide an app access token", "code": 100}})),
            route("GET", "/v23.0/1784000000", resp(200, {"id": "1784000000"})),
        ])
        env = secrets_env()
        check = H.check_instagram(http, env, H.secrets_from_env(env), NOW)
        self.assertEqual(check.status, "ok")
        self.assertIn("not measurable", check.detail)
        self.assertNotIn("2026-11-23", check.detail)
        self.assertEqual(len(http.calls), 2)
        assert_clean(self, check.detail + check.summary_hi)

    def test_both_reads_failing_is_a_bad_token(self):
        http = FakeHttp([
            route("GET", "debug_token", resp(400, {"error": {"message": f"Invalid token {IG_TOKEN}"}})),
            route("GET", "/v23.0/", resp(401, {"error": {"message": "Invalid OAuth access token."}})),
        ])
        env = secrets_env()
        check = H.check_instagram(http, env, H.secrets_from_env(env), NOW)
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.blocks, "instagram")
        assert_clean(self, check.detail + check.summary_hi)

    def test_missing_token(self):
        check = H.check_instagram(FakeHttp([]), {}, [], NOW)
        self.assertEqual(check.status, "fail")
        self.assertIn("IG_ACCESS_TOKEN", check.missing_secrets)


class QuotaTests(unittest.TestCase):
    def test_quota_is_a_note_and_makes_no_http_call(self):
        check = H.check_youtube_quota()
        self.assertEqual(check.status, "note")
        self.assertIn("not measurable", check.detail)
        self.assertNotRegex(check.detail, r"\d+\s+units")
        self.assertEqual(check.blocks, "")


def _healthy_checks():
    return [
        H.Check("YouTube token", "ok", "refresh ok"),
        H.Check("DeepSeek", "ok", "USD 12"),
        H.Check("Kimi", "skip", "not configured"),
        H.Check("Anthropic", "ok", "key accepted. Credit balance is not measurable."),
        H.Check("ElevenLabs", "ok", "remaining=9000"),
        H.Check("Instagram", "ok", "valid, expires in 30.0 days"),
        H.Check("YouTube quota", "note", "not measurable"),
    ]


def _broken_checks():
    checks = _healthy_checks()
    checks[0] = H.Check(
        "YouTube token", "fail", "invalid_grant",
        summary_hi="YouTube refresh token Google ne reject kar diya (invalid_grant).",
        blocks="youtube",
    )
    checks[1] = H.Check(
        "DeepSeek", "fail", "is_available=False, balance USD 0",
        summary_hi="DeepSeek balance khali hai.",
        blocks="youtube",
    )
    return checks


class ReportTests(unittest.TestCase):
    def test_alert_body_names_the_slot_in_hinglish_and_english(self):
        title, body = H.render_issue(_broken_checks(), "16:00", NOW, [DS_KEY, YT_REFRESH])
        self.assertIn("16:00 IST slot at risk", title)
        self.assertIn("YouTube token", title)
        self.assertIn("14:30 IST", body)
        self.assertIn("90 min pehle", body)
        self.assertIn("`0 9 * * *`", body)
        self.assertIn("**Video / YouTube:** AT RISK", body)
        self.assertIn("invalid_grant", body)
        self.assertIn("DeepSeek balance khali", body)
        self.assertIn("not measurable", body)
        self.assertIn("| YouTube quota | NOTE |", body)
        assert_clean(self, title + body)

    def test_heads_up_title_when_only_instagram_is_close_to_expiry(self):
        checks = _healthy_checks()
        checks[5] = H.Check(
            "Instagram", "warn", "valid, expires in 12 days",
            summary_hi="Instagram token 12 day(s) me expire hoga.",
        )
        title, body = H.render_issue(checks, "21:00", NOW, [])
        self.assertIn("heads-up", title)
        self.assertIn("21:00", title)
        self.assertIn("warning (expiry)", body)
        self.assertIn("**Video / YouTube:** ok", body)

    def test_backup_failure_does_not_call_the_video_blocked(self):
        checks = _healthy_checks()
        checks[2] = H.Check("Kimi", "fail", "account suspended", summary_hi="Kimi account suspended hai.", blocks="")
        title, body = H.render_issue(checks, "08:30", NOW, [])
        self.assertIn("backup provider", title)
        self.assertIn("**Video / YouTube:** ok", body)

    def test_quota_note_alone_is_not_a_problem(self):
        self.assertFalse(H.has_problem(_healthy_checks()))
        self.assertTrue(H.has_problem(_broken_checks()))


class IssueSyncTests(unittest.TestCase):
    def _sync(self, routes, problem, title="[health-alert] t", body="body"):
        http = FakeHttp(routes)
        result = H.sync_issue(http, "acme/widgets", GH_TOKEN, title, body, problem, [GH_TOKEN])
        return result, http

    def test_opens_one_issue_and_creates_the_label(self):
        result, http = self._sync([
            route("GET", "/labels/health-alert", resp(404, {"message": "Not Found"})),
            route("POST", "/labels", resp(201, {"name": "health-alert"})),
            route("GET", "/issues", resp(200, [])),
            route("POST", "/issues", resp(201, {"number": 12})),
        ], problem=True, body=f"leak {GH_TOKEN}")
        self.assertEqual(result.action, "opened")
        self.assertEqual(result.number, 12)
        posts = [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/issues")]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["json_body"]["labels"], ["health-alert"])
        self.assertNotIn(GH_TOKEN, posts[0]["json_body"]["body"])
        label = next(c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/labels"))
        self.assertEqual(label["json_body"]["name"], "health-alert")

    def test_updates_the_existing_issue_without_a_second_create(self):
        result, http = self._sync([
            route("GET", "/labels/health-alert", resp(200, {"name": "health-alert"})),
            route("GET", "/issues", resp(200, [{"number": 4, "title": "old"}])),
            route("PATCH", "/issues/4", resp(200, {"number": 4})),
        ], problem=True, title="[health-alert] 16:00 IST slot at risk — YouTube token", body="updated body")
        self.assertEqual(result.action, "updated")
        self.assertEqual(result.number, 4)
        creates = [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/issues")]
        self.assertEqual(creates, [])
        patch = next(c for c in http.calls if c["method"] == "PATCH")
        self.assertEqual(patch["json_body"]["title"], "[health-alert] 16:00 IST slot at risk — YouTube token")
        self.assertEqual(patch["json_body"]["body"], "updated body")
        self.assertNotIn("state", patch["json_body"])

    def test_closes_when_checks_pass_and_does_not_open_a_new_issue(self):
        result, http = self._sync([
            route("GET", "/issues", resp(200, [{"number": 4, "title": "old alert"}])),
            route("POST", "/issues/4/comments", resp(201, {})),
            route("PATCH", "/issues/4", resp(200, {"number": 4, "state": "closed"})),
        ], problem=False, title="[health-alert] resolved — 16:00 IST checks passed", body="all green")
        self.assertEqual(result.action, "closed")
        self.assertEqual(result.number, 4)
        creates = [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/issues")]
        self.assertEqual(creates, [])
        comment = next(c for c in http.calls if c["url"].endswith("/comments"))
        self.assertIn("pass", comment["json_body"]["body"].lower())
        self.assertIn("measurable", comment["json_body"]["body"])
        patch = next(c for c in http.calls if c["method"] == "PATCH")
        self.assertEqual(patch["json_body"]["state"], "closed")
        self.assertEqual(patch["json_body"]["state_reason"], "completed")
        self.assertEqual(patch["json_body"]["body"], "all green")

    def test_healthy_with_no_open_issue_does_nothing(self):
        result, http = self._sync([
            route("GET", "/issues", resp(200, [])),
        ], problem=False)
        self.assertEqual(result.action, "none")
        self.assertEqual([c["method"] for c in http.calls], ["GET"])

    def test_duplicate_open_issues_update_the_oldest_and_close_the_rest(self):
        result, http = self._sync([
            route("GET", "/labels/health-alert", resp(200, {"name": "health-alert"})),
            route("GET", "/issues", resp(200, [
                {"number": 9, "title": "newer"},
                {"number": 3, "title": "older"},
                {"number": 8, "title": "also", "pull_request": {"url": "x"}},
            ])),
            route("PATCH", "/issues/3", resp(200, {"number": 3})),
            route("POST", "/issues/9/comments", resp(201, {})),
            route("PATCH", "/issues/9", resp(200, {"number": 9})),
        ], problem=True)
        self.assertEqual(result.action, "updated")
        self.assertEqual(result.number, 3)
        creates = [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/issues")]
        self.assertEqual(creates, [])
        closed = next(c for c in http.calls if c["method"] == "PATCH" and c["url"].endswith("/issues/9"))
        self.assertEqual(closed["json_body"]["state"], "closed")

    def test_label_already_present_is_not_recreated(self):
        result, http = self._sync([
            route("GET", "/labels/health-alert", resp(200, {"name": "health-alert"})),
            route("GET", "/issues", resp(200, [])),
            route("POST", "/issues", resp(201, {"number": 1})),
        ], problem=True)
        self.assertEqual(result.action, "opened")
        self.assertFalse(any(c["method"] == "POST" and c["url"].endswith("/labels") for c in http.calls))

    def test_missing_token_does_not_call_github(self):
        http = FakeHttp([])
        result = H.sync_issue(http, "acme/widgets", "", "t", "b", True, [])
        self.assertEqual(result.action, "skipped")
        self.assertEqual(http.calls, [])


class WorkflowTests(unittest.TestCase):
    def on_block(self, text):
        m = re.search(r"^on:\n(.*?)(?=^\S)", text, re.M | re.S)
        return m.group(1) if m else ""

    def test_workflow_schedule_permissions_and_no_publish(self):
        text = (ROOT / ".github" / "workflows" / "health-check.yml").read_text(encoding="utf-8")
        on = self.on_block(text)
        self.assertIn("workflow_dispatch", on)
        self.assertNotRegex(on, r"(?m)^  push:")
        for cron, _check, _slot, _lead in H.SCHEDULE:
            self.assertIn(f'"{cron}"', text)
        self.assertIn("issues: write", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("secrets.GITHUB_TOKEN", text)
        self.assertNotIn("publish.py", text)
        self.assertNotIn("auto_agent.py", text)
        self.assertNotIn("youtube.upload", text)
        for option in ("08:30", "16:00", "21:00"):
            self.assertIn(option, text)


class MainTests(unittest.TestCase):
    def _routes(self, youtube_body, deepseek_body, eleven_body, ig_data, github):
        routes = [
            route("POST", "oauth2.googleapis.com/token", resp(200, youtube_body)),
            route("GET", "/user/balance", resp(200, deepseek_body)),
            route("GET", "api.elevenlabs.io", resp(200, eleven_body)),
            route("GET", "debug_token", resp(200, {"data": ig_data})),
        ]
        routes.extend(github)
        return routes

    def test_problem_opens_an_issue_and_exits_nonzero_without_printing_secrets(self):
        import io
        from contextlib import redirect_stdout
        expires = int(NOW.timestamp()) + 40 * 86400
        http = FakeHttp(self._routes(
            {"access_token": ACCESS, "expires_in": 3000, "token_type": "Bearer"},
            {"is_available": False, "balance_infos": [{"currency": "USD", "total_balance": "0"}]},
            {"character_count": 10, "character_limit": 10000, "status": "active"},
            {"is_valid": True, "expires_at": expires},
            [
                route("GET", "/labels/health-alert", resp(200, {"name": "health-alert"})),
                route("GET", "api.github.com/repos/acme/widgets/issues", resp(200, [])),
                route("POST", "api.github.com/repos/acme/widgets/issues", resp(201, {"number": 15})),
            ],
        ))
        env = secrets_env()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = H.main(http=http, env=env, now=NOW)
        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("issue opened #15", out)
        self.assertIn("16:00", out)
        assert_clean(self, out)
        post = next(c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/issues"))
        assert_clean(self, post["json_body"]["body"])
        self.assertIn("AT RISK", post["json_body"]["body"])
        hosts = {c["url"].split("/")[2] for c in http.calls}
        self.assertNotIn("www.googleapis.com", hosts)
        non_github_posts = [
            c for c in http.calls
            if c["method"] == "POST" and "api.github.com" not in c["url"]
        ]
        self.assertEqual([c["url"] for c in non_github_posts], ["https://oauth2.googleapis.com/token"])

    def test_all_clear_closes_the_open_issue_and_exits_zero(self):
        import io
        from contextlib import redirect_stdout
        expires = int(NOW.timestamp()) + 40 * 86400
        http = FakeHttp(self._routes(
            {"access_token": ACCESS, "expires_in": 3000},
            {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "8"}]},
            {"character_count": 10, "character_limit": 10000, "status": "active"},
            {"is_valid": True, "expires_at": expires},
            [
                route("GET", "api.github.com/repos/acme/widgets/issues", resp(200, [{"number": 15}])),
                route("POST", "/issues/15/comments", resp(201, {})),
                route("PATCH", "/issues/15", resp(200, {"number": 15})),
            ],
        ))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = H.main(http=http, env=secrets_env(), now=NOW)
        self.assertEqual(code, 0)
        self.assertIn("issue closed #15", buf.getvalue())
        assert_clean(self, buf.getvalue())
        patch = next(c for c in http.calls if c["method"] == "PATCH")
        self.assertEqual(patch["json_body"]["state"], "closed")

    def test_dry_run_does_not_touch_github(self):
        import io
        from contextlib import redirect_stdout
        expires = int(NOW.timestamp()) + 40 * 86400
        http = FakeHttp(self._routes(
            {"access_token": ACCESS, "expires_in": 3000},
            {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "8"}]},
            {"character_count": 10, "character_limit": 10000, "status": "active"},
            {"is_valid": True, "expires_at": expires},
            [],
        ))
        env = secrets_env(HEALTH_DRY_RUN="1")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = H.main(http=http, env=env, now=NOW)
        self.assertEqual(code, 0)
        self.assertIn("dry-run", buf.getvalue())
        self.assertFalse(any("api.github.com" in c["url"] for c in http.calls))


if __name__ == "__main__":
    unittest.main()
