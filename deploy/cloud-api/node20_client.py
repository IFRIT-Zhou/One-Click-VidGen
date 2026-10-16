"""Fixed, bounded control calls to the resident TTS node over its pinned SSH link."""
import base64
import json
import subprocess


class Node20Client:
    def __init__(self, *, run=subprocess.run, timeout=210):
        self.run, self.timeout = run, timeout

    def call(self, action):
        if action not in {'status', 'start_tts', 'stop_tts'}:
            raise ValueError('Unsupported node20 action')
        request = base64.b64encode(json.dumps({'action': action}).encode()).decode()
        bridge = (
            'import base64,subprocess,sys\n'
            "r=subprocess.run(['/mnt/c/Windows/System32/wsl.exe','-d','RayWorker','-u','root','--',"
            "'python3','/opt/ray-worker/bin/ocv-node20-control.py'],"
            'input=base64.b64decode(' + repr(request) + '),capture_output=True,timeout=180)\n'
            'sys.stdout.buffer.write(r.stdout)\nsys.exit(r.returncode)\n'
        )
        result = self.run([
            'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/root/.ssh/known_hosts',
            '-i', '/etc/ray-enrollment/tunnel-107', 'rayworker@10.250.0.20', 'python3', '-',
        ], input=bridge.encode(), capture_output=True, timeout=self.timeout)
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeError, TypeError):
            raise RuntimeError('NODE20_INVALID_RESPONSE') from None
        if result.returncode or not isinstance(response, dict) or response.get('ok') is not True:
            error = response.get('error') if isinstance(response, dict) else None
            raise RuntimeError(error if isinstance(error, str) else 'NODE20_CONTROL_FAILED')
        value = response.get('result')
        if not isinstance(value, dict):
            raise RuntimeError('NODE20_INVALID_RESPONSE')
        return value
