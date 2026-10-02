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
    ("Pre-publish Quality Gate", "quality_check.py"),
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
    ("Pre-publish Quality Gate", "quality_check.py"),
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


def resolve_publish_status(yt_rc, yt_uploaded, ig_published, ig_required, verdict_ready):
    """Final pipeline status after the opt-in publish step.

    A YouTube upload failure (invalid_grant is exit code 3) fails the job even
    when an Instagram Reel was confirmed. A missing READY verdict with nothing
    published also fails the job. A YouTube upload whose Reel was not confirmed
    is success with a warning: the slot is filled, and the Reel is retried later.
    """
    if yt_rc:
        if yt_rc == 3:
            return ("publish failed: YouTube invalid_grant (token rejected)",
                    "[STOP] YouTube upload failed: invalid_grant. Job will exit non-zero.")
        return (f"publish failed: YouTube upload rc={yt_rc}",
                "[STOP] YouTube upload failed. Job will exit non-zero.")
    published = bool(yt_uploaded or ig_published)
    if not published and not verdict_ready:
        return ("not published: verify verdict is not READY TO PUBLISH",
                "[STOP] Verify verdict is not READY — nothing was published (YouTube or Instagram).")
    if ig_required and not ig_published:
        if yt_uploaded:
            return (
                "success",
                "[warn] Instagram Reel was not confirmed after the YouTube upload. "
                "The slot stays filled. The Reel is retried from run/ig_status.txt.",
            )
        return ("publish failed: Instagram Reel not confirmed",
                "[STOP] Instagram Reel publish was not confirmed; YouTube status is independent.")
    if not published:
        return ("publish failed: YouTube and Instagram both failed",
                "[STOP] No platform confirmed a publish; topic was not marked used.")
    return ("success", None)


def _youtube_video_id():
    path = RUN / "uploaded.txt"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    token = raw.strip().split()
    if not token:
        return ""
    video_id = token[0]
    if video_id.lower().startswith("http"):
        video_id = video_id.rstrip("/").split("/")[-1]
    return video_id


def finish_publish(yt_rc, yt_uploaded, ig_published, ig_required, verdict_ready, ig_rc=0):
    """Resolve the publish step and, when only the Reel missed, leave a retry marker.

    ``run/ig_status.txt`` is ``skipped: <reason>``. The existing YouTube ledger
    row gets an ``ig_skipped`` note. The process then exits 0 because the status
    is success. YouTube failures still return a non-success status.
    """
    if yt_uploaded and ig_required and not ig_published and not yt_rc:
        import publish_instagram
        publish_instagram.mark_ig_skipped(
            RUN, _youtube_video_id(), f"Instagram publish rc={ig_rc}")
    return resolve_publish_status(
        yt_rc, yt_uploaded, ig_published, ig_required, verdict_ready)


def _finish_credits():
    try:
        import credit_log
        credit_log.write_summary()
    except Exception as exc:  # noqa: BLE001 — the summary must not hide the real exit
        print(f"[credits] summary skipped: {exc}")


