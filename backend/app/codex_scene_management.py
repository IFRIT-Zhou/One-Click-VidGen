"""Authenticated, non-generating scene asset edits for the production bridge."""
import copy
import io
import uuid
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field, model_validator

from . import codex_bridge as bridge


class SceneEdit(bridge.StrictModel):
    revision: int = Field(ge=1)
    timeline_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    audio_confirmed: Literal[True]
    action: Literal['disable', 'unbind', 'replace', 'restore']
    scene_id: str = Field(default='', max_length=100)
    shot_ids: list[str] = Field(default_factory=list, max_length=500)
    upload_id: str = Field(default='', max_length=200)
    image_confirmed: bool = False
    backup_id: str = Field(default='', pattern=r'^[A-Za-z0-9_-]*$', max_length=150)
    confirmed: bool = False
    confirmation_token: str = Field(default='', max_length=64)

    @model_validator(mode='after')
    def valid_operation(self):
        if len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValueError('镜头编号不得重复')
        if self.action == 'unbind' and (not self.shot_ids or self.scene_id or self.upload_id or self.backup_id):
            raise ValueError('解绑只提交 shot_ids')
        if self.action in {'disable', 'replace'} and (not self.scene_id or self.shot_ids or self.backup_id):
            raise ValueError('停用或替换须指定一个 scene_id')
        if self.action == 'replace' and (not self.upload_id or not self.image_confirmed):
            raise ValueError('替换须提供用户上传且已核对的 upload_id 和 image_confirmed=true')
        if self.action == 'disable' and self.upload_id:
            raise ValueError('停用不接受上传素材')
        if self.action == 'restore' and (not self.backup_id or self.scene_id or self.shot_ids or self.upload_id):
            raise ValueError('恢复只提交 backup_id')
        return self


def scene_pack(record):
    return [{'id': a['id'], 'name': a.get('name', ''), 'disabled': bool(a.get('disabled')),
             'image_version': a.get('image_version', ''), 'image_status': a.get('image_status'),
             'bound_shot_ids': [s['id'] for s in record.get('shots', [])
                               if s.get('scene_reference_id') == a['id'] and not s.get('scene_reference_disabled')],
             'image_url': f"/api/codex-bridge/projects/{record['id']}/scene-assets/{a['id']}/image"}
            for a in record.get('scene_assets', [])]


def prepare(record, path, data, user_id):
    updated = copy.deepcopy(record)
    assets = {a['id']: a for a in updated.get('scene_assets', [])}
    shots = {s['id']: s for s in updated.get('shots', [])}
    content = None
    scene_ids = []
    if data.action == 'restore':
        entry = next((r for r in record.get('codex_bridge', {}).get('scene_history', [])
                      if r['backup_id'] == data.backup_id), None)
        if not entry:
            raise ValueError('场景恢复记录不存在')
        previous = bridge.read_record(path / 'codex_bridge_backups' / data.backup_id)
        old_assets = {a['id']: a for a in previous.get('scene_assets', [])}
        old_shots = {s['id']: s for s in previous.get('shots', [])}
        scene_ids, affected = entry['scene_ids'], entry['shot_ids']
        if any(i not in assets or i not in old_assets for i in scene_ids) or any(i not in shots or i not in old_shots for i in affected):
            raise ValueError('场景或镜头结构已变化，不能恢复旧绑定')
        for identity in scene_ids:
            current = assets[identity]
            history = copy.deepcopy(current.get('bridge_image_history', []))
            history.append({'image': current.get('image'), 'image_version': current.get('image_version')})
            current.clear(); current.update(copy.deepcopy(old_assets[identity]))
            current['bridge_image_history'] = history
            # The restored active image must not be blocked by its own history.
            current['bridge_image_history'] = [r for r in history if r.get('image') != current.get('image')]
            image = (path / str(current.get('image') or '')).resolve()
            if path.resolve() not in image.parents or not image.is_file():
                raise ValueError('恢复所需的旧场景图已缺失，未修改绑定')
        for identity in affected:
            for key in ('scene_reference_id', 'scene_reference_disabled'):
                shots[identity].pop(key, None)
                if key in old_shots[identity]:
                    shots[identity][key] = copy.deepcopy(old_shots[identity][key])
    elif data.action == 'unbind':
        if any(i not in shots for i in data.shot_ids):
            raise ValueError('指定镜头不存在')
        affected = list(data.shot_ids)
        for identity in affected:
            shots[identity].pop('scene_reference_id', None)
            shots[identity]['scene_reference_disabled'] = True
    else:
        if data.scene_id not in assets:
            raise ValueError('指定场景不存在')
        scene_ids = [data.scene_id]
        asset = assets[data.scene_id]
        affected = [s['id'] for s in shots.values() if s.get('scene_reference_id') == data.scene_id
                    or data.scene_id in (s.get('redraw_selection') or {}).get('reference_ids', [])]
        if data.action == 'disable':
            asset['disabled'] = True
        else:
            from .editor import upload_path
            from PIL import Image
            try:
                source = upload_path(user_id, data.upload_id)
                if source.stat().st_size > 30 * 1024 * 1024:
                    raise ValueError('替换图片不能超过30 MB')
                with Image.open(source) as image:
                    image.verify()
                with Image.open(source) as image:
                    image.load()
                    output = io.BytesIO()
                    image.convert('RGB').save(output, format='JPEG', quality=95)
                    content = output.getvalue()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise ValueError('上传素材不是当前用户的有效图片') from exc
            import hashlib
            version = hashlib.sha256(content).hexdigest()
            asset.setdefault('bridge_image_history', []).append(
                {'image': asset.get('image'), 'image_version': asset.get('image_version'), 'upload_id': data.upload_id})
            asset.update(image=f'assets/scene_references/bridge_{version}.jpg', image_version=version,
                         image_status='completed', image_origin='upload', disabled=False, image_error='')
    for identity in affected:
        shots[identity].pop('redraw_selection', None)
    return updated, affected, scene_ids, content


