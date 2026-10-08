from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import httpx
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, load_only

from .adapters.model_pool import model_pool
from .adapters.payments import PaymentProduct, payment_provider
from .adapters.ray_tts import ray_tts
from .adapters.runninghub_image import runninghub_image
from .adapters.ican_image import ican_image, image_type
from . import ican_images
from .config import settings
from .storyboard_free import StoryboardStageRequest, stage_payload
from .deps import CurrentAdmin, CurrentUser, Db
from .documents import MAX_DOCUMENT_BYTES, DocumentParseError, parse_document
from .exceptions import ServiceError, UpstreamAuthError, UpstreamBusyError, UpstreamPermanentError
from .models import (AdminAuditLog, CloudJob, CreativeProject, DailyUsage, ImagePoolJob, ImagePoolMedia,
                     JobStatus, ModelPoolJob, OrderStatus, PaymentEvent,
                     QuotaPolicy, RechargeOrder, User, UserRole,
                     UserSession, UserStatus, UserVoice, WalletAccount, WalletLedger,
                     VideoAsset, VideoJob, VideoJobStatus, VideoStepStatus,
                     new_id, utcnow)
from .schemas import (CloudJobRequest, CreativeProjectCreateRequest,
                      CreativeProjectDetail, CreativeProjectSummary,
                      CreativeProjectUpdateRequest, CreditAdjustRequest, ImagePoolGenerateRequest,
                      ImagePoolQueryRequest, LoginRequest, ModelPoolCompletionRequest,
                      EnrollmentAdminActionRequest, EnrollmentTokenRequest, QuotaUpdateRequest, QuoteRequest, RechargeRequest,
                      RefreshRequest, RegisterRequest, TokenResponse, UserPublic,
                      VideoJobCreateRequest, VideoJobQuoteRequest)
from .security import (create_access_token, hash_password, hash_admin_reset_password, hash_refresh_token,
                       new_refresh_token, verify_password)
from .services import (PRODUCTS, ACTIVE_JOB_STATES, apply_wallet_entry, audit,
                       create_cloud_job, credit_recharge_order,
                       image_pool_reserve_credits,
                       mark_provider_account_failure, mark_provider_account_success,
                       model_pool_reserve_credits, pick_provider_account_slot,
                       provider_pool_available,
                       quote_credits, release_job_credits,
                       cancel_video_children, create_video_job,
                       release_video_job_credits, video_job_quote,
                       release_model_pool_job, reserve_image_pool_credits,
                       reserve_model_pool_credits, settle_model_pool_job,
                       wallet_for_update, json_hash)
from .video_storage import VideoStorageError, video_storage

router = APIRouter(prefix="/api/v1")
MAX_PROJECT_DOCUMENT_BYTES = 2 * 1024 * 1024

def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _credit(value: Any) -> float:
    return round(float(value or 0), 6)

def _tokens(db: Session, user: User) -> TokenResponse:
    access, expires = create_access_token(user.id, user.role, user.auth_version)
    refresh, refresh_hash = new_refresh_token()
    db.add(UserSession(id=new_id("ses"), user_id=user.id, refresh_token_hash=refresh_hash,
                       expires_at=utcnow() + timedelta(seconds=settings.refresh_token_seconds)))
    db.commit()
    return TokenResponse(access_token=access, expires_in=expires, refresh_token=refresh,
                         user=UserPublic.model_validate(user))

def _job(job: CloudJob) -> dict[str, Any]:
    result = job.result_json
    if isinstance(result, dict) and isinstance(result.get("chunks"), list):
        public_chunks = []
        for raw_item in result["chunks"]:
            if not isinstance(raw_item, dict):
                continue
            try:
                chunk_index = int(raw_item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            item = {
                key: value
                for key, value in raw_item.items()
                if key not in {"audio_url", "audio_bucket", "audio_key"}
            }
            item["audio_url"] = f"/api/v1/cloud/jobs/{job.id}/chunks/{chunk_index}/audio"
            public_chunks.append(item)
        result = {"chunks": sorted(public_chunks, key=lambda item: int(item["index"]))}
    return {"job_id": job.id, "client_job_id": job.client_job_id, "status": job.status,
            "progress": job.progress, "reserved_credits": _credit(job.reserved_credits),
            "consumed_credits": _credit(job.consumed_credits), "released_credits": _credit(job.released_credits),
            "total_chunks": job.total_chunks, "completed_chunks": job.completed_chunks,
            "message": job.message, "error": job.error_message, "result": result,
            "created_at": job.created_at, "updated_at": job.updated_at,
            "finished_at": job.finished_at, "expires_at": job.expires_at}

def _owned(db: Session, user: User, job_id: str) -> CloudJob:
    job = db.scalar(select(CloudJob).where(CloudJob.id == job_id, CloudJob.user_id == user.id))
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _video_owned(db: Session, user: User, job_id: str) -> VideoJob:
    job = db.scalar(select(VideoJob).where(
        VideoJob.id == job_id, VideoJob.user_id == user.id,
    ))
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video job not found")
    return job


def _project_owned(db: Session, user: User, project_id: str) -> CreativeProject:
    project = db.scalar(select(CreativeProject).where(
        CreativeProject.id == project_id,
        CreativeProject.user_id == user.id,
    ))
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _validate_project_document_size(document: dict[str, Any]) -> None:
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "document_json must contain valid JSON values",
        ) from exc
    if len(encoded) > MAX_PROJECT_DOCUMENT_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Project document exceeds the 2 MiB limit",
        )


def _video_job(job: VideoJob) -> dict[str, Any]:
    steps = {
        step.step_key: {
            "status": step.status,
            "attempt_count": step.attempt_count,
            "output": step.output_json,
            "error": (
                {"code": step.error_code, "message": step.error_message}
                if step.error_code or step.error_message else None
            ),
            "started_at": step.started_at,
            "finished_at": step.finished_at,
        }
        for step in job.steps
    }
    return {
        "job_id": job.id,
        "client_job_id": job.client_job_id,
        "status": job.status,
        "stage": job.current_step,
        "progress": job.progress,
        "steps": steps,
        "credits": {
            "reserved": _credit(job.reserved_credits),
            "consumed": _credit(job.consumed_credits),
            "released": _credit(job.released_credits),
        },
        "result": job.result_json,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code or job.error_message else None
        ),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


def _validate_video_voice(payload: VideoJobQuoteRequest, user: User, db: Session) -> None:
    if payload.voice.type == "preset":
        if payload.voice.id not in settings.preset_voice_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unsupported preset voice")
        return
    voice = db.scalar(select(UserVoice).where(
        UserVoice.id == payload.voice.id,
        UserVoice.user_id == user.id,
        UserVoice.status == "active",
    ))
    if not voice:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "User voice is unavailable")

def _order(order: RechargeOrder) -> dict[str, Any]:
    return {"order_id": order.id, "status": order.status, "amount_fen": order.amount_fen,
            "credits": order.credits, "payment": order.payment_payload,
            "expires_at": order.expires_at, "paid_at": order.paid_at}


PRESET_VOICE_DISPLAY_NAMES = {
    "voice_01.wav": "浑厚男音",
    "voice_02.wav": "温柔女声",
    "voice_03.wav": "元气女声",
    "voice_04.wav": "沉静女声",
    "voice_05.wav": "磁性男声",
    "voice_06.wav": "温和男声",
    "voice_07.wav": "沉稳男声",
    "voice_08.wav": "明亮少女声",
    "voice_09.wav": "知性女声",
    "voice_11.wav": "温润女声",
    "voice_12.wav": "低沉男声",
}


def _available_recharge_products() -> dict[str, tuple[int, int]]:
    return dict(PRODUCTS)


def _preset_voice(voice_id: str) -> dict[str, Any]:
    stem = voice_id.removeprefix("voice_").removesuffix(".wav")
    display_name = PRESET_VOICE_DISPLAY_NAMES.get(voice_id, f"云端默认音色 {stem}")
    return {"id": voice_id, "type": "preset", "display_name": display_name,
            "status": "active",
            "preview_url": f"/api/v1/cloud/voices/{voice_id}/audio"}


def _user_voice(voice: UserVoice) -> dict[str, Any]:
    return {
        "id": voice.id,
        "type": "uploaded",
        "display_name": voice.display_name,
        "status": voice.status,
        "audio": {"format": voice.audio_format, "size_bytes": voice.size_bytes},
        "created_at": voice.created_at,
    }


def _validate_voice(payload: QuoteRequest | CloudJobRequest, user: User, db: Session) -> None:
    if payload.voice.type == "preset":
        if payload.voice.id not in settings.preset_voice_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unsupported preset voice")
        return
    voice = db.scalar(select(UserVoice).where(
        UserVoice.id == payload.voice.id,
        UserVoice.user_id == user.id,
        UserVoice.status == "active",
    ))
    if not voice:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Uploaded voice is unavailable")


def _raise_ray_gateway_error(exc: httpx.HTTPError) -> None:
    if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Ray service is unavailable") from exc

    downstream_status = exc.response.status_code
    if downstream_status in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_413_CONTENT_TOO_LARGE,
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    }:
        raise HTTPException(downstream_status, "Ray rejected the voice file") from exc
    if downstream_status in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ray service authentication failed") from exc
    if downstream_status >= 500:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Ray service is unavailable") from exc
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ray service request failed") from exc

@router.get("/health")
def health() -> dict[str, Any]:
    try:
        ray = ray_tts.health()
        return {"ok": bool(ray.get("ok")), "control_api": True, "ray": ray}
    except Exception as exc:
        return {"ok": False, "control_api": True, "ray_error": str(exc)}

