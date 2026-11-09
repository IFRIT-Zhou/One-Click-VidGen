"""Explicit local cutouts through the pinned RMBG node in OCV's managed ComfyUI.

Only the node's mask is accepted. Final RGB always comes from the original crop.
This adapter never starts/stops an engine, installs dependencies, downloads a
model, retries a submission, or changes a project's formal shot.
"""
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import uuid

from PIL import Image, ImageChops, ImageOps
import requests

from .vibe_files import publish
from .vibe_rmbg_model import (
    MODEL_FILES, MODEL_NAME, MODEL_REVISION, RmbgModelError, digest as _digest,
    verify_models as _verify_pinned_models,
)


PLUGIN_COMMIT = 'a66a5e641a54988d80ed6aad18946081c85f309e'
NODE_CLASS = 'OCVBiRefNetRMBG'
PROJECT = Path(__file__).resolve().parents[2]
TRIAL = PROJECT / 'workspace' / 'vibe_rmbg_trial'
MODEL_DIR = TRIAL / 'models' / 'RMBG' / 'BiRefNet'
MAX_SOURCE_PIXELS = 40_000_000
MAX_RESULT_BYTES = 48 * 1024 * 1024


class ComfyMaterialError(RuntimeError):
    pass


class ComfyMaterialStopped(ComfyMaterialError):
    pass


def verify_models(model_dir=MODEL_DIR):
    """Legacy API: preserve its receipt and error type, not a second manifest."""
    try:
        result = _verify_pinned_models(model_dir, files=MODEL_FILES)
    except RmbgModelError as exc:
        raise ComfyMaterialError(str(exc)) from exc
    return {**result, 'plugin_commit': PLUGIN_COMMIT}


def _local_url(base_url):
    value = urlparse(str(base_url))
    if (value.scheme != 'http' or value.hostname != '127.0.0.1' or not value.port
            or value.username or value.password or value.path not in ('', '/')
            or value.query or value.fragment):
        raise ComfyMaterialError('抠图只允许主代理确认的 OCV 内置引擎回环地址')
    return str(base_url).rstrip('/')


