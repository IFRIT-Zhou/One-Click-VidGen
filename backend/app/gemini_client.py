import json
import os
import re
import time
from typing import Any

import requests

from .config import load_project_env

load_project_env()


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://llm.runninghub.ai/v1"
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


class GeminiOutputTruncated(GeminiError):
    """The provider stopped because the completion token budget was exhausted."""


def gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _provider() -> str:
    return os.getenv("GEMINI_PROVIDER", "google").strip().lower() or "google"


def _gemini_models() -> list[str]:
    primary = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    fallback_raw = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
    models = [primary]
    if fallback_raw:
        models.extend(re.split(r"[,;\s]+", fallback_raw))
    unique: list[str] = []
    for model in models:
        model = model.strip()
        if model and model not in unique:
            unique.append(model)
    return unique


def _gemini_retry_count() -> int:
    raw_value = os.getenv("GEMINI_RETRY_COUNT", "3").strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 3


def _gemini_retry_delay(attempt: int) -> float:
    raw_value = os.getenv("GEMINI_RETRY_DELAY_SECONDS", "3").strip()
    try:
        base_delay = max(0.5, float(raw_value))
    except ValueError:
        base_delay = 3.0
    return min(30.0, base_delay * (2 ** max(0, attempt - 1)))


def _extract_error_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    if not isinstance(data, dict):
        return str(data)[:300]
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("status")
        if message:
            return str(message)
    message = data.get("message") or data.get("errorMessage")
    return str(message) if message else str(data)[:300]


def _generate_openai_compatible_text(
    *,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    response_mime_type: str | None,
    max_output_tokens: int | None,
) -> str:
    base_url = os.getenv("GEMINI_API_BASE", DEFAULT_OPENAI_COMPATIBLE_BASE_URL).rstrip("/")
    extra_body: dict[str, Any] = {}
    reasoning_effort = os.getenv("GEMINI_REASONING_EFFORT", "none").strip()
    if reasoning_effort:
        extra_body["reasoning_effort"] = reasoning_effort
    if response_mime_type == "application/json":
        extra_body["response_format"] = {"type": "json_object"}

    errors: list[str] = []
    response: requests.Response | None = None
    for model in _gemini_models():
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
            "max_tokens": max_output_tokens or int(os.getenv("GEMINI_MAX_TOKENS", "4096")),
            "temperature": temperature,
            "top_p": float(os.getenv("GEMINI_TOP_P", "1")),
            "presence_penalty": float(os.getenv("GEMINI_PRESENCE_PENALTY", "0")),
            "frequency_penalty": float(os.getenv("GEMINI_FREQUENCY_PENALTY", "0")),
        }
        if extra_body:
            payload["extra_body"] = extra_body

        url = f"{base_url}/chat/completions"
        response = None
        for attempt in range(1, _gemini_retry_count() + 1):
            try:
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=120,
                )
            except requests.RequestException as exc:
                errors.append(f"{model}: 网络异常 {type(exc).__name__}")
                if attempt < _gemini_retry_count():
                    time.sleep(_gemini_retry_delay(attempt))
                    continue
                break

            if response.ok:
                break

            message = _extract_error_message(response)
            errors.append(f"{model}: HTTP {response.status_code} {message}")
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < _gemini_retry_count():
                time.sleep(_gemini_retry_delay(attempt))
                continue
            break
        if response is not None and response.ok:
            break
    else:
        response = None

    if response is None or not response.ok:
        raise GeminiError("OpenAI 兼容 Gemini 调用失败: " + "；".join(errors[-6:]))

    data = response.json()
    try:
        choice = data["choices"][0]
        finish_reason = str(choice.get("finish_reason") or "").strip().lower()
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"无法解析 OpenAI 兼容响应: {data}") from exc
    if finish_reason == "length":
        usage = data.get("usage") if isinstance(data, dict) else None
        raise GeminiOutputTruncated(
            f"Gemini 输出被长度上限截断（max_tokens={payload['max_tokens']}，usage={usage}）"
        )
    text = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
    if not text:
        raise GeminiError(f"OpenAI 兼容 Gemini 返回为空: {data}")
    return text


def generate_gemini_text(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiError("未配置 GEMINI_API_KEY")

    if _provider() in {"openai", "openai_compatible", "runninghub"}:
        return _generate_openai_compatible_text(
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
        )

    base_url = os.getenv("GEMINI_API_BASE", DEFAULT_GEMINI_BASE_URL).rstrip("/")
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt.strip()}\n\n{user_prompt.strip()}"}],
            }
        ],
        "generationConfig": {"temperature": temperature},
    }
    if max_output_tokens:
        payload["generationConfig"]["maxOutputTokens"] = max_output_tokens
    if response_mime_type:
        payload["generationConfig"]["responseMimeType"] = response_mime_type

    errors: list[str] = []
    response: requests.Response | None = None
    for model in _gemini_models():
        response = None
        url = f"{base_url}/models/{model}:generateContent"
        for attempt in range(1, _gemini_retry_count() + 1):
            try:
                response = requests.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=120,
                )
            except requests.RequestException as exc:
                errors.append(f"{model}: 网络异常 {type(exc).__name__}")
                if attempt < _gemini_retry_count():
                    time.sleep(_gemini_retry_delay(attempt))
                    continue
                break

            if response.ok:
                break

            message = _extract_error_message(response)
            errors.append(f"{model}: HTTP {response.status_code} {message}")
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < _gemini_retry_count():
                time.sleep(_gemini_retry_delay(attempt))
                continue
            break
        if response is not None and response.ok:
            break
    else:
        response = None

    if response is None or not response.ok:
        raise GeminiError("Gemini 调用失败: " + "；".join(errors[-6:]))

    data = response.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"无法解析 Gemini 响应: {data}") from exc
    if not text:
        raise GeminiError(f"Gemini 返回为空: {data}")
    return text


def parse_json_response(text: str) -> Any:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    return json.loads(cleaned)
