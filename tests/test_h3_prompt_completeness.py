"""Incomplete H3 conversions must never be padded into valid video requests."""
import copy
import json
import unittest
from unittest.mock import patch

from backend.app import h3_prompt_agent as agent
from test_h3_prompt_authority import INLINE


SUBJECT_ONLY = 'subject_definitions: <Subject 1> is a liver with three lesions.'
LEGACY_EMPTY = '''subject_definitions:
<Subject 1> is a liver with three lesions. <Picture 1> is the reference.
summary:
Create the approved target-video action using <Picture 1> as the visual anchor.
retention_analysis:
<Picture 1> is preserved for composition, subjects, style, lighting, and spatial relationships.
detailed_description:
[Shot 1] [00:00-00:10] The approved action unfolds continuously across the requested duration.
Do not generate subtitles, automatic captions, lower thirds, narration transcription, title cards, explanatory overlays, or watermarks. Only exact approved short in-scene text may appear inside its assigned object or bubble; never turn it into subtitles.
The approved action unfolds continuously while preserving <Picture 1>.
overall_soundscape:
Generate only scene-appropriate environmental ambience and physical action sound effects.
non_diegetic_music:
N/A; no background music.
'''


def with_description(text):
    return INLINE.replace(
        '[Shot 1] [00:00-00:10] The white-robed man stays still '
        'on the mountain while the camera slowly pushes closer and clouds drift.', text)


