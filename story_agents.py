"""File-backed story agents used by the visual generation pipeline.

Agent 1 reads the complete story and produces a compact story bible.  Agent 2
lives in ``module4_video_render`` and consumes that bible when writing prompts.
The file boundary is intentional: it makes both stages inspectable and allows a
failed job to resume without starting a new, context-free model call.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from backend.app.gemini_client import (
    GeminiError,
    GeminiOutputTruncated,
    gemini_configured,
    generate_gemini_text,
    parse_json_response,
)


PROJECT_ROOT = Path(__file__).resolve().parent
VISUAL_DIR = PROJECT_ROOT / "workspace" / "3_visual_template"
STORY_PLAN_PATH = VISUAL_DIR / "story_plan.json"
CONTENT_MODE_STORY = "urban_suspense"
CONTENT_MODE_SCIENCE = "science_explainer"


def normalize_content_mode(value: str | None) -> str:
    return CONTENT_MODE_SCIENCE if str(value or "").strip().lower() == CONTENT_MODE_SCIENCE else CONTENT_MODE_STORY


STORY_AGENT_SYSTEM_PROMPT = """你是故事视频流水线的 Agent 1：故事策划与叙事编辑。

你必须先通读系统提供的全部文案片段，再从全局理解故事。你的结果将交给另一个分镜提示词 Agent，
所以重点是建立稳定、可复用的上下文，而不是直接写生图提示词。

只输出严格 JSON 对象，不要 Markdown，不要解释。字段必须为：
{
  "story_type": "ghost_story | urban_suspense | urban_drama | other",
  "logline": "一句话概括",
  "theme": "故事主题与核心思想",
  "narrative_tone": "叙述口吻与情绪基调",
  "characters": [
    {"name":"姓名或稳定代称","role":"作用","appearance":"固定外貌",
     "wardrobe":"固定服装","signature_item":"标志物","relationships":"人物关系"}
  ],
  "locations": [
    {"name":"地点","visual_identity":"稳定环境特征","time_and_light":"时间与光线"}
  ],
  "story_beats": [
    {"beat_id":"beat_01","slide_ids":["..."],"purpose":"情节作用",
     "emotion":"主情绪","visual_focus":"核心视觉信息"}
  ],
  "clues_and_payoffs": [
    {"clue":"线索","first_seen":"slide_id","payoff":"如何回收"}
  ],
  "continuity_rules": ["角色、地点、道具和时序必须保持的连续性规则"],
  "segmentation_guidance": {
    "target_chars":"建议每段 45-90 个中文字符，避免少于 25 字的孤立短段",
    "pause_rules":["适合停顿和换段的叙事规则"],
    "keep_together":["不能被拆开的信息类型"]
  },
  "visual_safety": ["在不改变原剧情的前提下规避生图审核风险的具体规则"]
}

要求：
- characters、locations、story_beats、continuity_rules 必须是数组。
- 最多输出 10 个主要人物、10 个核心地点、8 个 story_beats、8 条 continuity_rules 和 8 条线索；
  只保留会影响后续分镜一致性的内容，每个文字字段尽量控制在 80 个中文字符以内。
- story_beats 按原文顺序覆盖关键剧情，slide_ids 只能使用输入中存在的 ID。
- 不得凭空增加鬼怪、凶案、人物或关键事件；都市小说不能被强行改成鬼故事。
- 外貌、服装和场景特征要具体，供后续每批分镜重复使用。
- 手机、平板、书信或照片本身承载关键信息时，在 continuity_rules 中注明：采用正面主体特写或插入镜头，让物件占据画面主体，只保留必要的手部或桌面边缘，不使用第一视角或越肩机位。
- 禁止建议露骨血腥、肢解、器官、性暴力、自残细节或针对未成年人的性化画面；
  必要情节改用影子、遮挡、反应镜头、环境痕迹和事后氛围表达。
