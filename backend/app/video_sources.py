"""Read-only access to completed source projects; copies are owned by video drafts."""
import shutil
import json
import hashlib
import wave
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
    if job.status not in {'completed', 'cancelled'} and not ready_audio:
        raise HTTPException(409, '当前仅支持导入已完成的项目')
    try:
        # A rendered dynamic output is not the editable narration workspace.
        from .tts_editor import tts_editor
        root = tts_editor._project_dir(job_id, int(user_id))
    except FileNotFoundError as exc:
        raise HTTPException(409, '来源项目输出目录不存在') from exc
    if not (root / 'other' / '最终字幕.srt').is_file() or not (root / 'input' / '配音.wav').is_file():
        raise HTTPException(409, '来源项目缺少最终字幕或配音文件，暂时无法导入')
    if tts_editor.status(job_id).get('status') == 'running':
        raise HTTPException(409, '配音编辑仍在运行或停止中，请等待退出后交接正式版本')
    if job.status == 'cancelled':
        try:
            from .pipeline import _step_audio_revision
            current = _step_audio_revision(root)
            marker = root / 'other' / 'audio_revision.json'
            if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8-sig')).get('fingerprint') != current['fingerprint']:
                raise ValueError('没有匹配的已完成配音快照')
            from .tts_editor import _srt_entries
            entries = _srt_entries(root / 'other' / '最终字幕.srt')
            with wave.open(str(root / 'input' / '配音.wav'), 'rb') as wav:
                duration = wav.getnframes() / wav.getframerate()
            if (not entries or duration <= 0 or any(float(e['end']) <= float(e['start']) for e in entries)
                    or float(entries[-1]['end']) > duration + .1
                    or any(float(b['start']) < float(a['end']) - .001 for a, b in zip(entries, entries[1:]))):
                raise ValueError('配音与字幕时序不完整或不一致')
        except (OSError, ValueError, RuntimeError, wave.Error) as exc:
            raise HTTPException(409, f'已停止任务的正式配音尚不能安全交接：{exc}') from exc
    return job, root


def source_readiness(user_id, job_id, *, include_snapshot=True):
    """Same eligibility as import, available before any write or model request."""
    store, _, _ = dependencies()
    job = store.get(job_id)
    if not job or str(job.user_id) != str(user_id):
        raise HTTPException(404, '来源项目不存在')
    result = dict(can_import=False, blocked_reason='', audio_ready=False, timeline_ready=False)
    try:
        _, root = source_project(user_id, job_id, audio_task=True)
        result.update(can_import=True, audio_ready=True, timeline_ready=True)
        if include_snapshot:
            result['adopted_snapshot'] = source_fingerprint(root)
    except HTTPException as exc:
        result['blocked_reason'] = str(exc.detail)
        # Readiness flags indicate committed usability, not mere file existence.
    return result


def source_fingerprint(source):
    files = ('input/配音.wav', 'other/最终字幕.srt')
    return {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files}


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
    before = source_fingerprint(source)
    audio = copy(source / 'input' / '配音.wav', Path('assets/audio.wav'))
    copy(source / 'other' / '最终字幕.srt', Path('assets/subtitles.srt'))
    copied = {'input/配音.wav': hashlib.sha256((target / audio).read_bytes()).hexdigest(),
              'other/最终字幕.srt': hashlib.sha256((target / 'assets/subtitles.srt').read_bytes()).hexdigest()}
    if before != copied or before != source_fingerprint(source):
        raise HTTPException(409, '复制期间正式配音版本发生变化，未采用混合版本，请重新交接')
    # Never inherit old visuals, reference material, or storyboard mappings.
    # The imported SRT alone supplies the new subtitle time base.
    return audio
