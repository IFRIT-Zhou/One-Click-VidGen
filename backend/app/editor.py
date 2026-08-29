import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .db import (
    append_editor_job_log,
    list_media_assets,
    load_editor_jobs,
    record_media_asset,
    upsert_editor_job,
)
from .pipeline import PROJECT_ROOT, WORKSPACE_DIR


EDITOR_DIR = WORKSPACE_DIR / "editor"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".vtt"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_EDITOR_UPLOAD_MAX_BYTES = 512 * 1024 * 1024


def editor_upload_limit(kind: str) -> int:
    """Return a server-side upload limit, overridable for large local media."""
    configured = str(os.getenv("EDITOR_UPLOAD_MAX_BYTES") or "").strip()
    try:
        maximum = int(configured) if configured else DEFAULT_EDITOR_UPLOAD_MAX_BYTES
    except ValueError:
        maximum = DEFAULT_EDITOR_UPLOAD_MAX_BYTES
    maximum = max(1 * 1024 * 1024, maximum)
    if kind == "image":
        return min(30 * 1024 * 1024, maximum)
    if kind in {"subtitle", "file"}:
        return min(10 * 1024 * 1024, maximum)
    return maximum


def ffmpeg_binary() -> str:
    local = PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists() and os.name == "nt":
        return str(local)
    return shutil.which("ffmpeg") or "ffmpeg"


def sanitize_filename(name: str) -> str:
    stem = Path(name).stem.strip() or "asset"
    suffix = Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80].strip("._") or "asset"
    return f"{uuid.uuid4().hex[:10]}_{stem}{suffix}"


def media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return "file"


def user_upload_dir(user_id: int) -> Path:
    path = EDITOR_DIR / f"user_{user_id}" / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_render_dir(user_id: int) -> Path:
    path = EDITOR_DIR / f"user_{user_id}" / "renders"
    path.mkdir(parents=True, exist_ok=True)
    return path


def upload_path(user_id: int, filename: str) -> Path:
    root = user_upload_dir(user_id).resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.exists():
        raise FileNotFoundError(filename)
    return path


def asset_snapshot(user_id: int, path: Path, display_name: str | None = None) -> dict[str, str]:
    filename = path.name
    return {
        "id": filename,
        "name": display_name or filename.split("_", 1)[-1],
        "kind": media_kind(path),
        "url": f"/api/editor/uploads/{filename}",
    }


def register_editor_asset(
    user_id: int,
    path: Path,
    role: str,
    editor_job_id: str | None = None,
    original_name: str | None = None,
) -> None:
    if not path.is_file():
        return
    record_media_asset(
        user_id=user_id,
        editor_job_id=editor_job_id,
        kind=media_kind(path),
        role=role,
        storage_backend="local",
        storage_path=str(path.resolve().relative_to(PROJECT_ROOT)),
        original_name=original_name or path.name,
        mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size_bytes=path.stat().st_size,
    )


async def save_upload(user_id: int, file: UploadFile) -> dict[str, str]:
    original = file.filename or "upload.bin"
    suffix = Path(original).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | SUBTITLE_EXTENSIONS | IMAGE_EXTENSIONS:
        raise ValueError("只支持图片、视频、音频和字幕文件")
    filename = sanitize_filename(original)
    target = user_upload_dir(user_id) / filename
    kind = media_kind(target)
    maximum = editor_upload_limit(kind)
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > maximum:
                    raise ValueError(f"{kind}文件不能超过 {maximum / (1024 * 1024):g} MB")
                out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    register_editor_asset(user_id, target, "editor_upload", original_name=original)
    return asset_snapshot(user_id, target, display_name=original)


def list_uploads(user_id: int) -> list[dict[str, str]]:
    root = user_upload_dir(user_id)
    rows = list_media_assets(user_id=user_id, role="editor_upload")
    known_paths = {str(row.get("storage_path") or "") for row in rows}

    # Backfill files created before database-backed asset management was added.
    for path in root.iterdir():
        storage_path = str(path.resolve().relative_to(PROJECT_ROOT))
        if path.is_file() and storage_path not in known_paths:
            register_editor_asset(user_id, path, "editor_upload")

    assets: list[dict[str, str]] = []
    for row in list_media_assets(user_id=user_id, role="editor_upload"):
        raw_path = row.get("storage_path")
        if not raw_path:
            continue
        path = (PROJECT_ROOT / str(raw_path)).resolve()
        if path.is_file() and path.parent == root.resolve():
            assets.append(asset_snapshot(user_id, path, display_name=str(row.get("original_name") or "")))
    return assets


def escape_filter_path(path: Path) -> str:
    text = path.as_posix()
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


@dataclass
class EditJob:
    id: str
    user_id: int
    status: str = "queued"
    progress: int = 0
    message: str = "等待剪辑"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    logs: list[str] = field(default_factory=list)
    request: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs": self.logs[-200:],
            "request": self.request,
            "artifacts": self.artifacts,
            "error": self.error,
        }


