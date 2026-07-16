import mimetypes
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import (
    append_generation_job_log,
    load_generation_jobs,
    record_media_asset,
    upsert_generation_job,
)
from .html_generator import generate_visual_html
from .semantic_timeline import generate_fine_grained_timeline
from .gemini_client import GeminiError, gemini_configured, generate_gemini_text, parse_json_response
from story_agents import load_or_create_segment_story_plan, load_or_create_story_plan


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
JOBS_DIR = WORKSPACE_DIR / "jobs"
FINAL_DIR = WORKSPACE_DIR / "4_final_video"
OUTPUT_DIR = PROJECT_ROOT / "output"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


STEPS = [
    ("tts", "断句、配音、原始字幕"),
    ("scene", "ASR 短字幕与页面断句"),
    ("correct", "原文对齐与字幕校准"),
    ("semantic", "模块 3 语义分镜"),
    ("visual", "模块 4 在线海报与页面生成"),
    ("render", "Hyperframes 图像、音频、字幕合成视频"),
    ("archive", "整理项目输出"),
]
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
ASR_RUNTIME_SUCCESS_CACHE: set[str] = set()
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
NOISY_PROGRESS_RE = re.compile(
    r"^(?:\[(?P<phase>字幕版 1/2|纯净版 2/2)\]\s*)?.*?"
    r"(?P<percent>\d{1,3})%\s+"
    r"(?P<label>Streaming frame|Capturing frame|Encoding video|Assembling final video|Render complete)",
    re.I,
)
TTS_SENTENCE_PROGRESS_RE = re.compile(
    r"^\[TTS_PROGRESS\]\s+配音进度\s+(?P<completed>\d+)/(?P<total>\d+)"
)


class GenerationCancelled(RuntimeError):
    """The user requested that this generation job stop."""


def default_project_name() -> str:
    return f"项目_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4].upper()}"


def normalize_project_name(value: str | None) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")).strip(" .")
    if not name:
        name = default_project_name()
    if name.upper() in WINDOWS_RESERVED_NAMES:
        name = f"项目_{name}"
    return name[:80].rstrip(" .") or default_project_name()


VISUAL_PACING_DEFAULTS = {
    "urban_suspense": {"min_duration": 6.0, "target_duration": 8.0, "max_duration": 12.0, "max_slides": 6},
    "science_explainer": {"min_duration": 7.0, "target_duration": 9.0, "max_duration": 14.0, "max_slides": 6},
}


def visual_pacing_settings(request: dict[str, Any]) -> dict[str, Any]:
    """Resolve mode defaults and the optional user pacing override safely."""
    mode = str(request.get("content_mode") or "urban_suspense")
    base = dict(VISUAL_PACING_DEFAULTS.get(mode, VISUAL_PACING_DEFAULTS["urban_suspense"]))
    preset = str(request.get("visual_pacing_preset") or "auto").lower()
    if preset not in {"auto", "slow", "standard", "fast", "custom"}:
        preset = "auto"

    result = {**base, "preset": preset}
    if preset == "slow":
        result["target_duration"] += 2.0
        result["max_duration"] += 2.0
        result["max_slides"] += 1
    elif preset == "fast":
        result["target_duration"] = max(result["min_duration"], result["target_duration"] - 2.0)
        result["max_duration"] = max(result["target_duration"], result["max_duration"] - 2.0)
        result["max_slides"] = min(result["max_slides"], 3)
    elif preset == "custom":
        for key, low, high in (
            ("min_duration", 4.0, 20.0),
            ("target_duration", 5.0, 30.0),
            ("max_duration", 6.0, 40.0),
        ):
            try:
                value = float(request.get(f"visual_{key}") or result[key])
                result[key] = max(low, min(high, value))
            except (TypeError, ValueError):
                pass
        try:
            result["max_slides"] = max(1, min(12, int(request.get("visual_max_slides") or result["max_slides"])))
        except (TypeError, ValueError):
            pass

    result["target_duration"] = max(result["min_duration"], result["target_duration"])
    result["max_duration"] = max(result["target_duration"], result["max_duration"])
    result["label"] = {
        "auto": "按作品风格自动",
        "slow": "舒缓",
        "standard": "标准",
        "fast": "紧凑",
        "custom": "自定义",
    }[preset]
    return result


