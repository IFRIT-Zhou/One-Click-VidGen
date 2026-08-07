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


def split_subtitle_text(text, max_chars=24):
    normalized = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not normalized:
        return []
    pieces = re.findall(r'[^，。！？；：,.!?;:]+[，。！？；：,.!?;:]?', normalized)
    chunks = []
    current = ''
    for piece in pieces or [normalized]:
        while len(piece) > max_chars:
            head, piece = piece[:max_chars], piece[max_chars:]
            if current:
                chunks.append(current)
                current = ''
            chunks.append(head)
        if not piece:
            continue
        if current and len(current) + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


def split_corrected_scenes(scene_data, corrected_texts, max_chars=24):
    result = []
    for index, item in enumerate(scene_data):
        text = corrected_texts[index] if index < len(corrected_texts) else item.get('text_content', '')
        chunks = split_subtitle_text(text, max_chars=max_chars) or [str(text or '').strip()]
        start = float(item.get('start') or 0)
        end = max(float(item.get('end') or start + 0.2), start + 0.2)
        weights = [max(1, len(re.sub(r'\s+', '', chunk))) for chunk in chunks]
        total_weight = sum(weights)
        consumed = 0
        for chunk_index, (chunk, weight) in enumerate(zip(chunks, weights)):
            chunk_start = start + (end - start) * consumed / total_weight
            consumed += weight
            chunk_end = end if chunk_index == len(chunks) - 1 else start + (end - start) * consumed / total_weight
            output_index = len(result) + 1
            result.append(
                {
                    **item,
                    'id': f'segment_{output_index:03d}',
                    'slide_id': f'scene_{output_index:03d}',
                    'start': round(chunk_start, 3),
                    'end': round(chunk_end, 3),
                    'text_content': chunk,
                }
            )
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
