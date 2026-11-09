#!/usr/bin/env python3
"""Pin, inventory and classify the official Shotcraft source and Gallery media.

Only standard-library code runs. No upstream package installation or rendering occurs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile


REPO = 'Vincentwei1021/video-shotcraft'
PINNED_COMMIT = '1266a3ec4831289c4d893d42dd5102723658039a'
RELEASE_API = f'https://api.github.com/repos/{REPO}/releases/tags/gallery-media'
DEFAULT_DEST = Path(__file__).resolve().parent / 'vibe_shotcraft_library'
ID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def safe_path(root: Path, relative: str) -> Path:
    if not relative or '\\' in relative or ':' in relative:
        raise ValueError(f'Unsafe relative path: {relative!r}')
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or '.' in relative.split('/'):
        raise ValueError(f'Unsafe relative path: {relative!r}')
    target = (root.resolve() / Path(*rel.parts)).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError(f'Path escapes destination: {relative!r}')
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict:
    return {'bytes': path.stat().st_size, 'sha256': sha256(path)}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def git(source: Path, *args: str) -> bytes:
    return subprocess.run(['git', '-C', str(source), *args], check=True,
                          capture_output=True).stdout


def archive_source(source: Path, dest: Path, expected_commit: str) -> tuple[str, dict]:
    commit = git(source, 'rev-parse', 'HEAD').decode().strip()
    if commit != expected_commit:
        raise ValueError(f'Source commit {commit} differs from pinned commit {expected_commit}')
    if git(source, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Tracked upstream source is modified; refuse to attribute changes to the pinned commit')
    files = {}
    for raw in git(source, 'ls-files', '-z').split(b'\0'):
        if not raw:
            continue
        relative = raw.decode('utf-8')
        src = safe_path(source, relative)
        if src.is_symlink() or not src.is_file():
            raise ValueError(f'Unsupported upstream tracked file: {relative}')
        target = safe_path(dest, f'source/{relative}')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or sha256(target) != sha256(src):
            shutil.copyfile(src, target)
        files[f'source/{relative}'] = file_record(target)
    shutil.copyfile(safe_path(source, 'LICENSE'), safe_path(dest, 'LICENSE'))
    files['LICENSE'] = file_record(dest / 'LICENSE')
    return commit, files


def reference_candidates(source: Path, card: dict) -> tuple[list[str], str]:
    recipe = safe_path(source, card['source']).read_text(encoding='utf-8')
    if '## 参考实现' not in recipe:
        raise ValueError(f"Card has no reference implementation: {card['name']}")
    section = recipe.split('## 参考实现', 1)[1].split('\n## ', 1)[0]
    paths = re.findall(r'demos/[A-Za-z0-9_./-]+', section)
    filenames = set(re.findall(r'[A-Za-z0-9_-]+\.tsx', section))
    candidates = set()
    for relative in paths:
        path = safe_path(source, relative.rstrip('/'))
        if relative.endswith('.tsx') and path.is_file():
            candidates.add(relative)
        elif path.is_dir():
            for filename in filenames:
                candidate = path / filename
                if candidate.is_file():
                    candidates.add(candidate.relative_to(source).as_posix())
    return sorted(candidates), section


def author_style_mapping(source: Path) -> dict[str, str]:
    result = {}
    for path in sorted((source / 'shot-polish' / 'final-notes').glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('styleKey'):
            key, stem = record['styleKey'], record['stem']
            if key in result and result[key] != stem:
                raise ValueError(f'Conflicting author mapping: {key}')
            result[key] = stem
    return result


def resolve_style_demos(source: Path, card: dict, style: dict,
                        author_map: dict[str, str]) -> tuple[list[str], str]:
    candidates, section = reference_candidates(source, card)
    key = style['key']
    # A is a transition layer in the full template, not B/C's standalone demo.
    if card['name'] == 'shot-transitions' and key == 'flash-cut':
        paths = ['assets/lib/FlashCut.tsx', 'template/src/aifl/Main.tsx']
        if not all(path in section and safe_path(source, path).is_file() for path in paths):
            raise ValueError('flash-cut reference implementation changed; re-review mapping')
        return paths, 'recipe_explicit_A_variant'
    if key in author_map:
        matches = [p for p in candidates if Path(p).stem == author_map[key]]
        if len(matches) == 1:
            return matches, 'author_final_notes_styleKey_and_recipe'
        raise ValueError(f'Author mapping does not match recipe: {key}')
    if len(card['styles']) == 1:
        if not candidates:
            raise ValueError(f'No reference source for {key}')
        return candidates, 'recipe_single_gallery_style'
    # No fuzzy match: a future multi-style card needs authoritative mapping.
    raise ValueError(f'Unresolved multi-style source mapping: {card["name"]} / {key}')


def local_dependencies(source: Path, demos: list[str]) -> list[str]:
    """Collect local text imports only; source inventory retains all static assets."""
    seen = set(demos)
    queue = list(demos)
    while queue:
        relative = queue.pop()
        path = safe_path(source, relative)
        if path.suffix not in {'.tsx', '.ts', '.jsx', '.js', '.mjs'}:
            continue
        text = path.read_text(encoding='utf-8')
        imports = re.findall(r'(?:from\s*|import\s*)[\'"](\.[^\'"]+)[\'"]', text)
        for spec in imports:
            base = (path.parent / spec).resolve()
            if not base.is_relative_to(source.resolve()):
                raise ValueError(f'Import escapes upstream source: {relative}: {spec}')
            choices = [base] if base.suffix else [base.with_suffix(s) for s in ('.tsx', '.ts', '.js', '.json')]
            choices += [base / f'index{s}' for s in ('.tsx', '.ts', '.js')]
            matches = [candidate for candidate in choices if candidate.is_file()]
            if not matches:
                raise ValueError(f'Unresolved local import: {relative}: {spec}')
            dependency = matches[0].relative_to(source).as_posix()
            if dependency not in seen:
                seen.add(dependency)
                queue.append(dependency)
    return sorted(seen - set(demos))


def parse_library(source: Path) -> tuple[dict, list[dict], list[dict]]:
    library = json.loads((source / 'gallery/api/library.json').read_text(encoding='utf-8'))
    author_map = author_style_mapping(source)
    cards, styles, ids = [], [], set()
    for card in library['cards']:
        identity, category = card['name'], card['category']
        if not ID_RE.fullmatch(identity) or category not in library['categories']:
            raise ValueError('Invalid upstream card identity/category')
        if card['source'] != f'references/shots/{category}/{identity}.md':
            raise ValueError(f'Category disagrees with source directory: {identity}')
        cards.append({'id': identity, 'category': category, 'summary': card['summary'],
                      'recipe_path': f'source/{card["source"]}',
                      'style_ids': [s['key'] for s in card['styles']]})
        for style in card['styles']:
            key = style['key']
            if not ID_RE.fullmatch(key) or key in ids:
                raise ValueError(f'Invalid or duplicate style ID: {key}')
            ids.add(key)
            demos, method = resolve_style_demos(source, card, style, author_map)
            media = style.get('media')
            url = media.get('url') if isinstance(media, dict) else media
            parsed = urllib.parse.urlparse(url or '')
            if parsed.scheme or parsed.netloc or parsed.path != f'./media/{key}.mp4':
                raise ValueError(f'Unexpected Gallery preview path for {key}: {url}')
            styles.append({
                'id': key, 'card_id': identity, 'category': category, 'tags': card.get('tags', [category]),
                'label': style.get('label', key), 'description': style.get('description', card['summary']),
                'use': style.get('use', card.get('use', '')), 'duration': card.get('duration', ''),
                'recipe_path': f'source/{card["source"]}',
                'demo_paths': [f'source/{p}' for p in demos],
                'dependency_paths': [f'source/{p}' for p in local_dependencies(source, demos)],
                'mapping_evidence': method,
                'preview_path': f'previews/{category}/{key}.mp4', 'upstream_preview_url': url,
                'runtime_readiness': {'status': 'archived_not_ready', 'all_styles_rendered': False},
            })
    if len(cards) != library['stats']['cardCount'] or len(styles) != library['stats']['styleCount']:
        raise ValueError('Gallery counts disagree with actual catalog')
    return library, cards, styles


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {
            'github.com', 'api.github.com', 'release-assets.githubusercontent.com',
            'objects.githubusercontent.com'}:
            raise ValueError('Release download redirected outside official GitHub hosts')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url: str):
    request = urllib.request.Request(url, headers={'User-Agent': 'OCV-Shotcraft-Archive/1.0',
                                                 'Accept': 'application/vnd.github+json'})
    return urllib.request.build_opener(OfficialRedirects).open(request, timeout=60)


def download_asset(asset: dict, cache: Path) -> Path:
    name = asset['name']
    if '/' in name or '\\' in name or name not in {'gallery-media.zip'} and not re.fullmatch(r'[a-z0-9-]+\.mp4', name):
        raise ValueError('Unexpected release asset name')
    expected_prefix = f'https://github.com/{REPO}/releases/download/gallery-media/'
    if asset['browser_download_url'] != expected_prefix + urllib.parse.quote(name):
        raise ValueError('Release asset URL is not in the expected official release')
    cache.mkdir(parents=True, exist_ok=True)
    target = safe_path(cache, f'{asset["id"]}-{name}')
    metadata = target.with_name(target.name + '.json')
    expected_digest = (asset.get('digest') or '').removeprefix('sha256:')
    if expected_digest and not re.fullmatch('[a-f0-9]{64}', expected_digest):
        raise ValueError('Unexpected release digest algorithm')
    if target.is_file() and metadata.is_file():
        previous = json.loads(metadata.read_text(encoding='utf-8'))
        digest = sha256(target)
        if target.stat().st_size == asset['size'] and previous.get('updated_at') == asset.get('updated_at') and digest == previous.get('sha256') and (not expected_digest or digest == expected_digest):
            return target
    partial = target.with_name(target.name + '.part')
    total = 0
    with open_url(asset['browser_download_url']) as response, partial.open('wb') as stream:
        for block in iter(lambda: response.read(1024 * 1024), b''):
            total += len(block)
            if total > asset['size']:
                raise ValueError(f'Asset exceeds published size: {name}')
            stream.write(block)
    digest = sha256(partial)
    if total != asset['size'] or expected_digest and digest != expected_digest:
        raise ValueError(f'Release asset integrity mismatch: {name}')
    partial.replace(target)
    write_json(metadata, {'id': asset['id'], 'updated_at': asset.get('updated_at'),
                          'bytes': total, 'sha256': digest, 'publisher_digest': asset.get('digest')})
    print(f'Downloaded {name}: {total:,} bytes', flush=True)
    return target


def download_previews(dest: Path, styles: list[dict]) -> dict:
    with open_url(RELEASE_API) as response:
        release = json.load(response)
    if release.get('tag_name') != 'gallery-media' or release.get('draft'):
        raise ValueError('Unexpected official media release')
    write_json(dest / 'release-assets.json', release)
    assets = release['assets']
    names = [a['name'] for a in assets]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate release asset names')
    wanted = {f"{s['id']}.mp4": s for s in styles}
    zipped = next((a for a in assets if a['name'] == 'gallery-media.zip'), None)
    cache = dest / '.downloads'
    provenance, in_zip = {}, set()
    if zipped:
        archive = download_asset(zipped, cache)
        archive_digest = sha256(archive)
        with zipfile.ZipFile(archive) as source_zip:
            for entry in source_zip.infolist():
                name = PurePosixPath(entry.filename).name
                if name not in wanted or entry.is_dir():
                    continue
                if name in in_zip or entry.file_size > 512 * 1024 * 1024:
                    raise ValueError(f'Duplicate or oversized preview in release ZIP: {name}')
                target = safe_path(dest, wanted[name]['preview_path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                with source_zip.open(entry) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                in_zip.add(name)
                provenance[name] = {'asset_id': zipped['id'], 'asset_name': zipped['name'],
                                    'asset_updated_at': zipped['updated_at'], 'zip_member': entry.filename,
                                    'asset_sha256': archive_digest}
    singles = [a for a in assets if a['name'] in wanted and
               (not zipped or a['updated_at'] > zipped['updated_at'] or a['name'] not in in_zip)]
    def install_single(asset):
        downloaded = download_asset(asset, cache)
        target = safe_path(dest, wanted[asset['name']]['preview_path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(downloaded, target)
        return asset['name'], {'asset_id': asset['id'], 'asset_name': asset['name'],
                               'asset_updated_at': asset['updated_at'], 'asset_sha256': sha256(downloaded)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, record in pool.map(install_single, singles):
            provenance[name] = record
    missing = sorted(set(wanted) - set(provenance))
    if missing:
        raise ValueError(f'Official release is missing {len(missing)} required previews: {missing}')
    return {'release_id': release['id'], 'tag': release['tag_name'], 'url': release['html_url'],
            'fetched_at': datetime.now(timezone.utc).isoformat(), 'previews': provenance}


def sync(source: Path, dest: Path, expected_commit: str = PINNED_COMMIT,
         with_previews: bool = False) -> dict:
    source, dest = source.resolve(), dest.resolve()
    if dest == source or dest.is_relative_to(source) or source.is_relative_to(dest):
        raise ValueError('Source and destination must be separate non-nested directories')
    dest.mkdir(parents=True, exist_ok=True)
    library, cards, styles = parse_library(source)
    commit, files = archive_source(source, dest, expected_commit)
    old_manifest = dest / 'manifest.json'
    media = json.loads(old_manifest.read_text(encoding='utf-8')).get('media_release') if old_manifest.is_file() else None
    if with_previews:
        media = download_previews(dest, styles)
    for style in styles:
        style['source_sha256'] = {path: files[path]['sha256'] for path in
                                  [style['recipe_path'], *style['demo_paths'], *style['dependency_paths']]}
        path = safe_path(dest, style['preview_path'])
        if path.is_file():
            with path.open('rb') as stream:
                header = stream.read(64)
            if b'ftyp' not in header:
                raise ValueError(f'Preview is not an MP4: {style["id"]}')
            files[style['preview_path']] = file_record(path)
            style['preview_sha256'] = files[style['preview_path']]['sha256']
            style['preview_available'] = True
        else:
            style['preview_available'] = False
    manifest = {'schema_version': 1, 'upstream': {'repository': f'https://github.com/{REPO}',
                'commit': commit, 'gallery_revision': library['revision'],
                'license': 'Apache-2.0', 'license_path': 'LICENSE'},
                'categories': library['categories'], 'cards': cards, 'styles': styles,
                'files': files, 'media_release': media,
                'summary': {'cards': len(cards), 'styles': len(styles),
                            'previews': sum(s['preview_available'] for s in styles),
                            'bytes': sum(f['bytes'] for f in files.values()),
                            'all_styles_rendered': False}}
    write_json(dest / 'manifest.json', manifest)
    for category, labels in library['categories'].items():
        write_json(dest / 'categories' / f'{category}.json', {
            'category': category, **labels, 'source_commit': commit,
            'cards': [c for c in cards if c['category'] == category],
            'styles': [s for s in styles if s['category'] == category]})
    write_json(dest / 'catalog.json', {'source_commit': commit, 'categories': library['categories'],
        'styles': [{k: s[k] for k in ('id', 'card_id', 'category', 'label', 'description', 'use', 'demo_paths')}
                   for s in styles], 'all_styles_rendered': False})
    return manifest['summary']


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--dest', type=Path, default=DEFAULT_DEST)
    parser.add_argument('--expected-commit', default=PINNED_COMMIT)
    parser.add_argument('--download-previews', action='store_true')
    args = parser.parse_args()
    try:
        summary = sync(args.source_dir, args.dest, args.expected_commit, args.download_previews)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Shotcraft archive failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
