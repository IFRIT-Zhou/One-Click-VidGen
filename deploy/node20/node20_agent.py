#!/usr/bin/env python3
"""Fixed, scheduler-owned .20 TTS container actions inside the RayWorker WSL distro.

Install as /opt/ray-worker/bin/ocv-node20-control.py. The head calls this over
the pinned administrative SSH path; it is not a public API or a generic Docker
command proxy. The head scheduler remains responsible for placement and for
waiting for the Ray/Serve model to become ready after a container start.
"""
try:
    import fcntl
except ImportError:  # Unit tests also run on Windows; production agent is Linux-only.
    fcntl = None
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request


NODE_IP = '10.250.0.20'
TTS = 'ray-worker-managed'
ROLE_PATH = Path('/etc/ray-worker/gpu-scheduler-primary.json')
CONFIG_PATH = Path('/etc/ray-worker/config.json')
LIFECYCLE_PATH = Path('/etc/ray-worker/lifecycle-control.json')
AGENT_LOCK = Path('/var/lib/ray-worker/agent.lock')
PHASE_LOCK = Path('/run/lock/ocv-gpu-phase.lock')
AGENT_PATH = Path('/opt/ray-worker/bin/worker-agent')
MANUAL_STOP = Path('/var/lib/ray-worker/gpu-scheduler-manual-stop.json')
GUARD_TAG = '# OCV_SCHEDULER_PRIMARY_GUARD_V1'
ROLE = {'schema': 1, 'owner': 'ocv-gpu-scheduler', 'node_ip': NODE_IP,
        'primary_role': 'tts', 'tts_container': TTS}
LIFECYCLE_URL = ('http://10.250.0.1:8000/api/tts/internal/workers/'
                 + NODE_IP + '/drain')


class ControlError(RuntimeError):
    """Safe error code for the head client and administrator audit."""


def read_json(path, error):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('not an object')
        return data
    except (OSError, ValueError, TypeError):
        raise ControlError(error) from None


def manual_stopped():
    if MANUAL_STOP.is_symlink():
        raise ControlError('MANUAL_STOP_MARKER_INVALID')
    if not MANUAL_STOP.exists():
        return False
    if read_json(MANUAL_STOP, 'MANUAL_STOP_MARKER_INVALID') != {
            'schema': 1, 'owner': 'ocv-gpu-scheduler', 'stopped': True}:
        raise ControlError('MANUAL_STOP_MARKER_INVALID')
    return True


def require_owner():
    if not ROLE_PATH.is_file() or ROLE_PATH.is_symlink():
        raise ControlError('SCHEDULER_ROLE_REQUIRED')
    if read_json(ROLE_PATH, 'SCHEDULER_ROLE_INVALID') != ROLE:
        raise ControlError('SCHEDULER_ROLE_INVALID')
    config = read_json(CONFIG_PATH, 'NODE20_CONFIG_INVALID')
    if config.get('node_ip') != NODE_IP or config.get('container_name') != TTS:
        raise ControlError('NODE20_CONFIG_INVALID')
    try:
        agent = AGENT_PATH.read_text(encoding='utf-8')
    except OSError:
        raise ControlError('NODE20_AGENT_GUARD_REQUIRED') from None
    if (AGENT_PATH.is_symlink() or GUARD_TAG not in agent
            or 'if _scheduler_manual_stop():' not in agent):
        raise ControlError('NODE20_AGENT_GUARD_REQUIRED')
    manual_stopped()


def write_manual_stop():
    MANUAL_STOP.parent.mkdir(parents=True, exist_ok=True)
    if MANUAL_STOP.is_symlink():
        raise ControlError('MANUAL_STOP_MARKER_INVALID')
    fd, name = tempfile.mkstemp(prefix='.manual-stop-', dir=MANUAL_STOP.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'schema': 1, 'owner': 'ocv-gpu-scheduler', 'stopped': True}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, MANUAL_STOP)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def clear_manual_stop():
    if MANUAL_STOP.is_symlink():
        raise ControlError('MANUAL_STOP_MARKER_INVALID')
    MANUAL_STOP.unlink(missing_ok=True)


