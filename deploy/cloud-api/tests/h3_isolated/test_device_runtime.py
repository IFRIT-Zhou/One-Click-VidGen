import importlib.util
import __future__
import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys
import types

ROOT = Path(__file__).parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'app' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('device_runtime')
t = load('device_telemetry')


class DeviceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.node3 = {'time': 100, 'reachable': True, 'tts_running': False,
                      'h3_running': True, 'h3_ready': True, 'exclusive': True,
                      'control_ready': True}
        self.node20 = {'time': 100, 'node_ip': '10.250.0.20', 'reachable': True,
                       'control_ready': True,
                       'containers': {'tts': {'installed': True, 'running': True},
                                      'h3': {'installed': False, 'running': False}}}
        self.placement = {'time': 100, 'ok': True, 'target_node': '10.250.0.20',
                          'actual_pin': '10.250.0.20', 'lifecycle_target': '10.250.0.20',
                          'transaction_pending': False, 'placement_ready': True, 'ready': True,
                          'nodes': {'10.250.0.3': {'ray_alive': False}, '10.250.0.20': {'ray_alive': True}}}

    def service(self, ip, node=None, placement=None, now=100):
        return m.device_services(ip, now=now, node=node if node is not None else self.node3,
                                 placement=placement if placement is not None else self.placement)

    def test_h3_only_node_is_ready_without_ray(self):
        out = self.service('10.250.0.3/32')
        self.assertEqual(out['h3']['status'], 'ready')
        self.assertIsNone(out['h3']['ray_connected'])
        self.assertFalse(out['tts']['ray_connected'])
        self.assertEqual(out['tts']['status'], 'stopped')

    def test_primary_tts_does_not_claim_h3_installed(self):
        out = self.service('10.250.0.20', self.node20)
        self.assertEqual(out['tts']['status'], 'ready')
        self.assertTrue(out['tts']['ray_connected'])
        self.assertEqual(out['h3']['status'], 'not_installed')
        self.assertIsNone(out['h3']['ray_connected'])

    def test_ready_is_not_copied_to_non_target_running_node(self):
        self.node3.update(tts_running=True, h3_running=False, h3_ready=False)
        self.placement['nodes']['10.250.0.3']['ray_alive'] = True
        out = self.service('10.250.0.3')
        self.assertEqual(out['tts']['status'], 'running')
        self.assertFalse(out['tts']['ready'])

    def test_node_data_cannot_cross_device_rows(self):
        out = self.service('10.250.0.20', {**self.node20, 'node_ip': '10.250.0.3'})
        self.assertEqual(out['tts']['status'], 'unknown')
        self.assertIsNone(out['tts']['container_running'])

    def test_stale_and_future_samples_are_unknown(self):
        for now in (99, 146):
            with self.subTest(now=now):
                out = self.service('10.250.0.3', now=now)
                self.assertEqual(out['h3']['status'], 'unknown')
                self.assertIsNone(out['h3']['ready'])
                self.assertIsNone(out['tts']['container_running'])

    def test_probe_failure_does_not_present_stale_success(self):
        out = self.service('10.250.0.3', {**self.node3, 'reachable': False})
        self.assertEqual(out['h3']['status'], 'unknown')
        self.assertIsNone(out['h3']['ready'])

    def test_placement_failure_does_not_hide_real_h3_health(self):
        out = self.service('10.250.0.3', placement={'time': 100, 'ok': False})
        self.assertEqual(out['h3']['status'], 'ready')
        self.assertEqual(out['tts']['status'], 'stopped')
        self.assertIsNone(out['tts']['ray_connected'])
        out = self.service('10.250.0.20', self.node20, {'time': 100, 'ok': False})
        self.assertEqual(out['tts']['status'], 'unknown')
        self.assertTrue(out['tts']['container_running'])
        self.assertIsNone(out['tts']['ready'])

    def test_stale_placement_and_lifecycle_mismatch_never_ready(self):
        for change in ({'time': 0}, {'lifecycle_target': '10.250.0.3'}, {'transaction_pending': True}):
            with self.subTest(change=change):
                out = self.service('10.250.0.20', self.node20, {**self.placement, **change})
                self.assertIsNot(out['tts']['ready'], True)

    def test_unmonitored_node_is_unknown_not_not_installed(self):
        out = self.service('10.250.0.19')
        self.assertEqual(out['tts']['status'], 'unknown')
        self.assertIsNone(out['tts']['container_name'])
        self.assertEqual(out['h3']['status'], 'unknown')

    def test_h3_gpu_conflict_prevents_ready(self):
        out = self.service('10.250.0.3', {**self.node3, 'exclusive': False})
        self.assertEqual(out['h3']['status'], 'running')
        self.assertFalse(out['h3']['ready'])

    def test_busy_model_health_timeout_is_uncertain_not_failure(self):
        out = self.service('10.250.0.20', self.node20, {**self.placement, 'ready': False})
        self.assertEqual(out['tts']['status'], 'running')
        self.assertIsNone(out['tts']['ready'])

    def test_api_path_reads_cache_without_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'node-health.json').write_text(json.dumps(self.node3))
            (root/'placement-health.json').write_text(json.dumps(self.placement))
            out = m.device_services('10.250.0.3', now=100, root=root)
            self.assertTrue(out['h3']['ready'])
            (root/'node-health.json').write_text('broken')
            self.assertEqual(m.device_services('10.250.0.3', now=100, root=root)['h3']['status'], 'unknown')

    def test_schema_is_stable(self):
        expected = {'container_name', 'container_running', 'ray_connected', 'ready',
                    'status', 'reason', 'checked_at', 'source', 'control_ready'}
        for service in self.service('10.250.0.3').values():
            self.assertEqual(set(service), expected)
            self.assertTrue(service['checked_at'].endswith('+00:00'))


