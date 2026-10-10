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
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_LOGS_DIR = PROJECT_ROOT / "runtime_logs"
DIAGNOSTICS_DIR = RUNTIME_LOGS_DIR / "diagnostics"

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)((?:api[_ -]?key|token|secret|authorization|password)[\"']?\s*[=:]\s*[\"']?)([^\s,;\"'}]+)"
)
_TOKEN_RE = re.compile(r"(?i)\b(?:sk|rk|AIza)[-_A-Za-z0-9]{12,}\b")
_USER_HOME_RE = re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")
_SENSITIVE_REQUEST_KEYS = {
    "api_key", "language_api_key", "image_api_key", "common_api_key", "qwen_tts_api_key",
    "script", "reference_text", "source_audio_id", "source_audio", "prompt", "visual_prompt_system",
    "agent0_prompt_system", "agent1_prompt_system", "agent2_prompt_system",
    "visual_style_prompt", "global_character_prompt", "story_environment_prompt",
}


def redact_text(value: object) -> str:
    """Remove credential-shaped values and personal machine paths from exported text."""
    text = str(value or "")
    text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer [REDACTED]', text)
    text = re.sub(r'(?i)\bms-[0-9a-f-]{32,}\b', '[REDACTED]', text)
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
    for package in ("fastapi", "uvicorn", "pydantic", "requests", "torch", "transformers", "faster-whisper", "ctranslate2", "av", "onnxruntime"):
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
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 256 * 1024))
                raw = stream.read(256 * 1024)
            text = raw.decode("utf-8", errors="replace")
            relative = path.relative_to(RUNTIME_LOGS_DIR).as_posix()
            archive.writestr(f"runtime_logs/{relative}", redact_text(text))
            exported.append({"name": relative, "bytes_exported": len(raw),
                             "truncated": path.stat().st_size > len(raw),
                             "modified_at": path.stat().st_mtime})
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


_STRUCTURAL_KEYS = {
    'id', 'index', 'status', 'step', 'revision', 'start', 'end', 'duration', 'kind',
    'error', 'message', 'progress', 'created_at', 'updated_at', 'source_project',
    'shots', 'scenes', 'scene_assets', 'characters', 'settings', 'export_settings',
    'image_status', 'video_status', 'image_task', 'video_task', 'logs',
    'reference_image_ids', 'reference_materials', 'reference_binding_version',
    'character_ids', 'scene_id', 'macro_scene_id', 'scene_reference_ids',
    'label', 'asset_id', 'input_number', 'ratio', 'use_video_audio', 'scene_references_status',
    'includes_slides',
    'segments', 'engine', 'filename', 'slide_id', 'items', 'mapping',
    'posters', 'results', 'agent_version', 'story_agent_version', 'content_mode',
    'director_strategy', 'story_source_fingerprint', 'scene_source_fingerprint',
}
_ASSET_KEYS = {'image', 'video', 'audio', 'subtitles', 'raw', 'path',
               'reference_image_paths', 'scene_reference_paths'}


