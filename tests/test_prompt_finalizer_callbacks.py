"""Exercise both public finalizers, including their bounded correction paths."""
import copy
import unittest
from unittest.mock import Mock

from backend.app.video_agents import write_image_prompts, write_video_prompts


class PromptFinalizerCallbackTests(unittest.TestCase):
    def setUp(self):
        self.shot = dict(id='shot-3', kind='video', intent='展示鸡蛋裂开的过程',
                         reference_ids=[], duration=5, generation_duration=5,
                         motion_plan=dict(
                             version=2, scene_anchor='厨房', participants=['鸡蛋'],
                             reference_beat=1, reference_visual='鸡蛋',
                             reference_participants=['鸡蛋'], reference_texts=[],
                             beats=[dict(action='鸡蛋出现裂纹', texts=[]),
                                    dict(action='鸡蛋裂开', texts=[])]))
        self.image_row = dict(id='shot-3', image_sections=dict(
            characters_and_style='写实风格，无人物出镜',
            scene='厨房台面上的鸡蛋出现裂纹。', constraints=''))
        self.video_row = dict(id='shot-3', continuity_prompt='厨房内保持鸡蛋造型与画风。',
                              beat_prompts=[dict(beat=1, prompt='鸡蛋出现裂纹。'),
                                            dict(beat=2, prompt='鸡蛋裂开。')],
                              ending_prompt='鸡蛋裂开后自然停留。')

    def ask_rows(self, *rows):
        return Mock(side_effect=[{'shots': [copy.deepcopy(row)]} for row in rows])

    def test_image_first_attempt_supports_static_and_dynamic_shots(self):
        for kind in ('static', 'video'):
            with self.subTest(kind=kind):
                shot = copy.deepcopy(self.shot)
                shot['kind'] = kind
                if kind == 'static':
                    shot.pop('motion_plan')
                ask = self.ask_rows(self.image_row)
                draft = Mock()
                rows = write_image_prompts({}, '写实', [shot], [], ask=ask, on_draft=draft)
                self.assertEqual(ask.call_count, 1)
                self.assertIn('【画面内容】厨房台面上的鸡蛋出现裂纹。', rows[0]['image_prompt'])
                self.assertEqual(draft.call_args.args[0], rows)

    def test_image_retry_keeps_two_argument_assembler_contract(self):
        blank = copy.deepcopy(self.image_row)
        blank['image_sections']['scene'] = ''
        ask = self.ask_rows(blank, self.image_row)
        rows = write_image_prompts({}, '写实', [self.shot], [], ask=ask)
        self.assertEqual(ask.call_count, 2)
        self.assertIn('画面内容', ask.call_args.args[1]['validation_errors'][0])
        self.assertIn('鸡蛋出现裂纹', rows[0]['image_prompt'])

    def test_image_empty_fields_still_fail_after_retry(self):
        blank = copy.deepcopy(self.image_row)
        blank['image_sections']['scene'] = ''
        ask = self.ask_rows(blank, blank)
        with self.assertRaisesRegex(ValueError, '画面内容'):
            write_image_prompts({}, '写实', [self.shot], [], ask=ask)
        self.assertEqual(ask.call_count, 2)

    def test_legacy_image_prompt_remains_supported(self):
        ask = self.ask_rows(dict(id='shot-3', image_prompt='厨房台面上的鸡蛋出现裂纹。'))
        rows = write_image_prompts({}, '写实', [self.shot], [], ask=ask)
        self.assertEqual(rows[0]['image_prompt'], '【本图旨在】展示鸡蛋裂开的过程\n厨房台面上的鸡蛋出现裂纹。')

    def test_video_complete_first_attempt_needs_no_fallback(self):
        ask = self.ask_rows(self.video_row)
        rows = write_video_prompts({}, [self.shot], [], ask=ask)
        self.assertEqual(ask.call_count, 1)
        self.assertIn('鸡蛋裂开后自然停留。', rows[0]['video_prompt'])
        self.assertFalse(any('空字段' in note for note in rows[0]['video_prompt_warnings']))

    def test_video_retry_fills_only_empty_fields_from_existing_plan(self):
        blank = copy.deepcopy(self.video_row)
        blank.update(continuity_prompt='', ending_prompt=None)
        blank['beat_prompts'][1]['prompt'] = ''
        ask = self.ask_rows(blank, blank)
        rows = write_video_prompts({}, [self.shot], [], ask=ask)
        self.assertEqual(ask.call_count, 2)
        self.assertIn('continuity_prompt', ask.call_args.args[1]['validation_errors'][0])
        self.assertIn('鸡蛋出现裂纹。', rows[0]['video_prompt'])
        self.assertIn('鸡蛋裂开', rows[0]['video_prompt'])
        self.assertTrue(any('空字段' in note for note in rows[0]['video_prompt_warnings']))

    def test_video_missing_phase_is_not_hidden_by_fallback(self):
        missing = copy.deepcopy(self.video_row)
        missing['beat_prompts'].pop()
        ask = self.ask_rows(missing, missing)
        with self.assertRaisesRegex(ValueError, 'shot-3.*阶段缺失'):
            write_video_prompts({}, [self.shot], [], ask=ask)
        self.assertEqual(ask.call_count, 2)


if __name__ == '__main__':
    unittest.main()
