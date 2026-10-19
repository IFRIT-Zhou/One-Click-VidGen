"""Bounded, transactional local shot splitting; never generates media."""
import copy
from .video_duration_limits import generation_duration
from .video_group_repair import repair_groups, validate_group_structure
from .video_plan import normalize_shots

MAX_REQUEST_SECONDS = 14


def split_window(record, shot_id, neighbors=False):
    rows = record['shots']
    index = next((i for i, row in enumerate(rows) if row['id'] == shot_id), -1)
    if index < 0 or rows[index]['kind'] != 'video':
        raise ValueError('请选择需要拆分的动态镜头')
    first, last = index, index + 1
    if neighbors:
        if index and rows[index-1]['kind'] == 'video':
            first -= 1
        if last < len(rows) and rows[last]['kind'] == 'video':
            last += 1
    window = rows[first:last]
    if any(row.get('manual_locked') for row in window) or any(row.get('boundary_locked') for row in window[:-1]):
        raise ValueError('所选范围包含已锁定的镜头边界，请解除锁定或选择仅拆分本镜')
    ids = [sid for row in window for sid in row['slide_ids']]
    scenes = [s for s in record['scenes'] if s['slide_id'] in ids]
    if [s['slide_id'] for s in scenes] != ids:
        raise ValueError('所选镜头的字幕范围不连续，请先检查时间轴')
    target = rows[index]
    by_id = {s['slide_id']: s for s in scenes}
    span = by_id[target['slide_ids'][-1]]['end'] - by_id[target['slide_ids'][0]]['start']
    if generation_duration(span) <= MAX_REQUEST_SECONDS:
        raise ValueError('本镜请求时长未超过14秒，无需自动拆分')
    for scene in scenes:
        if generation_duration(scene['end'] - scene['start']) > MAX_REQUEST_SECONDS:
            raise ValueError(f"字幕 {scene['slide_id']} 单条超过14秒，请先在配音与字幕中调整断句；原配音与分镜未修改")
    return first, last, scenes


def plan_split(record, shot_id, neighbors=False):
    from . import video_agents as agents
    first, last, scenes = split_window(record, shot_id, neighbors)
    window = record['shots'][first:last]
    context = copy.deepcopy(record.get('context') or {})
    context.update(dynamic_max_shot_duration=MAX_REQUEST_SECONDS,
                   video_generation_backend='cloud_h3')
    context['local_replan'] = dict(
        source_text='\n'.join(s['text'] for s in record['scenes']),
        instruction='仅重设计本次子镜，各自只表达本镜字幕；前后递进，不重复父镜的整段剧情、构图或动作。旧核心图不是本次设计依据。')
    # No parent images, prompts, intent, analysis or manual action are supplied.
    parent = dict(id=shot_id, kind='video', slide_ids=[s['slide_id'] for s in scenes],
                  boundary_locked=bool(window[-1].get('boundary_locked')))
    rows = repair_groups(context, scenes, [parent], agents.ask_json,
                         enforce_opening=False, repair_missing=False, coordinate_neighbors=False)
    refs = record.get('references', [])
    rows = normalize_shots(rows, scenes, [r['id'] for r in refs], max_duration=MAX_REQUEST_SECONDS)
    # Design siblings together, not independent copies of the old shot.
    def merge(updates, fields):
        if [r['id'] for r in updates] != [r['id'] for r in rows]:
            raise ValueError('局部重规划返回的镜头不完整，原分镜未修改')
        for row, update in zip(rows, updates):
            for field in fields:
                if field in update and update[field] is not None:
                    if field == 'semantic':
                        row.setdefault(field, {}).update(copy.deepcopy(update[field]))
                    elif field not in {'intent', 'progression_plan'} or str(update[field]).strip():
                        row[field] = copy.deepcopy(update[field])
    merge(agents.design_core_images(context, record['scenes'], rows, refs),
          ('visual_description','visual_design','reference_ids','semantic','intent','progression_plan'))
    merge(agents.direct_motion(context, rows, refs), ('action','motion_plan'))
    merge(agents.write_image_prompts(context, record['settings'].get('style',''), rows, refs),
          ('image_prompt','image_prompt_warnings'))
    merge(agents.write_video_prompts(context, rows, refs), ('video_prompt','video_prompt_warnings'))
    rows = normalize_shots(rows, scenes, [r['id'] for r in refs], max_duration=MAX_REQUEST_SECONDS)
    agents.audit_storyboard(rows, {r['id'] for r in refs}, parameters=context)
    for row in rows:
        if row['kind'] != 'video' or row['generation_duration'] > MAX_REQUEST_SECONDS:
            raise ValueError('拆分结果仍不满足14秒限制，原分镜未修改')
        source = next(old for old in window if row['slide_ids'][0] in old['slide_ids'])
        # Preserve generation route only; old scene binding may no longer fit.
        if 'video_generation_options' in source:
            row['video_generation_options'] = copy.deepcopy(source['video_generation_options'])
        row.update(image_status='pending', video_status='pending', design_needs_review=False,
                   design_review_confirmed=False, prompt_refresh_note='已按当前字幕重新规划，请检查新提示词后生成核心图。')
    return rows


def merge_split(current, snapshot, shot_id, neighbors, replacements):
    first, last, scenes = split_window(snapshot, shot_id, neighbors)
    old = snapshot['shots'][first:last]
    at = next((i for i, row in enumerate(current['shots']) if row['id'] == old[0]['id']), -1)
    # Unrelated clip completion can proceed; scope/settings/reference changes cannot.
    if at < 0 or current['shots'][at:at+len(old)] != old or any(
            current.get(key) != snapshot.get(key) for key in ('scenes','settings','references','context','creation_parameters')):
        raise ValueError('规划期间所选镜头或项目设定已改变，本次未覆盖，请重新尝试')
    validate_group_structure(replacements, scenes)
    result = current['shots'][:at] + replacements + current['shots'][at+len(old):]
    validate_group_structure(result, current['scenes'])
    return result
