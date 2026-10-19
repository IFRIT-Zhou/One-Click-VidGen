"""Review/edit transitions must preserve the user's effective H3 execution route."""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.app import main, video_generation as video, video_studio as studio
from backend.app.video_duration_limits import max_video_duration
from backend.app.video_plan import normalize_shots


class H3ReviewRoutingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.record = dict(id='h3-route-test', revision=1, status='storyboard_review',
                           settings={'ratio': '16:9'}, logs=[], references=[],
                           creation_parameters={'video_generation_backend': 'api',
                                                'dynamic_max_shot_duration': 30})
        self.set_scenes([4, 4])
        self.saved = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(studio, 'require_user', return_value={'id': 1}))
        self.stack.enter_context(patch.object(studio, 'directory', return_value=self.path))
        self.stack.enter_context(patch.object(studio, 'read', return_value=self.record))
        self.stack.enter_context(patch.object(studio, 'save',
            side_effect=lambda path, record: self.saved.append(copy.deepcopy(record))))
        self.stack.enter_context(patch.object(studio, 'project_has_image_edits', return_value=False))
        self.stack.enter_context(patch.object(video, 'load_config', return_value={'protocol': 'runninghub_workflow'}))

    def set_scenes(self, durations):
        scenes, start = [], 0
        for index, seconds in enumerate(durations):
            scenes.append(dict(slide_id=f's{index}', start=start, end=start + seconds,
                               text=f'第{index}句配音保持完整。'))
            start += seconds
        self.record['scenes'] = scenes
        self.record['shots'] = normalize_shots([dict(id='shot', kind='video',
            slide_ids=[row['slide_id'] for row in scenes], intent='表达本段过程',
            image_prompt='核心场景', action='依次变化', video_prompt='参考图1展现过程')], scenes)

    def save_cloud_options(self):
        video.save_generation_options(self.record['id'], 'shot', video.SaveClipOptions(
            revision=self.record['revision'], options=dict(backend='cloud_h3', resolution='custom',
                                                         width=2048, height=1152)), None)
        return copy.deepcopy(self.record['shots'][0]['video_generation_options'])

    def test_saved_cloud_2k_options_survive_edit_and_review_with_api_project_default(self):
        options = self.save_cloud_options()
        incoming = copy.deepcopy(self.record['shots'])
        # The planning editor sends design fields, not generation options.
        incoming[0].pop('video_generation_options')
        incoming[0]['image_prompt'] = '修订后的核心场景'
        studio.update(self.record['id'], studio.Edit(revision=self.record['revision'], shots=incoming), None)
        self.assertEqual(self.record['shots'][0]['video_generation_options'], options)
        studio.review(self.record['id'], studio.Review(revision=self.record['revision']), None)
        self.assertEqual(self.record['shots'][0]['video_generation_options'], options)
        resolved = video._resolve_generation_options(self.record, self.record['shots'][0], 1)
        self.assertEqual((resolved['backend'], resolved['width'], resolved['height']), ('cloud_h3', 2048, 1152))
        self.assertEqual(self.record['creation_parameters']['video_generation_backend'], 'api')
        self.assertEqual(self.saved[-1]['status'], 'generation_ready')

    def test_cloud_sixteen_second_review_is_rejected_atomically_even_in_api_project(self):
        self.set_scenes([8, 8])
        self.save_cloud_options()
        before = copy.deepcopy(self.record)
        save_count = len(self.saved)
        with self.assertRaises(HTTPException) as failure:
            studio.review(self.record['id'], studio.Review(revision=self.record['revision']), None)
        self.assertEqual(failure.exception.status_code, 400)
        self.assertIn('14 秒上限', failure.exception.detail)
        self.assertEqual(self.record, before)
        self.assertEqual(len(self.saved), save_count)

    def test_explicit_api_shot_in_cloud_project_uses_personal_thirty_second_limit(self):
        self.set_scenes([15, 15])
        self.record['creation_parameters']['video_generation_backend'] = 'cloud_h3'
        video.save_generation_options(self.record['id'], 'shot', video.SaveClipOptions(
            revision=self.record['revision'], options=dict(backend='api', resolution='1080p')), None)
        studio.review(self.record['id'], studio.Review(revision=self.record['revision']), None)
        shot = self.record['shots'][0]
        self.assertEqual(shot['video_generation_options']['backend'], 'api')
        self.assertEqual(shot['generation_duration'], 30)
        self.assertEqual(shot['warning'], '')
        self.assertEqual(self.record['status'], 'generation_ready')
        self.assertEqual(self.record['creation_parameters']['video_generation_backend'], 'cloud_h3')

    def test_explicit_planning_optout_allows_review_but_not_cluster_submission(self):
        self.set_scenes([10, 10])
        self.save_cloud_options()
        self.record['creation_parameters'].update(video_generation_backend='cloud_h3',
            dynamic_max_shot_duration=30, dynamic_cluster_duration_recommended=False)
        studio.review(self.record['id'], studio.Review(revision=self.record['revision']), None)
        self.assertEqual(self.record['shots'][0]['generation_duration'], 20)
        self.assertEqual(len(studio._project_video_duration_blockers(self.record, self.record['shots'])), 1)
        before = copy.deepcopy(self.record)
        with patch('backend.app.cloud_video.handles', return_value=False):
            with self.assertRaises(HTTPException) as failure:
                video._generate_for_user(self.record['id'], video.GenerateClips(revision=self.record['revision'], shot_ids=['shot']), 1)
        self.assertIn('14 秒', failure.exception.detail)
        self.assertEqual(self.record, before)
        from backend.app.cloud_video import validate_duration
        with self.assertRaises(ValueError):
            validate_duration(self.record['shots'][0], {'max_duration_seconds': 30})

    def test_planning_preference_change_preserves_existing_paid_assets(self):
        self.record['shots'][0].update(image='paid.png', video='paid.mp4', video_status='completed')
        before = copy.deepcopy(self.record['shots'])
        result = studio.edit_project_settings(self.record['id'], studio.ProjectSettingsEdit(
            revision=self.record['revision'], name='保留素材', video_generation_backend='cloud_h3',
            dynamic_text_mode=studio.normalize_text_mode(self.record['creation_parameters'].get('dynamic_text_mode')),
            parameters={'dynamic_max_shot_duration': 28, 'dynamic_cluster_duration_recommended': False}), None)
        self.assertEqual(result['shots'], before)
        self.assertEqual(result['creation_parameters']['dynamic_max_shot_duration'], 28)
        self.assertFalse(result['creation_parameters']['dynamic_cluster_duration_recommended'])

    def test_split_copies_cloud_configuration_to_both_child_shots(self):
        options = self.save_cloud_options()
        original_scenes = copy.deepcopy(self.record['scenes'])
        studio.structure(self.record['id'], studio.Structure(revision=self.record['revision'],
                                                             action='split', index=0, boundary=1), None)
        self.assertEqual(len(self.record['shots']), 2)
        self.assertEqual(self.record['scenes'], original_scenes)
        for shot in self.record['shots']:
            self.assertEqual(shot['video_generation_options'], options)
            self.assertEqual(shot['generation_duration'], 4)
        self.assertIsNot(self.record['shots'][0]['video_generation_options'],
                         self.record['shots'][1]['video_generation_options'])

    def test_confirm_adjusted_design_preserves_route_and_existing_media(self):
        self._check_confirmation_preserves_media()

    def test_split_rechecks_children_and_clears_old_duration_repair(self):
        self.set_scenes([2.4, 12.48])
        self.save_cloud_options()
        self.record['status'] = 'video_review'
        (self.path / 'core.png').write_bytes(b'original image')
        self.record['shots'][0].update(image='core.png', image_status='completed',
            video_status='failed', video_request={'id': 'old'}, video_task_id='old',
            duration_repair={'method': 'boundary_fallback', 'note': 'old overlimit'})
        original_scenes = copy.deepcopy(self.record['scenes'])
        studio.structure(self.record['id'], studio.Structure(revision=self.record['revision'],
            action='split', index=0, boundary=1), None)
        self.assertEqual(self.record['scenes'], original_scenes)
        self.assertEqual(self.record['duration_check'], {'passed': True, 'blockers': []})
        self.assertEqual([s['generation_duration'] for s in self.record['shots']], [4, 13])
        for shot in self.record['shots']:
            self.assertNotIn('duration_repair', shot)
            self.assertFalse(shot.get('video_request'))
            self.assertFalse(shot.get('video_task_id'))
            self.assertEqual(shot['warning'], '')
            self.assertEqual((self.path / shot['image']).read_bytes(), b'original image')
        studio._validate_project_video_durations(self.record, self.record['shots'])

    def test_split_reports_remaining_overlimit_child(self):
        self.set_scenes([2, 16])
        self.save_cloud_options()
        studio.structure(self.record['id'], studio.Structure(revision=self.record['revision'],
            action='split', index=0, boundary=1), None)
        check = self.record['duration_check']
        self.assertFalse(check['passed'])
        self.assertEqual([r['index'] for r in check['blockers']], [2])
        self.assertIn('第 02 镜（16 秒）', check['blockers'][0]['message'])

    def test_review_lists_every_overlimit_shot_with_ordinal_and_duration(self):
        self.set_scenes([6.3, 3.7, 5.9, 4, 14.53, 5, 14.65])
        self.record['creation_parameters']['video_generation_backend'] = 'cloud_h3'
        self.record['shots'] = normalize_shots([dict(id=f'shot-{index}', kind='video',
            slide_ids=[scene['slide_id']], intent='表达本段过程', image_prompt='核心场景',
            action='依次变化', video_prompt='参考图1展现过程')
            for index, scene in enumerate(self.record['scenes'])], self.record['scenes'])
        before = copy.deepcopy(self.record)
        with self.assertRaises(HTTPException) as failure:
            studio.review(self.record['id'], studio.Review(revision=self.record['revision']), None)
        message = failure.exception.detail
        self.assertIn('第 05 镜（14.53 秒）', message)
        self.assertIn('第 07 镜（14.65 秒）', message)
        self.assertEqual(message.count('请求需要 15 秒'), 2)
        self.assertEqual(message.count('14 秒上限'), 2)
        self.assertNotIn('第 01 镜', message)
        self.assertEqual(self.record, before)
        self.assertFalse(self.saved)
        with self.assertRaisesRegex(ValueError, '第 07 镜'):
            studio._validate_project_video_durations(self.record, [self.record['shots'][6]])

    def test_duration_report_respects_saved_route_and_static_shots(self):
        self.record['creation_parameters']['video_generation_backend'] = 'cloud_h3'
        shots = [dict(id='static', kind='static', duration=20),
                 dict(id='api', kind='video', duration=20, generation_duration=20,
                      video_generation_options={'backend':'api'}),
                 dict(id='cloud', kind='video', duration=14.001, generation_duration=15)]
        self.record['shots'] = shots
        blockers = studio._project_video_duration_blockers(self.record, shots)
        self.assertEqual([row['index'] for row in blockers], [3])
        self.assertIn('14.001 秒', blockers[0]['message'])
        shots[2].update(duration=14, generation_duration=14)
        self.assertEqual(studio._project_video_duration_blockers(self.record, shots), [])

    def _check_confirmation_preserves_media(self):
        options = self.save_cloud_options()
        self.record['shots'][0].update(design_needs_review=True, image='original.png',
                                       video='original.mp4', video_status='completed')
        studio.confirm_adjusted_design(self.record['id'], studio.DesignConfirmation(
            revision=self.record['revision'], shot_id='shot'), None)
        shot = self.record['shots'][0]
        self.assertEqual(shot['video_generation_options'], options)
        self.assertEqual((shot['image'], shot['video']), ('original.png', 'original.mp4'))
        self.assertTrue(shot['design_review_confirmed'])
        self.assertFalse(shot['design_needs_review'])

    def test_confirm_adjusted_cloud_design_also_rejects_overlimit_without_mutation(self):
        self.set_scenes([8, 8])
        self.save_cloud_options()
        self.record['shots'][0]['design_needs_review'] = True
        before = copy.deepcopy(self.record)
        with self.assertRaises(HTTPException) as failure:
            studio.confirm_adjusted_design(self.record['id'], studio.DesignConfirmation(
                revision=self.record['revision'], shot_id='shot'), None)
        self.assertIn('14 秒上限', failure.exception.detail)
        self.assertEqual(self.record, before)


