"""Text-only proofreading contracts: no ASR, TTS or timing changes."""
import hashlib
import json


def timing_token(rows):
    values = [(r.get('slide_id') or r.get('id'), r.get('start'), r.get('end')) for r in rows]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def preview(rows, updates, reference_text='', terms=()):
    by_id = {str(r.get('slide_id') or r.get('id') or f'scene_{i:03d}'): r for i, r in enumerate(rows, 1)}
    unknown = set(updates) - set(by_id)
    if unknown:
        raise ValueError('字幕编号不存在：' + '、'.join(sorted(unknown)))
    changes = []
    for identity, value in updates.items():
        text = str(value).strip()
        if not text:
            raise ValueError(identity + ' 的字幕不能为空')
        row = by_id[identity]
        old = str(row.get('text_content') or row.get('text') or '')
        if old != text:
            changes.append(dict(slide_id=identity, before=old, after=text, start=row['start'], end=row['end']))
    present = ''.join(str(r.get('text_content') or r.get('text') or '') for r in rows)
    return dict(changes=changes, changed_count=len(changes), timing_token=timing_token(rows),
                reference_text=reference_text, terms_to_review=[t for t in dict.fromkeys(terms) if t and t not in present],
                warning='术语提示仅供校对，不代表配音读错；不会自动替换医学术语或重新配音。',
                timing_unchanged=True, audio_unchanged=True, generation_started=False, written=False)
