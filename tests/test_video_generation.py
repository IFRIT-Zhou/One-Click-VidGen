"""Offline API/worker tests. No provider request or paid generation is made."""
import copy
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import video_generation as generation, video_studio as studio
from module6_dynamic_video import DynamicVideoStopped, DynamicVideoTaskFailed, DynamicVideoTaskUnknown
from module6_dynamic_video import RunningHubVideoProvider as RealProvider


class VideoGenerationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(studio, 'ROOT', Path(self.temp.name)).start()
        patch.object(studio, 'require_user', return_value={'id': '1'}).start()
        self.config = dict(api_key='private-test-key', base_url='https://video.test',
                           submit_path='/multimodal-video', resolution='720p')
        patch.object(generation, 'load_config', return_value=self.config).start()
        self.calls, self.paid, self.mode, self.provider_keys = [], 0, 'success', []
        self.waiting = threading.Event()
        self.parallel_barrier = threading.Barrier(2)
        test = self

        class FakeProvider:
            def __init__(self, api_key, *, base_url, submit_path, query_path=None, upload_path=None):
                self.api_key = api_key

            def run(self, request, state_path, *, progress, should_stop):
                test.calls.append(request)
                test.provider_keys.append(self.api_key)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding='utf-8'))
                    if not state.get('task_id'):
                        raise DynamicVideoTaskUnknown('结果未知，不能重投')
                else:
                    test.paid += 1
                    state = {'task_id': f'task-{test.paid}', 'status': 'RUNNING'}
                    if test.mode == 'lost':
                        state = {'status': 'UNKNOWN'}
                    state_path.write_text(json.dumps(state), encoding='utf-8')
                progress('已保存云端任务编号')
                if test.mode == 'parallel':
                    test.parallel_barrier.wait(timeout=2)
                if test.mode == 'failed':
                    state.update(status='FAILED', terminal=True)
                    state_path.write_text(json.dumps(state), encoding='utf-8')
                    raise DynamicVideoTaskFailed('模拟云端明确失败')
                if test.mode in {'unknown', 'lost'}:
                    raise DynamicVideoTaskUnknown('模拟网络中断')
                if test.mode == 'stop':
                    test.waiting.set()
                    deadline = time.monotonic() + 5
                    while not should_stop() and time.monotonic() < deadline:
                        time.sleep(.01)
                    if should_stop():
                        raise DynamicVideoStopped('停止本地查询，云端任务保留')
                request.output_path.write_bytes(b'offline-video-fixture')
                state['status'] = 'DOWNLOADED'
                state_path.write_text(json.dumps(state), encoding='utf-8')
                return request.output_path

        self.provider = patch.object(generation, 'RunningHubVideoProvider', FakeProvider).start()
        app = FastAPI()
        app.include_router(studio.router)
        app.include_router(generation.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.identity = 'a' * 32
        self.path = studio.directory('1', self.identity)
        self.path.mkdir(parents=True)
        for name, color in [('core.jpg', 'white'), ('chosen.jpg', 'red'), ('unused.jpg', 'blue')]:
            Image.new('RGB', (16, 16), color).save(self.path / name)
        self.record = dict(id=self.identity, revision=1, status='video_generation_ready',
            settings={'name': '离线动态测试', 'ratio': '16:9'}, scenes=[], logs=[], error='',
            references=[dict(id='chosen', file='chosen.jpg'), dict(id='unused', file='unused.jpg')],
            shots=[dict(id=identity, kind=kind, duration=4.6, generation_duration=5,
                        image='core.jpg', image_status='completed', reference_ids=['chosen'],
                        video_prompt='图1是核心图，图2是选定人物。观众举手，静音。')
                   for identity, kind in [('dynamic1', 'video'), ('still', 'static'), ('dynamic2', 'video')]])
        studio.save(self.path, self.record)
        self.url = '/api/video-studio/' + self.identity
        self.addCleanup(self.cleanup_worker)

    def cleanup_worker(self):
        event = studio.CANCEL_EVENTS.get(str(self.path))
        if event is not None:
            event.set()
        self.await_idle()

    def await_idle(self):
        deadline = time.monotonic() + 5
        while str(self.path) in studio.ACTIVE and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertNotIn(str(self.path), studio.ACTIVE)
        return self.client.get(self.url).json()

    def start(self, ids=None, retry=False):
        current = self.client.get(self.url).json()
        body = {'revision': current['revision'], 'retry_failed': retry}
        if ids is not None:
            body['shot_ids'] = ids
        return self.client.post(self.url + '/videos/generate', json=body)

    def test_reference_audio_is_cut_to_the_shot_interval_as_wav(self):
        source = self.path / 'assets' / 'audio.wav'
        source.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(source), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b'\0\0' * 48000)
        output = self.path / 'frozen' / 'voice.wav'
        generation._freeze_shot_audio(self.path, {'start': .2, 'duration': .35}, output)
        with wave.open(str(output), 'rb') as stream:
            self.assertEqual(stream.getframerate(), 48000)
            self.assertAlmostEqual(stream.getnframes() / stream.getframerate(), .35, places=2)

    def test_one_shot_uses_exact_prompt_and_only_selected_references(self):
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        record = self.await_idle()
        self.assertEqual(self.paid, 1)
        self.assertEqual(record['status'], 'video_review')
        request = self.calls[0]
        self.assertEqual(request.prompt,
                         generation.enforce_no_auto_subtitles(self.record['shots'][0]['video_prompt']))
        self.assertEqual(len(request.image_paths), 2)
        self.assertEqual(request.duration, 5)
        self.assertFalse(request.generate_audio)
        with Image.open(request.image_paths[0]) as image:
            self.assertGreater(min(image.getpixel((2, 2))), 240)
        with Image.open(request.image_paths[1]) as image:
            self.assertGreater(image.getpixel((2, 2))[0], 230)
        self.assertFalse(record['shots'][1].get('video'))
        self.assertFalse(record['shots'][2].get('video'))
        self.assertNotIn('private-test-key', json.dumps(record))
        snapshot = (request.output_path.parent / 'request.json').read_text(encoding='utf-8')
        self.assertNotIn('private-test-key', snapshot)
        self.assertEqual(self.client.get(self.url + '/videos/dynamic1').status_code, 200)
        self.assertEqual(self.client.get(self.url + '/videos/still').status_code, 404)

    def test_all_skips_static_and_completed_and_opening_never_generates(self):
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        self.await_idle()
        self.assertEqual(self.start().status_code, 200)
        final = self.await_idle()
        self.assertEqual(self.paid, 2)

    def test_completed_project_can_regenerate_all_after_workflow_change(self):
        current = studio.read(self.path)
        current['status'] = 'completed'
        current['export'] = {'raw': 'old.mp4', 'subtitles': 'old-subtitles.mp4'}
        for row in current['shots']:
            if row['kind'] == 'video':
                row.update(video_status='completed', video='old.mp4', video_version='old')
        studio.save(self.path, current)
        response = self.client.post(self.url + '/videos/generate', json={
            'revision': current['revision'],
            'shot_ids': ['dynamic1', 'dynamic2'],
            'regenerate_completed': True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        deadline = time.monotonic() + 5
        while str(self.path) in studio.ACTIVE and time.monotonic() < deadline:
            time.sleep(.01)
        final = studio.read(self.path)
        self.assertEqual(self.paid, 2)
        self.assertNotIn('export', final)
        self.assertTrue(all(row.get('video_status') == 'completed'
                            for row in final['shots'] if row['kind'] == 'video'))
        self.assertTrue(all(row['video_status'] == 'completed' for row in final['shots'] if row['kind'] == 'video'))
        self.client.get(self.url)
        self.client.get('/api/video-studio')
        self.assertEqual(self.paid, 2)
        self.assertEqual(self.start().status_code, 409)
        self.assertEqual(self.paid, 2)

    def test_uploaded_replacement_archives_current_and_history_can_be_restored(self):
        record = studio.read(self.path)
        shot = record['shots'][0]
        current = self.path / 'assets' / 'videos' / 'dynamic1' / 'attempt_001' / 'clip.mp4'
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b'generated-current')
        shot.update(video_attempt=1, video_backend='comfyui', video_status='completed',
                    video=str(current.relative_to(self.path)).replace('\\', '/'),
                    video_version=generation._video_version(current))
        record.update(status='completed', export={'raw': 'old.mp4'})
        studio.save(self.path, record)
        with patch('backend.app.pipeline.probe_media_duration', return_value=6.2):
            uploaded = self.client.post(
                self.url + '/videos/dynamic1/upload', data={'revision': record['revision']},
                files={'file': ('replacement.mp4', b'uploaded-replacement', 'video/mp4')})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        changed = uploaded.json()
        live = changed['shots'][0]
        self.assertEqual(changed['status'], 'video_review')
        self.assertNotIn('export', changed)
        self.assertEqual(live['video_backend'], 'upload')
        self.assertEqual(live['video_status'], 'completed')
        self.assertEqual(live['video_history'][0]['video_version'], shot['video_version'])
        self.assertEqual(self.client.get(self.url + '/videos/dynamic1/history/0').content,
                         b'generated-current')
        adopted = self.client.post(self.url + '/videos/dynamic1/history/0/adopt', json={
            'revision': changed['revision']})
        self.assertEqual(adopted.status_code, 200, adopted.text)
        restored = adopted.json()['shots'][0]
        self.assertEqual(restored['video_version'], shot['video_version'])
        self.assertEqual(len(restored['video_history']), 1)
        self.assertEqual(self.client.get(self.url + '/videos/dynamic1').content,
                         b'generated-current')

    def test_historical_video_can_be_deleted_without_touching_current(self):
        record = studio.read(self.path)
        shot = record['shots'][0]
        current = self.path / 'assets' / 'videos' / 'dynamic1' / 'attempt_002' / 'clip.mp4'
        history = self.path / 'assets' / 'videos' / 'dynamic1' / 'attempt_001' / 'clip.mp4'
        current.parent.mkdir(parents=True, exist_ok=True)
        history.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b'current')
        history.write_bytes(b'history')
        shot.update(video_attempt=2, video_status='completed',
                    video=str(current.relative_to(self.path)).replace('\\', '/'),
                    video_version='current-version', video_history=[{
                        'video_attempt': 1, 'video_status': 'completed',
                        'video': str(history.relative_to(self.path)).replace('\\', '/'),
                        'video_version': 'history-version'}])
        record['status'] = 'video_review'
        studio.save(self.path, record)
        deleted = self.client.delete(self.url + '/videos/dynamic1/history/0', params={
            'revision': record['revision']})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()['shots'][0]['video_history'], [])
        self.assertTrue(current.is_file())
        self.assertFalse(history.exists())

    def test_multiple_video_keys_generate_in_parallel(self):
        self.config.update(api_keys=['key-a', 'key-b'], per_key_concurrency=1,
                           concurrency_mode='auto', total_concurrency=2)
        self.mode = 'parallel'
        self.assertEqual(self.start().status_code, 200)
        final = self.await_idle()
        self.assertEqual(self.paid, 2)
        self.assertEqual(set(self.provider_keys), {'key-a', 'key-b'})
        self.assertTrue(all(shot.get('video_status') == 'completed'
                            for shot in final['shots'] if shot['kind'] == 'video'))
        fingerprints = {shot['video_request']['account_fingerprint']
                        for shot in final['shots'] if shot['kind'] == 'video'}
        self.assertEqual(len(fingerprints), 2)

    def test_unknown_with_id_resumes_without_new_paid_submission(self):
        self.mode = 'unknown'
        self.assertEqual(self.start().status_code, 200)
        first = self.await_idle()
        self.assertEqual(self.paid, 1)
        self.assertEqual(first['shots'][0]['video_status'], 'unknown')
        self.assertTrue(first['shots'][0]['video_resume_available'])
        self.assertEqual(first['shots'][2]['video_status'], 'pending')
        self.mode = 'success'
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        final = self.await_idle()
        self.assertEqual(self.paid, 1)
        self.assertEqual(final['shots'][0]['video_status'], 'completed', final['shots'][0].get('video_error'))

    def test_lost_submission_id_cannot_be_retried_even_with_retry_flag(self):
        self.mode = 'lost'
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        self.await_idle()
        self.assertEqual(self.paid, 1)
        self.assertEqual(self.start(['dynamic1'], retry=True).status_code, 400)
        self.assertEqual(self.paid, 1)

    def test_confirmed_failure_requires_explicit_retry_and_archives_old_identity(self):
        self.mode = 'failed'
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        first = self.await_idle()['shots'][0]
        self.assertTrue(first['video_terminal'])
        self.assertEqual(self.start(['dynamic1']).status_code, 400)
        self.mode = 'success'
        self.assertEqual(self.start(['dynamic1'], retry=True).status_code, 200)
        final = self.await_idle()['shots'][0]
        self.assertEqual(self.paid, 2)
        self.assertEqual(final['video_attempt'], 2)
        self.assertEqual(final['video_history'][0]['video_task_id'], 'task-1')
        self.assertTrue((self.path / first['video_state_file']).is_file())

    def test_stop_preserves_paid_task_and_does_not_submit_following_shot(self):
        self.mode = 'stop'
        self.assertEqual(self.start().status_code, 200)
        self.assertTrue(self.waiting.wait(3))
        for old_endpoint in ('/stop', '/images/stop'):
            self.assertEqual(self.client.post(self.url + old_endpoint).json()['status'], 'video_generating')
        self.assertFalse(studio.CANCEL_EVENTS[str(self.path)].is_set())
        self.assertEqual(self.client.post(self.url + '/plan').status_code, 409)
        self.assertEqual(self.client.post(self.url + '/videos/stop').status_code, 200)
        final = self.await_idle()
        self.assertEqual(self.paid, 1)
        self.assertEqual(final['shots'][0]['video_status'], 'stopped')
        self.assertTrue(final['shots'][0]['video_resume_available'])
        self.assertEqual(final['shots'][2]['video_status'], 'pending')

    def test_safe_pause_before_paid_submission_can_continue_same_attempt(self):
        record = copy.deepcopy(self.record)
        shot = record['shots'][0]
        generation._freeze_request(self.path, record, shot, self.config)
        state_path = self.path / shot['video_state_file']
        state_path.write_text(json.dumps({'status': 'PAUSED_BEFORE_SUBMIT',
                                         'paid_submission_started': False}), encoding='utf-8')
        shot['video_status'] = 'stopped'
        record['status'] = 'video_review'
        studio.save(self.path, record)
        # This mock models only the continuation: no previous paid request exists.
        def finish(request, saved_state, **kwargs):
            self.assertEqual(saved_state.resolve(), state_path.resolve())
            request.output_path.write_bytes(b'video')
            saved_state.write_text(json.dumps({'status': 'DOWNLOADED', 'task_id': 'first-paid'}), encoding='utf-8')
            return request.output_path
        with patch.object(self.provider, 'run', side_effect=finish) as called:
            self.assertEqual(self.start(['dynamic1']).status_code, 200)
            final = self.await_idle()
        called.assert_called_once()
        self.assertEqual(final['shots'][0]['video_attempt'], 1)
        self.assertEqual(final['shots'][0]['video_status'], 'completed', final['shots'][0].get('video_error'))

    def test_stop_at_real_provider_entry_records_safe_unsubmitted_state(self):
        provider = RealProvider(self.config['api_key'], base_url=self.config['base_url'],
                                submit_path=self.config['submit_path'])
        def cancel_at_entry(request, state_path, **kwargs):
            # Simulate the precise stop race after the worker's durable marker.
            live = studio.read(self.path)['shots'][0]
            self.assertTrue(live['video_execution_started'])
            return provider.run(request, state_path, should_stop=lambda: True)
        with patch.object(self.provider, 'run', side_effect=cancel_at_entry), \
                patch.object(provider, 'submit') as submit, patch.object(provider, 'query') as query:
            self.assertEqual(self.start(['dynamic1']).status_code, 200)
            shot = self.await_idle()['shots'][0]
        submit.assert_not_called()
        query.assert_not_called()
        self.assertEqual(shot['video_status'], 'stopped')
        self.assertTrue(shot['video_resume_available'])
        self.assertTrue(shot['video_not_submitted'])
        self.assertEqual(self.paid, 0)

    def test_missing_local_clip_can_be_downloaded_again_without_new_submission(self):
        self.start(['dynamic1'])
        record = self.await_idle()
        (self.path / record['shots'][0]['video']).unlink()
        loaded = self.client.get(self.url).json()
        self.assertEqual(loaded['shots'][0]['video_status'], 'unknown')
        self.assertTrue(loaded['shots'][0]['video_resume_available'])
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        self.assertEqual(self.await_idle()['shots'][0]['video_status'], 'completed')
        self.assertEqual(self.paid, 1)

    def test_missing_paid_state_never_causes_a_new_submission(self):
        self.mode = 'unknown'
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        record = self.await_idle()
        (self.path / record['shots'][0]['video_state_file']).unlink()
        response = self.start(['dynamic1'], retry=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('状态文件缺失', response.text)
        self.assertEqual(self.client.get(self.url).json()['shots'][0]['video_task_id'], 'task-1')
        self.assertEqual(self.paid, 1)

    def test_missing_clip_and_state_preserve_paid_identity_and_block_new_post(self):
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        record = self.await_idle()
        shot = record['shots'][0]
        (self.path / shot['video']).unlink()
        (self.path / shot['video_state_file']).unlink()
        loaded = self.client.get(self.url).json()['shots'][0]
        self.assertEqual(loaded['video_task_id'], 'task-1')
        self.assertFalse(loaded['video_resume_available'])
        self.assertIn('状态文件缺失', loaded['video_error'])
        self.assertEqual(self.start(['dynamic1'], retry=True).status_code, 400)
        self.assertEqual(self.paid, 1)

    def test_missing_unknown_state_without_id_is_also_frozen(self):
        self.mode = 'lost'
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        record = self.await_idle()
        self.assertTrue(record['shots'][0]['video_execution_started'])
        (self.path / record['shots'][0]['video_state_file']).unlink()
        self.assertEqual(self.start(['dynamic1'], retry=True).status_code, 400)
        self.assertEqual(self.paid, 1)

    def test_prepared_but_never_executed_request_can_start(self):
        record = copy.deepcopy(self.record)
        generation._freeze_request(self.path, record, record['shots'][0], self.config)
        record['status'] = 'video_review'
        studio.save(self.path, record)
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        final = self.await_idle()['shots'][0]
        self.assertEqual(final['video_attempt'], 1)
        self.assertEqual(final['video_status'], 'completed')
        self.assertEqual(self.paid, 1)

    def test_restart_recovery_is_local_and_does_not_submit_or_query(self):
        self.mode = 'unknown'
        self.start(['dynamic1'])
        record = self.await_idle()
        record.update(status='video_generating')
        record['shots'][0]['video_status'] = 'running'
        studio.save(self.path, record)
        calls = len(self.calls)
        loaded = self.client.get(self.url).json()
        self.assertEqual(loaded['status'], 'video_review')
        self.assertEqual(loaded['shots'][0]['video_task_id'], 'task-1')
        self.assertTrue(loaded['shots'][0]['video_resume_available'])
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.paid, 1)

    def test_invalid_image_can_be_fixed_without_leaving_a_fake_paid_attempt(self):
        (self.path / 'core.jpg').write_bytes(b'not-image')
        self.assertEqual(self.start(['dynamic1']).status_code, 400)
        self.assertEqual(self.paid, 0)
        Image.new('RGB', (16, 16), 'white').save(self.path / 'core.jpg')
        self.assertEqual(self.start(['dynamic1']).status_code, 200)
        self.assertEqual(self.await_idle()['shots'][0]['video_status'], 'completed')

    def test_stage_revision_selection_and_cross_user_guards(self):
        self.assertEqual(self.start(['still']).status_code, 400)
        self.assertEqual(self.start(['missing']).status_code, 400)
        self.assertEqual(self.client.post(self.url + '/videos/generate', json={'revision': 0}).status_code, 409)
        record = copy.deepcopy(self.record)
        record['status'] = 'image_review'
        studio.save(self.path, record)
        self.assertEqual(self.start(['dynamic1']).status_code, 409)
        with patch.object(studio, 'require_user', return_value={'id': 'another-user'}):
            self.assertEqual(self.client.get(self.url + '/videos/dynamic1').status_code, 404)
        self.assertEqual(self.paid, 0)

    def test_video_credentials_need_explicit_configuration_before_preparing(self):
        self.config['api_key'] = ''
        response = self.start(['dynamic1'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('视频 API', response.text)
        self.assertFalse((self.path / 'assets/videos').exists())
        self.assertEqual(self.paid, 0)


if __name__ == '__main__':
    unittest.main()
