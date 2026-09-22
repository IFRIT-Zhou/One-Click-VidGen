"""Read-only access to completed source projects; copies are owned by video drafts."""
import shutil
import json
from pathlib import Path
from fastapi import HTTPException


def narration_groups(source, scenes):
    """Use narration semantics only when all text matches the imported SRT.

    Timing always comes from SRT, never the TTS chunk clock (pauses may differ).
    No fuzzy matching: an edited or stale manifest must not move video boundaries.
    """
    try:
        manifest = json.loads((source / 'other' / 'tts_segments' / 'manifest.json').read_text(encoding='utf-8-sig'))
        segments = manifest['segments']
        clean = lambda text: ''.join(c for c in text if c.isalnum())
        texts = [clean(s['text']) for s in segments]
        subtitles = [clean(s['text']) for s in scenes]
        if not texts or any(not t for t in texts+subtitles) or ''.join(texts) != ''.join(subtitles):
            return []
        groups, cursor = [], 0
        for text in texts:
            first, collected = cursor, ''
            while cursor < len(scenes) and len(collected) < len(text):
                collected += subtitles[cursor]
                cursor += 1
            if collected != text:
                return []
            groups.append(dict(slide_ids=[s['slide_id'] for s in scenes[first:cursor]],
                               text=''.join(s['text'] for s in scenes[first:cursor])))
        return groups
    except (OSError, ValueError, KeyError, TypeError):
        return []


def dependencies():
    from .pipeline import store
    from .visual_editor import visual_editor
    from .db import list_media_assets
    return store, visual_editor, list_media_assets


def source_project(user_id, job_id, *, audio_task=False):
    store, editor, _ = dependencies()
    job = store.get(job_id)
    if not job or str(job.user_id) != str(user_id):
        raise HTTPException(404, '来源项目不存在')
    ready_audio = (audio_task and job.request.get('dynamic_video')
                   and job.status == 'waiting_confirmation'
                   and job.request.get('_step_mode_stage') == 'audio_review')
    if job.status != 'completed' and not ready_audio:
        raise HTTPException(409, '当前仅支持导入已完成的项目')
    try:
        root = editor.output_dir(job_id, int(user_id))
    except FileNotFoundError as exc:
        raise HTTPException(409, '来源项目输出目录不存在') from exc
    if not (root / 'other' / '最终字幕.srt').is_file() or not (root / 'input' / '配音.wav').is_file():
        raise HTTPException(409, '来源项目缺少最终字幕或配音文件，暂时无法导入')
    return job, root


def list_sources(user_id):
    _, _, assets = dependencies()
    ids = dict.fromkeys(str(a['generation_job_id']) for a in assets(user_id=int(user_id))
                        if a.get('role') == 'project_output' and a.get('generation_job_id'))
    rows = []
    for job_id in ids:
        try:
            job, root = source_project(user_id, job_id)
        except HTTPException:
            continue
        rows.append(dict(id=job_id, name=root.name, updated_at=job.updated_at))
    return sorted(rows, key=lambda row: row['updated_at'], reverse=True)


def copy_assets(source, target):
    """No hard links, source edits, or recursive copies of caches/history."""
    def copy(path, relative):
        path.resolve().relative_to(source.resolve())
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return relative.as_posix()
    audio = copy(source / 'input' / '配音.wav', Path('assets/audio.wav'))
    copy(source / 'other' / '最终字幕.srt', Path('assets/subtitles.srt'))
    # Never inherit old visuals, reference material, or storyboard mappings.
    # The imported SRT alone supplies the new subtitle time base.
    return audio
