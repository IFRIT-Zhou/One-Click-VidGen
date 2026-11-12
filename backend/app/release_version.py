"""Shared release identity for the application and Launcher."""
import json
import subprocess
import time
from pathlib import Path

_cache = (0.0, {})


def release_info():
    global _cache
    if time.monotonic() - _cache[0] < 30:
        return dict(_cache[1])
    root = Path(__file__).resolve().parents[2]
    try:
        channel = json.loads((root / 'launcher/update-channel.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        channel = {}
    release = str(channel.get('release_id') or '未标记版本')
    commit, dirty = '', None
    if (root / '.git').exists():
        try:
            options = dict(cwd=root, capture_output=True, text=True, timeout=3,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], **options)
            status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], **options)
            if head.returncode == 0:
                commit = head.stdout.strip()
            if status.returncode == 0:
                dirty = bool(status.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    label = 'OCV ' + release
    if commit:
        label += ' · ' + commit
    if dirty:
        label += ' · 本地修改版'
    info = dict(release_id=release, commit=commit, modified=dirty, label=label)
    _cache = (time.monotonic(), info)
    return dict(info)
