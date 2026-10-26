import copy
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from backend.app.video_agents import direct_motion, write_image_prompts
from backend.app.video_image_prompt import image_finalization_shots
from backend.app.video_text_policy import (
    explicit_visible_texts, motion_text_handoff_issues, visual_first_prompt_issues,
)
from backend.app.video_plan import normalize_shots, planning_fingerprint, plan_storyboard


CONTEXT = {'video_direction': {'dynamic_text_mode': 'visual_first'}}


def make_shot(identity='a'):
    return dict(id=identity, kind='video', intent='先准备，再启动', reference_ids=[],
                visual_description='机器依次显示“准备”和“启动”',
                motion_plan=dict(version=2, scene_anchor='工作台', participants=['机器'],
                    beats=[dict(action='机器上方浮现“准备”两个大字，随后消失。',
                                texts=[dict(text='准备', owner='画面标注', container='说明框')]),
                           dict(action='机器启动。',
                                texts=[dict(text='启动', owner='画面标注', container='说明框')])],
                    reference_beat=1, reference_visual='工作台上的机器，开关处于关闭位置。',
                    reference_participants=['机器'], reference_texts=[]))


def image_row(identity='a', scene='工作台上的机器，开关处于关闭位置。'):
    return dict(id=identity, image_sections=dict(characters_and_style='写实风格，无人物出镜',
                                                scene=scene, constraints=''))


