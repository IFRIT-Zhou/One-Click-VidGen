"""Expression policy for dynamic-video directors, not a text-content filter.

Missing settings retain the original text-assisted prompts. This module only
selects instructions; it never rewrites source narration, attribution or plans.
"""
from __future__ import annotations

import re
from typing import Any

from .video_director_contracts import TEXT_CONTRACT


TEXT_ASSISTED = 'text_assisted'
VISUAL_FIRST = 'visual_first'


def normalize_text_mode(value: Any) -> str:
    """Read legacy/optional settings without silently switching their behavior."""
    return value if isinstance(value, str) and value in (TEXT_ASSISTED, VISUAL_FIRST) else TEXT_ASSISTED


def dynamic_text_mode(context: dict[str, Any]) -> str:
    direction = context.get('video_direction')
    return normalize_text_mode(direction.get('dynamic_text_mode')) if isinstance(direction, dict) else TEXT_ASSISTED


VISUAL_FIRST_CONTRACT = """【动态视频表达模式：画面优先·少字表达（visual_first）】
首先用可见动作、人物表情、姿态互动、具体物体与景物及空间/状态变化表达原文关系，
让观众通过画面理解，而不是读画中对白或口播解释。依据整段语义设计，不用关键词套固定隐喻。
这是少字模式，不是零字模式。先判断动作和图案能否清楚表达；能表达就不再叠加解释文字。
若一个简短反应词、关键评价或必要标注比纯图案更直接准确，可以主动选用，不需要等用户逐字授权；
但短句并不自动必要，保留的字应承担画面难以替代的信息，而不是重复观众已经看懂的动作。
不把完整口播、长篇对白或整段旁白解释排进对话气泡、想象气泡、说明框或标题中；
不要把同一段口播拆成多个短字气泡绕过这个要求，也不把带字气泡当默认表达工具。
按当前语义选择尽量少、尽量简短且准确的文字，不按固定字数、固定气泡数或关键词模板机械裁切。
若想象确实有助于表达，可以用少量有归属的图案气泡，内部写清实际要画的景观、场景或物体，
例如旅行想象画具体景物，不以目的地名称或整句旅行提问代替景物。也可以完全不用气泡；
无字、留白的气泡仅在有表达作用时可选，不强制预留空气泡。图案内容写在 visual_description、
reference_visual 或 beat.action 中，不能把景物说明文字当成图案内要显示的字。
保留 semantic.speech_turns 的 speaker、addressee 与 spoken/thought/quoted 等关系：
谁提问、谁回应、谁想象须通过人物动作、视线、表情或气泡指向保持清楚，不把别人的想法转给旁白主角。
speech_turns.source_text、source_subtitles、intent 和导演说明都是内部语义依据，不是画中文字指令；
其中的引号也不意味着要把原文逐字画出来。主观想象与引用仍是对应主体的观点，不伪装成现实事实。
画面优先不等于全面禁字。菜单项目、店招/路牌、物体标签、必要数字等，若确实属于当前场景且
对理解有用，可以放在合理的真实载体上，保持简短准确；不要为了禁字删去合理场景信息，
也不要为显示原文另造招牌或标签。用户明确要求的必要文字仍须尊重。
texts 与 reference_texts 只登记经过上述选择、实际需要显示的文字及 owner、container；无字填[]。
无字图案气泡不登记到 texts，内容与归属由画面/动作描述承接。需要显示的文字才用引号标明。
定稿与局部修订延续同一模式：保持主体、事件顺序、发言归属和已选图案，
保留已选短反应词、关键评价与必要场景文字的精确原文、owner 和 container，不能以少字为由自行删掉。
不重新添加未选用的带字对白或解释，不输出与已选文字冲突的“全面禁止文字”。修复格式或漏项也不得退回原文复制。
"""


