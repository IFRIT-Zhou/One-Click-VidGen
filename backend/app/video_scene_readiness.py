"""Derived scene prerequisites: a feature switch is not a generation requirement."""
from pathlib import Path
from . import video_scene_references as scenes


def scene_readiness(record, path=None):
    shots = record.get('shots') or []
    assets = {a['id']: a for a in record.get('scene_assets') or []}
    blockers = []

    def block(code, message, scene_id=None, shot_ids=None):
        blockers.append({'code': code, 'message': message, 'scene_id': scene_id,
                         'shot_ids': shot_ids or [], 'action': 'retry_scene_references'})

    state = record.get('scene_references_status')
    if state in {'planning', 'generating', 'running', 'queued'}:
        block('SCENE_RUNNING', '场景参考正在准备，请等待完成。')
    for asset in assets.values():
        if (asset.get('image_task') or {}).get('status') in {'running', 'queued'}:
            block('SCENE_RUNNING', '场景参考图仍有运行中的生成任务。', asset['id'], asset.get('used_by'))
    if not scenes.enabled(record):
        return {'state': 'running' if blockers else 'not_applicable', 'can_confirm': not blockers, 'blockers': blockers}
    needed = {}
    for shot in shots:
        identity = shot.get('scene_reference_id')
        if not identity or shot.get('scene_reference_disabled') or assets.get(identity, {}).get('disabled'):
            continue
        needed.setdefault(identity, []).append(shot['id'])
    for entry in (record.get('scene_reference_plan') or {}).get('scenes', []):
        members = entry.get('members') or []
        valid = [shots[i] for i in members if isinstance(i, int) and 0 <= i < len(shots)]
        active = [s for s in valid if not s.get('scene_reference_disabled')]
        if valid and not active: continue
        matching = [a for a in assets.values() if entry.get('scene_id') is not None and
                    (a.get('scene_id') == entry['scene_id'] or a['id'] == entry['scene_id'])]
        if matching:
            for asset in matching:
                if not asset.get('disabled'): needed.setdefault(asset['id'], [s['id'] for s in active])
        elif not active or any(not s.get('scene_reference_id') for s in active):
            block('SCENE_PLAN_PENDING', '场景计划尚未准备为可用参考图：' + str(entry.get('name') or entry.get('scene_id') or '待准备场景'),
                  entry.get('scene_id'), [s['id'] for s in active])
    for identity, ids in needed.items():
        asset = assets.get(identity)
        label = str((asset or {}).get('name') or identity)
        if asset is None:
            block('SCENE_ASSET_MISSING', f'场景“{label}”缺少参考资产，请修复绑定或重试场景准备。', identity, ids)
        elif (asset.get('image_task') or {}).get('status') in {'running', 'queued'}:
            block('SCENE_RUNNING', f'场景“{label}”正在生成。', identity, ids)
        elif asset.get('image_status') != 'completed':
            block('SCENE_NOT_READY', f'场景“{label}”尚未生成成功，请重试场景参考。', identity, ids)
        elif path is not None:
            source = (Path(path) / str(asset.get('image') or '')).resolve()
            if not asset.get('image') or Path(path).resolve() not in source.parents or not source.is_file() or source.stat().st_size == 0:
                block('SCENE_FILE_MISSING', f'场景“{label}”参考图文件不可用，请替换或重试。', identity, ids)
    if not needed and not assets and state in {'failed', 'stopped', 'pending'} and not blockers:
        block('SCENE_PREPARATION_INCOMPLETE', '此前场景准备未完成，请检查原因后重试。')
    return {'state': ('running' if any(b['code'] == 'SCENE_RUNNING' for b in blockers) else 'blocked') if blockers else
            ('ready' if needed else 'not_applicable'), 'can_confirm': not blockers, 'blockers': blockers}
