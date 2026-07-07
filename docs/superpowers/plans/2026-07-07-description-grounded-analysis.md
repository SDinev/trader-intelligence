# Description-Grounded Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate hallucinated price levels by grounding analysis in the authoritative video description, tagging each level's source, dropping levels that came from a video the model never actually ingested, and retrying videos that yield nothing.

**Architecture:** Per video, route to description-only analysis (when the description is substantive) or description+video combined. Each level carries `source`/`quote`. A post-call token-count gate detects silent video-ingestion failure and drops `video`-sourced levels. Videos that yield no grounded level enter a `retry_queue` in `state.json`, retried each edition up to a limit.

**Tech Stack:** Python 3.12+, google-genai SDK, pytest. Runs on GitHub Actions; state committed to repo.

## Global Constraints

- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.
- Tests run from repo root: `.venv/bin/python -m pytest` (config: `pytest.ini` sets `pythonpath = src`).
- No network in unit tests — fake the Gemini client and `requests`.
- `description_min_chars: 400` — description length above which we skip the video call.
- `video_ingested_min_tokens: 4000` — `prompt_token_count` below which the video is considered not ingested.
- `max_retry_attempts: 2` — attempts before giving up on an unextractable video.
- Level source markers: `ᴰ` (U+1D30) = description, `ⱽ` (U+2C7D) = video.

---

### Task 1: Model fields for provenance, description, retry outcomes

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Video.description: str`; `PriceLevel.source: str`, `PriceLevel.quote: str`; `VideoAnalysis.video_attached: bool`, `VideoAnalysis.video_ingested: bool`; `Brief.given_up_video_ids: list[str]`, `Brief.retrying_video_ids: list[str]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_price_level_carries_source_and_quote():
    level = PriceLevel(
        price="605.50", timestamp_seconds=245, source_video_id="xyz789",
        source="description", quote="support at 605.50",
    )
    assert level.source == "description"
    assert level.quote == "support at 605.50"
    assert level.link == "https://www.youtube.com/watch?v=xyz789&t=245s"


def test_price_level_source_defaults_to_video():
    level = PriceLevel(price="1", timestamp_seconds=0, source_video_id="a")
    assert level.source == "video"
    assert level.quote == ""


def test_video_has_description_field_default_empty():
    from datetime import datetime, timezone
    v = Video(
        video_id="a", channel_handle="@h", title="t",
        published_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        duration_seconds=1, is_live=False,
    )
    assert v.description == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py -k "source_and_quote or source_defaults or has_description" -v`
Expected: FAIL — `PriceLevel.__init__() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: Write minimal implementation**

In `src/models.py`, update these dataclasses (add only the new fields):

```python
@dataclass
class Video:
    video_id: str
    channel_handle: str
    title: str
    published_at: datetime
    duration_seconds: int
    is_live: bool
    description: str = ""

    @property
    def url(self) -> str:
        return youtube_watch_url(self.video_id)


@dataclass
class PriceLevel:
    price: str
    timestamp_seconds: int
    source_video_id: str
    source: str = "video"
    quote: str = ""

    @property
    def link(self) -> str:
        return youtube_watch_url(self.source_video_id, self.timestamp_seconds)


@dataclass
class VideoAnalysis:
    video: Video
    assets: list[AssetLevels] = field(default_factory=list)
    macro_notes: str = ""
    no_levels_mentioned: bool = False
    video_attached: bool = False
    video_ingested: bool = False
```

And add two fields to `Brief` (after `metadata_failed`):

```python
    metadata_failed: bool = False
    given_up_video_ids: list[str] = field(default_factory=list)
    retrying_video_ids: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`
Expected: PASS (all model tests).

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: model fields for level provenance, description, retry outcomes"
```

---

### Task 2: Populate description from the YouTube Data API

**Files:**
- Modify: `src/youtube_meta.py:29-39` (`parse_videos_list_response`)
- Test: `tests/test_youtube_meta.py`

**Interfaces:**
- Consumes: `Video.description` (Task 1).
- Produces: enriched `Video` objects now carry `description` from `snippet.description`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_youtube_meta.py`:

