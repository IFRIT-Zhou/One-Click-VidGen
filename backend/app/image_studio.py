"""Independent single-image workspace. One submission, no automatic retries."""
import base64
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

router = APIRouter(prefix='/api/image-studio')
ROOT = Path(__file__).resolve().parents[2] / 'workspace' / 'image_studio'
LOCK = threading.RLock()
ACTIVE: set[int] = set()


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    provider: Literal['custom', 'pool'] = 'custom'
    ratio: Literal['2:1', '16:9', '1:1', '9:16'] = '2:1'
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
                      api_key=runtime['access_token'], cloud_base_url=base, cloud_pool='1')
    else:
        configs = visual._provider_configs()
        if not configs:
            raise ValueError('请先在接口与服务中保存图像 API 配置')
        config = dict(configs[0])
    config['ratio'] = data.ratio
    from .config import _parse_env_lines
    saved = _parse_env_lines(Path(__file__).resolve().parents[2] / '.env')
    config['resolution'] = data.resolution or saved.get('IMAGE_RESOLUTION') or saved.get('RUNNINGHUB_RESOLUTION') or config.get('resolution') or '1k'
    config['endpoint'] = visual._runninghub_generate_url(config)
    config['query_url'] = config.get('query_url') or visual._runninghub_url('/openapi/v2/query')
    return config, client


def execute(user: int, path: Path, record: dict, config: dict) -> None:
    import module4_video_render as visual
    try:
        payload = dict(prompt=record['prompt'], aspectRatio=record['ratio'], resolution=record['resolution'])
        if config.get('model'):
            payload['model'] = config['model']
        endpoint = config['endpoint']
        if record['references']:
            payload['imageUrls'] = [
                'data:'+ (mimetypes.guess_type(name)[0] or 'image/png') + ';base64,' +
                base64.b64encode((path/name).read_bytes()).decode('ascii') for name in record['references']
            ]
            endpoint = endpoint.replace('/text-to-image', '/image-to-image')
        if config.get('cloud_pool') == '1':
            payload['clientJobId'] = 'image-studio-' + record['id']
        headers = {'Authorization': 'Bearer '+config['api_key']}
        with requests.Session() as session:
            response = session.post(endpoint, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            result = response.json()
            task_id = visual._find_first_key(result, {'taskId', 'taskID', 'id'})
            if not task_id:
                raise RuntimeError(visual._runninghub_error_message(result) or '服务未返回任务 ID，提交结果尚未确认')
            record['remote_task_id'] = str(task_id)
            record['message'] = '已提交，等待图片生成'
            save(path, record)
            deadline = time.monotonic() + 1200
            while time.monotonic() < deadline:
                response = session.post(config.get('query_url') or visual._runninghub_url('/openapi/v2/query'),
                                        json={'taskId': task_id}, headers=headers, timeout=60)
                response.raise_for_status()
                result = response.json()
                status = str(visual._find_first_key(result, {'status', 'state', 'taskStatus'}) or '').upper()
                url = visual._find_image_url(result, base_url=config.get('cloud_base_url'))
                if status in {'FAILED','FAILURE','ERROR','CANCELLED','CANCELED','REJECTED','BLOCKED','ABORTED','TERMINATED','TIMEOUT','TIMED_OUT','EXPIRED'}:
                    raise RuntimeError(f"{status} / {visual._runninghub_result_error_code(result)}: " + (visual._runninghub_error_message(result) or '服务端未提供详细原因'))
                if url:
                    downloaded = session.get(url, timeout=120)
                    downloaded.raise_for_status()
                    from PIL import Image
                    import io
                    with Image.open(io.BytesIO(downloaded.content)) as picture:
                        picture.load()
                        record['width'], record['height'] = picture.size
                        picture.save(path/'result.png', format='PNG')
                    record.update(status='completed', message='生成完成')
                    break
                time.sleep(3)
            else:
                raise TimeoutError('等待超时，远端结果尚未确认，请核对服务端记录后再生成')
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, requests.RequestException):
            response = exc.response
            detail = ''
            if response is not None:
                try:
                    detail = visual._runninghub_error_message(response.json())
                except ValueError:
                    pass
            message = f'网络或接口错误：{type(exc).__name__}；{detail or message}。结果尚未确认，请核对扣费后再生成。'
        record.update(status='failed', message=message)
    finally:
        try:
            save(path, record)
        finally:
            with LOCK:
                ACTIVE.discard(user)


@router.post('')
def create(data: ImageRequest, request: Request):
    user = int(require_user(request)['id'])
    if not data.prompt.strip():
        raise HTTPException(400, '请填写提示词')
    with LOCK:
        if user in ACTIVE:
            raise HTTPException(409, '图片工作室已有图片正在生成')
        ACTIVE.add(user)
    path = None
    try:
        config, _ = config_for(user, data)
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
                      prompt=data.prompt, provider=data.provider, ratio=data.ratio,
                      resolution=config['resolution'], references=names)
        save(path, record)
        threading.Thread(target=execute, args=(user,path,record,config), daemon=True).start()
        return record
    except Exception as exc:
        with LOCK:
            ACTIVE.discard(user)
        raise HTTPException(400, str(exc)) from exc


@router.get('')
def history(request: Request):
    user = int(require_user(request)['id'])
    records = []
    with LOCK:
        for file in (ROOT/str(user)).glob('*/record.json'):
            record = json.loads(file.read_text(encoding='utf-8'))
            if record['status'] == 'running' and user not in ACTIVE:
                record.update(status='failed', message='后端已重启，远端结果尚未确认，请核对扣费后再生成')
            records.append(record)
    return {'items': sorted(records, key=lambda item:item['created_at'], reverse=True)}


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
        if user in ACTIVE:
            raise HTTPException(409, '请等待当前生成完成后删除历史')
        paths = [folder(user,identity)] if identity else list((ROOT/str(user)).glob('*'))
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
    return {'ok': True}
