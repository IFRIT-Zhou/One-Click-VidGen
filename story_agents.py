"""File-backed story agents used by the visual generation pipeline.

Agent 1 reads the complete story and produces a compact story bible.  Agent 2
lives in ``module4_video_render`` and consumes that bible when writing prompts.
The file boundary is intentional: it makes both stages inspectable and allows a
failed job to resume without starting a new, context-free model call.
"""

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun
# AGPL-3.0 Section 7 terms: ADDITIONAL_TERMS.md

from __future__ import annotations

import hashlib
import json
import os
import re
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
STORY_CONTEXT_PATH = VISUAL_DIR / "story_context.json"
CONTENT_MODE_STORY = "urban_suspense"
CONTENT_MODE_SCIENCE = "science_explainer"
CONTENT_MODE_PURE_SCIENCE = "pure_science"
CONTENT_MODE_GENERAL = "general"
STORY_AGENT_VERSION = 12
CHARACTER_CONTINUITY_VERSION = 4
STORY_CONTEXT_VERSION = 4


class AgentPlanningFatalError(RuntimeError):
    """The AI planning chain failed and image generation must not continue."""


def _planning_failure(stage: str, reason: object) -> AgentPlanningFatalError:
    detail = str(reason or "未知错误").strip()
    return AgentPlanningFatalError(
        f"{stage} 语言模型规划失败，已在提交图像任务前安全终止；"
        f"配音与字幕已保留，可排除 API Key、余额、限流或上游服务问题后断点续跑。"
        f"原始错误：{detail}"
    )


def normalize_content_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode == CONTENT_MODE_SCIENCE:
        return CONTENT_MODE_SCIENCE
    if mode == CONTENT_MODE_PURE_SCIENCE:
        return CONTENT_MODE_PURE_SCIENCE
    if mode == CONTENT_MODE_GENERAL:
        return CONTENT_MODE_GENERAL
    return CONTENT_MODE_STORY


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
     "wardrobe":"服装变化摘要，不得使用含糊的前期/后期二选一描述",
     "wardrobe_states":[
       {"state_id":"look_01","start_slide_id":"scene_001","end_slide_id":"scene_020",
        "wardrobe":"该阶段唯一明确服装","headwear":"该阶段明确头部状态","carried_items":"手持或随身物品"}
     ],
     "signature_item":"标志物及明确佩戴规则","relationships":"人物关系"}
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
- appearance 只能写年龄、脸型、肤色、身材、固定发型等不会随剧情改变的身份特征，禁止写“前期/后期”。
- 只要主要人物存在换装、脱帽、戴头盔、回忆或时间跳转，就必须填写 wardrobe_states；每个状态用起止
  slide_id 覆盖连续剧情范围，并且 wardrobe/headwear/carried_items 各自只写该阶段实际状态，禁止写“A或B”。
- 必须区分“戴着某物”和“手里拿着某物”。头盔、帽子等不得只写成含糊的 signature_item。
- 手机、平板、书信或照片本身承载关键信息时，在 continuity_rules 中注明：采用正面主体特写或插入镜头，让物件占据画面主体，只保留必要的手部或桌面边缘，不使用第一视角或越肩机位。
- 禁止建议露骨血腥、肢解、器官、性暴力、自残细节或针对未成年人的性化画面；
  必要情节改用影子、遮挡、反应镜头、环境痕迹和事后氛围表达。
- 不输出逐张图片提示词，也不要复述整篇原文。"""


STORY_AGENT_COMPACT_RETRY_PROMPT = """你是故事视频 Agent 1。上一次回答因长度上限被截断，
这次必须只输出一个紧凑 JSON 对象，不要解释、不要 Markdown、不要复述原文。
仅输出字段：story_type、logline、theme、narrative_tone、characters、locations、story_beats、continuity_rules。
characters 最多 8 项，每项仅含 name、role、appearance、wardrobe、wardrobe_states、signature_item、relationships；
如人物有换装或头部状态变化，wardrobe_states 最多 6 项，每项仅含 state_id、start_slide_id、
end_slide_id、wardrobe、headwear、carried_items；每个状态只允许一种明确造型，不得写“A或B”；
locations 最多 8 项，每项仅含 name、visual_identity、time_and_light；
story_beats 最多 8 项，每项仅含 beat_id、slide_ids、purpose、emotion、visual_focus；
continuity_rules 最多 8 条。所有文字字段尽量少于 60 个中文字符。
slide_ids 只能使用输入中存在的 ID。忠于原文，不增加事件；敏感事件使用非血腥、非直观的视觉表达。"""

GENERAL_AGENT_SYSTEM_PROMPT = STORY_AGENT_SYSTEM_PROMPT.replace(
    "故事视频流水线的 Agent 1：故事策划与叙事编辑",
    "通用视频流水线的 Agent 1：内容策划与叙事编辑",
).replace(
    "不得凭空增加鬼怪、凶案、人物或关键事件；都市小说不能被强行改成鬼故事。",
    "不得凭空增加人物、事件或题材设定；普通内容不能被强行改成鬼故事、科普或其他特定题材。",
)
GENERAL_AGENT_COMPACT_RETRY_PROMPT = STORY_AGENT_COMPACT_RETRY_PROMPT.replace(
    "故事视频 Agent 1", "通用视频 Agent 1"
)


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
- 原文人物存在换装或头部状态变化时，characters 必须使用 wardrobe_states 按 slide_id 起止范围记录，
  每个状态只写当时唯一明确的服装、头部状态和随身物品。
- 抽象知识优先建议生活化比喻、实验演示、物体对比和过程示意，不编造原文没有的数据或结论。
- 手机、平板、书信或照片本身承载关键信息时，采用正面主体特写或插入镜头，让物件占据画面主体，只保留必要的手部或桌面边缘，不使用第一视角或越肩机位。
- 不输出逐张图片提示词，不复述整篇原文。"""


SCIENCE_AGENT_COMPACT_RETRY_PROMPT = """你是科普口播 Agent 1。上次回答被长度截断。
只输出紧凑 JSON，字段仅限 story_type、logline、theme、narrative_tone、characters、locations、story_beats、continuity_rules。
story_type 为 science_explainer；characters 最多 4 项；locations 最多 6 项；story_beats 最多 8 项；
每个文字字段少于 60 个中文字符。人物换装时保留 wardrobe_states，每个状态包含起止 slide_id 和唯一造型。
story_beats 使用输入 slide_id，按核心观点、概念、证据、案例和结论组织。"""


SEGMENT_AGENT_SYSTEM_PROMPT = """你是故事视频流水线的 Agent 1B：局部分段策划。
系统会同时给你一份已经通读全文得到的全局故事设定，以及当前约 3000 字的原文片段。
请在不改变全局人物外貌、服装、标志物、人物关系、地点特征和剧情事实的前提下，细化当前片段。

只输出严格 JSON，不要 Markdown，不要解释。字段与全局设定相同：story_type、logline、theme、
narrative_tone、characters、locations、story_beats、clues_and_payoffs、continuity_rules、visual_safety。
story_beats 必须按当前片段顺序覆盖关键动作、转折、线索和情绪变化，slide_ids 只能使用当前片段中的 ID。
characters 中若出现全局已有角色，使用全局中的稳定姓名和设定，不得改写其固定外貌；原文只用关系称谓时，
应结合全局设定明确为“姓名/稳定代称 + 固定人物描写”。最多 8 个局部节拍，其余数组最多 8 项。
必须继承并细化当前片段适用的 wardrobe_states；每个镜头阶段只能选择一套明确服装和一种头部状态，
禁止把“前期居家服、后期骑行服”整句交给下游。
手机、平板、书信或照片承载信息时，采用正面主体特写或插入镜头，不使用第一视角或越肩机位。
忠于原文，不增加事件，不输出逐张生图提示词。"""


SCIENCE_SEGMENT_AGENT_SYSTEM_PROMPT = """你是科普口播视频流水线的 Agent 1B：局部知识段策划。
系统会给你全文知识总纲和当前约 3000 字片段。请保持全局术语、结论、数据关系与固定主持人设定，
细化当前片段的概念前置、因果链、案例、解释和结论。
只输出严格 JSON，字段与全局设定相同。story_beats 最多 8 项，按当前片段顺序组织，slide_ids 只能使用
当前片段中的 ID。不得编造数据或结论，不输出逐张生图提示词。"""

