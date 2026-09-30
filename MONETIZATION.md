# Monetization checklist: Paise Ki Pathshala + Quiz Pathshala

Checked on 30-Sep-2026 (IST). Sources: YouTube Help "YouTube Partner Program overview & eligibility"
(support.google.com/youtube/answer/72851), the YouTube Blog post of 10-Aug-2026 ("New opportunities to
earn and changes to the YouTube Partner Program"), and Instagram Help "How to earn money on Instagram Gifts"
(help.instagram.com/738469380549477). Re-check these before you apply, because the numbers change.

## Where the channels are now (public counts, 30-Sep-2026 ~15:00 IST)

| | Subscribers | Videos | Recent Shorts views |
|---|---|---|---|
| Paise Ki Pathshala (YouTube) | 21 | 46 | last 45 Shorts: 2,009 total, median 32 |
| Quiz Pathshala (YouTube) | 70 | 188 | last 47 Shorts: 1,852 total, median 15 |
| Paise Ki Pathshala (Instagram) | not verified (the automation has no follower read) | | |

We have no YouTube Analytics access (watch hours, impressions, retention) from the automation. Every figure
here comes from the public channel pages.

## YouTube Partner Program (YPP)

**Requirements to join (same for both channels):** follow the channel monetization policies; live in a YPP
country/region (India qualifies); no active Community Guidelines strikes; 2-Step Verification on the Google
account; advanced features access; one AdSense for YouTube account (create it only inside Studio).

**Early tier (fan funding + Shopping):** 500 subscribers, **plus** 3 public uploads in the last 90 days,
**plus** either 3,000 public long-form watch hours in 12 months **or** 3M public Shorts views in 90 days.

**Ads + Premium revenue share (current rule):** 1,000 subscribers, **plus** either 4,000 public long-form
watch hours in 12 months **or** 10M public Shorts views in 90 days. Watch time from Shorts feed views does
**not** count toward the 4,000 hours. Private, unlisted and deleted videos do not count.

**From 1-Feb-2027** (YouTube Blog, 10-Aug-2026):
- New applicants for ads and Premium need 8,000 watch hours in 365 days **or** 20M Shorts views in 90 days.
- Channels already in YPP keep their status, but must accept the new terms by 31-Jan-2027.
- Shorts ad and Premium revenue share will need 10M qualified Shorts views every 90 days, even for members.
- The fan-funding and Shopping thresholds stay the same.

**What this means for us:** at today's run-rate both channels are about 3 orders of magnitude below the
Shorts routes (roughly 2k views every ~10 days against 3M–20M per 90 days). The realistic path is the
**long-form watch-hour route**. Paise already builds a long (16:9) format; Quiz builds Shorts only.
Subscribers are the first gate for both: 21 and 70 today, against 500 and 1,000 needed.

| Item | Paise | Quiz | Automation does it? |
|---|---|---|---|
| Regular public uploads | 3 Shorts/day plus long format | 5 Shorts/day | Yes, but Paise's token died on 28-Sep (see PR #2). Uploads stop until you re-auth |
| Original / non-repetitive content (the "reused/repetitive content" policy) | topic bank, one-time use | LRU, no-repeat picker + 64 new questions (quiz PR) | Partly. Quiz repeated riddles until this PR, and a template channel making 5 near-identical videos a day is a YPP review risk |
| Long-form watch hours | long format exists | none | Paise: yes (1/topic). Quiz: no |
| Audience / made-for-kids label correct | general (finance) | **False, but the history and design point to kids. Risk** | No. Your decision (see the quiz README) |
| 2-Step Verification, AdSense, Studio "Get notified" | — | — | **No. Manual (you)** |
| No Community Guidelines strikes | unknown | unknown | Not checked. Look in Studio |
| Comments/engagement | pinned comment text | pinned comment **never posted (403 scope)** | Quiz: fixed in PR, needs a re-auth with `youtube.force-ssl` |

**Made-for-kids and money (Quiz):** a made-for-kids video can still earn in YPP, but it gets
non-personalized ads only (lower RPM), no comments, and some features switched off. A wrong "not for kids"
label is a policy and COPPA risk, and YouTube can change the label itself.

**Finance channel (Paise):** YouTube places financial-advice content under stricter ad-suitability rules.
Keep the "शिक्षा के लिए, सलाह नहीं" (education only, not advice) line and avoid naming buy/sell calls. The
automation already enforces this with a forbidden-word list and the disclaimer. Referral links in the
description carry a disclosure line (`data/referrals.json`).

## Instagram (Paise Ki Pathshala Reels)

Official (Instagram Help, Gifts):
- professional (Creator/Business) account
- 18 or older
- **at least 500 followers**
- Partner Monetization Policies, Content Monetization Policies and Community Standards
- Gifts Creator Terms accepted
- payout account set up
- a country where Gifts is available

Collab posts and accounts focused on children are **not** eligible for Gifts. Other tools (Subscriptions,
Bonuses, ads on Reels) have their own rules. Third-party guides say around 10,000 followers for
Subscriptions and that Bonuses are invite-only; I could not verify this on an official page. Check
**Professional dashboard → Monetization status** in the app. That screen is the real source of truth.

| Item | Automation does it? |
|---|---|
| Original content (not reposts or watermarks) | Yes: the same original Short, no watermark |
| ≤5 hashtags (Instagram limit since Dec-2025) | Yes: capped at 5 in `publish_instagram.py` |
| Caption for search and saves, clear disclaimer, referral disclosure | Yes (Paise PR #2) |
| 3:4-grid-safe cover | Yes (Paise PR #2), IG-only `ig_cover.jpg` |
| Posts only on READY and public runs | Yes (PR #2) |
| Token kept alive | **No.** `IG_ACCESS_TOKEN` (set 24-Sep) is a ~60-day token and expires around 23-Nov-2026. Refresh it manually |
| Follower count / monetization status read | No |
| Gifts, payouts, Creator Marketplace setup | **No. Manual (you) in the app** |
| Quiz Pathshala on Instagram | Not posted at all. If you add it, note the "content for children" exclusion |

## Your manual steps

1. Paise: set the OAuth consent screen to **In production**, then re-auth (`python youtube_auth.py --set-github-secret`).
2. Quiz: re-auth with `youtube.force-ssl` (`python src/upload.py --auth --set-github-secret`) so the comments actually post.
3. Quiz: decide on made-for-kids and set the channel audience in Studio to match.
4. Both: turn on 2-Step Verification, then in Studio go to **Earn → Get notified**. Create AdSense only when you apply.
5. Instagram: refresh the token before ~23-Nov, and check Professional dashboard → Monetization status.
6. Optional: add a `YT_DATA_API_KEY` secret so competitor + own-slot research runs.
