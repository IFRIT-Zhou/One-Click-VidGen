"""Confirmed, snapshot-bound image requests. Credentials never enter the durable ledger."""
import copy
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from fastapi import HTTPException, Request
from pydantic import Field, model_validator
from . import codex_bridge as bridge

RUNTIME = {}  # (project directory, request id) -> frozen inputs; deliberately not persisted
RUNNING = set()
MAX_WORKERS = 7
LIVE = {'queued', 'running'}


class ImageOptions(bridge.StrictModel):
    use_current_image: bool | None = None
    use_scene_reference: bool | None = None
    image_resolution: Literal['1k', '2k', '4k'] | None = None
    image_size: str | None = Field(default=None, pattern=r'^[1-9][0-9]{2,4}[xX×][1-9][0-9]{2,4}$')


class ImageGenerate(ImageOptions):
    revision: int = Field(ge=1)
    timeline_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    audio_confirmed: Literal[True]
    shot_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    confirmed: bool = False
    confirmation_token: str = ''


class BatchItem(ImageOptions):
    shot_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')


class ImageBatch(bridge.StrictModel):
    revision: int = Field(ge=1)
    timeline_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    audio_confirmed: Literal[True]
    batch_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    items: list[BatchItem] = Field(min_length=1, max_length=100)
    concurrency: int = Field(default=3, ge=1, le=7)
    confirmed: bool = False
    confirmation_token: str = ''

    @model_validator(mode='after')
    def unique(self):
        for field in ('shot_id', 'request_id'):
            if len({getattr(i, field) for i in self.items}) != len(self.items):
                raise ValueError(f'批次中的 {field} 必须唯一')
        return self


class GenerationSettings(bridge.StrictModel):
    revision: int = Field(ge=1)
    use_cloud_image_pool: bool | None = None
    image_resolution: Literal['1k', '2k', '4k'] | None = None
    image_size: str | None = Field(default=None, pattern=r'^[1-9][0-9]{2,4}[xX×][1-9][0-9]{2,4}$')


class BatchControl(bridge.StrictModel):
    batch_id: str
    action: Literal['pause', 'resume', 'cancel_pending']


def fail(code, message, status=409, **extra):
    raise HTTPException(status, {'code': code, 'message': message,
        'generation_started': False, 'charge_submitted': False, **extra})


def error_detail(exc):
    chain = []
    current = exc
    while current is not None and len(chain) < 8:
        chain.append(str(current)); current = current.__cause__ or current.__context__
    value = ' '.join(chain).upper()
    error_id = uuid.uuid4().hex
    # Do not return provider URLs, bearer tokens or raw upstream exception strings.
    code, message, status = 'IMAGE_PROVIDER_ERROR', '图片服务请求失败，请按错误编号查看服务端日志。', 502
    if 'CLOUD_LOGIN_REQUIRED' in value or '401' in value or '未登录' in value:
        code, message, status = 'IMAGE_AUTH_REQUIRED', '当前图片路由需要登录，请登录号池或明确切换到已配置的图像 API。', 401
    elif '429' in value or 'RATE_LIMIT' in value:
        code, message, status = 'IMAGE_RATE_LIMITED', '图片服务限流，请降低并发后重试。', 429
    elif '配置' in value or 'API KEY' in value or 'API_KEY' in value:
        code, message, status = 'IMAGE_API_NOT_CONFIGURED', '当前图片路由没有可用配置，请检查 API 设置。', 422
    elif 'PROXY' in value or 'CONNECTION' in value or 'TIMEOUT' in value:
        code, message, status = 'IMAGE_NETWORK_ERROR', '无法连接图片服务，请检查网络或代理设置。', 503
    logging.getLogger(__name__).warning('Bridge image error %s (%s)', error_id, type(exc).__name__)
    return status, {'code': code, 'message': message, 'error_id': error_id}


