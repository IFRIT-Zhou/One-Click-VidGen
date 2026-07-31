import mimetypes
import json
import os
import queue
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
    delete_generation_job,
    load_generation_jobs,
    record_media_asset,
    upsert_generation_job,
)
from .html_generator import generate_visual_html
from .semantic_timeline import generate_fine_grained_timeline
from .gemini_client import GeminiError, gemini_configured, generate_gemini_text, parse_json_response
from story_agents import load_or_create_story_context, load_or_create_story_plan, story_fingerprint


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
JOBS_DIR = WORKSPACE_DIR / "jobs"
FINAL_DIR = WORKSPACE_DIR / "4_final_video"
OUTPUT_DIR = PROJECT_ROOT / "output"
TTS_OUTPUT_DIR = PROJECT_ROOT / "TTS_Output"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
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
    r"^(?:\[(?P<phase>[^\]]+)\]\s*)?.*?"
    r"(?P<percent>\d{1,3})%\s+"
    r"(?P<label>Streaming frame|Capturing frame|Encoding video|Assembling final video|Render complete)",
    re.I,
)
TTS_SENTENCE_PROGRESS_RE = re.compile(
    r"^\[(?:TTS|QWEN_TTS)_PROGRESS\]\s+配音进度\s+(?P<completed>\d+)/(?P<total>\d+)"
)
POSTER_PROGRESS_RE = re.compile(
    r"^\[POSTER_PROGRESS\]\s*(?P<completed>\d+)\s*/\s*(?P<total>\d+)"
)


class GenerationCancelled(RuntimeError):
    """The user requested that this generation job stop."""


