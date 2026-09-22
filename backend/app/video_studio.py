"""Persisted first-stage video storyboard workspace."""
import copy
import json
import math
import hashlib
import re
import shutil
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Literal
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from .auth import require_user
from .video_plan import parse_srt, normalize_shots, edit_structure, plan_storyboard, planning_fingerprint
from .video_sources import list_sources, source_project, copy_assets, narration_groups
from . import video_scene_references as scene_references
from .video_text_policy import normalize_text_mode

router = APIRouter(prefix='/api/video-studio')
ROOT = Path(__file__).resolve().parents[2] / 'workspace' / 'video_studio'
LOCK = threading.RLock()
ACTIVE = set()
CANCEL_EVENTS = {}
IMAGE_EDITS = set()
VIDEO_STAGE_STATUSES = {'video_generation_ready', 'video_generating', 'video_stopping', 'video_review',
                        'exporting', 'export_failed', 'completed'}

def image_edit_key(record_id, asset_id):
    return f'{record_id}:{asset_id}'

def project_has_image_edits(record_id):
    prefix = f'{record_id}:'
    return any(str(item).startswith(prefix) for item in IMAGE_EDITS)

class PlanningStopped(Exception):
    pass

class Create(BaseModel):
    name: str = Field(default='动态视频草案', max_length=100)
    srt: str = Field(min_length=1, max_length=300000)
    style: str = Field(default='', max_length=10000)
    characters: str = Field(default='', max_length=10000)
    world: str = Field(default='', max_length=10000)
    ratio: str = '16:9'
    dynamic_text_mode: Literal['text_assisted', 'visual_first'] = 'text_assisted'
    scene_references_enabled: bool = True

class Edit(BaseModel):
    revision: int
    shots: list[dict]

class ImportProject(BaseModel):
    job_id: str = Field(min_length=1, max_length=80)
    name: str = Field(default='', max_length=100)
    style: str = Field(default='生动清晰的简笔画风格', max_length=10000)
    characters: str = Field(default='', max_length=10000)
    world: str = Field(default='', max_length=10000)
    ratio: str = Field(default='16:9', pattern=r'^(16:9|9:16)$')
    from_audio_task: bool = False
    dynamic_text_mode: Literal['text_assisted', 'visual_first'] = 'text_assisted'
    scene_references_enabled: bool = True

class Structure(BaseModel):
    revision: int
    action: str
    index: int
    boundary: int | None = None

class Review(BaseModel):
    revision: int

class ProjectSettingsEdit(Review):
    name: str = Field(max_length=100)
    style: str = Field(default='', max_length=10000)
    characters: str = Field(default='', max_length=10000)
    world: str = Field(default='', max_length=10000)
    dynamic_text_mode: Literal['text_assisted', 'visual_first'] = 'visual_first'
    scene_references_enabled: bool = True
    video_generation_backend: Literal['api', 'comfyui'] = 'api'
    comfyui_profile_id: str = Field(default='', max_length=80)
    comfyui_h3_prompt_agent: bool = False
    comfyui_reference_audio: bool = False
    dynamic_auto_advance: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)


class StoryboardConfirmation(Review):
    regenerate_shot_id: str | None = Field(default=None, max_length=100)

class DesignConfirmation(Review):
    shot_id: str

class StoryboardRedraw(BaseModel):
    revision: int
    prompt: str = Field(min_length=1, max_length=20000)
    reference_ids: list[str] = Field(default_factory=list, max_length=3)
    use_current_image: bool = False
    use_scene_reference: bool = True
    image_resolution: str | None = Field(default=None, pattern=r'^(1k|2k|4k)$')


class ShotPromptRefresh(Review):
    basis: str = Field(pattern=r'^(action|image)$')
    action: str = Field(default='', max_length=20000)
    image_prompt: str = Field(default='', max_length=20000)
    action_only: bool = False


class ShotMotionEdit(Review):
    kind: Literal['static', 'video']
    action: str = Field(default='', max_length=20000)
    video_prompt: str = Field(default='', max_length=20000)
    reference_audio_enabled: bool = True
    reference_audio_lipsync: bool = True


def _normalize_rgb_image(image: Path, *, strict: bool = False) -> bool:
    """Flatten transparent model output onto white and make .jpg honest JPEG."""
    from PIL import Image, ImageOps
    try:
        with Image.open(image) as source:
            actual_format = source.format
            transposed = ImageOps.exif_transpose(source)
            has_alpha = transposed.mode in {'RGBA', 'LA'} or (
                transposed.mode == 'P' and 'transparency' in transposed.info)
            if has_alpha:
                rgba = transposed.convert('RGBA')
                flattened = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
                flattened.alpha_composite(rgba)
                output = flattened.convert('RGB')
            else:
                output = transposed.convert('RGB')
            changed = has_alpha or actual_format != 'JPEG' or source.mode != 'RGB'
            if changed:
                temporary = image.with_name(image.name + '.rgb.tmp')
                output.save(temporary, format='JPEG', quality=95, subsampling=0)
                temporary.replace(image)
            return changed
    except Exception:
        if strict:
            raise
        return False


def _image_configs(record):
    import module4_video_render as visual
    snapshot = record.get('creation_parameters', {}).get('image_profile_snapshot')
    if isinstance(snapshot, dict):
        from .image_profiles import profile_provider_configs
        configs = profile_provider_configs(snapshot)
    else:
        configs = visual._provider_configs()
    if not configs:
        raise ValueError('请先在接口与服务中配置可用的图像 API')
    ratio = str(record.get('settings', {}).get('ratio') or '16:9')
    return [{**config, 'ratio': ratio} for config in configs]


def _shot_reference_paths(path, record, shot):
    catalog = {str(row.get('id')): row for row in record.get('references', [])}
    result = []
    for reference_id in shot.get('reference_ids', shot.get('reference_image_ids', [])):
        row = catalog.get(str(reference_id))
        candidate = path / str((row or {}).get('file') or '')
        if not row or not candidate.is_file() or path.resolve() not in candidate.resolve().parents:
            raise ValueError('镜头参考素材不存在，请重新选择素材')
        result.append(str(candidate.resolve()))
    return result


def _scene_asset(record, shot):
    return next((asset for asset in record.get('scene_assets', [])
                 if asset['id'] == shot.get('scene_reference_id')), None)


def _bind_material_numbers(record, shot, prompt):
    """Translate project labels to this request's selected material order once."""
    if shot.get('image_material_numbers_bound'):
        return prompt
    catalog = {row['id']: row for row in record.get('references', [])}
    rename = {catalog[identity].get('label', ''): f'图{index + 1}'
              for index, identity in enumerate(shot.get('reference_ids', [])) if identity in catalog}
    return re.sub(r'图\s*(\d+)(?!\d)', lambda match: rename.get('图' + match[1], match[0]), prompt)


def _image_inputs(path, record, shot, prompt, references=None, use_scene=True):
    """One input contract for first generation and subsequent image edits."""
    paths = list(references if references is not None else _shot_reference_paths(path, record, shot))
    prompt = scene_references.strip_scene_hint(prompt)
    if references is None:
        prompt = _bind_material_numbers(record, shot, prompt)
    asset = _scene_asset(record, shot) if use_scene and scene_references.enabled(record) else None
    if asset:
        if asset.get('image_status') != 'completed':
            raise ValueError('关联场景参考尚未生成，请先完成场景参考')
        paths.append(str(_storyboard_image_path(path, asset).resolve()))
        prompt = scene_references.scene_prompt(prompt, len(paths))
    if len(paths) > 4:
        raise ValueError('核心图最多接收 4 张参考图，启用场景参考时最多再使用 3 张人物或其他素材；请减少本镜头的参考素材')
    return prompt, paths, asset


def _image_version(path, item):
    item['image_version'] = hashlib.sha256(_storyboard_image_path(path, item).read_bytes()).hexdigest()


