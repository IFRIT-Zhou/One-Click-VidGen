"""Private offline CPU worker. Accepts one host-authored bounded JSON request."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def deny_network(*args, **kwargs):
    raise RuntimeError('OCR 推理禁止联网；请显式安装完整组件')


def main():
    # Loading an OCR image must never trigger model/font downloads. Also protect
    # against a future dependency version attempting a network fallback.
    socket.create_connection = deny_network
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    spec = json.loads((ROOT/'tools/vibe_ocr/runtime-spec.json').read_text(encoding='utf-8-sig'))
    for package, version in spec['packages'].items():
        if importlib.metadata.version(package) != version:
            raise ValueError('OCR 依赖版本不匹配：'+package)
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError('OCR 请求过大')
    request = json.loads(raw)
    expected = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    if request.get('runtime_fingerprint') != expected:
        raise ValueError('OCR 组件清单已变化')
    model_dir = ROOT/'models/vibe_ocr'/expected[:16]
    if Path(request['models']).resolve() != model_dir.resolve():
        raise ValueError('OCR 仅使用安装目录内的固定模型')
    for model in spec['models'].values():
        path = model_dir/model['file']
        if path.stat().st_size != model['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != model['sha256']:
            raise ValueError('OCR 模型文件校验失败')
    if request.get('check_only'):
        import onnxruntime
        import rapidocr
        print(json.dumps({'ready': True, 'runtime_fingerprint': expected}))
        return
    path = Path(request['path'])
    if path.stat().st_size > 64 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != request['source_sha256']:
        raise ValueError('OCR 原图已变化或过大')
    from PIL import Image, ImageOps
    import numpy as np
    import cv2
    cv2.setNumThreads(1)
    with Image.open(path) as original:
        if original.width*original.height > 40_000_000 or getattr(original, 'n_frames', 1) != 1:
            raise ValueError('OCR 原图尺寸不支持')
        image = ImageOps.exif_transpose(original).convert('RGB')
    size = list(image.size)
    if size != request['source_size']:
        raise ValueError('OCR 原图方向或尺寸变化')
    region = request.get('region')
    if region is not None and (not isinstance(region, list) or len(region) != 4
            or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in region)
            or region[0] >= region[2] or region[1] >= region[3]):
        raise ValueError('OCR 区域无效')
    crop = ([math.floor(region[0]*size[0]), math.floor(region[1]*size[1]),
             math.ceil(region[2]*size[0]), math.ceil(region[3]*size[1])] if region else [0, 0, *size])
    pixels = np.asarray(image.crop(crop))[:, :, ::-1].copy()
    image.close()
    params = {
        'Global.log_level': 'error', 'Global.text_score': .35,
        'Global.max_side_len': 4096, 'Global.min_side_len': 16,
        'Global.model_root_dir': str(model_dir),
        'Det.model_path': str(model_dir/spec['models']['det']['file']),
        'Cls.model_path': str(model_dir/spec['models']['cls']['file']),
        'Rec.model_path': str(model_dir/spec['models']['rec']['file']),
        'Det.limit_side_len': 1920, 'Det.limit_type': 'max',
        'EngineConfig.onnxruntime.intra_op_num_threads': min(4, os.cpu_count() or 1),
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        'EngineConfig.onnxruntime.use_cuda': False,
        'EngineConfig.onnxruntime.use_dml': False,
    }
    started = time.monotonic()
    with redirect_stdout(sys.stderr):
        from rapidocr import RapidOCR
        engine = RapidOCR(params=params)
        result = engine(pixels)
    boxes, texts, scores = result.boxes, result.txts, result.scores
    rows = []
    if any(value is None for value in (boxes, texts, scores)) and not all(value is None for value in (boxes, texts, scores)):
        raise ValueError('OCR 返回部分缺失，未把它当作空结果')
    if boxes is not None and texts is not None and scores is not None:
        if len(boxes) != len(texts) or len(texts) != len(scores) or len(texts) > 2000:
            raise ValueError('OCR 结果长度无效')
        for box, text, score in zip(boxes, texts, scores):
            x, y = box[:, 0], box[:, 1]
            rows.append(dict(text=text, confidence=float(score),
                box=[max(0., float(min(x))+crop[0]), max(0., float(min(y))+crop[1]),
                     min(float(size[0]), float(max(x))+crop[0]), min(float(size[1]), float(max(y))+crop[1])]))
    print(json.dumps(dict(texts=rows, source_size=size, source_sha256=request['source_sha256'],
        runtime_fingerprint=expected, inference_seconds=round(time.monotonic()-started, 3)), ensure_ascii=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(type(exc).__name__+': '+str(exc)[:400], file=sys.stderr)
        raise SystemExit(1)
