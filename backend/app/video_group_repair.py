"""Local semantic repair of subtitle-bound video groups, without media calls."""
from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Callable

from .gemini_client import GeminiError
from .video_director_contracts import SEMANTIC_CONTRACT


MAX_VIDEO_DURATION = 15.0
_SEMANTIC_KEYS = ('message', 'source_basis', 'fact_status', 'progression', 'continuity_requirement')
_FACT_STATUS = {'fact', 'hypothetical', 'metaphorical', 'quoted', 'question'}


def validate_group_structure(rows, scenes):
    """Validate coverage without normalizing overlong video groups into stills."""
    if not isinstance(rows, list) or not rows:
        raise ValueError('镜头列表不能为空')
    ordered = [scene['slide_id'] for scene in scenes]
    available, covered, identities = set(ordered), [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('镜头数据格式无效')
        ids = row.get('slide_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or v not in available for v in ids):
            raise ValueError('镜头包含无效字幕')
        if row.get('kind') not in ('video', 'static'):
            raise ValueError('镜头类型无效')
        identity = row.get('id')
        if identity is not None:
            if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identity) or identity in identities:
                raise ValueError('镜头编号重复或无效')
            identities.add(identity)
        covered.extend(ids)
    if covered != ordered:
        raise ValueError('镜头必须按顺序完整覆盖全部字幕，不能重复或遗漏')


def _duration(scenes):
    # Match the authoritative normalized timeline, including inter-caption gaps.
    return round(float(scenes[-1]['end']) - float(scenes[0]['start']), 3)


def legal_ranges(scenes):
    """All permitted consecutive ranges; the Agent does not calculate timing."""
    result = []
    compact = len(scenes) > 40
    furthest = 0
    for first, scene in enumerate(scenes):
        if compact:
            furthest = max(first, furthest)
            while furthest + 1 < len(scenes) and round(float(scenes[furthest + 1]['end']) - float(scene['start']), 3) <= MAX_VIDEO_DURATION:
                furthest += 1
            own_duration = _duration([scene])
            end = first if own_duration > MAX_VIDEO_DURATION else furthest
            result.append(dict(first_slide_id=scene['slide_id'], last_slide_id=scenes[end]['slide_id'],
                               duration=round(float(scenes[end]['end']) - float(scene['start']), 3),
                               required_kind='static' if own_duration > MAX_VIDEO_DURATION else 'video',
                               range_mode='single_only' if own_duration > MAX_VIDEO_DURATION else 'any_end_up_to_last'))
            continue
        for last in range(first, len(scenes)):
            duration = round(float(scenes[last]['end']) - float(scene['start']), 3)
            if duration > MAX_VIDEO_DURATION:
                if first == last:
                    result.append(dict(first_slide_id=scene['slide_id'], last_slide_id=scene['slide_id'],
                                       duration=duration, required_kind='static'))
                break
            result.append(dict(first_slide_id=scene['slide_id'], last_slide_id=scenes[last]['slide_id'],
                               duration=duration, required_kind='video'))
    return result


def safe_partitions(scenes):
    """Minimize tiny clips/count, avoid unfinished punctuation, then balance lengths."""
    count = len(scenes)
    best = [None] * (count + 1)
    next_index = [count] * count
    best[count] = (0, 0, 0, 0.0)
    for first in range(count - 1, -1, -1):
        own_duration = _duration(scenes[first:first + 1])
        if own_duration > MAX_VIDEO_DURATION:
            best[first] = best[first + 1]
            next_index[first] = first + 1
            continue
        for last in range(first, count):
            duration = round(float(scenes[last]['end']) - float(scenes[first]['start']), 3)
            if duration > MAX_VIDEO_DURATION:
                break
            tail_cost = best[last + 1]
            ending = str(scenes[last].get('text') or '').rstrip().rstrip('\"\'”’」』）)]】').rstrip()
            weak_boundary = last + 1 < count and ending.endswith(('：', ':', '，', ',', '、', '；', ';'))
            cost = (tail_cost[0] + int(duration < 4), tail_cost[1] + 1,
                    tail_cost[2] + int(weak_boundary), tail_cost[3] + duration * duration)
            if best[first] is None or cost < best[first]:
                best[first], next_index[first] = cost, last + 1
    result, start = [], 0
    while start < count:
        result.append(scenes[start:next_index[start]])
        start = next_index[start]
    return result


def _missing_fields(row):
    fields = ['intent', 'motion_basis'] + (['progression_plan'] if row['kind'] == 'video' else [])
    issues = [field for field in fields if not isinstance(row.get(field), str)
              or not row[field].strip() or len(row[field]) > 20000]
    if row.get('semantic') is not None and (not isinstance(row['semantic'], dict) or any(
            not isinstance(row['semantic'].get(key, ''), str) or len(row['semantic'].get(key, '')) > 10000
            for key in _SEMANTIC_KEYS)):
        issues.append('semantic')
    return issues


def _semantic_child(row):
    semantic = row.get('semantic')
    if not isinstance(semantic, dict) or any(not isinstance(semantic.get(key, ''), str)
                                             or len(semantic.get(key, '')) > 10000 for key in _SEMANTIC_KEYS):
        raise ValueError('子镜缺少有效语义信息')
    if any(not semantic.get(key, '').strip() for key in ('message', 'source_basis', 'fact_status')):
        raise ValueError('子镜缺少表达含义、原文依据或事实属性')
    if semantic['fact_status'].strip() not in _FACT_STATUS:
        raise ValueError('子镜事实属性无效')


def _validate_children(response, scenes, dynamic):
    if not isinstance(response, dict):
        raise ValueError('局部分段未返回 JSON 对象')
    rows = response.get('shots')
    validate_group_structure(rows, scenes)
    by_id = {scene['slide_id']: scene for scene in scenes}
    for row in rows:
        local = [by_id[value] for value in row['slide_ids']]
        duration = _duration(local)
        unavoidable_static = len(local) == 1 and duration > MAX_VIDEO_DURATION
        if duration > MAX_VIDEO_DURATION and not unavoidable_static:
            raise ValueError('子镜仍超过15秒，必须沿给定字幕边界继续拆分')
        required_kind = 'static' if unavoidable_static or not dynamic else 'video'
        if row['kind'] != required_kind:
            raise ValueError('不能把可拆分的动态内容降为静态，单条超限字幕才单独静态')
        if _missing_fields(row):
            raise ValueError('子镜缺少表达目的、动静依据或动态递进关系')
        if any(len(row.get(field, '')) > 20000 for field in ('intent', 'motion_basis', 'progression_plan')):
            raise ValueError('子镜文案过长')
        _semantic_child(row)
    return rows


def _fallback_row(scenes, parent, dynamic):
    text = ''.join(str(scene['text']) for scene in scenes)
    static = not dynamic or (len(scenes) == 1 and _duration(scenes) > MAX_VIDEO_DURATION)
    old_semantic = parent.get('semantic') if isinstance(parent.get('semantic'), dict) else {}
    status = old_semantic.get('fact_status', '')
    # Keep a known factual/quoted framing, but never copy the parent's whole meaning.
    if status not in _FACT_STATUS:
        status = 'quoted'
    return dict(slide_ids=[scene['slide_id'] for scene in scenes], kind='static' if static else 'video',
                intent='呈现本段字幕内容：' + text[:18000],
                motion_basis='单条字幕超出视频时长上限，保留为静态' if static and dynamic else
                    ('沿用本段动态表达，按当前字幕设计过程' if dynamic else '本段用静态画面表达'),
                progression_plan='' if static else '承接前段，依照本镜字幕的先后关系展开：' + text[:17000],
                semantic=dict(message=text[:10000], source_basis=text[:10000], fact_status=status,
                              progression='本镜只表达所覆盖字幕的内容',
                              continuity_requirement=str(old_semantic.get('continuity_requirement') or '')[:10000]))


def _decorate_children(children, parent, by_id, method, reason):
    result = []
    for index, child in enumerate(children):
        # Only grouping fields survive: pictures, motion plans and old assets are invalid after a split.
        row = {key: copy.deepcopy(child.get(key, {} if key == 'semantic' else '')) for key in
               ('slide_ids', 'kind', 'intent', 'motion_basis', 'progression_plan', 'semantic')}
        row['id'] = uuid.uuid4().hex[:12]
        row['parent_shot_id'] = parent['id']
        local = [by_id[value] for value in row['slide_ids']]
        own_static = len(local) == 1 and _duration(local) > MAX_VIDEO_DURATION
        actual_method = 'single_subtitle_static' if own_static else method
        row['duration_repair'] = dict(method=actual_method, parent_id=parent['id'],
                                     original_duration=_duration([by_id[value] for value in parent['slide_ids']]),
                                     part_index=index + 1, part_count=len(children), reason=reason)
        if own_static:
            row['kind_adjustment'] = '单条字幕超过15秒，已独立保留为静态；配音和字幕不变'
        elif method == 'boundary_fallback':
            row['kind_adjustment'] = '已按字幕边界安全拆分；可在分镜确认时调整表达'
        else:
            row['kind_adjustment'] = '已按语义沿字幕边界拆分' if len(children) > 1 else '已补全本镜规划信息'
        row['duration_repair']['note'] = row['kind_adjustment']
        result.append(row)
    return result


def repair_groups(context, scenes, rows, ask: Callable, *, progress=None, on_draft=None):
    """Repair only invalid parent groups, preserving all other groups and their order."""
    validate_group_structure(rows, scenes)
    result = copy.deepcopy(rows)
    for row in result:
        if not row.get('id'):
            row['id'] = uuid.uuid4().hex[:12]
    by_id = {scene['slide_id']: scene for scene in scenes}
    if on_draft:
        on_draft(copy.deepcopy(result))
    index = 0
    while index < len(result):
        parent = result[index]
        local = [by_id[value] for value in parent['slide_ids']]
        duration = _duration(local)
        dynamic = parent['kind'] == 'video' or index < 2
        own_static = len(local) == 1 and duration > MAX_VIDEO_DURATION
        missing = _missing_fields(parent)
        overlong = dynamic and duration > MAX_VIDEO_DURATION
        # A saved, explained single-caption exception needs no repeated repair on resume.
        repair = parent.get('duration_repair')
        if own_static and parent['kind'] == 'static' and not missing and isinstance(repair, dict) and repair.get('method') == 'single_subtitle_static':
            index += 1
            continue
        needs_dynamic = dynamic and parent['kind'] != 'video' and not own_static
        if not overlong and not missing and not needs_dynamic:
            index += 1
            continue
        reason = (f'本镜跨度{duration:.3f}秒，需要沿字幕边界拆分' if overlong else
                  '；'.join((['开头镜头需要动态表达'] if needs_dynamic else []) +
                           (['缺少规划字段：' + '、'.join(missing)] if missing else [])))
        if progress:
            progress(f'分镜时长协调：处理第{index + 1}镜（{duration:.2f}秒）')
        method, children = 'boundary_fallback', None
        if own_static:
            children = [_fallback_row(local, parent, dynamic=True)]
        elif not dynamic:
            # Static metadata is safe to reconstruct directly from its own source text.
            children = [_fallback_row(local, parent, dynamic=False)]
        else:
            system = """你是分镜时长协调 Agent 1B，只修订指定的一个父镜头，不重新导演全文。
依据原文含义、问答关系、补充说明及转折，沿提供的字幕边界分成连续子镜；每个动态子镜不超过15秒。
legal_ranges 已由程序计算，必须使用其中合法范围，不能改写字幕时间；range_mode 为 any_end_up_to_last
时表示该起点到 last_slide_id 之间的任一字幕结束处均合法，single_only 则只能独立保留该字幕。
所有 parent_shot.slide_ids 按原顺序恰好覆盖一次，不加入邻镜字幕，不改变其他镜头。
保留动态表达；仅单条字幕自己超15秒时将该条独立为static，绝不能把可拆分的多句整段降为static。
优先完整语义与问答关系，尽量避免不足4秒的碎段，不机械按每条字幕拆镜。
为每个子镜分别重写 intent、semantic、motion_basis、progression_plan，只表达该子镜覆盖的原文。
不复制父镜全文的目的，不写构图、提示词或秒级动作。保持引用、假设、比喻和事实的原有属性。
返回 {shots:[{slide_ids:[...],kind:"video|static",intent:"...",semantic:{message,source_basis,fact_status,progression,continuity_requirement},motion_basis:"...",progression_plan:"..."}]}。
""" + SEMANTIC_CONTRACT
            payload = dict(story_context=context, parent_shot=copy.deepcopy(parent), scenes=local,
                           legal_ranges=legal_ranges(local), validation_errors=[reason],
                           neighbors={side: {key: candidate.get(key, '') for key in ('intent', 'semantic')}
                                      for side, candidate in (('previous', result[index - 1] if index else {}),
                                                              ('following', result[index + 1] if index + 1 < len(result) else {}))})
            for attempt in range(2):
                if progress:
                    progress(f'Agent 1B：第{index + 1}镜语义二次分段' + ('（定点修订）' if attempt else ''))
                response = None
                try:
                    response = ask(system, payload)
                    candidate = _validate_children(response, local, dynamic=True)
                except (GeminiError, ValueError, TypeError) as exc:
                    payload['validation_errors'] = [str(exc)]
                    if isinstance(response, dict):
                        payload['previous_shots'] = response.get('shots')
                    candidate = None
                if candidate is not None:
                    children, method = candidate, 'semantic'
                    break
            if children is None:
                children = [_fallback_row(part, parent, dynamic=True) for part in safe_partitions(local)]
                if progress:
                    progress(f'第{index + 1}镜已按字幕边界安全拆分，继续规划；可在分镜确认时调整表达。')
        replacements = _decorate_children(children, parent, by_id, method, reason)
        result[index:index + 1] = replacements
        validate_group_structure(result, scenes)
        if on_draft:
            on_draft(copy.deepcopy(result))
        index += len(replacements)
    return result
