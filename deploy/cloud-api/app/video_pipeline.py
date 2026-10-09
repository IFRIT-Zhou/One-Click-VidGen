from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import errno
from pathlib import Path
import socket
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    VideoAsset, VideoJob, VideoJobStatus, VideoJobStep, VideoStepStatus, new_id, utcnow,
)
from .services import (
    VIDEO_STEP_JOB_STATES,
    cancel_video_children,
    release_video_job_credits,
    transition_video_job,
)
from .video_renderer import RenderResult, render_mp4
from .video_storage import LocalVideoStorage, StoredVideoAsset
from .video_storyboard import build_storyboard_messages, completion_content, parse_storyboard


_TRANSIENT_ERROR_CODES = {
    "TIMEOUT",
    "TIMED_OUT",
    "NETWORK_ERROR",
    "CONNECTION_ERROR",
    "UPSTREAM_TIMEOUT",
    "UPSTREAM_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "MINIO_TIMEOUT",
    "STORAGE_TIMEOUT",
}
_TRANSIENT_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporarily unavailable",
    "service unavailable",
    "network is unreachable",
    "host is unreachable",
    "broken pipe",
    "read operation timed out",
    "write operation timed out",
)
_NETWORK_ERRNOS = {
    errno.ETIMEDOUT,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.EPIPE,
    errno.ENETDOWN,
    errno.ENETRESET,
    errno.ENETUNREACH,
    errno.EHOSTDOWN,
    errno.EHOSTUNREACH,
}


def _is_retryable_io_error(exc: BaseException) -> bool:
    """Identify transient transport/storage failures without retrying bad input.

    Provider failures have their own durable state machine. Retrying those at
    the video-parent level could duplicate a provider submission or charge.
    """
    if isinstance(exc, _ProviderFailure):
        return False
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in _NETWORK_ERRNOS:
        return True
    code = str(getattr(exc, "code", "") or "").upper()
    if code in _TRANSIENT_ERROR_CODES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


class StoryboardProvider(Protocol):
    def submit(self, *, messages: list[dict[str, str]], action_token: str) -> str: ...

    def poll(self, *, provider_id: str) -> Mapping[str, Any]: ...


class TTSProvider(Protocol):
    def submit(
        self, *, chunks: list[dict[str, Any]], voice: Mapping[str, Any],
        audio: Mapping[str, Any], action_token: str,
    ) -> str: ...

    def poll(self, *, provider_id: str) -> Mapping[str, Any]: ...

    def stream_chunk(self, *, provider_id: str, index: int) -> Iterable[bytes]: ...


class ImageProvider(Protocol):
    def submit(
        self, *, prompt: str, aspect_ratio: str, scene_index: int, action_token: str,
    ) -> str: ...

    def poll(self, *, provider_id: str) -> Mapping[str, Any]: ...

    def stream(self, *, provider_id: str) -> Iterable[bytes]: ...


class VideoPipelineError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class AdvanceResult:
    job_id: str
    job_status: str
    current_step: str | None
    action: str


@dataclass(frozen=True)
class _Action:
    token: str
    execution_id: str
    owner: str
    version: int
    kind: str
    job_id: str
    user_id: str
    step_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class _ProviderFailure(Exception):
    code: str
    message: str
    details: dict[str, Any]


