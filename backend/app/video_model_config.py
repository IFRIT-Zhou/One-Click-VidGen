"""Independent credentials for the currently supported async video protocol.

Image credentials are only advertised as a migration option, never used by the
runtime until the user explicitly saves that choice. Public responses contain no
credential material (not even a key suffix).
"""
from __future__ import annotations

import os
import threading
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from module6_dynamic_video import DEFAULT_BASE_URL, QUERY_PATH, SUBMIT_PATH, UPLOAD_PATH
from .auth import require_user
from .config import ENV_PATH, _parse_env_lines, save_project_env_values
from .image_profiles import _legacy_document, _legacy_keys, list_profiles


router = APIRouter(prefix="/api/video-model")
LOCK = threading.RLock()
_COMPATIBLE_HOSTS = {"runninghub.ai", "www.runninghub.ai", "runninghub.cn", "www.runninghub.cn"}


class VideoModelRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    submit_path: str = Field(default=SUBMIT_PATH, min_length=1, max_length=1024)
    query_path: str = Field(default=QUERY_PATH, min_length=1, max_length=1024)
    upload_path: str = Field(default=UPLOAD_PATH, min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=2048)
    api_keys: list[str] = Field(default_factory=list, max_length=10)
    resolution: Literal["480p", "720p"] = "720p"
    concurrency_mode: Literal["auto", "manual"] = "auto"
    per_key_concurrency: int = Field(default=1, ge=1, le=8)
    total_concurrency: int = Field(default=3, ge=1, le=32)
    use_image_credentials: bool = False


def _unique_keys(values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        for value in str(raw or "").replace(";", ",").split(","):
            value = value.strip()
            if value and value not in result:
                result.append(value)
    return result


def _values() -> dict[str, str]:
    values = _parse_env_lines(ENV_PATH)
    values.update({key: value for key, value in os.environ.items() if value is not None})
    return values


def _validate_base(value: str) -> str:
    cleaned = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError:
        raise ValueError("视频 API Base URL 格式无效") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in cleaned or "#" in cleaned
            or "\\" in cleaned or any(char.isspace() or ord(char) < 32 for char in cleaned)):
        raise ValueError("视频 API Base URL 必须是 http(s) 地址，且不能包含账号、查询参数或锚点")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("视频 API Base URL 端口无效")
    return cleaned


def _validate_path(value: str) -> str:
    cleaned = str(value or "").strip()
    decoded = unquote(cleaned)
    parsed = urlsplit(cleaned)
    if (not cleaned.startswith("/") or cleaned.startswith("//")
            or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or "?" in cleaned or "#" in cleaned or "\\" in decoded
            or decoded.startswith("//") or any(part in {".", ".."} for part in decoded.split("/"))
            or any(char.isspace() or ord(char) < 32 for char in decoded)):
        raise ValueError("视频提交路径必须是以 / 开头的站内路径，不能包含其他网址或查询参数")
    return cleaned


def _origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(_validate_base(value))
    return parsed.scheme, str(parsed.hostname).lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def _compatible_image(values: dict[str, str], base_url: str | None = None) -> dict[str, str] | None:
    """Only offer the proven V2 protocol on the exact credential host."""
    candidates = list_profiles(include_secrets=True)
    legacy = _legacy_document(values)
    if legacy:
        candidates.insert(0, {**legacy, "api_keys": _legacy_keys(values)})
    for profile in candidates:
        keys = profile.get("api_keys") or []
        if not keys or profile.get("protocol") != "async_task":
            continue
        try:
            base = _validate_base(str(profile.get("base_url") or ""))
            parsed = urlsplit(base)
            if parsed.hostname not in _COMPATIBLE_HOSTS or parsed.scheme != "https" or parsed.path not in {"", "/"}:
                continue
            if base_url and _origin(base) != _origin(base_url):
                continue
            # Do not infer compatibility from a brand name alone. Both submit
            # and polling must be the same-origin standard V2 protocol.
            endpoints = [str(profile.get(name) or "") for name in ("text_endpoint", "query_endpoint")]
            valid = True
            for endpoint in endpoints:
                if endpoint.startswith(("https://", "http://")):
                    parts = urlsplit(endpoint)
                    if _origin(endpoint) != _origin(base) or parts.query or parts.fragment:
                        valid = False
                        break
                    endpoint = parts.path
                if not endpoint.startswith("/openapi/v2/") or "\\" in endpoint:
                    valid = False
                    break
            if valid:
                key = str(keys[0]).strip()
                if key and not any(char in key for char in ("\r", "\n")):
                    return {"base_url": base, "api_keys": _unique_keys([*keys])}
        except (ValueError, TypeError):
            continue
    return None


