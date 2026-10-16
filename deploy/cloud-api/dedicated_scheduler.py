"""Resident primary TTS, independent H3, and drained single-GPU failover.

Only the owning daemon calls tick. H3 work has one bounded executor slot;
placement changes never run while an admitted TTS request exists.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import time

PRIMARY = '10.250.0.20'
FALLBACK = '10.250.0.3'


class DedicatedScheduler:
    def __init__(self, tts, phases, h3, placement, *, clock=time.monotonic,
                 report=lambda event: None, executor=None, probe_interval=10,
                 offline_samples=3, online_samples=2, quantum=120,
                 h3_check_interval=30, startup_timeout=600, recovery_cooldown=300,
                 state_path=None, wall_clock=time.time, control_state=lambda node, service: None):
        self.tts, self.phases, self.h3, self.placement = tts, phases, h3, placement
        self.clock, self.report = clock, report
        self.control_state = control_state
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='resident-h3')
        self.probe_interval = probe_interval
        self.offline_samples, self.online_samples = offline_samples, online_samples
        self.quantum = quantum
        self.h3_check_interval, self.startup_timeout = h3_check_interval, startup_timeout
        self.recovery_cooldown = recovery_cooldown
        self.primary_start_since = None
        self.primary_retry_at = 0
        self.primary_failure_reason = None
        self.idle_released = False
        self.state_path = Path(state_path) if state_path else None
        self.wall_clock = wall_clock
        if self.state_path and self.state_path.exists():
            saved = json.loads(self.state_path.read_text(encoding='utf-8'))
            retry = saved.get('retry_after_epoch', 0)
            self.primary_retry_at = self.clock() + max(0, min(self.recovery_cooldown, retry-self.wall_clock()))
            started = saved.get('startup_since_epoch')
            if started is not None:
                self.primary_start_since = self.clock() - max(0, self.wall_clock()-started)
            self.primary_failure_reason = saved.get('failure_reason')
        self.mode = 'recovering'
        self.target_node = None
        self.primary_state = 'unknown'
        self.blocked_reason = None
        self.h3_state = 'unknown'
        self.h3_future = None
        self.h3_resident = False
        self.h3_checked_at = float('-inf')
        self.tts_state = 'unknown'
        self.tts_blocked_reason = None
        self.tts_wait_since = None
        self.last_probe = float('-inf')
        self.probe = None
        self.up = self.down = 0
        self.tts_started = None
        self.retry_h3_at = 0

    def _mode(self, mode):
        if self.mode != mode:
            self.mode = mode
            self.report({'mode': mode, 'time': time.time(), 'target_node': self.target_node})

    def _persist_policy(self):
        if self.state_path is None:
            return
        now, wall = self.clock(), self.wall_clock()
        saved = {'retry_after_epoch': wall + max(0, self.primary_retry_at-now),
                 'startup_since_epoch': None if self.primary_start_since is None else wall-(now-self.primary_start_since),
                 'failure_reason': self.primary_failure_reason}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.policy-', dir=self.state_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(saved, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _refresh(self, now):
        if now - self.last_probe < self.probe_interval:
            return
        # Unknown control-plane observations must never become 'primary offline'.
        try:
            probe = self.placement.snapshot()
            if not probe.get('ok'):
                raise RuntimeError('PLACEMENT_PROBE_FAILED')
        except Exception:
            self.probe = None
            self.primary_state = 'unknown'
            self.up = self.down = 0
            self.last_probe = now
            raise
        self.probe, self.last_probe = probe, now
        self.target_node = probe['target_node']
        if self.control_state(PRIMARY, 'tts') is False:
            # A manual stop is a persistent primary suppression, not a
            # transient Ray observation. Fallback remains demand driven.
            self.primary_state = 'offline'
            self.up, self.down = 0, self.offline_samples
            return
        online = probe['nodes'].get(PRIMARY, {}).get('online')
        if not isinstance(online, bool):
            self.primary_state = 'unknown'
            self.up = self.down = 0
            raise RuntimeError('PRIMARY_OBSERVATION_UNKNOWN')
        service = probe.get('primary_tts', {}).get('state', 'unknown')
        previous_policy = (self.primary_start_since, self.primary_retry_at, self.primary_failure_reason)
        # A five-second HTTP timeout during inference is not model failure.
        # Placement reports independent .20 evidence, never .3's readiness.
        if online and service not in ('ready', 'starting', 'failed', 'unassigned'):
            self.primary_state = 'unknown'
            self.up = self.down = 0
            return
        unavailable = not online
        if online and service == 'failed':
            unavailable = True
            self.primary_failure_reason = 'PRIMARY_TTS_FAILED'
        elif online and service == 'starting':
            if self.primary_start_since is None:
                self.primary_start_since = now
            unavailable = now - self.primary_start_since >= self.startup_timeout
            if unavailable:
                self.primary_failure_reason = 'TTS_MODEL_START_TIMEOUT'
        else:
            self.primary_start_since = None
        if online and service == 'unassigned' and now < self.primary_retry_at:
            unavailable = True
        self.up, self.down = (0, self.down + 1) if unavailable else (self.up + 1, 0)
        self.primary_state = ('online' if self.up >= self.online_samples else
                              ('unavailable' if online else 'offline')
                              if self.down >= self.offline_samples else 'checking')
        if service == 'ready' and self.primary_state == 'online':
            self.primary_failure_reason = None
            self.primary_retry_at = 0
        if previous_policy != (self.primary_start_since, self.primary_retry_at, self.primary_failure_reason):
            self._persist_policy()

    def _drained(self):
        state = self.tts.drain()
        return state.get('draining') is True and state.get('drained') is True and state.get('active_jobs') == 0

    def _h3_finished(self, now):
        if self.h3_future is None or not self.h3_future.done():
            return
        future, self.h3_future = self.h3_future, None
        try:
            future.result()
            self.h3_resident, self.h3_state = True, 'ready'
            self.h3_checked_at = now
            self.blocked_reason = None
        except Exception:
            # An uncertain H3 outcome must be reconciled by the durable queue.
            self.h3_resident, self.h3_state = False, 'blocked'
            self.blocked_reason = 'H3_NOT_IDLE_OR_UNAVAILABLE'
            self.retry_h3_at = now + 15

    def _h3(self, now):
        if self.control_state(FALLBACK, 'h3') is False:
            self.h3_state = 'stopped'
            return
        if self.h3_future is not None or now < self.retry_h3_at:
            return
        pending = self.h3.has_pending()
        if self.h3_resident and not pending and now - self.h3_checked_at < self.h3_check_interval:
            return
        self.h3_state = 'running' if pending else 'starting'
        self.idle_released = False
        def run():
            # Revalidate idle residency after a node/container restart without
            # blocking the independent primary TTS loop or reloading a warm H3.
            if not self.h3_resident or not self.phases.h3_ready():
                self.phases.ensure_h3()
            if pending:
                self.h3.run_one()
        self.h3_future = self.executor.submit(run)

    def _idle(self):
        # Caller has checked both queues and H3 uncertainty. Never evict an
        # active request, including one admitted since the queue snapshot.
        if not self.idle_released:
            if not self._drained():
                self._mode('tts_draining')
                return
            self.phases.ensure_idle()
            self.idle_released = True
        self.h3_resident, self.h3_state = False, 'idle'
        self.tts_started = None
        self.tts_state = 'idle'
        self._mode('idle')

    def _resume_ready(self, status):
        if (status.get('draining') is False and self.probe
                and self.target_node == PRIMARY
                and self.probe.get('lifecycle_target') == PRIMARY
                and self.probe.get('primary_tts', {}).get('state') == 'ready'):
            # Existing admission remains valid while the model health RPC is
            # queued behind real inference; do not call resume on that evidence.
            return True
        if not self.probe or not self.probe.get('ready'):
            return False
        if status.get('draining') is False:
            return True
        response = self.tts.resume()
        return response.get('resumed') is True

    def _tts_readiness(self, ready, now):
        if ready:
            self.tts_state = 'ready'
            self.tts_wait_since = None
            self.tts_blocked_reason = None
            return
        if self.tts_wait_since is None:
            self.tts_wait_since = now
        expired = now - self.tts_wait_since >= self.startup_timeout
        self.tts_state = 'unavailable' if expired else 'starting'
        self.tts_blocked_reason = 'TTS_MODEL_START_TIMEOUT' if expired else None

    def _select(self, target):
        result = self.placement.select(target)
        if not result.get('ok'):
            raise RuntimeError('PLACEMENT_CHANGE_FAILED')
        self.target_node = target
        self.probe = result
        self.last_probe = float('-inf')
        self.tts_started = None
        self.tts_wait_since = None
        self.tts_blocked_reason = None
        self.tts_state = 'starting'

    def tick(self):
        now = self.clock()
        self._h3_finished(now)
        self._refresh(now)
        if self.probe is None:
            raise RuntimeError('PLACEMENT_UNKNOWN')
        status = self.tts.snapshot()
        pending = int(status['queue_depth']) > 0
        active = int(status['active_jobs'])
        if not status.get('redis_ready', True):
            raise RuntimeError('QUEUE_UNAVAILABLE')
        if self.probe.get('transaction_pending'):
            target = self.probe.get('transaction_target')
            if target not in (PRIMARY, FALLBACK):
                raise RuntimeError('TRANSITION_TARGET_UNKNOWN')
            self._mode('dedicated_switching')
            if not self._drained():
                return
            if target == PRIMARY and self.probe.get('lifecycle_target') == FALLBACK:
                self.phases.stop_tts()
            self._select(target)
            return

        if self.primary_state == 'online':
            if self.target_node != PRIMARY:
                self._mode('dedicated_switching')
                if not self._drained():
                    return
                # Stop .3 TTS while its lifecycle token still owns the drain.
                # Existing H3 work is left intact by this action.
                self.phases.stop_tts()
                self._select(PRIMARY)
                status = {**status, 'draining': True}
            ready = self._resume_ready(status)
            self._tts_readiness(ready, now)
            if ready:
                self._mode('dedicated')
            else:
                self._mode('dedicated_starting')
            # H3 errors on .3 do not hold .20 TTS hostage.
            self._h3(now)
            return

        self.tts_state = 'unknown'
        self.tts_wait_since = None
        self.tts_blocked_reason = None
        if self.primary_state in ('checking', 'unknown'):
            self._mode('primary_waiting')
            return

        if self.target_node == PRIMARY:
            if not pending:
                if self.h3_future is not None:
                    self._mode('h3')
                    return
                if self.h3.has_inflight() or (self.control_state(FALLBACK, 'h3') is not False and self.h3.has_pending()):
                    self._mode('h3')
                    self._h3(now)
                    return
                if active:
                    self._mode('tts_draining')
                    self._drained()
                    return
                if self.control_state(FALLBACK, 'h3') is True:
                    if not self._drained():
                        return
                    self._mode('h3')
                    self._h3(now)
                    return
                self._idle()
                return
            self._mode('fallback_waiting')
            # Never interrupt a running/uncertain H3 job for fallback.
            if self.h3_future is not None or self.h3.has_inflight():
                self._h3(now)
                return
            if not self._drained():
                return
            self.phases.prepare_fallback()
            self.idle_released = False
            self.h3_resident = False
            # An alive but broken primary is retried only after a cooldown;
            # otherwise removing its failed replicas would look like recovery.
            if self.primary_state == 'unavailable':
                self.primary_retry_at = now + self.recovery_cooldown
                self._persist_policy()
            self._select(FALLBACK)

        # Already on .3: demand-driven exclusive phases, with both models
        # released when idle. Only a healthy .20 permits resident H3.
        if self.h3_future is not None:
            self._mode('h3')
            return
        if self.h3.has_inflight():
            if not self._drained():
                return
            self._mode('h3')
            self._h3(now)
            return
        h3_pending = self.control_state(FALLBACK, 'h3') is not False and self.h3.has_pending()
        if not pending and not active and not h3_pending:
            if self.control_state(FALLBACK, 'h3') is True:
                if not self._drained():
                    return
                self._mode('h3')
                self._h3(now)
                return
            self._idle()
            return
        yield_h3 = h3_pending and self.tts_started is not None and now-self.tts_started >= self.quantum
        if (not pending and active == 0) or yield_h3:
            self._mode('tts_draining')
            if not self._drained():
                return
            self.phases.stop_tts()
            self.tts_started = None
            self._mode('h3')
            self._h3(now)
            return
        if active:
            self._mode('tts')
            return
        if pending:
            if (self.tts_started is not None and self.probe.get('ready')
                    and status.get('draining') is False):
                self._mode('tts')
                return
            self._mode('tts_starting')
            if not self._drained():
                return
            self.phases.prepare_fallback()
            self.idle_released = False
            self.h3_resident = False
            # This branch just drained dispatch even if the initial snapshot
            # was accepting; never mistake that stale snapshot for admission.
            if self._resume_ready({**status, 'draining': True}):
                self.tts_started = now if self.tts_started is None else self.tts_started
                self._mode('tts')
