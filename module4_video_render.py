"""Module 4: turn the semantic timeline into an image-backed HTML presentation."""

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun
# AGPL-3.0 Section 7 terms: ADDITIONAL_TERMS.md

from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import threading
import time
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from backend.app.config import load_project_env
from backend.app.gemini_client import GeminiError, gemini_configured, generate_gemini_text, parse_json_response
from story_agents import (
    STORY_PLAN_PATH,
    load_or_create_story_plan,
    story_context_for_prompt,
    story_fingerprint,
)


PROJECT_ROOT = Path(__file__).resolve().parent
VISUAL_DIR = PROJECT_ROOT / "workspace" / "3_visual_template"
ASSETS_DIR = VISUAL_DIR / "assets"
TIMELINE_PATH = VISUAL_DIR / "fine_grained_timeline.json"
POSTER_MAPPING_PATH = VISUAL_DIR / "poster_mapping.json"
VISUAL_PROMPT_PLAN_PATH = VISUAL_DIR / "visual_prompt_plan.json"
DEFAULT_RUNNINGHUB_BASE_URL = "https://www.runninghub.ai"
_QUEUE_RETRY_LOCK = threading.Lock()
_REFERENCE_UPLOAD_LOCK = threading.Lock()
_CLOUD_TOKEN_REFRESH_LOCK = threading.Lock()
_REFERENCE_IMAGE_URLS: dict[tuple[str, str], str] = {}
# v13: screen/file contents are scoped again to each fixed image group. A long
# Agent 1 unit can no longer stamp the same evidence insert across every child
# poster after Python splits that unit by duration.
VISUAL_PROMPT_AGENT_VERSION = 16

REFERENCE_IMAGE_LABELS = ("图1", "图2", "图3", "图4")


AGENT2_DEVICE_SHOT_CONTRACT = """【设备画面三态硬约束（适用于所有模式）】
- Agent 1 的 semantic_units 会提供 device_shot_mode、device_type、screen_content，必须以这些结构化字段为准。
- screen_insert：只设计设备正面屏幕或显示内容的插入特写，屏幕占据主体；不得出现人物脸部、人物肖像、半身或反应特写，只允许必要的手指、设备边框或桌面边缘。character_ids 和 reference_image_ids 必须为 []。screen_content 只能使用 Agent 1 给出的原文内容，不得补写聊天、照片、网页、文件或界面信息。
- device_interaction：表现人物正在查看、拿取、操作或接听设备；屏幕背向镜头、虚化或不可读，不得编造任何屏幕文字、照片、网页、文件或界面信息。人物动作和环境是唯一视觉重点。
- none：按普通镜头处理，不因背景里存在设备而突出屏幕。
- 禁止折中成“人物脸部特写 + 可读设备屏幕”两个并列主体。"""

DEVICE_CREATIVE_GUIDANCE = (
    "- 手机、平板、电脑显示器等设备出现时，先区分“设备交互”和“明确屏幕内容”："
    "仅有查看、拿取、操作或接听动作，且原文没有给出具体内容时，只表现人物使用设备，屏幕背向镜头、虚化或不可读，不得编造界面；"
    "只有原文明示具体文字、照片、监控、网页或文件内容，而且它是本镜头重点时，才改用设备正面屏幕插入特写，屏幕占据主体，不并列人物脸部特写。"
)


def backup_poster_mapping(path: Path = POSTER_MAPPING_PATH) -> Path | None:
    """Back up an existing poster mapping before it is overwritten."""
    if not path.is_file():
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}.backup.{timestamp}{path.suffix}")
    suffix = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{path.stem}.backup.{timestamp}.{suffix}{path.suffix}")
        suffix += 1
    backup_path.write_bytes(path.read_bytes())
    print(f"已备份旧画面规划: {backup_path}", flush=True)
    return backup_path

DEFAULT_VISUAL_STYLE = (
    "伊藤润二式惊悚漫画与都市悬疑条漫风；冷青灰和墨黑为主色，暗红少量点缀，"
    "高反差电影光影、深阴影、薄雾与局部轮廓光，营造诡异、压迫、悬念渐进的氛围。"
    "人物比例写实、表情克制；人物身份与服装由单独的全局人物设定和镜头造型规则控制。"
    "画面适合横版故事视频，避免可爱Q版、明亮科普插画、PPT信息图、夸张血腥和无意义怪物堆砌。"
)
SCIENCE_VISUAL_STYLE = (
    "科教手绘漫画风的科普小漫画，理性、清晰；人物设定由单独的全局人物设定控制。"
    "画面可信、亲切、信息层级明确，适合口播视频背景。"
    "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
)
PURE_SCIENCE_VISUAL_STYLE = (
    "跨学科严肃科普与现代教材级知识可视化，准确、克制、清晰；依据题材选用结构图、受力图、"
    "实验装置、函数图像、时间轴、地图、剖面图、流程箭头、尺度对比或必要的三维示意。"
    "允许忠于原文的术语、化学式、公式、坐标、年代、地名、结构标签和少量解释文字，"
    "信息层级明确、标注可读，不设置固定主持人物。"
    "避免低幼卡通、娱乐化表情、无依据的数据、伪公式、乱码和与知识点无关的装饰。"
)
GENERAL_VISUAL_STYLE = (
    "通用横版叙事画面：请在此填写你希望的画风、色彩、质感、时代背景和镜头气质。"
    "未填写时，采用清晰、电影感、主体明确的叙事插画表现；避免乱码、水印、二维码和密集文字。"
)
CONTENT_MODE_STORY = "urban_suspense"
CONTENT_MODE_SCIENCE = "science_explainer"
CONTENT_MODE_PURE_SCIENCE = "pure_science"
CONTENT_MODE_GENERAL = "general"
DEFAULT_GLOBAL_CHARACTER_PROMPT = (
    "主角：35岁憔悴中年女性，黑色长发；前期戴红色鸭舌帽、穿灰色旧衣服；"
    "后期骑行一段时间、购入装备后，精神焕发，穿白色骑行服并佩戴白色骑行头盔。"
)
SCIENCE_GLOBAL_CHARACTER_PROMPT = "固定讲解主角：黑色短发、红色围巾的可爱少女；简洁科教风服装，出场时造型保持一致。"


def normalize_content_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode == CONTENT_MODE_SCIENCE:
        return CONTENT_MODE_SCIENCE
    if mode == CONTENT_MODE_PURE_SCIENCE:
        return CONTENT_MODE_PURE_SCIENCE
    if mode == CONTENT_MODE_GENERAL:
        return CONTENT_MODE_GENERAL
    return CONTENT_MODE_STORY


def _reference_image_catalog() -> dict[str, str]:
    raw_value = os.getenv("USER_REFERENCE_IMAGE_PATHS_JSON", "").strip()
    try:
        values = json.loads(raw_value) if raw_value else []
    except json.JSONDecodeError:
        values = []
    if not isinstance(values, list):
        values = []
    paths = [str(value).strip() for value in values if str(value).strip()][:3]
    if not paths:
        legacy_path = os.getenv("USER_PROTAGONIST_REFERENCE_IMAGE_PATH", "").strip()
        paths = [legacy_path] if legacy_path else []
    return {REFERENCE_IMAGE_LABELS[index]: path for index, path in enumerate(paths)}


def _reference_image_instruction() -> str:
    catalog = _reference_image_catalog()
    if not catalog:
        return "本次未上传角色形象参考图；reference_image_ids 必须输出 []。"
    labels = "、".join(catalog)
    return (
        f"本次可用角色形象参考图为：{labels}，按上传顺序对应 Image2 的第 1 至第 {len(catalog)} 张图。"
        "当镜头实际出现对应角色时，reference_image_ids 只能填写所需的图号；"
        "并且 image_prompt 必须紧跟角色名称明确写出“角色形象参考图N”。"
        "没有使用参考图的镜头必须输出 []，且 image_prompt 不得假装引用参考图。"
    )


def _strip_dynamic_reference_image_instructions(prompt: str) -> str:
    """Remove saved task-state lines before appending the current reference catalog."""
    kept: list[str] = []
    for line in str(prompt or "").splitlines():
        stripped = line.strip()
        if stripped == "【角色图像参考约束】":
            continue
        if "reference_image_ids" in stripped and any(
            marker in stripped
            for marker in ("本次未上传", "本次可用角色形象参考图", "本次已上传角色形象参考图")
        ):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _character_reference_label(name: str) -> str:
    """Resolve common user-authored character-to-image bindings."""
    character_name = str(name or "").strip()
    if not character_name:
        return ""
    character_bible = os.getenv("GLOBAL_CHARACTER_PROMPT", "")
    # Users naturally write this relationship in several equivalent forms:
    # ``林晚：图1`` / ``林晚参考图1`` / ``林晚形象参考图1`` /
    # ``林晚角色形象参考图1``.  Keep the parser permissive here while still
    # requiring the character name and an explicit image number.
    match = re.search(
        re.escape(character_name)
        + r"\s*[：:,，]?\s*(?:(?:角色)?形象参考|角色参考|参考)?\s*图\s*([1-4])",
        character_bible,
    )
    if not match:
        return ""
    label = f"图{match.group(1)}"
    return label if label in _reference_image_catalog() else ""


def build_visual_prompt_system(
    style: str = "",
    content_mode: str = CONTENT_MODE_STORY,
    global_character_prompt: str = "",
) -> str:
    content_mode = normalize_content_mode(content_mode)
    visual_style = style.strip() or {
        CONTENT_MODE_SCIENCE: SCIENCE_VISUAL_STYLE,
        CONTENT_MODE_PURE_SCIENCE: PURE_SCIENCE_VISUAL_STYLE,
        CONTENT_MODE_GENERAL: GENERAL_VISUAL_STYLE,
    }.get(content_mode, DEFAULT_VISUAL_STYLE)
    character_reference = global_character_prompt.strip() or {
        CONTENT_MODE_STORY: DEFAULT_GLOBAL_CHARACTER_PROMPT,
        CONTENT_MODE_SCIENCE: SCIENCE_GLOBAL_CHARACTER_PROMPT,
    }.get(content_mode, "未填写；只能依据原文建立必要的临时角色档案。")
    if content_mode == CONTENT_MODE_PURE_SCIENCE:
        return f"""你是跨学科严肃科普、知识教育与教材级可视化视频的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）、image_prompt（中文生图提示词）和 reference_image_ids（参考图编号数组）。
- 严格使用系统给出的固定 slide 分组；每组生成一张 2:1 横版科学画面，完整覆盖全部 slide_id，不遗漏、重复、合并或人为限制海报数量。
- 纯科普默认没有固定主持人物；没有用户参考角色时，character_ids 和 reference_image_ids 必须输出 []。

【跨学科分镜规则】
- 先识别本组所属学科及它是在提出问题、定义概念、解释结构、展示机制、比较状态、给出证据还是总结结论，再选择该学科最合适的视觉语言，禁止默认套用生物学或微观细胞画面。
- 生物与医学可用结构、剖面和生理过程；物理可用受力图、场线、光路和实验；化学可用结构式、反应过程和装置；数学可用几何、函数图像、坐标和推导关系；天文与地学可用尺度、轨道、地图和地层；工程与计算机可用系统结构、零件剖面、电路、数据流和算法步骤；历史、地理与社会知识可用时间轴、地图、史料物件、统计关系和情境复原。
- ATP、ADP、Pi、化学式、数学公式、结构名称、坐标、年代、地名和必要标签可以直接出现，不设置机械的 20 字上限；所有文字必须忠于原文、数量服务于讲解且清晰可读，禁止编造术语、数据和伪公式。
- 同一张图仍应围绕一个核心知识点组织信息；允许教材图、结构图、流程图或科学信息图，但避免把整段旁白塞进画面，也避免无层级的密集海报排版。
- 如原文类比存在口误、拼写误差或不严谨表达，画面优先使用正确科学结构，不把错误类比绘制成错误事实。
{DEVICE_CREATIVE_GUIDANCE}

【用户可控设定】
- 当前统一画风参考为：{visual_style}
- 用户全局人物设定为：{character_reference}
- {_reference_image_instruction()}
- 当画面中没有这些角色的时候，则本段人物设定不作为参考。
- image_prompt 只写当前知识点独有的结构、对象、过程、视角、构图、标注、光线和色彩，不重复整段通用画风。

【质量与安全】
- 概念关系、因果、方向、数量级、时间与空间关系优先于戏剧效果；没有把握的专业细节使用简化但不误导的示意表达。
- 禁止水印、二维码、品牌 logo、乱码、无意义装饰字符和与原文无关的人物。
- 系统会在每条 image_prompt 末尾统一加入干净画质要求，模型不要重复输出。"""
    if content_mode == CONTENT_MODE_SCIENCE:
        return f"""你是科普科技口播视频的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）、image_prompt（中文生图提示词）和 reference_image_ids（参考图编号数组）。
- reference_image_ids 只能使用本次已上传的图号；空镜、道具镜头、环境镜头或未出现参考角色时必须输出 []。

【分镜规则】
- 严格按照系统提供的固定 slide 分组，每组生成一张 2:1 横版解说漫画。
- 每张画面默认覆盖不超过 15 秒，不得合并、遗漏或重复 slide_id。
- 先理解该段要讲清的知识点、因果关系、案例或数据含义，再选择最直观的视觉表达。
- 优先采用生活化场景、实验演示、物体对比、过程示意和具象比喻；避免只画一个人在讲话。
- 忠于原文知识，不编造数据、实验结果、产品功能或科学结论。
{DEVICE_CREATIVE_GUIDANCE}

【用户可控设定】
- 当前统一画风参考为：{visual_style}
- 用户全局人物设定为：{character_reference}
- {_reference_image_instruction()}
- 当画面中没有这些角色的时候，则本段人物设定不作为参考。
- 上述设定只作为全局资料；image_prompt 只写本组独有的知识场景、动作、构图、光线和色彩，不重复通用风格或固定画质句。

【画面要求】
- 根据 text_content、visual_summary 和 Agent 1 的全文知识结构设计画面。
- 每张图只突出一个核心知识点，主体明确、空间干净、信息层级清楚。
- 抽象概念要转成可见的物体、动作或对比；确需图表时只保留一个简单关系，不做密集 PPT。
- 不要生成复杂公式、长段文字、密集小字、字幕、水印、二维码、logo 或乱码。
- 如出现文字，整张画面总字数必须少于 20 个中文字符。

【固定画质要求】
- 系统会在每条 image_prompt 末尾统一加入：
  避免噪点、脏污糊抹和无意义涂抹；保留所选画风需要的线稿、色块或可控绘制笔触，画面干净清晰。"""
    if content_mode == CONTENT_MODE_GENERAL:
        return f"""你是通用视频的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）、image_prompt（中文生图提示词）和 reference_image_ids（参考图编号数组）。
- reference_image_ids 只能使用本次已上传的图号；未出现参考角色时必须输出 []。
- 严格使用系统给出的固定 slide 分组；每组生成一张 2:1 横版视频画面，覆盖全部 slide_id，不遗漏、重复或合并分组。

【分镜规则】
- 先通读前后文，再为每组选择一个能清楚表达原文的具体瞬间、场景、物体或动作；每张图只有一个视觉焦点。
- 原文有角色时，首次出现必须写出具体外貌、年龄、发型、服装与标志物；再次出现直接复写已确定的特征。没有角色时可使用环境、物件、示意或空镜，不强行创建主角。
- 观点、情感和社会观察类口播不能长期停留在开场谈话场景。原文提到通勤、工作、家务、照料、医疗、住房或未来担忧时，优先使用来源明确的说明性 B-roll 直接呈现该项现实处境。
{DEVICE_CREATIVE_GUIDANCE}

【用户可控设定】
- 当前统一画风参考为：{visual_style}
- 用户全局人物设定为：{character_reference}
- {_reference_image_instruction()}
- 当画面中没有这些角色的时候，则本段人物设定不作为参考。
- 上述设定只作为全局资料；image_prompt 只写本镜头独有的主体、动作、环境、景别、机位、构图、光线和氛围，不重复整段通用画风或画质句。

【画面要求】
- 忠于原文，不擅自把普通内容改成恐怖、科普、儿童风或特定题材。
- 不要生成长文字、字幕、水印、二维码、品牌 logo、拼贴页或密集 PPT；如有文字，总量少于 20 个中文字符。
- 避免露骨血腥、裸体、自残及危险行为特写；必要时用反应、剪影、遮挡、远景或环境痕迹间接表达。
- 系统会在每条 image_prompt 末尾统一加入干净画质要求，模型不要重复输出。"""
    return f"""你是鬼故事与都市小说视频的惊悚漫画分镜导演。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）、image_prompt（中文生图提示词）和 reference_image_ids（参考图编号数组）。
- reference_image_ids 只能使用本次已上传的图号；未出现参考角色时必须输出 []。

【分镜规则】
- 严格按照系统为本次任务提供的固定 slide 分组，每组生成一张 2:1 横版电影感漫画分镜。
- 每张画面默认覆盖不超过 15 秒，不能为了减少图片数量而合并相邻分组。
- 覆盖每一个 slide_id，不得遗漏或重复。
- 通读前后文，识别人物关系、地点、时间、关键道具和悬念线索，让相邻画面具有叙事连续性。
- 每组选择一个最有戏剧张力的具体瞬间，不要把抽象观点、旁白文字或多个时间点堆在同一画面。
- 忠于原文事实：原文没有鬼怪、凶案或暴力时，不得擅自添加，只用光影、构图和人物状态制造都市悬疑感。
- 对都市情感、社会观察和观点口播，原文明确提到的通勤、工作、家务、照料、医疗、住房、过去经历或未来担忧可以使用说明性 B-roll 或假设性情境画面；这类画面用于解释旁白，不等于主时间线切换，也不属于凭空增加事件。
{DEVICE_CREATIVE_GUIDANCE}

【统一风格】
- 默认风格为：{visual_style}
- 设计具体画面时必须遵守上述统一风格，但 image_prompt 只输出本组的具体场景、动作、构图、光线和色彩。
- 不要在 image_prompt 中重复统一风格或固定画质要求；系统会在提交生图前统一注入一次。
- 首次出现的人物要提炼可识别的外貌、年龄、发型、服装和标志性物件；人物再次出现时必须在 image_prompt 中直接写出这些具体特征，禁止只写姓名、关系称呼或“同一个人”。

【用户可控设定】
- 用户全局人物设定为：{character_reference}
- {_reference_image_instruction()}
- 当画面中没有这些角色的时候，则本段人物设定不作为参考。
- 上述设定只作为全局资料；image_prompt 只写本镜头独有的主体、动作、环境、景别、机位、构图、光线和氛围，不重复整段通用画风或画质句。

【画面要求】
- 根据该组 slide 的 text_content 和 visual_summary 设计具体画面。
- image_prompt 必须写清人物特征与动作、环境、关键道具、景别、机位、构图、光线、色彩和氛围。
- 优先使用空镜、遮挡、镜面反射、门缝、走廊纵深、前景窥视感等电影语言制造悬念，但必须服务于原文情节。
- 鬼故事侧重未知感和逐步揭示；普通都市小说侧重人物冲突与情绪张力，不要强行恐怖化。
- 优先生成适合视频观看的单一视觉焦点和干净大画面，不要做成拼贴、分格页或杂乱 PPT。

【生图审核安全】
- 保留故事事实和惊悚气氛，但避免露骨血腥、喷溅血液、肢解、裸露器官、腐烂尸体特写、性暴力和自残细节。
- 必须出现敏感事件时，改用人物反应、剪影、遮挡、门外视角、远景、环境痕迹或事后氛围间接表达。
- 涉及未成年人时不得出现任何性化、裸体、虐待细节或危险行为特写。
- 不使用真实公众人物肖像，不生成品牌 logo、水印、二维码或仿新闻截图。
- 安全改写只能改变呈现方式，不能篡改人物、线索、因果关系和剧情结论。

【文字限制】
- 允许出现少量文字，但不是必须。
- 如出现文字，整张画面总字数必须少于 20 个中文字符。
- 不要生成长句、密集小字、字幕、水印、二维码、logo 或无意义乱码。

【固定画质要求】
- 系统会在每条 image_prompt 末尾统一加入以下内容，模型不要重复输出：
  避免噪点、脏污糊抹和无意义涂抹；保留所选画风需要的线稿、色块或可控绘制笔触，画面干净清晰。"""


