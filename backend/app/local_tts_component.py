"""Optional local IndexTTS-2.5 model installer for portable OCV packages."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from .indextts25_local import load_indextts25_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = PROJECT_ROOT / "tools" / "deploy_indextts25.ps1"
LOG_PATH = PROJECT_ROOT / "runtime_logs" / "indextts25_install.log"
ESTIMATED_MODEL_BYTES = 10_974_021_376

_LOCK = threading.RLock()
_PROCESS: subprocess.Popen[bytes] | None = None
_LOG_HANDLE: Any = None
_LAST_EXIT_CODE: int | None = None


def _model_bytes() -> int:
    model_dir = load_indextts25_config().model_dir
    if not model_dir.is_dir():
        return 0
    total = 0
    try:
        for path in model_dir.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _refresh_process() -> bool:
    global _PROCESS, _LOG_HANDLE, _LAST_EXIT_CODE
    with _LOCK:
        if _PROCESS is None:
            return False
        result = _PROCESS.poll()
        if result is None:
            return True
        _LAST_EXIT_CODE = int(result)
        _PROCESS = None
        if _LOG_HANDLE is not None:
            try:
                _LOG_HANDLE.close()
            finally:
                _LOG_HANDLE = None
        return False


def component_status() -> dict[str, Any]:
    installing = _refresh_process()
    config = load_indextts25_config()
    runtime_missing = config.missing_runtime_resources()
    model_missing = config.missing_model_resources()
    downloaded = _model_bytes() if installing or model_missing else ESTIMATED_MODEL_BYTES
    if config.ready:
        state = "ready"
        message = "本地 IndexTTS-2.5 已安装并可用。"
    elif installing:
        state = "installing"
        message = "正在下载并安装本地 TTS 权重，可关闭此窗口在后台继续。"
    elif runtime_missing:
        state = "runtime_incomplete"
        message = "本地 TTS 基础环境不完整，请重新下载 OCV 整合包。"
    elif _LAST_EXIT_CODE not in (None, 0):
        state = "failed"
        message = "本地 TTS 权重安装未完成，可检查网络后继续下载。"
    elif downloaded > 0:
        state = "partial"
        message = "本地 TTS 权重尚未下载完整，可继续安装。"
    else:
        state = "not_installed"
        message = "当前为轻便配置，尚未安装本地 TTS 权重。"
    return {
        "state": state,
        "ready": config.ready,
        "installing": installing,
        "optional": True,
        "downloaded_bytes": downloaded,
        "estimated_total_bytes": ESTIMATED_MODEL_BYTES,
        "runtime_missing": runtime_missing,
        "model_missing_count": len(model_missing),
        "last_exit_code": _LAST_EXIT_CODE,
        "log_path": str(LOG_PATH),
        "message": message,
    }


def start_install() -> dict[str, Any]:
    global _PROCESS, _LOG_HANDLE, _LAST_EXIT_CODE
    with _LOCK:
        current = component_status()
        if current["ready"] or current["installing"]:
            return current
        if current["runtime_missing"]:
            raise RuntimeError(current["message"])
        if os.name != "nt":
            raise RuntimeError("本地 TTS 一键安装目前仅支持 Windows 便携版。")
        if not INSTALL_SCRIPT.is_file():
            raise RuntimeError("找不到本地 TTS 安装脚本，请先通过 Launcher 更新 OCV。")
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOG_HANDLE = LOG_PATH.open("wb")
        env = os.environ.copy()
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            _PROCESS = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALL_SCRIPT),
                    "-ProjectRoot",
                    str(PROJECT_ROOT),
                ],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=_LOG_HANDLE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except Exception:
            _LOG_HANDLE.close()
            _LOG_HANDLE = None
            raise
        _LAST_EXIT_CODE = None
    return component_status()
