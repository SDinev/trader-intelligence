import json
from pathlib import Path

from models import Video

DEFAULT_STATE = {"processed_video_ids": [], "pending_video_ids": []}


def load_state(path: Path) -> dict:
    if not Path(path).exists():
        return dict(DEFAULT_STATE)
    with open(path) as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def filter_unprocessed(videos: list[Video], state: dict) -> list[Video]:
    processed = set(state["processed_video_ids"])
    return [v for v in videos if v.video_id not in processed]


def mark_processed(state: dict, video_ids: list[str]) -> dict:
    processed = set(state["processed_video_ids"]) | set(video_ids)
    pending = set(state["pending_video_ids"]) - set(video_ids)
    return {
        "processed_video_ids": sorted(processed),
        "pending_video_ids": sorted(pending),
    }


def mark_pending(state: dict, video_ids: list[str]) -> dict:
    pending = set(state["pending_video_ids"]) | set(video_ids)
    return {
        "processed_video_ids": list(state["processed_video_ids"]),
        "pending_video_ids": sorted(pending),
    }
