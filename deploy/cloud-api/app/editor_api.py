"""Persistent local editor exports with cross-process admission and job leases."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .deps import CurrentUser
from .editor_cache import SceneCache, owner_key
from .editor_render import AUDIO_FORMATS, VIDEO_FORMATS, RenderError, render, validate_subtitle_style

router = APIRouter(prefix="/api/v1/editor/exports", tags=["editor"])
ROOT = Path(os.getenv("EDITOR_EXPORT_ROOT", "/var/lib/cloud-api/editor-exports"))
RETENTION_SECONDS = 86400
GLOBAL_CONCURRENCY = 2
MAX_USER_JOBS = 20
ACTIVE_RESERVATION_BYTES = 1600 * 1024 ** 2
MAX_STORAGE_BYTES = 10 * 1024 ** 3
_ID = re.compile(r"^edt_[0-9a-f]{32}$")


def _write(directory: Path, state: dict) -> None:
    temporary = directory / f"state.{uuid4().hex}.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / "state.json")


def _read(directory: Path) -> dict:
    return json.loads((directory / "state.json").read_text(encoding="utf-8"))


@contextmanager
def _admission():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o750)
    with (ROOT / "admission.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _cleanup_inputs(directory: Path) -> None:
    for name in ("video", "bgm", "subtitles", "captions.srt", "output.part.mp4"):
        (directory / name).unlink(missing_ok=True)
    for pattern in ("asset_*", "scene_*.mp4", "timeline.*"):
        for path in directory.glob(pattern):
            if path.is_file():
                path.unlink(missing_ok=True)


def _recover(directory: Path, state: dict) -> dict:
    if state["status"] != "processing":
        return state
    with (directory / "lease.lock").open("a+b") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return state
        state = _read(directory)
        if state["status"] == "processing":
            state.update(status="failed", error="Export interrupted by a service restart")
            _cleanup_inputs(directory)
            _write(directory, state)
    return state


def _states() -> list[tuple[Path, dict]]:
    result = []
    for directory in ROOT.glob("edt_*"):
        if not _ID.fullmatch(directory.name) or not directory.is_dir():
            continue
        try:
            state = _recover(directory, _read(directory))
        except (OSError, ValueError):
            continue
        if state["status"] != "processing" and state["expires_at"] <= time.time():
            shutil.rmtree(directory)
        else:
            result.append((directory, state))
    return result


def _public(state: dict) -> dict:
    return {key: state.get(key) for key in ("id", "client_id", "status", "created_at", "expires_at", "error", "duration", "cached_scenes")} | {
        "download_url": f"/api/v1/editor/exports/{state['id']}/download" if state["status"] == "completed" else None,
    }


def _owned(user_id: str, job_id: str) -> tuple[Path, dict]:
    if not _ID.fullmatch(job_id):
        raise HTTPException(404, "Export not found")
    with _admission():
        for directory, state in _states():
            if state["id"] == job_id and state["user_id"] == user_id:
                return directory, state
    raise HTTPException(404, "Export not found")


def _worker(directory: Path, options: dict, lease) -> None:
    state = _read(directory)
    try:
        options["_lease_fd"] = lease.fileno()
        if options.get("timeline"):
            from .editor_timeline import build_timeline
            state["cached_scenes"] = build_timeline(
                directory, options["timeline"], lease_fd=lease.fileno(), cache_owner=owner_key(state["user_id"]),
            )
        duration = render(directory, options)
        state.update(status="completed", duration=duration, error=None)
    except Exception as exc:
        state.update(status="failed", error=str(exc) if isinstance(exc, RenderError) else "Export failed")
    finally:
        try:
            _cleanup_inputs(directory)
            _write(directory, state)
        finally:
            lease.close()


def start_cleanup() -> threading.Event:
    """Each process may sweep; the admission lock serializes shared-state changes."""
    stop = threading.Event()

    def sweep():
        while not stop.is_set():
            try:
                with _admission():
                    _states()
                SceneCache(ROOT / "editor-scene-cache").sweep()
            except OSError:
                # A storage outage must not kill the API or leave a busy loop.
                pass
            stop.wait(300)

    threading.Thread(target=sweep, daemon=True).start()
    return stop


def _save_upload(upload: UploadFile, path: Path, maximum: int) -> None:
    total = 0
    with path.open("wb") as target:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise HTTPException(413, "Uploaded file exceeds its size limit")
            target.write(chunk)
    if not total:
        raise HTTPException(422, "Uploaded files must not be empty")


def _reserve(user, idempotency_key):
    with _admission():
        states = _states()
        if idempotency_key:
            previous = next((state for _, state in states if state["user_id"] == user.id and state["client_id"] == idempotency_key), None)
            if previous:
                return None, previous, None
        active = [state for _, state in states if state["status"] == "processing"]
        if len(active) >= GLOBAL_CONCURRENCY or any(state["user_id"] == user.id for state in active):
            raise HTTPException(429, "Export capacity reached; retry after the current export finishes")
        if sum(state["user_id"] == user.id for _, state in states) >= MAX_USER_JOBS:
            raise HTTPException(429, "Up to 20 exports can be retained per user for 24 hours")
        # Reserve peak input+output space for every active job, including uploads
        # that have not written their bytes yet in another worker process.
        used = len(active) * ACTIVE_RESERVATION_BYTES
        for saved_directory, saved_state in states:
            if saved_state["status"] == "completed":
                try:
                    used += (saved_directory / "output.mp4").stat().st_size
                except FileNotFoundError:
                    pass
        if used + ACTIVE_RESERVATION_BYTES > MAX_STORAGE_BYTES:
            raise HTTPException(503, "Export storage is temporarily full")
        job_id = f"edt_{uuid4().hex}"
        directory = ROOT / job_id
        directory.mkdir(mode=0o750)
        lease = (directory / "lease.lock").open("a+b")
        fcntl.flock(lease, fcntl.LOCK_EX)
        created = time.time()
        state = {"id": job_id, "client_id": idempotency_key, "user_id": user.id, "status": "processing",
                 "created_at": created, "expires_at": created + RETENTION_SECONDS, "error": None}
        _write(directory, state)
    return directory, state, lease


@router.post("", status_code=202)
def create_export(
    user: CurrentUser,
    video: UploadFile = File(...),
    bgm: UploadFile | None = File(None),
    subtitles: UploadFile | None = File(None),
    trim_start: float = Form(0, ge=0, le=3600),
    trim_end: float | None = Form(None, gt=0, le=3600),
    source_volume: float = Form(1, ge=0, le=2),
    bgm_volume: float = Form(0.35, ge=0, le=2),
    bgm_delay: float = Form(0, ge=0, le=600),
    bgm_loop: bool = Form(True),
    bgm_fade: float = Form(1, ge=0, le=30),
    burn_subtitles: bool = Form(True),
    subtitle_style: str = Form("{}"),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=1, max_length=128),
) -> dict:
    values = (trim_start, source_volume, bgm_volume, bgm_delay, bgm_fade)
    try:
        style = validate_subtitle_style(subtitle_style)
    except RenderError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not all(math.isfinite(value) for value in values) or (trim_end is not None and (not math.isfinite(trim_end) or not 0 < trim_end-trim_start <= 600)):
        raise HTTPException(422, "Invalid clip range or audio settings")
    video_format = VIDEO_FORMATS.get(Path(video.filename or "").suffix.lower())
    bgm_format = AUDIO_FORMATS.get(Path(bgm.filename or "").suffix.lower()) if bgm else None
    subtitle_extension = Path(subtitles.filename or "").suffix.lower() if subtitles else None
    if not video_format or (bgm and not bgm_format) or (subtitles and subtitle_extension not in {".srt", ".ass", ".vtt"}):
        raise HTTPException(415, "Unsupported video, music or subtitle file type")
    directory, state, lease = _reserve(user, idempotency_key)
    if directory is None:
        return _public(state)
    options = dict(video_format=video_format, bgm_format=bgm_format, subtitle_extension=subtitle_extension,
                   trim_start=trim_start, trim_end=trim_end, source_volume=source_volume, bgm_volume=bgm_volume,
                   bgm_delay=bgm_delay, bgm_loop=bgm_loop, bgm_fade=bgm_fade, burn_subtitles=burn_subtitles,
                   subtitle_style=style)
    try:
        _save_upload(video, directory / "video", 200 * 1024 ** 2)
        if bgm:
            _save_upload(bgm, directory / "bgm", 30 * 1024 ** 2)
        if subtitles:
            _save_upload(subtitles, directory / "subtitles", 1024 ** 2)
        threading.Thread(target=_worker, args=(directory, options, lease), daemon=True).start()
    except Exception:
        _cleanup_inputs(directory)
        state.update(status="failed", error="Upload did not complete")
        _write(directory, state)
        lease.close()
        raise
    return _public(state)


@router.get("")
def list_exports(user: CurrentUser) -> dict:
    with _admission():
        states = [_public(state) for _, state in _states() if state["user_id"] == user.id]
    return {"items": sorted(states, key=lambda state: state["created_at"], reverse=True)}


@router.get("/{job_id}")
def get_export(job_id: str, user: CurrentUser) -> dict:
    return _public(_owned(user.id, job_id)[1])


@router.get("/{job_id}/download")
def download_export(job_id: str, user: CurrentUser) -> FileResponse:
    directory, state = _owned(user.id, job_id)
    if state["status"] != "completed" or not (directory / "output.mp4").is_file():
        raise HTTPException(409, "Export is not ready")
    return FileResponse(directory / "output.mp4", media_type="video/mp4", filename=f"{job_id}.mp4")
