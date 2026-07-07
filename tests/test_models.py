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


def test_price_level_carries_source_and_quote():
    level = PriceLevel(
        price="605.50", timestamp_seconds=245, source_video_id="xyz789",
        source="description", quote="support at 605.50",
    )
    assert level.source == "description"
    assert level.quote == "support at 605.50"
    assert level.link == "https://www.youtube.com/watch?v=xyz789&t=245s"


def test_price_level_source_defaults_to_video():
    level = PriceLevel(price="1", timestamp_seconds=0, source_video_id="a")
    assert level.source == "video"
    assert level.quote == ""


def test_video_has_description_field_default_empty():
    from datetime import datetime, timezone
    v = Video(
        video_id="a", channel_handle="@h", title="t",
        published_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        duration_seconds=1, is_live=False,
    )
    assert v.description == ""
