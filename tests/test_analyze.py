import json
from datetime import datetime, timezone

from analyze import analyze_video, build_analysis_prompt, parse_analysis_response
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
