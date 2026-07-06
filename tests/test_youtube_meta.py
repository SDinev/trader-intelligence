from datetime import datetime, timezone

from models import Video
from youtube_meta import fetch_video_metadata, parse_iso8601_duration, parse_videos_list_response


def make_video(video_id: str) -> Video:
    return Video(
        video_id=video_id,
        channel_handle="@TraderNick",
        title=f"title-{video_id}",
        published_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        duration_seconds=0,
        is_live=False,
    )


def test_parse_iso8601_duration_hours_minutes_seconds():
    assert parse_iso8601_duration("PT1H30M15S") == 5415


def test_parse_iso8601_duration_minutes_only():
    assert parse_iso8601_duration("PT45M") == 2700


def test_parse_iso8601_duration_seconds_only():
    assert parse_iso8601_duration("PT30S") == 30


def test_parse_iso8601_duration_live_stream_p0d_returns_zero():
    # YouTube reports "P0D" (no time component) for live/upcoming broadcasts
    assert parse_iso8601_duration("P0D") == 0


def test_parse_iso8601_duration_unparseable_returns_zero():
    assert parse_iso8601_duration("") == 0


def test_parse_videos_list_response_sets_duration_and_live_status():
    videos_by_id = {"v1": make_video("v1"), "v2": make_video("v2")}
    api_response = {
        "items": [
            {
                "id": "v1",
                "contentDetails": {"duration": "PT25M"},
                "snippet": {"liveBroadcastContent": "none"},
            },
            {
                "id": "v2",
                "contentDetails": {"duration": "P0D"},
                "snippet": {"liveBroadcastContent": "live"},
            },
        ]
    }
    result = parse_videos_list_response(api_response, videos_by_id)
    result_by_id = {v.video_id: v for v in result}

    assert result_by_id["v1"].duration_seconds == 1500
    assert result_by_id["v1"].is_live is False
    assert result_by_id["v1"].title == "title-v1"

    assert result_by_id["v2"].is_live is True
    assert result_by_id["v2"].duration_seconds == 0


def test_upcoming_broadcast_is_treated_as_not_finished():
    videos_by_id = {"v3": make_video("v3")}
    api_response = {
        "items": [
            {
                "id": "v3",
                "contentDetails": {"duration": "P0D"},
                "snippet": {"liveBroadcastContent": "upcoming"},
            }
        ]
    }
    result = parse_videos_list_response(api_response, videos_by_id)
    assert result[0].is_live is True  # not "none" -> not an analyzable finished VOD


def test_fetch_video_metadata_raises_with_response_body_on_error(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = '{"error": {"message": "YouTube Data API v3 has not been used", "status": "PERMISSION_DENIED"}}'

        def json(self):
            return {}

        def raise_for_status(self):
            raise AssertionError("should not be called; we surface body first")

    monkeypatch.setattr("youtube_meta.requests.get", lambda url, params=None, timeout=None: FakeResponse())

    import pytest

    with pytest.raises(RuntimeError) as exc:
        fetch_video_metadata([make_video("v1")], api_key="BAD_KEY")

    assert "403" in str(exc.value)
    assert "PERMISSION_DENIED" in str(exc.value)


def test_fetch_video_metadata_calls_api_with_ids_and_key(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "items": [
                    {
                        "id": "v1",
                        "contentDetails": {"duration": "PT10M"},
                        "snippet": {"liveBroadcastContent": "none"},
                    }
                ]
            }

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse()

    monkeypatch.setattr("youtube_meta.requests.get", fake_get)

    videos = [make_video("v1")]
    result = fetch_video_metadata(videos, api_key="TEST_KEY")

    assert captured["params"]["id"] == "v1"
    assert captured["params"]["key"] == "TEST_KEY"
    assert result[0].duration_seconds == 600