def _prepare_scene_assets(path, record, pool, cancelled):
    if not scene_references.enabled(record):
        record['scene_references_status'] = 'disabled'
        return
    with LOCK:
        record['scene_references_status'] = 'planning'
        record['logs'].append('场景协调员：核对各核心分镜共用的空间，只绑定相关镜头。')
        save(path, record)
    plan = scene_references.plan_references(path, record)
    if cancelled.is_set():
        raise PlanningStopped()
    existing = {asset['id']: asset for asset in record.get('scene_assets', [])}
    assets = []
    for entry in plan['scenes']:
        used_by = [record['shots'][index]['id'] for index in entry['members']]
        fingerprint = hashlib.sha256(json.dumps({
            'prompt': entry['reference_prompt'], 'members': used_by,
            'style': record['settings'].get('style', ''), 'world': record['settings'].get('world', ''),
            'ratio': record['settings'].get('ratio', '16:9')}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        identity = 'scene_ref_' + fingerprint
        asset = existing.get(identity) or {
            'id': identity, 'asset_kind': 'scene_reference', 'image_status': 'pending',
            'image_prompt': (entry['reference_prompt'].strip() +
                '\n纯场景资产：无人物、人体局部、人影或人形倒影；无字幕、对话气泡及临时特效。'),
            'image': f'assets/scene_references/{identity}.jpg'}
        asset.update(scene_id=entry['scene_id'], name=entry.get('name') or entry['scene_id'],
                     used_by=used_by, reason=entry['reason'])
        asset.setdefault('baseline_image_prompt', asset['image_prompt'])
        assets.append(asset)
    with LOCK:
        old_assets = list(record.get('scene_assets') or [])
        retained_ids = {asset['id'] for asset in assets}
        archived = record.setdefault('scene_asset_archive', [])
        archived.extend(asset for asset in old_assets if asset['id'] not in retained_ids)
        record.update(scene_reference_plan=plan, scene_assets=assets, scene_references_status='generating')
        for shot in record['shots']:
            shot.pop('scene_reference_id', None)
            shot['image_prompt'] = scene_references.strip_scene_hint(shot.get('image_prompt'))
        for asset in assets:
            for shot in record['shots']:
                if shot['id'] in asset['used_by']:
                    shot['scene_reference_id'] = asset['id']
        record['logs'].append(f'场景参考：找到 {len(assets)} 个共用空间；已生成的场景会复用。' if assets
                              else '场景参考：没有明确共用的空间，本轮无需额外生成场景图。')
        save(path, record)
    import module4_video_render as visual
    for asset in assets:
        if cancelled.is_set():
            raise PlanningStopped()
        target = path / asset['image']
        if asset.get('image_status') == 'completed' and target.is_file() and target.stat().st_size:
            if not asset.get('image_version'):
                _image_version(path, asset)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with LOCK:
            asset.update(image_status='running', image_error='')
            record['logs'].append(f'场景参考：正在生成“{asset["name"]}”无人场景图。')
            save(path, record)
        try:
            rendered = visual._render_poster_with_retry({
                'macro_scene_id': asset['id'], 'progress_label': '场景参考 · ' + asset['name'],
                'image_prompt': asset['image_prompt'], 'character_ids': [],
                'reference_image_ids': [], 'reference_image_paths': [], 'reference_binding_version': 1,
                '_output_path': str(target.resolve())}, pool)
            if not rendered.is_file() or not rendered.stat().st_size:
                raise ValueError('场景接口没有返回有效图片')
            _normalize_rgb_image(rendered)
            with LOCK:
                asset.update(image_status='completed', image_error='')
                _image_version(path, asset)
                record['logs'].append(f'场景参考：“{asset["name"]}”已生成，供 {len(asset["used_by"])} 个相关镜头使用。')
                save(path, record)
        except Exception as exc:
            with LOCK:
                asset.update(image_status='failed', image_error=str(exc))
                save(path, record)
            raise ValueError(f'场景参考“{asset["name"]}”生成失败：{exc}；可重试场景参考，已完成资产会保留') from exc
    with LOCK:
        for shot in record['shots']:
            asset = _scene_asset(record, shot)
            if asset:
                number = len(shot.get('reference_ids', shot.get('reference_image_ids', []))) + 1
                shot['image_prompt'] = scene_references.scene_prompt(shot.get('image_prompt'), number)
        record['scene_references_status'] = 'completed'
        save(path, record)


def _find_shot(record, shot_id):
    shot = next((row for row in record.get('shots', []) + record.get('scene_assets', [])
                 if str(row.get('id')) == shot_id), None)
    if shot is None:
        raise HTTPException(404, '核心分镜不存在')
    return shot


def _storyboard_image_path(path, shot):
    image = path / str(shot.get('image') or '')
    if not shot.get('image') or not image.is_file() or path.resolve() not in image.resolve().parents:
        raise HTTPException(404, '核心分镜图不存在')
    return image


def _archive_storyboard_image(path, shot):
    image = _storyboard_image_path(path, shot)
    history_dir = path / 'assets' / 'storyboard_history' / str(shot['id'])
    history_dir.mkdir(parents=True, exist_ok=True)
    archived = history_dir / f'{time.time_ns()}{image.suffix.lower()}'
    shutil.copy2(image, archived)
    history = list(shot.get('image_history') or [])
    history.append({'image': str(archived.relative_to(path)).replace('\\', '/'),
                    'prompt': str(shot.get('image_prompt') or ''), 'created_at': time.time(),
                    'image_origin': shot.get('image_origin', 'generated'),
                    'image_applied_prompt': shot.get('image_applied_prompt', shot.get('image_prompt', '')),
                    'scene_reference_used_version': shot.get('scene_reference_used_version', '')})
    shot['image_history'] = history
    shot.setdefault('baseline_image_prompt', str(shot.get('image_prompt') or ''))
    return image


def _invalidate_shot_video(record, shot, reason):
    """Keep paid artifacts on disk, but prevent a stale clip from being reused."""
    if shot.get('kind') != 'video' or not any(shot.get(key) for key in (
            'video_request', 'video_task_id', 'video', 'video_version')):
        return False
    history = list(shot.get('video_history') or [])
    history.append({key: copy.deepcopy(shot.get(key)) for key in (
        'video_attempt', 'video_task_id', 'video_state_file', 'video_request', 'video',
        'video_error', 'video_version', 'video_status')})
    history[-1].update(invalidated_at=time.time(), invalidated_reason=reason)
    shot['video_history'] = history
    for key in ('video_task_id', 'video_state_file', 'video_request', 'video', 'video_version'):
        shot.pop(key, None)
    shot.update(video_status='pending', video_error='', video_terminal=False,
                video_resume_available=False, video_not_submitted=True,
                video_execution_started=False)
    record.pop('export', None)
    record['logs'].append(f'{shot["id"]}：{reason}，原动态片段已保留但不再用于合成。')
    return True


def _start_storyboard_redraw(path, record, shot, data, configs, reference_paths, scene_version=''):
    import module4_video_render as visual
    shot_id = str(shot['id'])
    edit_key = image_edit_key(record['id'], shot_id)
    IMAGE_EDITS.add(edit_key)
    shot['image_task'] = {'status': 'running', 'action': 'redraw', 'message': '正在重绘核心分镜图'}
    record['logs'].append(f'{shot_id} 开始重绘，使用 {len(reference_paths)} 张参考图。')
    save(path, record)

    def worker():
        temporary = path / 'assets' / 'storyboard_redraw' / f'{shot_id}_{time.time_ns()}.jpg'
        temporary.parent.mkdir(parents=True, exist_ok=True)
        macro = {'macro_scene_id': shot_id, 'progress_label': f'{shot_id}（重绘）',
                 'image_prompt': data.prompt.strip(), 'reference_image_paths': reference_paths,
                 'reference_image_ids': [], 'reference_binding_version': 1,
                 '_output_path': str(temporary.resolve())}
        if reference_paths:
            macro['image_prompt'] = (
                f'【参考图编号】本次附带的第 1 至第 {len(reference_paths)} 张图片依次对应图1至图{len(reference_paths)}；'
                '提示词中提及图N时，必须严格以第N张参考图作为形象或画面依据。\n' + data.prompt.strip())
        try:
            pool = visual.shared_runninghub_account_pool(configs, namespace='video_storyboard_redraw')
            rendered = visual._render_poster_with_retry(macro, pool)
            if not rendered.is_file() or rendered.stat().st_size <= 0:
                raise FileNotFoundError('图像模型返回完成，但没有找到重绘图片')
            _normalize_rgb_image(rendered)
            with LOCK:
                latest = read(path)
                live_shot = _find_shot(latest, shot_id)
                image = _archive_storyboard_image(path, live_shot)
                shutil.copy2(rendered, image)
                live_shot.update(image_prompt=data.prompt.strip(), image_status='completed', image_error='',
                                 image_prompt_warnings=[],
                                 image_origin='reference_redraw' if reference_paths else 'prompt_redraw',
                                 image_applied_prompt=data.prompt.strip(), image_prompt_out_of_sync=False,
                                 image_material_numbers_bound=True,
                                 scene_reference_used_version=scene_version,
                                 image_task={'status': 'completed', 'action': 'redraw', 'message': '重绘完成，请检查效果'})
                _image_version(path, live_shot)
                _invalidate_shot_video(latest, live_shot, '核心分镜图已重绘')
                latest['revision'] += 1
                latest['logs'].append(f'{shot_id} 重绘完成。')
                save(path, latest)
        except Exception as exc:
            with LOCK:
                latest = read(path)
                live_shot = _find_shot(latest, shot_id)
                live_shot['image_task'] = {'status': 'failed', 'action': 'redraw', 'message': str(exc)}
                latest['revision'] += 1
                latest['logs'].append(f'{shot_id} 重绘失败：{exc}')
                save(path, latest)
        finally:
            temporary.unlink(missing_ok=True)
            with LOCK:
                IMAGE_EDITS.discard(edit_key)
    threading.Thread(target=worker, daemon=True).start()


def _start_storyboard_images(path, record, configs, *, scenes_only=False):
    import module4_video_render as visual
    cancelled = threading.Event()
    CANCEL_EVENTS[str(path)] = cancelled
    ACTIVE.add(str(path))

    def worker():
        try:
            pool = visual.shared_runninghub_account_pool(configs, namespace='video_storyboard')
            with LOCK:
                for shot in record['shots']:
                    shot['image_prompt'] = _bind_material_numbers(record, shot, shot.get('image_prompt', ''))
                    shot['image_material_numbers_bound'] = True
                save(path, record)
            _prepare_scene_assets(path, record, pool, cancelled)
            if cancelled.is_set():
                raise PlanningStopped()
            pending = [
                (index, shot) for index, shot in enumerate(record['shots'], start=1)
                if shot.get('image_status') != 'completed' or not shot.get('image')
            ] if not scenes_only else []
            per_key = visual._positive_env_int('RUNNINGHUB_PER_KEY_CONCURRENCY', 1)
            max_workers = min(len(pending), max(1, len(configs) * per_key))
            with LOCK:
                if not scenes_only:
                    record['logs'].append(
                    f'核心分镜图启用并行生成：{len(configs)} 个 API，'
                    f'每个 API 并行 {per_key}，本轮最多同时生成 {max_workers} 张。'
                    )
                save(path, record)

            def generate_one(index, shot):
                if cancelled.is_set():
                    return 'cancelled'
                with LOCK:
                    shot.update(image_status='running', image_error='')
                    record['logs'].append(f'核心分镜图 {index}/{len(record["shots"])}：正在生成 {shot["id"]}')
                    save(path, record)
                target = path / 'assets' / 'storyboards' / f'{shot["id"]}.jpg'
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    prompt, references, scene = _image_inputs(path, record, shot, shot.get('image_prompt'))
                    macro = {
                        'macro_scene_id': str(shot['id']),
                        'progress_label': f'核心分镜图 {index}/{len(record["shots"])}',
                        'image_prompt': prompt, 'reference_image_ids': [],
                        'reference_image_paths': references, 'reference_binding_version': 1,
                        '_output_path': str(target.resolve()),
                    }
                    with LOCK:
                        shot['image_prompt'] = prompt
                        if scene:
                            record['logs'].append(f'镜头 {index} 使用场景参考“{scene["name"]}”（图{len(references)}）。')
                        save(path, record)
                    rendered = visual._render_poster_with_retry(macro, pool)
                    _normalize_rgb_image(rendered)
                    with LOCK:
                        shot.update(image_status='completed', image='assets/storyboards/' + rendered.name,
                                    image_error='', image_origin='generated',
                                    image_applied_prompt=shot.get('image_prompt', ''), image_prompt_out_of_sync=False,
                                    scene_reference_used_version=scene.get('image_version', '') if scene else '')
                        _image_version(path, shot)
                        record['logs'].append(f'核心分镜图 {index}/{len(record["shots"])}：生成完成')
                        save(path, record)
                except Exception as exc:
                    with LOCK:
                        shot.update(image_status='failed', image_error=str(exc))
                        record['logs'].append(f'核心分镜图 {index}/{len(record["shots"])}：失败：{exc}')
                        save(path, record)
                    return 'failed'
                return 'completed'

            if pending:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='video-storyboard') as executor:
                    futures = [executor.submit(generate_one, index, shot) for index, shot in pending]
                    for future in as_completed(futures):
                        future.result()
            with LOCK:
                failed = sum(shot.get('image_status') == 'failed' for shot in record['shots'])
                completed = sum(shot.get('image_status') == 'completed' for shot in record['shots'])
                if scenes_only:
                    record['logs'].append('场景参考已准备完成；已有核心图保留不变，可选择关联镜头重绘以应用场景。')
                elif cancelled.is_set():
                    record['logs'].append(f'已停止核心图生成；保留已完成的 {completed} 张图片。')
                elif failed:
                    record['logs'].append(f'核心图生成结束：完成 {completed} 张，失败 {failed} 张；可重试失败项。')
                else:
                    record['logs'].append(f'全部 {completed} 张核心分镜图生成完成，请检查后进入动态视频生成。')
                record.update(status='image_review', revision=record['revision'] + 1)
                save(path, record)
        except PlanningStopped:
            with LOCK:
                record.update(status='image_review', revision=record['revision'] + 1)
                record['scene_references_status'] = 'stopped'
                record['logs'].append('场景参考/核心图生成已停止，已完成资产保留。')
                save(path, record)
        except Exception as exc:
            with LOCK:
                record.update(status='image_review', error=str(exc), revision=record['revision'] + 1)
                if record.get('scene_references_status') != 'completed':
                    record['scene_references_status'] = 'failed'
                record['logs'].append('核心图准备失败：' + str(exc))
                save(path, record)
        finally:
            with LOCK:
                ACTIVE.discard(str(path))
                CANCEL_EVENTS.pop(str(path), None)
    threading.Thread(target=worker, daemon=True).start()

def directory(user, identity):
    if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
        raise HTTPException(404, '视频草案不存在')
    return ROOT / str(user) / identity

def save(path, record):
    """Atomically persist one project without sharing a Windows temp filename.

    Background planning, image redraws, queue polling and browser requests can
    all save the same project.  A fixed ``record.tmp`` lets two writers race,
    and Windows may also hold the destination briefly while security/indexing
    software inspects it.  Serialize in-process writers, use a unique sibling
    temp file, and retry only the final atomic replace on transient sharing
    violations.
    """
    with LOCK:
        path.mkdir(parents=True, exist_ok=True)
        record['updated_at'] = time.time()
        target = path / 'record.json'
        temporary = path / f'.record.{uuid.uuid4().hex}.tmp'
        try:
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            for attempt in range(10):
                try:
                    temporary.replace(target)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.02 * (attempt + 1) ** 2)
        finally:
            temporary.unlink(missing_ok=True)

