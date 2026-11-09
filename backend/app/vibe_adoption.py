"""Explicitly adopt a verified local Vibe output into the normal export path.

The preview cache is disposable. Formal outputs and their undo provenance are
immutable video attempts. Neither operation submits a job or changes narration.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import video_generation as video
from . import video_studio as studio
from . import vibe_preview as previews
from .image_layer_packs import owned_project
from .pipeline import ffprobe_binary
from .vibe_files import publish

router = APIRouter(prefix='/api/video-studio')
_STAGES = {'image_review', *video.VIDEO_GENERATION_ENTRY_STAGES}
_INTENT_STAGES = _STAGES | {'storyboard_review', 'generation_ready'}
_FIELDS = (*video.VIDEO_VERSION_FIELDS, 'kind', 'kind_adjustment', 'generation_duration',
           'video_terminal', 'video_resume_available', 'video_not_submitted',
           'video_execution_started', 'video_resolution', 'video_design_changed', 'vibe_reserved')
_MOTION = ('action', 'video_prompt', 'motion_plan', 'video_prompt_warnings', 'kind_adjustment',
           'generation_duration', 'warning')


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(strict=True, ge=0)
    preview_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class Intent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(strict=True, ge=0)
    enabled: bool = Field(strict=True)


def _target(project, shot_id, revision, *, reservation=False):
    # Do not call studio.read: recovery there can change an unrelated shot even
    # on a failed adoption. Check mutation preconditions against the saved record.
    record = previews._read_json(previews._safe(project, project/'record.json', file=True), 32*1024**2)
    studio.editable(record, revision)
    stages = _INTENT_STAGES if reservation else _STAGES
    if str(project) in studio.ACTIVE or record.get('status') not in stages:
        raise HTTPException(409, '请在分镜检查阶段预留，或在核心图完成后采用动效')
    shot = studio.shot_editable(record, revision, shot_id, extra_stages=stages)
    if shot.get('asset_kind') == 'scene_reference' or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', shot_id):
        raise HTTPException(422, '请选择时间轴中的真实分镜')
    return record, shot


def _verify_silent(file, duration):
    args = [ffprobe_binary(), '-v', 'error', '-protocol_whitelist', 'file',
            '-f', 'mov', '-show_entries', 'stream=codec_type,codec_name,duration', '-of', 'json', str(file)]
    try:
        result = subprocess.run(args, check=True, capture_output=True, timeout=10,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        streams = json.loads(result.stdout)['streams']
        if (len(streams) != 1 or streams[0]['codec_type'] != 'video' or streams[0]['codec_name'] != 'h264'
                or not math.isclose(float(streams[0]['duration']), duration, rel_tol=0, abs_tol=1/30+.001)):
            raise ValueError
    except FileNotFoundError as exc:
        raise HTTPException(409, '缺少本地 ffprobe，未采用预览') from exc
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as exc:
        raise HTTPException(409, '预览无声版已损坏，或时长不覆盖本镜；原镜头保留') from exc


def _finish(project, record, note):
    record.pop('export', None)
    if record['status'] in {'completed', 'export_failed'}:
        record['status'] = 'video_review'
    record.setdefault('logs', []).append(note)
    record.update(revision=record['revision']+1, error='')
    studio.save(project, record)
    return record


def _require_quality_passed(receipt):
    """A review-only artifact is inspectable, never an export-ready result.

    Historical previews have no visual-review contract. New layered workflows
    must carry an explicit pass; a conflicting nested status cannot bypass it.
    """
    metrics = receipt.get('metrics')
    metrics = metrics if isinstance(metrics, dict) else {}
    statuses = [receipt.get('quality_status'), metrics.get('quality_status')]
    if 'needs_review' in statuses:
        raise HTTPException(409, '此预览尚未通过视觉核查，仅供检查，不能采用到正式镜头。'
                            '请查看预览中的待修正问题，调整动效要求后重新设计；未修改正式镜头。')
    if receipt.get('workflow_version') and not all(value in (None, 'passed') for value in statuses):
        raise HTTPException(409, '此预览的视觉核查结果不完整，请重新设计并通过核查后再采用；未修改正式镜头。')
    if receipt.get('workflow_version') and 'passed' not in statuses:
        raise HTTPException(409, '此预览缺少视觉核查通过记录，请重新设计并通过核查后再采用；未修改正式镜头。')


@router.post('/{identity}/shots/{shot_id}/vibe-intent')
def set_intent(identity: str, shot_id: str, data: Intent, request: Request):
    project = owned_project(request, identity)
    with studio.LOCK:
        record, shot = _target(project, shot_id, data.revision, reservation=True)
        if studio.has_vibe_output(shot):
            raise HTTPException(409, '本镜已采用 Vibe 动效，请先撤回采用')
        reserved = shot.get('vibe_reserved')
        if bool(reserved) == data.enabled:
            return record
        if data.enabled:
            if shot.get('kind') not in {'video','static'}:
                raise HTTPException(409, '本镜尚无有效的分镜类型')
            if not .1 <= float(shot['duration']) <= previews.MAX_SECONDS:
                raise HTTPException(422, f'本机 Vibe 动效支持 0.1 至 {previews.MAX_SECONDS} 秒，请先调整本镜范围')
            shot['vibe_reserved'] = dict(original_kind=shot['kind'],
                original_motion={key:copy.deepcopy(shot[key]) for key in _MOTION if key in shot})
            shot.update(kind='static', generation_duration=None, action='', video_prompt='',
                        video_prompt_warnings=[], warning='', kind_adjustment='已预留 Vibe 本机动效')
            shot.pop('motion_plan', None)
            note = '已预留 Vibe：先生成核心图，再预览并采用本机动效；不会调用视频模型。'
        else:
            for key in _MOTION:
                shot.pop(key,None)
            shot.update(reserved['original_motion'], kind=reserved['original_kind'])
            shot.pop('vibe_reserved')
            if shot['kind'] == 'video':
                shot['generation_duration'] = max(4,math.ceil(shot['duration']))
                if reserved.get('original_image_changed') and shot.get('video'):
                    studio._invalidate_shot_video(record,shot,'预留期间核心图发生变化，请检查原视频')
            note = '已取消 Vibe 预留，恢复原分镜类型及生成方式。'
        studio.discard_planning_resume(record)
        return _finish(project, record, shot_id+'：'+note)


def _origin(project,shot,preview_id):
    attempt = shot.get('video_attempt')
    if type(attempt) is not int or attempt < 1:
        raise HTTPException(409, '本镜动效版本记录不一致，未修改正式镜头')
    folder = project/'assets'/'videos'/shot['id']/f'attempt_{attempt:03d}'
    if shot.get('video') != (folder/'clip.mp4').relative_to(project).as_posix():
        raise HTTPException(409, '本镜动效文件不属于该版本，未修改正式镜头')
    previous = previews._read_json(previews._safe(project,folder/'vibe-origin.json',file=True), 4*1024**2)
    old = previous.get('original')
    if (previous.get('shot_id') != shot['id'] or previous.get('preview_id') != preview_id or
            not isinstance(old,dict) or old.get('kind') not in {'video','static'} or set(old) - set(_FIELDS)):
        raise HTTPException(409, '本镜原版本记录不一致，未修改正式镜头')
    return old


@router.post('/{identity}/shots/{shot_id}/vibe-adopt')
def adopt(identity: str, shot_id: str, data: Choice, request: Request):
    project = owned_project(request, identity)
    with studio.LOCK:
        record, shot = _target(project, shot_id, data.revision)
        if shot.get('image_status') != 'completed' or shot.get('design_needs_review'):
            raise HTTPException(409, '请先确认本镜设计并完成核心图，再采用动效')
        pointer = previews._pointer(project, shot_id)
        if not pointer.is_file() or previews._read_json(pointer, 4096).get('preview_id') != data.preview_id:
            raise HTTPException(409, '本镜已有其他预览或尚无预览；请刷新、核对后再采用')
        receipt = previews._receipt(project, data.preview_id)
        _require_quality_passed(receipt)
        stamp, _, _, _, start, end, _ = previews._snapshot(project, shot_id)
        if receipt.get('shot_id') != shot_id or receipt.get('input_stamp') != stamp:
            raise HTTPException(409, '本镜画面、配音或字幕已变；先重新预览，未改动正式镜头')
        if receipt.get('engine')=='cloud':
            from .vibe_cloud import validate_cloud_preview
            if not validate_cloud_preview(project,shot_id,receipt):
                raise HTTPException(409,'本镜提示词或参考素材已变；先重新预览，未改动正式镜头')
        current = shot.get('video_request') or {}
        previous_vibe = current.get('backend') == 'vibe'
        previous_clip_ok = (previous_vibe and shot.get('video') and
                            previews._safe(project, project/shot['video']).is_file() and
                            video._video_version(project/shot['video']) == shot.get('video_version'))
        if previous_clip_ok and current.get('preview_id') == data.preview_id and shot.get('video_status') == 'completed':
            adjustment = shot.get('audio_timing_adjustment') or {}
            if adjustment.get('needs_review'):
                adjustment['needs_review'] = False
                return _finish(project, record, f'{shot_id}：已重新核对并采用与当前时间轴相符的本机动效。')
            return record
        silent = previews._safe(project, previews._store(project)/data.preview_id/'silent.mp4', file=True)
        if not 0 < silent.stat().st_size <= 512*1024**2:
            raise HTTPException(409, '预览视频文件异常，未采用')
        attempt = video._next_confirmed_attempt(project, shot)
        base = previews._safe(project, project/'assets'/'videos'/shot_id)
        base.mkdir(parents=True, exist_ok=True)
        dest = previews._safe(project, base/f'attempt_{attempt:03d}')
        # Repair a missing/corrupted formal Vibe clip from a verified preview,
        # without turning that missing clip into the undo target.
        old = (_origin(project,shot,current.get('preview_id')) if previous_vibe and not previous_clip_ok else
               {k:copy.deepcopy(shot[k]) for k in _FIELDS if k in shot})
        with tempfile.TemporaryDirectory(prefix='.adopt-', dir=base) as tmp:
            stage = Path(tmp)/'version'
            stage.mkdir()
            clip = stage/'clip.mp4'
            shutil.copyfile(silent, clip)
            _verify_silent(clip, end-start)
            version = video._video_version(clip)
            (stage/'vibe-origin.json').write_text(json.dumps(dict(shot_id=shot_id, preview_id=data.preview_id,
                    original=old, audio_review=copy.deepcopy(shot.get('audio_timing_adjustment'))),
                    ensure_ascii=False), encoding='utf-8')
            publish(stage,dest)
        try:
            if not previous_vibe or previous_clip_ok:
                video._archive_current_video(project, shot, '采用本机分镜动效前保留的版本')
            shot.update(kind='video', kind_adjustment='已采用本镜 Vibe 动效', video_backend='vibe',
                    generation_duration=max(4, math.ceil(end-start)), video_attempt=attempt, video_status='completed',
                    video=(dest/'clip.mp4').relative_to(project).as_posix(), video_version=version,
                    video_request=dict(backend='vibe', preview_id=data.preview_id, input_stamp=stamp,
                                       duration=round(end-start,3), use_duration=shot.get('duration'),
                                       width=receipt['metrics'].get('width'),height=receipt['metrics'].get('height')),
                    video_state_file='', video_task_id='', video_error='', video_terminal=True,
                    video_resume_available=False, video_not_submitted=False, video_execution_started=True,
                    video_design_changed=False)
            shot.pop('video_resolution', None)
            shot.pop('vibe_reserved', None)
            if shot.get('audio_timing_adjustment'):
                shot['audio_timing_adjustment']['needs_review'] = False
            return _finish(project, record, f'{shot_id}：已采用本机 Vibe 无声动效，导出仍使用项目最终配音；原版本已保留。')
        except Exception:
            # Only this call created this private attempt. Never remove its parent.
            shutil.rmtree(dest, ignore_errors=True)
            raise


@router.post('/{identity}/shots/{shot_id}/vibe-undo')
def undo(identity: str, shot_id: str, data: Choice, request: Request):
    project = owned_project(request, identity)
    with studio.LOCK:
        record, shot = _target(project, shot_id, data.revision)
        current = shot.get('video_request') or {}
        if current.get('backend') != 'vibe' or current.get('preview_id') != data.preview_id:
            raise HTTPException(409, '当前正式镜头已改变，不能撤回其他版本')
        old = _origin(project,shot,data.preview_id)
        old_kind = (old.get('vibe_reserved') or {}).get('original_kind') or old.get('kind')
        if old_kind == 'video' and old.get('video_status') == 'completed':
            if not old.get('video') or not previews._safe(project, project/old['video']).is_file():
                raise HTTPException(409, '采用前的视频文件已不存在；当前动效保留')
        previous_review = previews._read_json(previews._safe(project, (project/shot['video']).parent/'vibe-origin.json',
                                            file=True), 4*1024**2).get('audio_review') or {}
        if previous_review.get('needs_review') and not (shot.get('audio_timing_adjustment') or {}).get('needs_review'):
            shot['audio_timing_adjustment'] = previous_review
        video._archive_current_video(project, shot, '撤回采用的本机 Vibe 版本')
        if old.get('video'):
            shot['video_history'] = [h for h in shot.get('video_history',[]) if h.get('video') != old['video']]
        for k in _FIELDS:
            shot.pop(k, None)
        shot.update(old)
        return _finish(project, record, f'{shot_id}：已撤回本次 Vibe 采用并恢复原镜头；配音、字幕和其他镜头保持不变。')
