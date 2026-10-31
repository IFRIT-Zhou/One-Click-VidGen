"""Pre-submit failures must not masquerade as resumable remote video jobs."""
import copy
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import cloud_video as cloud, video_generation as vg
from backend.app.cloud_client import CloudApiError


class CloudPreparationRecoveryTests(unittest.TestCase):
    def frozen(self, **values):
        return dict(id='s5', kind='video', video_backend='cloud_h3', video_status='pending',
                    video_terminal=False, video_execution_started=False,
                    video_request={'backend': 'cloud_h3', 'client_job_id': 'stable-client',
                                   'quote_id': 'saved-quote', 'width': 640, 'height': 384, 'duration': 4},
                    **values)

    def run_worker(self, shot=None, *, convert=None, freeze=None, client=None, event=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)
        (path / 'completed.mp4').write_bytes(b'keep-completed-clip')
        completed = dict(id='s1', kind='video', video_status='completed', video_queued=False,
                         video='completed.mp4', video_task_id='completed-job', duration=7,
                         video_request={'client_job_id': 'completed-client'})
        record = dict(id='project', revision=1, status='video_generating', logs=[],
                      active_video_backend='cloud_h3', creation_parameters={'language_route': 'personal'},
                      shots=[completed, shot or dict(id='s5', kind='video', video_prompt='tree',
                                                    generation_duration=4, video_status='pending')])
        completed_before = copy.deepcopy(completed)
        client = client or Mock()
        client.config.max_wait_seconds = 10
        converter = Mock(side_effect=convert, return_value=('converted', 'hash'))
        freezer = Mock(side_effect=freeze or RuntimeError('unexpected freeze'))

        def read(_):
            return copy.deepcopy(record)

        def save(_, value):
            record.clear()
            record.update(copy.deepcopy(value))

        cloud.studio.ACTIVE.add(str(path))
        with ExitStack() as stack:
            stack.enter_context(patch.object(cloud, 'cloud_client_for', return_value=client))
            stack.enter_context(patch.object(cloud.studio, 'read', side_effect=read))
            stack.enter_context(patch.object(cloud.studio, 'save', side_effect=save))
            stack.enter_context(patch.object(cloud.studio, 'project_language_scope', return_value=nullcontext()))
            stack.enter_context(patch.object(cloud.studio, '_storyboard_image_path', return_value=path / 'core.jpg'))
            stack.enter_context(patch.object(vg, 'convert_for_h3', converter))
            stack.enter_context(patch.object(vg, '_freeze_local_request', freezer))
            cloud._worker(path, 1, ['s5'], {'s5': ({'h3_prompt_agent': True, 'resolution': 'custom'}, {}, (640, 384))},
                          event or threading.Event())
        self.assertEqual(record['shots'][0], completed_before)
        self.assertEqual((path / 'completed.mp4').read_bytes(), b'keep-completed-clip')
        self.assertNotIn(str(path), cloud.studio.ACTIVE)
        self.assertEqual(record['status'], 'video_review')
        self.assertFalse(record['shots'][1]['video_queued'])
        return record, client, converter, freezer

    def test_h3_http_500_is_pending_without_any_video_submission(self):
        error = RuntimeError('自定义兼容接口（高级） 调用失败: google/gemini-3.1-flash-lite-preview: HTTP 500')
        record, client, converter, freezer = self.run_worker(convert=error)
        shot = record['shots'][1]
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertFalse(shot['video_resume_available'])
        self.assertFalse(shot['video_terminal'])
        self.assertNotIn('video_request', shot)
        self.assertIn('语言模型渠道', shot['video_progress_message'])
        self.assertNotIn('继续查询', shot['video_progress_message'])
        self.assertIn('第 02 镜（s5）H3 提示词转换', record['error'])
        self.assertIn('HTTP 500', record['logs'][-1])
        converter.assert_called_once()
        freezer.assert_not_called()
        client.video_job.assert_not_called()
        client.video_asset.assert_not_called()
        client.video_quote.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_local_freeze_failure_is_not_a_remote_query_failure(self):
        record, client, _, freezer = self.run_worker(freeze=OSError('reference copy failed'))
        shot = record['shots'][1]
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertIn('本地请求准备', shot['video_error'])
        freezer.assert_called_once()
        client.video_job.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_actual_incomplete_h3_conversion_never_reaches_video_submission(self):
        from backend.app import h3_prompt_agent as agent

        def convert(shot, duration, **kwargs):
            return agent.convert_for_h3(shot, duration)

        with patch.object(agent, 'generate_gemini_text', return_value=json.dumps({
                'h3_prompt': 'subject_definitions: <Subject 1> is a liver with three lesions.'})) as ask:
            record, client, _, freezer = self.run_worker(convert=convert)
        self.assertEqual(ask.call_count, 3)
        shot = record['shots'][1]
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertIn('动作/阶段', shot['video_error'])
        self.assertIn('第 02 镜（s5）', record['error'])
        freezer.assert_not_called()
        client.video_asset.assert_not_called()
        client.video_quote.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_failed_conversion_after_explicit_regeneration_keeps_history(self):
        shot = dict(id='s5', kind='video', video_prompt='tree', generation_duration=4,
                    video_status='completed', video_backend='cloud_h3', video_attempt=2,
                    video='old.mp4', video_task_id='old-done', video_request={'client_job_id': 'old'})
        cloud.studio._invalidate_shot_video({'logs': []}, shot, '用户明确重新生成集群 H3 片段')
        history = copy.deepcopy(shot['video_history'])
        record, client, _, _ = self.run_worker(shot, convert=RuntimeError('HTTP 500'))
        result = record['shots'][1]
        self.assertEqual(result['video_status'], 'pending')
        self.assertTrue(result['video_not_submitted'])
        self.assertFalse(result['video_resume_available'])
        self.assertEqual(result['video_history'], history)
        self.assertEqual(result['video_attempt'], 2)
        client.video_job.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_cancel_during_conversion_stays_unsubmitted(self):
        event = threading.Event()
        def convert(*args, **kwargs):
            event.set()
            return 'converted', 'hash'
        record, client, _, freezer = self.run_worker(convert=convert, event=event)
        self.assertEqual(record['shots'][1]['video_status'], 'pending')
        self.assertTrue(record['shots'][1]['video_not_submitted'])
        freezer.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_preparation_failure_after_terminal_attempt_preserves_old_identity(self):
        shot = self.frozen(video_task_id='old-failed-job', video_prompt='tree', generation_duration=4)
        shot.update(video_terminal=True, video_execution_started=True)
        saved = copy.deepcopy(shot['video_request'])
        record, client, _, _ = self.run_worker(shot, convert=RuntimeError('HTTP 500'))
        result = record['shots'][1]
        self.assertEqual(result['video_status'], 'failed')
        self.assertTrue(result['video_terminal'])
        self.assertFalse(result['video_resume_available'])
        self.assertEqual(result['video_request'], saved)
        self.assertEqual(result['video_task_id'], 'old-failed-job')
        client.video_job.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_upload_failure_preserves_frozen_unsubmitted_identity(self):
        shot = self.frozen()
        shot['video_request'].pop('quote_id')
        shot['video_request']['reference_files'] = ['core.jpg']
        client = Mock()
        client.video_job.side_effect = CloudApiError('missing', status_code=404)
        client.video_asset.side_effect = CloudApiError('upload offline', status_code=503)
        record, _, converter, _ = self.run_worker(shot, client=client)
        result = record['shots'][1]
        self.assertEqual(result['video_status'], 'pending')
        self.assertTrue(result['video_not_submitted'])
        self.assertTrue(result['video_resume_available'])
        self.assertEqual(result['video_request']['client_job_id'], 'stable-client')
        self.assertIn('参考图上传', result['video_error'])
        converter.assert_not_called()
        client.create_video_job.assert_not_called()

    def test_lost_submit_response_remains_unknown_and_keeps_same_identity(self):
        client = Mock()
        client.video_job.side_effect = CloudApiError('missing', status_code=404)
        client.create_video_job.side_effect = TimeoutError('response lost after POST')
        record, _, converter, _ = self.run_worker(self.frozen(), client=client)
        shot = record['shots'][1]
        self.assertEqual(shot['video_status'], 'unknown')
        self.assertFalse(shot['video_not_submitted'])
        self.assertTrue(shot['video_execution_started'])
        self.assertTrue(shot['video_resume_available'])
        self.assertIn('继续查询原任务', shot['video_progress_message'])
        self.assertFalse(cloud.recover_unsubmitted(record))
        client.create_video_job.assert_called_once_with('saved-quote', 'stable-client')
        converter.assert_not_called()

    def test_old_uncertain_submission_does_not_lose_execution_evidence_after_404(self):
        shot = self.frozen()
        shot['video_execution_started'] = True
        shot['video_request'].pop('quote_id')
        shot['video_request']['reference_files'] = ['core.jpg']
        client = Mock()
        client.video_job.side_effect = CloudApiError('missing', status_code=404)
        client.video_asset.side_effect = CloudApiError('upload offline', status_code=503)
        record, _, _, _ = self.run_worker(shot, client=client)
        result = record['shots'][1]
        self.assertEqual(result['video_status'], 'unknown')
        self.assertTrue(result['video_execution_started'])
        self.assertFalse(result['video_not_submitted'])
        self.assertEqual(result['video_request']['client_job_id'], 'stable-client')
        client.create_video_job.assert_not_called()

    def test_existing_malformed_task_response_is_not_safe_to_regenerate(self):
        client = Mock()
        client.video_job.return_value = {'status': 'running'}
        record, _, _, _ = self.run_worker(self.frozen(), client=client)
        shot = record['shots'][1]
        self.assertEqual(shot['video_status'], 'unknown')
        self.assertFalse(shot['video_not_submitted'])
        self.assertTrue(shot['video_execution_started'])
        client.create_video_job.assert_not_called()

    def test_lookup_outage_before_first_submit_retains_safe_frozen_request(self):
        client = Mock()
        client.video_job.side_effect = CloudApiError('lookup offline', status_code=503)
        record, _, _, _ = self.run_worker(self.frozen(), client=client)
        shot = record['shots'][1]
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertTrue(shot['video_resume_available'])
        self.assertEqual(shot['video_request']['client_job_id'], 'stable-client')
        client.create_video_job.assert_not_called()

    def test_legacy_error_recovery_is_idempotent_and_preserves_content(self):
        shot = dict(id='s5', kind='video', video_status='unknown', video_error='HTTP 500',
                    video_resume_available=True, duration=8.64, image='core.png', video_prompt='original',
                    video_generation_options={'backend': 'cloud_h3'})
        record = dict(revision=7, shots=[shot], logs=[])
        self.assertTrue(cloud.recover_unsubmitted(record))
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertFalse(shot['video_resume_available'])
        self.assertEqual((shot['duration'], shot['image'], shot['video_prompt'], shot['video_error']),
                         (8.64, 'core.png', 'original', 'HTTP 500'))
        self.assertEqual(record['revision'], 8)
        before = copy.deepcopy(record)
        self.assertFalse(cloud.recover_unsubmitted(record))
        self.assertEqual(record, before)

    def test_uncertain_or_other_backend_states_are_never_migrated(self):
        for extra in [dict(video_task_id='remote'), dict(video_execution_started=True),
                      dict(video_state_file='state.json'), dict(video_attempt=1),
                      dict(video='old.mp4'), dict(video_request={'backend': 'cloud_h3', 'client_job_id': 'old'}),
                      dict(video_backend='api'), dict(video_status='completed')]:
            with self.subTest(extra=extra):
                shot = dict(id='s5', kind='video', video_status='unknown',
                            video_generation_options={'backend': 'cloud_h3'})
                shot.update(extra)
                record = dict(revision=1, shots=[shot], logs=[])
                before = copy.deepcopy(record)
                self.assertFalse(cloud.recover_unsubmitted(record))
                self.assertEqual(record, before)

    def test_restart_during_prompt_conversion_is_pending_not_unknown(self):
        shot = dict(id='s5', kind='video', video_status='running',
                    video_generation_options={'backend': 'cloud_h3'})
        record = dict(revision=1, shots=[shot], logs=[])
        vg.recover_interrupted(Path('.'), record)
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertFalse(shot['video_resume_available'])
        self.assertIn('尚未提交', shot['video_error'])

    def test_restart_after_switch_to_cloud_does_not_recover_old_local_backend(self):
        shot = dict(id='s5', kind='video', video_status='running', video_backend='comfyui',
                    video_attempt=2, video_execution_started=False,
                    video_generation_options={'backend': 'cloud_h3'})
        record = dict(revision=1, shots=[shot], logs=[])
        vg.recover_interrupted(Path('.'), record)
        self.assertEqual(shot['video_status'], 'pending')
        self.assertTrue(shot['video_not_submitted'])
        self.assertFalse(shot['video_terminal'])

    def test_project_read_repairs_legacy_preparation_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            record = dict(id='project', revision=1, status='video_review', logs=[],
                          shots=[dict(id='s5', kind='video', video_status='unknown',
                                      video_error='HTTP 500', video_resume_available=True,
                                      video_generation_options={'backend': 'cloud_h3'})])
            (path / 'record.json').write_text(json.dumps(record), encoding='utf-8')
            with patch.object(cloud, 'cloud_client_for') as client_factory:
                recovered = cloud.studio.read(path)
                again = cloud.studio.read(path)
            self.assertEqual(recovered['shots'][0]['video_status'], 'pending')
            self.assertEqual(again['revision'], recovered['revision'])
            client_factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
