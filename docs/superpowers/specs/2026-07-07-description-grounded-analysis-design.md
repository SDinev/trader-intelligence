# Description-Grounded Analysis with Hallucination Guards — Design Spec

Date: 2026-07-07

## Problem

The first live runs produced a scannable brief, but the price levels were **hallucinated**. Confirmed by diagnostics:

- Levels matched 2023–2024 prices (AAPL 175/190, Gold ~2300), not the 2026 videos (AAPL ~312). Timestamp links did not land on the cited discussion.
- Logging Gemini's `usage_metadata` showed two regimes of `prompt_token_count`:
  - **~200 tokens** for three VerifiedInvesting videos — the video was **never ingested**; the model answered from the title alone and fabricated a full table.
  - **8k–103k tokens** for TraderNick/Soloway videos — the video **was** ingested.

Root cause: **Gemini silently fails to ingest some YouTube videos (restrictions/quota) and, under a forced JSON schema, hallucinates confident levels instead of erroring.** The pipeline trusts the output blindly. This violates the core success metric: *zero hallucinated price levels*.

A second finding: some creators (notably **@VerifiedInvesting**) publish detailed, per-symbol level breakdowns in the **video description** — authoritative, creator-authored text requiring no vision and carrying zero hallucination risk.

## Solution overview

Ground analysis in the authoritative **video description**, use the video only as a secondary source, and add guards that **detect silent video-ingestion failure and drop the levels it would have fabricated**. Add a retry queue so videos that yield nothing are re-tried (and so we can re-process the already-published hallucinated videos).

## Decisions (locked with user)

| Decision | Choice |
|---|---|
| Source strategy | Description + video together, **description authoritative**; video only adds levels the description didn't cover |
| Quota optimization | If description is substantive (**>400 chars**), analyze **description-only** — skip the video call entirely |
| Provenance | Every level carries `source` (`description`\|`video`) + verbatim `quote` |
| Hallucination guard | Token gate: if `prompt_token_count` < threshold (**~4000**, configurable), video was not ingested → **drop `video`-sourced levels**, keep `description`-sourced |
| Retry | Videos yielding no grounded level → `retry_queue`, re-analyzed each edition regardless of window, **max 2 attempts**, then given up |
| Backfill | Seed `retry_queue` with the two already-published hallucinated videos (`M8FhcMaccOI`, `_Fx85Gpj5sE`) so the new path re-does them |

## Data flow (per video)

1. `youtube_meta.fetch_video_metadata` already calls `videos.list?part=contentDetails,snippet`. Populate a new `Video.description` field from `snippet.description` (no new API call).
2. **Route by description richness:**
   - `len(description) > description_min_chars` (config, 400) → **description-only** analysis: send only the description text to Gemini; do not attach the video.
   - else → **combined** analysis: send description (may be short/empty) + the video URL part.
3. Gemini returns JSON where each asset's `support`/`resistance` levels each include `source` and `quote` (see schema below).
4. **Post-call guard:** read `response.usage_metadata.prompt_token_count`.
   - If the video was attached AND `prompt_token_count < video_ingested_min_tokens` (config, 4000) → the video was not ingested → **discard every level with `source == "video"`** for this video. `description`-sourced levels are kept.
   - Description-only calls skip this gate (no video was attached).
5. **Classify outcome:**
   - ≥1 surviving grounded level, or a truthful `no_levels_mentioned` → **success** → mark video processed; remove from retry queue if present.
   - 0 surviving levels because guarding dropped everything (video failed AND description too thin to yield levels) → **unextracted** → increment retry attempts.

## Response schema (per video)

```json
{
  "assets": [
    {
      "ticker": "GOLD",
      "support": [{"price": "3500", "timestamp_seconds": 315, "source": "video", "quote": "flush to thirty-five hundred"}],
      "resistance": [{"price": "4250", "timestamp_seconds": 0, "source": "description", "quote": "breakout above $4,250"}],
      "strategy": "..."
    }
  ],
  "macro_notes": "...",
  "no_levels_mentioned": false
}
```

