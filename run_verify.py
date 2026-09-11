"""Stage 6: Final Verify Gate. Runs the verify agent over the finished pipeline outputs
and writes a Hinglish report (run/report.md) for Rajesh. This is the report you read."""
import json
from common import cfg, claude, read, save, ok, RUN

FINAL_SYSTEM = """You are the Main Verify Agent doing the FINAL GATE on a finished faceless finance video, then writing a report for Rajesh in HINGLISH.

Re-check the three critical nodes one last time using the provided artifacts:
1. Facts: is every figure in the FACT LEDGER sourced (not UNVERIFIED)?
2. Compliance: no fake-expert persona, disclaimer present, educational (not advice) framing?
   The channel is Indian and NOT SEBI-registered. FAIL it if the script recommends buying,
   selling or holding any specific stock / fund scheme / bank product / insurance plan / app,
   promises or implies guaranteed returns, or gives a tax figure without naming the financial
   year. Tax and regulatory numbers not clearly sourced must be listed under Risks.
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
    engine = str(c["voice"].get("engine", "edge")).lower()
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


def main():
    c = cfg()
    script = read("script.txt")
    shots = json.loads(read("shots.json"))
    subs_made = (RUN / "subs.ass").exists()
    video_made = (RUN / "final.mp4").exists()
    signals = reused_content_signals(c, shots)

    user = f"""<artifacts>
<script_with_fact_ledger>
{script}
</script_with_fact_ledger>
<visual_checks>{json.dumps(shots.get('checks', {}))}</visual_checks>
<reused_content_signals>{json.dumps(signals)}</reused_content_signals>
<subtitles_generated>{subs_made}</subtitles_generated>
<video_built>{video_made}</video_built>
</artifacts>
Write the final Hinglish report."""

    report = claude(FINAL_SYSTEM, user, model=c["models"]["verify_model"], max_tokens=c["models"]["max_tokens"])
    save("report.md", report)
    print("\n" + "=" * 50 + "\n" + report + "\n" + "=" * 50)
    ok("wrote run/report.md")

if __name__ == "__main__":
    main()
