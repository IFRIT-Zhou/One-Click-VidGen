from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


MAX_SCENES = 16
MAX_NARRATION_CHARACTERS = 1_500
MAX_PROMPT_CHARACTERS = 4_000


@dataclass(frozen=True)
class StoryboardError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def build_storyboard_messages(
    *,
    script: str,
    scene_count: int,
    visual_style: str,
    aspect_ratio: str,
) -> list[dict[str, str]]:
    if not script.strip():
        raise StoryboardError("EMPTY_SCRIPT", "Script must not be empty")
    if not 2 <= scene_count <= MAX_SCENES:
        raise StoryboardError("INVALID_SCENE_COUNT", "Scene count must be between 2 and 16")

    system = (
        "You are a video storyboard planner. Return only valid JSON with a top-level "
        "scenes array. Create exactly the requested number of scenes. Preserve the source "
        "script verbatim and in order across narration fields, without adding spoken words. "
        "Each scene must contain index, title, narration, description, and image_prompt. "
        "image_prompt must describe one coherent still frame and must not request visible text."
    )
    user = (
        f"Scene count: {scene_count}\n"
        f"Visual style: {visual_style.strip() or 'cinematic realistic'}\n"
        f"Aspect ratio: {aspect_ratio}\n"
        "Source script:\n"
        f"{script.strip()}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_fallback_storyboard(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Build a strict local storyboard when the optional text model has no credit."""
    user = next(
        (str(item.get("content") or "") for item in messages if item.get("role") == "user"),
        "",
    )
    match = re.fullmatch(
        r"Scene count: (\d+)\nVisual style: (.*?)\nAspect ratio: (.*?)\n"
        r"Source script:\n(.*)",
        user,
        flags=re.DOTALL,
    )
    if match is None:
        raise StoryboardError(
            "INVALID_FALLBACK_REQUEST", "Storyboard fallback request is malformed"
        )
    scene_count = int(match.group(1))
    style = match.group(2).strip() or "cinematic realistic"
    aspect_ratio = match.group(3).strip()
    script = match.group(4).strip()
    if not 2 <= scene_count <= MAX_SCENES or len(script) < scene_count:
        raise StoryboardError(
            "INVALID_FALLBACK_REQUEST", "Script is too short for deterministic scenes"
        )

    boundaries = [round(len(script) * index / scene_count) for index in range(scene_count + 1)]
    scenes: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        narration = script[start:end]
        visual = narration.strip(" \t\r\n，。！？；：,.!?;:") or narration.strip()
        scenes.append({
            "index": index,
            "title": f"Scene {index + 1}",
            "narration": narration,
            "description": visual,
            "image_prompt": (
                f"{style}, {aspect_ratio} composition, a coherent cinematic still frame "
                f"visually inspired by this scene: {visual}. Natural detail, no visible "
                "text, no captions, no watermark."
            ),
        })
    return {"scenes": scenes}


def _json_payload(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StoryboardError("INVALID_STORYBOARD_JSON", "Model returned invalid storyboard JSON") from exc
    if not isinstance(payload, dict):
        raise StoryboardError("INVALID_STORYBOARD", "Storyboard must be a JSON object")
    return payload


def _spoken_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def parse_storyboard(
    value: str | dict[str, Any],
    *,
    source_script: str,
    expected_scene_count: int,
) -> list[dict[str, Any]]:
    payload = _json_payload(value)
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or len(raw_scenes) != expected_scene_count:
        raise StoryboardError(
            "INVALID_SCENE_COUNT",
            f"Storyboard must contain exactly {expected_scene_count} scenes",
        )

    scenes: list[dict[str, Any]] = []
    for offset, raw in enumerate(raw_scenes):
        if not isinstance(raw, dict):
            raise StoryboardError("INVALID_SCENE", f"Scene {offset + 1} must be an object")
        narration = str(raw.get("narration") or "").strip()
        prompt = str(raw.get("image_prompt") or raw.get("prompt") or "").strip()
        description = str(raw.get("description") or "").strip()
        title = str(raw.get("title") or f"Scene {offset + 1}").strip()
        if not narration or not prompt:
            raise StoryboardError(
                "INVALID_SCENE",
                f"Scene {offset + 1} requires narration and image_prompt",
            )
        if len(narration) > MAX_NARRATION_CHARACTERS or len(prompt) > MAX_PROMPT_CHARACTERS:
            raise StoryboardError("SCENE_TOO_LARGE", f"Scene {offset + 1} exceeds field limits")
        scenes.append(
            {
                "index": offset,
                "title": title[:160],
                "narration": narration,
                "description": description[:2_000],
                "prompt": prompt,
            }
        )

    if _spoken_text("".join(scene["narration"] for scene in scenes)) != _spoken_text(source_script):
        raise StoryboardError(
            "SCRIPT_COVERAGE_MISMATCH",
            "Storyboard narration must preserve the complete source script in order",
        )
    return scenes


def completion_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise StoryboardError("INVALID_MODEL_RESPONSE", "Model response contains no storyboard content") from exc
    if not isinstance(content, str) or not content.strip():
        raise StoryboardError("INVALID_MODEL_RESPONSE", "Model response contains no storyboard content")
    return content
