from datetime import datetime, timezone

import feedparser
import requests

from models import Video

RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
REQUEST_TIMEOUT_SECONDS = 15


def parse_feed(xml_content: str, handle: str) -> list[Video]:
    parsed = feedparser.parse(xml_content)
    videos = []
    for entry in parsed.entries:
        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        videos.append(
            Video(
                video_id=entry.yt_videoid,
                channel_handle=handle,
                title=entry.title,
                published_at=published_at,
                duration_seconds=0,
                is_live=False,
            )
        )
    return videos


def filter_videos_in_window(
    videos: list[Video], start: datetime, end: datetime
) -> list[Video]:
    return [v for v in videos if start <= v.published_at <= end]


def fetch_channel_videos(channel_id: str, handle: str) -> list[Video]:
    url = RSS_URL_TEMPLATE.format(channel_id=channel_id)
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return parse_feed(response.text, handle)