DEFAULT_VISUAL_PROMPT_SYSTEM = build_visual_prompt_system(
    content_mode=CONTENT_MODE_STORY,
    global_character_prompt=DEFAULT_GLOBAL_CHARACTER_PROMPT,
)
SCIENCE_VISUAL_PROMPT_SYSTEM = build_visual_prompt_system(
    content_mode=CONTENT_MODE_SCIENCE,
    global_character_prompt=SCIENCE_GLOBAL_CHARACTER_PROMPT,
)
GENERAL_VISUAL_PROMPT_SYSTEM = build_visual_prompt_system(content_mode=CONTENT_MODE_GENERAL)
PURE_SCIENCE_VISUAL_PROMPT_SYSTEM = build_visual_prompt_system(content_mode=CONTENT_MODE_PURE_SCIENCE)


@dataclass(frozen=True)
class PosterTask:
    macro: dict[str, Any]
    output: Path
    task_id: str | None


class RunningHubQueueFull(RuntimeError):
    """The account has reached the cloud-side active task limit (error 421)."""


class RunningHubTransientError(RuntimeError):
    """A temporary RunningHub or network failure that can be retried safely."""


class RunningHubReferenceUploadError(RuntimeError):
    """A reference upload response is valid but contains no usable image URL."""


class RunningHubResultRetryableError(RuntimeError):
    """A cloud task completed with a transient result-file failure."""


class RunningHubModerationError(RunningHubResultRetryableError):
    """One image prompt was rejected and should be safely rewritten before resubmission."""


class RunningHubPowerInsufficient(RuntimeError):
    """The selected RunningHub account has insufficient balance or compute quota."""


class RunningHubAccessDenied(RuntimeError):
    """The selected API key cannot call the endpoint on the configured site."""


class RunningHubAllAccountsPowerInsufficient(RuntimeError):
    """Every configured RunningHub account reported insufficient balance or quota."""


class RunningHubAllAccountsAccessDenied(RuntimeError):
    """Every configured RunningHub account was denied by the selected endpoint."""


class RunningHubAllAccountsBusy(RuntimeError):
    """Every configured image account currently returned a queue or rate limit."""


class RunningHubAccountPool:
    """Capacity-aware account scheduler with round-robin and automatic backoff."""

    def __init__(self, configs: list[dict[str, str]], per_key_concurrency: int | None = None) -> None:
        self._configs = configs
        server_managed_capacity = 64 if any(config.get("cloud_pool") == "1" for config in configs) else None
        self._configured_capacity = max(
            1,
            int(
                per_key_concurrency
                or server_managed_capacity
                or _positive_env_int("RUNNINGHUB_PER_KEY_CONCURRENCY", 1)
            ),
        )
        # A quota failure is deterministic for the current backend session.
        # Seed every new batch/redraw pool with it so a newly-created task does
        # not waste another 90 retries on an account already known to be empty.
        with _ACCOUNT_STATE_LOCK:
            self._power_exhausted = {
                str(config.get("api_key") or "") for config in configs
                if str(config.get("api_key") or "") in _POWER_EXHAUSTED_ACCOUNT_KEYS
            }
        self._access_denied: set[str] = set()
        self._queue_full: set[str] = set()
        self._inflight = {str(config.get("api_key") or ""): 0 for config in configs}
        self._effective_capacity = {
            str(config.get("api_key") or ""): self._configured_capacity for config in configs
        }
        self._active_leases: dict[int, str] = {}
        self._next_lease_id = 1
        self._next_index = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def _lease_locked(self, config: dict[str, str]) -> dict[str, str]:
        key = config["api_key"]
        lease_id = self._next_lease_id
        self._next_lease_id += 1
        self._inflight[key] = self._inflight.get(key, 0) + 1
        self._active_leases[lease_id] = key
        return {**config, "_lease_id": str(lease_id)}

    def _release_locked(self, config: dict[str, str]) -> None:
        try:
            lease_id = int(config.get("_lease_id") or 0)
        except (TypeError, ValueError):
            lease_id = 0
        key = self._active_leases.pop(lease_id, "")
        if not key:
            return
        self._inflight[key] = max(0, self._inflight.get(key, 0) - 1)
        self._condition.notify_all()

    def release(self, config: dict[str, str]) -> None:
        """Release one idempotent local lease after the remote task finishes or aborts."""
        with self._condition:
            self._release_locked(config)

    def acquire(self) -> dict[str, str]:
        with self._condition:
            while True:
                usable = [
                    config
                    for config in self._configs
                    if config["api_key"] not in self._power_exhausted
                    and config["api_key"] not in self._access_denied
                ]
                available = [
                    config
                    for config in usable
                    if config["api_key"] not in self._queue_full
                    and self._inflight.get(config["api_key"], 0)
                    < self._effective_capacity.get(config["api_key"], self._configured_capacity)
                ]
                if available:
                    config = available[self._next_index % len(available)]
                    self._next_index = (self._next_index + 1) % len(available)
                    return self._lease_locked(config)
                locally_busy = [
                    config for config in usable
                    if config["api_key"] not in self._queue_full
                ]
                if locally_busy:
                    self._condition.wait(timeout=1.0)
                    continue
                usable = [
                    config
                    for config in self._configs
                    if config["api_key"] not in self._power_exhausted
                    and config["api_key"] not in self._access_denied
                ]
                if usable:
                    raise RunningHubAllAccountsBusy(
                        "所有可用图像账号当前均处于队列或并发受限状态（421/429）"
                    )
                if self._access_denied:
                    raise RunningHubAllAccountsAccessDenied(
                        "所有已配置的第三方图像账号均被当前接口或模型拒绝访问"
                    )
                raise RunningHubAllAccountsPowerInsufficient(
                    "所有已配置的第三方图像账号余额或算力均不足"
                )

    def mark_power_exhausted(self, config: dict[str, str]) -> None:
        with self._condition:
            self._power_exhausted.add(config["api_key"])
            self._queue_full.discard(config["api_key"])
            self._release_locked(config)
        with _ACCOUNT_STATE_LOCK:
            _POWER_EXHAUSTED_ACCOUNT_KEYS.add(config["api_key"])

    def mark_access_denied(self, config: dict[str, str]) -> None:
        with self._condition:
            self._access_denied.add(config["api_key"])
            self._queue_full.discard(config["api_key"])
            self._release_locked(config)

    def mark_queue_full(self, config: dict[str, str]) -> None:
        with self._condition:
            if (
                config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
            ):
                self._queue_full.add(config["api_key"])
                remaining = max(0, self._inflight.get(config["api_key"], 0) - 1)
                self._effective_capacity[config["api_key"]] = max(1, min(
                    self._effective_capacity.get(config["api_key"], self._configured_capacity),
                    max(1, remaining),
                ))
            self._release_locked(config)

    def mark_available(self, config: dict[str, str]) -> None:
        with self._condition:
            self._queue_full.discard(config["api_key"])
            current = self._effective_capacity.get(config["api_key"], 1)
            self._effective_capacity[config["api_key"]] = min(self._configured_capacity, current + 1)
            self._release_locked(config)

    def acquire_waiting_account(self) -> dict[str, str]:
        """Choose any non-414 account as the account whose queue will be observed."""
        with self._condition:
            while True:
                usable = [
                    config
                    for config in self._configs
                    if config["api_key"] not in self._power_exhausted
                    and config["api_key"] not in self._access_denied
                ]
                if not usable:
                    if self._access_denied:
                        raise RunningHubAllAccountsAccessDenied(
                            "所有已配置的第三方图像账号均被当前接口或模型拒绝访问"
                        )
                    raise RunningHubAllAccountsPowerInsufficient(
                        "所有已配置的第三方图像账号余额或算力均不足"
                    )
                available = [
                    config for config in usable
                    if self._inflight.get(config["api_key"], 0)
                    < self._effective_capacity.get(config["api_key"], self._configured_capacity)
                ]
                if available:
                    config = available[self._next_index % len(available)]
                    self._next_index = (self._next_index + 1) % len(available)
                    return self._lease_locked(config)
                self._condition.wait(timeout=1.0)


_ACCOUNT_STATE_LOCK = threading.Lock()
_POWER_EXHAUSTED_ACCOUNT_KEYS: set[str] = set()
_SHARED_ACCOUNT_POOLS: dict[tuple[str, tuple[tuple[str, ...], ...]], RunningHubAccountPool] = {}
_SHARED_ACCOUNT_POOLS_LOCK = threading.Lock()


def shared_runninghub_account_pool(
    configs: list[dict[str, str]], *, namespace: str = "default"
) -> RunningHubAccountPool:
    """Reuse one round-robin cursor across independently started image tasks.

    The main batch renderer already shares a pool inside one call. Visual-editor
    redraws arrive as separate calls/threads, so without this registry every
    redraw starts from account 1 and the extra accounts are only used after 421.
    """
    signature = tuple(
        (
            str(config.get("api_key") or ""),
            str(config.get("endpoint") or ""),
            str(config.get("ratio") or ""),
            str(config.get("resolution") or ""),
            str(_positive_env_int("RUNNINGHUB_PER_KEY_CONCURRENCY", 1)),
        )
        for config in configs
    )
    key = (str(namespace or "default"), signature)
    with _SHARED_ACCOUNT_POOLS_LOCK:
        pool = _SHARED_ACCOUNT_POOLS.get(key)
        if pool is None:
            pool = RunningHubAccountPool(configs)
            _SHARED_ACCOUNT_POOLS[key] = pool
        return pool