def planning_parameters(record):
    parameters = copy.deepcopy(record.get('creation_parameters', {}))
    settings = record.get('settings') or {}
    if 'dynamic_text_mode' in parameters or 'dynamic_text_mode' in settings:
        parameters['dynamic_text_mode'] = normalize_text_mode(
            parameters.get('dynamic_text_mode', settings.get('dynamic_text_mode')))
    if record.get('narration_groups'):
        parameters['_narration_groups'] = record['narration_groups']
    if record.get('manual_groups'):
        parameters['_manual_shot_groups'] = record['manual_groups']
    return parameters


def remember_structure(record, label):
    entry = dict(shots=copy.deepcopy(record['shots']),
                 manual_groups=copy.deepcopy(record.get('manual_groups')), label=label)
    record['structure_history'] = (record.get('structure_history', []) + [entry])[-10:]


def lock_manual_groups(record):
    record['manual_groups'] = [{'id': row['id'], 'slide_ids': list(row['slide_ids'])}
                               for row in record['shots']]


def resumable_planning_state(record):
    state = record.get('planning_state')
    settings = record.get('settings') or {}
    expected = planning_fingerprint(record['scenes'], settings.get('style', ''),
        settings.get('characters', ''), settings.get('world', ''), record.get('references', []),
        planning_parameters(record))
    return state if isinstance(state, dict) and state.get('fingerprint') == expected else None


def discard_planning_resume(record):
    for field in ('planning_state', 'planning_resume_available', 'planning_display_from_checkpoint',
                  'planning_checkpoint', 'planning_recovery'):
        record.pop(field, None)


def recover_planning(record):
    checkpoint = record.get('planning_checkpoint') or {}
    if (not record.get('shots') or record.get('planning_display_from_checkpoint')) and checkpoint.get('shots'):
        record.update(shots=checkpoint['shots'], context=checkpoint.get('context', {}))
        record['planning_display_from_checkpoint'] = True
        record['planning_recovery'] = ('规划未完成，已保留至“' + checkpoint.get('stage', '分镜草案') +
            '”。可在下方查看草案，补齐或修改最终提示词后保存并确认；也可重新规划（会再次调用 API）。')
    elif record.get('shots'):
        record['planning_recovery'] = '本次规划未完成，原有分镜与手动修改已保留。可继续编辑，或重新规划。'
    state = resumable_planning_state(record)
    record['planning_resume_available'] = bool(state)
    if state:
        record['planning_recovery'] = ('规划未完成，已保存至“' + state.get('stage', '全文理解') +
            '”。点击“继续未完成规划”将复用已完成步骤；也可编辑已有草案，保存修改后旧续跑进度会清除。')
    record['status'] = 'storyboard_review' if record.get('shots') else 'draft'


def read(path):
    if not (path / 'record.json').is_file():
        raise HTTPException(404, '视频草案不存在')
    record = json.loads((path / 'record.json').read_text(encoding='utf-8'))
    if not project_has_image_edits(record['id']):
        interrupted = [item for item in record.get('shots', []) + record.get('scene_assets', [])
                       if (item.get('image_task') or {}).get('status') == 'running']
        if interrupted:
            for item in interrupted:
                item['image_task'] = {'status': 'failed', 'action': 'redraw', 'message': '上次重绘已中断，原图保留，请检查后重试'}
            record['revision'] += 1
            save(path, record)
    if record['status'] in {'planning', 'stopping'} and str(path) not in ACTIVE:
        recover_planning(record)
        record['error'] = '上次规划已中断。'
        save(path, record)
    elif record['status'] in {'image_generating', 'image_stopping'} and str(path) not in ACTIVE:
        record.update(status='image_review', error='上次核心图生成已中断；已完成图片仍然保留，可重试未完成项。')
        if record.get('scene_references_status') in {'planning', 'generating'}:
            record['scene_references_status'] = 'stopped'
        save(path, record)
    elif record['status'] in {'video_generating', 'video_stopping'} and str(path) not in ACTIVE:
        from .video_generation import recover_interrupted
        recover_interrupted(path, record)
        save(path, record)
    if record['status'] == 'video_review':
        from .video_generation import recover_missing_clips
        if recover_missing_clips(path, record):
            save(path, record)
    return record

