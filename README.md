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
| `YT_DATA_API_KEY` *(optional)* | Read-only YouTube Data API for competitor research and the daily trend job. Secret na ho to wahi OAuth chalega (`YT_FIN_CLIENT_ID` / `YT_FIN_CLIENT_SECRET` / `YT_FIN_REFRESH_TOKEN`). Dono na hon to YouTube wala hissa skip; naya secret mat banao jab tak key cloud project me ho |
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
error — step skip hota hai, video phir bhi banta hai. Quota: ~350 units/run, hard cap
`COMPETITOR_QUOTA_BUDGET` = 500 (10,000/din free, uploads ke saath shared).

## Daily trend research (read-only, publish nahi)

`trend_research.py` aur workflow **Daily trend research**
(`.github/workflows/daily-trend-research.yml`).

- **Schedule:** roz 11:23 UTC = **16:53 IST** (odd minute, 22:30–01:30 UTC ke bahar).
  08:30 wala build usi shaam 18:43 IST se shuru ho sakta hai, isliye research usse pehle chalti hai.
  **Run workflow** se bhi chalega (`dry_run` = fetch only,
  commit nahi). Is workflow par `push:` trigger nahi hai.
- **Kabhi publish / upload / Reel nahi.** Sirf `data/trends.json` aur
  `reports/trends-YYYY-WW.md` (IST ISO week, jaise `reports/trends-2026-40.md`) commit
  karta hai. Push race par `git fetch` + rebase, trend-file conflict par isi run ka
  snapshot rakhta hai, phir retry.
- **Auth:** `YT_DATA_API_KEY` ho to wahi, warna `yt_credentials.py` wala OAuth
  (secrets pehle se pipeline me hain). Naya secret add nahi karna.
- **Quota:** is job ki hard ceiling **1,200 units** (config se badh nahi sakti).
  `search.list` = 100, baaki calls (videos, channels, playlistItems, mostPopular) = 1.
  Queries max 6. `competitor_research.py` ki 500 ke saath mila kar research ~1,700
  par rukti hai — ~2,000 ki research ceiling ke neeche. Upload ~1,600 × 3 slots alag
  hain. Budget poora ho ya YouTube `quotaExceeded` de, job skip karke jo data mil chuka
  hai wahi likhti hai; metric invent nahi karti (field na ho to `null`).
- **Kya likhti hai:** pichhle 7 din ke Hindi finance Shorts (title, opening line se
  hook pattern, duration, views/likes/comments, channel, thumbnail size / tag se
  visual note — warna khali), aur India finance headlines (Economic Times, Mint,
  Business Standard, Hindu BusinessLine) source + date ke saath. Schema
  `trend_research.py` ke docstring aur `data/trends.json` ke `schema_note` me hai.
- **Picker:** `pick_topic.py` cluster keyword match par **+0.25**, cap **0.8**.
  Ek strong pattern weight 2.0 hai aur apna measured result 0.75 tak, isliye trend
  topic force nahi karta. 8 din se purana snapshot ignore. Templated placeholder
  (audit: generic "आसान भाषा में समझो" beats, ya repeated hook + fact = title)
  candidate list se hat jaate hain — `TOPIC_KEY` se bhi nahi chalte. Bank me jo
  topic hai sirf wahi rank hota hai; trend file naya topic nahi banati.

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

