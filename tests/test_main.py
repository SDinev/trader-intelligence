from datetime import datetime, timedelta, timezone

from main import run_pipeline
from models import Video

CONFIG = {
    "timezone": "Europe/Sofia",
    "editions": {
        "morning": {"target_hour": 9, "target_minute": 0, "lookback_hours": 14},
        "afternoon": {"target_hour": 17, "target_minute": 30, "lookback_hours": 10},
    },
    "roster": [
        {"handle": "@TraderNick", "channel_id": "chan-nick"},
        {"handle": "@danielpronk", "channel_id": "chan-daniel"},
    ],
    "quota": {"max_video_seconds_per_run": 100000, "max_video_seconds_single": 7200},
    "gemini": {"model": "gemini-flash-latest", "media_resolution": "MEDIA_RESOLUTION_LOW", "temperature": 0},
}

NOW_UTC = datetime(2026, 7, 6, 6, 5, tzinfo=timezone.utc)  # 09:05 Sofia (EEST) -> morning


def make_video(video_id, handle, minutes_ago=10, duration_seconds=600, is_live=False):
    return Video(
        video_id=video_id,
        channel_handle=handle,
        title=f"title-{video_id}",
        published_at=NOW_UTC - timedelta(minutes=minutes_ago),
        duration_seconds=duration_seconds,
        is_live=is_live,
    )


def empty_state():
    return {"processed_video_ids": [], "pending_video_ids": []}


def make_analysis_for(video):
    from models import VideoAnalysis

    return VideoAnalysis(video=video, assets=[], macro_notes="ok", no_levels_mentioned=True)


def test_returns_none_on_wrong_dst_twin_slot():
    wrong_slot_time = datetime(2026, 7, 6, 7, 0, tzinfo=timezone.utc)  # 10:00 Sofia, no edition match
    result = run_pipeline(
        now_utc=wrong_slot_time,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=lambda channel_id, handle: [],
        fetch_video_metadata=lambda videos, api_key: videos,
        analyze_video=lambda client, video, config: make_analysis_for(video),
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert result is None


def test_already_processed_video_excluded_and_not_analyzed():
    video = make_video("v1", "@TraderNick")
    analyze_calls = []

    def fake_analyze(client, v, config):
        analyze_calls.append(v.video_id)
        return make_analysis_for(v)

    state = {"processed_video_ids": ["v1"], "pending_video_ids": []}
    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=state,
        fetch_channel_videos=lambda channel_id, handle: [video] if handle == "@TraderNick" else [],
        fetch_video_metadata=lambda videos, api_key: videos,
        analyze_video=fake_analyze,
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert analyze_calls == []
    assert result["new_state"]["processed_video_ids"] == ["v1"]


def test_live_video_goes_to_pending_and_is_not_analyzed():
    video = make_video("v-live", "@TraderNick", is_live=False)  # RSS doesn't know live status yet

    def fake_meta(videos, api_key):
        # simulate the metadata API reporting this one as still live
        return [v.__class__(**{**v.__dict__, "is_live": True}) for v in videos]

    analyze_calls = []

    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=lambda channel_id, handle: [video] if handle == "@TraderNick" else [],
        fetch_video_metadata=fake_meta,
        analyze_video=lambda client, v, config: analyze_calls.append(v.video_id) or make_analysis_for(v),
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert analyze_calls == []
    assert "v-live" in result["new_state"]["pending_video_ids"]
    assert result["new_state"]["processed_video_ids"] == []


def test_too_long_video_is_not_analyzed_and_marked_processed():
    video = make_video("v-long", "@TraderNick", duration_seconds=8000)
    analyze_calls = []

    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=lambda channel_id, handle: [video] if handle == "@TraderNick" else [],
        fetch_video_metadata=lambda videos, api_key: videos,
        analyze_video=lambda client, v, config: analyze_calls.append(v.video_id) or make_analysis_for(v),
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert analyze_calls == []
    assert result["brief"].too_long_videos == [video]
    assert result["new_state"]["processed_video_ids"] == ["v-long"]


def test_analysis_failure_is_isolated_and_not_marked_processed():
    good_video = make_video("v-good", "@TraderNick")
    bad_video = make_video("v-bad", "@danielpronk")

    def fake_analyze(client, v, config):
        if v.video_id == "v-bad":
            raise RuntimeError("gemini exploded")
        return make_analysis_for(v)

    def fake_fetch(channel_id, handle):
        if handle == "@TraderNick":
            return [good_video]
        if handle == "@danielpronk":
            return [bad_video]
        return []

    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=fake_fetch,
        fetch_video_metadata=lambda videos, api_key: videos,
        analyze_video=fake_analyze,
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert result["brief"].failed_video_ids == ["v-bad"]
    assert "v-good" in result["new_state"]["processed_video_ids"]
    assert "v-bad" not in result["new_state"]["processed_video_ids"]
    handles_with_content = [cs.handle for cs in result["brief"].creator_summaries]
    assert "@TraderNick" in handles_with_content
    assert "@danielpronk" not in handles_with_content


def test_failing_channel_fetch_does_not_crash_run():
    good_video = make_video("v-good", "@danielpronk")

    def fake_fetch(channel_id, handle):
        if handle == "@TraderNick":
            raise RuntimeError("404 Not Found for RSS feed")
        if handle == "@danielpronk":
            return [good_video]
        return []

    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=fake_fetch,
        fetch_video_metadata=lambda videos, api_key: videos,
        analyze_video=lambda client, v, config: make_analysis_for(v),
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert result is not None
    assert "@TraderNick" in result["brief"].discovery_failed_handles
    assert "v-good" in result["new_state"]["processed_video_ids"]


def test_no_eligible_videos_still_returns_heartbeat_brief():
    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=lambda channel_id, handle: [],
        fetch_video_metadata=lambda videos, api_key: videos,
        analyze_video=lambda client, v, config: make_analysis_for(v),
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert result is not None
    assert result["brief"].creator_summaries == []
    assert result["brief"].edition == "morning"