class GenerationPaused(RuntimeError):
    """A step-mode checkpoint deliberately stopped the pipeline."""


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
    "pure_science": {"min_duration": 7.0, "target_duration": 10.0, "max_duration": 16.0, "max_slides": 8},
    "general": {"min_duration": 6.0, "target_duration": 8.0, "max_duration": 12.0, "max_slides": 6},
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
        self._pipeline_owner_id: str | None = None

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
            normalized = re.sub(r"^\[(?:TTS|QWEN_TTS)_PROGRESS\]\s*", "", normalized, count=1)
            if job.step == "tts":
                job.progress = min(29, 8 + round(min(completed, total) / total * 21))
                job.message = normalized[:500]
        poster_progress = POSTER_PROGRESS_RE.search(normalized)
        if poster_progress is not None and job.step == "visual":
            completed = int(poster_progress.group("completed"))
            total = max(1, int(poster_progress.group("total")))
            completed = min(completed, total)
            job.progress = max(job.progress, min(84, 55 + round(completed / total * 29)))
            job.message = f"模块 4：画面生成 {completed}/{total}"
        progress = parse_noisy_progress_log(normalized)
        render_progress_changed = False
        if progress is not None:
            key, percent = progress
            editor_render_mode = str(getattr(job, "_visual_editor_render_mode", "") or "")
            if editor_render_mode:
                if editor_render_mode == "both":
                    mapped_progress = round(percent * 0.5) if "字幕版" in key else 50 + round(percent * 0.5)
                else:
                    mapped_progress = percent
                job.progress = max(job.progress, min(99, mapped_progress))
                job.message = f"画面修改：{key} {percent}%"
                job.updated_at = time.time()
                upsert_generation_job(job.snapshot())
                # The progress bar and task message already expose this detail.
                # Do not flood the main log with renderer frame counters.
                return
            cache_key = f"{job.id}:{key}"
            previous = self._last_progress_log.get(cache_key)
            if previous and percent < 100 and percent - previous[1] < 10:
                return
            self._last_progress_log[cache_key] = (key, percent)
            normalized = f"{key}: {percent}%"
            if job.step == "render":
                if "1/2" in key:
                    mapped_progress = 86 + round(percent * 0.04)
                elif "2/2" in key:
                    mapped_progress = 90 + round(percent * 0.05)
                else:
                    mapped_progress = 86 + round(percent * 0.09)
                job.progress = max(job.progress, min(95, mapped_progress))
                job.message = f"模块 5：{normalized}"
                render_progress_changed = True
        stamp = time.strftime("%H:%M:%S")
        persisted_line = f"[{stamp}] {normalized}"
        job.logs.append(persisted_line)
        job.updated_at = time.time()
        append_generation_job_log(job.id, persisted_line, job.updated_at)
        if tts_progress is not None or poster_progress is not None or render_progress_changed:
            upsert_generation_job(job.snapshot())

    def run_async(self, job: Job, *, resume: bool = False) -> None:
        thread = threading.Thread(target=self._run_guarded, args=(job, resume), daemon=True)
        thread.start()

    def new_job_block_reason(self, user_id: int) -> str | None:
        """The pipeline owns shared workspace/GPU state, so it is intentionally single-job."""
        with self._lock:
            if self._pipeline_owner_id:
                owner = self._jobs.get(self._pipeline_owner_id)
                if owner and owner.status == "cancelled":
                    return "上一个任务正在停止并释放 TTS/GPU 资源，请稍候再开始新任务"
                return "当前已有任务正在运行，请先等待完成或停止后再开始新任务"
            pending = next(
                (
                    item for item in self._jobs.values()
                    if item.user_id == user_id and item.status in {"queued", "running", "waiting_confirmation"}
                ),
                None,
            )
        if pending:
            return "当前已有任务正在等待或运行中，请先完成、停止或断点续跑该任务"
        return None

    def resume(self, job: Job) -> dict[str, Any]:
        with self._lock:
            if job.status in {"queued", "running"}:
                return job.snapshot()
            self._cancel_events[job.id] = threading.Event()
        if str(job.request.get("tts_engine") or "") == "cluster" and (
            job.status == "cancelled"
            or str(job.request.get("_cloud_job_status") or "") in {"failed", "cancelled", "expired"}
        ):
            job.request.pop("_cloud_job_id", None)
            job.request.pop("_cloud_job_status", None)
            job.request["_cloud_tts_attempt"] = int(job.request.get("_cloud_tts_attempt", 1) or 1) + 1
        if job.status == "waiting_confirmation":
            self.log(job, "收到分步确认，将复用已经生成的中间产物继续执行")
        else:
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

    def retry_tts(self, job: Job) -> dict[str, Any]:
        """Restart a step-mode job from Module 1 after its audio review."""
        if job.status != "waiting_confirmation" or str(job.request.get("_step_mode_stage") or "") != "audio":
            raise ValueError("only the audio review checkpoint can retry TTS")
        if bool(job.request.get("skip_tts")):
            raise ValueError("uploaded source audio cannot be regenerated")

        with self._lock:
            self._cancel_events[job.id] = threading.Event()
        job.request.pop("_step_mode_stage", None)
        if str(job.request.get("tts_engine") or "") == "cluster":
            job.request.pop("_cloud_job_id", None)
            job.request.pop("_cloud_job_status", None)
            job.request["_cloud_tts_attempt"] = int(job.request.get("_cloud_tts_attempt", 1) or 1) + 1
        job_dir = JOBS_DIR / job.id
        shutil.rmtree(job_dir / "artifacts", ignore_errors=True)
        shutil.rmtree(job_dir / "step_mode_preview_images", ignore_errors=True)
        self.log(job, "收到重新配音请求：将清理本次任务的中间产物，并从模块 1 重新开始")
        self.update(
            job,
            status="queued",
            step="queued",
            progress=0,
            message="等待重新配音",
            error=None,
            artifacts={},
        )
        self.run_async(job, resume=False)
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
            if job.step == "tts":
                _request_graceful_tts_stop(process)
            else:
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

        is_tts = job.step == "tts"
        is_cluster_tts = is_tts and str(job.request.get("tts_engine") or "") == "cluster"
        self.log(job, "收到停止生成请求")
        self.update(
            job,
            status="cancelled",
            # Keep the phase visible until run_command observes the graceful
            # shutdown; it also tells that loop this is a CUDA-safe stop.
            step="tts" if is_tts else "cancelled",
            message=(
                "正在取消集群云端任务"
                if is_cluster_tts
                else ("正在安全停止配音，等待当前 GPU 推理释放资源" if is_tts else "已停止生成")
            ),
            error=None,
        )
        if process is not None:
            if is_tts:
                self.log(job, "安全停止：已请求 IndexTTS2 在当前推理点退出，不再强制杀死 CUDA 进程")
                _request_graceful_tts_stop(process)
            else:
                _terminate_process_tree(process)
        return job.snapshot()

    def delete(self, job: Job) -> None:
        """Remove a terminal task and only the files explicitly owned by it."""
        if job.status not in TERMINAL_JOB_STATUSES:
            raise ValueError("运行中的任务不能删除，请先停止并等待它完全结束")
        with self._lock:
            process = self._processes.get(job.id)
            still_stopping = self._pipeline_owner_id == job.id or (
                process is not None and process.poll() is None
            )
        if still_stopping:
            raise ValueError("该任务仍在停止并释放资源，请等待日志显示已安全停止后再删除")

        # All task-local work is namespaced by the immutable job id.
        shutil.rmtree(JOBS_DIR / job.id, ignore_errors=True)
        shutil.rmtree(WORKSPACE_DIR / "temp_chunks" / job.id, ignore_errors=True)

        # Output folders have user-editable names, so never infer ownership from
        # the folder name.  Only delete archives whose own metadata names this job.
        for root in (OUTPUT_DIR, TTS_OUTPUT_DIR):
            if not root.is_dir():
                continue
            for candidate in root.iterdir():
                if not candidate.is_dir() or candidate.name.startswith("."):
                    continue
                owned = False
                for metadata in candidate.rglob("*.json"):
                    try:
                        content = json.loads(metadata.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if isinstance(content, dict) and str(content.get("job_id") or "") == job.id:
                        owned = True
                        break
                if owned:
                    shutil.rmtree(candidate, ignore_errors=True)

        with self._lock:
            self._jobs.pop(job.id, None)
            self._cancel_events.pop(job.id, None)
            self._processes.pop(job.id, None)
            self._last_progress_log.pop(job.id, None)
            if self._pipeline_owner_id == job.id:
                self._pipeline_owner_id = None
        delete_generation_job(job.id, job.user_id)

    def _run_guarded(self, job: Job, resume: bool = False) -> None:
        with self._pipeline_lock:
            with self._lock:
                self._pipeline_owner_id = job.id
            try:
                self.raise_if_cancelled(job)
                run_pipeline(job, self, resume=resume)
            except GenerationPaused:
                # The checkpoint stored the waiting state deliberately.
                return
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
                elif job.step == "tts":
                    # `cancel()` deliberately keeps the TTS phase while CUDA
                    # is winding down.  Once run_command returns here, it is
                    # safe to mark the stop as fully complete.
                    if str(job.request.get("tts_engine") or "") == "cluster":
                        self.log(job, "集群云端任务已停止")
                        self.update(job, step="cancelled", message="已停止集群云端生成", error=None)
                    else:
                        self.log(job, "IndexTTS2 已安全退出，显存释放完成")
                        self.update(job, step="cancelled", message="已安全停止生成", error=None)
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
            finally:
                with self._lock:
                    if self._pipeline_owner_id == job.id:
                        self._pipeline_owner_id = None


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


def _request_graceful_tts_stop(process: subprocess.Popen[Any]) -> None:
    """Ask the outer TTS Python process to unwind without taskkill /T /F.

    A hard taskkill while two IndexTTS2 CUDA children are mid-kernel can leave
    the Windows display driver in a bad state.  Ctrl+Break lets Python unwind
    normally; `run_command` then waits for the process tree to release GPU
    resources instead of escalating automatically.
    """
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
    except (OSError, ProcessLookupError, AttributeError):
        # The process may already be completing its current inference.  In that
        # case waiting is safer than falling back to a forced tree kill.
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


SUBTITLE_VIDEO_STYLES: dict[str, dict[str, str]] = {
    "black_white_outline": {"label": "黑字白描边", "ass": "PrimaryColour=&H00000000,OutlineColour=&H00FFFFFF,BorderStyle=1,Outline=2,Shadow=0"},
    "white_black_outline": {"label": "白字黑描边", "ass": "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0"},
    "yellow_bg_black": {"label": "黄底黑字", "ass": "PrimaryColour=&H00000000,BackColour=&H0000FFFF,BorderStyle=3,Outline=4,Shadow=0"},
    "white_bg_black": {"label": "白底黑字", "ass": "PrimaryColour=&H00000000,BackColour=&H00FFFFFF,BorderStyle=3,Outline=4,Shadow=0"},
    "navy_bg_white": {"label": "默认成片样式（白字深蓝底）", "ass": "PrimaryColour=&H00FFFFFF,BackColour=&H2B341807,BorderStyle=3,Outline=3,Shadow=0"},
}


def system_subtitle_fonts() -> list[str]:
    """Return a practical, stable list of locally installed Windows font families."""
    fonts = {"Microsoft YaHei", "SimHei", "SimSun", "Arial", "Times New Roman"}
    if os.name == "nt":
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                index = 0
                while True:
                    try:
                        name, _value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    family = str(name).split("(", 1)[0].strip()
                    if family:
                        fonts.add(family.split(" & ", 1)[0].strip())
        except OSError:
            pass
    return sorted(font for font in fonts if font)[:300]


def _subtitle_filter_path(path: Path) -> str:
    # FFmpeg filter syntax needs a literal escaped drive colon on Windows.
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def render_standalone_subtitle_video(
    job: Job,
    store: JobStore,
    source: Path,
    srt_path: Path,
    output: Path,
    *,
    style_key: str,
    font_name: str,
) -> None:
    """Burn a completed Module 2 SRT into its original media without rerunning ASR."""
    if style_key not in SUBTITLE_VIDEO_STYLES:
        raise ValueError("未知字幕样式")
    source = source.resolve()
    srt_path = srt_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    font = str(font_name or "Microsoft YaHei").strip().replace("'", "") or "Microsoft YaHei"
    # Keep this independent subtitle tool visually consistent with the main story-video
    # renderer: bold white type, a deep-blue subtitle card, and bottom-centre placement.
    force_style = (
        f"FontName={font},FontSize=12,Bold=1,Alignment=2,MarginV=10,"
        f"{SUBTITLE_VIDEO_STYLES[style_key]['ass']}"
    )
    subtitle_filter = f"subtitles=filename='{_subtitle_filter_path(srt_path)}':charenc=UTF-8:force_style='{force_style}'"
    video_suffixes = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
    if source.suffix.lower() in video_suffixes:
        command = [
            ffmpeg_binary(), "-y", "-i", str(source), "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "copy", str(output),
        ]
        source_label = "原视频"
    else:
        command = [
            ffmpeg_binary(), "-y", "-f", "lavfi", "-i", "color=c=0x101827:s=1920x1080:r=30",
            "-i", str(source), "-vf", subtitle_filter, "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-shortest", str(output),
        ]
        source_label = "音频（自动生成深色背景视频）"
    store.log(job, f"字幕添加：使用{source_label}，样式「{SUBTITLE_VIDEO_STYLES[style_key]['label']}」，字体「{font}」")
    store.update(job, status="running", step="subtitle_render", progress=12, message="正在添加字幕")
    run_command(job, store, command, "字幕添加渲染")
    if not output.is_file() or output.stat().st_size < 1024:
        raise RuntimeError("字幕视频渲染未产生有效文件")


def user_upload_path(user_id: int, filename: str) -> Path:
    root = (WORKSPACE_DIR / "editor" / f"user_{user_id}" / "uploads").resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("找不到上传的配音文件")
    if path.suffix.lower() not in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
        raise ValueError("上传媒体仅支持 mp3、wav、m4a、aac、flac、ogg、mp4、mov、mkv、webm、avi、m4v")
    return path


def user_reference_image_path(user_id: int, filename: str) -> Path:
    root = (WORKSPACE_DIR / "editor" / f"user_{user_id}" / "uploads").resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("找不到上传的主角参考图")
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("主角参考图仅支持 jpg、jpeg、png、webp")
    return path


def bgm_tracks_for_job(job: Job, request: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(request.get("bgm_enabled")) or job.user_id is None:
        return []
    tracks: list[dict[str, Any]] = []
    for item in request.get("bgm_tracks") or []:
        asset_id = str(item.get("asset_id") or "").strip()
        if not asset_id:
            continue
        source = user_upload_path(int(job.user_id), asset_id)
        if source.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError(f"BGM 只支持音频文件：{source.name}")
        tracks.append({
            "path": str(source),
            "asset_id": asset_id,
            "volume_db": max(-60.0, min(6.0, float(item.get("volume_db", -10)))),
        })
    return tracks


def bgm_render_env(job: Job, request: dict[str, Any]) -> dict[str, str]:
    tracks = bgm_tracks_for_job(job, request)
    if not tracks:
        return {}
    return {
        "BGM_TRACKS_JSON": json.dumps(tracks, ensure_ascii=False),
        "BGM_FADE_ENABLED": "1" if bool(request.get("bgm_fade_enabled")) else "0",
        "BGM_FADE_DURATION": str(float(request.get("bgm_fade_duration") or 1)),
    }


def mix_final_videos_with_bgm(job: Job, store: JobStore, request: dict[str, Any]) -> None:
    tracks = bgm_tracks_for_job(job, request)
    if not tracks:
        return
    videos = [
        path for path in (
            FINAL_DIR / "final_with_subtitles.mp4",
            FINAL_DIR / "final_raw_presentation.mp4",
        ) if path.is_file()
    ]
    if not videos:
        return
    command = [sys.executable, "bgm_mixer.py"]
    for video in videos:
        command.extend(["--video", str(video)])
    command.extend(["--tracks-json", json.dumps(tracks, ensure_ascii=False)])
    if bool(request.get("bgm_fade_enabled")):
        command.append("--fade")
    command.extend(["--fade-duration", str(float(request.get("bgm_fade_duration") or 1))])
    store.log(job, f"BGM：按上传顺序混合 {len(tracks)} 首音乐，长度不足时从第一首开始列表循环")
    run_command(job, store, command, "添加背景音乐")


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
        is_video = source.suffix.lower() in VIDEO_EXTENSIONS
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
            "从视频提取音轨为 WAV" if is_video else "转换已有配音为 WAV",
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


def canonical_story_text(request: dict[str, Any], scenes: list[dict[str, Any]]) -> str:
    """Agent 0 reads author text when supplied, otherwise corrected ASR text."""
    authored = str(request.get("script") or "").strip()
    if authored:
        return authored
    return "\n".join(
        str(scene.get("text_content") or "").strip()
        for scene in scenes
        if str(scene.get("text_content") or "").strip()
    ).strip()


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
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        stream_closed = False
        safe_stop_requested = False
        safe_stop_notice_at = 0.0
        while not stream_closed:
            if store.is_cancelled(job):
                if job.step != "tts":
                    _terminate_process_tree(process)
                    raise GenerationCancelled("用户已停止生成")
                # IndexTTS2 owns one or more CUDA child processes.  Do not use
                # taskkill here: wait for Ctrl+Break to let those processes
                # release their CUDA contexts cleanly.
                if not safe_stop_requested:
                    safe_stop_requested = True
                    safe_stop_notice_at = time.monotonic()
                    store.log(job, "安全停止进行中：等待当前 IndexTTS2 推理结束并释放显存…")
                    _request_graceful_tts_stop(process)
                elif time.monotonic() - safe_stop_notice_at >= 30:
                    safe_stop_notice_at = time.monotonic()
                    store.log(job, "仍在安全停止：CUDA 进程尚在退出，继续等待以避免显卡驱动异常…")
            try:
                line = output_queue.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                stream_closed = True
                continue
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
        "story_context": WORKSPACE_DIR / "3_visual_template" / "story_context.json",
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
            WORKSPACE_DIR / "3_visual_template" / "story_context.json",
            archive_dir / "Agent0产出_全文资料.json",
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


def _scene_boundary_after(scene: dict[str, Any]) -> str:
    """Estimate a safe long-form cut when Agent 1 boundaries are unavailable."""
    text = str(scene.get("text_content") or "").strip()
    return "hard" if re.search(r"[。！？!?][”’」』】）)]?$", text) else "soft"


def _pack_contiguous_partitions(
    partitions: list[list[dict[str, Any]]],
    threshold: int,
    boundary_after: list[str] | None = None,
) -> list[list[dict[str, Any]]]:
    """Balance contiguous semantic partitions without crossing the hard cap.

    The previous implementation greedily filled every part to ``threshold``.
    That was safe, but often produced a tiny final video part.  This dynamic
    programme keeps the minimum number of parts, then chooses boundaries near
    the average size and mildly prefers Agent 1's hard semantic boundaries.
    """
    partitions = [list(partition) for partition in partitions if partition]
    if not partitions:
        return []
    threshold = max(1, int(threshold))
    weights = [scene_text_length(partition) for partition in partitions]
    boundary_after = list(boundary_after or [])
    if len(boundary_after) < len(partitions):
        boundary_after.extend(["soft"] * (len(partitions) - len(boundary_after)))

    # A left-to-right fill gives the minimum feasible number of contiguous
    # groups.  Oversized atomic entries are allowed only as a group of one.
    minimum_group_count = 0
    current_weight = 0
    for weight in weights:
        if current_weight and current_weight + weight > threshold:
            minimum_group_count += 1
            current_weight = 0
        if weight > threshold:
            if current_weight:
                minimum_group_count += 1
                current_weight = 0
            minimum_group_count += 1
        else:
            current_weight += weight
    if current_weight:
        minimum_group_count += 1
    minimum_group_count = max(1, minimum_group_count)

    total_weight = sum(weights)
    target = total_weight / minimum_group_count
    count = len(partitions)
    infinity = float("inf")
    costs = [[infinity] * (count + 1) for _ in range(minimum_group_count + 1)]
    previous = [[-1] * (count + 1) for _ in range(minimum_group_count + 1)]
    costs[0][0] = 0.0

    for group_index in range(1, minimum_group_count + 1):
        for end in range(1, count + 1):
            segment_weight = 0
            for start in range(end - 1, -1, -1):
                segment_weight += weights[start]
                oversized_atomic = start == end - 1 and weights[start] > threshold
                if segment_weight > threshold and not oversized_atomic:
                    break
                if costs[group_index - 1][start] == infinity:
                    continue
                deviation = ((segment_weight - target) / max(target, 1.0)) ** 2
                # Strong boundaries are preferred, but never at the cost of a
                # severely lopsided or additional render part.
                boundary_penalty = 0.0
                if end < count and str(boundary_after[end - 1]).lower() != "hard":
                    boundary_penalty = 0.08
                tiny_penalty = 0.0
                if minimum_group_count > 1 and segment_weight < target * 0.55:
                    tiny_penalty = ((target * 0.55 - segment_weight) / max(target, 1.0)) * 3.0
                candidate = costs[group_index - 1][start] + deviation + boundary_penalty + tiny_penalty
                if candidate < costs[group_index][end]:
                    costs[group_index][end] = candidate
                    previous[group_index][end] = start

    if previous[minimum_group_count][count] < 0:
        # Defensive fallback; this should only be reachable for malformed input.
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for partition, weight in zip(partitions, weights):
            if current and scene_text_length(current) + weight > threshold:
                groups.append(current)
                current = []
            current.extend(partition)
        if current:
            groups.append(current)
        return groups

    cuts: list[tuple[int, int]] = []
    end = count
    for group_index in range(minimum_group_count, 0, -1):
        start = previous[group_index][end]
        cuts.append((start, end))
        end = start
    cuts.reverse()
    return [
        [scene for partition in partitions[start:end] for scene in partition]
        for start, end in cuts
    ]


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


def correct_asr_subtitles_with_language_model(job: Job, store: JobStore) -> None:
    """Correct ASR wording while keeping each original timestamped segment intact."""
    if not gemini_configured():
        raise RuntimeError("未配置语言模型 API Key：请在左侧模型 API Key 面板填写语言模型或通用 API Key")
    scenes = load_scene_timeline()
    batches = [scenes[index:index + 40] for index in range(0, len(scenes), 40)]
    corrected: list[dict[str, Any]] = []
    system_prompt = (
        "你是中文视频字幕校对员。根据语义纠正 ASR 的错别字、同音字、标点和明显断句，"
        "不得添加原音频中不存在的内容。必须保留每个 id 一一对应，不能合并、删除或新增条目。"
        "仅返回 JSON：{\\\"items\\\":[{\\\"id\\\":\\\"原id\\\",\\\"text\\\":\\\"校对后的字幕\\\"}]}。"
    )
    for batch_index, batch in enumerate(batches, 1):
        store.raise_if_cancelled(job)
        payload = [{"id": str(item.get("id") or f"segment_{index:03d}"), "text": str(item.get("text_content") or "")} for index, item in enumerate(batch, 1)]
        store.log(job, f"语言模型字幕校对：第 {batch_index}/{len(batches)} 批（{len(batch)} 条）")
        try:
            response = generate_gemini_text(
                system_prompt=system_prompt,
                user_prompt=json.dumps({"items": payload}, ensure_ascii=False),
                temperature=0.05,
                max_output_tokens=4096,
            )
            parsed = parse_json_response(response)
        except GeminiError as exc:
            raise RuntimeError(f"语言模型字幕校对失败：{exc}") from exc
        items = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            raise RuntimeError("语言模型字幕校对返回格式无效")
        replacement = {
            str(item.get("id") or ""): str(item.get("text") or "").strip().replace("\n", " ")
            for item in items
            if isinstance(item, dict)
        }
        for scene, source in zip(batch, payload):
            updated = dict(scene)
            text = replacement.get(source["id"], "")
            updated["text_content"] = text or str(scene.get("text_content") or "").strip()
            corrected.append(updated)
    timeline_path = WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json"
    timeline_path.write_text(json.dumps(corrected, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt_from_scenes(corrected, WORKSPACE_DIR / "2_audio_srt" / "final_short.srt")
    store.log(job, f"语言模型字幕校对完成：共 {len(corrected)} 条字幕")


def split_scenes_by_text_length(
    scenes: list[dict[str, Any]],
    threshold: int,
) -> list[list[dict[str, Any]]]:
    partitions = [[scene] for scene in scenes]
    boundaries = [_scene_boundary_after(scene) for scene in scenes]
    return _pack_contiguous_partitions(partitions, threshold, boundaries)


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
    science_mode = (story_plan or {}).get("content_mode") in {"science_explainer", "pure_science"}
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

    semantic_partitions: list[list[dict[str, Any]]] = []
    semantic_boundaries: list[str] = []
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
            fragments = split_scenes_by_text_length(group, threshold)
            semantic_partitions.extend(fragments)
            semantic_boundaries.extend(["soft"] * max(0, len(fragments) - 1) + ["hard"])
        else:
            semantic_partitions.append(group)
            semantic_boundaries.append("hard")
        expected_start = end + 1
    if expected_start != len(scenes) + 1:
        return None
    groups = _pack_contiguous_partitions(semantic_partitions, threshold, semantic_boundaries)
    return groups if len(groups) > 1 else None


def split_scenes_by_agent1_boundaries(
    scenes: list[dict[str, Any]],
    threshold: int,
    story_plan: dict[str, Any],
) -> list[list[dict[str, Any]]] | None:
    """Bundle full-text Agent 1 units into renderable parts without cutting an event."""
    units = story_plan.get("semantic_units")
    if not isinstance(units, list) or not units:
        return None
    positions = {str(scene.get("slide_id") or ""): index for index, scene in enumerate(scenes)}
    partitions: list[list[dict[str, Any]]] = []
    boundaries: list[str] = []
    expected = 0
    for unit in units:
        if not isinstance(unit, dict):
            return None
        start = positions.get(str(unit.get("start_slide_id") or ""), -1)
        end = positions.get(str(unit.get("end_slide_id") or ""), -1)
        if start != expected or end < start:
            return None
        partition = scenes[start : end + 1]
        boundary = str(unit.get("boundary_after") or "soft").lower()
        if scene_text_length(partition) > threshold and len(partition) > 1:
            # An unusually large event cannot fit inside the render/API cap.
            # Split only at existing subtitle boundaries and mark artificial
            # internal cuts soft; retain Agent 1's boundary on the final piece.
            fragments = split_scenes_by_text_length(partition, threshold)
            partitions.extend(fragments)
            boundaries.extend(["soft"] * max(0, len(fragments) - 1) + [boundary])
        else:
            partitions.append(partition)
            boundaries.append(boundary)
        expected = end + 1
    if expected != len(scenes):
        return None

    groups = _pack_contiguous_partitions(partitions, threshold, boundaries)
    return groups if groups else None


def project_global_agent1_plan_to_segment(
    global_plan: dict[str, Any],
    all_scenes: list[dict[str, Any]],
    original_scenes: list[dict[str, Any]],
    normalized_scenes: list[dict[str, Any]],
    content_mode: str,
) -> dict[str, Any]:
    """Remap Agent 1's full-text unit boundaries to one locally rendered part."""
    units = global_plan.get("semantic_units")
    if not isinstance(units, list) or len(original_scenes) != len(normalized_scenes):
        raise ValueError("无法将全文 Agent 1 边界投影到当前分段")
    all_positions = {str(scene.get("slide_id") or ""): index for index, scene in enumerate(all_scenes)}
    original_ids = [str(scene.get("slide_id") or "") for scene in original_scenes]
    normalized_ids = [str(scene.get("slide_id") or "") for scene in normalized_scenes]
    segment_positions = {slide_id: index for index, slide_id in enumerate(original_ids)}
    projected_units: list[dict[str, Any]] = []
    expected = 0
    for source_unit in units:
        if not isinstance(source_unit, dict):
            continue
        global_start = all_positions.get(str(source_unit.get("start_slide_id") or ""), -1)
        global_end = all_positions.get(str(source_unit.get("end_slide_id") or ""), -1)
        if global_start == -1 or global_end == -1 or global_end < global_start:
            raise ValueError("全文 Agent 1 边界与当前分段不一致")
        first_global = all_positions.get(original_ids[0], -1)
        last_global = all_positions.get(original_ids[-1], -1)
        if global_end < first_global or global_start > last_global:
            continue
        overlap_start = max(global_start, first_global) - first_global
        overlap_end = min(global_end, last_global) - first_global
        if overlap_start != expected:
            raise ValueError("全文 Agent 1 边界没有完整覆盖当前分段")
        projected_units.append({
            **source_unit,
            "unit_id": f"segment_unit_{len(projected_units) + 1:02d}",
            "start_slide_id": normalized_ids[overlap_start],
            "end_slide_id": normalized_ids[overlap_end],
            # If a giant event had to be split for the render cap, do not
            # falsely make its artificial end a hard semantic boundary.
            "boundary_after": source_unit.get("boundary_after", "hard") if global_end <= last_global else "soft",
        })
        expected = overlap_end + 1
    if expected != len(normalized_scenes):
        raise ValueError("全文 Agent 1 投影后存在未覆盖字幕")
    plan = dict(global_plan)
    plan["semantic_units"] = projected_units
    plan["story_beats"] = [{
        "beat_id": f"segment_beat_{index:02d}",
        "slide_ids": [unit["start_slide_id"], unit["end_slide_id"]],
        "purpose": unit.get("purpose") or "语义推进",
        "emotion": "依据全文上下文",
        "visual_focus": unit.get("visual_focus") or "依据原文",
        "visual_pacing": unit.get("visual_pacing") or "normal",
    } for index, unit in enumerate(projected_units, 1)]
    plan["source_fingerprint"] = story_fingerprint(normalized_scenes, content_mode)
    plan["global_source_fingerprint"] = global_plan.get("source_fingerprint")
    plan["planning_scope"] = "global_agent1_projection"
    return plan


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


def pause_for_step_confirmation(job: Job, store: JobStore, stage: str, message: str) -> None:
    """Persist an intentional, resumable pause for the guided workflow."""
    if stage == "visual":
        source = WORKSPACE_DIR / "3_visual_template" / "assets"
        target = JOBS_DIR / job.id / "step_mode_preview_images"
        if source.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(source, target)
    request = dict(job.request)
    request["_step_mode_stage"] = stage
    store.update(
        job,
        request=request,
        status="waiting_confirmation",
        step=f"await_{stage}",
        progress=30 if stage == "audio" else 85,
        message=message,
        error=None,
    )
    store.log(job, f"分步模式：{message}")
    raise GenerationPaused(message)


def render_semantic_visual_video(
    job: Job,
    store: JobStore,
    request: dict[str, Any],
    *,
    resume: bool = False,
    story_plan_path: Path | None = None,
    story_plan_is_global: bool = True,
    apply_bgm: bool = True,
) -> None:
    store.raise_if_cancelled(job)
    # Agent 1 must see the original subtitle timeline before Module 3 adds
    # visual summaries.  Its semantic units become the fixed membership that
    # Agent 2 later receives; Python still guards only hard duration limits.
    raw_scenes = load_scene_timeline()
    if story_plan_path is None:
        story_plan_path = WORKSPACE_DIR / "3_visual_template" / "story_plan.json"
        story_plan = load_or_create_story_plan(
            raw_scenes,
            resume=resume,
            path=story_plan_path,
            content_mode=str(request.get("content_mode") or "urban_suspense"),
            require_ai_success=True,
        )
        store.log(job, "Agent 1：已按全文规划语义镜头单元")
    else:
        try:
            story_plan = json.loads(story_plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            story_plan = load_or_create_story_plan(
                raw_scenes,
                resume=resume,
                path=story_plan_path,
                content_mode=str(request.get("content_mode") or "urban_suspense"),
                require_ai_success=True,
            )
    if not isinstance(story_plan, dict) or story_plan.get("generation_source") != "gemini":
        raise RuntimeError(
            "Agent 1 规划产物不是有效的语言模型结果，已在提交图像任务前安全终止；"
            "配音与字幕已保留，请修复语言模型配置后断点续跑。"
        )
    fine_path = WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json"
    if resume and fine_path.is_file() and fine_path.stat().st_size > 0:
        store.log(job, f"断点续跑：复用模块 3 剧本: {fine_path}")
    else:
        fine_path = generate_fine_grained_timeline(story_plan=story_plan)
    store.raise_if_cancelled(job)
    store.log(job, f"模块 3 剧本写入: {fine_path}")
    store.update(job, step="visual", progress=max(job.progress, 55), message=STEPS[4][1])

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
        # Agent 2 is a paid-image safety gate. Never submit image jobs when its
        # language-model planning failed or silently fell back to raw subtitles.
        poster_env["REQUIRE_AI_AGENT_SUCCESS"] = "1"
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
        agent0_prompt_system = str(request.get("agent0_prompt_system") or "").strip()
        if agent0_prompt_system:
            poster_env["AGENT0_PROMPT_SYSTEM"] = agent0_prompt_system
        agent1_prompt_system = str(request.get("agent1_prompt_system") or "").strip()
        if agent1_prompt_system:
            poster_env["AGENT1_PROMPT_SYSTEM"] = agent1_prompt_system
        global_character_prompt = str(request.get("global_character_prompt") or "").strip()
        if global_character_prompt:
            poster_env["GLOBAL_CHARACTER_PROMPT"] = global_character_prompt
        requested_reference_ids = [
            str(value).strip()
            for value in request.get("reference_image_ids", [])
            if str(value).strip()
        ][:3]
        legacy_reference_id = str(request.get("protagonist_reference_image_id") or "").strip()
        if not requested_reference_ids and legacy_reference_id:
            requested_reference_ids = [legacy_reference_id]
        if requested_reference_ids:
            if job.user_id is None:
                raise ValueError("角色参考图需要用户身份")
            reference_images = [
                str(user_reference_image_path(int(job.user_id), image_id))
                for image_id in dict.fromkeys(requested_reference_ids)
            ]
            poster_env["USER_REFERENCE_IMAGE_PATHS_JSON"] = json.dumps(reference_images, ensure_ascii=False)
            poster_env["USER_PROTAGONIST_REFERENCE_IMAGE_PATH"] = reference_images[0]
            store.log(
                job,
                f"已启用 {len(reference_images)} 张角色参考图：Agent 2 将按图 1 至图 {len(reference_images)} 标记实际出场镜头。",
            )
        story_environment_prompt = str(request.get("story_environment_prompt") or "").strip()
        if story_environment_prompt:
            poster_env["GLOBAL_ENVIRONMENT_PROMPT"] = story_environment_prompt
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
    if bool(request.get("step_mode")) and str(request.get("_step_mode_stage") or "") != "visual":
        pause_for_step_confirmation(
            job,
            store,
            "visual",
            "画面已生成，请检查图片；确认后将开始渲染成片",
        )
    store.update(job, step="render", progress=max(job.progress, 86), message=STEPS[5][1])
    render_variant = str(request.get("video_render_variant") or "both").strip().lower()
    if render_variant not in {"subtitles", "raw", "both"}:
        render_variant = "both"
    variant_label = {"subtitles": "仅字幕版", "raw": "仅无字幕版", "both": "双版本"}[render_variant]
    store.log(job, f"模块 5 成片版本：{variant_label}")
    render_env = {"VIDEO_RENDER_VARIANT": render_variant}
    if apply_bgm:
        render_env.update(bgm_render_env(job, request))
    run_command(
        job,
        store,
        [sys.executable, "module5_video_render.py"],
        STEPS[5][1],
        extra_env=render_env,
    )


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
    return _available_named_output_path(OUTPUT_DIR, project_name)


def _available_named_output_path(root: Path, project_name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base = root / normalize_project_name(project_name)
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base.name}_{suffix}"
        suffix += 1
    return candidate


def organize_tts_output(job: Job, request: dict[str, Any]) -> Path:
    """Publish a module-1-only task outside disposable workspace folders."""
    final_dir = _available_named_output_path(
        TTS_OUTPUT_DIR,
        str(request.get("project_name") or f"TTS_{job.id}"),
    )
    temp_dir = TTS_OUTPUT_DIR / f".{final_dir.name}.{job.id}.building"
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        artifact_dir = JOBS_DIR / job.id / "artifacts"
        archived_audio = artifact_dir / "final_output.wav"
        archived_subtitle = artifact_dir / "final_output.srt"
        audio_source = archived_audio if archived_audio.is_file() else WORKSPACE_DIR / "2_audio_srt" / "final_output.wav"
        subtitle_source = archived_subtitle if archived_subtitle.is_file() else WORKSPACE_DIR / "2_audio_srt" / "final_output.srt"
        if not audio_source.is_file() or audio_source.stat().st_size <= 0:
            raise FileNotFoundError("模块 1 完成后没有找到有效配音文件，已保留 workspace 以便排查")
        shutil.copy2(audio_source, temp_dir / "配音.wav")
        if subtitle_source.is_file() and subtitle_source.stat().st_size > 0:
            shutil.copy2(subtitle_source, temp_dir / "配音字幕.srt")
        script_source = JOBS_DIR / job.id / "script.txt"
        if script_source.is_file() and script_source.stat().st_size > 0:
            shutil.copy2(script_source, temp_dir / "文案.txt")
        elif str(request.get("script") or "").strip():
            (temp_dir / "文案.txt").write_text(str(request["script"]).strip(), encoding="utf-8")
        (temp_dir / "任务信息.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": job.id,
                    "project_name": final_dir.name,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "tts_engine": request.get("tts_engine") or "indextts2",
                    "tts_voice_id": request.get("tts_voice_id"),
                    "tts_speed": request.get("tts_speed"),
                    "tts_volume": request.get("tts_volume"),
                    "tts_pitch": request.get("tts_pitch"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    for path in final_dir.iterdir():
        if path.is_file():
            register_job_asset(job, path, "tts_output", {"project_name": final_dir.name})
    return final_dir


def _copy_visual_segment(
    *,
    mapping_path: Path,
    timeline_path: Path,
    source_image_dir: Path,
    output_image_dir: Path,
    file_prefix: str,
    time_offset: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, list[dict[str, Any]]]:
    mapping = _json_list(mapping_path)
    scenes = _json_list(timeline_path)
    if not scenes:
        return [], [], 0.0, []
    scenes_by_id = {str(item.get("slide_id") or ""): item for item in scenes}
    slide_id_map = {
        slide_id: f"{file_prefix}_{slide_id}" if file_prefix else slide_id
        for slide_id in scenes_by_id
    }
    adjusted_scenes = [
        {
            **item,
            "id": f"{file_prefix}_{item.get('id')}" if file_prefix and item.get("id") else item.get("id"),
            "slide_id": slide_id_map.get(str(item.get("slide_id") or ""), str(item.get("slide_id") or "")),
            "start": round(float(item.get("start") or 0) + time_offset, 3),
            "end": round(float(item.get("end") or 0) + time_offset, 3),
        }
        for item in scenes
    ]
    poster_timeline: list[dict[str, Any]] = []
    archived_mapping: list[dict[str, Any]] = []
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
        output_macro_id = re.sub(r"_[0-9a-f]{8,}$", "", output_image.stem, flags=re.IGNORECASE)
        archived_mapping.append({
            **item,
            "macro_scene_id": output_macro_id,
            "image_prompt": str(item.get("image_prompt") or "").strip(),
            "includes_slides": [
                slide_id_map.get(str(value), str(value))
                for value in item.get("includes_slides", [])
            ],
        })
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
    return adjusted_scenes, poster_timeline, duration, archived_mapping


def validate_and_write_output_manifest(
    output_root: Path,
    job: Job,
    request: dict[str, Any],
    *,
    project_name: str | None = None,
) -> Path:
    """Refuse to publish an incomplete project, then persist its portable file inventory."""
    render_variant = str(request.get("video_render_variant") or "both").strip().lower()
    if render_variant not in {"subtitles", "raw", "both"}:
        render_variant = "both"
    required = [
        output_root / "input" / "配音.wav",
        output_root / "other" / "最终字幕.srt",
        output_root / "other" / "最终画面.html",
        output_root / "other" / "画面映射.json",
        output_root / "other" / "画面时间线.json",
    ]
    if render_variant in {"subtitles", "both"}:
        required.append(output_root / "video" / "最终视频_字幕版.mp4")
    if render_variant in {"raw", "both"}:
        required.append(output_root / "video" / "最终视频_纯净版.mp4")
    missing = [path.relative_to(output_root).as_posix() for path in required if not path.is_file() or path.stat().st_size <= 0]
    images = [
        path for path in (output_root / "image").glob("*")
        if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file() and path.stat().st_size > 0
    ]
    if not images:
        missing.append("image/<至少一张有效画面>")
    if missing:
        raise RuntimeError("项目归档不完整，已保留 workspace 以便恢复：" + "、".join(missing))

    files = []
    for path in sorted((value for value in output_root.rglob("*") if value.is_file()), key=lambda value: value.as_posix()):
        files.append({
            "path": path.relative_to(output_root).as_posix(),
            "size": path.stat().st_size,
        })
    manifest = output_root / "other" / "归档清单.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "job_id": job.id,
                "project_name": project_name or output_root.name,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "render_variant": render_variant,
                "editable_from_output": True,
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


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
        segment_archive = JOBS_DIR / job.id / "artifacts" / "tts_segments"
        if segment_archive.is_dir() and (segment_archive / "manifest.json").is_file():
            shutil.copytree(segment_archive, other_dir / "tts_segments", dirs_exist_ok=True)
        if str(request.get("tts_engine") or "indextts2") == "indextts2" and job.user_id is not None:
            try:
                from .indextts2_local import load_indextts2_config, resolve_voice_reference
                voice_source = resolve_voice_reference(
                    load_indextts2_config(),
                    str(request.get("tts_voice_id") or "voice_05.wav"),
                    user_id=int(job.user_id),
                )
                shutil.copy2(voice_source, input_dir / f"TTS参考音色{voice_source.suffix.lower()}")
            except (OSError, ValueError):
                # Built-in voices remain resolvable from the portable runtime;
                # a missing custom reference should not invalidate the archive.
                pass
        if request.get("skip_tts") and job.user_id is not None and request.get("source_audio_id"):
            source_audio = user_upload_path(int(job.user_id), str(request["source_audio_id"]))
            shutil.copy2(source_audio, input_dir / f"原始配音{source_audio.suffix.lower()}")

        bgm_manifest: dict[str, Any] | None = None
        bgm_tracks = bgm_tracks_for_job(job, request)
        if bgm_tracks:
            bgm_dir = input_dir / "BGM"
            bgm_dir.mkdir(parents=True, exist_ok=True)
            archived_tracks: list[dict[str, Any]] = []
            for index, item in enumerate(bgm_tracks, 1):
                source = Path(str(item["path"]))
                target = bgm_dir / f"{index:03d}{source.suffix.lower()}"
                shutil.copy2(source, target)
                archived_tracks.append({
                    "filename": target.name,
                    "volume_db": float(item["volume_db"]),
                })
            bgm_manifest = {
                "enabled": True,
                "tracks": archived_tracks,
                "fade_enabled": bool(request.get("bgm_fade_enabled")),
                "fade_duration": float(request.get("bgm_fade_duration") or 1),
            }
            (other_dir / "BGM设置.json").write_text(
                json.dumps(bgm_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        combined_scenes: list[dict[str, Any]] = []
        combined_posters: list[dict[str, Any]] = []
        combined_mapping: list[dict[str, Any]] = []
        parts_dir = JOBS_DIR / job.id / "artifacts" / "parts"
        part_mappings = sorted(parts_dir.glob("part_*_poster_mapping.json")) if parts_dir.is_dir() else []
        if part_mappings:
            time_offset = 0.0
            for mapping_path in part_mappings:
                part_name = mapping_path.name.removesuffix("_poster_mapping.json")
                scenes, posters, duration, archived_mapping = _copy_visual_segment(
                    mapping_path=mapping_path,
                    timeline_path=parts_dir / f"{part_name}_fine_grained_timeline.json",
                    source_image_dir=parts_dir / f"{part_name}_images",
                    output_image_dir=image_dir,
                    file_prefix=part_name,
                    time_offset=time_offset,
                )
                combined_scenes.extend(scenes)
                combined_posters.extend(posters)
                combined_mapping.extend(archived_mapping)
                time_offset += duration
        else:
            scenes, posters, _, archived_mapping = _copy_visual_segment(
                mapping_path=WORKSPACE_DIR / "3_visual_template" / "poster_mapping.json",
                timeline_path=WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json",
                source_image_dir=WORKSPACE_DIR / "3_visual_template" / "assets",
                output_image_dir=image_dir,
                file_prefix="",
                time_offset=0.0,
            )
            combined_scenes.extend(scenes)
            combined_posters.extend(posters)
            combined_mapping.extend(archived_mapping)

        # Keep the original task reference images inside output so a completed
        # project remains fully editable after workspace/uploads is cleaned.
        reference_manifest: list[dict[str, Any]] = []
        requested_reference_ids = [
            str(value).strip()
            for value in request.get("reference_image_ids", [])
            if str(value).strip()
        ][:3]
        if job.user_id is not None:
            reference_dir = other_dir / "reference_images"
            for index, image_id in enumerate(dict.fromkeys(requested_reference_ids), start=1):
                source = user_reference_image_path(int(job.user_id), image_id)
                if not source.is_file():
                    continue
                reference_dir.mkdir(parents=True, exist_ok=True)
                target = reference_dir / f"main_{index:02d}{source.suffix.lower()}"
                shutil.copy2(source, target)
                reference_manifest.append({
                    "reference_id": f"图{index}",
                    "upload_id": image_id,
                    "filename": target.name,
                })
        if reference_manifest:
            (other_dir / "参考图清单.json").write_text(
                json.dumps(reference_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

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
        mapping_by_macro = {
            str(item.get("macro_scene_id") or ""): item
            for item in combined_mapping
            if str(item.get("macro_scene_id") or "")
        }
        output_mapping: list[dict[str, Any]] = []
        for image in sorted(image_dir.glob("*")):
            if image.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            macro_id = re.sub(r"_[0-9a-f]{8,}$", "", image.stem, flags=re.IGNORECASE)
            prompt_file = image.with_suffix(".txt")
            source_item = mapping_by_macro.get(macro_id, {})
            output_mapping.append({
                **source_item,
                "macro_scene_id": macro_id,
                "image_prompt": prompt_file.read_text(encoding="utf-8") if prompt_file.is_file() else "",
                "includes_slides": list(source_item.get("includes_slides") or []),
                "reference_image_ids": list(source_item.get("reference_image_ids") or []),
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

        support_copies = {
            "scene_timeline.json": "模块2.5_校对后字幕场景.json",
            "story_context.json": "Agent0_全文资料.json",
            "story_plan.json": "Agent1_分镜规划.json",
            "visual_prompt_plan.json": "Agent2_画面提示词规划.json",
        }
        visual_workspace = WORKSPACE_DIR / "3_visual_template"
        for source_name, target_name in support_copies.items():
            source = visual_workspace / source_name
            if source.is_file() and source.stat().st_size > 0:
                shutil.copy2(source, other_dir / target_name)
        (other_dir / "任务参数.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        validate_and_write_output_manifest(temp_dir, job, request, project_name=final_dir.name)

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


def organize_subtitle_output(job: Job) -> Path:
    """Archive a standalone subtitle job in a stable, user-facing output directory."""
    source = WORKSPACE_DIR / "2_audio_srt" / "final_short.srt"
    if not source.is_file():
        raise FileNotFoundError("字幕识别完成后未找到最终 SRT 文件")
    output_dir = OUTPUT_DIR / job.id
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "最终字幕.srt"
    shutil.copy2(source, target)
    register_job_asset(job, target, "project_output", {"subtitle_only": True})
    return output_dir


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
    content_mode = str(request.get("content_mode") or "urban_suspense")
    full_text = canonical_story_text(request, scenes)
    global_character_prompt = str(request.get("global_character_prompt") or "").strip()
    world_prompt = str(request.get("story_environment_prompt") or "").strip()
    total_chars = scene_text_length(scenes)
    hierarchical_min_chars = max(
        threshold * 2,
        int(os.getenv("AGENT_HIERARCHICAL_MIN_CHARS", "6000")),
    )
    hierarchical_planning = total_chars > hierarchical_min_chars
    if bool(request.get("step_mode")) and auto_split and total_chars > threshold:
        # Segment rendering produces and encodes one part at a time.  Keep the
        # existing reliable long-text path intact; the audio checkpoint still works.
        store.log(job, "分步模式：超长文将保留配音确认；画面确认将在分段渲染流程稳定后开放。")
        request = {**request, "step_mode": False}
    if not auto_split or total_chars <= threshold:
        context_path = WORKSPACE_DIR / "3_visual_template" / "story_context.json"
        store.log(job, "Agent 0：开始通读全文资料")
        story_context = load_or_create_story_context(
            full_text,
            resume=resume,
            path=context_path,
            content_mode=content_mode,
            global_character_prompt=global_character_prompt,
            world_prompt=world_prompt,
            agent0_prompt_system=str(request.get("agent0_prompt_system") or "").strip(),
            require_ai_success=True,
        )
        story_plan_path = WORKSPACE_DIR / "3_visual_template" / "story_plan.json"
        store.log(job, "Agent 1：开始按字幕时间轴规划画面边界")
        load_or_create_story_plan(
            scenes,
            resume=resume,
            path=story_plan_path,
            content_mode=content_mode,
            story_context=story_context,
            require_ai_success=True,
        )
        store.log(job, f"Agent 自适应规划：短文模式（{total_chars} 字），仅执行一次全文规划")
        store.update(job, step="semantic", progress=48, message=STEPS[3][1])
        render_semantic_visual_video(job, store, request, resume=resume, story_plan_path=story_plan_path)
        return

    source_dir = WORKSPACE_DIR / "temp_chunks" / "long_split_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    global_story_context = source_dir / "story_context.full.json"
    global_story_plan = source_dir / "story_plan.full.json"
    store.log(job, "Agent 0：开始通读长文全文并建立全局资料")
    story_context = load_or_create_story_context(
        full_text,
        resume=resume,
        path=global_story_context,
        content_mode=content_mode,
        global_character_prompt=global_character_prompt,
        world_prompt=world_prompt,
        agent0_prompt_system=str(request.get("agent0_prompt_system") or "").strip(),
        require_ai_success=True,
    )
    store.log(job, f"Agent 1：开始通读长文全文（{len(scenes)} 个片段）")
    story_plan = load_or_create_story_plan(
        scenes,
        resume=resume,
        path=global_story_plan,
        content_mode=content_mode,
        story_context=story_context,
        require_ai_success=True,
    )
    store.log(job, f"Agent 1：全文故事上下文已保存: {global_story_plan}")
    if hierarchical_planning:
        store.log(job, f"Agent 自适应规划：超长文模式（>{hierarchical_min_chars} 字），启用全文总纲 + 分段细化")
    else:
        store.log(job, f"Agent 自适应规划：普通长文模式（≤{hierarchical_min_chars} 字），各段共用全文总纲")

    groups = split_scenes_by_agent1_boundaries(scenes, threshold, story_plan)
    if groups:
        store.log(job, f"Agent 1：按全文语义边界均衡拆为 {len(groups)} 个渲染段")
    else:
        groups = split_scenes_by_topic_with_llm(scenes, threshold, story_plan)
    if groups:
        store.log(job, f"长文渲染将严格保留全文 Agent 1 的语义边界")
    else:
        groups = split_scenes_by_text_length(scenes, threshold)
        store.log(job, "长文主题分段不可用，已回退到字幕边界字数分段")
    if len(groups) <= 1:
        store.update(job, step="semantic", progress=48, message=STEPS[3][1])
        render_semantic_visual_video(
            job,
            store,
            request,
            resume=resume,
            story_plan_path=global_story_plan,
        )
        return

    group_lengths = [scene_text_length(group) for group in groups]
    store.log(
        job,
        f"长文自动分段: {total_chars} 字，语义优先且单段不超过 {threshold} 字，"
        f"拆为 {len(groups)} 段（{' / '.join(str(length) for length in group_lengths)} 字）",
    )
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
        segment_plan_path = source_dir / f"story_plan.part_{index:03d}.json"
        segment_plan = project_global_agent1_plan_to_segment(
            story_plan,
            scenes,
            group,
            normalized,
            str(request.get("content_mode") or "urban_suspense"),
        )
        segment_plan_path.write_text(json.dumps(segment_plan, ensure_ascii=False, indent=2), encoding="utf-8")
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
            apply_bgm=False,
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
    if global_story_context.is_file():
        shutil.copy2(global_story_context, WORKSPACE_DIR / "3_visual_template" / "story_context.json")
    if global_story_plan.is_file():
        shutil.copy2(global_story_plan, WORKSPACE_DIR / "3_visual_template" / "story_plan.json")
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    store.update(job, step="render", progress=88, message="拼接分段视频")
    render_variant = str(request.get("video_render_variant") or "both").strip().lower()
    if render_variant not in {"subtitles", "raw", "both"}:
        render_variant = "both"
    if render_variant in {"subtitles", "both"}:
        concat_videos(
            job,
            store,
            part_with_subtitles,
            FINAL_DIR / "final_with_subtitles.mp4",
            "拼接字幕版分段视频",
        )
    if render_variant in {"raw", "both"}:
        concat_videos(
            job,
            store,
            part_raw,
            FINAL_DIR / "final_raw_presentation.mp4",
            "拼接纯净版分段视频",
        )
    mix_final_videos_with_bgm(job, store, request)
    store.log(job, "分段视频已按顺序拼接完成")


def finalize_completed_pipeline(job: Job, store: JobStore, request: dict[str, Any]) -> None:
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
    manifest = output_dir / "other" / "归档清单.json"
    if manifest.is_file():
        try:
            reset_generation_workspace()
            store.log(job, "归档完整性校验通过，已清理本次共享 workspace 临时产物；后续编辑将只读取 output。")
        except OSError as exc:
            store.log(job, f"项目已完整归档，但自动清理 workspace 失败，可稍后手动清理：{exc}")
    store.log(job, "全部完成")


def render_from_visual_checkpoint(job: Job, store: JobStore, request: dict[str, Any]) -> None:
    """Finish module 5 without calling Agent or Image2 again after visual approval."""
    store.raise_if_cancelled(job)
    store.update(job, status="running", step="render", progress=86, message=STEPS[5][1])
    render_variant = str(request.get("video_render_variant") or "both").strip().lower()
    if render_variant not in {"subtitles", "raw", "both"}:
        render_variant = "both"
    store.log(job, "分步模式：已确认画面，开始仅运行模块 5 渲染成片")
    render_env = {"VIDEO_RENDER_VARIANT": render_variant}
    render_env.update(bgm_render_env(job, request))
    run_command(
        job,
        store,
        [sys.executable, "module5_video_render.py"],
        STEPS[5][1],
        extra_env=render_env,
    )
    finalize_completed_pipeline(job, store, request)


def run_pipeline(job: Job, store: JobStore, *, resume: bool = False) -> None:
    store.raise_if_cancelled(job)
    request = job.request
    job_dir = JOBS_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    if resume and bool(request.get("step_mode")) and str(request.get("_step_mode_stage") or "") == "visual":
        render_from_visual_checkpoint(job, store, request)
        return
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
        tts_engine = str(request.get("tts_engine") or "indextts2").strip().lower()
        if tts_engine not in {"indextts2", "cluster", "qwen"}:
            tts_engine = "indextts2"
        if tts_engine == "cluster":
            store.log(job, "模块 1：使用集群 GPU 加速配音，通过 cloud-api 提交、轮询并下载分块 WAV")
        elif tts_engine == "qwen":
            store.log(job, "模块 1：使用 Qwen-TTS 云端配音，逐句下载并合并本地 WAV")
        else:
            store.log(job, "模块 1：使用官方 IndexTTS2 本地 GPU 配音")
        if tts_engine == "cluster":
            if job.user_id is None:
                raise RuntimeError("集群 GPU 模式需要本地用户身份")
            from .cloud_client import cloud_client_for
            from .cloud_tts import CloudTtsCancelled, synthesize_cloud_tts

            def update_cloud_progress(percent: int, message: str) -> None:
                mapped = min(29, 8 + round(max(0, min(100, percent)) / 100 * 21))
                store.update(job, progress=max(job.progress, mapped), message=f"集群 GPU：{message}"[:500])

            def remember_cloud_job(cloud_job_id: str, payload: dict[str, Any]) -> None:
                request["_cloud_job_id"] = cloud_job_id
                request["_cloud_job_status"] = str(payload.get("status") or "")
                for field in ("reserved_credits", "consumed_credits", "released_credits"):
                    if payload.get(field) is not None:
                        request[f"_cloud_{field}"] = payload.get(field)
                store.update(job, request=request)

            try:
                synthesize_cloud_tts(
                    client=cloud_client_for(int(job.user_id)),
                    local_job_id=job.id,
                    request=request,
                    output_dir=WORKSPACE_DIR / "2_audio_srt",
                    segment_archive_dir=JOBS_DIR / job.id / "artifacts" / "tts_segments",
                    temp_dir=WORKSPACE_DIR / "temp_chunks" / job.id,
                    is_cancelled=lambda: store.is_cancelled(job),
                    on_progress=update_cloud_progress,
                    on_log=lambda line: store.log(job, line),
                    on_remote_job=remember_cloud_job,
                )
            except CloudTtsCancelled as exc:
                raise GenerationCancelled(str(exc)) from exc
        else:
            tts_command = [
                sys.executable,
                "module1_agent_director.py",
                "--text",
                str(script_path),
                "--job-id",
                job.id,
                "--tts-engine",
                tts_engine,
                "--segment-archive-dir",
                str(JOBS_DIR / job.id / "artifacts" / "tts_segments"),
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
            qwen_instructions = str(request.get("qwen_tts_instructions") or "").strip()
            if tts_engine == "qwen" and qwen_instructions:
                tts_command.extend(["--qwen-instructions", qwen_instructions])
            if tts_engine == "qwen":
                tts_command.extend([
                    "--qwen-voice",
                    str(request.get("qwen_tts_voice") or "Elias"),
                    "--qwen-optimize-instructions",
                    "true" if request.get("qwen_tts_optimize_instructions", False) else "false",
                ])
            run_command(
                job,
                store,
                tts_command,
                STEPS[0][1],
            )

    store.raise_if_cancelled(job)
    if bool(request.get("step_mode")) and str(request.get("_step_mode_stage") or "") not in {"audio", "visual"}:
        store.update(job, artifacts=copy_artifacts(job))
        pause_for_step_confirmation(
            job,
            store,
            "audio",
            "配音已生成，请试听确认；确认后将开始字幕校对、Agent 规划与画面生成",
        )
    if request.get("module1_only"):
        artifacts = copy_artifacts(job)
        tts_output_dir = organize_tts_output(job, request)
        store.update(
            job,
            status="completed",
            step="completed",
            progress=100,
            message="模块 1 配音生成完成",
            artifacts=artifacts,
        )
        store.log(job, f"模块 1 独立任务完成：产物已整理到 {tts_output_dir}")
        try:
            reset_generation_workspace()
            store.log(job, "TTS 独立产物已安全归档，已清理共享 workspace 临时文件")
        except OSError as exc:
            store.log(job, f"TTS 已归档，但自动清理 workspace 失败：{exc}")
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

    if request.get("subtitle_only"):
        store.raise_if_cancelled(job)
        use_correction = bool(request.get("subtitle_use_correction", True))
        if use_correction:
            if script.strip():
                store.update(job, step="correct", progress=72, message="模块 2.5：参考文案校对")
                run_command(job, store, [sys.executable, "module2_5_text_corrector.py"], "模块 2.5：参考文案校对")
            else:
                store.update(job, step="correct", progress=72, message="模块 2.5：语言模型校对")
                correct_asr_subtitles_with_language_model(job, store)
        else:
            store.log(job, "已跳过模块 2.5：保留 ASR 原始字幕")
        artifacts = copy_artifacts(job)
        subtitle_output_dir = organize_subtitle_output(job)
        subtitle_artifacts = {key: value for key, value in artifacts.items() if key == "subtitle"}
        store.update(
            job,
            status="completed",
            step="completed",
            progress=100,
            message="字幕识别完成，SRT 已生成",
            artifacts=subtitle_artifacts,
        )
        store.log(job, f"模块 2 独立任务完成：已输出 SRT 字幕文件到 {subtitle_output_dir}")
        return

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

    finalize_completed_pipeline(job, store, request)
