import argparse
import os
import re
import sys
import wave
from dataclasses import replace

from backend.app.db import record_media_asset
from backend.app.runninghub_tts import (
    RunningHubTTSError,
    load_runninghub_tts_config,
    synthesize_runninghub_to_wav,
)


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "workspace", "2_audio_srt")
TEMP_ROOT = os.path.join(PROJECT_ROOT, "workspace", "temp_chunks")
TEMP_DIR = TEMP_ROOT

os.environ["PYTHONUNBUFFERED"] = "1"

CHUNK_MIN_LEN = 15
CHUNK_MAX_LEN = 50
MAX_TEST_CHUNKS = 999


def parse_args():
    parser = argparse.ArgumentParser(
        description="Module1 Agent Director - RunningHub TTS 合成与字幕生成"
    )
    parser.add_argument(
        "--text", required=True, help="口播文案 txt 文件路径（绝对或相对路径）"
    )
    parser.add_argument("--job-id", help="关联的生成任务 ID")
    parser.add_argument("--user-id", type=int, help="关联的用户 ID")
    parser.add_argument("--tts-voice-id", help="RunningHub MiniMax 系统音色 ID")
    parser.add_argument("--tts-speed", type=float, help="RunningHub 语速（0.5-2）")
    parser.add_argument("--tts-volume", type=float, help="RunningHub 音量（0.1-10）")
    parser.add_argument("--tts-pitch", type=int, help="RunningHub 音调（-12 到 12）")
    parser.add_argument("--tts-emotion", help="RunningHub 情绪")
    parser.add_argument("--tts-pronunciation", help="RunningHub 发音词典规则")
    parser.add_argument(
        "--tts-english-normalization",
        choices=("true", "false"),
        help="是否启用 RunningHub 英文规范化",
    )
    return parser.parse_args()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def clean_text(raw: str) -> str:
    """Remove production notes while preserving spoken parenthetical content."""
    lines = [line for line in raw.split("\n") if "此处留白" not in line]
    return re.sub(r"【[^】]*】", "", "\n".join(lines))


def step1_dynamic_chunk_slicing(text_path: str):
    print("[Step 1] Dynamic chunk slicing...", flush=True)
    with open(text_path, "r", encoding="utf-8") as f:
        cleaned = clean_text(f.read())

    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    raw_segments = re.split(r"(?<=[。，！？\n])", cleaned)
    raw_segments = [segment.strip() for segment in raw_segments if segment.strip()]

    chunks = []
    buffer = ""
    for segment in raw_segments:
        candidate = buffer + segment
        if len(candidate) <= CHUNK_MAX_LEN:
            buffer = candidate
        else:
            if buffer.strip():
                chunks.append(buffer)
            buffer = segment
    if buffer.strip():
        chunks.append(buffer)

    index = 0
    while index < len(chunks):
        if len(chunks[index]) < CHUNK_MIN_LEN and index + 1 < len(chunks):
            chunks[index] += chunks[index + 1]
            chunks.pop(index + 1)
        else:
            index += 1

    if len(chunks) > 1 and len(chunks[-1]) < CHUNK_MIN_LEN:
        combined = chunks[-2] + chunks[-1]
        if len(combined) <= CHUNK_MAX_LEN:
            chunks[-2] = combined
            chunks.pop()

    print(f"  -> Produced {len(chunks)} chunks", flush=True)
    for index, chunk in enumerate(chunks):
        print(f"     [{index}] ({len(chunk)} chars): {chunk[:50]}", flush=True)
    return chunks


