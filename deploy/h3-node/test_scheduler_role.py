import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('install_scheduler_role', Path(__file__).with_name('install_scheduler_role.py'))
role = importlib.util.module_from_spec(spec)
spec.loader.exec_module(role)

SOURCE = '''
def stop_managed(config, report=None):
    touched.append('drain')

def start_managed(config):
    touched.append('start')

def execute(request):
    command = request["command"]
    touched.append(command)

def validate(restart_policy):
    if (False
            or restart_policy.get("Name") != "unless-stopped"):
        raise AgentFailure('CONTAINER_CONFIG_MISMATCH', 'docker')
'''


class AgentFailure(Exception):
    pass


class RoleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'role.json'
        self.touched = []
        self.scope = {'pathlib': types.SimpleNamespace(Path=lambda _: self.path),
                      'json': json, 'AgentFailure': AgentFailure, 'touched': self.touched,
                      'managed_container': lambda config: {'running': config.get('running', False)},
                      'container_running': lambda container: container['running']}
        exec(role.patched_source(SOURCE), self.scope)

    def test_legacy_behavior_is_unchanged_without_marker(self):
        self.scope['execute']({'command': 'join'})
        self.scope['start_managed']({})
        self.scope['validate']({'Name': 'unless-stopped'})
        self.assertEqual(self.touched, ['join', 'start'])

    def test_guard_blocks_automatic_manual_join_and_direct_start(self):
        self.path.write_text(json.dumps(role.ROLE))
        for command in ('join', 'rejoin'):
            with self.assertRaisesRegex(AgentFailure, 'SCHEDULER_OWNS_GPU'):
                self.scope['execute']({'command': command})
        with self.assertRaisesRegex(AgentFailure, 'SCHEDULER_OWNS_GPU'):
            self.scope['start_managed']({})
        self.assertEqual(self.touched, [])

    def test_only_stopped_scheduler_container_skips_remote_drain(self):
        self.scope['stop_managed']({'running': False})
        self.assertEqual(self.touched, ['drain'])
        self.path.write_text(json.dumps(role.ROLE))
        self.touched.clear()
        self.scope['stop_managed']({'running': False})
        self.assertEqual(self.touched, [])
        self.scope['stop_managed']({'running': True})
        self.assertEqual(self.touched, ['drain'])

    def test_dedicated_container_validation_requires_no_restart(self):
        self.path.write_text(json.dumps(role.ROLE))
        self.scope['validate']({'Name': 'no'})
        with self.assertRaisesRegex(AgentFailure, 'CONFIG_MISMATCH'):
            self.scope['validate']({'Name': 'unless-stopped'})

    def test_status_and_pause_still_use_existing_agent(self):
        self.path.write_text(json.dumps(role.ROLE))
        self.scope['execute']({'command': 'status'})
        self.scope['execute']({'command': 'pause'})
        self.assertEqual(self.touched, ['status', 'pause'])

    def test_invalid_role_fails_closed_before_start(self):
        for value in ('invalid json', '{}', json.dumps({**role.ROLE, 'node_ip': '10.250.0.20'})):
            self.path.write_text(value)
            with self.assertRaisesRegex(AgentFailure, 'SCHEDULER_ROLE_INVALID'):
                self.scope['execute']({'command': 'join'})
        self.assertEqual(self.touched, [])

    def test_patch_is_idempotent_and_rejects_unknown_or_partial_source(self):
        patched = role.patched_source(SOURCE)
        self.assertEqual(role.patched_source(patched), patched)
        with self.assertRaisesRegex(RuntimeError, 'UNRECOGNIZED_AGENT_SOURCE'):
            role.patched_source(SOURCE.replace('def start_managed(config):', 'def unknown(config):'))
        with self.assertRaisesRegex(RuntimeError, 'PARTIAL_OR_CHANGED_AGENT_GUARD'):
            role.patched_source(patched.replace('if command in {"join", "rejoin"}', 'if command == "join"'))

    def test_installer_dry_run_and_safe_write_order_never_start_or_stop(self):
        agent_path=self.path.parent/'worker-agent';agent_path.write_text(SOURCE)
        config_path=self.path.parent/'config.json'
        config_path.write_text(json.dumps({'node_ip':'10.250.0.3','container_name':'ray-worker-managed'}))
        calls=[]
        def write(path, content, mode):
            calls.append(path.name)
            path.write_text(content)
        def docker(args, **kwargs):
            self.assertEqual(args,['docker','update','--restart=no','ray-worker-managed'])
            self.assertIn(role.TAG,agent_path.read_text())
            self.assertFalse(self.path.exists())
            calls.append('docker-no')
        with patch.object(role,'ROLE_PATH',self.path),patch.object(role,'atomic',side_effect=write),patch.object(role.subprocess,'run',side_effect=docker) as run:
            self.assertFalse(role.install(agent_path,config_path)['changed'])
            self.assertEqual(calls,[]);run.assert_not_called()
            self.assertTrue(role.install(agent_path,config_path,apply=True)['changed'])
        self.assertEqual(calls[-3:],['worker-agent','docker-no','role.json'])
        self.assertEqual(json.loads(self.path.read_text()),role.ROLE)

    def test_installer_refuses_other_nodes_without_mutation(self):
        config_path=self.path.parent/'config.json'
        config_path.write_text(json.dumps({'node_ip':'10.250.0.20','container_name':'ray-worker-managed'}))
        with patch.object(role.subprocess,'run') as run,patch.object(role,'atomic') as write:
            with self.assertRaisesRegex(RuntimeError,'NOT_DEDICATED_H3_NODE'):
                role.install(self.path.parent/'missing-agent',config_path,apply=True)
        run.assert_not_called();write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
