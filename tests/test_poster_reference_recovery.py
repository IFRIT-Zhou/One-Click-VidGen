import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import module4_video_render as visual
from backend.app import reference_materials as materials


class PosterReferenceRecoveryTests(unittest.TestCase):
    def test_explicit_reference_list_and_negation(self):
        self.assertEqual(materials.explicit_reference_labels('外观参考图1、图2'), {'图1', '图2'})
        self.assertEqual(materials.explicit_reference_labels('不要参考图1、图2'), set())

    def test_visible_character_recovers_two_unknown_views_but_not_context_only(self):
        rows = [dict(label=f'图{i}', kind='unknown') for i in (1, 2)]
        story = {'characters': [dict(character_id='speaker', name='讲解者', appearance='固定主角参考图1、图2')]}
        self.assertEqual(materials.recover_visible_character_references('讲解者举手', ['speaker'], story, rows), ['图1', '图2'])
        self.assertEqual(materials.recover_visible_character_references('纯图表', [], story, rows), [])

    def test_mentions_negation_and_style_do_not_infer_character_inputs(self):
        rows = [dict(label=f'图{i}', kind='unknown') for i in (1, 2)]
        story = {'characters': [dict(character_id='speaker', name='讲解者', appearance='参考图1、图2')]}
        for prompt, ids in [('旁白提到讲解者', []), ('仅借鉴讲解者图片的画风', []),
                            ('讲解者不出镜', ['speaker']), ('不要讲解者', ['speaker']),
                            ('纯图表，讲解者介绍收益', ['speaker'])]:
            with self.subTest(prompt=prompt):
                self.assertEqual(materials.recover_visible_character_references(prompt, ids, story, rows), [])
        self.assertEqual(materials.explicit_reference_labels('仅借鉴画风，参考图2'), {'图2'})

    def test_explicit_unknown_material_recovers_only_requested_identity(self):
        rows = [dict(label=f'图{i}', kind='unknown') for i in (1, 2)]
        with patch.object(visual, '_reference_image_catalog', return_value={'图1': 'one', '图2': 'two'}), patch.object(materials, 'reference_metadata', return_value=rows), patch.object(materials, 'required_every_shot_labels', return_value=[]):
            result = visual._synchronized_reference_image_ids({'reference_image_ids': []}, 'expanded参考图2', '参考图1', {})
            self.assertEqual(result, ['图1'])
            with self.assertRaisesRegex(ValueError, '不存在'):
                visual._synchronized_reference_image_ids({}, '', '参考图3', {})

    def test_failure_snapshot_preserves_candidate_and_successful_mapping(self):
        candidate = [{'macro_scene_id': 'poster_004', 'image_prompt': '参考图1', 'reference_image_ids': []}]
        with tempfile.TemporaryDirectory() as folder, patch.object(visual, '_visual_checkpoint_dir', return_value=Path(folder)), patch.object(visual, '_reference_image_catalog', return_value={'图1': 'one'}):
            with self.assertRaisesRegex(ValueError, 'poster_004'):
                visual._bind_material_references_once(candidate)
            files = list((Path(folder)/'failures').glob('*.json'))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['mapping'][0]['reference_image_ids'], [])
            self.assertEqual(candidate[0]['image_prompt'], '参考图1')

    def test_agent_retries_only_failed_shot_and_preserves_input(self):
        mapping = [{'macro_scene_id': 'ok', 'image_prompt': '空房间', 'reference_image_ids': []},
                   {'macro_scene_id': 'bad', 'image_prompt': '参考图2', 'reference_image_ids': []}]
        with tempfile.TemporaryDirectory() as folder, patch.object(visual, '_visual_checkpoint_dir', return_value=Path(folder)), patch.object(visual, '_reference_image_catalog', return_value={'图2': 'two'}), patch.object(visual, 'generate_gemini_text', side_effect=['{"reference_image_ids":[]}', '{"reference_image_ids":["图2"]}']) as agent:
            result = visual._bind_material_references_with_diagnostics(mapping)
            self.assertEqual(agent.call_count, 2)
            self.assertEqual(result[1]['reference_image_paths'], ['two'])
            self.assertEqual(result[0]['image_prompt'], '空房间')
            self.assertEqual(mapping[1]['reference_image_ids'], [])

    def test_agent_stops_after_two_invalid_selections(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(visual, '_visual_checkpoint_dir', return_value=Path(folder)), patch.object(visual, '_reference_image_catalog', return_value={'图1': 'one'}), patch.object(visual, 'generate_gemini_text', return_value='{"reference_image_ids":[]}') as agent:
            with self.assertRaisesRegex(ValueError, '2次仍失败'):
                visual._bind_material_references_with_diagnostics([{'image_prompt': '参考图1', 'reference_image_ids': []}])
            self.assertEqual(agent.call_count, 2)

    def test_missing_selection_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(visual, '_visual_checkpoint_dir', return_value=Path(folder)), patch.object(visual, '_reference_image_catalog', return_value={}), patch.object(visual, 'generate_gemini_text') as agent:
            with self.assertRaises(ValueError):
                visual._bind_material_references_with_diagnostics([{'image_prompt': '参考图1', 'reference_image_ids': ['图1']}])
            agent.assert_not_called()


if __name__ == '__main__':
    unittest.main()
