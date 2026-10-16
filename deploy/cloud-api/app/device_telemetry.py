"""Read-only device cache collector, independent of the GPU scheduler."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

NODE20 = '10.250.0.20'
CONTAINERS = {'tts': 'ray-worker-managed', 'h3': 'ocv-h3-comfyui'}
# Only names and running states leave the node. Never read container env,
# credentials, image authentication, or model files for this status endpoint.
INVENTORY = '''import json,subprocess
p=subprocess.run(['docker','ps','-a','--format','{{json .}}'],capture_output=True,text=True,timeout=8)
if p.returncode:raise RuntimeError('DOCKER_PROBE_FAILED')
rows=[json.loads(line) for line in p.stdout.splitlines() if line.strip()]
names={row['Names']:row['State'] for row in rows}
result={kind:{'installed':name in names,'running':names.get(name)=='running'} for kind,name in {'tts':'ray-worker-managed','h3':'ocv-h3-comfyui'}.items()}
role={'schema':1,'owner':'ocv-gpu-scheduler','node_ip':'10.250.0.20','primary_role':'tts','tts_container':'ray-worker-managed'}
try:
 installed=json.load(open('/etc/ray-worker/gpu-scheduler-primary.json'))==role
 agent=open('/opt/ray-worker/bin/ocv-node20-control.py').read()
 guard=open('/opt/ray-worker/bin/worker-agent').read()
 control_ready=installed and 'OCV_SCHEDULER_PRIMARY_GUARD_V1' in guard and '_scheduler_manual_stop()' in guard and "'stop_tts'" in agent
except (OSError,ValueError,TypeError):
 control_ready=False
print(json.dumps({'containers':result,'control_ready':control_ready}))
'''

# Querying a stopped WSL distribution would start it. Check the Windows running
# list first and report unknown without entering a stopped RayWorker distro.
NODE20_BRIDGE = '''import base64,json,subprocess
wsl='/mnt/c/Windows/System32/wsl.exe'
p=subprocess.run([wsl,'--list','--running','--quiet'],capture_output=True,timeout=6)
if p.returncode:raise RuntimeError('WSL_STATE_PROBE_FAILED')
raw=p.stdout
names=raw.decode('utf-16' if raw.startswith((b'\\xff\\xfe',b'\\xfe\\xff')) else 'utf-16le' if b'\\x00' in raw else 'utf-8').replace('\\x00','').splitlines()
if 'RayWorker' not in [name.strip() for name in names]:
 print(json.dumps({'reachable':False,'error':'WSL_NOT_RUNNING'}))
else:
 p=subprocess.run([wsl,'-d','RayWorker','-u','root','--','python3','-u','-'],input=base64.b64decode(INVENTORY_B64),capture_output=True,timeout=10)
 if p.returncode:raise RuntimeError('DOCKER_PROBE_FAILED')
 print(p.stdout.decode('utf-8'))
'''.replace('INVENTORY_B64', repr(base64.b64encode(INVENTORY.encode()).decode()))


def collect_node20(run=subprocess.run):
    result = run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
                  '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                  '-o', 'StrictHostKeyChecking=yes',
                  '-o', 'UserKnownHostsFile=/root/.ssh/known_hosts',
                  '-i', '/etc/ray-enrollment/tunnel-107', 'rayworker@' + NODE20,
                  'python3 -'],
                 input=NODE20_BRIDGE.encode(), capture_output=True, timeout=25)
    if result.returncode:
        raise RuntimeError('NODE_TRANSPORT_FAILED')
    data = json.loads(result.stdout)
    if data.get('reachable') is False and data.get('error') == 'WSL_NOT_RUNNING':
        return {'node_ip': NODE20, 'reachable': False, 'error': 'WSL_NOT_RUNNING'}
    containers = data.get('containers')
    if not isinstance(containers, dict):
        raise ValueError('INVALID_CONTAINER_OBSERVATION')
    for kind in CONTAINERS:
        item = containers.get(kind)
        if (not isinstance(item, dict) or not isinstance(item.get('installed'), bool)
                or not isinstance(item.get('running'), bool)):
            raise ValueError('INVALID_CONTAINER_OBSERVATION')
    if not isinstance(data.get('control_ready'), bool):
        raise ValueError('INVALID_CONTROL_CAPABILITY')
    return {'node_ip': NODE20, 'reachable': True, 'control_ready': data['control_ready'],
            'containers': {kind: {field: containers[kind][field] for field in ('installed', 'running')}
                           for kind in CONTAINERS}}


def collect_placement(run=subprocess.run):
    result = run(['/opt/ray-tts/head/.venv/bin/python', '/opt/cloud-api/tts_placement.py', 'snapshot'],
                 capture_output=True, timeout=50)
    if result.returncode:
        raise RuntimeError('PLACEMENT_PROBE_FAILED')
    data = json.loads(result.stdout)
    if data.get('ok') is not True:
        raise ValueError('INVALID_PLACEMENT_OBSERVATION')
    keys = ('ok', 'target_node', 'actual_pin', 'lifecycle_target', 'transaction_pending',
            'placement_ready', 'ready', 'worker_nodes', 'running_replicas', 'desired_replicas')
    safe = {key: data.get(key) for key in keys}
    safe['nodes'] = {ip: {'ray_alive': item.get('ray_alive')} for ip, item in (data.get('nodes') or {}).items()
                     if ip in ('10.250.0.3', NODE20) and isinstance(item, dict)}
    return safe


def atomic_cache(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o640)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_cache(path):
    """Read the last complete cache value without trusting malformed data."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def sample(path, collector, *, clock=time.time, writer=atomic_cache):
    # Preserve acquisition start. A slow/failed probe never refreshes an old
    # success timestamp, and exceptions cannot expose remote stderr or secrets.
    started = clock()
    try:
        payload = collector()
    except Exception as exc:
        payload = {'ok': False, 'reachable': False, 'error': type(exc).__name__}
        if Path(path).name == 'node20-health.json':
            payload['node_ip'] = NODE20
    if (Path(path).name == 'node20-health.json' and payload.get('error') == 'WSL_NOT_RUNNING'):
        # A stopped WSL distro proves its containers are not running. Reuse
        # only previously verified installation/control capability so Start
        # remains available without waking the distro during a read-only poll.
        previous = read_cache(path)
        containers = previous.get('containers') or {}
        tts = containers.get('tts') or {}
        if previous.get('control_ready') is True and tts.get('installed') is True:
            payload = {'node_ip': NODE20, 'reachable': True, 'control_ready': True,
                       'state_inferred': True,
                       'containers': {kind: {'installed': bool((containers.get(kind) or {}).get('installed')),
                                            'running': False}
                                      for kind in CONTAINERS}}
    writer(path, {**payload, 'time': started})
    return payload


def run_loop(path, collector, stop, interval):
    while not stop.is_set():
        before = time.monotonic()
        try:
            sample(path, collector)
        except OSError:
            # Cache write failure leaves the previous observation to expire.
            print(json.dumps({'event': 'telemetry_cache_write_failed', 'cache': path.name}), flush=True)
        stop.wait(max(1, interval - (time.monotonic() - before)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    root = Path(os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
    collectors = [(root / 'node20-health.json', collect_node20),
                  (root / 'placement-health.json', collect_placement)]
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    # Separate loops: a slow Ray read cannot delay Docker telemetry or vice versa.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='device-telemetry') as executor:
        futures = [executor.submit(sample, path, fn) if args.once else
                   executor.submit(run_loop, path, fn, stop, 30) for path, fn in collectors]
        for future in futures:
            future.result()


if __name__ == '__main__':
    main()
