"""Private head-to-node transport over the existing SSH administrative link."""
import base64
import json
import subprocess


class Node3Client:
    def call(self, action, **kwargs):
        payload = json.dumps({'action': action, **kwargs}).encode()
        encoded = base64.b64encode(payload).decode()
        # Only a base64 JSON literal enters Python code. User text is never shell code.
        bridge = "import subprocess,base64,sys\nr=subprocess.run(['/mnt/c/Windows/System32/wsl.exe','-d','RayWorker','-u','root','--','python3','/opt/ocv-h3/node3_agent.py'],input=base64.b64decode(" + repr(encoded) + "),stdout=subprocess.PIPE,stderr=subprocess.PIPE);sys.stdout.buffer.write(r.stdout);sys.exit(r.returncode)\n"
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/etc/ray-enrollment/ssh_known_hosts',
            '-i', '/etc/ray-enrollment/tunnel-107', 'rayworker@10.250.0.3', 'python3 -'],
            input=bridge.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError('Node3 transport did not return a valid response') from exc
        if result.returncode or not response.get('ok'):
            raise RuntimeError(response.get('error', 'Node3 operation failed'))
        return response['result']