```python
def test_parse_videos_list_response_populates_description():
    videos_by_id = {"v1": make_video("v1")}
    api_response = {
        "items": [
            {
                "id": "v1",
                "contentDetails": {"duration": "PT25M"},
                "snippet": {
                    "liveBroadcastContent": "none",
                    "description": "SPY support 605\nQQQ resistance 450",
                },
            }
        ]
    }
    result = parse_videos_list_response(api_response, videos_by_id)
    assert result[0].description == "SPY support 605\nQQQ resistance 450"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_youtube_meta.py::test_parse_videos_list_response_populates_description -v`
Expected: FAIL — `assert '' == 'SPY support 605...'` (description not populated).

- [ ] **Step 3: Write minimal implementation**

In `src/youtube_meta.py`, update `parse_videos_list_response` to read the description and pass it to `replace`:

```python
def parse_videos_list_response(api_response: dict, videos_by_id: dict[str, Video]) -> list[Video]:
    result = []
    for item in api_response.get("items", []):
        video_id = item["id"]
        original = videos_by_id[video_id]
        duration_seconds = parse_iso8601_duration(item["contentDetails"]["duration"])
        # Only "none" is a finished, analyzable VOD; "live" and "upcoming"
        # are not yet analyzable and are deferred to the pending queue.
        is_live = item["snippet"]["liveBroadcastContent"] != "none"
        description = item["snippet"].get("description", "")
        result.append(
            replace(
                original,
                duration_seconds=duration_seconds,
                is_live=is_live,
                description=description,
            )
        )
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_youtube_meta.py -v`
Expected: PASS (existing tests still green; their snippet dicts lack `description` and default to `""`).

- [ ] **Step 5: Commit**

```bash
git add src/youtube_meta.py tests/test_youtube_meta.py
git commit -m "feat: populate Video.description from snippet"
```

---

### Task 3: Description-grounded analysis with source tags and token gate

**Files:**
- Modify: `src/analyze.py` (schema, prompt, parse, `analyze_video`; add helpers)
- Test: `tests/test_analyze.py`

**Interfaces:**
- Consumes: `Video.description`, `PriceLevel.source/quote`, `VideoAnalysis.video_attached/video_ingested` (Task 1).
- Produces: `analyze_video(client, video, config) -> VideoAnalysis` (now sets provenance flags and drops video levels when not ingested); `analysis_is_grounded(analysis: VideoAnalysis) -> bool`.
- Config read: `config["analysis"]["description_min_chars"]`, `config["analysis"]["video_ingested_min_tokens"]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_analyze.py` (keep existing tests):

```python
def _payload(source_a="video"):
    import json as _json
    return _json.dumps({
        "assets": [{
            "ticker": "SPY",
            "support": [{"price": "605", "timestamp_seconds": 120, "source": source_a, "quote": "support 605"}],
            "resistance": [],
            "strategy": "",
        }],
        "macro_notes": "",
        "no_levels_mentioned": False,
    })


def test_parse_sets_source_and_quote():
    a = parse_analysis_response(VIDEO, _payload(source_a="description"))
    lvl = a.assets[0].support[0]
    assert lvl.source == "description"
    assert lvl.quote == "support 605"


class _Usage:
    def __init__(self, prompt_tokens):
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = 10
        self.total_token_count = prompt_tokens + 10


class _Resp:
    def __init__(self, text, prompt_tokens):
        self.text = text
        self.usage_metadata = _Usage(prompt_tokens)


class _Models:
    def __init__(self, text, prompt_tokens):
        self._text = text
        self._pt = prompt_tokens
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _Resp(self._text, self._pt)


class _Client:
    def __init__(self, text, prompt_tokens):
        self.models = _Models(text, prompt_tokens)


CONFIG = {
    "gemini": {"model": "gemini-flash-latest", "media_resolution": "MEDIA_RESOLUTION_LOW", "temperature": 0},
    "analysis": {"description_min_chars": 400, "video_ingested_min_tokens": 4000, "max_retry_attempts": 2},
}


def _video(description=""):
    from datetime import datetime, timezone
    return Video(video_id="v1", channel_handle="@c", title="t",
                 published_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
                 duration_seconds=1200, is_live=False, description=description)


def test_rich_description_skips_video_call():
    client = _Client(_payload(source_a="description"), prompt_tokens=800)
    v = _video(description="x" * 500)
    analysis = analyze_video(client, v, CONFIG)
    contents_repr = str(client.models.calls[0]["contents"])
    assert "youtube.com/watch" not in contents_repr  # no video part attached
    assert analysis.video_attached is False
    assert analysis_is_grounded(analysis) is True


def test_combined_call_attaches_video_when_description_thin():
    client = _Client(_payload(), prompt_tokens=50000)
    v = _video(description="short")
    analysis = analyze_video(client, v, CONFIG)
    contents_repr = str(client.models.calls[0]["contents"])
    assert "youtube.com/watch?v=v1" in contents_repr
    assert analysis.video_attached is True
    assert analysis.video_ingested is True


def test_low_token_count_drops_video_sourced_levels():
    client = _Client(_payload(source_a="video"), prompt_tokens=200)
    v = _video(description="short")
    analysis = analyze_video(client, v, CONFIG)
    assert analysis.video_ingested is False
    assert analysis.assets[0].support == []  # video-sourced level dropped
    assert analysis_is_grounded(analysis) is False  # nothing survived


def test_low_token_count_keeps_description_sourced_levels():
    client = _Client(_payload(source_a="description"), prompt_tokens=200)
    v = _video(description="short")
    analysis = analyze_video(client, v, CONFIG)
    assert analysis.assets[0].support[0].price == "605"
    assert analysis_is_grounded(analysis) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_analyze.py -k "source_and_quote or skips_video or attaches_video or drops_video or keeps_description" -v`