GENERAL_SEGMENT_AGENT_SYSTEM_PROMPT = SEGMENT_AGENT_SYSTEM_PROMPT.replace(
    "故事视频流水线的 Agent 1B：局部分段策划",
    "通用视频流水线的 Agent 1B：局部内容策划",
)


def story_fingerprint(
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
) -> str:
    compact = [
        {
            "slide_id": str(scene.get("slide_id") or ""),
            "text_content": str(scene.get("text_content") or ""),
            "source_paragraph_id": str(scene.get("source_paragraph_id") or ""),
            "source_boundary_after": str(scene.get("source_boundary_after") or ""),
        }
        for scene in scenes
    ]
    payload = json.dumps(
        {
            "content_mode": normalize_content_mode(content_mode),
            "agent1_prompt": hashlib.sha1(
                os.getenv("AGENT1_PROMPT_SYSTEM", "").strip().encode("utf-8")
            ).hexdigest(),
            "scenes": compact,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def story_context_fingerprint(
    full_text: str,
    content_mode: str = CONTENT_MODE_STORY,
    global_character_prompt: str = "",
    world_prompt: str = "",
    agent0_prompt_system: str = "",
) -> str:
    """Agent 0 cache key.  It deliberately has no subtitle timing data."""
    payload = json.dumps({
        "content_mode": normalize_content_mode(content_mode),
        "full_text": str(full_text or "").strip(),
        "global_character_prompt": str(global_character_prompt or "").strip(),
        "world_prompt": str(world_prompt or "").strip(),
        "agent0_prompt": hashlib.sha1(
            str(agent0_prompt_system or "").strip().encode("utf-8")
        ).hexdigest(),
        "version": STORY_CONTEXT_VERSION,
    }, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


AGENT0_SYSTEM_PROMPT = """你是视频流水线的 Agent 0：全文内容总编。
你只负责通读一篇完整文案，建立可复用的全局资料；不要规划字幕分组、镜头时长、slide_id，也不要写生图提示词。
只输出严格 JSON 对象，不要 Markdown。字段必须包括：
story_type、logline、theme、narrative_tone、characters、locations、key_information_objects、clues_and_payoffs、continuity_rules、visual_safety。
characters 每项只包含 character_id、name、aliases、group_aliases、role、appearance、wardrobe、signature_item、relationships；不要填写 wardrobe_states。
character_id 必须是唯一且稳定的英文小写 ID（例如 wife、husband、lin_wan）；name 必须是能唯一指向一个人的姓名或稳定代称。
aliases 只能放该人物独占的姓名或称呼；“家庭经营者、同事、村民”等可能同时指向多人的职业或群体称呼只能放进 group_aliases，禁止当作个人 name 或独占 aliases。
不同人物不得使用相同 name、character_id 或独占 alias。夫妻、父母、兄妹等没有姓名的人，必须分别命名为“妻子/丈夫”“母亲/父亲”等可区分的稳定代称。
忠于原文，不得编造人物、事件、数据或世界观。用户人物设定与世界设定优先于你的推断。
key_information_objects 只登记会被手机、平板、电脑显示器等设备展示并影响剧情理解的信息载体；
每项仅包含 object_id、device_type、content、first_context、later_references。content 必须忠于原文，原文没有明确内容时不得猜测或补写。
输出应紧凑：人物最多 10 个、地点最多 10 个、关键信息载体最多 10 个、连续性规则最多 10 条。"""

AGENT0_IDENTITY_CONTRACT = """【系统固定角色身份结构】
- characters 中每个自然人必须拥有唯一 character_id、唯一 name；character_id 使用英文小写字母、数字或下划线。
- aliases 只允许该人物独占的姓名或称呼；多人共用的职业、关系类别或群体称呼只能写入 group_aliases。
- 不得用“家庭经营者、同事、村民、主角”等可能指向多人的词作为不同人物共同的 name 或 aliases。
- 即使用户使用共同职业描述人物，也必须分别建立可区分的稳定人物，例如“妻子/wife”“丈夫/husband”。"""


AGENT0_DEVICE_INFORMATION_CONTRACT = """【系统固定设备信息结构】
- 必须输出 key_information_objects 数组，只登记原文明确给出的手机、平板、电脑显示器等屏幕内容，以及后文对同一内容的稳定指代。
- content 只能摘录或忠实概括原文已经明确说明的文字、照片、监控、网页或文件内容；只有“看手机、收到消息、打开电脑”等动作而内容不明时，不得登记虚构内容。
- 不规划具体镜头；该资料仅供 Agent 1 判断后文“那条消息、那张照片、那个文件”是否已有明确内容。"""

PURE_SCIENCE_AGENT0_CONTRACT = """【纯科普模式硬约束】
- 本模式没有默认主持人、默认少女或固定主角；禁止为了串联讲解而凭空创建人物。
- characters 只登记原文明确参与内容的人物，或用户在人物设定中明确要求的角色。纯概念、结构、机制、实验和数据讲解通常应输出空数组 []。
- 分子、细胞、器官、粒子、天体、地层、公式、算法、装置、国家、机构和抽象概念不是人物，不得写入 characters。
- 先识别真实学科；整理该学科的术语、结构、状态转换、因果链、时间空间关系、证据、实验或史料对象和必要标签，禁止默认把非生物学内容改写成细胞或分子题材。"""

PURE_SCIENCE_AGENT0_SYSTEM_PROMPT = AGENT0_SYSTEM_PROMPT + "\n\n" + PURE_SCIENCE_AGENT0_CONTRACT


def _normalize_key_information_objects(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [
        {
            "object_id": str(item.get("object_id") or f"device_info_{index:02d}").strip()[:80],
            "device_type": str(item.get("device_type") or "device").strip()[:40],
            "content": str(item.get("content") or "").strip()[:300],
            "first_context": str(item.get("first_context") or "").strip()[:160],
            "later_references": _string_list(item.get("later_references"), limit=8),
        }
        for index, item in enumerate(raw[:10], 1)
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]


def _fallback_story_context(
    full_text: str,
    content_mode: str,
    global_character_prompt: str = "",
    world_prompt: str = "",
    agent0_prompt_system: str = "",
) -> dict[str, Any]:
    context = _fallback_story_plan(
        [{"slide_id": "context_001", "text_content": full_text}],
        content_mode,
    )
    for key in ("story_beats", "semantic_units", "segmentation_guidance"):
        context.pop(key, None)
    if global_character_prompt:
        context["user_global_character_bible"] = global_character_prompt
    if world_prompt:
        context["world_bible"] = world_prompt
    context["generation_source"] = "local_fallback"
    return context


def _normalize_story_context(
    raw: Any,
    full_text: str,
    content_mode: str,
    global_character_prompt: str = "",
    world_prompt: str = "",
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    required = ("characters", "locations", "continuity_rules")
    if any(not isinstance(raw.get(key), list) for key in required):
        return None
    fallback = _fallback_story_context(full_text, content_mode, global_character_prompt, world_prompt)
    context = dict(fallback)
    for key in ("story_type", "logline", "theme", "narrative_tone", "characters", "locations", "key_information_objects", "clues_and_payoffs", "continuity_rules", "visual_safety"):
        value = raw.get(key)
        if value not in (None, "", []):
            context[key] = value
    # Agent 0 intentionally has no timeline.  Stable identity is useful here;
    # time-bounded wardrobe states remain an Agent 1 concern if needed later.
    context["characters"] = _normalize_characters(context.get("characters"), [])
    for character in context["characters"]:
        character["wardrobe_states"] = []
    context["content_mode"] = normalize_content_mode(content_mode)
    context["key_information_objects"] = _normalize_key_information_objects(
        context.get("key_information_objects")
    )
    context["source_fingerprint"] = story_context_fingerprint(full_text, content_mode, global_character_prompt, world_prompt)
    context["agent0_version"] = STORY_CONTEXT_VERSION
    context["user_global_character_bible"] = global_character_prompt
    if world_prompt:
        context["world_bible"] = world_prompt
    return context


def create_story_context(
    full_text: str,
    content_mode: str = CONTENT_MODE_STORY,
    *,
    global_character_prompt: str = "",
    world_prompt: str = "",
    agent0_prompt_system: str = "",
    require_ai_success: bool = False,
) -> dict[str, Any]:
    """Run Agent 0 once: full-text understanding without timing decisions."""
    content_mode = normalize_content_mode(content_mode)
    full_text = str(full_text or "").strip()
    fallback = _fallback_story_context(full_text, content_mode, global_character_prompt, world_prompt)
    fallback["source_fingerprint"] = story_context_fingerprint(
        full_text,
        content_mode,
        global_character_prompt,
        world_prompt,
        agent0_prompt_system,
    )
    if not gemini_configured():
        if require_ai_success:
            raise _planning_failure("Agent 0", "语言模型未配置")
        return fallback
    try:
        custom_prompt = str(agent0_prompt_system or "").strip()
        system_prompt = custom_prompt or (
            PURE_SCIENCE_AGENT0_SYSTEM_PROMPT
            if content_mode == CONTENT_MODE_PURE_SCIENCE
            else AGENT0_SYSTEM_PROMPT
        )
        if custom_prompt and "continuity_rules" not in custom_prompt:
            system_prompt += "\n\n" + AGENT0_SYSTEM_PROMPT
        # Expert prompts may change creative analysis, but may not remove the
        # machine-readable identity contract required by downstream stages.
        system_prompt += "\n\n" + AGENT0_IDENTITY_CONTRACT
        system_prompt += "\n\n" + AGENT0_DEVICE_INFORMATION_CONTRACT
        if content_mode == CONTENT_MODE_PURE_SCIENCE and PURE_SCIENCE_AGENT0_CONTRACT not in system_prompt:
            system_prompt += "\n\n" + PURE_SCIENCE_AGENT0_CONTRACT
        response = generate_gemini_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps({
                "complete_text": full_text,
                "user_global_character_bible": global_character_prompt,
                "user_world_bible": world_prompt,
            }, ensure_ascii=False),
            temperature=0.15,
            response_mime_type="application/json",
            max_output_tokens=4096,
        )
        raw_context = parse_json_response(response)
        context = _normalize_story_context(
            raw_context, full_text, content_mode,
            global_character_prompt, world_prompt,
        )
        if context is not None:
            issues = character_registry_issues(context.get("characters"))
            if issues:
                print(f"Agent 0：角色档案发现 {len(issues)} 个身份冲突，正在自动修订一次。", flush=True)
                repaired_response = generate_gemini_text(
                    system_prompt=system_prompt + (
                        "\n\n【角色档案修订】必须修正程序列出的身份冲突。"
                        "每个自然人使用唯一 character_id 和 name；共同职业只能放 group_aliases。"
                        "保留原故事事实及除 characters 外的全部字段。"
                    ),
                    user_prompt=json.dumps({
                        "complete_text": full_text,
                        "user_global_character_bible": global_character_prompt,
                        "previous_output": raw_context,
                        "validation_errors": issues,
                    }, ensure_ascii=False),
                    temperature=0.05,
                    response_mime_type="application/json",
                    max_output_tokens=4096,
                )
                repaired = _normalize_story_context(
                    parse_json_response(repaired_response), full_text, content_mode,
                    global_character_prompt, world_prompt,
                )
                if repaired is not None and not character_registry_issues(repaired.get("characters")):
                    context = repaired
                else:
                    # Safe fallback: keep the story context, but drop ambiguous
                    # duplicate identities instead of sending conflicting cards downstream.
                    seen_names: set[str] = set()
                    safe_characters: list[dict[str, Any]] = []
                    for character in context.get("characters", []):
                        name = str(character.get("name") or "").strip()
                        if name and name not in seen_names:
                            seen_names.add(name)
                            safe_characters.append(character)
                    context["characters"] = safe_characters
                    context["character_registry_warning"] = issues
            context["generation_source"] = "gemini"
            context["source_fingerprint"] = story_context_fingerprint(
                full_text,
                content_mode,
                global_character_prompt,
                world_prompt,
                agent0_prompt_system,
            )
            return context
    except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if require_ai_success:
            raise _planning_failure("Agent 0", exc) from exc
        print(f"Agent 0 全文资料规划失败，使用本地资料: {exc}", flush=True)
    if require_ai_success:
        raise _planning_failure("Agent 0", "模型返回内容无法形成有效的全文资料")
    return fallback


def load_or_create_story_context(
    full_text: str,
    *,
    resume: bool = False,
    path: Path = STORY_CONTEXT_PATH,
    content_mode: str = CONTENT_MODE_STORY,
    global_character_prompt: str = "",
    world_prompt: str = "",
    agent0_prompt_system: str = "",
    require_ai_success: bool = False,
) -> dict[str, Any]:
    fingerprint = story_context_fingerprint(full_text, content_mode, global_character_prompt, world_prompt, agent0_prompt_system)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        existing_is_ai = isinstance(existing, dict) and existing.get("generation_source") == "gemini"
        if (
            isinstance(existing, dict)
            and existing.get("source_fingerprint") == fingerprint
            and int(existing.get("agent0_version") or 0) >= STORY_CONTEXT_VERSION
            and (existing_is_ai or not require_ai_success)
        ):
            print(f"Agent 0：{'断点续跑' if resume else '全文未变化'}，复用全文资料: {path}", flush=True)
            return existing
    print("Agent 0：正在通读全文并建立全局资料...", flush=True)
    context = create_story_context(
        full_text, content_mode,
        global_character_prompt=global_character_prompt,
        world_prompt=world_prompt,
        agent0_prompt_system=agent0_prompt_system,
        require_ai_success=require_ai_success,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup_json(path)
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Agent 0：全文资料已保存: {path}", flush=True)
    return context


SEMANTIC_UNIT_RULES = """

【语义镜头单元（必须输出）】
额外输出 semantic_units 数组。它是 Agent 1 为后续画面分组提供的连续叙事单元，而不是逐图提示词。
每项仅包含 unit_id、start_slide_id、end_slide_id、purpose、visual_focus、visual_mode、setting_hint、novelty_anchor、visual_pacing、boundary_after、character_ids、device_shot_mode、device_type、screen_content。
所有单元必须严格按原文顺序首尾相接，完整覆盖每一个输入 slide_id，不能遗漏、重叠或倒序。
把同一动作、同一环境建立镜头、同一段对话或同一件事的连续描述放在同一单元；
不要因为一个长句中的分号、列举物件或修饰语就切开。只有事件、人物、地点、时间或叙事焦点明显变化时才新建单元。
visual_mode 只能是 literal_scene、illustrative_broll、symbolic：正在发生的动作使用 literal_scene；
旁白明确提到的原因、经历、日常负担、未来设想或社会处境，优先用 illustrative_broll 转成可见场景；
无法安全具象化的抽象结论才使用 symbolic。illustrative_broll 是说明性画面，不等于宣称它正在主时间线发生。
setting_hint 必须写当前画面最合适的一个具体地点；novelty_anchor 写相较前后画面新增的动作、物件或空间信息。
连续单元不得仅靠更换景别、机位或表情制造变化。同一地点加同一道具最多连续使用两次；第三次必须改用原文支持的
具体行动、环境、物件特写、说明性 B-roll 或象征画面，除非三段确实描述同一个不可中断的动作。
visual_pacing 只能是 hold、normal、fast。单元应尽量精炼，最多 96 项。
"""


DEVICE_SHOT_CONTRACT = """【设备画面三态（系统固定，必须执行）】
- 每个 semantic_units 项必须输出 device_shot_mode，只能是 none、device_interaction、screen_insert。
- screen_insert：原文或 Agent 0 的 key_information_objects 已明确给出手机、平板、电脑显示器中的文字、照片、监控、网页或文件内容，而且该内容是本单元的视觉重点。此时 device_type 写具体设备，screen_content 只写原文已有内容；character_ids 必须为 []，后续只拍设备正面内容，不拍人物脸部或人物特写。
- device_interaction：人物正在看、拿、操作、接听设备，但屏幕内容没有明确给出或并非本镜头重点。screen_content 必须为空；人物可以出镜，但屏幕必须不可读，不得编造聊天、照片、网页、文件或界面文字。
- none：设备未出现，或只是与核心画面无关的普通背景道具。screen_content 必须为空。
- screen_insert 必须精确限定在实际描述该项内容的字幕范围内；同一较长事件里，如果前面只是铺垫、后面已经转入人物反应、法律结果、搬家或其他后果，必须在内容特写结束处切出新的 semantic_unit，后续单元改为 none 或 device_interaction。禁止让一份屏幕/文件内容跨越多个不再描述它的画面事件。
- 不能仅因出现“消息、手机、电脑”等词就选择 screen_insert；必须能够从原文或 Agent 0 已登记的信息载体中指出具体内容。后文出现“那条消息、那张照片、那个文件”等指代时，应结合 Agent 0 全文资料判断。"""


_DEVICE_MARKERS = re.compile(r"手机|平板|电脑|显示器|屏幕|笔记本电脑|监控画面|网页|短信|聊天记录")
_EXPLICIT_SCREEN_MARKERS = re.compile(
    r"(?:屏幕|手机|平板|电脑|显示器|网页|短信|消息|聊天记录|监控画面)"
    r"(?:上|里|中|内容)?(?:清楚)?(?:显示|写着|出现|弹出|呈现|是|为|内容是)"
)


def _fallback_device_shot(scenes: list[dict[str, Any]]) -> tuple[str, str, str]:
    """Conservative local fallback; Agent 1 remains the semantic authority."""
    text = "".join(str(scene.get("text_content") or "") for scene in scenes).strip()
    if not _DEVICE_MARKERS.search(text):
        return "none", "", ""
    device_type = next(
        (name for name in ("手机", "平板", "电脑显示器", "电脑", "显示器") if name in text),
        "设备",
    )
    quoted = re.search(
        r"(?:短信|消息|聊天记录)(?:内容)?(?:是|为|写着|显示为|[:：]).{0,12}?"
        r"[“「『‘\"]([^”」』’\"]{1,120})[”」』’\"]",
        text,
    )
    explicit = _EXPLICIT_SCREEN_MARKERS.search(text)
    if explicit or quoted:
        content = quoted.group(1).strip() if quoted else text[explicit.start():].strip(" ，。；")[:180]
        return "screen_insert", device_type, content
    return "device_interaction", device_type, ""


def _fallback_semantic_units(scenes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Deterministic coverage used when a custom/failed Agent 1 omits units."""
    if not scenes:
        return []
    units: list[dict[str, str]] = []
    # Prefer complete sentences as boundaries, but avoid producing hundreds of
    # tiny units when subtitle recognition has already split a sentence.
    start = 0
    for index, scene in enumerate(scenes, 1):
        text = str(scene.get("text_content") or "").strip()
        is_sentence_end = bool(text and text[-1:] in "。！？!?；;")
        if (is_sentence_end and index - start >= 2) or index - start >= 5:
            first = scenes[start]
            unit_scenes = scenes[start:index]
            device_mode, device_type, screen_content = _fallback_device_shot(unit_scenes)
            units.append({
                "unit_id": f"unit_{len(units) + 1:02d}",
                "start_slide_id": str(first.get("slide_id") or ""),
                "end_slide_id": str(scene.get("slide_id") or ""),
                "purpose": "连续叙事单元",
                "visual_focus": "依据原文保持同一事件完整呈现",
                "visual_mode": "literal_scene",
                "setting_hint": "依据原文当前地点",
                "novelty_anchor": "当前单元新增的动作或信息",
                "visual_pacing": "normal",
                "boundary_after": "hard",
                "device_shot_mode": device_mode,
                "device_type": device_type,
                "screen_content": screen_content,
            })
            start = index
    if start < len(scenes):
        unit_scenes = scenes[start:]
        device_mode, device_type, screen_content = _fallback_device_shot(unit_scenes)
        units.append({
            "unit_id": f"unit_{len(units) + 1:02d}",
            "start_slide_id": str(scenes[start].get("slide_id") or ""),
            "end_slide_id": str(scenes[-1].get("slide_id") or ""),
            "purpose": "连续叙事单元",
            "visual_focus": "依据原文保持同一事件完整呈现",
            "visual_mode": "literal_scene",
            "setting_hint": "依据原文当前地点",
            "novelty_anchor": "当前单元新增的动作或信息",
            "visual_pacing": "normal",
            "boundary_after": "hard",
            "device_shot_mode": device_mode,
            "device_type": device_type,
            "screen_content": screen_content,
        })
    return units


def _normalize_semantic_units(raw_units: Any, scenes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Accept only a complete, ordered, non-overlapping semantic partition."""
    if not isinstance(raw_units, list) or not scenes:
        return []
    ordered_ids = [str(scene.get("slide_id") or "") for scene in scenes]
    positions = {slide_id: index for index, slide_id in enumerate(ordered_ids) if slide_id}
    expected_start = 0
    normalized: list[dict[str, str]] = []
    for index, unit in enumerate(raw_units[:96], 1):
        if not isinstance(unit, dict):
            return []
        start_id = str(unit.get("start_slide_id") or "").strip()
        end_id = str(unit.get("end_slide_id") or "").strip()
        if start_id not in positions or end_id not in positions:
            return []
        start, end = positions[start_id], positions[end_id]
        if start != expected_start or end < start:
            return []
        pacing = str(unit.get("visual_pacing") or "normal").strip().lower()
        boundary_after = str(unit.get("boundary_after") or "hard").strip().lower()
        visual_mode = str(unit.get("visual_mode") or "literal_scene").strip().lower()
        if visual_mode not in {"literal_scene", "illustrative_broll", "symbolic"}:
            visual_mode = "literal_scene"
        device_mode = str(unit.get("device_shot_mode") or "none").strip().lower()
        if device_mode not in {"none", "device_interaction", "screen_insert"}:
            device_mode = "none"
        screen_content = str(unit.get("screen_content") or "").strip()[:300]
        if device_mode != "screen_insert":
            screen_content = ""
        normalized.append({
            "unit_id": str(unit.get("unit_id") or f"unit_{index:02d}").strip(),
            "start_slide_id": start_id,
            "end_slide_id": end_id,
            "purpose": str(unit.get("purpose") or "连续叙事单元").strip()[:160],
            "visual_focus": str(unit.get("visual_focus") or "依据原文呈现").strip()[:240],
            "visual_mode": visual_mode,
            "setting_hint": str(unit.get("setting_hint") or "依据原文当前地点").strip()[:120],
            "novelty_anchor": str(unit.get("novelty_anchor") or "当前单元新增的视觉信息").strip()[:160],
            "visual_pacing": pacing if pacing in {"hold", "normal", "fast"} else "normal",
            "boundary_after": boundary_after if boundary_after in {"hard", "soft"} else "hard",
            "character_ids": _string_list(unit.get("character_ids"), limit=10),
            "device_shot_mode": device_mode,
            "device_type": str(unit.get("device_type") or "").strip()[:40] if device_mode != "none" else "",
            "screen_content": screen_content,
        })
        expected_start = end + 1
    return normalized if normalized and expected_start == len(ordered_ids) else []


AGENT1B_BOUNDARY_REFINER_PROMPT = """你是视频流水线的 Agent 1B：语义边界副导演。
Agent 1 已经完成全文规划，但系统检测到少数 semantic_unit 过长、覆盖字幕过多，或跨越了作者原文的段落结构。
你只细化当前父单元的内部边界，不能改变字幕文字、时间戳、slide_id 顺序、人物身份、事实或父单元覆盖范围。

只输出严格 JSON 对象：{"semantic_units":[...],"decision_reason":"..."}，不要 Markdown，不要解释。
semantic_units 每项必须包含 unit_id、start_slide_id、end_slide_id、purpose、visual_focus、visual_mode、setting_hint、novelty_anchor、visual_pacing、boundary_after、character_ids、device_shot_mode、device_type、screen_content。
所有子单元必须首尾相接、完整覆盖父单元且不遗漏、不重叠。一个子单元应对应一个可独立理解的画面事件或完整论述步骤，而不是为了凑时长机械切句。
通常以 6～14 秒为宜；结论收束、话题转折、因果转向、人物/地点/时间/视觉主体改变时应新起单元。作者段落边界 source_boundary_after=paragraph 是强提示，通常不应跨越；但两个极短且明显属于同一思想的相邻段落可以保留在同一单元。
禁止让一个子单元以前半句话结束，或以下半句、承接词孤立开始。若父单元确实是不可分割的同一连续事件，可以原样返回一个单元，并在 decision_reason 说明原因。
设备画面的 screen_insert、device_interaction、none 必须按输入字幕的实际内容重新限定，不能让屏幕内容跨到已经转入其他话题的子单元。"""


def _semantic_unit_scene_range(
    unit: dict[str, Any],
    scenes: list[dict[str, Any]],
) -> tuple[int, int] | None:
    positions = {
        str(scene.get("slide_id") or ""): index
        for index, scene in enumerate(scenes)
        if str(scene.get("slide_id") or "")
    }
    start = positions.get(str(unit.get("start_slide_id") or ""))
    end = positions.get(str(unit.get("end_slide_id") or ""))
    if start is None or end is None or end < start:
        return None
    return start, end


def semantic_unit_refinement_risks(
    unit: dict[str, Any],
    scenes: list[dict[str, Any]],
) -> list[str]:
    """Return local structural reasons for a conditional Agent 1B pass."""
    scene_range = _semantic_unit_scene_range(unit, scenes)
    if scene_range is None:
        return ["invalid_range"]
    start, end = scene_range
    selected = scenes[start:end + 1]
    if len(selected) < 2:
        return []
    try:
        max_seconds = max(12.0, float(os.getenv("AGENT1B_REFINEMENT_MAX_SECONDS", "24")))
    except ValueError:
        max_seconds = 24.0
    try:
        max_slides = max(4, int(os.getenv("AGENT1B_REFINEMENT_MAX_SLIDES", "8")))
    except ValueError:
        max_slides = 8
    duration = max(
        0.0,
        float(selected[-1].get("end") or 0) - float(selected[0].get("start") or 0),
    )
    reasons = []
    if duration > max_seconds:
        reasons.append(f"duration>{max_seconds:g}s")
    if len(selected) > max_slides:
        reasons.append(f"slides>{max_slides}")
    internal_paragraphs = sum(
        1
        for scene in selected[:-1]
        if str(scene.get("source_boundary_after") or "").lower() == "paragraph"
    )
    # Paragraph structure alone must not add an extra model call to an ordinary
    # short script. It becomes a risk only inside an already broad unit.
    if internal_paragraphs and (duration > 16.0 or len(selected) > 5):
        reasons.append(f"paragraph_boundaries={internal_paragraphs}")
    return reasons


def _candidate_refinement_is_safe(
    candidate: list[dict[str, Any]],
    parent_scenes: list[dict[str, Any]],
) -> tuple[bool, str]:
    if not candidate:
        return False, "empty"
    expected_ids = [str(scene.get("slide_id") or "") for scene in parent_scenes]
    covered_ids = []
    positions = {slide_id: index for index, slide_id in enumerate(expected_ids)}
    for unit in candidate:
        start = positions.get(str(unit.get("start_slide_id") or ""), -1)
        end = positions.get(str(unit.get("end_slide_id") or ""), -1)
        if start < 0 or end < start:
            return False, "invalid_range"
        covered_ids.extend(expected_ids[start:end + 1])
        duration = (
            float(parent_scenes[end].get("end") or 0)
            - float(parent_scenes[start].get("start") or 0)
        )
        if len(candidate) > 1 and duration < 2.0:
            return False, "subunit_too_short"
    if covered_ids != expected_ids:
        return False, "coverage_mismatch"
    return True, "ok"


def refine_risky_semantic_units(
    units: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    story_context: dict[str, Any],
    content_mode: str,
    *,
    require_ai_success: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Refine only risky units; safe units retain Agent 1 output byte-for-byte."""
    diagnostics: dict[str, Any] = {
        "version": 1,
        "triggered_units": [],
        "accepted_units": [],
        "unchanged_units": [],
        "failed_units": [],
    }
    refined: list[dict[str, Any]] = []
    for unit in units:
        reasons = semantic_unit_refinement_risks(unit, scenes)
        if not reasons:
            refined.append(dict(unit))
            continue
        parent_range = _semantic_unit_scene_range(unit, scenes)
        if parent_range is None:
            refined.append(dict(unit))
            diagnostics["failed_units"].append({"unit_id": unit.get("unit_id"), "reason": "invalid_range"})
            continue
        start, end = parent_range
        parent_scenes = scenes[start:end + 1]
        unit_id = str(unit.get("unit_id") or f"unit_{start + 1:03d}")
        diagnostics["triggered_units"].append({"unit_id": unit_id, "reasons": reasons})
        payload_scenes = [
            {
                "slide_id": str(scene.get("slide_id") or ""),
                "start": round(float(scene.get("start") or 0), 3),
                "end": round(float(scene.get("end") or 0), 3),
                "text": str(scene.get("text_content") or ""),
                "source_paragraph_id": str(scene.get("source_paragraph_id") or ""),
                "source_boundary_after": str(scene.get("source_boundary_after") or "none"),
            }
            for scene in parent_scenes
        ]
        payload = {
            "content_mode": normalize_content_mode(content_mode),
            "agent0_context": story_context_for_prompt(story_context),
            "parent_semantic_unit": unit,
            "previous_context": (
                {"slide_id": scenes[start - 1].get("slide_id"), "text": scenes[start - 1].get("text_content")}
                if start > 0 else None
            ),
            "current_timeline": payload_scenes,
            "next_context": (
                {"slide_id": scenes[end + 1].get("slide_id"), "text": scenes[end + 1].get("text_content")}
                if end + 1 < len(scenes) else None
            ),
            "risk_reasons": reasons,
        }
        try:
            response = generate_gemini_text(
                system_prompt=AGENT1B_BOUNDARY_REFINER_PROMPT + "\n\n" + DEVICE_SHOT_CONTRACT,
                user_prompt=json.dumps(payload, ensure_ascii=False),
                temperature=0.08,
                response_mime_type="application/json",
                max_output_tokens=6144,
            )
            parsed = parse_json_response(response)
            raw_units = parsed.get("semantic_units") if isinstance(parsed, dict) else None
            candidate = _normalize_semantic_units(raw_units, parent_scenes)
            safe, reason = _candidate_refinement_is_safe(candidate, parent_scenes)
            if not safe:
                raise ValueError(f"边界验收失败: {reason}")
            parent_positions = {
                str(scene.get("slide_id") or ""): scene for scene in parent_scenes
            }
            for child in candidate[:-1]:
                boundary_scene = parent_positions.get(str(child.get("end_slide_id") or ""), {})
                if str(boundary_scene.get("source_boundary_after") or "").lower() == "paragraph":
                    # A deputy-created split that agrees with an author paragraph
                    # is semantic evidence, not an optional pacing suggestion.
                    # Never let the minimum-dwell merger cross it again.
                    child["boundary_after"] = "hard"
            candidate[-1]["boundary_after"] = str(unit.get("boundary_after") or "hard")
            if len(candidate) == 1:
                refined.append(dict(unit))
                diagnostics["unchanged_units"].append(unit_id)
            else:
                refined.extend(candidate)
                diagnostics["accepted_units"].append({
                    "unit_id": unit_id,
                    "subunit_count": len(candidate),
                })
        except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
            diagnostics["failed_units"].append({"unit_id": unit_id, "reason": str(exc)[:300]})
            if require_ai_success:
                raise _planning_failure("Agent 1B", exc) from exc
            refined.append(dict(unit))

    # Preserve Agent 1 output exactly when the deputy was unnecessary or merely
    # confirmed that the broad unit is indivisible. Renumber only after an
    # accepted split, where newly returned child IDs may collide across parents.
    if diagnostics["accepted_units"]:
        for index, unit in enumerate(refined, 1):
            unit["unit_id"] = f"unit_{index:03d}"
    diagnostics["status"] = (
        "not_needed" if not diagnostics["triggered_units"]
        else "refined" if diagnostics["accepted_units"]
        else "confirmed_or_fallback"
    )
    return refined, diagnostics


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
                "visual_pacing": "normal",
            }
        )
    science_mode = content_mode in {CONTENT_MODE_SCIENCE, CONTENT_MODE_PURE_SCIENCE}
    hosted_science_mode = content_mode == CONTENT_MODE_SCIENCE
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
            "wardrobe_states": [],
            "signature_item": "红色围巾",
            "relationships": "负责串联知识讲解",
        }] if hosted_science_mode else []),
        "locations": [],
        "story_beats": beats,
        "semantic_units": _fallback_semantic_units(scenes),
        "clues_and_payoffs": [],
        "continuity_rules": (
            (["科普少女始终保持黑色短发和红色围巾", "术语、物体外观、数据关系与原文保持一致"]
             if hosted_science_mode else ["术语、公式、结构、物体外观和数据关系与原文保持一致"])
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
        "agent_version": STORY_AGENT_VERSION,
        "character_continuity_version": CHARACTER_CONTINUITY_VERSION,
    }


def _safe_character_id(value: Any, index: int) -> str:
    candidate = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return candidate[:48] or f"character_{index:02d}"


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:limit]


def character_registry_issues(characters: Any) -> list[str]:
    """Mechanical validation only; semantic repair remains Agent 0's job."""
    if not isinstance(characters, list):
        return ["characters 不是数组"]
    issues: list[str] = []
    id_owner: dict[str, str] = {}
    name_owner: dict[str, str] = {}
    alias_owner: dict[str, str] = {}
    for character in characters:
        if not isinstance(character, dict):
            issues.append("存在非对象角色记录")
            continue
        character_id = str(character.get("character_id") or "").strip()
        name = str(character.get("name") or "").strip()
        if not character_id or not name:
            issues.append("存在缺少 character_id 或 name 的角色")
            continue
        if character_id in id_owner and id_owner[character_id] != name:
            issues.append(f"character_id“{character_id}”同时属于“{id_owner[character_id]}”和“{name}”")
        id_owner[character_id] = name
        if name in name_owner and name_owner[name] != character_id:
            issues.append(f"name“{name}”被分配给多个角色")
        name_owner[name] = character_id
        for alias in [name, *_string_list(character.get("aliases"))]:
            previous = alias_owner.get(alias)
            if previous and previous != character_id:
                issues.append(f"独占称呼“{alias}”同时指向多个角色")
            alias_owner[alias] = character_id
    return list(dict.fromkeys(issues))


def _normalize_characters(raw_characters: Any, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize stable identity separately from time-bounded wardrobe states."""
    if not isinstance(raw_characters, list):
        return []
    valid_ids = [str(scene.get("slide_id") or "") for scene in scenes]
    positions = {slide_id: index for index, slide_id in enumerate(valid_ids) if slide_id}
    characters: list[dict[str, Any]] = []
    used_ids: dict[str, int] = {}
    for character_index, raw in enumerate(raw_characters[:10], 1):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        states: list[dict[str, str]] = []
        raw_states = raw.get("wardrobe_states")
        if isinstance(raw_states, list):
            for index, state in enumerate(raw_states[:8], 1):
                if not isinstance(state, dict):
                    continue
                start_id = str(state.get("start_slide_id") or "").strip()
                end_id = str(state.get("end_slide_id") or "").strip()
                if start_id not in positions or end_id not in positions:
                    continue
                if positions[start_id] > positions[end_id]:
                    start_id, end_id = end_id, start_id
                wardrobe = str(state.get("wardrobe") or "").strip(" ，。；")
                headwear = str(state.get("headwear") or "").strip(" ，。；")
                carried_items = str(state.get("carried_items") or "").strip(" ，。；")
                if not any((wardrobe, headwear, carried_items)):
                    continue
                states.append({
                    "state_id": str(state.get("state_id") or f"look_{index:02d}").strip(),
                    "start_slide_id": start_id,
                    "end_slide_id": end_id,
                    "wardrobe": wardrobe,
                    "headwear": headwear,
                    "carried_items": carried_items,
                })
        character_id = _safe_character_id(raw.get("character_id"), character_index)
        used_ids[character_id] = used_ids.get(character_id, 0) + 1
        if used_ids[character_id] > 1:
            character_id = f"{character_id}_{used_ids[character_id]}"
        aliases = [value for value in _string_list(raw.get("aliases")) if value != name]
        group_aliases = [
            value for value in _string_list(raw.get("group_aliases"))
            if value != name and value not in aliases
        ]
        characters.append({
            "character_id": character_id,
            "name": name,
            "aliases": aliases,
            "group_aliases": group_aliases,
            "role": str(raw.get("role") or "").strip(),
            "appearance": str(raw.get("appearance") or "").strip(" ，。；"),
            "wardrobe": str(raw.get("wardrobe") or "").strip(" ，。；"),
            "wardrobe_states": states,
            "signature_item": str(raw.get("signature_item") or "").strip(" ，。；"),
            "relationships": str(raw.get("relationships") or "").strip(" ，。；"),
        })
    return characters


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
                "visual_pacing": (
                    str(beat.get("visual_pacing") or "normal").strip().lower()
                    if str(beat.get("visual_pacing") or "normal").strip().lower() in {"hold", "normal", "fast"}
                    else "normal"
                ),
                "character_ids": _string_list(beat.get("character_ids"), limit=10),
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
    plan["characters"] = _normalize_characters(raw.get("characters"), scenes)
    plan["key_information_objects"] = _normalize_key_information_objects(
        raw.get("key_information_objects")
    )
    valid_character_ids = {
        str(character.get("character_id") or "") for character in plan["characters"]
    }
    for beat in normalized_beats:
        beat["character_ids"] = [
            value for value in beat.get("character_ids", []) if value in valid_character_ids
        ]
    plan["story_beats"] = normalized_beats
    plan["semantic_units"] = (
        _normalize_semantic_units(raw.get("semantic_units"), scenes)
        or _fallback_semantic_units(scenes)
    )
    for unit in plan["semantic_units"]:
        unit["character_ids"] = [
            value for value in unit.get("character_ids", []) if value in valid_character_ids
        ]
        if unit.get("device_shot_mode") == "screen_insert":
            unit["character_ids"] = []
    plan["source_fingerprint"] = story_fingerprint(scenes, content_mode)
    plan["content_mode"] = content_mode
    plan["agent_version"] = STORY_AGENT_VERSION
    plan["character_continuity_version"] = CHARACTER_CONTINUITY_VERSION
    return plan


TIMELINE_AGENT_SYSTEM_PROMPT = """你是视频流水线的 Agent 1：时间轴分镜导演。
Agent 0 已经完成全文理解、人物与世界观资料整理；你不需要重新总结全文、创建人物档案或改写世界观。
你的唯一任务是根据每条字幕的 slide_id、start、end、text，以及 Agent 0 资料，划分连续的具体画面事件。
只输出严格 JSON 对象：{\"story_beats\":[...],\"semantic_units\":[...]}，不要 Markdown。
    semantic_units 每项必须包含 unit_id、start_slide_id、end_slide_id、purpose、visual_focus、visual_mode、setting_hint、novelty_anchor、visual_pacing、boundary_after、character_ids、device_shot_mode、device_type、screen_content。
所有 semantic_units 必须按顺序完整覆盖全部 slide_id，不能遗漏、重叠、倒序或跳过。
输入字幕可能额外包含 source_paragraph_id 与 source_boundary_after。source_boundary_after=paragraph 表示作者原文在此处明确换段，
是话题、论证步骤或叙事阶段切换的强提示，通常应在此结束当前单元；单个换行只是软提示，不得机械地一行一画面。
无论是否换段，都不得让一个单元以前半句话结束，也不得让下一个单元从后半句、孤立宾语或承接词开始。
一个单元等于一个具体画面事件，不是章节、观点大类或整段口播：同一动作、同一环境建立镜头、同一段连续论证可保持在一起；
结论收束、话题转折、互动引导、人物/地点/时间改变、镜头关注对象改变时必须新起单元。
不要把整段观点口播机械地留在开场谈话地点。原文明确提到通勤、工作、家务、照料、医疗、住房、消费、教育、
过去经历或未来担忧时，应把对应单元规划为有来源依据的说明性 B-roll，让画面直接呈现该项现实压力。
visual_mode=literal_scene 表示主时间线正在发生；illustrative_broll 表示原文支持的经历、原因、日常状态或假设性未来画面；
symbolic 仅用于无法安全具象化的抽象结论。说明性 B-roll 不得被描述为主时间线已经发生的事实。
setting_hint 写一个具体地点，novelty_anchor 写本单元独有的动作、物件或空间信息。相邻单元仅更换人物表情、机位、景别
不算新画面；同一地点和同一核心道具不得连续出现超过两单元，除非原文确实持续描述同一不可中断动作。
不得为了多样性编造原文没有的病名、事故、人物关系或确定结果；医疗支出可表现陪诊、病房外等候或通用医疗环境，
但不能擅自增加手术、呼吸机、危重诊断。未来家务与育儿负担应明确作为设想画面，而非既成事实。
必须参考 start/end 的实际时长：通常以约 8-16 秒为宜；超过约 18 秒时，除非确实是同一个连续事件，否则应在自然语义边界拆开。
boundary_after 只能是 hard 或 soft：人物、地点、时间、事件、结论或话题明确切换时必须为 hard，后续程序绝不会跨过去合并画面；只有可有可无的补充、语气承接或画面主体不变的短过渡才可为 soft。
例如“很难靠它翻身”与其后的“省流结束/点赞引导”是两个单元，且前者 boundary_after 必须是 hard。visual_pacing 只能是 hold、normal、fast。
story_beats 仅用于概括较高层节奏，每项包含 beat_id、slide_ids、purpose、emotion、visual_focus、visual_pacing。"""

PURE_SCIENCE_TIMELINE_CONTRACT = """【纯科普模式硬约束】
- 不建立、暗示或反复调用默认主持人；原文没有人物参与时，character_ids 必须为 []。
- 以一个完整知识关系为优先边界：定义与其必要解释、反应物与产物、条件与结果、结构与功能不要因最短时长而机械拆开。
- 先识别物理、化学、生物医学、数学、天文地学、工程计算机或人文社会知识等真实题材，再决定视觉事件，不默认套用某一学科模板。
- 可把结构图、机制图、状态转换、实验演示、公式推导、地图、时间轴、统计关系、系统流程或必要术语标注作为独立视觉事件；不人为限制画面总数。"""

PURE_SCIENCE_TIMELINE_AGENT_SYSTEM_PROMPT = (
    TIMELINE_AGENT_SYSTEM_PROMPT + "\n\n" + PURE_SCIENCE_TIMELINE_CONTRACT
)


def _beats_from_semantic_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "beat_id": f"beat_{index:02d}",
            "slide_ids": [unit["start_slide_id"], unit["end_slide_id"]],
            "purpose": unit.get("purpose") or "叙事推进",
            "emotion": "依据原文情绪",
            "visual_focus": unit.get("visual_focus") or "依据原文场景",
            "visual_pacing": unit.get("visual_pacing") or "normal",
        }
        for index, unit in enumerate(units, 1)
    ]


def _create_timeline_story_plan(
    scenes: list[dict[str, Any]],
    story_context: dict[str, Any],
    content_mode: str,
    require_ai_success: bool = False,
) -> dict[str, Any]:
    """Agent 1: a narrow timed segmentation pass built on Agent 0 context."""
    fallback = _fallback_story_plan(scenes, content_mode)
    if not gemini_configured():
        if require_ai_success:
            raise _planning_failure("Agent 1", "语言模型未配置")
        plan = {**fallback, **dict(story_context)}
        plan["semantic_units"] = fallback["semantic_units"]
        plan["story_beats"] = fallback["story_beats"]
        plan["generation_source"] = "local_fallback"
        return plan
    compact_scenes = [
        {
            "slide_id": str(scene.get("slide_id") or ""),
            "start": round(float(scene.get("start") or 0), 3),
            "end": round(float(scene.get("end") or 0), 3),
            "text": str(scene.get("text_content") or ""),
            "source_paragraph_id": str(scene.get("source_paragraph_id") or ""),
            "source_boundary_after": str(scene.get("source_boundary_after") or "none"),
        }
        for scene in scenes
    ]
    custom_prompt = os.getenv("AGENT1_PROMPT_SYSTEM", "").strip()
    try:
        system_prompt = custom_prompt or (
            PURE_SCIENCE_TIMELINE_AGENT_SYSTEM_PROMPT
            if content_mode == CONTENT_MODE_PURE_SCIENCE
            else TIMELINE_AGENT_SYSTEM_PROMPT
        )
        if custom_prompt and "semantic_units" not in custom_prompt:
            system_prompt += "\n\n" + TIMELINE_AGENT_SYSTEM_PROMPT
        system_prompt += (
            "\n\n【角色 ID 约束】Agent 0 的 characters 已为每个人建立唯一 character_id。"
            "每个 story_beats 和 semantic_units 必须额外输出 character_ids 数组，"
            "只列出该段画面中实际出现的人物；环境、道具或仅被提及但未出镜的人物不要列入。"
            "只能使用 Agent 0 已存在的 character_id，禁止用职业、群体称呼或人名代替 ID。"
        )
        system_prompt += "\n\n" + DEVICE_SHOT_CONTRACT
        if content_mode == CONTENT_MODE_PURE_SCIENCE and PURE_SCIENCE_TIMELINE_CONTRACT not in system_prompt:
            system_prompt += "\n\n" + PURE_SCIENCE_TIMELINE_CONTRACT
        response = generate_gemini_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps({
                "agent0_context": story_context_for_prompt(story_context),
                "subtitle_timeline": compact_scenes,
            }, ensure_ascii=False),
            temperature=0.12,
            response_mime_type="application/json",
            max_output_tokens=8192,
        )
        raw = parse_json_response(response)
        if not isinstance(raw, dict):
            raise ValueError("Agent 1 response is not an object")
        units = _normalize_semantic_units(raw.get("semantic_units"), scenes)
        if not units:
            # A model can produce useful story beats while missing one exact
            # timeline boundary. The deterministic partition is authoritative
            # for coverage; do not discard the whole video run.
            units = fallback["semantic_units"]
            boundary_refinement = {
                "fallback_reason": "Agent 1 semantic_units incomplete"
            }
        else:
            units, boundary_refinement = refine_risky_semantic_units(
                units,
                scenes,
                story_context,
                content_mode,
                require_ai_success=require_ai_success,
            )
        combined = dict(story_context)
        combined["semantic_units"] = units
        # story_beats remain the higher-level rhythm map produced by Agent 1;
        # semantic_units are the authoritative picture boundaries. Keeping the
        # original beats also preserves pacing labels for every covered slide.
        # Keep model beats when they cover valid slides; malformed or empty
        # beats must not invalidate an otherwise usable timeline fallback.
        raw_beats = raw.get("story_beats")
        valid_slide_ids = {str(scene.get("slide_id") or "") for scene in scenes}
        has_valid_beats = isinstance(raw_beats, list) and any(
            isinstance(beat, dict)
            and any(str(slide_id) in valid_slide_ids for slide_id in beat.get("slide_ids", []))
            for beat in raw_beats
        )
        combined["story_beats"] = (
            raw_beats if has_valid_beats else _beats_from_semantic_units(units)
        )
        plan = _normalize_story_plan(combined, scenes, content_mode)
        if plan is None:
            raise ValueError("Agent 1 plan normalization failed")
        plan["generation_source"] = "gemini"
        plan["agent0_source_fingerprint"] = story_context.get("source_fingerprint")
        plan["boundary_refinement"] = boundary_refinement
        return plan
    except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if require_ai_success:
            raise _planning_failure("Agent 1", exc) from exc
        print(f"Agent 1 时间轴规划失败，使用本地分镜边界: {exc}", flush=True)
        plan = {**fallback, **dict(story_context)}
        plan["semantic_units"] = fallback["semantic_units"]
        plan["story_beats"] = fallback["story_beats"]
        plan["generation_source"] = "local_fallback"
        plan["agent0_source_fingerprint"] = story_context.get("source_fingerprint")
        return plan


def create_story_plan(
    scenes: list[dict[str, Any]],
    content_mode: str = CONTENT_MODE_STORY,
    *,
    story_context: dict[str, Any] | None = None,
    require_ai_success: bool = False,
) -> dict[str, Any]:
    """Run Agent 1 once, with a deterministic local fallback."""
    content_mode = normalize_content_mode(content_mode)
    if story_context is not None:
        return _create_timeline_story_plan(
            scenes, story_context, content_mode, require_ai_success=require_ai_success
        )
    science_mode = content_mode in {CONTENT_MODE_SCIENCE, CONTENT_MODE_PURE_SCIENCE}
    general_mode = content_mode == CONTENT_MODE_GENERAL
    generation_source = "local_fallback"
    # This is intentionally supplied as structured context, not as an editable
    # Agent 1 prompt.  It must also be available to the local fallback path.
    global_environment_prompt = os.getenv("GLOBAL_ENVIRONMENT_PROMPT", "").strip()
    custom_agent1_prompt = os.getenv("AGENT1_PROMPT_SYSTEM", "").strip()
    if not gemini_configured():
        if require_ai_success:
            raise _planning_failure("Agent 1", "语言模型未配置")
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
        global_character_prompt = os.getenv("GLOBAL_CHARACTER_PROMPT", "").strip()
        print(f"Agent 1：正在通读全文并建立故事上下文（{len(scenes)} 个片段）...", flush=True)
        try:
            user_prompt = json.dumps(
                {
                    "complete_story": compact_scenes,
                    "user_global_character_bible": global_character_prompt,
                    "user_world_bible": global_environment_prompt,
                },
                ensure_ascii=False,
            )
            system_prompt = custom_agent1_prompt or (
                SCIENCE_AGENT_SYSTEM_PROMPT if science_mode
                else GENERAL_AGENT_SYSTEM_PROMPT if general_mode
                else STORY_AGENT_SYSTEM_PROMPT
            )
            system_prompt += (
                "\n\n【新增硬规则】每个 story_beats 必须额外输出 visual_pacing，只能为 hold、normal 或 fast。"
                "hold 用于环境铺垫、稳定对话和需停留观察的信息；fast 用于动作、转折、地点切换或情绪骤变；其余为 normal。"
                "这只是节奏建议，程序会结合真实音频时间戳执行。"
                "\n【用户全局人物档案】输入中的 user_global_character_bible 是最高优先级。"
                "若非空，必须据此建立或修正主角固定 appearance，并把前期/后期换装、帽子或头盔拆成互不重叠的 wardrobe_states；"
                "不得把整段阶段说明塞进 appearance、wardrobe 或单一镜头。未登记角色仅依据原文建立最少的临时档案。"
                "\n【用户世界与环境设定】user_world_bible 若非空，必须作为地点、时代、天气、空间、常驻道具与环境连续性的最高优先级；"
                "将可复用内容整理到 locations、continuity_rules 和 world_bible，不得擅自替换用户指定的时代或地域。"
            )
            # Apply this structural contract even for an expert's custom
            # prompt.  The normalizer still has a local fallback for older
            # presets that do not return the new field.
            system_prompt += SEMANTIC_UNIT_RULES
            system_prompt += "\n\n" + DEVICE_SHOT_CONTRACT
            if content_mode == CONTENT_MODE_PURE_SCIENCE:
                system_prompt += "\n\n" + PURE_SCIENCE_AGENT0_CONTRACT
                system_prompt += "\n\n" + PURE_SCIENCE_TIMELINE_CONTRACT
            retry_prompt = custom_agent1_prompt or (
                SCIENCE_AGENT_COMPACT_RETRY_PROMPT if science_mode
                else GENERAL_AGENT_COMPACT_RETRY_PROMPT if general_mode
                else STORY_AGENT_COMPACT_RETRY_PROMPT
            )
            retry_prompt += (
                "\n额外字段：每个 story_beats 输出 visual_pacing（hold、normal、fast 三选一）；"
                "user_global_character_bible 为最高优先级，必须拆成固定 appearance 和明确 wardrobe_states；"
                "user_world_bible 为环境连续性的最高优先级。"
            )
            retry_prompt += SEMANTIC_UNIT_RULES
            retry_prompt += "\n\n" + DEVICE_SHOT_CONTRACT
            if content_mode == CONTENT_MODE_PURE_SCIENCE:
                retry_prompt += "\n\n" + PURE_SCIENCE_AGENT0_CONTRACT
                retry_prompt += "\n\n" + PURE_SCIENCE_TIMELINE_CONTRACT
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
            if require_ai_success:
                raise _planning_failure("Agent 1", exc) from exc
            print(f"Agent 1 规划失败，使用本地故事上下文: {exc}", flush=True)
            plan = None
        if plan is None:
            if require_ai_success:
                raise _planning_failure("Agent 1", "模型返回内容无法形成有效的时间轴规划")
            plan = _fallback_story_plan(scenes, content_mode)

    plan["source_fingerprint"] = story_fingerprint(scenes, content_mode)
    if global_environment_prompt:
        plan["world_bible"] = global_environment_prompt
    plan["content_mode"] = content_mode
    plan["generation_source"] = generation_source
    plan["agent_version"] = STORY_AGENT_VERSION
    plan["character_continuity_version"] = CHARACTER_CONTINUITY_VERSION
    return plan


def load_or_create_story_plan(
    scenes: list[dict[str, Any]],
    *,
    resume: bool = False,
    path: Path = STORY_PLAN_PATH,
    allow_source_mismatch: bool = False,
    content_mode: str = CONTENT_MODE_STORY,
    story_context: dict[str, Any] | None = None,
    require_ai_success: bool = False,
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
        plan_is_current = (
            isinstance(existing, dict)
            and int(existing.get("agent_version") or 0) >= STORY_AGENT_VERSION
            and int(existing.get("character_continuity_version") or 0) >= CHARACTER_CONTINUITY_VERSION
        )
        plan_is_ai = isinstance(existing, dict) and existing.get("generation_source") == "gemini"
        may_reuse_fallback = not gemini_configured()
        context_matches = story_context is None or (
            isinstance(existing, dict)
            and existing.get("agent0_source_fingerprint") == story_context.get("source_fingerprint")
        )
        if source_matches and context_matches and plan_is_current and (
            plan_is_ai or (may_reuse_fallback and not require_ai_success)
        ):
            reason = "断点续跑" if resume else "上下文未变化"
            print(f"Agent 1：{reason}，复用故事规划: {path}", flush=True)
            return existing
        if source_matches and gemini_configured():
            print("Agent 1：发现旧版或本地降级规划，Gemini 已配置，将重新生成。", flush=True)

    if story_context is not None or require_ai_success:
        plan = create_story_plan(
            scenes,
            content_mode,
            story_context=story_context,
            require_ai_success=require_ai_success,
        )
    else:
        # Preserve the public/default call shape for integrations that wrap the
        # legacy non-strict helper.
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
                if key == "wardrobe_states" and isinstance(value, list) and value:
                    current[key] = value
                    continue
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
    merged["semantic_units"] = list(segment_plan.get("semantic_units") or _fallback_semantic_units(scenes))
    merged["key_information_objects"] = list(global_plan.get("key_information_objects") or [])
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
    merged["agent_version"] = STORY_AGENT_VERSION
    merged["character_continuity_version"] = CHARACTER_CONTINUITY_VERSION
    return merged


def create_segment_story_plan(
    scenes: list[dict[str, Any]],
    global_plan: dict[str, Any],
    content_mode: str = CONTENT_MODE_STORY,
) -> dict[str, Any]:
    """Run Agent 1B for one long-text segment, constrained by the global plan."""
    content_mode = normalize_content_mode(content_mode)
    # New two-agent pipeline: Agent 0 data is already embedded in the global
    # plan, so the local pass only needs the same narrow time-axis duty.
    if global_plan.get("agent0_source_fingerprint"):
        result = _create_timeline_story_plan(scenes, global_plan, content_mode)
        result["source_fingerprint"] = story_fingerprint(scenes, content_mode)
        result["global_source_fingerprint"] = global_plan.get("source_fingerprint")
        result["planning_scope"] = "hierarchical_segment"
        return result
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
            segment_system_prompt = (
                SCIENCE_SEGMENT_AGENT_SYSTEM_PROMPT
                if content_mode in {CONTENT_MODE_SCIENCE, CONTENT_MODE_PURE_SCIENCE}
                else GENERAL_SEGMENT_AGENT_SYSTEM_PROMPT
                if content_mode == CONTENT_MODE_GENERAL
                else SEGMENT_AGENT_SYSTEM_PROMPT
            ) + (
                "\n每个 story_beats 必须输出 visual_pacing（hold、normal、fast 三选一）；"
                "它只表示叙事节奏，后续程序会以真实音频时间戳执行切图。"
            )
            segment_system_prompt += "\n\n" + DEVICE_SHOT_CONTRACT
            if content_mode == CONTENT_MODE_PURE_SCIENCE:
                segment_system_prompt += "\n\n" + PURE_SCIENCE_AGENT0_CONTRACT
                segment_system_prompt += "\n\n" + PURE_SCIENCE_TIMELINE_CONTRACT
            response = generate_gemini_text(
                system_prompt=segment_system_prompt,
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
            and int(existing.get("agent_version") or 0) >= STORY_AGENT_VERSION
            and int(existing.get("character_continuity_version") or 0) >= CHARACTER_CONTINUITY_VERSION
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
        "semantic_units",
        "key_information_objects",
        "clues_and_payoffs",
        "continuity_rules",
        "visual_safety",
        "world_bible",
    )
    return {key: plan.get(key) for key in keys if key in plan}
