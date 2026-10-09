from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .exceptions import ServiceError
from .models import (
    AdminAuditLog,
    CloudJob,
    DailyUsage,
    ImagePoolJob,
    ImagePoolMedia,
    ModelPoolJob,
    JobStatus,
    QuotaPolicy,
    ProviderAccountState,
    RechargeOrder,
    User,
    VideoAsset,
    VideoJob,
    VideoJobStatus,
    VideoJobStep,
    VideoStepStatus,
    WalletAccount,
    WalletLedger,
    new_id,
    utcnow,
)


ACTIVE_JOB_STATES = {
    JobStatus.CREATED.value,
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.FINALIZING.value,
    JobStatus.CANCEL_REQUESTED.value,
}

VIDEO_STEP_KEYS = ("storyboard", "tts", "images", "compose", "publish")
VIDEO_STEP_JOB_STATES = {
    "storyboard": VideoJobStatus.STORYBOARDING.value,
    "tts": VideoJobStatus.GENERATING_ASSETS.value,
    "images": VideoJobStatus.GENERATING_ASSETS.value,
    "compose": VideoJobStatus.COMPOSING.value,
    "publish": VideoJobStatus.PUBLISHING.value,
}
ACTIVE_VIDEO_JOB_STATES = {
    VideoJobStatus.QUEUED.value,
    VideoJobStatus.STORYBOARDING.value,
    VideoJobStatus.GENERATING_ASSETS.value,
    VideoJobStatus.COMPOSING.value,
    VideoJobStatus.PUBLISHING.value,
    VideoJobStatus.CANCEL_REQUESTED.value,
}


PRODUCTS: dict[str, tuple[int, int]] = {
    "credits_1": (100, 1),
    "credits_5": (500, 5),
    "credits_10": (1_000, 10),
    "credits_20": (2_000, 20),
    "credits_30": (3_000, 30),
    "credits_50": (5_000, 50),
    "credits_100": (10_000, 100),
}


def quote_credits(total_characters: int) -> Decimal:
    if total_characters <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty task")
    return (
        Decimal(math.ceil(total_characters / 200))
        * settings.tts_credits_per_200_chars
    ).quantize(Decimal("0.1"))


def json_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


CREDIT_QUANTUM = Decimal("0.000001")


def _with_markup(value: Decimal, percent: int) -> Decimal:
    if value < 0:
        raise ValueError("value must not be negative")
    multiplier = Decimal(100 + percent) / Decimal(100)
    return (value * multiplier).quantize(CREDIT_QUANTUM, rounding=ROUND_CEILING)


def usd_to_credits(value: Decimal, *, markup_percent: int) -> Decimal:
    return _with_markup(value * settings.billing_usd_to_cny_rate, markup_percent)


def wallet_for_update(db: Session, user_id: str) -> WalletAccount:
    wallet = db.scalar(
        select(WalletAccount)
        .where(WalletAccount.user_id == user_id)
        .with_for_update()
    )
    if wallet is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Wallet not initialized")
    return wallet


def _provider_account_state(
    db: Session,
    *,
    provider: str,
    slot: int,
) -> ProviderAccountState:
    state = db.scalar(
        select(ProviderAccountState).where(
            ProviderAccountState.provider == provider,
            ProviderAccountState.slot == slot,
        )
    )
    if state is None:
        state = ProviderAccountState(
            id=new_id("pas"),
            provider=provider,
            slot=slot,
            failure_count=0,
            permanently_disabled=False,
        )
        db.add(state)
        db.flush()
    return state


def upsert_provider_account_states(db: Session) -> None:
    for provider, count in {
        "model_pool": max(1, len(settings.model_pool_api_keys)),
        "runninghub_image": max(1, len(settings.runninghub_image_api_keys)),
    }.items():
        for slot in range(count):
            state = _provider_account_state(db, provider=provider, slot=slot)
            # Older workers permanently disabled RunningHub slots after a
            # balance error. A recharge would never make those slots usable
            # again, so migrate that legacy state to a bounded cooldown.
            if (
                provider == "runninghub_image"
                and state.permanently_disabled
                and state.last_error_code == "414"
            ):
                state.permanently_disabled = False
                state.disabled_until = utcnow() + timedelta(
                    seconds=settings.provider_account_cooldown_seconds
                )


