"""Subtitle-bound video storyboard contracts; no media submissions."""
import copy
import hashlib
import json
import math
import re
import uuid

from .video_motion_plan import normalize_motion_plan
from .video_director_contracts import VIDEO_DIRECTOR_REVISION
from .video_text_policy import normalize_text_mode
from .video_prompt_notices import has_prompt_warning


_DRAFT_FIELDS = ('id', 'slide_ids', 'kind', 'intent', 'action', 'image_prompt', 'video_prompt', 'visual_description')


def design_snapshot(shot):
    return {key: copy.deepcopy(shot[key]) for key in _DRAFT_FIELDS if key in shot}


def parse_srt(text):
    blocks = re.split(r"\n\s*\n", text.lstrip('\ufeff').replace('\r', '').strip())
    scenes = []
    stamp = r'(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})'
    def seconds(groups):
        h, m, s, ms = map(int, groups)
        if m > 59 or s > 59:
            raise ValueError('字幕时间格式无效')
        return h * 3600 + m * 60 + s + ms / 1000
    for block in blocks:
        lines = block.splitlines()
        index = next((i for i, line in enumerate(lines) if '-->' in line), -1)
        match = re.fullmatch(stamp + r'\s*-->\s*' + stamp, lines[index].strip()) if index >= 0 else None
        if not match:
            raise ValueError('请导入有效 SRT，包含开始与结束时间')
        start, end = seconds(match.groups()[:4]), seconds(match.groups()[4:])
        body = '\n'.join(lines[index + 1:]).strip()
        if not body or end <= start or (scenes and start < scenes[-1]['end'] - .001):
            raise ValueError('字幕不能为空、重叠或倒序；请先校对字幕')
        scenes.append(dict(slide_id=f'scene_{len(scenes)+1:03d}', start=start, end=end, text=body))
    if not scenes or len(scenes) > 1000:
        raise ValueError('第一轮支持 1～1000 条字幕')
    return scenes


def _normalize_speech_turns(value, source_text):
    """Optional semantic hints, never a new blocking gate for old/partial plans.

    Keep only bounded, source-anchored records. The original subtitles remain
    available to every director even if a provider omits/malforms these hints.
    """
    if not isinstance(value, list):
        return []
    def compact(text):
        return ''.join(char for char in text if char.isalnum()).casefold()
    source = compact(source_text)
    result = []
    limits = dict(source_text=2000, speaker=200, addressee=200, mode=40, basis=1200)
    for item in value[:32]:
        if not isinstance(item, dict) or any(not isinstance(item.get(key, ''), str)
                or len(item.get(key, '')) > limit for key, limit in limits.items()):
            continue
        turn = {key: item.get(key, '').strip() for key in limits}
        anchor = compact(turn['source_text'])
        if not anchor or anchor not in source:
            continue
        if turn['mode'] not in {'spoken', 'thought', 'quoted', 'narration', 'unknown'}:
            turn['mode'] = 'unknown'
        result.append(turn)
    return result