class EditStore:
    def __init__(self) -> None:
        self._jobs: dict[str, EditJob] = {}
        self._lock = threading.Lock()

    def create(self, user_id: int, request: dict[str, Any]) -> EditJob:
        job = EditJob(id=uuid.uuid4().hex[:12], user_id=user_id, request=request)
        with self._lock:
            self._jobs[job.id] = job
        upsert_editor_job(job.snapshot())
        return job

    def load_persisted(self) -> None:
        loaded: dict[str, EditJob] = {}
        interrupted: list[EditJob] = []
        for row in load_editor_jobs():
            job = EditJob(
                id=str(row["id"]),
                user_id=int(row["user_id"]),
                status=str(row.get("status") or "queued"),
                progress=int(row.get("progress") or 0),
                message=str(row.get("message") or ""),
                created_at=float(row.get("created_at") or time.time()),
                updated_at=float(row.get("updated_at") or time.time()),
                logs=list(row.get("logs") or []),
                request=dict(row.get("request") or {}),
                artifacts=dict(row.get("artifacts") or {}),
                error=row.get("error"),
            )
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.progress = 100
                job.message = "服务重启，剪辑任务已中断"
                job.error = "backend restarted before editor task completion"
                job.updated_at = time.time()
                interrupted.append(job)
            loaded[job.id] = job
        with self._lock:
            self._jobs = loaded
        for job in interrupted:
            self.log(job, "服务重启，未完成剪辑任务已标记为失败")
            upsert_editor_job(job.snapshot())

    def get(self, job_id: str) -> EditJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, user_id: int) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [job for job in self._jobs.values() if job.user_id == user_id]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return [job.snapshot() for job in jobs[:25]]

    def active_job_summary(self) -> list[dict[str, Any]]:
        """Return lifecycle-only data used to prevent destructive service restarts."""
        with self._lock:
            return [
                {
                    "id": job.id,
                    "status": job.status,
                    "step": "editor",
                    "progress": job.progress,
                }
                for job in self._jobs.values()
                if job.status in {"queued", "running"}
            ]

    def update(self, job: EditJob, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
        upsert_editor_job(job.snapshot())

    def log(self, job: EditJob, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        persisted_line = f"[{stamp}] {line.rstrip()}"
        job.logs.append(persisted_line)
        job.updated_at = time.time()
        append_editor_job_log(job.id, persisted_line, job.updated_at)

    def run_async(self, job: EditJob) -> None:
        thread = threading.Thread(target=self._run_guarded, args=(job,), daemon=True)
        thread.start()

    def _run_guarded(self, job: EditJob) -> None:
        try:
            run_edit_job(job, self)
        except Exception as exc:
            self.log(job, f"失败: {exc}")
            self.update(job, status="failed", progress=100, message="剪辑失败", error=str(exc))


edit_store = EditStore()


def run_process(job: EditJob, store: EditStore, command: list[str]) -> None:
    store.log(job, " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        store.log(job, line)
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg 退出码 {code}")


def run_edit_job(job: EditJob, store: EditStore) -> None:
    request = job.request
    user_id = job.user_id
    video = upload_path(user_id, request["video_id"])
    if media_kind(video) != "video":
        raise ValueError("主素材必须是视频文件")

    audio = upload_path(user_id, request["audio_id"]) if request.get("audio_id") else None
    subtitle = upload_path(user_id, request["subtitle_id"]) if request.get("subtitle_id") else None
    if audio and media_kind(audio) != "audio":
        raise ValueError("配乐素材必须是音频文件")
    if subtitle and media_kind(subtitle) != "subtitle":
        raise ValueError("字幕素材必须是 SRT/ASS/VTT 文件")

    trim_start = max(float(request.get("trim_start") or 0), 0)
    trim_end = max(float(request.get("trim_end") or 0), 0)
    if trim_end and trim_end <= trim_start:
        raise ValueError("结束时间必须大于开始时间")

    video_volume = max(float(request.get("video_volume") or 1), 0)
    audio_volume = max(float(request.get("audio_volume") or 1), 0)
    audio_offset = max(float(request.get("audio_offset") or 0), 0)
    burn_subtitles = bool(request.get("burn_subtitles")) and subtitle is not None

    out_dir = user_render_dir(user_id) / job.id
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "edited_video.mp4"

    command = [ffmpeg_binary(), "-y"]
    if trim_start:
        command += ["-ss", f"{trim_start:.3f}"]
    command += ["-i", str(video)]
    if audio:
        command += ["-i", str(audio)]
    if trim_end:
        command += ["-t", f"{trim_end - trim_start:.3f}"]

    filter_parts: list[str] = []
    map_args: list[str] = []
    if burn_subtitles:
        filter_parts.append(f"[0:v]subtitles={escape_filter_path(subtitle)}[vout]")
        map_args += ["-map", "[vout]"]
    else:
        map_args += ["-map", "0:v"]

    if audio:
        offset_ms = int(audio_offset * 1000)
        filter_parts.append(f"[0:a]volume={video_volume:.3f}[a0]")
        filter_parts.append(f"[1:a]adelay={offset_ms}|{offset_ms},volume={audio_volume:.3f}[a1]")
        filter_parts.append("[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        map_args += ["-map", "[aout]"]
    else:
        map_args += ["-map", "0:a?", "-filter:a", f"volume={video_volume:.3f}"]

    if filter_parts:
        command += ["-filter_complex", ";".join(filter_parts)]
    command += map_args
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]

    store.update(job, status="running", progress=20, message="ffmpeg 正在剪辑")
    run_process(job, store, command)
    store.update(
        job,
        status="completed",
        progress=100,
        message="剪辑完成",
        artifacts={"video": f"/api/editor/jobs/{job.id}/artifacts/{output.name}"},
    )
    register_editor_asset(user_id, output, "editor_render", editor_job_id=job.id)
    store.log(job, f"输出: {output}")


def render_artifact_path(user_id: int, job_id: str, filename: str) -> Path:
    root = (user_render_dir(user_id) / job_id).resolve()
    path = (root / filename).resolve()
    if root not in path.parents or not path.exists():
        raise FileNotFoundError(filename)
    return path
