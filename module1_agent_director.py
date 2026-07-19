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
import wave
from collections.abc import Callable
from pathlib import Path

from backend.app.indextts2_local import (
    emotion_vector_text,
    load_indextts2_config,
    resolve_voice_reference,
)
from backend.app.qwen_tts import QwenTtsError, detect_language_type, synthesize_to_file


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "2_audio_srt"
TEMP_ROOT = PROJECT_ROOT / "workspace" / "temp_chunks"
TEMP_DIR = TEMP_ROOT
FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"

os.environ["PYTHONUNBUFFERED"] = "1"

CHUNK_MIN_LEN = 15
CHUNK_MAX_LEN = 50
# The Qwen endpoint enforces a 600-unit payload ceiling.  Chinese UTF-8 text
# can consume multiple units per displayed character, so measure payload bytes
# (with headroom) rather than using Python character counts.
QWEN_CHUNK_MIN_LEN = 240
QWEN_CHUNK_MAX_LEN = 540


def parse_args():
    parser = argparse.ArgumentParser(
        description="Module1 Agent Director - official IndexTTS2 batch synthesis"
    )
    parser.add_argument(
        "--text", required=True, help="口播文案 txt 文件路径（绝对或相对路径）"
    )
    parser.add_argument("--job-id", help="关联的生成任务 ID")
    parser.add_argument("--user-id", type=int, help="关联的用户 ID")
    parser.add_argument("--tts-voice-id", help="官方 IndexTTS2 参考音频 ID")
    parser.add_argument("--tts-speed", type=float, default=1.0, help="输出语速（0.5-2）")
    parser.add_argument("--tts-volume", type=float, default=1.0, help="输出音量（0.1-10）")
    parser.add_argument("--tts-pitch", type=int, default=0, help="输出音调（-12 到 12）")
    parser.add_argument("--tts-parallelism", type=int, default=2, help="IndexTTS2 并行进程数，建议 1-3")
    parser.add_argument("--tts-emotion", help="IndexTTS2 八维情绪之一")
    parser.add_argument("--tts-engine", choices=("indextts2", "qwen"), default="indextts2")
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
        help="兼容旧请求；IndexTTS2 原生处理中英文文本",
    )
    return parser.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def clean_text(raw: str) -> str:
    """Remove production notes while preserving spoken parenthetical content."""
    lines = [line for line in raw.split("\n") if "此处留白" not in line]
    return re.sub(r"【[^】]*】", "", "\n".join(lines))


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
                # Preserve the legacy IndexTTS2 behavior exactly.
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
            print(prefix + "IndexTTS2 错误摘要：" + " | ".join(useful_tail[-4:])[:800], flush=True)
        raise RuntimeError(f"官方 IndexTTS2 批处理退出码: {return_code}")