def normalize_log_line(line: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", str(line or ""))
    text = text.replace("\r", "\n")
    text = "\n".join(part.strip() for part in text.splitlines() if part.strip())
    return text.strip()


def parse_noisy_progress_log(line: str) -> tuple[str, int] | None:
    match = NOISY_PROGRESS_RE.search(line)
    if not match:
        return None
    label = match.group("label").strip()
    phase = str(match.group("phase") or "").strip()
    if phase:
        label = f"{phase} {label}"
    percent = max(0, min(100, int(match.group("percent"))))
    return label, percent


@dataclass
class Job:
    id: str
    user_id: int | None = None
    status: str = "queued"
    step: str = "queued"
    progress: int = 0
    message: str = "等待开始"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    logs: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    request: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs": self.logs[-250:],
            "artifacts": self.artifacts,
            "error": self.error,
            "request": {k: v for k, v in self.request.items() if k != "api_key"},
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._last_progress_log: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()
        self._pipeline_lock = threading.Lock()

    def create(self, request: dict[str, Any], user_id: int | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], user_id=user_id, request=request)
        with self._lock:
            self._jobs[job.id] = job
            self._cancel_events[job.id] = threading.Event()
        upsert_generation_job(job.snapshot())
        return job

    def load_persisted(self) -> None:
        loaded: dict[str, Job] = {}
        interrupted: list[Job] = []
        for row in load_generation_jobs():
            job = Job(
                id=str(row["id"]),
                user_id=int(row["user_id"]) if row.get("user_id") is not None else None,
                status=str(row.get("status") or "queued"),
                step=str(row.get("step") or "queued"),
                progress=int(row.get("progress") or 0),
                message=str(row.get("message") or ""),
                created_at=float(row.get("created_at") or time.time()),
                updated_at=float(row.get("updated_at") or time.time()),
                logs=list(row.get("logs") or []),
                artifacts=dict(row.get("artifacts") or {}),
                error=row.get("error"),
                request=dict(row.get("request") or {}),
            )
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.message = "服务重启，任务已中断"
                job.error = "backend restarted before task completion"
                job.updated_at = time.time()
                interrupted.append(job)
            loaded[job.id] = job
        with self._lock:
            self._jobs = loaded
            self._cancel_events = {
                job_id: threading.Event() for job_id in loaded
            }
        for job in interrupted:
            self.log(job, "服务重启，未完成任务已标记为失败")
            upsert_generation_job(job.snapshot())

    def import_legacy_jobs(self, default_user_id: int | None) -> int:
        if not JOBS_DIR.is_dir():
            return 0
        imported = 0
        for job_dir in sorted(JOBS_DIR.iterdir()):
            if not job_dir.is_dir():
                continue
            job_id = job_dir.name
            with self._lock:
                if job_id in self._jobs:
                    continue

            script_path = job_dir / "script.txt"
            artifact_dir = job_dir / "artifacts"
            archive_dir = PROJECT_ROOT / "Archives" / job_id
            completed = (artifact_dir / "final_with_subtitles.mp4").is_file()
            created_at = (
                script_path.stat().st_mtime
                if script_path.is_file()
                else job_dir.stat().st_mtime
            )
            script = script_path.read_text(encoding="utf-8") if script_path.is_file() else ""
            artifact_urls: dict[str, str] = {}
            artifact_names = {
                "final_with_subtitles.mp4": "video_with_subtitles",
                "final_raw_presentation.mp4": "video_raw",
                "final_output.wav": "audio",
                "final_short.srt": "subtitle",
                "scene_timeline.json": "scene_timeline",
                "fine_grained_timeline.json": "fine_grained_timeline",
                "index.html": "html",
                "manifest.txt": "archive_manifest",
            }
            if artifact_dir.is_dir():
                for path in artifact_dir.iterdir():
                    key = artifact_names.get(path.name)
                    if key and path.is_file():
                        artifact_urls[key] = f"/api/jobs/{job_id}/artifacts/{path.name}"

            job = Job(
                id=job_id,
                user_id=default_user_id,
                status="completed" if completed else "failed",
                step="completed" if completed else "legacy",
                progress=100,
                message="历史任务已导入" if completed else "历史任务未完成",
                created_at=created_at,
                updated_at=job_dir.stat().st_mtime,
                artifacts=artifact_urls,
                error=None if completed else "legacy task did not contain completed artifacts",
                request={
                    "script": script,
                },
            )
            with self._lock:
                self._jobs[job.id] = job
            upsert_generation_job(job.snapshot())
            self.log(job, "从 workspace/jobs 导入历史任务")

            for path in job_dir.rglob("*"):
                if path.is_file():
                    role = "source_script" if path == script_path else "legacy_job_file"
                    register_job_asset(
                        job,
                        path,
                        role,
                        {"legacy_import": True},
                    )
            if archive_dir.is_dir():
                for path in archive_dir.rglob("*"):
                    if path.is_file():
                        register_job_asset(
                            job,
                            path,
                            "archive",
                            {
                                "legacy_import": True,
                                "archive_relative_path": str(path.relative_to(archive_dir)),
                            },
                        )
            imported += 1
        return imported

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, user_id: int | None = None) -> list[dict[str, Any]]:
        return self.list_page(user_id=user_id, page=1, page_size=25)["jobs"]

    def list_page(
        self,
        user_id: int | None = None,
        page: int = 1,
        page_size: int = 5,
    ) -> dict[str, Any]:
        safe_page = max(1, page)
        safe_page_size = max(1, min(100, page_size))
        with self._lock:
            jobs = list(self._jobs.values())
            if user_id is not None:
                jobs = [job for job in jobs if job.user_id == user_id]
            jobs = sorted(jobs, key=lambda job: job.created_at, reverse=True)
            total = len(jobs)
            total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
            safe_page = min(safe_page, total_pages)
            start = (safe_page - 1) * safe_page_size
            page_jobs = jobs[start : start + safe_page_size]
            snapshots = [job.snapshot() for job in page_jobs]
        return {
            "jobs": snapshots,
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        }

    def update(self, job: Job, **changes: Any) -> None:
        if (
            job.status == "cancelled"
            and changes.get("status", job.status) != "cancelled"
        ):
            return
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        upsert_generation_job(job.snapshot())

    def log(self, job: Job, line: str) -> None:
        normalized = normalize_log_line(line)
        if not normalized:
            return
        tts_progress = TTS_SENTENCE_PROGRESS_RE.search(normalized)
        if tts_progress is not None:
            completed = int(tts_progress.group("completed"))
            total = max(1, int(tts_progress.group("total")))
            normalized = normalized.replace("[TTS_PROGRESS] ", "", 1)
            if job.step == "tts":
                job.progress = min(29, 8 + round(min(completed, total) / total * 21))
                job.message = normalized[:500]
        progress = parse_noisy_progress_log(normalized)
        if progress is not None:
            key, percent = progress
            cache_key = f"{job.id}:{key}"
            previous = self._last_progress_log.get(cache_key)
            if previous and percent < 100 and percent - previous[1] < 10:
                return
            self._last_progress_log[cache_key] = (key, percent)
            normalized = f"{key}: {percent}%"
        stamp = time.strftime("%H:%M:%S")
        persisted_line = f"[{stamp}] {normalized}"
        job.logs.append(persisted_line)
        job.updated_at = time.time()
        append_generation_job_log(job.id, persisted_line, job.updated_at)
        if tts_progress is not None:
            upsert_generation_job(job.snapshot())

    def run_async(self, job: Job, *, resume: bool = False) -> None:
        thread = threading.Thread(target=self._run_guarded, args=(job, resume), daemon=True)
        thread.start()

    def resume(self, job: Job) -> dict[str, Any]:
        with self._lock:
            if job.status in {"queued", "running"}:
                return job.snapshot()
            self._cancel_events[job.id] = threading.Event()
        self.log(job, "收到断点续跑请求，将复用已生成的中间产物")
        self.update(
            job,
            status="queued",
            step="queued",
            message="等待断点续跑",
            error=None,
        )
        self.run_async(job, resume=True)
        return job.snapshot()

    def is_cancelled(self, job: Job) -> bool:
        with self._lock:
            event = self._cancel_events.get(job.id)
            return job.status == "cancelled" or bool(event and event.is_set())

    def raise_if_cancelled(self, job: Job) -> None:
        if self.is_cancelled(job):
            raise GenerationCancelled("用户已停止生成")

    def attach_process(
        self,
        job: Job,
        process: subprocess.Popen[str],
    ) -> None:
        with self._lock:
            event = self._cancel_events.setdefault(job.id, threading.Event())
            cancelled = event.is_set() or job.status == "cancelled"
            if not cancelled:
                self._processes[job.id] = process
        if cancelled:
            _terminate_process_tree(process)
            raise GenerationCancelled("用户已停止生成")

    def detach_process(
        self,
        job: Job,
        process: subprocess.Popen[str],
    ) -> None:
        with self._lock:
            if self._processes.get(job.id) is process:
                self._processes.pop(job.id, None)

    def cancel(self, job: Job) -> dict[str, Any]:
        with self._lock:
            if job.status in TERMINAL_JOB_STATUSES:
                return job.snapshot()
            event = self._cancel_events.setdefault(job.id, threading.Event())
            event.set()
            process = self._processes.get(job.id)

        self.log(job, "收到停止生成请求")
        self.update(
            job,
            status="cancelled",
            step="cancelled",
            message="已停止生成",
            error=None,
        )
        if process is not None:
            _terminate_process_tree(process)
        return job.snapshot()

    def _run_guarded(self, job: Job, resume: bool = False) -> None:
        with self._pipeline_lock:
            try:
                self.raise_if_cancelled(job)
                run_pipeline(job, self, resume=resume)
            except GenerationCancelled:
                if job.status != "cancelled":
                    self.log(job, "生成已停止")
                    self.update(
                        job,
                        status="cancelled",
                        step="cancelled",
                        message="已停止生成",
                        error=None,
                    )
            except Exception as exc:
                if self.is_cancelled(job):
                    if job.status != "cancelled":
                        self.update(
                            job,
                            status="cancelled",
                            step="cancelled",
                            message="已停止生成",
                            error=None,
                        )
                else:
                    self.log(job, f"失败: {exc}")
                    self.update(job, status="failed", error=str(exc), message="生成失败")


