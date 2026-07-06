from datetime import datetime, timedelta, timezone

from budget import allocate
from models import Video

CONFIG = {
    "quota": {
        "max_video_seconds_per_run": 3600,
        "max_video_seconds_single": 7200,
    }
}

BASE_TIME = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)


def make_video(video_id: str, duration_seconds: int, minutes_before_base: int) -> Video:
    return Video(
        video_id=video_id,
        channel_handle="@TraderNick",
        title=video_id,
        published_at=BASE_TIME - timedelta(minutes=minutes_before_base),
        duration_seconds=duration_seconds,
        is_live=False,
    )


def test_videos_over_single_cap_go_to_too_long():
    video = make_video("v1", duration_seconds=8000, minutes_before_base=0)
    result = allocate([video], CONFIG)
    assert result["too_long"] == [video]
    assert result["eligible"] == []
    assert result["skipped_quota"] == []


def test_budget_selects_newest_first_until_cap_exceeded():
    v_oldest = make_video("v_oldest", duration_seconds=1000, minutes_before_base=120)
    v_middle = make_video("v_middle", duration_seconds=1500, minutes_before_base=60)
    v_newest = make_video("v_newest", duration_seconds=2000, minutes_before_base=0)

    # input order deliberately not sorted
    result = allocate([v_middle, v_oldest, v_newest], CONFIG)

    assert result["eligible"] == [v_newest, v_middle]
    assert result["skipped_quota"] == [v_oldest]
    assert result["too_long"] == []


def test_videos_within_budget_are_all_eligible():
    v1 = make_video("v1", duration_seconds=600, minutes_before_base=10)
    v2 = make_video("v2", duration_seconds=600, minutes_before_base=0)
    result = allocate([v1, v2], CONFIG)
    assert result["eligible"] == [v2, v1]
    assert result["skipped_quota"] == []
