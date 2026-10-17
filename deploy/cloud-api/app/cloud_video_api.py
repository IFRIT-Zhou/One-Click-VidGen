"""Authenticated, free-test H3 queue. GPU execution belongs to a separate worker.

No wallet mutation, remote URL fetch, or caller-supplied ComfyUI graph is allowed.
Enable explicitly with CLOUD_VIDEO_ENABLED=1; optionally restrict account IDs with
CLOUD_VIDEO_TEST_USER_IDS (comma-separated). Storage must not be served statically.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import re
import secrets
import tempfile
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .deps import CurrentUser, Db
from .models import User

router = APIRouter(prefix="/api/v1/cloud/video", tags=["cloud-video-test"])
MODEL = "minimax-h3"
REVISION = "h3-fast-linux-v1"
MAX_VIDEO_PIXELS = 1664 * 936
RESOLUTIONS = ((1664, 936), (936, 1664), (1536, 864), (864, 1536), (1280, 720), (720, 1280), (512, 512), (640, 384), (384, 640))
DEFAULT_RESOLUTIONS = {"landscape": {"width": 1664, "height": 936},
                       "portrait": {"width": 936, "height": 1664}}
TERMINAL = {"succeeded", "failed", "cancelled"}
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000


def utcnow():
    return datetime.now(timezone.utc)


def identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CloudVideoAsset(Base):
    __tablename__ = "cloud_video_assets"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="image")
    storage_key: Mapped[str] = mapped_column(String(160))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CloudVideoQuote(Base):
    __tablename__ = "cloud_video_quotes"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    spec: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CloudVideoJob(Base):
    __tablename__ = "cloud_video_jobs"
    __table_args__ = (UniqueConstraint("user_id", "client_job_id", name="uq_cloud_video_client"),
                     UniqueConstraint("quote_id", name="uq_cloud_video_quote"))
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    quote_id: Mapped[str] = mapped_column(String(48))
    client_job_id: Mapped[str] = mapped_column(String(128))
    spec: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def create_schema(engine):
    Base.metadata.create_all(engine, tables=[CloudVideoAsset.__table__, CloudVideoQuote.__table__, CloudVideoJob.__table__])


def feature_enabled(user):
    allowed = {v.strip() for v in os.environ.get("CLOUD_VIDEO_TEST_USER_IDS", "").split(",") if v.strip()}
    return os.environ.get("CLOUD_VIDEO_ENABLED", "0") == "1" and (not allowed or user.id in allowed)


def require_enabled(user):
    if not feature_enabled(user):
        raise HTTPException(503, "H3 测试功能暂未向当前账号开放")


def storage_path(key):
    if not re.fullmatch(r"(?:assets/h3asset_[0-9a-f]{32}\.png|results/h3job_[0-9a-f]{32}\.mp4)", key):
        raise ValueError("Invalid H3 storage key")
    root = Path(os.environ.get("CLOUD_VIDEO_STORAGE_DIR", "/var/lib/cloud-api/h3")).resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Invalid H3 storage path")
    return target


def save_bytes(key, data):
    target = storage_path(key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o640)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class VideoSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=12000)
    reference_image_asset_id: str = Field(pattern=r"^h3asset_[0-9a-f]{32}$")
    width: int
    height: int
    duration_seconds: float = Field(ge=2, le=15, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: VideoSpec


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: str = Field(pattern=r"^h3quote_[0-9a-f]{32}$")
    client_job_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


def normalize_spec(spec):
    if spec.width <= 0 or spec.height <= 0 or spec.width * spec.height > MAX_VIDEO_PIXELS:
        raise HTTPException(422, "H3 output exceeds the 1664 x 936 total-pixel limit")
    if (spec.width, spec.height) not in RESOLUTIONS:
        raise HTTPException(422, "Unsupported H3 resolution")
    prompt = spec.prompt.strip()
    if not prompt:
        raise HTTPException(422, "Prompt must not be empty")
    frames = math.ceil(spec.duration_seconds * 24)
    frames += (5 - frames) % 17
    return {**spec.model_dump(), "prompt": prompt, "model": MODEL, "workflow_revision": REVISION,
            "fps": 24, "num_frames": frames, "seed": spec.seed if spec.seed is not None else secrets.randbelow(2**32),
            "actual_duration_seconds": frames / 24}


def owned_job(db, user, job_id, lock=False):
    statement = select(CloudVideoJob).where(CloudVideoJob.id == job_id, CloudVideoJob.user_id == user.id)
    job = db.scalar(statement.with_for_update() if lock else statement)
    if job is None:
        raise HTTPException(404, "Video job not found")
    return job


def snapshot(job):
    return {"job_id": job.id, "client_job_id": job.client_job_id, "status": job.status,
            "spec": job.spec, "progress": job.progress, "credits": "0", "test_mode": True,
            "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
            "result_url": f"/api/v1/cloud/video/jobs/{job.id}/result" if job.status == "succeeded" else None,
            "timings": job.timings, "created_at": job.created_at, "started_at": job.started_at,
            "finished_at": job.finished_at}


@router.get("/capabilities")
def capabilities(user: CurrentUser):
    return {"enabled": feature_enabled(user), "test_mode": True, "credits": "0", "model": MODEL,
            "workflow_revision": REVISION, "fps": 24, "resolutions": [{"width": w, "height": h} for w, h in RESOLUTIONS],
            "default_resolutions": DEFAULT_RESOLUTIONS,
            "max_output_pixels": MAX_VIDEO_PIXELS,
            "duration_seconds": {"min": 2, "max": 15}, "audio_supported": False,
            "max_upload_bytes": MAX_UPLOAD, "frame_divisor": 17, "frame_remainder": 5,
            "frame_rule": {"divisor": 17, "remainder": 5}}


@router.post("/assets", status_code=201)
async def upload_asset(db: Db, user: CurrentUser, kind: Literal["image"] = Form(...), file: UploadFile = File(...)):
    require_enabled(user)
    chunks, total = [], 0
    try:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD:
                raise HTTPException(413, "Reference image exceeds 20 MiB")
            chunks.append(chunk)
    finally:
        await file.close()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(b"".join(chunks))) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > MAX_PIXELS:
                    raise HTTPException(422, "Only JPEG/PNG/WebP images up to 16 megapixels are supported")
                source.load()
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                width, height = normalized.size
                buffer = io.BytesIO()
                normalized.save(buffer, format="PNG")
                data = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "Invalid reference image") from None
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Normalized reference image exceeds 20 MiB")
    # Serializes per-account upload quota and job submission on PostgreSQL.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    used = db.scalar(select(func.count()).select_from(CloudVideoAsset).where(CloudVideoAsset.user_id == user.id))
    if used >= int(os.environ.get("CLOUD_VIDEO_MAX_ASSETS_PER_USER", "500")):
        raise HTTPException(429, "H3 test asset quota reached")
    asset_id = identifier("h3asset")
    key = f"assets/{asset_id}.png"
    save_bytes(key, data)
    asset = CloudVideoAsset(id=asset_id, user_id=user.id, kind=kind, storage_key=key,
                            sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data), width=width, height=height,
                            expires_at=utcnow() + timedelta(days=7))
    try:
        db.add(asset)
        db.commit()
    except Exception:
        db.rollback()
        storage_path(key).unlink(missing_ok=True)
        raise
    return {"asset_id": asset.id, "kind": kind, "width": width, "height": height, "expires_at": asset.expires_at}


@router.post("/quotes", status_code=201)
def quote(body: QuoteRequest, db: Db, user: CurrentUser):
    require_enabled(user)
    spec = normalize_spec(body.spec)
    asset = db.scalar(select(CloudVideoAsset).where(CloudVideoAsset.id == spec["reference_image_asset_id"], CloudVideoAsset.user_id == user.id))
    if asset is None or aware(asset.expires_at) <= utcnow() or not storage_path(asset.storage_key).is_file():
        raise HTTPException(404, "Reference image not found or expired")
    record = CloudVideoQuote(id=identifier("h3quote"), user_id=user.id, spec=spec, expires_at=utcnow() + timedelta(minutes=15))
    db.add(record)
    db.commit()
    return {"quote_id": record.id, "spec": record.spec, "credits": "0", "test_mode": True, "expires_at": record.expires_at}


@router.post("/jobs", status_code=201)
def create_job(body: JobRequest, db: Db, user: CurrentUser):
    require_enabled(user)
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    client_id = body.client_job_id or identifier("h3client")
    existing = db.scalar(select(CloudVideoJob).where(CloudVideoJob.user_id == user.id, CloudVideoJob.client_job_id == client_id))
    if existing:
        if existing.quote_id != body.quote_id:
            raise HTTPException(409, "client_job_id already used for a different quote")
        return snapshot(existing)
    record = db.scalar(select(CloudVideoQuote).where(CloudVideoQuote.id == body.quote_id, CloudVideoQuote.user_id == user.id).with_for_update())
    if record is None:
        raise HTTPException(404, "Quote not found")
    existing = db.scalar(select(CloudVideoJob).where(CloudVideoJob.quote_id == record.id))
    if existing:
        if body.client_job_id is not None and existing.client_job_id != client_id:
            raise HTTPException(409, "Quote already submitted with a different client_job_id")
        return snapshot(existing)
    if aware(record.expires_at) <= utcnow():
        raise HTTPException(409, "Quote expired; request a new quote")
    if record.spec['width'] * record.spec['height'] > MAX_VIDEO_PIXELS:
        raise HTTPException(422, "H3 output exceeds the 1664 x 936 total-pixel limit; request a new quote")
    asset = db.get(CloudVideoAsset, record.spec["reference_image_asset_id"])
    if asset is None or asset.user_id != user.id or not storage_path(asset.storage_key).is_file():
        raise HTTPException(404, "Reference image unavailable")
    count = db.scalar(select(func.count()).select_from(CloudVideoJob).where(CloudVideoJob.user_id == user.id, CloudVideoJob.status.not_in(TERMINAL)))
    if count >= int(os.environ.get("CLOUD_VIDEO_MAX_PENDING_PER_USER", "6")):
        raise HTTPException(429, "Too many pending H3 test jobs")
    job = CloudVideoJob(id=identifier("h3job"), user_id=user.id, quote_id=record.id, client_job_id=client_id, spec=dict(record.spec), status="queued")
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Submission conflict; look up client_job_id before retrying") from None
    return snapshot(job)


@router.get("/jobs/by-client-id/{client_job_id}")
def get_by_client(client_job_id: str, db: Db, user: CurrentUser):
    job = db.scalar(select(CloudVideoJob).where(CloudVideoJob.user_id == user.id, CloudVideoJob.client_job_id == client_job_id))
    if job is None:
        raise HTTPException(404, "Video job not found")
    return snapshot(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Db, user: CurrentUser):
    return snapshot(owned_job(db, user, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Db, user: CurrentUser):
    job = owned_job(db, user, job_id, lock=True)
    if job.status == "queued":
        job.status, job.finished_at = "cancelled", utcnow()
    elif job.status == "running":
        job.status = "cancelling"
    db.commit()
    return snapshot(job)


@router.get("/jobs/{job_id}/result")
def result(job_id: str, db: Db, user: CurrentUser):
    job = owned_job(db, user, job_id)
    if job.status != "succeeded" or not job.result_path:
        raise HTTPException(409, "Video result is not ready")
    if job.result_path != f"results/{job.id}.mp4":
        raise HTTPException(404, "Video result unavailable")
    path = storage_path(job.result_path)
    if not path.is_file():
        raise HTTPException(404, "Video result unavailable")
    return FileResponse(path, media_type="video/mp4", filename=f"{job.id}.mp4", headers={"Cache-Control": "private, no-store"})
