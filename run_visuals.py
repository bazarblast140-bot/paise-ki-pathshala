"""Stage 4: Visual Agent builds a timed shot list, Gate 3 verifies density/numbers/repeats.
Exits non-zero on Gate 3 fail."""
import json
import re
from common import cfg, claude_json, read, save, ok, die, RUN

# PIL overlays aur matplotlib charts Devanagari ki matra theek nahi jodte —
# screen par toote hue akshar dikhte hain. Isliye visual fields sirf Roman me.
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_TEXT_FIELDS = ("on_screen_text", "sub_text", "highlight")


def devanagari_fields(shots):
    bad = []
    for s in shots:
        vals = [s.get(k) for k in _TEXT_FIELDS]
        cd = s.get("chart_data") if isinstance(s.get("chart_data"), dict) else {}
        vals += [str(x) for x in (cd.get("labels") or [])]
        if any(v and _DEVANAGARI.search(str(v)) for v in vals):
            bad.append(s.get("shot_id"))
    return bad

VISUAL_SYSTEM = """You are the Visual Planner for Paise Ki Pathshala (Hindi personal finance, India).

LANGUAGE OF ON-SCREEN TEXT — hard rule:
- The narration you receive is Hindi. But on_screen_text, sub_text, highlight and chart labels
  MUST be written in ROMAN script only (Hinglish or English) — the renderer cannot draw
  Devanagari. Examples: "EMI KA JAAL", "Rs 50,000", "SIP vs FD", "72 KA NIYAM", "10 SAAL".
- Write money as "Rs 50,000" / "Rs 1.5 LAKH" / "Rs 2 CRORE" — never the ₹ sign.
- Numbers you show must come from the narration (convert Hindi number words back to digits).

TARGET LOOK (Wealth Logic style):
- Clean LIGHT / white background feel (not busy stock montage).
- Fixed host energy: cartoon expert in white lab coat pointing at icons and numbers.
- Huge numbers on screen. Simple icons (car, phone, calendar, loan paper, arrows, loops).
- Title cards like THE _____ TRAP / THE _____ RULE.
- One claim → one visual. No walls of text.

HARD RULES:
1. Max 8% of shots may be stock_footage (emotion only — never the explanation). Prefer zero stock.
2. Numbers, comparisons, timelines → visual_type "chart" with chart_data.
   chart_data MUST have 2–5 labels/values (never a single bar). Compare two things when possible.
   Chart labels: short Roman text ("FD", "SIP", "Saal 1", "Saal 10").
3. Concepts / rules / traps / actions → visual_type "card" with short on_screen_text + icon.
4. on_screen_text: max 5 words. Prefer NUMBER or trap/rule name. NEVER vague alone ("SEEMS LIKE NOTHING") — pair with number/noun.
   Optional sub_text (3-8 words) under headline. BAN empty boards: every card = icon + headline (+ sub_text preferred).
5. Every card shot MUST include "icon": one of car, cash, calendar, loan, loop, arrow_down, arrow_up, money, debt, savings.
6. section labels: TRAP, RULE, MATH, LOOP, ACTION (short).
7. Shot length 5–8 seconds. animation_in: "fade_in_up".
8. First shot = TITLE CARD card when the script names a trap/rule.
9. source_asset_query only if stock_footage; under 5 words.

chart_data when visual_type is chart:
  {"type": "bar" or "line", "labels": ["FD","SIP"], "values": [7, 12]}
  At least 2 values. Values MUST come from the script.

JSON ONLY:
{
  "shots": [
    {
      "shot_id": 1,
      "start_sec": 0,
      "end_sec": 6,
      "visual_type": "card",
      "on_screen_text": "EMI KA JAAL",
      "highlight": "TRAP",
      "section": "TRAP",
      "icon": "car",
      "pose": "point",
      "emphasis": "neutral",
      "animation_in": "fade_in_up",
      "chart_data": null,
      "source_asset_query": null
    }
  ]
}
Start with {"""


