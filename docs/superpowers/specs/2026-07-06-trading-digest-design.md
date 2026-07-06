# Trader Intelligence Digest — Design Spec

Date: 2026-07-06

## Problem

An active retail trader in Sofia (Europe/Sofia timezone) tracking the US market spends hours daily watching financial YouTube creators' live streams and recaps to extract actionable key levels (support, resistance, buy/sell targets). Local mobile automation cannot do this: iOS/Android background execution is capped well under the 1–2 minutes multimodal video analysis requires, and rendering creator channel pages directly triggers regional consent walls.

## Solution

A cloud-only pipeline, scheduled twice daily around US market open/close (adjusted to Sofia local time), that:
1. Discovers new videos from a curated creator roster via lightweight RSS (no page rendering, no consent walls).
2. Verifies each candidate's live/duration status via the YouTube Data API (static endpoint, no scraping).
3. Sends eligible finished videos directly (by URL) to Gemini Flash for multimodal analysis — no dependency on YouTube's auto-captions.
4. Renders a consolidated Markdown "Trader Intelligence Brief" (asset table + per-creator notes + transparency footer) to a GitHub Pages site.
5. Pings a Discord webhook with a short summary and link.

Everything runs on GitHub Actions cron — zero client compute, zero battery drain, no 30-second mobile execution ceiling.

## Cadence & windows

- **Morning edition** ~09:00 Europe/Sofia, 14h lookback — captures prior US close recaps, late-night macro, after-hours earnings.
- **Afternoon edition** ~17:30 Europe/Sofia, 10h lookback — captures US pre-market game plans, with a 45-min buffer after typical live streams end.
- Actions cron runs in UTC; Sofia flips EEST/EET twice a year, so each edition is scheduled via **two** UTC cron entries (one per DST state). The script checks actual Europe/Sofia wall-clock time at run start and exits quietly (no-op, no commit, no Discord ping) if invoked on the wrong DST-twin slot.
- Windows are discovery bounds only, not the source of correctness: a committed `state.json` tracks already-processed video IDs plus a pending queue for videos that were still live at scan time, guaranteeing exactly-once processing even where morning/afternoon windows overlap or a stream runs long.

## Ingestion

- Static per-channel RSS: `https://www.youtube.com/feeds/videos.xml?channel_id=<ID>`. No page rendering, no bot/consent walls, roster stored as plain channel IDs in `config.yaml`.
- RSS alone cannot distinguish "finished VOD" from "currently live" — a live stream appears in the feed as soon as it starts. Each candidate is confirmed via YouTube Data API v3 `videos.list` (`contentDetails.duration`, `snippet.liveBroadcastContent`). Still-live/upcoming entries go into the pending queue for the next run instead of being analyzed prematurely. Data API usage is a handful of units per run against a 10,000/day free quota.

## Analysis

- Gemini Flash multimodal call per video, YouTube URL passed directly as a `file_data` part — the model watches/listens to the actual stream, not a transcript.
- `media_resolution: LOW`, `temperature: 0`, strict JSON response schema. Prompt instructs the model to report **only** levels explicitly stated by the creator, omitting anything inferred, and to cite a timestamp (seconds into the video) for every level so it renders as a clickable `youtube.com/watch?v=<id>&t=<n>s` link. This is the hallucination guardrail: nothing enters the brief without a citation a human can jump to and verify.
- Videos longer than 2 hours (config: `quota.max_video_seconds_single`) are **not analyzed** — free-tier quota can't cover marathon streams at usable resolution. They're listed in the brief as "too long — watch manually" with a link, rather than silently dropped.
- Per-run quota budget (config: `quota.max_video_seconds_per_run`, ~3.5 video-hours) caps total video-seconds sent to Gemini; eligible videos are prioritized newest-first, anything over budget is listed as "skipped (quota)".
- Per-video failures (API error, malformed response) are caught individually and listed as "failed" — one bad video never aborts the whole run.

## Output

- Consolidated **Asset Breakdown Table**: Ticker | Support | Resistance/Gap Fills | Strategy/Notes | Source (timestamped link), built by merging per-video results across creators.
- Per-creator sections below the table with fuller notes.
- Transparency footer: which videos were analyzed / too-long / skipped-quota / failed / still-pending.
- If nothing was eligible in the window, the brief still renders with an explicit "No new updates for this window" heartbeat — confirming the pipeline ran rather than silently doing nothing.
- Rendered as Markdown under `docs/briefs/YYYY-MM-DD-{morning|afternoon}.md`, served by GitHub Pages (default Jekyll rendering of `/docs`); `docs/index.md` is regenerated each run as a newest-first archive index.
- Discord webhook receives a short summary (ticker list, 2–3 headline levels, Pages link) — under 2000 characters, since the full detail lives on the page, not in the chat message.

## Security

- No credential is ever committed. `GEMINI_API_KEY`, `YOUTUBE_API_KEY`, `DISCORD_WEBHOOK_URL` are read only from environment variables — GitHub Actions Secrets in CI, a local `.env` (gitignored) for manual runs.
- The repo (and therefore Pages output) is public; this is acceptable since it only contains code, public channel IDs, and briefs derived from public videos. No user-identifying or account data is stored anywhere.
- If the Discord webhook URL leaks, blast radius is limited to posting messages into one channel — regenerable instantly with no other access implied.

## Failure handling

- Per-video try/except as described above.
- If the whole run throws (e.g., RSS unreachable, Gemini API down), the Actions workflow's `if: failure()` step posts a Discord alert, so a dead pipeline is never silent — this satisfies the "verified placeholder" requirement even under total failure, not just the empty-window case.

## Out of scope (for this iteration)

- Mobile app / client UI of any kind.
- Non-YouTube sources.
- Editing/replacing already-published briefs after the fact.
- Multi-user support — single recipient (owner's Discord channel).