def pick_provider_account_slot(db: Session, *, provider: str) -> ProviderAccountState:
    upsert_provider_account_states(db)
    now = utcnow()
    states = db.scalars(
        select(ProviderAccountState)
        .where(
            ProviderAccountState.provider == provider,
            ProviderAccountState.permanently_disabled.is_(False),
        )
        .order_by(ProviderAccountState.failure_count.asc(), ProviderAccountState.updated_at.asc())
        .with_for_update(skip_locked=True)
    ).all()
    for state in states:
        if state.disabled_until and _aware_utc(state.disabled_until) > now:
            continue
        if provider == "model_pool":
            active = db.scalar(select(func.count()).select_from(ModelPoolJob).where(
                ModelPoolJob.account_slot == state.slot,
                ModelPoolJob.status == "running",
            )) or 0
            if active >= settings.model_pool_per_account_concurrency:
                continue
        return state
    raise ServiceError(
        "PROVIDER_POOL_EXHAUSTED",
        "Provider account pool is temporarily exhausted",
        503,
        {"provider": provider},
    )


def provider_pool_available(db: Session, *, provider: str) -> bool:
    """Return whether at least one configured account can be selected now."""
    upsert_provider_account_states(db)
    now = utcnow()
    states = db.scalars(
        select(ProviderAccountState).where(
            ProviderAccountState.provider == provider,
            ProviderAccountState.permanently_disabled.is_(False),
        )
    ).all()
    return any(
        state.disabled_until is None or _aware_utc(state.disabled_until) <= now
        for state in states
    )


def mark_provider_account_failure(
    db: Session,
    *,
    provider: str,
    slot: int,
    error_code: str | None,
    permanent: bool = False,
) -> None:
    state = _provider_account_state(db, provider=provider, slot=slot)
    state.failure_count += 1
    state.last_error_code = error_code
    if permanent:
        state.permanently_disabled = True
        state.disabled_until = None
    else:
        state.disabled_until = utcnow() + timedelta(seconds=settings.provider_account_cooldown_seconds)


def mark_provider_account_success(db: Session, *, provider: str, slot: int) -> None:
    state = _provider_account_state(db, provider=provider, slot=slot)
    state.failure_count = 0
    state.disabled_until = None
    state.last_error_code = None


def apply_wallet_entry(
    db: Session,
    *,
    wallet: WalletAccount,
    entry_type: str,
    available_delta: Decimal | int,
    reserved_delta: Decimal | int,
    consumed_delta: Decimal | int,
    reference_type: str,
    reference_id: str,
    idempotency_key: str,
    note: str | None = None,
) -> WalletLedger:
    existing = db.scalar(
        select(WalletLedger).where(
            WalletLedger.idempotency_key == idempotency_key
        )
    )
    if existing:
        return existing
    next_available = wallet.available + available_delta
    next_reserved = wallet.reserved + reserved_delta
    next_consumed = wallet.consumed + consumed_delta
    if min(next_available, next_reserved, next_consumed) < 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Insufficient credits")
    wallet.available = next_available
    wallet.reserved = next_reserved
    wallet.consumed = next_consumed
    entry = WalletLedger(
        id=new_id("led"),
        user_id=wallet.user_id,
        entry_type=entry_type,
        available_delta=available_delta,
        reserved_delta=reserved_delta,
        consumed_delta=consumed_delta,
        available_after=next_available,
        reserved_after=next_reserved,
        consumed_after=next_consumed,
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        note=note,
    )
    db.add(entry)
    return entry


def reserve_job_credits(db: Session, job: CloudJob) -> None:
    wallet = wallet_for_update(db, job.user_id)
    apply_wallet_entry(
        db,
        wallet=wallet,
        entry_type="reserve",
        available_delta=-job.reserved_credits,
        reserved_delta=job.reserved_credits,
        consumed_delta=0,
        reference_type="cloud_job",
        reference_id=job.id,
        idempotency_key=f"job:{job.id}:reserve",
    )


def settle_job_credits(db: Session, job: CloudJob, actual: int) -> None:
    if job.consumed_credits or job.released_credits:
        return
    actual = max(0, min(actual, job.reserved_credits))
    release = job.reserved_credits - actual
    wallet = wallet_for_update(db, job.user_id)
    if actual:
        apply_wallet_entry(
            db,
            wallet=wallet,
            entry_type="consume",
            available_delta=0,
            reserved_delta=-actual,
            consumed_delta=actual,
            reference_type="cloud_job",
            reference_id=job.id,
            idempotency_key=f"job:{job.id}:consume",
        )
    if release:
        apply_wallet_entry(
            db,
            wallet=wallet,
            entry_type="release",
            available_delta=release,
            reserved_delta=-release,
            consumed_delta=0,
            reference_type="cloud_job",
            reference_id=job.id,
            idempotency_key=f"job:{job.id}:release",
        )
    job.consumed_credits = actual
    job.released_credits = release


