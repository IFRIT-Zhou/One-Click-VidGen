import io
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import video_studio


class VideoStudioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = patch.object(video_studio, 'ROOT', Path(self.temp.name))
        self.auth = patch.object(video_studio, 'require_user', return_value={'id': 'test-user'})
        self.root.start()
        self.auth.start()
        app = FastAPI()
        app.include_router(video_studio.router)
        self.client = TestClient(app)
        response = self.client.post('/api/video-studio', json={
            'srt': '1\n00:00:00,000 --> 00:00:02,000\n甲\n\n2\n00:00:02,000 --> 00:00:04,000\n乙',
            'scene_references_enabled': False})
        self.assertEqual(response.status_code, 200)
        self.project = response.json()
        self.url = '/api/video-studio/' + self.project['id']

    def tearDown(self):
        self.client.close()
        self.auth.stop()
        self.root.stop()
        self.temp.cleanup()

    def test_failed_planning_recovers_editable_checkpoint(self):
        from backend.app.video_plan import normalize_shots
        shots = normalize_shots([{'slide_ids': ['scene_001', 'scene_002'],
            'kind': 'static', 'intent': '测试', 'visual_description': '已完成的核心场面'}], self.project['scenes'])
        def failing(*args, checkpoint=None, **kwargs):
            checkpoint({'summary': '已理解全文'}, shots, '核心画面设计')
            raise ValueError('模拟后续 Agent 失败')
        with patch.object(video_studio, 'plan_storyboard', side_effect=failing):
            self.assertEqual(self.client.post(self.url + '/plan').status_code, 200)
            deadline = time.monotonic() + 5
            while video_studio.ACTIVE and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(video_studio.ACTIVE)
        record = self.client.get(self.url).json()
        self.assertEqual(record['status'], 'storyboard_review')
        self.assertEqual(record['shots'], shots)
        self.assertIn('核心画面设计', record['planning_recovery'])
        self.assertIn('模拟', record['error'])
        record['shots'][0]['intent'] = '用户修改'
        result = self.client.put(self.url, json={'revision': record['revision'], 'shots': record['shots']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['shots'][0]['intent'], '用户修改')

    def test_text_mode_survives_create_reload_and_planning_parameters(self):
        for mode in ('visual_first', 'text_assisted'):
            response = self.client.post('/api/video-studio', json={
                'srt': '1\n00:00:00,000 --> 00:00:02,000\n点餐。', 'dynamic_text_mode': mode})
            self.assertEqual(response.status_code, 200, response.text)
            record = self.client.get('/api/video-studio/' + response.json()['id']).json()
            self.assertEqual(record['settings']['dynamic_text_mode'], mode)
            self.assertEqual(record['creation_parameters']['dynamic_text_mode'], mode)
            self.assertEqual(video_studio.planning_parameters(record)['dynamic_text_mode'], mode)
        invalid = self.client.post('/api/video-studio', json={
            'srt': '1\n00:00:00,000 --> 00:00:02,000\n点餐。', 'dynamic_text_mode': 'unknown'})
        self.assertEqual(invalid.status_code, 422)

    def test_legacy_parameters_do_not_gain_a_new_fingerprint_input(self):
        legacy = {'creation_parameters': {'director_strategy': 'stable'}, 'settings': {}}
        self.assertEqual(video_studio.planning_parameters(legacy), {'director_strategy': 'stable'})

    def test_recovery_never_overwrites_existing_shots(self):
        original = [{'id': 'original', 'intent': '手动修改'}]
        record = dict(self.project, shots=original, planning_checkpoint={
            'shots': [{'id': 'new'}], 'stage': '核心画面设计'})
        video_studio.recover_planning(record)
        self.assertEqual(record['shots'], original)

    def test_interrupted_planning_loads_checkpoint(self):
        record = dict(self.project, status='planning', planning_checkpoint={
            'shots': [{'id': 'saved'}], 'context': {}, 'stage': '镜头划分'})
        path = video_studio.directory('test-user', record['id'])
        video_studio.save(path, record)
        recovered = video_studio.read(path)
        self.assertEqual(recovered['shots'][0]['id'], 'saved')
        self.assertEqual(recovered['status'], 'storyboard_review')

    def test_save_revision_and_structure(self):
        response = self.client.put(self.url, json={'revision': 1, 'shots': [
            {'slide_ids': ['scene_001', 'scene_002'], 'intent': '测试'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revision'], 2)
        self.assertEqual(self.client.put(self.url, json={'revision': 1, 'shots': []}).status_code, 409)
        response = self.client.post(self.url + '/structure', json={
            'revision': 2, 'action': 'split', 'index': 0, 'boundary': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['shots']), 2)
        self.assertEqual(len(self.client.get(self.url).json()['shots']), 2)

    def test_structure_retains_design_locks_groups_and_undo_restores(self):
        response = self.client.put(self.url, json={'revision': 1, 'shots': [
            {'slide_ids': ['scene_001', 'scene_002'], 'kind': 'video', 'intent': '测试',
             'action': '动作', 'image_prompt': '画面', 'video_prompt': '图1动作'}]})
        self.assertEqual(response.status_code, 200)
        original = response.json()
        record = self.client.post(self.url+'/structure', json={
            'revision': original['revision'], 'action': 'split', 'index': 0, 'boundary': 1}).json()
        self.assertTrue(record['manual_groups'])
        self.assertEqual(record['shots'][0]['image_prompt'], '画面')
        self.assertTrue(record['shots'][0]['design_needs_review'])
        self.assertEqual(self.client.post(self.url+'/review', json={'revision': record['revision']}).status_code, 400)
        record['shots'][0]['design_needs_review'] = False
        saved = self.client.put(self.url, json={'revision': record['revision'], 'shots': record['shots']})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()['shots'][0]['design_needs_review'])
        undone = self.client.post(self.url+'/structure/undo', json={'revision': saved.json()['revision']})
        self.assertEqual(undone.status_code, 200)
        self.assertEqual(undone.json()['shots'], original['shots'])
        self.assertFalse(undone.json().get('manual_groups'))

    def test_manual_prompt_edit_clears_only_its_stale_warnings(self):
        from backend.app.video_plan import normalize_shots
        record = dict(self.project, status='storyboard_review')
        record['shots'] = normalize_shots([{'id': 'shot1', 'kind': 'video',
            'slide_ids': ['scene_001', 'scene_002'], 'image_prompt': '原图提示词',
            'video_prompt': '原视频提示词', 'image_prompt_warnings': ['旧图核对提示'],
            'video_prompt_warnings': ['视频核对提示']}], record['scenes'])
        video_studio.save(video_studio.directory('test-user', record['id']), record)
        record['shots'][0]['image_prompt'] = '用户修改后的图提示词'
        response = self.client.put(self.url, json={'revision': record['revision'], 'shots': record['shots']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['shots'][0]['image_prompt_warnings'], [])
        self.assertEqual(response.json()['shots'][0]['video_prompt_warnings'], ['视频核对提示'])

    def test_reopen_one_shot_preserves_paid_clip_until_actual_edit(self):
        record = dict(self.project, status='completed', export={'subtitles':'old.mp4'})
        record['shots'] = [dict(id='first', kind='video', duration=5, start=0, end=5,
            slide_ids=['scene_001'], action='旧动作', video_prompt='旧视频提示',
            image_prompt='核心图', image_status='completed', video_status='completed',
            video='assets/videos/first/clip.mp4', video_version='abc',
            video_request={'prompt':'旧视频提示'}, video_attempt=1)]
        path = video_studio.directory('test-user', record['id'])
        clip = path / 'assets' / 'videos' / 'first' / 'clip.mp4'
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b'paid-video')
        video_studio.save(path, record)
        reopened = self.client.post(self.url+'/shots/first/reopen', json={'revision':record['revision']})
        self.assertEqual(reopened.status_code, 200, reopened.text)
        result = reopened.json()
        self.assertEqual(result['status'], 'image_review')
        self.assertEqual(result['shots'][0]['video_status'], 'completed')
        self.assertEqual(result['shots'][0]['video_version'], 'abc')
        self.assertEqual(result['export'], {'subtitles':'old.mp4'})
        unchanged = self.client.post(self.url+'/images/confirm', json={'revision':result['revision']})
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(unchanged.json()['status'], 'completed')
        reopened = self.client.post(self.url+'/shots/first/reopen', json={
            'revision':unchanged.json()['revision']})
        result = reopened.json()
        edited = self.client.post(self.url+'/shots/first/motion', json={
            'revision':result['revision'], 'kind':'video', 'action':'新动作',
            'video_prompt':'新视频提示'})
        self.assertEqual(edited.status_code, 200, edited.text)
        changed = edited.json()
        self.assertEqual(changed['shots'][0]['video_status'], 'pending')
        self.assertTrue(changed['shots'][0]['video_not_submitted'])
        self.assertNotIn('video', changed['shots'][0])
        self.assertNotIn('export', changed)
        self.assertEqual(changed['shots'][0]['video_history'][-1]['video_version'], 'abc')

    def test_reopen_rejects_active_or_pre_video_stage(self):
        record = dict(self.project, status='storyboard_review', shots=[
            dict(id='first', kind='video', duration=5)])
        path = video_studio.directory('test-user', record['id'])
        video_studio.save(path, record)
        body = {'revision':record['revision']}
        self.assertEqual(self.client.post(self.url+'/shots/first/reopen', json=body).status_code, 409)
        record['status'] = 'video_generating'
        video_studio.save(path, record)
        self.assertEqual(self.client.post(self.url+'/shots/first/reopen', json=body).status_code, 409)

    def test_unchanged_reedit_can_explicitly_reroll_only_current_video(self):
        record = dict(self.project, status='completed', export={'subtitles':'old.mp4'})
        record['shots'] = [dict(id='first', kind='video', duration=5, start=0, end=5,
            slide_ids=['scene_001'], action='原动作', video_prompt='原视频提示',
            image_prompt='核心图', image_status='completed', video_status='completed',
            video='assets/videos/first/clip.mp4', video_version='abc',
            video_request={'prompt':'原视频提示'}, video_attempt=1)]
        path = video_studio.directory('test-user', record['id'])
        clip = path / 'assets' / 'videos' / 'first' / 'clip.mp4'
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b'paid-video')
        video_studio.save(path, record)
        reopened = self.client.post(self.url+'/shots/first/reopen', json={'revision':record['revision']}).json()
        rerolled = self.client.post(self.url+'/images/confirm', json={
            'revision':reopened['revision'], 'regenerate_shot_id':'first'})
        self.assertEqual(rerolled.status_code, 200, rerolled.text)
        result = rerolled.json()
        self.assertEqual(result['status'], 'video_generation_ready')
        self.assertEqual(result['shots'][0]['video_status'], 'pending')
        self.assertTrue(result['shots'][0]['video_not_submitted'])
        self.assertNotIn('export', result)
        self.assertEqual(result['shots'][0]['video_history'][-1]['video_version'], 'abc')
        self.assertIn('素材与提示词不变', result['logs'][-2])

    def test_image_review_kind_switch_preserves_assets_and_other_shots(self):
        from backend.app.video_plan import normalize_shots
        record = dict(self.project, status='image_review')
        record['shots'] = normalize_shots([
            {'id':'first','slide_ids':['scene_001'],'kind':'static','image_prompt':'原核心图'},
            {'id':'second','slide_ids':['scene_002'],'kind':'static','image_prompt':'另一图'}], record['scenes'])
        record['shots'][0].update(image='first.png', image_status='completed')
        path = video_studio.directory('test-user', record['id'])
        video_studio.save(path, record)
        response = self.client.post(self.url+'/shots/first/motion', json={
            'revision':record['revision'], 'kind':'video'})
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated['status'], 'image_review')
        self.assertEqual(updated['shots'][1], record['shots'][1])
        for key in ('image', 'image_status', 'image_prompt', 'slide_ids', 'start', 'end', 'source_subtitles'):
            self.assertEqual(updated['shots'][0][key], record['shots'][0][key])
        self.assertEqual(updated['shots'][0]['generation_duration'], 4)
        self.assertEqual(self.client.get(self.url).json()['shots'][0]['kind'], 'video')
        self.assertEqual(self.client.post(self.url+'/shots/first/motion', json={
            'revision':record['revision'], 'kind':'static'}).status_code, 409)
        saved = self.client.post(self.url+'/shots/first/motion', json={
            'revision':updated['revision'], 'kind':'video', 'action':'小人抬手', 'video_prompt':'抬手再转身'})
        self.assertEqual(saved.json()['shots'][0]['video_prompt'], '抬手再转身')

    def test_motion_edit_rejects_overlong_shot_and_later_stage(self):
        record = dict(self.project, status='image_review', shots=[
            dict(id='first', kind='static', duration=16, image_prompt='原图')])
        path = video_studio.directory('test-user', record['id'])
        video_studio.save(path, record)
        body = {'revision':record['revision'], 'kind':'video'}
        self.assertEqual(self.client.post(self.url+'/shots/first/motion', json=body).status_code, 400)
        record['status'] = 'video_generation_ready'
        video_studio.save(path, record)
        self.assertEqual(self.client.post(self.url+'/shots/first/motion', json=body).status_code, 409)

    def test_confirm_images_reports_new_dynamic_shot_without_prompt(self):
        record = dict(self.project, status='image_review', shots=[
            dict(id='first', kind='video', duration=8, image_status='completed', video_prompt='')])
        video_studio.save(video_studio.directory('test-user', record['id']), record)
        response = self.client.post(self.url+'/images/confirm', json={'revision':record['revision']})
        self.assertEqual(response.status_code, 409)
        self.assertIn('第 1 镜', response.json()['detail'])

    def test_single_shot_prompt_refresh_never_changes_other_shots(self):
        from backend.app.video_plan import normalize_shots
        record = dict(self.project, status='image_review',
                      context={'summary': '测试', 'video_direction': {'dynamic_text_mode': 'text_assisted'}},
                      creation_parameters={**self.project['creation_parameters'], 'dynamic_text_mode': 'visual_first'})
        record['shots'] = normalize_shots([
            {'id':'first','slide_ids':['scene_001'],'kind':'video','intent':'甲','action':'甲动作',
             'image_prompt':'甲图','video_prompt':'甲视频'},
            {'id':'second','slide_ids':['scene_002'],'kind':'video','intent':'乙','action':'乙动作',
             'image_prompt':'乙图','video_prompt':'乙视频'}], record['scenes'])
        video_studio.save(video_studio.directory('test-user',record['id']),record)
        untouched=copy.deepcopy(record['shots'][1])
        revised=copy.deepcopy(record['shots'][0])
        revised.update(action='用户新动作',motion_plan={'version':2},image_prompt='新甲图',video_prompt='新甲视频')
        with patch('backend.app.video_prompt_refresh.refresh',return_value=(revised,None)) as refresh:
            response=self.client.post(self.url+'/shots/first/refresh-prompts',json={
                'revision':record['revision'],'basis':'action','action':'用户新动作','image_prompt':'甲图'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(refresh.call_args.args[0]['video_direction']['dynamic_text_mode'], 'visual_first')
        self.assertEqual(record['context']['video_direction']['dynamic_text_mode'], 'text_assisted')
        result=response.json()
        self.assertEqual(result['shots'][0]['image_prompt'],'新甲图')
        self.assertEqual(result['shots'][0]['video_prompt'],'新甲视频')
        self.assertEqual(result['shots'][1],untouched)

    def test_reopen_audio_task_reuses_saved_plan_without_import_or_llm(self):
        record = dict(self.project, source_project={'id': 'audio-job'},
                      creation_parameters={'dynamic_video': True},
                      shots=[{'id': 'saved-shot', 'intent': '用户已修改的表达'}])
        video_studio.save(video_studio.directory('test-user', record['id']), record)
        empty = dict(record, id='a' * 32, shots=[])
        video_studio.save(video_studio.directory('test-user', empty['id']), empty)
        with patch.object(video_studio, 'source_project') as source, patch.object(video_studio, 'plan_storyboard') as planner:
            found = self.client.get('/api/video-studio/by-audio-task/audio-job').json()['project']
            self.assertEqual(found['id'], record['id'])
            for _ in range(2):
                response = self.client.post('/api/video-studio/from-project', json={
                    'job_id': 'audio-job', 'from_audio_task': True})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['shots'], record['shots'])
                self.assertEqual(response.json()['id'], record['id'])
            source.assert_not_called()
            planner.assert_not_called()
        with patch.object(video_studio, 'require_user', return_value={'id': 'other-user'}):
            self.assertIsNone(self.client.get('/api/video-studio/by-audio-task/audio-job').json()['project'])

    def test_invalid_coverage_does_not_replace_record(self):
        response = self.client.put(self.url, json={'revision': 1, 'shots': [
            {'slide_ids': ['scene_001']}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.url).json()['revision'], 1)

    def test_review_gate_requires_complete_video_contract(self):
        shot = {'id': 'shot1', 'slide_ids': ['scene_001', 'scene_002'], 'kind': 'video',
                'intent': '表达变化', 'action': '人物先观察再举手',
                'image_prompt': '【人物与画风】简笔画\n【画面内容】人物在讲台\n【必要限制】无文字',
                'video_prompt': '参考图1的人物、画风和讲台。人物先观察再举手，静音。'}
        saved = self.client.put(self.url, json={'revision': 1, 'shots': [shot]}).json()
        reviewed = self.client.post(self.url + '/review', json={'revision': saved['revision']})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()['status'], 'generation_ready')
        self.assertTrue(reviewed.json()['shots'][0]['audit']['ready'])
        changed = dict(shot, video_prompt='不含核心图号')
        saved_again = self.client.put(self.url, json={
            'revision': reviewed.json()['revision'], 'shots': [changed]}).json()
        self.assertEqual(saved_again['status'], 'storyboard_review')
        failed = self.client.post(self.url + '/review', json={'revision': saved_again['revision']})
        self.assertEqual(failed.status_code, 400)
        self.assertIn('图1核心分镜', failed.text)

    def test_confirmed_storyboard_can_generate_and_confirm_core_images(self):
        shot = {'id': 'shot1', 'slide_ids': ['scene_001', 'scene_002'], 'kind': 'static',
                'intent': '表达主题', 'action': '',
                'image_prompt': '【本图旨在】表达主题\n【人物与画风】简笔画\n【画面内容】讲台场景\n【必要限制】无文字',
                'video_prompt': ''}
        saved = self.client.put(self.url, json={'revision': 1, 'shots': [shot]}).json()
        reviewed = self.client.post(self.url + '/review', json={'revision': saved['revision']}).json()
        with patch.object(video_studio, '_image_configs', return_value=[{'api_key': 'test'}]), \
             patch.object(video_studio, '_start_storyboard_images') as start:
            response = self.client.post(self.url + '/images/generate', json={'revision': reviewed['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'image_generating')
        start.assert_called_once()
        record = self.client.get(self.url).json()
        record['shots'][0].update(image_status='completed', image='assets/storyboards/shot1.jpg')
        record['status'] = 'image_review'
        video_studio.save(video_studio.directory('test-user', record['id']), record)
        confirmed = self.client.post(self.url + '/images/confirm', json={'revision': record['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()['status'], 'video_generation_ready')

    def test_storyboard_images_use_configured_accounts_in_parallel(self):
        path = video_studio.directory('test-user', self.project['id'])
        record = self.client.get(self.url).json()
        record['shots'] = [
            {'id': f'shot{i}', 'image_prompt': f'prompt{i}', 'reference_image_ids': []}
            for i in range(1, 4)
        ]
        record['logs'] = []
        active = 0
        peak = 0
        guard = threading.Lock()

        def render(macro, _pool):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.06)
            target = Path(macro['_output_path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'image')
            with guard:
                active -= 1
            return target

        with patch('module4_video_render._render_poster_with_retry', side_effect=render), \
             patch('module4_video_render._positive_env_int', return_value=1):
            video_studio._start_storyboard_images(path, record, [
                {'api_key': 'key1'}, {'api_key': 'key2'}, {'api_key': 'key3'}])
            deadline = time.time() + 3
            while str(path) in video_studio.ACTIVE and time.time() < deadline:
                time.sleep(0.02)
        self.assertGreaterEqual(peak, 2)
        self.assertEqual(record['status'], 'image_review')
        self.assertTrue(all(shot['image_status'] == 'completed' for shot in record['shots']))
        self.assertTrue(any('3 个 API' in line for line in record['logs']))

    def test_users_cannot_read_other_drafts(self):
        with patch.object(video_studio, 'require_user', return_value={'id': 'other-user'}):
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.get('/api/video-studio').json()['items'], [])

    def test_import_copies_assets_without_changing_source(self):
        import json
        source = Path(self.temp.name) / 'source'
        (source / 'input').mkdir(parents=True)
        (source / 'other' / 'reference_images').mkdir(parents=True)
        (source / 'image').mkdir()
        (source / 'input' / '配音.wav').write_bytes(b'audio-test')
        (source / 'other' / '最终字幕.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\n测试', encoding='utf-8')
        (source / 'other' / 'reference_images' / 'main.png').write_bytes(b'reference-test')
        (source / 'image' / 'poster_001.png').write_bytes(b'image-test')
        (source / 'other' / '参考图清单.json').write_text(json.dumps([
            {'filename': 'main.png', 'reference_id': '图1', 'description': '这是女主角', 'kind': 'character'}]), encoding='utf-8')
        before = {str(p): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        job = SimpleNamespace(id='source-job', request={'video_orientation': 'portrait',
            'visual_style_prompt': '简笔画', 'global_character_prompt': '红围巾', 'api_key': 'must-not-copy'})
        with patch.object(video_studio, 'source_project', return_value=(job, source)):
            response = self.client.post('/api/video-studio/from-project', json={'job_id': job.id, 'name': '独立新作'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['settings']['ratio'], '16:9')
        self.assertEqual(data['settings']['style'], '生动清晰的简笔画风格')
        self.assertEqual(data['settings']['characters'], '')
        self.assertEqual(data['references'], [])
        self.assertEqual(data['shots'], [])
        self.assertEqual(data['scenes'][0]['start'], 0)
        self.assertEqual(data['scenes'][0]['end'], 2)
        self.assertNotIn('must-not-copy', response.text)
        target = Path(self.temp.name) / 'test-user' / data['id']
        self.assertEqual((target / data['audio']).read_bytes(), b'audio-test')
        self.assertEqual(data['source_images'], [])
        self.assertEqual(sorted(p.name for p in (target / 'assets').iterdir()), ['audio.wav', 'subtitles.srt'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in source.rglob('*') if p.is_file()})
        (source / 'input' / '配音.wav').write_bytes(b'changed')
        self.assertEqual(self.client.get('/api/video-studio/' + data['id'] + '/audio').content, b'audio-test')
        with patch.object(video_studio, 'source_project', return_value=(job, source)):
            response = self.client.post('/api/video-studio/from-project', json={
                'job_id': job.id, 'style': '全新画风', 'characters': '蓝色小人', 'world': '新场景', 'ratio': '9:16',
                'dynamic_text_mode': 'visual_first'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['settings']['characters'], '蓝色小人')
        self.assertEqual(response.json()['settings']['style'], '全新画风')
        self.assertEqual(response.json()['settings']['ratio'], '9:16')
        self.assertEqual(response.json()['settings']['dynamic_text_mode'], 'visual_first')
        self.assertEqual(response.json()['creation_parameters']['dynamic_text_mode'], 'visual_first')
        job.request.update(dynamic_video=True, video_render_variant='both',
                           bgm_volume_db=-12, subtitle_layouts={'portrait': {'font_size': 50}},
                           dynamic_text_mode='visual_first')
        with patch.object(video_studio, 'source_project', return_value=(job, source)), \
             patch.object(video_studio, 'require_user', return_value={'id': 1}), \
             patch('backend.app.tts_editor.tts_editor.status', return_value={'status': 'idle'}), \
             patch('backend.app.tts_editor.tts_editor.commit_step_review') as commit, \
             patch('backend.app.pipeline.validate_step_audio_snapshot') as validate:
            response = self.client.post('/api/video-studio/from-project', json={
                'job_id': job.id, 'from_audio_task': True})
        self.assertEqual(response.status_code, 200, response.text)
        commit.assert_called_once()
        validate.assert_called_once()
        self.assertEqual(response.json()['creation_parameters']['bgm_volume_db'], -12)
        self.assertEqual(response.json()['creation_parameters']['subtitle_layouts']['portrait']['font_size'], 50)
        self.assertEqual(response.json()['creation_parameters']['dynamic_text_mode'], 'visual_first')
        self.assertEqual(response.json()['settings']['dynamic_text_mode'], 'visual_first')
        self.assertNotIn('api_key', response.json()['creation_parameters'])

    def test_dynamic_job_cannot_enter_old_image_pipeline(self):
        from backend.app.pipeline import JobStore
        job = SimpleNamespace(request={'dynamic_video': True})
        with self.assertRaisesRegex(ValueError, '动态视频'):
            JobStore().advance_step_workflow(job, 'start_visual')

    def _image_review_record(self):
        path = video_studio.directory('test-user', self.project['id'])
        image = path / 'assets' / 'storyboards' / 'shot1.jpg'
        image.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new('RGB', (4, 4), '#cc3322').save(image, 'JPEG')
        record = self.client.get(self.url).json()
        record.update(status='image_review', logs=[])
        record['shots'] = [{'id': 'shot1', 'slide_ids': ['scene_001', 'scene_002'],
                            'kind': 'static', 'intent': '主题', 'image_prompt': '初始提示词',
                            'image': 'assets/storyboards/shot1.jpg', 'image_status': 'completed'}]
        video_studio.save(path, record)
        return path, record, image.read_bytes()

    def test_local_storyboard_replacement_can_be_undone(self):
        path, record, original = self._image_review_record()
        from PIL import Image
        uploaded = io.BytesIO()
        Image.new('RGB', (6, 6), '#2266cc').save(uploaded, 'PNG')
        response = self.client.post(self.url + '/images/shot1/upload', data={
            'revision': record['revision'], 'prompt': '用户修改提示词'}, files={
            'file': ('replacement.png', uploaded.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        replaced = response.json()
        self.assertEqual(replaced['shots'][0]['image_prompt'], '用户修改提示词')
        self.assertEqual(len(replaced['shots'][0]['image_history']), 1)
        self.assertNotEqual((path / replaced['shots'][0]['image']).read_bytes(), original)
        undone = self.client.post(self.url + '/images/shot1/undo', json={
            'revision': replaced['revision']})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()['shots'][0]['image_prompt'], '初始提示词')
        self.assertEqual((path / undone.json()['shots'][0]['image']).read_bytes(), original)

    def test_storyboard_redraw_uses_numbered_references_and_keeps_history(self):
        path, record, original = self._image_review_record()
        from PIL import Image
        reference = io.BytesIO()
        Image.new('RGB', (3, 3), '#22aa66').save(reference, 'PNG')
        uploaded = self.client.post(self.url + '/redraw-references', files={
            'file': ('character.png', reference.getvalue(), 'image/png')})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        record = uploaded.json()
        reference_id = record['redraw_references'][0]['id']
        captured = {}

        def render(macro, _pool):
            captured.update(macro)
            target = Path(macro['_output_path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'new-storyboard-image')
            return target

        with patch.object(video_studio, '_image_configs', return_value=[{'api_key': 'test'}]), \
             patch('module4_video_render.shared_runninghub_account_pool', return_value=object()), \
             patch('module4_video_render._render_poster_with_retry', side_effect=render):
            started = self.client.post(self.url + '/images/shot1/redraw', json={
                'revision': record['revision'], 'prompt': '新提示词',
                'reference_ids': [reference_id], 'use_current_image': True, 'image_resolution': '2k'})
            self.assertEqual(started.status_code, 200, started.text)
            deadline = time.time() + 3
            while time.time() < deadline:
                record = self.client.get(self.url).json()
                if record['shots'][0].get('image_task', {}).get('status') != 'running':
                    break
                time.sleep(.02)
        shot = record['shots'][0]
        self.assertEqual(shot['image_task']['status'], 'completed')
        self.assertEqual(shot['image_prompt'], '新提示词')
        self.assertEqual(len(shot['image_history']), 1)
        self.assertEqual((path / shot['image_history'][0]['image']).read_bytes(), original)
        self.assertEqual(len(captured['reference_image_paths']), 2)
        self.assertIn('图1至图2', captured['image_prompt'])


if __name__ == '__main__':
    unittest.main()
