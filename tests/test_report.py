from datetime import datetime, timezone

from models import AssetLevels, Brief, CreatorSummary, PriceLevel, Video, VideoAnalysis
from report import discord_summary, list_brief_entries, render_brief_markdown, render_index_markdown

GENERATED_AT = datetime(2026, 7, 6, 9, 5, tzinfo=timezone.utc)


def make_video(video_id: str, title: str) -> Video:
    return Video(
        video_id=video_id,
        channel_handle="@TraderNick",
        title=title,
        published_at=GENERATED_AT,
        duration_seconds=1200,
        is_live=False,
    )


def test_heartbeat_brief_when_nothing_eligible():
    brief = Brief(edition="morning", generated_at=GENERATED_AT)
    md = render_brief_markdown(brief)
    assert "No new updates for this window." in md


def test_asset_table_includes_ticker_and_levels():
    video = make_video("v1", "Pre-market plan")
    analysis = VideoAnalysis(
        video=video,
        assets=[
            AssetLevels(
                ticker="SPY",
                support=[PriceLevel(price="605.50", timestamp_seconds=120, source_video_id="v1")],
                resistance=[PriceLevel(price="612.00", timestamp_seconds=200, source_video_id="v1")],
                strategy="Watch for reclaim of 610 to go long.",
            )
        ],
    )
    brief = Brief(
        edition="morning",
        generated_at=GENERATED_AT,
        creator_summaries=[CreatorSummary(handle="@TraderNick", analyses=[analysis])],
    )
    md = render_brief_markdown(brief)
    assert "SPY" in md
    assert "605.50" in md
    assert "612.00" in md
    assert "https://www.youtube.com/watch?v=v1&t=120s" in md
    assert "Watch for reclaim of 610 to go long." in md


def test_asset_table_merges_same_ticker_across_videos():
    v1 = make_video("v1", "Morning recap")
    v2 = make_video("v2", "Afternoon plan")
    analysis1 = VideoAnalysis(
        video=v1,
        assets=[
            AssetLevels(
                ticker="QQQ",
                support=[PriceLevel(price="500.00", timestamp_seconds=10, source_video_id="v1")],
            )
        ],
    )
    analysis2 = VideoAnalysis(
        video=v2,
        assets=[
            AssetLevels(
                ticker="QQQ",
                resistance=[PriceLevel(price="510.00", timestamp_seconds=20, source_video_id="v2")],
            )
        ],
    )
    brief = Brief(
        edition="morning",
        generated_at=GENERATED_AT,
        creator_summaries=[
            CreatorSummary(handle="@TraderNick", analyses=[analysis1]),
            CreatorSummary(handle="@danielpronk", analyses=[analysis2]),
        ],
    )
    md = render_brief_markdown(brief)
    # exactly one QQQ row in the asset table (only 1 pipe-table line should start with "| QQQ")
    qqq_rows = [line for line in md.splitlines() if line.startswith("| QQQ ")]
    assert len(qqq_rows) == 1
    assert "500.00" in qqq_rows[0]
    assert "510.00" in qqq_rows[0]


def test_asset_table_merges_case_insensitive_tickers():
    v1 = make_video("v1", "recap A")
    v2 = make_video("v2", "recap B")
    a1 = VideoAnalysis(video=v1, assets=[AssetLevels(
        ticker="Gold", support=[PriceLevel(price="2300", timestamp_seconds=10, source_video_id="v1")])])
    a2 = VideoAnalysis(video=v2, assets=[AssetLevels(
        ticker="GOLD", resistance=[PriceLevel(price="3600", timestamp_seconds=20, source_video_id="v2")])])
    brief = Brief(
        edition="morning",
        generated_at=GENERATED_AT,
        creator_summaries=[
            CreatorSummary(handle="@a", analyses=[a1]),
            CreatorSummary(handle="@b", analyses=[a2]),
        ],
    )
    md = render_brief_markdown(brief)
    gold_rows = [line for line in md.splitlines() if line.startswith("| GOLD ")]
    assert len(gold_rows) == 1
    assert "2300" in gold_rows[0] and "3600" in gold_rows[0]