store = JobStore()


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=3)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def project_storage_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def asset_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}:
        return "audio"
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
        return "video"
    if suffix in {".srt", ".ass", ".vtt"}:
        return "subtitle"
    if suffix in {".json"}:
        return "metadata"
    return "document"


def register_job_asset(job: Job, path: Path, role: str, metadata: dict[str, Any] | None = None) -> None:
    if not path.is_file():
        return
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    duration_seconds: float | None = None
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                duration_seconds = audio.getnframes() / audio.getframerate()
        except (OSError, wave.Error, ZeroDivisionError):
            duration_seconds = None
    record_media_asset(
        user_id=job.user_id,
        generation_job_id=job.id,
        kind=asset_kind(path),
        role=role,
        storage_backend="local",
        storage_path=project_storage_path(path),
        original_name=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        duration_seconds=duration_seconds,
        metadata=metadata,
    )


def ffmpeg_binary() -> str:
    local = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists() and os.name == "nt":
        return str(local)
    return shutil.which("ffmpeg") or "ffmpeg"


def user_upload_path(user_id: int, filename: str) -> Path:
    root = (WORKSPACE_DIR / "editor" / f"user_{user_id}" / "uploads").resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("找不到上传的配音文件")
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("已有配音只支持 mp3、wav、m4a、aac、flac、ogg")
    return path


def prepare_uploaded_audio(job: Job, store: JobStore, source_audio_id: str) -> Path:
    if job.user_id is None:
        raise ValueError("已有配音模式需要用户身份")
    source = user_upload_path(int(job.user_id), source_audio_id)
    output = WORKSPACE_DIR / "2_audio_srt" / "final_output.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    register_job_asset(job, source, "source_audio", {"skip_tts": True})
    if source.suffix.lower() == ".wav":
        shutil.copy2(source, output)
        store.log(job, f"已导入已有 WAV 配音: {source.name}")
    else:
        run_command(
            job,
            store,
            [
                ffmpeg_binary(),
                "-y",
                "-i",
                str(source),
                "-vn",
                "-acodec",
                "pcm_s16le",
                str(output),
            ],
            "转换已有配音为 WAV",
        )
    register_job_asset(job, output, "generation_artifact:audio", {"source_audio_id": source_audio_id})
    return output


def write_original_text_from_asr(job: Job) -> None:
    timeline_path = WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json"
    if not timeline_path.exists():
        return
    try:
        data = json.loads(timeline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, list):
        return
    text = "\n".join(
        str(item.get("text_content") or "").strip()
        for item in data
        if isinstance(item, dict) and str(item.get("text_content") or "").strip()
    )
    if text:
        target = WORKSPACE_DIR / "1_original_text.txt"
        target.write_text(text, encoding="utf-8")
        register_job_asset(job, target, "asr_transcript", {"skip_text_correction": True})


def reset_generation_workspace() -> None:
    """Remove stale per-run outputs from the shared workspace before a new job."""
    for path in (
        WORKSPACE_DIR / "2_audio_srt",
        WORKSPACE_DIR / "3_visual_template",
        FINAL_DIR,
        WORKSPACE_DIR / "temp_chunks",
    ):
        if path.exists():
            shutil.rmtree(path)
    (WORKSPACE_DIR / "1_original_text.txt").unlink(missing_ok=True)


def _asr_runtime_available(python_bin: str) -> tuple[bool, str]:
    if python_bin in ASR_RUNTIME_SUCCESS_CACHE:
        return True, ""
    try:
        timeout_seconds = max(30, int(os.getenv("ASR_RUNTIME_CHECK_TIMEOUT_SECONDS", "90")))
    except ValueError:
        timeout_seconds = 90
    try:
        result = subprocess.run(
            [
                python_bin,
                "-c",
                "import ctranslate2, faster_whisper",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"ASR 依赖导入自检超过 {timeout_seconds} 秒"
    except OSError as exc:
        return False, str(exc)
    available = result.returncode == 0
    if available:
        ASR_RUNTIME_SUCCESS_CACHE.add(python_bin)
    return available, result.stderr.strip()


def resolve_asr_python() -> str:
    """Select a Python runtime that can actually launch Faster-Whisper."""
    configured = os.getenv("ASR_PYTHON", "").strip()
    candidates: list[str | Path] = []
    if configured:
        candidates.append(configured)
    else:
        candidates.extend(
            [
                sys.executable,
                Path(sys.prefix) / "envs" / "pytorch2.11" / "bin" / "python",
                Path.home() / "miniconda3" / "envs" / "pytorch2.11" / "bin" / "python",
                PROJECT_ROOT / ".venv" / "bin" / "python",
            ]
        )

    checked: list[str] = []
    last_error = ""
    for candidate in candidates:
        value = str(candidate)
        executable = (
            shutil.which(value)
            if not Path(value).expanduser().is_absolute()
            else str(Path(value).expanduser())
        )
        if not executable or not Path(executable).is_file():
            checked.append(value)
            continue
        executable = str(Path(executable).resolve())
        if executable in checked:
            continue
        checked.append(executable)
        available, error = _asr_runtime_available(executable)
        if available:
            return executable
        last_error = error

    if configured:
        detail = f": {last_error}" if last_error else ""
        raise RuntimeError(
            f"ASR_PYTHON 不可用或缺少 faster-whisper/ctranslate2: {configured}{detail}"
        )
    raise RuntimeError(
        "未找到可运行 Faster-Whisper 的 Python。请安装 requirements.txt，"
        "或通过 ASR_PYTHON 指定已有环境。已检查: "
        + ", ".join(checked)
    )


def run_command(
    job: Job,
    store: JobStore,
    command: list[str],
    label: str,
    extra_env: dict[str, str] | None = None,
) -> None:
    store.raise_if_cancelled(job)
    store.log(job, f"开始: {label}")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=os.name != "nt",
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        ),
    )
    store.attach_process(job, process)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            store.log(job, line)
        return_code = process.wait()
    finally:
        store.detach_process(job, process)
    store.raise_if_cancelled(job)
    if return_code != 0:
        raise RuntimeError(f"{label} 失败，退出码 {return_code}")
    store.log(job, f"完成: {label}")


