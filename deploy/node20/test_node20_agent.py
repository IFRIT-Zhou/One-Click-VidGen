import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location('node20_agent', Path(__file__).with_name('node20_agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class Node20AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        agent.ROLE_PATH = self.root / 'role.json'
        agent.CONFIG_PATH = self.root / 'config.json'
        agent.LIFECYCLE_PATH = self.root / 'lifecycle.json'
        agent.MANUAL_STOP = self.root / 'manual-stop.json'
        agent.AGENT_PATH = self.root / 'worker-agent'
        agent.ROLE_PATH.write_text(json.dumps(agent.ROLE))
        agent.CONFIG_PATH.write_text(json.dumps({'node_ip': agent.NODE_IP, 'container_name': agent.TTS}))
        agent.AGENT_PATH.write_text(agent.GUARD_TAG + '\nif _scheduler_manual_stop():\n')
        self.running = True
        self.docker = Mock()
        self.docker_run_original = agent.subprocess.run
        agent.subprocess.run = self.docker
        self.drain = Mock()
        agent.require_drained = self.drain
        agent.container_running = Mock(side_effect=lambda: self.running)
        agent.docker_action = Mock(side_effect=self.do_docker)

    def tearDown(self):
        agent.subprocess.run = self.docker_run_original
        self.temp.cleanup()

    def do_docker(self, verb):
        self.running = verb == 'start'

    def test_stop_writes_manual_guard_and_verifies_container_stopped(self):
        result = agent.dispatch({'action': 'stop_tts'})
        self.assertEqual(result, {'tts_running': False})
        self.drain.assert_called_once()
        self.assertTrue(agent.manual_stopped())
        self.docker.assert_called_once()
        self.assertEqual(self.docker.call_args.args[0], ['docker', 'update', '--restart=no', agent.TTS])
        agent.docker_action.assert_called_once_with('stop')

    def test_stop_does_not_mutate_when_drain_fails(self):
        self.drain.side_effect = agent.ControlError('TTS_NOT_DRAINED')
        with self.assertRaisesRegex(agent.ControlError, 'TTS_NOT_DRAINED'):
            agent.dispatch({'action': 'stop_tts'})
        self.assertFalse(agent.MANUAL_STOP.exists())
        self.docker.assert_not_called()
        agent.docker_action.assert_not_called()

    def test_start_clears_manual_guard_only_after_running(self):
        agent.write_manual_stop()
        self.running = False
        result = agent.dispatch({'action': 'start_tts'})
        self.assertEqual(result, {'tts_running': True})
        self.assertFalse(agent.MANUAL_STOP.exists())
        agent.docker_action.assert_called_once_with('start')

    def test_invalid_role_fails_before_mutation(self):
        agent.ROLE_PATH.write_text('{}')
        with self.assertRaisesRegex(agent.ControlError, 'SCHEDULER_ROLE_INVALID'):
            agent.dispatch({'action': 'start_tts'})
        agent.docker_action.assert_not_called()

    def test_stop_marker_corruption_fails_closed(self):
        agent.MANUAL_STOP.write_text('{bad')
        with self.assertRaisesRegex(agent.ControlError, 'MANUAL_STOP_MARKER_INVALID'):
            agent.dispatch({'action': 'start_tts'})
        agent.docker_action.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
