"""Module 4: turn the semantic timeline into an image-backed HTML presentation."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import threading
import time
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
RUNNINGHUB_HOST = "https://www.runninghub.cn"
_QUEUE_RETRY_LOCK = threading.Lock()
VISUAL_PROMPT_AGENT_VERSION = 4


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
CONTENT_MODE_STORY = "urban_suspense"
CONTENT_MODE_SCIENCE = "science_explainer"
DEFAULT_GLOBAL_CHARACTER_PROMPT = (
    "主角：35岁憔悴中年女性，黑色长发；前期戴红色鸭舌帽、穿灰色旧衣服；"
    "后期骑行一段时间、购入装备后，精神焕发，穿白色骑行服并佩戴白色骑行头盔。"
)
SCIENCE_GLOBAL_CHARACTER_PROMPT = "固定讲解主角：黑色短发、红色围巾的可爱少女；简洁科教风服装，出场时造型保持一致。"


def normalize_content_mode(value: str | None) -> str:
    return CONTENT_MODE_SCIENCE if str(value or "").strip().lower() == CONTENT_MODE_SCIENCE else CONTENT_MODE_STORY


def build_visual_prompt_system(
    style: str = "",
    content_mode: str = CONTENT_MODE_STORY,
    global_character_prompt: str = "",
) -> str:
    content_mode = normalize_content_mode(content_mode)
    visual_style = style.strip() or (
        SCIENCE_VISUAL_STYLE if content_mode == CONTENT_MODE_SCIENCE else DEFAULT_VISUAL_STYLE
    )
    if content_mode == CONTENT_MODE_SCIENCE:
        return f"""你是科普科技口播视频的分镜视觉导演，也是本流水线的 Agent 2。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。

【分镜规则】
- 严格按照系统提供的固定 slide 分组，每组生成一张 2:1 横版解说漫画。
- 每张画面默认覆盖不超过 15 秒，不得合并、遗漏或重复 slide_id。
- 先理解该段要讲清的知识点、因果关系、案例或数据含义，再选择最直观的视觉表达。
- 优先采用生活化场景、实验演示、物体对比、过程示意和具象比喻；避免只画一个人在讲话。
- 忠于原文知识，不编造数据、实验结果、产品功能或科学结论。
- 手机、平板、书信或照片本身承载关键信息时，改用正面主体特写或插入镜头，让物件占据画面主体；只保留必要的手部或桌面边缘，不使用第一视角或越肩机位。

【统一风格】
- 默认风格为：{visual_style}
- 主角首次及再次出现时都必须保持黑色短发、红色围巾的少女形象一致。
- image_prompt 只写本组独有的知识场景、动作、构图、光线和色彩，不重复通用风格或固定画质句。

【画面要求】
- 根据 text_content、visual_summary 和 Agent 1 的全文知识结构设计画面。
- 每张图只突出一个核心知识点，主体明确、空间干净、信息层级清楚。
- 抽象概念要转成可见的物体、动作或对比；确需图表时只保留一个简单关系，不做密集 PPT。
- 不要生成复杂公式、长段文字、密集小字、字幕、水印、二维码、logo 或乱码。
- 如出现文字，整张画面总字数必须少于 20 个中文字符。

【固定画质要求】
- 系统会在每条 image_prompt 末尾统一加入：
  去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"""
    return f"""你是鬼故事与都市小说视频的惊悚漫画分镜导演。

【输出格式】
- 只输出严格 JSON 数组，不要 Markdown，不要解释。
- 每项必须包含 includes_slides（slide_id 数组）和 image_prompt（中文生图提示词）。

