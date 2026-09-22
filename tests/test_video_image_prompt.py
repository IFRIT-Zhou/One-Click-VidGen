import copy
import unittest

from backend.app.video_agents import write_image_prompts, write_video_prompts
from backend.app.video_image_prompt import assemble_image_body
from backend.app.video_prompt_notices import prompt_notice_level


class ImagePromptAssemblyTest(unittest.TestCase):
    def setUp(self):
        self.shot = {'id': 'shot1', 'intent': '观众在了解选项后拒绝', 'kind': 'video',
                     'duration': 5, 'generation_duration': 5, 'reference_ids': []}
        self.parts = {'scene': '观众抬掌，观众的对话气泡显示“不要”。',
                      'constraints': '保留画风，不加整句旁白。',
                      'characters_and_style': '主持人、观众，简笔手绘。'}

    def test_structured_order_is_compiled_not_decided_by_agent(self):
        before = copy.deepcopy(self.parts)
        calls = []
        def ask(system, payload):
            calls.append((system, payload))
            return {'shots': [{'id': 'shot1', 'image_sections': self.parts}]}
        rows = write_image_prompts({}, '手绘', [self.shot], [], ask=ask)
        prompt = rows[0]['image_prompt']
        self.assertEqual(prompt, '【本图旨在】观众在了解选项后拒绝\n'
                         '【人物与画风】主持人、观众，简笔手绘。\n'
                         '【画面内容】观众抬掌，观众的对话气泡显示“不要”。\n'
                         '【必要限制】保留画风，不加整句旁白。')
        self.assertIn('image_sections', calls[0][0])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.parts, before)

    def test_empty_constraints_do_not_invent_no_text_rule(self):
        row = {'id': 'shot1', 'image_sections': dict(self.parts, constraints='')}
        assemble_image_body(self.shot, row)
        self.assertNotIn('【必要限制】', row['image_prompt'])
        self.assertNotIn('禁止文字', row['image_prompt'])
        self.assertIn('“不要”', row['image_prompt'])

    def test_redundant_own_heading_removed_without_dropping_quoted_content(self):
        row = {'id': 'shot1', 'image_sections': dict(self.parts,
            scene='【画面内容】菜单写“【推荐】套餐”。')}
        assemble_image_body(self.shot, row)
        self.assertEqual(row['image_prompt'].count('【画面内容】'), 1)
        self.assertIn('“【推荐】套餐”', row['image_prompt'])

    def test_quoted_intent_label_inside_real_scene_is_not_deleted(self):
        scene = '墙上标牌写“【本图旨在】节约用水”，有人指向标牌。'
        for result in ({'image_sections': dict(self.parts, scene=scene)},
                       {'image_prompt': '【人物与画风】手绘【画面内容】' + scene}):
            rows = write_image_prompts({}, '手绘', [self.shot], [], ask=lambda *_: {
                'shots': [dict(id='shot1', **result)]})
            self.assertIn(scene, rows[0]['image_prompt'])

    def test_legacy_intent_only_is_not_treated_as_a_usable_image_prompt(self):
        with self.assertRaisesRegex(ValueError, '缺少实际画面内容'):
            write_image_prompts({}, '手绘', [self.shot], [], ask=lambda *_: {
                'shots': [{'id': 'shot1', 'image_prompt': '【本图旨在】表现一个概念'}]})

    def test_legacy_free_text_is_not_rewritten(self):
        for prompt in ('简笔画，观众举手。',
                       '【人物与画风】手绘【画面内容】会场【画面文字与归属】观众气泡写“不要”'):
            row = {'id': 'shot1', 'image_prompt': prompt}
            assemble_image_body(self.shot, row)
            self.assertEqual(row['image_prompt'], prompt)

    def test_incomplete_or_unknown_sections_repair_early_without_losing_draft(self):
        for broken in ({'scene': '观众举手'}, {'characters_and_style': '', 'scene': '观众'},
                       dict(self.parts, constraints=[]), dict(self.parts, extra='不能丢弃的内容'),
                       dict(self.parts, scene='【画面内容】')):
            calls = []
            def ask(system, payload):
                calls.append(payload)
                return {'shots': [{'id': 'shot1', 'image_sections': broken if len(calls) == 1 else self.parts}]}
            result = write_image_prompts({}, '手绘', [self.shot], [], ask=ask)
            self.assertEqual(len(calls), 2)
            self.assertIn('validation_errors', calls[1])
            self.assertEqual(calls[1]['previous_result']['shots'][0]['image_sections'], broken)
            self.assertIn('观众抬掌', result[0]['image_prompt'])

    def test_structured_output_does_not_use_stale_free_text_on_validation_failure(self):
        with self.assertRaisesRegex(ValueError, '人物与画风'):
            write_image_prompts({}, '手绘', [self.shot], [], ask=lambda *_: {'shots': [
                {'id': 'shot1', 'image_sections': {'scene': '新画面'}, 'image_prompt': '旧画面'}]})

    def test_only_lexical_advisories_do_not_spend_a_repair_call(self):
        plan = {'version': 1, 'scene_anchor': '会场', 'participants': ['主持人', '观众'],
                'beats': [{'action': '观众举手', 'texts': [{'text': '不要', 'owner': '观众', 'container': '对话气泡'}]}],
                'reference_beat': 1, 'reference_visual': '会场中的观众'}
        shot = dict(self.shot, motion_plan=plan)
        for medium in ('image', 'video'):
            calls = []
            def ask(*args):
                calls.append(args)
                return {'shots': [{'id': 'shot1', medium + '_prompt': '参照图1，讲者和台下听众，圆框显示“不要”。'}]}
            if medium == 'image':
                result = write_image_prompts({}, '手绘', [shot], [], ask=ask)
            else:
                result = write_video_prompts({}, [shot], [], ask=ask)
            self.assertEqual(len(calls), 1)
            self.assertTrue(result[0][medium + '_prompt_warnings'])
            self.assertTrue(all(prompt_notice_level(note) == 'info' for note in result[0][medium + '_prompt_warnings']))

    def test_notice_levels_separate_format_wording_and_conflicts(self):
        cases = {'提示词分节标题不完整或顺序不同': 'format',
                 '缺少既定主体名称：观众': 'info', '缺少文字归属：观众': 'info',
                 '缺少文字容器：对话气泡': 'info', '未知核对备注': 'info',
                 '缺少短文字原文：不要': 'warning',
                 '规划了画面短文字，却同时要求全面禁止文字': 'warning',
                 '核心图混入未选用的阶段文字：结论': 'warning'}
        for message, level in cases.items():
            self.assertEqual(prompt_notice_level(message), level)


if __name__ == '__main__':
    unittest.main()
