"""Annotated still-image review export, independent of paid video generation/final export."""
from __future__ import annotations

import copy
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import video_studio as studio
from .video_export import _asset, _run
from .pipeline import ffmpeg_binary, probe_media_duration, _subtitle_filter_path
from .subtitle_layout import normalize as normalize_layout, write_ass

router = APIRouter(prefix='/api/video-studio')
RUNNING = set()


class PreviewRequest(BaseModel):
    revision: int
    notes: dict[str, str] = Field(default_factory=dict)
    subtitles: bool = True
    background_transparency: int = Field(default=20, ge=0, le=100)


def effective_action(shot):
    """Strip known framing/guard boilerplate, never use expanded H3 prompts or call an LLM."""
    if shot.get('kind') != 'video':
        return ''

    def clean(value):
        lines = re.split(r'\n+|(?<=[。！？])\s*', str(value or ''))
        result = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.match(r'^(?:图\s*\d+\s*是|按提示词把|输出始终|禁止擅自生成|不得把配音|只有阶段方案|'
                        r'场景基础[:：]|当前可见文字[:：]|参考音频只|【仅用于发音|'
                        r'本镜保持.*(?:风格|造型|服装)|.*(?:严格参考图\s*\d+)\s*[。！]?$)', line):
                continue
            if re.match(r'^(?:subject_definitions|summary|retention_analysis|overall_soundscape|non_diegetic_music)\s*:', line, re.I):
                continue
            if re.match(r'^(?:Do not generate subtitles|Generate only scene-appropriate|N/A; no background)', line, re.I):
                continue
            line = re.sub(r'^(?:第\s*\d+\s*阶段|阶段\s*\d+|动态表达)\s*[:：]\s*', '', line)
            if line and line not in result:
                result.append(line)
        return '\n'.join(result)

    # Editable generic video prompt is authoritative over a possibly stale stage plan.
    text = clean(shot.get('video_prompt'))
    if text:
        return text
    beats = (shot.get('motion_plan') or {}).get('beats') or []
    text = clean('\n'.join(str(beat.get('action') or '') for beat in beats if isinstance(beat, dict)))
    return text or clean(shot.get('action'))


def preview_plan(record):
    shots = record.get('shots') or []
    return dict(revision=record['revision'], shots=[
        dict(id=s['id'], index=i + 1, kind=s.get('kind'), start=s.get('start'), end=s.get('end'),
             text=effective_action(s), image_ready=s.get('image_status') == 'completed' and bool(s.get('image')))
        for i, s in enumerate(shots)],
        note='使用当前核心图和最终配音；标记与动作说明仅用于分镜预览，不修改提示词或正式成片。')


def _font(size):
    candidates = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc',
                  Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/simhei.ttf',
                  Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
                  Path('/System/Library/Fonts/PingFang.ttc')]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise ValueError('分镜预览需要中文字体（微软雅黑、黑体或 Noto Sans CJK），请安装后重试')


def _wrap(text, font, width):
    lines = []
    for paragraph in text.splitlines():
        line = ''
        for character in paragraph:
            if line and font.getlength(line + character) > width:
                lines.append(line)
                line = ''
            line += character
        if line:
            lines.append(line)
    return lines