- 不输出逐张图片提示词，也不要复述整篇原文。"""


STORY_AGENT_COMPACT_RETRY_PROMPT = """你是故事视频 Agent 1。上一次回答因长度上限被截断，
这次必须只输出一个紧凑 JSON 对象，不要解释、不要 Markdown、不要复述原文。
仅输出字段：story_type、logline、theme、narrative_tone、characters、locations、story_beats、continuity_rules。
characters 最多 8 项，每项仅含 name、role、appearance、wardrobe、signature_item、relationships；
locations 最多 8 项，每项仅含 name、visual_identity、time_and_light；
story_beats 最多 8 项，每项仅含 beat_id、slide_ids、purpose、emotion、visual_focus；
continuity_rules 最多 8 条。所有文字字段尽量少于 60 个中文字符。
slide_ids 只能使用输入中存在的 ID。忠于原文，不增加事件；敏感事件使用非血腥、非直观的视觉表达。"""


SCIENCE_AGENT_SYSTEM_PROMPT = """你是科普口播视频流水线的 Agent 1：知识结构策划与讲解编辑。
你必须通读全部文案，建立供分镜 Agent 使用的全文知识上下文。只输出严格 JSON 对象，不要解释。
字段沿用统一结构：story_type 固定为 science_explainer；logline 写核心主题；theme 写学习目标；
narrative_tone 写讲解口吻；characters 记录固定讲解少女及原文必要人物；locations 记录实验、生活或行业场景；
story_beats 按讲解顺序拆成 4-8 个知识段，每项包含 beat_id、slide_ids、purpose、emotion、visual_focus；
clues_and_payoffs 记录概念与后续解释、问题与答案、现象与原因；continuity_rules 记录术语、物体和角色一致性；
segmentation_guidance 给出口播断句原则；visual_safety 给出安全可视化规则。

要求：
- 找出核心观点、前置概念、因果链、案例、数据、结论和行动建议，不使用鬼故事或悬疑叙事框架。
- 最多 8 个知识段、8 个关键概念、8 条连续性规则；文字字段尽量少于 80 个中文字符。
- 固定视觉主持人为黑色短发、红色围巾的可爱少女；她只在有助讲解时出现，不要每张图都站着讲话。
- 抽象知识优先建议生活化比喻、实验演示、物体对比和过程示意，不编造原文没有的数据或结论。
- 手机、平板、书信或照片本身承载关键信息时，采用正面主体特写或插入镜头，让物件占据画面主体，只保留必要的手部或桌面边缘，不使用第一视角或越肩机位。
- 不输出逐张图片提示词，不复述整篇原文。"""


SCIENCE_AGENT_COMPACT_RETRY_PROMPT = """你是科普口播 Agent 1。上次回答被长度截断。
只输出紧凑 JSON，字段仅限 story_type、logline、theme、narrative_tone、characters、locations、story_beats、continuity_rules。
story_type 为 science_explainer；characters 最多 4 项；locations 最多 6 项；story_beats 最多 8 项；
每个文字字段少于 60 个中文字符。story_beats 使用输入 slide_id，按核心观点、概念、证据、案例和结论组织。"""


SEGMENT_AGENT_SYSTEM_PROMPT = """你是故事视频流水线的 Agent 1B：局部分段策划。
系统会同时给你一份已经通读全文得到的全局故事设定，以及当前约 3000 字的原文片段。
请在不改变全局人物外貌、服装、标志物、人物关系、地点特征和剧情事实的前提下，细化当前片段。

