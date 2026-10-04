"""Hold an automatic public upload until Approve QA schedules it.

REQUIRE_QA_APPROVAL defaults to true when it is unset. The hold applies only
when AUTOMATIC_UPLOAD is set, which the main crons, Slot watch, and the buffer
fill steps set. A manual Publish an artifact run sets EXPLICIT_PRIVACY and is
left exactly as the dispatcher chose. Unlisted previews are not rewritten.
"""
import os
from pathlib import Path


def _flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def approval_required():
    """True unless REQUIRE_QA_APPROVAL is explicitly off. Unset means on."""
    raw = os.environ.get("REQUIRE_QA_APPROVAL")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def hold_active():
    """This upload must stay private, with no publishAt and no Reel."""
    if _flag("EXPLICIT_PRIVACY"):
        return False
    if not _flag("AUTOMATIC_UPLOAD"):
        return False
    return approval_required()


def auto_gate_active():
    """Automatic upload that publishes only after every automated gate passes.

    REQUIRE_QA_APPROVAL must be explicitly false. Unset still means the human
    hold. A manual Publish an artifact run sets EXPLICIT_PRIVACY and skips both.
    """
    if _flag("EXPLICIT_PRIVACY"):
        return False
    if not _flag("AUTOMATIC_UPLOAD"):
        return False
    return not approval_required()


def apply_hold(visibility, publish_at):
    """Return (visibility, publish_at, held).

    A public upload, including one already turned into private + publishAt,
    becomes private with no schedule. Unlisted stays unlisted.
    """
    if not hold_active():
        return visibility, publish_at, False
    vis = (visibility or "").strip().lower()
    if vis in ("public", "now") or (vis == "private" and publish_at):
        return "private", None, True
    return visibility, publish_at, False


def mark_run(run_dir):
    path = Path(run_dir) / "qa_hold.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("held\n", encoding="utf-8")
    return path


def run_is_held(run_dir):
    return (Path(run_dir) / "qa_hold.txt").is_file()