@router.post("/auth/register", status_code=201)
def register(payload: RegisterRequest, db: Db) -> dict[str, Any]:
    if not settings.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    user = User(id=new_id("usr"), email=email, password_hash=password_hash,
                status=UserStatus.ACTIVE.value, role=UserRole.USER.value,
                source=payload.source)
    db.add(user)
    db.add(WalletAccount(user_id=user.id))
    db.add(QuotaPolicy(user_id=user.id, max_concurrent_jobs=settings.default_max_concurrent_jobs,
                       daily_characters_limit=settings.default_daily_characters,
                       max_queue_jobs=settings.default_max_queue_jobs))
    db.commit()
    return {"user": UserPublic.model_validate(user), "verification_required": False}

@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Db) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()).with_for_update())
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is not active")
    return _tokens(db, user)

@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Db) -> TokenResponse:
    session = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_refresh_token(payload.refresh_token), UserSession.revoked_at.is_(None)))
    if not session or _aware(session.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = db.scalar(select(User).where(User.id == session.user_id).with_for_update())
    db.refresh(session)
    if session.revoked_at is not None or _aware(session.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    session.revoked_at = utcnow()
    if not user or user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is not active")
    return _tokens(db, user)

@router.post("/auth/logout", status_code=204)
def logout(payload: RefreshRequest, db: Db) -> None:
    session = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_refresh_token(payload.refresh_token)))
    if session:
        session.revoked_at = utcnow(); db.commit()

@router.get("/users/me", response_model=UserPublic)
def me(user: CurrentUser) -> User:
    return user


@router.post("/documents/parse")
async def parse_uploaded_document(
    user: CurrentUser,
    file: UploadFile = File(...),
) -> dict[str, str | int]:
    payload = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Document file exceeds the 5 MiB limit")
    try:
        return parse_document(file.filename, payload)
    except DocumentParseError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.get("/account/summary")
def summary(user: CurrentUser, db: Db) -> dict[str, Any]:
    wallet, quota = db.get(WalletAccount, user.id), db.get(QuotaPolicy, user.id)
    running = db.scalar(select(func.count(CloudJob.id)).where(CloudJob.user_id == user.id, CloudJob.status.in_([JobStatus.RUNNING.value, JobStatus.FINALIZING.value]))) or 0
    usage = db.scalar(select(DailyUsage).where(DailyUsage.user_id == user.id, DailyUsage.usage_date == utcnow().date()))
    return {"credits": {"available": _credit(wallet.available), "reserved": _credit(wallet.reserved), "consumed": _credit(wallet.consumed)},
            "quota": {"max_concurrent_jobs": quota.max_concurrent_jobs, "running_jobs": running,
                      "daily_characters_limit": quota.daily_characters_limit,
                      "daily_characters_used": usage.characters if usage else 0,
                      "max_queue_jobs": quota.max_queue_jobs}}


@router.get("/projects")
def list_projects(
    user: CurrentUser,
    db: Db,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    owned = CreativeProject.user_id == user.id
    total = db.scalar(select(func.count()).select_from(CreativeProject).where(owned)) or 0
    projects = db.scalars(
        select(CreativeProject)
        .options(load_only(
            CreativeProject.id, CreativeProject.title, CreativeProject.revision,
            CreativeProject.created_at, CreativeProject.updated_at,
        ))
        .where(owned)
        .order_by(CreativeProject.updated_at.desc(), CreativeProject.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [CreativeProjectSummary.model_validate(item) for item in projects],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    response_model=CreativeProjectDetail,
)
def create_project(
    payload: CreativeProjectCreateRequest,
    user: CurrentUser,
    db: Db,
) -> CreativeProject:
    _validate_project_document_size(payload.document_json)
    project = CreativeProject(
        id=new_id("prj"),
        user_id=user.id,
        title=payload.title,
        document_json=payload.document_json,
        revision=1,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=CreativeProjectDetail)
def get_project(project_id: str, user: CurrentUser, db: Db) -> CreativeProject:
    return _project_owned(db, user, project_id)


@router.put("/projects/{project_id}", response_model=CreativeProjectDetail)
def update_project(
    project_id: str,
    payload: CreativeProjectUpdateRequest,
    user: CurrentUser,
    db: Db,
) -> CreativeProject:
    _validate_project_document_size(payload.document_json)
    project = db.scalar(
        update(CreativeProject)
        .where(
            CreativeProject.id == project_id,
            CreativeProject.user_id == user.id,
            CreativeProject.revision == payload.expected_revision,
        )
        .values(
            title=payload.title,
            document_json=payload.document_json,
            revision=CreativeProject.revision + 1,
            updated_at=utcnow(),
        )
        .returning(CreativeProject)
        .execution_options(synchronize_session=False)
    )
    if project is None:
        if db.scalar(select(CreativeProject.id).where(
            CreativeProject.id == project_id,
            CreativeProject.user_id == user.id,
        )) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "PROJECT_REVISION_CONFLICT",
                "message": "Project was modified by another request",
            },
        )
    db.commit()
    return project


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, user: CurrentUser, db: Db) -> None:
    project = _project_owned(db, user, project_id)
    db.delete(project)
    db.commit()

