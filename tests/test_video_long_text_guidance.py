import unittest
from backend.app.video_text_policy import visual_first_long_text_issues
from backend.app.video_agents import design_core_images, _finalize_prompts


class LongTextGuidanceTests(unittest.TestCase):
    def test_visible_long_sentence_not_short_labels_or_removal(self):
        self.assertTrue(visual_first_long_text_issues('主持人头顶出现对话气泡“这是一句很长很长的解释性台词。”'))
        for prompt in ('观众的对话气泡写着“危险”。', '店招写着“面馆”。',
                       '移除对话气泡“这是一句很长很长的解释性台词。”'):
            self.assertEqual(visual_first_long_text_issues(prompt), [])

    def test_core_gets_targeted_visual_repair(self):
        calls = []
        def ask(system, data):
            calls.append(data)
            visual = ('主持人的对话气泡写着“这是一句很长很长的解释性台词。”'
                      if len(calls) == 1 else '主持人指向无字报纸图案，观众抬头思考。')
            return {'shots': [{'id': 'a', 'visual_description': visual}]}
        rows = design_core_images({'video_direction': {'dynamic_text_mode': 'visual_first'}},
                                  [], [{'id': 'a'}], [], ask=ask)
        self.assertEqual(len(calls), 2)
        self.assertIn('画中长句', calls[1]['validation_errors'][0])
        self.assertIn('无字报纸', rows[0]['visual_description'])

    def test_selected_long_text_is_visible_warning_without_repeated_paid_retry(self):
        text = '这是一句很长很长的解释性台词。'
        shot = {'id': 'a', 'motion_plan': {'version': 1, 'participants': ['主持人'],
            'scene_anchor': '讲台', 'reference_beat': 1, 'reference_visual': '主持人站在讲台',
            'beats': [{'action': '主持人抬手', 'texts': [{'text': text, 'owner': '主持人', 'container': '对话气泡'}]}]}}
        calls = []
        def ask(system, data):
            calls.append(data)
            return {'shots': [{'id': 'a', 'video_prompt': '主持人的对话气泡写着“' + text + '”'}]}
        rows = _finalize_prompts('', {'story_context': {'video_direction': {'dynamic_text_mode': 'visual_first'}}},
                                 [shot], 'video_prompt', 'video', ['a'], ask)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(v.startswith('少字表达建议：') for v in rows[0]['video_prompt_warnings']))

    def test_retry_removes_unselected_visible_words_without_losing_scene(self):
        shot = {'id': 'a', 'motion_plan': {'version': 1, 'participants': ['主持人'],
            'scene_anchor': '讲台', 'reference_beat': 1, 'reference_visual': '主持人站在讲台',
            'beats': [{'action': '主持人指向图表', 'texts': []}]}}
        calls = []
        def ask(system, data):
            calls.append(data)
            return {'shots': [{'id': 'a', 'video_prompt': '主持人站在讲台，指向图表；说明框显示“述评框架”。'}]}
        rows = _finalize_prompts('', {'story_context': {'video_direction': {'dynamic_text_mode': 'visual_first'}}},
                                 [shot], 'video_prompt', 'video', ['a'], ask)
        self.assertEqual(len(calls), 2)
        self.assertIn('无字图案', rows[0]['video_prompt'])
        self.assertIn('主持人站在讲台', rows[0]['video_prompt'])
        self.assertNotIn('述评框架', rows[0]['video_prompt'])


if __name__ == '__main__':
    unittest.main()
