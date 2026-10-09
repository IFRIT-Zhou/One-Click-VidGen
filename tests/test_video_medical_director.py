import copy
import threading
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app import video_agents as agents, video_studio as studio
from backend.app.video_medical_director import MEDICAL_DESIGN_FIELDS, medical_contract, medical_design_issues
from backend.app.video_plan import normalize_shots, plan_storyboard
from backend.app.video_text_policy import normalize_text_mode, text_mode_contract
from backend.app.video_prompt_refresh import revise_motion


CONTEXT = {'video_direction': {'dynamic_text_mode': 'medical_paper'}}
SCENES = [{'slide_id': 's1', 'start': 0., 'end': 4., 'text': '局部受到作用。'},
          {'slide_id': 's2', 'start': 4., 'end': 8., 'text': '又是否反过来影响细胞？'}]
DESIGN = {key: key + '：有原文依据的具体设计' for key in MEDICAL_DESIGN_FIELDS}
MOTION = dict(version=2, scene_anchor='三维组织模型', participants=['组织'],
              beats=[{'action': '0～4秒，组织局部受力，箭头落在受力部位', 'texts': []},
                     {'action': '4～8秒，镜头拉远，组织结构改变明显可见', 'texts': []}],
              reference_beat=2, reference_visual='组织宽景，三维结构',
              reference_participants=['组织'], reference_texts=[])


def shot(kind='video'):
    return dict(id='a', slide_ids=['s1', 's2'], kind=kind, intent='揭示反馈问题',
                motion_basis='依据原文的反馈关系', progression_plan='作用后反馈',
                semantic=dict(message='反馈问题', source_basis='反过来影响细胞', fact_status='question',
                              progression='先作用再反馈', continuity_requirement='同一局部'),
                visual_description='组织宽景，三维结构', visual_design=copy.deepcopy(DESIGN),
                reference_ids=[], source_subtitles=copy.deepcopy(SCENES), duration=8,
                motion_plan=copy.deepcopy(MOTION))


@unittest.skipUnless(__import__('backend.app.director_extensions', fromlist=['get_profile']).get_profile('medical_paper'),
                     'Optional private director profile is not installed')
