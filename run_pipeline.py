"""Runs the whole pipeline in order (for VPS cron or local testing).
Stops on the first gate failure. Always notifies at the end.

Usage:
  python run_pipeline.py            # generate + verify + notify (no publish)
  python run_pipeline.py --publish  # also publish IF verdict READY (opt-in)
  python run_pipeline.py --format short --publish   # 9:16 Short (~50s)
"""
import os, subprocess, sys

# Format env me daalo, cfg() import se PEHLE — har stage subprocess isi env ko
# inherit karta hai aur apna config (resolution, word count) isi se leta hai.
if "--format" in sys.argv:
    os.environ["VIDEO_FORMAT"] = sys.argv[sys.argv.index("--format") + 1]

from common import ROOT, cfg, video_format

STAGES = [
    ("Preflight",       "check_setup.py"),
    ("Pick topic",      "pick_topic.py"),
    ("Script + Gate 1", "run_script.py"),
    ("Voice + Gate 2",  "run_voice.py"),
    ("Visuals + Gate 3","run_visuals.py"),
    ("Assembly",        "run_assembly.py"),
    ("Final verify",    "run_verify.py"),
]

def run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / script), *args], cwd=ROOT).returncode

def main():
    print(f"[info] format: {video_format()}")
    status = "success"
    for label, script in STAGES:
        print(f"\n=== {label} ===")
        rc = run(script)
        if rc != 0:
            print(f"[STOP] {label} failed (rc={rc}). Pipeline halted.")
            status = f"failed at: {label}"
            break

    # optional publish
    if status == "success" and "--publish" in sys.argv and cfg()["pipeline"]["auto_publish"]:
        print("\n=== Publish (opt-in) ===")
        run("publish.py", "--only-if-ready")

    # always notify
    print("\n=== Notify ===")
    run("notify.py", "--status", status)

    # Propagate stage failures to CI (and cron callers).  Previously the
    # notification step ran and the process exited 0 even when a stage had
    # failed, making GitHub Actions report a misleading success.
    if status != "success":
        sys.exit(1)

if __name__ == "__main__":
    main()