Expected: FAIL — `analysis_is_grounded` not defined / `video_attached` not set / video part still attached.

- [ ] **Step 3: Write the implementation**

Rewrite `src/analyze.py` schema, prompt, parse, and `analyze_video`, and add the helpers. Replace the file's contents from the `RESPONSE_SCHEMA` definition through `analyze_video` with:

```python
LEVEL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "price": {"type": "string"},
        "timestamp_seconds": {"type": "integer"},
        "source": {"type": "string", "enum": ["description", "video"]},
        "quote": {"type": "string"},
    },
    "required": ["price", "timestamp_seconds", "source", "quote"],
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "support": {"type": "array", "items": LEVEL_ITEM_SCHEMA},
                    "resistance": {"type": "array", "items": LEVEL_ITEM_SCHEMA},
                    "strategy": {"type": "string"},
                },
                "required": ["ticker"],
            },
        },
        "macro_notes": {"type": "string"},
        "no_levels_mentioned": {"type": "boolean"},
    },
    "required": ["assets", "no_levels_mentioned"],
}


def build_analysis_prompt(video: Video) -> str:
    description = video.description or "(no description provided)"
    return (
        f"You are extracting actionable price levels from the financial YouTube video "
        f"titled \"{video.title}\" by creator {video.channel_handle}.\n\n"
        "PRIMARY SOURCE — the creator's own video description (authoritative ground truth):\n"
        "-----\n"
        f"{description}\n"
        "-----\n\n"
        "If a video is also attached, you may use it ONLY to add levels the description "
        "does not already cover. The description always wins on conflicts.\n\n"
        "Extract ONLY specific price levels (support, resistance, gap fills, buy/sell "
        "targets) that are explicitly stated. Do NOT infer, estimate, or recall levels "
        "from general knowledge. If a level is not explicitly stated in the description "
        "or the attached video, do not include it. If nothing specific is stated, set "
        "no_levels_mentioned to true and leave assets empty.\n\n"
        "For EACH level you MUST provide: source ('description' if it came from the "
        "description text, 'video' if from the attached video), and quote (the verbatim "
        "phrase from the description or spoken in the video). For 'video' levels include "
        "timestamp_seconds (whole seconds from the start); for 'description' levels use "
        "the timestamp from the description if given, else 0. Report one entry per "
        "distinct ticker (e.g. QQQ, SPY, BTC) with a short strategy note if one is given."
    )


def parse_analysis_response(video: Video, response_text: str) -> VideoAnalysis:
    data = json.loads(response_text)

    def levels(items):
        return [
            PriceLevel(
                price=lvl["price"],
                timestamp_seconds=lvl.get("timestamp_seconds", 0),
                source_video_id=video.video_id,
                source=lvl.get("source", "video"),
                quote=lvl.get("quote", ""),
            )
            for lvl in items
        ]

    assets = [
        AssetLevels(
            ticker=asset_data["ticker"],
            support=levels(asset_data.get("support", [])),
            resistance=levels(asset_data.get("resistance", [])),
            strategy=asset_data.get("strategy", ""),
        )
        for asset_data in data.get("assets", [])
    ]
    return VideoAnalysis(
        video=video,
        assets=assets,
        macro_notes=data.get("macro_notes", ""),
        no_levels_mentioned=data.get("no_levels_mentioned", False),
    )


def _has_any_level(analysis: VideoAnalysis) -> bool:
    return any(a.support or a.resistance for a in analysis.assets)


def _drop_video_levels(analysis: VideoAnalysis) -> None:
    for asset in analysis.assets:
        asset.support = [lvl for lvl in asset.support if lvl.source != "video"]
        asset.resistance = [lvl for lvl in asset.resistance if lvl.source != "video"]


def analysis_is_grounded(analysis: VideoAnalysis) -> bool:
    if not analysis.video_attached:
        return True
    if analysis.video_ingested:
        return True
    return _has_any_level(analysis)


def analyze_video(client, video: Video, config: dict) -> VideoAnalysis:
    gemini_config = config["gemini"]
    analysis_config = config.get("analysis", {})
    min_chars = analysis_config.get("description_min_chars", 400)
    min_tokens = analysis_config.get("video_ingested_min_tokens", 4000)

    description_only = len(video.description or "") > min_chars

    contents = [build_analysis_prompt(video)]
    if not description_only:
        contents.insert(0, types.Part.from_uri(file_uri=video.url, mime_type="video/*"))

    generation_config = types.GenerateContentConfig(
        temperature=gemini_config["temperature"],
        media_resolution=gemini_config["media_resolution"],
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
    )
    response = client.models.generate_content(
        model=gemini_config["model"],
        contents=contents,
        config=generation_config,
    )

    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", None) if usage is not None else None
    print(
        f"[usage] {video.video_id} ({video.channel_handle}): "
        f"description_only={description_only} prompt_tokens={prompt_tokens}"
    )

    analysis = parse_analysis_response(video, response.text)
    analysis.video_attached = not description_only
    if description_only:
        analysis.video_ingested = False
    else:
        # If we cannot measure tokens (e.g. in tests), assume ingested to avoid
        # over-dropping. In production usage_metadata is always present.
        analysis.video_ingested = prompt_tokens is None or prompt_tokens >= min_tokens
        if not analysis.video_ingested:
            _drop_video_levels(analysis)

    return analysis
```