- `source`: `"description"` or `"video"` (required per level).
- `quote`: verbatim phrase from the description or spoken in the video (required). Enables human trust-checking and is the anchor the guard/renderer rely on.
- `timestamp_seconds`: for `video` levels, the spoken moment; for `description` levels it may be 0 (link points to the video start) unless the description itself gives a timestamp.
- Prompt instructs: description is ground truth; report only explicitly-stated levels; omit rather than infer; if nothing, set `no_levels_mentioned` true and `assets` empty.

## State changes (`state.json`)

Add `retry_queue`: list of `{ "video_id": str, "attempts": int }`.

- Each run, before discovery: reload `retry_queue`, re-fetch metadata by ID (regardless of lookback window), and include those videos in the analysis set (skip any now found still-live → route to `pending_video_ids`).
- Unextracted outcome → `attempts + 1`; if `attempts >= max_retry_attempts` (config, 2) → drop from queue, mark processed, list under "couldn't extract."
- Success → remove from `retry_queue`, add to `processed_video_ids`.
- `pending_video_ids` (live) behavior unchanged.

Backfill: one-time edit adds `M8FhcMaccOI` and `_Fx85Gpj5sE` to `retry_queue` (attempts 0) and removes them from `processed_video_ids`.

## Brief changes (`report.py`)

- Each rendered level gets a source marker: superscript `ᴰ` (description) / `ⱽ` (video) appended to the linked price, e.g. `[3500](…&t=315s)ⱽ`. A short legend line explains the markers.
- New Pipeline Status line: **"Couldn't extract reliable levels (given up after retries):"** listing videos that exhausted attempts, and **"Retrying next edition:"** for videos still in the queue.
- Ticker-merge and case/alias normalization unchanged.

## Config additions (`config.yaml`)

```yaml
analysis:
  description_min_chars: 400        # >this -> description-only, skip video
  video_ingested_min_tokens: 4000  # prompt_token_count below this -> video not ingested
  max_retry_attempts: 2
```

## Components touched

| File | Change |
|---|---|
| `src/models.py` | `Video.description`; `PriceLevel.source` + `PriceLevel.quote` |
| `src/youtube_meta.py` | populate `description` from `snippet.description` |
| `src/analyze.py` | new schema (source/quote), description-in-prompt, description-only vs combined routing, token-gate that drops `video` levels, return outcome (grounded levels vs unextracted) |
| `src/main.py` | route by description richness; apply guard; classify success/unextracted; manage `retry_queue` (load, re-fetch, increment, give-up); pass through |
| `src/state.py` | `retry_queue` load/save + increment/give-up/success helpers |
| `src/report.py` | per-level source markers + legend; retry/given-up status lines |
| `config.yaml` | `analysis` block; backfill `state.json` |

## Testing (TDD)

Unit tests, no network:
- `youtube_meta`: description populated from snippet.
- `analyze`: parse levels with `source`/`quote`; description-only path attaches no video part; combined path attaches video; token-gate drops `video`-sourced levels when `prompt_token_count` below threshold and keeps `description`-sourced.
- `state`: retry_queue increment, give-up at max attempts, success removes from queue.
- `main`: video with rich description → description-only (no video attached); failed-ingestion + thin description → unextracted → retry_queue attempt incremented, not marked processed; failed-ingestion + rich description → description levels kept, marked processed; give-up after 2 → marked processed + listed.
- `report`: source markers render; retry/given-up status lines.

Live verification (dry-run on a branch, then real):
- Re-run the two backfilled videos; confirm levels now come from descriptions with `ᴰ` markers and match the actual description text; confirm no 2024-era phantom prices.
- Confirm a genuinely video-only creator (Soloway) still yields `ⱽ` levels when ingestion succeeds (high token count).

## Out of scope

- Improving video vision quality (higher `media_resolution`) — separate concern; LOW retained for quota.
- Parsing descriptions with regex instead of the LLM — the LLM handles varied formats better; descriptions are passed as text to the same call.
- Per-creator source configuration — routing is by description length, not a per-channel flag.
