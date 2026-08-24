"""RunningHub MiniMax speech-2.8-hd client used by the narration pipeline."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import load_project_env


RUNNINGHUB_HOST = "https://www.runninghub.cn"
DEFAULT_TTS_ENDPOINT = (
    "/openapi/v2/rhart-audio/text-to-audio/speech-2.8-hd"
)
DEFAULT_QUERY_ENDPOINT = "/openapi/v2/query"
SUCCESS_STATES = {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED"}
PENDING_STATES = {"", "RUNNING", "QUEUED", "PENDING", "CREATED", "WAITING"}
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
SYSTEM_VOICE_IDS = (
    "Wise_Woman",
    "Friendly_Person",
    "Inspirational_girl",
    "Deep_Voice_Man",
    "Calm_Woman",
    "Casual_Guy",
    "Lively_Girl",
    "Patient_Man",
    "Young_Knight",
    "Determined_Man",
    "Lovely_Girl",
    "Decent_Boy",
    "Imposing_Manner",
    "Elegant_Man",
    "Abbess",
    "Sweet_Girl_2",
    "Exuberant_Girl",
)
EMOTIONS = (
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
    "neutral",
)


class RunningHubTTSError(RuntimeError):
    """RunningHub could not produce a usable narration chunk."""


@dataclass(frozen=True)
class RunningHubTTSConfig:
    api_key: str
    endpoint: str
    query_endpoint: str
    voice_id: str
    speed: float
    volume: float
    pitch: int
    emotion: str | None
    pronunciation_dict: tuple[str, ...]
    english_normalization: bool
    poll_seconds: float
    max_wait_seconds: float
    request_attempts: int


@dataclass(frozen=True)
class RunningHubTTSResult:
    task_id: str
    audio_url: str
    output_type: str
    submit_seconds: float
    wait_seconds: float
    download_seconds: float
    elapsed_seconds: float


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def _env_bounded_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    return min(maximum, _env_float(name, default, minimum))


def _env_bounded_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    return min(maximum, _env_int(name, default, minimum))


def _absolute_url(value: str, default_path: str) -> str:
    endpoint = value.strip() or default_path
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    return f"{RUNNINGHUB_HOST}/{endpoint.lstrip('/')}"


def load_runninghub_tts_config() -> RunningHubTTSConfig | None:
    """Return configuration when the primary provider is enabled and keyed."""
    load_project_env()
    if not _env_bool("RUNNINGHUB_TTS_ENABLED", True):
        return None
    api_key = (
        os.getenv("RUNNINGHUB_TTS_API_KEY", "").strip()
        or os.getenv("RUNNINGHUB_API_KEY", "").strip()
    )
    if not api_key:
        return None
    emotion = os.getenv("RUNNINGHUB_TTS_EMOTION", "").strip() or None
    return RunningHubTTSConfig(
        api_key=api_key,
        endpoint=_absolute_url(
            os.getenv("RUNNINGHUB_TTS_ENDPOINT", ""),
            DEFAULT_TTS_ENDPOINT,
        ),
        query_endpoint=_absolute_url(
            os.getenv("RUNNINGHUB_TTS_QUERY_ENDPOINT", ""),
            DEFAULT_QUERY_ENDPOINT,
        ),
        voice_id=os.getenv("RUNNINGHUB_TTS_VOICE_ID", "Wise_Woman").strip()
        or "Wise_Woman",
        speed=_env_bounded_float("RUNNINGHUB_TTS_SPEED", 1.0, 0.5, 2.0),
        volume=_env_bounded_float("RUNNINGHUB_TTS_VOLUME", 1.0, 0.1, 10.0),
        pitch=_env_bounded_int("RUNNINGHUB_TTS_PITCH", 0, -12, 12),
        emotion=emotion,
        pronunciation_dict=tuple(
            value
            for value in [
                os.getenv("RUNNINGHUB_TTS_PRONUNCIATION", "").strip()
            ]
            if value
        ),
        english_normalization=_env_bool(
            "RUNNINGHUB_TTS_ENGLISH_NORMALIZATION",
            False,
        ),
        poll_seconds=_env_float("RUNNINGHUB_TTS_POLL_SECONDS", 1.0, 0.5),
        max_wait_seconds=_env_float(
            "RUNNINGHUB_TTS_MAX_WAIT_SECONDS",
            300.0,
            1.0,
        ),
        request_attempts=_env_int("RUNNINGHUB_TTS_REQUEST_ATTEMPTS", 3, 1),
    )


def runninghub_tts_configured() -> bool:
    return load_runninghub_tts_config() is not None


def _headers(config: RunningHubTTSConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


def _error_message(payload: dict[str, Any]) -> str:
    return str(
        payload.get("errorMessage")
        or payload.get("msg")
        or payload.get("message")
        or payload.get("error")
        or ""
    ).strip()


def _find_first(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item not in (None, ""):
                return item
        for item in value.values():
            found = _find_first(item, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first(item, keys)
            if found not in (None, ""):
                return found
    return None


def _audio_result(payload: dict[str, Any]) -> tuple[str | None, str]:
    results = payload.get("results")
    candidates = results if isinstance(results, list) else [payload]
    for item in candidates:
        url = _find_first(
            item,
            {
                "url",
                "fileUrl",
                "fileURL",
                "audioUrl",
                "audioURL",
                "downloadUrl",
                "downloadURL",
            },
        )
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            output_type = str(
                _find_first(item, {"outputType", "fileType", "format"}) or ""
            ).strip()
            return url, output_type
    return None, ""


def _request_json(
    session: requests.Session,
    config: RunningHubTTSConfig,
    url: str,
    *,
    label: str,
    json_payload: dict[str, Any],
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, config.request_attempts + 1):
        try:
            response = session.post(
                url,
                headers=_headers(config),
                json=json_payload,
                timeout=(10, 60),
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}",
                    response=response,
                )
            try:
                payload = response.json()
            except ValueError as exc:
                response.raise_for_status()
                raise RunningHubTTSError(f"{label}返回了无效 JSON") from exc
            if not isinstance(payload, dict):
                raise RunningHubTTSError(f"{label}返回了无效 JSON")
            if not response.ok:
                detail = _error_message(payload) or f"HTTP {response.status_code}"
                raise RunningHubTTSError(f"{label}失败: {detail}")
            return payload
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= config.request_attempts:
                break
            time.sleep(min(2**attempt, 8))
    raise RunningHubTTSError(
        f"{label}网络请求失败: {type(last_error).__name__ if last_error else 'unknown'}"
    ) from last_error


def _submit_task(
    session: requests.Session,
    config: RunningHubTTSConfig,
    text: str,
) -> str:
    payload: dict[str, Any] = {
        "text": text,
        "voice_id": config.voice_id,
        "speed": config.speed,
        "volume": config.volume,
        "pitch": config.pitch,
        "enable_base64_output": False,
        "english_normalization": config.english_normalization,
    }
    if config.emotion:
        payload["emotion"] = config.emotion
    if config.pronunciation_dict:
        payload["pronunciation_dict"] = list(config.pronunciation_dict)
    submitted = _request_json(
        session,
        config,
        config.endpoint,
        label="第三方 TTS 任务提交",
        json_payload=payload,
    )
    task_id = _find_first(submitted, {"taskId", "taskID"})
    if not task_id:
        detail = _error_message(submitted)
        raise RunningHubTTSError(
            f"第三方 TTS 未返回任务 ID{f': {detail}' if detail else ''}"
        )
    return str(task_id)


def _wait_for_result(
    session: requests.Session,
    config: RunningHubTTSConfig,
    task_id: str,
) -> tuple[str, str]:
    deadline = time.monotonic() + config.max_wait_seconds
    while time.monotonic() < deadline:
        result = _request_json(
            session,
            config,
            config.query_endpoint,
            label="第三方 TTS 任务查询",
            json_payload={"taskId": task_id},
        )
        status = str(
            _find_first(result, {"status", "state", "taskStatus"}) or ""
        ).upper()
        audio_url, output_type = _audio_result(result)
        if status in SUCCESS_STATES or audio_url:
            if not audio_url:
                raise RunningHubTTSError("第三方 TTS 成功但未返回音频地址")
            return audio_url, output_type
        error_code = str(
            _find_first(result, {"errorCode", "error_code"}) or ""
        ).strip()
        if error_code and error_code != "0":
            detail = _error_message(result) or f"错误码 {error_code}"
            raise RunningHubTTSError(f"第三方 TTS 云端任务失败: {detail}")
        if status not in PENDING_STATES:
            detail = _error_message(result) or status or "unknown error"
            raise RunningHubTTSError(f"第三方 TTS 云端任务失败: {detail}")
        time.sleep(config.poll_seconds)
    raise RunningHubTTSError(
        f"第三方 TTS 任务等待超时（{config.max_wait_seconds:g}s）"
    )


def _ffmpeg_binary() -> str:
    project_root = Path(__file__).resolve().parents[2]
    local = project_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if os.name == "nt" and local.is_file():
        return str(local)
    return shutil.which("ffmpeg") or "ffmpeg"


def _safe_network_error(exc: Exception) -> str:
    """Keep the useful TLS reason without logging a signed download URL."""
    message = str(exc).replace("\n", " ").strip()
    if "Caused by " in message:
        message = message.rsplit("Caused by ", 1)[-1]
    if " with url:" in message:
        message = message.split(" with url:", 1)[0]
    return message[:500] or type(exc).__name__


def _download_with_requests(
    session: requests.Session,
    config: RunningHubTTSConfig,
    audio_url: str,
    source_path: Path,
) -> None:
    last_error: requests.RequestException | None = None
    for attempt in range(1, config.request_attempts + 1):
        source_path.unlink(missing_ok=True)
        try:
            with session.get(audio_url, stream=True, timeout=(10, 120)) as response:
                response.raise_for_status()
                with source_path.open("wb") as output:
                    for block in response.iter_content(chunk_size=64 * 1024):
                        if block:
                            output.write(block)
            return
        except requests.exceptions.SSLError:
            source_path.unlink(missing_ok=True)
            raise
        except requests.RequestException as exc:
            last_error = exc
            source_path.unlink(missing_ok=True)
            if attempt >= config.request_attempts:
                break
            time.sleep(min(2**attempt, 8))
    assert last_error is not None
    raise last_error


def _download_with_curl(audio_url: str, source_path: Path) -> None:
    curl = shutil.which("curl")
    if not curl:
        raise RunningHubTTSError("系统未安装 curl，无法执行 TLS 下载回退")
    source_path.unlink(missing_ok=True)
    command = [curl]
    if _env_bool("RUNNINGHUB_TTS_CURL_BYPASS_PROXY", True):
        command.extend(["--noproxy", "*"])
    command.extend(
        [
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--retry-all-errors",
            "--retry-delay",
            "1",
            "--connect-timeout",
            "5",
            "--max-time",
            "60",
            "--output",
            str(source_path),
            audio_url,
        ]
    )
    process = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        source_path.unlink(missing_ok=True)
        lines: list[str] = []
        for raw_line in process.stderr.replace(audio_url, "<signed-url>").splitlines():
            line = raw_line.strip()
            if line and line not in lines:
                lines.append(line)
        detail = " | ".join(lines)
        raise RunningHubTTSError(
            f"curl TLS 下载回退失败（退出码 {process.returncode}）: {detail[:500]}"
        )


def _download_and_normalize(
    session: requests.Session,
    config: RunningHubTTSConfig,
    audio_url: str,
    wav_path: Path,
) -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    source_path = wav_path.with_suffix(f"{wav_path.suffix}.runninghub")
    partial_wav = wav_path.with_suffix(f"{wav_path.suffix}.part")
    try:
        download_backend = os.getenv(
            "RUNNINGHUB_TTS_DOWNLOAD_BACKEND",
            "auto",
        ).strip().lower()
        if download_backend == "curl":
            try:
                _download_with_curl(audio_url, source_path)
            except RunningHubTTSError as exc:
                print(
                    "[第三方 TTS] curl 下载失败，尝试 Requests："
                    f"{exc}",
                    flush=True,
                )
                _download_with_requests(session, config, audio_url, source_path)
        else:
            try:
                _download_with_requests(session, config, audio_url, source_path)
            except requests.exceptions.SSLError as exc:
                if download_backend == "requests":
                    raise
                print(
                    "[第三方 TTS] Requests TLS 下载失败，切换系统 curl："
                    f"{_safe_network_error(exc)}",
                    flush=True,
                )
                _download_with_curl(audio_url, source_path)
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise RunningHubTTSError("第三方 TTS 下载到了空音频")
        process = subprocess.run(
            [
                _ffmpeg_binary(),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(partial_wav),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            raise RunningHubTTSError(
                f"第三方 TTS 音频转 WAV 失败: {process.stderr.strip()}"
            )
        os.replace(partial_wav, wav_path)
    except requests.RequestException as exc:
        raise RunningHubTTSError(
            "第三方 TTS 音频下载失败: "
            f"{type(exc).__name__}: {_safe_network_error(exc)}"
        ) from exc
    finally:
        source_path.unlink(missing_ok=True)
        partial_wav.unlink(missing_ok=True)


def synthesize_runninghub_to_wav(
    text: str,
    wav_path: str | Path,
    config: RunningHubTTSConfig | None = None,
) -> RunningHubTTSResult:
    """Submit one text chunk, wait for it, and normalize the result to PCM WAV."""
    current_config = config or load_runninghub_tts_config()
    if current_config is None:
        raise RunningHubTTSError("第三方 TTS 未启用或未配置 API Key")
    started_at = time.perf_counter()
    with requests.Session() as session:
        submit_started_at = time.perf_counter()
        task_id = _submit_task(session, current_config, text)
        submit_seconds = time.perf_counter() - submit_started_at
        wait_started_at = time.perf_counter()
        audio_url, output_type = _wait_for_result(
            session,
            current_config,
            task_id,
        )
        wait_seconds = time.perf_counter() - wait_started_at
        download_started_at = time.perf_counter()
        try:
            _download_and_normalize(
                session,
                current_config,
                audio_url,
                Path(wav_path),
            )
        except RunningHubTTSError as exc:
            raise RunningHubTTSError(
                f"task_id={task_id}；{exc}"
            ) from exc
        download_seconds = time.perf_counter() - download_started_at
    return RunningHubTTSResult(
        task_id=task_id,
        audio_url=audio_url,
        output_type=output_type,
        submit_seconds=submit_seconds,
        wait_seconds=wait_seconds,
        download_seconds=download_seconds,
        elapsed_seconds=time.perf_counter() - started_at,
    )
