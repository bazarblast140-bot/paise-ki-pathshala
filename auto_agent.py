"""Auto QA + self-heal controller for Paise Ki Pathshala.

Design goal: one automatic retry, zero extra LLM/TTS spend where possible.
It never publishes a visually failed video. It diagnoses deterministic failures,
reuses cached narration/audio, and only retries failures that are safe to retry.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "run"
MAX_RETRIES = 1


def log(msg):
    print(f"[AUTO-AGENT] {msg}", flush=True)


def run_cmd(args, env=None, capture=False):
    p = subprocess.run(args, cwd=ROOT, env=env, text=True,
                       capture_output=capture)
    if capture:
        return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")
    return p.returncode


def topic_key():
    try:
        return json.loads((RUN / "topic.json").read_text(encoding="utf-8"))["key"]
    except Exception:
        return os.environ.get("TOPIC_KEY", "").strip()


def external_publish_failure(log_text):
    """Detect a real publish/auth failure without matching harmless env/config text.
    Never classify a build failure just because the log contains words like
    'youtube', 'instagram', or 'oauth' in environment variables or comments.
    """
    lines = [ln.lower() for ln in log_text.splitlines()]
    hard = (
        "invalid_grant",
        "refresherror",
        "invalid credentials",
        "insufficient permission",
        "access token has expired",
        "oauth token has expired",
        "authentication failed",
    )
    if any(x in ln for ln in lines for x in hard):
        return True

    for ln in lines:
        publish_ctx = any(x in ln for x in (
            "publish.py", "publish_instagram.py", "youtube upload",
            "instagram reel", "meta graph", "youtube api",
        ))
        auth_error = any(x in ln for x in (
            "401", "403", "unauthorized", "forbidden",
            "oauth", "access token", "permission denied",
        ))
        if publish_ctx and auth_error:
            return True
    return False


def board_quality(fmt="short"):
    """Cheap local visual + final-video QA; no AI/API/credit use."""
    try:
        from PIL import Image
        import numpy as np
    except Exception as e:
        return False, f"qa dependencies missing: {e}"

    frames = sorted(RUN.glob("b_*_*.png"))
    if fmt != "short":
        frames = []
    if fmt == "short" and len(frames) < 4:
        return False, f"only {len(frames)} board frames found"

    stats = []
    for p in frames:
        try:
            im = Image.open(p).convert("RGB")
            a = np.asarray(im, dtype=np.uint8)
            gray = a.mean(axis=2)
            bright = float((gray > 42).mean())
            spread = float(gray.std())
            if im.width < 500 or im.height < 800:
                return False, f"{p.name}: wrong dimensions {im.size}"
            if bright < 0.010 or spread < 5.0:
                return False, f"{p.name}: blank-like bright={bright:.3%} std={spread:.1f}"
            stats.append((bright, spread))
        except Exception as e:
            return False, f"{p.name}: {e}"

    # Final MP4 checks: duration, dimensions, audio/video streams, and sampled frames.
    final = RUN / "final.mp4"
    if not final.exists() or final.stat().st_size < 100_000:
        return False, "final.mp4 missing or suspiciously small"
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(final)],
            capture_output=True, text=True, check=True)
        meta = json.loads(probe.stdout)
        dur = float(meta["format"]["duration"])
        streams = meta.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = any(s.get("codec_type") == "audio" for s in streams)
        if not video or not audio:
            return False, "final.mp4 missing video or audio stream"
        expected = (1080, 1920) if fmt == "short" else (1920, 1080)
        if (video.get("width"), video.get("height")) != expected:
            return False, f"wrong final resolution {video.get('width')}x{video.get('height')}"
        if fmt == "short" and not 45 <= dur <= 70:
            return False, f"short duration {dur:.1f}s outside 45-70s QA window"
        if fmt != "short" and dur < 60:
            return False, f"long duration {dur:.1f}s unexpectedly short"
        # Sample the video at 5s intervals. Blank/near-static output is rejected.
        sample_dir = RUN / "auto_agent_samples"
        sample_dir.mkdir(exist_ok=True)
        sample = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(final), "-vf",
             "fps=1/5,scale=270:-1", "-frames:v", "14",
             str(sample_dir / "sample_%02d.jpg")],
            capture_output=True, text=True)
        if sample.returncode != 0:
            return False, "ffmpeg sample extraction failed"
        imgs = list(sample_dir.glob("sample_*.jpg"))
        if len(imgs) < 5:
            return False, "too few final-video samples"
        low_var = 0
        for p in imgs:
            a = np.asarray(Image.open(p).convert("L"), dtype=np.uint8)
            if float(a.std()) < 5.0 or float((a > 42).mean()) < 0.008:
                low_var += 1
        if low_var > max(1, len(imgs) // 3):
            return False, f"{low_var}/{len(imgs)} final-video samples look blank/static"
        return True, f"{len(frames)} boards + final {dur:.1f}s {video.get('width')}x{video.get('height')} + {len(imgs)} video samples passed"
    except Exception as e:
        return False, f"final video QA error: {e}"


def run_once(fmt, publish=True):
    args = [sys.executable, "run_pipeline.py", "--format", fmt]
    if publish:
        args.append("--publish")
    return run_cmd(args, capture=True)


def retry_same_topic(fmt, publish=True):
    """Retry without consuming a new topic. TOPIC_KEY also prevents rotation."""
    key = topic_key()
    env = os.environ.copy()
    if key:
        env["TOPIC_KEY"] = key
    args = [sys.executable, "run_pipeline.py", "--format", fmt]
    if publish:
        args.append("--publish")
    return run_cmd(args, env=env, capture=True)


def main():
    fmt = "short"
    if "--format" in sys.argv:
        i = sys.argv.index("--format")
        if i + 1 < len(sys.argv):
            fmt = sys.argv[i + 1]
    publish = "--publish" in sys.argv

    # First normal run.
    rc, out = run_once(fmt, publish)
    (RUN / "auto_agent_last.log").write_text(out[-30000:], encoding="utf-8")

    if rc == 0:
        ok, detail = board_quality(fmt)
        log(f"post-run visual QA: {detail}")
        if not ok:
            log("published pipeline reported success but visual QA failed; blocking success.")
            return 2
        log("pipeline + visual QA passed; no retry needed")
        return 0

    log("pipeline failed; diagnosing before spending any more credits")
    if external_publish_failure(out):
        log("failure looks like external publishing/OAuth; no blind rebuild/retry")
        return rc or 1

    # Only one self-heal retry. Existing run/narration + tts files are reused by
    # board_short, so the retry does not call the LLM/TTS again unnecessarily.
    log("safe build failure: retrying the SAME topic once")
    rc2, out2 = retry_same_topic(fmt, publish)
    (RUN / "auto_agent_retry.log").write_text(out2[-30000:], encoding="utf-8")
    if rc2 != 0:
        log("self-heal retry failed; stopping to avoid credit burn")
        return rc2 or 1

    ok, detail = board_quality(fmt)
    log(f"retry visual QA: {detail}")
    if not ok:
        log("retry produced a visually unsafe result; NOT publishing further")
        return 2

    log("self-heal succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
