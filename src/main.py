import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from google import genai

from budget import allocate
from edition import determine_edition, lookback_window
from discovery import fetch_channel_videos as real_fetch_channel_videos
from discovery import filter_videos_in_window
from models import Brief, CreatorSummary
from notify import post_discord_message
from report import discord_summary, list_brief_entries, render_brief_markdown, render_index_markdown
from state import filter_unprocessed, load_state, mark_pending, mark_processed, save_state
from youtube_meta import fetch_video_metadata as real_fetch_video_metadata
from analyze import analyze_video as real_analyze_video

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
STATE_PATH = REPO_ROOT / "state.json"
BRIEFS_DIR = REPO_ROOT / "docs" / "briefs"
INDEX_PATH = REPO_ROOT / "docs" / "index.md"


def run_pipeline(
    *,
    now_utc: datetime,
    config: dict,
    state: dict,
    fetch_channel_videos,
    fetch_video_metadata,
    analyze_video,
    gemini_client,
    youtube_api_key: str,
    forced_edition: str | None = None,
) -> dict | None:
    edition = determine_edition(now_utc, config, forced_edition=forced_edition)
    if edition is None:
        return None

    start, end = lookback_window(now_utc, config, edition)

    all_candidates = []
    discovery_failed_handles = []
    for entry in config["roster"]:
        try:
            videos = fetch_channel_videos(entry["channel_id"], entry["handle"])
        except Exception:
            discovery_failed_handles.append(entry["handle"])
            continue
        all_candidates.extend(filter_videos_in_window(videos, start, end))

    unprocessed = filter_unprocessed(all_candidates, state)
    enriched = fetch_video_metadata(unprocessed, youtube_api_key) if unprocessed else []

    live_videos = [v for v in enriched if v.is_live]
    finished_videos = [v for v in enriched if not v.is_live]

    allocation = allocate(finished_videos, config)
    eligible = allocation["eligible"]
    too_long = allocation["too_long"]
    skipped_quota = allocation["skipped_quota"]

    analyses_by_handle: dict[str, list] = {}
    failed_video_ids = []
    newly_processed_ids = [v.video_id for v in too_long]

    for video in eligible:
        try:
            analysis = analyze_video(gemini_client, video, config)
        except Exception:
            failed_video_ids.append(video.video_id)
            continue
        analyses_by_handle.setdefault(video.channel_handle, []).append(analysis)
        newly_processed_ids.append(video.video_id)

    creator_summaries = [
        CreatorSummary(handle=entry["handle"], analyses=analyses_by_handle[entry["handle"]])
        for entry in config["roster"]
        if entry["handle"] in analyses_by_handle
    ]

    brief = Brief(
        edition=edition,
        generated_at=now_utc,
        creator_summaries=creator_summaries,
        too_long_videos=too_long,
        skipped_quota_videos=skipped_quota,
        failed_video_ids=failed_video_ids,
        pending_video_ids=[v.video_id for v in live_videos],
        discovery_failed_handles=discovery_failed_handles,
    )

    new_state = mark_processed(state, newly_processed_ids)
    new_state = mark_pending(new_state, [v.video_id for v in live_videos])

    return {"brief": brief, "new_state": new_state}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=["morning", "afternoon"], default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG_PATH.read_text())
    state = load_state(STATE_PATH)
    now_utc = datetime.now(timezone.utc)

    gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    youtube_api_key = os.environ["YOUTUBE_API_KEY"]
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    result = run_pipeline(
        now_utc=now_utc,
        config=config,
        state=state,
        fetch_channel_videos=real_fetch_channel_videos,
        fetch_video_metadata=real_fetch_video_metadata,
        analyze_video=real_analyze_video,
        gemini_client=gemini_client,
        youtube_api_key=youtube_api_key,
        forced_edition=args.edition,
    )

    if result is None:
        print("Wrong DST-twin cron slot — no-op.")
        return

    brief = result["brief"]
    date_str = now_utc.strftime("%Y-%m-%d")
    brief_filename = f"{date_str}-{brief.edition}.md"
    brief_path = BRIEFS_DIR / brief_filename
    page_url = f"{config['pages']['base_url']}/briefs/{date_str}-{brief.edition}"

    markdown = render_brief_markdown(brief)
    print(markdown)

    if args.dry_run:
        print(f"[dry-run] would write {brief_path}, update state, and notify Discord.")
        return

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(markdown)

    INDEX_PATH.write_text(render_index_markdown(list_brief_entries(BRIEFS_DIR)))

    save_state(STATE_PATH, result["new_state"])

    if discord_webhook_url:
        post_discord_message(discord_webhook_url, discord_summary(brief, page_url))


if __name__ == "__main__":
    main()
