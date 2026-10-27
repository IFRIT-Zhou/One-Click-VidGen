"""Read-only shot context and immutable, project-scoped local repair uploads."""
from __future__ import annotations

import io
import hashlib
import re
import shutil
import uuid
import warnings
from pathlib import Path

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from . import codex_bridge as bridge

MAX_UPLOAD = 30 * 1024 * 1024
FORMATS = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}


def image_metadata(path):
    size = path.stat().st_size if isinstance(path, Path) else len(path.getvalue())
    sha256 = bridge.file_hash(path) if isinstance(path, Path) else hashlib.sha256(path.getvalue()).hexdigest()
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format not in FORMATS or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('仅支持单帧 PNG、JPEG 或 WebP 图片')
            width, height, format = image.width, image.height, image.format
            image.load()
    return {'width': width, 'height': height, 'format': format, 'bytes': size, 'sha256': sha256}


def staged_image(path, upload_id):
    # Content-addressed and scoped by authenticated project; never accept paths/URLs.
    if not re.fullmatch(r'img_[a-f0-9]{64}', upload_id):
        raise ValueError('无效的桥接图片 ID')
    folder = path / 'assets' / 'bridge_uploads'
    candidates = [folder / (upload_id + suffix) for suffix in FORMATS.values()]
    target = next((p for p in candidates if p.is_file()), None)
    if target is None or path.resolve() not in target.resolve().parents:
        raise ValueError('图片未上传到当前项目，请用 image-upload 上传')
    try:
        metadata = image_metadata(target)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('暂存图片已损坏，请重新上传') from exc
    if metadata['sha256'] != upload_id[4:]:
        raise ValueError('暂存图片内容已改变，请重新上传')
    return target, metadata


def shot_by_id(record, shot_id):
    row = next((s for s in record.get('shots', []) if s['id'] == shot_id), None)
    if row is None:
        raise HTTPException(404, '分镜不存在')
    return row


def current_image(record, path, shot):
    value = shot.get('image')
    if not value:
        return None
    target = (path / value).resolve()
    if path.resolve() not in target.parents or not target.is_file():
        return {'available': False}
    try:
        metadata = image_metadata(target)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return {'available': False, 'error': '当前图片不能解码'}
    return {'available': True, **metadata, 'version': shot.get('image_version'),
            'url': f"/api/codex-bridge/projects/{record['id']}/shots/{shot['id']}/image?v={metadata['sha256']}"}


def reference_preview(path, record, shot, *, use_current_image=None, use_scene_reference=None):
    from .codex_image_generation import reference_inputs
    try:
        prompt, refs, asset, ids, mapping = reference_inputs(
            path, record, shot, use_current_image=use_current_image, use_scene_reference=use_scene_reference)
    except HTTPException as exc:
        return {'ok': False, 'error': exc.detail, 'generation_started': False}
    numbers = sorted({int(n) for n in re.findall(r'图\s*(\d+)(?!\d)', prompt)})
    missing = [n for n in numbers if n < 1 or n > len(refs)]
    return {'ok': not missing, 'shot_id': shot['id'], 'reference_ids': ids,
            'actual_reference_count': len(refs), 'references': mapping, 'effective_prompt': prompt,
            'prompt_image_numbers': numbers, 'unmapped_image_numbers': missing,
            'semantic_review_required': True, 'generation_started': False, 'may_charge': False,
            'warnings': ['图号存在不代表人物身份或语义对应正确，仍需查看图片核对。']}