def load_config() -> dict[str, Any]:
    """Return runtime configuration; only dedicated keys may execute requests."""
    values = _values()
    keys = _unique_keys([values.get("VIDEO_API_KEY", ""), values.get("VIDEO_API_KEYS", "")])
    saved_base = str(values.get("VIDEO_API_BASE_URL") or "").strip()
    candidate = None if keys else _compatible_image(values, saved_base or None)
    base = _validate_base(saved_base or (candidate or {}).get("base_url") or DEFAULT_BASE_URL)
    submit_path = _validate_path(str(values.get("VIDEO_SUBMIT_PATH") or SUBMIT_PATH))
    query_path = _validate_path(str(values.get("VIDEO_QUERY_PATH") or QUERY_PATH))
    upload_path = _validate_path(str(values.get("VIDEO_UPLOAD_PATH") or UPLOAD_PATH))
    resolution = str(values.get("VIDEO_RESOLUTION") or "720p").strip().lower()
    if resolution not in {"480p", "720p"}:
        raise ValueError("视频原生分辨率只支持 480p 或 720p，请重新保存视频接口设置")
    if any(any(char in key for char in ("\r", "\n")) for key in keys):
        raise ValueError("视频 API Key 不能包含换行")
    # A manually injected key without its destination must not fall back to a
    # default host. Let the user complete the independent configuration first.
    usable_keys = keys if saved_base else []
    mode = str(values.get("VIDEO_CONCURRENCY_MODE") or "auto").lower()
    mode = mode if mode in {"auto", "manual"} else "auto"
    try: per_key = max(1, min(8, int(values.get("VIDEO_PER_KEY_CONCURRENCY") or 1)))
    except ValueError: per_key = 1
    try: total = max(1, min(32, int(values.get("VIDEO_TOTAL_CONCURRENCY") or 3)))
    except ValueError: total = 3
    capacity = len(usable_keys) * per_key
    effective = capacity if mode == "auto" else min(capacity, total)
    return {"base_url": base, "submit_path": submit_path, "query_path": query_path,
            "upload_path": upload_path, "resolution": resolution,
            "api_key": usable_keys[0] if usable_keys else "", "api_keys": usable_keys,
            "key_count": len(usable_keys), "key_hints": [f"••••{key[-4:]}" for key in usable_keys],
            "concurrency_mode": mode, "per_key_concurrency": per_key,
            "total_concurrency": total, "effective_concurrency": effective,
            "has_api_key": bool(usable_keys), "model_label": "多模态视频",
            "source": "dedicated" if usable_keys else "image_compatible" if candidate else "missing"}


def _public(config: dict[str, Any]) -> dict[str, Any]:
    return {key: config[key] for key in (
        "base_url", "submit_path", "query_path", "upload_path", "resolution", "has_api_key",
        "key_count", "key_hints", "concurrency_mode", "per_key_concurrency", "total_concurrency",
        "effective_concurrency", "model_label", "source")}


@router.get("")
def get_video_model(request: Request) -> dict[str, Any]:
    require_user(request)
    try:
        return _public(load_config())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("")
def save_video_model(payload: VideoModelRequest, request: Request) -> dict[str, Any]:
    require_user(request)
    try:
        base = _validate_base(payload.base_url)
        path = _validate_path(payload.submit_path)
        query_path = _validate_path(payload.query_path)
        upload_path = _validate_path(payload.upload_path)
        supplied = _unique_keys([str(payload.api_key or ""), *payload.api_keys])
        if any(any(char in key for char in ("\r", "\n")) for key in supplied):
            raise ValueError("视频 API Key 不能包含换行")
        if supplied and payload.use_image_credentials:
            raise ValueError("请在填写新视频 API Key 和复用已有凭据之间选择一种方式")
        with LOCK:
            values = _values()
            previous_keys = _unique_keys([values.get("VIDEO_API_KEY", ""), values.get("VIDEO_API_KEYS", "")])
            previous_base = str(values.get("VIDEO_API_BASE_URL") or "").strip()
            if payload.use_image_credentials:
                candidate = _compatible_image(values, base)
                if not candidate:
                    raise ValueError("此地址没有可安全复用的同站点图像接口凭据，请填写独立的视频 API Key")
                supplied = candidate["api_keys"]
            elif previous_keys:
                same_origin = bool(previous_base and _origin(previous_base) == _origin(base))
                if not supplied and not same_origin:
                    raise ValueError("修改视频接口站点时必须填写新 API Key，不能自动转发旧站点凭据")
                if same_origin:
                    supplied = _unique_keys([*previous_keys, *supplied])
            if not supplied:
                raise ValueError("请填写视频 API Key，或明确选择复用同站点图像接口凭据")
            save_project_env_values({"VIDEO_API_BASE_URL": base, "VIDEO_SUBMIT_PATH": path,
                                     "VIDEO_QUERY_PATH": query_path, "VIDEO_UPLOAD_PATH": upload_path,
                                     "VIDEO_API_KEY": supplied[0], "VIDEO_API_KEYS": ",".join(supplied[1:]),
                                     "VIDEO_RESOLUTION": payload.resolution,
                                     "VIDEO_CONCURRENCY_MODE": payload.concurrency_mode,
                                     "VIDEO_PER_KEY_CONCURRENCY": str(payload.per_key_concurrency),
                                     "VIDEO_TOTAL_CONCURRENCY": str(payload.total_concurrency)})
        return _public(load_config())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        # Avoid echoing raw storage exceptions that may include credential data.
        raise HTTPException(status_code=500, detail="保存视频接口设置失败，请检查配置文件写入权限") from exc


@router.delete("/keys/{index}")
def delete_video_key(index: int, request: Request) -> dict[str, Any]:
    require_user(request)
    with LOCK:
        values = _values()
        keys = _unique_keys([values.get("VIDEO_API_KEY", ""), values.get("VIDEO_API_KEYS", "")])
        if index < 0 or index >= len(keys):
            raise HTTPException(status_code=404, detail="视频 API Key 不存在")
        keys.pop(index)
        save_project_env_values({"VIDEO_API_KEY": keys[0] if keys else "",
                                 "VIDEO_API_KEYS": ",".join(keys[1:]) if keys else ""})
    return _public(load_config())
