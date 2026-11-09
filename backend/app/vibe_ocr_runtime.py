"""Bounded, offline RapidOCR CPU adapter. No service, GPU or implicit installs.

Inputs are already-authorized paths selected by the host, never paths chosen by
the model. OCR boxes are detection estimates, NOT verified pixel-perfect ink.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'ocv-rapidocr-cpu-v1'
MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_TEXTS = 2000
_SLOT = threading.BoundedSemaphore(1)
_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def read_spec(root=ROOT):
    path = Path(root) / 'tools/vibe_ocr/runtime-spec.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != VERSION or set(data.get('models', {})) != {'det', 'rec', 'cls'}:
        raise ValueError('OCR 组件清单无效，请更新组件程序')
    for model in data['models'].values():
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+\.onnx', model['file'])
                or not re.fullmatch(r'[a-f0-9]{64}', model['sha256'])
                or type(model['bytes']) is not int or not 0 < model['bytes'] <= 100 * 1024 * 1024):
            raise ValueError('OCR 权重清单无效')
    return data


def fingerprint(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


@lru_cache(maxsize=24)
def _hash_file(path, size, modified_ns):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_models(root, spec):
    folder = Path(root) / 'models/vibe_ocr' / fingerprint(spec)[:16]
    for model in spec['models'].values():
        path = folder / model['file']
        stat = path.stat()
        if stat.st_size != model['bytes'] or _hash_file(str(path), stat.st_size, stat.st_mtime_ns) != model['sha256']:
            raise ValueError('OCR 权重缺失或校验失败，请运行本地 OCR 安装/修复')
    return folder


def resolve_runtime(root=ROOT):
    root = Path(root)
    spec = read_spec(root)
    active = json.loads((root / 'runtime/vibe_ocr/active.json').read_text(encoding='utf-8-sig'))
    if not isinstance(active, dict):
        raise ValueError('OCR 独立环境登记无效，请运行本地 OCR 安装/修复')
    version = active.get('version', '')
    if not re.fullmatch(r'ocr-[a-f0-9]{12}-[a-f0-9]{12}', version) or active.get('fingerprint') != fingerprint(spec):
        raise ValueError('OCR 独立环境版本不匹配，请运行本地 OCR 安装/修复')
    python = root / 'runtime/vibe_ocr/versions' / version / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    worker = root / 'tools/vibe_ocr/worker.py'
    if not python.is_file() or not worker.is_file():
        raise ValueError('OCR 独立环境或组件程序不存在，请安装本地 OCR')
    return {'python': python, 'worker': worker, 'models': verify_models(root, spec), 'spec': spec,
            'fingerprint': fingerprint(spec)}


def normalized_region(value):
    if value is None:
        return None
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        raise ValueError('OCR 复核区域须为原图归一化 [left,top,right,bottom]，且有正面积')
    return [float(v) for v in value]


def _cancelled(cancelled):
    if cancelled and cancelled():
        raise InterruptedError('已停止本地 OCR 测量')


def _run(runtime, payload, cancelled):
    """Only our bundled worker is executable; its inference sockets are blocked."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PYTHON', 'CUDA', 'ORT_'))}
    env.update(PYTHONUTF8='1', PYTHONNOUSERSITE='1', CUDA_VISIBLE_DEVICES='',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='4')
    started = time.monotonic()
    process = subprocess.Popen([str(runtime['python']), '-I', '-B', '-X', 'utf8', str(runtime['worker'])],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(runtime['worker'].parent), env=env, creationflags=_FLAGS)
    try:
        data = json.dumps(payload, ensure_ascii=True).encode('utf-8')
        while True:
            _cancelled(cancelled)
            if time.monotonic() - started > min(90, runtime['spec'].get('timeout_seconds', 90)):
                raise TimeoutError('本地 OCR 超时，未把未完成识别当作定位依据')
            try:
                stdout, stderr = process.communicate(input=data, timeout=.2)
                break
            except subprocess.TimeoutExpired:
                data = None
        if process.returncode:
            # Worker errors are bounded JSON/text, not model/provider secrets.
            raise RuntimeError('本地 OCR 运行失败：' + stderr.decode('utf-8', 'replace').strip()[-400:])
        if len(stdout) > 4 * 1024 * 1024:
            raise ValueError('OCR 返回数据过大')
        return json.loads(stdout)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def _validated_texts(rows, size):
    if not isinstance(rows, list) or len(rows) > MAX_TEXTS:
        raise ValueError('OCR 文字列表无效')
    output = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('OCR 文字项目无效，未使用这份识别结果')
        text, box, confidence = row.get('text'), row.get('box'), row.get('confidence')
        if (not isinstance(text, str) or not text.strip() or len(text) > 2000
                or not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= size[0] or not 0 <= box[1] < box[3] <= size[1]
                or type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError('OCR 坐标或置信度无效，未使用这份识别结果')
        output.append(dict(id='ocr_'+str(len(output)), text=text, box=[round(float(v), 3) for v in box],
                           confidence=float(confidence)))
    return output


def analyze_image(path: Path, cache_dir: Path, *, region=None, cancelled=None) -> dict:
    """Return full-source pixel coordinates, even when inspecting a local ROI.

    A ready report can have zero texts, but never declares that the source has
    no text. Unavailable/error reports are not cached, so installation can be
    picked up without restarting the OCV application.
    """
    started = time.monotonic()
    _cancelled(cancelled)
    region = normalized_region(region)
    base = dict(version=VERSION, backend='RapidOCR/ONNXRuntime-CPU', status='unavailable',
                coordinate_space='source_pixels', region=region, texts=[], cached=False,
                confidence_kind='recognition_confidence_not_coordinate_accuracy',
                source_sha256=None, source_size=None)
    try:
        path = Path(path).resolve(strict=True)
        stat = path.stat()
        if not 0 < stat.st_size <= MAX_BYTES:
            raise ValueError('OCR 原图文件过大或为空')
        # Hash the original bytes on each call; cache must not survive source edits.
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('OCR 原图尺寸过大或包含多个画面')
            oriented = ImageOps.exif_transpose(image)
            size = list(oriented.size)
            if oriented is not image:
                oriented.close()
        base.update(source_sha256=source_hash, source_size=size)
        runtime = resolve_runtime()
        key = hashlib.sha256(json.dumps([VERSION, source_hash, runtime['fingerprint'], region]).encode()).hexdigest()
        cache = Path(cache_dir) / ('ocr_'+key+'.json')
        if cache.is_file() and cache.stat().st_size <= 4 * 1024 * 1024:
            try:
                saved = json.loads(cache.read_text(encoding='utf-8'))
                if (isinstance(saved, dict) and saved.get('cache_key') == key and saved.get('source_sha256') == source_hash
                        and saved.get('runtime_fingerprint') == runtime['fingerprint']
                        and saved.get('source_size') == size and saved.get('region') == region
                        and saved.get('status') == 'ready' and saved.get('coordinate_space') == 'source_pixels'):
                    saved['texts'] = _validated_texts(saved['texts'], size)
                    saved.update(cached=True, elapsed_seconds=round(time.monotonic()-started, 3))
                    return saved
            except (OSError, ValueError, TypeError, KeyError):
                pass
        acquired = False
        queue_started = time.monotonic()
        try:
            while not acquired:
                _cancelled(cancelled)
                if time.monotonic()-queue_started > 30:
                    raise TimeoutError('本地 OCR 正忙，测量排队超时；可稍后复核')
                acquired = _SLOT.acquire(timeout=.2)
            _cancelled(cancelled)
            result = _run(runtime, dict(path=str(path), region=region,
                source_sha256=source_hash, source_size=size, models=str(runtime['models']),
                runtime_fingerprint=runtime['fingerprint']), cancelled)
        finally:
            if acquired:
                _SLOT.release()
        if (not isinstance(result, dict) or result.get('source_sha256') != source_hash or result.get('source_size') != size
                or result.get('runtime_fingerprint') != runtime['fingerprint']):
            raise ValueError('OCR 运行期间原图或组件版本变化，请重新测量')
        base.update(status='ready', texts=_validated_texts(result.get('texts'), size),
                    runtime_fingerprint=runtime['fingerprint'], cache_key=key,
                    model_version='PP-OCRv6-small',
                    elapsed_seconds=round(time.monotonic()-started, 3),
                    inference_seconds=result.get('inference_seconds'),
                    note='OCR 框是检测估计，置信度是识别置信度，不是单像素精度保证；空结果也不证明原图没有文字。')
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temp.write_text(json.dumps(base, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temp.replace(cache)
        finally:
            temp.unlink(missing_ok=True)
        return deepcopy(base)
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        base.update(reason=str(exc)[:500], elapsed_seconds=round(time.monotonic()-started, 3),
                    setup_hint='使用 tools/vibe_ocr/setup_runtime.py --check 检查；缺失时由用户显式执行 --install。推理不会自行下载。')
        return base
