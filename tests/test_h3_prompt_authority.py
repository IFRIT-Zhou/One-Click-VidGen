import json
import re
import unittest
from unittest.mock import patch

from backend.app import h3_prompt_agent as agent


INLINE = ('subject_definitions: <Picture 1> is the visual reference. '
          'summary: A white-robed man watches clouds. '
          'retention_analysis: Preserve compatible landscape details from <Picture 1>. '
          'detailed_description: [Shot 1] [00:00-00:10] The white-robed man stays still '
          'on the mountain while the camera slowly pushes closer and clouds drift. '
          'overall_soundscape: Wind. non_diegetic_music: N/A.')


class H3PromptAuthorityTests(unittest.TestCase):
    def test_inline_sections_preserve_real_bodies_without_placeholders(self):
        result = agent._validate(INLINE, 10)
        for name in agent._SECTIONS:
            self.assertEqual(len(re.findall(rf'(?m)^\s*{name}:', result)), 1)
        self.assertIn('white-robed man stays still', agent._section_body(result, 'detailed_description'))
        self.assertNotIn('The approved action unfolds continuously', result)
        self.assertEqual(agent._validate(result, 10), result)

    def test_duplicate_sections_fail_before_gpu_submission(self):
        with self.assertRaisesRegex(ValueError, '重复'):
            agent._validate(INLINE + ' summary: Another design.', 10)

    def test_manual_video_prompt_is_only_creative_text_sent(self):
        shot = {'video_prompt': '白衣男子静立山巅，镜头缓慢推近。',
                'intent': '古今左右分屏', 'action': '庄子持钓竿',
                'image_prompt': '灰褐色旧衣', 'motion_plan': {}}
        with patch.object(agent, 'generate_gemini_text', return_value=json.dumps({'h3_prompt': INLINE})) as generate:
            agent.convert_for_h3(shot, 10)
        payload = json.loads(generate.call_args.kwargs['user_prompt'])
        self.assertEqual(payload['model_neutral_video_prompt'], shot['video_prompt'])
        for key in ('storyboard_intent', 'approved_dynamic_expression', 'core_image_prompt_for_context'):
            self.assertNotIn(key, payload)
        for stale in ('古今左右分屏', '庄子持钓竿', '灰褐色旧衣'):
            self.assertNotIn(stale, generate.call_args.kwargs['user_prompt'])
        self.assertIn('sole approved creative instruction', generate.call_args.kwargs['system_prompt'])
        self.assertIn('one [Shot 1]', generate.call_args.kwargs['system_prompt'])

    def test_final_prompt_changes_invalidate_cache(self):
        shot = {'video_prompt': 'A white-robed man.'}
        before = agent.source_fingerprint(shot, 10)
        shot['action'] = 'Outdated fishing action'
        self.assertEqual(before, agent.source_fingerprint(shot, 10))
        shot['video_prompt'] = 'Clouds only.'
        self.assertNotEqual(before, agent.source_fingerprint(shot, 10))


if __name__ == '__main__':
    unittest.main()