def copy_artifacts(job: Job) -> dict[str, str]:
    job_dir = JOBS_DIR / job.id / "artifacts"
    job_dir.mkdir(parents=True, exist_ok=True)
    candidates = {
        "video_with_subtitles": FINAL_DIR / "final_with_subtitles.mp4",
        "video_raw": FINAL_DIR / "final_raw_presentation.mp4",
        "audio": WORKSPACE_DIR / "2_audio_srt" / "final_output.wav",
        "module1_subtitle": WORKSPACE_DIR / "2_audio_srt" / "final_output.srt",
        "subtitle": WORKSPACE_DIR / "2_audio_srt" / "final_short.srt",
        "scene_timeline": WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json",
        "fine_grained_timeline": WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json",
        "poster_mapping": WORKSPACE_DIR / "3_visual_template" / "poster_mapping.json",
        "story_plan": WORKSPACE_DIR / "3_visual_template" / "story_plan.json",
        "visual_prompt_plan": WORKSPACE_DIR / "3_visual_template" / "visual_prompt_plan.json",
        "html": WORKSPACE_DIR / "3_visual_template" / "index.html",
        "archive_manifest": PROJECT_ROOT / "Archives" / job.id / "manifest.txt",
    }
    artifacts: dict[str, str] = {}
    for key, source in candidates.items():
        if source.exists():
            target = job_dir / source.name
            shutil.copy2(source, target)
            artifacts[key] = f"/api/jobs/{job.id}/artifacts/{target.name}"
            register_job_asset(job, target, f"generation_artifact:{key}")
    parts_dir = job_dir / "artifacts" / "parts"
    if parts_dir.is_dir():
        for path in sorted(parts_dir.glob("part_*_with_subtitles.mp4")):
            key = f"part_{path.name.split('_', 2)[1]}_video_with_subtitles"
            artifacts[key] = f"/api/jobs/{job.id}/artifacts/parts/{path.name}"
        for path in sorted(parts_dir.glob("part_*_raw.mp4")):
            key = f"part_{path.name.split('_', 2)[1]}_video_raw"
            artifacts[key] = f"/api/jobs/{job.id}/artifacts/parts/{path.name}"
    return artifacts