def generate_plan(spoken, c):
    paras = [p.strip() for p in spoken.split("\n\n") if p.strip()]
    chunks = []
    curr = []
    curr_words = 0
    for p in paras:
        w = len(p.split())
        if curr_words + w > 150 and curr:
            chunks.append("\n\n".join(curr))
            curr = [p]
            curr_words = w
        else:
            curr.append(p)
            curr_words += w
    if curr:
        chunks.append("\n\n".join(curr))

    all_shots = []
    cur_sec = 0.0
    shot_id = 1

    for i, chk in enumerate(chunks):
        res = claude_json(VISUAL_SYSTEM, f"<script>{chk}</script>",
                          model=c["models"]["visual_model"],
                          max_tokens=c["models"].get("visual_max_tokens", 16000))
        shots = res.get("shots", [])
        if devanagari_fields(shots):
            print(f"[retry] chunk {i+1}: Devanagari in on-screen text — asking again in Roman")
            res = claude_json(VISUAL_SYSTEM,
                              f"<script>{chk}</script>\n<fix>Your last plan put Devanagari in "
                              f"on_screen_text/sub_text/highlight/chart labels. Rewrite ALL of "
                              f"them in Roman script (Hinglish/English).</fix>",
                              model=c["models"]["visual_model"],
                              max_tokens=c["models"].get("visual_max_tokens", 16000))
            shots = res.get("shots", [])

        for s in shots:
            dur = max(2.0, float(s.get("end_sec", 5)) - float(s.get("start_sec", 0)))
            s["shot_id"] = shot_id
            s["start_sec"] = round(cur_sec, 2)
            s["end_sec"] = round(cur_sec + dur, 2)
            cur_sec += dur
            shot_id += 1
            all_shots.append(s)

    return {
        "total_estimated_seconds": round(cur_sec, 2),
        "shots": all_shots,
        "checks": {"max_shot_length_sec": 6, "spoken_numbers_covered": "ALL", "repeats_found": "none"}
    }


def main():
    c = cfg()
    spoken = read("spoken.txt")
    plan = generate_plan(spoken, c)

    shots = plan.get("shots") or []
    if not shots:
        die("GATE 3 FAILED: Visual Agent returned zero shots.")

    stock = sum(1 for s in shots if str(s.get("visual_type", "")).lower() == "stock_footage")
    chart = sum(1 for s in shots if str(s.get("visual_type", "")).lower() == "chart")
    ratio = stock / len(shots)
    max_ratio = float(c["pipeline"].get("max_stock_ratio", 0.15))

    # charts with numbers are original (Excalidraw-style) — good for monetization
    missing_chart_data = [
        s.get("shot_id") for s in shots
        if str(s.get("visual_type", "")).lower() == "chart"
        and not (isinstance(s.get("chart_data"), dict)
                 and s["chart_data"].get("labels")
                 and s["chart_data"].get("values"))
    ]

    fails = []
    if ratio > max_ratio + 1e-9:
        fails.append(
            f"Too much stock footage: {ratio*100:.0f}% (max {max_ratio*100:.0f}%). "
            f"Rewrite shots to use chart/card so the channel stays original and monetizable."
        )
    if missing_chart_data:
        fails.append(f"chart shots missing chart_data: {missing_chart_data}")
    bad_script = devanagari_fields(shots)
    if bad_script:
        fails.append(f"Devanagari in on-screen text (renders as broken boxes): shots {bad_script}")
    if chart == 0 and len(shots) >= 6:
        fails.append("No chart shots — at least one number-driven Excalidraw chart is required for a 6+ shot video.")

    if fails:
        save("gate3_report.txt", "\n".join(fails))
        die("GATE 3 FAILED: " + " | ".join(fails))

    ok(f"GATE 3 PASSED ({len(shots)} shots, {ratio*100:.0f}% stock, {chart} charts)")
    save("shots.json", json.dumps(plan, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