**Voice:** a live slot uses the configured ElevenLabs voice while at least 2,500 characters remain. Below that floor, or when the subscription API errors or reports insufficient quota, the slot uses Edge TTS and logs `tts_engine=edge` plus the reason in `run/credits.json`. The sync gate still runs on that audio. An invalid key or a missing permission is a hard error (no silent Edge fallback). Buffer Shorts are Edge only (`hi-IN-MadhurNeural`, `+0%`) and spend no ElevenLabs characters. Set `voice.engine: "edge"` to force Edge.

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
| `health_check.py` | pre-slot read-only check (token, credits, IG expiry); opens/closes `health-alert` |
| `check_setup.py` | preflight: keys, ffmpeg, deps, config TODOs — fails before spending credits |
| `sketch.py` | hand-drawn (Excalidraw-style) marker highlights, rough strokes, xkcd charts |
| `assets_pexels.py` | real stock footage per shot, cached in `assets/cache/` |
| `sfx.py` | synthesized click track — no sound files to ship |
| `pick_topic.py` | picks next unused non-placeholder topic; trends add at most 0.8 |
| `trend_research.py` | read-only daily YouTube + RSS research → `data/trends.json` + `reports/` |
| `competitor_research.py` | read-only competitor/own-channel patterns (quota cap 500) |
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
  Health check **14 din ya usse kam** pe warning issue kholta hai — expiry debug_token se aati hai, date guess nahi hoti.

Monetization thresholds + checklist: `MONETIZATION.md`.

## Schedule (build 12–14h ahead, 3 slots)

GitHub cron late hota hai, `:00` / `:30` gira deta hai, aur **22:30–01:30 UTC** par aksar run banata hi nahi. Isliye 9 trigger odd minutes par hain, us window ke bahar. Har slot ki 3 chances uske publish se **12–14 ghante** pehle hain. YouTube `publishAt` slot ka public time hai. Ek run sabse pehla **khali** future slot leta hai jo abhi se kam se kam **2 ghante** aur zyada se zyada **26 ghante** door ho (hold aur filled skip). Missed slot doosre slot par roll nahi hota aur turant public nahi hota. Din me public posts 3 hi rehte hain. Steady state me kam se kam 2 future slot pehle se private + publishAt par hone chahiye.

| UTC cron | IST | 12–14h target |
|---|---|---|
| `47 1 * * *` | 07:17 | 21:00 (13h43m) |
| `23 2 * * *` | 07:53 | 21:00 (13h07m) |
| `13 3 * * *` | 08:43 | 21:00 (12h17m) |
| `7 13 * * *` | 18:37 | agla 08:30 (13h53m) — 3 Oct 08:30, 2 Oct ki shaam |
| `47 13 * * *` | 19:17 | agla 08:30 (13h13m) |
| `23 14 * * *` | 19:53 | agla 08:30 (12h37m) |
| `37 20 * * *` | 02:07 | 16:00 (13h53m) |
| `13 21 * * *` | 02:43 | 16:00 (13h17m) |
| `47 21 * * *` | 03:17 | 16:00 (12h43m) |

08:30 IST = 03:00 UTC. 16:00 IST = 10:30 UTC. 21:00 IST = 15:30 UTC. 21:00 se agla 08:30 = 11.5h.

Slot watch (alert): `19 3` (08:49 IST), `19 11` (16:49 IST), `19 16` (21:49 IST). Khali slot par extra instant-public video nahi; fill sirf tab jab agla khali slot 26h lookahead me ho.

## Health check (build se pehle)

Workflow: **Health check** (`.github/workflows/health-check.yml`). Actions cron UTC hai; IST = UTC+5:30. Check us slot ke sabse pehle 12–14h build se pehle chalti hai, odd minute par, 22:30–01:30 UTC ke bahar:

| Check (IST) | UTC cron | Slot |
|---|---|---|
| 17:17 | `47 11 * * *` | 08:30 |
| 00:53 | `23 19 * * *` | 16:00 |
| 02:53 | `23 21 * * *` | 21:00 |

Agar ledger me **2 se kam** future slot private + publishAt par hon, yehi workflow `health-alert` issue kholta hai (hold aur withdrawn count nahi hote).

Agar backup buffer me **2 se kam** ready Short hon, yahi check `health-alert` kholta hai. Buffer ka matlab: 2–3 evergreen Shorts jo pehle se har gate pass kar chuke hain. Slot ka apna build gate pe fail ho, ya latest build tak slot khali ho, to pipeline sabse purana ready item private + `publishAt` par daal deta hai. Hold row kabhi nahi bharti. Item ek baar use hota hai.