class MedicalDirectorTests(unittest.TestCase):
    def test_mode_is_opt_in_and_schema_accepts_it(self):
        self.assertEqual(normalize_text_mode(None), 'text_assisted')
        self.assertEqual(normalize_text_mode('medical_paper'), 'medical_paper')
        self.assertEqual(medical_contract({}, 'core'), '')
        self.assertNotIn('医学文献专用', text_mode_contract({}))
        for schema in (studio.Create, studio.ImportProject, studio.ProjectSettingsEdit):
            self.assertIn('medical_paper', schema.model_json_schema()['properties']['dynamic_text_mode']['enum'])

    def test_group_agent_keeps_question_and_does_not_force_opening_motion(self):
        ask = Mock(return_value={'shots': [shot('static')]})
        result = agents.plan_groups(CONTEXT, SCENES, {}, ask)
        self.assertEqual(result[0]['slide_ids'], ['s1', 's2'])
        self.assertEqual(result[0]['kind'], 'static')
        system, payload = ask.call_args.args
        self.assertNotIn('开头两个镜头承担观众留存，必须规划为 video', system)
        self.assertIn('一个反问及其前面的铺垫尽量同镜', system)
        self.assertEqual(payload['arrangement']['opening_motion_shots'], 0)

    def test_core_handoff_repairs_once_and_survives_normalization(self):
        incomplete = shot()
        incomplete.pop('visual_design')
        ask = Mock(side_effect=[{'shots': [incomplete]}, {'shots': [shot()]}])
        result = agents.design_core_images(CONTEXT, SCENES, [shot()], [], ask)
        self.assertEqual(ask.call_count, 2)
        self.assertIn('medical_evidence', ask.call_args.args[1]['validation_errors'][0])
        self.assertIn('source_example', ask.call_args.args[1]['method_example'])
        normalized = normalize_shots(result, SCENES)[0]
        for key in MEDICAL_DESIGN_FIELDS:
            self.assertEqual(normalized['visual_design'][key], DESIGN[key])
        self.assertEqual(medical_design_issues({}, incomplete), [])

    def test_legacy_design_does_not_acquire_medical_fields(self):
        value = shot()
        value['visual_design'] = {}
        normalized = normalize_shots([value], SCENES)[0]
        self.assertTrue(set(MEDICAL_DESIGN_FIELDS).isdisjoint(normalized['visual_design']))

    def test_motion_preserves_late_reference_camera_and_timing(self):
        ask = Mock(return_value={'shots': [{'id': 'a', 'motion_plan': copy.deepcopy(MOTION)}]})
        result = agents.direct_motion(CONTEXT, [shot()], [], ask)[0]
        self.assertEqual(result['motion_plan']['reference_beat'], 2)
        self.assertIn('4～8秒，镜头拉远', result['action'])
        self.assertIn('变化幅度', ask.call_args.args[0])
        self.assertEqual(ask.call_args.args[1]['method_example']['motion_plan']['reference_beat'], 3)

    def test_two_short_labels_allowed_without_visual_first_budget(self):
        plan = copy.deepcopy(MOTION)
        plan['beats'][0]['texts'] = [dict(text=t, owner='组织', container='组织旁标注')
                                    for t in ('外部作用', '内部影响')]
        ask = Mock(return_value={'shots': [{'id': 'a', 'motion_plan': plan}]})
        result = agents.direct_motion(CONTEXT, [shot()], [], ask)[0]
        self.assertEqual(len(result['motion_plan']['beats'][0]['texts']), 2)
        self.assertEqual(ask.call_count, 1)

    def test_static_finalizer_keeps_cycle_labels_and_mode_style(self):
        ask = Mock(return_value={'shots': [dict(id='a', image_sections=dict(
            characters_and_style='三维医学建模，无人物出镜',
            scene='循环海报：组织→病变→力学改变→组织，短标签标在节点旁', constraints=''))]})
        result = agents.write_image_prompts(CONTEXT, '手绘风', [shot('static')], [], ask)
        self.assertIn('循环海报', result[0]['image_prompt'])
        self.assertIn('人物与画风', result[0]['image_prompt'])
        self.assertIn('不拿全局手绘风格盖回三维器官', ask.call_args.args[0])

    def test_video_finalizer_retains_each_phase_and_no_auto_subtitles(self):
        ask = Mock(return_value={'shots': [dict(id='a', continuity_prompt='三维组织，沿用核心图材质',
            beat_prompts=[{'beat': i, 'prompt': b['action']} for i, b in enumerate(MOTION['beats'], 1)],
            ending_prompt='组织宽景短暂收束')]})
        result = agents.write_video_prompts(CONTEXT, [shot()], [], ask)[0]
        self.assertIn('4～8秒，镜头拉远', result['video_prompt'])
        self.assertIn('禁止擅自生成任何字幕', result['video_prompt'])
        self.assertNotIn('medical_evidence', result['video_prompt'])
        self.assertIn('不新增疗效', ask.call_args.args[0])

    def test_manual_refresh_uses_medical_rules_without_overriding_user(self):
        ask = Mock(return_value={'shots': [{'id': 'a', 'motion_plan': copy.deepcopy(MOTION)}]})
        result = revise_motion(CONTEXT, shot(), [], '用户指定拉远', '宽景', 'image_prompt', ask)
        self.assertEqual(result['reference_beat'], 2)
        self.assertIn('manual_action 是用户亲自修改', ask.call_args.args[0])
        self.assertIn('医学动态导演', ask.call_args.args[0])

    def test_medical_finalizer_cannot_invent_displayed_claims(self):
        ask = Mock(return_value={'shots': [dict(id='a', continuity_prompt='三维组织',
            beat_prompts=[{'beat': 1, 'prompt': '组织上方标注“治愈率百分之百”'},
                          {'beat': 2, 'prompt': '组织恢复'}], ending_prompt='组织宽景')]})
        with self.assertRaisesRegex(ValueError, '未入选'):
            agents.write_video_prompts(CONTEXT, [shot()], [], ask)
        self.assertEqual(ask.call_count, 2)

    def test_generic_group_mode_still_keeps_original_opening_policy(self):
        ask = Mock(return_value={'shots': [shot()]})
        agents.plan_groups({}, SCENES, {}, ask)
        self.assertIn('开头两个镜头承担观众留存，必须规划为 video', ask.call_args.args[0])
        self.assertEqual(ask.call_args.args[1]['arrangement']['opening_motion_shots'], 2)

    def test_pipeline_does_not_force_medical_static_opening(self):
        rows = [shot('static')]
        with patch('story_agents.create_story_context', return_value={}) as agent0, \
             patch.object(agents, 'plan_groups', return_value=rows), \
             patch.object(agents, 'design_core_images', return_value=rows), \
             patch.object(agents, 'direct_motion') as motion, \
             patch.object(agents, 'write_video_prompts') as video, \
             patch.object(agents, 'write_image_prompts', return_value=[dict(id='a', image_prompt='三维循环海报')]):
            context, result = plan_storyboard(SCENES, '手绘', '', '', [], lambda message: None,
                                              parameters={'dynamic_text_mode': 'medical_paper'})
        self.assertEqual(result[0]['kind'], 'static')
        self.assertEqual(result[0]['visual_design']['medical_evidence'], DESIGN['medical_evidence'])
        motion.assert_not_called()
        video.assert_not_called()
        self.assertIn('后段宽景', context['video_direction']['core_image_role'])
        self.assertIn('医学全文理解', agent0.call_args.kwargs['agent0_prompt_system'])