def _write_manifest(path: Path, chunks: list[str]) -> None:
    path.write_text(
        "\n".join(json.dumps({"text": chunk}, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )


def _build_indextts2_command(
    config,
    *,
    manifest: Path,
    output_dir: Path,
    output_prefix: str,
    voice_path: Path,
    emotion_vector: str | None,
) -> list[str]:
    command = [
        str(config.python),
        "-I",
        "-m",
        "indextts.cli_v2",
        "batch",
        "--batch-file",
        str(manifest),
        "--model-dir",
        str(config.model_dir),
        "--output-dir",
        str(output_dir),
        "--output-prefix",
        output_prefix,
        "--voice",
        str(voice_path),
        "--device",
        config.device,
        "--fp16" if config.use_fp16 else "--no-fp16",
        "--no-deepspeed",
        "--no-cuda-kernel",
        "--no-accel",
        "--no-torch-compile",
        "--force",
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


def step2_indextts2_synthesize(chunks: list[str], args) -> tuple[list[Path], list[str]]:
    config = load_indextts2_config()
    missing = config.missing_resources()
    if missing:
        raise RuntimeError("官方 IndexTTS2 未就绪: " + ", ".join(missing))
    if not chunks:
        raise RuntimeError("清洗和断句后没有可合成的文案")

    ensure_dir(TEMP_DIR)
    voice_path = resolve_voice_reference(config, args.tts_voice_id, user_id=args.user_id)
    emotion_vector = emotion_vector_text(args.tts_emotion)

    env = os.environ.copy()
    env.update(config.runtime_environment())
    parallelism = min(3, max(1, int(getattr(args, "tts_parallelism", 1) or 1)))
    parallelism = min(parallelism, len(chunks))
    print(
        f"[TTS] 开始配音：共 {len(chunks)} 句，并行数 {parallelism}，"
        f"音色 {voice_path.name}，设备 {config.device}",
        flush=True,
    )
    progress_lock = threading.Lock()
    completed_count = 0

    def report_generated(original_index: int, text: str) -> None:
        nonlocal completed_count
        with progress_lock:
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

    if parallelism == 1:
        manifest = TEMP_DIR / "indextts2_batch.jsonl"
        _write_manifest(manifest, chunks)
        command = _build_indextts2_command(
            config,
            manifest=manifest,
            output_dir=TEMP_DIR,
            output_prefix="chunk",
            voice_path=voice_path,
            emotion_vector=emotion_vector,
        )
        single_items = list(enumerate(chunks))
        _run_and_stream(
            command,
            cwd=config.root,
            env=env,
            on_generated=generated_callback(single_items),
        )
        wav_paths = [TEMP_DIR / f"chunk-{index:04d}.wav" for index in range(1, len(chunks) + 1)]
    else:
        assignments: list[list[tuple[int, str]]] = [[] for _ in range(parallelism)]
        for index, chunk in enumerate(chunks):
            assignments[index % parallelism].append((index, chunk))

        wav_paths = [TEMP_DIR / f"chunk-missing-{index:04d}.wav" for index in range(1, len(chunks) + 1)]

        def run_worker(worker_index: int, items: list[tuple[int, str]]) -> list[tuple[int, Path]]:
            worker_id = worker_index + 1
            worker_dir = TEMP_DIR / f"worker_{worker_id}"
            ensure_dir(worker_dir)
            manifest = worker_dir / "indextts2_batch.jsonl"
            _write_manifest(manifest, [chunk for _, chunk in items])
            output_prefix = f"chunk-w{worker_id}"
            command = _build_indextts2_command(
                config,
                manifest=manifest,
                output_dir=worker_dir,
                output_prefix=output_prefix,
                voice_path=voice_path,
                emotion_vector=emotion_vector,
            )
            _run_and_stream(
                command,
                cwd=config.root,
                env=env,
                label=f"TTS-{worker_id}",
                on_generated=generated_callback(items),
            )
            return [
                (original_index, worker_dir / f"{output_prefix}-{local_index:04d}.wav")
                for local_index, (original_index, _) in enumerate(items, 1)
            ]

        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = [
                executor.submit(run_worker, worker_index, items)
                for worker_index, items in enumerate(assignments)
                if items
            ]
            for future in as_completed(futures):
                for original_index, path in future.result():
                    wav_paths[original_index] = path
    missing_outputs = [path.name for path in wav_paths if not path.is_file()]
    if missing_outputs:
        raise RuntimeError("IndexTTS2 未生成完整批次: " + ", ".join(missing_outputs[:10]))

    current_time = 0.0
    srt_entries: list[str] = []
    for index, (chunk, wav_path) in enumerate(zip(chunks, wav_paths), 1):
        _apply_audio_controls(
            wav_path,
            speed=args.tts_speed,
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


def step3_finalize(wav_paths: list[Path], srt_entries: list[str]) -> None:
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


def step4_cleanup() -> None:
    print("[Step 4] Cleaning up temp files...", flush=True)
    if TEMP_DIR.is_dir():
        shutil.rmtree(TEMP_DIR)
    print("  -> Cleanup done.", flush=True)


def main() -> None:
    args = parse_args()
    text_path = Path(args.text)
    if not text_path.is_absolute():
        text_path = PROJECT_ROOT / text_path
    if not text_path.is_file():
        sys.exit(f"【路径错误】找不到文件：{text_path}")

    print("=" * 60, flush=True)
    engine_label = "Qwen-TTS Cloud Pipeline" if args.tts_engine == "qwen" else "Official IndexTTS2 Pipeline"
    print(f"Module 1 -- {engine_label} Start", flush=True)
    print("=" * 60, flush=True)
    print(f"  文案路径: {text_path}", flush=True)

    set_runtime_temp_dir(args.job_id)
    clear_temp_chunks()
    print(f"  临时音频目录: {TEMP_DIR}", flush=True)

    try:
        if args.tts_engine == "qwen":
            chunks = step1_dynamic_chunk_slicing(
                text_path,
                min_len=QWEN_CHUNK_MIN_LEN,
                max_len=QWEN_CHUNK_MAX_LEN,
                measure=lambda value: len(value.encode("utf-8")),
            )
        else:
            chunks = step1_dynamic_chunk_slicing(text_path)
        if args.tts_engine == "qwen":
            wav_paths, srt_entries = step2_qwen_synthesize(chunks, args)
        else:
            wav_paths, srt_entries = step2_indextts2_synthesize(chunks, args)
        step3_finalize(wav_paths, srt_entries)
    except Exception as exc:
        clear_temp_chunks()
        sys.exit(f"【致命错误】{engine_label} 生成失败：{type(exc).__name__}: {exc}")

    step4_cleanup()
    print("=" * 60, flush=True)
    print(f"{engine_label} 与 SRT 批量合成完成。", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