## Backup buffer (evergreen Shorts)

Manifest: `data/buffer_manifest.json` (`ready` / `used`, gate timestamps). MP4 + metadata GitHub release tag **`buffer-shorts`** par hain. Sirf concept topics (risk, position size, stop-loss, compounding jaisi baatein) — live market price wale topics nahi. Screen par jo number ho wo example / मिसाल labelled hona chahiye. 3-trading-day freshness gate real market data par pehle jaisi hai; evergreen item par as-of stamp hota hi nahi.

**Pehle 2–3 item kaise bharen:** Actions → **Buffer refill** → Run workflow. Ek run **ek** item, tab tak jab ready 3 se kam hon. Roz `41 6 * * *` UTC (12:11 IST) bhi yahi karta hai — build crons se alag ghanta. Har item par DeepSeek **zyada se zyada 4** calls (saaf run mein aksar 2: ek narration + ek AI review). Buffer voice **sirf Edge** hai: `hi-IN-MadhurNeural`, rate `+0%`, **0 ElevenLabs characters**. Character floor buffer par lagta hi nahi. Gate fail ho to item store nahi hota.

**Live slot voice:** ElevenLabs tabhi jab subscription par **kam se kam 2,500** characters bache hon. Usse kam, API error, ya insufficient quota par wahi slot Edge se jaata hai (`tts_engine=edge` aur reason `run/credits.json` mein). Slot skip nahi hota. Whisper sync gate usi final audio par chalta hai, chahe engine Edge ho. Invalid key ya missing permission pehle ki tarah hard error hai.

Khali buffer par slot skip rehta hai aur `health-alert` issue khulta hai.

`workflow_dispatch` bhi hai (slot = auto / 08:30 / 16:00 / 21:00). Sirf read: YouTube **refresh** (access token discard, koi Data API call nahi), DeepSeek `GET /user/balance`, Kimi balance **agar key set ho**, Anthropic `GET /v1/models` **agar key set ho**, ElevenLabs `GET /v1/user/subscription`, Instagram `debug_token` (warna ek `fields=id` read). Koi upload / publish / Reel post nahi.

| Check | Problem kab |
|---|---|
| YouTube token | missing `YT_FIN_*` ya `invalid_grant` / `invalid_client` → slot at risk |
| DeepSeek | selected provider. Balance 0 / `is_available=false` → slot at risk. `< 1` currency unit → warning |
| Kimi | sirf jab `KIMI_API_KEY` set ho. Suspended ya balance 0 → issue. Video tab tak nahi rukta jab tak ye selected provider na ho |
| Anthropic | sirf jab `ANTHROPIC_API_KEY` set ho. Key invalid ya response me "credit balance too low" → issue. **Balance number measurable nahi** (standard key pe balance endpoint nahi; billed call nahi karte, number guess nahi) |
| ElevenLabs | key missing → warning (Edge fallback, video ban sakta hai). Invalid key ya missing permission → slot at risk. `< 6000` characters (ek din ke 3 Shorts) ya payment status incomplete/past_due, ya 0 characters → warning; quota/payment par run Edge TTS use karta hai, slot block nahi hota. Fields missing hon to 0 guess nahi karte |
| Instagram | missing / invalid / expired token → Reel at risk. **≤ 14 din** → warning. `debug_token` na chale aur cheap read chal jaye to expiry **not measurable** |
| YouTube quota | hamesha **not measurable**. Refresh response me remainder nahi aata, Cloud Monitoring credential nahi hai, Data API probe nahi chalate |

Koi naya secret nahi chahiye. Optional backup (`KIMI_API_KEY`, `ANTHROPIC_API_KEY`) unset ho to skip — ye missing nahi maana jata. Issue tab khulta hai jab koi check `fail` ya `warn` ho: label `health-alert` (na ho to ban jati hai), ek hi open issue, har run usi ko update karti hai. Sab clear ho to auto-close. `GITHUB_TOKEN` + `issues: write`. Values log ya issue me print nahi hoti.