def container_running():
    try:
        result = subprocess.run(['docker', 'inspect', TTS], capture_output=True,
                                text=True, timeout=10, check=True)
        rows = json.loads(result.stdout)
        running = rows[0]['State']['Running']
        if not isinstance(running, bool):
            raise ValueError('not a boolean')
        return running
    except (OSError, ValueError, IndexError, KeyError, TypeError,
            subprocess.SubprocessError):
        raise ControlError('TTS_CONTAINER_UNAVAILABLE') from None


def docker_action(verb):
    if verb not in ('start', 'stop'):
        raise ControlError('UNKNOWN_ACTION')
    args = ['docker', verb]
    if verb == 'stop':
        args += ['--time', '60']
    args.append(TTS)
    try:
        subprocess.run(args, capture_output=True, timeout=75, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ControlError('TTS_' + verb.upper() + '_FAILED') from None


def require_drained():
    lifecycle = read_json(LIFECYCLE_PATH, 'TTS_LIFECYCLE_UNAVAILABLE')
    if (lifecycle.get('node_ip') != NODE_IP
            or isinstance(lifecycle.get('generation'), bool)
            or not isinstance(lifecycle.get('generation'), int)
            or lifecycle['generation'] < 1
            or not isinstance(lifecycle.get('token'), str)
            or not lifecycle['token']):
        raise ControlError('TTS_LIFECYCLE_INVALID')
    request = urllib.request.Request(
        LIFECYCLE_URL, headers={'Authorization': 'Bearer ' + lifecycle['token'],
                                'If-Match': str(lifecycle['generation'])})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            state = json.load(response)
    except (OSError, ValueError, TypeError, KeyError):
        raise ControlError('TTS_DRAIN_PROBE_FAILED') from None
    if (not isinstance(state, dict) or state.get('draining') is not True
            or state.get('drained') is not True
            or type(state.get('active_jobs')) is not int
            or state['active_jobs'] != 0):
        raise ControlError('TTS_NOT_DRAINED')


def dispatch(request):
    if not isinstance(request, dict) or set(request) != {'action'}:
        raise ControlError('INVALID_REQUEST')
    action = request['action']
    if action == 'status':
        try:
            require_owner()
            control_ready = True
        except ControlError:
            control_ready = False
        return {'tts_running': container_running(), 'control_ready': control_ready,
                'manual_stopped': manual_stopped()}
    if action not in ('start_tts', 'stop_tts'):
        raise ControlError('UNKNOWN_ACTION')
    require_owner()
    if action == 'stop_tts':
        # Read the live dispatcher fence before any Docker mutation. The .1
        # scheduler must drain admission before issuing this action.
        require_drained()
        write_manual_stop()
        try:
            subprocess.run(['docker', 'update', '--restart=no', TTS],
                           capture_output=True, timeout=20, check=True)
        except (OSError, subprocess.SubprocessError):
            raise ControlError('TTS_RESTART_GUARD_FAILED') from None
        if container_running():
            docker_action('stop')
        if container_running():
            raise ControlError('TTS_STOP_NOT_CONFIRMED')
        return {'tts_running': False}
    if not container_running():
        docker_action('start')
    if not container_running():
        raise ControlError('TTS_START_NOT_CONFIRMED')
    clear_manual_stop()
    return {'tts_running': True}


def main():
    try:
        if fcntl is None:
            raise ControlError('LINUX_REQUIRED')
        request = json.load(sys.stdin)
        # The existing worker Agent uses agent.lock. The phase lock serializes
        # scheduler actions with other local GPU maintenance operations.
        AGENT_LOCK.parent.mkdir(parents=True, exist_ok=True)
        PHASE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        with AGENT_LOCK.open('a') as agent_lock, PHASE_LOCK.open('a') as phase_lock:
            try:
                fcntl.flock(agent_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ControlError('NODE20_BUSY') from None
            result = dispatch(request)
        print(json.dumps({'ok': True, 'result': result}))
        return 0
    except ControlError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'error': 'NODE20_CONTROL_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
