"""Independent single-image workspace. One submission, no automatic retries."""
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
from typing import Literal

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .auth import require_user
from .editor import upload_path
from .cloud_image_channel import ImageSize
from . import image_request_control as image_control

router = APIRouter(prefix='/api/image-studio')
ROOT = Path(__file__).resolve().parents[2] / 'workspace' / 'image_studio'
LOCK = threading.RLock()
ACTIVE: set[tuple[int, str]] = set()
QUEUED = []
RUNNING_LANES = {}


def enqueue(user, path, record, config, client=None):
    import module4_video_render as visual
    lane = hashlib.sha256((str(user)+'|'+str(config.get('endpoint'))+'|'+str(config.get('api_key'))).encode()).hexdigest()
    limit = min(32, visual._poster_worker_count([config], 32))
    with LOCK:
        key = (user, record['id'])
        if key in ACTIVE:
            raise RuntimeError('该图片已经在队列中')
        if sum(u == user for u, _ in ACTIVE) >= 100:
            raise RuntimeError('图片队列已满，请等待部分任务完成')
        record.update(status='queued', message='已加入队列，等待出图名额')
        save(path, record)
        ACTIVE.add(key)
        QUEUED.append((user, path, record, config, client, lane, limit))
        dispatch()


def dispatch():
    # All callers hold LOCK; each worker owns an immutable submission snapshot.
    for entry in list(QUEUED):
        user, path, record, config, client, lane, limit = entry
        if RUNNING_LANES.get(lane, 0) >= limit:
            continue
        QUEUED.remove(entry)
        RUNNING_LANES[lane] = RUNNING_LANES.get(lane, 0) + 1
        record.update(status='running', message='正在提交')
        save(path, record)
        def work(entry=entry):
            user, path, record, config, client, lane, _ = entry
            try:
                execute(user, path, record, config, client)
            finally:
                with LOCK:
                    ACTIVE.discard((user, record['id']))
                    RUNNING_LANES[lane] -= 1
                    dispatch()
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as exc:
            ACTIVE.discard((user, record['id']))
            RUNNING_LANES[lane] -= 1
            record.update(status='failed', message='启动出图任务失败：'+str(exc))
            save(path, record)


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    provider: Literal['custom', 'pool'] = 'custom'
    method: Literal['running', 'ican'] = 'running'
    size: ImageSize = "2560x1440"
    profile_id: str | None = Field(default=None, max_length=80)
    ratio: Literal['2:1', '16:9', '3:2', '1:1', '3:4', '2:3', '9:16', '21:9'] = '2:1'
    resolution: Literal['', '1k', '2k', '4k'] = ''
    references: list[str] = Field(default_factory=list, max_length=4)


def folder(user: int, identity: str) -> Path:
    if not identity or any(c not in '0123456789abcdef' for c in identity):
        raise HTTPException(404, '图片记录不存在')
    return ROOT / str(user) / identity


def save(path: Path, record: dict) -> None:
    with LOCK:
        temp = path / 'record.tmp'
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        temp.replace(path / 'record.json')