【分镜规则】
- 严格按照系统为本次任务提供的固定 slide 分组，每组生成一张 2:1 横版电影感漫画分镜。
- 每张画面默认覆盖不超过 15 秒，不能为了减少图片数量而合并相邻分组。
- 覆盖每一个 slide_id，不得遗漏或重复。
- 通读前后文，识别人物关系、地点、时间、关键道具和悬念线索，让相邻画面具有叙事连续性。
- 每组选择一个最有戏剧张力的具体瞬间，不要把抽象观点、旁白文字或多个时间点堆在同一画面。
- 忠于原文事实：原文没有鬼怪、凶案或暴力时，不得擅自添加，只用光影、构图和人物状态制造都市悬疑感。
- 手机、平板、书信或照片本身承载关键信息时，改用正面主体特写或插入镜头，让物件占据画面主体；只保留必要的手部或桌面边缘，不使用第一视角或越肩机位。

【统一风格】
- 默认风格为：{visual_style}
- 设计具体画面时必须遵守上述统一风格，但 image_prompt 只输出本组的具体场景、动作、构图、光线和色彩。
- 不要在 image_prompt 中重复统一风格或固定画质要求；系统会在提交生图前统一注入一次。
- 首次出现的人物要提炼可识别的外貌、年龄、发型、服装和标志性物件；人物再次出现时必须在 image_prompt 中直接写出这些具体特征，禁止只写姓名、关系称呼或“同一个人”。

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
  去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"""


DEFAULT_VISUAL_PROMPT_SYSTEM = build_visual_prompt_system(content_mode=CONTENT_MODE_STORY)
SCIENCE_VISUAL_PROMPT_SYSTEM = build_visual_prompt_system(content_mode=CONTENT_MODE_SCIENCE)


@dataclass(frozen=True)
class PosterTask:
    macro: dict[str, Any]
    output: Path
    task_id: str | None


class RunningHubQueueFull(RuntimeError):
    """The account has reached the cloud-side active task limit (error 421)."""


class RunningHubTransientError(RuntimeError):
    """A temporary RunningHub or network failure that can be retried safely."""


class RunningHubResultRetryableError(RuntimeError):
    """A cloud task completed with a transient result-file failure."""


class RunningHubModerationError(RunningHubResultRetryableError):
    """One image prompt was rejected and should be safely rewritten before resubmission."""


class RunningHubPowerInsufficient(RuntimeError):
    """The selected RunningHub WebApp has no remaining power-value quota."""


class RunningHubAccessDenied(RuntimeError):
    """The selected API key is not allowed to call the standard model endpoint."""


class RunningHubAllAccountsPowerInsufficient(RuntimeError):
    """Every configured RunningHub account returned power-value error 414."""


class RunningHubAllAccountsAccessDenied(RuntimeError):
    """Every configured RunningHub account returned access-denied error 1014."""


class RunningHubAllAccountsBusy(RuntimeError):
    """Every configured RunningHub account currently returned queue-full error 421."""


class RunningHubAccountPool:
    """Round-robin accounts, retire 414 accounts, and temporarily skip 421 accounts."""

    def __init__(self, configs: list[dict[str, str]]) -> None:
        self._configs = configs
        self._power_exhausted: set[str] = set()
        self._access_denied: set[str] = set()
        self._queue_full: set[str] = set()
        self._next_index = 0
        self._lock = threading.Lock()

    def acquire(self) -> dict[str, str]:
        with self._lock:
            available = [
                config
                for config in self._configs
                if config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
                and config["api_key"] not in self._queue_full
            ]
            if not available:
                usable = [
                    config
                    for config in self._configs
                    if config["api_key"] not in self._power_exhausted
                    and config["api_key"] not in self._access_denied
                ]
                if usable:
                    raise RunningHubAllAccountsBusy(
                        "所有可用 RunningHub 账号当前均处于队列满状态（421）"
                    )
                if self._access_denied:
                    raise RunningHubAllAccountsAccessDenied(
                        "所有已配置的 RunningHub 账号均返回访问拒绝（1014）"
                    )
                raise RunningHubAllAccountsPowerInsufficient(
                    "所有已配置的 RunningHub 账号均返回 power value 不足（414）"
                )
            config = available[self._next_index % len(available)]
            self._next_index = (self._next_index + 1) % len(available)
            return config

    def mark_power_exhausted(self, config: dict[str, str]) -> None:
        with self._lock:
            self._power_exhausted.add(config["api_key"])
            self._queue_full.discard(config["api_key"])

    def mark_access_denied(self, config: dict[str, str]) -> None:
        with self._lock:
            self._access_denied.add(config["api_key"])
            self._queue_full.discard(config["api_key"])

    def mark_queue_full(self, config: dict[str, str]) -> None:
        with self._lock:
            if (
                config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
            ):
                self._queue_full.add(config["api_key"])

    def mark_available(self, config: dict[str, str]) -> None:
        with self._lock:
            self._queue_full.discard(config["api_key"])

    def acquire_waiting_account(self) -> dict[str, str]:
        """Choose any non-414 account as the account whose queue will be observed."""
        with self._lock:
            available = [
                config
                for config in self._configs
                if config["api_key"] not in self._power_exhausted
                and config["api_key"] not in self._access_denied
            ]
            if not available:
                if self._access_denied:
                    raise RunningHubAllAccountsAccessDenied(
                        "所有已配置的 RunningHub 账号均返回访问拒绝（1014）"
                    )
                raise RunningHubAllAccountsPowerInsufficient(
                    "所有已配置的 RunningHub 账号均返回 power value 不足（414）"
                )
            config = available[self._next_index % len(available)]
            self._next_index = (self._next_index + 1) % len(available)
            return config


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
        missing.append("RUNNINGHUB_API_KEY（可追加 _2、_3 或 RUNNINGHUB_API_KEYS）")
    if not base_config["endpoint"]:
        missing.append("RUNNINGHUB_ENDPOINT")
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
        if current and (
            len(current) >= group_slides
            or candidate_duration > max_duration
            or (candidate_duration > group_target and current_duration >= min_duration)
            or (scene_pacing != group_pacing and current_duration >= min_duration)
        ):
            groups.append(current)
            current = []
        current.append(scene)
    if current:
        groups.append(current)

    # A last sentence or a pacing-boundary must not yield a 1--3 second image.
    # Prefer merging backwards, unless that would exceed the hard maximum and
    # the next group is a better fit.  A single short overall video is allowed.
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
) -> list[dict[str, Any]]:
    groups = _visual_groups(scenes, story_plan)
    science_mode = normalize_content_mode(os.getenv("CONTENT_MODE")) == CONTENT_MODE_SCIENCE
    return [
        {
            "macro_scene_id": f"poster_{index:03d}",
            "includes_slides": [str(scene["slide_id"]) for scene in group],
            "image_prompt": (
                (
                    "2:1 横版科教手绘解说漫画，单一知识焦点，使用生活化场景、物体对比或过程示意，"
                    f"具体讲清“{'；'.join(str(scene.get('visual_summary') or '') for scene in group)}”，"
                    "黑色短发、红色围巾的少女形象保持一致，知识准确、构图清楚，不做密集PPT、字幕或水印。"
                )
                if science_mode
                else (
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
            }
        )
    if not normalized or remaining:
        return None
    if required_groups is not None:
        actual_groups = [item["includes_slides"] for item in normalized]
        if actual_groups != required_groups:
            return None
    return normalized


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


def _style_protagonist_identity(style: str) -> str:
    """Extract an explicit user-authored protagonist lock from the style prompt."""
    match = re.search(r"主角\s*(?:为|是)\s*([^。；\n]+)", str(style or ""))
    return match.group(1).strip(" ，。；") if match else ""


def _character_descriptions(
    story_plan: dict[str, Any] | None,
    forced_style: str = "",
) -> list[tuple[str, str]]:
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
        role = str(character.get("role") or "")
        description = (
            protagonist_override
            if protagonist_override and "主角" in role
            else str(character.get("appearance") or "").strip(" ，。；")
        )
        if description:
            replacements.append((name, description))
    return sorted(replacements, key=lambda pair: len(pair[0]), reverse=True)


def _expand_character_names(
    prompt: str,
    story_plan: dict[str, Any] | None,
    forced_style: str = "",
) -> tuple[str, int]:
    expanded = str(prompt or "")
    count = 0
    for name, description in _character_descriptions(story_plan, forced_style):
        if name not in expanded:
            continue
        expanded, replacements = re.subn(re.escape(name), description, expanded)
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


def _character_continuity_block(
    original_prompt: str,
    story_plan: dict[str, Any] | None,
    forced_style: str,
    included_slides: list[str],
    scenes: list[dict[str, Any]],
) -> str:
    characters = (story_plan or {}).get("characters")
    if not isinstance(characters, list):
        return ""
    protagonist_override = _style_protagonist_identity(forced_style)
    lines: list[str] = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("name") or "").strip()
        role = str(character.get("role") or "").strip()
        explicitly_present = bool(name and name in original_prompt)
        generic_protagonist = (
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
        label = role or "人物"
        parts = [f"{label}：固定身份={identity}"] if identity else [label]
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
        lines.append("；".join(parts))
    if not lines:
        return ""
    return (
        "【本镜头角色造型硬约束：若正文有冲突，以本块为最高优先级；不得自行换装、摘帽或增加头饰】\n"
        + "\n".join(lines)
    )


def _plan_mapping_batch(
    scenes: list[dict[str, Any]],
    system_prompt: str,
    batch_label: str,
    story_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    required_groups = [
        [str(scene["slide_id"]) for scene in group]
        for group in _visual_groups(scenes, story_context)
    ]
    runtime_prompt = (
        system_prompt
        + "\n\n【Agent 1 提供的全文故事上下文】\n"
        + json.dumps(story_context or {}, ensure_ascii=False)
        + "\n必须把这份上下文视为跨批次共享的角色、地点、线索和连续性档案。"
        + "\n\n【本次任务的强制分组】\n"
        + json.dumps(required_groups, ensure_ascii=False)
        + "\n必须严格按上述顺序逐组输出：每组只生成一个对象，includes_slides 必须与对应分组完全一致，"
        "不得合并、拆分、遗漏或调整 slide_id。image_prompt 只写该组独有的具体画面内容，"
        "不要重复通用风格和固定画质句；但重复出场的角色必须使用角色的稳定姓名。"
        "必须根据当前 slide_id 选择 wardrobe_states 中唯一适用的一条造型，只写当前服装、当前头部状态和"
        "当前随身物品；严禁把‘前期/后期’、‘居家服或骑行服’等多个阶段同时写进一张图。"
        "用户画风中明确写出的主角年龄、发型、帽子等要求高于 Agent 1 的推断，不得改写。"
    )
    try:
        response = generate_gemini_text(
            system_prompt=runtime_prompt,
            user_prompt=json.dumps({"scenes": scenes, "required_groups": required_groups}, ensure_ascii=False),
            temperature=0.3,
            response_mime_type="application/json",
        )
        mapping = _normalize_mapping(parse_json_response(response), scenes, required_groups)
        if mapping:
            print(f"Gemini {batch_label} 已规划 {len(mapping)} 张海报。", flush=True)
            return mapping
        print(f"Gemini {batch_label} 返回的海报映射不完整。", flush=True)
    except (GeminiError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Gemini {batch_label} 规划失败: {exc}", flush=True)
    return None


def _finalize_mapping(
    mapping: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    story_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    forced_style = os.getenv("VISUAL_STYLE_PROMPT", "").strip()
    quality_requirement = "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
    clean_forced_style = _clean_style_for_image_prompt(forced_style, quality_requirement)
    expanded_character_names = 0
    for index, item in enumerate(mapping, 1):
        item["macro_scene_id"] = f"poster_{index:03d}"
        prompt = _apply_visual_safety_guard(str(item.get("image_prompt") or ""))
        if forced_style:
            prompt = prompt.replace(f"默认风格为：{forced_style}", "")
            prompt = prompt.replace(forced_style, "")
        if clean_forced_style:
            prompt = prompt.replace(clean_forced_style, "")
        for directive in STYLE_META_DIRECTIVES:
            prompt = prompt.replace(directive, "")
        prompt = prompt.replace(quality_requirement, "").strip(" \n，。")
        original_prompt = prompt
        continuity_block = _character_continuity_block(
            original_prompt,
            story_plan,
            forced_style,
            [str(value) for value in item.get("includes_slides", [])],
            scenes,
        )
        prompt, replacement_count = _expand_character_names(prompt, story_plan, forced_style)
        expanded_character_names += replacement_count
        if continuity_block:
            prompt = f"{continuity_block}\n{prompt}"
        if clean_forced_style:
            prompt = f"【统一画面风格】{clean_forced_style}\n{prompt}"
        prompt = f"{prompt}\n{quality_requirement}"
        item["image_prompt"] = prompt

    if expanded_character_names:
        print(f"角色实体展开已应用：共替换 {expanded_character_names} 处人物姓名或关系称呼。", flush=True)

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
    if not gemini_configured():
        print("Gemini 未配置，模块 4 使用本地分组提示词。", flush=True)
        return _finalize_mapping(_fallback_mapping(scenes, story_plan), scenes, story_plan)

    custom_prompt = os.getenv("VISUAL_PROMPT_SYSTEM", "").strip()
    system_prompt = custom_prompt or DEFAULT_VISUAL_PROMPT_SYSTEM
    story_context = story_context_for_prompt(story_plan or {})
    global_character_bible = os.getenv("GLOBAL_CHARACTER_PROMPT", "").strip()
    if global_character_bible:
        story_context["user_global_character_bible"] = global_character_bible
    protagonist_lock = _style_protagonist_identity(os.getenv("VISUAL_STYLE_PROMPT", ""))
    if protagonist_lock:
        story_context["user_protagonist_identity_lock"] = protagonist_lock
    prompt_source = "自定义" if custom_prompt else "默认"
    print(
        f"Agent 2：使用{prompt_source}画面提示词命令（{len(system_prompt)} 字），"
        f"已载入 Agent 1 全文上下文。",
        flush=True,
    )

    # Rich poster prompts make large one-shot responses easy to truncate.
    # Every bounded batch receives the exact same style instruction.
    batch_size = _positive_env_int("VISUAL_PROMPT_BATCH_SCENES", 28)
    batches = [scenes[index : index + batch_size] for index in range(0, len(scenes), batch_size)]
    if len(batches) > 1:
        print(
            f"画面规划共 {len(scenes)} 个字幕片段，拆为 {len(batches)} 批调用 Gemini，"
            "每批继承同一份画面提示词命令。",
            flush=True,
        )

    combined: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, 1):
        batch_label = f"画面规划批次 {index}/{len(batches)}"
        mapping = _plan_mapping_batch(batch, system_prompt, batch_label, story_context)
        if mapping is None:
            print(f"{batch_label} 已降级为本地分组提示词。", flush=True)
            mapping = _fallback_mapping(batch, story_plan)
        combined.extend(mapping)

    return _finalize_mapping(combined, scenes, story_plan)


def _request_json(session: requests.Session, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, url, timeout=60, **kwargs)
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
            f"{RUNNINGHUB_HOST}/uc/openapi/accountStatus",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json={"apikey": config["api_key"]},
        )
    except requests.RequestException as exc:
        raise RunningHubTransientError(
            f"RunningHub 队列状态查询网络异常: {type(exc).__name__}"
        ) from exc
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RunningHubTransientError("RunningHub 队列状态查询失败")
    try:
        return max(0, int(payload["data"].get("currentTaskCounts", 0)))
    except (TypeError, ValueError) as exc:
        raise RunningHubTransientError("RunningHub 返回了无效的活跃任务数") from exc


def _wait_for_queue_slot(poster_id: str, config: dict[str, str]) -> None:
    """Wait for a real RunningHub queue change after error 421."""
    max_wait = _positive_env_int("RUNNINGHUB_QUEUE_MAX_WAIT_SECONDS", 1800)
    poll_seconds = _queue_poll_seconds()
    probe_seconds = _queue_probe_seconds()
    deadline = time.monotonic() + max_wait
    blocked_task_count = _account_active_task_count(config)
    next_probe_at = time.monotonic() + probe_seconds
    print(
        f"{poster_id} 收到 421，已进入本地队列（当前 RunningHub 活跃任务 {blocked_task_count}）。",
        flush=True,
    )
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        active_tasks = _account_active_task_count(config)
        if active_tasks < blocked_task_count:
            print(
                f"{poster_id} 检测到 RunningHub 活跃任务下降（{blocked_task_count} -> {active_tasks}），准备重新提交。",
                flush=True,
            )
            return
        if time.monotonic() >= next_probe_at:
            print(f"{poster_id} 队列状态未变化，执行一次受控重新探测。", flush=True)
            return
        print(
            f"{poster_id} 仍在队列等待（RunningHub 活跃任务 {active_tasks}）。",
            flush=True,
        )
    raise RuntimeError(f"{poster_id} 等待 RunningHub 队列空位超时（{max_wait}s）")


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


def _find_image_url(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return None
    if isinstance(value, dict):
        for key in ("fileUrl", "fileURL", "imageUrl", "imageURL", "url", "downloadUrl", "downloadURL"):
            found = _find_image_url(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_image_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_image_url(item)
            if found:
                return found
    return None


def _runninghub_generate_url(config: dict[str, str]) -> str:
    endpoint = config["endpoint"]
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return f"{RUNNINGHUB_HOST}/openapi/v2/{endpoint.lstrip('/')}"


def _runninghub_headers(config: dict[str, str]) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
    }


def _handle_runninghub_submit_error(payload: dict[str, Any], status_code: int | None = None) -> None:
    code = _runninghub_error_code(payload, status_code)
    message = _runninghub_error_message(payload)
    if code == 421:
        raise RunningHubQueueFull("RunningHub 队列已满（421）")
    if code == 414:
        raise RunningHubPowerInsufficient(
            "当前 RunningHub 工作流的 power value 不足（414）。"
            "请为该工作流充值/补充算力值后再试。"
        )
    if code == 1014:
        detail = f" 原因: {message}" if message else ""
        raise RunningHubAccessDenied(
            "RunningHub 标准模型 API 只允许企业级-共享 API Key 调用。"
            "当前配置的 API Key 被拒绝（1014）。" + detail
        )
    if code in {408, 409, 429, 500, 502, 503, 504, 1005, 1010, 1011, 1012}:
        detail = f"，原因: {message}" if message else ""
        raise RunningHubTransientError(f"RunningHub 临时不可用，错误码: {code}{detail}")
    if _looks_like_moderation_failure(message):
        raise RunningHubModerationError(f"RunningHub 审核拦截: {message or code}")
    detail = f"，原因: {message}" if message else ""
    raise RunningHubTransientError(f"RunningHub 提交失败，错误码: {code}{detail}")


def _download_image(session: requests.Session, poster_id: str, file_url: str, output: Path) -> Path | None:
    try:
        response = session.get(str(file_url), timeout=120)
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
    response = session.post(
        _runninghub_generate_url(config),
        headers=_runninghub_headers(config),
        json=payload,
        timeout=60,
    )
    try:
        submitted = response.json()
    except ValueError as exc:
        response.raise_for_status()
        raise RunningHubTransientError("RunningHub 生成图接口返回了无效 JSON") from exc
    if not isinstance(submitted, dict):
        raise RunningHubTransientError("RunningHub 生成图接口返回了无效 JSON")
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
        raise RuntimeError("RunningHub 生成图接口未返回任务 ID")
    return task_id


def _poster_output_path(macro: dict[str, Any]) -> Path:
    poster_id = macro["macro_scene_id"]
    job_id = os.getenv("VOICE_OVER_VIDEO_JOB_ID", "").strip()
    suffix_source = f"{job_id}\0{macro.get('image_prompt', '')}"
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

    print(f"海报任务已提交: {progress_label} ({task_id})", flush=True)
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
                f"{RUNNINGHUB_HOST}/openapi/v2/query",
                headers=_runninghub_headers(config),
                json={"taskId": task.task_id},
            )
        except requests.RequestException as exc:
            print(f"{poster_id} 查询网络异常，稍后重试: {type(exc).__name__}", flush=True)
            time.sleep(5)
            continue
        status = str(_find_first_key(result, {"status", "state", "taskStatus"}) or "").upper()
        file_url = _find_image_url(result)
        if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED"} or file_url:
            if not file_url:
                raise RuntimeError(f"{poster_id} 未返回图像下载地址")
            downloaded = _download_image(session, poster_id, str(file_url), task.output)
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
            if error_code == 1516:
                raise RunningHubResultRetryableError(
                    f"{poster_id} 云端返图文件异常（1516）: {message or status}"
                )
            if _looks_like_moderation_failure(message):
                raise RunningHubModerationError(
                    f"{poster_id} 的提示词被云端审核拦截: {message or status}"
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
                    f"{poster_id} 的 {config['account_label']} 返回 421，"
                    f"切换到空闲的 {next_config['account_label']}。",
                    flush=True,
                )
                config = next_config
                queued = False
            continue
        except RunningHubPowerInsufficient:
            account_pool.mark_power_exhausted(config)
            print(
                f"{poster_id} 的 {config['account_label']} 返回 414，切换到下一个账号。",
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
                f"{poster_id} 的 {config['account_label']} 返回 1014，切换到下一个账号。",
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
    raise AssertionError("unreachable")


def render_posters_concurrently(
    mapping: list[dict[str, Any]], provider_configs: list[dict[str, str]]
) -> list[Path]:
    """Render with bounded local concurrency; RunningHub 421 responses enter the queue."""
    if not mapping:
        return []

    active_workers = _worker_count("RUNNINGHUB_ACTIVE_TASK_CONCURRENCY", 1, len(mapping))
    account_pool = RunningHubAccountPool(provider_configs)
    mapping = [
        {**macro, "progress_label": f"{macro['macro_scene_id']} ({index}/{len(mapping)})"}
        for index, macro in enumerate(mapping, 1)
    ]
    print(
        f"提交 {len(mapping)} 张海报任务（本地工作并发 {active_workers}，"
        f"{len(provider_configs)} 个账号可轮换，421 优先切账号后再入队）...",
        flush=True,
    )

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
            except RunningHubAllAccountsPowerInsufficient as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(
                    "所有已配置的 RunningHub 账号都返回 power value 不足（414），"
                    "已停止后续海报提交。请补充任一账号的工作流算力值后重新生成。"
                ) from exc
            except RunningHubAccessDenied as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(f"RunningHub 标准模型接口拒绝访问（1014）：{exc}") from exc
            except RunningHubAllAccountsAccessDenied as exc:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(
                    "所有已配置的 RunningHub 账号都返回访问拒绝（1014）。"
                    "已停止后续海报提交，请确认这些 key 在 RunningHub 后台属于企业级-共享 API Key。"
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
        included = [scenes_by_id[slide_id] for slide_id in macro["includes_slides"]]
        poster_timeline.append(
            {
                "start": min(float(scene["start"]) for scene in included),
                "end": max(float(scene["end"]) for scene in included),
                "url": f"./assets/{asset.name}",
            }
        )
    html_path = write_html(scenes, poster_timeline)
    print(f"模块 4 页面已写入: {html_path}", flush=True)


if __name__ == "__main__":
    try:
        run_online_poster_engine()
    except Exception as exc:
        print(f"模块 4 失败: {exc}", file=sys.stderr, flush=True)
        raise