@router.get("/wallet/ledger")
def ledger(user: CurrentUser, db: Db, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    query = select(WalletLedger).where(WalletLedger.user_id == user.id).order_by(WalletLedger.created_at.desc()).offset((page-1)*page_size).limit(page_size)
    items = db.scalars(query).all()
    total = db.scalar(select(func.count()).select_from(WalletLedger).where(WalletLedger.user_id == user.id)) or 0
    return {"items": [{"id": i.id, "type": i.entry_type, "amount": i.available_delta,
                       "balance_after": i.available_after, "reference_type": i.reference_type,
                       "reference_id": i.reference_id, "created_at": i.created_at} for i in items],
            "page": page, "page_size": page_size, "total": total}


@router.get("/cloud/voices")
def voices(
    user: CurrentUser,
    db: Db,
    voice_type: Literal["all", "preset", "uploaded", "custom"] = Query("all", alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    preset_items = [_preset_voice(voice_id) for voice_id in settings.preset_voice_ids]
    uploaded_items = [_user_voice(item) for item in db.scalars(select(UserVoice).where(
        UserVoice.user_id == user.id,
        UserVoice.status == "active",
    ).order_by(UserVoice.created_at.desc())).all()]
    if voice_type == "preset":
        filtered = preset_items
    elif voice_type in {"uploaded", "custom"}:
        filtered = uploaded_items
    else:
        filtered = [*preset_items, *uploaded_items]
    start = (page - 1) * page_size
    return {
        "items": filtered[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": len(filtered),
        "default_voice_id": settings.preset_voice_ids[0] if settings.preset_voice_ids else None,
        "capabilities": {"preset": True, "preview_preset": True,
                         "upload": True, "delete_upload": True},
        "limits": {"max_uploaded_voices": settings.max_user_voices,
                   "uploaded_voices_used": len(uploaded_items),
                   "max_voice_bytes": settings.max_voice_bytes},
    }


@router.get("/cloud/voices/{voice_id}/audio")
def preset_voice_audio(voice_id: str, user: CurrentUser) -> FileResponse:
    if voice_id not in settings.preset_voice_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset voice not found")
    voice_root = Path(settings.preset_voice_dir).resolve()
    voice_path = (voice_root / voice_id).resolve()
    if voice_root not in voice_path.parents or not voice_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset voice audio is unavailable")
    return FileResponse(
        voice_path,
        media_type="audio/wav",
        filename=voice_id,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/cloud/voices", status_code=201)
async def upload_voice(
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),
    display_name: str = Form(..., min_length=1, max_length=80),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=180),
) -> dict[str, Any]:
    existing = db.scalar(select(UserVoice).where(
        UserVoice.user_id == user.id,
        UserVoice.idempotency_key == idempotency_key,
    ))
    if existing:
        return {"voice": _user_voice(existing), "deduplicated": True}
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".wav", ".mp3", ".flac"}:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Voice must be WAV, MP3 or FLAC")
    payload = await file.read(settings.max_voice_bytes + 1)
    if not payload or len(payload) > settings.max_voice_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Voice file is empty or too large")
    digest = hashlib.sha256(payload).hexdigest()
    duplicate = db.scalar(select(UserVoice).where(
        UserVoice.user_id == user.id,
        UserVoice.sha256 == digest,
        UserVoice.status == "active",
    ))
    if duplicate:
        return {"voice": _user_voice(duplicate), "deduplicated": True}
    count = db.scalar(select(func.count()).select_from(UserVoice).where(
        UserVoice.user_id == user.id,
        UserVoice.status == "active",
    )) or 0
    if count >= settings.max_user_voices:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Uploaded voice quota exceeded")
    try:
        remote = ray_tts.upload_voice(
            filename=Path(file.filename or f"voice{suffix}").name,
            content_type=str(file.content_type or "application/octet-stream"),
            payload=payload,
        )
    except httpx.HTTPError as exc:
        _raise_ray_gateway_error(exc)
    voice_id = str(remote.get("voice_id") or "")
    if not voice_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Ray voice upload response is invalid")
    item = UserVoice(
        id=voice_id,
        user_id=user.id,
        display_name=display_name.strip(),
        status="active",
        sha256=str(remote.get("sha256") or digest),
        audio_format=suffix.lstrip("."),
        size_bytes=int(remote.get("size_bytes") or len(payload)),
        idempotency_key=idempotency_key,
    )
    db.add(item)
    db.commit()
    return {"voice": _user_voice(item), "deduplicated": False}


@router.get("/cloud/voices/{voice_id}")
def voice_detail(voice_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
    item = db.scalar(select(UserVoice).where(
        UserVoice.id == voice_id,
        UserVoice.user_id == user.id,
        UserVoice.status == "active",
    ))
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voice not found")
    return _user_voice(item)


@router.delete("/cloud/voices/{voice_id}", status_code=204)
def delete_voice(voice_id: str, user: CurrentUser, db: Db) -> None:
    item = db.scalar(select(UserVoice).where(
        UserVoice.id == voice_id,
        UserVoice.user_id == user.id,
        UserVoice.status == "active",
    ))
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voice not found")
    active_jobs = db.scalars(select(CloudJob).where(
        CloudJob.user_id == user.id,
        CloudJob.status.in_(ACTIVE_JOB_STATES),
    )).all()
    if any((job.request_json.get("voice") or {}).get("id") == voice_id for job in active_jobs):
        raise HTTPException(status.HTTP_409_CONFLICT, "Voice is used by an active job")
    try:
        ray_tts.delete_voice(voice_id)
    except httpx.HTTPError as exc:
        _raise_ray_gateway_error(exc)
    item.status = "deleted"
    item.deleted_at = utcnow()
    db.commit()


@router.post("/cloud/quotes")
def quote(payload: QuoteRequest, user: CurrentUser, db: Db) -> dict[str, Any]:
    _validate_voice(payload, user, db)
    total = sum(len(c.text) for c in payload.chunks)
    if total > 5000: raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Task too large")
    return {"quote_id": new_id("quote"), "estimated_credits": _credit(quote_credits(total)),
            "expires_at": utcnow() + timedelta(minutes=5), "limits": {"max_chunk_chars": 1000, "max_total_chars": 5000}}


@router.get("/recharge/products")
def recharge_products() -> dict[str, Any]:
    return {
        "items": [
            {
                "product_id": product_id,
                "amount_fen": amount,
                "credits": credits,
                "temporary": False,
            }
            for product_id, (amount, credits) in _available_recharge_products().items()
        ],
        "test_product_enabled": False,
    }

@router.post("/recharge/orders", status_code=201)
def recharge(payload: RechargeRequest, user: CurrentUser, db: Db, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, Any]:
    old = db.scalar(select(RechargeOrder).where(RechargeOrder.user_id == user.id, RechargeOrder.idempotency_key == idempotency_key))
    if old: return _order(old)
    product = _available_recharge_products().get(payload.product_id)
    if not product: raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown product")
    amount, credits = product; oid = new_id("ord"); provider = payment_provider(payload.payment_provider)
    provider_id, payment = provider.create_payment(oid, PaymentProduct(payload.product_id, amount, credits))
    order = RechargeOrder(id=oid, user_id=user.id, provider=payload.payment_provider, product_id=payload.product_id,
                          amount_fen=amount, credits=credits, idempotency_key=idempotency_key, provider_order_id=provider_id,
                          payment_payload=payment, expires_at=utcnow()+timedelta(minutes=30))
    db.add(order); db.commit(); return _order(order)

@router.get("/recharge/orders/{order_id}")
def recharge_status(order_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
    order = db.scalar(select(RechargeOrder).where(RechargeOrder.id == order_id, RechargeOrder.user_id == user.id))
    if not order: raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return _order(order)

@router.post("/payments/{provider_name}/notify")
async def payment_notify(
    provider_name: str,
    request: Request,
    db: Db,
    x_mock_signature: str | None = Header(None, alias="X-Mock-Signature"),
):
    raw = await request.body()
    provider = payment_provider(provider_name)
    payload = provider.parse_notification(raw, x_mock_signature)
    order = db.scalar(
        select(RechargeOrder)
        .where(RechargeOrder.id == payload.order_id)
        .with_for_update()
    )
    if (
        not order
        or order.provider != provider_name
        or order.provider_order_id != payload.provider_order_id
        or order.amount_fen != payload.amount_fen
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment validation failed")
    duplicate = db.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == provider_name,
            PaymentEvent.provider_event_id == payload.event_id,
        )
    )
    if not duplicate:
        db.add(
            PaymentEvent(
                id=new_id("payevt"),
                provider=provider_name,
                provider_event_id=payload.event_id,
                order_id=order.id,
                event_type=payload.status,
                payload=payload.payload,
            )
        )
        if payload.status == "paid" and order.status != OrderStatus.PAID.value:
            order.status = OrderStatus.PAID.value
            order.paid_at = utcnow()
            credit_recharge_order(db, order)
        elif (
            payload.status == "cancelled"
            and order.status == OrderStatus.PENDING.value
        ):
            order.status = OrderStatus.CANCELLED.value
        db.commit()
    if provider_name == "alipay":
        return PlainTextResponse("success")
    return {"ok": True, "duplicate": bool(duplicate)}

@router.post("/cloud/jobs", status_code=202)
def create_job(payload: CloudJobRequest, user: CurrentUser, db: Db, idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
    _validate_voice(payload, user, db)
    if idempotency_key and idempotency_key != payload.client_job_id: raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency-Key mismatch")
    request_json = payload.model_dump(exclude={"client_job_id"})
    try:
        job, _ = create_cloud_job(db, user=user, client_job_id=payload.client_job_id, request_json=request_json); db.commit()
    except Exception:
        db.rollback(); raise
    return {"job_id": job.id, "status": job.status, "reserved_credits": _credit(job.reserved_credits), "status_url": f"/api/v1/cloud/jobs/{job.id}"}

@router.get("/cloud/jobs")
def jobs(user: CurrentUser, db: Db, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), job_status: str | None = Query(None, alias="status")) -> dict[str, Any]:
    filters = [CloudJob.user_id == user.id]
    if job_status: filters.append(CloudJob.status == job_status)
    items = db.scalars(select(CloudJob).where(*filters).order_by(CloudJob.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {"items": [_job(j) for j in items], "page": page, "page_size": page_size, "total": db.scalar(select(func.count()).select_from(CloudJob).where(*filters)) or 0}

@router.get("/cloud/jobs/{job_id}")
def job(job_id: str, user: CurrentUser, db: Db) -> dict[str, Any]: return _job(_owned(db, user, job_id))

@router.post("/cloud/jobs/{job_id}/cancel")
def cancel(job_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
    item = _owned(db, user, job_id)
    if item.status in {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}: return _job(item)
    if item.status == JobStatus.QUEUED.value:
        item.status = JobStatus.CANCELLED.value; item.finished_at = utcnow(); release_job_credits(db, item)
    else: item.status = JobStatus.CANCEL_REQUESTED.value; item.cancel_requested = True
    db.commit(); return _job(item)

@router.get("/cloud/jobs/{job_id}/chunks/{chunk_index}/audio")
def download_audio(job_id: str, chunk_index: int, user: CurrentUser, db: Db) -> StreamingResponse:
    item = _owned(db, user, job_id)
    result_chunks = (
        item.result_json.get("chunks", [])
        if isinstance(item.result_json, dict)
        else []
    )
    chunk_ready = any(
        isinstance(chunk, dict) and int(chunk.get("index", -1)) == chunk_index
        for chunk in result_chunks
    )
    if not chunk_ready or not item.ray_job_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Audio is not ready")
    if item.expires_at and item.expires_at < utcnow():
        raise HTTPException(status.HTTP_410_GONE, "Result expired")
    return StreamingResponse(ray_tts.stream_audio(item.ray_job_id, chunk_index), media_type="audio/wav", headers={"Content-Disposition": f'attachment; filename="chunk_{chunk_index:04d}.wav"', "Cache-Control": "private, no-store"})


@router.post("/video-jobs/quote")
def quote_video_job(
    payload: VideoJobQuoteRequest, user: CurrentUser, db: Db,
) -> dict[str, Any]:
    _validate_video_voice(payload, user, db)
    request_json = payload.model_dump(mode="json", exclude={"client_job_id"})
    if payload.video.method == "ican":
        if not ican_image.configured():
            raise ServiceError("ICAN_NOT_CONFIGURED", "ICAN 尚未配置", 503)
        ican_images.resolve_references(db, user.id, request_json["reference_image_urls"])
    else:
        request_json["reference_image_urls"] = _resolve_image_references(
            db, user, request_json["reference_image_urls"],
        )
    quote = video_job_quote(request_json)
    return {
        "reserved_credits": _credit(quote["reserved_credits"]),
        "breakdown": {
            key: _credit(value) for key, value in quote["breakdown"].items()
        },
        "expires_at": utcnow() + timedelta(minutes=5),
        "currency": "credits",
    }


@router.post("/video-jobs", status_code=202)
def create_one_click_video_job(
    payload: VideoJobCreateRequest,
    user: CurrentUser,
    db: Db,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=180
    ),
) -> dict[str, Any]:
    if idempotency_key != payload.client_job_id:
        raise ServiceError(
            "IDEMPOTENCY_CONFLICT", "Idempotency-Key 与 client_job_id 不一致", 409
        )
    _validate_video_voice(payload, user, db)
    request_json = payload.model_dump(mode="json", exclude={"client_job_id"})
    if payload.video.method == "ican":
        if not ican_image.configured():
            raise ServiceError("ICAN_NOT_CONFIGURED", "ICAN 尚未配置", 503)
        ican_images.resolve_references(db, user.id, request_json["reference_image_urls"])
    else:
        request_json["reference_image_urls"] = _resolve_image_references(
            db, user, request_json["reference_image_urls"],
        )
    try:
        job, _ = create_video_job(
            db, user=user, client_job_id=payload.client_job_id,
            request_json=request_json,
        )
        db.commit()
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == status.HTTP_409_CONFLICT:
            raise ServiceError("INSUFFICIENT_CREDITS", "积分不足", 402) from exc
        raise
    except Exception:
        db.rollback()
        raise
    return _video_job(job)


@router.get("/video-jobs")
def list_video_jobs(
    user: CurrentUser,
    db: Db,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    job_status: str | None = Query(None, alias="status"),
) -> dict[str, Any]:
    filters = [VideoJob.user_id == user.id]
    if job_status:
        filters.append(VideoJob.status == job_status)
    items = db.scalars(select(VideoJob).where(*filters).order_by(
        VideoJob.created_at.desc()
    ).offset((page - 1) * page_size).limit(page_size)).all()
    total = db.scalar(select(func.count()).select_from(VideoJob).where(*filters)) or 0
    return {"items": [_video_job(item) for item in items], "page": page,
            "page_size": page_size, "total": total}


@router.get("/video-jobs/{job_id}")
def get_video_job(job_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
    return _video_job(_video_owned(db, user, job_id))


@router.post("/video-jobs/{job_id}/cancel")
def cancel_video_job(job_id: str, user: CurrentUser, db: Db) -> dict[str, Any]:
    job = _video_owned(db, user, job_id)
    terminal = {
        VideoJobStatus.COMPLETED.value, VideoJobStatus.FAILED.value,
        VideoJobStatus.CANCELLED.value,
    }
    if job.status in terminal:
        return _video_job(job)
    job.cancel_requested = True
    cancel_video_children(db, job)
    if job.status == VideoJobStatus.QUEUED.value:
        job.status = VideoJobStatus.CANCELLED.value
        job.current_step = None
        job.finished_at = utcnow()
        for step in job.steps:
            if step.status == VideoStepStatus.PENDING.value:
                step.status = VideoStepStatus.CANCELLED.value
                step.finished_at = utcnow()
        release_video_job_credits(db, job)
    else:
        job.status = VideoJobStatus.CANCEL_REQUESTED.value
    db.commit()
    return _video_job(job)


def _final_video_object_key(asset: VideoAsset, *, user_id: str, job_id: str) -> str:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    raw_key = metadata.get("object_key") or asset.uri
    if not isinstance(raw_key, str) or not raw_key:
        raise ServiceError("VIDEO_RESULT_MISSING", "视频结果文件缺失", 409)
    object_key = raw_key
    for prefix in ("object://", "local://"):
        if object_key.startswith(prefix):
            object_key = object_key.removeprefix(prefix)
            break
    parts = PurePosixPath(object_key).parts
    if (
        len(parts) != 4
        or parts[:3] != (user_id, job_id, "final_video")
        or parts[3] in {"", ".", ".."}
    ):
        raise ServiceError("VIDEO_RESULT_MISSING", "视频结果文件缺失", 409)
    return object_key


@router.get("/video-jobs/{job_id}/result")
def get_video_job_result(job_id: str, user: CurrentUser, db: Db) -> StreamingResponse:
    job = _video_owned(db, user, job_id)
    if job.status != VideoJobStatus.COMPLETED.value:
        raise ServiceError("VIDEO_RESULT_NOT_READY", "视频结果尚未就绪", 409)
    asset = db.scalar(select(VideoAsset).where(
        VideoAsset.video_job_id == job.id,
        VideoAsset.kind == "final_video",
        VideoAsset.status == "ready",
    ).order_by(VideoAsset.updated_at.desc()))
    if asset is None:
        raise ServiceError("VIDEO_RESULT_MISSING", "视频结果文件缺失", 409)
    object_key = _final_video_object_key(asset, user_id=user.id, job_id=job.id)
    try:
        path = video_storage.resolve_object(object_key)
    except VideoStorageError as exc:
        raise ServiceError("VIDEO_RESULT_MISSING", "视频结果文件缺失", 409) from exc
    return StreamingResponse(
        video_storage.stream_object(object_key),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="video_{job.id}.mp4"',
            "Cache-Control": "private, no-store",
            "Content-Length": str(path.stat().st_size),
        },
    )


def _request_token_estimate(payload: ModelPoolCompletionRequest) -> int:
    characters = 0
    for message in payload.messages:
        if isinstance(message.content, str):
            characters += len(message.content)
        else:
            characters += len(json.dumps(message.content, ensure_ascii=False))
    return max(1, (characters + 3) // 4)


@router.post("/model-pool/status")
def model_pool_status(user: CurrentUser, db: Db) -> dict[str, Any]:
    available = model_pool.configured() and provider_pool_available(db, provider="model_pool")
    return {
        "code": 0,
        "data": {
            "available": available,
            "model": "auto",
            "models": list(settings.model_pool_models) if available else [],
        },
    }


@router.post("/model-pool/v1/chat/completions")
def model_pool_completion(
    payload: ModelPoolCompletionRequest,
    user: CurrentUser,
    db: Db,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=180),
) -> dict[str, Any]:
    return _model_pool_completion(payload, user, db, idempotency_key, bill_user=True)


@router.post("/storyboard/plan-stage")
def storyboard_plan_stage(
    payload: StoryboardStageRequest,
    user: CurrentUser,
    db: Db,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=180),
) -> dict[str, Any]:
    key = f"storyboard:{idempotency_key}" if idempotency_key else None
    return _model_pool_completion(stage_payload(payload), user, db, key, bill_user=False)


def _model_pool_completion(
    payload: ModelPoolCompletionRequest, user: User, db: Session,
    idempotency_key: str | None, *, bill_user: bool,
) -> dict[str, Any]:
    if not model_pool.configured():
        raise ServiceError("MODEL_POOL_UNAVAILABLE", "文本模型号池暂不可用", 503)
    if payload.model != "auto" and payload.model not in settings.model_pool_models:
        raise ServiceError("MODEL_NOT_ALLOWED", "请求的文本模型不在云端允许列表中", 422)
    upstream_payload = payload.model_dump(exclude_none=True)
    request_hash = json_hash(upstream_payload if bill_user else {"free_storyboard": upstream_payload})
    key = idempotency_key or new_id("llmreq")
    existing = db.scalar(select(ModelPoolJob).where(
        ModelPoolJob.user_id == user.id,
        ModelPoolJob.idempotency_key == key,
    ))
    if existing:
        if existing.request_hash != request_hash:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不一致", 409)
        if existing.status == "completed" and isinstance(existing.response_json, dict):
            return existing.response_json
        raise ServiceError("MODEL_REQUEST_IN_PROGRESS", "该文本请求正在处理", 409)

    reserve = 0 if not bill_user else model_pool_reserve_credits(
        _request_token_estimate(payload), payload.max_tokens or 4096
    )
    job = ModelPoolJob(
        id=new_id("llm"), user_id=user.id, idempotency_key=key, status="running",
        request_hash=request_hash, reserved_credits=reserve,
    )
    db.add(job)
    try:
        if bill_user:
            reserve_model_pool_credits(db, user_id=user.id, job_id=job.id, credits=reserve)
        db.commit()
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == status.HTTP_409_CONFLICT:
            raise ServiceError("INSUFFICIENT_CREDITS", "积分不足", 402) from exc
        raise

    last_error: ServiceError | None = None
    attempted: set[int] = set()
    for _ in settings.model_pool_api_keys:
        try:
            state = pick_provider_account_slot(db, provider="model_pool")
        except ServiceError as exc:
            db.rollback()
            last_error = exc
            break
        slot = state.slot
        if slot in attempted:
            db.rollback()
            break
        attempted.add(slot)
        job = db.get(ModelPoolJob, job.id, with_for_update=True)
        job.account_slot = slot
        db.commit()
        try:
            result = model_pool.complete(slot=slot, payload=upstream_payload)
        except (UpstreamAuthError, UpstreamBusyError) as exc:
            db.rollback()
            mark_provider_account_failure(
                db, provider="model_pool", slot=slot,
                error_code=str(exc.details.get("upstream_status") or exc.code),
                permanent=isinstance(exc, UpstreamAuthError),
            )
            db.commit()
            last_error = exc
            continue
        except UpstreamPermanentError as exc:
            last_error = exc
            break
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        if prompt_tokens + completion_tokens <= 0:
            prompt_tokens = max(1, int(usage.get("total_tokens") or 0))
        actual = model_pool_reserve_credits(prompt_tokens, completion_tokens) if bill_user else 0
        with db.begin():
            job = db.get(ModelPoolJob, job.id, with_for_update=True)
            job.status = "completed"
            job.response_json = result
            job.upstream_usage = usage
            job.finished_at = utcnow()
            if bill_user:
                settle_model_pool_job(db, job=job, actual_credits=actual)
            mark_provider_account_success(db, provider="model_pool", slot=slot)
        return result

    with db.begin():
        job = db.get(ModelPoolJob, job.id, with_for_update=True)
        job.status = "failed"
        job.error_code = last_error.code if last_error else "MODEL_POOL_UNAVAILABLE"
        job.finished_at = utcnow()
        if bill_user:
            release_model_pool_job(db, job=job)
    if last_error:
        raise last_error
    raise ServiceError("MODEL_POOL_UNAVAILABLE", "文本模型号池暂不可用", 503)


def _image_owned(db: Session, user: User, task_id: str) -> ImagePoolJob:
    job = db.scalar(select(ImagePoolJob).where(
        ImagePoolJob.id == task_id,
        ImagePoolJob.user_id == user.id,
    ))
    if not job:
        raise ServiceError("IMAGE_JOB_NOT_FOUND", "找不到该出图任务", 404)
    return job


def _resolve_image_references(db: Session, user: User, urls: list[str]) -> list[str]:
    resolved: list[str] = []
    marker = "/api/v1/image-pool/media/"
    for url in urls:
        value = str(url).strip()
        if not value:
            continue
        if marker in value:
            asset_id = value.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0]
            asset = db.scalar(select(ImagePoolMedia).where(
                ImagePoolMedia.id == asset_id,
                ImagePoolMedia.user_id == user.id,
            ))
            if not asset or asset.provider != "runninghub":
                raise ServiceError("IMAGE_ASSET_NOT_FOUND", "找不到该参考图", 404)
            resolved.append(asset.upstream_url)
        else:
            raise ServiceError(
                "IMAGE_REFERENCE_NOT_ALLOWED",
                "参考图必须先通过当前账号上传",
                400,
            )
    return resolved


@router.post("/image-pool/account-status")
def image_pool_account_status(user: CurrentUser, db: Db, provider: Literal["runninghub", "ican"] = "runninghub") -> dict[str, Any]:
    active = db.scalar(select(func.count()).select_from(ImagePoolJob).where(
        ImagePoolJob.provider == provider,
        ImagePoolJob.status.in_(["queued", "submitting", "running"])
    )) or 0
    if provider == "ican":
        return {"code": 0, "data": {"currentTaskCounts": active, "provider": provider,
                "available": ican_image.configured() and bool(ican_images.prices())}}
    return {
        "code": 0,
        "data": {
            "currentTaskCounts": active,
            "available": runninghub_image.configured()
            and provider_pool_available(db, provider="runninghub_image"),
        },
    }


@router.post("/image-pool/generate")
def image_pool_generate(
    payload: ImagePoolGenerateRequest,
    user: CurrentUser,
    db: Db,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=180),
) -> dict[str, Any]:
    if payload.provider == "ican":
        return ican_images.enqueue(db, user, payload, idempotency_key)
    if payload.model is not None or payload.size is not None:
        raise ServiceError("IMAGE_PARAMETERS_INVALID", "RunningHub 使用 aspectRatio/resolution", 422)
    if not runninghub_image.configured():
        raise ServiceError("IMAGE_POOL_UNAVAILABLE", "图像号池暂不可用", 503)
    request_json = {
        "prompt": payload.prompt.strip(),
        "aspectRatio": payload.aspect_ratio,
        "resolution": payload.resolution,
        "imageUrls": _resolve_image_references(db, user, payload.image_urls),
    }
    request_hash = json_hash(request_json)
    if idempotency_key and payload.client_task_id and idempotency_key != payload.client_task_id:
        raise ServiceError("IDEMPOTENCY_CONFLICT", "Idempotency-Key 与 clientJobId 不一致", 409)
    key = idempotency_key or payload.client_task_id or new_id("imgreq")
    existing = db.scalar(select(ImagePoolJob).where(
        ImagePoolJob.user_id == user.id,
        ImagePoolJob.idempotency_key == key,
    ))
    if existing:
        if existing.request_hash != request_hash:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不一致", 409)
        return {"code": 0, "data": {"taskId": existing.id}, "reserved_credits": _credit(existing.reserved_credits)}
    reserve = image_pool_reserve_credits()
    job = ImagePoolJob(
        id=new_id("img"), user_id=user.id, idempotency_key=key,
        status="queued", request_json=request_json, request_hash=request_hash,
        reserved_credits=reserve,
    )
    db.add(job)
    try:
        reserve_image_pool_credits(db, user_id=user.id, job_id=job.id, credits=reserve)
        db.commit()
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == status.HTTP_409_CONFLICT:
            raise ServiceError("INSUFFICIENT_CREDITS", "积分不足", 402) from exc
        raise
    return {"code": 0, "data": {"taskId": job.id}, "reserved_credits": _credit(reserve)}


@router.post("/image-pool/query")
def image_pool_query(payload: ImagePoolQueryRequest, request: Request, user: CurrentUser, db: Db) -> dict[str, Any]:
    job = _image_owned(db, user, payload.task_id)
    public_status = {
        "queued": "QUEUED", "submitting": "QUEUED", "running": "RUNNING",
        "completed": "SUCCESS", "failed": "FAILED",
    }.get(job.status, job.status.upper())
    data: dict[str, Any] = {"taskId": job.id, "status": public_status}
    data["provider"] = job.provider
    if job.provider == "ican":
        data["model"] = job.request_json["model"]
        data["reserved_credits"] = _credit(job.reserved_credits)
        data["released_credits"] = _credit(job.released_credits)
    if job.status == "completed":
        data["imageUrl"] = str(request.url_for("image_pool_result", job_id=job.id))
        data["charged_credits"] = _credit(job.consumed_credits)
    if job.status == "failed":
        data["errorCode"] = job.error_code or "IMAGE_GENERATION_FAILED"
        data["message"] = job.error_message or "图片生成失败"
    return {"code": 0, "data": data}


@router.get("/image-pool/results/{job_id}", name="image_pool_result")
def image_pool_result(job_id: str, user: CurrentUser, db: Db) -> StreamingResponse:
    job = _image_owned(db, user, job_id)
    if job.status != "completed" or not job.result_url or job.account_slot is None:
        raise ServiceError("IMAGE_NOT_READY", "图片尚未生成完成", 409)
    if job.provider == "ican":
        data = ican_images.read_image(job.result_url)
        return StreamingResponse(iter([data]), media_type=image_type(data), headers={"Cache-Control": "private, no-store"})
    chunks, content_type = runninghub_image.stream_result(
        slot=job.account_slot, url=job.result_url
    )
    return StreamingResponse(chunks, media_type=content_type, headers={"Cache-Control": "private, no-store"})


@router.post("/image-pool/media/upload")
async def image_pool_media_upload(
    request: Request,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),
    provider: Literal["runninghub", "ican"] = "runninghub",
) -> dict[str, Any]:
    data = await file.read(settings.image_pool_max_upload_bytes + 1)
    if not data or len(data) > settings.image_pool_max_upload_bytes:
        raise ServiceError("IMAGE_UPLOAD_INVALID", "参考图为空或超过大小限制", 422)
    content_type = str(file.content_type or "application/octet-stream")
    if not content_type.startswith("image/"):
        raise ServiceError("IMAGE_UPLOAD_INVALID", "参考图文件类型不受支持", 415)
    if provider == "ican":
        if len(data) > settings.ican_image_max_bytes:
            raise ServiceError("IMAGE_UPLOAD_INVALID", "图片超过大小限制", 413)
        content_type = image_type(data)
        asset_id = new_id("imgasset")
        key = ican_images.save_image(f"reference/{asset_id}.bin", data)
        asset = ImagePoolMedia(id=asset_id, user_id=user.id, provider="ican", upstream_url=key,
                               account_slot=0, content_type=content_type, size_bytes=len(data))
        db.add(asset)
        db.commit()
        return {"code": 0, "data": {"media_id": asset.id, "provider": "ican",
                "download_url": str(request.url_for("image_pool_media", asset_id=asset.id)),
                "original_name": file.filename or "reference.png"}}
    attempted: set[int] = set()
    last_error: ServiceError | None = None
    for _ in settings.runninghub_image_api_keys:
        try:
            state = pick_provider_account_slot(db, provider="runninghub_image")
        except ServiceError as exc:
            db.rollback()
            last_error = exc
            break
        slot = state.slot
        if slot in attempted:
            break
        attempted.add(slot)
        try:
            upstream_url = runninghub_image.upload(
                slot=slot, filename=Path(file.filename or "reference.png").name,
                content_type=content_type, payload=data,
            )
        except (UpstreamAuthError, UpstreamBusyError) as exc:
            mark_provider_account_failure(
                db, provider="runninghub_image", slot=slot,
                error_code=str(exc.details.get("upstream_code") or exc.code),
                permanent=isinstance(exc, UpstreamAuthError),
            )
            db.commit()
            last_error = exc
            continue
        asset = ImagePoolMedia(
            id=new_id("imgasset"), user_id=user.id, upstream_url=upstream_url,
            account_slot=slot, content_type=content_type, size_bytes=len(data),
        )
        db.add(asset)
        mark_provider_account_success(db, provider="runninghub_image", slot=slot)
        db.commit()
        return {
            "code": 0,
            "data": {
                "media_id": asset.id,
                "download_url": str(request.url_for("image_pool_media", asset_id=asset.id)),
                "original_name": file.filename or "reference.png",
            },
        }
    if last_error:
        raise last_error
    raise ServiceError("IMAGE_POOL_UNAVAILABLE", "图像号池暂不可用", 503)


@router.get("/image-pool/media/{asset_id}", name="image_pool_media")
def image_pool_media(asset_id: str, user: CurrentUser, db: Db) -> StreamingResponse:
    asset = db.scalar(select(ImagePoolMedia).where(
        ImagePoolMedia.id == asset_id,
        ImagePoolMedia.user_id == user.id,
    ))
    if not asset:
        raise ServiceError("IMAGE_ASSET_NOT_FOUND", "找不到该参考图", 404)
    if asset.provider == "ican":
        data = ican_images.read_image(asset.upstream_url)
        return StreamingResponse(iter([data]), media_type=image_type(data), headers={"Cache-Control": "private, no-store"})
    chunks, content_type = runninghub_image.stream_result(
        slot=asset.account_slot, url=asset.upstream_url
    )
    return StreamingResponse(chunks, media_type=content_type, headers={"Cache-Control": "private, no-store"})

@router.get("/image-pool/models")
def image_provider_models(user: CurrentUser, provider: Literal["ican"] = "ican") -> dict[str, Any]:
    return {"code": 0, "data": ican_images.catalog()}


@router.post("/image-pool/quote")
def image_provider_quote(payload: ImagePoolGenerateRequest, user: CurrentUser) -> dict[str, Any]:
    if payload.provider == "ican":
        model = payload.model or settings.ican_image_model
        size = (payload.size or "1024x1024").lower().replace("×", "x")
        return {"code": 0, "data": {"provider": "ican", "model": model, "size": size,
                **ican_images.price_quote(model, size)}}
    return {"code": 0, "data": {"provider": "runninghub", "reserved_credits": _credit(image_pool_reserve_credits())}}


@router.get("/admin/users")
def admin_users(admin: CurrentAdmin, db: Db, page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100),
                q: str = Query("", max_length=160),
                source: int | None = Query(None, ge=1, le=2),
                user_status: Literal["active", "disabled", "pending"] | None = None) -> dict[str, Any]:
    filters = []
    if q.strip():
        term = q.strip()
        filters.append(or_(User.email.icontains(term, autoescape=True),
                           User.id.icontains(term, autoescape=True)))
    if source is not None:
        filters.append(User.source == source)
    if user_status is not None:
        filters.append(User.status == user_status)
    rows = db.execute(
        select(User, WalletAccount)
        .outerjoin(WalletAccount, WalletAccount.user_id == User.id)
        .where(*filters)
        .order_by(User.created_at.desc(), User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                **UserPublic.model_validate(user).model_dump(mode="json"),
                "credits": {
                    "available": _credit(wallet.available) if wallet else 0,
                    "reserved": _credit(wallet.reserved) if wallet else 0,
                    "consumed": _credit(wallet.consumed) if wallet else 0,
                },
            }
            for user, wallet in rows
        ],
        "total": db.scalar(select(func.count()).select_from(User).where(*filters)) or 0,
        "page": page, "page_size": page_size,
    }


