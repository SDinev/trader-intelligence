import json

from google.genai import types

from models import AssetLevels, PriceLevel, Video, VideoAnalysis

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "support": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "price": {"type": "string"},
                                "timestamp_seconds": {"type": "integer"},
                            },
                            "required": ["price", "timestamp_seconds"],
                        },
                    },
                    "resistance": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "price": {"type": "string"},
                                "timestamp_seconds": {"type": "integer"},
                            },
                            "required": ["price", "timestamp_seconds"],
                        },
                    },
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
    return (
        f"You are analyzing the financial YouTube video titled \"{video.title}\" "
        f"by creator {video.channel_handle}.\n\n"
        "Watch and listen to the actual video (audio and on-screen chart annotations), "
        "not any auto-generated captions. Extract only the specific price levels "
        "(support, resistance, gap fills, buy/sell targets) that the creator explicitly "
        "states out loud or shows on screen. Do not infer, estimate, or hallucinate a "
        "level that was not explicitly stated. If nothing specific was stated, set "
        "no_levels_mentioned to true and leave assets empty.\n\n"
        "For every single price level you report, you MUST include the timestamp "
        "(in whole seconds from the start of the video) at which it was stated, so it "
        "can be verified by a human. Report one entry per distinct asset/ticker "
        "(e.g. QQQ, SPY, BTC), including a short actionable strategy note for that "
        "asset if one was given."
    )


def parse_analysis_response(video: Video, response_text: str) -> VideoAnalysis:
    data = json.loads(response_text)
    assets = []
    for asset_data in data.get("assets", []):
        assets.append(
            AssetLevels(
                ticker=asset_data["ticker"],
                support=[
                    PriceLevel(
                        price=lvl["price"],
                        timestamp_seconds=lvl["timestamp_seconds"],
                        source_video_id=video.video_id,
                    )
                    for lvl in asset_data.get("support", [])
                ],
                resistance=[
                    PriceLevel(
                        price=lvl["price"],
                        timestamp_seconds=lvl["timestamp_seconds"],
                        source_video_id=video.video_id,
                    )
                    for lvl in asset_data.get("resistance", [])
                ],
                strategy=asset_data.get("strategy", ""),
            )
        )
    return VideoAnalysis(
        video=video,
        assets=assets,
        macro_notes=data.get("macro_notes", ""),
        no_levels_mentioned=data.get("no_levels_mentioned", False),
    )


def analyze_video(client, video: Video, config: dict) -> VideoAnalysis:
    gemini_config = config["gemini"]
    contents = [
        types.Part.from_uri(file_uri=video.url, mime_type="video/*"),
        build_analysis_prompt(video),
    ]
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
    # Diagnostic: if the YouTube video is truly ingested, prompt_token_count is
    # large (tens of thousands). A few hundred means the model never read the
    # video and is answering from the prompt/title alone.
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        print(
            f"[usage] {video.video_id} ({video.channel_handle}): "
            f"prompt_tokens={getattr(usage, 'prompt_token_count', '?')} "
            f"candidates_tokens={getattr(usage, 'candidates_token_count', '?')} "
            f"total_tokens={getattr(usage, 'total_token_count', '?')}"
        )
    return parse_analysis_response(video, response.text)
