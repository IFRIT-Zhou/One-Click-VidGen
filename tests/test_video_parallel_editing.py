import copy
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import video_generation as video
import test_shot_video_options as fixtures


class ParallelEditingTests(unittest.TestCase):
    setUp = fixtures.ShotVideoOptionsTests.setUp

    def running_project(self):
        self.record.update(status='video_generating', active_video_backend='api')
        self.record['shots'].append({'id': 'other', 'kind': 'video', 'video_status': 'running', 'video': 'old.mp4'})
        video.studio.ACTIVE.add(str(self.path))
        self.addCleanup(video.studio.ACTIVE.discard, str(self.path))

    def test_idle_shot_edit_preserves_running_shot_and_project_status(self):
        self.running_project()
        before = copy.deepcopy(self.record['shots'][1])
        video.studio.edit_shot_motion('test', 'a', video.studio.ShotMotionEdit(
            revision=1, kind='video', action='新的动作', video_prompt='新的提示词'), None)
        self.assertEqual(self.record['shots'][1], before)
        self.assertEqual(self.record['status'], 'video_generating')
        self.assertEqual(self.record['shots'][0]['video_prompt'], '新的提示词')

    def test_api_and_local_can_start_concurrently(self):
        self.running_project()
        with patch.dict(video.VIDEO_WORKERS, {str(self.path): {'api-worker': {'api:example.com'}}}, clear=True), patch.object(video, '_start_local_worker') as start:
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'], options={'backend':'comfyui','profile_id':'new','resolution':'720p'}), None)
        start.assert_called_once()
        self.assertFalse(self.record['shots'][0].get('video_waiting'))
        self.assertEqual(self.record['shots'][1]['video_status'], 'running')

    def test_worker_release_preserves_other_outlet_and_cancel_event(self):
        with patch.dict(video.VIDEO_WORKERS, {}, clear=True):
            one, event = video._register_video_worker(self.path, {'comfyui'})
            two, other = video._register_video_worker(self.path, {'api:example.com'})
            self.assertIs(event, other)
            video._release_video_worker(self.path, one)
            self.assertIn(str(self.path), video.studio.ACTIVE)
            self.assertIs(video.studio.CANCEL_EVENTS[str(self.path)], event)
            video._release_video_worker(self.path, two)
            self.assertNotIn(str(self.path), video.studio.ACTIVE)

    def test_api_outlet_normalizes_host_but_distinguishes_services(self):
        self.assertEqual(video._api_outlet({'base_url':'https://EXAMPLE.com/v1/'}), video._api_outlet({'base_url':'https://example.com/v1'}))
        self.assertNotEqual(video._api_outlet({'base_url':'https://a.com'}), video._api_outlet({'base_url':'https://b.com'}))

    def test_different_api_outlet_starts_while_original_runs(self):
        self.running_project()
        config = {'api_key':'test', 'resolution':'720p', 'base_url':'https://second.example/v1'}
        with patch.dict(video.VIDEO_WORKERS, {str(self.path): {'first': {'api:first.example/v1'}}}, clear=True), \
             patch.object(video, 'load_config', return_value=config), \
             patch.object(video, '_sync_identity', return_value=None), \
             patch.object(video, 'validate_request'), \
             patch.object(video, '_account_slots', return_value=['test']), \
             patch.object(video, '_config_for_shot', side_effect=lambda config, shot, index:config), \
             patch.object(video, '_freeze_request'), \
             patch.object(video, '_start_worker') as start:
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'], options={'backend':'api','resolution':'720p'}), None)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[3]['a']['base_url'], config['base_url'])
        self.assertEqual(self.record['shots'][1]['video_status'], 'running')

    def test_queued_and_running_shots_cannot_be_edited(self):
        self.running_project()
        for fields in ({'video_queued': True}, {'video_waiting': True}, {'video_status': 'running'}):
            shot = self.record['shots'][0]
            original = copy.deepcopy(shot)
            shot.update(fields)
            with self.assertRaises(HTTPException):
                video.studio.shot_editable(self.record, 1, 'a')
            shot.clear()
            shot.update(original)

    def test_other_project_local_running_id_does_not_lock_this_project(self):
        with patch.dict(video.LOCAL_VIDEO_RUNNING, {'G:/other-project': 'a'}, clear=True):
            video.studio.shot_editable(self.record, 1, 'a')

    def test_running_api_accepts_persisted_next_batch_without_generating(self):
        self.running_project()
        with patch.object(video, '_start_local_worker') as start:
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'],
                options={'backend': 'comfyui', 'profile_id': 'new', 'resolution': '720p'}), None)
        start.assert_not_called()
        self.assertTrue(self.record['shots'][0]['video_waiting'])
        self.assertEqual(self.record['video_waiting_queue'][0]['shot_options']['a']['resolution'], '720p')
        with self.assertRaises(HTTPException):
            video.generate('test', video.GenerateClips(revision=self.record['revision'], shot_ids=['a']), None)

    def test_cancel_waiting_unlocks_only_selected_shot(self):
        self.running_project()
        self.record['shots'][0]['video_waiting'] = True
        self.record['video_waiting_queue'] = [{'shot_ids': ['a'], 'shot_options': {'a': {'backend': 'comfyui'}}}]
        video.cancel_queued_shot('test', 'a', video.studio.Review(revision=1), None)
        self.assertEqual(self.record['video_waiting_queue'], [])
        video.studio.shot_editable(self.record, self.record['revision'], 'a')
        self.assertEqual(self.record['shots'][1]['video_status'], 'running')

    def test_cancel_cannot_win_after_worker_claims_shot(self):
        self.record['shots'][0]['video_queued'] = True
        with patch.dict(video.LOCAL_VIDEO_RUNNING, {str(self.path): 'a'}):
            with self.assertRaises(HTTPException):
                video.cancel_queued_shot('test', 'a', video.studio.Review(revision=1), None)
        self.assertTrue(self.record['shots'][0]['video_queued'])

    def test_cancelled_api_future_cannot_execute_a_newer_queue_entry(self):
        self.record['shots'][0]['video_queue_token'] = 'new'
        with patch.object(video, '_request') as request:
            self.assertTrue(video._process_api_clip(self.path, 'a', {'_queue_token': 'old'},
                                                   threading.Event(), threading.Event()))
        request.assert_not_called()
        self.assertEqual(self.record['shots'][0]['video_status'], 'pending')

    def test_restart_retains_not_started_work_without_submitting(self):
        shot = self.record['shots'][0]
        shot.update(video_queued=True, video_generation_options={'backend': 'comfyui', 'profile_id': 'new'})
        with patch.object(video, '_start_local_worker') as start:
            video.recover_interrupted(self.path, self.record)
        start.assert_not_called()
        self.assertFalse(shot['video_queued'])
        self.assertTrue(shot['video_waiting'])
        self.assertEqual(self.record['video_waiting_queue'][0]['shot_ids'], ['a'])

    def test_prompt_refresh_survives_other_shot_progress(self):
        self.running_project()
        self.record.update(context={}, references=[])
        self.record['shots'][0].update(action='旧动作', image_prompt='旧画面')
        def refresh(*args, **kwargs):
            self.record['revision'] += 2
            self.record['shots'][1]['video_progress_message'] = '正在采样'
            return {'action': '新动作', 'motion_plan': {}, 'video_prompt': '新视频', 'image_prompt': '新画面'}, None
        with patch('backend.app.video_prompt_refresh.refresh', side_effect=refresh), \
             patch.object(video.studio, 'project_language_scope'):
            video.studio.refresh_shot_prompts('test', 'a', video.studio.ShotPromptRefresh(
                revision=1, basis='action', action='新动作', image_prompt='旧画面'), None)
        self.assertEqual(self.record['shots'][0]['video_prompt'], '新视频')
        self.assertEqual(self.record['shots'][1]['video_progress_message'], '正在采样')
        self.assertEqual(self.record['status'], 'video_generating')

    def test_queue_claim_is_saved_before_launch_and_never_replayed(self):
        self.record['video_waiting_queue'] = [{'shot_ids': ['a'], 'revision': 1}]
        self.record['shots'][0]['video_waiting'] = True
        path = self.path / '1' / 'test'
        def uncertain(*args):
            self.assertEqual(self.record['video_waiting_queue'], [])
            raise RuntimeError('unknown submission result')
        with patch.object(video, '_generate_for_user', side_effect=uncertain) as submit:
            video._drain_video_waiting(path)
            video._drain_video_waiting(path)
        submit.assert_called_once()
        self.assertFalse(self.record['shots'][0]['video_waiting'])
        self.assertIn('队列提交暂停', self.record['error'])

    def test_changed_target_still_rejects_prompt_refresh(self):
        self.running_project()
        self.record.update(context={}, references=[])
        self.record['shots'][0].update(action='旧动作', image_prompt='旧画面')
        def refresh(*args, **kwargs):
            self.record['shots'][0]['action'] = '另一处编辑'
            return {'action': '覆盖', 'motion_plan': {}, 'video_prompt': '覆盖', 'image_prompt': '覆盖'}, None
        with patch('backend.app.video_prompt_refresh.refresh', side_effect=refresh), \
             patch.object(video.studio, 'project_language_scope'):
            with self.assertRaises(HTTPException):
                video.studio.refresh_shot_prompts('test', 'a', video.studio.ShotPromptRefresh(
                    revision=1, basis='action', action='新动作', image_prompt='旧画面'), None)
        self.assertEqual(self.record['shots'][0]['action'], '另一处编辑')


if __name__ == '__main__':
    unittest.main()
