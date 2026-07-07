from datetime import datetime, timedelta, timezone

from main import run_pipeline
from models import Video
from analyze import analysis_is_grounded  # noqa: F401  (ensures module import parity)

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
    "analysis": {"max_retry_attempts": 2},
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


def make_unextracted_analysis(video):
    # video attached, not ingested, no surviving levels -> not grounded
    from models import VideoAnalysis
    return VideoAnalysis(video=video, assets=[], macro_notes="", no_levels_mentioned=False,
                         video_attached=True, video_ingested=False)


def make_grounded_analysis(video):
    from models import VideoAnalysis, AssetLevels, PriceLevel
    return VideoAnalysis(
        video=video,
        assets=[AssetLevels(ticker="SPY", support=[
            PriceLevel(price="1", timestamp_seconds=0, source_video_id=video.video_id, source="description")])],
        video_attached=False, video_ingested=False,
    )


def test_unextracted_video_enters_retry_queue_not_processed():
    video = make_video("v1", "@TraderNick")
    result = run_pipeline(
        now_utc=NOW_UTC, config=CONFIG, state=empty_state(),
        fetch_channel_videos=lambda cid, h: [video] if h == "@TraderNick" else [],
        fetch_video_metadata=lambda vids, key: vids,
        analyze_video=lambda client, v, cfg: make_unextracted_analysis(v),
        gemini_client=object(), youtube_api_key="k",
    )
    assert result["new_state"]["processed_video_ids"] == []
    ids = [e["video_id"] for e in result["new_state"]["retry_queue"]]
    assert ids == ["v1"]
    assert result["new_state"]["retry_queue"][0]["attempts"] == 1
    assert "v1" in result["brief"].retrying_video_ids


def test_retry_video_gives_up_after_max_attempts():
    video = make_video("v1", "@TraderNick")
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "v1", "channel_handle": "@TraderNick", "title": "t",
         "published_at": NOW_UTC.isoformat(), "attempts": 1}
    ]}
    # CONFIG max_retry_attempts defaults to 2 -> attempt becomes 2 -> give up
    result = run_pipeline(
        now_utc=NOW_UTC, config={**CONFIG, "analysis": {"max_retry_attempts": 2}}, state=state,
        fetch_channel_videos=lambda cid, h: [],
        fetch_video_metadata=lambda vids, key: vids,
        analyze_video=lambda client, v, cfg: make_unextracted_analysis(v),
        gemini_client=object(), youtube_api_key="k",
    )
    assert result["new_state"]["retry_queue"] == []
    assert "v1" in result["new_state"]["processed_video_ids"]
    assert "v1" in result["brief"].given_up_video_ids


def test_grounded_retry_video_is_processed_and_removed_from_queue():
    video = make_video("v1", "@TraderNick")
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "v1", "channel_handle": "@TraderNick", "title": "t",
         "published_at": NOW_UTC.isoformat(), "attempts": 1}
    ]}
    result = run_pipeline(
        now_utc=NOW_UTC, config=CONFIG, state=state,
        fetch_channel_videos=lambda cid, h: [],
        fetch_video_metadata=lambda vids, key: vids,
        analyze_video=lambda client, v, cfg: make_grounded_analysis(v),
        gemini_client=object(), youtube_api_key="k",
    )
    assert result["new_state"]["retry_queue"] == []
    assert "v1" in result["new_state"]["processed_video_ids"]
    handles = [cs.handle for cs in result["brief"].creator_summaries]
    assert "@TraderNick" in handles


def test_retry_video_consumes_attempt_when_analysis_raises():
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "v1", "channel_handle": "@TraderNick", "title": "t",
         "published_at": NOW_UTC.isoformat(), "attempts": 0}
    ]}

    def boom(client, v, cfg):
        raise RuntimeError("gemini exploded")

    result = run_pipeline(
        now_utc=NOW_UTC, config={**CONFIG, "analysis": {"max_retry_attempts": 2}}, state=state,
        fetch_channel_videos=lambda cid, h: [],
        fetch_video_metadata=lambda vids, key: vids,
        analyze_video=boom,
        gemini_client=object(), youtube_api_key="k",
    )
    ids = [e["video_id"] for e in result["new_state"]["retry_queue"]]
    assert ids == ["v1"]
    attempts = {e["video_id"]: e["attempts"] for e in result["new_state"]["retry_queue"]}
    assert attempts["v1"] == 1
    assert "v1" in result["brief"].retrying_video_ids
    assert "v1" not in result["new_state"]["processed_video_ids"]


def test_retry_video_gives_up_when_it_disappears_from_api():
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "v1", "channel_handle": "@TraderNick", "title": "t",
         "published_at": NOW_UTC.isoformat(), "attempts": 1}
    ]}
    result = run_pipeline(
        now_utc=NOW_UTC, config={**CONFIG, "analysis": {"max_retry_attempts": 2}}, state=state,
        fetch_channel_videos=lambda cid, h: [],
        fetch_video_metadata=lambda vids, key: [],  # video vanished from the API
        analyze_video=lambda client, v, cfg: make_grounded_analysis(v),
        gemini_client=object(), youtube_api_key="k",
    )
    assert result["new_state"]["retry_queue"] == []
    assert "v1" in result["brief"].given_up_video_ids
    assert "v1" in result["new_state"]["processed_video_ids"]


def test_retry_video_skipped_for_quota_keeps_attempts():
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "v1", "channel_handle": "@TraderNick", "title": "t",
         "published_at": NOW_UTC.isoformat(), "attempts": 1}
    ]}
    quota_config = {**CONFIG,
                    "quota": {"max_video_seconds_per_run": 0, "max_video_seconds_single": 7200},
                    "analysis": {"max_retry_attempts": 2}}

    def enrich(vids, key):
        # return the retry stub as a finished, analyzable video (duration>0)
        return [v.__class__(**{**v.__dict__, "duration_seconds": 600, "is_live": False}) for v in vids]

    result = run_pipeline(
        now_utc=NOW_UTC, config=quota_config, state=state,
        fetch_channel_videos=lambda cid, h: [],
        fetch_video_metadata=enrich,
        analyze_video=lambda client, v, cfg: make_grounded_analysis(v),
        gemini_client=object(), youtube_api_key="k",
    )
    ids = [e["video_id"] for e in result["new_state"]["retry_queue"]]
    assert ids == ["v1"]
    attempts = {e["video_id"]: e["attempts"] for e in result["new_state"]["retry_queue"]}
    assert attempts["v1"] == 1
    assert "v1" not in result["new_state"]["processed_video_ids"]
    assert "v1" not in result["brief"].given_up_video_ids


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


def test_metadata_api_failure_degrades_instead_of_crashing():
    video = make_video("v1", "@TraderNick")
    analyze_calls = []

    def failing_meta(videos, api_key):
        raise RuntimeError("YouTube Data API returned 403: Forbidden")

    result = run_pipeline(
        now_utc=NOW_UTC,
        config=CONFIG,
        state=empty_state(),
        fetch_channel_videos=lambda channel_id, handle: [video] if handle == "@TraderNick" else [],
        fetch_video_metadata=failing_meta,
        analyze_video=lambda client, v, config: analyze_calls.append(v.video_id) or make_analysis_for(v),
        gemini_client=object(),
        youtube_api_key="yt-key",
    )
    assert result is not None
    assert analyze_calls == []  # can't safely analyze without live/duration info
    assert result["brief"].metadata_failed is True
    # videos not marked processed, so a later healthy run still picks them up
    assert result["new_state"]["processed_video_ids"] == []


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
