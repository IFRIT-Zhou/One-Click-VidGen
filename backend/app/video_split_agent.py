"""Agent 1C: bounded split coordination, separate from routine Agent 1B."""
import copy
from .gemini_client import GeminiError
from .video_group_repair import (legal_ranges, safe_partitions, _validate_children,
                                 _fallback_row, _decorate_children)
from .video_medical_director import medical_contract

ASSIGNMENT_FIELDS = ('role', 'focus', 'inherit', 'difference', 'continuity')


def split_direction(context, shots):
    if not context.get('local_split_plan') and not any(s.get('split_assignment') for s in shots):
        return ''
    return '''\n本次含 Agent 1C 拆分分工：按每镜 split_assignment 和 local_split_plan 协同设计。
core 子镜可以继承父镜核心含义、主体、构图或主要动作，不必为了差异而改掉核心表达。
support/continuation 子镜以自己 source_subtitles 的补充信息或后续阶段为中心，落实 difference，
不要复制另一子镜的整段事件再从头演一遍。同一事实可以保持主体、场景、风格乃至连续机位，
差异可以是信息、动作阶段、局部证据或关注点，不强制视觉反差。inherit 仅在当前字幕仍支持时使用。
父镜资料只作表达参考，不是要求复制旧提示词；不添加字幕没有的事件或结论。不改变字幕归属和时间戳。
'''


def parent_references(window):
    # Deliberately exclude paths, paid requests, historical snapshots and full prompts.
    fields = ('id', 'slide_ids', 'intent', 'user_intent', 'semantic', 'visual_description', 'visual_design',
              'action', 'reference_ids', 'progression_plan')
    return [{key: copy.deepcopy(row[key]) for key in fields if key in row} for row in window]


def _validate_assignments(rows, window):
    core_parents = set()
    focuses = set()
    for row in rows:
        assignment = row.get('split_assignment')
        if not isinstance(assignment, dict) or any(
                not isinstance(assignment.get(key), str) or not assignment[key].strip()
                or len(assignment[key]) > 4000 for key in ASSIGNMENT_FIELDS):
            raise ValueError('每个子镜须填写角色、字幕重点、继承范围、与同组其他子镜的分工及连续性')
        if assignment['role'] not in ('core', 'support', 'continuation'):
            raise ValueError('子镜角色只能为 core、support 或 continuation')
        parents = {old['id'] for old in window if set(old['slide_ids']) & set(row['slide_ids'])}
        if assignment['role'] == 'core':
            if core_parents & parents:
                raise ValueError('同一父镜不能让多个子镜都重复承担整段核心表达，请明确补充或后续分工')
            core_parents |= parents
        focus = ''.join(assignment['focus'].split())
        if focus in focuses:
            raise ValueError('子镜重点完全相同，请依据各自字幕区分信息或动作阶段；不必改变场景')
        focuses.add(focus)
    return rows


def plan_split_groups(context, scenes, window, ask, maximum):
    """One coordinated request (+ one repair); never touches audio or generates media."""
    parents = parent_references(window)
    system = '''你是仅用于局部拆分的 Agent 1C，不是常规规划的 Agent 1B。
按给定字幕边界和 legal_ranges 对 editable_parents 的范围重新分组；范围外不变。
时间由程序核算，不更改字幕文字、时间戳、字幕顺序；完整覆盖给定 scenes，每个字幕只出现一次。
所有子镜 kind=video，均不超过 max_request_seconds；尽量少拆，避免不必要的碎镜。
先联合分配各子镜职责，再交给后续导演：父镜核心内容可由一个核心子镜继承甚至重复原设计；
其余子镜围绕它们实际覆盖的补充说明、证据或后续阶段安排自己的画面，不重复核心子镜整段过程。
同一事实允许连续主体、场景和镜头语言，不强制不同构图或视觉反差；不得为了区别而编造事件。
split_assignment 中 role 为 core/support/continuation；focus 是本镜字幕重点，inherit 是可继承的父镜表达（无则写无），
difference 明确本镜相对同组其他镜头的信息或阶段分工，continuity 明确应延续的元素。
只输出分组、语义及分工，不写图片/视频成品提示词。用户原有意图须在当前字幕仍支持的范围内保留。
返回 {shots:[{slide_ids:[...],kind:"video",intent:"...",motion_basis:"...",progression_plan:"...",
semantic:{message:"...",source_basis:"原文依据",fact_status:"fact|quoted|question|hypothetical|metaphorical",progression:"...",continuity_requirement:"..."},
split_assignment:{role:"core|support|continuation",focus:"...",inherit:"...",difference:"...",continuity:"..."}}]}。
'''+medical_contract(context, 'groups')
    payload = dict(source_text=context['final_narration_script'], scenes=scenes,
                   editable_parents=parents, max_request_seconds=maximum,
                   legal_ranges=legal_ranges(scenes, maximum),
                   story_context={key: copy.deepcopy(context[key]) for key in
                                  ('video_direction', 'story_world', 'characters', 'theme') if key in context})
    candidate, method = None, 'semantic'
    for _ in range(2):
        response = None
        try:
            response = ask(system, payload)
            candidate = _validate_assignments(_validate_children(response, scenes, True, maximum), window)
            break
        except (GeminiError, ValueError, TypeError) as exc:
            payload['validation_errors'] = [str(exc)]
            if isinstance(response, dict):
                payload['previous_shots'] = response.get('shots')
    if candidate is None:
        # Honest fallback: authoritative subtitle partitions, not fabricated Agent output.
        method = 'boundary_fallback'
        candidate = []
        parts = safe_partitions(scenes, maximum)
        core_index = max(range(len(parts)), key=lambda i: len(''.join(s['text'] for s in parts[i])))
        for index, part in enumerate(parts):
            parent = next(old for old in window if part[0]['slide_id'] in old['slide_ids'])
            row = _fallback_row(part, parent, True, maximum)
            row['split_assignment'] = dict(role='core' if index == core_index else 'continuation',
                focus=''.join(s['text'] for s in part)[:4000],
                inherit='仅继承当前字幕仍支持的父镜主体、风格和表达；后续导演选择具体继承内容',
                difference=f'本组第 {index+1}/{len(parts)} 镜，只展开自身字幕的信息或阶段，不重演其他子镜整段过程',
                continuity='相同事实延续主体与场景，以本镜字幕的关注点或动作进展区分，不强制换场景')
            candidate.append(row)
    parent = dict(id=window[0]['id'], slide_ids=[s['slide_id'] for s in scenes],
                  boundary_locked=bool(window[-1].get('boundary_locked')))
    rows = _decorate_children(candidate, parent, {s['slide_id']: s for s in scenes}, method,
                              f'Agent 1C 局部拆分，目标请求不超过 {maximum} 秒', maximum)
    for row, source in zip(rows, candidate):
        row['split_assignment'] = {key: source['split_assignment'][key] for key in ASSIGNMENT_FIELDS}
        row['split_assignment']['source_parent_ids'] = [old['id'] for old in window if set(old['slide_ids']) & set(row['slide_ids'])]
        row['parent_shot_id'] = row['split_assignment']['source_parent_ids'][0]
        row['duration_repair']['parent_id'] = row['parent_shot_id']
        row['duration_repair']['planner'] = '1C'
    return rows, parents