def effective(record):
    p = record.get('creation_parameters') or {}
    profile = p.get('image_profile_snapshot') or {}
    public = {'route': 'cloud_pool' if p.get('use_cloud_image_pool') else 'api',
        'configuration_source': 'project_snapshot' if profile else 'project_route/global_api',
        'image_resolution': p.get('image_resolution', '1k'), 'image_size': p.get('size', ''),
        'ratio': record.get('settings', {}).get('ratio', '16:9')}
    public['configuration_version'] = bridge.digest(public)
    return public


def ledger(record):
    return record.setdefault('codex_bridge', {}).setdefault('image_requests', {})


def target_state(path, record, shot):
    """Relevant source content only: unrelated shot results must not invalidate this request."""
    return bridge.digest({'timeline': bridge.timeline_token(record, path),
        'shot': {k: shot.get(k) for k in ('image_prompt', 'reference_ids', 'redraw_selection',
            'scene_reference_id', 'scene_reference_disabled', 'image', 'image_version')},
        'settings': record.get('settings'), 'generation': record.get('creation_parameters'),
        'scene': bridge.studio._scene_asset(record, shot)})


def context(path, record, data, user, commit):
    if str(path) in bridge.studio.ACTIVE or record.get('status') != 'image_review':
        fail('PROJECT_BUSY', '项目正在运行或不在核心分镜图编辑阶段。', retry_after=3)
    if not commit and data.revision != record['revision']:
        fail('REVISION_CONFLICT', '项目版本已变化，请重新读取资料包。')
    bridge.check_edit_context(record, path,
        SimpleNamespace(revision=record['revision'], timeline_token=data.timeline_token),
        user['id'], allow_image_edits=True)


def prepare(path, record, item, user):
    shot = bridge.studio._find_shot(record, item.shot_id)
    if bridge.studio.image_edit_key(record['id'], item.shot_id) in bridge.studio.IMAGE_EDITS or (shot.get('image_task') or {}).get('status') in LIVE:
        fail('SHOT_BUSY', '本镜已有图片任务，请查询状态，不要重复提交。', shot_id=item.shot_id, retry_after=3)
    selection = shot.get('redraw_selection') or {}
    ids = list(selection.get('reference_ids', shot.get('reference_ids', [])))
    current = item.use_current_image if item.use_current_image is not None else selection.get('use_current_image', False)
    scene = item.use_scene_reference if item.use_scene_reference is not None else selection.get('use_scene_reference', True)
    rows = {str(r['id']): r for r in [*record.get('references', []), *record.get('redraw_references', []),
                                    *bridge.studio._scene_redraw_references(record)]}
    if len(set(ids)) != len(ids) or any(i not in rows for i in ids) or len(ids) + int(current) > 3:
        fail('INVALID_REFERENCES', '参考图选择无效或超过三张手动参考图。', 422)
    refs, kinds = [], []
    if current:
        refs.append(str(bridge.studio._storyboard_image_path(path, shot).resolve())); kinds.append('current_image')
    for i in ids:
        refs.append(str((path / rows[i]['file']).resolve())); kinds.append('reference')
    for p in refs:
        if path.resolve() not in Path(p).parents or not Path(p).is_file():
            fail('REFERENCE_MISSING', '选中的参考图文件不存在。', 422)
    try:
        prompt, refs, asset = bridge.studio._image_inputs(path, record, shot, shot['image_prompt'], references=refs, use_scene=scene)
    except ValueError as exc:
        fail('INVALID_REFERENCES', str(exc), 422)
    try:
        configs = copy.deepcopy(bridge.studio._image_configs(record, user['id']))
    except Exception as exc:
        status, detail = error_detail(exc); fail(**{'code': detail.pop('code'), 'message': detail.pop('message'), 'status': status}, **detail)
    resolution = item.image_resolution or (record.get('creation_parameters') or {}).get('image_resolution')
    if resolution:
        for c in configs: c['resolution'] = resolution
    size = item.image_size or (record.get('creation_parameters') or {}).get('size')
    if size:
        for c in configs:
            if c.get('method') == 'ican': c['size'] = size.replace('×', 'x').lower()
    files = [(p, bridge.file_hash(Path(p))) for p in refs]
    state = target_state(path, record, shot)
    snapshot = bridge.digest({'state': state, 'options': item.model_dump(), 'prompt': prompt, 'files': files, 'configs': configs})
    public = {'shot_id': item.shot_id, 'request_id': item.request_id, 'target_snapshot': snapshot,
        'actual_reference_count': len(refs), 'reference_ids': ids,
        'references': [{'local_image_number': n+1, 'kind': kinds[n] if n < len(kinds) else 'scene_reference',
                        'file': str(Path(p).relative_to(path.resolve()))} for n, p in enumerate(refs)],
        'effective_image_config': {**effective(record), 'image_resolution': configs[0].get('resolution', effective(record)['image_resolution']),
            'providers': [{k: c.get(k) for k in ('method', 'model', 'resolution', 'size') if k in c} for c in configs],
            'configuration_version': bridge.digest(configs)}}
    frozen = {'record': copy.deepcopy(record), 'prompt': prompt, 'refs': refs, 'files': files, 'configs': configs,
        'state': state, 'scene_version': asset.get('image_version', '') if asset else '', 'item': item.model_dump(), 'user_id': user['id']}
    return public, frozen


