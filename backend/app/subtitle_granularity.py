"""Subtitle cues are smaller than (and independent of) synthesized WAV chunks."""
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun
# AGPL-3.0 Section 7 terms: ../../ADDITIONAL_TERMS.md
from __future__ import annotations

import difflib
import re


def compact(text):
    return re.sub(r'\s+', '', str(text or ''))


def punctuation_cuts(text):
    """Use linguistic boundaries, never split a cue merely to evade a time cap."""
    cuts = []
    for index, char in enumerate(text):
        if char in '，。！？；：、,!?;:\n' or (
            char == '.' and not (index and index + 1 < len(text)
                                  and text[index - 1].isdigit() and text[index + 1].isdigit())
        ):
            end = index + 1
            while end < len(text) and (text[end] in '，。！？；：、,!?;:.”’」』）》】\"\'' or text[end].isspace()):
                end += 1
            if end < len(text) and compact(text[end:]):
                cuts.append(end)
    return sorted(set(cuts))


def _map_position(operations, position, length):
    for tag, a, b, c, d in operations:
        if tag == 'insert' and a == position:
            return c
        if a <= position < b:
            return c + position - a if tag == 'equal' else c + (position - a) * (d - c) / (b - a)
    return length


def resegment_cues(source, parts, texts):
    """Retain old cue boundaries AND the user's new TTS boundaries.

    Old timing anchors provide a reversible fallback. The editor subsequently
    refines these times against the new audio; this function never claims ASR.
    """
    if len(parts) != len(texts) or not parts:
        raise ValueError('配音分段与字幕数量不一致')
    old = ''.join(compact(r.get('text_content') or r.get('text')) for r in source)
    new = ''.join(compact(t) for t in texts)
    forward = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    backward = difflib.SequenceMatcher(None, new, old, autojunk=False).get_opcodes()
    anchors, cuts, cursor = [], set(), 0
    for row in source:
        size = len(compact(row.get('text_content') or row.get('text')))
        anchors.append((cursor, cursor + size, row))
        cursor += size
        cuts.add(round(_map_position(forward, cursor, len(new))))

    def original(position):
        position = _map_position(backward, position, len(old))
        for left, right, row in anchors:
            if position < right or row is anchors[-1][2]:
                ratio = max(0, min(1, (position - left) / max(1, right - left)))
                return float(row['start']) + ratio * (float(row['end']) - float(row['start'])), row
        raise ValueError('原字幕为空')

    result, cursor = [], 0
    for part, raw in zip(parts, texts):
        raw = str(raw)
        size = len(compact(raw))
        if not size or float(part['end']) <= float(part['start']):
            raise ValueError('字幕文字或配音时间无效')
        # Convert normalized offsets back to the original display text so spaces,
        # annotations and punctuation are not lost.
        indices = [i for i, char in enumerate(raw) if not char.isspace()] + [len(raw)]
        local = {0, size, *(c - cursor for c in cuts if cursor < c < cursor + size)}
        estimated = set()
        # Old buggy edits have one very long cue per WAV. Restore punctuation
        # boundaries only inside long cues, not every comma in normal subtitles.
        for a, b in zip(sorted(local), sorted(local)[1:]):
            duration = (float(part['end']) - float(part['start'])) * (b - a) / size
            if duration > 8:
                fragment = raw[indices[a]:indices[b]]
                estimated.update(a + len(compact(fragment[:cut])) for cut in punctuation_cuts(fragment))
        local.update(estimated)
        left_time, _ = original(cursor)
        right_time, _ = original(cursor + size)

        def retime(position):
            if position == 0:
                return float(part['start'])
            if position == size:
                return float(part['end'])
            old_time, _ = original(cursor + position)
            ratio = ((old_time - left_time) / (right_time - left_time)
                     if right_time > left_time else position / size)
            return float(part['start']) + max(0, min(1, ratio)) * (float(part['end']) - float(part['start']))

        ordered = sorted(local)
        boundaries = [retime(position) for position in ordered]
        if any(b - a < .001 for a, b in zip(boundaries, boundaries[1:])):
            # A newly inserted phrase may have no old time anchor. Fall back for
            # the entire part, not one cue (which could overlap its neighbours).
            boundaries = [float(part['start']) + position / size * (float(part['end']) - float(part['start']))
                          for position in ordered]
        for left, right, start, end in zip(ordered, ordered[1:], boundaries, boundaries[1:]):
            _, template = original(cursor + (left + right) / 2)
            text = raw[(0 if left == 0 else indices[left]):indices[right]]
            result.append(dict(template, text=text, text_content=text, start=start, end=end,
                               subtitle_timing_source='estimated_punctuation' if estimated else 'retimed_subtitle_boundaries'))
        cursor += size
    if any(b['start'] < a['end'] - .001 for a, b in zip(result, result[1:])):
        raise ValueError('字幕交界时间重叠，未提交修改')
    return result


def restore_coarse_cues(rows, segments):
    """Repair legacy one-cue-per-WAV rows, without touching independent captions."""
    result, affected = [], set()
    for row in rows:
        text = str(row.get('text_content') or row.get('text') or '')
        match = next((s for s in segments
                      if not s.get('subtitle_independent') and compact(s.get('text')) == compact(text)
                      and abs(float(s['start']) - float(row['start'])) < .05
                      and abs(float(s['end']) - float(row['end'])) < .05 + float(s.get('pause_after') or 0)), None)
        if match and not row.get('subtitle_hidden') and float(row['end']) - float(row['start']) > 8 and punctuation_cuts(text):
            result.extend(resegment_cues([row], [row], [text]))
            affected.add(int(match['index']))
        else:
            result.append(dict(row))
    return result, affected
