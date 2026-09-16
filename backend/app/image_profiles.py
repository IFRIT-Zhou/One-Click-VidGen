"""User-managed image model profiles with legacy configuration compatibility."""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_user
from .config import ENV_PATH, _parse_env_lines, save_project_env_values


router = APIRouter(prefix="/api/image-profiles")
PROFILE_PATH = Path(__file__).resolve().parents[2] / "workspace" / "settings" / "image_profiles.json"
LOCK = threading.RLock()
LEGACY_PROFILE_ID = "legacy-image-api"
SUPPORTED_RESOLUTIONS = {"1k", "2k", "4k"}


class ImageProfileRequest(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=60)
    protocol: Literal["async_task"] = "async_task"
    base_url: str = Field(min_length=1, max_length=2048)
    model_id: str = Field(min_length=1, max_length=256)
    text_endpoint: str = Field(default="/openapi/v2/{model}/text-to-image", max_length=512)
    reference_endpoint: str = Field(default="/openapi/v2/{model}/image-to-image", max_length=512)
    query_endpoint: str = Field(default="/openapi/v2/query", max_length=512)
    resolutions: list[Literal["1k", "2k", "4k"]] = Field(default_factory=lambda: ["1k", "2k", "4k"])
    reference_images: bool = True
    api_key: str | None = Field(default=None, max_length=2048)
    api_keys: list[str] = Field(default_factory=list, max_length=10)


def _read_documents() -> list[dict[str, Any]]:
    if not PROFILE_PATH.is_file():
        return []
    try:
        payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    return [item for item in payload.get("profiles", []) if isinstance(item, dict)] if isinstance(payload, dict) else []


def _write_documents(documents: list[dict[str, Any]]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROFILE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "profiles": documents}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(PROFILE_PATH)


def _secret_name(profile_id: str) -> str:
    return "OCV_IMAGE_PROFILE_KEYS_" + re.sub(r"[^A-Za-z0-9]", "_", profile_id).upper()


