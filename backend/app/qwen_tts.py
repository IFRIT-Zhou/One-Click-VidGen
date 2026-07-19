"""Small Qwen-TTS HTTP adapter used by Module 1.

The adapter deliberately uses the documented HTTP API instead of a DashScope SDK,
so the portable package does not gain another runtime dependency.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import requests


DEFAULT_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
# Elias is an official Instruct-compatible female narration voice.  Do not make
# a Flash-only voice the default: the normal UI flow includes a voice direction.
DEFAULT_VOICE = "Elias"

# Voices available to qwen3-tts-instruct-flash according to the official
# non-realtime voice list.  The remaining UI voices use qwen3-tts-flash and
# therefore require an empty instruction string.
INSTRUCT_COMPATIBLE_VOICES = frozenset({
    "Cherry", "Serena", "Ethan", "Chelsie", "Momo", "Vivian", "Moon",
    "Maia", "Kai", "Nofish", "Bella", "Eldric Sage", "Mia", "Mochi",
    "Bellona", "Vincent", "Bunny", "Neil", "Elias", "Arthur", "Nini",
    "Seren", "Pip", "Stella",
})


def voice_supports_instructions(voice: str) -> bool:
    """Whether an official system voice can be used with a voice direction."""
    return str(voice or DEFAULT_VOICE).strip() in INSTRUCT_COMPATIBLE_VOICES


class QwenTtsError(RuntimeError):
    """A safe, user-facing error without exposing the API key."""


def detect_language_type(text: str) -> str:
    """Choose one language mode for an entire Qwen synthesis job."""
    han_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
    latin_count = sum(char.isascii() and char.isalpha() for char in text)
    if han_count and latin_count:
        return "Auto"
    return "Chinese" if han_count else "English"


def _error_message(response: requests.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:300].strip()}"
    if not isinstance(payload, dict):
        return f"HTTP {response.status_code}"
    code = str(payload.get("code") or "")
    message = str(payload.get("message") or payload.get("detail") or "")
    request_id = str(payload.get("request_id") or "")
    suffix = f"（request_id: {request_id}）" if request_id else ""
    return f"HTTP {response.status_code} {code} {message}{suffix}".strip()


def synthesize_to_file(
    *,
    text: str,
    destination: Path,
    instructions: str = "",
    voice: str = DEFAULT_VOICE,
    language_type: str | None = None,
    optimize_instructions: bool = False,
    retries: int = 3,
) -> None:
    """Synthesize one short chunk and save the returned audio locally.

    Qwen returns a short-lived OSS URL. We download it immediately, so later
    rendering never depends on that URL still being valid.
    """

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise QwenTtsError("未配置 DASHSCOPE_API_KEY；请切换到 Qwen-TTS 后先保存 API Key")
    clean_text = str(text or "").strip()
    if not clean_text:
        raise QwenTtsError("Qwen-TTS 收到空文本")

    clean_instructions = str(instructions or "").strip()
    selected_voice = str(voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    if clean_instructions and not voice_supports_instructions(selected_voice):
        raise QwenTtsError("所选系统音色仅支持基础合成；请清空配音描述，或改选支持配音描述的音色")
    model = "qwen3-tts-instruct-flash" if clean_instructions else "qwen3-tts-flash"
    input_payload: dict[str, Any] = {
        "text": clean_text,
        "voice": selected_voice,
        "language_type": str(language_type or detect_language_type(clean_text)).strip() or "Auto",
    }
    if clean_instructions:
        input_payload["instructions"] = clean_instructions
        # Rewriting a carefully authored narrator brief can make it less strict.
        # Keep this opt-in rather than silently changing the user's direction.
        if optimize_instructions:
            input_payload["optimize_instructions"] = True

    payload = {"model": model, "input": input_payload}
    endpoint = os.getenv("DASHSCOPE_TTS_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_error = ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=(20, 180))
            if response.status_code >= 400:
                last_error = _error_message(response)
                if response.status_code >= 500 or response.status_code in {408, 429}:
                    if attempt < retries:
                        continue
                raise QwenTtsError(last_error)
            response.raise_for_status()
            result = response.json()
            output = result.get("output") if isinstance(result, dict) else None
            audio = output.get("audio") if isinstance(output, dict) else None
            if not isinstance(audio, dict):
                message = str(result.get("message") or result.get("code") or "响应中没有 audio 字段") if isinstance(result, dict) else "响应格式错误"
                raise QwenTtsError(message)

            destination.parent.mkdir(parents=True, exist_ok=True)
            audio_url = str(audio.get("url") or "").strip()
            if audio_url:
                download = requests.get(audio_url, stream=True, timeout=(20, 180))
                download.raise_for_status()
                with destination.open("wb") as handle:
                    for block in download.iter_content(chunk_size=1024 * 256):
                        if block:
                            handle.write(block)
            else:
                audio_data = str(audio.get("data") or "").strip()
                if not audio_data:
                    raise QwenTtsError("Qwen-TTS 未返回可下载的音频 URL 或数据")
                destination.write_bytes(base64.b64decode(audio_data))
            if not destination.is_file() or destination.stat().st_size < 128:
                raise QwenTtsError("Qwen-TTS 下载的音频为空")
            return
        except QwenTtsError:
            raise
        except (requests.RequestException, ValueError, OSError) as exc:
            last_error = str(exc)
            if attempt >= retries:
                break
    raise QwenTtsError(f"Qwen-TTS 请求或下载失败（已重试 {retries} 次）：{last_error[:500]}")
