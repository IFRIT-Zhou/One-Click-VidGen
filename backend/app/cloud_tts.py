"""Cluster TTS orchestration and local artifact assembly."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any, Callable

from .cloud_client import CloudApiError, CloudClient


CLOUD_MAX_CHUNKS = 20
CLOUD_MAX_CHUNK_CHARS = 1000
CLOUD_MAX_TOTAL_CHARS = 5000


class CloudTtsCancelled(RuntimeError):
    pass


def _sanitize_pcm16_wav(path: Path, *, target_peak: float = 0.95) -> dict[str, Any]:
    """Keep a downloaded PCM16 chunk below full scale before concatenation.

    The cluster finalizer performs the authoritative look-ahead limiting.  This
    client-side guard protects users from older/cached cluster results and from
    a future finalizer regression; it is deliberately lossless when the peak is
    already within the safe range.  It cannot restore waveform detail that was
    clipped upstream, which is why the model-side float-save fix is still
    required.
    """
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        payload = source.readframes(params.nframes)
    if params.sampwidth != 2:
        return {"peak": None, "clipped_samples": 0, "sample_count": 0}
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {"peak": 0.0, "clipped_samples": 0, "sample_count": 0}
    peak_raw = max(abs(value) for value in samples)
    clipped = sum(1 for value in samples if abs(value) >= 32767)
    peak = peak_raw / 32767.0
    # Floor the target so integer PCM quantization cannot round one sample
    # above the requested normalized peak.
    target_raw = max(1, min(32767, int(float(target_peak) * 32767)))
    if peak_raw > target_raw:
        scale = target_raw / peak_raw
        converted = array(
            "h",
            [
                max(-32768, min(32767, int(round(value * scale))))
                for value in samples
            ],
        )
        if sys.byteorder != "little":
            converted.byteswap()
        temporary = path.with_name(f".{path.name}.safe-{os.getpid()}")
        with wave.open(str(temporary), "wb") as destination:
            destination.setparams(params)
            destination.writeframes(converted.tobytes())
        os.replace(temporary, path)
        peak = target_raw / 32767.0
    return {
        "peak": round(float(peak), 8),
        "clipped_samples": clipped,
        "sample_count": len(samples),
    }


def split_cloud_text(text: str) -> list[str]:
    # Keep the deployed cluster's established prosody boundaries stable.
    from module1_agent_director import split_cluster_tts_text

    chunks = split_cluster_tts_text(text)
    if not chunks:
        raise ValueError("清洗和断句后没有可合成的文案")
    total_chars = sum(len(chunk) for chunk in chunks)
    if total_chars > CLOUD_MAX_TOTAL_CHARS:
        raise ValueError(
            f"单次集群配音最多支持 {CLOUD_MAX_TOTAL_CHARS} 字，"
            f"当前断句后共 {total_chars} 字；请启用长文自动分段或拆分后生成"
        )

    # The cloud API accepts at most 20 chunks in one atomic quote/job.  The
    # local prosody splitter deliberately emits shorter chunks, so ordinary
    # 1,000+ character scripts can exceed that list limit even though both the
    # total text and every individual chunk are valid.  Merge the shortest
    # neighbouring pair until the protocol limit is met.  This preserves text
    # order and exact coverage, and only affects the remote cluster path.
    merged = list(chunks)
    while len(merged) > CLOUD_MAX_CHUNKS:
        candidates = [
            (len(merged[index]) + len(merged[index + 1]), index)
            for index in range(len(merged) - 1)
            if len(merged[index]) + len(merged[index + 1]) <= CLOUD_MAX_CHUNK_CHARS
        ]
        if not candidates:
            raise ValueError(
                f"集群配音无法安全合并到 {CLOUD_MAX_CHUNKS} 个分块以内；"
                "请拆分文案后重试"
            )
        _combined_length, index = min(candidates)
        merged[index:index + 2] = [merged[index] + merged[index + 1]]

    if "".join(merged) != "".join(chunks):
        raise RuntimeError("集群配音分块合并完整性检查失败")
    return merged


def cloud_voice_payload(request: dict[str, Any]) -> dict[str, str]:
    voice_type = str(request.get("cluster_voice_type") or "preset").strip().lower()
    if voice_type == "custom":
        # The first client implementation and design document used ``custom``;
        # the deployed cloud API calls the same user-owned resource ``uploaded``.
        voice_type = "uploaded"
    if voice_type not in {"preset", "uploaded"}:
        raise ValueError("集群音色类型必须是 preset 或 uploaded")
    voice_id = str(request.get("cluster_voice_id") or "").strip()
    if not voice_id:
        raise ValueError("请选择集群参考音色")
    return {"type": voice_type, "id": voice_id}


def build_quote_payload(request: dict[str, Any], chunks: list[str] | None = None) -> dict[str, Any]:
    prepared = chunks or split_cloud_text(str(request.get("script") or ""))
    return {
        "chunks": [{"index": index, "text": text} for index, text in enumerate(prepared)],
        "voice": cloud_voice_payload(request),
        "audio": {"sample_rate": 24000},
        "gpu_acceleration": True,
    }


def build_job_payload(
    request: dict[str, Any],
    chunks: list[str],
    *,
    client_job_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_job_id": client_job_id,
        "chunks": [{"index": index, "text": text} for index, text in enumerate(chunks)],
        "voice": cloud_voice_payload(request),
        "audio": {
            "speed": float(request.get("tts_speed", 1) or 1),
            "volume": float(request.get("tts_volume", 1) or 1),
            "pitch": int(request.get("tts_pitch", 0) or 0),
            "sample_rate": 24000,
            "channels": 1,
        },
    }
    emotion = str(request.get("tts_emotion") or "").strip()
    if emotion:
        raw_emotion_weight = request.get("tts_emotion_weight", 0.65)
        payload["emotion"] = {
            "name": emotion,
            "weight": float(0.65 if raw_emotion_weight is None else raw_emotion_weight),
        }
    return payload


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wav_info(path: Path) -> tuple[wave._wave_params, float]:
    try:
        with wave.open(str(path), "rb") as audio:
            params = audio.getparams()
            if params.nchannels != 1 or params.sampwidth != 2 or params.framerate != 24000:
                raise RuntimeError(
                    f"云端音频格式错误：{path.name} 必须是 24 kHz、单声道、16-bit PCM WAV"
                )
            frame_count = audio.getnframes()
            payload = audio.readframes(frame_count)
            expected_bytes = frame_count * params.nchannels * params.sampwidth
            if len(payload) != expected_bytes:
                raise RuntimeError(
                    f"云端音频下载不完整：{path.name}（期望 {expected_bytes} 字节，实际 {len(payload)}）"
                )
            duration = frame_count / max(1, audio.getframerate())
    except (wave.Error, EOFError) as exc:
        raise RuntimeError(f"云端返回的音频不是有效 WAV：{path.name}") from exc
    if duration <= 0:
        raise RuntimeError(f"云端返回了空音频：{path.name}")
    return params, duration


def assemble_cloud_audio(
    chunks: list[str],
    wav_paths: list[Path],
    *,
    output_dir: Path,
    segment_archive_dir: Path,
    manifest_metadata: dict[str, Any],
) -> dict[str, Any]:
    if not chunks or len(chunks) != len(wav_paths):
        raise RuntimeError("云端音频分块数量与原文不一致")
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_archive_dir.mkdir(parents=True, exist_ok=True)

    params: wave._wave_params | None = None
    durations: list[float] = []
    quality: list[dict[str, Any]] = []
    for path in wav_paths:
        current_params, duration = _wav_info(path)
        current_format = (current_params.nchannels, current_params.sampwidth, current_params.framerate)
        if params is None:
            params = current_params
        elif current_format != (params.nchannels, params.sampwidth, params.framerate):
            raise RuntimeError(f"云端 WAV 分块格式不一致：{path.name}")
        quality.append(_sanitize_pcm16_wav(path))
        durations.append(duration)
    assert params is not None

    output_wav = output_dir / "final_output.wav"
    with wave.open(str(output_wav), "wb") as output:
        output.setparams(params)
        for path in wav_paths:
            with wave.open(str(path), "rb") as audio:
                output.writeframes(audio.readframes(audio.getnframes()))

    srt_entries: list[str] = []
    segment_items: list[dict[str, Any]] = []
    current_time = 0.0
    for index, (text, source, duration) in enumerate(zip(chunks, wav_paths, durations), start=1):
        end_time = current_time + duration
        srt_entries.append(
            f"{index}\n{_srt_timestamp(current_time)} --> {_srt_timestamp(end_time)}\n{text}\n"
        )
        filename = f"segment_{index:04d}.wav"
        shutil.copy2(source, segment_archive_dir / filename)
        segment_items.append(
            {
                "index": index,
                "text": text,
                "filename": filename,
                "start": round(current_time, 6),
                "end": round(end_time, 6),
                "duration": round(duration, 6),
            }
        )
        current_time = end_time

    output_srt = output_dir / "final_output.srt"
    output_srt.write_text("\n".join(srt_entries).rstrip() + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "engine": "cluster",
        **manifest_metadata,
        "total_duration": round(current_time, 6),
        "audio_quality": quality,
        "segments": segment_items,
    }
    (segment_archive_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "audio_path": str(output_wav),
        "subtitle_path": str(output_srt),
        "duration": round(current_time, 6),
    }


def _result_chunks_by_index(payload: dict[str, Any], total: int) -> dict[int, dict[str, Any]]:
    result = payload.get("result")
    items = result.get("chunks") if isinstance(result, dict) else None
    if items is None:
        return {}
    if not isinstance(items, list):
        raise RuntimeError("云端响应中 result.chunks 格式错误")
    by_index: dict[int, dict[str, Any]] = {}
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        try:
            index = int(raw_item.get("index"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= total:
            raise RuntimeError(f"云端返回了超出范围的分块索引：{index}")
        if index in by_index:
            raise RuntimeError(f"云端返回了重复分块索引：{index}")
        by_index[index] = raw_item
    return by_index


def _ordered_result_chunks(payload: dict[str, Any], total: int) -> list[dict[str, Any]]:
    by_index = _result_chunks_by_index(payload, total)
    expected = list(range(total))
    if sorted(by_index) != expected:
        raise RuntimeError(f"云端分块索引不完整：期望 0-{total - 1}，实际 {sorted(by_index)}")
    return [by_index[index] for index in expected]


def synthesize_cloud_tts(
    *,
    client: CloudClient,
    local_job_id: str,
    request: dict[str, Any],
    output_dir: Path,
    segment_archive_dir: Path,
    temp_dir: Path,
    is_cancelled: Callable[[], bool],
    on_progress: Callable[[int, str], None],
    on_log: Callable[[str], None],
    on_remote_job: Callable[[str, dict[str, Any]], None],
    chunks_override: list[str] | None = None,
) -> dict[str, Any]:
    chunks = [str(value) for value in (chunks_override or []) if str(value).strip()]
    if not chunks:
        chunks = split_cloud_text(str(request.get("script") or ""))
    attempt = max(1, int(request.get("_cloud_tts_attempt", 1) or 1))
    idempotency_key = f"{local_job_id}:tts:v{attempt}"
    remote_job_id = str(request.get("_cloud_job_id") or "").strip()
    if is_cancelled():
        raise CloudTtsCancelled("用户已停止生成")

    if remote_job_id:
        on_log(f"断点续跑：继续查询云端任务 {remote_job_id}")
        remote = client.get_job(remote_job_id)
    else:
        # The deployed Cloud API treats ``client_job_id`` and the HTTP
        # Idempotency-Key as the same request identity.  Include the TTS
        # attempt in both values so a deliberate retry can create a fresh
        # remote job while repeated delivery of the same attempt remains
        # idempotent.
        payload = build_job_payload(request, chunks, client_job_id=idempotency_key)
        on_log(f"正在提交集群 TTS：{len(chunks)} 个分块将全部入队，由空闲 GPU 自动调度")
        remote = client.create_job(payload, idempotency_key=idempotency_key)
        remote_job_id = str(remote.get("job_id") or "").strip()
        if not remote_job_id:
            raise RuntimeError("云端创建任务响应缺少 job_id")
        on_remote_job(remote_job_id, remote)
        on_log(f"云端任务已创建：{remote_job_id}，预扣积分 {remote.get('reserved_credits', 0)}")

    deadline = time.monotonic() + client.config.max_wait_seconds
    last_status = ""
    last_progress = -1
    last_completed_chunks = -1
    cancel_sent = False
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    downloaded_wavs: dict[int, Path] = {}
    while True:
        if is_cancelled():
            if not cancel_sent:
                cancel_sent = True
                try:
                    cancelled = client.cancel_job(remote_job_id)
                    on_remote_job(remote_job_id, cancelled)
                    on_log(f"已向云端任务 {remote_job_id} 发送取消请求")
                except CloudApiError as exc:
                    on_log(f"云端取消请求未确认：{exc}")
            raise CloudTtsCancelled("用户已停止生成")
        status = str(remote.get("status") or "").strip().lower()
        try:
            progress = max(0, min(100, int(remote.get("progress") or 0)))
        except (TypeError, ValueError):
            progress = 0
        message = str(remote.get("message") or "").strip() or f"云端任务 {status or '处理中'}"
        remote_error = remote.get("error")
        if status in {"failed", "cancelled", "expired"}:
            if isinstance(remote_error, dict):
                error_message = str(
                    remote_error.get("message")
                    or remote_error.get("detail")
                    or remote_error.get("code")
                    or ""
                ).strip()
            else:
                error_message = str(remote_error or "").strip()
            if error_message:
                message = error_message
        ready_chunks = _result_chunks_by_index(remote, len(chunks))
        completed_chunks = max(
            len(ready_chunks),
            int(remote.get("completed_chunks") or 0),
        )
        if status != last_status or completed_chunks != last_completed_chunks:
            on_remote_job(remote_job_id, remote)
            if status != last_status:
                on_log(f"云端状态：{status or 'unknown'} · {message}")
            last_status = status
            last_completed_chunks = completed_chunks

        for index, item in ready_chunks.items():
            if index in downloaded_wavs:
                continue
            if is_cancelled():
                raise CloudTtsCancelled("用户已停止生成")
            audio_url = str(item.get("audio_url") or "").strip()
            if not audio_url:
                audio_url = f"/api/v1/cloud/jobs/{remote_job_id}/chunks/{index}/audio"
            target = temp_dir / f"chunk-cluster-{index + 1:04d}.wav"
            try:
                client.download_to(audio_url, target)
                _wav_info(target)
            except (CloudApiError, RuntimeError) as exc:
                on_log(f"云端分块 {index + 1} 已生成，下载尚未就绪，将自动重试：{exc}")
                continue
            downloaded_wavs[index] = target
            on_log(
                f"[TTS_PROGRESS] 配音进度 {len(downloaded_wavs)}/{len(chunks)}："
                f"已下载云端分块 {index + 1}"
            )

        if progress != last_progress:
            on_progress(progress, message)
            last_progress = progress
        if status == "completed":
            _ordered_result_chunks(remote, len(chunks))
            if len(downloaded_wavs) == len(chunks):
                break
        if status in {"failed", "cancelled", "expired"}:
            code = str(remote_error.get("code") or "") if isinstance(remote_error, dict) else ""
            raise RuntimeError(f"云端任务{status}：{message}{f' ({code})' if code else ''}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"等待云端任务超时（{int(client.config.max_wait_seconds)} 秒）")
        time.sleep(client.config.poll_interval)
        remote = client.get_job(remote_job_id)

    wav_paths = [downloaded_wavs[index] for index in range(len(chunks))]

    metadata = {
        "cloud_job_id": remote_job_id,
        "cluster_voice_type": cloud_voice_payload(request)["type"],
        "cluster_voice_id": cloud_voice_payload(request)["id"],
        "tts_speed": float(request.get("tts_speed", 1) or 1),
        "tts_volume": float(request.get("tts_volume", 1) or 1),
        "tts_pitch": int(request.get("tts_pitch", 0) or 0),
        "tts_emotion": str(request.get("tts_emotion") or ""),
        "tts_emotion_weight": float(request.get("tts_emotion_weight", 0.65)),
        "reserved_credits": remote.get("reserved_credits"),
        "consumed_credits": remote.get("consumed_credits"),
        "released_credits": remote.get("released_credits"),
    }
    assembled = assemble_cloud_audio(
        chunks,
        wav_paths,
        output_dir=output_dir,
        segment_archive_dir=segment_archive_dir,
        manifest_metadata=metadata,
    )
    on_remote_job(remote_job_id, remote)
    on_log(
        f"集群 TTS 完成：{len(chunks)} 个分块，实际消耗积分 {remote.get('consumed_credits', 0)}，"
        f"释放 {remote.get('released_credits', 0)}"
    )
    return {"cloud_job": remote, **assembled}
