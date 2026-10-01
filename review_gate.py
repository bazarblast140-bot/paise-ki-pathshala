"""Fail-closed publish verdict.

A public upload needs an explicit AI line that is only READY, a meta file
that says that verdict was actually parsed, and (for Shorts) a self-review
that cleared the bar. A missing model, a PASS fallback, an empty narration,
or a template line that lists both READY and NEEDS FIX is not a verdict.
"""
import json
import re

FALLBACK_MARKERS = (
    "passed via robust json fallback",
    "passed via offline",
    "no valid ai verdict",
    "ai_verdict_valid",
)


def parse_report_verdict(text):
    """'READY', 'NEEDS_FIX', or None when the line is missing or ambiguous."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("VERDICT"):
            continue
        up = stripped.upper()
        has_ready = "READY TO PUBLISH" in up
        has_fix = "NEEDS FIX" in up or "NOT READY" in up
        if has_ready and has_fix:
            return None
        if has_fix:
            return "NEEDS_FIX"
        if has_ready:
            return "READY"
        return None
    return None


def spoken_word_count(script):
    """Words before the fact ledger. Zero means the 25-Sep empty-narration case."""
    text = script or ""
    cut = text.upper().find("FACT LEDGER")
    body = text[:cut] if cut >= 0 else text
    body = re.sub(r"\[[^\]]*\]", " ", body)
    return len(body.split())


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def decide(ai_text, ai_ok, script, self_review, fmt="short"):
    """Return the publish decision. public_ready is the only path to a public upload.

    ai_ok is False when the model call failed or the text has no single verdict
    line. Deterministic notes are written into the report, but they never
    upgrade a missing AI verdict to public.
    """
    reasons = []
    text = ai_text or ""
    low = text.lower()
    fallback = any(mark in low for mark in FALLBACK_MARKERS) and "ready to publish" not in low
    parsed = parse_report_verdict(text) if ai_ok and not fallback else None
    ai_valid = bool(ai_ok and not fallback and parsed in ("READY", "NEEDS_FIX"))
    if not ai_valid:
        reasons.append("AI verdict missing or invalid")

    words = spoken_word_count(script or "")
    if words < 20:
        reasons.append(f"narration too short ({words} words)")

    review_ok = True
    if fmt == "short":
        if not isinstance(self_review, dict) or not self_review.get("public_ok"):
            review_ok = False
            summary = (self_review or {}).get("summary") if isinstance(self_review, dict) else None
            reasons.append(summary or "self-review missing or below the bar")

    public_ready = bool(ai_valid and parsed == "READY" and review_ok and words >= 20)
    if public_ready:
        return {
            "parsed": "READY",
            "ai_verdict_valid": True,
            "public_ready": True,
            "exit_code": 0,
            "verdict_line": "VERDICT: [READY TO PUBLISH]",
            "reasons": [],
            "spoken_words": words,
            "keep_model_report": True,
        }

    if words < 20:
        verdict_line = "VERDICT: [NEEDS FIX — narration empty or too short]"
    elif not ai_valid:
        verdict_line = "VERDICT: [NEEDS FIX — AI reviewer unavailable]"
    elif not review_ok:
        verdict_line = "VERDICT: [NEEDS FIX — self-review below bar]"
    else:
        verdict_line = "VERDICT: [NEEDS FIX — review blocked public upload]"
    return {
        "parsed": "NEEDS_FIX",
        "ai_verdict_valid": ai_valid,
        "public_ready": False,
        "exit_code": 2,
        "verdict_line": verdict_line,
        "reasons": reasons,
        "spoken_words": words,
        "keep_model_report": False,
    }


def files_allow_public(run_dir, fmt):
    """True only when the files on disk agree the video may be uploaded publicly."""
    report_path = run_dir / "report.md"
    meta_path = run_dir / "verify_meta.json"
    if not report_path.exists() or not meta_path.exists():
        return False
    report = report_path.read_text(encoding="utf-8")
    if parse_report_verdict(report) != "READY":
        return False
    meta = _load_json(meta_path)
    if not meta or not meta.get("ai_verdict_valid") or meta.get("verdict") != "READY":
        return False
    if not meta.get("public_ready"):
        return False
    if fmt == "short":
        review = _load_json(run_dir / "self_review.json")
        if not review or not review.get("public_ok"):
            return False
    return True