def annotated_frames(source, destination, *, kind, note, size, transparency=20):
    """Text panel occupies < 1/4 of the canvas. Overflow is paginated, never clipped."""
    w, h = size
    unit = min(w, h)
    margin, radius = round(unit * .025), round(unit * .032)
    font = _font(max(18, round(unit * .028)))
    badge_font = _font(round(radius * 1.25))
    panel_w, panel_h = round(w * .44), round(h * .42)
    pad, leading = round(unit * .015), round(font.size * 1.5)
    lines = _wrap(note.strip(), font, panel_w - 2 * pad)
    capacity = max(1, (panel_h - 2 * pad - leading) // leading)
    pages = [lines[i:i + capacity] for i in range(0, len(lines), capacity)] or [[]]
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        image = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
        base = Image.new('RGB', size, '#101514')
        base.paste(image, ((w-image.width)//2, (h-image.height)//2))
    paths = []
    destination.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages):
        frame = base.convert('RGBA')
        overlay = Image.new('RGBA', size)
        draw = ImageDraw.Draw(overlay)
        center = (margin + radius, margin + radius)
        draw.ellipse((margin, margin, margin + 2*radius, margin + 2*radius),
                     fill='#239957' if kind == 'video' else '#cf4149')
        draw.text(center, '动' if kind == 'video' else '静', font=badge_font, fill='white', anchor='mm')
        if page:
            box_h = min(panel_h, 2*pad + leading*(len(page) + 1))
            x, y = w-panel_w-margin, h-round(h*.13)-box_h
            draw.rounded_rectangle((x, y, x+panel_w, y+box_h), radius=pad,
                                   fill=(0, 0, 0, round(255*(100-transparency)/100)))
            label = '动态动作预览' + (f' · {index+1}/{len(pages)}' if len(pages) > 1 else '')
            for row, text in enumerate([label, *page]):
                draw.text((x+pad, y+pad+row*leading), text, font=font, fill='white',
                          stroke_width=max(1, round(unit*.0018)), stroke_fill='black')
        output = destination / f'page_{index+1:03d}.png'
        Image.alpha_composite(frame, overlay).convert('RGB').save(output)
        paths.append(output)
    return paths


def render_preview(root, snapshot, data):
    record = snapshot
    portrait = record.get('settings', {}).get('ratio') == '9:16'
    size = (1080, 1920) if portrait else (1920, 1080)
    duration = probe_media_duration(root / 'audio.wav')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('最终配音时长无效')
    fps = 30
    total_frames = max(1, round(duration*fps))
    clips = []
    for index, shot in enumerate(record['shots']):
        start = round(float(shot['start'])*fps) if index else 0
        end = round(float(record['shots'][index+1]['start'])*fps) if index+1 < len(record['shots']) else total_frames
        count = end-start
        if count <= 0 or end > total_frames:
            raise ValueError(f'第 {index+1:02d} 镜时间轴无效，请先同步最终配音和分镜')
        frames = annotated_frames(root / f'image_{index:04d}.png', root / f'frames_{index:04d}',
                                  kind=shot['kind'], note=(data.notes.get(shot['id'], effective_action(shot)) if shot['kind'] == 'video' else ''),
                                  size=size, transparency=data.background_transparency)
        if len(frames) > count:
            raise ValueError(f'第 {index+1:02d} 镜说明文字过长，请缩短后重试')
        for page, frame in enumerate(frames):
            n = round((page+1)*count/len(frames))-round(page*count/len(frames))
            clip = root / f'clip_{index:04d}_{page:03d}.mp4'
            _run([ffmpeg_binary(), '-y', '-loop', '1', '-framerate', str(fps), '-i', str(frame),
                  '-frames:v', str(n), '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
                  '-threads', '2', '-pix_fmt', 'yuv420p', str(clip)], f'第 {index+1:02d} 镜分镜预览', timeout=600)
            clips.append(clip)
    concat = root / 'concat.txt'
    concat.write_text('\n'.join("file '"+p.name+"'" for p in clips), encoding='utf-8')
    assembled = root / 'assembled.mp4'
    _run([ffmpeg_binary(), '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-c', 'copy', str(assembled)], '预览拼接', timeout=600)
    output = root / '分镜预览.mp4'
    args = [ffmpeg_binary(), '-y', '-i', str(assembled), '-i', str(root / 'audio.wav'), '-map', '0:v:0', '-map', '1:a:0']
    if data.subtitles:
        layout = normalize_layout({'video_orientation': 'portrait' if portrait else 'landscape'})
        layout.update(position=96, size=42 if portrait else 32, background=False)
        ass = write_ass(root / 'subtitles.srt', root / 'subtitles.ass', layout)
        args += ['-vf', f"ass=filename='{_subtitle_filter_path(ass)}'", '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-threads', '2']
    else:
        args += ['-c:v', 'copy']
    _run([*args, '-c:a', 'aac', '-b:a', '192k', '-t', str(duration), '-movflags', '+faststart', str(output)], '分镜预览合成', timeout=1800)
    actual = probe_media_duration(output)
    if abs(actual-duration) > .15:
        raise ValueError('预览时长与最终配音不一致，未提交预览结果')
    return output


def _worker(path, root, snapshot, data, task_id):
    try:
        output = render_preview(root, snapshot, data)
        result = dict(status='completed', file=str(output.relative_to(path)), source_revision=data.revision,
                      task_id=task_id, finished_at=time.time(), message='分镜预览已完成；正式动态制作进度未改变。')
    except Exception as exc:
        result = dict(status='failed', task_id=task_id, source_revision=data.revision, message=str(exc)[:2000])
    with studio.LOCK:
        try:
            current = studio.read(path)
            if (current.get('storyboard_preview_task') or {}).get('task_id') == task_id:
                current['storyboard_preview_task'] = result
                if result['status'] == 'completed':
                    current['storyboard_preview'] = copy.deepcopy(result)
                # Preview metadata is not a storyboard edit. Do not invalidate
                # a user's open shot editor merely because rendering finished.
                current.setdefault('logs', []).append('分镜预览：' + result['message'])
                studio.save(path, current)
        finally:
            RUNNING.discard(str(path))


@router.get('/{identity}/storyboard-preview/plan')
def get_plan(identity: str, request: Request):
    path = studio.directory(studio.require_user(request)['id'], identity)
    with studio.LOCK:
        record = studio.read(path)
        task = record.get('storyboard_preview_task') or {}
        if task.get('status') == 'running' and str(path) not in RUNNING:
            task.update(status='failed', message='上次分镜预览被应用重启中断，请重新导出；原项目与已完成预览保留。')
            studio.save(path, record)
        return {**preview_plan(record), 'task': task, 'preview': record.get('storyboard_preview')}


@router.post('/{identity}/storyboard-preview')
def start_preview(identity: str, data: PreviewRequest, request: Request):
    path = studio.directory(studio.require_user(request)['id'], identity)
    with studio.LOCK:
        record = studio.read(path)
        if record['revision'] != data.revision:
            raise HTTPException(409, '项目已变化，请关闭窗口后重新读取分镜预览')
        if str(path) in RUNNING:
            raise HTTPException(409, '分镜预览正在导出')
        shots = record.get('shots') or []
        if not shots or record.get('status') in {'planning', 'stopping', 'image_generating', 'image_stopping', 'exporting'}:
            raise HTTPException(409, '请先完成分镜图片，再导出分镜预览')
        ids = {s['id'] for s in shots}
        if set(data.notes) - ids or any(len(text) > 6000 for text in data.notes.values()):
            raise HTTPException(400, '预览说明包含不存在的镜头或超过6000字')
        try:
            audio = _asset(path, record.get('audio') or 'assets/audio.wav')
            srt = _asset(path, record.get('subtitles') or 'assets/subtitles.srt') if data.subtitles else None
            images = []
            previous = -1
            for index, shot in enumerate(shots):
                if shot.get('kind') not in {'video', 'static'}:
                    raise ValueError(f'第 {index+1:02d} 镜动静类型无效，请先确认分镜')
                start, end = float(shot['start']), float(shot['end'])
                if not all(math.isfinite(v) for v in (start, end)) or start < 0 or start < previous or end <= start:
                    raise ValueError(f'第 {index+1:02d} 镜时间轴无效')
                previous = end
                if shot.get('image_status') != 'completed' or (shot.get('image_task') or {}).get('status') == 'running' or studio.image_edit_key(record['id'], shot['id']) in studio.IMAGE_EDITS:
                    raise ValueError(f'第 {index+1:02d} 镜核心图尚未完成或正在修改')
                try:
                    images.append(_asset(path, shot.get('image')))
                except ValueError as exc:
                    raise ValueError(f'第 {index+1:02d} 镜核心图不可用：{exc}') from exc
            task_id = uuid.uuid4().hex
            root = path / 'storyboard_previews' / task_id
            root.mkdir(parents=True)
            for index, source in enumerate(images):
                shutil.copyfile(source, root / f'image_{index:04d}.png')
            shutil.copyfile(audio, root / 'audio.wav')
            if srt:
                shutil.copyfile(srt, root / 'subtitles.srt')
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        snapshot = copy.deepcopy(record)
        record['storyboard_preview_task'] = dict(status='running', task_id=task_id, source_revision=data.revision,
                                                 message='正在用已完成分镜图合成预览，不调用图片或视频模型。')
        studio.save(path, record)
        RUNNING.add(str(path))
        try:
            threading.Thread(target=_worker, args=(path, root, snapshot, data, task_id), daemon=True).start()
        except RuntimeError as exc:
            RUNNING.discard(str(path))
            record['storyboard_preview_task'].update(status='failed', message='预览合成未能启动，请重试')
            studio.save(path, record)
            raise HTTPException(503, '预览合成未能启动，请重试') from exc
        return record


@router.get('/{identity}/storyboard-preview/file')
def preview_file(identity: str, request: Request):
    path = studio.directory(studio.require_user(request)['id'], identity)
    record = studio.read(path)
    try:
        file = _asset(path, (record.get('storyboard_preview') or {}).get('file', ''))
        if (path / 'storyboard_previews').resolve() not in file.parents:
            raise ValueError('预览文件路径无效')
    except ValueError as exc:
        raise HTTPException(404, '分镜预览尚未生成') from exc
    return FileResponse(file, media_type='video/mp4', filename='分镜预览.mp4', content_disposition_type='inline')