class H3PromptCompletenessTests(unittest.TestCase):
    def test_missing_empty_and_placeholder_actions_are_rejected(self):
        for prompt in (SUBJECT_ONLY, with_description(''), with_description('N/A'),
                       with_description('[Shot 1] [00:00-00:10]'),
                       with_description('The approved action unfolds continuously.'), LEGACY_EMPTY):
            with self.subTest(prompt=prompt):
                with self.assertRaisesRegex(ValueError, '动作|阶段'):
                    agent._validate(prompt, 10)

    def test_policies_and_reference_anchor_are_not_actions(self):
        prompt = with_description(
            '<Picture 1> anchors the opening composition; its depicted elements may '
            'unfold over time rather than remain simultaneous.\n'
            'Do not generate subtitles, automatic captions, lower thirds, narration '
            'transcription, title cards, explanatory overlays, or watermarks. '
            'Only exact approved short in-scene text may appear inside its assigned '
            'object or bubble; never turn it into subtitles.')
        with self.assertRaisesRegex(ValueError, '动作|阶段'):
            agent._validate(prompt, 10)

    def test_reference_audio_does_not_fill_missing_visual_action(self):
        with self.assertRaisesRegex(ValueError, '动作|阶段'):
            agent._validate(with_description(''), 10, reference_audio=True, lipsync=True)

    def test_legacy_lipsync_boilerplate_is_not_a_visual_plan(self):
        prompt = with_description(agent._LIPSYNC_DIRECTION)
        with self.assertRaisesRegex(ValueError, '动作|阶段'):
            agent._validate(prompt, 10, reference_audio=True, lipsync=True)

    def test_harmless_markdown_headings_preserve_actions(self):
        prompt = agent._validate(INLINE.replace('detailed_description:', '**detailed-description**:'), 10)
        self.assertIn('clouds drift', prompt)
        self.assertNotIn('The approved action unfolds continuously', prompt)

    def test_short_explicit_hold_is_valid_without_forcing_motion(self):
        prompt = agent._validate(with_description('[Shot 1] [00:00-00:10] The liver stays still.'), 10)
        self.assertIn('The liver stays still.', prompt)

    def test_missing_timestamps_keep_real_action_without_generic_padding(self):
        prompt = agent._validate(with_description('The liver stays still.'), 10)
        self.assertIn('[00:00-00:10]', prompt)
        self.assertIn('The liver stays still.', prompt)
        self.assertNotIn('The approved action unfolds continuously', prompt)
        self.assertEqual(agent._validate(prompt, 10), prompt)

    def test_old_timeline_padding_does_not_hide_real_action(self):
        prompt = agent._validate(with_description(
            '[Shot 1] [00:00-00:10] The approved action unfolds continuously across '
            'the requested duration.\nThe liver stays still.'), 10)
        self.assertIn('The liver stays still.', prompt)

    def test_incomplete_conversion_retries_from_unchanged_inputs(self):
        shot = {'id': 'atsi_11', 'video_prompt': 'Keep three lesions still; highlight the third, then reveal the label.'}
        before = copy.deepcopy(shot)
        with patch.object(agent, 'generate_gemini_text', side_effect=[
                json.dumps({'h3_prompt': SUBJECT_ONLY}), json.dumps({'h3_prompt': INLINE})]) as ask:
            prompt, _ = agent.convert_for_h3(shot, 10)
        self.assertEqual(ask.call_count, 2)
        self.assertIn('white-robed man stays still', prompt)
        self.assertEqual(shot, before)
        self.assertEqual(ask.call_args_list[0].kwargs['user_prompt'], ask.call_args_list[1].kwargs['user_prompt'])
        feedback = ask.call_args.kwargs['system_prompt']
        self.assertIn('missing concrete visual action', feedback)
        self.assertNotIn('could not be parsed', feedback)

    def test_incomplete_retries_are_bounded_and_leave_history_unchanged(self):
        shot = {'id': 'atsi_11', 'video_prompt': 'Highlight the lesion.',
                'video_history': [{'video': 'old.mp4'}], 'image': 'original.jpg'}
        before = copy.deepcopy(shot)
        with patch.object(agent, 'generate_gemini_text', return_value=json.dumps({'h3_prompt': LEGACY_EMPTY})) as ask:
            with self.assertRaisesRegex(ValueError, '自动重试 2 次.*尚未提交.*原提示词及素材保留'):
                agent.convert_for_h3(shot, 10)
        self.assertEqual(ask.call_count, 3)
        self.assertEqual(shot, before)

    def test_invalid_same_fingerprint_cache_is_reconverted(self):
        shot = {'video_prompt': 'The man watches drifting clouds.', 'h3_prompt': LEGACY_EMPTY}
        shot['h3_prompt_source'] = agent.source_fingerprint(shot, 10)
        with patch.object(agent, 'generate_gemini_text', return_value=json.dumps({'h3_prompt': INLINE})) as ask:
            prompt, _ = agent.convert_for_h3(shot, 10)
        ask.assert_called_once()
        self.assertIn('clouds drift', prompt)

    def test_valid_cache_is_reused(self):
        shot = {'video_prompt': 'The man watches drifting clouds.', 'h3_prompt': agent._validate(INLINE, 10)}
        shot['h3_prompt_source'] = agent.source_fingerprint(shot, 10)
        with patch.object(agent, 'generate_gemini_text') as ask:
            prompt, _ = agent.convert_for_h3(shot, 10)
        ask.assert_not_called()
        self.assertEqual(prompt, shot['h3_prompt'])

    def test_format_and_completeness_share_one_retry_budget(self):
        with patch.object(agent, 'generate_gemini_text', side_effect=[
                '{bad json', json.dumps({'h3_prompt': SUBJECT_ONLY}),
                json.dumps({'h3_prompt': INLINE})]) as ask:
            prompt, _ = agent.convert_for_h3({'video_prompt': 'The man watches clouds.'}, 10)
        self.assertEqual(ask.call_count, 3)
        self.assertIn('could not be parsed', ask.call_args_list[1].kwargs['system_prompt'])
        self.assertIn('missing concrete visual action', ask.call_args_list[2].kwargs['system_prompt'])
        self.assertNotIn('could not be parsed', ask.call_args_list[2].kwargs['system_prompt'])
        self.assertIn('clouds drift', prompt)


if __name__ == '__main__':
    unittest.main()
