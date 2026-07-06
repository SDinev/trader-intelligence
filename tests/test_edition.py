from datetime import datetime, timedelta, timezone

import pytest

from edition import determine_edition, lookback_window

CONFIG = {
    "timezone": "Europe/Sofia",
    "editions": {
        "morning": {"target_hour": 9, "target_minute": 0, "lookback_hours": 14},
        "afternoon": {"target_hour": 17, "target_minute": 30, "lookback_hours": 10},
    },
}


def test_morning_edition_matches_in_eest_summer():
    # 2026-07-06 09:05 Sofia (EEST, UTC+3) == 06:05 UTC
    now_utc = datetime(2026, 7, 6, 6, 5, tzinfo=timezone.utc)
    assert determine_edition(now_utc, CONFIG) == "morning"


def test_morning_edition_matches_in_eet_winter_dst_twin():
    # 2026-01-06 09:05 Sofia (EET, UTC+2) == 07:05 UTC — the DST-twin cron slot
    now_utc = datetime(2026, 1, 6, 7, 5, tzinfo=timezone.utc)
    assert determine_edition(now_utc, CONFIG) == "morning"


def test_afternoon_edition_matches():
    # 2026-07-06 17:35 Sofia (EEST) == 14:35 UTC
    now_utc = datetime(2026, 7, 6, 14, 35, tzinfo=timezone.utc)
    assert determine_edition(now_utc, CONFIG) == "afternoon"


def test_wrong_dst_twin_slot_returns_none():
    # A run fired for the "winter" UTC cron time, but it's actually summer —
    # Sofia local time lands nowhere near either edition target.
    # e.g. cron fires at 07:00 UTC in July (would-be winter morning slot),
    # which in EEST summer is 10:00 Sofia — outside tolerance of 09:00.
    now_utc = datetime(2026, 7, 6, 7, 0, tzinfo=timezone.utc)
    assert determine_edition(now_utc, CONFIG) is None


def test_forced_edition_overrides_time_check():
    now_utc = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    assert determine_edition(now_utc, CONFIG, forced_edition="afternoon") == "afternoon"


def test_lookback_window_morning():
    now_utc = datetime(2026, 7, 6, 6, 5, tzinfo=timezone.utc)
    start, end = lookback_window(now_utc, CONFIG, "morning")
    assert end == now_utc
    assert start == now_utc - timedelta(hours=14)


def test_lookback_window_afternoon():
    now_utc = datetime(2026, 7, 6, 14, 35, tzinfo=timezone.utc)
    start, end = lookback_window(now_utc, CONFIG, "afternoon")
    assert start == now_utc - timedelta(hours=10)
