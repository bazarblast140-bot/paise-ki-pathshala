"""Stops retry, repair, and test runs from posting a public video or Reel."""
import os

# These modes may still render, and may upload unlisted for a human to watch.
# They must not publish publicly or schedule a later public flip.
BLOCKED_MODES = {"retry", "repair", "test", "qa"}


def pipeline_mode():
    return os.environ.get("PIPELINE_MODE", "").strip().lower()


def _approved_narration_run():
    """A locked dry-run script the owner approved is a real Short, not a test.

    TOPIC_KEY alone still means a test. PIPELINE_MODE retry/repair/test/qa
    and BOARD_QA still block even when this is set.
    """
    approved = os.environ.get("APPROVED", "").strip().lower() in ("1", "true", "yes", "on")
    return approved and bool(os.environ.get("NARRATION_FROM_RUN", "").strip())


def blocked_reason():
    """None when a normal scheduled slot may publish. Otherwise a short reason."""
    mode = pipeline_mode()
    if mode in BLOCKED_MODES:
        return f"mode={mode}"
    if os.environ.get("TOPIC_KEY", "").strip() and not _approved_narration_run():
        return "test topic (TOPIC_KEY)"
    if os.environ.get("BOARD_QA", "").strip().lower() in ("1", "true", "yes", "on"):
        return "qa render"
    return None


def clamp_visibility(visibility, publish_at=None):
    """Force unlisted and drop publishAt so a test/repair run cannot go public later."""
    reason = blocked_reason()
    if not reason:
        return visibility, publish_at
    if visibility == "public" or publish_at:
        print(f"[BLOCK] public upload refused ({reason}); visibility=unlisted, no schedule")
        return "unlisted", None
    return visibility, None
