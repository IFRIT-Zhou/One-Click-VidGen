import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import video_scene_references as video_scenes
from scene_reference_coordinator import bind_scene_references


class VideoSceneReferencesTest(unittest.TestCase):
    def setUp(self):
        self.record = {
            'creation_parameters': {
                'director_strategy': 'enhanced_beta',
                'scene_references_enabled': True,
            },
            'settings': {'style': '简笔火柴人', 'world': '同一个社区会场', 'ratio': '16:9'},
            'scenes': [{'slide_id': 'scene_001', 'start': 0, 'end': 5, 'text': '去日韩旅行怎么样？'}],
            'shots': [
                {
                    'id': 'stage_question',
                    'image_prompt': '红衣主讲人站在会场讲台，头顶气泡展示日韩景观。',
                    'visual_design': {'expression': 'explanatory', 'human_presence': 'present'},
                    'semantic': {'fact_status': 'question', 'source_basis': '主讲人提问'},
                    'motion_plan': {
                        'scene_anchor': '木质讲台与环绕的灰色观众席',
                        'reference_visual': '同一会场内，景观只属于主讲人的气泡',
                    },
                },
                {
                    'id': 'stage_response',
                    'image_prompt': '同一会场中观众紧张回应，想象气泡里出现飞机。',
                    'visual_design': {'expression': 'metaphor', 'human_presence': 'present'},
                    'semantic': {'fact_status': 'hypothetical', 'source_basis': '观众的主观想象'},
                    'motion_plan': {
                        'scene_anchor': '木质讲台与环绕的灰色观众席',
                        'reference_visual': '同一会场内，飞机只出现在观众想象气泡',
                    },
                },
                {
                    'id': 'hospital',
                    'image_prompt': '画面实际转到医院病房，老人躺在病床上。',
                    'visual_design': {'expression': 'narrative', 'human_presence': 'present'},
                    'semantic': {'fact_status': 'fact'},
                },
            ],
        }
        self.plan = {'scenes': [{
            'name': '会场', 'members': [0, 1],
            'reference_prompt': '简笔手绘无人会场，木质讲台与环绕的灰色座椅。',
            'reason': '两镜主体均处于同一个会场，旅行与飞机只在气泡内。',
        }]}

    def test_switch_requires_enhanced_mode_and_respects_explicit_disable(self):
        self.assertFalse(video_scenes.enabled({}))
        for strategy, switch, expected in (
            ('enhanced_beta', True, True),
            ('enhanced_beta', False, False),
            ('stable', True, False),
            ('stable', False, False),
        ):
            with self.subTest(strategy=strategy, switch=switch):
                record = {'creation_parameters': {
                    'director_strategy': strategy, 'scene_references_enabled': switch,
                }}
                self.assertEqual(video_scenes.enabled(record), expected)
        self.assertTrue(video_scenes.enabled({
            'creation_parameters': {'director_strategy': 'enhanced_beta'},
        }))

    def test_mapping_preserves_physical_anchor_and_original_semantic_context(self):
        before = copy.deepcopy(self.record)
        mapping = video_scenes.scene_mapping(self.record)
        self.assertEqual(self.record, before)
        self.assertEqual([row['macro_scene_id'] for row in mapping],
                         ['stage_question', 'stage_response', 'hospital'])
        for shot, item in zip(self.record['shots'], mapping):
            design = item['visual_design']
            self.assertEqual(design['original_design'], shot['visual_design'])
            self.assertEqual(design['semantic'], shot['semantic'])
            self.assertEqual(design['scene_anchor'], shot.get('motion_plan', {}).get('scene_anchor', ''))
            self.assertEqual(design['reference_visual'], shot.get('motion_plan', {}).get('reference_visual', ''))

    def test_dynamic_text_modes_share_independent_scene_reference_switch(self):
        for mode in ('text_assisted', 'visual_first'):
            for strategy in ('stable', 'enhanced_beta'):
                for switch in (True, False):
                    record = {'creation_parameters': {'dynamic_video': True, 'dynamic_text_mode': mode,
                        'director_strategy': strategy, 'scene_references_enabled': switch}}
                    self.assertEqual(video_scenes.enabled(record), switch)

    def test_legacy_dynamic_flag_does_not_silently_activate_scene_references(self):
        for strategy, expected in (('stable', False), ('enhanced_beta', True)):
            record = {'creation_parameters': {'dynamic_video': True,
                'director_strategy': strategy, 'scene_references_enabled': True}}
            self.assertEqual(video_scenes.enabled(record), expected)

    def test_bubble_shots_remain_eligible_but_only_selected_members_are_bound(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            'scene_reference_coordinator.generate_gemini_text',
            return_value=json.dumps(self.plan, ensure_ascii=False),
        ) as generate:
            root = Path(directory)
            plan = video_scenes.plan_references(root, self.record)
            self.assertEqual(plan['scenes'][0]['members'], [0, 1])
            self.assertEqual(plan['exclusions'], [])
            self.assertEqual(generate.call_args.kwargs['system_prompt'], video_scenes.VIDEO_CONTRACT)
            payload = json.loads(generate.call_args.kwargs['user_prompt'])
            self.assertEqual(payload['source']['world'], '同一个社区会场')
            self.assertIn('飞机只出现在观众想象气泡',
                          payload['items'][1]['visual_design']['reference_visual'])
            image = root / 'scene.jpg'
            image.write_bytes(b'scene-reference-fixture')
            mapping = video_scenes.scene_mapping(self.record)
            bound = bind_scene_references(mapping, plan, {'location_1': image}, {})
            for item in bound[:2]:
                self.assertEqual(item['scene_reference']['scene_id'], 'location_1')
                self.assertEqual(item['reference_image_paths'], [str(image.resolve())])
            self.assertEqual(bound[2], mapping[2])
            self.assertNotIn('scene_reference', bound[2])

    def test_reopening_uses_cache_without_repeating_agent_call(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            'scene_reference_coordinator.generate_gemini_text',
            return_value=json.dumps(self.plan, ensure_ascii=False),
        ) as generate:
            root = Path(directory)
            first = video_scenes.plan_references(root, self.record)
            second = video_scenes.plan_references(root, copy.deepcopy(self.record))
            self.assertEqual(first, second)
            self.assertTrue((root / 'scene_reference_plan.json').is_file())
            generate.assert_called_once()
            for shot in self.record['shots'][:2]:
                shot['image_prompt'] = video_scenes.scene_prompt(shot['image_prompt'], 2)
            self.assertEqual(video_scenes.plan_references(root, self.record), first)
            generate.assert_called_once()

    def test_no_repeated_space_is_cached_without_inventing_scene(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            'scene_reference_coordinator.generate_gemini_text',
            return_value='{"scenes": []}',
        ) as generate:
            root = Path(directory)
            first = video_scenes.plan_references(root, self.record)
            self.assertEqual(first['scenes'], [])
            self.assertEqual(video_scenes.plan_references(root, self.record), first)
            generate.assert_called_once()

    def test_style_world_and_ratio_changes_invalidate_plan_cache(self):
        for field, replacement in (
            ('style', '都市港漫手绘'),
            ('world', '另一间会场，固定布景已经变化'),
            ('ratio', '9:16'),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory, patch(
                'scene_reference_coordinator.generate_gemini_text',
                return_value=json.dumps(self.plan, ensure_ascii=False),
            ) as generate:
                root = Path(directory)
                original = video_scenes.plan_references(root, self.record)
                changed = copy.deepcopy(self.record)
                changed['settings'][field] = replacement
                updated = video_scenes.plan_references(root, changed)
                self.assertNotEqual(original['fingerprint'], updated['fingerprint'])
                self.assertEqual(generate.call_count, 2)
                self.assertEqual(video_scenes.plan_references(root, changed), updated)
                self.assertEqual(generate.call_count, 2)

    def test_scene_hint_is_idempotent_and_renumbering_preserves_original_prompt(self):
        prompt = '【本图旨在】解释观众反应\n【人物与画风】简笔画\n【画面内容】主讲人在会场\n【必要限制】不要水印'
        once = video_scenes.scene_prompt(prompt, 2)
        self.assertEqual(video_scenes.scene_prompt(once, 2), once)
        renumbered = video_scenes.scene_prompt(once, 3)
        self.assertEqual(renumbered.count('【场景参考】'), 1)
        self.assertIn('【场景参考】图3', renumbered)
        self.assertNotIn('【场景参考】图2', renumbered)
        self.assertEqual(video_scenes.strip_scene_hint(renumbered), prompt)
        self.assertEqual(video_scenes.strip_scene_hint(prompt), prompt)


if __name__ == '__main__':
    unittest.main()
