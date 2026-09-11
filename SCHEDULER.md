# DAILY SCHEDULER — Faceless Finance YouTube Pipeline

Two ways to run the pipeline once a day. **Default = generate + verify + notify (no auto-publish).** You review and publish with one tap.

---

## OPTION A — GitHub Actions (recommended: free, matches your Hindi Shorts setup)

Save as `.github/workflows/daily-finance.yml` in your repo.

```yaml
name: Daily Finance Video

on:
  schedule:
    # 01:00 UTC = 06:30 AM IST daily. Adjust to taste.
    # GitHub cron is NOT exact — it can be delayed several minutes under load.
    # For a once-daily generate job that's fine.
    - cron: "0 1 * * *"
  workflow_dispatch:   # lets you also trigger it manually from the Actions tab

# Only one run at a time — never overlap two daily runs
concurrency:
  group: daily-finance
  cancel-in-progress: false

jobs:
  build-video:
    runs-on: ubuntu-latest
    timeout-minutes: 60   # hard stop so a hung run doesn't burn minutes

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install FFmpeg
        run: sudo apt-get update && sudo apt-get install -y ffmpeg

      - name: Install deps
        run: pip install -r finance-english/requirements.txt

      # ---- PIPELINE STAGES (each stage exits non-zero on gate FAIL) ----

      - name: 1. Pick today's topic
        run: python finance-english/pick_topic.py
        # reads topic_bank.json, picks next unused topic, writes run/topic.txt

      - name: 2. Script Agent + Gate 1
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python finance-english/run_script.py   # fails if Gate 1 fails

      - name: 3. Voice Agent + Gate 2
        env:
          ELEVENLABS_API_KEY: ${{ secrets.ELEVENLABS_API_KEY }}
        run: python finance-english/run_voice.py     # fails if Gate 2 fails

      - name: 4. Visual Agent + Gate 3
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python finance-english/run_visuals.py   # fails if Gate 3 fails

      - name: 5. Assembly (FFmpeg)
        run: python finance-english/run_assembly.py  # builds run/final.mp4

      - name: 6. Final Verify Gate → report
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python finance-english/run_verify.py    # writes run/report.md

      # ---- OUTPUT: keep the video + report, do NOT auto-publish ----

      - name: Upload video + report as artifact
        if: always()   # keep outputs even if a later step failed, for debugging
        uses: actions/upload-artifact@v4
        with:
          name: daily-video-${{ github.run_number }}
          path: |
            finance-english/run/final.mp4
            finance-english/run/report.md
            finance-english/run/script.txt
          retention-days: 7

      # ---- NOTIFY YOU (pick one; Telegram shown) ----

      - name: Notify Rajesh (Telegram)
        if: always()
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TG_CHAT: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python finance-english/notify.py --status "${{ job.status }}"
        # sends: verdict + report + "review artifact & publish" link
```

**What you set up once (GitHub → Settings → Secrets):**
`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
(add `YOUTUBE_*` secrets only when/if you enable auto-publish — see below).

**How your day looks:** 06:30 IST it runs → ~10 min later Telegram pings you with the verdict and a link → you open the video on your phone → if good, you publish. Done.

---

## OPTION B — VPS crontab (if you want it fully on your own server)

You already considered a ~₹500/mo VPS. More reliable timing + heavier FFmpeg renders than GitHub's free runners. Add one line to crontab (`crontab -e`):

```bash
# Daily finance video at 06:30 IST. Server assumed on IST; if UTC, use 30 1 * * *
30 6 * * * cd /home/rajesh/finance-english && /usr/bin/python3 run_pipeline.py >> logs/daily.log 2>&1
```

Where `run_pipeline.py` chains the same 6 stages and stops on any gate FAIL. Keep the log — that's your debug trail when a run misbehaves.

---

## AUTO-PUBLISH (opt-in only — read the warning)

If later you trust the pipeline enough to skip manual review, you can flip it to auto-publish ONLY when the verify gate returns READY. Add this as the last step, gated on the report:

```yaml
      - name: 7. Auto-publish IF gate passed (opt-in)
        if: ${{ success() && env.AUTO_PUBLISH == 'true' }}
        env:
          AUTO_PUBLISH: ${{ vars.AUTO_PUBLISH }}   # repo variable, default 'false'
          YOUTUBE_TOKEN: ${{ secrets.YOUTUBE_TOKEN }}
        run: python finance-english/publish.py --only-if-ready
        # publish.py re-reads report.md; refuses to upload unless verdict == READY.
        # Uploads as PRIVATE + scheduled publishAt = peak US time, so you still
        # get a window to pull it before it goes live.
```

**Warning (this is the channel-safety line):** even in auto mode, `publish.py` must (1) refuse unless the verify verdict is READY, and (2) upload as **private + scheduled**, not instant-public — so a bad video is still catchable before it's live. Never wire instant-public auto-upload on a finance channel. One bad "guaranteed returns" video going live unattended is how channels get struck.

---

## TIMING NOTES

- **Generation time ≠ publish time.** Generate early (06:30 IST) so there's buffer; schedule the actual YouTube publish for peak US viewing (roughly 3–6 PM ET on weekdays) via the API's `publishAt`. Your video waits, then goes live when your US audience is active.
- **GitHub cron drift:** fine for daily. If you ever need exact-minute timing, use the VPS.
- **One topic per run:** `pick_topic.py` must mark the topic used so you never publish duplicates. This is the one bit of state the daily loop depends on — guard it.

---

## WHAT STILL NEEDS BUILDING

This scheduler assumes the `finance-english/` project scripts exist (`run_script.py`, `run_voice.py`, etc.). Those are the next piece — the project skeleton that wires the agent prompts to actual API calls and FFmpeg. The scheduler is the outer loop; the skeleton is the engine it calls.
