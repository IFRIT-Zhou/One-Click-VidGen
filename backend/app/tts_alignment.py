"""Align existing subtitle boundaries to regenerated speech, never stretch old gaps."""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path


def clean(text):
    return ''.join(c.lower() for c in str(text) if c.isalnum())


def align_starts(texts, words, duration):
    voiced = [w for w in words if clean(w['word']) and float(w['end']) > float(w['start'])]
    if any(float(b['start'])-float(a['end']) > 3.0 for a, b in zip(voiced, voiced[1:])):
        raise ValueError('新配音中存在超过 3 秒的无语音区间，可能是异常停顿或低频噪音；请试听后重配，未替换原配音')
    chars, spans = [], []
    for word in words:
        value = clean(word['word'])
        start, end = float(word['start']), float(word['end'])
        for index, char in enumerate(value):
            chars.append(char)
            spans.append((start + (end-start)*index/len(value), end))
    expected = ''.join(clean(text) for text in texts)
    matcher = difflib.SequenceMatcher(None, expected, ''.join(chars), autojunk=False)
    mapping = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapping[block.a+offset] = block.b+offset
    starts, cursor = [], 0
    for text in texts:
        value = clean(text)
        matched = [i for i in range(cursor, cursor+len(value)) if i in mapping]
        if not value or len(matched)/len(value) < .65:
            raise ValueError('新配音与字幕无法可靠对齐，可能漏读或发音异常；新配音已保留，请检查后重试，不会沿用旧时间戳')
        # A phrase must have evidence near its beginning, not merely its tail.
        first = matched[0]
        if first-cursor > max(2, len(value)//5):
            raise ValueError('新配音句首无法定位，请检查是否漏读')
        starts.append(max(0.0, min(float(duration), spans[mapping[first]][0])))
        cursor += len(value)
    if any(b <= a for a, b in zip(starts, starts[1:])):
        raise ValueError('新配音字幕边界顺序异常')
    return starts


def main():
    from module2_scene_director import transcribe_audio
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    result = []
    for item in request:
        segments, _, _ = transcribe_audio(Path(item['audio']), word_timestamps=True)
        words = [dict(word=w.word, start=w.start, end=w.end) for s in segments for w in (s.words or [])]
        result.append(align_starts(item['texts'], words, item['duration']))
    Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    main()
