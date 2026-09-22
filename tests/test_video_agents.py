import copy
import unittest

from backend.app.video_agents import (audit_storyboard, design_core_images, direct_motion, plan_groups,
                                      write_image_prompts, write_video_prompts)


class VideoAgentsTest(unittest.TestCase):
    def setUp(self):
        self.context = {'summary': '测试'}
        self.scenes = [{'slide_id': 'scene_001', 'text': '甲', 'start': 0, 'end': 5}]
        self.shot = {'id': 'shot1', 'slide_ids': ['scene_001'], 'kind': 'video',
                     'intent': '观众理解提问热烈', 'action': '观众举手', 'start': 0,
                     'end': 5, 'duration': 5, 'generation_duration': 5,
                     'reference_ids': [], 'warning': '', 'image_prompt': '', 'video_prompt': ''}
        self.motion_plan = {'version': 1, 'scene_anchor': '主讲人与观众在同一会场',
                            'participants': ['主讲人', '观众'], 'beats': [
                                {'action': '观众举手提问', 'texts': [
                                    {'text': '为什么', 'owner': '观众', 'container': '对话气泡'}]},
                                {'action': '提问气泡消失，主讲人点头回应', 'texts': []}],
                            'reference_beat': 1,
                            'reference_visual': '主讲人在讲台前，观众举手，观众的对话气泡显示“为什么”'}

    def test_motion_draft_omissions_are_deferred_to_finalizer(self):
        plan = dict(self.motion_plan, version=2,
                    reference_participants=['主讲人', '观众'],
                    reference_texts=self.motion_plan['beats'][0]['texts'],
                    reference_visual='讲台前的人面对台下举手的人群')
        calls = []
        def ask(*args):
            calls.append(args)
            return {'shots': [{'id': 'shot1', 'motion_plan': plan}]}
        row = direct_motion(self.context, [dict(self.shot, visual_description='讲台前的人群')], [], ask=ask)[0]
        self.assertEqual(len(calls), 1)
        self.assertTrue(row['handoff_notes'])
        self.assertEqual(row['motion_plan']['reference_texts'][0]['text'], '为什么')
        # Literal differences remain visible for review without losing the draft.
        finalized = write_image_prompts(
            self.context, '简笔画', [dict(self.shot, motion_plan=row['motion_plan'])], [],
            ask=lambda *_: {'shots': [{'id': 'shot1', 'image_prompt':
                '【人物与画风】简笔画【画面内容】讲台【必要限制】清晰'}]})[0]
        self.assertIn('【画面内容】讲台', finalized['image_prompt'])
        self.assertTrue(finalized['image_prompt_warnings'])

    def test_reference_placeholder_recovers_only_pictured_registered_labels(self):
        plan = copy.deepcopy(self.motion_plan)
        plan.update(version=2, reference_visual='图1',
                    reference_participants=['主讲人', '观众'], reference_texts=[])
        plan['beats'] = [
            {'action': '主讲人介绍案例', 'texts': [
                {'text': '案例预告', 'owner': '主讲人', 'container': '标签气泡'}]},
            {'action': '观众回应', 'texts': [
                {'text': '结尾总结', 'owner': '观众', 'container': '对话气泡'}]},
            {'action': '主讲人补充', 'texts': [
                {'text': '后续话题', 'owner': '主讲人', 'container': '标签气泡'}]},
        ]
        picture = ('主讲人在讲台前面对观众，主讲人的标签气泡写着“案例预告”。'
                   '不要显示观众的对话气泡“结尾总结”。')
        before = copy.deepcopy(plan)
        result = direct_motion(
            self.context, [dict(self.shot, visual_description=picture)], [],
            ask=lambda *_: {'shots': [{'id': 'shot1', 'motion_plan': plan}]})[0]['motion_plan']
        self.assertEqual(result['reference_visual'], picture)
        self.assertEqual(result['reference_texts'], [plan['beats'][0]['texts'][0]])
        self.assertEqual(result['beats'], before['beats'])
        self.assertEqual(plan, before)

    def test_complete_text_free_reference_is_not_overwritten_by_old_picture(self):
        for picture in ('主讲人面对观众，画面无文字。', '图1中主讲人面对观众，画面无文字。'):
            with self.subTest(picture=picture):
                plan = dict(self.motion_plan, version=2, reference_visual=picture,
                            reference_participants=['主讲人', '观众'], reference_texts=[])
                result = direct_motion(
                    self.context, [dict(self.shot, visual_description=self.motion_plan['reference_visual'])], [],
                    ask=lambda *_: {'shots': [{'id': 'shot1', 'motion_plan': plan}]})[0]['motion_plan']
                self.assertEqual(result['reference_visual'], picture)
                self.assertEqual(result['reference_texts'], [])

    def test_reference_placeholder_keeps_explicit_reference_text_selection(self):
        selected = [{'text': '内容示意', 'owner': '画面标注', 'container': '图例'}]
        plan = dict(self.motion_plan, version=2, reference_visual='图1',
                    reference_participants=['主讲人', '观众'], reference_texts=selected)
        result = direct_motion(
            self.context, [dict(self.shot, visual_description=self.motion_plan['reference_visual'])], [],
            ask=lambda *_: {'shots': [{'id': 'shot1', 'motion_plan': plan}]})[0]['motion_plan']
        self.assertEqual(result['reference_texts'], selected)

    def test_literal_image_wording_difference_is_saved_without_paid_repair(self):
        shot = dict(self.shot, motion_plan=self.motion_plan)
        calls, drafts = [], []
        text = ('【人物与画风】主讲人与观众，简笔画。'
                '【画面内容】观众的对话框里写“为什么”。【必要限制】清晰。')
        def ask(_system, payload):
            calls.append(copy.deepcopy(payload))
            return {'shots': [{'id': 'shot1', 'image_prompt': text}]}
        rows = write_image_prompts(self.context, '简笔画', [shot], [], ask=ask,
                                   on_draft=lambda rows: drafts.append(copy.deepcopy(rows)))
        self.assertEqual(len(calls), 1)
        self.assertTrue(rows[0]['image_prompt_warnings'])
        self.assertEqual(rows[0]['image_prompt'], '【本图旨在】' + shot['intent'] + '\n' + text)
        self.assertEqual(len(drafts), 1)
        for batch in drafts:
            self.assertTrue(batch[0]['image_prompt'].startswith('【本图旨在】' + shot['intent']))
            self.assertEqual(batch[0]['image_prompt'].count('【本图旨在】'), 1)
            self.assertTrue(batch[0]['image_prompt_warnings'])

    def test_first_candidate_is_preserved_when_repair_returns_wrong_shot(self):
        shot = dict(self.shot, motion_plan=self.motion_plan)
        drafts, calls = [], []
        def ask(_system, payload):
            calls.append(payload)
            return {'shots': [{'id': 'shot1' if len(calls) == 1 else 'missing',
                              'image_prompt': '【人物与画风】简笔画【画面内容】讲台【必要限制】清晰'}]}
        with self.assertRaisesRegex(ValueError, '缺失、重复或顺序'):
            write_image_prompts(self.context, '简笔画', [shot], [], ask=ask,
                                on_draft=lambda rows: drafts.append(copy.deepcopy(rows)))
        self.assertEqual(len(calls), 2)
        self.assertTrue(drafts)
        self.assertEqual(drafts[0][0]['id'], 'shot1')
        self.assertIn('【画面内容】讲台', drafts[0][0]['image_prompt'])

    def test_valid_image_candidate_survives_another_shots_empty_prompt(self):
        shots = [dict(self.shot, motion_plan=self.motion_plan), dict(self.shot, id='shot2')]
        drafts, calls = [], []
        valid = ('【人物与画风】主讲人与观众，简笔画。'
                 '【画面内容】观众的对话气泡显示“为什么”。【必要限制】清晰。')
        def ask(_system, payload):
            calls.append(payload)
            return {'shots': [{'id': 'shot1', 'image_prompt': valid}, {'id': 'shot2', 'image_prompt': ''}]}
        with self.assertRaisesRegex(ValueError, '缺少最终提示词'):
            write_image_prompts(self.context, '简笔画', shots, [], ask=ask,
                                on_draft=lambda rows: drafts.append(copy.deepcopy(rows)))
        self.assertEqual(len(calls), 2)
        saved = [row for batch in drafts for row in batch]
        self.assertTrue(any(row['id'] == 'shot1' and valid in row['image_prompt'] for row in saved))
        self.assertFalse(any(row['id'] == 'shot2' for row in saved))

    def test_each_agent_keeps_its_contract(self):
        planned = plan_groups(self.context, self.scenes, {}, ask=lambda _system, _data: {
            'shots': [{'slide_ids': ['scene_001'], 'kind': 'video', 'intent': '表达',
                       'motion_basis': '提问互动', 'progression_plan': '提问到回答'}]})
        self.assertEqual(planned[0]['kind'], 'video')
        directed = design_core_images(self.context, self.scenes, [self.shot], [], ask=lambda _system, _data: {
            'shots': [{'id': 'shot1', 'action': '先举手再示意', 'visual_description': '观众围着讲台举手', 'reference_ids': []}]})
        self.assertEqual(directed[0]['action'], '先举手再示意')
        images = write_image_prompts(self.context, '简笔画', [self.shot], [], ask=lambda _system, _data: {
            'shots': [{'id': 'shot1', 'image_prompt': '【人物与画风】简笔画\n【画面内容】举手\n【必要限制】无文字'}]})
        self.assertIn('【画面内容】', images[0]['image_prompt'])
        self.assertTrue(images[0]['image_prompt'].startswith('【本图旨在】'+self.shot['intent']))
        captured = {}
        def video_ask(_system, data):
            captured.update(data)
            return {'shots': [{'id': 'shot1', 'video_prompt': '参考图1的造型。观众举手，静音。'}]}
        videos = write_video_prompts(self.context, [self.shot], [], ask=video_ask)
        self.assertEqual(captured['shots'][0]['numbered_references'][0]['number'], '图1')
        self.assertIn('图1', videos[0]['video_prompt'])

    def test_agent_response_must_preserve_id_and_order(self):
        with self.assertRaisesRegex(ValueError, '缺失、重复或顺序'):
            design_core_images(self.context, self.scenes, [self.shot], [], ask=lambda *_: {
                'shots': [{'id': 'other', 'action': '', 'reference_ids': []}]})

    def test_long_dynamic_is_repaired_before_normalization(self):
        scenes = [{'slide_id': f's{i}', 'text': str(i), 'start': i*10, 'end': (i+1)*10} for i in range(2)]
        calls = []
        def ask(_system, data):
            calls.append(data)
            groups = [['s0', 's1']] if len(calls) == 1 else [['s0'], ['s1']]
            return {'shots': [dict(slide_ids=ids, kind='video', intent='逐步回答',
                                  motion_basis='提问回应', progression_plan='提问再回答',
                                  semantic=dict(message='本段问答', source_basis='本段字幕', fact_status='quoted')) for ids in groups]}
        rows = plan_groups(self.context, scenes, {}, ask=ask)
        self.assertEqual(len(calls), 2)
        self.assertIn('validation_errors', calls[1])
        self.assertEqual([r['slide_ids'] for r in rows], [['s0'], ['s1']])
        self.assertTrue(all(r['kind'] == 'video' for r in rows))

    def test_repair_cannot_hide_long_dynamic_by_downgrading(self):
        scenes = [{'slide_id': f's{i}', 'text': str(i), 'start': i*5, 'end': (i+1)*5} for i in range(6)]
        calls = []
        def ask(_system, data):
            calls.append(data)
            rows = [dict(slide_ids=[f's{i}'], kind='video', intent='开场', motion_basis='留存',
                         progression_plan='变化') for i in range(2)]
            rows.append(dict(slide_ids=['s2','s3','s4','s5'], kind='video' if len(calls)==1 else 'static',
                             intent='递进', motion_basis='过程', progression_plan='逐个出现'))
            return {'shots': rows}
        rows = plan_groups(self.context, scenes, {}, ask=ask)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(row['kind'] == 'video' for row in rows))
        self.assertTrue(all(row['duration_repair']['method'] == 'boundary_fallback' for row in rows[2:]))
        self.assertEqual([identity for row in rows for identity in row['slide_ids']], [f's{i}' for i in range(6)])

    def test_static_requires_visual_design_before_finalization(self):
        shot = dict(self.shot, kind='static', action='')
        with self.assertRaisesRegex(ValueError, '缺少核心画面设计'):
            design_core_images(self.context, self.scenes, [shot], [], ask=lambda *_: {
                'shots': [{'id': 'shot1', 'action': '', 'reference_ids': []}]})

    def test_final_prompt_preserves_purpose_and_receives_source_and_design(self):
        shot = dict(self.shot, source_subtitles=self.scenes,
                    visual_description='观众围着讲台举手',
                    visual_design={'visible_evidence': '多人主动提问'})
        def ask(_system, data):
            self.assertEqual(data['shots'][0]['source_subtitles'], self.scenes)
            self.assertEqual(data['shots'][0]['visual_description'], shot['visual_description'])
            return {'shots': [{'id': 'shot1', 'image_prompt':
                '【本图旨在】模型改写的目的\n【人物与画风】简笔画\n【画面内容】多人举手\n【必要限制】清晰'}]}
        prompt = write_image_prompts(self.context, '简笔画', [shot], [], ask=ask)[0]['image_prompt']
        self.assertEqual(prompt.count('【本图旨在】'), 1)
        self.assertTrue(prompt.startswith('【本图旨在】'+shot['intent']+'\n'))
        self.assertNotIn('模型改写的目的', prompt)

    def test_motion_uses_scene_draft_before_final_image_and_only_dynamic_shots(self):
        from copy import deepcopy
        plan = dict(self.motion_plan, version=2, reference_participants=['主讲人', '观众'],
                    reference_texts=self.motion_plan['beats'][0]['texts'])
        shot = dict(self.shot, image_prompt='【本图旨在】提问热烈\n最终核心图全文',
                    visual_description='主讲人与观众互动的核心画面草案',
                    source_subtitles=self.scenes)
        before = deepcopy(shot)
        def ask(_system, data):
            self.assertEqual(len(data['shots']), 1)
            self.assertNotIn('image_prompt', data['shots'][0])
            self.assertEqual(data['shots'][0]['visual_description'], shot['visual_description'])
            self.assertEqual(data['shots'][0]['source_subtitles'], self.scenes)
            return {'shots': [{'id': 'shot1', 'motion_plan': plan}]}
        result = direct_motion(self.context, [shot, dict(shot, id='static', kind='static')], [], ask=ask)
        self.assertTrue(result[0]['action'])
        self.assertEqual(result[0]['motion_plan'], plan)
        self.assertIn('为什么', result[0]['action'])
        self.assertEqual(shot, before)
        def forbidden(*args):
            self.fail('静态镜头不应调用动态 Agent')
        self.assertEqual(direct_motion(self.context, [dict(shot, kind='static')], [], ask=forbidden), [])
        self.assertEqual(write_video_prompts(self.context, [dict(shot, kind='static')], [], ask=forbidden), [])
        with self.assertRaisesRegex(ValueError, '先完成核心画面草案'):
            direct_motion(self.context, [self.shot], [], ask=forbidden)

    def test_shared_plan_survives_both_finalizers_and_keeps_bubble_text(self):
        shot = dict(self.shot, motion_plan=self.motion_plan, visual_description='旧草案',
                    intent='观众从提问到回应')
        def images(system, data):
            self.assertEqual(data['shots'][0]['motion_plan'], self.motion_plan)
            self.assertIn('reference_visual', system)
            return {'shots': [{'id': 'shot1', 'image_prompt':
                '【人物与画风】主讲人与观众，简笔画【画面内容】观众的对话气泡显示“为什么”【必要限制】不添加额外字幕'}]}
        shot.update(write_image_prompts(self.context, '简笔画', [shot], [], ask=images)[0])
        def videos(system, data):
            self.assertEqual(data['shots'][0]['motion_plan'], self.motion_plan)
            self.assertEqual(data['shots'][0]['image_prompt'], shot['image_prompt'])
            self.assertNotIn('禁止生成文字，默认静音', system)
            return {'shots': [{'id': 'shot1', 'video_prompt':
                '图1为核心参考，主讲人面对观众。观众的对话气泡出现“为什么”，随后消失，主讲人点头。静音。'}]}
        self.assertIn('为什么', write_video_prompts(self.context, [shot], [], ask=videos)[0]['video_prompt'])

    def test_finalizer_repairs_concrete_text_conflict_once(self):
        shot = dict(self.shot, motion_plan=self.motion_plan)
        calls = []
        def ask(system, data):
            calls.append(data)
            text = '图1为核心参考，主讲人与观众，观众的对话气泡显示“为什么”。'
            return {'shots': [{'id': 'shot1', 'video_prompt': text + ('禁止生成文字。' if len(calls) == 1 else '随后气泡消失。')}]}
        result = write_video_prompts(self.context, [shot], [], ask=ask)
        self.assertEqual(len(calls), 2)
        self.assertIn('validation_errors', calls[1])
        self.assertNotIn('禁止生成文字', result[0]['video_prompt'])

    def test_invalid_motion_plan_has_one_bounded_repair(self):
        calls = []
        def ask(system, data):
            calls.append(data)
            return {'shots': [{'id': 'shot1', 'motion_plan': dict(self.motion_plan, reference_beat=99)}]}
        with self.assertRaisesRegex(ValueError, '动态阶段方案修订仍未通过'):
            direct_motion(self.context, [dict(self.shot, visual_description='核心草案')], [], ask=ask)
        self.assertEqual(len(calls), 2)

    def test_purpose_not_mistaken_for_visible_text_from_another_phase(self):
        plan = dict(self.motion_plan, reference_beat=2,
                    reference_visual='主讲人面对观众点头，无文字')
        shot = dict(self.shot, motion_plan=plan, intent='观众在追问“为什么”之后得到回应')
        result = write_image_prompts(self.context, '简笔画', [shot], [], ask=lambda *_: {'shots': [
            {'id': 'shot1', 'image_prompt': '【人物与画风】主讲人与观众，简笔画【画面内容】主讲人点头【必要限制】无文字'}]})
        self.assertIn('“为什么”', result[0]['image_prompt'])

    def test_deterministic_audit_accepts_complete_contract(self):
        shot = dict(self.shot, image_prompt='【人物与画风】简笔画\n【画面内容】举手\n【必要限制】无文字',
                    video_prompt='参考图1的人物造型，观众举手，静音。')
        result = audit_storyboard([shot], set())
        self.assertTrue(result[0]['audit']['ready'])

    def test_deterministic_audit_blocks_missing_core_reference(self):
        shot = dict(self.shot, image_prompt='【人物与画风】简笔画\n【画面内容】举手\n【必要限制】无文字',
                    video_prompt='观众举手，静音。')
        with self.assertRaisesRegex(ValueError, '图1核心分镜'):
            audit_storyboard([shot], set())

    def test_heading_variants_are_not_user_warnings_or_discarded_content(self):
        for prompt in (
            '【人物与画风】简笔画【画面内容】观众举手【画面文字与归属】菜单上显示套餐',
            '【画面内容】观众举手【人物与画风】简笔画【必要限制】保持角色',
            '简笔画，观众举手，讲者侧身倾听。',
        ):
            shot = dict(self.shot, image_prompt=prompt, video_prompt='参考图1，观众举手。')
            result = audit_storyboard([shot], set())[0]
            self.assertTrue(result['audit']['ready'])
            self.assertEqual(result['image_prompt'], prompt)
            self.assertFalse(result['image_prompt_warnings'])
        with self.assertRaisesRegex(ValueError, '缺少核心分镜图提示词'):
            audit_storyboard([dict(self.shot, image_prompt='', video_prompt='参考图1')], set())

    def test_audit_retires_old_format_warning_only(self):
        shot = dict(self.shot, image_prompt='简笔画，观众举手', video_prompt='图1为参考',
                    image_prompt_warnings=['提示词分节标题不完整或顺序不同，内容已保留', '缺少短文字原文：为什么'])
        result = audit_storyboard([shot], set())[0]
        self.assertEqual(result['image_prompt_warnings'], ['缺少短文字原文：为什么'])
        self.assertEqual(len(shot['image_prompt_warnings']), 2)

    def test_static_shot_cannot_retain_motion_instruction(self):
        shot = dict(self.shot, kind='static', generation_duration=None,
                    image_prompt='【人物与画风】简笔画\n【画面内容】讲台\n【必要限制】无文字',
                    video_prompt='移动', action='移动')
        with self.assertRaisesRegex(ValueError, '静态镜头残留'):
            audit_storyboard([shot], set())


if __name__ == '__main__': unittest.main()
