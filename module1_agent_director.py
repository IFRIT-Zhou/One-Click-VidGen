# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

from backend.app.indextts25_local import (
    emotion_vector_text,
    load_indextts25_config,
    resolve_voice_reference,
)
from backend.app.qwen_tts import QwenTtsError, detect_language_type, synthesize_to_file
from backend.app.tts_segmentation import (
    INDEXTTS25_SEGMENT_MAX_TOKENS,
    segment_indextts25_text,
)


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "2_audio_srt"
TEMP_ROOT = PROJECT_ROOT / "workspace" / "temp_chunks"
TEMP_DIR = TEMP_ROOT
FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"

os.environ["PYTHONUNBUFFERED"] = "1"

CHUNK_MIN_LEN = 25
CHUNK_TARGET_LEN = 50
CHUNK_SOFT_MAX_LEN = 65
CHUNK_MAX_LEN = 85
# The Qwen endpoint enforces a 600-unit payload ceiling.  Chinese UTF-8 text
# can consume multiple units per displayed character, so measure payload bytes
# (with headroom) rather than using Python character counts.
QWEN_CHUNK_MIN_LEN = 240
QWEN_CHUNK_MAX_LEN = 540
NON_BOUNDARY_WRAPPER_MARKS = frozenset("“”《》<>【】")
PRODUCTION_NOTE_PATTERN = re.compile(
    r"【\s*(?:镜头|画面|场景|制作|剪辑|字幕|音效|配乐|BGM|停顿|留白)[^】]*】",
    flags=re.IGNORECASE,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Module1 Agent Director - official IndexTTS-2.5 batch synthesis"
    )
    parser.add_argument(
        "--text", required=True, help="口播文案 txt 文件路径（绝对或相对路径）"
    )
    parser.add_argument("--job-id", help="关联的生成任务 ID")
    parser.add_argument("--user-id", type=int, help="关联的用户 ID")
    parser.add_argument("--tts-voice-id", help="官方 IndexTTS-2.5 参考音频 ID")
    parser.add_argument("--tts-voice-path", help="重配音时使用的已归档参考音频绝对路径")
    parser.add_argument("--tts-speed", type=float, default=1.0, help="输出语速（0.5-2）")
    parser.add_argument("--tts-volume", type=float, default=1.0, help="输出音量（0.1-10）")
    parser.add_argument("--tts-pitch", type=int, default=0, help="输出音调（-12 到 12）")
    parser.add_argument("--tts-parallelism", type=int, default=2, help="IndexTTS-2.5 并行进程数，建议 1-3")
    parser.add_argument("--tts-emotion", help="IndexTTS-2.5 八维情绪之一")
    parser.add_argument(
        "--tts-engine",
        choices=("indextts25", "qwen"),
        default="indextts25",
    )
    parser.add_argument("--qwen-instructions", default="", help="Qwen3-TTS-Instruct-Flash 配音描述")
    parser.add_argument("--qwen-voice", default="Elias", help="Qwen-TTS 系统音色")
    parser.add_argument(
        "--qwen-optimize-instructions",
        choices=("true", "false"),
        default="false",
        help="是否允许 Qwen 改写配音描述",
    )
    parser.add_argument("--tts-pronunciation", help="兼容旧请求；官方版请直接在文案中使用拼音标注")
    parser.add_argument(
        "--tts-english-normalization",
        choices=("true", "false"),
        help="兼容旧请求；IndexTTS-2.5 原生处理中英文文本",
    )
    parser.add_argument("--chunks-json", help="跳过自动断句，按 JSON 数组中的文本逐句合成")
    parser.add_argument("--output-dir", help="覆盖合并音频与字幕的输出目录")
    parser.add_argument("--segment-archive-dir", help="保留逐句 WAV 与断句清单的目录")
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def clean_text(raw: str) -> str:
    """Remove production notes while preserving spoken parenthetical content."""
    lines = [line for line in raw.split("\n") if "此处留白" not in line]
    return PRODUCTION_NOTE_PATTERN.sub("", "\n".join(lines))


def _split_sentences_preserving_closers(paragraph: str) -> list[str]:
    """Split on true sentence endings and keep closing quotes with the sentence."""
    endings = "。！？!?"
    closers = "”’」』】）)]"
    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(paragraph):
        # Quotes, book-title marks and user-facing brackets are formatting
        # wrappers, never sentence boundaries by themselves.
        if paragraph[index] in NON_BOUNDARY_WRAPPER_MARKS:
            index += 1
            continue
        if paragraph[index] not in endings:
            index += 1
            continue
        end = index + 1
        while end < len(paragraph) and paragraph[end] in closers:
            end += 1
        sentence = paragraph[start:end]
        if sentence.strip():
            sentences.append(sentence)
        start = end
        index = end
    tail = paragraph[start:]
    if tail.strip():
        sentences.append(tail)
    return sentences


