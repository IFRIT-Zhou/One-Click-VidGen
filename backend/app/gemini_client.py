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

# The original Gemini variable names are retained for backwards compatibility.
# New providers keep their own key/model variables so switching in the UI never
# overwrites a key the user has already configured for another provider.
LANGUAGE_PROVIDER_OPTIONS: dict[str, dict[str, str]] = {
    "gemini": {
        "label": "Google Gemini",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "GEMINI_MODEL",
        "default_base": DEFAULT_GEMINI_BASE_URL,
        "default_model": DEFAULT_GEMINI_MODEL,
        "protocol": "gemini",
    },
    "runninghub": {
        "label": "第三方兼容接口",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "GEMINI_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": DEFAULT_GEMINI_MODEL,
        "protocol": "openai",
    },
    "deepseek": {
        "label": "DeepSeek",
        "key_env": "DEEPSEEK_API_KEY",
        "base_env": "DEEPSEEK_API_BASE",
        "model_env": "DEEPSEEK_MODEL",
        "default_base": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "protocol": "openai",
    },
    "openai": {
        "label": "OpenAI GPT",
        "key_env": "OPENAI_API_KEY",
        "base_env": "OPENAI_API_BASE",
        "model_env": "OPENAI_MODEL",
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
        "protocol": "openai",
    },
    "kimi": {
        "label": "Kimi",
        "key_env": "MOONSHOT_API_KEY",
        "base_env": "MOONSHOT_API_BASE",
        "model_env": "MOONSHOT_MODEL",
        "default_base": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2.5",
        "protocol": "openai",
    },
    "glm": {
        "label": "智谱 GLM",
        "key_env": "ZAI_API_KEY",
        "base_env": "ZAI_API_BASE",
        "model_env": "ZAI_MODEL",
        "default_base": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.7-flash",
        "protocol": "openai",
    },
}


class GeminiError(RuntimeError):
    pass


class GeminiOutputTruncated(GeminiError):
    """The provider stopped because the completion token budget was exhausted."""


def gemini_configured() -> bool:
    config = language_provider_config()
    return bool(os.getenv(config["key_env"], "").strip())


def _provider() -> str:
    selected = os.getenv("LANGUAGE_PROVIDER", "").strip().lower()
    if selected in LANGUAGE_PROVIDER_OPTIONS:
        return selected
    legacy = os.getenv("GEMINI_PROVIDER", "google").strip().lower() or "google"
    if legacy in {"google", "gemini"}:
        return "gemini"
    if legacy in {"openai", "openai_compatible", "runninghub"}:
        return "runninghub"
    return legacy


def language_provider_config(provider: str | None = None) -> dict[str, str]:
    """Return the selected provider config, while accepting legacy relays."""
    provider_name = (provider or _provider()).strip().lower()
    if provider_name in LANGUAGE_PROVIDER_OPTIONS:
        return LANGUAGE_PROVIDER_OPTIONS[provider_name]
    return {
        "label": "OpenAI compatible",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "GEMINI_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": DEFAULT_GEMINI_MODEL,
        "protocol": "openai",
    }


def language_provider_status() -> dict[str, Any]:
    selected = _provider()
    options = [
        {"value": name, "label": config["label"], "configured": bool(os.getenv(config["key_env"], "").strip())}
        for name, config in LANGUAGE_PROVIDER_OPTIONS.items()
    ]
    config = language_provider_config(selected)
    return {
        "provider": selected,
        "provider_label": config["label"],
        "configured": bool(os.getenv(config["key_env"], "").strip()),
        "model": os.getenv(config["model_env"], config["default_model"]).strip() or config["default_model"],
        "providers": options,
    }


def _gemini_models(provider: str | None = None) -> list[str]:
    config = language_provider_config(provider)
    primary = os.getenv(config["model_env"], config["default_model"]).strip() or config["default_model"]
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
    provider: str | None = None,
) -> str:
    config = language_provider_config(provider)
    base_url = os.getenv(config["base_env"], config["default_base"]).rstrip("/")
    extra_body: dict[str, Any] = {}
    reasoning_effort = os.getenv("GEMINI_REASONING_EFFORT", "none").strip()
    if reasoning_effort and reasoning_effort.lower() != "none":
        extra_body["reasoning_effort"] = reasoning_effort
    if response_mime_type == "application/json":
        extra_body["response_format"] = {"type": "json_object"}

    errors: list[str] = []
    response: requests.Response | None = None
    for model in _gemini_models(provider):
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
        # Existing RunningHub relays historically accept this wrapper; official
        # OpenAI-compatible providers expect these fields at the request root.
        if provider in {"deepseek", "openai", "kimi", "glm"}:
            payload.update(extra_body)
        elif extra_body:
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
    provider = _provider()
    config = language_provider_config(provider)
    api_key = os.getenv(config["key_env"], "").strip()
    if not api_key:
        raise GeminiError(f"未配置 {config['key_env']}")

    if config["protocol"] == "openai":
        return _generate_openai_compatible_text(
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
            provider=provider,
        )

    base_url = os.getenv(config["base_env"], config["default_base"]).rstrip("/")
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
    for model in _gemini_models(provider):
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