def config_for(user: int, data: ImageRequest):
    import module4_video_render as visual
    client = None
    if data.provider == 'pool':
        from .cloud_client import cloud_client_for
        client = cloud_client_for(user)
        runtime = client.image_pool_runtime()
        base = runtime['base_url'].rstrip('/')
        config = dict(endpoint=base+'/image-pool/generate', query_url=base+'/image-pool/query',
                      upload_url=base+'/image-pool/media/upload',
                      api_key=runtime['access_token'], refresh_token=runtime['refresh_token'],
                      cloud_base_url=base, cloud_pool='1')
        config.update({key: runtime[key] for key in ('cloud_user_id', 'cloud_session_store_path') if key in runtime})
        from .cloud_image_channel import configure_channel
        config = configure_channel(config, data.method, data.size)
    else:
        if data.profile_id:
            from .image_profiles import profile_environment, profile_snapshot
            snapshot = profile_snapshot(data.profile_id, data.resolution)
            environment = profile_environment(snapshot)
            config = {
                'endpoint': environment['RUNNINGHUB_ENDPOINT'],
                'query_url': environment['OCV_IMAGE_QUERY_URL'],
                'reference_endpoint': environment['RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT'],
                'api_key': environment['RUNNINGHUB_API_KEY'],
                'model': environment['IMAGE_MODEL_ID'],
                'resolution': environment['IMAGE_RESOLUTION'],
                'profile_name': snapshot.get('name') or '',
                'reference_images': snapshot.get('reference_images', True),
            }
        else:
            configs = visual._provider_configs()
            if not configs:
                raise ValueError('请先在接口与服务中保存图像 API 配置')
            config = dict(configs[0])
    config['ratio'] = data.ratio
    from .config import _parse_env_lines
    saved = _parse_env_lines(Path(__file__).resolve().parents[2] / '.env')
    config['resolution'] = data.resolution or config.get('resolution') or saved.get('IMAGE_RESOLUTION') or saved.get('RUNNINGHUB_RESOLUTION') or '1k'
    config['endpoint'] = visual._runninghub_generate_url(config)
    config['query_url'] = config.get('query_url') or visual._runninghub_url('/openapi/v2/query')
    return config, client