class VideoPipeline:
    """Crash-recoverable orchestration with no database transaction held during I/O."""

    def __init__(
        self, *, session_factory: Callable[[], Session], storage: LocalVideoStorage,
        storyboard_provider: StoryboardProvider, tts_provider: TTSProvider,
        image_provider: ImageProvider, renderer: Callable[..., RenderResult] = render_mp4,
        burn_subtitles: bool = True, worker_id: str | None = None,
        action_lease_seconds: float = 60, compose_lease_seconds: float = 1800,
        step_max_retries: int = 2, retry_backoff_seconds: float = 2.0,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if action_lease_seconds <= 0 or compose_lease_seconds <= 0:
            raise ValueError("Pipeline lease durations must be positive")
        if step_max_retries < 0:
            raise ValueError("step_max_retries must not be negative")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        if not worker_id:
            worker_id = f"video-worker-{uuid4().hex}"
        self._session_factory = session_factory
        self._storage = storage
        self._storyboard = storyboard_provider
        self._tts = tts_provider
        self._images = image_provider
        self._renderer = renderer
        self._burn_subtitles = burn_subtitles
        self._worker_id = worker_id
        self._action_lease_seconds = action_lease_seconds
        self._compose_lease_seconds = max(compose_lease_seconds, action_lease_seconds)
        self._step_max_retries = int(step_max_retries)
        self._retry_backoff_seconds = float(retry_backoff_seconds)
        self._clock = clock

    def advance(self, job_id: str) -> AdvanceResult:
        claimed = self._claim(job_id)
        if isinstance(claimed, AdvanceResult):
            return claimed
        try:
            outcome = self._execute(claimed)
        except Exception as exc:
            return self._persist_failure(claimed, exc)
        return self._persist(claimed, outcome)

    def _claim(self, job_id: str) -> _Action | AdvanceResult:
        with self._session_factory() as db, db.begin():
            job = db.scalar(select(VideoJob).where(VideoJob.id == job_id).with_for_update())
            if job is None:
                raise VideoPipelineError(
                    "VIDEO_JOB_NOT_FOUND", "Video job does not exist", job_id=job_id
                )
            terminal = {
                VideoJobStatus.COMPLETED.value, VideoJobStatus.FAILED.value,
                VideoJobStatus.CANCELLED.value,
            }
            if job.status in terminal:
                return self._result(job, "terminal")
            if job.cancel_requested or job.status == VideoJobStatus.CANCEL_REQUESTED.value:
                self._cancel(db, job)
                return self._result(job, "cancelled")
            step = self._current_step(db, job)
            if isinstance(step, AdvanceResult):
                return step
            # The retry deadline is stored with the step, so restarts cannot
            # turn a short storage outage into a hot retry loop.
            state = dict(step.output_json or {})
            retry_after = self._parse_deadline(state.get("retry_after"))
            if retry_after is not None and retry_after > self._now():
                return self._result(job, "retry_waiting")
            if retry_after is not None:
                state.pop("retry_after", None)
                step.output_json = state
                job.lease_expires_at = None
            self._start(job, step)
            state = dict(step.output_json or {})
            pending = state.get("pending_action")
            if isinstance(pending, Mapping):
                claimed = self._claim_pending(db, job, step, state, pending)
                if isinstance(claimed, AdvanceResult):
                    return claimed
                return claimed

            kind = self._next_action_kind(db, job, step, state)
            if isinstance(kind, AdvanceResult):
                return kind
            token = self._token(job.id, step.step_key, kind, state)
            pending = self._new_execution(kind=kind, token=token, version=1)
            state["pending_action"] = pending
            step.output_json = state
            return self._action(db, job, step, pending)

    def _claim_pending(
        self, db: Session, job: VideoJob, step: VideoJobStep,
        state: dict[str, Any], pending: Mapping[str, Any],
    ) -> _Action | AdvanceResult:
        kind, token = str(pending.get("kind") or ""), str(pending.get("token") or "")
        if not kind or not token:
            return self._fail_locked(
                db, job, step,
                VideoPipelineError("INVALID_PENDING_ACTION", "Pending action is incomplete"),
            )
        owner = str(pending.get("owner") or "")
        deadline = self._parse_deadline(pending.get("lease_expires_at"))
        now = self._now()
        if owner and owner != self._worker_id and deadline is not None and deadline > now:
            return self._result(job, "action_in_progress")
        if owner == self._worker_id:
            claimed = dict(pending)
            claimed["lease_expires_at"] = self._lease_deadline(kind).isoformat()
        else:
            claimed = self._new_execution(
                kind=kind, token=token, version=int(pending.get("version") or 0) + 1,
            )
        state["pending_action"] = claimed
        step.output_json = state
        return self._action(db, job, step, claimed)

    def _new_execution(self, *, kind: str, token: str, version: int) -> dict[str, Any]:
        return {
            "kind": kind,
            "token": token,
            "owner": self._worker_id,
            "execution_id": uuid4().hex,
            "version": version,
            "lease_expires_at": self._lease_deadline(kind).isoformat(),
        }

    def _lease_deadline(self, kind: str) -> datetime:
        seconds = self._compose_lease_seconds if kind == "compose" else self._action_lease_seconds
        return self._now() + timedelta(seconds=seconds)

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _parse_deadline(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _current_step(self, db: Session, job: VideoJob) -> VideoJobStep | AdvanceResult:
        if not job.current_step:
            return self._fail_locked(
                db, job, None,
                VideoPipelineError("INVALID_PIPELINE_STATE", "Active job has no current step"),
            )
        step = db.scalar(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job.id,
            VideoJobStep.step_key == job.current_step,
        ).with_for_update())
        if step is None:
            return self._fail_locked(
                db, job, None,
                VideoPipelineError(
                    "MISSING_PIPELINE_STEP", "Current pipeline step does not exist",
                    step=job.current_step,
                ),
            )
        return step

    @staticmethod
    def _start(job: VideoJob, step: VideoJobStep) -> None:
        if step.status == VideoStepStatus.PENDING.value:
            step.status = VideoStepStatus.RUNNING.value
            step.attempt_count += 1
            step.started_at = utcnow()
        job.started_at = job.started_at or utcnow()
        job.status = VIDEO_STEP_JOB_STATES.get(step.step_key, job.status)

    def _next_action_kind(
        self, db: Session, job: VideoJob, step: VideoJobStep, state: dict[str, Any]
    ) -> str | AdvanceResult:
        if step.step_key == "storyboard":
            return "storyboard_poll" if state.get("provider_id") else "storyboard_submit"
        if step.step_key == "tts":
            if not state.get("provider_id"):
                return "tts_submit"
            if not state.get("provider_ready"):
                return "tts_poll"
            scenes = self._scenes(db, job.id)
            downloaded = {int(value) for value in state.get("downloaded", [])}
            missing = [int(scene["index"]) for scene in scenes if int(scene["index"]) not in downloaded]
            if missing:
                return f"tts_download:{missing[0]}"
            self._complete_asset_step(db, job, step, state)
            return self._result(job, "tts_completed")
        if step.step_key == "images":
            scenes = self._scenes(db, job.id)
            provider_ids = dict(state.get("provider_ids") or {})
            for scene in scenes:
                index = str(int(scene["index"]))
                if index not in provider_ids:
                    return "image_submit_batch"
            ready = {str(value) for value in state.get("ready", [])}
            for scene in scenes:
                index = str(int(scene["index"]))
                if index not in ready:
                    return "image_poll_batch"
            downloaded = {str(value) for value in state.get("downloaded", [])}
            for scene in scenes:
                index = str(int(scene["index"]))
                if index not in downloaded:
                    return "image_download_batch"
            self._complete_asset_step(db, job, step, state)
            return self._result(job, "images_completed")
        if step.step_key == "compose":
            existing = self._asset(db, job.id, "final_video")
            if existing is not None:
                self._complete_step(
                    db, job, step,
                    {"asset_key": existing.asset_key, "object_key": existing.uri},
                )
                return self._result(job, "compose_recovered")
            return "compose"
        if step.step_key == "publish":
            asset = self._required_asset(db, job.id, "final_video")
            metadata = dict(asset.metadata_json or {})
            actual_credits = self._actual_asset_credits(db, job.id)
            self._complete_step(db, job, step, {"final_video": {
                "asset_key": asset.asset_key, "object_key": asset.uri,
                "size_bytes": metadata.get("size_bytes"), "sha256": metadata.get("sha256"),
                "duration_seconds": metadata.get("duration_seconds"),
            }, "actual_credits": float(actual_credits)})
            return self._result(job, "published")
        return self._fail_locked(
            db, job, step,
            VideoPipelineError(
                "UNKNOWN_PIPELINE_STEP", "Pipeline step is unsupported", step=step.step_key
            ),
        )

    @staticmethod
    def _token(job_id: str, step_key: str, kind: str, state: Mapping[str, Any]) -> str:
        # Kind includes scene/chunk ordinal, making provider submissions naturally idempotent.
        return f"video:{job_id}:{step_key}:{kind}"

    def _execute(self, action: _Action) -> dict[str, Any]:
        kind, payload = action.kind, action.payload
        request, state = payload["request"], payload["state"]
        scenes = payload.get("scenes") or []
        if kind == "storyboard_submit":
            video = request["video"]
            if request.get("storyboard") is not None:
                planned = parse_storyboard(
                    {"scenes": request["storyboard"]}, source_script=str(request["script"]),
                    expected_scene_count=int(video["scene_count"]),
                )
                return {"scenes": planned, "actual_credits": 0, "source": "provided"}
            provider_id = self._storyboard.submit(
                messages=build_storyboard_messages(
                    script=str(request["script"]), scene_count=int(video["scene_count"]),
                    visual_style=str(video.get("visual_style") or "cinematic"),
                    aspect_ratio=str(video["aspect_ratio"]),
                ),
                action_token=action.token,
            )
            return {"provider_id": self._provider_id(provider_id)}
        if kind == "storyboard_poll":
            polled = self._poll(self._storyboard.poll(provider_id=str(state["provider_id"])))
            if polled["status"] == "completed":
                raw = polled.get("result")
                if isinstance(raw, dict) and "choices" in raw:
                    raw = completion_content(raw)
                polled["scenes"] = parse_storyboard(
                    raw, source_script=str(request["script"]),
                    expected_scene_count=int(request["video"]["scene_count"]),
                )
            return polled
        if kind == "tts_submit":
            provider_id = self._tts.submit(
                chunks=[{"index": int(scene["index"]), "text": str(scene["narration"])} for scene in scenes],
                voice=dict(request["voice"]), audio=dict(request.get("audio") or {}),
                action_token=action.token,
            )
            return {"provider_id": self._provider_id(provider_id)}
        if kind == "tts_poll":
            return self._poll(self._tts.poll(provider_id=str(state["provider_id"])))
        if kind.startswith("tts_download:"):
            index = int(kind.rsplit(":", 1)[1])
            stored = self._storage.write_chunks(
                user_id=action.user_id, job_id=action.job_id, kind="audio",
                filename=f"scene-{index:03d}.audio",
                chunks=self._tts.stream_chunk(provider_id=str(state["provider_id"]), index=index),
            )
            return {"index": index, "stored": stored}
        if kind.startswith("image_submit:"):
            index = int(kind.rsplit(":", 1)[1])
            scene = next(scene for scene in scenes if int(scene["index"]) == index)
            provider_id = self._images.submit(
                prompt=str(scene["prompt"]), aspect_ratio=str(request["video"]["aspect_ratio"]),
                scene_index=index, action_token=action.token,
            )
            return {"index": index, "provider_id": self._provider_id(provider_id)}
        if kind == "image_submit_batch":
            pending = [
                scene for scene in scenes
                if str(int(scene["index"])) not in (state.get("provider_ids") or {})
            ]

            def submit(scene: Mapping[str, Any]) -> dict[str, Any]:
                index = int(scene["index"])
                provider_id = self._images.submit(
                    prompt=str(scene["prompt"]),
                    aspect_ratio=str(request["video"]["aspect_ratio"]),
                    scene_index=index,
                    # Keep the established per-scene idempotency contract even
                    # though the durable pipeline action is now batched.
                    action_token=self._token(action.job_id, "images", f"image_submit:{index}", state),
                )
                return {"index": index, "provider_id": self._provider_id(provider_id)}

            with ThreadPoolExecutor(max_workers=max(1, min(16, len(pending)))) as pool:
                items = list(pool.map(submit, pending))
            return {"items": items}
        if kind.startswith("image_poll:"):
            index = int(kind.rsplit(":", 1)[1])
            polled = self._poll(self._images.poll(
                provider_id=str(state["provider_ids"][str(index)])
            ))
            return {"index": index, **polled}
        if kind == "image_poll_batch":
            provider_ids = dict(state.get("provider_ids") or {})
            ready = {str(value) for value in state.get("ready", [])}
            pending = [
                (int(scene["index"]), str(provider_ids[str(int(scene["index"]))]))
                for scene in scenes
                if str(int(scene["index"])) not in ready
            ]

            def poll_one(item: tuple[int, str]) -> dict[str, Any]:
                index, provider_id = item
                return {"index": index, **self._poll(self._images.poll(provider_id=provider_id))}

            with ThreadPoolExecutor(max_workers=max(1, min(16, len(pending)))) as pool:
                items = list(pool.map(poll_one, pending))
            return {"items": items}
        if kind.startswith("image_download:"):
            index = int(kind.rsplit(":", 1)[1])
            provider_id = str(state["provider_ids"][str(index)])
            stored = self._storage.write_chunks(
                user_id=action.user_id, job_id=action.job_id, kind="image",
                filename=f"scene-{index:03d}.image", chunks=self._images.stream(provider_id=provider_id),
            )
            return {"index": index, "provider_id": provider_id, "stored": stored}
        if kind == "image_download_batch":
            provider_ids = dict(state.get("provider_ids") or {})
            ready = {str(value) for value in state.get("ready", [])}
            downloaded = {str(value) for value in state.get("downloaded", [])}
            pending = [
                int(scene["index"]) for scene in scenes
                if str(int(scene["index"])) in ready
                and str(int(scene["index"])) not in downloaded
            ]

            def download(index: int) -> dict[str, Any]:
                provider_id = str(provider_ids[str(index)])
                stored = self._storage.write_chunks(
                    user_id=action.user_id, job_id=action.job_id, kind="image",
                    filename=f"scene-{index:03d}.image",
                    chunks=self._images.stream(provider_id=provider_id),
                )
                return {"index": index, "provider_id": provider_id, "stored": stored}

            with ThreadPoolExecutor(max_workers=max(1, min(16, len(pending)))) as pool:
                items = list(pool.map(download, pending))
            return {"items": items}
        if kind == "compose":
            return self._compose(action, scenes, request)
        raise VideoPipelineError("UNKNOWN_PIPELINE_ACTION", "Pipeline action is unsupported", action=kind)

    def _compose(
        self, action: _Action, scenes: list[dict[str, Any]], request: dict[str, Any]
    ) -> dict[str, Any]:
        # Asset rows were snapshotted into action payload by _action; resolve paths without DB I/O.
        asset_keys = action.payload["assets"]
        render_scenes = [{
            "image_path": self._storage.resolve_object(asset_keys[f"image:{int(scene['index'])}"]),
            "audio_path": self._storage.resolve_object(asset_keys[f"audio:{int(scene['index'])}"]),
            "narration": str(scene["narration"]),
        } for scene in scenes]
        task_dir = self._storage.job_dir(user_id=action.user_id, job_id=action.job_id, create=True)
        intermediate = self._storage.asset_path(
            user_id=action.user_id, job_id=action.job_id, kind="intermediate",
            filename=f"composed-{action.execution_id}.mp4", create=True,
        )
        rendered = self._renderer(
            task_dir, render_scenes, str(request["video"]["aspect_ratio"]), intermediate,
            burn_subtitles=self._burn_subtitles,
        )
        stored = self._storage.write_chunks(
            user_id=action.user_id, job_id=action.job_id, kind="final_video",
            filename=f"final-{action.execution_id}.mp4",
            chunks=self._file_chunks(Path(rendered.output_path)),
        )
        return {"stored": stored, "duration_seconds": float(rendered.duration_seconds)}

    def _persist(self, action: _Action, outcome: dict[str, Any]) -> AdvanceResult:
        with self._session_factory() as db, db.begin():
            job, step, state = self._locked_action(db, action)
            if job is None:
                return AdvanceResult(action.job_id, "unknown", None, "stale_action")
            if job.cancel_requested or job.status == VideoJobStatus.CANCEL_REQUESTED.value:
                self._cancel(db, job)
                return self._result(job, "cancelled")
            state.pop("pending_action", None)
            kind = action.kind
            if kind == "storyboard_submit" and outcome.get("source") == "provided":
                self._complete_step(db, job, step, outcome)
                return self._result(job, "storyboard_completed")
            if kind.endswith("_submit") or kind.startswith("image_submit:") or kind == "image_submit_batch":
                if kind == "storyboard_submit" or kind == "tts_submit":
                    state["provider_id"] = outcome["provider_id"]
                    if kind == "tts_submit":
                        state.update({"provider_ready": False, "downloaded": []})
                else:
                    ids = dict(state.get("provider_ids") or {})
                    items = outcome.get("items") if kind == "image_submit_batch" else [outcome]
                    for item in items:
                        ids[str(item["index"])] = item["provider_id"]
                    state.update({"provider_ids": ids, "ready": state.get("ready", []),
                                  "downloaded": state.get("downloaded", [])})
                step.output_json = state
                if step.step_key in {"tts", "images"}:
                    self._rotate_asset_step(db, job, step)
                action_name = (
                    "image_submitted"
                    if kind.startswith("image_submit:") or kind == "image_submit_batch"
                    else kind.replace("_submit", "_submitted")
                )
                return self._result(job, action_name)
            if kind == "storyboard_poll":
                if outcome["status"] == "pending":
                    step.output_json = state
                    return self._result(job, "storyboard_waiting")
                self._complete_step(db, job, step, {
                    "provider_id": state["provider_id"], "scenes": outcome["scenes"],
                    "actual_credits": outcome["actual_credits"],
                })
                return self._result(job, "storyboard_completed")
            if kind == "tts_poll":
                if outcome["status"] == "pending":
                    step.output_json = state
                    self._rotate_asset_step(db, job, step)
                    return self._result(job, "tts_waiting")
                state["provider_ready"] = True
                state["provider_result"] = outcome.get("result") if isinstance(outcome.get("result"), Mapping) else {}
                state["actual_credits"] = outcome["actual_credits"]
                step.output_json = state
                self._rotate_asset_step(db, job, step)
                return self._result(job, "tts_ready")
            if kind.startswith("tts_download:"):
                index, stored = int(outcome["index"]), outcome["stored"]
                self._record_asset(db, job, step, key=f"audio:{index}", kind="audio", ordinal=index,
                                   provider="tts", upstream_id=str(state["provider_id"]), stored=stored)
                state["downloaded"] = sorted({int(v) for v in state.get("downloaded", [])} | {index})
                step.output_json = state
                self._rotate_asset_step(db, job, step)
                return self._result(job, "tts_chunk_downloaded")
            if kind.startswith("image_poll:") or kind == "image_poll_batch":
                items = outcome.get("items") if kind == "image_poll_batch" else [outcome]
                for item in items:
                    if item["status"] == "pending":
                        continue
                    index = str(item["index"])
                    state["ready"] = sorted({str(v) for v in state.get("ready", [])} | {index}, key=int)
                    state["actual_credits"] = float(
                        Decimal(str(state.get("actual_credits") or 0))
                        + Decimal(str(item["actual_credits"]))
                    )
                if any(item["status"] == "pending" for item in items):
                    step.output_json = state
                    self._rotate_asset_step(db, job, step)
                    return self._result(job, "images_waiting")
                step.output_json = state
                self._rotate_asset_step(db, job, step)
                return self._result(job, "images_ready")
            if kind.startswith("image_poll:"):
                if outcome["status"] == "pending":
                    step.output_json = state
                    self._rotate_asset_step(db, job, step)
                    return self._result(job, "images_waiting")
                index = str(outcome["index"])
                state["ready"] = sorted({str(v) for v in state.get("ready", [])} | {index}, key=int)
                state["actual_credits"] = float(
                    Decimal(str(state.get("actual_credits") or 0))
                    + Decimal(str(outcome["actual_credits"]))
                )
                step.output_json = state
                self._rotate_asset_step(db, job, step)
                return self._result(job, "image_ready")
            if kind == "image_download_batch":
                for item in outcome.get("items", []):
                    self._record_asset(db, job, step, key=f"image:{int(item['index'])}", kind="image",
                                       ordinal=int(item["index"]), provider="image",
                                       upstream_id=item["provider_id"], stored=item["stored"])
                    state["downloaded"] = sorted(
                        {str(v) for v in state.get("downloaded", [])} | {str(item["index"])}, key=int,
                    )
                step.output_json = state
                self._rotate_asset_step(db, job, step)
                return self._result(job, "images_downloaded")
            if kind.startswith("image_download:"):
                index, stored = int(outcome["index"]), outcome["stored"]
                self._record_asset(db, job, step, key=f"image:{index}", kind="image", ordinal=index,
                                   provider="image", upstream_id=outcome["provider_id"], stored=stored)
                state["downloaded"] = sorted({str(v) for v in state.get("downloaded", [])} | {str(index)}, key=int)
                step.output_json = state
                self._rotate_asset_step(db, job, step)
                return self._result(job, "image_downloaded")
            if kind == "compose":
                stored = outcome["stored"]
                asset = self._record_asset(
                    db, job, step, key="final_video", kind="final_video", ordinal=0,
                    provider="local_ffmpeg", upstream_id=None, stored=stored,
                    metadata={"duration_seconds": outcome["duration_seconds"]},
                )
                self._complete_step(db, job, step, {
                    "asset_key": asset.asset_key, "object_key": asset.uri,
                })
                return self._result(job, "composed")
            raise VideoPipelineError("UNKNOWN_PIPELINE_ACTION", "Cannot persist pipeline action")

    def _persist_failure(self, action: _Action, exc: Exception) -> AdvanceResult:
        with self._session_factory() as db, db.begin():
            job, step, _ = self._locked_action(db, action)
            if job is None:
                return AdvanceResult(action.job_id, "unknown", None, "stale_action")
            if job.cancel_requested or job.status == VideoJobStatus.CANCEL_REQUESTED.value:
                self._cancel(db, job)
                return self._result(job, "cancelled")
            if (
                step is not None
                and _is_retryable_io_error(exc)
                and step.attempt_count <= self._step_max_retries
            ):
                return self._schedule_retry_locked(db, job, step, exc)
            return self._fail_locked(db, job, step, exc)

    def _locked_action(
        self, db: Session, action: _Action
    ) -> tuple[VideoJob | None, VideoJobStep | None, dict[str, Any]]:
        job = db.scalar(select(VideoJob).where(VideoJob.id == action.job_id).with_for_update())
        if job is None or job.current_step != action.step_key:
            return None, None, {}
        step = db.scalar(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job.id, VideoJobStep.step_key == action.step_key,
        ).with_for_update())
        state = dict(step.output_json or {}) if step else {}
        pending = state.get("pending_action")
        if (
            step is None
            or not isinstance(pending, Mapping)
            or pending.get("token") != action.token
            or pending.get("execution_id") != action.execution_id
            or pending.get("owner") != action.owner
            or int(pending.get("version") or 0) != action.version
        ):
            return None, None, {}
        return job, step, state

    def _action(
        self, db: Session, job: VideoJob, step: VideoJobStep, pending: Mapping[str, Any]
    ) -> _Action:
        payload: dict[str, Any] = {"request": dict(job.request_json), "state": dict(step.output_json or {})}
        if step.step_key != "storyboard":
            payload["scenes"] = self._scenes(db, job.id)
            if step.step_key == "compose":
                assets = db.scalars(select(VideoAsset).where(
                    VideoAsset.video_job_id == job.id, VideoAsset.status == "ready",
                )).all()
                payload["assets"] = {asset.asset_key: str(asset.uri) for asset in assets}
        return _Action(
            token=str(pending["token"]),
            execution_id=str(pending["execution_id"]),
            owner=str(pending["owner"]),
            version=int(pending["version"]),
            kind=str(pending["kind"]),
            job_id=job.id,
            user_id=job.user_id,
            step_key=step.step_key,
            payload=payload,
        )

    @staticmethod
    def _provider_id(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise VideoPipelineError("INVALID_PROVIDER_ID", "Provider returned invalid task id")
        return value.strip()

    @staticmethod
    def _poll(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise VideoPipelineError("INVALID_PROVIDER_RESPONSE", "Poll response must be an object")
        status = str(value.get("status") or "").lower()
        if status in {"queued", "pending", "running", "processing"}:
            return {"status": "pending"}
        if status in {"completed", "succeeded", "ready"}:
            raw_credits = value.get("actual_credits", 0)
            if isinstance(raw_credits, bool):
                raise VideoPipelineError(
                    "INVALID_PROVIDER_RESPONSE", "Provider actual_credits must be a non-negative number"
                )
            try:
                actual_credits = Decimal(str(raw_credits))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise VideoPipelineError(
                    "INVALID_PROVIDER_RESPONSE", "Provider actual_credits must be a non-negative number"
                ) from exc
            if actual_credits < 0:
                raise VideoPipelineError(
                    "INVALID_PROVIDER_RESPONSE", "Provider actual_credits must be a non-negative number"
                )
            return {
                "status": "completed", "result": value.get("result"),
                "actual_credits": float(actual_credits),
            }
        if status in {"failed", "error", "cancelled"}:
            error = value.get("error") if isinstance(value.get("error"), Mapping) else {}
            details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
            raise _ProviderFailure(
                str(error.get("code") or value.get("code") or "PROVIDER_FAILED"),
                str(error.get("message") or value.get("message") or "Provider task failed"),
                dict(details),
            )
        raise VideoPipelineError(
            "INVALID_PROVIDER_RESPONSE", "Poll response has unknown status", status=status
        )

    @staticmethod
    def _scenes(db: Session, job_id: str) -> list[dict[str, Any]]:
        step = db.scalar(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job_id, VideoJobStep.step_key == "storyboard",
        ))
        scenes = (step.output_json or {}).get("scenes") if step else None
        if not isinstance(scenes, list) or not scenes:
            raise VideoPipelineError("STORYBOARD_NOT_AVAILABLE", "Storyboard scenes unavailable")
        return scenes

    @staticmethod
    def _asset(db: Session, job_id: str, asset_key: str) -> VideoAsset | None:
        return db.scalar(select(VideoAsset).where(
            VideoAsset.video_job_id == job_id, VideoAsset.asset_key == asset_key,
        ))

    def _required_asset(self, db: Session, job_id: str, asset_key: str) -> VideoAsset:
        asset = self._asset(db, job_id, asset_key)
        if asset is None or asset.status != "ready" or not asset.uri:
            raise VideoPipelineError("ASSET_NOT_AVAILABLE", "Required asset unavailable", asset_key=asset_key)
        return asset

    @staticmethod
    def _actual_asset_credits(db: Session, job_id: str) -> Decimal:
        steps = db.scalars(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job_id,
            VideoJobStep.step_key.in_(["storyboard", "tts", "images"]),
        )).all()
        return sum(
            (Decimal(str((step.output_json or {}).get("actual_credits") or 0)) for step in steps),
            Decimal(0),
        )

    @staticmethod
    def _complete_step(
        db: Session, job: VideoJob, step: VideoJobStep, output: dict[str, Any]
    ) -> None:
        transition_video_job(db, job=job, step=step, output=output)

    def _rotate_asset_step(self, db: Session, job: VideoJob, step: VideoJobStep) -> None:
        other_key = "images" if step.step_key == "tts" else "tts"
        other = db.scalar(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job.id, VideoJobStep.step_key == other_key,
        ))
        if other is not None and other.status != VideoStepStatus.COMPLETED.value:
            job.current_step = other_key
            job.status = VideoJobStatus.GENERATING_ASSETS.value
            job.progress = max(job.progress, 20)

    def _complete_asset_step(
        self, db: Session, job: VideoJob, step: VideoJobStep, output: dict[str, Any]
    ) -> None:
        step.status = VideoStepStatus.COMPLETED.value
        step.output_json = output
        step.finished_at = utcnow()
        other_key = "images" if step.step_key == "tts" else "tts"
        other = db.scalar(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job.id, VideoJobStep.step_key == other_key,
        ))
        if other is not None and other.status == VideoStepStatus.COMPLETED.value:
            job.current_step = "compose"
            job.status = VideoJobStatus.COMPOSING.value
            job.progress = 60
        else:
            job.current_step = other_key
            job.status = VideoJobStatus.GENERATING_ASSETS.value
            job.progress = max(job.progress, 40)

    def _record_asset(
        self, db: Session, job: VideoJob, step: VideoJobStep, *, key: str, kind: str,
        ordinal: int, provider: str, upstream_id: str | None, stored: StoredVideoAsset,
        metadata: Mapping[str, Any] | None = None,
    ) -> VideoAsset:
        existing = self._asset(db, job.id, key)
        if existing is not None:
            return existing
        asset = VideoAsset(
            id=new_id("vasset"), video_job_id=job.id, step_id=step.id, asset_key=key,
            kind=kind, ordinal=ordinal, status="ready", provider=provider,
            upstream_id=upstream_id, uri=stored.object_key,
            metadata_json={"size_bytes": stored.size_bytes, "sha256": stored.sha256,
                           **dict(metadata or {})},
        )
        db.add(asset)
        db.flush()
        return asset

    def _schedule_retry_locked(
        self,
        db: Session,
        job: VideoJob,
        step: VideoJobStep,
        exc: Exception,
    ) -> AdvanceResult:
        """Requeue one transient step while retaining completed assets and credits."""
        previous = dict(step.output_json or {})
        previous.pop("pending_action", None)
        retry_number = int(step.attempt_count)
        delay = min(
            60.0,
            self._retry_backoff_seconds * (2 ** max(0, retry_number - 1)),
        )
        retry_after = self._now() + timedelta(seconds=delay)
        code = str(getattr(exc, "code", "STORAGE_TRANSIENT_ERROR"))[:64]
        message = str(getattr(exc, "message", None) or str(exc) or type(exc).__name__)
        previous["last_retry_error"] = {
            "code": code,
            "message": message[:2000],
            "retry": retry_number,
        }
        previous["retry_after"] = retry_after.isoformat()
        step.output_json = previous
        step.status = VideoStepStatus.PENDING.value
        step.error_code = "RETRYING"
        step.error_message = (
            f"Transient storage/network error; retry {retry_number}/"
            f"{self._step_max_retries} scheduled"
        )[:2000]
        step.finished_at = None
        step.lease_expires_at = None
        job.status = VIDEO_STEP_JOB_STATES.get(step.step_key, job.status)
        job.current_step = step.step_key
        job.lease_expires_at = retry_after
        job.error_code = None
        job.error_message = None
        return self._result(job, "retry_scheduled")

    @staticmethod
    def _file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> Iterable[bytes]:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def _cancel(self, db: Session, job: VideoJob) -> None:
        cancel_video_children(db, job)
        job.status, job.current_step, job.finished_at = VideoJobStatus.CANCELLED.value, None, utcnow()
        job.result_json = {"status": "cancelled"}
        for step in db.scalars(select(VideoJobStep).where(
            VideoJobStep.video_job_id == job.id,
            VideoJobStep.status.in_([VideoStepStatus.PENDING.value, VideoStepStatus.RUNNING.value]),
        )):
            step.status, step.finished_at = VideoStepStatus.CANCELLED.value, utcnow()
        release_video_job_credits(db, job)

    def _fail_locked(
        self, db: Session, job: VideoJob, step: VideoJobStep | None, exc: Exception
    ) -> AdvanceResult:
        code = str(getattr(exc, "code", "VIDEO_PIPELINE_FAILED"))[:64]
        message = str(getattr(exc, "message", None) or str(exc) or "Pipeline failed")[:2000]
        raw_details = getattr(exc, "details", {})
        details = dict(raw_details) if isinstance(raw_details, Mapping) else {}
        error = {"code": code, "message": message, "details": details}
        if step is not None:
            previous = dict(step.output_json or {})
            previous.pop("pending_action", None)
            step.status, step.error_code, step.error_message = VideoStepStatus.FAILED.value, code, message
            step.output_json, step.finished_at = {**previous, "error": error}, utcnow()
        job.status, job.current_step = VideoJobStatus.FAILED.value, None
        job.error_code, job.error_message = code, message
        job.result_json, job.finished_at = {"error": error}, utcnow()
        release_video_job_credits(db, job)
        return self._result(job, "failed")

    @staticmethod
    def _result(job: VideoJob, action: str) -> AdvanceResult:
        return AdvanceResult(job.id, job.status, job.current_step, action)
