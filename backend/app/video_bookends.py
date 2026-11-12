"""Local intro/outro assembly, after body audio and subtitles are rendered."""
import json
import math
import subprocess
import tempfile
from pathlib import Path


def resolve_clips(user_id, settings):
    from .editor import upload_path, media_kind
    result = []
    for key in ('intro_video_id', 'outro_video_id'):
        value = settings.get(key)
        if not value:
            result.append(None)
            continue
        source = upload_path(int(user_id), str(value))
        if media_kind(source) != 'video':
            raise ValueError('片头片尾必须是视频文件')
        result.append(source)
    return result


def probe(path):
    from .pipeline import ffprobe_binary
    p = subprocess.run([ffprobe_binary(), '-v', 'error', '-show_streams', '-show_format',
                        '-of', 'json', str(path)], capture_output=True, timeout=60, check=True)
    data = json.loads(p.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video:
        raise ValueError('片头片尾素材没有可用的视频轨道')
    duration = float(video.get('duration') or data.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('无法读取片头片尾视频时长')
    return video, duration, any(s.get('codec_type') == 'audio' for s in data['streams'])


def apply_bookends(target, user_id, settings):
    """Atomically replace a fresh body export. Never call on an already joined export."""
    intro, outro = resolve_clips(user_id, settings)
    if not intro and not outro:
        return
    from .pipeline import ffmpeg_binary
    target = Path(target)
    video, _, _ = probe(target)
    width, height = int(video['width']), int(video['height'])
    width -= width % 2
    height -= height % 2
    def run(args):
        p = subprocess.run([ffmpeg_binary(), '-y', *args], capture_output=True,
                           timeout=3600)
        if p.returncode:
            raise RuntimeError('片头片尾拼接失败：' + p.stderr.decode('utf-8', errors='replace')[-1500:])
    with tempfile.TemporaryDirectory(prefix='bookends-', dir=target.parent) as directory:
        root = Path(directory)
        parts = []
        for index, source in enumerate(p for p in (intro, target, outro) if p is not None):
            _, duration, audio = probe(source)
            output = root / f'{index}.mp4'
            args = ['-i', str(source)]
            if not audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            args += ['-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0',
                     '-vf', f'setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p',
                     '-af', 'asetpts=PTS-STARTPTS,aresample=48000,apad', '-t', str(duration),
                     '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                     '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '192k', str(output)]
            run(args)
            parts.append(output)
        manifest = root / 'concat.txt'
        manifest.write_text('\n'.join(f"file '{p.name}'" for p in parts), encoding='utf-8')
        result = root / 'result.mp4'
        run(['-f', 'concat', '-safe', '0', '-i', str(manifest), '-c', 'copy',
             '-movflags', '+faststart', str(result)])
        probe(result)
        result.replace(target)
