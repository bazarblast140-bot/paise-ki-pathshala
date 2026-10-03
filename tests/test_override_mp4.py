"""Release-asset override for Publish an artifact. No network."""
import subprocess
import tempfile
import unittest
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