def editable(record, revision, *, allow_image_edits=False):
    if record['status'] in {'planning', 'stopping', 'image_generating', 'image_stopping', 'video_generating', 'video_stopping', 'exporting'} or (project_has_image_edits(record['id']) and not allow_image_edits) or record['revision'] != revision:
        raise HTTPException(409, '项目正在规划或已被其他页面修改，请重新读取')

@router.get('')
def listing(request: Request):
    user = require_user(request)
    with LOCK:
        rows = [read(p.parent) for p in (ROOT / str(user['id'])).glob('*/record.json')]
        from .video_export import reconcile_source_job
        for record in rows:
            if record.get('status') == 'completed':
                reconcile_source_job(record, int(user['id']))
    return {'items': sorted(rows, key=lambda r:r['updated_at'], reverse=True)}

@router.post('')
def create(data: Create, request: Request):
    user = require_user(request)
    try:
        scenes = parse_srt(data.srt)
        if data.ratio not in ('16:9','9:16'):
            raise ValueError('请选择横屏或竖屏')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record = dict(id=uuid.uuid4().hex, revision=1, status='draft', scenes=scenes,
                  settings=data.model_dump(exclude={'srt'}), shots=[], references=[], logs=[], error='',
                  creation_parameters={'dynamic_video': True, 'dynamic_text_mode': data.dynamic_text_mode,
                                       'scene_references_enabled': data.scene_references_enabled})
    with LOCK:
        save(directory(user['id'], record['id']), record)
    return record

@router.get('/sources')
def sources(request: Request):
    return {'items': list_sources(require_user(request)['id'])}


@router.post('/{identity}/settings')
def edit_project_settings(identity: str, data: ProjectSettingsEdit, request: Request):
    user_id = int(require_user(request)['id'])
    with LOCK:
        path = directory(user_id, identity)
        record = read(path)
        editable(record, data.revision)
        parameters = record.setdefault('creation_parameters', {})
        settings = record['settings']
        effective_reference_audio = bool(data.comfyui_reference_audio and
                                         not data.comfyui_h3_prompt_agent)
        if data.video_generation_backend == 'comfyui':
            from .comfyui_bridge import video_profile
            try:
                profile = video_profile(user_id, data.comfyui_profile_id)
                if effective_reference_audio:
                    binding = (profile.get('mappings') or {}).get('audio') or {}
                    if not binding.get('node_id') or not binding.get('input_name'):
                        raise ValueError('所选工作流尚未配置参考音频节点')
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        visual_changed = any(settings.get(key, '') != getattr(data, key)
                             for key in ('style', 'characters', 'world'))
        visual_changed |= normalize_text_mode(parameters.get('dynamic_text_mode', settings.get('dynamic_text_mode'))) != data.dynamic_text_mode
        visual_changed |= parameters.get('scene_references_enabled', True) != data.scene_references_enabled
        safe_parameter_keys = {
            'visual_pacing_preset', 'visual_min_duration', 'visual_target_duration',
            'visual_max_duration', 'visual_max_slides', 'image_profile_id',
            'image_resolution', 'visual_backend', 'video_orientation',
            'video_render_variant', 'subtitle_font', 'subtitle_size', 'subtitle_color',
            'subtitle_outline_color', 'subtitle_outline_width', 'subtitle_position',
            'subtitle_margin_bottom',
        }
        safe_parameters = {key: value for key, value in data.parameters.items() if key in safe_parameter_keys}
        planning_parameter_keys = safe_parameter_keys - {
            'video_render_variant', 'subtitle_font', 'subtitle_size', 'subtitle_color',
            'subtitle_outline_color', 'subtitle_outline_width', 'subtitle_position',
            'subtitle_margin_bottom',
        }
        render_parameter_keys = safe_parameter_keys - planning_parameter_keys
        visual_changed |= any(parameters.get(key) != safe_parameters[key] for key in planning_parameter_keys if key in safe_parameters)
        render_changed = any(parameters.get(key) != safe_parameters[key] for key in render_parameter_keys if key in safe_parameters)
        record.setdefault('edit_history', []).append({
            'at': time.time(), 'settings': copy.deepcopy(settings),
            'creation_parameters': copy.deepcopy(parameters), 'status': record['status'],
            'shots': copy.deepcopy(record['shots']) if visual_changed else None,
        })
        record['edit_history'] = record['edit_history'][-10:]
        settings.update(name=data.name, style=data.style, characters=data.characters,
                        world=data.world, dynamic_text_mode=data.dynamic_text_mode)
        parameters.update(project_name=data.name, visual_style_prompt=data.style,
                          global_character_prompt=data.characters, story_environment_prompt=data.world,
                          **data.model_dump(exclude={'revision', 'name', 'style', 'characters', 'world', 'parameters'}),
                          **safe_parameters)
        # H3 uses OCV's final narration only during export. Stale values from
        # an older audio-enabled profile must not require an audio node.
        parameters['comfyui_reference_audio'] = effective_reference_audio
        if visual_changed:
            for shot in record['shots']:
                _invalidate_shot_video(record, shot, '全局画面设定已调整')
            record['shots'] = []
            for key in ('manual_groups', 'context', 'scene_assets', 'scene_reference_plan',
                        'scene_references_status', 'export', 'reedit_shot_id', 'reedit_return_status'):
                record.pop(key, None)
            discard_planning_resume(record)
            record['status'] = 'draft'
            note = '画面设定已保存，已确认配音、字幕与时间戳继续复用；请重新规划分镜。原分镜已存入编辑历史。'
        elif render_changed:
            record.pop('export', None)
            if record['status'] in {'completed', 'exporting', 'export_failed'}:
                record['status'] = 'video_review'
            note = '字幕或成片样式已保存；配音、字幕时间戳、分镜图和动态片段继续复用，请重新导出成片。'
        else:
            note = '生成配置已保存，将用于后续生成或单镜重生成；已有配音、图片和动态片段继续保留。'
        record.update(revision=record['revision'] + 1, error='')
        record['logs'].append(note)
        save(path, record)
        return record


@router.post('/from-project')
def import_project(data: ImportProject, request: Request):
    user = require_user(request)
    with LOCK:
        if data.from_audio_task:
            existing = find_audio_storyboard(user['id'], data.job_id)
            if existing:
                return existing
        return import_new_project(data, user)


def find_audio_storyboard(user_id, job_id):
    records = [read(p.parent) for p in (ROOT / str(user_id)).glob('*/record.json')]
    matches = [r for r in records if r.get('source_project', {}).get('id') == job_id
               and r.get('creation_parameters', {}).get('dynamic_video')]
    # Older versions could create several drafts. Prefer saved work over empty drafts.
    return max(matches, key=lambda r: (bool(r.get('shots')), r.get('updated_at', 0)), default=None)


@router.get('/by-audio-task/{job_id}')
def by_audio_task(job_id: str, request: Request):
    with LOCK:
        return {'project': find_audio_storyboard(require_user(request)['id'], job_id)}


