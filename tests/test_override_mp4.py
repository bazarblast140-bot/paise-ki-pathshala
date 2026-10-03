"""Release-asset override for Publish an artifact. No network."""
import json
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import override_mp4

ROOT = Path(__file__).resolve().parents[1]
GOOD = "https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/short-177/final.mp4"


def _mp4(path, seconds, size="1080x1920"):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={size}:r=10:d={seconds}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest", "-t", str(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


class UrlTests(unittest.TestCase):
    def test_only_this_repos_release_mp4_is_accepted(self):
        self.assertTrue(override_mp4.release_url_ok(GOOD))
        rejected = (
            "http://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/short-177/final.mp4",
            "https://github.com/other/paise-ki-pathshala/releases/download/short-177/final.mp4",
            "https://objects.githubusercontent.com/bazarblast140-bot/paise-ki-pathshala/releases/download/short-177/final.mp4",
            GOOD + "?token=1",
            GOOD.replace("final.mp4", "../final.mp4"),
            GOOD.replace(".mp4", ".zip"),
            "https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/final.mp4",
        )
        for url in rejected:
            self.assertFalse(override_mp4.release_url_ok(url), url)


class ReplaceTests(unittest.TestCase):
    def _open(self, blob):
        class Body:
            def read(self, n=-1):
                if n is None or n < 0:
                    return blob
                return blob[:n]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Body()

    def test_a_matching_release_replaces_the_artifact_and_a_mismatch_does_not(self):
        workflow = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        self.assertIn("override_mp4_url:", workflow)
        self.assertLess(workflow.index("python override_mp4.py"), workflow.index("python publish.py"))
        self.assertLess(workflow.index("python override_mp4.py"), workflow.index("python publish_instagram.py"))
        self.assertIn("inputs.ig_only != 'true'", workflow)
        replace = workflow.split("name: Replace final.mp4", 1)[1].split("name: Schedule publishAt", 1)[0]
        self.assertIn("inputs.override_mp4_url != ''", replace)
        self.assertNotIn("ig_only", replace)
        self.assertNotIn("skip_ig", replace)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "final.mp4"
            match = root / "match.mp4"
            long = root / "long.mp4"
            small = root / "small.mp4"
            _mp4(artifact, 1.0)
            _mp4(match, 1.0)
            _mp4(long, 3.0)
            _mp4(small, 1.0, size="640x360")
            original = artifact.read_bytes()

            def open_match(_req, timeout=0):
                return self._open(match.read_bytes())

            with mock.patch.object(override_mp4.urllib.request, "urlopen", side_effect=open_match):
                override_mp4.apply(GOOD, artifact)
            self.assertEqual(artifact.read_bytes(), match.read_bytes())

            artifact.write_bytes(original)

            def open_long(_req, timeout=0):
                return self._open(long.read_bytes())

            with mock.patch.object(override_mp4.urllib.request, "urlopen", side_effect=open_long):
                with self.assertRaises(SystemExit) as cm:
                    override_mp4.apply(GOOD, artifact)
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(artifact.read_bytes(), original)

            def open_small(_req, timeout=0):
                return self._open(small.read_bytes())

            with mock.patch.object(override_mp4.urllib.request, "urlopen", side_effect=open_small):
                with self.assertRaises(SystemExit) as cm:
                    override_mp4.apply(GOOD, artifact)
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(artifact.read_bytes(), original)

            with mock.patch.object(override_mp4.urllib.request, "urlopen", side_effect=AssertionError("no fetch")):
                with self.assertRaises(SystemExit):
                    override_mp4.apply("https://example.com/final.mp4", artifact)
            self.assertEqual(artifact.read_bytes(), original)

    def test_token_fetches_the_asset_api_and_drops_auth_off_github(self):
        workflow = (ROOT / ".github" / "workflows" / "publish-artifact.yml").read_text(encoding="utf-8")
        job = workflow.split("jobs:", 1)[1].split("steps:", 1)[0]
        self.assertIn("contents: write", job)
        self.assertIn("actions: read", job)
        replace = workflow.split("name: Replace final.mp4", 1)[1].split("name: Schedule publishAt", 1)[0]
        self.assertIn("GH_TOKEN: ${{ github.token }}", replace)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "final.mp4"
            remix = root / "remix.mp4"
            _mp4(artifact, 1.0)
            _mp4(remix, 1.0)
            remix_bytes = remix.read_bytes()
            seen = []

            class Body:
                def __init__(self, blob):
                    self._blob = blob

                def read(self, n=-1):
                    if n is None or n < 0:
                        return self._blob
                    return self._blob[:n]

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            def fake_open(req, timeout=0):
                seen.append(req)
                url = req.full_url
                headers = {k.lower(): v for k, v in req.header_items()}
                self.assertEqual(headers.get("authorization"), "Bearer test-token")
                if url.endswith("/releases/tags/short-177"):
                    self.assertIn("application/vnd.github+json", headers.get("accept", ""))
                    payload = json.dumps({
                        "assets": [
                            {"id": 7, "name": "other.mp4"},
                            {"id": 42, "name": "final.mp4"},
                        ]
                    }).encode()
                    return Body(payload)
                if url.endswith("/releases/assets/42"):
                    self.assertEqual(headers.get("accept"), "application/octet-stream")
                    return Body(remix_bytes)
                raise AssertionError(url)

            env = {"GH_TOKEN": "test-token", "GITHUB_TOKEN": ""}
            with mock.patch.object(override_mp4, "_open", side_effect=fake_open), \
                 mock.patch.dict("os.environ", env, clear=False):
                override_mp4.apply(GOOD, artifact)
            self.assertEqual(artifact.read_bytes(), remix_bytes)
            self.assertEqual(
                [req.full_url for req in seen],
                [
                    "https://api.github.com/repos/bazarblast140-bot/paise-ki-pathshala/releases/tags/short-177",
                    "https://api.github.com/repos/bazarblast140-bot/paise-ki-pathshala/releases/assets/42",
                ],
            )

        handler = override_mp4._DropAuthOffGitHub()
        original = urllib.request.Request(
            "https://api.github.com/repos/bazarblast140-bot/paise-ki-pathshala/releases/assets/42",
            headers={
                "Authorization": "Bearer test-token",
                "Accept": "application/octet-stream",
                "User-Agent": "paise-ki-pathshala",
            },
        )
        storage = "https://release-assets.githubusercontent.com/short-177/final.mp4"
        redirected = handler.redirect_request(original, None, 302, "Found", {}, storage)
        headers = {k.lower(): v for k, v in redirected.header_items()}
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers.get("accept"), "application/octet-stream")
        self.assertEqual(redirected.full_url, storage)
        stayed = handler.redirect_request(
            original, None, 302, "Found", {},
            "https://api.github.com/repos/bazarblast140-bot/paise-ki-pathshala/releases/assets/42",
        )
        stayed_headers = {k.lower(): v for k, v in stayed.header_items()}
        self.assertEqual(stayed_headers.get("authorization"), "Bearer test-token")
        foreign = handler.redirect_request(
            original, None, 302, "Found", {},
            "https://example-bucket.s3.amazonaws.com/final.mp4",
        )
        foreign_headers = {k.lower(): v for k, v in foreign.header_items()}
        self.assertNotIn("authorization", foreign_headers)

        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        hops = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hops.append((self.path, self.headers.get("Authorization"), self.headers.get("Accept")))
                if self.path == "/asset":
                    self.send_response(302)
                    loc = f"http://127.0.0.1:{self.server.server_address[1]}/file"
                    self.send_header("Location", loc)
                    self.end_headers()
                    return
                body = b"downloaded"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/asset",
                headers={
                    "Authorization": "Bearer test-token",
                    "Accept": "application/octet-stream",
                },
            )
            with override_mp4._open(req) as resp:
                self.assertEqual(resp.read(), b"downloaded")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(hops[0][0], "/asset")
        self.assertEqual(hops[0][1], "Bearer test-token")
        self.assertEqual(hops[1][0], "/file")
        self.assertIsNone(hops[1][1])
        self.assertEqual(hops[1][2], "application/octet-stream")
