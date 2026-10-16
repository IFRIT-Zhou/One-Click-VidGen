#!/usr/bin/env python3
"""Opt-in .3 GPU ownership guard; patch the installed Agent without replacing it.

Default is read-only validation. --apply changes only local Agent source,
Docker restart policy and a root-owned role marker; it never starts/stops work.
Run after reviewing the current .3 startup chain and recording a backup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROLE_PATH = Path('/etc/ray-worker/gpu-scheduler-role.json')
ROLE = {'schema': 1, 'owner': 'ocv-gpu-scheduler', 'node_ip': '10.250.0.3',
        'primary_role': 'h3', 'tts_container': 'ray-worker-managed'}
TAG = '# OCV_SCHEDULER_GPU_GUARD_V1'
HELPER = '''# OCV_SCHEDULER_GPU_GUARD_V1
def _scheduler_owns_gpu():
    role_path = pathlib.Path('/etc/ray-worker/gpu-scheduler-role.json')
    if not role_path.exists():
        return False
    try:
        role = json.loads(role_path.read_text())
        if role != {'schema': 1, 'owner': 'ocv-gpu-scheduler',
                    'node_ip': '10.250.0.3', 'primary_role': 'h3',
                    'tts_container': 'ray-worker-managed'}:
            raise ValueError('role')
    except (OSError, ValueError, TypeError):
        raise AgentFailure('SCHEDULER_ROLE_INVALID', 'configuration') from None
    return True


'''

PATCHES = (
    ('def stop_managed(config, report=None):\n',
     'def stop_managed(config, report=None):\n'
     '    # The scheduler may already have stopped this fallback container.\n'
     '    # Record a real local pause without asking the other node to drain.\n'
     '    if _scheduler_owns_gpu() and not container_running(managed_container(config)):\n'
     '        return\n'),
    ('def start_managed(config):\n', HELPER +
     'def start_managed(config):\n'
     '    if _scheduler_owns_gpu():\n'
     '        raise AgentFailure("SCHEDULER_OWNS_GPU", "configuration")\n'),
    ('    command = request["command"]\n',
     '    command = request["command"]\n'
     '    if command in {"join", "rejoin"} and _scheduler_owns_gpu():\n'
     '        raise AgentFailure("SCHEDULER_OWNS_GPU", "configuration")\n'),
    ('or restart_policy.get("Name") != "unless-stopped"):',
     'or restart_policy.get("Name") != ("no" if _scheduler_owns_gpu() else "unless-stopped")):'),
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
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(agent_path, config_path, apply=False):
    config = json.loads(config_path.read_text())
    if (config.get('node_ip') != ROLE['node_ip'] or
            config.get('container_name') != ROLE['tts_container']):
        raise RuntimeError('NOT_DEDICATED_H3_NODE')
    if agent_path.is_symlink() or agent_path.stat().st_uid != 0:
        raise RuntimeError('AGENT_NOT_ROOT_OWNED_REGULAR_FILE')
    source = agent_path.read_text()
    candidate = patched_source(source)
    if ROLE_PATH.exists() and json.loads(ROLE_PATH.read_text()) != ROLE:
        raise RuntimeError('EXISTING_ROLE_CONFLICT')
    if not apply:
        return {'validated': True, 'changed': False}
    # Caller holds both the Agent and node GPU action locks. No restart or drain.
    backup = agent_path.with_name(agent_path.name + '.pre-scheduler-' +
                                  hashlib.sha256(source.encode()).hexdigest()[:16])
    if not backup.exists():
        atomic(backup, source, 0o700)
    if candidate != source:
        atomic(agent_path, candidate, agent_path.stat().st_mode & 0o777)
    # With no marker yet, patched Agent expects unless-stopped and rejects the
    # no-policy container. This ordering fails closed if interrupted here.
    subprocess.run(['docker', 'update', '--restart=no', ROLE['tts_container']],
                   check=True, capture_output=True, timeout=30)
    atomic(ROLE_PATH, json.dumps(ROLE, sort_keys=True) + '\n', 0o600)
    return {'validated': True, 'changed': True, 'owner': ROLE['owner']}


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED')
    # Match the two existing locks; do not race a BAT/client join or scheduler.
    with open('/var/lib/ray-worker/agent.lock', 'a') as agent_lock, \
            open('/run/lock/ocv-gpu-phase.lock', 'a') as phase_lock:
        fcntl.flock(agent_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = install(Path('/opt/ray-worker/bin/worker-agent'),
                         Path('/etc/ray-worker/config.json'), args.apply)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
