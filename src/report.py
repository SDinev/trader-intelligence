from pathlib import Path

from models import Brief, CreatorSummary


def normalize_ticker(raw: str, aliases: dict | None = None) -> str:
    canonical = raw.strip().upper()
    if aliases:
        # alias keys are matched case-insensitively
        alias_map = {k.strip().upper(): v.strip().upper() for k, v in aliases.items()}
        canonical = alias_map.get(canonical, canonical)
    return canonical


def _merge_assets_by_ticker(
    creator_summaries: list[CreatorSummary], ticker_aliases: dict | None = None
) -> dict:
    merged: dict[str, dict] = {}
    for summary in creator_summaries:
        for analysis in summary.analyses:
            for asset in analysis.assets:
                ticker = normalize_ticker(asset.ticker, ticker_aliases)
                bucket = merged.setdefault(
                    ticker, {"support": [], "resistance": [], "strategy_notes": []}
                )
                bucket["support"].extend(asset.support)
                bucket["resistance"].extend(asset.resistance)
                if asset.strategy:
                    bucket["strategy_notes"].append((summary.handle, asset.strategy))
    return merged


def _format_levels_cell(levels: list) -> str:
    if not levels:
        return "—"
    return "; ".join(f"[{lvl.price}]({lvl.link})" for lvl in levels)


def _format_strategy_cell(strategy_notes: list[tuple[str, str]]) -> str:
    if not strategy_notes:
        return "—"
    return "; ".join(f"{handle}: {note}" for handle, note in strategy_notes)


def render_brief_markdown(brief: Brief, ticker_aliases: dict | None = None) -> str:
    lines = [
        f"# Trader Intelligence Brief — {brief.edition.capitalize()} Edition",
        f"_Generated {brief.generated_at.isoformat()}_",
        "",
    ]

    has_content = bool(
        brief.creator_summaries
        or brief.too_long_videos
        or brief.skipped_quota_videos
        or brief.failed_video_ids
        or brief.discovery_failed_handles
        or brief.metadata_failed
    )
    if not has_content:
        lines.append("No new updates for this window.")
        lines.append("")
        return "\n".join(lines)

    merged_assets = _merge_assets_by_ticker(brief.creator_summaries, ticker_aliases)
    if merged_assets:
        lines.append("## Asset Breakdown")
        lines.append("")
        lines.append("| Ticker | Support | Resistance / Gap Fills | Strategy / Notes |")
        lines.append("|---|---|---|---|")
        for ticker in sorted(merged_assets):
            bucket = merged_assets[ticker]
            support_cell = _format_levels_cell(bucket["support"])
            resistance_cell = _format_levels_cell(bucket["resistance"])
            strategy_cell = _format_strategy_cell(bucket["strategy_notes"])
            lines.append(f"| {ticker} | {support_cell} | {resistance_cell} | {strategy_cell} |")
        lines.append("")

    for summary in brief.creator_summaries:
        lines.append(f"## {summary.handle}")
        lines.append("")
        for analysis in summary.analyses:
            lines.append(f"### [{analysis.video.title}]({analysis.video.url})")
            if analysis.no_levels_mentioned:
                lines.append("No specific levels mentioned.")
            if analysis.macro_notes:
                lines.append(analysis.macro_notes)
            lines.append("")

    lines.append("## Pipeline Status")
    lines.append("")
    if brief.too_long_videos:
        lines.append("**Too long to analyze (watch manually):**")
        for v in brief.too_long_videos:
            lines.append(f"- [{v.title}]({v.url})")
        lines.append("")
    if brief.skipped_quota_videos:
        lines.append("**Skipped (quota budget exhausted this run):**")
        for v in brief.skipped_quota_videos:
            lines.append(f"- [{v.title}]({v.url})")
        lines.append("")
    if brief.failed_video_ids:
        lines.append("**Failed to analyze:**")
        for video_id in brief.failed_video_ids:
            lines.append(f"- {video_id}")
        lines.append("")
    if brief.pending_video_ids:
        lines.append("**Still live — pending next edition:**")
        for video_id in brief.pending_video_ids:
            lines.append(f"- {video_id}")
        lines.append("")
    if brief.discovery_failed_handles:
        lines.append("**Could not fetch (channel feed unreachable):**")
        for handle in brief.discovery_failed_handles:
            lines.append(f"- {handle}")
        lines.append("")
    if brief.metadata_failed:
        lines.append(
            "**Video metadata lookup failed (YouTube Data API) — analysis skipped "
            "this run; videos will be retried next edition.**"
        )
        lines.append("")

    return "\n".join(lines)


def discord_summary(brief: Brief, page_url: str, ticker_aliases: dict | None = None) -> str:
    merged_assets = _merge_assets_by_ticker(brief.creator_summaries, ticker_aliases)
    tickers = sorted(merged_assets) if merged_assets else []

    lines = [f"**Trader Intelligence Brief — {brief.edition.capitalize()} Edition**"]
    if tickers:
        lines.append(f"Tickers: {', '.join(tickers)}")
    else:
        lines.append("No new updates for this window.")
    lines.append(page_url)
    return "\n".join(lines)


def list_brief_entries(briefs_dir: Path) -> list[dict]:
    briefs_dir = Path(briefs_dir)
    if not briefs_dir.exists():
        return []
    entries = []
    for path in briefs_dir.glob("*.md"):
        date, edition = path.stem.rsplit("-", 1)
        entries.append({"date": date, "edition": edition, "path": f"briefs/{path.name}"})
    return entries


def render_index_markdown(entries: list[dict]) -> str:
    lines = ["# Trader Intelligence Brief Archive", ""]
    sorted_entries = sorted(
        entries, key=lambda e: (e["date"], e["edition"]), reverse=True
    )
    for entry in sorted_entries:
        label = f"{entry['date']} — {entry['edition'].capitalize()} Edition"
        lines.append(f"- [{label}]({entry['path']})")
    return "\n".join(lines)
