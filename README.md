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
| `YT_FIN_CLIENT_ID`, `YT_FIN_CLIENT_SECRET`, `YT_FIN_REFRESH_TOKEN` | Channel ka OAuth — `python youtube_auth.py --set-github-secret` se (neeche dekho) |
| variable `YT_FIN_CHANNEL_ID` | Upload guard — token ka channel isi ID se match hona chahiye |
| `YT_DATA_API_KEY` *(optional, naya)* | Competitor research (read-only). Na ho to OAuth token se chalega, woh bhi na chale to step skip |
| `IG_ACCESS_TOKEN`, variable `IG_USER_ID` | Instagram Reel cross-post |
| `KIMI_API_KEY`, `ANTHROPIC_API_KEY` | Optional backup LLM |
| variable `SELF_REPAIR` *(optional)* | `1` = CI ke andar code self-repair (LLM / deterministic patch, phir `main` par push). Default **band**; "Autonomous Repair Watchdog" ab sirf manual (Run workflow) |

## YouTube token permanent kaise karein (ek baar, ~10 min)

**Kya hua tha:** `YT_FIN_REFRESH_TOKEN` 21-Sep-2026 ko bana aur 28-Sep ko (theek 7 din baad)
Google ne `invalid_grant: Token has been expired or revoked` de diya. Iska matlab lagbhag
hamesha yahi hota hai ki Google Cloud project ka **OAuth consent screen "Testing" status me hai** —
Testing me Google har refresh token ko 7 din me khatam kar deta hai. "In production" me token
tab tak chalta hai jab tak aap khud revoke na karo / password badlo / 6 mahine use na ho
(pipeline roz chalti hai, to 6 mahine wala issue nahi aayega).

Code ab har run me refresh token se naya access token leta hai (`yt_credentials.py`); access
token ka 1 ghante me expire hona normal hai aur apne aap handle hota hai. Token mar jaaye to
run me saaf error aata hai (`::error::YouTube token expired or revoked`, Telegram par bhi), aur
Instagram Reel phir bhi jaati hai.

**Steps:**
1. <https://console.cloud.google.com/> kholo → wahi project chuno jiska OAuth client
   `YT_FIN_CLIENT_ID` me hai (APIs & Services → Credentials me client ID match karo).
2. **APIs & Services → OAuth consent screen** (naye UI me: **Google Auth Platform → Audience**).
3. "Publishing status: Testing" dikhe to **PUBLISH APP** → **Confirm**. Status ab
   **"In production"** dikhna chahiye. User type "External" hi rehne do.
   - Google "verification" maang sakta hai (YouTube scopes sensitive hain). Apne khud ke
     channel ke liye verification zaroori nahi: unverified app bhi production me chalta hai,
     bas login par "Google hasn't verified this app" warning aati hai (100 users tak limit) —
     **Advanced → Go to … (unsafe)** dabao. Production me token 7 din wala expiry nahi hota.
4. Laptop par (browser wala computer), is repo me:
   ```bash
   pip install google-auth-oauthlib google-api-python-client
   # Credentials page se OAuth client (Desktop app) ka JSON download karo: client_secret.json
   python youtube_auth.py --client-secret-file client_secret.json --set-github-secret
   ```
   Browser me **Paise Ki Pathshala** channel wala account chuno, saare permission tick karo
   (agar "time-limited access" ka option aaye to time limit **mat** chuno). Script channel ka
   naam dikhayegi, `y` dabao. `--set-github-secret` token ko seedha GitHub secret
   `YT_FIN_REFRESH_TOKEN` me daal deta hai (`gh` CLI login chahiye) — token screen par print
   nahi hota. `gh` na ho to script manual command bata degi.
   - Naya OAuth client banaya ho to `YT_FIN_CLIENT_ID` / `YT_FIN_CLIENT_SECRET` secrets bhi
     usi client ke daalne honge (client aur token ek hi app ke hone chahiye).