@router.post('/{identity}/sync-audio')
def sync_edited_audio(identity: str, data: Review, request: Request):
    user_id = int(require_user(request)['id'])
    with LOCK:
        path = directory(user_id, identity)
        record = read(path)
        editable(record, data.revision)
        source_job_id = record.get('source_project', {}).get('id', '')
        from .video_sources import dependencies
        store, editor, _ = dependencies()
        job = store.get(source_job_id)
        if not job or str(job.user_id) != str(user_id):
            raise HTTPException(404, '来源配音任务不存在')
        if not job.request.get('dynamic_video'):
            raise HTTPException(409, '来源任务不是动态视频的配音阶段')
        try:
            source = editor.output_dir(job.id, user_id)
        except FileNotFoundError as exc:
            raise HTTPException(409, '来源配音任务的编辑目录不存在') from exc
        from .tts_editor import tts_editor
        if tts_editor.status(job.id).get('status') == 'running':
            raise HTTPException(409, '配音仍在修改，请等待完成')
        try:
            tts_editor.commit_step_review(job=job, user_id=user_id)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(409, f'配音与字幕尚未保存完整：{exc}') from exc
        if not (source / 'other' / '最终字幕.srt').is_file() or not (source / 'input' / '配音.wav').is_file():
            raise HTTPException(409, '保存后仍缺少最终字幕或配音文件，请保留任务并联系支持')
        scenes = parse_srt((source / 'other' / '最终字幕.srt').read_text(encoding='utf-8-sig'))
        old = record['scenes']
        audio_path = path / record['audio']
        new_audio = source / 'input' / '配音.wav'
        same_structure = len(old) == len(scenes)
        if audio_path.read_bytes() == new_audio.read_bytes() and old == scenes:
            return record
        record.setdefault('edit_history', []).append({'at': time.time(), 'scenes': copy.deepcopy(old), 'shots': copy.deepcopy(record['shots']), 'reason': '配音编辑'})
        record['edit_history'] = record['edit_history'][-10:]
        if same_structure:
            by_id = {s['slide_id']: s for s in scenes}
            old_by_id = {s['slide_id']: s for s in old}
            def signature(file, start, end):
                with wave.open(str(file), 'rb') as stream:
                    rate = stream.getframerate()
                    stream.setpos(min(stream.getnframes(), round(start * rate)))
                    return (stream.getnchannels(), stream.getsampwidth(), rate,
                            hashlib.sha256(stream.readframes(round((end-start)*rate))).hexdigest())
            for shot in record['shots']:
                text_changed = any(old_by_id[sid]['text'] != by_id[sid]['text'] for sid in shot['slide_ids'])
                start = by_id[shot['slide_ids'][0]]['start']
                end = by_id[shot['slide_ids'][-1]]['end']
                try:
                    changed = signature(audio_path, shot['start'], shot['end']) != signature(new_audio, start, end)
                except (wave.Error, EOFError):
                    changed = True
                duration = round(end-start, 3)
                if changed or text_changed or abs(duration-shot['duration']) > .01:
                    _invalidate_shot_video(record, shot, '本镜所属配音已修改')
                if text_changed:
                    shot['design_needs_review'] = True
                    shot['image_prompt_out_of_sync'] = True
                shot.update(start=start, end=end, duration=duration,
                            generation_duration=max(4, math.ceil(duration)) if shot['kind']=='video' else None)
                if duration > 15 and shot['kind']=='video':
                    shot['design_needs_review'] = True
            record['status'] = ('storyboard_review' if any(s.get('design_needs_review') for s in record['shots'])
                                else 'video_review' if record['shots'] and all(s.get('image_status')=='completed' for s in record['shots'])
                                else 'storyboard_review' if record['shots'] else 'draft')
            note = '已同步配音与时间戳；保留核心图，仅受影响镜头需要重新生成动态。'
        else:
            # Text/boundary changes need a fresh subtitle-to-shot mapping.
            record['shots'] = []
            record.pop('manual_groups', None)
            record.pop('context', None)
            discard_planning_resume(record)
            record['status'] = 'draft'
            note = '字幕文字或分段已调整，已复用新配音并保存原分镜历史，请重新规划画面。'
        record['audio'] = copy_assets(source, path)
        record['scenes'] = scenes
        record['narration_groups'] = narration_groups(source, scenes)
        record.pop('export', None)
        record.update(revision=record['revision']+1, error='')
        record['logs'].append(note)
        save(path, record)
        return record


def import_new_project(data, user):
    job, source = source_project(user['id'], data.job_id, audio_task=data.from_audio_task)
    if data.from_audio_task:
        from .tts_editor import tts_editor
        if not job.request.get('dynamic_video'):
            raise HTTPException(409, '该任务不是动态视频的配音阶段')
        if tts_editor.status(job.id).get('status') == 'running':
            raise HTTPException(409, '配音仍在修改，请等待完成')
        from .pipeline import validate_step_audio_snapshot
        try:
            tts_editor.commit_step_review(job=job, user_id=int(user['id']))
            validate_step_audio_snapshot(job)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(409, f'配音与字幕快照尚未一致，请先完成精修：{exc}') from exc
    try:
        scenes = parse_srt((source / 'other' / '最终字幕.srt').read_text(encoding='utf-8-sig'))
        identity = uuid.uuid4().hex
        target = directory(user['id'], identity)
        audio = copy_assets(source, target)
        record = dict(id=identity, revision=1, status='draft', scenes=scenes, shots=[], logs=[], error='',
                      references=[], source_images=[], audio=audio, import_scope='audio_subtitles',
                      narration_groups=narration_groups(source, scenes),
                      source_project={'id': job.id, 'name': source.name},
                      settings=dict(name=data.name.strip() or (source.name[:90] + ' · 动态版'),
                                    style=data.style, characters=data.characters,
                                    world=data.world, ratio=data.ratio, dynamic_text_mode=data.dynamic_text_mode),
                      creation_parameters={'dynamic_video': True, 'dynamic_text_mode': data.dynamic_text_mode,
                                           'scene_references_enabled': data.scene_references_enabled})
        if data.from_audio_task:
            # These are this NEW video's own settings, not inherited source visuals.
            config = {k: v for k, v in job.request.items()
                      if not k.startswith('_') and 'key' not in k.lower() and 'token' not in k.lower()}
            record['creation_parameters'] = config
            record['settings'].update(
                name=config.get('project_name') or record['settings']['name'],
                style=config.get('visual_style_prompt') or '',
                characters=config.get('global_character_prompt') or '',
                world=config.get('story_environment_prompt') or '',
                dynamic_text_mode=normalize_text_mode(config.get('dynamic_text_mode')),
                ratio='9:16' if config.get('video_orientation') == 'portrait' else '16:9')
            from .reference_materials import request_reference_catalog
            from .pipeline import user_reference_image_path
            import shutil
            for index, row in enumerate(request_reference_catalog(config), start=1):
                original = user_reference_image_path(int(user['id']), row['asset_id'])
                if not original.is_file():
                    raise ValueError(f'新任务参考素材缺失：{row["label"]}')
                relative = f'assets/references/ref_{index:02d}{original.suffix.lower()}'
                copied = target / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, copied)
                record['references'].append(dict(id=f'ref_{index:02d}', label=row['label'],
                    description=row['description'], kind=row['kind'], file=relative))
        with LOCK:
            save(target, record)
        return record
    except (OSError, ValueError) as exc:
        raise HTTPException(400, f'无法导入来源项目：{exc}') from exc


@router.get('/{identity}/audio')
def audio(identity: str, request: Request):
    from fastapi.responses import FileResponse
    path = directory(require_user(request)['id'], identity)
    read(path)
    file = path / 'assets' / 'audio.wav'
    if not file.is_file():
        raise HTTPException(404, '本草案没有配音')
    return FileResponse(file, media_type='audio/wav')


@router.get('/{identity}')
def get(identity: str, request: Request):
    with LOCK:
        user_id = int(require_user(request)['id'])
        record = read(directory(user_id, identity))
        if record.get('status') == 'completed':
            from .video_export import reconcile_source_job
            reconcile_source_job(record, user_id)
        return record

@router.put('/{identity}')
def update(identity: str, data: Edit, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] == 'image_review' or record['status'] in VIDEO_STAGE_STATUSES:
            raise HTTPException(409, '核心图阶段请使用单图编辑工具，已确认的分镜结构不可修改')
        try:
            previous = {shot['id']: shot for shot in record['shots']}
            incoming = copy.deepcopy(data.shots)
            if record.get('manual_groups') and [(row.get('id'), row['slide_ids']) for row in incoming] != [(row['id'], row['slide_ids']) for row in record['shots']]:
                raise ValueError('请通过调整边界、拆分或合并修改字幕分组，以保留设计和撤回记录')
            for row in incoming:
                old = previous.get(row.get('id'), {})
                row['design_needs_review'] = bool(old.get('design_needs_review'))
                row['previous_designs'] = old.get('previous_designs', [])
            normalized = normalize_shots(incoming, record['scenes'], [r['id'] for r in record['references']])
            for shot in normalized:
                old = previous.get(shot['id'], {})
                if old.get('design_needs_review'):
                    shot['design_needs_review'] = True
                    shot['previous_designs'] = old.get('previous_designs', [])
                for field in ('image_prompt', 'video_prompt'):
                    if shot[field] != previous.get(shot['id'], {}).get(field, ''):
                        shot[field + '_warnings'] = []
            record['shots'] = normalized
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        record.update(revision=record['revision']+1, status='storyboard_review')
        discard_planning_resume(record)
        save(path, record)
        return record

@router.post('/{identity}/structure')
def structure(identity: str, data: Structure, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] == 'image_review' or record['status'] in VIDEO_STAGE_STATUSES:
            raise HTTPException(409, '核心图阶段已确认分镜结构，不能拆分或合并')
        try:
            changed = edit_structure(record['shots'], record['scenes'], data.action, data.index,
                                             data.boundary, [r['id'] for r in record['references']])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        remember_structure(record, data.action)
        record['shots'] = changed
        lock_manual_groups(record)
        record.update(revision=record['revision']+1, status='storyboard_review')
        discard_planning_resume(record)
        save(path, record)
        return record


@router.post('/{identity}/structure/undo')
def undo_structure(identity: str, data: Review, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] not in {'draft', 'storyboard_review'}:
            raise HTTPException(409, '请在分镜确认前撤回结构调整')
        history = record.get('structure_history', [])
        if not history:
            raise HTTPException(409, '没有可撤回的结构调整')
        snapshot = history.pop()
        record['shots'] = snapshot['shots']
        if snapshot.get('manual_groups'):
            record['manual_groups'] = snapshot['manual_groups']
        else:
            record.pop('manual_groups', None)
        discard_planning_resume(record)
        record.update(revision=record['revision'] + 1, error='', status='storyboard_review')
        save(path, record)
        return record


