"""Fail-closed publish verdict.

A public upload needs an explicit AI line that is only READY, a meta file
that says that verdict was actually parsed, and (for Shorts) a self-review
that cleared the bar. A missing model, a PASS fallback, an empty narration,
or a template line that lists both READY and NEEDS FIX is not a verdict.

A Short whose deterministic self-review is already at the high bar below is
not blocked when the AI's only notes are judgment calls (thumbnail, tone,
optional labels), and only when that narration is the gated LLM script.
Hard compliance, fact, and safety failures still block, including when that
self-review is perfect. A topic-card fallback blocks even at the high bar.
The pass bar in self_review.py (part 60 / overall 75) does not change.
"""
import json
import re

# Soft judgment may not block a Short that is already clean on the
# deterministic rubric. Schematic visuals score 90 when they pass, so 90 is
# that clean floor, not a discount. Accuracy must be 100: a number that
# failed the ledger check is a fact failure.
HIGH_BAR_OVERALL = 90
HIGH_BAR_PART = 90

_FAIL_MARKS = ("✗", "✘", "❌", "❎", "✖")
_UNVERIFIED = re.compile(r"UNVERIFIED", re.I)
_UNVERIFIED_NEGATED = re.compile(
    r"(?:no|not|nahi|nahin|zero|without)\s+(?:\w+\s+){0,3}UNVERIFIED"
    r"|UNVERIFIED\s+(?:\w+\s+){0,3}(?:nahi|nahin|not|none|zero)",
    re.I,
)
_UNSOURCED = re.compile(r"(source nahi|no source|unsourced|not sourced|without a source)", re.I)
_EXAMPLE = re.compile(r"(illustrative|example|misal)", re.I)
_STALE = re.compile(r"(\bmonths?\s+old\b|\byears?\s+old\b|\bstale\b|\boutdated\b|mahine\s+puran)", re.I)
_GUARANTEE = re.compile(r"guaranteed\s+(profit|returns?)", re.I)
_GUARANTEE_SOFT = re.compile(r"(check karo|jaisa na|na lage|\bnahi\b|\bnot\b|does not|don't)", re.I)
_ADVICE = re.compile(
    r"(you should (buy|sell|hold)|recommends? (buying|selling|holding)|"
    r"personalized advice|fake[- ]expert|buy this (stock|fund|scheme)|"
    r"sell this (stock|fund|scheme))",
    re.I,
)
_DISCLAIMER = re.compile(
    r"(no disclaimer|missing disclaimer|disclaimer (is )?(missing|absent)|disclaimer nahi)",
    re.I,
)
_SUBS = re.compile(
    r"(subtitles? (were |was )?(not generated|missing|absent)|no subtitles?|"
    r"\bsubs (missing|absent))",
    re.I,
)
_TAX_YEAR = re.compile(
    r"tax[^\n|]{0,80}(without|missing|no)\s+(a\s+)?(financial year|\bfy\b)"
    r"|tax[^\n|]{0,60}(financial year|\bfy\b)[^\n|]{0,40}(missing|not named|nahi)",
    re.I,
)
_TAX_CLEAR = re.compile(
    r"(zaroorat nahi|not needed|no tax|tax number nahi|koi tax number nahi)",
    re.I,
)

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


def _clauses(text):
    parts = []
    for line in (text or "").splitlines():
        parts.extend(piece.strip() for piece in line.split("|"))
    return [piece for piece in parts if piece]


def _auto_segments(text):
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if not re.search(r"auto-checks", line, re.I):
            continue
        blob = line
        if index + 1 < len(lines) and lines[index + 1].strip():
            blob += "\n" + lines[index + 1]
        return [piece.strip() for piece in blob.split("|") if piece.strip()]
    return []


def _check_name(segment):
    match = re.match(r"\s*([A-Za-z]+)", segment or "")
    return match.group(1).lower() if match else ""


def _is_fail(segment):
    return any(mark in (segment or "") for mark in _FAIL_MARKS)


