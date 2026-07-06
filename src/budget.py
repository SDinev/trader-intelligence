from models import Video


def allocate(videos: list[Video], config: dict) -> dict[str, list[Video]]:
    max_single = config["quota"]["max_video_seconds_single"]
    max_per_run = config["quota"]["max_video_seconds_per_run"]

    too_long = [v for v in videos if v.duration_seconds > max_single]
    candidates = sorted(
        (v for v in videos if v.duration_seconds <= max_single),
        key=lambda v: v.published_at,
        reverse=True,
    )

    eligible: list[Video] = []
    skipped_quota: list[Video] = []
    used_seconds = 0
    for video in candidates:
        if used_seconds + video.duration_seconds <= max_per_run:
            eligible.append(video)
            used_seconds += video.duration_seconds
        else:
            skipped_quota.append(video)

    return {"eligible": eligible, "too_long": too_long, "skipped_quota": skipped_quota}
