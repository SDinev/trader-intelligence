from datetime import datetime, timezone
from pathlib import Path

from discovery import fetch_channel_videos, filter_videos_in_window, parse_feed
from models import Video

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_parse_feed_extracts_video_fields():
    xml_content = FIXTURE.read_text()
    videos = parse_feed(xml_content, handle="@TraderNick")
    assert len(videos) == 2
    newest = next(v for v in videos if v.video_id == "newvid001")
    assert newest.title == "Pre-market game plan for tomorrow"
    assert newest.channel_handle == "@TraderNick"
    assert newest.published_at == datetime(2026, 7, 6, 6, 0, tzinfo=timezone.utc)


def test_filter_videos_in_window_excludes_outside_range():
    xml_content = FIXTURE.read_text()
    videos = parse_feed(xml_content, handle="@TraderNick")
    start = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    result = filter_videos_in_window(videos, start, end)
    assert [v.video_id for v in result] == ["newvid001"]


def test_fetch_channel_videos_requests_correct_url(monkeypatch):
    captured = {}

    class FakeResponse:
        text = FIXTURE.read_text()

        def raise_for_status(self):
            pass

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("discovery.requests.get", fake_get)

    videos = fetch_channel_videos("UCPCPE5MoI7DS1WiVB_-mzNw", "@TraderNick")

    assert "channel_id=UCPCPE5MoI7DS1WiVB_-mzNw" in captured["url"]
    assert len(videos) == 2
    assert all(isinstance(v, Video) for v in videos)
