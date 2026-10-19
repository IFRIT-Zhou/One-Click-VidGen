"""Durable single-node H3 queue runner, called under the scheduler's process lock.

Transport errors never cause a second submit. Recovery only polls the original
persisted attempt. The scheduler MUST retain exclusive GPU ownership if this
runner raises WorkerUnsafeState, and must not resume TTS until node idle is known.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
import uuid
from datetime import timedelta

from sqlalchemy import select

from .cloud_video_api import CloudVideoAsset, CloudVideoJob, TERMINAL, aware, save_bytes, storage_path, utcnow
from .db import SessionLocal


class WorkerUnsafeState(RuntimeError):
    """Node ownership or execution outcome could not be made safe."""


NODE_ERRORS = {
    'WORKER_RESTARTED': 'GPU 节点或 WSL 已重启，原视频任务中断；请手动重试。',
    'WORKER_LOST': 'GPU 视频引擎已停止，原任务中断；请手动重试。',
    'INVALID_WORKFLOW': 'GPU 节点工作流校验失败，请联系管理员检查节点配置。',
    'ENGINE_ERROR': 'H3 推理失败，请联系管理员查看 GPU 节点日志。',
    'RESULT_VALIDATION_FAILED': '视频输出校验失败，请联系管理员检查生成结果。',
}


class H3Queue:
    def __init__(self, node_client, session_factory=SessionLocal, *, poll_seconds=2, timeout_seconds=7200,
                 lease_seconds=180, sleep=time.sleep, monotonic=time.monotonic):
        self.node = node_client
        self.sessions = session_factory
        self.owner = f"h3-worker-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self.poll_seconds, self.timeout_seconds, self.lease_seconds = poll_seconds, timeout_seconds, lease_seconds
        self.sleep, self.monotonic = sleep, monotonic

    def has_pending(self):
        with self.sessions() as db:
            return db.scalar(select(CloudVideoJob.id).where(CloudVideoJob.status.in_({"queued", "running", "cancelling"})).limit(1)) is not None

    def has_inflight(self):
        with self.sessions() as db:
            return db.scalar(select(CloudVideoJob.id).where(CloudVideoJob.status.in_({"running", "cancelling"})).limit(1)) is not None

    def _claim(self):
        with self.sessions() as db:
            # Single daemon lock is mandatory. Recover execution before new work.
            job = db.scalar(select(CloudVideoJob).where(CloudVideoJob.status.in_({"running", "cancelling"})).order_by(CloudVideoJob.created_at).with_for_update(skip_locked=True).limit(1))
            fresh = job is None
            if fresh:
                job = db.scalar(select(CloudVideoJob).where(CloudVideoJob.status == "queued").order_by(CloudVideoJob.created_at).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            if fresh:
                job.status = "running"
                job.started_at = utcnow()
                job.attempt_token = uuid.uuid4().hex
            elif not job.attempt_token:
                # Malformed old state is unrecoverable; never create a new attempt.
                raise WorkerUnsafeState("Active H3 job has no persisted attempt token")
            job.lease_owner = self.owner
            job.heartbeat_at = utcnow()
            job.lease_expires_at = utcnow() + timedelta(seconds=self.lease_seconds)
            db.commit()
            return {"id": job.id, "token": job.attempt_token, "spec": dict(job.spec), "fresh": fresh,
                    "elapsed": max(0, (utcnow() - aware(job.started_at)).total_seconds()) if job.started_at else 0}

    def _locked(self, db, handle):
        job = db.scalar(select(CloudVideoJob).where(CloudVideoJob.id == handle["id"]).with_for_update())
        if job is None or job.attempt_token != handle["token"] or job.lease_owner != self.owner:
            raise WorkerUnsafeState("H3 worker lease fencing failed")
        return job

    def _heartbeat(self, handle, **changes):
        with self.sessions() as db:
            job = self._locked(db, handle)
            if job.status in TERMINAL:
                raise WorkerUnsafeState("H3 job became terminal during execution")
            job.heartbeat_at = utcnow()
            job.lease_expires_at = utcnow() + timedelta(seconds=self.lease_seconds)
            for key, value in changes.items():
                setattr(job, key, value)
            status = job.status
            db.commit()
            return status

    def _rpc(self, action, handle, **kwargs):
        self._heartbeat(handle)
        try:
            result = self.node.call(action, job_id=handle["id"], attempt_token=handle["token"], **kwargs)
        except Exception:
            # No raw transport exception can flow to user error fields.
            raise WorkerUnsafeState(f"Node RPC failed during {action}; preserve attempt for recovery") from None
        self._heartbeat(handle)
        if not isinstance(result, dict):
            raise WorkerUnsafeState("Invalid node RPC response")
        return result

    def _assert_idle(self):
        try:
            status = self.node.call("status")
        except Exception:
            raise WorkerUnsafeState("Cannot confirm node idle") from None
        if status.get("busy") is not False:
            raise WorkerUnsafeState("Node has not confirmed GPU work is idle")

    def _finish(self, handle, status, *, error_code=None, error_message=None, result_path=None, timings=None):
        self._assert_idle()
        with self.sessions() as db:
            job = self._locked(db, handle)
            # A cancellation winning this lock must never become a successful job.
            if job.status == "cancelling":
                status, result_path, error_code, error_message = "cancelled", None, None, None
            if job.status not in {"running", "cancelling"}:
                raise WorkerUnsafeState("Unexpected terminal transition")
            job.status, job.result_path = status, result_path
            job.error_code, job.error_message = error_code, error_message
            job.progress = 1 if status == "succeeded" else job.progress
            job.timings = self._safe_timings(timings)
            job.finished_at = utcnow()
            job.lease_expires_at = None
            db.commit()

    @staticmethod
    def _safe_timings(timings):
        if not isinstance(timings, dict):
            return {}
        return {key: float(value) for key, value in timings.items()
                if key in {"container_start_seconds", "model_load_seconds", "inference_seconds", "execution_seconds", "total_seconds", "queue_seconds"}
                and isinstance(value, (int, float)) and 0 <= value < 86400}

    def _store_result(self, handle):
        payload = self._rpc("result", handle)
        try:
            encoded = payload["data_b64"]
            if not isinstance(encoded, str) or len(encoded) > 350 * 1024 * 1024:
                raise ValueError()
            data = base64.b64decode(encoded, validate=True)
            if len(data) != payload["bytes"] or hashlib.sha256(data).hexdigest() != payload["sha256"]:
                raise ValueError()
            # Node performs ffprobe; reject obvious protocol/format corruption too.
            if len(data) < 12 or data[4:8] != b"ftyp":
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise WorkerUnsafeState("Invalid or corrupted H3 video artifact") from None
        key = f"results/{handle['id']}.mp4"
        save_bytes(key, data)
        return key

    def run_one(self):
        handle = self._claim()
        if handle is None:
            self._assert_idle()
            return False
        start = self.monotonic()
        if handle["fresh"]:
            with self.sessions() as db:
                asset = db.get(CloudVideoAsset, handle["spec"]["reference_image_asset_id"])
                key = asset.storage_key if asset else None
            try:
                data = storage_path(key).read_bytes() if key else None
                if not data:
                    raise OSError()
            except (OSError, ValueError):
                self._finish(handle, "failed", error_code="REFERENCE_UNAVAILABLE", error_message="参考图片不可用，请重新上传")
                return True
            # Check cancellation immediately before the one permitted submit.
            if self._heartbeat(handle) != "cancelling":
                response = self._rpc("submit", handle, spec=handle["spec"], image_b64=base64.b64encode(data).decode("ascii"))
                prompt = response.get("prompt_id")
                if prompt:
                    self._heartbeat(handle, remote_prompt_id=str(prompt)[:128])
        cancel_sent = False
        cancel_started = None
        timed_out = False
        while True:
            state = self._heartbeat(handle)
            timed_out = timed_out or handle["elapsed"] + self.monotonic() - start > self.timeout_seconds
            if (state == "cancelling" or timed_out) and not cancel_sent:
                self._rpc("cancel", handle)
                cancel_sent = True
                cancel_started = self.monotonic()
            report = self._rpc("poll", handle)
            status = report.get("status")
            if status == "running":
                # A failed cancel cannot wait forever; caller preserves exclusivity.
                if cancel_sent and self.monotonic() - cancel_started > 120:
                    raise WorkerUnsafeState("H3 cancellation has not been confirmed")
                self.sleep(self.poll_seconds)
                continue
            if status == "succeeded" and not timed_out and state != "cancelling":
                result_key = self._store_result(handle)
                self._finish(handle, "succeeded", result_path=result_key, timings=report.get("timings"))
            elif status == "cancelled" or (status == "succeeded" and state == "cancelling"):
                if timed_out and state != "cancelling":
                    self._finish(handle, "failed", error_code="GENERATION_TIMEOUT", error_message="视频生成超时，执行已停止")
                else:
                    self._finish(handle, "cancelled")
            elif status == "failed" or (status == "succeeded" and timed_out):
                code = report.get('error_code') if not timed_out else 'GENERATION_TIMEOUT'
                if code == 'GENERATION_TIMEOUT':
                    message = '视频生成超时，执行已停止'
                elif code in NODE_ERRORS:
                    message = NODE_ERRORS[code]
                else:
                    code, message = 'GENERATION_FAILED', 'H3 视频生成失败，请检查任务诊断后手动重试'
                self._finish(handle, "failed", error_code=code, error_message=message, timings=report.get("timings"))
            elif status == "unknown":
                # Explicit stop/ownership check, never resubmit a lost attempt.
                self._rpc("cancel", handle)
                self._assert_idle()
                self._finish(handle, "failed", error_code="EXECUTION_OUTCOME_UNKNOWN", error_message="执行状态无法恢复，已确认停止；请手动重试")
            else:
                raise WorkerUnsafeState("Unrecognized H3 node execution state")
            return True
