"""Configurable ComfyUI bridge used by the standalone workbench.

The bridge deliberately stores connections separately from workflow profiles:
moving a workflow to another ComfyUI server must not invalidate its node map.
Only API-format workflows are executed; browser canvas JSON contains UI state but
not the complete prompt graph expected by ComfyUI's ``/prompt`` endpoint.
"""
from __future__ import annotations

import copy
import json
import math
import mimetypes
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import requests
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .auth import require_user


router = APIRouter(prefix="/api/comfyui")
ROOT = Path(__file__).resolve().parents[2] / "workspace" / "comfyui"
LOCK = threading.RLock()


DEFAULT_CONNECTION = {
    "name": "本机 ComfyUI",
    "base_url": "http://127.0.0.1:8188",
    "websocket_url": "",
    "prompt_path": "/prompt",
    "history_path": "/history/{prompt_id}",
    "upload_path": "/upload/image",
    "view_path": "/view",
    "interrupt_path": "/interrupt",
}


class ConnectionRequest(BaseModel):
    name: str = Field(default="本机 ComfyUI", min_length=1, max_length=80)
    base_url: str = Field(default="http://127.0.0.1:8188", min_length=1, max_length=2048)
    websocket_url: str = Field(default="", max_length=2048)
    prompt_path: str = Field(default="/prompt", min_length=1, max_length=512)
    history_path: str = Field(default="/history/{prompt_id}", min_length=1, max_length=512)
    upload_path: str = Field(default="/upload/image", min_length=1, max_length=512)
    view_path: str = Field(default="/view", min_length=1, max_length=512)
    interrupt_path: str = Field(default="/interrupt", min_length=1, max_length=512)


class Binding(BaseModel):
    node_id: str = Field(default="", max_length=80)
    input_name: str = Field(default="", max_length=120)


class WorkflowMappings(BaseModel):
    prompt: Binding = Field(default_factory=Binding)
    image: Binding = Field(default_factory=Binding)
    audio: Binding = Field(default_factory=Binding)
    duration: Binding = Field(default_factory=Binding)
    width: Binding = Field(default_factory=Binding)
    height: Binding = Field(default_factory=Binding)
    seed: Binding = Field(default_factory=Binding)
    output_node_id: str = Field(default="", max_length=80)
    duration_mode: Literal["seconds", "frames"] = "frames"
    fps: float = Field(default=24, gt=0, le=240)
    frame_divisor: int = Field(default=1, ge=1, le=1000)
    frame_remainder: int = Field(default=0, ge=0, le=999)


class ProfileRequest(BaseModel):
    id: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["image", "video", "upscale", "custom"] = "custom"
    workflow: dict[str, Any]
    mappings: WorkflowMappings = Field(default_factory=WorkflowMappings)
    resolution_preset: Literal["480p", "720p", "1080p", "custom"] = "720p"
    default_width: int = Field(default=1280, ge=32, le=8192)
    default_height: int = Field(default=720, ge=32, le=8192)
    default_seed: int = Field(default=-1, ge=-1, le=2**63 - 1)


def _user_root(user_id: int) -> Path:
    result = ROOT / str(user_id)
    result.mkdir(parents=True, exist_ok=True)
    return result


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return copy.deepcopy(fallback)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _connection(user_id: int) -> dict[str, Any]:
    return {**DEFAULT_CONNECTION, **_read_json(_user_root(user_id) / "connection.json", {})}