ADMIN_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _admin_day(db: Session, column):
    # SQLite is used in tests; production timestamps are PostgreSQL timestamptz.
    if db.get_bind().dialect.name == "sqlite":
        return func.date(column, "+8 hours")
    return func.date(func.timezone("Asia/Shanghai", column))


@router.get("/admin/orders")
@router.get("/admin/recharge-orders")
def admin_orders(admin: CurrentAdmin, db: Db, page: int = Query(1, ge=1),
                 page_size: int = Query(20, ge=1, le=100),
                 q: str = Query("", max_length=160),
                 order_status: OrderStatus | None = None,
                 date_from: date | None = None, date_to: date | None = None) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if date_to == date.max:
        raise HTTPException(422, "结束日期超出支持范围")
    filters = []
    if q.strip():
        term = q.strip()
        filters.append(or_(*(column.icontains(term, autoescape=True) for column in (
            RechargeOrder.id, RechargeOrder.user_id, User.email, RechargeOrder.provider_order_id))))
    if order_status is not None:
        filters.append(RechargeOrder.status == order_status.value)
    if date_from:
        filters.append(RechargeOrder.created_at >= datetime.combine(date_from, time.min, ADMIN_TIMEZONE).astimezone(timezone.utc))
    if date_to:
        filters.append(RechargeOrder.created_at < datetime.combine(date_to + timedelta(days=1), time.min, ADMIN_TIMEZONE).astimezone(timezone.utc))
    rows = db.execute(select(RechargeOrder, User.email)
                      .join(User, User.id == RechargeOrder.user_id).where(*filters)
                      .order_by(RechargeOrder.created_at.desc(), RechargeOrder.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    total = db.scalar(select(func.count()).select_from(RechargeOrder)
                      .join(User, User.id == RechargeOrder.user_id).where(*filters)) or 0
    paid_count, paid_fen, paid_credits = db.execute(select(func.count(RechargeOrder.id), func.coalesce(func.sum(RechargeOrder.amount_fen), 0), func.coalesce(func.sum(RechargeOrder.credits), 0))
                                     .join(User, User.id == RechargeOrder.user_id)
                                     .where(*filters, RechargeOrder.status == OrderStatus.PAID.value)).one()
    return {"items": [{"id": order.id, "user_id": order.user_id, "email": email, "user_email": email,
                       "provider": order.provider, "product_id": order.product_id,
                       "amount_fen": order.amount_fen, "amount_cny": round(order.amount_fen / 100, 2),
                       "paid_amount_cny": round(order.amount_fen / 100, 2) if order.status == OrderStatus.PAID.value else 0,
                       "credits": order.credits, "paid_credits": order.credits if order.status == OrderStatus.PAID.value else 0,
                       "status": order.status,
                       "provider_order_id": order.provider_order_id,
                       "created_at": order.created_at, "paid_at": order.paid_at,
                       "expires_at": order.expires_at} for order, email in rows],
            "total": total, "page": page, "page_size": page_size,
            "timezone": "Asia/Shanghai", "date_field": "created_at",
            "summary": {"orders": total, "paid_orders": paid_count, "paid_cny": round(paid_fen / 100, 2),
                         "paid_amount_cny": round(paid_fen / 100, 2), "paid_credits": int(paid_credits or 0)}}


@router.get("/admin/analytics")
def admin_analytics(
    admin: CurrentAdmin,
    db: Db,
    days: int = Query(30, ge=7, le=365),
    granularity: Literal["day", "month"] = Query("day"),
) -> dict[str, Any]:
    """Return registration and settled-recharge trends for the admin dashboard."""
    now = utcnow()
    local_start = (now.astimezone(ADMIN_TIMEZONE) - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = local_start.astimezone(timezone.utc)
    user_day = _admin_day(db, User.created_at)
    paid_day = _admin_day(db, RechargeOrder.paid_at)
    user_rows = db.execute(
        select(user_day, func.count(User.id))
        .where(User.created_at >= period_start, User.created_at <= now)
        .group_by(user_day)
    ).all()
    recharge_rows = db.execute(
        select(
            paid_day,
            func.count(RechargeOrder.id),
            func.coalesce(func.sum(RechargeOrder.amount_fen), 0),
            func.coalesce(func.sum(RechargeOrder.credits), 0),
        )
        .where(
            RechargeOrder.status == OrderStatus.PAID.value,
            RechargeOrder.paid_at.is_not(None),
            RechargeOrder.paid_at >= period_start,
            RechargeOrder.paid_at <= now,
        )
        .group_by(paid_day)
    ).all()

    def day_key(value: Any) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    user_by_day = {day_key(day): int(count or 0) for day, count in user_rows}
    recharge_by_day = {
        day_key(day): {
            "orders": int(orders or 0), "amount_fen": int(amount_fen or 0),
            "credits": float(credits or 0),
        }
        for day, orders, amount_fen, credits in recharge_rows
    }
    buckets: dict[str, dict[str, Any]] = {}
    for index in range(days):
        current = (local_start + timedelta(days=index)).date()
        key = current.isoformat() if granularity == "day" else current.strftime("%Y-%m")
        bucket = buckets.setdefault(key, {"period": key, "new_users": 0, "paid_orders": 0, "recharge_cny": 0.0, "recharge_credits": 0.0})
        daily_users = user_by_day.get(current.isoformat(), 0)
        daily_recharge = recharge_by_day.get(current.isoformat(), {})
        bucket["new_users"] += daily_users
        bucket["paid_orders"] += daily_recharge.get("orders", 0)
        bucket["recharge_cny"] += daily_recharge.get("amount_fen", 0) / 100
        bucket["recharge_credits"] += daily_recharge.get("credits", 0)
    series = list(buckets.values())
    for item in series:
        item["recharge_cny"] = round(item["recharge_cny"], 2)
        item["recharge_credits"] = round(item["recharge_credits"], 6)
    total_available = db.scalar(select(func.coalesce(func.sum(WalletAccount.available), 0))) or 0
    return {
        "days": days,
        "timezone": "Asia/Shanghai",
        "start_date": local_start.date().isoformat(),
        "end_date": now.astimezone(ADMIN_TIMEZONE).date().isoformat(),
        "granularity": granularity,
        "series": series,
        "summary": {
            "total_users": db.scalar(select(func.count()).select_from(User)) or 0,
            "new_users": sum(item["new_users"] for item in series),
            "paid_orders": sum(item["paid_orders"] for item in series),
            "recharge_cny": round(sum(item["recharge_cny"] for item in series), 2),
            "recharge_credits": round(sum(item["recharge_credits"] for item in series), 6),
            "available_credits": _credit(total_available),
        },
    }

@router.post("/admin/users/{user_id}/reset-password")
def reset_user_password(user_id: str, admin: CurrentAdmin, db: Db) -> dict[str, bool]:
    if user_id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "不能在此重置当前登录的管理员账户")
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.password_hash = hash_admin_reset_password()
    user.auth_version += 1
    db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None)).update({"revoked_at": utcnow()})
    audit(db, admin, "user.password_reset", "user", user_id, {"sessions_revoked": True})
    db.commit()
    return {"ok": True}


