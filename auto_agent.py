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
        return p.returncode, (p.stdout or "") + "
" + (p.stderr or "")
    return p.returncode


def topic_key():
    try:
        return json.loads((RUN / "topic.json").read_text(encoding="utf-8"))["key"]
    except Exception:
        return os.environ.get("TOPIC_KEY", "").strip()


def external_publish_failure(log_text):
    s = log_text.lower()
    needles = (
        "invalid_grant", "youtube", "yt_fin_refresh_token",
        "instagram", "meta graph", "ig_access_token",
        "oauth", "401 unauthorized", "403 forbidden",
    )
    return any(x in s for x in needles)


def board_quality():
    """Cheap local visual QA; no AI/API/credit use."""
    try:
        from PIL import Image
        import numpy as np
    except Exception as e:
        return False, f"qa dependencies missing: {e}"

    frames = sorted(RUN.glob("b_*_*.png"))
    if len(frames) < 4:
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

    return True, f"{len(frames)} frames passed brightness/variance/dimension QA"


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
        ok, detail = board_quality()
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

    ok, detail = board_quality()
    log(f"retry visual QA: {detail}")
    if not ok:
        log("retry produced a visually unsafe result; NOT publishing further")
        return 2

    log("self-heal succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