def execute(identity, data, request, commit):
    user = bridge.user_for(request)
    items = data.items if isinstance(data, ImageBatch) else [BatchItem(**data.model_dump(include=set(BatchItem.model_fields)))]
    batch_id = data.batch_id if isinstance(data, ImageBatch) else data.request_id
    signature = bridge.digest(data.model_dump(exclude={'revision', 'confirmed', 'confirmation_token'}))
    with bridge.studio.LOCK:
        path = bridge.studio.directory(user['id'], identity)
        record = bridge.read_record(path)
        batches = record.setdefault('codex_bridge', {}).setdefault('image_batches', {})
        previous = batches.get(batch_id)
        if previous:
            if previous['signature'] != signature: fail('REQUEST_ID_CONFLICT', '此批次或请求编号已用于其他内容。')
            return {**previous['result'], 'already_submitted': True}
        for item in items:
            old = ledger(record).get(item.request_id)
            if old:
                legacy_payload = data.model_dump(include={'revision', 'timeline_token', 'audio_confirmed', 'shot_id', 'request_id'})
                if len(items) == 1 and old.get('receipt') == bridge.digest(legacy_payload):
                    return {**old['result'], 'already_submitted': True}
                fail('REQUEST_ID_CONFLICT', '此请求编号已有提交记录，请查询 image-status。')
        context(path, record, data, user, commit)
        prepared = [prepare(path, record, i, user) for i in items]
        previews = [p for p, _ in prepared]
        token = bridge.digest({'signature': signature, 'targets': previews})
        result = {'ok': True, 'project_id': identity, 'revision': record['revision'], 'batch_id': batch_id,
            'confirmation_token': token, 'items': previews, 'may_charge': True, 'generation_started': False,
            'charge_submitted': False}
        if len(items) == 1: result.update(previews[0])
        if not commit: return result
        if not data.confirmed or data.confirmation_token != token:
            fail('TARGET_SNAPSHOT_CHANGED', '请预检、核对路由和参考图并确认费用；目标内容改变时必须重新预检。')
        backup = 'image-' + uuid.uuid4().hex
        bridge.studio.save(path / 'codex_bridge_backups' / backup, copy.deepcopy(record))
        result.update(generation_started=True, backup_id=backup, revision=record['revision']+1)
        batches[batch_id] = {'signature': signature, 'paused': False,
            'concurrency': data.concurrency if isinstance(data, ImageBatch) else 1, 'result': result}
        for item, (public, frozen) in zip(items, prepared):
            shot = bridge.studio._find_shot(record, item.shot_id)
            sequence = int(shot.get('bridge_image_sequence', 0))+1
            shot['bridge_image_sequence'] = sequence
            shot['image_task'] = {'status': 'queued', 'request_id': item.request_id, 'message': '制作桥排队中'}
            ledger(record)[item.request_id] = {**public, 'batch_id': batch_id, 'status': 'queued', 'sequence': sequence,
                'created_at': time.time(), 'charge_submitted': False, 'review_status': 'not_reviewed'}
            RUNTIME[(str(path), item.request_id)] = frozen
            bridge.studio.IMAGE_EDITS.add(bridge.studio.image_edit_key(identity, item.shot_id))
        record['revision'] += 1
        bridge.studio.save(path, record)
        pump(path)
        return result