def release_job_credits(db: Session, job: CloudJob) -> None:
    settle_job_credits(db, job, 0)


def video_job_quote(request_json: dict[str, Any]) -> dict[str, Any]:
    script_length = len(str(request_json["script"]))
    scene_count = int(request_json["video"]["scene_count"])
    storyboard = 0
    tts = quote_credits(script_length)
    video = request_json["video"]
    if video.get("method") == "ican":
        from .ican_images import price_quote
        images = scene_count * Decimal(price_quote("gpt-image-2.5", video["size"])["credits"])
    else:
        images = scene_count * image_pool_reserve_credits()
    breakdown = {
        "storyboard": storyboard,
        "tts": tts,
        "images": images,
        "compose": 0,
        "publish": 0,
    }
    return {"reserved_credits": sum(breakdown.values()), "breakdown": breakdown}


def reserve_video_job_credits(db: Session, job: VideoJob) -> None:
    wallet = wallet_for_update(db, job.user_id)
    apply_wallet_entry(
        db,
        wallet=wallet,
        entry_type="reserve_video_job",
        available_delta=-job.reserved_credits,
        reserved_delta=job.reserved_credits,
        consumed_delta=0,
        reference_type="video_job",
        reference_id=job.id,
        idempotency_key=f"video:{job.id}:reserve",
        note="One-click video parent reservation; child steps must not bill",
    )


def settle_video_job_credits(db: Session, job: VideoJob, actual_credits: Decimal) -> None:
    if job.consumed_credits or job.released_credits:
        return
    actual = max(0, min(actual_credits, job.reserved_credits))
    release = job.reserved_credits - actual
    wallet = wallet_for_update(db, job.user_id)
    if actual:
        apply_wallet_entry(
            db, wallet=wallet, entry_type="consume_video_job",
            available_delta=0, reserved_delta=-actual, consumed_delta=actual,
            reference_type="video_job", reference_id=job.id,
            idempotency_key=f"video:{job.id}:consume",
        )
    if release:
        apply_wallet_entry(
            db, wallet=wallet, entry_type="release_video_job",
            available_delta=release, reserved_delta=-release, consumed_delta=0,
            reference_type="video_job", reference_id=job.id,
            idempotency_key=f"video:{job.id}:release",
        )
    job.consumed_credits = actual
    job.released_credits = release


def release_video_job_credits(db: Session, job: VideoJob) -> None:
    settle_video_job_credits(db, job, 0)


def cancel_video_children(db: Session, job: VideoJob) -> None:
    """Stop zero-billed children and make them ineligible for worker claims."""
    now = utcnow()
    for child in db.scalars(select(ModelPoolJob).where(
        ModelPoolJob.parent_video_job_id == job.id,
        ModelPoolJob.status == "running",
    ).with_for_update()).all():
        child.status = "failed"
        child.error_code = "PARENT_VIDEO_CANCELLED"
        child.response_json = {"error": {
            "code": child.error_code, "message": "Parent video job was cancelled",
        }}
        child.finished_at = now

    for child in db.scalars(select(CloudJob).where(
        CloudJob.parent_video_job_id == job.id,
        CloudJob.status.in_(ACTIVE_JOB_STATES),
    ).with_for_update()).all():
        child.cancel_requested = True
        if child.ray_job_id:
            child.status = JobStatus.CANCEL_REQUESTED.value
            child.message = "Cancellation requested by parent video job"
        else:
            child.status = JobStatus.CANCELLED.value
            child.message = "Cancelled before submission by parent video job"
            child.finished_at = now
            child.lease_expires_at = None

    for child in db.scalars(select(ImagePoolJob).where(
        ImagePoolJob.parent_video_job_id == job.id,
        ImagePoolJob.status.in_(["queued", "submitting", "running"]),
    ).with_for_update()).all():
        child.status = "failed"
        child.error_code = "PARENT_VIDEO_CANCELLED"
        child.error_message = "Parent video job was cancelled"
        child.finished_at = now
        child.lease_expires_at = None