def normalize_shots(raw, scenes, reference_ids=()):
    if not isinstance(raw, list) or not raw:
        raise ValueError('镜头列表不能为空')
    ordered = [s['slide_id'] for s in scenes]
    by_id = {s['slide_id']: s for s in scenes}
    covered, result, identities = [], [], set()
    for source in raw:
        if not isinstance(source, dict):
            raise ValueError('镜头数据格式无效')
        row = copy.deepcopy(source)
        ids = row.get('slide_ids', [])
        if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or s not in by_id for s in ids):
            raise ValueError('镜头包含无效字幕')
        covered.extend(ids)
        identity = row.get('id') or uuid.uuid4().hex[:12]
        if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identity) or identity in identities:
            raise ValueError('镜头编号重复或无效')
        identities.add(identity)
        kind = row.get('kind', 'static')
        if kind not in ('static', 'video'):
            raise ValueError('镜头类型无效')
        start, end = by_id[ids[0]]['start'], by_id[ids[-1]]['end']
        duration = round(end - start, 3)
        warning = ''
        if duration > 15 and kind == 'video':
            kind = 'static'
            warning = '超过 15 秒，暂用静态画面；可在字幕边界拆分后改为动态。'
        refs = row.get('reference_ids', [])
        if not isinstance(refs, list) or len(refs) > 8 or any(not isinstance(r, str) for r in refs) or len(set(refs)) != len(refs) or any(r not in reference_ids for r in refs):
            raise ValueError('参考素材选择无效，最多 8 张，另预留 1 张核心分镜图')
        clean = dict(id=identity, slide_ids=ids, kind=kind, start=start, end=end,
                     duration=duration, generation_duration=max(4, math.ceil(duration)) if kind == 'video' else None,
                     reference_ids=refs, warning=warning)
        clean['design_needs_review'] = bool(row.get('design_needs_review', False))
        clean['reference_audio_enabled'] = bool(row.get('reference_audio_enabled', True))
        clean['reference_audio_lipsync'] = bool(row.get('reference_audio_lipsync', True))
        history = row.get('previous_designs', [])
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise ValueError('旧设计资料无效')
        clean['previous_designs'] = [design_snapshot(item) for item in history[-6:]]
        repair = row.get('duration_repair')
        if repair is not None:
            if not isinstance(repair, dict) or repair.get('method') not in {
                    'semantic', 'boundary_fallback', 'single_subtitle_static'}:
                raise ValueError('镜头时长修复资料无效')
            clean['duration_repair'] = copy.deepcopy(repair)
        if row.get('parent_shot_id'):
            parent = row['parent_shot_id']
            if not isinstance(parent, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', parent):
                raise ValueError('原镜头编号无效')
            clean['parent_shot_id'] = parent
        for field in ('intent', 'action', 'image_prompt', 'video_prompt', 'visual_description',
                      'motion_basis', 'progression_plan', 'planning_kind', 'kind_adjustment',
                      'attribution_correction'):
            value = row.get(field, '')
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError('镜头文案字段无效或过长')
            clean[field] = value.strip()
        for field in ('image_prompt_warnings', 'video_prompt_warnings'):
            warnings = row.get(field, [])
            if not isinstance(warnings, list) or len(warnings) > 200 or any(
                    not isinstance(item, str) or len(item) > 2000 for item in warnings):
                raise ValueError('提示词核对提示格式无效')
            clean[field] = list(warnings)
        for field, keys in (
            ('semantic', ('message', 'source_basis', 'fact_status', 'progression', 'continuity_requirement')),
            ('visual_design', ('candidates', 'selection_reason', 'expression', 'human_presence', 'visible_evidence')),
        ):
            value = row.get(field, {})
            if not isinstance(value, dict):
                raise ValueError('镜头设计资料格式无效')
            clean[field] = {}
            for key in keys:
                entry = value.get(key, [] if key == 'candidates' else '')
                if key == 'candidates':
                    if not isinstance(entry, list) or len(entry) > 2 or any(not isinstance(v, str) or len(v) > 3000 for v in entry):
                        raise ValueError('镜头候选方案无效')
                elif not isinstance(entry, str) or len(entry) > 10000:
                    raise ValueError('镜头设计资料无效或过长')
                clean[field][key] = entry
        if 'speech_turns' in row.get('semantic', {}):
            clean['semantic']['speech_turns'] = _normalize_speech_turns(
                row['semantic']['speech_turns'], ''.join(by_id[identity]['text'] for identity in ids))
        # Derive from authoritative subtitles; never trust stale/client-provided text.
        clean['source_subtitles'] = [dict(by_id[identity]) for identity in ids]
        if kind == 'video' and row.get('motion_plan') is not None:
            clean['motion_plan'] = normalize_motion_plan(row['motion_plan'])
        if kind == 'static' and not clean['design_needs_review']:
            clean['visual_description'] = clean['visual_description'] or clean['action']
            clean['action'] = ''
            clean['video_prompt'] = ''
            clean['video_prompt_warnings'] = []
        result.append(clean)
    if covered != ordered:
        raise ValueError('镜头必须按顺序完整覆盖全部字幕，不能重复或遗漏')
    return result


def edit_structure(shots, scenes, action, index, boundary=None, reference_ids=()):
    rows = copy.deepcopy(shots)
    def invalidate_phase_design(row):
        row.pop('motion_plan', None)
        row['design_needs_review'] = True
        row.pop('duration_repair', None)
        row['image_prompt_warnings'] = []
        row['video_prompt_warnings'] = []
        # Keep user-visible text as a draft. New subtitles must not silently use
        # an old phase plan; regeneration/explicit user confirmation resolves it.
        row['semantic'] = {}
        row['attribution_correction'] = ''
    def remember(row, original):
        row['previous_designs'] = (list(row.get('previous_designs', [])) + [design_snapshot(original)])[-6:]
    if index < 0 or index >= len(rows):
        raise ValueError('镜头不存在')
    if action == 'delete':
        if len(rows) == 1:
            raise ValueError('至少保留一个镜头')
        removed = rows.pop(index)
        if index:
            remember(rows[index - 1], shots[index - 1])
            remember(rows[index - 1], removed)
            rows[index - 1]['slide_ids'] += removed['slide_ids']
            invalidate_phase_design(rows[index - 1])
        else:
            remember(rows[0], shots[1])
            remember(rows[0], removed)
            rows[0]['slide_ids'] = removed['slide_ids'] + rows[0]['slide_ids']
            invalidate_phase_design(rows[0])
    elif action == 'boundary':
        if index + 1 >= len(rows):
            raise ValueError('没有后一个镜头')
        ids = rows[index]['slide_ids'] + rows[index + 1]['slide_ids']
        if type(boundary) is not int or not 0 < boundary < len(ids):
            raise ValueError('请选择相邻两镜之间的字幕边界，两边都必须保留字幕')
        if boundary == len(rows[index]['slide_ids']):
            return normalize_shots(rows, scenes, reference_ids)
        for position in (index, index + 1):
            remember(rows[position], shots[position])
            invalidate_phase_design(rows[position])
        rows[index]['slide_ids'], rows[index + 1]['slide_ids'] = ids[:boundary], ids[boundary:]
    elif action == 'merge':
        if index + 1 >= len(rows):
            raise ValueError('没有后一个镜头')
        remember(rows[index], shots[index])
        remember(rows[index], shots[index + 1])
        rows[index]['slide_ids'] += rows.pop(index + 1)['slide_ids']
        invalidate_phase_design(rows[index])
    elif action in ('split', 'insert'):
        ids = rows[index]['slide_ids']
        if not isinstance(boundary, int) or not 0 < boundary < len(ids):
            raise ValueError('请选择两条字幕之间的分界；单句请先拆分字幕')
        following = copy.deepcopy(rows[index])
        remember(rows[index], shots[index])
        remember(following, shots[index])
        following['id'] = uuid.uuid4().hex[:12]
        following['slide_ids'] = ids[boundary:]
        rows[index]['slide_ids'] = ids[:boundary]
        invalidate_phase_design(rows[index])
        invalidate_phase_design(following)
        if action == 'insert':
            following.update(kind='static', intent='', action='', image_prompt='', video_prompt='', reference_ids=[],
                             visual_description='', visual_design={}, semantic={}, motion_basis='', progression_plan='',
                             planning_kind='', kind_adjustment='')
        rows.insert(index + 1, following)
    else:
        raise ValueError('未知操作')
    return normalize_shots(rows, scenes, reference_ids)


def enforce_opening_motion(shots, count=2):
    """Keep the opening visually active when its subtitle timing can be generated."""
    result = copy.deepcopy(shots)
    for shot in result[:max(0, int(count))]:
        if float(shot.get('duration') or 0) <= 15:
            if shot['kind'] != 'video':
                shot['kind_adjustment'] = '开头留存规则：设为动态'
            shot['kind'] = 'video'
            shot['generation_duration'] = max(4, math.ceil(float(shot['duration'])))
            shot['warning'] = ''
    return result


def planning_fingerprint(scenes, style, characters, world, references, parameters=None):
    payload = dict(version=1, director_revision=VIDEO_DIRECTOR_REVISION, scenes=scenes,
                   style=style, characters=characters, world=world, references=references,
                   parameters=parameters or {})
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plan_storyboard(scenes, style, characters, world, references, progress, parameters=None, checkpoint=None,
                    *, resume_state=None, save_state=None, fixed_shots=None, repair_only=False):
    parameters = parameters or {}
    from story_agents import create_story_context
    from .video_agents import (audit_storyboard, design_core_images, direct_motion, plan_groups,
                               write_image_prompts, write_video_prompts)
    fingerprint = planning_fingerprint(scenes, style, characters, world, references, parameters)
    state = copy.deepcopy(resume_state) if isinstance(resume_state, dict) and (
        resume_state.get('fingerprint') == fingerprint) else {'fingerprint': fingerprint}
    recover_final_gate = str(state.get('stage') or '').startswith('最终校验待修订')
    if fixed_shots and not state.get('shots'):
        state['shots'] = normalize_shots(fixed_shots, scenes, [r['id'] for r in references])
        retained = [row['id'] for row in state['shots'] if not row.get('design_needs_review')]
        state['completed'] = {phase: retained[:] if repair_only else []
                              for phase in ('core', 'motion', 'image', 'video')}
        state['manual_groups'] = True
    def persist_state(stage):
        state['stage'] = stage
        if save_state:
            save_state(copy.deepcopy(state))
    context = state.get('context')
    if isinstance(context, dict):
        progress('复用已保存的全文理解，继续未完成规划。')
    else:
        progress('Agent 0：通读全文与整理人物资料')
        context = create_story_context('\n'.join(s['text'] for s in scenes),
                                       content_mode=parameters.get('content_mode', 'general'),
                                       global_character_prompt=characters, world_prompt=world, require_ai_success=True,
                                       director_strategy=parameters.get('director_strategy', 'stable'))
    context['video_direction'] = {
        'director_revision': VIDEO_DIRECTOR_REVISION,
        'director_strategy': parameters.get('director_strategy', 'stable'),
        'dynamic_text_mode': normalize_text_mode(parameters.get('dynamic_text_mode')),
        'user_characters': characters, 'user_world': world, 'visual_style': style,
        'reference_materials': references,
        'core_image_role': '单张核心图以前段场面为主，可容纳有归属的复合素材；视频按原文依次展开，不需照搬首帧',
    }
    state['context'] = context
    persist_state('全文理解')
    mode_label = '画面优先' if context['video_direction']['dynamic_text_mode'] == 'visual_first' else '文字辅助'
    progress(f'动态视频表达模式：{mode_label}')
    # Older final gates invalidated all completed phases for cosmetic headings.
    # Re-audit the saved results before paying to rebuild them. The fingerprint
    # above already ensures these results still match the current inputs.
    if recover_final_gate and state.get('shots'):
        try:
            recovered = normalize_shots(state['shots'], scenes, [r['id'] for r in references])
            recovered = audit_storyboard(recovered, {r['id'] for r in references})
        except ValueError:
            pass
        else:
            state['shots'] = recovered
            state['completed'] = {phase: [shot['id'] for shot in recovered
                if phase in ('core', 'image') or shot['kind'] == 'video']
                for phase in ('core', 'motion', 'image', 'video')}
            persist_state('提示词定稿已恢复')
            progress('已复核并恢复保存的完整提示词；标题排版差异不再阻断，无需重新调用 Agent。')
            return context, recovered
    arrangement = {key: parameters[key] for key in (
        'director_strategy', 'video_orientation') if key in parameters}
    arrangement['narration_groups'] = parameters.get('_narration_groups', [])
    if arrangement['narration_groups']:
        progress(f'已读取 {len(arrangement["narration_groups"])} 段与当前字幕全文一致的配音语义分组；分镜时长以字幕时间轴为准。')
    if state.get('shots'):
        shots = normalize_shots(state['shots'], scenes, [r['id'] for r in references])
        progress('复用已保存的镜头划分与设计，继续未完成步骤。')
    else:
        progress('Agent 1：按字幕边界规划动静镜头')
        def preserve_groups(rows):
            state['groups'] = copy.deepcopy(rows)
            persist_state('镜头分组初稿与局部修订')
        planned = plan_groups(context, scenes, arrangement, progress=progress,
                              on_draft=preserve_groups, resume_rows=state.get('groups'), boundary_review=True)
        for shot in planned:
            shot['planning_kind'] = shot['kind']
        shots = enforce_opening_motion(normalize_shots(planned, scenes))
    completed = state.setdefault('completed', {})
    def pending(batch, phase):
        done = completed.get(phase, [])
        return [shot for shot in batch if shot['id'] not in done]
    def mark_complete(batch, phase):
        completed[phase] = sorted(set(completed.get(phase, [])) | {shot['id'] for shot in batch})
    def preserve(stage, batch=None, offset=0):
        if batch is not None:
            shots[offset:offset+len(batch)] = batch
        state['shots'] = copy.deepcopy(shots)
        persist_state(stage)
        if checkpoint:
            checkpoint(copy.deepcopy(context), copy.deepcopy(shots), stage)
    preserve('镜头划分')
    for offset in range(0, len(shots), 6):
        batch = shots[offset:offset+6]
        needed = pending(batch, 'core')
        if needed:
            progress(f'Agent 2：设计前段核心场面与按需素材（单图 v2）{offset+1}～{offset+len(batch)}')
            updates = {row['id']: row for row in design_core_images(context, scenes, needed, references)}
            for shot in needed:
                update = updates[shot['id']]
                shot.update(action='', reference_ids=update.get('reference_ids', []),
                            visual_description=update.get('visual_description', ''),
                            visual_design=update.get('visual_design', {}))
                if shot.get('design_needs_review'):
                    shot['intent'] = update['intent']
                    shot['semantic'] = update.get('semantic', {})
                else:
                    # The core director verifies attribution even for an unchanged
                    # subtitle range. Do not discard that correction or replace
                    # the whole semantic record with a missing/partial response.
                    semantic = update.get('semantic')
                    if isinstance(semantic, dict) and 'speech_turns' in semantic:
                        shot.setdefault('semantic', {})['speech_turns'] = semantic['speech_turns']
                    correction = update.get('attribution_correction')
                    shot['attribution_correction'] = ''
                    if isinstance(correction, str) and correction.strip() and len(correction) <= 2000:
                        shot['attribution_correction'] = correction.strip()
                        for field in ('intent', 'progression_plan'):
                            value = update.get(field)
                            if isinstance(value, str) and value.strip() and len(value) <= 20000:
                                shot[field] = value.strip()
                        if isinstance(semantic, dict) and isinstance(semantic.get('source_basis'), str):
                            shot['semantic']['source_basis'] = semantic['source_basis']
                        progress(f"镜头 {shot['id']}：已核对发言归属；{correction[:500]}")
            # Validate material selection before either prompt Agent sees it.
            batch[:] = normalize_shots(batch, [scene for scene in scenes
                if scene['slide_id'] in {identity for shot in batch for identity in shot['slide_ids']}],
                [row['id'] for row in references])
            mark_complete(needed, 'core')
            preserve(f'核心画面设计 {offset+1}～{offset+len(batch)}', batch, offset)
        needed = pending([shot for shot in batch if shot['kind'] == 'video'], 'motion')
        if needed:
            progress(f'Agent 3：展开动作与反应、核对单图素材 {offset+1}～{offset+len(batch)}')
            updates = {row['id']: row for row in direct_motion(context, needed, references)}
            for shot in needed:
                update = updates[shot['id']]
                shot['motion_plan'] = normalize_motion_plan(update.get('motion_plan'))
                if not shot['motion_plan']:
                    raise ValueError(f"镜头 {shot['id']} 缺少动态阶段方案")
                shot['action'] = update['action']
                if update.get('handoff_notes'):
                    progress(f"镜头 {shot['id']}：核心画面草案存在文字交接差异，交由图像定稿整理并核对。")
            mark_complete(needed, 'motion')
            preserve(f'动态过程设计 {offset+1}～{offset+len(batch)}', batch, offset)
        def accept_prompts(rows, field, stage):
            updates = {row['id']: row for row in rows}
            for shot in batch:
                if shot['id'] in updates:
                    update = updates[shot['id']]
                    shot[field] = update[field]
                    shot[field + '_warnings'] = update.get(field + '_warnings', [])
            preserve(stage, batch, offset)
        needed = pending(batch, 'image')
        if needed:
            progress(f'Agent 4：定稿单张核心分镜图提示词 {offset+1}～{offset+len(batch)}')
            image_rows = write_image_prompts(context, style, needed, references,
                on_draft=lambda rows: accept_prompts(rows, 'image_prompt', f'图像提示词草案 {offset+1}～{offset+len(batch)}'))
            mark_complete(needed, 'image')
            accept_prompts(image_rows, 'image_prompt', f'图像提示词定稿 {offset+1}～{offset+len(batch)}')
            if any(has_prompt_warning(row.get('image_prompt_warnings')) for row in image_rows):
                progress('核心图提示词已保留；部分文字与阶段记录有差异，请在分镜确认时查看核对提示。')
        needed = pending([shot for shot in batch if shot['kind'] == 'video'], 'video')
        if needed:
            progress(f'Agent 5：定稿视频提示词 {offset+1}～{offset+len(batch)}')
            video_rows = write_video_prompts(context, needed, references,
                on_draft=lambda rows: accept_prompts(rows, 'video_prompt', f'视频提示词草案 {offset+1}～{offset+len(batch)}'))
            mark_complete(needed, 'video')
            accept_prompts(video_rows, 'video_prompt', f'视频提示词定稿 {offset+1}～{offset+len(batch)}')
            if any(has_prompt_warning(row.get('video_prompt_warnings')) for row in video_rows):
                progress('视频提示词已保留；部分文字与阶段记录有差异，请在分镜确认时查看核对提示。')
        for shot in batch:
            shot['design_needs_review'] = False
        preserve(f'提示词定稿 {offset+1}～{offset+len(batch)}', batch, offset)
    progress('程序校验：核对字幕覆盖、时长、图号与提示词合同')
    normalized = normalize_shots(shots, scenes, [r['id'] for r in references])
    reference_ids = {r['id'] for r in references}
    try:
        audited = audit_storyboard(normalized, reference_ids)
    except ValueError:
        # A completed API response can still fail the final contract. Retrying
        # must re-design those shots rather than repeatedly auditing the same data.
        failed = set()
        for shot in normalized:
            try:
                audit_storyboard([shot], reference_ids)
            except ValueError:
                failed.add(shot['id'])
        for phase, identities in completed.items():
            completed[phase] = [identity for identity in identities if identity not in failed]
        preserve('最终校验待修订：继续时仅重做未通过镜头')
        raise
    return context, audited
