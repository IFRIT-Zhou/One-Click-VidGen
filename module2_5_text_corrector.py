import json
import re
import os
import sys
import difflib


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

    def clean_text(text):
        return re.sub(r'[^\w\u4e00-\u9fff]', '', text)

    clean_orig = clean_text(orig_text)
    orig_map = [i for i, char in enumerate(orig_text) if clean_text(char)]
    if not clean_orig or not orig_map:
        print("❌ 原始文案不含可校准文字")
        sys.exit(1)

    clean_asr_full = ""
    segment_bounds = []

    # 步骤 1：拼接全部 ASR 文本获取全局坐标
    for segment in scene_data:
        c_text = clean_text(segment['text_content'])
        start_idx = len(clean_asr_full)
        clean_asr_full += c_text
        end_idx = len(clean_asr_full)
        segment_bounds.append((start_idx, end_idx, segment['text_content']))

    # 步骤 2：生成全局字符级绝对映射字典
    sm = difflib.SequenceMatcher(None, clean_asr_full, clean_orig)
    asr_to_orig_map = {}

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                asr_to_orig_map[i1 + k] = j1 + k
        elif tag == 'replace':
            asr_len = i2 - i1
            orig_len = j2 - j1
            for k in range(asr_len):
                asr_to_orig_map[i1 + k] = min(j1 + int(k * orig_len / asr_len), j2 - 1)
        elif tag == 'delete':
            for k in range(i1, i2):
                asr_to_orig_map[k] = j1

    corrected_texts = []

    # 步骤 3：根据绝对映射表强行切割原始文案
    for idx, (start_idx, end_idx, original_asr) in enumerate(segment_bounds):
        if start_idx == end_idx:
            corrected_texts.append(original_asr)
            continue

        mapped_start = asr_to_orig_map.get(start_idx, asr_to_orig_map.get(start_idx+1, 0))
        mapped_end = asr_to_orig_map.get(end_idx - 1, asr_to_orig_map.get(end_idx - 2, len(clean_orig) - 1))

        if idx == len(segment_bounds) - 1:
            mapped_end = len(clean_orig) - 1

        if mapped_start > mapped_end:
            mapped_start = mapped_end

        safe_start = min(mapped_start, len(orig_map) - 1)
        safe_end = min(mapped_end, len(orig_map) - 1)

        real_start = orig_map[safe_start]
        real_end = orig_map[safe_end]

        # 智能标点穿透：将句子末尾的非中文字符（标点）完整包裹
        while real_end + 1 < len(orig_text) and not clean_text(orig_text[real_end + 1]):
            real_end += 1

        best_text = orig_text[real_start:real_end + 1].strip()
        corrected_texts.append(best_text)

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
