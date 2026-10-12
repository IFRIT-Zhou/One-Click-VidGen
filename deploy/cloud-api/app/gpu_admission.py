"""Public non-sensitive admission hint, backed by fresh scheduler health."""
import json
import os
from pathlib import Path
import time


def tts_admission(*, now=None, state_path=None):
    enabled = os.environ.get('GPU_SCHEDULER_ENABLED', '0') == '1'
    result = {'accepting': False, 'on_demand': enabled, 'scheduler_ready': False, 'queue_ready': False}
    if not enabled:
        return result
    path = Path(state_path or os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
    try:
        state = json.loads((path / 'heartbeat.json').read_text())
        age = (time.time() if now is None else now) - float(state['time'])
        fresh = 0 <= age <= 15
        result['scheduler_ready'] = fresh and state.get('scheduler_ready') is True
        result['queue_ready'] = fresh and state.get('queue_ready') is True
        result['accepting'] = result['scheduler_ready'] and result['queue_ready']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result