def _bounded_json(path: Path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('JSON 超过 4 MiB 采集上限')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _structure(value, root: Path, depth=0):
    """Explicit allowlist: no original script, image prompts or credentials."""
    if depth > 12:
        return '[depth limit]'
    if isinstance(value, list):
        return [_structure(item, root, depth + 1) for item in value[:1000]]
    if not isinstance(value, dict):
        return redact_text(value)[:2000] if isinstance(value, str) else value
    result = {}
    for key, item in value.items():
        if key in _ASSET_KEYS:
            def asset(raw):
                raw = str(raw or '')
                if '://' in raw or raw.startswith('data:'):
                    return {'external': True}  # Never export signed URLs/data.
                path = (root / raw).resolve()
                boundary = next((parent.parent for parent in (root, *root.parents)
                                 if parent.name == 'other'), root).resolve()
                allowed = boundary == path or boundary in path.parents
                return {'name': Path(raw).name, 'exists': path.is_file() if allowed else None,
                        'inside_project': allowed}
            result[key] = [asset(raw) for raw in item[:20]] if isinstance(item, list) else asset(item)
        elif key in _STRUCTURAL_KEYS:
            result[key] = _structure(item, root, depth + 1)
        elif key == 'description' and isinstance(item, str):
            from .reference_materials import material_is_character, required_every_shot_labels
            row = dict(value, label='reference')
            result['purpose_flags'] = {'character': material_is_character(row),
                                       'required_every_shot': bool(required_every_shot_labels([row]))}
    return result


def _windows_environment():
    if os.name != 'nt':
        return {'available': False, 'reason': 'not Windows'}
    script = """$ErrorActionPreference='Stop';$report=@{};
try {$report.cpu=@(Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors);
$mem=Get-CimInstance Win32_OperatingSystem;$report.memory=@{total_kb=$mem.TotalVisibleMemorySize;free_kb=$mem.FreePhysicalMemory;virtual_total_kb=$mem.TotalVirtualMemorySize;virtual_free_kb=$mem.FreeVirtualMemory}}catch{$report.system_error=$_.Exception.GetType().Name};
try {$report.python_crashes=@(Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000;StartTime=(Get-Date).AddDays(-2)} -MaxEvents 100 | Where-Object {$_.Properties[0].Value -match '^python(w)?\\.exe$'} | Select-Object -First 20 | ForEach-Object {@{time=$_.TimeCreated.ToString('o');application=$_.Properties[0].Value;application_version=$_.Properties[1].Value;module=$_.Properties[3].Value;module_version=$_.Properties[4].Value;exception_code=$_.Properties[6].Value;fault_offset=$_.Properties[7].Value}})}catch{$report.events_error=$_.Exception.GetType().Name};$report|ConvertTo-Json -Depth 5 -Compress"""
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;' + script],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=12, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            return {'available': False, 'reason': f'exit {result.returncode}'}
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {'available': False, 'reason': type(exc).__name__}


def _write_related_reports(archive, job):
    manifest = []
    request = getattr(job, 'request', {}) or {}
    job_id = str(getattr(job, 'id', ''))
    from .reference_materials import request_reference_catalog
    catalog = request_reference_catalog(request)
    archive.writestr('reference_catalog_summary.json', json.dumps(
        _structure(catalog, PROJECT_ROOT), ensure_ascii=False, indent=2))

    def collect(path, name):
        try:
            if not path.is_file():
                manifest.append({'name': name, 'status': 'missing'})
                return
            resolved = path.resolve()
            if PROJECT_ROOT.resolve() not in resolved.parents:
                raise ValueError('source outside project')
            report = _structure(_bounded_json(resolved), resolved.parent)
            archive.writestr(name, json.dumps(report, ensure_ascii=False, indent=2))
            manifest.append({'name': name, 'status': 'included', 'source_bytes': resolved.stat().st_size})
        except (OSError, ValueError, TypeError) as exc:
            manifest.append({'name': name, 'status': 'unavailable', 'reason': type(exc).__name__})

    output_name = str(request.get('_step_output_dir') or '')
    if output_name and Path(output_name).name == output_name:
        output = PROJECT_ROOT / 'output' / output_name
        for name in ('poster_mapping.json', 'visual_prompt_plan.json', 'story_plan.json',
                     '画面映射.json', 'Agent1_分镜规划.json', 'Agent2_画面提示词规划.json',
                     '画面时间线.json', 'tts_segments/manifest.json'):
            collect(output / 'other' / name, 'output_summary/' + name)
    if re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        for name in ('poster_mapping.json', 'visual_prompt_plan.json', 'story_plan.json',
                     'scene_timeline.json', 'tts_segments/manifest.json'):
            collect(PROJECT_ROOT / 'workspace/jobs' / job_id / 'artifacts' / name, 'task_summary/' + name)
    user_id = getattr(job, 'user_id', None)
    if user_id is not None and str(user_id).isdigit():
        connection_path = PROJECT_ROOT / 'workspace/comfyui' / str(user_id) / 'connection.json'
        try:
            if connection_path.is_file() and PROJECT_ROOT.resolve() in connection_path.resolve().parents:
                connection = _bounded_json(connection_path)
                parsed = urlparse(str(connection.get('base_url') or ''))
                summary = {'mode': connection.get('mode', 'external'), 'scheme': parsed.scheme,
                           'host': parsed.hostname, 'port': parsed.port,
                           'profile_id': str(request.get('comfyui_profile_id') or '')}
                archive.writestr('comfyui_connection_summary.json', json.dumps(summary, ensure_ascii=False, indent=2))
                manifest.append({'name': 'comfyui_connection_summary.json', 'status': 'included'})
            else:
                manifest.append({'name': 'comfyui_connection_summary.json', 'status': 'missing'})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            manifest.append({'name': 'comfyui_connection_summary.json', 'status': 'unavailable', 'reason': type(exc).__name__})
        owner_root = PROJECT_ROOT / 'workspace/video_studio' / str(user_id)
        candidates = sorted(owner_root.glob('*/record.json'))[:1000]
        for path in candidates:
            try:
                if owner_root.resolve() not in path.resolve().parents or PROJECT_ROOT.resolve() not in path.resolve().parents:
                    continue
                record = _bounded_json(path)
                if (record.get('source_project') or {}).get('id') == job_id or record.get('id') == request.get('_dynamic_video_project_id'):
                    collect(path, 'dynamic_projects/' + path.parent.name + '/record_summary.json')
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                manifest.append({'name': 'dynamic_project_scan', 'status': 'unavailable', 'reason': type(exc).__name__})
        manifest.append({'name': 'dynamic_project_scan', 'status': 'scanned', 'records_checked': len(candidates),
                         'scan_limit': 1000})
    else:
        manifest.append({'name': 'dynamic_project_scan', 'status': 'unavailable', 'reason': 'missing owner id'})
    return manifest


def create_diagnostic_package(job: Any) -> Path:
    """Create a shareable zip without API keys, prompts, media, or model files."""
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    package_path = DIAGNOSTICS_DIR / f"问题诊断包_{getattr(job, 'id', 'unknown')}_{timestamp}.zip"
    system_report = {
        "diagnostic_schema_version": 2,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "privacy": "不主动打包文案、提示词、媒体、模型或 .env 文件；配置与关联快照脱敏。任务和运行日志可能保留文案片段，请分享前确认。",
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "windows_system": _windows_environment(),
        "disk_free_gb": round(shutil.disk_usage(PROJECT_ROOT).free / (1024 ** 3), 2),
        "commands": {
            "ffmpeg": _command_version(["ffmpeg", "-version"]),
            "node": _command_version(["node", "--version"]),
            "nvidia_smi": _command_version(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]),
        },
        "packages": _package_versions(),
        "asr_settings": {key: redact_text(os.getenv(key, default)) for key, default in
                         (("ASR_DEVICE", "auto"), ("ASR_MODEL", "base"), ("ASR_PYTHON", sys.executable))},
    }
    try:
        channel = _bounded_json(PROJECT_ROOT / 'launcher/update-channel.json')
        system_report['release'] = {key: channel.get(key) for key in ('release_id', 'release_order')}
    except (OSError, ValueError):
        system_report['release'] = {'release_id': 'unknown'}
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "One-Click VidGen 问题诊断包\n\n"
            "此压缩包用于定位运行问题。配置和关联 JSON 使用脱敏结构摘要，不打包媒体、模型、原文/提示词文件或 .env。\n"
            "任务和运行日志可能包含文案片段；密钥会尽力脱敏，分享前请确认日志内容。\n"
            "请将整个 zip 文件提供给开发者，并同时说明复现步骤。\n",
        )
        archive.writestr("任务信息.json", json.dumps(_job_report(job), ensure_ascii=False, indent=2))
        archive.writestr("运行环境.json", json.dumps(system_report, ensure_ascii=False, indent=2))
        archive.writestr("任务日志.txt", "\n".join(redact_text(line) for line in (getattr(job, "logs", []) or [])))
        exported_logs = _write_runtime_logs(archive)
        related = _write_related_reports(archive, job)
        archive.writestr("导出清单.json", json.dumps({"diagnostic_schema_version": 2,
                         "runtime_logs": exported_logs, "related_reports": related,
                         "privacy": "关联 JSON 为白名单结构摘要，原文、提示词、用途说明及媒体不导出"}, ensure_ascii=False, indent=2))
    _cleanup_old_packages()
    return package_path
