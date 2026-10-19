"""One .1 process controls the .3 GPU lifecycle for both TTS and H3."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request
from sqlalchemy import select, func, text

from gpu_scheduler import Scheduler, daemon_lock
from node3_client import Node3Client
from tts_redis_status import TTSRedisHTTP

from .cloud_video_worker import H3Queue
from .db import SessionLocal
from .device_control_executor import DeviceControlExecutor
from .service_control import desired_state as service_control_desired_state

LOG = logging.getLogger("ocv.gpu.scheduler")


class CloudTTSStatus(TTSRedisHTTP):
    def snapshot(self):
        status = super().snapshot()
        from .models import CloudJob
        with SessionLocal() as db:
            pending = db.scalar(select(func.count()).select_from(CloudJob).where(
                CloudJob.status.in_({'queued', 'running'}), CloudJob.ray_job_id.is_(None),
                CloudJob.cancel_requested.is_(False))) or 0
        return {**status, 'queue_depth': status['queue_depth'] + pending, 'cloud_pending': pending}

    def admission_snapshot(self):
        status = self.snapshot()
        # /drain confirms ownership/admission state but has no consumer_alive
        # field. Reuse the existing health route strictly for control-plane
        # liveness; dormant model readiness is intentionally irrelevant here.
        with urllib.request.urlopen(self.base_url + '/v1/health', timeout=15) as response:
            health = json.load(response)
        dispatcher = health.get('dispatcher', {})
        return {**status, 'consumer_alive': dispatcher.get('consumer_alive') is True,
                'redis_ready': status.get('redis_ready') is True and dispatcher.get('redis_ready') is True}


def heartbeat_loop(scheduler, stop):
    root = Path(os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
    while not stop.is_set():
        queue_ready = False
        try:
            status = scheduler.tts.admission_snapshot()
            queue_ready = status.get('redis_ready') is True and status.get('consumer_alive') is True
            with SessionLocal() as db:
                db.execute(text('SELECT 1'))
        except Exception:
            queue_ready = False
        payload = {'time': time.time(), 'mode': scheduler.mode, 'queue_ready': queue_ready,
                   'scheduler_ready': queue_ready and scheduler.mode != 'recovering'
                   and getattr(scheduler, 'tts_state', None) != 'unavailable'}
        if hasattr(scheduler, 'target_node'):
            payload.update(policy='dedicated', tts_node=scheduler.target_node, h3_node='10.250.0.3',
                           primary_state=scheduler.primary_state, h3_state=scheduler.h3_state,
                           primary_failure_reason=getattr(scheduler, 'primary_failure_reason', None),
                           blocked_reason=scheduler.blocked_reason,
                           tts_state=scheduler.tts_state, tts_blocked_reason=scheduler.tts_blocked_reason)
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o750)
            temporary = root / 'heartbeat.tmp'
            temporary.write_text(json.dumps(payload))
            temporary.chmod(0o640)
            temporary.replace(root / 'heartbeat.json')
        except OSError:
            LOG.warning('Could not persist scheduler heartbeat')
        stop.wait(5)


def node_health_loop(node, stop):
    root = Path(os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
    while not stop.is_set():
        started = time.time()
        try:
            payload = {'time': started, 'reachable': True, **node.call('health')}
        except Exception:
            payload = {'time': started, 'reachable': False}
        # Preserve diagnostic history in journald, not only an overwritten cache.
        # No prompts, credentials, command lines, or remote exception text.
        memory = payload.get('memory') or {}
        available = memory.get('MemAvailable')
        available = available if type(available) is int and available >= 0 else None
        LOG.info('node3_health reachable=%s h3_ready=%s h3_busy=%s mem_available_bytes=%s probe_seconds=%.3f',
                 payload.get('reachable') is True, payload.get('h3_ready') is True,
                 payload.get('h3_busy') is True, available, time.time()-started)
        try:
            root.mkdir(parents=True, exist_ok=True)
            temporary = root/'node-health.tmp'
            temporary.write_text(json.dumps(payload))
            temporary.chmod(0o640)
            temporary.replace(root/'node-health.json')
        except OSError:
            LOG.warning('Could not persist node health')
        stop.wait(15)


class NodePhases:
    def __init__(self, node, *, health_url="http://127.0.0.1:8000/api/tts/v1/health",
                 tts_start_timeout=600, sleep=time.sleep, monotonic=time.monotonic, health_probe=None):
        self.node = node
        self.health_url = health_url
        self.timeout = tts_start_timeout
        self.sleep, self.monotonic = sleep, monotonic
        self.health_probe = health_probe or self._health

    def _health(self):
        with urllib.request.urlopen(self.health_url, timeout=20) as response:
            return json.load(response)

    def ensure_tts(self):
        self.node.call("start_tts")
        deadline = self.monotonic() + self.timeout
        while True:
            try:
                payload = self.health_probe()
                dispatcher = payload.get("dispatcher", {})
                deps = dispatcher.get("dependencies", {})
                # Require real actor health, not merely container/ray registration.
                if (payload.get("ok") is True and deps.get("model_worker", {}).get("ready") is True
                        and deps.get("audio_finalizer", {}).get("ready") is True):
                    state = self.node.call("status")
                    if state.get("tts_running") is not True or state.get("h3_running") is not False:
                        raise RuntimeError("GPU phase ownership changed during TTS startup")
                    return
            except (OSError, ValueError, TimeoutError):
                pass
            if self.monotonic() >= deadline:
                raise RuntimeError("TTS startup timed out; dispatch remains drained")
            self.sleep(3)

    def ensure_h3(self):
        self.node.call("start_h3")
        state = self.node.call("status")
        if state.get("tts_running") is not False or state.get("h3_running") is not True or state.get("ready") is not True:
            raise RuntimeError("H3 did not acquire exclusive GPU phase")
        # Existing H3 work is deliberately allowed for durable recovery.

    def stop_all(self):
        self.node.call("stop_all")
        state = self.node.call("status")
        if state.get("tts_running") is not False or state.get("h3_running") is not False or state.get("busy") is not False:
            raise RuntimeError("Node did not confirm both GPU containers stopped")

    def stop_tts(self):
        self.node.call('stop_tts')

    def stop_h3(self):
        self.node.call('stop_h3')
        state = self.node.call('health')
        if state.get('h3_running') is not False:
            raise RuntimeError('H3_STOP_NOT_CONFIRMED')

    def ensure_idle(self):
        self.stop_all()

    def prepare_fallback(self):
        # Node-side guard refuses foreign Windows H3 work and non-idle managed
        # H3. Readiness is polled by the placement adapter, without blocking H3.
        self.node.call('start_tts')

    def h3_ready(self):
        state = self.node.call('health')
        return (state.get('h3_running') is True and state.get('h3_ready') is True
                and state.get('tts_running') is False
                and not (state.get('local_h3') or {}).get('blocked', False))


class PlacementClient:
    def _call(self, *args):
        result = subprocess.run([
            '/opt/ray-tts/head/.venv/bin/python', '/opt/cloud-api/tts_placement.py', *args],
            capture_output=True, text=True, timeout=90)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError('Placement helper returned invalid status') from None
        if result.returncode or payload.get('ok') is not True:
            raise RuntimeError('Placement helper requires reconciliation')
        return payload

    def snapshot(self):
        return self._call('snapshot')

    def select(self, target):
        return self._call('select', '--target', target)


def record_event(event):
    # Events contain scheduler-created scalar fields, never prompts/tokens.
    LOG.info("phase %s", json.dumps(event, ensure_ascii=True))
    try:
        root = Path(os.environ.get("GPU_SCHEDULER_STATE_DIR", "/var/lib/cloud-api/gpu-scheduler"))
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        with (root / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
    except OSError:
        LOG.warning("Could not persist scheduler event")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    # Root-owned lock excludes concurrent daemon instances and stays held during
    # all remote actions/DB claims. A forced shutdown is recovered on next start.
    with daemon_lock(os.environ.get("GPU_SCHEDULER_LOCK", "/run/ocv-gpu-scheduler/owner.lock")):
        node = Node3Client()
        dedicated = os.environ.get('GPU_SCHEDULER_POLICY') == 'dedicated'
        tts = CloudTTSStatus(node_ip=None if dedicated else '10.250.0.3')
        h3 = H3Queue(node)
        phases = NodePhases(node, tts_start_timeout=float(os.environ.get("GPU_TTS_START_TIMEOUT", "600")))
        control_executor = None
        if dedicated:
            from dedicated_scheduler import DedicatedScheduler
            from node20_client import Node20Client
            state_root = Path(os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
            placement = PlacementClient()
            control_state = lambda node_ip, service: service_control_desired_state(
                node_ip, service, root=state_root)
            scheduler = DedicatedScheduler(tts, phases, h3, placement, report=record_event,
                state_path='/var/lib/cloud-api/tts-placement/dedicated-policy.json',
                control_state=control_state)
            control_executor = DeviceControlExecutor(node, Node20Client(), scheduler=scheduler,
                tts=tts, phases=phases, placement=placement, root=state_root)
        else:
            scheduler = Scheduler(tts, phases, h3,
                              tts_quantum_seconds=float(os.environ.get("GPU_TTS_QUANTUM_SECONDS", "120")),
                              idle_grace_seconds=float(os.environ.get("GPU_IDLE_GRACE_SECONDS", "0")),
                                  report=record_event)
        heartbeat = threading.Thread(target=heartbeat_loop, args=(scheduler, stop), daemon=True, name='admission-heartbeat')
        heartbeat.start()
        threading.Thread(target=node_health_loop, args=(node,stop), daemon=True, name="node-health").start()
        while not stop.is_set():
            try:
                if control_executor is not None:
                    control_executor.process_one()
                scheduler.tick()
            except Exception as exc:
                # Never log exception text: transport stderr may carry paths or
                # configuration. No resume occurs after an uncertain transition.
                LOG.error("GPU scheduling requires reconciliation (%s)", type(exc).__name__)
                scheduler.mode = "recovering"
                scheduler.prefer_h3 = h3.has_inflight()
                stop.wait(5)
            else:
                stop.wait(1)
        # Leave active work and container state intact. On restart the persisted
        # job and lifecycle drain records decide the safe next phase.
        LOG.info("Scheduler stopped; next startup will reconcile persisted work")


if __name__ == "__main__":
    main()