def single_pack(record, path, shot):
    selected = set(shot.get('reference_ids') or []) | set((shot.get('redraw_selection') or {}).get('reference_ids') or [])
    keys = ('id', 'kind', 'slide_ids', 'start', 'end', 'duration', 'intent', 'image_prompt', 'motion_plan',
            'video_prompt', 'image_status', 'video_status', 'image_version', 'image_material_numbers_bound',
            'image_prompt_out_of_sync', 'reference_ids', 'redraw_selection', 'image_prompt_reference_order',
            'image_reference_context', 'scene_reference_id', 'scene_reference_disabled')
    return {'schema_version': 2, 'project_id': record['id'], 'name': record.get('settings', {}).get('name', ''),
            'revision': record['revision'], 'timeline_token': bridge.timeline_token(record, path),
            'status': record['status'], 'shot_number': next(i + 1 for i, s in enumerate(record['shots']) if s['id'] == shot['id']),
            'shot': {k: shot.get(k) for k in keys if k in shot}, 'current_image': current_image(record, path, shot),
            'subtitles': [{k: s.get(k) for k in ('slide_id', 'start', 'end', 'text')}
                          for s in record.get('scenes', []) if s['slide_id'] in shot.get('slide_ids', [])],
            'settings': {k: record.get('settings', {}).get(k) for k in ('style', 'characters', 'world', 'ratio')},
            'references': [{k: r.get(k, '') for k in ('id', 'label', 'name', 'description', 'kind', 'source')} |
                           {'url': f"/api/codex-bridge/projects/{record['id']}/references/{r['id']}"}
                           for r in bridge.studio._material_references(record) if r['id'] in selected],
            'reference_preview': reference_preview(path, record, shot),
            'evidence': record.get('codex_bridge', {}).get('evidence', {}).get(shot['id'], '')}


def attach_staged_references(path, updated, patches):
    known = {r['id'] for r in bridge.studio._material_references(updated)}
    for patch in patches:
        for rid in patch.reference_ids or []:
            if rid in known:
                continue
            target, metadata = staged_image(path, rid)
            updated.setdefault('redraw_references', []).append({
                'id': rid, 'file': target.relative_to(path).as_posix(), 'name': '桥接上传参考图', **metadata})
            known.add(rid)


def use_replacement(path, row, upload_id):
    target, metadata = staged_image(path, upload_id)
    row.update(image=target.relative_to(path).as_posix(), image_version=metadata['sha256'],
               image_status='completed', image_error='', image_origin='upload',
               image_applied_prompt=row.get('image_prompt', ''), image_prompt_out_of_sync=False,
               scene_reference_used_version='', bridge_image_upload_id=upload_id,
               image_task={'status': 'completed', 'action': 'upload', 'message': '制作桥已替换图片，待视觉核查'})


def materialize_replacement(path, row):
    # Native redraw may overwrite the current-image path. Never point it at the
    # content-addressed upload: that file can also be another shot's reference.
    source, metadata = staged_image(path, row['bridge_image_upload_id'])
    target = path / 'assets' / 'bridge_replacements' / (uuid.uuid4().hex + source.suffix)
    if path.resolve() not in target.resolve().parents:
        raise ValueError('替换图片目录不在当前项目内')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if bridge.file_hash(target) != metadata['sha256']:
        raise ValueError('替换图片复制校验失败')
    row['image'] = target.relative_to(path).as_posix()


def verification(path, before, after, ids, audio_hash):
    untouched_before = [s for s in before['shots'] if s['id'] not in ids]
    untouched_after = [s for s in after['shots'] if s['id'] not in ids]
    checks = {
        'audio_unchanged': before.get('audio') == after.get('audio') and bridge.file_hash(path / after['audio']) == audio_hash,
        'subtitles_unchanged': before.get('scenes') == after.get('scenes'),
        'narration_groups_unchanged': before.get('narration_groups') == after.get('narration_groups'),
        'other_shots_unchanged': untouched_before == untouched_after,
        'timing_unchanged': [{k: s.get(k) for k in ('id', 'slide_ids', 'start', 'end', 'duration', 'kind')} for s in before['shots']]
            == [{k: s.get(k) for k in ('id', 'slide_ids', 'start', 'end', 'duration', 'kind')} for s in after['shots']],
    }
    if not all(checks.values()):
        raise ValueError('局部写入保护核查未通过')
    shots = []
    for shot in after['shots']:
        if shot['id'] not in ids:
            continue
        preview = reference_preview(path, after, shot)
        preview.pop('effective_prompt', None)  # Receipt maps IDs/numbers, not another full prompt dump.
        shots.append({'id': shot['id'], 'image': current_image(after, path, shot),
                      'reference_preview': preview, 'image_prompt_out_of_sync': bool(shot.get('image_prompt_out_of_sync'))})
    return {**checks, 'audio_sha256': audio_hash, 'other_shots_checked': len(untouched_before),
            'visual_review_required': True, 'shots': shots}