def _validate_connection(value: dict[str, Any]) -> dict[str, Any]:
    parsed = urlparse(str(value.get("base_url") or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ComfyUI 地址必须是完整的 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        raise ValueError("请勿把用户名或密码写进 ComfyUI 地址")
    result = dict(value)
    result["base_url"] = str(value["base_url"]).rstrip("/")
    for key in ("prompt_path", "history_path", "upload_path", "view_path", "interrupt_path"):
        path = str(value.get(key) or "").strip()
        if not path.startswith("/"):
            raise ValueError(f"{key} 必须以 / 开头")
        result[key] = path
    websocket = str(value.get("websocket_url") or "").strip()
    if websocket and urlparse(websocket).scheme not in {"ws", "wss"}:
        raise ValueError("WebSocket 地址必须以 ws:// 或 wss:// 开头")
    return result


def _url(connection: dict[str, Any], path: str) -> str:
    return urljoin(connection["base_url"] + "/", path.lstrip("/"))


def _profiles(user_id: int) -> list[dict[str, Any]]:
    values = _read_json(_user_root(user_id) / "profiles.json", [])
    return values if isinstance(values, list) else []


def video_profile(user_id: int, profile_id: str) -> dict[str, Any]:
    value = next((item for item in _profiles(user_id) if item.get("id") == profile_id), None)
    if not value:
        raise ValueError("所选 ComfyUI 工作流预设不存在，请回到画面编排重新选择")
    if value.get("kind") != "video":
        raise ValueError("所选 ComfyUI 预设不是跑视频工作流")
    _api_graph(value.get("workflow") or {})
    return value


def _api_graph(workflow: dict[str, Any]) -> dict[str, Any]:
    candidate = workflow.get("prompt") if isinstance(workflow.get("prompt"), dict) else workflow
    if "nodes" in candidate and isinstance(candidate.get("nodes"), list):
        raise ValueError("这是 ComfyUI 画布工作流。请在 ComfyUI 开启开发者模式后导出“API 格式”工作流再导入。")
    if not candidate or not all(isinstance(value, dict) and value.get("class_type") for value in candidate.values()):
        raise ValueError("无法识别工作流；需要 ComfyUI API 格式的 JSON")
    return candidate


def _node_summary(graph: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for node_id, node in graph.items():
        meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        result.append({
            "id": str(node_id),
            "class_type": str(node.get("class_type") or ""),
            "title": str(meta.get("title") or node.get("class_type") or node_id),
            # Linked inputs remain selectable: mapping width/height directly on
            # a sampler intentionally replaces an upstream resolution link.
            "inputs": [str(key) for key in inputs],
        })
    return result


def _binding(graph: dict[str, Any], data: Binding, label: str, *, required: bool = False) -> tuple[dict[str, Any], str] | None:
    if not data.node_id and not data.input_name and not required:
        return None
    node = graph.get(str(data.node_id))
    if not isinstance(node, dict):
        raise ValueError(f"{label}映射的节点 {data.node_id or '（空）'} 不存在")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or data.input_name not in inputs:
        raise ValueError(f"{label}映射的输入 {data.input_name or '（空）'} 不存在")
    return inputs, data.input_name


def _duration_value(seconds: float, mappings: WorkflowMappings) -> float | int:
    if mappings.duration_mode == "seconds":
        return round(seconds, 3)
    frames = max(1, math.ceil(seconds * mappings.fps))
    divisor = max(1, mappings.frame_divisor)
    remainder = mappings.frame_remainder % divisor
    frames += (remainder - frames) % divisor
    return frames


VIDEO_RESOLUTION_PRESETS: dict[str, tuple[int, int]] = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}


def _video_dimensions(profile: dict[str, Any], ratio: str) -> tuple[int, int]:
    """Resolve a profile preset and orient it for the current OCV project."""
    preset = str(profile.get("resolution_preset") or "").strip().lower()
    base_width, base_height = VIDEO_RESOLUTION_PRESETS.get(
        preset,
        (
            int(profile.get("default_width") or 1280),
            int(profile.get("default_height") or 720),
        ),
    )
    landscape = ratio != "9:16"
    return (
        (max(base_width, base_height), min(base_width, base_height))
        if landscape
        else (min(base_width, base_height), max(base_width, base_height))
    )


def _disconnect_optional_reference(graph: dict[str, Any], node_id: str) -> None:
    """Remove an unused loader and every graph input wired to its output."""
    node_id = str(node_id or "")
    if not node_id:
        return
    for node in graph.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for key, value in list(inputs.items()):
            if isinstance(value, list) and value and str(value[0]) == node_id:
                inputs.pop(key, None)
    graph.pop(node_id, None)


def _patch_graph(graph: dict[str, Any], mappings: WorkflowMappings, *, prompt: str, duration: float, width: int, height: int, seed: int, image_name: str = "", audio_name: str = "") -> dict[str, Any]:
    graph = copy.deepcopy(graph)
    values = (
        ("提示词", mappings.prompt, prompt),
        ("参考图", mappings.image, image_name),
        ("参考音频", mappings.audio, audio_name),
        ("时长", mappings.duration, _duration_value(duration, mappings)),
        ("宽度", mappings.width, width),
        ("高度", mappings.height, height),
        ("Seed", mappings.seed, seed),
    )
    for label, mapped, value in values:
        if label == "参考音频" and not value:
            _disconnect_optional_reference(graph, mapped.node_id)
            continue
        target = _binding(graph, mapped, label, required=label == "提示词")
        if target is not None:
            if label == "参考图" and not value:
                raise ValueError(f"当前预设需要{label}，请先提供")
            target[0][target[1]] = value
    if mappings.output_node_id and str(mappings.output_node_id) not in graph:
        raise ValueError(f"输出节点 {mappings.output_node_id} 不存在")
    return graph


def _find_output_file(history: dict[str, Any], output_node_id: str) -> dict[str, str] | None:
    outputs = history.get("outputs") if isinstance(history, dict) else None
    if not isinstance(outputs, dict):
        return None
    selected = outputs.get(str(output_node_id)) if output_node_id else None
    groups = [selected] if isinstance(selected, dict) else list(outputs.values())
    for group in groups:
        if not isinstance(group, dict):
            continue
        for key in ("gifs", "videos", "images", "audio"):
            for item in group.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    return {"filename": str(item["filename"]), "subfolder": str(item.get("subfolder") or ""), "type": str(item.get("type") or "output")}
    return None


def _history_payload(connection: dict[str, Any], prompt_id: str) -> dict[str, Any] | None:
    """Read one history item; a busy local server timeout is not task failure.

    Some large video workflows block ComfyUI's HTTP handler while loading or
    offloading models. Once ``prompt_id`` is known, retrying the status GET is
    safe while resubmitting the workflow is not.
    """
    history_path = connection["history_path"].replace("{prompt_id}", prompt_id)
    try:
        response = requests.get(_url(connection, history_path), timeout=(5, 30))
    except (requests.Timeout, requests.ConnectionError):
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _queued_prompt_ids(connection: dict[str, Any]) -> tuple[set[str], set[str]] | None:
    """Return running and pending prompt ids, or ``None`` when status is unknown."""
    try:
        response = requests.get(_url(connection, "/queue"), timeout=(5, 15))
        response.raise_for_status()
        payload = response.json()
    except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return set(), set()

    def identities(items: Any) -> set[str]:
        result: set[str] = set()
        for item in items or []:
            if isinstance(item, (list, tuple)) and len(item) > 1:
                result.add(str(item[1]))
            elif isinstance(item, dict):
                value = item.get("prompt_id") or item.get("promptId")
                if value:
                    result.add(str(value))
        return result

    return identities(payload.get("queue_running")), identities(payload.get("queue_pending"))


def video_task_state(user_id: int, prompt_id: str) -> str:
    """Inspect a persisted local task without ever submitting a replacement.

    ``missing`` is returned only after the task is absent from history and both
    queue buckets, followed by a second history read. This matters after a full
    ComfyUI restart: its in-memory queue/history can disappear while OCV still
    safely remembers the old prompt id.
    """
    prompt_id = str(prompt_id or "").strip()
    if not prompt_id:
        return "missing"
    connection = _connection(user_id)

    def history_state(payload: dict[str, Any] | None) -> str:
        if payload is None:
            return "unknown"
        history = payload.get(prompt_id) if isinstance(payload, dict) else None
        if not isinstance(history, dict):
            return "absent"
        status = history.get("status") or {}
        messages = status.get("messages") or []
        if any(isinstance(item, list) and item and item[0] == "execution_error" for item in messages):
            return "failed"
        if _find_output_file(history, ""):
            return "completed"
        return "history"

    first = history_state(_history_payload(connection, prompt_id))
    if first != "absent":
        return first
    queued = _queued_prompt_ids(connection)
    if queued is None:
        return "unknown"
    running, pending = queued
    if prompt_id in running:
        return "running"
    if prompt_id in pending:
        return "pending"
    # Close the small race where a task leaves the queue between the two GETs.
    second = history_state(_history_payload(connection, prompt_id))
    return "missing" if second == "absent" else second


def _job_path(user_id: int, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "任务不存在")
    return _user_root(user_id) / "jobs" / job_id


def _save_job(path: Path, value: dict[str, Any]) -> None:
    with LOCK:
        _write_json(path / "record.json", value)


def _upload_reference(connection: dict[str, Any], source: Path) -> str:
    with source.open("rb") as stream:
        response = requests.post(
            _url(connection, connection["upload_path"]),
            files={"image": (source.name, stream, mimetypes.guess_type(source.name)[0] or "application/octet-stream")},
            data={"type": "input", "overwrite": "true"}, timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    name = str(payload.get("name") or "")
    if not name:
        raise RuntimeError("ComfyUI 已接收图片，但没有返回文件名")
    subfolder = str(payload.get("subfolder") or "").strip("/\\")
    return f"{subfolder}/{name}" if subfolder else name


class ComfyUIStopped(RuntimeError):
    pass


def run_video_profile(user_id: int, profile_id: str, *, prompt: str, image_path: Path,
                      duration: float, ratio: str, output_path: Path, progress=None,
                      should_stop=None, seed: int = -1, existing_prompt_id: str = '',
                      on_submitted=None, audio_path: Path | None = None) -> None:
    """Run one configured video preset synchronously for a storyboard worker."""
    profile = video_profile(user_id, profile_id)
    connection = _connection(user_id)
    mappings = WorkflowMappings.model_validate(profile.get("mappings") or {})
    width, height = _video_dimensions(profile, ratio)
    actual_seed = seed if seed >= 0 else uuid.uuid4().int % (2**63 - 1)
    prompt_id = str(existing_prompt_id or '').strip()
    if prompt_id:
        if progress:
            progress(f"继续查询原 ComfyUI 任务 {prompt_id}，不会重复提交")
    else:
        if should_stop and should_stop():
            raise ComfyUIStopped("尚未提交，已停止本地生成。")
        if progress:
            progress("正在向 ComfyUI 上传核心分镜图与本镜参考配音" if audio_path else "正在向 ComfyUI 上传核心分镜图")
        image_name = _upload_reference(connection, image_path)
        audio_name = _upload_reference(connection, audio_path) if audio_path else ""
        graph = _patch_graph(_api_graph(profile["workflow"]), mappings, prompt=prompt,
                             duration=duration, width=width, height=height,
                             seed=actual_seed, image_name=image_name, audio_name=audio_name)
        client_id = "ocv-video-" + uuid.uuid4().hex
        response = requests.post(_url(connection, connection["prompt_path"]),
                                 json={"prompt": graph, "client_id": client_id}, timeout=60)
        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(f"ComfyUI 拒绝工作流：{detail}")
        prompt_id = str(response.json().get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("ComfyUI 未返回 prompt_id")
        if on_submitted:
            on_submitted(prompt_id)
        if progress:
            progress(f"ComfyUI 已接收任务 {prompt_id}，正在生成")
    deadline = time.monotonic() + 7200
    interrupted = False
    timeout_notified = False
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            if not interrupted:
                try:
                    requests.post(_url(connection, connection["interrupt_path"]), timeout=10).raise_for_status()
                finally:
                    interrupted = True
            raise ComfyUIStopped("已请求终止 ComfyUI 当前任务。")
        payload = _history_payload(connection, prompt_id)
        if payload is None:
            if progress and not timeout_notified:
                progress("ComfyUI 正忙，状态查询超过 30 秒；任务仍在服务端运行，OCV 将继续等待且不会重复提交")
            timeout_notified = True
            time.sleep(2)
            continue
        history = payload.get(prompt_id) if isinstance(payload, dict) else None
        if isinstance(history, dict):
            status = history.get("status") or {}
            errors = [item for item in status.get("messages") or []
                      if isinstance(item, list) and item and item[0] == "execution_error"]
            if errors:
                raise RuntimeError("ComfyUI 执行失败：" + str(errors[-1][1]))
            result = _find_output_file(history, mappings.output_node_id)
            if result:
                downloaded = requests.get(_url(connection, connection["view_path"]),
                                          params=result, timeout=300)
                downloaded.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(downloaded.content)
                if not output_path.is_file() or output_path.stat().st_size <= 0:
                    raise RuntimeError("ComfyUI 返回了空的视频文件")
                if progress:
                    progress("ComfyUI 视频已下载到当前镜头")
                return
        time.sleep(2)
    raise TimeoutError("等待 ComfyUI 视频结果超时")


def _execute(user_id: int, job_id: str, profile: dict[str, Any], source: Path | None,
             audio_source: Path | None, values: dict[str, Any]) -> None:
    path = _job_path(user_id, job_id)
    record = _read_json(path / "record.json", {})
    try:
        connection = _connection(user_id)
        record.update(message="正在上传参考素材" if source or audio_source else "正在提交工作流", progress=5)
        _save_job(path, record)
        image_name = _upload_reference(connection, source) if source else ""
        audio_name = _upload_reference(connection, audio_source) if audio_source else ""
        mappings = WorkflowMappings.model_validate(profile.get("mappings") or {})
        graph = _patch_graph(_api_graph(profile["workflow"]), mappings, image_name=image_name,
                             audio_name=audio_name, **values)
        client_id = "ocv-" + uuid.uuid4().hex
        response = requests.post(_url(connection, connection["prompt_path"]), json={"prompt": graph, "client_id": client_id}, timeout=60)
        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(f"ComfyUI 拒绝工作流：{detail}")
        payload = response.json()
        prompt_id = str(payload.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("ComfyUI 未返回 prompt_id")
        record.update(remote_prompt_id=prompt_id, message="ComfyUI 已接收，正在生成", progress=15)
        _save_job(path, record)
        deadline = time.monotonic() + 7200
        timeout_notified = False
        while time.monotonic() < deadline:
            payload = _history_payload(connection, prompt_id)
            if payload is None:
                if not timeout_notified:
                    record.update(message="ComfyUI 正忙，状态查询超时；任务仍在运行，继续等待", progress=max(15, record.get("progress", 0)))
                    _save_job(path, record)
                timeout_notified = True
                time.sleep(2)
                continue
            history = payload.get(prompt_id) if isinstance(payload, dict) else None
            if isinstance(history, dict):
                status = history.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False and status.get("messages"):
                    errors = [item for item in status.get("messages") or [] if isinstance(item, list) and item and item[0] == "execution_error"]
                    if errors:
                        raise RuntimeError("ComfyUI 执行失败：" + str(errors[-1][1]))
                output = _find_output_file(history, mappings.output_node_id)
                if output:
                    downloaded = requests.get(_url(connection, connection["view_path"]), params=output, timeout=300)
                    downloaded.raise_for_status()
                    suffix = Path(output["filename"]).suffix.lower() or ".bin"
                    destination = path / ("result" + suffix)
                    destination.write_bytes(downloaded.content)
                    record.update(status="completed", message="生成完成", progress=100, result_name=destination.name)
                    _save_job(path, record)
                    return
            time.sleep(2)
        raise TimeoutError("等待 ComfyUI 结果超时")
    except Exception as exc:
        record.update(status="failed", message=str(exc), progress=record.get("progress", 0))
        _save_job(path, record)


@router.get("")
def overview(request: Request) -> dict[str, Any]:
    user_id = int(require_user(request)["id"])
    connection = _connection(user_id)
    profiles = _profiles(user_id)
    return {"connection": connection, "profiles": [{**value, "workflow": None, "nodes": _node_summary(_api_graph(value["workflow"]))} for value in profiles]}


@router.put("/connection")
def save_connection(payload: ConnectionRequest, request: Request) -> dict[str, Any]:
    user_id = int(require_user(request)["id"])
    value = _validate_connection(payload.model_dump())
    _write_json(_user_root(user_id) / "connection.json", value)
    return value


@router.post("/connection/test")
def test_connection(payload: ConnectionRequest, request: Request) -> dict[str, Any]:
    require_user(request)
    value = _validate_connection(payload.model_dump())
    started = time.monotonic()
    try:
        response = requests.get(_url(value, "/system_stats"), timeout=8)
        response.raise_for_status()
        stats = response.json()
        nodes = requests.get(_url(value, "/object_info"), timeout=15)
        nodes.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(400, f"无法连接 ComfyUI：{exc}") from exc
    return {"ok": True, "latency_ms": round((time.monotonic() - started) * 1000), "node_count": len(nodes.json()), "system": stats.get("system", {})}


@router.put("/profiles")
def save_profile(payload: ProfileRequest, request: Request) -> dict[str, Any]:
    user_id = int(require_user(request)["id"])
    try:
        graph = _api_graph(payload.workflow)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    profile_id = payload.id if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", payload.id or "") else uuid.uuid4().hex[:16]
    value = {**payload.model_dump(), "id": profile_id, "workflow": graph, "updated_at": time.time()}
    # Validate every configured binding before persisting a broken preset.
    mappings = payload.mappings
    for label, binding in (("提示词", mappings.prompt), ("参考图", mappings.image), ("参考音频", mappings.audio), ("时长", mappings.duration), ("宽度", mappings.width), ("高度", mappings.height), ("Seed", mappings.seed)):
        try:
            _binding(graph, binding, label, required=label == "提示词")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if mappings.output_node_id and mappings.output_node_id not in graph:
        raise HTTPException(400, f"输出节点 {mappings.output_node_id} 不存在")
    values = _profiles(user_id)
    values = [value] + [item for item in values if item.get("id") != profile_id]
    _write_json(_user_root(user_id) / "profiles.json", values[:50])
    return {**value, "workflow": None, "nodes": _node_summary(graph)}


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: str, request: Request) -> dict[str, Any]:
    user_id = int(require_user(request)["id"])
    value = next((item for item in _profiles(user_id) if item.get("id") == profile_id), None)
    if not value:
        raise HTTPException(404, "工作流预设不存在")
    return {**value, "nodes": _node_summary(_api_graph(value["workflow"]))}


@router.post("/capture-latest")
def capture_latest(request: Request) -> dict[str, Any]:
    """Read the last queued graph, avoiding a manual API-workflow export."""
    user_id = int(require_user(request)["id"])
    connection = _connection(user_id)
    collection_path = connection["history_path"].split("/{prompt_id}", 1)[0].rstrip("/") or "/history"
    try:
        response = requests.get(_url(connection, collection_path), params={"max_items": 30}, timeout=30)
        response.raise_for_status()
        history = response.json()
    except requests.RequestException as exc:
        raise HTTPException(400, f"读取 ComfyUI 历史失败：{exc}") from exc
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for prompt_id, item in (history.items() if isinstance(history, dict) else []):
        prompt = item.get("prompt") if isinstance(item, dict) else None
        graph = prompt[2] if isinstance(prompt, list) and len(prompt) > 2 and isinstance(prompt[2], dict) else None
        if graph:
            sequence = float(prompt[0]) if isinstance(prompt[0], (int, float)) else 0
            candidates.append((sequence, str(prompt_id), graph))
    if not candidates:
        raise HTTPException(404, "ComfyUI 暂无可读取的执行历史；请先在 ComfyUI 成功提交一次工作流")
    sequence, prompt_id, graph = max(candidates, key=lambda item: item[0])
    return {"prompt_id": prompt_id, "sequence": sequence, "workflow": graph, "nodes": _node_summary(graph)}


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request) -> dict[str, bool]:
    user_id = int(require_user(request)["id"])
    values = _profiles(user_id)
    remaining = [value for value in values if value.get("id") != profile_id]
    if len(remaining) == len(values):
        raise HTTPException(404, "工作流预设不存在")
    _write_json(_user_root(user_id) / "profiles.json", remaining)
    return {"deleted": True}


@router.post("/jobs")
async def create_job(request: Request, profile_id: str = Form(...), prompt: str = Form(...), duration: float = Form(5), width: int = Form(1280), height: int = Form(720), seed: int = Form(-1), reference: UploadFile | None = File(default=None), reference_audio: UploadFile | None = File(default=None)) -> dict[str, Any]:
    user_id = int(require_user(request)["id"])
    profile = next((value for value in _profiles(user_id) if value.get("id") == profile_id), None)
    if not profile:
        raise HTTPException(404, "工作流预设不存在")
    if not prompt.strip():
        raise HTTPException(400, "请填写提示词")
    job_id = uuid.uuid4().hex
    path = _job_path(user_id, job_id)
    path.mkdir(parents=True, exist_ok=False)
    source = None
    if reference is not None and reference.filename:
        suffix = Path(reference.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(400, "参考图仅支持 PNG、JPG 和 WebP")
        source = path / ("reference" + suffix)
        with source.open("wb") as stream:
            shutil.copyfileobj(reference.file, stream)
    audio_source = None
    if reference_audio is not None and reference_audio.filename:
        suffix = Path(reference_audio.filename).suffix.lower()
        if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
            raise HTTPException(400, "参考音频仅支持 WAV、MP3、FLAC、M4A 和 OGG")
        audio_source = path / ("reference_audio" + suffix)
        with audio_source.open("wb") as stream:
            shutil.copyfileobj(reference_audio.file, stream)
    actual_seed = seed if seed >= 0 else uuid.uuid4().int % (2**63 - 1)
    record = {"id": job_id, "profile_id": profile_id, "profile_name": profile.get("name"), "status": "running", "message": "准备提交", "progress": 0, "created_at": time.time(), "prompt": prompt, "duration": duration, "width": width, "height": height, "seed": actual_seed}
    _save_job(path, record)
    threading.Thread(target=_execute, args=(user_id, job_id, profile, source, audio_source, {"prompt": prompt, "duration": max(.1, duration), "width": max(32, width), "height": max(32, height), "seed": actual_seed}), daemon=True).start()
    return record


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    user_id = int(require_user(request)["id"])
    record = _read_json(_job_path(user_id, job_id) / "record.json", None)
    if not record:
        raise HTTPException(404, "任务不存在")
    if record.get("result_name"):
        record["result_url"] = f"/api/comfyui/jobs/{job_id}/result"
    return record


@router.get("/jobs/{job_id}/result")
def get_job_result(job_id: str, request: Request) -> FileResponse:
    user_id = int(require_user(request)["id"])
    path = _job_path(user_id, job_id)
    record = _read_json(path / "record.json", None)
    result = path / str((record or {}).get("result_name") or "")
    if not result.is_file() or result.parent != path:
        raise HTTPException(404, "生成结果不存在")
    return FileResponse(result, filename=result.name, headers={"Cache-Control": "no-store"})


@router.post("/interrupt")
def interrupt(request: Request) -> dict[str, bool]:
    user_id = int(require_user(request)["id"])
    connection = _connection(user_id)
    try:
        response = requests.post(_url(connection, connection["interrupt_path"]), timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(400, f"终止请求失败：{exc}") from exc
    return {"interrupted": True}