def archive_project_assets(job: Job) -> Path:
    archive_dir = PROJECT_ROOT / "Archives" / job.id
    final_video_dir = archive_dir / "模块5产出_最终成片"
    final_video_dir.mkdir(parents=True, exist_ok=True)

    copies = [
        (WORKSPACE_DIR / "2_audio_srt" / "final_output.wav", archive_dir / "模块1产出_配音.wav"),
        (WORKSPACE_DIR / "2_audio_srt" / "final_short.srt", archive_dir / "模块2产出_精准字幕.srt"),
        (
            WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json",
            archive_dir / "模块3产出_剧本.json",
        ),
        (
            WORKSPACE_DIR / "3_visual_template" / "story_plan.json",
            archive_dir / "Agent1产出_全文故事规划.json",
        ),
        (
            WORKSPACE_DIR / "3_visual_template" / "visual_prompt_plan.json",
            archive_dir / "Agent2产出_分镜提示词规划.json",
        ),
        (
            WORKSPACE_DIR / "3_visual_template" / "poster_mapping.json",
            archive_dir / "模块4产出_海报映射.json",
        ),
        (WORKSPACE_DIR / "3_visual_template" / "index.html", archive_dir / "模块4产出_HTML页面.html"),
    ]
    for source, target in copies:
        if source.exists():
            shutil.copy2(source, target)

    for video in FINAL_DIR.glob("*.mp4"):
        shutil.copy2(video, final_video_dir / video.name)

    visual_assets = WORKSPACE_DIR / "3_visual_template" / "assets"
    if visual_assets.is_dir():
        shutil.copytree(
            visual_assets,
            archive_dir / "模块4素材",
            dirs_exist_ok=True,
        )

    manifest = archive_dir / "manifest.txt"
    manifest.write_text(
        "\n".join(
            [
                f"job_id={job.id}",
                f"created_at={time.strftime('%Y-%m-%d %H:%M:%S')}",
                "module1=模块1产出_配音.wav",
                "module2=模块2产出_精准字幕.srt",
                "module3=模块3产出_剧本.json",
                "agent1=Agent1产出_全文故事规划.json",
                "agent2=Agent2产出_分镜提示词规划.json",
                "poster_mapping=模块4产出_海报映射.json",
                "module4=模块4产出_HTML页面.html",
                "module5=模块5产出_最终成片/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for archived_path in archive_dir.rglob("*"):
        if archived_path.is_file():
            register_job_asset(
                job,
                archived_path,
                "archive",
                {"archive_relative_path": str(archived_path.relative_to(archive_dir))},
            )
    return archive_dir


def scene_text_length(scenes: list[dict[str, Any]]) -> int:
    return sum(len(str(item.get("text_content") or "").strip()) for item in scenes)


def load_scene_timeline() -> list[dict[str, Any]]:
    timeline_path = WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json"
    if not timeline_path.exists():
        raise FileNotFoundError(f"找不到模块 2 分镜资产: {timeline_path}")
    data = json.loads(timeline_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("scene_timeline.json 必须是非空数组")
    return [dict(item) for item in data if isinstance(item, dict)]


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    milliseconds = int(round((seconds - int(seconds)) * 1000))
    if milliseconds >= 1000:
        whole_seconds += 1
        milliseconds -= 1000
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def write_srt_from_scenes(scenes: list[dict[str, Any]], path: Path) -> None:
    blocks: list[str] = []
    for index, item in enumerate(scenes, 1):
        text = str(item.get("text_content") or "").strip()
        if not text:
            continue
        start = float(item.get("start") or 0)
        end = max(float(item.get("end") or start + 0.2), start + 0.2)
        blocks.append(
            f"{index}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n"
        )
    if not blocks:
        raise RuntimeError("分段字幕为空")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")


def split_scenes_by_text_length(
    scenes: list[dict[str, Any]],
    threshold: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0
    for scene in scenes:
        text_len = len(str(scene.get("text_content") or "").strip())
        if current and current_len + text_len > threshold:
            groups.append(current)
            current = []
            current_len = 0
        current.append(scene)
        current_len += text_len
    if current:
        groups.append(current)
    return groups


def split_scenes_by_topic_with_llm(
    scenes: list[dict[str, Any]],
    threshold: int,
    story_plan: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]] | None:
    if not gemini_configured():
        return None
    compact = []
    for index, scene in enumerate(scenes, 1):
        text = str(scene.get("text_content") or "").strip()
        compact.append(
            {
                "index": index,
                "slide_id": str(scene.get("slide_id") or scene.get("id") or f"scene_{index:03d}"),
                "chars": len(text),
                "text": text,
            }
        )
    science_mode = (story_plan or {}).get("content_mode") == "science_explainer"
    role_and_goal = (
        "你是科普口播视频 Agent 1 的知识结构分段执行器。"
        "你的任务是通读全文字幕段，识别问题提出、原理解释、因果链、案例或实验、结论与行动建议，"
        "按能够独立讲清一个知识点的完整单元切分成多个连续段落。"
        "不要把一个因果链或同一案例的条件与结论拆散，也不要把不相关知识点硬凑在一起。"
        if science_mode
        else (
            "你是故事视频 Agent 1 的叙事分段执行器。"
            "你的任务是通读全文字幕段，按人物、事件、场景、冲突与悬念的主题完整性切分成多个连续段落。"
        )
    )
    system_prompt = (
        role_and_goal
        + "只输出严格 JSON 数组，不要 Markdown，不要解释。"
        "不要为了接近字数上限而硬凑；几百字、一千字、两千字都可以，只要内容完整。"
        f"每段目标是不超过 {threshold} 个中文字符。"
        "只有当一个完整内容单元本身确实超过上限时，才允许超过上限。"
        "断点必须落在输入字幕段之间，不能拆开任何一个字幕段。"
        "每一段必须连续覆盖，不得遗漏或重复。"
        "输出格式：[{\"start_index\":1,\"end_index\":5,\"reason\":\"这一段的主题或知识点\"}]。"
    )
    user_prompt = json.dumps(
        {"story_context": story_plan or {}, "subtitle_scenes": compact},
        ensure_ascii=False,
    )
    try:
        response = generate_gemini_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.15,
            response_mime_type="application/json",
        )
        raw = parse_json_response(response)
    except (GeminiError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(raw, list) or not raw:
        return None

    groups: list[list[dict[str, Any]]] = []
    expected_start = 1
    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            start = int(item.get("start_index"))
            end = int(item.get("end_index"))
        except (TypeError, ValueError):
            return None
        if start != expected_start or end < start or end > len(scenes):
            return None
        group = scenes[start - 1 : end]
        if not group:
            return None
        if scene_text_length(group) > threshold and len(group) > 1:
            groups.extend(split_scenes_by_text_length(group, threshold))
        else:
            groups.append(group)
        expected_start = end + 1
    if expected_start != len(scenes) + 1:
        return None
    return groups if len(groups) > 1 else None


def normalize_segment_scenes(scenes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float, float]:
    start_offset = min(float(item.get("start") or 0) for item in scenes)
    end_time = max(float(item.get("end") or start_offset + 0.2) for item in scenes)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(scenes, 1):
        start = round(max(0.0, float(item.get("start") or 0) - start_offset), 3)
        end = round(max(start + 0.2, float(item.get("end") or 0) - start_offset), 3)
        normalized.append(
            {
                **item,
                "id": f"segment_{index:03d}",
                "slide_id": f"scene_{index:03d}",
                "start": start,
                "end": end,
            }
        )
    return normalized, start_offset, end_time


def slice_audio(
    job: Job,
    store: JobStore,
    source_audio: Path,
    start: float,
    end: float,
    output: Path,
    label: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        job,
        store,
        [
            ffmpeg_binary(),
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source_audio),
            "-vn",
            "-acodec",
            "pcm_s16le",
            str(output),
        ],
        label,
    )


def render_semantic_visual_video(
    job: Job,
    store: JobStore,
    request: dict[str, Any],
    *,
    resume: bool = False,
    story_plan_path: Path | None = None,
    story_plan_is_global: bool = True,
) -> None:
    store.raise_if_cancelled(job)
    fine_path = WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json"
    if resume and fine_path.is_file() and fine_path.stat().st_size > 0:
        store.log(job, f"断点续跑：复用模块 3 剧本: {fine_path}")
    else:
        fine_path = generate_fine_grained_timeline()
    store.raise_if_cancelled(job)
    store.log(job, f"模块 3 剧本写入: {fine_path}")

    visual_backend = str(request.get("visual_backend") or "poster").lower()
    if visual_backend in {"html", "gpt", "html-gpt"}:
        html_path, provider = generate_visual_html(
            api_key=request.get("api_key") or None,
            base_url=request.get("base_url") or None,
            model=request.get("model") or None,
            style=request.get("visual_style") or "video-edit-agent",
        )
        store.log(job, f"HTML 模板写入: {html_path}")
        store.log(job, f"视觉生成来源: {provider}")
    elif visual_backend in {"poster", "online-poster", "runninghub"}:
        poster_env = {"VOICE_OVER_VIDEO_JOB_ID": job.id}
        poster_env["CONTENT_MODE"] = str(request.get("content_mode") or "urban_suspense")
        visual_pacing = visual_pacing_settings(request)
        poster_env.update({
            "VISUAL_PACING_PRESET": visual_pacing["preset"],
            "VISUAL_MIN_DURATION_SECONDS": str(visual_pacing["min_duration"]),
            "VISUAL_TARGET_DURATION_SECONDS": str(visual_pacing["target_duration"]),
            "VISUAL_MAX_DURATION_SECONDS": str(visual_pacing["max_duration"]),
            "VISUAL_MAX_SLIDES_PER_IMAGE": str(visual_pacing["max_slides"]),
        })
        store.log(
            job,
            "画面节奏：%s（每张至少 %ss，目标 %ss，最长 %ss，最多 %s 个字幕片段）"
            % (
                visual_pacing["label"],
                visual_pacing["min_duration"],
                visual_pacing["target_duration"],
                visual_pacing["max_duration"],
                visual_pacing["max_slides"],
            ),
        )
        if story_plan_path is not None:
            poster_env["STORY_AGENT_PLAN_PATH"] = str(story_plan_path.resolve())
            poster_env["STORY_AGENT_PLAN_IS_GLOBAL"] = "1" if story_plan_is_global else "0"
        if resume:
            poster_env["VOICE_OVER_VIDEO_RESUME"] = "1"
        visual_prompt_system = str(request.get("visual_prompt_system") or "").strip()
        if visual_prompt_system:
            poster_env["VISUAL_PROMPT_SYSTEM"] = visual_prompt_system
        global_character_prompt = str(request.get("global_character_prompt") or "").strip()
        if global_character_prompt:
            poster_env["GLOBAL_CHARACTER_PROMPT"] = global_character_prompt
        if str(request.get("visual_prompt_mode") or "simple") == "simple":
            visual_style_prompt = str(request.get("visual_style_prompt") or "").strip()
            if visual_style_prompt:
                poster_env["VISUAL_STYLE_PROMPT"] = visual_style_prompt
        run_command(
            job,
            store,
            [sys.executable, "module4_online_poster.py"],
            STEPS[4][1],
            extra_env=poster_env,
        )
    else:
        raise ValueError(f"不支持的视觉后端: {visual_backend}")

    store.raise_if_cancelled(job)
    run_command(job, store, [sys.executable, "module5_video_render.py"], STEPS[5][1])


def copy_part_outputs(job: Job, part_index: int) -> dict[str, Path]:
    part_dir = JOBS_DIR / job.id / "artifacts" / "parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_image_dir = part_dir / f"part_{part_index:03d}_images"
    copied = {
        "video_with_subtitles": part_dir / f"part_{part_index:03d}_with_subtitles.mp4",
        "video_raw": part_dir / f"part_{part_index:03d}_raw.mp4",
        "audio": part_dir / f"part_{part_index:03d}_audio.wav",
        "module1_subtitle": part_dir / f"part_{part_index:03d}_module1.srt",
        "subtitle": part_dir / f"part_{part_index:03d}.srt",
        "scene_timeline": part_dir / f"part_{part_index:03d}_scene_timeline.json",
        "fine_grained_timeline": part_dir / f"part_{part_index:03d}_fine_grained_timeline.json",
        "poster_mapping": part_dir / f"part_{part_index:03d}_poster_mapping.json",
        "story_plan": part_dir / f"part_{part_index:03d}_story_plan.json",
        "visual_prompt_plan": part_dir / f"part_{part_index:03d}_visual_prompt_plan.json",
        "html": part_dir / f"part_{part_index:03d}.html",
    }
    sources = {
        "video_with_subtitles": FINAL_DIR / "final_with_subtitles.mp4",
        "video_raw": FINAL_DIR / "final_raw_presentation.mp4",
        "audio": WORKSPACE_DIR / "2_audio_srt" / "final_output.wav",
        "module1_subtitle": WORKSPACE_DIR / "2_audio_srt" / "final_output.srt",
        "subtitle": WORKSPACE_DIR / "2_audio_srt" / "final_short.srt",
        "scene_timeline": WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json",
        "fine_grained_timeline": WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json",
        "poster_mapping": WORKSPACE_DIR / "3_visual_template" / "poster_mapping.json",
        "story_plan": WORKSPACE_DIR / "3_visual_template" / "story_plan.json",
        "visual_prompt_plan": WORKSPACE_DIR / "3_visual_template" / "visual_prompt_plan.json",
        "html": WORKSPACE_DIR / "3_visual_template" / "index.html",
    }
    result: dict[str, Path] = {}
    for key, source in sources.items():
        if source.exists():
            shutil.copy2(source, copied[key])
            result[key] = copied[key]
            register_job_asset(job, copied[key], f"generation_part:{key}", {"part_index": part_index})
    source_images = WORKSPACE_DIR / "3_visual_template" / "assets"
    if source_images.is_dir():
        shutil.copytree(source_images, part_image_dir, dirs_exist_ok=True)
        result["images"] = part_image_dir
        for image_path in part_image_dir.iterdir():
            if image_path.is_file():
                register_job_asset(
                    job,
                    image_path,
                    "generation_part:image",
                    {"part_index": part_index},
                )
    return result


def _json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _available_output_path(project_name: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUTPUT_DIR / normalize_project_name(project_name)
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = OUTPUT_DIR / f"{base.name}_{suffix}"
        suffix += 1
    return candidate


def _copy_visual_segment(
    *,
    mapping_path: Path,
    timeline_path: Path,
    source_image_dir: Path,
    output_image_dir: Path,
    file_prefix: str,
    time_offset: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    mapping = _json_list(mapping_path)
    scenes = _json_list(timeline_path)
    if not scenes:
        return [], [], 0.0
    scenes_by_id = {str(item.get("slide_id") or ""): item for item in scenes}
    adjusted_scenes = [
        {
            **item,
            "start": round(float(item.get("start") or 0) + time_offset, 3),
            "end": round(float(item.get("end") or 0) + time_offset, 3),
        }
        for item in scenes
    ]
    poster_timeline: list[dict[str, Any]] = []
    for item in mapping:
        poster_id = str(item.get("macro_scene_id") or "").strip()
        if not poster_id or not source_image_dir.is_dir():
            continue
        candidates = sorted(path for path in source_image_dir.glob(f"{poster_id}_*") if path.is_file())
        if not candidates:
            continue
        source_image = candidates[0]
        output_name = f"{file_prefix}_{source_image.name}" if file_prefix else source_image.name
        output_image = output_image_dir / output_name
        shutil.copy2(source_image, output_image)
        output_image.with_suffix(".txt").write_text(
            str(item.get("image_prompt") or "").strip(),
            encoding="utf-8",
        )
        included = [
            scenes_by_id[slide_id]
            for slide_id in (str(value) for value in item.get("includes_slides", []))
            if slide_id in scenes_by_id
        ]
        if included:
            poster_timeline.append(
                {
                    "start": min(float(scene.get("start") or 0) for scene in included) + time_offset,
                    "end": max(float(scene.get("end") or 0) for scene in included) + time_offset,
                    "url": f"../image/{output_name}",
                }
            )
    duration = max(float(item.get("end") or 0) for item in scenes)
    return adjusted_scenes, poster_timeline, duration


def organize_project_output(job: Job, request: dict[str, Any]) -> Path:
    project_name = normalize_project_name(str(request.get("project_name") or ""))
    final_dir = _available_output_path(project_name)
    temp_dir = OUTPUT_DIR / f".{final_dir.name}.{job.id}.building"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    input_dir = temp_dir / "input"
    image_dir = temp_dir / "image"
    video_dir = temp_dir / "video"
    other_dir = temp_dir / "other"
    for directory in (input_dir, image_dir, video_dir, other_dir):
        directory.mkdir(parents=True, exist_ok=True)

    try:
        script_path = JOBS_DIR / job.id / "script.txt"
        fallback_script = WORKSPACE_DIR / "1_original_text.txt"
        selected_script = script_path if script_path.is_file() and script_path.stat().st_size else fallback_script
        if selected_script.is_file() and selected_script.stat().st_size:
            shutil.copy2(selected_script, input_dir / "文案.txt")

        final_audio = WORKSPACE_DIR / "2_audio_srt" / "final_output.wav"
        if final_audio.is_file():
            shutil.copy2(final_audio, input_dir / "配音.wav")
        if request.get("skip_tts") and job.user_id is not None and request.get("source_audio_id"):
            source_audio = user_upload_path(int(job.user_id), str(request["source_audio_id"]))
            shutil.copy2(source_audio, input_dir / f"原始配音{source_audio.suffix.lower()}")

        combined_scenes: list[dict[str, Any]] = []
        combined_posters: list[dict[str, Any]] = []
        parts_dir = JOBS_DIR / job.id / "artifacts" / "parts"
        part_mappings = sorted(parts_dir.glob("part_*_poster_mapping.json")) if parts_dir.is_dir() else []
        if part_mappings:
            time_offset = 0.0
            for mapping_path in part_mappings:
                part_name = mapping_path.name.removesuffix("_poster_mapping.json")
                scenes, posters, duration = _copy_visual_segment(
                    mapping_path=mapping_path,
                    timeline_path=parts_dir / f"{part_name}_fine_grained_timeline.json",
                    source_image_dir=parts_dir / f"{part_name}_images",
                    output_image_dir=image_dir,
                    file_prefix=part_name,
                    time_offset=time_offset,
                )
                combined_scenes.extend(scenes)
                combined_posters.extend(posters)
                time_offset += duration
        else:
            scenes, posters, _ = _copy_visual_segment(
                mapping_path=WORKSPACE_DIR / "3_visual_template" / "poster_mapping.json",
                timeline_path=WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json",
                source_image_dir=WORKSPACE_DIR / "3_visual_template" / "assets",
                output_image_dir=image_dir,
                file_prefix="",
                time_offset=0.0,
            )
            combined_scenes.extend(scenes)
            combined_posters.extend(posters)

        video_copies = {
            FINAL_DIR / "final_with_subtitles.mp4": video_dir / "最终视频_字幕版.mp4",
            FINAL_DIR / "final_raw_presentation.mp4": video_dir / "最终视频_纯净版.mp4",
        }
        for source, target in video_copies.items():
            if source.is_file():
                shutil.copy2(source, target)

        final_srt = WORKSPACE_DIR / "2_audio_srt" / "final_short.srt"
        output_srt = other_dir / "最终字幕.srt"
        if final_srt.is_file():
            shutil.copy2(final_srt, output_srt)

        if combined_scenes and combined_posters:
            from module4_video_render import write_html
            from module5_video_render import with_subtitles

            output_html = write_html(
                combined_scenes,
                sorted(combined_posters, key=lambda item: float(item["start"])),
                html_path=other_dir / "最终画面.html",
                audio_url="../input/配音.wav",
            )
        if output_srt.is_file():
            output_html.write_text(
                with_subtitles(output_html.read_text(encoding="utf-8"), output_srt),
                encoding="utf-8",
            )

        # Keep a self-contained post-production manifest in output.  The visual
        # editor must keep working after workspace/jobs has been cleaned up.
        output_mapping: list[dict[str, Any]] = []
        for image in sorted(image_dir.glob("*")):
            if image.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            macro_id = re.sub(r"_[0-9a-f]{8,}$", "", image.stem, flags=re.IGNORECASE)
            prompt_file = image.with_suffix(".txt")
            output_mapping.append({
                "macro_scene_id": macro_id,
                "image_prompt": prompt_file.read_text(encoding="utf-8") if prompt_file.is_file() else "",
                "includes_slides": [],
            })
        (other_dir / "画面映射.json").write_text(
            json.dumps(output_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (other_dir / "画面时间线.json").write_text(
            json.dumps(combined_scenes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (other_dir / "画面修改清单.json").write_text(
            json.dumps({"job_id": job.id, "project_name": final_dir.name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        temp_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    for output_path in final_dir.rglob("*"):
        if output_path.is_file():
            register_job_asset(
                job,
                output_path,
                "project_output",
                {"project_name": final_dir.name},
            )
    return final_dir


def concat_videos(job: Job, store: JobStore, inputs: list[Path], output: Path, label: str) -> None:
    if not inputs:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = output.with_suffix(".concat.txt")
    lines = []
    for path in inputs:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_command(
        job,
        store,
        [
            ffmpeg_binary(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output),
        ],
        label,
    )
    list_path.unlink(missing_ok=True)
    register_job_asset(job, output, f"generation_artifact:{label}")


def render_downstream(job: Job, store: JobStore, request: dict[str, Any], *, resume: bool = False) -> None:
    threshold = int(request.get("split_text_threshold") or 3000)
    auto_split = bool(request.get("auto_split_long_text", True))
    scenes = load_scene_timeline()
    total_chars = scene_text_length(scenes)
    hierarchical_min_chars = max(
        threshold * 2,
        int(os.getenv("AGENT_HIERARCHICAL_MIN_CHARS", "6000")),
    )
    hierarchical_planning = total_chars > hierarchical_min_chars
    if not auto_split or total_chars <= threshold:
        store.log(job, f"Agent 自适应规划：短文模式（{total_chars} 字），仅执行一次全文规划")
        store.update(job, step="semantic", progress=54, message=STEPS[3][1])
        render_semantic_visual_video(job, store, request, resume=resume)
        return

    source_dir = WORKSPACE_DIR / "temp_chunks" / "long_split_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    global_story_plan = source_dir / "story_plan.full.json"
    store.log(job, f"Agent 1：开始通读长文全文（{len(scenes)} 个片段）")
    story_plan = load_or_create_story_plan(
        scenes,
        resume=resume,
        path=global_story_plan,
        content_mode=str(request.get("content_mode") or "urban_suspense"),
    )
    store.log(job, f"Agent 1：全文故事上下文已保存: {global_story_plan}")
    if hierarchical_planning:
        store.log(job, f"Agent 自适应规划：超长文模式（>{hierarchical_min_chars} 字），启用全文总纲 + 分段细化")
    else:
        store.log(job, f"Agent 自适应规划：普通长文模式（≤{hierarchical_min_chars} 字），各段共用全文总纲")

    groups = split_scenes_by_topic_with_llm(scenes, threshold, story_plan)
    if groups:
        store.log(job, f"Agent 1：结合全文上下文拆为 {len(groups)} 个叙事段")
    else:
        groups = split_scenes_by_text_length(scenes, threshold)
        store.log(job, "长文主题分段不可用，已回退到字幕边界字数分段")
    if len(groups) <= 1:
        store.update(job, step="semantic", progress=54, message=STEPS[3][1])
        render_semantic_visual_video(
            job,
            store,
            request,
            resume=resume,
            story_plan_path=global_story_plan,
        )
        return

    store.log(job, f"长文自动分段: {total_chars} 字，最大每段 {threshold} 字，拆为 {len(groups)} 段")
    full_audio = source_dir / "final_output.full.wav"
    full_srt = source_dir / "final_short.full.srt"
    full_timeline = source_dir / "scene_timeline.full.json"
    shutil.copy2(WORKSPACE_DIR / "2_audio_srt" / "final_output.wav", full_audio)
    shutil.copy2(WORKSPACE_DIR / "2_audio_srt" / "final_short.srt", full_srt)
    shutil.copy2(WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json", full_timeline)
    store.log(job, "Agent 1：所有视频分段将共用同一份全文故事上下文")

    part_with_subtitles: list[Path] = []
    part_raw: list[Path] = []
    for index, group in enumerate(groups, 1):
        store.raise_if_cancelled(job)
        progress = min(84, 50 + int(index / len(groups) * 34))
        store.update(
            job,
            step="semantic",
            progress=progress,
            message=f"长文分段渲染 {index}/{len(groups)}",
        )
        normalized, start, end = normalize_segment_scenes(group)
        segment_plan_path = global_story_plan
        segment_plan_is_global = True
        if hierarchical_planning:
            segment_plan_path = source_dir / f"story_plan.part_{index:03d}.json"
            store.log(job, f"Agent 1B：开始细化第 {index}/{len(groups)} 个长文分段")
            load_or_create_segment_story_plan(
                normalized,
                story_plan,
                resume=resume,
                path=segment_plan_path,
                content_mode=str(request.get("content_mode") or "urban_suspense"),
            )
            segment_plan_is_global = False
        shutil.rmtree(WORKSPACE_DIR / "3_visual_template", ignore_errors=True)
        shutil.rmtree(FINAL_DIR, ignore_errors=True)
        (WORKSPACE_DIR / "3_visual_template").mkdir(parents=True, exist_ok=True)
        (WORKSPACE_DIR / "2_audio_srt").mkdir(parents=True, exist_ok=True)
        (WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json").write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_srt_from_scenes(normalized, WORKSPACE_DIR / "2_audio_srt" / "final_short.srt")
        slice_audio(
            job,
            store,
            full_audio,
            start,
            end,
            WORKSPACE_DIR / "2_audio_srt" / "final_output.wav",
            f"切分第 {index} 段音频",
        )
        store.log(job, f"开始渲染第 {index}/{len(groups)} 段: {start:.2f}s - {end:.2f}s")
        render_semantic_visual_video(
            job,
            store,
            request,
            resume=resume,
            story_plan_path=segment_plan_path,
            story_plan_is_global=segment_plan_is_global,
        )
        copied = copy_part_outputs(job, index)
        if "video_with_subtitles" in copied:
            part_with_subtitles.append(copied["video_with_subtitles"])
        if "video_raw" in copied:
            part_raw.append(copied["video_raw"])

    shutil.rmtree(WORKSPACE_DIR / "3_visual_template", ignore_errors=True)
    (WORKSPACE_DIR / "3_visual_template").mkdir(parents=True, exist_ok=True)
    shutil.copy2(full_audio, WORKSPACE_DIR / "2_audio_srt" / "final_output.wav")
    shutil.copy2(full_srt, WORKSPACE_DIR / "2_audio_srt" / "final_short.srt")
    shutil.copy2(full_timeline, WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json")
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    store.update(job, step="render", progress=88, message="拼接分段视频")
    concat_videos(
        job,
        store,
        part_with_subtitles,
        FINAL_DIR / "final_with_subtitles.mp4",
        "拼接字幕版分段视频",
    )
    concat_videos(
        job,
        store,
        part_raw,
        FINAL_DIR / "final_raw_presentation.mp4",
        "拼接纯净版分段视频",
    )
    store.log(job, "分段视频已按顺序拼接完成")


def run_pipeline(job: Job, store: JobStore, *, resume: bool = False) -> None:
    store.raise_if_cancelled(job)
    request = job.request
    job_dir = JOBS_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    if not resume:
        reset_generation_workspace()
    store.log(job, "已清理本轮生成的共享 workspace 旧产物")
    if resume:
        store.log(job, "断点续跑：保留 workspace 中已生成的音频、字幕、分镜和海报")
    script = str(request.get("script") or "")
    script_path = job_dir / "script.txt"
    script_path.write_text(script, encoding="utf-8")
    if script.strip():
        register_job_asset(job, script_path, "source_script")
        (WORKSPACE_DIR / "1_original_text.txt").write_text(script, encoding="utf-8")

    audio_path = WORKSPACE_DIR / "2_audio_srt" / "final_output.wav"
    module1_srt_path = WORKSPACE_DIR / "2_audio_srt" / "final_output.srt"
    subtitle_path = WORKSPACE_DIR / "2_audio_srt" / "final_short.srt"
    scene_timeline_path = WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json"
    skip_existing_tts = (
        resume
        and audio_path.is_file()
        and audio_path.stat().st_size > 0
        and (module1_srt_path.is_file() or subtitle_path.is_file())
    )

    skip_tts = bool(request.get("skip_tts"))
    if skip_existing_tts:
        store.update(job, status="running", step="tts", progress=30, message="断点续跑：复用配音")
        store.log(job, "断点续跑：检测到已有 final_output.wav，跳过模块 1")
    elif skip_tts:
        store.update(job, status="running", step="tts", progress=8, message="导入已有配音")
        prepare_uploaded_audio(job, store, str(request.get("source_audio_id") or ""))
        store.log(job, "已跳过模块 1：使用上传配音作为 final_output.wav")
    else:
        store.update(job, status="running", step="tts", progress=8, message=STEPS[0][1])
        tts_command = [
            sys.executable,
            "module1_agent_director.py",
            "--text",
            str(script_path),
            "--job-id",
            job.id,
        ]
        if job.user_id is not None:
            tts_command.extend(["--user-id", str(job.user_id)])
        tts_command.extend(
            [
                "--tts-voice-id",
                  str(request.get("tts_voice_id") or "voice_05.wav"),
                "--tts-speed",
                str(request.get("tts_speed", 1)),
                "--tts-volume",
                str(request.get("tts_volume", 1)),
                "--tts-pitch",
                str(request.get("tts_pitch", 0)),
                "--tts-parallelism",
                str(request.get("tts_parallelism", 2)),
                "--tts-english-normalization",
                "true" if request.get("tts_english_normalization", False) else "false",
            ]
        )
        emotion = str(request.get("tts_emotion") or "").strip()
        if emotion:
            tts_command.extend(["--tts-emotion", emotion])
        pronunciation = str(request.get("tts_pronunciation") or "").strip()
        if pronunciation:
            tts_command.extend(["--tts-pronunciation", pronunciation])
        run_command(
            job,
            store,
            tts_command,
            STEPS[0][1],
        )

    store.raise_if_cancelled(job)
    if request.get("module1_only"):
        artifacts = copy_artifacts(job)
        store.update(
            job,
            status="completed",
            step="completed",
            progress=100,
            message="模块 1 配音生成完成",
            artifacts=artifacts,
        )
        store.log(job, "模块 1 独立任务完成：已跳过 ASR、Agent、出图和视频合成")
        return

    if resume and scene_timeline_path.is_file() and scene_timeline_path.stat().st_size > 0:
        store.update(job, step="scene", progress=44, message="断点续跑：复用分镜")
        store.log(job, "断点续跑：检测到已有 scene_timeline.json，跳过模块 2")
    else:
        store.update(job, step="scene", progress=32, message=STEPS[1][1])
        asr_python = resolve_asr_python()
        store.log(job, f"ASR Python: {asr_python}")
        run_command(
            job,
            store,
            [asr_python, "module2_scene_director.py"],
            STEPS[1][1],
        )

    store.raise_if_cancelled(job)
    store.update(job, step="correct", progress=45, message=STEPS[2][1])
    if resume and scene_timeline_path.is_file() and scene_timeline_path.stat().st_size > 0:
        store.log(job, "断点续跑：分镜已存在，跳过模块 2.5")
    elif request.get("skip_text_correction"):
        write_original_text_from_asr(job)
        store.log(job, "已跳过模块 2.5：使用 ASR 字幕作为后续文案")
    else:
        run_command(job, store, [sys.executable, "module2_5_text_corrector.py"], STEPS[2][1])

    store.raise_if_cancelled(job)
    render_downstream(job, store, request, resume=resume)

    store.raise_if_cancelled(job)
    store.update(job, step="archive", progress=96, message=STEPS[6][1])
    output_dir = organize_project_output(job, request)
    store.raise_if_cancelled(job)
    store.log(job, f"项目输出已整理: {output_dir}")

    artifacts = copy_artifacts(job)
    store.raise_if_cancelled(job)
    store.update(
        job,
        status="completed",
        step="completed",
        progress=100,
        message="视频生成完成",
        artifacts=artifacts,
    )
    store.log(job, "全部完成")