def render(path, entry, frozen):
    import module4_video_render as visual
    output = path / 'assets' / 'bridge_images' / (entry['request_id'] + '.jpg')
    output.parent.mkdir(parents=True, exist_ok=True)
    prompt = frozen['prompt']
    if frozen['refs']:
        prompt = f'【参考图编号】附带图片依次为图1至图{len(frozen["refs"])}，严格按编号引用。\n' + prompt
    macro = {'macro_scene_id': entry['shot_id'], 'image_prompt': prompt, 'reference_image_paths': frozen['refs'],
        'reference_image_ids': [], 'reference_binding_version': 1, '_output_path': str(output.resolve())}
    pool = visual.shared_runninghub_account_pool(frozen['configs'], namespace='video_storyboard_redraw', retry_power_exhausted=True)
    with bridge.studio._image_language_scope(path, frozen['record']):
        result = visual._render_poster_with_retry(macro, pool)
    if not result.is_file() or result.stat().st_size == 0: raise FileNotFoundError('Empty image result')
    bridge.studio._normalize_rgb_image(result)
    return result


def pump(path):
    """Caller holds studio.LOCK. Provider pool additionally enforces per-key quotas."""
    record = bridge.read_record(path)
    for rid, entry in ledger(record).items():
        key = (str(path), rid)
        batch = record['codex_bridge']['image_batches'].get(entry.get('batch_id'), {})
        limit = batch.get('concurrency', 1)
        active = sum(k[0] == str(path) for k in RUNNING)
        if entry.get('status') != 'queued' or batch.get('paused') or key not in RUNTIME or key in RUNNING:
            continue
        if len(RUNNING) >= MAX_WORKERS or active >= limit: break
        RUNNING.add(key)
        threading.Thread(target=worker, args=(path, rid), daemon=True).start()


