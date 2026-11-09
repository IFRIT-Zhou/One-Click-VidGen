"""Trusted local host for bounded, model-authored JSX scene programs.

The model never receives filesystem paths. Node compiles a restricted AST;
the trusted browser interpreter, not eval/Function, executes that AST.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from PIL import Image

from . import vibe_remotion as host

VERSION = 'ocv-code-program-v1'
RENDERER = 'ocv-remotion-code-v1'


def preflight(*, cancelled=None, timeout=40):
    node, browser = host._runtime_paths()
    script = host.ENGINE / 'render-code.mjs'
    if not script.is_file():
        raise RuntimeError('Vibe 代码渲染组件尚未安装；本次未调用模型')
    result = host._run([str(node), str(script), '--check', '--browser', str(browser)],
                       timeout=timeout, cancelled=cancelled)
    try:
        info = json.loads(result.strip().splitlines()[-1])
        if (info.get('ready') is not True or RENDERER not in info.get('renderers', [])
                or VERSION not in info.get('plan_versions', [])):
            raise ValueError('missing capability')
    except (ValueError, IndexError, TypeError):
        raise RuntimeError('Vibe 代码渲染组件预检失败；本次未调用模型') from None
    return {'node': str(node), 'browser': str(browser), 'renderer': RENDERER}


def runtime_geometry(manifest, assets):
    """Translate verified normalized source anchors to the actual canvas pixels."""
    width, height = manifest['canvas']
    result = {}
    for row in manifest['subjects']:
        box = row.get('source_anchor_box')
        value = {'prepared_size': row['prepared_size'],
                 'anchor_available': row['anchor_available'],
                 'anchor_unavailable_reason': row.get('anchor_unavailable_reason')}
        if box and row['anchor_available']:
            left, top, right, bottom = box
            value['box'] = [left*width, top*height, (right-left)*width, (bottom-top)*height]
        for key in (row['asset_id'], row['asset_id']+'_original'):
            if key in assets:
                result[key] = dict(value)
    return result


def _json(path, limit=2_000_000):
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise RuntimeError('代码渲染器没有输出有效的 '+path.name)
    return json.loads(path.read_text(encoding='utf-8'))


def run(*, mode, code, canvas, duration, assets, geometry, output_dir,
        measurements=None, measure_requests=None, cancelled=None, timeout=240,
        preview_level='full'):
    if mode not in ('measure', 'preview', 'video'):
        raise ValueError('未知代码渲染操作')
    if preview_level not in ('layout', 'full'):
        raise ValueError('代码预览级别须为 layout 或 full')
    if mode == 'video':
        preview_level = 'full'
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 1 <= duration <= 60:
        raise ValueError('代码动效时长须为 1–60 秒')
    if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
            or any(type(v) is not int or not 64 <= v <= 1920 or v % 2 for v in canvas)
            or canvas[0] * canvas[1] > 1920 * 1080):
        raise ValueError('代码动效画布尺寸无效')
    if not isinstance(code, str) or len(code) > 45000:
        raise ValueError('代码大小超过限额')
    output_dir = Path(output_dir)
    if not output_dir.is_dir() or any(output_dir.iterdir()):
        raise ValueError('代码渲染目标必须是独立空目录')
    paths = {key: Path(value).resolve() for key, value in assets.items()}
    if not paths.get('background') or any(not p.is_file() for p in paths.values()):
        raise ValueError('代码渲染所需的已授权素材缺失')
    node, browser = host._runtime_paths()
    input_path = output_dir.parent / (output_dir.name+'-input.json')
    input_path.write_text(json.dumps(dict(mode=mode, preview_level=preview_level, code=code, canvas=list(canvas),
        duration=duration, fps=30, assets={k: str(p) for k, p in paths.items()},
        geometry=geometry, measurements=measurements or {}, measure_requests=measure_requests or []),
        ensure_ascii=False), encoding='utf-8')
    try:
        host._run([str(node), str(host.ENGINE/'render-code.mjs'), '--input', str(input_path),
                   '--output', str(output_dir), '--browser', str(browser)], timeout=timeout,
                  cancelled=cancelled, log=output_dir.parent/(output_dir.name+'-render.log'))
    except RuntimeError as exc:
        # Only model-actionable contract errors enter the paid rework loop.
        # Broken Chromium/dependencies, disk and IPC failures must stop here.
        match = re.search(r'OCV_CODE_(?:CONTRACT|CONTENT):[^\r\n]*', str(exc))
        if match:
            raise ValueError(match.group(0)[:1800]) from None
        raise
    if cancelled and cancelled():
        raise InterruptedError('已停止代码动效预览')
    measured = _json(output_dir/'measurements.json')
    if mode == 'measure':
        return {'measurements': measured}
    metadata = _json(output_dir/'metadata.json')
    if (metadata.get('renderer') != RENDERER or metadata.get('plan_version') != VERSION
            or (metadata.get('width'), metadata.get('height')) != tuple(canvas)):
        raise ValueError('代码渲染回执与所需组件或画布不一致')
    full_proof = preview_level == 'full'
    if (metadata.get('preview_level') != preview_level
            or metadata.get('proof_complete') is not full_proof
            or metadata.get('checks', {}).get('animation') != ('checked' if full_proof else 'not_checked')):
        raise ValueError('代码渲染回执与所需预览级别不一致；快速校样不能作为完整动效证明')
    stages = metadata.get('stage_frames', [])
    measured_frames = measured.get('frames', [])
    if (not isinstance(stages, list) or (not full_proof and stages)
            or (full_proof and (len(stages) != 4
                or {row.get('id') for row in stages if isinstance(row, dict)} != {'early', 'middle', 'hold', 'end'}))):
        raise ValueError('代码渲染关键帧证明不完整或与预览级别不一致')
    if (not isinstance(measured_frames, list) or (not full_proof and measured_frames)
            or (full_proof and (len(measured_frames) != 4
                or {row.get('id') for row in measured_frames if isinstance(row, dict)} != {'early', 'middle', 'hold', 'end'}))):
        raise ValueError('代码渲染关键帧测量不完整或与预览级别不一致')
    with Image.open(output_dir/'poster.png') as image:
        if image.format != 'PNG' or image.size != tuple(canvas):
            raise ValueError('代码预览海报尺寸不一致')
        image.verify()
    metadata['measurements'] = measured
    if metadata.get('stage_frames'):
        metadata['proof_times'] = host._proof(output_dir, metadata)
    if mode == 'video':
        if metadata.get('frames') != math.floor(duration*30+.5) or metadata.get('fps') != 30:
            raise ValueError('代码视频帧数与最终配音时间轴不一致')
        if not (output_dir/'silent.mp4').is_file():
            raise RuntimeError('代码渲染未生成完整视频')
    return metadata


def mux(*, output_dir, audio, start, duration, ffmpeg, cancelled=None):
    output_dir, audio = Path(output_dir), Path(audio)
    if (type(start) not in (int, float) or not math.isfinite(start) or start < 0
            or not audio.is_file() or start+duration > host._audio_length(audio)+.001):
        raise ValueError('最终配音未覆盖整个代码镜头')
    preview = output_dir/'preview.mp4'
    host._run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
        '-i', str(output_dir/'silent.mp4'), '-ss', f'{start:.6f}', '-t', f'{duration:.6f}',
        '-protocol_whitelist', 'file', '-f', 'wav', '-i', str(audio),
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
        '-movflags', '+faststart', str(preview)], timeout=90, cancelled=cancelled)
    if not preview.is_file() or not preview.stat().st_size:
        raise RuntimeError('代码动效原配音封装失败')
    return preview