def _split_clause_units(sentence: str) -> list[str]:
    """Create weak/medium pause units while preserving their punctuation."""
    boundaries = "；;：:，,"
    units: list[str] = []
    start = 0
    for index, char in enumerate(sentence):
        if char in NON_BOUNDARY_WRAPPER_MARKS:
            continue
        if char not in boundaries:
            continue
        unit = sentence[start:index + 1]
        if unit.strip():
            units.append(unit)
        start = index + 1
    tail = sentence[start:]
    if tail.strip():
        units.append(tail)
    return units


def _safe_hard_split(text: str, target_len: int, hard_max_len: int) -> list[str]:
    """Bound punctuation-free text without cutting through an ASCII word/number."""
    # Keep whitespace exactly as supplied.  It can be semantically meaningful
    # in English/mixed-language narration, and dropping a space at a chunk
    # boundary makes the integrity guard report a false omission.
    remaining = text
    result: list[str] = []
    while len(remaining) > hard_max_len:
        split_at = min(target_len, len(remaining))
        while (
            split_at > 1
            and split_at < len(remaining)
            and remaining[split_at - 1].isascii()
            and remaining[split_at - 1].isalnum()
            and remaining[split_at].isascii()
            and remaining[split_at].isalnum()
        ):
            split_at -= 1
        if split_at <= 1:
            split_at = hard_max_len
        piece = remaining[:split_at]
        if piece:
            result.append(piece)
        remaining = remaining[split_at:]
    if remaining:
        result.append(remaining)
    return result


def _merge_short_chunks(
    chunks: list[str],
    *,
    min_len: int,
    target_len: int,
    hard_max_len: int,
) -> list[str]:
    """Merge a short chunk with the semantically nearest viable neighbor."""
    chunks = [chunk for chunk in chunks if chunk]
    index = 0
    while len(chunks) > 1 and index < len(chunks):
        if len(chunks[index]) >= min_len:
            index += 1
            continue
        candidates: list[tuple[int, str, int]] = []
        if index > 0:
            combined = chunks[index - 1] + chunks[index]
            if len(combined) <= hard_max_len:
                candidates.append((abs(len(combined) - target_len), "previous", index - 1))
        if index + 1 < len(chunks):
            combined = chunks[index] + chunks[index + 1]
            if len(combined) <= hard_max_len:
                candidates.append((abs(len(combined) - target_len), "next", index))
        if not candidates:
            index += 1
            continue
        _, direction, target_index = min(candidates, key=lambda value: (value[0], value[1] != "previous"))
        if direction == "previous":
            chunks[target_index] += chunks[index]
            chunks.pop(index)
            index = max(0, target_index)
        else:
            chunks[index] += chunks[index + 1]
            chunks.pop(index + 1)
    return chunks


def split_cluster_tts_text(
    raw: str,
    *,
    min_len: int = CHUNK_MIN_LEN,
    target_len: int = CHUNK_TARGET_LEN,
    soft_max_len: int = CHUNK_SOFT_MAX_LEN,
    hard_max_len: int = CHUNK_MAX_LEN,
) -> list[str]:
    """Prosody-aware chunks retained for the remote cluster protocol."""
    cleaned = clean_text(raw)
    # Treat line breaks as paragraph boundaries, but preserve meaningful spaces
    # inside English/mixed-language narration.  Collapsing all whitespace here
    # would turn e.g. "One Click VidGen" into "OneClickVidGen" before TTS.
    paragraphs = [re.sub(r"[ \t]+", " ", value).strip() for value in re.split(r"[\r\n]+", cleaned)]
    paragraphs = [value for value in paragraphs if value]
    all_chunks: list[str] = []
    for paragraph in paragraphs:
        paragraph_chunks: list[str] = []
        for sentence in _split_sentences_preserving_closers(paragraph):
            if len(sentence) <= soft_max_len:
                paragraph_chunks.append(sentence)
                continue
            units: list[str] = []
            for unit in _split_clause_units(sentence):
                if len(unit) > hard_max_len:
                    units.extend(_safe_hard_split(unit, target_len, hard_max_len))
                else:
                    units.append(unit)
            current = ""
            for unit in units:
                candidate = current + unit
                if not current or len(candidate) <= soft_max_len:
                    current = candidate
                elif len(current) < min_len and len(candidate) <= hard_max_len:
                    current = candidate
                else:
                    paragraph_chunks.append(current)
                    current = unit
            if current:
                paragraph_chunks.append(current)
        paragraph_chunks = _merge_short_chunks(
            paragraph_chunks,
            min_len=min_len,
            target_len=target_len,
            hard_max_len=hard_max_len,
        )
        all_chunks.extend(paragraph_chunks)

    expected = "".join(paragraphs)
    if "".join(all_chunks) != expected:
        raise RuntimeError("集群配音断句完整性检查失败：断句结果未能完整覆盖文案")
    if any(len(chunk) > hard_max_len for chunk in all_chunks):
        raise RuntimeError("集群配音断句失败：仍存在超过绝对上限的片段")
    return all_chunks


