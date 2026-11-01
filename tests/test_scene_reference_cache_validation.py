import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.video_scene_references import plan_references


class SceneReferenceCacheValidationTests(unittest.TestCase):
    def setUp(self):
        self.record = {'settings': {}, 'references': [{'id': 'ref_03', 'label': '图3'}],
                       'shots': [{'id': 'a', 'image_prompt': '房间远景'},
                                 {'id': 'b', 'image_prompt': '同一房间近景'}]}
        self.good = {'scenes': [{'name': '房间', 'members': [0, 1],
                     'reference_prompt': '参考图1中的桌子，无人房间',
                     'reason': '同一房间', 'reference_ids': ['ref_03']}]}

    def test_invalid_result_is_not_cached_and_next_start_can_succeed(self):
        for ids in [['missing'], ['ref_03', 'ref_03'], [3], 'ref_03',
                    ['ref_03'] * 4]:
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as folder:
                bad = copy.deepcopy(self.good)
                bad['scenes'][0]['reference_ids'] = ids
                before = copy.deepcopy(self.record)
                with patch('scene_reference_coordinator.generate_gemini_text',
                           side_effect=[json.dumps(bad), json.dumps(self.good)]) as ask:
                    with self.assertRaisesRegex(ValueError, '房间.*参考素材选择无效'):
                        plan_references(Path(folder), self.record)
                    self.assertFalse((Path(folder) / 'scene_reference_plan.json').exists())
                    result = plan_references(Path(folder), self.record)
                    self.assertEqual(result['scenes'][0]['reference_ids'], ['ref_03'])
                    self.assertEqual(ask.call_count, 2)
                self.assertEqual(self.record, before)

    def test_legacy_poisoned_cache_is_replanned_then_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with patch('scene_reference_coordinator.generate_gemini_text',
                       return_value=json.dumps(self.good)) as ask:
                first = plan_references(path, self.record)
                cached = copy.deepcopy(first)
                cached['scenes'][0]['reference_ids'] = ['missing']
                (path / 'scene_reference_plan.json').write_text(json.dumps(cached), encoding='utf-8')
                repaired = plan_references(path, self.record)
                self.assertEqual(repaired['scenes'][0]['reference_ids'], ['ref_03'])
                self.assertEqual(plan_references(path, self.record), repaired)
                self.assertEqual(ask.call_count, 2)

    def test_failed_replanning_does_not_hide_missing_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with patch('scene_reference_coordinator.generate_gemini_text',
                       return_value=json.dumps(self.good)):
                first = plan_references(path, self.record)
            first['scenes'][0]['reference_ids'] = ['missing']
            (path / 'scene_reference_plan.json').write_text(json.dumps(first), encoding='utf-8')
            with patch('scene_reference_coordinator.generate_gemini_text',
                       return_value=json.dumps(first)) as ask:
                with self.assertRaisesRegex(ValueError, 'missing'):
                    plan_references(path, self.record)
                self.assertEqual(ask.call_count, 1)

    def test_no_materials_selected_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            self.good['scenes'][0]['reference_ids'] = []
            with patch('scene_reference_coordinator.generate_gemini_text',
                       return_value=json.dumps(self.good)):
                self.assertEqual(plan_references(Path(folder), self.record)['scenes'][0]['reference_ids'], [])
