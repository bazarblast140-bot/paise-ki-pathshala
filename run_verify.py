"""Stage 6: Final Verify Gate. Runs the verify agent over the finished pipeline outputs
and writes a Hinglish report (run/report.md) for Rajesh. This is the report you read.

A missing or invalid model verdict is NOT READY. The process exits 2 so the
slot is skipped and nothing is uploaded. Deterministic self-review can block
a video; it cannot by itself mark the video ready for a public upload.

A NEEDS FIX that is only judgment calls does not skip a Short whose
self-review is already at the high bar. Hard compliance, fact, and safety
findings still exit 2.
"""
import json
import hashlib
import re
import sys
from common import cfg, claude, read, save, ok, RUN, video_format, TruncatedResponse
import review_gate

FINAL_SYSTEM = """You are the Main Verify Agent doing the FINAL GATE on a finished faceless finance video, then writing a report for Rajesh in HINGLISH.

Re-check the three critical nodes one last time using the provided artifacts:
1. Facts: is every figure in the FACT LEDGER sourced (not UNVERIFIED)?
2. Compliance: no fake-expert persona, disclaimer present, educational (not advice) framing?
   The channel is Indian and NOT SEBI-registered. FAIL it if the script recommends buying,
   selling or holding any specific stock / fund scheme / bank product / insurance plan / app,
   promises or implies guaranteed returns, or gives a tax figure without naming the financial
   year. Tax and regulatory numbers not clearly sourced must be listed under Risks.
   EDUCATIONAL EXAMPLE PLANS: when <educational_example_plan> is true, the video teaches a
   pattern on an ILLUSTRATIVE teaching chart (example candles, no named stock) and is labelled
   on screen "शैक्षिक उदाहरण, स्टॉक टिप नहीं". Its entry / stop-loss / target / invalidation
   numbers are the lesson, described ("इस उदाहरण में एंट्री ... होगी"). Do NOT fail Compliance
   for that plan, and do not list it under "fails this round". Still FAIL Compliance if it orders
   the viewer to trade ("खरीदो", "लगाओ", "प्रॉफिट बुक करो"), names a real stock/fund with a
   buy/sell/hold, entry or target, or promises returns. When the flag is false, a specific
   buy/sell plan on a named stock is a non-SEBI call and FAILS.
3. Subtitles: were subtitles generated? (assume yes if a subs file was produced — stated below.)

Then surface JUDGMENT calls the human must decide (thumbnail appeal, "is this boring?", any assumption like an illustrative return rate).

Also score REUSED-CONTENT RISK from <reused_content_signals> — this is the YPP monetization / "inauthentic content" concern (the rule that terminated 16 channels in Jan 2026). Score LOW / MEDIUM / HIGH, don't guess:
- HIGH: stock_footage_pct high (>50) OR mostly-stock with robotic default TTS and little original.
- MEDIUM: some stock (roughly 25-50%) with robotic Edge TTS, but original script + charts present.
- LOW: mostly original (charts/cards), low stock, OR premium AI voice with original script + charts.
- Edge TTS (robotic) pushes risk UP one level vs a premium AI voice. If voice_engine is 'edge', say switching to ElevenLabs drops the risk a level.
- script_original and original_overlays_always_on being true are the mitigating factors — name them.

Write the report in this exact Hinglish format, answer-first, mobile-readable:

VERDICT: [READY TO PUBLISH] / [NEEDS FIX — X items]

✅ Auto-checks pass:
<one line: Hook | Facts | Compliance | Coverage | Visuals | Length>

♻️ Reused-content risk: <LOW/MEDIUM/HIGH>
- <one line: stock %, voice type, what's original, and the one lever to lower it>

⚠️ Tumhe dekhna hai (judgment calls):
- <thumbnail note>
- <any assumption/taste concern with timestamp>

🔴 Risks / assumptions:
- <claims marked Likely/Assuming, jurisdiction notes>

FAILS this round: <none | list>

Keep it short. No preamble before VERDICT."""

