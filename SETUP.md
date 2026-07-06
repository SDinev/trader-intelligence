# Setup — Trader Intelligence Digest

All code and tests were written and verified locally. These are the remaining steps to get it running live on GitHub, targeting **https://github.com/SDinev/trader-intelligence/**.

## Status

- ✅ **Step 1 — Code pushed.** Initial commit `481baa7` (31 files) is live on `main`, authored as `SDinev`. Pushed over HTTPS using the `SDinev` `gh` token (SSH was avoided because the machine's `id_ed25519` key belongs to a separate work account). The token needed the `workflow` scope added (`gh auth refresh -h github.com -s workflow`) before the push would accept `.github/workflows/digest.yml`.
- ✅ **Step 3/4 — Secrets added.** `GEMINI_API_KEY`, `YOUTUBE_API_KEY`, `DISCORD_WEBHOOK_URL` are set as Actions repository secrets.
- ⬜ **Step 2 — Enable Pages** (below)
- ⬜ **Step 5/6 — First dry run, then first live run** (below)

## 1. Push the code — DONE

For reference, this is how it was pushed (already completed):

```bash
cd "Trading Briefing"
git init && git branch -M main
git config user.name "SDinev"
git config user.email "13866657+SDinev@users.noreply.github.com"
git add -A
git commit -m "Initial pipeline: ..."
git remote add origin https://github.com/SDinev/trader-intelligence.git
# HTTPS push authenticated via: gh auth git-credential (SDinev token, workflow scope)
git push -u origin main
```

## 2. Enable GitHub Pages

Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder: `/docs`.

The archive will be live at `https://sdinev.github.io/trader-intelligence/` (matches `pages.base_url` in [config.yaml](config.yaml) — update that value if the repo/org name differs).

## 3. Get the three API keys / secrets

| Secret name | Where to get it |
|---|---|
| `GEMINI_API_KEY` | You already have this from Google AI Studio. |
| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → enable **YouTube Data API v3** → Credentials → Create API key. Free quota (10,000 units/day) is far more than this pipeline uses (~20/day). |
| `DISCORD_WEBHOOK_URL` | In Discord: target channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL. |

## 4. Add them as Actions Secrets

Repo → Settings → Secrets and variables → Actions → New repository secret, for each of the three above. They are encrypted at rest, masked in logs, and never touch the repo contents — safe in a public repo.

For local testing, instead copy [.env.example](.env.example) to `.env` and fill it in (it's gitignored) — `main.py` reads these from the process environment either way, so `export $(cat .env | xargs)` before running locally works, or use `python-dotenv` if preferred.

## 5. First dry run

Once secrets are set:

Repo → Actions → "Trader Intelligence Digest" → Run workflow → tick `dry_run` → Run.

Check the job log: it should print a rendered Markdown brief (or "No new updates for this window" if nothing in the roster published recently) without committing anything or posting to Discord.

## 6. First live run

Run the workflow again without `dry_run`. Confirm:
- A new file appears under `docs/briefs/`
- `docs/index.md` lists it
- The Pages site (may take a minute to redeploy) shows the brief
- A message arrives in the Discord channel with a link

## 7. Let the schedule take over

No further action needed — `.github/workflows/digest.yml` already has both DST-twin cron entries for each edition. Confirm after one real day that only one of each pair fires meaningfully (the other should log "Wrong DST-twin cron slot — no-op." and exit cleanly).
