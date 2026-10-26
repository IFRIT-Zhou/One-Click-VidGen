"""No production project changes or paid calls. Run with runtime/ocv_mcp Python."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex_bridge'))
from ocv_mcp.adapter import Adapter, HttpSession, ApiError, clean
from ocv_mcp.catalog import OPERATIONS
from ocv_mcp.contracts import bind


def application_schema():
    # Import routes only; do not run lifespan/startup, read a live project, or
    # create jobs. Use the existing OCV interpreter for its application deps.
    result = subprocess.run([str(ROOT / 'runtime/python/python.exe'), '-c',
        'import json; from backend.app.main import app; print(json.dumps(app.openapi()))'],
        cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'}, capture_output=True, text=True,
        encoding='utf-8', check=True, timeout=60)
    return json.loads(result.stdout)


class FakeBackend:
    def __init__(self, schema):
        self.schema, self.user, self.revision = schema, 1, 3
        self.calls, self.error = [], None
        self.wait = None
        self.enabled = True

    def close(self):
        pass

    def call(self, method, path, **kwargs):
        if path == '/api/session':
            return {'user': {'id': self.user} if self.user else None, 'auth_mode': 'local'}
        if path == '/api/codex-bridge/info':
            if not self.enabled:
                raise ApiError(404, 'disabled')
            return {'plugin_version': '1.6.0'}
        if path == '/openapi.json':
            return self.schema
        if path.endswith('/pack'):
            return {'id': 'a'*32, 'revision': self.revision, 'status': 'video_review', 'timeline_token': 't'*64}
        if method == 'GET' and path == '/api/jobs/job1':
            return {'id': 'job1', 'status': 'completed', 'updated_at': self.revision, 'step': 'render'}
        self.calls.append((method, path, kwargs))
        if self.wait:
            self.wait.wait(4)
        if self.error:
            raise self.error
        return {'ok': True, 'job_id': 'job1', 'status': 'running', 'api_key': 'do-not-return'}


class McpAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = application_schema()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.backend = FakeBackend(self.schema)
        self.adapter = self.new_adapter()

    def tearDown(self):
        if self.backend.wait:
            self.backend.wait.set()
        deadline = time.time() + 5
        while self.adapter.active and time.time() < deadline:
            time.sleep(.01)
        self.temp.cleanup()

    def new_adapter(self, **kwargs):
        return Adapter('http://127.0.0.1:8010', self.folder / 'state',
                       transport_factory=lambda: self.backend, allow_generation=True, **kwargs)

    def prep(self, operation='ocv_video_export', arguments=None, request_id='request_123'):
        args = arguments or {'path': {'identity': 'a'*32}, 'body': {'revision': 3}}
        return self.adapter.prepare(operation, args, request_id)

    def completed(self, plan):
        deadline = time.time() + 5
        while time.time() < deadline:
            receipt = self.adapter.receipt(plan)
            if receipt['status'] != 'submitting':
                return receipt
            time.sleep(.01)
        self.fail('adapter did not finish')

    def test_every_allowlisted_operation_exists_in_current_application(self):
        bound, unavailable = bind(self.schema)
        self.assertEqual(unavailable, [])
        self.assertEqual(len(bound), len(OPERATIONS))
        self.assertGreaterEqual(len(bound), 150)

    def test_unique_names_and_no_unsafe_global_routes(self):
        self.assertEqual(len({o.name for o in OPERATIONS}), len(OPERATIONS))
        for op in OPERATIONS:
            self.assertNotIn('/auth/', op.path)
            self.assertNotIn('install', op.path)
            self.assertNotIn('open-folder', op.path)
            if op.path == '/api/settings':
                self.assertEqual((op.name, op.method, op.access), ('ocv_creation_options', 'GET', 'read'))

    def test_older_server_omits_missing_route(self):
        bound, missing = bind({'paths': {}})
        self.assertEqual(bound, {})
        self.assertEqual(len(missing), len(OPERATIONS))

    def test_describe_removes_credentials(self):
        schema = self.adapter.describe('ocv_job_create')['input_schema']
        self.assertNotIn('api_key', schema['properties']['body']['properties'])
        for key in ('image_api_key', 'image_api_keys', 'qwen_tts_api_key'):
            self.assertNotIn(key, schema['properties']['body']['properties'])

    def test_only_local_origins(self):
        for address in ('http://example.com', 'http://localhost@evil.com', 'http://127.0.0.1/path',
                        'http://localhost?key=x', 'file:///foo', 'http://127.0.0.1#x'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                HttpSession(address)

    def test_prepare_does_not_submit(self):
        plan = self.prep()
        self.assertEqual(plan['status'], 'prepared')
        self.assertFalse(self.backend.calls)

    def test_confirmation_required(self):
        plan = self.prep()
        with self.assertRaisesRegex(ValueError, '明确授权'):
            self.adapter.commit(plan['plan_id'])
        self.assertFalse(self.backend.calls)

    def test_generation_permission_disabled(self):
        adapter = Adapter('http://127.0.0.1:8010', self.folder/'denied', transport_factory=lambda:self.backend)
        with self.assertRaisesRegex(ValueError, '未获'):
            adapter.prepare('ocv_job_create', {'body': {'script': '测试'}}, 'request_1')

    def test_readonly_permission(self):
        adapter = self.new_adapter(read_only=True)
        with self.assertRaisesRegex(ValueError, '未获'):
            adapter.prepare('ocv_video_images_confirm', {'path': {'identity':'a'*32},'body':{'revision':3}}, 'request_1')

    def test_delete_disabled(self):
        with self.assertRaisesRegex(ValueError, '未获'):
            self.prep('ocv_job_delete', {'path': {'job_id': 'job1'}})

    def test_repeat_prepare_and_commit_are_idempotent(self):
        first = self.prep()
        self.assertEqual(self.prep()['plan_id'], first['plan_id'])
        self.backend.wait = threading.Event()
        self.adapter.commit(first['plan_id'], True)
        self.adapter.commit(first['plan_id'], True)
        self.backend.wait.set()
        result = self.completed(first['plan_id'])
        self.adapter.commit(first['plan_id'], True)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['result']['data']['api_key'], '[REDACTED]')

    def test_parallel_commits_single_dispatch(self):
        from concurrent.futures import ThreadPoolExecutor
        plan = self.prep()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.adapter.commit, plan['plan_id'], True) for _ in range(8)]
            for future in futures:
                future.result()
        self.completed(plan['plan_id'])
        self.assertEqual(len(self.backend.calls), 1)

    def test_changed_request_id_payload_conflicts(self):
        self.prep()
        with self.assertRaisesRegex(ValueError, '另一组参数'):
            self.prep(arguments={'path': {'identity': 'a'*32}, 'body': {'revision': 4}})

    def test_stale_project_rejected_before_dispatch(self):
        plan = self.prep()
        self.backend.revision += 1
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.adapter.commit(plan['plan_id'], True)
        self.assertFalse(self.backend.calls)

    def test_repeated_prepare_preserves_original_guard(self):
        original = self.prep()
        self.backend.revision += 1
        repeated = self.prep()
        self.assertEqual(repeated['plan_id'], original['plan_id'])
        self.assertEqual(repeated['guard'], original['guard'])
        self.assertTrue(repeated['state_changed_since_prepare'])

    def test_authentication_and_plugin_gate(self):
        self.backend.user = None
        with self.assertRaisesRegex(ValueError, '登录'):
            self.adapter.status()
        self.backend.user, self.backend.enabled = 1, False
        with self.assertRaises(ApiError):
            self.adapter.status()

    def test_other_user_cannot_commit_or_read_receipt(self):
        plan = self.prep()
        self.backend.user = 2
        with self.assertRaisesRegex(ValueError, '当前用户'):
            self.adapter.commit(plan['plan_id'], True)
        with self.assertRaises(ValueError):
            self.adapter.receipt(plan['plan_id'])

    def test_restart_recovers_completed_receipt(self):
        plan = self.prep()
        self.adapter.commit(plan['plan_id'], True)
        self.completed(plan['plan_id'])
        other = self.new_adapter()
        self.assertEqual(other.commit(plan['plan_id'], True)['status'], 'submitted')
        self.assertEqual(len(self.backend.calls), 1)

    def test_unknown_never_resubmits(self):
        self.backend.error = TimeoutError('connection interrupted')
        plan = self.prep()
        self.adapter.commit(plan['plan_id'], True)
        self.assertEqual(self.completed(plan['plan_id'])['status'], 'unknown')
        self.new_adapter().commit(plan['plan_id'], True)
        self.assertEqual(len(self.backend.calls), 1)

    def test_server_5xx_is_ambiguous_not_safe_retry(self):
        self.backend.error = ApiError(500, 'failed after save')
        plan = self.prep()
        self.adapter.commit(plan['plan_id'], True)
        self.assertEqual(self.completed(plan['plan_id'])['status'], 'unknown')

    def test_native_validation_preserved(self):
        self.backend.error = ApiError(409, '第 05 镜需拆分')
        plan = self.prep()
        self.adapter.commit(plan['plan_id'], True)
        result = self.completed(plan['plan_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('第 05 镜', result['result']['error'])

    def test_unconfirmed_submission_survives_restart(self):
        plan = self.prep()
        owner = self.adapter.authenticate(self.backend)
        self.adapter.state.claim(owner, plan['plan_id'])
        result = self.new_adapter().commit(plan['plan_id'], True)
        self.assertEqual(result['status'], 'submitting')
        self.assertIn('不会自动重发', result['warning'])
        self.assertFalse(self.backend.calls)

    def test_expired_preview(self):
        plan = self.prep()
        with patch('ocv_mcp.adapter.time.time', return_value=time.time()+601), self.assertRaisesRegex(ValueError, '过期'):
            self.adapter.commit(plan['plan_id'], True)

    def test_discard_only_prepared(self):
        plan = self.prep()
        self.assertTrue(self.adapter.discard(plan['plan_id'])['discarded'])
        self.assertEqual(self.adapter.commit(plan['plan_id'], True)['status'], 'discarded')
        self.assertFalse(self.backend.calls)

    def test_write_cannot_use_read(self):
        with self.assertRaisesRegex(ValueError, '写操作'):
            self.adapter.read('ocv_video_export', {'path': {'identity': 'a'*32}, 'body': {'revision': 3}})

    def test_extra_fields_and_path_traversal_rejected(self):
        for args in ({'path': {'job_id': '../other'}}, {'path': {'job_id': '%2fother'}},
                     {'path': {'job_id': 'job1'}, 'unexpected': True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.adapter.read('ocv_job_get', args)

    def test_required_native_revision_preserved(self):
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.prep(arguments={'path': {'identity': 'a'*32}, 'body': {}})

    def test_subtitle_only_requires_expected_revision(self):
        with self.assertRaisesRegex(ValueError, 'expected_revision'):
            self.prep('ocv_tts_subtitle_only', {'path': {'job_id':'job1'}, 'body':{'updates':{'1':'注释'}}})

    def test_resegment_forces_copy_sync(self):
        with self.assertRaises(ValueError):
            self.prep('ocv_tts_resegment', {'path':{'job_id':'job1'},
                'body':{'start_index':1,'replace_count':1,'parts':[{'text':'新文字'}], 'sync_text':False}})

    def test_upload_requires_explicit_root(self):
        image = self.folder/'test.png'
        image.write_bytes(b'fake')
        with self.assertRaisesRegex(ValueError, '授权'):
            self.prep('ocv_upload', {'local_path':str(image)})

    def test_upload_changed_after_preview(self):
        self.adapter = self.new_adapter(upload_roots=[self.folder])
        image = self.folder/'test.png'
        image.write_bytes(b'old')
        plan = self.prep('ocv_upload', {'local_path':str(image)})
        image.write_bytes(b'new')
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.adapter.commit(plan['plan_id'])
        self.assertFalse(self.backend.calls)

    def test_upload_no_scripts_or_credentials(self):
        self.adapter = self.new_adapter(upload_roots=[self.folder])
        script = self.folder/'test.py'
        script.write_text('')
        with self.assertRaises(ValueError):
            self.prep('ocv_upload', {'local_path':str(script)})
        with self.assertRaisesRegex(ValueError, '凭据'):
            self.prep('ocv_job_create', {'body':{'script':'text','api_key':'secret'}})

    def test_upload_sends_frozen_bytes_and_native_form_revision(self):
        self.adapter = self.new_adapter(upload_roots=[self.folder])
        file = self.folder/'image.png'
        file.write_bytes(b'picture')
        plan = self.prep('ocv_video_image_replace', {'path':{'identity':'a'*32,'shot_id':'s1'},
                            'form':{'revision':3,'prompt':'新版'},'local_path':str(file)})
        self.adapter.commit(plan['plan_id'])
        self.completed(plan['plan_id'])
        _, route, data = self.backend.calls[-1]
        self.assertEqual(route, '/api/video-studio/'+'a'*32+'/images/s1/upload')
        self.assertEqual(data['files']['file'][1], b'picture')
        self.assertEqual(data['data']['revision'], '3')

    def test_narration_edit_semantics_are_preserved(self):
        cases = [
            ('ocv_tts_regenerate', {'indices':[1], 'tts_text_overrides':{'1':'读音修正'}}),
            ('ocv_tts_subtitle_only', {'updates':{'1':'仅显示（注释）'},'expected_revision':3}),
            ('ocv_tts_rebuild', {'pauses':{'1':.6}, 'expected_revision':3}),
            ('ocv_tts_resegment', {'start_index':1,'replace_count':1,'parts':[{'text':'新文案'}],'sync_text':True}),
        ]
        for i, (op, body) in enumerate(cases):
            plan = self.prep(op, {'path':{'job_id':'job1'},'body':body}, 'narration_'+str(i))
            self.adapter.commit(plan['plan_id'], True)
            self.completed(plan['plan_id'])
            self.assertEqual(self.backend.calls[-1][2]['json'], body)
        self.assertNotIn('subtitle_text_overrides', self.backend.calls[0][2]['json'])
        self.assertTrue(self.backend.calls[2][1].endswith('/rebuild-audio'))

    def test_dynamic_split_and_illustrated_render_passthrough(self):
        for op, args in [
            ('ocv_video_auto_split', {'path':{'identity':'a'*32,'shot_id':'s1'},
                                     'body':{'revision':3,'include_neighbors':True}}),
            ('ocv_visual_render', {'path':{'job_id':'job1'},'body':{'mode':'both','bgm_enabled':False}}),
        ]:
            plan=self.prep(op,args,op+'_request')
            self.adapter.commit(plan['plan_id'],True)
            self.assertEqual(self.completed(plan['plan_id'])['status'],'submitted')
            self.assertEqual(self.backend.calls[-1][2]['json'],args['body'])

    def test_asset_scope_and_path_guard(self):
        owner=self.adapter.authenticate(self.backend)
        folder=self.adapter.state.folder/'assets'/owner
        folder.mkdir(parents=True)
        path=folder/'image.png'
        path.write_bytes(b'image')
        receipt=self.adapter.state.add_asset(owner,path,'image/png')
        self.assertEqual(self.adapter.asset(receipt['asset_id'])[0],path)
        self.backend.user=2
        with self.assertRaises(ValueError):
            self.adapter.asset(receipt['asset_id'])
        self.backend.user=1
        outside=self.folder/'other.png'
        outside.write_bytes(b'private')
        corrupt=self.adapter.state.add_asset(owner,outside,'image/png')
        with self.assertRaises(ValueError):
            self.adapter.asset(corrupt['asset_id'])

    def test_oversized_item_can_be_read_by_field(self):
        owner=self.adapter.authenticate(self.backend)
        packed=self.adapter.compact(owner, {'shots':[{'id':'s1','prompt':'x'*20000}]})
        page=self.adapter.result(packed['result_id'],'/shots')
        self.assertEqual(page['oversized_item_pointer'],'/shots/0')
        part=self.adapter.result(packed['result_id'],'/shots/0/prompt')
        self.assertEqual(len(part['text']),18000)

    def test_large_results_paged_and_user_scoped(self):
        self.adapter.status()
        owner = self.adapter.authenticate(self.backend)
        result = self.adapter.compact(owner, {'shots':[{'id':str(i),'text':'x'*1000} for i in range(40)]})
        page = self.adapter.result(result['result_id'], '/shots', 10, 3)
        self.assertEqual(len(page['items']), 3)
        self.assertEqual(page['items'][0]['id'], '10')
        self.backend.user=2
        with self.assertRaises(ValueError):
            self.adapter.result(result['result_id'])

    def test_secret_redaction_keeps_concurrency_tokens(self):
        result = clean({'api_key':'secret', 'timeline_token':'revision-hash',
                        'confirmation_token':'confirm-hash', 'nested':{'password':'secret'},
                        'logs':['Authorization=Bearer abc.def', 'sk-abcdefghijklmnop']})
        self.assertEqual(result['timeline_token'], 'revision-hash')
        self.assertEqual(result['confirmation_token'], 'confirm-hash')
        self.assertNotIn('abc.def', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))

    def test_prefixed_provider_credentials_are_blocked_and_redacted(self):
        for key in ('image_api_key', 'image_api_keys', 'qwen_tts_api_key', 'provider_access_token'):
            with self.subTest(key=key):
                self.assertEqual(clean({key: 'private-value'})[key], '[REDACTED]')
                with self.assertRaisesRegex(ValueError, '凭据'):
                    self.prep('ocv_job_create', {'body': {'script': '文本', key: 'private-value'}})

    def test_illustrated_and_dynamic_operations_share_native_routes(self):
        self.adapter.read('ocv_tts_get', {'path':{'job_id':'job1'}})
        self.adapter.read('ocv_visual_get', {'path':{'job_id':'job1'}})
        self.adapter.read('ocv_video_get', {'path':{'identity':'a'*32}})
        self.assertEqual([c[1] for c in self.backend.calls],
                         ['/api/jobs/job1/tts-editor','/api/jobs/job1/visual-editor','/api/video-studio/'+'a'*32])


class StdioProtocolTests(unittest.TestCase):
    def test_official_sdk_roundtrip_with_fake_ocv(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        schema = application_schema()
        backend = FakeBackend(schema)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.respond('GET')
            def do_POST(self): self.respond('POST')
            def respond(self, method):
                size = int(self.headers.get('Content-Length', '0'))
                payload = json.loads(self.rfile.read(size)) if size else None
                data = json.dumps(backend.call(method, self.path, json=payload)).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        http = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                async def run():
                    params = StdioServerParameters(command=sys.executable, args=[
                        str(ROOT/'plugins/codex_bridge/mcp_server.py'), '--base-url',
                        f'http://127.0.0.1:{http.server_port}', '--state-dir', temp, '--allow-generation'])
                    async with stdio_client(params) as (reader, writer):
                        async with ClientSession(reader,writer) as session:
                            await session.initialize()
                            self.assertEqual(len((await session.list_tools()).tools),10)
                            self.assertFalse((await session.call_tool('ocv_connection',{})).isError)
                            self.assertFalse((await session.read_resource('ocv://guide/workflow')).contents == [])
                            described=await session.call_tool('ocv_describe',{'operation':'ocv_video_export'})
                            self.assertIn('input_schema',described.structuredContent)
                            plan = await session.call_tool('ocv_prepare',{'operation':'ocv_video_export',
                                'arguments':{'path':{'identity':'a'*32},'body':{'revision':3}},'request_id':'stdio_test_1'})
                            ident = plan.structuredContent['plan_id']
                            await session.call_tool('ocv_commit',{'plan_id':ident,'confirmed':True})
                            for _ in range(100):
                                r=await session.call_tool('ocv_receipt',{'plan_id':ident})
                                if r.structuredContent['status'] != 'submitting': break
                                await asyncio.sleep(.02)
                            self.assertEqual(r.structuredContent['status'],'submitted')
                            await session.call_tool('ocv_commit',{'plan_id':ident,'confirmed':True})
                            self.assertEqual(len(backend.calls),1)
                asyncio.run(run())
        finally:
            http.shutdown()
            http.server_close()


class HttpBoundaryTests(unittest.TestCase):
    def client(self, response):
        import httpx
        client=HttpSession('http://127.0.0.1:8010')
        client.client.close()
        client.client=httpx.Client(base_url=client.base_url, follow_redirects=False,
                                  transport=httpx.MockTransport(lambda request: response))
        self.addCleanup(client.close)
        return client

    def test_redirect_not_followed(self):
        import httpx
        client=self.client(httpx.Response(302,headers={'Location':'https://attacker.invalid'},json={'detail':'redirect'}))
        with self.assertRaises(ApiError) as caught:
            client.call('GET','/api/jobs')
        self.assertEqual(caught.exception.status,302)

    def test_media_download_and_json_preview(self):
        import httpx
        with tempfile.TemporaryDirectory() as temp:
            client=self.client(httpx.Response(200,headers={'content-type':'image/png'},content=b'image'))
            file,mime=client.download('GET','/api/media',Path(temp))
            self.assertEqual(file.read_bytes(),b'image')
            self.assertEqual(mime,'image/png')
            client=self.client(httpx.Response(200,json={'preview':'base64-data'}))
            file,payload=client.download('POST','/api/preview',Path(temp),json={})
            self.assertIsNone(file)
            self.assertEqual(payload['preview'],'base64-data')

    def test_download_limit_removes_incomplete_file(self):
        import httpx
        client=self.client(httpx.Response(200,headers={'content-type':'video/mp4'},content=b'123456'))
        with tempfile.TemporaryDirectory() as temp, patch('ocv_mcp.adapter.MAX_BYTES',3):
            with self.assertRaises(ValueError):
                client.download('GET','/api/video',Path(temp))
            self.assertEqual(list(Path(temp).iterdir()),[])


if __name__ == '__main__':
    unittest.main()