def persist_video_step_assets(
    db: Session, *, job: VideoJob, step: VideoJobStep, output: dict[str, Any]
) -> None:
    """Upsert real orchestrator assets without invoking any child billing path."""
    assets = output.get("assets")
    if not isinstance(assets, list):
        return
    for position, raw in enumerate(assets):
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "unknown")[:32]
        ordinal = int(raw.get("ordinal", position))
        asset_key = str(raw.get("asset_key") or f"{step.step_key}:{kind}:{ordinal}")[:160]
        asset = db.scalar(select(VideoAsset).where(
            VideoAsset.video_job_id == job.id, VideoAsset.asset_key == asset_key,
        ))
        if asset is None:
            asset = VideoAsset(
                id=new_id("vasset"), video_job_id=job.id, step_id=step.id,
                asset_key=asset_key, kind=kind, ordinal=ordinal,
            )
            db.add(asset)
        asset.status = str(raw.get("status") or "ready")[:32]
        asset.provider = str(raw["provider"])[:64] if raw.get("provider") else None
        asset.upstream_id = str(raw["upstream_id"])[:180] if raw.get("upstream_id") else None
        asset.uri = str(raw["uri"]) if raw.get("uri") else None
        asset.metadata_json = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}


def create_video_job(
    db: Session,
    *,
    user: User,
    client_job_id: str,
    request_json: dict[str, Any],
) -> tuple[VideoJob, bool]:
    request_hash = json_hash(request_json)
    existing = db.scalar(select(VideoJob).where(
        VideoJob.user_id == user.id, VideoJob.client_job_id == client_job_id,
    ))
    if existing:
        if existing.request_hash != request_hash:
            raise ServiceError(
                "IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不一致", 409
            )
        return existing, False

    quota = db.scalar(select(QuotaPolicy).where(
        QuotaPolicy.user_id == user.id,
    ).with_for_update())
    if quota is None:
        raise ServiceError("QUOTA_NOT_INITIALIZED", "Quota not initialized", 409)
    active_count = db.scalar(select(func.count()).select_from(VideoJob).where(
        VideoJob.user_id == user.id,
        VideoJob.status.in_(ACTIVE_VIDEO_JOB_STATES),
    )) or 0
    if active_count >= quota.max_queue_jobs:
        raise ServiceError("VIDEO_QUEUE_LIMIT", "视频任务队列已达上限", 429)

    quote = video_job_quote(request_json)
    if request_json["video"].get("method") == "ican":
        request_json = {**request_json, "image_billing_credits": str(
            quote["breakdown"]["images"] / int(request_json["video"]["scene_count"]))}
    job = VideoJob(
        id=new_id("vid"), user_id=user.id, client_job_id=client_job_id,
        request_hash=request_hash, request_json=request_json,
        status=VideoJobStatus.QUEUED.value, current_step=VIDEO_STEP_KEYS[0],
        progress=0, reserved_credits=quote["reserved_credits"],
    )
    db.add(job)
    for ordinal, step_key in enumerate(VIDEO_STEP_KEYS):
        db.add(VideoJobStep(
            id=new_id("vstep"), video_job_id=job.id, step_key=step_key,
            ordinal=ordinal, status=VideoStepStatus.PENDING.value,
            input_json={"request_hash": request_hash} if ordinal == 0 else None,
        ))
    reserve_video_job_credits(db, job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(VideoJob).where(
            VideoJob.user_id == user.id, VideoJob.client_job_id == client_job_id,
        ))
        if existing and existing.request_hash == request_hash:
            return existing, False
        if existing:
            raise ServiceError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不一致", 409)
        raise
    return job, True


