import re
from dataclasses import replace

import requests

from models import Video

VIDEOS_LIST_URL = "https://www.googleapis.com/youtube/v3/videos"
REQUEST_TIMEOUT_SECONDS = 15
MAX_IDS_PER_REQUEST = 50

DURATION_RE = re.compile(
    r"P(?:\d+D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?"
)


def parse_iso8601_duration(duration: str) -> int:
    match = DURATION_RE.fullmatch(duration)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def parse_videos_list_response(api_response: dict, videos_by_id: dict[str, Video]) -> list[Video]:
    result = []
    for item in api_response.get("items", []):
        video_id = item["id"]
        original = videos_by_id[video_id]
        duration_seconds = parse_iso8601_duration(item["contentDetails"]["duration"])
        is_live = item["snippet"]["liveBroadcastContent"] == "live"
        result.append(replace(original, duration_seconds=duration_seconds, is_live=is_live))
    return result


def fetch_video_metadata(videos: list[Video], api_key: str) -> list[Video]:
    videos_by_id = {v.video_id: v for v in videos}
    ids = list(videos_by_id.keys())
    result: list[Video] = []

    for i in range(0, len(ids), MAX_IDS_PER_REQUEST):
        chunk = ids[i : i + MAX_IDS_PER_REQUEST]
        response = requests.get(
            VIDEOS_LIST_URL,
            params={
                "part": "contentDetails,snippet",
                "id": ",".join(chunk),
                "key": api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"YouTube Data API returned {response.status_code}: {response.text[:500]}"
            )
        result.extend(parse_videos_list_response(response.json(), videos_by_id))

    return result
