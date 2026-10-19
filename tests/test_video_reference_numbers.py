import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app.video_reference_numbers import normalize_video_references
from backend.app.video_agents import _numbered_dynamic_shots
from backend.app import video_generation as vg


class ReferenceNumberTests(unittest.TestCase):
    def setUp(self):
        self.refs = [dict(id=f'ref_{n:02}', label=f'图{n}', number=f'图{n}') for n in range(1, 5)]
        self.shot = dict(id='3278ce0636df', kind='video', reference_ids=['ref_03', 'ref_04'],
                         image_status='completed', video_prompt='图1核心图；图3（ref_03）、图4（ref_04）')

    def test_non_contiguous_ids_and_core_slot(self):
        result = normalize_video_references(self.shot['video_prompt'], self.shot, self.refs)
        self.assertEqual(result, '图1核心图；图2（ref_03）、图3（ref_04）')
        self.assertEqual(normalize_video_references(result, self.shot, self.refs), result)

    def test_planner_has_only_local_labels(self):
        rows = _numbered_dynamic_shots([self.shot], self.refs)[0]['numbered_references']
        self.assertEqual([r['number'] for r in rows], ['图1', '图2', '图3'])
        self.assertEqual([r['label'] for r in rows[1:]], ['图2', '图3'])

    def test_missing_identity_and_ambiguous_legacy_number_are_not_guessed(self):
        for prompt in ['图4', '图2（ref_01）', '图2（missing）']:
            with self.subTest(prompt=prompt), self.assertRaisesRegex(ValueError, '3278ce0636df'):
                normalize_video_references(prompt, self.shot, self.refs)

    def test_actual_input_order_and_other_completed_shot_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core = root / 'core.jpg'
            core.touch()
            other = dict(id='done', video_status='completed', video_prompt='图4（ref_04）')
            record = dict(references=self.refs, shots=[self.shot, other])
            before = copy.deepcopy(other)
            with patch.object(vg.studio, '_storyboard_image_path', return_value=core), patch.object(
                    vg.studio, '_shot_reference_paths', return_value=[root/'three.jpg', root/'four.jpg']) as resolve:
                self.assertEqual(vg._inputs(root, record, self.shot), (core, root/'three.jpg', root/'four.jpg'))
                self.assertEqual(resolve.call_args.args[2]['reference_ids'], ['ref_03', 'ref_04'])
            self.assertEqual(other, before)
            with patch.object(vg.studio, '_storyboard_image_path', return_value=core), patch.object(
                    vg.studio, '_shot_reference_paths', side_effect=ValueError('missing file')):
                with self.assertRaises(ValueError):
                    vg._inputs(root, record, self.shot)


if __name__ == '__main__':
    unittest.main()