def worker(path, rid):
    key = (str(path), rid)
    frozen = RUNTIME.get(key)
    if frozen is None:
        with bridge.studio.LOCK: RUNNING.discard(key)
        return
    entry = None
    try:
        with bridge.studio.LOCK:
            record = bridge.read_record(path); entry = ledger(record)[rid]
            if entry['status'] != 'queued' or record['codex_bridge']['image_batches'][entry['batch_id']]['paused']: return
            shot = bridge.studio._find_shot(record, entry['shot_id'])
            bridge.check_edit_context(record, path, SimpleNamespace(revision=record['revision'],
                timeline_token=bridge.timeline_token(frozen['record'], path)), frozen['user_id'], allow_image_edits=True)
            if target_state(path, record, shot) != frozen['state'] or any(bridge.file_hash(Path(p)) != h for p, h in frozen['files']):
                fail('TARGET_SNAPSHOT_CHANGED', '排队期间目标素材或配置变化，未提交图片服务。')
            entry.update(status='running', started_at=time.time(), charge_submitted=True)
            shot['image_task'].update(status='running', message='正在生成图片')
            record['revision'] += 1; bridge.studio.save(path, record)
        rendered = render(path, entry, frozen)
        with bridge.studio.LOCK:
            latest = bridge.read_record(path); live = ledger(latest)[rid]
            shot = bridge.studio._find_shot(latest, entry['shot_id'])
            if shot.get('bridge_image_sequence') != entry['sequence'] or target_state(path, latest, shot) != frozen['state'] or any(bridge.file_hash(Path(p)) != h for p, h in frozen['files']):
                live.update(status='stale_result', result_file=str(rendered.relative_to(path)), error_code='TARGET_SNAPSHOT_CHANGED')
                if shot.get('bridge_image_sequence') == entry['sequence']:
                    shot['image_task'] = {'status': 'failed', 'request_id': rid, 'message': '目标已变更，返回图片已留存但未覆盖当前图'}
            else:
                if shot.get('image') and (path / shot['image']).is_file():
                    image = bridge.studio._archive_storyboard_image(path, shot)
                else:
                    shot['image'] = f'assets/bridge_images/current_{shot["id"]}.jpg'
                    image = path / shot['image']
                image.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rendered, image)
                shot.update(image_status='completed', image_error='', image_applied_prompt=shot['image_prompt'],
                    image_prompt_out_of_sync=False, image_material_numbers_bound=True,
                    scene_reference_used_version=frozen['scene_version'],
                    image_task={'status': 'completed', 'request_id': rid, 'message': '图片生成完成，请人工检查'})
                bridge.studio._image_version(path, shot)
                bridge.studio._invalidate_shot_video(latest, shot, '制作桥已重绘核心图')
                live.update(status='completed', result_file=shot.get('image'), review_status='not_reviewed')
            live['finished_at'] = time.time(); latest['revision'] += 1; bridge.studio.save(path, latest)
    except Exception as exc:
        if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
            status, detail = exc.status_code, exc.detail
        elif isinstance(exc, HTTPException):
            status, detail = exc.status_code, {'code': 'SOURCE_CONTEXT_CHANGED', 'message': str(exc.detail)}
        else:
            status, detail = error_detail(exc)
        with bridge.studio.LOCK:
            latest = bridge.read_record(path); live = ledger(latest)[rid]
            live.update(status='failed', error=detail, finished_at=time.time())
            shot = bridge.studio._find_shot(latest, live['shot_id'])
            if shot.get('bridge_image_sequence') == live['sequence']:
                shot['image_task'] = {'status': 'failed', 'request_id': rid, 'message': detail['message']}
            latest['revision'] += 1; bridge.studio.save(path, latest)
    finally:
        with bridge.studio.LOCK:
            RUNNING.discard(key)
            # A paused launch must remain resumable, with frozen inputs and target lock.
            latest = bridge.read_record(path)
            live = ledger(latest)[rid]
            if live['status'] != 'queued':
                RUNTIME.pop(key, None)
                bridge.studio.IMAGE_EDITS.discard(bridge.studio.image_edit_key(latest['id'], live['shot_id']))
            for pending_path, _ in list(RUNTIME): pump(Path(pending_path))


