"""Stage 2: Script Agent writes the script, local Gate 1 verifies structure.
Gate 1 is now a fast local Python check (no LLM call) — saves credits.
Max 2 attempts: generate → local check → (if fail) regenerate with fix notes → done."""
import re
from common import cfg, claude, read, save, ok, die

SCRIPT_SYSTEM = """You are the Script Agent for "Paise Ki Pathshala" — a HINDI personal-finance
education channel for Indian viewers (18-40, salaried, students, small business owners).

LANGUAGE:
- Write the spoken script in natural, simple HINDI (Devanagari), the way a friendly teacher
  talks in India. Common English money words stay as Indians say them, in Roman script:
  SIP, EMI, FD, PPF, NPS, UPI, CIBIL, credit card, loan, mutual fund, index fund.
- Money in rupees with the Indian system (लाख / करोड़), e.g. ₹50,000, ₹1.5 लाख, ₹2 करोड़.
- Keep the section HEADINGS and tags below exactly in English (TITLE, HOOK, BODY, [SEGMENT n: ...],
  [SOURCE: ...], CTA:, DISCLAIMER LINE:, FACT LEDGER:) — software parses them.

VISUAL STYLE TO WRITE FOR:
- One fixed cartoon host (teacher in a white lab coat) points at icons, numbers and charts.
- Big on-screen numbers. One idea per 5–8 second beat. Name the trap/rule in a title card.

Your job: make ONE money rule or trap so clear that a busy person gets it in under 5 minutes.

STRUCTURE (follow exactly):
TITLE:
<Searchable Hindi + key English words, max 80 characters. Prefer "___ का जाल" / "___ का नियम"
 + a concrete number. Example: "Credit Card का Minimum Due जाल — ₹50,000 कैसे बनते हैं ₹1 लाख">

HOOK (0-12 sec):
<Pattern-interrupt in Hindi. First sentence stops the scroll. No greeting, no throat-clearing.>

BODY:
[SEGMENT 1: TITLE CARD]
<Name the trap/rule in one sharp spoken line.>

[SEGMENT 2: THE HARD NUMBER]
<ONE concrete Indian example with a real number and [SOURCE: ...].>

[SEGMENT 3: HOW THE TRAP WORKS]
<Mechanism in plain Hindi. Walk a simple timeline (पहला साल → तीसरा साल → ...). Short sentences.>

[SEGMENT 4: WHAT MOST PEOPLE ACTUALLY DO]
<The common mistake. One vivid Indian picture (सैलरी, EMI, त्योहार का खर्च). Calm, not preachy.>

[SEGMENT 5: THE ONE ACTION]
<Exactly one thing to do this week. Specific.>

CTA:
<One calm Hindi line, e.g. "अगर ये नियम काम का लगा, तो अगला वीडियो इसी का दूसरा पहलू है।">

DISCLAIMER LINE:
<One spoken Hindi sentence: यह वीडियो सिर्फ़ जानकारी के लिए है, निवेश सलाह नहीं।>

FACT LEDGER:
<- each figure → source>

HARD RULES:
1. YMYL: at least THREE [SOURCE: ...] tags inside the BODY. Prefer Indian primary sources:
   RBI, SEBI, AMFI, Income Tax Department, IRDAI, NPCI, PFRDA, EPFO, PIB, MOSPI (CPI data).
2. FACT LEDGER lists every figure with its source.
3. SEBI finfluencer rules — the channel is NOT a registered adviser:
   - NEVER recommend buying/selling/holding any specific stock, mutual fund scheme, bank
     product, insurance plan, app or crypto. Categories are fine ("index fund", "term insurance").
   - NEVER promise or imply guaranteed returns. Any return rate is illustrative: say
     "मान लीजिए 12% सालाना" and keep it realistic.
   - No "tips", no "ye share le lo", no price targets.
4. Tax and regulation change every year: name the financial year for any tax figure
   (e.g. "FY 2025-26 के नियम से") and add "अपने case के लिए ताज़ा नियम ज़रूर देखें".
   If you are not sure a number is current, tag it [UNVERIFIED] instead of guessing.
5. Must include sections exactly named: CTA:  and  DISCLAIMER LINE:  and  FACT LEDGER:
6. Spoken length: 550–750 words (about 5 minutes). Dense, not padded.
7. Forbidden filler: "आज के इस वीडियो में", "दोस्तों स्वागत है", "बिना समय गंवाए",
   "तो चलिए शुरू करते हैं", "वीडियो को अंत तक देखें", "in this video", "let's dive in".
8. Narrator = calm teacher, not hype. Short sentences. One idea per sentence.
9. Prefer one vivid Indian example over three vague tips. No emojis.

Output plain text only — no markdown fences."""


