from unittest.mock import patch
from test_codex_bridge_scenes import SceneBridgeTests
from backend.app import codex_bridge as bridge, video_studio as studio
from backend.app import codex_image_generation as images


class ImageGenerationTests(SceneBridgeTests):
    def setUp(self):
        super().setUp()
        images.RUNTIME.clear(); images.RUNNING.clear(); studio.IMAGE_EDITS.clear()
        self.addCleanup(images.RUNTIME.clear); self.addCleanup(images.RUNNING.clear)
        self.addCleanup(studio.IMAGE_EDITS.clear)

    def test_confirmed_redraw_and_idempotency(self):
        self.record['shots'][0]['kind'] = 'video'
        self.record['creation_parameters']['director_strategy'] = 'enhanced_beta'
        studio.save(self.path, self.record)
        payload = {'revision': 1, 'timeline_token': bridge.timeline_token(self.record, self.path),
                   'audio_confirmed': True, 'shot_id': 'one', 'request_id': 'request-0001'}
        url = f'/api/codex-bridge/projects/{self.id}/image-'
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), \
             patch.object(images, 'pump') as worker:
            preview = self.client.post(url + 'validate', json=payload)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['actual_reference_count'], 1)
            worker.assert_not_called()
            self.assertEqual(self.client.post(url + 'apply', json=payload).status_code, 409)
            payload.update(confirmed=True, confirmation_token=preview.json()['confirmation_token'])
            response = self.client.post(url + 'apply', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['generation_started'])
            self.assertEqual(worker.call_count, 1)
            self.assertIn('old.jpg', images.RUNTIME[(str(self.path), 'request-0001')]['refs'][0])
            repeated = self.client.post(url + 'apply', json=payload)
            self.assertTrue(repeated.json()['already_submitted'])
            self.assertEqual(worker.call_count, 1)
            current = bridge.read_record(self.path)
            self.assertEqual(current['scenes'], self.record['scenes'])
            self.assertEqual(current['audio'], self.record['audio'])
            status = self.client.get(url + 'status')
            self.assertEqual(status.json()['shots'][0]['image_task']['status'], 'queued')
            payload['shot_id'] = 'two'
            self.assertEqual(self.client.post(url + 'apply', json=payload).status_code, 409)

    def request_payload(self, **extra):
        record = self.load()
        return dict(revision=record['revision'], timeline_token=bridge.timeline_token(record, self.path),
                    audio_confirmed=True, shot_id='one', request_id='test-request-01', **extra)

    def url(self, operation):
        return f'/api/codex-bridge/projects/{self.id}/' + operation

    def submit(self, payload, batch=False):
        operation = 'image-batch-' if batch else 'image-'
        result = self.client.post(self.url(operation+'validate'), json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        return self.client.post(self.url(operation+'apply'), json={**payload, 'confirmed': True,
            'confirmation_token': result.json()['confirmation_token']})

    def test_config_patch_is_partial_and_free(self):
        self.record['creation_parameters'].update(use_cloud_image_pool=True, untouched='kept')
        studio.save(self.path, self.record)
        response = self.client.patch(self.url('generation-settings'), json={'revision': 1, 'use_cloud_image_pool': False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['effective_image_config']['route'], 'api')
        self.assertFalse(response.json()['generation_started'])
        self.assertEqual(self.load()['creation_parameters']['untouched'], 'kept')

    def test_pool_auth_failure_is_not_500(self):
        error = ValueError('pool unavailable'); error.__cause__ = RuntimeError('CLOUD_LOGIN_REQUIRED 401 secret-key')
        with patch.object(studio, '_image_configs', side_effect=error):
            response = self.client.post(self.url('image-validate'), json=self.request_payload())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['detail']['code'], 'IMAGE_AUTH_REQUIRED')
        self.assertNotIn('secret-key', response.text)
        self.assertFalse(response.json()['detail']['charge_submitted'])

    def test_other_shot_completion_does_not_expire_token(self):
        payload = self.request_payload()
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            preview = self.client.post(self.url('image-validate'), json=payload).json()
            latest = self.load(); latest['revision'] += 1
            latest['shots'][2]['image_task'] = {'status': 'completed'}
            studio.save(self.path, latest)
            response = self.client.post(self.url('image-apply'), json={**payload, 'confirmed': True,
                'confirmation_token': preview['confirmation_token']})
            self.assertEqual(response.status_code, 200, response.text)

    def test_target_change_expires_token(self):
        payload = self.request_payload()
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            preview = self.client.post(self.url('image-validate'), json=payload).json()
            latest = self.load(); latest['shots'][0]['image_prompt'] = 'changed'; latest['revision'] += 1
            studio.save(self.path, latest)
            response = self.client.post(self.url('image-apply'), json={**payload, 'confirmed': True,
                'confirmation_token': preview['confirmation_token']})
            self.assertEqual(response.status_code, 409)
            self.assertFalse(images.RUNTIME)

    def test_batch_queue_pause_resume_cancel_and_current_status(self):
        p = self.request_payload(); p.pop('shot_id'); p.pop('request_id')
        p.update(batch_id='batch-test-01', concurrency=7,
            items=[{'shot_id': i, 'request_id': 'request-'+i+'-01'} for i in ('one', 'two', 'three')])
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            response = self.submit(p, batch=True)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(images.RUNTIME), 3)
            repeated = self.client.post(self.url('image-batch-apply'), json=p)
            self.assertTrue(repeated.json()['already_submitted'])
            for action in ('pause', 'resume', 'cancel_pending'):
                result = self.client.post(self.url('image-batch-control'), json={'batch_id': p['batch_id'], 'action': action})
                self.assertEqual(result.status_code, 200, result.text)
            status = self.client.get(self.url('image-status')+'?batch_id='+p['batch_id']).json()
            self.assertTrue(all(i['status']=='cancelled' and not i['charge_submitted'] for i in status['requests']))
            self.assertFalse(images.RUNTIME)

    def test_current_image_preview_and_resolution(self):
        self.record['creation_parameters']['director_strategy'] = 'enhanced_beta'
        self.record['shots'][0]['image'] = 'assets/other.jpg'; studio.save(self.path, self.record)
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            response = self.submit(self.request_payload(use_current_image=True, image_resolution='2k'))
            self.assertEqual(response.status_code, 200, response.text)
            refs = response.json()['references']
            self.assertEqual(refs[0]['kind'], 'current_image')
            self.assertEqual(refs[1]['kind'], 'scene_reference')
            self.assertEqual(images.RUNTIME[(str(self.path), 'test-request-01')]['configs'][0]['resolution'], '2k')

    def test_other_shot_can_submit_while_one_queued(self):
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            self.assertEqual(self.submit(self.request_payload()).status_code, 200)
            p = self.request_payload(); p.update(shot_id='two', request_id='test-request-02')
            self.assertEqual(self.submit(p).status_code, 200)
            p = self.request_payload(); p['request_id'] = 'different-request'
            response = self.client.post(self.url('image-validate'), json=p)
            self.assertEqual(response.json()['detail']['code'], 'SHOT_BUSY')

    def test_worker_writes_only_current_request_and_preserves_audio(self):
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            self.assertEqual(self.submit(self.request_payload()).status_code, 200)
            with patch.object(images, 'render', return_value=self.path/'assets/other.jpg'):
                images.worker(self.path, 'test-request-01')
        current = self.load()
        self.assertEqual(current['codex_bridge']['image_requests']['test-request-01']['status'], 'completed')
        self.assertEqual(current['audio'], self.record['audio'])
        self.assertEqual(current['scenes'], self.record['scenes'])

    def test_late_result_preserved_without_overwrite(self):
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump'):
            self.assertEqual(self.submit(self.request_payload()).status_code, 200)
            def render(*args):
                current = self.load(); current['shots'][0]['image_prompt'] = 'newer plan'; studio.save(self.path, current)
                return self.path/'assets/other.jpg'
            with patch.object(images, 'render', side_effect=render): images.worker(self.path, 'test-request-01')
        current = self.load()
        self.assertEqual(current['codex_bridge']['image_requests']['test-request-01']['status'], 'stale_result')
        self.assertEqual(current['shots'][0]['image_prompt'], 'newer plan')

    def test_restart_does_not_resubmit(self):
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump') as pump:
            self.assertEqual(self.submit(self.request_payload()).status_code, 200)
            images.RUNTIME.clear()
            response = self.client.get(self.url('image-status')).json()
            self.assertEqual(response['requests'][0]['status'], 'interrupted_pending')
            response = self.client.post(self.url('image-batch-control'), json={'batch_id':'test-request-01', 'action':'resume'})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(pump.call_count, 1)

    def test_scheduler_seven_slots_no_duplicate_launch_and_eighth_queued(self):
        import copy
        self.record['shots'] = [dict(copy.deepcopy(self.record['shots'][0]), id='shot-'+str(n)) for n in range(8)]
        studio.save(self.path, self.record)
        p = self.request_payload(); p.pop('shot_id'); p.pop('request_id')
        p.update(batch_id='batch-eight', concurrency=7,
            items=[{'shot_id': s['id'], 'request_id': 'request-eight-'+str(n)} for n,s in enumerate(self.record['shots'])])
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images.threading, 'Thread') as thread:
            response = self.submit(p, batch=True)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(thread.call_count, 7)
            with studio.LOCK: images.pump(self.path)
            self.assertEqual(thread.call_count, 7)
            self.assertEqual(len(images.RUNNING), 7)
            self.assertEqual(len(images.RUNTIME), 8)

    def test_batch_validation_atomic_and_legacy_request_not_resubmitted(self):
        p = self.request_payload(); p.pop('shot_id'); p.pop('request_id')
        p.update(batch_id='invalid-batch', items=[{'shot_id': 'one','request_id':'atomic-request-01'},
            {'shot_id':'missing','request_id':'atomic-request-02'}])
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), patch.object(images, 'pump') as pump:
            self.assertEqual(self.client.post(self.url('image-batch-validate'), json=p).status_code, 404)
            self.assertFalse(images.RUNTIME); pump.assert_not_called()
            payload = self.request_payload()
            latest = self.load(); latest['codex_bridge'] = {'image_requests': {payload['request_id']:
                {'receipt': bridge.digest(payload), 'result': {'ok': True, 'generation_started': True}}}}
            studio.save(self.path, latest)
            result = self.client.post(self.url('image-apply'), json=payload)
            self.assertTrue(result.json()['already_submitted']); pump.assert_not_called()