Note: the existing test `test_analyze_video_calls_gemini_with_youtube_url_and_returns_parsed_analysis` uses a fake response without `usage_metadata` and an empty description → combined path, `prompt_tokens=None` → `video_ingested=True`; it still asserts `analysis.macro_notes` and the URL in contents, so it stays green.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_analyze.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/analyze.py tests/test_analyze.py
git commit -m "feat: description-grounded analysis with source tags and ingestion gate"
```

---

### Task 4: State retry_queue helpers

**Files:**
- Modify: `src/state.py` (default state, key-preserving `mark_processed`/`mark_pending`, retry helpers)
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `Video` (Task 1).
- Produces: `load_state` returns dict with `retry_queue`; `mark_processed`/`mark_pending` preserve `retry_queue`; `retry_stub_videos(state) -> list[Video]`; `retry_attempts(state) -> dict[str, int]`; `retry_entry(video: Video, attempts: int) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_state.py`:

```python
from state import retry_attempts, retry_entry, retry_stub_videos


def test_load_state_missing_file_includes_retry_queue(tmp_path):
    state = load_state(tmp_path / "state.json")
    assert state["retry_queue"] == []


def test_load_state_injects_retry_queue_when_absent(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"processed_video_ids": [], "pending_video_ids": []}))
    state = load_state(path)
    assert state["retry_queue"] == []