5. Check (upload nahi karta, sirf channel ka naam dikhata hai):
   Actions → "Paise Ki Pathshala" → Run workflow (privacy **unlisted**) → step
   "YouTube token health check" hara hona chahiye. Ya laptop par `python yt_credentials.py`.

**Tip:** Quiz Pathshala (`finance-english-youtube-auto`) ke uploads roz chal rahe hain, yaani
uska OAuth project shayad pehle se production me hai. Chaaho to usi project ka client
ID/secret use karke Paise wala token bana sakte ho — bas login Paise Ki Pathshala channel se
karna. (Ye maine verify nahi kiya — Cloud Console me status dekh lena.)

## Competitor research → self-improvement (naya, optional)

`competitor_research.py` har din ek baar (pipeline ke andar, `--max-age-hours 20`) read-only
YouTube Data API calls karta hai:
- `data/research_sources.json` ke competitor channels (Deepu Maurya, Stock Menthol — aur
  `config.json → research.extra_channel_ids`) ke pichhle 30 din ke uploads,
- `config.json → research.search_keywords` (max 3) ke top Hindi Shorts,
- apne channel ke uploads (ledger `data/published.json` ke `topic_key` se).

Har video ko uske **apne channel ke median** se compare karta hai (bada channel sirf bada hone
se nahi jeetta) aur likhta hai:
- `data/competitor_insights.json` — top videos (title, views/day, duration, IST upload time,
  tags), jeetne wale title patterns (sawaal / number / warning …), keywords, best upload hours.
  **Ye aapke padhne ke liye hai.**
- `data/competitor_performance.json` — auto rows (sirf labels, koi title nahi) jinse
  `pick_topic.py` topic chunta hai.
- `data/own_topic_performance.json` — apne kaunse topics chale; wahi type zyada chuna jaata hai.

`prompt_hint()` ek aggregate hint deta hai ("sawaal wale title, ye keywords…") — competitor ka
title/script kabhi copy ke liye nahi. Ye hint abhi narration prompt me **wired nahi hai** (board_short.py
ki narration logic jaisi hai waisi chhodi hai); chaaho to baad me jod sakte ho. Secret na ho / quota khatam /
error — step skip hota hai, video phir bhi banta hai. Quota: ~350 units/run (10,000/din free).

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

## Instagram Reel — kya aur kyun (30-Sep-2026)

`publish_instagram.py` wahi `run/final.mp4` Reel ki tarah daalta hai — sirf **public** run par
(`IG_CROSSPOST`) aur sirf jab `run/report.md` me `READY TO PUBLISH` ho. "Instagram Reel Publish
Watchdog" bhi sirf tab retry karta hai jab fail hone wala step khud Instagram tha aur us run ne
Reel pehle se nahi daali.

- **Caption:** keyword wala title sabse upar (pehle ~125 char "more" se pehle dikhte hain, aur
  Instagram search caption padhta hai) → hook → "📌 याद रखो" 2 bullet (FACT LEDGER se, save-worthy)
  → comment + save CTA → disclaimer → bio referral line + `data/referrals.json` ki disclosure.
  Caption me URL nahi (clickable nahi hota).
- **Hashtags:** `hashtags_for()` = main wala topic-specific set (base 4 + category 2 + seo tag).
  Instagram app Dec-2025 se 5 tag ki limit batata hai; API se 6-7 tag wali Reels abhi tak chali
  gayi hain. Reject hone lage to `hashtags_for()` ko 5 par kaat do.
- **Cover:** profile grid Reel cover ko beech se 3:4 crop karta hai (y 240–1680) — hamara
  thumbnail title y≈150 par tha, grid me kat jaata tha. Ab IG-only `run/ig_cover.jpg`: poora
  design 3:4 box me, peeche blur. YouTube thumbnail nahi badalta. Band karna ho:
  `config.json` → `"instagram": {"grid_safe_cover": false}`.
- Token: `IG_ACCESS_TOKEN` ~60 din ka hai (24-Sep set → ~23-Nov expire). Refresh manual.

Monetization thresholds + checklist: `MONETIZATION.md`.