def _load_runninghub_env_from_file() -> None:
    """Use the current .env as the source of truth for RunningHub settings."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        load_project_env()
        return

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("RUNNINGHUB_"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    for key in list(os.environ):
        if key.startswith("RUNNINGHUB_") and key not in values:
            os.environ.pop(key, None)
    os.environ.update(values)


def _provider_configs() -> list[dict[str, str]]:
    use_cloud_pool = os.getenv("USE_CLOUD_IMAGE_POOL", "").strip().lower() in {"1", "true", "yes", "on"}
    if use_cloud_pool:
        base_url = os.getenv("CLOUD_IMAGE_POOL_BASE_URL", "").strip().rstrip("/")
        access_token = os.getenv("CLOUD_IMAGE_POOL_ACCESS_TOKEN", "").strip()
        if not base_url or not access_token:
            raise RuntimeError("云端号池运行凭据缺失，请重新登录云端账户后重试")
        return [{
            "endpoint": f"{base_url}/image-pool/generate",
            "query_url": f"{base_url}/image-pool/query",
            "upload_url": f"{base_url}/image-pool/media/upload",
            "account_url": f"{base_url}/image-pool/account-status",
            "resolution": os.getenv("RUNNINGHUB_RESOLUTION", "1k").strip(),
            "ratio": os.getenv("RUNNINGHUB_TARGET_RATIO", "2:1").strip(),
            "api_key": access_token,
            "refresh_token": os.getenv("CLOUD_IMAGE_POOL_REFRESH_TOKEN", "").strip(),
            "cloud_base_url": base_url,
            "account_label": "云端号池",
            "cloud_pool": "1",
        }]
    _load_runninghub_env_from_file()
    base_config = {
        "endpoint": os.getenv("RUNNINGHUB_ENDPOINT", "/rhart-image-g-2/text-to-image").strip(),
        "resolution": os.getenv("RUNNINGHUB_RESOLUTION", "1k").strip(),
        "ratio": os.getenv("RUNNINGHUB_TARGET_RATIO", "2:1").strip(),
    }
    raw_keys = [os.getenv("RUNNINGHUB_API_KEY", "")]
    raw_keys.extend(re.split(r"[,;\s]+", os.getenv("RUNNINGHUB_API_KEYS", "")))
    raw_keys.extend(
        value
        for name, value in sorted(os.environ.items())
        if re.fullmatch(r"RUNNINGHUB_API_KEY_?\d+", name)
    )
    api_keys: list[str] = []
    for raw_key in raw_keys:
        key = raw_key.strip()
        if key and key not in api_keys:
            api_keys.append(key)

    missing = []
    if not api_keys:
        missing.append("第三方图像 API Key")
    if not base_config["endpoint"]:
        missing.append("第三方图像接口地址")
    if missing:
        raise RuntimeError(f"模块 4 缺少配置: {', '.join(missing)}。请在 .env 中设置后重试。")
    return [
        {**base_config, "api_key": api_key, "account_label": f"账号 {index}"}
        for index, api_key in enumerate(api_keys, 1)
    ]


def _pacing_by_slide(story_plan: dict[str, Any] | None) -> dict[str, str]:
    pacing: dict[str, str] = {}
    for beat in (story_plan or {}).get("story_beats", []):
        if not isinstance(beat, dict):
            continue
        value = str(beat.get("visual_pacing") or "normal").strip().lower()
        value = value if value in {"hold", "normal", "fast"} else "normal"
        for slide_id in beat.get("slide_ids", []):
            pacing[str(slide_id)] = value
    return pacing


def _visual_groups(
    scenes: list[dict[str, Any]],
    story_plan: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    def duration_from_env(name: str, default: float, lower: float, upper: float) -> float:
        try:
            return max(lower, min(upper, float(os.getenv(name, str(default)).strip())))
        except ValueError:
            return default

    min_duration = duration_from_env("VISUAL_MIN_DURATION_SECONDS", 6.0, 3.0, 30.0)
    target_duration = duration_from_env("VISUAL_TARGET_DURATION_SECONDS", 8.0, min_duration, 45.0)
    raw_duration = os.getenv("VISUAL_MAX_DURATION_SECONDS", "12").strip()
    try:
        max_duration = max(target_duration, float(raw_duration))
    except ValueError:
        max_duration = max(target_duration, 12.0)
    max_slides = _positive_env_int("VISUAL_MAX_SLIDES_PER_IMAGE", 6)
    # Agent pacing describes relative density only.  It never overrides the
    # user-selected minimum dwell time; short trailing groups are merged below.
    pacing_limits = {
        "hold": (max_duration, max_slides),
        "normal": (target_duration, max_slides),
        "fast": (max(min_duration, min(target_duration, 6.0)), min(max_slides, 3)),
    }
    scene_ids = [str(scene.get("slide_id") or "") for scene in scenes]
    positions = {slide_id: index for index, slide_id in enumerate(scene_ids) if slide_id}

    def semantic_partitions() -> list[tuple[list[dict[str, Any]], str]]:
        units = (story_plan or {}).get("semantic_units")
        if not isinstance(units, list) or not units:
            return []
        partitions: list[tuple[list[dict[str, Any]], str]] = []
        expected = 0
        for unit in units:
            if not isinstance(unit, dict):
                return []
            start = positions.get(str(unit.get("start_slide_id") or ""), -1)
            end = positions.get(str(unit.get("end_slide_id") or ""), -1)
            if start != expected or end < start:
                return []
            boundary_after = str(unit.get("boundary_after") or "hard").strip().lower()
            partitions.append((scenes[start : end + 1], boundary_after if boundary_after in {"hard", "soft"} else "hard"))
            expected = end + 1
        return partitions if expected == len(scenes) else []

    def split_semantic_partition(partition: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Split inside one semantic event, never by crossing into the next event."""
        if not partition:
            return []
        result: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for scene in partition:
            candidate = current + [scene]
            candidate_duration = float(candidate[-1].get("end") or 0) - float(candidate[0].get("start") or 0)
            current_duration = (
                float(current[-1].get("end") or 0) - float(current[0].get("start") or 0)
                if current else 0.0
            )
            if current and (
                candidate_duration > max_duration
                or len(current) >= max_slides
                or (candidate_duration > target_duration and current_duration >= min_duration)
            ):
                result.append(current)
                current = []
            current.append(scene)
        if current:
            result.append(current)
        # A short tail may merge only with its previous frame in the same event.
        if len(result) >= 2:
            tail = result[-1]
            tail_duration = float(tail[-1].get("end") or 0) - float(tail[0].get("start") or 0)
            previous = result[-2]
            previous_duration = float(previous[-1].get("end") or 0) - float(previous[0].get("start") or 0)
            if tail_duration < min_duration and previous_duration + tail_duration <= max_duration:
                previous.extend(tail)
                result.pop()
        return result

    semantic_groups = semantic_partitions()
    if semantic_groups:
        # Agent 1 decides event membership. Python only controls picture density
        # inside an event. A hard boundary is never crossed; a soft boundary is
        # eligible for one minimum-duration merge.
        result: list[list[dict[str, Any]]] = []
        pending_soft_boundary = False
        for partition, boundary_after in semantic_groups:
            split_groups = split_semantic_partition(partition)
            if pending_soft_boundary and result and split_groups:
                previous = result[-1]
                first = split_groups[0]
                previous_duration = float(previous[-1].get("end") or 0) - float(previous[0].get("start") or 0)
                first_duration = float(first[-1].get("end") or 0) - float(first[0].get("start") or 0)
                if previous_duration < min_duration and previous_duration + first_duration <= max_duration:
                    previous.extend(first)
                    split_groups = split_groups[1:]
            result.extend(split_groups)
            pending_soft_boundary = boundary_after == "soft"
        return result

    # Safe backward-compatible grouping for old plans and custom Agent 1
    # presets that omit semantic_units.
    pacing_by_slide = _pacing_by_slide(story_plan)
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for scene in scenes:
        scene_end = float(scene.get("end") or 0)
        candidate_start = float((current[0] if current else scene).get("start") or 0)
        group_pacing = pacing_by_slide.get(str((current[0] if current else scene).get("slide_id") or ""), "normal")
        scene_pacing = pacing_by_slide.get(str(scene.get("slide_id") or ""), "normal")
        group_target, group_slides = pacing_limits[group_pacing]
        current_duration = float(current[-1].get("end") or 0) - candidate_start if current else 0.0
        candidate_duration = scene_end - candidate_start
        if current and (len(current) >= group_slides or candidate_duration > max_duration or (candidate_duration > group_target and current_duration >= min_duration) or (scene_pacing != group_pacing and current_duration >= min_duration)):
            groups.append(current)
            current = []
        current.append(scene)
    if current:
        groups.append(current)
    # Legacy/custom plans without semantic_units retain the established
    # minimum-dwell behavior.
    index = 0
    while index < len(groups):
        group = groups[index]
        group_duration = float(group[-1].get("end") or 0) - float(group[0].get("start") or 0)
        if group_duration >= min_duration or len(groups) == 1:
            index += 1
            continue
        if index > 0:
            previous = groups[index - 1]
            previous_duration = float(previous[-1].get("end") or 0) - float(previous[0].get("start") or 0)
            if previous_duration + group_duration <= max_duration or index == len(groups) - 1:
                previous.extend(group)
                groups.pop(index)
                continue
        if index + 1 < len(groups):
            groups[index + 1] = group + groups[index + 1]
            groups.pop(index)
            continue
        index += 1
    return groups