def reused_content_signals(c, shots):
    """Concrete, checkable inputs for the reused-content risk line in the report.
    The verify agent scores LOW/MEDIUM/HIGH from these — it does not guess."""
    shot_list = shots.get("shots") or []
    total = len(shot_list) or 1
    stock = sum(1 for s in shot_list if str(s.get("visual_type", "")).lower() == "stock_footage")
    charts = sum(1 for s in shot_list if str(s.get("visual_type", "")).lower() == "chart")
    used = RUN / "voice_engine.txt"          # board_short: jo engine asal me chala
    engine = (used.read_text(encoding="utf-8").strip() if used.exists()
              else str(c["voice"].get("engine", "edge"))).lower()
    return {
        "stock_footage_pct": round(stock / total * 100),
        "chart_shots": charts,
        "total_shots": len(shot_list),
        "voice_engine": engine,
        "voice_is_synthetic": True,          # both edge and elevenlabs are TTS
        "voice_quality": "robotic default TTS" if engine == "edge" else "premium AI voice",
        "script_original": True,             # Claude-written with a sourced fact ledger
        "original_overlays_always_on": True,  # typed headings, marker, watermark, intro
    }


def _self_review():
    path = RUN / "self_review.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(decision):
    (RUN / "verify_meta.json").write_text(json.dumps({
        "ai_verdict_valid": bool(decision["ai_verdict_valid"]),
        "verdict": "READY" if decision["public_ready"] else "NEEDS_FIX",
        "public_ready": bool(decision["public_ready"]),
        "soft_judgment_only": bool(decision.get("soft_judgment_only")),
        "reasons": decision.get("reasons") or [],
        "spoken_words": decision.get("spoken_words"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _blocked_report(decision, model_text):
    """A report that cannot be mistaken for READY. The model text is kept only as a note."""
    lines = [
        decision["verdict_line"],
        "",
        "Public upload blocked. The slot is skipped (this step exits non-zero).",
        "Nothing is uploaded as public, unlisted, or private from this failure.",
        "",
    ]
    if decision.get("reasons"):
        lines.append("Reasons:")
        lines.extend(f"- {r}" for r in decision["reasons"])
        lines.append("")
    review = _self_review() or {}
    if review.get("summary"):
        lines.append("Deterministic self-review: " + review["summary"])
    else:
        lines.append("Deterministic self-review: not available.")
    trend = (review.get("parts") or {}).get("trend")
    if trend is None:
        lines.append("Trend fit: skipped (data/trends.json absent or has no themes).")
    lines.append("")
    if decision.get("ai_verdict_valid"):
        lines.append("The AI verdict was read. It does not clear the publish gate.")
    else:
        lines.append("A missing or invalid AI verdict is not a pass.")
    if model_text and "READY TO PUBLISH" not in model_text.upper():
        lines.extend(["", "Model text (not used as the verdict):", model_text.strip()[:1200]])
    return "\n".join(lines).strip() + "\n"


def _judgment_bullets(model_text):
    """Judgment-call bullets only. The model's VERDICT line is not copied."""
    lines = (model_text or "").splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.search(r"tumhe dekhna hai|judgment calls", line, re.I):
            start = index + 1
            break
    if start is None:
        return []
    bullets = []
    for line in lines[start:]:
        if re.search(r"risks / assumptions|fails this round|auto-checks|reused-content", line, re.I):
            break
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT"):
            continue
        if re.match(r"[-*•]\s+\S", stripped):
            bullets.append(stripped if stripped.startswith("-") else "- " + stripped.lstrip("-*• ").strip())
        if len(bullets) >= 8:
            break
    return bullets


def _soft_ready_report(model_text, review):
    """A READY report. The model's NEEDS FIX line is not the publish verdict."""
    summary = ""
    if isinstance(review, dict) and review.get("summary"):
        summary = " (" + review["summary"] + ")"
    lines = [
        "VERDICT: [READY TO PUBLISH]",
        "",
        "Deterministic self-review cleared the high bar" + summary + ".",
        "The reviewer listed judgment calls only (thumbnail, tone, optional labels).",
        "No hard compliance, fact, or safety failure was found, so the slot is not skipped.",
        "",
    ]
    bullets = _judgment_bullets(model_text)
    if bullets:
        lines.append("Judgment calls kept for Rajesh:")
        lines.extend(bullets)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _report_from_decision(decision, model_text, review):
    """The file that publish reads. A soft pass must not start with NEEDS FIX."""
    if decision.get("public_ready") and decision.get("keep_model_report"):
        text = (model_text or "").strip()
        if text and review_gate.parse_report_verdict(text) == "READY":
            return text + ("" if text.endswith("\n") else "\n")
    if decision.get("public_ready") and decision.get("soft_judgment_only"):
        return _soft_ready_report(model_text, review)
    if decision.get("public_ready"):
        line = (decision.get("verdict_line") or "").strip()
        if review_gate.parse_report_verdict(line) == "READY":
            return line + "\n"
    return _blocked_report(decision, model_text or "")


def _finish(report, decision, cache_file, cache_key, model_text):
    save("report.md", report)
    _write_meta(decision)
    cache_file.write_text(json.dumps({
        "key": cache_key,
        "report": model_text or report,
        "ai_verdict_valid": bool(decision["ai_verdict_valid"] and decision["public_ready"]),
        "verdict": "READY" if decision["public_ready"] else "NEEDS_FIX",
    }, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 50 + "\n" + report + "\n" + "=" * 50)
    if decision["exit_code"]:
        print("[HOLD] not READY for public upload — skipping the slot. "
              + "; ".join(decision.get("reasons") or ["review blocked"]))
        sys.exit(decision["exit_code"])
    ok("wrote run/report.md")


def review_budgets(c):
    """Output tokens for the verify call, then one larger retry. Thinking stays off."""
    models = (c or {}).get("models") or {}
    first = int(models.get("verify_max_tokens") or models.get("max_tokens") or 16000)
    second = int(models.get("verify_retry_max_tokens") or first * 2)
    if second <= first:
        second = first * 2
    return first, second


def fetch_review(c, system, user):
    """Ask the reviewer. A truncated reply is not a verdict; retry once, larger.

    Thinking is disabled inside claude() for DeepSeek, so the budget is for
    the report itself. finish_reason=length raises TruncatedResponse and the
    partial text is thrown away.
    """
    first, second = review_budgets(c)
    model = (c.get("models") or {}).get("verify_model")
    try:
        return claude(system, user, model=model, max_tokens=first, raise_on_truncate=True)
    except TruncatedResponse:
        print(f"[retry] reviewer response truncated (finish_reason=length, max_tokens={first}); "
              f"retrying once at {second}. The partial text is not a verdict.")
        return claude(system, user, model=model, max_tokens=second, raise_on_truncate=True)


def _topic():
    try:
        return json.loads((RUN / "topic.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    c = cfg()
    script = read("script.txt")
    shots = json.loads(read("shots.json"))
    fmt = video_format()
    review = _self_review()
    cache_key = hashlib.sha256((script + json.dumps(shots, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()
    cache_file = RUN / "verify_cache.json"
    if cache_file.exists() and (RUN / "report.md").exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("key") == cache_key and cached.get("report") and cached.get("ai_verdict_valid"):
                decision = review_gate.decide(cached["report"], True, script, review, fmt)
                if decision["public_ready"]:
                    report = _report_from_decision(decision, cached["report"], review)
                    _finish(report, decision, cache_file, cache_key, cached["report"])
                    ok("verify cache reused — no LLM credit")
                    return
        except SystemExit:
            raise
        except Exception:
            pass
    subs_made = (RUN / "subs.ass").exists()
    video_made = (RUN / "final.mp4").exists()
    signals = reused_content_signals(c, shots)
    edu_plan = review_gate.educational_example_plan(_topic(), script)

    user = f"""<artifacts>
<script_with_fact_ledger>
{script}
</script_with_fact_ledger>
<visual_checks>{json.dumps(shots.get('checks', {}))}</visual_checks>
<reused_content_signals>{json.dumps(signals)}</reused_content_signals>
<educational_example_plan>{edu_plan}</educational_example_plan>
<subtitles_generated>{subs_made}</subtitles_generated>
<video_built>{video_made}</video_built>
</artifacts>
Write the final Hinglish report. The first line must be exactly one of:
VERDICT: [READY TO PUBLISH]
VERDICT: [NEEDS FIX — <short reason>]
Do not print both outcomes on the same line."""

    ai_text = ""
    ai_ok = False
    try:
        ai_text = fetch_review(c, FINAL_SYSTEM, user)
        ai_ok = bool((ai_text or "").strip())
    except TruncatedResponse as exc:
        # Second attempt was cut off too. Do not parse the partial NEEDS FIX.
        print(f"[HOLD] reviewer response truncated again (finish_reason=length, "
              f"max_tokens={exc.max_tokens}); not using the partial text as a verdict")
        ai_text = ""
        ai_ok = False
    except SystemExit as exc:
        print(f"[HOLD] AI reviewer stopped before a verdict: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[HOLD] AI reviewer failed: {type(exc).__name__}: {exc}")

    decision = review_gate.decide(ai_text, ai_ok, script, review, fmt)
    model_for_report = ai_text if ai_ok else ""
    report = _report_from_decision(decision, model_for_report, review)
    _finish(report, decision, cache_file, cache_key, ai_text if decision["public_ready"] else "")

if __name__ == "__main__":
    main()
