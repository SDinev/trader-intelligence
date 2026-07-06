from datetime import datetime, timezone

from models import PriceLevel, Video, youtube_watch_url


def test_youtube_watch_url_without_timestamp():
    assert youtube_watch_url("abc123") == "https://www.youtube.com/watch?v=abc123"


def test_youtube_watch_url_with_timestamp():
    assert (
        youtube_watch_url("abc123", timestamp_seconds=90)
        == "https://www.youtube.com/watch?v=abc123&t=90s"
    )


def test_video_url_property_uses_video_id():
    video = Video(
        video_id="xyz789",
        channel_handle="@TraderNick",
        title="Pre-market plan",
        published_at=datetime(2026, 7, 6, 6, 0, tzinfo=timezone.utc),
        duration_seconds=1800,
        is_live=False,
    )
    assert video.url == "https://www.youtube.com/watch?v=xyz789"


def test_price_level_link_includes_video_timestamp():
    level = PriceLevel(price="605.50", timestamp_seconds=245, source_video_id="xyz789")
    assert level.link == "https://www.youtube.com/watch?v=xyz789&t=245s"