def _fallback_mapping(
    scenes: list[dict[str, Any]],
    story_plan: dict[str, Any] | None = None,
    required_groups: list[list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    groups = required_groups if required_groups is not None else _visual_groups(scenes, story_plan)
    content_mode = normalize_content_mode(os.getenv("CONTENT_MODE"))
    science_mode = content_mode == CONTENT_MODE_SCIENCE
    pure_science_mode = content_mode == CONTENT_MODE_PURE_SCIENCE
    general_mode = content_mode == CONTENT_MODE_GENERAL
    return [
        {
            "macro_scene_id": f"poster_{index:03d}",
            "includes_slides": [str(scene["slide_id"]) for scene in group],
            "reference_image_ids": [],
            "image_prompt": (
                (
                    "2:1 横版科教手绘解说漫画，单一知识焦点，使用生活化场景、物体对比或过程示意，"
                    f"具体讲清“{'；'.join(str(scene.get('visual_summary') or '') for scene in group)}”，"
                    "黑色短发、红色围巾的少女形象保持一致，知识准确、构图清楚，不做密集PPT、字幕或水印。"
                )
                if science_mode
                else (
                    "2:1 横版跨学科严肃知识可视化，单一核心知识点，依据具体学科使用准确的结构图、过程示意、实验、地图、时间轴、函数图像或系统图，"
                    f"公式或必要标签讲清“{'；'.join(str(scene.get('visual_summary') or '') for scene in group)}”，"
                    "默认不出现主持人物，允许忠于原文的专业术语、公式与必要标签，不编造数据、伪公式、乱码或水印。"
                ) if pure_science_mode
                else (
                    "2:1 横版叙事插画或漫画分镜，单一视觉焦点，人物、环境或关键物件清楚服务于原文，"
                    f"具体呈现“{'；'.join(str(scene.get('visual_summary') or '') for scene in group)}”，"
                    "镜头、光线和情绪按原文自然选择，不强加惊悚或科普风格，不要拼贴、分格、字幕、水印或边框。"
                ) if general_mode else (
                    "2:1 横版电影感惊悚漫画分镜，单一视觉焦点，人物与环境具有明确叙事关系，"
                    f"具体呈现“{'；'.join(str(scene.get('visual_summary') or '') for scene in group)}”，"
                    "用景别、遮挡、阴影和冷色光营造悬念，忠于原文，不凭空添加鬼怪或血腥内容，"
                    "不要拼贴、分格、字幕、水印或边框。"
                )
            ),
        }
        for index, group in enumerate(groups, 1)
    ]


def _normalize_mapping(
    raw: Any,
    scenes: list[dict[str, Any]],
    required_groups: list[list[str]] | None = None,
) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    remaining = {str(scene["slide_id"]) for scene in scenes}
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            continue
        included = [str(value) for value in item.get("includes_slides", []) if str(value) in remaining]
        prompt = str(item.get("image_prompt", "")).strip()
        if not included or not prompt:
            continue
        for slide_id in included:
            remaining.discard(slide_id)
        normalized.append(
            {
                "macro_scene_id": f"poster_{index:03d}",
                "includes_slides": included,
                "image_prompt": prompt,
                "character_ids": list(dict.fromkeys(
                    value.strip()
                    for value in item.get("character_ids", [])
                    if isinstance(value, str) and value.strip()
                ))[:10],
                "reference_image_ids": list(dict.fromkeys(
                    value.strip()
                    for value in item.get("reference_image_ids", [])
                    if isinstance(value, str) and value.strip() in REFERENCE_IMAGE_LABELS
                ))[:3],
            }
        )
    if not normalized or remaining:
        return None
    if required_groups is not None:
        actual_groups = [item["includes_slides"] for item in normalized]
        if actual_groups != required_groups:
            return None
    return normalized


def _multi_moment_prompt_risk(prompt: str) -> bool:
    """Detect prompts likely to make an image model invent comic panels."""
    text = str(prompt or "")
    if re.search(r"对照|对比|差异展示|前后变化比较", text):
        return False
    return bool(re.search(
        r"随后|依次|先.+再|镜头拉开|镜头转向|画面左侧|画面右侧|上半部分|下半部分|"
        r"分屏|多格|拼贴|四格|三格|多个时间点",
        text,
    ))


_COMMON_VISUAL_ANCHORS = (
    "餐桌", "账单", "厨房", "客厅", "卧室", "办公室", "工位", "地铁", "公交",
    "病房", "医院走廊", "候诊区", "街道", "人行道", "教室", "实验室", "会议室",
)


def _repeated_visual_anchor_runs(
    mapping: list[dict[str, Any]],
    story_context: dict[str, Any] | None = None,
    *,
    minimum_run: int = 3,
) -> list[tuple[str, int, int]]:
    """Find consecutive prompts that keep reusing one concrete visual anchor."""
    anchors = set(_COMMON_VISUAL_ANCHORS)
    for location in (story_context or {}).get("locations", []):
        if not isinstance(location, dict):
            continue
        name = str(location.get("name") or "").strip()
        if len(name) >= 2:
            anchors.add(name)
        for common in _COMMON_VISUAL_ANCHORS:
            if common in name:
                anchors.add(common)
    result: list[tuple[str, int, int]] = []
    for anchor in sorted(anchors, key=len, reverse=True):
        run_start = None
        for index, item in enumerate(mapping):
            present = anchor in str(item.get("image_prompt") or "")
            if present and run_start is None:
                run_start = index
            if (not present or index == len(mapping) - 1) and run_start is not None:
                run_end = index if present and index == len(mapping) - 1 else index - 1
                if run_end - run_start + 1 >= minimum_run:
                    result.append((anchor, run_start, run_end))
                run_start = None
    return result


def _single_scene_guard(prompt: str) -> str:
    if not _multi_moment_prompt_risk(prompt):
        return prompt
    return (
        "【单镜头构图硬约束】只定格下述内容中最有代表性的一个瞬间；"
        "保持单一连续场景、单一机位和单一时间点，不使用多格漫画、分屏、拼贴，"
        "不让同一角色重复出现在画面中。\n"
        + prompt
    )


def _apply_visual_safety_guard(prompt: str) -> str:
    """Turn commonly blocked explicit imagery into indirect cinematic language."""
    guarded = str(prompt or "").strip()
    replacements = {
        "开膛破肚": "事发过程被门框与阴影完全遮挡",
        "血肉模糊": "受伤细节被阴影与遮挡隐藏",
        "肢解": "危险事件以人物反应和散落物件间接表达",
        "断肢": "危险事件以远景剪影间接表达",
        "内脏外露": "伤情细节不直接入镜",
        "器官外露": "伤情细节不直接入镜",
        "喷溅鲜血": "克制的暗红环境痕迹",
        "大量鲜血": "少量克制的暗红环境痕迹",
        "满地鲜血": "地面一处克制的暗红痕迹",
        "血流成河": "异常事件留下的暗红环境痕迹",
        "腐烂尸体": "被雾气和遮挡隐藏的静止轮廓远景",
        "赤裸尸体": "被完整遮盖的静止轮廓远景",
        "强奸": "侵害事件只用门外视角和受害者事后反应表达",
        "性侵": "侵害事件只用门外视角和受害者事后反应表达",
        "割腕": "自伤情节只用人物情绪和被移开的危险物品表达",
        "上吊": "死亡情节只用空镜、影子和旁观者反应表达",
    }
    changed = False
    for unsafe, safer in replacements.items():
        if unsafe in guarded:
            guarded = guarded.replace(unsafe, safer)
            changed = True
    if changed:
        guarded += "；敏感事件不直接展示过程或伤情细节，使用远景、遮挡、剪影与人物反应表达。"
    return guarded


STYLE_META_DIRECTIVES = (
    "同一角色的脸型、发型、年龄、服装和标志性物件在所有画面中保持一致。",
    "同一角色的脸型、发型、年龄、服装和标志性物件在所有画面中保持一致",
)


def _clean_style_for_image_prompt(style: str, quality_requirement: str) -> str:
    """Keep visual traits, but remove cross-image instructions a single image model cannot execute."""
    cleaned = str(style or "").replace(quality_requirement, "")
    for directive in STYLE_META_DIRECTIVES:
        cleaned = cleaned.replace(directive, "")
    return cleaned.strip()


def _illustration_medium_lock(style: str) -> str:
    """Strengthen an explicitly illustrated medium without changing the user's subject matter."""
    text = str(style or "").strip()
    if not text:
        return ""
    illustration_markers = ("插画", "漫画", "绘本", "手绘", "条漫", "平涂", "厚涂", "水彩", "国画")
    photo_markers = ("摄影风", "真人实拍", "照片级", "纪实摄影", "写实摄影")
    if any(marker in text for marker in illustration_markers) and not any(marker in text for marker in photo_markers):
        return (
            "【视觉媒介锁】明确采用绘制类视觉媒介，保留与所选画风一致的线条、色块或可控笔触；"
            "不是摄影，不是真人实拍，不做照片级皮肤和镜头质感。"
        )
    return ""


def _style_protagonist_identity(style: str) -> str:
    """Extract an explicit user-authored protagonist lock from the style prompt."""
    match = re.search(r"主角\s*(?:为|是)\s*([^。；\n]+)", str(style or ""))
    return match.group(1).strip(" ，。；") if match else ""


def _character_descriptions(
    story_plan: dict[str, Any] | None,
    forced_style: str = "",
) -> list[tuple[str, str, str]]:
    """Build name -> immutable identity replacements without multi-stage wardrobe summaries."""
    generic_names = {"她", "他", "主角", "女人", "男人", "女孩", "男孩", "少女", "妈妈", "母亲", "父亲"}
    replacements: list[tuple[str, str]] = []
    characters = (story_plan or {}).get("characters")
    if not isinstance(characters, list):
        return replacements
    for character in characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        if len(name) < 2 or name in generic_names:
            continue
        protagonist_override = _style_protagonist_identity(forced_style)
        role = str(character.get("role") or "").strip()
        description = (
            protagonist_override
            if protagonist_override and "主角" in role
            else str(character.get("appearance") or "").strip(" ，。；")
        )
        if description:
            identity_label = f"{role}{name}" if role and role not in name else name
            reference_label = _character_reference_label(name)
            reference_clause = f"，角色形象参考{reference_label}" if reference_label else ""
            replacements.append((name, role, f"{identity_label}（{description}{reference_clause}）"))
    return sorted(replacements, key=lambda pair: len(pair[0]), reverse=True)


def _expand_character_names(
    prompt: str,
    story_plan: dict[str, Any] | None,
    forced_style: str = "",
) -> tuple[str, int]:
    expanded = str(prompt or "")
    count = 0
    for name, role, description in _character_descriptions(story_plan, forced_style):
        if name not in expanded:
            continue
        identity_label = f"{role}{name}" if role and role not in name else name
        if role:
            expanded = re.sub(
                rf"(?:{re.escape(role)}){{2,}}(?={re.escape(name)})",
                role,
                expanded,
            )
        canonical_inside = description.removeprefix(identity_label).strip()
        if canonical_inside.startswith("（") and canonical_inside.endswith("）"):
            canonical_inside = canonical_inside[1:-1]
        pattern = re.compile(
            rf"(?:{re.escape(role)})?{re.escape(name)}(?P<parens>(?:（[^（）]*）)*)"
            if role else rf"{re.escape(name)}(?P<parens>(?:（[^（）]*）)*)"
        )

        def replace_character(match: re.Match[str]) -> str:
            parts = [value.strip() for value in re.split(r"[，,；;]", canonical_inside) if value.strip()]
            for group in re.findall(r"（([^（）]*)）", match.group("parens") or ""):
                for value in re.split(r"[，,；;]", group):
                    value = value.strip()
                    if not value or re.fullmatch(r"角色形象(?:严格)?参考图[1-3]", value):
                        continue
                    if any(value == existing or value in existing for existing in parts):
                        continue
                    parts.append(value)
            return f"{identity_label}（{'，'.join(dict.fromkeys(parts))}）"

        expanded, replacements = pattern.subn(replace_character, expanded)
        count += replacements
    return expanded, count


def _active_wardrobe_state(
    character: dict[str, Any],
    included_slides: list[str],
    scenes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    states = character.get("wardrobe_states")
    if not isinstance(states, list) or not states or not included_slides:
        return None
    positions = {
        str(scene.get("slide_id") or ""): index
        for index, scene in enumerate(scenes)
    }
    included_positions = [positions[value] for value in included_slides if value in positions]
    if not included_positions:
        return None
    center = sum(included_positions) / len(included_positions)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        start = positions.get(str(state.get("start_slide_id") or ""))
        end = positions.get(str(state.get("end_slide_id") or ""))
        if start is None or end is None:
            continue
        start, end = min(start, end), max(start, end)
        overlap = sum(1 for value in included_positions if start <= value <= end)
        if overlap:
            candidates.append((overlap * 1000 - abs(center - (start + end) / 2), state))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _character_reference_for_record(character: dict[str, Any]) -> str:
    for token in [
        str(character.get("name") or "").strip(),
        *[str(value).strip() for value in character.get("aliases", []) if str(value).strip()],
    ]:
        label = _character_reference_label(token)
        if label:
            return label
    return ""


def _shot_character_ids(
    item: dict[str, Any],
    prompt: str,
    story_plan: dict[str, Any] | None,
    scenes: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Resolve active people from structured IDs, Agent 1 units, then safe name matching."""
    characters = [
        character for character in (story_plan or {}).get("characters", [])
        if isinstance(character, dict) and str(character.get("character_id") or "").strip()
    ]
    valid = {str(character["character_id"]): character for character in characters}
    selected = [
        str(value).strip() for value in item.get("character_ids", [])
        if str(value).strip() in valid
    ]
    included = {str(value) for value in item.get("includes_slides", [])}
    scene_order = [
        str(scene.get("slide_id") or "")
        for scene in (scenes or [])
        if isinstance(scene, dict)
    ]
    positions = {slide_id: index for index, slide_id in enumerate(scene_order) if slide_id}
    included_positions = [positions[value] for value in included if value in positions]
    if included:
        for unit in (story_plan or {}).get("semantic_units", []):
            if not isinstance(unit, dict):
                continue
            start_id = str(unit.get("start_slide_id") or "")
            end_id = str(unit.get("end_slide_id") or "")
            overlaps = start_id in included or end_id in included
            if not overlaps and included_positions and start_id in positions and end_id in positions:
                unit_start, unit_end = sorted((positions[start_id], positions[end_id]))
                overlaps = any(unit_start <= value <= unit_end for value in included_positions)
            if overlaps:
                selected.extend(
                    str(value).strip() for value in unit.get("character_ids", [])
                    if str(value).strip() in valid
                )
        for beat in (story_plan or {}).get("story_beats", []):
            if not isinstance(beat, dict):
                continue
            if included.intersection(str(value) for value in beat.get("slide_ids", [])):
                selected.extend(
                    str(value).strip() for value in beat.get("character_ids", [])
                    if str(value).strip() in valid
                )
    if not selected:
        for character_id, character in valid.items():
            tokens = [
                str(character.get("name") or "").strip(),
                *[str(value).strip() for value in character.get("aliases", []) if str(value).strip()],
            ]
            if any(len(token) >= 2 and token in prompt for token in tokens):
                selected.append(character_id)
    return list(dict.fromkeys(selected))


def _compact_character_mentions(
    prompt: str,
    characters: list[dict[str, Any]],
) -> tuple[str, int]:
    """Keep identity details in the role card and names/actions in the scene body."""
    compacted = str(prompt or "")
    changes = 0
    for character in characters:
        name = str(character.get("name") or "").strip()
        if not name:
            continue
        role = str(character.get("role") or "").strip()
        aliases = [str(value).strip() for value in character.get("aliases", []) if str(value).strip()]
        tokens = sorted(set([name, *aliases]), key=len, reverse=True)
        for token in tokens:
            optional_role = rf"(?:{re.escape(role)})?" if role and role not in token else ""
            pattern = re.compile(
                optional_role + re.escape(token) + r"(?P<details>(?:（[^（）]*）)+)?"
            )
            compacted, count = pattern.subn(name, compacted)
            changes += count
    return compacted, changes


def _character_continuity_block(
    original_prompt: str,
    story_plan: dict[str, Any] | None,
    forced_style: str,
    included_slides: list[str],
    scenes: list[dict[str, Any]],
    selected_character_ids: list[str] | None = None,
) -> str:
    characters = (story_plan or {}).get("characters")
    if not isinstance(characters, list):
        return ""
    protagonist_override = _style_protagonist_identity(forced_style)
    selected = set(selected_character_ids or [])
    lines: list[str] = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        character_id = str(character.get("character_id") or "").strip()
        role = str(character.get("role") or "").strip()
        explicitly_present = bool(character_id in selected) if selected else bool(name and name in original_prompt)
        generic_protagonist = (
            not selected
            and
            "主角" in role
            and not explicitly_present
            and bool(re.search(r"主角|女主|她|女人|女性|妈妈", original_prompt))
        )
        if not explicitly_present and not generic_protagonist:
            continue
        identity = (
            protagonist_override
            if protagonist_override and "主角" in role
            else str(character.get("appearance") or "").strip(" ，。；")
        )
        parts = [f"{name}：{identity}"] if identity else [name]
        reference_label = _character_reference_for_record(character)
        if reference_label:
            parts.append(f"角色形象参考{reference_label}")
        state = _active_wardrobe_state(character, included_slides, scenes)
        if state:
            wardrobe = str(state.get("wardrobe") or "").strip(" ，。；")
            headwear = str(state.get("headwear") or "").strip(" ，。；")
            carried = str(state.get("carried_items") or "").strip(" ，。；")
            if wardrobe:
                parts.append(f"本镜头服装={wardrobe}")
            style_locks_headwear = bool(
                protagonist_override
                and re.search(r"始终|一直|随时|全程", protagonist_override)
            )
            if headwear and not style_locks_headwear:
                parts.append(f"本镜头头部状态={headwear}")
            if carried:
                parts.append(f"本镜头随身物品={carried}")
        else:
            wardrobe = str(character.get("wardrobe") or "").strip(" ，。；")
            if wardrobe:
                parts.append(f"本镜头服装={wardrobe}")
        lines.append("；".join(parts))
    if not lines:
        return ""
    return (
        "【本镜头唯一角色卡：身份与造型只在本块出现一次；正文中的姓名仅表示动作关系】\n"
        + "\n".join(lines)
    )


def _plan_mapping_batch(
    scenes: list[dict[str, Any]],
    system_prompt: str,
    batch_label: str,
    story_context: dict[str, Any] | None = None,
    required_groups: list[list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]] | None:
    require_ai_success = os.getenv("REQUIRE_AI_AGENT_SUCCESS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    required_slide_groups = [
        [str(scene["slide_id"]) for scene in group]
        for group in (required_groups if required_groups is not None else _visual_groups(scenes, story_context))
    ]
    runtime_prompt = (
        system_prompt
        + "\n\n"
        + AGENT2_DEVICE_SHOT_CONTRACT
        + "\n\n【唯一角色 ID（适用于所有模式）】\n"
        + "- 每项除 includes_slides、image_prompt、reference_image_ids 外，必须输出 character_ids 数组。\n"
        + "- character_ids 只能使用 Agent 0 characters 中已有的 character_id，只列实际出镜人物；空镜、物件和仅被旁白提及的人物输出 []。\n"
        + "- image_prompt 使用角色的稳定 name，不得用可能属于多人的职业或群体称呼代替姓名，也不要重复角色完整外貌；程序会统一注入一次角色卡。\n"
        + "\n\n【单镜头构图优先级（适用于所有模式）】\n"
        + "- 默认每张图只呈现一个连续场景、一个机位和一个明确时间点，定格最能代表本组内容的瞬间。\n"
        + "- 不要把动作的前后过程同时画出；避免‘随后、依次、先……再……、镜头拉开后’等多时刻描述。\n"
        + "- 禁止漫画多格、分屏、拼贴、上下左右并列画面，以及同一角色在一张图中重复出现。\n"
        + "- 只有原文明示要做两项对照，且单一场景无法表达时，才可使用最多双区的统一构图；不得超过两区。"
        + "\n\n【视觉变化与说明性 B-roll 硬约束（适用于所有非纯科普模式）】\n"
        + "- 必须读取 Agent 1 semantic_units 中的 visual_mode、setting_hint、novelty_anchor。literal_scene 保持主时间线；illustrative_broll 使用原文明确支持的经历、原因、日常负担或未来设想；symbolic 才使用象征画面。\n"
        + "- 旁白从现场动作转入通勤、工作、家务、育儿、照料、医疗、住房等具体议题时，应把画面切到对应的生活场景，不要继续让人物坐在原地点听旁白。\n"
        + "- 相邻画面必须至少改变一项实质信息：地点、主要行动、核心物件、出镜人物组合或表达方式。只换景别、机位、人物朝向、手势或表情不算变化。\n"
        + "- 同一地点加同一核心道具最多连续使用两张；第三张必须改用原文支持的 B-roll、环境、行动、物件特写或象征表达，除非字幕仍在描述同一不可中断动作。\n"
        + "- 不得为了多样性编造具体病名、事故、既成的子女或确定结果。假设性未来要写明为设想感画面；医疗压力只能使用原文支持的通用陪诊、等候、病房或医疗物件，不擅自添加手术和危重设备。\n"
        + "\n\n【Agent 1 提供的全文故事上下文】\n"
        + json.dumps(story_context or {}, ensure_ascii=False)
        + "\n必须把这份上下文视为跨批次共享的角色、地点、线索和连续性档案。"
        + "\n\n【本次任务的强制分组】\n"
        + json.dumps(required_slide_groups, ensure_ascii=False)
        + "\n必须严格按上述顺序逐组输出：每组只生成一个对象，includes_slides 必须与对应分组完全一致，"
        "不得合并、拆分、遗漏或调整 slide_id。image_prompt 只写该组独有的具体画面内容；"
        "reference_image_ids 必须始终输出数组，只能填写本次实际使用的图号。"
        "角色使用参考图时，image_prompt 中必须写出“角色形象参考图N”，没有参考角色则输出 []。"
        "不要重复通用风格和固定画质句；但重复出场的角色必须使用角色的稳定姓名。"
        "必须根据当前 slide_id 选择 wardrobe_states 中唯一适用的一条造型，只写当前服装、当前头部状态和"
        "当前随身物品；严禁把‘前期/后期’、‘居家服或骑行服’等多个阶段同时写进一张图。"
        "用户画风中明确写出的主角年龄、发型、帽子等要求高于 Agent 1 的推断，不得改写。"
    )
    max_attempts = max(1, min(5, _positive_env_int("AGENT2_PLAN_MAX_ATTEMPTS", 3)))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        retry_instruction = ""
        if attempt > 1:
            retry_instruction = (
                "\n\n【本次为完整性重试】上次输出可能被截断或遗漏。"
                "请从头输出完整 JSON 数组，严格一组对应一项，不要附加解释。"
            )
        try:
            response = generate_gemini_text(
                system_prompt=runtime_prompt + retry_instruction,
                user_prompt=json.dumps({"scenes": scenes, "required_groups": required_slide_groups}, ensure_ascii=False),
                temperature=0.3 if attempt == 1 else 0.15,
                response_mime_type="application/json",
                json_root="array",
            )
            raw_mapping = parse_json_response(response)
            if isinstance(raw_mapping, dict):
                for wrapper_key in ("items", "mapping", "posters", "results", "scenes"):
                    wrapped = raw_mapping.get(wrapper_key)
                    if isinstance(wrapped, list):
                        raw_mapping = wrapped
                        break
                else:
                    if len(required_slide_groups) == 1 and {
                        "includes_slides", "image_prompt",
                    }.issubset(raw_mapping):
                        raw_mapping = [raw_mapping]
            mapping = _normalize_mapping(raw_mapping, scenes, required_slide_groups)
            if not mapping:
                raise RuntimeError("模型返回的海报映射不完整或无法解析")
            if any(_multi_moment_prompt_risk(item["image_prompt"]) for item in mapping):
                print(f"Gemini {batch_label} 检测到多时刻/多格构图风险，正在自动收束为单镜头。", flush=True)
                try:
                    revision = generate_gemini_text(
                        system_prompt=runtime_prompt,
                        user_prompt=json.dumps({
                            "scenes": scenes,
                            "required_groups": required_slide_groups,
                            "previous_output": mapping,
                            "revision_instruction": (
                                "保持 includes_slides 和角色连续性不变，重写所有有‘随后、依次、先后过程、分屏或多格’风险的 image_prompt；"
                                "每组只保留一个最具代表性的瞬间、一个机位、一个连续场景。"
                            ),
                        }, ensure_ascii=False),
                        temperature=0.2,
                        response_mime_type="application/json",
                        json_root="array",
                    )
                    raw_revision = parse_json_response(revision)
                    if isinstance(raw_revision, dict):
                        for wrapper_key in ("items", "mapping", "posters", "results", "scenes"):
                            wrapped = raw_revision.get(wrapper_key)
                            if isinstance(wrapped, list):
                                raw_revision = wrapped
                                break
                        else:
                            if len(required_slide_groups) == 1 and {
                                "includes_slides", "image_prompt",
                            }.issubset(raw_revision):
                                raw_revision = [raw_revision]
                    revised_mapping = _normalize_mapping(raw_revision, scenes, required_slide_groups)
                    if revised_mapping:
                        mapping = revised_mapping
                except (GeminiError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
                    # The original mapping is already complete.  A cosmetic
                    # single-shot rewrite must never throw away valid planning.
                    print(f"Gemini {batch_label} 单镜头优化失败，保留原始完整规划: {exc}", flush=True)
            repetition_runs = _repeated_visual_anchor_runs(mapping, story_context)
            if repetition_runs:
                readable_runs = "、".join(
                    f"{anchor}连续{end - start + 1}张"
                    for anchor, start, end in repetition_runs[:6]
                )
                print(
                    f"Gemini {batch_label} 检测到实质画面重复（{readable_runs}），正在请求 B-roll 多样化改写。",
                    flush=True,
                )
                try:
                    revision = generate_gemini_text(
                        system_prompt=runtime_prompt,
                        user_prompt=json.dumps({
                            "scenes": scenes,
                            "required_groups": required_slide_groups,
                            "previous_output": mapping,
                            "repetition_runs": repetition_runs,
                            "revision_instruction": (
                                "保持 includes_slides、事实、人物身份和单镜头构图不变，重写重复画面的 image_prompt。"
                                "严格读取对应 semantic_units 的 visual_mode、setting_hint、novelty_anchor；"
                                "把原文明确提到的通勤、工作、家务、照料、医疗、住房压力或未来设想改成各自具体的说明性 B-roll。"
                                "相邻画面至少改变地点、主要行动、核心物件、人物组合或表达方式之一；只换机位和表情不算变化。"
                                "不得编造病名、事故、手术、危重设备、已经存在的子女或其他原文未确认事实。"
                            ),
                        }, ensure_ascii=False),
                        temperature=0.25,
                        response_mime_type="application/json",
                        json_root="array",
                    )
                    raw_revision = parse_json_response(revision)
                    if isinstance(raw_revision, dict):
                        for wrapper_key in ("items", "mapping", "posters", "results", "scenes"):
                            wrapped = raw_revision.get(wrapper_key)
                            if isinstance(wrapped, list):
                                raw_revision = wrapped
                                break
                    revised_mapping = _normalize_mapping(raw_revision, scenes, required_slide_groups)
                    if revised_mapping:
                        mapping = revised_mapping
                except (GeminiError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
                    print(f"Gemini {batch_label} B-roll 多样化改写失败，保留原始完整规划: {exc}", flush=True)
            print(f"Gemini {batch_label} 已规划 {len(mapping)} 张海报。", flush=True)
            return mapping
        except (GeminiError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt < max_attempts:
                print(
                    f"Gemini {batch_label} 第 {attempt}/{max_attempts} 次输出不完整，正在自动重试: {exc}",
                    flush=True,
                )
                continue
            print(f"Gemini {batch_label} 连续 {max_attempts} 次规划失败: {exc}", flush=True)
    if require_ai_success:
        raise RuntimeError(
            f"Agent 2 {batch_label}语言模型规划失败，已在提交 Image2 前安全终止；"
            "配音与字幕已保留，可排除 API Key、余额、限流或上游服务问题后断点续跑。"
            f"原始错误：{last_error}"
        ) from last_error
    return None


def _plan_mapping_groups_resilient(
    batch_groups: list[list[dict[str, Any]]],
    system_prompt: str,
    batch_label: str,
    story_context: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Retry a malformed Agent 2 batch at a smaller, group-safe size."""
    batch = [scene for group in batch_groups for scene in group]
    try:
        mapping = _plan_mapping_batch(
            batch,
            system_prompt,
            batch_label,
            story_context,
            required_groups=batch_groups,
        )
    except RuntimeError:
        if len(batch_groups) <= 1:
            raise
        mapping = None
    if mapping is not None or len(batch_groups) <= 1:
        return mapping
    midpoint = max(1, len(batch_groups) // 2)
    left_groups = batch_groups[:midpoint]
    right_groups = batch_groups[midpoint:]
    print(
        f"Agent 2 {batch_label}完整重试仍失败，改为 {len(left_groups)}+{len(right_groups)} 个固定分组缩小重试。",
        flush=True,
    )
    left = _plan_mapping_groups_resilient(left_groups, system_prompt, f"{batch_label}-A", story_context)
    right = _plan_mapping_groups_resilient(right_groups, system_prompt, f"{batch_label}-B", story_context)
    if left is None or right is None:
        return None
    return [*left, *right]


def _synchronized_reference_image_ids(
    item: dict[str, Any],
    prompt: str,
    original_prompt: str,
    story_plan: dict[str, Any] | None,
    explicit_character_ids: list[str] | None = None,
) -> list[str]:
    """Recover reference IDs without turning broad story context into image input.

    ``item['character_ids']`` is enriched later with Agent 1 semantic context so
    continuity cards can survive pronouns.  That enriched list is deliberately
    *not* sufficient evidence that a person is visible in the shot: a prop or
    environment shot may belong to a semantic unit involving the protagonist.
    Reference images are therefore bound only when Agent 2 explicitly selected
    the character, or when the original shot prompt names that character (or an
    explicit reference marker).
    """
    catalog = _reference_image_catalog()
    if not catalog:
        return []
    selected = {
        str(value).strip()
        for value in item.get("reference_image_ids", [])
        if str(value).strip() in catalog
    }
    # Inspect the original Agent 2 prompt rather than ``prompt``.  The finalized
    # prompt can contain a continuity card added from Agent 1 context, which must
    # not by itself switch an empty/environment shot to image-to-image.
    for label in catalog:
        number = re.escape(label.removeprefix("图"))
        if re.search(rf"(?:角色)?形象参考图\s*{number}(?!\d)", original_prompt):
            selected.add(label)
    characters = (story_plan or {}).get("characters")
    if isinstance(characters, list):
        active_ids = {
            str(value).strip() for value in (explicit_character_ids or []) if str(value).strip()
        }
        for character in characters:
            if not isinstance(character, dict):
                continue
            name = str(character.get("name") or "").strip()
            character_id = str(character.get("character_id") or "").strip()
            if (character_id and character_id in active_ids) or (name and name in original_prompt):
                label = _character_reference_for_record(character)
                if label:
                    selected.add(label)
    return [label for label in catalog if label in selected][:3]


def _extract_explicit_screen_content(text: str) -> tuple[str, str]:
    """Return a conservative device type/content pair from explicit source text."""
    source = str(text or "").strip()
    device_type = next(
        (name for name in ("手机", "平板", "电脑显示器", "电脑", "显示器") if name in source),
        "设备",
    )
    device_pattern = r"手机|平板|电脑|显示器|屏幕|笔记本电脑|监控画面|网页|短信|聊天记录"
    explicit = re.search(
        rf"(?:{device_pattern})(?:上|里|中|内容)?(?:清楚)?(?:显示|写着|出现|弹出|呈现|是|为|内容是)",
        source,
    )
    quoted = re.search(
        r"(?:短信|消息|聊天记录)(?:内容)?(?:是|为|写着|显示为|[:：]).{0,12}?"
        r"[“「『‘\"]([^”」』’\"]{1,160})[”」』’\"]",
        source,
    )
    if quoted:
        return device_type, quoted.group(1).strip()
    if explicit:
        return device_type, source[explicit.start():].strip(" ，。；")[:240]
    return device_type, ""


def _compact_device_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _device_content_parts(screen_content: str) -> list[str]:
    parts = [
        str(value).strip()
        for value in re.split(r"[、，,；;]+", str(screen_content or ""))
        if str(value).strip()
    ]
    return parts or ([str(screen_content).strip()] if str(screen_content).strip() else [])


def _device_type_parts(device_type: str) -> list[str]:
    return [
        str(value).strip()
        for value in re.split(r"[/／、，,；;]+", str(device_type or ""))
        if str(value).strip()
    ]


def _device_content_local_score(content: str, group_text: str) -> int:
    """Estimate whether one Agent 1 information item is present in this group.

    Chinese bigram overlap is intentionally conservative: one broad semantic
    unit may contain several documents, while each fixed image group should
    inherit at most the item actually named by its own subtitle text.
    """
    content_text = _compact_device_text(content)
    local_text = _compact_device_text(group_text)
    if not content_text or not local_text:
        return 0
    if content_text in local_text:
        return 100 + len(content_text)
    if len(local_text) >= 3 and local_text in content_text:
        return 80 + len(local_text)
    content_bigrams = {content_text[index:index + 2] for index in range(len(content_text) - 1)}
    local_bigrams = {local_text[index:index + 2] for index in range(len(local_text) - 1)}
    overlap = content_bigrams & local_bigrams
    # A single generic bigram such as “内容” or “文件” is not enough to turn a
    # whole child poster into the same insert shot.
    weak = {"内容", "文件", "记录", "手机", "屏幕", "报告", "照片", "消息", "账单"}
    strong_overlap = overlap - weak
    return len(strong_overlap) * 10 + len(overlap)


def _localize_device_insert(
    device_type: str,
    screen_content: str,
    group_text: str,
) -> tuple[str, str]:
    """Select one source-backed information item for the current image group."""
    content_parts = _device_content_parts(screen_content)
    if not content_parts:
        return "", ""
    scored = [(_device_content_local_score(content, group_text), index, content) for index, content in enumerate(content_parts)]
    score, selected_index, selected_content = max(scored, key=lambda value: (value[0], -value[1]))
    reference_only = bool(re.search(
        r"(?:这|那|该|上述|前述)(?:条|张|份|个)?(?:消息|短信|照片|文件|报告|账单|记录|网页|画面)",
        str(group_text or ""),
    ))
    if score <= 0 and not reference_only:
        return "", ""
    type_parts = _device_type_parts(device_type)
    selected_type = (
        type_parts[selected_index]
        if len(type_parts) == len(content_parts) and selected_index < len(type_parts)
        else str(device_type or "").strip()
    )
    return selected_type, selected_content


def _localize_key_information_object(
    story_plan: dict[str, Any] | None,
    group_text: str,
) -> tuple[str, str]:
    """Match a fixed child group against Agent 0's precise information registry.

    Agent 1 may summarize a long evidence sequence and omit one of several
    documents.  The full-text Agent 0 registry is more precise for deciding
    which individual message, report or bill belongs to each child poster.
    """
    matches: list[tuple[int, int, str, str]] = []
    for index, record in enumerate((story_plan or {}).get("key_information_objects", [])):
        if not isinstance(record, dict):
            continue
        content = str(record.get("content") or "").strip()
        if not content:
            continue
        score = _device_content_local_score(content, group_text)
        matches.append((score, -index, str(record.get("device_type") or "").strip(), content))
    if not matches:
        return "", ""
    score, _order, device_type, content = max(matches, key=lambda value: (value[0], value[1]))
    # Two meaningful Chinese bigrams (or a direct substring) are required. This
    # rejects generic overlaps such as only “报告” or “记录”.
    if score < 20:
        return "", ""
    return device_type, content


_DEVICE_EVIDENCE_CUE_RE = re.compile(
    r"手机|平板|电脑|显示器|屏幕|网页|页面|界面|短信|消息|聊天记录|"
    r"报告|账单|照片|文件|票据|档案|动态|记录|表格|清单|截图|打印"
)


def _device_insert_assignments(
    mapping: list[dict[str, Any]],
    story_plan: dict[str, Any] | None,
    scenes: list[dict[str, Any]],
) -> dict[int, tuple[str, str]]:
    """Assign each structured screen/document object to at most one child shot.

    Agent 1 semantic units can be wider than Python's fixed image groups.  The old
    per-item fuzzy lookup consequently copied one cost table or workflow page into
    every later group that happened to repeat words such as H3 or RunningHub.  This
    global assignment keeps the useful anaphora recovery, but requires local
    evidence and consumes a structured object only once.
    """
    if not mapping or not story_plan:
        return {}
    scene_ids = [str(scene.get("slide_id") or "") for scene in scenes]
    positions = {slide_id: index for index, slide_id in enumerate(scene_ids) if slide_id}
    text_by_id = {
        str(scene.get("slide_id") or ""): str(scene.get("text_content") or "")
        for scene in scenes
    }
    groups: list[tuple[set[int], str, str]] = []
    for item in mapping:
        item_positions = {
            positions[slide_id]
            for slide_id in (str(value) for value in item.get("includes_slides", []))
            if slide_id in positions
        }
        group_text = "".join(
            text_by_id.get(str(value), "") for value in item.get("includes_slides", [])
        )
        groups.append((item_positions, group_text, _compact_device_text(group_text)))

    screen_ranges: list[tuple[int, int]] = []
    semantic_records: list[tuple[str, str, tuple[int, int]]] = []
    for unit in story_plan.get("semantic_units", []):
        if not isinstance(unit, dict) or str(unit.get("device_shot_mode") or "none") != "screen_insert":
            continue
        start = positions.get(str(unit.get("start_slide_id") or ""))
        end = positions.get(str(unit.get("end_slide_id") or ""))
        if start is None or end is None:
            continue
        bounds = tuple(sorted((start, end)))
        screen_ranges.append(bounds)
        contents = _device_content_parts(str(unit.get("screen_content") or ""))
        types = _device_type_parts(str(unit.get("device_type") or ""))
        for content_index, content in enumerate(contents):
            device_type = (
                types[content_index]
                if len(types) == len(contents) and content_index < len(types)
                else str(unit.get("device_type") or "").strip()
            )
            semantic_records.append((device_type, content, bounds))
    if not screen_ranges:
        return {}

    records: list[tuple[str, str, tuple[int, int]]] = []
    # Agent 0's registry is usually more precise than Agent 1's combined summary.
    for record in story_plan.get("key_information_objects", []):
        if not isinstance(record, dict) or not str(record.get("content") or "").strip():
            continue
        for bounds in screen_ranges:
            records.append((
                str(record.get("device_type") or "").strip(),
                str(record.get("content") or "").strip(),
                bounds,
            ))
    records.extend(semantic_records)

    deduplicated: list[tuple[str, str, tuple[int, int]]] = []
    seen_records: set[tuple[str, int, int]] = set()
    for device_type, content, bounds in records:
        signature = (_compact_device_text(content), bounds[0], bounds[1])
        if not signature[0] or signature in seen_records:
            continue
        seen_records.add(signature)
        deduplicated.append((device_type, content, bounds))

    candidate_pairs: list[tuple[int, int, str, str, str]] = []
    for device_type, content, (start, end) in deduplicated:
        compact_content = _compact_device_text(content)
        for item_index, (item_positions, group_text, compact_group) in enumerate(groups):
            if not item_positions or not any(start <= value <= end for value in item_positions):
                continue
            score = _device_content_local_score(content, group_text)
            if score <= 0:
                continue
            direct = bool(
                compact_content in compact_group
                or (len(compact_group) >= 4 and compact_group in compact_content)
            )
            reference_only = bool(re.search(
                r"(?:这|那|该|上述|前述)(?:条|张|份|个)?(?:消息|短信|照片|文件|报告|账单|记录|网页|画面)",
                group_text,
            ))
            previous_tail = groups[item_index - 1][1][-60:] if item_index > 0 else ""
            evidence_cue = bool(_DEVICE_EVIDENCE_CUE_RE.search(group_text))
            continuation_cue = bool(
                previous_tail
                and _DEVICE_EVIDENCE_CUE_RE.search(previous_tail)
                and score >= 40
            )
            if not (direct or reference_only or ((evidence_cue or continuation_cue) and score >= 20)):
                continue
            rank = score + (10000 if direct else 0) + (2000 if reference_only else 0) + (500 if evidence_cue else 0)
            candidate_pairs.append((rank, item_index, device_type, content, compact_content))

    assignments: dict[int, tuple[str, str]] = {}
    used_contents: set[str] = set()
    # Sort only by confidence. Python's stable sort preserves Agent 0/Agent 1
    # source order when two evidence fragments score equally.
    for _rank, item_index, device_type, content, compact_content in sorted(
        candidate_pairs, key=lambda value: value[0], reverse=True
    ):
        if item_index in assignments or compact_content in used_contents:
            continue
        assignments[item_index] = (device_type or "设备", content)
        used_contents.add(compact_content)
    return assignments


def _device_shot_for_item(
    item: dict[str, Any],
    story_plan: dict[str, Any] | None,
    scenes: list[dict[str, Any]],
    assigned_insert: tuple[str, str] | None = None,
) -> tuple[str, str, str]:
    """Resolve Agent 1's structured device direction for one fixed image group."""
    included = {str(value) for value in item.get("includes_slides", [])}
    scene_ids = [str(scene.get("slide_id") or "") for scene in scenes]
    positions = {slide_id: index for index, slide_id in enumerate(scene_ids) if slide_id}
    included_positions = [positions[value] for value in included if value in positions]
    candidates: list[tuple[int, str, str, str]] = []
    for unit in (story_plan or {}).get("semantic_units", []):
        if not isinstance(unit, dict):
            continue
        start = positions.get(str(unit.get("start_slide_id") or ""))
        end = positions.get(str(unit.get("end_slide_id") or ""))
        if start is None or end is None or not included_positions:
            continue
        start, end = sorted((start, end))
        if not any(start <= value <= end for value in included_positions):
            continue
        mode = str(unit.get("device_shot_mode") or "none").strip().lower()
        priority = {"none": 0, "device_interaction": 1, "screen_insert": 2}.get(mode, 0)
        candidates.append((
            priority,
            mode if priority else "none",
            str(unit.get("device_type") or "").strip()[:40],
            str(unit.get("screen_content") or "").strip()[:300],
        ))

    group_text = "".join(
        str(scene.get("text_content") or "")
        for scene in scenes
        if str(scene.get("slide_id") or "") in included
    )
    source_device_type, explicit_source_content = _extract_explicit_screen_content(group_text)
    if candidates:
        _priority, mode, device_type, screen_content = max(candidates, key=lambda value: value[0])
    else:
        mode, device_type, screen_content = "none", "", ""

    # A verbatim explicit screen statement is safe to elevate even if a custom
    # Agent 1 preset forgot the new field. Conversely, screen_insert without any
    # source-backed content is downgraded so downstream models cannot invent UI.
    if explicit_source_content:
        mode = "screen_insert"
        device_type = device_type or source_device_type
        screen_content = explicit_source_content
    elif mode == "screen_insert":
        if assigned_insert:
            device_type, screen_content = assigned_insert
        else:
            # The parent semantic unit discusses a screen or file somewhere,
            # but this fixed child group no longer does. Keep Agent 2's unique
            # scene instead of repeating the parent's evidence insert.
            mode, device_type, screen_content = "none", "", ""
    if mode == "screen_insert" and not screen_content:
        mode = "device_interaction"
    if mode == "device_interaction" and not device_type:
        device_type = source_device_type
    if mode == "none":
        device_type = ""
        screen_content = ""
    elif mode != "screen_insert":
        screen_content = ""
    return mode, device_type or "设备", screen_content


def _apply_device_shot_guard(
    prompt: str,
    mode: str,
    device_type: str,
    screen_content: str,
) -> str:
    body = str(prompt or "").strip()
    if mode == "screen_insert":
        # Keep only global medium/style headers. The Agent 2 scene body may still
        # contain a face or reaction even after being told not to; retaining it
        # would give the image model two contradictory subjects.
        style_lines = [
            line.strip() for line in body.splitlines()
            if line.strip().startswith(("【统一画面风格】", "【视觉媒介锁】"))
        ]
        physical_document = bool(re.search(r"纸|报告|账单|书信|信件|照片|文件|票据|档案", device_type))
        if physical_document:
            guarded = (
                f"【信息载体特写硬约束】本镜头只展示{device_type}正面及其内容，纸面或载体占据画面主体；"
                "不出现人物脸部、人物肖像、半身或反应特写，只允许必要的手指、纸张边缘或桌面边缘；"
                f"画面需要准确传达的核心信息为：{screen_content}；"
                "可按常见纸质材料补充合理的版式、表格线、项目符号、页边与留白等非剧情性视觉细节，"
                "版式与辅助细节可以自然发挥；涉及具体姓名、数字、结论或剧情证据时，以原文已有信息为准。"
            )
        else:
            guarded = (
                f"【设备内容镜头硬约束】本镜头只展示{device_type}正面屏幕及其内容，屏幕占据画面主体；"
                "不出现人物脸部、人物肖像、半身、反应特写或人物与屏幕并列构图，只允许必要的手指、设备边框或桌面边缘；"
                f"画面需要准确传达的核心信息为：{screen_content}；"
                "可根据常见应用形态设计合理的状态栏、列表排版、头像占位、图标、色块与视觉层级，"
                "界面与辅助信息可以自然发挥；涉及具体聊天内容、人物关系、姓名、金额、日期等剧情关键信息时，以原文已有信息为准。"
            )
        return "\n".join([*style_lines, guarded])
    if mode == "device_interaction":
        return (
            f"【设备使用镜头硬约束】表现人物正在使用{device_type}，人物动作、状态和环境是唯一视觉重点；"
            "屏幕必须背向镜头、虚化或不可读，不出现可辨识的聊天、照片、网页、文件或界面文字，"
            f"不得采用人物脸部与可读屏幕并列的双主体构图。\n{body}"
        )
    return body


def _finalize_mapping(
    mapping: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    story_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    forced_style = os.getenv("VISUAL_STYLE_PROMPT", "").strip()
    quality_requirement = (
        "避免噪点、脏污糊抹和无意义涂抹；保留所选画风需要的线稿、色块或可控绘制笔触，"
        "画面干净清晰。"
    )
    legacy_quality_requirement = "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
    clean_forced_style = _clean_style_for_image_prompt(forced_style, quality_requirement)
    clean_forced_style = clean_forced_style.replace(legacy_quality_requirement, "").strip(" \n，。；")
    medium_lock = _illustration_medium_lock(clean_forced_style)
    expanded_character_names = 0
    recovered_reference_ids = 0
    device_insert_assignments = _device_insert_assignments(mapping, story_plan, scenes)
    if device_insert_assignments:
        print(
            f"设备内容镜头局部证据校验已应用：锁定 {len(device_insert_assignments)} 个不重复信息特写。",
            flush=True,
        )
    for index, item in enumerate(mapping, 1):
        item["macro_scene_id"] = f"poster_{index:03d}"
        prompt = _single_scene_guard(_apply_visual_safety_guard(str(item.get("image_prompt") or "")))
        if forced_style:
            prompt = prompt.replace(f"默认风格为：{forced_style}", "")
            prompt = prompt.replace(forced_style, "")
        if clean_forced_style:
            prompt = prompt.replace(clean_forced_style, "")
        for directive in STYLE_META_DIRECTIVES:
            prompt = prompt.replace(directive, "")
        prompt = prompt.replace(quality_requirement, "")
        prompt = prompt.replace(legacy_quality_requirement, "").strip(" \n，。")
        original_prompt = prompt
        device_shot_mode, device_type, screen_content = _device_shot_for_item(
            item,
            story_plan,
            scenes,
            device_insert_assignments.get(index - 1),
        )
        explicit_character_ids = [
            str(value).strip()
            for value in item.get("character_ids", [])
            if str(value).strip()
        ]
        if device_shot_mode == "screen_insert":
            explicit_character_ids = []
            shot_character_ids = []
        else:
            shot_character_ids = _shot_character_ids(item, original_prompt, story_plan, scenes)
        characters_by_id = {
            str(character.get("character_id") or ""): character
            for character in (story_plan or {}).get("characters", [])
            if isinstance(character, dict)
        }
        active_characters = [
            characters_by_id[character_id]
            for character_id in shot_character_ids
            if character_id in characters_by_id
        ]
        continuity_block = "" if device_shot_mode == "screen_insert" else _character_continuity_block(
            original_prompt,
            story_plan,
            forced_style,
            [str(value) for value in item.get("includes_slides", [])],
            scenes,
            shot_character_ids,
        )
        if continuity_block:
            prompt, replacement_count = _compact_character_mentions(prompt, active_characters)
        else:
            prompt, replacement_count = _expand_character_names(prompt, story_plan, forced_style)
        expanded_character_names += replacement_count
        if continuity_block:
            prompt = f"{continuity_block}\n{prompt}"
        style_for_prompt = clean_forced_style
        if continuity_block and _style_protagonist_identity(forced_style):
            # Legacy presets sometimes mixed the protagonist profile into the
            # style field. Once a character card exists, keep that identity in
            # exactly one place instead of repeating it in the style header.
            style_for_prompt = re.sub(
                r"主角\s*(?:为|是)\s*[^。；\n]+[。；]?",
                "",
                style_for_prompt,
            ).strip(" \n，。；")
        if style_for_prompt:
            prompt = f"【统一画面风格】{style_for_prompt}\n{prompt}"
        if medium_lock:
            prompt = f"{medium_lock}\n{prompt}"
        prompt = _apply_device_shot_guard(
            prompt,
            device_shot_mode,
            device_type,
            screen_content,
        )
        prompt = f"{prompt}\n{quality_requirement}"
        item["image_prompt"] = prompt
        item["character_ids"] = shot_character_ids
        item["device_shot_mode"] = device_shot_mode
        item["device_type"] = device_type if device_shot_mode != "none" else ""
        item["screen_content"] = screen_content if device_shot_mode == "screen_insert" else ""
        previous_reference_ids = [
            str(value).strip() for value in item.get("reference_image_ids", []) if str(value).strip()
        ]
        synchronized_ids = [] if device_shot_mode == "screen_insert" else _synchronized_reference_image_ids(
            item,
            prompt,
            original_prompt,
            story_plan,
            explicit_character_ids,
        )
        recovered_reference_ids += len(set(synchronized_ids) - set(previous_reference_ids))
        item["reference_image_ids"] = synchronized_ids

    if expanded_character_names:
        print(f"角色实体展开已应用：共替换 {expanded_character_names} 处人物姓名或关系称呼。", flush=True)
    if recovered_reference_ids:
        print(f"参考图绑定保护已应用：自动补齐 {recovered_reference_ids} 个角色参考图编号。", flush=True)

    scenes_by_id = {str(scene["slide_id"]): scene for scene in scenes}
    durations = []
    for item in mapping:
        included = [scenes_by_id[slide_id] for slide_id in item["includes_slides"]]
        durations.append(
            max(float(scene["end"]) for scene in included)
            - min(float(scene["start"]) for scene in included)
        )
    if durations:
        print(
            f"画面时长约束已应用：{len(mapping)} 张，最长 {max(durations):.2f}s。",
            flush=True,
        )
    return mapping


def build_macro_mapping(
    scenes: list[dict[str, Any]],
    story_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    require_ai_success = os.getenv("REQUIRE_AI_AGENT_SUCCESS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not gemini_configured():
        if require_ai_success:
            raise RuntimeError(
                "Agent 2 语言模型未配置，已在提交 Image2 前安全终止；"
                "配音与字幕已保留，请配置语言模型后断点续跑。"
            )
        print("Gemini 未配置，模块 4 使用本地分组提示词。", flush=True)
        return _finalize_mapping(_fallback_mapping(scenes, story_plan), scenes, story_plan)

    global_character_bible = os.getenv("GLOBAL_CHARACTER_PROMPT", "").strip()
    content_mode = normalize_content_mode(os.getenv("CONTENT_MODE", CONTENT_MODE_STORY))
    custom_prompt = os.getenv("VISUAL_PROMPT_SYSTEM", "").strip()
    system_prompt = _strip_dynamic_reference_image_instructions(custom_prompt) or build_visual_prompt_system(
        style=os.getenv("VISUAL_STYLE_PROMPT", ""),
        content_mode=content_mode,
        global_character_prompt=global_character_bible,
    )
    if custom_prompt:
        system_prompt += f"\n\n【角色图像参考约束】\n{_reference_image_instruction()}"
    story_context = story_context_for_prompt(story_plan or {})
    if global_character_bible:
        story_context["user_global_character_bible"] = global_character_bible
    global_environment_bible = os.getenv("GLOBAL_ENVIRONMENT_PROMPT", "").strip()
    if global_environment_bible:
        story_context["user_world_bible"] = global_environment_bible
    protagonist_lock = _style_protagonist_identity(os.getenv("VISUAL_STYLE_PROMPT", ""))
    if protagonist_lock:
        story_context["user_protagonist_identity_lock"] = protagonist_lock
    story_context["user_reference_image_catalog"] = list(_reference_image_catalog())
    prompt_source = "自定义" if custom_prompt else "默认"
    print(
        f"Agent 2：使用{prompt_source}画面提示词命令（{len(system_prompt)} 字），"
        f"已载入 Agent 1 全文上下文。",
        flush=True,
    )

    # Rich poster prompts make large one-shot responses easy to truncate. Split only
    # between fixed visual groups: a raw scene-count split could cut an Agent 1
    # semantic event in half and silently re-enable the old local grouping logic.
    batch_size = _positive_env_int("VISUAL_PROMPT_BATCH_SCENES", 28)
    fixed_groups = _visual_groups(scenes, story_plan)
    batches: list[list[list[dict[str, Any]]]] = []
    current_batch: list[list[dict[str, Any]]] = []
    current_size = 0
    for group in fixed_groups:
        if current_batch and current_size + len(group) > batch_size:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        current_batch.append(group)
        current_size += len(group)
    if current_batch:
        batches.append(current_batch)
    if len(batches) > 1:
        print(
            f"画面规划共 {len(scenes)} 个字幕片段，拆为 {len(batches)} 批调用 Gemini，"
            "每批继承同一份画面提示词命令。",
            flush=True,
        )

    combined: list[dict[str, Any]] = []
    for index, batch_groups in enumerate(batches, 1):
        batch = [scene for group in batch_groups for scene in group]
        batch_label = f"画面规划批次 {index}/{len(batches)}"
        mapping = _plan_mapping_groups_resilient(
            batch_groups,
            system_prompt,
            batch_label,
            story_context,
        )
        if mapping is None:
            if require_ai_success:
                raise RuntimeError(
                    f"Agent 2 {batch_label}未生成有效画面规划，已在提交 Image2 前安全终止。"
                )
            print(f"{batch_label} 已降级为本地分组提示词。", flush=True)
            mapping = _fallback_mapping(batch, story_plan, required_groups=batch_groups)
        combined.extend(mapping)

    return _finalize_mapping(combined, scenes, story_plan)


def _persist_cloud_pool_session(config: dict[str, str], *, expires_in: int = 900) -> None:
    raw_path = os.getenv("CLOUD_IMAGE_POOL_SESSION_UPDATE_PATH", "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps({
        "access_token": config.get("api_key", ""),
        "refresh_token": config.get("refresh_token", ""),
        "expires_in": expires_in,
    }), encoding="utf-8")
    temporary.replace(path)


def _request_with_cloud_refresh(
    session: requests.Session,
    method: str,
    url: str,
    *,
    config: dict[str, str] | None = None,
    timeout: float = 60,
    **kwargs: Any,
) -> requests.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    if config and config.get("cloud_pool") == "1":
        headers["Authorization"] = f"Bearer {config['api_key']}"
    response = session.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if response.status_code != 401 or not config or config.get("cloud_pool") != "1":
        return response
    failed_token = config.get("api_key", "")
    response.close()
    with _CLOUD_TOKEN_REFRESH_LOCK:
        if config.get("api_key", "") == failed_token:
            refresh_token = config.get("refresh_token", "")
            refresh_url = f"{config.get('cloud_base_url', '').rstrip('/')}/auth/refresh"
            if not refresh_token or not refresh_url.startswith(("http://", "https://")):
                raise RuntimeError("云端号池登录已过期，请重新登录后断点续跑")
            refreshed = requests.post(refresh_url, json={"refresh_token": refresh_token}, timeout=30)
            refreshed.raise_for_status()
            payload = refreshed.json()
            access_token = str(payload.get("access_token") or "").strip()
            next_refresh = str(payload.get("refresh_token") or refresh_token).strip()
            if not access_token:
                raise RuntimeError("云端号池刷新登录后未返回访问令牌")
            config["api_key"] = access_token
            config["refresh_token"] = next_refresh
            try:
                expires_in = max(30, int(payload.get("expires_in") or 900))
            except (TypeError, ValueError):
                expires_in = 900
            _persist_cloud_pool_session(config, expires_in=expires_in)
    headers["Authorization"] = f"Bearer {config['api_key']}"
    return session.request(method, url, headers=headers, timeout=timeout, **kwargs)


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    config: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    response = _request_with_cloud_refresh(session, method, url, config=config, timeout=60, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("图像服务返回了无效 JSON")
    return payload


def _new_session() -> requests.Session:
    """Requests sessions are local to one worker; they are not shared between threads."""
    return requests.Session()


def _worker_count(name: str, default: int, task_count: int) -> int:
    return max(1, min(task_count, _positive_env_int(name, default)))


def _poster_worker_count(provider_configs: list[dict[str, str]], task_count: int) -> int:
    if any(config.get("cloud_pool") == "1" for config in provider_configs):
        return max(1, task_count)
    per_key = _positive_env_int("RUNNINGHUB_PER_KEY_CONCURRENCY", 1)
    account_capacity = max(1, len(provider_configs) * per_key)
    mode = os.getenv("RUNNINGHUB_CONCURRENCY_MODE", "auto").strip().lower()
    if mode == "manual":
        requested = _positive_env_int("RUNNINGHUB_ACTIVE_TASK_CONCURRENCY", account_capacity)
        account_capacity = min(account_capacity, requested)
    return max(1, min(task_count, account_capacity))


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        configured = int(raw_value)
    except ValueError:
        configured = default
    return max(1, configured)


def _retry_delay_seconds(attempt: int) -> float:
    raw_value = os.getenv("RUNNINGHUB_RETRY_DELAY_SECONDS", "8").strip()
    try:
        base_delay = max(1.0, float(raw_value))
    except ValueError:
        base_delay = 8.0
    return min(60.0, base_delay * min(attempt, 4))


def _queue_poll_seconds() -> float:
    raw_value = os.getenv("RUNNINGHUB_QUEUE_POLL_SECONDS", "5").strip()
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return 5.0


def _queue_probe_seconds() -> float:
    raw_value = os.getenv("RUNNINGHUB_QUEUE_PROBE_SECONDS", "30").strip()
    try:
        return max(_queue_poll_seconds(), float(raw_value))
    except ValueError:
        return 30.0


def _account_active_task_count(config: dict[str, str]) -> int:
    session = _new_session()
    try:
        payload = _request_json(
            session,
            "POST",
            config.get("account_url") or _runninghub_url("/uc/openapi/accountStatus"),
            headers={"Authorization": f"Bearer {config['api_key']}"},
            config=config,
            json={"apikey": config["api_key"]},
        )
    except requests.RequestException as exc:
        raise RunningHubTransientError(
            f"第三方图像接口队列状态查询网络异常: {type(exc).__name__}"
        ) from exc
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RunningHubTransientError("第三方图像接口队列状态查询失败")
    try:
        return max(0, int(payload["data"].get("currentTaskCounts", 0)))
    except (TypeError, ValueError) as exc:
        raise RunningHubTransientError("第三方图像接口返回了无效的活跃任务数") from exc


def _wait_for_queue_slot(poster_id: str, config: dict[str, str]) -> None:
    """Wait for a real RunningHub queue change after error 421."""
    max_wait = _positive_env_int("RUNNINGHUB_QUEUE_MAX_WAIT_SECONDS", 1800)
    poll_seconds = _queue_poll_seconds()
    probe_seconds = _queue_probe_seconds()
    deadline = time.monotonic() + max_wait
    blocked_task_count = _account_active_task_count(config)
    next_probe_at = time.monotonic() + probe_seconds
    print(
        f"{poster_id} 收到 421，已进入本地队列（当前第三方接口活跃任务 {blocked_task_count}）。",
        flush=True,
    )
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        active_tasks = _account_active_task_count(config)
        if active_tasks < blocked_task_count:
            print(
                f"{poster_id} 检测到第三方接口活跃任务下降（{blocked_task_count} -> {active_tasks}），准备重新提交。",
                flush=True,
            )
            return
        if time.monotonic() >= next_probe_at:
            print(f"{poster_id} 队列状态未变化，执行一次受控重新探测。", flush=True)
            return
        print(
            f"{poster_id} 仍在队列等待（第三方接口活跃任务 {active_tasks}）。",
            flush=True,
        )
    raise RuntimeError(f"{poster_id} 等待第三方接口队列空位超时（{max_wait}s）")


def _runninghub_error_code(payload: dict[str, Any], status_code: int | None = None) -> int | None:
    raw_code = payload.get("code", payload.get("errorCode", status_code))
    try:
        return int(raw_code)
    except (TypeError, ValueError):
        return status_code


def _runninghub_error_message(payload: dict[str, Any]) -> str:
    message = (
        payload.get("msg")
        or payload.get("message")
        or payload.get("errorMessage")
        or payload.get("error")
        or ""
    )
    return str(message).strip()


def _looks_like_power_insufficient(code: int | None, message: str) -> bool:
    """Recognize quota exhaustion from both submit and asynchronous task results.

    RunningHub can report a depleted account as a normal task failure or with
    one of several balance codes. That must retire the account immediately;
    retrying the same key cannot ever succeed and starves the remaining pool.
    """
    if code in {414, 416, 812}:
        return True
    normalized = str(message or "").lower()
    markers = (
        "power value", "powervalue", "insufficient power", "insufficient balance",
        "insufficient credit", "quota exceeded", "余额不足", "积分不足", "算力不足",
        "算力值不足", "点数不足", "额度不足", "余额已不足",
    )
    return any(marker in normalized for marker in markers)


def _runninghub_result_error_code(payload: dict[str, Any]) -> int | None:
    raw_code = _find_first_key(payload, {"errorCode"})
    try:
        if raw_code not in {None, ""}:
            return int(raw_code)
    except (TypeError, ValueError):
        pass
    message = _runninghub_error_message(payload)
    match = re.search(r'["\']?errorCode["\']?\s*:\s*["\']?(\d+)', message, re.I)
    return int(match.group(1)) if match else None


def _looks_like_moderation_failure(message: str) -> bool:
    normalized = str(message or "").lower()
    markers = (
        "审核", "审查", "敏感", "违规", "违禁", "内容安全", "安全策略", "风控",
        "blocked", "moderation", "content policy", "content safety", "safety check",
        "nsfw", "violation", "inappropriate", "unsafe prompt",
    )
    return any(marker in normalized for marker in markers)


def _rewrite_prompt_after_moderation(prompt: str, retry_index: int) -> str:
    guarded = _apply_visual_safety_guard(prompt)
    safety_suffixes = (
        "安全重绘：不直接展示伤害过程、伤口、血液、尸体或危险行为，改用环境空镜、人物克制反应、遮挡、远景剪影和事件后的氛围表达。",
        "审核友好重绘：保留人物关系、地点和剧情结果，但移除一切暴力、血腥、惊吓实体和敏感文字，只用光影、构图、表情与普通环境道具表达悬念。",
        "保守重绘：使用无暴力、无血腥、无裸露、无危险动作的日常场景；画面只表现人物神情和环境气氛，不呈现敏感事件本身。",
    )
    suffix = safety_suffixes[min(max(1, retry_index), len(safety_suffixes)) - 1]
    if suffix not in guarded:
        guarded = f"{guarded}\n{suffix}"
    return guarded


def _find_first_key(value: Any, key_names: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names and item:
                return item
        for item in value.values():
            found = _find_first_key(item, key_names)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_key(item, key_names)
            if found:
                return found
    return None


def _find_image_url(value: Any, *, base_url: str | None = None) -> str | None:
    """Find an image URL in a provider response.

    The cloud pool may return an absolute URL (as RunningHub does) or a
    relative URL under its own API prefix.  Keep the old absolute-only
    behaviour for direct-provider calls, but resolve relative cloud URLs at
    the call site so they are not mistaken for a successful upload with no
    usable image.
    """
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://"):
            return value
        if base_url and value.startswith("/"):
            # urljoin preserves the cloud host and avoids duplicating an
            # existing prefix such as ``/api/v1``.
            return urljoin(f"{base_url.rstrip('/')}/", value)
        return None
    if isinstance(value, dict):
        for key in (
            "fileUrl", "fileURL", "file_url",
            "imageUrl", "imageURL", "image_url",
            "downloadUrl", "downloadURL", "download_url",
            "url",
        ):
            found = _find_image_url(value.get(key), base_url=base_url)
            if found:
                return found
        for item in value.values():
            found = _find_image_url(item, base_url=base_url)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_image_url(item, base_url=base_url)
            if found:
                return found
    return None


def _runninghub_generate_url(config: dict[str, str], endpoint: str | None = None) -> str:
    endpoint = endpoint or config["endpoint"]
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return _runninghub_url(f"/openapi/v2/{endpoint.lstrip('/')}")


def _runninghub_url(path: str) -> str:
    base_url = os.getenv("RUNNINGHUB_BASE_URL", "").strip() or DEFAULT_RUNNINGHUB_BASE_URL
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _runninghub_headers(config: dict[str, str]) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
    }


def _handle_runninghub_submit_error(payload: dict[str, Any], status_code: int | None = None) -> None:
    code = _runninghub_error_code(payload, status_code)
    message = _runninghub_error_message(payload)
    if code in {421, 429}:
        raise RunningHubQueueFull(f"图像接口队列或并发额度已满（{code}）")
    if _looks_like_power_insufficient(code, message):
        raise RunningHubPowerInsufficient(
            f"当前第三方图像账号余额或算力不足（{code or '云端返回'}）。"
            "请充值或补充算力后再试。"
        )
    if code in {1014, 40310}:
        detail = f" 原因: {message}" if message else ""
        if code == 40310:
            raise RunningHubAccessDenied(
                "当前 API Key 与第三方接口类型不匹配（40310），"
                "当前模型需要具有相应权限的 API Key。" + detail
            )
        raise RunningHubAccessDenied(
            "第三方标准模型接口只允许具有相应权限的 API Key 调用。"
            "当前配置的 API Key 被拒绝（1014）。" + detail
        )
    if code == 1501 or _looks_like_moderation_failure(message):
        raise RunningHubModerationError(f"第三方图像接口审核拦截: {message or code}")
    if code == 1504:
        raise RunningHubResultRetryableError(
            f"第三方图像模型执行超时（1504）{f': {message}' if message else ''}"
        )
    if code in {408, 409, 500, 502, 503, 504, 1005, 1010, 1011, 1012}:
        detail = f"，原因: {message}" if message else ""
        raise RunningHubTransientError(f"第三方图像接口临时不可用，错误码: {code}{detail}")
    detail = f"，原因: {message}" if message else ""
    raise RunningHubTransientError(f"第三方图像接口提交失败，错误码: {code}{detail}")


def _download_image(
    session: requests.Session,
    poster_id: str,
    file_url: str,
    output: Path,
    config: dict[str, str] | None = None,
) -> Path | None:
    try:
        headers = (
            {"Authorization": f"Bearer {config['api_key']}"}
            if config and config.get("cloud_pool") == "1"
            else None
        )
        response = _request_with_cloud_refresh(
            session, "GET", str(file_url), config=config, headers=headers, timeout=120
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"{poster_id} 下载网络异常，稍后重试: {type(exc).__name__}", flush=True)
        return None
    output.write_bytes(response.content)
    if output.stat().st_size == 0:
        raise RuntimeError(f"{poster_id} 下载到空图像")
    print(f"海报已生成: {output.name}", flush=True)
    return output


def _submit_poster_request(
    macro: dict[str, Any], config: dict[str, str], session: requests.Session
) -> str:
    payload = {
        "prompt": macro["image_prompt"],
        "aspectRatio": config["ratio"],
        "resolution": config["resolution"],
    }
    endpoint: str | None = None
    selected_reference_paths = [
        str(path).strip() for path in macro.get("reference_image_paths", []) if str(path).strip()
    ][:4]
    if not selected_reference_paths:
        catalog = _reference_image_catalog()
        if any(reference_id in catalog for reference_id in macro.get("reference_image_ids", [])):
            # Keep 图 1/图 2/图 3 stable for the image model. A scene may only use 图 2,
            # but the request still carries the catalog in original order so 图 2 never
            # becomes the model's first input image by accident.
            selected_reference_paths = list(catalog.values())
    reference_urls = [
        url for url in (_reference_image_url(config, path) for path in selected_reference_paths) if url
    ]
    uses_protagonist_reference = "主角" in {str(value).strip() for value in macro.get("character_ids", [])}
    if not reference_urls and uses_protagonist_reference:
        protagonist_url = _reference_image_url(config)
        if protagonist_url:
            reference_urls = [protagonist_url]
    if reference_urls:
        payload["imageUrls"] = reference_urls
        endpoint = os.getenv("RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT", "").strip() or \
            str(config["endpoint"]).replace("/text-to-image", "/image-to-image")
    if config.get("cloud_pool") == "1":
        job_id = os.getenv("VOICE_OVER_VIDEO_JOB_ID", "").strip() or "desktop"
        scene_id = str(macro.get("macro_scene_id") or "scene").strip()
        request_fingerprint = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        stable_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{job_id}-{scene_id}").strip("-._")
        payload["clientJobId"] = f"ocv-{stable_prefix[:80]}-{request_fingerprint}"
    response = _request_with_cloud_refresh(
        session,
        "POST",
        _runninghub_generate_url(config, endpoint),
        config=config,
        headers=_runninghub_headers(config),
        json=payload,
        timeout=60,
    )
    try:
        submitted = response.json()
    except ValueError as exc:
        response.raise_for_status()
        raise RunningHubTransientError("第三方生成图接口返回了无效 JSON") from exc
    if not isinstance(submitted, dict):
        raise RunningHubTransientError("第三方生成图接口返回了无效 JSON")
    if not response.ok:
        _handle_runninghub_submit_error(submitted, response.status_code)
    submitted_data = submitted.get("data") if isinstance(submitted.get("data"), dict) else {}
    task_id = str(
        submitted.get("taskId")
        or submitted_data.get("taskId", "")
        or _find_first_key(submitted, {"taskId", "taskID", "id"})
        or ""
    )
    if not task_id:
        _handle_runninghub_submit_error(submitted, response.status_code)
        raise RuntimeError("第三方生成图接口未返回任务 ID")
    return task_id


def _poster_output_path(macro: dict[str, Any]) -> Path:
    explicit_output = str(macro.get("_output_path") or "").strip()
    if explicit_output:
        candidate = Path(explicit_output).resolve()
        redraw_root = (PROJECT_ROOT / "workspace" / "jobs").resolve()
        if redraw_root not in candidate.parents:
            raise ValueError("重绘输出路径必须位于当前项目的独立任务目录中")
        return candidate
    poster_id = macro["macro_scene_id"]
    job_id = os.getenv("VOICE_OVER_VIDEO_JOB_ID", "").strip()
    reference_source = "\0".join(
        str(path).strip() for path in macro.get("reference_image_paths", []) if str(path).strip()
    )
    suffix_source = f"{job_id}\0{macro.get('image_prompt', '')}\0{reference_source}"
    suffix = hashlib.sha1(suffix_source.encode("utf-8")).hexdigest()[:10]
    return ASSETS_DIR / f"{poster_id}_{suffix}.jpg"


def _submit_poster(macro: dict[str, Any], config: dict[str, str]) -> PosterTask:
    poster_id = macro["macro_scene_id"]
    output = _poster_output_path(macro)
    progress_label = str(macro.get("progress_label") or poster_id)
    if output.is_file() and output.stat().st_size > 0:
        print(f"复用已生成海报: {progress_label} -> {output.name}", flush=True)
        return PosterTask(macro=macro, output=output, task_id=None)

    session = _new_session()
    try:
        task_id = _submit_poster_request(macro, config, session)
    except requests.RequestException as exc:
        raise RunningHubTransientError(
            f"{poster_id} 提交图像任务网络异常: {type(exc).__name__}"
        ) from exc

    account_label = str(config.get("account_label") or "当前账号")
    print(f"海报任务已提交: {progress_label} [{account_label}] ({task_id})", flush=True)
    return PosterTask(macro=macro, output=output, task_id=task_id)


def _wait_for_poster(task: PosterTask, config: dict[str, str]) -> Path:
    if task.task_id is None:
        return task.output

    poster_id = task.macro["macro_scene_id"]
    progress_label = str(task.macro.get("progress_label") or poster_id)
    session = _new_session()
    print(f"等待海报结果: {progress_label}", flush=True)
    started_at = time.monotonic()
    deadline = started_at + _positive_env_int("RUNNINGHUB_IMAGE_MAX_WAIT_SECONDS", 1200)
    next_notice_at = started_at
    while time.monotonic() < deadline:
        try:
            result = _request_json(
                session,
                "POST",
                config.get("query_url") or _runninghub_url("/openapi/v2/query"),
                headers=_runninghub_headers(config),
                config=config,
                json={"taskId": task.task_id},
            )
        except requests.RequestException as exc:
            print(f"{poster_id} 查询网络异常，稍后重试: {type(exc).__name__}", flush=True)
            time.sleep(5)
            continue
        status = str(_find_first_key(result, {"status", "state", "taskStatus"}) or "").upper()
        file_url = _find_image_url(
            result,
            base_url=config.get("cloud_base_url") if config.get("cloud_pool") == "1" else None,
        )
        if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED"} or file_url:
            if not file_url:
                raise RuntimeError(f"{poster_id} 未返回图像下载地址")
            downloaded = _download_image(session, poster_id, str(file_url), task.output, config)
            if downloaded:
                return downloaded
        elif status in {"RUNNING", "QUEUED", "PENDING", ""}:
            now = time.monotonic()
            if now >= next_notice_at:
                elapsed = int(now - started_at)
                visible_status = status or "UNKNOWN"
                print(
                    f"{progress_label} 仍在等待返图结果，云端状态: {visible_status}，已等待 {elapsed}s",
                    flush=True,
                )
                next_notice_at = now + 30
        else:
            message = _runninghub_error_message(result)
            error_code = _runninghub_result_error_code(result)
            if error_code is None:
                error_code = _runninghub_error_code(result)
            if _looks_like_power_insufficient(error_code, message):
                raise RunningHubPowerInsufficient(
                    f"{poster_id} 的账号算力/余额不足（{error_code or '云端返回'}）: {message or status}"
                )
            if error_code in {1014, 40310}:
                raise RunningHubAccessDenied(
                    f"{poster_id} 的账号或站点无权调用当前图像模型（{error_code}）: {message or status}"
                )
            if error_code == 1501 or _looks_like_moderation_failure(message):
                raise RunningHubModerationError(
                    f"{poster_id} 的提示词被云端审核拦截: {message or status}"
                )
            if error_code == 1516:
                raise RunningHubResultRetryableError(
                    f"{poster_id} 云端返图文件异常（1516）: {message or status}"
                )
            raise RunningHubResultRetryableError(
                f"{poster_id} 的云端图像工作流执行失败: {message or status}"
            )
        time.sleep(5)
    raise RunningHubResultRetryableError(f"{poster_id} 图像生成超时")


def _render_poster_with_retry(
    macro: dict[str, Any], account_pool: RunningHubAccountPool
) -> Path:
    """Try another account after 421; queue only when every configured account is busy."""
    poster_id = macro["macro_scene_id"]
    max_attempts = _positive_env_int("RUNNINGHUB_SUBMIT_MAX_ATTEMPTS", 90)
    max_result_retries = _positive_env_int("RUNNINGHUB_RESULT_MAX_RETRIES", 8)
    max_moderation_retries = _positive_env_int("RUNNINGHUB_MODERATION_MAX_RETRIES", 3)
    result_retries = 0
    moderation_retries = 0
    working_macro = dict(macro)
    queued = False
    try:
        config = account_pool.acquire()
    except RunningHubAllAccountsBusy:
        config = account_pool.acquire_waiting_account()
        queued = True
    for attempt in range(1, max_attempts + 1):
        try:
            if queued:
                # Only one queued worker checks a newly-free slot and resubmits at a time.
                with _QUEUE_RETRY_LOCK:
                    _wait_for_queue_slot(poster_id, config)
                    task = _submit_poster(working_macro, config)
            else:
                task = _submit_poster(working_macro, config)
            result = _wait_for_poster(task, config)
            account_pool.mark_available(config)
            return result
        except RunningHubQueueFull:
            account_pool.mark_queue_full(config)
            try:
                next_config = account_pool.acquire()
            except RunningHubAllAccountsBusy:
                config = account_pool.acquire_waiting_account()
                queued = True
            else:
                print(
                    f"{poster_id} 的 {config['account_label']} 返回队列/并发限制，"
                    f"切换到空闲的 {next_config['account_label']}。",
                    flush=True,
                )
                config = next_config
                queued = False
            continue
        except RunningHubPowerInsufficient:
            account_pool.mark_power_exhausted(config)
            print(
                f"{poster_id} 的 {config['account_label']} 余额或算力不足，切换到下一个账号。",
                flush=True,
            )
            try:
                config = account_pool.acquire()
                queued = False
            except RunningHubAllAccountsBusy:
                config = account_pool.acquire_waiting_account()
                queued = True
            continue
        except RunningHubAccessDenied:
            account_pool.mark_access_denied(config)
            print(
                f"{poster_id} 的 {config['account_label']} 被当前站点或模型拒绝，切换到下一个账号。",
                flush=True,
            )
            try:
                config = account_pool.acquire()
                queued = False
            except RunningHubAllAccountsBusy:
                config = account_pool.acquire_waiting_account()
                queued = True
            continue
        except RunningHubTransientError as exc:
            if attempt == max_attempts:
                account_pool.release(config)
                raise RuntimeError(f"{poster_id} 重试 {max_attempts} 次后仍未提交: {exc}") from exc
            delay = _retry_delay_seconds(attempt)
            print(
                f"{poster_id} 遇到临时网络或服务异常，{delay:.0f}s 后重试 "
                f"({attempt}/{max_attempts}): {exc}",
                flush=True,
            )
            time.sleep(delay)
        except RunningHubModerationError as exc:
            if moderation_retries >= max_moderation_retries:
                account_pool.release(config)
                raise RuntimeError(
                    f"{poster_id} 已安全改写并重试 {max_moderation_retries} 次，仍被审核拦截: {exc}"
                ) from exc
            moderation_retries += 1
            working_macro["image_prompt"] = _rewrite_prompt_after_moderation(
                str(working_macro.get("image_prompt") or ""), moderation_retries
            )
            delay = _retry_delay_seconds(moderation_retries)
            print(
                f"{poster_id} 被审核拦截，已自动安全改写提示词，{delay:.0f}s 后只重跑该图片 "
                f"（{moderation_retries}/{max_moderation_retries}）: {exc}",
                flush=True,
            )
            time.sleep(delay)
        except RunningHubResultRetryableError as exc:
            if result_retries >= max_result_retries:
                account_pool.release(config)
                raise RuntimeError(
                    f"{poster_id} 云端返图异常，额外重试 {max_result_retries} 次后仍失败: {exc}"
                ) from exc
            result_retries += 1
            macro_output = _poster_output_path(working_macro)
            macro_output.unlink(missing_ok=True)
            delay = _retry_delay_seconds(result_retries)
            print(
                f"{poster_id} 返图文件异常，{delay:.0f}s 后重新提交该图片 "
                f"（第 {result_retries}/{max_result_retries} 次重试）: {exc}",
                flush=True,
            )
            time.sleep(delay)
        except Exception:
            account_pool.release(config)
            raise
    account_pool.release(config)
    raise AssertionError("unreachable")


def render_posters_concurrently(
    mapping: list[dict[str, Any]], provider_configs: list[dict[str, str]]
) -> list[Path]:
    """Render with bounded local concurrency; RunningHub 421 responses enter the queue."""
    if not mapping:
        return []

    uses_cloud_pool = any(config.get("cloud_pool") == "1" for config in provider_configs)
    active_workers = _poster_worker_count(provider_configs, len(mapping))
    account_pool = RunningHubAccountPool(
        provider_configs,
        per_key_concurrency=active_workers if uses_cloud_pool else None,
    )
    mapping = [
        {**macro, "progress_label": f"{macro['macro_scene_id']} ({index}/{len(mapping)})"}
        for index, macro in enumerate(mapping, 1)
    ]
    if uses_cloud_pool:
        concurrency_label = f"云端全量入队 {active_workers}，并发由服务器调度"
    else:
        mode = os.getenv("RUNNINGHUB_CONCURRENCY_MODE", "auto").strip().lower()
        per_key = _positive_env_int("RUNNINGHUB_PER_KEY_CONCURRENCY", 1)
        concurrency_label = (
            f"本地{'自动' if mode != 'manual' else '手动'}并发 {active_workers}，"
            f"单 Key 上限 {per_key}"
        )
    print(
        f"提交 {len(mapping)} 张海报任务（{concurrency_label}，"
        f"{len(provider_configs)} 个账号可轮换，421/429 优先切账号后再入队）...",
        flush=True,
    )
    print(f"[POSTER_PROGRESS] 0/{len(mapping)}", flush=True)

    completed: dict[int, Path] = {}
    failures: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=active_workers, thread_name_prefix="runninghub-task") as executor:
        futures = {
            executor.submit(_render_poster_with_retry, macro, account_pool): index
            for index, macro in enumerate(mapping)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                completed[index] = future.result()
                print(f"[POSTER_PROGRESS] {len(completed)}/{len(mapping)}", flush=True)
            except RunningHubAllAccountsPowerInsufficient as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(
                    "所有已配置的第三方图像账号余额或算力均不足，"
                    "已停止后续海报提交。请充值后重新生成。"
                ) from exc
            except RunningHubAccessDenied as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(f"第三方图像接口或模型拒绝访问：{exc}") from exc
            except RunningHubAllAccountsAccessDenied as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(
                    "所有已配置的第三方图像账号都被当前接口或模型拒绝访问。"
                    "已停止后续海报提交，请确认 API Key 具有当前模型的调用权限。"
                ) from exc
            except Exception as exc:
                failures[index] = str(exc)
    if failures:
        allow_neighbor_fallback = os.getenv(
            "RUNNINGHUB_ALLOW_NEIGHBOR_FALLBACK", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        if completed and allow_neighbor_fallback:
            successful_indices = tuple(completed)
            for failed_index, error in sorted(failures.items()):
                nearest_index = min(successful_indices, key=lambda value: abs(value - failed_index))
                completed[failed_index] = completed[nearest_index]
                print(
                    f"{mapping[failed_index]['macro_scene_id']} 单图重试仍失败，"
                    f"自动复用最近成功画面 {mapping[nearest_index]['macro_scene_id']}，任务继续: {error}",
                    flush=True,
                )
        else:
            details = "；".join(
                f"{mapping[index]['macro_scene_id']}: {error}"
                for index, error in sorted(failures.items())
            )
            raise RuntimeError("海报任务生成失败：" + details)
    return [completed[index] for index in range(len(mapping))]


def write_html(
    scenes: list[dict[str, Any]],
    poster_timeline: list[dict[str, Any]],
    html_path: Path | None = None,
    audio_url: str = "./2_audio_srt/final_output.wav",
) -> Path:
    if not poster_timeline:
        raise RuntimeError("没有可用海报，拒绝生成空白视频页面")
    total_duration = max(float(item["end"]) for item in scenes)
    poster_divs = "\n".join(
        f'<div class="poster-item" id="poster-{index}" style="background-image:url(\'{html.escape(item["url"], quote=True)}\')"></div>'
        for index, item in enumerate(poster_timeline)
    )
    poster_data = json.dumps(poster_timeline, ensure_ascii=False)
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1920, initial-scale=1.0">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}} html,body{{width:1920px;height:1080px;overflow:hidden;background:#050a12;font-family:'PingFang SC','Microsoft YaHei',sans-serif}} #stage{{position:relative;width:100%;height:100%;background:#050a12}} .poster-item{{position:absolute;left:2.1875%;top:5%;width:95.625%;height:85%;background-size:contain;background-repeat:no-repeat;background-position:center;opacity:0}} #subtitle-overlay{{position:absolute;z-index:10;left:0;top:90%;width:100%;height:10%;display:flex;align-items:center;justify-content:center;padding:4px 64px;text-align:center;pointer-events:none;overflow:hidden}} .subtitle-inner{{display:inline-block;max-width:1780px;max-height:100%;padding:5px 24px;border-radius:8px;background:rgba(7,24,52,.84);color:#fff;font-size:36px;font-weight:600;line-height:1.15;letter-spacing:0;overflow:hidden}} .subtitle-inner:empty{{display:none}} #main-audio{{display:none}}
</style></head><body>
<audio id="main-audio" src="{html.escape(audio_url, quote=True)}" data-start="0" autoplay></audio>
<div id="stage" data-composition-id="main" data-width="1920" data-height="1080" data-duration="{total_duration}" data-start="0">{poster_divs}<div id="subtitle-overlay"><div class="subtitle-inner" id="subtitle-text"></div></div></div>
<script>
window.base64Subtitle = "";
const posterTimeline = {poster_data}; let subtitleData=[];
function parseTime(value){{const p=value.split(':');const s=p[2].split(',');return +p[0]*3600 + +p[1]*60 + +s[0] + +s[1]/1000;}}
try{{if(window.base64Subtitle){{const raw=decodeURIComponent(escape(atob(window.base64Subtitle)));for(const block of raw.trim().split(/\\n\\s*\\n/)){{const lines=block.split('\\n');const match=lines[1]?.match(/([\\d:,]+)\\s*-->\\s*([\\d:,]+)/);if(match)subtitleData.push({{start:parseTime(match[1]),end:parseTime(match[2]),text:lines.slice(2).join(' ').trim()}})}}}}}}catch(error){{console.error(error)}}
window.__timelines=window.__timelines||{{}}; window.__timelines.main={{duration:{total_duration},seek(t){{let visiblePosterIndex=0;for(let index=0;index<posterTimeline.length;index+=1){{if(t>=posterTimeline[index].start)visiblePosterIndex=index;else break}}posterTimeline.forEach((poster,index)=>{{const el=document.getElementById('poster-'+index);if(index===visiblePosterIndex){{el.style.opacity=index===0?'1':String(Math.min(Math.max((t-poster.start)/.8,0),1))}}else if(index===visiblePosterIndex-1&&visiblePosterIndex>0&&t<posterTimeline[visiblePosterIndex].start+.8){{el.style.opacity='1'}}else{{el.style.opacity='0'}}}});let active=subtitleData.find(item=>t>=item.start&&t<=item.end);if(!active){{active=[...subtitleData].reverse().find(item=>t>item.end&&t-item.end<=.35)}}document.getElementById('subtitle-text').textContent=active?.text||''}},play(){{}},pause(){{}}}};
</script></body></html>"""
    html_path = html_path or VISUAL_DIR / "index.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page, encoding="utf-8")
    return html_path


def run_online_poster_engine() -> None:
    print("[模块 4] 在线海报与页面生成启动", flush=True)
    if not TIMELINE_PATH.is_file():
        raise FileNotFoundError(f"找不到模块 3 剧本: {TIMELINE_PATH}")
    scenes = json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("模块 3 剧本必须是非空数组")
    required_fields = {"slide_id", "start", "end", "visual_summary"}
    if any(not isinstance(scene, dict) or not required_fields.issubset(scene) for scene in scenes):
        raise ValueError("模块 3 剧本缺少模块 4 所需字段")

    provider_configs = _provider_configs()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    resume = os.getenv("VOICE_OVER_VIDEO_RESUME", "").strip().lower() in {"1", "true", "yes"}
    content_mode = normalize_content_mode(os.getenv("CONTENT_MODE"))
    configured_story_path = Path(os.getenv("STORY_AGENT_PLAN_PATH", str(STORY_PLAN_PATH))).resolve()
    global_story_plan = os.getenv("STORY_AGENT_PLAN_IS_GLOBAL", "").strip().lower() in {"1", "true", "yes"}
    story_plan = load_or_create_story_plan(
        scenes,
        resume=resume,
        path=configured_story_path,
        allow_source_mismatch=global_story_plan,
        content_mode=content_mode,
        require_ai_success=os.getenv("REQUIRE_AI_AGENT_SUCCESS", "").strip().lower()
        in {"1", "true", "yes", "on"},
    )
    if configured_story_path != STORY_PLAN_PATH.resolve():
        STORY_PLAN_PATH.write_text(json.dumps(story_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Agent 1：已保存本段使用的全文上下文快照: {STORY_PLAN_PATH}", flush=True)
    reuse_existing_mapping = resume and POSTER_MAPPING_PATH.is_file()
    if reuse_existing_mapping:
        if VISUAL_PROMPT_PLAN_PATH.is_file():
            try:
                previous_plan = json.loads(VISUAL_PROMPT_PLAN_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("断点续跑发现 visual_prompt_plan.json 损坏，拒绝盲目复用旧画面") from exc
            previous_scene_fingerprint = (
                previous_plan.get("scene_source_fingerprint")
                if isinstance(previous_plan, dict)
                else None
            )
            if previous_scene_fingerprint and previous_scene_fingerprint != story_fingerprint(scenes, content_mode):
                raise ValueError("断点续跑发现字幕场景已变化，旧海报规划与当前文案不匹配，已停止以防错图")
            prompt_agent_is_current = (
                isinstance(previous_plan, dict)
                and int(previous_plan.get("agent_version") or 0) >= VISUAL_PROMPT_AGENT_VERSION
                and int(previous_plan.get("story_agent_version") or 0) >= int(story_plan.get("agent_version") or 0)
                and int(previous_plan.get("character_continuity_version") or 0)
                >= int(story_plan.get("character_continuity_version") or 0)
            )
            if not prompt_agent_is_current:
                reuse_existing_mapping = False
                print("断点续跑：检测到旧版人物连续性规划，将重新生成提示词并只重画受影响图片。", flush=True)
        else:
            reuse_existing_mapping = False
            print("断点续跑：缺少可校验的 Agent 2 规划，将重新生成提示词。", flush=True)
    if reuse_existing_mapping:
        mapping = json.loads(POSTER_MAPPING_PATH.read_text(encoding="utf-8"))
        if not isinstance(mapping, list) or not mapping:
            raise ValueError("断点续跑发现 poster_mapping.json 无效，无法复用画面规划")
        print(f"断点续跑：复用已有画面规划: {POSTER_MAPPING_PATH}", flush=True)
    else:
        mapping = build_macro_mapping(scenes, story_plan)
        backup_poster_mapping()
        POSTER_MAPPING_PATH.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"最终画面提示词已保存: {POSTER_MAPPING_PATH}", flush=True)
    visual_prompt_plan = {
        "agent_version": VISUAL_PROMPT_AGENT_VERSION,
        "story_source_fingerprint": story_plan.get("source_fingerprint"),
        "story_generation_source": story_plan.get("generation_source"),
        "story_agent_version": story_plan.get("agent_version"),
        "character_continuity_version": story_plan.get("character_continuity_version"),
        "content_mode": content_mode,
        "scene_source_fingerprint": story_fingerprint(scenes, content_mode),
        "mapping": mapping,
    }
    if not resume:
        backup_poster_mapping(VISUAL_PROMPT_PLAN_PATH)
    VISUAL_PROMPT_PLAN_PATH.write_text(
        json.dumps(visual_prompt_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Agent 2：可检查的分镜提示词计划已保存: {VISUAL_PROMPT_PLAN_PATH}", flush=True)
    scenes_by_id = {str(scene["slide_id"]): scene for scene in scenes}
    assets = render_posters_concurrently(mapping, provider_configs)
    poster_timeline: list[dict[str, Any]] = []
    for macro, asset in zip(mapping, assets, strict=True):
        macro["asset_filename"] = asset.name
        included = [scenes_by_id[slide_id] for slide_id in macro["includes_slides"]]
        poster_timeline.append(
            {
                "start": min(float(scene["start"]) for scene in included),
                "end": max(float(scene["end"]) for scene in included),
                "url": f"./assets/{asset.name}",
            }
        )
    # Persist the concrete asset name so the FFmpeg renderer and archived visual
    # editor can resolve replacements without parsing the generated HTML.
    POSTER_MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    visual_prompt_plan["mapping"] = mapping
    VISUAL_PROMPT_PLAN_PATH.write_text(
        json.dumps(visual_prompt_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path = write_html(scenes, poster_timeline)
    print(f"模块 4 页面已写入: {html_path}", flush=True)


def _reference_image_url(config: dict[str, str], raw_path: str | None = None) -> str | None:
    raw_path = (raw_path or os.getenv("USER_PROTAGONIST_REFERENCE_IMAGE_PATH", "")).strip()
    path = Path(raw_path)
    if not raw_path or not path.is_file():
        return None
    cache_key = (config["api_key"], str(path.resolve()))
    with _REFERENCE_UPLOAD_LOCK:
        cached = _REFERENCE_IMAGE_URLS.get(cache_key)
        if cached:
            return cached
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload_error: Exception | None = None
        response: requests.Response | None = None
        payload: Any = {}
        try:
            # Keep the multipart body replayable.  Cloud-pool requests can
            # refresh an expired access token and retry the request; passing
            # an already-consumed file handle would upload an empty file on
            # that second attempt and surface as a misleading upload failure.
            file_bytes = path.read_bytes()
            with _new_session() as session:
                response = _request_with_cloud_refresh(
                    session,
                    "POST",
                    config.get("upload_url") or _runninghub_url("/openapi/v2/media/upload/binary"),
                    config=config,
                    headers={"Authorization": f"Bearer {config['api_key']}"},
                    files={"file": (path.name, file_bytes, mime_type)},
                    timeout=120,
                )
            payload = response.json()
        except (OSError, requests.RequestException, ValueError, RuntimeError) as exc:
            # The cloud proxy currently returns HTTP 500 for some valid image
            # uploads. Keep the generation request usable: image-pool/generate
            # accepts a data URI, so this is a safe transport fallback rather
            # than a paid retry or silently dropping the reference.
            upload_error = exc
            response = None
            payload = {}
        if response is not None and response.ok and isinstance(payload, dict):
            # RunningHub and the OCV cloud pool use slightly different response
            # envelopes and camel/snake-case field names. Search the complete
            # payload so a successful upload is not mistaken for a network error.
            url = str(
                _find_image_url(
                    payload,
                    base_url=config.get("cloud_base_url") if config.get("cloud_pool") == "1" else None,
                )
                or ""
            ).strip()
        else:
            url = ""
        if response is not None:
            response.close()
        if not url:
            # Standard-model resource fields such as ``imageUrls`` accept a
            # Base64 Data URI as well as a public URL. Some RunningHub account
            # types return a successful upload envelope without ``download_url``;
            # falling back locally keeps redraw/reference-image tasks usable and
            # avoids repeatedly submitting a paid generation request.
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError as exc:
                raise RunningHubReferenceUploadError(
                    f"参考图无法读取，不能继续重绘：{path.name}"
                ) from exc
            if mime_type == "application/octet-stream":
                suffix_mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                }.get(path.suffix.lower())
                if not suffix_mime:
                    raise RunningHubReferenceUploadError(
                        f"参考图格式不受支持：{path.suffix or '未知格式'}"
                    )
                mime_type = suffix_mime
            url = f"data:{mime_type};base64,{encoded}"
            message = (
                f"参考图上传接口暂不可用（{type(upload_error).__name__}），已改用 Base64 直传"
                if upload_error
                else "参考图上传响应未提供图片链接，已改用 Base64 直传"
            )
            print(f"{message}: {path.name}", flush=True)
        _REFERENCE_IMAGE_URLS[cache_key] = url
        if url.startswith("http://") or url.startswith("https://"):
            print(f"参考图已上传至第三方图像服务: {path.name}", flush=True)
        return url


if __name__ == "__main__":
    try:
        run_online_poster_engine()
    except Exception as exc:
        print(f"模块 4 失败: {exc}", file=sys.stderr, flush=True)
        raise
