import json
import re
from pathlib import Path
from typing import Any

from .gemini_client import GeminiError, generate_gemini_text, gemini_configured, parse_json_response


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
VISUAL_DIR = WORKSPACE_DIR / "3_visual_template"


def _first_present(item: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return default


def _compact_summary(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"^[，。！？、；：\s]+|[，。！？、；：\s]+$", "", normalized)
    if len(normalized) <= 34:
        return normalized or "核心观点"

    cuts = re.split(r"[。！？；;]", normalized)
    best = next((part for part in cuts if 8 <= len(part) <= 34), "")
    if best:
        return best
    return normalized[:32] + "..."


def _visual_brief(text: str) -> str:
    summary = _compact_summary(text)
    if not summary.endswith(("。", "！", "？", "...")):
        summary += "。"
    return summary


def normalize_scene(item: dict[str, Any], index: int) -> dict[str, Any]:
    slide_id = str(_first_present(item, ("slide_id", "scene_id", "id"), f"scene_{index:03d}"))
    if slide_id.startswith("segment_"):
        slide_id = f"scene_{index:03d}"
    start = float(_first_present(item, ("start", "start_time"), 0))
    end = float(_first_present(item, ("end", "end_time"), start + 1))
    text = str(_first_present(item, ("text_content", "text", "content"), "")).strip()
    return {
        "slide_id": slide_id,
        "start": round(start, 3),
        "end": round(max(end, start + 0.2), 3),
        "text_content": text,
        "visual_summary": str(item.get("visual_summary") or _visual_brief(text)),
    }


def generate_fine_grained_timeline(story_plan: dict[str, Any] | None = None) -> Path:
    source = VISUAL_DIR / "scene_timeline.json"
    if not source.exists():
        raise FileNotFoundError(f"找不到模块 2 分镜资产: {source}")

    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("scene_timeline.json 必须是数组")

    fine = [normalize_scene(item, index) for index, item in enumerate(raw, 1)]
    if gemini_configured():
        system_prompt = (
            "你是鬼故事与都市小说视频的语义分镜导演。"
            "你只能输出严格 JSON 数组，不要 Markdown，不要解释。"
            "保持 slide_id、start、end、text_content 不变，只重写 visual_summary。"
            "visual_summary 必须是可直接用于惊悚漫画分镜的具体视觉镜头摘要，"
            "写清本镜头的人物、地点、动作、关键道具和悬念氛围，避免抽象金句和 PPT 表述。"
            "忠于原文事实，原文没有鬼怪、凶案或暴力时不得擅自添加；"
            "重复人物的身份与外观线索要前后一致，每条尽量控制在 60 个中文字符以内。"
        )
        # Agent 1 has already read the full text.  Module 3 only needs a small
        # continuity excerpt, not a second full-story planning call.
        plan_context = {
            "characters": (story_plan or {}).get("characters", [])[:10],
            "locations": (story_plan or {}).get("locations", [])[:10],
            "continuity_rules": (story_plan or {}).get("continuity_rules", [])[:10],
            "semantic_units": (story_plan or {}).get("semantic_units", [])[:36],
        }
        user_prompt = json.dumps(
            {"agent1_context": plan_context, "timeline": fine},
            ensure_ascii=False,
        )
        try:
            result = generate_gemini_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.25,
                response_mime_type="application/json",
            )
            gemini_items = parse_json_response(result)
            if isinstance(gemini_items, list) and len(gemini_items) == len(fine):
                normalized = []
                for index, item in enumerate(gemini_items, 1):
                    base = fine[index - 1]
                    visual_summary = str(item.get("visual_summary") or base["visual_summary"]).strip()
                    normalized.append({**base, "visual_summary": visual_summary})
                fine = normalized
        except (GeminiError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(f"Gemini 模块3语义分镜失败，使用本地摘要: {exc}", flush=True)

    out = VISUAL_DIR / "fine_grained_timeline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fine, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
