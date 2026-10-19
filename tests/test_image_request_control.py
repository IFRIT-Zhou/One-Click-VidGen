import base64
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import module4_video_render as visual
from backend.app import image_request_control as control


class ImageRequestControlTests(unittest.TestCase):
    def test_stop_does_not_wait_for_socket_and_discards_late_response(self):
        cancelled, release, closed = threading.Event(), threading.Event(), threading.Event()
        response = Mock(close=Mock(side_effect=closed.set))
        def blocked():
            cancelled.set()
            release.wait(3)
            return response
        started = time.monotonic()
        try:
            with control.scope(cancel_event=cancelled), self.assertRaises(control.ImageWaitStopped):
                control.request(blocked)
            self.assertLess(time.monotonic() - started, 0.8)
        finally:
            release.set()
        self.assertTrue(closed.wait(2))
        response.close.assert_called_once()

    def test_default_deadline_is_five_minutes_and_invalid_env_is_safe(self):
        for value in ('', 'nan', '-1', 'inf'):
            with patch.dict(os.environ, {'OCV_IMAGE_TASK_TIMEOUT_SECONDS': value}), control.scope():
                self.assertEqual(control._CURRENT.get().timeout, 300)

    def test_deadline_includes_blocking_http_and_is_not_reset_by_nested_scope(self):
        release = threading.Event()
        response = Mock()
        try:
            with control.scope(timeout=0.05, label='第 3 镜'):
                original = control._CURRENT.get()
                with control.scope(timeout=600):
                    self.assertIs(control._CURRENT.get(), original)
                    with self.assertRaisesRegex(control.ImageWaitTimeout, '第 3 镜.*停止自动重试'):
                        control.request(lambda: (release.wait(2), response)[1])
        finally:
            release.set()

    def test_retry_backoff_obeys_total_deadline_and_releases_capacity(self):
        pool = visual.RunningHubAccountPool([{'api_key': 'test'}], per_key_concurrency=1)
        with patch.object(visual, '_hydrate_cloud_retry_context', side_effect=lambda m: m), \
             patch.object(visual, '_submit_poster', side_effect=visual.RunningHubTransientError('busy')) as submit, \
             patch.object(visual, '_retry_delay_seconds', return_value=60), \
             patch.dict(os.environ, {'OCV_IMAGE_TASK_TIMEOUT_SECONDS': '0.05'}):
            with self.assertRaises(control.ImageWaitTimeout):
                visual._render_poster_with_retry({'macro_scene_id': 'shot'}, pool)
        submit.assert_called_once()
        self.assertEqual(pool._active_leases, {})
        lease = pool.acquire()
        pool.release(lease)

    def test_stop_during_polling_releases_capacity_without_resubmission(self):
        cancelled = threading.Event()
        pool = visual.RunningHubAccountPool([{'api_key': 'test'}], per_key_concurrency=1)
        def polling(*_):
            cancelled.set()
            control.wait(60)
        with patch.object(visual, '_hydrate_cloud_retry_context', side_effect=lambda m: m), \
             patch.object(visual, '_submit_poster') as submit, \
             patch.object(visual, '_wait_for_poster', side_effect=polling):
            with self.assertRaises(control.ImageWaitStopped):
                visual._render_poster_with_retry({'macro_scene_id': 'shot', '_cancel_event': cancelled}, pool)
        submit.assert_called_once()
        self.assertEqual(pool._active_leases, {})

    def test_stopped_capacity_wait_does_not_take_another_lease(self):
        pool = visual.RunningHubAccountPool([{'api_key': 'test'}], per_key_concurrency=1)
        first = pool.acquire()
        cancelled = threading.Event()
        cancelled.set()
        for acquire in (pool.acquire, pool.acquire_waiting_account):
            with control.scope(cancel_event=cancelled), self.assertRaises(control.ImageWaitStopped):
                acquire()
        self.assertEqual(len(pool._active_leases), 1)
        pool.release(first)

    def test_queue_lock_wait_can_be_stopped(self):
        lock, cancelled = threading.Lock(), threading.Event()
        lock.acquire()
        cancelled.set()
        try:
            with control.scope(cancel_event=cancelled), self.assertRaises(control.ImageWaitStopped):
                with control.locked(lock):
                    self.fail('must not enter')
        finally:
            lock.release()

    def test_stopped_upload_is_not_converted_into_base64_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'ref.png'
            Image.new('RGB', (4, 4)).save(source)
            config = {'api_key': 'test', 'cloud_pool': '1', 'method': 'ican'}
            with patch.object(visual, '_request_with_cloud_refresh', side_effect=control.ImageWaitStopped()):
                with self.assertRaises(control.ImageWaitStopped):
                    visual._reference_image_url(config, str(source))

    def test_cloud_request_honors_image_transport_timeout(self):
        from backend.app.cloud_client import CloudClient, CloudConfig
        client = CloudClient(1, config=CloudConfig(base_url='https://example.invalid/api/v1'), session_store=Mock())
        response = Mock(status_code=200)
        with patch.object(client, '_ensure_access_token', return_value=SimpleNamespace(access_token='test')), \
             patch('backend.app.cloud_client.cloud_client_for_image', return_value=client), \
             patch('backend.app.cloud_client.requests.request', return_value=response) as request:
            with control.scope():
                visual._request_with_cloud_refresh(Mock(), 'POST', '/images',
                    config={'cloud_pool': '1', 'cloud_user_id': '1'}, timeout=37)
        self.assertEqual(request.call_args.kwargs['timeout'], 37)

    def test_stop_during_auth_does_not_submit_after_auth_returns(self):
        from backend.app.cloud_client import CloudClient, CloudConfig
        client = CloudClient(1, config=CloudConfig(base_url='https://example.invalid/api/v1'), session_store=Mock())
        cancelled, release, finished = threading.Event(), threading.Event(), threading.Event()
        def auth():
            cancelled.set()
            release.wait(3)
            return SimpleNamespace(access_token='test')
        def operation():
            try:
                return client._send('POST', '/images')
            finally:
                finished.set()
        with patch.object(client, '_ensure_access_token', side_effect=auth), \
             patch('backend.app.cloud_client.requests.request') as request:
            try:
                with control.scope(cancel_event=cancelled), self.assertRaises(control.ImageWaitStopped):
                    control.request(operation)
            finally:
                release.set()
            self.assertTrue(finished.wait(2))
            request.assert_not_called()

    def test_cancelled_download_never_overwrites_existing_image(self):
        cancelled = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'image.jpg'
            output.write_bytes(b'keep')
            def response(*a, **kw):
                cancelled.set()
                return Mock(content=b'late')
            with patch.object(visual, '_request_with_cloud_refresh', side_effect=response):
                with control.scope(cancel_event=cancelled), self.assertRaises(control.ImageWaitStopped):
                    visual._download_image(Mock(), 'shot', 'https://example.invalid/image', output)
            self.assertEqual(output.read_bytes(), b'keep')

    def test_cancelled_sync_response_never_writes_an_image(self):
        from backend.app.openai_image_adapter import render
        cancelled = threading.Event()
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4)).save(buffer, 'PNG')
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}]}
        def post(*a, **kw):
            cancelled.set()
            return response
        session = Mock(post=Mock(side_effect=post))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'image.jpg'
            with control.scope(cancel_event=cancelled), self.assertRaises(control.ImageWaitStopped):
                render({'image_prompt': 'test'}, {'model': 'image', 'api_key': 'test',
                       'endpoint': 'https://example.invalid/images'}, session, output)
            self.assertFalse(output.exists())
            session.post.assert_called_once()

    def test_image_studio_timeout_fails_without_resubmitting(self):
        from backend.app import image_studio as studio
        record = {'id': 'test', 'prompt': 'test', 'ratio': '16:9', 'resolution': '1k',
                  'references': [], 'remote_task_id': 'original'}
        config = {'api_key': 'test', 'endpoint': 'https://example.invalid/images'}
        with patch.object(visual, '_request_with_cloud_refresh', side_effect=control.ImageWaitTimeout('等待超时')) as request, \
             patch.object(studio, 'save'):
            studio.execute(1, Path('unused-test-path'), record, config)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['remote_task_id'], 'original')
        request.assert_called_once()

    def _batch(self, failure):
        from backend.app import video_studio as studio
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory)
            old = {'id': 'done', 'image': 'keep.jpg', 'image_prompt': 'keep', 'image_status': 'completed'}
            (path / 'keep.jpg').write_bytes(b'keep')
            pending = [{'id': str(i), 'image_prompt': 'test', 'image_status': 'pending'} for i in range(3)]
            record = {'id': 'batch', 'revision': 1, 'shots': [old, *pending], 'logs': []}
            stack.enter_context(patch.object(studio, 'threading', SimpleNamespace(
                Event=threading.Event, Thread=lambda target, **kw: SimpleNamespace(start=target))))
            stack.enter_context(patch.object(studio, 'save'))
            stack.enter_context(patch.object(studio, '_prepare_scene_assets'))
            stack.enter_context(patch.object(studio, '_bind_material_numbers', side_effect=lambda r,s,p:p))
            stack.enter_context(patch.object(studio, '_image_inputs', return_value=('test', [], None)))
            stack.enter_context(patch.object(studio, '_image_language_scope', side_effect=lambda *a: ExitStack()))
            stack.enter_context(patch.object(visual, 'shared_runninghub_account_pool', return_value=object()))
            stack.enter_context(patch.object(visual, '_poster_worker_count', return_value=1))
            def fail(macro, pool):
                if isinstance(failure, control.ImageWaitStopped):
                    macro['_cancel_event'].set()
                raise failure
            render = stack.enter_context(patch.object(visual, '_render_poster_with_retry', side_effect=fail))
            studio._start_storyboard_images(path, record, [{}])
            render.assert_called_once()
            self.assertEqual(record['status'], 'image_review')
            self.assertNotIn(str(path), studio.ACTIVE)
            self.assertNotIn(str(path), studio.CANCEL_EVENTS)
            self.assertEqual(record['error'], str(failure))
            self.assertEqual(pending[0]['image_status'], 'failed' if isinstance(failure, control.ImageWaitTimeout) else 'pending')
            self.assertTrue(all(s['image_status'] == 'pending' for s in pending[1:]))
            self.assertEqual((path / 'keep.jpg').read_bytes(), b'keep')
            # A user retry (possibly another channel) can finish only missing images.
            def succeed(macro, pool):
                target = Path(macro['_output_path'])
                Image.new('RGB', (4, 4)).save(target)
                return target
            render.reset_mock()
            render.side_effect = succeed
            studio._start_storyboard_images(path, record, [{}])
            self.assertEqual(render.call_count, 3)
            self.assertNotIn('error', record)
            self.assertTrue(all(s['image_status'] == 'completed' for s in record['shots']))
            self.assertEqual((path / 'keep.jpg').read_bytes(), b'keep')

    def test_batch_timeout_unlocks_preserves_completed_and_allows_retry(self):
        self._batch(control.ImageWaitTimeout('第 2 镜超时；请切换号池'))

    def test_batch_stop_unlocks_preserves_completed_and_allows_retry(self):
        self._batch(control.ImageWaitStopped())

    def test_switch_image_channel_preserves_shots_and_refreshes_personal_profile(self):
        from backend.app import video_studio as studio
        from backend.app import image_profiles
        for changes in ({'method': 'running'},
                        {'use_cloud_image_pool': False, 'image_profile_id': 'new', 'image_resolution': '2k'}):
            record = {'id': 'test', 'revision': 1, 'status': 'image_review', 'logs': [], 'references': [],
                      'settings': {'name': 'test', 'style': '', 'characters': '', 'world': '', 'ratio': '16:9'},
                      'creation_parameters': {'use_cloud_image_pool': True, 'method': 'ican',
                          'image_profile_id': 'old', 'image_profile_snapshot': {'id': 'old'},
                          'dynamic_text_mode': 'visual_first'},
                      'shots': [{'id': 'keep', 'image_status': 'completed', 'image': 'keep.jpg',
                                 'video': 'keep.mp4'}]}
            shots = record['shots']
            with patch.object(studio, 'require_user', return_value={'id': 1}), \
                 patch.object(studio, 'directory', return_value=Path('unused-test-path')), \
                 patch.object(studio, 'read', return_value=record), patch.object(studio, 'save'), \
                 patch.object(image_profiles, 'profile_snapshot', return_value={'id': 'new', 'resolution': '2k'}):
                result = studio.edit_project_settings('test', studio.ProjectSettingsEdit(
                    revision=1, name='test', parameters=changes), Mock())
            self.assertIs(result['shots'], shots)
            self.assertEqual(result['status'], 'image_review')
            self.assertEqual(shots[0]['image'], 'keep.jpg')
            self.assertEqual(shots[0]['video'], 'keep.mp4')
            if changes.get('image_profile_id'):
                self.assertEqual(result['creation_parameters']['image_profile_snapshot']['id'], 'new')

    def test_illustrated_batch_timeout_never_silently_copies_neighbor_image(self):
        mapping = [{'macro_scene_id': 'one'}, {'macro_scene_id': 'two'}]
        with patch.object(visual, '_render_poster_with_retry', side_effect=[Path('done.jpg'), control.ImageWaitTimeout('超时')]), \
             patch.object(visual, '_poster_worker_count', return_value=1), \
             patch.object(visual, '_record_partial_poster_success') as save, \
             patch.object(visual.shutil, 'copy2') as copy:
            with self.assertRaises(control.ImageWaitTimeout):
                visual.render_posters_concurrently(mapping, [{'api_key': 'test'}])
        save.assert_called_once()
        copy.assert_not_called()

    def test_runtime_cancel_event_is_not_saved_into_poster_mapping(self):
        mapping = [{'macro_scene_id': 'one', 'progress_label': 'test', '_cancel_event': threading.Event()}]
        with patch.object(visual, '_atomic_write_json') as write, \
             patch.object(visual, 'VISUAL_PROMPT_PLAN_PATH', Path('does-not-exist-control-test.json')), \
             patch.object(visual, '_sync_visual_checkpoint'):
            visual._record_partial_poster_success(mapping, 0, Path('done.jpg'))
        saved = write.call_args_list[0].args[1][0]
        self.assertNotIn('_cancel_event', saved)
        self.assertNotIn('progress_label', saved)

    def test_restart_recovery_clears_stale_running_state_and_keeps_completed(self):
        from backend.app import video_studio as studio
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            record = {'id': 'test', 'revision': 4, 'status': 'image_stopping', 'logs': [],
                      'shots': [{'id': 'done', 'image_status': 'completed', 'image': 'keep.jpg'},
                                {'id': 'stuck', 'image_status': 'running'}]}
            (path / 'record.json').write_text(json.dumps(record), encoding='utf-8')
            with patch.object(studio, '_recover_uploaded_image_status', return_value=False), \
                 patch.object(studio, '_recover_replanned_image_cache', return_value=False), \
                 patch.object(studio, 'project_has_image_edits', return_value=False), \
                 patch.object(studio, 'save'):
                recovered = studio.read(path)
            self.assertEqual(recovered['status'], 'image_review')
            self.assertEqual(recovered['revision'], 5)
            self.assertEqual(recovered['shots'][0]['image'], 'keep.jpg')
            self.assertEqual(recovered['shots'][0]['image_status'], 'completed')
            self.assertEqual(recovered['shots'][1]['image_status'], 'pending')
            self.assertIn('计费', recovered['error'])


if __name__ == '__main__':
    unittest.main()
