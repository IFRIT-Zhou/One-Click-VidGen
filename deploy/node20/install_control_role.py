#!/usr/bin/env python3
"""Install the .20 Agent guard for scheduler-owned manual TTS stops.

The default command validates only.  --apply patches the inspected Agent,
disables Docker's independent restart policy, and writes a root-owned marker.
It never starts or stops a model container.
"""
import argparse
try:
    import fcntl
except ImportError:  # Unit tests also run on Windows; installation is Linux-only.
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROLE_PATH = Path('/etc/ray-worker/gpu-scheduler-primary.json')
CONTROL_PATH = Path('/opt/ray-worker/bin/ocv-node20-control.py')
ROLE = {'schema': 1, 'owner': 'ocv-gpu-scheduler', 'node_ip': '10.250.0.20',
        'primary_role': 'tts', 'tts_container': 'ray-worker-managed'}
TAG = '# OCV_SCHEDULER_PRIMARY_GUARD_V1'
HELPER = '''# OCV_SCHEDULER_PRIMARY_GUARD_V1
def _scheduler_primary_role():
    role_path = pathlib.Path('/etc/ray-worker/gpu-scheduler-primary.json')
    if not role_path.exists():
        return False
    try:
        role = json.loads(role_path.read_text())
        if role != {'schema': 1, 'owner': 'ocv-gpu-scheduler',
                    'node_ip': '10.250.0.20', 'primary_role': 'tts',
                    'tts_container': 'ray-worker-managed'}:
            raise ValueError('role')
    except (OSError, ValueError, TypeError):
        raise AgentFailure('SCHEDULER_ROLE_INVALID', 'configuration') from None
    return True


def _scheduler_manual_stop():
    if not _scheduler_primary_role():
        return False
    marker = pathlib.Path('/var/lib/ray-worker/gpu-scheduler-manual-stop.json')
    if marker.is_symlink():
        raise AgentFailure('SCHEDULER_STOP_MARKER_INVALID', 'configuration')
    if not marker.exists():
        return False
    try:
        value = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError):
        raise AgentFailure('SCHEDULER_STOP_MARKER_INVALID', 'configuration') from None
    if value != {'schema': 1, 'owner': 'ocv-gpu-scheduler', 'stopped': True}:
        raise AgentFailure('SCHEDULER_STOP_MARKER_INVALID', 'configuration')
    return True


'''
PATCHES = (
    ('def stop_managed(config, report=None):\n',
     'def stop_managed(config, report=None):\n'
     '    if _scheduler_primary_role() and not container_running(managed_container(config)):\n'
     '        return\n'),
    ('def start_managed(config):\n', HELPER +
     'def start_managed(config):\n'
     '    if _scheduler_manual_stop():\n'
     '        raise AgentFailure("SCHEDULER_MANUAL_STOP", "configuration")\n'),
    ('    command = request["command"]\n',
     '    command = request["command"]\n'
     '    if command in {"join", "rejoin"} and _scheduler_manual_stop():\n'
     '        raise AgentFailure("SCHEDULER_MANUAL_STOP", "configuration")\n'),
    ('or restart_policy.get("Name") != "unless-stopped"):',
     'or restart_policy.get("Name") != ("no" if _scheduler_primary_role() else "unless-stopped")):'),
)


def patched_source(source):
    source = source.replace('\r\n', '\n')
    if TAG in source:
        if any(source.count(replacement) != 1 for _, replacement in PATCHES):
            raise RuntimeError('PARTIAL_OR_CHANGED_AGENT_GUARD')
        compile(source, 'worker-agent', 'exec')
        return source
    for anchor, replacement in PATCHES:
        if source.count(anchor) != 1:
            raise RuntimeError('UNRECOGNIZED_AGENT_SOURCE')
        source = source.replace(anchor, replacement, 1)
    compile(source, 'worker-agent', 'exec')
    return source


def atomic(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(agent_path, config_path, apply=False):
    config = json.loads(config_path.read_text())
    if config.get('node_ip') != ROLE['node_ip'] or config.get('container_name') != ROLE['tts_container']:
        raise RuntimeError('NOT_PRIMARY_TTS_NODE')
    if agent_path.is_symlink() or agent_path.stat().st_uid != 0:
        raise RuntimeError('AGENT_NOT_ROOT_OWNED_REGULAR_FILE')
    source = agent_path.read_text()
    candidate = patched_source(source)
    if ROLE_PATH.exists() and json.loads(ROLE_PATH.read_text()) != ROLE:
        raise RuntimeError('EXISTING_ROLE_CONFLICT')
    if CONTROL_PATH.is_symlink() or not CONTROL_PATH.is_file() or CONTROL_PATH.stat().st_uid != 0:
        raise RuntimeError('CONTROL_AGENT_NOT_INSTALLED')
    if not apply:
        return {'validated': True, 'changed': False}
    backup = agent_path.with_name(agent_path.name + '.pre-primary-' +
                                  hashlib.sha256(source.encode()).hexdigest()[:16])
    if not backup.exists():
        atomic(backup, source, 0o700)
    if candidate != source:
        atomic(agent_path, candidate, agent_path.stat().st_mode & 0o777)
    subprocess.run(['docker', 'update', '--restart=no', ROLE['tts_container']],
                   check=True, capture_output=True, timeout=30)
    atomic(ROLE_PATH, json.dumps(ROLE, sort_keys=True) + '\n', 0o600)
    return {'validated': True, 'changed': True, 'owner': ROLE['owner']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if fcntl is None or not hasattr(os, 'geteuid'):
        raise RuntimeError('LINUX_REQUIRED')
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED')
    with open('/var/lib/ray-worker/agent.lock', 'a') as agent_lock, \
            open('/run/lock/ocv-gpu-phase.lock', 'a') as phase_lock:
        fcntl.flock(agent_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = install(Path('/opt/ray-worker/bin/worker-agent'),
                         Path('/etc/ray-worker/config.json'), args.apply)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