class H3StepWorkflowRoutingTests(unittest.TestCase):
    def test_start_visual_preserves_route_and_overrides_legacy_ten_second_limit(self):
        job = SimpleNamespace(id='h3-step', user_id=1, status='waiting_confirmation', message='',
            request=dict(step_mode=True, _step_workflow_version=2, _step_mode_stage='visual_setup',
                         video_generation_backend='api', dynamic_max_shot_duration=30,
                         use_cloud_image_pool=False, image_profile_id=''))
        parameters = dict(video_generation_backend='cloud_h3', dynamic_max_shot_duration=10,
                          comfyui_profile_id='h3-fast', comfyui_h3_prompt_agent=True,
                          comfyui_reference_audio=True, dynamic_text_mode='text_assisted')
        snapshots = []
        with patch.object(main, 'require_user', return_value={'id': 1}), \
             patch.object(main.store, 'get', return_value=job), \
             patch.object(main.store, 'update', side_effect=lambda job, **fields: snapshots.append(copy.deepcopy(fields))), \
             patch.object(main.store, 'log'), \
             patch.object(main, 'persist_step_workflow_state'), \
             patch.object(main, 'validate_step_audio_snapshot', return_value={'sentence_count': 2}), \
             patch.object(main, '_required_job_config_error', return_value=''), \
             patch.object(main.store, 'advance_step_workflow', return_value={'stage': 'visual_running'}) as advance, \
             patch.object(main, 'cloud_client_for') as cloud, \
             patch.object(main.tts_editor, 'regenerate') as regenerate:
            result = main.advance_step_workflow('h3-step', main.StepWorkflowAdvanceRequest(
                action='start_visual', parameters=parameters), None)
        self.assertEqual(result['stage'], 'visual_running')
        advance.assert_called_once_with(job, 'start_visual')
        for key, expected in parameters.items():
            self.assertEqual(job.request[key], expected)
            self.assertEqual(snapshots[-1]['request'][key], expected)
        self.assertEqual(max_video_duration(snapshots[-1]['request']), 14)
        cloud.assert_not_called()
        regenerate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