def transition_video_job(
    db: Session,
    *,
    job: VideoJob,
    step: VideoJobStep,
    output: dict[str, Any],
) -> None:
    """Complete one deterministic step; repeated completion is a no-op."""
    if step.status == VideoStepStatus.COMPLETED.value:
        return
    if step.video_job_id != job.id or step.step_key != job.current_step:
        raise ValueError("step does not match the current video job stage")
    step.status = VideoStepStatus.COMPLETED.value
    step.output_json = output
    step.lease_expires_at = None
    step.finished_at = utcnow()
    next_ordinal = step.ordinal + 1
    if next_ordinal >= len(VIDEO_STEP_KEYS):
        raw_actual = output.get("actual_credits", job.reserved_credits)
        if isinstance(raw_actual, bool):
            raise ValueError("final video actual_credits must be a non-negative number")
        try:
            actual_credits = Decimal(str(raw_actual))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("final video actual_credits must be a non-negative number") from exc
        if actual_credits < 0:
            raise ValueError("final video actual_credits must be a non-negative number")
        output = {**output, "actual_credits": float(actual_credits)}
        step.output_json = output
        job.status = VideoJobStatus.COMPLETED.value
        job.current_step = None
        job.progress = 100
        job.result_json = output
        job.finished_at = utcnow()
        settle_video_job_credits(db, job, min(actual_credits, job.reserved_credits))
        return
    job.current_step = VIDEO_STEP_KEYS[next_ordinal]
    job.status = VIDEO_STEP_JOB_STATES[job.current_step]
    job.progress = next_ordinal * 20


def create_cloud_job(
    db: Session,
    *,
    user: User,
    client_job_id: str,
    request_json: dict[str, Any],
) -> tuple[CloudJob, bool]:
    existing = db.scalar(
        select(CloudJob).where(
            CloudJob.user_id == user.id,
            CloudJob.client_job_id == client_job_id,
        )
    )
    if existing:
        return existing, False

    quota = db.scalar(
        select(QuotaPolicy)
        .where(QuotaPolicy.user_id == user.id)
        .with_for_update()
    )
    if quota is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Quota not initialized")
    chunks = request_json["chunks"]
    total_characters = sum(len(item["text"]) for item in chunks)
    active_count = db.scalar(
        select(func.count(CloudJob.id)).where(
            CloudJob.user_id == user.id,
            CloudJob.status.in_(ACTIVE_JOB_STATES),
        )
    ) or 0
    running_count = db.scalar(
        select(func.count(CloudJob.id)).where(
            CloudJob.user_id == user.id,
            CloudJob.status.in_([
                JobStatus.RUNNING.value,
                JobStatus.FINALIZING.value,
            ]),
        )
    ) or 0
    if active_count >= quota.max_queue_jobs:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Queue quota exceeded")
    if running_count >= quota.max_concurrent_jobs:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Concurrent job quota exceeded"
        )

    usage = db.scalar(
        select(DailyUsage)
        .where(DailyUsage.user_id == user.id, DailyUsage.usage_date == date.today())
        .with_for_update()
    )
    if usage is None:
        usage = DailyUsage(
            id=new_id("use"),
            user_id=user.id,
            usage_date=date.today(),
            jobs_created=0,
            characters=0,
        )
        db.add(usage)
    if usage.characters + total_characters > quota.daily_characters_limit:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Daily quota exceeded")

    credits = quote_credits(total_characters)
    job = CloudJob(
        id=new_id("job"),
        user_id=user.id,
        client_job_id=client_job_id,
        status=JobStatus.QUEUED.value,
        request_json=request_json,
        result_json=None,
        total_characters=total_characters,
        total_chunks=len(chunks),
        reserved_credits=credits,
        message="任务已进入集群队列",
    )
    db.add(job)
    reserve_job_credits(db, job)
    usage.jobs_created += 1
    usage.characters += total_characters
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(CloudJob).where(
                CloudJob.user_id == user.id,
                CloudJob.client_job_id == client_job_id,
            )
        )
        if existing:
            return existing, False
        raise
    return job, True


def credit_recharge_order(db: Session, order: RechargeOrder) -> None:
    wallet = wallet_for_update(db, order.user_id)
    apply_wallet_entry(
        db,
        wallet=wallet,
        entry_type="recharge",
        available_delta=order.credits,
        reserved_delta=0,
        consumed_delta=0,
        reference_type="recharge_order",
        reference_id=order.id,
        idempotency_key=f"recharge:{order.id}:paid",
    )


def reserve_model_pool_credits(db: Session, *, user_id: str, job_id: str, credits: Decimal) -> None:
    wallet = wallet_for_update(db, user_id)
    apply_wallet_entry(
        db,
        wallet=wallet,
        entry_type="reserve_model_pool",
        available_delta=-credits,
        reserved_delta=credits,
        consumed_delta=0,
        reference_type="model_pool_job",
        reference_id=job_id,
        idempotency_key=f"model:{job_id}:reserve",
    )


