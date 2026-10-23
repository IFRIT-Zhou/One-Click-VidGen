"""Local-only Launcher probes. No PowerShell and no system HTTP proxy."""
import json
import socket
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

OPENER = build_opener(ProxyHandler({}))


def fetch(url):
    if not url.startswith('http://127.0.0.1:'):
        raise ValueError('Only loopback HTTP probes are allowed')
    with OPENER.open(url, timeout=2) as response:
        return response.read(1048576)


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def backend(root):
    if not listening(8010):
        print('[PROBE] Backend port is closed; backend needs starting.')
        return 1
    # A listener is not proof that it is OCV. Never kill an unknown process.
    data = json.loads(fetch('http://127.0.0.1:8010/api/health'))
    started = float(data['server_started_at'])
    if started <= 0:
        raise ValueError('Invalid backend start timestamp')
    files = list((root / 'backend/app').rglob('*.py')) + list(root.glob('*.py'))
    stale = any(p.stat().st_mtime > started + 2 for p in files)
    if stale:
        return 3 if data.get('active_task_count') is None or int(data['active_task_count']) > 0 else 2
    return 0


def main(args):
    mode, target = args
    if mode == 'backend':
        return backend(Path(target))
    deadline = time.monotonic() + (45 if mode == 'wait' else 10 if mode == 'closed' else 0)
    last = ''
    while True:
        try:
            if mode == 'closed':
                if not listening(int(target)):
                    return 0
            elif mode in ('url', 'wait'):
                fetch(target)
                return 0
            else:
                raise ValueError('Unknown probe mode')
        except Exception as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            print('[PROBE] Not ready: ' + target + ' ' + last, flush=True)
            return 1
        time.sleep(0.5)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print('[ERROR] Service probe failed: ' + str(exc), file=sys.stderr, flush=True)
        sys.exit(10)