def main():
    global STAGES
    import slot_guard
    reason = slot_guard.scheduled_slot_block()
    if reason:
        print(f"[skip] {reason}; exiting before paid APIs")
        _finish_credits()
        sys.exit(0)
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
    if status == "success" and "--publish" in sys.argv:
        # Second ledger read. The opening guard ran before paid APIs; this
        # one runs after the build and before YouTube or Instagram.
        reason = slot_guard.scheduled_slot_block()
        if reason:
            print(f"[skip] {reason}; ledger re-read before upload, not publishing")
            _finish_credits()
            sys.exit(0)
        print("\n=== Publish (opt-in) ===")
        (RUN / "uploaded.txt").unlink(missing_ok=True)
        (RUN / "youtube_status.txt").unlink(missing_ok=True)
        # YouTube is optional for the short: a missing/invalid refresh token
        # must not block the Instagram Reel cross-post.
        yt_rc = run("publish.py", "--only-if-ready")
        if yt_rc != 0:
            print(f"[WARN] YouTube publish failed (rc={yt_rc}); continuing to Instagram.")
            if yt_rc == 3:   # yt_credentials.EXIT_TOKEN_DEAD
                note = "YouTube TOKEN EXPIRED/REVOKED — README: 'YouTube token permanent kaise karein'"
                if os.environ.get("SUPPRESS_GHA_COMMANDS") != "1":
                    print("::error title=YouTube token expired or revoked::Regenerate YT_FIN_REFRESH_TOKEN "
                          "(README: 'YouTube token permanent kaise karein').")
            else:
                note = f"YouTube upload nahi hua (publish.py rc={yt_rc})"
            (RUN / "youtube_status.txt").write_text(note + "\n", encoding="utf-8")

        yt_uploaded = (RUN / "uploaded.txt").exists()
        ig_published = False
        ig_rc = 0

        # Same short → Instagram Reel (no re-render). Opt-in via IG_CROSSPOST=1.
        # Instagram can publish from final.mp4 even when YouTube upload failed.
        # Force is scoped to this pipeline call; direct standalone runs retain
        # the safer uploaded.txt check.
        if video_format() == "short":
            print("\n=== Instagram Reel (cross-post) ===")
            old_force = os.environ.get("IG_CROSSPOST_FORCE")
            os.environ["IG_CROSSPOST_FORCE"] = "1"
            try:
                ig_rc = run("publish_instagram.py")
            finally:
                if old_force is None:
                    os.environ.pop("IG_CROSSPOST_FORCE", None)
                else:
                    os.environ["IG_CROSSPOST_FORCE"] = old_force
            ig_published = (RUN / "ig_reel.txt").exists()
            if ig_rc != 0:
                print(f"[WARN] Instagram publish failed (rc={ig_rc}).")

        # The YouTube upload fills the slot. A missed Reel is a warning plus
        # run/ig_status.txt, not a failed Short: the repair workflow posts it
        # later. A YouTube failure still fails the job, even if a Reel went up.
        from publish_guard import blocked_reason
        import review_gate
        ig_required = (
            video_format() == "short"
            and os.environ.get("IG_CROSSPOST", "").strip().lower() in ("1", "true", "yes", "on")
            and not blocked_reason()
        )
        published = yt_uploaded or ig_published
        settle_topic(published)

        # Instagram-only publish (YouTube failed) still fills the slot in the
        # ledger, so Slot watch doesn't start a second "fill" run that would post
        # another Reel (and still fail on YouTube). The job itself still fails
        # when YouTube upload failed, including invalid_grant.
        if ig_published and not yt_uploaded:
            try:
                from eleven_voice import ledger_voice_kwargs
                from ledger import record_publish
                from slot_guard import target_date
                slot = os.environ.get("PUBLISH_SLOT_IST", "").strip()
                note = ""
                status_path = RUN / "youtube_status.txt"
                if status_path.exists():
                    note = status_path.read_text(encoding="utf-8")
                import credit_log
                record_publish(None, None, slot or None, platform="instagram",
                               date_ist=target_date(slot) if slot else None, note=note or None,
                               **ledger_voice_kwargs(RUN), credits=credit_log.ledger_fields())
            except Exception as e:  # noqa: BLE001
                print(f"[warn] ledger IG-only entry skipped: {e}")

        verdict_ready = review_gate.files_allow_public(RUN, video_format())
        status, stop = finish_publish(
            yt_rc, yt_uploaded, ig_published, ig_required, verdict_ready, ig_rc)
        if stop:
            print(stop)

    # always notify
    print("\n=== Notify ===")
    run("notify.py", "--status", status)
    _finish_credits()

    # Propagate stage failures to CI (and cron callers).  Previously the
    # notification step ran and the process exited 0 even when a stage had
    # failed, making GitHub Actions report a misleading success.
    if status != "success":
        sys.exit(1)

if __name__ == "__main__":
    main()
