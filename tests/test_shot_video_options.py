import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from backend.app import video_generation as video
from backend.app import comfyui_bridge as bridge


class ShotVideoOptionsTests(unittest.TestCase):
    def test_split_inherits_independent_core_image(self):
        image = self.path / 'source.jpg'
        Image.new('RGB', (16, 16), 'red').save(image)
        self.record.update(status='image_review', references=[], scenes=[
            {'slide_id': 's1', 'start': 0, 'end': 4, 'text': '前'},
            {'slide_id': 's2', 'start': 4, 'end': 8, 'text': '后'}], shots=[{
                'id': 'a', 'kind': 'video', 'slide_ids': ['s1', 's2'], 'image': 'source.jpg',
                'image_status': 'completed', 'video_status': 'pending', 'intent': '测试'}])
        video.studio.structure('test', video.studio.Structure(revision=1, action='split', index=0, boundary=1), None)
        left, right = self.record['shots']
        self.assertEqual(right['image_status'], 'completed')
        self.assertNotEqual(left['image'], right['image'])
        self.assertEqual((self.path / left['image']).read_bytes(), (self.path / right['image']).read_bytes())
        self.assertTrue(right['design_needs_review'])
        self.assertEqual(right['video_status'], 'pending')

    def test_bulk_confirmation_real_audit_and_atomic_failure(self):
        self.record.update(status='image_review', references=[], shots=[{
            'id': 'a', 'kind': 'video', 'image_status': 'completed', 'image': 'a.jpg',
            'video_status': 'pending', 'intent': '表达', 'image_prompt': '核心图',
            'action': '动作', 'video_prompt': '图1展示动作', 'duration': 6,
            'generation_duration': 6, 'design_needs_review': True}])
        with patch.object(video.studio.scene_references, 'enabled', return_value=False):
            self.record['shots'][0]['action'] = ''
            with self.assertRaises(HTTPException):
                video.studio.confirm_storyboard_images('test', video.studio.StoryboardConfirmation(
                    revision=1, confirm_adjusted_designs=True), None)
            self.assertTrue(self.record['shots'][0]['design_needs_review'])
            self.record['shots'][0]['action'] = '动作'
            video.studio.confirm_storyboard_images('test', video.studio.StoryboardConfirmation(
                revision=1, confirm_adjusted_designs=True), None)
        self.assertFalse(self.record['shots'][0]['design_needs_review'])
        self.assertEqual(self.record['shots'][0]['image'], 'a.jpg')
        self.assertEqual(self.record['status'], 'video_generation_ready')

    def test_single_confirmation_never_replaces_runtime_shots(self):
        self.record.update(status='image_review', scenes=[], references=[], shots=[
            {'id': 'a', 'design_needs_review': True, 'image': 'a.jpg', 'image_status': 'completed',
             'video': 'a.mp4', 'video_status': 'completed', 'video_generation_options': {'backend': 'comfyui'}},
            {'id': 'b', 'image': 'b.jpg', 'video': 'b.mp4', 'video_status': 'completed'}])
        before = copy.deepcopy(self.record['shots'])
        with patch.object(video.studio, 'normalize_shots', return_value=[{'id': 'a'}, {'id': 'b'}]), \
             patch('backend.app.video_agents.audit_storyboard'):
            video.studio.confirm_adjusted_design('test', video.studio.DesignConfirmation(revision=1, shot_id='a'), None)
        self.assertEqual(self.record['shots'][1], before[1])
        for key in ('image', 'image_status', 'video', 'video_status', 'video_generation_options'):
            self.assertEqual(self.record['shots'][0][key], before[0][key])
        self.assertTrue(self.record['shots'][0]['design_review_confirmed'])

    def test_explicit_project_confirmation_clears_adjusted_design_gate(self):
        self.record.update(status='image_review', shots=[{'id': 'b', 'kind': 'video',
            'image_status': 'completed', 'video_prompt': '图1动作', 'design_needs_review': True,
            'video_status': 'pending'}])
        with patch.object(video.studio.scene_references, 'enabled', return_value=False), \
             patch('backend.app.video_agents.audit_storyboard') as audit:
            video.studio.confirm_storyboard_images('test', video.studio.StoryboardConfirmation(
                revision=1, confirm_adjusted_designs=True), None)
        audit.assert_called_once()
        self.assertFalse(self.record['shots'][0]['design_needs_review'])
        self.assertTrue(self.record['shots'][0]['design_review_confirmed'])
        self.assertEqual(self.record['shots'][0]['video_status'], 'pending')
        self.assertEqual(self.record['status'], 'video_generation_ready')

    def test_project_reedit_confirmation_preserves_existing_videos(self):
        self.record.update(status='image_review', reedit_shot_id='a', reedit_return_status='video_review',
                           shots=[{'id': 'a', 'kind': 'video', 'image_status': 'completed',
                                   'video_status': 'completed', 'video': 'a.mp4', 'video_prompt': '图1动作'},
                                  {'id': 'b', 'kind': 'video', 'image_status': 'completed',
                                   'video_status': 'pending', 'video_prompt': '图1动作'}])
        with patch.object(video.studio.scene_references, 'enabled', return_value=False):
            video.studio.confirm_storyboard_images('test', video.studio.StoryboardConfirmation(revision=1), None)
        self.assertEqual(self.record['status'], 'video_generation_ready')
        self.assertEqual(self.record['shots'][0]['video'], 'a.mp4')
        self.assertEqual(self.record['shots'][0]['video_status'], 'completed')
        self.assertNotIn('reedit_shot_id', self.record)

    def test_project_confirmation_checks_all_adjusted_designs(self):
        self.record.update(status='image_review', shots=[{'id': 'b', 'kind': 'video',
            'image_status': 'completed', 'video_prompt': '图1动作', 'design_needs_review': True}])
        with patch.object(video.studio.scene_references, 'enabled', return_value=False):
            with self.assertRaises(HTTPException) as caught:
                video.studio.confirm_storyboard_images('test', video.studio.StoryboardConfirmation(revision=1), None)
        self.assertIn('设计', caught.exception.detail)
        self.assertEqual(self.record['status'], 'image_review')

    def test_manual_long_dynamic_shot_is_preserved_and_can_freeze_local_request(self):
        from backend.app.video_plan import normalize_shots
        scenes = [{'slide_id': 's', 'start': 0, 'end': 15.2, 'text': '长镜头'}]
        shot = normalize_shots([{'id': 'long', 'kind': 'video', 'slide_ids': ['s'],
                                 'video_prompt': '图1长镜头'}], scenes)[0]
        self.assertEqual(shot['kind'], 'video')
        self.assertEqual(shot['generation_duration'], 16)
        self.assertIn('建议', shot['warning'])
        self.record['shots'] = [shot]
        Image.new('RGB', (32, 32), 'white').save(self.path / 'core.jpg')
        video._freeze_local_request(self.path, self.record, shot, 'new',
                                    dimensions=(1344, 768))
        self.assertEqual(shot['video_request']['duration'], 16)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.record = {'id': 'test', 'revision': 1, 'status': 'video_review', 'logs': [],
                       'settings': {'ratio': '9:16'}, 'creation_parameters': {
                           'video_generation_backend': 'comfyui', 'comfyui_profile_id': 'old',
                           'comfyui_h3_prompt_agent': True},
                       'shots': [{'id': 'a', 'kind': 'video', 'duration': 5.2,
                                  'generation_duration': 6, 'video_prompt': '动作', 'video_status': 'pending'}]}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [('require_user', {'id': 1}), ('directory', self.path),
                            ('read', self.record), ('save', None), ('editable', None),
                            ('project_has_image_edits', False)]:
            self.stack.enter_context(patch.object(video.studio, name, return_value=value))
        self.stack.enter_context(patch.object(video, '_inputs', return_value=[self.path / 'core.jpg']))
        self.stack.enter_context(patch.object(video, 'video_profile', return_value={
            'id': 'new', 'name': '新工作流', 'resolution_preset': '1080p', 'mappings': {}}))

    def test_single_shot_overrides_project_without_changing_project_defaults(self):
        original = copy.deepcopy(self.record['creation_parameters'])
        with patch.object(video, '_start_local_worker') as start:
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'], options={
                'backend': 'comfyui', 'profile_id': 'new', 'resolution': '480p', 'h3_prompt_agent': False}), None)
        self.assertEqual(self.record['creation_parameters'], original)
        self.assertEqual(self.record['shots'][0]['video_generation_options']['resolution'], '480p')
        self.assertEqual(start.call_args.args[4], 'new')
        self.assertFalse(start.call_args.kwargs['use_h3_agent'])

    def test_apply_all_updates_dynamic_only_and_preserves_media(self):
        self.record['shots'] += [{'id':'b', 'kind':'video', 'video_status':'completed', 'video':'old.mp4'},
                                 {'id':'c', 'kind':'static', 'image':'still.png'}]
        static = copy.deepcopy(self.record['shots'][2])
        video.save_all_generation_options('test', video.SaveClipOptions(revision=1, options={
            'backend':'comfyui', 'profile_id':'new', 'resolution':'720p'}), None)
        self.assertEqual(self.record['shots'][0]['video_generation_options'], self.record['shots'][1]['video_generation_options'])
        self.assertEqual(self.record['shots'][1]['video'], 'old.mp4')
        self.assertEqual(self.record['shots'][2], static)

    def test_apply_all_rejects_unresolved_paid_task_atomically(self):
        self.record['shots'].append({'id':'b', 'kind':'video', 'video_status':'unknown', 'video_request':{'task':'paid'}})
        with self.assertRaises(HTTPException):
            video.save_all_generation_options('test', video.SaveClipOptions(revision=1, options={
                'backend':'comfyui', 'profile_id':'new', 'resolution':'720p'}), None)
        self.assertNotIn('video_generation_options', self.record['shots'][0])

    def test_switch_to_api_uses_requested_resolution_and_no_h3(self):
        with patch.object(video, 'load_config', return_value={'api_key': 'test', 'resolution': '720p'}), \
             patch.object(video, '_sync_identity', return_value=None), \
             patch.object(video, 'validate_request'), \
             patch.object(video, '_account_slots', return_value=['test']), \
             patch.object(video, '_config_for_shot', return_value={}), \
             patch.object(video, '_freeze_request') as freeze, \
             patch.object(video, '_start_worker'):
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'], options={
                'backend': 'api', 'resolution': '480p', 'h3_prompt_agent': True}), None)
        self.assertEqual(freeze.call_args.args[3]['resolution'], '480p')
        self.assertFalse(self.record['shots'][0]['video_generation_options']['h3_prompt_agent'])
        self.assertEqual(self.record['active_video_backend'], 'api')

    def test_unknown_paid_task_cannot_switch_provider(self):
        self.record['shots'][0].update(video_request={'resolution': '720p'}, video_backend='api',
                                       video_task_id='paid-task', video_terminal=False)
        with patch.object(video, '_start_local_worker') as start:
            with self.assertRaises(HTTPException) as failure:
                video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'], options={
                    'backend': 'comfyui', 'profile_id': 'new', 'resolution': '720p'}), None)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.record['shots'][0]['video_task_id'], 'paid-task')
        start.assert_not_called()

    def test_local_queue_executes_each_shots_own_profile_and_resolution(self):
        Image.new('RGB', (32, 32), 'white').save(self.path / 'core.jpg')
        self.record['shots'] = [dict(self.record['shots'][0], id=identity, video_generation_options={
            'backend': 'comfyui', 'profile_id': profile, 'resolution': resolution, 'h3_prompt_agent': False})
            for identity, profile, resolution in [('a', 'first', '480p'), ('b', 'second', 'custom')]]
        self.record['shots'][1]['video_generation_options'].update(width=960, height=1600)
        with patch.object(video.threading, 'Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])), \
             patch.object(video, 'run_video_profile') as run, \
             patch.object(video, '_video_version', return_value='version'):
            video._start_local_worker(self.path, self.record, ['a', 'b'], 1, 'old', use_h3_agent=True)
        self.assertEqual([call.args[1] for call in run.call_args_list], ['first', 'second'])
        self.assertEqual([call.kwargs['dimensions'] for call in run.call_args_list], [(480, 854), (960, 1600)])
        self.assertEqual([shot['video_request']['resolution'] for shot in self.record['shots']], ['480p', 'custom'])
        self.assertTrue(all(shot['video_status'] == 'completed' for shot in self.record['shots']))
        self.assertNotIn(str(self.path), video.studio.ACTIVE)

    def test_changed_settings_resume_original_engine_without_resubmitting(self):
        shot = self.record['shots'][0]
        shot.update(video_task_id='old-task', video='old.mp4', video_request={
            'profile_id': 'original-engine', 'resolution': '1080p',
            'width': 1920, 'height': 1080, 'prompt': 'original prompt',
            'duration': 11, 'ratio': '16:9', 'reference_files': ['core.jpg']},
            video_generation_options={'profile_id': 'new-engine', 'resolution': 'custom',
                                      'width': 1664, 'height': 936, 'h3_prompt_agent': True})
        with patch.object(video.threading, 'Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])), \
             patch.object(video, '_recover_local_prompt_id', return_value='old-task'), \
             patch.object(video, 'video_task_state', return_value='completed') as state, \
             patch.object(video, 'run_video_profile') as run, \
             patch.object(video, 'convert_for_h3') as convert, \
             patch.object(video, '_freeze_local_request') as freeze, \
             patch.object(video, '_video_version', return_value='version'):
            video._start_local_worker(self.path, self.record, ['a'], 1, 'new-engine')
        state.assert_called_once_with(1, 'old-task', 'original-engine')
        self.assertEqual(run.call_args.args[1], 'original-engine')
        self.assertEqual(run.call_args.kwargs['existing_prompt_id'], 'old-task')
        self.assertEqual(run.call_args.kwargs['dimensions'], (1920, 1080))
        convert.assert_not_called()
        freeze.assert_not_called()
        self.assertEqual(shot['video_status'], 'completed')
        self.assertEqual(shot['video_generation_options']['width'], 1664)

    def test_custom_dimensions_persist_and_missing_height_is_rejected(self):
        with patch.object(video, '_start_local_worker'):
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a'], options={
                'backend': 'comfyui', 'profile_id': 'new', 'resolution': 'custom',
                'width': 960, 'height': 1600}), None)
        self.assertEqual(self.record['shots'][0]['video_generation_options']['width'], 960)
        with self.assertRaises(HTTPException):
            video.generate('test', video.GenerateClips(revision=self.record['revision'], shot_ids=['a'], options={
                'backend': 'comfyui', 'profile_id': 'new', 'resolution': 'custom', 'width': 960}), None)

    def test_bridge_submits_frozen_portrait_dimensions(self):
        profile = {'resolution_preset': '1080p', 'workflow': {}, 'mappings': {}}
        connection = {'prompt_path': '/prompt', 'history_path': '/history/{prompt_id}', 'view_path': '/view'}
        response = SimpleNamespace(ok=True, json=lambda: {'prompt_id': 'new-task'})
        downloaded = SimpleNamespace(content=b'video', raise_for_status=lambda: None)
        with patch.object(bridge, 'video_profile', return_value=profile), \
             patch.object(bridge, '_connection', return_value=connection), \
             patch.object(bridge, '_url', side_effect=lambda conn, path: 'http://local'+path), \
             patch.object(bridge, '_upload_reference', return_value='core.jpg'), \
             patch.object(bridge, '_api_graph', return_value={}), \
             patch.object(bridge, '_patch_graph', return_value={}) as graph, \
             patch.object(bridge.requests, 'post', return_value=response), \
             patch.object(bridge.requests, 'get', return_value=downloaded), \
             patch.object(bridge, '_history_payload', return_value={'new-task': {'status': {}}}), \
             patch.object(bridge, '_find_output_file', return_value={'filename': 'clip.mp4'}):
            bridge.run_video_profile(1, 'profile', prompt='动作', image_path=self.path/'core.jpg',
                duration=6, ratio='9:16', output_path=self.path/'clip.mp4',
                resolution='custom', dimensions=(960, 1600))
        self.assertEqual((graph.call_args.kwargs['width'], graph.call_args.kwargs['height']), (960, 1600))
        self.assertEqual(profile['resolution_preset'], '1080p')

    def test_batch_preserves_each_saved_configuration(self):
        self.record['shots'] = [dict(self.record['shots'][0], id=key, video_generation_options={
            'backend': 'comfyui', 'profile_id': profile, 'resolution': resolution,
            'h3_prompt_agent': h3}) for key, profile, resolution, h3 in
            [('a', 'first', '480p', False), ('b', 'second', '720p', True)]]
        with patch.object(video, '_start_local_worker') as start:
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a', 'b']), None)
        self.assertEqual([s['video_generation_options']['profile_id'] for s in self.record['shots']], ['first', 'second'])
        self.assertEqual([s['video_generation_options']['resolution'] for s in self.record['shots']], ['480p', '720p'])
        self.assertEqual([s['video_generation_options']['h3_prompt_agent'] for s in self.record['shots']], [False, True])
        start.assert_called_once()

    def test_save_configuration_does_not_invalidate_existing_assets(self):
        self.record['shots'][0].update(video_status='completed', video='old.mp4',
                                       video_request={'backend': 'comfyui', 'profile_id': 'old'})
        self.record['export'] = {'raw': 'finished.mp4'}
        video.save_generation_options('test', 'a', video.SaveClipOptions(revision=1, options={
            'backend': 'comfyui', 'profile_id': 'new', 'resolution': '480p'}), None)
        self.assertEqual(self.record['shots'][0]['video'], 'old.mp4')
        self.assertEqual(self.record['shots'][0]['video_request']['profile_id'], 'old')
        self.assertEqual(self.record['export']['raw'], 'finished.mp4')
        self.assertEqual(self.record['revision'], 2)

    def test_mixed_batch_routes_api_and_local_separately(self):
        self.record['shots'].append(dict(self.record['shots'][0], id='b'))
        with patch.object(video, 'load_config', return_value={'api_key': 'test', 'resolution': '720p'}), \
             patch.object(video, '_sync_identity', return_value=None), \
             patch.object(video, 'validate_request'), \
             patch.object(video, '_account_slots', return_value=['test']), \
             patch.object(video, '_config_for_shot', side_effect=lambda config, shot, index: config), \
             patch.object(video, '_freeze_request') as freeze, \
             patch.object(video, '_start_local_worker') as start:
            video.generate('test', video.GenerateClips(revision=1, shot_ids=['a', 'b'], shot_options={
                'a': {'backend': 'api', 'resolution': '480p'},
                'b': {'backend': 'comfyui', 'profile_id': 'local', 'resolution': '720p'}}), None)
        self.assertEqual(self.record['active_video_backend'], 'mixed')
        self.assertEqual(freeze.call_args.args[2]['id'], 'a')
        self.assertEqual(start.call_args.kwargs['api_configs']['a']['resolution'], '480p')
        self.assertNotIn('b', start.call_args.kwargs['api_configs'])

    def test_all_static_images_can_confirm_and_export(self):
        from backend.app import video_export
        self.record.update(status='image_review', shots=[{'id': 'a', 'kind': 'static', 'image_status': 'completed'}])
        with patch.object(video.studio.scene_references, 'enabled', return_value=False):
            video.studio.confirm_storyboard_images('test', video.studio.StoryboardConfirmation(revision=1), None)
        self.assertEqual(self.record['status'], 'video_review')
        with patch.object(video_export.threading, 'Thread') as thread:
            try:
                video_export.start_export('test', video_export.ExportRequest(revision=2), None)
                thread.return_value.start.assert_called_once()
                self.assertEqual(self.record['status'], 'exporting')
            finally:
                video.studio.ACTIVE.discard(str(self.path))

    def test_post_generation_merge_retains_unaffected_assets(self):
        self.record.update(scenes=[], references=[], shots=[
            {'id': key, 'kind': 'video', 'slide_ids': [key], 'video_status': 'completed',
             'video': key+'.mp4', 'image': key+'.png', 'image_status': 'completed'}
            for key in ['a', 'b', 'c']])
        changed = [{'id': 'a', 'kind': 'video', 'slide_ids': ['a', 'b']},
                   {'id': 'c', 'kind': 'video', 'slide_ids': ['c']}]
        with patch.object(video.studio, 'edit_structure', return_value=changed):
            video.studio.structure('test', video.studio.Structure(revision=1, action='delete', index=1), None)
        self.assertEqual(self.record['status'], 'video_review')
        self.assertEqual(self.record['shots'][0]['image'], 'a.png')
        self.assertEqual(self.record['shots'][0]['video_status'], 'pending')
        self.assertEqual(self.record['shots'][1]['video'], 'c.mp4')
        self.assertEqual(self.record['manual_groups'][0]['slide_ids'], ['a', 'b'])
        video.studio.undo_structure('test', video.studio.Review(revision=self.record['revision']), None)
        self.assertEqual(self.record['status'], 'video_review')
        self.assertEqual([shot['id'] for shot in self.record['shots']], ['a', 'b', 'c'])
        self.assertEqual(self.record['shots'][0]['video'], 'a.mp4')
        self.assertEqual(self.record['shots'][1]['video'], 'b.mp4')
        self.assertNotIn('manual_groups', self.record)


if __name__ == '__main__':
    unittest.main()
