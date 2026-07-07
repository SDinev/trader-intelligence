import json
from datetime import datetime, timezone

from analyze import (
    analysis_is_grounded,
    analyze_video,
    build_analysis_prompt,
    parse_analysis_response,
)
from models import Video

VIDEO = Video(
    video_id="v1",
    channel_handle="@TraderNick",
    title="Pre-market game plan",
    published_at=datetime(2026, 7, 6, 6, 0, tzinfo=timezone.utc),
    duration_seconds=1200,
    is_live=False,
)


def test_build_analysis_prompt_includes_title_and_guardrails():
    prompt = build_analysis_prompt(VIDEO)
    assert VIDEO.title in prompt
    assert "only" in prompt.lower()
    assert "timestamp" in prompt.lower()


def test_parse_analysis_response_builds_video_analysis_with_source_ids():
    payload = json.dumps(
        {
            "assets": [
                {
                    "ticker": "SPY",
                    "support": [{"price": "605.50", "timestamp_seconds": 120}],
                    "resistance": [{"price": "612.00", "timestamp_seconds": 200}],
                    "strategy": "Reclaim of 610 opens room to 615.",
                }
            ],
            "macro_notes": "CPI print tomorrow morning.",
            "no_levels_mentioned": False,
        }
    )
    analysis = parse_analysis_response(VIDEO, payload)

    assert analysis.video is VIDEO
    assert analysis.macro_notes == "CPI print tomorrow morning."
    assert analysis.no_levels_mentioned is False
    assert len(analysis.assets) == 1
    asset = analysis.assets[0]
    assert asset.ticker == "SPY"
    assert asset.support[0].price == "605.50"
    assert asset.support[0].source_video_id == "v1"
    assert asset.support[0].link == "https://www.youtube.com/watch?v=v1&t=120s"
    assert asset.resistance[0].price == "612.00"
    assert asset.strategy == "Reclaim of 610 opens room to 615."


def test_parse_analysis_response_handles_no_levels_mentioned():
    payload = json.dumps({"assets": [], "macro_notes": "General market chat.", "no_levels_mentioned": True})
    analysis = parse_analysis_response(VIDEO, payload)
    assert analysis.assets == []
    assert analysis.no_levels_mentioned is True


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return FakeResponse(self.response_text)


class FakeClient:
    def __init__(self, response_text):
        self.models = FakeModels(response_text)


def test_analyze_video_calls_gemini_with_youtube_url_and_returns_parsed_analysis():
    response_text = json.dumps(
        {"assets": [], "macro_notes": "quiet session", "no_levels_mentioned": True}
    )
    client = FakeClient(response_text)
    config = {
        "gemini": {
            "model": "gemini-flash-latest",
            "media_resolution": "MEDIA_RESOLUTION_LOW",
            "temperature": 0,
        }
    }

    analysis = analyze_video(client, VIDEO, config)

    assert analysis.macro_notes == "quiet session"
    call = client.models.calls[0]
    assert call["model"] == "gemini-flash-latest"
    assert call["config"].temperature == 0
    # the YouTube URL must be present somewhere in the contents sent to the model
    contents_repr = str(call["contents"])
    assert VIDEO.url in contents_repr


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
