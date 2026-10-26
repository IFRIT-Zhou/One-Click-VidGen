"""Transactional whole-segment insert/delete; never regenerate retained audio."""
from __future__ import annotations

import copy
import json
import shutil
import threading
import time
import uuid

from .narration_structure import cues_by_segment, inserted_cues


BACKUP_FILES = ('画面映射.json', '配音调整保留画面.json', '最终配音快照.json', '模块2.5_校对后字幕场景.json')


def prepare(editor, job, user_id, data):
    if job.request.get('skip_tts'):
        raise ValueError('上传的整段成品配音暂不支持新增或删除 TTS 分段')
    root = editor._project_dir(job.id, user_id)
    manifest = editor._load_manifest(root)
    if int(manifest.get('revision') or 0) != data['expected_revision']:
        raise RuntimeError('配音已发生变化，请刷新后重新选择，未执行增删')
    segments = copy.deepcopy(manifest['segments'])
    rows = json.loads((root/'other/画面时间线.json').read_text(encoding='utf-8-sig'))
    grouped = cues_by_segment(rows, segments)  # Validate before paying for TTS.
    action = data['action']
    if action == 'delete':
        indices = data.get('indices') or []
        if not indices or len(set(indices)) != len(indices) or any(i not in grouped for i in indices):
            raise ValueError('删除范围无效，请重新选择完整配音段')
        if len(indices) >= len(segments):
            raise ValueError('至少保留一段配音；如需更换全文，请新建项目')
    elif action == 'insert':
        if data.get('anchor_index') not in grouped or data.get('position') not in ('before', 'after'):
            raise ValueError('插入位置已失效，请重新选择')
        if not str(data.get('text') or '').strip():
            raise ValueError('新增配音文字不能为空')
    else:
        raise ValueError('不支持的配音结构操作')
    return root, manifest, segments, rows, grouped


def preview(editor, job, user_id, data):
    if editor.status(job.id).get('status') == 'running':
        raise RuntimeError('请等待当前配音编辑完成')
    root, manifest, segments, rows, grouped = prepare(editor, job, user_id, data)
    selected = [s for s in segments if int(s['index']) in (data.get('indices') or [])]
    cue_ids = {row['slide_id'] for s in selected for row in grouped[int(s['index'])]}
    affected = []
    mapping = root/'other/画面映射.json'
    if mapping.is_file():
        affected = [str(r.get('macro_scene_id')) for r in json.loads(mapping.read_text(encoding='utf-8-sig'))
                    if cue_ids.intersection(r.get('includes_slides') or [])]
    return dict(revision=int(manifest.get('revision') or 0), action=data['action'],
                segments=[dict(index=s['index'], text=s['text'], duration=s.get('duration', 0)) for s in selected],
                removed_seconds=round(sum(float(s['end'])-float(s['start'])+float(s.get('pause_after') or 0)
                                          for s in selected), 3), affected_visuals=affected,
                message='删除会同时移除配音、对应文案和字幕，后续时间轴前移；不重新配音，可撤销。')


def snapshot_extras(root, history):
    present = []
    for name in BACKUP_FILES:
        source = root/'other'/name
        if source.is_file():
            shutil.copy2(source, history/name)
            present.append(name)
    (history/'structure_backup.json').write_text(json.dumps({'present': present}), encoding='utf-8')


def restore_extras(root, history):
    marker = history/'structure_backup.json'
    if not marker.is_file():
        return
    present = json.loads(marker.read_text(encoding='utf-8'))['present']
    for name in BACKUP_FILES:
        target = root/'other'/name
        if name in present:
            shutil.copy2(history/name, target)
        else:
            target.unlink(missing_ok=True)
    from .visual_editor import VisualEditor
    if VisualEditor._mapping_path(root).is_file():
        VisualEditor._write_timing_html(root, VisualEditor._load_mapping(root), VisualEditor._load_timeline(root))


def start(editor, job, user_id, data):
    # Reserve the editor before returning to HTTP, so a double click cannot
    # synthesize twice or delete a newly renumbered segment.
    with editor._lock:
        if editor._tasks.get(job.id, {}).get('status') == 'running':
            raise RuntimeError('请等待当前配音编辑完成')
        prepared = prepare(editor, job, user_id, data)
        editor._tasks[job.id] = dict(status='running', progress=0, updated_at=time.time(),
                                    message='正在新增配音…' if data['action']=='insert' else '正在删除所选配音…')
    threading.Thread(target=execute, args=(editor, job, user_id, data, prepared),
                     daemon=True, name=f'tts-structure-{job.id}').start()


