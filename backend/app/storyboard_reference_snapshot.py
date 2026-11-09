"""Immutable, request-ordered references for the image that actually completed.

No provider calls and no project-record mutation happen here. Call capture before
dispatch, send its frozen paths, and bind make_snapshot only after image commit.
"""
import copy
import hashlib
import os
import re
import uuid
from pathlib import Path


def _inside(root, value):
    value = Path(value)
    candidate = (value if value.is_absolute() else root / value).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError('参考快照路径超出当前项目，未提交图片请求')
    return candidate


def _bindings(root, record, shot, selection_ids, use_current_image):
    materials = [*record.get('references', []), *record.get('redraw_references', [])]
    scenes = list(record.get('scene_assets', []))
    catalog = {str(row.get('id')): ('reference', row) for row in materials}
    catalog.update({str(row.get('id')): ('scene_reference', row) for row in scenes})
    preferred = []
    fallback = []

    def add(target, kind, row):
        value = row.get('file') if kind == 'reference' else row.get('image')
        if value:
            try:
                target.append((kind, str(row.get('id') or ''), _inside(root, value)))
            except ValueError:
                pass  # Unselected stale/outside records never become inputs.

    if use_current_image:
        add(preferred, 'current_image', shot)
    for identity in (selection_ids if selection_ids is not None else shot.get('reference_ids', shot.get('reference_image_ids', []))):
        entry = catalog.get(str(identity))
        if entry:
            add(preferred, *entry)
    for row in scenes:
        add(fallback, 'scene_reference', row)
    add(fallback, 'current_image', shot)
    shots = record.get('shots', [])
    index = next((i for i, row in enumerate(shots) if row.get('id') == shot.get('id')), None)
    if index is not None and index > 0:
        add(fallback, 'previous_shot', shots[index - 1])
    for row in materials:
        add(fallback, 'reference', row)
    return preferred, fallback


def _extension(data, source):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    suffix = source.suffix.lower()
    return '.jpg' if suffix == '.jpeg' else suffix if suffix in {'.png', '.jpg', '.webp'} else '.bin'


def _freeze(root, source):
    if not source.is_file():
        raise ValueError('本次参考图片已不存在，未提交图片请求')
    data = source.read_bytes()
    if not data:
        raise ValueError('本次参考图片为空，未提交图片请求')
    digest = hashlib.sha256(data).hexdigest()
    folder = _inside(root, 'assets/storyboard_reference_snapshots')
    folder.mkdir(parents=True, exist_ok=True)
    destination = _inside(root, folder / (digest + _extension(data, source)))
    if not destination.exists():
        temporary = folder / ('.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as handle:
                handle.write(data)
            try:
                # Windows rename is atomic and refuses to replace an existing
                # destination; unlike hard links it also works on portable
                # exFAT installs and without the hard-link sandbox privilege.
                # POSIX rename may replace, so use link's exclusive publish.
                if os.name == 'nt':
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise ValueError('参考图片快照校验失败，未提交图片请求')
    # Content-addressed copies are logically immutable. Do not set the OS
    # read-only bit: users must still be able to remove a project normally.
    return destination, digest


def capture_reference_inputs(project, record, shot, reference_paths, *, selection_ids=None, use_current_image=False):
    root = Path(project).resolve()
    preferred, fallback = _bindings(root, record, shot, selection_ids, use_current_image)
    paths, entries = [], []
    for number, value in enumerate(reference_paths, 1):
        source = _inside(root, value)
        match = next((i for i, entry in enumerate(preferred) if entry[2] == source), None)
        identity = preferred.pop(match) if match is not None else next((entry for entry in fallback if entry[2] == source), None)
        if identity is None or not identity[1]:
            raise ValueError(f'第 {number} 张参考图片无法定位素材身份，未提交图片请求')
        frozen, digest = _freeze(root, source)
        paths.append(str(frozen))
        entries.append({'kind': identity[0], 'id': identity[1], 'file': frozen.relative_to(root).as_posix(),
                        'sha256': digest, 'local_image_number': number})
    return paths, entries


def make_snapshot(image_sha256, references):
    if not isinstance(image_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', image_sha256):
        raise ValueError('已完成图片缺少有效哈希，不能绑定参考快照')
    return {'version': 1, 'image_sha256': image_sha256, 'references': copy.deepcopy(references)}
