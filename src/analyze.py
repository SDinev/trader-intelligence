import json

from google.genai import types

from models import AssetLevels, PriceLevel, Video, VideoAnalysis

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
