from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def determine_edition(
    now_utc: datetime,
    config: dict,
    forced_edition: str | None = None,
) -> str | None:
    if forced_edition is not None:
        return forced_edition

    # Map to the NEAREST edition target in local wall-clock time. GitHub Actions
    # cron routinely fires 1-3 hours late, so an exact-time window would reject
    # a delayed-but-correct firing. The two editions are ~8.5h apart, so nearest
    # is unambiguous; duplicate firings are handled by per-day idempotency in
    # state, not by rejecting here.
    editions = config["editions"]
    if not editions:
        return None

    local_now = now_utc.astimezone(ZoneInfo(config["timezone"]))
    local_minutes = local_now.hour * 60 + local_now.minute

    def distance(edition: dict) -> int:
        target_minutes = edition["target_hour"] * 60 + edition["target_minute"]
        return abs(local_minutes - target_minutes)

    return min(editions, key=lambda name: distance(editions[name]))


def lookback_window(
    now_utc: datetime, config: dict, edition: str
) -> tuple[datetime, datetime]:
    lookback_hours = config["editions"][edition]["lookback_hours"]
    return now_utc - timedelta(hours=lookback_hours), now_utc