只输出严格 JSON，不要 Markdown，不要解释。字段与全局设定相同：story_type、logline、theme、
narrative_tone、characters、locations、story_beats、clues_and_payoffs、continuity_rules、visual_safety。
story_beats 必须按当前片段顺序覆盖关键动作、转折、线索和情绪变化，slide_ids 只能使用当前片段中的 ID。
characters 中若出现全局已有角色，使用全局中的稳定姓名和设定，不得改写其固定外貌；原文只用关系称谓时，
应结合全局设定明确为“姓名/稳定代称 + 固定人物描写”。最多 8 个局部节拍，其余数组最多 8 项。
手机、平板、书信或照片承载信息时，采用正面主体特写或插入镜头，不使用第一视角或越肩机位。
忠于原文，不增加事件，不输出逐张生图提示词。"""


SCIENCE_SEGMENT_AGENT_SYSTEM_PROMPT = """你是科普口播视频流水线的 Agent 1B：局部知识段策划。
系统会给你全文知识总纲和当前约 3000 字片段。请保持全局术语、结论、数据关系与固定主持人设定，
细化当前片段的概念前置、因果链、案例、解释和结论。
只输出严格 JSON，字段与全局设定相同。story_beats 最多 8 项，按当前片段顺序组织，slide_ids 只能使用
当前片段中的 ID。不得编造数据或结论，不输出逐张生图提示词。"""


def story_fingerprint(
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
) -> str:
    compact = [
        {
            "slide_id": str(scene.get("slide_id") or ""),
            "text_content": str(scene.get("text_content") or ""),
            "visual_summary": str(scene.get("visual_summary") or ""),
        }
        for scene in scenes
    ]
    payload = json.dumps(
        {"content_mode": normalize_content_mode(content_mode), "scenes": compact},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _backup_json(path: Path) -> Path | None:
    if not path.is_file():
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.backup.{timestamp}{path.suffix}")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.stem}.backup.{timestamp}.{suffix}{path.suffix}")
        suffix += 1
    backup.write_bytes(path.read_bytes())
    return backup


def _fallback_story_plan(
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    content_mode = normalize_content_mode(content_mode)
    slide_ids = [str(scene.get("slide_id") or "") for scene in scenes]
    beats: list[dict[str, Any]] = []
    beat_size = max(1, (len(slide_ids) + 7) // 8)
    for index in range(0, len(slide_ids), beat_size):
        group = scenes[index : index + beat_size]
        summaries = [
            str(scene.get("visual_summary") or scene.get("text_content") or "").strip()
            for scene in group
        ]
        beats.append(
            {
                "beat_id": f"beat_{len(beats) + 1:02d}",
                "slide_ids": slide_ids[index : index + beat_size],
                "purpose": "承接并推进原文叙事",
                "emotion": "依据原文情绪递进",
                "visual_focus": "；".join(value for value in summaries if value)[:300],
            }
        )
    science_mode = content_mode == CONTENT_MODE_SCIENCE
    return {
        "content_mode": content_mode,
        "story_type": "science_explainer" if science_mode else "other",
        "logline": "依据原文结构讲清核心知识" if science_mode else "依据原文顺序讲述完整故事",
        "theme": "准确解释概念、原因、案例与结论" if science_mode else "忠于原文，不额外改写核心事实",
        "narrative_tone": "理性、清晰、可信、循序渐进" if science_mode else "克制、连贯、逐步推进",
        "characters": ([{
            "name": "科普少女",
            "role": "固定视觉主持人",
            "appearance": "可爱少女，黑色短发，始终佩戴红色围巾",
            "wardrobe": "简洁科教风服装，红色围巾",
            "signature_item": "红色围巾",
            "relationships": "负责串联知识讲解",
        }] if science_mode else []),
        "locations": [],
        "story_beats": beats,
        "clues_and_payoffs": [],
        "continuity_rules": (
            ["科普少女始终保持黑色短发和红色围巾", "术语、物体外观、数据关系与原文保持一致"]
            if science_mode
            else ["同一人物的外貌、服装与标志物保持一致", "地点与时间顺序忠于原文"]
        ),
        "segmentation_guidance": {
            "target_chars": "建议每段 45-90 个中文字符，避免少于 25 字的孤立短段",
            "pause_rules": (
                ["一个知识点讲清、因果链闭合、案例结束或进入结论时可换段"]
                if science_mode
                else ["情节转折、人物切换、地点变化或情绪落点处可换段"]
            ),
            "keep_together": (
                ["概念及其解释", "原因及其结果", "案例条件及其结论"]
                if science_mode
                else ["人物动作及其结果", "线索及其直接解释"]
            ),
        },
        "visual_safety": [
            "不生成露骨血腥、肢解、器官、性暴力或自残细节",
            "敏感情节使用阴影、遮挡、反应镜头和环境痕迹表达",
            "不凭空增加鬼怪、凶案或暴力",
        ],
    }


def _normalize_story_plan(
    raw: Any,
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    required_lists = ("characters", "locations", "story_beats", "continuity_rules")
    if any(not isinstance(raw.get(key), list) for key in required_lists):
        return None

    valid_ids = {str(scene.get("slide_id") or "") for scene in scenes}
    normalized_beats: list[dict[str, Any]] = []
    for index, beat in enumerate(raw.get("story_beats", []), 1):
        if not isinstance(beat, dict):
            continue
        ids = [str(value) for value in beat.get("slide_ids", []) if str(value) in valid_ids]
        if not ids:
            continue
        normalized_beats.append(
            {
                "beat_id": f"beat_{index:02d}",
                "slide_ids": ids,
                "purpose": str(beat.get("purpose") or "推进叙事").strip(),
                "emotion": str(beat.get("emotion") or "依据原文").strip(),
                "visual_focus": str(beat.get("visual_focus") or "依据原文场景").strip(),
            }
        )
    if not normalized_beats:
        return None

    content_mode = normalize_content_mode(content_mode)
    fallback = _fallback_story_plan(scenes, content_mode)
    plan = dict(fallback)
    for key in fallback:
        value = raw.get(key)
        if value not in (None, "", []):
            plan[key] = value
    plan["story_beats"] = normalized_beats
    plan["source_fingerprint"] = story_fingerprint(scenes, content_mode)
    plan["content_mode"] = content_mode
    plan["agent_version"] = 2
    return plan


def create_story_plan(
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    """Run Agent 1 once, with a deterministic local fallback."""
    content_mode = normalize_content_mode(content_mode)
    science_mode = content_mode == CONTENT_MODE_SCIENCE
    generation_source = "local_fallback"
    if not gemini_configured():
        print("Agent 1：Gemini 未配置，使用本地故事上下文。", flush=True)
        plan = _fallback_story_plan(scenes, content_mode)
    else:
        compact_scenes = [
            {
                "slide_id": str(scene.get("slide_id") or ""),
                "text": str(scene.get("text_content") or ""),
            }
            for scene in scenes
        ]
        print(f"Agent 1：正在通读全文并建立故事上下文（{len(scenes)} 个片段）...", flush=True)
        try:
            user_prompt = json.dumps({"complete_story": compact_scenes}, ensure_ascii=False)
            system_prompt = SCIENCE_AGENT_SYSTEM_PROMPT if science_mode else STORY_AGENT_SYSTEM_PROMPT
            retry_prompt = SCIENCE_AGENT_COMPACT_RETRY_PROMPT if science_mode else STORY_AGENT_COMPACT_RETRY_PROMPT
            try:
                response = generate_gemini_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.2,
                    response_mime_type="application/json",
                    max_output_tokens=8192,
                )
            except GeminiOutputTruncated as exc:
                print(f"Agent 1：首次输出被截断，正在使用紧凑结构自动重试: {exc}", flush=True)
                response = generate_gemini_text(
                    system_prompt=retry_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    response_mime_type="application/json",
                    max_output_tokens=12288,
                )
            plan = _normalize_story_plan(parse_json_response(response), scenes, content_mode)
            if plan is not None:
                generation_source = "gemini"
        except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"Agent 1 规划失败，使用本地故事上下文: {exc}", flush=True)
            plan = None
        if plan is None:
            plan = _fallback_story_plan(scenes, content_mode)

    plan["source_fingerprint"] = story_fingerprint(scenes, content_mode)
    plan["content_mode"] = content_mode
    plan["generation_source"] = generation_source
    plan["agent_version"] = 2
    return plan


def load_or_create_story_plan(
    scenes: list[dict[str, Any]],
    *,
    resume: bool = False,
    path: Path = STORY_PLAN_PATH,
    allow_source_mismatch: bool = False,
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    """Reuse a matching Agent 1 artifact or create and persist a new one."""
    content_mode = normalize_content_mode(content_mode)
    fingerprint = story_fingerprint(scenes, content_mode)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        source_matches = isinstance(existing, dict) and (
            existing.get("source_fingerprint") == fingerprint or allow_source_mismatch
        )
        plan_is_current = isinstance(existing, dict) and int(existing.get("agent_version") or 0) >= 2
        plan_is_ai = isinstance(existing, dict) and existing.get("generation_source") == "gemini"
        may_reuse_fallback = not gemini_configured()
        if source_matches and plan_is_current and (plan_is_ai or may_reuse_fallback):
            reason = "断点续跑" if resume else "上下文未变化"
            print(f"Agent 1：{reason}，复用故事规划: {path}", flush=True)
            return existing
        if source_matches and gemini_configured():
            print("Agent 1：发现旧版或本地降级规划，Gemini 已配置，将重新生成。", flush=True)

    plan = create_story_plan(scenes, content_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_json(path)
    if backup:
        print(f"Agent 1：已备份旧故事规划: {backup}", flush=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Agent 1：故事规划已保存: {path}", flush=True)
    return plan


def _merge_named_records(
    global_items: Any,
    local_items: Any,
) -> list[dict[str, Any]]:
    """Keep global identity fields authoritative while allowing local additions."""
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in global_items if isinstance(global_items, list) else []:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        name = str(record.get("name") or "").strip()
        if name:
            positions[name] = len(merged)
        merged.append(record)
    for item in local_items if isinstance(local_items, list) else []:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        name = str(record.get("name") or "").strip()
        if name and name in positions:
            current = merged[positions[name]]
            for key, value in record.items():
                if key not in current or current[key] in (None, "", []):
                    current[key] = value
        else:
            if name:
                positions[name] = len(merged)
            merged.append(record)
    return merged[:10]


def _unique_text_items(*values: Any, limit: int = 12) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        for item in value if isinstance(value, list) else []:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
            if marker not in seen:
                seen.add(marker)
                result.append(item)
            if len(result) >= limit:
                return result
    return result


def merge_global_and_segment_plan(
    global_plan: dict[str, Any],
    segment_plan: dict[str, Any],
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    """Build the Agent 2 context: global identity bible plus local story beats."""
    content_mode = normalize_content_mode(content_mode)
    merged = dict(segment_plan)
    merged["story_type"] = global_plan.get("story_type") or segment_plan.get("story_type")
    global_logline = str(global_plan.get("logline") or "").strip()
    local_logline = str(segment_plan.get("logline") or "").strip()
    merged["logline"] = "；当前段落：".join(value for value in (global_logline, local_logline) if value)
    merged["theme"] = global_plan.get("theme") or segment_plan.get("theme")
    merged["narrative_tone"] = global_plan.get("narrative_tone") or segment_plan.get("narrative_tone")
    merged["characters"] = _merge_named_records(global_plan.get("characters"), segment_plan.get("characters"))
    merged["locations"] = _merge_named_records(global_plan.get("locations"), segment_plan.get("locations"))
    merged["story_beats"] = list(segment_plan.get("story_beats") or [])
    merged["clues_and_payoffs"] = _unique_text_items(
        global_plan.get("clues_and_payoffs"), segment_plan.get("clues_and_payoffs"), limit=12
    )
    merged["continuity_rules"] = _unique_text_items(
        global_plan.get("continuity_rules"), segment_plan.get("continuity_rules"), limit=12
    )
    merged["visual_safety"] = _unique_text_items(
        global_plan.get("visual_safety"), segment_plan.get("visual_safety"), limit=10
    )
    merged["source_fingerprint"] = story_fingerprint(scenes, content_mode)
    merged["global_source_fingerprint"] = global_plan.get("source_fingerprint")
    merged["content_mode"] = content_mode
    merged["planning_scope"] = "hierarchical_segment"
    merged["agent_version"] = 3
    return merged


def create_segment_story_plan(
    scenes: list[dict[str, Any]],
    global_plan: dict[str, Any],
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    """Run Agent 1B for one long-text segment, constrained by the global plan."""
    content_mode = normalize_content_mode(content_mode)
    generation_source = "local_fallback"
    local_plan: dict[str, Any] | None = None
    if gemini_configured():
        payload = {
            "global_story_bible": story_context_for_prompt(global_plan),
            "current_segment": [
                {
                    "slide_id": str(scene.get("slide_id") or ""),
                    "text": str(scene.get("text_content") or ""),
                }
                for scene in scenes
            ],
        }
        try:
            response = generate_gemini_text(
                system_prompt=(
                    SCIENCE_SEGMENT_AGENT_SYSTEM_PROMPT
                    if content_mode == CONTENT_MODE_SCIENCE
                    else SEGMENT_AGENT_SYSTEM_PROMPT
                ),
                user_prompt=json.dumps(payload, ensure_ascii=False),
                temperature=0.15,
                response_mime_type="application/json",
                max_output_tokens=8192,
            )
            local_plan = _normalize_story_plan(parse_json_response(response), scenes, content_mode)
            if local_plan is not None:
                generation_source = "gemini"
        except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"Agent 1B 局部规划失败，使用本地分段上下文: {exc}", flush=True)
    if local_plan is None:
        local_plan = _fallback_story_plan(scenes, content_mode)
    result = merge_global_and_segment_plan(global_plan, local_plan, scenes, content_mode)
    result["generation_source"] = generation_source
    return result


def load_or_create_segment_story_plan(
    scenes: list[dict[str, Any]],
    global_plan: dict[str, Any],
    *,
    resume: bool = False,
    path: Path,
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    """Persist and safely reuse an Agent 1B plan for one video segment."""
    content_mode = normalize_content_mode(content_mode)
    fingerprint = story_fingerprint(scenes, content_mode)
    global_fingerprint = global_plan.get("source_fingerprint")
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        matches = (
            isinstance(existing, dict)
            and existing.get("source_fingerprint") == fingerprint
            and existing.get("global_source_fingerprint") == global_fingerprint
            and existing.get("planning_scope") == "hierarchical_segment"
            and int(existing.get("agent_version") or 0) >= 3
        )
        reusable = matches and (
            existing.get("generation_source") == "gemini" or not gemini_configured()
        )
        if reusable:
            reason = "断点续跑" if resume else "分段内容未变化"
            print(f"Agent 1B：{reason}，复用局部规划: {path}", flush=True)
            return existing

    print(f"Agent 1B：细化当前长文分段（{len(scenes)} 个片段）...", flush=True)
    plan = create_segment_story_plan(scenes, global_plan, content_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_json(path)
    if backup:
        print(f"Agent 1B：已备份旧局部规划: {backup}", flush=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Agent 1B：局部规划已保存: {path}", flush=True)
    return plan


def story_context_for_prompt(plan: dict[str, Any]) -> dict[str, Any]:
    """Return only narrative fields useful to Agent 2, excluding bookkeeping."""
    keys = (
        "story_type",
        "logline",
        "theme",
        "narrative_tone",
        "characters",
        "locations",
        "story_beats",
        "clues_and_payoffs",
        "continuity_rules",
        "visual_safety",
    )
    return {key: plan.get(key) for key in keys if key in plan}
