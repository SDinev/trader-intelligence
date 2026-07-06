from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TOLERANCE_MINUTES = 20


def determine_edition(
    now_utc: datetime,
    config: dict,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
    forced_edition: str | None = None,
) -> str | None:
    if forced_edition is not None:
        return forced_edition

    local_now = now_utc.astimezone(ZoneInfo(config["timezone"]))
    local_minutes = local_now.hour * 60 + local_now.minute

    for name, edition in config["editions"].items():
        target_minutes = edition["target_hour"] * 60 + edition["target_minute"]
        if abs(local_minutes - target_minutes) <= tolerance_minutes:
            return name
    return None


def lookback_window(
    now_utc: datetime, config: dict, edition: str
) -> tuple[datetime, datetime]:
    lookback_hours = config["editions"][edition]["lookback_hours"]
    return now_utc - timedelta(hours=lookback_hours), now_utc
