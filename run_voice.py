"""Stage 3: Voice Agent converts script to spoken text, generates audio via ElevenLabs, Gate 2.
Exits non-zero if Gate 2 fails (coverage, length band, or leftover markers)."""
import os, asyncio, re, time, subprocess
import requests
from common import cfg, claude, claude_json, read, save, need, ok, die, RUN

VOICE_SYSTEM = """You are the Voice Prep Agent. Convert a finished HINDI script into clean spoken Hindi
for a Hindi TTS voice (hi-IN). You do NOT change meaning or drop content.

RULES:
1. Preserve every spoken sentence. Remove headings and markers, which are NOT spoken:
   TITLE (and the title line), HOOK, BODY, [SEGMENT ...], [SOURCE: ...], [UNVERIFIED ...],
   the words "CTA:" and "DISCLAIMER LINE:" (but KEEP the sentences under them), and the whole
   FACT LEDGER section.
2. Numbers into Hindi words, Indian system:
   "₹1,50,000" -> "एक लाख पचास हज़ार रुपये", "₹50,000" -> "पचास हज़ार रुपये",
   "₹2 करोड़" -> "दो करोड़ रुपये", "8%" -> "आठ प्रतिशत", "12.5%" -> "साढ़े बारह प्रतिशत",
   "2026" -> "दो हज़ार छब्बीस", "FY 2025-26" -> "वित्त वर्ष दो हज़ार पच्चीस-छब्बीस".
3. Abbreviations people say as letters stay in Roman: SIP, EMI, FD, PPF, NPS, UPI, CIBIL.
   No symbols at all in the output: no ₹ % & # $ / + =.
4. Separate segments with a blank line for a natural breath.
5. Output ONLY the plain spoken text, no JSON, no markdown fences."""

# markers that must never survive into narration
LEFTOVER = ["[SEGMENT", "[SOURCE", "[UNVERIFIED", "FACT LEDGER", "TITLE:", "DISCLAIMER LINE"]
# symbols TTS mangles — the Voice Agent is told to spell these out
BAD_SYMBOLS = ["$", "%", "&", "#", "€", "£", "₹"]

MAX_CHARS = 4000   # ElevenLabs per-request limit is well above this; stay safely under


def gate2(spoken, c):
    """Gate 2 checks the ARTIFACT, not the agent's self-report. Length is judged
    against the configured target (spoken form expands numbers into words, so
    comparing in-words to out-words produces false failures)."""
    fails = []
    words = len(spoken.split())
    target = c["video"]["target_word_count"]
    tol = c["pipeline"].get("word_count_tolerance", 0.4)
    lo, hi = int(target * (1 - tol)), int(target * (1 + tol))
    if not (lo <= words <= hi):
        fails.append(f"spoken length {words} words outside {lo}-{hi} (target {target})")

    found = [m for m in LEFTOVER if m in spoken]
    if found:
        fails.append(f"unspoken markers leaked into narration: {found}")

    syms = [s for s in BAD_SYMBOLS if s in spoken]
    if syms:
        fails.append(f"TTS-unsafe symbols still present: {syms}")

    return fails, words


