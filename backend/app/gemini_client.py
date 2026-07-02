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
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


def gemini_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


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


def generate_gemini_text(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiError("未配置 GEMINI_API_KEY")

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
