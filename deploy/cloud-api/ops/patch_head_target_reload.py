#!/usr/bin/env python3
"""Persisted worker selection must survive a future deploy-loop reconnect.

Patches disk only. Never restart deploy_loop separately: start-head.sh's EXIT
trap stops the entire Ray head. The running loop adopts this on its next normal
process start, not on the current process's next reconnect.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil

ANCHOR = '    from service.app import app\n'
REPLACEMENT = '''    # OCV_PERSISTED_TTS_TARGET_V1
    import importlib
    from pathlib import Path
    env_path = Path(__file__).resolve().parent / '.env'
    targets = [line.split('=', 1)[1].strip() for line in env_path.read_text().splitlines()
               if line.startswith('TTS_WORKER_NODE_IP=')]
    if len(targets) != 1 or targets[0] not in {'10.250.0.3', '10.250.0.20'}:
        raise RuntimeError('Persisted TTS target is unavailable')
    os.environ['TTS_WORKER_NODE_IP'] = targets[0]
    import service.app as service_app
    app = importlib.reload(service_app).app
'''


def patch(path, apply=False):
    source = path.read_text()
    if REPLACEMENT in source:
        return {'changed': False, 'validated': True}
    if source.count(ANCHOR) != 1:
        raise RuntimeError('UNRECOGNIZED_DEPLOY_LOOP')
    candidate = source.replace(ANCHOR, REPLACEMENT)
    compile(candidate, str(path), 'exec')
    if not apply:
        return {'changed': False, 'validated': True}
    backup = path.with_name(path.name + '.before-dedicated-' + hashlib.sha256(source.encode()).hexdigest()[:16])
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = path.with_name(path.name + '.dedicated-new')
    temporary.write_text(candidate)
    shutil.copymode(path, temporary)
    os.replace(temporary, path)
    return {'changed': True, 'validated': True, 'activation': 'next_normal_process_start'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--path', type=Path, default=Path('/opt/ray-tts/head/deploy_loop.py'))
    args = parser.parse_args()
    print(patch(args.path, args.apply))
