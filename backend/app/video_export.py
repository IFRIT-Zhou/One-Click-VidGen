"""Local final assembly for dynamic-video projects."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import video_studio as studio
from .pipeline import (ffmpeg_binary, ffprobe_binary, probe_media_duration, _subtitle_filter_path,
                       persist_step_workflow_state, register_job_asset, store)

router = APIRouter(prefix='/api/video-studio')
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ExportRequest(BaseModel):
    revision: int
    use_video_audio: bool = True


def _run(command, label):
    completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or 'unknown error').strip()[-3000:]
        raise RuntimeError(f'{label}失败：{detail}')


def _size(record):
    return (1080, 1920) if record.get('settings', {}).get('ratio') == '9:16' else (1920, 1080)


def _safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(value or '动态视频')).strip(' .')
    return value[:80] or '动态视频'


def _asset(path: Path, relative: str) -> Path:
    result = (path / str(relative)).resolve()
    if path.resolve() not in result.parents:
        raise ValueError('动态视频资产路径无效')
    if not result.is_file() or result.stat().st_size <= 0:
        raise ValueError(f'动态视频资产不存在：{relative}')
    return result


def _source(path, shot):
    relative = shot.get('video') if shot.get('kind') == 'video' else shot.get('image')
    if not relative:
        raise ValueError(f'镜头 {shot.get("id")} 缺少{"视频" if shot.get("kind") == "video" else "图片"}资产')
    return _asset(path, relative)


def _has_audio_stream(path: Path) -> bool:
    completed = subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-select_streams', 'a:0',
         '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    return completed.returncode == 0 and bool((completed.stdout or '').strip())


def _ass_colour(value, default):
    match = re.fullmatch(r'#?([0-9a-fA-F]{6})', str(value or ''))
    if not match:
        return default
    rgb = match.group(1)
    return '&H00' + rgb[4:6] + rgb[2:4] + rgb[0:2]


def _subtitle_style(record, height):
    params = record.get('creation_parameters') or {}
    orientation = 'portrait' if record.get('settings', {}).get('ratio') == '9:16' else 'landscape'
    defaults = dict(font='Microsoft YaHei', size=56 if orientation == 'portrait' else 36,
                    position=75 if orientation == 'portrait' else 95, color='#ffffff',
                    outline_color='#000000', outline=2, background=True, opacity=.75)
    defaults.update((params.get('subtitle_layouts') or {}).get(orientation) or {})
    font = str(defaults['font']).replace("'", '') or 'Microsoft YaHei'
    margin = max(10, round(height * max(0, 100-float(defaults['position'])) / 100))
    primary = _ass_colour(defaults['color'], '&H00FFFFFF')
    outline = _ass_colour(defaults['outline_color'], '&H00000000')
    border = 3 if defaults.get('background') else 1
    return (f"FontName={font},FontSize={float(defaults['size']):g},Bold=1,Alignment=2,MarginV={margin},"
            f"PrimaryColour={primary},OutlineColour={outline},BorderStyle={border},"
            f"Outline={float(defaults['outline']):g},Shadow=0")


def _render(path, record):
    path = path.resolve()
    width, height = _size(record)
    output_root = PROJECT_ROOT / 'output' / (_safe_name(record['settings'].get('name')) + '_动态视频')
    work = path / 'export_work'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    output_root.mkdir(parents=True, exist_ok=True)
    clips = []
    use_video_audio = bool((record.get('export_settings') or {}).get('use_video_audio', True))
    vf = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p'
    for index, shot in enumerate(record['shots'], 1):
        source = _source(path, shot)
        duration = float(shot['end']) - float(shot['start'])
        clip = work / f'{index:04d}.mp4'
        command = [ffmpeg_binary(), '-y']
        if shot['kind'] == 'static':
            command += ['-loop', '1', '-i', str(source)]
        else:
            if shot.get('video_status') != 'completed':
                raise ValueError(f'第 {index} 镜动态片段尚未完成')
            command += ['-i', str(source)]
        keep_source_audio = bool(use_video_audio and shot['kind'] == 'video' and _has_audio_stream(source))
        if keep_source_audio:
            command += ['-map', '0:v:0', '-map', '0:a:0', '-af',
                        'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad']
        else:
            command += ['-f', 'lavfi', '-t', f'{duration:.3f}', '-i', 'anullsrc=r=48000:cl=stereo',
                        '-map', '0:v:0', '-map', '1:a:0']
        command += ['-t', f'{duration:.3f}', '-vf', vf, '-c:v', 'libx264', '-preset', 'fast',
                    '-crf', '18', '-pix_fmt', 'yuv420p', '-r', '30', '-c:a', 'aac', '-b:a', '192k', str(clip)]
        _run(command, f'第 {index} 镜标准化')
        clips.append(clip)
    concat = work / 'concat.txt'
    concat.write_text('\n'.join("file '" + clip.as_posix().replace("'", "'\\''") + "'" for clip in clips), encoding='utf-8')
    assembled = work / 'assembled.mp4'
    _run([ffmpeg_binary(), '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-c', 'copy', str(assembled)], '镜头拼接')
    audio = _asset(path, record['audio'])
    raw = output_root / '最终视频_纯净版.mp4'
    if use_video_audio:
        mix = ('[0:a:0]volume=0.65[effects];[1:a:0]volume=1.0[narration];'
               '[effects][narration]amix=inputs=2:duration=first:dropout_transition=0,'
               'alimiter=limit=0.95[mixed]')
        audio_args = ['-filter_complex', mix, '-map', '0:v:0', '-map', '[mixed]']
    else:
        audio_args = ['-map', '0:v:0', '-map', '1:a:0']
    _run([ffmpeg_binary(), '-y', '-i', str(assembled), '-i', str(audio), *audio_args,
          '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(raw)], '配音与音效合成')
    srt = _asset(path, 'assets/subtitles.srt')
    shutil.copy2(srt, output_root / '最终字幕.srt')
    subtitled = output_root / '最终视频_字幕版.mp4'
    style = _subtitle_style(record, height)
    subtitle_filter = f"subtitles=filename='{_subtitle_filter_path(srt)}':charenc=UTF-8:force_style='{style}'"
    _run([ffmpeg_binary(), '-y', '-i', str(raw), '-vf', subtitle_filter, '-c:v', 'libx264', '-preset', 'fast',
          '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', str(subtitled)], '字幕版合成')
    shutil.copy2(audio, output_root / '配音.wav')
    (output_root / '动态分镜方案.json').write_text(json.dumps(record['shots'], ensure_ascii=False, indent=2), encoding='utf-8')
    expected = float(record['scenes'][-1]['end'])
    actual = probe_media_duration(raw)
    if abs(actual - expected) > max(1.0, expected * .01):
        raise RuntimeError(f'成片时长校验失败：预期 {expected:.2f} 秒，实际 {actual:.2f} 秒')
    return output_root, raw, subtitled


def reconcile_source_job(record, user_id: int) -> bool:
    """Close the original guided task after its dynamic-video child is exported.

    Dynamic video has an independent editable project, but users still see the
    original guided task in the project list. Both records must converge on the
    same terminal state. This helper is idempotent and also repairs projects
    exported by builds from before the lifecycle bridge existed.
    """
    source_id = str((record.get('source_project') or {}).get('id') or '').strip()
    exported = record.get('export') if isinstance(record.get('export'), dict) else {}
    if record.get('status') != 'completed' or not source_id or not exported:
        return False
    job = store.get(source_id)
    if job is None or job.user_id != int(user_id) or not job.request.get('dynamic_video'):
        return False
    raw = Path(str(exported.get('raw') or '')).resolve()
    subtitled = Path(str(exported.get('subtitles') or '')).resolve()
    output_root = Path(str(exported.get('directory') or '')).resolve()
    if not raw.is_file() or not subtitled.is_file() or not output_root.is_dir():
        return False
    project_id = str(record.get('id') or '')
    artifacts = dict(job.artifacts or {})
    artifacts.update(
        video_with_subtitles=f'/api/video-studio/{project_id}/export/subtitles',
        video_raw=f'/api/video-studio/{project_id}/export/raw',
    )
    job.request['_dynamic_video_project_id'] = project_id
    job.request['_step_output_dir'] = output_root.name
    job.request['_step_mode_stage'] = 'completed'
    already_synced = (
        job.status == 'completed'
        and str(job.request.get('_step_mode_stage') or '') == 'completed'
        and job.artifacts.get('video_raw') == artifacts['video_raw']
        and job.artifacts.get('video_with_subtitles') == artifacts['video_with_subtitles']
    )
    if not already_synced:
        store.update(job, request=job.request, status='completed', step='completed', progress=100,
                     message='动态视频已完成，可预览、下载或继续精修', error=None,
                     artifacts=artifacts)
        persist_step_workflow_state(job, 'completed', message=job.message)
        store.update(job, request=job.request)
        store.log(job, f'动态视频项目 {project_id} 已完成，字幕版与纯净版已归入本任务。')
        # Register actual files for project history and support diagnostics.
        register_job_asset(job, subtitled, 'project_output',
                           {'dynamic_video': True, 'variant': 'subtitles', 'project_id': project_id})
        register_job_asset(job, raw, 'project_output',
                           {'dynamic_video': True, 'variant': 'raw', 'project_id': project_id})
    return True


def reconcile_completed_projects() -> int:
    """Repair lifecycle links for exports produced by older OCV builds."""
    repaired = 0
    if not studio.ROOT.is_dir():
        return repaired
    for user_dir in studio.ROOT.iterdir():
        if not user_dir.is_dir() or not user_dir.name.isdigit():
            continue
        for record_path in user_dir.glob('*/record.json'):
            try:
                record = json.loads(record_path.read_text(encoding='utf-8'))
                if reconcile_source_job(record, int(user_dir.name)):
                    repaired += 1
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return repaired


def _worker(path, user_id):
    try:
        with studio.LOCK:
            record = studio.read(path)
        output_root, raw, subtitled = _render(path, record)
        with studio.LOCK:
            current = studio.read(path)
            current.update(status='completed', error='', revision=current['revision']+1,
                           export=dict(directory=str(output_root), raw=str(raw), subtitles=str(subtitled)))
            current['logs'].append('动态视频合成完成：已输出字幕版、纯净版、SRT、配音与分镜方案。')
            studio.save(path, current)
            finished = current.copy()
        try:
            reconcile_source_job(finished, user_id)
        except Exception as exc:
            # The dynamic result is valid even if bookkeeping is temporarily
            # unavailable. A later project GET/list call retries idempotently.
            with studio.LOCK:
                current = studio.read(path)
                current['logs'].append('来源任务状态暂未同步，将在下次打开项目时自动重试：' + str(exc)[:1200])
                studio.save(path, current)
    except Exception as exc:
        with studio.LOCK:
            current = studio.read(path)
            current.update(status='export_failed', error=str(exc)[:3000], revision=current['revision']+1)
            current['logs'].append('动态视频合成失败：' + current['error'])
            studio.save(path, current)
    finally:
        studio.ACTIVE.discard(str(path))


@router.post('/{identity}/export')
def start_export(identity: str, data: ExportRequest, request: Request):
    with studio.LOCK:
        user_id = int(studio.require_user(request)['id'])
        path = studio.directory(user_id, identity)
        record = studio.read(path)
        studio.editable(record, data.revision)
        if record['status'] not in {'video_review', 'export_failed', 'completed'}:
            raise HTTPException(409, '请先完成全部动态镜头')
        missing = [shot['id'] for shot in record['shots'] if shot['kind']=='video' and shot.get('video_status')!='completed']
        if missing:
            raise HTTPException(409, f'仍有 {len(missing)} 个动态镜头未完成')
        if str(path) in studio.ACTIVE:
            raise HTTPException(409, '此任务正在处理')
        studio.ACTIVE.add(str(path))
        record.update(status='exporting', error='', revision=record['revision']+1,
                      export_settings={'use_video_audio': data.use_video_audio})
        record['logs'].append('开始合成动态视频：按字幕时长裁切动态片段并补齐静态镜头；' +
                              ('保留动态片段音效并与 TTS 配音混合。' if data.use_video_audio else '忽略动态片段原声，仅使用 TTS 配音。'))
        studio.save(path, record)
        threading.Thread(target=_worker, args=(path, user_id), daemon=True).start()
        return record


@router.get('/{identity}/export/{variant}')
def download(identity: str, variant: str, request: Request):
    path = studio.directory(studio.require_user(request)['id'], identity)
    record = studio.read(path)
    if variant not in {'raw', 'subtitles'} or not isinstance(record.get('export'), dict):
        raise HTTPException(404, '成片尚未生成')
    file = Path(record['export'][variant]).resolve()
    root = (PROJECT_ROOT / 'output').resolve()
    if root not in file.parents or not file.is_file():
        raise HTTPException(404, '成片文件不存在')
    return FileResponse(file, media_type='video/mp4', filename=file.name, content_disposition_type='inline')


@router.post('/{identity}/export/open')
def open_folder(identity: str, request: Request):
    path = studio.directory(studio.require_user(request)['id'], identity)
    record = studio.read(path)
    folder = Path((record.get('export') or {}).get('directory', '')).resolve()
    root = (PROJECT_ROOT / 'output').resolve()
    if root not in folder.parents or not folder.is_dir():
        raise HTTPException(404, '输出目录尚未生成')
    if os.name == 'nt':
        os.startfile(folder)  # type: ignore[attr-defined]
    return {'ok': True}