class TelemetryTests(unittest.TestCase):
    def test_sample_preserves_acquisition_time_and_redacts_failure(self):
        writes = []
        clock = Mock(return_value=100)
        collector = Mock(side_effect=RuntimeError('SECRET_REMOTE_STDERR'))
        t.sample(Path('node20-health.json'), collector, clock=clock,
                 writer=lambda p, value: writes.append(value))
        self.assertEqual(writes[0]['time'], 100)
        self.assertFalse(writes[0]['reachable'])
        self.assertEqual(writes[0]['node_ip'], '10.250.0.20')
        self.assertNotIn('SECRET', json.dumps(writes))

    def test_stopped_rayworker_uses_only_prior_verified_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'node20-health.json'
            path.write_text(json.dumps({'node_ip':'10.250.0.20','reachable':True,'control_ready':True,
                'containers':{'tts':{'installed':True,'running':True},'h3':{'installed':False,'running':False}}}))
            t.sample(path, lambda: {'node_ip':'10.250.0.20','reachable':False,'error':'WSL_NOT_RUNNING'},
                     clock=lambda:100)
            value = json.loads(path.read_text())
            self.assertTrue(value['reachable'])
            self.assertTrue(value['state_inferred'])
            self.assertTrue(value['control_ready'])
            self.assertFalse(value['containers']['tts']['running'])
            placement = {'time': 100, 'ok': True, 'target_node': '10.250.0.20',
                         'actual_pin': '10.250.0.20', 'lifecycle_target': '10.250.0.20',
                         'transaction_pending': False, 'placement_ready': True, 'ready': True,
                         'nodes': {'10.250.0.20': {'ray_alive': False}}}
            result = m.device_services('10.250.0.20', now=100, node=value, placement=placement)
            self.assertEqual(result['tts']['status'], 'stopped')
            self.assertTrue(result['tts']['control_ready'])

    def test_collection_failures_are_independent(self):
        writes = {}
        writer = lambda p, value: writes.update({str(p): value})
        t.sample(Path('node20-health.json'), Mock(side_effect=OSError()), clock=lambda: 1, writer=writer)
        t.sample(Path('placement-health.json'), lambda: {'ok': True}, clock=lambda: 2, writer=writer)
        self.assertFalse(writes['node20-health.json']['reachable'])
        self.assertTrue(writes['placement-health.json']['ok'])

    def test_placement_only_uses_readonly_snapshot_and_safe_fields(self):
        run = Mock(return_value=Mock(returncode=0, stdout=json.dumps({
            'ok': True, 'token': 'SECRET', 'nodes': {'10.250.0.20': {'ray_alive': True, 'secret': 'SECRET'}}}).encode()))
        result = t.collect_placement(run)
        self.assertEqual(run.call_args.args[0][-1], 'snapshot')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_docker_probe_uses_strict_existing_trust_and_no_mutation(self):
        containers = {key: {'installed': key == 'tts', 'running': key == 'tts'} for key in t.CONTAINERS}
        run = Mock(return_value=Mock(returncode=0, stdout=json.dumps({
            'containers': containers, 'control_ready': True}).encode()))
        out = t.collect_node20(run)
        self.assertTrue(out['containers']['tts']['running'])
        self.assertTrue(out['control_ready'])
        self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])
        source = run.call_args.kwargs['input'].decode()
        self.assertIn("'--list','--running','--quiet'", source)
        self.assertIn("['docker','ps','-a'", t.INVENTORY)
        self.assertNotIn('inspect', t.INVENTORY)
        self.assertNotIn('restart', t.INVENTORY)

    def test_bridge_does_not_start_stopped_wsl(self):
        run = Mock(return_value=Mock(returncode=0, stdout='Ubuntu\r\n'.encode('utf-16le')))
        output = io.StringIO()
        with patch.dict(sys.modules, {'subprocess': Mock(run=run)}), contextlib.redirect_stdout(output):
            exec(t.NODE20_BRIDGE, {})
        run.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())['error'], 'WSL_NOT_RUNNING')

    def test_bridge_enters_only_running_rayworker(self):
        run = Mock(side_effect=[Mock(returncode=0, stdout='RayWorker\r\n'.encode('utf-16')),
                                Mock(returncode=0, stdout=b'{"containers":{}}')])
        with patch.dict(sys.modules, {'subprocess': Mock(run=run)}), contextlib.redirect_stdout(io.StringIO()):
            exec(t.NODE20_BRIDGE, {})
        self.assertEqual(run.call_count, 2)
        self.assertIn('RayWorker', run.call_args.args[0])


