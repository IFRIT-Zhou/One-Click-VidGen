import copy
import unittest

from backend.app.video_motion_plan import (
    normalize_motion_plan,
    prompt_plan_issues,
    render_motion_action,
)


class VideoMotionPlanTest(unittest.TestCase):
    def setUp(self):
        self.plan = {
            'version': 1,
            'scene_anchor': '同一张实验桌，器材位置保持一致',
            'participants': ['温度计', '烧杯'],
            'beats': [
                {'action': '镜头展示装置的初始状态', 'texts': [
                    {'text': '尚未加热', 'owner': '温度计', 'container': '状态卡'},
                ]},
                {'action': '液面微动，温度刻度缓慢变化', 'texts': [
                    {'text': '开始升温', 'owner': '烧杯', 'container': '提示框'},
                ]},
                {'action': '变化结束，装置保持稳定', 'texts': []},
            ],
            'reference_beat': 2,
            'reference_visual': '实验桌的单张俯视画面，器材处于变化中的代表状态',
        }

    def image_prompt(self):
        return '温度计与烧杯保持原位。烧杯旁显示提示框，精确文字为“开始升温”。'

    def video_prompt(self):
        return (
            '温度计与烧杯保持原位。先由温度计的状态卡显示“尚未加热”；'
            '随后移除该卡，由烧杯的提示框显示“开始升温”；最后移除提示框。'
            '不额外生成字幕或标题。'
        )

    def test_empty_legacy_plan_is_accepted(self):
        self.assertEqual(normalize_motion_plan(None), {})
        self.assertEqual(normalize_motion_plan({}), {})

    def test_canonical_plan_round_trip_does_not_mutate_input(self):
        before = copy.deepcopy(self.plan)
        normalized = normalize_motion_plan(self.plan)
        self.assertEqual(normalized, before)
        self.assertEqual(self.plan, before)
        normalized['beats'][0]['texts'][0]['text'] = '修改副本'
        normalized['participants'].append('副本主体')
        self.assertEqual(self.plan, before)

    def test_no_people_and_no_text_are_valid(self):
        plan = dict(self.plan, participants=[], beats=[
            {'action': '光斑从桌面左侧移向右侧', 'texts': []},
        ], reference_beat=1, reference_visual='桌面上只有一个光斑')
        normalized = normalize_motion_plan(plan)
        self.assertEqual(normalized['participants'], [])
        self.assertEqual(normalized['beats'][0]['texts'], [])

    def test_nonhuman_subject_can_own_text(self):
        normalized = normalize_motion_plan(self.plan)
        self.assertEqual(normalized['beats'][1]['texts'][0]['owner'], '烧杯')

    def test_scene_annotation_does_not_require_a_participant(self):
        plan = dict(self.plan, participants=[], beats=[
            {'action': '光斑缓慢移动', 'texts': [
                {'text': '移动方向', 'owner': '画面标注', 'container': '箭头旁标签'},
            ]},
        ], reference_beat=1)
        normalized = normalize_motion_plan(plan)
        self.assertEqual(normalized['beats'][0]['texts'][0]['owner'], '画面标注')

    def test_required_fields_cannot_be_missing(self):
        for key in self.plan:
            with self.subTest(key=key):
                plan = copy.deepcopy(self.plan)
                del plan[key]
                with self.assertRaises(ValueError):
                    normalize_motion_plan(plan)

    def test_top_level_types_are_rejected(self):
        for value in ([], '', 'plan', 1, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_motion_plan(value)

    def test_version_type_and_value_are_checked(self):
        for value in (0, 2, '1', True, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_motion_plan(dict(self.plan, version=value))

    def test_scene_and_reference_visual_require_bounded_nonempty_strings(self):
        for key, limit in (('scene_anchor', 2000), ('reference_visual', 6000)):
            for value in ('', '   ', None, 3, [], '字' * (limit + 1)):
                with self.subTest(key=key, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        normalize_motion_plan(dict(self.plan, **{key: value}))
            self.assertEqual(
                normalize_motion_plan(dict(self.plan, **{key: '字' * limit}))[key],
                '字' * limit,
            )

    def test_participants_require_unique_strings_and_limit(self):
        for value in ('温度计', None, [1], ['温度计', '温度计'],
                      ['主体' + str(index) for index in range(13)]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_motion_plan(dict(self.plan, participants=value))

    def test_beats_require_one_to_six_objects(self):
        for value in (None, {}, [], ['动作'], [{'action': '移动', 'texts': []}] * 7):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_motion_plan(dict(self.plan, beats=value))
        plan = dict(self.plan, beats=[{'action': '移动', 'texts': []}] * 6)
        self.assertEqual(len(normalize_motion_plan(plan)['beats']), 6)

    def test_action_requires_bounded_nonempty_string(self):
        for value in ('', '   ', None, 1, [], '动' * 4001):
            with self.subTest(value_type=type(value).__name__):
                plan = copy.deepcopy(self.plan)
                plan['beats'][0]['action'] = value
                with self.assertRaises(ValueError):
                    normalize_motion_plan(plan)
        plan = copy.deepcopy(self.plan)
        plan['beats'][0]['action'] = '动' * 4000
        self.assertEqual(normalize_motion_plan(plan)['beats'][0]['action'], '动' * 4000)

    def test_texts_require_explicit_list_and_four_item_limit(self):
        text = self.plan['beats'][0]['texts'][0]
        for value in (None, {}, '文字', [None], [text] * 5):
            with self.subTest(value=value):
                plan = copy.deepcopy(self.plan)
                plan['beats'][0]['texts'] = value
                with self.assertRaises(ValueError):
                    normalize_motion_plan(plan)
        for key in ('action', 'texts'):
            with self.subTest(missing=key):
                plan = copy.deepcopy(self.plan)
                del plan['beats'][0][key]
                with self.assertRaises(ValueError):
                    normalize_motion_plan(plan)

    def test_text_fields_require_bounded_nonempty_strings(self):
        for key, limit in (('text', 80), ('owner', 160), ('container', 80)):
            for value in ('', '   ', None, 1, [], '字' * (limit + 1)):
                with self.subTest(key=key, value_type=type(value).__name__):
                    plan = copy.deepcopy(self.plan)
                    plan['beats'][0]['texts'][0][key] = value
                    with self.assertRaises(ValueError):
                        normalize_motion_plan(plan)
            plan = copy.deepcopy(self.plan)
            del plan['beats'][0]['texts'][0][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                normalize_motion_plan(plan)

    def test_unknown_text_owner_is_rejected(self):
        plan = copy.deepcopy(self.plan)
        plan['beats'][0]['texts'][0]['owner'] = '未登记主体'
        with self.assertRaises(ValueError):
            normalize_motion_plan(plan)

    def test_reference_beat_is_one_based_integer_not_boolean(self):
        for value in (0, -1, 4, 1.0, '2', True, False, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_motion_plan(dict(self.plan, reference_beat=value))
        self.assertEqual(normalize_motion_plan(self.plan)['reference_beat'], 2)

    def test_text_states_are_not_accumulated_or_filled_forward(self):
        normalized = normalize_motion_plan(self.plan)
        self.assertEqual([row['texts'] for row in normalized['beats']], [
            [{'text': '尚未加热', 'owner': '温度计', 'container': '状态卡'}],
            [{'text': '开始升温', 'owner': '烧杯', 'container': '提示框'}],
            [],
        ])

    def test_render_preserves_stage_order_and_all_text_fields_without_mutation(self):
        before = copy.deepcopy(self.plan)
        action = render_motion_action(self.plan)
        self.assertIsInstance(action, str)
        self.assertIn(self.plan['scene_anchor'], action)
        for participant in self.plan['participants']:
            self.assertIn(participant, action)
        positions = [action.index(beat['action']) for beat in self.plan['beats']]
        self.assertEqual(positions, sorted(positions))
        for beat in self.plan['beats']:
            for text in beat['texts']:
                for value in text.values():
                    self.assertIn(value, action)
        self.assertEqual(self.plan, before)

    def test_render_adds_neither_timing_units_nor_subject_template(self):
        action = render_motion_action(self.plan)
        self.assertNotRegex(action, r'\d+\s*(?:秒|毫秒|分钟|seconds?|frames?|帧)')
        for unrelated in ('主讲人', '观众提问', '会场', '讲台'):
            self.assertNotIn(unrelated, action)

    def test_image_uses_only_second_reference_stage_text(self):
        self.assertFalse(prompt_plan_issues(self.image_prompt(), self.plan, medium='image'))
        prompt = self.image_prompt() + '温度计的状态卡同时显示“尚未加热”。'
        self.assertTrue(prompt_plan_issues(prompt, self.plan, medium='image'))

    def test_image_checks_exact_text_not_paraphrase(self):
        prompt = self.image_prompt().replace('开始升温', '温度上升')
        self.assertTrue(prompt_plan_issues(prompt, self.plan, medium='image'))

    def test_prompt_checks_missing_participant(self):
        prompt = self.image_prompt().replace('温度计', '仪器')
        self.assertTrue(prompt_plan_issues(prompt, self.plan, medium='image'))

    def test_prompt_checks_missing_owner_and_container(self):
        for original, substitute in (('烧杯', '容器'), ('提示框', '提示')):
            with self.subTest(field=original):
                prompt = self.image_prompt().replace(original, substitute)
                self.assertTrue(prompt_plan_issues(prompt, self.plan, medium='image'))
        plan = dict(self.plan, participants=[], beats=[
            {'action': '光斑移动', 'texts': [
                {'text': '移动方向', 'owner': '画面标注', 'container': '箭头标签'},
            ]},
        ], reference_beat=1)
        self.assertTrue(prompt_plan_issues('箭头标签写“移动方向”。', plan, medium='image'))

    def test_video_requires_all_stages_exact_text(self):
        self.assertFalse(prompt_plan_issues(self.video_prompt(), self.plan, medium='video'))
        for text in ('尚未加热', '开始升温'):
            with self.subTest(text=text):
                self.assertTrue(prompt_plan_issues(
                    self.video_prompt().replace(text, '概括描述'), self.plan, medium='video'))

    def test_planned_text_conflicts_with_blanket_text_ban(self):
        for medium, prompt in (('image', self.image_prompt()), ('video', self.video_prompt())):
            with self.subTest(medium=medium):
                self.assertTrue(prompt_plan_issues(
                    prompt + '禁止生成文字。', self.plan, medium=medium))

    def test_restricting_extra_captions_is_not_a_blanket_text_ban(self):
        for medium, prompt in (('image', self.image_prompt()), ('video', self.video_prompt())):
            with self.subTest(medium=medium):
                self.assertFalse(prompt_plan_issues(
                    prompt + '不要生成额外字幕、标题或计划外文字。', self.plan, medium=medium))

    def test_local_or_final_stage_without_text_is_allowed(self):
        for suffix in ('最后一个阶段没有文字，只有器材。', '背景墙无文字。',
                       '最后一阶段禁止生成文字。', '背景墙禁止生成文字。'):
            with self.subTest(suffix=suffix):
                self.assertFalse(prompt_plan_issues(self.video_prompt() + suffix, self.plan, 'video'))

    def test_explicit_global_text_bans_are_rejected(self):
        for suffix in ('全片没有任何文字。', '全程禁止生成文字。',
                       '【必要限制】禁止生成文字，默认静音。'):
            with self.subTest(suffix=suffix):
                self.assertTrue(prompt_plan_issues(self.video_prompt() + suffix, self.plan, 'video'))

    def test_removing_a_previous_stage_label_is_not_requesting_it(self):
        for suffix in ('不要显示之前的“尚未加热”。', '旧标签“尚未加热”已经消失。',
                       '去掉温度计的状态卡“尚未加热”。'):
            with self.subTest(suffix=suffix):
                self.assertFalse(prompt_plan_issues(self.image_prompt() + suffix, self.plan, 'image'))

    def test_unquoted_description_is_not_assumed_to_be_a_label(self):
        self.assertFalse(prompt_plan_issues(self.image_prompt() + '旁边有另一份尚未加热的样本。', self.plan, 'image'))

    def test_prompt_checks_do_not_mutate_plan(self):
        before = copy.deepcopy(self.plan)
        prompt_plan_issues(self.image_prompt(), self.plan, medium='image')
        prompt_plan_issues(self.video_prompt(), self.plan, medium='video')
        self.assertEqual(self.plan, before)


if __name__ == '__main__':
    unittest.main()
