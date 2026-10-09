"""Explicitly confirmed bridge image generation through the native redraw pipeline."""
import copy
import hashlib
import uuid
from pathlib import Path
from fastapi import HTTPException, Request
from pydantic import Field
from typing import Literal
from . import codex_bridge as bridge


class ImageGenerate(bridge.StrictModel):
    revision: int = Field(ge=1)
    timeline_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    audio_confirmed: Literal[True]
    shot_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    confirmed: bool = False
    confirmation_token: str = ''


def execute(identity, data, request, commit):
    user = bridge.user_for(request)
    with bridge.studio.LOCK:
        path = bridge.studio.directory(user['id'], identity)
        record = bridge.read_record(path)
        payload = data.model_dump(exclude={'confirmed', 'confirmation_token'})
        receipt = bridge.digest(payload)
        previous = record.get('codex_bridge', {}).get('image_requests', {}).get(data.request_id)
        if previous:
            if previous['receipt'] != receipt:
                raise HTTPException(409, 'request_id 已用于不同请求')
            return {**previous['result'], 'already_submitted': True}
        bridge.check_edit_context(record, path, data, user['id'])
        if record['status'] != 'image_review':
            raise HTTPException(409, '请先返回核心分镜图编辑阶段')
        shot = bridge.studio._find_shot(record, data.shot_id)
        if (shot.get('image_task') or {}).get('status') == 'running':
            raise HTTPException(409, '本镜正在重绘，请查询状态，不要重复提交')
        selection = shot.get('redraw_selection') or {}
        ids = list(selection.get('reference_ids', shot.get('reference_ids', [])))
        native = bridge.studio.StoryboardRedraw(revision=record['revision'], prompt=shot['image_prompt'],
            reference_ids=ids, use_current_image=selection.get('use_current_image', False),
            use_scene_reference=selection.get('use_scene_reference', True))
        rows = {str(r['id']): r for r in [*record.get('references', []), *record.get('redraw_references', []),
                                        *bridge.studio._scene_redraw_references(record)]}
        if len(set(ids)) != len(ids) or any(i not in rows for i in ids):
            raise HTTPException(400, '参考图选择无效，请先修正绑定')
        refs = []
        if native.use_current_image:
            refs.append(str(bridge.studio._storyboard_image_path(path, shot).resolve()))
        for i in ids:
            source = (path / rows[i]['file']).resolve()
            if path.resolve() not in source.parents or not source.is_file():
                raise HTTPException(400, '参考图文件缺失')
            refs.append(str(source))
        prompt, refs, _ = bridge.studio._image_inputs(path, record, shot, native.prompt,
                                                     references=refs, use_scene=native.use_scene_reference)
        if len(ids) + int(native.use_current_image) > 3:
            raise HTTPException(400, '手动重绘参考图超过3张，请调整选择')
        configs = bridge.studio._image_configs(record, user['id'])
        token = bridge.digest({'payload': payload, 'record': record, 'prompt': prompt,
                               'references': [(p, hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in refs],
                               'configs': configs})
        result = {'ok': True, 'project_id': identity, 'shot_id': data.shot_id,
                  'revision': record['revision'], 'confirmation_token': token,
                  'reference_ids': ids, 'actual_reference_count': len(refs),
                  'may_charge': True, 'generation_started': False}
        if not commit:
            return result
        if not data.confirmed or data.confirmation_token != token:
            raise HTTPException(409, '请预检并确认可能产生图片费用，再携带原 confirmation_token 提交')
        backup_id = 'image-' + str(record['revision']) + '-' + uuid.uuid4().hex
        bridge.studio.save(path / 'codex_bridge_backups' / backup_id, copy.deepcopy(record))
        result.update(generation_started=True, backup_id=backup_id, revision=record['revision'] + 1)
        record.setdefault('codex_bridge', {}).setdefault('image_requests', {})[data.request_id] = {
            'receipt': receipt, 'result': result}
        bridge.studio.save(path, record)
        try:
            generated = bridge.studio.redraw_storyboard_image(identity, data.shot_id, native, request)
        except Exception as exc:
            # Keep the submission receipt: a launch failure must not cause a paid retry.
            raise HTTPException(500, '重绘提交异常，请先查询 image-status，勿新建请求重复提交') from exc
        result['revision'] = generated['revision']
        return result


def register(router):
    @router.post('/projects/{identity}/image-validate')
    def validate(identity: str, data: ImageGenerate, request: Request):
        return execute(identity, data, request, False)

    @router.post('/projects/{identity}/image-apply')
    def apply(identity: str, data: ImageGenerate, request: Request):
        return execute(identity, data, request, True)

    @router.get('/projects/{identity}/image-status')
    def status(identity: str, request: Request):
        user = bridge.user_for(request)
        record = bridge.read_record(bridge.studio.directory(user['id'], identity))
        return {'revision': record['revision'], 'shots': [
            {k: s.get(k) for k in ('id', 'image_status', 'image_task', 'image_error')}
            for s in record.get('shots', [])]}
