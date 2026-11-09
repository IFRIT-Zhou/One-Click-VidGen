"""Build silent distributable template thumbnails from existing smoke renders.

No model calls or re-rendering. Only the rendered pixels of each stage are
transcoded; project paths, narration and source attachments are not packaged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
RULES = json.loads((ROOT / 'lib/shotcraft-recipes.json').read_text(encoding='utf-8'))


def run(command):
    result = subprocess.run([str(value) for value in command], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:] or 'Media conversion failed')
    return result.stdout


def probe(binary, path):
    return json.loads(run([binary, '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', path]))


def source_stages(source):
    stages = {}
    for document in sorted(source.glob('input_*.json')):
        match = re.fullmatch(r'input_(\d+)\.json', document.name)
        if not match:
            continue
        batch = match.group(1)
        data = json.loads(document.read_text(encoding='utf-8'))
        render = source / f'render_{batch}'
        movie = render / 'silent.mp4'
        metadata = json.loads((render / 'metadata.json').read_text(encoding='utf-8'))
        if not movie.is_file() or metadata.get('renderer') != 'ocv-remotion-shotcraft-v1':
            raise ValueError(f'Batch {batch} has no completed Shotcraft video')
        proofs = {row['id']: row for row in metadata['stage_frames']}
        items = {row['id']: row for row in metadata.get('item_frames', [])}
        for stage in data['plan']['stages']:
            identity = stage['recipe']
            if identity not in RULES or identity in stages:
                raise ValueError(f'Unknown or duplicate template: {identity}')
            start, end = float(stage['start']), float(stage['end'])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end
                    <= metadata['duration_seconds'] + 0.05):
                raise ValueError(f'Invalid stage timing: {identity}')
            proof = proofs[stage['id']]
            # Flip's normal end proof shows the back. Its audited front proof
            # is the clearer poster for this hospital-photo demonstration.
            if identity == 'card-flip-reveal':
                proof = items.get(stage['items'][0]['id'], proof)
            if not start <= proof['time'] < end:
                raise ValueError(f'Poster is outside stage: {identity}')
            stages[identity] = {'movie': movie, 'start': start, 'end': end,
                                'poster_time': proof['time'], 'batch': int(batch)}
    if set(stages) != set(RULES):
        raise ValueError('Expected exactly the twelve adapted templates')
    return stages


def build(source, output, ffmpeg, ffprobe, width=320, fps=10):
    source, output = source.resolve(), output.resolve()
    if not output.is_relative_to(ROOT) or output in (ROOT, source):
        raise ValueError('Gallery output must be a dedicated directory under tools/vibe_remotion')
    if width < 160 or width > 640 or width % 16 or not 8 <= fps <= 12:
        raise ValueError('Use an even 16:9 thumbnail width (160–640) and 8–12 fps')
    height = width * 9 // 16
    stages = source_stages(source)
    output.mkdir(parents=True, exist_ok=True)
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    manifest = {'schema_version': 1, 'version': 'shotcraft-gallery-20261104.1',
                'demo_note': '医院素材离线模板示意；仅展示动效结构，实际文案、配色和素材由本镜决定。',
                'audio': False, 'styles': {}}
    with tempfile.TemporaryDirectory(prefix='gallery-build-', dir=cache) as temporary:
        temporary = Path(temporary)
        for identity, rule in RULES.items():
            stage = stages[identity]
            duration = stage['end'] - stage['start']
            gif = temporary / (identity + '.gif')
            poster = temporary / (identity + '.jpg')
            filters = (f'fps={fps},scale={width}:{height}:flags=lanczos,split[a][b];'
                       '[a]palettegen=max_colors=128:stats_mode=diff[p];'
                       '[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle')
            run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                 '-threads', '1', '-ss', stage['start'], '-i', stage['movie'],
                 '-t', duration, '-an', '-filter_complex_threads', '1',
                 '-filter_complex', filters, '-loop', '0', gif])
            run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                 '-threads', '1', '-ss', stage['poster_time'], '-i', stage['movie'],
                 '-an', '-frames:v', '1', '-vf', f'scale={width}:{height}:flags=lanczos',
                 '-q:v', '3', poster])
            media = probe(ffprobe, gif)
            stream = media['streams'][0]
            actual = float(media['format']['duration'])
            if ((stream['width'], stream['height']) != (width, height)
                    or any(row['codec_type'] == 'audio' for row in media['streams'])
                    or abs(actual - duration) > 1 / fps + 0.01
                    or gif.stat().st_size > 2 * 1024 * 1024):
                raise ValueError(f'Thumbnail validation failed: {identity}')
            manifest['styles'][identity] = {
                'name': rule['label'], 'preview': gif.name, 'poster': poster.name,
                'duration_seconds': round(actual, 3), 'width': width, 'height': height,
                'fps': fps, 'preview_bytes': gif.stat().st_size,
                'poster_bytes': poster.stat().st_size,
                'preview_sha256': hashlib.sha256(gif.read_bytes()).hexdigest(),
                'poster_sha256': hashlib.sha256(poster.read_bytes()).hexdigest(),
            }
            print(f'{identity}: {actual:g}s, {gif.stat().st_size // 1024} KiB', flush=True)
        # Publish only after every generated thumbnail passed validation.
        for row in manifest['styles'].values():
            for key in ('preview', 'poster'):
                os.replace(temporary / row[key], output / row[key])
        staged = temporary / 'manifest.json'
        staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(staged, output / 'manifest.json')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path,
                        help='Existing shotcraft-expanded-smoke output containing input_N.json/render_N')
    parser.add_argument('--output', type=Path, default=ROOT / 'gallery')
    parser.add_argument('--ffmpeg', type=Path, default=REPO / 'tools/ffmpeg/bin/ffmpeg.exe')
    parser.add_argument('--ffprobe', type=Path, default=REPO / 'tools/ffmpeg/bin/ffprobe.exe')
    parser.add_argument('--width', type=int, default=320)
    parser.add_argument('--fps', type=int, default=10)
    args = parser.parse_args()
    manifest = build(args.source, args.output, args.ffmpeg, args.ffprobe, args.width, args.fps)
    print('Total GIF bytes:', sum(row['preview_bytes'] for row in manifest['styles'].values()))