def execute(editor, job, user_id, data, prepared):
    from .tts_editor import (_concat_segment_wavs, _write_srt_entries, _refine_subtitle_timing,
                             _commit_step_audio_edit, store)
    root, manifest, segments, source_rows, grouped = prepared
    history = None
    old_request = copy.deepcopy(job.request)
    work = editor._segment_dir(root)/('.structure-'+uuid.uuid4().hex)
    created_files = []
    try:
        work.mkdir(parents=True)
        before = {int(s['index']): s for s in segments}
        if data['action'] == 'delete':
            removed = set(data['indices'])
            updated = [copy.deepcopy(s) for s in segments if int(s['index']) not in removed]
            new_item = None
        else:
            text = data['text'].strip()
            editor._set_task(job.id, status='running', progress=10, message='仅生成新增配音，原有配音保持不变…')
            generated = editor._synthesize_parts(job=job, user_id=user_id, project_dir=root,
                                                 manifest=manifest, texts=[text], work_dir=work)
            if len(generated) != 1:
                raise ValueError('新增配音没有返回完整音频，原项目未修改')
            filename = 'segment_'+uuid.uuid4().hex[:12]+'.wav'
            target = editor._segment_dir(root)/filename
            shutil.copy2(generated[0], target)
            created_files.append(target)
            new_item = dict(text=text, tts_text=text, filename=filename, pause_after=data.get('pause_after', 0))
            offset = next(i for i,s in enumerate(segments) if s['index']==data['anchor_index'])
            offset += data['position'] == 'after'
            updated = copy.deepcopy(segments)
            updated.insert(offset, new_item)
            removed = set()
        origins = {s['filename']: int(s['index']) for s in segments}
        duration = editor._refresh_segment_timing(updated, editor._segment_dir(root))
        rows = []
        inserted = []
        for segment in updated:
            old_index = origins.get(segment['filename'])
            if old_index is None:
                current = inserted_cues(segment['text'], segment['start'], segment['end'])
                inserted.append(segment)
            else:
                delta = float(segment['start']) - float(before[old_index]['start'])
                current = [dict(row, start=round(float(row['start'])+delta, 6),
                                end=round(float(row['end'])+delta, 6)) for row in grouped[old_index]]
            rows.extend(current)
        fallbacks = 0
        if inserted:
            editor._set_task(job.id, status='running', progress=70, message='新增配音已生成，正在同步小字幕时间…')
            rows, fallbacks = _refine_subtitle_timing(root, rows, inserted,
                {int(s['index']): editor._segment_dir(root)/s['filename'] for s in inserted})
        for index, row in enumerate(rows, 1):
            row.update(id=f'scene_{index:03d}', slide_id=f'scene_{index:03d}', scene_id=f'scene_{index:03d}', index=index)
        if not rows or any(b['start'] < a['end']-.001 for a,b in zip(rows, rows[1:])):
            raise ValueError('增删后的字幕时间轴重叠或为空，未提交修改')
        # Full audio is built and validated off to the side first.
        editor._set_task(job.id, status='running', progress=90, message='正在重组完整配音并同步时间轴…')
        _concat_segment_wavs(updated, editor._segment_dir(root), work/'audio.wav')
        if editor.revision(job.id, user_id) != data['expected_revision']:
            raise RuntimeError('生成期间配音已被修改，新增音频保留，未覆盖现有内容')
        count, _ = editor._snapshot_history(root, manifest, sorted(removed),
                                            '删除配音片段' if removed else '插入配音片段')
        history = editor._history_entries(root)[-1]
        snapshot_extras(root, history)
        editor._snapshot_boundary_script(job, root, history)
        manifest.update(segments=updated, total_duration=duration, structure_token=uuid.uuid4().hex,
                        revision=int(manifest.get('revision') or 0)+1, updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        (root/'other/画面时间线.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        _write_srt_entries(root/'other/最终字幕.srt', [row for row in rows if not row.get('subtitle_hidden')])
        (editor._segment_dir(root)/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.copy2(work/'audio.wav', root/'input/.配音.structure.wav')
        (root/'input/.配音.structure.wav').replace(root/'input/配音.wav')
        job.request.pop('_final_narration_visual_warning', None)
        _commit_step_audio_edit(job, root)
        if job.request.get('_final_narration_visual_warning'):
            raise ValueError(job.request['_final_narration_visual_warning'])
        editor._sync_module1_flat_outputs(root, job.id)
        message = (f'已删除 {len(removed)} 段配音，未调用 TTS。' if removed else '新增配音已生成，其他配音未重配。')
        message += '文案、字幕与时间轴已同步；请核对受影响画面，可撤销。'
        if fallbacks:
            message += '新增段小字幕使用估算时间，请试听核对。'
        editor._set_task(job.id, status='completed', progress=100, message=message, structure_edit=True)
        store.log(job, message)
        shutil.rmtree(work)
    except Exception as exc:
        rollback_error = ''
        if history is not None:
            try:
                editor._restore_boundary_script(job, root, history)
                editor._restore_history(root, history, consume=True)
                job.request.clear()
                job.request.update(old_request)
                store.update(job, request=job.request)
            except Exception as rollback:
                rollback_error = f'；回滚异常，请保留项目并联系支持：{rollback}'
        # Generated new audio stays in work/output for recovery on failure.
        for file in created_files:
            if history is None:
                file.unlink(missing_ok=True)
        editor._set_task(job.id, status='failed', message=f'配音增删失败：{exc}；原素材已保留{rollback_error}')
        store.log(job, f'配音增删失败：{exc}{rollback_error}')
