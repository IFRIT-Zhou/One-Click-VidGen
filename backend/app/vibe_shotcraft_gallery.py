"""Read-only bundled motion examples; never renders or contacts a provider."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .auth import require_user
from .vibe_shotcraft_capabilities import VERSION, capabilities_for, selection_taxonomy
from .vibe_shotcraft_library import ShotcraftLibrary
from .vibe_shotcraft_plan import RECIPE_RULES

router = APIRouter(prefix='/api/vibe-templates')
GALLERY_ROOT = Path(__file__).resolve().parents[2] / 'tools/vibe_remotion/gallery'
_MEDIA = {'preview': ('preview', '.gif', 'image/gif'), 'poster': ('poster', '.jpg', 'image/jpeg')}


def _manifest():
    path = GALLERY_ROOT / 'manifest.json'
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('Gallery manifest too large')
        raw = path.read_bytes()
        data = json.loads(raw)
        if data.get('schema_version') != 1 or not isinstance(data.get('styles'), dict):
            raise ValueError('Invalid gallery manifest')
        return data, hashlib.sha256(raw).hexdigest()[:16]
    except (OSError, ValueError, TypeError):
        return {'version': 'unavailable', 'styles': {}}, 'unavailable'


def _media_path(manifest, identity, asset):
    if identity not in RECIPE_RULES or asset not in _MEDIA:
        return None
    row = manifest['styles'].get(identity)
    if not isinstance(row, dict):
        return None
    field, suffix, _ = _MEDIA[asset]
    # Only the exact bundled filename is addressable, not arbitrary manifest
    # paths, project files, local downloads, remote URLs or symlink escapes.
    expected = identity + suffix
    if row.get(field) != expected:
        return None
    root = GALLERY_ROOT.resolve()
    try:
        target = (root / expected).resolve()
        if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > 8 * 1024 * 1024:
            return None
        return target
    except OSError:
        return None


@router.get('')
def catalog(request: Request):
    require_user(request)
    manifest, version_key = _manifest()
    readiness = {}
    runtime_reason = ''
    try:
        library = ShotcraftLibrary()
        for identity in RECIPE_RULES:
            readiness[identity] = library.get_style(identity)['runtime_readiness']['ready']
    except (OSError, ValueError, KeyError, TypeError):
        runtime_reason = '本地模板库未安装完整，请更新或安装后再生成；查看示意不调用模型'
    items = []
    for identity, rule in RECIPE_RULES.items():
        preview = _media_path(manifest, identity, 'preview')
        poster = _media_path(manifest, identity, 'poster')
        available = readiness.get(identity, False)
        items.append({
            'id': identity, 'name': rule.get('label', identity),
            'capabilities': capabilities_for(identity), 'available': available,
            'preview_available': preview is not None,
            'preview_url': f'/api/vibe-templates/{identity}/preview?v={version_key}' if preview else None,
            'poster_url': f'/api/vibe-templates/{identity}/poster?v={version_key}' if poster else None,
            'reason': '' if available else runtime_reason or '本地模板源文件尚未就绪',
            'preview_reason': '' if preview else '动态示意尚未安装，可更新补齐',
        })
    return {'version': manifest.get('version', 'unavailable'), 'capability_version': VERSION,
            'tag_labels': selection_taxonomy()['tag_labels'], 'max_templates': 3,
            'demo_note': '医院案例动效示意；正式生成仍使用本镜的文案、素材和原图配色。',
            'items': items, 'may_charge': False}


@router.get('/{identity}/{asset}')
def media(identity: str, asset: str, request: Request):
    require_user(request)
    manifest, _ = _manifest()
    path = _media_path(manifest, identity, asset)
    if path is None:
        raise HTTPException(404, '模板示意不存在或尚未安装；不会临时调用模型生成')
    return FileResponse(path, media_type=_MEDIA[asset][2], headers={'Cache-Control': 'private, max-age=86400'})
