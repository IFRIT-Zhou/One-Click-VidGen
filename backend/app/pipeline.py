import mimetypes
import os
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
JOBS_DIR = WORKSPACE_DIR / "jobs"
FINAL_DIR = WORKSPACE_DIR / "4_final_video"


STEPS = [
    ("tts", "断句、配音、原始字幕"),
    ("scene", "ASR 短字幕与页面断句"),
    ("correct", "原文对齐与字幕校准"),
    ("semantic", "模块 3 语义分镜"),
    ("visual", "模块 4 在线海报与页面生成"),
    ("render", "Hyperframes 图像、音频、字幕合成视频"),
    ("archive", "项目资产归档"),
]
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


class GenerationCancelled(RuntimeError):
    """The user requested that this generation job stop."""


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
        stamp = time.strftime("%H:%M:%S")
        persisted_line = f"[{stamp}] {line.rstrip()}"
        job.logs.append(persisted_line)
        job.updated_at = time.time()
        append_generation_job_log(job.id, persisted_line, job.updated_at)

    def run_async(self, job: Job) -> None:
        thread = threading.Thread(target=self._run_guarded, args=(job,), daemon=True)
        thread.start()

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

    def _run_guarded(self, job: Job) -> None:
        with self._pipeline_lock:
            try:
                self.raise_if_cancelled(job)
                run_pipeline(job, self)
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
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return result.returncode == 0, result.stderr.strip()


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
        "subtitle": WORKSPACE_DIR / "2_audio_srt" / "final_short.srt",
        "scene_timeline": WORKSPACE_DIR / "3_visual_template" / "scene_timeline.json",
        "fine_grained_timeline": WORKSPACE_DIR / "3_visual_template" / "fine_grained_timeline.json",
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


def run_pipeline(job: Job, store: JobStore) -> None:
    store.raise_if_cancelled(job)
    request = job.request
    job_dir = JOBS_DIR / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    reset_generation_workspace()
    store.log(job, "已清理本轮生成的共享 workspace 旧产物")
    script_path = job_dir / "script.txt"
    script_path.write_text(request["script"], encoding="utf-8")
    register_job_asset(job, script_path, "source_script")
    (WORKSPACE_DIR / "1_original_text.txt").write_text(request["script"], encoding="utf-8")

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
            str(request.get("tts_voice_id") or "Wise_Woman"),
            "--tts-speed",
            str(request.get("tts_speed", 1)),
            "--tts-volume",
            str(request.get("tts_volume", 1)),
            "--tts-pitch",
            str(request.get("tts_pitch", 0)),
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
    run_command(job, store, [sys.executable, "module2_5_text_corrector.py"], STEPS[2][1])

    store.raise_if_cancelled(job)
    store.update(job, step="semantic", progress=54, message=STEPS[3][1])
    fine_path = generate_fine_grained_timeline()
    store.raise_if_cancelled(job)
    store.log(job, f"模块 3 剧本写入: {fine_path}")

    store.update(job, step="visual", progress=68, message=STEPS[4][1])
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
        run_command(
            job,
            store,
            [sys.executable, "module4_online_poster.py"],
            STEPS[4][1],
            extra_env={"VOICE_OVER_VIDEO_JOB_ID": job.id},
        )
    else:
        raise ValueError(f"不支持的视觉后端: {visual_backend}")

    store.raise_if_cancelled(job)
    store.update(job, step="render", progress=86, message=STEPS[5][1])
    run_command(job, store, [sys.executable, "module5_video_render.py"], STEPS[5][1])

    store.raise_if_cancelled(job)
    store.update(job, step="archive", progress=96, message=STEPS[6][1])
    archive_dir = archive_project_assets(job)
    store.raise_if_cancelled(job)
    store.log(job, f"项目资产已归档: {archive_dir}")

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