class DecoratorRegressionTests(unittest.TestCase):
    def test_services_do_not_override_stale_heartbeat_or_lifecycle(self):
        tree = ast.parse((ROOT/'app/api.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_decorate_enrollment_device')
        module = ast.Module(body=[function], type_ignores=[])
        scope = {'Any': object, '__package__': 'isolated_device_test',
                 '_connectivity': lambda _: ('stale', 'heartbeat expired', 200),
                 '_ray_node_connected': lambda _: (True, 'Ray connected')}
        exec(compile(module, 'decorator', 'exec', flags=__future__.annotations.compiler_flag), scope)
        runtime = types.ModuleType('isolated_device_test.device_runtime')
        runtime.device_services = lambda _: {'tts': {'status': 'ready'}, 'h3': {'status': 'unknown'}}
        with patch.dict(sys.modules, {'isolated_device_test.device_runtime': runtime}):
            row = scope['_decorate_enrollment_device']({'ray_node_ip': '10.250.0.20',
                                                       'lifecycle_state': 'PAUSED', 'last_seen_at': None})
        self.assertEqual(row['heartbeat_status'], 'stale')
        self.assertEqual(row['connectivity_status'], 'stale')
        self.assertEqual(row['lifecycle_state'], 'PAUSED')
        self.assertEqual(row['services']['tts']['status'], 'ready')

    def test_ray_probe_rejects_missing_boolean(self):
        tree = ast.parse((ROOT/'app/api.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_ray_node_connected')
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.return_value.json.return_value = {'available': True}
        scope = {'settings': types.SimpleNamespace(ray_cluster_status_url='http://local'),
                 'httpx': types.SimpleNamespace(Client=Mock(return_value=client), HTTPError=OSError)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'probe', 'exec',
                     flags=__future__.annotations.compiler_flag), scope)
        self.assertIsNone(scope['_ray_node_connected']('10.250.0.20/32')[0])


if __name__ == '__main__':
    unittest.main()