def upload_pool_reference(session: requests.Session, source: Path, config: dict) -> str:
    """Upload a reference into the signed-in pool account before generation."""
    import module4_video_render as visual

    mime_type = mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
    response = visual._request_with_cloud_refresh(
        session,
        'POST',
        config['upload_url'],
        config=config,
        files={'file': (source.name, source.read_bytes(), mime_type)},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    url = str(visual._find_image_url(payload, base_url=config.get('cloud_base_url')) or '').strip()
    if not url:
        raise RuntimeError('号池参考图上传成功，但服务端未返回可用图片地址')
    return url


def execute(user: int, path: Path, record: dict, config: dict, cloud_pool_client=None) -> None:
    with image_control.scope(label='图片工作室任务'):
        _execute(user, path, record, config, cloud_pool_client)


def _execute(user: int, path: Path, record: dict, config: dict, cloud_pool_client=None) -> None:
    import module4_video_render as visual
    generation_attempted = False
    try:
        image_control.check()
        payload = dict(prompt=record['prompt'], aspectRatio=record['ratio'], resolution=record['resolution'])
        if config.get('model'):
            payload['model'] = config['model']
        endpoint = config['endpoint']
        if config.get('cloud_pool') == '1':
            payload['clientJobId'] = 'image-studio-' + record['id']
        with requests.Session() as session:
            if record['references'] and not record.get('remote_task_id'):
                if config.get('reference_images') is False:
                    raise ValueError('所选图像模型配置未启用参考图能力')
                if config.get('cloud_pool') == '1':
                    record['message'] = f"正在上传 {len(record['references'])} 张参考图"
                    save(path, record)
                    payload['imageUrls'] = [
                        upload_pool_reference(session, path / name, config)
                        for name in record['references']
                    ]
                else:
                    payload['imageUrls'] = [
                        'data:'+ (mimetypes.guess_type(name)[0] or 'image/png') + ';base64,' +
                        base64.b64encode((path/name).read_bytes()).decode('ascii')
                        for name in record['references']
                    ]
                endpoint = config.get('reference_endpoint') or endpoint.replace('/text-to-image', '/image-to-image')
            from .cloud_image_channel import generation_payload
            payload = generation_payload(payload, config)
            headers = {'Authorization': 'Bearer '+config['api_key']}
            # Keep the same clientJobId while renewing a short-lived cloud token.
            # A 401 therefore resumes polling the original task instead of creating
            # another billed image job.
            generation_attempted = True
            task_id = record.get('remote_task_id')
            if not task_id:
                response = visual._request_with_cloud_refresh(
                    session, 'POST', endpoint, json=payload, headers=headers, config=config, timeout=60
                )
                response.raise_for_status()
                result = response.json()
                task_id = visual._find_first_key(result, {'taskId', 'taskID', 'id'})
            else:
                result = {}
            if not task_id:
                raise RuntimeError(visual._runninghub_error_message(result) or '服务未返回任务 ID，提交结果尚未确认')
            record['remote_task_id'] = str(task_id)
            record['message'] = '已提交，等待图片生成'
            save(path, record)
            deadline = time.monotonic() + 1200
            while time.monotonic() < deadline:
                image_control.check()
                response = visual._request_with_cloud_refresh(
                    session, 'POST', config.get('query_url') or visual._runninghub_url('/openapi/v2/query'),
                    json={'taskId': task_id}, headers=headers, config=config, timeout=60
                )
                response.raise_for_status()
                result = response.json()
                status = str(visual._find_first_key(result, {'status', 'state', 'taskStatus'}) or '').upper()
                url = visual._find_image_url(result, base_url=config.get('cloud_base_url'))
                if status in {'FAILED','FAILURE','ERROR','CANCELLED','CANCELED','REJECTED','BLOCKED','ABORTED','TERMINATED','TIMEOUT','TIMED_OUT','EXPIRED'}:
                    raise RuntimeError(f"{status} / {visual._runninghub_result_error_code(result)}: " + (visual._runninghub_error_message(result) or '服务端未提供详细原因'))
                if url:
                    downloaded = visual._request_with_cloud_refresh(
                        session, 'GET', url, config=config, timeout=120
                    )
                    downloaded.raise_for_status()
                    from PIL import Image
                    import io
                    with Image.open(io.BytesIO(downloaded.content)) as picture:
                        picture.load()
                        image_control.check()
                        record['width'], record['height'] = picture.size
                        picture.save(path/'result.png', format='PNG')
                    record.update(status='completed', message='生成完成')
                    break
                image_control.wait(3)
            else:
                raise TimeoutError('等待超时，远端结果尚未确认，请核对服务端记录后再生成')
    except Exception as exc:
        message = str(exc)
        if record.get('references') and not generation_attempted:
            detail = message
            if isinstance(exc, requests.RequestException) and exc.response is not None:
                try:
                    detail = visual._runninghub_error_message(exc.response.json()) or detail
                except ValueError:
                    pass
            message = f'参考图上传失败：{detail}。出图任务尚未提交，不会产生图片费用。'
        elif isinstance(exc, requests.RequestException):
            response = exc.response
            detail = ''
            if response is not None:
                try:
                    detail = visual._runninghub_error_message(response.json())
                except ValueError:
                    pass
            message = f'网络或接口错误：{type(exc).__name__}；{detail or message}。结果尚未确认，请核对扣费后再生成。'
        record.update(status='paused' if isinstance(exc, visual.CloudImageLoginRequired) else 'failed', message=message)
    finally:
        try:
            save(path, record)
        finally:
            if cloud_pool_client is not None and config.get('cloud_pool') == '1' and not config.get('cloud_user_id'):
                cloud_pool_client.adopt_image_pool_runtime({
                    'access_token': config.get('api_key'),
                    'refresh_token': config.get('refresh_token'),
                    'expires_in': 900,
                })


@router.post('')
def create(data: ImageRequest, request: Request):
    user = int(require_user(request)['id'])
    if not data.prompt.strip():
        raise HTTPException(400, '请填写提示词')
    path = None
    try:
        config, cloud_pool_client = config_for(user, data)
        sources = []
        for reference in data.references:
            if reference.startswith('history:'):
                parts = reference.split(':')
                name = parts[2] if len(parts) == 3 else 'result.png'
                source = folder(user, parts[1]) / name
                if name != 'result.png' and not (name.startswith('ref_') and '/' not in name and '\\' not in name):
                    raise ValueError('参考图无效')
            else:
                source = upload_path(user, reference)
            if source.suffix.lower() not in {'.jpg','.jpeg','.png','.webp'} or not source.is_file():
                raise ValueError('参考图片不存在或格式不支持')
            sources.append(source)
        identity = uuid.uuid4().hex
        path = folder(user, identity)
        path.mkdir(parents=True)
        names = []
        for i, source in enumerate(sources, 1):
            name = f'ref_{i}{source.suffix.lower()}'
            shutil.copy2(source, path/name)
            names.append(name)
        record = dict(id=identity, created_at=time.time(), status='running', message='正在提交',
                      prompt=data.prompt, provider=data.provider, method=data.method,
                      size=data.size, ratio=data.ratio,
                      profile_id=data.profile_id, profile_name=config.get('profile_name') or '',
                      resolution=data.size if data.provider == 'pool' and data.method == 'ican' else config['resolution'], references=names)
        if data.provider == 'pool' and data.method == 'ican':
            from math import gcd
            width, height = map(int, data.size.split('x'))
            divisor = gcd(width, height)
            record['ratio'] = f'{width // divisor}:{height // divisor}'
        save(path, record)
        enqueue(user, path, record, config, cloud_pool_client)
        return record
    except Exception as exc:
        if path is not None and (path/'record.json').is_file():
            record.update(status='failed', message=str(exc))
            save(path, record)
        if getattr(exc, 'code', '') == 'CLOUD_LOGIN_REQUIRED':
            raise HTTPException(401, '[CLOUD_LOGIN_REQUIRED] 登录已过期，请重新登录') from exc
        raise HTTPException(400, str(exc)) from exc


@router.post('/{identity}/resume')
def resume_image(identity: str, request: Request):
    user = int(require_user(request)['id'])
    path = folder(user, identity)
    with LOCK:
        if not (path / 'record.json').is_file():
            raise HTTPException(404, '图片记录不存在')
        record = json.loads((path / 'record.json').read_text(encoding='utf-8'))
        if record.get('status') != 'paused':
            raise HTTPException(409, '只有等待重新登录的图片可以继续')
        try:
            config, client = config_for(user, ImageRequest(**{
                key: record[key] for key in ('prompt', 'provider', 'method', 'size', 'profile_id', 'ratio', 'references') if key in record
            }, resolution=record['resolution'] if record['resolution'] in ('1k', '2k', '4k') else ''))
        except Exception as exc:
            if getattr(exc, 'code', '') == 'CLOUD_LOGIN_REQUIRED':
                raise HTTPException(401, '[CLOUD_LOGIN_REQUIRED] 登录已过期，请重新登录') from exc
            raise
        record.update(status='running', message='继续获取原任务结果')
        save(path, record)
        enqueue(user, path, record, config, client)
    return record


@router.get('')
def history(request: Request):
    user = int(require_user(request)['id'])
    records = []
    with LOCK:
        for file in (ROOT/str(user)).glob('*/record.json'):
            record = json.loads(file.read_text(encoding='utf-8'))
            if record['status'] in ('running', 'queued') and (user,record['id']) not in ACTIVE:
                record.update(status='failed', message='后端已重启，排队任务未自动提交；如任务此前已运行，请核对远端结果后再生成')
            records.append(record)
    return {'items': sorted(records, key=lambda item:item['created_at'], reverse=True)}


@router.post('/{identity}/cancel-queue')
def cancel_queued(identity: str, request: Request):
    user = int(require_user(request)['id'])
    with LOCK:
        entry = next((e for e in QUEUED if e[0] == user and e[2]['id'] == identity), None)
        if entry is None:
            raise HTTPException(409, '任务已经开始或不在队列中，不能取消排队')
        QUEUED.remove(entry)
        ACTIVE.discard((user, identity))
        record = entry[2]
        record.update(status='cancelled', message='已取消排队，未提交出图')
        save(entry[1], record)
        return record


@router.get('/{identity}/files/{name}')
def image_file(identity: str, name: str, request: Request):
    path = folder(int(require_user(request)['id']), identity)
    record = json.loads((path/'record.json').read_text(encoding='utf-8')) if (path/'record.json').is_file() else {}
    if name not in ['result.png', *record.get('references', [])] or not (path/name).is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(path/name)


@router.delete('')
def remove(request: Request, identity: str = ''):
    user = int(require_user(request)['id'])
    with LOCK:
        if any(u == user and (not identity or i == identity) for u, i in ACTIVE):
            raise HTTPException(409, '请等待当前生成完成后删除历史')
        paths = [folder(user,identity)] if identity else list((ROOT/str(user)).glob('*'))
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
    return {'ok': True}
