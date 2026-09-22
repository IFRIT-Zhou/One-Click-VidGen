"""Offline policy handoffs, not a benchmark of an LLM's visual understanding."""
import copy
import unittest
from unittest.mock import patch

from backend.app import video_agents, video_prompt_refresh
from backend.app.video_director_contracts import TEXT_CONTRACT
from backend.app.video_director_examples import (
    CORE_DESIGN_EXAMPLE, MOTION_DESIGN_EXAMPLE,
    VISUAL_FIRST_CORE_DESIGN_EXAMPLE, VISUAL_FIRST_MOTION_DESIGN_EXAMPLE,
)
from backend.app.video_text_policy import (
    VISUAL_FIRST_CONTRACT, dynamic_text_mode, normalize_text_mode, text_mode_contract,
    visual_first_plan_issues, visual_first_prompt_issues,
)


class VideoTextPolicyTest(unittest.TestCase):
    def setUp(self):
        self.context = {'summary': '旅行者提出旅行想法，同伴回应。',
                        'video_direction': {'dynamic_text_mode': 'visual_first'}}
        self.scenes = [{'slide_id': 's1', 'text': '旅行者问：我们去海边旅行好吗？同伴微笑点头。',
                        'start': 0, 'end': 5}]
        self.turns = [{'source_text': '我们去海边旅行好吗', 'speaker': '旅行者', 'addressee': '同伴',
                       'mode': 'spoken', 'basis': '原文明示旅行者向同伴提出设想'}]
        self.visual = ('旅行者面对同伴摊手，旅行者的图案想象气泡里是阳光下的海岸和帆船，'
                       '同伴微笑点头，不画对话句子。')
        self.plan = {
            'version': 2, 'scene_anchor': '旅行者和同伴在同一张桌子前',
            'participants': ['旅行者', '同伴'],
            'beats': [
                {'action': '旅行者向同伴摊手，旅行者的图案想象气泡出现海岸和帆船。', 'texts': []},
                {'action': '同伴微笑点头，旅行者收回手，海岸与帆船气泡消失。', 'texts': []},
            ],
            'reference_beat': 1, 'reference_visual': self.visual,
            'reference_participants': ['旅行者', '同伴'], 'reference_texts': [],
        }
        self.shot = {
            'id': 'shot1', 'slide_ids': ['s1'], 'kind': 'video', 'duration': 5, 'generation_duration': 5,
            'intent': '旅行者提出旅行设想并得到同伴赞同', 'source_subtitles': self.scenes,
            'semantic': {'message': '提问和回应', 'speech_turns': self.turns},
            'visual_description': self.visual, 'reference_ids': [], 'motion_plan': self.plan,
            'action': '旅行者摊手，同伴点头',
        }

    def response(self, stage):
        if stage == 'design':
            return {'shots': [{'id': 'shot1', 'visual_description': self.visual, 'reference_ids': [],
                               'semantic': {'speech_turns': copy.deepcopy(self.turns)}}]}
        if stage in ('motion', 'refresh'):
            return {'shots': [{'id': 'shot1', 'motion_plan': copy.deepcopy(self.plan)}]}
        if stage == 'image':
            return {'shots': [{'id': 'shot1', 'image_prompt':
                '【人物与画风】旅行者与同伴，简约手绘。'
                '【画面内容】' + self.visual + '【必要限制】保持主体归属。'}]}
        return {'shots': [{'id': 'shot1', 'continuity_prompt': '旅行者与同伴保持造型，参照图1的手绘场景。',
                           'beat_prompts': [
                               {'beat': 1, 'prompt': self.plan['beats'][0]['action']},
                               {'beat': 2, 'prompt': self.plan['beats'][1]['action']},
                           ], 'ending_prompt': '旅行者与同伴微笑相对，气泡已经消失，余下时间自然停留，静音。'}]}

    def run_stage(self, stage, context, ask):
        if stage == 'design':
            return video_agents.design_core_images(context, self.scenes, [self.shot], [], ask=ask)
        if stage == 'motion':
            return video_agents.direct_motion(context, [self.shot], [], ask=ask)
        if stage == 'image':
            return video_agents.write_image_prompts(context, '简约手绘', [self.shot], [], ask=ask)
        if stage == 'video':
            return video_agents.write_video_prompts(context, [self.shot], [], ask=ask)
        return video_prompt_refresh.revise_motion(
            context, self.shot, [], self.shot['action'], self.visual, 'image_prompt', ask=ask,
            refresh_basis='action')

    def test_missing_and_invalid_settings_keep_legacy_default(self):
        for value in (None, '', 'unknown', 1, [], {}, 'text_assisted'):
            with self.subTest(value=value):
                self.assertEqual(normalize_text_mode(value), 'text_assisted')
        for context in ({}, {'video_direction': None}, {'video_direction': {}},
                        {'video_direction': {'dynamic_text_mode': 'unknown'}}):
            self.assertEqual(dynamic_text_mode(context), 'text_assisted')
            self.assertEqual(text_mode_contract(context), TEXT_CONTRACT)
        self.assertEqual(normalize_text_mode('visual_first'), 'visual_first')

    def test_every_creative_finalizing_and_refresh_agent_receives_policy_and_source_owners(self):
        before = copy.deepcopy((self.context, self.shot))
        for stage in ('design', 'motion', 'image', 'video', 'refresh'):
            with self.subTest(stage=stage):
                calls = []

                def ask(system, payload):
                    calls.append((system, copy.deepcopy(payload)))
                    self.assertIn(VISUAL_FIRST_CONTRACT, system)
                    self.assertNotIn(TEXT_CONTRACT, system)
                    self.assertEqual(payload['story_context']['video_direction']['dynamic_text_mode'], 'visual_first')
                    supplied = payload['shot'] if stage == 'refresh' else payload['shots'][0]
                    self.assertEqual(supplied['semantic']['speech_turns'], self.turns)
                    self.assertEqual(supplied['source_subtitles'], self.scenes)
                    self.assertIn('内部语义依据', system)
                    self.assertIn('菜单', system)
                    self.assertIn('不强制预留空气泡', system)
                    return self.response(stage)

                result = self.run_stage(stage, self.context, ask)
                self.assertEqual(len(calls), 1)
                if stage == 'design':
                    self.assertEqual(result[0]['semantic']['speech_turns'], self.turns)
                    example = calls[0][1]['method_example']
                    self.assertIn('illustrative_image_prompt', example)
                    self.assertNotEqual(example, CORE_DESIGN_EXAMPLE)
                if stage == 'motion':
                    self.assertEqual(result[0]['motion_plan']['reference_texts'], [])
                    self.assertIn('海岸和帆船', result[0]['action'])
                    self.assertNotEqual(calls[0][1]['method_example'], MOTION_DESIGN_EXAMPLE)
                if stage in ('image', 'video'):
                    prompt = result[0][stage + '_prompt']
                    self.assertIn('同伴', prompt)
                    self.assertNotIn(self.turns[0]['source_text'], prompt)
                    self.assertEqual(result[0][stage + '_prompt_warnings'], [])
        self.assertEqual((self.context, self.shot), before)

    def test_policy_and_examples_survive_existing_bounded_repair_paths(self):
        for stage in ('design', 'motion', 'image', 'video'):
            with self.subTest(stage=stage):
                calls = []

                def ask(system, payload):
                    calls.append((system, copy.deepcopy(payload)))
                    return {'shots': []} if len(calls) == 1 else self.response(stage)

                self.run_stage(stage, self.context, ask)
                self.assertEqual(len(calls), 2)
                for system, payload in calls:
                    self.assertIn(VISUAL_FIRST_CONTRACT, system)
                    self.assertNotIn(TEXT_CONTRACT, system)
                    self.assertEqual(dynamic_text_mode(payload['story_context']), 'visual_first')
                self.assertIn('validation_errors', calls[1][1])
                if stage in ('design', 'motion'):
                    self.assertEqual(calls[0][1]['method_example'], calls[1][1]['method_example'])

    def test_explicit_text_assisted_and_missing_setting_use_identical_original_instructions(self):
        for stage in ('design', 'motion', 'image', 'video', 'refresh'):
            with self.subTest(stage=stage):
                captures = []
                for context in ({}, {'video_direction': {'dynamic_text_mode': 'text_assisted'}}):
                    def ask(system, payload):
                        captures.append((system, copy.deepcopy(payload)))
                        return self.response(stage)
                    self.run_stage(stage, context, ask)
                self.assertEqual(captures[0][0], captures[1][0])
                self.assertNotIn(VISUAL_FIRST_CONTRACT, captures[0][0])
                if stage == 'refresh':
                    self.assertEqual(captures[0][0], video_prompt_refresh.MOTION_SYSTEM)
                else:
                    self.assertIn(TEXT_CONTRACT, captures[0][0])
                if stage == 'design':
                    self.assertEqual(captures[0][1]['method_example'], CORE_DESIGN_EXAMPLE)
                if stage == 'motion':
                    self.assertEqual(captures[0][1]['method_example'], MOTION_DESIGN_EXAMPLE)

    def test_necessary_diegetic_menu_label_is_not_filtered_or_rejected(self):
        label = {'text': '午市套餐 28元', 'owner': '菜单', 'container': '菜单价目栏'}
        plan = {'version': 2, 'scene_anchor': '餐厅桌面', 'participants': ['顾客', '菜单'],
                'beats': [{'action': '顾客指向菜单价目栏上的“午市套餐 28元”。', 'texts': [label]}],
                'reference_beat': 1, 'reference_visual': '顾客手指停在菜单价目栏的“午市套餐 28元”旁。',
                'reference_participants': ['顾客', '菜单'], 'reference_texts': [label]}
        shot = dict(self.shot, motion_plan=plan)
        image_prompt = ('【人物与画风】顾客与菜单，手绘。'
                        '【画面内容】顾客指向菜单价目栏的“午市套餐 28元”。【必要限制】不加字幕条。')
        rows = video_agents.write_image_prompts(self.context, '手绘', [shot], [], ask=lambda *_: {
            'shots': [{'id': 'shot1', 'image_prompt': image_prompt}]})
        self.assertIn(label['text'], rows[0]['image_prompt'])
        self.assertEqual(rows[0]['image_prompt_warnings'], [])
        rows = video_agents.write_video_prompts(self.context, [shot], [], ask=lambda *_: {
            'shots': [{'id': 'shot1', 'continuity_prompt': '顾客与菜单保持图1造型。',
                       'beat_prompts': [{'beat': 1, 'prompt': plan['beats'][0]['action']}],
                       'ending_prompt': '保持顾客的手指与菜单价目栏，自然停留，静音。'}]})
        self.assertIn(label['text'], rows[0]['video_prompt'])
        self.assertEqual(rows[0]['video_prompt_warnings'], [])

    def test_visual_first_allows_selective_reaction_words_without_explicit_user_request(self):
        # No user-written text instruction: this is a choice made by the core
        # director because this evaluation is clearer as a concise utterance.
        label = {'text': '太贵了', 'owner': '顾客', 'container': '对话气泡'}
        visual = '顾客查看价目牌后皱眉缩回手，顾客的短小对话气泡写着“太贵了”。'
        plan = {'version': 2, 'scene_anchor': '小店柜台', 'participants': ['顾客'],
                'beats': [{'action': visual, 'texts': [label]}],
                'reference_beat': 1, 'reference_visual': visual,
                'reference_participants': ['顾客'], 'reference_texts': [label]}
        shot = dict(self.shot, visual_description=visual, motion_plan=plan,
                    intent='顾客认为价格高而放弃购买', semantic={'speech_turns': []},
                    source_subtitles=[{'slide_id': 's1', 'text': '看清价格后，他摇摇头离开了。',
                                       'start': 0, 'end': 5}])
        self.assertNotIn('太贵了', shot['source_subtitles'][0]['text'])
        for clause in ('只有用户明确要求保留时才例外', '不重新添加带字对白'):
            self.assertNotIn(clause, VISUAL_FIRST_CONTRACT)
        self.assertIn('不需要等用户逐字授权', VISUAL_FIRST_CONTRACT)
        self.assertIn('不按固定字数', VISUAL_FIRST_CONTRACT)

        image_prompt = ('【人物与画风】顾客，简约手绘。'
                        '【画面内容】' + visual + '【必要限制】不增加其他画中文字。')
        rows = video_agents.write_image_prompts(self.context, '简约手绘', [shot], [], ask=lambda *_: {
            'shots': [{'id': 'shot1', 'image_prompt': image_prompt}]})
        self.assertIn(label['text'], rows[0]['image_prompt'])
        self.assertEqual(rows[0]['image_prompt_warnings'], [])
        rows = video_agents.write_video_prompts(self.context, [shot], [], ask=lambda *_: {
            'shots': [{'id': 'shot1', 'continuity_prompt': '保持图1中顾客的造型与小店柜台场景。',
                       'beat_prompts': [{'beat': 1, 'prompt': visual}],
                       'ending_prompt': '顾客转身离开，短小对话气泡消失，静音。'}]})
        self.assertIn(label['text'], rows[0]['video_prompt'])
        self.assertEqual(rows[0]['video_prompt_warnings'], [])

        captured = []
        def ask(system, payload):
            captured.append(system)
            return {'shots': [{'id': 'shot1', 'motion_plan': copy.deepcopy(plan)}]}
        revised = video_prompt_refresh.revise_motion(
            self.context, shot, [], '顾客看清价格后缩回手，最后转身离开。',
            visual, 'image_prompt', ask=ask, refresh_basis='action')
        self.assertEqual(revised['reference_texts'], [label])
        self.assertIn('不要求用户必须明确授权每个词', captured[0])

    def test_visual_first_examples_combine_pictorial_travel_with_one_useful_short_reaction(self):
        image = VISUAL_FIRST_CORE_DESIGN_EXAMPLE['illustrative_image_prompt']
        video = VISUAL_FIRST_MOTION_DESIGN_EXAMPLE['illustrative_video_body']
        for prompt in (image, video):
            self.assertIn('“汉奸”', prompt)
            self.assertIn('观众', prompt)
            self.assertIn('没有地名或问句' if prompt == image else '不显示地名或问句', prompt)
            self.assertNotIn('没有带字对白', prompt)
        self.assertIn('不是通用模板', VISUAL_FIRST_CORE_DESIGN_EXAMPLE['scope'])
        self.assertIn('菜单', VISUAL_FIRST_MOTION_DESIGN_EXAMPLE['scope'])

    def test_direct_mechanism_scene_needs_no_person_or_bubble(self):
        plan = {'version': 2, 'scene_anchor': '剖面管道', 'participants': ['阀门', '水流'],
                'beats': [{'action': '阀门逐渐合拢，水流从连续水柱变成细流。', 'texts': []}],
                'reference_beat': 1, 'reference_visual': '剖面管道内，阀门半开，水流穿过开口。',
                'reference_participants': ['阀门', '水流'], 'reference_texts': []}
        shot = dict(self.shot, intent='阀门开口缩小使水流减弱', semantic={'speech_turns': []},
                    visual_description=plan['reference_visual'], motion_plan=plan)
        draft = video_agents.direct_motion(self.context, [shot], [], ask=lambda *_: {
            'shots': [{'id': 'shot1', 'motion_plan': plan}]})[0]
        self.assertNotIn('气泡', draft['motion_plan']['reference_visual'])
        self.assertEqual(draft['motion_plan']['participants'], ['阀门', '水流'])
        prompt = ('【人物与画风】技术剖面示意，无人物出镜。'
                  '【画面内容】剖面管道内，阀门半开，水流穿过开口。【必要限制】结构清晰。')
        rows = video_agents.write_image_prompts(self.context, '技术示意', [shot], [], ask=lambda *_: {
            'shots': [{'id': 'shot1', 'image_prompt': prompt}]})
        self.assertEqual(rows[0]['image_prompt_warnings'], [])
        self.assertNotIn('气泡', rows[0]['image_prompt'])

    def test_image_based_refresh_keeps_observed_text_as_fact_not_new_dialogue_instruction(self):
        observed = copy.deepcopy(self.plan)
        label = {'text': '我们去海边旅行好吗', 'owner': '旅行者', 'container': '对话气泡'}
        observed['reference_visual'] = '旅行者面对同伴，旅行者的对话气泡写着“我们去海边旅行好吗”。'
        observed['reference_texts'] = [label]
        captured = []

        def ask(system, payload):
            captured.append((system, payload))
            return {'shots': [{'id': 'shot1', 'motion_plan': observed}]}

        result = video_prompt_refresh.revise_motion(
            self.context, self.shot, [], '旧气泡消失，同伴微笑点头。', observed['reference_visual'],
            'image_analysis', ask=ask, refresh_basis='image')
        self.assertEqual(result['reference_texts'], [label])
        self.assertEqual(result['beats'], self.plan['beats'])
        self.assertIn('不能为符合模式而虚构', captured[0][0])
        self.assertEqual(captured[0][1]['core_basis'], observed['reference_visual'])

    def test_action_and_image_refresh_forward_same_selected_context(self):
        for basis in ('action', 'image'):
            with self.subTest(basis=basis):
                with patch.object(video_prompt_refresh, 'revise_motion', return_value=self.plan) as motion, \
                     patch.object(video_prompt_refresh, 'write_image_prompts', return_value=[
                         {'id': 'shot1', 'image_prompt': '新画面'}]) as image, \
                     patch.object(video_prompt_refresh, 'write_video_prompts', return_value=[
                         {'id': 'shot1', 'video_prompt': '新动作'}]) as video:
                    video_prompt_refresh.refresh(self.context, '手绘', self.shot, [], basis=basis,
                        action='旅行者摊手，同伴点头', image_prompt=self.visual)
                self.assertEqual(motion.call_args.args[0], self.context)
                self.assertEqual(video.call_args.args[0], self.context)
                if basis == 'action':
                    self.assertEqual(image.call_args.args[0], self.context)
                else:
                    image.assert_not_called()

    def test_semantic_grouping_system_does_not_change_with_mode(self):
        captures = []
        for context in ({}, self.context):
            def ask(system, payload):
                captures.append(system)
                return {'shots': [{'slide_ids': ['s1'], 'kind': 'video',
                                   'intent': self.shot['intent'], 'motion_basis': '提问和回应',
                                   'progression_plan': '提问后点头'}]}
            video_agents.plan_groups(context, self.scenes, {}, ask=ask)
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0], captures[1])
        self.assertNotIn(VISUAL_FIRST_CONTRACT, captures[1])

    def test_visual_first_rejects_full_narration_bubble_but_keeps_short_scene_text(self):
        bad = copy.deepcopy(self.plan)
        bad['beats'][0]['texts'] = [{
            'text': '那么西方国家对中国的形容是正确的吗？',
            'owner': '旅行者', 'container': '对话气泡'}]
        self.assertTrue(visual_first_plan_issues(bad))
        self.assertTrue(visual_first_prompt_issues(
            '旅行者的对话气泡显示“那么西方国家对中国的形容是正确的吗？”。', self.plan))
        menu = copy.deepcopy(self.plan)
        menu['beats'][0]['texts'] = [{
            'text': '午市套餐 28元', 'owner': '旅行者', 'container': '菜单价目栏'}]
        self.assertEqual(visual_first_plan_issues(menu), [])

    def test_video_finalizer_repairs_unselected_full_dialogue_once(self):
        calls = []
        def ask(_system, _payload):
            calls.append(1)
            first = ('旅行者的对话气泡显示“那么西方国家对中国的形容是正确的吗？”。'
                     if len(calls) == 1 else '旅行者摊开双手，左右两侧依次浮现具体景物图案。')
            return {'shots': [{'id': 'shot1', 'continuity_prompt': '保持图1的人物与场景。',
                               'beat_prompts': [
                                   {'beat': 1, 'prompt': first},
                                   {'beat': 2, 'prompt': '同伴观察两侧图案后点头。'},
                               ], 'ending_prompt': '两人停在思考状态，图案自然淡出，静音。'}]}
        result = video_agents.write_video_prompts(self.context, [self.shot], [], ask=ask)
        self.assertEqual(len(calls), 2)
        self.assertNotIn('西方国家', result[0]['video_prompt'])
        self.assertIn('具体景物图案', result[0]['video_prompt'])


if __name__ == '__main__':
    unittest.main()
