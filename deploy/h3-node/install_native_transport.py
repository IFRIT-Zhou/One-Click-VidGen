#!/usr/bin/env python3
"""Allow the enrolled Linux user to invoke only the fixed H3 adapter as root."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

POLICY = 'rayworker ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/ocv-h3/node3_agent.py\n'
TARGET = Path('/etc/sudoers.d/ocv-h3-agent')


def main():
    if os.geteuid() != 0:
        raise SystemExit('ROOT_REQUIRED')
    for name in ('/opt', '/opt/ocv-h3', '/opt/ocv-h3/node3_agent.py'):
        path = Path(name)
        st = path.lstat()
        if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
            raise SystemExit('ADAPTER_PATH_NOT_ROOT_CONTROLLED')
    visudo = shutil.which('visudo')
    if not visudo:
        raise SystemExit('VISUDO_REQUIRED')
    subprocess.run([visudo, '-c'], check=True, capture_output=True)
    if TARGET.exists():
        if TARGET.is_symlink() or TARGET.read_text() != POLICY:
            raise SystemExit('EXISTING_POLICY_CONFLICT')
        st = TARGET.stat()
        if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o440:
            raise SystemExit('EXISTING_POLICY_PERMISSIONS_INVALID')
        print('NATIVE_TRANSPORT_ALREADY_CONFIGURED')
        return
    fd, name = tempfile.mkstemp(prefix='.ocv-h3-', dir=str(TARGET.parent))
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(POLICY)
        os.chmod(name, 0o440)
        subprocess.run([visudo, '-cf', name], check=True, capture_output=True)
        # Never replace an unrelated policy created since the check.
        os.link(name, TARGET)
    finally:
        os.unlink(name)
    print('NATIVE_TRANSPORT_CONFIGURED_EXACT_COMMAND_ONLY')


if __name__ == '__main__':main()
