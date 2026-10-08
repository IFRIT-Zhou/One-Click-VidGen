from __future__ import annotations

import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import or_, select

from .config import settings
from .db import SessionLocal
from .models import VideoJob, VideoJobStatus, utcnow
from .video_pipeline import AdvanceResult, VideoPipeline
from .video_providers import VideoImageProvider, VideoStoryboardProvider, VideoTTSProvider
from .video_storage import video_storage


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("video-job-worker")

ACTIVE_STATUSES = (
    VideoJobStatus.QUEUED.value,
    VideoJobStatus.STORYBOARDING.value,
    VideoJobStatus.GENERATING_ASSETS.value,
    VideoJobStatus.COMPOSING.value,
    VideoJobStatus.PUBLISHING.value,
    VideoJobStatus.CANCEL_REQUESTED.value,
)


def candidate_job_ids(limit: int | None = None) -> list[str]:
    maximum = limit or settings.video_worker_concurrency
    with SessionLocal() as db:
        return list(db.scalars(
            select(VideoJob.id)
            .where(VideoJob.status.in_(ACTIVE_STATUSES))
            .where(or_(
                VideoJob.status == VideoJobStatus.CANCEL_REQUESTED.value,
                VideoJob.lease_expires_at.is_(None),
                VideoJob.lease_expires_at <= utcnow(),
            ))
            .order_by(VideoJob.created_at, VideoJob.id)
            .limit(maximum)
        ).all())


def build_pipeline(job_id: str, *, worker_id: str) -> VideoPipeline:
    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if job is None:
            raise LookupError(f"Video job does not exist: {job_id}")
        user_id = job.user_id
        resolution = str((job.request_json.get("video") or {}).get("resolution") or "1k")
        reference_image_urls = list(job.request_json.get("reference_image_urls") or [])
        image_options = job.request_json.get("video") or {}
        method = image_options.get("method", "running")
        size = image_options.get("size")
        billing_credits = job.request_json.get("image_billing_credits")
    return VideoPipeline(
        session_factory=SessionLocal,
        storage=video_storage,
        storyboard_provider=VideoStoryboardProvider(user_id=user_id, job_id=job_id),
        tts_provider=VideoTTSProvider(user_id=user_id, job_id=job_id),
        image_provider=VideoImageProvider(
            user_id=user_id,
            job_id=job_id,
            resolution=resolution, method=method, size=size, billing_credits=billing_credits,
            reference_image_urls=reference_image_urls,
        ),
        worker_id=f"{worker_id}:{job_id}",
        action_lease_seconds=settings.video_action_lease_seconds,
        compose_lease_seconds=settings.video_compose_lease_seconds,
        step_max_retries=settings.video_step_max_retries,
        retry_backoff_seconds=settings.video_retry_backoff_seconds,
    )


def advance_job(job_id: str, *, worker_id: str) -> AdvanceResult:
    return build_pipeline(job_id, worker_id=worker_id).advance(job_id)


def run_once(*, worker_id: str, concurrency: int | None = None) -> list[AdvanceResult]:
    maximum = concurrency or settings.video_worker_concurrency
    job_ids = candidate_job_ids(maximum)
    if not job_ids:
        return []
    with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="video-job") as executor:
        futures = [executor.submit(advance_job, job_id, worker_id=worker_id) for job_id in job_ids]
        results: list[AdvanceResult] = []
        for job_id, future in zip(job_ids, futures, strict=True):
            try:
                results.append(future.result())
            except Exception:
                log.exception("Video worker failed outside pipeline handling for job %s", job_id)
        return results


def run() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log.info(
        "Video job worker started (worker_id=%s concurrency=%s)",
        worker_id,
        settings.video_worker_concurrency,
    )
    while True:
        try:
            run_once(worker_id=worker_id)
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("Video worker iteration failed")
        time.sleep(settings.video_worker_poll_seconds)


if __name__ == "__main__":
    run()
