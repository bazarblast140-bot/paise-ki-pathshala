"""Runs the whole pipeline in order (for VPS cron or local testing).
Stops on the first gate failure. Always notifies at the end.

Usage:
  python run_pipeline.py            # generate + verify + notify (no publish)
  python run_pipeline.py --publish  # also publish IF verdict READY (opt-in)
  python run_pipeline.py --format short --publish   # 9:16 Short (~50s)

For short videos, Instagram Reel cross-post is independent of YouTube upload:
  IG_CROSSPOST=1  + IG_USER_ID + IG_ACCESS_TOKEN
  A missing/failed YouTube upload must not block Instagram.
"""
import json, os, subprocess, sys

# Format env me daalo, cfg() import se PEHLE — har stage subprocess isi env ko
# inherit karta hai aur apna config (resolution, word count) isi se leta hai.
if "--format" in sys.argv:
    os.environ["VIDEO_FORMAT"] = sys.argv[sys.argv.index("--format") + 1]

from common import ROOT, RUN, cfg, video_format

# Itni baar upload na ho paaye to topic chhod do — warna har slot usi par atakta.
MAX_TOPIC_TRIES = 2

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

# Short = Chart Board template (board_short.py): tasveer code se, on-screen Hindi
# topic_bank se, LLM sirf narration. Long wala raasta pehle jaisa.
SHORT_STAGES = [
    ("Preflight",       "check_setup.py"),
    ("Pick topic",      "pick_topic.py"),
    ("Chart Board",     "board_short.py"),
    ("Final verify",    "run_verify.py"),
]


def settle_topic(uploaded):
    """pick_topic topic ko pehle hi used mark kar deta hai. Upload na hua (verify
    NOT READY / upload error) to use rotation me wapas daalo — 14-Sep-2026 ko
    nifty_base aise hi bina video ke kharch ho gaya tha. Har fail ginti hoti hai;
    MAX_TOPIC_TRIES ke baad topic chhoot jaata hai. Upload hone par ginti saaf."""
    if os.environ.get("TOPIC_KEY", "").strip():
        return  # test run: pick_topic ne used mark hi nahi kiya
    try:
        tid = json.loads((RUN / "topic.json").read_text(encoding="utf-8"))["id"]
    except (OSError, ValueError, KeyError):
        return
    bank_path = ROOT / "topic_bank.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    t = next((t for t in bank["topics"] if t.get("id") == tid), None)
    flag = "used_short" if video_format() == "short" else "used"
    fails_key = f"{flag}_fails"
    if not t or (uploaded and fails_key not in t):
        return
    if uploaded:
        del t[fails_key]
    else:
        t[fails_key] = t.get(fails_key, 0) + 1
        if t[fails_key] < MAX_TOPIC_TRIES:
            t[flag] = False
            print(f"[info] upload nahi hua — topic #{tid} agle slot me dobara "
                  f"({t[fails_key]}/{MAX_TOPIC_TRIES})")
        else:
            print(f"[warn] topic #{tid} {t[fails_key]} baar upload nahi hua — chhod raha hoon")
    bank_path.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    global STAGES
    print(f"[info] format: {video_format()}")
    if video_format() == "short":
        STAGES = SHORT_STAGES
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
        (RUN / "uploaded.txt").unlink(missing_ok=True)
        # YouTube is optional for the short: a missing/invalid refresh token
        # must not block the Instagram Reel cross-post.
        run("publish.py", "--only-if-ready")

        yt_uploaded = (RUN / "uploaded.txt").exists()
        ig_published = False

        # Same short → Instagram Reel (no re-render). Opt-in via IG_CROSSPOST=1.
        # Instagram can publish from final.mp4 even when YouTube upload failed.
        # Force is scoped to this pipeline call; direct standalone runs retain
        # the safer uploaded.txt check.
        if video_format() == "short":
            print("\n=== Instagram Reel (cross-post) ===")
            old_force = os.environ.get("IG_CROSSPOST_FORCE")
            os.environ["IG_CROSSPOST_FORCE"] = "1"
            try:
                run("publish_instagram.py")
            finally:
                if old_force is None:
                    os.environ.pop("IG_CROSSPOST_FORCE", None)
                else:
                    os.environ["IG_CROSSPOST_FORCE"] = old_force
            ig_published = (RUN / "ig_reel.txt").exists()

        # A topic is considered successfully published if either platform got it.
        settle_topic(yt_uploaded or ig_published)

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
