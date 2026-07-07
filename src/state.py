import json
from datetime import datetime
from pathlib import Path

from models import Video

DEFAULT_STATE = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": []}


def load_state(path: Path) -> dict:
    if not Path(path).exists():
        return {k: list(v) for k, v in DEFAULT_STATE.items()}
    with open(path) as f:
        state = json.load(f)
    state.setdefault("processed_video_ids", [])
    state.setdefault("pending_video_ids", [])
    state.setdefault("retry_queue", [])
    return state


def save_state(path: Path, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def filter_unprocessed(videos: list[Video], state: dict) -> list[Video]:
    processed = set(state["processed_video_ids"])
    return [v for v in videos if v.video_id not in processed]


def mark_processed(state: dict, video_ids: list[str]) -> dict:
    new = dict(state)
    new["processed_video_ids"] = sorted(set(state["processed_video_ids"]) | set(video_ids))
    new["pending_video_ids"] = sorted(set(state.get("pending_video_ids", [])) - set(video_ids))
    return new


def mark_pending(state: dict, video_ids: list[str]) -> dict:
    new = dict(state)
    new["pending_video_ids"] = sorted(set(state.get("pending_video_ids", [])) | set(video_ids))
    new["processed_video_ids"] = list(state["processed_video_ids"])
    return new


def retry_attempts(state: dict) -> dict[str, int]:
    return {e["video_id"]: e["attempts"] for e in state.get("retry_queue", [])}


def retry_stub_videos(state: dict) -> list[Video]:
    stubs = []
    for e in state.get("retry_queue", []):
        stubs.append(
            Video(
                video_id=e["video_id"],
                channel_handle=e["channel_handle"],
                title=e["title"],
                published_at=datetime.fromisoformat(e["published_at"]),
                duration_seconds=0,
                is_live=False,
                description="",
            )
        )
    return stubs


def retry_entry(video: Video, attempts: int) -> dict:
    return {
        "video_id": video.video_id,
        "channel_handle": video.channel_handle,
        "title": video.title,
        "published_at": video.published_at.isoformat(),
        "attempts": attempts,
    }