@router.post('/{identity}/design/confirm')
def confirm_adjusted_design(identity: str, data: DesignConfirmation, request: Request):
    from .video_agents import audit_storyboard
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] not in {'draft', 'storyboard_review'}:
            raise HTTPException(409, '请在分镜确认前检查设计')
        shot = next((row for row in record['shots'] if row['id'] == data.shot_id), None)
        if shot is None:
            raise HTTPException(404, '镜头不存在')
        candidate = copy.deepcopy(record['shots'])
        for row in candidate:
            if row['id'] == data.shot_id:
                row['design_needs_review'] = False
        try:
            normalized = normalize_shots(candidate, record['scenes'], [r['id'] for r in record['references']])
            audit_storyboard([row for row in normalized if row['id'] == data.shot_id], {r['id'] for r in record['references']})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        record['shots'] = normalized
        discard_planning_resume(record)
        record['revision'] += 1
        save(path, record)
        return record


@router.post('/{identity}/review')
def review(identity: str, data: Review, request: Request):
    from .video_agents import audit_storyboard
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] == 'image_review' or record['status'] in VIDEO_STAGE_STATUSES:
            raise HTTPException(409, '分镜方案已经确认，请使用核心图检查阶段的操作')
        try:
            normalized = normalize_shots(record['shots'], record['scenes'],
                                         [row['id'] for row in record['references']])
            record['shots'] = audit_storyboard(normalized, {row['id'] for row in record['references']})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        record.update(revision=record['revision']+1, status='generation_ready',
                      reviewed_at=time.time(), error='')
        discard_planning_resume(record)
        record['logs'].append('分镜、时长、参考图编号及提示词合同校验通过；等待用户逐镜生成。')
        save(path, record)
        return record


@router.post('/{identity}/images/generate')
def generate_storyboard_images(identity: str, data: Review, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] not in {'generation_ready', 'image_review'}:
            raise HTTPException(409, '请先确认分镜方案')
        try:
            configs = _image_configs(record)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        unfinished = [shot for shot in record['shots'] if shot.get('image_status') != 'completed']
        if not unfinished:
            raise HTTPException(409, '核心分镜图已经全部生成')
        record.update(status='image_generating', error='', revision=record['revision'] + 1)
        record['logs'].append(f'开始生成核心分镜图；本轮共 {len(unfinished)} 张。')
        save(path, record)
        _start_storyboard_images(path, record, configs)
        return record


@router.post('/{identity}/images/stop')
def stop_storyboard_images(identity: str, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        event = CANCEL_EVENTS.get(str(path))
        if event is not None and record['status'] in {'image_generating', 'image_stopping'}:
            event.set()
            record['status'] = 'image_stopping'
            record['logs'].append('已请求停止；当前已经提交的图片会等待结果，其余图片不再提交。')
            save(path, record)
        return record


@router.post('/{identity}/scene-references/generate')
def generate_scene_references(identity: str, data: Review, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心图检查阶段补充场景参考')
        if not scene_references.enabled(record):
            raise HTTPException(409, '这个任务未启用叙事增强的场景参考')
        try:
            configs = _image_configs(record)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        record.update(status='image_generating', error='', revision=record['revision'] + 1)
        record['logs'].append('开始补充场景参考，仅准备共用场景资产；不自动重绘已有核心图。')
        save(path, record)
        _start_storyboard_images(path, record, configs, scenes_only=True)
        return record


@router.get('/{identity}/images/{shot_id}')
def storyboard_image(identity: str, shot_id: str, request: Request):
    from fastapi.responses import FileResponse
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        shot = _find_shot(record, shot_id)
        image = _storyboard_image_path(path, shot)
        _normalize_rgb_image(image)
        return FileResponse(image)


@router.post('/{identity}/redraw-references')
async def upload_storyboard_reference(identity: str, request: Request, file: UploadFile = File(...)):
    user = require_user(request)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise HTTPException(400, '参考图仅支持 JPG、JPEG、PNG 或 WebP')
    content = await file.read()
    if not content or len(content) > 30 * 1024 * 1024:
        raise HTTPException(400, '参考图大小必须在 1 字节到 30 MB 之间')
    with LOCK:
        path = directory(user['id'], identity)
        record = read(path)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段上传重绘参考图')
        if project_has_image_edits(record['id']):
            raise HTTPException(409, '请等待当前重绘完成后再上传参考图')
        identity_value = uuid.uuid4().hex
        target = path / 'assets' / 'redraw_references' / f'{identity_value}{suffix}'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        try:
            from PIL import Image
            with Image.open(target) as image:
                image.verify()
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(400, '上传文件不是有效图片') from exc
        rows = list(record.get('redraw_references') or [])
        rows.append({'id': identity_value, 'name': Path(file.filename or '参考图').name,
                     'file': str(target.relative_to(path)).replace('\\', '/')})
        record['redraw_references'] = rows[-30:]
        record['revision'] += 1
        save(path, record)
        return record


def _redraw_reference_row(record, reference_id):
    for origin, rows in (('project', record.get('references', [])),
                         ('uploaded', record.get('redraw_references', []))):
        row = next((item for item in rows if str(item.get('id')) == str(reference_id)), None)
        if row:
            return origin, row
    return None, None


@router.get('/{identity}/redraw-references/{reference_id}')
def storyboard_reference_image(identity: str, reference_id: str, request: Request):
    from fastapi.responses import FileResponse
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        _, row = _redraw_reference_row(record, reference_id)
        candidate = path / str((row or {}).get('file') or '')
        if not row or not candidate.is_file() or path.resolve() not in candidate.resolve().parents:
            raise HTTPException(404, '参考图不存在')
        return FileResponse(candidate)


@router.delete('/{identity}/redraw-references/{reference_id}')
def delete_storyboard_reference(identity: str, reference_id: str, revision: int, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, revision, allow_image_edits=True)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段管理重绘参考图')
        if project_has_image_edits(record['id']):
            raise HTTPException(409, '请等待当前重绘完成后再删除参考图')
        origin, row = _redraw_reference_row(record, reference_id)
        if origin != 'uploaded' or not row:
            raise HTTPException(400, '任务原始参考图只能取消选择，不能在这里删除')
        candidate = path / str(row.get('file') or '')
        if candidate.is_file() and path.resolve() in candidate.resolve().parents:
            candidate.unlink(missing_ok=True)
        record['redraw_references'] = [item for item in record.get('redraw_references', [])
                                       if str(item.get('id')) != str(reference_id)]
        for shot in record.get('shots', []):
            selection = shot.get('redraw_selection')
            if isinstance(selection, dict):
                selection['reference_ids'] = [value for value in selection.get('reference_ids', [])
                                              if str(value) != str(reference_id)]
        record['revision'] += 1
        save(path, record)
        return record


@router.delete('/{identity}/redraw-references')
def clear_storyboard_references(identity: str, revision: int, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, revision, allow_image_edits=True)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段管理重绘参考图')
        if project_has_image_edits(record['id']):
            raise HTTPException(409, '请等待当前重绘完成后再清空参考图')
        uploaded_ids = {str(row.get('id')) for row in record.get('redraw_references', [])}
        for row in record.get('redraw_references', []):
            candidate = path / str(row.get('file') or '')
            if candidate.is_file() and path.resolve() in candidate.resolve().parents:
                candidate.unlink(missing_ok=True)
        record['redraw_references'] = []
        for shot in record.get('shots', []):
            selection = shot.get('redraw_selection')
            if isinstance(selection, dict):
                selection['reference_ids'] = [value for value in selection.get('reference_ids', [])
                                              if str(value) not in uploaded_ids]
        record['revision'] += 1
        save(path, record)
        return record


@router.post('/{identity}/images/{shot_id}/redraw')
def redraw_storyboard_image(identity: str, shot_id: str, data: StoryboardRedraw, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision, allow_image_edits=True)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段重绘')
        shot = _find_shot(record, shot_id)
        if (shot.get('image_task') or {}).get('status') == 'running':
            raise HTTPException(409, '当前画面正在重绘，请等待完成')
        references = {str(row['id']): row for row in [*record.get('references', []),
                                                       *record.get('redraw_references', [])]}
        if len(set(data.reference_ids)) != len(data.reference_ids) or any(value not in references for value in data.reference_ids):
            raise HTTPException(400, '重绘参考图选择无效')
        if len(data.reference_ids) + int(data.use_current_image) > 3:
            raise HTTPException(400, '每次重绘最多使用 3 张参考图（包含当前画面）')
        reference_paths = []
        if data.use_current_image:
            reference_paths.append(str(_storyboard_image_path(path, shot).resolve()))
        for reference_id in data.reference_ids:
            candidate = path / references[reference_id]['file']
            if not candidate.is_file() or path.resolve() not in candidate.resolve().parents:
                raise HTTPException(404, '重绘参考图不存在，请重新上传')
            reference_paths.append(str(candidate.resolve()))
        shot['redraw_selection'] = {
            'reference_ids': list(data.reference_ids),
            'use_current_image': bool(data.use_current_image),
            'use_scene_reference': bool(data.use_scene_reference),
        }
        try:
            prompt, reference_paths, scene = _image_inputs(
                path, record, shot, data.prompt,
                references=reference_paths,
                use_scene=data.use_scene_reference)
            configs = _image_configs(record)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if data.image_resolution:
            configs = [{**config, 'resolution': data.image_resolution} for config in configs]
        data = data.model_copy(update={'prompt': prompt})
        record['revision'] += 1
        _start_storyboard_redraw(path, record, shot, data, configs, reference_paths,
                                 scene.get('image_version', '') if scene else '')
        return record


@router.post('/{identity}/images/{shot_id}/upload')
async def replace_storyboard_image(identity: str, shot_id: str, request: Request,
                                   revision: int = Form(...), prompt: str = Form(''),
                                   file: UploadFile = File(...)):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise HTTPException(400, '替换图片仅支持 JPG、JPEG、PNG 或 WebP')
    content = await file.read()
    if not content or len(content) > 30 * 1024 * 1024:
        raise HTTPException(400, '替换图片大小必须在 1 字节到 30 MB 之间')
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, revision)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段替换图片')
        shot = _find_shot(record, shot_id)
        if (shot.get('image_task') or {}).get('status') == 'running':
            raise HTTPException(409, '该画面正在重绘')
        temporary = path / 'assets' / 'storyboard_uploads' / f'{shot_id}_{time.time_ns()}{suffix}'
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(content)
        try:
            image = _archive_storyboard_image(path, shot)
            _normalize_rgb_image(temporary, strict=True)
            shutil.copy2(temporary, image)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, '上传文件不是有效图片') from exc
        finally:
            temporary.unlink(missing_ok=True)
        if prompt.strip():
            shot['image_prompt'] = prompt.strip()
            shot['image_prompt_warnings'] = []
        shot['image_task'] = {'status': 'completed', 'action': 'upload', 'message': '已替换本地图片'}
        shot['image_origin'] = 'upload'
        shot['image_applied_prompt'] = prompt.strip() or shot.get('image_prompt', '')
        shot['image_prompt_out_of_sync'] = False
        shot['scene_reference_used_version'] = ''
        _image_version(path, shot)
        _invalidate_shot_video(record, shot, '核心分镜图已替换')
        record['revision'] += 1
        record['logs'].append(f'{shot_id} 已替换本地图片。')
        save(path, record)
        return record


