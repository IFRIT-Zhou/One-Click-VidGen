import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('install_control_role', Path(__file__).with_name('install_control_role.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


AGENT_SOURCE = '''import json
import pathlib

class AgentFailure(Exception):
    pass

def managed_container(config):
    return config["container_name"]

def container_running(name):
    return True

def stop_managed(config, report=None):
    return None

def start_managed(config):
    return None

def command(request):
    command = request["command"]
    return command

def restart_policy(restart_policy):
    if (False or restart_policy.get("Name") != "unless-stopped"):
        return False
    return True
'''


class InstallControlRoleTests(unittest.TestCase):
    def test_guard_patches_known_agent_and_is_idempotent(self):
        patched = installer.patched_source(AGENT_SOURCE)
        self.assertIn(installer.TAG, patched)
        self.assertEqual(installer.patched_source(patched), patched)
        self.assertIn('if _scheduler_manual_stop():', patched)
        self.assertIn('"no" if _scheduler_primary_role() else "unless-stopped"', patched)

    def test_unrecognized_or_partial_agent_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'UNRECOGNIZED_AGENT_SOURCE'):
            installer.patched_source('def unrelated():\n    return True\n')
        with self.assertRaisesRegex(RuntimeError, 'PARTIAL_OR_CHANGED_AGENT_GUARD'):
            installer.patched_source(installer.TAG + '\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
