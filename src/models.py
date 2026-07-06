from dataclasses import dataclass, field
from datetime import datetime


def youtube_watch_url(video_id: str, timestamp_seconds: int | None = None) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    if timestamp_seconds is not None:
        url += f"&t={timestamp_seconds}s"
    return url


@dataclass
class Video:
    video_id: str
    channel_handle: str
    title: str
    published_at: datetime
    duration_seconds: int
    is_live: bool

    @property
    def url(self) -> str:
        return youtube_watch_url(self.video_id)


@dataclass
class PriceLevel:
    price: str
    timestamp_seconds: int
    source_video_id: str

    @property
    def link(self) -> str:
        return youtube_watch_url(self.source_video_id, self.timestamp_seconds)


@dataclass
class AssetLevels:
    ticker: str
    support: list[PriceLevel] = field(default_factory=list)
    resistance: list[PriceLevel] = field(default_factory=list)
    strategy: str = ""


@dataclass
class VideoAnalysis:
    video: Video
    assets: list[AssetLevels] = field(default_factory=list)
    macro_notes: str = ""
    no_levels_mentioned: bool = False


@dataclass
class CreatorSummary:
    handle: str
    analyses: list[VideoAnalysis] = field(default_factory=list)


@dataclass
class Brief:
    edition: str
    generated_at: datetime
    creator_summaries: list[CreatorSummary] = field(default_factory=list)
    too_long_videos: list[Video] = field(default_factory=list)
    skipped_quota_videos: list[Video] = field(default_factory=list)
    failed_video_ids: list[str] = field(default_factory=list)
    pending_video_ids: list[str] = field(default_factory=list)
    discovery_failed_handles: list[str] = field(default_factory=list)