@router.post('/{identity}/images/{shot_id}/undo')
def undo_storyboard_image(identity: str, shot_id: str, data: Review, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段撤回图片')
        shot = _find_shot(record, shot_id)
        history = list(shot.get('image_history') or [])
        if not history:
            raise HTTPException(409, '当前画面没有可撤回的历史版本')
        previous = history.pop()
        archived = path / previous['image']
        if not archived.is_file() or path.resolve() not in archived.resolve().parents:
            raise HTTPException(404, '历史图片不存在')
        image = _storyboard_image_path(path, shot)
        discarded = path / 'assets' / 'storyboard_undo_discarded' / str(shot_id) / f'{time.time_ns()}{image.suffix}'
        discarded.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image, discarded)
        shutil.copy2(archived, image)
        _normalize_rgb_image(image)
        shot.update(image_history=history, image_prompt=str(previous.get('prompt') or shot.get('image_prompt') or ''),
                    image_prompt_warnings=[],
                    image_origin=previous.get('image_origin', 'generated'),
                    image_applied_prompt=previous.get('image_applied_prompt', previous.get('prompt', '')),
                    image_prompt_out_of_sync=False,
                    scene_reference_used_version=previous.get('scene_reference_used_version', ''),
                    image_task={'status': 'completed', 'action': 'undo', 'message': '已撤回到上一版图片'})
        _image_version(path, shot)
        _invalidate_shot_video(record, shot, '核心分镜图已撤回到历史版本')
        record['revision'] += 1
        record['logs'].append(f'{shot_id} 已撤回到上一版图片。')
        save(path, record)
        return record


