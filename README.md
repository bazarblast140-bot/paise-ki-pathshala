# Paise Ki Pathshala — Hindi personal finance pipeline

Hindi personal-finance YouTube channel (India) ka pipeline: topic → Hindi script → Hindi voice →
visuals → video → verify → upload. Sirf isi channel ke liye — quiz channel ka code
`finance-english-youtube-auto` repo me hai, dono ko kabhi mat milana.

- **Do format:** long (16:9, ~5 min) aur **short** (9:16, ~50s). Har topic ka ek short aur ek
  long banta hai; short description me long video ki taraf bhejta hai.
  `python run_pipeline.py --format short` / `--format long`. Short ke overrides
  `config.json -> formats.short` me.
- Short ka layout YouTube Shorts ke UI se bahar rehta hai: upar 190px, right rail x > 890
  (beech se neeche), neeche 1400px ke baad kuch zaroori nahi (`run_assembly.py`, `V_*`).
- **Intro/outro nahi.** Video pehle frame se hi hook se shuru hota hai.
- Script / voice / visual prompts Hindi ke liye (`run_script.py`, `run_voice.py`, `run_visuals.py`).
- Captions Devanagari me; cards, charts aur thumbnail ka text Roman (Hinglish) me — PIL aur
  matplotlib Devanagari ki matra theek nahi jodte.
- SEBI-safe: koi share/fund/product recommendation nahi, guaranteed return nahi, tax figure ke saath FY.
- Upload **sirf** tab hota hai jab token ka channel `EXPECTED_CHANNEL_ID` (repo variable
  `YT_FIN_CHANNEL_ID`) se match kare.
- Review phase: videos **unlisted** jaate hain, schedule band hai. 3-5 video dekh kar hi roz ka
  cron chalu karna.

## GitHub secrets (Settings → Secrets and variables → Actions)

| Naam | Kya |
|---|---|
| `DEEPSEEK_API_KEY` | Script/visual/verify LLM (config: `models.llm_provider = deepseek`) |
| `PEXELS_API_KEY` | Kabhi-kabhi stock clip (max 8%) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Har run ka report |
| `YT_FIN_CLIENT_ID`, `YT_FIN_CLIENT_SECRET`, `YT_FIN_REFRESH_TOKEN` | Channel ka OAuth — `yt_token_to_github.py --prefix YT_FIN` se |
| variable `YT_FIN_CHANNEL_ID` | Upload guard — wahi script set karti hai |
| `KIMI_API_KEY`, `ANTHROPIC_API_KEY` | Optional backup LLM |

---

## Purana README (English "The Money Rule" setup — structure wahi hai)

# Finance English — Faceless YouTube Pipeline

Faceless English finance/investing channel pipeline. Script → Voice → Visuals → Assembly, with a Verify Agent gating every stage, and a daily scheduler.

**Separate from your Hindi Shorts channel.** Different folder, different config, different YouTube token. Never mix them.

---

## THE GOLDEN RULE (read first)

**Do NOT turn on the daily auto-scheduler until 3–5 videos have proven they retain viewers.**

Automation scales whatever you feed it. If the content works, automation makes you 100x. If it doesn't, automation just scales failure — one boring video a day, dragging the channel's signal down. So:

1. Run the pipeline manually (`python run_pipeline.py`).
2. Watch the video. Read the verify report. Fix prompts if needed.
3. Publish manually. **Wait 48 hours. Check the retention graph in YouTube Studio.**
4. Retention decent (viewers staying)? → enable the daily scheduler.
5. Retention bad? → tune the prompts in `prompts/` and `run_*.py`, NOT the plumbing. Repeat.

---

## SETUP (once)

```bash
cd finance-english
cp .env.example .env          # then fill in your keys
pip install -r requirements.txt
```

Edit `config.json`:
- `channel.name`
- keep `pipeline.auto_publish: false` for now

**Voice:** uses the configured ElevenLabs voice ID when `ELEVENLABS_API_KEY` is available. If the key is missing or ElevenLabs fails, narration falls back to free Edge TTS. Set `voice.engine: "edge"` to force Edge.

Check everything before spending credits:
```bash
python check_setup.py
```

(Optional, nicer text) drop a bold TTF into `assets/fonts/Inter-Bold.ttf`.

---

## RUN ONE VIDEO (manual)

```bash
python run_pipeline.py
```

This runs all stages, stops if any gate fails, writes everything to `run/`, and pings you on Telegram with the verdict.

Outputs in `run/`:
- `script.txt` — the script + FACT LEDGER
- `voice.mp3` — the narration
- `shots.json` — the timed shot list
- `final.mp4` — the assembled video
- `report.md` — the Hinglish verify report (verdict + what you must review)

Review → if good, publish manually (upload `final.mp4` yourself), or:
```bash
python publish.py --only-if-ready    # uploads PRIVATE, only if verdict == READY
```

---

## GO DAILY (after 3–5 good videos)

**GitHub Actions:** the workflow in `.github/workflows/` is already wired for this
folder as the repo root. Add repo Secrets
(`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`),
and it runs every day at 06:30 IST. Still generate-only (no auto-publish).
It commits `topic_bank.json` back after each run so topics never repeat.

**VPS cron:** see `SCHEDULER.md`.

To later allow auto-publish: set `config.json → pipeline.auto_publish: true` and add YouTube secrets.
Even then it uploads **private + only if verdict READY** — never instant-public.

---

## FILE MAP

| File | Does |
|---|---|
| `check_setup.py` | preflight: keys, ffmpeg, deps, config TODOs — fails before spending credits |
| `sketch.py` | hand-drawn (Excalidraw-style) marker highlights, rough strokes, xkcd charts |
| `assets_pexels.py` | real stock footage per shot, cached in `assets/cache/` |
| `sfx.py` | synthesized click track — no sound files to ship |
| `pick_topic.py` | picks next unused topic from `topic_bank.json` |
| `run_script.py` | Script Agent + **Gate 1** (hook, facts, compliance) |
| `run_voice.py` | Voice Agent + Edge TTS / ElevenLabs + **Gate 2** (coverage, length, TTS-safety) |
| `run_visuals.py` | Visual Agent + **Gate 3** (density, numbers, repeats) |
| `run_assembly.py` | FFmpeg: cards + voice + burned subtitles → `final.mp4` |
| `run_verify.py` | **Final Gate** → Hinglish `report.md` |
| `notify.py` | Telegram ping |
| `publish.py` | opt-in YouTube upload (private, only if READY) |
| `run_pipeline.py` | chains all stages, stops on gate fail |
| `prompts/` | full Verify Agent + Worker Agent prompts |

---

## HOW THE VIDEO IS BUILT

Each shot becomes its own clip, then the clips are joined. Per shot:

- **backdrop** — real Pexels footage (slow pan), or a hand-drawn xkcd chart of the
  script's own numbers, or a plain card. Whichever is available; footage never
  fails a run.
- **overlay** — coloured section kicker, headline with a marker highlight on the
  key phrase, bottom scrim, progress bar. Zooms and fades in.
- **sound** — a quiet click on the cut, synthesized (no asset files).

Layout is fixed so the two text layers can never collide: kicker on top, headline
in the middle, and the bottom 28% belongs to captions alone. On chart shots the
headline shrinks to a title band so the chart stays readable.

**Memory:** the renderer caps x264 threads and lookahead. On a 3–4GB machine the
defaults die with `malloc of size N failed` partway through a render.

**Footage cache:** `assets/cache/` fills up (~120MB per video) and is gitignored.
Delete it any time — clips are re-fetched from each shot's `source_asset_query`.
