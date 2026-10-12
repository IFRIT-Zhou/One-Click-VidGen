import os
import json
import sys
import time
import tempfile
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.app.cloud_client import CloudClient, CloudConfig, CloudSessionStore, CloudAuthSession, CloudApiError
import module4_video_render as visual


def response(status, payload=None):
    result = Mock(status_code=status, headers={}, text='')
    result.json.return_value = payload or {}
    return result


class CloudImageAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'sessions.json')
        self.env = patch.dict(os.environ, {'CLOUD_SESSION_STORE_PATH': self.path})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = CloudSessionStore()
        self.store.set(7, CloudAuthSession('old-access', 'old-refresh', time.time() + 600))
        self.client = CloudClient(7, session_store=self.store,
                                  config=CloudConfig(base_url='https://cloud.test/api/v1', retry_count=0))

    def test_failed_refresh_is_terminal_and_clears_session(self):
        with patch('requests.request', side_effect=[response(401), response(401)]) as send:
            with self.assertRaises(CloudApiError) as err:
                self.client.account_summary()
            self.assertEqual(err.exception.code, 'CLOUD_LOGIN_REQUIRED')
            self.assertIsNone(self.store.get(7))
            with self.assertRaises(CloudApiError):
                self.client.account_summary()
            self.assertEqual(send.call_count, 2)

    def test_network_failure_during_refresh_keeps_session(self):
        self.store.set(7, CloudAuthSession('old', 'refresh', 0))
        with patch.object(self.client, '_json_request', side_effect=CloudApiError('offline', status_code=503)):
            with self.assertRaises(CloudApiError):
                self.client._ensure_access_token()
        self.assertEqual(self.store.get(7).refresh_token, 'refresh')

    def test_late_401_reuses_already_rotated_token(self):
        self.store.set(7, CloudAuthSession('new-access', 'new-refresh', time.time() + 600))
        with patch('requests.request') as send:
            current = self.client._refresh_access_token(force=True, failed_token='old-access')
        self.assertEqual(current.access_token, 'new-access')
        send.assert_not_called()

    def test_two_store_instances_preserve_users_and_observe_logout(self):
        second = CloudSessionStore()
        second.set(8, CloudAuthSession('other', 'other-refresh', 0))
        self.store.set(7, CloudAuthSession('new', 'new-refresh', 0))
        self.assertEqual(second.get(7).access_token, 'new')
        self.assertIsNotNone(self.store.get(8))
        second.clear(7)
        self.assertIsNone(self.store.get(7))

    def test_worker_auth_failure_does_not_retry_submit_or_sleep(self):
        config = {'cloud_pool': '1', 'cloud_user_id': '7', 'cloud_session_store_path': self.path,
                  'cloud_base_url': 'https://cloud.test/api/v1', 'api_key': 'old-access'}
        pool = Mock()
        pool.acquire.return_value = config
        def submit(macro, cfg):
            visual._request_with_cloud_refresh(Mock(), 'POST', 'https://cloud.test/api/v1/image-pool/generate',
                                               config=cfg, json={'clientJobId': 'unchanged'})
        with patch.object(visual, '_submit_poster', side_effect=submit) as submit_mock, \
             patch('requests.request', side_effect=[response(401), response(401)]), \
             patch.object(visual, '_hydrate_cloud_retry_context', side_effect=lambda x: x), \
             patch.object(visual.time, 'sleep') as sleep:
            with self.assertRaises(visual.CloudImageLoginRequired):
                visual._render_poster_with_retry({'macro_scene_id': 'poster_004'}, pool)
        self.assertEqual(submit_mock.call_count, 1)
        sleep.assert_not_called()
        pool.release.assert_called_once_with(config)

    def test_refresh_replays_identical_request_with_new_credentials(self):
        fresh = response(200, {'access_token': 'new', 'refresh_token': 'new-r', 'expires_in': 600})
        with patch('requests.request', side_effect=[response(401), fresh, response(200, {'id': 'img'})]) as send:
            result = self.client._json_request('POST', '/image-pool/generate', json={'clientJobId': 'stable'})
        calls = send.call_args_list
        self.assertEqual(calls[0].kwargs['json'], calls[2].kwargs['json'])
        self.assertEqual(calls[2].kwargs['headers']['Authorization'], 'Bearer new')
        self.assertEqual(result['id'], 'img')

    def test_cross_process_refresh_happens_once(self):
        self.store.set(7, CloudAuthSession('expired', 'refresh', 0))
        refreshes = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                refreshes.append(1)
                time.sleep(0.1)
                self.reply({'access_token': 'fresh', 'refresh_token': 'rotated', 'expires_in': 600})
            def do_GET(self):
                self.reply({'authorized': self.headers.get('Authorization') == 'Bearer fresh'})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            code = ("from backend.app.cloud_client import *; "
                    f"c=CloudClient(7,config=CloudConfig(base_url='http://127.0.0.1:{server.server_port}/api/v1')); "
                    "assert c.account_summary()['authorized']")
            children = [subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        for _ in range(2)]
            for child in children:
                out, err = child.communicate(timeout=25)
                self.assertEqual(child.returncode, 0, err.decode(errors='replace'))
            self.assertEqual(len(refreshes), 1)
            self.assertEqual(self.store.get(7).refresh_token, 'rotated')
        finally:
            server.shutdown()
            server.server_close()

    def test_batch_pauses_preserves_completed_and_skips_remaining(self):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from backend.app import video_studio as studio
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory)
            completed = {'id': 'done', 'image': 'keep.jpg', 'image_prompt': 'keep', 'image_status': 'completed'}
            (path / 'keep.jpg').write_bytes(b'keep')
            pending = [{'id': str(i), 'image_prompt': 'prompt', 'image_status': 'pending'} for i in range(3)]
            record = {'id': 'batch', 'revision': 1, 'shots': [completed, *pending], 'logs': []}
            stack.enter_context(patch.object(studio, 'threading', SimpleNamespace(
                Event=threading.Event, Thread=lambda target, **kw: SimpleNamespace(start=target))))
            stack.enter_context(patch.object(studio, 'save'))
            stack.enter_context(patch.object(studio, '_prepare_scene_assets'))
            stack.enter_context(patch.object(studio, '_bind_material_numbers', side_effect=lambda r,s,p:p))
            stack.enter_context(patch.object(studio, '_image_inputs', return_value=('prompt', [], None)))
            stack.enter_context(patch.object(studio, '_image_language_scope', side_effect=lambda *a: ExitStack()))
            stack.enter_context(patch.object(visual, 'shared_runninghub_account_pool', return_value=object()))
            stack.enter_context(patch.object(visual, '_positive_env_int', return_value=1))
            render = stack.enter_context(patch.object(visual, '_render_poster_with_retry',
                                                       side_effect=visual.CloudImageLoginRequired()))
            studio._start_storyboard_images(path, record, [{}])
            self.assertEqual(render.call_count, 1)
            self.assertTrue(record['cloud_login_required'])
            self.assertEqual(record['status'], 'image_review')
            self.assertTrue(all(s['image_status'] == 'pending' for s in pending))
            self.assertEqual((path / 'keep.jpg').read_bytes(), b'keep')
            self.assertEqual(completed['image_status'], 'completed')
            from PIL import Image
            def recovered(macro, _pool):
                target = Path(macro['_output_path'])
                Image.new('RGB', (16, 9)).save(target)
                return target
            render.reset_mock()
            render.side_effect = recovered
            studio._start_storyboard_images(path, record, [{}])
            self.assertEqual(render.call_count, 3)
            self.assertNotIn('cloud_login_required', record)
            self.assertTrue(all(s['image_status'] == 'completed' for s in record['shots']))
            self.assertEqual((path / 'keep.jpg').read_bytes(), b'keep')

    def test_single_image_resume_polls_existing_remote_task_without_resubmitting(self):
        import io
        from PIL import Image
        from backend.app import image_studio as studio
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            record = {'id': 'abc', 'prompt': 'same', 'references': ['ref.png'], 'ratio': '16:9',
                      'resolution': '1k', 'status': 'paused', 'remote_task_id': 'img-existing'}
            config = {'api_key': 'test', 'cloud_pool': '1', 'endpoint': 'https://cloud.test/generate',
                      'query_url': 'https://cloud.test/query'}
            buf = io.BytesIO()
            Image.new('RGB', (16, 9)).save(buf, format='PNG')
            query = response(200, {'status': 'SUCCESS', 'url': 'https://cloud.test/result'})
            download = response(200)
            download.content = buf.getvalue()
            with patch.object(visual, '_request_with_cloud_refresh', side_effect=[query, download]) as request:
                studio.execute(7, path, record, config)
            self.assertEqual(record['status'], 'completed')
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args_list[0].args[2], config['query_url'])
            self.assertEqual(request.call_args_list[0].kwargs['json'], {'taskId': 'img-existing'})
            self.assertTrue((path / 'result.png').is_file())

    def test_non_auth_http_status_is_preserved_for_provider_retry_rules(self):
        config = {'cloud_pool': '1', 'cloud_user_id': '7', 'cloud_session_store_path': self.path,
                  'cloud_base_url': 'https://cloud.test/api/v1', 'api_key': 'old'}
        upstream = response(429, {'code': 'QUEUE_FULL'})
        with patch('requests.request', return_value=upstream):
            result = visual._request_with_cloud_refresh(Mock(), 'POST',
                       'https://cloud.test/api/v1/image-pool/generate', config=config)
        self.assertIs(result, upstream)

    def test_subprocess_auth_error_reaches_parent_job(self):
        from backend.app.pipeline import Job, run_command
        job = Job(id='expired-login', step='visual')
        store = Mock()
        store.is_cancelled.return_value = False
        process = Mock()
        process.stdout = [str(visual.CloudImageLoginRequired()) + '\n']
        process.wait.return_value = 1
        with patch('backend.app.pipeline.subprocess.Popen', return_value=process), \
             patch('backend.app.subtitle_layout.presentation_env', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'CLOUD_LOGIN_REQUIRED'):
                run_command(job, store, ['python'], '模块 4 在线海报与页面生成')
