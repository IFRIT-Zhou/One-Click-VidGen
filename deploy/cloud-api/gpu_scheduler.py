"""Single-owner TTS/H3 scheduling on the head host.

Run one instance, guarded by daemon_lock(). Call tick() serially. Lifecycle
callbacks MUST verify the other container stopped; H3 callbacks must recover
durable in-flight jobs before accepting a new one. No browser calls lifecycle
endpoints directly. A callback exception leaves TTS drained and is retried by
the hosting daemon; it must never be interpreted as successful shutdown.
"""
from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol


class TTS(Protocol):
    def snapshot(self) -> dict: ...
    def drain(self) -> dict: ...
    def resume(self) -> dict: ...


class Phases(Protocol):
    def ensure_tts(self) -> None:
        """Stop idle H3, start TTS, wait for Ray + model readiness; DO NOT resume."""
    def ensure_h3(self) -> None:
        """After acknowledged drain, stop TTS, start H3 and wait for readiness."""
    def stop_all(self) -> None:
        """Verify no H3 work remains and stop both containers, or raise."""


class H3Queue(Protocol):
    def has_pending(self) -> bool:
        """Include recoverable in-flight jobs, not just fresh queued jobs."""
    def run_one(self) -> None:
        """Claim/recover and finish one durable job; don't return with GPU busy."""


class Scheduler:
    def __init__(self, tts: TTS, phases: Phases, h3: H3Queue, *,
                 tts_quantum_seconds: float = 120, idle_grace_seconds: float = 0,
                 clock=time.monotonic, report=lambda event: None):
        self.tts, self.phases, self.h3 = tts, phases, h3
        self.quantum = max(1, tts_quantum_seconds)
        self.idle_grace = max(0, idle_grace_seconds)
        self.clock, self.report = clock, report
        self.mode = "recovering"
        self.tts_started = 0.0
        self.idle_since = None
        self.prefer_h3 = False

    def _mode(self, mode):
        self.mode = mode
        self.report({"mode": mode, "time": time.time()})

    @staticmethod
    def _active(status):
        return int(status["active_jobs"])

    def _drained(self):
        status = self.tts.drain()
        # Fail closed: a missing acknowledgement is not a successful drain.
        return status.get("draining") is True and status.get("drained") is True and self._active(status) == 0

    def tick(self):
        now = self.clock()
        if self.mode == "recovering":
            if not self._drained():
                return
            self._mode("idle")
        status = self.tts.snapshot()
        if not status.get("consumer_alive", True) or status.get("redis_ready", True) is not True:
            raise RuntimeError("TTS control plane is unavailable")
        tts_waiting = int(status["queue_depth"]) > 0
        h3_waiting = self.h3.has_pending()
        # Prefer reconciling persisted work after a daemon restart, even when
        # new TTS jobs arrived while this process was down.
        if getattr(self.h3, "has_inflight", lambda: False)():
            self.prefer_h3 = True

        if self.mode in {"tts", "tts_draining"}:
            # Give H3 a turn after a bounded admission window. Already admitted
            # jobs finish; timeouts/cancellation remain the TTS dispatcher's job.
            yield_to_h3 = h3_waiting and now - self.tts_started >= self.quantum
            finished = not tts_waiting and self._active(status) == 0
            if self.mode == "tts_draining" or yield_to_h3 or finished:
                self.prefer_h3 = self.prefer_h3 or h3_waiting
                self._mode("tts_draining")
                if not self._drained():
                    return
                self._mode("idle")
                status = self.tts.snapshot()
                tts_waiting = int(status["queue_depth"]) > 0
            else:
                return

        if self.mode in {"idle", "off"}:
            if h3_waiting and (self.prefer_h3 or not tts_waiting):
                self.idle_since = None
                if not self._drained():
                    return
                self._mode("h3_starting")
                try:
                    started = self.clock()
                    self.phases.ensure_h3()
                    self.report({"phase": "h3", "startup_seconds": self.clock() - started})
                    self._mode("h3")
                    self.h3.run_one()
                    # Next tick gives TTS priority; consecutive H3 jobs can
                    # retain its container until a different stage is needed.
                    self.prefer_h3 = False
                    self._mode("idle")
                except Exception:
                    # An RPC failure can leave GPU work running. Recover that
                    # durable H3 job before yielding to newly queued TTS.
                    self.prefer_h3 = True
                    self._mode("recovering")
                    raise
                return
            if tts_waiting:
                self.idle_since = None
                if not self._drained():
                    return
                self._mode("tts_starting")
                try:
                    started = self.clock()
                    self.phases.ensure_tts()
                    resumed = self.tts.resume()
                    if resumed.get("resumed") is not True:
                        raise RuntimeError("TTS worker did not become ready; dispatch stays drained")
                    self.tts_started = self.clock()
                    self.report({"phase": "tts", "startup_seconds": self.tts_started - started})
                    self._mode("tts")
                except Exception:
                    self._mode("recovering")
                    raise
                return
            if self.mode == "off":
                return
            self.idle_since = now if self.idle_since is None else self.idle_since
            if now - self.idle_since >= self.idle_grace:
                if not self._drained():
                    return
                self.phases.stop_all()
                self._mode("off")


class TTSHTTP:
    """Credentials remain on .1; never return them or expose them to clients."""
    def __init__(self, node_ip="10.250.0.3", *,
                 base_url="http://127.0.0.1:8000/api/tts",
                 credentials="/etc/ray-tts/worker-lifecycle.json", timeout=20):
        self.node_ip, self.base_url = node_ip, base_url.rstrip("/")
        self.credentials, self.timeout = Path(credentials), timeout

    def _request(self, action, method="GET"):
        config = json.loads(self.credentials.read_text())
        node_ip = config["dispatch_node_ip"] if self.node_ip is None else self.node_ip
        if config["dispatch_node_ip"] != node_ip:
            raise RuntimeError("Lifecycle dispatch target changed")
        worker = config["workers"][node_ip]
        request = urllib.request.Request(
            f"{self.base_url}/internal/workers/{node_ip}/{action}",
            method=method, headers={"Authorization": "Bearer " + worker["token"],
                                    "If-Match": str(worker["generation"])})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid TTS lifecycle response")
        return payload

    def snapshot(self):
        return self._request("status")

    def drain(self):
        return self._request("drain", "POST")

    def resume(self):
        return self._request("resume", "POST")


@contextlib.contextmanager
def daemon_lock(path="/run/ocv-gpu-scheduler.lock"):
    import fcntl
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