def register(router):
    @router.post('/projects/{identity}/image-validate')
    def validate(identity: str, data: ImageGenerate, request: Request): return execute(identity, data, request, False)

    @router.post('/projects/{identity}/image-apply')
    def apply(identity: str, data: ImageGenerate, request: Request): return execute(identity, data, request, True)

    @router.post('/projects/{identity}/image-batch-validate')
    def batch_validate(identity: str, data: ImageBatch, request: Request): return execute(identity, data, request, False)

    @router.post('/projects/{identity}/image-batch-apply')
    def batch_apply(identity: str, data: ImageBatch, request: Request): return execute(identity, data, request, True)

    @router.get('/projects/{identity}/generation-settings')
    def settings(identity: str, request: Request):
        user = bridge.user_for(request); record = bridge.read_record(bridge.studio.directory(user['id'], identity))
        return {'ok': True, 'revision': record['revision'], 'effective_image_config': effective(record)}

    @router.patch('/projects/{identity}/generation-settings')
    def settings_patch(identity: str, data: GenerationSettings, request: Request):
        user = bridge.user_for(request)
        with bridge.studio.LOCK:
            path = bridge.studio.directory(user['id'], identity); record = bridge.read_record(path)
            bridge.studio.editable(record, data.revision)
            if str(path) in bridge.studio.ACTIVE: fail('PROJECT_BUSY', '项目正在运行，不能修改生成路由。')
            updates = data.model_dump(exclude={'revision'}, exclude_unset=True)
            if not updates or any(v is None for v in updates.values()): fail('INVALID_SETTINGS', '只提交要修改的非空配置。', 422)
            if 'image_size' in updates: updates['size'] = updates.pop('image_size').replace('×', 'x').lower()
            backup = 'settings-' + uuid.uuid4().hex
            bridge.studio.save(path / 'codex_bridge_backups' / backup, copy.deepcopy(record))
            record.setdefault('creation_parameters', {}).update(updates)
            record['revision'] += 1; bridge.studio.save(path, record)
            return {'ok': True, 'revision': record['revision'], 'backup_id': backup, 'generation_started': False,
                'effective_image_config': effective(record)}

    @router.post('/projects/{identity}/image-batch-control')
    def control(identity: str, data: BatchControl, request: Request):
        user = bridge.user_for(request)
        with bridge.studio.LOCK:
            path = bridge.studio.directory(user['id'], identity); record = bridge.read_record(path)
            batch = record.get('codex_bridge', {}).get('image_batches', {}).get(data.batch_id)
            if not batch: fail('BATCH_NOT_FOUND', '找不到该图片批次。', 404)
            batch['paused'] = data.action != 'resume'
            for rid, entry in ledger(record).items():
                if entry.get('batch_id') != data.batch_id or entry.get('status') != 'queued': continue
                if data.action == 'cancel_pending':
                    entry.update(status='cancelled', finished_at=time.time())
                    RUNTIME.pop((str(path), rid), None)
                    bridge.studio.IMAGE_EDITS.discard(bridge.studio.image_edit_key(identity, entry['shot_id']))
                    bridge.studio._find_shot(record, entry['shot_id'])['image_task'] = {'status': 'cancelled', 'request_id': rid}
                elif data.action == 'resume' and (str(path), rid) not in RUNTIME:
                    fail('RESTART_CONFIRMATION_REQUIRED', '服务已重启，待提交任务需重新预检确认，不会自动补交付费请求。')
            record['revision'] += 1; bridge.studio.save(path, record); pump(path)
            return {'ok': True, 'batch_id': data.batch_id, 'action': data.action, 'running_requests_not_cancelled': True}

    @router.get('/projects/{identity}/image-status')
    def status(identity: str, request: Request, request_id: str | None = None, batch_id: str | None = None):
        user = bridge.user_for(request)
        with bridge.studio.LOCK:
            path = bridge.studio.directory(user['id'], identity); record = bridge.read_record(path)
            rows = []
            for rid, item in ledger(record).items():
                if request_id and rid != request_id or batch_id and item.get('batch_id') != batch_id: continue
                row = copy.deepcopy(item)
                if row.get('status') in LIVE and (str(path), rid) not in RUNTIME:
                    row['status'] = 'interrupted_unknown' if row['status'] == 'running' else 'interrupted_pending'
                rows.append(row)
            return {'ok': True, 'revision': record['revision'], 'requests': rows,
                'shots': [{**{k: s.get(k) for k in ('id', 'image_status', 'image_task', 'image_error')},
                    'has_existing_image': bool(s.get('image')), 'image_matches_current_plan': bool(s.get('image')) and not s.get('image_prompt_out_of_sync', False),
                    'current_request_id': (s.get('image_task') or {}).get('request_id')} for s in record.get('shots', [])]}
