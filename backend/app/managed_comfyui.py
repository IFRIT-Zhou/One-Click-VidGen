"""OCV-owned, optional ComfyUI runtime. Never manages an external installation."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import uuid

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_user

PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT / "comfyui" / "engine"
DATA = PROJECT / "workspace" / "managed_comfyui"
LOCK = threading.RLock()
START_LOCK = threading.Lock()
PROCESS = None
LOG_HANDLE = None
STATE = "stopped"
ERROR = ""
PORT = 0
LEASES = 0
LOADED_NODES = None
router = APIRouter(prefix="/api/comfyui/managed")


def maintenance_enabled():
    return os.environ.get("OCV_COMFYUI_MAINTAINER", "").strip().lower() in {"1", "true", "yes"}


def builtin_profile():
    """Compatibility accessor for the original fast-sampling profile."""
    profiles = builtin_profiles()
    return next((p for p in profiles if p["id"] == "ocv-h3-managed-v1"), profiles[0] if profiles else None)


def builtin_profiles():
    release, manifest = runtime_info()
    if release is None:
        return []
    if manifest.get("profiles") == "profiles.json":
        profiles = read_json(release / "profiles.json", [])
    else:
        profiles = [read_json(release / "profile.json", None)]
    if not isinstance(profiles, list):
        return []
    result, seen = [], set()
    for profile in profiles:
        if not isinstance(profile, dict) or not isinstance(profile.get("id"), str) or not profile["id"] or profile["id"] in seen:
            continue
        seen.add(profile["id"])
        from .presenter_mode import with_optional_h3_audio
        profile = with_optional_h3_audio(profile)
        result.append({**profile, "engine": "managed", "managed_builtin": True,
                       "component_version": manifest["version"]})
    return result


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def runtime_info():
    # Older optional packages remain usable until the local migration is run.
    runtime = RUNTIME
    if not (runtime / "active.json").exists():
        runtime = PROJECT / "runtime" / "comfyui"
    active = read_json(runtime / "active.json", {})
    version = str(active.get("version") or "")
    if not version or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in version) or version in {".", ".."}:
        return None, {}
    release = runtime / "releases" / version
    manifest = read_json(release / "component.json", {})
    if manifest.get("version") != version or not (release / "python" / "python.exe").is_file() or not (release / "ComfyUI" / "main.py").is_file():
        return None, {}
    return release, manifest


def model_roots():
    config = read_json(DATA / "settings.json", {})
    roots = [PROJECT / "models" / "comfyui", RUNTIME / "models", PROJECT / "runtime" / "comfyui" / "models"]
    if config.get("model_directory"):
        roots.append(Path(config["model_directory"]))
    return roots


def model_inventory(manifest):
    result = []
    for item in manifest.get("models", []):
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("组件模型清单包含非法路径")
        candidates = [root / relative for root in model_roots() if (root / relative).is_file()]
        # Match ComfyUI's search order; an incomplete earlier copy must not be
        # reported ready merely because a valid duplicate exists elsewhere.
        found = candidates[0] if candidates else None
        size = found.stat().st_size if found else 0
        result.append({"name": relative.name, "path": relative.as_posix(), "bytes": item.get("bytes", 0),
                       "actual_bytes": size, "found_path": str(found or ""),
                       "install_path": str(PROJECT / "models" / "comfyui" / relative),
                       "ready": bool(found and size > 0 and (not item.get("bytes") or size == item["bytes"]))})
    return result


def status():
    global STATE, ERROR
    with LOCK:
        if PROCESS is not None and PROCESS.poll() is not None and STATE in {"starting", "ready"}:
            STATE = "failed"
            ERROR = f"内置引擎已退出（代码 {PROCESS.returncode}），请查看引擎日志。"
        release, manifest = runtime_info()
        models = model_inventory(manifest)
        ready_paths = {item["path"] for item in models if item["ready"]}
        ready_profiles = [p["id"] for p in builtin_profiles() if p.get("required_models") and set(p["required_models"]).issubset(ready_paths)]
        return {"installed": release is not None, "version": manifest.get("version", ""),
                "maintenance_enabled": maintenance_enabled(),
                "state": STATE, "error": ERROR, "active_tasks": LEASES, "models": models,
                "models_ready": bool(models) and all(item["ready"] for item in models),
                "ready_profiles": ready_profiles, "any_models_ready": bool(ready_profiles),
                "model_directory": read_json(DATA / "settings.json", {}).get("model_directory", ""),
                "base_url": f"http://127.0.0.1:{PORT}" if PORT else "",
                "log_available": (DATA / "engine.log").exists()}


def node_status():
    directory = PROJECT / 'comfyui' / 'custom_nodes'
    profiles = []
    for profile in builtin_profiles():
        required = {n['class_type'] for n in profile.get('workflow', {}).values()}
        missing = sorted(required - LOADED_NODES) if LOADED_NODES is not None and STATE == 'ready' else []
        profiles.append({'id': profile['id'], 'name': profile.get('name', profile['id']),
                         'checked': LOADED_NODES is not None and STATE == 'ready', 'missing_nodes': missing})
    return {'directory': str(directory), 'profiles': profiles,
            'installed_folders': sorted(p.name for p in directory.iterdir() if p.is_dir() and not p.name.startswith('.')) if directory.is_dir() else [],
            'selflift_url': 'https://github.com/facok/comfyui-SelfLift',
            'restart_required': STATE in {'starting', 'ready'}}


def _extra_paths():
    # JSON is valid YAML; no string interpolation into YAML or shell commands.
    categories = ("checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "latent_upscale_models", "upscale_models", "embeddings", "clip_vision")
    result = {}
    for index, root in enumerate(model_roots()):
        root.mkdir(parents=True, exist_ok=True) if index == 0 else None
        result[f"ocv_models_{index}"] = {"base_path": str(root.resolve()), **{name: name for name in categories}}
    plugins = PROJECT / 'comfyui' / 'custom_nodes'
    plugins.mkdir(parents=True, exist_ok=True)
    result['ocv_user_nodes'] = {'custom_nodes': str(plugins.resolve())}
    legacy_plugins = PROJECT / 'comfyui_plugins'
    if legacy_plugins.is_dir():
        result['ocv_legacy_nodes'] = {'custom_nodes': str(legacy_plugins.resolve())}
    write_json(DATA / "model_paths.yaml", result)


def verify_core_overrides(release, manifest):
    for entry in manifest.get("overrides", []):
        relative = Path(entry["path"])
        core = (release / "ComfyUI").resolve()
        target = (core / relative).resolve()
        if not target.is_relative_to(core) or not target.is_file():
            raise RuntimeError("内置引擎兼容补丁缺失或路径异常，请修复引擎组件。")
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry.get("sha256"):
            raise RuntimeError("内置 H3 兼容补丁被修改或覆盖，请修复引擎组件后再运行。")


def ensure_ready(timeout=180, required_models=None):
    global PROCESS, LOG_HANDLE, STATE, ERROR, PORT, LOADED_NODES
    with START_LOCK:
        release, manifest = runtime_info()
        if release is None:
            raise RuntimeError("尚未安装 OCV 内置视频引擎组件；仍可使用外部 ComfyUI 或视频 API。")
        verify_core_overrides(release, manifest)
        inventory = model_inventory(manifest)
        if required_models is not None:
            known = {item["path"] for item in inventory}
            if set(required_models) - known:
                raise RuntimeError("内置工作流模型清单与组件不一致，请更新引擎组件。")
            inventory = [item for item in inventory if item["path"] in required_models]
        missing = [item["name"] for item in inventory if not item["ready"]]
        if missing:
            raise RuntimeError("H3 模型缺失或不完整：" + "、".join(missing) + "。请在生成设置的「模型安装指引」查看下载入口和存放目录，补齐后重新检查。")
        if status()["state"] == "ready":
            return status()["base_url"]
        DATA.mkdir(parents=True, exist_ok=True)
        for name in ("input", "output", "user", "cache", "temp"):
            (DATA / name).mkdir(exist_ok=True)
        _extra_paths()
        # Bind only loopback. Never reuse/terminate whatever occupies a port.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(key, None)
        env.update(PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HOME=str(DATA / "cache"),
                   TORCH_HOME=str(DATA / "cache" / "torch"), TEMP=str(DATA / "temp"), TMP=str(DATA / "temp"))
        env.update(TRITON_CACHE_DIR=str(DATA / "cache" / "triton"),
                   TORCHINDUCTOR_CACHE_DIR=str(DATA / "cache" / "inductor"),
                   NUMBA_CACHE_DIR=str(DATA / "cache" / "numba"),
                   CUDA_CACHE_PATH=str(DATA / "cache" / "cuda"))
        ffmpeg = PROJECT / "tools" / "ffmpeg" / "bin"
        env["PATH"] = str(release / "python") + os.pathsep + str(ffmpeg) + os.pathsep + env.get("PATH", "")
        command = [str(release / "python" / "python.exe"), "-s", str(PROJECT / "backend" / "app" / "managed_comfyui_worker.py"),
                   str(os.getpid()), str(release / "ComfyUI"), "--listen", "127.0.0.1", "--port", str(port),
                   "--disable-auto-launch", "--extra-model-paths-config", str(DATA / "model_paths.yaml"),
                   "--input-directory", str(DATA / "input"), "--output-directory", str(DATA / "output"),
                   "--user-directory", str(DATA / "user"), "--database-url", "sqlite:///" + (DATA / "comfy.db").as_posix()]
        with LOCK:
            STATE, ERROR, PORT = "starting", "", port
            if LOG_HANDLE:
                LOG_HANDLE.close()
            LOG_HANDLE = (DATA / "engine.log").open("ab")
            try:
                PROCESS = subprocess.Popen(command, cwd=release / "ComfyUI", env=env, stdin=subprocess.DEVNULL,
                                           stdout=LOG_HANDLE, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                LOG_HANDLE.close()
                LOG_HANDLE = None
                STATE = "failed"
                raise
        session = requests.Session()
        session.trust_env = False
        url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if PROCESS.poll() is not None:
                    raise RuntimeError("内置 ComfyUI 启动失败，请查看引擎日志。")
                try:
                    response = session.get(url + "/system_stats", timeout=2)
                    response.raise_for_status()
                    node_response = session.get(url + "/object_info", timeout=30)
                    node_response.raise_for_status()
                    # Optional workflows must not prevent the engine or other
                    # workflows from starting. Validate the selected graph at execution.
                    LOADED_NODES = set(node_response.json())
                    if PROCESS.poll() is not None:
                        raise RuntimeError("内置引擎启动过程中退出。")
                    with LOCK:
                        STATE = "ready"
                    return url
                except requests.RequestException:
                    time.sleep(.5)
            raise RuntimeError("内置引擎启动超时，请查看日志检查依赖和显卡驱动。")
        except Exception as exc:
            with LOCK:
                ERROR, STATE = str(exc), "failed"
            if PROCESS.poll() is None:
                PROCESS.terminate()
                PROCESS.wait(timeout=15)
            raise
        finally:
            session.close()


def stop(*, shutdown=False):
    global PROCESS, LOG_HANDLE, STATE, PORT, LOADED_NODES
    with START_LOCK:
        if not shutdown and LEASES:
            raise RuntimeError("OCV 仍有任务正在准备或等待结果，请结束任务后再关闭引擎。")
        if PROCESS is not None and PROCESS.poll() is None:
            if not shutdown:
                session = requests.Session()
                session.trust_env = False
                try:
                    response = session.get(f"http://127.0.0.1:{PORT}/queue", timeout=5)
                    response.raise_for_status()
                    queue = response.json()
                    if queue.get("queue_running") or queue.get("queue_pending"):
                        raise RuntimeError("内置引擎仍有运行或排队任务，请先停止任务后再关闭引擎。")
                finally:
                    session.close()
            PROCESS.terminate()
            try:
                PROCESS.wait(timeout=15)
            except subprocess.TimeoutExpired:
                PROCESS.kill()
                PROCESS.wait(timeout=5)
        with LOCK:
            PROCESS, STATE, PORT = None, "stopped", 0
            LOADED_NODES = None
            if LOG_HANDLE:
                LOG_HANDLE.close()
                LOG_HANDLE = None


@contextmanager
def execution_lease(required_models=None, required_nodes=None):
    global LEASES
    # Register before startup/upload so an empty ComfyUI queue does not mean idle.
    with LOCK:
        LEASES += 1
    try:
        url = ensure_ready(required_models=required_models)
        missing = sorted(set(required_nodes or []) - (LOADED_NODES or set()))
        if missing:
            raise RuntimeError('此工作流缺少已加载节点：' + '、'.join(missing)
                               + '。请在 ComfyUI 工作台的「用户节点」查看安装指引，将插件放入 OCV/comfyui/custom_nodes，关闭并重新启动内置引擎后检查。')
        yield url
    finally:
        with LOCK:
            LEASES -= 1


def _local_user(request):
    user = require_user(request)
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "内置引擎管理仅允许在本机操作")
    return user


@router.get("")
def get_status(request: Request):
    _local_user(request)
    return status()


@router.post("/start")
def start_engine(request: Request):
    _local_user(request)
    if status()["state"] not in {"starting", "ready"}:
        def start():
            global ERROR, STATE
            try:
                ready = status()["ready_profiles"]
                selected = next((p for p in builtin_profiles() if p["id"] in ready), None)
                ensure_ready(required_models=selected.get("required_models") if selected else None)
            except Exception as exc:
                with LOCK:
                    ERROR, STATE = str(exc), "failed"
        threading.Thread(target=start, daemon=True).start()
    return status()


@router.post("/stop")
def stop_engine(request: Request):
    _local_user(request)
    try:
        stop()
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    return status()


class Settings(BaseModel):
    model_directory: str = Field(default="", max_length=2048)


@router.put("/settings")
def save_settings(payload: Settings, request: Request):
    _local_user(request)
    with START_LOCK:
        if status()["state"] in {"starting", "ready"}:
            raise HTTPException(409, "请先关闭内置引擎，再修改模型目录。")
        path = payload.model_directory.strip()
        if path and (not Path(path).is_absolute() or not Path(path).is_dir()):
            raise HTTPException(400, "请选择现有的模型根目录，例如 ComfyUI/models。")
        write_json(DATA / "settings.json", {"model_directory": path})
    return status()


@router.get("/logs")
def get_logs(request: Request):
    _local_user(request)
    try:
        with (DATA / "engine.log").open("rb") as stream:
            stream.seek(max(0, stream.seek(0, 2) - 24000))
            content = stream.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        content = "尚无启动日志"
    return {"text": content}


@router.get('/nodes')
def get_nodes(request: Request):
    _local_user(request)
    return node_status()


@router.post('/nodes/open-folder')
def open_nodes_folder(request: Request):
    _local_user(request)
    directory = PROJECT / 'comfyui' / 'custom_nodes'
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(directory))
    return {'directory': str(directory)}


@router.get("/models")
def get_model_installation(request: Request, kind: str = "comfyui", profile_id: str = ""):
    _local_user(request)
    from .model_library import installation
    return installation(kind, profile_id)


class ModelFolder(BaseModel):
    kind: str = "comfyui"
    profile_id: str = ""
    path: str = ""


@router.post("/models/open-folder")
def open_model_folder(payload: ModelFolder, request: Request):
    _local_user(request)
    from .model_library import open_folder
    return open_folder(payload.kind, payload.profile_id, payload.path)


@router.post("/models/verify")
def verify_model_files(payload: ModelFolder, request: Request):
    _local_user(request)
    if payload.kind != "comfyui":
        raise HTTPException(400, "此完整校验用于内置 ComfyUI 模型")
    from .model_library import verify_models
    return verify_models(payload.profile_id)


class Selection(BaseModel):
    mode: str = Field(pattern="^(managed|external)$")


@router.put("/selection")
def select_engine(payload: Selection, request: Request):
    user = _local_user(request)
    from . import comfyui_bridge as bridge
    user_id = int(user["id"])
    with LOCK, bridge.LOCK:
        if LEASES:
            raise HTTPException(409, "内置引擎有任务进行中，请结束后再切换。")
        if payload.mode == "managed":
            release, manifest = runtime_info()
            if release is None:
                raise HTTPException(409, "请先安装内置引擎组件。")
            profile = read_json(release / "profile.json", None)
            if not profile:
                raise HTTPException(409, "组件缺少预置工作流。")
            profile = bridge.ProfileRequest.model_validate(profile).model_dump()
            profiles = bridge._profiles(user_id)
            # Never overwrite a user's edits to a previously imported preset.
            if not any(item.get("id") == profile["id"] for item in profiles):
                bridge._write_json(bridge._user_root(user_id) / "profiles.json", [profile, *profiles])
        value = bridge._saved_connection(user_id)
        value["mode"] = payload.mode
        bridge._write_json(bridge._user_root(user_id) / "connection.json", value)
    return {"mode": payload.mode}


atexit.register(lambda: stop(shutdown=True))