@router.post('/{identity}/images/{shot_id}/reset-prompt')
def reset_storyboard_prompt(identity: str, shot_id: str, data: Review, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段恢复提示词')
        shot = _find_shot(record, shot_id)
        baseline = str(shot.get('baseline_image_prompt') or '')
        if not baseline:
            raise HTTPException(409, '当前画面还没有初始提示词记录')
        shot.update(image_prompt=baseline, image_prompt_warnings=[])
        record['revision'] += 1
        save(path, record)
        return record


@router.post('/{identity}/shots/{shot_id}/motion')
def edit_shot_motion(identity: str, shot_id: str, data: ShotMotionEdit, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段调整动静态')
        shot = _find_shot(record, shot_id)
        if shot.get('asset_kind') == 'scene_reference':
            raise HTTPException(400, '场景参考不占用视频时间轴')
        if data.kind == 'video' and float(shot['duration']) > 15:
            raise HTTPException(400, '本镜超过15秒，请先调整分镜边界，不能直接转为动态')
        changed_kind = shot['kind'] != data.kind
        changed_motion = (shot.get('action', '') != data.action.strip() or
                          shot.get('video_prompt', '') != data.video_prompt.strip() or
                          bool(shot.get('reference_audio_enabled', True)) != data.reference_audio_enabled or
                          bool(shot.get('reference_audio_lipsync', True)) != data.reference_audio_lipsync)
        if shot.get('action', '') != data.action.strip():
            shot.pop('motion_plan', None)
        if shot.get('video_prompt', '') != data.video_prompt.strip():
            shot['video_prompt_warnings'] = []
        shot.update(kind=data.kind, action=data.action.strip(), video_prompt=data.video_prompt.strip(),
                    reference_audio_enabled=data.reference_audio_enabled,
                    reference_audio_lipsync=data.reference_audio_lipsync,
                    generation_duration=max(4, math.ceil(shot['duration'])) if data.kind == 'video' else None)
        if changed_kind:
            shot['kind_adjustment'] = '用户手动选择动态' if data.kind == 'video' else '用户手动选择静态'
            record['logs'].append(f'{shot_id}：{shot["kind_adjustment"]}；核心图和字幕时间轴保持不变。')
        if changed_motion:
            _invalidate_shot_video(record, shot, '动态表达或视频提示词已修改')
        record['revision'] += 1
        save(path, record)
        return record


@router.post('/{identity}/shots/{shot_id}/refresh-prompts')
def refresh_shot_prompts(identity: str, shot_id: str, data: ShotPromptRefresh, request: Request):
    user = require_user(request)
    with LOCK:
        path = directory(user['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] not in {'storyboard_review', 'image_review'}:
            raise HTTPException(409, '请在分镜确认或核心分镜图检查阶段更新提示词')
        shot = _find_shot(record, shot_id)
        if shot.get('asset_kind') == 'scene_reference' or shot.get('kind') != 'video':
            raise HTTPException(409, '只有动态镜头需要重新规划视频提示词')
        if data.action_only and (data.basis != 'image' or data.action.strip() or str(shot.get('action') or '').strip()):
            raise HTTPException(409, '自动补写仅用于尚无动态表达的镜头，不会覆盖已有动态表达')
        if data.basis == 'action' and not data.action.strip():
            raise HTTPException(400, '请先填写本镜动态表达')
        image_path = None
        if shot.get('image_status') == 'completed' and shot.get('image'):
            image_path = _storyboard_image_path(path, shot)
            _normalize_rgb_image(image_path)
        legacy_edit = (shot.get('image_task') or {}).get('action') in {'redraw', 'upload'}
        force_vision = data.basis == 'image' and image_path is not None and (
            shot.get('image_origin') in {'reference_redraw', 'upload'} or
            not shot.get('image_origin') and legacy_edit)
        cached_analysis = shot.get('image_analysis') if force_vision else None
        if not isinstance(cached_analysis, dict) or not image_path or cached_analysis.get('fingerprint') != hashlib.sha256(image_path.read_bytes()).hexdigest():
            cached_analysis = None
        if data.basis == 'image' and not force_vision and not data.image_prompt.strip():
            raise HTTPException(400, '请先填写核心分镜图提示词')
        snapshot = copy.deepcopy(shot)
        edit_key = image_edit_key(record['id'], shot_id)
        IMAGE_EDITS.add(edit_key)
    try:
        from .video_prompt_refresh import refresh
        context = copy.deepcopy(record.get('context') or {})
        context.setdefault('video_direction', {})['dynamic_text_mode'] = normalize_text_mode(
            planning_parameters(record).get('dynamic_text_mode'))
        updated, analysis = refresh(context, record['settings'].get('style', ''),
                                    snapshot, record.get('references', []), basis=data.basis,
                                    action=data.action, image_prompt=data.image_prompt,
                                    image_path=image_path, force_vision=force_vision,
                                    image_analysis=cached_analysis, **({'action_only': True} if data.action_only else {}))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        with LOCK:
            IMAGE_EDITS.discard(edit_key)
    with LOCK:
        current = read(path)
        if current['revision'] != data.revision:
            raise HTTPException(409, '项目已被其他操作修改，请重新读取后再试')
        target = _find_shot(current, shot_id)
        before_action = str(target.get('action') or '')
        before_video_prompt = str(target.get('video_prompt') or '')
        target.update(action=updated['action'], motion_plan=updated['motion_plan'])
        if not data.action_only:
            target.update(video_prompt=updated['video_prompt'],
                          video_prompt_warnings=updated.get('video_prompt_warnings', []))
        if isinstance(updated.get('semantic'), dict):
            target['semantic'] = copy.deepcopy(updated['semantic'])
        target['attribution_correction'] = ''
        if data.action_only:
            note = '已依据本镜核心画面自动补写动态表达；图片、图像及视频提示词和其他镜头均未修改。'
        elif data.basis == 'action':
            target.update(image_prompt=updated['image_prompt'],
                          image_prompt_warnings=updated.get('image_prompt_warnings', []),
                          image_prompt_out_of_sync=bool(target.get('image_status') == 'completed'))
            note = '已按用户动态表达更新本镜核心图提示词和视频提示词；其他镜头未修改。'
        else:
            target['image_prompt'] = data.image_prompt.strip()
            target['image_prompt_out_of_sync'] = bool(not analysis and target.get('image_status') == 'completed'
                and target['image_prompt'] != target.get('image_applied_prompt', snapshot.get('image_prompt', '')))
            note = ('已反推当前最终图片并更新本镜视频提示词；其他镜头未修改。' if analysis else
                    '已按当前核心图提示词更新本镜视频提示词；其他镜头未修改。')
        if analysis:
            target['image_analysis'] = analysis
        if target.get('action', '') != before_action or target.get('video_prompt', '') != before_video_prompt:
            _invalidate_shot_video(current, target, '本镜动态方案已更新')
        target['prompt_refresh_note'] = note
        current['logs'].append(f'{shot_id}：{note}')
        current['revision'] += 1
        save(path, current)
        return current


@router.post('/{identity}/shots/{shot_id}/reopen')
def reopen_shot_for_editing(identity: str, shot_id: str, data: Review, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] not in VIDEO_STAGE_STATUSES:
            raise HTTPException(409, '当前任务已在分镜编辑阶段')
        shot = _find_shot(record, shot_id)
        if shot.get('asset_kind') == 'scene_reference':
            raise HTTPException(400, '场景参考不属于时间轴镜头')
        record.update(status='image_review', reedit_shot_id=shot_id,
                      reedit_return_status=record['status'],
                      revision=record['revision'] + 1, error='')
        record['logs'].append(f'{shot_id}：返回分镜编辑。未修改前保留现有核心图和动态片段。')
        save(path, record)
        return record


@router.post('/{identity}/images/confirm')
def confirm_storyboard_images(identity: str, data: StoryboardConfirmation, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        editable(record, data.revision)
        if record['status'] != 'image_review':
            raise HTTPException(409, '请在核心分镜图检查阶段确认图片')
        if not record['shots'] or any(shot.get('image_status') != 'completed' for shot in record['shots']):
            raise HTTPException(409, '请先完成并检查全部核心分镜图')
        if any((shot.get('image_task') or {}).get('status') == 'running' for shot in record['shots']):
            raise HTTPException(409, '请等待正在重绘的核心分镜图完成')
        if scene_references.enabled(record) and record.get('scene_references_status') != 'completed':
            raise HTTPException(409, '该任务启用了场景参考，请先完成场景参考准备并检查关联镜头')
        missing = [str(i + 1) for i, shot in enumerate(record['shots'])
                   if shot['kind'] == 'video' and not str(shot.get('video_prompt') or '').strip()]
        if missing:
            raise HTTPException(409, '请先补齐第 ' + '、'.join(missing) + ' 镜的视频提示词，可使用“按核心图更新视频提示词”')
        if data.regenerate_shot_id:
            if data.regenerate_shot_id != record.get('reedit_shot_id'):
                raise HTTPException(409, '只能重新生成当前返修的镜头')
            reroll = _find_shot(record, data.regenerate_shot_id)
            if reroll.get('kind') != 'video' or reroll.get('video_status') != 'completed':
                raise HTTPException(409, '当前镜头没有可重新抽取的已完成动态片段')
            _invalidate_shot_video(record, reroll, '用户要求在素材与提示词不变的情况下重新生成本镜动态片段')
        return_status = record.pop('reedit_return_status', '')
        record.pop('reedit_shot_id', None)
        all_ready = all(shot.get('kind') != 'video' or shot.get('video_status') == 'completed'
                        for shot in record['shots'])
        if return_status == 'completed' and record.get('export') and all_ready:
            next_status = 'completed'
            note = '本次返回编辑未改变需重生成的资产；继续使用原动态片段和成片。'
        elif return_status == 'video_review' and all_ready:
            next_status = 'video_review'
            note = '核心分镜图已确认；现有动态片段仍可继续检查。'
        else:
            next_status = 'video_generation_ready'
            note = '核心分镜图已确认；可以开始生成动态镜头。'
        record.update(status=next_status, revision=record['revision'] + 1, error='')
        record['logs'].append(note)
        save(path, record)
        return record

@router.post('/{identity}/plan')
def plan(identity: str, request: Request, repair_only: bool = False):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        if record['status'] in VIDEO_STAGE_STATUSES:
            raise HTTPException(409, '核心图已经确认，请在动态镜头阶段继续；此处不再重写已确认的分镜')
        if record['status'] in {'planning', 'stopping'}:
            raise HTTPException(409, '正在规划')
        if project_has_image_edits(record['id']):
            raise HTTPException(409, '请等待当前图片重绘完成后再重新规划')
        if ACTIVE:
            raise HTTPException(409, '已有视频草案正在规划，请稍后重试')
        # Older drafts imported SRT but discarded the confirmed narration groups.
        # Backfill only text-verified hints; never replace their audio/timeline.
        if 'narration_groups' not in record and record.get('source_project'):
            try:
                _, source = source_project(require_user(request)['id'], record['source_project']['id'],
                                           audio_task=bool(record.get('creation_parameters', {}).get('dynamic_video')))
                record['narration_groups'] = narration_groups(source, record['scenes'])
            except HTTPException:
                record['narration_groups'] = []
        if repair_only:
            affected = [row for row in record['shots'] if row.get('design_needs_review') or
                        not row.get('image_prompt') or (row['kind'] == 'video' and not row.get('video_prompt'))]
            if not affected:
                raise HTTPException(409, '没有需要更新设计的镜头')
            for row in affected:
                row['design_needs_review'] = True
            lock_manual_groups(record)
        fixed_shots = copy.deepcopy(record['shots']) if record.get('manual_groups') else None
        if fixed_shots and any(row['kind'] == 'video' and row['duration'] > 15 for row in fixed_shots):
            raise HTTPException(409, '手动动态镜头超过15秒，请调整边界或改为静态后再更新设计')
        ACTIVE.add(str(path))
        cancelled = threading.Event()
        CANCEL_EVENTS[str(path)] = cancelled
        resume_state = copy.deepcopy(resumable_planning_state(record))
        if not resume_state:
            discard_planning_resume(record)
            # A fresh fixed-group redesign can reuse the existing global reading.
            if fixed_shots and isinstance(record.get('context'), dict):
                settings = record['settings']
                resume_state = {'fingerprint': planning_fingerprint(record['scenes'], settings['style'],
                    settings['characters'], settings['world'], record['references'], planning_parameters(record)),
                    'context': copy.deepcopy(record['context'])}
        record.update(status='planning', error='',
                      logs=record.get('logs', []) if resume_state else [], revision=record['revision']+1)
        record.pop('planning_recovery', None)
        save(path, record)
    def progress(message):
        with LOCK:
            if cancelled.is_set():
                raise PlanningStopped()
            record['logs'].append(message)
            save(path, record)
    def checkpoint(context, shots, stage):
        with LOCK:
            record['planning_checkpoint'] = dict(context=context, shots=shots, stage=stage)
            save(path, record)
            if cancelled.is_set():
                raise PlanningStopped()
    def save_state(state):
        with LOCK:
            record['planning_state'] = state
            record['planning_resume_available'] = True
            if state.get('shots'):
                record['planning_checkpoint'] = dict(context=state.get('context', {}),
                    shots=state['shots'], stage=state.get('stage', '分镜草案'))
            save(path, record)
            if cancelled.is_set():
                raise PlanningStopped()
    def worker():
        try:
            settings = record['settings']
            context, shots = plan_storyboard(record['scenes'], settings['style'], settings['characters'],
                                             settings['world'], record['references'], progress,
                                             planning_parameters(record), checkpoint=checkpoint,
                                             resume_state=resume_state, save_state=save_state,
                                             fixed_shots=fixed_shots, repair_only=repair_only)
            with LOCK:
                if cancelled.is_set():
                    raise PlanningStopped()
                record.update(context=context, shots=shots, status='storyboard_review')
                discard_planning_resume(record)
        except PlanningStopped:
            with LOCK:
                recover_planning(record)
                record['error'] = ''
                record['logs'].append('规划已停止，已保存可用分镜草案。')
        except Exception as exc:
            with LOCK:
                recover_planning(record)
                record['error'] = '' if cancelled.is_set() else str(exc)
                if cancelled.is_set():
                    record['logs'].append('规划已停止。')
        finally:
            with LOCK:
                record['revision'] += 1
                try:
                    save(path, record)
                finally:
                    ACTIVE.discard(str(path))
                    CANCEL_EVENTS.pop(str(path), None)
    threading.Thread(target=worker, daemon=True).start()
    return record


@router.post('/{identity}/stop')
def stop(identity: str, request: Request):
    with LOCK:
        path = directory(require_user(request)['id'], identity)
        record = read(path)
        event = CANCEL_EVENTS.get(str(path))
        if event is not None and record['status'] in {'planning', 'stopping'}:
            event.set()
            record['status'] = 'stopping'
            record['logs'].append('已请求停止；正在等待已发出的 API 请求返回，不再执行后续 Agent。')
            save(path, record)
        return record
