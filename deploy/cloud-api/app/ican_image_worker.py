"""Dedicated bounded worker: never replay an uncertain synchronous generation."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from .adapters.ican_image import ican_image
from .config import settings
from .db import SessionLocal
from .exceptions import ServiceError
from .ican_images import read_image, save_image, storage_path
from .models import ImagePoolJob, ProviderAccountState, VideoJob, new_id, utcnow
from .services import release_image_pool_job, settle_image_pool_job

log = logging.getLogger("ican-image-worker")
_claim_lock = threading.Lock()


def ensure_accounts():
    with SessionLocal.begin() as db:
        for slot in range(len(settings.ican_image_api_keys)):
            exists = db.scalar(select(ProviderAccountState).where(
                ProviderAccountState.provider == "ican_image", ProviderAccountState.slot == slot))
            if not exists:
                db.add(ProviderAccountState(id=new_id("pas"), provider="ican_image", slot=slot,
                                           failure_count=0, permanently_disabled=False))


def recover_expired():
    with SessionLocal.begin() as db:
        jobs = db.scalars(select(ImagePoolJob).where(
            ImagePoolJob.provider == "ican", ImagePoolJob.status == "running",
            ImagePoolJob.lease_expires_at < utcnow()).with_for_update(skip_locked=True)).all()
        for job in jobs:
            # A committed local artifact can safely be settled after a process crash.
            key = f"result/{job.id}.bin"
            if storage_path(key).is_file():
                job.status = "completed"
                job.result_url = key
                if not job.parent_video_job_id:
                    settle_image_pool_job(db, job=job, actual_credits=job.reserved_credits)
            else:
                job.status = "failed"
                job.error_code = "ICAN_OUTCOME_UNKNOWN"
                job.error_message = "生成进程中断，已退回积分；未自动重新提交，上游可能已产生费用"
                if not job.parent_video_job_id:
                    release_image_pool_job(db, job=job)
            job.finished_at = utcnow()
            job.lease_expires_at = None


def submit_one():
    if not ican_image.configured():
        return False
    with _claim_lock, SessionLocal.begin() as db:
        # Shared account locks serialize claims across worker processes, too.
        accounts = db.scalars(select(ProviderAccountState).where(
            ProviderAccountState.provider == "ican_image",
            ProviderAccountState.slot < len(settings.ican_image_api_keys),
        ).order_by(ProviderAccountState.updated_at, ProviderAccountState.slot).with_for_update()).all()
        now = utcnow()
        available = [a for a in accounts if not a.disabled_until or
                     a.disabled_until.replace(tzinfo=now.tzinfo) <= now]
        if not available:
            return False
        running = db.scalar(select(func.count()).select_from(ImagePoolJob).where(
            ImagePoolJob.provider == "ican", ImagePoolJob.status == "running")) or 0
        if running >= settings.ican_image_concurrency:
            return False
        job = db.scalar(select(ImagePoolJob).where(
            ImagePoolJob.provider == "ican", ImagePoolJob.status == "queued",
        ).order_by(ImagePoolJob.created_at).with_for_update(skip_locked=True))
        if not job:
            return False
        if job.parent_video_job_id:
            parent = db.get(VideoJob, job.parent_video_job_id)
            if parent is None or parent.cancel_requested or parent.status in {"cancel_requested", "cancelled", "failed"}:
                job.status = "failed"
                job.error_code = "PARENT_VIDEO_CANCELLED"
                job.error_message = "Parent video job was cancelled"
                job.finished_at = utcnow()
                return True
        slot = available[0].slot
        available[0].updated_at = now
        job.status, job.account_slot = "running", slot
        job.attempt_count += 1
        job.started_at = now
        # Covers model lookup + generation + bounded artifact download.
        job.lease_expires_at = now + timedelta(seconds=settings.ican_image_timeout_seconds * 2 + 180)
        job_id, payload = job.id, job.request_json
    try:
        references = [read_image(key) for key in payload["references"]]
        data, content_type = ican_image.generate(slot=slot, payload=payload, references=references)
        result_key = save_image(f"result/{job_id}.bin", data)
    except Exception as exc:
        error = exc if isinstance(exc, ServiceError) else ServiceError(
            "ICAN_PROCESSING_FAILED", "图片处理失败，未自动重新提交", 502)
        # Do not log exceptions or upstream payloads: they may contain image data or secrets.
        log.warning("ICAN job %s failed: %s", job_id, error.code)
        with SessionLocal.begin() as db:
            job = db.get(ImagePoolJob, job_id, with_for_update=True)
            if job and job.status == "running":
                job.status = "failed"
                job.error_code, job.error_message = error.code, error.message
                job.finished_at, job.lease_expires_at = utcnow(), None
                if not job.parent_video_job_id:
                    release_image_pool_job(db, job=job)
            account = db.scalar(select(ProviderAccountState).where(
                ProviderAccountState.provider == "ican_image", ProviderAccountState.slot == slot).with_for_update())
            if account and (error.code in {"ICAN_OUTCOME_UNKNOWN", "ICAN_MODEL_UNAVAILABLE"} or
                            error.details.get("upstream_status") in {401, 403, 429}):
                account.disabled_until = utcnow() + timedelta(seconds=60)
                account.last_error_code = error.code
                account.failure_count += 1
        return True
    with SessionLocal.begin() as db:
        job = db.get(ImagePoolJob, job_id, with_for_update=True)
        if job and job.status == "running":
            job.status = "completed"
            job.result_url, job.result_content_type = result_key, content_type
            job.finished_at, job.lease_expires_at = utcnow(), None
            if not job.parent_video_job_id:
                settle_image_pool_job(db, job=job, actual_credits=job.reserved_credits)
    return True


def run():
    import signal
    settings.validate()
    ensure_accounts()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    def loop():
        while not stop.is_set():
            try:
                recover_expired()
                if submit_one():
                    continue
            except Exception:
                log.error("ICAN worker iteration failed; no generation replay")
            stop.wait(2)
    with ThreadPoolExecutor(max_workers=settings.ican_image_concurrency) as pool:
        list(pool.map(lambda _: loop(), range(settings.ican_image_concurrency)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