def test_asset_table_applies_configured_ticker_aliases():
    v1 = make_video("v1", "recap A")
    v2 = make_video("v2", "recap B")
    a1 = VideoAnalysis(video=v1, assets=[AssetLevels(
        ticker="XAUUSD", support=[PriceLevel(price="4200", timestamp_seconds=10, source_video_id="v1")])])
    a2 = VideoAnalysis(video=v2, assets=[AssetLevels(
        ticker="GOLD", support=[PriceLevel(price="3500", timestamp_seconds=20, source_video_id="v2")])])
    brief = Brief(
        edition="morning",
        generated_at=GENERATED_AT,
        creator_summaries=[
            CreatorSummary(handle="@a", analyses=[a1]),
            CreatorSummary(handle="@b", analyses=[a2]),
        ],
    )
    md = render_brief_markdown(brief, ticker_aliases={"XAUUSD": "GOLD"})
    gold_rows = [line for line in md.splitlines() if line.startswith("| GOLD ")]
    xau_rows = [line for line in md.splitlines() if line.startswith("| XAUUSD ")]
    assert len(gold_rows) == 1
    assert len(xau_rows) == 0
    assert "4200" in gold_rows[0] and "3500" in gold_rows[0]


def test_footer_lists_too_long_videos():
    too_long_video = make_video("v3", "3-hour marathon stream")
    brief = Brief(edition="morning", generated_at=GENERATED_AT, too_long_videos=[too_long_video])
    md = render_brief_markdown(brief)
    assert "3-hour marathon stream" in md
    assert too_long_video.url in md


def test_footer_lists_discovery_failures():
    brief = Brief(
        edition="afternoon",
        generated_at=GENERATED_AT,
        discovery_failed_handles=["@VerifiedInvesting"],
    )
    md = render_brief_markdown(brief)
    assert "@VerifiedInvesting" in md
    assert "could not fetch" in md.lower() or "unreachable" in md.lower()


def test_discord_summary_contains_tickers_and_link():
    video = make_video("v1", "Pre-market plan")
    analysis = VideoAnalysis(
        video=video,
        assets=[AssetLevels(ticker="SPY", support=[], resistance=[], strategy="")],
    )
    brief = Brief(
        edition="morning",
        generated_at=GENERATED_AT,
        creator_summaries=[CreatorSummary(handle="@TraderNick", analyses=[analysis])],
    )
    summary = discord_summary(brief, "https://sdinev.github.io/trader-intelligence/briefs/2026-07-06-morning")
    assert len(summary) <= 2000
    assert "SPY" in summary
    assert "https://sdinev.github.io/trader-intelligence/briefs/2026-07-06-morning" in summary
    assert "morning" in summary.lower()


def test_render_index_lists_newest_first():
    entries = [
        {"date": "2026-07-05", "edition": "morning", "path": "briefs/2026-07-05-morning.md"},
        {"date": "2026-07-06", "edition": "morning", "path": "briefs/2026-07-06-morning.md"},
    ]
    md = render_index_markdown(entries)
    idx_06 = md.index("2026-07-06-morning")
    idx_05 = md.index("2026-07-05-morning")
    assert idx_06 < idx_05


def test_list_brief_entries_parses_filenames(tmp_path):
    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    (briefs_dir / "2026-07-05-morning.md").write_text("x")
    (briefs_dir / "2026-07-06-afternoon.md").write_text("x")

    entries = list_brief_entries(briefs_dir)

    assert {"date": "2026-07-05", "edition": "morning", "path": "briefs/2026-07-05-morning.md"} in entries
    assert {"date": "2026-07-06", "edition": "afternoon", "path": "briefs/2026-07-06-afternoon.md"} in entries
    assert len(entries) == 2


def test_list_brief_entries_missing_dir_returns_empty(tmp_path):
    assert list_brief_entries(tmp_path / "nonexistent") == []
