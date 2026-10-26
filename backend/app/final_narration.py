"""Final narration semantics and subtitle-unit visual remapping (no model calls)."""
import copy
import hashlib
import json
import wave
from difflib import SequenceMatcher


SNAPSHOT_FILENAME = '最终配音快照.json'


def clean_text(value):
    return ''.join(c for c in str(value or '') if c.isalnum())


def final_narration_snapshot(project_dir, scenes=None):
    """Never use phonetic TTS spellings or independent captions as spoken copy."""
    if scenes is None:
        scenes = json.loads((project_dir / 'other' / '画面时间线.json').read_text(encoding='utf-8-sig'))
    from .narration_structure import IDENTITY_FIELDS
    # SRT has no identity metadata. Enrich its exact matching cues from the
    # canonical timeline so structural edits reach dynamic projects as well.
    try:
        canonical = json.loads((project_dir / 'other' / '画面时间线.json').read_text(encoding='utf-8-sig'))
        def cue_key(row):
            return (str(row.get('text_content', row.get('text', ''))),
                    round(float(row['start']), 3), round(float(row['end']), 3))
        keyed = {cue_key(row): row for row in canonical}
        enriched = []
        for row in scenes:
            source = keyed.get(cue_key(row), {})
            enriched.append({**row, **{key: source[key] for key in IDENTITY_FIELDS if key in source}})
        scenes = enriched
    except (OSError, ValueError, KeyError, TypeError):
        pass
    rows = [dict(slide_id=s.get('slide_id', f'scene_{i:03d}'),
                 text=str(s.get('text_content', s.get('text', ''))),
                 start=float(s['start']), end=float(s['end']),
                 **{k: v for k, v in s.items() if k.startswith('subtitle_hidden') or k in IDENTITY_FIELDS}) for i, s in enumerate(scenes, 1)]
    try:
        manifest = json.loads((project_dir / 'other' / 'tts_segments' / 'manifest.json').read_text(encoding='utf-8-sig'))
        segments = manifest.get('segments') or []
    except (OSError, ValueError):
        segments = []
        manifest = {}
    semantic_rows = rows
    try:
        complete = json.loads((project_dir / 'other' / '画面时间线.json').read_text(encoding='utf-8-sig'))
        # Hidden captions do not remove their spoken content from the story.
        if any(s.get('subtitle_hidden') for s in complete):
            semantic_rows = [dict(text=s.get('text_content', s.get('text', ''))) for s in complete]
    except (OSError, ValueError, TypeError):
        pass
    matched = (bool(segments) and
               ''.join(clean_text(s.get('text')) for s in segments) == ''.join(clean_text(s['text']) for s in semantic_rows))
    script = '\n'.join(str(s.get('subtitle_source_text', s.get('text', ''))
                           if s.get('subtitle_independent') else s.get('text', '')) for s in segments) if matched else '\n'.join(s['text'] for s in semantic_rows)
    snapshot = dict(version=1, script=script, scenes=rows,
                    duration=max((s['end'] for s in rows), default=0),
                    text_source='recorded_segments' if matched else 'final_timeline')
    if manifest.get('structure_token'):
        snapshot['structure_token'] = manifest['structure_token']
    try:
        with wave.open(str(project_dir / 'input' / '配音.wav'), 'rb') as audio:
            snapshot['duration'] = max(snapshot['duration'], audio.getnframes()/audio.getframerate())
    except (OSError, EOFError, wave.Error):
        pass
    snapshot['fingerprint'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return snapshot


def visual_playback_range(shots, index, total_duration=None):
    """A visual holds through a sentence-end pause; subtitle times stay intact."""
    shot = shots[index]
    start = float(shot['start']) if index else 0.0
    end = (float(shots[index+1]['start']) if index+1 < len(shots)
           else max(float(shot['end']), float(total_duration or 0)))
    return dict(start=start, end=end, duration=max(.001, end-start))


def remap_visual_groups(old_scenes, new_scenes, groups, *, key='slide_ids'):
    """Rebind existing visuals to whole final subtitle units, preserving assets.

    Exact matching text anchors surrounding local edits locate old boundaries;
    replacement spans are interpolated only to choose a *subtitle edge*, never
    to manufacture subtitle timings. Collapsed visuals are retained as alternates.
    """
    if not groups:
        return [], []
    old_ids = [s['slide_id'] for s in old_scenes]
    if [sid for group in groups for sid in group.get(key, [])] != old_ids:
        raise ValueError('原画面与字幕对应关系不完整，请先核对原分镜；已有素材未修改')
    if not new_scenes:
        raise ValueError('最终配音时间轴为空；已有素材未修改')
    from .narration_structure import remap_structure
    structural = remap_structure(old_scenes, new_scenes, groups, key)
    if structural is not None:
        return structural
    lengths = [len(group[key]) for group in groups]
    if len(old_scenes) == len(new_scenes):
        edges, cursor = [0], 0
        for length in lengths:
            cursor += length
            edges.append(cursor)
    else:
        def offsets(rows):
            texts = [clean_text(s.get('text_content', s.get('text', ''))) for s in rows]
            edges = [0]
            for text in texts:
                edges.append(edges[-1] + len(text))
            return ''.join(texts), edges
        before, old_edges = offsets(old_scenes)
        after, new_edges = offsets(new_scenes)
        operations = SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
        def project(position):
            for _, a, b, c, d in operations:
                if a <= position <= b and b > a:
                    return c + (position-a) * (d-c) / (b-a)
            return len(after)
        edges, cursor = [0], 0
        for i, length in enumerate(lengths[:-1], 1):
            cursor += length
            wanted = project(old_edges[cursor])
            # If enough subtitle units remain, retain every existing visual.
            low = edges[-1] + (len(new_scenes) >= len(groups))
            high = len(new_scenes) - (len(groups)-i if len(new_scenes) >= len(groups) else 0)
            edges.append(min(range(low, high+1), key=lambda n: (abs(new_edges[n]-wanted), n)))
        edges.append(len(new_scenes))
    retained, detached = [], []
    for group, left, right in zip(groups, edges, edges[1:]):
        row = copy.deepcopy(group)
        if right <= left:
            row['audio_sync_retained_reason'] = '字幕合并后原画面已无独立字幕区间，素材保留供核对或重新选用'
            detached.append(row)
            continue
        row[key] = [s['slide_id'] for s in new_scenes[left:right]]
        retained.append(row)
    return retained, detached
