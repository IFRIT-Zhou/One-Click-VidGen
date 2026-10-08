import copy
import json
import unittest
from unittest.mock import patch

import story_agents
from backend.app.video_speech import (normalize_attribution, normalize_turns,
    inherit_attribution, reconcile_core_attribution, text_owner_issues, prompt_owner_issues)
from backend.app.video_agents import design_core_images, _finalize_prompts
from backend.app.video_plan import normalize_shots


def turn(**overrides):
    return dict(source_text='危险', speaker='观众', addressee='主持人', mode='spoken',
                basis='观众回答主持人的提问', on_screen='dialogue', certainty='contextual', **overrides)


class SpeechAttributionTests(unittest.TestCase):
    def setUp(self):
        self.scenes = [{'slide_id': 's1', 'text': '去那里怎么样？危险。', 'start': 0, 'end': 4}]
        self.shot = {'id': 'a', 'slide_ids': ['s1'], 'source_subtitles': self.scenes,
                     'kind': 'static', 'semantic': {'speech_mode': 'dialogue', 'speech_turns': [turn()]}}

    def test_narration_clears_stale_dialogue_without_creating_people(self):
        context = {'speech_attribution': {'mode': 'none', 'turns': []}}
        result = inherit_attribution(context, self.shot, self.scenes)
        self.assertEqual(result['semantic']['speech_turns'], [])
        self.assertEqual(result['semantic']['speech_mode'], 'none')
        self.assertNotIn('characters', result)
        self.assertEqual(self.shot['semantic']['speech_turns'], [turn()])

    def test_unknown_and_quoted_narration_do_not_enforce_owner(self):
        for updates in ({'certainty': 'uncertain'}, {'on_screen': 'none', 'mode': 'quoted'}):
            shot = copy.deepcopy(self.shot)
            shot['semantic']['speech_turns'][0].update(updates)
            self.assertEqual(text_owner_issues(shot, [{'text': '危险', 'owner': '主持人', 'container': '对话气泡'}]), [])

    def test_unique_source_anchor_inherited_but_repeated_anchor_not_guessed(self):
        context = {'speech_attribution': {'mode': 'mixed', 'turns': [turn()]}}
        shot = {**self.shot, 'semantic': {}}
        self.assertEqual(inherit_attribution(context, shot, self.scenes)['semantic']['speech_turns'], [turn()])
        repeated = self.scenes + [{'slide_id': 's2', 'text': '危险。'}]
        self.assertNotIn('speech_turns', inherit_attribution(context, shot, repeated)['semantic'])

    def test_only_literal_dialogue_conflicts_are_flagged(self):
        self.assertTrue(text_owner_issues(self.shot, [{'text': '危险', 'owner': '主持人', 'container': '对话气泡'}]))
        for owner, container, text in [('观众甲', '对话气泡', '危险'), ('主持人', '菜单标签', '危险'),
                                        ('主持人', '对话气泡', '请坐')]:
            self.assertEqual(text_owner_issues(self.shot, [{'text': text, 'owner': owner, 'container': container}]), [])

    def test_prompt_checks_explicit_claims_not_negative_instructions(self):
        self.assertTrue(prompt_owner_issues(self.shot, '主持人的对话气泡写着“危险”。'))
        self.assertFalse(prompt_owner_issues(self.shot, '不得把主持人的对话气泡写成“危险”。'))
        self.assertFalse(prompt_owner_issues(self.shot, '路边招牌写着“危险”。'))

    def test_core_cannot_silently_reassign_or_drop_confirmed_dialogue(self):
        for turns in ([], [{**turn(), 'speaker': '主持人'}]):
            with self.assertRaisesRegex(ValueError, '更改了已有发言归属'):
                reconcile_core_attribution(self.shot, {'semantic': {'speech_turns': turns}})
        revised = {'semantic': {'speech_turns': [{**turn(), 'speaker': '主持人'}]},
                   'attribution_correction': '用户指定此句由主持人复述'}
        reconcile_core_attribution(self.shot, revised)
        self.assertEqual(revised['semantic']['speech_turns'][0]['speaker'], '主持人')
        with self.assertRaisesRegex(ValueError, '更改了已有发言归属'):
            reconcile_core_attribution(self.shot, {'semantic': {'speech_mode': 'none'}})

    def test_narration_can_be_corrected_with_explicit_reason(self):
        original = {**self.shot, 'semantic': {'speech_mode': 'none', 'speech_turns': []}}
        revised = {'semantic': {'speech_turns': [turn()]},
                   'attribution_correction': '用户设定要求观众回答主持人的这个问题'}
        reconcile_core_attribution(original, revised)
        self.assertEqual(revised['semantic']['speech_mode'], 'dialogue')

    def test_legacy_and_malformed_hints_remain_nonblocking(self):
        self.assertEqual(normalize_attribution(None, '危险')['mode'], 'uncertain')
        self.assertEqual(normalize_turns([{'source_text': '不存在'}], '危险'), [])
        value = {**turn(), 'on_screen': [], 'certainty': {}}
        self.assertEqual(normalize_turns([value], '危险')[0]['certainty'], 'uncertain')

    def test_normalization_retains_attribution_flags(self):
        result = normalize_shots([self.shot], self.scenes)[0]
        self.assertEqual(result['semantic']['speech_turns'], [turn()])
        self.assertEqual(result['semantic']['speech_mode'], 'dialogue')

    def test_core_inherits_narration_and_removes_old_downstream_prompts(self):
        calls = []
        def ask(system, payload):
            calls.append(payload)
            return {'shots': [{'id': 'a', 'visual_description': '河水流过山谷。'}]}
        result = design_core_images({'speech_attribution': {'mode': 'none', 'turns': []}},
            self.scenes, [{**self.shot, 'video_prompt': '旧的说话画面'}], [], ask=ask)
        self.assertEqual(result[0]['semantic']['speech_mode'], 'none')
        self.assertNotIn('video_prompt', calls[0]['shots'][0])

    def test_prompt_conflict_gets_one_targeted_repair(self):
        calls = []
        def ask(system, payload):
            calls.append(payload)
            owner = '主持人' if len(calls) == 1 else '观众'
            return {'shots': [{'id': 'a', 'image_prompt': owner + '的对话气泡写着“危险”。'}]}
        rows = _finalize_prompts('', {}, [self.shot], 'image_prompt', 'image', ['a'], ask)
        self.assertEqual(len(calls), 2)
        self.assertIn('发言归属冲突', calls[1]['validation_errors'][0])
        self.assertEqual(rows[0]['image_prompt_warnings'], [])

    def test_agent0_feature_is_opt_in_and_preserves_narration(self):
        response = json.dumps({'characters': [], 'locations': [], 'continuity_rules': [],
                               'speech_attribution': {'mode': 'none', 'turns': []}})
        with patch.object(story_agents, 'gemini_configured', return_value=True), \
             patch.object(story_agents, 'generate_gemini_text', return_value=response) as generate:
            context = story_agents.create_story_context('水流经过山谷。', content_mode='pure_science', speech_attribution=True)
            self.assertEqual(context['speech_attribution'], {'mode': 'none', 'turns': []})
            self.assertEqual(context['characters'], [])
            self.assertIn('先判断是否存在需要表现的说话关系', generate.call_args.kwargs['system_prompt'])
            story_agents.create_story_context('水流经过山谷。', content_mode='pure_science')
            self.assertNotIn('先判断是否存在需要表现的说话关系', generate.call_args.kwargs['system_prompt'])


if __name__ == '__main__':
    unittest.main()