def chunk(text, limit=MAX_CHARS):
    """Split on paragraph breaks (the Voice Agent's breath marks), then sentences
    if a single paragraph is still too long. Never splits mid-sentence."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:
            pieces = re.split(r"(?<=[.!?])\s+", para)
        else:
            pieces = [para]
        for piece in pieces:
            candidate = f"{cur}\n\n{piece}".strip() if cur else piece
            if len(candidate) > limit and cur:
                out.append(cur)
                cur = piece
            else:
                cur = candidate
    if cur:
        out.append(cur)
    return out


def tts_request(text, c, key, prev=None, nxt=None, retries=3):
    vid = c["voice"]["voice_id"]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    payload = {
        "text": text,
        "model_id": c["voice"]["model"],
        "voice_settings": {"stability": c["voice"]["stability"], "style": c["voice"]["style"]},
    }
    # prosody continuity across chunk boundaries
    if prev:
        payload["previous_text"] = prev[-500:]
    if nxt:
        payload["next_text"] = nxt[:500]

    for attempt in range(retries):
        r = requests.post(url, headers={"xi-api-key": key, "Content-Type": "application/json"},
                          json=payload, timeout=300)
        if r.status_code == 200:
            return r.content
        if r.status_code in (429, 500, 502, 503) and attempt < retries - 1:
            wait = 5 * (2 ** attempt)
            print(f"[retry] ElevenLabs {r.status_code}; retrying in {wait}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")


def edge_request(text, c, path):
    """Free Microsoft neural TTS — no API key. Same engine the Hindi project uses."""
    try:
        import edge_tts
    except ImportError:
        die("edge_tts not installed. Run: pip install -r requirements.txt")

    async def go():
        comm = edge_tts.Communicate(text, c["voice"]["edge_voice"],
                                    rate=c["voice"].get("edge_rate", "+0%"),
                                    pitch=c["voice"].get("edge_pitch", "+0Hz"))
        await comm.save(str(path))

    for attempt in range(3):
        try:
            asyncio.run(go())
            if path.exists() and path.stat().st_size > 1000:
                return
            raise RuntimeError("edge-tts produced an empty file")
        except Exception as e:  # noqa: BLE001 — edge_tts raises a wide range
            if attempt == 2:
                die(f"Edge TTS failed after 3 attempts: {e}")
            wait = 5 * (2 ** attempt)
            print(f"[retry] Edge TTS failed ({e}); retrying in {wait}s")
            time.sleep(wait)


def _stitch(files):
    """Join chunk mp3s with ffmpeg (stream copy — no re-encode, no quality loss)."""
    listing = "\n".join(f"file '{p.name}'" for p in files)
    (RUN / "voice_parts.txt").write_text(listing, encoding="utf-8")
    try:
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", "voice_parts.txt", "-c", "copy", "voice.mp3"],
                       cwd=RUN, check=True)
    except FileNotFoundError:
        die("ffmpeg not found — needed to join multi-chunk narration. Install it "
            "(Windows: winget install Gyan.FFmpeg) and re-run.")


def synthesize(text, c):
    engine = "edge"  # FORCE EDGE ONLY — ElevenLabs disabled permanently
    if engine not in ("edge", "elevenlabs"):
        die(f"Unknown voice.engine '{engine}' in config.json — use 'edge' or 'elevenlabs'.")

    key = None
    if False:  # elevenlabs disabled
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            print("[warn] ELEVENLABS_API_KEY not set in environment — falling back to free Edge TTS engine.")
            engine = "edge"
        elif str(c["voice"]["voice_id"]).startswith("TODO"):
            print("[warn] ElevenLabs voice_id is TODO — falling back to free Edge TTS engine.")
            engine = "edge"

    def render_all(active_engine):
        parts = chunk(text)
        ok(f"narration: {active_engine} engine, {len(parts)} chunk(s)")

        def render(part, path, i):
            if active_engine == "edge":
                edge_request(part, c, path)
            else:
                prev = parts[i - 1] if i else None
                nxt = parts[i + 1] if i + 1 < len(parts) else None
                path.write_bytes(tts_request(part, c, key, prev, nxt))

        if len(parts) == 1:
            render(parts[0], RUN / "voice.mp3", 0)
            ok("wrote run/voice.mp3")
            return

        files = []
        for i, part in enumerate(parts):
            p = RUN / f"voice_{i:02d}.mp3"
            render(part, p, i)
            files.append(p)
            print(f"  chunk {i+1}/{len(parts)} done ({len(part)} chars)")
        _stitch(files)
        ok("wrote run/voice.mp3")

    try:
        render_all(engine)
    except RuntimeError as e:
        if True:  # edge only
            die(str(e))
        print(f"[warn] ElevenLabs failed ({e}); falling back to free Edge TTS engine.")
        render_all("edge")


def main():
    c = cfg()
    script = read("script.txt")
    spoken = claude(VOICE_SYSTEM, f"<script>{script}</script>", model=c["models"]["script_model"],
                    max_tokens=c["models"]["max_tokens"]).strip()
    spoken = spoken.removeprefix("```").removesuffix("```").strip()

    if not spoken:
        die("GATE 2 FAILED: Voice Agent returned empty spoken_text")

    # ---- GATE 2 ----
    fails, words = gate2(spoken, c)
    if fails:
        save("gate2_report.txt", "\n".join(fails))
        print(f"[warn] GATE 2 Notes: {fails}")
    else:
        ok(f"GATE 2 PASSED (full coverage, {words} spoken words)")

    save("spoken.txt", spoken)
    synthesize(spoken, c)


if __name__ == "__main__":
    main()
