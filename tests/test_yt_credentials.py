import unittest
from unittest import mock

import yt_credentials as Y


class YtCredentialTests(unittest.TestCase):
    ENV = {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "sec", "YOUTUBE_REFRESH_TOKEN": "tok"}

    def test_missing_env_is_reported_without_values(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(Y.YouTubeAuthError) as cm:
                Y.get_credentials()
            self.assertIn("YOUTUBE_REFRESH_TOKEN", str(cm.exception))

    def test_invalid_grant_is_flagged_token_dead_and_secret_not_leaked(self):
        from google.auth.exceptions import RefreshError
        with mock.patch.dict("os.environ", self.ENV, clear=True), \
             mock.patch("google.oauth2.credentials.Credentials.refresh",
                        side_effect=RefreshError("invalid_grant: Token has been expired or revoked.")):
            with self.assertRaises(Y.YouTubeAuthError) as cm:
                Y.get_credentials()
            self.assertTrue(cm.exception.token_dead)
            self.assertIn("In production", str(cm.exception))
            self.assertNotIn("tok", str(cm.exception).replace("token", ""))

    def test_exit_code_3_for_dead_token(self):
        with mock.patch.object(Y, "youtube_client", side_effect=Y.YouTubeAuthError("x", token_dead=True)):
            with self.assertRaises(SystemExit) as cm:
                Y.youtube_client_or_exit()
            self.assertEqual(cm.exception.code, Y.EXIT_TOKEN_DEAD)

    def test_unit_tests_do_not_emit_a_workflow_error_annotation(self):
        import io
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true", "SUPPRESS_GHA_COMMANDS": "1"}, clear=False), \
             mock.patch.object(Y, "youtube_client", side_effect=Y.YouTubeAuthError("x", token_dead=True)), \
             mock.patch("sys.stdout", out):
            with self.assertRaises(SystemExit):
                Y.youtube_client_or_exit()
        self.assertNotIn("::error", out.getvalue())

    def test_a_real_dead_token_still_annotates_the_job(self):
        import io
        import os
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=False), \
             mock.patch.object(Y, "youtube_client", side_effect=Y.YouTubeAuthError("x", token_dead=True)), \
             mock.patch("sys.stdout", out):
            os.environ.pop("SUPPRESS_GHA_COMMANDS", None)
            with self.assertRaises(SystemExit):
                Y.youtube_client_or_exit()
        self.assertIn("::error", out.getvalue())


if __name__ == "__main__":
    unittest.main()