def gate1_local(script, c):
    """Local Gate 1: checks script structure without any LLM call.
    Returns (pass: bool, fails: list[str])."""
    fails = []
    upper = script.upper()

    # Structure checks
    if "TITLE" not in upper[:200]:
        fails.append("Missing TITLE section at the start")

    if "HOOK" not in upper[:500]:
        fails.append("Missing HOOK section")

    segments = re.findall(r"\[SEGMENT\s+\d+", script, re.IGNORECASE)
    if len(segments) < 3:
        fails.append(f"Only {len(segments)} [SEGMENT] tags found (need at least 3)")

    sources = re.findall(r"\[SOURCE:", script, re.IGNORECASE)
    if len(sources) < 3:
        fails.append(f"Only {len(sources)} [SOURCE:] tags found (need at least 3)")

    if "CTA" not in upper:
        fails.append("Missing CTA section")

    if "DISCLAIMER" not in upper:
        fails.append("Missing DISCLAIMER LINE")

    if "FACT LEDGER" not in upper:
        fails.append("Missing FACT LEDGER section")

    # Word count check (approximate spoken words = everything before FACT LEDGER)
    body_end = script.upper().find("FACT LEDGER")
    spoken_part = script[:body_end] if body_end > 0 else script
    # Strip [SOURCE:...] and [SEGMENT...] tags for word count
    spoken_clean = re.sub(r"\[SOURCE:[^\]]*\]", "", spoken_part)
    spoken_clean = re.sub(r"\[SEGMENT[^\]]*\]", "", spoken_clean)
    spoken_clean = re.sub(r"\[UNVERIFIED[^\]]*\]", "", spoken_clean)
    words = len(spoken_clean.split())

    target = c["video"]["target_word_count"]
    tol = c["pipeline"].get("word_count_tolerance", 0.4)
    lo, hi = int(target * (1 - tol)), int(target * (1 + tol))
    if words < lo:
        fails.append(f"Script too short: ~{words} words (minimum {lo})")
    elif words > hi:
        fails.append(f"Script too long: ~{words} words (maximum {hi})")

    return len(fails) == 0, fails


def main():
    c = cfg()
    topic = read("topic.txt").strip()
    wc = c["video"]["target_word_count"]
    mins = c["video"]["target_minutes"]

    prompt = f"""<task>Write a One Rule Board script in HINDI: one rule, clear mechanism, one action. Explain — do not pad.</task>
<context>Channel: Paise Ki Pathshala. Audience: Indian beginners, Hindi. Target ~{mins} min ({wc} spoken words ±25%). Calm teacher voice. No filler phrases. SEBI-safe: education only, no product recommendations, no guaranteed returns.</context>
<topic>{topic}</topic>
<must_include>
- At least 3 separate [SOURCE: ...] tags inside the spoken BODY
- Sections: CTA: / DISCLAIMER LINE: / FACT LEDGER:
- At least 3 [SEGMENT n: ...] blocks
- Spoken body at least {int(wc*0.75)} words before FACT LEDGER
</must_include>
<output_format>TITLE / HOOK (0-12 sec) / BODY with [SEGMENT 1-5] as defined in system / CTA / DISCLAIMER LINE / FACT LEDGER</output_format>"""

    last_fails = []
    model = c["models"]["script_model"]
    max_tok = c["models"].get("script_max_tokens", 4000)
    print(f"[info] Script Agent: model={model}, max_tokens={max_tok}, topic='{topic}'")

    for attempt in range(3):  # max 3 attempts
        user = prompt
        if last_fails:
            user += f"\n\n<previous_attempt_issues>\nThe previous script was rejected by automated checks due to these structural issues:\n- " + "\n- ".join(last_fails) + "\n\nYou MUST fix every listed issue in this rewrite.\n</previous_attempt_issues>"

        print(f"[info] Attempt {attempt+1}/3: calling LLM...")
        script = claude(SCRIPT_SYSTEM, user, model=model, max_tokens=max_tok)
        save("script.txt", script)
        print(f"[info] Script generated: {len(script)} chars, ~{len(script.split())} words")

        # ---- GATE 1 (local, no LLM call, zero credits) ----
        passed, fails = gate1_local(script, c)
        if passed:
            ok(f"GATE 1 PASSED (attempt {attempt+1}, local check). Structure verified.")
            return

        last_fails = fails
        print(f"[retry] Gate 1 local check attempt {attempt+1}/3 failed: {fails}")

    # All attempts exhausted — STOP the pipeline (don't waste credits downstream)
    save("gate1_report.txt", "\n".join(last_fails))
    die(f"GATE 1 FAILED after 3 attempts. Issues: {last_fails}")

if __name__ == "__main__":
    main()
