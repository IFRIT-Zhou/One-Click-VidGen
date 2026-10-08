import json
import unittest
from unittest.mock import Mock

from backend.app.video_agents import _complete_rows, design_core_images


class VideoAgentResponseShapeTests(unittest.TestCase):
    def test_supported_wrappers_keep_exact_rows(self):
        rows = [{'id': 'a'}, {'id': 'b'}]
        for response in ({'shots': rows}, {'shots': json.dumps(rows)},
                         {'data': {'shots': rows}}, {'result': {'shots': json.dumps(rows)}}):
            with self.subTest(response=response):
                self.assertEqual(_complete_rows(response, ['a', 'b'], '导演'), rows)

    def test_single_shot_object_only_for_single_expected_shot(self):
        self.assertEqual(_complete_rows({'id': 'a'}, ['a'], '导演'), [{'id': 'a'}])
        with self.assertRaises(ValueError):
            _complete_rows({'id': 'a'}, ['a', 'b'], '导演')

    def test_missing_duplicate_and_reordered_ids_are_rejected(self):
        for ids in (['a'], ['a', 'a'], ['b', 'a']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                _complete_rows({'shots': [{'id': i} for i in ids]}, ['a', 'b'], '导演')

    def test_no_arbitrary_nested_list_is_accepted(self):
        with self.assertRaisesRegex(ValueError, '返回顶层字段：method_example'):
            _complete_rows({'method_example': [{'id': 'a'}]}, ['a'], '导演')

    def test_core_director_repairs_invalid_shape(self):
        calls = []
        def ask(system, payload):
            calls.append((system, payload))
            if len(calls) == 1:
                return {'description': 'wrong schema'}
            return {'data': {'shots': [{'id': 'a', 'visual_description': '人物挥手'}]}}
        rows = design_core_images({}, [], [{'id': 'a'}], [], ask=ask)
        self.assertEqual(rows[0]['id'], 'a')
        self.assertEqual(len(calls), 2)
        self.assertIn('validation_errors', calls[1][1])

    def test_core_director_repairs_invented_references_locally(self):
        ask = Mock(side_effect=[
            {'shots': [{'id': 'last', 'visual_description': '讲解者', 'reference_ids': ['图1']}]},
            {'shots': [{'id': 'last', 'visual_description': '讲解者', 'reference_ids': []}]},
        ])
        rows = design_core_images({}, [], [{'id': 'last'}], [], ask=ask)
        self.assertEqual(rows[0]['reference_ids'], [])
        self.assertEqual(ask.call_count, 2)
        payload = ask.call_args.args[1]
        self.assertIn('没有上传参考素材', payload['validation_errors'][0])
        self.assertEqual([r['id'] for r in payload['shots']], ['last'])

    def test_core_director_deduplicates_real_reference_ids(self):
        ask = Mock(return_value={'shots': [{'id': 'a', 'visual_description': '人物', 'reference_ids': ['r', 'r']}]})
        rows = design_core_images({}, [], [{'id': 'a'}], [{'id': 'r'}], ask=ask)
        self.assertEqual(rows[0]['reference_ids'], ['r'])
        self.assertEqual(ask.call_count, 1)

    def test_repeated_bad_references_fail_with_shot_identity(self):
        ask = Mock(return_value={'shots': [{'id': 'a', 'visual_description': '人物', 'reference_ids': ['missing']}]})
        with self.assertRaisesRegex(ValueError, '镜头 a：参考素材选择无效'):
            design_core_images({}, [], [{'id': 'a'}], [{'id': 'real'}], ask=ask)
        self.assertEqual(ask.call_count, 2)


if __name__ == '__main__':
    unittest.main()