def settle_model_pool_job(
    db: Session,
    *,
    job: ModelPoolJob,
    actual_credits: Decimal,
) -> None:
    if job.consumed_credits or job.released_credits:
        return
    actual_credits = max(0, min(actual_credits, job.reserved_credits))
    release = job.reserved_credits - actual_credits
    wallet = wallet_for_update(db, job.user_id)
    if actual_credits:
        apply_wallet_entry(
            db,
            wallet=wallet,
            entry_type="consume_model_pool",
            available_delta=0,
            reserved_delta=-actual_credits,
            consumed_delta=actual_credits,
            reference_type="model_pool_job",
            reference_id=job.id,
            idempotency_key=f"model:{job.id}:consume",
        )
    if release:
        apply_wallet_entry(
            db,
            wallet=wallet,
            entry_type="release_model_pool",
            available_delta=release,
            reserved_delta=-release,
            consumed_delta=0,
            reference_type="model_pool_job",
            reference_id=job.id,
            idempotency_key=f"model:{job.id}:release",
        )
    job.consumed_credits = actual_credits
    job.released_credits = release


def release_model_pool_job(db: Session, *, job: ModelPoolJob) -> None:
    settle_model_pool_job(db, job=job, actual_credits=0)


def reserve_image_pool_credits(db: Session, *, user_id: str, job_id: str, credits: Decimal) -> None:
    wallet = wallet_for_update(db, user_id)
    apply_wallet_entry(
        db,
        wallet=wallet,
        entry_type="reserve_image_pool",
        available_delta=-credits,
        reserved_delta=credits,
        consumed_delta=0,
        reference_type="image_pool_job",
        reference_id=job_id,
        idempotency_key=f"image:{job_id}:reserve",
    )


def settle_image_pool_job(
    db: Session,
    *,
    job: ImagePoolJob,
    actual_credits: Decimal,
) -> None:
    if job.consumed_credits or job.released_credits:
        return
    actual_credits = max(0, min(actual_credits, job.reserved_credits))
    release = job.reserved_credits - actual_credits
    wallet = wallet_for_update(db, job.user_id)
    if actual_credits:
        apply_wallet_entry(
            db,
            wallet=wallet,
            entry_type="consume_image_pool",
            available_delta=0,
            reserved_delta=-actual_credits,
            consumed_delta=actual_credits,
            reference_type="image_pool_job",
            reference_id=job.id,
            idempotency_key=f"image:{job.id}:consume",
        )
    if release:
        apply_wallet_entry(
            db,
            wallet=wallet,
            entry_type="release_image_pool",
            available_delta=release,
            reserved_delta=-release,
            consumed_delta=0,
            reference_type="image_pool_job",
            reference_id=job.id,
            idempotency_key=f"image:{job.id}:release",
        )
    job.consumed_credits = actual_credits
    job.released_credits = release


def release_image_pool_job(db: Session, *, job: ImagePoolJob) -> None:
    settle_image_pool_job(db, job=job, actual_credits=0)


def model_pool_reserve_credits(prompt_tokens: int, completion_tokens: int) -> Decimal:
    prompt_tokens = max(0, prompt_tokens)
    completion_tokens = max(0, completion_tokens)
    if prompt_tokens + completion_tokens <= 0:
        prompt_tokens = 1
    upstream_usd = (
        Decimal(prompt_tokens) * settings.model_pool_input_usd_per_1000_tokens
        + Decimal(completion_tokens) * settings.model_pool_output_usd_per_1000_tokens
    ) / Decimal(1000)
    return usd_to_credits(upstream_usd, markup_percent=settings.model_pool_markup_percent)


def image_pool_reserve_credits() -> Decimal:
    return usd_to_credits(
        settings.image_pool_max_upstream_cost_usd,
        markup_percent=settings.image_pool_markup_percent,
    )


def image_pool_actual_credits(actual_cost_credits: str | int | Decimal | None) -> Decimal:
    if actual_cost_credits is None:
        return usd_to_credits(
            settings.image_pool_default_upstream_cost_usd,
            markup_percent=settings.image_pool_markup_percent,
        )
    base = actual_cost_credits
    try:
        amount = Decimal(str(base))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("actual image cost is invalid") from exc
    if amount < 0:
        raise ValueError("actual image cost must not be negative")
    return _with_markup(amount, settings.image_pool_markup_percent)


def audit(
    db: Session,
    admin: User,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any],
) -> None:
    db.add(
        AdminAuditLog(
            id=new_id("aud"),
            admin_user_id=admin.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    )