def format_srt(index, start, end, text):
    def fmt_time(value):
        hours = int(value // 3600)
        minutes = int((value % 3600) // 60)
        seconds = int(value % 60)
        millis = int((value - int(value)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    return f"{index}\n{fmt_time(start)} --> {fmt_time(end)}\n{text}\n"


def get_wav_duration(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def set_runtime_temp_dir(job_id: str | None) -> None:
    """Keep chunk filenames isolated per generation job."""
    global TEMP_DIR
    safe_id = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        job_id or f"manual_{os.getpid()}",
    ).strip("._")
    TEMP_DIR = os.path.join(TEMP_ROOT, safe_id or f"manual_{os.getpid()}")


def clear_temp_chunks() -> None:
    if not os.path.isdir(TEMP_DIR):
        return
    for filename in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, filename)
        if os.path.isfile(path):
            os.remove(path)


def step2_runninghub_synthesize(
    chunks,
    job_id: str | None = None,
    user_id: int | None = None,
    config=None,
):
    config = config or load_runninghub_tts_config()
    if config is None:
        raise RunningHubTTSError("RunningHub TTS 未启用或未配置 API Key")

    print(
        "[Step 2] RunningHub minimax/speech-2.8-hd synthesis "
        f"(voice_id={config.voice_id})...",
        flush=True,
    )
    ensure_dir(TEMP_DIR)
    total = min(len(chunks), MAX_TEST_CHUNKS)
    if total == 0:
        raise RunningHubTTSError("清洗和断句后没有可合成的文案")

    current_time = 0.0
    wav_paths = []
    srt_entries = []
    for index, chunk in enumerate(chunks[:total]):
        print(
            f"[PROGRESS] RunningHub 正在生成第 {index + 1}/{total} 句...",
            flush=True,
        )
        wav_path = os.path.join(TEMP_DIR, f"chunk_{index:04d}.wav")
        result = synthesize_runninghub_to_wav(chunk, wav_path, config)
        real_duration = get_wav_duration(wav_path)
        print(
            f"     task_id: {result.task_id}, 总耗时: {result.elapsed_seconds:.2f}s "
            f"(提交 {result.submit_seconds:.2f}s / 每秒轮询等待 "
            f"{result.wait_seconds:.2f}s / 下载转码 {result.download_seconds:.2f}s), "
            f"音频时长: {real_duration:.3f}s",
            flush=True,
        )
        if job_id:
            record_media_asset(
                user_id=user_id,
                generation_job_id=job_id,
                kind="audio",
                role="tts_chunk_remote",
                storage_backend="remote_runninghub",
                storage_path=result.audio_url,
                remote_id=result.task_id,
                original_name=f"runninghub_{result.task_id}.wav",
                mime_type="audio/wav",
                size_bytes=os.path.getsize(wav_path),
                duration_seconds=real_duration,
                sequence_index=index,
                metadata={
                    "text": chunk,
                    "provider": "runninghub",
                    "model": "minimax/speech-2.8-hd",
                    "voice_id": config.voice_id,
                    "source_output_type": result.output_type,
                    "submit_seconds": result.submit_seconds,
                    "wait_seconds": result.wait_seconds,
                    "download_seconds": result.download_seconds,
                    "client_elapsed_seconds": result.elapsed_seconds,
                },
            )
        wav_paths.append(wav_path)
        srt_entries.append(
            format_srt(
                index + 1,
                current_time,
                current_time + real_duration,
                chunk,
            )
        )
        current_time += real_duration
    return wav_paths, srt_entries


def step3_finalize(wav_paths, srt_entries):
    print("[Step 3] Finalizing output...", flush=True)
    ensure_dir(OUTPUT_DIR)

    output_wav = os.path.join(OUTPUT_DIR, "final_output.wav")
    with wave.open(wav_paths[0], "rb") as first:
        params = first.getparams()
    with wave.open(output_wav, "wb") as output:
        output.setparams(params)
        for path in wav_paths:
            with wave.open(path, "rb") as audio:
                output.writeframes(audio.readframes(audio.getnframes()))
    print(f"  -> WAV written: {output_wav}", flush=True)

    output_srt = os.path.join(OUTPUT_DIR, "final_output.srt")
    with open(output_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_entries) + "\n")
    print(f"  -> SRT written: {output_srt}", flush=True)


def step4_cleanup():
    print("[Step 4] Cleaning up temp files...", flush=True)
    if os.path.isdir(TEMP_DIR):
        clear_temp_chunks()
        os.rmdir(TEMP_DIR)
    print("  -> Cleanup done.", flush=True)


def runninghub_config_from_args(args):
    config = load_runninghub_tts_config()
    if config is None:
        raise RunningHubTTSError("RunningHub TTS 未启用或未配置 API Key")

    overrides = {}
    if args.tts_voice_id is not None:
        overrides["voice_id"] = args.tts_voice_id
    if args.tts_speed is not None:
        overrides["speed"] = args.tts_speed
    if args.tts_volume is not None:
        overrides["volume"] = args.tts_volume
    if args.tts_pitch is not None:
        overrides["pitch"] = args.tts_pitch
    if args.tts_emotion is not None:
        overrides["emotion"] = args.tts_emotion or None
    if args.tts_pronunciation is not None:
        pronunciation = args.tts_pronunciation.strip()
        overrides["pronunciation_dict"] = (pronunciation,) if pronunciation else ()
    if args.tts_english_normalization is not None:
        overrides["english_normalization"] = (
            args.tts_english_normalization == "true"
        )
    return replace(config, **overrides)


def main():
    args = parse_args()
    if not os.path.isabs(args.text):
        args.text = os.path.join(PROJECT_ROOT, args.text)
    if not os.path.isfile(args.text):
        sys.exit(f"【路径错误】找不到文件：{args.text}")

    print("=" * 60, flush=True)
    print("Module 1 -- RunningHub TTS Pipeline Start", flush=True)
    print("=" * 60, flush=True)
    print(f"  文案路径: {args.text}", flush=True)

    set_runtime_temp_dir(args.job_id)
    clear_temp_chunks()
    print(f"  临时音频目录: {TEMP_DIR}", flush=True)

    try:
        chunks = step1_dynamic_chunk_slicing(args.text)
        config = runninghub_config_from_args(args)
        wav_paths, srt_entries = step2_runninghub_synthesize(
            chunks,
            args.job_id,
            args.user_id,
            config,
        )
        step3_finalize(wav_paths, srt_entries)
    except Exception as exc:
        clear_temp_chunks()
        sys.exit(
            "【致命错误】RunningHub minimax/speech-2.8-hd 生成失败："
            f"{type(exc).__name__}: {exc}"
        )

    step4_cleanup()
    print("=" * 60, flush=True)
    print("RunningHub TTS 与 SRT 合成完成。", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
