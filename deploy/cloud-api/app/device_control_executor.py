"""Execute the durable admin container-control queue from the GPU owner."""
from __future__ import annotations

from .service_control import ServiceControlError, process_next


SAFE_ERRORS = {
    'SERVICE_BUSY': ('SERVICE_BUSY', '服务当前有任务，操作已拒绝', 409),
    'H3_BUSY': ('SERVICE_BUSY', 'H3 当前有任务，操作已拒绝', 409),
    'H3_EXECUTION_UNRESOLVED': ('SERVICE_BUSY', 'H3 仍有未完成任务，操作已拒绝', 409),
    'TTS_NOT_DRAINED': ('SERVICE_BUSY', 'TTS 尚未完成排空，操作已拒绝', 409),
    'TTS_DRAIN_PROBE_FAILED': ('PLACEMENT_UNKNOWN', 'TTS 排空状态无法确认，操作已拒绝', 503),
    'NODE20_BUSY': ('NODE_BUSY', '节点正在执行其他维护操作', 409),
    'SCHEDULER_ROLE_REQUIRED': ('CONTROL_AGENT_UNAVAILABLE', '节点控制代理未完成安全授权', 503),
    'SCHEDULER_ROLE_INVALID': ('CONTROL_AGENT_UNAVAILABLE', '节点控制代理授权无效', 503),
    'NODE20_AGENT_GUARD_REQUIRED': ('CONTROL_AGENT_UNAVAILABLE', '节点控制代理未完成安全授权', 503),
    'TTS_RESTART_GUARD_FAILED': ('CONTROL_AGENT_UNAVAILABLE', 'TTS 自动重启保护未生效', 503),
    'MODEL_CACHE_COPY_IN_PROGRESS': ('NODE_BUSY', '节点正在准备模型缓存', 409),
}


class DeviceControlExecutor:
    def __init__(self, node3, node20, *, scheduler, tts, phases, placement, root=None):
        self.node3, self.node20, self.root = node3, node20, root
        self.scheduler, self.tts = scheduler, tts
        self.phases, self.placement = phases, placement

    @staticmethod
    def _error(exc):
        code, message, status = SAFE_ERRORS.get(str(exc),
                                                 ('CONTROL_EXECUTION_FAILED', '设备操作未完成，请检查节点状态后重试', 503))
        return ServiceControlError(code, message, status)

    def _run(self, record):
        node_ip, service, action = record['node_ip'], record['service'], record['action']
        if node_ip == '10.250.0.3' and service == 'h3':
            if action == 'stop' and (self.scheduler.h3.has_pending()
                                     or self.scheduler.h3.has_inflight()
                                     or self.scheduler.h3_future is not None):
                raise ServiceControlError('SERVICE_BUSY', 'H3 仍有排队或执行任务，操作已拒绝', 409)
            if action == 'start' and self.scheduler.target_node != '10.250.0.20':
                before = self.tts.snapshot()
                if before.get('queue_depth') != 0 or before.get('active_jobs') != 0:
                    raise ServiceControlError('SERVICE_BUSY', 'TTS 尚有排队或执行任务，操作已拒绝', 409)
                status = self.tts.drain()
                if status.get('draining') is not True or status.get('drained') is not True \
                        or status.get('active_jobs') != 0:
                    raise ServiceControlError('SERVICE_BUSY', 'TTS 尚有正在执行的任务，操作已拒绝', 409)
            try:
                if action == 'start':
                    self.phases.ensure_h3()
                    state = self.node3.call('health')
                    running, ready = state.get('h3_running'), state.get('h3_ready')
                else:
                    self.node3.call('stop_h3')
                    state = self.node3.call('health')
                    running, ready = state.get('h3_running'), state.get('h3_ready')
            except Exception as exc:
                raise self._error(exc) from None
            if running is not (action == 'start'):
                raise ServiceControlError('CONTROL_POSTCONDITION_FAILED', 'H3 状态未达到请求结果', 503)
            if action == 'start' and ready is not True:
                raise ServiceControlError('CONTROL_POSTCONDITION_FAILED', 'H3 容器已启动但服务尚未就绪', 503)
            self.scheduler.h3_resident = bool(running)
            self.scheduler.h3_state = 'ready' if running else 'stopped'
            self.scheduler.h3_checked_at = self.scheduler.clock()
            self.scheduler.retry_h3_at = 0
            return {'container_running': running, 'ready': bool(ready),
                    'reason': 'H3 容器已启动' if running else 'H3 容器已停止'}
        if node_ip == '10.250.0.20' and service == 'tts':
            try:
                if action == 'stop':
                    probe = self.placement.snapshot()
                    if (probe.get('ok') is not True or probe.get('transaction_pending') is True
                            or probe.get('target_node') != '10.250.0.20'
                            or probe.get('actual_pin') != '10.250.0.20'
                            or probe.get('lifecycle_target') != '10.250.0.20'):
                        raise ServiceControlError('PLACEMENT_UNKNOWN', 'TTS 当前调度归属不是该节点，操作已拒绝', 409)
                    before = self.tts.snapshot()
                    if before.get('queue_depth') != 0 or before.get('active_jobs') != 0:
                        raise ServiceControlError('SERVICE_BUSY', 'TTS 尚有排队或执行任务，操作已拒绝', 409)
                    drained = self.tts.drain()
                    if (drained.get('draining') is not True or drained.get('drained') is not True
                            or drained.get('active_jobs') != 0):
                        raise ServiceControlError('SERVICE_BUSY', 'TTS 尚有正在执行的任务，操作已拒绝', 409)
                    state = self.node20.call('stop_tts')
                else:
                    state = self.node20.call('start_tts')
            except Exception as exc:
                if isinstance(exc, ServiceControlError):
                    raise
                raise self._error(exc) from None
            running = state.get('tts_running')
            if running is not (action == 'start'):
                raise ServiceControlError('CONTROL_POSTCONDITION_FAILED', 'TTS 状态未达到请求结果', 503)
            self.scheduler.last_probe = float('-inf')
            self.scheduler.probe = None
            self.scheduler.primary_state = 'unknown'
            self.scheduler.up = self.scheduler.down = 0
            return {'container_running': running, 'ready': False,
                    'reason': 'TTS 容器已启动，等待模型健康检查' if running else 'TTS 容器已停止，调度入口保持排空'}
        raise ServiceControlError('UNSUPPORTED_SERVICE', '该节点服务没有受控启停通道', 409)

    def process_one(self, *, now=None):
        return process_next(self._run, root=self.root, now=now)