def step1_indextts25_voice_agent_slicing(text_path: Path) -> list[str]:
    """Use the 2.5-only voice segmentation Agent with a Python hard guard."""
    print("[Step 1] IndexTTS-2.5 配音断句 Agent...", flush=True)
    text = clean_text(text_path.read_text(encoding="utf-8")).strip()
    if not text:
        raise ValueError("IndexTTS-2.5 配音文案为空")
    chunks, source, total_tokens = segment_indextts25_text(text)
    source_label = {
        "short_text": "110 token 内，跳过 Agent",
        "voice_segmentation_agent": "配音断句 Agent",
        "python_fallback": "本地 Python 安全兜底",
    }.get(source, source)
    print(
        f"  -> 原文 {total_tokens} token，生成 {len(chunks)} 个配音段落；"
        f"来源：{source_label}；绝对上限 {INDEXTTS25_SEGMENT_MAX_TOKENS} token",
        flush=True,
    )
    for index, chunk in enumerate(chunks, 1):
        preview = re.sub(r"\s+", " ", chunk).strip()
        print(f"  -> 第 {index} 段：{preview[:42]}{'…' if len(preview) > 42 else ''}", flush=True)
    return chunks


def step1_indextts25_raw_input(text_path: Path) -> list[str]:
    """Backward-compatible alias retained for older callers and tests."""
    return step1_indextts25_voice_agent_slicing(text_path)


def step1_dynamic_chunk_slicing(
    text_path: Path,
    *,
    min_len: int = CHUNK_MIN_LEN,
    max_len: int = CHUNK_MAX_LEN,
    measure: Callable[[str], int] = len,
) -> list[str]:
    print("[Step 1] Dynamic chunk slicing...", flush=True)
    cleaned = clean_text(text_path.read_text(encoding="utf-8"))
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    raw_segments = re.split(r"(?<=[。，！？\n])", cleaned)
    raw_segments = [segment.strip() for segment in raw_segments if segment.strip()]

    chunks: list[str] = []
    buffer = ""
    for segment in raw_segments:
        candidate = buffer + segment
        if measure(candidate) <= max_len:
            buffer = candidate
        else:
            if buffer.strip():
                chunks.append(buffer)
            if max_len == CHUNK_MAX_LEN:
                # Preserve the established character-count fallback behavior.
                buffer = segment
            else:
                # A punctuation-free paragraph can still exceed the Qwen safe limit.
                while measure(segment) > max_len:
                    split_at = len(segment)
                    while split_at > 1 and measure(segment[:split_at]) > max_len:
                        split_at -= 1
                    chunks.append(segment[:split_at])
                    segment = segment[split_at:]
                buffer = segment
    if buffer.strip():
        chunks.append(buffer)

    index = 0
    while index < len(chunks):
        if measure(chunks[index]) < min_len and index + 1 < len(chunks):
            chunks[index] += chunks[index + 1]
            chunks.pop(index + 1)
        else:
            index += 1

    if len(chunks) > 1 and measure(chunks[-1]) < min_len:
        combined = chunks[-2] + chunks[-1]
        if measure(combined) <= max_len:
            chunks[-2] = combined
            chunks.pop()

    print(f"  -> Produced {len(chunks)} chunks (no batch cap)", flush=True)
    return chunks


