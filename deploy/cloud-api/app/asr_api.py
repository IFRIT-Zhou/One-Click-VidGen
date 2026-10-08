"""Authenticated, durable ASR jobs in a separate low-priority CPU process."""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from .deps import CurrentUser

router = APIRouter(prefix="/api/v1/asr", tags=["asr"])
ROOT = Path(os.getenv("ASR_JOB_ROOT", "/var/lib/cloud-api/asr-jobs"))
PYTHON = Path(os.getenv("ASR_PYTHON", "/opt/ocvg-asr/.venv/bin/python"))
MODEL = Path(os.getenv("ASR_MODEL_PATH", "/opt/ocvg-asr/models/tiny"))
ENABLED = os.getenv("ASR_ENABLED", "false").lower() in {"1", "true", "yes"}
MAX_BYTES = 50 * 1024 * 1024
RETENTION = 86400
ID_PATTERN = re.compile(r"^asr_[0-9a-f]{32}$")
FORMATS = {".wav": "wav", ".mp3": "mp3", ".flac": "flac", ".ogg": "ogg", ".m4a": "mov", ".mp4": "mov", ".mov": "mov", ".webm": "matroska", ".mkv": "matroska"}


def available() -> bool:
    return ENABLED and PYTHON.is_file() and all((MODEL / filename).is_file() for filename in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")) and bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


def _read(directory: Path) -> dict:
    return json.loads((directory / "state.json").read_text(encoding="utf-8"))


def _write(directory: Path, state: dict) -> None:
    temporary = directory / f"state.{uuid4().hex}.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(directory / "state.json")


@contextmanager
def _admission():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o750)
    with (ROOT / "admission.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _states() -> list[tuple[Path, dict]]:
    result = []
    for directory in ROOT.glob("asr_*"):
        if not ID_PATTERN.fullmatch(directory.name) or not directory.is_dir():
            continue
        try:
            state = _read(directory)
            if state["status"] == "running":
                with (directory / "lease.lock").open("a+b") as lease:
                    try:
                        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        state = _read(directory)
                        if state["status"] == "running":
                            state.update(status="failed", error="识别进程已中断，请重新上传文件。")
                            for name in ("source", "audio.wav"):
                                (directory / name).unlink(missing_ok=True)
                            _write(directory, state)
            if state["status"] != "running" and state["expires_at"] <= time.time():
                shutil.rmtree(directory)
            else:
                result.append((directory, state))
        except (OSError, ValueError, KeyError):
            continue
    return result


def _public(state: dict) -> dict:
    return {key: state.get(key) for key in ("id", "client_id", "status", "created_at", "expires_at", "error", "cues", "duration", "detected_language")}


def start_cleanup() -> threading.Event:
    stop = threading.Event()
    def sweep():
        while not stop.is_set():
            try:
                with _admission():
                    _states()
            except OSError:
                pass
            stop.wait(300)
    threading.Thread(target=sweep, daemon=True).start()
    return stop


def _watch(process: subprocess.Popen) -> None:
    try:
        process.wait(timeout=900)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _owned(user_id: str, job_id: str) -> dict:
    if not ID_PATTERN.fullmatch(job_id):
        raise HTTPException(404, "ASR task not found")
    with _admission():
        for _, state in _states():
            if state["id"] == job_id and state["user_id"] == user_id:
                return state
    raise HTTPException(404, "ASR task not found")


@router.get("/status")
def asr_status(user: CurrentUser) -> dict:
    return {"available": available(), "max_bytes": MAX_BYTES, "max_duration_seconds": 600, "retention_seconds": RETENTION, "model": "tiny-multilingual-cpu-int8", "billing": "no-credit-charge"}


@router.post("/jobs", status_code=202)
def create_asr_job(user: CurrentUser, file: UploadFile = File(...), language: str = Form("zh"), idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128)) -> dict:
    if language not in {"zh", "en", "auto"}:
        raise HTTPException(422, "Language must be zh, en or auto")
    demuxer = FORMATS.get(Path(file.filename or "").suffix.lower())
    if not demuxer:
        raise HTTPException(415, "Supported formats: WAV, MP3, FLAC, OGG, M4A, MP4, MOV, WebM, MKV")
    with _admission():
        states = _states()
        for _, state in states:
            if state["user_id"] == user.id and state["client_id"] == idempotency_key:
                return _public(state)
        if not available():
            raise HTTPException(503, "独立 ASR 模型或运行环境尚未就绪，请稍后重试。")
        if any(state["status"] == "running" for _, state in states):
            raise HTTPException(429, "CPU 识别正在处理另一任务，请稍后重试。")
        if sum(state["user_id"] == user.id for _, state in states) >= 20:
            raise HTTPException(429, "每个账号每天最多提交 20 个识别任务。")
        directory = ROOT / f"asr_{uuid4().hex}"
        directory.mkdir(mode=0o750)
        lease = None
        try:
            total = 0
            with (directory / "source").open("wb") as target:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise HTTPException(413, "音视频文件不能超过 50 MB。")
                    target.write(chunk)
            if not total:
                raise HTTPException(422, "音视频文件不能为空。")
            now = time.time()
            state = {"id": directory.name, "client_id": idempotency_key, "user_id": user.id, "status": "running", "created_at": now, "expires_at": now + RETENTION, "demuxer": demuxer, "language": None if language == "auto" else language, "cues": [], "error": None}
            _write(directory, state)
            lease = (directory / "lease.lock").open("a+b")
            fcntl.flock(lease, fcntl.LOCK_EX)
            process = subprocess.Popen([str(PYTHON), str(Path(__file__).with_name("asr_worker.py")), str(directory.resolve()), str(MODEL.resolve())], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(lease.fileno(),), start_new_session=True, env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "HF_HUB_OFFLINE": "1"})
            threading.Thread(target=_watch, args=(process,), daemon=True).start()
            return _public(state)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            if lease:
                lease.close()


@router.get("/jobs/{job_id}")
def get_asr_job(job_id: str, user: CurrentUser) -> dict:
    return _public(_owned(user.id, job_id))


@router.get("/requests/{client_id}")
def find_asr_request(client_id: str, user: CurrentUser) -> dict:
    with _admission():
        for _, state in _states():
            if state["user_id"] == user.id and state["client_id"] == client_id:
                return _public(state)
    raise HTTPException(404, "ASR request not found")
