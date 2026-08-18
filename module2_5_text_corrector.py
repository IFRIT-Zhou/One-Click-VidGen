# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

import json
import re
import os
import sys
import difflib


def clean_alignment_text(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', str(text or ''))


def correct_scene_texts_to_original(scene_data, orig_text):
    """Map ASR segment boundaries onto the original text without losing characters.

    Character-by-character mappings drop an original-only ``insert`` opcode when
    Whisper omits a whole phrase. Boundary mapping instead partitions the original
    into contiguous slices, so every original character belongs to exactly one
    subtitle segment and no neighboring segment overlaps it.
    """
    clean_orig = clean_alignment_text(orig_text)
    orig_map = [index for index, char in enumerate(orig_text) if clean_alignment_text(char)]
    if not clean_orig or not orig_map:
        raise ValueError("原始文案不含可校准文字")

    clean_asr = ""
    segment_ends = []
    for segment in scene_data:
        clean_asr += clean_alignment_text(segment.get('text_content', ''))
        segment_ends.append(len(clean_asr))
    if not clean_asr or not segment_ends:
        raise ValueError("ASR 字幕不含可校准文字")

    matcher = difflib.SequenceMatcher(None, clean_asr, clean_orig, autojunk=False)
    boundary_map = {}
    original_only_boundaries = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for offset in range(i2 - i1 + 1):
                boundary_map[i1 + offset] = j1 + offset
        elif tag == 'replace':
            asr_length = i2 - i1
            original_length = j2 - j1
            if asr_length:
                for offset in range(asr_length + 1):
                    boundary_map[i1 + offset] = j1 + round(offset * original_length / asr_length)
        elif tag == 'delete':
            for boundary in range(i1, i2 + 1):
                boundary_map[boundary] = j1
        elif tag == 'insert':
            # Assign an ASR-omitted original phrase to the segment on its right.
            # The final boundary is forced to EOF below, so trailing omissions
            # naturally remain with the final segment.
            original_only_boundaries[i1] = j1

    def mapped_boundary(asr_boundary):
        if asr_boundary in original_only_boundaries:
            return original_only_boundaries[asr_boundary]
        if asr_boundary in boundary_map:
            return boundary_map[asr_boundary]
        lower = max((value for value in boundary_map if value < asr_boundary), default=0)
        upper = min((value for value in boundary_map if value > asr_boundary), default=len(clean_asr))
        if upper == lower:
            return boundary_map.get(lower, 0)
        ratio = (asr_boundary - lower) / (upper - lower)
        return round(boundary_map[lower] + ratio * (boundary_map[upper] - boundary_map[lower]))

    clean_boundaries = [0] + [mapped_boundary(end) for end in segment_ends]
    clean_boundaries[0] = 0
    clean_boundaries[-1] = len(clean_orig)
    for index in range(1, len(clean_boundaries)):
        clean_boundaries[index] = max(
            clean_boundaries[index - 1],
            min(len(clean_orig), clean_boundaries[index]),
        )

    def original_boundary(clean_index):
        if clean_index <= 0:
            return 0
        if clean_index >= len(clean_orig):
            return len(orig_text)
        return orig_map[clean_index]

    real_boundaries = [original_boundary(value) for value in clean_boundaries]
    corrected = [
        orig_text[real_boundaries[index]:real_boundaries[index + 1]].strip()
        for index in range(len(scene_data))
    ]
    if clean_alignment_text(''.join(corrected)) != clean_orig:
        raise RuntimeError("字幕校准完整性检查失败：校准结果未能 100% 覆盖原始文案")
    return corrected


_STRONG_SUBTITLE_ENDINGS = frozenset('。！？!?')
_SECONDARY_SUBTITLE_ENDINGS = frozenset('；;')
_COMMA_SUBTITLE_ENDINGS = frozenset('，,')
_COLON_SUBTITLE_ENDINGS = frozenset('：:')
_SUBTITLE_CLOSERS = frozenset('”’」』】）》〉>')
_SUBTITLE_OPENERS = frozenset('“‘「『【（《〈<')
_NON_BREAKING_PUNCTUATION = frozenset('、')
_CLAUSE_TRANSITIONS = (
    '与此同时', '换句话说', '也就是说', '更重要的是',
    '推到了', '变成了', '成为了', '意味着', '导致了', '造成了',
    '因此', '所以', '但是', '然而', '不过', '于是', '然后', '同时',
    '最终', '后来', '原来', '实际上', '反而', '仍然', '继续', '开始',
)


def _normalized_subtitle_text(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _subtitle_boundaries(text):
    """Return candidate cut positions without treating wrappers or 、 as breaks."""
    boundaries = {0: 'start', len(text): 'end'}
    index = 0
    while index < len(text):
        char = text[index]
        if char in _STRONG_SUBTITLE_ENDINGS:
            kind = 'strong'
        elif char in _SECONDARY_SUBTITLE_ENDINGS:
            kind = 'secondary'
        elif char in _COMMA_SUBTITLE_ENDINGS:
            kind = 'comma'
        elif char in _COLON_SUBTITLE_ENDINGS:
            kind = 'colon'
        else:
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in _SUBTITLE_CLOSERS:
            end += 1
        boundaries[end] = kind
        index = end
    return boundaries


def _safe_fallback_positions(text, start, end):
    """Offer last-resort cuts while keeping wrappers and Latin tokens intact."""
    positions = []
    for position in range(start + 1, end):
        previous = text[position - 1]
        following = text[position]
        if (
            previous in _SUBTITLE_OPENERS
            or following in _SUBTITLE_CLOSERS
            or previous in _NON_BREAKING_PUNCTUATION
            or following in _NON_BREAKING_PUNCTUATION
        ):
            continue
        if previous.isascii() and previous.isalnum() and following.isascii() and following.isalnum():
            continue
        positions.append(position)
    return positions


def _clause_transition_positions(text, start, end):
    positions = set()
    for marker in _CLAUSE_TRANSITIONS:
        position = text.find(marker, start + 1, end)
        while position >= 0:
            if position > start and position < end:
                positions.add(position)
            position = text.find(marker, position + len(marker), end)
    return positions


def _partition_subtitle_range(text, start, end, boundaries, max_chars, overflow_chars):
    """Choose a globally balanced partition for one complete sentence."""
    if end - start <= max_chars:
        return [text[start:end]]

    hard_max = max_chars + overflow_chars
    candidates = {start: 'start', end: boundaries.get(end, 'end')}
    for position, kind in boundaries.items():
        if start < position < end and kind in {'secondary', 'comma', 'colon'}:
            candidates[position] = kind
    for position in _clause_transition_positions(text, start, end):
        candidates.setdefault(position, 'clause')

    # A punctuation-free clause can still exceed the display guard.  These
    # positions carry a large cost and are never preferred over punctuation.
    if not any(position > start and position - start <= hard_max for position in candidates):
        for position in _safe_fallback_positions(text, start, end):
            candidates.setdefault(position, 'fallback')
    else:
        # Later portions may still need a fallback even when the first portion
        # has punctuation, so expose safe positions only for genuinely long spans.
        ordered_punctuation = sorted(candidates)
        for left, right in zip(ordered_punctuation, ordered_punctuation[1:]):
            if right - left > hard_max:
                for position in _safe_fallback_positions(text, left, right):
                    candidates.setdefault(position, 'fallback')

    ordered = sorted(candidates)
    target = min(max_chars, 18)
    boundary_cost = {
        'strong': 0.0,
        'secondary': 0.15,
        'comma': 0.55,
        'colon': 1.25,
        'clause': 2.0,
        'end': 0.0,
        'fallback': 8.0,
    }
    best = {start: (0.0, None)}
    for right in ordered[1:]:
        choice = None
        for left in ordered:
            if left >= right or left not in best:
                continue
            length = right - left
            if length > hard_max:
                continue
            cost = best[left][0] + boundary_cost.get(candidates[right], 2.0) + 0.35
            cost += ((length - target) / max(target, 1)) ** 2
            if length < 6:
                cost += (6 - length) * 1.8
            if length > max_chars:
                cost += (length - max_chars) * 1.5
            if choice is None or cost < choice[0]:
                choice = (cost, left)
        if choice is not None:
            best[right] = choice

    if end not in best:
        # This only applies to pathological text where every safe position is
        # blocked.  Preserve coverage with the old hard guarantee as a last resort.
        chunks = []
        cursor = start
        while cursor < end:
            cut = min(cursor + hard_max, end)
            chunks.append(text[cursor:cut])
            cursor = cut
        return chunks

    cuts = [end]
    cursor = end
    while cursor != start:
        cursor = best[cursor][1]
        cuts.append(cursor)
    cuts.reverse()
    return [text[left:right] for left, right in zip(cuts, cuts[1:]) if right > left]


def split_subtitle_text(text, max_chars=24, overflow_chars=4):
    """Split clean prose at semantic punctuation, with a rare safe fallback."""
    normalized = _normalized_subtitle_text(text)
    if not normalized:
        return []
    boundaries = _subtitle_boundaries(normalized)
    strong_positions = [
        position
        for position, kind in sorted(boundaries.items())
        if position and kind in {'strong', 'end'}
    ]
    chunks = []
    start = 0
    for end in strong_positions:
        chunks.extend(
            _partition_subtitle_range(
                normalized,
                start,
                end,
                boundaries,
                max_chars=max_chars,
                overflow_chars=overflow_chars,
            )
        )
        start = end
    return chunks


def split_corrected_scenes(scene_data, corrected_texts, max_chars=24):
    if not scene_data:
        return []
    source_texts = [
        corrected_texts[index] if index < len(corrected_texts) else item.get('text_content', '')
        for index, item in enumerate(scene_data)
    ]
    full_text = _normalized_subtitle_text(''.join(str(value or '') for value in source_texts))
    chunks = split_subtitle_text(full_text, max_chars=max_chars) or [full_text]

    source_weights = [max(1, len(re.sub(r'\s+', '', str(value or '')))) for value in source_texts]
    source_starts = []
    cumulative = 0
    for weight in source_weights:
        source_starts.append(cumulative)
        cumulative += weight
    total_weight = max(1, cumulative)

    def time_at(weight_offset):
        weight_offset = max(0, min(total_weight, weight_offset))
        for index, (item, source_start, weight) in enumerate(zip(scene_data, source_starts, source_weights)):
            source_end = source_start + weight
            if weight_offset <= source_end or index == len(scene_data) - 1:
                start = float(item.get('start') or 0)
                end = max(float(item.get('end') or start + 0.2), start + 0.2)
                ratio = (weight_offset - source_start) / max(weight, 1)
                return start + (end - start) * max(0.0, min(1.0, ratio))
        return float(scene_data[-1].get('end') or 0)

    result = []
    consumed = 0
    chunk_weights = [max(1, len(re.sub(r'\s+', '', chunk))) for chunk in chunks]
    for chunk, weight in zip(chunks, chunk_weights):
        chunk_start = time_at(consumed)
        consumed += weight
        chunk_end = time_at(consumed)
        output_index = len(result) + 1
        source_index = max(
            0,
            min(
                len(scene_data) - 1,
                next(
                    (
                        index
                        for index, (source_start, source_weight) in enumerate(zip(source_starts, source_weights))
                        if consumed - weight < source_start + source_weight
                    ),
                    len(scene_data) - 1,
                ),
            ),
        )
        result.append(
            {
                **scene_data[source_index],
                'id': f'segment_{output_index:03d}',
                'slide_id': f'scene_{output_index:03d}',
                'start': round(chunk_start, 3),
                'end': round(max(chunk_end, chunk_start + 0.001), 3),
                'text_content': chunk,
            }
        )
    if result:
        result[0]['start'] = round(float(scene_data[0].get('start') or 0), 3)
        result[-1]['end'] = round(float(scene_data[-1].get('end') or result[-1]['end']), 3)
    return result


def format_srt_time(seconds):
    milliseconds = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}'


def run_correction():
    print("🔍 [全局 Opcode 对齐] 正在执行物理级双轨文本校准...")
    base_dir = os.path.dirname(os.path.abspath(__file__))

    orig_file = os.path.join(base_dir, "workspace", "1_original_text.txt")
    json_file = os.path.join(base_dir, "workspace", "3_visual_template", "scene_timeline.json")
    srt_file = os.path.join(base_dir, "workspace", "2_audio_srt", "final_short.srt")

    if not os.path.exists(orig_file):
        print("❌ 找不到原始文案，请确保原文案已保存在 workspace/1_original_text.txt")
        sys.exit(1)
    if not os.path.exists(json_file):
        print("❌ 找不到 ASR 分镜 JSON，请先完成模块 2")
        sys.exit(1)
    if not os.path.exists(srt_file):
        print("❌ 找不到短字幕 SRT，请先完成模块 2")
        sys.exit(1)

    with open(orig_file, 'r', encoding='utf-8') as f:
        orig_text = f.read()

    with open(json_file, 'r', encoding='utf-8') as f:
        scene_data = json.load(f)

    try:
        corrected_texts = correct_scene_texts_to_original(scene_data, orig_text)
    except (ValueError, RuntimeError) as exc:
        print(f"❌ {exc}")
        sys.exit(1)

    # 步骤 4：校对后再次按屏幕可读长度断句，并同步重建 JSON 与 SRT。
    scene_data = split_corrected_scenes(scene_data, corrected_texts, max_chars=24)

    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(scene_data, f, ensure_ascii=False, indent=2)

    new_blocks = [
        f"{index}\n{format_srt_time(item['start'])} --> {format_srt_time(item['end'])}\n{item['text_content']}"
        for index, item in enumerate(scene_data, 1)
        if str(item.get('text_content') or '').strip()
    ]

    with open(srt_file, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(new_blocks) + '\n\n')

    print(f"✅ 文本校准与短字幕重切完毕，共 {len(scene_data)} 条，每条最多约 24 字。")

if __name__ == "__main__":
    run_correction()
