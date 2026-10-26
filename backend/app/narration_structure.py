"""Explicit subtitle provenance for reversible whole-TTS insertion/deletion."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid


IDENTITY_FIELDS = ('narration_unit_id', 'narration_ancestors', 'narration_structure', 'narration_origin')


def unit_id(row):
    if row.get('narration_unit_id'):
        return row['narration_unit_id']
    value = [row.get('slide_id') or row.get('id'), row.get('text_content', row.get('text', '')),
             round(float(row['start']), 3), round(float(row['end']), 3)]
    return 'legacy_' + hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()[:24]


def tag(row, *, split=False):
    row = copy.deepcopy(row)
    parent = unit_id(row)
    row['narration_ancestors'] = list(dict.fromkeys([*(row.get('narration_ancestors') or []), parent]))
    row['narration_unit_id'] = uuid.uuid4().hex if split else parent
    row['narration_structure'] = True
    return row


def cues_by_segment(rows, segments):
    """Match exact text offsets, not fuzzy text or a subtitle's midpoint.

    A legacy cue crossing a WAV boundary is split at that *known* boundary;
    no word-level audio splice or ASR guess is used for the deletion itself.
    """
    compact = lambda text: ''.join(str(text or '').split())
    if ''.join(compact(r.get('text_content', r.get('text'))) for r in rows) != ''.join(compact(s['text']) for s in segments):
        raise ValueError('配音分段与完整字幕内容不一致，请先同步字幕再增删；原配音未修改')
    ranges, cursor = [], 0
    for segment in segments:
        size = len(compact(segment['text']))
        if not size:
            raise ValueError('原配音存在空白分段，不能安全增删')
        ranges.append((cursor, cursor + size, segment))
        cursor += size
    result = {int(s['index']): [] for s in segments}
    cursor = 0
    for row in rows:
        raw = str(row.get('text_content', row.get('text', '')))
        positions = [i for i, char in enumerate(raw) if not char.isspace()] + [len(raw)]
        size = len(positions) - 1
        for left, right, segment in ranges:
            a, b = max(cursor, left), min(cursor + size, right)
            if b <= a:
                continue
            fragment = tag(row, split=a != cursor or b != cursor + size)
            text = raw[0 if a == cursor else positions[a-cursor]:positions[b-cursor]]
            start = max(float(row['start']), float(segment['start']))
            end = min(float(row['end']), float(segment['end']) + float(segment.get('pause_after') or 0))
            if a > cursor:
                start = float(segment['start'])
            if b < cursor + size:
                end = float(segment['end'])
            if end <= start:
                raise ValueError('字幕与配音交界无法对应，请先检查交界；原配音未修改')
            fragment.update(text=text, text_content=text, start=start, end=end)
            result[int(segment['index'])].append(fragment)
        cursor += size
    return result


def inserted_cues(text, start, end):
    from .subtitle_granularity import punctuation_cuts
    edges = [0, *punctuation_cuts(text), len(text)]
    rows = []
    for a, b in zip(edges, edges[1:]):
        if not text[a:b].strip():
            continue
        rows.append(dict(text=text[a:b], text_content=text[a:b],
                         start=start + (end-start)*a/len(text), end=start + (end-start)*b/len(text),
                         narration_unit_id=uuid.uuid4().hex, narration_ancestors=[], narration_structure=True,
                         narration_origin='inserted',
                         subtitle_timing_source='estimated_insert'))
    return rows


def remap_structure(old_scenes, new_scenes, groups, key):
    """Return None for ordinary edits; explicit structural edits never guess."""
    if not any(s.get('narration_structure') for s in new_scenes):
        return None
    owners = {sid: index for index, group in enumerate(groups) for sid in group[key]}
    tokens = {unit_id(row): owners[row['slide_id']] for row in old_scenes}
    assigned = []
    for row in new_scenes:
        identity = unit_id(row)
        # A retained child can share ancestry with a sibling now bound to a
        # different shot. Its current identity is always more specific.
        if identity in tokens:
            assigned.append(tokens[identity])
            continue
        ancestry = set(row.get('narration_ancestors') or [])
        matches = {tokens[token] for token in ancestry if token in tokens}
        if len(matches) > 1:
            raise ValueError('新增配音的字幕跨越多个旧画面，请先核对交界；已有素材已保留')
        if not matches and row.get('narration_origin') != 'inserted':
            raise ValueError('原配音字幕的画面归属无法定位，请先同步原配音版本；素材未修改')
        assigned.append(next(iter(matches)) if matches else None)
    # When metadata cannot connect *any* original cue, do not disguise a stale
    # source snapshot as a completely new project.
    if not any(owner is not None for owner in assigned):
        raise ValueError('配音结构编辑与当前分镜版本不一致，请先同步原配音版本；素材未修改')
    runs = []
    for row, owner in zip(new_scenes, assigned):
        if not runs or runs[-1][0] != owner:
            runs.append((owner, []))
        runs[-1][1].append(row['slide_id'])
    retained, seen = [], set()
    id_key = 'id' if key == 'slide_ids' else 'macro_scene_id'
    for owner, ids in runs:
        if owner is None:
            item = {id_key: 'audio_' + uuid.uuid4().hex[:12], key: ids, 'placeholder': True,
                    'narration_inserted': True, 'design_needs_review': True,
                    'intent': '新增配音，待规划画面', 'image_prompt': '', 'video_prompt': '',
                    'reference_ids': [], 'kind': 'video', 'action': '', 'visual_description': '',
                    'image_status': 'pending', 'video_status': 'pending'}
        else:
            item = copy.deepcopy(groups[owner])
            item[key] = ids
            if owner in seen:
                item['narration_parent_visual_id'] = item[id_key]
                item[id_key] = 'audio_' + uuid.uuid4().hex[:12]
            seen.add(owner)
        retained.append(item)
    detached = [dict(copy.deepcopy(group), audio_sync_retained_reason='对应配音已删除，画面退出时间轴，素材保留可撤销')
                for index, group in enumerate(groups) if index not in seen]
    return retained, detached
