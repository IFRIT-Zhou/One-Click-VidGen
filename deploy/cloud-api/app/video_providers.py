from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from . import ican_images
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.model_pool import model_pool
from .adapters.ray_tts import ray_tts
from .adapters.runninghub_image import runninghub_image
from .db import SessionLocal
from .config import settings
from .exceptions import ServiceError
from .models import CloudJob, ImagePoolJob, JobStatus, ModelPoolJob, new_id, utcnow
from .services import (
    json_hash,
    mark_provider_account_failure,
    mark_provider_account_success,
    pick_provider_account_slot,
    image_pool_actual_credits,
    model_pool_reserve_credits,
    quote_credits,
)
from .video_storyboard import build_fallback_storyboard


SessionFactory = Callable[[], Session]


def _conflict() -> ServiceError:
    return ServiceError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不一致", 409)


def _failed(code: str | None, message: str | None = None) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": {
            "code": code or "PROVIDER_FAILED",
            "message": message or "Provider task failed",
        },
    }


def _require_action_token(value: str, expected: str) -> None:
    if value != expected:
        raise ServiceError(
            "INVALID_VIDEO_ACTION_TOKEN", "Video provider action token does not match", 409
        )


class VideoStoryboardProvider:
    def __init__(
        self,
        *,
        user_id: str,
        job_id: str,
        session_factory: SessionFactory = SessionLocal,
    ) -> None:
        self.user_id = user_id
        self.job_id = job_id
        self._session_factory = session_factory
        self.idempotency_key = f"video:{job_id}:storyboard"

    def submit(self, *, messages: list[dict[str, str]], action_token: str) -> str:
        _require_action_token(
            action_token, f"video:{self.job_id}:storyboard:storyboard_submit",
        )
        payload: dict[str, Any] = {
            "model": "auto",
            "messages": messages,
            "temperature": 0.2,
        }
        request_hash = json_hash(payload)
        claimed: tuple[str, int, dict[str, Any]] | None = None
        with self._session_factory.begin() as db:
            child = db.scalar(select(ModelPoolJob).where(
                ModelPoolJob.user_id == self.user_id,
                ModelPoolJob.idempotency_key == self.idempotency_key,
            ).with_for_update())
            if child is not None:
                if child.request_hash != request_hash:
                    raise _conflict()
                child_id = child.id
                if child.status in {"completed", "failed"}:
                    return child.id
                if self._submission_expired(child.response_json):
                    attempt = self._submission_attempt(child.response_json) + 1
                    child.response_json = self._submission(payload, attempt)
                    claimed = (child.id, int(child.account_slot or 0), payload)
            else:
                state = pick_provider_account_slot(db, provider="model_pool")
                child = ModelPoolJob(
                    id=new_id("llm"), user_id=self.user_id,
                    parent_video_job_id=self.job_id,
                    idempotency_key=self.idempotency_key, status="running",
                    request_hash=request_hash, account_slot=state.slot,
                    reserved_credits=0, consumed_credits=0, released_credits=0,
                    response_json=self._submission(payload, 1),
                )
                db.add(child)
                db.flush()
                child_id = child.id
                claimed = (child.id, state.slot, payload)
        if claimed is None:
            return child_id
        self._complete_claim(*claimed)
        return child_id

    def _complete_claim(self, child_id: str, slot: int, payload: dict[str, Any]) -> None:
        fallback_error: tuple[str, bool] | None = None
        try:
            result = model_pool.complete(
                slot=slot, payload=payload, idempotency_key=self.idempotency_key,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "MODEL_POOL_FAILED"))[:64]
            message = str(getattr(exc, "message", None) or str(exc) or "Model request failed")[:500]
            details = getattr(exc, "details", {})
            payment_required = (
                isinstance(details, dict)
                and details.get("upstream_status") == 402
            )
            if code == "UPSTREAM_AUTH_ERROR" or payment_required:
                storyboard = build_fallback_storyboard(payload.get("messages") or [])
                result = {
                    "choices": [{"message": {"content": json.dumps(
                        storyboard, ensure_ascii=False,
                    )}}],
                    "usage": {"total_tokens": 0},
                    "video_storyboard_fallback": True,
                }
                fallback_error = (code, code == "UPSTREAM_AUTH_ERROR")
            else:
                with self._session_factory.begin() as db:
                    child = db.get(ModelPoolJob, child_id, with_for_update=True)
                    if child is not None and child.status == "running":
                        child.status = "failed"
                        child.error_code = code
                        child.response_json = {"error": {"code": code, "message": message}}
                        child.finished_at = utcnow()
                        mark_provider_account_failure(
                            db, provider="model_pool", slot=slot, error_code=code,
                            permanent=code == "UPSTREAM_AUTH_ERROR",
                        )
                raise
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        with self._session_factory.begin() as db:
            child = db.get(ModelPoolJob, child_id, with_for_update=True)
            if child is not None and child.status == "running":
                child.status = "completed"
                child.response_json = result
                child.upstream_usage = usage
                child.finished_at = utcnow()
                if fallback_error is None:
                    mark_provider_account_success(db, provider="model_pool", slot=slot)
                else:
                    mark_provider_account_failure(
                        db, provider="model_pool", slot=slot,
                        error_code=fallback_error[0], permanent=fallback_error[1],
                    )

    @staticmethod
    def _submission(payload: dict[str, Any], attempt: int) -> dict[str, Any]:
        return {
            "submission": {
                "payload": payload,
                "attempt": attempt,
                "lease_expires_at": (
                    utcnow() + timedelta(seconds=settings.pool_worker_lease_seconds)
                ).isoformat(),
            }
        }

    @staticmethod
    def _submission_attempt(value: Any) -> int:
        marker = value.get("submission") if isinstance(value, dict) else None
        try:
            return int(marker.get("attempt") or 0) if isinstance(marker, dict) else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _submission_expired(value: Any) -> bool:
        marker = value.get("submission") if isinstance(value, dict) else None
        raw = marker.get("lease_expires_at") if isinstance(marker, dict) else None
        if not isinstance(raw, str):
            return True
        try:
            deadline = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return deadline <= utcnow()

    def _resume_if_expired(self, provider_id: str) -> None:
        claimed: tuple[str, int, dict[str, Any]] | None = None
        with self._session_factory.begin() as db:
            child = db.scalar(select(ModelPoolJob).where(
                ModelPoolJob.id == provider_id, ModelPoolJob.user_id == self.user_id,
                ModelPoolJob.idempotency_key == self.idempotency_key,
            ).with_for_update())
            if child is None or child.status != "running" or not self._submission_expired(child.response_json):
                return
            marker = child.response_json.get("submission") if isinstance(child.response_json, dict) else {}
            payload = marker.get("payload") if isinstance(marker, dict) else None
            if not isinstance(payload, dict) or json_hash(payload) != child.request_hash:
                child.status = "failed"
                child.error_code = "MODEL_SUBMISSION_LOST"
                child.response_json = {"error": {"code": child.error_code, "message": "Stored model request is unavailable"}}
                child.finished_at = utcnow()
                return
            attempt = self._submission_attempt(child.response_json) + 1
            child.response_json = self._submission(payload, attempt)
            claimed = (child.id, int(child.account_slot or 0), payload)
        if claimed is not None:
            self._complete_claim(*claimed)

    def poll(self, *, provider_id: str) -> Mapping[str, Any]:
        self._resume_if_expired(provider_id)
        with self._session_factory() as db:
            child = db.scalar(select(ModelPoolJob).where(
                ModelPoolJob.id == provider_id, ModelPoolJob.user_id == self.user_id,
                ModelPoolJob.idempotency_key == self.idempotency_key,
            ))
            if child is None:
                return _failed("MODEL_JOB_NOT_FOUND", "Storyboard task was not found")
            if child.status == "completed":
                usage = child.upstream_usage if isinstance(child.upstream_usage, dict) else {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                if prompt_tokens + completion_tokens <= 0:
                    prompt_tokens = max(1, int(usage.get("total_tokens") or 0))
                fallback = bool((child.response_json or {}).get("video_storyboard_fallback"))
                return {
                    "status": "completed", "result": child.response_json or {},
                    "actual_credits": 0,
                }
            if child.status == "failed":
                error = child.response_json.get("error") if isinstance(child.response_json, dict) else {}
                return _failed(child.error_code, str((error or {}).get("message") or "Storyboard failed"))
            return {"status": "pending"}


class VideoTTSProvider:
    def __init__(
        self,
        *,
        user_id: str,
        job_id: str,
        session_factory: SessionFactory = SessionLocal,
    ) -> None:
        self.user_id = user_id
        self.job_id = job_id
        self._session_factory = session_factory
        self.idempotency_key = f"video:{job_id}:tts"

    def submit(
        self,
        *,
        chunks: list[dict[str, Any]],
        voice: Mapping[str, Any],
        audio: Mapping[str, Any],
        action_token: str,
    ) -> str:
        _require_action_token(action_token, f"video:{self.job_id}:tts:tts_submit")
        voice_payload = dict(voice)
        if voice_payload.get("type") == "user":
            voice_payload["type"] = "uploaded"
        request_json = {
            "chunks": chunks,
            "voice": voice_payload,
            "emotion": {
                "name": audio.get("emotion"),
                "weight": audio.get("emotion_weight", 0.65),
            },
            "audio": {
                "speed": audio.get("speed", 1),
                "volume": audio.get("volume", 1),
                "pitch": audio.get("pitch", 0),
                "sample_rate": audio.get("sample_rate", 24000),
                "channels": audio.get("channels", 1),
            },
            "gpu_acceleration": True,
        }
        request_hash = json_hash(request_json)
        with self._session_factory.begin() as db:
            child = db.scalar(select(CloudJob).where(
                CloudJob.user_id == self.user_id,
                CloudJob.client_job_id == self.idempotency_key,
            ).with_for_update())
            if child is not None:
                if json_hash(child.request_json) != request_hash:
                    raise _conflict()
                return child.id
            total_characters = sum(len(str(item["text"])) for item in chunks)
            child = CloudJob(
                id=new_id("job"), user_id=self.user_id,
                parent_video_job_id=self.job_id,
                client_job_id=self.idempotency_key, status=JobStatus.QUEUED.value,
                request_json=request_json, result_json=None,
                total_characters=total_characters, total_chunks=len(chunks),
                completed_chunks=0, progress=0, reserved_credits=0,
                consumed_credits=0, released_credits=0,
                message="Queued by one-click video parent", cancel_requested=False,
            )
            db.add(child)
            db.flush()
            return child.id

    def poll(self, *, provider_id: str) -> Mapping[str, Any]:
        with self._session_factory() as db:
            child = db.scalar(select(CloudJob).where(
                CloudJob.id == provider_id, CloudJob.user_id == self.user_id,
                CloudJob.client_job_id == self.idempotency_key,
            ))
            if child is None:
                return _failed("TTS_JOB_NOT_FOUND", "TTS task was not found")
            if child.status == JobStatus.COMPLETED.value:
                return {
                    "status": "completed", "result": child.result_json or {},
                    "actual_credits": float(quote_credits(child.total_characters)),
                }
            if child.status in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
                return _failed("TTS_JOB_FAILED", child.error_message)
            return {"status": "pending"}

    def stream_chunk(self, *, provider_id: str, index: int) -> Iterable[bytes]:
        with self._session_factory() as db:
            child = db.scalar(select(CloudJob).where(
                CloudJob.id == provider_id, CloudJob.user_id == self.user_id,
                CloudJob.client_job_id == self.idempotency_key,
            ))
            if child is None or child.status != JobStatus.COMPLETED.value or not child.ray_job_id:
                raise ServiceError("TTS_RESULT_NOT_READY", "TTS result is not ready", 409)
            ray_job_id = child.ray_job_id
        return ray_tts.stream_audio(ray_job_id, index)


class VideoImageProvider:
    def __init__(
        self,
        *,
        user_id: str,
        job_id: str,
        resolution: str = "1k",
        method: str = "running",
        size: str | None = None,
        billing_credits: str | None = None,
        reference_image_urls: list[str] | None = None,
        session_factory: SessionFactory = SessionLocal,
    ) -> None:
        self.user_id = user_id
        self.job_id = job_id
        self.resolution = resolution
        self.method, self.size, self.billing_credits = method, size, billing_credits
        self.reference_image_urls = list(reference_image_urls or [])
        self._session_factory = session_factory

    def _key(self, scene_index: int) -> str:
        if scene_index < 0 or scene_index > 15:
            raise ServiceError("INVALID_SCENE_INDEX", "Scene index is out of range", 422)
        return f"video:{self.job_id}:image:{scene_index}"

    def _owns_key(self, value: str) -> bool:
        prefix = f"video:{self.job_id}:image:"
        suffix = value.removeprefix(prefix)
        if not value.startswith(prefix) or not suffix.isdigit():
            return False
        index = int(suffix)
        return 0 <= index <= 15 and suffix == str(index)

    def submit(
        self, *, prompt: str, aspect_ratio: str, scene_index: int, action_token: str,
    ) -> str:
        _require_action_token(
            action_token, f"video:{self.job_id}:images:image_submit:{scene_index}",
        )
        key = self._key(scene_index)
        request_json = {
            "prompt": prompt.strip(), "aspectRatio": aspect_ratio,
            "resolution": self.resolution, "imageUrls": self.reference_image_urls,
        }
        if self.method == "ican":
            request_json = {"provider": "ican", "model": "gpt-image-2.5", "size": self.size,
                            "prompt": prompt.strip(), "imageUrls": self.reference_image_urls}
        request_hash = json_hash(request_json)
        with self._session_factory.begin() as db:
            child = db.scalar(select(ImagePoolJob).where(
                ImagePoolJob.user_id == self.user_id,
                ImagePoolJob.idempotency_key == key,
            ).with_for_update())
            if child is not None:
                if child.request_hash != request_hash:
                    raise _conflict()
                return child.id
            if self.method == "ican":
                request_json["references"] = ican_images.resolve_references(db, self.user_id, self.reference_image_urls)
                request_json["billing"] = {"credits": self.billing_credits or
                    ican_images.price_quote("gpt-image-2.5", self.size)["credits"]}
                request_json.pop("imageUrls")
            child = ImagePoolJob(
                provider="ican" if self.method == "ican" else "runninghub",
                id=new_id("img"), user_id=self.user_id, idempotency_key=key,
                parent_video_job_id=self.job_id,
                status="queued", request_json=request_json, request_hash=request_hash,
                reserved_credits=0, consumed_credits=0, released_credits=0,
            )
            db.add(child)
            db.flush()
            return child.id

    def poll(self, *, provider_id: str) -> Mapping[str, Any]:
        with self._session_factory() as db:
            child = db.scalar(select(ImagePoolJob).where(
                ImagePoolJob.id == provider_id, ImagePoolJob.user_id == self.user_id,
            ))
            if child is None or not self._owns_key(child.idempotency_key):
                return _failed("IMAGE_JOB_NOT_FOUND", "Image task was not found")
            if child.status == "completed":
                return {
                    "status": "completed",
                    "result": {"image_url": child.result_url},
                    "actual_credits": (float(child.request_json["billing"]["credits"]) if child.provider == "ican"
                                       else float(image_pool_actual_credits(child.actual_cost_credits))),
                }
            if child.status == "failed":
                return _failed(child.error_code, child.error_message)
            return {"status": "pending"}

    def stream(self, *, provider_id: str) -> Iterable[bytes]:
        with self._session_factory() as db:
            child = db.scalar(select(ImagePoolJob).where(
                ImagePoolJob.id == provider_id, ImagePoolJob.user_id == self.user_id,
            ))
            if (
                child is None or not self._owns_key(child.idempotency_key)
                or child.status != "completed" or child.account_slot is None
                or not child.result_url
            ):
                raise ServiceError("IMAGE_RESULT_NOT_READY", "Image result is not ready", 409)
            slot, result_url = child.account_slot, child.result_url
            if child.provider == "ican":
                return iter([ican_images.read_image(result_url)])
        chunks, _ = runninghub_image.stream_result(slot=slot, url=result_url)
        return chunks


# Explicit aliases keep construction readable at the VideoPipeline integration point.
ModelPoolStoryboardProvider = VideoStoryboardProvider
RayTTSVideoProvider = VideoTTSProvider
RunningHubVideoImageProvider = VideoImageProvider
