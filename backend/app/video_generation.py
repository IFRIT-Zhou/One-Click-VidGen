"""User-triggered, resumable paid clip generation for reviewed storyboards.

No Agent calls or automatic generation on navigation. A clip's inputs and paid
task identity stay frozen until the provider confirms a terminal failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import threading
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import video_studio as studio
from .video_model_config import load_config
from .ark_video_provider import ArkVideoProvider
from .comfyui_bridge import ComfyUIStopped, run_video_profile, video_profile, video_task_state, _video_dimensions, VIDEO_RESOLUTION_PRESETS
from .h3_prompt_agent import H3_SKILL_SOURCE, convert_for_h3
from .video_director_contracts import enforce_no_auto_subtitles
from module6_dynamic_video import (
    DynamicVideoStopped, DynamicVideoTaskFailed, DynamicVideoTaskUnknown,
    FAILURE, RunningHubVideoProvider, VideoGenerationRequest, validate_request,
)


router = APIRouter(prefix='/api/video-studio')
VIDEO_STAGES = {'video_generation_ready', 'video_generating', 'video_stopping', 'video_review'}
VIDEO_GENERATION_ENTRY_STAGES = {'video_generation_ready', 'video_review', 'export_failed', 'completed'}
LOCAL_VIDEO_QUEUES: dict[str, list[str]] = {}
LOCAL_VIDEO_RUNNING: dict[str, str] = {}


class ClipGenerationOptions(BaseModel):
    backend: Literal['api', 'comfyui']
    profile_id: str = Field(default='', max_length=80)
    resolution: Literal['', '480p', '720p', '1080p', 'custom'] = ''
    width: int | None = Field(default=None, ge=32, le=8192)
    height: int | None = Field(default=None, ge=32, le=8192)
    h3_prompt_agent: bool = False


class GenerateClips(BaseModel):
    revision: int
    shot_ids: list[str] = Field(default_factory=list, max_length=1000)
    retry_failed: bool = False
    regenerate_completed: bool = False
    options: ClipGenerationOptions | None = None
    shot_options: dict[str, ClipGenerationOptions] = Field(default_factory=dict)


class SaveClipOptions(BaseModel):
    revision: int
    options: ClipGenerationOptions


def _resolve_generation_options(record, shot, user_id, override=None):
    params = record.get('creation_parameters') or {}
    saved = shot.get('video_generation_options') or {}
    previous = shot.get('video_request') or {}
    values = dict(backend=params.get('video_generation_backend') or 'api',
                  profile_id=params.get('comfyui_profile_id') or '', resolution='',
                  h3_prompt_agent=bool(params.get('comfyui_h3_prompt_agent')))
    if not saved and previous:
        values.update({key: previous[key] for key in ('backend', 'profile_id', 'resolution', 'width', 'height') if key in previous})
        values['h3_prompt_agent'] = previous.get('prompt_format') == 'h3_ref2va'
    values.update(saved)
    if override is not None:
        values.update(override.model_dump())
    options = ClipGenerationOptions.model_validate(values).model_dump()
    if options['backend'] == 'comfyui':
        profile = video_profile(user_id, options['profile_id'])
        if options['resolution'] == 'custom' and not (options['width'] and options['height']):
            raise ValueError('自定义分辨率需要填写宽度与高度')
    else:
        options.update(profile_id='', h3_prompt_agent=False)
        allowed = {'', '480p', '720p', '1080p'} if load_config().get('protocol') == 'ark' else {'', '480p', '720p'}
        if options['resolution'] not in allowed:
            raise ValueError('当前视频 API 支持 480p 和 720p')
    return options


@router.put('/{identity}/shots/{shot_id}/generation-options')
def save_generation_options(identity: str, shot_id: str, data: SaveClipOptions, request: Request):
    with studio.LOCK:
        user_id = int(studio.require_user(request)['id'])
        path = studio.directory(user_id, identity)
        record = studio.read(path)
        studio.shot_editable(record, data.revision, shot_id)
        shot = studio._find_shot(record, shot_id)
        if shot.get('kind') != 'video':
            raise HTTPException(400, '静态镜头不需要视频生成配置')
        if shot.get('video_request') and not shot.get('video_terminal') and shot.get('video_status') != 'completed':
            raise HTTPException(409, '原任务尚未确认结束，请先继续查询原任务')
        try:
            options = _resolve_generation_options(record, shot, user_id, data.options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        shot['video_generation_options'] = options
        record['revision'] += 1
        record.setdefault('logs', []).append(f'{shot_id}：已保存下次生成配置；现有视频保持不变。')
        studio.save(path, record)
        return record


class VideoHistoryAction(BaseModel):
    revision: int


@router.put('/{identity}/generation-options')
def save_all_generation_options(identity: str, data: SaveClipOptions, request: Request):
    with studio.LOCK:
        user_id = int(studio.require_user(request)['id'])
        path = studio.directory(user_id, identity)
        record = studio.read(path)
        studio.editable(record, data.revision)
        if str(path) in studio.ACTIVE or record.get('status') in {'video_generating', 'video_stopping'}:
            raise HTTPException(409, '请等待当前生成结束后再批量保存配置')
        shots = [shot for shot in record.get('shots', []) if shot.get('kind') == 'video']
        if any(shot.get('video_request') and not shot.get('video_terminal')
               and shot.get('video_status') != 'completed' for shot in shots):
            raise HTTPException(409, '部分镜头的原任务尚未确认结束，请先继续查询原任务')
        try:
            resolved = [_resolve_generation_options(record, shot, user_id, data.options) for shot in shots]
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        for shot, options in zip(shots, resolved):
            shot['video_generation_options'] = options
        record['revision'] += 1
        record.setdefault('logs', []).append(f'已将当前生成配置应用到全部 {len(shots)} 个动态镜头；下次生成使用，现有素材保留。')
        studio.save(path, record)
        return record


def _asset(path: Path, relative: str) -> Path:
    result = (path / relative).resolve()
    if path.resolve() not in result.parents:
        raise ValueError('视频资产路径无效')
    return result


def _state_path(path, shot):
    relative = shot.get('video_state_file')
    return _asset(path, relative) if isinstance(relative, str) and relative else None


def _state(path, shot):
    file = _state_path(path, shot)
    if file is None or not file.exists():
        return None
    try:
        value = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('原视频任务状态文件无法读取；为避免重复扣费，暂不允许重新提交') from exc
    if not isinstance(value, dict):
        raise ValueError('原视频任务状态资料无效，禁止重新提交')
    return value


def _sync_identity(path, shot):
    state = _state(path, shot)
    if state is None:
        # Absence of a task file is not evidence that a paid POST never happened.
        # Keep the independent task identity and fail closed after execution began.
        shot.update(video_terminal=False, video_resume_available=False, video_not_submitted=False)
        if shot.get('video_task_id') or (shot.get('video_request') and
                shot.get('video_execution_started') is not False):
            raise ValueError('原视频任务状态文件缺失，已保留任务编号；为避免重复扣费，禁止重新提交，请保留项目并联系支持核实。')
        return state
    if not state:
        raise ValueError('原视频任务状态资料为空，禁止重新提交')
    task_id = str(state.get('task_id') or '')
    if shot.get('video_task_id') and task_id != shot['video_task_id']:
        raise ValueError('原视频任务编号与状态文件不一致，禁止重新提交，请保留项目并联系支持核实。')
    terminal = bool(state.get('terminal') and state.get('status') in FAILURE)
    safe_pause = state.get('status') == 'PAUSED_BEFORE_SUBMIT' and state.get('paid_submission_started') is False
    shot.update(video_task_id=task_id, video_terminal=terminal,
                video_resume_available=(bool(state.get('task_id')) or safe_pause) and not terminal,
                video_not_submitted=safe_pause)
    return state


def recover_interrupted(path, record):
    """Recovery only exposes saved state. It never queries or submits a task."""
    waiting = [shot for shot in record.get('shots', []) if shot.get('video_queued') and shot.get('video_status') != 'running']
    if waiting:
        record.setdefault('video_waiting_queue', []).append({
            'revision': record['revision'], 'shot_ids': [shot['id'] for shot in waiting],
            'shot_options': {shot['id']: shot['video_generation_options'] for shot in waiting if shot.get('video_generation_options')},
            'retry_failed': False, 'regenerate_completed': False})
        for shot in waiting:
            shot['video_waiting'] = True
    for shot in record.get('shots', []):
        shot['video_queued'] = False
    for shot in record.get('shots', []):
        if shot.get('kind') != 'video' or shot.get('video_status') != 'running':
            continue
        if shot.get('video_backend') == 'comfyui':
            shot.update(video_status='failed', video_error='上次本地 ComfyUI 生成被 OCV 关闭中断，可重新生成本镜。',
                        video_resume_available=False, video_terminal=True)
            continue
        try:
            state = _sync_identity(path, shot)
            shot['video_status'] = 'failed' if shot.get('video_terminal') else ('pending' if not state or shot.get('video_not_submitted') else 'unknown')
            shot['video_error'] = ('云端已确认失败，可检查后选择重新付费生成。' if shot.get('video_terminal') else
                '上次本地处理已中断；继续时只查询原任务，不重复提交。' if shot.get('video_task_id') else
                '提交结果尚未确认且未取得任务编号，请先向服务商核实，禁止再次付费提交。' if state and not shot.get('video_not_submitted') else
                '尚未提交，可继续生成。')
        except ValueError as exc:
            shot.update(video_status='unknown', video_error=str(exc), video_resume_available=False,
                        video_terminal=False)
    record.update(status='video_review', error='上次动态镜头处理已中断，已完成片段和原任务身份均已保留。',
                  revision=record['revision'] + 1)
    record.setdefault('logs', []).append(record['error'])


def recover_missing_clips(path, record):
    """Missing local results can be retrieved again without paying for a new clip."""
    changed = False
    for shot in record.get('shots', []):
        if shot.get('kind') != 'video' or shot.get('video_status') != 'completed':
            continue
        try:
            file = _asset(path, str(shot.get('video') or ''))
            if file.is_file() and file.stat().st_size:
                continue
            if shot.get('video_backend') == 'comfyui':
                shot.update(video_status='failed', video_error='本地 ComfyUI 视频文件缺失，可重新生成本镜。',
                            video_resume_available=False, video_terminal=True)
                changed = True
                continue
            _sync_identity(path, shot)
        except (OSError, ValueError) as exc:
            shot.update(video_resume_available=False, video_terminal=False)
            message = str(exc)
        else:
            message = '本地视频文件缺失，可继续查询原任务并重新下载；不会重新提交付费生成。'
        shot.update(video_status='unknown', video_error=message)
        changed = True
    if changed:
        record['revision'] += 1
    return changed


def _inputs(path, record, shot):
    if shot.get('image_status') != 'completed':
        raise ValueError('请先完成并确认本镜核心分镜图')
    if shot.get('design_needs_review'):
        raise ValueError('本镜字幕范围调整后尚未确认设计')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', str(shot.get('id') or '')):
        raise ValueError('镜头编号无效')
    # No global asset dump: exactly the numbers used by the video finalizer.
    core = studio._storyboard_image_path(path, shot)
    selected = [Path(value) for value in studio._shot_reference_paths(path, record, shot)]
    return (core, *selected)


def _freeze_shot_audio(path: Path, shot: dict, destination: Path) -> None:
    """Cut the confirmed TTS interval into the immutable local request."""
    source = path / 'assets' / 'audio.wav'
    if not source.is_file():
        raise ValueError('当前动态项目缺少完整配音，无法传入本镜参考音频')
    start = float(shot.get('start') or 0)
    duration = float(shot.get('duration') or 0)
    if start < 0 or not 0 < duration:
        raise ValueError('本镜配音时间范围无效，无法裁剪参考音频')
    project_root = Path(__file__).resolve().parents[2]
    local = project_root / 'tools' / 'ffmpeg' / 'bin' / 'ffmpeg.exe'
    ffmpeg = str(local) if local.is_file() else (shutil.which('ffmpeg') or 'ffmpeg')
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([
        ffmpeg, '-y', '-i', str(source), '-ss', f'{start:.3f}', '-t', f'{duration:.3f}',
        '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(destination),
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 44:
        raise ValueError('裁剪本镜参考配音失败：' + (completed.stderr or 'FFmpeg 未生成有效 WAV')[-1200:])


def _request(path, shot):
    saved = shot.get('video_request')
    if not isinstance(saved, dict):
        raise ValueError('缺少原视频请求快照，禁止重新提交')
    return VideoGenerationRequest(
        prompt=saved['prompt'], image_paths=tuple(_asset(path, item) for item in saved['reference_files']),
        output_path=_asset(path, shot['video']), duration=saved['duration'], ratio=saved['ratio'],
        resolution=saved['resolution'], generate_audio=False, real_person_mode=False,
    )


def _publish_prepared_attempt(staging: Path, target: Path) -> None:
    """Windows may briefly hold a prepared directory open while scanning files."""
    for retry in range(5):
        if target.exists():
            raise ValueError('发现已有同编号视频请求资产；为避免重复提交，请先核实原任务')
        try:
            staging.replace(target)
            return
        except PermissionError as exc:
            if retry == 4:
                raise ValueError('本地视频请求文件夹暂时被占用，尚未提交视频 API；请稍后重试') from exc
            time.sleep(0.15 * (retry + 1))


def _freeze_request(path, record, shot, config):
    """Copy only selected references, before the worker can make a paid POST."""
    inputs = _inputs(path, record, shot)
    prompt = enforce_no_auto_subtitles(shot.get('video_prompt'))
    seconds = float(shot.get('duration') or 0)
    duration = shot.get('generation_duration')
    if not seconds > 0 or type(duration) is not int or duration < seconds:
        raise ValueError('本镜视频时长与字幕跨度不一致，请检查分镜')
    attempt = int(shot.get('video_attempt') or 0) + 1
    relative = f"assets/videos/{shot['id']}/attempt_{attempt:03d}"
    target = _asset(path, relative)
    if target.exists():
        raise ValueError('发现已有同编号视频请求资产；为避免覆盖付费任务，请先核实原任务')
    request = VideoGenerationRequest(prompt, tuple(inputs), target / 'clip.mp4', duration,
        record['settings'].get('ratio') or '16:9', config['resolution'])
    validate_request(request)
    references = [f'{relative}/references/ref_{number:02d}.jpg' for number in range(1, len(inputs) + 1)]
    snapshot = dict(prompt=prompt, duration=duration, use_duration=seconds, ratio=request.ratio,
                    protocol=config.get('protocol', 'async_task'), model=config.get('model', ''),
                    resolution=request.resolution, reference_files=references,
                    base_url=config['base_url'], submit_path=config['submit_path'],
                    query_path=config.get('query_path'), upload_path=config.get('upload_path'),
                    account_fingerprint=hashlib.sha256(config['api_key'].encode('utf-8')).hexdigest())
    target.parent.mkdir(parents=True, exist_ok=True)
    # Prepare atomically. A bad local image must not leave an attempt directory
    # that looks like a submitted paid task or blocks a corrected retry.
    with tempfile.TemporaryDirectory(prefix='.preparing-', dir=target.parent) as temporary:
        staging = Path(temporary)
        for number, original in enumerate(inputs, 1):
            file = staging / 'references' / f'ref_{number:02d}.jpg'
            file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, file)
            studio._normalize_rgb_image(file, strict=True)
        # This file intentionally contains no API key.
        (staging / 'request.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        _publish_prepared_attempt(staging, target)
    # Keep previous confirmed failures for support; do not erase paid task IDs.
    if shot.get('video_attempt'):
        shot.setdefault('video_history', []).append({key: copy.deepcopy(shot.get(key)) for key in (
            'video_attempt', 'video_task_id', 'video_state_file', 'video_request', 'video', 'video_error')})
    shot.update(video_attempt=attempt, video_backend='api', video_request=snapshot, video=f'{relative}/clip.mp4',
                video_state_file=f'{relative}/task.json', video_task_id='', video_terminal=False,
                video_resume_available=False, video_status='pending', video_error='', video_version='',
                video_resolution=config['resolution'], video_execution_started=False)


LIPSYNC_REFERENCE_AUDIO_INSTRUCTION = (
    '【参考音频与对口型】本次上传的参考音频只用于驱动画面中实际说话人物的口型、面部表情、停顿和动作节奏，'
    '请让可见发言者与音频自然、准确同步。参考音频不是最终成片音轨，不要生成播放器、字幕、波形或音频可视化；'
    '不要额外添加人物对白、旁白、歌声或背景音乐。可以正常生成与当前场景相符的环境音效和动作音效。\n'
)
RHYTHM_REFERENCE_AUDIO_INSTRUCTION = (
    '【参考音频】本次上传的参考音频只用于动作节奏、停顿和情绪参考，不要求人物对口型。'
    '参考音频不是最终成片音轨；不要额外添加人物对白、旁白、歌声或背景音乐，可以生成自然的环境音效和动作音效。\n'
)


def _prompt_with_reference_audio(prompt: str, *, lipsync: bool) -> str:
    return (LIPSYNC_REFERENCE_AUDIO_INSTRUCTION if lipsync else RHYTHM_REFERENCE_AUDIO_INSTRUCTION) + prompt


def _freeze_local_request(path, record, shot, profile_id, *, prompt=None, prompt_format='generic',
                          include_reference_audio=False, resolution='', dimensions=None):
    """Freeze one local request without cloud task identity or billing state."""
    core = _inputs(path, record, shot)[0]
    source_prompt = str(shot.get('video_prompt') or '').strip()
    prompt = enforce_no_auto_subtitles(prompt if prompt is not None else source_prompt)
    seconds = float(shot.get('duration') or 0)
    duration = shot.get('generation_duration')
    if not prompt:
        raise ValueError('本镜视频提示词为空')
    if not 0 < seconds or type(duration) is not int or duration < seconds:
        raise ValueError('本镜视频时长与字幕跨度不一致，请检查分镜')
    attempt = int(shot.get('video_attempt') or 0) + 1
    relative = f"assets/videos/{shot['id']}/attempt_{attempt:03d}"
    target = _asset(path, relative)
    if target.exists():
        raise ValueError('发现已有同编号本地视频资产，请保留项目并核实后重试')
    snapshot = dict(backend='comfyui', profile_id=profile_id, prompt=prompt,
                    source_prompt=source_prompt, prompt_format=prompt_format,
                    duration=duration, use_duration=seconds,
                    ratio=record['settings'].get('ratio') or '16:9',
                    reference_files=[f'{relative}/references/core.jpg'],
                    seed=uuid.uuid4().int % (2**63 - 1))
    if resolution:
        snapshot['resolution'] = resolution
    if dimensions:
        snapshot['width'], snapshot['height'] = dimensions
    if prompt_format == 'h3_ref2va':
        snapshot['h3_skill_source'] = H3_SKILL_SOURCE
    target.mkdir(parents=True, exist_ok=False)
    try:
        reference = target / 'references' / 'core.jpg'
        reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(core, reference)
        studio._normalize_rgb_image(reference, strict=True)
        if include_reference_audio:
            snapshot['reference_audio_lipsync'] = bool(shot.get('reference_audio_lipsync', True))
            audio_relative = f'{relative}/references/voice.wav'
            _freeze_shot_audio(path, shot, _asset(path, audio_relative))
            snapshot['reference_audio_file'] = audio_relative
        (target / 'request.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    if shot.get('video_attempt'):
        shot.setdefault('video_history', []).append({key: copy.deepcopy(shot.get(key)) for key in (
            'video_attempt', 'video_backend', 'video_task_id', 'video_request', 'video', 'video_error')})
    shot.update(video_attempt=attempt, video_backend='comfyui', video_request=snapshot,
                video=f'{relative}/clip.mp4', video_state_file='', video_task_id='',
                video_terminal=True, video_resume_available=False, video_status='pending',
                video_error='', video_version='', video_execution_started=False,
                video_resolution=resolution)


def _recover_local_prompt_id(record, shot):
    """Recover task identity written by builds that logged but did not persist it."""
    abandoned = {str(item).strip() for item in shot.get('video_abandoned_task_ids') or [] if str(item).strip()}
    current = str(shot.get('video_task_id') or '').strip()
    if current and current not in abandoned:
        return current
    identity = re.escape(str(shot.get('id') or ''))
    pattern = re.compile(rf'{identity}：ComfyUI 已接收任务\s+([0-9a-f-]{{16,80}})', re.I)
    for line in reversed(record.get('logs') or []):
        match = pattern.search(str(line))
        if match and match.group(1) not in abandoned:
            shot['video_task_id'] = match.group(1)
            return match.group(1)
    return ''


def _enqueue_local_videos(path, identities):
    """Append shots to the live per-project worker without duplicating work."""
    worker_key = str(path)
    queue = LOCAL_VIDEO_QUEUES.setdefault(worker_key, [])
    running = LOCAL_VIDEO_RUNNING.get(worker_key)
    added = []
    for identity in identities:
        if identity == running or identity in queue:
            continue
        queue.append(identity)
        added.append(identity)
    return added


def _start_local_worker(path, record, identities, user_id, profile_id, use_h3_agent=False,
                        use_reference_audio=False, api_configs=None):
    cancelled = threading.Event()
    worker_key = str(path)
    studio.ACTIVE.add(worker_key)
    studio.CANCEL_EVENTS[worker_key] = cancelled
    LOCAL_VIDEO_QUEUES[worker_key] = list(dict.fromkeys(identities))
    default_profile_id, default_h3_agent = profile_id, use_h3_agent

    def worker():
        try:
            while True:
                with studio.LOCK:
                    queue = LOCAL_VIDEO_QUEUES.setdefault(worker_key, [])
                    if not queue:
                        LOCAL_VIDEO_RUNNING[worker_key] = '__finishing__'
                        break
                    identity = queue.pop(0)
                    LOCAL_VIDEO_RUNNING[worker_key] = identity
                if cancelled.is_set():
                    break
                if api_configs and identity in api_configs:
                    with studio.LOCK:
                        current = studio.read(path)
                        studio._find_shot(current, identity)['video_queued'] = False
                        studio.save(path, current)
                    if not _process_api_clip(path, identity, api_configs[identity], cancelled, threading.Event()):
                        break
                    continue
                status, error = 'completed', ''
                # Prompt conversion and request freezing happen inside the worker.
                # Therefore a slow/failed language call cannot block the HTTP
                # response and never starts a GPU job with a partial prompt.
                with studio.LOCK:
                    current = studio.read(path)
                    shot = studio._find_shot(current, identity)
                    options = shot.get('video_generation_options') or {}
                    profile_id = options.get('profile_id') or default_profile_id
                    use_h3_agent = bool(options.get('h3_prompt_agent', default_h3_agent))
                    resolution = str(options.get('resolution') or '')
                    resume_prompt_id = _recover_local_prompt_id(current, shot) if shot.get('video_request') else ''
                    shot.update(video_status='running', video_error='', video_execution_started=False, video_queued=False,
                                video_started_at=time.time(), video_finished_at=None,
                                video_progress_message='正在查询原 ComfyUI 任务' if resume_prompt_id else
                                '正在转换 H3 提示词' if use_h3_agent else '正在准备工作流与参考素材')
                    if resume_prompt_id:
                        current['logs'].append(f'{identity}：发现原 ComfyUI 任务 {resume_prompt_id}，将续查并下载，不会重复提交。')
                    elif use_h3_agent:
                        current['logs'].append(f'{identity}：H3 提示词转换 Agent 正在读取本镜核心分镜图，并按增强 Skill 整理提示词。')
                    else:
                        current['logs'].append(f'{identity}：沿用通用视频提示词，正在冻结本地生成请求。')
                    current['revision'] += 1
                    studio.save(path, current)

                # A complete computer/ComfyUI restart clears its in-memory
                # queue. Validate a remembered id before waiting on it forever.
                # Unknown connectivity is deliberately conservative: never
                # submit a duplicate merely because the server is unreachable.
                if resume_prompt_id:
                    # A remembered task belongs to its frozen engine/profile,
                    # not the settings the user has selected for the next run.
                    previous_request = shot.get('video_request') or {}
                    local_state = video_task_state(user_id, resume_prompt_id,
                                                  previous_request.get('profile_id') or profile_id)
                    if local_state in {'missing', 'failed'}:
                        with studio.LOCK:
                            current = studio.read(path)
                            shot = studio._find_shot(current, identity)
                            abandoned = shot.setdefault('video_abandoned_task_ids', [])
                            if resume_prompt_id not in abandoned:
                                abandoned.append(resume_prompt_id)
                            shot['video_task_id'] = ''
                            reason = ('已不在 ComfyUI 队列或历史中，判定为服务重启后失效'
                                      if local_state == 'missing' else '已在 ComfyUI 中明确失败')
                            current['logs'].append(
                                f'{identity}：原任务 {resume_prompt_id} {reason}；按本次重跑操作创建新任务。')
                            current['revision'] += 1
                            studio.save(path, current)
                        resume_prompt_id = ''
                    elif local_state == 'unknown':
                        with studio.LOCK:
                            current = studio.read(path)
                            current['logs'].append(
                                f'{identity}：暂时无法确认原 ComfyUI 任务 {resume_prompt_id} 的状态；为避免重复占用显存，继续按原任务查询。')
                            studio.save(path, current)

                try:
                    if resume_prompt_id:
                        previous = shot.get('video_request') or {}
                        if (previous.get('profile_id') != profile_id or
                                (previous.get('resolution') and resolution and previous['resolution'] != resolution)):
                            with studio.LOCK:
                                latest = studio.read(path)
                                latest['logs'].append(
                                    f'{identity}：本次先按原请求恢复查询并取回结果，不重复提交；新配置已保留，原任务结束后再次生成时使用。')
                                studio.save(path, latest)
                    h3_prompt = None
                    h3_source = ''
                    shot_reference_audio = False
                    shot_lipsync = True
                    with studio.LOCK:
                        source_record = studio.read(path)
                        source_shot = copy.deepcopy(studio._find_shot(source_record, identity))
                    from .presenter_mode import audio_policy, spoken_text
                    shot_reference_audio, shot_lipsync = audio_policy(
                        source_record.get('creation_parameters', {}), source_shot, use_h3_agent, source_record.get('references', []))
                    effective_source_prompt = enforce_no_auto_subtitles(source_shot.get('video_prompt'))
                    if shot_reference_audio:
                        effective_source_prompt = _prompt_with_reference_audio(effective_source_prompt, lipsync=shot_lipsync)
                        if shot_lipsync:
                            effective_source_prompt += '\n【仅用于发音，不可上屏的台词】\n' + spoken_text(source_shot)
                        audio_source = path / 'assets' / 'audio.wav'
                        if not audio_source.is_file():
                            raise ValueError('当前项目缺少完整配音，无法启用讲解员参考音频')
                        source_shot['reference_audio_digest'] = hashlib.sha256(audio_source.read_bytes()).hexdigest()
                    if use_h3_agent and not resume_prompt_id:
                        duration = int(source_shot.get('generation_duration') or 0)
                        source_shot['video_prompt'] = effective_source_prompt
                        with studio.project_language_scope(user_id, studio.planning_parameters(source_record)):
                            h3_prompt, h3_source = convert_for_h3(
                                source_shot, duration, reference_audio=shot_reference_audio,
                                lipsync=bool(shot_reference_audio and shot_lipsync),
                                image_path=studio._storyboard_image_path(path, source_shot))
                    if cancelled.is_set():
                        raise ComfyUIStopped('H3 提示词准备完成前已停止；尚未启动本地生成。')
                    with studio.LOCK:
                        current = studio.read(path)
                        shot = studio._find_shot(current, identity)
                        if resume_prompt_id:
                            shot.update(video_status='running', video_error='', video_execution_started=True,
                                        video_task_id=resume_prompt_id)
                            snapshot = copy.deepcopy(shot)
                            current['logs'].append(f'{identity}：开始续查原本地任务，不创建新的生成尝试。')
                            current['revision'] += 1
                            studio.save(path, current)
                            shot = None
                        elif use_h3_agent:
                            shot.update(h3_prompt=h3_prompt, h3_prompt_source=h3_source,
                                        h3_prompt_skill=H3_SKILL_SOURCE)
                            current['logs'].append(f'{identity}：H3 专用提示词已生成并缓存；通用提示词保持不变。')
                        if not resume_prompt_id:
                            profile = video_profile(user_id, profile_id)
                            if resolution:
                                profile = {**profile, 'resolution_preset': resolution}
                            dimensions = _video_dimensions(profile, current['settings'].get('ratio') or '16:9')
                            if resolution == 'custom':
                                dimensions = (int(options['width']), int(options['height']))
                            _freeze_local_request(
                                path, current, shot, profile_id,
                                prompt=h3_prompt if use_h3_agent else effective_source_prompt,
                                prompt_format='h3_ref2va' if use_h3_agent else 'generic',
                                include_reference_audio=shot_reference_audio,
                                resolution=resolution if resolution in {*VIDEO_RESOLUTION_PRESETS, 'custom'} else '',
                                dimensions=dimensions,
                            )
                            shot.update(video_status='running', video_error='', video_execution_started=True)
                            current['logs'].append(f'{identity}：开始调用本地 ComfyUI，使用预设“{profile.get("name") or profile_id}”，实际分辨率 {dimensions[0]} × {dimensions[1]}，本地任务串行执行。')
                            current['revision'] += 1
                            studio.save(path, current)
                            snapshot = copy.deepcopy(shot)
                except ComfyUIStopped as exc:
                    status, error = 'stopped', str(exc)
                except Exception as exc:
                    status, error = 'failed', f'H3 提示词转换或本地请求准备失败：{str(exc)[:1600]}' if use_h3_agent else str(exc)[:1800]

                def progress(message):
                    with studio.LOCK:
                        latest = studio.read(path)
                        studio._find_shot(latest, identity)['video_progress_message'] = str(message)[:1800]
                        latest['logs'].append(f'{identity}：{str(message)[:1800]}')
                        studio.save(path, latest)

                try:
                    if status != 'completed':
                        raise ComfyUIStopped(error)
                    saved = snapshot['video_request']
                    reference = _asset(path, saved['reference_files'][0])
                    reference_audio = (_asset(path, saved['reference_audio_file'])
                                       if saved.get('reference_audio_file') else None)
                    def submitted(prompt_id):
                        with studio.LOCK:
                            latest = studio.read(path)
                            live = studio._find_shot(latest, identity)
                            live['video_task_id'] = prompt_id
                            latest['revision'] += 1
                            studio.save(path, latest)
                    run_video_profile(
                        user_id, saved['profile_id'], prompt=saved['prompt'], image_path=reference,
                        duration=float(saved['duration']), ratio=saved['ratio'],
                        output_path=_asset(path, snapshot['video']), seed=int(saved.get('seed', -1)),
                        progress=progress, should_stop=cancelled.is_set,
                        existing_prompt_id=resume_prompt_id, on_submitted=submitted,
                        audio_path=reference_audio,
                        resolution=saved.get('resolution') or '',
                        dimensions=(int(saved['width']), int(saved['height'])) if saved.get('width') and saved.get('height') else None,
                    )
                except ComfyUIStopped as exc:
                    if status == 'completed':
                        status, error = 'stopped', str(exc)
                except Exception as exc:
                    status, error = 'failed', str(exc)[:1800]
                with studio.LOCK:
                    latest = studio.read(path)
                    live = studio._find_shot(latest, identity)
                    live.update(video_status=status, video_error=error,
                                video_resume_available=False, video_terminal=True)
                    live.update(video_finished_at=time.time(), video_progress_message='已完成，可试看' if status == 'completed' else error)
                    if status == 'completed':
                        live['video_design_changed'] = False
                        live['video_version'] = _video_version(_asset(path, live['video']))
                        latest['logs'].append(f'{identity}：本地 ComfyUI 视频已生成并归档，可直接预览。')
                    else:
                        latest['logs'].append(f'{identity}：{error or "本地生成已停止"}')
                    latest['revision'] += 1
                    studio.save(path, latest)
                with studio.LOCK:
                    LOCAL_VIDEO_RUNNING.pop(worker_key, None)
                if status == 'stopped' and cancelled.is_set():
                    break
        except Exception as exc:
            with studio.LOCK:
                current = studio.read(path)
                current['error'] = str(exc)[:1800]
                current['logs'].append('本地 ComfyUI 动态镜头处理暂停：' + current['error'])
                studio.save(path, current)
        finally:
            with studio.LOCK:
                try:
                    current = studio.read(path)
                    for shot in current['shots']:
                        shot.pop('video_queued', None)
                        if shot.get('video_status') == 'running':
                            if api_configs and shot['id'] in api_configs:
                                shot.update(video_status='unknown', video_error='本地处理中断，请继续查询原任务。')
                                try:
                                    _sync_identity(path, shot)
                                except ValueError:
                                    shot['video_resume_available'] = False
                            else:
                                shot.update(video_status='failed', video_error='本地 ComfyUI 处理已中断，可重新生成。',
                                            video_resume_available=False, video_terminal=True)
                    completed = sum(shot.get('video_status') == 'completed' for shot in current['shots'] if shot['kind'] == 'video')
                    total = sum(shot['kind'] == 'video' for shot in current['shots'])
                    current.update(status='video_review', revision=current['revision'] + 1)
                    current['logs'].append(f'动态片段已完成 {completed}/{total}。请逐镜预览；静态镜头保留核心图。')
                    studio.save(path, current)
                finally:
                    LOCAL_VIDEO_QUEUES.pop(worker_key, None)
                    LOCAL_VIDEO_RUNNING.pop(worker_key, None)
                    studio.ACTIVE.discard(worker_key)
                    studio.CANCEL_EVENTS.pop(worker_key, None)
                    if not cancelled.is_set():
                        _drain_video_waiting(path)

    threading.Thread(target=worker, daemon=True, name='ocv-comfyui-video').start()


def _safe_message(exc, config):
    text = str(exc)
    secret = str(config.get('api_key') or '')
    if secret:
        text = text.replace(secret, '[已隐藏密钥]')
    return text[:1800]


def _video_version(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()[:20]


VIDEO_VERSION_FIELDS = (
    'video_attempt', 'video_backend', 'video_task_id', 'video_state_file',
    'video_request', 'video', 'video_error', 'video_version', 'video_status',
)


def _history_video(path: Path, shot: dict, index: int) -> tuple[dict, Path]:
    history = shot.get('video_history') or []
    if not 0 <= index < len(history) or not isinstance(history[index], dict):
        raise HTTPException(404, '历史视频版本不存在')
    entry = history[index]
    try:
        output = _asset(path, str(entry.get('video') or ''))
    except ValueError as exc:
        raise HTTPException(404, '历史视频文件已不存在') from exc
    if not output.is_file() or output.stat().st_size <= 0:
        raise HTTPException(404, '历史视频文件已不存在')
    return entry, output


def _archive_current_video(path: Path, shot: dict, reason: str) -> None:
    relative = str(shot.get('video') or '')
    if not relative:
        return
    try:
        output = _asset(path, relative)
    except ValueError:
        return
    if not output.is_file() or output.stat().st_size <= 0:
        return
    entry = {key: copy.deepcopy(shot.get(key)) for key in VIDEO_VERSION_FIELDS}
    entry.update(invalidated_at=time.time(), invalidated_reason=reason)
    history = list(shot.get('video_history') or [])
    if not history or history[-1].get('video') != relative:
        history.append(entry)
    shot['video_history'] = history


def _invalidate_export_after_video_choice(record: dict, message: str) -> None:
    record.pop('export', None)
    record.update(status='video_review', error='')
    record.setdefault('logs', []).append(message)


def _account_slots(config):
    keys = list(config.get('api_keys') or ([config.get('api_key')] if config.get('api_key') else []))
    per_key = max(1, int(config.get('per_key_concurrency') or 1))
    slots = [key for key in keys for _ in range(per_key)]
    if config.get('concurrency_mode') == 'manual':
        slots = slots[:max(1, int(config.get('total_concurrency') or 1))]
    return slots


def _config_for_shot(config, shot, fallback=0):
    slots = _account_slots(config)
    if not slots:
        raise ValueError('请先在“接口与服务”保存视频 API Key')
    expected = str((shot.get('video_request') or {}).get('account_fingerprint') or '')
    if expected:
        key = next((item for item in dict.fromkeys(slots)
                    if hashlib.sha256(item.encode('utf-8')).hexdigest() == expected), None)
        if not key:
            raise ValueError('原视频任务使用的 API 账号已不在当前配置中，请恢复原 Key 后继续查询')
    else:
        key = slots[fallback % len(slots)]
    return {**config, 'api_key': key}


def _process_api_clip(path, identity, config, cancelled, halted):
    if cancelled.is_set() or halted.is_set():
        return False
    with studio.LOCK:
        current = studio.read(path)
        shot = studio._find_shot(current, identity)
        if shot.get('video_queue_cancelled') or (config.get('_queue_token') and config['_queue_token'] != shot.get('video_queue_token')):
            return True
        shot.update(video_status='running', video_error='', video_queued=False)
        shot.update(video_started_at=time.time(), video_finished_at=None, video_progress_message='正在准备并提交视频请求')
        current['logs'].append(f'{identity}：开始处理动态镜头，使用 {shot["duration"]} 秒，请求 {shot["video_request"]["duration"]} 秒。')
        current['revision'] += 1
        studio.save(path, current)
        snapshot = copy.deepcopy(shot)
    def progress(message):
        with studio.LOCK:
            latest = studio.read(path)
            live = studio._find_shot(latest, identity)
            _sync_identity(path, live)
            live['video_progress_message'] = _safe_message(message, config)
            latest['logs'].append(f'{identity}：{_safe_message(message, config)}')
            studio.save(path, latest)
    status, error = 'completed', ''
    try:
        saved = snapshot['video_request']
        if (config['base_url'].rstrip('/') != saved['base_url'].rstrip('/')
                or config['submit_path'] != saved['submit_path']
                or config.get('query_path') != saved.get('query_path', config.get('query_path'))
                or config.get('upload_path') != saved.get('upload_path', config.get('upload_path'))):
            raise ValueError('原视频任务使用另一套接口配置，请恢复原配置后继续查询，不会重新付费提交')
        provider_type = ArkVideoProvider if saved.get('protocol', config.get('protocol')) == 'ark' else RunningHubVideoProvider
        extra = {'model': saved.get('model', config.get('model'))} if provider_type is ArkVideoProvider else {}
        provider = provider_type(config['api_key'], base_url=saved['base_url'],
                                           submit_path=saved['submit_path'],
                                           query_path=saved.get('query_path'),
                                           upload_path=saved.get('upload_path'), **extra)
        frozen_request = _request(path, snapshot)
        validate_request(frozen_request)
        if cancelled.is_set():
            raise DynamicVideoStopped('尚未开始提交，已停止本地处理。')
        with studio.LOCK:
            latest = studio.read(path)
            live = studio._find_shot(latest, identity)
            live['video_execution_started'] = True
            studio.save(path, latest)
        provider.run(frozen_request, _state_path(path, snapshot),
                     progress=progress, should_stop=cancelled.is_set)
    except DynamicVideoStopped as exc:
        status, error = 'stopped', _safe_message(exc, config)
    except DynamicVideoTaskFailed as exc:
        status, error = 'failed', _safe_message(exc, config)
    except Exception as exc:
        status, error = 'unknown', _safe_message(exc, config)
    with studio.LOCK:
        latest = studio.read(path)
        live = studio._find_shot(latest, identity)
        live.update(video_status=status, video_error=error)
        live.update(video_finished_at=time.time(), video_progress_message='已完成，可试看' if status == 'completed' else error)
        try:
            state = _sync_identity(path, live)
            if live.get('video_terminal'):
                live['video_status'] = 'failed'
            elif status == 'unknown' and not state:
                live['video_status'] = 'pending'
        except ValueError as exc:
            live.update(video_status='unknown', video_resume_available=False, video_terminal=False,
                        video_error=str(exc))
        if live['video_status'] == 'completed':
            live['video_design_changed'] = False
            output = _asset(path, live['video'])
            live['video_version'] = _video_version(output)
            live['video_resume_available'] = False
            latest['logs'].append(f'{identity}：视频已下载，可直接预览。')
        else:
            latest['logs'].append(f'{identity}：{live["video_error"] or "已停止，本镜头资产已保留"}')
        latest['revision'] += 1
        studio.save(path, latest)
    if status != 'completed':
        halted.set()
    return status == 'completed'


def _start_worker(path, record, identities, configs):
    cancelled = threading.Event()
    halted = threading.Event()
    studio.ACTIVE.add(str(path))
    studio.CANCEL_EVENTS[str(path)] = cancelled

    def process(identity, config):
        return _process_api_clip(path, identity, config, cancelled, halted)

    def worker():
        try:
            maximum = max(1, min(len(identities), len(_account_slots(next(iter(configs.values()))))))
            with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix='ocv-video') as pool:
                futures = [pool.submit(process, identity, configs[identity]) for identity in identities]
                for future in as_completed(futures):
                    future.result()
        except Exception as exc:
            with studio.LOCK:
                current = studio.read(path)
                current['error'] = _safe_message(exc, next(iter(configs.values())))
                current['logs'].append('动态镜头处理暂停：' + current['error'])
                studio.save(path, current)
        finally:
            with studio.LOCK:
                try:
                    current = studio.read(path)
                    for shot in current['shots']:
                        if shot.get('video_status') == 'running':
                            shot.update(video_status='unknown', video_error='本地处理中断，请继续查询原任务。')
                            try:
                                _sync_identity(path, shot)
                            except ValueError:
                                shot['video_resume_available'] = False
                    completed = sum(shot.get('video_status') == 'completed' for shot in current['shots'] if shot['kind'] == 'video')
                    total = sum(shot['kind'] == 'video' for shot in current['shots'])
                    current.update(status='video_review', revision=current['revision'] + 1)
                    current['logs'].append(f'动态片段已完成 {completed}/{total}。' +
                        ('已安全停止；云端已接收的任务不会因本地停止而撤销，请继续查询结果。' if cancelled.is_set() else
                         '请逐镜预览；静态画面保留原图，本阶段尚未合成整片。'))
                    studio.save(path, current)
                finally:
                    studio.ACTIVE.discard(str(path))
                    studio.CANCEL_EVENTS.pop(str(path), None)
                    for shot in current['shots']:
                        shot['video_queued'] = False
                    studio.save(path, current)
                    if not cancelled.is_set():
                        _drain_video_waiting(path)

    thread = threading.Thread(target=worker, daemon=True)
    try:
        thread.start()
    except Exception:
        studio.ACTIVE.discard(str(path))
        studio.CANCEL_EVENTS.pop(str(path), None)
        raise


@router.post('/{identity}/videos/generate')
def generate(identity: str, data: GenerateClips, request: Request):
    return _generate_for_user(identity, data, int(studio.require_user(request)['id']))


def _generate_for_user(identity, data, user_id):
    with studio.LOCK:
        path = studio.directory(user_id, identity)
        record = studio.read(path)
        if data.options and (len(data.shot_ids) != 1 or data.shot_options):
            raise HTTPException(400, '单镜配置只能用于一个明确选择的镜头')
        catalog = {shot['id']: shot for shot in record['shots']}
        if len(set(data.shot_ids)) != len(data.shot_ids) or any(key not in catalog for key in data.shot_ids):
            raise HTTPException(400, '选择的镜头不存在或重复')
        if set(data.shot_options) - set(data.shot_ids):
            raise HTTPException(400, '镜头配置与本次选择不一致')
        if any(catalog[key]['kind'] != 'video' for key in data.shot_ids):
            raise HTTPException(400, '静态镜头不需要生成视频')
        if data.regenerate_completed and not data.shot_ids:
            raise HTTPException(400, '重新生成必须明确选择镜头')
        selected = [shot for shot in record['shots'] if shot['kind'] == 'video'
                    and (not data.shot_ids or shot['id'] in data.shot_ids)
                    and (data.regenerate_completed or shot.get('video_status') != 'completed')]
        if not selected:
            raise HTTPException(409, '没有需要生成的动态镜头')
        try:
            resolved = {shot['id']: _resolve_generation_options(
                record, shot, user_id, data.options or data.shot_options.get(shot['id'])) for shot in selected}
            backends = {options['backend'] for options in resolved.values()}
            backend = next(iter(backends)) if len(backends) == 1 else 'mixed'
            config = load_config() if 'api' in backends else {}
            if 'api' in backends and not str(config.get('api_key') or '').strip():
                raise ValueError('请先到“接口与服务”保存视频 API 配置')
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        local_queue_append = (backend == 'comfyui' and
                              record.get('active_video_backend') in {'comfyui', 'mixed'} and
                              LOCAL_VIDEO_RUNNING.get(str(path)) != '__finishing__' and
                              str(path) in studio.ACTIVE and record.get('status') == 'video_generating')
        if record['status'] == 'video_generating' and str(path) in studio.ACTIVE and not local_queue_append:
            if record['revision'] != data.revision:
                raise HTTPException(409, '项目刚刚发生变化，请刷新后重试')
            for shot in selected:
                studio.shot_editable(record, data.revision, shot['id'])
                try:
                    _inputs(path, record, shot)
                except (ValueError, OSError) as exc:
                    raise HTTPException(409, str(exc)) from exc
                if not str(shot.get('video_prompt') or '').strip():
                    raise HTTPException(409, '请先填写本镜视频提示词')
            payload = data.model_dump(mode='json')
            payload['shot_ids'] = [shot['id'] for shot in selected]
            payload['options'] = None
            payload['shot_options'] = resolved
            record.setdefault('video_waiting_queue', []).append(payload)
            for shot in selected:
                shot['video_waiting'] = True
            record['revision'] += 1
            record['logs'].append(f'已保存 {len(selected)} 镜到生成队列，当前批次完成后继续。')
            studio.save(path, record)
            return record
        if local_queue_append:
            if int(record.get('revision') or 0) != data.revision:
                raise HTTPException(409, '项目刚刚发生变化，请刷新后重试')
        else:
            studio.editable(record, data.revision, allow_image_edits=True)
            if record['status'] not in VIDEO_GENERATION_ENTRY_STAGES:
                raise HTTPException(409, '请先确认核心分镜图，再生成动态镜头')
        if str(path) in studio.ACTIVE and not local_queue_append:
            raise HTTPException(409, '此任务正在处理，请稍后重试')
        profiles = {}
        use_reference_audio = bool(record.get('creation_parameters', {}).get('comfyui_reference_audio'))
        # Validate every selected configuration before invalidating any asset or starting work.
        try:
            for shot in selected:
                if (studio.image_edit_key(record['id'], shot['id']) in studio.IMAGE_EDITS or
                        (shot.get('image_task') or {}).get('status') == 'running' or shot.get('video_waiting')):
                    raise ValueError('所选镜头正在编辑或已在等待队列中')
                options = resolved[shot['id']]
                if local_queue_append and (shot['id'] == LOCAL_VIDEO_RUNNING.get(str(path)) or shot['id'] in LOCAL_VIDEO_QUEUES.get(str(path), [])):
                    raise ValueError('此镜头已在生成队列中，不能修改配置或重复添加')
                previous = shot.get('video_request') or {}
                if previous and not data.regenerate_completed and not shot.get('video_terminal'):
                    previous_backend = previous.get('backend') or shot.get('video_backend') or 'api'
                    if previous_backend != options['backend']:
                        raise ValueError('原任务尚未确认结束，请先查询原任务；不能更换配置后重复提交')
                    explicit = data.options or data.shot_options.get(shot['id'])
                    if explicit:
                        if options['backend'] == 'comfyui':
                            profile = video_profile(user_id, options['profile_id'])
                            chosen = {**profile, **({'resolution_preset': options['resolution']} if options['resolution'] else {})}
                            dimensions = ((options['width'], options['height']) if options['resolution'] == 'custom'
                                          else _video_dimensions(chosen, record['settings'].get('ratio') or '16:9'))
                            if previous.get('profile_id') != options['profile_id'] or (previous.get('width') and dimensions != (previous['width'], previous['height'])):
                                raise ValueError('原任务尚未确认结束，不能更换配置后重复提交')
                        elif previous.get('resolution') != (options['resolution'] or config['resolution']):
                            raise ValueError('原任务尚未确认结束，不能更换分辨率后重复提交')
                _inputs(path, record, shot)
                if not str(shot.get('video_prompt') or '').strip():
                    raise ValueError(f'{shot["id"]} 的视频提示词为空')
                if options['backend'] == 'comfyui':
                    profile = profiles.setdefault(options['profile_id'], video_profile(user_id, options['profile_id']))
                    if profile.get('engine') == 'managed' and (not previous or data.regenerate_completed or shot.get('video_terminal')):
                        from .model_library import installation
                        model_state = installation(profile_id=profile['id'])
                        if model_state['runtime_missing'] or not model_state['ready']:
                            missing = '、'.join(item['name'] for item in model_state['items'] if not item['ready'])
                            raise ValueError('内置工作流模型尚未齐备：' + (missing or '引擎组件未安装') + '。请打开本镜生成设置中的「模型安装指引」，补齐后重新检查。')
                    binding = (profile.get('mappings') or {}).get('audio') or {}
                    from .presenter_mode import audio_policy
                    needs_audio, _ = audio_policy(record.get('creation_parameters', {}), shot, options['h3_prompt_agent'], record.get('references', []))
                    if needs_audio and not (binding.get('node_id') and binding.get('input_name')):
                        raise ValueError(f'{shot["id"]} 的工作流没有映射参考音频节点')
                    if needs_audio and not (path / 'assets' / 'audio.wav').is_file():
                        raise ValueError('当前项目缺少完整配音，无法启用讲解员参考音频')
                elif not previous or data.regenerate_completed or shot.get('video_terminal'):
                    from .presenter_mode import presenter_requested
                    if presenter_requested(record.get('creation_parameters', {}), shot, record.get('references', [])):
                        raise ValueError('讲解员音频对口型目前支持 ComfyUI 音频工作流；请切换本镜生成方式，或关闭本镜人物对口型后使用视频 API')
                    validate_request(VideoGenerationRequest(str(shot.get('video_prompt') or ''),
                        _inputs(path, record, shot), path / 'unused.mp4', shot.get('generation_duration'),
                        record['settings'].get('ratio') or '16:9', options['resolution'] or config['resolution']))
                if options['backend'] == 'api' and not data.regenerate_completed and (not previous or previous.get('backend', shot.get('video_backend') or 'api') == 'api'):
                    state = _sync_identity(path, shot)
                    if state is not None and shot.get('video_terminal') and not data.retry_failed:
                        raise ValueError('所选镜头有已确认失败的云端任务；请明确选择重新生成')
                    if state is not None and not shot.get('video_terminal') and not state.get('task_id') and not shot.get('video_not_submitted'):
                        raise ValueError('有提交结果未知的镜头，请先核实原任务')
                    if previous and not shot.get('video_terminal'):
                        validate_request(_request(path, shot))
        except (ValueError, OSError) as exc:
            raise HTTPException(409, _safe_message(exc, config)) from exc
        execution_configs = {}
        for index, shot in enumerate(selected):
            options = resolved[shot['id']]
            shot['video_queue_token'] = uuid.uuid4().hex
            if data.regenerate_completed:
                studio._invalidate_shot_video(record, shot, '用户要求重新生成本镜动态片段，保留本镜配置')
            elif shot.get('video_request') and shot.get('video_terminal') and (shot['video_request'].get('backend') or shot.get('video_backend') or 'api') != options['backend']:
                studio._invalidate_shot_video(record, shot, '用户切换本镜视频生成方式')
            shot['video_generation_options'] = options
            if options['backend'] == 'api':
                per_shot = {**config, 'resolution': options['resolution'] or config['resolution']}
                if not shot.get('video_request') or shot.get('video_terminal'):
                    fresh = {**per_shot, 'api_key': _account_slots(config)[index % len(_account_slots(config))]}
                    try:
                        _freeze_request(path, record, shot, fresh)
                    except (ValueError, OSError) as exc:
                        raise HTTPException(409, _safe_message(exc, config)) from exc
                    record['revision'] += 1
                    studio.save(path, record)
                execution_configs[shot['id']] = {**_config_for_shot(per_shot, shot, index), '_queue_token': shot['video_queue_token']}
            shot['video_queued'] = True
            shot.pop('video_queue_cancelled', None)
        if local_queue_append:
            added = _enqueue_local_videos(path, [shot['id'] for shot in selected])
            for shot in selected:
                shot.update(video_status='pending', video_error='', video_terminal=False)
            record['logs'].append(f'已按各镜头保存的配置追加 {len(added)} 个镜头到本地队列。')
        else:
            record['logs'].append(f'按各镜头配置处理 {len(selected)} 个镜头；' +
                                 ('视频 API 按接口并发运行。' if backend == 'api' else '本地或混合批次依次运行。'))
        record.update(status='video_generating',
                      active_video_backend=record.get('active_video_backend', backend) if local_queue_append else backend,
                      error='', revision=record['revision'] + 1)
        studio.save(path, record)
        if not local_queue_append:
            if backend == 'api':
                _start_worker(path, record, [shot['id'] for shot in selected], execution_configs)
            else:
                local_options = next(options for options in resolved.values() if options['backend'] == 'comfyui')
                kwargs = {'api_configs': execution_configs} if execution_configs else {}
                _start_local_worker(path, record, [shot['id'] for shot in selected], user_id,
                                    local_options['profile_id'], use_h3_agent=local_options['h3_prompt_agent'],
                                    use_reference_audio=use_reference_audio, **kwargs)
        return record


def _drain_video_waiting(path):
    """Drain a persisted, explicitly authorized batch after the active worker."""
    if str(path) in studio.ACTIVE:
        return
    record = studio.read(path)
    queue = record.get('video_waiting_queue') or []
    if not queue:
        return
    payload = queue.pop(0)
    selected = set(payload['shot_ids'])
    for shot in record['shots']:
        if shot['id'] in selected:
            shot['video_waiting'] = False
    record['video_waiting_queue'] = queue
    record['revision'] += 1
    studio.save(path, record)
    payload['revision'] = record['revision']
    try:
        _generate_for_user(record['id'], GenerateClips(**payload), int(path.parent.name))
    except Exception as exc:
        current = studio.read(path)
        message = str(exc.detail) if isinstance(exc, HTTPException) else type(exc).__name__
        current['error'] = '队列提交暂停：' + message
        current['logs'].append(current['error'])
        current['revision'] += 1
        studio.save(path, current)


@router.post('/{identity}/videos/queue/resume')
def resume_waiting(identity: str, data: studio.Review, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        if record['revision'] != data.revision or str(path) in studio.ACTIVE:
            raise HTTPException(409, '项目状态已变化，请刷新后重试')
        _drain_video_waiting(path)
        return studio.read(path)


@router.post('/{identity}/videos/{shot_id}/cancel-queue')
def cancel_queued_shot(identity: str, shot_id: str, data: studio.Review, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        if record['revision'] != data.revision:
            raise HTTPException(409, '项目状态已变化，请刷新后重试')
        shot = studio._find_shot(record, shot_id)
        if shot.get('video_status') == 'running' or LOCAL_VIDEO_RUNNING.get(str(path)) == shot_id:
            raise HTTPException(409, '本镜已经开始处理，不能取消排队')
        if not (shot.get('video_queued') or shot.get('video_waiting')):
            raise HTTPException(409, '本镜不在等待队列中')
        queue = LOCAL_VIDEO_QUEUES.get(str(path), [])
        if shot_id in queue:
            queue.remove(shot_id)
        waiting = []
        for item in record.get('video_waiting_queue') or []:
            item['shot_ids'] = [value for value in item['shot_ids'] if value != shot_id]
            item.get('shot_options', {}).pop(shot_id, None)
            if item['shot_ids']:
                waiting.append(item)
        record['video_waiting_queue'] = waiting
        shot.update(video_queued=False, video_waiting=False, video_queue_cancelled=True)
        shot['video_queue_token'] = uuid.uuid4().hex
        if not shot.get('video_execution_started') and shot.get('video_status') != 'completed':
            studio._invalidate_shot_video(record, shot, '已取消尚未开始的生成任务')
        record['revision'] += 1
        record['logs'].append(f'{shot_id}：已取消排队，可以编辑。')
        studio.save(path, record)
        return record


@router.post('/{identity}/videos/stop')
def stop(identity: str, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        record['video_waiting_queue'] = []
        for shot in record['shots']:
            shot['video_waiting'] = False
        studio.save(path, record)
        if record['status'] in {'video_generating', 'video_stopping'}:
            event = studio.CANCEL_EVENTS.get(str(path))
            if event is not None:
                event.set()
                record.update(status='video_stopping', revision=record['revision'] + 1)
                if (record.get('active_video_backend') or record.get('creation_parameters', {}).get('video_generation_backend')) == 'comfyui':
                    record['logs'].append('已请求停止本地 ComfyUI 当前任务，并且不再提交后续镜头。')
                else:
                    record['logs'].append('已请求停止本地处理，不再提交后续镜头；已提交的云端付费任务不能保证撤销。')
                studio.save(path, record)
        return record


@router.get('/{identity}/videos/{shot_id}')
def video(identity: str, shot_id: str, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        shot = studio._find_shot(record, shot_id)
        if shot.get('video_status') != 'completed':
            raise HTTPException(404, '本镜视频尚未完成下载')
        try:
            output = _asset(path, str(shot.get('video') or ''))
        except ValueError as exc:
            raise HTTPException(404, '视频资产不存在') from exc
        if not output.is_file() or output.stat().st_size <= 0:
            raise HTTPException(404, '视频文件缺失，请检查本地任务目录')
        return FileResponse(output, media_type='video/mp4', filename=f'{shot_id}.mp4',
                            content_disposition_type='inline', headers={'Cache-Control': 'no-cache'})


@router.get('/{identity}/videos/{shot_id}/history/{index}')
def historical_video(identity: str, shot_id: str, index: int, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        shot = studio._find_shot(record, shot_id)
        _, output = _history_video(path, shot, index)
        return FileResponse(output, media_type='video/mp4', filename=f'{shot_id}-history-{index + 1}.mp4',
                            content_disposition_type='inline', headers={'Cache-Control': 'no-cache'})


@router.post('/{identity}/videos/{shot_id}/history/{index}/adopt')
def adopt_historical_video(identity: str, shot_id: str, index: int,
                           data: VideoHistoryAction, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        studio.editable(record, data.revision)
        if record.get('status') not in VIDEO_GENERATION_ENTRY_STAGES:
            raise HTTPException(409, '请在动态镜头检查或成片阶段切换历史版本')
        shot = studio._find_shot(record, shot_id)
        if shot.get('kind') != 'video':
            raise HTTPException(409, '静态镜头没有历史视频版本')
        entry, output = _history_video(path, shot, index)
        _archive_current_video(path, shot, '切换历史版本前保留的当前片段')
        history = list(shot.get('video_history') or [])
        # The current version may have just been appended, so remove by the
        # original index and preserve the newly archived current version.
        selected = history.pop(index)
        shot['video_history'] = history
        for key in VIDEO_VERSION_FIELDS:
            if key in selected:
                shot[key] = copy.deepcopy(selected[key])
            else:
                shot.pop(key, None)
        shot.update(video_status='completed', video_error='', video_terminal=True,
                    video_resume_available=False, video_not_submitted=False,
                    video_execution_started=True,
                    video_version=str(selected.get('video_version') or _video_version(output)))
        _invalidate_export_after_video_choice(
            record, f'{shot_id}：已采用历史动态片段；其他镜头、配音和分镜保持不变，请重新合成成片。')
        record['revision'] += 1
        studio.save(path, record)
        return record


@router.delete('/{identity}/videos/{shot_id}/history/{index}')
def delete_historical_video(identity: str, shot_id: str, index: int,
                            revision: int, request: Request):
    with studio.LOCK:
        path = studio.directory(studio.require_user(request)['id'], identity)
        record = studio.read(path)
        studio.editable(record, revision)
        shot = studio._find_shot(record, shot_id)
        _, output = _history_video(path, shot, index)
        history = list(shot.get('video_history') or [])
        history.pop(index)
        shot['video_history'] = history
        # Attempts are immutable directories. Remove an old attempt only when
        # no current/history entry references anything inside it.
        attempt_dir = output.parent.resolve()
        shot_root = (path / 'assets' / 'videos' / str(shot_id)).resolve()
        referenced = {str(shot.get('video') or '')}
        referenced.update(str(item.get('video') or '') for item in history if isinstance(item, dict))
        still_used = any(value and attempt_dir in _asset(path, value).parents
                         for value in referenced if value)
        if not still_used and shot_root in attempt_dir.parents:
            shutil.rmtree(attempt_dir, ignore_errors=True)
        record.setdefault('logs', []).append(f'{shot_id}：已删除一个未采用的历史动态片段。')
        record['revision'] += 1
        studio.save(path, record)
        return record


@router.post('/{identity}/videos/{shot_id}/upload')
async def upload_replacement_video(identity: str, shot_id: str, request: Request,
                                   revision: int = Form(...), file: UploadFile = File(...)):
    if Path(file.filename or '').suffix.lower() != '.mp4':
        raise HTTPException(400, '目前仅支持上传 MP4 视频')
    user_id = studio.require_user(request)['id']
    path = studio.directory(user_id, identity)
    with studio.LOCK:
        record = studio.read(path)
        studio.editable(record, revision)
        if record.get('status') not in VIDEO_GENERATION_ENTRY_STAGES:
            raise HTTPException(409, '请在动态镜头检查或成片阶段上传替换视频')
        shot = studio._find_shot(record, shot_id)
        if shot.get('kind') != 'video':
            raise HTTPException(409, '静态镜头不能上传动态片段')
        attempts = [int(shot.get('video_attempt') or 0)]
        attempts.extend(int(item.get('video_attempt') or 0) for item in shot.get('video_history') or []
                        if isinstance(item, dict))
        attempt = max(attempts, default=0) + 1
        target_dir = _asset(path, f'assets/videos/{shot_id}/attempt_{attempt:03d}')
        target_dir.mkdir(parents=True, exist_ok=False)
        temporary = target_dir / '.uploading.mp4'
    maximum = 2 * 1024 * 1024 * 1024
    size = 0
    try:
        with temporary.open('wb') as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, '上传视频不能超过 2GB')
                stream.write(chunk)
        from .pipeline import probe_media_duration
        duration = probe_media_duration(temporary)
        output = target_dir / 'clip.mp4'
        temporary.replace(output)
        with studio.LOCK:
            latest = studio.read(path)
            studio.editable(latest, revision)
            live = studio._find_shot(latest, shot_id)
            _archive_current_video(path, live, '上传本地替换片段前保留的当前版本')
            relative = str(output.relative_to(path.resolve())).replace('\\', '/')
            live.update(video_attempt=attempt, video_backend='upload', video_task_id='',
                        video_state_file='', video_request={
                            'backend': 'upload', 'filename': Path(file.filename or 'clip.mp4').name,
                            'duration': round(duration, 3), 'use_duration': live.get('duration'),
                        }, video=relative, video_status='completed', video_error='',
                        video_version=_video_version(output), video_terminal=True,
                        video_resume_available=False, video_not_submitted=False,
                        video_execution_started=True)
            _invalidate_export_after_video_choice(
                latest, f'{shot_id}：已上传本地视频作为当前片段；原片已进入历史，其他镜头保持不变。')
            latest['revision'] += 1
            studio.save(path, latest)
            return latest
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    finally:
        await file.close()
