"""Align existing subtitle boundaries to regenerated speech, never stretch old gaps."""
from __future__ import annotations

import difflib
import json
import re
import sys
import os
import ctypes
from pathlib import Path


_NUMBER = re.compile(r'[零〇○一二两三四五六七八九十百千万亿]+|[0-9]+')
_DIGITS = dict(zip('零〇○一二两三四五六七八九', (0, 0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))


def _simplified(text):
    # Portable OCV targets Windows: use its Unicode conversion table rather
    # than introducing another model/runtime dependency.
    if os.name != 'nt' or not text:
        return text
    target = ctypes.create_unicode_buffer(len(text) + 1)
    count = ctypes.windll.kernel32.LCMapStringW(0x0804, 0x02000000, text,
                                               len(text), target, len(target))
    return target.value if count else text


def _number(value):
    if value.isascii():
        return value
    if not any(c in '十百千万亿' for c in value):
        return ''.join(str(_DIGITS[c]) for c in value)
    total, section, digit = 0, 0, 0
    for char in value:
        if char in _DIGITS:
            digit = _DIGITS[char]
        elif char in '十百千':
            section += (digit or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            digit = 0
        else:
            section += digit
            if char == '万':
                total += (section or 1) * 10000
            else:
                total = (total + section or 1) * 100000000
            section, digit = 0, 0
    return str(total + section + digit)


def _canonical(text, spans=None):
    result, times, cursor = [], [], 0
    for match in _NUMBER.finditer(text):
        result.append(text[cursor:match.start()])
        if spans is not None:
            times.extend(spans[cursor:match.start()])
        value = _number(match.group())
        result.append(value)
        if spans is not None:
            for index in range(len(value)):
                offset = match.start() + min(match.end() - match.start() - 1,
                                            index * (match.end() - match.start()) // len(value))
                times.append(spans[offset])
        cursor = match.end()
    result.append(text[cursor:])
    if spans is not None:
        times.extend(spans[cursor:])
    return ''.join(result), times


def _characters(text):
    return ''.join(c.lower() for c in _simplified(str(text)) if c.isalnum())


def clean(text):
    return _canonical(_characters(text))[0]


def spoken_cues(texts, reading_text):
    """Map display cue boundaries onto the actual TTS input, not old timings."""
    values = [clean(text) for text in texts]
    display = ''.join(values)
    spoken = clean(reading_text)
    if not spoken or spoken == display:
        return values
    opcodes = difflib.SequenceMatcher(None, display, spoken, autojunk=False).get_opcodes()

    def boundary(position):
        for tag, a, b, c, d in opcodes:
            if tag == 'insert' and a == position:
                return c
            if a <= position < b:
                if tag == 'equal':
                    return c + position - a
                return c + round((position - a) * (d - c) / (b - a))
        return len(spoken)

    cuts, cursor = [0], 0
    for value in values[:-1]:
        cursor += len(value)
        cuts.append(boundary(cursor))
    cuts.append(len(spoken))
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        raise ValueError('朗读文本删除了整条字幕，无法可靠定位字幕边界；请同步修改字幕后重试')
    return [spoken[a:b] for a, b in zip(cuts, cuts[1:])]


def align_starts(texts, words, duration, reading_text=None):
    if reading_text is not None:
        texts = spoken_cues(texts, reading_text)
    voiced = [w for w in words if clean(w['word']) and float(w['end']) > float(w['start'])]
    if any(float(b['start'])-float(a['end']) > 3.0 for a, b in zip(voiced, voiced[1:])):
        raise ValueError('新配音中存在超过 3 秒的无语音区间，可能是异常停顿或低频噪音；请试听后重配，未替换原配音')
    chars, spans = [], []
    for word in words:
        value = _characters(word['word'])
        start, end = float(word['start']), float(word['end'])
        for index, char in enumerate(value):
            chars.append(char)
            spans.append((start + (end-start)*index/len(value), end))
    recognized, spans = _canonical(''.join(chars), spans)
    expected = ''.join(clean(text) for text in texts)
    matcher = difflib.SequenceMatcher(None, expected, recognized, autojunk=False)
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
        result.append(align_starts(item['texts'], words, item['duration'], item.get('reading_text')))
    Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    main()
