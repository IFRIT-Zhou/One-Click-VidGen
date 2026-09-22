import copy
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import video_studio
from backend.app.video_plan import normalize_shots, plan_storyboard, planning_fingerprint


class VideoPlanningResumeTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.scenes = [
            {'slide_id': 's1', 'start': 0.0, 'end': 4.0, 'text': '观众提出问题。'},
            {'slide_id': 's2', 'start': 4.0, 'end': 8.0, 'text': '主持人举例回答。'},
        ]
        self.saved = []
        self.logs = []
        self.phase_plan = {
            'version': 1, 'scene_anchor': '讲台与观众席', 'participants': [],
            'beats': [{'action': '先提出问题再举例', 'texts': []}],
            'reference_beat': 1, 'reference_visual': '讲台前的场面',
        }
        self.mocks = {
            'context': self.stack.enter_context(patch(
                'story_agents.create_story_context', return_value={'summary': '问答场景'})),
        }
        functions = {
            'groups': ('plan_groups', self.groups),
            'core': ('design_core_images', self.core),
            'motion': ('direct_motion', self.motion),
            'image': ('write_image_prompts', self.images),
            'video': ('write_video_prompts', self.videos),
        }
        for key, (name, implementation) in functions.items():
            self.mocks[key] = self.stack.enter_context(patch(
                'backend.app.video_agents.' + name, side_effect=implementation))

    def groups(self, _context, scenes, _arrangement, **kwargs):
        return [dict(id='shot-' + row['slide_id'], slide_ids=[row['slide_id']],
                     kind='video', intent=row['text']) for row in scenes]

    def core(self, _context, _scenes, shots, _references):
        return [dict(id=shot['id'], visual_description='讲台前可见问答关系',
                     visual_design={'visible_evidence': '讲台前可见问答关系'},
                     reference_ids=[]) for shot in shots]

    def motion(self, _context, shots, _references):
        return [dict(id=shot['id'], action='先提出问题再举例',
                     motion_plan=copy.deepcopy(self.phase_plan)) for shot in shots]

    def images(self, _context, _style, shots, _references, on_draft=None):
        rows = [dict(id=shot['id'], image_prompt=(
            '【人物与画风】简笔画\n【画面内容】讲台前可见问答关系\n【必要限制】无额外文字'))
                for shot in shots]
        if on_draft:
            on_draft(copy.deepcopy(rows))
        return rows

    def videos(self, _context, shots, _references, on_draft=None):
        rows = [dict(id=shot['id'], video_prompt='参考图1的讲台场景，先提出问题再举例，静音。')
                for shot in shots]
        if on_draft:
            on_draft(copy.deepcopy(rows))
        return rows

    def run_plan(self, resume=None, style='简笔画'):
        return plan_storyboard(
            self.scenes, style, '', '', [], self.logs.append,
            resume_state=resume, save_state=lambda state: self.saved.append(copy.deepcopy(state)))

    def test_fixed_manual_groups_repair_only_affected_design(self):
        _, original = self.run_plan()
        for mock in self.mocks.values():
            mock.reset_mock()
        fixed = copy.deepcopy(original)
        fixed[0]['design_needs_review'] = True
        def revised_core(context, scenes, shots, references):
            return [dict(row, intent='按新字幕重新表达') for row in self.core(context, scenes, shots, references)]
        self.mocks['core'].side_effect = revised_core
        _, repaired = plan_storyboard(self.scenes, '简笔画', '', '', [], self.logs.append,
                                     fixed_shots=fixed, repair_only=True)
        self.mocks['groups'].assert_not_called()
        self.assertEqual([s['slide_ids'] for s in repaired], [s['slide_ids'] for s in fixed])
        self.assertEqual(repaired[0]['intent'], '按新字幕重新表达')
        self.assertFalse(repaired[0]['design_needs_review'])
        self.assertEqual(repaired[1], original[1])
        for key in ('core','motion','image','video'):
            self.assertEqual(self.mocks[key].call_count, 1)
        self.assertEqual(len(self.mocks['core'].call_args.args[2]), 1)

    def test_old_heading_failure_recovers_without_any_agent_calls(self):
        self.run_plan()
        saved = copy.deepcopy(self.saved[-1])
        saved['stage'] = '最终校验待修订：继续时仅重做未通过镜头'
        saved['completed'] = {phase: [] for phase in ('core', 'motion', 'image', 'video')}
        for shot in saved['shots']:
            shot['image_prompt'] = '【人物与画风】简笔画【画面内容】观众举手【画面文字与归属】菜单'
        for mock in self.mocks.values():
            mock.reset_mock()
        _, recovered = self.run_plan(saved)
        for mock in self.mocks.values():
            mock.assert_not_called()
        self.assertEqual([s['image_prompt'] for s in recovered], [s['image_prompt'] for s in saved['shots']])
        self.assertTrue(all(s['audit']['ready'] and not s['image_prompt_warnings'] for s in recovered))
        self.assertEqual(len(self.saved[-1]['completed']['image']), 2)

    def test_normal_replan_with_fixed_groups_never_calls_group_agent(self):
        _, original = self.run_plan()
        self.mocks['groups'].reset_mock()
        _, rebuilt = plan_storyboard(self.scenes, '简笔画', '', '', [], self.logs.append,
                                    fixed_shots=original)
        self.mocks['groups'].assert_not_called()
        self.assertEqual([s['slide_ids'] for s in rebuilt], [s['slide_ids'] for s in original])

    def test_group_failure_reuses_context_and_saved_group_draft(self):
        draft = self.groups({}, self.scenes, {})

        def interrupted(context, scenes, arrangement, **kwargs):
            kwargs['on_draft'](draft)
            raise RuntimeError('局部拆分请求中断')

        self.mocks['groups'].side_effect = interrupted
        with self.assertRaisesRegex(RuntimeError, '局部拆分'):
            self.run_plan()
        resume = copy.deepcopy(self.saved[-1])
        self.assertEqual(resume['context']['summary'], '问答场景')
        self.assertEqual(resume['groups'], draft)
        self.assertFalse(resume.get('shots'))
        self.mocks['groups'].side_effect = self.groups
        _, shots = self.run_plan(resume)
        self.mocks['context'].assert_called_once()
        self.assertEqual(self.mocks['groups'].call_count, 2)
        self.assertEqual(self.mocks['groups'].call_args.kwargs['resume_rows'], draft)
        self.assertEqual([row['slide_ids'] for row in shots], [['s1'], ['s2']])
        self.assertTrue(all(row['audit']['ready'] for row in shots))

    def test_video_failure_reuses_completed_core_motion_and_image(self):
        def interrupted(*args, **kwargs):
            self.videos(*args, **kwargs)
            raise RuntimeError('视频提示词请求中断')

        self.mocks['video'].side_effect = interrupted
        with self.assertRaisesRegex(RuntimeError, '视频提示词'):
            self.run_plan()
        resume = copy.deepcopy(self.saved[-1])
        identities = ['shot-s1', 'shot-s2']
        for phase in ('core', 'motion', 'image'):
            self.assertEqual(resume['completed'][phase], identities)
        self.assertFalse(resume['completed'].get('video'))
        self.assertTrue(resume['shots'][0]['video_prompt'])
        self.mocks['video'].side_effect = self.videos
        _, shots = self.run_plan(resume)
        for stage in ('context', 'groups', 'core', 'motion', 'image'):
            self.mocks[stage].assert_called_once()
        self.assertEqual(self.mocks['video'].call_count, 2)
        self.assertEqual(self.saved[-1]['completed']['video'], identities)
        self.assertTrue(all(row['audit']['ready'] for row in shots))

    def test_image_draft_does_not_count_as_completed_finalization(self):
        def interrupted(*args, **kwargs):
            self.images(*args, **kwargs)
            raise RuntimeError('图像定稿中断')

        self.mocks['image'].side_effect = interrupted
        with self.assertRaisesRegex(RuntimeError, '图像定稿'):
            self.run_plan()
        resume = copy.deepcopy(self.saved[-1])
        self.assertTrue(resume['shots'][0]['image_prompt'])
        self.assertFalse(resume['completed'].get('image'))
        self.mocks['video'].assert_not_called()
        self.mocks['image'].side_effect = self.images
        self.run_plan(resume)
        for stage in ('context', 'groups', 'core', 'motion', 'video'):
            self.mocks[stage].assert_called_once()
        self.assertEqual(self.mocks['image'].call_count, 2)

    def test_later_batch_failure_does_not_repeat_completed_earlier_batch(self):
        self.scenes = [dict(slide_id=f's{i}', start=i * 4.0, end=(i + 1) * 4.0,
                            text=f'第{i}段内容。') for i in range(7)]

        def interrupted(context, scenes, shots, references):
            if self.mocks['core'].call_count == 2:
                raise RuntimeError('第二批核心画面中断')
            return self.core(context, scenes, shots, references)

        self.mocks['core'].side_effect = interrupted
        with self.assertRaisesRegex(RuntimeError, '第二批'):
            self.run_plan()
        resume = copy.deepcopy(self.saved[-1])
        self.assertEqual(len(resume['completed']['video']), 6)
        self.mocks['core'].side_effect = self.core
        _, shots = self.run_plan(resume)
        self.assertEqual(len(shots), 7)
        self.assertEqual(self.mocks['core'].call_count, 3)
        self.assertEqual([row['id'] for row in self.mocks['core'].call_args.args[2]], ['shot-s6'])
        for stage in ('motion', 'image', 'video'):
            self.assertEqual(self.mocks[stage].call_count, 2)
        self.mocks['groups'].assert_called_once()
        self.mocks['context'].assert_called_once()

    def test_changed_input_invalidates_completed_resume_state(self):
        self.run_plan()
        resume = copy.deepcopy(self.saved[-1])
        original = copy.deepcopy(resume)
        self.run_plan(resume, style='水墨画')
        self.assertNotEqual(self.saved[-1]['fingerprint'], original['fingerprint'])
        for stage, mocked in self.mocks.items():
            with self.subTest(stage=stage):
                self.assertEqual(mocked.call_count, 2)
        self.assertEqual(resume, original)

    def test_text_mode_reaches_all_design_agents_and_invalidates_old_resume(self):
        legacy_context, _ = self.run_plan()
        self.assertEqual(legacy_context['video_direction']['dynamic_text_mode'], 'text_assisted')
        old_state = copy.deepcopy(self.saved[-1])
        context, _ = plan_storyboard(self.scenes, '简笔画', '', '', [], self.logs.append,
            parameters={'dynamic_text_mode': 'visual_first'}, resume_state=old_state,
            save_state=lambda value: self.saved.append(copy.deepcopy(value)))
        self.assertNotEqual(old_state['fingerprint'], self.saved[-1]['fingerprint'])
        for phase in ('groups', 'core', 'motion', 'image', 'video'):
            self.assertEqual(self.mocks[phase].call_count, 2)
            self.assertEqual(self.mocks[phase].call_args.args[0]['video_direction']['dynamic_text_mode'],
                             'visual_first')
        self.assertEqual(context['video_direction']['dynamic_text_mode'], 'visual_first')
        for mocked in self.mocks.values():
            mocked.reset_mock()
        plan_storyboard(self.scenes, '简笔画', '', '', [], self.logs.append,
            parameters={'dynamic_text_mode': 'visual_first'}, resume_state=self.saved[-1])
        for mocked in self.mocks.values():
            mocked.assert_not_called()

    def test_failed_final_audit_redesigns_only_the_invalid_shot_on_resume(self):
        def incomplete_videos(*args, **kwargs):
            rows = self.videos(*args, **kwargs)
            rows[1]['video_prompt'] = '缺少核心分镜图引用的旧草稿'
            return rows

        self.mocks['video'].side_effect = incomplete_videos
        with self.assertRaisesRegex(ValueError, '最终校验未通过'):
            self.run_plan()
        resume = copy.deepcopy(self.saved[-1])
        for phase in ('core', 'motion', 'image', 'video'):
            self.assertEqual(resume['completed'][phase], ['shot-s1'])
        valid_before = copy.deepcopy(resume['shots'][0])
        self.mocks['video'].side_effect = self.videos
        _, shots = self.run_plan(resume)
        self.mocks['context'].assert_called_once()
        self.mocks['groups'].assert_called_once()
        for phase, argument_index in (('core', 2), ('motion', 1), ('image', 2), ('video', 1)):
            self.assertEqual(self.mocks[phase].call_count, 2)
            retried = self.mocks[phase].call_args.args[argument_index]
            self.assertEqual([row['id'] for row in retried], ['shot-s2'])
        for field in ('visual_description', 'motion_plan', 'image_prompt', 'video_prompt'):
            self.assertEqual(shots[0][field], valid_before[field])
        self.assertTrue(all(row['audit']['ready'] for row in shots))
        self.assertEqual(self.saved[-1]['completed']['video'], ['shot-s1', 'shot-s2'])


class VideoPlanningResumeApiTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(video_studio, 'ROOT', Path(temp)))
        self.stack.enter_context(patch.object(video_studio, 'require_user', return_value={'id': 'resume-user'}))
        app = FastAPI()
        app.include_router(video_studio.router)
        self.client = self.stack.enter_context(TestClient(app))
        response = self.client.post('/api/video-studio', json={
            'srt': '1\n00:00:00,000 --> 00:00:04,000\n观众提问。\n\n'
                   '2\n00:00:04,000 --> 00:00:08,000\n主持人回答。'})
        self.assertEqual(response.status_code, 200, response.text)
        self.record = response.json()
        self.url = '/api/video-studio/' + self.record['id']
        self.path = video_studio.directory('resume-user', self.record['id'])
        self.shots = normalize_shots([dict(
            id='complete-shot', kind='static', slide_ids=['scene_001', 'scene_002'], intent='解释问答关系',
            image_prompt='【人物与画风】简笔画\n【画面内容】讲台前的问答场景\n【必要限制】无额外文字',
        )], self.record['scenes'])

    def make_state(self):
        settings = self.record['settings']
        return dict(
            fingerprint=planning_fingerprint(
                self.record['scenes'], settings['style'], settings['characters'], settings['world'],
                self.record['references'], self.record.get('creation_parameters', {})),
            context={'summary': '已理解问答关系'}, stage='全文理解')

    def seed_resume(self):
        self.record.update(status='storyboard_review', shots=copy.deepcopy(self.shots),
                           planning_state=self.make_state(), planning_resume_available=True)
        video_studio.save(self.path, self.record)

    def wait_for_plan(self):
        deadline = time.monotonic() + 5
        while str(self.path) in video_studio.ACTIVE and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertNotIn(str(self.path), video_studio.ACTIVE)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_failed_plan_persists_resume_and_successful_retry_clears_it(self):
        state = self.make_state()

        def interrupted(*args, save_state=None, **kwargs):
            save_state(copy.deepcopy(state))
            raise RuntimeError('计划请求中断')

        with patch.object(video_studio, 'plan_storyboard', side_effect=interrupted):
            response = self.client.post(self.url + '/plan')
            self.assertEqual(response.status_code, 200, response.text)
            failed = self.wait_for_plan()
        self.assertTrue(failed['planning_resume_available'])
        self.assertEqual(failed['planning_state'], state)

        def resumed(*args, resume_state=None, **kwargs):
            self.assertEqual(resume_state, state)
            return state['context'], copy.deepcopy(self.shots)

        with patch.object(video_studio, 'plan_storyboard', side_effect=resumed):
            response = self.client.post(self.url + '/plan')
            self.assertEqual(response.status_code, 200, response.text)
            succeeded = self.wait_for_plan()
        self.assertEqual(succeeded['status'], 'storyboard_review')
        self.assertEqual(succeeded['shots'], self.shots)
        self.assertFalse(succeeded.get('planning_state'))
        self.assertFalse(succeeded.get('planning_resume_available'))

    def test_editing_or_confirming_plan_invalidates_resume(self):
        for action in ('edit', 'split', 'review'):
            with self.subTest(action=action):
                self.seed_resume()
                revision = self.record['revision']
                if action == 'edit':
                    changed = copy.deepcopy(self.shots)
                    changed[0]['intent'] = '用户亲自修改的意图'
                    response = self.client.put(self.url, json={'revision': revision, 'shots': changed})
                elif action == 'split':
                    response = self.client.post(self.url + '/structure', json={
                        'revision': revision, 'action': 'split', 'index': 0, 'boundary': 1})
                else:
                    response = self.client.post(self.url + '/review', json={'revision': revision})
                self.assertEqual(response.status_code, 200, response.text)
                record = self.client.get(self.url).json()
                self.assertFalse(record.get('planning_state'))
                self.assertFalse(record.get('planning_resume_available'))
                if action == 'edit':
                    self.assertEqual(record['shots'][0]['intent'], '用户亲自修改的意图')
                elif action == 'split':
                    self.assertEqual([row['slide_ids'] for row in record['shots']],
                                     [['scene_001'], ['scene_002']])
                else:
                    self.assertEqual(record['status'], 'generation_ready')

    def test_stop_during_agent_call_saves_returned_response_and_resume_skips_it(self):
        for stop_stage in ('context', 'core'):
            with self.subTest(stage=stop_stage), ExitStack() as stack:
                video_studio.save(self.path, copy.deepcopy(self.record))
                started = threading.Event()
                release = threading.Event()
                stopped = threading.Event()

                def gate(stage):
                    if stage == stop_stage and not release.is_set():
                        started.set()
                        if not release.wait(5):
                            raise RuntimeError('测试等待停止操作超时')

                def context(*args, **kwargs):
                    gate('context')
                    return {'summary': '停止期间仍成功返回的全文理解'}

                def core(_context, _scenes, shots, _references):
                    gate('core')
                    return [dict(id=row['id'], visual_description='停止期间完成的核心场面',
                                 visual_design={}, reference_ids=[]) for row in shots]

                def planning(*args, **kwargs):
                    try:
                        return plan_storyboard(*args, **kwargs)
                    except video_studio.PlanningStopped:
                        stopped.set()
                        raise

                context_call = stack.enter_context(patch('story_agents.create_story_context', side_effect=context))
                core_call = stack.enter_context(patch('backend.app.video_agents.design_core_images', side_effect=core))
                stack.enter_context(patch('backend.app.video_agents.plan_groups', return_value=[{
                    'id': 'complete-shot', 'kind': 'video', 'slide_ids': ['scene_001', 'scene_002'],
                    'intent': '解释问答关系'}]))
                stack.enter_context(patch('backend.app.video_agents.direct_motion', return_value=[{
                    'id': 'complete-shot', 'action': '先提问再回答', 'motion_plan': {
                        'version': 1, 'scene_anchor': '讲台与观众席', 'participants': [],
                        'beats': [{'action': '先提问再回答', 'texts': []}],
                        'reference_beat': 1, 'reference_visual': '问答的场面'}}]))
                stack.enter_context(patch('backend.app.video_agents.write_image_prompts', return_value=[{
                    'id': 'complete-shot', 'image_prompt': self.shots[0]['image_prompt']}]))
                stack.enter_context(patch('backend.app.video_agents.write_video_prompts', return_value=[{
                    'id': 'complete-shot', 'video_prompt': '参考图1的讲台场景，先提问再回答，静音。'}]))
                stack.enter_context(patch.object(video_studio, 'plan_storyboard', side_effect=planning))
                try:
                    response = self.client.post(self.url + '/plan')
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(started.wait(5))
                    response = self.client.post(self.url + '/stop')
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['status'], 'stopping')
                finally:
                    release.set()
                    paused = self.wait_for_plan()
                self.assertTrue(stopped.is_set())
                self.assertTrue(paused['planning_resume_available'])
                self.assertEqual(paused['planning_state']['context']['summary'],
                                 '停止期间仍成功返回的全文理解')
                if stop_stage == 'core':
                    self.assertEqual(paused['planning_state']['completed']['core'], ['complete-shot'])
                    self.assertEqual(paused['planning_state']['shots'][0]['visual_description'],
                                     '停止期间完成的核心场面')
                    self.assertEqual(paused['shots'][0]['visual_description'], '停止期间完成的核心场面')
                response = self.client.post(self.url + '/plan')
                self.assertEqual(response.status_code, 200, response.text)
                finished = self.wait_for_plan()
                self.assertEqual(finished['status'], 'storyboard_review')
                self.assertFalse(finished.get('error'))
                self.assertFalse(finished.get('planning_resume_available'))
                self.assertTrue(finished['shots'][0]['audit']['ready'])
                context_call.assert_called_once()
                core_call.assert_called_once()


if __name__ == '__main__':
    unittest.main()