def _unique_keys(values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        for value in re.split(r"[,;\s]+", str(raw or "")):
            value = value.strip()
            if value and value not in result:
                result.append(value)
    return result


def _legacy_keys(values: dict[str, str]) -> list[str]:
    candidates = [values.get("RUNNINGHUB_API_KEY", ""), values.get("RUNNINGHUB_API_KEYS", "")]
    candidates.extend(value for name, value in sorted(values.items()) if re.fullmatch(r"RUNNINGHUB_API_KEY_?\d+", name))
    return _unique_keys(candidates)


def _legacy_document(values: dict[str, str]) -> dict[str, Any] | None:
    keys = _legacy_keys(values)
    base_url = str(values.get("IMAGE_API_BASE_URL") or values.get("RUNNINGHUB_BASE_URL") or "").strip().rstrip("/")
    if not keys or not base_url:
        return None
    model = str(values.get("IMAGE_MODEL_ID") or values.get("RUNNINGHUB_IMAGE_MODEL") or "").strip()
    if not model:
        return None
    endpoint = str(values.get("RUNNINGHUB_ENDPOINT") or "/{model}/text-to-image").strip()
    endpoint = endpoint.replace(model, "{model}") if model in endpoint else endpoint
    if endpoint.startswith("/") and not endpoint.startswith("/openapi/"):
        endpoint = "/openapi/v2" + endpoint
    return {
        "id": LEGACY_PROFILE_ID,
        "name": "已有图像接口",
        "protocol": "async_task",
        "base_url": base_url,
        "model_id": model,
        "text_endpoint": endpoint,
        "reference_endpoint": str(values.get("RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT") or endpoint.replace("text-to-image", "image-to-image")),
        "query_endpoint": "/openapi/v2/query",
        "resolutions": ["1k", "2k", "4k"],
        "reference_images": True,
        "legacy": True,
    }


def _validate_url(value: str) -> str:
    cleaned = str(value or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API Base URL 必须是有效的 http(s) 地址，且不能包含账号、查询参数或锚点")
    return cleaned


def _validate_endpoint(value: str, label: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned or "\n" in cleaned or "\r" in cleaned:
        raise ValueError(f"{label}不能为空或包含换行")
    if cleaned.startswith(("http://", "https://")):
        _validate_url(cleaned)
    elif not cleaned.startswith("/"):
        raise ValueError(f"{label}应填写以 / 开头的路径或完整 http(s) 地址")
    return cleaned


def list_profiles(*, include_secrets: bool = False) -> list[dict[str, Any]]:
    with LOCK:
        documents = _read_documents()
        env = _parse_env_lines(ENV_PATH)
        legacy = _legacy_document(env)
        if legacy and not any(item.get("id") == LEGACY_PROFILE_ID for item in documents):
            documents.insert(0, legacy)
        result = []
        for document in documents:
            profile = dict(document)
            keys = _legacy_keys(env) if profile.get("legacy") else _unique_keys([env.get(_secret_name(str(profile.get("id") or "")), "")])
            profile["configured"] = bool(keys and profile.get("base_url") and profile.get("model_id"))
            profile["key_count"] = len(keys)
            profile["key_hints"] = [f"••••{key[-4:]}" if len(key) >= 4 else "••••" for key in keys]
            if include_secrets:
                profile["api_keys"] = keys
            result.append(profile)
        return result


def profile_by_id(profile_id: str, *, require_configured: bool = True) -> dict[str, Any]:
    profile = next((item for item in list_profiles(include_secrets=True) if item.get("id") == profile_id), None)
    if not profile:
        raise ValueError("所选图像模型配置不存在，请在接口与服务中重新选择")
    if require_configured and not profile.get("configured"):
        raise ValueError("所选图像模型配置尚未填写有效 API Key")
    return profile


def profile_snapshot(profile_id: str, resolution: str | None = None) -> dict[str, Any]:
    profile = profile_by_id(profile_id)
    selected = str(resolution or "").strip().lower() or str((profile.get("resolutions") or ["1k"])[0]).lower()
    if selected not in profile.get("resolutions", []):
        raise ValueError(f"所选配置不支持 {selected.upper()} 分辨率")
    return {key: profile.get(key) for key in (
        "id", "name", "protocol", "base_url", "model_id", "text_endpoint",
        "reference_endpoint", "query_endpoint", "reference_images",
    )} | {"resolution": selected}


def profile_environment(snapshot: dict[str, Any]) -> dict[str, str]:
    configs = profile_provider_configs(snapshot)
    first = configs[0]
    return {
        "OCV_IMAGE_PROFILE_ACTIVE": "1",
        "IMAGE_API_BASE_URL": str(first["base_url"]),
        "IMAGE_MODEL_ID": str(first["model"]),
        "IMAGE_RESOLUTION": str(first["resolution"]),
        "RUNNINGHUB_BASE_URL": str(first["base_url"]),
        "RUNNINGHUB_API_KEY": str(first["api_key"]),
        "RUNNINGHUB_API_KEYS": ",".join(str(config["api_key"]) for config in configs[1:]),
        "RUNNINGHUB_ENDPOINT": str(first["endpoint"]),
        "RUNNINGHUB_IMAGE_TO_IMAGE_ENDPOINT": str(first["reference_endpoint"]),
        "OCV_IMAGE_QUERY_URL": str(first["query_url"]),
    }


def profile_provider_configs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve a safe task snapshot with the profile's current credentials."""
    profile = profile_by_id(str(snapshot.get("id") or ""))
    resolution = str(snapshot.get("resolution") or "1k").lower()
    model = str(snapshot.get("model_id") or profile.get("model_id") or "").strip()
    base = str(snapshot.get("base_url") or profile.get("base_url") or "").rstrip("/")
    def absolute(endpoint: str) -> str:
        value = endpoint.replace("{model}", model)
        return value if value.startswith(("http://", "https://")) else f"{base}/{value.lstrip('/')}"
    common = {
        "base_url": base,
        "model": model,
        "resolution": resolution,
        "endpoint": absolute(str(snapshot.get("text_endpoint") or profile.get("text_endpoint"))),
        "reference_endpoint": absolute(str(snapshot.get("reference_endpoint") or profile.get("reference_endpoint"))),
        "query_url": absolute(str(snapshot.get("query_endpoint") or profile.get("query_endpoint"))),
        "reference_images": snapshot.get("reference_images", profile.get("reference_images", True)),
    }
    return [
        {**common, "api_key": str(key), "account_label": f"账号 {index}"}
        for index, key in enumerate(profile.get("api_keys") or [], 1)
    ]


@router.get("")
def get_profiles(request: Request) -> dict[str, Any]:
    require_user(request)
    return {"profiles": list_profiles()}


@router.put("")
def save_profile(payload: ImageProfileRequest, request: Request) -> dict[str, Any]:
    require_user(request)
    try:
        base_url = _validate_url(payload.base_url)
        model_id = payload.model_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]+", model_id) or ".." in model_id:
            raise ValueError("模型 ID 包含不支持的字符")
        profile_id = str(payload.id or uuid.uuid4().hex[:12]).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile_id):
            raise ValueError("配置 ID 无效")
        resolutions = list(dict.fromkeys(value.lower() for value in payload.resolutions))
        if not resolutions or any(value not in SUPPORTED_RESOLUTIONS for value in resolutions):
            raise ValueError("请至少选择一种受支持的分辨率")
        document = {
            "id": profile_id, "name": payload.name.strip(), "protocol": payload.protocol,
            "base_url": base_url, "model_id": model_id,
            "text_endpoint": _validate_endpoint(payload.text_endpoint, "文生图路径"),
            "reference_endpoint": _validate_endpoint(payload.reference_endpoint, "参考生图路径"),
            "query_endpoint": _validate_endpoint(payload.query_endpoint, "状态查询路径"),
            "resolutions": resolutions, "reference_images": bool(payload.reference_images),
        }
        with LOCK:
            documents = _read_documents()
            index = next((i for i, item in enumerate(documents) if item.get("id") == profile_id), -1)
            if index >= 0:
                document["legacy"] = bool(documents[index].get("legacy"))
                documents[index] = document
            else:
                document["legacy"] = profile_id == LEGACY_PROFILE_ID
                documents.append(document)
            _write_documents(documents)
            supplied = _unique_keys([str(payload.api_key or ""), *payload.api_keys])
            if supplied:
                if document.get("legacy"):
                    save_project_env_values({"RUNNINGHUB_API_KEY": supplied[0], "RUNNINGHUB_API_KEYS": ",".join(supplied[1:])})
                else:
                    save_project_env_values({_secret_name(profile_id): ",".join(supplied)})
        saved_profile = profile_by_id(profile_id, require_configured=False)
        saved_profile.pop("api_keys", None)
        return {"ok": True, "message": "图像模型配置已保存", "profile": saved_profile}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{profile_id}")
def delete_profile(profile_id: str, request: Request) -> dict[str, Any]:
    require_user(request)
    with LOCK:
        documents = _read_documents()
        retained = [item for item in documents if item.get("id") != profile_id]
        if len(retained) == len(documents):
            raise HTTPException(status_code=404, detail="图像模型配置不存在")
        _write_documents(retained)
        save_project_env_values({_secret_name(profile_id): ""})
    return {"ok": True, "message": "图像模型配置已删除"}