def test_mark_processed_preserves_retry_queue():
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [{"video_id": "x", "attempts": 1}]}
    new = mark_processed(state, ["a"])
    assert new["retry_queue"] == [{"video_id": "x", "attempts": 1}]


def test_retry_stub_videos_reconstructs_videos():
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "x", "channel_handle": "@h", "title": "T",
         "published_at": "2026-07-06T20:00:00+00:00", "attempts": 1}
    ]}
    videos = retry_stub_videos(state)
    assert videos[0].video_id == "x"
    assert videos[0].channel_handle == "@h"
    assert videos[0].title == "T"


def test_retry_attempts_maps_id_to_count():
    state = {"processed_video_ids": [], "pending_video_ids": [], "retry_queue": [
        {"video_id": "x", "attempts": 2}
    ]}
    assert retry_attempts(state) == {"x": 2}


def test_retry_entry_from_video_roundtrips():
    from datetime import datetime, timezone
    v = make_video("x")
    entry = retry_entry(v, attempts=1)
    assert entry["video_id"] == "x"
    assert entry["channel_handle"] == "@TraderNick"
    assert entry["attempts"] == 1
    assert entry["published_at"] == v.published_at.isoformat()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_state.py -k "retry" -v`
Expected: FAIL — `ImportError: cannot import name 'retry_attempts'`.

- [ ] **Step 3: Write the implementation**

In `src/state.py`, update imports and functions:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_state.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/state.py tests/test_state.py
git commit -m "feat: state retry_queue helpers, key-preserving state updates"
```

---

### Task 5: Orchestrate retry queue and groundedness in run_pipeline