def text_mode_contract(context: dict[str, Any]) -> str:
    """Return the original contract verbatim unless visual-first is selected."""
    return VISUAL_FIRST_CONTRACT if dynamic_text_mode(context) == VISUAL_FIRST else TEXT_CONTRACT


def approved_visible_texts(plan: Any, *, include_reference: bool = True) -> list[str]:
    """Return the exact visible strings selected by the structured director.

    Narration and subtitle text deliberately do not enter this list.  It is the
    small, authoritative handoff used by finalizers and model adapters.
    """
    if not isinstance(plan, dict):
        return []
    entries = []
    for beat in plan.get('beats') or []:
        if isinstance(beat, dict):
            entries.extend(beat.get('texts') or [])
    if include_reference:
        entries.extend(plan.get('reference_texts') or [])
    result = []
    for entry in entries:
        text = str(entry.get('text') or '').strip() if isinstance(entry, dict) else ''
        if text and text not in result:
            result.append(text)
    return result


def visual_first_plan_issues(plan: Any, *, inspect_reference: bool = True) -> list[str]:
    """Enforce an H3-friendly *small text budget* without becoming no-text mode."""
    if not isinstance(plan, dict):
        return []
    issues = []
    beats = plan.get('beats') or []
    all_texts = []
    for index, beat in enumerate(beats, 1):
        texts = beat.get('texts') or [] if isinstance(beat, dict) else []
        if len(texts) > 1:
            issues.append(f'第{index}阶段同时安排了{len(texts)}处文字；画面优先模式每阶段最多保留一处必要短字')
        all_texts.extend(texts)
    reference = plan.get('reference_texts') or []
    if inspect_reference and len(reference) > 2:
        issues.append(f'核心参考图安排了{len(reference)}处文字；应最多保留两处必要短字，其余改用动作或图案')
    if inspect_reference:
        all_texts.extend(reference)
    distinct = []
    for entry in all_texts:
        text = str(entry.get('text') or '').strip() if isinstance(entry, dict) else ''
        if not text:
            continue
        compact = re.sub(r'\s+', '', text)
        if len(compact) > 10 or (len(compact) > 5 and re.search(r'[，。！？；：,.!?;:]', compact)):
            issues.append(f'可见文字“{text}”过长或近似完整句；请改成图案、动作，或最多十个字的必要短词')
        if text not in distinct:
            distinct.append(text)
    if len(distinct) > 2:
        issues.append(f'本镜共安排了{len(distinct)}组不同文字；短视频动效应最多保留两组必要短词并依次出现')
    return list(dict.fromkeys(issues))


_VISIBLE_TEXT_INSTRUCTION = re.compile(
    r'(?P<context>.{0,28}(?:对话气泡|想象气泡|文字气泡|标签气泡|说明框|标题|字幕条|画面文字|'
    r'speech bubble|thought bubble|text bubble|callout|caption|subtitle|title|label)'
    r'.{0,18}(?:显示|写着|写有|出现|浮现|变为|内容为|shows?|displays?|reads?|contains?)?\s*)'
    r'[“「『\"](?P<text>[^”」』\"\n]{1,120})[”」』\"]'
, re.I)
_NEGATIVE_VISIBLE_TEXT = re.compile(
    r'不|无|禁止|避免|移除|消失|删去|清除|不要|不得|\b(?:no|not|without|remove|omit|avoid|disappear)\b', re.I)


def visual_first_prompt_issues(prompt: str, plan: Any) -> list[str]:
    """Find finalizers that reintroduced narration as visible dialogue."""
    allowed = set(approved_visible_texts(plan))
    issues = []
    for match in _VISIBLE_TEXT_INSTRUCTION.finditer(str(prompt or '')):
        text = match.group('text').strip()
        if text in allowed or _NEGATIVE_VISIBLE_TEXT.search(match.group('context')):
            continue
        issues.append(f'擅自新增未入选的画中文字“{text}”；应改用动作、表情或具体图案')
    return list(dict.fromkeys(issues))