class VideoTextHandoffTests(unittest.TestCase):
    def test_grouped_labels_and_floating_words_detected(self):
        self.assertEqual(explicit_visible_texts('说明框显示“准备”、“启动”。'), ['准备', '启动'])
        self.assertEqual(explicit_visible_texts('“准备”消失，浮现“启动”两个大字。'), ['启动'])
        self.assertEqual(explicit_visible_texts('浮现“先用药”三个大字，随后“先用药”消失，浮现“再手术”三个大字。'),
                         ['先用药', '再手术'])

    def test_narration_and_negative_quotes_not_promoted(self):
        for value in ('口播说“先准备”，动作表现为检查开关。', '不要显示标签“启动”。',
                      '移除标题“准备”和“启动”。', '人物呈现“若有所思”的表情。'):
            self.assertEqual(explicit_visible_texts(value), [])
        self.assertEqual(explicit_visible_texts('屏幕浮现“研究”标题，下方出现“人群”的图标。'), ['研究'])

    def test_phase_registry_missing_label_is_repaired_by_director(self):
        shot = make_shot()
        broken = copy.deepcopy(shot['motion_plan'])
        broken['beats'][0]['texts'] = []
        ask = Mock(side_effect=[{'shots': [dict(id='a', motion_plan=broken)]},
                                {'shots': [dict(id='a', motion_plan=shot['motion_plan'])]}])
        result = direct_motion({}, [shot], [], ask=ask)
        self.assertEqual(ask.call_count, 2)
        self.assertIn('漏登', ask.call_args.args[1]['validation_errors'][0])
        self.assertEqual(result[0]['motion_plan']['beats'][0]['texts'][0]['text'], '准备')
        self.assertEqual(result[0]['motion_plan']['reference_texts'], [])
        self.assertEqual(broken['beats'][0]['texts'], [])

    def test_inconsistent_registry_not_silently_approved(self):
        plan = make_shot()['motion_plan']
        plan['beats'][0]['texts'] = []
        ask = Mock(return_value={'shots': [dict(id='a', motion_plan=plan)]})
        with self.assertRaisesRegex(ValueError, 'a.*漏登'):
            direct_motion({}, [make_shot()], [], ask=ask)

    def test_director_reports_all_broken_shots_and_only_retries_them(self):
        shots = [make_shot(identity) for identity in ('a', 'b', 'c')]
        rows = [dict(id=s['id'], motion_plan=copy.deepcopy(s['motion_plan'])) for s in shots]
        rows[1]['motion_plan']['beats'][0]['texts'] = []
        rows[2]['motion_plan']['beats'][0]['texts'] = []
        ask = Mock(side_effect=[{'shots': rows}, {'shots': [
            dict(id=s['id'], motion_plan=s['motion_plan']) for s in shots[1:]]}])
        result = direct_motion({}, shots, [], ask=ask)
        self.assertEqual([row['id'] for row in result], ['a', 'b', 'c'])
        retry = ask.call_args.args[1]
        self.assertEqual([row['id'] for row in retry['shots']], ['b', 'c'])
        self.assertIn('镜头 b', retry['validation_errors'][0])
        self.assertIn('镜头 c', retry['validation_errors'][0])

    def test_core_and_video_have_separate_approved_texts(self):
        plan = make_shot()['motion_plan']
        self.assertEqual(visual_first_prompt_issues('说明框显示“准备”和“启动”。', plan), [])
        issues = visual_first_prompt_issues('说明框显示“准备”和“启动”。', plan, medium='image')
        self.assertEqual(len(issues), 2)
        plan['reference_texts'] = [plan['beats'][0]['texts'][0]]
        self.assertEqual(visual_first_prompt_issues('说明框显示“准备”。', plan, medium='image'), [])
        self.assertTrue(visual_first_prompt_issues('说明框显示“启动”。', plan, medium='image'))

    def test_legacy_v1_core_uses_only_reference_beat(self):
        plan = make_shot()['motion_plan']
        plan['version'] = 1
        plan.pop('reference_texts')
        plan.pop('reference_participants')
        self.assertEqual(visual_first_prompt_issues('说明框显示“准备”。', plan, medium='image'), [])
        self.assertTrue(visual_first_prompt_issues('说明框显示“启动”。', plan, medium='image'))

    def test_image_projection_preserves_original_and_static_shots(self):
        shot = make_shot()
        shot['action'] = '依次显示“准备”、“启动”'
        original = copy.deepcopy(shot)
        static = dict(id='s', kind='static', visual_description='完整说明图')
        prepared = image_finalization_shots([shot, static])
        self.assertEqual(shot, original)
        self.assertNotIn('beats', prepared[0]['motion_plan'])
        self.assertNotIn('action', prepared[0])
        self.assertEqual(prepared[0]['visual_description'], shot['motion_plan']['reference_visual'])
        self.assertEqual(prepared[1], static)

    def test_retry_only_failed_shot_and_keep_successful_neighbor(self):
        good = image_row('a')
        bad = image_row('b', '机器旁的说明框显示“启动”。')
        ask = Mock(side_effect=[{'shots': [good, bad]}, {'shots': [image_row('b')]}])
        draft = Mock()
        shots = [make_shot('a'), make_shot('b')]
        result = write_image_prompts(CONTEXT, '写实', shots, [], ask=ask, on_draft=draft)
        self.assertEqual([r['id'] for r in result], ['a', 'b'])
        retry = ask.call_args.args[1]
        self.assertEqual([r['id'] for r in retry['shots']], ['b'])
        self.assertEqual([r['id'] for r in retry['previous_result']['shots']], ['b'])
        self.assertEqual([r['id'] for r in draft.call_args_list[0].args[0]], ['a'])
        self.assertIn('开关处于关闭位置', result[0]['image_prompt'])

    def test_scene_reference_flag_not_required_to_repair(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                context = {**CONTEXT, 'scene_references_enabled': enabled}
                ask = Mock(side_effect=[{'shots': [image_row(scene='机器旁的说明框显示“启动”。')]},
                                       {'shots': [image_row()]}])
                result = write_image_prompts(context, '写实', [make_shot()], [], ask=ask)
                self.assertNotIn('说明框', result[0]['image_prompt'])
                self.assertEqual(ask.call_count, 2)

    def test_real_unselected_words_still_fail(self):
        ask = Mock(return_value={'shots': [image_row(scene='机器上方标题显示“额外宣传语”。')]})
        with self.assertRaisesRegex(ValueError, 'a.*额外宣传语'):
            write_image_prompts(CONTEXT, '写实', [make_shot()], [], ask=ask)

    def test_legacy_partial_resume_repairs_only_inconsistent_unfinished_shot(self):
        scenes = [dict(slide_id='s1', start=0., end=5., text='准备机器。'),
                  dict(slide_id='s2', start=5., end=10., text='启动机器。')]
        rows = [make_shot('a'), make_shot('b')]
        for index, row in enumerate(rows):
            row.update(slide_ids=[scenes[index]['slide_id']], action='机器启动',
                       image_prompt='已完成的机器核心图', video_prompt='参考图1，机器启动')
            row['motion_plan']['beats'][0]['texts'] = []
        saved = normalize_shots(rows, scenes)
        parameters = {'dynamic_text_mode': 'visual_first'}
        state = dict(fingerprint=planning_fingerprint(scenes, '', '', '', [], parameters),
                     context={}, stage='图像提示词草案', shots=saved,
                     completed=dict(core=['a', 'b'], motion=['a', 'b'], image=['b'], video=['b']))
        snapshots = []
        with ExitStack() as stack:
            core = stack.enter_context(patch('backend.app.video_agents.design_core_images'))
            motion = stack.enter_context(patch('backend.app.video_agents.direct_motion',
                side_effect=lambda context, shots, refs: [dict(id=s['id'], motion_plan=make_shot()['motion_plan'],
                                                              action='机器依次准备并启动') for s in shots]))
            stack.enter_context(patch('backend.app.video_agents.write_image_prompts',
                side_effect=lambda context, style, shots, refs, **kwargs: [dict(id=s['id'], image_prompt='机器核心图') for s in shots]))
            stack.enter_context(patch('backend.app.video_agents.write_video_prompts',
                side_effect=lambda context, shots, refs, **kwargs: [dict(id=s['id'], video_prompt='参考图1，机器启动') for s in shots]))
            _, result = plan_storyboard(scenes, '', '', '', [], lambda _: None, parameters=parameters,
                                        resume_state=state, save_state=snapshots.append)
        core.assert_not_called()
        self.assertEqual([s['id'] for s in motion.call_args.args[1]], ['a'])
        self.assertEqual(result[1]['image_prompt'], saved[1]['image_prompt'])
        self.assertEqual(result[1]['motion_plan'], saved[1]['motion_plan'])
        self.assertEqual(result[1]['video_prompt'], saved[1]['video_prompt'])
        self.assertEqual(snapshots[-1]['completed']['video'], ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
