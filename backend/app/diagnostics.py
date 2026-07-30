"""Privacy-aware diagnostic package export for support troubleshooting."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from importlib import metadata
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_LOGS_DIR = PROJECT_ROOT / "runtime_logs"
DIAGNOSTICS_DIR = RUNTIME_LOGS_DIR / "diagnostics"

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)((?:api[_ -]?key|token|secret|authorization|password)\s*[=:]\s*)([^\s,;]+)"
)
_TOKEN_RE = re.compile(r"(?i)\b(?:sk|rk|AIza)[-_A-Za-z0-9]{12,}\b")
_USER_HOME_RE = re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")
_SENSITIVE_REQUEST_KEYS = {
    "api_key", "language_api_key", "image_api_key", "common_api_key", "qwen_tts_api_key",
    "script", "reference_text", "source_audio_id", "source_audio", "prompt", "visual_prompt_system",
    "agent0_prompt_system", "agent1_prompt_system", "agent2_prompt_system",
}


def redact_text(value: object) -> str:
    """Remove credential-shaped values and personal machine paths from exported text."""
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = _TOKEN_RE.sub("[REDACTED]", text)
    text = text.replace(str(PROJECT_ROOT), "<project>")
    text = _USER_HOME_RE.sub("<user-home>", text)
    return text


def sanitize_request(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep operational switches while intentionally omitting user content and secrets."""
    result: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        normalized = str(key).lower()
        if normalized in _SENSITIVE_REQUEST_KEYS or "key" in normalized or "token" in normalized:
            result[str(key)] = "[REDACTED]"
        elif isinstance(value, str):
            result[str(key)] = redact_text(value)[:500]
        elif isinstance(value, list):
            result[str(key)] = f"[list: {len(value)} items]"
        elif isinstance(value, dict):
            result[str(key)] = f"[object: {len(value)} fields]"
        else:
            result[str(key)] = value
    return result


def _command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    return redact_text(output[0] if output else f"exit code {completed.returncode}")[:500]


def _package_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for package in ("fastapi", "uvicorn", "pydantic", "requests", "torch", "transformers", "faster-whisper"):
        try:
            result[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            result[package] = "not installed"
    return result


def _job_report(job: Any) -> dict[str, Any]:
    artifacts = getattr(job, "artifacts", {}) or {}
    return {
        "id": str(getattr(job, "id", "")),
        "status": str(getattr(job, "status", "")),
        "step": str(getattr(job, "step", "")),
        "progress": int(getattr(job, "progress", 0) or 0),
        "message": redact_text(getattr(job, "message", "")),
        "error": redact_text(getattr(job, "error", "")),
        "created_at": getattr(job, "created_at", None),
        "updated_at": getattr(job, "updated_at", None),
        "request": sanitize_request(getattr(job, "request", {}) or {}),
        "artifacts": {str(key): Path(str(value)).name for key, value in artifacts.items()},
        "log_line_count": len(getattr(job, "logs", []) or []),
    }


def _recent_runtime_logs() -> list[Path]:
    if not RUNTIME_LOGS_DIR.is_dir():
        return []
    candidates = [
        item for item in RUNTIME_LOGS_DIR.rglob("*")
        if item.is_file() and item.suffix.lower() in {".log", ".txt"} and DIAGNOSTICS_DIR not in item.parents
    ]
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[:10]


def _write_runtime_logs(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    exported: list[dict[str, Any]] = []
    for path in _recent_runtime_logs():
        try:
            raw = path.read_bytes()[-256 * 1024:]
            text = raw.decode("utf-8", errors="replace")
            relative = path.relative_to(RUNTIME_LOGS_DIR).as_posix()
            archive.writestr(f"runtime_logs/{relative}", redact_text(text))
            exported.append({"name": relative, "bytes_exported": len(raw)})
        except OSError as exc:
            exported.append({"name": path.name, "error": str(exc)})
    return exported


def _cleanup_old_packages() -> None:
    if not DIAGNOSTICS_DIR.is_dir():
        return
    packages = sorted(DIAGNOSTICS_DIR.glob("问题诊断包_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in packages[10:]:
        try:
            stale.unlink()
        except OSError:
            pass


def create_diagnostic_package(job: Any) -> Path:
    """Create a shareable zip without API keys, prompts, media, or model files."""
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    package_path = DIAGNOSTICS_DIR / f"问题诊断包_{getattr(job, 'id', 'unknown')}_{timestamp}.zip"
    system_report = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "privacy": "不包含 API Key、文案原文、提示词、音视频图片、模型文件或 .env。",
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "disk_free_gb": round(shutil.disk_usage(PROJECT_ROOT).free / (1024 ** 3), 2),
        "commands": {
            "ffmpeg": _command_version(["ffmpeg", "-version"]),
            "node": _command_version(["node", "--version"]),
            "nvidia_smi": _command_version(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]),
        },
        "packages": _package_versions(),
    }
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "One-Click VidGen 问题诊断包\n\n"
            "此压缩包用于定位运行问题。它不包含 API Key、文案原文、提示词、音视频图片、模型文件或 .env。\n"
            "请将整个 zip 文件提供给开发者，并同时说明复现步骤。\n",
        )
        archive.writestr("任务信息.json", json.dumps(_job_report(job), ensure_ascii=False, indent=2))
        archive.writestr("运行环境.json", json.dumps(system_report, ensure_ascii=False, indent=2))
        archive.writestr("任务日志.txt", "\n".join(redact_text(line) for line in (getattr(job, "logs", []) or [])))
        exported_logs = _write_runtime_logs(archive)
        archive.writestr("导出清单.json", json.dumps({"runtime_logs": exported_logs}, ensure_ascii=False, indent=2))
    _cleanup_old_packages()
    return package_path
