from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from typing import Any, Protocol

import httpx
from sqlalchemy import func, or_, select

from .adapters.ray_tts import ray_tts
from .adapters.runninghub_image import runninghub_image
from .config import settings
from .db import SessionLocal
from .exceptions import UpstreamAuthError, UpstreamBusyError, UpstreamPermanentError
from .models import (
    CloudJob, ImagePoolJob, JobStatus, ProviderAccountState, QuotaPolicy,
    VideoJob, VideoJobStatus, VideoJobStep, VideoStepStatus, utcnow,
)
from .services import (
    image_pool_actual_credits, mark_provider_account_failure,
    mark_provider_account_success, release_image_pool_job, release_job_credits,
    persist_video_step_assets, release_video_job_credits,
    settle_image_pool_job, settle_job_credits,
    transition_video_job, upsert_provider_account_states,
)
from .video_pipeline import _is_retryable_io_error

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cloud-job-worker")
_image_submit_claim_lock = threading.Lock()


class VideoOrchestrator(Protocol):
    def run_step(
        self,
        *,
        job_id: str,
        step_key: str,
        request: dict[str, Any],
        prior_outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Call the real provider/renderer and return durable, public-safe output."""


def _aware_utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _retryable_ray_submission_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429} or exc.response.status_code >= 500
    return False


def _poll_failure_exhausted(attempts: int, started_at) -> bool:
    if attempts >= settings.pool_poll_max_retries:
        return True
    if started_at is None:
        return False
    return (utcnow() - _aware_utc(started_at)).total_seconds() >= settings.pool_poll_timeout_seconds


def _poll_retry_delay(attempts: int) -> int:
    return min(60, max(2, 2 ** max(0, attempts - 1)))


def _recover_expired_leases() -> None:
    now = utcnow()
    with SessionLocal.begin() as db:
        tts_jobs = db.scalars(select(CloudJob).where(
            CloudJob.status == JobStatus.RUNNING.value,
            CloudJob.ray_job_id.is_(None),
            CloudJob.lease_expires_at.is_not(None),
            CloudJob.lease_expires_at < now,
        ).with_for_update(skip_locked=True)).all()
        for job in tts_jobs:
            job.status = JobStatus.QUEUED.value
            job.message = "Recovered after an interrupted Ray submission"
            job.lease_expires_at = None
        image_jobs = db.scalars(select(ImagePoolJob).where(
            ImagePoolJob.provider == "runninghub",
            ImagePoolJob.status == "submitting",
            ImagePoolJob.upstream_task_id.is_(None),
            ImagePoolJob.lease_expires_at.is_not(None),
            ImagePoolJob.lease_expires_at < now,
        ).with_for_update(skip_locked=True)).all()
        for job in image_jobs:
            job.status = "queued"
            job.lease_expires_at = None
        video_steps = db.scalars(select(VideoJobStep).where(
            VideoJobStep.status == VideoStepStatus.RUNNING.value,
            VideoJobStep.lease_expires_at.is_not(None),
            VideoJobStep.lease_expires_at < now,
        ).with_for_update(skip_locked=True)).all()
        for step in video_steps:
            job = db.get(VideoJob, step.video_job_id, with_for_update=True)
            if not job or job.status in {
                VideoJobStatus.COMPLETED.value, VideoJobStatus.FAILED.value,
                VideoJobStatus.CANCELLED.value,
            }:
                continue
            if job.cancel_requested:
                step.status = VideoStepStatus.CANCELLED.value
                step.finished_at = now
                step.lease_expires_at = None
                job.status = VideoJobStatus.CANCELLED.value
                job.current_step = None
                job.finished_at = now
                job.lease_expires_at = None
                for pending in job.steps:
                    if pending.status == VideoStepStatus.PENDING.value:
                        pending.status = VideoStepStatus.CANCELLED.value
                        pending.finished_at = now
                release_video_job_credits(db, job)
                continue
            step.status = VideoStepStatus.PENDING.value
            step.lease_expires_at = None
            job.status = (
                VideoJobStatus.QUEUED.value
                if step.ordinal == 0 else job.status
            )
            job.lease_expires_at = None


def process_one_video(orchestrator: VideoOrchestrator) -> None:
    """Advance exactly one durable step using an explicitly supplied integration."""
    claimed: tuple[str, str, dict[str, Any], dict[str, dict[str, Any]]] | None = None
    with SessionLocal.begin() as db:
        job = db.scalar(select(VideoJob).where(
            VideoJob.status.in_([
                VideoJobStatus.QUEUED.value,
                VideoJobStatus.STORYBOARDING.value,
                VideoJobStatus.GENERATING_ASSETS.value,
                VideoJobStatus.COMPOSING.value,
                VideoJobStatus.PUBLISHING.value,
            ]),
            VideoJob.cancel_requested.is_(False),
            or_(
                VideoJob.lease_expires_at.is_(None),
                VideoJob.lease_expires_at <= utcnow(),
            ),
        ).order_by(VideoJob.created_at).with_for_update(skip_locked=True))
        if not job or not job.current_step:
            return
        step = db.scalar(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job.id,
            VideoJobStep.step_key == job.current_step,
        ).with_for_update())
        if not step or step.status == VideoStepStatus.COMPLETED.value:
            return
        step.status = VideoStepStatus.RUNNING.value
        step.attempt_count += 1
        step.started_at = step.started_at or utcnow()
        step.lease_expires_at = utcnow() + timedelta(seconds=settings.pool_worker_lease_seconds)
        job.started_at = job.started_at or utcnow()
        job.attempt_count += 1
        job.status = {
            "storyboard": VideoJobStatus.STORYBOARDING.value,
            "tts": VideoJobStatus.GENERATING_ASSETS.value,
            "images": VideoJobStatus.GENERATING_ASSETS.value,
            "compose": VideoJobStatus.COMPOSING.value,
            "publish": VideoJobStatus.PUBLISHING.value,
        }[step.step_key]
        job.lease_expires_at = step.lease_expires_at
        prior = {
            item.step_key: item.output_json
            for item in job.steps
            if item.status == VideoStepStatus.COMPLETED.value and item.output_json is not None
        }
        claimed = (job.id, step.id, job.request_json, prior)
    if claimed is None:
        return
    job_id, step_id, request, prior = claimed
    with SessionLocal() as db:
        step_key = db.get(VideoJobStep, step_id).step_key
    try:
        output = orchestrator.run_step(
            job_id=job_id, step_key=step_key, request=request, prior_outputs=prior,
        )
        if not isinstance(output, dict):
            raise TypeError("video orchestrator output must be a dictionary")
    except Exception as exc:
        log.exception("Video step %s failed for job %s", step_key, job_id)
        with SessionLocal.begin() as db:
            job = db.get(VideoJob, job_id, with_for_update=True)
            step = db.get(VideoJobStep, step_id, with_for_update=True)
            if job and step and step.status == VideoStepStatus.RUNNING.value:
                if (
                    _is_retryable_io_error(exc)
                    and step.attempt_count <= settings.video_step_max_retries
                ):
                    retry_number = int(step.attempt_count)
                    delay = min(
                        60.0,
                        settings.video_retry_backoff_seconds
                        * (2 ** max(0, retry_number - 1)),
                    )
                    state = dict(step.output_json or {})
                    state["retry_after"] = (
                        utcnow() + timedelta(seconds=delay)
                    ).isoformat()
                    state["last_retry_error"] = str(exc)[:1000]
                    step.status = VideoStepStatus.PENDING.value
                    step.error_code = "RETRYING"
                    step.error_message = (
                        f"Transient storage/network error; retry "
                        f"{retry_number}/{settings.video_step_max_retries} scheduled"
                    )[:2000]
                    step.output_json = state
                    step.finished_at = None
                    step.lease_expires_at = None
                    job.status = {
                        "storyboard": VideoJobStatus.STORYBOARDING.value,
                        "tts": VideoJobStatus.GENERATING_ASSETS.value,
                        "images": VideoJobStatus.GENERATING_ASSETS.value,
                        "compose": VideoJobStatus.COMPOSING.value,
                        "publish": VideoJobStatus.PUBLISHING.value,
                    }[step.step_key]
                    job.current_step = step.step_key
                    job.error_code = None
                    job.error_message = None
                    job.lease_expires_at = utcnow() + timedelta(seconds=delay)
                else:
                    step.status = VideoStepStatus.FAILED.value
                    step.error_code = "VIDEO_STEP_FAILED"
                    step.error_message = str(exc)[:2000]
                    step.finished_at = utcnow()
                    step.lease_expires_at = None
                    job.status = VideoJobStatus.FAILED.value
                    job.error_code = "VIDEO_STEP_FAILED"
                    job.error_message = f"{step.step_key}: {exc}"[:2000]
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    release_video_job_credits(db, job)
        return
    with SessionLocal.begin() as db:
        job = db.get(VideoJob, job_id, with_for_update=True)
        step = db.get(VideoJobStep, step_id, with_for_update=True)
        if not job or not step or step.status != VideoStepStatus.RUNNING.value:
            return
        job.lease_expires_at = None
        if job.cancel_requested:
            step.status = VideoStepStatus.CANCELLED.value
            step.finished_at = utcnow()
            step.lease_expires_at = None
            job.status = VideoJobStatus.CANCELLED.value
            job.current_step = None
            job.finished_at = utcnow()
            for pending in job.steps:
                if pending.status == VideoStepStatus.PENDING.value:
                    pending.status = VideoStepStatus.CANCELLED.value
                    pending.finished_at = utcnow()
            release_video_job_credits(db, job)
            return
        persist_video_step_assets(db, job=job, step=step, output=output)
        transition_video_job(db, job=job, step=step, output=output)


def submit_one() -> None:
    claimed: tuple[str, dict] | None = None
    with SessionLocal.begin() as db:
        candidates = db.scalars(select(CloudJob).where(
            CloudJob.status == JobStatus.QUEUED.value,
            or_(
                CloudJob.lease_expires_at.is_(None),
                CloudJob.lease_expires_at <= utcnow(),
            ),
        ).order_by(CloudJob.created_at).limit(50).with_for_update(skip_locked=True)).all()
        for job in candidates:
            if job.parent_video_job_id:
                parent = db.get(VideoJob, job.parent_video_job_id)
                if parent is None or parent.cancel_requested or parent.status in {
                    VideoJobStatus.CANCEL_REQUESTED.value,
                    VideoJobStatus.CANCELLED.value,
                    VideoJobStatus.FAILED.value,
                }:
                    job.status = JobStatus.CANCELLED.value
                    job.cancel_requested = True
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    continue
            quota = db.scalar(select(QuotaPolicy).where(
                QuotaPolicy.user_id == job.user_id,
            ).with_for_update())
            if quota is None or quota.max_concurrent_jobs <= 0:
                continue
            running = db.scalar(select(func.count()).select_from(CloudJob).where(
                CloudJob.user_id == job.user_id,
                CloudJob.status.in_([JobStatus.RUNNING.value, JobStatus.FINALIZING.value]),
                CloudJob.ray_job_id.is_not(None),
            )) or 0
            if running >= quota.max_concurrent_jobs:
                continue
            job.status = JobStatus.RUNNING.value
            job.started_at = job.started_at or utcnow()
            job.message = "Submitting to Ray"
            job.submission_attempts += 1
            job.lease_expires_at = utcnow() + timedelta(seconds=settings.pool_worker_lease_seconds)
            claimed = (job.id, job.request_json)
            break
    if not claimed:
        return
    job_id, payload = claimed
    with SessionLocal() as db:
        job = db.get(CloudJob, job_id)
        if not job or job.status != JobStatus.RUNNING.value or job.cancel_requested:
            return
    try:
        ray_id = ray_tts.create_job(job_id, payload)
    except Exception as exc:
        log.exception("Ray submission failed for job %s", job_id)
        with SessionLocal.begin() as db:
            job = db.get(CloudJob, job_id, with_for_update=True)
            if job and job.ray_job_id is None and job.status == JobStatus.RUNNING.value:
                if _retryable_ray_submission_error(exc):
                    retry_seconds = min(60, max(2, job.submission_attempts * 2))
                    job.status = JobStatus.QUEUED.value
                    job.message = "Ray temporarily unavailable; queued for retry"
                    job.error_message = None
                    job.lease_expires_at = utcnow() + timedelta(seconds=retry_seconds)
                else:
                    job.status = JobStatus.FAILED.value
                    job.error_message = "Ray rejected the job submission"
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    release_job_credits(db, job)
        return
    with SessionLocal.begin() as db:
        job = db.get(CloudJob, job_id, with_for_update=True)
        if job:
            job.ray_job_id = ray_id
            if job.cancel_requested:
                job.status = JobStatus.CANCEL_REQUESTED.value
                job.message = "Ray cancellation pending"
            else:
                job.message = "Ray processing"
            job.lease_expires_at = None


def poll() -> None:
    with SessionLocal() as db:
        ids = db.scalars(select(CloudJob.id).where(
            CloudJob.status.in_([
                JobStatus.RUNNING.value, JobStatus.FINALIZING.value,
                JobStatus.CANCEL_REQUESTED.value,
            ]),
            CloudJob.ray_job_id.is_not(None),
        )).all()
    for job_id in ids:
        with SessionLocal() as db:
            job = db.get(CloudJob, job_id)
            ray_id, cancel = job.ray_job_id, job.cancel_requested
            if job.lease_expires_at and _aware_utc(job.lease_expires_at) > utcnow():
                continue
        with SessionLocal.begin() as db:
            job = db.get(CloudJob, job_id, with_for_update=True)
            if not job:
                continue
            job.poll_started_at = job.poll_started_at or utcnow()
            poll_attempts, poll_started_at = job.poll_attempts, job.poll_started_at
        try:
            if cancel:
                ray_tts.cancel_job(ray_id)
            remote = ray_tts.get_job(ray_id)
        except Exception as exc:
            log.exception("Ray polling failed for job %s", job_id)
            with SessionLocal.begin() as db:
                job = db.get(CloudJob, job_id, with_for_update=True)
                if not job:
                    continue
                job.poll_attempts = int(job.poll_attempts or 0) + 1
                poll_attempts = job.poll_attempts
                if _poll_failure_exhausted(poll_attempts, poll_started_at):
                    job.status = JobStatus.FAILED.value
                    job.error_message = "Ray polling timed out or remained unavailable"
                    job.finished_at = utcnow()
                    job.poll_started_at = None
                    release_job_credits(db, job)
                else:
                    job.message = f"Ray polling retry {poll_attempts}/{settings.pool_poll_max_retries}: {str(exc)[:300]}"
                    job.lease_expires_at = utcnow() + timedelta(seconds=_poll_retry_delay(poll_attempts))
            continue
        with SessionLocal.begin() as db:
            job = db.get(CloudJob, job_id, with_for_update=True)
            if not job:
                continue
            state = str(remote.get("status", "running"))
            job.lease_expires_at = None
            job.progress = int(remote.get("progress", job.progress))
            job.completed_chunks = int(remote.get("completed_chunks", job.completed_chunks))
            job.message = str(remote.get("message", job.message))[:500]
            partial_result = remote.get("result")
            if isinstance(partial_result, dict):
                job.result_json = partial_result
            if state == "completed":
                job.status = JobStatus.COMPLETED.value
                job.result_json = remote.get("result") or {}
                job.progress = 100
                job.finished_at = utcnow()
                job.expires_at = utcnow() + timedelta(seconds=settings.result_ttl_seconds)
                settle_job_credits(db, job, job.reserved_credits)
            elif state in {"failed", "cancelled"}:
                job.status = JobStatus.CANCELLED.value if state == "cancelled" or job.cancel_requested else JobStatus.FAILED.value
                job.error_message = str(remote.get("error") or "Ray job failed")[:2000]
                job.finished_at = utcnow()
                release_job_credits(db, job)
            elif state == "finalizing":
                job.status = JobStatus.FINALIZING.value
            if state in {"completed", "failed", "cancelled"}:
                job.poll_attempts = 0
                job.poll_started_at = None


def _healthy_image_accounts(db) -> list[ProviderAccountState]:
    upsert_provider_account_states(db)
    now = utcnow()
    return list(db.scalars(select(ProviderAccountState).where(
        ProviderAccountState.provider == "runninghub_image",
        ProviderAccountState.permanently_disabled.is_(False),
    ).order_by(
        ProviderAccountState.failure_count.asc(), ProviderAccountState.updated_at.asc(),
    ).with_for_update(skip_locked=True)).all())


def _configured_image_accounts(db) -> list[ProviderAccountState]:
    upsert_provider_account_states(db)
    slots = tuple(range(max(1, len(settings.runninghub_image_api_keys))))
    return list(db.scalars(select(ProviderAccountState).where(
        ProviderAccountState.provider == "runninghub_image",
        ProviderAccountState.slot.in_(slots),
    ).order_by(ProviderAccountState.slot).with_for_update()).all())


def _unavailable_image_pool_error(
    accounts: list[ProviderAccountState], now,
) -> tuple[str, str] | None:
    if not accounts:
        return "IMAGE_POOL_UNAVAILABLE", "图像号池暂不可用"
    cooling = [
        account for account in accounts
        if account.disabled_until and _aware_utc(account.disabled_until) > now
    ]
    if len(cooling) == len(accounts) and all(
        account.last_error_code == "414" for account in accounts
    ):
        return "IMAGE_POOL_BALANCE_UNAVAILABLE", "图像号池上游余额不足"
    if all(account.permanently_disabled for account in accounts):
        return "IMAGE_POOL_UNAVAILABLE", "图像号池账号不可用"
    return None


def submit_one_image() -> bool:
    claimed: tuple[str, int, dict] | None = None
    # PostgreSQL honors SKIP LOCKED, while the SQLite test/local runtime does
    # not. Serialize only the short claim transaction; provider calls below
    # still run concurrently.
    with _image_submit_claim_lock:
        with SessionLocal.begin() as db:
            job = db.scalar(select(ImagePoolJob).where(
                ImagePoolJob.provider == "runninghub",
                ImagePoolJob.status == "queued",
            ).order_by(ImagePoolJob.created_at).with_for_update(skip_locked=True))
            if not job:
                return False
            if job.parent_video_job_id:
                parent = db.get(VideoJob, job.parent_video_job_id)
                if parent is None or parent.cancel_requested or parent.status in {
                    VideoJobStatus.CANCEL_REQUESTED.value,
                    VideoJobStatus.CANCELLED.value,
                    VideoJobStatus.FAILED.value,
                }:
                    job.status = "failed"
                    job.error_code = "PARENT_VIDEO_CANCELLED"
                    job.error_message = "Parent video job was cancelled"
                    job.finished_at = utcnow()
                    release_image_pool_job(db, job=job)
                    return True
            now = utcnow()
            selected: tuple[ProviderAccountState, int] | None = None
            accounts = _healthy_image_accounts(db)
            for account in accounts:
                if account.disabled_until and _aware_utc(account.disabled_until) > now:
                    continue
                running = db.scalar(select(func.count()).select_from(ImagePoolJob).where(
                    ImagePoolJob.provider == "runninghub",
                    ImagePoolJob.account_slot == account.slot,
                    ImagePoolJob.status.in_(["submitting", "running"]),
                )) or 0
                if selected is None or running < selected[1]:
                    selected = (account, running)
            if selected is None:
                unavailable = _unavailable_image_pool_error(
                    _configured_image_accounts(db), now,
                )
                if unavailable:
                    job.status = "failed"
                    job.error_code, job.error_message = unavailable
                    job.finished_at = now
                    job.lease_expires_at = None
                    release_image_pool_job(db, job=job)
                    return True
            if selected is not None:
                account = selected[0]
                job.status = "submitting"
                job.account_slot = account.slot
                job.attempt_count += 1
                job.started_at = job.started_at or now
                job.lease_expires_at = now + timedelta(seconds=settings.pool_worker_lease_seconds)
                claimed = (job.id, account.slot, job.request_json)
    if not claimed:
        return False
    job_id, slot, payload = claimed
    try:
        upstream_task_id = runninghub_image.submit(slot=slot, job_id=job_id, payload=payload)
    except (UpstreamAuthError, UpstreamBusyError, UpstreamPermanentError) as exc:
        code = str(exc.details.get("upstream_code") or exc.code)
        retry_account = isinstance(exc, (UpstreamAuthError, UpstreamBusyError)) or code == "414"
        with SessionLocal.begin() as db:
            job = db.get(ImagePoolJob, job_id, with_for_update=True)
            mark_provider_account_failure(
                db, provider="runninghub_image", slot=slot, error_code=code,
                permanent=isinstance(exc, UpstreamAuthError),
            )
            if job and job.status == "submitting":
                job.lease_expires_at = None
                if retry_account and job.attempt_count < max(3, len(settings.runninghub_image_api_keys) * 3):
                    job.status = "queued"
                    job.account_slot = None
                else:
                    job.status = "failed"
                    job.error_code = "IMAGE_GENERATION_REJECTED" if isinstance(exc, UpstreamPermanentError) else "IMAGE_POOL_UNAVAILABLE"
                    job.error_message = "图片生成请求被拒绝" if isinstance(exc, UpstreamPermanentError) else "图像号池暂不可用"
                    job.finished_at = utcnow()
                    release_image_pool_job(db, job=job)
        return True
    with SessionLocal.begin() as db:
        job = db.get(ImagePoolJob, job_id, with_for_update=True)
        if job and job.status == "submitting":
            job.upstream_task_id = upstream_task_id
            job.status = "running"
            job.lease_expires_at = None
            job.poll_attempts = 0
            job.poll_started_at = utcnow()
            mark_provider_account_success(db, provider="runninghub_image", slot=slot)
    return True


def submit_queued_images() -> int:
    # Claim several independent jobs in parallel. Each claim is still guarded
    # by the database row lock, while the provider requests can use all
    # configured account slots concurrently.
    with SessionLocal() as db:
        account_count = max(1, len(settings.runninghub_image_api_keys))
    # Materialize provider slots once before worker threads start so SQLite and
    # PostgreSQL do not race while inserting the same provider state rows.
    with SessionLocal.begin() as db:
        upsert_provider_account_states(db)
    workers = max(1, min(16, account_count * max(1, settings.image_pool_per_account_concurrency)))
    submitted = 0
    while True:
        with SessionLocal() as db:
            pending = int(db.scalar(select(func.count()).select_from(ImagePoolJob).where(ImagePoolJob.provider == "runninghub", ImagePoolJob.status == "queued")) or 0)
        if not pending:
            return submitted
        batch_size = min(workers, pending)
        with ThreadPoolExecutor(max_workers=batch_size, thread_name_prefix="image-submit") as executor:
            submitted += sum(1 for result in executor.map(lambda _: submit_one_image(), range(batch_size)) if result)


def poll_images() -> None:
    with SessionLocal() as db:
        ids = db.scalars(select(ImagePoolJob.id).where(
            ImagePoolJob.provider == "runninghub",
            ImagePoolJob.status == "running",
            ImagePoolJob.upstream_task_id.is_not(None),
        )).all()
    for job_id in ids:
        with SessionLocal() as db:
            job = db.get(ImagePoolJob, job_id)
            task_id, slot = job.upstream_task_id, job.account_slot
            poll_started_at = job.poll_started_at
            lease_expires_at = job.lease_expires_at
        if slot is None or (lease_expires_at and _aware_utc(lease_expires_at) > utcnow()):
            continue
        try:
            remote = runninghub_image.query(slot=slot, task_id=task_id)
        except (UpstreamAuthError, UpstreamBusyError, httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError) as exc:
            with SessionLocal.begin() as db:
                job = db.get(ImagePoolJob, job_id, with_for_update=True)
                if not job or job.status != "running":
                    continue
                if isinstance(exc, (UpstreamAuthError, UpstreamBusyError)):
                    mark_provider_account_failure(
                        db, provider="runninghub_image", slot=slot,
                        error_code=str(getattr(exc, "details", {}).get("upstream_code") or getattr(exc, "code", "POLL_ERROR")),
                        permanent=isinstance(exc, UpstreamAuthError),
                    )
                job.poll_attempts = int(job.poll_attempts or 0) + 1
                job.poll_started_at = job.poll_started_at or poll_started_at or utcnow()
                if _poll_failure_exhausted(job.poll_attempts, job.poll_started_at):
                    job.status = "failed"
                    job.error_code = "IMAGE_POLL_TIMEOUT"
                    job.error_message = "图片服务轮询超时，请稍后重试"
                    job.finished_at = utcnow()
                    job.lease_expires_at = None
                    release_image_pool_job(db, job=job)
                else:
                    job.error_message = f"图片轮询重试 {job.poll_attempts}/{settings.pool_poll_max_retries}: {str(exc)[:300]}"
                    job.lease_expires_at = utcnow() + timedelta(seconds=_poll_retry_delay(job.poll_attempts))
            continue
        except UpstreamPermanentError:
            with SessionLocal.begin() as db:
                job = db.get(ImagePoolJob, job_id, with_for_update=True)
                if job:
                    job.status = "failed"
                    job.error_code = "IMAGE_GENERATION_FAILED"
                    job.error_message = "图片生成失败"
                    job.finished_at = utcnow()
                    release_image_pool_job(db, job=job)
            continue
        if remote["status"] != "SUCCESS":
            with SessionLocal.begin() as db:
                job = db.get(ImagePoolJob, job_id, with_for_update=True)
                if job and job.status == "running":
                    job.poll_started_at = job.poll_started_at or poll_started_at or utcnow()
                    if (utcnow() - _aware_utc(job.poll_started_at)).total_seconds() >= settings.pool_poll_timeout_seconds:
                        job.status = "failed"
                        job.error_code = "IMAGE_POLL_TIMEOUT"
                        job.error_message = "图片服务轮询超时，请稍后重试"
                        job.finished_at = utcnow()
                        job.lease_expires_at = None
                        release_image_pool_job(db, job=job)
            continue
        base_cost = remote.get("actual_cost_credits")
        charged = image_pool_actual_credits(base_cost)
        with SessionLocal.begin() as db:
            job = db.get(ImagePoolJob, job_id, with_for_update=True)
            if not job or job.status != "running":
                continue
            job.status = "completed"
            job.result_url = remote["image_url"]
            job.actual_cost_credits = base_cost
            job.finished_at = utcnow()
            job.poll_attempts = 0
            job.poll_started_at = None
            job.lease_expires_at = None
            settle_image_pool_job(db, job=job, actual_credits=charged)
            mark_provider_account_success(db, provider="runninghub_image", slot=slot)


def run() -> None:
    log.info("Cloud job worker started")
    while True:
        try:
            _recover_expired_leases()
            submit_one()
            submit_queued_images()
            poll()
            poll_images()
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("worker iteration failed")
        time.sleep(2)


if __name__ == "__main__":
    run()
