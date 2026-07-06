import json
from datetime import datetime, timezone

from models import Video
from state import filter_unprocessed, load_state, mark_pending, mark_processed, save_state


def make_video(video_id: str) -> Video:
    return Video(
        video_id=video_id,
        channel_handle="@TraderNick",
        title="t",
        published_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        duration_seconds=600,
        is_live=False,
    )


def test_load_state_missing_file_returns_default(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path)
    assert state == {"processed_video_ids": [], "pending_video_ids": []}


def test_load_state_reads_existing_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"processed_video_ids": ["a"], "pending_video_ids": ["b"]}))
    state = load_state(path)
    assert state == {"processed_video_ids": ["a"], "pending_video_ids": ["b"]}


def test_save_state_round_trips(tmp_path):
    path = tmp_path / "state.json"
    state = {"processed_video_ids": ["a", "b"], "pending_video_ids": []}
    save_state(path, state)
    assert load_state(path) == state


def test_filter_unprocessed_excludes_already_processed():
    state = {"processed_video_ids": ["a"], "pending_video_ids": []}
    videos = [make_video("a"), make_video("b")]
    result = filter_unprocessed(videos, state)
    assert [v.video_id for v in result] == ["b"]


def test_mark_processed_adds_ids_without_duplicates():
    state = {"processed_video_ids": ["a"], "pending_video_ids": []}
    new_state = mark_processed(state, ["a", "b"])
    assert sorted(new_state["processed_video_ids"]) == ["a", "b"]


def test_mark_processed_removes_ids_from_pending():
    state = {"processed_video_ids": [], "pending_video_ids": ["a", "b"]}
    new_state = mark_processed(state, ["a"])
    assert new_state["pending_video_ids"] == ["b"]


def test_mark_pending_adds_ids_without_duplicates():
    state = {"processed_video_ids": [], "pending_video_ids": ["a"]}
    new_state = mark_pending(state, ["a", "c"])
    assert sorted(new_state["pending_video_ids"]) == ["a", "c"]
