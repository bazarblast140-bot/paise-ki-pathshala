# Instagram Reel cross-post — names to add

GitHub → this repo → **Settings → Secrets and variables → Actions**

Fill the **values** yourself. Names must match exactly.

## Secrets (Secret)

| Name | Kya daalna hai |
|------|----------------|
| `IG_ACCESS_TOKEN` | Instagram / Facebook Page long-lived token (HEYGEN FactVizer wala same chalega) |
| `MEDIA_REPO_TOKEN` | GitHub PAT with `contents:write` on the public media repo (optional if default token can write to MEDIA_REPO) |

## Variables (Variable — not secret)

| Name | Suggested value |
|------|-----------------|
| `IG_USER_ID` | Instagram Business account ID (numbers, e.g. `17841...`) |
| `IG_SURFACE` | `facebook` |
| `MEDIA_REPO` | `bazarblast140-bot/HEYGEN--INSTAGRAM-` |

## Notes

- Sirf **short** (9:16) videos IG pe jaati hain.
- `IG_CROSSPOST` workflow automatically `1` sets for short runs.
- Is repo private hai — isliye video public `MEDIA_REPO` pe host hota hai taaki Instagram fetch kar sake.
- Secrets missing hone par IG skip; YouTube normal.
