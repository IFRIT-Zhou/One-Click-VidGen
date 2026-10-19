"""Private head-to-node transport over the existing SSH administrative link."""
import json
import logging
import subprocess
import time
import uuid

LOG = logging.getLogger('ocv.node3.transport')
SAFE_ERRORS = frozenset({
    'NODE_PHASE_BUSY', 'WINDOWS_H3_BUSY', 'WINDOWS_H3_RESOURCE_BLOCKED',
    'WINDOWS_H3_PROBE_FAILED', 'H3_BUSY', 'H3_EXECUTION_UNRESOLVED',
    'TTS_NOT_DRAINED', 'TTS_STILL_RUNNING', 'H3_START_TIMEOUT',
    'MODEL_CACHE_COPY_IN_PROGRESS', 'SCHEDULER_ROLE_INVALID',
    'SCHEDULER_ROLE_REQUIRED', 'HEALTH_PROBE_FAILED',
})


class Node3Client:
    def call(self, action, **kwargs):
        # Log identifiers and timings only: payload/stderr can contain secrets.
        if action not in {'health','status','start_h3','prepare_dedicated_h3',
                          'start_tts','stop_tts','stop_h3','stop_all',
                          'submit','poll','cancel','result'}:
            raise ValueError('UNKNOWN_NODE_ACTION')
        call_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        LOG.info('node3_begin action=%s call_id=%s', action, call_id)
        try:
            result = self._call(action, **kwargs)
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                code = 'NODE_TRANSPORT_TIMEOUT'
            elif isinstance(exc, OSError):
                code = 'NODE_TRANSPORT_OS_ERROR'
            else:
                code = str(exc) if str(exc) in SAFE_ERRORS else 'NODE_RPC_FAILED'
            LOG.warning('node3_failed action=%s call_id=%s elapsed=%.3f code=%s',
                        action, call_id, time.monotonic()-started, code)
            raise
        LOG.info('node3_done action=%s call_id=%s elapsed=%.3f',
                 action, call_id, time.monotonic()-started)
        return result

    def _call(self, action, **kwargs):
        payload = json.dumps({'action': action, **kwargs}).encode()
        # SSH already terminates inside Linux. Do not re-enter through Windows
        # wsl.exe/VMBus for every submit/poll. Sudo permits this exact root-owned
        # adapter only; -I excludes user-controlled Python paths/environment.
        command = 'sudo -n -- /usr/bin/python3 -I /opt/ocv-h3/node3_agent.py'
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/etc/ray-enrollment/ssh_known_hosts',
            '-i', '/etc/ray-enrollment/tunnel-107', 'rayworker@10.250.0.3', command],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError('Node3 transport did not return a valid response') from exc
        if result.returncode or not response.get('ok'):
            raise RuntimeError(response.get('error', 'Node3 operation failed'))
        return response['result']