def register(router):
    @router.get('/projects/{identity}/shots/{shot_id}')
    def shot_pack(identity: str, shot_id: str, request: Request):
        user = bridge.user_for(request)
        with bridge.studio.LOCK:
            path = bridge.studio.directory(user['id'], identity)
            record = bridge.read_record(path)
            return single_pack(record, path, shot_by_id(record, shot_id))

    @router.get('/projects/{identity}/shots/{shot_id}/image')
    def shot_image(identity: str, shot_id: str, request: Request, v: str | None = None):
        user = bridge.user_for(request)
        with bridge.studio.LOCK:
            path = bridge.studio.directory(user['id'], identity)
            shot = shot_by_id(bridge.read_record(path), shot_id)
            target = bridge.studio._storyboard_image_path(path, shot)
            if v is not None and bridge.file_hash(target) != v:
                raise HTTPException(409, '当前图片版本已改变，请重新读取单镜资料包')
            return FileResponse(target, headers={'Cache-Control': 'no-cache'})

    @router.get('/projects/{identity}/shots/{shot_id}/references-preview')
    def preview(identity: str, shot_id: str, request: Request,
                use_current_image: bool | None = None, use_scene_reference: bool | None = None):
        user = bridge.user_for(request)
        with bridge.studio.LOCK:
            path = bridge.studio.directory(user['id'], identity)
            record = bridge.read_record(path)
            return {'project_id': identity, 'revision': record['revision'],
                    'timeline_token': bridge.timeline_token(record, path),
                    **reference_preview(path, record, shot_by_id(record, shot_id),
                        use_current_image=use_current_image, use_scene_reference=use_scene_reference)}

    @router.post('/projects/{identity}/uploads')
    async def upload(identity: str, request: Request, file: UploadFile = File(...)):
        user = bridge.user_for(request)
        path = bridge.studio.directory(user['id'], identity)
        bridge.read_record(path)  # Authorize ownership before reading the body.
        content = await file.read(MAX_UPLOAD + 1)
        if not content or len(content) > MAX_UPLOAD:
            raise HTTPException(400, '图片须为 1 字节到 30 MB')
        try:
            metadata = image_metadata(io.BytesIO(content))
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(400, '上传文件不是有效的单帧 PNG、JPEG 或 WebP 图片') from exc
        upload_id = 'img_' + metadata['sha256']
        with bridge.studio.LOCK:
            record = bridge.read_record(path)
            target = path / 'assets' / 'bridge_uploads' / (upload_id + FORMATS[metadata['format']])
            if path.resolve() not in target.resolve().parents:
                raise HTTPException(400, '图片目录不在当前项目内')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_name('.' + uuid.uuid4().hex + '.tmp')
                try:
                    temporary.write_bytes(content)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            staged_image(path, upload_id)
            return {'ok': True, 'project_id': identity, 'upload_id': upload_id, 'reference_id': upload_id,
                    'image': metadata, 'url': f'/api/codex-bridge/projects/{identity}/uploads/{upload_id}',
                    'revision': record['revision'], 'project_modified': False, 'generation_started': False}

    @router.get('/projects/{identity}/uploads/{upload_id}')
    def uploaded_image(identity: str, upload_id: str, request: Request):
        user = bridge.user_for(request)
        path = bridge.studio.directory(user['id'], identity)
        bridge.read_record(path)
        try:
            target, _ = staged_image(path, upload_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(target)
