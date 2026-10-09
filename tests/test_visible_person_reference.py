import json
import os
import unittest
from unittest.mock import patch
import module4_video_render as visual
from backend.app.reference_materials import bind_material_references


class VisiblePersonReferenceTests(unittest.TestCase):
    def test_unbound_explicit_reference_cannot_silently_generate(self):
        with self.assertRaisesRegex(ValueError, '没有绑定'):
            bind_material_references([{'image_prompt': '人物外貌参考图1', 'reference_image_ids': []}], {'图1': 'person.jpg'})

    def test_presenter_note_recovers_image_and_binding(self):
        rows = [{'label': '图1', 'kind': 'unknown', 'description': '这是讲解员，必须出现在画面中'}]
        story = {'characters': [{'character_id': 'xiaoy', 'name': '小Y', 'appearance': '参考图1所示的讲解员形象'}]}
        with patch.dict(os.environ, {'USER_REFERENCE_IMAGE_PATHS_JSON': '["portrait.jpg"]',
                                    'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(rows)}, clear=False):
            ids = visual._synchronized_reference_image_ids({'reference_image_ids': []}, '', '小Y在演播室挥手', story, ['xiaoy'])
            self.assertEqual(ids, ['图1'])
            bound = bind_material_references([{'image_prompt': '小Y挥手', 'reference_image_ids': ids}], {'图1': 'portrait.jpg'})[0]
            self.assertEqual(bound['reference_image_paths'], ['portrait.jpg'])
            # Continuity text inserted later must not force the person into B-roll.
            self.assertEqual(visual._synchronized_reference_image_ids({'reference_image_ids': []}, '小Y参考图1', '胃黏膜结构特写', story, []), [])

    def test_multiple_people_do_not_receive_each_others_reference(self):
        rows = [{'label': '图1', 'kind': 'character'}, {'label': '图2', 'kind': 'character'}]
        story = {'characters': [{'character_id': 'a', 'name': '甲', 'appearance': '参考图1'}, {'character_id': 'b', 'name': '乙', 'appearance': '参考图2'}]}
        with patch.dict(os.environ, {'USER_REFERENCE_IMAGE_PATHS_JSON': '["a.jpg","b.jpg"]', 'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(rows)}):
            self.assertEqual(visual._synchronized_reference_image_ids({'reference_image_ids': []}, '', '乙微笑', story, ['b']), ['图2'])