def review(identity, data, request, commit):
    user = bridge.user_for(request)
    with bridge.studio.LOCK:
        path = bridge.studio.directory(user['id'], identity)
        record = bridge.read_record(path)
        payload = data.model_dump(exclude={'confirmed', 'confirmation_token'})
        receipt = bridge.digest(payload)
        previous = record.get('codex_bridge', {}).get('scene_receipt', {})
        if previous.get('receipt') == receipt and previous.get('revision') == record['revision']:
            bridge.check_edit_context(record, path, data.model_copy(update={'revision': record['revision']}), user['id'])
            return {**previous['summary'], 'written': False, 'already_applied': True}
        bridge.check_edit_context(record, path, data, user['id'])
        if any(s.get('video_status') in {'running', 'unknown'} or
               (s.get('image_task') or {}).get('status') == 'running' or
               (s.get('video_status') != 'completed' and not s.get('video_terminal') and
                (s.get('video_resume_available') or s.get('video_task_id') or
                 (s.get('video_request') and s.get('video_execution_started') is not False)))
               for s in record.get('shots', [])):
            raise HTTPException(409, '项目有未结束的素材任务，请先查询或结束')
        try:
            updated, affected, scene_ids, content = prepare(record, path, data, user['id'])
        except (ValueError, KeyError) as exc:
            return {'ok': False, 'errors': [str(exc)], 'written': False, 'generation_started': False}
        token = bridge.digest({'payload': payload, 'record': record, 'proposal': updated})
        summary = {'ok': True, 'project_id': identity, 'revision': record['revision'],
                   'changed': affected, 'impacts': [{'id': i, 'scene_reference_changed': True} for i in affected],
                   'confirmation_token': token, 'generation_started': False, 'written': False,
                   'preserved': ['audio', 'subtitles', 'timeline', 'other_scenes', 'existing_images', 'videos']}
        summary['scene_state_token'] = bridge.digest(scene_pack(updated))
        if not commit:
            return summary
        if not data.confirmed or data.confirmation_token != token:
            raise HTTPException(409, '请先预检受影响镜头，再携带 confirmation_token 和 confirmed=true 确认写入')
        backup_id = 'scene-' + str(record['revision']) + '-' + uuid.uuid4().hex
        bridge.studio.save(path / 'codex_bridge_backups' / backup_id, copy.deepcopy(record))
        if content is not None:
            asset = next(a for a in updated['scene_assets'] if a['id'] == data.scene_id)
            target = path / asset['image']
            target.parent.mkdir(parents=True, exist_ok=True)
            # Content-addressed immutable file; old image is never overwritten.
            if target.exists() and target.read_bytes() != content:
                raise HTTPException(409, '场景文件内容冲突')
            if not target.exists():
                pending = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    pending.write_bytes(content)
                    pending.replace(target)
                finally:
                    pending.unlink(missing_ok=True)
        updated.setdefault('codex_bridge', {}).setdefault('scene_history', []).append(
            {'backup_id': backup_id, 'action': data.action, 'scene_ids': scene_ids, 'shot_ids': affected})
        updated['revision'] += 1
        summary.update(revision=updated['revision'], backup_id=backup_id, written=True)
        updated['codex_bridge']['scene_receipt'] = {'receipt': receipt, 'revision': updated['revision'], 'summary': summary}
        updated.setdefault('creation_parameters', {})['dynamic_auto_advance'] = False
        updated.setdefault('logs', []).append('制作桥：场景参考 ' + data.action + '；未启动生成。')
        bridge.studio.save(path, updated)
        actual = bridge.read_record(path)
        if actual != updated or bridge.timeline_token(actual, path) != data.timeline_token:
            bridge.studio.save(path, record)
            raise HTTPException(500, '场景修改回读校验失败，已恢复原记录')
        return summary


def register(router):
    @router.get('/projects/{identity}/scene-assets')
    def list_scenes(identity: str, request: Request):
        user = bridge.user_for(request)
        path = bridge.studio.directory(user['id'], identity)
        record = bridge.read_record(path)
        return {'items': scene_pack(record), 'revision': record['revision'],
                'history': record.get('codex_bridge', {}).get('scene_history', [])}

    @router.get('/projects/{identity}/scene-assets/{scene_id}/image')
    def scene_image(identity: str, scene_id: str, request: Request):
        user = bridge.user_for(request)
        path = bridge.studio.directory(user['id'], identity)
        record = bridge.read_record(path)
        asset = next((a for a in record.get('scene_assets', []) if a['id'] == scene_id), None)
        target = (path / str((asset or {}).get('image', ''))).resolve()
        if not asset or path.resolve() not in target.parents or not target.is_file():
            raise HTTPException(404, '场景图不存在')
        return FileResponse(target, headers={'Cache-Control': 'no-store'})

    @router.post('/projects/{identity}/scene-validate')
    def validate(identity: str, data: SceneEdit, request: Request):
        return review(identity, data, request, False)

    @router.post('/projects/{identity}/scene-apply')
    def apply(identity: str, data: SceneEdit, request: Request):
        return review(identity, data, request, True)
