"""Read the pinned, offline Shotcraft archive without executing upstream code."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .vibe_shotcraft_capabilities import capabilities_for


DEFAULT_LIBRARY_ROOT = Path(__file__).resolve().parents[2] / 'tools' / 'vibe_shotcraft_library'
_ID = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid Shotcraft identifier')
    return value


def safe_path(root: Path, relative: str) -> Path:
    """Reject absolute/Windows/parent paths, including resolved symlink escapes."""
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('Invalid archive path')
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or '.' in relative.split('/'):
        raise ValueError('Archive path traversal is forbidden')
    target = (root.resolve() / Path(*rel.parts)).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError('Archive path escapes library root')
    return target


def _limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2_000_000:
        raise ValueError('Character limit must be between 1 and 2000000')
    return value


class ShotcraftLibrary:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or DEFAULT_LIBRARY_ROOT).resolve()
        self.manifest = json.loads(safe_path(self.root, 'manifest.json').read_text(encoding='utf-8'))
        if self.manifest.get('schema_version') != 1:
            raise ValueError('Unsupported Shotcraft manifest schema')
        self.styles = self._index(self.manifest.get('styles', []), 'id')
        self.cards = self._index(self.manifest.get('cards', []), 'id')
        self.files = self.manifest.get('files', {})
        for path in self.files:
            safe_path(self.root, path)
        runtime_path = safe_path(self.root, 'runtime_supported.json')
        runtime = json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.is_file() else {}
        if runtime and runtime.get('schema_version') != 1:
            raise ValueError('Unsupported Shotcraft runtime registry schema')
        self.runtime = runtime.get('styles', {})
        for style_id in self.runtime:
            _identifier(style_id)
            if style_id not in self.styles:
                raise ValueError(f'Runtime registry names unknown style: {style_id}')

    @staticmethod
    def _index(items: list[dict], key: str) -> dict[str, dict]:
        result = {}
        for item in items:
            identity = _identifier(item[key])
            if identity in result:
                raise ValueError(f'Duplicate Shotcraft identifier: {identity}')
            result[identity] = item
        return result

    def _style(self, style_id: str) -> dict:
        style_id = _identifier(style_id)
        if style_id not in self.styles:
            raise KeyError(f'Unknown Shotcraft style: {style_id}')
        return self.styles[style_id]

    def _present(self, relative: str | None) -> bool:
        if not relative or relative not in self.files:
            return False
        path = safe_path(self.root, relative)
        expected = self.files[relative]
        return path.is_file() and path.stat().st_size == expected['bytes']

    def _readiness(self, style: dict) -> dict:
        runtime = self.runtime.get(style['id'], {})
        required = [style['recipe_path'], *style['demo_paths'], *style.get('dependency_paths', [])]
        source_present = all(self._present(path) for path in required)
        supported = runtime.get('status') == 'supported'
        return {
            'status': 'supported' if supported else 'archived_not_ready',
            'runtime_supported': supported,
            'source_present': source_present,
            'preview_present': self._present(style.get('preview_path')),
            'ready': supported and source_present,
            'renderer_id': runtime.get('renderer_id') if supported else None,
            'render_validation': runtime.get('render_validation', 'not_claimed'),
        }

    def status(self, verify_hashes: bool = False) -> dict:
        missing, damaged = [], []
        for relative, expected in self.files.items():
            path = safe_path(self.root, relative)
            if not path.is_file():
                missing.append(relative)
            elif path.stat().st_size != expected['bytes']:
                damaged.append(relative)
            elif verify_hashes and hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
                damaged.append(relative)
        ready = [key for key, style in self.styles.items() if self._readiness(style)['ready']]
        previews = sum(self._present(s.get('preview_path')) for s in self.styles.values())
        return {
            'available': True, 'source_commit': self.manifest['upstream']['commit'],
            'card_count': len(self.cards), 'style_count': len(self.styles),
            'category_count': len(self.manifest['categories']), 'preview_count': previews,
            'runtime_ready_count': len(ready), 'runtime_ready_styles': ready,
            'archive_complete': not missing and not damaged and previews == len(self.styles),
            'hashes_verified': verify_hashes, 'missing_files': missing, 'damaged_files': damaged,
            'archive_bytes': sum(f['bytes'] for f in self.files.values()),
            'all_styles_rendered': False,
        }

    def compact_catalog(self, ready_only: bool = False, category: str | None = None,
                        max_chars: int = 30000) -> dict:
        """A bounded, machine-readable catalog; omitted entries are never hidden."""
        _limit(max_chars)
        if category is not None and category not in self.manifest['categories']:
            raise KeyError(f'Unknown Shotcraft category: {category}')
        items = []
        for style in self.styles.values():
            if category is not None and style['category'] != category:
                continue
            # Most archived styles have no adapter. Filter the cheap registry
            # first so a ready-only query probes only supported styles' files.
            if ready_only and self.runtime.get(style['id'], {}).get('status') != 'supported':
                continue
            readiness = self._readiness(style)
            if ready_only and not readiness['ready']:
                continue
            items.append({
                'id': style['id'], 'card_id': style['card_id'], 'category': style['category'],
                'label': style['label'], 'description': style['description'],
                'use': style.get('use', ''), 'demo_paths': style['demo_paths'],
                'runtime': readiness['status'], 'ready': readiness['ready'],
            })
            capabilities = capabilities_for(style['id'])
            if capabilities is not None:
                items[-1]['capabilities'] = capabilities
        result = {'source_commit': self.manifest['upstream']['commit'], 'ready_only': ready_only,
                  'total_styles': len(items), 'returned_styles': 0, 'omitted_styles': len(items),
                  'complete': False, 'items': []}
        if len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) > max_chars:
            raise ValueError('Character limit is too small for catalog metadata')
        for item in items:
            candidate = copy.deepcopy(result)
            candidate['items'].append(item)
            candidate['returned_styles'] += 1
            candidate['omitted_styles'] -= 1
            candidate['complete'] = candidate['omitted_styles'] == 0
            if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))) > max_chars:
                break
            result = candidate
        if not items:
            result['complete'] = True
        return result

    def get_style(self, style_id: str) -> dict:
        style = copy.deepcopy(self._style(style_id))
        style['runtime_readiness'] = self._readiness(style)
        capabilities = capabilities_for(style_id)
        if capabilities is not None:
            style['capabilities'] = capabilities
        return style

    def get_card(self, card_id: str) -> dict:
        card_id = _identifier(card_id)
        if card_id not in self.cards:
            raise KeyError(f'Unknown Shotcraft card: {card_id}')
        return copy.deepcopy(self.cards[card_id])

    def _read_text(self, relative: str, max_chars: int) -> dict:
        _limit(max_chars)
        if relative not in self.files:
            raise ValueError('File is not in the pinned archive manifest')
        path = safe_path(self.root, relative)
        if not path.is_file():
            raise FileNotFoundError(f'Shotcraft archive file missing: {relative}')
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        expected = self.files[relative]
        if digest != expected['sha256'] or len(data) != expected['bytes']:
            raise ValueError(f'Shotcraft archive integrity mismatch: {relative}')
        text = data.decode('utf-8')
        return {'path': relative, 'sha256': digest, 'text': text[:max_chars],
                'total_chars': len(text), 'returned_chars': min(len(text), max_chars),
                'truncated': len(text) > max_chars, 'complete': len(text) <= max_chars}

    def read_recipe(self, style_id: str, max_chars: int = 20000) -> dict:
        style = self._style(style_id)
        return {'style_id': style_id, 'card_id': style['card_id'],
                **self._read_text(style['recipe_path'], max_chars)}

    def read_sources(self, style_id: str, max_chars_per_file: int = 20000,
                     include_dependencies: bool = False) -> dict:
        style = self._style(style_id)
        paths = list(style['demo_paths'])
        if include_dependencies:
            paths.extend(style.get('dependency_paths', []))
        files = [self._read_text(path, max_chars_per_file) for path in dict.fromkeys(paths)]
        return {'style_id': style_id, 'files': files, 'complete': all(f['complete'] for f in files),
                'dependencies_included': include_dependencies,
                'dependency_paths': copy.deepcopy(style.get('dependency_paths', [])),
                'runtime_readiness': self._readiness(style)}

    def read_source(self, style_id: str, source_path: str, max_chars: int = 20000) -> dict:
        style = self._style(style_id)
        safe_path(self.root, source_path)
        if source_path not in [*style['demo_paths'], *style.get('dependency_paths', [])]:
            raise ValueError('Source path does not belong to this Shotcraft style')
        return self._read_text(source_path, max_chars)


def status(root: Path | str | None = None, verify_hashes: bool = False) -> dict:
    try:
        return ShotcraftLibrary(root).status(verify_hashes)
    except FileNotFoundError:
        return {'available': False, 'archive_complete': False, 'runtime_ready_count': 0,
                'all_styles_rendered': False, 'reason': 'Shotcraft archive is not installed'}


def compact_catalog(ready_only: bool = False, *, root: Path | str | None = None,
                    category: str | None = None, max_chars: int = 30000) -> dict:
    return ShotcraftLibrary(root).compact_catalog(ready_only, category, max_chars)


def read_recipe(style_id: str, *, root: Path | str | None = None, max_chars: int = 20000) -> dict:
    return ShotcraftLibrary(root).read_recipe(style_id, max_chars)


def read_sources(style_id: str, *, root: Path | str | None = None,
                 max_chars_per_file: int = 20000, include_dependencies: bool = False) -> dict:
    return ShotcraftLibrary(root).read_sources(style_id, max_chars_per_file, include_dependencies)
