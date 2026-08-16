# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Zhou Ruoyu and He Yun

import json
import os
import re
import time
from typing import Any

import requests

from .config import load_project_env

load_project_env()


DEFAULT_GEMINI_MODEL = "google/gemini-3.1-flash-lite-preview"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://llm.runninghub.ai/v1"
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}

# The configured third-party node is the transport; provider names below are
# model families, not separate API accounts. All visible families therefore
# share one base URL and one language-model key while retaining their own last
# selected model. ``runninghub`` remains as a hidden legacy alias only.
LANGUAGE_PROVIDER_OPTIONS: dict[str, dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "GEMINI_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": DEFAULT_GEMINI_MODEL,
        "protocol": "openai",
        "models": [
            {"value": "google/gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash-Lite（推荐）"},
            {"value": "google/gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
            {"value": "google/gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite（省钱）"},
            {"value": "google/gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview（高质量）"},
        ],
    },
    "runninghub": {
        "label": "第三方兼容接口",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "GEMINI_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": DEFAULT_GEMINI_MODEL,
        "protocol": "openai",
        "models": [],
        "allow_custom_model": True,
        "hidden": True,
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "ANTHROPIC_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": "anthropic/claude-sonnet-5",
        "protocol": "openai",
        "models": [
            {"value": "anthropic/claude-sonnet-5", "label": "Claude Sonnet 5（推荐）"},
            {"value": "anthropic/claude-opus-5", "label": "Claude Opus 5（高质量）"},
            {"value": "anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5（省钱）"},
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "DEEPSEEK_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": "deepseek/deepseek-v4-flash",
        "protocol": "openai",
        "models": [
            {"value": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash（推荐）"},
            {"value": "deepseek/deepseek-v4-pro", "label": "DeepSeek V4 Pro（高质量）"},
        ],
    },
    "openai": {
        "label": "OpenAI GPT",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "OPENAI_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": "openai/gpt-5.6-terra",
        "protocol": "openai",
        "models": [
            {"value": "openai/gpt-5.6-terra", "label": "GPT-5.6 Terra（推荐）"},
            {"value": "openai/gpt-5.6-sol", "label": "GPT-5.6 Sol（高质量）"},
            {"value": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna（省钱）"},
            {"value": "openai/gpt-5.4-mini", "label": "GPT-5.4 mini（兼顾速度）"},
        ],
    },
    "qwen": {
        "label": "阿里云 Qwen",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "QWEN_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": "qwen/qwen3.7-plus",
        "protocol": "openai",
        "models": [
            {"value": "qwen/qwen3.8-max", "label": "Qwen3.8 Max（高质量）"},
            {"value": "qwen/qwen3.7-plus", "label": "Qwen3.7 Plus（推荐）"},
            {"value": "qwen/qwen3.6-flash", "label": "Qwen3.6 Flash（省钱）"},
        ],
    },
    "kimi": {
        "label": "Kimi（当前节点未开放）",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "MOONSHOT_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": "kimi-k3",
        "protocol": "openai",
        "models": [],
        "disabled": True,
        "disabled_reason": "当前三方节点的 /models 列表尚未开放 Kimi",
    },
    "glm": {
        "label": "智谱 GLM",
        "key_env": "GEMINI_API_KEY",
        "base_env": "GEMINI_API_BASE",
        "model_env": "ZAI_MODEL",
        "default_base": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "default_model": "glm-5.2",
        "protocol": "openai",
        "models": [
            {"value": "glm-5.2", "label": "GLM-5.2（推荐）"},
            {"value": "glm-5-turbo", "label": "GLM-5 Turbo（高速）"},
            {"value": "glm-5.1", "label": "GLM-5.1（兼顾成本）"},
        ],
    },
    "custom": {
        "label": "自定义兼容接口（高级）",
        "key_env": "CUSTOM_LLM_API_KEY",
        "base_env": "CUSTOM_LLM_API_BASE",
        "model_env": "CUSTOM_LLM_MODEL",
        "default_base": "http://127.0.0.1:1234/v1",
        "default_model": "local-model",
        "protocol": "openai",
        "models": [],
        "allow_custom_model": True,
        "optional_key": True,
    },
}


class GeminiError(RuntimeError):
    pass


class GeminiOutputTruncated(GeminiError):
    """The provider stopped because the completion token budget was exhausted."""


def gemini_configured() -> bool:
    return language_provider_configured()


def _provider() -> str:
    selected = os.getenv("LANGUAGE_PROVIDER", "").strip().lower()
    if selected in LANGUAGE_PROVIDER_OPTIONS:
        return "gemini" if selected == "runninghub" else selected
    legacy = os.getenv("GEMINI_PROVIDER", "google").strip().lower() or "google"
    if legacy in {"google", "gemini"}:
        return "gemini"
    if legacy in {"openai", "openai_compatible", "runninghub"}:
        return "gemini"
    return legacy


def language_provider_config(provider: str | None = None) -> dict[str, Any]:
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
        "models": [],
        "allow_custom_model": True,
    }


def language_provider_models(provider: str | None = None) -> list[dict[str, str]]:
    config = language_provider_config(provider)
    return [
        {"value": str(item.get("value") or ""), "label": str(item.get("label") or item.get("value") or "")}
        for item in config.get("models", [])
        if str(item.get("value") or "").strip()
    ]


def language_model(provider: str | None = None) -> str:
    config = language_provider_config(provider)
    return os.getenv(config["model_env"], config["default_model"]).strip() or config["default_model"]


def language_model_allowed(provider: str, model: str) -> bool:
    config = language_provider_config(provider)
    if config.get("allow_custom_model"):
        return bool(model.strip())
    candidate = model.strip()
    configured = os.getenv(config["model_env"], "").strip()
    return candidate == configured or candidate in {item["value"] for item in language_provider_models(provider)}


def language_provider_configured(provider: str | None = None, values: Any | None = None) -> bool:
    config = language_provider_config(provider)
    source = values if values is not None else os.environ
    if config.get("optional_key"):
        return bool(
            str(source.get(config["base_env"], "")).strip()
            and str(source.get(config["model_env"], "")).strip()
        )
    return bool(str(source.get(config["key_env"], "")).strip())


def language_provider_status() -> dict[str, Any]:
    selected = _provider()
    options = []
    for name, config in LANGUAGE_PROVIDER_OPTIONS.items():
        if config.get("hidden"):
            continue
        options.append({
            "value": name,
            "label": config["label"],
            "configured": language_provider_configured(name) and not bool(config.get("disabled")),
            "selected_model": language_model(name),
            "models": language_provider_models(name),
            "allow_custom_model": bool(config.get("allow_custom_model")),
            "disabled": bool(config.get("disabled")),
            "disabled_reason": str(config.get("disabled_reason") or ""),
        })
    config = language_provider_config(selected)
    return {
        "provider": selected,
        "provider_label": config["label"],
        "configured": language_provider_configured(selected),
        "model": language_model(selected),
        "providers": options,
    }


def _gemini_models(provider: str | None = None) -> list[str]:
    config = language_provider_config(provider)
    primary = language_model(provider)
    fallback_raw = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
    models = [primary]
    if fallback_raw and (provider or _provider()) in {"gemini", "runninghub"}:
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


def _gemini_timeout() -> tuple[float, float]:
    try:
        connect = max(1.0, float(os.getenv("GEMINI_CONNECT_TIMEOUT_SECONDS", "10")))
    except ValueError:
        connect = 10.0
    try:
        read = max(connect, float(os.getenv("GEMINI_READ_TIMEOUT_SECONDS", "120")))
    except ValueError:
        read = 120.0
    return connect, read


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
    json_root: str | None = None,
) -> str:
    config = language_provider_config(provider)
    base_url = os.getenv(config["base_env"], config["default_base"]).rstrip("/")
    extra_body: dict[str, Any] = {}
    reasoning_effort = os.getenv("GEMINI_REASONING_EFFORT", "none").strip()
    if reasoning_effort and reasoning_effort.lower() != "none":
        extra_body["reasoning_effort"] = reasoning_effort
    # OpenAI's json_object mode requires a top-level object. Agent 2, however,
    # intentionally returns a top-level array. Enabling json_object for that
    # request makes GPT collapse a multi-item storyboard into one object.
    if response_mime_type == "application/json" and json_root != "array":
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
        if provider in {"deepseek", "openai", "qwen", "kimi", "glm"}:
            payload.update(extra_body)
        elif extra_body:
            payload["extra_body"] = extra_body

        url = f"{base_url}/chat/completions"
        response = None
        for attempt in range(1, _gemini_retry_count() + 1):
            try:
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=_gemini_timeout(),
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
    json_root: str | None = None,
) -> str:
    provider = _provider()
    config = language_provider_config(provider)
    api_key = os.getenv(config["key_env"], "").strip()
    if not api_key and not config.get("optional_key"):
        raise GeminiError(f"未配置 {config['key_env']}")
    if config.get("optional_key") and not language_provider_configured(provider):
        raise GeminiError(
            f"自定义语言接口未配置完整，请在 .env 中填写 {config['base_env']} 和 {config['model_env']}"
        )

    if config["protocol"] == "openai":
        return _generate_openai_compatible_text(
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
            provider=provider,
            json_root=json_root,
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
                    timeout=_gemini_timeout(),
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