def _write_receipt(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    publish(temporary, path, replace=True)


def _check_stop(should_stop):
    if should_stop and should_stop():
        raise ComfyMaterialStopped('已停止本地抠图等待；不会重新提交')


def _prepare_source(source, box):
    path = Path(source).resolve(strict=True)
    with Image.open(path) as file:
        if file.width * file.height > MAX_SOURCE_PIXELS or getattr(file, 'n_frames', 1) != 1:
            raise ComfyMaterialError('抠图输入必须是尺寸受限的单帧图片')
        original = ImageOps.exif_transpose(file).convert('RGBA')
    width, height = original.size
    box = tuple(box) if box is not None else (0, 0, width, height)
    if (len(box) != 4 or any(type(value) is not int for value in box)
            or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
        raise ComfyMaterialError('主体 bbox 必须是原图坐标的有效整数矩形 [左,上,右,下)')
    crop = original.crop(box)
    # Hidden source RGB must not be uploaded even to a local node.
    visible = Image.new('RGB', crop.size, (48, 48, 48))
    visible.paste(crop, mask=crop.getchannel('A'))
    buffer = io.BytesIO()
    visible.save(buffer, 'PNG')
    return path, original.size, box, crop, buffer.getvalue()


def build_workflow(upload_name, token):
    return {
        '1': {'class_type': 'LoadImage', 'inputs': {'image': upload_name}},
        '2': {'class_type': NODE_CLASS, 'inputs': {
            'image': ['1', 0], 'model': MODEL_NAME, 'sensitivity': 1.0,
            'mask_blur': 0, 'mask_offset': 0, 'invert_output': False,
            'refine_foreground': False, 'unload_model': True,
            'background': 'Alpha', 'background_color': '#222222'}},
        '3': {'class_type': 'SaveImage', 'inputs': {
            'images': ['2', 2], 'filename_prefix': 'vibe_rmbg/mask_' + token}},
    }


def _cancel_prompt(session, base, prompt_id):
    """Use the core's atomic job cancel; legacy fallback only deletes our queue item.

    The legacy interrupt endpoint checks a snapshot before setting a global
    flag, so even its prompt_id option can interrupt the next unrelated job.
    Never call it. Failed/unsupported atomic cancellation leaves running work
    to finish naturally instead of risking another user's generation.
    """
    reason = 'unconfirmed_response'
    try:
        response = session.post(base + '/api/jobs/' + prompt_id + '/cancel',
                                json={}, timeout=(3, 10), allow_redirects=False)
        if response.status_code in (404, 405, 501):
            reason = 'unsupported_endpoint'
        else:
            response.raise_for_status()
            payload = response.json()
            if (200 <= response.status_code < 300 and isinstance(payload, dict)
                    and type(payload.get('cancelled')) is bool):
                return {'method': 'atomic_job_cancel', 'cancelled': payload['cancelled'],
                        'pending_delete_requested': False,
                        'running_must_finish_naturally': False}
    except (requests.RequestException, ValueError):
        reason = 'atomic_cancel_request_failed'
    pending_delete_requested = False
    try:
        response = session.post(base + '/queue', json={'delete': [prompt_id]},
                                timeout=(3, 10), allow_redirects=False)
        response.raise_for_status()
        pending_delete_requested = 200 <= response.status_code < 300
    except requests.RequestException:
        pass
    return {'method': 'pending_delete_only', 'cancelled': False, 'reason': reason,
            'pending_delete_requested': pending_delete_requested,
            'running_must_finish_naturally': True}


def _download_mask(session, base, output):
    if not isinstance(output, dict) or output.get('type', 'output') != 'output':
        raise ComfyMaterialError('RMBG 未返回输出区蒙版')
    params = {key: output.get(key, '') for key in ('filename', 'subfolder', 'type')}
    if not params['filename'] or Path(params['filename']).name != params['filename']:
        raise ComfyMaterialError('RMBG 蒙版文件名无效')
    response = session.get(base + '/view', params=params, timeout=(5, 60), stream=True)
    response.raise_for_status()
    data = bytearray()
    try:
        for chunk in response.iter_content(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_RESULT_BYTES:
                raise ComfyMaterialError('RMBG 蒙版文件超过安全大小')
    finally:
        response.close()
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_SOURCE_PIXELS:
            raise ComfyMaterialError('RMBG 蒙版尺寸超过安全限制')
        rgb = image.convert('RGB')
        r, g, b = rgb.split()
        if ImageChops.difference(r, g).getbbox() or ImageChops.difference(r, b).getbbox():
            raise ComfyMaterialError('RMBG 返回的不是灰度蒙版，未采用模型颜色')
        return r.copy()


def _run_connected(source, output_dir, *, base_url, bbox=None, asset_id='subject',
                   should_stop=None, timeout=600, poll_interval=1, session,
                   on_submitted=None, require_idle=True, trim_padding=None):
    base = _local_url(base_url)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', asset_id):
        raise ComfyMaterialError('素材编号无效')
    if not 0 < timeout <= 1800 or not 0 <= poll_interval <= 10:
        raise ComfyMaterialError('抠图等待参数无效')
    if trim_padding is not None and (type(trim_padding) is not int or not 0 <= trim_padding <= 256):
        raise ComfyMaterialError('透明裁紧边距必须是 0 到 256 的整数像素')
    started = time.monotonic()
    _check_stop(should_stop)
    source, source_size, box, crop, upload = _prepare_source(source, bbox)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    receipt_path = output_dir / f'{asset_id}-{token}.json'
    receipt = dict(status='preparing', task_id=token, engine='managed-comfyui-rmbg',
                   source_path=str(source), source_sha256=_digest(source), source_size=list(source_size),
                   crop_box_px=list(box), asset_id=asset_id, model=MODEL_NAME,
                   model_revision=MODEL_REVISION, plugin_commit=PLUGIN_COMMIT,
                   may_charge=False, requires_review=True, original_rgb_preserved=True)
    _write_receipt(receipt_path, receipt)
    prompt_id = None
    try:
        nodes_response = session.get(base + '/object_info', timeout=(5, 30))
        nodes_response.raise_for_status()
        available = nodes_response.json()
        missing = {'LoadImage', 'SaveImage', NODE_CLASS} - set(available)
        if missing:
            raise ComfyMaterialError('内置 ComfyUI 未加载所需抠图节点：' + '、'.join(sorted(missing)))
        if require_idle:
            queue_response = session.get(base + '/queue', timeout=(5, 15))
            queue_response.raise_for_status()
            queue = queue_response.json()
            if not isinstance(queue, dict) or any(not isinstance(queue.get(key), list)
                    for key in ('queue_running', 'queue_pending')):
                raise ComfyMaterialError('无法确认内置引擎队列空闲，未提交抠图')
            if queue.get('queue_running') or queue.get('queue_pending'):
                raise ComfyMaterialError('内置 ComfyUI 正在运行或排队；请等待 H3/其他任务结束后再试')
        _check_stop(should_stop)
        response = session.post(base + '/upload/image',
            files={'image': (f'vibe-rmbg-{token}.png', upload, 'image/png')},
            data={'type': 'input', 'overwrite': 'false'}, timeout=(5, 60))
        response.raise_for_status()
        uploaded = response.json()
        name = str(uploaded.get('name') or '')
        if not name or Path(name).name != name:
            raise ComfyMaterialError('内置引擎没有返回有效的唯一输入文件名')
        subfolder = str(uploaded.get('subfolder') or '').strip('/\\')
        upload_name = f'{subfolder}/{name}' if subfolder else name
        workflow = build_workflow(upload_name, token)
        receipt.update(status='submitting', upload_name=upload_name)
        _write_receipt(receipt_path, receipt)
        _check_stop(should_stop)
        response = session.post(base + '/prompt',
            json={'prompt': workflow, 'client_id': 'ocv-rmbg-' + token}, timeout=(5, 60))
        response.raise_for_status()
        prompt_id = str(response.json().get('prompt_id') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', prompt_id):
            raise ComfyMaterialError('提交结果未确认：引擎未返回 prompt_id；禁止自动重提')
        receipt.update(status='running', prompt_id=prompt_id)
        _write_receipt(receipt_path, receipt)
        if on_submitted:
            on_submitted(prompt_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _check_stop(should_stop)
            try:
                response = session.get(base + '/history/' + prompt_id, timeout=(5, 30))
                response.raise_for_status()
                history = response.json().get(prompt_id)
            except requests.Timeout:
                # Re-reading a known prompt is safe; re-submitting is not.
                continue
            if isinstance(history, dict):
                status = history.get('status') or {}
                if status.get('status_str') in {'error', 'failed', 'interrupted'}:
                    raise ComfyMaterialError('RMBG 节点执行失败；请查看内置引擎日志，未自动重提')
                images = history.get('outputs', {}).get('3', {}).get('images') or []
                if images:
                    mask = _download_mask(session, base, images[0])
                    if mask.size != crop.size:
                        raise ComfyMaterialError('RMBG 蒙版与原裁片尺寸不一致，未自动拉伸')
                    alpha = ImageChops.multiply(mask, crop.getchannel('A'))
                    alpha_box = alpha.getbbox()
                    if alpha_box is None:
                        raise ComfyMaterialError('RMBG 未检测到主体；未写入空图层')
                    rgba = crop.copy()
                    rgba.putalpha(alpha)
                    trim_box = (0, 0, crop.width, crop.height)
                    if trim_padding is not None:
                        trim_box = (max(0, alpha_box[0] - trim_padding),
                                    max(0, alpha_box[1] - trim_padding),
                                    min(crop.width, alpha_box[2] + trim_padding),
                                    min(crop.height, alpha_box[3] + trim_padding))
                        rgba = rgba.crop(trim_box)
                        alpha = alpha.crop(trim_box)
                    rgba_path = output_dir / f'{asset_id}-{token}.png'
                    mask_path = output_dir / f'{asset_id}-{token}-mask.png'
                    rgba.save(rgba_path, 'PNG')
                    alpha.save(mask_path, 'PNG')
                    receipt.update(status='completed', path=str(rgba_path), mask_path=str(mask_path),
                        alpha_bbox_px=list(alpha.getbbox()),
                        source_alpha_bbox_px=[alpha_box[0]+box[0], alpha_box[1]+box[1],
                                              alpha_box[2]+box[0], alpha_box[3]+box[1]],
                        trim_padding_px=trim_padding, trim_box_in_crop_px=list(trim_box),
                        output_box_in_source_px=[trim_box[0]+box[0], trim_box[1]+box[1],
                                                 trim_box[2]+box[0], trim_box[3]+box[1]],
                        output_size_px=list(rgba.size), elapsed_seconds=round(time.monotonic()-started, 3),
                        mask_has_background=alpha.getextrema()[0] < 250)
                    _write_receipt(receipt_path, receipt)
                    return {**receipt, 'receipt_path': str(receipt_path)}
            time.sleep(poll_interval)
        raise TimeoutError('RMBG 本地等待超时；不会重新提交')
    except Exception as exc:
        if isinstance(exc, (ComfyMaterialStopped, TimeoutError)) and prompt_id:
            receipt['cancellation'] = _cancel_prompt(session, base, prompt_id)
            receipt['targeted_cancel_confirmed'] = receipt['cancellation']['cancelled']
        receipt['status'] = 'stopped' if isinstance(exc, ComfyMaterialStopped) else 'failed'
        receipt['message'] = str(exc) if isinstance(exc, (ComfyMaterialError, TimeoutError)) else '本地引擎请求失败；未自动重提，请核对任务回执'
        if receipt.get('cancellation', {}).get('running_must_finish_naturally'):
            receipt['message'] += '；未确认安全的原子取消，仅尝试删除本任务的排队项；若已在运行，需等待自然结束，未发送全局中断'
        _write_receipt(receipt_path, receipt)
        if isinstance(exc, requests.RequestException):
            raise ComfyMaterialError(receipt['message']) from None
        if isinstance(exc, (ComfyMaterialStopped, TimeoutError)):
            raise type(exc)(receipt['message']) from None
        raise


def run_connected_cutout(source, output_dir, *, base_url, model_dir=MODEL_DIR, **kwargs):
    """Prototype caller owns engine lifecycle; this never starts/stops ComfyUI.

    Use only after obtaining the running managed engine URL from OCV. This is
    not an external-ComfyUI fallback and does not acquire the backend's lease.
    Source/output paths are trusted local prototype inputs, not public API
    parameters; a production route must add project ownership/path validation.
    """
    if Path(model_dir).resolve() != MODEL_DIR.resolve():
        raise ComfyMaterialError('固定试验节点只使用已登记的 RMBG 模型目录')
    verify_models(model_dir)
    with requests.Session() as session:
        session.trust_env = False
        return _run_connected(source, output_dir, base_url=base_url, session=session, **kwargs)


def run_cutout(source, output_dir, *, model_dir=MODEL_DIR, **kwargs):
    """Backend integration entry; the owner must explicitly start the engine."""
    from . import managed_comfyui as engine
    if Path(model_dir).resolve() != MODEL_DIR.resolve():
        raise ComfyMaterialError('固定试验节点只使用已登记的 RMBG 模型目录')
    verify_models(model_dir)
    if engine.status()['state'] != 'ready':
        raise ComfyMaterialError('请先由用户启动 OCV 内置引擎；抠图不会自行启动或重启')
    with engine.execution_lease(required_models=[], required_nodes={'LoadImage', 'SaveImage', NODE_CLASS}) as base:
        with requests.Session() as session:
            session.trust_env = False
            return _run_connected(source, output_dir, base_url=base, session=session, **kwargs)