class MedicalReplanRouteTests(unittest.TestCase):
    def run_replan(self, *, regroup=False, fail=False):
        record = dict(id='medical-test', revision=3, status='image_review', logs=[], scenes=copy.deepcopy(SCENES),
                      shots=normalize_shots([shot()], SCENES), references=[], audio='original.wav',
                      settings=dict(style='手绘', characters='', world='', dynamic_text_mode='visual_first'),
                      creation_parameters=dict(dynamic_text_mode='visual_first', dynamic_auto_advance=True),
                      manual_groups=[dict(id='a', slide_ids=['s1', 's2'], kind='video')])
        original = copy.deepcopy(record)
        planned = Mock(side_effect=ValueError('模拟规划失败') if fail else None,
                       return_value=(copy.deepcopy(CONTEXT), normalize_shots([shot('static')], SCENES)))
        fake_threading = SimpleNamespace(Event=threading.Event,
            Thread=lambda target, **kwargs: SimpleNamespace(start=target))
        with ExitStack() as stack:
            for name, value in [('require_user', lambda r: {'id': 1}), ('directory', lambda *a: Path('medical-test')),
                                ('read', lambda p: record), ('save', Mock()), ('threading', fake_threading),
                                ('project_has_image_edits', lambda *a: False), ('editable', Mock()),
                                ('project_language_scope', lambda *a: nullcontext()), ('plan_storyboard', planned),
                                ('ACTIVE', set()), ('CANCEL_EVENTS', {})]:
                stack.enter_context(patch.object(studio, name, value))
            result = studio.plan('medical-test', object(), fresh=True, revision=3, regroup=regroup,
                                 expression_mode='medical_paper')
        return original, result, planned

    def test_regroup_preserves_audio_subtitles_and_archives_old_shots(self):
        old, result, planned = self.run_replan(regroup=True)
        self.assertIsNone(planned.call_args.kwargs['fixed_shots'])
        self.assertEqual(result['audio'], old['audio'])
        self.assertEqual(result['scenes'], old['scenes'])
        self.assertEqual(result['status'], 'storyboard_review')
        self.assertEqual(result['creation_parameters']['dynamic_text_mode'], 'medical_paper')
        self.assertFalse(result['creation_parameters']['dynamic_auto_advance'])
        self.assertNotIn('manual_groups', result)
        self.assertEqual(result['replanning_history'][-1]['shots'], old['shots'])

    def test_default_replan_keeps_boundaries(self):
        old, result, planned = self.run_replan()
        self.assertEqual(planned.call_args.kwargs['fixed_shots'], old['shots'])

    def test_failed_replan_restores_mode_and_existing_media(self):
        old, result, planned = self.run_replan(regroup=True, fail=True)
        for field in ('audio', 'scenes', 'shots', 'settings', 'creation_parameters', 'manual_groups', 'status'):
            self.assertEqual(result[field], old[field])
        self.assertIn('模拟规划失败', result['error'])


if __name__ == '__main__':
    unittest.main()
