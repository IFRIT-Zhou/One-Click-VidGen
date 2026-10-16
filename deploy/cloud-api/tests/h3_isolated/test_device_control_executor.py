import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.device_control_executor import DeviceControlExecutor
from app.service_control import ServiceControlError


class DeviceControlExecutorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.scheduler = SimpleNamespace(
            h3=SimpleNamespace(has_pending=Mock(return_value=False), has_inflight=Mock(return_value=False)),
            h3_future=None, target_node='10.250.0.20', h3_resident=True, h3_state='ready',
            h3_checked_at=0, retry_h3_at=15, clock=lambda: 100,
            last_probe=10, probe={'ok': True}, primary_state='online', up=1, down=0,
        )
        self.node3 = SimpleNamespace(call=Mock(side_effect=self.call_node3))
        self.node20 = SimpleNamespace(call=Mock(side_effect=self.call_node20))
        self.tts = SimpleNamespace(snapshot=Mock(return_value={'queue_depth': 0, 'active_jobs': 0}),
                                   drain=Mock(return_value={'draining': True, 'drained': True, 'active_jobs': 0}))
        self.phases = SimpleNamespace(ensure_h3=Mock(), h3_ready=Mock(return_value=True))
        self.placement = SimpleNamespace(snapshot=Mock(return_value={
            'ok': True, 'transaction_pending': False, 'target_node': '10.250.0.20',
            'actual_pin': '10.250.0.20', 'lifecycle_target': '10.250.0.20',
        }))
        self.executor = DeviceControlExecutor(self.node3, self.node20, scheduler=self.scheduler,
                                              tts=self.tts, phases=self.phases,
                                              placement=self.placement, root='unused')

    def call_node3(self, action):
        self.calls.append(('node3', action))
        if action == 'health':
            return {'h3_running': self.calls[-2][1] != 'stop_h3', 'h3_ready': True}
        return {'ok': True}

    def call_node20(self, action):
        self.calls.append(('node20', action))
        return {'tts_running': action == 'start_tts'}

    def test_tts_stop_requires_expected_placement_and_drain(self):
        result = self.executor._run({'node_ip': '10.250.0.20', 'service': 'tts', 'action': 'stop'})
        self.assertFalse(result['container_running'])
        self.tts.drain.assert_called_once()
        self.node20.call.assert_called_once_with('stop_tts')
        self.assertEqual(self.scheduler.primary_state, 'unknown')
        self.assertIsNone(self.scheduler.probe)

    def test_tts_stop_rejects_queue_without_mutation(self):
        self.tts.snapshot.return_value = {'queue_depth': 1, 'active_jobs': 0}
        with self.assertRaises(ServiceControlError) as caught:
            self.executor._run({'node_ip': '10.250.0.20', 'service': 'tts', 'action': 'stop'})
        self.assertEqual(caught.exception.code, 'SERVICE_BUSY')
        self.tts.drain.assert_not_called()
        self.node20.call.assert_not_called()

    def test_tts_stop_rejects_placement_drift(self):
        self.placement.snapshot.return_value['actual_pin'] = '10.250.0.3'
        with self.assertRaises(ServiceControlError) as caught:
            self.executor._run({'node_ip': '10.250.0.20', 'service': 'tts', 'action': 'stop'})
        self.assertEqual(caught.exception.code, 'PLACEMENT_UNKNOWN')
        self.tts.snapshot.assert_not_called()
        self.node20.call.assert_not_called()

    def test_h3_stop_rejects_pending_work(self):
        self.scheduler.h3.has_pending.return_value = True
        with self.assertRaises(ServiceControlError) as caught:
            self.executor._run({'node_ip': '10.250.0.3', 'service': 'h3', 'action': 'stop'})
        self.assertEqual(caught.exception.code, 'SERVICE_BUSY')
        self.node3.call.assert_not_called()

    def test_h3_start_verifies_readiness_and_updates_scheduler(self):
        self.scheduler.target_node = '10.250.0.3'
        self.node3.call.side_effect = lambda action: {'h3_running': True, 'h3_ready': True}
        result = self.executor._run({'node_ip': '10.250.0.3', 'service': 'h3', 'action': 'start'})
        self.assertTrue(result['container_running'])
        self.phases.ensure_h3.assert_called_once()
        self.assertTrue(self.scheduler.h3_resident)
        self.assertEqual(self.scheduler.h3_state, 'ready')


if __name__ == '__main__':
    unittest.main(verbosity=2)
