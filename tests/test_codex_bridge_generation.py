from unittest.mock import patch
from test_codex_bridge_scenes import SceneBridgeTests
from backend.app import codex_bridge as bridge, video_studio as studio


class ImageGenerationTests(SceneBridgeTests):
    def test_confirmed_redraw_and_idempotency(self):
        self.record['shots'][0]['kind'] = 'video'
        self.record['creation_parameters']['director_strategy'] = 'enhanced_beta'
        studio.save(self.path, self.record)
        payload = {'revision': 1, 'timeline_token': bridge.timeline_token(self.record, self.path),
                   'audio_confirmed': True, 'shot_id': 'one', 'request_id': 'request-0001'}
        url = f'/api/codex-bridge/projects/{self.id}/image-'
        def start(path, record, shot, data, configs, refs, version):
            shot['image_task'] = {'status': 'running'}
            studio.save(path, record)
        with patch.object(studio, '_image_configs', return_value=[{'method': 'mock'}]), \
             patch.object(studio, '_start_storyboard_redraw', side_effect=start) as worker:
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
            self.assertIn('old.jpg', worker.call_args.args[5][0])
            repeated = self.client.post(url + 'apply', json=payload)
            self.assertTrue(repeated.json()['already_submitted'])
            self.assertEqual(worker.call_count, 1)
            current = bridge.read_record(self.path)
            self.assertEqual(current['scenes'], self.record['scenes'])
            self.assertEqual(current['audio'], self.record['audio'])
            status = self.client.get(url + 'status')
            self.assertEqual(status.json()['shots'][0]['image_task']['status'], 'running')
            payload['shot_id'] = 'two'
            self.assertEqual(self.client.post(url + 'apply', json=payload).status_code, 409)