def format_srt(index: int, start: float, end: float, text: str) -> str:
    def fmt_time(value: float) -> str:
        hours = int(value // 3600)
        minutes = int((value % 3600) // 60)
        seconds = int(value % 60)
        millis = int((value - int(value)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    return f"{index}\n{fmt_time(start)} --> {fmt_time(end)}\n{text}\n"


def get_wav_duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def set_runtime_temp_dir(job_id: str | None) -> None:
    global TEMP_DIR
    safe_id = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        job_id or f"manual_{os.getpid()}",
    ).strip("._")
    TEMP_DIR = TEMP_ROOT / (safe_id or f"manual_{os.getpid()}")


def clear_temp_chunks() -> None:
    if not TEMP_DIR.is_dir():
        return
    for path in TEMP_DIR.iterdir():
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def _run_and_stream(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str | None = None,
    on_generated: Callable[[], None] | None = None,
) -> None:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    diagnostic_tail: list[str] = []
    ansi_escape = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    for raw_line in process.stdout:
        for part in re.split(r"[\r\n]+", ansi_escape.sub("", raw_line)):
            line = part.strip()
            if not line:
                continue
            diagnostic_tail.append(line)
            diagnostic_tail = diagnostic_tail[-20:]
            if line.startswith("Generated:"):
                if on_generated is not None:
                    on_generated()
                continue
            lowered = line.lower()
            if (
                line.startswith("ERROR:")
                or line.startswith("Traceback")
                or "cuda out of memory" in lowered
                or "exception" in lowered
                or "fatal" in lowered
            ):
                prefix = f"[{label}] " if label else ""
                print(prefix + line, flush=True)
    return_code = process.wait()
    if return_code != 0:
        useful_tail = [
            line for line in diagnostic_tail
            if "%|" not in line and "it/s" not in line and not re.search(r"\d+/\d+\s*\[", line)
        ]
        if useful_tail:
            prefix = f"[{label}] " if label else ""
            print(prefix + "IndexTTS-2.5 错误摘要：" + " | ".join(useful_tail[-4:])[:800], flush=True)
        raise RuntimeError(f"官方 IndexTTS-2.5 批处理退出码: {return_code}")


def _watch_generated_wavs(
    items: list[tuple[int, str, Path]],
    report_generated: Callable[[int, str], None],
    stop_event: threading.Event,
    *,
    poll_seconds: float = 0.4,
) -> None:
    """Report completed batch items from stable WAV files, independent of CLI buffering."""
    baseline: dict[Path, tuple[int, int]] = {}
    for _, _, path in items:
        try:
            stat = path.stat()
            baseline[path] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue

    observed: dict[Path, tuple[tuple[int, int], int]] = {}
    reported: set[Path] = set()
    while not stop_event.is_set():
        for original_index, text, path in items:
            if path in reported:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            if stat.st_size <= 44 or signature == baseline.get(path):
                continue
            previous_signature, stable_checks = observed.get(path, ((-1, -1), 0))
            stable_checks = stable_checks + 1 if signature == previous_signature else 1
            observed[path] = (signature, stable_checks)
            if stable_checks >= 2:
                reported.add(path)
                report_generated(original_index, text)
        stop_event.wait(max(0.1, poll_seconds))

    # Once the child has exited, every non-empty changed WAV is complete.
    for original_index, text, path in items:
        if path in reported:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        signature = (stat.st_size, stat.st_mtime_ns)
        if stat.st_size > 44 and signature != baseline.get(path):
            report_generated(original_index, text)


def _write_manifest(path: Path, chunks: list[str]) -> None:
    path.write_text(
        "\n".join(json.dumps({"text": chunk}, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )


def _build_indextts25_command(
    config,
    *,
    manifest: Path,
    output_dir: Path,
    output_prefix: str,
    voice_path: Path,
    emotion_vector: str | None,
    speed: float = 1.0,
) -> list[str]:
    duration_factor = 1.0 / min(2.0, max(0.5, float(speed)))
    command = [
        str(config.python),
        "-I",
        str(PROJECT_ROOT / "backend" / "app" / "indextts25_runner.py"),
        "--batch-file",
        str(manifest),
        "--output-dir",
        str(output_dir),
        "--output-prefix",
        output_prefix,
        "--voice",
        str(voice_path),
        "--device",
        config.device,
        "--lang",
        config.language,
        "--duration-factor",
        f"{duration_factor:.8f}",
        "--bf16" if config.use_bf16 else "--no-bf16",
        "--accel" if config.use_accel else "--no-accel",
        "--torch-compile" if config.use_torch_compile else "--no-torch-compile",
    ]
    if emotion_vector:
        command.extend(
            [
                "--emotion-vector",
                emotion_vector,
                "--emotion-weight",
                str(config.emotion_weight),
            ]
        )
    return command


def _apply_audio_controls(path: Path, *, speed: float, volume: float, pitch: int) -> None:
    speed = min(2.0, max(0.5, float(speed)))
    volume = min(10.0, max(0.1, float(volume)))
    pitch = min(12, max(-12, int(pitch)))
    if math.isclose(speed, 1.0) and math.isclose(volume, 1.0) and pitch == 0:
        return
    if not FFMPEG.is_file():
        raise FileNotFoundError(f"找不到 FFmpeg: {FFMPEG}")

    filters: list[str] = []
    if pitch:
        with wave.open(str(path), "rb") as audio:
            sample_rate = audio.getframerate()
        factor = 2 ** (pitch / 12)
        filters.extend(
            [
                f"asetrate={sample_rate}*{factor:.8f}",
                f"aresample={sample_rate}",
                f"atempo={1 / factor:.8f}",
            ]
        )
    if not math.isclose(speed, 1.0):
        filters.append(f"atempo={speed:.8f}")
    if not math.isclose(volume, 1.0):
        filters.append(f"volume={volume:.8f}")

    adjusted = path.with_name(f"{path.stem}.adjusted.wav")
    result = subprocess.run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-filter:a",
            ",".join(filters),
            "-c:a",
            "pcm_s16le",
            str(adjusted),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not adjusted.is_file():
        adjusted.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg 音频参数处理失败: {result.stderr.strip()}")
    os.replace(adjusted, path)


def step2_indextts25_synthesize(chunks: list[str], args) -> tuple[list[Path], list[str]]:
    config = load_indextts25_config()
    missing = config.missing_resources()
    if missing:
        raise RuntimeError("官方 IndexTTS-2.5 未就绪: " + ", ".join(missing))
    if not chunks:
        raise RuntimeError("清洗和断句后没有可合成的文案")

    ensure_dir(TEMP_DIR)
    explicit_voice_path = Path(str(getattr(args, "tts_voice_path", "") or "")).resolve()
    if str(getattr(args, "tts_voice_path", "") or "").strip():
        if not explicit_voice_path.is_file() or explicit_voice_path.suffix.lower() not in {".wav", ".mp3", ".flac"}:
            raise FileNotFoundError(f"找不到有效的重配音参考音频: {explicit_voice_path}")
        voice_path = explicit_voice_path
    else:
        voice_path = resolve_voice_reference(config, args.tts_voice_id, user_id=args.user_id)
    emotion_vector = emotion_vector_text(args.tts_emotion)

    env = os.environ.copy()
    env.update(config.runtime_environment())
    # The official CLI prints one Generated line per WAV without flush=True.
    # Force unbuffered child output while the file watcher remains the fallback.
    env["PYTHONUNBUFFERED"] = "1"
    parallelism = min(3, max(1, int(getattr(args, "tts_parallelism", 1) or 1)))
    parallelism = min(parallelism, len(chunks))
    print(
        f"[TTS] 开始配音：共 {len(chunks)} 句，并行数 {parallelism}，"
        "引擎 IndexTTS-2.5，"
        f"音色 {voice_path.name}，设备 {config.device}",
        flush=True,
    )
    progress_lock = threading.Lock()
    completed_count = 0
    reported_indices: set[int] = set()
    synthesis_started_at = time.monotonic()
    heartbeat_stop = threading.Event()

    def report_generated(original_index: int, text: str) -> None:
        nonlocal completed_count
        with progress_lock:
            if original_index in reported_indices:
                return
            reported_indices.add(original_index)
            completed_count += 1
            completed = completed_count
        preview = re.sub(r"\s+", " ", text).strip()
        if len(preview) > 56:
            preview = preview[:56] + "…"
        print(
            f"[TTS_PROGRESS] 配音进度 {completed}/{len(chunks)}："
            f"原文第 {original_index + 1} 句已生成｜{preview}",
            flush=True,
        )

    def generated_callback(items: list[tuple[int, str]]) -> Callable[[], None]:
        local_index = 0

        def callback() -> None:
            nonlocal local_index
            if local_index >= len(items):
                return
            original_index, text = items[local_index]
            local_index += 1
            report_generated(original_index, text)

        return callback

    def run_with_file_progress(
        command: list[str],
        items: list[tuple[int, str]],
        paths: list[Path],
        *,
        label: str | None = None,
    ) -> None:
        watcher_stop = threading.Event()
        watched_items = [
            (original_index, text, path)
            for (original_index, text), path in zip(items, paths)
        ]
        watcher = threading.Thread(
            target=_watch_generated_wavs,
            args=(watched_items, report_generated, watcher_stop),
            daemon=True,
        )
        watcher.start()
        try:
            _run_and_stream(
                command,
                cwd=config.root,
                env=env,
                label=label,
                on_generated=generated_callback(items),
            )
        finally:
            watcher_stop.set()
            watcher.join(timeout=2)

    def report_heartbeat() -> None:
        """Keep long GPU inference visible without forwarding noisy model bars."""
        while not heartbeat_stop.wait(45):
            with progress_lock:
                completed = completed_count
            elapsed = max(0, round(time.monotonic() - synthesis_started_at))
            minutes, seconds = divmod(elapsed, 60)
            print(
                f"[TTS_HEARTBEAT] 正在生成：已完成 {completed}/{len(chunks)} 句，"
                f"并行 {parallelism}，已运行 {minutes}分{seconds:02d}秒；"
                "IndexTTS-2.5 正在进行 GPU 推理，请耐心等待下一句完成。",
                flush=True,
            )

    heartbeat_thread = threading.Thread(target=report_heartbeat, daemon=True)
    heartbeat_thread.start()

    try:
        if parallelism == 1:
            manifest = TEMP_DIR / "indextts25_batch.jsonl"
            _write_manifest(manifest, chunks)
            command = _build_indextts25_command(
                config,
                manifest=manifest,
                output_dir=TEMP_DIR,
                output_prefix="chunk",
                voice_path=voice_path,
                emotion_vector=emotion_vector,
                speed=args.tts_speed,
            )
            single_items = list(enumerate(chunks))
            wav_paths = [TEMP_DIR / f"chunk-{index:04d}.wav" for index in range(1, len(chunks) + 1)]
            run_with_file_progress(command, single_items, wav_paths)
        else:
            assignments: list[list[tuple[int, str]]] = [[] for _ in range(parallelism)]
            for index, chunk in enumerate(chunks):
                assignments[index % parallelism].append((index, chunk))

            wav_paths = [TEMP_DIR / f"chunk-missing-{index:04d}.wav" for index in range(1, len(chunks) + 1)]

            def run_worker(worker_index: int, items: list[tuple[int, str]]) -> list[tuple[int, Path]]:
                worker_id = worker_index + 1
                worker_dir = TEMP_DIR / f"worker_{worker_id}"
                ensure_dir(worker_dir)
                manifest = worker_dir / "indextts25_batch.jsonl"
                _write_manifest(manifest, [chunk for _, chunk in items])
                output_prefix = f"chunk-w{worker_id}"
                command = _build_indextts25_command(
                    config,
                    manifest=manifest,
                    output_dir=worker_dir,
                    output_prefix=output_prefix,
                    voice_path=voice_path,
                    emotion_vector=emotion_vector,
                    speed=args.tts_speed,
                )
                worker_outputs = [
                    (original_index, worker_dir / f"{output_prefix}-{local_index:04d}.wav")
                    for local_index, (original_index, _) in enumerate(items, 1)
                ]
                run_with_file_progress(
                    command,
                    items,
                    [path for _, path in worker_outputs],
                    label=f"TTS-{worker_id}",
                )
                return worker_outputs

            with ThreadPoolExecutor(max_workers=parallelism) as executor:
                futures = [
                    executor.submit(run_worker, worker_index, items)
                    for worker_index, items in enumerate(assignments)
                    if items
                ]
                for future in as_completed(futures):
                    for original_index, path in future.result():
                        wav_paths[original_index] = path
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
    missing_outputs = [path.name for path in wav_paths if not path.is_file()]
    if missing_outputs:
        raise RuntimeError("IndexTTS-2.5 未生成完整批次: " + ", ".join(missing_outputs[:10]))

    current_time = 0.0
    srt_entries: list[str] = []
    for index, (chunk, wav_path) in enumerate(zip(chunks, wav_paths), 1):
        _apply_audio_controls(
            wav_path,
            speed=1.0,
            volume=args.tts_volume,
            pitch=args.tts_pitch,
        )
        duration = get_wav_duration(wav_path)
        srt_entries.append(format_srt(index, current_time, current_time + duration, chunk))
        current_time += duration
    print(f"[TTS] {len(chunks)} 句配音全部生成，正在合并音频与字幕", flush=True)
    return wav_paths, srt_entries


def _normalize_qwen_audio(source: Path, destination: Path) -> None:
    """Normalize Qwen audio and remove only the synthetic leading silence.

    Do not use a trailing silenceremove stage here: natural sentence pauses in
    a long cloud response are indistinguishable from the final tail to FFmpeg.
    """
    if not FFMPEG.is_file():
        raise FileNotFoundError(f"找不到 FFmpeg: {FFMPEG}")
    result = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-af", "silenceremove=start_periods=1:start_duration=0.10:start_threshold=-45dB:stop_periods=0",
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not destination.is_file():
        raise RuntimeError(f"Qwen-TTS 音频转码失败: {result.stderr.strip()[:500]}")


def _normalize_qwen_loudness(path: Path) -> None:
    """Normalize each independent cloud chunk to a consistent narration level."""
    adjusted = path.with_name(f"{path.stem}.loudnorm.wav")
    result = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
            "-filter:a", "loudnorm=I=-18:TP=-2:LRA=7:linear=true",
            "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(adjusted),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not adjusted.is_file():
        adjusted.unlink(missing_ok=True)
        raise RuntimeError(f"Qwen-TTS 响度归一化失败: {result.stderr.strip()[:500]}")
    os.replace(adjusted, path)


def step2_qwen_synthesize(chunks: list[str], args) -> tuple[list[Path], list[str]]:
    if not chunks:
        raise RuntimeError("清洗和断句后没有可合成的文案")
    if not os.getenv("DASHSCOPE_API_KEY", "").strip():
        raise RuntimeError("未配置 DASHSCOPE_API_KEY；请在 Qwen-TTS 面板保存 API Key 后重试")

    ensure_dir(TEMP_DIR)
    # Qwen requests are stateless.  Run each longer chunk in order, rather than
    # interleaving independent cloud generations, so every call receives one
    # immutable voice profile and the output is easier to diagnose/retry.
    parallelism = 1
    instructions = str(getattr(args, "qwen_instructions", "") or "").strip()
    # Rewriting instructions independently per request is a direct source of
    # prosody drift.  The app therefore sends the user's description verbatim.
    optimize_instructions = False
    language_type = detect_language_type("\n".join(chunks))
    print(
        f"[QWEN_TTS] 开始云端配音：共 {len(chunks)} 句，并行数 {parallelism}，"
        f"模型 {'qwen3-tts-instruct-flash' if instructions else 'qwen3-tts-flash'}，音色 {args.qwen_voice}，语言 {language_type}",
        flush=True,
    )
    progress_lock = threading.Lock()
    completed_count = 0

    def synthesize_one(index: int, chunk: str) -> tuple[int, Path]:
        nonlocal completed_count
        source = TEMP_DIR / f"qwen-source-{index + 1:04d}.wav"
        target = TEMP_DIR / f"chunk-qwen-{index + 1:04d}.wav"
        try:
            synthesize_to_file(
                text=chunk,
                destination=source,
                instructions=instructions,
                voice=args.qwen_voice,
                language_type=language_type,
                optimize_instructions=optimize_instructions,
            )
            _normalize_qwen_audio(source, target)
            _normalize_qwen_loudness(target)
        finally:
            source.unlink(missing_ok=True)
        with progress_lock:
            completed_count += 1
            completed = completed_count
        preview = re.sub(r"\s+", " ", chunk).strip()
        print(f"[QWEN_TTS_PROGRESS] 配音进度 {completed}/{len(chunks)}：原文第 {index + 1} 句已生成｜{preview[:56]}", flush=True)
        return index, target

    wav_paths: list[Path] = [TEMP_DIR / f"missing-{index:04d}.wav" for index in range(1, len(chunks) + 1)]
    try:
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = [executor.submit(synthesize_one, index, chunk) for index, chunk in enumerate(chunks)]
            for future in as_completed(futures):
                index, path = future.result()
                wav_paths[index] = path
    except QwenTtsError as exc:
        raise RuntimeError(f"Qwen-TTS 合成失败: {exc}") from exc

    missing_outputs = [path.name for path in wav_paths if not path.is_file()]
    if missing_outputs:
        raise RuntimeError("Qwen-TTS 未生成完整批次: " + ", ".join(missing_outputs[:10]))
    current_time = 0.0
    srt_entries: list[str] = []
    for index, (chunk, wav_path) in enumerate(zip(chunks, wav_paths), 1):
        _apply_audio_controls(wav_path, speed=args.tts_speed, volume=args.tts_volume, pitch=args.tts_pitch)
        duration = get_wav_duration(wav_path)
        srt_entries.append(format_srt(index, current_time, current_time + duration, chunk))
        current_time += duration
    print(f"[QWEN_TTS] {len(chunks)} 句云端配音已下载，正在合并音频与字幕", flush=True)
    return wav_paths, srt_entries


def export_tts_segments(
    archive_dir: Path,
    chunks: list[str],
    wav_paths: list[Path],
    args,
) -> None:
    """Persist exact per-request WAVs so completed projects can selectively regenerate speech."""
    shutil.rmtree(archive_dir, ignore_errors=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    current_time = 0.0
    items: list[dict[str, object]] = []
    for index, (chunk, source) in enumerate(zip(chunks, wav_paths), 1):
        filename = f"segment_{index:04d}.wav"
        target = archive_dir / filename
        shutil.copy2(source, target)
        duration = get_wav_duration(target)
        items.append({
            "index": index,
            "text": chunk,
            "filename": filename,
            "start": round(current_time, 6),
            "end": round(current_time + duration, 6),
            "duration": round(duration, 6),
        })
        current_time += duration
    manifest = {
        "schema_version": 1,
        "engine": str(getattr(args, "tts_engine", "indextts25") or "indextts25"),
        "tts_voice_id": str(getattr(args, "tts_voice_id", "") or ""),
        "tts_speed": float(getattr(args, "tts_speed", 1) or 1),
        "tts_volume": float(getattr(args, "tts_volume", 1) or 1),
        "tts_pitch": int(getattr(args, "tts_pitch", 0) or 0),
        "tts_emotion": str(getattr(args, "tts_emotion", "") or ""),
        "qwen_voice": str(getattr(args, "qwen_voice", "") or ""),
        "qwen_instructions": str(getattr(args, "qwen_instructions", "") or ""),
        "total_duration": round(current_time, 6),
        "segments": items,
    }
    (archive_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  -> Per-sentence TTS archive: {archive_dir}", flush=True)


def step3_finalize(
    wav_paths: list[Path],
    srt_entries: list[str],
    chunks: list[str] | None = None,
    args=None,
) -> None:
    print("[Step 3] Finalizing output...", flush=True)
    ensure_dir(OUTPUT_DIR)
    output_wav = OUTPUT_DIR / "final_output.wav"
    with wave.open(str(wav_paths[0]), "rb") as first:
        params = first.getparams()
        expected_format = (params.nchannels, params.sampwidth, params.framerate)
    with wave.open(str(output_wav), "wb") as output:
        output.setparams(params)
        for path in wav_paths:
            with wave.open(str(path), "rb") as audio:
                actual_format = (
                    audio.getnchannels(),
                    audio.getsampwidth(),
                    audio.getframerate(),
                )
                if actual_format != expected_format:
                    raise RuntimeError(f"批次 WAV 格式不一致: {path.name}")
                output.writeframes(audio.readframes(audio.getnframes()))
    print(f"  -> WAV written: {output_wav}", flush=True)

    output_srt = OUTPUT_DIR / "final_output.srt"
    output_srt.write_text("\n".join(srt_entries) + "\n", encoding="utf-8")
    print(f"  -> SRT written: {output_srt}", flush=True)
    segment_archive = str(getattr(args, "segment_archive_dir", "") or "").strip() if args is not None else ""
    if segment_archive and chunks is not None:
        export_tts_segments(Path(segment_archive).resolve(), chunks, wav_paths, args)


def step4_cleanup() -> None:
    print("[Step 4] Cleaning up temp files...", flush=True)
    if TEMP_DIR.is_dir():
        shutil.rmtree(TEMP_DIR)
    print("  -> Cleanup done.", flush=True)


def main() -> None:
    global OUTPUT_DIR
    args = parse_args()
    if str(args.output_dir or "").strip():
        OUTPUT_DIR = Path(args.output_dir).resolve()
    text_path = Path(args.text)
    if not text_path.is_absolute():
        text_path = PROJECT_ROOT / text_path
    if not text_path.is_file():
        sys.exit(f"【路径错误】找不到文件：{text_path}")

    print("=" * 60, flush=True)
    engine_label = {
        "qwen": "Qwen-TTS Cloud Pipeline",
        "indextts25": "Official IndexTTS-2.5 Pipeline",
    }.get(args.tts_engine, "Official IndexTTS-2.5 Pipeline")
    print(f"Module 1 -- {engine_label} Start", flush=True)
    print("=" * 60, flush=True)
    print(f"  文案路径: {text_path}", flush=True)

    set_runtime_temp_dir(args.job_id)
    clear_temp_chunks()
    print(f"  临时音频目录: {TEMP_DIR}", flush=True)

    try:
        if str(args.chunks_json or "").strip():
            raw_chunks = json.loads(Path(args.chunks_json).read_text(encoding="utf-8"))
            if not isinstance(raw_chunks, list) or not raw_chunks:
                raise ValueError("chunks-json 必须是非空文本数组")
            chunks = [str(value).strip() for value in raw_chunks if str(value).strip()]
            if not chunks:
                raise ValueError("chunks-json 中没有可配音文本")
            print(f"[Step 1] Using {len(chunks)} preserved TTS chunks...", flush=True)
        elif args.tts_engine == "qwen":
            chunks = step1_dynamic_chunk_slicing(
                text_path,
                min_len=QWEN_CHUNK_MIN_LEN,
                max_len=QWEN_CHUNK_MAX_LEN,
                measure=lambda value: len(value.encode("utf-8")),
            )
        elif args.tts_engine == "indextts25":
            chunks = step1_indextts25_voice_agent_slicing(text_path)
        else:
            raise ValueError(f"不支持的配音引擎: {args.tts_engine}")
        if args.tts_engine == "qwen":
            wav_paths, srt_entries = step2_qwen_synthesize(chunks, args)
        else:
            wav_paths, srt_entries = step2_indextts25_synthesize(chunks, args)
        step3_finalize(wav_paths, srt_entries, chunks, args)
    except Exception as exc:
        clear_temp_chunks()
        sys.exit(f"【致命错误】{engine_label} 生成失败：{type(exc).__name__}: {exc}")

    step4_cleanup()
    print("=" * 60, flush=True)
    print(f"{engine_label} 与 SRT 批量合成完成。", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
