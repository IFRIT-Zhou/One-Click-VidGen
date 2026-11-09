"""Offline MCP template defaults; fake transport only, never contacts OCV or providers."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex_bridge'))
from ocv_mcp.adapter import Adapter
from ocv_mcp.contracts import bind

OPERATION = 'ocv_video_vibe_cloud_create'
ROUTE = '/api/video-studio/{identity}/shots/{shot_id}/vibe-cloud-jobs'


def cloud_schema(template_mode=True):
    properties = {
        'confirm_cloud': {'type': 'boolean', 'default': False},
        'max_image_requests': {'type': 'integer', 'minimum': 0, 'maximum': 12},
    }
    if template_mode:
        properties['template_mode'] = {
            'type': 'string', 'enum': ['semantic', 'shotcraft'], 'default': 'semantic'}
        properties['template_ids'] = {'type': 'array', 'maxItems': 3,
            'items': {'type': 'string', 'enum': ['card-stack', 'spotlight-hero-card']}}
    return {'paths': {ROUTE: {'post': {
        'parameters': [{'in': 'path', 'name': name, 'required': True,
                        'schema': {'type': 'string'}} for name in ('identity', 'shot_id')],
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'properties': properties}}}},
    }}}}


class FakeBackend:
    def __init__(self, schema):
        self.schema, self.calls = schema, []

    def close(self):
        pass

    def call(self, method, path, **kwargs):
        if path == '/api/session':
            return {'user': {'id': 1}, 'auth_mode': 'local'}
        if path == '/api/codex-bridge/info':
            return {'plugin_version': 'test'}
        if path == '/openapi.json':
            return self.schema
        if path.endswith('/pack'):
            return {'id': 'a' * 32, 'revision': 1, 'status': 'video_review', 'timeline_token': 'test'}
        self.calls.append((method, path, copy.deepcopy(kwargs)))
        return {'task_id': 'b' * 32, 'status': 'queued'}


class ShotcraftMcpContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = FakeBackend(cloud_schema())
        self.adapter = self.new_adapter()
        self.args = {'path': {'identity': 'a' * 32, 'shot_id': 's1'},
                     'body': {'confirm_cloud': True, 'max_image_requests': 0}}

    def tearDown(self):
        self.temp.cleanup()

    def new_adapter(self):
        return Adapter('http://127.0.0.1:8010', Path(self.temp.name) / 'state',
                       transport_factory=lambda: self.backend, allow_generation=True)

    def commit(self, plan, adapter=None):
        adapter = adapter or self.adapter
        adapter.commit(plan['plan_id'], confirmed=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            receipt = adapter.receipt(plan['plan_id'])
            if not receipt['adapter_request_running']:
                self.assertEqual(receipt['status'], 'submitted')
                return
            time.sleep(.01)
        self.fail('Mock submission did not finish')

    def test_mcp_default_is_shotcraft_without_changing_backend_default(self):
        document = cloud_schema()
        bound, _ = bind(document)
        mode = bound[OPERATION][1]['properties']['body']['properties']['template_mode']
        self.assertEqual(mode['default'], 'shotcraft')
        self.assertEqual(mode['enum'], ['semantic', 'shotcraft'])
        backend_mode = document['paths'][ROUTE]['post']['requestBody']['content']['application/json']['schema']['properties']['template_mode']
        self.assertEqual(backend_mode['default'], 'semantic')
        self.assertIn('已验证镜头模板', mode['description'])

    def test_new_preparation_freezes_default_for_confirmed_backend_submission(self):
        original = copy.deepcopy(self.args)
        plan = self.adapter.prepare(OPERATION, self.args, 'new_default')
        self.assertEqual(self.args, original, 'caller arguments must not be mutated')
        self.assertEqual(plan['arguments']['body']['template_mode'], 'shotcraft')
        self.assertTrue(plan['requires_confirmation'])
        self.assertEqual(self.backend.calls, [])
        repeated = self.adapter.prepare(OPERATION, self.args, 'new_default')
        self.assertEqual(repeated['plan_id'], plan['plan_id'])
        with self.assertRaisesRegex(ValueError, '明确授权'):
            self.adapter.commit(plan['plan_id'])
        self.commit(plan)
        self.adapter.commit(plan['plan_id'], confirmed=True)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.backend.calls[0][2]['json'], {**self.args['body'], 'template_mode': 'shotcraft'})

    def test_explicit_semantic_remains_compatible(self):
        self.args['body']['template_mode'] = 'semantic'
        plan = self.adapter.prepare(OPERATION, self.args, 'explicit_semantic')
        self.commit(plan)
        self.assertEqual(self.backend.calls[0][2]['json']['template_mode'], 'semantic')

    def test_manual_templates_follow_live_schema_and_are_committed_unchanged(self):
        self.args['body']['template_ids'] = ['card-stack', 'spotlight-hero-card']
        plan = self.adapter.prepare(OPERATION, self.args, 'manual_templates')
        self.assertEqual(plan['arguments']['body']['template_ids'], self.args['body']['template_ids'])
        self.commit(plan)
        self.assertEqual(self.backend.calls[0][2]['json']['template_ids'], self.args['body']['template_ids'])
        self.assertEqual(self.backend.calls[0][2]['json']['template_mode'], 'shotcraft')

    def test_invalid_mode_does_not_prepare_or_submit(self):
        for mode in ('custom-code', '', None, 1):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.adapter.prepare(OPERATION, {**self.args, 'body': {
                    **self.args['body'], 'template_mode': mode}}, 'invalid_mode')
        self.assertEqual(self.backend.calls, [])

    def test_old_contract_does_not_receive_unsupported_parameter(self):
        self.backend.schema = cloud_schema(template_mode=False)
        plan = self.adapter.prepare(OPERATION, self.args, 'old_contract')
        self.assertNotIn('template_mode', plan['arguments']['body'])
        self.commit(plan)
        self.assertEqual(self.backend.calls[0][2]['json'], self.args['body'])

    def test_saved_legacy_plan_is_not_rewritten_during_commit(self):
        self.backend.schema = cloud_schema(template_mode=False)
        plan = self.adapter.prepare(OPERATION, self.args, 'saved_legacy')
        self.backend.schema = cloud_schema()
        upgraded = self.new_adapter()
        self.commit(plan, upgraded)
        self.assertEqual(self.backend.calls[0][2]['json'], self.args['body'])
        client, owner = upgraded.connect()
        client.close()
        stored = upgraded.state.get(owner, plan['plan_id'])
        self.assertNotIn('template_mode', json.loads(stored['arguments'])['body'])


if __name__ == '__main__':
    unittest.main()