def _only_empty_visual_checks(segment):
    """The model marks Visuals ✗ when the checks blob is empty.

    That is a missing input, not a broken frame. A real visual defect in the
    same segment still counts.
    """
    if not _check_name(segment).startswith("visual"):
        return False
    if not re.search(r"visual[_ ]checks", segment, re.I):
        return False
    if not re.search(r"(khaali|khali|empty|blank|\{\}|none|missing)", segment, re.I):
        return False
    if re.search(
        r"(missing (number|text|chart|source)|fabricat|wrong|not on screen|no chart|blank frame)",
        segment,
        re.I,
    ):
        return False
    return True


def _unverified_is_hard(clause):
    if not _UNVERIFIED.search(clause or ""):
        return False
    return _UNVERIFIED_NEGATED.search(clause or "") is None


def _unsourced_is_hard(clause):
    if not _UNSOURCED.search(clause or ""):
        return False
    # An illustrative example the ledger already owns is a label note.
    return _EXAMPLE.search(clause or "") is None


def _guarantee_is_hard(clause):
    if not _GUARANTEE.search(clause or ""):
        return False
    # "check it does not look like guaranteed profit" is a thumbnail note.
    return _GUARANTEE_SOFT.search(clause or "") is None


def _tax_year_is_hard(clause):
    if not re.search(r"\btax\b", clause or "", re.I):
        return False
    if _TAX_CLEAR.search(clause or ""):
        return False
    return _TAX_YEAR.search(clause or "") is not None


def hard_failure_reasons(text):
    """Compliance, fact, and safety findings. Empty means none were stated.

    Judgment-call wording is not a finding. A negated 'no UNVERIFIED figure'
    is not a finding either.
    """
    reasons = []
    seen = set()

    def add(reason):
        if reason and reason not in seen:
            seen.add(reason)
            reasons.append(reason)

    for segment in _auto_segments(text):
        if not _is_fail(segment) or _only_empty_visual_checks(segment):
            continue
        name = _check_name(segment) or "check"
        add(f"auto-check failed: {name}")

    for clause in _clauses(text):
        if _unverified_is_hard(clause):
            add("unverified figure")
        if _unsourced_is_hard(clause):
            add("unsourced figure")
        if _STALE.search(clause):
            add("stale or outdated figure")
        if _guarantee_is_hard(clause):
            add("guaranteed return or profit")
        if _ADVICE.search(clause):
            add("advice or fake-expert framing")
        if _DISCLAIMER.search(clause):
            add("disclaimer missing")
        if _SUBS.search(clause):
            add("subtitles missing")
        if _tax_year_is_hard(clause):
            add("tax figure without a financial year")

    if re.search(r"reused-content risk:\s*HIGH\b", text or "", re.I):
        add("reused-content risk HIGH")
    fails = re.search(r"FAILS this round[^:\n]*:\s*(.+)", text or "", re.I)
    if fails:
        rest = fails.group(1).strip()
        if rest and not re.fullmatch(r"(none|n/a|no|nahi|—|-|\.)", rest, re.I):
            add("fails this round: " + rest[:160])
    return reasons


def has_judgment_calls(text):
    """True when the report has at least one judgment-call bullet."""
    lines = (text or "").splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.search(r"tumhe dekhna hai|judgment calls", line, re.I):
            start = index + 1
            break
    if start is None:
        return False
    for line in lines[start:]:
        if re.search(r"risks / assumptions|fails this round|auto-checks|reused-content", line, re.I):
            break
        if re.match(r"\s*[-*•]\s+\S", line):
            return True
    return False


def soft_judgment_only(text):
    """True when the AI listed judgment calls and no hard failure."""
    if not (text or "").strip():
        return False
    if hard_failure_reasons(text):
        return False
    return has_judgment_calls(text)


def narration_is_fallback(self_review):
    """True when this review is a topic-card template, not a gated LLM script."""
    if not isinstance(self_review, dict):
        return False
    if self_review.get("narration_fallback") is True:
        return True
    for key in ("narration_source", "source"):
        if str(self_review.get(key) or "").strip().lower() == "fallback":
            return True
    return False


def narration_is_gated_llm(self_review):
    """True only for the LLM narration that passed the hook/teaching gate.

    board_short writes narration_source as llm or cache after that gate.
    A missing source is not that mark. A fallback flag wins even if the
    source was left as llm and the numeric scores are at the high bar.
    """
    if not isinstance(self_review, dict) or narration_is_fallback(self_review):
        return False
    source = str(
        self_review.get("narration_source") or self_review.get("source") or ""
    ).strip().lower()
    return source in ("llm", "cache")


