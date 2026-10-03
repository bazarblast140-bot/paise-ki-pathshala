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
MAX_REPAIRS = 3


def log(msg):
    print(f"[AUTO-AGENT] {msg}", flush=True)


def run_cmd(args, env=None, capture=False):
    p = subprocess.run(args, cwd=ROOT, env=env, text=True,
                       capture_output=capture)
    if capture:
        return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")
    return p.returncode


def _paid_work_skipped(out):
    """True when slot_guard stopped the run before DeepSeek or ElevenLabs."""
    text = out or ""
    return "[skip]" in text and "before paid" in text


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
        "instagram publish failed",
        "ig_reel.txt missing",
        "instagram reel not confirmed",
        "elevenlabs api key is invalid",
        "elevenlabs api key is invalid/expired",
        "paid voice was not used",
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


def short_duration_window():
    """Four-beat live Shorts are 45-65s. Six-beat stays 70-95s. Buffer QA stays 45-70s."""
    lo, hi, label = 45.0, 70.0, "45-70s"
    try:
        data = json.loads((RUN / "narration.json").read_text(encoding="utf-8"))
        if str(data.get("source") or "") == "approved":
            import board_short
            lo, hi = board_short.APPROVED_VOICE_SECONDS
            return lo, hi, f"{lo:.0f}-{hi:.0f}s"
        if str(data.get("live_beats") or "") == "four":
            return 45.0, 65.0, "45-65s"
        beats = data.get("beats") or {}
        if str(beats.get("udaharan") or "").strip() and str(beats.get("galti") or "").strip():
            return 70.0, 95.0, "70-95s"
    except (OSError, ValueError, AttributeError):
        pass
    return lo, hi, label


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
        if fmt == "short":
            lo, hi, label = short_duration_window()
            if not lo <= dur <= hi:
                return False, f"short duration {dur:.1f}s outside {label} QA window"
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


def retry_same_topic(fmt, publish=True, mode="retry"):
    """Retry without consuming a new topic. Never a public upload.

    TOPIC_KEY keeps the same topic. PIPELINE_MODE and an unlisted visibility
    stop publish.py from scheduling a public video, and stop the Instagram
    Reel (a Reel is always public).
    """
    key = topic_key()
    env = os.environ.copy()
    if key:
        env["TOPIC_KEY"] = key
    env["PIPELINE_MODE"] = mode if mode in ("retry", "repair", "test", "qa") else "retry"
    env["PUBLISH_VISIBILITY"] = "unlisted"
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
    import credit_log
    credit_log.write_summary()

    if _paid_work_skipped(out):
        for line in (out or "").splitlines():
            if line.startswith("[skip]"):
                log(line)
        log("slot filled or on hold; exited before paid APIs")
        return 0 if rc == 0 else (rc or 1)

    if rc == 0:
        qa_ok, detail = board_quality(fmt)
        log(f"post-run visual QA: {detail}")
        if qa_ok:
            yt_id = (RUN / "uploaded.txt").read_text(encoding="utf-8").strip() if (RUN / "uploaded.txt").exists() else ""
            ig_id = (RUN / "ig_reel.txt").read_text(encoding="utf-8").strip() if (RUN / "ig_reel.txt").exists() else ""
            if yt_id:
                log(f"YouTube publish confirmed: {yt_id}")
            else:
                log("YouTube publish: no uploaded.txt confirmation")
            if ig_id:
                log(f"Instagram Reel publish confirmed: {ig_id}")
            else:
                log("Instagram Reel publish: no ig_reel.txt confirmation")
            log("pipeline + visual QA passed; no retry needed")
            return 0
        # A process exit of 0 is not enough: if the final video looks wrong,
        # feed the exact QA finding into the autonomous repair loop.
        out = out + "\n[AUTO-AGENT QA FAILURE]\n" + detail
        rc = 2
        log("pipeline exited 0 but visual QA failed; sending QA finding to self-repair")

    log("pipeline/QA failed; diagnosing before spending any more credits")
    # Print the captured child-process output so the actual root cause is visible
    # to Actions and available to the bounded self-repair engine.
    log("---- pipeline diagnostic tail ----")
    print(out[-12000:], flush=True)
    log("---- end pipeline diagnostic ----")
    if external_publish_failure(out):
        log("failure looks like external publishing/OAuth; no blind rebuild/retry")
        return _buffer_or_fail(rc, out)

    # Code self-repair (patch source files inside CI, re-run + publish, then
    # commit and push the patch to main) is OFF by default. Between 28-Sep and
    # 01-Oct-2026 it produced broken commits and burned LLM credit without fixing
    # the real causes. Opt in with repo variable / env SELF_REPAIR=1.
    if os.environ.get("SELF_REPAIR", "0").strip().lower() not in ("1", "true", "yes", "on"):
        log("self-repair disabled (set SELF_REPAIR=1 to enable); failing clearly instead")
        return _buffer_or_fail(rc, out)

    # Bounded LLM repair loop. The repair model sees the actual failure,
    # relevant source files, and proposes a safe patch. Each patch is syntax
    # checked before the same topic is retried. Max two repairs.
    try:
        import self_repair
    except Exception as e:
        log("self-repair engine unavailable: " + str(e))
        self_repair = None

    last_out = out
    for attempt in range(1, MAX_REPAIRS + 1):
        if self_repair is None:
            break
        try:
            changed, detail = self_repair.propose_and_apply(last_out, attempt)
        except Exception as e:
            changed, detail = False, "repair engine error: " + str(e)
        log("repair " + str(attempt) + ": " + detail)
        if not changed:
            break

        rc2, out2 = retry_same_topic(fmt, publish, mode="repair")
        last_out = out2
        (RUN / ("auto_agent_repair_" + str(attempt) + ".log")).write_text(
            out2[-30000:], encoding="utf-8"
        )
        if rc2 == 0:
            ok, detail = board_quality(fmt)
            log("repair " + str(attempt) + " visual QA: " + detail)
            if ok:
                try:
                    self_repair.commit_repair()
                    log("verified repair committed for future runs")
                except Exception as e:
                    log("verified repair could not be committed: " + str(e))
                log("self-heal succeeded")
                return 0
            log("repair produced an unsafe final video; trying next repair")
        else:
            if external_publish_failure(out2):
                log("repair retry exposed external publishing/auth failure; stopping")
                return rc2 or 1

    log("bounded self-repair exhausted; stopping without publishing an unsafe result")
    return _buffer_or_fail(rc, out)


def _buffer_or_fail(rc, out):
    """After the build has given up, fill the open slot from the buffer."""
    import buffer_shorts
    if not buffer_shorts.failure_should_try_fill():
        return rc or 1
    if not buffer_shorts.build_failed_gates(out):
        return rc or 1
    try:
        result = buffer_shorts.fill_from_env(False)
    except Exception as exc:  # noqa: BLE001 — a buffer error must not hide the build failure
        log(f"buffer fill failed: {exc}")
        return rc or 1
    if result.get("filled"):
        log(f"slot filled from buffer {result.get('video_id')}")
        return 0
    return rc or 1


if __name__ == "__main__":
    raise SystemExit(main())
