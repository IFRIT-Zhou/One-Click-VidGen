"""H3 validation is opt-in; a rejected conversion must allow a fresh Start."""
import copy
import json
import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from backend.app import cloud_video as cloud, video_generation as video
from backend.app.cloud_client import CloudApiError
from backend.app.h3_prompt_agent import H3PromptIncomplete
import test_shot_video_options as fixtures


class H3RoutingRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ShotVideoOptionsTests.setUp(self)
        Image.new('RGB', (32, 32), 'white').save(self.path / 'core.jpg')
        self.record['shots'][0].update(image='core.jpg', image_status='completed')
        (self.path / 'done.mp4').write_bytes(b'existing-completed-video')
        self.completed = dict(id='done', kind='video', video_status='completed',
                              video='done.mp4', video_queued=False)
        self.record['shots'].append(copy.deepcopy(self.completed))

        def read(_):
            return copy.deepcopy(self.record)

        def save(_, value):
            self.record.clear()
            self.record.update(copy.deepcopy(value))

        self.stack.enter_context(patch.object(video.studio, 'read', side_effect=read))
        self.stack.enter_context(patch.object(video.studio, 'save', side_effect=save))
        self.stack.enter_context(patch.object(video.studio, 'project_language_scope', return_value=nullcontext()))
        self.stack.enter_context(patch.object(video.studio, '_storyboard_image_path', return_value=self.path / 'core.jpg'))
        self.stack.enter_context(patch.object(video.threading, 'Thread',
            side_effect=lambda **kw: SimpleNamespace(
                start=lambda: kw['target'](*kw.get('args', ())))))
        self.stack.enter_context(patch.dict(video.VIDEO_WORKERS, {}, clear=True))
        self.stack.enter_context(patch.dict(video.LOCAL_VIDEO_QUEUES, {}, clear=True))
        self.stack.enter_context(patch.dict(video.LOCAL_VIDEO_RUNNING, {}, clear=True))
        self.addCleanup(video.studio.ACTIVE.discard, str(self.path))
        self.addCleanup(video.studio.CANCEL_EVENTS.pop, str(self.path), None)

    def start(self, options=None):
        # Ordinary Start, without retry_failed/regenerate_completed overrides.
        return video.generate('test', video.GenerateClips(
            revision=self.record['revision'], shot_ids=['a'], options=options), None)

    def assert_idle(self):
        self.assertEqual(self.record['status'], 'video_review')
        self.assertNotIn(str(self.path), video.studio.ACTIVE)
        self.assertNotIn(str(self.path), video.studio.CANCEL_EVENTS)
        self.assertNotIn(str(self.path), video.VIDEO_WORKERS)
        self.assertNotIn(str(self.path), video.LOCAL_VIDEO_QUEUES)
        self.assertNotIn(str(self.path), video.LOCAL_VIDEO_RUNNING)
        self.assertFalse(self.record['shots'][0].get('video_queued'))
        self.assertEqual(self.record['shots'][1], self.completed)
        self.assertEqual((self.path / 'done.mp4').read_bytes(), b'existing-completed-video')

    @staticmethod
    def write_local_clip(*args, **kwargs):
        kwargs['output_path'].write_bytes(b'local-fixture-video')

    def test_local_conversion_failure_then_ordinary_start_runs_again(self):
        with patch.object(video, 'convert_for_h3', side_effect=[
                H3PromptIncomplete('缺少有效动作/阶段内容'), ('valid H3 actions', 'new-hash')]) as convert, \
             patch.object(video, '_freeze_local_request', wraps=video._freeze_local_request) as freeze, \
             patch.object(video, 'run_video_profile', side_effect=self.write_local_clip) as run:
            self.start()
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'pending')
            self.assertTrue(shot['video_not_submitted'])
            self.assertFalse(shot['video_terminal'])
            self.assertFalse(shot['video_resume_available'])
            self.assertIn('重新点击生成', shot['video_progress_message'])
            self.assertNotIn('video_request', shot)
            self.assertNotIn('video_attempt', shot)
            freeze.assert_not_called()
            run.assert_not_called()
            self.assert_idle()

            self.start()
            self.assertEqual(convert.call_count, 2)
            freeze.assert_called_once()
            run.assert_called_once()
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'completed')
            self.assertFalse(shot['video_not_submitted'])
            self.assertEqual(shot['video_attempt'], 1)
            self.assertEqual(shot['video_request']['prompt_format'], 'h3_ref2va')
            self.assertIn('valid H3 actions', shot['video_request']['prompt'])
            self.assertEqual(run.call_args.kwargs['existing_prompt_id'], '')
            self.assert_idle()

    def test_disabled_h3_conversion_uses_generic_local_prompt(self):
        with patch.object(video, 'convert_for_h3', side_effect=AssertionError('H3 must not run')) as convert, \
             patch.object(video, 'run_video_profile', side_effect=self.write_local_clip) as run:
            self.start({'backend': 'comfyui', 'profile_id': 'new', 'h3_prompt_agent': False})
            convert.assert_not_called()
            run.assert_called_once()
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'completed')
            self.assertEqual(shot['video_request']['prompt_format'], 'generic')
            self.assertIn('动作', shot['video_request']['prompt'])
            self.assertNotIn('detailed_description', shot['video_request']['prompt'])
            self.assert_idle()

    def test_seedance_api_ignores_stale_h3_flag_and_cache(self):
        config = dict(protocol='ark', model='seedance-test', api_key='fixture-only',
                      base_url='https://example.invalid/api', submit_path='/tasks',
                      query_path='/tasks/{task_id}', resolution='720p')
        self.record['shots'][0].update(h3_prompt='subject_definitions: incomplete cache',
                                      h3_prompt_source='stale')
        def run_api(path, record, identities, configs):
            for identity in identities:
                video._process_api_clip(path, identity, configs[identity],
                                        threading.Event(), threading.Event())
        with patch.object(video, 'load_config', return_value=config), \
             patch.object(video, '_start_worker', side_effect=run_api), \
             patch.object(video, 'ArkVideoProvider') as provider, \
             patch.object(video, '_video_version', return_value='test-version'), \
             patch.object(video, 'convert_for_h3', side_effect=AssertionError('H3 must not run')) as convert, \
             patch.object(video, '_start_local_worker') as local:
            def finish_api(request, state_path, **kwargs):
                request.output_path.write_bytes(b'api-fixture-video')
                state_path.write_text(json.dumps({'task_id': 'seedance-done', 'status': 'completed'}), encoding='utf-8')
            provider.return_value.run.side_effect = finish_api
            self.start({'backend': 'api', 'resolution': '720p', 'h3_prompt_agent': True})
            convert.assert_not_called()
            local.assert_not_called()
            provider.return_value.run.assert_called_once()
            request = provider.return_value.run.call_args.args[0]
            self.assertIn('动作', request.prompt)
            self.assertNotIn('subject_definitions', request.prompt)
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'completed')
            self.assertFalse(shot['video_generation_options']['h3_prompt_agent'])
            self.assertEqual(shot['video_request']['model'], 'seedance-test')

    def test_actual_engine_failure_is_not_marked_as_unsubmitted(self):
        with patch.object(video, 'convert_for_h3', return_value=('valid actions', 'hash')), \
             patch.object(video, 'run_video_profile', side_effect=RuntimeError('GPU execution failed')):
            self.start()
        shot = self.record['shots'][0]
        self.assertEqual(shot['video_status'], 'failed')
        self.assertTrue(shot['video_terminal'])
        self.assertFalse(shot['video_not_submitted'])
        self.assertTrue(shot['video_execution_started'])
        self.assertIn('video_request', shot)
        self.assert_idle()

    def test_preparation_error_preserves_previous_frozen_request(self):
        previous = dict(backend='comfyui', profile_id='old', prompt='old actions', prompt_format='h3_ref2va')
        self.record['shots'][0].update(video_status='failed', video_terminal=True,
            video_request=copy.deepcopy(previous), video_attempt=1, video='previous.mp4')
        with patch.object(video, 'convert_for_h3', side_effect=[
                H3PromptIncomplete('missing actions'), ('valid new actions', 'new-hash')]) as convert, \
             patch.object(video, 'run_video_profile', side_effect=self.write_local_clip) as run:
            self.start()
            shot = self.record['shots'][0]
            run.assert_not_called()
            self.assertEqual(shot['video_request'], previous)
            self.assertEqual(shot['video_attempt'], 1)
            self.assertEqual(shot['video'], 'previous.mp4')
            self.assertTrue(shot['video_terminal'])
            self.assert_idle()

            self.start()
            run.assert_called_once()
            self.assertEqual(convert.call_count, 2)
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'completed')
            self.assertEqual(shot['video_attempt'], 2)
            self.assertEqual(shot['video_history'][0]['video_request'], previous)
            self.assert_idle()

    def test_cloud_conversion_failure_then_ordinary_start_submits_once(self):
        client = Mock()
        client.config.max_wait_seconds = 10
        client.video_capabilities.return_value = {
            'enabled': True, 'workflow_revision': 'test-h3', 'model': 'minimax-h3',
            'resolutions': [{'width': 384, 'height': 640}],
            'duration_seconds': {'min': 2, 'max': 14}}
        client.video_job.side_effect = CloudApiError('missing', status_code=404)
        client.video_asset.return_value = {'asset_id': 'asset'}
        client.video_quote.side_effect = lambda spec: {
            'quote_id': 'quote', 'credits': '0', 'test_mode': True, 'spec': spec}
        client.create_video_job.return_value = {'job_id': 'new-job', 'status': 'succeeded'}
        client.download_to.side_effect = lambda url, target, **kw: target.write_bytes(b'cloud-fixture-video')
        with patch.object(cloud, 'cloud_client_for', return_value=client), \
             patch.object(video, 'convert_for_h3', side_effect=[
                H3PromptIncomplete('缺少有效动作/阶段内容'), ('valid H3 actions', 'new-hash')]) as convert, \
             patch('backend.app.pipeline.probe_media_duration', return_value=6):
            self.start({'backend': 'cloud_h3', 'h3_prompt_agent': True,
                        'resolution': 'custom', 'width': 384, 'height': 640})
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'pending')
            self.assertTrue(shot['video_not_submitted'])
            self.assertFalse(shot['video_terminal'])
            client.video_asset.assert_not_called()
            client.video_quote.assert_not_called()
            client.create_video_job.assert_not_called()
            self.assert_idle()

            self.start()
            self.assertEqual(convert.call_count, 2)
            client.video_asset.assert_called_once()
            client.video_quote.assert_called_once()
            client.create_video_job.assert_called_once()
            client.download_to.assert_called_once()
            shot = self.record['shots'][0]
            self.assertEqual(shot['video_status'], 'completed')
            self.assertFalse(shot['video_not_submitted'])
            self.assertEqual(shot['video_task_id'], 'new-job')
            self.assertEqual(shot['video_attempt'], 1)
            self.assertEqual(self.record['error'], '')
            self.assert_idle()


if __name__ == '__main__':
    unittest.main()