**Files:**
- Modify: `src/main.py` (`run_pipeline` body and its `Brief` construction / `new_state`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `analysis_is_grounded` (Task 3); `retry_attempts`, `retry_stub_videos`, `retry_entry` (Task 4); `VideoAnalysis` provenance flags.
- Produces: `run_pipeline` result whose `brief` has `given_up_video_ids`/`retrying_video_ids` and whose `new_state` has an updated `retry_queue`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main.py` (import the helper and build analyses with flags):

```python
from analyze import analysis_is_grounded  # noqa: F401  (ensures module import parity)


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
```

Note: `CONFIG` in `tests/test_main.py` currently has no `analysis` key. Extend it — add `"analysis": {"max_retry_attempts": 2}` to the existing `CONFIG` dict at the top of the file so `run_pipeline` can read the limit.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_main.py -k "unextracted or gives_up or grounded_retry" -v`
Expected: FAIL — retry_queue not present / KeyError, since `run_pipeline` doesn't handle retries yet.

- [ ] **Step 3: Write the implementation**

Replace the body of `run_pipeline` in `src/main.py` from the line `unprocessed = filter_unprocessed(...)` through the `return {...}` with:

```python
    max_attempts = config.get("analysis", {}).get("max_retry_attempts", 2)
    attempts_by_id = retry_attempts(state)
    retry_ids = set(attempts_by_id)

    discovered = [v for v in filter_unprocessed(all_candidates, state) if v.video_id not in retry_ids]
    to_enrich = discovered + retry_stub_videos(state)

    metadata_failed = False
    enriched = []
    if to_enrich:
        try:
            enriched = fetch_video_metadata(to_enrich, youtube_api_key)
        except Exception as exc:
            metadata_failed = True
            print(f"Video metadata lookup failed, skipping analysis this run: {exc}")

    live_videos = [v for v in enriched if v.is_live]
    finished_videos = [v for v in enriched if not v.is_live]

    allocation = allocate(finished_videos, config)
    eligible = allocation["eligible"]
    too_long = allocation["too_long"]
    skipped_quota = allocation["skipped_quota"]

    analyses_by_handle: dict[str, list] = {}
    failed_video_ids = []
    given_up_video_ids = []
    retrying_video_ids = []
    new_retry_entries: list[dict] = []
    newly_processed_ids = [v.video_id for v in too_long]

    def handle_unextracted(video):
        n = attempts_by_id.get(video.video_id, 0) + 1
        if n >= max_attempts:
            given_up_video_ids.append(video.video_id)
            newly_processed_ids.append(video.video_id)
        else:
            retrying_video_ids.append(video.video_id)
            new_retry_entries.append(retry_entry(video, n))

    for video in eligible:
        try:
            analysis = analyze_video(gemini_client, video, config)
        except Exception:
            failed_video_ids.append(video.video_id)
            continue
        if analysis_is_grounded(analysis):
            analyses_by_handle.setdefault(video.channel_handle, []).append(analysis)
            newly_processed_ids.append(video.video_id)
        else:
            handle_unextracted(video)

    creator_summaries = [
        CreatorSummary(handle=entry["handle"], analyses=analyses_by_handle[entry["handle"]])
        for entry in config["roster"]
        if entry["handle"] in analyses_by_handle
    ]

    pending_ids = [v.video_id for v in live_videos]

    brief = Brief(
        edition=edition,
        generated_at=now_utc,
        creator_summaries=creator_summaries,
        too_long_videos=too_long,
        skipped_quota_videos=skipped_quota,
        failed_video_ids=failed_video_ids,
        pending_video_ids=pending_ids,
        discovery_failed_handles=discovery_failed_handles,
        metadata_failed=metadata_failed,
        given_up_video_ids=given_up_video_ids,
        retrying_video_ids=retrying_video_ids,
    )

    new_state = mark_processed(state, newly_processed_ids)
    new_state = mark_pending(new_state, pending_ids)

    # Rebuild retry_queue: keep prior entries not resolved this run, plus new ones.
    resolved = set(newly_processed_ids) | set(pending_ids) | set(retrying_video_ids)
    carryover = [e for e in state.get("retry_queue", []) if e["video_id"] not in resolved]
    new_state["retry_queue"] = carryover + new_retry_entries

    return {"brief": brief, "new_state": new_state}
```

Then update the imports at the top of `src/main.py`:

```python
from state import (
    filter_unprocessed,
    load_state,
    mark_pending,
    mark_processed,
    retry_attempts,
    retry_entry,
    retry_stub_videos,
    save_state,
)
from analyze import analysis_is_grounded
from analyze import analyze_video as real_analyze_video
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: PASS (new retry tests + existing main tests). Existing tests use `make_analysis_for` returning a `VideoAnalysis` with `video_attached=False` → grounded → processed, matching prior behavior.

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: retry queue orchestration and groundedness gating in run_pipeline"
```

---

### Task 6: Render source markers and retry status in the brief

**Files:**
- Modify: `src/report.py` (`_format_levels_cell`, table legend, `has_content`, status lines)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `PriceLevel.source` (Task 1); `Brief.given_up_video_ids`/`retrying_video_ids` (Task 1).
- Produces: brief markdown with per-level `ᴰ`/`ⱽ` markers, a legend line, and "gave up"/"retrying" status lines.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_report.py`:

```python
def test_level_cell_shows_source_marker():
    video = make_video("v1", "recap")
    analysis = VideoAnalysis(video=video, assets=[AssetLevels(
        ticker="SPY",
        support=[PriceLevel(price="605", timestamp_seconds=10, source_video_id="v1", source="description")],
        resistance=[PriceLevel(price="612", timestamp_seconds=20, source_video_id="v1", source="video")],
    )])
    brief = Brief(edition="morning", generated_at=GENERATED_AT,
                  creator_summaries=[CreatorSummary(handle="@a", analyses=[analysis])])
    md = render_brief_markdown(brief)
    assert "[605](https://www.youtube.com/watch?v=v1&t=10s)ᴰ" in md
    assert "[612](https://www.youtube.com/watch?v=v1&t=20s)ⱽ" in md
    assert "Level source:" in md  # legend present


def test_given_up_and_retrying_status_lines():
    brief = Brief(edition="morning", generated_at=GENERATED_AT,
                  given_up_video_ids=["gone1"], retrying_video_ids=["later1"])
    md = render_brief_markdown(brief)
    assert "gone1" in md
    assert "later1" in md
    assert "gave up" in md.lower()
    assert "retrying" in md.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_report.py -k "source_marker or given_up_and_retrying" -v`
Expected: FAIL — markers not appended / status lines missing.

- [ ] **Step 3: Write the implementation**

In `src/report.py`, add the marker map and update `_format_levels_cell`:

```python
_LEVEL_MARKERS = {"description": "ᴰ", "video": "ⱽ"}


def _format_levels_cell(levels: list) -> str:
    if not levels:
        return "—"
    return "; ".join(
        f"[{lvl.price}]({lvl.link}){_LEVEL_MARKERS.get(lvl.source, '')}" for lvl in levels
    )
```

In `render_brief_markdown`, after the asset-table rows are appended (the `lines.append("")` that follows the `for ticker in sorted(...)` loop), insert a legend line. Change:

```python
            lines.append(f"| {ticker} | {support_cell} | {resistance_cell} | {strategy_cell} |")
        lines.append("")
```

to:

```python
            lines.append(f"| {ticker} | {support_cell} | {resistance_cell} | {strategy_cell} |")
        lines.append("")
        lines.append("_Level source: ᴰ = creator's video description · ⱽ = spoken/shown in video_")
        lines.append("")
```

Extend `has_content` to include the new fields:

```python
    has_content = bool(
        brief.creator_summaries
        or brief.too_long_videos
        or brief.skipped_quota_videos
        or brief.failed_video_ids
        or brief.discovery_failed_handles
        or brief.metadata_failed
        or brief.given_up_video_ids
        or brief.retrying_video_ids
    )
```

Add status lines in the Pipeline Status section, immediately before the `if brief.metadata_failed:` block:

```python
    if brief.given_up_video_ids:
        lines.append("**Couldn't extract reliable levels (gave up after retries):**")
        for video_id in brief.given_up_video_ids:
            lines.append(f"- {video_id}")
        lines.append("")
    if brief.retrying_video_ids:
        lines.append("**Retrying next edition (no levels extracted yet):**")
        for video_id in brief.retrying_video_ids:
            lines.append(f"- {video_id}")
        lines.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS (new + existing). Existing level-cell tests used default `source="video"`, so their expected strings must now tolerate the `ⱽ` suffix — check: `test_asset_table_includes_ticker_and_levels` asserts substrings like `"605.50"` and the bare link, which remain present (the marker is appended after the link, not inside it), so they still pass.

- [ ] **Step 5: Commit**

```bash
git add src/report.py tests/test_report.py
git commit -m "feat: per-level source markers and retry status lines in brief"
```

---

### Task 7: Config block and one-time state backfill

**Files:**
- Modify: `config.yaml` (add `analysis` block)
- Modify: `state.json` (add `retry_queue` with the two hallucinated videos; remove them from `processed_video_ids`)

**Interfaces:**
- Consumes: config keys read by Task 3 (`analysis.description_min_chars`, `analysis.video_ingested_min_tokens`) and Task 5 (`analysis.max_retry_attempts`).

- [ ] **Step 1: Add the analysis config block**

In `config.yaml`, add after the `quota:` block:

```yaml
analysis:
  description_min_chars: 400        # description longer than this -> analyze description only, skip the video call
  video_ingested_min_tokens: 4000   # Gemini prompt_token_count below this means the video was NOT ingested
  max_retry_attempts: 2             # attempts before giving up on a video that yields no levels
```

- [ ] **Step 2: Backfill state.json**

Replace the contents of `state.json` with (removes `M8FhcMaccOI` and `_Fx85Gpj5sE` from processed, adds them to `retry_queue`):

```json
{
  "processed_video_ids": [
    "6Fo5jznqU5Q",
    "CcfDcz1X0oE",
    "PXk1wVJ19P0",
    "xzXduRI5isw"
  ],
  "pending_video_ids": [
    "r8RGF92lQZ0"
  ],
  "retry_queue": [
    {
      "video_id": "_Fx85Gpj5sE",
      "channel_handle": "@VerifiedInvesting",
      "title": "Reddit Could Be Setting Up a Breakout — Plus 9 More Levels",
      "published_at": "2026-07-06T20:00:00+00:00",
      "attempts": 0
    },
    {
      "video_id": "M8FhcMaccOI",
      "channel_handle": "@VerifiedInvesting",
      "title": "Markets Shake Off Semi's Swoon (But Epic Storm Clouds Form), Gold, Silver And Bitcoin Trouble",
      "published_at": "2026-07-06T20:00:00+00:00",
      "attempts": 0
    }
  ]
}
```

- [ ] **Step 3: Verify config and state parse**

Run: `.venv/bin/python -c "import yaml, json; yaml.safe_load(open('config.yaml')); json.load(open('state.json')); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add config.yaml state.json
git commit -m "chore: analysis config block; backfill retry_queue with hallucinated videos"
```

---

### Task 8: Full-suite verification and live dry-run

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Local sanity of the description-only routing (no network)**

Run:
```bash
.venv/bin/python -c "
import sys; sys.path.insert(0,'src')
from analyze import analyze_video
from models import Video
from datetime import datetime, timezone
class U: prompt_token_count=900; candidates_token_count=5; total_token_count=905
class R:
    text='{\"assets\":[{\"ticker\":\"GOLD\",\"support\":[{\"price\":\"3500\",\"timestamp_seconds\":0,\"source\":\"description\",\"quote\":\"flush to 3500\"}],\"resistance\":[],\"strategy\":\"\"}],\"macro_notes\":\"\",\"no_levels_mentioned\":false}'
    usage_metadata=U()
class M:
    def generate_content(self,**k): 
        assert all('youtube.com/watch' not in str(c) for c in k['contents']), 'video should NOT be attached'
        return R()
class C: models=M()
cfg={'gemini':{'model':'m','media_resolution':'LOW','temperature':0},'analysis':{'description_min_chars':400,'video_ingested_min_tokens':4000}}
v=Video('x','@VerifiedInvesting','t',datetime(2026,7,6,tzinfo=timezone.utc),1200,False,'D'*500)
a=analyze_video(C(),v,cfg)
print('grounded=', __import__('analyze').analysis_is_grounded(a), 'source=', a.assets[0].support[0].source)
"
```
Expected: prints `grounded= True source= description` with no assertion error.

- [ ] **Step 3: Push and run a live dry-run on a throwaway branch**

```bash
git push origin HEAD:diag/desc-grounded
gh workflow run digest.yml --repo SDinev/trader-intelligence --ref diag/desc-grounded -f edition=afternoon -f dry_run=true
```
Wait for completion (`gh run watch`), then read the pipeline log. Expected in the `[usage]` lines: VerifiedInvesting videos show `description_only=True`; the brief's GOLD/SPY levels now carry `ᴰ` markers and the prices match the actual description text (no 2024-era phantom values like AAPL 175/190).

- [ ] **Step 4: Clean up the branch**

```bash
git push origin --delete diag/desc-grounded
```

- [ ] **Step 5: Merge to main and do a real run**

Fast-forward `main` (all task commits are already on it), push, then dispatch a real run:
```bash
git push origin main
gh workflow run digest.yml --repo SDinev/trader-intelligence --ref main -f edition=afternoon -f dry_run=false
```
Verify: the two backfilled videos (`_Fx85Gpj5sE`, `M8FhcMaccOI`) are re-analyzed; their levels now come from the descriptions (`ᴰ`), the committed brief reflects that, `state.json` moves them from `retry_queue` to `processed_video_ids` on success, and Discord receives the summary.

---

## Self-Review

**Spec coverage:**
- Description carried through → Task 2. ✔
- Route by description richness (>400 → description-only) → Task 3. ✔
- Source+quote per level → Tasks 1, 3. ✔
- Token gate drops video levels → Task 3. ✔
- Grounded/unextracted classification → Tasks 3 (`analysis_is_grounded`), 5. ✔
- retry_queue, max 2 attempts, regardless of window → Tasks 4, 5. ✔
- Backfill two hallucinated videos → Task 7. ✔
- Brief source markers + legend + gave-up/retrying lines → Task 6. ✔
- Config block → Task 7. ✔
- Testing (TDD unit + live verification) → all tasks + Task 8. ✔

**Placeholder scan:** No TBD/TODO; every code step shows complete code.

**Type consistency:** `analyze_video` returns `VideoAnalysis` (provenance flags on the dataclass) throughout; `analysis_is_grounded(VideoAnalysis) -> bool` used in Task 5 matches Task 3; `retry_entry`/`retry_stub_videos`/`retry_attempts` signatures identical between Tasks 4 and 5; `PriceLevel.source` used in Tasks 1/3/6 consistently.