@router.post("/admin/users/{user_id}/disable")
def disable_user(user_id: str, admin: CurrentAdmin, db: Db) -> dict[str, bool]:
    if user_id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "不能停用当前登录的管理员账户")
    user = db.get(User, user_id)
    if not user: raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.status = UserStatus.DISABLED.value
    db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None)).update({"revoked_at": utcnow()})
    audit(db, admin, "user.disable", "user", user_id, {"sessions_revoked": True})
    db.commit()
    return {"ok": True}


@router.post("/admin/users/{user_id}/enable")
def enable_user(user_id: str, admin: CurrentAdmin, db: Db) -> dict[str, bool]:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.status != UserStatus.DISABLED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "仅可恢复已停用账户")
    user.status = UserStatus.ACTIVE.value
    audit(db, admin, "user.enable", "user", user_id, {})
    db.commit()
    return {"ok": True}

@router.post("/admin/users/{user_id}/quota")
def update_quota(user_id: str, payload: QuotaUpdateRequest, admin: CurrentAdmin, db: Db) -> dict[str, Any]:
    if not db.get(User, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    quota = db.get(QuotaPolicy, user_id)
    if not quota:
        quota = QuotaPolicy(
            user_id=user_id,
            max_concurrent_jobs=settings.default_max_concurrent_jobs,
            daily_characters_limit=settings.default_daily_characters,
            max_queue_jobs=settings.default_max_queue_jobs,
        )
        db.add(quota)
    for key, value in payload.model_dump().items(): setattr(quota, key, value)
    audit(db, admin, "quota.update", "user", user_id, payload.model_dump()); db.commit(); return payload.model_dump()


@router.get("/admin/users/{user_id}/quota")
def admin_user_quota(user_id: str, admin: CurrentAdmin, db: Db) -> dict[str, int]:
    if not db.get(User, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    quota = db.get(QuotaPolicy, user_id)
    return {
        "max_concurrent_jobs": quota.max_concurrent_jobs if quota else settings.default_max_concurrent_jobs,
        "daily_characters_limit": quota.daily_characters_limit if quota else settings.default_daily_characters,
        "max_queue_jobs": quota.max_queue_jobs if quota else settings.default_max_queue_jobs,
    }

@router.post("/admin/users/{user_id}/credits/adjust")
def adjust_credits(user_id: str, payload: CreditAdjustRequest, admin: CurrentAdmin, db: Db) -> dict[str, Any]:
    if not db.get(User, user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    wallet = wallet_for_update(db, user_id)
    key = f"admin:{admin.id}:{payload.idempotency_key}"
    prior = db.scalar(select(WalletLedger).where(WalletLedger.idempotency_key == key))
    if prior:
        if prior.user_id != user_id or prior.available_delta != payload.amount or prior.note != payload.reason:
            raise HTTPException(status.HTTP_409_CONFLICT, "幂等键已用于不同的积分调整")
        return {"ledger_id": prior.id, "available": _credit(prior.available_after)}
    entry = apply_wallet_entry(db, wallet=wallet, entry_type="admin_adjust", available_delta=payload.amount, reserved_delta=0, consumed_delta=0, reference_type="admin", reference_id=admin.id, idempotency_key=f"admin:{admin.id}:{payload.idempotency_key}", note=payload.reason)
    audit(db, admin, "credits.adjust", "user", user_id, {"amount": str(payload.amount), "reason": payload.reason, "ledger_id": entry.id}); db.commit(); return {"ledger_id": entry.id, "available": _credit(wallet.available)}


def _enrollment_request(method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
    """Call the .3 enrollment control plane without exposing its admin secret."""
    if not settings.enrollment_admin_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Enrollment 控制面尚未配置")
    try:
        with httpx.Client(timeout=settings.enrollment_request_timeout_seconds) as client:
            response = client.request(
                method,
                f"{settings.enrollment_api_base_url}{path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {settings.enrollment_admin_token}",
                },
                json=payload,
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Enrollment 控制面暂时不可达") from exc
    try:
        data = response.json()
    except ValueError:
        data = {"detail": "Enrollment 控制面返回了无效响应"}
    if response.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise HTTPException(response.status_code if response.status_code < 500 else status.HTTP_502_BAD_GATEWAY, detail or "Enrollment 控制面请求失败")
    return data


def _enrollment_read(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Read the enrollment DB through the local tunnel with a read-only transaction."""
    if not settings.enrollment_database_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Enrollment 数据库只读通道尚未配置")
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(settings.enrollment_database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '3000ms'")
                cursor.execute(query, params)
                return list(cursor.fetchall())
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Enrollment 数据库暂时不可读") from exc


def _connectivity(last_seen_at: Any) -> tuple[str, str, int | None]:
    """Translate the enrollment heartbeat timestamp into an honest status.

    A missing timestamp means the node has never reported a heartbeat; it is
    deliberately different from an offline/stale node.  This prevents the
    admin UI from presenting an unknown value as if it were a live status.
    """
    if last_seen_at is None:
        return "not_reported", "节点尚未上报 heartbeat", None
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    value = last_seen_at
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    age = max(0, int((now - value).total_seconds()))
    if age <= 90:
        return "online", "最近 90 秒内有 heartbeat", age
    return "stale", "最后 heartbeat 已超过 90 秒", age


def _ray_node_connected(ray_node_ip: str | None) -> tuple[bool | None, str | None]:
    """Ask the private Ray status probe whether this peer is in the live Ray set."""
    if not ray_node_ip:
        return None, None
    try:
        # PostgreSQL renders the inet column as ``10.250.0.x/32`` while the
        # status probe expects a plain host address.
        probe_ip = str(ray_node_ip).split("/", 1)[0]
        with httpx.Client(timeout=2.5) as client:
            response = client.get(
                f"{settings.ray_cluster_status_url}/v1/status",
                params={"node_ip": probe_ip},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("available") is not True:
            return None, "Ray 状态探针不可用"
        connected = bool(payload.get("current_node_connected"))
        return connected, "Ray 节点已连接" if connected else "Ray 节点未出现在存活列表"
    except (httpx.HTTPError, ValueError, TypeError):
        return None, "Ray 状态探针不可用"


def _decorate_enrollment_device(row: dict[str, Any]) -> dict[str, Any]:
    status_value, reason, age = _connectivity(row.get("last_seen_at"))
    ray_connected, ray_reason = _ray_node_connected(row.get("ray_node_ip"))
    # Heartbeat is the authoritative node-agent signal.  When it has not been
    # implemented on a node yet, a live Ray membership is still a reliable
    # transport/service signal and should not be shown as "unknown".
    if ray_connected is True:
        status_value = "online"
        reason = f"{ray_reason}；{reason}"
    elif status_value == "not_reported" and ray_connected is False:
        reason = f"{reason}；{ray_reason}"
    row["connectivity_status"] = status_value
    row["connectivity_reason"] = reason
    row["heartbeat_age_seconds"] = age
    row["ray_connected"] = ray_connected
    row["ray_connectivity_reason"] = ray_reason
    row["inventory_status"] = "not_reported"
    row["inventory_reason"] = "节点尚未上报镜像/模型库存"
    row["images"] = []
    row["models"] = []
    return row


def _enrollment_devices(limit: int = 100) -> list[dict[str, Any]]:
    rows = _enrollment_read(
        """SELECT d.device_id, d.generation, d.state::text AS lifecycle_state,
                  d.profile_id, d.ray_node_ip::text AS ray_node_ip,
                  d.desired_peer_enabled, d.desired_revision, d.applied_revision,
                  d.last_seen_at, d.created_at, d.updated_at,
                  p.display_name AS profile_name, p.ray_resources,
                  CASE WHEN d.applied_revision = d.desired_revision THEN 'applied' ELSE 'pending' END AS peer_status
           FROM enrollment_devices d
           JOIN enrollment_profiles p ON p.id = d.profile_id
           ORDER BY d.updated_at DESC LIMIT %s""",
        (limit,),
    )
    return [_decorate_enrollment_device(row) for row in rows]


def _enrollment_next_generation(device_id: str) -> int:
    rows = _enrollment_read(
        "SELECT generation FROM enrollment_devices WHERE device_id=%s",
        (device_id,),
    )
    return 1 if not rows else int(rows[0]["generation"]) + 1


def _enrollment_device(device_id: str) -> dict[str, Any]:
    rows = _enrollment_read(
        """SELECT d.device_id, d.generation, d.state::text AS lifecycle_state,
                  d.profile_id, d.ray_node_ip::text AS ray_node_ip,
                  d.desired_peer_enabled, d.desired_revision, d.applied_revision,
                  d.last_seen_at, d.created_at, d.updated_at,
                  p.display_name AS profile_name, p.ray_resources,
                  CASE WHEN d.applied_revision = d.desired_revision THEN 'applied' ELSE 'pending' END AS peer_status,
                  (SELECT max(expires_at) FROM enrollment_device_credentials c
                   WHERE c.device_id=d.device_id AND c.revoked_at IS NULL) AS credential_expires_at,
                  (SELECT max(created_at) FROM wireguard_device_keys k
                   WHERE k.device_id=d.device_id AND k.retired_at IS NULL) AS key_created_at
           FROM enrollment_devices d
           JOIN enrollment_profiles p ON p.id = d.profile_id
           WHERE d.device_id = %s""",
        (device_id,),
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    row = rows[0]
    return _decorate_enrollment_device(row)


def _enrollment_model_releases() -> list[dict[str, Any]]:
    return _enrollment_read(
        "SELECT model_id, version, enabled, published_at FROM enrollment_model_releases "
        "ORDER BY published_at DESC, model_id, version"
    )


def _set_model_release(model_id: str, version: str, enabled: bool,
                       payload: EnrollmentAdminActionRequest, admin: User) -> dict[str, Any]:
    if not settings.enrollment_database_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Enrollment 数据库控制通道尚未配置")
    digest = bytes.fromhex(json_hash({"model_id": model_id, "version": version,
                                      "enabled": enabled, "reason": payload.reason}))
    operation = f"model-release:{'enable' if enabled else 'disable'}:{model_id}:{version}"
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(settings.enrollment_database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5000ms'")
                cursor.execute("SELECT request_digest, response_json FROM enrollment_idempotency "
                               "WHERE principal_id=%s AND operation=%s AND idempotency_key=%s",
                               (admin.id, operation, payload.idempotency_key))
                prior = cursor.fetchone()
                if prior:
                    if bytes(prior["request_digest"]) != digest:
                        raise HTTPException(status.HTTP_409_CONFLICT, "幂等键已用于不同请求")
                    return prior["response_json"]
                cursor.execute("SELECT model_id, version, enabled, published_at FROM enrollment_model_releases "
                               "WHERE model_id=%s AND version=%s FOR UPDATE", (model_id, version))
                release = cursor.fetchone()
                if not release:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "Model release not found")
                cursor.execute("UPDATE enrollment_model_releases SET enabled=%s WHERE model_id=%s AND version=%s "
                               "RETURNING model_id, version, enabled, published_at", (enabled, model_id, version))
                result = dict(cursor.fetchone())
                result["action"] = "enabled" if enabled else "disabled"
                result["reason"] = payload.reason
                response_json = json.dumps(result, default=str)
                cursor.execute("INSERT INTO enrollment_idempotency "
                               "(principal_id, operation, idempotency_key, request_digest, response_status, response_json, expires_at) "
                               "VALUES (%s,%s,%s,%s,200,%s::jsonb,now()+interval '30 days')",
                               (admin.id, operation, payload.idempotency_key, digest, response_json))
                cursor.execute("INSERT INTO enrollment_audit_log "
                               "(actor_type, actor_id, action, device_id, generation, request_id, outcome, details) "
                               "VALUES ('admin',%s,%s,NULL,NULL,%s,'success',%s::jsonb)",
                               (admin.id, "MODEL_RELEASE_ENABLED" if enabled else "MODEL_RELEASE_DISABLED",
                                payload.idempotency_key, json.dumps({"model_id": model_id, "version": version,
                                                                      "reason": payload.reason})))
                return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Enrollment 数据库控制通道暂时不可用") from exc


def _prior_admin_action(db: Session, admin: User, action: str, target_id: str,
                        idempotency_key: str) -> dict[str, Any] | None:
    """Return a previously committed response for a retried mutating action."""
    rows = db.scalars(
        select(AdminAuditLog)
        .where(AdminAuditLog.admin_user_id == admin.id,
               AdminAuditLog.action == action,
               AdminAuditLog.target_id == target_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(100)
    ).all()
    for row in rows:
        details = row.details if isinstance(row.details, dict) else {}
        if details.get("idempotency_key") == idempotency_key and isinstance(details.get("response"), dict):
            return details["response"]
    return None


@router.get("/admin/enrollment/profiles")
def admin_enrollment_profiles(admin: CurrentAdmin) -> Any:
    return _enrollment_request("GET", "/ray-worker/api/enrollment/v1/profiles")


@router.post("/admin/enrollment/tokens", status_code=status.HTTP_201_CREATED)
def admin_enrollment_token(payload: EnrollmentTokenRequest, admin: CurrentAdmin) -> Any:
    request_payload = payload.model_dump(exclude_none=True)
    if payload.device_id:
        request_payload["expected_generation"] = _enrollment_next_generation(payload.device_id)
    return _enrollment_request(
        "POST",
        "/ray-worker/api/enrollment/v1/admin/tokens",
        payload=request_payload,
    )


@router.get("/admin/cluster/devices")
def admin_cluster_devices(admin: CurrentAdmin, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    items = _enrollment_devices(limit)
    return {"items": items, "total": len(items), "inventory_note": "节点尚未上报镜像/模型库存"}


@router.get("/admin/cluster/devices/{device_id}")
def admin_cluster_device(device_id: str, admin: CurrentAdmin) -> dict[str, Any]:
    return _enrollment_device(device_id)


@router.post("/admin/cluster/devices/{device_id}/disable", status_code=status.HTTP_202_ACCEPTED)
def admin_cluster_device_disable(device_id: str, payload: EnrollmentAdminActionRequest,
                                 admin: CurrentAdmin, db: Db) -> Any:
    action = "cluster.device.disable"
    prior = _prior_admin_action(db, admin, action, device_id, payload.idempotency_key)
    if prior is not None:
        return prior
    result = _enrollment_request(
        "POST", f"/ray-worker/api/enrollment/v1/admin/devices/{device_id}/disable",
        payload={"reason": payload.reason},
    )
    audit(db, admin, action, "enrollment_device", device_id,
          {"reason": payload.reason, "idempotency_key": payload.idempotency_key, "response": result})
    db.commit()
    return result


@router.post("/admin/cluster/devices/{device_id}/rekey-token", status_code=status.HTTP_201_CREATED)
def admin_cluster_device_rekey(device_id: str, payload: EnrollmentAdminActionRequest,
                               admin: CurrentAdmin, db: Db) -> Any:
    action = "cluster.device.rekey_token"
    prior = _prior_admin_action(db, admin, action, device_id, payload.idempotency_key)
    if prior is not None:
        return prior
    result = _enrollment_request(
        "POST", f"/ray-worker/api/enrollment/v1/admin/devices/{device_id}/rekey-token",
    )
    audit(db, admin, action, "enrollment_device", device_id,
          {"reason": payload.reason, "idempotency_key": payload.idempotency_key, "response": result})
    db.commit()
    return result


@router.get("/admin/cluster/model-releases")
def admin_cluster_model_releases(admin: CurrentAdmin) -> dict[str, Any]:
    return {"items": _enrollment_model_releases()}


@router.post("/admin/cluster/model-releases/{model_id}/{version}/enable")
def admin_enable_model_release(model_id: str, version: str, payload: EnrollmentAdminActionRequest,
                               admin: CurrentAdmin, db: Db) -> dict[str, Any]:
    result = _set_model_release(model_id, version, True, payload, admin)
    audit(db, admin, "cluster.model_release.enable", "model_release", f"{model_id}:{version}",
          {"reason": payload.reason, "idempotency_key": payload.idempotency_key})
    db.commit()
    return result


@router.post("/admin/cluster/model-releases/{model_id}/{version}/disable")
def admin_disable_model_release(model_id: str, version: str, payload: EnrollmentAdminActionRequest,
                                admin: CurrentAdmin, db: Db) -> dict[str, Any]:
    result = _set_model_release(model_id, version, False, payload, admin)
    audit(db, admin, "cluster.model_release.disable", "model_release", f"{model_id}:{version}",
          {"reason": payload.reason, "idempotency_key": payload.idempotency_key})
    db.commit()
    return result


@router.patch("/admin/cluster/devices/{device_id}/network")
def admin_cluster_device_network_placeholder(device_id: str, admin: CurrentAdmin) -> Any:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "IP 修改功能预留，当前未实现")


@router.patch("/admin/cluster/devices/{device_id}/profile")
def admin_cluster_device_profile_placeholder(device_id: str, admin: CurrentAdmin) -> Any:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Profile 修改功能预留，当前未实现")

@router.get("/admin/cluster/summary")
def cluster_summary(admin: CurrentAdmin) -> dict[str, Any]:
    try:
        ray = ray_tts.health()
        return {"ok": ray.get("ok") is True, "control_api": True, "ray": ray}
    except Exception:
        return {"ok": False, "control_api": True, "ray": {}, "error": "Ray 服务暂不可达"}

@router.get("/admin/cloud/jobs")
def admin_jobs(admin: CurrentAdmin, db: Db, page: int = Query(1, ge=1),
               page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=160),
               job_status: JobStatus | None = None) -> dict[str, Any]:
    filters = []
    if q.strip():
        filters.append(or_(CloudJob.id.icontains(q.strip(), autoescape=True),
                           CloudJob.user_id.icontains(q.strip(), autoescape=True)))
    if job_status is not None:
        filters.append(CloudJob.status == job_status.value)
    items = db.scalars(select(CloudJob).where(*filters).order_by(CloudJob.created_at.desc(), CloudJob.id)
                       .offset((page-1)*page_size).limit(page_size)).all()
    return {"items": [{**_job(j), "user_id": j.user_id} for j in items],
            "total": db.scalar(select(func.count()).select_from(CloudJob).where(*filters)) or 0,
            "page": page, "page_size": page_size}


def _admin_job_public(job: Any, email: str, kind: str) -> dict[str, Any]:
    """Normalize TTS and video jobs for the read-only operations console."""
    payload = _job(job) if kind == "tts" else _video_job(job)
    if kind == "video" and isinstance(payload.get("steps"), dict):
        payload["steps"] = [dict(value, step_key=key) for key, value in payload["steps"].items()]
    if kind == "tts":
        payload["credits"] = {
            "reserved": payload.pop("reserved_credits", 0),
            "consumed": payload.pop("consumed_credits", 0),
            "released": payload.pop("released_credits", 0),
        }
        payload["stage"] = "tts"
        if isinstance(payload.get("error"), str):
            payload["error"] = {"message": payload["error"]}
    payload["kind"] = kind
    payload["user_email"] = email
    return payload


@router.get("/admin/jobs")
def admin_jobs_unified(admin: CurrentAdmin, db: Db, page: int = Query(1, ge=1),
                       page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=160),
                       kind: Literal["tts", "video"] = "tts",
                       job_status: str | None = None) -> dict[str, Any]:
    model = CloudJob if kind == "tts" else VideoJob
    filters = []
    if q.strip():
        term = q.strip()
        filters.append(or_(model.id.icontains(term, autoescape=True),
                           model.user_id.icontains(term, autoescape=True),
                           User.email.icontains(term, autoescape=True)))
    if job_status:
        filters.append(model.status == job_status)
    rows = db.execute(select(model, User.email).join(User, User.id == model.user_id)
                      .where(*filters).order_by(model.created_at.desc(), model.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    total = db.scalar(select(func.count()).select_from(model).join(User, User.id == model.user_id).where(*filters)) or 0
    return {"kind": kind, "items": [_admin_job_public(job, email, kind) for job, email in rows],
            "total": total, "page": page, "page_size": page_size}


@router.get("/admin/jobs/{kind}/{job_id}")
def admin_job_detail(kind: Literal["tts", "video"], job_id: str, admin: CurrentAdmin, db: Db) -> dict[str, Any]:
    if kind == "tts":
        row = db.execute(select(CloudJob, User.email).join(User, User.id == CloudJob.user_id)
                         .where(CloudJob.id == job_id)).first()
    else:
        row = db.execute(select(VideoJob, User.email).join(User, User.id == VideoJob.user_id)
                         .where(VideoJob.id == job_id)).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return _admin_job_public(row[0], row[1], kind)

@router.get("/admin/audit-logs")
def audit_logs(admin: CurrentAdmin, db: Db, page: int = Query(1, ge=1),
               page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=160)) -> dict[str, Any]:
    filters = []
    if q.strip():
        filters.append(or_(AdminAuditLog.action.icontains(q.strip(), autoescape=True),
                           AdminAuditLog.target_id.icontains(q.strip(), autoescape=True)))
    items = db.scalars(select(AdminAuditLog).where(*filters)
                       .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id)
                       .offset((page-1)*page_size).limit(page_size)).all()
    return {"items": [{"id": i.id, "admin_user_id": i.admin_user_id, "action": i.action,
                       "target_type": i.target_type, "target_id": i.target_id,
                       "details": _redact_admin_details(i.details), "created_at": i.created_at} for i in items],
            "total": db.scalar(select(func.count()).select_from(AdminAuditLog).where(*filters)) or 0,
            "page": page, "page_size": page_size}


def _redact_admin_details(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[redacted]" if any(part in key.lower() for part in ("token", "password", "credential", "secret"))
                else _redact_admin_details(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_admin_details(item) for item in value]
    return value