def self_review_clears_high_bar(self_review):
    """True when a passing Short review is already clean, not merely above 75."""
    if narration_is_fallback(self_review):
        return False
    if not isinstance(self_review, dict) or not self_review.get("public_ok"):
        return False
    parts = self_review.get("parts")
    if not isinstance(parts, dict):
        return False
    try:
        overall = float(self_review.get("overall"))
    except (TypeError, ValueError):
        return False
    if overall < HIGH_BAR_OVERALL:
        return False
    for name in ("hook", "visual", "accuracy"):
        raw = parts.get(name)
        if raw is None:
            return False
        try:
            score = float(raw)
        except (TypeError, ValueError):
            return False
        if name == "accuracy" and score < 100:
            return False
        if score < HIGH_BAR_PART:
            return False
    trend = parts.get("trend")
    if trend is not None:
        try:
            trend_score = float(trend)
        except (TypeError, ValueError):
            return False
        if trend_score < HIGH_BAR_PART:
            return False
    return True


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def decide(ai_text, ai_ok, script, self_review, fmt="short"):
    """Return the publish decision. public_ready is the only path to a public upload.

    ai_ok is False when the model call failed or the text has no single verdict
    line. Deterministic notes never upgrade a missing AI verdict to public.

    A parsed NEEDS FIX still blocks, except one case: the text is judgment
    calls only, and a Short self-review is already at the high bar, and the
    narration is the gated LLM script. Hard compliance, fact, or safety
    findings block even then. A fallback flag blocks whatever the scores.
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
    if narration_is_fallback(self_review):
        review_ok = False
        reasons.append(
            "narration is a topic-card fallback, not an LLM script that passed the hook/teaching gate"
        )
    if fmt == "short":
        if not isinstance(self_review, dict) or not self_review.get("public_ok"):
            review_ok = False
            summary = (self_review or {}).get("summary") if isinstance(self_review, dict) else None
            reasons.append(summary or "self-review missing or below the bar")

    hard = hard_failure_reasons(text) if ai_valid and parsed == "NEEDS_FIX" else []
    if hard:
        reasons.extend(hard)
    judgment_only = bool(ai_valid and parsed == "NEEDS_FIX" and soft_judgment_only(text))
    high = fmt == "short" and self_review_clears_high_bar(self_review)
    soft_override = bool(
        judgment_only
        and high
        and review_ok
        and narration_is_gated_llm(self_review)
        and words >= 20
        and not hard
    )

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
            "soft_judgment_only": False,
        }
    if soft_override:
        return {
            "parsed": "READY",
            "model_verdict": "NEEDS_FIX",
            "ai_verdict_valid": True,
            "public_ready": True,
            "exit_code": 0,
            "verdict_line": "VERDICT: [READY TO PUBLISH]",
            "reasons": [],
            "spoken_words": words,
            "keep_model_report": False,
            "soft_judgment_only": True,
        }

    if parsed == "NEEDS_FIX" and not hard:
        if not high:
            reasons.append("AI NEEDS FIX stands: self-review is not at the high bar")
        elif not judgment_only:
            reasons.append("AI NEEDS FIX stands: review is not judgment-calls only")
        elif not narration_is_gated_llm(self_review):
            reasons.append("AI NEEDS FIX stands: narration is not the gated LLM script")

    if words < 20:
        verdict_line = "VERDICT: [NEEDS FIX — narration empty or too short]"
    elif not ai_valid:
        verdict_line = "VERDICT: [NEEDS FIX — AI reviewer unavailable]"
    elif not review_ok:
        verdict_line = "VERDICT: [NEEDS FIX — self-review below bar]"
    elif hard:
        verdict_line = "VERDICT: [NEEDS FIX — hard compliance, fact, or safety failure]"
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
        "soft_judgment_only": False,
    }


def files_allow_public(run_dir, fmt):
    """True only when the files on disk agree the video may be uploaded publicly."""
    if (run_dir / "narration_block.json").exists():
        return False
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
        if not review or not review.get("public_ok") or narration_is_fallback(review):
            return False
    return True
